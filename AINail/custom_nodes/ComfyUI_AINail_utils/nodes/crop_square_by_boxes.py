import json
import torch


class CropSquareByBoxes:
    """Crop square regions from an image using SAM3 boxes output.
    Expands each box to a square and pads with black where the region exceeds image bounds.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "boxes": ("STRING", {"forceInput": True}),
                "scale": ("FLOAT", {"default": 1.2, "min": 1.0, "max": 10.0, "step": 0.05}),
                "min_area_ratio": ("FLOAT", {"default": 0.005, "min": 0.0, "max": 1.0, "step": 0.001}),
                "avoid_bbox_overlap": ("BOOLEAN", {"default": False}),
                "merge_overlapping_squares": ("BOOLEAN", {"default": True}),
                "shift_to_avoid_padding": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("crops", "crop_info")
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "crop"
    CATEGORY = "AINail/image"

    @staticmethod
    def _avoid_overlap_position(cx, cy, x1, y1, x2, y2, size, all_boxes, box_idx):
        """
        Shift the square's top-left so padding doesn't intrude into neighboring bboxes.
        The square must still fully contain the original bbox [x1,y1,x2,y2].
        Excess padding is moved to the opposite side.
        """
        rx1 = int(round(cx - size / 2))
        ry1 = int(round(cy - size / 2))
        rx2 = rx1 + size
        ry2 = ry1 + size

        # Valid range for ry1 / rx1 so the square still covers the original box
        ry1_lo, ry1_hi = y2 - size, y1
        rx1_lo, rx1_hi = x2 - size, x1

        for j, other in enumerate(all_boxes):
            if j == box_idx:
                continue
            ox1, oy1, ox2, oy2 = int(other[0]), int(other[1]), int(other[2]), int(other[3])

            # Y-axis: tighten only when the two squares share X extent.
            # Use the midpoint of the gap so each box claims only its half.
            if rx1 < ox2 and rx2 > ox1:
                if oy2 <= y1:          # other box is strictly above current box
                    mid_y = (oy2 + y1) // 2
                    ry1_lo = max(ry1_lo, mid_y)
                elif oy1 >= y2:        # other box is strictly below current box
                    mid_y = (y2 + oy1) // 2
                    ry1_hi = min(ry1_hi, mid_y - size)

            # X-axis: tighten only when the two squares share Y extent.
            # Use the midpoint of the gap so each box claims only its half.
            if ry1 < oy2 and ry2 > oy1:
                if ox2 <= x1:          # other box is strictly to the left
                    mid_x = (ox2 + x1) // 2
                    rx1_lo = max(rx1_lo, mid_x)
                elif ox1 >= x2:        # other box is strictly to the right
                    mid_x = (x2 + ox1) // 2
                    rx1_hi = min(rx1_hi, mid_x - size)

        # If constraints are infeasible (size too large for the gap), keep centered
        if ry1_lo <= ry1_hi:
            ry1 = int(max(ry1_lo, min(ry1_hi, ry1)))
        if rx1_lo <= rx1_hi:
            rx1 = int(max(rx1_lo, min(rx1_hi, rx1)))

        return rx1, ry1

    @staticmethod
    def _rects_overlap(a, b):
        return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]

    @classmethod
    def _merge_overlapping_boxes(cls, boxes, rects):
        """
        One pass: merge original bboxes whose pre-computed effective rects overlap.
        Returns (new_boxes, changed).
        """
        used = [False] * len(boxes)
        new_boxes = []
        changed = False
        for i in range(len(boxes)):
            if used[i]:
                continue
            acc = list(boxes[i])
            rect_acc = rects[i]
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                if cls._rects_overlap(rect_acc, rects[j]):
                    acc = [
                        min(acc[0], boxes[j][0]), min(acc[1], boxes[j][1]),
                        max(acc[2], boxes[j][2]), max(acc[3], boxes[j][3]),
                    ]
                    used[j] = True
                    changed = True
            new_boxes.append(acc)
        return new_boxes, changed

    @staticmethod
    def _shift_to_avoid_padding(rx1, ry1, size, W, H):
        rx2 = rx1 + size
        ry2 = ry1 + size
        if rx1 < 0:
            rx1 = 0
        elif rx2 > W:
            rx1 = max(0, W - size)
        if ry1 < 0:
            ry1 = 0
        elif ry2 > H:
            ry1 = max(0, H - size)
        return rx1, ry1

    def crop(self, image: torch.Tensor, boxes: str, scale: float, min_area_ratio: float,
             avoid_bbox_overlap: bool = False, merge_overlapping_squares: bool = True,
             shift_to_avoid_padding: bool = True):
        # image: [B, H, W, C]
        boxes_list = json.loads(boxes)
        if not boxes_list:
            raise ValueError("NO_HAND_DETECTED")

        _, H, W, C = image.shape
        crops = []
        info = []

        image_area = H * W

        # Filter by area first, then optionally merge boxes whose padded squares overlap.
        # Two-phase loop: Phase 1 computes every box's fully-shifted rect (reflecting the
        # complete neighbour picture), Phase 2 merges pairs that still overlap after shifting.
        valid_boxes = [b for b in boxes_list
                       if (b[2] - b[0]) * (b[3] - b[1]) >= image_area * min_area_ratio]
        if merge_overlapping_squares and valid_boxes:
            changed = True
            while changed:
                # Phase 1: compute each box's effective padded rect with the full current list
                eff_rects = []
                for idx, b in enumerate(valid_boxes):
                    bx1, by1, bx2, by2 = b
                    bcx, bcy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
                    bsize = int(round(max(bx2 - bx1, by2 - by1) * scale))
                    if avoid_bbox_overlap:
                        brx1, bry1 = self._avoid_overlap_position(
                            bcx, bcy, int(bx1), int(by1), int(bx2), int(by2),
                            bsize, valid_boxes, idx,
                        )
                    else:
                        brx1 = int(round(bcx - bsize / 2))
                        bry1 = int(round(bcy - bsize / 2))
                    if shift_to_avoid_padding:
                        brx1, bry1 = self._shift_to_avoid_padding(brx1, bry1, bsize, W, H)
                    eff_rects.append((brx1, bry1, brx1 + bsize, bry1 + bsize))
                # Phase 2: merge pairs whose effective rects still overlap
                valid_boxes, changed = self._merge_overlapping_boxes(valid_boxes, eff_rects)

        for i, box in enumerate(valid_boxes):
            x1, y1, x2, y2 = box

            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            half = max((x2 - x1), (y2 - y1)) / 2.0 * scale

            # derive size first, then compute top-left from center to avoid rounding mismatch
            size = int(round(half * 2))

            if avoid_bbox_overlap:
                rx1, ry1 = self._avoid_overlap_position(
                    cx, cy, int(x1), int(y1), int(x2), int(y2),
                    size, valid_boxes, i,
                )
            else:
                rx1 = int(round(cx - size / 2))
                ry1 = int(round(cy - size / 2))
            rx2 = rx1 + size
            ry2 = ry1 + size

            # Shift the square into image bounds to avoid black padding.
            # If size > image dimension, padding is unavoidable but minimised.
            if shift_to_avoid_padding:
                rx1, ry1 = self._shift_to_avoid_padding(rx1, ry1, size, W, H)
                rx2 = rx1 + size
                ry2 = ry1 + size

            # clamp to image bounds
            sx1 = max(0, rx1)
            sy1 = max(0, ry1)
            sx2 = min(W, rx2)
            sy2 = min(H, ry2)

            # padding offsets (where the cropped patch sits inside the square canvas)
            pad_left = sx1 - rx1
            pad_top  = sy1 - ry1

            canvas = torch.zeros((image.shape[0], size, size, C),
                                 dtype=image.dtype, device=image.device)
            patch = image[:, sy1:sy2, sx1:sx2, :]
            ph, pw = patch.shape[1], patch.shape[2]
            canvas[:, pad_top:pad_top + ph, pad_left:pad_left + pw, :] = patch

            crops.append(canvas)
            info.append({
                # where the patch was taken from in the original image
                "src_x1": sx1, "src_y1": sy1,
                "src_x2": sx2, "src_y2": sy2,
                # where the patch sits inside the square canvas
                "pad_left": pad_left, "pad_top": pad_top,
                # square canvas size
                "size": size,
                # original image size
                "orig_w": W, "orig_h": H,
            })

        if not crops:
            raise ValueError("NO_HAND_DETECTED")

        return (crops, json.dumps(info))


NODE_CLASS_MAPPINGS = {
    "AINailCropSquareByBoxes": CropSquareByBoxes,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AINailCropSquareByBoxes": "Crop Square by Boxes (AINail)",
}
