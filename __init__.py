from .nodes import LoadOnnxFacePipelineNode, FaceDetailRestorerNode

NODE_CLASS_MAPPINGS = {
    "LoadOnnxFacePipeline": LoadOnnxFacePipelineNode,
    "FaceDetailRestorer": FaceDetailRestorerNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadOnnxFacePipeline": "Load Face Model Pipeline (ONNX)",
    "FaceDetailRestorer": "Face Detail Restorer",
}

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']