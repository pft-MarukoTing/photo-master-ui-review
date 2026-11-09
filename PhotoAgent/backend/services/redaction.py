"""Keeps model prompts and cost data out of API responses outside debug mode.

Every analysis in a JSON response gains `executed`: which parts of the Editor
prompt actually ran, so the result tags never need the prompt text itself.
"""
import json
import re

from flask import current_app

from photo_agent.editor_skills.base import _axis_set

EXECUTED_STYLE_MARKERS = {
    "pose": re.compile(r"(?:^|\n)-\s*Pose\s*:", re.I),
    "angle": re.compile(r"(?:^|\n)-\s*Angle\s*:", re.I),
    "lighting": re.compile(r"(?:^|\n)-\s*Lighting\s*:", re.I),
    "size": re.compile(r"(?:^|\n)-\s*Framing size\s*:", re.I),
}

EXECUTED_FACE_MARKERS = {
    "blink_issue": [
        re.compile(r"Accidental blink repair priority:", re.I),
        re.compile(r"PRIMARY EYE-PAIR REPAIR", re.I),
        re.compile(r"\beyelid correction\b", re.I),
        re.compile(r"^\s*-\s*Eye-pair repair\s*\([^\n]*\):\s*(?:open|use)\b", re.I | re.M),
    ],
    "gaze_issue": [
        re.compile(r"Approved gaze repair execution instructions:", re.I),
        re.compile(r"ONLY task for this edit:[\s\S]{0,180}redirect[^\n]*gaze", re.I),
        re.compile(r"Use a .* eyes-(?:only|plus-head/face) correction[\s\S]{0,240}direct lens contact", re.I),
        re.compile(r"Required change:[\s\S]{0,900}(?:direct(?:ly)?|camera lens)[^\n]*", re.I),
        re.compile(r"^\s*-\s*Use a [^\n]*eyes-(?:only|plus-head/face) correction[^\n]*;\s*reach clear direct lens contact\.", re.I | re.M),
    ],
    "hair_issue": [
        re.compile(r"Hair tidying priority:", re.I),
        re.compile(r"Still-air hair \(user-selected, mandatory\):", re.I),
        re.compile(r"Hair level — (?:local cleanup|fully groomed|preserve motion)", re.I),
        re.compile(r"Hair (?:correction plan|target) —?", re.I),
    ],
    "smile_issue": [
        re.compile(r"Smile adjustment \(user-selected, mandatory\)", re.I),
        re.compile(r"target_smile:", re.I),
        re.compile(r"(?:Transform this person's existing|Render) .*? smile on the existing face", re.I),
    ],
}

# A crop item reaches the Editor rewritten as the framing goal (food: composition goal), never verbatim.
FRAMING_GOAL_MARKER = re.compile(
    r"(?:^|\n)[ \t]*-[ \t]*(?:Framing|Composition) goal:|fall where the framing goal places them:", re.I,
)
# A crop item reaches the Editor rewritten as the framing goal (food: composition goal), never verbatim.
FRAMING_GOAL_MARKER = re.compile(
    r"(?:^|\n)[ \t]*-[ \t]*(?:Framing|Composition) goal:|fall where the framing goal places them:", re.I,
)
GENERIC_SMILE_MARKER = re.compile(r"For every person whose expression is readable:\s*target_smile:", re.I)
BODY_RESHAPE_MARKER = re.compile(r"Body reshape execution instructions \(analysis-approved\):", re.I)
SUBJECT_SECTION_END = re.compile(
    r"\n[ \t]*(?:-\s*)?Subject:[ \t]*|\n[ \t]*(?:Shared facial execution rules|Body reshape execution instructions"
    r"|Targets|Other approved corrections|Finished light and color target|Scope is limited|Execution by method):",
    re.I,
)

# Removed from every response outside debug mode.
SECRET_ANALYSIS_KEYS = ("prompts", "usage", "_body_reshape_error", "_body_reshape_partial_failures")
SECRET_RESULT_KEYS = ("token_usage",)
SECRET_EXTRA_INFO_KEYS = ("body_reshape_edit_prompt", "body_reshape_edit_prompts")
_SCAN_TOKENS = (b'"prompts"', b'"token_usage"', b'"body_reshape_edit_prompt', b'"_body_reshape_error"')


