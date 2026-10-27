"""Eyes skill: approved blink and gaze-contact repairs."""

import re

from prompts import (
    _disabled_issue_subject_indices,
    _has_person,
    approved_blink_items,
    approved_gaze_items,
    build_eye_repair_pass_prompt,
    final_smile_by_subject,
    resolve_user_blink_request,
    resolve_user_gaze_request,
    subject_issue_items,
)

from .base import (
    MODE_HEAD_ALIGNED_GAZE,
    EditorFeature,
    EditorSkill,
    FollowupEdit,
    _style_change_footprint,
)
from .expression import _smile_contracts
from .face import (
    FACE_SYMMETRY,
    _BASE_PRESERVE,
    _EYE_IDENTITY_PRESERVE,
    _EYE_MAKEUP_CARRY_RULE,
    _IDENTITY_GEOMETRY_RULE,
    _box,
    _resolved_features,
    has_user_face_state,
    resolve_feature_contracts,
)


def _repair_strength(value, default="moderate"):
    """Collapse model prose back to the small strength vocabulary."""
    text = str(value or "").strip().lower()
    for level in ("subtle", "moderate", "strong"):
        if text == level or text.startswith(level + " ") or text.startswith(level + ","):
            return level
    return default


def _compact_blink_strategy_text(value):
    """Keep planner detail while removing stale eyelid-asymmetry preservation.

    The photograph-side matching contract owns final aperture and visible
    double-eyelid band width. Older saved plans can contain a clause such as
    ``while retaining a natural, non-identical asymmetry``; forwarding it to
    the compact editor would directly contradict that final state.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"(?i)\s*(?:,?\s*(?:while|and)\s+)?"
        r"(?:preserv(?:e|es|ed|ing)|retain(?:s|ed|ing)?|maintain(?:s|ed|ing)?)\s+"
        r"(?:an?\s+)?(?:natural[\s,-]+)?(?:non[ -]?identical[\s,-]+)?"
        r"(?:eyelid[ -]?)?asymmetr(?:y|ies)",
        "",
        text,
    )
    text = re.sub(
        r"(?:保留|維持|保持)[^。；;]*(?:眼瞼|眼皮|雙眼皮)?(?:不對稱|非對稱)[。；;]?",
        "",
        text,
    )
    return re.sub(r"\s{2,}", " ", text).strip(" ,;；")


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
        contract = {
            "feature": "gaze",
            "subject_index": item.get("_subject_index"),
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
        contracts.append(contract)
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
        # never uses, so a subject carrying both features cannot collide. Old
        # single-call analyses carry no per-person blink strategy, so these stay
        # absent (and the rendered line generic) for them -- except a forced
        # row, which always carries one (resolve_user_blink_request).
        strategy = item.get("blink_repair_strategy") or {}
        endpoint = {
            "eye_state": "naturally_open_and_matched_to_expression",
            "blink_user_forced": blink_user_forced,
        }
        subject_index = item.get("_subject_index")
        subjects = analysis.get("subjects") or []
        subject = (
            subjects[subject_index]
            if isinstance(subject_index, int)
            and not isinstance(subject_index, bool)
            and 0 <= subject_index < len(subjects)
            and isinstance(subjects[subject_index], dict)
            else {}
        )
        gaze_issue = subject.get("gaze_issue") or {}
        endpoint["head_pose"] = str(gaze_issue.get("head_pose") or "unclear").strip().lower()
        photograph_states = {
            "photograph-left": item.get("left_eye_state"),
            "photograph-right": item.get("right_eye_state"),
        }
        source_eye_states = tuple(photograph_states.values())
        endpoint.update({
            "photograph_left_eye_state": photograph_states["photograph-left"],
            "photograph_right_eye_state": photograph_states["photograph-right"],
        })
        fully_closed_sides = [
            side for side, state in photograph_states.items() if state == "fully_closed"
        ]
        if len(fully_closed_sides) == 1:
            closed_side = fully_closed_sides[0]
            reference_side = "photograph-right" if closed_side == "photograph-left" else "photograph-left"
            reference_state = photograph_states[reference_side]
            # Only a genuinely open eye is an endpoint reference; a narrowed
            # or half-closed opposite eye is blink-affected itself and is
            # opened alongside the closed one by the paired branch below.
            if reference_state == "open":
                endpoint.update({
                    "paired_blink_repair": True,
                    "closed_photograph_side": closed_side,
                    "reference_photograph_side": reference_side,
                    "reference_eye_state": reference_state,
                })
        # Every two-eye blink combination is handled as one coordinated final
        # eyelid state. Exactly-one-fully-closed cases above additionally name
        # the readable photograph-side reference.
        paired_blink_repair = (
            bool(endpoint.get("reference_photograph_side"))
            or (
                all(state in {"fully_closed", "half_closed", "narrowed_open"} for state in source_eye_states)
                and any(state in {"fully_closed", "half_closed"} for state in source_eye_states)
            )
        )
        if paired_blink_repair:
            endpoint["paired_blink_repair"] = True
            if (
                not endpoint.get("reference_photograph_side")
                and len(set(source_eye_states)) > 1
            ):
                # Mixed blink states (for example fully closed beside
                # half-closed) visually bias the editor toward unequal final
                # eyes. Equal-state pairs and repairs with a genuinely open
                # reference do not need the stricter anti-compensation rule.
                endpoint["asymmetric_blink_pair"] = True
        for key, source in (
            ("eyelid_target", "target_eye_state"),
            ("eyelid_strength", "repair_strength"),
            ("eyelid_adjustment", "eyelid_adjustment"),
        ):
            if (
                key in {"eyelid_target", "eyelid_adjustment"}
                and paired_blink_repair
            ):
                # Paired repairs use one deterministic, target-only contract
                # below.  Keeping Call C's prose here repeats captured
                # fully_closed/half_closed states and makes the image model
                # anchor on the defect instead of the desired final pair.
                continue
            value = str(strategy.get(source) or "").strip()
            if key in {"eyelid_target", "eyelid_adjustment"}:
                value = _compact_blink_strategy_text(value)
            if value:
                endpoint[key] = _repair_strength(value, default="subtle") if key == "eyelid_strength" else value
        if (
            "fully_closed" in source_eye_states
            or sum(state == "half_closed" for state in source_eye_states) == 2
        ):
            endpoint["eyelid_strength"] = "strong"
        contract = {
            "feature": "blink",
            "subject_index": item.get("_subject_index"),
            "subject": item.get("subject_label") or "畫面中的人物",
            "box": _box(item),
            "endpoint": endpoint,
            "direct_changes": ["eyelid_aperture"],
            "dependent_changes": ["iris_visibility", "lash_line_position"],
            "preserve": sorted(_BASE_PRESERVE | _EYE_IDENTITY_PRESERVE),
        }
        contracts.append(contract)
    return contracts


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


_BLINK_IDENTITY_SCOPE = (
    " For each named blink repair, adjust only the eyelid margins, crease, and minimally necessary lid "
    "endpoints; preserve brows, eye centers, globes, lashes, makeup, and surrounding anatomy."
)


# With a balanced face the user-directed Eyes item sets eye size and position, so a blink repair keeps only lashes and makeup.
_USER_STATE_BLINK_IDENTITY_SCOPE = (
    " For each named blink repair, adjust the eyelid margins, crease, and lid endpoints; preserve lashes and makeup, "
    "with brows, eye centers and eye size where the user-directed final state sets them."
)

# Gaze target for a person with a balanced face: the analyzer's own wording usually also freezes the eye openings.
_USER_STATE_GAZE_ADJUSTMENT = (
    "move both irises and pupils so the two visual axes meet at the camera lens, parallel and natural, with no "
    "crossed eyes, side-eye or excess sclera. Eye openings, eyelids, eye size and eye shape follow the "
    "user-directed Eyes item; this gaze change only moves the irises and pupils"
)


_BLINK_PAIR_RULES = (
    "- Shared eye-pair target for every named blink repair: finish both eyes with the same natural degree "
    "of opening. Match their opening size and shape, visible iris width, upper- and lower-lid contours, "
    "and double-eyelid fold shape and visible width.",
    "- Retract each upper-lid margin as needed and fold blink-compressed lid tissue into a smooth crease over "
    "the existing globe. Keep both final openings and creases equally clear under the original lighting, "
    "lashes, makeup, and lid shadows.",
)


def _eye_state_lines(state, *, subject_has_blink, style_options, include_gaze_method=True):
    """Eyelid and gaze lines for one resolved subject; include_gaze_method=False leaves the gaze method to another prompt."""
    state_lines = []
    smile = state.get("smile_level")
    if state.get("eye_state"):
        eyelid_strength = str(state.get("eyelid_strength") or "").strip()
        eyelid_target = str(state.get("eyelid_target") or "").strip().rstrip(".。")
        eyelid_adjustment = str(state.get("eyelid_adjustment") or "").strip().rstrip(".。")
        if state.get("paired_blink_repair"):
            strength_text = f"{eyelid_strength} " if eyelid_strength else ""
            opening_target = (
                "the same smile- or laughter-shaped final aperture, with matched natural periocular narrowing"
                if smile else
                "the same natural, comfortably open final aperture"
            )
            closed_side = state.get("closed_photograph_side")
            reference_side = state.get("reference_photograph_side")
            if closed_side and reference_side:
                reference_geometry = (
                    "finished expression-shaped eyelid geometry"
                    if smile else
                    "finished open eyelid geometry"
                )
                paired_eye_lines = [
                    f"Eye-pair repair ({strength_text}correction): use the {reference_side} eye's "
                    f"{reference_geometry} as the common design for the repaired {closed_side} eye; retain each "
                    "eye's own iris, lashes, and makeup."
                ]
            elif state.get("asymmetric_blink_pair"):
                opening_rank = {"fully_closed": 0, "half_closed": 1, "narrowed_open": 2, "open": 3}
                left_rank = opening_rank[state["photograph_left_eye_state"]]
                right_rank = opening_rank[state["photograph_right_eye_state"]]
                more_closed_side = "photograph-left" if left_rank < right_rank else "photograph-right"
                less_closed_side = "photograph-right" if left_rank < right_rank else "photograph-left"
                paired_eye_lines = [
                    f"Eye-pair repair ({strength_text}bilateral correction): open both eyes together to "
                    f"{opening_target}. Lift the {more_closed_side} upper lid farther than the "
                    f"{less_closed_side} upper lid so both finished eyes have matching opening height and "
                    "iris exposure. Match their upper- and lower-lid contours and natural double-eyelid folds."
                ]
            else:
                paired_eye_lines = [
                    f"Eye-pair repair ({strength_text}bilateral correction): open both blink-affected eyes to "
                    f"{opening_target}. Retract each upper-lid margin by the amount it needs to reach this "
                    "shared endpoint, then form matching smooth upper-lid creases."
                ]
            if state.get("head_pose") == "frontal":
                paired_eye_lines.append(
                    "For this frontal face, match the final upper- and lower-lid contours across the facial midline while "
                    "keeping the eye centers and interocular distance stable."
                )
            else:
                paired_eye_lines.append(
                    "For this turned face, match the final lid contours in each eye's local axis with natural "
                    "perspective projection."
                )
            state_lines.extend(f"- {line}" for line in paired_eye_lines)
            eyelid_text = None
        elif smile:
            eyelid_text = (
                "Set both eyes to the same smile- or laughter-shaped final aperture, with matched natural "
                "periocular narrowing"
            )
        elif state.get("blink_user_forced"):
            # Forced means the analyzer itself found nothing to fix here --
            # unlike the branch below, "accidentally closed/half-closed"
            # would misdescribe the eye this row is actually about.
            opening = f"Apply a {eyelid_strength} eyelid correction: open" if eyelid_strength else "Open"
            eyelid_text = f"{opening} both eyes clearly and comfortably into a natural open resting state"
            if eyelid_target:
                eyelid_text += f". Target eyelid state: {eyelid_target}"
        else:
            if eyelid_target:
                opening = f"Apply a {eyelid_strength} eyelid correction" if eyelid_strength else "Apply an eyelid correction"
                eyelid_text = f"{opening} from the captured blink to the analysis-defined final eyelid state"
            else:
                opening = f"Apply a {eyelid_strength} eyelid correction: open" if eyelid_strength else "Open"
                eyelid_text = f"{opening} both eyes clearly and comfortably from the captured blink into a natural open resting state"
            if eyelid_target:
                eyelid_text += f". Target eyelid state: {eyelid_target}"
        if eyelid_text is not None:
            if eyelid_adjustment:
                eyelid_text += f". Eyelid method: {eyelid_adjustment}"
            if not eyelid_text.endswith("."):
                eyelid_text += "."
            state_lines.append(f"- {eyelid_text}")
    if include_gaze_method and state.get("gaze"):
        gaze_method = state.get("method") or "eyes_only"
        gaze_strength = state.get("strength") or "moderate"
        eye_adjustment = (
            _USER_STATE_GAZE_ADJUSTMENT if state.get(FACE_SYMMETRY)
            else _safe_gaze_eye_adjustment(state.get("eye_adjustment"))
        )
        head_face_adjustment = _safe_gaze_head_face_adjustment(
            state.get("head_face_adjustment"),
            allow_pose_change="pose" in style_options,
        )
        if gaze_method == "eyes_only":
            # A user-directed facial final state on this person owns the head angle.
            gaze_method_text = (
                f"Use a {gaze_strength} eyes-only correction"
                if state.get(FACE_SYMMETRY) else
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
    if subject_has_blink and not state.get("gaze"):
        state_lines.append(
            "- Inside the final eye pair, preserve the source-supported natural gaze target and head-compatible "
            "binocular direction. Set both irises, pupils, sclera balances, and catchlights coherently as one "
            "binocular pair under the original lighting; do not force camera contact."
        )
    elif smile and not state.get("gaze"):
        state_lines.append("- Keep the captured iris/pupil direction, sclera balance, and catchlights.")
    return state_lines


def build_blink_pass_targets(analysis):
    """Blink repairs for the two-stage eye pass (Step 5b), worded exactly as a single-pass Step 5 would word them.

    Returns (targets, shared_rules); each target is {subject_index, label, box, lines}.
    """
    blink = _blink_contracts(analysis)
    if not blink:
        return [], []
    resolved = resolve_feature_contracts(
        _smile_contracts(analysis) + _gaze_contracts(analysis, True) + blink,
        subject_count=len(analysis.get("subjects") or []),
    )
    style_options, _ = _style_change_footprint(analysis.get("style_options"))
    targets = []
    for item in resolved:
        if "blink" not in item["features"]:
            continue
        targets.append({
            "subject_index": item.get("subject_index"),
            "label": item["subject"],
            "box": item["box"],
            "lines": _eye_state_lines(
                item["final_state"], subject_has_blink=True, style_options=style_options,
                include_gaze_method=False,
            ),
        })
    rules = [_IDENTITY_GEOMETRY_RULE + _BLINK_IDENTITY_SCOPE, *_BLINK_PAIR_RULES, _EYE_MAKEUP_CARRY_RULE]
    return targets, rules


_RULE_EYELIDS = 20
_RULE_SMILE_GAZE = 50
_RULE_GAZE = 60
_QA_BLINK = 10
_QA_GAZE = 50
_CAP_BLINK = 20


BLINK_FEATURE = EditorFeature(
    id="blink",
    label="閉眼偵測",
    description=(
        "Reopen a person's accidentally closed or half-closed eyes so both eyes match in opening and "
        "eyelid fold, keeping the person's identity and expression."
    ),
    issue_field="blink_issue",
    verdict_field="should_fix",
    toggle="eye_closed_detection",
)

GAZE_FEATURE = EditorFeature(
    id="gaze",
    label="眼神對焦",
    description=(
        "Redirect a person's gaze to make direct eye contact with the camera lens, turning the head "
        "slightly only where the analysis planned it."
    ),
    issue_field="gaze_issue",
    verdict_field="should_fix",
    toggle="gaze_contact_detection",
)


class EyesSkill(EditorSkill):
    name = "eyes"
    # Eye-identity properties are rendered in concrete visual terms by the eye lines.
    hidden_preserve = frozenset(_EYE_IDENTITY_PRESERVE)
    facial_features = frozenset({"gaze", "blink"})

    def features(self):
        return (BLINK_FEATURE, GAZE_FEATURE)

    def final_state_terms(self, analysis):
        return ("eye state", "gaze")

    def contracts(self, analysis, ctx):
        return (
            _gaze_contracts(analysis, ctx.include_gaze)
            + (_blink_contracts(analysis) if ctx.include_blink else [])
        )

    def subject_lines(self, item, ctx):
        return _eye_state_lines(
            item["final_state"], subject_has_blink="blink" in item.get("features", []),
            style_options=ctx.style_options,
        )

    def prompt_modes(self, analysis, resolved, ctx):
        return {MODE_HEAD_ALIGNED_GAZE} if _has_head_aligned_gaze(resolved) else set()

    def identity_scope(self, resolved, ctx):
        blink_scope = _USER_STATE_BLINK_IDENTITY_SCOPE if has_user_face_state(resolved) else _BLINK_IDENTITY_SCOPE
        scope = blink_scope if "blink" in _resolved_features(resolved) else ""
        if _has_head_aligned_gaze(resolved):
            scope += (
                " An authorized eyes-plus-head/face gaze endpoint may update viewpoint projection only through its specified bounded head orientation; anatomical proportions remain fixed"
                + (" outside a user-directed final state." if has_user_face_state(resolved) else ".")
            )
        return scope

    def face_rules(self, resolved, ctx):
        features = _resolved_features(resolved)
        rules = []
        if "blink" in features:
            rules.extend((_RULE_EYELIDS, rule) for rule in _BLINK_PAIR_RULES)
        # A balanced face sets eye openings and size itself; these rules would pin them to the source.
        balanced = has_user_face_state(resolved)
        if not balanced and any({"smile", "gaze"}.issubset(set(item["features"])) for item in resolved):
            rules.append((_RULE_SMILE_GAZE,
                "- Where smile and gaze affect the same person, gaze changes iris/pupil direction only. Let laughter "
                "narrow both eyelids together through natural cheek lift while retaining clear iris visibility."
            ))
        if not balanced and "gaze" in features:
            rules.append((_RULE_GAZE,
                "- For gaze edits, retain iris size/color/texture and pupil proportions; change only necessary sclera balance and catchlight placement, preserving catchlight count, shape, brightness, and light-source logic."
            ))
        return rules


    def needs_top_tier_edit(self, analysis, single_pass_edit):
        # An approved blink repair only comes out reliably at the top tier; two-stage defers it to Step 5b.
        return single_pass_edit and bool(approved_blink_items(analysis))

    def followup_edit(self, analysis):
        """Step 5b: the approved gaze and/or blink repair(s) as an isolated edit on Step 5's output.

        Under the two-call path (single_pass_edit=False), Step 5 is told to leave
        gaze and eyelid state untouched even when a fix is approved (the
        include_gaze/include_blink gates), so this pass is the ONLY place those
        repairs happen. A single call bundling them with exposure/crop/skin/style
        reliably applies the coarse, whole-frame changes but often no-ops or
        under-applies the fine, localized eye work; a call whose prompt contains
        nothing but the eye repairs gives them the model's full attention.

        A blink repair runs on the top tier, the one blink repair is reliable on.
        Each toggle and per-person opt-out is honoured here as well as in the
        edit prompt, since this pass rebuilds its own item lists from the analysis.
        """
        gaze_items = approved_gaze_items(analysis)
        blink_targets, blink_rules = build_blink_pass_targets(analysis)
        if not gaze_items and not blink_targets:
            return None
        # Step 5 has already applied the smile setting; the gaze has to be redirected inside that expression.
        final_smile = final_smile_by_subject(analysis)
        gaze_items = [
            {**item, "_final_smile": final_smile.get(item.get("_subject_index"), final_smile.get(None))}
            for item in gaze_items
        ]
        return FollowupEdit(
            prompt=build_eye_repair_pass_prompt(gaze_items, blink_targets, blink_rules),
            top_tier=bool(blink_targets),
        )

    def qa_checks(self, analysis):
        # The edit prompt renders per-person repairs only for a photo with people.
        if not _has_person(analysis):
            return []
        checks = []
        if _blink_contracts(analysis):
            checks.append((_QA_BLINK, "- Approved accidental blink repair: if the retouch recipe or style/correction plan asked to fix a named person's accidental closed or half-closed eyes, verify that the eyelids are naturally corrected while identity, face shape, glasses, makeup, and expression style remain believable. Check two independent mandatory outcomes at normal viewing size: (1) the repaired eye has the same final eye-opening height and iris exposure as its photograph-side reference eye; (2) it has the same double-eyelid fold shape as that reference eye -- the same fold type, crease visible along the same portion of the lid, the same crease path from inner to outer corner, the same fold arc and peak height, and the same visible crease-to-lash band width. Compare size in face-relative geometry: fixed canthi and horizontal span, matching aperture-height-to-eye-width ratio, and matching upper/lower-lid profiles at the inner, central, and outer thirds, allowing only head-perspective differences rather than demanding duplicated pixels. When exactly one photograph-side source eye was fully closed and the opposite source eye was open, the opposite eye is the reference; compare against its finished state if another approved edit also changed it. When the opposite source eye was itself half-closed or narrowed, both eyes were to be opened, so check the two requirements between the two finished eyes and reject a result that leaves either eye still narrowed or half closed, or that finishes the two eyes at visibly different eye-opening heights. Do not accept a result that improves only one requirement or compensates unequal eye opening with unequal crease width. A formerly closed eye whose crease disappears against the lash line while the reference eye retains a visible double lid fails this check. The result must not be a no-op, merely sharpened eyelids, oversized/doll-like eyes, copied-looking eyes, puffy lids, or an unrelated expression change. If that same person also had an approved gaze repair, verify one coherent final result: the required eyelid match plus exact-lens pupil/iris alignment with natural sclera and catchlights, not two conflicting eye states. If no gaze repair was approved, preserve a natural gaze consistent with the original head direction instead of forcing camera contact.\n"))
        if _gaze_contracts(analysis, True):
            checks.append((_QA_GAZE, "- Approved gaze-contact repair: if a subject's gaze_issue.should_fix=true, or if the retouch recipe or correction plan asked to make a named person's gaze look toward the camera, verify that a clearly visible gaze-direction change actually occurred for the named subject and that the final gaze immediately and unmistakably reads as direct contact with the exact current lens at normal photo-viewing size. The iris/pupil positions and visual axes must aim naturally at the lens, with believable sclera balance and gaze-consistent catchlights. If the approved strategy is eyes_plus_head_face_alignment, verify that the head/face also visibly turned and/or tilted toward the lens by the planned bounded amount; unchanged head direction with pupil-only movement is a failure. If the approved strategy is eyes_only, verify that the original head was already frontal and lens-aligned and that the miss was genuinely tiny. In all cases preserve eyelid openness, identity, expression structure, facial anatomy, neck anatomy, body pose, shoulder direction, torso direction, glasses/reflections, and every unlisted person. Fail the check for a no-op, sharpen/brighten-only eyes, a change visible only in a highly enlarged crop, residual off-camera/near-camera/past-camera/slightly-beside-lens gaze, crossed or strained visual axes, excessive sclera, incompatible head/eye geometry, rotated shoulders/torso, or an unplanned identity/expression change.\n"))
        return checks

    def qa_score_caps(self, analysis):
        if not (_has_person(analysis) and _blink_contracts(analysis)):
            return []
        return [(_CAP_BLINK, '- If an approved blink repair leaves either eye closed/narrowed, or leaves visibly unequal normalized aperture, three-point lid contour, iris exposure, or double-eyelid fold path/width, score must be 4 or lower and pass must be false.\n')]


def _has_head_aligned_gaze(resolved):
    return any(
        item["final_state"].get("method") == "eyes_plus_head_face_alignment"
        for item in resolved
    )
