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


class DirectPasteBack:
    """Directly overwrite the original image's crop region with the generated image.

    For each generated image:
      1. Undo resize: generated image → square space
      2. Extract patch from square (undo black padding) → original resolution
      3. Overwrite the corresponding region in the original image

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
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    OUTPUT_IS_LIST = (False,)
    FUNCTION = "paste_back"
    CATEGORY = "AINail/image"

    def paste_back(self, original_image, generated_images, crop_info, resize_info):
        orig = original_image[0].clone().float()   # [B, H, W, C]
        crops_info   = json.loads(crop_info[0])
        resizes_info = json.loads(resize_info[0])

        for j, gen_image in enumerate(generated_images):
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

            # Step 1: undo resize — generated image → square space [B, size, size, C]
            frames = []
            for frame in gen_image.float():
                np_frame = (frame.cpu().numpy() * 255).astype(np.uint8)
                pil_img = Image.fromarray(np_frame)
                pil_img = pil_img.resize((size, size), resample)
                frames.append(np.array(pil_img).astype(np.float32) / 255.0)
            gen_square = torch.from_numpy(np.stack(frames))

            # Step 2: extract patch from square (undo black padding) → [B, ph, pw, C]
            patch = gen_square[:, pad_top:pad_top + ph, pad_left:pad_left + pw, :]

            # Step 3: overwrite region in original image
            orig[:, src_y1:src_y2, src_x1:src_x2, :] = patch.to(orig.device)

        return (orig,)


NODE_CLASS_MAPPINGS = {
    "AINailDirectPasteBack": DirectPasteBack,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailDirectPasteBack": "Direct Paste Back (AINail)",
}
