import json
import numpy as np
import torch
from PIL import Image

from .paste_back import RESIZE_METHODS, dilate_mask


def box_iou(a, b):
    """IoU of two [x1, y1, x2, y2] boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def match_boxes_by_iou(boxes_1, boxes_2, min_iou=0.0):
    """Greedy one-to-one matching: highest-IoU pairs are assigned first.

    Each box_1 gets at most one box_2 (and vice versa). A pair is only
    eligible when its IoU is strictly greater than `min_iou`, so an
    isolated box_1 with no overlapping box_2 is left unmatched.

    Returns a list of (i, j, iou) for matched pairs.
    """
    candidates = []
    for i, b1 in enumerate(boxes_1):
        for j, b2 in enumerate(boxes_2):
            iou = box_iou(b1, b2)
            if iou > min_iou:
                candidates.append((iou, i, j))
    candidates.sort(key=lambda t: t[0], reverse=True)

    used_1, used_2 = set(), set()
    matches = []
    for iou, i, j in candidates:
        if i in used_1 or j in used_2:
            continue
        used_1.add(i)
        used_2.add(j)
        matches.append((i, j, iou))
    return matches


def mask_intersection_area(mask_a, mask_b, threshold=0.5):
    """Pixel count of the binarized intersection of two [H, W] masks."""
    a = mask_a > threshold
    b = mask_b > threshold
    return (a & b).sum().item()


class PasteBackMatched:
    """Paste generated nail crops back onto the original image, restricted to
    nails that actually existed in the pre-generation crop.

    Compared to PasteBack, this node additionally receives the crop (i.e. the
    image actually fed into generation) and SAM3 "nail"/"finger" boxes/masks
    detected on it and on the generated image. It matches nail_boxes_1 (crop)
    to nail_boxes_2 (generated) by IoU so that only generated nails
    corresponding to a real, pre-existing nail get pasted back — any
    hallucinated/extra nail detected only in the generated image is ignored.

    It additionally matches finger_boxes (crop) to nail_boxes_1 by the same
    IoU algorithm, to find which finger each pre-existing nail sits on. For a
    matched nail, if the generated nail mask has grown far past the finger
    mask compared to how much the original nail mask overlapped that finger
    (nail/finger overlap area more than `finger_overlap_ratio`x larger after
    generation), the generated nail likely bled onto the finger — that nail's
    mask is excluded from pasting.

    For each hand:
      1. Match nail_boxes_1[i] <-> nail_boxes_2[j] by greedy highest-IoU
         assignment (no reuse of either side, and a match requires overlap).
      2. Match finger_boxes[f] <-> nail_boxes_1[i] the same way, to find each
         nail's finger.
      3. For each matched (i, j), if nail i has a matched finger f, drop the
         match when intersection(finger_masks[f], nail_masks_2[j]) is at
         least `finger_overlap_ratio` times intersection(finger_masks[f],
         nail_masks_1[i]).
      4. Union nail_masks_2 for the surviving j's -> composite mask.
      5. Dilate, undo resize (generated image + mask -> hand square space),
         undo crop padding, alpha-blend the patch onto the original image.

    Inputs (all collected via INPUT_IS_LIST):
      original_image  : [1] single original photo
      crop_info       : [1] JSON string from CropSquareByBoxes
      resize_info     : [1] JSON string from ResizeImage
      crop_images     : [N_hands] original image after crop+resize (what was fed to generation)
      generated_images: [N_hands] one generated hand image per hand
      nail_boxes_1    : [N_hands] JSON "[[x1,y1,x2,y2], ...]" — SAM3 "nail" boxes on crop_images
      nail_boxes_2    : [N_hands] JSON "[[x1,y1,x2,y2], ...]" — SAM3 "nail" boxes on generated_images
      nail_masks_1    : [N_hands] each [N_nails_1, H, W] — SAM3 "nail" masks on crop_images
      nail_masks_2    : [N_hands] each [N_nails_2, H, W] — SAM3 "nail" masks on generated_images,
                        index-aligned with nail_boxes_2
      finger_boxes    : [N_hands] JSON "[[x1,y1,x2,y2], ...]" — SAM3 "finger" boxes on crop_images
      finger_masks    : [N_hands] each [N_fingers, H, W] — SAM3 "finger" masks on crop_images,
                        index-aligned with finger_boxes
    """

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original_image": ("IMAGE",),
                "crop_info": ("STRING", {"forceInput": True}),
                "resize_info": ("STRING", {"forceInput": True}),
                "crop_images": ("IMAGE",),
                "generated_images": ("IMAGE",),
                "nail_boxes_1": ("STRING", {"forceInput": True}),
                "nail_boxes_2": ("STRING", {"forceInput": True}),
                "nail_masks_1": ("MASK",),
                "nail_masks_2": ("MASK",),
                "finger_boxes": ("STRING", {"forceInput": True}),
                "finger_masks": ("MASK",),
                "dilate_pixels": ("INT", {"default": 20, "min": 0, "max": 64, "step": 1}),
                "min_iou": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "finger_overlap_ratio": ("FLOAT", {"default": 3.0, "min": 1.0, "max": 20.0, "step": 0.1}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    OUTPUT_IS_LIST = (False,)
    FUNCTION = "paste_back"
    CATEGORY = "AINail/image"

    def paste_back(self, original_image, crop_info, resize_info,
                   crop_images, generated_images,
                   nail_boxes_1, nail_boxes_2, nail_masks_1, nail_masks_2,
                   finger_boxes, finger_masks,
                   dilate_pixels, min_iou, finger_overlap_ratio):
        orig = original_image[0].clone().float()   # [B, H, W, C]
        crops_info   = json.loads(crop_info[0])
        resizes_info = json.loads(resize_info[0])
        dilate_px    = dilate_pixels[0]
        min_iou_val  = min_iou[0]
        overlap_ratio = finger_overlap_ratio[0]

        for j, gen_image in enumerate(generated_images):
            c_info = crops_info[j]
            r_info = resizes_info[j]

            b1 = json.loads(nail_boxes_1[j])
            b2 = json.loads(nail_boxes_2[j])
            fb = json.loads(finger_boxes[j])

            hand_nail_masks_1 = nail_masks_1[j]
            if hand_nail_masks_1.dim() == 2:
                hand_nail_masks_1 = hand_nail_masks_1.unsqueeze(0)
            hand_nail_masks_2 = nail_masks_2[j]
            if hand_nail_masks_2.dim() == 2:
                hand_nail_masks_2 = hand_nail_masks_2.unsqueeze(0)
            hand_finger_masks = finger_masks[j]
            if hand_finger_masks.dim() == 2:
                hand_finger_masks = hand_finger_masks.unsqueeze(0)

            nail_matches = match_boxes_by_iou(b1, b2, min_iou_val)
            finger_matches = match_boxes_by_iou(fb, b1, min_iou_val)
            nail1_to_finger = {i1: f for f, i1, _ in finger_matches}

            matched_j = []
            for i1, j2, _ in nail_matches:
                f = nail1_to_finger.get(i1)
                if f is not None:
                    area_1 = mask_intersection_area(hand_finger_masks[f], hand_nail_masks_1[i1])
                    area_2 = mask_intersection_area(hand_finger_masks[f], hand_nail_masks_2[j2])
                    if area_2 >= overlap_ratio * area_1:
                        # Generated nail bled onto the finger far more than it originally did.
                        continue
                matched_j.append(j2)

            if not matched_j:
                # No generated nail corresponds to a pre-existing nail: skip this hand entirely.
                continue

            src_x1   = c_info["src_x1"]
            src_y1   = c_info["src_y1"]
            src_x2   = c_info["src_x2"]
            src_y2   = c_info["src_y2"]
            pad_left = c_info["pad_left"]
            pad_top  = c_info["pad_top"]
            size     = c_info["size"]
            ph       = src_y2 - src_y1
            pw       = src_x2 - src_x1

            resample = RESIZE_METHODS.get(r_info["method"], Image.LANCZOS)

            # Union only the matched generated-nail masks -> composite [H_gen, W_gen]
            composite_mask = hand_nail_masks_2[matched_j].max(dim=0).values.float()

            # Dilate composite mask
            composite_mask = dilate_mask(composite_mask, dilate_px)

            # Undo resize — generated image -> hand square space [B, size, size, C]
            frames = []
            for frame in gen_image.float():
                np_frame = (frame.cpu().numpy() * 255).astype(np.uint8)
                pil_img = Image.fromarray(np_frame)
                pil_img = pil_img.resize((size, size), resample)
                frames.append(np.array(pil_img).astype(np.float32) / 255.0)
            gen_square = torch.from_numpy(np.stack(frames))  # [B, size, size, C]

            # Extract patch from hand square (undo black padding) -> [B, ph, pw, C]
            patch = gen_square[:, pad_top:pad_top + ph, pad_left:pad_left + pw, :]

            # Undo resize — composite mask -> hand square space [size, size]
            mask_np = (composite_mask.cpu().numpy() * 255).astype(np.uint8)
            mask_pil = Image.fromarray(mask_np, mode='L')
            mask_pil = mask_pil.resize((size, size), Image.LANCZOS)
            mask_square = np.array(mask_pil).astype(np.float32) / 255.0

            # Extract mask patch from hand square (undo black padding) -> [ph, pw]
            mask_patch = mask_square[pad_top:pad_top + ph, pad_left:pad_left + pw]
            mask_patch = torch.from_numpy(mask_patch).unsqueeze(0).unsqueeze(-1).to(orig.device)  # [1, ph, pw, 1]

            # Alpha-blend patch onto original image
            patch = patch.to(orig.device)
            region = orig[:, src_y1:src_y2, src_x1:src_x2, :]
            orig[:, src_y1:src_y2, src_x1:src_x2, :] = (
                patch * mask_patch + region * (1.0 - mask_patch)
            )

        return (orig,)


NODE_CLASS_MAPPINGS = {
    "AINailPasteBackMatched": PasteBackMatched,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailPasteBackMatched": "Paste Back Matched (AINail)",
}
