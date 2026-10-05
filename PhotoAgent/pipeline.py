import base64
import inspect
import logging
import time
from functools import partial
from typing import Optional

from compatibility_version import CURRENT_COMPATIBILITY_VERSION
from config import MODELS
from engine_result_code import RESULT_INVALID_PARAMETER
from image_tools import (
    apply_derived_aspect_ratio,
    create_local_enhanced_bytes,
    get_image_dimensions,
    image_to_data_url,
)
from gemini_client import (
    edit_photo as edit_photo_gemini,
    edit_eye_repair_pass as edit_eye_repair_pass_gemini,
    NANO_BANANA_LITE_MODEL,
)
from mai_client import edit_photo as edit_photo_mai, MAI_IMAGE_26_MODEL
from openai_client import (
    analyze_photo,
    review_user_instruction,
    edit_photo,
    edit_eye_repair_pass as edit_eye_repair_pass_openai,
    quality_check_photo,
    reframe_composition,
)
from prompts import (
    build_overall_analysis_instruction,
    build_portrait_qc_instruction,
    build_eye_repair_planning_instruction,
    build_eye_repair_pass_prompt,
    build_body_reshape_planning_instruction,
    body_reshape_enabled,
    strip_body_reshape,
    sanitize_body_reshape_analysis,
    get_body_reshape_edit_prompt,
    body_reshape_items,
    eye_repair_targets,
    final_smile_by_subject,
    portrait_qc_persons,
    apply_rejected_reframing,
    approved_blink_items,
    approved_gaze_items,
    composition_state,
    merge_reframe_result,
    restore_composition_state,
    with_reframing_proposal,
)
from photo_agent.feature_resolver import build_blink_pass_targets
from photo_agent.param_validation import DEFAULT_EDITOR_PROMPT_STRATEGY

BLINK_REPAIR_IMAGE_MODEL = "gpt-image-2.5-sunburst"
BLINK_REPAIR_QUALITY = "high"

EDIT_PHOTO_BY_MODEL = {
    "gpt-image-2": edit_photo,
    "gpt-image-1.5": partial(edit_photo, model="gpt-image-1.5"),
    "gpt-image-2.5-sunburst": partial(edit_photo, model="gpt-image-2.5-sunburst"),
    "gpt-image-2.5-flare": partial(edit_photo, model="gpt-image-2.5-flare"),
    "nano-banana-2": edit_photo_gemini,
    "nano-banana-lite": partial(edit_photo_gemini, model=NANO_BANANA_LITE_MODEL),
    "MAI-Image-2.6": partial(edit_photo_mai, model=MAI_IMAGE_26_MODEL),
}

# Step 5b's model is a separate choice from Step 5's (config.MODELS["gaze_repair"],
# not image_model) -- picking gpt-image-2 low for Step 5 doesn't have to mean
# gpt-image-2 low also runs the eye repair pass. Keyed the same way as
# EDIT_PHOTO_BY_MODEL for the same reason: a one-line config swap between
# providers while comparing which one repairs gaze more reliably.
EYE_REPAIR_PASS_BY_MODEL = {
    "gpt-image-2": edit_eye_repair_pass_openai,
    "gpt-image-1.5": partial(edit_eye_repair_pass_openai, model="gpt-image-1.5"),
    "gpt-image-2.5-sunburst": partial(edit_eye_repair_pass_openai, model="gpt-image-2.5-sunburst"),
    "gpt-image-2.5-flare": partial(edit_eye_repair_pass_openai, model="gpt-image-2.5-flare"),
    "nano-banana-2": edit_eye_repair_pass_gemini,
    "nano-banana-lite": partial(edit_eye_repair_pass_gemini, model=NANO_BANANA_LITE_MODEL),
}

logger = logging.getLogger(__name__)


def _run_edit_photo_compat(editor, image_bytes, analysis, quality, preset_key, *, aspect_ratio, single_pass_edit):
    """Call an editor while keeping compatibility with legacy/test adapters.

    Provider editors in this repository accept ``single_pass_edit``.  A few
    integrations (and lightweight test doubles) still expose the older
    five-argument contract, so do not make that optional control parameter a
    hard requirement for every callable placed in ``EDIT_PHOTO_BY_MODEL``.
    Built-in editors continue to receive the flag unchanged.
    """
    kwargs = {"aspect_ratio": aspect_ratio}
    try:
        signature = inspect.signature(editor)
    except (TypeError, ValueError):
        # Some extension callables do not expose an inspectable signature;
        # preserve the current behavior and pass the complete contract.
        accepts_single_pass = True
    else:
        accepts_single_pass = (
            "single_pass_edit" in signature.parameters
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        )
    if accepts_single_pass:
        kwargs["single_pass_edit"] = single_pass_edit
    return editor(image_bytes, analysis, quality, preset_key, **kwargs)


