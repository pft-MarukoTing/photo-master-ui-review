"""The contract between the analysis orchestrator and a category analysis skill."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AnalysisContext:
    """One photo's call surface for skills; openai_client supplies the transport.

    request(instruction, text_format, error_label, *, with_face_crops=True) sends
    the instruction with the photo (and the local face crops) and returns the
    raw Responses envelope; parse(envelope) returns the reply's JSON.
    """

    request: Callable[..., dict]
    parse: Callable[[dict], dict]


class AnalysisSkill:
    """Extra analysis passes for one photo category, run after Call A."""

    name = ""
    # One line on what the skill judges, for whoever chooses or explains analyses.
    description = ""
    # Per-subject fields the skill writes; editor features read them through their issue_field.
    produces = frozenset()

    def applies_to(self, analysis):
        """Whether Call A's result calls for this skill."""
        raise NotImplementedError

    def run(self, analysis, ctx):
        """Return (analysis, usages). Must not raise: a failed pass falls back on its own."""
        raise NotImplementedError

    def skip(self, analysis):
        """Return the analysis with this skill's fields in their not-applicable shape."""
        raise NotImplementedError

    def run_early(self, roster, ctx):
        """Optional: run the passes that need only the people, on a roster built
        before Call A (subjects carry label, box, is_person, preview_person_id and
        local_detection_index). Return (roster, usages), or None to wait for Call A."""
        return None

    def adopt_early(self, analysis, early, ctx):
        """Return (analysis, usages) with run_early's result applied to Call A's
        analysis -- running any remaining passes that need Call A's own fields --
        or None when it no longer fits and run must be used instead."""
        return None

    def prompt_records(self, analysis):
        """analysis["prompts"] entries for this skill's passes, None where a pass did not apply."""
        raise NotImplementedError
