"""Resolve interacting portrait features before rendering an editor prompt.

The resolver works on properties and final states, not prompt strings.  This
keeps one feature's captured-state preservation from silently cancelling a
different, authorized feature on the same face.
"""

from __future__ import annotations

import json
import re

from aspect_ratio_rules import AUTO_ASPECT_RATIO_LABELS
from config import STYLE_OPTION_VALUE_LABELS

from prompts import (
    OCCLUDED_PERSON_REMOVAL_RULE,
    USER_SCENE_CONSISTENCY_RULE,
    _has_person,
    build_reviewed_light_color_target,
    build_user_preserved_choices,
    has_approved_alignment_correction,
    has_approved_distraction_removal,
    is_dedicated_route_axes,
)
from photo_agent.editor_skills import EDITOR_SKILLS, EditContext
from photo_agent.editor_skills.base import (
    MODE_HEAD_ALIGNED_GAZE,
    MODE_HOLISTIC_BODY,
    MODE_USER_FACE_GEOMETRY,
    _axis_set,
    _style_change_footprint,
)
from photo_agent.editor_skills.face import render_shared_face_rules, resolve_feature_contracts
from photo_agent.editor_skills.skin import FACE_TONAL_PREFIX
from photo_agent.editor_skills.symmetry import (
    FACE_GEOMETRY_TASK_INTRO,
    FaceSymmetrySkill,
    face_geometry_guard,
    face_geometry_guard_line,
)

_HIDDEN_PRESERVE = frozenset().union(*(skill.hidden_preserve for skill in EDITOR_SKILLS))

_DEDICATED_ROUTE_TERMS = (
    "眼神", "虹膜", "瞳孔", "direct_camera", "gaze", "閉眼", "眼皮", "blink", "笑", "smile", "laugh",
    "髮", "头发", "hair", "bang", "fringe", "flyaway", "frizz",
)


def _has_dedicated_route(text, axes):
    """Eye, expression and hair items are executed from each person's own issue, not the general plan."""
    if axes:
        return is_dedicated_route_axes(axes)
    # Analyses saved before correction_axes existed can only be judged by their text.
    lowered = str(text or "").lower()
    return any(term in lowered for term in _DEDICATED_ROUTE_TERMS)


def _build_color_blocks(analysis, claimed, ctx, lighting_style=None):
    """Render one concise execution contract beneath the photo-specific target."""
    if lighting_style:
        lighting_label = STYLE_OPTION_VALUE_LABELS["lighting"].get(lighting_style, lighting_style)
        scene_line = f"- Apply the selected {lighting_label} atmosphere coherently across the scene."
    else:
        scene_line = "- Let scene exposure and palette change wherever the approved plan calls for it."
    execution_lines = [
        "Light and color execution:",
        "- Realize the finished target through the approved corrections as one coherent photographic grade. Make every named adjustment clearly visible with natural tonal transitions and believable materials.",
        scene_line,
    ]
    for skill in EDITOR_SKILLS:
        execution_lines.extend(skill.color_lines(analysis, claimed[skill.name], ctx))
    return "\n".join(execution_lines), ""


def _crop_plan_has_visible_trim(crop_plan, axis_threshold=0.08, area_threshold=0.12):
    """Return whether the resolved frame visibly tightens an axis or total area."""
    if not isinstance(crop_plan, dict):
        return False
    try:
        width = float(crop_plan.get("w", 1))
        height = float(crop_plan.get("h", 1))
    except (TypeError, ValueError):
        return False
    width = max(0.0, min(1.0, width))
    height = max(0.0, min(1.0, height))
    return (
        (1 - width) >= axis_threshold
        or (1 - height) >= axis_threshold
        or (1 - width * height) >= area_threshold
    )


def _common_ratio_label(aspect):
    """Name a pixel-size ratio such as 944:1680 by the common label within 2% of it, e.g. "9:16"."""
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(aspect or ""))
    if not match or not int(match.group(2)):
        return aspect
    ratio = int(match.group(1)) / int(match.group(2))
    for common in AUTO_ASPECT_RATIO_LABELS:
        w, h = (int(part) for part in common.split(":"))
        if abs(ratio / (w / h) - 1) <= 0.02:
            return common
    return f"{match.group(1)}:{match.group(2)}"


def _readable_aspect(aspect):
    """Common ratio label plus orientation, e.g. "9:16 portrait"."""
    label = _common_ratio_label(aspect)
    match = re.fullmatch(r"(\d+):(\d+)", str(label or ""))
    if not match or not int(match.group(2)):
        return aspect
    ratio = int(match.group(1)) / int(match.group(2))
    orientation = "square" if abs(ratio - 1) <= 0.02 else "portrait" if ratio < 1 else "landscape"
    return f"{label} {orientation}"


def _end_state_crop_instruction(
    aspect, tightness, closer, side_trim, crop_plan, has_body_reshape, local_shape_exception, framing_goal="",
):
    """Describe a one-axis crop as the finished frame, so the editor cuts instead of squeezing."""
    kept_axis = "h" if side_trim else "w"
    try:
        full_axis = float((crop_plan or {}).get(kept_axis, 0)) >= 0.98
    except (TypeError, ValueError):
        full_axis = False
    narrower, edges, grow_edges, distortion = (
        ("narrower", "left and right", "top and bottom", "narrow")
        if side_trim else
        ("shorter", "top and bottom", "left and right", "flatten")
    )
    span = f" at the photograph's full {'height' if side_trim else 'width'}" if full_axis else ""
    tightness_text = "" if full_axis else f" Overall it is {tightness}."
    exception = (
        "; the holistic body instruction remains the only deliberate figure-geometry exception"
        if has_body_reshape else ""
    )
    result = (
        f"The finished picture is a {_readable_aspect(aspect)} frame{span}, {narrower} than the photograph, so its {edges} edges "
        + (
            f"fall where the framing goal places them: {framing_goal.rstrip('。.')}."
            if framing_goal else "fall inside the photograph, guided by the subject."
        )
        + f"{tightness_text}{closer} Everything that remains in "
        "the frame, the subject and its surroundings alike, appears at exactly its original size and proportions, "
        f"as a straight cut from the photograph; only the frame is {narrower}. If you keep more of the "
        f"{'width' if side_trim else 'height'} than that, shrink the whole photograph evenly and continue the scene "
        f"past its {grow_edges} edges; never {distortion} anything{exception}."
    )
    return result + local_shape_exception if has_body_reshape else result


