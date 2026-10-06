"""The contract between the editor prompt and an editor skill."""

from dataclasses import dataclass, field

from prompts import AXIS_ALIASES


# Sort order of shared facial rules (EditorSkill.face_rules). The shared identity
# rule always comes first and the eye-makeup carry rule last.
RULE_EYELIDS = 20
RULE_EXPRESSION = 30
RULE_LAUGHTER = 40
RULE_SMILE_GAZE = 50
RULE_GAZE = 60

# Prompt modes a skill can switch on; the editor prompt's own wording depends on them.
MODE_HEAD_ALIGNED_GAZE = "head_aligned_gaze"
MODE_HOLISTIC_BODY = "holistic_body"


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
    # Resolver features that count as facial edits and so bring in the shared facial rules.
    facial_features = frozenset()

    def contracts(self, analysis, ctx):
        """Per-person feature contracts for the shared resolver (editor_skills.face)."""
        return []

    def subject_lines(self, item, ctx):
        """Final-state lines for one resolved person."""
        return []

    def identity_scope(self, resolved, ctx):
        """Text appended to the shared identity-geometry rule."""
        return ""

    def face_rules(self, resolved, ctx):
        """(order, line) pairs for the shared facial rules; order is one of the RULE_* values."""
        return []

    def execution_block(self, analysis, resolved, ctx):
        """A block placed after the shared facial rules, with its own leading newline."""
        return ""

    def prompt_modes(self, analysis, resolved, ctx):
        """The MODE_* values this skill switches on for this edit."""
        return set()

    def prompt_metadata(self, analysis):
        """Entries this skill adds to the editor prompt's metadata."""
        return {}

    def claims_correction(self, text, axes, analysis):
        """Whether this skill renders an approved correction_plan item itself instead of the general list."""
        return False

    def color_lines(self, analysis, claimed, ctx):
        """Lines appended to the light-and-color execution block; claimed holds this skill's (text, axes) items."""
        return []

    def color_metadata(self, claimed):
        """Entries this skill adds to the prompt metadata's color_contract."""
        return {}


def _axis_set(value):
    """Flatten the analysis's per-correction axis lists into canonical tags."""
    flattened = set()
    for item in value or []:
        for axis in item if isinstance(item, (list, tuple, set)) else [item]:
            name = str(axis or "").strip().lower()
            if name:
                flattened.add(AXIS_ALIASES.get(name, name))
    return flattened
