"""The contract for a branch skill: a whole analysis path that replaces Call A for one kind of photo."""


class BranchSkill:
    """Analysis, results-page reframing and the editor prompt for the photo categories it claims.

    The analysis it returns follows the shared contract (correction arrays, crop geometry, subjects), so the review
    screen, selections, local geometry and QA work unchanged; analysis["analysis_branch"] names the skill.
    """

    name = ""
    # Call 0 main_category values this skill takes over from Call A.
    categories = ()

    def analyze(self, image_bytes, aspect_ratio, photo_facts, analysis_model, user_requests=None):
        """Return (analysis, usage, prompt_text)."""
        raise NotImplementedError

    def reframe(self, image_bytes, analysis, aspect_ratio, analysis_model):
        """Return (analysis with only its framing re-planned for aspect_ratio, usage, prompt_text)."""
        raise NotImplementedError

    def build_edit_prompt(self, framed_analysis, crop_plan, source_size, revision_feedback=None):
        raise NotImplementedError
