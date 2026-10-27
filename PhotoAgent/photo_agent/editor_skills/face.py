"""Shared per-person resolver: merges every skill's contracts for one person into one final state."""


_BASE_PRESERVE = {
    "identity", "underlying_facial_anatomy", "head_pose", "body_pose",
    "skin_texture", "hair", "clothing", "scene",
}


_EYE_IDENTITY_PRESERVE = {
    "eye_landmark_geometry", "iris_identity", "eyelash_identity",
    "eyelid_crease_identity", "eye_makeup", "glasses_and_reflections",
}


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
    """Resolve the small priority vocabulary used by explicit user choices."""
    value = item.get("priority", item.get("source_priority", 0))
    if item.get("user_override") or str(item.get("source") or "").lower() in {
        "user", "user_instruction", "explicit_user",
    }:
        return 100
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return {"user": 100, "explicit_user": 100, "planner": 10, "analysis": 10}.get(str(value).lower(), 0)


def _legacy_same_subject(left, right):
    """Keep the historical matching for ordinary feature contracts."""
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
    """Match stable-id contracts by parent subject, legacy contracts by old rules."""
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
    # A stable-id contract may coexist with a legacy face contract from an older
    # saved analysis.  Use the old geometric relation for that mixed pair.
    return _legacy_same_subject(left, right)


def _resolve_legacy_feature_contracts(contracts):
    """Resolve ordinary feature contracts with the historical semantics."""
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
    # Requests without stable subject ids retain the historical resolver output
    # and conflict semantics used by ordinary eye/hair/smile edits.
    if not any(
        "subject_index" in item
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
        for item in items:
            for key, value in (item.get("source_state") or {}).items():
                source_states.setdefault(key, value)
            for key, value in (item.get("endpoint") or {}).items():
                endpoint_candidates.setdefault(key, []).append((value, item))

        conflicts = []
        overridden_endpoints = []
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
        # active, so a conflicting choice cannot open a preservation exception.
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


_IDENTITY_GEOMETRY_RULE = (
    "- Fixed identity geometry: retain head/face proportions, jaw and cheekbone structure, the baseline "
    "eye–nose–mouth layout, eye-globe size, eye centers and interocular distance, nose geometry, philtrum "
    "length, and ear shape."
)
_USER_STATE_IDENTITY_RULE = (
    "- Identity geometry: a person's user-directed final state sets that face's contour, chin, nose line, eye "
    "height, size and opening, brows and mouth, and takes precedence over the rest of this rule. Everything else "
    "keeps the source head/face proportions, cheekbone structure, philtrum length, and ear shape."
)

# Endpoint key of a user's request that one person's face read balanced left to right.
FACE_SYMMETRY = "face_symmetry"


def has_user_face_state(resolved):
    """Whether any resolved person carries a user-directed facial-geometry final state."""
    return any(item["final_state"].get(FACE_SYMMETRY) for item in resolved)


_EYE_MAKEUP_CARRY_RULE = (
    "- Carry the original lash line/lashes, brow design, eyeliner, eyeshadow, mascara, false lashes, glasses, "
    "and reflections through authorized movement without redesigning, enhancing, removing, or replacing them."
)
# A balanced face may level and re-arch the brows, so only their colour and hairs are carried.
_USER_STATE_MAKEUP_CARRY_RULE = _EYE_MAKEUP_CARRY_RULE.replace("brow design", "brow color and hair texture")


def _resolved_features(resolved):
    return {feature for item in resolved for feature in item["features"]}


def render_shared_face_rules(skills, resolved, ctx):
    """The "Shared facial execution rules" block covering every resolved person."""
    facial = _resolved_features(resolved) & set().union(*(skill.facial_features for skill in skills))
    rules = []
    if facial:
        identity_rule = _USER_STATE_IDENTITY_RULE if has_user_face_state(resolved) else _IDENTITY_GEOMETRY_RULE
        rules.append((0, identity_rule + "".join(skill.identity_scope(resolved, ctx) for skill in skills)))
    for skill in skills:
        rules.extend(skill.face_rules(resolved, ctx))
    if facial:
        rules.append((100, _USER_STATE_MAKEUP_CARRY_RULE if has_user_face_state(resolved) else _EYE_MAKEUP_CARRY_RULE))
    rules.sort(key=lambda rule: rule[0])
    return (
        "\nShared facial execution rules:\n" + "\n".join(text for _, text in rules)
        if rules else ""
    )
