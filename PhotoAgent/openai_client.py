import json
import os
import re

import requests

from config import MODELS, PRESETS, PRESET_PROMPTS
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR
from image_tools import (
    create_preview_bytes,
    create_cropped_bytes,
    create_candidate_preview_bytes,
    image_to_data_url,
    get_image_dimensions,
    normalize_crop_box,
    build_crop_candidates,
    resolve_crop_box_with_report,
    clamp_raw_crop_box,
    crop_validation_risk,
    user_direction_affects_crop,
)
from prompts import (
    ANALYSIS_INSTRUCTION,
    build_user_instruction_review_prompt,
    build_crop_validation_prompt,
    build_edit_prompt,
    build_quality_check_prompt,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_IMAGES_EDITS_URL = "https://api.openai.com/v1/images/edits"

ANALYSIS_MODEL = MODELS["analysis"]
IMAGE_MODEL = MODELS["image"]


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _openai_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Environment variable OPENAI_API_KEY is not set", RESULT_UNKNOWN_INTERNAL_ERROR)
    return key


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_openai_api_key()}"}


def _post_json(url: str, body: dict, label: str, timeout: int = 120) -> dict:
    resp = requests.post(
        url,
        headers={**_auth_headers(), "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=timeout,
    )
    data = _safe_json(resp)
    if not resp.ok:
        msg = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
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


def _parse_json_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", str(text or ""))
        if not m:
            raise ValueError("AI 回應沒有包含有效 JSON。")
        return json.loads(m.group(0))


def _responses_payload(model: str, content: list) -> dict:
    return {"model": model, "input": [{"role": "user", "content": content}]}


# ── Step 2: photo analysis ─────────────────────────────────────────────────────

def analyze_photo(image_bytes: bytes) -> dict:
    preview = create_preview_bytes(image_bytes)
    data_url = image_to_data_url(preview, "image/jpeg")

    payload = _responses_payload(ANALYSIS_MODEL, [
        {"type": "input_text", "text": ANALYSIS_INSTRUCTION},
        {"type": "input_image", "image_url": data_url},
    ])
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Photo analysis failed")
    return _parse_json_from_text(_extract_output_text(data))


# ── Step 3: user instruction review ───────────────────────────────────────────

def review_user_instruction(user_direction: str, analysis: dict) -> dict:
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
        }

    prompt = build_user_instruction_review_prompt(trimmed, analysis)
    payload = _responses_payload(ANALYSIS_MODEL, [{"type": "input_text", "text": prompt}])
    data = _post_json(OPENAI_RESPONSES_URL, payload, "User instruction review failed")
    result = _parse_json_from_text(_extract_output_text(data))
    result["has_user_direction"] = True
    return result


# ── Step 4: crop validation ────────────────────────────────────────────────────

