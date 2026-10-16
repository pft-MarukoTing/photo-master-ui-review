"""
AIDoubleChinPasteBack -- FaceRegionPasteBack (ComfyUI-FaceRestorer) that keeps
the source's own full-resolution pixels wherever the LoRA did not need to
change them.

The crop goes resize-to-1MP -> VAE -> 4-step sampling -> resize back, so every
pixel of it comes back softer (eyelashes, pores, hair), worse the larger the
source crop is. FaceRegionPasteBack pastes all of it, feathered only at the
crop border.

    mask   only the area the LoRA changed takes the result: below the nose
           (from the crop node's landmarks) AND where the low-pass result
           differs from the source; the rest stays the source pixels
    crop   FaceRegionPasteBack's behaviour (baseline for comparisons)

Sizes are in inference-crop pixels (~1024 side), so they mean the same at any
input resolution.
"""

import cv2
import numpy as np
import torch

MODES = ["mask", "crop"]


def _smoothstep(lo, hi, x):
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def edge_feather(h, w):
    """FaceDetailRestorer.paste_back_crop_region's border feather."""
    feather = int(min(h, w) * 0.1) | 1
    fade = max(1, int(feather * 0.8))
    m = np.ones((h, w), np.float32)
    m[:fade] = 0
    m[-fade:] = 0
    m[:, :fade] = 0
    m[:, -fade:] = 0
    return cv2.GaussianBlur(m, (feather, feather), 0)


def lower_face_prior(meta, x0, y0, h, w):
    """Soft 0..1 map of the (x0, y0, w, h) image window: 1 below the nose bottom,
    measured perpendicular to the eye line toward the chin, 0 above. None without
    landmarks."""
    if not meta or not meta.get("success"):
        return None
    M = np.asarray(meta["M"], np.float32)  # full image -> aligned crop, scale + shift only
    pts = (np.asarray(meta["pts"], np.float32) - M[:, 2]) / np.array([M[0, 0], M[1, 1]], np.float32)
    eyes = pts[42:48].mean(0) - pts[36:42].mean(0)
    down = np.array([-eyes[1], eyes[0]]) / (np.linalg.norm(eyes) + 1e-6)
    if np.dot(pts[8] - pts[33], down) < 0:  # toward the chin, not image-down: holds at any roll
        down = -down
    band = 0.25 * np.linalg.norm(eyes)
    xs = np.arange(x0, x0 + w, dtype=np.float32) - pts[33, 0]
    ys = np.arange(y0, y0 + h, dtype=np.float32) - pts[33, 1]
    dist = ys[:, None] * down[1] + xs[None, :] * down[0]
    return _smoothstep(-band, band, dist).astype(np.float32)


def change_mask(src, gen, threshold):
    """src/gen: uint8 BGR at inference scale. Soft 0..1 mask of where gen's low
    frequencies differ from src's by more than `threshold` (Lab, 0-255 scale).
    The low-pass cancels the lost texture/edge sharpness (zero-mean), which at
    finer scales outweighs subtle edits; a moved jawline still survives it."""
    side = max(src.shape[:2])
    d = np.abs(cv2.GaussianBlur(cv2.cvtColor(src, cv2.COLOR_BGR2LAB).astype(np.float32)
                                - cv2.cvtColor(gen, cv2.COLOR_BGR2LAB).astype(np.float32), (0, 0), 4))
    d = np.maximum(d[..., 0], 1.5 * np.maximum(d[..., 1], d[..., 2]))
    m = (d > threshold).astype(np.uint8)
    # Drop specks by extent, not area: a shifted jawline is a thin but long line.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    small = np.maximum(stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]) < side * 0.03
    small[0] = True
    m[small[labels]] = 0
    k = int(side * 0.03) | 1
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return cv2.GaussianBlur(m.astype(np.float32), (0, 0), side * 0.01)


def paste_back(full_bgr, crop_bgr, crop_info, mode="mask", meta=None, change_threshold=6.0):
    """full_bgr: uint8 source; crop_bgr: uint8 LoRA result of the crop_info=(x1, y1, x2, y2) region."""
    x1, y1, x2, y2 = crop_info
    h_img, w_img = full_bgr.shape[:2]
    tw, th = x2 - x1, y2 - y1
    dx1, dy1, dx2, dy2 = max(0, x1), max(0, y1), min(w_img, x2), min(h_img, y2)
    if tw <= 0 or th <= 0 or dx2 <= dx1 or dy2 <= dy1:
        return full_bgr
    scale = tw / crop_bgr.shape[1]  # source px per inference px
    inside = (slice(dy1 - y1, dy2 - y1), slice(dx1 - x1, dx2 - x1))
    gen = cv2.resize(crop_bgr, (tw, th), interpolation=cv2.INTER_LINEAR)[inside]
    src = full_bgr[dy1:dy2, dx1:dx2]
    alpha = edge_feather(th, tw)[inside]

    if mode == "mask":
        work = (max(1, round(gen.shape[1] / scale)), max(1, round(gen.shape[0] / scale)))
        m = change_mask(cv2.resize(src, work, interpolation=cv2.INTER_AREA),
                        cv2.resize(gen, work, interpolation=cv2.INTER_AREA), change_threshold)
        m = cv2.resize(m, (gen.shape[1], gen.shape[0]), interpolation=cv2.INTER_LINEAR)
        prior = lower_face_prior(meta, dx1, dy1, gen.shape[0], gen.shape[1])
        alpha = alpha * (m if prior is None else m * prior)

    a = alpha[..., None]
    out = full_bgr.copy()
    out[dy1:dy2, dx1:dx2] = np.clip(src * (1.0 - a) + gen * a + 0.5, 0, 255).astype(np.uint8)
    return out


class AIDoubleChinPasteBack:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_original": ("IMAGE",),
                "image_processed_crop": ("IMAGE",),
                "crop_info": ("CROP_INFO",),
                "mode": (MODES, {"default": "mask"}),
                "change_threshold": ("FLOAT", {"default": 6.0, "min": 1.0, "max": 50.0, "step": 0.5,
                                               "tooltip": "mask: low-pass Lab difference (0-255 scale) that counts as changed"}),
            },
            "optional": {
                "face_metadata": ("FACE_METADATA",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "paste"
    CATEGORY = "AIDoubleChin"

    def paste(self, image_original, image_processed_crop, crop_info, mode, change_threshold, face_metadata=None):
        out = []
        for i in range(image_original.shape[0]):
            full = cv2.cvtColor((image_original[i].cpu().numpy() * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
            j = i if i < image_processed_crop.shape[0] else 0
            crop = cv2.cvtColor((image_processed_crop[j].cpu().numpy() * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
            info = crop_info[i] if i < len(crop_info) else crop_info[-1]
            meta = face_metadata[min(i, len(face_metadata) - 1)] if face_metadata else None
            res = paste_back(full, crop, info, mode, meta, change_threshold)
            out.append(torch.from_numpy(cv2.cvtColor(res, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0))
        return (torch.stack(out),)


NODE_CLASS_MAPPINGS = {"AIDoubleChinPasteBack": AIDoubleChinPasteBack}
NODE_DISPLAY_NAME_MAPPINGS = {"AIDoubleChinPasteBack": "AIDoubleChin Paste Back (edited area only)"}
