import base64
import logging

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


def run_pipeline(
    image_bytes: bytes,
    user_prompt: str,
    preset_key: str,
    quality: str,
    ai_photo_editing: bool = True,
) -> dict:
    """
    Full 5-step pipeline.
    Returns: {
        result_image: str,          # base64 PNG (no data-URL prefix)
        result_image_data_url: str, # data:image/png;base64,...
        result_text: dict           # analysis + quality_check + usage
    }
    """

    # Step 1 — read
    logger.info("Step 1: image loaded (%d bytes)", len(image_bytes))

    # Step 2 — analyze
    logger.info("Step 2: analyzing photo")
    analysis = analyze_photo(image_bytes)
    logger.info("Step 2 done: subject=%s scene=%s", analysis.get("subject_category"), analysis.get("scene_category"))

    # Step 3 — review user instruction
    logger.info("Step 3: reviewing user instruction (has_prompt=%s)", bool(user_prompt))
    user_instruction = review_user_instruction(user_prompt, analysis)
    analysis = {**analysis, "user_instruction": user_instruction}
    logger.info("Step 3 done: has_direction=%s", user_instruction.get("has_user_direction"))

    # Step 4 — crop validation (conditional)
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
        analysis = validate_crop(
            image_bytes,
            analysis,
            risk=risk,
            force_validation=has_user_crop,
        )
        logger.info("Step 4 done: selected=%s", (analysis.get("crop_validation") or {}).get("selected_candidate_label"))
    else:
        logger.info("Step 4: crop validation skipped")

    if ai_photo_editing:
        # Step 5 — AI edit
        logger.info("Step 5: editing photo (preset=%s quality=%s)", preset_key, quality)
        edit_result = edit_photo(image_bytes, analysis, quality, preset_key)
        image_b64 = edit_result["image_b64"]
        logger.info("Step 5 done: image returned")

        # Step 6 — quality check
        logger.info("Step 6: running quality check")
        try:
            qa = quality_check_photo(image_bytes, image_b64, analysis)
            logger.info("Step 6 done: score=%s pass=%s", qa.get("score"), qa.get("pass"))
        except Exception as exc:
            logger.warning("Step 6 QA failed: %s", exc)
            qa = _quality_check_fallback(exc)

        usage = edit_result.get("usage")
    else:
        # Step 5 — local crop + basic enhancement (no AI API call)
        logger.info("Step 5: local enhancement only (ai_photo_editing=False)")
        enhanced_bytes = create_local_enhanced_bytes(image_bytes, analysis.get("crop_box"), analysis)
        image_b64 = base64.b64encode(enhanced_bytes).decode()
        logger.info("Step 5 done: local enhancement applied")

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

    return {
        "result_image": image_b64,
        "result_image_data_url": f"data:image/png;base64,{image_b64}",
        "result_text": {
            "analysis": analysis,
            "quality_check": qa,
            "usage": usage,
        },
    }
