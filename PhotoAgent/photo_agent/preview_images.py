"""Stage 2.0 visual references; never mutate detector or formal crop records."""
import io
import math

from PIL import Image, ImageDraw, ImageFont

POLICY = 'candidate_visual_identity_v1'
FULL_MAX_SIDE = 1024
CROP_MAX_SIDE = 448
HEADER_HEIGHT = 36


def _font(size):
    try:
        return ImageFont.truetype('DejaVuSans-Bold.ttf', size)
    except OSError:
        return ImageFont.load_default()


def _jpeg(image):
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=90)
    return out.getvalue()


def _bounds(box, width, height):
    if (set(box) != {'x', 'y', 'w', 'h'} or
            any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box.values()) or
            min(box['x'], box['y']) < 0 or min(box['w'], box['h']) <= 0 or
            box['x'] + box['w'] > 1.000001 or box['y'] + box['h'] > 1.000001):
        raise ValueError('invalid_visual_reference_box')
    return (box['x'] * width, box['y'] * height,
            (box['x'] + box['w']) * width, (box['y'] + box['h']) * height)


def _overlap(a, b):
    return max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))


def prepare_identity_images(image_bytes, crops, candidates):
    """Derive all ID-marked targets from original EXIF-normalized geometry.

    Existing bytes/sent_to_model flags retain their Stage 2.1 crop semantics.
    No detector runs here. References are keyed by the exact schema slots.
    """
    import config
    from image_tools import _open_rgb
    if (len(crops) > config.SUBJECT_PREVIEW_MAX_DETECTIONS or
            len(candidates) > config.SUBJECT_PREVIEW_MAX_LOW_CONFIDENCE_CANDIDATES):
        raise ValueError('visual_reference_budget_exceeded')
    source = _open_rgb(image_bytes)
    full = source.copy()
    full.thumbnail((FULL_MAX_SIDE, FULL_MAX_SIDE), Image.Resampling.LANCZOS)
    entries = [('A'+str(i), str(i), c) for i, c in enumerate(crops, 1)]
    entries += [(c['candidate_id'].upper(), c['candidate_id'], c) for c in candidates]
    face_bounds = [_bounds(c['face_box'], *full.size) for _, _, c in entries]
    draw = ImageDraw.Draw(full)
    font = _font(20)
    placed = []
    targets = {}
    for (display_id, slot, candidate), face in zip(entries, face_bounds):
        color = '#00FFFF' if display_id.startswith('A') else '#FFFF00'
        draw.rectangle(face, outline=color, width=2)
        label_bounds = draw.textbbox((0, 0), display_id, font=font)
        w, h = label_bounds[2]-label_bounds[0]+10, label_bounds[3]-label_bounds[1]+8
        proposals = []
        for x, y in [(face[0], face[1]-h-3), (face[0], face[3]+3),
                     (face[0]-w-3, face[1]), (face[2]+3, face[1])]:
            x, y = max(0, min(x, full.width-w)), max(0, min(y, full.height-h))
            proposals.append((x, y, x+w, y+h))
        label = min(proposals, key=lambda b: sum(_overlap(b, f) for f in face_bounds) +
                    2*sum(_overlap(b, p) for p in placed))
        placed.append(label)
        draw.line((label[0]+w/2, label[1]+h/2, face[0], face[1]), fill=color, width=1)
        draw.rectangle(label, fill='black', outline=color)
        draw.text((label[0]+5-label_bounds[0], label[1]+4-label_bounds[1]), display_id, fill=color, font=font)

        padded = _bounds(candidate['box'], *source.size)
        bounds = tuple(round(v) for v in padded)
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise ValueError('empty_visual_reference_crop')
        crop = source.crop(bounds)
        scale = CROP_MAX_SIDE / max(crop.size)
        crop = crop.resize((max(1, round(crop.width*scale)), max(1, round(crop.height*scale))), Image.Resampling.LANCZOS)
        tile = Image.new('RGB', (max(crop.width, 180), crop.height+HEADER_HEIGHT), 'black')
        tile.paste(crop, (0, HEADER_HEIGHT))
        td = ImageDraw.Draw(tile)
        td.text((8, 6), display_id+' TARGET FACE', fill=color, font=_font(18))
        tight = _bounds(candidate['face_box'], *source.size)
        target = ((tight[0]-bounds[0])*crop.width/(bounds[2]-bounds[0]),
                  (tight[1]-bounds[1])*crop.height/(bounds[3]-bounds[1])+HEADER_HEIGHT,
                  (tight[2]-bounds[0])*crop.width/(bounds[2]-bounds[0]),
                  (tight[3]-bounds[1])*crop.height/(bounds[3]-bounds[1])+HEADER_HEIGHT)
        td.rectangle(target, outline=color, width=3)
        targets[display_id] = {'slot': slot, 'bytes': _jpeg(tile)}
    return {'policy': POLICY, 'marked_full_bytes': _jpeg(full), 'targets': targets}
