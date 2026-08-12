import copy
import json
import os
import re
from typing import Tuple

import backoff
import requests

from config import MODELS, PRESETS, PRESET_PROMPTS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import (
    create_preview_bytes,
    image_to_data_url,
    get_image_dimensions,
    resolve_crop_box_with_report,
    apply_framing_size,
    apply_aspect_ratio_preference,
    nearest_openai_edit_size,
    supports_arbitrary_openai_size,
    openai_edit_size_for_ratio,
    output_geometry_warnings,
    ensure_png_bytes,
)
from prompts import (
    ANALYSIS_INSTRUCTION,
    build_analysis_instruction,
    build_user_instruction_review_prompt,
    build_edit_prompt,
    build_quality_check_prompt,
    style_option_entries,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_IMAGES_EDITS_URL = "https://api.openai.com/v1/images/edits"

ANALYSIS_MODEL = MODELS["analysis"]
IMAGE_MODEL = MODELS["image"]

# Ratios the analysis prompt may return in auto mode. GPT Image 2 can express
# all of these with an arbitrary WIDTHxHEIGHT size (subject to its 3:1 API
# limit); older GPT Image models are snapped to their fixed size enum below.
_AUTO_ASPECT_RATIOS = {
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1",
}


def _analysis_aspect_ratio_label(analysis: dict) -> str:
    label = str(analysis.get("recommended_aspect_ratio") or (analysis.get("crop_box") or {}).get("aspect_ratio") or "original").strip()
    if label == "original":
        return "original"
    return label if label in _AUTO_ASPECT_RATIOS else "original"


def _ratio_dimensions(label: str) -> Tuple[int, int]:
    left, right = label.split(":", 1)
    return int(left), int(right)


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _openai_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Environment variable OPENAI_API_KEY is not set", RESULT_UNKNOWN_INTERNAL_ERROR)
    return key


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_openai_api_key()}"}


class _RetryableApiError(RuntimeError):
    """A transient OpenAI API failure (5xx) -- distinct from a 4xx rejection
    (e.g. the safety system blocking a request), which will fail identically
    on retry since the problem is the request's content, not the network."""


_RETRYABLE_EXCEPTIONS = (requests.exceptions.Timeout, requests.exceptions.ConnectionError, _RetryableApiError)


@backoff.on_exception(backoff.expo, _RETRYABLE_EXCEPTIONS, max_tries=3, jitter=backoff.full_jitter)
def _post_json(url: str, body: dict, label: str, timeout: int = 120) -> dict:
    resp = requests.post(
        url,
        headers={**_auth_headers(), "Content-Type": "application/json"},
        # str data must be bytes before hitting http.client, or it tries
        # (and fails) to encode non-ASCII chars as Latin-1.
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    data = _safe_json(resp)
    if not resp.ok:
        msg = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        if resp.status_code >= 500:
            raise _RetryableApiError(f"{label}: {msg}")
        raise RuntimeError(f"{label}: {msg}")
    return data


def _safe_json(resp) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


def _extract_output_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    chunks = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text") and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[\}\]])", r"\1", text)


def _parse_json_from_text(text: str) -> dict:
    candidates = [str(text or "")]
    m = re.search(r"\{[\s\S]*\}", str(text or ""))
    if m:
        candidates.append(m.group(0))

    last_error = None
    for candidate in candidates:
        for variant in (candidate, _strip_trailing_commas(candidate)):
            for strict in (True, False):
                try:
                    return json.loads(variant, strict=strict)
                except Exception as e:
                    last_error = e

    raise ValueError("AI 回應沒有包含有效 JSON。") from last_error


def _responses_payload(model: str, content: list, text_format: dict = None) -> dict:
    payload = {"model": model, "input": [{"role": "user", "content": content}]}
    if text_format:
        payload["text"] = {"format": text_format}
    return payload


# ── Step 2: photo analysis ─────────────────────────────────────────────────────

def analyze_photo(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None) -> dict:
    preview = create_preview_bytes(image_bytes)
    data_url = image_to_data_url(preview, "image/jpeg")

    payload = _responses_payload(ANALYSIS_MODEL, [
        {"type": "input_text", "text": build_analysis_instruction(aspect_ratio, advanced_options)},
        {"type": "input_image", "image_url": data_url},
    ])
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Photo analysis failed")
    return _parse_json_from_text(_extract_output_text(data))


# ── Step 3: user instruction review ───────────────────────────────────────────

def review_user_instruction(user_direction: str, analysis: dict):
    """Returns (result, prompt) -- prompt is None when no GPT call was made
    (no user direction to review)."""
    trimmed = (user_direction or "").strip()
    if not trimmed:
        return {
            "has_user_direction": False,
            "user_intent_summary": "",
            "normalized_user_direction": "",
            "explicit_priorities": [],
            "explicit_prohibitions": [],
            "unspecified_agent_freedom": ["可依照片分析保守改善光線、色彩、清晰度、構圖與干擾。"],
            "crop_or_framing_requested": False,
            "crop_guidance": "使用者未指定裁切，Agent 可依分析保守判斷。",
            "qa_focus": [],
            "conflicts": [],
            "warning": "",
        }, None

    prompt = build_user_instruction_review_prompt(trimmed, analysis)
    payload = _responses_payload(ANALYSIS_MODEL, [{"type": "input_text", "text": prompt}])
    data = _post_json(OPENAI_RESPONSES_URL, payload, "User instruction review failed")
    result = _parse_json_from_text(_extract_output_text(data))
    result["has_user_direction"] = True
    return result, prompt


# ── Step 5: image edit ─────────────────────────────────────────────────────────

def edit_photo(
    image_bytes: bytes,
    analysis: dict,
    quality: str = "medium",
    preset_key: str = "natural",
    revision_feedback: str = None,
    model: str = IMAGE_MODEL,
    aspect_ratio: str = "auto",
):
    """Returns (result, prompt)."""
    preset = PRESETS.get(preset_key) or PRESETS["natural"]
    preset_prompt = PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"]

    user_instruction = analysis.get("user_instruction") or {}
    user_direction = user_instruction.get("normalized_user_direction") or ""
    img_w, img_h = get_image_dimensions(image_bytes)

    # Let the selected framing size (close/closer) drive the crop geometry, not
    # just the style-hint text. For closer this rewrites crop_box onto a single
    # subject and clears the other protected regions so the resolver does not
    # re-expand the frame back over everyone. wide/wider/unset pass through.
    size = (analysis.get("style_options") or {}).get("size")
    framed_analysis = apply_framing_size(analysis, size)
    # User's explicit aspect-ratio choice overrides the analysis's own
    # recommendation ("original" only -- "auto" passes through unchanged).
    # The effective output ratio is written into this working copy so crop
    # geometry and prompt text agree with the API request. Do not mutate the
    # shared analysis object: it is reused by QA and by independent RUNs.
    framed_analysis = copy.deepcopy(apply_aspect_ratio_preference(framed_analysis, aspect_ratio))
    # Resolve one effective ratio and reuse it for crop and output canvas.
    if aspect_ratio == "original":
        ratio_w, ratio_h = img_w, img_h
        effective_ratio_label = "original"
    else:
        effective_ratio_label = _analysis_aspect_ratio_label(framed_analysis)
        if effective_ratio_label == "original":
            ratio_w, ratio_h = img_w, img_h
        else:
            ratio_w, ratio_h = _ratio_dimensions(effective_ratio_label)
    size_param = openai_edit_size_for_ratio(ratio_w, ratio_h) if supports_arbitrary_openai_size(model) else nearest_openai_edit_size(ratio_w, ratio_h)
    if "x" in size_param:
        output_w, output_h = (int(v) for v in size_param.lower().split("x", 1))
        effective_ratio_label = f"{output_w}:{output_h}"
    framed_analysis["recommended_aspect_ratio"] = effective_ratio_label
    if framed_analysis.get("crop_box"):
        framed_analysis["crop_box"]["aspect_ratio"] = effective_ratio_label
    crop_plan = resolve_crop_box_with_report(framed_analysis.get("crop_box"), framed_analysis, img_w, img_h)["cropBox"]
    prompt_text = build_edit_prompt(framed_analysis, preset, preset_prompt, user_direction, crop_plan, revision_feedback, aspect_ratio_pref=aspect_ratio, source_size=(img_w, img_h))

    # Converted once, outside the retry-wrapped call, so a retry doesn't
    # needlessly re-encode the same bytes.
    png_bytes = ensure_png_bytes(image_bytes)
    data = _post_image_edit(model, prompt_text, quality, size_param, png_bytes)

    b64 = (data.get("data") or [{}])[0].get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI 回應沒有包含圖片資料。")

    return {
        "image_b64": b64,
        "usage": data.get("usage"),
        "geometry_warnings": output_geometry_warnings(b64, effective_ratio_label, crop_plan, img_w, img_h),
    }, prompt_text


