import json

import numpy as np
import torch

from ..core import visualize as V
from ..core.pipeline import NAIL_COUNT
from .common import CATEGORY, bgr_u8_to_image, to_rgb_u8_list

MODES = ["blend_mask", "blend_dir", "blend_heat", "blend_roi", "blend_fit_roi", "blend_patch", "blend_anchor"]

_NEEDS = {
    "blend_mask": ("combined_mask",),
    "blend_dir": ("nail_masks",),
    "blend_heat": ("point_raw",),
    "blend_roi": (),
    "blend_fit_roi": ("nail_masks",),
    "blend_patch": ("patch_grid", "patch_raw"),
    "blend_anchor": ("patch_grid", "patch_raw"),
}


def _mask_u8(t):
    return (t.detach().cpu().float().clamp(0, 1) * 255.0).round().byte().numpy()


class NailSegViz:
    """Draw one of the original blend_* debug images. Only the inputs required by the chosen mode need to be connected."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "nail_info": ("STRING", {"forceInput": True}),
                "mode": (MODES,),
            },
            "optional": {
                "nail_masks": ("MASK",),
                "combined_mask": ("MASK",),
                "point_raw": ("NAIL_POINT_RAW",),
                "patch_grid": ("IMAGE",),
                "patch_raw": ("NAIL_PATCH_RAW",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("vis",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Draw one of the debug blend images. Only the inputs required by the chosen mode need to be connected.'

    def run(self, image, nail_info, mode, nail_masks=None, combined_mask=None,
            point_raw=None, patch_grid=None, patch_raw=None):
        given = {"nail_masks": nail_masks, "combined_mask": combined_mask, "point_raw": point_raw,
                 "patch_grid": patch_grid, "patch_raw": patch_raw}
        missing = [k for k in _NEEDS[mode] if given[k] is None]
        if missing:
            raise ValueError(f"[NailSegViz] mode '{mode}' requires input(s): {', '.join(missing)}")

        infos = json.loads(nail_info)
        images_rgb = to_rgb_u8_list(image)
        outs = []
        for b, info in enumerate(infos):
            nails = info["nails"]
            bgr = np.ascontiguousarray(images_rgb[b][:, :, ::-1])

            if mode == "blend_mask":
                out = V.blend_mask(bgr, nails, _mask_u8(combined_mask[b]))
            elif mode == "blend_dir":
                out = V.blend_dir(bgr, nails, _mask_u8(nail_masks[b * NAIL_COUNT:(b + 1) * NAIL_COUNT]))
            elif mode == "blend_heat":
                heat_u8 = (point_raw["heat"][b] * 255.0).astype(np.uint8)
                out = V.blend_heat(bgr, heat_u8)
            elif mode == "blend_roi":
                out = V.blend_roi(bgr, nails)
            elif mode == "blend_fit_roi":
                out = V.blend_fit_roi(bgr, nails, _mask_u8(nail_masks[b * NAIL_COUNT:(b + 1) * NAIL_COUNT]))
            else:
                grid = (patch_grid[b, ..., :3].detach().cpu().float().clamp(0, 1) * 255.0).round().byte().numpy()
                if mode == "blend_patch":
                    out = V.blend_patch(grid, patch_raw["mask"][b])
                else:
                    out = V.blend_anchor(grid, patch_raw["heat"][b])
            outs.append(out)
        return (bgr_u8_to_image(outs),)


NODE_CLASS_MAPPINGS = {"NailSegViz": NailSegViz}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegViz": "Visualize (NailSegment)"}
