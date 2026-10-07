"""Editor skills: each kind of edit renders its own part of the Step 5 prompt."""

from .base import EditContext, EditorFeature, EditorSkill
from .body import BodySkill
from .expression import ExpressionSkill
from .eyes import EyesSkill
from .hair import HairSkill
from .skin import SkinSkill

# Order is the order each person's final-state lines are rendered in.
EDITOR_SKILLS = (ExpressionSkill(), EyesSkill(), HairSkill(), SkinSkill(), BodySkill())


def editor_features():
    """Every EditorFeature currently offered, in skill order."""
    return tuple(feature for skill in EDITOR_SKILLS for feature in skill.features())


def describe_editor_features():
    """JSON-ready list of the edits a user or agent can choose, with each one's parameter schema."""
    return [
        {"skill": skill.name, **feature.describe()}
        for skill in EDITOR_SKILLS
        for feature in skill.features()
    ]


__all__ = [
    "EDITOR_SKILLS", "BodySkill", "EditContext", "EditorFeature", "EditorSkill", "ExpressionSkill",
    "EyesSkill", "HairSkill", "SkinSkill", "describe_editor_features", "editor_features",
]
