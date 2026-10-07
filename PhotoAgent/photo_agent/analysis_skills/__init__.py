"""Category analysis skills that run after Call A, chosen by select_analysis_skills."""

from .base import AnalysisContext, AnalysisSkill
from .portrait import PortraitAnalysisSkill

ANALYSIS_SKILLS = (PortraitAnalysisSkill(),)


def produced_subject_fields():
    """Every per-subject field some analysis skill writes."""
    return frozenset().union(*(skill.produces for skill in ANALYSIS_SKILLS))


def select_analysis_skills(analysis):
    """The router: every skill whose category Call A found in this photo."""
    return [skill for skill in ANALYSIS_SKILLS if skill.applies_to(analysis)]


__all__ = [
    "ANALYSIS_SKILLS",
    "AnalysisContext",
    "AnalysisSkill",
    "PortraitAnalysisSkill",
    "produced_subject_fields",
    "select_analysis_skills",
]
