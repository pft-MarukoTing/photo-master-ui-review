"""The skin-map model (skinmap_230428-132702.hdf5, WrinkleWorseEmulation's),
run in this process: the face's skin, for strayHair's -o face_source=skinmap.

WrinkleWorseEmulation (Web_SkincareServer's WWE_server.py) runs it on the
face aligned by util_wrinkle.get_transform_info / transform_img, and
process_utils.fast_get_thresholded_skin_map; skin_map does the same:

  1. The photo turned upright about the face into a 960x1280 crop (WWE's
     transformed_size at --target_size 1280): its centre a third of the way
     from the outer eye corners' midpoint to the nose wings', the outer eye
     corners half the crop's width apart, the eyes level. WWE takes these
     from Perfect's 51 landmarks (SB_LEFT_EYE_LEFT/RIGHT,
     SB_RIGHT_EYE_LEFT/RIGHT, SB_NOSE_LEFT/RIGHT); here FAN's 68
     (hairfx/landmarks.py) stand in for them: 36/39, 42/45, 31/35.
  2. The crop, BGR, resized to the model's 96x128 (INTER_AREA) and divided
     by 255; out comes the skin's probability, resized to the crop's size.
  3. That probability warped back onto the photo (0 outside the crop) and
     taken as skin at THRESHOLD and above.

The skin is the face's bare skin: unlike the face gpt-image draws for
strayHair, it leaves out what lies on it (strands across the forehead,
the brows, the eyes) and stops at the hairline, so a mask made from it wants
growing further.

face() makes the whole face of it, as gpt-image draws it, strands and all:

  4. The face FAN's 68 landmarks outline (face_polygon): the jaw (0-16) and
     a forehead over the brows (17-26) raised by _FOREHEAD of the brows'
     height over the nose's bottom (about the face's thirds), less towards
     the brows' ends (_FOREHEAD_TAPER).
  5. The skin kept near that face only (_CLIP_MARGIN of the jaw's width
     round it, up to _FOREHEAD_CLIP over the brows), so the neck, ears,
     hands and shoulders don't widen it; where the skin reaches higher, a
     bare forehead up to a high hairline, it is the skin's.
  6. The convex hull of those two: the holes and gaps the strands across
     the face, the brows and the eyes leave in the skin filled.
"""

import math
import os
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# SKIN_MAP_MODEL sets it; unset, the one in models/.
SKIN_MAP_MODEL = Path(os.environ.get("SKIN_MAP_MODEL")
                      or _ROOT / "models" / "skinmap_230428-132702"
                      / "skinmap_230428-132702.hdf5")
THRESHOLD = 0.5  # fast_get_thresholded_skin_map's skin_map_threshold

# WWE_server.py's transformed_size, (height, width), and the model's input,
# cv2's (width, height).
_CROP = (1280, 960)
_INPUT = (96, 128)

# FAN's 68 landmarks standing in for WWE's 51 (util_wrinkle.get_transform_info,
# util.get_angle_from_shape's eye_index).
_LEFT_EYE = (36, 39)    # SB_LEFT_EYE_LEFT, SB_LEFT_EYE_RIGHT
_RIGHT_EYE = (42, 45)   # SB_RIGHT_EYE_LEFT, SB_RIGHT_EYE_RIGHT
_NOSE = (31, 35)        # SB_NOSE_LEFT, SB_NOSE_RIGHT

# face(): the landmarks' forehead, over the brows, is this share of the
# brows' height over the nose's bottom (the face's thirds: brows to hairline
# about brows to nose), kept under the hairline; at the brows' ends this
# share of it, rounding its top.
_FOREHEAD = 0.8
_FOREHEAD_TAPER = 0.6
# face(): the skin is kept within this share of the jaw's width round the
# landmarks' face, and up to this share of the brows' height over the nose
# above the brows.
_CLIP_MARGIN = 0.08
_FOREHEAD_CLIP = 1.6

_MODELS: dict = {}
_LOCK = threading.Lock()


def _load(path: Path):
    """The Keras 2 model at `path`, loaded once (hairseg._load's way: tf_keras
    where it is installed)."""
    key = str(Path(path).resolve())
    with _LOCK:
        if key not in _MODELS:
            if not Path(key).is_file():
                raise RuntimeError(f"the skin-map model isn't there: {key} "
                                   "(set SKIN_MAP_MODEL)")
            os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
            try:
                import tf_keras as keras
            except ImportError:
                from tensorflow import keras
            _MODELS[key] = keras.models.load_model(key, compile=False)
        return _MODELS[key]


def preload(path=None) -> None:
    """Load the model now (server start), so a missing or broken model fails
    there rather than in the first task."""
    _load(Path(path or SKIN_MAP_MODEL))


