import json

import cv2
import numpy as np
import torch
from skimage.color import deltaE_ciede2000, rgb2lab

_WORK_SIZE = 256


def _resize(img, size, interp):
    return cv2.resize(img, (size, size), interpolation=interp)


def _diff_mask(crop, gen, delta_e_threshold, open_kernel, close_kernel, min_area):
    """crop/gen: [H, W, 3] float32 in [0, 1]. Returns nails [K, 256, 256] float32 {0, 1}, ordered left to right (K may be 0)."""
    crop_s = _resize(crop, _WORK_SIZE, cv2.INTER_AREA)
    gen_s = _resize(gen, _WORK_SIZE, cv2.INTER_AREA)

    de = deltaE_ciede2000(rgb2lab(crop_s), rgb2lab(gen_s))
    mask = (de > delta_e_threshold).astype(np.uint8)

    if open_kernel > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_kernel, open_kernel))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    if close_kernel > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_kernel, close_kernel))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    comps = [
        (i, centroids[i, 0])
        for i in range(1, n)
        if stats[i, cv2.CC_STAT_AREA] >= min_area
    ]
    comps.sort(key=lambda c: c[1])
    return np.stack([(labels == i) for i, _ in comps]).astype(np.float32) if comps \
        else np.zeros((0, _WORK_SIZE, _WORK_SIZE), dtype=np.float32)


def _to_size(masks, h, w):
    """[K, 256, 256] -> [K, h, w] binary."""
    if (h, w) == (_WORK_SIZE, _WORK_SIZE) or masks.shape[0] == 0:
        return np.zeros((0, h, w), dtype=np.float32) if masks.shape[0] == 0 else masks
    return np.stack([cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR) > 0.5 for m in masks]).astype(np.float32)


def _boxes_of(masks):
    """[K, H, W] binary -> JSON "[[x1,y1,x2,y2], ...]" (xyxy, x2/y2 exclusive), index-aligned with masks."""
    boxes = []
    for m in masks:
        ys, xs = np.where(m > 0.5)
        boxes.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1])
    return json.dumps(boxes)


class NailDiffMask:
    """Find where generated_images differ from crop_images and split the difference into per-nail masks.

    Pipeline per image: downscale both to 256x256 (INTER_AREA) -> RGB->Lab ->
    CIEDE2000 -> threshold -> morphological open (drop speckle / faint
    background brightness drift) then close (fill holes inside a nail) ->
    connected components -> drop components below min_area. There is no cap
    on the number of nails; ordering is left to right by centroid x.

    Why CIEDE2000 and not max RGB diff: it is perceptual, so the tiny global
    brightness drift the generator leaves on the background (typically
    dE < ~3) is separated from a real colour/polish change (typically dE > 8)
    by one threshold, while a max-RGB diff weights dark and bright regions
    and channels unevenly.

    Inputs are lists (INPUT_IS_LIST) of [B, H, W, C] tensors, like ResizeImage;
    every frame is processed independently and yields one entry in each
    output list, in input order. Masks are at the input resolution.

    Outputs (one entry per frame):
      diff_mask   : [1, H, W]  all nails combined
      nail_masks  : [K, H, W]  one mask per nail, left to right. When K == 0 this is a
                               single all-zero [1, H, W] placeholder (check nail_count).
      nail_count  : INT        K
      nail_boxes  : STRING     JSON "[[x1,y1,x2,y2], ...]" in input-image pixels (x2/y2 exclusive),
                               index-aligned with nail_masks; "[]" when K == 0. Same format as the
                               SAM3 nail_boxes that PasteBackMatched takes.
    """

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "crop_images": ("IMAGE",),
                "generated_images": ("IMAGE",),
                "delta_e_threshold": ("FLOAT", {"default": 1.5, "min": 0.0, "max": 100.0, "step": 0.1}),
                "open_kernel": ("INT", {"default": 3, "min": 0, "max": 51, "step": 1}),
                "close_kernel": ("INT", {"default": 5, "min": 0, "max": 51, "step": 1}),
                "min_area": ("INT", {"default": 20, "min": 0, "max": 65536, "step": 10}),
            }
        }

    RETURN_TYPES = ("MASK", "MASK", "INT", "STRING")
    RETURN_NAMES = ("diff_mask", "nail_masks", "nail_count", "nail_boxes")
    OUTPUT_IS_LIST = (True, True, True, True)
    FUNCTION = "compute"
    CATEGORY = "AINail/image"

    def compute(self, crop_images, generated_images, delta_e_threshold,
                open_kernel, close_kernel, min_area):
        thr, ok, ck, ma = delta_e_threshold[0], open_kernel[0], close_kernel[0], min_area[0]

        diff_masks, nail_masks, counts, boxes = [], [], [], []
        for crop, gen in zip(crop_images, generated_images):
            crop_np = crop[..., :3].clamp(0, 1).cpu().numpy().astype(np.float32)
            gen_np = gen[..., :3].clamp(0, 1).cpu().numpy().astype(np.float32)
            for k in range(min(crop_np.shape[0], gen_np.shape[0])):
                c, g = crop_np[k], gen_np[k]
                h, w = c.shape[:2]
                if g.shape[:2] != (h, w):
                    g = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA)

                nails = _to_size(_diff_mask(c, g, thr, ok, ck, ma), h, w)
                counts.append(int(nails.shape[0]))
                boxes.append(_boxes_of(nails))
                if nails.shape[0] == 0:
                    combined = np.zeros((1, h, w), dtype=np.float32)
                    nails = combined
                else:
                    combined = nails.max(axis=0, keepdims=True)
                diff_masks.append(torch.from_numpy(combined))
                nail_masks.append(torch.from_numpy(nails))

        return (diff_masks, nail_masks, counts, boxes)


NODE_CLASS_MAPPINGS = {
    "AINailNailDiffMask": NailDiffMask,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailNailDiffMask": "Nail Diff Mask (AINail)",
}
