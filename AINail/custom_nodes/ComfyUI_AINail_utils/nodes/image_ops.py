import numpy as np
from PIL import Image


def resize_via_pil(arr: np.ndarray, target_h: int, target_w: int, resample=Image.LANCZOS) -> np.ndarray:
    """Resize a [H, W] or [H, W, C] float array in [0, 1] via PIL, round-tripping through uint8.

    Callers are responsible for clamping to [0, 1] first if their input isn't
    already guaranteed to be in range — this function does not clip, so it
    behaves identically for callers that never clamped before.
    """
    mode = "L" if arr.ndim == 2 else None
    pil_img = Image.fromarray((arr * 255).astype(np.uint8), mode=mode)
    resized = np.array(pil_img.resize((target_w, target_h), resample))
    return resized.astype(np.float32) / 255.0
