import json
import numpy as np
import torch
from PIL import Image

from .paste_back import RESIZE_METHODS, dilate_mask


def mask_iou_matrix(masks_a, masks_b, threshold=0.5):
    """Pairwise IoU matrix [N_a, N_b] between two [N, H, W] mask sets.

    Computed as a single matmul over flattened binarized masks instead of a
    per-pair python loop, so it stays cheap even at mask resolution (a
    handful of nails/fingers against ~1024x1024 masks is a few milliseconds).
    Box IoU is not used for matching: a large/merged box can spuriously
    overlap several unrelated boxes, while the actual mask shapes disambiguate
    correctly.
    """
    n_a, n_b = masks_a.shape[0], masks_b.shape[0]
    if n_a == 0 or n_b == 0:
        return torch.zeros((n_a, n_b))
    a = (masks_a > threshold).float().reshape(n_a, -1)
    b = (masks_b > threshold).float().reshape(n_b, -1)
    inter = a @ b.T
    area_a = a.sum(dim=1, keepdim=True)
    area_b = b.sum(dim=1, keepdim=True).T
    union = area_a + area_b - inter
    return inter / union.clamp(min=1e-8)


def match_by_iou_matrix(iou_matrix, min_iou=0.0):
    """Greedy one-to-one matching over a precomputed IoU matrix [N_a, N_b].

    Highest-IoU pairs are assigned first; each side is used at most once. A
    pair is only eligible when its IoU is strictly greater than `min_iou`, so
    an isolated entry with no overlapping counterpart is left unmatched.

    Returns a list of (i, j, iou) for matched pairs.
    """
    n_a, n_b = iou_matrix.shape
    candidates = []
    for i in range(n_a):
        for j in range(n_b):
            iou = iou_matrix[i, j].item()
            if iou > min_iou:
                candidates.append((iou, i, j))
    candidates.sort(key=lambda t: t[0], reverse=True)

    used_a, used_b = set(), set()
    matches = []
    for iou, i, j in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matches.append((i, j, iou))
    return matches


def mask_intersection_area(mask_a, mask_b, threshold=0.5):
    """Pixel count of the binarized intersection of two [H, W] masks."""
    a = mask_a > threshold
    b = mask_b > threshold
    return (a & b).sum().item()


def merge_overlapping_masks(masks, containment_thresh=0.9, threshold=0.5):
    """Union together near-duplicate detections of the same object.

    SAM3 sometimes emits two boxes/masks for one physical nail: a large one
    covering the whole nail and a small one nested almost entirely inside it
    (covering only part of it). Left as-is, the smaller mask can win a 1:1
    match by IoU purely by chance, leaving the true full-extent mask (and
    the pixels it alone covers) unused. Merge any pair where the smaller
    mask's containment inside the larger one (intersection / smaller_area)
    is at least `containment_thresh`, replacing both with their union, and
    repeat until no more pairs qualify.
    """
    n = masks.shape[0]
    if n <= 1:
        return masks
    m = masks
    changed = True
    while changed and m.shape[0] > 1:
        n = m.shape[0]
        flat = (m > threshold).float().reshape(n, -1)
        areas = flat.sum(dim=1)
        inter = flat @ flat.T
        changed = False
        for i in range(n):
            for k in range(i + 1, n):
                min_area = min(areas[i].item(), areas[k].item())
                if min_area <= 0:
                    continue
                containment = inter[i, k].item() / min_area
                if containment >= containment_thresh:
                    merged = torch.max(m[i], m[k])
                    keep = [idx for idx in range(n) if idx not in (i, k)]
                    rest = m[keep] if keep else m[:0]
                    m = torch.cat([rest, merged.unsqueeze(0)], dim=0)
                    changed = True
                    break
            if changed:
                break
    return m