def _compact_raw_crop_instruction(
    raw_instruction, *, has_body_reshape=False, crop_plan=None,
    approved_crop_correction=False, framing_goal="",
):
    """Keep the raw builder's crop decision while removing explanatory prose."""
    raw = str(raw_instruction or "").strip()
    if not raw:
        return ""

    local_shape_exception = (
        " Preserve tilt, relative placement, and every unlisted proportion; the holistic body instruction may "
        "adjust the visible figure continuously while unrelated content remains source-matched."
        if has_body_reshape else
        " Preserve internal proportions, tilt, relative sizes, spacing, and overlap. Everything inside the frame keeps the proportions, the tilt, and the relative sizes and spacing it has in the input photograph."
    )

    if raw.startswith("No crop."):
        if not has_body_reshape:
            return (
                "Keep the supplied image boundaries as the final frame; do not crop, reframe, expand, or pad the canvas. "
                "Scale uniformly only—never stretch or squash. Resizing a single axis is not available. "
                "Preserve all internal proportions, tilt, relative sizes, and spacing."
            )
        return (
            "Keep the supplied image boundaries as the final frame; do not crop, reframe, expand, or pad the canvas. "
            "Scale the whole canvas uniformly only—never stretch or squash it. Resizing a single axis is not available."
            + local_shape_exception
        )

    if raw.startswith("Wider framing requested."):
        strength = ""
        match = re.search(r"subject's visible frame ratio about (\d+)% smaller", raw)
        if match:
            strength = f"; make the main subject about {match.group(1)}% smaller in the frame"
        result = (
            "Create the requested wider same-scene framing"
            f"{strength}. Show more plausible continuation of the existing environment while preserving subject identity, "
            "relative geometry, perspective, materials, and coherence with the final lighting. Use uniform scaling and same-scene outpainting only; "
            "never stretch edges, add blank bands, or introduce unrelated scenery."
        )
        return result + local_shape_exception if has_body_reshape else result

    if raw.startswith("Reframing:"):
        # The analysis decided this frame is reached by extending. Rebuilt from
        # the raw block's own numbers rather than summarized, because the
        # sentence this branch would otherwise produce says the opposite.
        plan = re.search(
            r"continuing the scene past the photograph's (top and bottom|left and right), "
            r"not by cropping into it: the subject already fills the (width|height).*?"
            r"Carry the scene about (\d+)% further in (width|height)",
            raw, re.S,
        )
        if plan:
            edges, filled_axis, grown, grow_axis = plan.groups()
            trim = re.search(r"about (\d+)% of the (width|height) still leaves the frame", raw)
            aspect_match = re.search(r"Target output aspect ratio:\s*([^\.\n]+)", raw)
            aspect = aspect_match.group(1).strip() if aspect_match else "the stated output ratio"
            result = (
                f"Reframe to {aspect} by continuing the same real scene past the photograph's {edges} edges, "
                f"about {grown}% further in {grow_axis} -- the subject already fills the {filled_axis}, so nothing "
                f"already visible leaves the frame on that axis. "
                + (
                    f"Then let about {trim.group(1)}% of the {trim.group(2)} go from its outer edges, guided by the "
                    "subject, never by narrowing what stays. "
                    if trim else ""
                )
                + "What you add must match the existing perspective, lighting, depth, materials, and background "
                "logic: never blank borders, padding, empty bands, stretched edges, or scenery from somewhere "
                "else. Extend and crop only; scale uniformly. "
                + (
                    "Preserve tilt, relative placement, and unlisted proportions while applying the holistic body "
                    "instruction."
                    if has_body_reshape else
                    "Preserve internal proportions, tilt, relative sizes, spacing, and overlap."
                )
            )
            return result + local_shape_exception if has_body_reshape else result

        aspect_match = re.search(r"Target output aspect ratio:\s*([^\.\n]+)", raw)
        tightness_match = re.search(r"Roughly how tight:\s*([^\.\n]+)", raw)
        aspect = aspect_match.group(1).strip() if aspect_match else "the stated output ratio"
        tightness = tightness_match.group(1).strip() if tightness_match else "use the stated crop tightness"
        same_shape = "same shape as the photograph" in raw and "nothing has to be given up" in raw
        near_full_frame = "close to the full frame" in tightness.lower()
        visible_trim = (
            _crop_plan_has_visible_trim(crop_plan)
            or (approved_crop_correction and bool(crop_plan))
        )
        if same_shape and near_full_frame and "Closer framing was requested" not in raw and not visible_trim:
            if not has_body_reshape:
                return (
                    "Keep the supplied framing and subject scale; at most trim a small amount from the outer edges if needed. "
                    "Do not zoom out, outpaint, or recompose. Scale uniformly only—never stretch content. Resizing a single axis is not available. Preserve internal "
                    "proportions, tilt, relative sizes, spacing, and overlap."
                )
            return (
                "Keep the supplied framing and subject scale; at most trim a small amount from the outer edges if needed. "
                "Do not zoom out, outpaint, or recompose. Scale the whole canvas uniformly only—never stretch content. Resizing a single axis is not available."
                + local_shape_exception
            )
        if same_shape and near_full_frame and visible_trim:
            tightness = "a visible tightening of the original frame"
        consequence = ""
        if "left and right edges cannot both be inside" in raw:
            consequence = (
                " Trim side content as needed rather than narrowing people or objects; the holistic body instruction "
                "remains the only deliberate figure-geometry exception."
                if has_body_reshape else
                " Trim side content as needed rather than narrowing people or objects."
            )
        elif "top and bottom edges cannot both be inside" in raw:
            consequence = (
                " Trim top or bottom content as needed rather than flattening people or objects; the holistic body "
                "instruction remains the only deliberate figure-geometry exception."
                if has_body_reshape else
                " Trim top or bottom content as needed rather than flattening people or objects."
            )
        # The raw builder's expand-permitted consequence, which offers a second
        # way to reach the shape. Detected separately because it deliberately
        # does not claim content must leave the frame.
        expanding = "cannot both survive as they are" in raw
        if expanding:
            consequence = (
                " Crop to reach it: let side content fall outside the frame rather than narrowing people or objects"
                if "full width and the frame's shape" in raw else
                " Crop to reach it: let top or bottom content fall outside the frame rather than flattening people "
                "or objects"
            ) + (
                ". If the subject already fills that axis and cropping would cut into it, instead continue the same "
                "real scene past the photograph's edge -- matching perspective, lighting, depth, and materials, "
                "only as far as the shape needs -- and keep everything the photograph shows, at its own proportions. "
                "Never blank borders, padding, empty bands, stretched edges, or scenery from somewhere else."
            )
        closer = " Come in visibly closer than the original." if "Closer framing was requested" in raw else ""
        side_trim = "left and right edges cannot both be inside" in raw
        if not expanding and (side_trim or "top and bottom edges cannot both be inside" in raw):
            return _end_state_crop_instruction(
                aspect, tightness, closer, side_trim, crop_plan, has_body_reshape, local_shape_exception,
                framing_goal,
            )
        operations = "Crop or extend the scene, and scale uniformly." if expanding else "Crop and scale uniformly."
        result = (
            f"Reframe to {aspect}; {tightness}.{consequence}{closer} Frame the visible main subject and meaningful "
            f"context comfortably inside. {operations} "
            + (
                "Preserve tilt, relative placement, and unlisted proportions while applying the holistic body instruction."
                if has_body_reshape else
                "Preserve internal proportions, tilt, relative sizes, spacing, and overlap."
            )
        )
        return result + local_shape_exception if has_body_reshape else result

    # A new raw-builder shape should remain correct before this compactor is
    # taught its semantics; verbosity is safer than silently dropping a rule.
    return raw


