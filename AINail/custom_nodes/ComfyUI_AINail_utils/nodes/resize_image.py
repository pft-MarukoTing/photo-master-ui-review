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


class ResizeImage:
    """Resize images to target resolution. Outputs resize_info to restore original size."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "width": ("INT", {"default": 1024, "min": 1, "max": 16384, "step": 1}),
                "height": ("INT", {"default": 1024, "min": 1, "max": 16384, "step": 1}),
                "method": (list(RESIZE_METHODS.keys()),),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("resized", "resize_info")
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "resize"
    CATEGORY = "AINail/image"

    def resize(self, images: list, width: list, height: list, method: list):
        target_w = width[0]
        target_h = height[0]
        resample = RESIZE_METHODS[method[0]]

        resized_list = []
        info = []

        for image in images:
            # image: [B, H, W, C]
            _, H, W, C = image.shape
            frames = []

            for frame in image:
                # frame: [H, W, C] float32 0-1
                np_frame = (frame.clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
                pil_img = Image.fromarray(np_frame)
                pil_img = pil_img.resize((target_w, target_h), resample)
                frames.append(np.array(pil_img).astype(np.float32) / 255.0)

            resized_tensor = torch.from_numpy(np.stack(frames)).to(device=image.device, dtype=image.dtype)
            resized_list.append(resized_tensor)
            info.append({
                "orig_w": W, "orig_h": H,
                "target_w": target_w, "target_h": target_h,
                "method": method[0],
            })

        return (resized_list, json.dumps(info))


NODE_CLASS_MAPPINGS = {
    "AINailResizeImage": ResizeImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailResizeImage": "Resize Image (AINail)",
}
