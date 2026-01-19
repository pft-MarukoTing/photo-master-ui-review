import torch
import numpy as np
import cv2
import os

from .core.post_blend import FaceDetailRestorer
from .core.face_model_pipe import OnnxFacePipeline
from .core.face_align.align import set_lm3d

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
def get_model_path(filename):
    return os.path.join(CURRENT_DIR, filename)

class LoadOnnxFacePipelineNode:
    """
    Load ONNX Face Pipeline once
    """
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
            }
        }

    RETURN_TYPES = ("FACE_PIPELINE",)
    RETURN_NAMES = ("pipeline",)
    FUNCTION = "load_pipeline"
    CATEGORY = "FaceRestoration"

    def load_pipeline(self,):
        align_model_path = get_model_path("core/LVOcclusionONNX/alignment_0407_fp16.onnx")
        seg_model_path = get_model_path("core/LVOcclusionONNX/makeup_region_detect_20250331.onnx")
        detect_model_path = get_model_path("core/LVOcclusionONNX/facert_20230307.onnx")
        lm3d_path = get_model_path("core/face_align/similarity_Lm3D_all.mat")

        # Check if models exist
        missing_files = []
        if not os.path.exists(align_model_path):
            missing_files.append(f"Align Model: {align_model_path}")
        if not os.path.exists(seg_model_path):
            missing_files.append(f"Seg Model: {seg_model_path}")
        if not os.path.exists(detect_model_path):
            missing_files.append(f"Detect Model: {detect_model_path}")
            
        if missing_files:
            raise FileNotFoundError(f"Missing model files:\n" + "\n".join(missing_files))
        
        if os.path.exists(lm3d_path):
            set_lm3d(lm3d_path)
        else:
            print(f"Warning: LM3D path not found: {lm3d_path}")
        
        # init Pipeline
        pipeline = OnnxFacePipeline(
            align_model_path=align_model_path,
            seg_model_path=seg_model_path,
            detect_model_path=detect_model_path
        )
        return (pipeline,)

class FaceDetailRestorerNode:
    """
    Base class for Face Detail Restorer Nodes to avoid code duplication
    """
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("FACE_PIPELINE",),
                "image_origin": ("IMAGE",),      # Reference
                "image_generated": ("IMAGE",),   # Generated (Smile)
                #"mode": (["add", "replace", "eyes_replacement"],{"default": "eyes_replacement"}),
                "sigma": ("INT", {"default": 7, "min": 1, "max": 50}),
                "detail_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05}),
                #"forehead_strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 2.0, "step": 0.05}),
                "mask_shrink_size": ("INT", {"default": 0, "min": 0, "max": 100}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "restore_face"
    CATEGORY = "FaceRestoration"

    def restore_face(self, pipeline, image_origin, image_generated, sigma, detail_strength, mask_shrink_size):
        mode = "eyes_replacement" # ["add", "replace", "eyes_replacement"]
        forehead_strength = 0.5
        return self._restore_common(pipeline, image_origin, image_generated, sigma, detail_strength, forehead_strength, mask_shrink_size, mode=mode)

    def _restore_common(self, pipeline, image_origin, image_generated, sigma, detail_strength, forehead_strength, mask_shrink_size, mode):
        # init Restorer
        restorer = FaceDetailRestorer(
            pipeline=pipeline,
            sigma=sigma,
            detail_strength=detail_strength,
            forehead_strength=forehead_strength,
            mask_shrink_size=mask_shrink_size,
        )

        output_tensors = []
        batch_count = image_origin.shape[0]
        for i in range(batch_count):
            # Format conversion: ComfyUI Tensor (RGB, 0-1) -> OpenCV Numpy (BGR, 0-255)
            
            # Broadcasting
            idx_gen = i if i < image_generated.shape[0] else 0
            
            img_a_np = (image_origin[i].cpu().numpy() * 255).astype(np.uint8)
            img_a_bgr = cv2.cvtColor(img_a_np, cv2.COLOR_RGB2BGR)
            
            img_b_np = (image_generated[idx_gen].cpu().numpy() * 255).astype(np.uint8)
            img_b_bgr = cv2.cvtColor(img_b_np, cv2.COLOR_RGB2BGR)

            try:
                # Call the unified restore function with mode
                result_bgr = restorer.restore_core(img_a_bgr, img_b_bgr, mode=mode)
            except Exception as e:
                print(f"Face Restoration Error: {e}")
                result_bgr = img_b_bgr

            # Format conversion: OpenCV Numpy (BGR, 0-255) -> Tensor (RGB, 0-1)
            result_rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
            result_tensor = torch.from_numpy(result_rgb.astype(np.float32) / 255.0)
            output_tensors.append(result_tensor)
        
        return (torch.stack(output_tensors),)
