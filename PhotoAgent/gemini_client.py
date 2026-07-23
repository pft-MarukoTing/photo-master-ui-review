import base64
import os
from typing import Optional

import requests

from aiserver_utils.aescipher import AESCipher
from config import PRESETS, PRESET_PROMPTS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import get_image_dimensions, resolve_crop_box_with_report, apply_framing_size
from prompts import build_edit_prompt

GEMINI_GENERATE_CONTENT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# "Nano Banana 2"/"Nano Banana 2 Lite" are Google's public nicknames for these models.
NANO_BANANA_2_MODEL = "gemini-3.1-flash-image"
NANO_BANANA_LITE_MODEL = "gemini-3.1-flash-lite-image"

_QUALITY_TO_IMAGE_SIZE = {"low": "1K", "medium": "1K", "high": "2K"}


def _gemini_api_key() -> str:
    encrypted = os.getenv("GEMINI_API_KEY", "").strip()
    if not encrypted:
        raise RuntimeError("Environment variable GEMINI_API_KEY is not set", RESULT_UNKNOWN_INTERNAL_ERROR)
    try:
        return AESCipher().decrypt(encrypted)
    except Exception as e:
        raise RuntimeError(
            "GEMINI_API_KEY must be encrypted with aiserver_utils.aescipher.AESCipher",
            RESULT_UNKNOWN_INTERNAL_ERROR,
        ) from e


def _safe_json(resp) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


def _extract_inline_image(data: dict) -> Optional[str]:
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline_data = part.get("inlineData")
            if inline_data and inline_data.get("data"):
                return inline_data["data"]
    return None


# ── Step 5: image edit (Gemini / "Nano Banana 2") ──────────────────────────────

def edit_photo(
    image_bytes: bytes,
    analysis: dict,
    quality: str = "medium",
    preset_key: str = "natural",
    revision_feedback: str = None,
    model: str = NANO_BANANA_2_MODEL,
):
    """Returns (result, prompt) -- same contract as openai_client.edit_photo."""
    preset = PRESETS.get(preset_key) or PRESETS["natural"]
    preset_prompt = PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"]

    user_instruction = analysis.get("user_instruction") or {}
    user_direction = user_instruction.get("normalized_user_direction") or ""
    img_w, img_h = get_image_dimensions(image_bytes)

    # See openai_client.edit_photo: framing size (close/closer) drives the crop
    # geometry, not just the style-hint text.
    size = (analysis.get("style_options") or {}).get("size")
    framed_analysis = apply_framing_size(analysis, size)
    crop_plan = resolve_crop_box_with_report(framed_analysis.get("crop_box"), framed_analysis, img_w, img_h)["cropBox"]

    prompt_text = build_edit_prompt(framed_analysis, preset, preset_prompt, user_direction, crop_plan, revision_feedback)

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_bytes).decode()}},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"imageSize": _QUALITY_TO_IMAGE_SIZE.get(quality, "1K")},
        },
    }

    resp = requests.post(
        GEMINI_GENERATE_CONTENT_URL.format(model=model),
        headers={"x-goog-api-key": _gemini_api_key(), "Content-Type": "application/json"},
        json=payload,
        timeout=300,
    )
    data = _safe_json(resp)
    if not resp.ok:
        msg = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        raise RuntimeError(f"Image edit failed (Gemini): {msg}")

    b64 = _extract_inline_image(data)
    if not b64:
        raise RuntimeError("Gemini 回應沒有包含圖片資料。")

    return {"image_b64": b64, "usage": data.get("usageMetadata")}, prompt_text