def _run_eye_repair_pass(image_b64: str, analysis: dict, quality: str):
    """Step 5b: perform the approved gaze and/or blink repair(s) as a second,
    isolated whole-image edit on top of Step 5's own output.

    Under the two-call path (single_pass_edit=False), Step 5 is told to leave
    gaze and eyelid state untouched even when a fix is approved (the
    include_gaze/include_blink gates in feature_resolver and the deferred
    blocks in build_edit_prompt), so this pass is the ONLY place those
    repairs happen. A single call bundling them with exposure/crop/skin/style
    reliably applies the coarse, whole-frame changes but often no-ops or
    under-applies the fine, localized eye work; a call whose prompt contains
    nothing but the eye repairs gives them the model's full attention.

    Model: a blink repair runs on BLINK_REPAIR_IMAGE_MODEL at
    BLINK_REPAIR_QUALITY, the tier blink repair is reliable on; a gaze-only
    pass runs on config.MODELS["gaze_repair"] at the requested quality.

    Each toggle (gaze_contact_detection / eye_closed_detection) and each
    per-person opt-out (_disabled_issue_subject_indices) is honoured here as
    well as in build_edit_prompt, since this pass rebuilds its own item lists
    from the analysis. resolve_user_gaze_request / build_blink_pass_targets
    turn a manually-forced row into a real repair target.

    Best-effort: the caller's try/except keeps the Step 5 image untouched if
    this fails, so it can only help, never regress a job.

    Returns (image_b64, prompt_text, usage) -- prompt_text and usage are None
    when there was no approved eye repair to run, matching every other
    stage's "no GPT call made" convention (see finish_stage's prompts dict).
    """
    gaze_items = approved_gaze_items(analysis)
    blink_targets, blink_rules = build_blink_pass_targets(analysis)
    if not gaze_items and not blink_targets:
        return image_b64, None, None
    # Step 5 has already applied the smile setting; the gaze has to be redirected inside that expression.
    final_smile = final_smile_by_subject(analysis)
    gaze_items = [
        {**item, "_final_smile": final_smile.get(item.get("_subject_index"), final_smile.get(None))}
        for item in gaze_items
    ]

    if blink_targets:
        model, quality = BLINK_REPAIR_IMAGE_MODEL, BLINK_REPAIR_QUALITY
    else:
        model = MODELS["gaze_repair"]
    run_eye_repair_pass = EYE_REPAIR_PASS_BY_MODEL.get(model, edit_eye_repair_pass_gemini)
    prompt = build_eye_repair_pass_prompt(gaze_items, blink_targets, blink_rules)
    result_bytes = base64.b64decode(image_b64)
    edited_bytes, prompt_text, usage = run_eye_repair_pass(result_bytes, prompt, quality=quality)
    return base64.b64encode(edited_bytes).decode(), prompt_text, usage


def _quality_check_fallback(error: Exception) -> dict:
    return {
        "pass": False,
        "overall_status": "QA 未完成",
        "score": None,
        "summary": "輸出已完成，但品質檢查階段未成功回傳可解析結果。",
        "checks": {
            "subject_preserved": None,
            "crop_safe": None,
            "not_overedited": None,
            "light_color_improved": None,
        },
        "warnings": [str(error) or "品質檢查失敗"],
        "recommendation": "請人工快速比對原圖與輸出圖。",
    }


def _normalize_quality_check_result(result):
    """Return the general QA payload without Body Reshape endpoint data.

    The current strict QA schema cannot emit ``body_adherence``.  Removing it
    here as well keeps a saved/legacy or test-double payload from reintroducing
    endpoint-level Body Reshape results into a new run's result artifact.
    """
    qa = dict(result or {})
    qa.pop("body_adherence", None)
    return qa


