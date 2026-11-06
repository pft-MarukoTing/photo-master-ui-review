"""run_ai_hair_pipeline with a fake hairfx effect: process and finish timed
apart, the OpenAI calls through the timed client, Skip and errors."""

import pytest
from PIL import Image

from ai_hair import pipeline
from ai_hair.exceptions import AiHairError
from ai_hair.timing import TaskTiming
from engine_result_code import RESULT_ERROR_MODERATION_BLOCKED, RESULT_UNKNOWN_INTERNAL_ERROR
from hairfx import core
from hairfx.features import EFFECTS


class _Images:
    def __init__(self, error=None):
        self.error = error

    def edit(self, **kwargs):
        if self.error:
            raise self.error
        return "rendered"


class _Client:
    def __init__(self, error=None):
        self.images = _Images(error)

    def with_options(self, **kwargs):
        return self


class FakeFeature(core.Feature):
    """Calls images.edit through ctx.client, then makes n outputs; finish
    marks them post-processed."""

    def __init__(self, skip=None, fail=None):
        super().__init__("fake")
        self.skip, self.fail = skip, fail

    def prompts(self, opts):
        return {"edit": "p"}

    def process(self, ctx, photo, opts, result):
        result.step = "edit"
        try:  # as core.edit makes its call
            ctx.client.images.edit(model=ctx.render.model)
        except Exception as e:
            raise ctx.infer._raise_openai_error(e) from e
        if self.skip:
            raise core.Skip(self.skip)
        if self.fail:
            raise self.fail
        for i in range(ctx.render.n_images):
            result.outputs[f"result_{i}"] = photo.image.copy()
        result.warnings.append("a warning")

    def finish(self, ctx, photo, opts, result):
        result.info["finished"] = True
        result.info["aligned_by"] = {name: "global" for name in result.outputs}


class Logger:
    def __init__(self):
        self.infos, self.warnings = [], []

    def info(self, task_id, message):
        self.infos.append(message)

    def warning(self, task_id, message):
        self.warnings.append(message)


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (64, 48), (120, 80, 40)).save(path)
    return str(path)


def run(monkeypatch, photo, feature, client=None, n_images=2):
    monkeypatch.setitem(EFFECTS, "fake", feature)
    monkeypatch.setattr(pipeline, "check_models", lambda *a: None)
    timing = TaskTiming()
    logger = Logger()
    params = {"feature": "fake", "level": None}
    try:
        return pipeline.run_ai_hair_pipeline(params=params, src_paths=[photo], n_images=n_images,
                                             task_id="t", logger=logger, timing=timing,
                                             client=client or _Client()), timing, logger
    finally:
        run.timing = timing


def test_outputs_and_timing(monkeypatch, photo):
    result, timing, logger = run(monkeypatch, photo, FakeFeature())
    assert len(result["outputs"]) == 2 and result["skipped"] is None
    assert result["photo"].size == (64, 48)
    assert result["metadata"]["feature"] == "fake"
    assert result["metadata"]["aligned_by"] == {"result_0": "global", "result_1": "global"}
    for key in ("load_sec", "generate_sec", "postprocess_sec", "api_sec"):
        assert key in timing
    assert timing["api_calls"] == {"images.edit": {"count": 1, "sec": timing["api_calls"]["images.edit"]["sec"]}}
    assert timing["api_sec"] <= timing["generate_sec"]
    assert logger.warnings == ["[aiHair] fake: a warning"]


def test_skip_returns_no_outputs(monkeypatch, photo):
    result, timing, _ = run(monkeypatch, photo, FakeFeature(skip="no grey hair"))
    assert result["skipped"] == "no grey hair" and result["outputs"] == []
    assert "postprocess_sec" in timing


class BadRequestError(Exception):
    status_code = 400
    code = "moderation_blocked"


def test_failure_keeps_its_code_and_timing(monkeypatch, photo):
    with pytest.raises(AiHairError) as e:
        run(monkeypatch, photo, FakeFeature(), client=_Client(BadRequestError("blocked")))
    assert e.value.code == RESULT_ERROR_MODERATION_BLOCKED
    assert "generate_sec" in run.timing and "api_sec" in run.timing
    assert "postprocess_sec" not in run.timing


def test_other_failures_are_internal_errors(monkeypatch, photo):
    with pytest.raises(AiHairError) as e:
        run(monkeypatch, photo, FakeFeature(fail=ValueError("bad mask")))
    assert e.value.code == RESULT_UNKNOWN_INTERNAL_ERROR
    assert "fake edit failed — ValueError: bad mask" in e.value.args[0]


def test_core_edit_through_the_bundle_shim_and_timed_client(photo):
    """hairfx's own images.edit call, its response read by bundle/infer.py."""
    import base64
    import io
    from types import SimpleNamespace

    import infer
    from ai_hair.timing import CallTimer, TimedClient

    buf = io.BytesIO()
    Image.new("RGB", (128, 96), (200, 10, 10)).save(buf, format="PNG")
    item = SimpleNamespace(b64_json=base64.b64encode(buf.getvalue()).decode(), url=None)

    class Images:
        def edit(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(data=[item, item], usage=None)

    class Client:
        images = Images()

    timer = CallTimer()
    ctx = pipeline.build_context(TimedClient(Client(), timer), n_images=2)
    loaded = core.load_photo(infer, photo)
    result = core.EffectResult(effect="x", photo=photo)
    renders = core.edit(ctx, result, "edit", [loaded.file], "prompt", loaded.size, n=2)
    assert [r.size for r in renders] == [(64, 48), (64, 48)]
    assert Client.images.kwargs["n"] == 2 and Client.images.kwargs["model"] == core.DEFAULT_MODEL
    assert timer.summary()["images.edit"]["count"] == 1
    assert result.calls[0].step == "edit"


def test_photo_must_be_an_image(tmp_path):
    import infer
    from engine_result_code import RESULT_ERROR_DECODE_REF_IMAGE

    path = tmp_path / "x.jpg"
    path.write_bytes(b"not an image")
    with pytest.raises(AiHairError) as e:
        core.load_photo(infer, str(path))
    assert e.value.code == RESULT_ERROR_DECODE_REF_IMAGE
