import base64
import copy
import json
import logging
import os
import re
from typing import Tuple

import backoff
import requests

from config import MODELS, ANALYSIS_MODEL_REASONING_EFFORT, PRESETS, PRESET_PROMPTS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import (
    create_preview_bytes,
    detect_face_crops,
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
    ANALYSIS_RESPONSE_SCHEMA,
    ANALYSIS_RESPONSE_SCHEMA_OVERALL,
    build_analysis_instruction,
    build_overall_analysis_instruction,
    build_portrait_qc_instruction,
    build_portrait_qc_response_schema,
    build_eye_repair_planning_instruction,
    build_eye_repair_planning_response_schema,
    build_user_instruction_review_prompt,
    build_edit_prompt,
    build_gaze_only_edit_prompt,
    build_quality_check_prompt,
    expand_subject_boxes_to_contain_faces,
    merge_portrait_qc_issues,
    derive_hair_decisions,
    derive_luna_blink_decisions,
    derive_luna_gaze_decisions,
    eye_repair_targets,
    merge_eye_repair_strategies,
    portrait_qc_persons,
    style_option_entries,
    _apply_default_person_issues,
)
from photo_agent.feature_resolver import build_compact_editor_prompt

logger = logging.getLogger(__name__)

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
    entries appended after the main photo -- shared by the single-pass flow
    and both calls of the gpt-5.6-luna two-pass split below, since all three
    show the model the exact same locally-detected faces.

    Analysis is position-only now (no eye/gaze/hair/smile judging left), but
    the local, on-device face detector is still a more reliable way to find
    every person than asking the vision model to spot small/backgrounded
    faces in the full-frame preview alone. So each detected face is handed
    back to the model as its own zoomed crop image, captioned as the basis
    for subject detection: the model is told to anchor its subjects[] list
    on these detections (one subjects[] entry per detection, expanded to
    that person's full extent) rather than only self-discovering faces and
    cross-checking them against this afterward. See ANALYSIS_INSTRUCTION's
    "Local face detector reference" rule for the matching instruction text.

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
    """Deterministic backstop for the face-detector containment rule in
    ANALYSIS_INSTRUCTION -- see expand_subject_boxes_to_contain_faces's own
    docstring for why the prompt rule alone isn't enough to guarantee this.
    Uses every locally-detected face_box, including ones beyond the crop cap
    that were never sent to the model as an image -- this backstop only
    needs the box, not the crop. Also stamps the debug-UI-only face-box
    fields (frontend's #boxes-toggle-btn and face-thumbnail row). Shared by
    the single-pass flow and the two-pass split's Call A."""
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


def _analyze_photo_single_pass(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = ANALYSIS_MODEL) -> dict:
    preview = create_preview_bytes(image_bytes)
    data_url = image_to_data_url(preview, "image/jpeg")

    face_crops = detect_face_crops(image_bytes)
    content = [
        {"type": "input_text", "text": build_analysis_instruction(aspect_ratio, advanced_options, analysis_model)},
        {"type": "input_image", "image_url": data_url},
        *_face_crop_content_items(face_crops),
    ]

    # Constrains decoding to ANALYSIS_RESPONSE_SCHEMA's shape (Responses API
    # strict structured output) rather than relying on the prompt's "return
    # exactly this JSON shape" wording alone -- added after a production
    # gpt-5-mini call returned a long response with one object closed by `]`
    # instead of `}`, which _parse_json_from_text correctly rejected as
    # invalid JSON. The schema only constrains structure (types, required
    # keys, no extra keys), not the enum-like fields' documented value sets,
    # so a judgement call outside those sets still comes through instead of
    # the API hard-rejecting the request.
    payload = _responses_payload(
        analysis_model, content, text_format=ANALYSIS_RESPONSE_SCHEMA,
        reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    )
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Photo analysis failed")
    result = _parse_json_from_text(_extract_output_text(data))
    result = _stamp_local_face_debug_fields(result, face_crops)
    result = derive_hair_decisions(result)
    return result, {
        "model": analysis_model,
        "usage": data.get("usage"),
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }


def _sum_usage(*usage_items: dict) -> dict:
    """Combine raw OpenAI Responses API usage objects into one, so the
    Luna multi-call split still returns a single usage dict at the same
    {"model", "usage"} shape every other analyze_photo caller gets --
    downstream cost math (test_eye_analysis_accuracy.py) reads
    input_tokens/output_tokens/*_tokens_details off exactly one usage object
    per analyze_photo() call, and this keeps that true for the luna path
    too, now backed by two real API calls instead of one."""
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


def _analyze_photo_two_pass(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = ANALYSIS_MODEL) -> dict:
    """gpt-5.6-luna only (see analyze_photo below). Call A does overall photo
    direction and subject identification, same as the single-pass flow minus
    blink/hair/gaze/smile; Call B is a dedicated portrait-QC pass that observes
    those four fields and writes each candidate per-person hair correction,
    only for the people Call A already identified, merged
    back by prompts.merge_portrait_qc_issues's stable subject_index (never by
    response-array position). Python then derives hair/blink/gaze decisions;
    an optional Call C creates eye repair strategies only for people that pass.

    Call B (including a validation failure raised by merge_portrait_qc_issues
    itself -- a missing/duplicate/mismatched subject_index) is best-effort:
    on failure, every person keeps prompts._default_person_issues' "no
    portrait defects detected" reading and Call A's own result (crop/color/
    composition/subjects) is still returned, rather than failing the whole
    analysis over a pass that only ever adds detail on top of it.
    """
    preview = create_preview_bytes(image_bytes)
    data_url = image_to_data_url(preview, "image/jpeg")
    face_crops = detect_face_crops(image_bytes)
    face_crop_items = _face_crop_content_items(face_crops)

    content_a = [
        {"type": "input_text", "text": build_overall_analysis_instruction(aspect_ratio, advanced_options)},
        {"type": "input_image", "image_url": data_url},
        *face_crop_items,
    ]
    payload_a = _responses_payload(
        analysis_model, content_a, text_format=ANALYSIS_RESPONSE_SCHEMA_OVERALL,
        reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    )
    data_a = _post_json(OPENAI_RESPONSES_URL, payload_a, "Photo analysis failed (overall pass)")
    overall_analysis = _parse_json_from_text(_extract_output_text(data_a))
    overall_analysis = _stamp_local_face_debug_fields(overall_analysis, face_crops)

    persons = portrait_qc_persons(overall_analysis)

    analysis = None
    usage_b = None
    usage_c = None
    if persons:
        try:
            content_b = [
                {"type": "input_text", "text": build_portrait_qc_instruction(persons)},
                {"type": "input_image", "image_url": data_url},
                *face_crop_items,
            ]
            payload_b = _responses_payload(
                analysis_model, content_b, text_format=build_portrait_qc_response_schema(len(persons)),
                reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
            )
            data_b = _post_json(OPENAI_RESPONSES_URL, payload_b, "Photo analysis failed (portrait-QC pass)")
            portrait_qc = _parse_json_from_text(_extract_output_text(data_b))
            analysis = merge_portrait_qc_issues(overall_analysis, portrait_qc)
            analysis = derive_hair_decisions(analysis)
            analysis = derive_luna_blink_decisions(analysis)
            analysis = derive_luna_gaze_decisions(analysis)
            usage_b = data_b.get("usage")
        except Exception as exc:
            logger.warning(
                "Portrait-QC pass (gpt-5.6-luna two-pass split) failed, falling back to "
                "\"no portrait defects detected\" for this analysis: %s", exc,
            )

    if analysis is None:
        # No persons to review, or Call B failed/produced an invalid merge --
        # either way, every subject (person or not) still needs the four
        # issue fields filled in, same "不適用" shape merge_portrait_qc_issues
        # would have applied on success.
        _apply_default_person_issues(overall_analysis.get("subjects"))
        analysis = overall_analysis

    repair_targets = eye_repair_targets(analysis)
    if repair_targets:
        try:
            content_c = [
                {"type": "input_text", "text": build_eye_repair_planning_instruction(repair_targets)},
                {"type": "input_image", "image_url": data_url},
                *face_crop_items,
            ]
            payload_c = _responses_payload(
                analysis_model, content_c,
                text_format=build_eye_repair_planning_response_schema(len(repair_targets)),
                reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
            )
            data_c = _post_json(
                OPENAI_RESPONSES_URL, payload_c, "Photo analysis failed (eye repair-planning pass)"
            )
            repair_response = _parse_json_from_text(_extract_output_text(data_c))
            analysis = merge_eye_repair_strategies(analysis, repair_response)
            usage_c = data_c.get("usage")
        except Exception as exc:
            logger.warning(
                "Eye repair-planning pass failed; keeping deterministic fallback strategies: %s", exc,
            )

    usage = _sum_usage(data_a.get("usage"), usage_b, usage_c)
    return analysis, {
        "model": analysis_model,
        "usage": usage,
        # All three calls (A/B/C) share the same analysis_model, hence one
        # effort value for the combined usage -- see _sum_usage's docstring
        # for why this path still returns a single {"model", "usage"} shape.
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }


def analyze_photo(image_bytes: bytes, aspect_ratio: str = "auto", advanced_options: dict = None, analysis_model: str = ANALYSIS_MODEL) -> dict:
    if analysis_model == "gpt-5.6-luna":
        return _analyze_photo_two_pass(image_bytes, aspect_ratio, advanced_options, analysis_model)
    return _analyze_photo_single_pass(image_bytes, aspect_ratio, advanced_options, analysis_model)


# ── Step 3: user instruction review ───────────────────────────────────────────

def review_user_instruction(user_direction: str, analysis: dict, analysis_model: str = ANALYSIS_MODEL):
    """Returns (result, prompt, usage) -- usage is {"model", "usage"}, both
    usage and prompt are None when no GPT call was made (no user direction to
    review)."""
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
        }, None, None

    prompt = build_user_instruction_review_prompt(trimmed, analysis)
    payload = _responses_payload(
        analysis_model, [{"type": "input_text", "text": prompt}],
        reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    )
    data = _post_json(OPENAI_RESPONSES_URL, payload, "User instruction review failed")
    result = _parse_json_from_text(_extract_output_text(data))
    result["has_user_direction"] = True
    return result, prompt, {
        "model": analysis_model,
        "usage": data.get("usage"),
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }


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
    raw_prompt, raw_prompt_sections = build_edit_prompt(
        framed_analysis, preset, preset_prompt, user_direction, crop_plan,
        revision_feedback, aspect_ratio_pref=aspect_ratio,
        source_size=(img_w, img_h), single_pass_edit=single_pass_edit,
        return_sections=True,
    )
    advanced_options = framed_analysis.get("_advanced_options") or {}
    prompt_strategy = advanced_options.get("editor_prompt_strategy") or "legacy"
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

    # Converted once, outside the retry-wrapped call, so a retry doesn't
    # needlessly re-encode the same bytes.
    png_bytes = ensure_png_bytes(image_bytes)
    data = _post_image_edit(model, prompt_text, quality, size_param, png_bytes)

    b64 = (data.get("data") or [{}])[0].get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI 回應沒有包含圖片資料。")

    return {
        "image_b64": b64,
        "usage": {"model": model, "usage": data.get("usage")},
        "geometry_warnings": output_geometry_warnings(b64, effective_ratio_label, crop_plan, img_w, img_h),
        "raw_prompt": raw_prompt,
        "prompt_resolution": resolution_metadata,
        "prompt_strategy": prompt_strategy,
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


# ── Step 5b: gaze-only repair pass (OpenAI) ────────────────────────────────────

def edit_gaze_only(image_bytes: bytes, gaze_items: list, quality: str = "low", model: str = IMAGE_MODEL):
    """Runs the approved gaze repair(s) as a second, isolated full-image edit
    on top of Step 5's own output (see pipeline._run_gaze_repair_pass for why
    this is a second whole-image call rather than a crop-and-paste). OpenAI
    counterpart to gemini_client.edit_gaze_only -- which of the two runs is
    picked by config.MODELS["gaze_repair"], independent of whichever model
    ran Step 5 itself.

    Returns (image_bytes, prompt_text, usage) -- usage is {"model", "usage"};
    same shape as the Gemini version, so pipeline.py can surface this stage's
    prompt in the "Sent to GPT" debug view regardless of which provider
    actually ran it.
    """
    prompt_text = build_gaze_only_edit_prompt(gaze_items)
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
    ], text_format=QUALITY_CHECK_SCHEMA, reasoning_effort=ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model))
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Quality check failed", timeout=120)
    result = _parse_json_from_text(_extract_output_text(data))
    _relabel_style_items(result, analysis)
    return result, qa_prompt, {
        "model": analysis_model,
        "usage": data.get("usage"),
        "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model),
    }