def analyze_stage(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = MODELS["analysis"]) -> dict:
    """Steps 1-2: read the photo and analyze it.

    The user's instruction is passed through in finish_stage instead of here --
    it's collected on the analyze-preview screen, shown to the user only
    after this stage's result comes back, so there's nothing to review yet
    at this point.

    advanced_options: the Advanced Settings toggles dict (e.g.
    {"eye_closed_detection": False}). These do not change the one-time
    analysis; they are stored on the result and enforced when repairs are
    reviewed and applied (see _run_eye_repair_pass and build_edit_prompt).
    calm_hair and smile_adjustment/smile_level are likewise edit-only, but
    represent transformations the user elects rather than detected-defect
    filters.
    """

    # Step 1 — read
    logger.info("Step 1: image loaded (%d bytes)", len(image_bytes))

    # Step 2 — analyze
    t0 = time.time()
    logger.info("Step 2: analyzing photo (aspect_ratio=%s)", aspect_ratio)
    analysis, step2_usage = analyze_photo(image_bytes, aspect_ratio, advanced_options, analysis_model=analysis_model)
    analysis = sanitize_body_reshape_analysis(analysis)
    if not body_reshape_enabled():
        # Keep the stage boundary clean even when an alternate analyzer or a
        # retry returns an analysis object carrying a stale body plan.
        analysis = strip_body_reshape(analysis)
    step2_duration = time.time() - t0
    logger.info("Step 2 done in %.2fs: subject=%s scene=%s", step2_duration, analysis.get("subject_category"), analysis.get("scene_category"))

    # Records which aspect-ratio preference was active when this analysis ran,
    # so a later edit under a *different* preference (e.g. user picked "keep
    # original" here but switches to "Auto" on the analyze-preview screen
    # before regenerating) knows recommended_aspect_ratio="original" was
    # forced by that earlier choice, not a genuine model judgment -- see
    # build_edit_prompt's use of this field.
    analysis["_aspect_ratio_pref_at_analysis"] = aspect_ratio
    # Geometry, not model-authored prose, owns the executable output ratio.
    # Resolve every preference here so the review screen already shows the
    # fitted crop and locally inserted ratio text. The finish-stage repeats
    # this after checkbox/picker changes because those do not re-run analysis.
    img_w, img_h = get_image_dimensions(image_bytes)
    analysis = apply_derived_aspect_ratio(
        analysis, img_w, img_h, aspect_ratio_pref=aspect_ratio,
    )
    logger.info(
        "Step 2 aspect ratio resolved locally: %s (crop measured %s, source=%s)",
        analysis.get("recommended_aspect_ratio"),
        analysis.get("crop_box_measured_ratio"),
        analysis.get("aspect_ratio_source"),
    )

    # Two-pass split (see openai_client._analyze_photo_two_pass): analysis
    # already ran by this point, so subjects[] here is Call A's own
    # finalized result -- portrait_qc_persons re-derives the exact same
    # subject_index-tagged list Call B was actually given (the merge
    # never reorders or filters subjects[], so this reconstruction
    # matches what was actually sent).
    persons = portrait_qc_persons(analysis)
    repair_targets = eye_repair_targets(analysis)
    analysis["prompts"] = {
        "photo_analysis": build_overall_analysis_instruction(
            aspect_ratio, advanced_options, source_size=get_image_dimensions(image_bytes)),
        "photo_analysis_portrait_qc": build_portrait_qc_instruction(persons) if persons else None,
        "photo_analysis_eye_repair_planning": (
            build_eye_repair_planning_instruction(repair_targets)
            if repair_targets else None
        ),
        "photo_analysis_body_reshape": (
            build_body_reshape_planning_instruction(persons)
            if persons and body_reshape_enabled() else None
        ),
    }
    analysis["timings"] = {
        "Step 2 - photo_analysis": step2_duration,
    }
    analysis["usage"] = {
        "Step 2 - photo_analysis": step2_usage,
    }
    # Carry the edit-stage toggles on the analysis so the two-stage flow and
    # debug-UI /rerun both hand them back automatically, with the stored
    # analysis recording which corrections that run allowed.
    # Recorded, not applied here: eye_closed_detection/hair_mess_detection/
    # gaze_contact_detection now only gate the EDIT stage (build_edit_prompt's
    # own strip_disabled_topic_items call) -- analysis always runs full
    # detection regardless of these three, so the review screen shows every
    # genuine finding and a later revise can turn a toggle back on without
    # re-analyzing the photo.
    analysis["_advanced_options"] = dict(advanced_options or {})
    # Stamped so a history item resumed under a later app version (see
    # backend/routes/api.py's continue_job) can tell which analyze_stage
    # shape produced it -- see compatibility_version.py for how that's read back.
    analysis["compatibility_version"] = CURRENT_COMPATIBILITY_VERSION
    return analysis


