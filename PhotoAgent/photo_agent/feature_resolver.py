"""Resolve interacting portrait features before rendering an editor prompt.

The resolver works on properties and final states, not prompt strings.  This
keeps one feature's captured-state preservation from silently cancelling a
different, authorized feature on the same face.
"""

from __future__ import annotations

import json
import math
import re

# The legacy prompt's own readers, deliberately shared rather than reimplemented
# here: both builders must agree on which people a feature is in scope for,
# including the per-person opt-out (_disabled_issue_subject_indices).
from prompts import (
    _disabled_issue_subject_indices,
    _has_person,
    _read_smile_states,
    build_reviewed_light_color_target,
    build_user_preserved_choices,
    resolve_user_blink_request,
    resolve_user_gaze_request,
    resolve_user_hair_request,
    body_reshape_items,
    body_reshape_execution_plan,
    _body_reshape_parse_key_point,
    _body_reshape_endpoint_identity,
    _body_reshape_overlap_identity,
    _body_reshape_measurement_identity,
    _body_reshape_slider_config,
    resolve_smile_targets,
    smile_level,
    subject_issue_items,
)

_BASE_PRESERVE = {
    "identity", "underlying_facial_anatomy", "head_pose", "body_pose",
    "skin_texture", "hair", "clothing", "scene",
}
_EYE_IDENTITY_PRESERVE = {
    "eye_landmark_geometry", "iris_identity", "eyelash_identity",
    "eyelid_crease_identity", "eye_makeup", "glasses_and_reflections",
}
_HAIR_IDENTITY_PRESERVE = {
    "hair_parting", "hair_length", "hair_volume", "hair_density",
    "hairline", "curl_pattern", "hair_color", "haircut_silhouette",
}
_HAIR_ACTION_TEXT = {
    "lay_back": "Lay only the named loose strands back into the adjacent existing hair flow, following their source roots, length, direction, and texture.",
    "remove": "Remove only the named isolated strands along their thin footprints, continuing the already-visible skin, facial feature, or background cleanly underneath with matching texture and grain.",
    "settle": "Settle only the named disturbed area into the still-air silhouette defined by undisturbed hair on the same head, retaining individual strand separation and natural unevenness.",
    "redistribute": "Hair edit — redistribute (required, across the whole named fringe): First, reduce oily hard reflections to the surrounding hair's natural sheen and restore visible strand detail. Second, separate every named thick/stuck cord into finer natural locks and spread those same strands across the adjacent abnormal wide gaps. Finish with a translucent, naturally irregular fringe with no thick-cord-next-to-wide-gap pattern; changing shine alone is not completion.",
}

_SKIN_TERMS = (
    "膚色", "皮膚", "臉色", "臉部", "面部", "face tone", "skin", "complexion",
)
_SKIN_COLOR_TERMS = (
    "色", "暖", "冷", "黃", "紅", "綠", "灰", "洋紅", "偏色", "色溫",
    "tone", "warm", "cool", "yellow", "orange", "red", "green", "gray",
    "grey", "magenta", "cast", "hue", "chroma", "saturation",
)
_FACE_TONAL_AXES = {"exposure", "shadow", "highlight", "contrast"}
_HAIR_PLAN_TERMS = (
    "髮", "头发", "hair", "bang", "fringe", "flyaway", "frizz",
)


def _axis_set(value):
    """Flatten the analysis's per-correction axis lists into canonical tags."""
    flattened = set()
    for item in value or []:
        if isinstance(item, (list, tuple, set)):
            flattened.update(str(axis) for axis in item)
        elif item:
            flattened.add(str(item))
    return flattened


def _skin_identity_lines(analysis):
    lines = []
    for subject in analysis.get("subjects") or []:
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        label = str(subject.get("label") or "").strip()
        skin = str(subject.get("skin") or "").strip()
        if label and skin and skin not in {"不適用", "N/A"}:
            lines.append(f"- {label}: {skin}")
    return lines


def _is_skin_correction(text, axes):
    lowered = re.sub(
        r"非皮膚|非膚色|non[ -]skin|non[ -]complexion",
        "", text.lower(),
    )
    return "skin" in axes or (
        any(term in lowered for term in _SKIN_TERMS)
        and any(term in lowered for term in _SKIN_COLOR_TERMS)
    )


def _is_face_tonal_correction(text, axes):
    return bool(_FACE_TONAL_AXES & set(axes)) and bool(
        re.search(r"(?:臉部|面部|臉頰|人臉|\bface\b|\bfacial\b)", text, re.IGNORECASE)
    )


def _is_hair_correction(text, axes):
    lowered = str(text or "").lower()
    return any("hair" in str(axis).lower() for axis in axes) or any(
        term in lowered for term in _HAIR_PLAN_TERMS
    )


def _repair_strength(value, default="moderate"):
    """Collapse model prose back to the small strength vocabulary."""
    text = str(value or "").strip().lower()
    for level in ("subtle", "moderate", "strong"):
        if text == level or text.startswith(level + " ") or text.startswith(level + ","):
            return level
    return default


def _build_color_blocks(analysis, skin_corrections, face_tonal_corrections=(), lighting_style=None):
    """Render one concise execution contract beneath the photo-specific target."""
    has_person = _has_person(analysis)
    if lighting_style:
        lighting_label = {"gold": "Gold Tone", "soft": "Soft Glow"}.get(lighting_style, lighting_style)
        scene_line = f"- Apply the selected {lighting_label} atmosphere coherently across the scene."
    else:
        scene_line = "- Let scene exposure and palette change wherever the approved plan calls for it."
    execution_lines = [
        "Light and color execution:",
        "- Realize the finished target through the approved corrections as one coherent photographic grade. Make every named adjustment clearly visible with natural tonal transitions and believable materials.",
        scene_line,
    ]

    if not has_person:
        return "\n".join(execution_lines), ""

    identities = _skin_identity_lines(analysis)
    identity_text = "\n".join(identities) if identities else "- Read each person's existing depth and undertone from the supplied photograph."
    correction_text = "\n".join(
        f"- Approved skin correction: {item} Apply it as the named cast correction and stop at the recorded native complexion."
        for item in skin_corrections
    )
    face_tonal_text = "\n".join(
        f"- Approved local face tonal correction: {item} Apply it across the full named region, including clothing when specified; brighten skin through exposure while retaining facial geometry and complexion."
        for item in face_tonal_corrections
    )
    execution_lines.extend([
        "Skin boundary:",
        identity_text,
    ])
    if correction_text:
        execution_lines.append(correction_text)
    if face_tonal_text:
        execution_lines.append(face_tonal_text)
    execution_lines.extend([
        "- Keep each person's native complexion depth, undertone, and chroma while skin follows the final shared exposure and actual illumination.",
        "- Apply scene white balance, hue, and saturation to the non-skin palette independently from complexion.",
        "- Keep tone continuous across face, neck, ears, and visible hands, with natural texture and modelling.",
    ])
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


def _compact_raw_crop_instruction(raw_instruction, *, has_body_reshape=False, crop_plan=None):
    """Keep the raw builder's crop decision while removing explanatory prose."""
    raw = str(raw_instruction or "").strip()
    if not raw:
        return ""

    local_shape_exception = (
        " Preserve tilt, relative placement, and every unlisted proportion; this whole-image rule does not freeze "
        "the mandatory local measurements named by approved body-reshape endpoints."
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
        aspect_match = re.search(r"Target output aspect ratio:\s*([^\.\n]+)", raw)
        tightness_match = re.search(r"Roughly how tight:\s*([^\.\n]+)", raw)
        aspect = aspect_match.group(1).strip() if aspect_match else "the stated output ratio"
        tightness = tightness_match.group(1).strip() if tightness_match else "use the stated crop tightness"
        same_shape = "same shape as the photograph" in raw and "nothing has to be given up" in raw
        near_full_frame = "close to the full frame" in tightness.lower()
        visible_trim = _crop_plan_has_visible_trim(crop_plan)
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
                " Trim side content as needed rather than narrowing people or objects; approved body endpoints "
                "remain the only deliberate local geometry exception."
                if has_body_reshape else
                " Trim side content as needed rather than narrowing people or objects."
            )
        elif "top and bottom edges cannot both be inside" in raw:
            consequence = (
                " Trim top or bottom content as needed rather than flattening people or objects; approved body "
                "endpoints remain the only deliberate local geometry exception."
                if has_body_reshape else
                " Trim top or bottom content as needed rather than flattening people or objects."
            )
        closer = " Come in visibly closer than the original." if "Closer framing was requested" in raw else ""
        result = (
            f"Reframe to {aspect}; {tightness}.{consequence}{closer} Frame the visible main subject and meaningful "
            "context comfortably inside. Crop and scale uniformly. "
            + (
                "Preserve tilt, relative placement, and unlisted proportions while completing approved local body endpoints."
                if has_body_reshape else
                "Preserve internal proportions, tilt, relative sizes, spacing, and overlap."
            )
        )
        return result + local_shape_exception if has_body_reshape else result

    # A new raw-builder shape should remain correct before this compactor is
    # taught its semantics; verbosity is safer than silently dropping a rule.
    return raw


def _compact_crop_goal_text(raw_goal, resolved_ratio):
    """Keep crop semantics while normalizing stale ratios and dropping box coordinates."""
    text = str(raw_goal or "").strip()
    if not text:
        return ""

    # Geometry validation may move or grow the proposed box. Keep the named
    # edge/content intent, but do not forward obsolete numeric trim amounts.
    text = re.sub(
        r"(?i)(?:by\s+)?(?:approximately|about|roughly|around|約|大約)?\s*"
        r"\d+(?:\.\d+)?\s*%\s*(?:of\s+the\s+|的)?",
        "",
        text,
    )

    ratio = str(resolved_ratio or "").strip()
    if ratio and ratio != "original":
        spoken_ratios = (
            "一比一", "三比二", "二比三", "三比四", "四比三", "四比五", "五比四",
            "九比十六", "十六比九", "二十一比九", "一比四", "四比一", "一比八", "八比一",
        )
        text = re.sub(r"(?<!\d)\d+\s*:\s*\d+(?!\d)", ratio, text)
        for spoken_ratio in spoken_ratios:
            text = text.replace(spoken_ratio, ratio)

    clauses = re.split(r"(?<=[，；。;])", text)
    semantic_clauses = []
    for clause in clauses:
        coordinate_count = sum(
            bool(re.search(rf"(?i)(?<![a-z]){name}\s*=", clause))
            for name in ("x", "y", "w", "h")
        )
        if coordinate_count >= 2:
            continue
        semantic_clauses.append(clause)
    return "".join(semantic_clauses).strip(" ，；;")


def _compact_analysis_crop_guidance(analysis):
    """Render the approved semantic crop intent without stale geometry."""
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
    if goal:
        lines.append(f"- Framing goal: {goal}")
    # The reviewed correction is authoritative. Structured fields fill only
    # details it did not already state, keeping one crop plan in the prompt.
    if trimmed_edges and not has_approved_goal:
        lines.append(f"- Set the final frame by trimming the {', '.join(trimmed_edges)} edges.")
    if distractions and not has_approved_goal:
        lines.append(f"- Place outside the final frame: {'; '.join(distractions)}.")
    if anchor:
        lines.append(f"- Main visual anchor: {anchor}.")
    if keep:
        lines.append(f"- Keep comfortably inside the final frame: {'; '.join(keep)}.")
    return "\n".join(lines)


def _analysis_has_alignment_correction(analysis):
    """Return whether an approved crop-axis item changes tilt or perspective."""
    alignment_terms = (
        "rotate", "rotation", "straighten", "level the horizon",
        "horizon correction", "perspective correction", "keystone",
        "旋轉", "水平校正", "修正水平", "校正地平", "地平線校正", "透視校正",
        "垂直校正", "拉直", "扶正",
    )
    axes = analysis.get("correction_axes") or []
    for index, raw_item in enumerate(analysis.get("correction_plan") or []):
        item_axes = _axis_set([axes[index]]) if index < len(axes) else set()
        item = str(raw_item or "").strip().lower()
        if "crop" in item_axes and any(term in item for term in alignment_terms):
            return True
    return False


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


def _style_change_footprint(style_options):
    """Properties that selected styles are authorized to replace."""
    options = {
        key: value for key, value in (style_options or {}).items()
        if value and value != "default"
    }
    footprint = set()
    if "pose" in options:
        footprint.update({
            "body pose", "head pose", "gesture", "body language", "limb placement",
            "subject silhouette", "within-subject spacing", "within-subject overlap",
        })
    if "angle" in options:
        footprint.update({"camera viewpoint", "scene perspective", "projected overlap"})
    if "lighting" in options:
        footprint.update({"lighting logic", "light/color atmosphere", "highlight and shadow rendering"})
    if "size" in options:
        footprint.update({"framing", "subject scale", "visible extent", "image boundaries"})
    return options, footprint


def _box(item):
    value = item.get("box") or {}
    return value if isinstance(value, dict) else {}


def _valid_contract_subject_index(value, subject_count=None):
    """Validate the stable parent ``subjects[]`` index.

    ``local_detection_index`` is deliberately not consulted: it is a detector
    slot, not a cross-feature identity.  An upper bound is checked whenever the
    originating analysis supplies one.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return False
    if subject_count is None:
        return True
    if isinstance(subject_count, bool) or not isinstance(subject_count, int) or subject_count < 0:
        return False
    return value < subject_count


def _contract_subject_index(item):
    """Read the canonical subject id, accepting the legacy private spelling."""
    if not isinstance(item, dict):
        return None
    if "subject_index" in item and "_subject_index" in item:
        # Both names are present in a few saved/legacy contracts.  They are
        # aliases, not two independent matching hints: disagreement means the
        # parent relation cannot be trusted and must remain unresolved.
        public = item.get("subject_index")
        private = item.get("_subject_index")
        # ``True == 1`` in Python, but bool is never a valid subject id.  Do
        # not let that equality make a contradictory legacy pair appear
        # consistent.
        if (
            isinstance(public, bool) or isinstance(private, bool)
            or not isinstance(public, int) or not isinstance(private, int)
            or public != private
        ):
            return None
    if "subject_index" in item:
        return item.get("subject_index")
    return item.get("_subject_index")


def _contract_priority(item):
    """Resolve the small priority vocabulary used by explicit user endpoints."""
    value = item.get("priority", item.get("source_priority", 0))
    if item.get("user_override") or str(item.get("source") or "").lower() in {
        "user", "user_instruction", "explicit_user",
    }:
        return 100
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return {"user": 100, "explicit_user": 100, "planner": 10, "analysis": 10}.get(str(value).lower(), 0)


def _body_endpoint_record(value, source_item, source_index=0, entry_value=None):
    """Normalize one body-adjustment value for resolver-level merging.

    The normal request path already coalesces body points in
    ``build_body_reshape_execution_plan``.  This extra normalization is for
    legacy/imported analyses and for callers that construct more than one
    body contract for a subject: distinct local regions must survive that
    merge, while two instructions for the same local measurement must be
    deduplicated or reported as a conflict.
    """
    metadata = entry_value if isinstance(entry_value, dict) else {}
    text = (
        metadata.get("canonical_text") or metadata.get("endpoint") or metadata.get("raw_text")
        if metadata else value
    )
    text = str(text or "").strip()
    parsed = _body_reshape_parse_key_point(text)
    identity = _body_reshape_endpoint_identity(parsed)
    metadata_identity = None
    if metadata:
        region = metadata.get("region_id") or metadata.get("region")
        measurement = metadata.get("measurement")
        if region and measurement:
            metadata_identity = (
                region,
                metadata.get("segment"),
                metadata.get("screen_side"),
                metadata.get("measurement_id") or _body_reshape_measurement_identity(measurement),
            )
    # An invalid legacy point is not made executable by this resolver.  A
    # request-plan entry may carry the parser's structured metadata even when
    # its display text is an older/longer alias; trust that metadata only when
    # it explicitly says the point is executable and has a complete, bounded
    # operation/target.  Free-form text alone never becomes an endpoint here.
    metadata_target = metadata.get("target_pct") if metadata else None
    try:
        metadata_target = float(metadata_target)
    except (TypeError, ValueError):
        metadata_target = None
    metadata_valid = bool(
        metadata_identity
        and (identity is None or identity == metadata_identity)
        and metadata.get("executable") is not False
        and metadata.get("policy_valid") is True
        and metadata.get("operation") in {"increase", "decrease"}
        and metadata_target is not None
        and math.isfinite(metadata_target)
        and 0 < metadata_target < 100
        and metadata_target <= _body_reshape_slider_config()["max"]
    )
    valid = bool(parsed.get("syntax_valid") and parsed.get("policy_valid")) or metadata_valid
    if identity is None and metadata_valid:
        identity = metadata_identity
    canonical = parsed.get("canonical_text") or text
    signature = (
        identity,
        parsed.get("operation") or metadata.get("operation"),
        parsed.get("target_pct") if parsed.get("target_pct") is not None else metadata.get("target_pct"),
    )
    return {
        "text": canonical,
        "identity": identity or ("raw", canonical),
        "signature": signature,
        "entry": metadata or None,
        "source": source_item,
        "source_index": source_index,
        # A request-plan entry carries the endpoint's provenance even though
        # its parent body contract is shared by all points.  Let that
        # provenance retain the explicit-user precedence when this resolver
        # is called directly with multiple imported body contracts.
        "priority": max(
            _contract_priority(source_item),
            _contract_priority(metadata) if metadata else 0,
        ),
        "valid": valid,
        "reason_code": None if valid else (parsed.get("reason_code") or "invalid_syntax_or_semantics"),
        "reason": None if valid else (parsed.get("reason") or "invalid body endpoint"),
        "overlap_identity": _body_reshape_overlap_identity(parsed),
    }


def _merge_body_endpoint_values(items):
    """Merge body points by local endpoint identity, not by whole-list tuple.

    Returns ``(values, entries, sources, conflicts)``.  An explicit user
    contract can replace a lower-priority planner amount/direction; ties with
    incompatible values are surfaced as ``needs_review``.  A conflict for one
    waist/arm/etc. endpoint does not discard unrelated body regions from the
    same person.
    """
    records = []
    for item in items:
        endpoint = item.get("endpoint") or {}
        values = endpoint.get("body_adjustments")
        if values is None:
            continue
        if isinstance(values, (str, bytes, dict)):
            values = [values]
        elif not isinstance(values, (list, tuple)):
            values = [values]
        metadata_values = endpoint.get("body_adjustment_entries")
        if isinstance(metadata_values, dict):
            metadata_values = [metadata_values]
        elif not isinstance(metadata_values, (list, tuple)):
            metadata_values = []
        for index, value in enumerate(values):
            metadata = metadata_values[index] if index < len(metadata_values) else None
            records.append(_body_endpoint_record(value, item, index, metadata))

    grouped = {}
    order = []
    invalid_records = []
    for record in records:
        if not record.get("valid"):
            invalid_records.append(record)
            continue
        identity = record["identity"]
        if identity not in grouped:
            grouped[identity] = []
            order.append(identity)
        grouped[identity].append(record)

    # Broad waist/abdomen widths can be two labels for the same visible
    # contour.  Put them into one merge group so equal requests are
    # deduplicated and incompatible requests become an explicit conflict.
    overlap_grouped = {}
    for identity in order:
        for record in grouped.get(identity, []):
            overlap_id = record.get("overlap_identity")
            if overlap_id is not None:
                overlap_grouped.setdefault((overlap_id,), []).append(record)

    merged_values = []
    merged_entries = []
    sources = []
    conflicts = []
    for record in invalid_records:
        conflicts.append({
            "endpoint": "body_adjustments",
            "measurement": record.get("identity"),
            "values": [record.get("text")],
            "reason": record.get("reason_code") or "invalid_syntax_or_semantics",
        })
    for identity in order:
        candidates = grouped[identity]
        signatures = {}
        for candidate in candidates:
            signatures.setdefault(candidate["signature"], []).append(candidate)
        if len(signatures) > 1:
            top_priority = max(max(candidate["priority"] for candidate in group) for group in signatures.values())
            winners = [
                group[0] for group in signatures.values()
                if max(candidate["priority"] for candidate in group) == top_priority
            ]
            if len(winners) == 1 and any(
                max(candidate["priority"] for candidate in group) < top_priority
                for group in signatures.values()
            ):
                selected = winners[0]
            else:
                conflicts.append({
                    "endpoint": "body_adjustments",
                    "measurement": identity,
                    "values": [candidate["text"] for candidate in candidates],
                })
                continue
        else:
            selected = next(iter(signatures.values()))[0]
        if selected["text"] not in merged_values:
            merged_values.append(selected["text"])
            if selected.get("entry") is not None:
                merged_entries.append(selected["entry"])
            sources.extend(candidate["source"] for candidate in candidates if candidate["source"] not in sources)

    # Apply the cross-region overlap decision to the already selected values.
    # The normal request path has made the same decision in
    # build_body_reshape_execution_plan; this keeps direct/legacy resolver
    # callers consistent as well.  If both broad measurements have the same
    # operation/amount, keep the first and attach the later source index to its
    # metadata.  Otherwise remove both and surface one conflict.
    for records in overlap_grouped.values():
        # Same-region exact duplicates have already been handled above; only
        # a group containing both waist and abdomen identities is a
        # cross-region overlap.
        if len(records) < 2 or len({record["identity"] for record in records}) < 2:
            continue
        signatures = {
            (record["signature"][1], record["signature"][2])
            for record in records
        }
        selected_texts = {record["text"] for record in records}
        if len(signatures) == 1:
            winner = records[0]
            duplicate_indices = [
                record.get("entry", {}).get("key_point_index")
                for record in records[1:]
                if isinstance(record.get("entry"), dict)
                and isinstance(record.get("entry", {}).get("key_point_index"), int)
            ]
            if duplicate_indices and isinstance(winner.get("entry"), dict):
                entry = winner["entry"]
                existing = entry.setdefault(
                    "source_key_point_indices",
                    [entry.get("key_point_index")] if isinstance(entry.get("key_point_index"), int) else [],
                )
                for index in duplicate_indices:
                    if index not in existing:
                        existing.append(index)
                entry.setdefault("duplicate_source_key_point_indices", []).extend(
                    index for index in duplicate_indices
                    if index not in entry["duplicate_source_key_point_indices"]
                )
            for record in records[1:]:
                if record["text"] != winner["text"] and record["text"] in merged_values:
                    merged_values.remove(record["text"])
                if record.get("entry") is not winner.get("entry") and record.get("entry") in merged_entries:
                    merged_entries.remove(record.get("entry"))
        else:
            for record in records:
                if record["text"] in merged_values:
                    merged_values.remove(record["text"])
                if record.get("entry") in merged_entries:
                    merged_entries.remove(record.get("entry"))
            conflicts.append({
                "endpoint": "body_adjustments",
                "measurement": "waist-abdomen-overlap",
                "values": sorted(selected_texts),
                "reason": "overlapping broad waist/abdomen measurements conflict",
            })
    return tuple(merged_values), tuple(merged_entries), sources, conflicts


def _legacy_same_subject(left, right):
    """Keep the pre-body resolver's matching for ordinary feature contracts."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if left.get("subject") == right.get("subject"):
        return True
    a, b = left.get("box") or {}, right.get("box") or {}
    if not all(key in a and key in b for key in ("x", "y", "w", "h")):
        return False
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    return abs(ax - bx) <= max(a["w"], b["w"]) * .35 and abs(ay - by) <= max(a["h"], b["h"]) * .35


