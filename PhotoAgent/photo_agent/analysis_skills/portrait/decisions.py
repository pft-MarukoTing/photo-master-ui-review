"""Merging Call B/C replies into the analysis and the deterministic blink/gaze/hair decisions."""

import re

from prompts import _HAIR_ACTION_BY_STATE, _HAIR_FALLBACK_PLAN_BY_STATE


def _default_person_issues():
    """The "不適用" shape for subjects with no portrait-QC reading -- the
    pre-merge baseline in photo_agent.analysis_skills.portrait, so
    every subjects[] entry is well-formed even before Call B runs or if it
    fails outright.
    """
    return {
        "blink_issue": {
            "blink_observability": "unreadable", "eyelid_detail_quality": "unreadable",
            "left_eye_state": "unclear", "right_eye_state": "unclear",
            "physical_eye_state": "unclear", "eye_closure_confidence": 0.0,
            "mouth_laughter_state": "unclear", "periocular_contraction": "unclear",
            "closure_expression_dependence": "unclear", "explicit_closure_context": "unclear",
            "intent_evidence_confidence": 0.0, "overall_demeanor": "unclear",
            "blink_candidate": False,
            "eye_state": "不適用", "severity": "none", "intent_cues": [],
            "closure_intent": "ambiguous", "expression_coherence": "unclear",
            "blink_failure_confidence": 0.0, "should_fix": False, "reason": "不適用",
            "blink_repair_strategy": {
                "target_eye_state": "不適用", "eyelid_adjustment": "不適用",
                "repair_strength": "subtle", "edit_instruction": "不適用",
                "success_condition": "不適用", "preserve_during_repair": [], "failure_to_avoid": [],
            },
        },
        "hair_issue": {
            "hair_state": "不適用", "visible_evidence": [], "front_hair": "不適用",
            "fix_action": "none", "mess_intent": "ambiguous", "hair_mess_confidence": 0.0,
            "should_fix": False, "correction_plan": "不適用", "reason": "不適用",
        },
        "gaze_issue": {
            "gaze_observability": "unreadable", "eye_detail_quality": "unreadable",
            "head_pose": "unclear", "left_eye_direction": "unclear",
            "right_eye_direction": "unclear", "binocular_alignment": "unverifiable",
            "gaze_state": "不適用", "eye_focus_quality": "unclear",
            "gaze_target_in_frame": {"present": False, "description": "不適用", "box": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}},
            "lens_contact_decision": "unclear", "lens_contact_confidence": 0.0,
            "not_looking_at_lens_confidence": 0.0, "gaze_correction_reasonableness": 0.0,
            "gaze_repair_strategy": {
                "strategy_type": "not_safe_to_repair", "original_gaze_read": "不適用",
                "target_gaze_read": "不適用", "eye_adjustment": "不適用",
                "sclera_catchlight_adjustment": "不適用", "head_face_adjustment": "none",
                "repair_strength": "subtle", "edit_instruction": "不適用",
                "success_condition": "不適用", "preserve_during_repair": [], "failure_to_avoid": [],
            },
            "should_fix": False, "reason": "不適用",
        },
        "smile_issue": {"smile_intensity": "unclear", "adjust_safe": False, "reason": "不適用"},
    }


def _apply_default_person_issues(subjects):
    for subject in subjects or []:
        defaults = _default_person_issues()
        for key, value in defaults.items():
            subject.setdefault(key, value)
    return subjects


def merge_portrait_qc_issues(analysis, portrait_qc):
    """Merge Call B into Call A by stable subject_index, never by position.

    Raises ValueError on a missing, duplicate, out-of-range, non-person, or
    face-crop-mismatched result. Each candidate hair repair remains nested in
    that person's hair_issue.correction_plan for the Python decision pass.
    """
    cleaned = dict(analysis)
    subjects = [dict(subject) for subject in (analysis.get("subjects") or [])]
    cleaned["subjects"] = subjects

    expected_indices = {
        index for index, subject in enumerate(subjects)
        if isinstance(subject, dict) and subject.get("is_person")
    }
    seen_indices = set()

    for item in (portrait_qc or {}).get("person_issues") or []:
        if not isinstance(item, dict):
            raise ValueError("portrait-QC person_issues entries must be objects")

        subject_index = item.get("subject_index")
        if isinstance(subject_index, bool) or not isinstance(subject_index, int):
            raise ValueError("portrait-QC subject_index must be an integer")
        if subject_index not in expected_indices:
            raise ValueError(
                "portrait-QC subject_index {} does not identify a person in Call A".format(subject_index)
            )
        if subject_index in seen_indices:
            raise ValueError("duplicate portrait-QC subject_index {}".format(subject_index))

        subject = subjects[subject_index]
        expected_detection_index = subject.get("local_detection_index", -1)
        returned_detection_index = item.get("local_detection_index", -1)
        if returned_detection_index != expected_detection_index:
            raise ValueError(
                "portrait-QC local_detection_index mismatch for subject_index {}: expected {}, got {}".format(
                    subject_index, expected_detection_index, returned_detection_index
                )
            )

        for key in ("blink_issue", "hair_issue", "gaze_issue", "smile_issue"):
            value = item.get(key)
            if not isinstance(value, dict):
                raise ValueError(
                    "portrait-QC {} is missing or invalid for subject_index {}".format(key, subject_index)
                )
            subject[key] = value

        seen_indices.add(subject_index)

    missing_indices = sorted(expected_indices - seen_indices)
    if missing_indices:
        raise ValueError(
            "portrait-QC results missing subject_index values: {}".format(missing_indices)
        )

    _apply_default_person_issues(subjects)
    return cleaned