# A frame position or size written as a number: 22%, 0.18, 百分之二十四.
_GEOMETRY_NUMBER = r"(?:\d+(?:\.\d+)?\s*%|百分之[零〇一二兩三四五六七八九十百點]+|(?<![\d.])\d*\.\d+)"


def _is_geometry_clause(clause):
    """Whether a clause places the frame by numbers (a range, a start point, or box coordinates)."""
    coordinate_count = sum(
        bool(re.search(rf"(?i)(?<![a-z]){name}\s*=", clause))
        for name in ("x", "y", "w", "h")
    )
    return (
        coordinate_count >= 2
        or len(re.findall(_GEOMETRY_NUMBER, clause)) >= 2
        or bool(re.search(_GEOMETRY_NUMBER + r"\s*(?:起|處|至|之間|的位置)", clause))
    )


def _compact_crop_goal_text(raw_goal, resolved_ratio):
    """Keep crop semantics while normalizing stale ratios and dropping box coordinates."""
    text = str(raw_goal or "").strip()
    if not text:
        return ""

    # Geometry validation may move or grow the proposed box, so numeric
    # positions are dropped whole; deleting only their numbers leaves broken
    # clauses. A lone trim amount keeps its named content without the number.
    text = "".join(
        clause for clause in re.split(r"(?<=[，；。;])", text)
        if not _is_geometry_clause(clause)
    )
    text = re.sub(
        r"(?i)(?:by\s+)?(?:approximately|about|roughly|around|約|大約)?\s*"
        r"(?:\d+(?:\.\d+)?\s*%|百分之[零〇一二兩三四五六七八九十百點]+)\s*(?:of\s+the\s+|的)?",
        "",
        text,
    )

    ratio = str(_common_ratio_label(resolved_ratio) or "").strip()
    if ratio and ratio != "original":
        spoken_ratios = (
            "一比一", "三比二", "二比三", "三比四", "四比三", "四比五", "五比四",
            "九比十六", "十六比九", "二十一比九", "一比四", "四比一", "一比八", "八比一",
        )
        text = re.sub(r"(?<!\d)\d+\s*:\s*\d+(?!\d)", ratio, text)
        for spoken_ratio in spoken_ratios:
            text = text.replace(spoken_ratio, ratio)
    return text.strip(" ，；;")


def _analysis_crop_goal(analysis):
    """Return the approved crop goal text and whether it came from a reviewed correction item."""
    resolved_ratio = analysis.get("recommended_aspect_ratio") or (analysis.get("crop_box") or {}).get("aspect_ratio")
    crop_items = []
    axes = analysis.get("correction_axes") or []
    for index, raw_item in enumerate(analysis.get("correction_plan") or []):
        item_axes = _axis_set([axes[index]]) if index < len(axes) else set()
        if "crop" in item_axes and str(raw_item or "").strip():
            crop_items.append(_compact_crop_goal_text(raw_item, resolved_ratio))

    crop_box = analysis.get("crop_box") or {}
    goal = " ".join(crop_items).strip()
    has_approved_goal = bool(goal)
    if not goal:
        goal = _compact_crop_goal_text(
            crop_box.get("reason") or analysis.get("crop_recommendation"), resolved_ratio,
        )
    return goal, has_approved_goal


def _compact_analysis_crop_guidance(analysis, *, goal_in_framing=False):
    """Render the approved semantic crop intent without stale geometry."""
    goal, has_approved_goal = _analysis_crop_goal(analysis)
    # An end-state framing sentence already carries the goal, which names what leaves the frame.
    goal_in_framing = goal_in_framing and bool(goal)
    intent = analysis.get("crop_intent") or {}
    trimmed_edges = [
        edge for edge in ("left", "right", "top", "bottom")
        if intent.get(f"trim_{edge}") is True
    ]
    distractions = [
        str(item).strip() for item in (intent.get("remove_edge_distractions") or [])
        if str(item or "").strip()
    ]
    anchor = str(intent.get("subject_anchor") or analysis.get("roi_anchor") or "").strip()

    keep = list(dict.fromkeys(
        str(region.get("label") or "").strip()
        for region in (analysis.get("protected_regions") or [])
        if isinstance(region, dict) and str(region.get("label") or "").strip()
    ))
    extent = analysis.get("subject_extent_box") or {}
    extent_reason = str(extent.get("reason") or "").strip()
    if extent_reason and not keep:
        keep.append(extent_reason)

    lines = []
    if goal and not goal_in_framing:
        lines.append(f"- Framing goal: {goal}")
    # The reviewed correction is authoritative. Structured fields fill only
    # details it did not already state, keeping one crop plan in the prompt.
    if trimmed_edges and not has_approved_goal:
        lines.append(f"- Set the final frame by trimming the {', '.join(trimmed_edges)} edges.")
    if distractions and not has_approved_goal:
        lines.append(f"- Place outside the final frame: {'; '.join(distractions)}.")
    if anchor:
        lines.append(f"- Main visual anchor: {anchor}.")
    if keep and not goal_in_framing:
        lines.append(f"- Keep comfortably inside the final frame: {'; '.join(keep)}.")
    return "\n".join(lines)


