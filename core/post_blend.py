import cv2
import numpy as np
import os
import math
import time
from .face_model_pipe import OnnxFacePipeline

DEBUG = False

class FaceDetailRestorer:
    def __init__(self, pipeline, sigma=7, detail_strength=0.7, forehead_strength=0.7, mask_shrink_size=12, is_pre_aligned=False):
        """
        Initialize the restorer
        """
        self.sigma = sigma
        self.detail_strength = detail_strength
        self.forehead_strength = forehead_strength # eyereplacement mode support
        self.mask_shrink_size = mask_shrink_size
        self.is_pre_aligned = is_pre_aligned
        self.pipeline = pipeline
        if DEBUG:
            print('[Init] FaceDetailRestorer initialized with pipeline.')

    class LevelAnchorTransform:
        """Helper class to handle coordinate system transformation (Eye-Level)."""
        def __init__(self, left_eye_center, right_eye_center):
            # 建立基底向量
            diff = right_eye_center - left_eye_center
            dist = np.linalg.norm(diff)
            
            if dist > 0:
                self.cos_v = diff[0] / dist
                self.sin_v = diff[1] / dist
            else:
                self.cos_v = 1.0
                self.sin_v = 0.0
            
            self.origin = left_eye_center
            self.scale = 1.0 # Assuming scale 1 for simplicity

        def transform(self, point):
            """Global to Local"""
            dx = point[0] - self.origin[0]
            dy = point[1] - self.origin[1]
            tx = dx * self.cos_v + dy * self.sin_v
            ty = -dx * self.sin_v + dy * self.cos_v
            return np.array([tx, ty], dtype=np.float32)

        def inverse_transform(self, point):
            """Local to Global"""
            tx = point[0]
            ty = point[1]
            x = self.origin[0] + tx * self.cos_v - ty * self.sin_v
            y = self.origin[1] + tx * self.sin_v + ty * self.cos_v
            return np.array([x, y], dtype=np.float32)
    
    def _estimate_head_pose(self, points):
        """
        Estimates Roll, Yaw, Tilt from 68 landmarks for the algorithm.
        Returns: (roll, yaw, tilt) in radians.
        """
        # 1. Roll: 兩眼連線的角度
        left_eye_center = np.mean(points[36:42], axis=0)
        right_eye_center = np.mean(points[42:48], axis=0)
        dY = right_eye_center[1] - left_eye_center[1]
        dX = right_eye_center[0] - left_eye_center[0]
        roll = np.arctan2(dY, dX)

        # 2. Yaw: 鼻頭 (30) 在兩眼寬度中的相對位置
        nose_top = points[27] # 鼻根
        nose_tip = points[30] # 鼻頭
        
        # 投影到眼睛連線向量上計算比例
        eye_dist = np.linalg.norm(right_eye_center - left_eye_center)
        if eye_dist == 0: eye_dist = 1.0
        
        # 向量：鼻頭 - 鼻根
        vec_nose = nose_tip - nose_top
        # 旋轉校正 Roll
        vec_nose_rot = np.array([
            vec_nose[0] * np.cos(-roll) - vec_nose[1] * np.sin(-roll),
            vec_nose[0] * np.sin(-roll) + vec_nose[1] * np.cos(-roll)
        ])
        
        # X 位移代表 Yaw
        yaw = np.arctan2(vec_nose_rot[0], eye_dist * 0.5) * 1.5 # 係數調整

        # 3. Tilt: 鼻子長度 vs 臉部長度
        chin = points[8]
        nose_len = np.linalg.norm(points[30] - points[27])
        face_len = np.linalg.norm(points[8] - points[27])
        
        # 正常比例約 1:3 左右? 這裡做一個經驗法則估算
        ratio = nose_len / (face_len + 1e-6)
        # 基準值 0.25, 大於它代表抬頭，小於代表低頭
        tilt = (ratio - 0.25) * 2.0 

        return roll, yaw, tilt

    def _get_forehead_points_ymk(self, points, face_rect_y):
        """
        Finding forehead points.
        """
        pts = np.array(points)
        
        # 0. pose estimation
        head_roll, head_yaw, head_tilt = self._estimate_head_pose(pts)

        def get_pt(idx): return pts[idx].astype(np.float32)

        # 1. Pose Ratio Logic
        clamp_yaw = np.clip(head_yaw, -0.5, 0.5)
        clamp_roll = np.clip(head_roll, -0.5, 0.5)
        ratio_left = 0.0
        ratio_right = 0.0
        
        if clamp_yaw > 0:
            ratio_left = clamp_yaw / 0.5
        elif clamp_yaw < 0:
            ratio_right = clamp_yaw / -0.5

        # 2. Level Anchor Transform
        left_mid_eye = self.left_eye_center.astype(np.float32)
        right_mid_eye = self.right_eye_center.astype(np.float32)
        transformer = self.LevelAnchorTransform(left_mid_eye, right_mid_eye)

        # 3. Calculate Mid Forehead
        pt_nose_bridge_top = get_pt(27)
        pt_nose_bottom = get_pt(33)
        pt_nose_tip = get_pt(30)
        
        mid_eye = pt_nose_bridge_top * (2.0/3.0) + pt_nose_tip * (1.0/3.0)
        
        # Vector from nose bottom to bridge top
        mid_eye_forehead_offset = pt_nose_bridge_top - pt_nose_bottom
        offset_norm = np.linalg.norm(mid_eye_forehead_offset)
        
        if offset_norm > 0:
            mid_eye_forehead_offset /= offset_norm
            
            # Predict Logic (Cross product check)
            offset1 = pt_nose_bridge_top - pt_nose_bottom
            offset2 = left_mid_eye - right_mid_eye
            delta = offset1[0]*offset2[1] - offset1[1]*offset2[0]

            pt_chin = get_pt(8)
            nose_bridge_length = np.linalg.norm(pt_nose_bridge_top - pt_nose_bottom)
            nose_bottom_chin_length = np.linalg.norm(pt_nose_bottom - pt_chin)
            
            # Estimation logic (Algorithm index != 2 path)
            estimate_tilt_theta = head_tilt
            transit_up = 0.1
            transit_down = -0.25
            ratio_up = 0.85
            ratio_down = 1.0
            
            # FitInRange logic
            val = (head_tilt - transit_down) / (transit_up - transit_down)
            weight_up = np.clip(val, 0.0, 1.0)
            ratio = ratio_up * weight_up + ratio_down * (1.0 - weight_up)

            tan_tilt = np.tan(estimate_tilt_theta)
            adjust_ratio = (1.0 - tan_tilt / 4.0) / (1.0 + tan_tilt / 4.0)
            
            mid_eye_forehead_length = (adjust_ratio * nose_bottom_chin_length + 
                                       (1.0/6.0 + 0.5 * adjust_ratio) * nose_bridge_length) * ratio
            
            factor = 0.6 + 0.4 * max((1.0 - max(ratio_left, ratio_right) * 0.5 * (abs(clamp_roll) * 1.25)), 0.0)
            mid_eye_forehead_length *= factor

            # Transform Vector
            transform_vec = transformer.transform(mid_eye + mid_eye_forehead_offset) - transformer.transform(mid_eye) # Vector transform hack
            
            adjust_offset = head_tilt * 2.0 if head_tilt > 0 else head_tilt * 1.5
            adjust_offset = adjust_offset * ratio_left if ratio_left > ratio_right else adjust_offset * ratio_right
            
            transform_vec[0] *= (1.0 + np.clip(adjust_offset, -1.0, 1.0))
            
            # Inverse Transform Vector
            vec_global_end = transformer.inverse_transform(transformer.transform(mid_eye) + transform_vec)
            mid_eye_forehead_offset = vec_global_end - mid_eye
            mid_eye_forehead_offset /= (np.linalg.norm(mid_eye_forehead_offset) + 1e-6)

            mid_forehead = mid_eye + mid_eye_forehead_offset * mid_eye_forehead_length
            
            # Clamp Y
            # In our case, face_rect.y is usually 0 if pre_aligned, or bounding box top.
            # We want forehead to be ABOVE eyes.
            mid_forehead[1] = min(mid_forehead[1], face_rect_y if face_rect_y > 0 else mid_eye[1])

        else:
            mid_forehead = mid_eye # Fallback

        # 4. Calculate Left/Right Forehead
        # heavily modifies the position based on "Ear Top"
        
        # Transform everything to Local
        trans_mid_forehead = transformer.transform(mid_forehead)
        trans_left_ear = transformer.transform(get_pt(0))   # Left Temple/Ear
        trans_right_ear = transformer.transform(get_pt(16)) # Right Temple/Ear
        
        # Mid-face interpolation
        trans_face_mid = trans_left_ear * 0.5 + trans_right_ear * 0.5
        trans_l_outer = trans_face_mid * 0.2 + trans_left_ear * 0.8
        trans_r_outer = trans_face_mid * 0.2 + trans_right_ear * 0.8
        
        # Adjust Ratios
        down_ratio = (1.0 - head_tilt) / 2.0
        l_adj_ratio = min(down_ratio * 0.25 + ratio_right * 0.6, 1.0)
        r_adj_ratio = min(down_ratio * 0.25 + ratio_left * 0.6, 1.0)
        
        # Calculate X
        final_l_x = trans_left_ear[0] * l_adj_ratio + trans_l_outer[0] * (1.0 - l_adj_ratio)
        final_r_x = trans_right_ear[0] * r_adj_ratio + trans_r_outer[0] * (1.0 - r_adj_ratio)
        
        # Brow Constraints (Inner anchors)
        trans_l_inner = trans_face_mid * 0.25 + trans_left_ear * 0.75
        trans_r_inner = trans_face_mid * 0.25 + trans_right_ear * 0.75
        
        trans_l_brow_top = transformer.transform(get_pt(19)) # Brow Peak
        trans_r_brow_top = transformer.transform(get_pt(24))
        
        final_l_x = final_l_x * (1.0 - ratio_left) + min(trans_l_brow_top[0], trans_l_inner[0]) * ratio_left
        final_r_x = final_r_x * (1.0 - ratio_right) + max(trans_r_brow_top[0], trans_r_inner[0]) * ratio_right
        
        # Calculate Y, Interpolate between "Ellipse Sample" (which we approximate as MidForehead Y) and Brow Y
        
        trans_l_brow_left = transformer.transform(get_pt(17))
        trans_r_brow_right = transformer.transform(get_pt(26))
        
        l_y_adj = ratio_left * 0.25 + max(head_tilt, 0.0) * 0.25
        r_y_adj = ratio_right * 0.25 + max(head_tilt, 0.0) * 0.25
        
        # Initial Y guess (from ellipse logic approximation)
        start_y = trans_mid_forehead[1] 
        
        final_l_y = start_y * (1.0 - l_y_adj) + trans_l_brow_left[1] * l_y_adj
        final_r_y = start_y * (1.0 - r_y_adj) + trans_r_brow_right[1] * r_y_adj
        
        # Shift Y logic
        trans_nose_top = transformer.transform(pt_nose_tip)
        trans_nose_bridge = transformer.transform(pt_nose_bridge_top)
        y_shift_dist = (trans_nose_top[1] - trans_nose_bridge[1]) * 0.1
        
        final_l_y *= 1.01
        final_r_y *= 1.01
        final_l_y -= y_shift_dist * (0.15 + 0.85 * l_adj_ratio)
        final_r_y -= y_shift_dist * (0.15 + 0.85 * r_adj_ratio)
        
        # Adjust Tilt Global
        adjust_tilt = max((1.0 + -np.tan(head_tilt) * 3.0), 0.0)
        final_l_y -= y_shift_dist * adjust_tilt
        final_r_y -= y_shift_dist * adjust_tilt
        trans_mid_forehead[1] -= y_shift_dist * adjust_tilt
        
        # Inverse Transform
        ret_mid = transformer.inverse_transform(trans_mid_forehead)
        ret_left = transformer.inverse_transform(np.array([final_l_x, final_l_y]))
        ret_right = transformer.inverse_transform(np.array([final_r_x, final_r_y]))
        
        # Return as list of tuples (Left, Mid, Right)
        return [
            tuple(ret_left.astype(int)),
            tuple(ret_mid.astype(int)),
            tuple(ret_right.astype(int))
        ]
    
    def _map_84_to_68(self, points_84):
        """
        Maps 84-point landmarks (Custom) to 68-point landmarks (Dlib Standard).
        """
        if len(points_84) != 84:
            return points_84

        points_68 = [(0, 0)] * 68

        # --- 1. Jaw (Dlib 0-16) ---
        # Image: 65(L Ear) -> 73(Chin) -> 81(R Ear)
        for i in range(17):
            points_68[i] = points_84[65 + i]

        # --- 2. Eyebrows (Dlib 17-26) ---
        # Left Brow (Dlib 17-21) <- Image 0-4
        for i in range(5):
            points_68[17 + i] = points_84[0 + i]
        
        # Right Brow (Dlib 22-26) <- Image 15-19
        for i in range(5):
            points_68[22 + i] = points_84[15 + i]

        # --- 3. Nose (Dlib 27-35) ---
        # Bridge (27-30) <- Image 30-33
        for i in range(4):
            points_68[27 + i] = points_84[30 + i]
        
        # Base (31-35) <- Image 34-38
        # Dlib: 31(L), 32, 33(Tip), 34, 35(R)
        # Image: 34(L), 35, 36(Tip), 37, 38(R)
        points_68[31] = points_84[37] # L Corner
        points_68[32] = points_84[34]
        points_68[33] = points_84[35] # Tip
        points_68[34] = points_84[36]
        points_68[35] = points_84[38] # R Corner

        # --- 4. Eyes (Dlib 36-47) ---
        # Left Eye (Dlib 36-41): Corner, Top1, Top2, Corner, Bot1, Bot2
        points_68[36] = points_84[5]  # L Corner
        points_68[37] = points_84[11]  # Top 1
        points_68[38] = points_84[12]  # Top 2
        points_68[39] = points_84[7]  # R Corner
        points_68[40] = points_84[13] # Bot 1
        points_68[41] = points_84[14] # Bot 2

        # Right Eye (Dlib 42-47)
        points_68[42] = points_84[20] # L Corner
        points_68[43] = points_84[26] # Top 1
        points_68[44] = points_84[27] # Top 2
        points_68[45] = points_84[22] # R Corner
        points_68[46] = points_84[28] # Bot 1
        points_68[47] = points_84[29] # Bot 2

        # --- 5. Mouth (Dlib 48-67) ---
        # Dlib 48-59 (Outer Lip, 12 points)
        points_68[48] = points_84[39] # L Corner
        points_68[49] = points_84[44]
        points_68[50] = points_84[40]
        points_68[51] = points_84[41] # Top Middle
        points_68[52] = points_84[48] 
        points_68[53] = points_84[40] 
        points_68[54] = points_84[43] # R Corner
        points_68[55] = points_84[56]
        points_68[56] = points_84[55]
        points_68[57] = points_84[50] # Bot Middle
        points_68[58] = points_84[54]
        points_68[59] = points_84[53]

        # Inner Lip (60-67)
        # Image Inner: 46(L), 58(R). Upper(46-50), Lower(58-64)
        points_68[60] = points_84[57] # L Inner
        points_68[61] = points_84[60]
        points_68[62] = points_84[58]
        points_68[63] = points_84[61]
        points_68[64] = points_84[59] # R Inner
        points_68[65] = points_84[64] 
        points_68[66] = points_84[62]
        points_68[67] = points_84[63]

        self.left_eye_center = np.array(points_84[10])
        self.right_eye_center = np.array(points_84[25])

        return points_68
    
    def get_landmarks(self, img, debug_save_path=None, detect_debug_path=None):
        h, w = img.shape[:2]
        
        # 1. Detection
        rect_list = self.pipeline.detect(img, bypass=True) # return [x, y, w, h]
        if rect_list is None:
            raise ValueError("No face detected.")
        
        if DEBUG and detect_debug_path:
            vis = img.copy()
            x, y, rect_w, rect_h = rect_list
            
            # rect_list [x, y, w, h]
            pt1 = (int(x), int(y))
            pt2 = (int(x + rect_w), int(y + rect_h))
            
            cv2.rectangle(vis, pt1, pt2, (0, 0, 255), 3)
            
            info_text = f"Rect: {int(x)},{int(y)} {int(rect_w)}x{int(rect_h)}"
            cv2.putText(vis, info_text, (int(x), int(y)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            
            print(f"[Debug] Saving detection rect to {detect_debug_path}")
            cv2.imwrite(detect_debug_path, vis)
        
        # 2. Alignment (84 points)
        points_84 = self.pipeline.align(img, rect_list)[:84]
        
        # Debug Save Raw 84 points
        if DEBUG and debug_save_path:
            self.draw_debug_landmarks(img, points_84, debug_save_path)
        
        points_68 = self._map_84_to_68(points_84)
        
        # Clamp
        safe_points = []
        for (x, y) in points_68:
            safe_points.append((max(0, min(x, w - 1)), max(0, min(y, h - 1))))
            
        return safe_points, points_84

    def draw_debug_landmarks(self, img, points, filename):
        """Draws landmarks WITH INDICES on the image and saves it."""
        canvas = img.copy()

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.3
        
        for i, p in enumerate(points):
            p = (int(p[0]), int(p[1]))
            cv2.circle(canvas, p, 2, (0, 255, 0), -1)
            
            cv2.putText(canvas, str(i), (p[0]+3, p[1]-2), font, font_scale, (0, 0, 255), 1, cv2.LINE_AA)
            
        cv2.imwrite(filename, canvas)

    def extract_texture(self, img):
        """
        Extracts texture from the LAB L-channel.
        """
        img_lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
        l_channel, _, _ = cv2.split(img_lab)
        
        l_blurred = cv2.GaussianBlur(l_channel, (0, 0), self.sigma)
        texture_l = l_channel - l_blurred

        '''
        # Shadow Attenuation
        shadow_indices = texture_l < 0
        texture_l[shadow_indices] *= 0.5 
        
        # Clamping
        CLIP_POS = 30.0
        CLIP_NEG = 15.0
        texture_l = np.clip(texture_l, -CLIP_NEG, CLIP_POS)
        '''
        
        texture_monochrome = cv2.merge([texture_l, texture_l, texture_l])
        
        return texture_monochrome

    def _apply_affine_transform(self, src, src_tri, dst_tri, size):
        """Internal helper: Applies affine transform to a rectangular patch."""
        warp_mat = cv2.getAffineTransform(np.float32(src_tri), np.float32(dst_tri))
        dst = cv2.warpAffine(src, warp_mat, (size[0], size[1]), 
                             None, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return dst

    def _warp_triangle(self, img_src, img_dst, t_src, t_dst):
        """Internal helper: Warps a specific triangular region."""
        # Find bounding box for each triangle
        r1 = cv2.boundingRect(np.float32([t_src]))
        r2 = cv2.boundingRect(np.float32([t_dst]))

        # Offset points by left top corner of the respective rectangles
        t1_rect = []
        t2_rect = []
        t2_rect_int = []

        for i in range(0, 3):
            t1_rect.append(((t_src[i][0] - r1[0]), (t_src[i][1] - r1[1])))
            t2_rect.append(((t_dst[i][0] - r2[0]), (t_dst[i][1] - r2[1])))
            t2_rect_int.append(((t_dst[i][0] - r2[0]), (t_dst[i][1] - r2[1])))

        # Get mask by filling triangle
        mask = np.zeros((r2[3], r2[2], 3), dtype=np.float32)
        cv2.fillConvexPoly(mask, np.int32(t2_rect_int), (1.0, 1.0, 1.0), 16, 0)

        # Apply warp to small rectangular patches
        img1_rect = img_src[r1[1]:r1[1] + r1[3], r1[0]:r1[0] + r1[2]]
        size = (r2[2], r2[3])
        
        img2_rect = self._apply_affine_transform(img1_rect, t1_rect, t2_rect, size)
        img2_rect = img2_rect * mask
        
        # Blend into destination
        img_dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]] = \
            img_dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]] * ((1.0, 1.0, 1.0) - mask)
        
        img_dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]] = \
            img_dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]] + img2_rect

    def generate_warped_texture(self, texture_src, points_src, points_dst, target_shape):
        """
        Performs Delaunay Triangulation and warps texture (Core function without debug).
        Returns:
            warped_texture: The result image.
            tri_indices: List of tuples [(idx1, idx2, idx3), ...] used for the mesh.
        """
        warped_texture = np.zeros(target_shape, dtype=np.float32)
        
        rect = (0, 0, target_shape[1] + 10, target_shape[0] + 10)
        subdiv = cv2.Subdiv2D(rect)
        
        for p in points_dst:
            p_safe = (float(p[0]), float(p[1]))
            subdiv.insert(p_safe)
            
        triangle_list = subdiv.getTriangleList()
        
        used_tri_indices = []

        for t in triangle_list:
            pts_tri_dst = []
            pts_tri_src = []
            pts_indices = []
            
            valid_triangle = True
            for i in range(3):
                pt = (t[2*i], t[2*i+1])
                
                min_dist = 1000
                index = -1
                for k in range(len(points_dst)):
                    dist = np.linalg.norm(np.array(pt) - np.array(points_dst[k]))
                    if dist < min_dist:
                        min_dist = dist
                        index = k
                
                if min_dist < 1.0:
                    pts_tri_dst.append(points_dst[index])
                    pts_tri_src.append(points_src[index])
                    pts_indices.append(index)
                else:
                    valid_triangle = False
                    break
            
            if valid_triangle:
                # Warp
                self._warp_triangle(texture_src, warped_texture, pts_tri_src, pts_tri_dst)
                used_tri_indices.append(tuple(pts_indices))
        
        return warped_texture, used_tri_indices
    
    def _suppress_mouth_corner_shadows(self, texture, points, shape):
        """
        Suppresses deep wrinkles (Nasolabial folds & Marionette lines) using 
        anatomical polygons instead of simple circles.
        """
        h, w = shape[:2]
        
        mask_wrinkles = np.zeros((h, w), dtype=np.float32)

        def p(idx): return (int(points[idx][0]), int(points[idx][1]))

        poly_left = np.array([
            p(31), # 鼻翼
            p(48), # 嘴角
            p(6),  # 下顎轉角 (木偶紋終點)
            p(4)   # 臉頰外側 (讓範圍寬一點)
        ], dtype=np.int32)
        
        poly_right = np.array([
            p(35), 
            p(54), 
            p(10), 
            p(12)
        ], dtype=np.int32)

        cv2.fillPoly(mask_wrinkles, [poly_left, poly_right], 1.0)
        mask_wrinkles = cv2.GaussianBlur(mask_wrinkles, (61, 61), 30)
        mask_wrinkles_3c = cv2.merge([mask_wrinkles, mask_wrinkles, mask_wrinkles])
        
        tex_neg = np.minimum(texture, 0)
        tex_pos = np.maximum(texture, 0)
        suppression_factor = 1.0 - (mask_wrinkles_3c * 0.95)
        tex_neg_suppressed = tex_neg * suppression_factor
        
        min_allowed_map = -100.0 + (mask_wrinkles_3c * 95.0) # -100 ~ -5
        
        tex_neg_suppressed = np.maximum(tex_neg_suppressed, min_allowed_map)
        texture_fixed = tex_pos + tex_neg_suppressed
        
        return texture_fixed
    
    def _get_forehead_points(self, points, shape):
        """
        Calculates 3 auxiliary points for the forehead with ROTATION ROBUSTNESS.
        """
        h, w = shape[:2]
        
        try:
            forehead_points_ymk = self._get_forehead_points_ymk(points, 0)
            
            # Clamp
            clamped_pts = []
            for p in forehead_points_ymk:
                x = max(0, min(int(p[0]), w - 1))
                y = max(0, min(int(p[1]), h - 1))
                clamped_pts.append((x, y))
            
            return clamped_pts
            
        except Exception as e:
            if DEBUG:
                print(f"Error in YMK forehead logic: {e}. Falling back to simple logic.")
        
            # 1. 臉的垂直方向
            brow_center = (points[21] + points[22]) / 2.0
            nose_bridge = points[27]
            up_vec = brow_center - nose_bridge
            
            # 2. 計算額頭中心點 (Top Mid)
            # scale，3.0 ~ 3.5 差不多是髮際線高度
            scale = 3.0
            top_mid = brow_center + up_vec * scale
            
            # 3. 計算左右額頭點 (Top Left / Top Right)
            # 19(左眉峰)、24(右眉峰)
            anchor_left = points[19]
            anchor_right = points[24]
            
            raw_top_left = anchor_left + up_vec * scale * 0.9
            raw_top_right = anchor_right + up_vec * scale * 0.9
            
            convergence = 0.2
            
            final_top_left = raw_top_left * (1 - convergence) + top_mid * convergence
            final_top_right = raw_top_right * (1 - convergence) + top_mid * convergence
            
            # 4. Clamp
            raw_pts = [final_top_left, top_mid, final_top_right]
            clamped_pts = []
            
            for p in raw_pts:
                x = max(0, min(int(p[0]), w - 1))
                y = max(0, min(int(p[1]), h - 1))
                clamped_pts.append((x, y))
                
            return clamped_pts
    
    def _get_cheek_points(self, points):
        """
        Calculates 2 auxiliary points for the center of the cheeks (Steiner Points).
        This breaks the large triangles on cheeks to prevent ghosting artifacts.
        """
        # 左臉頰中心 (Left Cheek)
        # 取決於: 左眼外角(36), 左鼻翼(31), 左嘴角(48), 左下顎角(4)
        left_cheek_x = (points[36][0] + points[31][0] + points[48][0] + points[4][0]) / 4.0
        left_cheek_y = (points[36][1] + points[31][1] + points[48][1] + points[4][1]) / 4.0
        
        # 右臉頰中心 (Right Cheek)
        # 取決於: 右眼外角(45), 右鼻翼(35), 右嘴角(54), 右下顎角(12)
        right_cheek_x = (points[45][0] + points[35][0] + points[54][0] + points[12][0]) / 4.0
        right_cheek_y = (points[45][1] + points[35][1] + points[54][1] + points[12][1]) / 4.0
        
        return [
            (int(left_cheek_x), int(left_cheek_y)), 
            (int(right_cheek_x), int(right_cheek_y))
        ]
    
    def create_blending_mask(self, points, shape, seg_mask=None, shrink_override=None, blur_override=None, \
                             roi_dilate_size=41, fade_power=2.0, exclude_features=True, seg_erosion=0):
        """
        Creates a soft mask based on Landmarks + Optional Segmentation Mask.
        Logic: (Dilated ROI * SegMask) - (Mouth + NoseTip + Eyes)
        """
        mask_u8 = np.zeros((shape[0], shape[1]), dtype=np.uint8)

        shrink_size = shrink_override if shrink_override is not None else self.mask_shrink_size
        blur_size = blur_override if blur_override is not None else 30
        
        # ==========================================
        # Step A: Draw Base Geometry (Positive Area)
        # ==========================================
        roi_points = []
        
        # jawline
        for i in range(0, 17): 
            roi_points.append(points[i])
        
        # Right Eye / Nose / Left Eye Logic
        roi_points.extend([points[45], points[46], points[47], points[42]]) 
        roi_points.extend([points[35], points[34], points[33], points[32], points[31]]) 
        roi_points.extend([points[39], points[40], points[41], points[36]]) 
        
        roi_points = np.array(roi_points, dtype=np.int32)
        cv2.fillPoly(mask_u8, [roi_points], 255)

        # Adding Forehead
        pts_np = np.array(points)
        forehead_pts = self._get_forehead_points(pts_np, shape)
        forehead_poly = [
            points[0], points[36] ,points[17], points[19], points[21], points[22], points[24], points[26], points[45], points[16],
            forehead_pts[2], forehead_pts[1], forehead_pts[0]
        ]
        cv2.fillPoly(mask_u8, [np.array(forehead_poly, dtype=np.int32)], 255)

        # 左眼窩 (Left Socket): 連接 左眉毛(17-21) 與 左眼上緣(36-39)
        left_eyelid_poly = [
            points[36], # 左眼外角
            points[17], # 左眉尾
            points[18],
            points[19],
            points[20],
            points[21], # 左眉頭
            points[39], # 左眼內角
            points[38], # 左眼上緣
            points[37]  # 左眼上緣
        ]
        cv2.fillPoly(mask_u8, [np.array(left_eyelid_poly, dtype=np.int32)], 255)

        # 右眼窩 (Right Socket): 連接 右眉毛(22-26) 與 右眼上緣(42-45)
        right_eyelid_poly = [
            points[45], # 右眼外角
            points[26], # 右眉尾
            points[25],
            points[24],
            points[23],
            points[22], # 右眉頭
            points[42], # 右眼內角
            points[43], # 右眼上緣
            points[44]  # 右眼上緣
        ]
        cv2.fillPoly(mask_u8, [np.array(right_eyelid_poly, dtype=np.int32)], 255)

        # Adding Nose Bridge
        nose_poly = [
            points[31], points[35],
            points[42], points[22], points[21], points[39]
        ]
        cv2.fillPoly(mask_u8, [np.array(nose_poly, dtype=np.int32)], 255)

        # ==========================================
        # Step B: Integrate Segmentation Mask
        # ==========================================
        if seg_mask is not None:
            # 1. Dilate
            kernel_dilate = np.ones((roi_dilate_size, roi_dilate_size), np.uint8)
            mask_expanded = cv2.dilate(mask_u8, kernel_dilate, iterations=1)
            
            # 2. Multiply
            mask_float = mask_expanded.astype(np.float32) / 255.0
            if len(seg_mask.shape) == 3 and seg_mask.shape[2] == 3:
                seg_mask_single = seg_mask[:,:,0]
            else:
                seg_mask_single = seg_mask
                
            if seg_erosion > 0:
                kernel_seg = np.ones((seg_erosion, seg_erosion), np.uint8)
                seg_u8 = (seg_mask_single * 255).astype(np.uint8)
                seg_eroded_u8 = cv2.erode(seg_u8, kernel_seg, iterations=1)
                seg_eroded_f = seg_eroded_u8.astype(np.float32) / 255.0
                
                erosion_weight = np.zeros(shape[:2], dtype=np.float32)
                
                p0 = np.array(points[0])   # 左鬢角
                p16 = np.array(points[16]) # 右鬢角
                
                vec_x = p16[0] - p0[0]
                vec_y = p16[1] - p0[1]
                face_width = np.hypot(vec_x, vec_y)
                
                if face_width == 0: face_width = 1.0
                
                dir_right_x = vec_x / face_width
                dir_right_y = vec_y / face_width
                dir_up_x = dir_right_y
                dir_up_y = -dir_right_x
                
                ext_w = face_width * 1.0 
                ext_h = face_width * 2.0
                
                p16_far = p16 + np.array([dir_right_x, dir_right_y]) * ext_w
                p0_far = p0 - np.array([dir_right_x, dir_right_y]) * ext_w
                p16_sky = p16_far + np.array([dir_up_x, dir_up_y]) * ext_h
                p0_sky = p0_far + np.array([dir_up_x, dir_up_y]) * ext_h
                
                brows_right = [points[i] for i in range(26, 21, -1)]
                brows_left = [points[i] for i in range(21, 16, -1)]
                
                upper_zone_poly = np.array([
                    p16_sky,         # 右上
                    p16_far,         # 右側
                    p16,             # 右鬢角 (接合點)
                    *brows_right,    # 右眉上緣
                    *brows_left,     # 左眉上緣
                    p0,              # 左鬢角 (接合點)
                    p0_far,          # 左側
                    p0_sky           # 左上
                ], dtype=np.int32)
                
                cv2.fillPoly(erosion_weight, [upper_zone_poly], 1.0)
                
                erosion_weight = cv2.GaussianBlur(erosion_weight, (101, 101), 50)
                
                seg_mask_final = seg_eroded_f * erosion_weight + seg_mask_single * (1.0 - erosion_weight)
            else:
                seg_mask_final = seg_mask_single
            
            mask_combined = mask_float * seg_mask_final
            #mask_combined = mask_float * seg_mask_single
            mask_u8 = (mask_combined * 255).astype(np.uint8)

        # ==========================================
        # Step C: Strict Exclusions (Negative Area)
        # ==========================================
        
        def get_extended_point(p_base, p_target, ratio=0.25):
            p_base = np.array(p_base)
            p_target = np.array(p_target)
            res = p_base + (p_target - p_base) * ratio
            return res.astype(np.int32)
        
        # 0. Mouth (Inner)
        mouth_points = np.array(points[60:68], dtype=np.int32)
        cv2.fillPoly(mask_u8, [mouth_points], 0)

        if exclude_features:
            # 1. Mouth
            mouth_points = np.array(points[48:60], dtype=np.int32)
            cv2.fillPoly(mask_u8, [mouth_points], 0)
            
            # 2. Nose Tip
            nose_exclusion = np.array([
                points[30], points[31], points[32], points[33], points[34], points[35]
            ], dtype=np.int32)
            cv2.fillPoly(mask_u8, [nose_exclusion], 0)

            # 3. Eyes
            left_eye = np.array(points[36:42], dtype=np.int32)
            right_eye = np.array(points[42:48], dtype=np.int32)
            cv2.fillPoly(mask_u8, [left_eye], 0)
            cv2.fillPoly(mask_u8, [right_eye], 0)

            left_eyebrow = np.array(points[17:21], dtype=np.int32)
            right_eyebrow = np.array(points[22:26], dtype=np.int32)
            cv2.fillPoly(mask_u8, [left_eyebrow], 0)
            cv2.fillPoly(mask_u8, [right_eyebrow], 0)
            
            # 4. Nose Sidewalls
            inner_gap = 0.6   # 內側保留: 數值越大，鼻樑留得越寬
            outer_reach = 0.55 # 外側覆蓋: 數值越大，遮住的陰影越多
            flare_ratio = 1.3  # 鼻翼外擴: 針對鼻溝陰影

            # --- 左側鼻坡 (Left Slope Exclusion) ---
            # 內緣
            p27_in_L = get_extended_point(points[27], points[39], ratio=inner_gap)
            p28_in_L = get_extended_point(points[28], points[39], ratio=inner_gap)
            p29_in_L = get_extended_point(points[29], points[39], ratio=inner_gap)
            
            # 外緣
            p28_out_L = get_extended_point(points[28], points[39], ratio=outer_reach)
            p29_out_L = get_extended_point(points[29], points[39], ratio=outer_reach)
            
            # 鼻翼外擴點 (針對法令紋起點)
            p31_flare_L = get_extended_point(points[30], points[31], ratio=flare_ratio)

            nose_slope_left = np.array([
                p27_in_L,     # 上內
                p28_in_L,     # 中內
                p29_in_L,     # 下內
                p31_flare_L,  # 下外 (Flare)
                p29_out_L,    # 中外
                p28_out_L     # 上外
            ], dtype=np.int32)
            cv2.fillPoly(mask_u8, [nose_slope_left], 0)

            # --- 右側鼻坡 (Right Slope Exclusion) ---
            # 內緣
            p27_in_R = get_extended_point(points[27], points[42], ratio=inner_gap)
            p28_in_R = get_extended_point(points[28], points[42], ratio=inner_gap)
            p29_in_R = get_extended_point(points[29], points[42], ratio=inner_gap)
            
            # 外緣
            p28_out_R = get_extended_point(points[28], points[42], ratio=outer_reach)
            p29_out_R = get_extended_point(points[29], points[42], ratio=outer_reach)
            
            # Flare
            p35_flare_R = get_extended_point(points[30], points[35], ratio=flare_ratio)

            nose_slope_right = np.array([
                p27_in_R,
                p28_in_R,
                p29_in_R,
                p35_flare_R,
                p29_out_R,
                p28_out_R
            ], dtype=np.int32)
            cv2.fillPoly(mask_u8, [nose_slope_right], 0)

        # ==========================================
        # Step D: Final Post-Processing
        # ==========================================
        if shrink_size > 0:
            kernel = np.ones((shrink_size, shrink_size), np.uint8)
            mask_u8 = cv2.erode(mask_u8, kernel, iterations=1)

        dist_map = cv2.distanceTransform(mask_u8, cv2.DIST_L2, 5)
        fade_width = float(blur_size if blur_size is not None else 30)
        mask_geom_f = np.clip(dist_map / fade_width, 0.0, 1.0)
        mask_geom_f = mask_geom_f * mask_geom_f * (3.0 - 2.0 * mask_geom_f) # Smoothstep
        
        if seg_mask is not None:
            seg_blur_size = int(fade_width) | 1
            seg_mask_soft = cv2.GaussianBlur(seg_mask_final, (seg_blur_size, seg_blur_size), 0)
            mask_final_f = np.minimum(mask_geom_f, seg_mask_soft)
        else:
            mask_final_f = mask_geom_f
        
        if fade_power != 1.0:
            mask_final_f = np.power(mask_final_f, fade_power)
        
        mask_final_f = cv2.GaussianBlur(mask_final_f, (5, 5), 0)
        
        return cv2.merge([mask_final_f, mask_final_f, mask_final_f])

    def _create_structure_mask(self, img, threshold=15, dilation_size=9):
        """
        Creates a mask identifying strong structural edges.
        dilation_size: Controls how much to expand the protection area. 
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 1. Detect Edges
        blurred = cv2.GaussianBlur(gray, (0, 0), 3)
        high_freq = cv2.absdiff(gray, blurred)
        
        # 2. Thresholding
        _, mask_u8 = cv2.threshold(high_freq, threshold, 255, cv2.THRESH_BINARY)
        
        # 3. Dilate
        k_size = dilation_size
        if k_size % 2 == 0: k_size += 1
            
        kernel = np.ones((k_size, k_size), np.uint8)
        mask_u8 = cv2.dilate(mask_u8, kernel, iterations=1)
        
        # 4. Blur
        blur_sigma = k_size // 2
        blur_k = blur_sigma * 2 + 1
        mask_f = mask_u8.astype(np.float32) / 255.0
        mask_f = cv2.GaussianBlur(mask_f, (blur_k, blur_k), blur_sigma)
        
        return cv2.merge([mask_f, mask_f, mask_f])
    
    def _create_target_smoothness_map(self, l_channel, sensitivity=0.5, min_strength=0.2, max_strength=1.0):
        """
        Analyzes the Target Image (L-channel) to find smooth areas vs. structured areas.
        Returns a weight map indicating desired texture strength.
        
        Args:
            l_channel: The L channel of the target image (float32).
            sensitivity: Controls gradient detection threshold. Lower = more sensitive to edges.
            min_strength: Texture strength on edges (keep low to avoid dirty look).
            max_strength: Texture strength on smooth skin (boost to remove plastic look).
        """
        l_blur = cv2.GaussianBlur(l_channel, (0, 0), 2.0)
        
        grad_x = cv2.Sobel(l_blur, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(l_blur, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = cv2.magnitude(grad_x, grad_y)
        
        mag_norm = magnitude / 50.0
        
        base_weight = 1.0 / (1.0 + np.power(mag_norm / sensitivity, 4.0))
        
        smoothness_map = min_strength + (base_weight * (max_strength - min_strength))
        
        kernel = np.ones((5, 5), np.uint8)
        smoothness_map = cv2.erode(smoothness_map, kernel, iterations=1)
        smoothness_map = cv2.GaussianBlur(smoothness_map, (9, 9), 0)
        
        return smoothness_map
    
    def _calculate_luminance_protection(self, l_channel, threshold_low=30, threshold_high=225):
        """
        Calculates a weight map to protect highlights and shadows from clipping.
        - Near 255 or 0: Weight -> 0.0 (Stop adding texture)
        - Mid-tones: Weight -> 1.0 (Safe to add)
        """
        l_norm = l_channel.astype(np.float32) / 255.0
        
        high_limit = threshold_high / 255.0
        protect_high = np.clip((1.0 - l_norm) / (1.0 - high_limit), 0.0, 1.0)
        protect_high = protect_high * protect_high * (3.0 - 2.0 * protect_high)
        
        low_limit = threshold_low / 255.0
        protect_low = np.clip(l_norm / low_limit, 0.0, 1.0)
        protect_low = protect_low * protect_low * (3.0 - 2.0 * protect_low)
        
        protection_map = protect_high * protect_low
        
        return protection_map
    
    def _get_crop_matrix(self, rect, src_w, src_h, target_size=1024, padding=2.0):
        """
        Calculates the Affine Matrix to crop and resize the face to target_size.
        
        Args:
            rect: [x, y, w, h] from detection.
            padding: 擴張係數。
                     1.0 = 剛好框住臉 (太擠，會切到額頭/下巴)
                     2.0 = 框住臉並包含周圍背景 (建議值，確保包含額頭與髮際線)
        """
        if rect is None:
            # 如果沒有框 (例如 pre-aligned)，直接 Resize
            scale = target_size / max(src_w, src_h)
            M = np.array([
                [scale, 0, (target_size - src_w * scale) * 0.5],
                [0, scale, (target_size - src_h * scale) * 0.5]
            ])
            return M

        x, y, w, h = rect
        
        # 1. 計算中心點
        center_x = x + w * 0.5
        center_y = y + h * 0.5
        
        # 2. 決定裁切大小 (取長邊並乘上 padding)
        box_size = max(w, h) * padding
        
        # 3. 計算縮放比例 (目標 1024 / 實際裁切大小)
        scale = target_size / box_size
        
        # 4. 建立仿射矩陣 [Scale, 0, TransX; 0, Scale, TransY]
        # TransX = (目標中心) - (原圖中心 * 縮放)
        M = np.array([
            [scale, 0, target_size * 0.5 - center_x * scale],
            [0, scale, target_size * 0.5 - center_y * scale]
        ])
        
        return M

    def _paste_back(self, full_img, crop_processed, crop_mask, M):
        """
        Pastes the processed crop back into the full image using Inverse Warp.
        """
        h_full, w_full = full_img.shape[:2]
        h_crop, w_crop = crop_processed.shape[:2]
        
        warped_result = cv2.warpAffine(
            crop_processed, M, (w_full, h_full), 
            flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0)
        )
        
        warped_mask = cv2.warpAffine(
            crop_mask, M, (w_full, h_full), 
            flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0)
        )
        
        # 3. Alpha Blending
        # Final = Original * (1 - Mask) + Processed * Mask
        full_img_float = full_img.astype(np.float32)
        warped_result_float = warped_result.astype(np.float32)
        
        if len(warped_mask.shape) == 2:
            warped_mask = cv2.merge([warped_mask, warped_mask, warped_mask])
            
        final_img = full_img_float * (1.0 - warped_mask) + warped_result_float * warped_mask
        
        return np.clip(final_img, 0, 255).astype(np.uint8)
    
    def _get_crow_feet_poly(self, points, side='left'):
        """
        Calculates a dynamic polygon covering the Crow's Feet area.
        Adapts to head rotation (yaw) by using face contour landmarks.
        """
        if side == 'left':
            idx_eye_corner = [39, 40, 41, 36]  # 左眼下緣
            idx_brow_tail = 17   # 左眉尾
            idx_jaw = [0, 1, 2]
        else: # right
            idx_eye_corner = [42, 47, 46, 45]
            idx_brow_tail = 26
            idx_jaw = [16, 15, 14]
        
        p_eye = np.array([points[i] for i in idx_eye_corner])
        p_brow = np.array(points[idx_brow_tail])
        p_jaw = np.array([points[i] for i in idx_jaw])
        
        poly = np.vstack([
            p_eye,
            p_brow,
            p_jaw
        ]).astype(np.int32)
        
        return poly

    def _create_eye_surround_mask(self, points, shape, clip_mask=None):
        """
        Creates a mask covering the area AROUND the eyes
        Used to force 'Replacement Mode' in these areas to remove wrinkles.
        """
        h, w = shape[:2]
        
        mask_L = np.zeros((h, w), dtype=np.float32)
        mask_R = np.zeros((h, w), dtype=np.float32)

        poly_left = self._get_crow_feet_poly(points, side='left')
        poly_right = self._get_crow_feet_poly(points, side='right')

        def get_dynamic_sigma(pt_eye, pt_edge, min_sigma=2.0, max_sigma=12.0, ratio=0.25):
            dist = np.linalg.norm(np.array(pt_eye) - np.array(pt_edge))
            
            sigma = dist * ratio

            return max(min_sigma, min(sigma, max_sigma))

        # === Left Side ===
        if poly_left is not None:
            cv2.fillPoly(mask_L, [poly_left], 1.0)
            
            if clip_mask is not None:
                clipper = clip_mask[:,:,0] if len(clip_mask.shape) == 3 else clip_mask
                mask_L = mask_L * clipper
            
            sigma_L = get_dynamic_sigma(points[36], points[0])
            
            if sigma_L > 0:
                k_size = int(sigma_L * 3) | 1
                mask_L = cv2.GaussianBlur(mask_L, (k_size, k_size), sigma_L)

        # === Right Side ===
        if poly_right is not None:
            cv2.fillPoly(mask_R, [poly_right], 1.0)
            
            if clip_mask is not None:
                clipper = clip_mask[:,:,0] if len(clip_mask.shape) == 3 else clip_mask
                mask_R = mask_R * clipper
            
            sigma_R = get_dynamic_sigma(points[45], points[16])
            
            if sigma_R > 0:
                k_size = int(sigma_R * 3) | 1
                mask_R = cv2.GaussianBlur(mask_R, (k_size, k_size), sigma_R)
        
        mask = np.maximum(mask_L, mask_R)
        return mask
    
    def _create_forehead_intensity_mask(self, points, shape):
        """
        Creates a soft mask specifically for the forehead area to adjust texture intensity.
        Returns: 1.0 on forehead, 0.0 on lower face.
        """
        h, w = shape[:2]
        mask = np.zeros((h, w), dtype=np.float32)
        
        p0 = np.array(points[0])   # 左鬢角
        p16 = np.array(points[16]) # 右鬢角
        
        vec_x = p16[0] - p0[0]
        vec_y = p16[1] - p0[1]
        face_width = np.hypot(vec_x, vec_y)
        if face_width == 0: face_width = 1.0
        
        dir_up_x = vec_y / face_width
        dir_up_y = -vec_x / face_width
        
        dir_right_x = vec_x / face_width
        dir_right_y = vec_y / face_width
        
        ext_h = face_width * 2.0
        ext_w = face_width * 1.0
        
        p16_far = p16 + np.array([dir_right_x, dir_right_y]) * ext_w
        p0_far = p0 - np.array([dir_right_x, dir_right_y]) * ext_w
        p16_sky = p16_far + np.array([dir_up_x, dir_up_y]) * ext_h
        p0_sky = p0_far + np.array([dir_up_x, dir_up_y]) * ext_h
        
        brows_right = [points[i] for i in range(26, 21, -1)]
        brows_left = [points[i] for i in range(21, 16, -1)]
        
        poly = np.array([
            p16_sky, p16_far, p16,
            *brows_right, *brows_left,
            p0, p0_far, p0_sky
        ], dtype=np.int32)
        cv2.fillPoly(mask, [poly], 1.0)
        
        mask = cv2.GaussianBlur(mask, (101, 101), 50)
        
        return mask

    def _suppress_halos_by_gradient(self, texture, src_img, sensitivity=0.5, gain=5.0):
        """
        Suppresses texture in areas with high structural gradients (like shadow edges)
        using a soft, continuous curve instead of a hard binary mask.
        
        Args:
            texture: The extracted texture (3-channel).
            src_img: The original crop image (used to detect edges).
            sensitivity: Controls how much gradient triggers suppression (Lower = more sensitive).
            gain: Controls how hard the suppression is (Higher = stronger reduction).
        """
        gray = cv2.cvtColor(src_img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        blurred_src = cv2.GaussianBlur(gray, (0, 0), 2.0)
        
        grad_x = cv2.Sobel(blurred_src, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(blurred_src, cv2.CV_32F, 0, 1, ksize=3)
        
        magnitude = cv2.magnitude(grad_x, grad_y)
        magnitude_norm = magnitude / 100.0
        
        weight_map = 1.0 / (1.0 + np.power(magnitude_norm / sensitivity, gain))
        
        kernel_size = 9 
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        weight_map = cv2.erode(weight_map, kernel, iterations=1)
        
        weight_map = cv2.GaussianBlur(weight_map, (5, 5), 0)
        weight_map_3c = cv2.merge([weight_map, weight_map, weight_map])
        texture_suppressed = texture * weight_map_3c
        
        return texture_suppressed

    def _select_best_pair(self, candidates_a, candidates_b, img_w, img_h):
        """
        Selects the best pair of faces (rect_a, rect_b) based on:
        1. Overlap (IoU) - Primary factor (Must be the same face position)
        2. Size (Area) - Secondary factor (Prefer larger faces)
        3. Centricity - Tertiary factor (Prefer centered faces)
        """
        if not candidates_a or not candidates_b:
            return None, None

        best_pair = (None, None)
        best_score = -1.0
        
        img_cx, img_cy = img_w / 2.0, img_h / 2.0
        img_diag = math.sqrt(img_w**2 + img_h**2)

        # Helper: 計算 IoU
        def get_iou(r1, r2):
            # r = [x, y, w, h]
            x1 = max(r1[0], r2[0])
            y1 = max(r1[1], r2[1])
            x2 = min(r1[0]+r1[2], r2[0]+r2[2])
            y2 = min(r1[1]+r1[3], r2[1]+r2[3])
            
            inter_w = max(0, x2 - x1)
            inter_h = max(0, y2 - y1)
            inter_area = inter_w * inter_h
            
            area1 = r1[2] * r1[3]
            area2 = r2[2] * r2[3]
            union_area = area1 + area2 - inter_area
            
            if union_area <= 0: return 0
            return inter_area / union_area
        
        for rect_a in candidates_a[:3]:
            for rect_b in candidates_b[:3]:
                
                iou_val = get_iou(rect_a, rect_b)
                
                if iou_val < 0.3: 
                    continue
                
                avg_area = (rect_a[2] * rect_a[3] + rect_b[2] * rect_b[3]) / 2.0
                size_score = math.sqrt(avg_area) / math.sqrt(img_w * img_h)
                
                cx_a = rect_a[0] + rect_a[2] / 2
                cy_a = rect_a[1] + rect_a[3] / 2
                cx_b = rect_b[0] + rect_b[2] / 2
                cy_b = rect_b[1] + rect_b[3] / 2
                
                avg_cx = (cx_a + cx_b) / 2
                avg_cy = (cy_a + cy_b) / 2
                
                dist_to_center = math.sqrt((avg_cx - img_cx)**2 + (avg_cy - img_cy)**2)
                center_score = 1.0 - (dist_to_center / (img_diag / 2))
                
                # === 綜合評分 ===
                # 權重設定：
                # IoU 最重要 (確保是同一個人)
                # Size 次之 (確保是主角)
                # Center 用來當 Size 差不多時的決勝點
                total_score = (iou_val * 2.0) + (size_score * 1.5) + (center_score * 0.5)
                
                if total_score > best_score:
                    best_score = total_score
                    best_pair = (rect_a, rect_b)
        
        if best_pair[0] is None:
            print("[Best Match] No matching pair found (IoU too low). Falling back to largest faces.")
            return candidates_a[0], candidates_b[0]
            
        return best_pair
    
    def get_paired_face_rects(self, img_a, img_b):
        """
        Stage 1: Comparison & Selection.
        Detects faces in both images and selects the best corresponding pair based on heuristics.
        Returns: (rect_a, rect_b)
        """
        if self.is_pre_aligned:
            h_a, w_a = img_a.shape[:2]
            h_b, w_b = img_b.shape[:2]
            return [0, 0, w_a, h_a], [0, 0, w_b, h_b]
        
        rect_a_large, full_detect_a = self.pipeline.detect(img_a, bypass=False)
        rect_b_large, full_detect_b = self.pipeline.detect(img_b, bypass=False)

        rect_a, rect_b = self._select_best_pair(
            full_detect_a, full_detect_b, img_a.shape[1], img_a.shape[0]
        )

        if rect_a is None or rect_b is None:
            rect_a, rect_b = rect_a_large, rect_b_large # fallback to largest faces
        
        return rect_a, rect_b
    
    def analyze_face(self, img, rect=None, target_size=1024, padding=1.4):
        """
        Pre-process stage.
        Detects, Crops, Aligns, Segment, and calculates landmarks.
        Returns a dictionary containing all necessary data for restoration.
        """
        # 2. Crop Matrix
        M = self._get_crop_matrix(rect, img.shape[1], img.shape[0], target_size, padding)
        
        # 3. Crop Image (Align)
        crop_img = cv2.warpAffine(img, M, (target_size, target_size))
        
        try:
            # 4. Landmarks
            pts, pts_84 = self.get_landmarks(crop_img)
            
            # 5. Aux Points (Forehead & Cheek)
            aux_forehead = self._get_forehead_points(np.array(pts), crop_img.shape)
            aux_cheek = self._get_cheek_points(pts)
            
            # 6. Segmentation
            seg_mask = self.pipeline.segment(crop_img, pts_84) # Can be None
            
            return {
                "success": True,
                "crop_img": crop_img, # OpenCV BGR
                "M": M,
                "pts": pts,
                "pts_84": pts_84,
                "seg_mask": seg_mask, # (H,W,3) 0-1 float or similar
                "aux_forehead": aux_forehead,
                "aux_cheek": aux_cheek
            }
        except Exception as e:
            if DEBUG: print(f"Analysis failed: {e}")
            return {"success": False}

    def restore_face_from_data(self, 
                               img_a_crop, mask_a, data_a, 
                               img_b_crop, mask_b, data_b, 
                               full_img_b, mode="add"):
        """
        Post-process stage.
        Takes explicitly provided images/masks and metadata.
        """
        if not data_a.get("success") or not data_b.get("success"):
            return full_img_b

        # Unpack Data B (Target)
        pts_b = data_b["pts"]
        M_b = data_b["M"]
        pts_b_extended = pts_b + data_b["aux_forehead"] + data_b["aux_cheek"]
        seg_mask_b = mask_b if mask_b is not None else data_b.get("seg_mask")

        # Unpack Data A (Reference)
        pts_a = data_a["pts"]
        pts_a_extended = pts_a + data_a["aux_forehead"] + data_a["aux_cheek"]
        seg_mask_a = mask_a if mask_a is not None else data_a.get("seg_mask")

        # Ensure masks are valid for calculations (numpy array)
        if seg_mask_a is None:
             seg_mask_a = np.zeros(img_a_crop.shape, dtype=np.float32)
        if seg_mask_b is None:
             seg_mask_b = np.zeros(img_b_crop.shape, dtype=np.float32)

        # === Step 2: Texture Extraction ===
        texture_a = self.extract_texture(img_a_crop)
        
        texture_a = self._suppress_halos_by_gradient(
            texture_a, 
            img_a_crop, 
            sensitivity=0.4, 
            gain=4.0
        )
        
        # === Step 3: Mask Creation & Cleaning ===
        if mode == "replace":
            final_mask_a = self.create_blending_mask(
                pts_a, 
                img_a_crop.shape,
                seg_mask=seg_mask_a, 
                shrink_override=10, 
                blur_override=10, 
                fade_power=1.0 
            )
        else: # "add" mode & "eyes_replacement" mode
            mask_a_strict = self.create_blending_mask(
                pts_a, 
                img_a_crop.shape,
                seg_mask=seg_mask_a, 
                shrink_override=10, 
                blur_override=10, 
                fade_power=1.0,
                exclude_features=True,
                seg_erosion=45, 
            )
            
            mask_a_broad = self.create_blending_mask(
                pts_a, img_a_crop.shape, seg_mask=seg_mask_a, 
                shrink_override=5, blur_override=10, fade_power=1.0,
                exclude_features=False, 
                seg_erosion=45, 
            )

            raw_mask_a = (mask_a_broad * 0.4) + (mask_a_strict * 0.6)
            final_mask_a = np.clip(raw_mask_a, 0, 1.0)

        texture_a = texture_a * final_mask_a
        texture_a = self._suppress_mouth_corner_shadows(texture_a, pts_a, img_a_crop.shape)

        # === Step 4: Texture Warping & Mesh Generation ===
        warped_texture, tri_indices = self.generate_warped_texture(
            texture_a, pts_a_extended, pts_b_extended, img_b_crop.shape
        )

        # === Step 5: Blending Mask Generation ===
        mask_b_strict = self.create_blending_mask(
            pts_b, 
            img_b_crop.shape,
            seg_mask=seg_mask_b,
            shrink_override=self.mask_shrink_size,
            blur_override=40,
            fade_power=1.5,
            exclude_features=True
        )

        if mode == "replace":
            mask_b = mask_b_strict
        else: 
            mask_b_broad = self.create_blending_mask(
                pts_b, img_b_crop.shape, seg_mask=seg_mask_b,
                shrink_override=0, blur_override=40, fade_power=1.5,
                exclude_features=False
            )
            
            mask_b = (mask_b_broad * 0.4) + (mask_b_strict * 0.6)
            mask_b = np.clip(mask_b, 0, 1.0)

        # === LAB Blending ===
        img_b_lab = cv2.cvtColor(img_b_crop, cv2.COLOR_BGR2LAB).astype(np.float32)
        l, a, b = cv2.split(img_b_lab)

        target_smoothness_map = self._create_target_smoothness_map(
            l, 
            sensitivity=0.3, 
            min_strength=0.2, 
            max_strength=1.0
        )
        
        luminance_safe_map = self._calculate_luminance_protection(l, threshold_low=20, threshold_high=230)
        
        # 1. Base from B
        l_blurred = cv2.GaussianBlur(l, (0, 0), self.sigma)
        
        # 2. Texture from A
        texture_a_warped = warped_texture[:, :, 0]
        
        # 3. Blend
        mask_single = mask_b[:, :, 0]
        
        if mode == "replace":
            texture_boost = 1.0
            l_replaced = l_blurred + (texture_a_warped * self.detail_strength * texture_boost)
            l_final = l * (1.0 - mask_single) + l_replaced * mask_single
        elif mode == "eyes_replacement":
            mask_eye_zone = self._create_eye_surround_mask(pts_b, img_b_crop.shape)
            l_blurred = cv2.GaussianBlur(l, (0, 0), self.sigma)
            texture_b = l - l_blurred

            # add extra forehead intensity mask
            mask_forehead_zone = self._create_forehead_intensity_mask(pts_b, img_b_crop.shape)
            strength_map = self.detail_strength * (1.0 - mask_forehead_zone) + \
                       self.forehead_strength * mask_forehead_zone
            
            KEEP_B_CHEEK = 1.0
            KEEP_B_EYE   = 0.0
            target_keep_b_map = KEEP_B_CHEEK * (1.0 - mask_eye_zone) + KEEP_B_EYE * mask_eye_zone
            ratio_keep_b = 1.0 - (mask_single * (1.0 - target_keep_b_map))

            ADD_A_CHEEK = 1.0
            ADD_A_EYE   = 1.0
            
            target_add_a_map = ADD_A_CHEEK * (1.0 - mask_eye_zone) + ADD_A_EYE * mask_eye_zone
            ratio_add_a = mask_single * target_add_a_map * strength_map * target_smoothness_map * luminance_safe_map
            
            l_final = l_blurred + \
                    (texture_b * ratio_keep_b) + \
                    (texture_a_warped * ratio_add_a)
        else:
            texture_b = l - l_blurred
            RATIO_A = 1.0 
            RATIO_B = 0.0 
            
            ratio_keep_b = 1.0 - (mask_single * RATIO_B) 
            ratio_add_a = mask_single * RATIO_A
            
            l_final = l_blurred + \
                      (texture_b * ratio_keep_b) + \
                      (texture_a_warped * self.detail_strength * ratio_add_a)
        
        l_final = np.clip(l_final, 0, 255)
        a_final = np.clip(a, 0, 255)
        b_final = np.clip(b, 0, 255)
        
        img_b_lab_new = cv2.merge([l_final, a_final, b_final])
        final_crop = cv2.cvtColor(img_b_lab_new.astype(np.uint8), cv2.COLOR_LAB2BGR)
        
        final_img = self._paste_back(full_img_b, final_crop, mask_b, M_b)

        return final_img

    def restore_core(self, img_a, img_b, mode="add", output_folder=None):
        if img_a is None or img_b is None:
            if DEBUG: print("Error: Could not read images.")
            return

        rect_a, rect_b = self.get_paired_face_rects(img_a, img_b)

        # 1. Analyze A
        data_a = self.analyze_face(img_a, rect=rect_a)
        if not data_a["success"]:
            print("Face A not found")
            return img_b
        
        # 2. Analyze B
        data_b = self.analyze_face(img_b, rect=rect_b)
        if not data_b["success"]:
            print("Face B not found")
            return img_b
        
        # 3. Restore
        # Legacy support: use the data in the dict directly
        return self.restore_face_from_data(
            data_a["crop_img"], data_a["seg_mask"], data_a,
            data_b["crop_img"], data_b["seg_mask"], data_b,
            img_b, mode=mode
        )
    
    def restore_core_addon(self, img_a, img_b, output_folder=None):
        return self.restore_core(img_a, img_b, mode="add", output_folder=output_folder)

    def restore_core_replacement(self, img_a, img_b, output_folder=None):
        return self.restore_core(img_a, img_b, mode="replace", output_folder=output_folder)