import json
import torch


class PadToSquare:
    """Pad images to square with black borders. Outputs pad_info to reverse the padding."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("padded", "pad_info")
    INPUT_IS_LIST = (True,)
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "pad"
    CATEGORY = "AINail/image"

    def pad(self, images: list):
        padded_list = []
        info = []

        for image in images:
            # image: [B, H, W, C]
            _, H, W, C = image.shape
            size = max(H, W)

            offset_x = (size - W) // 2
            offset_y = (size - H) // 2

            canvas = torch.zeros((image.shape[0], size, size, C),
                                 dtype=image.dtype, device=image.device)
            canvas[:, offset_y:offset_y + H, offset_x:offset_x + W, :] = image

            padded_list.append(canvas)
            info.append({
                "x1": offset_x, "y1": offset_y,
                "x2": offset_x + W, "y2": offset_y + H,
                "orig_w": W, "orig_h": H,
                "padded_size": size,
            })

        return (padded_list, json.dumps(info))


NODE_CLASS_MAPPINGS = {
    "AINailPadToSquare": PadToSquare,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailPadToSquare": "Pad to Square (AINail)",
}
