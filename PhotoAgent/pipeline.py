import base64
import inspect
import logging
import time
from functools import partial
from typing import Optional

from config import MODELS
from image_tools import (
    apply_derived_aspect_ratio,
    create_local_enhanced_bytes,
    get_image_dimensions,
    image_to_data_url,
)
from gemini_client import (
    edit_photo as edit_photo_gemini,
    edit_gaze_only as edit_gaze_only_gemini,
    NANO_BANANA_LITE_MODEL,
)
from mai_client import edit_photo as edit_photo_mai, MAI_IMAGE_26_MODEL
from openai_client import (
    analyze_photo,
    review_user_instruction,
    edit_photo,
    edit_gaze_only as edit_gaze_only_openai,
    quality_check_photo,
)
from prompts import (
    build_analysis_instruction,
    build_overall_analysis_instruction,
    build_portrait_qc_instruction,
    build_eye_repair_planning_instruction,
    build_body_reshape_planning_instruction,
    build_body_reshape_execution_plan,
    body_reshape_enabled,
    strip_body_reshape,
    eye_repair_targets,
    portrait_qc_persons,
    resolve_user_gaze_request,
    subject_issue_items,
    _disabled_issue_subject_indices,
)

from photo_agent.param_validation import DEFAULT_EDITOR_PROMPT_STRATEGY
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
# gpt-image-2 low also runs the gaze-only pass. Keyed the same way as
# EDIT_PHOTO_BY_MODEL for the same reason: a one-line config swap between
# providers while comparing which one repairs gaze more reliably.
GAZE_REPAIR_BY_MODEL = {
    "gpt-image-2": edit_gaze_only_openai,
    "gpt-image-1.5": partial(edit_gaze_only_openai, model="gpt-image-1.5"),
    "gpt-image-2.5-sunburst": partial(edit_gaze_only_openai, model="gpt-image-2.5-sunburst"),
    "gpt-image-2.5-flare": partial(edit_gaze_only_openai, model="gpt-image-2.5-flare"),
    "nano-banana-2": edit_gaze_only_gemini,
    "nano-banana-lite": partial(edit_gaze_only_gemini, model=NANO_BANANA_LITE_MODEL),
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


def _run_gaze_repair_pass(image_b64: str, analysis: dict, quality: str):
    """Step 5b: perform the approved gaze repair(s) as a second, isolated
    whole-image edit on top of Step 5's own output.

    Runs on whichever model config.MODELS["gaze_repair"] names (see
    GAZE_REPAIR_BY_MODEL above), independent of whichever model Step 5 itself
    used. Under the two-call path (single_pass_edit=False), Step 5 is
    told to leave gaze completely untouched even when a fix is approved (see
    build_edit_prompt's single_pass_edit-gated gaze_block in prompts.py) --
    this pass is the ONLY place any approved gaze redirection happens, not a
    touch-up on top of a partial Step 5 attempt. That split exists because
    on gpt-image-2 low specifically, a single call bundling the gaze fix
    together with exposure/crop/skin/style/etc reliably applies the coarse,
    whole-frame changes but often no-ops the fine, localized iris/pupil
    movement -- confirmed by comparing several before/after test photos
    where the analysis's edit_instruction was just as clear and specific in
    the cases that failed as in the ones that worked. A second, isolated
    call whose prompt contains nothing but the gaze fix gives the model's
    full attention to just this one change, for every approved subject at
    once -- which also sidesteps the multi-person matching problem a
    crop-based approach would have (no need to figure out which detected
    face is which named subject; the model sees the whole photo and every
    name at once, same as Step 5 does today).

    Under single_pass_edit=True this pass never runs at all (see
    finish_stage), so build_edit_prompt keeps the older bundled-attempt
    behavior for that path instead -- Step 5 is the only edit call, so it
    has to be the one that at least attempts the fix.

    An earlier version of this pass cropped to each face and pasted the
    edited crop back in. Workable in principle, but the crop's own edit call
    also regenerates its background (hair, collar, whatever the padding
    captured), and that regeneration didn't reliably match the base image
    closely enough to paste back invisibly -- it produced a doubled face and
    a warped background pattern on real test photos, even after restricting
    the paste to a re-detected eye region. A second whole-image call has no
    seam to paste and nothing separate to keep consistent: the model edits
    and returns the entire photo itself.

    Best-effort: the caller's try/except keeps the Step 5 image untouched if
    this fails, so it can only help, never regress a job.

    Returns (image_b64, prompt_text, usage) -- prompt_text and usage are None
    when there was no approved gaze repair to run, matching every other
    stage's "no GPT call made" convention (see finish_stage's prompts dict).

    Gated here on advanced_options["gaze_contact_detection"] -- the analysis
    stage itself no longer reads this toggle at all (it always runs full gaze
    detection, so each subject's gaze_issue reflects genuine judgement
    regardless of the toggle); this plain dict lookup, plus build_edit_prompt's
    own strip_disabled_topic_items call on correction_plan, are the only two
    places the toggle is actually enforced, both at edit time.

    Also skips (falls through to the empty-gaze_items return below) once
    every subject with an approved gaze fix has been individually opted out
    via _disabled_issue_subject_indices -- the same per-person "允許 AI 修正此
    項目" toggle build_edit_prompt's own blink/hair/gaze_items_to_fix already
    honor. Without this, a person the user explicitly excluded on the review
    screen would still get their gaze redirected here, since this pass
    rebuilds its own item list straight from analysis rather than reusing
    build_edit_prompt's already-filtered list.
    """
    if not (analysis.get("_advanced_options") or {}).get("gaze_contact_detection", True):
        return image_b64, None, None
    # resolve_user_gaze_request turns a manually-forced should_fix=false row
    # into a real repair target -- required here too, since this pass rebuilds
    # its own item list straight from analysis rather than reusing build_edit_
    # prompt's gaze_items_to_fix, and a forced item that only cleared that
    # other filter would otherwise never reach an actual edit call.
    gaze_disabled_indices = _disabled_issue_subject_indices(analysis, "gaze_issue")
    gaze_items = [
        resolve_user_gaze_request(item) for item in subject_issue_items(analysis, "gaze_issue")
        if (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in gaze_disabled_indices
    ]
    if not gaze_items:
        return image_b64, None, None

    run_edit_gaze_only = GAZE_REPAIR_BY_MODEL.get(MODELS["gaze_repair"], edit_gaze_only_gemini)
    result_bytes = base64.b64decode(image_b64)
    edited_bytes, prompt_text, usage = run_edit_gaze_only(result_bytes, gaze_items, quality=quality)
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


def _normalize_quality_check_result(result, analysis, execution_plan, *, status="completed"):
    """Normalize QA state and reconcile body details with actual endpoints.

    A model response is allowed to omit the new optional body detail while old
    fixtures/mocks are still in use, but an omitted target is never promoted to
    a successful check.  ``pass`` is deliberately nullable for skipped/error
    states so UI/statistics cannot treat an unexamined image as a pass.
    """
    qa = dict(result or {})
    plan = execution_plan if isinstance(execution_plan, dict) else {}
    # Keep the baseline QA payload untouched for requests that do not contain
    # any body-reshape review points.  The extra status/adherence fields belong
    # only to the body execution path and must not alter ordinary eye, hair,
    # smile, style, or colour results.
    # A 0% slider keeps the review point visible but is an explicit stop, not
    # an endpoint QA must inspect.  Only positive executable points opt into
    # body adherence normalization; ordinary features keep their legacy QA
    # shape when every body point is stopped.
    if not plan.get("executable_point_count"):
        return qa
    qa_status = qa.get("qa_status") if qa.get("qa_status") in {"completed", "skipped", "error"} else status
    qa["qa_status"] = qa_status
    checks = qa.get("checks")
    qa["checks"] = dict(checks) if isinstance(checks, dict) else {}
    warnings = qa.get("warnings")
    qa["warnings"] = list(warnings) if isinstance(warnings, list) else []
    expected = [
        entry for entry in plan.get("points") or []
        if entry.get("executable")
    ]
    if qa_status != "completed":
        qa["pass"] = None
        # A skipped/error run may still carry a legacy mock's boolean check
        # values.  They describe no completed inspection, so expose them as
        # unknown instead of allowing UI truthiness to present a pass/fail
        # claim for work that never ran.
        qa["checks"] = {key: None for key in qa["checks"]}
    elif not isinstance(qa.get("pass"), bool):
        qa["pass"] = None

    adherence = qa.get("body_adherence")
    adherence = list(adherence) if isinstance(adherence, list) else []
    indexed = {}
    for item in adherence:
        if not isinstance(item, dict):
            continue
        if (
            isinstance(item.get("subject_index"), bool)
            or not isinstance(item.get("subject_index"), int)
            or isinstance(item.get("source_key_point_index"), bool)
            or not isinstance(item.get("source_key_point_index"), int)
        ):
            continue
        key = (item.get("subject_index"), item.get("source_key_point_index"))
        if key not in indexed:
            indexed[key] = item
    normalized_adherence = []
    missing = False
    for entry in expected:
        key = (entry.get("subject_index"), entry.get("key_point_index"))
        item = dict(indexed.get(key) or {})
        if not item:
            missing = True
            item = {
                "subject_index": entry.get("subject_index"),
                "source_key_point_index": entry.get("key_point_index"),
                "endpoint": entry.get("canonical_text") or entry.get("raw_text") or "",
                "status": "not_evaluated",
                "measured_change_pct": None,
                "minimum_threshold_verified": None,
                "assessment": "visual assessment unavailable",
                "note": "QA did not return a result for this actual endpoint.",
            }
        else:
            # The request plan is authoritative for identity and endpoint
            # text.  Do not let a QA model's stale label/index redirect a
            # result to another person or make a duplicate look like a new
            # target.
            item["subject_index"] = entry.get("subject_index")
            item["source_key_point_index"] = entry.get("key_point_index")
            item["endpoint"] = entry.get("canonical_text") or entry.get("raw_text") or ""
            # No reliable contour/mask measurement is implemented in this
            # pipeline.  Keep these fields explicitly unknown even if a model
            # guesses a number; the requested target is not an achieved
            # percentage and must never be presented as one.
            item["measured_change_pct"] = None
            item["minimum_threshold_verified"] = None
            item["assessment"] = item.get("assessment") if isinstance(item.get("assessment"), str) else "visual assessment"
            item["note"] = item.get("note") if isinstance(item.get("note"), str) else ""
            if item.get("status") not in {"not_applicable", "not_evaluated", "passed", "failed", "unjudgeable"}:
                item["status"] = "not_evaluated"
                missing = True
        normalized_adherence.append(item)
    qa["body_adherence"] = normalized_adherence
    if qa_status == "completed" and expected:
        statuses = {item.get("status") for item in normalized_adherence}
        if missing or statuses & {"not_applicable", "not_evaluated", "failed", "unjudgeable"}:
            qa["pass"] = False
            qa["checks"]["user_intent_followed"] = False
            if qa.get("overall_status") in {None, "可展示", "通過"}:
                qa["overall_status"] = "需人工複查"
            warning = "body reshape QA did not verify every executable endpoint"
            if warning not in qa["warnings"]:
                qa["warnings"].append(warning)
        elif qa["checks"].get("user_intent_followed") is False:
            # A model cannot report an overall pass while explicitly saying
            # the user-approved body intent was not followed.
            qa["pass"] = False
    return qa


def analyze_stage(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = MODELS["analysis"]) -> dict:
    """Steps 1-2: read the photo and analyze it.

    The user's instruction is reviewed in finish_stage instead of here --
    it's collected on the analyze-preview screen, shown to the user only
    after this stage's result comes back, so there's nothing to review yet
    at this point.

    advanced_options: the Advanced Settings toggles dict (e.g.
    {"eye_closed_detection": False}). Only build_analysis_instruction reads it
    here, and only for calm_hair (an opt-in style request) -- the detection/
    repair toggles (eye_closed_detection, hair_mess_detection,
    gaze_contact_detection) no longer affect analysis at all; they're only
    enforced later, at edit time (see _run_gaze_repair_pass and
    build_edit_prompt's strip_disabled_topic_items). Still stamped onto
    analysis["_advanced_options"] below regardless, since those edit-time
    consumers read it off the analysis object rather than as their own
    argument.
    """

    # Step 1 — read
    logger.info("Step 1: image loaded (%d bytes)", len(image_bytes))

    # Step 2 — analyze
    t0 = time.time()
    logger.info("Step 2: analyzing photo (aspect_ratio=%s)", aspect_ratio)
    analysis, step2_usage = analyze_photo(image_bytes, aspect_ratio, advanced_options, analysis_model=analysis_model)
    if not body_reshape_enabled():
        # Keep the stage boundary clean even when an alternate analyzer or a
        # retry returns an analysis object carrying a stale body plan.
        analysis = strip_body_reshape(analysis)
    step2_duration = time.time() - t0
    logger.info("Step 2 done in %.2fs: subject=%s scene=%s", step2_duration, analysis.get("subject_category"), analysis.get("scene_category"))

    # Auto mode: the output ratio is decided here, from the crop rectangle the
    # analysis proposed, rather than by the analysis model itself (which is
    # asked not to pick one). A model-chosen label that disagreed with its own
    # crop geometry used to make the crop and the output canvas mutually
    # unsatisfiable -- see image_tools.derive_aspect_ratio_from_crop. Done
    # before the analysis leaves this stage so every downstream consumer, and
    # the JSON the user reviews, sees one already-consistent value.
    if aspect_ratio == "auto":
        img_w, img_h = get_image_dimensions(image_bytes)
        analysis = apply_derived_aspect_ratio(analysis, img_w, img_h)
        logger.info(
            "Step 2 aspect ratio derived locally: %s (crop measured %s)",
            analysis.get("recommended_aspect_ratio"), analysis.get("crop_box_measured_ratio"),
        )

    if analysis_model == "gpt-5.6-luna":
        # Two-pass split (see openai_client._analyze_photo_two_pass): analysis
        # already ran by this point, so subjects[] here is Call A's own
        # finalized result -- portrait_qc_persons re-derives the exact same
        # subject_index-tagged list Call B was actually given (the merge
        # never reorders or filters subjects[], so this reconstruction
        # matches what was actually sent).
        persons = portrait_qc_persons(analysis)
        repair_targets = eye_repair_targets(analysis)
        analysis["prompts"] = {
            "photo_analysis": build_overall_analysis_instruction(aspect_ratio, advanced_options),
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
    else:
        analysis["prompts"] = {
            "photo_analysis": build_analysis_instruction(aspect_ratio, advanced_options, analysis_model),
        }
    analysis["timings"] = {
        "Step 2 - photo_analysis": step2_duration,
    }
    analysis["usage"] = {
        "Step 2 - photo_analysis": step2_usage,
    }
    # Records which aspect-ratio preference was active when this analysis ran,
    # so a later edit under a *different* preference (e.g. user picked "keep
    # original" here but switches to "Auto" on the analyze-preview screen
    # before regenerating) knows recommended_aspect_ratio="original" was
    # forced by that earlier choice, not a genuine model judgment -- see
    # build_edit_prompt's use of this field.
    analysis["_aspect_ratio_pref_at_analysis"] = aspect_ratio
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
    body_plan = build_body_reshape_execution_plan(analysis)
    if body_plan.get("review_point_count"):
        analysis["_body_reshape_execution_plan"] = body_plan
    return analysis


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
) -> dict:
    """Steps 3, 5-6: review user instruction, then AI edit + QA (or local enhancement).

    There is no separate crop-validation step: the crop guidance from
    analyze_stage (crop_box, roi_box, protected_regions, ...) is passed
    straight to the edit model, which is instructed to perform that crop
    itself as part of the edit.

    single_pass_edit: skip Step 5b (see _run_gaze_repair_pass) and fall back
    to having Step 5's own edit call bundle any approved gaze fix in with
    everything else (build_edit_prompt's single_pass_edit-gated gaze_block,
    prompts.py). Under the two-call path (single_pass_edit=False), Step 5 is
    instead told to leave gaze completely untouched and Step 5b performs 100%
    of any approved gaze redirection as an isolated second pass -- so
    single_pass_edit (the default) trades that dedicated pass's reliability
    for a single edit-model round trip. That bundled call is only trusted to
    apply an approved gaze fix reliably on gpt-image-2 at medium+, so when
    single_pass_edit is on, image_model is gpt-image-2, quality is "low",
    and this photo has an approved gaze_issue, Step 5 below bumps its own
    edit call up to "medium" -- reported back as extra-info.image_quality --
    rather than silently keeping a combination known not to apply the fix
    reliably in one call. Requests with no approved gaze issue stay on "low".

    Returns: {
        result_image: str,          # base64 PNG (no data-URL prefix)
        result_image_data_url: str, # data:image/png;base64,...
        result_text: dict,          # analysis + quality_check + token_usage
        pre_edit_image: Optional[str], # base64 PNG, Step 5 output before Step 5b
                                     # overwrites it -- None unless Step 5b
                                     # actually ran (see pre_edit_image_b64 below)
    }
    """
    if not body_reshape_enabled():
        # finish_stage may be called with a saved analysis created before the
        # internal feature was disabled.  Strip it before any prompt, QA, or
        # result metadata is built so no downstream path can execute it.
        analysis = strip_body_reshape(analysis)
    prompts = dict(analysis.get("prompts") or {})
    timings = dict(analysis.get("timings") or {})
    usage = dict(analysis.get("usage") or {})

    # Step 3 — review user instruction (collected on the analyze-preview
    # screen, so it's only available now that this stage has started)
    t0 = time.time()
    logger.info("Step 3: reviewing user instruction (has_prompt=%s, overwrite_prompt=%s)", bool(user_prompt), overwrite_prompt)
    if overwrite_prompt and (user_prompt or "").strip():
        # "取代最終送給編輯模型的完整指令" checkbox (index.html, beside 補充指令):
        # the raw text becomes the WHOLE edit-model prompt verbatim (see
        # prompts.py's build_edit_prompt), so there is nothing here to
        # structure into priorities/prohibitions -- skip the LLM review call.
        user_instruction = {
            "has_user_direction": True,
            "normalized_user_direction": user_prompt.strip(),
            "overwrite_prompt": True,
        }
        review_prompt = None
        step3_usage = None
    else:
        user_instruction, review_prompt, step3_usage = review_user_instruction(user_prompt, analysis, analysis_model=analysis_model)
    has_body_review_points = any(
        isinstance(subject, dict)
        and isinstance(subject.get("body_reshape"), dict)
        and bool(
            subject.get("body_reshape", {}).get("key_points")
            or subject.get("body_reshape", {}).get("raw_key_points")
        )
        for subject in (analysis.get("subjects") or [])
    )
    if has_body_review_points:
        user_instruction = dict(user_instruction or {})
    if has_body_review_points and (user_prompt or "").strip():
        user_instruction["original_user_direction"] = user_prompt.strip()
    analysis = {**analysis, "user_instruction": user_instruction}
    analysis.pop("_body_reshape_execution_plan", None)
    body_execution_plan = build_body_reshape_execution_plan(analysis)
    if not ai_photo_editing and body_execution_plan.get("review_point_count"):
        body_execution_plan = dict(body_execution_plan)
        body_execution_plan["body_active"] = False
        body_execution_plan["no_executable_reason"] = "AI photo editing is disabled"
    body_review_active = bool(body_execution_plan.get("review_point_count"))
    if body_review_active:
        analysis["_body_reshape_execution_plan"] = body_execution_plan
    prompts["user_instruction_review"] = review_prompt
    usage["Step 3 - user_instruction_review"] = step3_usage
    timings["Step 3 - user_instruction_review"] = time.time() - t0
    logger.info("Step 3 done in %.2fs: has_direction=%s", timings["Step 3 - user_instruction_review"], user_instruction.get("has_user_direction"))

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
    promotion_reasons = []

    if ai_photo_editing:
        # Step 5 — AI edit
        t0 = time.time()
        # single_pass_edit's bundled call only reliably applies an approved
        # gaze fix on gpt-image-2 at medium+ (see _run_gaze_repair_pass's
        # docstring) -- bump up from the requested "low" here, but only when
        # this photo actually has one, since with no gaze fix to bundle
        # there's nothing that combination would fail to apply.
        if (
            body_reshape_enabled()
            and image_model == "gpt-image-2"
            and quality == "low"
            and body_execution_plan.get("body_active")
            and body_execution_plan.get("executable_point_count", 0)
        ):
            edit_quality = "medium"
            promotion_reasons.append("body_reshape_requires_medium")
        if single_pass_edit and image_model == "gpt-image-2" and quality == "low":
            gaze_fix_approved = any(
                item.get("should_fix") or item.get("_user_forced")
                for item in subject_issue_items(analysis, "gaze_issue")
            )
            if gaze_fix_approved:
                edit_quality = "medium"
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

        # Step 5b — gaze-only repair pass (best-effort; see
        # _run_gaze_repair_pass for scope and why it exists). Never allowed to
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
                image_b64, gaze_repair_prompt, gaze_repair_usage = _run_gaze_repair_pass(image_b64, analysis, quality)
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
                qa = _normalize_quality_check_result(
                    qa, analysis, body_execution_plan, status="completed"
                )
                prompts["quality_check"] = qa_prompt
                usage["Step 6 - quality_check"] = qa_usage
                timings["Step 6 - quality_check"] = time.time() - t0
                logger.info("Step 6 done in %.2fs: score=%s pass=%s", timings["Step 6 - quality_check"], qa.get("score"), qa.get("pass"))
            except Exception as exc:
                timings["Step 6 - quality_check"] = time.time() - t0
                logger.warning("Step 6 QA failed in %.2fs: %s", timings["Step 6 - quality_check"], exc)
                qa = _quality_check_fallback(exc)
                qa = _normalize_quality_check_result(
                    qa, analysis, body_execution_plan, status="error"
                )
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
            qa = _normalize_quality_check_result(
                qa, analysis, body_execution_plan, status="skipped"
            )
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
        qa = _normalize_quality_check_result(
            qa, analysis, body_execution_plan, status="skipped"
        )
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
        "timings": timings,
    }
    if body_review_active:
        extra_info.update({
            "requested_quality": quality,
            "effective_quality": edit_quality,
            "promotion_reason": ",".join(promotion_reasons) if promotion_reasons else None,
            "body_reshape_execution_plan": body_execution_plan,
            "body_reshape_execution": {
                key: body_execution_plan.get(key)
                for key in (
                    "body_active", "no_executable_reason", "review_point_count",
                    "selected_point_count", "executable_point_count", "excluded_point_count",
                    "disabled_point_count", "amount_zero_point_count", "duplicate_point_count", "conflict_point_count",
                    "user_override_point_count", "resolved_subject_count", "rendered_endpoint_count",
                    "planner_default_target_pct", "planner_min_target_pct", "planner_max_target_pct",
                    "slider_config",
                )
            },
        })

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
