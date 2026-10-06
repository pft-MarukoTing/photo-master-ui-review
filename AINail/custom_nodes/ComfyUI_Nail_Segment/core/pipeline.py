"""Two-stage nail segmentation pipeline ported from TestRealImage() in
DeepTrainer_Ring/NailSegment/test_point.py, split so each stage can be a separate ComfyUI node.

Stages (point model):
  point_infer   - point model predict only
  decode_point  - heatmap/size/visible -> per-nail center, radius, ROI
"""
import math

import cv2
import numpy as np

from .geometry import get_center

NAIL_COUNT = 5


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
