from .nodes import LoadOnnxFacePipelineNode, FaceDetailRestorerNode, FaceDetailRestorerwMetadataNode, \
                    FaceMaskRegionNode, PadToSquareNode, UndoPadNode

NODE_CLASS_MAPPINGS = {
    "LoadOnnxFacePipeline": LoadOnnxFacePipelineNode,
    "FaceDetailRestorer": FaceDetailRestorerNode,
    "FaceDetailRestorerwMetadata": FaceDetailRestorerwMetadataNode,
    "FaceMaskDetect": FaceMaskRegionNode,
    "PadToSquare": PadToSquareNode,
    "UndoPad": UndoPadNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadOnnxFacePipeline": "Load Face Model Pipeline (ONNX)",
    "FaceDetailRestorer": "Face Detail Restorer",
    "FaceDetailRestorerwMetadata": "Face Detail Restorer w/ Metadata",
    "FaceMaskDetect": "Face Region Detector",
    "PadToSquare": "Pad Image to Square",
    "UndoPad": "Undo Image Padding"
}

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']