def validate_crop(
    image_bytes: bytes,
    analysis: dict,
    risk: dict = None,
    revision_feedback: str = None,
    force_validation: bool = False,
    previous_output_b64: str = None,
) -> dict:
    preview_bytes = create_preview_bytes(image_bytes)
    img_w, img_h = get_image_dimensions(preview_bytes)
    candidates = build_crop_candidates(analysis, high_risk=(risk or {}).get("level") == "high")

    has_enabled_candidate = any((c.get("cropBox") or {}).get("enabled") is True for c in candidates)
    if not revision_feedback and not force_validation and not has_enabled_candidate:
        return analysis

    original_data_url = image_to_data_url(preview_bytes, "image/jpeg")

    candidate_payloads = []
    for c in candidates:
        crop_box = c.get("cropBox") or {}
        if crop_box.get("enabled"):
            cand_preview = create_candidate_preview_bytes(preview_bytes, crop_box, analysis)
            cand_data_url = image_to_data_url(cand_preview, "image/jpeg")
            local_geom = resolve_crop_box_with_report(crop_box, analysis, img_w, img_h)
        else:
            cand_data_url = original_data_url
            local_geom = {"cropBox": None, "adjustmentReasons": []}

        candidate_payloads.append({**c, "localGeometry": local_geom, "imageDataUrl": cand_data_url})

    if not candidate_payloads:
        return analysis

    content = [
        {"type": "input_text", "text": build_crop_validation_prompt(
            analysis,
            candidate_payloads,
            revision_feedback,
            bool(previous_output_b64),
        )},
        {"type": "input_text", "text": "Original photo"},
        {"type": "input_image", "image_url": original_data_url},
    ]

    if previous_output_b64:
        content.append({
            "type": "input_text",
            "text": (
                "Previous failed output from the last run. Use this only as negative reference "
                "for understanding the QA feedback; do not treat it as the source image."
            ),
        })
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{previous_output_b64}"})

    for c in candidate_payloads:
        local_geom = c.get("localGeometry") or {}
        adj_crop = local_geom.get("cropBox")
        adj_reasons = local_geom.get("adjustmentReasons") or []
        adjusted_str = f"Adjusted crop: {json.dumps(adj_crop)}" if adj_crop else "Adjusted crop: original frame"
        reasons_str = f"Local geometry adjustments: {'; '.join(adj_reasons)}" if adj_reasons else "Local geometry adjustments: none"
        content.append({"type": "input_text", "text": f"Crop candidate {c['id']}: {c['label']}. {adjusted_str}. {reasons_str}"})
        content.append({"type": "input_image", "image_url": c["imageDataUrl"]})

    payload = _responses_payload(ANALYSIS_MODEL, content)
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Crop validation failed", timeout=120)
    validation = _parse_json_from_text(_extract_output_text(data))

    revised = validation.get("revised_crop_box")
    selected_id = validation.get("selected_candidate_id")
    selected_candidate = next((c for c in candidates if c["id"] == selected_id), None)
    use_revised = validation.get("pass") is False and revised and isinstance(revised, dict)

    selected_crop_box = revised if use_revised else (
        (selected_candidate or {}).get("cropBox") or analysis.get("crop_box")
    )
    selected_label = "AI 修正裁切" if use_revised else (
        (selected_candidate or {}).get("label") or "原始裁切建議"
    )

    return {
        **analysis,
        "crop_validation": {
            **validation,
            "selected_candidate_label": selected_label,
            "risk_level": (risk or {}).get("level") or "unknown",
            "risk_reasons": (risk or {}).get("reasons") or [],
        },
        "crop_candidates": [
            {
                "id": c["id"],
                "label": c["label"],
                "cropBox": c.get("cropBox"),
                "compositionIntent": c.get("compositionIntent") or "",
                "tradeoffs": c.get("tradeoffs") or "",
            }
            for c in candidates
        ],
        "crop_box": selected_crop_box,
        "crop_recommendation": (
            f"{analysis.get('crop_recommendation') or ''} 裁切評選修正：{revised.get('reason', '')}".strip()
            if use_revised and (revised or {}).get("reason")
            else analysis.get("crop_recommendation")
        ),
    }


# ── Step 5: image edit ─────────────────────────────────────────────────────────

def edit_photo(
    image_bytes: bytes,
    analysis: dict,
    quality: str = "medium",
    preset_key: str = "natural",
    revision_feedback: str = None,
) -> dict:
    preset = PRESETS.get(preset_key) or PRESETS["natural"]
    preset_prompt = PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"]

    user_instruction = analysis.get("user_instruction") or {}
    user_direction = user_instruction.get("normalized_user_direction") or ""
    crop_applied = bool(normalize_crop_box(analysis.get("crop_box"), analysis))

    cropped_bytes = create_cropped_bytes(image_bytes, analysis.get("crop_box"), analysis)
    prompt_text = build_edit_prompt(analysis, preset, preset_prompt, user_direction, crop_applied, revision_feedback)

    resp = requests.post(
        OPENAI_IMAGES_EDITS_URL,
        headers=_auth_headers(),
        files={"image": ("photo.png", cropped_bytes, "image/png")},
        data={"model": IMAGE_MODEL, "prompt": prompt_text, "quality": quality, "size": "auto"},
        timeout=300,
    )
    data = _safe_json(resp)
    if not resp.ok:
        msg = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        raise RuntimeError(f"Image edit failed: {msg}")

    b64 = (data.get("data") or [{}])[0].get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI 回應沒有包含圖片資料。")

    return {"image_b64": b64, "usage": data.get("usage")}


# ── Step 6: quality check ──────────────────────────────────────────────────────

def quality_check_photo(image_bytes: bytes, output_b64: str, analysis: dict) -> dict:
    preview = create_preview_bytes(image_bytes)
    original_data_url = image_to_data_url(preview, "image/jpeg")
    output_data_url = f"data:image/png;base64,{output_b64}"

    payload = _responses_payload(ANALYSIS_MODEL, [
        {"type": "input_text", "text": build_quality_check_prompt(analysis)},
        {"type": "input_text", "text": "Original photo"},
        {"type": "input_image", "image_url": original_data_url},
        {"type": "input_text", "text": "Edited output"},
        {"type": "input_image", "image_url": output_data_url},
    ])
    data = _post_json(OPENAI_RESPONSES_URL, payload, "Quality check failed", timeout=120)
    return _parse_json_from_text(_extract_output_text(data))
