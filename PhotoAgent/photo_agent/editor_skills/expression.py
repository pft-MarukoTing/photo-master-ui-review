"""Expression skill: the user-selected smile level."""

from prompts import (
    SMILE_LEVEL_LABELS,
    _disabled_issue_subject_indices,
    _read_smile_states,
    resolve_smile_targets,
    smile_level,
    smile_qa_expectation,
)

from .base import EditorFeature, EditorSkill
from .face import _BASE_PRESERVE, _EYE_IDENTITY_PRESERVE, _box, _resolved_features


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


_RULE_EXPRESSION = 30
_RULE_LAUGHTER = 40
_QA_SMILE = 40


SMILE_FEATURE = EditorFeature(
    id="smile",
    label="笑容調整",
    description=(
        "Set how strongly a person smiles. 'auto' gives a natural smile to people who are not smiling "
        "and leaves smiling people as they are; the other levels set the overall intensity directly."
    ),
    issue_field="smile_issue",
    verdict_field="adjust_safe",
    toggle="smile_adjustment",
    levels=(("auto", "自動"), ("neutral", "不笑"), ("gentle", "微笑"), ("broad", "開朗笑"), ("laughing", "大笑")),
    photo_options=(("smile_level", {"type": "string", "enum": list(SMILE_LEVEL_LABELS)}),),
)


class ExpressionSkill(EditorSkill):
    name = "expression"
    facial_features = frozenset({"smile"})

    def features(self):
        return (SMILE_FEATURE,)

    def final_state_terms(self, analysis):
        return ("smile",)

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

    def face_rules(self, resolved, ctx):
        rules = []
        if "smile" in _resolved_features(resolved):
            rules.append((_RULE_EXPRESSION,
                "- For every smile target, limit expression movement to mouth-corner position and opening, cheek soft-tissue lift, brow/periocular tension, and eyelid aperture over the fixed landmarks above. Coordinate them as one expression, using the captured mouth center, lip contour/volume, and visible dental characteristics rather than regenerating or beautifying the whole face."
            ))
            rules.append((_RULE_EXPRESSION,
                "- Retain the original skin age and fold pattern. Existing crow's-feet, under-eye creases, and nasolabial folds may compress minimally but must not become deeper, darker, sharper, longer, or more numerous."
            ))
        if any(item["final_state"].get("smile_level") == "laughing" for item in resolved):
            rules.append((_RULE_LAUGHTER,
                "- For laughter, show spontaneous joy coherently through the mouth, cheek lift, periocular response, and a compatible relaxed brow; do not leave a neutral, worried, stern, or angry upper face. Reach the requested intensity through coordinated expression—not an oversized mouth or reshaped facial features."
            ))
        return rules

    def qa_checks(self, analysis):
        # 笑容調整 has no recipe item to score against (the level is a 人像設定
        # choice applied at edit time, not a correction the analysis proposed), so
        # QA is told the expected endpoint here or it reads the new expression as
        # an unrequested change to the moment and caps the score for it.
        smile_expectations = smile_qa_expectation(analysis)
        if not smile_expectations:
            return []
        return [(
            _QA_SMILE,
            "- User-selected smile adjustment: the user asked for these people's smiles to be changed, so the new "
            f"expression is requested, not a lost moment -- {smile_expectations}. Check that each named person "
            "reached that overall intensity through a natural, coordinated facial expression. Identity, facial "
            "anatomy, skin texture, gaze direction, head pose, and all unrelated image content remain faithful "
            "to the original. A changed expression on anyone not named fails this check.\n",
        )]
