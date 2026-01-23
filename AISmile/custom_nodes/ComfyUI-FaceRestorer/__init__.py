from .nodes import LoadOnnxFacePipelineNode, FaceDetailRestorerNode, FaceDetailRestorerwMetadataNode, FaceMaskRegionNode

NODE_CLASS_MAPPINGS = {
    "LoadOnnxFacePipeline": LoadOnnxFacePipelineNode,
    "FaceDetailRestorer": FaceDetailRestorerNode,
    "FaceDetailRestorerwMetadata": FaceDetailRestorerwMetadataNode,
    "FaceMaskDetect": FaceMaskRegionNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadOnnxFacePipeline": "Load Face Model Pipeline (ONNX)",
    "FaceDetailRestorer": "Face Detail Restorer",
    "FaceDetailRestorerwMetadata": "Face Detail Restorer w/ Metadata",
    "FaceMaskDetect": "Face Region Detector"
}

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']