def _next_reframe_stage_key(usage):
    """Each reframe call gets its own usage key so repeated reframes add up instead of overwriting."""
    key, n = "Step 2b - photo_reframe", 1
    while key in usage:
        n += 1
        key = f"Step 2b - photo_reframe ({n})"
    return key


def reframe_stage(
    image_bytes: bytes,
    analysis: dict,
    aspect_ratio: str = "auto",
    analysis_model: str = MODELS["analysis"],
) -> dict:
    """Step 2b: re-plan the composition of an existing analysis for a 長寬比
    picked on the results page, leaving every other finding as analyzed.

    Each ratio's resolved composition is kept in _composition_by_ratio, so
    returning to a ratio already planned costs no further call.
    """
    if analysis.get("_skipped_analysis"):
        raise RuntimeError("跳過分析的結果沒有構圖可以重新規劃。", RESULT_INVALID_PARAMETER)
    pref = str(aspect_ratio or "auto").strip().lower()
    base = with_reframing_proposal(analysis)
    planned_for = str(base.get("_aspect_ratio_pref_at_analysis") or "auto").strip().lower()
    by_ratio = dict(base.get("_composition_by_ratio") or {})
    by_ratio.setdefault(planned_for, composition_state(base))

    if pref in by_ratio:
        logger.info("Step 2b: reusing the composition already planned for %s", pref)
        reframed = restore_composition_state(base, by_ratio[pref])
    else:
        t0 = time.time()
        logger.info("Step 2b: reframing for aspect_ratio=%s (planned for %s)", pref, planned_for)
        reframe, usage, prompt_text = reframe_composition(
            image_bytes, base, pref, analysis_model=analysis_model,
        )
        reframed = merge_reframe_result(base, reframe, pref)
        img_w, img_h = get_image_dimensions(image_bytes)
        reframed = apply_derived_aspect_ratio(reframed, img_w, img_h, aspect_ratio_pref=pref)
        reframed["prompts"] = {**(reframed.get("prompts") or {}), "photo_reframe": prompt_text}
        prior_usage = reframed.get("usage") or {}
        stage_key = _next_reframe_stage_key(prior_usage)
        reframed["timings"] = {**(reframed.get("timings") or {}), stage_key: time.time() - t0}
        reframed["usage"] = {**prior_usage, stage_key: usage}
        by_ratio[pref] = composition_state(reframed)
        logger.info(
            "Step 2b done: %s (crop measured %s)",
            reframed.get("recommended_aspect_ratio"), reframed.get("crop_box_measured_ratio"),
        )

    reframed["_composition_by_ratio"] = by_ratio
    return reframed


def review_stage(
    analysis: dict,
    user_prompt: str,
    *,
    overwrite_prompt: bool = False,
    analysis_model: str = MODELS["analysis"],
) -> dict:
    """Step 3: pass the typed instruction through for every editor prompt mode.

    The instruction arrives after the analyze-preview screen. Return the same
    shape used by finish_stage, without an LLM prompt or usage.
    """
    t0 = time.time()
    logger.info("Step 3: passing through user instruction (has_prompt=%s, overwrite_prompt=%s)", bool(user_prompt), overwrite_prompt)
    user_instruction, review_prompt, step3_usage = review_user_instruction(
        user_prompt, analysis, analysis_model=analysis_model,
    )
    if overwrite_prompt and user_instruction.get("has_user_direction"):
        user_instruction["overwrite_prompt"] = True
    duration = time.time() - t0
    logger.info("Step 3 done in %.2fs: has_direction=%s", duration, user_instruction.get("has_user_direction"))
    return {
        "user_instruction": user_instruction,
        "prompt": review_prompt,
        "usage": step3_usage,
        "duration": duration,
    }


