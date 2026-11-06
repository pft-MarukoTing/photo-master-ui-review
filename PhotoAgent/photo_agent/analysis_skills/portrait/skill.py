"""The portrait analysis skill: runs Call B, Call C and then the Body Reshape pass.

Call B needs only the people, so with a Stage 2.0 fixed roster it starts
alongside Call A (run_early). Its observations are moved onto Call A's people
(adopt_early), and only then do the Python decisions and Call C run, so every
label and box they write is Call A's."""

import logging

from prompts import body_reshape_enabled, strip_body_reshape

from ..base import AnalysisSkill
from .body import _run_body_reshape_pass_or_fallback, build_body_reshape_planning_instruction
from .decisions import (
    _apply_default_person_issues,
    derive_blink_decisions,
    derive_gaze_decisions,
    derive_hair_decisions,
    derive_lens_decisions,
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

# Where the people passes leave the Call B / Call C instructions they sent, for prompt_records.
SENT_PROMPTS_KEY = "_portrait_prompts_sent"
# On run_early's roster: whether Call B's observations were merged (False: it failed or had nobody).
QC_OK_KEY = "_portrait_qc_ok"


def _sent_prompts(portrait_qc):
    return {"photo_analysis_portrait_qc": portrait_qc, "photo_analysis_eye_repair_planning": None}


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
        "Judges each person's closed eyes, gaze contact, messy hair, smile and wide-angle face stretch, plans the eye repairs, "
        "and plans Body Reshape when that feature is on."
    )
    produces = frozenset({"blink_issue", "gaze_issue", "hair_issue", "smile_issue", "lens_issue", "body_reshape"})

    def applies_to(self, analysis):
        return bool(portrait_qc_persons(analysis))

    # The fields Call B writes on each person (Call C and the Python decisions refine them).
    PEOPLE_FIELDS = ("blink_issue", "gaze_issue", "hair_issue", "smile_issue", "lens_issue")

    def run(self, analysis, ctx):
        # The body pass is given the people as Call A wrote them, before Call B's fields.
        persons = portrait_qc_persons(analysis)
        merged, usages, sent = self._request_portrait_qc(analysis, ctx)
        analysis, usages = self._finish_people_passes(analysis, merged, ctx, usages, sent)
        return self._run_body_pass(analysis, persons, ctx, usages)

    def run_early(self, roster, ctx):
        # Call B reads only each person's label, box and face, which Stage 2.0's
        # fixed roster already has, so it can run alongside Call A. Everything that
        # writes a label or a box into the result -- the Python decisions, the lens
        # correction item and Call C -- waits for Call A in adopt_early.
        # The Body Reshape pass needs Call A's body boxes; with it on, the whole skill waits.
        if body_reshape_enabled():
            return None
        if not portrait_qc_persons(roster):
            # No readable face: nobody to review, now or after Call A, and nothing sent.
            return {**roster, QC_OK_KEY: False, SENT_PROMPTS_KEY: _sent_prompts(None)}, []
        merged, usages, sent = self._request_portrait_qc(roster, ctx)
        if merged is None:
            return {**roster, QC_OK_KEY: False, SENT_PROMPTS_KEY: sent}, usages
        return {**merged, QC_OK_KEY: True, SENT_PROMPTS_KEY: sent}, usages

    def adopt_early(self, analysis, early, ctx):
        roster, usages = early
        early_subjects = roster.get("subjects") or []
        subjects = [dict(subject) for subject in (analysis.get("subjects") or [])]
        for index, subject in enumerate(subjects):
            if not isinstance(subject, dict) or not subject.get("is_person"):
                continue
            source = early_subjects[index] if index < len(early_subjects) else None
            # Call A keeps Stage 2.0's people first and in order; anything else means the roster moved.
            if (not isinstance(source, dict)
                    or source.get("preview_person_id") != subject.get("preview_person_id")
                    or source.get("local_detection_index") != subject.get("local_detection_index")):
                return None
            # A face Stage 2.0 judged unreadable was not reviewed early. Such a face is a
            # borderline case, so it keeps the no-repair defaults instead of a second review.
            if not source.get("is_person"):
                continue
            for key in self.PEOPLE_FIELDS:
                if key in source:
                    subject[key] = source[key]
        analysis = {**analysis, "subjects": subjects}
        _apply_default_person_issues(analysis["subjects"])
        # Call B's observations are now on Call A's people, so from here on every
        # label and box is Call A's, exactly as when the skill runs after Call A.
        merged = analysis if roster.get(QC_OK_KEY) else None
        analysis, usages = self._finish_people_passes(
            analysis, merged, ctx, list(usages), dict(roster.get(SENT_PROMPTS_KEY) or _sent_prompts(None)))
        return strip_body_reshape(analysis), usages

    def _request_portrait_qc(self, analysis, ctx):
        """Call B alone: (analysis with Call B's observations merged, or None on
        failure; usages; the instructions sent)."""
        persons = portrait_qc_persons(analysis)
        # The instruction as sent: on the early path it carries Stage 2.0's labels
        # and boxes, which Call A's finished analysis no longer has to rebuild it from.
        sent = _sent_prompts(build_portrait_qc_instruction(persons))
        usages = []
        try:
            data_b = ctx.request(
                sent["photo_analysis_portrait_qc"],
                build_portrait_qc_response_schema(len(persons)),
                "Photo analysis failed (portrait-QC pass)",
            )
            usages.append(data_b.get("usage"))
            return merge_portrait_qc_issues(analysis, ctx.parse(data_b)), usages, sent
        except Exception as exc:
            logger.warning(
                "Portrait-QC pass (two-pass split) failed, falling back to "
                "\"no portrait defects detected\" for this analysis: %s", exc,
            )
            return None, usages, sent

    def _finish_people_passes(self, analysis, merged, ctx, usages, sent):
        """The Python decisions on Call B's observations, then Call C (eye repair planning).

        merged is analysis with Call B's observations, or None when Call B failed."""
        if merged is not None:
            try:
                merged = derive_hair_decisions(merged)
                merged = derive_blink_decisions(merged)
                merged = derive_gaze_decisions(merged)
                merged = derive_lens_decisions(merged)
            except Exception as exc:
                logger.warning(
                    "Portrait-QC decisions failed, falling back to "
                    "\"no portrait defects detected\" for this analysis: %s", exc,
                )
                merged = None
        if merged is None:
            _apply_default_person_issues(analysis.get("subjects"))
            merged = analysis
        analysis = merged

        repair_targets = eye_repair_targets(analysis)
        if repair_targets:
            sent["photo_analysis_eye_repair_planning"] = build_eye_repair_planning_instruction(repair_targets)
            try:
                data_c = ctx.request(
                    sent["photo_analysis_eye_repair_planning"],
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
        return {**analysis, SENT_PROMPTS_KEY: sent}, usages

    def _run_body_pass(self, analysis, persons, ctx, usages):
        """The Body Reshape pass needs Call A's own body boxes, so it always runs after Call A."""
        if body_reshape_enabled():
            analysis, usage_body = _run_body_reshape_pass_or_fallback(analysis, persons, ctx)
            usages.append(usage_body)
        else:
            analysis = strip_body_reshape(analysis)
        return analysis, usages

    def skip(self, analysis):
        # Every subject still needs the four issue fields, in the "不適用" shape.
        _apply_default_person_issues(analysis.get("subjects"))
        if not body_reshape_enabled():
            analysis = strip_body_reshape(analysis)
        return analysis

    def prompt_records(self, analysis):
        # Call B and Call C as actually sent; popped so the copy stays out of the stored analysis.
        sent = analysis.pop(SENT_PROMPTS_KEY, None)
        persons = portrait_qc_persons(analysis)
        if sent is not None:
            return {**sent, "photo_analysis_body_reshape": (
                build_body_reshape_planning_instruction(persons)
                if persons and body_reshape_enabled() else None)}
        # No people pass ran (skipped skill): rebuilt from the finished analysis.
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
