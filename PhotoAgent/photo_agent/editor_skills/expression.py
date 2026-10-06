"""Expression skill: the user-selected smile level."""

from prompts import (
    _disabled_issue_subject_indices,
    _read_smile_states,
    resolve_smile_targets,
    smile_level,
)

from .base import EditorSkill
from .face import _BASE_PRESERVE, _EYE_IDENTITY_PRESERVE, _box


def _smile_contracts(analysis):
    targets = resolve_smile_targets(analysis)

    # An explicit user-selected level is kept when analysis produced no usable
    # reading: a missing or malformed analyzer value must not silently
    # turn a mandatory UI choice into "preserve expression".  Explicitly
    # unreadable faces remain excluded because there are no pixels on which
    # to execute a reliable expression edit.
    level = smile_level(analysis)
    if not targets and level not in (None, "auto") and not _read_smile_states(analysis):
        disabled = _disabled_issue_subject_indices(analysis, "smile_issue")
        for index, subject in enumerate(analysis.get("subjects") or []):
            if not isinstance(subject, dict) or not subject.get("is_person") or index in disabled:
                continue
            issue = subject.get("smile_issue") or {}
            raw_state = issue.get("smile_intensity") or issue.get("smile_state")
            normalized_raw = str(raw_state or "").strip().lower().replace("-", "_")
            if normalized_raw in {
                "unclear", "occluded_or_unclear", "uncertain",
                "不清楚", "不清晰", "無法判斷", "无法判断",
            }:
                continue
            target = {
                "label": (subject.get("label") or "").strip() or "畫面中的人",
                "box": subject.get("box") or {},
                "current": None,
                "target": level,
                "_subject_index": index,
            }
            targets.append(target)

    contracts = []
    for item in targets:
        contract = {
            "feature": "smile",
            "subject_index": item.get("_subject_index"),
            "subject": item.get("label") or "畫面中的人物",
            "box": _box(item),
            "source_state": ({"smile_level": item["current"]} if item["current"] else {}),
            "endpoint": {"smile_level": item["target"]},
            "direct_changes": ["mouth_expression"],
            "dependent_changes": [
                "facial_expression", "cheek_tension", "brow_expression",
                "periocular_expression", "eyelid_aperture",
            ],
            # This mirrors the existing smile block's useful independent
            # protections. gaze_direction is deliberately represented here:
            # the resolver removes it when gaze is also authorized.
            "preserve": sorted(_BASE_PRESERVE | _EYE_IDENTITY_PRESERVE | {"gaze_direction"}),
        }
        contracts.append(contract)
    return contracts


class ExpressionSkill(EditorSkill):
    name = "expression"

    def contracts(self, analysis, ctx):
        return _smile_contracts(analysis)

    def subject_lines(self, item, ctx):
        state_lines = []
        state = item["final_state"]
        source_state = item.get("source_state") or {}
        smile = state.get("smile_level")
        if smile:
            smile_text = {
                "neutral": "a neutral, non-smiling expression",
                "gentle": "a small, clearly readable natural smile",
                "broad": "a clearly broad, joyful smile",
                "laughing": "an unmistakably happy, intense and natural expression of genuine laughter",
            }[smile]
            replacement_guard = (
                "not a generic replacement or merely a broad posed smile"
                if smile == "laughing" else "not a generic replacement"
            )
            current_smile = source_state.get("smile_level")
            if current_smile:
                state_lines.append(
                    f"- Transform this person's existing {current_smile} expression into {smile_text} on the existing face—{replacement_guard}."
                )
            else:
                state_lines.append(f"- Render {smile_text} on the existing face—{replacement_guard}.")
        return state_lines
