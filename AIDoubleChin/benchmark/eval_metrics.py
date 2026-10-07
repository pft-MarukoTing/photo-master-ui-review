#!/usr/bin/env python3
"""
eval_metrics.py -- pixel-level metrics comparing a generated benchmark output
to its ground-truth target.

Uses PSNR (dB) rather than raw MSE for the headline numbers -- MSE's scale
depends on image content, whereas PSNR has widely-known reference bands
(>40dB near-identical, 30-40dB good, 20-30dB visibly off, <20dB bad).

Beyond the whole-image score, the comparison is split into the edited region
(where the retoucher changed the image, i.e. chin/neck) vs everything outside
it (face, hair, clothing, background). control and target are pixel-aligned
(datasets/prepare_pairs.py pads both identically), so a Lab-space pixel diff
between them is enough to locate the edit -- no model needed.
"""

import numpy as np
from PIL import Image
from skimage.color import rgb2lab
from skimage.metrics import structural_similarity as ssim

MAX_PIXEL_VALUE = 255.0


def load_rgb(path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"))


def _align(a: np.ndarray, b: np.ndarray):
    """Resizes b to a's shape if they differ, so metrics never crash on
    off-by-a-few-pixels outputs."""
    if a.shape[:2] != b.shape[:2]:
        b = np.array(Image.fromarray(b).resize((a.shape[1], a.shape[0]), Image.LANCZOS))
    return a, b


def _mse(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _align(a, b)
    return float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))


def _masked_mse(generated: np.ndarray, target: np.ndarray, region_mask: np.ndarray) -> float:
    generated, target = _align(generated, target)
    if region_mask.sum() == 0:
        return float("nan")
    diff = (generated.astype(np.float64) - target.astype(np.float64)) ** 2
    return float(diff.mean(axis=-1)[region_mask].mean())


def _psnr_from_mse(mse_value: float) -> float:
    if mse_value != mse_value:  # NaN (empty region)
        return float("nan")
    if mse_value == 0:
        return float("inf")
    return float(10 * np.log10((MAX_PIXEL_VALUE ** 2) / mse_value))


def _masked_ssim(generated: np.ndarray, target: np.ndarray, region_mask: np.ndarray) -> float:
    """Mean SSIM over the given region. SSIM is computed once over the whole
    image (its per-pixel map already reflects local windows), then averaged
    over just the pixels where region_mask is True."""
    generated, target = _align(generated, target)
    if region_mask.sum() == 0:
        return float("nan")
    _, ssim_map = ssim(generated, target, channel_axis=2, data_range=255, full=True)
    return float(ssim_map.mean(axis=-1)[region_mask].mean())


def global_psnr(a: np.ndarray, b: np.ndarray) -> float:
    return _psnr_from_mse(_mse(a, b))


def global_ssim(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _align(a, b)
    return float(ssim(a, b, channel_axis=2, data_range=255))


def edit_mask_from_control_target(control: np.ndarray, target: np.ndarray, lab_threshold: float = 5.0) -> np.ndarray:
    """Boolean mask (True = edited region) of where control and target
    disagree, i.e. the region the ground-truth retouch actually touched."""
    control, target = _align(control, target)
    diff = np.linalg.norm(rgb2lab(control) - rgb2lab(target), axis=-1)
    return diff > lab_threshold


def edit_psnr(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """PSNR inside the edited region -- how close the generated chin is to ground truth."""
    return _psnr_from_mse(_masked_mse(generated, target, mask))


def edit_ssim(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """SSIM inside the edited region -- perceptual complement to edit_psnr."""
    return _masked_ssim(generated, target, mask)


def background_psnr(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """PSNR outside the edited region -- the regression signal."""
    return _psnr_from_mse(_masked_mse(generated, target, ~mask))


def background_ssim(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """SSIM outside the edited region. Should stay high regardless of edit
    quality; a drop means the LoRA is altering things it shouldn't touch."""
    return _masked_ssim(generated, target, ~mask)
