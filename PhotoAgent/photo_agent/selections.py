"""Folds the user's review choices into an analysis before the finish stage.

The review screen (修圖建議 checkboxes, 人像修正 per-person toggles, Body
Reshape switch, pose/angle/lighting/size picks, 人像修正設定 toggles) used to be
applied onto the analysis in the browser (app.js's buildEffectiveAnalysis),
and only the already-rewritten analysis reached the backend. The frontend now
sends the analysis it reviewed plus these choices as a separate `selections`
object, and /continue, /rerun and /aipoc/submit call apply_selections() here,
so every entry point (and later the chat tools) shares one implementation.

selections (every key optional):
    advanced_options      dict  人像修正設定 toggles as currently set. When
                                present, correction_plan items naming a
                                disabled topic are stripped first, and the dict
                                replaces analysis["_advanced_options"].
    recipe_checked        list  修圖建議 checkbox state, indexed by position in
                                correction_plan AFTER that strip (the same list
                                the review screen rendered). False = rejected;
                                true/null/missing = kept.
    face_issue_overrides  dict  {"<subject_index>:<category>": False |
                                {"allowed": bool, "level": str}}, as produced
                                by results.js's getFaceIssueOverrides.
    body_reshape_enabled  bool  The Body Reshape review switch. null/missing
                                keeps the analysis's own _body_reshape_enabled.
    options               dict  {pose, angle, lighting, size}; "default" or
                                empty values are dropped.
"""

import copy

import prompts

_ADVANCED_TOPIC_KEYS = (
    "gaze_contact_detection",
    "eye_closed_detection",
    "hair_mess_detection",
    "smile_adjustment",
)

# Only composition reframing counts as "crop" here -- mirrors app.js's
# CROP_AXIS_NAMES, which the review screen uses for the same decision.
_CROP_AXIS_NAMES = {"crop", "cropping", "framing", "reframing"}

# Planner bookkeeping that must never reach the editor; the per-person
# body_reshape plans are the only hand-off.
_BODY_RESHAPE_PRIVATE_KEYS = (
    "_disabled_body_reshape_subjects",
    "_disabled_body_reshape_key_points",
    "_body_reshape_amount_overrides",
    "_body_reshape_execution_plan",
    "key_points",
    "raw_key_points",
    "target_pct",
    "effective_target_pct",
    "planner_target_pct",
    "planner_default_target_pct",
    "body_reshape_execution",
    "body_reshape_execution_plan",
)

SELECTION_KEYS = (
    "advanced_options",
    "recipe_checked",
    "face_issue_overrides",
    "body_reshape_enabled",
    "options",
)


def validate_selections(selections):
    """Returns an error message for a malformed `selections`, else None."""
    if not isinstance(selections, dict):
        return "selections must be a JSON object"
    expected = {
        "advanced_options": dict,
        "recipe_checked": list,
        "face_issue_overrides": dict,
        "body_reshape_enabled": bool,
        "options": dict,
    }
    for key, kind in expected.items():
        value = selections.get(key)
        if value is not None and not isinstance(value, kind):
            return f"selections.{key} must be a {kind.__name__}"
    return None


