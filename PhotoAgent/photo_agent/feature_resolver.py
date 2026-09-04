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
    _read_smile_states,
    resolve_smile_targets,
    smile_level,
    subject_issue_items,
)

_BASE_PRESERVE = {
    "identity", "underlying_facial_anatomy", "head_pose", "body_pose",
    "skin_texture", "hair", "clothing", "scene", "lighting",
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
    lowered = text.lower()
    return "skin" in axes or (
        any(term in lowered for term in _SKIN_TERMS)
        and any(term in lowered for term in _SKIN_COLOR_TERMS)
    )


def _is_hair_correction(text, axes):
    lowered = str(text or "").lower()
    return any("hair" in str(axis).lower() for axis in axes) or any(
        term in lowered for term in _HAIR_PLAN_TERMS
    )


def _build_color_blocks(analysis, skin_corrections, lighting_style=None):
    """Compact equivalents of the tuned legacy color craft and stop check."""
    rejected = _axis_set(analysis.get("rejected_axes")) - _axis_set(analysis.get("correction_axes"))
    tonal = []
    if "shadow" not in rejected:
        tonal.append("open shadows enough to reveal detail without flattening them")
    if "highlight" not in rejected:
        tonal.append("hold highlights so they roll off rather than clip")
    if "contrast" not in rejected:
        tonal.append("build depth through tonal separation between subject, midground, and background")
    tonal_line = (
        "- Work like a skilled colorist: " + "; ".join(tonal) + "."
        if tonal else
        "- Keep tonal rendering within the approved correction plan."
    )
    allowed_color = []
    if "saturation" not in rejected:
        allowed_color.append("saturation")
    if "white_balance" not in rejected:
        allowed_color.append("warm/cool balance")
    if lighting_style:
        lighting_label = {"gold": "Gold Tone", "soft": "Soft Glow"}.get(lighting_style, lighting_style)
        color_line = (
            f"- The selected {lighting_label} style defines the final lighting and color atmosphere. Apply it visibly and coherently "
            "across subject and environment while retaining believable material colors; it overrides preserving the captured light/color character."
        )
    else:
        color_line = (
            "- Draw visual presence primarily from exposure, contrast placement, and micro-contrast. "
            f"Use {' and '.join(allowed_color)} only secondarily to refine the non-skin palette without imposing a new look."
            if allowed_color else
            "- Draw visual presence from light and tonal separation; preserve the original color palette."
        )
    memory = str(analysis.get("memory_color_strategy") or "").strip()
    memory_line = (
        f"\n- Scene-specific memory colors: {memory}"
        if memory and not lighting_style else ""
    )
    color_block = f"""Global light and color:
{tonal_line}
{color_line}
- This global grade governs the non-skin palette. Skin is graded independently under the Skin color contract below and does not have to inherit the same white-balance, hue, or saturation shift.{memory_line}"""

    identities = _skin_identity_lines(analysis)
    identity_text = "\n".join(identities) if identities else "- Read each person's existing depth and undertone from the supplied photograph."
    correction_text = "\n".join(f"- Approved skin correction: {item}" for item in skin_corrections)
    if not correction_text:
        correction_text = "- No local skin-color correction is approved; retain each person's native complexion under the final scene light."
    skin_block = f"""Skin color contract:
{identity_text}
{correction_text}
- Preserve each person's native complexion depth, undertone, and chroma. Let skin respond naturally to the final scene light without tinting skin independently.
- Grade skin independently from the surrounding palette. A global white-balance, hue, or saturation adjustment may change the environment without forcing the same shift onto skin; skin still responds naturally to shared exposure, contrast, and actual scene illumination.
- Treat wording such as “slightly warmer” only as a relative correction away from a specifically identified unwanted cool/cyan/green/gray cast, stopping at that person's recorded native complexion under the existing light. It is not permission to add warmth, yellow, orange, red, rosiness, or saturation.
- Correct face, neck, ears, and visible hands as one continuous skin surface with no patch, band, edge, or mismatched region. Preserve pores and natural tonal modelling."""

    if lighting_style:
        lighting_label = {"gold": "Gold Tone", "soft": "Soft Glow"}.get(lighting_style, lighting_style)
        grade_check = f"""Final color check:
- The selected {lighting_label} lighting endpoint is clearly visible across both subject and environment, with believable depth and highlight rolloff.
- Material colors remain plausible and nobody's complexion identity is changed; the style reads as scene light, not a uniform skin tint or pasted-on filter."""
    else:
        grade_check = """Final color check against the original:
- The result retains the original photograph's intentional color character while showing visibly better tonal depth, highlight rolloff, and tonal distinction between subject and background.
- Every approved cast correction is complete without introducing a new global filter or changing anyone's complexion identity."""
    return color_block, skin_block, grade_check


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
            "preserve": sorted(_BASE_PRESERVE | {"gaze_direction"}),
        })
    return contracts