class PasteBackMatched:
    """Paste generated nail crops back onto the original image, restricted to
    nails that actually existed in the pre-generation crop.

    Compared to PasteBack, this node additionally receives the crop (i.e. the
    image actually fed into generation) and SAM3 "nail"/"finger" boxes/masks
    detected on it and on the generated image. It matches nail_masks_1 (crop)
    to nail_masks_2 (generated) by mask IoU so that only generated nails
    corresponding to a real, pre-existing nail get pasted back — any
    hallucinated/extra nail detected only in the generated image is ignored.
    Matching uses the masks rather than the boxes: a large/merged detection
    box can spuriously have high box-IoU with several unrelated smaller
    boxes, while the actual mask shapes disambiguate correctly.

    It additionally matches finger_masks (crop) to nail_masks_1 by the same
    IoU algorithm, to find which finger each pre-existing nail sits on. For a
    matched nail, if the generated nail mask has grown far past the finger
    mask compared to how much the original nail mask overlapped that finger
    (nail/finger overlap area more than `finger_overlap_ratio`x larger after
    generation), the generated nail likely bled onto the finger — that nail's
    mask is excluded from pasting.

    Before matching, near-duplicate detections of the same nail/finger (e.g. a
    small box nested almost entirely inside a larger one) are merged by union
    (see `merge_overlapping_masks`), so the smaller one can't win a match away
    from the true full-extent mask.

    For each hand:
      1. Match nail_masks_1[i] <-> nail_masks_2[j] by greedy highest-mask-IoU
         assignment (no reuse of either side, and a match requires overlap).
      2. Match finger_masks[f] <-> nail_masks_1[i] the same way, to find each
         nail's finger.
      3. For each matched (i, j), if nail i has a matched finger f, drop the
         match when intersection(finger_masks[f], nail_masks_2[j]) is at
         least `finger_overlap_ratio` times intersection(finger_masks[f],
         nail_masks_1[i]).
      4. Union nail_masks_1[i] with nail_masks_2[j] for each surviving (i, j)
         -> composite mask. Including the old (crop) nail mask ensures the
         full extent of the original nail is overwritten even when the new
         nail is smaller/shorter, instead of leaving a stale sliver of it.
      5. Dilate, undo resize (generated image + mask -> hand square space),
         undo crop padding, alpha-blend the patch onto the original image.

    Inputs (all collected via INPUT_IS_LIST):
      original_image  : [1] single original photo
      crop_info       : [1] JSON string from CropSquareByBoxes
      resize_info     : [1] JSON string from ResizeImage
      crop_images     : [N_hands] original image after crop+resize (what was fed to generation)
      generated_images: [N_hands] one generated hand image per hand
      nail_boxes_1    : [N_hands] JSON "[[x1,y1,x2,y2], ...]" — SAM3 "nail" boxes on crop_images
                        (accepted for graph-wiring parity with nail_masks_1; not used for matching)
      nail_boxes_2    : [N_hands] JSON "[[x1,y1,x2,y2], ...]" — SAM3 "nail" boxes on generated_images
                        (accepted for graph-wiring parity with nail_masks_2; not used for matching)
      nail_masks_1    : [N_hands] each [N_nails_1, H, W] — SAM3 "nail" masks on crop_images
      nail_masks_2    : [N_hands] each [N_nails_2, H, W] — SAM3 "nail" masks on generated_images,
                        index-aligned with nail_boxes_2
      finger_boxes    : [N_hands] JSON "[[x1,y1,x2,y2], ...]" — SAM3 "finger" boxes on crop_images
                        (accepted for graph-wiring parity with finger_masks; not used for matching)
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
                "dilate_pixels": ("INT", {"default": 10, "min": 0, "max": 64, "step": 1}),
                "min_iou": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "finger_overlap_ratio": ("FLOAT", {"default": 3.0, "min": 1.0, "max": 20.0, "step": 0.1}),
                "dedup_containment_thresh": ("FLOAT", {"default": 0.9, "min": 0.5, "max": 1.0, "step": 0.01}),
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
                   dilate_pixels, min_iou, finger_overlap_ratio,
                   dedup_containment_thresh):
        orig = original_image[0].clone().float()   # [B, H, W, C]
        crops_info   = json.loads(crop_info[0])
        resizes_info = json.loads(resize_info[0])
        dilate_px    = dilate_pixels[0]
        min_iou_val  = min_iou[0]
        overlap_ratio = finger_overlap_ratio[0]
        dedup_thresh = dedup_containment_thresh[0]

        for j, gen_image in enumerate(generated_images):
            c_info = crops_info[j]
            r_info = resizes_info[j]

            hand_nail_masks_1 = nail_masks_1[j]
            if hand_nail_masks_1.dim() == 2:
                hand_nail_masks_1 = hand_nail_masks_1.unsqueeze(0)
            hand_nail_masks_2 = nail_masks_2[j]
            if hand_nail_masks_2.dim() == 2:
                hand_nail_masks_2 = hand_nail_masks_2.unsqueeze(0)
            hand_finger_masks = finger_masks[j]
            if hand_finger_masks.dim() == 2:
                hand_finger_masks = hand_finger_masks.unsqueeze(0)

            # Collapse near-duplicate detections of the same nail/finger (e.g. a small
            # box nested inside a large one) before matching, so the smaller one can't
            # win a match away from the true full-extent mask.
            hand_nail_masks_1 = merge_overlapping_masks(hand_nail_masks_1, dedup_thresh)
            hand_nail_masks_2 = merge_overlapping_masks(hand_nail_masks_2, dedup_thresh)
            hand_finger_masks = merge_overlapping_masks(hand_finger_masks, dedup_thresh)

            nail_matches = match_by_iou_matrix(
                mask_iou_matrix(hand_nail_masks_1, hand_nail_masks_2), min_iou_val
            )
            finger_matches = match_by_iou_matrix(
                mask_iou_matrix(hand_finger_masks, hand_nail_masks_1), min_iou_val
            )
            nail1_to_finger = {i1: f for f, i1, _ in finger_matches}

            matched_pairs = []
            for i1, j2, _ in nail_matches:
                f = nail1_to_finger.get(i1)
                if f is not None:
                    area_1 = mask_intersection_area(hand_finger_masks[f], hand_nail_masks_1[i1])
                    area_2 = mask_intersection_area(hand_finger_masks[f], hand_nail_masks_2[j2])
                    if area_2 >= overlap_ratio * area_1:
                        # Generated nail bled onto the finger far more than it originally did.
                        continue
                matched_pairs.append((i1, j2))

            if not matched_pairs:
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

            # Union each matched pair's old (crop) + new (generated) nail mask, so the
            # old nail's full extent gets overwritten even where the new nail is smaller
            # -> composite [H_gen, W_gen]
            matched_i1 = [i1 for i1, _ in matched_pairs]
            matched_j2 = [j2 for _, j2 in matched_pairs]
            composite_mask = torch.cat([
                hand_nail_masks_1[matched_i1], hand_nail_masks_2[matched_j2],
            ], dim=0).max(dim=0).values.float()

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
