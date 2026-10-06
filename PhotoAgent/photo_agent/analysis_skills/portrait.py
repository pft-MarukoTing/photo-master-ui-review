"""Portrait analysis skill: Call B (portrait QC), Body Reshape and Call C (eye repair planning)."""

import logging
import traceback
from datetime import datetime, timezone

from photo_agent.utils import bounded_debug_value
from prompts import (
    _apply_default_person_issues,
    body_reshape_enabled,
    build_body_reshape_planning_instruction,
    build_body_reshape_planning_response_schema,
    build_eye_repair_planning_instruction,
    build_eye_repair_planning_response_schema,
    build_portrait_qc_instruction,
    build_portrait_qc_response_schema,
    derive_blink_decisions,
    derive_gaze_decisions,
    derive_hair_decisions,
    eye_repair_targets,
    merge_body_reshape_edit_prompt,
    merge_body_reshape_plans_partial,
    merge_eye_repair_strategies,
    merge_portrait_qc_issues,
    portrait_qc_persons,
    strip_body_reshape,
)

from .base import AnalysisSkill

logger = logging.getLogger(__name__)


class PortraitAnalysisSkill(AnalysisSkill):
    """Per-person blink/hair/gaze/smile review for the people Call A identified.

    Call B observes the four states only for Call A's is_person subjects and is
    merged back by prompts.merge_portrait_qc_issues's stable subject_index (never
    by response-array position). Python then applies the exact-lens gate, and an
    optional Call C creates repair strategies only for people that passed it.
    When ENABLE_BODY_RESHAPE is enabled, one body-analysis pass stores a
    per-person ``edit_prompt`` under each matching ``subjects[]`` entry; when it
    is disabled, that call is skipped and stale body data is removed.

    Call B (including a validation failure raised by merge_portrait_qc_issues
    itself) is best-effort: on failure every person keeps
    prompts._default_person_issues' "no portrait defects detected" reading and
    Call A's own result is still returned.
    """

    name = "portrait"

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


class _BodyReshapeResponse(dict):
    """Parsed planner response carrying the raw Responses API envelope."""

    def __init__(self, value, *, raw_response=None, usage=None, prompt=""):
        super().__init__(value or {})
        self.raw_response = bounded_debug_value(raw_response)
        self.usage = usage
        self.prompt = prompt


def _analyze_body_reshape_pass(persons, ctx):
    """Run the dedicated per-person Body Reshape analyzer.

    Keep the planner prompt and raw Responses envelope attached to the parsed
    value. If JSON parsing fails, the exception itself receives the same
    context so the caller can persist a useful diagnostic artifact.
    """
    body_prompt = ""
    data = None
    try:
        body_prompt = build_body_reshape_planning_instruction(persons)
        data = ctx.request(
            body_prompt,
            # Strictly require one returned plan for every supplied person.
            # The subject/local-detection indices inside each plan are then
            # validated by merge_body_reshape_plans.
            build_body_reshape_planning_response_schema(len(persons)),
            "Photo analysis failed (body reshape pass)",
            with_face_crops=False,
        )
        response = ctx.parse(data)
        return _BodyReshapeResponse(
            response,
            raw_response=data,
            usage=data.get("usage") if isinstance(data, dict) else None,
            prompt=body_prompt,
        ), data.get("usage") if isinstance(data, dict) else None
    except Exception as exc:
        # Do not overwrite API context attached by _post_json. A parse/build
        # failure gets its own explicit kind for downstream diagnosis.
        if not getattr(exc, "body_reshape_error_kind", None):
            exc.body_reshape_error_kind = "response_parse"
        exc.body_reshape_prompt = body_prompt
        if data is not None and not getattr(exc, "body_reshape_raw_response", None):
            exc.body_reshape_raw_response = bounded_debug_value(data)
            if isinstance(data, dict):
                exc.body_reshape_usage = data.get("usage")
        raise


