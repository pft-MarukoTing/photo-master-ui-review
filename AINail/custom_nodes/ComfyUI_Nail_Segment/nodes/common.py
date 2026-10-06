import numpy as np
import torch

CATEGORY = "NailSegment"


def to_rgb_u8_list(image: torch.Tensor):
    """ComfyUI IMAGE [B,H,W,C] float 0..1 -> list of HxWx3 uint8 RGB."""
    arr = (image[..., :3].detach().cpu().float().clamp(0, 1) * 255.0).round().byte().numpy()
    return [np.ascontiguousarray(a) for a in arr]


def bgr_u8_to_image(bgr_list):
    """list of HxWx3 uint8 BGR -> IMAGE [B,H,W,3] float 0..1 (RGB)."""
    arr = np.stack([b[:, :, ::-1] for b in bgr_list]).astype(np.float32) / 255.0
    return torch.from_numpy(arr)