def derive_hair_decisions(analysis):
    """Derive hair_issue.should_fix from physical observations in Python.

    The analyzer classifies state and proposes a candidate operation; it is
    never authoritative for edit approval.  A clean body-driven ``motion``
    state remains locally non-actionable, but the user's later fully-groomed
    option can still transform it because that global hair level is resolved
    independently by the compact editor-prompt builder.
    """
    cleaned = dict(analysis)
    subjects = [dict(subject) for subject in (analysis.get("subjects") or [])]
    cleaned["subjects"] = subjects

    for subject_index, subject in enumerate(subjects):
        if not subject.get("is_person"):
            continue

        issue = dict(subject.get("hair_issue") or {})
        state = issue.get("hair_state") or "uncertain"
        action = _HAIR_ACTION_BY_STATE.get(state)

        # Removing an isolated strand can be more geometrically appropriate
        # than laying it back. Preserve that bounded analyzer choice; all other
        # actionable states use one deterministic operation.
        if state == "strands_across_face" and issue.get("fix_action") == "remove":
            action = "remove"

        # Redistribution is valid only for a positively identified existing
        # fringe. This guard prevents long side hair from becoming new bangs.
        if state == "bangs_clumped" and issue.get("front_hair") != "clumped":
            action = None

        should_fix = action is not None
        plan = str(issue.get("correction_plan") or "").strip()
        if should_fix and (not plan or plan == "不適用"):
            plan = _HAIR_FALLBACK_PLAN_BY_STATE[state]
        elif not should_fix:
            plan = "不適用"

        issue.update({
            "fix_action": action or "none",
            "mess_intent": "ambiguous",
            "should_fix": should_fix,
            "correction_plan": plan,
            "_subject_index": subject_index,
        })
        subject["hair_issue"] = issue

    return cleaned


