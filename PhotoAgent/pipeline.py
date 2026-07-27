import base64
import logging
import time
from functools import partial

from image_tools import (
    create_local_enhanced_bytes,
    image_to_data_url,
)
from gemini_client import edit_photo as edit_photo_gemini, NANO_BANANA_LITE_MODEL
from openai_client import (
    analyze_photo,
    review_user_instruction,
    edit_photo,
    quality_check_photo,
)
from prompts import build_analysis_instruction

EDIT_PHOTO_BY_MODEL = {
    "gpt-image-2": edit_photo,
    "gpt-image-1.5": partial(edit_photo, model="gpt-image-1.5"),
    "nano-banana-2": edit_photo_gemini,
    "nano-banana-lite": partial(edit_photo_gemini, model=NANO_BANANA_LITE_MODEL),
}

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


def analyze_stage(image_bytes: bytes, aspect_ratio: str = "auto") -> dict:
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
    logger.info("Step 2: analyzing photo (aspect_ratio=%s)", aspect_ratio)
    analysis = analyze_photo(image_bytes, aspect_ratio)
    step2_duration = time.time() - t0
    logger.info("Step 2 done in %.2fs: subject=%s scene=%s", step2_duration, analysis.get("subject_category"), analysis.get("scene_category"))

    analysis["prompts"] = {
        "photo_analysis": build_analysis_instruction(aspect_ratio),
    }
    analysis["timings"] = {
        "Step 2 - photo_analysis": step2_duration,
    }
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
    logger.info("Step 3: reviewing user instruction (has_prompt=%s)", bool(user_prompt))
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
) -> dict:
    """Full pipeline: analyze_stage followed by finish_stage."""
    analysis = analyze_stage(image_bytes, aspect_ratio)
    return finish_stage(image_bytes, analysis, user_prompt, preset_key, quality, ai_photo_editing, image_model, run_qa, aspect_ratio)