@backoff.on_exception(backoff.expo, _RETRYABLE_EXCEPTIONS, max_tries=3, jitter=backoff.full_jitter)
def _post_image_edit(model: str, prompt_text: str, quality: str, size_param: str, png_bytes: bytes) -> dict:
    resp = requests.post(
        OPENAI_IMAGES_EDITS_URL,
        headers=_auth_headers(),
        files={"image": ("photo.png", png_bytes, "image/png")},
        data={"model": model, "prompt": prompt_text, "quality": quality, "size": size_param},
        timeout=300,
    )
    data = _safe_json(resp)
    if not resp.ok:
        msg = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        if resp.status_code >= 500:
            raise _RetryableApiError(f"Image edit failed: {msg}")
        raise RuntimeError(f"Image edit failed: {msg}")
    return data


# ── Step 6: quality check ──────────────────────────────────────────────────────

_QA_CHECK_KEYS = [
    "subject_preserved",
    "user_intent_followed",
    "crop_safe",
    "protected_regions_safe",
    "framing_intent_respected",
    "aspect_ratio_feels_intentional",
    "composition_method_matches",
    "empty_space_has_function",
    "alignment_stable_or_intentional",
    "distractions_reassessed_after_crop",
    "scene_preserved",
    "frame_breathing_room_ok",
    "not_overedited",
    "light_color_improved",
]

QUALITY_CHECK_SCHEMA = {
    "type": "json_schema",
    "name": "quality_check_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "pass": {"type": "boolean"},
            "overall_status": {"type": "string"},
            "score": {"type": ["number", "null"]},
            "failure_level": {"type": "string", "enum": ["none", "minor", "borderline", "major", "severe"]},
            "score_reason": {"type": "string"},
            "summary": {"type": "string"},
            "checks": {
                "type": "object",
                "properties": {key: {"type": ["boolean", "null"]} for key in _QA_CHECK_KEYS},
                "required": _QA_CHECK_KEYS,
                "additionalProperties": False,
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
            "recommendation": {"type": "string"},
            "recipe_adherence": {
                "type": "object",
                "properties": {
                    "score": {"type": ["number", "null"]},
                    "reason": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "followed": {"type": ["boolean", "null"]},
                                "note": {"type": "string"},
                            },
                            "required": ["item", "followed", "note"],
                            "additionalProperties": False,
                        },
                    },
                    "style_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "followed": {"type": ["boolean", "null"]},
                                "note": {"type": "string"},
                            },
                            "required": ["item", "followed", "note"],
                            "additionalProperties": False,
                        },
                    },
                    "rejected_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "violated": {"type": ["boolean", "null"]},
                                "note": {"type": "string"},
                            },
                            "required": ["item", "violated", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["score", "reason", "items", "style_items", "rejected_items"],
                "additionalProperties": False,
            },
        },
        "required": [
            "pass", "overall_status", "score", "failure_level", "score_reason",
            "summary", "checks", "warnings", "recommendation", "recipe_adherence",
        ],
        "additionalProperties": False,
    },
}


def _relabel_style_items(result: dict, analysis: dict) -> None:
    """Overwrites recipe_adherence.style_items[].item with the known short
    label (e.g. "Pose: Strong") instead of trusting the QA model to echo it
    back verbatim -- asking an LLM to reproduce a given string exactly isn't
    reliable, and it sometimes restates the long instruction text instead."""
    style_items = ((result.get("recipe_adherence") or {}).get("style_items")) or []
    entries = style_option_entries(analysis)
    for item, (short_label, _description) in zip(style_items, entries):
        item["item"] = short_label


def quality_check_photo(image_bytes: bytes, output_b64: str, analysis: dict):
    """Returns (result, prompt)."""
    preview = create_preview_bytes(image_bytes)
    original_data_url = image_to_data_url(preview, "image/jpeg")
    output_data_url = f"data:image/png;base64,{output_b64}"

    # Judge framing against the same single-subject geometry the edit was told
    # to produce, so an intentional closer crop that drops other people is not
    # penalized as missing content.
    size = (analysis.get("style_options") or {}).get("size")
    qa_prompt = build_quality_check_prompt(apply_framing_size(analysis, size))
    payload = _responses_payload(ANALYSIS_MODEL, [
        {"type": "input_text", "text": qa_prompt},
        {"type": "input_text", "text": "Original photo"},
        {"type": "input_image", "image_url": original_data_url},
        {"type": "input_text", "text": "Edited output"},
        {"type": "input_image", "image_url": output_data_url},
    ], text_format=QUALITY_CHECK_SCHEMA)
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Quality check failed", timeout=120)
    result = _parse_json_from_text(_extract_output_text(data))
    _relabel_style_items(result, analysis)
    return result, qa_prompt
