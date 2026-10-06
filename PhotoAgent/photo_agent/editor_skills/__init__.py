"""Editor skills: each kind of edit renders its own part of the Step 5 prompt."""

from .base import EditContext, EditorSkill
from .expression import ExpressionSkill
from .eyes import EyesSkill
from .hair import HairSkill

# Order is the order each person's final-state lines are rendered in.
EDITOR_SKILLS = (ExpressionSkill(), EyesSkill(), HairSkill())

__all__ = ["EDITOR_SKILLS", "EditContext", "EditorSkill", "ExpressionSkill", "EyesSkill", "HairSkill"]
