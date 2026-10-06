"""Two-stage nail segmentation pipeline ported from TestRealImage() in
DeepTrainer_Ring/NailSegment/test_point.py, split so each stage can be a separate ComfyUI node.

Stages:
  point_infer   - point model predict only
  decode_point  - heatmap/size/visible -> per-nail center, radius, ROI
  build_patch_grid - crop ROIs into one 320x64 grid
  patch_infer   - patch model predict only
  decode_patch  - grid outputs -> per-nail mask on the source image, yaw, anchors
"""
import math

import cv2
import numpy as np

from .geometry import get_center

NAIL_COUNT = 5
PATCH_SIZE = 64


def segment_size(width, height, inf_size, align):
    ratio = math.sqrt((inf_size * inf_size) / (height * width))
    return (int(width * ratio) // align) * align, (int(height * ratio) // align) * align


# ---------------------------------------------------------------- point model
def point_infer(model, images_rgb, inf_size=192, align=32):
    """images_rgb: list of HxWx3 uint8 RGB, all the same size. Returns raw model outputs (batched)."""
    h, w = images_rgb[0].shape[:2]
    seg_w, seg_h = segment_size(w, h, inf_size, align)
    x = np.stack([cv2.resize(im, (seg_w, seg_h), interpolation=cv2.INTER_LINEAR) for im in images_rgb])
    x = x.astype(np.float32) / 255.0
    y = model.predict(x, verbose=0)
    return {
        "heat": np.asarray(y[0]),      # [B, sh, sw, 5]
        "size": np.asarray(y[1]),      # [B, 5] (global-average-pooled) or [B, h, w, 5]
        "visible": np.asarray(y[2]),   # same layout as size
        "seg_w": seg_w, "seg_h": seg_h, "src_w": w, "src_h": h,
    }


def decode_point(raw, b):
    """Per-nail center / radius / ROI for batch item b. Returns the per-image info dict."""
    w, h = raw["src_w"], raw["src_h"]
    seg_w, seg_h = raw["seg_w"], raw["seg_h"]
    heat_u8 = (raw["heat"][b] * 255.0).astype(np.uint8)
    size_map = raw["size"][b]
    vis_map = raw["visible"][b]

    nails = []
    for i in range(NAIL_COUNT):
        if size_map.ndim == 1:
            size, vis = size_map[i], vis_map[i]
        else:
            size, vis = np.mean(size_map[:, :, i]), np.mean(vis_map[:, :, i])
        size = np.float32(np.float32(size) * max(h, w) / 5)
        vis = np.float32(vis)

        cx, cy = get_center(heat_u8[:, :, i].astype(np.float32))
        center = (np.float32((cx + 0.5) * 2.0 * w / seg_w - 0.5),
                  np.float32((cy + 0.5) * 2.0 * h / seg_h - 0.5))

        nail = {
            "index": i, "valid": False, "visible": float(vis), "size": float(size),
            "center": [float(center[0]), float(center[1])],
            "patch_size": 0, "roi_on_src": [0, 0, 0, 0], "roi_on_patch": [0, 0, 0, 0],
        }
        if vis >= 0.5 and size >= 1:
            radius = size
            patch_size = int(radius * 2.0 + 0.5)
            start_x = int(center[0] - radius + 0.5)
            start_y = int(center[1] - radius + 0.5)
            end_x = start_x + patch_size
            end_y = start_y + patch_size
            cend_x = int(np.clip(end_x, 0, w))
            cend_y = int(np.clip(end_y, 0, h))
            cstart_x = int(np.clip(start_x, 0, cend_x))
            cstart_y = int(np.clip(start_y, 0, cend_y))
            nail.update(
                valid=True, patch_size=patch_size,
                roi_on_src=[cstart_x, cstart_y, cend_x - cstart_x, cend_y - cstart_y],
                roi_on_patch=[cstart_x - start_x, cstart_y - start_y, cend_x - cstart_x, cend_y - cstart_y],
            )
        nails.append(nail)

    return {"width": w, "height": h, "seg_w": seg_w, "seg_h": seg_h, "nails": nails}


# ---------------------------------------------------------------- patch grid
def build_patch_grid(image_rgb, nails):
    """Crop each valid nail ROI, resize to 64x64 and stack vertically -> [320, 64, 3] uint8 (RGB)."""
    grid = np.zeros((PATCH_SIZE * NAIL_COUNT, PATCH_SIZE, 3), np.uint8)
    for i, nail in enumerate(nails):
        if not nail["valid"]:
            continue
        ps = nail["patch_size"]
        rs, rp = nail["roi_on_src"], nail["roi_on_patch"]
        patch = np.zeros((ps, ps, 3), np.uint8)
        patch[rp[1]:rp[1] + rp[3], rp[0]:rp[0] + rp[2], :] = image_rgb[rs[1]:rs[1] + rs[3], rs[0]:rs[0] + rs[2], :]
        grid[i * PATCH_SIZE:(i + 1) * PATCH_SIZE] = cv2.resize(patch, (PATCH_SIZE, PATCH_SIZE), interpolation=cv2.INTER_LINEAR)
    return grid


# ---------------------------------------------------------------- patch model
def patch_infer(model, grids_u8):
    """grids_u8: [B, 320, 64, 3] uint8 RGB. Returns raw model outputs (batched)."""
    x = grids_u8.astype(np.float32) / 255.0
    y = model.predict(x, verbose=0)
    mask = np.asarray(y[0])[..., 0] * 255.0                       # [B, 320, 64]
    vec = np.clip(np.asarray(y[1])[..., :2], -1, 1)               # [B, 320, P, 2]
    heat = (np.asarray(y[2]) if len(y) == 3 else np.asarray(y[0])) * 255.0  # [B, 320, 64, C]
    return {"mask": mask, "vec": vec, "heat": heat}


def decode_patch(info, raw, b):
    """Fill per-nail mask / yaw / anchors for batch item b.

    Returns (nail_masks [5,H,W] uint8, combined_mask [H,W] uint8) and updates `info` in place.
    """
    w, h = info["width"], info["height"]
    seg_w, seg_h = info["seg_w"], info["seg_h"]
    mask_grid = raw["mask"][b].astype(np.uint8)
    vec_grid = raw["vec"][b]
    heat_grid = raw["heat"][b]
    inf_patch = vec_grid.shape[1]
    heat_count = heat_grid.shape[2]

    nail_masks = np.zeros((NAIL_COUNT, h, w), np.uint8)
    resized_combined = np.zeros((seg_h, seg_w), np.uint8)

    for i, nail in enumerate(info["nails"]):
        nail["mask_center"] = None
        nail["yaw"] = None
        nail["anchors"] = []
        if not nail["valid"]:
            continue

        ps = nail["patch_size"]
        rs, rp = nail["roi_on_src"], nail["roi_on_patch"]

        mask_patch = mask_grid[i * PATCH_SIZE:(i + 1) * PATCH_SIZE, :]
        mask_patch_inf = cv2.resize(mask_patch, (inf_patch, inf_patch), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        mask_patch_src = cv2.resize(mask_patch, (ps, ps), interpolation=cv2.INTER_LINEAR)

        temp = nail_masks[i]
        temp[rs[1]:rs[1] + rs[3], rs[0]:rs[0] + rs[2]] = mask_patch_src[rp[1]:rp[1] + rp[3], rp[0]:rp[0] + rp[2]]
        resized_temp = cv2.resize(temp, (seg_w, seg_h), interpolation=cv2.INTER_LINEAR)
        scx, scy = get_center(resized_temp)
        nail["mask_center"] = [float((scx + 0.5) / seg_w * w - 0.5), float((scy + 0.5) / seg_h * h - 0.5)]
        resized_combined = np.maximum(resized_combined, resized_temp)

        vec_patch = vec_grid[i * inf_patch:(i + 1) * inf_patch]
        denom = max(np.sum(mask_patch_inf), 0.0001)
        nail["yaw"] = [float(np.sum(vec_patch[..., 0] * mask_patch_inf) / denom),
                       float(np.sum(vec_patch[..., 1] * mask_patch_inf) / denom)]

        heat_patch = heat_grid[i * PATCH_SIZE:(i + 1) * PATCH_SIZE]
        for k in range(heat_count):
            ax, ay = get_center(heat_patch[:, :, k])
            ax_n = (ax + 0.5) / PATCH_SIZE
            ay_n = (ay + 0.5) / PATCH_SIZE
            nail["anchors"].append([float((ax_n * ps - 0.5) - rp[0] + rs[0]),
                                    float((ay_n * ps - 0.5) - rp[1] + rs[1])])

    combined = cv2.resize(resized_combined, (w, h), interpolation=cv2.INTER_LINEAR)
    return nail_masks, combined
