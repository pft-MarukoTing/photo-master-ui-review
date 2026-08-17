import base64
import logging
import time
from functools import partial

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
from openai_client import (
    analyze_photo,
    review_user_instruction,
    edit_photo,
    edit_gaze_only as edit_gaze_only_openai,
    quality_check_photo,
)
from prompts import build_analysis_instruction

EDIT_PHOTO_BY_MODEL = {
    "gpt-image-2": edit_photo,
    "gpt-image-1.5": partial(edit_photo, model="gpt-image-1.5"),
    "nano-banana-2": edit_photo_gemini,
    "nano-banana-lite": partial(edit_photo_gemini, model=NANO_BANANA_LITE_MODEL),
}

# Step 5b's model is a separate choice from Step 5's (config.MODELS["gaze_repair"],
# not image_model) -- picking gpt-image-2 low for Step 5 doesn't have to mean
# gpt-image-2 low also runs the gaze-only pass. Keyed the same way as
# EDIT_PHOTO_BY_MODEL for the same reason: a one-line config swap between
# providers while comparing which one repairs gaze more reliably.
GAZE_REPAIR_BY_MODEL = {
    "gpt-image-2": edit_gaze_only_openai,
    "gpt-image-1.5": partial(edit_gaze_only_openai, model="gpt-image-1.5"),
    "nano-banana-2": edit_gaze_only_gemini,
    "nano-banana-lite": partial(edit_gaze_only_gemini, model=NANO_BANANA_LITE_MODEL),
}

logger = logging.getLogger(__name__)


def _run_gaze_repair_pass(image_b64: str, analysis: dict, quality: str):
    """Step 5b: redo the approved gaze repair(s) as a second, isolated
    whole-image edit on top of Step 5's own output.

    Runs on whichever model config.MODELS["gaze_repair"] names (see
    GAZE_REPAIR_BY_MODEL above), independent of whichever model Step 5 itself
    used: Step 5 bundles every gaze fix into one call together with
    exposure/crop/skin/style/etc, and on gpt-image-2 low specifically that
    call reliably applies the coarse, whole-frame changes but often no-ops
    the fine, localized iris/pupil movement -- confirmed by comparing several
    before/after test photos where the analysis's edit_instruction was just
    as clear and specific in the cases that failed as in the ones that
    worked. A second, isolated call whose prompt contains nothing but the
    gaze fix gives the model's full attention to just this one change, for
    every approved subject at once -- which also sidesteps the multi-person
    matching problem a crop-based approach would have (no need to figure out
    which detected face is which named subject; the model sees the whole
    photo and every name at once, same as Step 5 does today).

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

    Returns (image_b64, prompt_text) -- prompt_text is None when there was no
    approved gaze repair to run, matching every other stage's "no GPT call
    made" convention (see finish_stage's prompts dict).

    Gated a second time here on advanced_options["gaze_contact_detection"],
    not just left to the analysis prompt's own disabled-override (prompts.py's
    GAZE_CONTACT_DETECTION_DISABLED_OVERRIDE, which asks the analysis model to
    set should_fix=false on every item when the toggle is off). That is a
    request to an LLM, not a guarantee -- the whole rest of this file exists
    because analysis output doesn't always follow the rules it's given. A
    plain dict lookup here means the toggle holds even on the run where the
    analysis model doesn't comply.
    """
    if not (analysis.get("_advanced_options") or {}).get("gaze_contact_detection", True):
        return image_b64, None
    gaze_items = [
        item for item in (analysis.get("gaze_contact_issues") or [])
        if isinstance(item, dict) and item.get("should_fix")
    ]
    if not gaze_items:
        return image_b64, None

    run_edit_gaze_only = GAZE_REPAIR_BY_MODEL.get(MODELS["gaze_repair"], edit_gaze_only_gemini)
    result_bytes = base64.b64decode(image_b64)
    edited_bytes, prompt_text = run_edit_gaze_only(result_bytes, gaze_items, quality=quality)
    return base64.b64encode(edited_bytes).decode(), prompt_text


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


def analyze_stage(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None) -> dict:
    """Steps 1-2: read the photo and analyze it.

    The user's instruction is reviewed in finish_stage instead of here --
    it's collected on the analyze-preview screen, shown to the user only
    after this stage's result comes back, so there's nothing to review yet
    at this point.

    advanced_options: the Advanced Settings toggles dict (e.g.
    {"eye_closed_detection": False}) -- passed through opaquely to
    build_analysis_instruction, which is the only place that reads individual
    keys out of it.
    """

    # Step 1 — read
    logger.info("Step 1: image loaded (%d bytes)", len(image_bytes))

    # Step 2 — analyze
    t0 = time.time()
    logger.info("Step 2: analyzing photo (aspect_ratio=%s)", aspect_ratio)
    analysis = analyze_photo(image_bytes, aspect_ratio, advanced_options)
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

    analysis["prompts"] = {
        "photo_analysis": build_analysis_instruction(aspect_ratio, advanced_options),
    }
    analysis["timings"] = {
        "Step 2 - photo_analysis": step2_duration,
    }
    # Records which aspect-ratio preference was active when this analysis ran,
    # so a later edit under a *different* preference (e.g. user picked "keep
    # original" here but switches to "Auto" on the analyze-preview screen
    # before regenerating) knows recommended_aspect_ratio="original" was
    # forced by that earlier choice, not a genuine model judgment -- see
    # build_edit_prompt's use of this field.
    analysis["_aspect_ratio_pref_at_analysis"] = aspect_ratio
    # Same trick for the Advanced Settings toggles: build_edit_prompt needs the
    # calm-hair switch at step 5, and carrying it on the analysis rather than as
    # a finish_stage argument means the two-stage flow and the debug-UI /rerun
    # both hand it back automatically, with the stored analysis recording which
    # toggles that run actually used.
    analysis["_advanced_options"] = dict(advanced_options or {})
    return analysis


