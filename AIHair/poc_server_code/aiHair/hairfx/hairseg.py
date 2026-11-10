"""The in-house hair model (hair/hair_model_input_none_tf115.hdf5, coarse + refinement
in one Keras model), run in this process: the hair masks that outputs are
lined up outside of (Feature.run), the blends, the colour match and
hairline's hair_source model.

aiHair: HairFeatures ran make_hair_masks.py in a python of its own (the
hairseg-tf env), starting it, TensorFlow and the model afresh for every
call, one call at a time; here the model is loaded once and kept.
hair_probability is make_hair_masks.py's _hair_probability as it is: the
photo goes in twice, at about 640x512 and 160x128 pixels (its own aspect,
edges multiples of 16), as BGR minus the Caffe means, divided by 255; out
comes a hair probability at the larger size, resized to the photo's and
taken as hair at THRESHOLD and above.

hair_masks keeps HairFeatures' signature, so hairfx's callers are as they
were: their `seg_python` (Context.seg_python) is the model's path here.
"""

import math
import os
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# HAIR_SEG_MODEL sets it; unset, the one shipped in models/.
SEG_MODEL = Path(os.environ.get("HAIR_SEG_MODEL")
                 or _ROOT / "models" / "hair" / "hair_model_input_none_tf115.hdf5")
# What Context.seg_python defaults to and hair_masks takes: the model.
SEG_PYTHON = SEG_MODEL
THRESHOLD = 0.5  # make_hair_masks.py's --threshold

# make_hair_masks.py's (Inference_refinement_model_20231116.py's
# --imagesize / --imagesize_160 and its Caffe means).
_SIZE_HI = (640, 512)
_SIZE_LO = (160, 128)
_BGR_MEAN = (104.008, 116.669, 122.675)

_MODELS: dict = {}
_LOAD_LOCK = threading.Lock()
# One photo at a time: the model and _BUFFERS are shared by every worker
# thread.
_LOCK = threading.Lock()


def _load(path: Path):
    """The Keras 2 model at `path`, loaded once, rebuilt to take any input
    size (_any_size). TF 2.16+ ships Keras 3, which can't read this Keras 2
    .hdf5, so tf_keras (Keras 2) is used where it is installed
    (make_hair_masks.py's _load_model)."""
    key = str(Path(path).resolve())
    with _LOAD_LOCK:
        if key not in _MODELS:
            if not Path(key).is_file():
                raise RuntimeError(f"the hair model isn't there: {key} (set HAIR_SEG_MODEL)")
            os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
            try:
                import tf_keras as keras
            except ImportError:
                from tensorflow import keras
            _MODELS[key] = _any_size(keras, keras.models.load_model(key, compile=False))
        return _MODELS[key]


def _any_size(keras, model):
    """`model` with its inputs' height and width left open, its weights as
    they are. hair_model_input_none_tf115.hdf5 was saved for 640x512 and 160x128
    only, and refuses any other size (Keras' input check), though every
    layer takes any size whose sides are multiples of 16: rebuilt so, each
    photo goes in at its own aspect, as make_hair_masks.py's model took it."""
    config = model.get_config()
    opened = False
    for layer in config["layers"]:
        if layer["class_name"] != "InputLayer":
            continue
        for name in ("batch_input_shape", "batch_shape"):
            shape = layer["config"].get(name)
            if shape and len(shape) == 4:
                layer["config"][name] = [shape[0], None, None, shape[3]]
                opened = True
    if not opened:
        return model
    rebuilt = keras.Model.from_config(config)
    rebuilt.set_weights(model.get_weights())
    return rebuilt


def preload(path=None) -> None:
    """Load the model now (server start), so a missing or broken model fails
    there rather than in the first task."""
    _load(Path(path or SEG_MODEL))


# The photo as float32 and the probability at its size are made in buffers
# kept from one photo to the next (under _LOCK, so one set does): a fresh
# array of a 12 MP photo's float32 took 2 s to allocate on the dev server,
# copying into a kept one 0.1 s. Kept for photos up to this many pixels;
# a larger one gets fresh arrays, so the server doesn't hold on to them.
_KEEP_MAX_PIXELS = 16 * 1024 * 1024
_BUFFERS: dict = {}


def _buffer(name: str, shape: tuple):
    import numpy as np

    size = math.prod(shape)
    if shape[0] * shape[1] > _KEEP_MAX_PIXELS:
        return np.empty(shape, np.float32)
    kept = _BUFFERS.get(name)
    if kept is None or kept.size < size:
        kept = _BUFFERS[name] = np.empty(size, np.float32)
    return kept[:size].reshape(shape)


def _probability(model, bgr):
    """make_hair_masks.py's _hair_probability, unclipped, in _BUFFERS: a
    view valid until the next call, so to be called and read under _LOCK.
    The model is called directly rather than through predict, as photos of
    each size would retrace it."""
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    ratio = math.sqrt(_SIZE_LO[0] * _SIZE_LO[1] / (h * w))
    lo_h, lo_w = int(h * ratio // 16 * 16), int(w * ratio // 16 * 16)
    hi_h, hi_w = lo_h * (_SIZE_HI[0] // _SIZE_LO[0]), lo_w * (_SIZE_HI[1] // _SIZE_LO[1])
    shrink = h >= hi_h and w >= hi_w
    photo = _buffer("photo", (h, w, 3))
    np.copyto(photo, bgr, casting="unsafe")  # bgr.astype(np.float32)'s values
    image = cv2.resize(photo, (hi_w, hi_h),
                       interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR)
    image = (image - np.array(_BGR_MEAN, dtype=np.float32)) / 255.0
    image_lo = cv2.resize(image, (lo_w, lo_h), interpolation=cv2.INTER_AREA)
    prob = model([image[np.newaxis], image_lo[np.newaxis]], training=False)
    prob = np.ascontiguousarray(np.asarray(prob)[0, :, :, 0], dtype=np.float32)
    return cv2.resize(prob, (w, h), dst=_buffer("prob", (h, w)),
                      interpolation=cv2.INTER_LINEAR)


def hair_probability(model, bgr):
    """Hair probability (0-1) at the photo's size, for a BGR uint8 photo:
    make_hair_masks.py's _hair_probability."""
    import numpy as np

    with _LOCK:
        return np.clip(_probability(model, bgr), 0, 1)


def hair_masks(images: list, seg_python=None) -> list:
    """The hair in each of `images` (PIL), as boolean arrays at its size,
    True = hair: the hair model (at `seg_python`, the model's path; None,
    SEG_MODEL) at THRESHOLD. The probability isn't clipped first, which
    leaves which pixels reach THRESHOLD as they are. Raises RuntimeError if
    it fails."""
    import numpy as np

    model = _load(Path(seg_python or SEG_MODEL))
    masks = []
    try:
        for image in images:
            rgb = np.asarray(image.convert("RGB"))
            with _LOCK:
                masks.append(_probability(model, rgb[:, :, ::-1]) >= THRESHOLD)
    except Exception as e:  # noqa: BLE001 — as the subprocess's failure was
        raise RuntimeError(f"the hair model failed — {type(e).__name__}: {e}") from e
    return masks
