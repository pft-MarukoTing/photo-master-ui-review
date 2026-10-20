"""Step 5's editor prompt, shared by every image-edit client."""

from config import PRESET_PROMPTS
from photo_agent.feature_resolver import build_compact_editor_prompt
from prompts import build_framing_sections
from photo_agent.text_overlay import editor_instruction


def overwrites_prompt(user_instruction):
    """True when the user's own text replaces the whole edit prompt."""
    user_instruction = user_instruction or {}
    return bool(user_instruction.get("overwrite_prompt")
                and (user_instruction.get("normalized_user_direction") or "").strip())


def assemble_edit_prompt(framed_analysis, crop_plan, *, preset_key, aspect_ratio, source_size,
                         single_pass_edit, revision_feedback=None):
    """Return (prompt_text, resolution_metadata, prompt_strategy) for one edit call.

    With overwrite_prompt, the user's own text is the whole prompt ("overwrite");
    otherwise the compact editor prompt is built ("resolved_compact").
    """
    user_instruction = framed_analysis.get("user_instruction") or {}
    user_direction = user_instruction.get("normalized_user_direction") or ""
    from photo_agent.side_reference import editor_instruction as side_instruction
    side_contract=side_instruction(user_instruction.get('original_user_direction') or user_direction)
    if overwrites_prompt(user_instruction):
        return user_direction.strip() + editor_instruction(framed_analysis) + side_contract, None, "overwrite"

    sections = build_framing_sections(framed_analysis, crop_plan, aspect_ratio, source_size)
    prompt_text, resolution_metadata = build_compact_editor_prompt(
        framed_analysis, PRESET_PROMPTS.get(preset_key) or PRESET_PROMPTS["natural"], user_direction, crop_plan,
        preset_key=preset_key, include_gaze=single_pass_edit, include_blink=single_pass_edit,
        revision_feedback=revision_feedback,
        raw_crop_instruction=sections["crop_instruction"],
        raw_style_instruction=sections["style_instruction"],
    )
    return prompt_text + editor_instruction(framed_analysis) + side_contract, resolution_metadata, "resolved_compact"