def _editor_prompt(prompts):
    parts = [prompts.get(key) for key in ("image_edit", "gaze_repair_pass")]
    return "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def _matches(text, markers):
    return any(marker.search(text) for marker in markers)


def _subject_section(prompt, label):
    if not label:
        return ""
    heading = re.compile(rf"(?:^|\n)[ \t]*(?:-\s*)?Subject:[ \t]*{re.escape(label)}[ \t]*(?:\n|$)", re.I)
    match = heading.search(prompt)
    if not match:
        return ""
    end = SUBJECT_SECTION_END.search(prompt, match.end())
    return prompt[match.start():end.start() if end else len(prompt)]


def _subject_evidence(prompt, subject):
    if not isinstance(subject, dict):
        return None
    label = str(subject.get("label") or "").strip()
    section = _subject_section(prompt, label)
    entry = {"has_section": bool(section)}
    for field, markers in EXECUTED_FACE_MARKERS.items():
        entry[field] = bool(section) and _matches(section, markers)
    body = subject.get("body_reshape")
    body_prompt = body.get("edit_prompt").strip() if isinstance(body, dict) and isinstance(body.get("edit_prompt"), str) else ""
    entry["body_targeted"] = (body_prompt in prompt) if body_prompt else ((label in prompt) if label else True)
    return entry


def _recipe_ran(analysis, index, command, prompt):
    if not isinstance(command, str) or not command.strip():
        return False
    if command.strip() in prompt:
        return True
    axes = analysis.get("correction_axes") if isinstance(analysis.get("correction_axes"), list) else []
    return index < len(axes) and "crop" in _axis_set([axes[index]]) and bool(FRAMING_GOAL_MARKER.search(prompt))


def executed_evidence(analysis):
    """What the stored Editor prompt carried, or None when no Editor prompt ran."""
    prompts = analysis.get("prompts")
    prompt = _editor_prompt(prompts) if isinstance(prompts, dict) else ""
    if not prompt:
        return None
    plan = analysis.get("correction_plan") if isinstance(analysis.get("correction_plan"), list) else []
    subjects = analysis.get("subjects") if isinstance(analysis.get("subjects"), list) else []
    return {
        "style": {key: bool(marker.search(prompt)) for key, marker in EXECUTED_STYLE_MARKERS.items()},
        "recipe": [_recipe_ran(analysis, i, c, prompt) for i, c in enumerate(plan)],
        "face": {field: _matches(prompt, markers) for field, markers in EXECUTED_FACE_MARKERS.items()},
        "subjects": [_subject_evidence(prompt, s) for s in subjects],
        "generic_smile": bool(GENERIC_SMILE_MARKER.search(prompt)),
        "body_marker": bool(BODY_RESHAPE_MARKER.search(prompt)),
    }


def redact(value, debug):
    """Adds `executed` to every analysis found; outside debug also drops the secret keys."""
    if isinstance(value, list):
        return [redact(item, debug) for item in value]
    if not isinstance(value, dict):
        return value
    out = {key: redact(item, debug) for key, item in value.items()}
    if isinstance(value.get("prompts"), dict):
        out["executed"] = executed_evidence(value)
        if not debug:
            for key in SECRET_ANALYSIS_KEYS:
                out.pop(key, None)
    if not debug:
        for key in SECRET_RESULT_KEYS:
            out.pop(key, None)
        extra = out.get("extra-info")
        if isinstance(extra, dict):
            out["extra-info"] = {k: v for k, v in extra.items() if k not in SECRET_EXTRA_INFO_KEYS}
    return out


def redact_response(response):
    """after_request hook: rewrites JSON bodies that carry an analysis or result."""
    if response.direct_passthrough or response.mimetype != "application/json" or response.headers.get("Content-Encoding"):
        return response
    data = response.get_data()
    if not any(token in data for token in _SCAN_TOKENS):
        return response
    try:
        body = json.loads(data)
    except ValueError:
        return response
    response.set_data(current_app.json.dumps(redact(body, bool(current_app.config.get("DEBUG_UI", False)))))
    return response


def init_redaction(app):
    app.after_request(redact_response)