def derive_blink_decisions(analysis):
    """Turn Call B's recall-first eyelid observations into the legacy
    blink_issue decision shape.

    Physical closure detection is intentionally broader than automatic edit
    approval: blink_candidate records every readable half/full closure, while
    should_fix additionally requires safe two-eye geometry and no strictly
    proven expression/activity exception.
    """
    cleaned = dict(analysis)
    subjects = [dict(subject) for subject in (analysis.get("subjects") or [])]
    cleaned["subjects"] = subjects

    def score(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    closure_states = ("half_closed", "fully_closed")
    candidate_states = ("half_closed", "fully_closed", "one_eye_closed")

    for subject_index, subject in enumerate(subjects):
        if not subject.get("is_person"):
            continue

        issue = dict(subject.get("blink_issue") or {})
        observability = issue.get("blink_observability") or "unreadable"
        detail = issue.get("eyelid_detail_quality") or "unreadable"
        left_state = issue.get("left_eye_state") or "unclear"
        right_state = issue.get("right_eye_state") or "unclear"
        raw_physical = issue.get("physical_eye_state") or "unclear"
        closure_confidence = score(issue.get("eye_closure_confidence"))
        intent_confidence = score(issue.get("intent_evidence_confidence"))

        per_eye = (left_state, right_state)
        readable_per_eye = [state for state in per_eye if state not in ("not_visible", "unclear")]
        two_concrete_per_eye_states = len(readable_per_eye) == 2
        # Luna occasionally uses one_eye to mean "only one iris is visible"
        # even though it simultaneously gives concrete states for both
        # eyelids and summarizes them as one_eye_closed.  That interpretation
        # contradicts the prompt/schema semantics and suppressed obvious
        # fully_closed+narrowed_open repairs.  Reconcile only the strongest
        # internally redundant case; a profile/occluded eye should instead be
        # not_visible/unclear and does not pass this gate.
        reconciled_two_eye_evidence = bool(
            observability == "one_eye"
            and two_concrete_per_eye_states
            and detail == "readable"
            and raw_physical == "one_eye_closed"
            and "fully_closed" in per_eye
            and closure_confidence >= 0.75
        )
        if reconciled_two_eye_evidence:
            observability = "both_eyes"
        both_states_readable = observability == "both_eyes" and two_concrete_per_eye_states

        if observability == "unreadable" or detail == "unreadable" or not readable_per_eye:
            physical = "unclear"
        elif both_states_readable:
            closed_count = sum(state == "fully_closed" for state in readable_per_eye)
            half_count = sum(state == "half_closed" for state in readable_per_eye)
            narrowed_count = sum(state == "narrowed_open" for state in readable_per_eye)
            if closed_count == 2:
                physical = "fully_closed"
            elif closed_count == 1:
                physical = "one_eye_closed"
            elif half_count:
                physical = "half_closed"
            elif narrowed_count:
                physical = "narrowed_open"
            else:
                physical = "open"
        elif any(state in closure_states for state in readable_per_eye):
            # Preserve recall for a physically visible closure even when only
            # one eye can be inspected, but do not automatically edit it.
            physical = raw_physical if raw_physical in candidate_states else "half_closed"
        elif any(state == "narrowed_open" for state in readable_per_eye):
            physical = "narrowed_open"
        elif readable_per_eye and all(state == "open" for state in readable_per_eye):
            physical = "open"
        else:
            physical = "unclear"

        mouth = issue.get("mouth_laughter_state") or "unclear"
        periocular = issue.get("periocular_contraction") or "unclear"
        dependence = issue.get("closure_expression_dependence") or "unclear"
        context = issue.get("explicit_closure_context") or "unclear"

        # narrowed_open is genuinely ambiguous: it's the correct read for a
        # smile-driven squint (which must stay excluded from should_fix), but
        # despite this section's own instruction not to, the model also uses
        # it for a reduced-but-not-fully-closed eyelid with NO smiling
        # explanation at all. Confirmed on eval_results/20260902-luna-round1-
        # demeanor-eyeclose/raw_results.json: of the 400 eye_close positive
        # rows, 67/129 missed rows (52% of all misses) were entirely
        # narrowed_open subjects with mouth_laughter_state=not_laughing and
        # periocular_contraction=none_or_mild -- i.e. nothing about them reads
        # as a smile-squint either. Reclassifying narrowed_open as half_closed
        # specifically in that no-smile signature recovered 91 of those rows
        # in an offline check, with zero new false positives introduced on
        # the 200 negative rows (no negative-image subject shared that exact
        # signature). Left as narrowed_open -- excluded from candidate_states,
        # same as before -- whenever there IS a laughter/smile explanation,
        # preserving the original smile-squint protection this exclusion
        # exists for.
        #
        # Scoped to BOTH eyes independently reading narrowed_open, not just
        # the derived physical=="narrowed_open" summary: an asymmetric
        # left_eye_state="open"/right_eye_state="narrowed_open" pair (one eye
        # fully open, the other only mildly reduced) also derives to
        # physical="narrowed_open", but that pattern is normal facial
        # asymmetry, not a reduced-aperture candidate -- confirmed on a
        # production photo where eye_closure_confidence was only 0.18 and the
        # model's own reason text said the eyes did not reach half/fully
        # closed, yet this reclassification still promoted it to should_fix.
        # Confidence floor added after this same reclassification fired on
        # three more production false positives that all share one signature:
        # both_eyes_narrowed at eye_closure_confidence 0.14-0.18, with the
        # model's own reason text explicitly denying half/full closure (a
        # smiling child's smile-squint the model itself called "因微笑略為
        # 瞇窄但仍保持睜開", and a stern/squinting adult's "眼瞼開口略窄但沒有
        # 達到半閉或全閉") -- the exact asymmetric-case risk this block's own
        # comment above already flagged, just occurring on the symmetric
        # (both_eyes_narrowed=true) side that guard doesn't cover. The one
        # genuine narrowed-eye accidental-closure case checked alongside these
        # (a different subject, model-reported left_eye_state=half_closed
        # directly rather than via this reclassification) carried
        # eye_closure_confidence 0.72 -- comfortably clear of 0.5, so this
        # floor is set to trust the model's own low-confidence signal instead
        # of overriding it, while still promoting genuinely-uncertain-but-not-
        # rock-bottom readings the original fix targeted.
        both_eyes_narrowed = left_state == "narrowed_open" and right_state == "narrowed_open"
        if (
            physical == "narrowed_open"
            and mouth == "not_laughing"
            and periocular == "none_or_mild"
            and both_eyes_narrowed
            and closure_confidence >= 0.5
        ):
            physical = "half_closed"

        blink_candidate = bool(
            physical in candidate_states
            and observability != "unreadable"
            and detail != "unreadable"
        )
        symmetric_closure = bool(
            both_states_readable
            and all(state in closure_states for state in readable_per_eye)
            and len(set(readable_per_eye)) == 1
        )
        strong_laughter = bool(
            blink_candidate
            and symmetric_closure
            and mouth == "active_high_intensity_laughter"
            and periocular == "strong_bilateral"
            and dependence == "integral_to_expression"
            and intent_confidence >= 0.80
        )

        gaze_target = ((subject.get("gaze_issue") or {}).get("gaze_target_in_frame") or {})
        # Unlike strong_laughter/explicit_activity below, a deliberate wink is
        # not required to be "integral_to_expression" -- a wink is routinely a
        # deliberate action layered on top of an otherwise ordinary/neutral
        # expression (closure_expression_dependence legitimately reads
        # independent_or_transitional), not necessarily something the rest of
        # the face's expression depends on. Requiring integral_to_expression
        # here was rejecting genuine winks the model itself classified as
        # explicit_closure_context=deliberate_wink at intent_evidence_confidence
        # ~0.98 -- observed on 4/10 eye_close negative images (505135523,
        # pexels-olly-3777574, pyQoH7AJoRKQ2aYU48F4mR, and a screenshot),
        # each with the exact same signature: context=deliberate_wink,
        # confidence=0.98, dependence=independent_or_transitional.
        # A wink is a one-sided squeeze, so the other eye stays fully open.
        # When that eye is itself narrowed_open or half_closed, both lids are
        # descending -- an uneven blink the model labels deliberate_wink from
        # the asymmetry alone.
        other_eye_open = "open" in readable_per_eye
        deliberate_wink = bool(
            physical == "one_eye_closed"
            and both_states_readable
            and other_eye_open
            and context == "deliberate_wink"
            and intent_confidence >= 0.85
        )
        # 0.90 was rejecting correct context+dependence matches at confidence
        # 0.82-0.86 (savoring/crying_or_pain_or_strong_emotion, observed on 2/10
        # eye_close negative images) purely for falling short of that bar --
        # lowered to 0.80. Checked against the 400 positive eye_close rows in
        # eval_results/20260901-luna-current-eyeclose-v3/raw_results.json: zero
        # currently-should_fix=true subjects have confidence in [0.80, 0.90)
        # with a matching context+dependence, so this does not risk turning any
        # currently-correct accidental-blink detection into a miss.
        # Sleep, like a deliberate wink, is not required to be
        # "integral_to_expression" -- sleep is a state the person is in, not
        # an expression their eyes are "integral to". Requiring that here
        # rejected genuine sleep photos: confirmed on 3 real photos (a subway
        # nap, a child asleep in a car, someone dozing at a desk) where the
        # model consistently read explicit_closure_context=sleep at
        # confidence 0.98-0.99, correctly, but closure_expression_dependence
        # as independent_or_transitional -- a self-consistent reading, just
        # not what this exception demanded. prayer_or_meditation/savoring/
        # crying_or_pain_or_strong_emotion keep the dependence requirement
        # for now (not yet checked against real photos the way sleep was).
        sleep_exception = bool(
            physical in candidate_states
            and context == "sleep"
            and intent_confidence >= 0.80
        )
        explicit_activity = bool(
            physical in candidate_states
            and dependence == "integral_to_expression"
            and intent_confidence >= 0.80
            and (
                context in (
                    "prayer_or_meditation", "savoring",
                    "crying_or_pain_or_strong_emotion",
                )
                or (
                    context == "downward_at_visible_target"
                    and bool(gaze_target.get("present"))
                )
            )
        )
        # Every exception above requires a specific named story (laughter,
        # wink, sleep, ...), but most legitimately-fine closed-eye photos have
        # no such story at all -- explicit_closure_context=none and low
        # intent_evidence_confidence describe a genuine accidental blink
        # exactly as well as they describe an ordinary calm/content closed-eye
        # moment (confirmed empirically: this signature appears on both the
        # 200 eye_close negative rows and the 400 positive rows in
        # eval_results/20260901-luna-current-eyeclose-v3, so gating should_fix
        # on that signature alone is not a safe lever). overall_demeanor is an
        # independent evidence axis -- this person's own posture/tension,
        # nothing to do with why their eyes are closed -- that a genuine
        # accidental blink and an ordinary restful moment do NOT share: someone
        # caught mid-blink while otherwise alert and camera-ready still reads
        # as alert_or_camera_ready, while someone genuinely at ease reads as
        # calm_or_at_ease regardless of whether a specific story applies.
        demeanor = issue.get("overall_demeanor") or "unclear"
        # Scoped to fully_closed only, not every candidate_states member: the
        # 4 false positives this targets (see eval note above) were all
        # fully_closed, and half_closed/one_eye_closed's own droopy/uneven/
        # transitional framing is already a specific accidental-capture
        # signature on its own that a calm demeanor reading shouldn't be
        # allowed to override without separate validation.
        calm_demeanor_exception = bool(
            physical == "fully_closed"
            and context in ("none", "unclear")
            and demeanor == "calm_or_at_ease"
        )
        intentional_exception = (
            strong_laughter or deliberate_wink or explicit_activity
            or calm_demeanor_exception or sleep_exception
        )

        readable_enough = detail == "readable" or (
            detail == "borderline" and closure_confidence >= 0.75
        )
        safe_edit_geometry = observability == "both_eyes" and both_states_readable
        should_fix = bool(
            blink_candidate
            and readable_enough
            and safe_edit_geometry
            and not intentional_exception
        )

        if physical == "unclear":
            eye_state = "occluded_or_unclear"
            severity = "none"
            closure_intent = "ambiguous"
            expression_coherence = "unclear"
            failure_confidence = 0.0
            gate_reason = "Python gate：眼瞼物理狀態不可可靠讀取，不建立閉眼候選。"
        elif physical == "open":
            eye_state = "open"
            severity = "none"
            closure_intent = "ambiguous"
            expression_coherence = "unclear"
            failure_confidence = 0.0
            gate_reason = "Python gate：雙眼物理狀態為張開，不建立閉眼候選。"
        elif physical == "narrowed_open":
            eye_state = "natural_smiling_squint"
            severity = "none"
            closure_intent = "ambiguous"
            expression_coherence = "unclear"
            failure_confidence = 0.0
            gate_reason = "Python gate：眼睛仍有可見開口，屬 narrowed_open，不建立閉眼候選。"
        elif intentional_exception:
            if strong_laughter:
                eye_state = "expressive_closed"
                exception_name = "明顯高強度大笑"
            elif deliberate_wink:
                eye_state = "intentional_wink"
                exception_name = "明確眨單眼"
            elif context == "sleep":
                eye_state = "sleeping"
                exception_name = "明確睡眠"
            elif context == "downward_at_visible_target":
                eye_state = "downward_gaze"
                exception_name = "畫面內明確向下目標"
            elif context == "crying_or_pain_or_strong_emotion":
                eye_state = "expressive_closed"
                exception_name = "明確強烈情緒"
            elif calm_demeanor_exception:
                eye_state = "intentional_closed"
                exception_name = "整體神情放鬆平靜，非清醒準備拍照狀態"
            else:
                eye_state = "intentional_closed"
                exception_name = "明確閉眼活動"
            severity = "none"
            closure_intent = "intentional_expression"
            expression_coherence = "supports_expression"
            failure_confidence = min(0.25, 1.0 - intent_confidence)
            gate_reason = "Python gate：{}的嚴格可見證據通過，因此保留閉眼。".format(exception_name)
        else:
            eye_state = "accidental_blink" if physical == "fully_closed" else "accidental_half_blink"
            severity = "severe" if physical == "fully_closed" else "moderate"
            closure_intent = "accidental_capture" if should_fix else "ambiguous"
            expression_coherence = "conflicts_with_expression" if should_fix else "unclear"
            failure_confidence = max(0.70 if detail == "readable" else 0.55, closure_confidence)
            if should_fix:
                gate_reason = "Python gate：偵測到可讀的閉眼候選，且沒有通過嚴格豁免，should_fix=true。"
            elif observability != "both_eyes":
                gate_reason = "Python gate：保留 blink_candidate=true，但只有單眼幾何可讀，不自動修正。"
            else:
                gate_reason = "Python gate：保留 blink_candidate=true，但細節品質未達自動修正門檻。"

        reason = str(issue.get("reason") or "").strip()
        # Saved analyses can pass this deterministic stage again during a
        # retry. Replace the prior verdict instead of appending a second,
        # potentially contradictory Python-gate sentence.
        reason = re.sub(r"\s*Python gate：[^。]*(?:。|$)", "", reason).strip()
        issue.update({
            "blink_observability": observability,
            "physical_eye_state": physical,
            "eye_closure_confidence": closure_confidence,
            "intent_evidence_confidence": intent_confidence,
            "blink_candidate": blink_candidate,
            "eye_state": eye_state,
            "severity": severity,
            "closure_intent": closure_intent,
            "expression_coherence": expression_coherence,
            "blink_failure_confidence": failure_confidence,
            "should_fix": should_fix,
            "reason": (reason + " " + gate_reason).strip(),
            "blink_repair_strategy": (
                _fallback_blink_repair_strategy(issue, subject.get("label") or "the subject")
                if should_fix else _empty_blink_repair_strategy()
            ),
            "_subject_index": subject_index,
        })
        subject["blink_issue"] = issue

    return cleaned


def _empty_blink_repair_strategy():
    return {
        "target_eye_state": "不適用",
        "eyelid_adjustment": "不適用",
        "repair_strength": "subtle",
        "edit_instruction": "不適用",
        "success_condition": "不適用",
        "preserve_during_repair": [],
        "failure_to_avoid": [],
    }


FOLD_MATCH_RULE = (
    "Treat both eyes as one coupled bilateral structure and establish one shared final lid geometry before rendering "
    "either completed eye. Preserve each canthus and eye-globe identity. Match final opening degree, eye size "
    "and shape, visible iris width and exposure, upper/lower-lid contour, and double-eyelid crease type, path, "
    "peak, visible length, and crease-to-lash width as distinct properties. For a frontal face use the facial midline "
    "as the bilateral comparison axis; for a turned face compare in each eye's local axes and allow only "
    "unavoidable foreshortening."
)


def _fallback_blink_repair_strategy(issue, label):
    """Deterministic pre-Call-C strategy, and Call C's own failure fallback.

    Carries the generic blink-repair wording (reopen/relax naturally, avoid doll-like/oversized/symmetrical eyes,
    match lighting, never convert an intentional wink/laugh/sleep/etc. into
    open eyes) into the structured shape gaze repairs already use, so
    behavior is unchanged the moment this ships, before Call C ever
    authors something better for a given person.
    """
    photograph_left_state = issue.get("left_eye_state")
    photograph_right_state = issue.get("right_eye_state")
    photograph_states = {
        "photograph-left": photograph_left_state,
        "photograph-right": photograph_right_state,
    }
    per_eye_states = tuple(photograph_states.values())
    fully_closed_sides = [
        side for side, state in photograph_states.items() if state == "fully_closed"
    ]
    reference_side = None
    closed_side = None
    if len(fully_closed_sides) == 1:
        candidate_closed_side = fully_closed_sides[0]
        candidate_reference_side = (
            "photograph-right" if candidate_closed_side == "photograph-left" else "photograph-left"
        )
        # A narrowed_open or half_closed opposite eye is itself blink-affected,
        # so its captured aperture is not the endpoint -- both eyes go through
        # the paired branch below and finish open and matched.
        if photograph_states[candidate_reference_side] == "open":
            closed_side = candidate_closed_side
            reference_side = candidate_reference_side
    repair_strength = (
        "strong"
        if "fully_closed" in per_eye_states
        or sum(state == "half_closed" for state in per_eye_states) == 2
        else "moderate"
    )
    if reference_side:
        target_eye_state = (
            f"{label}'s {closed_side} eye is naturally open and matches the {reference_side} eye's finished "
            "aperture, iris exposure, lid contours, and double-eyelid fold geometry."
        )
        eyelid_adjustment = (
            f"Use the {reference_side} eye's finished open geometry as the shared template and fit the "
            f"{closed_side} eyelids to it without changing either eye's fixed corners or horizontal span. "
            "Move the lid margin over the existing globe and resolve compressed lid tissue into a smooth crease "
            "without adding upper-lid mass. " + FOLD_MATCH_RULE
        )
        edit_instruction = (
            f"Render {label}'s two eyes as one coupled pair, using the {reference_side} eye's finished open "
            f"geometry as the template for the repaired {closed_side} eye."
        )
        success_condition = (
            f"At normal viewing size, {label}'s repaired {closed_side} eye and {reference_side} reference eye "
            "read as the same size and share the same lid-contour and double-eyelid fold design."
        )
    else:
        target_eye_state = (
            f"Both of {label}'s eyes are naturally open and read as the same size, with one shared lid-contour "
            "and double-eyelid fold design."
        )
        eyelid_adjustment = (
            "Raise both upper-lid margins to one shared naturally-open target; use source pixels only for identity "
            "cues, never as final opening-height references. Resolve compressed lid tissue into smooth creases "
            "without adding upper-lid mass. " + FOLD_MATCH_RULE
        )
        edit_instruction = (
            f"Render both of {label}'s eyes together as one coupled bilateral structure at a shared naturally-open "
            "eyelid endpoint."
        )
        success_condition = (
            f"At normal viewing size, both of {label}'s eyes read as the same size and share the same lid-contour "
            "and double-eyelid fold design without puffiness."
        )
    return {
        "target_eye_state": target_eye_state,
        "eyelid_adjustment": eyelid_adjustment,
        "repair_strength": repair_strength,
        "edit_instruction": edit_instruction,
        "success_condition": success_condition,
        "preserve_during_repair": [
            "identity", "gaze direction unless a separately approved gaze repair says otherwise",
            "facial structure", "expression style", "skin texture", "glasses", "makeup", "lighting",
            "unlisted people",
        ],
        "failure_to_avoid": [
            "residual unequal eyelid openness from the captured blink", "unequal visible double-eyelid band width",
            "oversized or doll-like eyes", "extra or discontinuous eyelid folds",
            "puffy or swollen upper eyelid", "increased eyelid tissue volume", "high or overly deep crease",
            "upper-lid tissue bunched upward toward the brow", "retained closed-lid mound above the opened eye",
            "exactly mirrored or cloned eye geometry",
            "reshaped face or smoothed skin",
            "converting an intentional wink, laugh-squint, crying/pained/dramatic closed-eye "
            "reaction, sleeping/napping closed eyes, or deliberate closed-eye expression into "
            "neutral open eyes",
            "changes to unlisted people",
        ],
    }


def _empty_gaze_repair_strategy():
    return {
        "strategy_type": "not_safe_to_repair",
        "original_gaze_read": "不適用",
        "target_gaze_read": "不適用",
        "eye_adjustment": "不適用",
        "sclera_catchlight_adjustment": "不適用",
        "head_face_adjustment": "none",
        "repair_strength": "subtle",
        "edit_instruction": "不適用",
        "success_condition": "不適用",
        "preserve_during_repair": [],
        "failure_to_avoid": [],
    }


def _fallback_gaze_repair_strategy(issue, label):
    gaze_state = issue.get("gaze_state") or "near_camera"
    head_pose = issue.get("head_pose") or "unclear"
    binocular = issue.get("binocular_alignment") or "unverifiable"
    visible_directions = [
        direction for direction in (
            issue.get("left_eye_direction"), issue.get("right_eye_direction")
        )
        if direction and direction != "not_visible"
    ]
    near_lens_directions = ("near_lens_left", "near_lens_right")
    try:
        original_lens_confidence = float(issue.get("lens_contact_confidence", 0.0))
    except (TypeError, ValueError):
        original_lens_confidence = 0.0
    tiny_frontal_miss = bool(
        gaze_state == "near_camera"
        and head_pose == "frontal"
        and len(visible_directions) == 2
        and all(direction in near_lens_directions for direction in visible_directions)
        and len(set(visible_directions)) == 1
        and binocular == "near_lens_convergent"
        and original_lens_confidence >= 0.70
    )
    eyes_only = tiny_frontal_miss
    strategy_type = "eyes_only" if eyes_only else "eyes_plus_head_face_alignment"
    head_adjustment = (
        "none; the head and face are already naturally aligned to the current lens"
        if eyes_only else
        "visibly but naturally align the head/face toward the current camera lens with bounded yaw and/or pitch; keep the torso, shoulders, and body pose unchanged"
    )
    return {
        "strategy_type": strategy_type,
        "original_gaze_read": issue.get("reason") or "The readable eyes miss the exact current lens.",
        "target_gaze_read": "Both pupils/irises aim at the exact current camera lens; the viewer reads unmistakable direct eye contact.",
        "eye_adjustment": "Move both pupils/irises from the observed gaze direction to the exact current camera-lens point.",
        "sclera_catchlight_adjustment": "Rebalance sclera naturally around the corrected iris positions and keep catchlights consistent with the original lighting.",
        "head_face_adjustment": head_adjustment,
        "repair_strength": "moderate",
        "edit_instruction": (
            "Redirect {}'s readable eyes to the exact current camera lens using {}. "
            "Make the completed correction immediately readable at normal photo-viewing size. "
            "Do not stop at near-camera gaze; preserve identity, eyelid openness, eye shape, expression, glasses, lighting, torso, shoulders, and body pose."
        ).format(label, "pupil/iris repositioning only because the frontal miss is extremely small" if eyes_only else "pupil/iris repositioning plus a visible, bounded head/face alignment toward the lens"),
        "success_condition": "At normal viewing size, the subject immediately and unmistakably looks into the exact current camera lens, not beside, above, below, or past it.",
        "preserve_during_repair": [
            "identity", "eyelid openness", "eye shape and color", "expression", "glasses",
            "skin texture", "body pose", "clothing", "background", "unlisted people",
        ],
        "failure_to_avoid": [
            "near-camera only", "correction visible only in a highly enlarged crop",
            "pupil shift without compatible head/face alignment", "cross-eyed or strained gaze",
            "excessive sclera", "changed eyelid shape", "changed identity",
            "rotated shoulders or torso", "changes to unlisted people",
        ],
    }


def derive_gaze_decisions(analysis):
    """Convert Call B's observation-only gaze result into the legacy
    downstream gaze_issue shape.  The strict positive gate and should_fix
    threshold live here so prompt wording cannot silently change them.
    """
    cleaned = dict(analysis)
    subjects = [dict(subject) for subject in (analysis.get("subjects") or [])]
    cleaned["subjects"] = subjects

    for subject_index, subject in enumerate(subjects):
        if not subject.get("is_person"):
            continue
        issue = dict(subject.get("gaze_issue") or {})
        observability = issue.get("gaze_observability") or "unreadable"
        detail = issue.get("eye_detail_quality") or "unreadable"
        state = issue.get("gaze_state") or "unclear"
        head_pose = issue.get("head_pose") or "unclear"
        left_direction = issue.get("left_eye_direction") or "unclear"
        right_direction = issue.get("right_eye_direction") or "unclear"
        binocular = issue.get("binocular_alignment") or "unverifiable"
        target = issue.get("gaze_target_in_frame") or {
            "present": False, "description": "不適用",
            "box": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
        }
        try:
            lens_confidence = max(0.0, min(1.0, float(issue.get("lens_contact_confidence", 0.0))))
        except (TypeError, ValueError):
            lens_confidence = 0.0
        try:
            miss_confidence = max(0.0, min(1.0, float(issue.get("not_looking_at_lens_confidence", 0.0))))
        except (TypeError, ValueError):
            miss_confidence = 0.0

        # Cross-check against derive_blink_decisions, which runs first
        # (see openai_client.py's two-pass call order) and already wrote the
        # deterministically-derived eyelid state onto this same subject's
        # blink_issue. The hard gaze rule requires
        # should_fix=false whenever the eyes are closed/half-closed enough
        # that gaze isn't readable, but until this check existed that rule
        # depended entirely on the model itself reporting unreadable/
        # not_visible eye directions in this same Call B response -- observed
        # in production eval to fail routinely: the model would correctly
        # call blink_issue.physical_eye_state="fully_closed" while still
        # reporting a readable, on-lens gaze direction for the same person.
        # Gating on the already-computed physical_eye_state removes that
        # dependency on the model policing itself. one_eye_closed is included
        # for the same reason the prompt's own half-face rule refuses to
        # judge gaze from a single visible eye.
        # Narrowed after a follow-up eye_contact-only production eval (320
        # calls, 16 photos) showed this gate firing 46 times, 45 of them on
        # half_closed (never once on fully_closed -- these photos generally
        # have open eyes, just not aimed at the lens) and one on
        # one_eye_closed. Of those 45 half_closed triggers, 24 (53%) landed on
        # a subject whose gaze_issue fields, from that exact same Call B
        # response, independently read gaze_observability="both_eyes" and
        # eye_detail_quality="readable" -- i.e. the gaze pass itself had
        # direct, high-quality evidence the eyes were open enough to read,
        # contradicting blink's own half_closed call for the same eyes in the
        # same response. Concretely, on sample_images/eye_contact/000000594.jpg,
        # a photo whose gaze reason text opens with "雙眼虹膜與瞳孔細節可讀"
        # (both irises/pupils are legibly detailed), the gate was still
        # suppressing should_fix=true on blink's half_closed call alone,
        # turning a previously-correct prediction into a miss. fully_closed
        # keeps the unconditional gate -- gaze claiming "both eyes, readable"
        # against a fully_closed call is a far less physically plausible
        # disagreement than against a half_closed one, and no such case
        # showed up in this eval to weigh against keeping it strict.
        gaze_independently_readable = observability == "both_eyes" and detail == "readable"
        blink_state = (subject.get("blink_issue") or {}).get("physical_eye_state")
        eyes_closed = blink_state == "fully_closed" or (
            blink_state in ("half_closed", "one_eye_closed") and not gaze_independently_readable
        )

        visible_directions = [
            direction for direction in (left_direction, right_direction)
            if direction != "not_visible"
        ]
        if eyes_closed:
            decision = "unclear"
            state = "unclear"
            should_fix = False
            miss_confidence = min(miss_confidence, 0.49)
            gate_reason = (
                "Python gate：blink_issue.physical_eye_state={}，眼睛閉合/半閉合導致視線不可讀，"
                "依 hard gaze rule 不觸發視線修正。"
            ).format(blink_state)
        elif observability == "unreadable" or detail == "unreadable" or not visible_directions:
            decision = "unclear"
            state = "unclear"
            should_fix = False
            miss_confidence = min(miss_confidence, 0.49)
            gate_reason = "Python gate：眼部方向不可可靠讀取，因此不觸發視線修正。"
        else:
            exact_eye_evidence = all(direction == "toward_exact_lens" for direction in visible_directions)
            expected_visible_count = 2 if observability == "both_eyes" else 1
            exact_eye_evidence = exact_eye_evidence and len(visible_directions) == expected_visible_count
            exact_binocular_evidence = (
                binocular == "exact_lens_convergent"
                if observability == "both_eyes"
                else binocular == "unverifiable"
            )
            exact_positive = (
                issue.get("lens_contact_decision") == "looking_at_lens"
                and state == "direct_camera"
                and lens_confidence >= 0.80
                and exact_eye_evidence
                and exact_binocular_evidence
            )
            if exact_positive:
                decision = "looking_at_lens"
                miss_confidence = min(miss_confidence, 0.25, 1.0 - lens_confidence)
                should_fix = False
                gate_reason = "Python gate：雙眼物理證據通過 exact-lens positive gate。"
            else:
                decision = "not_looking_at_lens"
                if state in ("direct_camera", "ambiguous", "unclear"):
                    state = "near_camera"
                miss_floor = 0.75 if state not in ("near_camera", "ambiguous") else 0.55
                miss_confidence = max(miss_confidence, miss_floor, 1.0 - lens_confidence)
                lens_confidence = min(lens_confidence, 0.79, 1.0 - miss_confidence)
                # The borderline-detail bar used to be a flat 0.75 regardless
                # of state, but miss_confidence is itself floored per state
                # just above (0.55 for near_camera/ambiguous, 0.75 for
                # everything else) -- so a flat 0.75 bar was a no-op grace
                # margin for off_camera-like states (their floor already
                # clears it) while being an almost-unreachable ceiling for
                # near_camera, whose floor sits 0.20 below it and rarely rises
                # much past it organically. Confirmed in production eval: a
                # single-subject near-camera photo with a stable ground-truth
                # gaze-miss went should_fix=true only on the ~1/8 of runs
                # where eye_focus_quality happened to read "readable" instead
                # of "borderline" for the exact same face, purely on that
                # borderline/readable coin flip, not on any change in the
                # gaze evidence itself (its miss_confidence spanned 0.55-0.75
                # on both readable and borderline runs alike). Scaling the
                # bar off each state's own floor keeps the intended "extra
                # assurance beyond the state's own bar" margin (now +0.10)
                # without that state-dependent no-op/wall split.
                readable_enough = detail == "readable" or (
                    detail == "borderline" and miss_confidence >= miss_floor + 0.10
                )
                edit_geometry_available = (
                    observability == "both_eyes"
                    and head_pose not in ("profile", "rear_or_hidden", "unclear")
                )
                should_fix = bool(
                    miss_confidence >= 0.55
                    and readable_enough
                    and edit_geometry_available
                    and not bool(target.get("present"))
                )
                gate_reason = (
                    "Python gate：未通過 exact-lens positive gate；依可讀性、雙眼可見性、"
                    "信心門檻與可見目標規則推導 should_fix={}。"
                ).format(str(should_fix).lower())

        reason = str(issue.get("reason") or "").strip()
        issue.update({
            "gaze_state": state,
            "eye_focus_quality": detail,
            "gaze_target_in_frame": target,
            "lens_contact_decision": decision,
            "lens_contact_confidence": lens_confidence,
            "not_looking_at_lens_confidence": miss_confidence,
            "gaze_correction_reasonableness": (
                0.90 if head_pose == "frontal" else
                0.75 if head_pose == "slight_yaw" else
                0.55 if head_pose == "strong_yaw" else 0.20
            ),
            "gaze_repair_strategy": (
                _fallback_gaze_repair_strategy(issue, subject.get("label") or "the subject")
                if should_fix else _empty_gaze_repair_strategy()
            ),
            "should_fix": should_fix,
            "reason": (reason + " " + gate_reason).strip(),
            "_subject_index": subject_index,
        })
        subject["gaze_issue"] = issue
    return cleaned


def eye_repair_targets(analysis):
    """Union of every person whose blink and/or gaze gate approved a fix --
    the shared gate for Call C, which now plans both topics per person
    instead of gaze alone.
    """
    targets = []
    for subject_index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        gaze_issue = subject.get("gaze_issue") or {}
        blink_issue = subject.get("blink_issue") or {}
        needs_gaze = bool(gaze_issue.get("should_fix"))
        needs_blink = bool(blink_issue.get("should_fix"))
        if not (needs_gaze or needs_blink):
            continue
        targets.append({
            "subject_index": subject_index,
            "local_detection_index": subject.get("local_detection_index", -1),
            "label": subject.get("label") or "person",
            "box": subject.get("box") or {},
            "needs_gaze_repair": needs_gaze,
            "needs_blink_repair": needs_blink,
            "gaze_issue": gaze_issue if needs_gaze else None,
            "blink_issue": blink_issue if needs_blink else None,
        })
    return targets


def merge_eye_repair_strategies(analysis, repair_response):
    cleaned = dict(analysis)
    subjects = [dict(subject) for subject in (analysis.get("subjects") or [])]
    cleaned["subjects"] = subjects
    targets_by_index = {target["subject_index"]: target for target in eye_repair_targets(analysis)}
    expected = set(targets_by_index)
    seen = set()

    for item in (repair_response or {}).get("repair_plans") or []:
        subject_index = item.get("subject_index")
        if subject_index not in expected or subject_index in seen:
            raise ValueError("invalid or duplicate eye repair subject_index {}".format(subject_index))
        target = targets_by_index[subject_index]
        subject = subjects[subject_index]
        expected_detection = subject.get("local_detection_index", -1)
        if item.get("local_detection_index", -1) != expected_detection:
            raise ValueError("eye repair local_detection_index mismatch for subject_index {}".format(subject_index))

        if target["needs_gaze_repair"]:
            gaze_strategy = item.get("gaze_repair_strategy")
            if not isinstance(gaze_strategy, dict):
                raise ValueError("missing gaze_repair_strategy for subject_index {}".format(subject_index))
            gaze_issue = dict(subject.get("gaze_issue") or {})
            gaze_issue["gaze_repair_strategy"] = gaze_strategy
            if gaze_strategy.get("strategy_type") == "not_safe_to_repair":
                gaze_issue["should_fix"] = False
                gaze_issue["reason"] = (str(gaze_issue.get("reason") or "") + " Repair planner：影像或幾何不足以安全修正。").strip()
            subject["gaze_issue"] = gaze_issue

        if target["needs_blink_repair"]:
            blink_strategy = item.get("blink_repair_strategy")
            if not isinstance(blink_strategy, dict):
                raise ValueError("missing blink_repair_strategy for subject_index {}".format(subject_index))
            blink_issue = dict(subject.get("blink_issue") or {})
            blink_issue["blink_repair_strategy"] = blink_strategy
            subject["blink_issue"] = blink_issue

        seen.add(subject_index)

    missing = sorted(expected - seen)
    if missing:
        raise ValueError("eye repair plans missing subject_index values: {}".format(missing))
    return cleaned
