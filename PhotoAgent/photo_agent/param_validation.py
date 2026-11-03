"""Request validation for the photoAgent effect."""

from __future__ import annotations

import json
from typing import TypedDict

from engine_result_code import RESULT_INVALID_PARAMETER

from . import capabilities
from .utils import optional_bool, optional_text


EFFECT_NAME = "photoAgent"

# Every allowed value below comes from the feature registry (capabilities.py).
VALID_PRESETS = capabilities.PRESET_IDS
VALID_QUALITIES = capabilities.QUALITIES
VALID_IMAGE_MODELS = tuple(m.id for m in capabilities.IMAGE_MODELS)
VALID_ANALYSIS_MODELS = tuple(m.id for m in capabilities.ANALYSIS_MODELS)
# "auto" (from the proposed crop) / "original" (the source frame) / one fixed
# W:H frame.
VALID_ASPECT_RATIOS = tuple(c.value for c in capabilities.ASPECT_RATIOS)
VALID_OUTPUT_SCALES = tuple(c.value for c in capabilities.OUTPUT_SCALES)
MAX_RUN_COUNT = capabilities.MAX_RUN_COUNT

# Advanced Settings (人像設定 on the upload page) -- bundled into one
# "advanced_options" dict/field instead of each becoming its own named
# parameter, so a new setting only needs a new registry entry plus a matching
# check in prompts.py, instead of threading a new argument through every
# function between the frontend and the edit-prompt builders. Unknown keys
# are dropped rather than rejected (see normalize_advanced_options), the same
# tolerant-of-drift stance aipoc_client.py already takes for cross-service
# param mismatches. Toggles (bool) and choices (one of a fixed set of strings)
# are kept apart so ADVANCED_OPTION_DEFAULTS stays the "every value is a
# bool" contract the toggles rely on.
ADVANCED_OPTION_DEFAULTS = {o.key: o.default for o in capabilities.ADVANCED_OPTIONS if not o.choices}
SMILE_LEVELS = capabilities.SMILE_LEVELS
SMILE_LEVEL_ALIASES = capabilities.SMILE_LEVEL_ALIASES
ADVANCED_OPTION_CHOICES = {o.key: o.choices for o in capabilities.ADVANCED_OPTIONS if o.choices}
ADVANCED_OPTION_CHOICE_DEFAULTS = {o.key: o.default for o in capabilities.ADVANCED_OPTIONS if o.choices}


