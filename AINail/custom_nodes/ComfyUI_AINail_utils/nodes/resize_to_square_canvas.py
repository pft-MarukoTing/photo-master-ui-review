import numpy as np
import torch

from .image_ops import resize_via_pil


class ResizeToSquareCanvas:
    """Turn an arbitrary-aspect-ratio image into a fixed canvas_size x canvas_size
    square: resize so the longest side fits canvas_size (aspect preserved), then
    center the result on a black canvas_size x canvas_size canvas.

    If the image's longest-side-fitted size already equals its current size
    (e.g. it's already exactly canvas_size x canvas_size), the resize step is
    skipped entirely so the pixels are pasted through unchanged -- no PIL
    round-trip, no partial-pixel offset, no black border.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "canvas_size": ("INT", {"default": 1024, "min": 1, "max": 16384, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"
    CATEGORY = "AINail/image"

    def run(self, image, canvas_size):
        # image: [B, H, W, C] float32 in [0, 1]
        B, H, W, C = image.shape
        scale = canvas_size / max(H, W)
        new_h = max(1, round(H * scale))
        new_w = max(1, round(W * scale))

        offset_y = (canvas_size - new_h) // 2
        offset_x = (canvas_size - new_w) // 2

        canvas = torch.zeros((B, canvas_size, canvas_size, C), dtype=image.dtype, device=image.device)
        for b in range(B):
            frame = image[b]
            if (new_h, new_w) != (H, W):
                np_frame = frame.clamp(0, 1).cpu().numpy().astype(np.float32)
                np_frame = resize_via_pil(np_frame, new_h, new_w)
                frame = torch.from_numpy(np_frame).to(device=image.device, dtype=image.dtype)
            canvas[b, offset_y:offset_y + new_h, offset_x:offset_x + new_w, :] = frame

        return (canvas,)


NODE_CLASS_MAPPINGS = {
    "AINailResizeToSquareCanvas": ResizeToSquareCanvas,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailResizeToSquareCanvas": "Resize to Square Canvas (AINail)",
}
