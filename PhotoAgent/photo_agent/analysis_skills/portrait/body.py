"""Body Reshape planning pass: instruction, response schema, the call and merging the per-person plans."""

import logging
import re
import traceback
from datetime import datetime, timezone

from photo_agent.utils import bounded_debug_value

from prompts import (
    BODY_RESHAPE_EDIT_PROMPT_MAX_LENGTH,
    BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH,
    _body_reshape_fallback_prompt,
    _schema_boolean,
    _schema_integer,
    _schema_object,
    body_reshape_enabled,
    body_reshape_prompt_display_fallback,
    normalize_body_reshape_edit_prompt,
    normalize_body_reshape_prompt_display,
    normalize_body_reshape_report,
    sanitize_body_reshape_analysis,
    strip_body_reshape,
)

from .instructions import _person_list_text

logger = logging.getLogger(__name__)


def build_body_reshape_planning_instruction(persons=None):
    """Ask for one body-reshape plan per person, including head/neck outer-contour assessment."""

    if not body_reshape_enabled():
        return ""

    people_text = (
        _person_list_text(persons or [])
        if persons
        else "No separately identified person."
    )

    return f"""You are a professional portrait retoucher planning Body Reshape edits.

Inspect the attached original photograph and analyze every listed person independently. Never transfer a body observation, region, amount, or instruction from one person to another. Return one plan for every supplied person, in the same order, using the supplied subject_index and local_detection_index unchanged. For each plan, the first sentence of report must contain that plan's exact supplied person label; the report may begin with the aesthetic reason, but identify the correct person before giving evidence. When should_edit=true, edit_prompt must begin exactly with「將{{exact supplied person label}}」. The user-facing prompt_display must describe only that same person's approved change. Do not substitute another person's label even when scanning the photograph in a different spatial order.

Body Reshape is enabled for this planning pass, but first perform a whole-visible-silhouette body-type suitability check. If a person is already markedly thin or very slender overall — for example, the visible torso and limbs have minimal soft-tissue contour, a notably narrow overall silhouette, or prominent bony landmarks — do not propose further slimming merely because one local width can technically be measured. A clearly visible abdomen or waist is not by itself permission to narrow it. Set should_edit=false for a slimming direction, explain in the report that the existing visible body is already very lean and that another reduction would be disproportionate, and return edit_prompt as the empty string. This is a visual proportion safeguard, not a medical diagnosis; do not infer it from clothing alone, and do not label a person over-thin from one narrow region, perspective, or pose in isolation. A thin-body no-op is valid even when a local contour is measurable and overrides the normal edit-default rule.

For every other person, when a visible body contour contains at least one safe, localizable, measurable geometric adjustment, treat should_edit=true as the default and provide a concrete Body Reshape recommendation. An obvious defect is not required when a modest, anatomically natural local contour adjustment is supported by the image. A tasteful, modest slimming or contour refinement of one clearly visible body region — for example gently narrowing a waist, abdomen, thigh, or upper-arm outline toward a more balanced silhouette — is a valid, sufficient Body Reshape opportunity as long as the change stays anatomically plausible, keeps the person recognizably the same body type and identity, and stays within a subtle, localized amount.

Whenever should_edit=true, edit_prompt must name at least one visible body region and state a concrete geometric increase or decrease; never pair true with preservation-only or vague wording. Set should_edit=false when either the person is already markedly thin for the requested slimming direction or no body region can be safely identified and measured (for example, the relevant contours are materially occluded, out of frame, or cannot be distinguished from another object/person). In both no-edit cases, explain the visual evidence and limitation in the report rather than inventing a target, and set edit_prompt="". The edit_prompt key remains required by the response format, but a false plan must never contain an Editor instruction, a preservation sentence,「不執行」, or other placeholder text.

A seated, bent, crossed, or otherwise non-standing pose does not by itself block editing, but any specific segment whose outer edge is overlapped by another limb, garment fold, furniture, another person, or the frame boundary is not a safely measurable target even when a different segment of the same person is; never substitute a nearby unoccluded contour as a stand-in for that segment's true width. When an edit is warranted, confine the primary body adjustment to the single most clearly visible, fully unoccluded local side or segment that supports it; do not widen that primary target to the opposite side, the whole waist/torso, or any adjacent segment unless that other side or segment is independently and just as clearly visible and unoccluded. Necessary, explicitly specified head/neck proportion compensation under the rules below is a permitted exception to this single-primary-region limit, not permission to reshape other unlisted regions. Never invent a hidden measurement or force an edit when no safe visible target exists.

[Required head and neck outer-contour assessment]
For every should_edit=true plan, assess the selected body contour together with the visible outer contour of the head and neck so the complete silhouette remains natural. Body Reshape may authorize only the named body contour and the minimum head outer-contour or neck outer-contour follow-through needed for proportion. Other feature endpoints remain owned by their own contracts and must not be added to this body command. Conversely, Body Reshape must never preserve, freeze, cancel, override, or reduce the priority of a separately approved non-body endpoint, including gaze-driven face orientation or head alignment. The body planner does not know which independent endpoints will be active at Editor time.

- Classify the body change before writing the command. A modest/local change affects one clearly visible segment and is normally below 15% of that segment's original total width. A substantial change is any explicit total target of 15% or more, a change spanning two or more adjacent trunk regions, a broad torso/full-visible-silhouette change, a visibly large transition from a markedly heavy silhouette toward a much smaller silhouette, or a post-edit proportion risk in which the head would look oversized relative to the altered shoulders, torso, or limb even when the body amount is below 15%.
- For a modest/local change, head and neck outer-contour compensation may be omitted only after a post-edit proportion check confirms that the head, neck, shoulders, torso, and edited limb still read naturally. A modest upper-arm or shoulder-adjacent edit is not automatically exempt: if the exact body change would make the head look too large or the neck transition look disproportionate, treat it as a proportion-risk case and require both outer-contour operations. For a substantial or proportion-risk change, head outer-contour and neck outer-contour compensation are mandatory whenever those contours are visible and measurable; never write that both remain unchanged. If a required contour is cropped, occluded, or unsafe to measure, do not approve the broad/proportion-risk body change as written—reduce it to a safe local target or set should_edit=false with the visibility limitation.

- Head outer contour: when compensation is optional or mandatory under the classification above, uniformly scale the visible outer head contour as one coherent unit. State a concrete amount chosen from the visible proportions; use the same linear factor for width and height, and keep the original head-to-neck connection, tilt, direction, and pose. Do not blindly copy the body percentage and do not independently translate, rotate, stretch, or compress the head.
- Neck outer contour: when compensation is optional or mandatory under the classification above, adjust only a clearly visible local outer width at a named level. Both boundaries must be visible for a total-width percentage; choose the amount from the visible body-to-neck proportions rather than copying the body percentage, and keep the neck centerline, visible length, head/shoulder attachment, and pose unchanged.
- Choose head and neck outer-contour amounts independently from the primary body amount and keep each compensation no larger than necessary to restore a natural silhouette. Do not use a head or neck adjustment to justify a larger body change or to alter any unlisted region.
- Apply visibility and occlusion checks independently to the head and neck outer contours. Do not guess hidden boundaries or modify a cropped/occluded connection. If no compensation is needed, record that conclusion in report only; omit head and neck from edit_prompt rather than writing a broad unchanged instruction that could conflict with another feature contract.
- Every approved head or neck outer-contour adjustment must appear explicitly in edit_prompt with its region, geometric operation, direction, and amount. If no compensation is selected after the post-edit proportion check, do not add a head/neck preservation clause to edit_prompt. Absence means Body Reshape authorizes no head/neck scaling; it does not freeze a separately approved non-body endpoint such as gaze-driven face orientation or head alignment.

For should_edit=true, edit_prompt is a single Traditional-Chinese editor command of at least {BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH} and at most {BODY_RESHAPE_EDIT_PROMPT_MAX_LENGTH} Unicode characters. Use concise clauses within that one command to include the primary body edit and any approved head/neck outer-contour compensation. It must name that person's visible body region or contour, geometric increase/decrease direction, and the visible local measurement or target amount when the image supports one. State only the necessary local garment follow-through: the existing garment follows the approved contour without changing garment identity or coverage. For a substantial target, include both the mandatory head outer-contour and neck outer-contour operations with concrete amounts; never state that both remain unchanged. For a modest/local target with no compensation, omit head and neck from edit_prompt entirely.

Do not add any blanket preservation/remainder clause to edit_prompt. In particular, never write「其餘身體與場景保持不變」、「其他區域維持原狀」、「其餘部分不變」、「其他人物或背景保持不變」or an equivalent sentence. Such text can freeze or compete with independently approved gaze, blink, smile, hair, pose, crop, lighting, or style work. An omitted region is already outside Body Reshape authority; do not restate that boundary inside the per-person command. The report may explain why a target was accepted or rejected, but its prose must not be copied into edit_prompt.

Mention only static geometry: never request a pose, action, gesture, head/body direction, joint angle, clothing redesign, or background reconstruction. This means Body Reshape neither authorizes nor prohibits those properties; a separately approved feature or style contract remains authoritative for its own endpoint. The explicitly approved head outer-contour and neck outer-contour operations are the only additional proportion adjustments allowed beside the named body region. Do not use vague text such as「微調輪廓」alone. Numeric percentages refer to the original photograph and must be interpreted as a total change of the named visible 2D measurement, not per side or hidden anatomy; a head-scaling percentage applies equally to the head's original width and height.

The report is a concise Traditional-Chinese explanation written from a professional portrait photographer's perspective, not an engineering log. Keep it to two to four sentences: the first sentence must name the supplied person and state the visible aesthetic reason; then describe the specific contour, its relationship to clothing, pose, light, shadow, negative space, or visual weight, and the expected effect on the photograph. Include the supported direction and amount in natural wording only when useful, and describe head/neck decisions as visible head-to-shoulder or neck-to-torso proportion. For every edited person, mention the head/neck proportion conclusion and any visibility limit in photographic language. For a no-edit person, explain why the current silhouette suits the frame or identify the concrete occlusion that makes a reliable refinement impossible. Avoid internal identifiers, schema/pipeline terms, and phrases such as「局部幾何」、「可量測端點」、「比例補償」. Do not use「更俐落自然」、「俐落和諧」or「更自然」as a stock conclusion; replace them with an observable visual result. Do not call body size a defect, imply thinner is universally more attractive, or claim an edit has already succeeded. The report is review context; edit_prompt remains the exact text handed to Editor and must contain every approved geometric operation, while report prose must not be copied into it.
Report wording rule: do not expose percentages, total-width measurements, internal thresholds, English classifications such as markedly thin or modest, the feature name Body Reshape, or validation terms such as「可量測」、「局部幾何」and「比例補償」. Put exact geometry in edit_prompt; in report, describe the visible styling reason and expected photographic result with concrete cues such as how a sleeve meets the elbow, how a waist edge meets nearby negative space, or how head-to-shoulder weight is balanced.

Choose the explanation from the actual photograph instead of a fixed template, and give different people or regions different visual reasons. Ground the report in one or two concrete cues such as a jacket side seam that projects into the frame, a sleeve-to-elbow transition that carries excess visual weight, a waist edge that competes with the subject's negative space, a jacket front that no longer follows the torso, or a head-to-shoulder relationship that needs proportion review. For a no-op, explain the current silhouette's photographic strength or the exact overlap that prevents a trustworthy reading. These cues guide observation rather than provide text to copy; do not repeat the same adjective pair or sentence ending across people.

For should_edit=true, prompt_display is one short Traditional-Chinese sentence (roughly 12–50 characters) for the user. It must name only the approved visible region and tell the user what visual difference to expect, using concrete photographic language rather than a generic compliment. Choose the wording from the image and vary it across regions or people; do not reuse「更俐落自然」、「俐落和諧」、「更自然」or「更和諧」as a stock ending. Examples of the intended specificity are：「收整腰側外緣，讓坐姿上衣側線貼合身形。」、「減輕短袖下方的上臂份量，讓袖口到手肘的線條更清楚。」、「整理外套前襟與腰側銜接，平衡軀幹的視覺重量。」、「調整腹部外緣與肩線份量，讓人物在畫面中更修長。」These are examples, not templates. Do not include percentages, engineering terms, Editor commands, garment-preservation rules, unsupported promises, or extra body/face/pose changes. Do not copy the full edit_prompt or report. For should_edit=false, set prompt_display="". This field is shown in the UI only and is never an Editor instruction.
Prompt-display variation rule: choose the benefit that matches the named region—sleeve-to-elbow continuity for an arm, waist-to-garment fit for the middle body, leg flow for the lower body, shoulder-to-head balance for a substantial upper-body change, or subject-to-background spacing for a general contour. Two people must not receive the same adjective pair when their visible evidence differs.
prompt_display 必須只使用繁體中文漢字與中文標點，不得出現任何英文字母或英文術語；例如 silhouette、contour、outline 一律改寫為「輪廓」，sleeve silhouette 一律寫成「袖型輪廓」。若模型草稿含有英文，請先轉成自然繁體中文再輸出。

Report construction rule: before writing the prose, choose one dominant photographic cue from the supplied image—sleeve-to-elbow continuity, shoulder-to-sleeve transition, jacket front or side seam, waist/abdomen against the garment and nearby negative space, trouser or skirt flow, head-to-shoulder balance, or subject-to-background spacing. The report must name that cue and explain why it affects this frame; do not replace the explanation with a generic judgement such as「好看」、「自然」、「和諧」or「俐落」. Exact percentage and measurement language belongs only in edit_prompt; report may describe magnitude as「小幅」or「明顯」only when that helps the photographic explanation.

Report variation rule: two people or two different body regions must not share the same opening, adjective pair, or final clause. Use the visual relationship that actually changes—for example「讓短袖下緣到手肘的轉折更清楚」、「使外套前襟沿腰側更貼合」、「平衡腰側與背景留白的份量」or「讓褲管線條順著腿部姿態延伸」—as guidance only, and write a fresh sentence grounded in the image. A no-op report must name the silhouette's existing compositional strength or the concrete overlap/crop that prevents a reliable reading.

Prompt-display construction rule: prompt_display is a user-facing benefit sentence, not a shortened edit_prompt. Name only the approved region, then state the visible compositional result in Traditional Chinese. Select the result from the actual evidence (garment edge, line, spacing, or visual weight), vary the verb and ending across people, and never end with「更俐落自然」、「俐落和諧」、「更自然」、「更和諧」、「更好看」or a similarly empty compliment. Do not mention percentages, internal analysis, validation, or any other feature endpoint; when should_edit=false, return an empty string.

Scope rule for planning only; do not copy or paraphrase it into edit_prompt: Body Reshape authorizes only the named body contour and any explicitly approved head or neck outer contour. Separately approved feature/style endpoints remain authoritative. Existing fabric, texture, seams, folds, local clothing boundaries, and the narrow adjacent background strip may follow only as necessary for the approved contour, without changing garment identity or coverage. Do not move a person to their box coordinates or treat a box as a target silhouette.

Return exactly this JSON object:
{{
  "body_reshape_plans": [
    {{"subject_index": 0, "local_detection_index": 0, "should_edit": true, "report": "...", "prompt_display": "...", "edit_prompt": "..."}}
  ]
}}

People visible in the source:
{people_text}
""".strip()


