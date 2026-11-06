"""hairfx/hairseg.py: make_hair_masks.py's preprocessing, and the shipped
model run in this process at any photo's aspect."""

import numpy as np
import pytest
from PIL import Image

from hairfx import hairseg


class FakeModel:
    """Records what it is given; answers 0.75 hair at the larger size."""

    def __call__(self, inputs, training=False):
        self.inputs = inputs
        hi = inputs[0]
        return np.full((1, hi.shape[1], hi.shape[2], 1), 0.75, np.float32)


@pytest.mark.parametrize("h,w,hi,lo", [
    (4000, 3000, (640, 448), (160, 112)),
    (3000, 4000, (448, 640), (112, 160)),
    (1000, 800, (640, 512), (160, 128)),
    (571, 296, (768, 384), (192, 96)),
])
def test_preprocessing_is_make_hair_masks(h, w, hi, lo):
    model = FakeModel()
    bgr = np.zeros((h, w, 3), np.uint8)
    bgr[..., 0], bgr[..., 1], bgr[..., 2] = 104, 117, 123
    prob = hairseg.hair_probability(model, bgr)
    big, small = model.inputs
    assert big.shape == (1, *hi, 3) and small.shape == (1, *lo, 3)
    assert big.dtype == np.float32
    # BGR minus the Caffe means, divided by 255: this photo is about zero.
    assert np.abs(big).max() < 0.002
    assert prob.shape == (h, w) and np.allclose(prob, 0.75)


def test_hair_masks_threshold_and_bgr(monkeypatch):
    seen = {}

    def fake_probability(model, bgr):
        seen["bgr"] = bgr
        return np.where(np.arange(bgr.shape[1]) < 2, 0.5, 0.49)[None, :].repeat(bgr.shape[0], 0)

    monkeypatch.setattr(hairseg, "_load", lambda path: "model")
    monkeypatch.setattr(hairseg, "_probability", fake_probability)
    mask, = hairseg.hair_masks([Image.new("RGB", (4, 3), (10, 20, 30))])
    assert seen["bgr"][0, 0].tolist() == [30, 20, 10]
    assert mask.dtype == bool and mask.shape == (3, 4)
    assert mask[:, :2].all() and not mask[:, 2:].any()


def test_failures_are_runtime_errors(monkeypatch):
    def boom(model, bgr):
        raise ValueError("bad")

    monkeypatch.setattr(hairseg, "_load", lambda path: "model")
    monkeypatch.setattr(hairseg, "_probability", boom)
    with pytest.raises(RuntimeError, match="the hair model failed — ValueError: bad"):
        hairseg.hair_masks([Image.new("RGB", (4, 4))])


def test_missing_model(tmp_path):
    with pytest.raises(RuntimeError, match="HAIR_SEG_MODEL"):
        hairseg.hair_masks([Image.new("RGB", (4, 4))], str(tmp_path / "none.hdf5"))


def test_shipped_model_at_any_aspect_matches_the_saved_one():
    """The rebuilt model gives the saved model's answer at its own size, and
    takes the sizes the saved one refuses."""
    pytest.importorskip("tensorflow")
    try:
        import tf_keras as keras
    except ImportError:
        from tensorflow import keras
    if not hairseg.SEG_MODEL.is_file():
        pytest.skip("no hair model")
    saved = keras.models.load_model(str(hairseg.SEG_MODEL), compile=False)
    model = hairseg._load(hairseg.SEG_MODEL)
    assert model is hairseg._load(hairseg.SEG_MODEL)  # loaded once
    rng = np.random.default_rng(0)
    x = [rng.normal(0, 0.3, (1, 640, 512, 3)).astype(np.float32),
         rng.normal(0, 0.3, (1, 160, 128, 3)).astype(np.float32)]
    expected = np.asarray(saved(x, training=False))
    got = np.asarray(model(x, training=False))
    assert np.abs(expected - got).max() < 1e-5
    for h, w in ((4000, 3000), (1080, 1920), (1024, 1024), (571, 296)):
        prob = hairseg.hair_probability(model, rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
        assert prob.shape == (h, w) and 0 <= prob.min() <= prob.max() <= 1


def _reference_probability(model, bgr):
    """make_hair_masks.py's _hair_probability as written, the model called
    directly (the saved model's predict refuses other sizes)."""
    import math

    import cv2

    h, w = bgr.shape[:2]
    ratio = math.sqrt(160 * 128 / (h * w))
    lo_h, lo_w = int(h * ratio // 16 * 16), int(w * ratio // 16 * 16)
    hi_h, hi_w = lo_h * 4, lo_w * 4
    shrink = h >= hi_h and w >= hi_w
    image = cv2.resize(bgr.astype(np.float32), (hi_w, hi_h),
                       interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR)
    image = (image - np.array([104.008, 116.669, 122.675], dtype=np.float32)) / 255.0
    image_lo = cv2.resize(image, (lo_w, lo_h), interpolation=cv2.INTER_AREA)
    prob = np.asarray(model([image[np.newaxis], image_lo[np.newaxis]], training=False))[0, :, :, 0]
    return np.clip(cv2.resize(prob, (w, h), interpolation=cv2.INTER_LINEAR), 0, 1)


def test_buffers_give_make_hair_masks_results_exactly(monkeypatch):
    """Kept buffers, reused across sizes (larger, smaller, then over the
    keep limit), give the reference's probabilities and masks bit for bit."""
    pytest.importorskip("tensorflow")
    if not hairseg.SEG_MODEL.is_file():
        pytest.skip("no hair model")
    monkeypatch.setattr(hairseg, "_KEEP_MAX_PIXELS", 900 * 700)
    model = hairseg._load(hairseg.SEG_MODEL)
    rng = np.random.default_rng(1)
    for h, w in ((900, 700), (480, 640), (901, 700), (900, 700)):
        rgb = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        expected = _reference_probability(model, rgb[:, :, ::-1])
        assert np.array_equal(hairseg.hair_probability(model, rgb[:, :, ::-1]), expected)
        mask, = hairseg.hair_masks([Image.fromarray(rgb, "RGB")])
        assert np.array_equal(mask, expected >= 0.5)
