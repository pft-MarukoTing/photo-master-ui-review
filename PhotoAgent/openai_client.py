import base64
import json
import logging
import os
import re
from functools import partial
from typing import Tuple

import requests

from config import MODELS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import (
    create_preview_bytes,
    detect_face_crops,
    image_to_data_url,
    get_image_dimensions,
    crop_resolution_ratio_label,
    apply_framing_size,
    nearest_openai_edit_size,
    supports_arbitrary_openai_size,
    openai_edit_size_for_ratio,
    ensure_png_bytes,
)
from prompts import (
    ANALYSIS_RESPONSE_SCHEMA_OVERALL,
    REFRAME_RESPONSE_SCHEMA,
    build_reframe_instruction,
    build_overall_analysis_instruction,
    build_quality_check_prompt,
    expand_subject_boxes_to_contain_faces,
    style_option_entries,
)
from photo_agent.capabilities import ANALYSIS_MODEL_REASONING_EFFORT
from photo_agent.providers.http import api_error, checked_json, error_message, retry_transient, safe_json
from photo_agent.providers.image_edit import EditGeometry, run_image_edit
from photo_agent.analysis_skills import ANALYSIS_SKILLS, AnalysisContext, select_analysis_skills
from photo_agent.utils import bounded_debug_value

logger = logging.getLogger(__name__)


def _attach_api_error_context(exc, *, url, body, label=None, raw_response=None, status=None):
    """Attach non-sensitive request/response context to an API exception.

    The image data URL is deliberately not copied from the request body.
    These attributes are consumed by the Body Reshape fallback and also make
    the exception useful to callers that only log the original exception.
    """
    try:
        exc.body_reshape_endpoint = url
        exc.body_reshape_api_label = label
        exc.body_reshape_model = body.get("model") if isinstance(body, dict) else None
        exc.body_reshape_http_status = status
        if raw_response is not None:
            exc.body_reshape_raw_response = bounded_debug_value(raw_response)
        exc.body_reshape_error_kind = "api_call"
    except Exception:
        # Diagnostics must never mask the original API error.
        pass


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


