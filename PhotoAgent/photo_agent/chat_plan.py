"""The chat session's edit plan: what the next render of a photo will use.

Every photo has its own plan, so changing one photo never changes another:

    settings      render settings (preset, model/quality, aspect ratio,
                  run_count, custom text), style picks and the 人像修正設定
                  toggles -- the part that can also be changed for every
                  photo at once.
    photo-only    the 修圖建議 checkboxes, per-person 人像修正 overrides,
                  the Body Reshape switch and the upload page's face picks.
                  These index into that photo's own analysis, so they are
                  only ever set one photo at a time.

The session keeps a copy of the settings as the defaults a newly added photo
starts from. A render snapshots the photo's plan (build_selections /
build_render_form) into the version it records, so editing a plan only
affects renders from then on.
"""

import copy
import math

from photo_agent import capabilities
from photo_agent.param_validation import normalize_advanced_options
from photo_agent.selections import validate_selections

USER_PROMPT_MAX_LEN = 2000
PHOTO_ONLY_KEYS = ("recipe_checked", "face_issue_overrides", "body_reshape_enabled", "prompt_faces")
PROMPT_FACES_MAX = 8
PROMPT_FACE_TEXT_MAX_LEN = 120


def default_settings():
    return {
        "preset": capabilities.DEFAULT_PRESET,
        "render_option": capabilities.DEFAULT_RENDER_OPTION,
        "analysis_model": capabilities.DEFAULT_ANALYSIS_MODEL,
        "aspect_ratio": capabilities.DEFAULT_ASPECT_RATIO,
        "run_count": capabilities.DEFAULT_RUN_COUNT,
        "user_prompt": "",
        "overwrite_prompt": False,
        "style_options": {},
        "advanced_options": normalize_advanced_options(None),
    }


def default_plan(settings=None):
    """A new photo's plan: `settings` (the session defaults) plus empty
    photo-only fields."""
    return {
        **copy.deepcopy(settings or default_settings()),
        "recipe_checked": [],
        "face_issue_overrides": {},
        "body_reshape_enabled": None,
        "prompt_faces": [],
    }


def settings_of(plan):
    return {k: copy.deepcopy(v) for k, v in plan.items() if k not in PHOTO_ONLY_KEYS}


def _validated_prompt_faces(value):
    """The upload page's face picks: [{box, ref, where}].

    A face can be pointed at before the photo has been analyzed, so the pick
    reaches the plan as words inside user_prompt. `ref` is the exact text it
    wrote there and `where` the part of it that stays true whatever the
    analysis says; the box is what ties the pick back to a real person once
    there is an analysis to tie it to (chat_brief.resolve_prompt_faces).
    """
    if not isinstance(value, list) or len(value) > PROMPT_FACES_MAX:
        raise ValueError(f"plan.prompt_faces must be a list of at most {PROMPT_FACES_MAX} entries")
    faces = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("plan.prompt_faces[] must be a JSON object")
        box = entry.get("box")
        numbers = [box.get(k) for k in ("x", "y", "w", "h")] if isinstance(box, dict) else []
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers) \
                or len(numbers) != 4:
            raise ValueError("plan.prompt_faces[].box must be {x, y, w, h} numbers")
        texts = {}
        for key, required in (("ref", True), ("where", False)):
            text = entry.get(key, "")
            if not isinstance(text, str) or len(text) > PROMPT_FACE_TEXT_MAX_LEN or (required and not text.strip()):
                raise ValueError(f"plan.prompt_faces[].{key} must be a string of at most "
                                 f"{PROMPT_FACE_TEXT_MAX_LEN} characters")
            texts[key] = text
        faces.append({"box": dict(zip(("x", "y", "w", "h"), (float(n) for n in numbers))), **texts})
    return faces


def _offered(entry, debug):
    return debug or not getattr(entry, "debug_only", False)


def _check_choice(key, value, entries, debug, id_attr="id"):
    for entry in entries:
        if getattr(entry, id_attr) == value and _offered(entry, debug):
            return None
    allowed = tuple(getattr(e, id_attr) for e in entries if _offered(e, debug))
    return f"plan.{key} must be one of {allowed}"