def _index(value):
    """Non-negative integer index, or None (bools and fractions rejected)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _override_subject_index(text):
    text = text.strip()
    if not text:
        return 0  # Number("") is 0 in the JS this replaced
    try:
        return _index(float(text))
    except ValueError:
        return None


def correction_axes_include_crop(entry):
    axes = entry if isinstance(entry, list) else [entry]
    return any(str(axis or "").strip().lower() in _CROP_AXIS_NAMES for axis in axes)


def strip_for_toggles(analysis, advanced_options):
    """prompts.strip_disabled_topic_items against the given toggles rather
    than the analysis's baked-in ones. A toggle counts as off only when it is
    literally False, the same rule the review screen renders with. The
    _advanced_options it reads is overwritten by apply_selections anyway."""
    toggles = {key: (advanced_options or {}).get(key) is not False for key in _ADVANCED_TOPIC_KEYS}
    return prompts.strip_disabled_topic_items({**analysis, "_advanced_options": toggles})


def _apply_body_reshape(effective, body_reshape_enabled, feature_enabled):
    review_enabled = (
        body_reshape_enabled
        if isinstance(body_reshape_enabled, bool)
        else effective.get("_body_reshape_enabled") is not False
    )
    raw_disabled = effective.get("_disabled_body_reshape_subjects")
    disabled_subjects = {
        index for index in (_index(v) for v in (raw_disabled if isinstance(raw_disabled, list) else []))
        if index is not None
    }

    has_nested_plans = False
    subjects = effective.get("subjects")
    if isinstance(subjects, list):
        for subject_index, subject in enumerate(subjects):
            if not isinstance(subject, dict) or not isinstance(subject.get("body_reshape"), dict):
                continue
            if not feature_enabled:
                del subject["body_reshape"]
                continue
            has_nested_plans = True
            if not review_enabled or subject_index in disabled_subjects:
                subject["body_reshape"]["should_edit"] = False

    for key in _BODY_RESHAPE_PRIVATE_KEYS:
        effective.pop(key, None)

    if has_nested_plans:
        # Nested plans are the source of truth; a leftover top-level prompt or
        # global flag could duplicate or suppress only part of the hand-off.
        effective.pop("body_reshape", None)
        effective.pop("_body_reshape_enabled", None)
        return
    body = effective.get("body_reshape")
    prompt = body.get("edit_prompt") if isinstance(body, dict) else None
    if feature_enabled and isinstance(prompt, str) and prompt.strip():
        effective["body_reshape"] = {"edit_prompt": prompt.strip()}
        if review_enabled:
            effective.pop("_body_reshape_enabled", None)
        else:
            effective["_body_reshape_enabled"] = False
    else:
        effective.pop("body_reshape", None)
        effective.pop("_body_reshape_enabled", None)


def _js_truthy(value):
    # {} and [] are truthy in the JS this replaced.
    return value is not None and value is not False and value != 0 and value != ""


def _analyzer_flagged_face_issue(issue, category):
    """Did the ANALYZER flag this issue (vs. the user forcing it on)? An
    existing _user_disabled counts: the turn-off that stamped it erased the
    verdict, so it is the only surviving record of one."""
    if not isinstance(issue, dict):
        return False
    if _js_truthy(issue.get("_user_disabled")):
        return True
    return issue.get("adjust_safe" if category == "smile_issue" else "should_fix") is True


def _apply_face_issue_overrides(effective, overrides):
    """Per-person "allow AI to fix this" choices.

    Turning an item off flips should_fix to False (smile's adjust_safe is
    left alone -- resolve_smile_targets drops a disabled subject before it
    reads it) AND lists the pair in _disabled_face_issues, which
    build_edit_prompt hard-excludes. If the analyzer had flagged it, the
    issue is also stamped _user_disabled, so the next version still renders
    it as 已設定不修正 rather than 無需修正 / 可以調整. Turning an item on
    clears that stamp and, over a negative verdict, sets _user_forced (and
    _user_level when a level was picked).
    """
    subjects = effective["subjects"]
    disabled_face_issues = []
    for key, override in overrides.items():
        parts = str(key).split(":")
        if len(parts) < 2:
            continue
        subject_index = _override_subject_index(parts[0])
        category = parts[1]
        if subject_index is None or subject_index >= len(subjects):
            continue
        subject = subjects[subject_index]
        if not isinstance(subject, dict) or not isinstance(subject.get(category), dict):
            continue
        issue = subject[category]
        if override is False:
            allowed = False
        elif isinstance(override, dict):
            allowed = override.get("allowed") is not False
        else:
            allowed = True

        if not allowed:
            # Read BEFORE the write below overwrites the field that says so.
            was_flagged = _analyzer_flagged_face_issue(issue, category)
            if category == "body_reshape":
                issue["should_edit"] = False
            elif category != "smile_issue":
                issue["should_fix"] = False
            if category != "body_reshape":
                disabled_face_issues.append({"subject_index": subject_index, "category": category})
                if was_flagged:
                    issue["_user_disabled"] = True
        elif category == "body_reshape":
            # An explicit ON must re-enable a plan saved with should_edit=false.
            issue["should_edit"] = True
        elif isinstance(override, dict):
            issue.pop("_user_disabled", None)
            if override.get("level"):
                issue["_user_level"] = override["level"]
            if category in ("hair_issue", "blink_issue", "gaze_issue"):
                issue["_user_forced"] = issue.get("should_fix") is not True
            elif category == "smile_issue":
                issue["_user_forced"] = True
    effective["_disabled_face_issues"] = disabled_face_issues


def _apply_recipe_checked(effective, base, recipe_checked):
    """Unchecked 修圖建議 items are dropped from correction_plan (what the editor
    reads) AND recorded as rejected_*, which build_edit_prompt turns into
    explicit "do not do this" / "preserve this instead" rules -- omitting them
    alone isn't enough, since other analysis fields could still lead the model
    to make the same change."""

    def rejected(i):
        return i < len(recipe_checked) and recipe_checked[i] is False

    def keep(key):
        if key not in base:
            effective.pop(key, None)
            return
        values = base[key]
        effective[key] = [v for i, v in enumerate(values) if not rejected(i)] if isinstance(values, list) else values

    def drop(key):
        values = base.get(key)
        return [v for i, v in enumerate(values) if rejected(i)] if isinstance(values, list) else []

    keep("correction_plan")
    keep("retouch_recipe")
    keep("correction_axes")
    effective["rejected_corrections"] = drop("correction_plan")
    # The human-readable text the checklist shows; lets a later re-render of
    # this (now shrunk) analysis still list the rejected item.
    effective["rejected_retouch_recipe"] = drop("retouch_recipe")
    effective["rejected_axes"] = drop("correction_axes")
    effective["rejected_preserve"] = drop("correction_inverse")


def apply_selections(analysis, selections, *, body_reshape_feature_enabled=None):
    """Returns the effective analysis for the finish stage. `analysis` is not
    modified. `selections` of None/{} applies only the always-on cleanup
    (Body Reshape planner keys, an empty style_options)."""
    selections = selections or {}
    if body_reshape_feature_enabled is None:
        body_reshape_feature_enabled = prompts.body_reshape_enabled()
    advanced_options = selections.get("advanced_options")

    # Strip first, so recipe_checked lines up with the list the review screen
    # rendered from these same toggles.
    base = strip_for_toggles(analysis, advanced_options) if advanced_options is not None else analysis
    base = copy.deepcopy(base)
    effective = copy.deepcopy(base)

    _apply_body_reshape(effective, selections.get("body_reshape_enabled"), body_reshape_feature_enabled)

    overrides = selections.get("face_issue_overrides")
    if overrides and isinstance(base.get("subjects"), list):
        _apply_face_issue_overrides(effective, overrides)

    # Read off the analysis at finish time by build_edit_prompt and the
    # gaze-repair pass, so a toggle changed on the results page must land here.
    if advanced_options is not None:
        effective["_advanced_options"] = copy.deepcopy(advanced_options)

    recipe_checked = selections.get("recipe_checked")
    if isinstance(recipe_checked, list) and recipe_checked:
        _apply_recipe_checked(effective, base, recipe_checked)

    # A previous rejected-reframe run kept the analyzer's crop proposal in a
    # private snapshot; restore it only once the crop item is approved again.
    kept_crop = any(correction_axes_include_crop(e) for e in (effective.get("correction_axes") or []))
    rejected_crop = any(correction_axes_include_crop(e) for e in (effective.get("rejected_axes") or []))
    source = effective.get("_rejected_reframing_source")
    if kept_crop and not rejected_crop and isinstance(source, dict):
        effective.update(source)
        effective.pop("_rejected_reframing_source", None)
        effective.pop("_preserve_original_composition", None)

    # Always assigned, even empty: the analysis may carry a previous run's
    # style_options, and clearing every pick back to default must erase it.
    effective["style_options"] = {
        key: value for key, value in (selections.get("options") or {}).items()
        if value and value != "default"
    }
    return effective
