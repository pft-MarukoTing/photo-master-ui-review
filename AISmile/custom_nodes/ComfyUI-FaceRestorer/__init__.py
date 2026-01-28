from .nodes import LoadOnnxFacePipelineNode, FaceDetailRestorerNode, FaceDetailRestorerwMetadataNode, \
                    FaceMaskRegionNode, PadToSquareNode, UndoPadNode, PadTo1MCustomNode, UndoPad1MCustomNode, \
                    FaceRegionCropNode, FaceRegionPasteBackNode

NODE_CLASS_MAPPINGS = {
    "LoadOnnxFacePipeline": LoadOnnxFacePipelineNode,
    "FaceDetailRestorer": FaceDetailRestorerNode,
    "FaceDetailRestorerwMetadata": FaceDetailRestorerwMetadataNode,
    "FaceMaskDetect": FaceMaskRegionNode,
    "PadToSquare": PadToSquareNode,
    "UndoPad": UndoPadNode,
    "PadTo1MCustom": PadTo1MCustomNode,
    "UndoPad1MCustom": UndoPad1MCustomNode,
    "FaceRegionCrop": FaceRegionCropNode,
    "FaceRegionPasteBack": FaceRegionPasteBackNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadOnnxFacePipeline": "Load Face Model Pipeline (ONNX)",
    "FaceDetailRestorer": "Face Detail Restorer",
    "FaceDetailRestorerwMetadata": "Face Detail Restorer w/ Metadata",
    "FaceMaskDetect": "Face Region Detector",
    "PadToSquare": "Pad Image to Square",
    "UndoPad": "Undo Image Padding",
    "PadTo1MCustom": "Pad Image to ~1M Pixels (Custom)",
    "UndoPad1MCustom": "Undo Pad to ~1M Pixels (Custom)",
    "FaceRegionCrop": "Crop Face Region from Image",
    "FaceRegionPasteBack": "Paste Face Region Back to Image"
}

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']