def _apply_setting(plan, key, value, debug):
    """Validated new value for one settings key, or raises ValueError."""
    if key == "preset":
        if value not in capabilities.PRESET_IDS:
            raise ValueError(f"plan.preset must be one of {capabilities.PRESET_IDS}")
    elif key == "render_option":
        error = _check_choice(key, value, capabilities.RENDER_OPTIONS, debug)
        if error:
            raise ValueError(error)
    elif key == "analysis_model":
        error = _check_choice(key, value, capabilities.ANALYSIS_MODELS, debug)
        if error:
            raise ValueError(error)
    elif key == "aspect_ratio":
        error = _check_choice(key, value, capabilities.ASPECT_RATIOS, debug, id_attr="value")
        if error:
            raise ValueError(error)
    elif key == "run_count":
        limit = capabilities.MAX_RUN_COUNT if debug else capabilities.GENERAL_MAX_RUN_COUNT
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
            raise ValueError(f"plan.run_count must be an integer from 1 to {limit}")
    elif key == "user_prompt":
        if not isinstance(value, str) or len(value) > USER_PROMPT_MAX_LEN:
            raise ValueError(f"plan.user_prompt must be a string of at most {USER_PROMPT_MAX_LEN} characters")
    elif key == "overwrite_prompt":
        if not isinstance(value, bool):
            raise ValueError("plan.overwrite_prompt must be a boolean")
    elif key == "style_options":
        if not isinstance(value, dict):
            raise ValueError("plan.style_options must be a JSON object")
        merged = {**plan["style_options"], **value}
        error = validate_selections({"options": merged})
        if error:
            raise ValueError(error.replace("selections.options", "plan.style_options"))
        value = {k: v for k, v in merged.items() if v not in (None, *capabilities.STYLE_UNSET_VALUES)}
    elif key == "advanced_options":
        if not isinstance(value, dict):
            raise ValueError("plan.advanced_options must be a JSON object")
        try:
            value = normalize_advanced_options({**plan["advanced_options"], **value})
        except RuntimeError as exc:
            raise ValueError("plan." + str(exc.args[0]).split(": ", 1)[-1])
    else:
        raise ValueError(f"plan.{key} is not a known setting")
    return value


def apply_plan_patch(current, patch, *, debug=False, settings_only=False):
    """Returns (new_plan, None) or (None, error_message); `current` is not
    modified.

    style_options, advanced_options and face_issue_overrides merge key by key
    (a null override removes that key, back to the analyzer's own verdict);
    every other field is replaced. settings_only rejects the photo-only
    fields -- the batch edit, and the session defaults, have no analysis for
    them to index into.
    """
    if not isinstance(patch, dict):
        return None, "plan must be a JSON object"
    plan = copy.deepcopy(current)
    try:
        for key, value in patch.items():
            if key in PHOTO_ONLY_KEYS:
                if settings_only:
                    raise ValueError(f"plan.{key} can only be set on one photo")
                if key == "face_issue_overrides":
                    if not isinstance(value, dict):
                        raise ValueError("plan.face_issue_overrides must be a JSON object")
                    merged = {**plan["face_issue_overrides"], **value}
                    value = {k: v for k, v in merged.items() if v is not None}
                elif key == "prompt_faces":
                    value = _validated_prompt_faces(value)
            else:
                value = _apply_setting(plan, key, value, debug)
            plan[key] = value
    except ValueError as exc:
        return None, str(exc)
    if not settings_only:
        error = validate_selections(build_selections(plan))
        if error:
            return None, error.replace("selections.", "plan.")
    return plan, None


def build_selections(plan):
    """The `selections` object apply_selections takes."""
    selections = {
        "advanced_options": copy.deepcopy(plan["advanced_options"]),
        "options": copy.deepcopy(plan["style_options"]),
        "recipe_checked": copy.deepcopy(plan["recipe_checked"]),
        "face_issue_overrides": copy.deepcopy(plan["face_issue_overrides"]),
    }
    if plan.get("body_reshape_enabled") is not None:
        selections["body_reshape_enabled"] = plan["body_reshape_enabled"]
    return selections


def build_render_form(plan, stage):
    """The params form api._parse_and_validate_params takes, for one job.

    run_count is always 1 here: the finish stage produces one image per job,
    and the render route fans out plan["run_count"] jobs itself, the same
    way the review screen fans out N /continue calls.
    """
    option = next(o for o in capabilities.RENDER_OPTIONS if o.id == plan["render_option"])
    form = {
        "preset": plan["preset"],
        "image_quality": option.quality,
        "image_model": option.image_model,
        "analysis_model": plan["analysis_model"],
        "aspect_ratio": plan["aspect_ratio"],
        "advanced_options": copy.deepcopy(plan["advanced_options"]),
        "run_count": 1,
    }
    if stage == "finish":
        form["user_prompt"] = plan["user_prompt"]
        form["overwrite_prompt"] = plan["overwrite_prompt"]
    return form