def finish_stage(
    image_bytes: bytes,
    analysis: dict,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
    image_model: str = "gpt-image-2",
    run_qa: bool = True,
    aspect_ratio: str = "auto",
    overwrite_prompt: bool = False,
) -> dict:
    """Steps 3, 5-6: review user instruction, then AI edit + QA (or local enhancement).

    There is no separate crop-validation step: the crop guidance from
    analyze_stage (crop_box, roi_box, protected_regions, ...) is passed
    straight to the edit model, which is instructed to perform that crop
    itself as part of the edit.

    Returns: {
        result_image: str,          # base64 PNG (no data-URL prefix)
        result_image_data_url: str, # data:image/png;base64,...
        result_text: dict           # analysis + quality_check + usage
    }
    """
    prompts = dict(analysis.get("prompts") or {})
    timings = dict(analysis.get("timings") or {})

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
    else:
        user_instruction, review_prompt = review_user_instruction(user_prompt, analysis)
    analysis = {**analysis, "user_instruction": user_instruction}
    prompts["user_instruction_review"] = review_prompt
    timings["Step 3 - user_instruction_review"] = time.time() - t0
    logger.info("Step 3 done in %.2fs: has_direction=%s", timings["Step 3 - user_instruction_review"], user_instruction.get("has_user_direction"))

    prompts["crop_validation"] = None
    timings["Step 4 - crop_validation"] = None

    if ai_photo_editing:
        # Step 5 — AI edit
        t0 = time.time()
        logger.info("Step 5: editing photo (preset=%s quality=%s model=%s)", preset_key, quality, image_model)
        run_edit_photo = EDIT_PHOTO_BY_MODEL.get(image_model, edit_photo)
        edit_result, edit_prompt = run_edit_photo(image_bytes, analysis, quality, preset_key, aspect_ratio=aspect_ratio)
        prompts["image_edit"] = edit_prompt
        timings["Step 5 - image_edit"] = time.time() - t0
        image_b64 = edit_result["image_b64"]
        logger.info("Step 5 done in %.2fs: image returned", timings["Step 5 - image_edit"])

        # Step 5b — gaze-only repair pass (best-effort; see
        # _run_gaze_repair_pass for scope and why it exists). Never allowed to
        # fail the job -- worst case is the Step 5 image passes through as-is.
        t0 = time.time()
        try:
            image_b64, gaze_repair_prompt = _run_gaze_repair_pass(image_b64, analysis, quality)
            prompts["gaze_repair_pass"] = gaze_repair_prompt
            timings["Step 5b - gaze_repair_pass"] = time.time() - t0
            logger.info("Step 5b done in %.2fs", timings["Step 5b - gaze_repair_pass"])
        except Exception as exc:
            prompts["gaze_repair_pass"] = None
            timings["Step 5b - gaze_repair_pass"] = time.time() - t0
            logger.warning("Step 5b gaze repair pass failed in %.2fs, keeping Step 5 output: %s", timings["Step 5b - gaze_repair_pass"], exc)

        # Step 6 — quality check (debug-ui escape hatch: skip the QA call
        # entirely when run_qa=False, e.g. to save time/cost while iterating
        # on the edit step alone)
        t0 = time.time()
        if run_qa:
            logger.info("Step 6: running quality check")
            try:
                qa, qa_prompt = quality_check_photo(image_bytes, image_b64, analysis)
                prompts["quality_check"] = qa_prompt
                timings["Step 6 - quality_check"] = time.time() - t0
                logger.info("Step 6 done in %.2fs: score=%s pass=%s", timings["Step 6 - quality_check"], qa.get("score"), qa.get("pass"))
            except Exception as exc:
                timings["Step 6 - quality_check"] = time.time() - t0
                logger.warning("Step 6 QA failed in %.2fs: %s", timings["Step 6 - quality_check"], exc)
                qa = _quality_check_fallback(exc)
                prompts["quality_check"] = None
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
            prompts["quality_check"] = None
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

        usage = edit_result.get("usage")
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
        usage = None
        prompts["quality_check"] = None
        timings["Step 6 - quality_check"] = None

    analysis = {**analysis, "prompts": prompts}
    analysis.pop("timings", None)

    return {
        "result_image": image_b64,
        "result_image_data_url": f"data:image/png;base64,{image_b64}",
        "result_text": {
            "analysis": analysis,
            "quality_check": qa,
            "usage": usage,
            "extra-info": {
                "image_model": image_model if ai_photo_editing else None,
                "image_quality": quality,
                "timings": timings,
            },
        },
    }


def run_pipeline(
    image_bytes: bytes,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
    image_model: str = "gpt-image-2",
    run_qa: bool = True,
    aspect_ratio: str = "auto",
    overwrite_prompt: bool = False,
) -> dict:
    """Full pipeline: analyze_stage followed by finish_stage."""
    analysis = analyze_stage(image_bytes, aspect_ratio)
    return finish_stage(image_bytes, analysis, user_prompt, preset_key, quality, ai_photo_editing, image_model, run_qa, aspect_ratio, overwrite_prompt)
