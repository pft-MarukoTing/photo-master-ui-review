"""Resolve interacting portrait features before rendering an editor prompt.

The resolver works on properties and final states, not prompt strings.  This
keeps one feature's captured-state preservation from silently cancelling a
different, authorized feature on the same face.
"""

from __future__ import annotations

import json
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
    "eye_makeup", "glasses_and_reflections",
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


def _compact_raw_crop_instruction(raw_instruction):
    """Keep the raw builder's crop decision while removing explanatory prose."""
    raw = str(raw_instruction or "").strip()
    if not raw:
        return ""

    if raw.startswith("No crop."):
        return (
            "Keep the supplied image boundaries as the final frame; do not crop, reframe, expand, or pad the canvas. "
            "Scale uniformly only—never stretch or squash—and preserve all internal proportions, tilt, relative sizes, and spacing."
        )

    if raw.startswith("Wider framing requested."):
        strength = ""
        match = re.search(r"subject's visible frame ratio about (\d+)% smaller", raw)
        if match:
            strength = f"; make the main subject about {match.group(1)}% smaller in the frame"
        return (
            "Create the requested wider same-scene framing"
            f"{strength}. Show more plausible continuation of the existing environment while preserving subject identity, "
            "relative geometry, perspective, materials, and coherence with the final lighting. Use uniform scaling and same-scene outpainting only; "
            "never stretch edges, add blank bands, or introduce unrelated scenery."
        )

    if raw.startswith("Reframing:"):
        aspect_match = re.search(r"Target output aspect ratio:\s*([^\.\n]+)", raw)
        tightness_match = re.search(r"Roughly how tight:\s*([^\.\n]+)", raw)
        aspect = aspect_match.group(1).strip() if aspect_match else "the stated output ratio"
        tightness = tightness_match.group(1).strip() if tightness_match else "use the stated crop tightness"
        same_shape = "same shape as the photograph" in raw and "nothing has to be given up" in raw
        near_full_frame = "close to the full frame" in tightness.lower()
        if same_shape and near_full_frame and "Closer framing was requested" not in raw:
            return (
                "Keep the supplied framing and subject scale; at most trim a small amount from the outer edges if needed. "
                "Do not zoom out, outpaint, or recompose. Scale uniformly only—never stretch content—and preserve internal "
                "proportions, tilt, relative sizes, spacing, and overlap."
            )
        consequence = ""
        if "left and right edges cannot both be inside" in raw:
            consequence = " Trim side content as needed rather than narrowing people or objects."
        elif "top and bottom edges cannot both be inside" in raw:
            consequence = " Trim top or bottom content as needed rather than flattening people or objects."
        closer = " Come in visibly closer than the original." if "Closer framing was requested" in raw else ""
        return (
            f"Reframe to {aspect}; {tightness}.{consequence}{closer} Choose frame edges from the visible main subject, "
            "keeping faces, hands, held objects, and meaningful context comfortably inside. Crop and scale uniformly only; "
            "never stretch content or alter internal proportions, tilt, relative sizes, spacing, or overlap."
        )

    # A new raw-builder shape should remain correct before this compactor is
    # taught its semantics; verbosity is safer than silently dropping a rule.
    return raw


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
            "or weight shift; color, lighting, or expression changes alone do not satisfy it."
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
        footprint.update({"body pose", "head pose", "gesture", "body language"})
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


def _same_subject(left, right):
    if left["subject"] == right["subject"]:
        return True
    a, b = left.get("box") or {}, right.get("box") or {}
    if not all(key in a and key in b for key in ("x", "y", "w", "h")):
        return False
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    return abs(ax - bx) <= max(a["w"], b["w"]) * .35 and abs(ay - by) <= max(a["h"], b["h"]) * .35


def _smile_contracts(analysis):
    targets = resolve_smile_targets(analysis)

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
            targets.append({
                "label": (subject.get("label") or "").strip() or "畫面中的人",
                "box": subject.get("box") or {},
                "current": None,
                "target": level,
            })

    contracts = []
    for item in targets:
        contracts.append({
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
        })
    return contracts


def _gaze_contracts(analysis, include_gaze):
    options = analysis.get("_advanced_options") or {}
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
        contracts.append({
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
        })
    return contracts


def _blink_contracts(analysis):
    options = analysis.get("_advanced_options") or {}
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
        contracts.append({
            "feature": "blink",
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": endpoint,
            "direct_changes": ["eyelid_aperture"],
            "dependent_changes": ["iris_visibility", "lash_line_position"],
            "preserve": sorted(_BASE_PRESERVE | _EYE_IDENTITY_PRESERVE),
        })
    return contracts


def _hair_contracts(analysis):
    options = analysis.get("_advanced_options") or {}
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
        contracts.append({
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
        })
    return contracts