def _analysis_has_alignment_correction(analysis):
    """Return whether reviewed work changes whole-frame tilt or perspective."""
    return has_approved_alignment_correction(analysis)


def _compact_raw_style_instruction(raw_instruction, style_options=None):
    """Keep the raw builder's functional option contracts, dropping wrapper repetition."""
    raw = str(raw_instruction or "").strip()
    if not raw:
        return ""

    options = {
        key: value for key, value in (style_options or {}).items()
        if value and value != "default"
    }
    selected = []
    for match in re.finditer(r"(?m)^- ([^:\n]+): (.+)$", raw):
        label, description = match.groups()
        description = description.strip()
        if label == "Angle" and options.get("size") and options.get("angle") in {"high", "low", "eye"}:
            # A vertical-angle recipe normally freezes distance and scale. A
            # simultaneous size recipe owns those properties, while angle still
            # owns the vertical viewing direction.
            sentences = re.split(r"(?<=[.!?])\s+", description)
            frozen_scale_terms = (
                "same distance", "distance does not change", "framing scale is unchanged",
                "same fraction of the frame", "same size and crop", "do not zoom",
                "move the camera closer or farther", "pull the camera back",
                "exactly the same size",
            )
            description = " ".join(
                sentence for sentence in sentences
                if not any(term in sentence.lower() for term in frozen_scale_terms)
            )
        if label == "Framing size" and options.get("lighting"):
            description = description.replace(
                "continuing the existing perspective, depth, lighting, and materials",
                "continuing the existing perspective, depth, and materials under the selected final lighting",
            )
        selected.append(f"- {label}: {description}")
    if not selected:
        # Preserve future raw formats losslessly rather than silently dropping
        # a user-selected transformation the compact renderer does not know.
        return raw

    requirements = [
        "Apply every selected item as a clearly visible same-scene transformation. "
        "For each selected aspect, this requirement overrides preserving its original state; preserve unselected aspects.",
    ]
    if "Pose override requirement:" in raw:
        requirements.append(
            "A pose selection must visibly change body language through posture, gesture, hand placement, body/head direction, "
            "or weight shift; color, lighting, or expression changes alone do not satisfy it. The re-posed body ends "
            "anatomically sound: joints bend within natural range, each hand keeps five fingers with correct joint "
            "direction, and limbs stay attached, matched in length, and unduplicated."
        )
    if "Angle override requirement (vertical only):" in raw:
        if options.get("size"):
            requirements.append(
                "Combine vertical angle and framing size: angle owns the up/down viewing direction; size owns camera distance, "
                "subject scale, crop, and frame extent. Keep the horizontal camera direction, scene layout, and side placement recognizable."
            )
        else:
            requirements.append(
                "A vertical-angle selection changes camera height/viewing angle only; keep the horizontal camera direction, "
                "scene layout, side placement, subject scale, and framing recognizable."
            )
    if "This is a true wider-framing transformation." in raw:
        ratio_match = re.search(r"visible frame ratio about (\d+)% smaller", raw)
        ratio = f" by about {ratio_match.group(1)}%" if ratio_match else ""
        requirements.append(
            f"A wider-framing selection must make the main subject visibly smaller{ratio} and reveal a plausible continuation "
            "of the same scene, never borders, stretched edges, or unrelated scenery."
        )
    elif "This is a true closer-framing transformation." in raw:
        requirements.append(
            "A closer-framing selection must make the chosen subject visibly larger while preserving identity, essential form, "
            "role cues, and clean breathing room."
        )

    return "\n".join(selected + [f"- {item}" for item in requirements])


def build_feature_contracts(analysis, *, include_gaze=True, include_blink=True):
    """Return active, editor-stage feature contracts from analysis/options.

    Every contract carries its parent ``subjects[]`` index so the resolver
    merges by identity; without it two overlapping faces are merged by box
    proximity and one person's final state lands on the other.
    """
    ctx = EditContext(include_gaze=include_gaze, include_blink=include_blink)
    contracts = []
    for skill in EDITOR_SKILLS:
        contracts.extend(skill.contracts(analysis, ctx))
    return contracts


