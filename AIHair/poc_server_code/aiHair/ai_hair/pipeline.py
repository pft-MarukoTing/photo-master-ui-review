"""One aiHair job: the request's feature (hairfx/features, by its name) run
on the photo, the way HairFeatures' main.py runs it with its default
switches — the colour match, the lining up and the blend each as the
feature has them by default — timed in two sections: generation (Feature.process) and
post-processing (Feature.finish).
"""

from __future__ import annotations

import os
import threading

import config
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from hairfx import core
from hairfx.features import EFFECTS

from .exceptions import AiHairError, NonRetryableError
from .features import feature_options
from .timing import CallTimer, TaskTiming, TimedClient
from .utils import log

import infer  # noqa: E402 — bundle/infer.py, on sys.path via hairfx.core

_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _openai_api_key() -> str:
    """OPENAI_API_KEY, encrypted with AESCipher as aiSong's is."""
    from aiserver_utils.aescipher import AESCipher

    encrypted = os.getenv("OPENAI_API_KEY")
    if not encrypted or not encrypted.strip():
        raise NonRetryableError("Environment variable OPENAI_API_KEY is not set",
                                RESULT_UNKNOWN_INTERNAL_ERROR)
    try:
        return AESCipher().decrypt(encrypted.strip())
    except Exception as e:
        raise NonRetryableError("OPENAI_API_KEY must be encrypted with aiserver_utils.aescipher.AESCipher",
                                RESULT_UNKNOWN_INTERNAL_ERROR) from e


def get_openai_client():
    """The OpenAI client every task shares (thread-safe); each task wraps it
    in a TimedClient of its own."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            from openai import OpenAI

            timeout = float(config.AIHAIR_OPENAI_TIMEOUT_S or infer._DEFAULT_TIMEOUT_S)
            _CLIENT = OpenAI(api_key=_openai_api_key(), timeout=timeout,
                             max_retries=config.AIHAIR_OPENAI_MAX_RETRIES)
        return _CLIENT


def build_context(client, n_images: int) -> core.Context:
    """A hairfx Context as main.py builds it with its default flags: lined
    up (align), the blend, the colour match and their areas left to each
    effect (None), the hair model hairfx's own (HAIR_SEG_MODEL, run in
    this process) and the face model's python (HAIR_FACE_PYTHON)."""
    render = core.RenderSettings(
        model=config.AIHAIR_RENDER_MODEL or core.DEFAULT_MODEL,
        quality=config.AIHAIR_RENDER_QUALITY or core.DEFAULT_QUALITY,
        size=config.AIHAIR_RENDER_SIZE,
        n_images=n_images,
        keep_rendered_size=False,
    )
    ctx = core.Context(client, infer, render, workers=config.AIHAIR_CALLS_PER_TASK,
                       align=True, align_mode=None, blend=None, colour_match=True,
                       colour_match_area=None)
    return ctx


def check_models(ctx: core.Context, feature: core.Feature, opts) -> None:
    """main.py's checks that the hair model (ctx.seg_python: its path) and
    the face model's python are there where the effect's post-processing
    needs them; without them hairfx would leave the results as drawn with
    only a warning."""
    from pathlib import Path

    aligns, blends = feature.aligns(ctx, opts), feature.blends(ctx, opts)
    how = feature.align_how(ctx) if aligns else None
    colours = feature.colour_matches(ctx, opts)
    if (aligns or blends or colours) and not Path(ctx.seg_python).is_file():
        raise AiHairError(f"the hair model isn't there: {ctx.seg_python} "
                          "(set HAIR_SEG_MODEL)", RESULT_UNKNOWN_INTERNAL_ERROR)
    needs_face = (how in ("local", "local_flow", "regions_flow")
                  or (blends and feature.blend_face(opts))
                  or (colours and feature.colour_area(ctx) == "hair_inset"))
    if needs_face and not Path(ctx.face_python).is_file():
        raise AiHairError(f"the face model's python isn't there: {ctx.face_python} "
                          "(set HAIR_FACE_PYTHON)", RESULT_UNKNOWN_INTERNAL_ERROR)


def _failed(result: core.EffectResult, e: Exception) -> AiHairError:
    """`e`, raised in a step of the effect, as an AiHairError: one already
    (an OpenAI error mapped by bundle/infer.py) keeps its code."""
    where = f"{result.effect} {result.step} failed — " if result.step else f"{result.effect} failed — "
    if isinstance(e, AiHairError):
        return type(e)(where + e.args[0], e.code)
    code = e.args[1] if len(e.args) > 1 and isinstance(e.args[1], str) else RESULT_UNKNOWN_INTERNAL_ERROR
    return AiHairError(f"{where}{type(e).__name__}: {e}", code)


def run_ai_hair_pipeline(*, params: dict, src_paths: list[str], n_images: int, task_id: str,
                         logger=None, timing: TaskTiming | None = None,
                         client=None) -> dict:
    """The request's feature on the photo at src_paths[0], `n_images` results
    asked for. Fills `timing` as it goes (generate_sec, postprocess_sec,
    api_sec, the calls by kind), so a failed task's is there too. Returns
    {"outputs": [PIL images, in order], "photo": the photo (PIL),
    "skipped": why the effect left the photo as it is, or None,
    "metadata": {...}}."""
    timing = timing if timing is not None else TaskTiming()
    feature_name = params["feature"]
    feature = EFFECTS[feature_name]
    opts = core.parse_options(feature, feature_options(feature_name, params.get("level")))
    timer = CallTimer()
    ctx = build_context(TimedClient(client or get_openai_client(), timer), n_images)
    check_models(ctx, feature, opts)

    with timing.section("load"):
        photo = core.load_photo(infer, src_paths[0])
    log(logger, task_id, f"feature={feature_name} level={params.get('level')} "
                         f"photo={photo.size[0]}x{photo.size[1]} n_images={n_images} "
                         f"model={ctx.render.model} quality={ctx.render.quality}")

    # Feature.run, with its two halves timed apart; a failure ends the task
    # (Feature.run would keep it in the result instead).
    result = core.EffectResult(effect=feature_name, photo=photo.ref)
    try:
        with timing.section("generate"):
            try:
                feature.process(ctx, photo, opts, result)
            except core.Skip as e:
                result.skipped = str(e)
            except Exception as e:  # noqa: BLE001 — reported with its code
                raise _failed(result, e) from e
        with timing.section("postprocess"):
            feature.finish(ctx, photo, opts, result)
    finally:
        timing["api_sec"] = round(timer.busy_seconds(), 3)
        timing["api_calls"] = timer.summary()
        timing["cost_usd"] = round(result.cost, 6)

    if logger is not None:
        for warning in result.warnings:
            logger.warning(task_id, f"[aiHair] {feature_name}: {warning}")
    outputs = list(result.outputs.values())[:n_images]
    if not result.skipped and not outputs:
        raise AiHairError(f"{feature_name} made no result", RESULT_UNKNOWN_INTERNAL_ERROR)
    info = result.info
    return {
        "outputs": outputs,
        "photo": photo.image,
        "skipped": result.skipped,
        "metadata": {
            "feature": feature_name,
            "level": params.get("level"),
            "warnings": list(result.warnings),
            "aligned_by": info.get("aligned_by"),
            "blend": (info.get("blend") or {}).get("mask"),
            "colour_match": info.get("colour_match"),
            "cost_usd": round(result.cost, 6),
        },
    }