def _gaze_contracts(analysis, include_gaze):
    options = analysis.get("_advanced_options") or {}
    if not include_gaze or not options.get("gaze_contact_detection", True):
        return []
    disabled = _disabled_issue_subject_indices(analysis, "gaze_issue")
    contracts = []
    for item in subject_issue_items(analysis, "gaze_issue"):
        if not item.get("should_fix") or item.get("_subject_index") in disabled:
            continue
        strategy = item.get("gaze_repair_strategy") or {}
        contracts.append({
            "feature": "gaze",
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": {
                "gaze": "direct_camera",
                "method": strategy.get("strategy_type") or "eyes_only",
                "strength": strategy.get("repair_strength") or "moderate",
            },
            "direct_changes": ["gaze_direction", "iris_direction", "pupil_direction"],
            "dependent_changes": ["sclera_balance", "catchlight_consistency"],
            # Existing analysis strategies commonly freeze these during gaze
            # repair. They are compatible for gaze-only, but are removed from
            # preservation when a smile owns the final facial expression.
            "preserve": sorted(_BASE_PRESERVE | {
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
    for item in subject_issue_items(analysis, "blink_issue"):
        if not item.get("should_fix") or item.get("_subject_index") in disabled:
            continue
        contracts.append({
            "feature": "blink",
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": {"eye_state": "naturally_open_and_matched_to_expression"},
            "direct_changes": ["eyelid_aperture"],
            "dependent_changes": ["iris_visibility", "lash_line"],
            "preserve": sorted(_BASE_PRESERVE),
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
    fully_groomed = bool(options.get("calm_hair"))
    contracts = []
    for index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict):
            continue
        issue = subject.get("hair_issue") or {}
        local_fix = (
            isinstance(issue, dict) and issue.get("should_fix")
            and index not in disabled
        )
        if not local_fix and not (fully_groomed and subject.get("is_person")):
            continue
        action = issue.get("fix_action") if local_fix else ""
        hair_level = "fully_groomed" if fully_groomed else "local_cleanup"
        correction_plan = str(issue.get("correction_plan") or "").strip() if local_fix else ""
        if local_fix and not fully_groomed and issue.get("hair_state") == "windblown":
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


def build_feature_contracts(analysis, *, include_gaze=True):
    """Return active, editor-stage feature contracts from analysis/options."""
    return (
        _smile_contracts(analysis)
        + _gaze_contracts(analysis, include_gaze)
        + _blink_contracts(analysis)
        + _hair_contracts(analysis)
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
            state_lines.append("- Coordinate the mouth, cheeks, brows, periocular muscles, and eyelid aperture naturally as one expression.")
        if state.get("eye_state"):
            state_lines.append("- Open or relax the accidentally closed/half-closed eye naturally, matching the final expression and the person's anatomy.")
        if state.get("gaze"):
            state_lines.append("- Within the final expression, align both irises and pupils toward the camera for unmistakable direct lens contact.")
            state_lines.append("- Rebalance sclera and catchlights naturally around the new iris positions.")
        hair_level = state.get("hair_level")
        hair_correction_plan = str(state.get("hair_correction_plan") or "").strip()
        hair_action = state.get("hair_action")
        redistribute = (
            hair_action == "redistribute"
            and state.get("hair_state") == "bangs_clumped"
            and state.get("front_hair") == "clumped"
        )
        if hair_level == "local_cleanup" and not redistribute:
            state_lines.append(
                "- Hair level — local cleanup: complete the approved defect correction inside its named footprint while preserving the rest of the captured hairstyle and movement. This preservation boundary does not permit leaving the named defect unchanged."
            )
        elif hair_level == "fully_groomed":
            state_lines.append(
                "- Hair level — fully groomed: bring lifted, fanned, or streaming hair under its own weight into the still-air silhouette of this person's existing haircut, retaining natural strand separation rather than forming a smooth solid mass."
            )
        if redistribute:
            evidence = "；".join(state.get("hair_visible_evidence") or [])
            target = evidence or hair_correction_plan
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
            state_lines.append(f"- {_hair_action_instruction(state)}")
            if redistribute:
                state_lines.append(
                    "- Preserve the source fringe type, main part and sweep, length, outer edge, hairline, and total hair mass. Keep subtype-defining openings, but not the defective clump layout or accidental gaps; do not add hair or form an opaque sheet."
                )
        display_preserve = set(item["preserve"]) - _HAIR_IDENTITY_PRESERVE
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
    correction_axes = analysis.get("correction_axes") or []
    for index, raw_item in enumerate(analysis.get("correction_plan") or []):
        item = str(raw_item)
        axes = _axis_set([correction_axes[index]]) if index < len(correction_axes) else set()
        if any(term.lower() in item.lower() for term in feature_terms):
            continue
        if _is_hair_correction(item, axes):
            # Hair has one authoritative route: the matching person's
            # hair_issue. Call A occasionally violates its schema and emits a
            # free-text hair item with tags such as hair_edge; never let that
            # unscoped copy become an Other approved correction.
            continue
        elif _is_skin_correction(item, axes):
            skin_corrections.append(item)
        else:
            other_corrections.append(item)
    correction_block = "\n".join(f"- {item}" for item in other_corrections) or "- No additional approved local corrections."
    color_block, skin_block, grade_check = _build_color_blocks(
        analysis, skin_corrections, lighting_style=style_options.get("lighting"),
    )
    subjects_block = "\n\n".join(subject_blocks) or "No resolved facial or full-groom transformation is active."
    hair_boundary_block = (
        "\nHair execution boundary:\n"
        "- Treat hair work as an arrangement/local-sheen edit, not a head or face regeneration. Keep the source haircut, parting, length, volume, density, hairline, curl pattern, and color. Where hair crosses a facial feature, retain that feature's visible source shape and position and repair only the strand-width footprint; any authorized eye or expression changes follow only their own resolved final states above."
        if "hair" in resolved_features else ""
    )
    user_text = user_direction.strip() or "No additional direction."
    revision_text = f"\n\nRevision feedback:\n{revision_feedback}" if revision_feedback else ""
    # Compact mode deliberately does not reuse prose that freezes a captured
    # expression (the portrait preset historically does). These endpoint-safe
    # finishes describe only rendering qualities, leaving subject state to the
    # resolved blocks above.
    finish = {
        "natural": "Use a restrained premium photographic finish with believable light, faithful color, controlled contrast, and natural texture.",
        "editorial": "Use a restrained editorial finish with graceful highlight rolloff, confident tonal separation, faithful color, and no gimmicks.",
        "product": "Use a clean commercial finish with truthful shape, material texture, color, light, and disciplined background rendering.",
        "portrait": "Use a humane photographic portrait finish with identity, skin tone, facial geometry, and natural texture intact.",
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
    if "pose" not in style_options:
        spatial_preserve.extend(["pose", "gesture", "interaction points"])
    if "lighting" not in style_options:
        spatial_preserve.append("lighting logic")
    spatial_preserve_text = ", ".join(spatial_preserve)
    style_scope_text = (
        "- Selected style options may change only: " + ", ".join(sorted(style_footprint)) + "."
        if style_footprint else
        "- No style option authorizes a change to pose, viewpoint, lighting style, or framing size."
    )
    facial_locality = (
        "- Apply facial feature edits locally. Any head/body movement must come only from the selected pose style."
        if "pose" in style_options else
        "- Apply facial edits locally without moving the head, body, or surrounding content."
    )
    portrait_style_priority = (
        "- Resolved subject final states remain mandatory inside the style transformation; style may coordinate surrounding pose and light but must not replace a specified smile, eye state, gaze, or hair endpoint."
        if style_footprint and resolved else ""
    )

    prompt = f"""Edit the supplied photograph; do not create a new scene.

Required framing:
{compact_crop_block}

Resolved subject final states:
Locations identify subjects only; they are not target positions.
{subjects_block}
{hair_boundary_block}

Other approved corrections:
{correction_block}

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
- Preserve these unselected properties from the original: {spatial_preserve_text}.
{style_scope_text}
{facial_locality}
{portrait_style_priority}
- Preserve natural skin and material texture. Do not add text, logos, watermarks, people, props, or a different background.
- Every required final state above is mandatory. Do not restore a captured property that an authorized feature needs to change.

{grade_check}{revision_text}
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
            "memory_color_strategy": analysis.get("memory_color_strategy") or "",
        },
        "crop_instruction_source": crop_instruction_source,
        "raw_crop_instruction": raw_crop_block,
        "compact_crop_instruction": compact_crop_block,
        "style_instruction_source": style_instruction_source,
        "raw_style_instruction": raw_style_block,
        "compact_style_instruction": compact_style_block,
        "style_change_footprint": sorted(style_footprint),
    }
