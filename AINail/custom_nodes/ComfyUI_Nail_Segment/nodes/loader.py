import os

from ..core.onnx_backend import load_onnx
from .common import CATEGORY

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
POINT_MODEL = os.path.join(_MODELS_DIR, "point_model_weight_1input_modified.onnx")
PATCH_MODEL = os.path.join(_MODELS_DIR, "patch_model_weight_1input_modified.onnx")


class NailSegLoader:
    """Load the point + patch ONNX models bundled in this package's models/ folder (cached by path+mtime)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"device": (["auto", "cpu"],)}}

    RETURN_TYPES = ("NAIL_SEG_MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = 'Load the bundled point + patch ONNX models.'

    def load(self, device):
        return ({"point": load_onnx(POINT_MODEL, device), "patch": load_onnx(PATCH_MODEL, device)},)


NODE_CLASS_MAPPINGS = {"NailSegLoader": NailSegLoader}
NODE_DISPLAY_NAME_MAPPINGS = {"NailSegLoader": "Load Models (NailSegment)"}
