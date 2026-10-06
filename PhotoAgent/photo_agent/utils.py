"""Small shared helpers for photoAgent."""

from __future__ import annotations

import json
import os

from engine_result_code import RESULT_INVALID_PARAMETER

# Keep enough request context in an error artifact to diagnose API/JSON/merge
# failures without retaining the image data URL from the request payload.
DEBUG_VALUE_MAX_CHARS = 50000


def bounded_debug_value(value, max_chars=DEBUG_VALUE_MAX_CHARS):
    """Return a JSON-safe, bounded value for an error artifact."""
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    if len(text) <= max_chars:
        try:
            return json.loads(text)
        except Exception:
            return text
    return {
        "truncated": True,
        "original_chars": len(text),
        "text": text[:max_chars],
    }


def log(logger, task_id: str, message: str) -> None:
    if logger:
        logger.info(task_id, f"[photoAgent] {message}")


def write_json_artifact(path: str, payload: dict) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def require_text(value, field_name: str) -> str:
    if value is None:
        raise RuntimeError(f"photoAgent: {field_name} is required", RESULT_INVALID_PARAMETER)
    if not isinstance(value, str):
        raise RuntimeError(f"photoAgent: {field_name} must be a string", RESULT_INVALID_PARAMETER)
    value = value.strip()
    if not value:
        raise RuntimeError(f"photoAgent: {field_name} must be a non-empty string", RESULT_INVALID_PARAMETER)
    return value


def optional_text(value, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RuntimeError(f"photoAgent: {field_name} must be a string", RESULT_INVALID_PARAMETER)
    return value.strip()


def optional_bool(value, field_name: str) -> bool:
    if value is None:
        return False
    if type(value) is not bool:
        raise RuntimeError(f"photoAgent: {field_name} must be a boolean", RESULT_INVALID_PARAMETER)
    return value


def text_field(params: dict, key: str, required: bool = False) -> str:
    value = params.get(key)
    return require_text(value, key) if required else optional_text(value, key)