def normalize_advanced_options(value) -> dict:
    """Coerce `value` (None / dict / JSON string) into a full advanced_options
    dict -- every known key present, holding a bool (ADVANCED_OPTION_DEFAULTS)
    or one of its allowed strings (ADVANCED_OPTION_CHOICES), unknown keys
    dropped."""
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
    normalized.update(ADVANCED_OPTION_CHOICE_DEFAULTS)
    for key in ADVANCED_OPTION_DEFAULTS:
        if key in value:
            if not isinstance(value[key], bool):
                raise RuntimeError(
                    f"{EFFECT_NAME}: advanced_options.{key} must be a boolean",
                    RESULT_INVALID_PARAMETER,
                )
            normalized[key] = value[key]
    for key, allowed in ADVANCED_OPTION_CHOICES.items():
        if key in value:
            choice = SMILE_LEVEL_ALIASES.get(value[key], value[key]) if key == "smile_level" else value[key]
            if choice not in allowed:
                raise RuntimeError(
                    f"{EFFECT_NAME}: advanced_options.{key} must be one of {allowed}",
                    RESULT_INVALID_PARAMETER,
                )
            normalized[key] = choice
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
    stage = normalized.get("stage", "full")
    if stage not in ("analyze_preview", "analyze", "reframe", "finish", "full"):
        raise RuntimeError("unsupported stage", RESULT_INVALID_PARAMETER)
    try:
        from photo_agent.preview_context import validate_preview_binding, validate_preview_context
        if stage == "analyze_preview":
            normalized["preview_binding"] = validate_preview_binding(normalized.get("preview_binding"))
        if normalized.get("precomputed_preview") is not None:
            if stage != "analyze":
                raise ValueError("preview_context_requires_analyze")
            normalized["precomputed_preview"] = validate_preview_context(normalized["precomputed_preview"])
    except ValueError as exc:
        raise RuntimeError(str(exc), RESULT_INVALID_PARAMETER) from exc

    if normalized.get('analysis_user_requests') is not None:
        from .chat_analysis import validate
        try:
            if normalized.get('stage') != 'analyze':
                raise ValueError('analysis_requests_require_analyze_stage')
            context = normalized.get('precomputed_preview')
            normalized['analysis_user_requests'] = validate(normalized['analysis_user_requests'],
                known_ids=[p['preview_person_id'] for p in context['preview_people']] if context else None,
                source_sha256=context['binding']['source_sha256'] if context else None,
                analysis_model=normalized.get('analysis_model', capabilities.DEFAULT_ANALYSIS_MODEL))
        except ValueError as exc:
            raise RuntimeError(str(exc), RESULT_INVALID_PARAMETER) from exc

    preset = normalized.get("preset", capabilities.DEFAULT_PRESET)
    if not isinstance(preset, str) or preset not in VALID_PRESETS:
        raise RuntimeError(
            f"{EFFECT_NAME}: preset must be one of {VALID_PRESETS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["preset"] = preset

    image_quality = normalized.get("image_quality", capabilities.DEFAULT_QUALITY)
    if not isinstance(image_quality, str) or image_quality not in VALID_QUALITIES:
        raise RuntimeError(
            f"{EFFECT_NAME}: image_quality must be one of {VALID_QUALITIES}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["image_quality"] = image_quality

    image_model = normalized.get("image_model", capabilities.DEFAULT_IMAGE_MODEL)
    if not isinstance(image_model, str) or image_model not in VALID_IMAGE_MODELS:
        raise RuntimeError(
            f"{EFFECT_NAME}: image_model must be one of {VALID_IMAGE_MODELS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["image_model"] = image_model

    analysis_model = normalized.get("analysis_model", capabilities.DEFAULT_ANALYSIS_MODEL)
    if not isinstance(analysis_model, str) or analysis_model not in VALID_ANALYSIS_MODELS:
        raise RuntimeError(
            f"{EFFECT_NAME}: analysis_model must be one of {VALID_ANALYSIS_MODELS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["analysis_model"] = analysis_model
    context = normalized.get("precomputed_preview")
    binding = normalized.get("preview_binding") if stage == "analyze_preview" else context.get("binding") if context else None
    if binding and binding["analysis_model"] != analysis_model:
        raise RuntimeError("preview_analysis_model_mismatch", RESULT_INVALID_PARAMETER)

    aspect_ratio = normalized.get("aspect_ratio", capabilities.DEFAULT_ASPECT_RATIO)
    if not isinstance(aspect_ratio, str) or aspect_ratio not in VALID_ASPECT_RATIOS:
        raise RuntimeError(
            f"{EFFECT_NAME}: aspect_ratio must be one of {VALID_ASPECT_RATIOS}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["aspect_ratio"] = aspect_ratio

    output_scale = normalized.get("output_scale", capabilities.DEFAULT_OUTPUT_SCALE)
    if isinstance(output_scale, str):
        output_scale = capabilities.RETIRED_OUTPUT_SCALES.get(output_scale, output_scale)
    if not isinstance(output_scale, str) or output_scale not in VALID_OUTPUT_SCALES:
        raise RuntimeError(
            f"{EFFECT_NAME}: output_scale must be one of {VALID_OUTPUT_SCALES}",
            RESULT_INVALID_PARAMETER,
        )
    normalized["output_scale"] = output_scale

    normalized["user_prompt"] = optional_text(normalized.get("user_prompt"), "user_prompt")
    if 'literal_texts' in normalized:
        from photo_agent.text_overlay import validate_texts
        try:
            normalized['literal_texts'] = validate_texts(normalized['literal_texts'], normalized['user_prompt'] or '')
        except ValueError as exc:
            raise RuntimeError(str(exc), RESULT_INVALID_PARAMETER) from exc

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

    # analyze stage only: store a stub analysis instead of calling the
    # analyzer (pipeline.skipped_analysis_stage) -- the user asked for their
    # own 個人化需求 alone, so the edit need not wait on an analysis.
    skip_analysis = normalized.get("skip_analysis", False)
    if not isinstance(skip_analysis, bool):
        raise RuntimeError(
            f"{EFFECT_NAME}: skip_analysis must be a boolean",
            RESULT_INVALID_PARAMETER,
        )
    normalized["skip_analysis"] = skip_analysis

    # Skips pipeline.finish_stage's Step 5b (the isolated gaze-repair re-pass)
    # and relies on Step 5's own bundled edit call instead -- one edit-model
    # round trip instead of two, at the cost of Step 5b's insurance against a
    # bundled call under-applying the gaze fix. Any quality bump this needs
    # depends on the analysis's findings, so it happens later, in
    # pipeline.finish_stage.
    single_pass_edit = normalized.get("single_pass_edit", True)
    if not isinstance(single_pass_edit, bool):
        raise RuntimeError(
            f"{EFFECT_NAME}: single_pass_edit must be a boolean",
            RESULT_INVALID_PARAMETER,
        )
    normalized["single_pass_edit"] = single_pass_edit

    normalized["advanced_options"] = normalize_advanced_options(normalized.get("advanced_options"))

    # The app's review screen shows per-aspect findings cards; other clients leave them out of the analysis.
    findings_cards = normalized.get("findings_cards", False)
    if not isinstance(findings_cards, bool):
        raise RuntimeError(f"{EFFECT_NAME}: findings_cards must be a boolean", RESULT_INVALID_PARAMETER)
    normalized["findings_cards"] = findings_cards

    # How many independent edit variations the combined ("full" stage) job
    # generates from ONE shared analysis, entirely within itself -- lets a
    # multi-RUN request keep generating server-side regardless of whether the
    # client's tab is open/foregrounded to see it. Not used by the two-stage
    # analyze-only/finish-only stages (always exactly one result each).
    run_count = normalized.get("run_count", capabilities.DEFAULT_RUN_COUNT)
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
