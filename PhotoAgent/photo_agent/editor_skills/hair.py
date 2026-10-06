"""Hair skill: approved hair cleanup and the user-selected hair level."""

from prompts import _disabled_issue_subject_indices, resolve_user_hair_request

from .base import EditorSkill, _axis_set
from .face import _BASE_PRESERVE, _box, _resolved_features


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


def _hair_contracts(analysis):
    options = analysis.get("_advanced_options") or {}
    if not options.get("hair_mess_detection", True):
        return []
    # Compatibility for analyses produced before hair approval moved to the
    # per-person face-review toggle. New analyses never put hair on these
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
            "subject_index": index,
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
        contracts.append(contract)
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


class HairSkill(EditorSkill):
    name = "hair"
    hidden_preserve = frozenset(_HAIR_IDENTITY_PRESERVE)

    def contracts(self, analysis, ctx):
        return _hair_contracts(analysis)

    def subject_lines(self, item, ctx):
        state_lines = []
        state = item["final_state"]
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
        return state_lines

    def execution_block(self, analysis, resolved, ctx):
        if "hair" not in _resolved_features(resolved):
            return ""
        return (
            "\nHair execution boundary:\n"
            "- Treat hair work as an arrangement/local-sheen edit, not a head or face regeneration. Keep the source haircut, parting, length, volume, density, hairline, curl pattern, and color. Where hair crosses a facial feature, retain that feature's visible source shape and position and repair only the strand-width footprint; any authorized eye or expression changes follow only their own resolved final states above."
        )
