"""ONNX Runtime model wrapper. Models were converted from the Keras models
DeepTrainer_Ring/Models/Nails/230324/{point,patch}_model_weight_1input_modified.h5;
layout is NHWC with dynamic batch/height/width."""
import os

import numpy as np

_CACHE = {}


class OnnxModel:
    """Exposes the same predict(x, verbose=0) -> list[np.ndarray] call the pipeline used with Keras."""

    def __init__(self, path, device="auto"):
        import onnxruntime as ort

        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if device == "auto" and "CUDAExecutionProvider" in available:
            providers.insert(0, "CUDAExecutionProvider")
        self.session = ort.InferenceSession(path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def predict(self, x, verbose=0):
        return self.session.run(None, {self.input_name: np.ascontiguousarray(x, dtype=np.float32)})


def load_onnx(path, device="auto"):
    """Cached by (path, mtime, device)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"[NailSeg] model file not found: {path}")
    key = (os.path.abspath(path), os.path.getmtime(path), device)
    if key not in _CACHE:
        _CACHE[key] = OnnxModel(path, device)
    return _CACHE[key]