def aligned_matrix(points):
    """The 2x3 affine matrix (cv2's) that takes the photo into WWE's aligned
    crop: util_wrinkle.MakeCVForwardTransformMatrix of get_transform_info's
    Transform, from FAN's 68 `points` (x, y)."""
    import numpy as np

    p = np.asarray(points, np.float64)
    outer_left, outer_right = p[_LEFT_EYE[0]], p[_RIGHT_EYE[1]]
    scale = float(np.linalg.norm(outer_right - outer_left))
    eye_centre = (outer_left + outer_right) / 2
    nose_centre = (p[_NOSE[0]] + p[_NOSE[1]]) / 2
    centre = (eye_centre * 2 + nose_centre) / 3
    left, right = p[list(_LEFT_EYE)].mean(0), p[list(_RIGHT_EYE)].mean(0)
    angle = -math.atan2(right[1] - left[1], right[0] - left[0])

    height, width = _CROP
    post = min(width, height) / 2
    cos, sin = math.cos(angle), math.sin(angle)
    # post * R(angle) * (x - centre) / scale + (width / 2, height / 2)
    a = post / scale * np.array([[cos, -sin], [sin, cos]])
    offset = np.array([width / 2, height / 2]) - a @ centre
    return np.hstack([a, offset[:, None]]).astype(np.float32)


def skin_map(image, points, path=None):
    """The skin in `image` (PIL), from the skin-map model on the face FAN's 68
    `points` align: (skin, a boolean array at the image's size; the skin's
    probability there, 0-1; the aligned crop, RGB uint8; the probability
    over the crop)."""
    import cv2
    import numpy as np

    model = _load(Path(path or SKIN_MAP_MODEL))
    rgb = np.asarray(image.convert("RGB"))
    height, width = _CROP
    matrix = aligned_matrix(points)
    crop = cv2.warpAffine(rgb[:, :, ::-1], matrix, (width, height))  # BGR, as WWE's
    x = cv2.resize(crop.astype(np.float32), _INPUT, interpolation=cv2.INTER_AREA) / 255.0
    with _LOCK:
        prob = np.asarray(model(x[np.newaxis], training=False))[0, :, :, 0]
    crop_prob = cv2.resize(prob.astype(np.float32), (width, height),
                           interpolation=cv2.INTER_LINEAR)
    h, w = rgb.shape[:2]
    photo_prob = cv2.warpAffine(crop_prob, matrix, (w, h),
                                flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return (photo_prob >= THRESHOLD, np.clip(photo_prob, 0, 1),
            np.ascontiguousarray(crop[:, :, ::-1]), np.clip(crop_prob, 0, 1))


def face_polygon(points, forehead: float = _FOREHEAD):
    """The face FAN's 68 `points` (x, y) outline, as a polygon (N x 2): the
    jaw (0-16), then the brows (26-17) raised along the face's upright axis
    (chin to the nose's top) by `forehead` of the brows' height over the
    nose's bottom, less towards their ends (_FOREHEAD_TAPER)."""
    import numpy as np

    p = np.asarray(points, np.float64)
    up = p[27] - p[8]
    up /= np.linalg.norm(up)
    across = np.array([-up[1], up[0]])
    brows = p[17:27]
    height = forehead * float(np.dot(brows.mean(0) - p[33], up))
    t = (brows - brows.mean(0)) @ across
    t = t / max(1e-6, np.abs(t).max())
    lift = height * np.sqrt(np.clip(1 - (1 - _FOREHEAD_TAPER ** 2) * t ** 2, 0, 1))
    raised = brows + lift[:, None] * up
    return np.vstack([p[0:17], raised[::-1]])


def _filled(shape, polygon):
    import cv2
    import numpy as np

    out = np.zeros(shape, np.uint8)
    cv2.fillPoly(out, [np.round(polygon).astype(np.int32)], 1)
    return out.astype(bool)


def face(skin, points):
    """The whole face, strands across it and all (steps 4-6 of the module's
    docstring), from the skin-map model's `skin` (a boolean array at the
    photo's size) and FAN's 68 `points`: (the face; the landmarks' face; the
    skin kept), each a boolean array of `skin`'s shape."""
    import cv2
    import numpy as np

    p = np.asarray(points, np.float64)
    outline = _filled(skin.shape, face_polygon(p))
    margin = _CLIP_MARGIN * float(np.linalg.norm(p[16] - p[0]))
    near = _filled(skin.shape, face_polygon(p, _FOREHEAD_CLIP))
    size = 2 * int(round(margin)) + 1
    near = cv2.dilate(near.astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))).astype(bool)
    kept = skin & near
    both = (kept | outline).astype(np.uint8)
    contours, _ = cv2.findContours(both, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(np.vstack(contours))
    whole = np.zeros(skin.shape, np.uint8)
    cv2.fillConvexPoly(whole, hull, 1)
    return whole.astype(bool), outline, kept
