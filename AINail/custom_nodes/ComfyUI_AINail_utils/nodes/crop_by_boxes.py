import json
import torch


class CropByBoxes:
    """Crop regions from an image using SAM3 boxes output, with optional scale expansion."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "boxes": ("STRING", {"forceInput": True}),
                "scale": ("FLOAT", {"default": 1.2, "min": 1.0, "max": 10.0, "step": 0.05}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("crops", "crop_info")
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "crop"
    CATEGORY = "AINail/image"

    def crop(self, image: torch.Tensor, boxes: str, scale: float):
        # image shape: [B, H, W, C]
        boxes_list = json.loads(boxes)
        if not boxes_list:
            _, H, W, _ = image.shape
            info = [{"x1": 0, "y1": 0, "x2": W, "y2": H, "orig_w": W, "orig_h": H}]
            return ([image], json.dumps(info))

        _, H, W, _ = image.shape
        crops = []
        info = []

        for box in boxes_list:
            x1, y1, x2, y2 = box

            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            half_w = (x2 - x1) / 2.0 * scale
            half_h = (y2 - y1) / 2.0 * scale

            sx1 = max(0, int(cx - half_w))
            sy1 = max(0, int(cy - half_h))
            sx2 = min(W, int(cx + half_w))
            sy2 = min(H, int(cy + half_h))

            crops.append(image[:, sy1:sy2, sx1:sx2, :])
            info.append({
                "x1": sx1, "y1": sy1,
                "x2": sx2, "y2": sy2,
                "orig_w": W, "orig_h": H,
            })

        return (crops, json.dumps(info))


NODE_CLASS_MAPPINGS = {
    "AINailCropByBoxes": CropByBoxes,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailCropByBoxes": "Crop by Boxes (AINail)",
}
