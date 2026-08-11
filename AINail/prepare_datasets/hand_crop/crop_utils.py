"""
Plain PIL port of the square-crop algorithm in
custom_nodes/ComfyUI_AINail_utils/nodes/crop_square_by_boxes.py, so it can run
outside ComfyUI/torch. Method names and behavior mirror that node 1:1.
"""
from PIL import Image, ImageDraw


def pad_to_square(pil_image, output_size=None):
    """Pad an image to a square with centered black borders (port of
    custom_nodes/ComfyUI_AINail_utils/nodes/pad_to_square.py), optionally resizing the
    result to (output_size, output_size)."""
    W, H = pil_image.size
    size = max(W, H)
    offset_x = (size - W) // 2
    offset_y = (size - H) // 2

    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(pil_image, (offset_x, offset_y))

    if output_size is not None and size != output_size:
        canvas = canvas.resize((output_size, output_size), Image.LANCZOS)

    return canvas


def _avoid_overlap_position(cx, cy, x1, y1, x2, y2, size, all_boxes, box_idx):
    rx1 = int(round(cx - size / 2))
    ry1 = int(round(cy - size / 2))
    rx2 = rx1 + size
    ry2 = ry1 + size

    ry1_lo, ry1_hi = y2 - size, y1
    rx1_lo, rx1_hi = x2 - size, x1

    for j, other in enumerate(all_boxes):
        if j == box_idx:
            continue
        ox1, oy1, ox2, oy2 = int(other[0]), int(other[1]), int(other[2]), int(other[3])

        if rx1 < ox2 and rx2 > ox1:
            if oy2 <= y1:
                mid_y = (oy2 + y1) // 2
                ry1_lo = max(ry1_lo, mid_y)
            elif oy1 >= y2:
                mid_y = (y2 + oy1) // 2
                ry1_hi = min(ry1_hi, mid_y - size)

        if ry1 < oy2 and ry2 > oy1:
            if ox2 <= x1:
                mid_x = (ox2 + x1) // 2
                rx1_lo = max(rx1_lo, mid_x)
            elif ox1 >= x2:
                mid_x = (x2 + ox1) // 2
                rx1_hi = min(rx1_hi, mid_x - size)

    if ry1_lo <= ry1_hi:
        ry1 = int(max(ry1_lo, min(ry1_hi, ry1)))
    if rx1_lo <= rx1_hi:
        rx1 = int(max(rx1_lo, min(rx1_hi, rx1)))

    return rx1, ry1


def _rects_overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _merge_overlapping_boxes(boxes, rects):
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
            if _rects_overlap(rect_acc, rects[j]):
                acc = [
                    min(acc[0], boxes[j][0]), min(acc[1], boxes[j][1]),
                    max(acc[2], boxes[j][2]), max(acc[3], boxes[j][3]),
                ]
                used[j] = True
                changed = True
        new_boxes.append(acc)
    return new_boxes, changed


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


def _center_if_oversized(rx1, ry1, size, W, H):
    """When the square is larger than the image in a given dimension, padding on that
    axis is unavoidable -- center it (split evenly on both sides) rather than pinning
    the patch to one edge and dumping all the black padding on the other."""
    if size > W:
        rx1 = (W - size) // 2
    if size > H:
        ry1 = (H - size) // 2
    return rx1, ry1


