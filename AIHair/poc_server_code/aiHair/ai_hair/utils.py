"""Small shared helpers for aiHair."""

from __future__ import annotations

from engine_result_code import RESULT_INVALID_PARAMETER


def log(logger, task_id: str, message: str) -> None:
    if logger:
        logger.info(task_id, f"[aiHair] {message}")


def require_text(value, field_name: str) -> str:
    if value is None:
        raise RuntimeError(f"aiHair: {field_name} is required", RESULT_INVALID_PARAMETER)
    if not isinstance(value, str):
        raise RuntimeError(f"aiHair: {field_name} must be a string", RESULT_INVALID_PARAMETER)
    value = value.strip()
    if not value:
        raise RuntimeError(f"aiHair: {field_name} must be a non-empty string", RESULT_INVALID_PARAMETER)
    return value
