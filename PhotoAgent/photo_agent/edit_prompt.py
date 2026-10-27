"""Step 5's editor prompt, shared by every image-edit client."""

import config
from config import PRESET_PROMPTS
from photo_agent.feature_resolver import build_compact_editor_prompt
from prompts import build_framing_sections, build_style_override_block
from photo_agent.text_overlay import editor_instruction
from photo_agent.branch_skills import branch_named


def overwrites_prompt(user_instruction):
    """True when the user's own text replaces the whole edit prompt."""
    user_instruction = user_instruction or {}
    return bool(user_instruction.get("overwrite_prompt")
                and (user_instruction.get("normalized_user_direction") or "").strip())


def build_overwrite_baseline_block(preset_key):
    """The light whole-photo retouch an overwrite run still gets after the user's request.

    The editor otherwise only follows the request, so a photo edited from 個人化需求
    alone came back no better looking than the original everywhere else.
    """
    preset = PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"]
    return (
        "Baseline retouch: beyond the request, give the whole photo a light, natural polish. "
        f"{preset} "
        "Keep it to light, color, tone, clarity, and texture, subtle enough that the photo still reads as the same shot. "
        "The request and any selected styles win wherever they touch the same aspect. This polish itself must not "
        "change the composition, framing, content, sky, background, pose, expression, or anyone's identity, and must "
        "not add or remove anything; only the request above and the selected styles may make such changes."
    )


def build_continuation_block():
    """What a 接著這張調整 edit must leave alone.

    The source is an already-retouched version, so any extra polish the editor adds
    stacks on top of the last one and drifts further from the photo with each round.
    """
    return (
        "This photo is already a finished, retouched version. Make only the change requested above and treat "
        "everything else as final.\n"
        "- Keep everything the request does not mention exactly as it is: every face and identity, expression, eyes, "
        "skin texture, hair, body shape, pose, clothing, background, and every object.\n"
        "- Do not retouch any further: no extra smoothing, slimming, relighting, color grading, contrast, saturation, "
        "or sharpening beyond what the request asks for.\n"
        "- Keep the same aspect ratio, framing, and crop; do not zoom, extend, shift, or reframe the scene."
    )


def build_continuation_original_block(restore):
    """Which image is which when a 接著這張調整 edit also gets the original.

    The original is only there for what the request brings back; anything else
    taken from it would undo the earlier retouch. Its framing may differ from
    the version's, since earlier rounds usually crop.
    """
    lines = [
        "Two images are attached.",
        "- The first image is the retouched version to edit. The output is this image with the change above: "
        "same framing, crop, and aspect ratio.",
        "- The second image is the untouched original photo it was made from. Use it only as a reference for what "
        "the request wants brought back to how it was; take nothing else from it.",
    ]
    if restore:
        lines.append(f"- Bring back from the original photo: {restore}.")
    lines.append("- The original may be framed or cropped differently; match the subject, not its framing.")
    return "\n".join(lines)


def assemble_edit_prompt(framed_analysis, crop_plan, *, preset_key, aspect_ratio, source_size,
                         single_pass_edit, revision_feedback=None):
    """Return (prompt_text, resolution_metadata, prompt_strategy) for one edit call.

    With overwrite_prompt, the user's own text leads the prompt ("overwrite"),
    followed by any style the user picked and, with ENABLE_OVERWRITE_BASELINE_RETOUCH, a light
    baseline retouch; otherwise the compact editor prompt is built ("resolved_compact").
    """
    user_instruction = framed_analysis.get("user_instruction") or {}
    user_direction = user_instruction.get("normalized_user_direction") or ""
    from photo_agent.side_reference import editor_instruction as side_instruction
    side_contract = (side_instruction(user_instruction.get('original_user_direction') or user_direction)
                     if user_instruction.get('chat_body_side_clarification', True) else '')
    if framed_analysis.get("_continuation") and user_direction.strip():
        # 接著這張調整: the source is already a finished version, so only this
        # round's request applies -- never the styles or a baseline retouch again.
        prompt_text = user_direction.strip() + "\n\n" + build_continuation_block()
        original = framed_analysis.get("_continuation_original")
        if original is not None:
            prompt_text += "\n\n" + build_continuation_original_block((original.get("restore") or "").strip())
        return prompt_text + editor_instruction(framed_analysis) + side_contract, None, "continuation"
    if overwrites_prompt(user_instruction):
        style_block = build_style_override_block(framed_analysis, alongside_request=True)
        prompt_text = "\n\n".join(filter(None, (
            user_direction.strip(), style_block,
            build_overwrite_baseline_block(preset_key) if config.ENABLE_OVERWRITE_BASELINE_RETOUCH else "")))
        return prompt_text + editor_instruction(framed_analysis) + side_contract, None, "overwrite"

    branch = branch_named(framed_analysis.get("analysis_branch"))
    if branch is not None:
        prompt_text = branch.build_edit_prompt(framed_analysis, crop_plan, source_size, revision_feedback)
        return prompt_text + editor_instruction(framed_analysis), None, f"{branch.name}_branch"

    sections = build_framing_sections(framed_analysis, crop_plan, aspect_ratio, source_size)
    prompt_text, resolution_metadata = build_compact_editor_prompt(
        framed_analysis, PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"], user_direction, crop_plan,
        preset_key=preset_key, include_gaze=single_pass_edit, include_blink=single_pass_edit,
        revision_feedback=revision_feedback,
        raw_crop_instruction=sections["crop_instruction"],
        raw_style_instruction=sections["style_instruction"],
    )
    return prompt_text + editor_instruction(framed_analysis) + side_contract, resolution_metadata, "resolved_compact"