def _face_first_prompt(
    analysis, resolved, ctx, claimed, *, task_intro, crop_block, other_corrections, visual_target_block,
    user_preserved_block, finish_text, style_block, global_lines, user_text, revision_text,
):
    """The prompt for a photo with a user-requested balanced face: the face balance first, every other
    approved edit listed together after it, then the preservation rules."""
    face_blocks, balanced_edits, other_people, preserve_blocks = [], [], [], []
    for item in resolved:
        balance_lines, other_lines = [], []
        for skill in EDITOR_SKILLS:
            target = balance_lines if isinstance(skill, FaceSymmetrySkill) else other_lines
            target.extend(skill.subject_lines(item, ctx))
        heading = [f"Subject: {item['subject']}", f"Approximate location: {json.dumps(item['box'], ensure_ascii=False)}"]
        if balance_lines:
            face_blocks.append("\n".join(heading + balance_lines + [face_geometry_guard_line(item["subject"])]))
            balanced_edits += other_lines
        elif other_lines:
            other_people += heading + other_lines
        preserve = ", ".join(value.replace("_", " ") for value in sorted(set(item["preserve"]) - _HIDDEN_PRESERVE))
        preserve_blocks.append(f"Preserve for {item['subject']}:\n- Preserve: {preserve}.")

    # The light target's own framing lines refer to the usual layout and are left out.
    target_lines = [
        line for line in visual_target_block.split("\n")[1:]
        if not line.startswith(("- Reviewed finished result", "- User-preserved choices below override"))
    ]
    color_lines = "\n".join(
        line for skill in EDITOR_SKILLS for line in skill.color_lines(analysis, claimed[skill.name], ctx)
    ).split("\n")
    tonal_lines = [line for line in color_lines if line.startswith(FACE_TONAL_PREFIX)]
    skin_lines = [line for line in color_lines if line and not line.startswith(FACE_TONAL_PREFIX)]
    removal = ["- " + OCCLUDED_PERSON_REMOVAL_RULE] if has_approved_distraction_removal(analysis) else []
    edits = balanced_edits + target_lines + [f"- {item}" for item in other_corrections] + removal + tonal_lines + other_people
    preserved_choices = "\n".join(
        line for line in user_preserved_block.split("\n") if line != "- Apply the approved correction plan everywhere else."
    )
    shared_face_block = render_shared_face_rules(EDITOR_SKILLS, resolved, ctx)
    execution_block = "".join(skill.execution_block(analysis, resolved, ctx) for skill in EDITOR_SKILLS)

    sections = [task_intro, "\n\n".join(face_blocks)]
    if edits:
        sections.append("Together with the face balance above, apply these approved edits:\n" + "\n".join(edits))
    sections.append(f"Required framing:\n{crop_block}")
    if preserved_choices:
        sections.append(preserved_choices)
    sections.append("\n\n".join(preserve_blocks))
    sections += [block.strip("\n") for block in (shared_face_block, "\n".join(skin_lines), execution_block) if block]
    sections.append(f"Finish:\n{finish_text}")
    if style_block:
        sections.append(f"Mandatory style overrides:\n{style_block}")
    sections.append(f"Global preservation:\n{global_lines}")
    sections.append(f"User direction:\n{user_text}{revision_text}")
    return "\n\n".join(sections) + "\n"


