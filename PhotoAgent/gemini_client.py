import base64
import copy
import math
import os
from typing import Optional, Tuple

import backoff
import requests

from aiserver_utils.aescipher import AESCipher
from config import PRESETS, PRESET_PROMPTS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import (
    get_image_dimensions,
    resolve_crop_box_with_report,
    apply_framing_size,
    apply_aspect_ratio_preference,
    output_geometry_warnings,
    ensure_png_bytes,
)
from prompts import build_edit_prompt, build_gaze_only_edit_prompt
from photo_agent.feature_resolver import build_compact_editor_prompt
from photo_agent.param_validation import DEFAULT_EDITOR_PROMPT_STRATEGY

GEMINI_GENERATE_CONTENT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# "Nano Banana 2"/"Nano Banana 2 Lite" are Google's public nicknames for these models.
NANO_BANANA_2_MODEL = "gemini-3.1-flash-image"
NANO_BANANA_LITE_MODEL = "gemini-3.1-flash-lite-image"

_QUALITY_TO_IMAGE_SIZE = {"low": "1K", "medium": "1K", "high": "2K"}

# Ratios accepted by the Gemini 3.1 image models.
_GEMINI_COMMON_ASPECT_RATIOS = {
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9",
}
_GEMINI_FLASH_EXTRA_ASPECT_RATIOS = {"1:4", "4:1", "1:8", "8:1"}
_AUTO_ASPECT_RATIOS = _GEMINI_COMMON_ASPECT_RATIOS | _GEMINI_FLASH_EXTRA_ASPECT_RATIOS

# Documented 1K raster dimensions. Higher supported sizes are integer
# multiples, so these dimensions also define the exact physical output ratio
# at 2K/4K. Several named ratios are necessarily approximate on the pixel
# grid (for example, API label 4:3 produces 1200x896).
_GEMINI_1K_OUTPUT_DIMENSIONS = {
    "1:1": (1024, 1024),
    "1:4": (512, 2048),
    "1:8": (384, 3072),
    "2:3": (848, 1264),
    "3:2": (1264, 848),
    "3:4": (896, 1200),
    "4:1": (2048, 512),
    "4:3": (1200, 896),
    "4:5": (928, 1152),
    "5:4": (1152, 928),
    "8:1": (3072, 384),
    "9:16": (768, 1376),
    "16:9": (1376, 768),
    "21:9": (1584, 672),
}


def _analysis_aspect_ratio_label(analysis: dict) -> str:
    label = str(analysis.get("recommended_aspect_ratio") or (analysis.get("crop_box") or {}).get("aspect_ratio") or "original").strip()
    if label == "original":
        return "original"
    return label if label in _AUTO_ASPECT_RATIOS else "original"


def _ratio_dimensions(label: str) -> Tuple[int, int]:
    left, right = label.split(":", 1)
    return int(left), int(right)


def _nearest_ratio_label(target: float) -> str:
    return min(
        _AUTO_ASPECT_RATIOS,
        key=lambda label: abs(math.log(target / (_ratio_dimensions(label)[0] / _ratio_dimensions(label)[1]))),
    )


def _effective_aspect_ratio_label(analysis: dict, image_width: int, image_height: int) -> str:
    """Return one concrete, model-supported output ratio.

    Auto uses the analysis recommendation when the selected Gemini model
    accepts it. "original" and invalid labels are snapped to the closest
    supported ratio. This is necessarily an approximation because Gemini
    exposes a closed aspect-ratio enum.
    """
    requested = _analysis_aspect_ratio_label(analysis)
    supported = _AUTO_ASPECT_RATIOS
    if requested in supported:
        return requested

    if requested == "original":
        target = (image_width / image_height) if image_width and image_height else 1.0
    else:
        ratio_width, ratio_height = _ratio_dimensions(requested)
        target = ratio_width / ratio_height

    return _nearest_ratio_label(target)


def _image_size_for_model(quality: str, model: str) -> str:
    # Gemini 3.1 Flash Lite Image supports 1K output only.
    if model == NANO_BANANA_LITE_MODEL:
        return "1K"
    return _QUALITY_TO_IMAGE_SIZE.get(quality, "1K")


def _output_geometry_label(api_aspect_ratio: str) -> str:
    width, height = _GEMINI_1K_OUTPUT_DIMENSIONS[api_aspect_ratio]
    return f"{width}:{height}"


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


class _RetryableApiError(RuntimeError):
    """A transient Gemini API failure (5xx) -- distinct from a 4xx rejection,
    which will fail identically on retry since the problem is the request's
    content, not the network. Mirrors openai_client.py's same distinction."""