@retry_transient
def _post_json(url: str, body: dict, label: str, timeout: int = 120) -> dict:
    try:
        resp = requests.post(
            url,
            headers={**_auth_headers(), "Content-Type": "application/json"},
            # str data must be bytes before hitting http.client, or it tries
            # (and fails) to encode non-ASCII chars as Latin-1.
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
    except Exception as exc:
        _attach_api_error_context(exc, url=url, body=body, label=label)
        raise
    data = safe_json(resp)
    raw_response = data
    if not data:
        text = getattr(resp, "text", "")
        raw_response = {"raw_text": text} if text else data
    if not resp.ok:
        exc = api_error(resp, f"{label}: {error_message(data, resp.status_code)}")
        _attach_api_error_context(
            exc, url=url, body=body, label=label, raw_response=raw_response, status=resp.status_code
        )
        raise exc
    return data


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


def _responses_payload(model: str, content: list, text_format: dict = None, reasoning_effort: str = None) -> dict:
    payload = {"model": model, "input": [{"role": "user", "content": content}]}
    if text_format:
        payload["text"] = {"format": text_format}
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


# ── Step 2: photo analysis ─────────────────────────────────────────────────────

def _face_crop_content_items(face_crops):
    """Builds the "Face crop N" / "Local face detector reference N" content
    entries appended after the main photo -- shared by Calls A, B and C of the
    analysis split below, since all three show the model the exact same
    locally-detected faces.

    Analysis is position-only now (no eye/gaze/hair/smile judging left), but
    the local, on-device face detector is still a more reliable way to find
    every person than asking the vision model to spot small/backgrounded
    faces in the full-frame preview alone. So each detected face is handed
    back to the model as its own zoomed crop image, captioned as the basis
    for subject detection: the model is told to anchor its subjects[] list
    on these detections (one subjects[] entry per detection, expanded to
    that person's full extent) rather than only self-discovering faces and
    cross-checking them against this afterward. See _FACE_CROP_EVIDENCE_SECTION
    in prompts.py for the matching instruction text.

    Only entries with an actual cropped image (the first _FACE_CROP_MAX_COUNT,
    largest first -- see detect_face_crops' docstring) get sent to the model
    as an image; a photo with more locally-detected faces than that still
    gets the rest as a coordinate-only text hint, never as an extra image/
    token cost.
    """
    items = []
    for i, crop in enumerate(face_crops, start=1):
        fb = crop["face_box"]
        if crop.get("bytes") is not None:
            items.append({
                "type": "input_text",
                "text": (
                    f"Face crop {i} (zoomed, for anchoring subject detection and judging eye state, blink, "
                    "gaze/pupil direction, smile state, and strand-level front-hair detail; it is not a separate photo). Local face detector "
                    "reference (normalized original-image coordinates, tight face-only box): "
                    f"x={fb['x']:.3f}, y={fb['y']:.3f}, w={fb['w']:.3f}, h={fb['h']:.3f} -- this is "
                    f"detection_index {i}: give it a local_face_review entry, and create a matching "
                    "subjects[] entry only when it passes the Subject inclusion rule; see the Subject "
                    "inclusion rule, Zoomed face-crop images, and Coordinate rules sections above for how "
                    "to judge and how much to trust this."
                ),
            })
            items.append({"type": "input_image", "image_url": image_to_data_url(crop["bytes"], "image/jpeg")})
        else:
            items.append({
                "type": "input_text",
                "text": (
                    f"Local face detector reference {i} (normalized original-image coordinates, tight "
                    f"face-only box): x={fb['x']:.3f}, y={fb['y']:.3f}, w={fb['w']:.3f}, h={fb['h']:.3f} -- "
                    f"this is detection_index {i} too: give it a local_face_review entry, and create a "
                    "matching subjects[] entry only when it passes the Subject inclusion rule; see the "
                    "Subject inclusion rule and Coordinate rules sections above for how to judge and how "
                    "much to trust this."
                ),
            })
    return items


def _stamp_local_face_debug_fields(result, face_crops):
    """Deterministic backstop for the face-detector containment rule in the
    overall analysis instruction -- see expand_subject_boxes_to_contain_faces's own
    docstring for why the prompt rule alone isn't enough to guarantee this.
    Uses every locally-detected face_box, including ones beyond the crop cap
    that were never sent to the model as an image -- this backstop only
    needs the box, not the crop. Also stamps the debug-UI-only face-box
    fields (frontend's #boxes-toggle-btn and face-thumbnail row). Applied to
    Call A's result."""
    if not face_crops:
        return result
    result = expand_subject_boxes_to_contain_faces(result, [c["face_box"] for c in face_crops])
    sent_crops = [c for c in face_crops if c.get("bytes") is not None]
    if sent_crops:
        result["_face_crop_boxes"] = [c["box"] for c in sent_crops]
    result["_local_face_boxes"] = [
        {"box": c["box"], "face_box": c["face_box"], "sent_to_model": c.get("bytes") is not None}
        for c in face_crops
    ]
    return result


def _sum_usage(*usage_items: dict) -> dict:
    """Combine raw OpenAI Responses API usage objects into one, so the
    multi-call analysis split still returns a single usage dict at the
    {"model", "usage"} shape -- downstream cost math
    (test_eye_analysis_accuracy.py) reads input_tokens/output_tokens/
    *_tokens_details off exactly one usage object per analyze_photo() call."""
    usage_items = [usage or {} for usage in usage_items if usage]
    combined = {
        "input_tokens": sum((usage.get("input_tokens") or 0) for usage in usage_items),
        "output_tokens": sum((usage.get("output_tokens") or 0) for usage in usage_items),
    }
    combined["total_tokens"] = combined["input_tokens"] + combined["output_tokens"]
    for details_key in ("input_tokens_details", "output_tokens_details"):
        details = [usage.get(details_key) or {} for usage in usage_items]
        combined[details_key] = {
            key: sum((detail.get(key) or 0) for detail in details)
            for key in set().union(*(set(detail) for detail in details))
        }
    return combined


def _analysis_request(analysis_model, data_url, face_crop_items, instruction, text_format, label,
                      *, with_face_crops=True):
    """Send one analysis instruction with the photo and return the raw Responses envelope."""
    content = [
        {"type": "input_text", "text": instruction},
        {"type": "input_image", "image_url": data_url},
    ]
    if with_face_crops:
        content.extend(face_crop_items)
    payload = _responses_payload(
        analysis_model, content, text_format=text_format,
        reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    )
    return _post_json(OPENAI_RESPONSES_URL, payload, label)


def _parse_response_json(data: dict) -> dict:
    return _parse_json_from_text(_extract_output_text(data))


def _analyze_photo_two_pass(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = ANALYSIS_MODEL):
    """The analysis flow for every analysis model. Call A does overall photo
    direction and subject identification; each analysis skill the router
    selects from that result then runs its own passes (see
    photo_agent.analysis_skills), and every other skill fills its fields with
    the not-applicable shape.
    """
    preview = create_preview_bytes(image_bytes)
    data_url = image_to_data_url(preview, "image/jpeg")
    face_crops = detect_face_crops(image_bytes)
    face_crop_items = _face_crop_content_items(face_crops)
    ctx = AnalysisContext(
        request=partial(_analysis_request, analysis_model, data_url, face_crop_items),
        parse=_parse_response_json,
    )

    data_a = ctx.request(
        build_overall_analysis_instruction(aspect_ratio, source_size=get_image_dimensions(image_bytes)),
        ANALYSIS_RESPONSE_SCHEMA_OVERALL,
        "Photo analysis failed (overall pass)",
    )
    analysis = _stamp_local_face_debug_fields(ctx.parse(data_a), face_crops)

    usages = [data_a.get("usage")]
    selected = select_analysis_skills(analysis)
    for skill in ANALYSIS_SKILLS:
        if skill in selected:
            analysis, skill_usages = skill.run(analysis, ctx)
            usages.extend(skill_usages)
        else:
            analysis = skill.skip(analysis)

    return analysis, {
        "model": analysis_model,
        "usage": _sum_usage(*usages),
        # Every call shares the same analysis_model, hence one effort value
        # for the combined usage -- see _sum_usage's docstring.
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }


def reframe_composition(image_bytes: bytes, analysis: dict, aspect_ratio: str = "auto", analysis_model: str = ANALYSIS_MODEL):
    """Re-plan only the composition of an existing analysis for another
    aspect-ratio preference (see prompts.build_reframe_instruction).

    Returns (reframe_json, usage_info, prompt_text).
    """
    data_url = image_to_data_url(create_preview_bytes(image_bytes), "image/jpeg")
    before_photo, after_photo = build_reframe_instruction(
        analysis, aspect_ratio, source_size=get_image_dimensions(image_bytes),
    )
    content = [
        {"type": "input_text", "text": before_photo},
        {"type": "input_image", "image_url": data_url},
        {"type": "input_text", "text": after_photo},
    ]
    payload = _responses_payload(
        analysis_model, content, text_format=REFRAME_RESPONSE_SCHEMA,
        reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    )
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Photo reframing failed")
    reframe = _parse_json_from_text(_extract_output_text(data))
    usage = {
        "model": analysis_model,
        "usage": data.get("usage"),
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }
    return reframe, usage, f"{before_photo}\n\n[photo]\n\n{after_photo}"


def analyze_photo(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = ANALYSIS_MODEL):
    return _analyze_photo_two_pass(image_bytes, aspect_ratio, advanced_options, analysis_model)


# ── Step 3: user instruction pass-through ─────────────────────────────────────

def review_user_instruction(user_direction: str, analysis: dict, analysis_model: str = ANALYSIS_MODEL):
    """Pass the user's text through to the editor without an LLM review call."""
    trimmed = (user_direction or "").strip()
    return {
        "has_user_direction": bool(trimmed),
        "user_intent_summary": trimmed,
        "normalized_user_direction": trimmed,
        "explicit_priorities": [],
        "explicit_prohibitions": [],
        "unspecified_agent_freedom": ["可依照片分析保守改善光線、色彩、清晰度、構圖與干擾。"] if not trimmed else [],
        "crop_or_framing_requested": False,
        "crop_guidance": "使用者未指定裁切，Agent 可依分析保守判斷。" if not trimmed else "",
        "qa_focus": [],
        "conflicts": [],
        "warning": "",
    }, None, None


# ── Step 5: image edit ─────────────────────────────────────────────────────────

def edit_photo(
    image_bytes: bytes,
    analysis: dict,
    quality: str = "medium",
    preset_key: str = "natural",
    revision_feedback: str = None,
    model: str = IMAGE_MODEL,
    aspect_ratio: str = "auto",
    single_pass_edit: bool = False,
):
    """Returns (result, prompt)."""

    def resolve_geometry(framed_analysis, img_w, img_h):
        if aspect_ratio == "original":
            ratio_w, ratio_h = img_w, img_h
            ratio_label = "original"
        else:
            ratio_label = _analysis_aspect_ratio_label(framed_analysis)
            if ratio_label == "original":
                ratio_w, ratio_h = img_w, img_h
            else:
                ratio_w, ratio_h = _ratio_dimensions(ratio_label)
        size_param = openai_edit_size_for_ratio(ratio_w, ratio_h) if supports_arbitrary_openai_size(model) else nearest_openai_edit_size(ratio_w, ratio_h)
        if "x" in size_param:
            output_w, output_h = (int(v) for v in size_param.lower().split("x", 1))
            ratio_label = f"{output_w}:{output_h}"
        return EditGeometry(
            ratio_label=ratio_label,
            # In expand mode the rectangle is not the canvas's shape and must
            # not be refitted to it -- the canvas is reached by extending,
            # not by cropping.
            crop_ratio_label=crop_resolution_ratio_label(framed_analysis, img_w, img_h, ratio_label),
            expanding=bool(framed_analysis.get("reframe_extension")),
            request={"size": size_param},
        )

    def send(png_bytes, prompt_text, geometry):
        data = _post_image_edit(model, prompt_text, quality, geometry.request["size"], png_bytes)
        b64 = (data.get("data") or [{}])[0].get("b64_json")
        if not b64:
            raise RuntimeError("OpenAI 回應沒有包含圖片資料。")
        return b64, {"model": model, "usage": data.get("usage")}

    return run_image_edit(
        image_bytes, analysis, preset_key=preset_key, revision_feedback=revision_feedback,
        aspect_ratio=aspect_ratio, single_pass_edit=single_pass_edit,
        resolve_geometry=resolve_geometry, send=send,
    )


@retry_transient
def _post_image_edit(model: str, prompt_text: str, quality: str, size_param: str, png_bytes: bytes) -> dict:
    resp = requests.post(
        OPENAI_IMAGES_EDITS_URL,
        headers=_auth_headers(),
        files={"image": ("photo.png", png_bytes, "image/png")},
        data={"model": model, "prompt": prompt_text, "quality": quality, "size": size_param},
        timeout=300,
    )
    return checked_json(resp, "Image edit failed")


# ── Step 5b: eye repair pass (OpenAI) ──────────────────────────────────────────

def edit_eye_repair_pass(image_bytes: bytes, prompt: str, quality: str = "low", model: str = IMAGE_MODEL):
    """Runs the approved gaze/blink repair(s) as a second, isolated full-image edit
    on top of Step 5's own output (see pipeline._run_eye_repair_pass for why
    this is a second whole-image call rather than a crop-and-paste). OpenAI
    counterpart to gemini_client.edit_eye_repair_pass; which one runs is
    picked by pipeline._run_eye_repair_pass, independent of whichever model
    ran Step 5 itself.

    Returns (image_bytes, prompt_text, usage) -- usage is {"model", "usage"};
    same shape as the Gemini version, so pipeline.py can surface this stage's
    prompt in the "Sent to GPT" debug view regardless of which provider
    actually ran it.
    """
    prompt_text = prompt
    img_w, img_h = get_image_dimensions(image_bytes)
    size_param = openai_edit_size_for_ratio(img_w, img_h) if supports_arbitrary_openai_size(model) else nearest_openai_edit_size(img_w, img_h)
    png_bytes = ensure_png_bytes(image_bytes)
    data = _post_image_edit(model, prompt_text, quality, size_param, png_bytes)
    b64 = (data.get("data") or [{}])[0].get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI 回應沒有包含圖片資料。")
    return base64.b64decode(b64), prompt_text, {"model": model, "usage": data.get("usage")}


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


def _quality_check_schema(analysis):
    """Return the holistic QA schema for every request.

    Body Reshape is a single natural-language instruction, so QA deliberately
    uses the same general checks as other edits.  There is no endpoint list or
    numeric body measurement to add to the response schema.
    """
    return QUALITY_CHECK_SCHEMA


def _relabel_style_items(result: dict, analysis: dict) -> None:
    """Overwrites recipe_adherence.style_items[].item with the known short
    label (e.g. "Pose: Strong") instead of trusting the QA model to echo it
    back verbatim -- asking an LLM to reproduce a given string exactly isn't
    reliable, and it sometimes restates the long instruction text instead."""
    style_items = ((result.get("recipe_adherence") or {}).get("style_items")) or []
    entries = style_option_entries(analysis)
    for item, (short_label, _description) in zip(style_items, entries):
        item["item"] = short_label


def quality_check_photo(image_bytes: bytes, output_b64: str, analysis: dict, analysis_model: str = ANALYSIS_MODEL):
    """Returns (result, prompt, usage) -- usage is {"model", "usage"}."""
    preview = create_preview_bytes(image_bytes)
    original_data_url = image_to_data_url(preview, "image/jpeg")
    output_data_url = f"data:image/png;base64,{output_b64}"

    # Judge framing against the same single-subject geometry the edit was told
    # to produce, so an intentional closer crop that drops other people is not
    # penalized as missing content.
    size = (analysis.get("style_options") or {}).get("size")
    qa_prompt = build_quality_check_prompt(apply_framing_size(analysis, size))
    payload = _responses_payload(analysis_model, [
        {"type": "input_text", "text": qa_prompt},
        {"type": "input_text", "text": "Original photo"},
        {"type": "input_image", "image_url": original_data_url},
        {"type": "input_text", "text": "Edited output"},
        {"type": "input_image", "image_url": output_data_url},
    ], text_format=_quality_check_schema(analysis), reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model))
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Quality check failed", timeout=120)
    result = _parse_json_from_text(_extract_output_text(data))
    _relabel_style_items(result, analysis)
    return result, qa_prompt, {
        "model": analysis_model,
        "usage": data.get("usage"),
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }
