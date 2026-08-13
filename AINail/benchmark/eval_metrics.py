#!/usr/bin/env python3
"""
eval_metrics.py -- pixel-level metrics comparing a generated benchmark output
to its ground-truth target.

Uses PSNR (dB) rather than raw MSE for the headline numbers -- MSE's scale
depends on image content and isn't intuitive on its own, whereas PSNR has
widely-known reference bands (>40dB near-identical, 30-40dB good, 20-30dB
visibly off, <20dB bad).

Beyond the whole-image score, this also splits the comparison into the nail
region vs everything outside it (hand shape, skin, background), using the
same pixel-diff technique already validated in
prepare_datasets/evaluate_data_pairs/plan.md (dimension 1: outside-mask SSIM
between an aligned pre/post pair was stable at 0.98-0.99 across samples,
using nothing more than a Lab-space pixel diff -- no model needed). Since
`source` and `target` here are pixel-aligned the same way `control_1`/
`target` were in that dataset, the same mask works to answer the question
this benchmark is actually for: did the new checkpoint change the nail
region (expected), or did it also change the hand/background (a regression)?
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


def global_psnr(a: np.ndarray, b: np.ndarray) -> float:
    return _psnr_from_mse(_mse(a, b))


def global_ssim(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _align(a, b)
    return float(ssim(a, b, channel_axis=2, data_range=255))


def nail_mask_from_source_target(source: np.ndarray, target: np.ndarray, lab_threshold: float = 5.0) -> np.ndarray:
    """Boolean mask (True = nail region) of where source and target disagree,
    i.e. the region the ground-truth edit actually touched."""
    source, target = _align(source, target)
    diff = np.linalg.norm(rgb2lab(source) - rgb2lab(target), axis=-1)
    return diff > lab_threshold


def _masked_ssim(generated: np.ndarray, target: np.ndarray, region_mask: np.ndarray) -> float:
    """Mean SSIM over the given region. SSIM is computed once over the whole
    image (its per-pixel map already reflects local windows, i.e. neighboring
    pixels including context outside the region), then averaged over just the
    pixels where region_mask is True -- same technique whether the region is
    the nail mask itself or its inverse."""
    generated, target = _align(generated, target)
    if region_mask.sum() == 0:
        return float("nan")
    _, ssim_map = ssim(generated, target, channel_axis=2, data_range=255, full=True)
    return float(ssim_map.mean(axis=-1)[region_mask].mean())


def background_ssim(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """SSIM restricted to pixels OUTSIDE the nail mask (hand shape, skin, background).

    This is the regression signal: it should stay ~as high as the
    plan.md baseline (0.98-0.99) regardless of nail-transfer quality. A drop
    means the new checkpoint is altering things it shouldn't touch.
    """
    return _masked_ssim(generated, target, ~mask)


def background_psnr(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """PSNR restricted to pixels OUTSIDE the nail mask -- the PSNR counterpart
    to background_ssim."""
    return _psnr_from_mse(_masked_mse(generated, target, ~mask))


def nail_mask_ssim(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """SSIM restricted to the nail region -- a more perceptually-grounded
    complement to nail_mask_psnr for judging transfer quality. Can be noisier
    than background_ssim since the nail region is usually much smaller."""
    return _masked_ssim(generated, target, mask)


def nail_mask_psnr(generated: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
    """PSNR restricted to the nail region -- how close the transferred nail
    style is to ground truth."""
    return _psnr_from_mse(_masked_mse(generated, target, mask))
