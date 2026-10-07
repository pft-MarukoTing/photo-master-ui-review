"""The portrait analysis skill: runs Call B, the Body Reshape pass and Call C in order."""

import logging

from prompts import body_reshape_enabled, strip_body_reshape

from ..base import AnalysisSkill
from .body import _run_body_reshape_pass_or_fallback, build_body_reshape_planning_instruction
from .decisions import (
    _apply_default_person_issues,
    derive_blink_decisions,
    derive_gaze_decisions,
    derive_hair_decisions,
    eye_repair_targets,
    merge_eye_repair_strategies,
    merge_portrait_qc_issues,
)
from .instructions import (
    build_eye_repair_planning_instruction,
    build_eye_repair_planning_response_schema,
    build_portrait_qc_instruction,
    build_portrait_qc_response_schema,
    portrait_qc_persons,
)

logger = logging.getLogger(__name__)


class PortraitAnalysisSkill(AnalysisSkill):
    """Per-person blink/hair/gaze/smile review for the people Call A identified.

    Call B observes the four states only for Call A's is_person subjects and is
    merged back by merge_portrait_qc_issues's stable subject_index (never
    by response-array position). Python then applies the exact-lens gate, and an
    optional Call C creates repair strategies only for people that passed it.
    When ENABLE_BODY_RESHAPE is enabled, one body-analysis pass stores a
    per-person ``edit_prompt`` under each matching ``subjects[]`` entry; when it
    is disabled, that call is skipped and stale body data is removed.

    Call B (including a validation failure raised by merge_portrait_qc_issues
    itself) is best-effort: on failure every person keeps
    _default_person_issues' "no portrait defects detected" reading and
    Call A's own result is still returned.
    """

    name = "portrait"
    description = (
        "Judges each person's closed eyes, gaze contact, messy hair and smile, plans the eye repairs, "
        "and plans Body Reshape when that feature is on."
    )
    produces = frozenset({"blink_issue", "gaze_issue", "hair_issue", "smile_issue", "body_reshape"})

    def applies_to(self, analysis):
        return bool(portrait_qc_persons(analysis))

    def run(self, analysis, ctx):
        persons = portrait_qc_persons(analysis)
        usages = []

        merged = None
        try:
            data_b = ctx.request(
                build_portrait_qc_instruction(persons),
                build_portrait_qc_response_schema(len(persons)),
                "Photo analysis failed (portrait-QC pass)",
            )
            portrait_qc = ctx.parse(data_b)
            merged = merge_portrait_qc_issues(analysis, portrait_qc)
            merged = derive_hair_decisions(merged)
            merged = derive_blink_decisions(merged)
            merged = derive_gaze_decisions(merged)
            usages.append(data_b.get("usage"))
        except Exception as exc:
            logger.warning(
                "Portrait-QC pass (two-pass split) failed, falling back to "
                "\"no portrait defects detected\" for this analysis: %s", exc,
            )
        if merged is None:
            _apply_default_person_issues(analysis.get("subjects"))
            merged = analysis
        analysis = merged

        if body_reshape_enabled():
            analysis, usage_body = _run_body_reshape_pass_or_fallback(analysis, persons, ctx)
            usages.append(usage_body)
        else:
            analysis = strip_body_reshape(analysis)

        repair_targets = eye_repair_targets(analysis)
        if repair_targets:
            try:
                data_c = ctx.request(
                    build_eye_repair_planning_instruction(repair_targets),
                    build_eye_repair_planning_response_schema(len(repair_targets)),
                    "Photo analysis failed (eye repair-planning pass)",
                )
                repair_response = ctx.parse(data_c)
                analysis = merge_eye_repair_strategies(analysis, repair_response)
                usages.append(data_c.get("usage"))
            except Exception as exc:
                logger.warning(
                    "Eye repair-planning pass failed; keeping deterministic fallback strategies: %s", exc,
                )
        return analysis, usages

    def skip(self, analysis):
        # Every subject still needs the four issue fields, in the "不適用" shape.
        _apply_default_person_issues(analysis.get("subjects"))
        if not body_reshape_enabled():
            analysis = strip_body_reshape(analysis)
        return analysis

    def prompt_records(self, analysis):
        # Rebuilt from the finished analysis; the merges never reorder or filter subjects[].
        # TODO: Call C's record drops a target whose gaze plan came back not_safe_to_repair.
        persons = portrait_qc_persons(analysis)
        repair_targets = eye_repair_targets(analysis)
        return {
            "photo_analysis_portrait_qc": build_portrait_qc_instruction(persons) if persons else None,
            "photo_analysis_eye_repair_planning": (
                build_eye_repair_planning_instruction(repair_targets)
                if repair_targets else None
            ),
            "photo_analysis_body_reshape": (
                build_body_reshape_planning_instruction(persons)
                if persons and body_reshape_enabled() else None
            ),
        }