def compute_crop_info(boxes, img_w, img_h, scale, min_area_ratio,
                       avoid_bbox_overlap=False, merge_overlapping_squares=True,
                       shift_to_avoid_padding=True):
    """boxes: list of [x1,y1,x2,y2] in pixel coords. Returns list of crop_info dicts,
    same fields as CropSquareByBoxes: src_x1/y1/x2/y2, pad_left, pad_top, size, orig_w, orig_h."""
    if not boxes:
        return []

    W, H = img_w, img_h
    image_area = H * W

    valid_boxes = [b for b in boxes
                   if (b[2] - b[0]) * (b[3] - b[1]) >= image_area * min_area_ratio]

    if merge_overlapping_squares and valid_boxes:
        changed = True
        while changed:
            eff_rects = []
            for idx, b in enumerate(valid_boxes):
                bx1, by1, bx2, by2 = b
                bcx, bcy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
                bsize = int(round(max(bx2 - bx1, by2 - by1) * scale))
                if avoid_bbox_overlap:
                    brx1, bry1 = _avoid_overlap_position(
                        bcx, bcy, int(bx1), int(by1), int(bx2), int(by2),
                        bsize, valid_boxes, idx,
                    )
                else:
                    brx1 = int(round(bcx - bsize / 2))
                    bry1 = int(round(bcy - bsize / 2))
                if shift_to_avoid_padding:
                    brx1, bry1 = _shift_to_avoid_padding(brx1, bry1, bsize, W, H)
                brx1, bry1 = _center_if_oversized(brx1, bry1, bsize, W, H)
                eff_rects.append((brx1, bry1, brx1 + bsize, bry1 + bsize))
            valid_boxes, changed = _merge_overlapping_boxes(valid_boxes, eff_rects)

    info = []
    for i, box in enumerate(valid_boxes):
        x1, y1, x2, y2 = box

        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        half = max((x2 - x1), (y2 - y1)) / 2.0 * scale
        size = int(round(half * 2))

        if avoid_bbox_overlap:
            rx1, ry1 = _avoid_overlap_position(
                cx, cy, int(x1), int(y1), int(x2), int(y2),
                size, valid_boxes, i,
            )
        else:
            rx1 = int(round(cx - size / 2))
            ry1 = int(round(cy - size / 2))
        rx2 = rx1 + size
        ry2 = ry1 + size

        if shift_to_avoid_padding:
            rx1, ry1 = _shift_to_avoid_padding(rx1, ry1, size, W, H)
            rx2 = rx1 + size
            ry2 = ry1 + size

        rx1, ry1 = _center_if_oversized(rx1, ry1, size, W, H)
        rx2 = rx1 + size
        ry2 = ry1 + size

        sx1 = max(0, rx1)
        sy1 = max(0, ry1)
        sx2 = min(W, rx2)
        sy2 = min(H, ry2)

        pad_left = sx1 - rx1
        pad_top = sy1 - ry1

        info.append({
            "src_x1": sx1, "src_y1": sy1,
            "src_x2": sx2, "src_y2": sy2,
            "pad_left": pad_left, "pad_top": pad_top,
            "size": size,
            "orig_w": W, "orig_h": H,
        })

    return info


def apply_crop(pil_image, entry, output_size=None):
    """Build the black square canvas and paste the source patch, per one crop_info entry.
    Resizes pil_image to entry's orig_w/orig_h first if dimensions differ but aspect ratio
    matches (mirrors SyncCrop's behavior so control_1 can be a different resolution than
    the target image the boxes were detected on). The canvas is always square; if
    output_size is given, it's resized to (output_size, output_size) before returning."""
    W, H = pil_image.size
    orig_w, orig_h = entry["orig_w"], entry["orig_h"]

    if W != orig_w or H != orig_h:
        tol = 1e-3
        if abs(W / H - orig_w / orig_h) > tol:
            raise ValueError(
                f"Aspect ratio mismatch: image is {W}x{H} ({W/H:.4f}) but crop_info "
                f"expects {orig_w}x{orig_h} ({orig_w/orig_h:.4f})"
            )
        pil_image = pil_image.resize((orig_w, orig_h), Image.LANCZOS)

    size = entry["size"]
    src_x1, src_y1, src_x2, src_y2 = entry["src_x1"], entry["src_y1"], entry["src_x2"], entry["src_y2"]
    pad_left, pad_top = entry["pad_left"], entry["pad_top"]

    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    patch = pil_image.crop((src_x1, src_y1, src_x2, src_y2))
    canvas.paste(patch, (pad_left, pad_top))

    if output_size is not None and (size != output_size):
        canvas = canvas.resize((output_size, output_size), Image.LANCZOS)

    return canvas


def compute_content_rect(entry, output_size=None):
    """Return (x1, y1, x2, y2) of the real (non-black-padding) content area within
    apply_crop's output, in that output's own pixel coordinates -- i.e. scaled to
    output_size if apply_crop was called with one."""
    size = entry["size"]
    content_w = entry["src_x2"] - entry["src_x1"]
    content_h = entry["src_y2"] - entry["src_y1"]
    x1, y1 = entry["pad_left"], entry["pad_top"]
    x2, y2 = x1 + content_w, y1 + content_h

    if output_size is not None and output_size != size:
        s = output_size / size
        x1, y1, x2, y2 = round(x1 * s), round(y1 * s), round(x2 * s), round(y2 * s)

    return x1, y1, x2, y2


def restore_padding(original, inpainted, entry, output_size=None):
    """Paste original's black-padding pixels back over an inpainted version of the same
    apply_crop output, everywhere outside the content rect from compute_content_rect.
    Restores those pixels exactly (rather than re-blackening), since a resized canvas's
    content/padding boundary is anti-aliased, not a hard edge. Both images must be the
    same size (apply_crop's output for the same entry/output_size)."""
    if original.size != inpainted.size:
        raise ValueError(f"Size mismatch: original is {original.size}, inpainted is {inpainted.size}")

    x1, y1, x2, y2 = compute_content_rect(entry, output_size)

    mask = Image.new("L", inpainted.size, 255)
    ImageDraw.Draw(mask).rectangle([x1, y1, x2 - 1, y2 - 1], fill=0)

    return Image.composite(original, inpainted, mask)
