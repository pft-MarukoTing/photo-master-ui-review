import json

import numpy as np
import torch

from ..core import pipeline
from .common import CATEGORY


def decode_all(nail_info, patch_raw):
    infos = json.loads(nail_info)
    per_nail, combined = [], []
    for b, info in enumerate(infos):
        nm, cm = pipeline.decode_patch(info, patch_raw, b)
        per_nail.append(nm)
        combined.append(cm)
    nail_masks = torch.from_numpy(np.concatenate(per_nail, axis=0).astype(np.float32) / 255.0)
    combined_t = torch.from_numpy(np.stack(combined).astype(np.float32) / 255.0)
    return nail_masks, combined_t, json.dumps(infos)


class NailSegDecode:
    """Stage 4: patch outputs -> per-nail masks on the source image, yaw, anchors."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"nail_info": ("STRING", {"forceInput": True}), "patch_raw": ("NAIL_PATCH_RAW",)}}

    RETURN_TYPES = ("MASK", "MASK", "STRING")
    RETURN_NAMES = ("nail_masks", "combined_mask", "nail_info")
    OUTPUT_TOOLTIPS = ("[B*5,H,W], image-major then nail index 0..4", "[B,H,W]", "")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Decode the patch model output: paste per-nail masks back onto the source image and add yaw (mask-weighted mean of the direction map) and anchors (centroids of the heat channels) to nail_info.'

    def run(self, nail_info, patch_raw):
        return decode_all(nail_info, patch_raw)


NODE_CLASS_MAPPINGS = {"NailSegDecode": NailSegDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegDecode": "Decode Patch Output (NailSegment)"}
