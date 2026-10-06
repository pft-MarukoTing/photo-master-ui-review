"""Debug overlays equivalent to the blend_* folders written by TestRealImage(). All drawing is BGR (cv2)."""
import cv2
import numpy as np

from .geometry import get_nail_fit_roi_corners, get_vec_debug_mask
from .pipeline import NAIL_COUNT, PATCH_SIZE

DEBUG_COLOR_R = [255, 128, 0, 0, 0, 0, 0, 0, 255, 128]
DEBUG_COLOR_G = [0, 0, 255, 128, 0, 0, 255, 128, 255, 128]
DEBUG_COLOR_B = [0, 0, 0, 0, 255, 128, 255, 128, 0, 0]


def nail_color(i):
    """BGR colour used for nail i in the original (index*2 into the debug palette)."""
    return (DEBUG_COLOR_B[i * 2], DEBUG_COLOR_G[i * 2], DEBUG_COLOR_R[i * 2])


def _blend(base_bgr, weight, color_bgr):
    out = np.zeros_like(base_bgr)
    weight = np.clip(weight, 0.0, 1.0)
    for c in range(3):
        out[:, :, c] = (weight * color_bgr[c] + (1.0 - weight) * base_bgr[:, :, c]).astype(np.uint8)
    return out


def blend_heat(image_bgr, heat_u8):
    h, w = image_bgr.shape[:2]
    out = image_bgr.copy()
    for i in range(NAIL_COUNT):
        weight = cv2.resize(heat_u8[:, :, i], (w, h)) / 255.0
        color = nail_color(i)
        for c in range(3):
            out[:, :, c] = (weight * color[c] + (1.0 - weight) * out[:, :, c]).astype(np.uint8)
    return out


def blend_roi(image_bgr, nails):
    out = image_bgr.copy()
    for i, n in enumerate(nails):
        if not n["valid"]:
            continue
        x, y, rw, rh = n["roi_on_src"]
        cv2.rectangle(out, (x, y), (x + rw, y + rh), nail_color(i), 2)
    return out


def blend_fit_roi(image_bgr, nails, nail_masks_u8):
    h, w = image_bgr.shape[:2]
    out = image_bgr.copy()
    for i, n in enumerate(nails):
        if len(n.get("anchors", [])) < 2:
            continue
        corners = get_nail_fit_roi_corners(nail_masks_u8[i], n["anchors"][0], n["anchors"][1], w, h)
        if corners is None:
            continue
        cv2.polylines(out, [corners.reshape(-1, 1, 2)], True, nail_color(i), 2)
    return out


def blend_mask(image_bgr, nails, combined_u8):
    out = _blend(image_bgr, combined_u8 / 255.0, (0, 255, 0))
    for n in nails:
        if n.get("mask_center"):
            cv2.circle(out, (int(n["mask_center"][0] + 0.5), int(n["mask_center"][1] + 0.5)), 4, (0, 0, 255), -1)
    return out


def blend_dir(image_bgr, nails, nail_masks_u8):
    h, w = image_bgr.shape[:2]
    dir_mask = np.zeros((h, w), np.uint8)
    for i, n in enumerate(nails):
        if not n.get("anchors") or n.get("yaw") is None:
            continue
        ax = sum(a[0] for a in n["anchors"]) / len(n["anchors"])
        ay = sum(a[1] for a in n["anchors"]) / len(n["anchors"])
        dir_mask = np.maximum(dir_mask, get_vec_debug_mask(nail_masks_u8[i], ax, ay, n["yaw"][0], n["yaw"][1]))
    out = _blend(image_bgr, dir_mask / 255.0, (255, 0, 255))
    for n in nails:
        for a in n.get("anchors", []):
            cv2.circle(out, (int(a[0] + 0.5), int(a[1] + 0.5)), 4, (0, 255, 0), -1)
    return out


def blend_patch(grid_rgb_u8, mask_grid):
    grid_bgr = grid_rgb_u8[:, :, ::-1].copy()
    return _blend(grid_bgr, mask_grid / 255.0, (0, 255, 0))


def blend_anchor(grid_rgb_u8, heat_grid):
    grid_bgr = grid_rgb_u8[:, :, ::-1].copy()
    return _blend(grid_bgr, np.amax(heat_grid, axis=-1) / 255.0, (255, 0, 0))
