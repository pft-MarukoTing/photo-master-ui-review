import torch
import torch.nn.functional as F
import math
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
                #"mode": (["add", "replace", "eyes_replacement", "eyes_replacement_v2"],{"default": "eyes_replacement"}),
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
        mode = "eyes_replacement_v2" # ["add", "replace", "eyes_replacement", "eyes_replacement_v2"]
        forehead_strength = 0.5
        return self._restore_common(pipeline, image_origin, image_generated, sigma, detail_strength, forehead_strength, mask_shrink_size, mode=mode)

    def _restore_common(self, pipeline, image_origin, image_generated, sigma, detail_strength, forehead_strength, mask_shrink_size, mode):
        # init Restorer
        restorer = FaceDetailRestorer(
            pipeline=pipeline,
            sigma=sigma,
            detail_strength=detail_strength,
            forehead_strength=forehead_strength,
            mask_shrink_size=mask_shrink_size
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

class FaceDetailRestorerwMetadataNode:
    """
    Face Detail Restorer with metadata input
    """
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("FACE_PIPELINE",),
                "image_generated": ("IMAGE",),   # Generated (Smile)
                "face_metadata": ("FACE_METADATA",),
                "sigma": ("INT", {"default": 7, "min": 1, "max": 50}),
                "detail_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05}),
                "mask_shrink_size": ("INT", {"default": 0, "min": 0, "max": 100}),
                #"mode": (["add", "replace", "eyes_replacement", "eyes_replacement_v2"],{"default": "eyes_replacement_v2"}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "restore_face_with_metadata"
    CATEGORY = "FaceRestoration"

    def restore_face_with_metadata(self, pipeline, image_generated, face_metadata, sigma, detail_strength, mask_shrink_size, mode="eyes_replacement"):
        mode = "eyes_replacement_v2" # ["add", "replace", "eyes_replacement", "eyes_replacement_v2"]
        forehead_strength = 0.5
        return self._restore_w_metadata(pipeline, image_generated, sigma, detail_strength, forehead_strength, mask_shrink_size, face_metadata, mode=mode)

    def _restore_w_metadata(self, pipeline, image_generated, sigma, detail_strength, forehead_strength, mask_shrink_size, face_metadata, mode):
        # init Restorer
        restorer = FaceDetailRestorer(
            pipeline=pipeline,
            sigma=sigma,
            detail_strength=detail_strength,
            forehead_strength=forehead_strength,
            mask_shrink_size=mask_shrink_size,
        )

        output_tensors = []
        batch_count = image_generated.shape[0]
        for i in range(batch_count):
            # Format conversion: ComfyUI Tensor (RGB, 0-1) -> OpenCV Numpy (BGR, 0-255)
            
            # Broadcasting
            idx_gen = i if i < image_generated.shape[0] else 0
            
            img_b_np = (image_generated[idx_gen].cpu().numpy() * 255).astype(np.uint8)
            img_b_bgr = cv2.cvtColor(img_b_np, cv2.COLOR_RGB2BGR)

            metadata = face_metadata[i] if i < len(face_metadata) else {}

            try:
                # Call the unified restore function with mode and metadata
                result_bgr = restorer.restore_with_metadata(img_b_bgr, metadata, mode=mode)
            except Exception as e:
                print(f"Face Restoration Error: {e}")
                result_bgr = img_b_bgr

            # Format conversion: OpenCV Numpy (BGR, 0-255) -> Tensor (RGB, 0-1)
            result_rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
            result_tensor = torch.from_numpy(result_rgb.astype(np.float32) / 255.0)
            output_tensors.append(result_tensor)
        
        return (torch.stack(output_tensors),)

class FaceMaskRegionNode:
    """
    Simple face detection and segmentation mask generation.
    Returns a mask of the face region.
    """
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("FACE_PIPELINE",),
                "image": ("IMAGE",),
                "range_scale": ("FLOAT", {"default": 0.15, "min": 0.0, "max": 1.0, "step": 0.01}),
                "blur_scale": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "gain": ("FLOAT", {"default": 1.5, "min": 0.0, "max": 5.0, "step": 0.1}),
                "mask_type": (["rect", "circle", "seg"], {"default": "seg"}),
            },
            "optional": {
                "face_metadata": ("FACE_METADATA",),
                "face_rect_metadata": ("FACE_RECT_METADATA",),
            }
        }

    RETURN_TYPES = ("MASK", "FACE_METADATA")
    RETURN_NAMES = ("mask", "face_metadata")
    FUNCTION = "get_face_mask"
    CATEGORY = "FaceRestoration"

    def get_face_mask(self, pipeline, image, range_scale=0.15, blur_scale=3.0, gain=1.5, mask_type='seg', face_metadata=None, face_rect_metadata=None):
        # image is Tensor [B, H, W, C]
        # For this simple node, we don't need detailed restorer params
        restorer = FaceDetailRestorer(pipeline)
        
        output_masks = []
        output_metadata = []
        batch_count = image.shape[0]
        
        for i in range(batch_count):
            img_np = (image[i].cpu().numpy() * 255).astype(np.uint8)
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

            provided_rects = None
            provided_local_rects = None
            if face_metadata is not None and i < len(face_metadata):
                provided_rects = face_metadata[i].get("aligned_rects", None)
                provided_local_rects = face_metadata[i].get("face_rect_local", None)
            
            try:
                mask_single, metadata = restorer.pre_detect_all_faces(img_bgr, provided_rects=provided_rects, \
                                                                      range_scale=range_scale, blur_scale=blur_scale, gain=gain, \
                                                                      mask_type=mask_type, face_rect_metadata=face_rect_metadata, provided_local_rects=provided_local_rects)
                
                output_masks.append(torch.from_numpy(mask_single))
                output_metadata.append(metadata)
            except Exception as e:
                print(f"Face Mask Generation Error: {e}")
                # Return empty mask on error
                h, w = img_bgr.shape[:2]
                output_masks.append(torch.zeros((h, w), dtype=torch.float32))
                output_metadata.append({})

        return (torch.stack(output_masks), output_metadata)
    
class PadToSquareNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "PADDING_INFO")
    RETURN_NAMES = ("image", "padding_info")
    FUNCTION = "pad_image"
    CATEGORY = "FaceRestoration"

    def pad_image(self, image):
        try:
            padded_img, pads = self.pad_to_square(image)
            return (padded_img, [pads] * image.shape[0])
        except Exception as e:
            print(f"Pad to Square Error: {e}")
            return (image, [(0,0,0,0)] * image.shape[0])
    
    def pad_to_square(self, image):
        """
        Pads the batch of images to be square matching the largest dimension.
        Args:
            image: Tensor of shape (B, H, W, C)
        Returns:
            padded_img: Tensor of shape (B, Dim, Dim, C)
            pads: Tuple of (top, bottom, left, right)
        """
        B, H, W, C = image.shape
        
        if H == W:
            return image, (0, 0, 0, 0)
        
        dim = max(H, W)
        pad_h = dim - H
        pad_w = dim - W
        
        top = pad_h // 2
        bottom = pad_h - top
        left = pad_w // 2
        right = pad_w - left
        
        img_permuted = image.permute(0, 3, 1, 2)
        padded_img_permuted = F.pad(img_permuted, (left, right, top, bottom), value=0)
        
        padded_img = padded_img_permuted.permute(0, 2, 3, 1)
        
        return padded_img, (top, bottom, left, right)

class UndoPadNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "padding_info": ("PADDING_INFO",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "undo_pad_image"
    CATEGORY = "FaceRestoration"

    def undo_pad_image(self, image, padding_info):
        try:
            output_img = self.undo_pad_to_square(image, padding_info)
            return (output_img,)
        except Exception as e:
            print(f"Undo Pad Error: {e}")
            return (image,)

    def undo_pad_to_square(self, image, pads):
        """
        Removes the padding applied by pad_to_square.
        Args:
            image: The padded image tensor (B, Dim, Dim, C).
            pads: list of tuples (top, bottom, left, right).
        """
        top, bottom, left, right = pads[0]
        B, H, W, C = image.shape

        if H - top - bottom <= 0 or W - left - right <= 0:
            raise ValueError("Padding info error: crop area is invalid.")
        
        h_end = max(top + 1, H - bottom)
        w_end = max(left + 1, W - right)
        
        restored_img = image[:, top:h_end, left:w_end, :]
        
        return restored_img

class PadTo1MCustomNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "PADDING_INFO")
    RETURN_NAMES = ("image", "padding_info")
    FUNCTION = "pad_to_1M"
    CATEGORY = "FaceRestoration"

    def pad_to_1M(self, image):
        try:
            padded_images, pads_info = self.pad_to_1M_torch(image)
            return (padded_images, pads_info)
        except Exception as e:
            print(f"Pad to 1M Error: {e}")
            return (image, [(0,0,0,0)] * image.shape[0])
    
    def pad_to_1M_torch(self, image, target_pixel_count=1000000, align_factor=8):
        B, H, W, C = image.shape
        scale = math.sqrt(target_pixel_count / (W * H))
        
        target_w_raw = W * scale
        target_h_raw = H * scale

        target_w = int(round(target_w_raw / align_factor) * align_factor)
        target_h = int(round(target_h_raw / align_factor) * align_factor)
        
        target_w = max(align_factor, target_w)
        target_h = max(align_factor, target_h)
        
        ratio = min(target_w / W, target_h / H)
        
        new_unpad_w = int(round(W * ratio))
        new_unpad_h = int(round(H * ratio))

        img_permuted = image.permute(0, 3, 1, 2) 
        
        mode = 'area' if ratio < 1 else 'bilinear'
        align_corners = False if mode != 'area' else None
        
        resized_img = F.interpolate(
            img_permuted, 
            size=(new_unpad_h, new_unpad_w), 
            mode=mode, 
            align_corners=align_corners
        )

        # 4. Calculate padding
        dw = target_w - new_unpad_w
        dh = target_h - new_unpad_h
        
        pad_left = dw // 2
        pad_right = dw - pad_left
        pad_top = dh // 2
        pad_bottom = dh - pad_top
        
        padded_img = F.pad(resized_img, (pad_left, pad_right, pad_top, pad_bottom), mode='constant', value=0)
        output_images = padded_img.permute(0, 2, 3, 1)

        output_pads = []
        info_dict = {
            "original_shape": (H, W),
            "pad_top": pad_top,
            "pad_bottom": pad_bottom,
            "pad_left": pad_left,
            "pad_right": pad_right,
            "ratio": ratio,
            "final_shape": (target_h, target_w, C) 
        }
        
        for _ in range(B):
            output_pads.append(info_dict)

        return output_images, output_pads

