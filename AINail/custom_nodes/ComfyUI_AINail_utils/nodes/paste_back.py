import json
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


RESIZE_METHODS = {
    "lanczos": Image.LANCZOS,
    "bilinear": Image.BILINEAR,
    "bicubic": Image.BICUBIC,
    "nearest": Image.NEAREST,
    "box": Image.BOX,
    "hamming": Image.HAMMING,
}


def dilate_mask(mask: torch.Tensor, radius: int) -> torch.Tensor:
    """Dilate a [H, W] mask by `radius` pixels using max pooling."""
    if radius <= 0:
        return mask
    kernel = radius * 2 + 1
    m = mask.unsqueeze(0).unsqueeze(0)  # [1, 1, H, W]
    m = F.max_pool2d(m, kernel_size=kernel, stride=1, padding=radius)
    return m.squeeze(0).squeeze(0)


class PasteBack:
    """Paste generated hand crops back onto the original image using nail masks.

    For each generated hand image:
      1. Combine all nail masks into a composite mask (union)
      2. Dilate the composite mask by `dilate_pixels`
      3. Undo resize: generated image → hand square space
      4. Extract patch from hand square (undo black padding) → original resolution
      5. Undo resize: composite mask → hand square space
      6. Extract mask patch from hand square (undo black padding)
      7. Alpha-blend patch onto original image guided by dilated nail mask

    Inputs (all collected via INPUT_IS_LIST):
      original_image  : [1] single original photo
      generated_images: [N_hands] one generated hand image per hand
      masks           : [N_hands] each is [N_nails, H_gen, W_gen] from SAM3
      crop_info       : [1] JSON string from CropSquareByBoxes
      resize_info     : [1] JSON string from ResizeImage
    """

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original_image": ("IMAGE",),
                "generated_images": ("IMAGE",),
                "masks": ("MASK",),
                "crop_info": ("STRING", {"forceInput": True}),
                "resize_info": ("STRING", {"forceInput": True}),
                "dilate_pixels": ("INT", {"default": 10, "min": 0, "max": 64, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    OUTPUT_IS_LIST = (False,)
    FUNCTION = "paste_back"
    CATEGORY = "AINail/image"

    def paste_back(self, original_image, generated_images, masks,
                   crop_info, resize_info, dilate_pixels):
        orig = original_image[0].clone().float()   # [B, H, W, C]
        crops_info   = json.loads(crop_info[0])
        resizes_info = json.loads(resize_info[0])
        dilate_px    = dilate_pixels[0]

        for j, (gen_image, hand_masks) in enumerate(zip(generated_images, masks)):
            c_info = crops_info[j]
            r_info = resizes_info[j]

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

            # Step 1: combine nail masks → composite [H_gen, W_gen]
            if hand_masks.dim() == 2:
                hand_masks = hand_masks.unsqueeze(0)
            composite_mask = hand_masks.max(dim=0).values.float()  # [H_gen, W_gen]

            # Step 2: dilate composite mask
            composite_mask = dilate_mask(composite_mask, dilate_px)

            # Step 3: undo resize — generated image → hand square space [B, size, size, C]
            frames = []
            for frame in gen_image.float():
                np_frame = (frame.cpu().numpy() * 255).astype(np.uint8)
                pil_img = Image.fromarray(np_frame)
                pil_img = pil_img.resize((size, size), resample)
                frames.append(np.array(pil_img).astype(np.float32) / 255.0)
            gen_square = torch.from_numpy(np.stack(frames))  # [B, size, size, C]

            # Step 4: extract patch from hand square (undo black padding) → [B, ph, pw, C]
            patch = gen_square[:, pad_top:pad_top + ph, pad_left:pad_left + pw, :]

            # Step 5: undo resize — composite mask → hand square space [size, size]
            mask_np = (composite_mask.cpu().numpy() * 255).astype(np.uint8)
            mask_pil = Image.fromarray(mask_np, mode='L')
            mask_pil = mask_pil.resize((size, size), Image.LANCZOS)
            mask_square = np.array(mask_pil).astype(np.float32) / 255.0

            # Step 6: extract mask patch from hand square (undo black padding) → [ph, pw]
            mask_patch = mask_square[pad_top:pad_top + ph, pad_left:pad_left + pw]
            mask_patch = torch.from_numpy(mask_patch).unsqueeze(0).unsqueeze(-1).to(orig.device)  # [1, ph, pw, 1]

            # Step 7: alpha-blend patch onto original image
            patch = patch.to(orig.device)
            region = orig[:, src_y1:src_y2, src_x1:src_x2, :]
            orig[:, src_y1:src_y2, src_x1:src_x2, :] = (
                patch * mask_patch + region * (1.0 - mask_patch)
            )

        return (orig,)


NODE_CLASS_MAPPINGS = {
    "AINailPasteBack": PasteBack,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailPasteBack": "Paste Back (AINail)",
}
