"""Microsoft Foundry MAI image-edit client used by PhotoAgent Step 5."""

import base64
import copy
import os

import backoff
import requests

from config import PRESETS, PRESET_PROMPTS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import (
    apply_aspect_ratio_preference,
    apply_framing_size,
    ensure_png_bytes,
    get_image_dimensions,
    output_geometry_warnings,
    resolve_crop_box_with_report,
)
from photo_agent.feature_resolver import build_compact_editor_prompt
from photo_agent.param_validation import DEFAULT_EDITOR_PROMPT_STRATEGY
from prompts import build_edit_prompt


MAI_IMAGE_26_MODEL = "MAI-Image-2.6"
DEFAULT_MAI_GENERATIONS_URL = (
    "https://pf-app-api-dev.services.ai.azure.com"
    "/mai/v1/images/generations"
)


def _azure_api_key() -> str:
    key = os.getenv("AZURE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Environment variable AZURE_API_KEY is not set",
            RESULT_UNKNOWN_INTERNAL_ERROR,
        )
    return key


def _mai_edits_url() -> str:
    """Resolve a base, generations, or edits endpoint to the edits endpoint."""
    endpoint = (
        os.getenv("AZURE_MAI_ENDPOINT", "").strip().strip('"').strip("'")
        or DEFAULT_MAI_GENERATIONS_URL
    ).rstrip("/")
    generations_suffix = "/mai/v1/images/generations"
    edits_suffix = "/mai/v1/images/edits"
    if endpoint.endswith(generations_suffix):
        return endpoint[: -len(generations_suffix)] + edits_suffix
    if endpoint.endswith(edits_suffix):
        return endpoint
    return endpoint + edits_suffix


def _deployment_name(model: str) -> str:
    """Azure expects the deployment name, which may differ from the UI ID."""
    return os.getenv("AZURE_MAI_DEPLOYMENT", "").strip() or model


def _safe_json(response) -> dict:
    try:
        return response.json()
    except Exception:
        return {}


def _error_message(data: dict, status_code: int) -> str:
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or f"HTTP {status_code}")
    if error:
        return str(error)
    return f"HTTP {status_code}"


class _RetryableApiError(RuntimeError):
    """Transient Azure MAI failure that is safe to retry."""


_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    _RetryableApiError,
)


@backoff.on_exception(
    backoff.expo,
    _RETRYABLE_EXCEPTIONS,
    max_tries=3,
    jitter=backoff.full_jitter,
)
def _post_image_edit(
    deployment: str,
    prompt_text: str,
    png_bytes: bytes,
    *,
    auto_aspect_ratio: bool,
) -> dict:
    response = requests.post(
        _mai_edits_url(),
        headers={"api-key": _azure_api_key()},
        files={"image": ("photo.png", png_bytes, "image/png")},
        data={
            "model": deployment,
            "prompt": prompt_text,
            "auto_aspect_ratio": "true" if auto_aspect_ratio else "false",
            "web_grounding": "false",
        },
        timeout=300,
    )
    data = _safe_json(response)
    if not response.ok:
        message = _error_message(data, response.status_code)
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise _RetryableApiError(f"Image edit failed (MAI): {message}")
        raise RuntimeError(f"Image edit failed (MAI): {message}")
    return data


def _effective_ratio_label(analysis: dict, image_width: int, image_height: int) -> str:
    label = str(
        analysis.get("recommended_aspect_ratio")
        or (analysis.get("crop_box") or {}).get("aspect_ratio")
        or "original"
    ).strip()
    return f"{image_width}:{image_height}" if label == "original" else label


def edit_photo(
    image_bytes: bytes,
    analysis: dict,
    quality: str = "medium",
    preset_key: str = "natural",
    revision_feedback: str = None,
    model: str = MAI_IMAGE_26_MODEL,
    aspect_ratio: str = "auto",
    single_pass_edit: bool = False,
):
    """Edit one source image through Azure MAI, matching other clients' result contract.

    MAI's edits endpoint does not accept the UI quality tiers, so ``quality``
    is accepted only to preserve PhotoAgent's shared editor-call signature.
    """
    del quality
    preset = PRESETS.get(preset_key) or PRESETS["natural"]
    preset_prompt = PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"]

    user_instruction = analysis.get("user_instruction") or {}
    user_direction = user_instruction.get("normalized_user_direction") or ""
    image_width, image_height = get_image_dimensions(image_bytes)

    size = (analysis.get("style_options") or {}).get("size")
    framed_analysis = apply_framing_size(analysis, size)
    framed_analysis = copy.deepcopy(
        apply_aspect_ratio_preference(framed_analysis, aspect_ratio)
    )
    effective_ratio_label = _effective_ratio_label(
        framed_analysis, image_width, image_height
    )
    framed_analysis["recommended_aspect_ratio"] = effective_ratio_label
    if framed_analysis.get("crop_box"):
        framed_analysis["crop_box"]["aspect_ratio"] = effective_ratio_label
    crop_plan = resolve_crop_box_with_report(
        framed_analysis.get("crop_box"),
        framed_analysis,
        image_width,
        image_height,
    )["cropBox"]

    raw_prompt, raw_prompt_sections = build_edit_prompt(
        framed_analysis,
        preset,
        preset_prompt,
        user_direction,
        crop_plan,
        revision_feedback,
        aspect_ratio_pref=aspect_ratio,
        source_size=(image_width, image_height),
        single_pass_edit=single_pass_edit,
        return_sections=True,
    )
    advanced_options = framed_analysis.get("_advanced_options") or {}
    prompt_strategy = (
        advanced_options.get("editor_prompt_strategy")
        or DEFAULT_EDITOR_PROMPT_STRATEGY
    )
    if user_instruction.get("overwrite_prompt"):
        prompt_strategy = "legacy"
    prompt_text = raw_prompt
    resolution_metadata = None
    if prompt_strategy == "resolved_compact":
        prompt_text, resolution_metadata = build_compact_editor_prompt(
            framed_analysis,
            preset_prompt,
            user_direction,
            crop_plan,
            preset_key=preset_key,
            include_gaze=single_pass_edit,
            revision_feedback=revision_feedback,
            raw_crop_instruction=raw_prompt_sections.get("crop_instruction"),
            raw_style_instruction=raw_prompt_sections.get("style_instruction"),
        )

    png_bytes = ensure_png_bytes(image_bytes)
    deployment = _deployment_name(model)
    data = _post_image_edit(
        deployment,
        prompt_text,
        png_bytes,
        # The edits API has no width/height fields. For Auto and named ratios,
        # let MAI select from the explicit framing/ratio instruction in the
        # prompt. Disable this only for "original", where the input canvas is
        # the authoritative aspect ratio.
        auto_aspect_ratio=aspect_ratio != "original",
    )
    image_b64 = (data.get("data") or [{}])[0].get("b64_json")
    if not image_b64:
        raise RuntimeError("Azure MAI response did not contain image data.")

    return {
        "image_b64": image_b64,
        "usage": {"model": model, "usage": data.get("usage")},
        "geometry_warnings": output_geometry_warnings(
            image_b64,
            effective_ratio_label,
            crop_plan,
            image_width,
            image_height,
        ),
        "raw_prompt": raw_prompt,
        "prompt_resolution": resolution_metadata,
        "prompt_strategy": prompt_strategy,
    }, prompt_text