_BODY_RESHAPE_PLAN_SCHEMA = _schema_object({
    "subject_index": _schema_integer(),
    "local_detection_index": _schema_integer(),
    "should_edit": _schema_boolean(),
    "report": {"type": "string", "minLength": BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH, "maxLength": 600},
    "prompt_display": {"type": "string", "minLength": 0, "maxLength": 120},
    "edit_prompt": {
        "type": "string",
        # Structured Outputs cannot express a minLength conditional on the
        # sibling should_edit flag.  Empty is valid only for a no-op; the merge
        # boundary below enforces the active-plan minimum.
        "minLength": 0,
        "maxLength": BODY_RESHAPE_EDIT_PROMPT_MAX_LENGTH,
    },
})


def build_body_reshape_planning_response_schema(person_count=None):
    """Strict transport schema for the per-person Body plans."""
    properties = {"body_reshape_plans": {
        "type": "array", "items": _BODY_RESHAPE_PLAN_SCHEMA,
    }}
    if person_count is not None:
        count = max(0, int(person_count))
        properties["body_reshape_plans"].update(minItems=count, maxItems=count)
    return {"type": "json_schema", "name": "body_reshape_planning", "strict": True,
            "schema": _schema_object(properties)}


def merge_body_reshape_plans(analysis, response):
    """Validate and merge one independent plan into every person subject."""
    if not body_reshape_enabled():
        return strip_body_reshape(analysis)
    cleaned = sanitize_body_reshape_analysis(analysis or {})
    subjects = [dict(subject) if isinstance(subject, dict) else subject
                for subject in (cleaned.get("subjects") or [])]
    cleaned["subjects"] = subjects
    expected = {
        index for index, subject in enumerate(subjects)
        if isinstance(subject, dict) and subject.get("is_person") is True
    }
    plans = response.get("body_reshape_plans") if isinstance(response, dict) else None
    if not isinstance(plans, list):
        raise ValueError("body reshape planning response must contain body_reshape_plans")
    seen = set()
    for plan in plans:
        if not isinstance(plan, dict):
            raise ValueError("body reshape plan entries must be objects")
        subject_index = plan.get("subject_index")
        if isinstance(subject_index, bool) or not isinstance(subject_index, int) or subject_index not in expected or subject_index in seen:
            raise ValueError("invalid or duplicate body reshape subject_index {}".format(subject_index))
        subject = subjects[subject_index]
        expected_detection = subject.get("local_detection_index", -1)
        returned_detection = plan.get("local_detection_index", -1)
        if returned_detection != expected_detection:
            raise ValueError("body reshape local_detection_index mismatch for subject_index {}".format(subject_index))
        subject_label = " ".join(str(subject.get("label") or "person").split()).strip()
        report = " ".join(str(plan.get("report") or "").split()).strip()
        report_first_sentence = re.split(r"[。！？!?\n]", report, maxsplit=1)[0]
        if subject_label not in report_first_sentence:
            raise ValueError(
                "body reshape report subject mismatch for subject_index {}: expected label {!r}".format(
                    subject_index, subject_label
                )
            )
        should_edit = plan.get("should_edit") is not False
        body = {
            "subject_index": subject_index,
            "local_detection_index": returned_detection,
            "should_edit": should_edit,
        }
        if should_edit:
            prompt = plan.get("edit_prompt")
            # Accept one old-version key_points array while migrating saved test
            # fixtures; new API responses always use edit_prompt.
            if not isinstance(prompt, str) or not prompt.strip():
                points = plan.get("key_points")
                prompt = "；".join(
                    str(point).strip() for point in points or [] if str(point).strip()
                ) if isinstance(points, list) else ""
            compact_prompt = " ".join(str(prompt or "").split()).strip().strip('"“”')
            if len(compact_prompt) < BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH:
                raise ValueError(
                    "body reshape should_edit=true requires a detailed edit_prompt"
                )
            if not compact_prompt.startswith("將" + subject_label):
                raise ValueError(
                    "body reshape edit_prompt subject mismatch for subject_index {}: expected prefix {!r}".format(
                        subject_index, "將" + subject_label
                    )
                )
            body["edit_prompt"] = normalize_body_reshape_edit_prompt(
                prompt, fallback=_body_reshape_fallback_prompt(subject.get("label"), True)
            )
            # Older responses may omit this UI-only field; provide a concise,
            # region-aware fallback instead of hiding the active Body plan.
            body["prompt_display"] = normalize_body_reshape_prompt_display(
                plan.get("prompt_display"),
                fallback=body_reshape_prompt_display_fallback(prompt, subject_label),
            )
        body["report"] = normalize_body_reshape_report(
            report, subject_label=subject_label, should_edit=should_edit
        )
        subject["body_reshape"] = body
        seen.add(subject_index)
    missing = sorted(expected - seen)
    if missing:
        raise ValueError("body reshape plans missing subject_index values: {}".format(missing))
    cleaned.pop("body_reshape", None)
    cleaned.pop("_body_reshape_status", None)
    cleaned.pop("_body_reshape_error", None)
    cleaned.pop("_body_reshape_partial_failures", None)
    cleaned.pop("_body_reshape_enabled", None)
    cleaned.pop("_disabled_body_reshape_subjects", None)
    return cleaned