class UndoPad1MCustomNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "padding_info": ("PADDING_INFO",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "undo_pad_1M"
    CATEGORY = "FaceRestoration"

    def undo_pad_1M(self, image, padding_info):
        try:
            restored_images = self.undo_pad_to_1M_torch(image, padding_info)
            return (restored_images,)
        except Exception as e:
            print(f"Undo Pad to 1M Error: {e}")
            return (image,)
    
    def undo_pad_to_1M_torch(self, processed_img, restore_info):
        """
        Undo padding and resize back to original size using Torch.
        Supports Batch processing.
        """
        
        B, H_curr, W_curr, C = processed_img.shape
        
        info = restore_info[0]
        
        top = info["pad_top"]
        bottom = info["pad_bottom"]
        left = info["pad_left"]
        right = info["pad_right"]
        h_orig, w_orig = info["original_shape"]
        
        crop_h_end = H_curr - bottom
        crop_w_end = W_curr - right
        
        if crop_h_end <= top or crop_w_end <= left:
            raise ValueError("Padding info error: crop area is invalid.")
        
        cropped_img = processed_img[:, top:crop_h_end, left:crop_w_end, :]
        cropped_permuted = cropped_img.permute(0, 3, 1, 2)
        
        restored_img = F.interpolate(
            cropped_permuted, 
            size=(h_orig, w_orig), 
            mode='bilinear', 
            align_corners=False
        )
        
        return restored_img.permute(0, 2, 3, 1)

class FaceRegionCropNode:
    """
    Crops the region covering all detected faces from the image.
    Returns the cropped image, crop coordinates, and metadata for the first detected face (relative to crop).
    """
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("FACE_PIPELINE",),
                "image": ("IMAGE",),
                "padding_factor": ("FLOAT", {"default": 0.45, "min": 0.0, "max": 1.0, "step": 0.05}),
                "bbox_padding_mode": (["shift", "pad"], {"default": "shift"}),
                "alignment_reinfine": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "face_rect_metadata": ("FACE_RECT_METADATA",),
                "all_bboxs": ("FACE_BBOXS_XYWH",),
            }
        }

    RETURN_TYPES = ("IMAGE", "CROP_INFO", "FACE_METADATA")
    RETURN_NAMES = ("crop_image", "crop_info", "face_metadata")
    FUNCTION = "crop_face_region"
    CATEGORY = "FaceRestoration"

    def crop_face_region(self, pipeline, image, padding_factor, bbox_padding_mode='shift', alignment_reinfine=False, face_rect_metadata=None, all_bboxs=None):
        restorer = FaceDetailRestorer(pipeline)
        
        output_images = []
        output_crop_infos = []
        output_metadata = []
        batch_count = image.shape[0]

        for i in range(batch_count):
            img_np = (image[i].cpu().numpy() * 255).astype(np.uint8)
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
            
            crop_bgr, crop_info, first_meta = restorer.get_all_faces_crop_region(img_bgr, padding_factor, bbox_padding_mode=bbox_padding_mode, alignment_reinfine=alignment_reinfine, face_rect_metadata=face_rect_metadata, all_bboxs=all_bboxs)
            
            img_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            img_tensor = torch.from_numpy(img_rgb.astype(np.float32) / 255.0)
            
            output_images.append(img_tensor)
            output_crop_infos.append(crop_info)
            output_metadata.append(first_meta)
        
        if len(output_images) > 1:
            try:
                final_tensor = torch.stack(output_images)
            except RuntimeError:
                print("Warning: Crop sizes inconsistent in batch. Resizing to match the first image.")
                target_h, target_w = output_images[0].shape[:2]
                resized_images = []
                for img in output_images:
                    # image is H,W,C
                    img_np = img.numpy()
                    img_resized = cv2.resize(img_np, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
                    resized_images.append(torch.from_numpy(img_resized))
                final_tensor = torch.stack(resized_images)
        else:
            final_tensor = torch.stack(output_images)

        return (final_tensor, output_crop_infos, output_metadata)

class FaceRegionPasteBackNode:
    """
    Pastes the processed crop back onto the original image using the coordinates from FaceRegionCropNode.
    """
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image_original": ("IMAGE",),      # The full canvas
                "image_processed_crop": ("IMAGE",), # The processed crop
                "crop_info": ("CROP_INFO",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "paste_back"
    CATEGORY = "FaceRestoration"

    def paste_back(self, image_original, image_processed_crop, crop_info):
        # We don't need a pipeline for pasting back, just the helper methods
        restorer = FaceDetailRestorer(None)
        
        output_images = []
        batch_count = image_original.shape[0]
        
        for i in range(batch_count):
            # 1. Prepare Full Image
            img_full_np = (image_original[i].cpu().numpy() * 255).astype(np.uint8)
            img_full_bgr = cv2.cvtColor(img_full_np, cv2.COLOR_RGB2BGR)
            
            # 2. Prepare Crop Image 
            # (Handle broadcasting if processed crop batch is smaller than original batch, usually 1:1)
            idx_crop = i if i < image_processed_crop.shape[0] else 0
            img_crop_np = (image_processed_crop[idx_crop].cpu().numpy() * 255).astype(np.uint8)
            img_crop_bgr = cv2.cvtColor(img_crop_np, cv2.COLOR_RGB2BGR)
            
            # 3. Get Info
            info = crop_info[i] if i < len(crop_info) else crop_info[-1]
            
            # 4. Paste
            result_bgr = restorer.paste_back_crop_region(img_full_bgr, img_crop_bgr, info)
            
            # 5. Convert back
            result_rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
            result_tensor = torch.from_numpy(result_rgb.astype(np.float32) / 255.0)
            
            output_images.append(result_tensor)

        return (torch.stack(output_images),)

class SelectBBoxNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "face_bboxs": ("FACE_BBOXS", ),
                "random_select": ("BOOLEAN", {"default": False}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "format_mode": (["xywh", "xyxy"], {"default": "xyxy"}),
            },
        }

    RETURN_TYPES = ("INT", "INT", "INT", "INT", "FACE_BBOXS_XYWH")
    RETURN_NAMES = ("x", "y", "w", "h", "all_bboxs")
    FUNCTION = "execute"
    CATEGORY = "FaceRestoration/tools"

    def execute(self, image, face_bboxs, random_select, seed, format_mode):
        b, h, w, c = image.shape

        if face_bboxs is None:
            return (-1, -1, -1, -1, [])
        
        if not isinstance(face_bboxs, np.ndarray):
            face_bboxs = np.array(face_bboxs)
            
        if face_bboxs.size == 0:
            print("Warning: Empty bboxes received.")
            return (-1, -1, -1, -1, [])

        if random_select:
            seed = seed & 0xffffffff
            np.random.seed(seed)
            idx = np.random.randint(0, len(face_bboxs))
        else:
            if format_mode == "xywh":
                w_vals = face_bboxs[:, 2]
                h_vals = face_bboxs[:, 3]
                cx = face_bboxs[:, 0] + w_vals / 2
                cy = face_bboxs[:, 1] + h_vals / 2
            else: # xyxy
                w_vals = face_bboxs[:, 2] - face_bboxs[:, 0]
                h_vals = face_bboxs[:, 3] - face_bboxs[:, 1]
                cx = (face_bboxs[:, 0] + face_bboxs[:, 2]) / 2
                cy = (face_bboxs[:, 1] + face_bboxs[:, 3]) / 2
                
            areas = w_vals * h_vals
            img_cx = w / 2
            img_cy = h / 2
            distances = np.sqrt((cx - img_cx)**2 + (cy - img_cy)**2)
            max_dist = np.sqrt(img_cx**2 + img_cy**2) + 1e-6
            k = 5.0 
            scores = areas * np.exp(-k * (distances / max_dist))
            idx = np.argmax(scores)
        
        first_bbox = face_bboxs[idx]
        
        val_1, val_2, val_3, val_4 = first_bbox[:4]
        
        x, y, w, h = 0, 0, 0, 0

        if format_mode == "xywh":
            x, y, w, h = int(val_1), int(val_2), int(val_3), int(val_4)
            
        else:
            x1, y1, x2, y2 = int(val_1), int(val_2), int(val_3), int(val_4)
            x = x1
            y = y1
            w = x2 - x1
            h = y2 - y1

        # Convert all bboxes to xywh format
        all_bboxs = []
        for bbox in face_bboxs:
            bv1, bv2, bv3, bv4 = bbox[:4]
            if format_mode == "xywh":
                all_bboxs.append({"x": int(bv1), "y": int(bv2), "w": int(bv3), "h": int(bv4)})
            else:
                all_bboxs.append({"x": int(bv1), "y": int(bv2), "w": int(bv3 - bv1), "h": int(bv4 - bv2)})

        print(f"Selected BBox: x={x}, y={y}, w={w}, h={h}")
        print(f"All BBoxes ({len(all_bboxs)}): {all_bboxs}")
        return (x, y, w, h, all_bboxs)

class FaceRectMetadataNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "x": ("INT", {"default": 0}),
                "y": ("INT", {"default": 0}),
                "w": ("INT", {"default": 512}),
                "h": ("INT", {"default": 512}),
            },
        }

    RETURN_TYPES = ("FACE_RECT_METADATA", )
    RETURN_NAMES = ("face_rect_metadata", )
    FUNCTION = "execute"
    CATEGORY = "FaceRestoration/tools"

    def execute(self, enabled, x, y, w, h):
        # all values is zero or negative, treat as disabled
        if w <= 0 or h <= 0:
            enabled = False

        metadata = {
            "face_rect_flag": enabled,
            "face_rect": [int(x), int(y), int(w), int(h)]
        }
        
        # Debug print
        status = "Active" if enabled else "Inactive"
        print(f"Generated Metadata [{status}]: {metadata}")
        
        return (metadata, )

class FaceRectMetadataSwitchNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "face_rect_provided": ("FACE_RECT_METADATA",),
                "face_rect_detected": ("FACE_RECT_METADATA",),
            },
        }

    RETURN_TYPES = ("FACE_RECT_METADATA", "BOOLEAN")
    RETURN_NAMES = ("face_rect_metadata", "is_face_detected",)
    FUNCTION = "execute"
    CATEGORY = "FaceRestoration/tools"

    def execute(self, face_rect_provided, face_rect_detected):
        metadata = face_rect_provided if face_rect_provided["face_rect_flag"] else face_rect_detected
        is_face_detected = metadata["face_rect_flag"]
        return (metadata, is_face_detected)