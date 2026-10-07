"""Body skill: the approved per-person Body Reshape instructions."""

from prompts import body_reshape_enabled, build_body_reshape_edit_block, get_body_reshape_edit_prompt

from .base import CAP_BODY, MODE_HOLISTIC_BODY, QA_BODY, EditorFeature, EditorSkill


BODY_RESHAPE_FEATURE = EditorFeature(
    id="body_reshape",
    label="身形調整",
    description=(
        "Apply the analysis's planned natural contour and proportion refinement to a person's figure, "
        "keeping clothing, anatomy and the surrounding scene continuous."
    ),
    issue_field="body_reshape",
    verdict_field="should_edit",
)


class BodySkill(EditorSkill):
    name = "body"

    def features(self):
        return (BODY_RESHAPE_FEATURE,) if body_reshape_enabled() else ()

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

    def qa_checks(self, analysis):
        body_prompt = get_body_reshape_edit_prompt(analysis)
        body_qa_block = "" if not body_prompt else (
            "- Holistic Body Reshape instruction (copied from Analyze; judge this sentence's visible intent as written): "
            f"{body_prompt}\n"
            "  Compare the visible figure with the original and judge whether the requested overall contour/proportion "
            "improvement was carried out naturally. Check anatomy continuity, identity, face, clothing identity and "
            "semantic coverage, and nearby scene geometry. Do not require key points, regions, or numeric measurements; "
            "uncertainty should be reported rather than converted into a fabricated percentage.\n"
        )
        return [(QA_BODY, body_qa_block)]

    def qa_score_caps(self, analysis):
        body_prompt = get_body_reshape_edit_prompt(analysis)
        body_qa_score_cap = "" if not body_prompt else (
            "- For Body Reshape, redesigning clothing, changing semantic coverage, damaging anatomy continuity, or "
            "warping nearby content is a major preservation failure: pass must be false and score must be 3 or lower.\n"
        )
        return [(CAP_BODY, body_qa_score_cap)]
