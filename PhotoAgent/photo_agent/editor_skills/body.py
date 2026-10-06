"""Body skill: the approved per-person Body Reshape instructions."""

from prompts import build_body_reshape_edit_block, get_body_reshape_edit_prompt

from .base import MODE_HOLISTIC_BODY, EditorSkill


class BodySkill(EditorSkill):
    name = "body"

    def prompt_modes(self, analysis, resolved, ctx):
        return {MODE_HOLISTIC_BODY} if get_body_reshape_edit_prompt(analysis) else set()

    def execution_block(self, analysis, resolved, ctx):
        # The analyzer sentence is the complete Body Reshape contract.  Add it
        # once, verbatim, beside the other feature instructions; no per-subject
        # resolver state is created for it.
        if not get_body_reshape_edit_prompt(analysis):
            return ""
        block = build_body_reshape_edit_block(analysis)
        return "\n" + block if block else ""

    def prompt_metadata(self, analysis):
        body_prompt = get_body_reshape_edit_prompt(analysis)
        return {"body_reshape_edit_prompt": body_prompt} if body_prompt else {}
