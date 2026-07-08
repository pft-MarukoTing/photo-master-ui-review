import base64
import logging
import time

from image_tools import (
    normalize_crop_box,
    crop_validation_risk,
    user_direction_affects_crop,
    create_local_enhanced_bytes,
    image_to_data_url,
)
from openai_client import (
    analyze_photo,
    review_user_instruction,
    validate_crop,
    edit_photo,
    quality_check_photo,
)
from prompts import ANALYSIS_INSTRUCTION

logger = logging.getLogger(__name__)


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


def analyze_stage(image_bytes: bytes) -> dict:
    """Steps 1-2: read the photo and analyze it.

    The user's instruction is reviewed in finish_stage instead of here --
    it's collected on the analyze-preview screen, shown to the user only
    after this stage's result comes back, so there's nothing to review yet
    at this point.
    """

    # Step 1 — read
    logger.info("Step 1: image loaded (%d bytes)", len(image_bytes))

    # Step 2 — analyze
    t0 = time.time()
    logger.info("Step 2: analyzing photo")
    analysis = analyze_photo(image_bytes)
    step2_duration = time.time() - t0
    logger.info("Step 2 done in %.2fs: subject=%s scene=%s", step2_duration, analysis.get("subject_category"), analysis.get("scene_category"))

    analysis["prompts"] = {
        "photo_analysis": ANALYSIS_INSTRUCTION,
    }
    analysis["timings"] = {
        "photo_analysis": step2_duration,
    }
    return analysis


def finish_stage(
    image_bytes: bytes,
    analysis: dict,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
) -> dict:
    """Steps 4-6: crop validation, then AI edit + QA (or local enhancement).

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
    logger.info("Step 3: reviewing user instruction (has_prompt=%s)", bool(user_prompt))
    user_instruction, review_prompt = review_user_instruction(user_prompt, analysis)
    analysis = {**analysis, "user_instruction": user_instruction}
    prompts["user_instruction_review"] = review_prompt
    timings["user_instruction_review"] = time.time() - t0
    logger.info("Step 3 done in %.2fs: has_direction=%s", timings["user_instruction_review"], user_instruction.get("has_user_direction"))

    # Step 4 — crop validation (conditional)
    t0 = time.time()
    logger.info("Step 4: evaluating crop")
    planned_crop = normalize_crop_box(analysis.get("crop_box"), analysis)
    has_ai_candidate = any(
        (c.get("crop_box") or c.get("cropBox") or {}).get("enabled") is True
        for c in (analysis.get("crop_candidates") or [])
    )
    has_user_crop = user_direction_affects_crop(user_instruction, user_prompt)
    crop_risk = crop_validation_risk(analysis)

    if planned_crop or has_ai_candidate or has_user_crop:
        logger.info("Step 4: running crop validation (risk=%s, force=%s)", crop_risk.get("level"), has_user_crop)
        risk = {"level": "high", "reasons": ["user-crop-instruction"]} if has_user_crop else crop_risk
        analysis, crop_prompt = validate_crop(
            image_bytes,
            analysis,
            risk=risk,
            force_validation=has_user_crop,
        )
        prompts["crop_validation"] = crop_prompt
        timings["crop_validation"] = time.time() - t0
        logger.info("Step 4 done in %.2fs: selected=%s", timings["crop_validation"], (analysis.get("crop_validation") or {}).get("selected_candidate_label"))
    else:
        prompts["crop_validation"] = None
        timings["crop_validation"] = time.time() - t0
        logger.info("Step 4 done in %.2fs: crop validation skipped", timings["crop_validation"])

    if ai_photo_editing:
        # Step 5 — AI edit
        t0 = time.time()
        logger.info("Step 5: editing photo (preset=%s quality=%s)", preset_key, quality)
        edit_result, edit_prompt = edit_photo(image_bytes, analysis, quality, preset_key)
        prompts["image_edit"] = edit_prompt
        timings["image_edit"] = time.time() - t0
        image_b64 = edit_result["image_b64"]
        logger.info("Step 5 done in %.2fs: image returned", timings["image_edit"])

        # Step 6 — quality check
        t0 = time.time()
        logger.info("Step 6: running quality check")
        try:
            qa, qa_prompt = quality_check_photo(image_bytes, image_b64, analysis)
            prompts["quality_check"] = qa_prompt
            timings["quality_check"] = time.time() - t0
            logger.info("Step 6 done in %.2fs: score=%s pass=%s", timings["quality_check"], qa.get("score"), qa.get("pass"))
        except Exception as exc:
            timings["quality_check"] = time.time() - t0
            logger.warning("Step 6 QA failed in %.2fs: %s", timings["quality_check"], exc)
            qa = _quality_check_fallback(exc)
            prompts["quality_check"] = None

        usage = edit_result.get("usage")
    else:
        # Step 5 — local crop + basic enhancement (no AI API call)
        t0 = time.time()
        logger.info("Step 5: local enhancement only (ai_photo_editing=False)")
        enhanced_bytes = create_local_enhanced_bytes(image_bytes, analysis.get("crop_box"), analysis)
        image_b64 = base64.b64encode(enhanced_bytes).decode()
        timings["image_edit"] = time.time() - t0
        logger.info("Step 5 done in %.2fs: local enhancement applied", timings["image_edit"])
        prompts["image_edit"] = None

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
        timings["quality_check"] = None

    analysis = {**analysis, "prompts": prompts, "timings": timings}

    return {
        "result_image": image_b64,
        "result_image_data_url": f"data:image/png;base64,{image_b64}",
        "result_text": {
            "analysis": analysis,
            "quality_check": qa,
            "usage": usage,
        },
    }


def run_pipeline(
    image_bytes: bytes,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
) -> dict:
    """Full pipeline: analyze_stage followed by finish_stage."""
    analysis = analyze_stage(image_bytes)
    return finish_stage(image_bytes, analysis, user_prompt, preset_key, quality, ai_photo_editing)