def merge_body_reshape_plans_partial(analysis, response):
    """Merge independently valid plans while isolating invalid people.

    The strict merge remains the normal path. This recovery path is used only
    after that strict merge fails: each returned plan is validated against
    synthetic no-op plans for the other people, so a bad subject cannot make
    a valid person's command disappear. Missing or invalid people become an
    explicit analysis_status=error no-op and are reported to the caller.
    """
    if not body_reshape_enabled():
        return strip_body_reshape(analysis), []
    plans = response.get("body_reshape_plans") if isinstance(response, dict) else None
    if not isinstance(plans, list):
        raise ValueError("body reshape planning response must contain body_reshape_plans")
    base = sanitize_body_reshape_analysis(analysis or {})
    subjects = [subject for subject in (base.get("subjects") or [])]
    expected = {
        index for index, subject in enumerate(subjects)
        if isinstance(subject, dict) and subject.get("is_person") is True
    }

    def no_op_plan(subject_index, reason):
        subject = subjects[subject_index]
        label = " ".join(str(subject.get("label") or "person").split()).strip()
        report = f"{label}{reason}"
        return {
            "subject_index": subject_index,
            "local_detection_index": subject.get("local_detection_index", -1),
            "should_edit": False,
            "report": report[:600],
            "edit_prompt": "",
        }

    valid = {}
    failures = []
    seen = set()
    for position, plan in enumerate(plans):
        failure = {"position": position}
        if not isinstance(plan, dict):
            failure["reason"] = "body reshape plan entry is not an object"
            failures.append(failure)
            continue
        subject_index = plan.get("subject_index")
        failure["subject_index"] = subject_index
        if (
            isinstance(subject_index, bool)
            or not isinstance(subject_index, int)
            or subject_index not in expected
        ):
            failure["reason"] = f"invalid body reshape subject_index {subject_index}"
            failures.append(failure)
            continue
        if subject_index in seen:
            failure["reason"] = f"duplicate body reshape subject_index {subject_index}"
            failures.append(failure)
            continue
        seen.add(subject_index)
        trial = [
            plan if index == subject_index else no_op_plan(
                index, "本次回應未包含此人物的可驗證身形指令，保留原狀。"
            )
            for index in sorted(expected)
        ]
        try:
            merge_body_reshape_plans(base, {"body_reshape_plans": trial})
        except Exception as exc:
            failure["reason"] = str(exc)
            failures.append(failure)
        else:
            valid[subject_index] = plan

    final_plans = [
        valid[index] if index in valid else no_op_plan(
            index, "本次沒有足夠可靠的身形判讀，為維持畫面比例，先保留目前輪廓。"
        )
        for index in sorted(expected)
    ]
    merged = merge_body_reshape_plans(base, {"body_reshape_plans": final_plans})
    if failures:
        for subject_index in sorted(expected - set(valid)):
            body = merged["subjects"][subject_index].get("body_reshape")
            if isinstance(body, dict):
                body["analysis_status"] = "error"
        merged["_body_reshape_status"] = "partial_error"
        merged["_body_reshape_partial_failures"] = failures
    return merged, failures


def merge_body_reshape_edit_prompt(analysis, response):
    """Merge the new per-person plans, with a history-compatible fallback."""
    if isinstance(response, dict) and "body_reshape_plans" in response:
        return merge_body_reshape_plans(analysis, response)
    # Older offline callers supplied one top-level sentence. Preserve it as a
    # compatibility shape; new Analyze calls never take this branch.
    if not body_reshape_enabled():
        return strip_body_reshape(analysis)
    cleaned = sanitize_body_reshape_analysis(analysis or {})
    value = response.get("edit_prompt") if isinstance(response, dict) else None
    cleaned["body_reshape"] = {"edit_prompt": normalize_body_reshape_edit_prompt(value)}
    return cleaned


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
