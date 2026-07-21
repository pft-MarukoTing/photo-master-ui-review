"""Analysis-specific OpenAI client for comparison-only retouching QA.

Self-contained rewrite that keeps the exact calling convention, API-key
handling, and response-reading format of the main PhotoAgent openai_client
(Responses API via requests, strict json_schema structured output,
output_text extraction), plus the retry/latency/usage bookkeeping the
evaluation pipeline needs.  Each evaluation sends exactly three content
blocks: one QA prompt, the original image, and the edited result.  The API key only ever lives in this backend
process and is never logged or returned.
"""

import base64
import io
import json
import logging
import os
import random
import re
import time

import requests
from PIL import Image, ImageOps

from analyze.backend import config
from analyze.backend.services.prompts import build_evaluation_prompt

logger = logging.getLogger("analyze")

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


OPENAI_API_KEY_MISSING_CODE = "OPENAI_API_KEY_MISSING"
OPENAI_API_KEY_MISSING_MESSAGE = "未設定 OPENAI_API_KEY，無法執行圖片評分。"


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _openai_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(OPENAI_API_KEY_MISSING_MESSAGE)
    return key


def openai_key_present() -> bool:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    return bool(key)


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_openai_api_key()}"}


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
        raise RuntimeError(f"{label}: {msg}", resp.status_code)
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


def _responses_payload(model: str, content: list, text_format: dict = None) -> dict:
    payload = {"model": model, "input": [{"role": "user", "content": content}]}
    if text_format:
        payload["text"] = {"format": text_format}
    return payload


# ── Image encoding helpers ─────────────────────────────────────────────────────

def create_preview_bytes(
    image_bytes: bytes,
    max_side: int = None,
    jpeg_quality: int = 88,
) -> bytes:
    max_side = max_side or config.EVAL_IMAGE_MAX_SIDE
    jpeg_quality = max(1, min(95, int(jpeg_quality)))
    img = _open_rgb(image_bytes)
    longest = max(img.width, img.height)
    if longest > max_side:
        scale = max_side / longest
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality)
    return buf.getvalue()


def image_to_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def decode_image(image_bytes: bytes) -> "Image.Image":
    """Decodes and normalises an image, raising on unparseable input.

    Used for cheap parseability validation without producing a preview -- the
    resize and JPEG re-encode that create_preview_bytes performs are unneeded
    when the caller only wants to confirm the bytes are a readable image.
    """
    return _open_rgb(image_bytes)


def _open_rgb(image_bytes: bytes) -> Image.Image:
    # Phone/camera JPEGs often store the intended rotation only in EXIF.  The
    # preview is saved as a fresh JPEG without that metadata, so first bake the
    # orientation into the pixels or the browser will display it sideways.
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


# ── Structured-output schema ───────────────────────────────────────────────────

DEFECT_CODES = [
    "subject_identity_damage",
    "scene_rebuild_or_replacement",
    "unintended_content_change",
    "required_content_loss",
    "crop_boundary_damage",
    "anatomy_shape_material_damage",
    "geometry_perspective_damage",
    "artificial_border_or_padding",
    "lighting_shadow_regression",
    "color_tone_regression",
    "detail_texture_damage",
    "overprocessing_synthetic_look",
    "new_generation_artifact",
    "other",
]

DEFECT_SEVERITIES = ["minor", "major", "critical"]

# enum keeps the direct 0-100 integer score enforced by the constrained
# decoder (strict mode does not reliably honour minimum/maximum).
_SCORE_0_100 = {"type": "integer", "enum": list(range(101))}

_DIMENSION_OBJECT = {
    "type": "object",
    "properties": {
        "score": _SCORE_0_100,
        "reason": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "reason", "issues"],
    "additionalProperties": False,
}

EVALUATION_SCHEMA = {
    "type": "json_schema",
    "name": "image_quality_evaluation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "dimensions": {
                "type": "object",
                "properties": {key: _DIMENSION_OBJECT for key in config.DIMENSION_KEYS},
                "required": config.DIMENSION_KEYS,
                "additionalProperties": False,
            },
            "critical_defects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": DEFECT_CODES},
                        "severity": {"type": "string", "enum": DEFECT_SEVERITIES},
                        "dimension": {"type": "string", "enum": config.DIMENSION_KEYS},
                        "description": {"type": "string"},
                    },
                    "required": ["code", "severity", "dimension", "description"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
            "suggestions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["dimensions", "critical_defects", "summary", "suggestions"],
        "additionalProperties": False,
    },
}


# ── Evaluation call ────────────────────────────────────────────────────────────

def _is_retryable(exc) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, ValueError):
        # Unparseable model JSON -- the model may glitch; a retry can recover.
        return True
    message = str(exc)
    if "OPENAI_API_KEY" in message:
        return False
    if isinstance(exc, RuntimeError) and len(exc.args) > 1:
        status = exc.args[1]
        return status == 429 or (isinstance(status, int) and status >= 500)
    return bool(re.search(r"rate limit|overloaded|timeout", message, re.IGNORECASE))


def build_evaluation_content(original_bytes: bytes, result_bytes: bytes) -> list:
    """Returns exactly: QA prompt, original image, edited-result image."""
    return [
        {"type": "input_text", "text": build_evaluation_prompt()},
        {
            "type": "input_image",
            "image_url": image_to_data_url(create_preview_bytes(original_bytes), "image/jpeg"),
        },
        {
            "type": "input_image",
            "image_url": image_to_data_url(create_preview_bytes(result_bytes), "image/jpeg"),
        },
    ]


def evaluate_image(
    original_bytes: bytes,
    result_bytes: bytes,
    model: str = None,
    timeout: int = None,
    max_retries: int = None,
):
    """Scores one edited result; returns raw output and call telemetry.

    The model only returns direct per-dimension 0-100 integer scores, reasons, issues,
    critical_defects, summary, and suggestions -- the weighted overall score
    is computed by the backend
    (evaluator.compute_scores). Transient failures are retried up to
    max_retries with exponential backoff; fatal errors raise immediately.
    """
    model = model or config.EVAL_MODEL
    timeout = timeout or config.EVAL_TIMEOUT
    max_retries = config.EVAL_MAX_RETRIES if max_retries is None else max_retries

    payload = _responses_payload(
        model,
        build_evaluation_content(original_bytes, result_bytes),
        text_format=EVALUATION_SCHEMA,
    )

    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            data = _post_json(OPENAI_RESPONSES_URL, payload, "Image quality evaluation failed", timeout=timeout)
            raw = _parse_json_from_text(_extract_output_text(data))
            latency_ms = round((time.time() - t0) * 1000, 1)
            usage = data.get("usage")
            logger.info("evaluation ok", extra={"params": {
                "model": model, "latency_ms": latency_ms,
                "tokens": (usage or {}).get("total_tokens"), "retries": attempt,
            }})
            return {"raw": raw, "usage": usage, "latency_ms": latency_ms, "retry_count": attempt}
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable(exc):
                logger.error("evaluation failed", extra={
                    "error_type": type(exc).__name__,
                    "params": {"model": model, "attempt": attempt, "error": str(exc)},
                })
                raise
            sleep_s = min(2 ** attempt, 20) + random.uniform(0, 0.5)
            logger.warning("evaluation retry", extra={"params": {
                "attempt": attempt + 1, "max_retries": max_retries,
                "sleep_s": round(sleep_s, 1), "error": str(exc),
            }})
            time.sleep(sleep_s)
