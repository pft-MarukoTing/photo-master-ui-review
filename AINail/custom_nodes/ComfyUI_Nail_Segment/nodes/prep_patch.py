import json

import numpy as np
import torch

from ..core import pipeline
from .common import CATEGORY, to_rgb_u8_list


class NailSegPrepPatch:
    """Between the two models: decode point output into per-nail center/radius/ROI and build the 320x64 patch grid."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",), "point_raw": ("NAIL_POINT_RAW",)}}

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("patch_grid", "nail_info")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Prepare the patch model input. First decodes the point model output (per-nail center, radius and ROI; nails with visible < 0.5 or size < 1 are skipped), then crops each ROI to 64x64 and stacks the 5 nails into a 320x64 grid. Also outputs nail_info (JSON) holding the decoded center / ROI.'

    def run(self, image, point_raw):
        images = to_rgb_u8_list(image)
        infos, grids = [], []
        for b, im in enumerate(images):
            info = pipeline.decode_point(point_raw, b)
            infos.append(info)
            grids.append(pipeline.build_patch_grid(im, info["nails"]))
        grid_t = torch.from_numpy(np.stack(grids).astype(np.float32) / 255.0)
        return (grid_t, json.dumps(infos))


NODE_CLASS_MAPPINGS = {"NailSegPrepPatch": NailSegPrepPatch}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegPrepPatch": "Prepare Patch Input (NailSegment)"}