def finish_stage(
    image_bytes: bytes,
    analysis: dict,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
    image_model: str = "gpt-image-2",
    analysis_model: str = MODELS["analysis"],
    run_qa: bool = True,
    aspect_ratio: str = "auto",
    overwrite_prompt: bool = False,
    single_pass_edit: bool = True,
    review: dict = None,
) -> dict:
    """Steps 3, 5-6: pass user instruction through, then AI edit + QA (or local enhancement).

    review: a review_stage result to reuse instead of calling Step 3 here, so
    every RUN of one job edits from the same user instruction.

    There is no separate crop-validation step: the crop guidance from
    analyze_stage (crop_box, roi_box, protected_regions, ...) is passed
    straight to the edit model, which is instructed to perform that crop
    itself as part of the edit.

    single_pass_edit: skip Step 5b (see _run_eye_repair_pass) and fall back
    to having Step 5's own edit call bundle any approved gaze/blink fix in with
    everything else (build_edit_prompt's single_pass_edit-gated gaze_block/
    blink_block, prompts.py). Under the two-call path (single_pass_edit=False),
    Step 5 is instead told to leave gaze and eyelid state untouched and Step 5b
    performs 100% of any approved gaze/blink repair as an isolated second pass -- so
    single_pass_edit (the default) trades that dedicated pass's reliability
    for a single edit-model round trip. When single_pass_edit is on, the photo
    has an approved blink repair and the request is BLINK_REPAIR_IMAGE_MODEL at
    "medium", Step 5 bumps its own edit call to BLINK_REPAIR_QUALITY --
    reported back as extra-info.image_quality.

    Returns: {
        result_image: str,          # base64 PNG (no data-URL prefix)
        result_image_data_url: str, # data:image/png;base64,...
        result_text: dict,          # analysis + quality_check + token_usage
        pre_edit_image: Optional[str], # base64 PNG, Step 5 output before Step 5b
                                     # overwrites it -- None unless Step 5b
                                     # actually ran (see pre_edit_image_b64 below)
    }
    """
    analysis = sanitize_body_reshape_analysis(analysis)
    if not body_reshape_enabled():
        # finish_stage may be called with a saved analysis created before the
        # internal feature was disabled.  Strip it before any prompt, QA, or
        # result metadata is built so no downstream path can execute it.
        analysis = strip_body_reshape(analysis)
    prompts = dict(analysis.get("prompts") or {})
    timings = dict(analysis.get("timings") or {})
    usage = dict(analysis.get("usage") or {})

    # Step 3 — pass through user instruction (collected on the analyze-preview
    # screen, so it's only available now that this stage has started).
    # A multi-RUN job prepares it once in job_processor and passes it in here.
    review = review or review_stage(
        analysis, user_prompt,
        overwrite_prompt=overwrite_prompt, analysis_model=analysis_model,
    )
    user_instruction = review["user_instruction"]
    if (user_prompt or "").strip():
        user_instruction = dict(user_instruction or {})
        user_instruction["original_user_direction"] = user_prompt.strip()
    analysis = {**analysis, "user_instruction": user_instruction}
    # A declined framing recipe owns both decisions the analyzer bundled into
    # it: the crop/recomposition and its output shape.  Resolve that review
    # choice before body planning, editing, local cropping, and QA so no later
    # consumer can revive the stale crop_box or ratio recommendation.
    analysis, aspect_ratio = apply_rejected_reframing(analysis, aspect_ratio)
    # Results-page ratio changes and a re-checked reframing item reuse the
    # stored analyzer geometry. Refit it for this run and rewrite the visible
    # review text from that resolved result. ``original`` controls only the
    # output shape; a meaningful same-ratio crop remains enabled.
    img_w, img_h = get_image_dimensions(image_bytes)
    analysis = apply_derived_aspect_ratio(
        analysis, img_w, img_h, aspect_ratio_pref=aspect_ratio,
    )
    analysis["_aspect_ratio_pref_at_edit"] = aspect_ratio
    # A body edit is active when at least one person's approved plan remains
    # enabled.  ``body_reshape_items`` preserves the person-to-instruction
    # mapping that the Editor prompt needs; the joined reader is retained only
    # for backwards-compatible metadata/log consumers.
    body_prompt = get_body_reshape_edit_prompt(analysis)
    body_items = body_reshape_items(analysis)
    body_review_active = bool(body_prompt)
    prompts["user_instruction_review"] = review["prompt"]
    usage["Step 3 - user_instruction_review"] = review["usage"]
    timings["Step 3 - user_instruction_review"] = review["duration"]

    prompts["crop_validation"] = None
    timings["Step 4 - crop_validation"] = None

    # Populated only when Step 5b actually runs (below) -- Step 5's own
    # output before that pass overwrites it, kept around so callers can
    # surface it as a debug-UI "stage 1" image distinct from the final
    # result. Stays None under single_pass_edit or ai_photo_editing=False,
    # where there is no second stage to distinguish it from.
    pre_edit_image_b64 = None
    # Default for the ai_photo_editing=False branch below, where no edit call
    # runs and this stays whatever was requested; overwritten just below when
    # an edit call actually happens.
    edit_quality = quality

    if ai_photo_editing:
        # Step 5 — AI edit
        t0 = time.time()
        # An approved blink repair only comes out reliably at Sunburst's top tier; two-stage defers it to Step 5b.
        if (single_pass_edit and image_model == BLINK_REPAIR_IMAGE_MODEL and quality == "medium"
                and approved_blink_items(analysis)):
            edit_quality = BLINK_REPAIR_QUALITY
        logger.info("Step 5: editing photo (preset=%s quality=%s model=%s)", preset_key, edit_quality, image_model)
        run_edit_photo = EDIT_PHOTO_BY_MODEL.get(image_model, edit_photo)
        edit_result, edit_prompt = _run_edit_photo_compat(
            run_edit_photo,
            image_bytes,
            analysis,
            edit_quality,
            preset_key,
            aspect_ratio=aspect_ratio,
            single_pass_edit=single_pass_edit,
        )
        prompts["image_edit_raw"] = edit_result.get("raw_prompt") or edit_prompt
        prompts["image_edit"] = edit_prompt
        prompts["image_edit_resolution"] = edit_result.get("prompt_resolution")
        prompts["image_edit_strategy"] = edit_result.get("prompt_strategy") or DEFAULT_EDITOR_PROMPT_STRATEGY
        usage["Step 5 - image_edit"] = edit_result.get("usage")
        timings["Step 5 - image_edit"] = time.time() - t0
        image_b64 = edit_result["image_b64"]
        logger.info("Step 5 done in %.2fs: image returned", timings["Step 5 - image_edit"])

        # Step 5b — gaze/blink repair pass (best-effort; see
        # _run_eye_repair_pass for scope and why it exists). Never allowed to
        # fail the job -- worst case is the Step 5 image passes through as-is.
        # Skipped entirely under single_pass_edit -- see finish_stage's own
        # docstring for that tradeoff.
        t0 = time.time()
        if single_pass_edit:
            prompts["gaze_repair_pass"] = None
            usage["Step 5b - gaze_repair_pass"] = None
            timings["Step 5b - gaze_repair_pass"] = time.time() - t0
            logger.info("Step 5b skipped (single_pass_edit=True)")
        else:
            pre_edit_image_b64 = image_b64
            try:
                image_b64, gaze_repair_prompt, gaze_repair_usage = _run_eye_repair_pass(image_b64, analysis, quality)
                prompts["gaze_repair_pass"] = gaze_repair_prompt
                usage["Step 5b - gaze_repair_pass"] = gaze_repair_usage
                timings["Step 5b - gaze_repair_pass"] = time.time() - t0
                logger.info("Step 5b done in %.2fs", timings["Step 5b - gaze_repair_pass"])
            except Exception as exc:
                prompts["gaze_repair_pass"] = None
                usage["Step 5b - gaze_repair_pass"] = None
                timings["Step 5b - gaze_repair_pass"] = time.time() - t0
                logger.warning("Step 5b gaze repair pass failed in %.2fs, keeping Step 5 output: %s", timings["Step 5b - gaze_repair_pass"], exc)

        # Step 6 — quality check (debug-ui escape hatch: skip the QA call
        # entirely when run_qa=False, e.g. to save time/cost while iterating
        # on the edit step alone)
        t0 = time.time()
        if run_qa:
            logger.info("Step 6: running quality check")
            try:
                qa, qa_prompt, qa_usage = quality_check_photo(image_bytes, image_b64, analysis, analysis_model=analysis_model)
                qa = _normalize_quality_check_result(qa)
                prompts["quality_check"] = qa_prompt
                usage["Step 6 - quality_check"] = qa_usage
                timings["Step 6 - quality_check"] = time.time() - t0
                logger.info("Step 6 done in %.2fs: score=%s pass=%s", timings["Step 6 - quality_check"], qa.get("score"), qa.get("pass"))
            except Exception as exc:
                timings["Step 6 - quality_check"] = time.time() - t0
                logger.warning("Step 6 QA failed in %.2fs: %s", timings["Step 6 - quality_check"], exc)
                qa = _quality_check_fallback(exc)
                qa = _normalize_quality_check_result(qa)
                prompts["quality_check"] = None
                usage["Step 6 - quality_check"] = None
        else:
            logger.info("Step 6: skipping quality check (run_qa=False)")
            qa = {
                "pass": True,
                "overall_status": "已略過 QA",
                "score": None,
                "summary": "已依設定略過 AI 輸出 QA 檢查。",
                "checks": {},
                "warnings": [],
                "recommendation": "",
            }
            qa = _normalize_quality_check_result(qa)
            prompts["quality_check"] = None
            usage["Step 6 - quality_check"] = None
            timings["Step 6 - quality_check"] = None

        # Deterministic geometry verification (see
        # image_tools.output_geometry_warnings): reported alongside the QA model's
        # own findings, but measured, not judged -- a stretched/squashed output is
        # arithmetic, and the QA model was silently passing it. Surfaced even when
        # run_qa=False, since it costs nothing and this is the one check that
        # catches the crop/canvas mismatch that causes the deformation.
        geometry_warnings = edit_result.get("geometry_warnings") or []
        for warning in geometry_warnings:
            logger.warning("Output geometry check: %s", warning)
        if geometry_warnings:
            qa["warnings"] = list(qa.get("warnings") or []) + geometry_warnings
    else:
        # Step 5 — local crop + basic enhancement (no AI API call)
        t0 = time.time()
        logger.info("Step 5: local enhancement only (ai_photo_editing=False)")
        enhanced_bytes = create_local_enhanced_bytes(image_bytes, analysis.get("crop_box"), analysis)
        image_b64 = base64.b64encode(enhanced_bytes).decode()
        timings["Step 5 - image_edit"] = time.time() - t0
        logger.info("Step 5 done in %.2fs: local enhancement applied", timings["Step 5 - image_edit"])
        prompts["image_edit"] = None
        prompts["gaze_repair_pass"] = None
        usage["Step 5 - image_edit"] = None
        usage["Step 5b - gaze_repair_pass"] = None
        timings["Step 5b - gaze_repair_pass"] = None

        # Step 6 — skip QA
        qa = {
            "pass": True,
            "overall_status": "本機預覽",
            "score": None,
            "summary": "本機模式只做簡易裁切與色彩增強，未執行 AI 輸出 QA。",
            "checks": {
                "subject_preserved": True,
                "crop_safe": True,
                "not_overedited": True,
                "light_color_improved": True,
            },
            "warnings": [],
            "recommendation": "若要檢查生成式修圖品質，請開啟 AI 修圖。",
        }
        qa = _normalize_quality_check_result(qa)
        prompts["quality_check"] = None
        usage["Step 6 - quality_check"] = None
        timings["Step 6 - quality_check"] = None

    analysis = {**analysis, "prompts": prompts}
    analysis.pop("timings", None)
    analysis.pop("usage", None)

    extra_info = {
        "image_model": image_model if ai_photo_editing else None,
        "image_quality": edit_quality,
        "analysis_model": analysis_model,
        "single_pass_edit": single_pass_edit,
        "timings": timings,
    }
    if body_review_active:
        extra_info["body_reshape_edit_prompt"] = body_prompt
        extra_info["body_reshape_edit_prompts"] = [
            {
                "subject_index": item["subject_index"],
                "subject_label": item["subject_label"],
                "edit_prompt": item["edit_prompt"],
            }
            for item in body_items
        ]

    return {
        "result_image": image_b64,
        "result_image_data_url": f"data:image/png;base64,{image_b64}",
        "pre_edit_image": pre_edit_image_b64,
        "result_text": {
            "analysis": analysis,
            "quality_check": qa,
            "token_usage": usage,
            "extra-info": extra_info,
        },
    }


def run_pipeline(
    image_bytes: bytes,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
    image_model: str = "gpt-image-2",
    analysis_model: str = MODELS["analysis"],
    run_qa: bool = True,
    aspect_ratio: str = "auto",
    overwrite_prompt: bool = False,
    single_pass_edit: bool = True,
) -> dict:
    """Full pipeline: analyze_stage followed by finish_stage."""
    analysis = analyze_stage(image_bytes, aspect_ratio, analysis_model=analysis_model)
    return finish_stage(
        image_bytes, analysis, user_prompt, preset_key, quality, ai_photo_editing,
        image_model, analysis_model, run_qa, aspect_ratio, overwrite_prompt,
        single_pass_edit,
    )