def _body_reshape_error_record(exc, persons, *, prompt="", raw_response=None, usage=None):
    """Build the persisted diagnostic for a failed planner/merge call."""
    people = []
    for position, person in enumerate(persons or []):
        if not isinstance(person, dict):
            continue
        people.append({
            "position": position,
            "subject_index": person.get("subject_index", person.get("_subject_index", position)),
            "local_detection_index": person.get("local_detection_index", -1),
            "label": person.get("label") or "person",
        })
    if raw_response is None:
        raw_response = getattr(exc, "body_reshape_raw_response", None)
    if usage is None:
        usage = getattr(exc, "body_reshape_usage", None)
    record = {
        "record_type": "body_reshape_api_error",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "stage": "analysis.body_reshape",
        "error_kind": getattr(exc, "body_reshape_error_kind", None) or "response_validation",
        "exception": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
        "context": {
            "person_count": len(people),
            "persons": people,
            "fallback_applied": True,
        },
    }
    for key, attr in (
        ("api_label", "body_reshape_api_label"),
        ("endpoint", "body_reshape_endpoint"),
        ("model", "body_reshape_model"),
        ("http_status", "body_reshape_http_status"),
    ):
        value = getattr(exc, attr, None)
        if value is not None:
            record["context"][key] = value
    if prompt:
        record["planner_prompt"] = bounded_debug_value(prompt)
    if raw_response is not None:
        record["raw_response"] = bounded_debug_value(raw_response)
    if usage:
        record["usage"] = bounded_debug_value(usage)
    return record


def _body_reshape_failure_fallback(analysis, persons, error_record):
    """Keep one visible, non-executable error plan for every person."""
    fallback = dict(analysis or {})
    subjects = [
        dict(subject) if isinstance(subject, dict) else subject
        for subject in (fallback.get("subjects") or [])
    ]
    fallback["subjects"] = subjects
    for position, person in enumerate(persons or []):
        if not isinstance(person, dict):
            continue
        subject_index = person.get("subject_index", person.get("_subject_index", position))
        if not isinstance(subject_index, int) or not (0 <= subject_index < len(subjects)):
            continue
        subject = subjects[subject_index]
        if not isinstance(subject, dict) or subject.get("is_person") is not True:
            continue
        label = " ".join(str(subject.get("label") or person.get("label") or "此人物").split())
        subject["body_reshape"] = {
            "subject_index": subject_index,
            "local_detection_index": person.get(
                "local_detection_index", subject.get("local_detection_index", -1)
            ),
            "should_edit": False,
            "analysis_status": "error",
            "report": (
                f"{label}本次未取得足夠可靠的身形判讀，為保留畫面比例，"
                "先維持目前輪廓；請重新分析後再試。"
            ),
        }
    fallback["_body_reshape_status"] = "error"
    fallback["_body_reshape_error"] = error_record
    return fallback


def _run_body_reshape_pass_or_fallback(analysis, persons, ctx):
    """Run Body Reshape without hiding a failed batch behind empty subjects."""
    body_response = None
    usage_body = None
    try:
        body_response, usage_body = _analyze_body_reshape_pass(persons, ctx)
        return merge_body_reshape_edit_prompt(analysis, body_response), usage_body
    except Exception as exc:
        if body_response is not None:
            prompt = getattr(body_response, "prompt", "") or ""
            raw_response = getattr(body_response, "raw_response", None)
            usage_body = usage_body or getattr(body_response, "usage", None)
        else:
            prompt = getattr(exc, "body_reshape_prompt", "") or ""
            raw_response = getattr(exc, "body_reshape_raw_response", None)
        if not prompt:
            try:
                prompt = build_body_reshape_planning_instruction(persons)
            except Exception:
                prompt = ""
        if not getattr(exc, "body_reshape_error_kind", None):
            exc.body_reshape_error_kind = "response_validation"
        record = _body_reshape_error_record(
            exc, persons, prompt=prompt, raw_response=raw_response, usage=usage_body
        )
        if isinstance(body_response, dict) and "body_reshape_plans" in body_response:
            try:
                partial, failures = merge_body_reshape_plans_partial(
                    analysis, body_response
                )
            except Exception as recovery_exc:
                record["context"]["partial_recovery_error"] = {
                    "type": type(recovery_exc).__name__,
                    "message": str(recovery_exc),
                }
            else:
                if failures:
                    record["context"]["partial_merge"] = True
                    record["context"]["partial_failures"] = failures
                    partial["_body_reshape_status"] = "partial_error"
                    partial["_body_reshape_error"] = record
                    logger.exception(
                        "Body reshape validation failed; partial recovery applied "
                        "(valid plans retained, failures=%d): %s",
                        len(failures), exc,
                    )
                    return partial, usage_body
        logger.exception(
            "Body reshape analysis pass failed; fallback applied (kind=%s): %s",
            record["error_kind"], exc,
        )
        return _body_reshape_failure_fallback(analysis, persons, record), usage_body