def _body_reshape_contracts(analysis):
    """Turn body-planning key points into bounded per-person contracts."""
    contracts = []
    posture_terms = ("姿勢", "站姿", "肩線", "挺直", "重心")
    for item in body_reshape_items(analysis):
        key_points = tuple(item.get("key_points") or [])
        direct = ["named_body_contours", "named_body_proportions"]
        if any(term in point for point in key_points for term in posture_terms):
            direct.append("body_pose")
        contracts.append({
            "feature": "body_reshape",
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": {"body_adjustments": key_points},
            "direct_changes": direct,
            "dependent_changes": [
                "body_edge_continuity",
                "source_texture_transport_inside_existing_clothing_or_skin_region",
            ],
            "preserve": sorted(_BASE_PRESERVE | {
                "face", "expression", "hands", "feet", "unlisted_body_regions",
                "clothing", "clothing_design", "garment_coverage",
                "garment_boundaries", "garment_length", "garment_construction",
                "exposed_skin_boundaries", "body_clothing_topology",
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


def build_feature_contracts(analysis, *, include_gaze=True):
    """Return active, editor-stage feature contracts from analysis/options."""
    return (
        _smile_contracts(analysis)
        + _gaze_contracts(analysis, include_gaze)
        + _blink_contracts(analysis)
        + _hair_contracts(analysis)
        + _body_reshape_contracts(analysis)
    )


def resolve_feature_contracts(contracts):
    """Merge contracts per subject and remove preservation inside the union footprint."""
    groups = []
    for contract in contracts:
        group = next((candidate for candidate in groups if _same_subject(candidate["contracts"][0], contract)), None)
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


def build_compact_editor_prompt(
    analysis, preset_prompt, user_direction, crop_plan, *, preset_key="natural", include_gaze=True,
    revision_feedback=None, raw_crop_instruction=None, raw_style_instruction=None,
):
    """Render one compact execution document from resolved final states."""
    contracts = build_feature_contracts(analysis, include_gaze=include_gaze)
    resolved = resolve_feature_contracts(contracts)
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
    ratio = analysis.get("recommended_aspect_ratio") or (analysis.get("crop_box") or {}).get("aspect_ratio") or "original"
    crop_text = json.dumps(crop_plan or {}, ensure_ascii=False)
    raw_crop_block = str(raw_crop_instruction or "").strip()
    compact_crop_block = _compact_raw_crop_instruction(raw_crop_block)
    crop_instruction_source = "raw_edit_prompt" if compact_crop_block else "compact_fallback"
    if not compact_crop_block:
        compact_crop_block = (
            f"- Output aspect ratio: {ratio}.\n"
            "- Crop/reframe by choosing the frame and scaling uniformly; never stretch or squash image content.\n"
            f"- Resolved crop plan: {crop_text}"
        )

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
            head_face_adjustment = str(state.get("head_face_adjustment") or "").strip()
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
            state_lines.append("- Body reshape — apply every approved adjustment below to this person and no unlisted body change:")
            state_lines.extend(f"  - {point}" for point in body_adjustments)
            state_lines.append(
                "- Keep the adjustment restrained and anatomically believable. Preserve the overall identity and pose unless a listed point explicitly requests a small posture correction."
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

    feature_terms = ("視線", "眼神", "虹膜", "瞳孔", "direct_camera", "gaze", "閉眼", "眼皮", "blink", "笑", "smile", "laugh")
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
            "- Fixed identity geometry: retain the source-matched head/face width-to-length ratio, skeletal jaw/chin contour, cheekbone placement, baseline eye–nose–mouth landmark layout, eye-globe size and interocular/canthus positions, nose bridge/tip/nostril geometry, philtrum length, ear shape, and natural asymmetry. Preserve lashes/brows, makeup, and all landmarks outside an explicitly authorized local movement."
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
            "- Where smile and gaze affect the same person, gaze changes iris/pupil direction only. Let laughter narrow the eyelids modestly through natural cheek lift while retaining canthi, lid folds, recognizable eye openness, and iris visibility; avoid both fixed-open staring and exaggerated squinting."
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
        "\nBody reshape execution boundary:\n"
        "- Treat body work as a local contour/proportion correction, not a new identity, weight-loss transformation, fitness transformation, or unrelated pose change. Preserve unlisted body regions, face, skin texture, hair, hands, feet, accessories, nearby people/objects, background lines, architecture, camera viewpoint, and perspective. No warping, bending, smearing, duplication, or displacement outside the named body region.\n"
        "- CLOTHING/BODY TOPOLOGY HARD LOCK: clothing is a preservation layer, never a substitute for body reshaping. Keep every neckline, sleeve opening, shirt/top hem, waistband, trouser/skirt top, belt, exposed-skin boundary, overlap, seam, fold, opening, garment length, and tucked/untucked state at its exact source position, shape, coverage, and front/back ordering.\n"
        "- Never extend/lower a shirt to cover exposed abdomen, raise a waistband, add fabric, remove exposed skin, reveal new skin, shorten/lengthen a garment, or redraw folds to conceal body volume. If bare abdomen is visible between a top hem and waistband, preserve that same bare area, hem, waistband, navel, and relative order.\n"
        "- A body contour may move only inside its existing skin or clothing region. Keep a narrow immutable guard band around hems, waistbands, necklines, sleeve openings, and every skin/clothing boundary. If a requested reshape cannot be achieved without moving or crossing those anchors, leave the body contour unchanged rather than modifying clothing."
        if "body_reshape" in resolved_features else ""
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

    spatial_preserve = ["relative positions", "depth order", "spacing", "overlap"]
    if "angle" in style_options:
        spatial_preserve = ["underlying scene layout"]
    if "size" not in style_options:
        spatial_preserve.extend(["scale", "visible extent", "framing"])
    body_posture_active = any(
        "body_pose" in item.get("change_footprint", []) for item in resolved
        if "body_reshape" in item.get("features", [])
    )
    if "pose" not in style_options and not body_posture_active:
        spatial_preserve.extend(["pose", "gesture", "interaction points"])
    elif "pose" not in style_options:
        spatial_preserve.extend(["gesture", "interaction points outside the named posture adjustment"])
    spatial_preserve_text = ", ".join(spatial_preserve)
    style_scope_text = (
        "- Selected style options may change only: " + ", ".join(sorted(style_footprint)) + "."
        if style_footprint else
        "- No style option authorizes a change to pose, viewpoint, or framing size."
    )
    body_reshape_active = "body_reshape" in resolved_features
    if "pose" in style_options:
        facial_locality = (
            "- Apply facial feature edits locally. Head/body movement is limited to the selected pose style "
            "plus any explicitly resolved gaze or body-reshape endpoint."
        )
    elif has_head_aligned_gaze and body_reshape_active:
        facial_locality = (
            "- Apply facial edits locally. Only the resolved eyes-plus-head/face gaze endpoint may make its specified "
            "bounded head/face alignment. Apply body changes only inside the named body regions and by the minimum "
            "amount required; keep surrounding content and unlisted body regions fixed."
        )
    elif has_head_aligned_gaze:
        facial_locality = (
            "- Apply facial edits locally. Only the resolved eyes-plus-head/face gaze endpoint may make its specified bounded head/face alignment; "
            "keep the body and surrounding content fixed."
        )
    elif body_reshape_active:
        facial_locality = (
            "- Apply facial edits locally. Apply body changes only inside the named body regions and by the minimum "
            "amount required; do not move the head, surrounding content, or unlisted body regions."
        )
    else:
        facial_locality = "- Apply facial edits locally without moving the head, body, or surrounding content."
    portrait_style_priority = (
        "- Resolved subject final states remain mandatory inside the style transformation; style may coordinate surrounding pose and light but must not replace a specified smile, eye state, gaze, hair, or body-reshape endpoint."
        if style_footprint and resolved else ""
    )

    prompt = f"""You are editing the supplied image, not creating a new image.
Act like a high-end human retoucher: restrained, photographic, detail-aware, and realistic.

Required framing:
{compact_crop_block}

{visual_target_block}

{user_preserved_block}

Other approved corrections:
{correction_block}

Resolved subject final states:
Locations identify subjects only; they are not target positions.
{subjects_block}
{shared_face_block}{hair_boundary_block}{body_boundary_block}

{color_block}

{skin_block}

Finish:
{finish}

Mandatory style overrides:
{style_text}

User direction:
{user_text}

Global preservation:
- Always preserve identities, underlying anatomy or object form, clothing, location, scene identity, and natural material texture. Do not transform, add, or erase people/objects; authorized framing may place existing content outside the output frame.
- Preserve each person's anatomical facial proportions and profile identity. Keep head yaw/pitch and projected eye–nose–mouth geometry fixed unless a resolved eyes-plus-head/face gaze endpoint or selected style explicitly changes viewpoint; then change only the specified orientation and its natural projection, never the underlying anatomy.
- Preserve these unselected properties from the original: {spatial_preserve_text}.
{style_scope_text}
{facial_locality}
{portrait_style_priority}
- Preserve natural skin and material texture. Do not add text, logos, watermarks, people, props, or a different background.
- Every required final state above is mandatory. Do not restore a captured property that an authorized feature needs to change.{revision_text}
"""
    return prompt.strip(), {
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
        "style_instruction_source": style_instruction_source,
        "raw_style_instruction": raw_style_block,
        "compact_style_instruction": compact_style_block,
        "style_change_footprint": sorted(style_footprint),
    }
