"""The food branch's whole editor prompt, built from the reviewed analysis and the shared crop geometry."""

from aspect_ratio_rules import _label_ratio
from photo_agent.capabilities import ASPECT_RATIOS
from photo_agent.feature_resolver import _compact_raw_style_instruction
from prompts import (
    UNIFORM_SCALE_ONLY, USER_SCENE_CONSISTENCY_RULE, _expand_plan_lines, build_framing_sections,
    build_user_preserved_choices,
)

from .adapter import LOOK_AXES

EDIT_INTRO = (
    "You are editing the supplied food photograph, not creating a new image.\n"
    "You are the food photographer finishing your own shot: make it a food photo people want to eat, through clearly "
    "visible, committed changes, while it stays this same photograph."
)
EDIT_PRESERVATION = (
    "Keep unchanged, apart from the approved removals: the same dishes, ingredients, portions, shapes, counts, and "
    "arrangement; the same tableware, table, place{viewpoint}. {added}, everything keeps its place "
    "relative to everything else, and nothing is stretched or squashed."
)
# Style categories the food editor applies; pose would rearrange the dishes this prompt keeps in place.
FOOD_STYLE_CATEGORIES = ("angle", "lighting", "size")
LIGHTING_OWNERSHIP = (
    "The selected lighting owns the light: its source, direction, colour, and the light and shadow it casts belong to "
    "the finished photo. The finished look still sets the food's appeal, texture, and colour, and approved shadow "
    "items refer only to shadows already in the photograph."
)
# Fixed finished states the editor renders towards, whatever wording the analysis used for the item.
ITEM_DONE = {
    "shadow": (
        "Shadows to relight end with no trace that they were there: the same brightness, colour temperature, and "
        "contrast as the lit part of the same surface, their outline gone, with no leftover dark band, halo, or patch. "
        "The food's own shadow side ends only slightly darker than its lit side, as if filled by a reflector. "
        "Shadows to soften end clearly lighter with softer edges. Shadows named to stay are kept."
    ),
    "alignment": (
        "Rotate the whole photo, and correct its perspective, until the lines that should be vertical stand vertical "
        "and the lines that should be level sit level; crop away the exposed corners rather than painting them in or "
        "leaving borders. The camera viewpoint stays."
    ),
}
ALIGNMENT_ONLY_FRAME = (
    "Use the supplied image boundaries as the base frame. Apply the approved alignment correction, then trim only the "
    "exposed outer wedges needed for a clean rectangular canvas."
)
_NAMED_RATIO_TOLERANCE = 0.02


def _kind(axes):
    axes = set(axes if isinstance(axes, list) else [axes])
    if axes == set(LOOK_AXES):
        return "look"
    return next((kind for kind in ("crop", "alignment", "distraction", "shadow") if kind in axes), "other")


def _ratio_text(label, source_size):
    """The named ratio an output size stands for (1120:1408 -> 4:5), or the source's own shape for original."""
    if label in ("", "original", None):
        return f"{source_size[0]}:{source_size[1]}"
    try:
        ratio = _label_ratio(label)
    except AttributeError:
        return label
    for option in ASPECT_RATIOS:
        named = _label_ratio(option.value) if ":" in option.value else None
        if named and abs(ratio - named) / named <= _NAMED_RATIO_TOLERANCE:
            return option.value
    return label


def _trim_word(fraction):
    if fraction < 0.03:
        return None
    return "a little" if fraction < 0.1 else "some" if fraction < 0.22 else "a lot"


def _trim_text(crop):
    """Each trimmed side in words; the editor gets no coordinates."""
    sides = (("the left", crop["x"]), ("the right", 1 - crop["x"] - crop["w"]),
             ("the top", crop["y"]), ("the bottom", 1 - crop["y"] - crop["h"]))
    parts = [f"{word} off {name}" for name, amount in sides if (word := _trim_word(amount))]
    return "trimming " + ", ".join(parts) if parts else "with the edges almost unchanged"


