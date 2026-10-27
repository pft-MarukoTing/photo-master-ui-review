"""The contract between the editor prompt and an editor skill."""

from dataclasses import dataclass, field

from prompts import AXIS_ALIASES


# Facial rules, QA checks and QA score caps from every skill are merged by an
# order number each skill gives its own entries, lowest first. Shared facial
# rules run from 0 (the identity rule) to 100 (the eye-makeup carry rule).

# Prompt modes a skill can switch on; the editor prompt's own wording depends on them.
MODE_HEAD_ALIGNED_GAZE = "head_aligned_gaze"
MODE_HOLISTIC_BODY = "holistic_body"
MODE_USER_FACE_GEOMETRY = "user_face_geometry"


@dataclass(frozen=True)
class EditContext:
    """Edit-time choices every skill reads."""

    style_options: dict = field(default_factory=dict)
    include_gaze: bool = True
    include_blink: bool = True


@dataclass(frozen=True)
class FollowupEdit:
    """A second, isolated whole-image edit on Step 5's output (Step 5b)."""

    prompt: str
    # Run on the pipeline's top image tier instead of its follow-up model.
    top_tier: bool = False


@dataclass(frozen=True)
class EditorFeature:
    """An edit a skill offers the user or an agent: a photo-wide switch plus a decision per person."""

    id: str
    label: str
    # Tells an agent what the edit does and when to choose it.
    description: str
    # Per-person analysis field holding the analyzer's reading.
    issue_field: str
    # Field of issue_field whose True value means the analyzer recommends the edit.
    verdict_field: str
    # advanced_options key that switches the feature off photo-wide; empty when it has none there.
    toggle: str = ""
    # (value, label) choices for one person's level; empty when the edit has no levels.
    levels: tuple = ()
    # (advanced_options key, JSON schema) for further photo-wide choices.
    photo_options: tuple = ()

    def params_schema(self):
        """JSON schema of the choices this feature takes for one edit."""
        subject = {
            "subject_index": {"type": "integer", "minimum": 0},
            "allowed": {"type": "boolean"},
        }
        if self.levels:
            subject["level"] = {"type": "string", "enum": [value for value, _ in self.levels]}
        properties = {"enabled": {"type": "boolean"}}
        properties.update({key: schema for key, schema in self.photo_options})
        properties["subjects"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": subject,
                "required": ["subject_index", "allowed"],
                "additionalProperties": False,
            },
        }
        return {"type": "object", "properties": properties, "additionalProperties": False}

    def describe(self):
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "issue_field": self.issue_field,
            "verdict_field": self.verdict_field,
            "toggle": self.toggle,
            "levels": [{"value": value, "label": label} for value, label in self.levels],
            "params_schema": self.params_schema(),
        }


class EditorSkill:
    """One kind of edit (expression, eyes, hair, ...), active only when an approved item asks for it."""

    name = ""
    # Contract preserve properties the skill already renders in concrete visual terms.
    hidden_preserve = frozenset()
    # Resolver features that count as facial edits and so bring in the shared facial rules.
    facial_features = frozenset()

    def features(self):
        """The EditorFeature controls this skill offers; empty when it is driven only by approved corrections."""
        return ()

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
        """(order, line) pairs for the shared facial rules, order between 0 and 100."""
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

    def needs_top_tier_edit(self, analysis, single_pass_edit):
        """Whether Step 5 itself must run on the top image tier for this skill's edit to come out."""
        return False

    def followup_edit(self, analysis):
        """The FollowupEdit this skill needs after Step 5, or None."""
        return None

    def qa_checks(self, analysis):
        """(order, text) QA check entries; text ends with a newline."""
        return []

    def qa_score_caps(self, analysis):
        """(order, text) QA score caps; text ends with a newline."""
        return []

    def final_state_terms(self, analysis):
        """Names of the per-person final states this skill renders, for the style-priority rule."""
        return ()

    def claims_correction(self, text, axes, analysis):
        """Whether this skill renders an approved correction_plan item itself instead of the general list."""
        return False

    def color_lines(self, analysis, claimed, ctx):
        """Lines appended to the light-and-color execution block; claimed holds this skill's (text, axes) items."""
        return []

    def color_metadata(self, claimed):
        """Entries this skill adds to the prompt metadata's color_contract."""
        return {}

    def finalize_prompt(self, prompt, analysis, claimed, ctx):
        """The finished editor prompt after this skill's own edits; claimed holds this skill's (text, axes) items."""
        return prompt


def _axis_set(value):
    """Flatten the analysis's per-correction axis lists into canonical tags."""
    flattened = set()
    for item in value or []:
        for axis in item if isinstance(item, (list, tuple, set)) else [item]:
            name = str(axis or "").strip().lower()
            if name:
                flattened.add(AXIS_ALIASES.get(name, name))
    return flattened


def _style_change_footprint(style_options):
    """Properties that selected styles are authorized to replace."""
    options = {
        key: value for key, value in (style_options or {}).items()
        if value and value != "default"
    }
    footprint = set()
    if "pose" in options:
        footprint.update({
            "body pose", "head pose", "gesture", "body language", "limb placement",
            "subject silhouette", "within-subject spacing", "within-subject overlap",
        })
    if "angle" in options:
        footprint.update({"camera viewpoint", "scene perspective", "projected overlap"})
    if "lighting" in options:
        footprint.update({"lighting logic", "light/color atmosphere", "highlight and shadow rendering"})
    if "size" in options:
        footprint.update({"framing", "subject scale", "visible extent", "image boundaries"})
    return options, footprint
