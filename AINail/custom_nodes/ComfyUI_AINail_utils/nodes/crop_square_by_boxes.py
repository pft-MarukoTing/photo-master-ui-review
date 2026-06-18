import json
import torch


class CropSquareByBoxes:
    """Crop square regions from an image using SAM3 boxes output.
    Expands each box to a square and pads with black where the region exceeds image bounds.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "boxes": ("STRING", {"forceInput": True}),
                "scale": ("FLOAT", {"default": 1.2, "min": 1.0, "max": 10.0, "step": 0.05}),
                "min_area_ratio": ("FLOAT", {"default": 0.005, "min": 0.0, "max": 1.0, "step": 0.001}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("crops", "crop_info")
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "crop"
    CATEGORY = "AINail/image"

    def crop(self, image: torch.Tensor, boxes: str, scale: float, min_area_ratio: float):
        # image: [B, H, W, C]
        boxes_list = json.loads(boxes)
        if not boxes_list:
            _, H, W, C = image.shape
            size = max(H, W)
            offset_x = (size - W) // 2
            offset_y = (size - H) // 2
            canvas = torch.zeros((image.shape[0], size, size, C),
                                 dtype=image.dtype, device=image.device)
            canvas[:, offset_y:offset_y + H, offset_x:offset_x + W, :] = image
            info = [{
                "src_x1": 0, "src_y1": 0, "src_x2": W, "src_y2": H,
                "pad_left": offset_x, "pad_top": offset_y,
                "size": size, "orig_w": W, "orig_h": H,
            }]
            return ([canvas], json.dumps(info))

        _, H, W, C = image.shape
        crops = []
        info = []

        image_area = H * W

        for box in boxes_list:
            x1, y1, x2, y2 = box

            box_area = (x2 - x1) * (y2 - y1)
            if box_area < image_area * min_area_ratio:
                continue

            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            half = max((x2 - x1), (y2 - y1)) / 2.0 * scale

            # derive size first, then compute top-left from center to avoid rounding mismatch
            size = int(round(half * 2))
            rx1 = int(round(cx - size / 2))
            ry1 = int(round(cy - size / 2))
            rx2 = rx1 + size
            ry2 = ry1 + size

            # clamp to image bounds
            sx1 = max(0, rx1)
            sy1 = max(0, ry1)
            sx2 = min(W, rx2)
            sy2 = min(H, ry2)

            # padding offsets (where the cropped patch sits inside the square canvas)
            pad_left = sx1 - rx1
            pad_top  = sy1 - ry1

            canvas = torch.zeros((image.shape[0], size, size, C),
                                 dtype=image.dtype, device=image.device)
            patch = image[:, sy1:sy2, sx1:sx2, :]
            ph, pw = patch.shape[1], patch.shape[2]
            canvas[:, pad_top:pad_top + ph, pad_left:pad_left + pw, :] = patch

            crops.append(canvas)
            info.append({
                # where the patch was taken from in the original image
                "src_x1": sx1, "src_y1": sy1,
                "src_x2": sx2, "src_y2": sy2,
                # where the patch sits inside the square canvas
                "pad_left": pad_left, "pad_top": pad_top,
                # square canvas size
                "size": size,
                # original image size
                "orig_w": W, "orig_h": H,
            })

        if not crops:
            size = max(H, W)
            offset_x = (size - W) // 2
            offset_y = (size - H) // 2
            canvas = torch.zeros((image.shape[0], size, size, C),
                                 dtype=image.dtype, device=image.device)
            canvas[:, offset_y:offset_y + H, offset_x:offset_x + W, :] = image
            crops = [canvas]
            info = [{
                "src_x1": 0, "src_y1": 0, "src_x2": W, "src_y2": H,
                "pad_left": offset_x, "pad_top": offset_y,
                "size": size, "orig_w": W, "orig_h": H,
            }]

        return (crops, json.dumps(info))


NODE_CLASS_MAPPINGS = {
    "AINailCropSquareByBoxes": CropSquareByBoxes,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailCropSquareByBoxes": "Crop Square by Boxes (AINail)",
}