def _framing_block(framing, crop, ratio, expand, aligning):
    if not crop and not expand:
        if aligning:
            return "Framing:\n" + ALIGNMENT_ONLY_FRAME + " " + UNIFORM_SCALE_ONLY
        return ("Framing:\nKeep the supplied image boundaries as the final frame; do not crop, reframe, expand, or pad "
                "the canvas. " + UNIFORM_SCALE_ONLY)
    lines = ["Framing:"]
    if expand:
        lines.append(f"Target output aspect ratio: {ratio}; the output canvas has to be this shape."
                     + (f" The photograph's part of it is a cut {_trim_text(crop)}." if crop else ""))
        lines += _expand_plan_lines(expand)
    else:
        lines.append(f"The final frame is a {ratio} cut straight from the photograph, {_trim_text(crop)}. Everything "
                     "inside keeps its original size and proportions; only the frame changes."
                     + (" The approved alignment below also rotates the whole photo." if aligning else ""))
        lines.append(UNIFORM_SCALE_ONLY)
    lines.append(f"- Composition goal: {framing['framing_goal']}")
    keep = "; ".join(k["label"] for k in framing["keep_in_frame"])
    if keep:
        lines.append(f"- Keep whole inside the frame: {keep}")
    return "\n".join(lines)


def _selected_styles(framed_analysis):
    options = framed_analysis.get("style_options") or {}
    return {key: options[key] for key in FOOD_STYLE_CATEGORIES if options.get(key) and options[key] != "default"}


def build_food_edit_prompt(framed_analysis, crop_plan, source_size, revision_feedback=None):
    """The editor prompt for an approved food-branch analysis; crop_plan is the shared geometry's fitted crop."""
    food = framed_analysis["food_branch"]
    approved = list(zip(framed_analysis.get("correction_plan") or [], framed_analysis.get("correction_axes") or []))
    kinds = [_kind(axes) for _, axes in approved]
    styles = _selected_styles(framed_analysis)
    widening = styles.get("size") in ("wide", "wider")
    style_sections = build_framing_sections(
        {**framed_analysis, "style_options": styles}, crop_plan, source_size=source_size,
    )
    # A selected framing size replaces the analysis's own reframing, as in the main editor prompt.
    expand = None if "size" in styles else framed_analysis.get("reframe_extension")
    crop = crop_plan
    if crop and crop["w"] > 0.965 and crop["h"] > 0.965 and not expand:
        crop = None
    ratio = _ratio_text(framed_analysis.get("recommended_aspect_ratio"), source_size)

    sections = [EDIT_INTRO]
    if "look" in kinds:
        sections.append("Finished look:\n" + food["edit_brief"])
    if widening:
        sections.append("Framing:\n" + style_sections["crop_instruction"])
    else:
        sections.append(_framing_block(food["framing"], crop, ratio, expand, "alignment" in kinds))
    items = [(plan, kind) for (plan, _), kind in zip(approved, kinds) if kind not in ("look", "crop")]
    if items:
        sections.append("Approved changes:\n" + "\n".join(
            f"- {plan}" + (f"\n  {ITEM_DONE[kind]}" if kind in ITEM_DONE else "") for plan, kind in items))
    preserved = build_user_preserved_choices(framed_analysis)
    if preserved:
        sections.append(preserved)
    sections.append(EDIT_PRESERVATION.format(
        viewpoint="" if "angle" in styles else ", and camera viewpoint",
        added="No dish, ingredient, or prop is added" if widening else "Nothing is added",
    ))
    style_block = _compact_raw_style_instruction(style_sections["style_instruction"], styles)
    if style_block:
        if "lighting" in styles:
            style_block += "\n- " + LIGHTING_OWNERSHIP
        sections.append("Mandatory style overrides:\n" + style_block)
    user_direction = ((framed_analysis.get("user_instruction") or {}).get("normalized_user_direction") or "").strip()
    if user_direction:
        sections.append("User direction (highest priority):\n" + user_direction + "\n" + USER_SCENE_CONSISTENCY_RULE)
    if revision_feedback:
        sections.append("Revision feedback:\n" + revision_feedback)
    return "\n\n".join(sections)
