from ..core import pipeline
from .common import CATEGORY, to_rgb_u8_list


class NailSegPointInfer:
    """Stage 1: point model predict only. Outputs raw heatmap / size / visible."""

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

    RETURN_TYPES = ("NAIL_POINT_RAW",)
    RETURN_NAMES = ("point_raw",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Run the point model only. Outputs raw heatmap / size / visible tensors; no post-processing.'

    def run(self, image, model, inf_size, align_parameter):
        raw = pipeline.point_infer(model["point"], to_rgb_u8_list(image), inf_size, align_parameter)
        return (raw,)


NODE_CLASS_MAPPINGS = {"NailSegPointInfer": NailSegPointInfer}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegPointInfer": "Point Infer (NailSegment)"}
