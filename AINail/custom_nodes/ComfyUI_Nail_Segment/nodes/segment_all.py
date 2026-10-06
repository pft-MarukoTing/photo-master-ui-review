import json

import numpy as np
import torch

from ..core import pipeline
from .common import CATEGORY, to_rgb_u8_list
from .decode import decode_all


class NailSegmentAll:
    """Stages 1-4 in one node, calling core directly (no per-stage tensor hand-off)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "model": ("NAIL_SEG_MODEL",),
                "inf_size": ("INT", {"default": 192, "min": 64, "max": 512, "step": 32}),
                "align_parameter": ("INT", {"default": 32, "min": 1, "max": 64}),
            }
        }

    RETURN_TYPES = ("MASK", "MASK", "STRING", "NAIL_POINT_RAW", "IMAGE", "NAIL_PATCH_RAW")
    RETURN_NAMES = ("nail_masks", "combined_mask", "nail_info", "point_raw", "patch_grid", "patch_raw")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Run all stages (Point Infer -> Prepare Patch Input -> Patch Infer -> Decode Patch Output) in one node. Also outputs the intermediate tensors needed by Visualize.'

    def run(self, image, model, inf_size, align_parameter):
        images = to_rgb_u8_list(image)
        point_raw = pipeline.point_infer(model["point"], images, inf_size, align_parameter)
        infos, grids = [], []
        for b, im in enumerate(images):
            info = pipeline.decode_point(point_raw, b)
            infos.append(info)
            grids.append(pipeline.build_patch_grid(im, info["nails"]))
        grids_u8 = np.stack(grids)
        patch_raw = pipeline.patch_infer(model["patch"], grids_u8)
        nail_masks, combined, nail_info = decode_all(json.dumps(infos), patch_raw)
        grid_t = torch.from_numpy(grids_u8.astype(np.float32) / 255.0)
        return (nail_masks, combined, nail_info, point_raw, grid_t, patch_raw)


NODE_CLASS_MAPPINGS = {"NailSegmentAll": NailSegmentAll}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegmentAll": "Segment All Stages (NailSegment)"}
