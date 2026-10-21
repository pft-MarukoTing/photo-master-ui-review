"""Branch skills: whole analysis paths that replace Call A for the photo categories Call 0 assigns them."""

from .base import BranchSkill
from .food import FoodBranchSkill

BRANCH_SKILLS = (FoodBranchSkill(),)


def branch_named(name):
    """The branch skill an analysis was made by, or None for the default Call A path."""
    return next((skill for skill in BRANCH_SKILLS if skill.name == name), None) if name else None


__all__ = ["BRANCH_SKILLS", "BranchSkill", "FoodBranchSkill", "branch_named"]