def _same_subject(left, right):
    """Match body-aware contracts by stable id, legacy contracts by old rules.

    Body reshape contracts carry the parent ``subjects[]`` index so their
    per-person endpoints cannot be merged by a label or a large bounding box.
    Contracts that do not participate in a body request retain the resolver's
    historical label/box matching and therefore do not change eye, hair, or
    smile behavior.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_explicit = "subject_index" in left or "_subject_index" in left
    right_explicit = "subject_index" in right or "_subject_index" in right
    if not left_explicit and not right_explicit:
        return _legacy_same_subject(left, right)
    if left_explicit and right_explicit:
        left_id = _contract_subject_index(left)
        right_id = _contract_subject_index(right)
        left_count = left.get("_subject_count")
        right_count = right.get("_subject_count")
        return (
            _valid_contract_subject_index(left_id, left_count)
            and _valid_contract_subject_index(right_id, right_count)
            and left_id == right_id
        )
    # A body contract may coexist with a legacy face contract from an older
    # saved analysis.  Only use the old geometric relation for that mixed pair;
    # it never affects a request containing ordinary contracts alone.
    return _legacy_same_subject(left, right)


def _smile_contracts(analysis):
    targets = resolve_smile_targets(analysis)
    body_context = _analysis_has_body_plan(analysis)

    # The legacy/raw prompt has always retained an explicit user-selected
    # level when analysis produced no usable reading.  Keep the compact path
    # equivalent: a missing or malformed analyzer value must not silently
    # turn a mandatory UI choice into "preserve expression".  Explicitly
    # unreadable faces remain excluded because there are no pixels on which
    # to execute a reliable expression edit.
    level = smile_level(analysis)
    if not targets and level not in (None, "auto") and not _read_smile_states(analysis):
        disabled = _disabled_issue_subject_indices(analysis, "smile_issue")
        for index, subject in enumerate(analysis.get("subjects") or []):
            if not isinstance(subject, dict) or not subject.get("is_person") or index in disabled:
                continue
            issue = subject.get("smile_issue") or {}
            raw_state = issue.get("smile_intensity") or issue.get("smile_state")
            normalized_raw = str(raw_state or "").strip().lower().replace("-", "_")
            if normalized_raw in {
                "unclear", "occluded_or_unclear", "uncertain",
                "不清楚", "不清晰", "無法判斷", "无法判断",
            }:
                continue
            target = {
                "label": (subject.get("label") or "").strip() or "畫面中的人",
                "box": subject.get("box") or {},
                "current": None,
                "target": level,
            }
            if body_context:
                target.update(subject_index=index, _subject_index=index)
            targets.append(target)

    contracts = []
    for item in targets:
        contract = {
            "feature": "smile",
            "subject": item.get("label") or "畫面中的人物",
            "box": _box(item),
            "source_state": ({"smile_level": item["current"]} if item["current"] else {}),
            "endpoint": {"smile_level": item["target"]},
            "direct_changes": ["mouth_expression"],
            "dependent_changes": [
                "facial_expression", "cheek_tension", "brow_expression",
                "periocular_expression", "eyelid_aperture",
            ],
            # This mirrors the existing smile block's useful independent
            # protections. gaze_direction is deliberately represented here:
            # the resolver removes it when gaze is also authorized.
            "preserve": sorted(_BASE_PRESERVE | _EYE_IDENTITY_PRESERVE | {"gaze_direction"}),
        }
        if body_context:
            contract.update(
                subject_index=item.get("subject_index", item.get("_subject_index")),
                _subject_count=len(analysis.get("subjects") or []),
            )
        contracts.append(contract)
    return contracts


def _gaze_contracts(analysis, include_gaze):
    options = analysis.get("_advanced_options") or {}
    body_context = _analysis_has_body_plan(analysis)
    if not include_gaze or not options.get("gaze_contact_detection", True):
        return []
    disabled = _disabled_issue_subject_indices(analysis, "gaze_issue")
    contracts = []
    for raw_item in subject_issue_items(analysis, "gaze_issue"):
        item = resolve_user_gaze_request(raw_item)
        # A positive automatic verdict always belongs to the automatic path;
        # forced is reserved for a user opt-in over should_fix=false, same
        # convention as _hair_contracts' hair_user_forced below.
        gaze_user_forced = bool(item.get("_user_forced")) and not bool(item.get("should_fix"))
        if not (item.get("should_fix") or gaze_user_forced) or item.get("_subject_index") in disabled:
            continue
        strategy = item.get("gaze_repair_strategy") or {}
        method = strategy.get("strategy_type") or "eyes_only"
        # A frontal face already has the required head/face alignment. Letting
        # a model-authored plan add a speculative "slight alignment if needed"
        # expands a local gaze correction into a face reprojection and is a
        # common identity failure, especially when hair is edited nearby. Does
        # not apply to a forced request: _FORCED_GAZE_STRATEGY deliberately
        # asks for eyes_plus_head_face_alignment regardless of head_pose,
        # since a forced row's starting gaze/head angle is unknown and may be
        # far off-camera -- downgrading it here would silently zero out
        # head_face_adjustment below and reproduce the exact "won't move"
        # complaint that strategy was changed to fix.
        if method == "eyes_plus_head_face_alignment" and not gaze_user_forced and str(item.get("head_pose") or "").lower() == "frontal":
            method = "eyes_only"
        direct_changes = ["gaze_direction", "iris_direction", "pupil_direction"]
        dependent_changes = ["sclera_balance", "catchlight_consistency"]
        if method == "eyes_plus_head_face_alignment":
            direct_changes.extend(["head_pose", "face_orientation"])
            dependent_changes.append("viewpoint_projected_facial_geometry")
        contract = {
            "feature": "gaze",
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": {
                "gaze": "direct_camera",
                "method": method,
                "strength": _repair_strength(strategy.get("repair_strength")),
                "eye_adjustment": strategy.get("eye_adjustment") or "",
                "head_face_adjustment": (
                    strategy.get("head_face_adjustment") or ""
                    if method == "eyes_plus_head_face_alignment" else ""
                ),
            },
            "direct_changes": direct_changes,
            "dependent_changes": dependent_changes,
            # Existing analysis strategies commonly freeze these during gaze
            # repair. They are compatible for gaze-only, but are removed from
            # preservation when a smile owns the final facial expression.
            "preserve": sorted(_BASE_PRESERVE | _EYE_IDENTITY_PRESERVE | {
                "mouth_expression", "facial_expression", "eyelid_aperture",
            }),
        }
        if body_context:
            contract.update(
                subject_index=item.get("subject_index", item.get("_subject_index")),
                _subject_count=len(analysis.get("subjects") or []),
            )
        contracts.append(contract)
    return contracts


def _blink_contracts(analysis):
    options = analysis.get("_advanced_options") or {}
    body_context = _analysis_has_body_plan(analysis)
    if not options.get("eye_closed_detection", True):
        return []
    disabled = _disabled_issue_subject_indices(analysis, "blink_issue")
    contracts = []
    for raw_item in subject_issue_items(analysis, "blink_issue"):
        item = resolve_user_blink_request(raw_item)
        # A positive automatic verdict always belongs to the automatic path;
        # forced is reserved for a user opt-in over should_fix=false, same
        # convention as _hair_contracts' hair_user_forced below.
        blink_user_forced = bool(item.get("_user_forced")) and not bool(item.get("should_fix"))
        if not (item.get("should_fix") or blink_user_forced) or item.get("_subject_index") in disabled:
            continue
        # eye_state stays the machine-level endpoint the resolver reasons about;
        # the analysis strategy's own wording rides alongside it under keys gaze
        # never uses, so a subject carrying both features cannot collide. Only
        # luna authors a per-person blink strategy, so these stay absent (and
        # the rendered line generic) for every other analysis model -- except
        # a forced row, which always carries one (resolve_user_blink_request).
        strategy = item.get("blink_repair_strategy") or {}
        endpoint = {
            "eye_state": "naturally_open_and_matched_to_expression",
            "blink_user_forced": blink_user_forced,
        }
        for key, source in (
            ("eyelid_target", "target_eye_state"),
            ("eyelid_strength", "repair_strength"),
            ("eyelid_adjustment", "eyelid_adjustment"),
        ):
            value = str(strategy.get(source) or "").strip()
            if value:
                endpoint[key] = _repair_strength(value, default="subtle") if key == "eyelid_strength" else value
        contract = {
            "feature": "blink",
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": endpoint,
            "direct_changes": ["eyelid_aperture"],
            "dependent_changes": ["iris_visibility", "lash_line_position"],
            "preserve": sorted(_BASE_PRESERVE | _EYE_IDENTITY_PRESERVE),
        }
        if body_context:
            contract.update(
                subject_index=item.get("subject_index", item.get("_subject_index")),
                _subject_count=len(analysis.get("subjects") or []),
            )
        contracts.append(contract)
    return contracts


def _hair_contracts(analysis):
    options = analysis.get("_advanced_options") or {}
    body_context = _analysis_has_body_plan(analysis)
    if not options.get("hair_mess_detection", True):
        return []
    # Compatibility for analyses produced before hair approval moved to the
    # per-person face-review toggle. New Luna output never puts hair on these
    # top-level axes, but an imported old run may still express rejection here.
    if (
        "hair" in _axis_set(analysis.get("rejected_axes"))
        and "hair" not in _axis_set(analysis.get("correction_axes"))
    ):
        return []

    disabled = _disabled_issue_subject_indices(analysis, "hair_issue")
    fully_groomed_default = bool(options.get("calm_hair"))
    contracts = []
    for index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict):
            continue
        issue = resolve_user_hair_request(subject.get("hair_issue") or {})
        # Old saved analyses may have marked every enabled hair row as forced.
        # A positive automatic verdict always belongs to the automatic path;
        # forced is reserved for a user opt-in over should_fix=false.
        hair_user_forced = bool(issue.get("_user_forced")) and not bool(issue.get("should_fix"))
        local_fix = (
            isinstance(issue, dict) and (issue.get("should_fix") or hair_user_forced)
            and index not in disabled
        )
        # Automatic findings and explicit per-person opt-ins share the same
        # two levels and preservation contract.
        if not local_fix:
            continue
        user_level = issue.get("_user_level")
        fully_groomed = user_level == "tidy" if user_level in {"dynamic", "tidy"} else fully_groomed_default
        action = issue.get("fix_action") if local_fix else ""
        hair_level = "fully_groomed" if fully_groomed else "local_cleanup"
        correction_plan = str(issue.get("correction_plan") or "").strip() if local_fix else ""
        if local_fix and not fully_groomed and issue.get("hair_state") in {"windblown", "motion"}:
            # Keep the exact per-person target, but resolve the requested level
            # into controlled wind movement rather than either untouched chaos
            # or the still-air endpoint used by fully_groomed.
            hair_level = "preserve_motion"
        direct = ["hair", "hair_arrangement"]
        dependent = ["strand_distribution", "hair_texture_continuity"]
        if (
            action == "redistribute"
            and issue.get("hair_state") == "bangs_clumped"
            and issue.get("front_hair") == "clumped"
        ):
            direct.extend(["fringe_distribution", "hair_sheen"])
        contract = {
            "feature": "hair",
            "subject": subject.get("label") or "畫面中的人物",
            "box": _box(subject),
            "endpoint": {
                "hair_level": hair_level,
                "hair_action": action or "",
                "hair_user_forced": hair_user_forced,
                "hair_state": issue.get("hair_state") or "",
                "front_hair": issue.get("front_hair") or "",
                "hair_correction_plan": correction_plan,
                "hair_visible_evidence": [
                    str(value).strip()
                    for value in (issue.get("visible_evidence") or [])
                    if str(value).strip()
                ],
            },
            "direct_changes": direct,
            "dependent_changes": dependent,
            "preserve": sorted(
                (_BASE_PRESERVE - {"hair"}) | _HAIR_IDENTITY_PRESERVE
            ),
        }
        if body_context:
            contract.update(subject_index=index, _subject_count=len(analysis.get("subjects") or []))
        contracts.append(contract)
    return contracts


def _body_reshape_contracts(analysis):
    """Turn body-planning key points into bounded per-person contracts."""
    contracts = []
    for item in body_reshape_items(analysis):
        key_points = tuple(item.get("key_points") or [])
        direct = ["named_body_contours", "named_body_proportions"]
        contracts.append({
            "feature": "body_reshape",
            "subject_index": item.get("subject_index"),
            "_subject_count": len(analysis.get("subjects") or []),
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": {
                "body_adjustments": key_points,
                "body_adjustment_entries": tuple(item.get("key_point_entries") or ()),
            },
            "direct_changes": direct,
            "dependent_changes": [
                "body_edge_continuity",
                # These names deliberately match otherwise requested preserve
                # tokens where applicable. resolve_feature_contracts subtracts
                # the union footprint after smile/gaze/hair contracts merge, so
                # their generic clothing preservation cannot re-freeze the
                # approved person's local garment follow-through.
                "clothing",
                "garment_boundaries",
                "exposed_skin_boundaries",
                "local_garment_contour_and_surface_geometry",
                "source_texture_and_pattern_continuity",
                "necessary_adjacent_background_continuation",
            ],
            "preserve": sorted((_BASE_PRESERVE - {"clothing"}) | {
                "face", "expression", "hands", "feet", "unlisted_body_regions",
                "clothing_design", "garment_coverage_relationship",
                "garment_length", "garment_construction", "garment_identity",
                "garment_wearing_method", "body_clothing_topology",
                "background_geometry", "camera_viewpoint",
            }),
        })
    return contracts


def _hair_action_instruction(state):
    action = state.get("hair_action") or ""
    if action == "redistribute" and not (
        state.get("hair_state") == "bangs_clumped"
        and state.get("front_hair") == "clumped"
    ):
        return (
            "Redistribute only the specifically named strands within their existing local region to the approved target; "
            "do not reinterpret them as a fringe or extend the operation to adjacent hair."
        )
    return _HAIR_ACTION_TEXT.get(
        action, "Perform only the approved hair correction inside the analyzed region."
    )


def _safe_gaze_eye_adjustment(value):
    """Keep Call C detail without forwarding language that invites eye redraw."""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"\b(?:reconstruct|rebuild|replace|regenerate|redraw)\w*\b", text, re.IGNORECASE):
        return (
            "reposition the existing irises and pupils inside the unchanged eye openings, "
            "preserving eye shape, canthi, lid folds, lashes, makeup, and iris identity"
        )
    return text


def _analysis_has_body_plan(analysis):
    # ``apply_default_body_reshape_plans`` gives every person an empty
    # ``body_reshape`` object.  That placeholder must not switch ordinary
    # eye/hair/smile requests onto the body-aware resolver; only a request
    # with at least one executable endpoint needs the stable body identity
    # metadata and merge rules.
    if not isinstance(analysis, dict):
        return False
    try:
        return bool(body_reshape_execution_plan(analysis).get("executable_point_count"))
    except Exception:
        # Keep the legacy feature path available if an imported/partially
        # formed analysis cannot be inspected as a body plan.
        return False


def _safe_gaze_head_face_adjustment(value, *, allow_pose_change=False):
    """Drop stale Call C prose that would freeze a selected pose.

    Older analyses may already contain planner-authored sentences such as
    "Keep the torso, shoulders, seated pose, and camera framing unchanged."
    Structured preservation is resolved elsewhere, so forwarding that prose
    during a pose override would reintroduce a conflict the resolver removed.
    """
    text = str(value or "").strip()
    if not text or not allow_pose_change:
        return text

    pose_terms = re.compile(
        r"\b(?:torso|shoulders?|body[ -]?pose|seated[ -]?pose|stance|gesture|"
        r"interaction points?|camera framing)\b",
        re.IGNORECASE,
    )
    preserve_terms = re.compile(
        r"\b(?:keep|keeping|preserve|preserving|retain|retaining|leave|leaving|"
        r"hold|holding|maintain|maintaining|freeze|freezing|restore|restoring|"
        r"do not (?:change|move|alter)|without (?:changing|moving|altering))\b",
        re.IGNORECASE,
    )
    kept = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if pose_terms.search(sentence) and preserve_terms.search(sentence):
            # Keep a real head/face action that precedes a trailing
            # "while keeping the body..." clause; discard only that clause.
            sentence = re.sub(
                r"(?:,\s*|\s+)(?:while\s+)?(?:keeping|preserving|retaining|leaving|"
                r"without (?:changing|moving|altering))\b[^.!?;]*"
                r"(?:torso|shoulders?|body[ -]?pose|seated[ -]?pose|stance|gesture|"
                r"interaction points?|camera framing)[^.!?;]*[.!?;]?",
                "",
                sentence,
                flags=re.IGNORECASE,
            ).strip()
            if pose_terms.search(sentence) and preserve_terms.search(sentence):
                continue
        if sentence:
            kept.append(sentence)
    return " ".join(kept).strip()


def build_feature_contracts(analysis, *, include_gaze=True):
    """Return active, editor-stage feature contracts from analysis/options."""
    contracts = (
        _smile_contracts(analysis)
        + _gaze_contracts(analysis, include_gaze)
        + _blink_contracts(analysis)
        + _hair_contracts(analysis)
        + _body_reshape_contracts(analysis)
    )
    if _analysis_has_body_plan(analysis):
        subjects = analysis.get("subjects") or []
        for contract in contracts:
            index = _contract_subject_index(contract)
            if _valid_contract_subject_index(index, len(subjects)):
                subject = subjects[index]
                if isinstance(subject, dict):
                    contract["canonical_subject"] = {
                        "label": subject.get("label") or "畫面中的人物",
                        "box": subject.get("box") or {},
                    }
    return contracts


def _resolve_legacy_feature_contracts(contracts):
    """Resolve an ordinary (non-body) request exactly as before body support."""
    groups = []
    for contract in contracts:
        group = next((candidate for candidate in groups if _legacy_same_subject(candidate["contracts"][0], contract)), None)
        if group is None:
            group = {"contracts": []}
            groups.append(group)
        group["contracts"].append(contract)

    resolved = []
    for group in groups:
        items = group["contracts"]
        direct = {value for item in items for value in item["direct_changes"]}
        dependent = {value for item in items for value in item["dependent_changes"]}
        footprint = direct | dependent
        requested_preserve = {value for item in items for value in item["preserve"]}
        preserve = requested_preserve - footprint
        endpoints = {}
        source_states = {}
        conflicts = []
        for item in items:
            for key, value in (item.get("source_state") or {}).items():
                source_states.setdefault(key, value)
            for key, value in item["endpoint"].items():
                if key in endpoints and endpoints[key] != value:
                    conflicts.append(f"{key}: {endpoints[key]} vs {value}")
                endpoints[key] = value

        if endpoints.get("smile_level") and endpoints.get("gaze"):
            endpoints["eyelids"] = "naturally_shaped_by_the_final_expression"
            endpoints["gaze_within_expression"] = "align_irises_and_pupils_inside_that_expression"
        if endpoints.get("eye_state") and endpoints.get("smile_level"):
            endpoints["eye_state"] = "naturally_open_while_remaining_coherent_with_the_final_expression"
        if endpoints.get("eye_state") and endpoints.get("gaze"):
            endpoints["coordinated_eye_edit"] = "render_eyelid_openness_and_final_gaze_together"

        resolved.append({
            "subject": items[0]["subject"],
            "box": items[0].get("box") or {},
            "features": [item["feature"] for item in items],
            "source_state": source_states,
            "final_state": endpoints,
            "direct_changes": sorted(direct),
            "dependent_changes": sorted(dependent),
            "change_footprint": sorted(footprint),
            "preserve": sorted(preserve),
            "removed_preservations": sorted(requested_preserve & footprint),
            "conflicts": conflicts,
        })
    return resolved


def resolve_feature_contracts(contracts, subject_count=None):
    """Merge contracts without guessing identity and without last-write wins.

    Contracts carrying the same valid parent ``subject_index`` are merged. A
    missing/invalid id gets its own unresolved group even when labels and boxes
    happen to match. Endpoint values are deduplicated when identical; a
    conflicting key is removed from the executable final state and marked for
    review rather than being overwritten by whichever contract happened to be
    iterated last.
    """
    contracts = list(contracts or [])
    # No body contract and no explicit stable ids means this is an ordinary
    # eye/hair/smile request. Keep its historical resolver output and
    # conflict semantics byte-for-byte compatible with the baseline commit.
    if not any(
        item.get("feature") == "body_reshape"
        or "subject_index" in item
        or "_subject_index" in item
        for item in contracts if isinstance(item, dict)
    ):
        return _resolve_legacy_feature_contracts(contracts)

    groups = []
    for contract in contracts or []:
        if not isinstance(contract, dict):
            continue
        item = dict(contract)
        item["subject_index"] = _contract_subject_index(item)
        count = item.get("_subject_count", subject_count)
        if count is not None:
            item["_subject_count"] = count
        group = next((candidate for candidate in groups if _same_subject(candidate["contracts"][0], item)), None)
        if group is None:
            group = {"contracts": []}
            groups.append(group)
        group["contracts"].append(item)

    resolved = []
    for group in groups:
        items = group["contracts"]
        valid_ids = [
            _contract_subject_index(item) for item in items
            if _valid_contract_subject_index(_contract_subject_index(item), item.get("_subject_count", subject_count))
        ]
        canonical_subject_index = valid_ids[0] if valid_ids and all(value == valid_ids[0] for value in valid_ids) else None
        unresolved_identity = canonical_subject_index is None
        # A contract generated from the current analysis carries the canonical
        # parent data. Prefer that explicit parent snapshot over a feature's
        # display label/box (which may be stale or model-authored).  Do not let
        # the first feature in iteration order decide which person's label is
        # shown when its final state came from another contract.
        canonical_item = next(
            (
                item for item in items
                if canonical_subject_index is not None
                and _contract_subject_index(item) == canonical_subject_index
                and isinstance(item.get("canonical_subject"), dict)
                and (
                    item.get("canonical_subject", {}).get("label")
                    or item.get("canonical_subject", {}).get("box")
                )
            ),
            next(
                (
                    item for item in items
                    if canonical_subject_index is not None
                    and _contract_subject_index(item) == canonical_subject_index
                ),
                items[0],
            ),
        )
        canonical_data = canonical_item.get("canonical_subject") or {}
        canonical_label = canonical_data.get("label") or canonical_item.get("subject") or "畫面中的人物"
        canonical_box = canonical_data.get("box") or canonical_item.get("box") or {}

        source_states = {}
        endpoint_values = {}
        endpoint_sources = {}
        endpoint_candidates = {}
        body_items = []
        for item in items:
            for key, value in (item.get("source_state") or {}).items():
                source_states.setdefault(key, value)
            for key, value in (item.get("endpoint") or {}).items():
                if item.get("feature") == "body_reshape" and key == "body_adjustments":
                    body_items.append(item)
                    continue
                # body_adjustment_entries carries provenance for the shared
                # body list, not a second independent feature endpoint.  It is
                # rebuilt alongside body_adjustments below so it cannot make
                # two otherwise distinct body regions look like a tuple-wide
                # conflict.
                if item.get("feature") == "body_reshape" and key == "body_adjustment_entries":
                    continue
                endpoint_candidates.setdefault(key, []).append((value, item))

        conflicts = []
        overridden_endpoints = []
        if body_items:
            body_values, body_entries, body_sources, body_conflicts = _merge_body_endpoint_values(body_items)
            if body_values:
                endpoint_values["body_adjustments"] = body_values
                endpoint_sources["body_adjustments"] = body_sources
                if body_entries:
                    endpoint_values["body_adjustment_entries"] = body_entries
                    endpoint_sources["body_adjustment_entries"] = body_sources
            conflicts.extend(body_conflicts)
        for key, candidates in endpoint_candidates.items():
            unique = []
            for value, item in candidates:
                existing = next((entry for entry in unique if entry[0] == value), None)
                if existing is None:
                    unique.append((value, [_contract_priority(item)], [item]))
                else:
                    existing[1].append(_contract_priority(item))
                    existing[2].append(item)
            if len(unique) == 1:
                endpoint_values[key] = unique[0][0]
                endpoint_sources[key] = unique[0][2]
                continue
            top_priority = max(max(entry[1]) for entry in unique)
            top = [entry for entry in unique if max(entry[1]) == top_priority]
            if len(top) == 1 and any(max(entry[1]) < top_priority for entry in unique):
                endpoint_values[key] = top[0][0]
                endpoint_sources[key] = top[0][2]
                overridden_endpoints.append({
                    "endpoint": key,
                    "winner": top[0][0],
                    "priority": top_priority,
                })
                continue
            conflicts.append({"endpoint": key, "values": [entry[0] for entry in unique]})
        if unresolved_identity:
            conflicts.append({
                "endpoint": "subject_index",
                "values": ["missing_or_invalid"],
                "reason": "subject_unresolved",
            })
            endpoint_values = {}

        # Features whose only endpoint was rejected by a conflict are not
        # active. This prevents body conflict metadata from opening a body
        # preservation exception or triggering a quality promotion.
        active_items = []
        if not unresolved_identity:
            for item in items:
                endpoint_keys = set((item.get("endpoint") or {}).keys())
                if endpoint_keys and endpoint_keys & set(endpoint_values):
                    active_items.append(item)
        direct = {value for item in active_items for value in item.get("direct_changes", [])}
        dependent = {value for item in active_items for value in item.get("dependent_changes", [])}
        footprint = direct | dependent
        requested_preserve = {value for item in active_items for value in item.get("preserve", [])}
        preserve = requested_preserve - footprint

        if endpoint_values.get("smile_level") and endpoint_values.get("gaze"):
            endpoint_values["eyelids"] = "naturally_shaped_by_the_final_expression"
            endpoint_values["gaze_within_expression"] = "align_irises_and_pupils_inside_that_expression"
        if endpoint_values.get("eye_state") and endpoint_values.get("smile_level"):
            endpoint_values["eye_state"] = "naturally_open_while_remaining_coherent_with_the_final_expression"
        if endpoint_values.get("eye_state") and endpoint_values.get("gaze"):
            endpoint_values["coordinated_eye_edit"] = "render_eyelid_openness_and_final_gaze_together"

        feature_names = []
        for item in active_items:
            if item.get("feature") not in feature_names:
                feature_names.append(item.get("feature"))
        resolved.append({
            "subject_index": canonical_subject_index,
            "subject": canonical_label,
            "box": canonical_box,
            "features": feature_names,
            "source_state": source_states,
            "final_state": endpoint_values,
            "direct_changes": sorted(direct),
            "dependent_changes": sorted(dependent),
            "change_footprint": sorted(footprint),
            "preserve": sorted(preserve),
            "removed_preservations": sorted(requested_preserve & footprint),
            "conflicts": conflicts,
            "needs_review": bool(conflicts),
            "subject_unresolved": unresolved_identity,
            "overridden_endpoints": overridden_endpoints,
        })
    return resolved


def build_compact_editor_prompt(
    analysis, preset_prompt, user_direction, crop_plan, *, preset_key="natural", include_gaze=True,
    revision_feedback=None, raw_crop_instruction=None, raw_style_instruction=None,
):
    """Render one compact execution document from resolved final states."""
    contracts = build_feature_contracts(analysis, include_gaze=include_gaze)
    resolved = resolve_feature_contracts(
        contracts, subject_count=len(analysis.get("subjects") or [])
    )
    body_plan = body_reshape_execution_plan(analysis)
    body_reshape_active = bool(body_plan.get("executable_point_count")) and any(
        "body_adjustments" in (item.get("final_state") or {})
        for item in resolved
    )
    for item in resolved:
        if "lighting" in item["preserve"]:
            item["preserve"].remove("lighting")
            item["removed_preservations"] = sorted(set(item["removed_preservations"]) | {"lighting"})
    style_options, style_footprint = _style_change_footprint(analysis.get("style_options"))
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
    if body_reshape_active:
        body_style_footprint = set()
        if "angle" in style_options:
            body_style_footprint.update({"camera_viewpoint", "scene_perspective", "projected_overlap"})
        if "size" in style_options:
            body_style_footprint.update({"framing", "subject_scale", "visible_extent", "image_boundaries"})
        for item in resolved:
            removed = set(item["preserve"]) & body_style_footprint
            item["preserve"] = sorted(set(item["preserve"]) - body_style_footprint)
            item["removed_preservations"] = sorted(set(item["removed_preservations"]) | removed)
    ratio = analysis.get("recommended_aspect_ratio") or (analysis.get("crop_box") or {}).get("aspect_ratio") or "original"
    raw_crop_block = str(raw_crop_instruction or "").strip()
    compact_crop_block = _compact_raw_crop_instruction(
        raw_crop_block, has_body_reshape=body_reshape_active, crop_plan=crop_plan,
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

    size_override = style_options.get("size")
    size_reframe_active = size_override in {"close", "closer", "wide", "wider"}
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
    compact_crop_guidance = ""
    if framing_change_active and not size_reframe_active:
        compact_crop_guidance = _compact_analysis_crop_guidance(analysis)
        if compact_crop_guidance:
            compact_crop_block += "\n" + compact_crop_guidance

    subject_blocks = []
    resolved_features = {feature for item in resolved for feature in item["features"]}
    has_smile = "smile" in resolved_features
    has_laugh = any(item["final_state"].get("smile_level") == "laughing" for item in resolved)
    has_gaze = "gaze" in resolved_features
    has_facial_edit = bool(resolved_features & {"smile", "gaze", "blink"})
    has_joint_smile_gaze = any(
        {"smile", "gaze"}.issubset(set(item["features"])) for item in resolved
    )
    has_head_aligned_gaze = any(
        item["final_state"].get("method") == "eyes_plus_head_face_alignment"
        for item in resolved
    )
    correction_axes = analysis.get("correction_axes") or []
    for item in resolved:
        state_lines = []
        state = item["final_state"]
        source_state = item.get("source_state") or {}
        smile = state.get("smile_level")
        if smile:
            smile_text = {
                "neutral": "a neutral, non-smiling expression",
                "gentle": "a small, clearly readable natural smile",
                "broad": "a clearly broad, joyful smile",
                "laughing": "an unmistakably happy, intense and natural expression of genuine laughter",
            }[smile]
            replacement_guard = (
                "not a generic replacement or merely a broad posed smile"
                if smile == "laughing" else "not a generic replacement"
            )
            current_smile = source_state.get("smile_level")
            if current_smile:
                state_lines.append(
                    f"- Transform this person's existing {current_smile} expression into {smile_text} on the existing face—{replacement_guard}."
                )
            else:
                state_lines.append(f"- Render {smile_text} on the existing face—{replacement_guard}.")
        if state.get("eye_state"):
            # Strategy fields are empty unless the analysis authored a per-person
            # blink plan (luna only), so every other model keeps the generic
            # sentence this block has always emitted.
            eyelid_strength = str(state.get("eyelid_strength") or "").strip()
            eyelid_target = str(state.get("eyelid_target") or "").strip()
            eyelid_adjustment = str(state.get("eyelid_adjustment") or "").strip()
            if smile:
                # The smile endpoint owns the final expression, so the planned
                # target eyelid state -- written against the captured
                # expression -- is dropped here; its method still applies.
                eyelid_text = "Shape the eyelids for the final expression first, then reopen the affected eye only enough to be naturally readable within that expression; retain natural smile narrowing rather than restoring captured openness"
            elif state.get("blink_user_forced"):
                # Forced means the analyzer itself found nothing to fix here --
                # unlike the branch below, "accidentally closed/half-closed"
                # would misdescribe the eye this row is actually about.
                opening = f"Apply a {eyelid_strength} eyelid correction: open" if eyelid_strength else "Open"
                eyelid_text = f"{opening} this person's eyes naturally into a relaxed, alert expression, matching their anatomy"
                if eyelid_target:
                    eyelid_text += f". Target eyelid state: {eyelid_target}"
            else:
                opening = f"Apply a {eyelid_strength} eyelid correction: open" if eyelid_strength else "Open"
                eyelid_text = f"{opening} or relax the accidentally closed/half-closed eye naturally, matching the person's anatomy"
                if eyelid_target:
                    eyelid_text += f". Target eyelid state: {eyelid_target}"
            if eyelid_adjustment:
                eyelid_text += f". Eyelid method: {eyelid_adjustment}"
            if not eyelid_text.endswith("."):
                eyelid_text += "."
            state_lines.append(f"- {eyelid_text}")
        if state.get("gaze"):
            gaze_method = state.get("method") or "eyes_only"
            gaze_strength = state.get("strength") or "moderate"
            eye_adjustment = _safe_gaze_eye_adjustment(state.get("eye_adjustment"))
            head_face_adjustment = _safe_gaze_head_face_adjustment(
                state.get("head_face_adjustment"),
                allow_pose_change="pose" in style_options,
            )
            if gaze_method == "eyes_only":
                gaze_method_text = (
                    f"Use a {gaze_strength} eyes-only correction and keep the original head/face angle fixed"
                )
            else:
                bounded_head_change = head_face_adjustment or "only the minimum head/face alignment needed"
                gaze_method_text = (
                    f"Use a {gaze_strength} eyes-plus-head/face correction, limited to {bounded_head_change}"
                )
            if eye_adjustment:
                gaze_method_text += f"; iris/pupil movement: {eye_adjustment}"
            state_lines.append(f"- {gaze_method_text}; reach clear direct lens contact.")
        if state.get("coordinated_eye_edit"):
            state_lines.append(
                "- Render the final eyelid openness, iris/pupil direction, sclera balance, and catchlights as one coordinated local eye edit; do not reopen the eye to one gaze and then redirect it."
            )
        if (smile or state.get("eye_state")) and not state.get("gaze"):
            state_lines.append("- Keep the captured iris/pupil direction, sclera balance, and catchlights.")
        body_adjustments = state.get("body_adjustments") or []
        if body_adjustments:
            source_indices = []
            for entry in state.get("body_adjustment_entries") or ():
                candidates = entry.get("source_key_point_indices") if isinstance(entry, dict) else None
                if not isinstance(candidates, (list, tuple)):
                    candidates = [entry.get("key_point_index")] if isinstance(entry, dict) else []
                for index in candidates:
                    if isinstance(index, int) and not isinstance(index, bool) and index not in source_indices:
                        source_indices.append(index)
            provenance = f" (source key-point indices: {source_indices})" if source_indices else ""
            state_lines.append(
                "- Body reshape source" + provenance + ":\nBody reshape:"
            )
            state_lines.extend(f"- {point}" for point in body_adjustments)
            state_lines.append(
                "- Treat every listed point as a mandatory geometric endpoint that must be clearly recognizable at normal viewing size. Natural means anatomically believable and texture-faithful, not subtle, restrained, barely visible, or minimum-change. Body reshape itself never authorizes a posture, action, gesture, joint-angle, or body-direction change; a separately selected pose style remains authoritative for its own pose transformation."
            )
        hair_level = state.get("hair_level")
        hair_correction_plan = str(state.get("hair_correction_plan") or "").strip()
        hair_action = state.get("hair_action")
        redistribute = (
            hair_action == "redistribute"
            and state.get("hair_state") == "bangs_clumped"
            and state.get("front_hair") == "clumped"
        )
        hair_user_forced = bool(state.get("hair_user_forced"))
        if hair_level == "local_cleanup" and not redistribute and not hair_user_forced:
            state_lines.append(
                "- Hair level — local cleanup: complete the approved defect correction inside its named footprint while preserving the rest of the captured hairstyle and movement. This preservation boundary does not permit leaving the named defect unchanged."
            )
        elif hair_level == "fully_groomed":
            state_lines.append(
                "- Hair level — fully groomed (mandatory visible change): fully comb and organize this person's entire visible hairstyle into a clean, deliberate, portrait-ready arrangement—not merely a local cleanup. Settle every loose, lifted, crossed, uneven, windblown, or directionless strand into coherent sections following the existing haircut; neaten the fringe, part, surface flow, and outer silhouette. The result must look clearly tidier at normal viewing size; leaving the hair unchanged or making only a barely perceptible adjustment fails. Keep natural strand separation and texture rather than forming a smooth solid mass."
            )
        if redistribute:
            evidence = "；".join(state.get("hair_visible_evidence") or [])
            target = hair_correction_plan if hair_user_forced else (evidence or hair_correction_plan)
            if target and target != "不適用":
                state_lines.append(f"- Hair target — existing fringe: {target}")
        elif hair_correction_plan and hair_correction_plan != "不適用":
            if hair_level == "preserve_motion":
                state_lines.append(
                    "- Analysis-specific hair target (use only to locate the affected regions; "
                    f"the preserve-motion endpoint below controls the final arrangement): {hair_correction_plan}"
                )
            else:
                state_lines.append(
                    f"- Hair correction plan (target areas; its execution extent is governed by the selected hair level above): {hair_correction_plan}"
                )
        if hair_level == "preserve_motion":
            # Keep this after the analyzer-authored plan: the plan identifies
            # where the disorder is, while this user-selected level is the
            # authoritative endpoint and must win if that plan says "settle".
            state_lines.append(
                "- Hair level — preserve motion (authoritative final state): keep the clearly visible wind direction and lifted, flowing volume, but express that motion through a small number of clean, coherent locks and broad streaming arcs—not scattered flyaways, loops, tangles, or crisscrossing strands. Sweep displaced windblown hair away from the eyes, nose, mouth, and central face so the expression and facial contours are clearly readable; preserve only intentional fringe or face-framing hair that belongs to the existing haircut. Clean the outer silhouette and merge chaotic strands into the dominant wind flow while retaining natural strand separation. The result must remain unmistakably windblown, but controlled and portrait-ready rather than messy; do not settle the hair into a still-air shape."
            )
        if hair_action and hair_level != "preserve_motion":
            if not (hair_user_forced and hair_level == "local_cleanup"):
                state_lines.append(f"- {_hair_action_instruction(state)}")
            if redistribute:
                state_lines.append(
                    "- Preserve the source fringe type, main part and sweep, length, outer edge, hairline, and total hair mass. Keep subtype-defining openings, but not the defective clump layout or accidental gaps; do not add hair or form an opaque sheet."
                )
        # Eye-identity properties are rendered above in concrete visual terms;
        # repeating their internal contract names here adds tokens but gives the
        # image editor less useful guidance.
        display_preserve = set(item["preserve"]) - _EYE_IDENTITY_PRESERVE - _HAIR_IDENTITY_PRESERVE
        preserve = ", ".join(value.replace("_", " ") for value in sorted(display_preserve))
        subject_blocks.append(
            f"Subject: {item['subject']}\n"
            f"Approximate location: {json.dumps(item['box'], ensure_ascii=False)}\n"
            + "\n".join(state_lines)
            + f"\n- Preserve: {preserve}."
        )

    feature_terms = ("眼神", "虹膜", "瞳孔", "direct_camera", "gaze", "閉眼", "眼皮", "blink", "笑", "smile", "laugh")
    other_corrections = []
    skin_corrections = []
    face_tonal_corrections = []
    for index, raw_item in enumerate(analysis.get("correction_plan") or []):
        item = str(raw_item)
        axes = _axis_set([correction_axes[index]]) if index < len(correction_axes) else set()
        if any(term.lower() in item.lower() for term in feature_terms):
            continue
        # Required framing already carries the authoritative compacted raw crop
        # decision. Repeating an analysis crop item here can turn a same-shape,
        # near-full-frame instruction into a second generative reframe request.
        if "crop" in axes:
            continue
        if _is_hair_correction(item, axes):
            # Hair has one authoritative route: the matching person's
            # hair_issue. Call A occasionally violates its schema and emits a
            # free-text hair item with tags such as hair_edge; never let that
            # unscoped copy become an Other approved correction.
            continue
        elif _is_face_tonal_correction(item, axes) and not (
            {"skin", "white_balance", "hue", "saturation", "tint"} & axes
        ):
            face_tonal_corrections.append(item)
        elif _is_skin_correction(item, axes):
            skin_corrections.append(item)
        else:
            other_corrections.append(item)
    correction_block = "\n".join(f"- {item}" for item in other_corrections) or "- No additional approved local corrections."
    color_block, skin_block = _build_color_blocks(
        analysis, skin_corrections, face_tonal_corrections,
        lighting_style=style_options.get("lighting"),
    )
    visual_target_block = build_reviewed_light_color_target(
        analysis, lighting_style=style_options.get("lighting")
    )
    user_preserved_block = build_user_preserved_choices(analysis)
    subjects_block = "\n\n".join(subject_blocks) or "No resolved facial or full-groom transformation is active."
    shared_face_rules = []
    if has_facial_edit:
        projection_exception = (
            " An authorized eyes-plus-head/face gaze endpoint may update viewpoint projection only through its specified bounded head orientation; anatomical proportions remain fixed."
            if has_head_aligned_gaze else ""
        )
        shared_face_rules.append(
            "- Fixed identity geometry: retain the source-matched head/face width-to-length ratio, skeletal jaw/chin contour, cheekbone placement, baseline eye–nose–mouth landmark layout, eye-globe size and interocular/canthus positions, nose bridge/tip/nostril geometry, philtrum length, ear shape, and natural asymmetry. Preserve each eye's original upper-eyelid crease topology—including monolid versus double-lid structure, crease count, height, depth, and continuity—and never create a new fold. Preserve lashes/brows, makeup, and all landmarks outside an explicitly authorized local movement."
            + projection_exception
        )
    if has_smile:
        shared_face_rules.append(
            "- For every smile target, limit expression movement to mouth-corner position and opening, cheek soft-tissue lift, brow/periocular tension, and eyelid aperture over the fixed landmarks above. Coordinate them as one expression, using the captured mouth center, lip contour/volume, natural asymmetry, and visible dental characteristics rather than regenerating or beautifying the whole face."
        )
        shared_face_rules.append(
            "- Retain the original skin age and fold pattern. Existing crow's-feet, under-eye creases, and nasolabial folds may compress minimally but must not become deeper, darker, sharper, longer, or more numerous."
        )
    if has_laugh:
        shared_face_rules.append(
            "- For laughter, show spontaneous joy coherently through the mouth, cheek lift, periocular response, and a compatible relaxed brow; do not leave a neutral, worried, stern, or angry upper face. Reach the requested intensity through coordinated expression—not an oversized mouth or reshaped facial features."
        )
    if has_joint_smile_gaze:
        shared_face_rules.append(
            "- Where smile and gaze affect the same person, gaze changes iris/pupil direction only. Let laughter narrow the eyelids modestly through natural cheek lift while retaining canthi, the exact original upper-eyelid crease topology, recognizable eye openness, and iris visibility; avoid both fixed-open staring and exaggerated squinting."
        )
    if has_gaze:
        shared_face_rules.append(
            "- For gaze edits, retain iris size/color/texture and pupil proportions; change only necessary sclera balance and catchlight placement, preserving catchlight count, shape, brightness, and light-source logic."
        )
    if has_facial_edit:
        shared_face_rules.append(
            "- Carry the original lash line/lashes, brow design, eyeliner, eyeshadow, mascara, false lashes, glasses, and reflections through authorized movement without redesigning, enhancing, removing, or replacing them."
        )
    shared_face_block = (
        "\nShared facial execution rules:\n" + "\n".join(shared_face_rules)
        if shared_face_rules else ""
    )
    hair_boundary_block = (
        "\nHair execution boundary:\n"
        "- Treat hair work as an arrangement/local-sheen edit, not a head or face regeneration. Keep the source haircut, parting, length, volume, density, hairline, curl pattern, and color. Where hair crosses a facial feature, retain that feature's visible source shape and position and repair only the strand-width footprint; any authorized eye or expression changes follow only their own resolved final states above."
        if "hair" in resolved_features else ""
    )
    body_boundary_block = (
        "\nBody reshape targets:\n"
        "- Treat body work as local geometry, not a new identity, weight-loss claim, fitness transformation, or unselected pose change. Every approved endpoint is mandatory and must visibly change the named local measurement in the stated direction. A no-op, near-no-op, lighting-only change, smoothing, sharpening, or fold-only change fails the endpoint. A separately selected pose style remains authoritative for its own pose transformation.\n"
        "- Interpret a percentage against the named region's visible 2D local measurement in the input. It is the total dimensional change, not the amount per side and not a percentage of the person, bounding box, canvas, weight, circumference, or hidden depth. Do not stack duplicate percentages on one measurement; join adjacent waist/abdomen endpoints as one continuous contour.\n"
        "- If the user's explicit direction gives a different amount or prohibits this same body-region change, that explicit instruction overrides the planner's default 12% endpoint; preserve the stated amount exactly and do not silently clamp it.\n"
        "- GARMENT IDENTITY AND COVERAGE CONTRACT: preserve the same garment, design, color, material, pattern, construction, openings, layering, front/back order, coverage relationship, and wearing method. Inside an approved region, allow its existing garment silhouette, fabric, texture, pattern, seams, folds, and local clothing/skin boundary to move continuously with the body geometry. Do not freeze those authorized local pixels or edges in their source position or shape.\n"
        "- This authorized local garment follow-through overrides generic clothing or internal-proportion preservation elsewhere for the approved region only. Those rules still protect garment identity/coverage and every unlisted region.\n"
        "- Any generic instruction elsewhere to preserve clothing means preserve garment identity and coverage outside the approved local geometry. It does not freeze garment silhouette, seams, folds, texture, or the local clothing/skin boundary inside the approved region. Leaving the garment silhouette unchanged is not valid preservation when it prevents the mandatory endpoint.\n"
        "- Never lengthen/lower a top to cover abdomen, raise a waistband to cover waist, add/remove fabric, alter neckline or sleeve design, change which body area is clothed/exposed, or rearrange coverage to fake shaping. Do not use shadows, blur, sharpening, or redrawn folds instead of geometry.\n"
        "- When an approved outer contour moves, continue only the necessary narrow strip of the same background between old and new contours. Preserve unlisted body regions, face identity and facial anatomy (except separately approved smile, gaze, blink, or hair endpoints), skin texture, hair identity, hands, feet, accessories, nearby people/objects, background structure, camera viewpoint, and perspective; never broaden the narrow continuation into scene reconstruction."
        if body_reshape_active else ""
    )
    user_text = user_direction.strip() or "No additional direction."
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
        "Then render the resolved local smile, eye, gaze, hair, and body-detail endpoints inside that new pose. "
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
    if style_footprint:
        style_scope_text = "- Selected style options may change only: " + ", ".join(sorted(style_footprint)) + "."
    elif alignment_change_active and composition_reframe_active:
        style_scope_text = "- Required framing owns the final frame edges, frame-relative subject scale, and visible extent; approved alignment owns tilt and perspective. Preserve the source pose and viewpoint."
    elif alignment_only_active:
        style_scope_text = "- Approved alignment owns tilt, perspective, and the minimal edge trim it requires; preserve the source subject scale, pose, and viewpoint."
    elif framing_change_active:
        style_scope_text = "- Required framing owns the final frame edges, frame-relative subject scale, and visible extent; preserve the source pose and viewpoint."
    else:
        style_scope_text = "- Preserve the source pose, viewpoint, subject scale, visible extent, and framing."
    if "pose" in style_options:
        gaze_endpoint_text = (
            "plus any explicitly resolved gaze or body-reshape endpoint"
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
            "bounded head/face alignment. Apply each approved body endpoint visibly inside its named body region, "
            "including only its necessary garment and adjacent-background follow-through; keep unlisted regions fixed."
        )
    elif has_head_aligned_gaze:
        facial_locality = (
            "- Apply facial edits locally. Only the resolved eyes-plus-head/face gaze endpoint may make its specified bounded head/face alignment; "
            "keep the body and surrounding content fixed."
        )
    elif body_reshape_active:
        facial_locality = (
            "- Apply facial edits locally. Apply each approved body endpoint visibly inside its named body region, "
            "including only its necessary garment and adjacent-background follow-through; do not move the head or unlisted regions."
        )
    else:
        facial_locality = (
            "- Apply facial edits locally within the required final frame; keep the visible head, body, and scene content source-matched."
            if framing_change_active else
            "- Apply facial edits locally while keeping the head, body, and surrounding content source-matched."
        )
    if style_footprint and resolved:
        # Keep the enabled wording compatible with the existing compact
        # prompt; only omit the body endpoint when the feature is inactive.
        final_state_labels = (
            "smile, eye state, gaze, hair, or body-reshape"
            if body_reshape_active else
            "smile, eye state, gaze, or hair"
        )
        if "pose" in style_options:
            portrait_style_priority = (
                "- Resolved subject final states remain mandatory local endpoints inside the style transformation, but they "
                "must not preserve, restore, or substitute for the selected pose. Apply each specified "
                f"{final_state_labels} endpoint within the visibly changed pose."
            )
        else:
            portrait_style_priority = (
                "- Resolved subject final states remain mandatory inside the style transformation; "
                "style may coordinate surrounding pose and light but must not replace a specified "
                f"{final_state_labels} endpoint."
            )
    else:
        portrait_style_priority = ""

    if "pose" in style_options and body_reshape_active:
        task_intro = (
            "You are performing an image-to-image same-scene reshoot transformation, not a conservative retouch. "
            "Keep the same subject identity, scene, clothing, props, and photographic realism, but visibly change "
            "the subject's pose according to the selected pose override. Complete every approved body endpoint "
            "at its specified magnitude while preserving the same people, scene, and identity."
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
            "Complete every approved body endpoint at its specified magnitude while preserving the same people, scene, and identity."
        )
    else:
        task_intro = (
            "You are editing the supplied image, not creating a new image.\n"
            "Act like a high-end human retoucher: restrained, photographic, detail-aware, and realistic."
        )
    finish_text = (
        "Finish only with believable light, color, contrast, and texture quality. Do not reduce or omit an approved local body endpoint."
        if body_reshape_active else finish
    )
    global_preservation = (
        "- Preserve the same identities, person count, scene identity, and photographic realism. Approved body endpoints may change only the named local geometry; the existing garment may follow that geometry locally under its garment contract. Keep every unlisted person and body region unchanged by body reshape. Keep face identity and facial anatomy unchanged except for separately approved smile, gaze, blink, or hair endpoints, which remain active in their own contracts."
        if body_reshape_active else
        "- Always preserve identities, underlying anatomy or object form, clothing, location, scene identity, and natural material texture. Do not transform, add, or erase people/objects; authorized framing may place existing content outside the output frame."
    )
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
            f"\nUser direction:\n{user_text}"
            f"\n\nMandatory style overrides:\n{style_text}{pose_execution_order}\n"
        )
        style_after_finish_block = ""
    else:
        style_before_states_block = ""
        style_after_finish_block = (
            f"\n\nMandatory style overrides:\n{style_text}"
            f"\n\nUser direction:\n{user_text}"
        )

    prompt = f"""{task_intro}

