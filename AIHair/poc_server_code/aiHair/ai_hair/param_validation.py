"""Request validation for the aiHair effect.

acts[0].params:
    feature   one of features.FEATURE_NAMES (case-insensitive), required
    level     hairline only: 1 (HairFeatures' hairline_simple_lv1) or 2
              (its hairline_simple_lv1.5); default 1
srcKeys:  exactly one photo (PNG, JPEG or WebP)
dstKeys:  one key per result image wanted (1 to MAX_RESULTS); each one's
          extension (.jpg/.jpeg/.png/.webp) is the format it is saved in
"""

from __future__ import annotations

from typing import Optional, TypedDict

from engine_result_code import RESULT_ERROR_EMPTY_SRC_KEYS, RESULT_INVALID_PARAMETER

from .features import DEFAULT_LEVEL, FEATURE_NAMES, LEVELLED, canonical_feature
from .utils import require_text


EFFECT_NAME = "aiHair"
# Result images per job at most: images.edit's `n` limit is 10, and each one
# is a billed render.
MAX_RESULTS = 4
RESULT_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
DEFAULT_INFERENCE_STYLE_VALUES = {
    "feature": "",
    "level": DEFAULT_LEVEL,
}


class AiHairRequest(TypedDict):
    act_id: str
    params: dict
    src_keys: list[str]
    dst_keys: list[str]
    style_id: object
    has_style_values: bool
    applied_style_keys: list[str]


def get_inference_style(input_effect_params: dict):
    """Extract style_id and first item values from inference_style_list."""
    if not isinstance(input_effect_params, dict):
        return None, None
    inference_style_list = input_effect_params.get("inference_style_list")
    if not isinstance(inference_style_list, list) or len(inference_style_list) == 0:
        return None, None
    first_style = inference_style_list[0]
    if not isinstance(first_style, dict):
        return None, None

    style_items = first_style.get("items")
    if not isinstance(style_items, list) or len(style_items) == 0:
        return first_style.get("style_id"), None
    style_values = style_items[0]
    if not isinstance(style_values, dict):
        return first_style.get("style_id"), None

    return first_style.get("style_id"), style_values


def apply_inference_style_values(input_effect_params: dict, style_values: Optional[dict], allowed_keys: Optional[set] = None) -> list[str]:
    """Override top-level params from inference_style_list[0].items[0]."""
    if not isinstance(input_effect_params, dict) or not isinstance(style_values, dict):
        return []

    applied_keys = []
    for key, style_value in style_values.items():
        if allowed_keys is not None and key not in allowed_keys:
            continue
        if key not in DEFAULT_INFERENCE_STYLE_VALUES:
            continue
        input_effect_params[key] = style_value
        applied_keys.append(key)
    return applied_keys


def _validate_keys(value, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError(f"{EFFECT_NAME}: {field_name} must be a list", RESULT_INVALID_PARAMETER)
    keys = []
    for i, key in enumerate(value):
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError(
                f"{EFFECT_NAME}: {field_name}[{i}] must be a non-empty string",
                RESULT_INVALID_PARAMETER,
            )
        keys.append(key.strip())
    return keys


def _validate_src_keys(value) -> list[str]:
    keys = _validate_keys(value, "srcKeys")
    if not keys:
        raise RuntimeError(f"{EFFECT_NAME}: srcKeys must contain the photo", RESULT_ERROR_EMPTY_SRC_KEYS)
    if len(keys) > 1:
        raise RuntimeError(f"{EFFECT_NAME}: srcKeys must contain exactly one photo", RESULT_INVALID_PARAMETER)
    return keys


def _validate_dst_keys(value) -> list[str]:
    keys = _validate_keys(value, "dstKeys")
    if not keys:
        raise RuntimeError(f"{EFFECT_NAME}: dstKeys must contain at least one key", RESULT_INVALID_PARAMETER)
    if len(keys) > MAX_RESULTS:
        raise RuntimeError(f"{EFFECT_NAME}: dstKeys must contain at most {MAX_RESULTS} keys", RESULT_INVALID_PARAMETER)
    if len(set(keys)) != len(keys):
        raise RuntimeError(f"{EFFECT_NAME}: dstKeys must not repeat a key", RESULT_INVALID_PARAMETER)
    for i, key in enumerate(keys):
        if not key.lower().endswith(RESULT_EXTENSIONS):
            raise RuntimeError(
                f"{EFFECT_NAME}: dstKeys[{i}] must end with one of {RESULT_EXTENSIONS}",
                RESULT_INVALID_PARAMETER,
            )
    return keys


def _validate_level(feature: str, value) -> int | None:
    if feature not in LEVELLED:
        return None
    if value is None:
        return DEFAULT_LEVEL
    # bool is an int subclass: True isn't level 1.
    if type(value) is str and value.strip().isdigit():
        value = int(value.strip())
    if type(value) is not int or value not in LEVELLED[feature]:
        raise RuntimeError(
            f"{EFFECT_NAME}: level for {feature} must be one of {LEVELLED[feature]}",
            RESULT_INVALID_PARAMETER,
        )
    return value


def _validate_params(params: dict) -> dict:
    normalized = dict(params)
    normalized.pop("inference_style_list", None)
    name = require_text(normalized.get("feature"), "feature")
    feature = canonical_feature(name)
    if feature is None:
        raise RuntimeError(f"{EFFECT_NAME}: feature must be one of {FEATURE_NAMES}", RESULT_INVALID_PARAMETER)
    normalized["feature"] = feature
    normalized["level"] = _validate_level(feature, normalized.get("level"))
    return normalized


def validate_ai_hair_request(task_info: dict, task_id: str = "") -> AiHairRequest:
    """Validate the queue payload shape and extract the first action."""
    if not isinstance(task_info, dict):
        raise RuntimeError(f"{EFFECT_NAME}: task_info must be a dict", RESULT_INVALID_PARAMETER)

    effect_type = task_info.get("effect")
    if effect_type != EFFECT_NAME:
        raise RuntimeError(
            "Invalid effect type: %s" % effect_type,
            RESULT_INVALID_PARAMETER,
        )

    acts = task_info.get("acts")
    if not isinstance(acts, list) or not acts:
        raise RuntimeError(f"{EFFECT_NAME}: acts must contain at least one action", RESULT_INVALID_PARAMETER)

    act = acts[0]
    if not isinstance(act, dict):
        raise RuntimeError(f"{EFFECT_NAME}: acts[0] must be a dict", RESULT_INVALID_PARAMETER)

    params = act.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RuntimeError(f"{EFFECT_NAME}: acts[0].params must be a dict", RESULT_INVALID_PARAMETER)

    act_id = act.get("id", 0)
    if not isinstance(act_id, (str, int)):
        raise RuntimeError(f"{EFFECT_NAME}: acts[0].id must be a string or integer", RESULT_INVALID_PARAMETER)

    style_id, style_values = get_inference_style(params)
    applied_style_keys = apply_inference_style_values(params, style_values)

    return {
        "act_id": str(act_id),
        "params": _validate_params(params),
        "src_keys": _validate_src_keys(task_info.get("srcKeys", [])),
        "dst_keys": _validate_dst_keys(task_info.get("dstKeys", [])),
        "style_id": style_id,
        "has_style_values": isinstance(style_values, dict),
        "applied_style_keys": applied_style_keys,
    }
