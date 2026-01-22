import os
import numpy as np
import cv2
import math
import onnxruntime as ort
from .align_util import align_face, compute_rect_from_shape, iou
from .face_align.align import mkl_to_afw, Preprocess

DEBUG = False

class ONNXModelWrapper:
    def __init__(self, onnx_path, dtype=None, providers=None):
        if providers is None:
            providers = ["CPUExecutionProvider"]

        self.session = ort.InferenceSession(
            onnx_path,
            providers=providers
        )
        self.input_name = self.session.get_inputs()[0].name
        self.dtype = dtype

    def predict(self, x, verbose=0):
        if self.dtype == None and x.dtype != np.float32:
            x = x.astype(np.float32)
        else:
            x = x.astype(self.dtype)

        outputs = self.session.run(None, {self.input_name: x})
        return outputs

class OnnxFacePipeline:
    def __init__(self, align_model_path, seg_model_path, detect_model_path=None):
        """
        Loads all ONNX models (Detection, Alignment, Segmentation).
        """
        if detect_model_path and os.path.exists(detect_model_path):
            self.detect_model = ONNXModelWrapper(detect_model_path)
            if DEBUG:
                print(f"  - Detect Model loaded: {detect_model_path}")
        else:
            self.detect_model = None
            if DEBUG:
                print("  - Detect Model NOT loaded (Assume Pre-aligned inputs)")
        self.align_model = ONNXModelWrapper(align_model_path, dtype=np.float16)
        self.seg_model = ONNXModelWrapper(seg_model_path)
        
        # Detection Constants
        self.GROUP_THRESHOLD = 0.25
        self.SCORE_THRESHOLD = 0.7
        self.MAX_IMAGE_SIDE = 1024
        self.align_rounds = 1

    # ==========================================
    # 2. Alignment Logic (84 Points)
    # ==========================================
    def align(self, src_img, rect):
        """
        Runs recursive alignment to get 84 landmarks.
        rect: [x, y, w, h]
        """
        curr_rect = rect
        points_84 = None
        
        for i in range(self.align_rounds):
            points_84 = align_face(self.align_model, curr_rect, src_img, (256, 256))
            curr_rect = compute_rect_from_shape(points_84)
        
        return points_84

    # ==========================================
    # 3. Segmentation Logic (With Inverse Warp)
    # ==========================================
    def segment(self, src_img, landmarks_84):
        """
        1. Preprocess (Align/Warp to 256x256)
        2. Predict Mask
        3. Inverse Warp Mask back to Original Size
        """
        h_orig, w_orig = src_img.shape[:2]
        
        landmarks_afw = mkl_to_afw(landmarks_84)
        trans_img, _, _, _, trans_mat = Preprocess(src_img, None, None, landmarks_afw, 256)
        # 2. Predict
        rgb_img = cv2.cvtColor(trans_img, cv2.COLOR_BGR2RGB)
        x = np.expand_dims(rgb_img, axis=0) / 255.
        output = self.seg_model.predict(x, verbose=0)
        # save for debug
        mask_aligned = output[0][0, :, :, 0]
        
        # 3. Inverse Warp
        mask_orig = cv2.warpAffine(
            mask_aligned, 
            trans_mat, 
            (w_orig, h_orig), 
            flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR
        )
        
        # 4. Clean up (Threshold & Blur)
        mask_orig[mask_orig < 0.1] = 0.0
        mask_orig = np.clip(mask_orig, 0.0, 1.0)
        
        return cv2.merge([mask_orig, mask_orig, mask_orig])
    
    # ==========================================
    # 1. Detection Logic
    # ==========================================
    def detect(self, src_img, bypass=False):
        """
        Returns the best face bounding box [x, y, w, h].
        """
        # Resize logic for detection
        height, width = src_img.shape[:2]

        if self.detect_model is None or bypass:
            if DEBUG:
                print("[KerasPipeline] Detect bypassed. Using full image bounds.")
            return [0, 0, width, height], None

        scale = 1
        if height > self.MAX_IMAGE_SIDE or width > self.MAX_IMAGE_SIDE:
            scale = self.MAX_IMAGE_SIDE / max(height, width)

        target_shape = (int(width * scale), int(height * scale))
        img = cv2.resize(src_img, target_shape, interpolation=cv2.INTER_AREA)

        # Run internal detect logic
        best_rect, full_detect = self._detect_face_internal(self.detect_model, img)
        if DEBUG:
            print(f"Best detected rect (scaled): {best_rect}")
        
        if best_rect is None:
            return None, None

        # Scale back to original size
        x, y, w, h = best_rect
        def _rescale_rect(rect, scale):
            rx, ry, rw, rh = rect
            return [rx / scale, ry / scale, rw / scale, rh / scale]
        
        for i in range(len(full_detect)):
            fx, fy, fw, fh = full_detect[i][0]
            full_detect[i] = _rescale_rect((fx, fy, fw, fh), scale)
        
        return [x / scale, y / scale, w / scale, h / scale], full_detect

    def _detect_face_internal(self, model, src_img):
        anchor_list_dict = {
            32: [[-248, -248, 263, 263], [-120, -120, 135, 135]],
            16: [[-56, -56, 71, 71], [-24, -24, 39, 39]],
            8: [[-8, -8, 23, 23], [0, 0, 15, 15]],
        }

        h_raw, w_raw = src_img.shape[:2]
        
        pad_ratio = 0.1 
        border_h = int(h_raw * pad_ratio)
        border_w = int(w_raw * pad_ratio)
        img_padded = cv2.copyMakeBorder(src_img, border_h, border_h, border_w, border_w, cv2.BORDER_CONSTANT, value=(0,0,0))
        
        h_pad, w_pad = img_padded.shape[:2]
        
        pad_h_32 = (32 - h_pad % 32) % 32
        pad_w_32 = (32 - w_pad % 32) % 32
        img_input = cv2.copyMakeBorder(img_padded, 0, pad_h_32, 0, pad_w_32, cv2.BORDER_CONSTANT, value=(0,0,0))
        
        img_tensor = img_input[np.newaxis, :, :, ::-1]
        tensors = model.predict(img_tensor)
        
        tensors = sorted(tensors, key=lambda s: (s.shape[1], s.shape[3]))
        
        result_tuple = []
        input_h, input_w = img_input.shape[:2]

        num_stages = 3 
        group_size = len(tensors) // num_stages

        for idx in range(0, len(tensors), group_size):
            _, f_h, f_w, c = tensors[idx].shape
            
            stride = int(round(input_w / f_w))
            
            if stride not in anchor_list_dict:
                stride = min(anchor_list_dict.keys(), key=lambda x: abs(x - stride))

            score_map = tensors[idx]
            curr_shape = score_map.shape
            score = np.exp(np.swapaxes(score_map.reshape(*curr_shape[:-1], 2, 2), -2, -1))
            score /= np.sum(score, axis=-1)[..., np.newaxis]
            score = np.swapaxes(score, -2, -1).reshape(*curr_shape)[0]
            score = score[..., 2:]
            
            indices = np.argwhere(score > self.SCORE_THRESHOLD)
            
            for max_index in indices:
                f_y, f_x, a_idx = max_index
                max_score = score[f_y, f_x, a_idx]
                anchor_template = anchor_list_dict[stride][a_idx]
                
                shift_x = f_x * stride
                shift_y = f_y * stride
                anchor = np.array([
                    anchor_template[0] + shift_x,
                    anchor_template[1] + shift_y,
                    anchor_template[2] + shift_x,
                    anchor_template[3] + shift_y
                ])

                box_offset = tensors[idx + 1][0, f_y, f_x, a_idx * 4 : (a_idx + 1) * 4]
                
                anchor_w = anchor[2] - anchor[0] + 1
                anchor_h = anchor[3] - anchor[1] + 1
                anchor_cx = anchor[0] + 0.5 * (anchor_w - 1.)
                anchor_cy = anchor[1] + 0.5 * (anchor_h - 1.)
                
                box_cx = anchor_cx + box_offset[0] * anchor_w
                box_cy = anchor_cy + box_offset[1] * anchor_h
                box_w = anchor_w * math.exp(box_offset[2])
                box_h = anchor_h * math.exp(box_offset[3])

                detected_x = box_cx - .5 * (box_w - 1.)
                detected_y = box_cy - .5 * (box_h - 1.)
                
                final_x = detected_x - border_w
                final_y = detected_y - border_h
                
                result_tuple.append(((final_x, final_y, box_w, box_h), max_score))
        
        sorted_tuple = sorted(result_tuple, key=lambda t: t[1], reverse=True)
        passed_tuple = []
        while sorted_tuple:
            curr_tuple = sorted_tuple[0]
            passed_tuple.append(curr_tuple)
            curr_rect = curr_tuple[0]
            del sorted_tuple[0]
            sorted_tuple = [t for t in sorted_tuple if iou(curr_rect, t[0]) < self.GROUP_THRESHOLD]

        if passed_tuple:
            img_area = w_raw * h_raw
            img_cx, img_cy = w_raw / 2, h_raw / 2
            max_dist = ((w_raw / 2) ** 2 + (h_raw / 2) ** 2) ** 0.5
            def get_priority(item):
                rect, score = item
                x, y, w, h = rect
                norm_area = (w * h) / img_area

                box_cx = x + (w / 2)
                box_cy = y + (h / 2)
                dist = ((box_cx - img_cx) ** 2 + (box_cy - img_cy) ** 2) ** 0.5
                norm_center = 1 - (dist / max_dist)
                return score + norm_area + 0.5 * norm_center
            
            passed_tuple.sort(key=get_priority, reverse=True)
            
            best_detect, full_detect = passed_tuple[0][0], passed_tuple
        else:
            best_detect, full_detect = None, None

        return best_detect, full_detect

if __name__ == "__main__":
    sess = ort.InferenceSession("LVOcclusionONNX/alignment_0407_fp16.onnx")
    x = np.random.rand(1, 256, 256, 3).astype(np.float16)
    out = sess.run(None, {sess.get_inputs()[0].name: x})
    print("OK", [o.shape for o in out])