"""Lens skill: an approved wide-angle correction, rendered as one block that leads the editor prompt."""

import re

from prompts import LENS_AXIS

from .base import EditorSkill

_LENS_BLOCK = (
    "Lens distortion correction:\n"
    "- The photo was taken with a wide-angle lens, which stretches content near the frame edges and corners outward, "
    "away from the frame center. Faces there look widened and elongated toward the nearest edge or corner, their outer "
    "half larger than the inner half; faces near the center are not affected.\n"
    "- Correct it the way a lens-profile correction does, across the whole photo: lines that are straight in reality but "
    "bow in the photo come out straight, objects near the edges lose their outward stretch, and each face and head near "
    "an edge or corner takes the proportions it would have at the frame center, its outer cheek, jaw and side of the "
    "head drawn back toward its own midline. Keep the composition and the placement of people and objects.\n"
    "- Faces stretched most, to correct with particular care: {names}."
)

# The shared rules that freeze proportions, facial projection and head/face locality make way for the correction.
_EXCEPTION = "apart from the lens distortion correction"
_RULE_EXCEPTIONS = (
    (r"([Pp]reserve (?:all )?internal proportions[^.\n]*)\.", r"\1, " + _EXCEPTION + "."),
    (r"- Fixed identity geometry: ", "- Fixed identity geometry, " + _EXCEPTION + ": "),
    (r"retaining facial geometry and complexion", "retaining complexion"),
    (r"Keep head yaw/pitch and projected eye–nose–mouth geometry fixed unless",
     "Keep head yaw/pitch and projected eye–nose–mouth geometry fixed, " + _EXCEPTION + ", unless"),
    (r"(- Preserve these unselected properties from the original:[^\n]*)\.", r"\1, " + _EXCEPTION + "."),
    (r"(- Preserve the source pose, viewpoint[^\n]*)\.", r"\1, " + _EXCEPTION + "."),
    (r"(- Apply facial edits locally[^\n]*)\.", r"\1, " + _EXCEPTION + "."),
)


def lens_targets(analysis):
    """'label (toward the direction)' for every person whose lens_issue the analysis marked for correction."""
    names = []
    for subject in analysis.get("subjects") or []:
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        issue = subject.get("lens_issue") or {}
        if issue.get("should_fix"):
            label = subject.get("label") or "畫面中的人物"
            direction = issue.get("_face_direction")
            names.append(f"{label} (toward the {direction})" if direction else label)
    return names


class LensSkill(EditorSkill):
    name = "lens"

    def claims_correction(self, text, axes, analysis):
        return LENS_AXIS in axes

    def finalize_prompt(self, prompt, analysis, claimed, ctx):
        names = lens_targets(analysis)
        if not claimed or not names:
            return prompt
        for pattern, replacement in _RULE_EXCEPTIONS:
            prompt = re.sub(pattern, replacement, prompt)
        intro, rest = prompt.split("\n\n", 1)
        return intro + "\n\n" + _LENS_BLOCK.format(names="; ".join(names)) + "\n\n" + rest
