import json
import numpy as np
import torch
from PIL import Image


RESIZE_METHODS = {
    "lanczos": Image.LANCZOS,
    "bilinear": Image.BILINEAR,
    "bicubic": Image.BICUBIC,
    "nearest": Image.NEAREST,
    "box": Image.BOX,
    "hamming": Image.HAMMING,
}


def _feather_mask(ph, pw, feather_ratio, device):
    """Build a [ph, pw] blending mask: 0 at edges, 1 in the interior."""
    fh = max(1, int(feather_ratio * ph))
    fw = max(1, int(feather_ratio * pw))
    ramp_y = torch.ones(ph, device=device)
    ramp_x = torch.ones(pw, device=device)
    if fh > 0:
        t = torch.linspace(0, 1, fh + 1, device=device)[1:]
        ramp_y[:fh]  = t
        ramp_y[-fh:] = t.flip(0)
    if fw > 0:
        t = torch.linspace(0, 1, fw + 1, device=device)[1:]
        ramp_x[:fw]  = t
        ramp_x[-fw:] = t.flip(0)
    return torch.minimum(ramp_y.unsqueeze(1), ramp_x.unsqueeze(0))  # [ph, pw]


def _extract_patch(gen_image, c_info, r_info):
    """Undo resize + padding: returns patch [B, ph, pw, C] and (ph, pw)."""
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
    frames = []
    for frame in gen_image.float():
        np_frame = (frame.cpu().numpy() * 255).astype(np.uint8)
        pil_img = Image.fromarray(np_frame).resize((size, size), resample)
        frames.append(np.array(pil_img).astype(np.float32) / 255.0)
    gen_square = torch.from_numpy(np.stack(frames))
    patch = gen_square[:, pad_top:pad_top + ph, pad_left:pad_left + pw, :]
    return patch, ph, pw, src_x1, src_y1, src_x2, src_y2


class DirectPasteBack:
    """Directly overwrite the original image's crop region with the generated image.

    For each generated image:
      1. Undo resize: generated image → square space
      2. Extract patch from square (undo black padding) → original resolution
      3. Blend the patch into the original image with a feather mask

    Inputs (all collected via INPUT_IS_LIST):
      original_image  : [1] single original photo
      generated_images: [N] one generated image per crop
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
                "crop_info": ("STRING", {"forceInput": True}),
                "resize_info": ("STRING", {"forceInput": True}),
                "feather_ratio": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 0.5, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    OUTPUT_IS_LIST = (False,)
    FUNCTION = "paste_back"
    CATEGORY = "AINail/image"

    def paste_back(self, original_image, generated_images, crop_info, resize_info, feather_ratio):
        orig = original_image[0].clone().float()   # [B, H, W, C]
        crops_info   = json.loads(crop_info[0])
        resizes_info = json.loads(resize_info[0])
        feather_ratio = feather_ratio[0]

        for j, gen_image in enumerate(generated_images):
            patch, ph, pw, src_x1, src_y1, src_x2, src_y2 = _extract_patch(
                gen_image, crops_info[j], resizes_info[j]
            )
            patch = patch.to(orig.device)
            if feather_ratio > 0.0:
                mask = _feather_mask(ph, pw, feather_ratio, orig.device)  # [ph, pw]
                mask = mask.unsqueeze(0).unsqueeze(-1)                     # [1, ph, pw, 1]
                orig_region = orig[:, src_y1:src_y2, src_x1:src_x2, :]
                orig[:, src_y1:src_y2, src_x1:src_x2, :] = mask * patch + (1.0 - mask) * orig_region
            else:
                orig[:, src_y1:src_y2, src_x1:src_x2, :] = patch

        return (orig,)


class DirectPasteBackDebug:
    """Debug node: visualise feather mask and masked patch for a single crop.

    Mirrors DirectPasteBack inputs but outputs the intermediate tensors for a
    chosen crop_index so they can be wired to a Preview Image node.
    """

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generated_images": ("IMAGE",),
                "crop_info": ("STRING", {"forceInput": True}),
                "resize_info": ("STRING", {"forceInput": True}),
                "feather_ratio": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 0.5, "step": 0.01}),
                "crop_index": ("INT", {"default": 0, "min": 0, "max": 99, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("mask_vis", "masked_patch")
    OUTPUT_IS_LIST = (False, False)
    FUNCTION = "debug"
    CATEGORY = "AINail/image"

    def debug(self, generated_images, crop_info, resize_info, feather_ratio, crop_index):
        crops_info   = json.loads(crop_info[0])
        resizes_info = json.loads(resize_info[0])
        feather_ratio = feather_ratio[0]
        idx = min(crop_index[0], len(generated_images) - 1)

        patch, ph, pw, *_ = _extract_patch(
            generated_images[idx], crops_info[idx], resizes_info[idx]
        )

        mask = _feather_mask(ph, pw, feather_ratio, patch.device)  # [ph, pw]

        # mask_vis: grayscale → RGB IMAGE [1, ph, pw, 3]
        mask_rgb = mask.unsqueeze(0).unsqueeze(-1).expand(1, ph, pw, 3)

        # masked_patch: [1, ph, pw, C]
        masked = mask.unsqueeze(0).unsqueeze(-1) * patch

        return (mask_rgb, masked)


NODE_CLASS_MAPPINGS = {
    "AINailDirectPasteBack": DirectPasteBack,
    "AINailDirectPasteBackDebug": DirectPasteBackDebug,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailDirectPasteBack": "Direct Paste Back (AINail)",
    "AINailDirectPasteBackDebug": "Direct Paste Back Debug (AINail)",
}
