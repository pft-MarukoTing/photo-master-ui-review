"""Request validation for the photoAgent effect."""

from __future__ import annotations

import json
from typing import TypedDict

from engine_result_code import RESULT_INVALID_PARAMETER

from .utils import optional_bool, optional_text


EFFECT_NAME = "photoAgent"
VALID_PRESETS = ("natural", "editorial", "product", "portrait", "custom")
VALID_QUALITIES = ("low", "medium", "high")
VALID_IMAGE_MODELS = ("gpt-image-2", "gpt-image-1.5", "nano-banana-2", "nano-banana-lite")
# "auto" (from the proposed crop) / "original" (the source frame) / one fixed
# W:H frame. The label list mirrors image_tools.USER_SELECTABLE_ASPECT_RATIO_LABELS,
# kept literal so this validator does not pull in that module's Pillow import.
VALID_ASPECT_RATIOS = (
    "auto", "original",
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9",
)
MAX_RUN_COUNT = 20

# Advanced Settings toggles (Eyes category on the upload page, and whatever
# joins it later) -- bundled into one "advanced_options" dict/field instead of
# each becoming its own named parameter, so a new toggle only needs a new
# entry here plus a matching check in prompts.py, instead of threading a new
# argument through every function between the frontend and build_analysis_
# instruction. Unknown keys are dropped rather than rejected (see
# normalize_advanced_options), the same tolerant-of-drift stance
# aipoc_client.py already takes for cross-service param mismatches.
ADVANCED_OPTION_DEFAULTS = {
    "eye_closed_detection": True,
    "hair_mess_detection": True,
    # Off by default, unlike the two above: those gate a repair the analysis
    # proposes only when it finds a defect, while this one is a transformation
    # the user elects and is applied whether or not the wind reads as a flaw.
    "calm_hair": False,
}


def normalize_advanced_options(value) -> dict:
    """Coerce `value` (None / dict / JSON string) into a full advanced_options
    dict -- every known key present with a bool value, unknown keys dropped."""
    if value is None or value == "":
        value = {}
    elif isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            raise RuntimeError(
                f"{EFFECT_NAME}: advanced_options must be valid JSON",
                RESULT_INVALID_PARAMETER,
            )
    if not isinstance(value, dict):
        raise RuntimeError(
            f"{EFFECT_NAME}: advanced_options must be a JSON object",
            RESULT_INVALID_PARAMETER,
        )
    normalized = dict(ADVANCED_OPTION_DEFAULTS)
    for key in ADVANCED_OPTION_DEFAULTS:
        if key in value:
            if not isinstance(value[key], bool):
                raise RuntimeError(
                    f"{EFFECT_NAME}: advanced_options.{key} must be a boolean",
                    RESULT_INVALID_PARAMETER,
                )
            normalized[key] = value[key]
    return normalized


class PhotoAgentRequest(TypedDict):
    act_id: str
    params: dict
    src_keys: list[str]
    dst_key: str


def get_inference_style(input_effect_params: dict):
    # TODO: implement if photoAgent ever supports inference_style_list overrides
    raise NotImplementedError("get_inference_style is not applicable to photoAgent")


def apply_inference_style_values(input_effect_params: dict, style_values, allowed_keys=None) -> list[str]:
    # TODO: implement if photoAgent ever supports style value injection
    raise NotImplementedError("apply_inference_style_values is not applicable to photoAgent")


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


def _validate_single_key(value, field_name: str) -> str:
    keys = _validate_keys(value, field_name)
    if not keys:
        raise RuntimeError(f"{EFFECT_NAME}: {field_name} must contain one base key", RESULT_INVALID_PARAMETER)
    return keys[0]


def _validate_params(params: dict) -> dict:
    normalized = dict(params)

    preset = normalized.get("preset", "natural")
    if not isinstance(preset, str) or preset not in VALID_PRESETS:
        raise RuntimeError(
            f"{EFFECT_NAME}: preset must be one of {VALID_PRESETS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["preset"] = preset

    image_quality = normalized.get("image_quality", "medium")
    if not isinstance(image_quality, str) or image_quality not in VALID_QUALITIES:
        raise RuntimeError(
            f"{EFFECT_NAME}: image_quality must be one of {VALID_QUALITIES}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["image_quality"] = image_quality

    image_model = normalized.get("image_model", "gpt-image-2")
    if not isinstance(image_model, str) or image_model not in VALID_IMAGE_MODELS:
        raise RuntimeError(
            f"{EFFECT_NAME}: image_model must be one of {VALID_IMAGE_MODELS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["image_model"] = image_model

    aspect_ratio = normalized.get("aspect_ratio", "auto")
    if not isinstance(aspect_ratio, str) or aspect_ratio not in VALID_ASPECT_RATIOS:
        raise RuntimeError(
            f"{EFFECT_NAME}: aspect_ratio must be one of {VALID_ASPECT_RATIOS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["aspect_ratio"] = aspect_ratio

    normalized["user_prompt"] = optional_text(normalized.get("user_prompt"), "user_prompt")

    # "取代最終送給編輯模型的完整指令" checkbox (index.html, beside 補充指令) --
    # when true, user_prompt replaces the whole templated edit/QA prompt
    # verbatim instead of being folded into it (see prompts.py).
    normalized["overwrite_prompt"] = optional_bool(normalized.get("overwrite_prompt"), "overwrite_prompt")

    ai_photo_editing = normalized.get("ai_photo_editing", True)
    if not isinstance(ai_photo_editing, bool):
        raise RuntimeError(
            f"{EFFECT_NAME}: ai_photo_editing must be a boolean",
            RESULT_INVALID_PARAMETER,
        )
    normalized["ai_photo_editing"] = ai_photo_editing

    run_qa = normalized.get("run_qa", True)
    if not isinstance(run_qa, bool):
        raise RuntimeError(
            f"{EFFECT_NAME}: run_qa must be a boolean",
            RESULT_INVALID_PARAMETER,
        )
    normalized["run_qa"] = run_qa

    normalized["advanced_options"] = normalize_advanced_options(normalized.get("advanced_options"))

    # How many independent edit variations the combined ("full" stage) job
    # generates from ONE shared analysis, entirely within itself -- lets a
    # multi-RUN request keep generating server-side regardless of whether the
    # client's tab is open/foregrounded to see it. Not used by the two-stage
    # analyze-only/finish-only stages (always exactly one result each).
    run_count = normalized.get("run_count", 1)
    if isinstance(run_count, bool) or not isinstance(run_count, int) or not (1 <= run_count <= MAX_RUN_COUNT):
        raise RuntimeError(
            f"{EFFECT_NAME}: run_count must be an integer between 1 and {MAX_RUN_COUNT}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["run_count"] = run_count

    return normalized


def validate_photo_agent_request(task_info: dict, task_id: str = "") -> PhotoAgentRequest:
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

    return {
        "act_id": str(act_id),
        "params": _validate_params(params),
        "src_keys": _validate_keys(task_info.get("srcKeys", []), "srcKeys"),
        "dst_key": _validate_single_key(task_info.get("dstKeys", []), "dstKeys"),
    }