Required framing:
{compact_crop_block}

{visual_target_block}{pose_target_boundary}

{user_preserved_block}

Other approved corrections:
{correction_block}
{style_before_states_block}
Resolved subject final states:
Locations identify subjects only; they are not target positions.
{subjects_block}
{shared_face_block}{hair_boundary_block}{body_boundary_block}

{color_block}

{skin_block}

Finish:
{finish_text}{style_after_finish_block}

Global preservation:
{global_preservation}
- Preserve each person's anatomical facial proportions and profile identity. Keep head yaw/pitch and projected eye–nose–mouth geometry fixed unless a resolved eyes-plus-head/face gaze endpoint or selected style explicitly changes viewpoint; then change only the specified orientation and its natural projection, never the underlying anatomy.
- Preserve these unselected properties from the original: {spatial_preserve_text}.
{style_scope_text}
{facial_locality}
{portrait_style_priority}
- Preserve natural skin and material texture. Do not add text, logos, watermarks, people, props, or a different background.
- Every required final state above is mandatory. Do not restore a captured property that an authorized feature needs to change.{pose_completion_guard}{revision_text}
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
            "skin_corrections": skin_corrections,
            "face_tonal_corrections": face_tonal_corrections,
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
    if body_plan.get("review_point_count"):
        metadata["body_reshape_execution_plan"] = body_plan
    if body_reshape_active:
        metadata["body_reshape_targets"] = list(body_plan.get("items") or [])
    return prompt.strip(), metadata
