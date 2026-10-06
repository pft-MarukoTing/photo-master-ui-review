from ..core import pipeline
from .common import CATEGORY
import numpy as np


class NailSegPatchInfer:
    """Stage 3: patch model predict only. Outputs raw mask / direction / anchor-heat grids."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"patch_grid": ("IMAGE",), "model": ("NAIL_SEG_MODEL",)}}

    RETURN_TYPES = ("NAIL_PATCH_RAW",)
    RETURN_NAMES = ("patch_raw",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Run the patch model only. Outputs raw mask / direction / anchor-heat grids; no post-processing.'

    def run(self, patch_grid, model):
        grids = (patch_grid[..., :3].detach().cpu().float().clamp(0, 1) * 255.0).round().byte().numpy()
        return (pipeline.patch_infer(model["patch"], np.ascontiguousarray(grids)),)


NODE_CLASS_MAPPINGS = {"NailSegPatchInfer": NailSegPatchInfer}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegPatchInfer": "Patch Infer (NailSegment)"}