_RETRYABLE_EXCEPTIONS = (requests.exceptions.Timeout, requests.exceptions.ConnectionError, _RetryableApiError)


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
    aspect_ratio: str = "auto",
    single_pass_edit: bool = False,
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
    # User's explicit aspect-ratio choice overrides the analysis's own
    # recommendation ("original" only -- "auto" passes through unchanged).
    # Work on a deep copy because this function writes the effective provider
    # ratio back into the crop/prompt context. The original analysis is shared
    # by QA and by every independent RUN.
    framed_analysis = copy.deepcopy(apply_aspect_ratio_preference(framed_analysis, aspect_ratio))
    # Resolve one effective ratio and reuse it for crop and output canvas.
    image_config = {"imageSize": _image_size_for_model(quality, model)}
    api_ratio_label = _effective_aspect_ratio_label(framed_analysis, img_w, img_h)
    geometry_ratio_label = _output_geometry_label(api_ratio_label)
    framed_analysis["recommended_aspect_ratio"] = geometry_ratio_label
    if framed_analysis.get("crop_box"):
        framed_analysis["crop_box"]["aspect_ratio"] = geometry_ratio_label
    crop_plan = resolve_crop_box_with_report(framed_analysis.get("crop_box"), framed_analysis, img_w, img_h)["cropBox"]
    raw_prompt, raw_prompt_sections = build_edit_prompt(
        framed_analysis, preset, preset_prompt, user_direction, crop_plan,
        revision_feedback, aspect_ratio_pref=aspect_ratio,
        source_size=(img_w, img_h), single_pass_edit=single_pass_edit,
        return_sections=True,
    )
    advanced_options = framed_analysis.get("_advanced_options") or {}
    prompt_strategy = advanced_options.get("editor_prompt_strategy") or DEFAULT_EDITOR_PROMPT_STRATEGY
    if user_instruction.get("overwrite_prompt"):
        prompt_strategy = "legacy"
    prompt_text = raw_prompt
    resolution_metadata = None
    if prompt_strategy == "resolved_compact":
        prompt_text, resolution_metadata = build_compact_editor_prompt(
            framed_analysis, preset_prompt, user_direction, crop_plan,
            preset_key=preset_key, include_gaze=single_pass_edit,
            revision_feedback=revision_feedback,
            raw_crop_instruction=raw_prompt_sections.get("crop_instruction"),
            raw_style_instruction=raw_prompt_sections.get("style_instruction"),
        )
    image_config["aspectRatio"] = api_ratio_label

    # Converted once, outside the retry-wrapped call, so a retry doesn't
    # needlessly re-encode the same bytes.
    png_b64 = base64.b64encode(ensure_png_bytes(image_bytes)).decode()
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inlineData": {"mimeType": "image/png", "data": png_b64}},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": image_config,
        },
    }

    data = _post_generate_content(model, payload)

    b64 = _extract_inline_image(data)
    if not b64:
        raise RuntimeError("Gemini 回應沒有包含圖片資料。")

    return {
        "image_b64": b64,
        "usage": {"model": model, "usage": data.get("usageMetadata")},
        # geometry_ratio_label, not api_ratio_label: Gemini's named ratios are
        # approximate on its own pixel grid (4:3 rasters as 1200x896), so the
        # physical ratio is what the output can actually be held to.
        "geometry_warnings": output_geometry_warnings(b64, geometry_ratio_label, crop_plan, img_w, img_h),
        "raw_prompt": raw_prompt,
        "prompt_resolution": resolution_metadata,
        "prompt_strategy": prompt_strategy,
    }, prompt_text


# ── Step 5b: gaze-only repair pass (Gemini / "Nano Banana") ────────────────────

def edit_gaze_only(image_bytes: bytes, gaze_items: list, quality: str = "low", model: str = NANO_BANANA_2_MODEL):
    """Runs the approved gaze repair(s) as a second, isolated full-image edit
    on top of Step 5's own output (see pipeline._run_gaze_repair_pass for why
    this is a second whole-image call rather than a crop-and-paste). Gemini
    counterpart to openai_client's OpenAI-only Step 5 pipeline -- used as the
    Step 5b model regardless of which model ran Step 5, since gpt-image-2 low
    was the one observed to no-op or under-apply this specific fix.

    Requests the same aspect ratio as the input (nearest Gemini-supported
    label) rather than anything analysis-recommended: this call must not
    re-crop or reframe what Step 5 already finished.

    Returns (image_bytes, prompt_text, usage) -- usage is {"model", "usage"};
    same shape as openai_client.edit_gaze_only, so pipeline.py can surface
    this stage's prompt in the "Sent to GPT" debug view alongside every other
    one.
    """
    prompt_text = build_gaze_only_edit_prompt(gaze_items)
    img_w, img_h = get_image_dimensions(image_bytes)
    api_ratio_label = _nearest_ratio_label(img_w / img_h if img_w and img_h else 1.0)
    image_config = {"imageSize": _image_size_for_model(quality, model), "aspectRatio": api_ratio_label}

    png_b64 = base64.b64encode(ensure_png_bytes(image_bytes)).decode()
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inlineData": {"mimeType": "image/png", "data": png_b64}},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": image_config,
        },
    }

    data = _post_generate_content(model, payload)
    b64 = _extract_inline_image(data)
    if not b64:
        raise RuntimeError("Gemini 回應沒有包含圖片資料。")
    return base64.b64decode(b64), prompt_text, {"model": model, "usage": data.get("usageMetadata")}


@backoff.on_exception(backoff.expo, _RETRYABLE_EXCEPTIONS, max_tries=3, jitter=backoff.full_jitter)
def _post_generate_content(model: str, payload: dict) -> dict:
    resp = requests.post(
        GEMINI_GENERATE_CONTENT_URL.format(model=model),
        headers={"x-goog-api-key": _gemini_api_key(), "Content-Type": "application/json"},
        json=payload,
        timeout=300,
    )
    data = _safe_json(resp)
    if not resp.ok:
        msg = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        if resp.status_code >= 500:
            raise _RetryableApiError(f"Image edit failed (Gemini): {msg}")
        raise RuntimeError(f"Image edit failed (Gemini): {msg}")
    return data
