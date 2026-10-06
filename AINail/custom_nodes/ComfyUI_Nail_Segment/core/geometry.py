"""Pure-numpy/cv2 geometry helpers ported from DeepTrainer_Ring/NailSegment/test_point.py."""
import math

import cv2
import numpy as np


def get_center(heatmap):
    """Intensity-weighted centroid (x, y) of a 2D heatmap; (0, 0) if it is all zero."""
    h, w = heatmap.shape[:2]
    total = np.sum(heatmap)
    if total == 0:
        return 0.0, 0.0
    x_coor = np.arange(w)[None, :]
    y_coor = np.arange(h)[:, None]
    x_avg = np.sum(np.multiply(x_coor, heatmap)) / total
    y_avg = np.sum(np.multiply(y_coor, heatmap)) / total
    return x_avg, y_avg


def get_vec_debug_mask(src_nail_mask_byte, anchor_center_x, anchor_center_y, vec_x, vec_y):
    width = src_nail_mask_byte.shape[1]
    height = src_nail_mask_byte.shape[0]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dilate_mask = cv2.dilate(src_nail_mask_byte, kernel, iterations=1)
    result_mask = np.zeros((height, width), np.uint8)
    vec_length = math.sqrt(vec_x * vec_x + vec_y * vec_y)
    center_x = anchor_center_x
    center_y = anchor_center_y
    if vec_length > 0.001:
        vec_x = vec_x / vec_length
        vec_y = vec_y / vec_length
        color = (255, 255, 255)
        cv2.circle(result_mask, (int(center_x + 0.5), int(center_y + 0.5)), 2, color, -1)
        for index in range(-20, 20):
            scan_x = int(center_x + vec_x * index + 0.5)
            scan_y = int(center_y + vec_y * index + 0.5)
            if scan_x < 0 or scan_x >= width:
                continue
            if scan_y < 0 or scan_y >= height:
                continue
            if dilate_mask[scan_y, scan_x] > 128:
                cv2.circle(result_mask, (scan_x, scan_y), 2, color, -1)
    return result_mask


def bresenham_line_points(x0, y0, x1, y1):
    points = []
    x0, y0, x1, y1 = int(x0 + 0.5), int(y0 + 0.5), int(x1 + 0.5), int(y1 + 0.5)
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy
    return points


def get_nail_fit_roi_corners(nail_mask_byte, p0, p1, img_w, img_h, padding=3):
    """Tight rotated box of a nail mask, oriented along p0->p1. Returns 4x2 int32 corners or None."""
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1.0:
        return None

    d = (dx / length, dy / length)
    n = (-dy / length, dx / length)

    cx = (p0[0] + p1[0]) / 2.0
    cy = (p0[1] + p1[1]) / 2.0
    scan_r = length * 2.0

    min_d = float('inf')
    max_d = float('-inf')
    min_n = float('inf')
    max_n = float('-inf')

    steps = int(scan_r * 2) + 1
    for i in range(steps):
        t = -scan_r + i * (scan_r * 2.0) / max(steps - 1, 1)
        ax = cx + t * d[0]
        ay = cy + t * d[1]

        bx0 = ax + scan_r * n[0]
        by0 = ay + scan_r * n[1]
        bx1 = ax - scan_r * n[0]
        by1 = ay - scan_r * n[1]

        for (px, py) in bresenham_line_points(bx0, by0, bx1, by1):
            if 0 <= px < img_w and 0 <= py < img_h and nail_mask_byte[py, px] > 128:
                pd = (px - cx) * d[0] + (py - cy) * d[1]
                pn = (px - cx) * n[0] + (py - cy) * n[1]
                if pd < min_d: min_d = pd
                if pd > max_d: max_d = pd
                if pn < min_n: min_n = pn
                if pn > max_n: max_n = pn

    if min_d == float('inf'):
        return None

    min_d -= padding
    max_d += padding
    min_n -= padding
    max_n += padding

    return np.array([
        [cx + min_d * d[0] + min_n * n[0], cy + min_d * d[1] + min_n * n[1]],
        [cx + max_d * d[0] + min_n * n[0], cy + max_d * d[1] + min_n * n[1]],
        [cx + max_d * d[0] + max_n * n[0], cy + max_d * d[1] + max_n * n[1]],
        [cx + min_d * d[0] + max_n * n[0], cy + min_d * d[1] + max_n * n[1]],
    ], dtype=np.int32)
