"""The contract between the editor prompt and an editor skill."""

from dataclasses import dataclass, field

from prompts import AXIS_ALIASES


@dataclass(frozen=True)
class EditContext:
    """Edit-time choices every skill reads."""

    style_options: dict = field(default_factory=dict)
    include_gaze: bool = True
    include_blink: bool = True


class EditorSkill:
    """One kind of edit (expression, eyes, hair, ...), active only when an approved item asks for it."""

    name = ""
    # Contract preserve properties the skill already renders in concrete visual terms.
    hidden_preserve = frozenset()

    def contracts(self, analysis, ctx):
        """Per-person feature contracts for the shared resolver (editor_skills.face)."""
        return []

    def subject_lines(self, item, ctx):
        """Final-state lines for one resolved person."""
        return []


def _axis_set(value):
    """Flatten the analysis's per-correction axis lists into canonical tags."""
    flattened = set()
    for item in value or []:
        for axis in item if isinstance(item, (list, tuple, set)) else [item]:
            name = str(axis or "").strip().lower()
            if name:
                flattened.add(AXIS_ALIASES.get(name, name))
    return flattened