def build_compact_editor_prompt(
    analysis, preset_prompt, user_direction, crop_plan, *, preset_key="natural", include_gaze=True,
    include_blink=True, revision_feedback=None, raw_crop_instruction=None, raw_style_instruction=None,
):
    """Render one compact execution document from resolved final states."""
    contracts = build_feature_contracts(analysis, include_gaze=include_gaze, include_blink=include_blink)
    resolved = resolve_feature_contracts(
        contracts, subject_count=len(analysis.get("subjects") or [])
    )
    for item in resolved:
        if "lighting" in item["preserve"]:
            item["preserve"].remove("lighting")
            item["removed_preservations"] = sorted(set(item["removed_preservations"]) | {"lighting"})
    style_options, style_footprint = _style_change_footprint(analysis.get("style_options"))
    skill_ctx = EditContext(style_options=style_options, include_gaze=include_gaze, include_blink=include_blink)
    modes = set().union(*(skill.prompt_modes(analysis, resolved, skill_ctx) for skill in EDITOR_SKILLS))
    body_reshape_active = MODE_HOLISTIC_BODY in modes
    has_head_aligned_gaze = MODE_HEAD_ALIGNED_GAZE in modes
    # A user-directed facial final state (e.g. a balanced face) is an authorized change the generic
    # proportion, pose and locality rules below must make way for.
    user_face_geometry = MODE_USER_FACE_GEOMETRY in modes
    subject_style_footprint = set()
    if "pose" in style_options:
        subject_style_footprint.update({"body_pose", "head_pose"})
    if "lighting" in style_options:
        subject_style_footprint.add("lighting")
    if subject_style_footprint:
        for item in resolved:
            removed = set(item["preserve"]) & subject_style_footprint
            item["preserve"] = sorted(set(item["preserve"]) - subject_style_footprint)
            item["removed_preservations"] = sorted(set(item["removed_preservations"]) | removed)
    ratio = analysis.get("recommended_aspect_ratio") or (analysis.get("crop_box") or {}).get("aspect_ratio") or "original"
    raw_crop_block = str(raw_crop_instruction or "").strip()
    approved_crop_correction = "crop" in _axis_set(analysis.get("correction_axes"))
    size_reframe_active = style_options.get("size") in {"close", "closer", "wide", "wider"}
    compact_crop_block = _compact_raw_crop_instruction(
        raw_crop_block,
        has_body_reshape=body_reshape_active,
        crop_plan=crop_plan,
        approved_crop_correction=approved_crop_correction,
        framing_goal="" if size_reframe_active else _analysis_crop_goal(analysis)[0],
    )
    crop_instruction_source = "raw_edit_prompt" if compact_crop_block else "compact_fallback"
    if not compact_crop_block:
        compact_crop_block = (
            f"Use the approved crop to create the {ratio} final frame. Choose the frame around the visible subject "
            "and scale uniformly, preserving internal geometry and spatial relationships."
            if crop_plan else
            "Keep the supplied image boundaries as the final frame and scale uniformly."
        )

    raw_reframe_active = raw_crop_block.startswith(("Reframing:", "Wider framing requested."))
    composition_reframe_active = bool(crop_plan) if not raw_crop_block else raw_reframe_active
    framing_change_active = composition_reframe_active
    if compact_crop_block.startswith("Keep the supplied framing and subject scale"):
        framing_change_active = False

    alignment_change_active = _analysis_has_alignment_correction(analysis)
    alignment_only_active = (
        alignment_change_active
        and not composition_reframe_active
        and not size_reframe_active
    )
    if alignment_only_active:
        compact_crop_block = (
            "Use the supplied image boundaries as the base frame. Apply the approved alignment correction, "
            "then trim only the exposed outer wedges needed for a clean rectangular canvas. Scale uniformly "
            "and preserve internal proportions, relative sizes, spacing, and overlap."
        )
    elif alignment_change_active:
        compact_crop_block = compact_crop_block.replace(
            "Preserve internal proportions, tilt, relative sizes",
            "Preserve internal proportions, relative sizes",
        ).replace("Preserve tilt, relative placement", "Preserve relative placement")
    framing_change_active = framing_change_active or size_reframe_active or alignment_change_active
    if user_face_geometry:
        compact_crop_block = re.sub(
            r"([Pp]reserve (?:all )?internal proportions[^.]*)\.", r"\1 outside user-directed final states.",
            compact_crop_block,
        )
    end_state_crop_active = compact_crop_block.startswith("The finished picture is") and not size_reframe_active
    compact_crop_guidance = ""
    if framing_change_active and not size_reframe_active:
        compact_crop_guidance = _compact_analysis_crop_guidance(analysis, goal_in_framing=end_state_crop_active)
        if compact_crop_guidance:
            compact_crop_block += "\n" + compact_crop_guidance

    subject_blocks = []
    correction_axes = analysis.get("correction_axes") or []
    for item in resolved:
        state_lines = []
        for skill in EDITOR_SKILLS:
            state_lines.extend(skill.subject_lines(item, skill_ctx))
        # Identity properties a skill renders in concrete visual terms are not
        # repeated here by their internal contract names.
        display_preserve = set(item["preserve"]) - _HIDDEN_PRESERVE
        preserve = ", ".join(value.replace("_", " ") for value in sorted(display_preserve))
        subject_blocks.append(
            f"Subject: {item['subject']}\n"
            f"Approximate location: {json.dumps(item['box'], ensure_ascii=False)}\n"
            + "\n".join(state_lines)
            + f"\n- Preserve: {preserve}."
        )

    other_corrections = []
    claimed = {skill.name: [] for skill in EDITOR_SKILLS}
    for index, raw_item in enumerate(analysis.get("correction_plan") or []):
        item = str(raw_item)
        axes = _axis_set([correction_axes[index]]) if index < len(correction_axes) else set()
        if _has_dedicated_route(item, axes):
            # Call A occasionally violates its schema and repeats a gaze, blink
            # or hair repair here; never let that unscoped copy become an Other
            # approved correction.
            continue
        # Required framing already carries the authoritative compacted raw crop
        # decision. Repeating an analysis crop item here can turn a same-shape,
        # near-full-frame instruction into a second generative reframe request.
        if "crop" in axes:
            continue
        owner = next((skill for skill in EDITOR_SKILLS if skill.claims_correction(item, axes, analysis)), None)
        if owner is None:
            other_corrections.append(item)
        else:
            claimed[owner.name].append((item, axes))
    correction_block = "\n".join(f"- {item}" for item in other_corrections) or "- No additional approved local corrections."
    removal_face_block = (
        "\n\nRequired people cleanup during approved removal:\n- " + OCCLUDED_PERSON_REMOVAL_RULE
        if has_approved_distraction_removal(analysis) else ""
    )
    color_block, skin_block = _build_color_blocks(
        analysis, claimed, skill_ctx, lighting_style=style_options.get("lighting"),
    )
    visual_target_block = build_reviewed_light_color_target(
        analysis, lighting_style=style_options.get("lighting")
    )
    user_preserved_block = build_user_preserved_choices(analysis)
    has_person = _has_person(analysis)
    subject_section = ""
    if has_person:
        subjects_block = "\n\n".join(subject_blocks) or "No resolved facial or full-groom transformation is active."
        shared_face_block = render_shared_face_rules(EDITOR_SKILLS, resolved, skill_ctx)
        skill_execution_block = "".join(
            skill.execution_block(analysis, resolved, skill_ctx) for skill in EDITOR_SKILLS
        )
        subject_section = (
            "\nResolved subject final states:\n"
            "Locations identify subjects only; they are not target positions.\n"
            f"{subjects_block}\n{shared_face_block}{skill_execution_block}"
        )
    user_text = user_direction.strip() or "No additional direction."
    # A supplied user direction outranks every other block of this prompt.
    user_priority_text = (
        "\nThis user direction has the highest priority.\n" + USER_SCENE_CONSISTENCY_RULE
        if user_text != "No additional direction." else ""
    )
    revision_text = f"\n\nRevision feedback:\n{revision_feedback}" if revision_feedback else ""
    # Compact mode deliberately does not reuse prose that freezes a captured
    # expression (the portrait preset historically does). These endpoint-safe
    # finishes describe only rendering qualities, leaving subject state to the
    # resolved blocks above.
    finish = {
        "natural": "Finish with photographic realism, clean detail, and natural texture.",
        "editorial": "Finish with editorial polish, clean detail, and natural texture.",
        "product": "Finish with commercial clarity, truthful geometry, and material detail.",
        "portrait": "Finish with natural portrait detail, identity, facial geometry, and skin texture intact.",
        "custom": "Keep the finish photographic, coherent, and faithful to the supplied image.",
    }.get(preset_key, preset_prompt)
    raw_style_block = str(raw_style_instruction or "").strip()
    compact_style_block = _compact_raw_style_instruction(raw_style_block, style_options)
    style_instruction_source = "raw_edit_prompt" if compact_style_block else "none"
    style_text = compact_style_block or "No style override."

    pose_target_boundary = (
        "\nPose ownership boundary:\n"
        "- Any captured pose, action, stillness, mood, or body-language wording inside the light/color target is "
        "descriptive context only. It must not preserve or restore the original pose against the selected pose override."
        if "pose" in style_options else ""
    )
    pose_execution_order = (
        "\nPose-first execution order:\n"
        "- Establish the visibly different pose, gesture, head/body direction, limb placement, and silhouette first. "
        "Then render the resolved local smile, eye, gaze, hair, and holistic body instruction inside that new pose. "
        "A changed expression or gaze alone does not complete the pose transformation."
        if "pose" in style_options else ""
    )

    if "pose" in style_options:
        # A pose necessarily changes within-subject spacing, overlaps,
        # silhouette, and sometimes visible extent. Preserve only scene-level
        # relationships here; the style footprint owns the subject internally.
        spatial_preserve = [
            "scene geometry outside the selected subject",
            "positions of unselected people and objects",
            "scene-level depth order",
        ]
        if "angle" not in style_options:
            spatial_preserve.append("camera viewpoint")
        if "size" not in style_options and not framing_change_active:
            spatial_preserve.append("final framing")
    else:
        spatial_preserve = ["relative positions", "depth order", "spacing", "overlap"]
        if "angle" in style_options:
            spatial_preserve = ["underlying scene layout"]
        if "size" not in style_options and not framing_change_active:
            spatial_preserve.extend(["scale", "visible extent", "framing"])
        elif alignment_only_active:
            spatial_preserve.append("scale")
    if "pose" not in style_options:
        spatial_preserve.extend(["pose", "gesture", "interaction points"])
    spatial_preserve_text = ", ".join(spatial_preserve)
    if user_face_geometry:
        spatial_preserve_text += "; a user-directed final state still reshapes that person's face and features"
    if style_footprint:
        style_scope_text = "- Selected style options may change only: " + ", ".join(sorted(style_footprint)) + "."
    elif end_state_crop_active:
        style_scope_text = "- Required framing owns the final frame edges and visible extent" + (
            "; approved alignment owns tilt and perspective" if alignment_change_active else ""
        ) + "; everything kept keeps its source proportions, pose and viewpoint."
    elif alignment_change_active and composition_reframe_active:
        style_scope_text = "- Required framing owns the final frame edges, frame-relative subject scale, and visible extent; approved alignment owns tilt and perspective. Preserve the source pose and viewpoint."
    elif alignment_only_active:
        style_scope_text = "- Approved alignment owns tilt, perspective, and the minimal edge trim it requires; preserve the source subject scale, pose, and viewpoint."
    elif framing_change_active:
        style_scope_text = "- Required framing owns the final frame edges, frame-relative subject scale, and visible extent; preserve the source pose and viewpoint."
    else:
        style_scope_text = "- Preserve the source pose, viewpoint, subject scale, visible extent, and framing."
    if user_face_geometry and not style_footprint:
        style_scope_text = (
            style_scope_text[:-1] + ", apart from the small, natural head correction a user-directed final state uses."
        )
    if "pose" in style_options:
        gaze_endpoint_text = (
            "plus the direct holistic body-reshape instruction"
            if body_reshape_active else
            "plus any explicitly resolved gaze endpoint"
        )
        facial_locality = (
            "- Apply facial feature edits locally. Head/body movement is limited to the selected pose style "
            f"{gaze_endpoint_text}."
        )
    elif has_head_aligned_gaze and body_reshape_active:
        facial_locality = (
            "- Apply facial edits locally. Only the resolved eyes-plus-head/face gaze endpoint may make its specified "
            "bounded head/face alignment. Apply the direct holistic body instruction with continuous, natural "
            "garment and adjacent-background follow-through; keep unlisted regions fixed."
        )
    elif has_head_aligned_gaze and user_face_geometry:
        facial_locality = (
            "- Apply facial edits locally. Only a resolved eyes-plus-head/face gaze endpoint, or a user-directed final "
            "state within a small, natural head correction, may make its specified bounded head/face change; "
            "keep the body and surrounding content fixed."
        )
    elif has_head_aligned_gaze:
        facial_locality = (
            "- Apply facial edits locally. Only the resolved eyes-plus-head/face gaze endpoint may make its specified bounded head/face alignment; "
            "keep the body and surrounding content fixed."
        )
    elif body_reshape_active:
        facial_locality = (
            "- Apply facial edits locally. Apply the direct holistic body instruction with only the necessary "
            "garment and adjacent-background follow-through; do not move the head or unlisted regions."
        )
    else:
        facial_locality = (
            "- Apply facial edits locally within the required final frame; keep the visible head, body, and scene content source-matched."
            if framing_change_active else
            "- Apply facial edits locally while keeping the head, body, and surrounding content source-matched."
        )
        if user_face_geometry:
            facial_locality = (
                facial_locality[:-1]
                + ", except the facial contour, feature balance and small, natural head correction a user-directed final state sets."
            )
    if style_footprint and resolved:
        terms = [term for skill in EDITOR_SKILLS for term in skill.final_state_terms(analysis)]
        final_state_labels = ", ".join(terms[:-1]) + ", or " + terms[-1]
        if "pose" in style_options:
            portrait_style_priority = (
                "- Resolved subject final states remain mandatory local endpoints inside the style transformation, but they "
                "must not preserve, restore, or substitute for the selected pose. Apply each specified "
                f"{final_state_labels} instruction within the visibly changed pose."
            )
        else:
            portrait_style_priority = (
                "- Resolved subject final states remain mandatory inside the style transformation; "
                "style may coordinate surrounding pose and light but must not replace a specified "
                f"{final_state_labels} instruction."
            )
    else:
        portrait_style_priority = ""

    if "pose" in style_options and body_reshape_active:
        task_intro = (
            "You are performing an image-to-image same-scene reshoot transformation, not a conservative retouch. "
            "Keep the same subject identity, scene, clothing, props, and photographic realism, but visibly change "
            "the subject's pose according to the selected pose override. Apply the holistic body instruction "
            "while preserving the same people, scene, and identity."
        )
    elif "pose" in style_options:
        task_intro = (
            "You are performing an image-to-image same-scene reshoot transformation, not a conservative retouch. "
            "Keep the same subject identity, scene, clothing, props, and photographic realism, but visibly change "
            "the subject's pose according to the selected pose override."
        )
    elif body_reshape_active:
        task_intro = (
            "You are editing the supplied photograph with photographic realism, anatomically coherent local geometry, and faithful texture. "
            "Apply the holistic body instruction while preserving the same people, scene, and identity."
        )
    elif user_face_geometry:
        task_intro = FACE_GEOMETRY_TASK_INTRO
    else:
        task_intro = (
            "You are editing the supplied image, not creating a new image.\n"
            "Act like a high-end human retoucher: restrained, photographic, detail-aware, and realistic."
        )
    finish_text = (
        "Finish only with believable light, color, contrast, and texture quality. Do not omit the approved body instruction."
        if body_reshape_active else finish
    )
    global_preservation = (
        "- Preserve the same identities, person count, scene identity, and photographic realism. Apply Body Reshape as one continuous overall-figure adjustment; the existing garment may follow the resulting geometry locally while retaining its identity and coverage. Keep every unlisted person and unrelated scene geometry unchanged. Keep face identity and facial anatomy unchanged except for separately approved smile, gaze, blink, or hair instructions, which remain active in their own contracts."
        if body_reshape_active else
        "- Always preserve identities, "
        + ("underlying anatomy outside a user-directed final state, object form" if user_face_geometry else "underlying anatomy or object form")
        + ", clothing, location, scene identity, and natural material texture. Do not transform or add people/objects, or erase them outside an approved local removal; authorized framing may place existing content outside the output frame."
    )
    # Rules about faces and people are left out of a photo without people.
    if not has_person:
        facial_identity_line = ""
    elif user_face_geometry:
        facial_identity_line = (
            "\n- Preserve each person's anatomical facial proportions and profile identity, except what that person's user-directed final state sets. Keep head yaw/pitch fixed unless a resolved eyes-plus-head/face gaze endpoint, a user-directed final state, or selected style changes it; a user-directed final state sets that person's eye–nose–mouth geometry."
        )
    else:
        facial_identity_line = (
            "\n- Preserve each person's anatomical facial proportions and profile identity. Keep head yaw/pitch and projected eye–nose–mouth geometry fixed unless a resolved eyes-plus-head/face gaze endpoint or selected style explicitly changes viewpoint; then change only the specified orientation and its natural projection, never the underlying anatomy."
        )
    locality_lines = f"\n{facial_locality}\n{portrait_style_priority}" if has_person else ""
    texture_text = "natural skin and material texture" if has_person else "natural material texture"
    pose_completion_guard = (
        "\n- The result is incomplete if the selected subject keeps essentially the original body pose and only the "
        "expression, gaze, lighting, or color changes, or if the new pose carries a joint bent outside natural range, "
        "a duplicated, detached, or mismatched limb, or a hand without five correctly jointed fingers."
        if "pose" in style_options else ""
    )
    # Each block carries the blank lines it needs: both slots sit flush against
    # their neighbours in the template below, so a no-pose prompt keeps the
    # original layout with style and user direction after Finish.
    if "pose" in style_options:
        style_before_states_block = (
            f"\nUser direction:\n{user_text}{user_priority_text}"
            f"\n\nMandatory style overrides:\n{style_text}{pose_execution_order}\n"
        )
        style_after_finish_block = ""
    else:
        style_before_states_block = ""
        style_after_finish_block = (
            f"\n\nMandatory style overrides:\n{style_text}"
            f"\n\nUser direction:\n{user_text}{user_priority_text}"
        )

    global_lines = (
        f"{global_preservation}{facial_identity_line}\n"
        f"- Preserve these unselected properties from the original: {spatial_preserve_text}.\n"
        f"{style_scope_text}{locality_lines}\n"
        f"- Preserve {texture_text}. Do not add text, logos, watermarks, people, props, or a different background.\n"
        "- Every required final state above is mandatory. Do not restore a captured property that an authorized feature needs to change."
    )
    if user_face_geometry and not body_reshape_active and "pose" not in style_options:
        prompt = _face_first_prompt(
            analysis, resolved, skill_ctx, claimed,
            task_intro=task_intro, crop_block=compact_crop_block, other_corrections=other_corrections,
            visual_target_block=visual_target_block, user_preserved_block=user_preserved_block,
            finish_text=finish_text, style_block=compact_style_block, global_lines=global_lines,
            user_text=user_text, revision_text=revision_text,
        )
    else:
        prompt = f"""{task_intro}

Required framing:
{compact_crop_block}{removal_face_block}

{visual_target_block}{pose_target_boundary}

{user_preserved_block}

Other approved corrections:
{correction_block}
{style_before_states_block}{subject_section}

{color_block}

{skin_block}

Finish:
{finish_text}{style_after_finish_block}

Global preservation:
{global_preservation}{facial_identity_line}
- Preserve these unselected properties from the original: {spatial_preserve_text}.
{style_scope_text}{locality_lines}
- Preserve {texture_text}. Do not add text, logos, watermarks, people, props, or a different background.
- Every required final state above is mandatory. Do not restore a captured property that an authorized feature needs to change.{face_geometry_guard(resolved)}{pose_completion_guard}{revision_text}
"""
    metadata = {
        "strategy": "resolved_compact",
        "feature_contracts": contracts,
        "resolved_features": resolved,
        "removed_preservations": [
            {"subject": item["subject"], "properties": item["removed_preservations"]}
            for item in resolved if item["removed_preservations"]
        ],
        "unresolved_conflicts": [
            {"subject": item["subject"], "conflicts": item["conflicts"]}
            for item in resolved if item["conflicts"]
        ],
        "color_contract": {
            **{
                key: value
                for skill in EDITOR_SKILLS
                for key, value in skill.color_metadata(claimed[skill.name]).items()
            },
            "memory_color_strategy": analysis.get("memory_color_strategy") or "",
        },
        "visual_target": {
            "edit_intent": analysis.get("edit_intent") or "",
            "lighting_strategy": analysis.get("lighting_strategy") or "",
            "memory_color_strategy": analysis.get("memory_color_strategy") or "",
        },
        "user_preserved_choices": list(analysis.get("rejected_preserve") or []),
        "crop_instruction_source": crop_instruction_source,
        "raw_crop_instruction": raw_crop_block,
        "compact_crop_instruction": compact_crop_block,
        "compact_crop_guidance": compact_crop_guidance,
        "framing_change_active": framing_change_active,
        "alignment_change_active": alignment_change_active,
        "style_instruction_source": style_instruction_source,
        "raw_style_instruction": raw_style_block,
        "compact_style_instruction": compact_style_block,
        "style_change_footprint": sorted(style_footprint),
    }
    for skill in EDITOR_SKILLS:
        metadata.update(skill.prompt_metadata(analysis))
    prompt = prompt.strip()
    for skill in EDITOR_SKILLS:
        prompt = skill.finalize_prompt(prompt, analysis, claimed[skill.name], skill_ctx)
    return prompt, metadata
