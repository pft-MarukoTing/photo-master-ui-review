import copy
import json
import logging
import re

import config as _photoagent_config
from config import STYLE_OPTION_INSTRUCTIONS, STYLE_OPTION_LABELS, STYLE_OPTION_VALUE_LABELS

logger = logging.getLogger(__name__)


def body_reshape_enabled():
    """Return the current internal Body Reshape feature flag.

    Read the module attribute dynamically instead of importing the boolean by
    value.  This keeps tests and long-running development processes able to
    change ``config.ENABLE_BODY_RESHAPE`` without leaving a stale copy in this
    module.
    """
    return bool(getattr(_photoagent_config, "ENABLE_BODY_RESHAPE", True))


# Body Reshape is a per-person contract.  The old review UI showed each
# person's own body instruction; keep that relationship all the way through
# Analyze -> review -> Editor instead of collapsing several people into one
# holistic sentence.  Twenty characters is an intentional lower bound: a
# command must name enough visible geometry to be useful to the editor.
BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH = 20
# Head/neck proportion follow-through is part of a single Editor command for
# substantial or proportion-risk edits.  Keep enough room for the body target,
# independent head and neck amounts, and garment-follow-through wording; a
# shorter limit would silently replace a valid detailed command with the
# generic fallback during normalization.
BODY_RESHAPE_EDIT_PROMPT_MAX_LENGTH = 360
BODY_RESHAPE_FALLBACK_PROMPT = "自然調整可見身形輪廓，必要時順接頭部與頸部外圍，保持解剖連續、衣物邊界及背景透視"

# prompt_display is a short review-only sentence, not the Editor command.
# Models occasionally leave an English retouching term in that sentence even
# when the rest of the response is Traditional Chinese. Normalize the small
# set of common terms and remove any remaining Latin words at this boundary so
# the review UI never exposes mixed-language copy. The detailed
# edit_prompt is deliberately not passed through this helper.
_BODY_RESHAPE_PROMPT_DISPLAY_REPLACEMENTS = (
    (re.compile(r"\bsilhouette\b", re.I), "輪廓"),
    (re.compile(r"\bcontour\b", re.I), "輪廓"),
    (re.compile(r"\boutline\b", re.I), "輪廓"),
    (re.compile(r"\bshape\b", re.I), "線條"),
    (re.compile(r"\bsleeve\b", re.I), "袖型"),
    (re.compile(r"\b(?:natural|naturally)\b", re.I), "自然"),
    (re.compile(r"\b(?:clean|cleaner)\b", re.I), "俐落"),
    (re.compile(r"\b(?:fit|fitting)\b", re.I), "貼合"),
    (re.compile(r"局部幾何(?:調整|修改|修整)?|幾何(?:調整|修改|修整)?"), "輪廓修飾"),
    (re.compile(r"可量測|可測量|量測|測量"), "可清楚辨識"),
    (re.compile(r"比例補償"), "頭肩比例調整"),
    (re.compile(r"總寬度"), "外緣線條"),
)

# These are low-information conclusions that models tend to repeat.  They are
# replaced from the surrounding body-region words, so an arm explanation does
# not receive the same ending as a waist, leg, or head/shoulder explanation.
# The replacement is deliberately phrased as a visible photographic result,
# not as a geometric operation.
_BODY_RESHAPE_STOCK_PHRASE_RE = re.compile(
    r"更俐落和諧|更俐落自然|俐落和諧|俐落自然|"
    r"自然[、，,]和諧|自然且和諧|更和諧|更自然|更俐落"
)

_BODY_RESHAPE_REPORT_REPLACEMENTS = (
    (re.compile(r"\bmarkedly\s+thin\b", re.I), "整體偏纖細"),
    (re.compile(r"\bmodest\b", re.I), "小幅"),
    (re.compile(r"\bbody\s+reshape\b", re.I), "身形調整"),
    (re.compile(r"頭部(?:與|及|或)頸部外輪廓補償|頭頸外輪廓補償"), "頭肩比例另外修飾"),
    (re.compile(r"局部幾何(?:調整|修改|修整)?"), "局部輪廓修整"),
    (re.compile(r"比例補償"), "頭肩比例調整"),
    (re.compile(r"可量測|可測量|量測|測量"), "可清楚辨識"),
    (re.compile(r"總寬度"), "外緣寬度"),
    (re.compile(r"比例檢查"), "頭肩比例觀察"),
    (re.compile(r"比例風險"), "頭肩比例失衡疑慮"),
    (re.compile(r"視覺和諧"), "符合畫面份量"),
    (re.compile(r"不納入相關操作"), "不需另外處理"),
    (re.compile(r"低於\s*15\s*[%％]\s*(?:的)?\s*(?:局部)?(?:調整|微調)"), "小幅局部修飾"),
)
# These keys appeared at the analysis root in intermediate/debug artifacts.
# They are accepted only as history input and removed before an edit request;
# ``_disabled_body_reshape_subjects`` is the one request-scoped review field
# retained because the UI can disable one person's plan without changing the
# analyzer output.
_BODY_RESHAPE_LEGACY_KEYS = (
    "key_points", "raw_key_points", "target_pct", "effective_target_pct",
    "planner_target_pct", "planner_default_target_pct", "body_reshape_execution",
    "body_reshape_execution_plan", "_disabled_body_reshape_key_points",
    "_body_reshape_amount_overrides", "_body_reshape_execution_plan",
)


def _body_reshape_fallback_prompt(label=None, should_edit=True):
    """Build an active-plan fallback while retaining person context."""
    label = " ".join(str(label or "此人物").split()).strip() or "此人物"
    if should_edit:
        return (
            f"針對{label}調整可見身形輪廓，必要時順接頭部與頸部外圍，保持自然解剖、衣物邊界及背景透視連續"
        )
    # A no-op is explained by ``report`` only.  Supplying a preservation
    # sentence here would make it look like an Editor command and could freeze
    # independently approved face/style work.
    return ""


def normalize_body_reshape_edit_prompt(value, fallback=BODY_RESHAPE_FALLBACK_PROMPT):
    """Return one detailed editor-ready body instruction.

    Unlike the short holistic contract used briefly in the previous revision,
    this normalizer intentionally preserves useful numeric/detail wording and
    only falls back when the instruction is empty, too short, too long, or
    clearly formatted as a list.  In particular, percentages are allowed:
    they are often the only unambiguous magnitude an editor can follow.
    """
    fallback_text = " ".join(str(fallback or BODY_RESHAPE_FALLBACK_PROMPT).split()).strip()
    if len(fallback_text) < BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH:
        fallback_text = BODY_RESHAPE_FALLBACK_PROMPT
    if len(fallback_text) > BODY_RESHAPE_EDIT_PROMPT_MAX_LENGTH:
        fallback_text = BODY_RESHAPE_FALLBACK_PROMPT
    if not isinstance(value, str):
        return fallback_text
    text = " ".join(value.split()).strip().strip('"“”')
    if not text or len(text) < BODY_RESHAPE_EDIT_PROMPT_MIN_LENGTH:
        return fallback_text
    if len(text) > BODY_RESHAPE_EDIT_PROMPT_MAX_LENGTH:
        return fallback_text
    if re.search(r"^(?:[#*•●▪-]|\d+\s*[.)、．：:])\s*", text):
        return fallback_text
    return text


def _body_reshape_outcome_phrase(text, offset, *, report=False):
    """Choose a concrete visual outcome from the nearby named region."""
    source = str(text)
    start = max(0, offset - 160)
    context = source[start:min(len(source), offset + 160)]
    relative_offset = offset - start
    cues = (
        ("arm", re.compile(r"上臂|手臂|袖|肩臂")),
        ("waist", re.compile(r"腰|腹|肚|下腹|側腹")),
        ("leg", re.compile(r"腿|大腿|小腿|臀|髖")),
        ("head", re.compile(r"頭|頸|肩")),
        ("ratio", re.compile(r"整體比例|比例")),
        ("overall", re.compile(r"份量|整體(?!比例)|胸|軀幹|身形")),
    )
    nearby = []
    for name, pattern in cues:
        matches = list(pattern.finditer(context))
        if matches:
            match = min(matches, key=lambda item: abs(item.start() - relative_offset))
            nearby.append((abs(match.start() - relative_offset), name))
    if nearby:
        name = min(nearby, key=lambda item: item[0])[1]
    else:
        name = "generic"
    if name == "arm":
        return "更清楚地銜接肩線與袖口" if report else "更清楚"
    if name == "waist":
        return "更貼合腰腹弧線" if report else "更貼合身形"
    if name == "leg":
        return "更順著腿部姿態" if report else "更修長"
    if name == "head":
        return "更協調地銜接頭肩比例" if report else "更協調"
    if name == "ratio":
        return "更均衡"
    if name == "overall":
        return "更均衡地分配畫面份量" if report else "更均衡"
    return "與畫面留白更平衡" if report else "更順暢"


def _replace_body_reshape_stock_phrases(text, *, report=False):
    """Replace repetitive aesthetic conclusions using local visual context."""
    if not isinstance(text, str):
        return ""
    return _BODY_RESHAPE_STOCK_PHRASE_RE.sub(
        lambda match: _body_reshape_outcome_phrase(text, match.start(), report=report),
        text,
    )


def normalize_body_reshape_prompt_display(value, fallback=""):
    """Return UI copy with no Latin words while preserving Chinese wording.

    prompt_display is generated by the model and is intentionally kept
    separate from the Editor command. A deterministic cleanup here prevents a
    stray term such as silhouette from reaching the review UI without
    weakening or rewriting the actual body-reshape instruction.
    """
    def clean(raw):
        if not isinstance(raw, str):
            return ""
        text = " ".join(raw.split()).strip()
        for pattern, replacement in _BODY_RESHAPE_PROMPT_DISPLAY_REPLACEMENTS:
            text = pattern.sub(replacement, text)
        text = _replace_body_reshape_stock_phrases(text)
        text = re.sub(r"約?\s*\d+(?:\.\d+)?\s*[%％]", "", text)
        # Unknown English words are not useful in this user-facing sentence.
        # Remove complete Latin tokens rather than individual letters so a
        # Chinese sentence remains readable after cleanup.
        text = re.sub(r"[A-Za-z][A-Za-z0-9_./-]*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
        text = re.sub(r"\s+([，。！？、：；,.!?])", r"\1", text)
        text = re.sub(r"([，。！？、：；]){2,}", r"\1", text)
        return text.strip(" -—:：;；")[:120]

    text = clean(value)
    if text:
        return text
    return clean(fallback)


def body_reshape_prompt_display_fallback(edit_prompt="", subject_label=""):
    """Build a region-aware UI sentence when a planner omits prompt_display."""
    source = str(edit_prompt or "")
    if re.search(r"上臂|手臂|袖", source):
        return "修飾上臂線條，讓袖口至手肘更清楚"
    if re.search(r"腰|腹|肚|下腹|側腹", source):
        return "收整腰腹外緣，讓衣著側線更貼合"
    if re.search(r"腿|大腿|小腿|臀|髖", source):
        return "修飾腿部外緣，讓下身線條更修長"
    if re.search(r"頭|頸|肩", source):
        return "調整頭肩外緣，讓整體比例更協調"
    return f"修飾{subject_label or '身形'}外緣，讓畫面份量更均衡"


def normalize_body_reshape_report(value, *, subject_label="", should_edit=True):
    """Keep the review report photographic, specific, and Traditional Chinese.

    Reports are review prose, not Editor commands. This boundary removes a
    small set of leaked optimizer/API vocabulary and rewrites repeated stock
    conclusions according to the nearby visible region. It intentionally
    leaves the detailed ``edit_prompt`` untouched.
    """
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    for pattern, replacement in _BODY_RESHAPE_REPORT_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = re.sub(
        r"(?:原始|該)?(?:可見)?(?:局部)?外緣寬度(?:縮減|收窄|減少)?\s*"
        r"(?:約|大約)?\s*\d+(?:\.\d+)?\s*[%％](?:的)?(?:幅度)?"
        r"(?:向內)?(?:縮減|收窄|減少)?",
        "局部外緣小幅收整",
        text,
    )
    text = re.sub(
        r"(?:約|大約)?\s*\d+(?:\.\d+)?\s*[%％](?:的)?(?:局部)?(?:調整|修整|微調)",
        "小幅修整",
        text,
    )
    text = re.sub(
        r"(?:約|大約)?\s*\d+(?:\.\d+)?\s*[%％]\s*(?:的)?\s*局部(?:外緣)?寬度(?:縮減|收窄|減少)",
        "小幅收整",
        text,
    )
    text = re.sub(r"局部外緣寬度(?:縮減|收窄|減少)", "局部外緣收整", text)
    text = re.sub(r"外緣寬度(?:縮減|收窄|減少)", "外緣收整", text)
    text = re.sub(r"約?\s*\d+(?:\.\d+)?\s*[%％]", "小幅", text)
    text = _replace_body_reshape_stock_phrases(text, report=True)
    text = re.sub(r"可清楚辨識(?:並)?\s*可清楚辨識", "可清楚辨識", text)
    text = re.sub(r"清楚可清楚辨識|清楚且可清楚辨識", "清楚辨識", text)
    text = re.sub(r"更均衡[、，,]\s*(?:整體)?比例更均衡", "更均衡，讓整體比例更平衡", text)
    text = re.sub(r"不需(?:要)?進行頭肩比例調整", "可維持頭肩比例", text)
    text = re.sub(r"無需頭肩比例另外修飾|不進行頭肩比例調整", "頭肩比例維持現狀", text)
    text = re.sub(r"頭頸輪廓也不納入本次指令", "頭頸比例維持現狀", text)
    text = re.sub(r"替代測量區域", "替代判斷", text)
    text = re.sub(
        r"(?:不提出|不進行|暫不執行)(?:身形調整|Body Reshape)(?:修改|調整)?|身形調整\s*不執行",
        "因此保留現有輪廓",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r"\s+([，。！？、：；,.!?])", r"\1", text)
    text = re.sub(r"([，。！？、：；]){2,}", r"\1", text)
    return text[:600]


def _body_reshape_disabled_subjects(analysis):
    values = analysis.get("_disabled_body_reshape_subjects") if isinstance(analysis, dict) else None
    return {
        value for value in (values or [])
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }


def body_reshape_plan_entries(analysis, *, include_disabled=True):
    """Return normalized per-person body plans for review or execution.

    ``include_disabled`` is used by the review UI so an opt-out still shows
    the Analyze text.  Editor consumers use ``body_reshape_items`` below,
    which returns only actionable plans.
    """
    if not body_reshape_enabled() or not isinstance(analysis, dict):
        return []
    if analysis.get("_body_reshape_enabled") is False:
        return []
    disabled = _body_reshape_disabled_subjects(analysis)
    subjects = analysis.get("subjects") if isinstance(analysis.get("subjects"), list) else []
    entries = []
    for subject_index, subject in enumerate(subjects):
        if not isinstance(subject, dict) or subject.get("is_person") is not True:
            continue
        body = subject.get("body_reshape")
        if not isinstance(body, dict):
            continue
        should_edit = body.get("should_edit") is not False
        prompt = ""
        if should_edit:
            prompt = body.get("edit_prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                legacy_points = body.get("key_points")
                if isinstance(legacy_points, list):
                    prompt = "；".join(str(point).strip() for point in legacy_points if str(point).strip())
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            prompt = normalize_body_reshape_edit_prompt(
                prompt,
                fallback=_body_reshape_fallback_prompt(subject.get("label"), True),
            )
        subject_label = subject.get("label") or f"人物 {subject_index + 1}"
        entry = {
            "subject_index": subject_index,
            "local_detection_index": subject.get("local_detection_index", -1),
            "subject_label": subject_label,
            "box": subject.get("box") or {},
            "edit_prompt": prompt,
            "prompt_display": (
                normalize_body_reshape_prompt_display(
                    body.get("prompt_display"),
                    fallback=body_reshape_prompt_display_fallback(prompt, subject_label),
                )
                if should_edit else ""
            ),
            "report": normalize_body_reshape_report(
                body.get("report"), subject_label=subject_label, should_edit=should_edit
            ),
            "should_edit": should_edit,
            "disabled": subject_index in disabled,
        }
        entry["active"] = should_edit and not entry["disabled"]
        if include_disabled or entry["active"]:
            entries.append(entry)

    # Keep old saved analyses readable.  A top-level prompt has no person
    # identity, so it is only a compatibility fallback when no nested plans
    # exist for a real person; unrelated/non-person legacy objects must not
    # suppress that fallback.
    has_person_plan = any(
        isinstance(subject, dict)
        and subject.get("is_person") is True
        and isinstance(subject.get("body_reshape"), dict)
        for subject in subjects
    )
    if not entries and not has_person_plan:
        body = analysis.get("body_reshape")
        if isinstance(body, dict) and isinstance(body.get("edit_prompt"), str) and body.get("edit_prompt").strip():
            entries.append({
                "subject_index": None,
                "local_detection_index": -1,
                "subject_label": "主要人物",
                "box": {},
                "edit_prompt": normalize_body_reshape_edit_prompt(body.get("edit_prompt")),
                "report": "",
                "should_edit": True,
                "disabled": False,
                "active": True,
            })
    return entries


def get_body_reshape_edit_prompts(analysis):
    """Return the visible per-person Analyze instructions, including opt-outs."""
    return body_reshape_plan_entries(analysis, include_disabled=True)


def get_body_reshape_edit_prompt(analysis):
    """Compatibility reader returning active instructions as one string."""
    entries = body_reshape_plan_entries(analysis, include_disabled=False)
    return "；".join(entry["edit_prompt"] for entry in entries if entry.get("active"))


def _without_body_reshape_prompt_lines(instruction):
    """Remove dedicated body-reshape references from an analysis prompt.

    The general analysis schema no longer contains a body-reshape field, but
    a few shared legacy bullets still mention that field as an ``is_person``
    placeholder.  When the feature is disabled those lines must not be sent
    to the analysis model either.  Removing complete lines keeps all other
    analysis instructions byte-for-byte unchanged.
    """
    return "\n".join(
        line for line in str(instruction).splitlines()
        if "body_reshape" not in line.lower()
    )


# ── Correction axes ───────────────────────────────────────────────────────────
#
# The vocabulary for correction_axes: which property of the photograph a
# correction_plan item moves. Defined here, above ANALYSIS_INSTRUCTION, because
# it has to be the single source for three things that must not drift apart --
# the vocabulary the analysis model is given, the axes the edit prompt gates on
# (CORRECTION_AXES). An
# axis the analysis model invents but the edit prompt does not know is dropped
# silently, which is exactly the failure a hand-maintained second copy invites.
#
# The description doubles as the model's definition of the axis, so it has to be
# specific enough to tag by.
AXIS_DEFINITIONS = {
    "exposure": "global brightness",
    "shadow": "shadow density / detail in dark areas",
    "highlight": "bright-end level and rolloff",
    "contrast": "tonal separation, micro-contrast, depth",
    "white_balance": "warm/cool character, color cast",
    "saturation": "color intensity",
    "skin": "skin tone or complexion specifically",
    "sharpen": "acuity, clarity, noise",
    "distraction": "toning down, blurring, or cleaning up scene elements",
    "crop": "composition reframing: crop boundaries, output aspect ratio, frame-relative subject scale or placement",
    "alignment": "whole-frame rotation, horizon/vertical straightening, or keystone/perspective correction",
    "expression": "facial expression repair, eye openness, accidental blink or half-closed eyes",
    "hair": "hair tidiness: stray or windblown strands, disturbed bangs, frizz, a standing tuft",
    "other": "anything the list above does not cover",
}

CORRECTION_AXES = tuple(AXIS_DEFINITIONS)

_AXIS_VOCABULARY = ", ".join(f'"{axis}" ({desc})' for axis, desc in AXIS_DEFINITIONS.items())

# Near-misses the analysis model actually produces despite being handed the
# vocabulary -- "noise" showed up on 3 of 6 photos in the first batch, tagging
# denoise items that belong to "sharpen". Left unmapped it silently breaks the
# kept-axis subtraction in resolve_rejected_axes (an approved denoise item fails
# to protect `sharpen`, so a rejected sharpen item locks it anyway and the prompt
# contradicts itself). Cheaper and more robust to absorb the synonyms than to
# keep tightening the instruction.
AXIS_ALIASES = {
    "noise": "sharpen", "denoise": "sharpen", "clarity": "sharpen", "sharpness": "sharpen",
    "brightness": "exposure", "exposures": "exposure",
    "shadows": "shadow", "highlights": "highlight",
    "warmth": "white_balance", "color_temperature": "white_balance", "colour_balance": "white_balance",
    "saturation_level": "saturation", "color": "saturation", "color_harmony": "saturation",
    "colour_harmony": "saturation", "color_grading": "saturation",
    "skin_tone": "skin", "complexion": "skin",
    "distractions": "distraction", "cleanup": "distraction",
    "cropping": "crop", "framing": "crop", "reframing": "crop",
    "straighten": "alignment", "straightening": "alignment", "rotation": "alignment",
    "horizon": "alignment", "perspective": "alignment", "keystone": "alignment",
    "eyes": "expression", "eye": "expression", "blink": "expression", "blinking": "expression",
    "closed_eyes": "expression", "half_closed_eyes": "expression", "squint": "expression",
    "facial_expression": "expression", "expression": "expression",
    "hairs": "hair", "hairstyle": "hair", "hair_tidiness": "hair", "messy_hair": "hair",
    "flyaway": "hair", "flyaways": "hair", "stray_hair": "hair", "stray_hairs": "hair",
    "bangs": "hair", "fringe": "hair", "frizz": "hair", "frizzy": "hair",
    "gaze": "expression", "eye_contact": "expression", "camera_gaze": "expression",
    "off_camera_gaze": "expression", "unfocused_gaze": "expression", "eye_focus": "expression",
    "gaze_contact": "expression", "looking_direction": "expression",
}


def style_option_entries(analysis):
    """Non-default style_options as (short_label, description) pairs, in order.

    short_label is what should ever be shown to a user (e.g. "Pose: Strong");
    description is the long instruction text used only to drive/judge the
    edit -- callers must not surface description directly in any UI-facing
    field, since an LLM asked to echo back short_label verbatim can't be
    trusted to do so reliably (that's also why quality_check_photo overwrites
    style_items[].item with this list after the fact instead of trusting the
    QA model's own "item" text).
    """
    style_options = analysis.get("style_options") or {}
    entries = []
    for category, value in style_options.items():
        if not value or value == "default":
            continue
        description = (STYLE_OPTION_INSTRUCTIONS.get(category) or {}).get(value)
        if not description:
            continue
        short_label = f"{STYLE_OPTION_LABELS.get(category, category)}: {(STYLE_OPTION_VALUE_LABELS.get(category) or {}).get(value, value)}"
        entries.append((short_label, description))
    return entries


# Shared by both analysis paths: spliced into ANALYSIS_INSTRUCTION below (the
# single-call flow) and into build_portrait_qc_instruction's Call B prompt.
_HAIR_ANALYSIS_SECTION = """## Hair analysis

### Role
- Observe each listed person's physical hair state from the full photo and matched face crop.
- Classify the state and propose its candidate plan; do not decide whether the edit should run or anticipate the user's hair level.
- Always output should_fix=false as a placeholder. Python overwrites it after this response.

### Output fields
- hair_state: choose exactly one from tidy / styled_messy / styled_bangs / styled_face_framing / motion / natural_texture / disheveled / windblown / strands_across_face / bangs_disturbed / bangs_clumped / lock_out_of_place / frizz_halo / tuft_or_flattened / occluded_or_unclear / uncertain.
- visible_evidence: name the exact strands, fringe, or region and the visible geometry that supports the state. Do not write aesthetic preference or guessed intent.
- front_hair: none / even / clumped / unclear. It is a reading, not a state: never copy its value into hair_state.
- fix_action: propose one physical operation from none / lay_back / remove / settle / redistribute. This is a candidate action, not an edit approval.
- mess_intent: always output ambiguous as a compatibility placeholder. Do not infer intent or use it as an edit verdict.
- hair_mess_confidence: confidence from 0 to 1 in the physical hair_state classification, not confidence that an edit should happen.
- correction_plan: write a candidate editor instruction for a physically actionable state; otherwise write "不適用". Writing a plan does not set should_fix.
- reason: briefly explain the physical classification and evidence. Do not argue whether the user should keep or remove it.

### Inspection order
1. Readability: if hair structure cannot be resolved because of blur, backlight, occlusion, or subject size, use occluded_or_unclear.
2. Source of movement: use motion when the person's jump, spin, flip, throw, or turn drives the hair; use windblown when environmental wind substantially lifts, opens, scatters, or pushes hair away from its settled silhouette. A coherent wind direction confirms windblown and does not make it non-actionable. Hair does not need to cover a facial feature.
3. Front-hair check: first decide whether a real fringe exists. A fringe is a shorter front layer rooted along the front hairline and visibly distinct in length or grouping from the main lengths; it may be blunt, curtain/八字, see-through, or side-swept. A side part or full-length front section that continues into the shoulder-length hair is not a fringe merely because it crosses the forehead, brow, eye, or cheek; record front_hair="none". Never use position on the face alone as evidence—use the strand's root band, endpoint, length relative to the main hair, and continuity into the longer lengths. For every confirmed fringe, inspect its left, center, and right zones at the roots and through the lengths before choosing even versus clumped. Compare adjacent bundle widths and the forehead gaps beside them; do not summarize the fringe as "light", "see-through", "roughly balanced", or "naturally separated" until this comparison is complete. If any zone contains a conspicuously thicker stuck bundle beside a much thinner bundle or an abnormally wide bare gap, use front_hair="clumped" and bangs_clumped. One such local contradiction is sufficient even when the remaining zones are balanced, the lock follows the parting direction, or oil sheen is not readable. Before choosing styled_face_framing, apply the same test to any shorter front layer: use front_hair="clumped" and bangs_clumped even when that lock follows the parting direction; a coarse separated lock with a source gap/hard edge is clumped fringe, not intentional face framing.
4. Structure: distinguish deliberate repeated styling or natural texture from disheveled hair with several unrelated disruptions, and from a single local contradiction in the surrounding hair flow.
5. Select the single clearest state. If moving or windblown hair also contains a more specific local defect such as strands_across_face or lock_out_of_place, use that local defect as hair_state and mention the movement context in visible_evidence and correction_plan.

### State and candidate-action routing
- tidy: settled hair; fix_action=none; correction_plan="不適用".
- styled_messy: deliberate tousle, cut, or product separation; fix_action=none; correction_plan="不適用".
- styled_bangs: center-parted, curtain/八字, or see-through fringe only after the left/center/right comparison finds no local thick-cord-beside-abnormal-gap contradiction; fix_action=none; correction_plan="不適用". Repetition alone does not prove styling: several separated bundles can all be clumps. A fringe is not even merely because its strands point the same way, its overall coverage looks balanced, or forehead is intentionally visible through it.
- styled_face_framing: deliberate paired temple/cheek strands integrated with an updo, ponytail, or clearly repeated face-framing cut; fix_action=none; correction_plan="不適用". Do not use this state for one isolated front lock on otherwise worn-down hair, especially when it is stuck together, has separated from the surrounding fringe/front layer, leaves a visible source gap, or falls across a brow or eye. Following the general parting direction alone does not prove intentional face framing.
- natural_texture: coherent curly, coily, fluffy, or baby-hair texture; fix_action=none; correction_plan="不適用".
- motion: body-driven flowing hair without a more specific local defect; fix_action=none; correction_plan="不適用". This state does not prevent the user's later fully-groomed transformation.
- disheveled: several visible regions have unrelated uneven directions, tangles, displaced sections, or collapsed volume that break the readable haircut, without environmental wind or body motion explaining them; fix_action=settle; describe the affected regions and use coherent hair on the same head as the target reference. Do not use this state for a consistent tousled style or natural texture.
- windblown: environmental wind has displaced a region or the whole hairstyle; fix_action=settle; provide a candidate correction_plan describing the whole disturbed region and the same head's readable haircut/flow as reference.
- strands_across_face: hair has left its surrounding flow and rests across an eye, brow, nose, mouth, lip, or cheek; use lay_back when adjacent hair can receive it, otherwise remove. Hair that belongs to an existing fringe is not this state.
- bangs_disturbed: a real fringe is flipped or pushed against its own direction; fix_action=lay_back.
- bangs_clumped: a confirmed existing fringe either forms conspicuously unequal thick bundles beside abnormal bare forehead gaps, or contains one visibly stuck-together coarse front lock separated from the otherwise coherent fringe and leaving a gap or hard edge at its source; fix_action=redistribute. This is primarily a distribution-geometry decision, not an oil-detection decision. One strong coarse pattern in any left/center/right zone is sufficient. Oil/sweat, forehead adhesion, darker or slicker grouping, and a hard continuous reflection support this reading but are not required. A clumped lock may still point in the same direction as the parting; directional agreement does not make it styled. Only front_hair="clumped" on a confirmed existing fringe may use this state and action.
- lock_out_of_place: one lock has escaped a tie, clip, hat, or surrounding flow; fix_action=lay_back.
- frizz_halo: a localized static halo contradicts an otherwise settled outline; fix_action=settle.
- tuft_or_flattened: one local tuft stands up or one area is visibly pressed against its neighbours; fix_action=settle.
- uncertain: visible hair whose state cannot be selected reliably; fix_action=none; correction_plan="不適用".

### Action meanings
- lay_back: return only the named strands to adjacent existing hair flow. A non-fringe correction must return them to the source side part or long-hair flow; never create a fringe or close forehead gaps.
- remove: use only for isolated hair that has left the hairstyle. Never remove an existing fringe, hairline, or hairstyle edge. Strands resting further down on a cheek, jaw, or lip may use remove when no adjacent flow can receive them.
- settle: describe the complete disturbed area and use undisturbed hair on the same head to define length, parting, volume, direction, and silhouette. Treat the whole disturbed area as the work, not only strands touching the face.
- redistribute: use only for a confirmed clumped fringe. Reduce its oily hard reflection to the surrounding hair's sheen and spread the same existing strands into a balanced but translucent fringe, closing abnormal gaps without adding hair, density, length, or width.

### Face and hairstyle safety
- Hair over an eye, nose, or mouth may still receive a candidate plan when the feature remains visibly recoverable around the strands. The plan must preserve that person's visible feature shape and position and repair only the strand-width footprint.
- If hair completely buries a feature and no source evidence remains around it, use occluded_or_unclear rather than inventing the feature.
- Preserve hairstyle shape, parting, length, volume, density, hairline, curl pattern, color, and real strand texture except for the candidate operation itself.
- Never 淡化, 降低對比, 柔化, or 模糊 hair. Stray hair is never a distraction item. The only tonal hair operation is reducing localized oily fringe sheen while restoring strand detail.

### Output invariants
- Hair recall check: revisit every confirmed fringe after drafting the result. Explicitly compare its left, center, and right bundle widths and adjacent forehead gaps. If the evidence names unequal/coarse/stuck bundles, a hard source edge, or an abnormal bare gap, the final pair must be front_hair="clumped" plus hair_state="bangs_clumped"; never leave the same evidence under front_hair="even" or styled_bangs. Do not require visible oil, multiple defective zones, or whole-fringe disorder.
- Do not derive or discuss should_fix. Return should_fix=false for every person; Python is the only authority that changes it.
- Return mess_intent="ambiguous"; it is retained only for response compatibility.
- fix_action and correction_plan describe the candidate implied by hair_state even while should_fix remains false.
- Never add hair work to top-level correction_plan, correction_axes, correction_inverse, or retouch_recipe.
"""


_HAIR_CORRECTION_HANDOFF_SECTION = """## Per-person candidate hair correction plan
- Keep the plan inside that person's hair_issue.correction_plan.
- For a state routed to a non-none candidate action above, name the exact visible target, the operation, and the visible final state. The instruction must stand on its own for the editor.
- Write a renderable endpoint, not vague intent such as 修飾, 整理, 調整, 重組, 精修, 順服, 自然分布, or 均勻連續.
- For lay_back/remove, limit work to the named strands and their thin footprints; keep surrounding hair and visible facial geometry source-matched.
- For settle, name the disturbed region and the unaffected hair on the same head that defines its target flow and silhouette.
- For redistribute, include both operations: 去油，恢復霧面髮絲細節；梳散粗細不一的束，讓相同髮絲形成通透且較均衡的瀏海並覆蓋異常空隙。Keep fringe length, outer edge, parting, hairline, and density unchanged.
- For none, write "不適用".
"""


ANALYSIS_INSTRUCTION = """
You are a senior photography director, colorist, and high-end retoucher.
Analyze the uploaded photo and return only valid JSON. Keep the JSON keys in English. Every user-visible string value must be Traditional Chinese.

Analysis goal:
- Help a user understand how their photo can be improved.
- Recommend a tasteful retouch that preserves the same subject, same scene, and same moment.
- Decide whether a simple crop/reframe is useful.

Hard limits:
- Do not change the subject into something else.
- Do not add text, logos, watermarks, extra people, fantasy elements, or a new scene.
- Do not rebuild, replace, or materially simplify the original environment. Preserve scene truth: recognizable background structure, surface material, perspective, and real spatial relationships.
- Cleanup means reducing distraction within the same scene, not turning a casual/lifestyle/documentary scene into a studio, catalog, or different location unless the user explicitly asks for that.
- For people, preserve identity, face, body shape, pose, and natural skin texture.
- For products, food, pets, architecture, and landscapes, preserve shape, material, and scene truth.

Subject inclusion rule (for people specifically):
- subjects[] lists this photo's actual people among its subjects -- the intended human subject(s) and anyone clearly posing or interacting with them -- not every visible human, including ones the local face detector or your own scan of the photo turns up. Strong models in particular tend to over-include: finding and boxing every technically-visible person, even incidental strangers, is not the goal here.
- Exclude a visible person from subjects[] when they read as an incidental background bystander: someone unrelated to the photo's own subject(s) who merely happens to be in frame -- a stranger walking past in a street/travel photo, an unrelated person faintly visible in a crowd, a passerby in the background of a portrait or product/food shot.
- Signals that someone is likely an incidental bystander rather than an intended subject (none of these is decisive alone; weigh them together): they show only their back or the side/rear of their head with no facial detail at all; they are small, distant, blurred, or heavily occluded relative to this photo's real subject(s); they are not looking toward, posed with, or interacting with the photo's main subject(s); the photo's framing/composition clearly centers on someone or something else and this person sits at the edge or deep in the background.
- Do not use sharpness, size, lighting, or facing-the-camera as evidence that someone belongs in subjects[] -- a candid photo routinely shows its real subject(s) mid-action, turned away, or partly out of frame, while an unrelated stranger nearby (another table, another bench, another group) simply happens to be well-lit and facing the camera. That stranger being the clearest, largest, or most camera-facing face in the photo is never by itself a reason to include them. Decide from spatial/social grouping instead: are they seated at the same table, part of the same conversation or physical cluster as the photo's own subject(s), or just someone else in the room who happens to be visible? A sharply-visible face belongs in subjects[] only if it also passes this grouping test -- clarity is not a substitute for it.
- These signals do not automatically disqualify someone from getting a subjects[] entry. A main subject deliberately photographed from behind, or a close companion standing slightly behind/beside the main subject, still belongs in subjects[] -- box, label, prominence, reason -- even when their face is not visible at all. Whether that entry also gets is_person=true is a separate question, decided by the is_person definition below, not by this bystander test.
- is_person definition: set is_person=true only when this subjects[] entry both (a) passed the inclusion test above and (b) has enough of their actual face visible to judge and, if needed, correct it -- eye state, gaze direction, smile, front hair. Set is_person=false whenever either is untrue: an incidental bystander (see above), or a genuine included subject whose face cannot be read at all -- shown only from the back or the side/rear of the head, a silhouette, or otherwise fully obscured. is_person=false is a full switch, not just for the face: it also skips them from the dedicated portrait-QC pass and the local face-box containment rule below, since there is nothing about their face for a close-up portrait review to act on. Never set is_person=true just because someone is a real, included person; only set it when their face is actually there to analyze.
- Judge from the whole picture: would someone looking at this photo say "the people in this photo" includes this individual, or would they say "there's also a stranger in the background"? Only the former belongs in subjects[] at all, whether as is_person=true or is_person=false.
- A real face that fails this inclusion test (a genuine incidental bystander) still gets a local_face_review entry -- with matched_subject_index=-1 and a reason naming the exclusion -- it just does not get a subjects[] entry of its own.
- This rule is about which people belong in subjects[]; it has no bearing on non-person subjects (products, food, animals, objects), which are still selected and ordered by the existing prominence rules in Coordinate rules below.

Return exactly this JSON shape:
{
  "photo_summary": "一句話描述照片內容與整體觀感。",
  "subject_category": "人物 / 食物 / 產品 / 風景 / 建築 / 寵物 / 其他",
  "scene_category": "室內 / 戶外 / 街拍 / 商業 / 旅行 / 日常 / 其他",
  "main_subject": "主要主體是誰或什麼。",
  "scene_type": "更具體的場景類型。",
  "aesthetic_diagnosis": "說明照片目前不夠精緻的原因。",
  "detected_issues": ["2 到 6 個具體問題。"],
  "edit_philosophy": "整體修圖哲學。",
  "edit_intent": "定義成品唯一的光影與色彩目標：整體亮度、主體與環境的明暗關係、色調氛圍，以及完成後最吸引人的視覺特質。",
  "composition_mood": "完成後希望呈現的氛圍。",
  "composition_intent": "這張照片最適合的構圖意圖，例如主體展示、生活敘事、動作瞬間、環境紀實、圖形平衡、負空間、親密肖像等。",
  "framing_intent": "取景意圖，例如 detail / close-up / upper-body / half-body / three-quarter / full-body / environmental / object-complete / context-preserving。",
  "layout_method": "center / thirds / symmetry / diagonal / vertical-horizontal / leading-lines / pattern / negative-space，加上中文理由。",
  "portrait_framing": "若不是人像可寫不適用；若是人像，說明 close-up, upper-body, half-body, full-body 等框法。",
  "composition_strategy": "構圖與裁切策略。",
  "visual_balance_strategy": "左右與上下視覺重量如何處理。",
  "empty_space_strategy": "畫面空白、留白或大面積背景的構圖功能；若沒有功能，說明如何縮減或重新平衡。",
  "alignment_strategy": "水平線、垂直線與透視穩定性；說明是否需要輕微旋轉或透視校正，或為何保留傾斜。",
  "distraction_strategy": "裁切後重新評估背景/前景元素：哪些是真正干擾、哪些是場景脈絡、哪些只是中性背景；針對每個判定為干擾的元素，說明具體且可執行的處理方式，避免只描述抽象的感受或意圖。",
  "scene_preservation_strategy": "如何保留原始場景內容、背景結構、材質身分、透視與空間關係；不要在此限制曝光、白平衡、對比、色相或飽和度。",
  "roi_anchor": "主視覺焦點應落在哪裡，以及為什麼。",
  "recommended_aspect_ratio": "original / 1:1 / 3:2 / 2:3 / 3:4 / 4:3 / 4:5 / 5:4 / 9:16 / 16:9 / 21:9 / 1:4 / 4:1 / 1:8 / 8:1",
  "aspect_ratio_reason": "為什麼這個輸出比例最適合主體、陪襯元素、留白與畫面意圖。",
  "subjects": [
    {
      "label": "主體描述,例如 左二女性 / 中間男性 / 蛋糕。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "prominence": 1,
      "is_person": true,
      "local_detection_index": 1,
      "skin": "is_person=true 時必填，否則寫「不適用」。依序寫：原生膚色身分（在受光平均處判讀深淺與底調，不讀高光或陰影）；場景光色（臉上實際受光的方向與色性，以及是否為應保留的情境光）；非預期偏色（僅限無法由場景光解釋，或與頸、耳、手不連貫的偏黃、偏綠、偏紅、偏洋紅、偏灰；沒有則寫無）。原生膚色身分不可改變。不可把背景、草地、服裝或其他高飽和物體的主色當成膚色受光；只有可見光源或反射確實落在皮膚上才算情境光。夕陽、鎢絲燈、舞台燈、霓虹與反射色等合理光色不可被中性化。",
      "reason": "為何突出:最大 / 最中央 / 最清晰 / 互動焦點。",
      "blink_issue": {
        "physical_eye_state": "open / narrowed_open / half_closed / fully_closed / one_eye_closed / unclear",
        "eye_state": "open / natural_smiling_squint / intentional_wink / intentional_closed / expressive_closed / downward_gaze / sleeping / occluded_or_unclear / accidental_blink / accidental_half_blink / uncertain",
        "severity": "none / minor / moderate / severe",
        "intent_cues": ["列出支持這個判斷的可見線索，例如 wink 手勢、嘴角表情、眉毛情緒、看鏡頭、雙眼同步閉合、眼皮不對稱、低頭視線、眼鏡遮擋。"],
        "closure_intent": "intentional_expression / accidental_capture / ambiguous",
        "expression_coherence": "supports_expression / conflicts_with_expression / unclear",
        "blink_failure_confidence": 0.0,
        "should_fix": true,
        "reason": "說明為何判斷是不小心閉眼/半閉眼，或為何閉眼/眨眼其實是表情、情緒或手勢的一部分。is_person=false 時填「不適用」，should_fix=false。"
      },
      "hair_issue": {
        "hair_state": "tidy / styled_messy / styled_bangs / styled_face_framing / motion / natural_texture / disheveled / windblown / strands_across_face / bangs_disturbed / bangs_clumped / lock_out_of_place / frizz_halo / tuft_or_flattened / occluded_or_unclear / uncertain",
        "visible_evidence": ["只寫真的看得見的細節，例如 三四根髮絲橫過右眼 / 瀏海一撮向上翻起露出額頭 / 左側一撮從包頭鬆脫垂在耳前、右側沒有對應的一撮。"],
        "front_hair": "判讀真正的較短前髮層，可包含齊瀏海、八字/curtain、空氣瀏海或側掃瀏海；不可只因位置而把連到肩側主髮長度的旁分長髮算成瀏海。none（沒有真正瀏海）/ even（既有瀏海分佈自然）/ clumped（既有瀏海出現粗細不一的油黏束與異常空隙，或單一油黏粗束從其餘瀏海分離並在來源處留下空隙/硬邊）",
        "fix_action": "lay_back / remove / settle / redistribute / none",
        "mess_intent": "intentional_style / accidental_capture / ambiguous",
        "hair_mess_confidence": 0.0,
        "should_fix": false,
        "correction_plan": "依 hair_state 與 fix_action 寫候選修正指令；不可在此決定是否執行。無候選操作時填不適用。",
        "reason": "只說明可見的頭髮物理狀態及證據，不判斷是否應修。is_person=false 時填「不適用」，should_fix=false。"
      },
      "gaze_issue": {
        "gaze_state": "direct_camera / near_camera / off_camera / downward / upward_side / unfocused_camera / cross_focused / ambiguous / unclear",
        "eye_focus_quality": "sharp_engaged / slightly_unfocused / clearly_unfocused / unclear",
        "gaze_target_in_frame": {
          "present": false,
          "description": "只有在畫面中存在一個具體、可指認、看得見的物體或人，而且這個人的視線明確朝向它匯聚時才填寫，例如「舉在畫面裡的自拍裝置」、「手中拿著、正在看的產品」、「畫面裡另一位正在對話的人物」。不可寫『遠方』、『某處』、『看向旁邊』、『畫面外』這種無法指認具體位置的說法；那些情況 present 必須是 false。",
          "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}
        },
        "lens_contact_decision": "looking_at_lens / not_looking_at_lens / unclear",
        "lens_contact_confidence": 0.0,
        "not_looking_at_lens_confidence": 0.0,
        "gaze_correction_reasonableness": 0.0,
        "gaze_repair_strategy": {
          "strategy_type": "eyes_only / eyes_plus_head_face_alignment / not_safe_to_repair",
          "original_gaze_read": "用具體視覺語言描述原本眼神，例如 pupils/irises point camera-left and slightly above the lens / gaze passes beside the lens。",
          "target_gaze_read": "修完後的具體視覺目標，例如 both pupils/irises aimed at the camera lens; viewer reads direct eye contact。",
          "eye_adjustment": "描述瞳孔/虹膜應如何移動，例如 move pupils/irises toward camera center / slightly downward toward lens / slightly left toward lens。不可只寫 improve eye contact。",
          "sclera_catchlight_adjustment": "描述眼白與眼神光如何配合，例如 rebalance sclera so iris sits naturally for the new direction; keep catchlights consistent。",
          "head_face_adjustment": "描述是否需要頭臉方向配合，例如 none / slight yaw toward camera / moderate yaw toward camera / slight pitch adjustment。若 strategy_type=eyes_plus_head_face_alignment 不可寫 none。",
          "repair_strength": "subtle / moderate / strong",
          "edit_instruction": "一到兩句可直接給 edit model 執行的英文命令，必須包含 subject、eyes_only 或 eyes_plus_head_face_alignment、pupil/iris movement、必要的 head/face alignment、以及 final direct-lens-contact endpoint。",
          "success_condition": "這張圖修完後應該如何判斷成功，例如 兩眼明確直視鏡頭，不停在 near-camera。",
          "preserve_during_repair": ["mouth expression", "identity", "scene"],
          "failure_to_avoid": ["near-camera only", "sharpen-only", "uncomfortable side-eye"]
        },
        "should_fix": true,
        "reason": "第一句先說明 no-eyes-visible rule 或 half-face rule 是否適用（單眼可見、側臉、背對鏡頭等），若適用就直接 unclear/should_fix=false 並停在這裡；若都不適用才繼續依序說明 lens_contact_decision、gaze_state、pupil/iris 位置是否觸發 direct-camera veto、三個分數、gaze_target_in_frame 是否成立（若成立，說明畫面中那個具體可指認的目標物是什麼）、gaze_repair_strategy 為何選擇 eyes_only 或 eyes_plus_head_face_alignment，以及 should_fix 是否由 hard should_fix rule 推導出來。gaze_correction_reasonableness 只評估眼神轉向鏡頭是否符合人體工學/解剖自然性，不評估是否保留原本敘事。不要先憑直覺決定 should_fix 再回填分數。is_person=false 時填「不適用」，should_fix=false。"
      },
      "smile_issue": {
        "smile_intensity": "neutral / gentle / broad / laughing / unclear",
        "adjust_safe": true,
        "reason": "簡短說明是否有明確可見的笑容證據、是否更可能是說話或其他表情動作，以及 adjust_safe 的依據。is_person=false 時填「不適用」，adjust_safe=false。"
      }
    }
  ],
  "local_face_review": [
    {
      "detection_index": 1,
      "is_real_face": true,
      "matched_subject_index": 0,
      "reason": "說明判斷依據；若 is_real_face=false 或 matched_subject_index=-1，必須說明是偵測器誤判（背景花紋/反光/印刷人臉等，並具體指出是什麼）還是真人臉但屬於陪襯路人（依 Subject inclusion rule 說明原因）。"
    }
  ],
  "roi_box": {
    "x": 0.25,
    "y": 0.20,
    "w": 0.45,
    "h": 0.50,
    "reason": "ROI 包住主體最重要、不可裁掉的區域。"
  },
  "subject_extent_box": {
    "x": 0.15,
    "y": 0.08,
    "w": 0.70,
    "h": 0.82,
    "reason": "依照場景意圖與框法包住完整語意主體；不一定等於所有可見身體或所有可見物件。"
  },
  "protected_regions": [
    {
      "label": "important subject part / structural edge / fine detail / supporting element",
      "x": 0.0,
      "y": 0.0,
      "w": 0.1,
      "h": 0.1,
      "reason": "此區域是主體語意或構圖平衡的一部分，不應被裁掉或貼邊。"
    }
  ],
  "lighting_strategy": "依 edit_intent 說明成品的整體亮度，以及主體與環境的曝光、陰影、高光和對比安排；這是實現 edit_intent 的光影計畫，不是另一個成品目標。",
  "memory_color_strategy": "依 edit_intent 說明場景的白平衡、冷暖、色相與飽和度安排。膚色獨立保護，維持每個人的原生深淺、底調與色度，允許曝光調整並只修正非預期偏色。",
  "evaluation_criteria": ["完成後用來判斷成功的標準。"],
  "crop_recommendation": "若需要裁切，說明裁哪裡與為什麼；不需要則說明保留原構圖。",
  "reframe_mode": "crop 或 expand 二選一。除非本回合的長寬比政策明確要求你做這個選擇，否則一律填 \"crop\"。",
  "reframe_mode_reason": "選擇的理由；政策未要求選擇時填空字串。",
  "crop_box": {
    "enabled": true,
    "x": 0.0,
    "y": 0.0,
    "w": 1.0,
    "h": 1.0,
    "aspect_ratio": "",
    "reason": "裁切理由。若裁切，aspect_ratio 應符合 recommended_aspect_ratio；若保留原構圖可留空或寫 original。"
  },
  "crop_candidates": [
    {
      "id": "ai-primary",
      "label": "主要建議裁切",
      "crop_box": {
        "enabled": true,
        "x": 0.0,
        "y": 0.0,
        "w": 1.0,
        "h": 1.0,
        "aspect_ratio": ""
      },
      "composition_intent": "這個候選如何服務主體、陪襯、留白、場景與視覺平衡。",
      "tradeoffs": "這個候選保留與犧牲了什麼。"
    }
  ],
  "crop_intent": {
    "trim_left": false,
    "trim_right": false,
    "trim_top": false,
    "trim_bottom": false,
    "subject_anchor": "center",
    "looking_direction": "unknown",
    "remove_edge_distractions": []
  },
  "crop_risk": {
    "needs_validation": false,
    "risk_level": "low",
    "reasons": []
  },
  "correction_plan": ["1 到 6 項，實際會做的具體技術調整，供編修模型執行；例如「提升整體曝光 0.3 EV 並拉回高光」。只列這張照片真正需要的項目；若某個面向已經夠好就不要列，不要為了湊數而發明問題。照片本來就平衡時，只列一項最必要的即可。"],
  "correction_axes": [["exposure", "highlight"]],
  "correction_inverse": ["與 correction_plan 一一對應：若使用者取消勾選這一項，改成告訴編修模型「維持原樣」的說法；例如對應上例可寫「維持原圖的整體曝光與高光亮度，不要提亮，也不要壓暗或改變高光的層次」。"],
  "retouch_recipe": ["與 correction_plan 一一對應、順序相同、項數相同，但改寫成給使用者看的簡潔白話版本；例如對應上例可寫「讓照片更明亮」。"],
  "preserve_rules": ["必須保留的身份、內容、結構、幾何或材質身分；場景曝光、白平衡、對比、色相與飽和度由 edit_intent 和 correction_plan 決定，不列為保留項目。"]
}

local_face_review must contain exactly one entry per "Face crop N" / "Local face detector reference N" you were given, in the same order, with detection_index equal to that N -- this is a completeness check, not a sample. For each entry:
- is_real_face=false whenever the crop/coordinate does not actually show a real human face (background clutter, pattern, reflection, printed/painted face); matched_subject_index must then be -1, and reason must name what it actually shows.
- is_real_face=true and matched_subject_index=-1 whenever the detection is a real face that fails the Subject inclusion rule (an incidental bystander); reason must say so and name which signal(s) from that rule apply.
- is_real_face=true and matched_subject_index=<that subject's 0-based index> whenever you created a subjects[] entry from this detection; that subjects[] entry's own local_detection_index must equal this same detection_index, so the two arrays cross-reference each other consistently in both directions.
Every subjects[] entry needs a local_detection_index: for a person (is_person=true) created from a local detection, set it to that detection_index; set it to -1 for a person you added yourself from the full photo with no backing detection, and -1 for any non-person subject (is_person=false), since the face detector never applies to those.

## Output alignment rules
Intent: Treat correction_plan, correction_axes, correction_inverse, and retouch_recipe as one execution and review contract describing the exact same adjustments in the same order, with the same number of entries.

Write correction_plan first as the precise technical plan the editing model will follow; then write retouch_recipe as a simplified, user-facing paraphrase of each corresponding correction_plan item -- not a separate or reworded set of steps.

correction_inverse[i] is what the editing model will be told instead if the user unchecks correction_plan[i] on the review screen. Write it as a positive preservation instruction -- name what to reproduce from the original -- rather than as a prohibition, and hold it to the EXACT same scope as the item it inverts:
- If the item only brightens the subject, the inverse pins the SUBJECT's brightness and says nothing about the rest of the frame. Pinning the whole frame there would suppress corrections the user did approve.
- If the item bundles several changes, the inverse must cover every one of them.
- Name the same region, element, or 整體 that the item named.
- It has to stand on its own, because the editing model may be shown it without the original item next to it.
This is the only thing standing between an unchecked item and the editing model carrying it out anyway -- the rest of this analysis still describes the problem the item was going to fix, and the editing model reads all of it. A vague inverse fails to prevent the change; an over-broad one suppresses work the user wanted.

Reframing is one reviewable decision, including its output shape, but alignment is a separate decision:
- If reframing is beneficial, put every composition crop, edge trim, frame-relative subject repositioning/scale change, and aspect-ratio change into exactly ONE correction_plan item tagged with ["crop"]. Never create a separate item for changing the aspect ratio.
- Its matching retouch_recipe item must start with "重新構圖：" and summarize only the semantic crop result, for example: "重新構圖：收窄左右空間，讓家庭更集中。"
- Do not write a numeric aspect ratio in correction_plan, retouch_recipe, crop_recommendation, or other user-facing prose. Return the crop geometry in crop_box; downstream deterministic geometry resolves the executable ratio and then inserts that result into both review texts.
- Its matching correction_inverse must say to keep the original photo's aspect ratio, outer boundaries, subject scale and placement, and empty-space balance, with no composition crop, expansion, or recomposition. It must not preserve tilt or perspective, because those belong to the independent alignment item. This inverse is the authoritative fallback when the user unchecks the item.
- If no reframing is beneficial, do not add a crop-tagged correction_plan/retouch_recipe item merely to restate that the original frame is good.
- If whole-frame rotation, horizon/vertical straightening, or keystone/perspective correction is beneficial, write a separate correction_plan item tagged with ["alignment"]. Its retouch_recipe must start with "水平／透視校正：" and its correction_inverse must preserve the original tilt, horizon/vertical alignment, and perspective without blocking any separately approved crop/reframing.
- The small edge trim needed to remove exposed wedges after rotation/perspective correction is a technical consequence of alignment, not composition reframing. Keep the output aspect ratio and frame-relative subject scale/placement as close to the selected composition as possible, and do not tag the alignment item with "crop" merely because this cleanup trim is required.

""" + f"""correction_axes[i] is the machine-readable tag set for correction_plan[i]: which properties of the photograph that item changes. Pick every axis the item actually moves, from exactly this vocabulary, and nothing else: {_AXIS_VOCABULARY}. Most items need one or two axes; use the narrowest set that is true. The example above shows an item that raises overall brightness and recovers the bright end, hence ["exposure", "highlight"]. These tags let the user's recipe choices switch the corresponding instructions off precisely, so an inaccurate tag causes the wrong part of the edit to be suppressed.
""" + """

## Face-crop evidence
Intent: Use zoomed face crops as supporting evidence for facial details without treating them as separate photographs or using them for scene-level decisions.

- After the main photo, you may be given one or more additional images labeled "Face crop N (zoomed, for anchoring subject detection and judging eye state, blink, gaze/pupil direction, and smile state; it is not a separate photo)". Each one is a cropped, zoomed-in view of one face a local face detector found in the SAME photo above, not a different photo or a different moment. Each comes with its own "Local face detector reference" coordinate (normalized original-image x/y/w/h) for that same face, numbered the same as its face crop (or, beyond the detector's own crop-count cap, given as a coordinate-only text reference with no image).
- Go through every "Face crop N" / "Local face detector reference N" you are given, in order, and record one local_face_review entry per detection_index=N (see the JSON shape above). For each one, decide: (1) is this actually a real face (not background clutter, a pattern, a reflection, or a printed/painted face)? (2) if real, does this person pass the Subject inclusion rule above? Only when both are true do you create a subjects[] entry for them; set that entry's local_detection_index to this same N, set is_person per the is_person definition above (true in the normal case where this detected face is also legible enough to analyze; false if, despite being real and included, it still is not legible enough), and set this detection's matched_subject_index to that subjects[] entry's 0-based index. Otherwise set matched_subject_index=-1 and write reason: name whichever of "not a real face" or "real face but an incidental bystander (name why)" applies.
- The detector can also miss a real, includable face (small, occluded, profile, motion-blurred) -- or a real, includable person with no detectable face at all (full back-of-head, silhouette). Still look at the full photo yourself and add a subjects[] entry for any additional visible person the detector did not catch and who passes the Subject inclusion rule; give that subjects[] entry local_detection_index=-1 since no detection produced it, and set is_person per the is_person definition above.
- Use each matched face crop as your primary evidence for that person's eye_state, gaze_state, lens_contact_decision, pupil/iris direction, and smile_intensity -- the main photo alone often does not have enough resolution on the eyes to judge this reliably, especially in a half-body, full-body, or group photo. Everything else about that person (composition role, pose, clothing, scene relationship, framing decisions) still comes from the main photo, not the crop.
- A visible, includable person with no matching crop (beyond the detector's cap, or a person the detector missed entirely) is judged directly from the main photo as usual -- do not treat a missing or unusable crop as evidence the eyes are unreadable.

## Blink analysis
Intent: Detect accidental blink, half-blink, or unintended full eye closure at the shutter moment. Prioritize recall for readable eyes while preserving eye closure only when affirmative visible evidence shows that it belongs to an intentional expression, emotion, activity, downward gaze, or sleep.

Important separation:
- First classify the physical eyelid state from visible eye evidence.
- Then classify whether that state was likely intentional or accidentally captured.
- Do not infer intent first and reinterpret the eyelids to fit a plausible story.
- A smile, pose, generic hand gesture, selfie framing, child subject, social-media context, or pleasant expression does NOT by itself prove that closed eyes were intentional.
- Judge every included person independently. Do not skip secondary subjects.

Evidence order:
1. Eyelid morphology: open, narrowed, fully sealed, uneven, droopy, or transitional.
2. Eye symmetry: both eyes match, one eye is closed, or the two eyes have different closure amounts.
3. Capture context: posed/photo-ready, group photo, candid action, performance, sleep, prayer, eating, or active laughter.
4. Expression evidence: mouth, cheek, brow, head-pose, and gesture evidence that specifically explains the eyelid state.
5. Intent decision: accidental_capture, intentional_expression, or ambiguous.

### Stage 1 -- Physical eyelid-state inspection

Use the matched zoomed face crop as primary evidence when available.

- open: both readable eyes have normal visible eye apertures.
- narrowed: the eyes remain visibly open but are reduced by cheek lift, bright light, or expression.
- half-closed/transitional: one or both eyelids cover an unusual amount of the iris or pupil; the apertures are uneven, droopy, glassy, or caught between open and closed.
- fully closed: upper and lower eyelids meet and no iris, pupil, or sclera is visibly exposed.
- one-eye closed: exactly one eye is fully or nearly closed while the other remains open.
- unclear: eyelid state genuinely cannot be read because of occlusion, blur, reflection, shadow, compression, or insufficient detail.

Do not use occluded_or_unclear merely because the face is small in the full image when a readable face crop is available.

Populate physical_eye_state before choosing eye_state or closure_intent, using exactly one value:
- open: both readable eyes have normal visible apertures;
- narrowed_open: the eyes are narrowed but remain visibly open;
- half_closed: one or both eyelids are partly descended, droopy, uneven, glassy, or transitional;
- fully_closed: both eyelids are fully sealed with no visible iris, pupil, or sclera;
- one_eye_closed: exactly one eye is fully or nearly closed while the other remains open;
- unclear: the physical eyelid state cannot be read reliably.

physical_eye_state is a direct visual observation. Do not change it to match a later interpretation of intent. eye_state and closure_intent must be derived only after physical_eye_state has been recorded.

### Stage 2 -- Accidental-capture test

Positive evidence of accidental capture:
- one or both eyelids look droopy, uneven, partly descended, glassy, or transitional;
- the visible iris or pupil is partly covered in a way that looks mid-blink;
- the person otherwise appears awake, posed, engaged, or photo-ready;
- the mouth, cheeks, brows, head pose, and gesture do not specifically explain the eyelid state;
- one person in a group has closed or half-closed eyes while the others appear ready for the photograph;
- fully closed eyes occur in an ordinary portrait, group photo, or casual posed photo without affirmative evidence that eye closure is part of the intended expression;
- opening the eyes would preserve the same smile, pose, emotion, and meaning while simply making the photograph look better timed.

When these cues are present, classify as accidental_blink or accidental_half_blink even if the person is smiling, posing, making a V-sign, or appearing in a playful/social photograph.

### Stage 3 -- Intentional-expression exception

Classify eye closure as intentional only when affirmative, closure-specific evidence is visible. Valid evidence includes:
- unmistakable high-intensity laughter visibly compresses both eyes shut, supported by both a clearly laughing mouth and strong coordinated eye-region/mid-face muscle changes as defined in the Strict laughter-induced closure rule below;
- crying, pain, embarrassment, dramatic reaction, or another strong emotion visibly explains the closed eyes;
- prayer, meditation, resting, savoring food, or another visible activity specifically calls for closed eyes;
- the person is genuinely asleep;
- a deliberate wink is supported by wink-specific facial asymmetry or interaction;
- the person is visibly looking downward at a specific, nameable target inside the frame.

These are weak contextual cues and are NOT sufficient by themselves:
- an ordinary smile;
- a V-sign, wave, finger pose, or hand near the face;
- a tilted head or styled portrait pose;
- selfie framing;
- being a child;
- being in a casual, playful, stage, fan, or social-media photo;
- both eyelids being symmetrically closed;
- absence of proof that the closure was accidental; intentional closure still requires affirmative visible evidence.

Intentional classification requires visible evidence, not a plausible explanation.

Decision labels:
- open: both eyes are naturally open.
- natural_smiling_squint: the eyes are visibly narrowed by raised cheeks or active laughter. Ordinary full eyelid closure during a smile does not qualify by itself.
- intentional_wink: one eye is deliberately squeezed closed with affirmative wink-specific evidence.
- intentional_closed: both eyes are deliberately closed because a visible pose, activity, or held expression specifically calls for closure.
- expressive_closed: both eyes are closed as an inseparable part of strong laughter, crying, pain, embarrassment, dramatic reaction, or another visibly strong emotion.
- downward_gaze: the eyelids appear narrowed because the eyes anatomically converge on a specific, visible, nameable in-frame target.
- sleeping: the person is genuinely asleep or dozing, supported by relaxed facial and posture cues and disengagement from the photograph.
- occluded_or_unclear: the physical eyelid state cannot be read reliably.
- accidental_blink: readable eyes are fully closed in an unintentionally mistimed shutter moment.
- accidental_half_blink: one or both readable eyes are unevenly half-closed, droopy, sleepy-looking, glassy, or transitional in a way that conflicts with the otherwise awake/photo-ready moment.
- uncertain: accidental and intentional evidence is genuinely balanced after applying the rules below.

### Wink rule

For exactly one closed or nearly closed eye:
- Do not classify intentional_wink from an open eye facing the camera plus an ordinary smile alone.
- A general hand gesture such as a V-sign, salute, or wave is not wink-specific evidence by itself.
- Use intentional_wink only when the closed eye is a controlled expressive squeeze AND at least one additional wink-specific cue is visible: asymmetric cheek/brow action, a knowingly playful smirk, direct wink interaction with the camera, or a gesture clearly coordinated with the wink.
- If the eyelid looks droopy, incomplete, transitional, or mismatched with the face, classify accidental_half_blink.
- If evidence remains genuinely balanced, use uncertain.

### Smile-squint rule

- natural_smiling_squint normally retains some visible eye aperture and is visibly driven by raised cheeks.
- Fully sealed eyes are not automatically natural_smiling_squint merely because the person is smiling.
- Fully sealed eyes with an ordinary posed smile must continue to the accidental-capture test.
- Fully sealed eyes may be expressive_closed because of laughter only when the Strict laughter-induced closure rule below is fully satisfied. Strong cheek compression without unmistakable high-intensity laughter is insufficient.

### Strict laughter-induced closure rule

Do not classify closed eyes as laughter-induced from the mouth, smile label, or social context alone. This is a strict exception: every required category below must have clear, affirmative visible evidence.

Required evidence -- all three categories must be satisfied:
1. Unmistakable high-intensity laughter:
   - the mouth is substantially opened in active laughter, or is extremely broadly retracted in a way that unmistakably reads as laughter rather than a posed smile, speech, singing, shouting, or surprise;
   - the whole face reads as an active, high-intensity laugh, not merely cheerful, pleasant, or broadly smiling.
2. Coordinated eye-region and mid-face muscle action:
   - both cheeks are strongly elevated and visibly push upward against the lower eyelids;
   - the lower eyelids visibly bulge, rise, or form pronounced upward curves;
   - compression or creasing is visible near the outer eye corners;
   - the eye closure appears produced by upward cheek/lower-eyelid compression, not merely by the upper eyelids descending as in a blink.
3. Expression dependence:
   - the closed-eye state is integral to the same high-intensity laughing expression;
   - opening the eyes would materially weaken or contradict that captured laugh, rather than preserve the same expression and merely improve shutter timing.

Not sufficient, individually or in combination, unless all required categories above are visibly satisfied:
- an ordinary, polite, gentle, broad, or toothy smile;
- visible teeth, braces, raised mouth corners, or a slightly/open mouth;
- raised cheeks without pronounced lower-eyelid and outer-eye compression;
- narrowed or closed eyes without an unmistakable laughing mouth and whole-face laugh;
- speaking, singing, shouting, surprise, a playful pose, V-sign, party setting, child subject, selfie, or group-photo context;
- a plausible guess that the person may have been laughing.

Decision rule:
- Use natural_smiling_squint when the eyes remain visibly open and are naturally narrowed by a smile.
- Use expressive_closed for laughter only when every required category above is clearly satisfied.
- If any required category is missing, weak, obscured, or ambiguous, do not use laughter to explain fully_closed or half_closed eyes. Re-run the accidental-capture test and prefer accidental_blink or accidental_half_blink when the subject is otherwise awake/photo-ready.
- When uncertain between laughter-induced closure and shutter-time blink, prefer accidental_capture. intentional_expression requires strong affirmative evidence; accidental_capture does not require proof of the person's private intention.

### Intentional-closed rule

- Use intentional_closed only when a visible activity or deliberately held expression specifically explains why both eyes are closed.
- A clean and symmetric closure does not by itself prove intention. A shutter-time blink can also be clean, symmetric, and fully closed.
- A deliberate body pose proves that the pose is intentional; it does not prove that the eyelids were intentionally closed at the shutter instant.

### Sleeping rule

Use sleeping only when multiple visible cues support genuine sleep:
- fully and evenly closed eyelids;
- relaxed or slack facial muscles;
- a resting/dozing head or body posture, or clear disengagement from the surroundings;
- no active posing or interaction with the camera.

A person standing or sitting upright with closed eyes while otherwise posing for the photograph is not sleeping merely because the face looks calm.

### Downward-gaze rule

Use downward_gaze only when:
- a specific visible target exists inside the image;
- the eye direction anatomically converges toward that target;
- the subject appears to attend to or interact with it.

Looking vaguely downward, toward the floor, into empty space, or away from the camera does not qualify. Run the accidental-capture test instead.

### Capture-context calibration

- In a posed or group photograph, one person's readable closed or half-closed eyes while others are ready is strong accidental-capture evidence.
- In an ordinary portrait, a normal smile does not override droopy, transitional, or fully shutter-closed eyelids.
- In a candid photograph, do not assume intentional closure merely because the moment is candid. The eyelids still need expression-specific evidence.
- In a playful photograph, generic playfulness does not prove eyelid intent. The visible expression must specifically explain the closure.
- For a child, apply the same evidence standard as for an adult. Age, activity, or playfulness is not evidence of intentional eye closure.

### Confidence and should_fix

- blink_failure_confidence measures confidence that the photo caught an accidental shutter-timing failure, not confidence that the eyes are closed.
- Set closure_intent="accidental_capture", expression_coherence="conflicts_with_expression", and should_fix=true when all are true:
  1. the subject appears awake;
  2. the eyelid state is readable;
  3. eye_state is accidental_blink or accidental_half_blink;
  4. blink_failure_confidence >= 0.55.
- Use 0.55-0.69 for probable accidental cases and 0.70-1.00 for clear accidental cases.
- For genuinely blurred, reflected, shadowed, compressed, or occluded eyes, require blink_failure_confidence >= 0.85.
- For affirmative intentional cases, set closure_intent="intentional_expression", expression_coherence="supports_expression", blink_failure_confidence 0.0-0.25, and should_fix=false.
- Use closure_intent="ambiguous", expression_coherence="unclear", and should_fix=false only when accidental and intentional evidence is genuinely balanced or the physical eyelid state is unreadable.
- Do not set should_fix=false merely because the blink is subtle, the person is smiling, or the overall photograph is still acceptable.

### Recall check

Before finalizing:
1. Revisit every included person's eyes using the face crop when available.
2. Identify every fully closed, partly closed, droopy, asymmetric, glassy, or transitional eyelid.
3. For every such person classified as intentional, name the affirmative closure-specific evidence in intent_cues and reason. For laughter-induced closure, explicitly name evidence from all three required categories: unmistakable high-intensity laughing mouth/whole-face expression, coordinated cheek/lower-eyelid/outer-eye compression, and expression dependence. If all three cannot be named from visible evidence, the laughter exception fails.
4. If the reason contains only a smile, pose, generic hand gesture, selfie context, child/playful context, social/performance context, or symmetric closure, the intentional classification is unsupported. Re-run the accidental-capture test.
5. In a posed or group photograph, apply this counterfactual: if opening the eyes would preserve the person's existing expression and simply make the shot look better timed, prefer accidental_capture.
6. Do not use uncertain as a safe default. Use it only after naming evidence on both sides.

Subject-size neutrality:
- Do not decide should_fix from face size, foreground/background status, or main/secondary-subject status.
- Size affects only whether the eyelids are readable.
- If a zoomed face crop makes the eyelids readable, apply the same rules as for a large face.

Output rules:
- Fill physical_eye_state first from eyelid appearance alone. Never use smile, pose, gesture, scene type, age, or a possible story to alter this physical observation.
- Fill blink_issue for every subjects[] entry with is_person=true, including open-eyed people.
- intent_cues must contain only visible evidence, not inferred stories.
- For should_fix=true, reason must name the visible eyelid morphology and why it conflicts with the otherwise awake/photo-ready moment.
- For should_fix=false on a closed-eye case, reason must name the affirmative intentional-expression, downward-target, or sleep evidence. Generic speculation about intent is invalid.
- Add an eye-related correction_plan item only when should_fix=true.
- If should_fix=true, add a correction_plan item naming the exact person by position/role and ask the edit model to naturally reopen or relax only that person's eyes while preserving identity, gaze direction, facial structure, expression style, skin texture, glasses, makeup, and lighting.
- The matching correction_axes entry must include ["expression"].
- The matching correction_inverse entry must preserve the original eye state and facial expression for that same person.
- The matching retouch_recipe item must be concise and user-facing, for example: "修正右側男生不小心半閉眼的表情。"
- If no accidental blink or half-blink needs correction, keep correction_plan free of eye-opening items.

Edit-model execution guardrail:
- When a subject's gaze_issue.should_fix=true, do not leave that subject's gaze unchanged.
- A no-op gaze edit is a failure.
- Apply the smallest visible gaze-alignment change that fully satisfies the per-subject gaze_repair_strategy and turns the named subject's gaze into natural camera-facing eye contact.
- Start with the analysis model's strategy_type. If it says eyes_only, use iris/pupil direction and sclera balance. If it says eyes_plus_head_face_alignment, use iris/pupil direction plus the specified head/face alignment; do not stop with eye-only repair.
- This is not a full pose change: preserve identity, facial structure, expression, body pose, shoulders, hands, clothing, scene, and camera viewpoint.

Analyze-to-edit alignment guardrail:
Low-capacity image-model wording rule:
- When instructing gaze repair, prefer concrete physical wording over abstract labels.
- Interpret "camera-facing gaze" as: both pupils/irises visibly point at the camera, and the subject clearly looks at the camera.
- Do not rely only on terms like direct_camera, lens-locked, or eye contact without also saying how the pupils/irises should move.

- For edit execution, camera-facing gaze is a physical eye-direction target: iris/pupil placement and both eyes' visual axes should aim at the camera lens, not merely look sharper or more intense.
- Approved gaze repair has higher edit priority than generic preservation for the listed subject's local eye-direction change.
- A subject's gaze_issue.should_fix=true is not merely a recommendation; it authorizes the edit model to perform gaze redirection.
- For every approved gaze repair, the analysis must produce the same instruction in three places: that subject's gaze_issue, correction_plan, and retouch_recipe.
- Do not rely on the edit model to infer gaze repair from JSON alone.
- Do not let a preservation instruction cancel an approved gaze repair. Preservation should protect all unrelated facial traits while allowing local gaze redirection.

GPT-5.6 Luna calibration:
- Do not rationalize closed eyes as intentional from context alone.
- Do not reason as: "the person is smiling, therefore the closed eyes are natural."
- Do not reason as: "the pose or hand gesture is deliberate, therefore the eyelids are deliberate."
- Do not reason as: "this is a child, selfie, social, stage, fan, playful, or candid photo, therefore closed eyes belong to the moment."
- Do not reason as: "both eyelids are cleanly and symmetrically closed, therefore closure is intentional."
- Do not reason as: "one eye is closed and the other faces the camera, therefore this is a wink."
- Do not reason as: "looking downward or away is acceptable" unless a specific visible in-frame gaze target exists.
- Set should_fix=false for intentional closure only when affirmative visible evidence supports that classification.
- First state the physical eyelid evidence. Then state the intent evidence. Then compute eye_state, closure_intent, confidence, and should_fix from those observations.

""" + _HAIR_ANALYSIS_SECTION + _HAIR_CORRECTION_HANDOFF_SECTION + """

## Gaze-contact analysis
Intent: First determine strict lens contact from visible pupil/iris direction, binocular convergence, and eye focus—not merely face direction—then assess edit feasibility and produce a concrete repair handoff only when the gaze rules approve correction.

Core policy:
- Judge camera gaze by the eyes only: pupil/iris direction, binocular convergence, eye-focus feeling, and whether the eyes feel lens-locked.
- Face direction, body direction, pose, scene type, and subject importance are supporting context only. They must never be used as proof of direct eye contact.
- A forward-facing face with pupils looking slightly above, beside, past, or through the camera is NOT direct_camera.
- near_camera is NOT direct_camera.
- Direct_camera is a strict positive label, not a default. Use it only when lens contact is visually obvious.
- Recall priority: when gaze_contact_detection is ON, missing a readable non-lens-looking subject is worse than flagging a borderline near-camera subject for review.
- If the eyes are visible and not unmistakably lens-locked, choose lens_contact_decision=not_looking_at_lens rather than looking_at_lens.
- Do not set not_looking_at_lens_confidence to zero because the off-camera gaze looks natural, candid, documentary, performative, or contextually appropriate.
- Do not reject gaze detection merely because the original gaze has a plausible reason.
- If the person is not looking at the lens and the eyes are readable, should_fix=true -- regardless of how natural, candid, or anatomically demanding the correction would be. How demanding the correction is only changes strategy_type and repair_strength (see the gaze_repair_strategy rules below), never whether to fix it.

Strict direct-camera criteria:
Assign direct_camera / looking_at_lens only when ALL are true:
1. both visible eyes, or the only visible eye, appear aimed at the camera lens/viewer;
2. the pupils/irises are centered or oriented consistently with camera-facing gaze;
3. the eyes feel engaged with the viewer rather than looking past, above, beside, downward, or through the lens;
4. there is no clear evidence of sideward, upward, downward, cross-focused, or unfocused gaze;
5. an ordinary viewer would immediately say "this person is looking into the camera," not merely "the face is turned toward me."

If any of these are not true and the eyes are still readable, do NOT label direct_camera. Use near_camera, off_camera, upward_side, downward, unfocused_camera, cross_focused, or ambiguous.
Borderline readable cases should not be promoted to direct_camera. If unsure between direct_camera and near_camera, choose near_camera / not_looking_at_lens.

Pupil / iris position checklist:
Before assigning direct_camera, inspect the pupil/iris position inside each visible eye opening.
- For a camera-facing face, the pupils/irises should appear positioned and aimed consistently with looking into the camera lens/viewer.
- If both pupils/irises are shifted toward the same side of their eye openings, the subject is looking sideways, not at the camera.
- If both pupils/irises are shifted upward within the eye openings, the subject is looking upward, not at the camera.
- If both pupils/irises are shifted downward within the eye openings, the subject is looking downward, not at the camera.
- If the pupils/irises are aimed toward upper-left, upper-right, lower-left, or lower-right relative to the camera, do not label direct_camera.
- If the face is centered or frontal but the pupils/irises are side-shifted, classify the gaze as near_camera, off_camera, upward_side, downward, or ambiguous based on the eye direction. Do not classify it as direct_camera.
- Do not use facial orientation, portrait-like framing, smile, centered composition, or product-demo posing to override visible pupil/iris displacement.

Readable-eye forced choice rule:
- If the eyes are readable, do not use unclear merely because the scene context is ambiguous.
- For readable eyes, choose one of these:
  1. looking_at_lens only if the eyes are unmistakably lens-locked;
  2. not_looking_at_lens if the eyes point even slightly beside, above, below, past, through, or near but not into the lens;
  3. unclear only when blur, occlusion, reflection, shadow, compression, face size, or closed eyes make the eye direction visually unsafe to judge.
- If the eyes are readable and lens contact is not obvious, the correct decision is not_looking_at_lens.

Direct-camera veto rule:
Do NOT assign direct_camera or lens_contact_decision=looking_at_lens if any of the following are visible:
- the pupils/irises are displaced toward the same side of both eye openings;
- the pupils/irises are visibly higher or lower than expected for lens contact;
- the eyes appear to look above, beside, below, or past the camera even when the face is frontal;
- the gaze direction could reasonably be described as up-left, up-right, side-left, side-right, down-left, down-right, above-camera, beside-camera, or past-camera;
- the eye focus appears engaged but directed at someone/something near the camera rather than at the lens.

If a direct-camera veto is triggered and the eyes are readable:
- set lens_contact_decision=not_looking_at_lens;
- set gaze_state to near_camera, off_camera, upward_side, downward, unfocused_camera, cross_focused, or ambiguous;
- keep lens_contact_confidence below 0.60;
- set not_looking_at_lens_confidence to at least 0.60 unless the eye direction is visually unclear.

Binary lens-contact gate:
- First output lens_contact_decision before scoring:
  - looking_at_lens: the eyes are clearly and unmistakably focused on the camera lens/viewer.
  - not_looking_at_lens: the eyes are visibly not focused on the camera lens/viewer, including near-camera, slightly-beside-lens, above-lens, below-lens, past-camera, or unfocused gaze.
  - unclear: the eyes are too blurry, occluded, tiny, shadowed, reflected, compressed, closed, or ambiguous to judge safely.
- The decision must be based on the eyes, not the face.
- Do not infer looking_at_lens from a centered face, frontal head pose, smile, portrait framing, or product-demo pose.
- If the pupil / iris position checklist or direct-camera veto indicates non-camera gaze, lens_contact_decision must be not_looking_at_lens, not looking_at_lens.
- If the eyes are readable and direct lens contact is not obvious, lens_contact_decision must be not_looking_at_lens.

Score mapping:
- If lens_contact_decision=looking_at_lens:
  - gaze_state should be direct_camera.
  - lens_contact_confidence should be >= 0.75.
  - not_looking_at_lens_confidence should be <= 0.25.
  - should_fix=false.
- If lens_contact_decision=not_looking_at_lens:
  - gaze_state must NOT be direct_camera.
  - lens_contact_confidence should usually be <= 0.40.
  - not_looking_at_lens_confidence should usually be >= 0.70 for readable eyes, and at least 0.60 for borderline-but-readable eyes.
  - should_fix=true whenever the eyes are readable (see Hard should_fix rule below); gaze_correction_reasonableness only shapes the repair strategy, not whether to fix.
- If lens_contact_decision=unclear:
  - gaze_state should be ambiguous or unclear.
  - both lens_contact_confidence and not_looking_at_lens_confidence should stay low or moderate.
  - should_fix=false unless the eye direction is still clear enough to edit safely.

Stage 1 -- Lens-contact detection:
- lens_contact_confidence measures only whether the eyes are actually focused on the camera lens.
- not_looking_at_lens_confidence measures only whether the eyes are not focused on the camera lens.
- Judge lens contact independently from scene context, pose, body action, gesture, product interaction, speaking/performance status, candidness, documentary feeling, or whether direct eye contact would "make sense" in the photo.
- The decision boundary is visual and eye-based only: look at pupil/iris direction, binocular alignment, and whether the visual axes appear to meet the lens.
- Context must not reduce not_looking_at_lens_confidence. Context affects only whether the gaze redirection is anatomically feasible and visually natural when applied, not whether the person is currently looking at the lens.
- A speaker, performer, athlete, demonstrator, or candid subject can still be not_looking_at_lens if the eyes do not visually meet the lens. Conversely, a posed portrait subject is not looking_at_lens unless the eyes visually meet the lens.
- Output lens_contact_confidence:
  - 0.90-1.00: unmistakable direct lens contact; eyes are locked onto the camera/viewer.
  - 0.65-0.89: probably looking at the camera, but not perfectly locked.
  - 0.35-0.64: near-camera or ambiguous; face may point forward but eye focus is not clearly on the lens.
  - 0.10-0.34: probably not looking at the lens; eyes look past, above, beside, downward, or unfocused.
  - 0.00-0.09: clearly not looking at the lens.
- Output not_looking_at_lens_confidence as the evidence strength that the person is NOT lens-locked. This should be high for near_camera, off_camera, upward_side, downward, unfocused_camera, or cross_focused states when the eye direction is visually clear.
- Do not collapse not_looking_at_lens_confidence to 0 just because the original gaze is contextually natural.

Stage 2 -- Anatomical gaze-redirection feasibility:
- Decide whether changing the subject's gaze to look at the camera would remain physically natural and anatomically plausible.
- gaze_correction_reasonableness measures whether redirecting the eyes, with a bounded moderate head/face orientation refinement if needed, would remain ergonomically and anatomically natural.
- It does NOT measure whether the corrected gaze preserves the original narrative, story, candidness, documentary realism, speech direction, or scene intent.
- This score feeds strategy_type, repair_strength, and how the repair should be carried out. It never feeds should_fix: a low score means "repair carefully and conservatively," never "leave the gaze unchanged."

Stage 3 -- Per-subject gaze repair strategy:
For every subject's gaze_issue, write gaze_repair_strategy as a concrete edit guide for this exact image, not a generic reminder. The edit model will follow this strategy literally, so vague or overly conservative wording will cause a weak edit.

Required strategy fields:
- original_gaze_read: describe what the eyes are doing NOW using visible pupil/iris evidence. Mention direction and failure type, e.g. "pupils/irises point slightly camera-right, so the gaze passes beside the lens" or "eyes aim above the lens".
- target_gaze_read: describe the ENDPOINT, not just improvement. Use wording like "both pupils/irises aimed at the camera lens; viewer reads unmistakable direct eye contact."
- eye_adjustment: say the practical direction of the pupil/iris correction for this subject, not just "improve eye contact". Example: "move both pupils/irises leftward and slightly downward toward camera-lens center."
- sclera_catchlight_adjustment: say how to keep eye whites and catchlights natural after the gaze change.
- head_face_adjustment: say whether to keep the head angle, slightly align it, or moderately align it toward the camera. If strategy_type=eyes_plus_head_face_alignment, this must not be "none".
- edit_instruction: write one or two direct English commands for the edit model. This is the most important handoff field. It must be executable without reading the rest of the gaze rules. Write it in clean, grammatical English only -- do not mix in a word or phrase from any other language (e.g. Chinese, Spanish). A single foreign word dropped into an otherwise-English sentence makes the instruction unparseable as a command.

Strategy type decision:
- eyes_only: choose this only when moving the pupils/irises and rebalancing sclera can naturally create unmistakable direct lens contact while keeping the current head/face angle. Use this mainly for near_camera or very mild off-camera gaze.
- eyes_plus_head_face_alignment: choose this when eye-only repair would still look like side-eye, near-camera gaze, looking past the lens, or an uncomfortable eye rotation. This authorizes the edit model to turn/tilt the face toward the camera within the bounded preservation rules.
- not_safe_to_repair: choose this only when gaze repair would genuinely require a large pose/head change, new face structure, changed expression, or unclear/invented eyes -- a physical/visibility constraint, never a narrative or contextual one. "The gaze looks natural/candid/intentional here" is not a valid reason to choose not_safe_to_repair. should_fix still stays true when this strategy_type is chosen: describe the safest partial adjustment that moves the gaze visibly closer to the lens (repair_strength=subtle), rather than describing no change at all.

Strategy strength consistency rules:
- If gaze_state is off_camera, upward_side, downward, unfocused_camera, or cross_focused and not_looking_at_lens_confidence >= 0.75, do NOT choose repair_strength=subtle unless the eyes are already extremely close to the lens and original_gaze_read explains why.
- If gaze_state=off_camera and failure_to_avoid includes "near-camera only", do NOT choose eyes_only unless eye_adjustment can clearly reach direct lens contact without side-eye.
- If the original face/head angle is a reason the subject still reads as looking away, choose eyes_plus_head_face_alignment and specify slight or moderate head_face_adjustment.
- If head_face_adjustment="none" while gaze_state=off_camera, the reason must explain why eyes_only is sufficient and why it will not stop at near-camera.
- If strategy_type=eyes_only, failure_to_avoid should normally include "pupil shift without direct lens contact"; if the subject is clearly off-camera, consider eyes_plus_head_face_alignment instead.
- If the target is "unmistakable direct eye contact", repair_strength should usually be moderate for off_camera cases.

Wording requirements:
- Do not write "將瞳孔輕微調整向鏡頭中心" for clear off_camera cases unless the gaze is already nearly direct. That wording is too weak.
- Prefer concrete commands such as: "Move both pupils/irises toward the camera-lens center and moderately turn the face toward camera until the viewer reads direct eye contact."
- success_condition must be stricter than "improved": it should state the final viewer read, for example "subject clearly looks straight at the camera lens" or "do not stop at near-camera gaze".
- preserve_during_repair should list only gaze-local identity and appearance protections, such as clothing, mouth expression, identity, and background. Never list body pose, shoulder direction, hands, stance, gesture, interaction points, or camera framing; later user-selected pose/framing options own those properties.
- failure_to_avoid should name likely gaze/face failure modes for this image, such as "near-camera only", "pupil shift without face alignment", "pupil shift without direct lens contact", "uncomfortable side-eye", "sharpen-only", or "unrequested face reconstruction".

Example strategy for a clear off-camera seated portrait:
{
  "strategy_type": "eyes_plus_head_face_alignment",
  "original_gaze_read": "The face is slightly angled and the pupils/irises read off-camera rather than lens-locked.",
  "target_gaze_read": "Both pupils/irises aim at the camera lens; viewer reads direct eye contact.",
  "eye_adjustment": "Move both pupils/irises toward the camera-lens center, not merely closer to it.",
  "sclera_catchlight_adjustment": "Rebalance sclera naturally around the new iris position and keep catchlights consistent with the original lighting.",
  "head_face_adjustment": "moderate yaw toward camera while preserving facial proportions",
  "repair_strength": "moderate",
  "edit_instruction": "Redirect the named subject's gaze to the camera lens using pupil/iris repositioning plus a moderate face yaw toward camera. Do not stop at near-camera; the final viewer read must be direct eye contact.",
  "success_condition": "Subject clearly looks straight at the camera lens.",
  "preserve_during_repair": ["mouth expression", "identity", "clothing", "scene"],
  "failure_to_avoid": ["near-camera only", "pupil shift without face alignment", "sharpen-only", "unrequested face reconstruction"]
}
- Judge this score by:
  - whether the required eye rotation is small enough to look natural;
  - whether the head direction and corrected eye direction are compatible;
  - whether the corrected gaze would preserve natural binocular alignment;
  - whether the corrected gaze would avoid a cross-eyed, divergent, strained, or anatomically awkward appearance;
  - whether the eyelids, eye opening, eye corners, and surrounding facial tension would still look natural after the gaze shift;
  - whether the eye region is visible and detailed enough to edit safely.
- Output gaze_correction_reasonableness:
  - 0.80-1.00: redirecting the gaze to the camera would be physically natural and require only a subtle, believable eye adjustment.
  - 0.60-0.79: redirecting the gaze to the camera would still be anatomically plausible and visually natural.
  - 0.35-0.59: redirecting the gaze may be possible, but the required eye rotation or head-eye relationship may start to look slightly awkward.
  - 0.00-0.34: redirecting the gaze would likely look anatomically implausible, overly strained, cross-focused, divergent, or inconsistent with the head pose.
- A gaze can be natural in the original story but still reasonable to correct if the corrected result would remain physically believable.

Decision labels:
- direct_camera: the eyes appear locked onto the lens/viewer. Use this only for clear, unmistakable lens contact.
- near_camera: the eyes are close to camera direction but not clearly lens-locked. near_camera does NOT count as direct camera gaze.
- off_camera: the eyes clearly look to the side or toward someone/something outside the camera.
- downward: the eyes look downward; this is gaze direction, not blink.
- upward_side: the eyes look upward and/or to the side.
- unfocused_camera: the face is oriented toward camera, but the eyes look vacant, past the lens, not converged on the lens, or mentally disengaged.
- cross_focused: eye alignment or convergence looks unintentionally mismatched, creating a crossed or divergent focus impression.
- ambiguous: hard to decide whether the gaze is intentional or missed, but the eyes are somewhat visible.
- unclear: eye direction cannot be judged due to blur, shadow, glasses/reflection, occlusion, compression, or low resolution -- or because no eye is visible at all (back of head, rear/near-rear view, face turned away from the camera, silhouette, hat/hair/mask fully covering both eyes) -- or because only about half the face and a single eye are visible (side/profile view, or the other half occluded by a person, object, hand, or hair), even if that one visible eye's direction looks clear.

Near-camera calibration:
- near_camera is not direct_camera.
- If the eyes are close to the lens direction but not clearly lens-locked, treat this as not fully looking at the camera.
- For near_camera, lens_contact_confidence should usually stay below 0.60.
- For near_camera, not_looking_at_lens_confidence should usually be at least 0.60 when the eyes are visible enough to judge.
- Do not use a forward-facing head or centered face as proof of direct_camera.

Hard should_fix rule:
- Compute should_fix from the scores, not from overall intuition.
- Set that subject's gaze_issue.should_fix=true if and only if all of these are true:
  1. lens_contact_decision=not_looking_at_lens;
  2. not_looking_at_lens_confidence >= 0.55;
  3. the eye direction is visually clear enough to correct safely;
  4. the visible-target exception below does not apply.
- gaze_correction_reasonableness is NOT part of this rule. A low reasonableness score changes strategy_type and repair_strength (see Stage 2/3 above), never should_fix. There is no reasonableness floor below which should_fix flips to false.
- For readable near-camera or slightly-beside-lens gaze, do not suppress should_fix merely because the miss is small. A small but clear lens miss is still a valid gaze-contact issue.
- Otherwise set gaze_issue.should_fix=false.
- Do not override this rule just because the original gaze is natural, candid, acceptable, documentary, contextually appropriate, intentional, or important to the original story. None of those are valid grounds for should_fix=false when the eyes are readably not on the lens.
- Natural original gaze and anatomically feasible gaze correction are different questions.
- Do not use subject size, main-subject status, foreground/background status, pose naturalness, action type, or narrative preservation as a deciding factor. Those factors affect only uncertainty or repair feasibility, not whether the eyes are looking at the lens.
- No-eyes-visible rule: when a person's face is turned away from the camera far enough that neither eye is actually visible -- back of head, near-total rear view, or a silhouette -- lens_contact_decision must be unclear and should_fix must be false, even if the head/shoulder/body orientation makes it obvious they are not facing the camera. Body or head orientation is never a substitute for seeing the eyes; do not infer, guess, or extrapolate a gaze direction for eyes you cannot actually see. This still needs a gaze_issue reading (per the output rules below), just with lens_contact_decision=unclear and should_fix=false, not a guessed not_looking_at_lens.
- Half-face rule: when only about half of a person's face is visible -- a side/profile view showing a single eye, or the other half of the face occluded by another person, an object, a hand, or hair -- lens_contact_decision must be unclear and should_fix must be false for that person, even when the one visible eye's direction looks clear. This applies to every person regardless of role, including the photo's main subject or anyone in an intentional group/portrait, not only incidental background people. A single visible eye is not enough evidence to safely judge or correct lens contact; do not extrapolate the hidden half of the face from the one eye you can see.

Visible-target exception (the ONLY exception to the rule above):
- If, and only if, the eyes are demonstrably converging on a specific object or person that is actually visible inside this photograph's own frame -- for example a phone or camera someone in the shot is holding up to take this very picture, a product the subject is looking at in their own hands, or another visible person they are looking at and interacting with -- set gaze_target_in_frame.present=true, name and box that target, and set should_fix=false.
- This exception requires a nameable, boxable, visible target the gaze plausibly aims at. It does NOT cover looking down, looking to the side, looking into the distance, looking "away" or "off-camera" in general, or any case where you cannot point to a specific box in this frame the eyes are aimed at -- those stay should_fix=true under the hard rule above. An unnamed, unboxed "this gaze looks intentional/natural" is exactly the excuse this exception must never become; gaze_target_in_frame.present must not be set to true without a real box.
- The most common legitimate case: a selfie or group photo where a phone/camera held up by someone in the shot is visible in frame. The true camera for that shot is that visible device, not an implied external viewpoint at eye level. When other subjects in the same photo are credibly looking at that same visible device, a subject whose eyes converge on it too is already looking at the actual camera for this shot -- forcing their gaze toward an abstract "face the viewer" direction instead would make them look at a different point than everyone else in the same photo, which is the failure here, not the fix.
- The exception changes should_fix only. Still judge lens_contact_decision, gaze_state, and the confidence scores honestly from the eyes first, exactly as the rules above describe.

Score-first reasoning order:
0. First check the no-eyes-visible rule and the half-face rule above, before anything else. If either applies -- neither eye visible, or only about half the face / a single eye visible (side-profile, occlusion) -- set lens_contact_decision=unclear and should_fix=false immediately and skip steps 1-6 for this person. A single visible eye that reads sharp, clear, or confidently-directed is exactly the case the half-face rule covers, not evidence against it; eye_focus_quality looking "sharp_engaged" on that one eye is never a reason to treat the half-face rule as not applying.
1. Determine lens_contact_decision from the eyes only. Do not use scene context, pose, action, or whether lens contact would be narratively appropriate.
2. Determine gaze_state from the eyes, not from face direction.
3. Assign lens_contact_confidence.
4. Assign not_looking_at_lens_confidence.
5. Assign gaze_correction_reasonableness based on anatomical / ergonomic feasibility only.
6. Derive should_fix strictly from the hard should_fix rule.
- Do not decide should_fix first and then back-fill the scores.
- The reason field must state first, before anything else, whether the no-eyes-visible or half-face rule applies to this person and why. Only if neither applies should the reason field go on to say whether the eyes are lens-locked or not lens-locked and explain how the scores lead to should_fix.

Strong evidence for not looking at the lens:
- Pupils/irises point slightly left/right/up/down relative to the camera even though the face points forward.
- The eyes appear to look past the lens, above the lens, beside the lens, at a person nearby, toward an audience, toward an object, or into the distance.
- The eyes look vacant, disengaged, cross-focused, or not converged on the viewer.
- One or both eyes lack the sense of contact even though the face is otherwise camera-facing.

Strong evidence that gaze redirection is anatomically feasible:
- The head pose is already close to camera-facing, or can become camera-facing with a bounded moderate natural yaw/pitch refinement.
- The required pupil/iris shift is small or moderate, not extreme.
- The corrected eyes would keep normal left/right eye alignment and convergence.
- The eyelids and eye opening can remain the same; the gaze change can be achieved through eye direction plus bounded head/face alignment.
- The expression can be preserved while changing the eye direction and aligning the face more directly toward the camera.
- The eye and face region are clear enough to edit without inventing new eye shape or facial structure.

Strong evidence that gaze redirection is NOT anatomically feasible:
- The head is turned so far away that camera gaze would require an unnatural eye rotation.
- Redirecting the gaze would create a strained side-eye, cross-eyed, divergent, or visually awkward look.
- The corrected gaze would conflict with the eyelid shape or surrounding facial tension in a way that looks physically unnatural.
- The eyes are too blurry, occluded, shadowed, reflected by glasses, low-resolution, or too small to adjust safely.
- The required correction would need changing head pose, face structure, eyelid openness, or expression, rather than only subtly redirecting the eyes.

Output rules:
- Fill gaze_issue independently for every subjects[] entry with is_person=true whose gaze is relevant, even when should_fix ends up false.
- If a person subject's eyes are readable and the person is not unmistakably looking into the lens, their gaze_issue must not default to looking_at_lens/should_fix=false just to skip the work.
- Use should_fix=false only for lens_contact_decision=looking_at_lens or lens_contact_decision=unclear (eyes too blurry/occluded/small to judge safely, not visible at all because the person faces away from the camera, or only about half the face/a single eye is visible). A readable off-camera, near-camera, or unfocused-looking gaze is should_fix=true; do not use should_fix=false to mean "correctable but better left as-is."
- When any subject's gaze_issue has should_fix=true, gaze repair is an approved edit request for that subject. It must not remain analysis-only.
- For every subject whose gaze_issue.should_fix=true, fill gaze_repair_strategy with a concrete, per-image guide. This is mandatory, not optional.
- Add a correction_plan item for every subject whose gaze_issue.should_fix=true. This is mandatory, not optional.
- Add a matching retouch_recipe item for every subject whose gaze_issue.should_fix=true. This is mandatory, not optional.
- Add a matching correction_axes entry including ["expression"] for every subject whose gaze_issue.should_fix=true.
- Add a matching correction_inverse entry that protects unrelated properties while explicitly allowing the named subject's pupil/iris direction, eye-focus direction, sclera balance, gaze-consistent catchlight placement, and, when needed, the exact head/face alignment change approved by the analysis.
- Treat the analysis output as the full repair specification. The edit model should not need to invent its own gaze-repair method.
- Therefore, for every approved gaze repair, the analysis must make the intended execution explicit in four places that all agree: that subject's gaze_issue.gaze_repair_strategy, correction_plan, correction_inverse, and retouch_recipe.
- The correction_plan item must be self-sufficient: name the subject, state that the gaze should be redirected toward the camera lens / direct_camera, state the exact allowed method (for example, eyes only, or eyes plus slight/moderate head-face alignment), and include the same concrete method as gaze_repair_strategy.edit_instruction.
- The correction_inverse item must explicitly say what to preserve and what is allowed to change for that subject. Do not leave these allowances implicit. It should mirror preserve_during_repair and the allowed local changes in gaze_repair_strategy.
- The retouch_recipe item must stay concise and user-facing, but still clearly indicate that this is a gaze-to-camera correction for the named subject and whether face/head alignment may be involved.
- Phrase the gaze correction action as a concrete repair instruction, not as opening eyes and not as a vague "make eye contact better" suggestion.
- Do not describe gaze repair only as "improve eye contact" or "enhance gaze"; state that the named subject's gaze should be redirected toward direct_camera / the camera lens and whether head/face alignment is part of the approved method.
- Preserve identity, overall eye shape, eyelid openness, eye color, glasses/reflections, mouth expression, brow expression, facial structure, skin texture, and lighting, except where the approved analysis explicitly allows a local change.
- Never use blink repair wording for gaze repair.
- If the reason the eye direction cannot be judged is that the eyes may be closed or half-closed -- original_gaze_read says something like "eyes closed" or "gaze direction not observable" -- that is lens_contact_decision=unclear, not not_looking_at_lens, and should_fix must be false on that subject's gaze_issue no matter how confident the rest of the photo's gaze judgments are. Do not write a gaze_repair_strategy that reopens the eyes and redirects them in the same instruction; eyelid state is blink_issue's question, not this one, and "the eyes might be closed" is never evidence that they are pointed away from the lens.

Analysis-to-edit handoff guardrail:
- When a subject's gaze_issue.should_fix=true, the analysis must hand off a fully actionable repair, not just a diagnosis.
- A no-op or underspecified handoff is a failure.
- Make the approved change strong enough, specific enough, and concrete enough that the edit model can execute it without inventing extra gaze logic.

Mini-model calibration:
- Default away from direct_camera unless the eyes are unmistakably lens-locked.
- False-negative guardrail: many failures come from calling near-camera or contextually natural off-camera gaze "direct_camera." Do not do that.
- Visible pupil/iris displacement overrides frontal face direction: if the face is frontal but both pupils/irises point up/side/down, do not call it direct_camera.
- A centered face with side-shifted or upward-shifted pupils is a not_looking_at_lens case.
- A face that looks portrait-like, formal, smiling, or centered is still not_looking_at_lens if the eyes are not lens-locked.
- Do not use broad scene context to zero out not_looking_at_lens_confidence.
- Do not treat face-forward as proof of direct eye contact.
- Do not reject gaze correction just because it changes the original story, speech direction, documentary feeling, or candidness.
- Do not decide should_fix by intuition.
- First judge whether the eyes are lens-locked.
- Then judge whether redirecting the gaze to the lens would be anatomically and ergonomically natural -- this only shapes strategy_type and repair_strength, never should_fix.
- Then compute should_fix strictly from the lens-contact thresholds (readable + not_looking_at_lens), independent of the anatomical feasibility score.
- The default output for any readable, not-lens-locked gaze is should_fix=true, even when the required adjustment is large or the strategy ends up being a conservative partial one.
- If the person is not looking at the lens, recommend the correction; use the anatomical feasibility score only to decide how boldly to describe the fix.

## Smile analysis
Intent: Classify each visible face as neutral, gentle, broad, laughing, or unclear for optional edit-stage adjustment without treating an existing smile as a defect.

A smile is never a defect, so do not add it to correction_plan, detected_issues, or retouch_recipe.
Default to neutral. Only use gentle or above when a smile is clearly and unmistakably present.
- neutral: no clear smile; includes speaking, posing, winking, pouting, parted lips, visible teeth, or pleasant/friendly expressions without definite smile structure.
- gentle: clear but restrained smile.
- broad: strong cheerful smile.
- laughing: unmistakable laughter or intense joy.
Judge smile presence before smile intensity.
If the expression could reasonably be caused by speaking, pronunciation, posing, or another facial action, classify it as neutral.
Visible teeth, mouth openness, slight mouth-corner elevation, narrowed eyes, or raised cheeks do not establish a smile by themselves.
Require a coherent smile pattern, especially coordinated mouth-corner retraction/elevation that clearly reads as smiling rather than speaking or posing.
Prefer precision over recall: when uncertain between neutral and gentle, choose neutral.
Use unclear only when the expression cannot be seen reliably. Set adjust_safe=false only when editing could overwrite distinctive dental features or when the face is obscured, too small, or turned too far away to render a reliable expression. adjust_safe measures technical edit feasibility, not whether the captured emotion should be preserved. Speaking, laughter, strong emotion, or another visible facial action does not by itself make smile adjustment unsafe.

## Analysis responsibilities
Intent: Translate photographic problems and opportunities for aesthetic improvement into an actionable, internally consistent correction_plan.

- Observe the image, identify semantic roles, and describe why the photo can improve.
- Define the main subject, useful supporting elements, optional context, harmful distractions, ROI, subject_extent_box, and protected_regions.
- Decide the composition intent and framing intent before proposing any crop. Centering, thirds, symmetry, leading lines, and negative space are candidate strategies, not fixed rules.
- Treat edit_intent as the single finished light-and-color target. Define a specific overall brightness, tonal relationship, and scene palette that make this photograph polished and visually compelling.
- Make lighting_strategy and memory_color_strategy implementation plans for that same target. They must strengthen edit_intent rather than introduce a different mood, brightness target, or instruction to retain the captured scene color.
- Translate the target into correction_plan. Each item names its region, concrete adjustment, useful strength, and visible contribution to the finished result. Include the light and color work needed to create the target, not only defect repair. Word it concretely and executably -- if an item only conveys an abstract feeling or impression rather than an operation someone could actually carry out, rewrite it to name that operation.
- Protect skin color independently according to subjects[].skin. Scene exposure, white balance, contrast, hue, and saturation remain available to achieve edit_intent.
- Size correction_plan to what the photo actually needs, choosing the few adjustments with the greatest visual benefit. An already successful photograph may need none.
- Eye-opening is not a routine portrait enhancement. Add eye or blink repair to correction_plan only when the expression-intent rules above say should_fix=true.
- Eye-contact repair is separate from blink repair. Analyze whether the eyes are truly lens-locked, not merely whether the face points forward. Derive each subject's gaze_issue.should_fix from the hard gaze rule, then add gaze/contact correction only when should_fix=true; do not use blink repair to solve off-camera, near-camera, cross-focused, or unfocused gaze.
- Gaze detection recall check: before finalizing analysis, revisit every subjects[] person's eyes. If any readable eyes are not unmistakably lens-locked and that subject's gaze_issue does not yet reflect it, fix it.
- Gaze analyze-to-edit consistency check: before finalizing analysis, verify that every subject whose gaze_issue.should_fix=true has a complete gaze_repair_strategy plus a matching correction_plan item and a matching retouch_recipe item naming that subject and the same goal: redirect gaze toward the camera lens / direct_camera. If any matching item or strategy detail is missing, add it.
- Reverse gaze consistency check -- run this in the other direction too, separately from the check above: before finalizing analysis, re-read every correction_plan item. If any of them describes redirecting someone's gaze, eye direction, or eye contact toward the camera, or fixing where someone is looking, that item is only valid when that person's gaze_issue has should_fix=true and a matching gaze_repair_strategy. A subject's gaze_issue is the only place a gaze judgment is allowed to originate -- do not decide independently, while writing correction_plan/detected_issues/aesthetic_diagnosis, that someone's gaze needs fixing and add it there as a shortcut. If a correction_plan item describes a gaze fix with no such backing gaze_issue, delete that correction_plan item (and its correction_axes/correction_inverse/retouch_recipe entry at the same index) rather than leaving it in; add the missing should_fix=true only if the hard should_fix rule actually supports it for that subject.
- A subject's gaze_issue.should_fix=true authorizes the edit model to perform local gaze redirection. Do not leave approved gaze repair only inside analysis JSON.
- For one-eye-closed cases, do not default to intentional_wink. Classify intentional_wink only when wink-specific affirmative evidence is visible. An open eye facing the camera plus an ordinary smile or generic hand gesture is insufficient. Otherwise classify accidental_half_blink when the closed eyelid looks transitional or mistimed, or uncertain when the evidence is genuinely balanced.
- Apply the Strict laughter-induced closure rule above. For laughter, use expressive_closed only when all three visible evidence categories are satisfied: unmistakable high-intensity laughter, coordinated cheek/lower-eyelid/outer-eye compression, and expression dependence. A normal or broad smile, visible teeth, open mouth, raised cheeks alone, speech-like mouth shape, child subject, playful pose, V-sign, selfie, party, or social-photo context is insufficient. If any required laughter category is weak or ambiguous, re-run the accidental-capture test. Other strong emotions such as crying or pain still require their own affirmative coordinated facial evidence.
- For a person who is genuinely asleep or napping, classify as sleeping and do not add blink repair, even if the mouth looks neutral.
- Blink analyze-to-edit consistency check, same reasoning as the gaze one above: before finalizing analysis, verify that every subject whose blink_issue.should_fix=true has a matching correction_plan item, a matching correction_axes entry including ["expression"], a matching correction_inverse entry, and a matching retouch_recipe item, naming that subject. If any matching item is missing, add it now, following the wording rules above.
- Reverse blink consistency check, same reasoning as the gaze one above: if any correction_plan item describes reopening, relaxing, or fixing someone's closed/half-closed eyes with no backing blink_issue at should_fix=true, delete that correction_plan item (and its correction_axes/correction_inverse/retouch_recipe entry) rather than leaving it in.
- Hair analysis is observation-only. Classify the physical state and, when that state has a candidate action, keep its candidate correction_plan inside the matching person's hair_issue. Always return hair_issue.should_fix=false; Python derives the final value after analysis.
- A candidate fix_action or correction_plan may coexist with the should_fix=false placeholder. Top-level correction_plan/correction_axes/correction_inverse/retouch_recipe must contain no hair work.
- Write preserve_rules only for identity, content, geometry, scene structure, and material identity. Do not use preserve_rules to retain scene exposure, white balance, contrast, hue, saturation, or captured color atmosphere; those are governed by edit_intent and correction_plan. Keep eyes, blink, eye contact, gaze, hair, and smile out of preserve_rules because their structured issue fields govern them.
- Prioritize natural-looking skin over global color neutrality. Preserve each person's complexion, skin chroma, and intentional scene illumination; correct only a cast that is distracting, inconsistent across connected skin, or not plausibly explained by the light. Never write a correction_plan item that makes skin warmer, yellower, rosier, healthier-looking, more vivid, or more saturated. If correction is needed, name the unwanted hue, affected skin area, and corrective direction. When subjects[].skin says no unexpected cast, write no skin temperature, hue, or saturation adjustment. A global white-balance, hue, or saturation adjustment may refine the image palette, but it must not force skin to follow the background grade.
- Choose the strongest framing independently of the output aspect-ratio preference. Compare the complete frame with at least one materially different viable crop that conforms to the selected ratio, treating both as equally eligible. Compare subject emphasis, edge distractions, empty-space function, balance, and protected content; select the crop only when it produces a clearly stronger composition. Return crop_candidates for viable alternatives, with each candidate explaining its composition intent and tradeoffs.
- Reframing consistency check: if crop_box.enabled=true, verify that exactly one correction_plan item owns all composition reframing and any aspect-ratio change, its correction_axes includes "crop", its retouch_recipe starts with "重新構圖：", and its correction_inverse restores the original ratio and complete composition without preserving tilt/perspective. The crop item text must describe the semantic crop without naming a numeric ratio; crop_box geometry owns that decision and local deterministic code inserts the resolved ratio afterward. If any composition-framing part is split across items, merge those parts and their aligned arrays before returning JSON. Keep any horizon/vertical/perspective correction in its own "alignment" item.
- Local_face_review completeness check: before finalizing analysis, verify local_face_review has exactly one entry per "Face crop N" / "Local face detector reference N" you were given, and that every entry's matched_subject_index (when >= 0) points at a subjects[] entry whose own local_detection_index equals that same detection_index. If a detection was skipped, or the cross-reference does not agree in both directions, fix it before finalizing.
- Subject inclusion recall check: before finalizing analysis, re-apply the Subject inclusion rule to every person subjects[] entry, not only the ones that came from a local detection. If a subject you added yourself from the full photo actually reads as an incidental bystander, remove that subjects[] entry (and its blink_issue/hair_issue/gaze_issue/smile_issue, and any correction_plan/correction_axes/correction_inverse/retouch_recipe entries that named them) rather than leaving it in.
- is_person recall check: for every remaining person subjects[] entry, re-apply the is_person definition above. If it is currently true but this person's face is not actually legible enough to judge (back of head, silhouette, fully obscured), flip it to false and reset skin/blink_issue/hair_issue/gaze_issue/smile_issue to their is_person=false placeholders instead of leaving stale judged values in place.
- Do not score output quality here. Do not write QA penalties here. Leave candidate ranking to the crop judge and final scoring to QA.
- Before returning JSON, read edit_intent, lighting_strategy, memory_color_strategy, correction_plan, and preserve_rules together. Make them one consistent target and execution plan; remove or rewrite any statement that weakens or preserves away an approved light or color adjustment.

## Coordinate and geometry rules
Intent: Produce normalized subject and crop geometry that downstream framing logic can use safely and consistently.

- crop_box, roi_box, subject_extent_box, and protected_regions use normalized original-image coordinates. x/y are top-left, w/h are width/height: the origin (x=0, y=0) is the photo's own top-left corner, x increases toward the photo's right edge, y increases toward its bottom edge.
- Any "left"/"right"/"middle" wording -- in a subjects[].label, a reason field, or anywhere else describing where someone or something sits in the frame -- is always from the viewer's perspective looking at the photo (the side with the smaller x is "left"), never that person's own anatomical left/right. Keep this the same reference frame you use when writing box x/y/w/h for that same entry, so the coordinates and the words describing them never disagree about which side of the frame something is on.
- roi_box is the focal area. subject_extent_box is the semantic subject area required by the intended framing; it is not automatically every visible body part, foreground object, furniture piece, or background element.
- subjects lists the candidate main subjects (people, animals, products, food, objects) ordered so index 0 is the photo's true main subject -- what the photo is actually about, consistent with main_subject, roi_box, and the composition intent. prominence means semantic importance as the subject, NOT which element is merely brightest, most colorful, or most saturated. Incidental background or decor -- flowers, plants, props, tableware, posters, scenery -- must rank lower than the real subject even when it is visually eye-catching; e.g. in a food photo the dish is the subject, not a bright vase of flowers behind it. Always output at least one entry, even for a single-subject photo.
- Each subjects[].box must tightly frame EXACTLY ONE individual person/animal/object, never a group. In a multi-person or multi-object scene, output one separate entry per individual, each with its own tight box; do NOT return a single box that spans several subjects or most of the frame. If there are four people, subjects must contain four entries, each boxing one person -- index 0 is only the single most prominent one, not all four. This holds even when main_subject or photo_summary describe the group as a whole; subjects is always per-individual so a downstream close-up can isolate exactly one.
- For a person-type subject whose face is visible anywhere in the photo, subjects[].box must include that entire visible head. A box that starts at or below the chin/neck/shoulders and excludes the face -- even if it otherwise fits the rest of the visible body tightly and looks "correct" by itself -- is wrong and must be corrected; the head is not an optional part of a person subject's box.
- blink_issue, hair_issue, gaze_issue, and smile_issue are per-subject fields nested directly inside each subjects[] entry, not separate lists -- fill all four for every subject with is_person=true (using should_fix=false / adjust_safe=false defaults when nothing applies to that person), and set them to a "不適用" reading with should_fix=false / adjust_safe=false for any subject with is_person=false. Because each of these four fields lives on the exact subject it describes, there is no separate label to keep in sync and no way for one of them to silently apply to the wrong person.
- For every "Local face detector reference" you relied on to create a subjects[] entry (see "Zoomed face-crop images" above and its local_face_review requirement), that person's subjects[].box must fully contain the detected face box -- its top/left/right/bottom edges at or beyond the detected face box's own edges on every side, never cutting into or excluding it. A subjects[].box that does not contain the face detection it was created from is wrong and must be corrected before finalizing the analysis; this containment check is mandatory whenever such a detection exists, not merely a suggestion for when your own estimate looks "noticeably off". subjects[].box must still extend beyond just the face to cover that individual's full semantic extent -- containing the face box is a minimum, not the whole box.
- protected_regions contain only indispensable subject details and role cues retained by the selected composition. When crop_box.enabled=true, keep every protected region inside crop_box with breathing room; leave optional background structure and intentionally trimmed subject/body edges outside these boxes.
- If no reframing is beneficial or the crop is almost full frame, set recommended_aspect_ratio to "original" and crop_box.enabled to false.
- The output does not need to preserve the original canvas aspect ratio in auto mode. Choose exactly one value from this supported output set: original, 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1.
- Changing the outer canvas ratio never permits changing the internal proportions of the photographed content. Plan the new frame only by cropping/recomposition; never stretch or squash the original scene to fill it.
- The crop_box.aspect_ratio and every crop_candidates[].crop_box.aspect_ratio must match recommended_aspect_ratio exactly. Do not describe one ratio while returning crop geometry intended for another ratio.
- This is not just a label match: each crop_box's own x/y/w/h must itself compute to that same ratio -- (w * original_image_width) : (h * original_image_height) must equal the stated aspect_ratio, for both crop_box and every crop_candidates[].crop_box. A correctly-labeled aspect_ratio paired with geometry sized for a different ratio is an error, not a valid answer.
- Prefer ordinary photographic ratios (1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9). Use 1:4, 4:1, 1:8, or 8:1 only when strongly justified and subject safety remains excellent.
- Choose aspect ratio from subject type, scene intent, supporting elements, empty-space function, visual balance, and breathing room.
- Set crop_risk.needs_validation to true when composition is ambiguous, crop is aggressive, ROI is near an edge, protected regions are near edges, framing could be misread, or alignment/context tradeoffs need judgment.

## Composition decision framework
Intent: Choose framing from subject meaning, scene context, visual balance, useful empty space, and preservation needs rather than a fixed composition formula.

- For completeness-first presentation, catalog-like, symmetrical, circular, stacked, shape-critical, food/product/object/still-life images, centered or near-centered balance is often competitive.
- For lifestyle, story, portrait, street, travel, action, gaze, gesture, interaction, leading lines, asymmetry, or intentional negative space, off-center, thirds, diagonal, symmetry, or context-preserving framing may be stronger.
- Empty space must have a visible job: direction, story, scale, calmness, graphic balance, leading lines, room for action, environmental context, rhythm, or intentional negative space. Preserve generous breathing room when it serves those jobs; reduce or rebalance empty/background areas when they do not.
- Reassess distractions after the proposed crop. A visible element may be harmful, contextual, or neutral depending on the final frame. Preserve contextual/neutral elements; for each harmful element, decide a concrete and executable treatment rather than an abstract description of the desired feel. Never rebuild the scene to remove it.
- Check alignment when credible horizontal/vertical references exist. Correct unintended tilt or keystone only when it feels unstable or physically implausible; preserve intentional slant only when it has a clear visual purpose.
- Preserve scene truth: background structure, surface material, perspective, spatial relationships, and real-world context. Do not turn casual, lifestyle, documentary, travel, street, or memory photos into studio/catalog scenes unless requested.
- For people and animals, the face ROI alone is not automatically the whole subject. Protect the semantic subject extent and role cues required by the framing. Partial-person crops are acceptable only when intentional, clean, and supportive of the scene.
- For action, eating, gesture, interaction, or expression-driven moments, protect expression, hands, held objects, interaction points, and relevant context. Preserve lower body, furniture, foreground objects, or peripheral context only when they support posture, story, scale, balance, or scene intent.
- For fashion, posture, full-body portrait, dance, sports, product shape, architecture, or object-completeness tasks, preserve the full visible form more conservatively.
- Judge real-image breathing room from actual photo content, not transparent padding, UI checkerboard, or empty canvas.
"""


# gpt-5.6-luna measured 22% recall vs gpt-4.1-mini's 46% on the eye_close eval
# (eval_results/20260828-155737-eyeclose-2posimg-production-2models-100runs), while
# both sit at ~99-100% precision/specificity -- so luna under-detects genuine
# accidental blinks rather than false-flagging. The blink section below was
# rewritten (evidence-first structure, lower confidence bars, an explicit
# recall check) to correct that. Since gpt-4.1-mini's own recall/precision
# balance on the original wording is untouched by this problem, the rewritten
# section is spliced in only for gpt-5.6-luna; every other model keeps the
# original wording captured in _BLINK_ANALYSIS_LEGACY /
# _BLINK_WINK_DEFAULT_BULLET_LEGACY below. See build_analysis_instruction's
# analysis_model param.
_BLINK_ANALYSIS_LUNA = """## Blink analysis
Intent: Detect accidental blink, half-blink, or unintended full eye closure at the shutter moment. Prioritize recall for readable eyes while preserving eye closure only when affirmative visible evidence shows that it belongs to an intentional expression, emotion, activity, downward gaze, or sleep.

Important separation:
- First classify the physical eyelid state from visible eye evidence.
- Then classify whether that state was likely intentional or accidentally captured.
- Do not infer intent first and reinterpret the eyelids to fit a plausible story.
- A smile, pose, generic hand gesture, selfie framing, child subject, social-media context, or pleasant expression does NOT by itself prove that closed eyes were intentional.
- Judge every included person independently. Do not skip secondary subjects.

Evidence order:
1. Eyelid morphology: open, narrowed, fully sealed, uneven, droopy, or transitional.
2. Eye symmetry: both eyes match, one eye is closed, or the two eyes have different closure amounts.
3. Capture context: posed/photo-ready, group photo, candid action, performance, sleep, prayer, eating, or active laughter.
4. Expression evidence: mouth, cheek, brow, head-pose, and gesture evidence that specifically explains the eyelid state.
5. Intent decision: accidental_capture, intentional_expression, or ambiguous.

### Stage 1 -- Physical eyelid-state inspection

Use the matched zoomed face crop as primary evidence when available.

- open: both readable eyes have normal visible eye apertures.
- narrowed: the eyes remain visibly open but are reduced by cheek lift, bright light, or expression.
- half-closed/transitional: one or both eyelids cover an unusual amount of the iris or pupil; the apertures are uneven, droopy, glassy, or caught between open and closed.
- fully closed: upper and lower eyelids meet and no iris, pupil, or sclera is visibly exposed.
- one-eye closed: exactly one eye is fully or nearly closed while the other remains open.
- unclear: eyelid state genuinely cannot be read because of occlusion, blur, reflection, shadow, compression, or insufficient detail.

Do not use occluded_or_unclear merely because the face is small in the full image when a readable face crop is available.

Populate physical_eye_state before choosing eye_state or closure_intent, using exactly one value:
- open: both readable eyes have normal visible apertures;
- narrowed_open: the eyes are narrowed but remain visibly open;
- half_closed: one or both eyelids are partly descended, droopy, uneven, glassy, or transitional;
- fully_closed: both eyelids are fully sealed with no visible iris, pupil, or sclera;
- one_eye_closed: exactly one eye is fully or nearly closed while the other remains open;
- unclear: the physical eyelid state cannot be read reliably.

physical_eye_state is a direct visual observation. Do not change it to match a later interpretation of intent. eye_state and closure_intent must be derived only after physical_eye_state has been recorded.

### Stage 2 -- Accidental-capture test

Positive evidence of accidental capture:
- one or both eyelids look droopy, uneven, partly descended, glassy, or transitional;
- the visible iris or pupil is partly covered in a way that looks mid-blink;
- the person otherwise appears awake, posed, engaged, or photo-ready;
- the mouth, cheeks, brows, head pose, and gesture do not specifically explain the eyelid state;
- one person in a group has closed or half-closed eyes while the others appear ready for the photograph;
- fully closed eyes occur in an ordinary portrait, group photo, or casual posed photo without affirmative evidence that eye closure is part of the intended expression;
- opening the eyes would preserve the same smile, pose, emotion, and meaning while simply making the photograph look better timed.

When these cues are present, classify as accidental_blink or accidental_half_blink even if the person is smiling, posing, making a V-sign, or appearing in a playful/social photograph.

### Stage 3 -- Intentional-expression exception

Classify eye closure as intentional only when affirmative, closure-specific evidence is visible. Valid evidence includes:
- unmistakable high-intensity laughter visibly compresses both eyes shut, supported by both a clearly laughing mouth and strong coordinated eye-region/mid-face muscle changes as defined in the Strict laughter-induced closure rule below;
- crying, pain, embarrassment, dramatic reaction, or another strong emotion visibly explains the closed eyes;
- prayer, meditation, resting, savoring food, or another visible activity specifically calls for closed eyes;
- the person is genuinely asleep;
- a deliberate wink is supported by wink-specific facial asymmetry or interaction;
- the person is visibly looking downward at a specific, nameable target inside the frame.

These are weak contextual cues and are NOT sufficient by themselves:
- an ordinary smile;
- a V-sign, wave, finger pose, or hand near the face;
- a tilted head or styled portrait pose;
- selfie framing;
- being a child;
- being in a casual, playful, stage, fan, or social-media photo;
- both eyelids being symmetrically closed;
- absence of proof that the closure was accidental; intentional closure still requires affirmative visible evidence.

Intentional classification requires visible evidence, not a plausible explanation.

Decision labels:
- open: both eyes are naturally open.
- natural_smiling_squint: the eyes are visibly narrowed by raised cheeks or active laughter. Ordinary full eyelid closure during a smile does not qualify by itself.
- intentional_wink: one eye is deliberately squeezed closed with affirmative wink-specific evidence.
- intentional_closed: both eyes are deliberately closed because a visible pose, activity, or held expression specifically calls for closure.
- expressive_closed: both eyes are closed as an inseparable part of strong laughter, crying, pain, embarrassment, dramatic reaction, or another visibly strong emotion.
- downward_gaze: the eyelids appear narrowed because the eyes anatomically converge on a specific, visible, nameable in-frame target.
- sleeping: the person is genuinely asleep or dozing, supported by relaxed facial and posture cues and disengagement from the photograph.
- occluded_or_unclear: the physical eyelid state cannot be read reliably.
- accidental_blink: readable eyes are fully closed in an unintentionally mistimed shutter moment.
- accidental_half_blink: one or both readable eyes are unevenly half-closed, droopy, sleepy-looking, glassy, or transitional in a way that conflicts with the otherwise awake/photo-ready moment.
- uncertain: accidental and intentional evidence is genuinely balanced after applying the rules below.

### Wink rule

For exactly one closed or nearly closed eye:
- Do not classify intentional_wink from an open eye facing the camera plus an ordinary smile alone.
- A general hand gesture such as a V-sign, salute, or wave is not wink-specific evidence by itself.
- Use intentional_wink only when the closed eye is a controlled expressive squeeze AND at least one additional wink-specific cue is visible: asymmetric cheek/brow action, a knowingly playful smirk, direct wink interaction with the camera, or a gesture clearly coordinated with the wink.
- If the eyelid looks droopy, incomplete, transitional, or mismatched with the face, classify accidental_half_blink.
- If evidence remains genuinely balanced, use uncertain.

### Smile-squint rule

- natural_smiling_squint normally retains some visible eye aperture and is visibly driven by raised cheeks.
- Fully sealed eyes are not automatically natural_smiling_squint merely because the person is smiling.
- Fully sealed eyes with an ordinary posed smile must continue to the accidental-capture test.
- Fully sealed eyes may be expressive_closed because of laughter only when the Strict laughter-induced closure rule below is fully satisfied. Strong cheek compression without unmistakable high-intensity laughter is insufficient.

### Strict laughter-induced closure rule

Do not classify closed eyes as laughter-induced from the mouth, smile label, or social context alone. This is a strict exception: every required category below must have clear, affirmative visible evidence.

Required evidence -- all three categories must be satisfied:
1. Unmistakable high-intensity laughter:
   - the mouth is substantially opened in active laughter, or is extremely broadly retracted in a way that unmistakably reads as laughter rather than a posed smile, speech, singing, shouting, or surprise;
   - the whole face reads as an active, high-intensity laugh, not merely cheerful, pleasant, or broadly smiling.
2. Coordinated eye-region and mid-face muscle action:
   - both cheeks are strongly elevated and visibly push upward against the lower eyelids;
   - the lower eyelids visibly bulge, rise, or form pronounced upward curves;
   - compression or creasing is visible near the outer eye corners;
   - the eye closure appears produced by upward cheek/lower-eyelid compression, not merely by the upper eyelids descending as in a blink.
3. Expression dependence:
   - the closed-eye state is integral to the same high-intensity laughing expression;
   - opening the eyes would materially weaken or contradict that captured laugh, rather than preserve the same expression and merely improve shutter timing.

Not sufficient, individually or in combination, unless all required categories above are visibly satisfied:
- an ordinary, polite, gentle, broad, or toothy smile;
- visible teeth, braces, raised mouth corners, or a slightly/open mouth;
- raised cheeks without pronounced lower-eyelid and outer-eye compression;
- narrowed or closed eyes without an unmistakable laughing mouth and whole-face laugh;
- speaking, singing, shouting, surprise, a playful pose, V-sign, party setting, child subject, selfie, or group-photo context;
- a plausible guess that the person may have been laughing.

Decision rule:
- Use natural_smiling_squint when the eyes remain visibly open and are naturally narrowed by a smile.
- Use expressive_closed for laughter only when every required category above is clearly satisfied.
- If any required category is missing, weak, obscured, or ambiguous, do not use laughter to explain fully_closed or half_closed eyes. Re-run the accidental-capture test and prefer accidental_blink or accidental_half_blink when the subject is otherwise awake/photo-ready.
- When uncertain between laughter-induced closure and shutter-time blink, prefer accidental_capture. intentional_expression requires strong affirmative evidence; accidental_capture does not require proof of the person's private intention.

### Intentional-closed rule

- Use intentional_closed only when a visible activity or deliberately held expression specifically explains why both eyes are closed.
- A clean and symmetric closure does not by itself prove intention. A shutter-time blink can also be clean, symmetric, and fully closed.
- A deliberate body pose proves that the pose is intentional; it does not prove that the eyelids were intentionally closed at the shutter instant.

### Sleeping rule

Use sleeping only when multiple visible cues support genuine sleep:
- fully and evenly closed eyelids;
- relaxed or slack facial muscles;
- a resting/dozing head or body posture, or clear disengagement from the surroundings;
- no active posing or interaction with the camera.

A person standing or sitting upright with closed eyes while otherwise posing for the photograph is not sleeping merely because the face looks calm.

### Downward-gaze rule

Use downward_gaze only when:
- a specific visible target exists inside the image;
- the eye direction anatomically converges toward that target;
- the subject appears to attend to or interact with it.

Looking vaguely downward, toward the floor, into empty space, or away from the camera does not qualify. Run the accidental-capture test instead.

### Capture-context calibration

- In a posed or group photograph, one person's readable closed or half-closed eyes while others are ready is strong accidental-capture evidence.
- In an ordinary portrait, a normal smile does not override droopy, transitional, or fully shutter-closed eyelids.
- In a candid photograph, do not assume intentional closure merely because the moment is candid. The eyelids still need expression-specific evidence.
- In a playful photograph, generic playfulness does not prove eyelid intent. The visible expression must specifically explain the closure.
- For a child, apply the same evidence standard as for an adult. Age, activity, or playfulness is not evidence of intentional eye closure.

### Confidence and should_fix

- blink_failure_confidence measures confidence that the photo caught an accidental shutter-timing failure, not confidence that the eyes are closed.
- Set closure_intent="accidental_capture", expression_coherence="conflicts_with_expression", and should_fix=true when all are true:
  1. the subject appears awake;
  2. the eyelid state is readable;
  3. eye_state is accidental_blink or accidental_half_blink;
  4. blink_failure_confidence >= 0.55.
- Use 0.55-0.69 for probable accidental cases and 0.70-1.00 for clear accidental cases.
- For genuinely blurred, reflected, shadowed, compressed, or occluded eyes, require blink_failure_confidence >= 0.85.
- For affirmative intentional cases, set closure_intent="intentional_expression", expression_coherence="supports_expression", blink_failure_confidence 0.0-0.25, and should_fix=false.
- Use closure_intent="ambiguous", expression_coherence="unclear", and should_fix=false only when accidental and intentional evidence is genuinely balanced or the physical eyelid state is unreadable.
- Do not set should_fix=false merely because the blink is subtle, the person is smiling, or the overall photograph is still acceptable.

### Recall check

Before finalizing:
1. Revisit every included person's eyes using the face crop when available.
2. Identify every fully closed, partly closed, droopy, asymmetric, glassy, or transitional eyelid.
3. For every such person classified as intentional, name the affirmative closure-specific evidence in intent_cues and reason. For laughter-induced closure, explicitly name evidence from all three required categories: unmistakable high-intensity laughing mouth/whole-face expression, coordinated cheek/lower-eyelid/outer-eye compression, and expression dependence. If all three cannot be named from visible evidence, the laughter exception fails.
4. If the reason contains only a smile, pose, generic hand gesture, selfie context, child/playful context, social/performance context, or symmetric closure, the intentional classification is unsupported. Re-run the accidental-capture test.
5. In a posed or group photograph, apply this counterfactual: if opening the eyes would preserve the person's existing expression and simply make the shot look better timed, prefer accidental_capture.
6. Do not use uncertain as a safe default. Use it only after naming evidence on both sides.

Subject-size neutrality:
- Do not decide should_fix from face size, foreground/background status, or main/secondary-subject status.
- Size affects only whether the eyelids are readable.
- If a zoomed face crop makes the eyelids readable, apply the same rules as for a large face.

Output rules:
- Fill physical_eye_state first from eyelid appearance alone. Never use smile, pose, gesture, scene type, age, or a possible story to alter this physical observation.
- Fill blink_issue for every subjects[] entry with is_person=true, including open-eyed people.
- intent_cues must contain only visible evidence, not inferred stories.
- For should_fix=true, reason must name the visible eyelid morphology and why it conflicts with the otherwise awake/photo-ready moment.
- For should_fix=false on a closed-eye case, reason must name the affirmative intentional-expression, downward-target, or sleep evidence. Generic speculation about intent is invalid.
- Add an eye-related correction_plan item only when should_fix=true.
- If should_fix=true, add a correction_plan item naming the exact person by position/role and ask the edit model to naturally reopen or relax only that person's eyes while preserving identity, gaze direction, facial structure, expression style, skin texture, glasses, makeup, and lighting.
- The matching correction_axes entry must include ["expression"].
- The matching correction_inverse entry must preserve the original eye state and facial expression for that same person.
- The matching retouch_recipe item must be concise and user-facing, for example: "修正右側男生不小心半閉眼的表情。"
- If no accidental blink or half-blink needs correction, keep correction_plan free of eye-opening items.

Edit-model execution guardrail:
- When a subject's gaze_issue.should_fix=true, do not leave that subject's gaze unchanged.
- A no-op gaze edit is a failure.
- Apply the smallest visible gaze-alignment change that fully satisfies the per-subject gaze_repair_strategy and turns the named subject's gaze into natural camera-facing eye contact.
- Start with the analysis model's strategy_type. If it says eyes_only, use iris/pupil direction and sclera balance. If it says eyes_plus_head_face_alignment, use iris/pupil direction plus the specified head/face alignment; do not stop with eye-only repair.
- This is not a full pose change: preserve identity, facial structure, expression, body pose, shoulders, hands, clothing, scene, and camera viewpoint.

Analyze-to-edit alignment guardrail:
Low-capacity image-model wording rule:
- When instructing gaze repair, prefer concrete physical wording over abstract labels.
- Interpret "camera-facing gaze" as: both pupils/irises visibly point at the camera, and the subject clearly looks at the camera.
- Do not rely only on terms like direct_camera, lens-locked, or eye contact without also saying how the pupils/irises should move.

- For edit execution, camera-facing gaze is a physical eye-direction target: iris/pupil placement and both eyes' visual axes should aim at the camera lens, not merely look sharper or more intense.
- Approved gaze repair has higher edit priority than generic preservation for the listed subject's local eye-direction change.
- A subject's gaze_issue.should_fix=true is not merely a recommendation; it authorizes the edit model to perform gaze redirection.
- For every approved gaze repair, the analysis must produce the same instruction in three places: that subject's gaze_issue, correction_plan, and retouch_recipe.
- Do not rely on the edit model to infer gaze repair from JSON alone.
- Do not let a preservation instruction cancel an approved gaze repair. Preservation should protect all unrelated facial traits while allowing local gaze redirection.

GPT-5.6 Luna calibration:
- Do not rationalize closed eyes as intentional from context alone.
- Do not reason as: "the person is smiling, therefore the closed eyes are natural."
- Do not reason as: "the pose or hand gesture is deliberate, therefore the eyelids are deliberate."
- Do not reason as: "this is a child, selfie, social, stage, fan, playful, or candid photo, therefore closed eyes belong to the moment."
- Do not reason as: "both eyelids are cleanly and symmetrically closed, therefore closure is intentional."
- Do not reason as: "one eye is closed and the other faces the camera, therefore this is a wink."
- Do not reason as: "looking downward or away is acceptable" unless a specific visible in-frame gaze target exists.
- Set should_fix=false for intentional closure only when affirmative visible evidence supports that classification.
- First state the physical eyelid evidence. Then state the intent evidence. Then compute eye_state, closure_intent, confidence, and should_fix from those observations.

"""

_BLINK_ANALYSIS_LEGACY = """## Blink analysis
Intent: Classify expression intent before considering repair: detect accidental shutter-timing failures without judging eyelid openness alone, while preserving eye closure that belongs to the person's expression, emotion, gesture, sleep, downward gaze, or captured moment.

Two-stage decision process:
Before Stage 1, populate physical_eye_state from eyelid appearance alone using exactly one of open / narrowed_open / half_closed / fully_closed / one_eye_closed / unclear. This field is a direct observation and must not be changed to fit the later intent classification.
Stage 1 -- Expression coherence test:
- Ask whether the eye state supports the visible expression. Look at mouth shape, eyebrows, cheeks, head pose, hand gesture, body language, gaze direction, social context, and scene type.
- If the closed/narrowed/winked eye supports the person's expression or emotion, classify it as intentional_wink, natural_smiling_squint, intentional_closed, expressive_closed, downward_gaze, or sleeping. Set closure_intent="intentional_expression", expression_coherence="supports_expression", blink_failure_confidence 0.0-0.25, and should_fix=false.
- Examples of expression-coherent eye closure: wink with smile/smirk/greeting; crying face with squeezed eyes; laughing with eyes closed; cute child expression; playful selfie/video gesture; stage/fan-service expression; pain/sadness/embarrassment face; looking down at a specific, visible in-frame target.

Stage 2 -- Accidental capture test:
- Only if the eye state does NOT support the expression, ask whether it looks like a shutter-timing mistake.
- Accidental blink/half-blink means the eye closure conflicts with the rest of the face or moment: the mouth/expression is otherwise neutral or photo-ready, but the eyelids look droopy, transitional, uneven, or mistimed -- for an instant, not for the whole visible moment. This is different from a person who is genuinely asleep; run the sleeping guardrail below before concluding accidental capture.
- If the eye state conflicts with the intended expression and blink_failure_confidence >= 0.65, set closure_intent="accidental_capture", expression_coherence="conflicts_with_expression", and should_fix=true.
- If evidence is mixed, set closure_intent="ambiguous", expression_coherence="unclear", and should_fix=false.

Core policy:
- The goal is to catch photo-taking mistakes users would likely want fixed, not to make every person open their eyes.
- A closed or narrowed eye is not a defect when it carries emotion, charm, playfulness, pain, laughter, sadness, greeting, or performance.
- False positive eye repair is worse than missing a subtle blink when the closed eye is part of an intentional or emotional expression.
- Do not use subject size, foreground/background status, or main/secondary subject status as a deciding factor. Size only affects visual uncertainty.

Decision labels:
- open: both eyes are naturally open.
- natural_smiling_squint: eyes are narrowed because the person is smiling, laughing, reacting, or showing joy; this is not a failure.
- intentional_wink: one eye is closed or nearly closed while the other eye is open, and the face/body/context reads as a deliberate wink, greeting, pose, playful expression, performance, selfie gesture, video moment, or social-media interaction; this is not a failure.
- intentional_closed: eyes are deliberately closed as part of a pose, prayer/resting expression, savoring food, calm moment, or other intentional moment; this is not a failure. Requires the eyelids themselves to be fully and cleanly shut (no visible sclera or pupil) -- see the pose-does-not-imply-eyelid-state guardrail below.
- expressive_closed: eyes are closed as part of a strong emotional or expressive face, such as crying, exaggerated sadness, pain, embarrassment, cute reaction, dramatic performance, or meme-like reaction face; this is not a failure.
- downward_gaze: eyes appear closed or narrowed because the person is looking down at a specific, nameable, visible target inside this same frame and the gaze is anatomically consistent with looking at it; this is not a blink failure. Looking down or away with no identifiable in-frame target does not qualify for this label; see the downward-gaze guardrail below.
- sleeping: the person is genuinely asleep or napping -- both eyes fully and evenly closed, face and jaw relaxed/slack, and a resting posture (head tilted onto a pillow/shoulder/arm, reclined or lying down, eyes closed for the whole visible moment rather than mid-transition); this is not a failure, regardless of how neutral the mouth looks.
- occluded_or_unclear: eyes are hard to judge because of glasses, hair, motion blur, shadow, hand/object occlusion, low resolution, compression, or face size. Do not fix unless the accidental blink is visually clear.
- accidental_blink: eyes are caught closed in an unintentionally mistimed shutter moment.
- accidental_half_blink: one or both eyes are caught unevenly half-closed, droopy, sleepy-looking, or transitional in a way that conflicts with the person's expression and likely weakens the photo.
- uncertain: evidence is mixed or not important enough to repair.

Wink / one-eye-closed guardrail:
- If exactly one eye is closed or nearly closed and the other eye is open, run the Expression coherence test first. Do not jump to accidental_half_blink.
- Classify as intentional_wink with should_fix=false when at least TWO of these cues are present:
  1. the open eye looks toward the camera/viewer;
  2. the mouth has a controlled smile, smirk, greeting expression, speaking shape, or playful expression;
  3. the eyebrows/cheeks/head pose support a deliberate expression rather than a sleepy mistake;
  4. there is a hand gesture, salute/wave, selfie framing, video screenshot feel, stage/performance context, close camera interaction, or social-media capture;
  5. the closed eye is a clean expressive squeeze rather than a droopy transitional lid.
- A one-eye-closed face is accidental_half_blink only when the mouth, brow, gaze, head pose, and context look neutral/unprepared AND the closed lid looks mistimed rather than expressive.

Emotional closed-eye guardrail:
- If eyes are closed together with a strong mouth/brow emotion, classify by expression first, not by eyelid state.
- Crying face, pained face, exaggerated sadness, embarrassment, dramatic reaction, cute reaction, meme-like expression, or laugh-with-eyes-closed should be expressive_closed or natural_smiling_squint with should_fix=false.
- Do not open eyes when opening them would erase the emotional meaning of the photo.
- Counterfactual test: If opening the eyes would make the expression less truthful, less funny, less cute, less emotional, or less like the captured moment, then should_fix=false.

Smile-squint vs. full-closure guardrail:
- A genuine smile-driven squint narrows the eyes; it does not require them to be fully sealed shut. Before classifying closed eyes during a smile as natural_smiling_squint or intentional_closed, check whether the eyelids are only narrowed (a visible eyelash line, a slight eye-corner crease, still some sliver of the eye showing) versus fully and completely shut with no eye visible at all.
- Full, complete eyelid closure during an otherwise ordinary smile or casual pose is not, by itself, evidence of natural_smiling_squint or intentional_closed -- a blink can happen at the exact camera-click moment independent of what the mouth is doing at that instant. Only classify fully-shut eyes as natural_smiling_squint/intentional_closed when there is supporting evidence beyond "the person is smiling": an active laugh (visible teeth, raised cheeks compressing the eyes shut), a deliberate pose context (prayer, savoring food, resting, a repeated stylistic expression across other photos of the same shoot), or an intentional_wink/performance context.
- intentional_closed's own qualifying contexts are specific -- a held pose, prayer/rest, savoring food, a calm moment. An ordinary candid or posed smile in a casual or group photo does not by itself qualify; let it fall through to the Stage 2 accidental-capture test instead of defaulting to intentional_closed.
- In a group photo, one person with fully shut eyes while everyone else's eyes are open and the shot otherwise looks photo-ready is strong evidence for accidental capture (see "Strong evidence for accidental capture" above) -- that person's own smile, on its own, does not override this signal.

Pose-does-not-imply-eyelid-state guardrail:
- A deliberate held pose -- hands framing the chin/cheek, head tilted back or to the side, a styled selfie/portrait setup -- proves the pose is intentional. It does NOT by itself prove the eyelids are also fully and deliberately closed at that same instant; a blink can happen involuntarily even while every other part of a held pose is exactly as intended.
- Before classifying eyes as intentional_closed, expressive_closed, or natural_smiling_squint on the strength of a deliberate pose, check the eyelids themselves: are they cleanly, fully, and evenly shut with no sclera or pupil visible? If any sliver of white or the pupil/iris is visible, the eyes are not fully closed -- treat it as a half-open, transitional state and run the Stage 2 accidental-capture test instead of defaulting to intentional_closed just because the pose looks deliberate.
- A symmetric hand-on-face / head-tilt pose paired with half-open, glassy, unfocused eyes and no visible in-frame gaze target is a common signature of an accidental blink caught mid-transition during an otherwise well-executed pose -- it is not, by itself, evidence that the eye closure was intentional.

Sleeping guardrail:
- Before concluding accidental_blink or accidental_half_blink, check whether the person is genuinely asleep rather than mid-blink while awake.
- Evidence of genuine sleep: both eyelids are fully and evenly closed (not droopy, uneven, or half-open); the face and jaw are relaxed/slack rather than tensed for a shot; the head/body is in a resting posture -- tilted onto a pillow, shoulder, arm, car seat, stroller, or someone's lap, reclined, or lying down; the eyes are closed for the entire visible moment, not caught transitioning between open and closed. This applies to babies, children, and adults alike, and to candid or posed photos of someone napping.
- Genuine sleep does not require a reclined posture. A person can be sitting or standing upright with eyes fully and evenly closed, a slack/disengaged face, and no interaction with the camera or surroundings, and still be genuinely dozing off rather than caught mid-blink.
- A neutral or slack mouth on a sleeping person is expected and is not, by itself, evidence of an accidental blink -- a real accidental blink still requires the droopy/uneven/transitional eyelid look, not just closed eyes plus a neutral mouth.
- When genuine sleep is indicated, classify as eye_state=sleeping, closure_intent=intentional_expression, expression_coherence=supports_expression, blink_failure_confidence 0.0-0.25, should_fix=false. Never add an eye-opening correction_plan item for a sleeping subject.
- Only override this and treat it as accidental capture if the eyelids themselves look droopy, uneven, or mid-transition (evidence the person is awake and drifting/struggling to keep eyes open) rather than fully and evenly at rest.

Downward-gaze guardrail:
- downward_gaze requires a nameable, boxable, visible target inside this same frame that the person is plausibly looking at or interacting with. This is the ONLY thing that qualifies narrowed/closed eyes as downward_gaze.
- It does NOT cover looking down at the ground/floor with nothing there, looking away into empty space or the distance, or any case where you cannot point to a specific in-frame object the eyes are aimed at. A vague "they're just looking down/away" is not evidence of this label.
- When narrowed or closed eyes have no qualifying in-frame target, do not classify as downward_gaze and do not default to should_fix=false on that basis alone -- run the Stage 2 accidental-capture test normally; this is typically accidental_half_blink or accidental_blink, should_fix=true, unless another guardrail (wink, emotional, sleeping) independently applies.

Balanced should_fix rule:
- Set should_fix=true only when blink_failure_confidence >= 0.65 AND the eye issue visibly looks like accidental capture timing on someone who is awake.
- Moderate accidental half-blinks should be fixed when they make a person who is awake and otherwise posed/ready look unintentionally droopy, distracted, unprepared, or mid-blink, and there is no strong intentional-expression cue and no sleeping-guardrail evidence.
- For blurry, occluded, compressed, shadowed, glasses-reflected, or unclear eyes, require stronger confidence around 0.85 before fixing. This is about visual ambiguity, not subject size or prominence.
- If there is strong evidence of intentional_wink, natural_smiling_squint, intentional_closed, expressive_closed, downward_gaze (with a genuine in-frame target -- see the downward-gaze guardrail), sleeping, or playful/performance/emotional expression, should_fix=false even if one or both eyes are closed.

Strong evidence for accidental capture:
- The person otherwise looks normally posed or photo-ready.
- The mouth, brows, cheeks, and head pose do not support a wink, laugh, cute expression, emotional reaction, downward gaze, or performance.
- The eyelids look droopy, uneven, transitional, sleepy, or mistimed.
- The eye state makes the person look unintentionally tired, distracted, unprepared, or caught mid-blink.
- In a group photo, the person's expression is the only one that reads accidentally mistimed while the scene otherwise looks photo-ready.

Strong evidence against accidental capture:
- The closed eye matches the mouth expression, brow tension, cheek lift, hand gesture, head pose, or emotional tone.
- One eye is closed and the other eye is open in a controlled way, with a wink-like expression, smirk, greeting, salute, wave, playful mouth shape, or camera interaction.
- Both eyes are closed because of laughter, smile-squint, crying, pain, sadness, embarrassment, cute expression, prayer/rest, eating/savoring, dramatic reaction, or downward gaze at a specific visible in-frame target.
- The image is a selfie, video screenshot, stage/performance moment, fan-service moment, close-up social-media capture, or playful social snapshot where expressive eyes are normal.
- The person is genuinely asleep: eyes fully and evenly closed, face relaxed, head/body in a resting posture (pillow, shoulder, reclined, lying down) -- see the sleeping guardrail above.

Practical examples:
- Close-up selfie or video frame where one eye is closed and the other eye looks at the camera, with a smile/smirk/salute/wave/greeting gesture: intentional_wink, closure_intent=intentional_expression, should_fix=false.
- Stage or celebrity close-up where one eye is closed in a controlled expression: intentional_wink, should_fix=false.
- Crying face, pained reaction, exaggerated sad face, or dramatic meme-like expression with eyes squeezed shut: expressive_closed, should_fix=false.
- Person laughing so hard both eyes close: natural_smiling_squint or expressive_closed, should_fix=false.
- Casual child photo where the child smiles/plays with eyes closed: natural_smiling_squint or expressive_closed, should_fix=false unless the eye state clearly conflicts with the expression.
- Group photo where one person has a sleepy half-blink while the mouth/brows are otherwise neutral and everyone else is ready: accidental_half_blink, closure_intent=accidental_capture, should_fix=true.
- A baby or adult napping with eyes fully and evenly closed, relaxed face, and head resting on a pillow/shoulder/arm or lying down, even if the mouth is neutral: sleeping, closure_intent=intentional_expression, should_fix=false.
- Formal or ordinary portrait where the subject's eyelids are accidentally half-closed and the expression otherwise looks neutral/photo-ready: accidental_half_blink, should_fix=true.
- Person looking down at a specific, visible in-frame target: downward_gaze, should_fix=false unless the eye state clearly conflicts with the rest of the face.
- Person looking down or away with no identifiable in-frame target (staring at the ground/floor, gazing off into empty space or the distance): does not qualify as downward_gaze; run the accidental-capture test -- typically accidental_half_blink/accidental_blink, should_fix=true, unless another guardrail (wink, emotional, sleeping) applies.
- Blurry, compressed, shadowed, or unclear eyes: should_fix=false unless the accidental capture is visually clear.

Subject-size neutrality:
- Do not decide should_fix based on whether the face is large, small, foreground, background, main subject, or secondary subject.
- Size/prominence may affect how easy the eye state is to see, but it must not be the reason for fixing or not fixing.
- If the eye state and expression are visually clear, apply the same blink-vs-expression logic to every person.

Output rules:
- For every visible person, inspect eye state independently.
- Fill blink_issue for every subjects[] entry with is_person=true whose eyes are legible enough to judge, not only the ones with something to explain -- an ordinary open-eyed person with nothing unusual still gets should_fix=false, the same as a wink or laugh does.
- Do not leave a person subject's blink_issue at a placeholder just because their eyes look fine; every person subject gets a genuine reading, not only the flagged or noteworthy ones.
- Use should_fix=false with a reason explaining why a closed-eye case is intentionally left unchanged, especially for winks, laughter, crying/emotional expressions, child expressions, or ambiguous cases.
- Add an eye-related correction_plan item only when should_fix=true.
- If should_fix=true, add a correction_plan item that names the exact person by position/role and asks the edit model to naturally reopen or relax only that person's eyes while preserving identity, gaze direction, facial structure, expression style, skin texture, glasses, makeup, and lighting.
- The matching correction_axes entry for that correction_plan item must include ["expression"].
- The matching correction_inverse entry should preserve the original eye state and facial expression for that same person if the user unchecks the item.
- The matching retouch_recipe item should be user-facing and concise, for example: "修正右側男生不小心半閉眼的表情。"
- If no accidental blink or half-blink needs correction, keep correction_plan free of eye-opening items.

Edit-model execution guardrail:
- When a subject's gaze_issue.should_fix=true, do not leave that subject's gaze unchanged.
- A no-op gaze edit is a failure.
- Apply the smallest visible gaze-alignment change that fully satisfies the per-subject gaze_repair_strategy and turns the named subject's gaze into natural camera-facing eye contact.
- Start with the analysis model's strategy_type. If it says eyes_only, use iris/pupil direction and sclera balance. If it says eyes_plus_head_face_alignment, use iris/pupil direction plus the specified head/face alignment; do not stop with eye-only repair.
- This is not a full pose change: preserve identity, facial structure, expression, body pose, shoulders, hands, clothing, scene, and camera viewpoint.

Analyze-to-edit alignment guardrail:
Low-capacity image-model wording rule:
- When instructing gaze repair, prefer concrete physical wording over abstract labels.
- Interpret "camera-facing gaze" as: both pupils/irises visibly point at the camera, and the subject clearly looks at the camera.
- Do not rely only on terms like direct_camera, lens-locked, or eye contact without also saying how the pupils/irises should move.

- For edit execution, camera-facing gaze is a physical eye-direction target: iris/pupil placement and both eyes' visual axes should aim at the camera lens, not merely look sharper or more intense.
- Approved gaze repair has higher edit priority than generic preservation for the listed subject's local eye-direction change.
- A subject's gaze_issue.should_fix=true is not merely a recommendation; it authorizes the edit model to perform gaze redirection.
- For every approved gaze repair, the analysis must produce the same instruction in three places: that subject's gaze_issue, correction_plan, and retouch_recipe.
- Do not rely on the edit model to infer gaze repair from JSON alone.
- Do not let a preservation instruction cancel an approved gaze repair. Preservation should protect all unrelated facial traits while allowing local gaze redirection.

Mini-model calibration:
- Do not reason as: "eyes closed means accidental blink."
- Do not reason as: "one eye closed means half-blink."
- Do not reason as: "emotional closed eyes should be opened."
- Do not reason as: "neutral mouth plus closed eyes means accidental blink" -- a genuinely sleeping person also has a neutral, relaxed mouth; check the sleeping guardrail's posture and eyelid cues before assuming a mistake.
- Do not reason as: "the person is looking down/away so it's fine" -- that is only true when there is a specific, visible, nameable target in this frame that the gaze is aimed at; check the downward-gaze guardrail before assuming a mistake.
- Do not reason as: "the pose is clearly deliberate (hands on face, head tilt, styled shot), so the closed eyes must be deliberate too" -- a held pose does not guarantee the eyelids were fully closed on purpose; check whether sclera or pupil is visible before assuming intentional_closed. See the pose-does-not-imply-eyelid-state guardrail.
- Always ask first: "Does the eye state support the visible mouth/brow/gesture/emotion?" If yes, do not fix.
- For one-eye-closed faces, run the wink guardrail before any blink-failure reasoning.
- For strong emotional faces, run the emotional closed-eye guardrail before any blink-failure reasoning.
- For any fully-closed-eyes case, run the sleeping guardrail before any blink-failure reasoning.
- For any downward/averted-gaze case, run the downward-gaze guardrail before any blink-failure reasoning.
- For any deliberately posed shot (hands on face, held head tilt, styled portrait/selfie), run the pose-does-not-imply-eyelid-state guardrail before any blink-failure reasoning.
- When the expression could reasonably be deliberate, emotional, charming, playful, or performative, choose should_fix=false.
- When the eye state clearly conflicts with the expression and looks like shutter mistiming, choose should_fix=true.

"""

_BLINK_WINK_DEFAULT_BULLET_LUNA = "- For one-eye-closed cases, do not default to intentional_wink. Classify intentional_wink only when wink-specific affirmative evidence is visible. An open eye facing the camera plus an ordinary smile or generic hand gesture is insufficient. Otherwise classify accidental_half_blink when the closed eyelid looks transitional or mistimed, or uncertain when the evidence is genuinely balanced."

_BLINK_WINK_DEFAULT_BULLET_LEGACY = "- For one-eye-closed cases, intentional_wink is the default classification when the open eye faces the camera and the face/gesture/emotion looks controlled; do not add blink repair for that case."


# ---------------------------------------------------------------------------
# Two-pass split for gpt-5.6-luna: Call A (overall photo direction + subject
# identification) and Call B (dedicated portrait-QC pass judging blink/hair/
# gaze/smile only for the people Call A already identified). See
# openai_client._analyze_photo_two_pass. Every other model keeps the single
# ANALYSIS_INSTRUCTION call above untouched.
#
# Why split: the single-call flow doesn't just ask the model to judge blink/
# gaze/hair -- it also asks it to keep every should_fix=true issue in sync
# with a hand-authored correction_plan/correction_axes/correction_inverse/
# retouch_recipe entry, and to reverse-check and delete any it invents
# without backing. That's substantial extra bookkeeping layered on top of the
# visual judgment itself, and a plausible independent contributor to
# gpt-5.6-luna's low blink recall (see project memory
# project_luna_eyeclose_recall.md / the 20260828 eval). Call A below is told
# to leave correction_plan free of eye/gaze/hair items entirely;
# Call B authors the hair handoff directly inside subjects[].hair_issue.
# Blink and gaze execute directly from their structured repair strategies,
# while smile executes directly from smile_issue, so none of those three
# needs a correction_plan backfill.
# ---------------------------------------------------------------------------

_FACE_CROP_EVIDENCE_SECTION = """## Face-crop evidence
Intent: Use zoomed face crops as supporting evidence for facial details without treating them as separate photographs or using them for scene-level decisions.

- After the main photo, you may be given one or more additional images labeled "Face crop N (zoomed, for anchoring subject detection and judging eye state, blink, gaze/pupil direction, and smile state; it is not a separate photo)". Each one is a cropped, zoomed-in view of one face a local face detector found in the SAME photo above, not a different photo or a different moment. Each comes with its own "Local face detector reference" coordinate (normalized original-image x/y/w/h) for that same face, numbered the same as its face crop (or, beyond the detector's own crop-count cap, given as a coordinate-only text reference with no image).
- Go through every "Face crop N" / "Local face detector reference N" you are given, in order, and record one local_face_review entry per detection_index=N (see the JSON shape above). For each one, decide: (1) is this actually a real face (not background clutter, a pattern, a reflection, or a printed/painted face)? (2) if real, does this person pass the Subject inclusion rule above? Only when both are true do you create a subjects[] entry for them; set that entry's local_detection_index to this same N, set is_person per the is_person definition above (true in the normal case where this detected face is also legible enough to analyze; false if, despite being real and included, it still is not legible enough), and set this detection's matched_subject_index to that subjects[] entry's 0-based index. Otherwise set matched_subject_index=-1 and write reason: name whichever of "not a real face" or "real face but an incidental bystander (name why)" applies.
- The detector can also miss a real, includable face (small, occluded, profile, motion-blurred) -- or a real, includable person with no detectable face at all (full back-of-head, silhouette). Still look at the full photo yourself and add a subjects[] entry for any additional visible person the detector did not catch and who passes the Subject inclusion rule; give that subjects[] entry local_detection_index=-1 since no detection produced it, and set is_person per the is_person definition above.
- Use each matched face crop as your primary evidence for that person's eye_state, gaze_state, lens_contact_decision, and pupil/iris direction -- the main photo alone often does not have enough resolution on the eyes to judge this reliably, especially in a half-body, full-body, or group photo. Everything else about that person (composition role, pose, clothing, scene relationship, framing decisions) still comes from the main photo, not the crop.
- A visible, includable person with no matching crop (beyond the detector's cap, or a person the detector missed entirely) is judged directly from the main photo as usual -- do not treat a missing or unusable crop as evidence the eyes are unreadable.
"""

_PORTRAIT_QC_FACE_CROP_EVIDENCE_SECTION = """## Face-crop evidence for portrait QC
Intent: Use the supplied full photo for photographic context and each zoomed face crop for local facial evidence, without repeating subject discovery or changing the subject list finalized by Call A.

- The people to review are authoritative. Do not create, remove, reorder, merge, split, or relabel people.
- Each listed person has an immutable subject_index from Call A. Echo that exact subject_index in the corresponding output item.
- Also echo local_detection_index exactly as listed. It identifies the matching Face crop N when its value is greater than or equal to 1; -1 means no detector-backed crop is available.
- Match a Face crop N only to the listed person whose local_detection_index equals N. Do not associate crops by visual similarity, list position, prominence, or guesswork.
- Use the matched face crop as primary evidence for the physical reading of a face: physical_eye_state, blink/closure, pupil/iris detail, smile_intensity and the mouth-corner, lip, and cheek structure behind it, and strand-level hair detail -- the fringe's root band, strand thickness and spacing, forehead showing between clumps, oily sheen, and strands resting on a feature.
- The crop is one person enlarged, never the evidence for which person this is: the listed box and label identify them in the full photo, and the crop stops at the head. So the hairstyle as a whole is read from the full photo -- whether the front strands continue into the lengths (a side sweep or long front hair) or stop as a shorter band (a fringe), whether the disorder is even across the whole head (style) or a single local contradiction (accident), whether wind shows elsewhere in the frame on clothing, foliage, or other people's hair, and which part of the same head is still undisturbed enough to define what a settle returns to.
- For gaze, use the crop and full photo together: the crop resolves pupil/iris placement, while both views establish head yaw/pitch, facial geometry, and the current camera viewpoint. Never treat the crop center as the lens position.
- Use the full photo for what that reading means and for everything outside the face: expression intent, gesture, body posture, group-photo readiness, visible gaze targets, sleep/activity context, and the relationship between people.
- A crop is the same person and same shutter moment as the full photo, not a separate image.
- If local_detection_index=-1 or the matching crop is unavailable, judge from the full photo and mark a field unclear only when the relevant visual evidence is genuinely unreadable.
- Return exactly one result for every listed subject_index. Never output an issue result for a person not listed here.
"""

_BLINK_DETECTION_LUNA_SECTION = """## Blink / eyelid physical-observation pass
Intent: maximize recall of readable half-closed, fully closed, and one-eye-closed states. Report physical evidence first. Deterministic Python, not you, decides blink_candidate, intent labels, confidence, and should_fix.

Use the matched face crop as primary eyelid evidence and the full photo only for expression and activity context. Inspect every listed person independently, including secondary people.

Physical fields:
- blink_observability: both_eyes when both eyelids can be inspected; one_eye only when one eyelid is actually hidden, cropped out, or too degraded to inspect; unreadable only when neither eyelid state is reliable. A fully_closed eye is still observable as an eyelid even though its iris is not visible. If you can assign concrete left_eye_state and right_eye_state values other than not_visible/unclear, blink_observability must be both_eyes.
- eyelid_detail_quality: readable, borderline, or unreadable based on blur, occlusion, reflection, compression, darkness, and actual pixel detail.
- left_eye_state always means the eye on the photograph-left side (viewer-left, smaller image x); right_eye_state always means the eye on the photograph-right side (viewer-right, larger image x). These are photograph-side fields, never the person's anatomical left/right. Each value is one of: open, narrowed_open, half_closed, fully_closed, not_visible, or unclear.
- half_closed means an eyelid unusually covers the iris or pupil, or the aperture looks droopy, uneven, glassy, or transitional. Do not demote it to narrowed_open because the person is smiling.
- fully_closed means the eyelids meet with no iris, pupil, or sclera visible.
- physical_eye_state summarizes the two per-eye states without interpreting intention.
- When one eye is closed and the other is half-closed or open, describe them as mismatched transitional eyelid states. Natural anatomical variation applies to stable eye contour and crease structure, not to this captured difference in eyelid openness.
- eye_closure_confidence is confidence from 0 to 1 that at least one eye is physically half_closed or fully_closed. It is not confidence that the closure was accidental.

Record the following exception evidence separately; never use it to rewrite the physical states:
- mouth_laughter_state: active_high_intensity_laughter only for unmistakable active laughter, not a normal, toothy, broad, posed, speaking, singing, shouting, or surprised mouth.
- periocular_contraction: strong_bilateral only when both cheeks strongly elevate into the lower eyelids and pronounced lower-lid/outer-eye compression is visible. Raised cheeks alone are none_or_mild.
- closure_expression_dependence: integral_to_expression only when opening the eyes would visibly contradict the same captured expression; otherwise independent_or_transitional or unclear.
- explicit_closure_context: choose a named value only when clear visible evidence supports it. A V-sign, generic hand gesture, selfie, child subject, party, stage, playful pose, ordinary smile, symmetric closure, or plausible story is none, not evidence of intention. deliberate_wink additionally requires the other eye clearly open plus a wink-specific cue such as one-sided cheek or brow action, a knowing smirk, or camera-directed play; one eye closed while the other is narrowed, half-closed, or looking away is mismatched eyelid timing, so it is none. downward_at_visible_target requires a specific visible target in this same frame.
- intent_evidence_confidence measures confidence in that explicit exception evidence, not confidence in eyelid closure.
- intent_cues must name only visible facts, and each cue must add evidence beyond the eyelid states themselves -- restating that one eye is closed, that the other is open, or that the two differ is not a cue. Do not write guesses such as possibly intentional, natural for the moment, cute, candid, or likely posing.
- overall_demeanor reads this person's whole visible body: posture, muscle tension, and engagement with the camera or the moment, independent of the eyelid state itself and independent of explicit_closure_context. This is a separate axis from whether a specific named story applies -- most photos have no specific story either way, and this field is what still tells them apart. alert_or_camera_ready: posture upright/engaged, muscles active, everything else in the frame (hands, head angle, other people) reads as poised and mid-moment -- a closed eye here specifically conflicts with the rest of the person's own readiness. calm_or_at_ease: posture relaxed, unhurried, or settled -- shoulders down, no tension, nothing else about this person suggests they were caught mid-action -- so closed eyes read as consistent with the rest of their own relaxed state, not as a mismatch with it. unclear: body language cannot be read (too small, occluded, cropped tightly to the face). Judge only this person's own body, not the scene or other people, and only from what the pose/tension actually shows -- not from the eyelid state itself, not from guessing why their eyes are closed.

Strict laughter evidence requires all three observations simultaneously: active_high_intensity_laughter, strong_bilateral periocular_contraction, and integral_to_expression. If any one is weak, obscured, or unclear, the laughter exception is not established.

Output compatibility fields in this observation pass as placeholders only: blink_candidate=false, eye_state="observation_only", severity="none", closure_intent="ambiguous", expression_coherence="unclear", blink_failure_confidence=0.0, and should_fix=false. Python overwrites them after validating the physical evidence. The Traditional-Chinese reason should concisely describe left/right eyelid morphology, readability, laughter muscle evidence, and any explicit closure context. Do not make a repair decision in reason.
"""

_GAZE_CONTACT_ANALYSIS_SECTION_LEGACY = """## Gaze-contact analysis
Intent: First determine strict lens contact from visible pupil/iris direction, binocular convergence, and eye focus—not merely face direction—then assess edit feasibility and produce a concrete repair handoff only when the gaze rules approve correction.

Core policy:
- Judge camera gaze by the eyes only: pupil/iris direction, binocular convergence, eye-focus feeling, and whether the eyes feel lens-locked.
- Face direction, body direction, pose, scene type, and subject importance are supporting context only. They must never be used as proof of direct eye contact.
- A forward-facing face with pupils looking slightly above, beside, past, or through the camera is NOT direct_camera.
- near_camera is NOT direct_camera.
- Direct_camera is a strict positive label, not a default. Use it only when lens contact is visually obvious.
- Recall priority: when gaze_contact_detection is ON, missing a readable non-lens-looking subject is worse than flagging a borderline near-camera subject for review.
- If the eyes are visible and not unmistakably lens-locked, choose lens_contact_decision=not_looking_at_lens rather than looking_at_lens.
- Do not set not_looking_at_lens_confidence to zero because the off-camera gaze looks natural, candid, documentary, performative, or contextually appropriate.
- Do not reject gaze detection merely because the original gaze has a plausible reason.
- If the person is not looking at the lens and the eyes are readable, should_fix=true -- regardless of how natural, candid, or anatomically demanding the correction would be. How demanding the correction is only changes strategy_type and repair_strength (see the gaze_repair_strategy rules below), never whether to fix it.

Strict direct-camera criteria:
Assign direct_camera / looking_at_lens only when ALL are true:
1. both visible eyes, or the only visible eye, appear aimed at the camera lens/viewer;
2. the pupils/irises are centered or oriented consistently with camera-facing gaze;
3. the eyes feel engaged with the viewer rather than looking past, above, beside, downward, or through the lens;
4. there is no clear evidence of sideward, upward, downward, cross-focused, or unfocused gaze;
5. an ordinary viewer would immediately say "this person is looking into the camera," not merely "the face is turned toward me."

If any of these are not true and the eyes are still readable, do NOT label direct_camera. Use near_camera, off_camera, upward_side, downward, unfocused_camera, cross_focused, or ambiguous.
Borderline readable cases should not be promoted to direct_camera. If unsure between direct_camera and near_camera, choose near_camera / not_looking_at_lens.

Pupil / iris position checklist:
Before assigning direct_camera, inspect the pupil/iris position inside each visible eye opening.
- For a camera-facing face, the pupils/irises should appear positioned and aimed consistently with looking into the camera lens/viewer.
- If both pupils/irises are shifted toward the same side of their eye openings, the subject is looking sideways, not at the camera.
- If both pupils/irises are shifted upward within the eye openings, the subject is looking upward, not at the camera.
- If both pupils/irises are shifted downward within the eye openings, the subject is looking downward, not at the camera.
- If the pupils/irises are aimed toward upper-left, upper-right, lower-left, or lower-right relative to the camera, do not label direct_camera.
- If the face is centered or frontal but the pupils/irises are side-shifted, classify the gaze as near_camera, off_camera, upward_side, downward, or ambiguous based on the eye direction. Do not classify it as direct_camera.
- Do not use facial orientation, portrait-like framing, smile, centered composition, or product-demo posing to override visible pupil/iris displacement.

Readable-eye forced choice rule:
- If the eyes are readable, do not use unclear merely because the scene context is ambiguous.
- For readable eyes, choose one of these:
  1. looking_at_lens only if the eyes are unmistakably lens-locked;
  2. not_looking_at_lens if the eyes point even slightly beside, above, below, past, through, or near but not into the lens;
  3. unclear only when blur, occlusion, reflection, shadow, compression, face size, or closed eyes make the eye direction visually unsafe to judge.
- If the eyes are readable and lens contact is not obvious, the correct decision is not_looking_at_lens.

Direct-camera veto rule:
Do NOT assign direct_camera or lens_contact_decision=looking_at_lens if any of the following are visible:
- the pupils/irises are displaced toward the same side of both eye openings;
- the pupils/irises are visibly higher or lower than expected for lens contact;
- the eyes appear to look above, beside, below, or past the camera even when the face is frontal;
- the gaze direction could reasonably be described as up-left, up-right, side-left, side-right, down-left, down-right, above-camera, beside-camera, or past-camera;
- the eye focus appears engaged but directed at someone/something near the camera rather than at the lens.

If a direct-camera veto is triggered and the eyes are readable:
- set lens_contact_decision=not_looking_at_lens;
- set gaze_state to near_camera, off_camera, upward_side, downward, unfocused_camera, cross_focused, or ambiguous;
- keep lens_contact_confidence below 0.60;
- set not_looking_at_lens_confidence to at least 0.60 unless the eye direction is visually unclear.

Binary lens-contact gate:
- First output lens_contact_decision before scoring:
  - looking_at_lens: the eyes are clearly and unmistakably focused on the camera lens/viewer.
  - not_looking_at_lens: the eyes are visibly not focused on the camera lens/viewer, including near-camera, slightly-beside-lens, above-lens, below-lens, past-camera, or unfocused gaze.
  - unclear: the eyes are too blurry, occluded, tiny, shadowed, reflected, compressed, closed, or ambiguous to judge safely.
- The decision must be based on the eyes, not the face.
- Do not infer looking_at_lens from a centered face, frontal head pose, smile, portrait framing, or product-demo pose.
- If the pupil / iris position checklist or direct-camera veto indicates non-camera gaze, lens_contact_decision must be not_looking_at_lens, not looking_at_lens.
- If the eyes are readable and direct lens contact is not obvious, lens_contact_decision must be not_looking_at_lens.

Score mapping:
- If lens_contact_decision=looking_at_lens:
  - gaze_state should be direct_camera.
  - lens_contact_confidence should be >= 0.75.
  - not_looking_at_lens_confidence should be <= 0.25.
  - should_fix=false.
- If lens_contact_decision=not_looking_at_lens:
  - gaze_state must NOT be direct_camera.
  - lens_contact_confidence should usually be <= 0.40.
  - not_looking_at_lens_confidence should usually be >= 0.70 for readable eyes, and at least 0.60 for borderline-but-readable eyes.
  - should_fix=true whenever the eyes are readable (see Hard should_fix rule below); gaze_correction_reasonableness only shapes the repair strategy, not whether to fix.
- If lens_contact_decision=unclear:
  - gaze_state should be ambiguous or unclear.
  - both lens_contact_confidence and not_looking_at_lens_confidence should stay low or moderate.
  - should_fix=false unless the eye direction is still clear enough to edit safely.

Stage 1 -- Lens-contact detection:
- lens_contact_confidence measures only whether the eyes are actually focused on the camera lens.
- not_looking_at_lens_confidence measures only whether the eyes are not focused on the camera lens.
- Judge lens contact independently from scene context, pose, body action, gesture, product interaction, speaking/performance status, candidness, documentary feeling, or whether direct eye contact would "make sense" in the photo.
- The decision boundary is visual and eye-based only: look at pupil/iris direction, binocular alignment, and whether the visual axes appear to meet the lens.
- Context must not reduce not_looking_at_lens_confidence. Context affects only whether the gaze redirection is anatomically feasible and visually natural when applied, not whether the person is currently looking at the lens.
- A speaker, performer, athlete, demonstrator, or candid subject can still be not_looking_at_lens if the eyes do not visually meet the lens. Conversely, a posed portrait subject is not looking_at_lens unless the eyes visually meet the lens.
- Output lens_contact_confidence:
  - 0.90-1.00: unmistakable direct lens contact; eyes are locked onto the camera/viewer.
  - 0.65-0.89: probably looking at the camera, but not perfectly locked.
  - 0.35-0.64: near-camera or ambiguous; face may point forward but eye focus is not clearly on the lens.
  - 0.10-0.34: probably not looking at the lens; eyes look past, above, beside, downward, or unfocused.
  - 0.00-0.09: clearly not looking at the lens.
- Output not_looking_at_lens_confidence as the evidence strength that the person is NOT lens-locked. This should be high for near_camera, off_camera, upward_side, downward, unfocused_camera, or cross_focused states when the eye direction is visually clear.
- Do not collapse not_looking_at_lens_confidence to 0 just because the original gaze is contextually natural.

Stage 2 -- Anatomical gaze-redirection feasibility:
- Decide whether changing the subject's gaze to look at the camera would remain physically natural and anatomically plausible.
- gaze_correction_reasonableness measures whether redirecting the eyes, with a bounded moderate head/face orientation refinement if needed, would remain ergonomically and anatomically natural.
- It does NOT measure whether the corrected gaze preserves the original narrative, story, candidness, documentary realism, speech direction, or scene intent.
- This score feeds strategy_type, repair_strength, and how the repair should be carried out. It never feeds should_fix: a low score means "repair carefully and conservatively," never "leave the gaze unchanged."

Stage 3 -- Per-subject gaze repair strategy:
For every subject's gaze_issue, write gaze_repair_strategy as a concrete edit guide for this exact image, not a generic reminder. The edit model will follow this strategy literally, so vague or overly conservative wording will cause a weak edit.

Required strategy fields:
- original_gaze_read: describe what the eyes are doing NOW using visible pupil/iris evidence. Mention direction and failure type, e.g. "pupils/irises point slightly camera-right, so the gaze passes beside the lens" or "eyes aim above the lens".
- target_gaze_read: describe the ENDPOINT, not just improvement. Use wording like "both pupils/irises aimed at the camera lens; viewer reads unmistakable direct eye contact."
- eye_adjustment: say the practical direction of the pupil/iris correction for this subject, not just "improve eye contact". Example: "move both pupils/irises leftward and slightly downward toward camera-lens center."
- sclera_catchlight_adjustment: say how to keep eye whites and catchlights natural after the gaze change.
- head_face_adjustment: say whether to keep the head angle, slightly align it, or moderately align it toward the camera. If strategy_type=eyes_plus_head_face_alignment, this must not be "none".
- edit_instruction: write one or two direct English commands for the edit model. This is the most important handoff field. It must be executable without reading the rest of the gaze rules. Write it in clean, grammatical English only -- do not mix in a word or phrase from any other language (e.g. Chinese, Spanish). A single foreign word dropped into an otherwise-English sentence makes the instruction unparseable as a command.

Strategy type decision:
- eyes_only: choose this only when moving the pupils/irises and rebalancing sclera can naturally create unmistakable direct lens contact while keeping the current head/face angle. Use this mainly for near_camera or very mild off-camera gaze.
- eyes_plus_head_face_alignment: choose this when eye-only repair would still look like side-eye, near-camera gaze, looking past the lens, or an uncomfortable eye rotation. This authorizes the edit model to turn/tilt the face toward the camera within the bounded preservation rules.
- not_safe_to_repair: choose this only when gaze repair would genuinely require a large pose/head change, new face structure, changed expression, or unclear/invented eyes -- a physical/visibility constraint, never a narrative or contextual one. "The gaze looks natural/candid/intentional here" is not a valid reason to choose not_safe_to_repair. should_fix still stays true when this strategy_type is chosen: describe the safest partial adjustment that moves the gaze visibly closer to the lens (repair_strength=subtle), rather than describing no change at all.

Strategy strength consistency rules:
- If gaze_state is off_camera, upward_side, downward, unfocused_camera, or cross_focused and not_looking_at_lens_confidence >= 0.75, do NOT choose repair_strength=subtle unless the eyes are already extremely close to the lens and original_gaze_read explains why.
- If gaze_state=off_camera and failure_to_avoid includes "near-camera only", do NOT choose eyes_only unless eye_adjustment can clearly reach direct lens contact without side-eye.
- If the original face/head angle is a reason the subject still reads as looking away, choose eyes_plus_head_face_alignment and specify slight or moderate head_face_adjustment.
- If head_face_adjustment="none" while gaze_state=off_camera, the reason must explain why eyes_only is sufficient and why it will not stop at near-camera.
- If strategy_type=eyes_only, failure_to_avoid should normally include "pupil shift without direct lens contact"; if the subject is clearly off-camera, consider eyes_plus_head_face_alignment instead.
- If the target is "unmistakable direct eye contact", repair_strength should usually be moderate for off_camera cases.

Wording requirements:
- Do not write "將瞳孔輕微調整向鏡頭中心" for clear off_camera cases unless the gaze is already nearly direct. That wording is too weak.
- Prefer concrete commands such as: "Move both pupils/irises toward the camera-lens center and moderately turn the face toward camera until the viewer reads direct eye contact."
- success_condition must be stricter than "improved": it should state the final viewer read, for example "subject clearly looks straight at the camera lens" or "do not stop at near-camera gaze".
- preserve_during_repair should list only gaze-local identity and appearance protections, such as clothing, mouth expression, identity, and background. Never list body pose, shoulder direction, hands, stance, gesture, interaction points, or camera framing; later user-selected pose/framing options own those properties.
- failure_to_avoid should name likely gaze/face failure modes for this image, such as "near-camera only", "pupil shift without face alignment", "pupil shift without direct lens contact", "uncomfortable side-eye", "sharpen-only", or "unrequested face reconstruction".

Example strategy for a clear off-camera seated portrait:
{
  "strategy_type": "eyes_plus_head_face_alignment",
  "original_gaze_read": "The face is slightly angled and the pupils/irises read off-camera rather than lens-locked.",
  "target_gaze_read": "Both pupils/irises aim at the camera lens; viewer reads direct eye contact.",
  "eye_adjustment": "Move both pupils/irises toward the camera-lens center, not merely closer to it.",
  "sclera_catchlight_adjustment": "Rebalance sclera naturally around the new iris position and keep catchlights consistent with the original lighting.",
  "head_face_adjustment": "moderate yaw toward camera while preserving facial proportions",
  "repair_strength": "moderate",
  "edit_instruction": "Redirect the named subject's gaze to the camera lens using pupil/iris repositioning plus a moderate face yaw toward camera. Do not stop at near-camera; the final viewer read must be direct eye contact.",
  "success_condition": "Subject clearly looks straight at the camera lens.",
  "preserve_during_repair": ["mouth expression", "identity", "clothing", "scene"],
  "failure_to_avoid": ["near-camera only", "pupil shift without face alignment", "sharpen-only", "unrequested face reconstruction"]
}
- Judge this score by:
  - whether the required eye rotation is small enough to look natural;
  - whether the head direction and corrected eye direction are compatible;
  - whether the corrected gaze would preserve natural binocular alignment;
  - whether the corrected gaze would avoid a cross-eyed, divergent, strained, or anatomically awkward appearance;
  - whether the eyelids, eye opening, eye corners, and surrounding facial tension would still look natural after the gaze shift;
  - whether the eye region is visible and detailed enough to edit safely.
- Output gaze_correction_reasonableness:
  - 0.80-1.00: redirecting the gaze to the camera would be physically natural and require only a subtle, believable eye adjustment.
  - 0.60-0.79: redirecting the gaze to the camera would still be anatomically plausible and visually natural.
  - 0.35-0.59: redirecting the gaze may be possible, but the required eye rotation or head-eye relationship may start to look slightly awkward.
  - 0.00-0.34: redirecting the gaze would likely look anatomically implausible, overly strained, cross-focused, divergent, or inconsistent with the head pose.
- A gaze can be natural in the original story but still reasonable to correct if the corrected result would remain physically believable.

Decision labels:
- direct_camera: the eyes appear locked onto the lens/viewer. Use this only for clear, unmistakable lens contact.
- near_camera: the eyes are close to camera direction but not clearly lens-locked. near_camera does NOT count as direct camera gaze.
- off_camera: the eyes clearly look to the side or toward someone/something outside the camera.
- downward: the eyes look downward; this is gaze direction, not blink.
- upward_side: the eyes look upward and/or to the side.
- unfocused_camera: the face is oriented toward camera, but the eyes look vacant, past the lens, not converged on the lens, or mentally disengaged.
- cross_focused: eye alignment or convergence looks unintentionally mismatched, creating a crossed or divergent focus impression.
- ambiguous: hard to decide whether the gaze is intentional or missed, but the eyes are somewhat visible.
- unclear: eye direction cannot be judged due to blur, shadow, glasses/reflection, occlusion, compression, or low resolution -- or because no eye is visible at all (back of head, rear/near-rear view, face turned away from the camera, silhouette, hat/hair/mask fully covering both eyes) -- or because only about half the face and a single eye are visible (side/profile view, or the other half occluded by a person, object, hand, or hair), even if that one visible eye's direction looks clear.

Near-camera calibration:
- near_camera is not direct_camera.
- If the eyes are close to the lens direction but not clearly lens-locked, treat this as not fully looking at the camera.
- For near_camera, lens_contact_confidence should usually stay below 0.60.
- For near_camera, not_looking_at_lens_confidence should usually be at least 0.60 when the eyes are visible enough to judge.
- Do not use a forward-facing head or centered face as proof of direct_camera.

Hard should_fix rule:
- Compute should_fix from the scores, not from overall intuition.
- Set that subject's gaze_issue.should_fix=true if and only if all of these are true:
  1. lens_contact_decision=not_looking_at_lens;
  2. not_looking_at_lens_confidence >= 0.55;
  3. the eye direction is visually clear enough to correct safely;
  4. the visible-target exception below does not apply.
- gaze_correction_reasonableness is NOT part of this rule. A low reasonableness score changes strategy_type and repair_strength (see Stage 2/3 above), never should_fix. There is no reasonableness floor below which should_fix flips to false.
- For readable near-camera or slightly-beside-lens gaze, do not suppress should_fix merely because the miss is small. A small but clear lens miss is still a valid gaze-contact issue.
- Otherwise set gaze_issue.should_fix=false.
- Do not override this rule just because the original gaze is natural, candid, acceptable, documentary, contextually appropriate, intentional, or important to the original story. None of those are valid grounds for should_fix=false when the eyes are readably not on the lens.
- Natural original gaze and anatomically feasible gaze correction are different questions.
- Do not use subject size, main-subject status, foreground/background status, pose naturalness, action type, or narrative preservation as a deciding factor. Those factors affect only uncertainty or repair feasibility, not whether the eyes are looking at the lens.
- No-eyes-visible rule: when a person's face is turned away from the camera far enough that neither eye is actually visible -- back of head, near-total rear view, or a silhouette -- lens_contact_decision must be unclear and should_fix must be false, even if the head/shoulder/body orientation makes it obvious they are not facing the camera. Body or head orientation is never a substitute for seeing the eyes; do not infer, guess, or extrapolate a gaze direction for eyes you cannot actually see. This still needs a gaze_issue reading (per the output rules below), just with lens_contact_decision=unclear and should_fix=false, not a guessed not_looking_at_lens.
- Half-face rule: when only about half of a person's face is visible -- a side/profile view showing a single eye, or the other half of the face occluded by another person, an object, a hand, or hair -- lens_contact_decision must be unclear and should_fix must be false for that person, even when the one visible eye's direction looks clear. This applies to every person regardless of role, including the photo's main subject or anyone in an intentional group/portrait, not only incidental background people. A single visible eye is not enough evidence to safely judge or correct lens contact; do not extrapolate the hidden half of the face from the one eye you can see.

Visible-target exception (the ONLY exception to the rule above):
- If, and only if, the eyes are demonstrably converging on a specific object or person that is actually visible inside this photograph's own frame -- for example a phone or camera someone in the shot is holding up to take this very picture, a product the subject is looking at in their own hands, or another visible person they are looking at and interacting with -- set gaze_target_in_frame.present=true, name and box that target, and set should_fix=false.
- This exception requires a nameable, boxable, visible target the gaze plausibly aims at. It does NOT cover looking down, looking to the side, looking into the distance, looking "away" or "off-camera" in general, or any case where you cannot point to a specific box in this frame the eyes are aimed at -- those stay should_fix=true under the hard rule above. An unnamed, unboxed "this gaze looks intentional/natural" is exactly the excuse this exception must never become; gaze_target_in_frame.present must not be set to true without a real box.
- The most common legitimate case: a selfie or group photo where a phone/camera held up by someone in the shot is visible in frame. The true camera for that shot is that visible device, not an implied external viewpoint at eye level. When other subjects in the same photo are credibly looking at that same visible device, a subject whose eyes converge on it too is already looking at the actual camera for this shot -- forcing their gaze toward an abstract "face the viewer" direction instead would make them look at a different point than everyone else in the same photo, which is the failure here, not the fix.
- The exception changes should_fix only. Still judge lens_contact_decision, gaze_state, and the confidence scores honestly from the eyes first, exactly as the rules above describe.

Score-first reasoning order:
0. First check the no-eyes-visible rule and the half-face rule above, before anything else. If either applies -- neither eye visible, or only about half the face / a single eye visible (side-profile, occlusion) -- set lens_contact_decision=unclear and should_fix=false immediately and skip steps 1-6 for this person. A single visible eye that reads sharp, clear, or confidently-directed is exactly the case the half-face rule covers, not evidence against it; eye_focus_quality looking "sharp_engaged" on that one eye is never a reason to treat the half-face rule as not applying.
1. Determine lens_contact_decision from the eyes only. Do not use scene context, pose, action, or whether lens contact would be narratively appropriate.
2. Determine gaze_state from the eyes, not from face direction.
3. Assign lens_contact_confidence.
4. Assign not_looking_at_lens_confidence.
5. Assign gaze_correction_reasonableness based on anatomical / ergonomic feasibility only.
6. Derive should_fix strictly from the hard should_fix rule.
- Do not decide should_fix first and then back-fill the scores.
- The reason field must state first, before anything else, whether the no-eyes-visible or half-face rule applies to this person and why. Only if neither applies should the reason field go on to say whether the eyes are lens-locked or not lens-locked and explain how the scores lead to should_fix.

Strong evidence for not looking at the lens:
- Pupils/irises point slightly left/right/up/down relative to the camera even though the face points forward.
- The eyes appear to look past the lens, above the lens, beside the lens, at a person nearby, toward an audience, toward an object, or into the distance.
- The eyes look vacant, disengaged, cross-focused, or not converged on the viewer.
- One or both eyes lack the sense of contact even though the face is otherwise camera-facing.

Strong evidence that gaze redirection is anatomically feasible:
- The head pose is already close to camera-facing, or can become camera-facing with a bounded moderate natural yaw/pitch refinement.
- The required pupil/iris shift is small or moderate, not extreme.
- The corrected eyes would keep normal left/right eye alignment and convergence.
- The eyelids and eye opening can remain the same; the gaze change can be achieved through eye direction plus bounded head/face alignment.
- The expression can be preserved while changing the eye direction and aligning the face more directly toward the camera.
- The eye and face region are clear enough to edit without inventing new eye shape or facial structure.

Strong evidence that gaze redirection is NOT anatomically feasible:
- The head is turned so far away that camera gaze would require an unnatural eye rotation.
- Redirecting the gaze would create a strained side-eye, cross-eyed, divergent, or visually awkward look.
- The corrected gaze would conflict with the eyelid shape or surrounding facial tension in a way that looks physically unnatural.
- The eyes are too blurry, occluded, shadowed, reflected by glasses, low-resolution, or too small to adjust safely.
- The required correction would need changing head pose, face structure, eyelid openness, or expression, rather than only subtly redirecting the eyes.

Output rules:
- Fill gaze_issue independently for every subjects[] entry with is_person=true whose gaze is relevant, even when should_fix ends up false.
- If a person subject's eyes are readable and the person is not unmistakably looking into the lens, their gaze_issue must not default to looking_at_lens/should_fix=false just to skip the work.
- Use should_fix=false only for lens_contact_decision=looking_at_lens or lens_contact_decision=unclear (eyes too blurry/occluded/small to judge safely, not visible at all because the person faces away from the camera, or only about half the face/a single eye is visible). A readable off-camera, near-camera, or unfocused-looking gaze is should_fix=true; do not use should_fix=false to mean "correctable but better left as-is."
- When any subject's gaze_issue has should_fix=true, gaze repair is an approved edit request for that subject. It must not remain analysis-only.
- For every subject whose gaze_issue.should_fix=true, fill gaze_repair_strategy with a concrete, per-image guide. This is mandatory, not optional.
- Add a correction_plan item for every subject whose gaze_issue.should_fix=true. This is mandatory, not optional.
- Add a matching retouch_recipe item for every subject whose gaze_issue.should_fix=true. This is mandatory, not optional.
- Add a matching correction_axes entry including ["expression"] for every subject whose gaze_issue.should_fix=true.
- Add a matching correction_inverse entry that protects unrelated properties while explicitly allowing the named subject's pupil/iris direction, eye-focus direction, sclera balance, gaze-consistent catchlight placement, and, when needed, the exact head/face alignment change approved by the analysis.
- Treat the analysis output as the full repair specification. The edit model should not need to invent its own gaze-repair method.
- Therefore, for every approved gaze repair, the analysis must make the intended execution explicit in four places that all agree: that subject's gaze_issue.gaze_repair_strategy, correction_plan, correction_inverse, and retouch_recipe.
- The correction_plan item must be self-sufficient: name the subject, state that the gaze should be redirected toward the camera lens / direct_camera, state the exact allowed method (for example, eyes only, or eyes plus slight/moderate head-face alignment), and include the same concrete method as gaze_repair_strategy.edit_instruction.
- The correction_inverse item must explicitly say what to preserve and what is allowed to change for that subject. Do not leave these allowances implicit. It should mirror preserve_during_repair and the allowed local changes in gaze_repair_strategy.
- The retouch_recipe item must stay concise and user-facing, but still clearly indicate that this is a gaze-to-camera correction for the named subject and whether face/head alignment may be involved.
- Phrase the gaze correction action as a concrete repair instruction, not as opening eyes and not as a vague "make eye contact better" suggestion.
- Do not describe gaze repair only as "improve eye contact" or "enhance gaze"; state that the named subject's gaze should be redirected toward direct_camera / the camera lens and whether head/face alignment is part of the approved method.
- Preserve identity, overall eye shape, eyelid openness, eye color, glasses/reflections, mouth expression, brow expression, facial structure, skin texture, and lighting, except where the approved analysis explicitly allows a local change.
- Never use blink repair wording for gaze repair.
- If the reason the eye direction cannot be judged is that the eyes may be closed or half-closed -- original_gaze_read says something like "eyes closed" or "gaze direction not observable" -- that is lens_contact_decision=unclear, not not_looking_at_lens, and should_fix must be false on that subject's gaze_issue no matter how confident the rest of the photo's gaze judgments are. Do not write a gaze_repair_strategy that reopens the eyes and redirects them in the same instruction; eyelid state is blink_issue's question, not this one, and "the eyes might be closed" is never evidence that they are pointed away from the lens.

Analysis-to-edit handoff guardrail:
- When a subject's gaze_issue.should_fix=true, the analysis must hand off a fully actionable repair, not just a diagnosis.
- A no-op or underspecified handoff is a failure.
- Make the approved change strong enough, specific enough, and concrete enough that the edit model can execute it without inventing extra gaze logic.

Mini-model calibration:
- Default away from direct_camera unless the eyes are unmistakably lens-locked.
- False-negative guardrail: many failures come from calling near-camera or contextually natural off-camera gaze "direct_camera." Do not do that.
- Visible pupil/iris displacement overrides frontal face direction: if the face is frontal but both pupils/irises point up/side/down, do not call it direct_camera.
- A centered face with side-shifted or upward-shifted pupils is a not_looking_at_lens case.
- A face that looks portrait-like, formal, smiling, or centered is still not_looking_at_lens if the eyes are not lens-locked.
- Do not use broad scene context to zero out not_looking_at_lens_confidence.
- Do not treat face-forward as proof of direct eye contact.
- Do not reject gaze correction just because it changes the original story, speech direction, documentary feeling, or candidness.
- Do not decide should_fix by intuition.
- First judge whether the eyes are lens-locked.
- Then judge whether redirecting the gaze to the lens would be anatomically and ergonomically natural -- this only shapes strategy_type and repair_strength, never should_fix.
- Then compute should_fix strictly from the lens-contact thresholds (readable + not_looking_at_lens), independent of the anatomical feasibility score.
- The default output for any readable, not-lens-locked gaze is should_fix=true, even when the required adjustment is large or the strategy ends up being a conservative partial one.
- If the person is not looking at the lens, recommend the correction; use the anatomical feasibility score only to decide how boldly to describe the fix.
"""

_GAZE_DETECTION_LUNA_SECTION = """## Exact lens-contact detection
Intent: For every listed person, decide only whether the visible eyes point at the exact lens that captured this image. Do not decide whether the gaze is attractive, intentional, narratively appropriate, or worth editing. Do not produce a repair strategy; Python derives should_fix after this visual classification.

### Observation order

Record these physical observations before choosing lens_contact_decision:
1. gaze_observability: both_eyes / one_eye / unreadable.
2. eye_detail_quality: readable / borderline / unreadable.
3. head_pose: frontal / slight_yaw / strong_yaw / profile / rear_or_hidden / unclear.
4. left_eye_direction and right_eye_direction: toward_exact_lens / near_lens_left / near_lens_right / above_lens / below_lens / off_left / off_right / up / down / unclear / not_visible.
5. binocular_alignment: exact_lens_convergent / near_lens_convergent / off_lens_convergent / mismatched / unverifiable.

Use the matched face crop for pupil/iris detail. Use both the crop and full photo for head yaw, pitch, facial geometry, and camera-viewpoint interpretation. The crop's geometric center is not the camera lens.

Head direction is never proof of lens contact, but it is required geometric context for interpreting iris position. A turned face may still look at the lens through compensating eye rotation. A frontal face may still look beside the lens. Do not require centered irises when head yaw makes an offset anatomically consistent with exact lens contact.

### Strict exact-lens gate

- direct_camera / looking_at_lens is a strict positive label. Use it only when exact contact with the current lens is unmistakable.
- near_camera is always not_looking_at_lens.
- Looking at another nearby camera, the photographer's face, or a point slightly above, below, left, right, beside, or past the lens is not_looking_at_lens.
- A frontal, centered, smiling, posed, camera-aware, or engaging face is not evidence of exact lens contact.
- If readable eyes look generally toward the camera but exact lock is not unmistakable, use near_camera / not_looking_at_lens.
- If uncertain between direct_camera and near_camera, use near_camera / not_looking_at_lens.
- Use unclear only when blur, reflection, occlusion, closed eyes, compression, or insufficient detail makes direction physically unreadable. Do not use unclear for readable near-camera eyes.
- One visible eye may support a lens-contact classification, but binocular_alignment must be unverifiable. Repair safety is decided later; do not erase a readable detection merely because only one eye is visible.

### Labels and confidence

gaze_state:
- direct_camera: unmistakable exact-current-lens contact.
- near_camera: close to the current lens but landing beside, above, below, or past it.
- off_camera: clearly looking left or right away from the lens.
- downward: clearly looking down.
- upward_side: clearly looking up and/or sideways.
- unfocused_camera: eye axes visibly fail to converge on one lens point; never infer attention, emotion, or mental engagement.
- cross_focused: the two eyes visibly converge inconsistently.
- ambiguous: readable evidence conflicts, but direction is not physically unreadable.
- unclear: direction cannot be resolved from the pixels.

lens_contact_confidence measures exact positive lens contact:
- 0.80-1.00: unmistakable exact-lens lock; looking_at_lens.
- 0.40-0.79: near-camera or uncertain exact contact; not_looking_at_lens when eyes are readable.
- 0.00-0.39: clearly not looking at the exact lens.

not_looking_at_lens_confidence measures evidence for a lens miss:
- Use at least 0.55 for readable near-camera or off-camera eyes.
- Use 0.75-1.00 when the miss direction is clear.
- Keep both confidence values low only when lens_contact_decision=unclear.
- Do not assign high confidence to both sides. Their sum should normally be approximately 1.0 when the eyes are readable.

gaze_target_in_frame records observation only. Set present=true only for a specific, visible, boxable object or person the eyes clearly target. A phone is the current capture device only when visible mirror geometry or another explicit capture setup proves it; a merely visible phone is not enough. This field does not change lens_contact_decision.

### Hard-negative calibration

Treat each of these as near_camera / not_looking_at_lens, not direct_camera:
- a posed person whose face points at the camera but pupils land just beside the lens;
- a press-photo subject looking toward a neighboring photographer or nearby camera;
- a foreground subject whose gaze feels engaging but the two eye axes meet slightly beside or past the current lens.

Output only the structured gaze-detection fields. reason must briefly name the visible pupil/iris direction, head-pose geometry, and why exact lens lock is present, missed, or unreadable. Do not write should_fix, gaze_correction_reasonableness, or gaze_repair_strategy; those are derived later.
"""


_SMILE_ANALYSIS_SECTION = """## Smile analysis
Intent: Classify each visible face as neutral, gentle, broad, laughing, or unclear for optional edit-stage adjustment without treating an existing smile as a defect.

A smile is never a defect, so do not add it to correction_plan, detected_issues, or retouch_recipe.
Default to neutral. Only use gentle or above when a smile is clearly and unmistakably present.
- neutral: no clear smile; includes speaking, posing, winking, pouting, parted lips, visible teeth, or pleasant/friendly expressions without definite smile structure.
- gentle: clear but restrained smile.
- broad: strong cheerful smile.
- laughing: unmistakable laughter or intense joy.
Judge smile presence before smile intensity.
If the expression could reasonably be caused by speaking, pronunciation, posing, or another facial action, classify it as neutral.
Visible teeth, mouth openness, slight mouth-corner elevation, narrowed eyes, or raised cheeks do not establish a smile by themselves.
Require a coherent smile pattern, especially coordinated mouth-corner retraction/elevation that clearly reads as smiling rather than speaking or posing.
Prefer precision over recall: when uncertain between neutral and gentle, choose neutral.
Use unclear only when the expression cannot be seen reliably. Set adjust_safe=false only when editing could overwrite distinctive dental features or when the face is obscured, too small, or turned too far away to render a reliable expression. adjust_safe measures technical edit feasibility, not whether the captured emotion should be preserved. Speaking, laughter, strong emotion, or another visible facial action does not by itself make smile adjustment unsafe.
"""

_ASPECT_RATIO_POLICY_ORIGINAL = """
## Aspect-ratio policy

- The user explicitly requested that the final output keep the original photo's aspect ratio.
- Set recommended_aspect_ratio to "original".
- If crop_box.enabled is true, crop_box.aspect_ratio must be "original" (or blank).
- Every crop candidate must preserve the source aspect ratio.
- Do not select a named ratio that changes the original proportions.
"""

_ASPECT_RATIO_POLICY_AUTO = """
## Aspect-ratio policy

- Select recommended_aspect_ratio only from the supported output set declared above.
- crop_box.aspect_ratio and every crop candidate aspect_ratio must equal recommended_aspect_ratio exactly.
- The normalized x/y/w/h values are a composition proposal, not permission to use a different ratio.
- Downstream deterministic geometry will fit the crop rectangle to this selected ratio, so protect the ROI, subject extent, and protected regions with enough breathing room.
"""

# Formatted with the W:H label the user picked. Unlike the auto policy, the
# ratio is an input to the composition here, not an outcome of it.
_ASPECT_RATIO_POLICY_FIXED = """
## Aspect-ratio policy

- The user explicitly requested {ratio} as the output shape, so the choice is already made: set recommended_aspect_ratio to "{ratio}". Never answer "original" or any other value, and ignore every earlier rule that lets you fall back to "original" when no reframing seems beneficial -- that fallback does not apply once the user has named the shape.
- Plan the composition for a {ratio} frame from the start. Do not compose for the source shape and leave the ratio to be reached afterwards; the rectangle you return is the composition that gets executed.
- crop_box.aspect_ratio and every crop candidate aspect_ratio must be "{ratio}", and each crop_box's own x/y/w/h must itself compute to it: (w * original_image_width) : (h * original_image_height) must equal {ratio}.
- Set crop_box.enabled to false only when the complete source frame already has this shape and no reframing improves it. Otherwise return an enabled crop, including when it ends up close to full frame.
- {ratio} may differ sharply from the source frame, so reaching it can cost much more of the photo than a freely chosen ratio would. Keep the ROI, subject extent, and protected regions inside the crop with breathing room; when the shape cannot hold all of them, keep the ROI and name in crop_box.reason what was given up.
- Downstream deterministic geometry still fits the rectangle to {ratio} exactly, so a proposal that is already exactly that shape is the one that survives unchanged.
"""

# Appended to the fixed-ratio policy only when the gap between the source and
# the requested shape is wide enough that a crop has to eat into the subject
# (EXPAND_PERMISSION_RETAINED_THRESHOLD). The mode is decided here, during
# composition, rather than by the edit model: only the analysis can see whether
# the subject still has room to lose, and a mode chosen after the composition
# notes are written would contradict them.
_ASPECT_RATIO_POLICY_EXPAND = """
- {ratio} is not this photograph's shape, so reaching it by cropping alone would take {lost_pct}% of its {lost_axis}. Decide how it is reached and record the decision in reframe_mode:
  - "crop": the photograph has {lost_axis} to spare. The subject, ROI and protected regions still sit comfortably inside a {ratio} rectangle, and what leaves the frame is background, edge clutter, or context the picture does not need. This is the normal answer -- prefer it whenever the photograph allows it.
  - "expand": the subject already fills the {lost_axis}, so any {ratio} rectangle that keeps the composition intact would cut into a person, a face, a held object, or the structure the picture is built on. The scene is then continued past the photograph's {grow_edges} instead, and nothing already visible leaves the frame.
- Decide this from the photograph, not from the {lost_pct}%. A small cost is still the wrong answer when the subject is already tight against both {lost_axis} edges, and a large one is still right when the {lost_axis} is mostly background. A crowded group filling the frame edge to edge, a subject already cropped close at the {lost_axis}, or a composition whose meaning depends on both {lost_axis} edges are the cases for "expand"; a subject with space around it is a "crop".
- State the reason in reframe_mode_reason: what specifically would be cut, or what specifically has room to go.
- When you choose "expand", compose crop_box for the photograph's own shape rather than for {ratio} -- the framing you would choose if the output shape were not a constraint. Deterministic geometry then continues the scene past the {grow_edges} to reach {ratio}, so a crop_box that is already exactly {ratio} would defeat the choice. When you choose "crop", compose it for {ratio} exactly, as the rules above require.
"""

_OVERALL_ANALYSIS_PERSONA = """You are a senior photography director, colorist, and high-end retoucher.
Analyze the uploaded photo and return only valid JSON. Keep the JSON keys in English. Every user-visible string value must be Traditional Chinese.

Analysis goal:
- Help a user understand how their photo can be improved.
- Recommend a tasteful retouch that preserves the same subject, same scene, and same moment.
- Choose the strongest final framing by actively comparing the captured frame with at least one viable alternative crop.
- Identify every subject in the photo (people, animals, products, food, objects) so a separate, dedicated pass can judge each person's blink/hair/gaze/smile state -- your own output does not include those four fields at all.

Hard limits:
- Do not change the subject into something else.
- Do not add text, logos, watermarks, extra people, fantasy elements, or a new scene.
- Do not rebuild, replace, or materially simplify the original environment. Preserve scene truth: recognizable background structure, surface material, perspective, and real spatial relationships.
- Cleanup means reducing distraction within the same scene, not turning a casual/lifestyle/documentary scene into a studio, catalog, or different location unless the user explicitly asks for that.
- For people, preserve identity, face, body shape, pose, and natural skin texture.
- For products, food, pets, architecture, and landscapes, preserve shape, material, and scene truth.

Subject inclusion rule (for people specifically):
- subjects[] lists this photo's actual people among its subjects -- the intended human subject(s) and anyone clearly posing or interacting with them -- not every visible human, including ones the local face detector or your own scan of the photo turns up. Strong models in particular tend to over-include: finding and boxing every technically-visible person, even incidental strangers, is not the goal here.
- Exclude a visible person from subjects[] when they read as an incidental background bystander: someone unrelated to the photo's own subject(s) who merely happens to be in frame -- a stranger walking past in a street/travel photo, an unrelated person faintly visible in a crowd, a passerby in the background of a portrait or product/food shot.
- Signals that someone is likely an incidental bystander rather than an intended subject (none of these is decisive alone; weigh them together): they show only their back or the side/rear of their head with no facial detail at all; they are small, distant, blurred, or heavily occluded relative to this photo's real subject(s); they are not looking toward, posed with, or interacting with the photo's main subject(s); the photo's framing/composition clearly centers on someone or something else and this person sits at the edge or deep in the background.
- Do not use sharpness, size, lighting, or facing-the-camera as evidence that someone belongs in subjects[] -- a candid photo routinely shows its real subject(s) mid-action, turned away, or partly out of frame, while an unrelated stranger nearby (another table, another bench, another group) simply happens to be well-lit and facing the camera. That stranger being the clearest, largest, or most camera-facing face in the photo is never by itself a reason to include them. Decide from spatial/social grouping instead: are they seated at the same table, part of the same conversation or physical cluster as the photo's own subject(s), or just someone else in the room who happens to be visible? A sharply-visible face belongs in subjects[] only if it also passes this grouping test -- clarity is not a substitute for it.
- These signals do not automatically disqualify someone from getting a subjects[] entry. A main subject deliberately photographed from behind, or a close companion standing slightly behind/beside the main subject, still belongs in subjects[] -- box, label, prominence, reason -- even when their face is not visible at all. Whether that entry also gets is_person=true is a separate question, decided by the is_person definition below, not by this bystander test.
- is_person definition: set is_person=true only when this subjects[] entry both (a) passed the inclusion test above and (b) has enough of their actual face visible to judge and, if needed, correct it -- eye state, gaze direction, smile, front hair. Set is_person=false whenever either is untrue: an incidental bystander (see above), or a genuine included subject whose face cannot be read at all -- shown only from the back or the side/rear of the head, a silhouette, or otherwise fully obscured. is_person=false is a full switch, not just for the face: it also skips them from the dedicated portrait-QC pass and the local face-box containment rule below, since there is nothing about their face for a close-up portrait review to act on. Never set is_person=true just because someone is a real, included person; only set it when their face is actually there to analyze.
- Judge from the whole picture: would someone looking at this photo say "the people in this photo" includes this individual, or would they say "there's also a stranger in the background"? Only the former belongs in subjects[] at all, whether as is_person=true or is_person=false.
- A real face that fails this inclusion test (a genuine incidental bystander) still gets a local_face_review entry -- with matched_subject_index=-1 and a reason naming the exclusion -- it just does not get a subjects[] entry of its own.
- This rule is about which people belong in subjects[]; it has no bearing on non-person subjects (products, food, animals, objects), which are still selected and ordered by the existing prominence rules in Coordinate rules below.

"""

_RETURN_SHAPE_OVERALL = """Return exactly this JSON shape:
{
  "photo_summary": "一句話描述照片內容與整體觀感。",
  "subject_category": "人物 / 食物 / 產品 / 風景 / 建築 / 寵物 / 其他",
  "scene_category": "室內 / 戶外 / 街拍 / 商業 / 旅行 / 日常 / 其他",
  "main_subject": "主要主體是誰或什麼。",
  "scene_type": "更具體的場景類型。",
  "aesthetic_diagnosis": "說明照片目前不夠精緻的原因。",
  "detected_issues": ["2 到 6 個具體問題。"],
  "edit_philosophy": "整體修圖哲學。",
  "edit_intent": "定義成品唯一的光影與色彩目標：整體亮度、主體與環境的明暗關係、色調氛圍，以及完成後最吸引人的視覺特質。",
  "composition_mood": "完成後希望呈現的氛圍。",
  "composition_intent": "這張照片最適合的構圖意圖，例如主體展示、生活敘事、動作瞬間、環境紀實、圖形平衡、負空間、親密肖像等。",
  "framing_intent": "取景意圖，例如 detail / close-up / upper-body / half-body / three-quarter / full-body / environmental / object-complete / context-preserving。",
  "layout_method": "center / thirds / symmetry / diagonal / vertical-horizontal / leading-lines / pattern / negative-space，加上中文理由。",
  "portrait_framing": "若不是人像可寫不適用；若是人像，說明 close-up, upper-body, half-body, full-body 等框法。",
  "composition_strategy": "構圖與裁切策略。",
  "visual_balance_strategy": "左右與上下視覺重量如何處理。",
  "empty_space_strategy": "畫面空白、留白或大面積背景的構圖功能；若沒有功能，說明如何縮減或重新平衡。",
  "alignment_strategy": "水平線、垂直線與透視穩定性；說明是否需要輕微旋轉或透視校正，或為何保留傾斜。",
  "distraction_strategy": "裁切後重新評估背景/前景元素：哪些是真正干擾、哪些是場景脈絡、哪些只是中性背景；針對每個判定為干擾的元素，說明具體且可執行的處理方式，避免只描述抽象的感受或意圖。",
  "scene_preservation_strategy": "如何保留原始場景內容、背景結構、材質身分、透視與空間關係；不要在此限制曝光、白平衡、對比、色相或飽和度。",
  "roi_anchor": "主視覺焦點應落在哪裡，以及為什麼。",
  "recommended_aspect_ratio": "original / 1:1 / 3:2 / 2:3 / 3:4 / 4:3 / 4:5 / 5:4 / 9:16 / 16:9 / 21:9 / 1:4 / 4:1 / 1:8 / 8:1",
  "aspect_ratio_reason": "為什麼這個輸出比例最適合主體、陪襯元素、留白與畫面意圖。",
  "subjects": [
    {
      "label": "主體描述,例如 左二女性 / 中間男性 / 蛋糕。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "prominence": 1,
      "is_person": true,
      "local_detection_index": 1,
      "skin": "is_person=true 時必填，否則寫「不適用」。依序寫：原生膚色身分（在受光平均處判讀深淺與底調，不讀高光或陰影）；場景光色（臉上實際受光的方向與色性，以及是否為應保留的情境光）；非預期偏色（僅限無法由場景光解釋，或與頸、耳、手不連貫的偏黃、偏綠、偏紅、偏洋紅、偏灰；沒有則寫無）。原生膚色身分不可改變。不可把背景、草地、服裝或其他高飽和物體的主色當成膚色受光；只有可見光源或反射確實落在皮膚上才算情境光。夕陽、鎢絲燈、舞台燈、霓虹與反射色等合理光色不可被中性化。",
      "reason": "為何突出:最大 / 最中央 / 最清晰 / 互動焦點。",
""" + "\n" + """    }
  ],
  "local_face_review": [
    {
      "detection_index": 1,
      "is_real_face": true,
      "matched_subject_index": 0,
      "reason": "說明判斷依據；若 is_real_face=false 或 matched_subject_index=-1，必須說明是偵測器誤判（背景花紋/反光/印刷人臉等，並具體指出是什麼）還是真人臉但屬於陪襯路人（依 Subject inclusion rule 說明原因）。"
    }
  ],
  "roi_box": {
    "x": 0.25,
    "y": 0.20,
    "w": 0.45,
    "h": 0.50,
    "reason": "ROI 包住主體最重要、不可裁掉的區域。"
  },
  "subject_extent_box": {
    "x": 0.15,
    "y": 0.08,
    "w": 0.70,
    "h": 0.82,
    "reason": "依照場景意圖與框法包住完整語意主體；不一定等於所有可見身體或所有可見物件。"
  },
  "protected_regions": [
    {
      "label": "important subject part / structural edge / fine detail / supporting element",
      "x": 0.0,
      "y": 0.0,
      "w": 0.1,
      "h": 0.1,
      "reason": "此區域是主體語意或構圖平衡的一部分，不應被裁掉或貼邊。"
    }
  ],
  "lighting_strategy": "依 edit_intent 說明成品的整體亮度，以及主體與環境的曝光、陰影、高光和對比安排；這是實現 edit_intent 的光影計畫，不是另一個成品目標。",
  "memory_color_strategy": "依 edit_intent 說明場景的白平衡、冷暖、色相與飽和度安排。膚色獨立保護，維持每個人的原生深淺、底調與色度，允許曝光調整並只修正非預期偏色。",
  "evaluation_criteria": ["完成後用來判斷成功的標準。"],
  "crop_recommendation": "若需要裁切，說明裁哪裡與為什麼；不需要則說明保留原構圖。",
  "reframe_mode": "crop 或 expand 二選一。除非本回合的長寬比政策明確要求你做這個選擇，否則一律填 \"crop\"。",
  "reframe_mode_reason": "選擇的理由；政策未要求選擇時填空字串。",
  "crop_box": {
    "enabled": true,
    "x": 0.0,
    "y": 0.0,
    "w": 1.0,
    "h": 1.0,
    "aspect_ratio": "",
    "reason": "裁切理由。若裁切，aspect_ratio 應符合 recommended_aspect_ratio；若保留原構圖可留空或寫 original。"
  },
  "crop_candidates": [
    {
      "id": "ai-primary",
      "label": "主要建議裁切",
      "crop_box": {
        "enabled": true,
        "x": 0.0,
        "y": 0.0,
        "w": 1.0,
        "h": 1.0,
        "aspect_ratio": ""
      },
      "composition_intent": "這個候選如何服務主體、陪襯、留白、場景與視覺平衡。",
      "tradeoffs": "這個候選保留與犧牲了什麼。"
    }
  ],
  "crop_intent": {
    "trim_left": false,
    "trim_right": false,
    "trim_top": false,
    "trim_bottom": false,
    "subject_anchor": "center",
    "looking_direction": "unknown",
    "remove_edge_distractions": []
  },
  "crop_risk": {
    "needs_validation": false,
    "risk_level": "low",
    "reasons": []
  },
  "correction_plan": ["1 到 6 項，實際會做的具體技術調整，供編修模型執行；例如「提升整體曝光 0.3 EV 並拉回高光」。只列這張照片真正需要的項目；若某個面向已經夠好就不要列，不要為了湊數而發明問題。照片本來就平衡時，只列一項最必要的即可。"],
  "correction_axes": [["exposure", "highlight"]],
  "correction_inverse": ["與 correction_plan 一一對應：若使用者取消勾選這一項，改成告訴編修模型「維持原樣」的說法；例如對應上例可寫「維持原圖的整體曝光與高光亮度，不要提亮，也不要壓暗或改變高光的層次」。"],
  "retouch_recipe": ["與 correction_plan 一一對應、順序相同、項數相同，但改寫成給使用者看的簡潔白話版本；例如對應上例可寫「讓照片更明亮」。"],
  "preserve_rules": ["必須保留的身份、內容、結構、幾何或材質身分；場景曝光、白平衡、對比、色相與飽和度由 edit_intent 和 correction_plan 決定，不列為保留項目。"]
}

local_face_review must contain exactly one entry per "Face crop N" / "Local face detector reference N" you were given, in the same order, with detection_index equal to that N -- this is a completeness check, not a sample. For each entry:
- is_real_face=false whenever the crop/coordinate does not actually show a real human face (background clutter, pattern, reflection, printed/painted face); matched_subject_index must then be -1, and reason must name what it actually shows.
- is_real_face=true and matched_subject_index=-1 whenever the detection is a real face that fails the Subject inclusion rule (an incidental bystander); reason must say so and name which signal(s) from that rule apply.
- is_real_face=true and matched_subject_index=<that subject's 0-based index> whenever you created a subjects[] entry from this detection; that subjects[] entry's own local_detection_index must equal this same detection_index, so the two arrays cross-reference each other consistently in both directions.
Every subjects[] entry needs a local_detection_index: for a person (is_person=true) created from a local detection, set it to that detection_index; set it to -1 for a person you added yourself from the full photo with no backing detection, and -1 for any non-person subject (is_person=false), since the face detector never applies to those.
"""

_OUTPUT_ALIGNMENT_RULES_OVERALL = f"""## Execution contract

- correction_plan, correction_axes, correction_inverse, and retouch_recipe describe the same adjustments in the same order and have the same number of entries.
- Write correction_plan as the precise editor instructions that realize edit_intent. Keep each item actionable on its own.
- Tag each correction_plan item with every axis it changes, using only this vocabulary: {_AXIS_VOCABULARY}.
- Write retouch_recipe as the user-facing version of the same item, retaining its region, main action, and useful strength.
- Write correction_inverse for the exact same region and properties as its correction_plan item.
- Treat reframing and its output aspect ratio as one reviewable decision. When reframing is beneficial, write exactly one crop-tagged correction_plan item containing every composition crop/trim/frame-relative subject repositioning or scale action; never split output shape into another item.
- The matching retouch_recipe item must start with "重新構圖：" and describe only the semantic crop result, for example "重新構圖：收窄左右空間，讓家庭更集中。"
- Do not put a numeric aspect ratio in correction_plan, retouch_recipe, crop_recommendation, or other user-facing prose. Local deterministic geometry resolves the executable ratio from crop_box and inserts the result into the review text afterward.
- The matching correction_inverse must preserve the original aspect ratio, outer boundaries, subject scale and placement, and empty-space balance, with no composition crop, expansion, or recomposition. It must not preserve tilt or perspective.
- When the original frame is selected, do not add a crop-tagged review item just to say that it is unchanged.
- Whole-frame rotation, horizon/vertical straightening, and keystone/perspective correction are not reframing. When beneficial, put them in one separate correction_plan item tagged with ["alignment"]; its retouch_recipe starts with "水平／透視校正：" and its inverse preserves the original tilt/alignment/perspective without blocking a separately approved crop.
- The minimal edge trim needed to remove exposed wedges after alignment correction belongs to the alignment item. Keep the selected output ratio and composition as close as possible, and do not add the "crop" axis solely for that technical trim.

"""

_COORDINATE_GEOMETRY_RULES_OVERALL = """## Coordinate and geometry rules
Intent: Produce normalized subject and crop geometry that downstream framing logic can use safely and consistently.

- crop_box, roi_box, subject_extent_box, and protected_regions use normalized original-image coordinates. x/y are top-left, w/h are width/height: the origin (x=0, y=0) is the photo's own top-left corner, x increases toward the photo's right edge, y increases toward its bottom edge.
- Any "left"/"right"/"middle" wording -- in a subjects[].label, a reason field, or anywhere else describing where someone or something sits in the frame -- is always from the viewer's perspective looking at the photo (the side with the smaller x is "left"), never that person's own anatomical left/right. Keep this the same reference frame you use when writing box x/y/w/h for that same entry, so the coordinates and the words describing them never disagree about which side of the frame something is on.
- roi_box is the focal area. subject_extent_box is the semantic subject area required by the intended framing; it is not automatically every visible body part, foreground object, furniture piece, or background element.
- subjects lists the candidate main subjects (people, animals, products, food, objects) ordered so index 0 is the photo's true main subject -- what the photo is actually about, consistent with main_subject, roi_box, and the composition intent. prominence means semantic importance as the subject, NOT which element is merely brightest, most colorful, or most saturated. Incidental background or decor -- flowers, plants, props, tableware, posters, scenery -- must rank lower than the real subject even when it is visually eye-catching; e.g. in a food photo the dish is the subject, not a bright vase of flowers behind it. Always output at least one entry, even for a single-subject photo.
- Each subjects[].box must tightly frame EXACTLY ONE individual person/animal/object, never a group. In a multi-person or multi-object scene, output one separate entry per individual, each with its own tight box; do NOT return a single box that spans several subjects or most of the frame. If there are four people, subjects must contain four entries, each boxing one person -- index 0 is only the single most prominent one, not all four. This holds even when main_subject or photo_summary describe the group as a whole; subjects is always per-individual so a downstream close-up can isolate exactly one.
- For a person-type subject whose face is visible anywhere in the photo, subjects[].box must include that entire visible head. A box that starts at or below the chin/neck/shoulders and excludes the face -- even if it otherwise fits the rest of the visible body tightly and looks "correct" by itself -- is wrong and must be corrected; the head is not an optional part of a person subject's box.
""" + "\n" + """- For every "Local face detector reference" you relied on to create a subjects[] entry (see "Zoomed face-crop images" above and its local_face_review requirement), that person's subjects[].box must fully contain the detected face box -- its top/left/right/bottom edges at or beyond the detected face box's own edges on every side, never cutting into or excluding it. A subjects[].box that does not contain the face detection it was created from is wrong and must be corrected before finalizing the analysis; this containment check is mandatory whenever such a detection exists, not merely a suggestion for when your own estimate looks "noticeably off". subjects[].box must still extend beyond just the face to cover that individual's full semantic extent -- containing the face box is a minimum, not the whole box.
- protected_regions contain only indispensable subject details and role cues retained by the selected composition. When crop_box.enabled=true, keep every protected region inside crop_box with breathing room; leave optional background structure and intentionally trimmed subject/body edges outside these boxes.
- Choose "original" with crop_box.enabled=false when the captured boundaries support the intended subject emphasis, balance, context, and edge cleanliness as well as any viable crop.
- A same-ratio or near-full composition crop remains valid when it removes an edge distraction, strengthens the subject anchor, or gives the frame a cleaner visual endpoint. Do not enable crop_box only to straighten the horizon or correct perspective; alignment owns its own minimal cleanup trim.
- The output does not need to preserve the original canvas aspect ratio in auto mode. Choose exactly one value from this supported output set: original, 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1.
- Changing the outer canvas ratio never permits changing the internal proportions of the photographed content. Plan the new frame only by cropping/recomposition; never stretch or squash the original scene to fill it.
- The crop_box.aspect_ratio and every crop_candidates[].crop_box.aspect_ratio must match recommended_aspect_ratio exactly. Do not describe one ratio while returning crop geometry intended for another ratio.
- This is not just a label match: each crop_box's own x/y/w/h must itself compute to that same ratio -- (w * original_image_width) : (h * original_image_height) must equal the stated aspect_ratio, for both crop_box and every crop_candidates[].crop_box. A correctly-labeled aspect_ratio paired with geometry sized for a different ratio is an error, not a valid answer.
- Prefer ordinary photographic ratios (1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9). Use 1:4, 4:1, 1:8, or 8:1 only when strongly justified and subject safety remains excellent.
- Choose aspect ratio from subject type, scene intent, supporting elements, empty-space function, visual balance, and breathing room.
- Set crop_risk.needs_validation to true when composition is ambiguous, crop is aggressive, ROI is near an edge, protected regions are near edges, framing could be misread, or alignment/context tradeoffs need judgment.
"""

_COMPOSITION_DECISION_FRAMEWORK = """## Composition decision framework
Intent: Choose framing from subject meaning, scene context, visual balance, useful empty space, and preservation needs rather than a fixed composition formula.

- For completeness-first presentation, catalog-like, symmetrical, circular, stacked, shape-critical, food/product/object/still-life images, centered or near-centered balance is often competitive.
- For lifestyle, story, portrait, street, travel, action, gaze, gesture, interaction, leading lines, asymmetry, or intentional negative space, off-center, thirds, diagonal, symmetry, or context-preserving framing may be stronger.
- Empty space must have a visible job: direction, story, scale, calmness, graphic balance, leading lines, room for action, environmental context, rhythm, or intentional negative space. Preserve generous breathing room when it serves those jobs; reduce or rebalance empty/background areas when they do not.
- Reassess distractions after the proposed crop. A visible element may be harmful, contextual, or neutral depending on the final frame. Preserve contextual and neutral elements. For a harmful distraction touching an outer edge, place it outside the crop when the subject and useful context remain comfortably protected; use local de-emphasis when cropping would sacrifice more important content.
- Check alignment when credible horizontal/vertical references exist. Correct unintended tilt or keystone only when it feels unstable or physically implausible; preserve intentional slant only when it has a clear visual purpose.
- Preserve scene identity, background structure, surface material, perspective, spatial relationships, and real-world context. The captured outer boundaries remain independently editable.
- For people and animals, the face ROI alone is not automatically the whole subject. Protect the semantic subject extent and role cues required by the framing. Partial-person crops are acceptable only when intentional, clean, and supportive of the scene.
- For action, eating, gesture, interaction, or expression-driven moments, protect expression, hands, held objects, interaction points, and relevant context. Preserve lower body, furniture, foreground objects, or peripheral context only when they support posture, story, scale, balance, or scene intent.
- For fashion, posture, full-body portrait, dance, sports, product shape, architecture, or object-completeness tasks, preserve the full visible form more conservatively.
- Judge real-image breathing room from actual photo content, not transparent padding, UI checkerboard, or empty canvas.
"""

_ANALYSIS_RESPONSIBILITIES_OVERALL = """## Analysis responsibilities
Intent: Translate photographic problems and opportunities for aesthetic improvement into an actionable, internally consistent correction_plan, leaving every eye, gaze, and hair judgment to the dedicated portrait-QC pass that runs after this one.

- Observe the image, identify semantic roles, and describe why the photo can improve.
- Define the main subject, useful supporting elements, optional context, harmful distractions, ROI, subject_extent_box, and protected_regions.
- Decide the composition intent and framing intent before proposing any crop. Centering, thirds, symmetry, leading lines, and negative space are candidate strategies, not fixed rules.
- Treat edit_intent as the single finished light-and-color target. Define a specific overall brightness, tonal relationship, and scene palette that make this photograph polished and visually compelling.
- Make lighting_strategy and memory_color_strategy implementation plans for that same target. They must strengthen edit_intent rather than introduce a different mood, brightness target, or instruction to retain the captured scene color.
- Translate the target into correction_plan. Each item names its region, concrete adjustment, useful strength, and visible contribution to the finished result. Include the light and color work needed to create the target, not only defect repair. Word it concretely and executably -- if an item only conveys an abstract feeling or impression rather than an operation someone could actually carry out, rewrite it to name that operation.
- Protect skin color independently according to subjects[].skin. Scene exposure, white balance, contrast, hue, and saturation remain available to achieve edit_intent.
- Size correction_plan to what the photo actually needs, choosing the few adjustments with the greatest visual benefit. An already successful photograph may need none.
- correction_plan, correction_axes, correction_inverse, and retouch_recipe must not include any eye/blink repair, gaze/eye-contact repair, or hair correction. A separate portrait-QC pass judges each person's blink_issue/hair_issue/gaze_issue/smile_issue after this analysis; any approved blink/gaze repair is executed directly from that person's own blink_issue/gaze_issue, never through correction_plan. Write correction_plan here as if every subject's eyes, gaze, and hair are already fine, covering composition/crop/color/skin/product corrections and aesthetic improvements supported by the photo.
- Write preserve_rules only for identity, content, geometry, scene structure, and material identity. Do not use preserve_rules to retain scene exposure, white balance, contrast, hue, saturation, or captured color atmosphere; those are governed by edit_intent and correction_plan. Keep eyes, blink, eye contact, gaze, hair, and smile out of preserve_rules because the portrait-QC pass governs them.
- Prioritize natural-looking skin over global color neutrality. Preserve each person's complexion, skin chroma, and intentional scene illumination; correct only a cast that is distracting, inconsistent across connected skin, or not plausibly explained by the light. Never write a correction_plan item that makes skin warmer, yellower, rosier, healthier-looking, more vivid, or more saturated. If correction is needed, name the unwanted hue, affected skin area, and corrective direction. When subjects[].skin says no unexpected cast, write no skin temperature, hue, or saturation adjustment. A global white-balance, hue, or saturation adjustment may refine the image palette, but it must not force skin to follow the background grade.
- Choose the strongest framing independently of the output aspect-ratio preference. Compare the complete frame with at least one materially different viable crop that conforms to the selected ratio, treating both as equally eligible. Compare subject emphasis, edge distractions, empty-space function, balance, and protected content; select the crop only when it produces a clearly stronger composition. Return crop_candidates for viable alternatives, with each candidate explaining its composition intent and tradeoffs.
- Make an approved composition crop visibly consequential: normally remove at least 8% of the original span on one axis (w <= 0.92 or h <= 0.92) and change subject scale, placement, or edge content. Use original for smaller cosmetic trims; alignment may still use its minimal cleanup crop.
- Populate subject_extent_box and protected_regions for the selected final composition. Cover the semantic subject and must-retain role cues only; keep optional context and intended-to-remove edge content outside these protection boxes so crop validation can honor the chosen frame.
- When crop_box.enabled=true, verify that every protected_regions box fits inside crop_box with breathing room. Resolve any conflict by repositioning the crop or limiting protection to the indispensable detail; background structure and intentionally trimmed subject/body edges remain outside protected_regions.
- Local_face_review completeness check: before finalizing analysis, verify local_face_review has exactly one entry per "Face crop N" / "Local face detector reference N" you were given, and that every entry's matched_subject_index (when >= 0) points at a subjects[] entry whose own local_detection_index equals that same detection_index. If a detection was skipped, or the cross-reference does not agree in both directions, fix it before finalizing.
- Subject inclusion recall check: before finalizing analysis, re-apply the Subject inclusion rule to every person subjects[] entry, not only the ones that came from a local detection. If a subject you added yourself from the full photo actually reads as an incidental bystander, remove that subjects[] entry (and any correction_plan/correction_axes/correction_inverse/retouch_recipe entries that named them) rather than leaving it in.
- is_person recall check: for every remaining person subjects[] entry, re-apply the is_person definition above. If it is currently true but this person's face is not actually legible enough to judge (back of head, silhouette, fully obscured), flip it to false and reset skin/blink_issue/hair_issue/gaze_issue/smile_issue to their is_person=false placeholders instead of leaving stale judged values in place.
- Do not score output quality here. Do not write QA penalties here. Leave candidate ranking to the crop judge and final scoring to QA.
- Make the composition-framing decision decisive and consistent. When composition_strategy, distraction_strategy, or detected_issues identifies a composition crop/reframe to execute, express it through crop_box and a crop-tagged correction_plan item. When crop_box.enabled is false, describe the original frame as the selected final composition with a definitive preserve decision. An alignment_strategy correction alone must not enable crop_box.
- Consolidate the entire composition-framing decision into that one crop-tagged item: its crop/trim/frame-relative repositioning or scale actions and any change to recommended_aspect_ratio must never become separate review items. The paired retouch_recipe starts with "重新構圖：" and contains no numeric ratio; local deterministic geometry adds the resolved ratio later. Its correction_inverse explicitly restores the original ratio and composition but says nothing about tilt or perspective.
- Keep alignment independently reviewable. If alignment_strategy calls for whole-frame rotation, horizon/vertical straightening, or keystone/perspective correction, create a separate alignment-tagged item whose retouch_recipe starts with "水平／透視校正：". Do not merge it into the crop item; any minimal wedge-removal trim is part of alignment and must preserve the selected ratio and composition as closely as possible.
- Before returning JSON, read edit_intent, lighting_strategy, memory_color_strategy, correction_plan, and preserve_rules together. Make them one consistent target and execution plan; remove or rewrite any statement that weakens or preserves away an approved light or color adjustment.

"""

_ANALYSIS_RESPONSIBILITIES_PORTRAIT_QC = """- Hair analysis is observation-only: classify hair_state, record evidence, and provide the routed candidate fix_action/correction_plan. Always leave hair_issue.should_fix=false for Python to derive; do not suppress a physical state because it may be intentional or because the selected hair level is unknown.
- Eye-contact detection is separate from blink repair. Analyze whether the readable eyes are locked to the exact current lens, not merely whether the face points forward. Do not derive should_fix and do not write a repair strategy in this pass.
- Gaze detection recall check: before finalizing, revisit every listed person's eyes. If any readable eyes are not unmistakably lens-locked and that subject's gaze_issue does not yet reflect it, fix it.
- Gaze consistency check: direct_camera must pair with looking_at_lens; every other readable gaze state, including near_camera, must pair with not_looking_at_lens; physically unreadable eyes must pair with unclear.
- Blink detection is observation-only in this pass. Report left/right physical eyelid states and the separate exception-evidence fields exactly as seen. Keep blink_candidate=false and should_fix=false placeholders; deterministic Python derives both after this response.
- Blink recall check: revisit every listed person's eyelids and record every readable half_closed, fully_closed, or one-eye-closed state even when the person is smiling, posing, making a gesture, or appears playful.
- Never use an ordinary or broad smile as laughter evidence. active_high_intensity_laughter, strong_bilateral periocular contraction, and integral_to_expression must each be independently visible before recording the full laughter exception evidence.
"""

_PORTRAIT_QC_PERSONA_HEADER = """You are a meticulous portrait quality-control reviewer. A separate photography-direction pass has already analyzed this photo's composition, color, and crop, and has identified the people listed below. Your only job is to inspect each of them, independently, for four physical/expression states: blink/eye closure, gaze direction, hair disruption, and smile intensity. You are not judging composition, color, or crop. For hair, author only the nested per-person candidate hair_issue.correction_plan required by the observed state; Python decides whether that candidate is enabled.

Return only valid JSON. Keep the JSON keys in English. Every user-visible string value (e.g. each issue's "reason") must be Traditional Chinese.

For every listed person, echo subject_index and local_detection_index exactly as provided. These identifiers are authoritative and are used to merge results; do not derive them from output order. Populate blink_issue, hair_issue, gaze_issue, and smile_issue for that same person. Do not output any top-level correction_plan, correction_axes, correction_inverse, or retouch_recipe.
"""


def _person_list_text(persons):
    entries = []
    for i, p in enumerate(persons):
        box = p.get("box") or {}
        subject_index = p.get("subject_index", p.get("_subject_index", i))
        local_detection_index = p.get("local_detection_index", -1)
        entries.append(
            "subject_index={} | local_detection_index={} | {} -- box x={:.3f}, y={:.3f}, w={:.3f}, h={:.3f}".format(
                subject_index, local_detection_index, p.get("label") or "person",
                box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0),
            )
        )
    return "\n".join(entries)


def portrait_qc_persons(analysis):
    """Return Call B's person list with stable indices from Call A.

    Callers should pass this result to build_portrait_qc_instruction instead
    of filtering subjects[] themselves, because a filtered-list position is
    not necessarily the subject's index in the original subjects[] array.
    """
    persons = []
    for subject_index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        person = dict(subject)
        person["subject_index"] = subject_index
        persons.append(person)
    return persons


# Any W:H reaching here has already passed param_validation.VALID_ASPECT_RATIOS;
# "auto" and "original" are the only two non-numeric preferences.
_FIXED_ASPECT_RATIO_PREF = re.compile(r"^\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?$")


def _aspect_ratio_policy_suffix(aspect_ratio_pref, source_size=None):
    """source_size: the photograph's own (width, height). Without it the
    expand clause is left out -- the choice it describes depends on how far
    this shape is from that one, so an unknown source keeps crop-only."""
    pref = str(aspect_ratio_pref or "auto").strip().lower()
    if pref == "original":
        return _ASPECT_RATIO_POLICY_ORIGINAL
    if not _FIXED_ASPECT_RATIO_PREF.match(pref):
        return _ASPECT_RATIO_POLICY_AUTO

    policy = _ASPECT_RATIO_POLICY_FIXED.format(ratio=pref)
    if not expand_permitted(pref, pref, source_size):
        return policy
    narrower = _canvas_shape_difference(pref, source_size) == "narrower"
    return policy + _ASPECT_RATIO_POLICY_EXPAND.format(
        ratio=pref,
        lost_pct=round((1 - _cross_direction_retained(pref, source_size)) * 100),
        lost_axis="width" if narrower else "height",
        grow_edges="top and bottom edges" if narrower else "left and right edges",
    )


def build_overall_analysis_instruction(aspect_ratio_pref="auto", advanced_options=None,
                                      source_size=None):
    """Call A of the gpt-5.6-luna two-pass split (see the module comment
    above _FACE_CROP_EVIDENCE_SECTION): everything ANALYSIS_INSTRUCTION does
    except judging blink/hair/gaze/smile, which the portrait-QC pass
    (build_portrait_qc_instruction) does afterward from this call's own
    finalized subjects[] list.
    """
    advanced_options = advanced_options or {}
    instruction = (
        _OVERALL_ANALYSIS_PERSONA
        + _RETURN_SHAPE_OVERALL
        + "\n\n"
        + _OUTPUT_ALIGNMENT_RULES_OVERALL
        + _FACE_CROP_EVIDENCE_SECTION
        + "\n"
        + _ANALYSIS_RESPONSIBILITIES_OVERALL
        + _COORDINATE_GEOMETRY_RULES_OVERALL
        + "\n\n"
        + _COMPOSITION_DECISION_FRAMEWORK
        + "\n"
    )
    if not body_reshape_enabled():
        instruction = _without_body_reshape_prompt_lines(instruction)
    return instruction + _aspect_ratio_policy_suffix(aspect_ratio_pref, source_size)


def build_portrait_qc_instruction(persons):
    """Call B of the gpt-5.6-luna two-pass split: given Call A's already-
    finalized, ordered list of is_person=true subjects (label + box), judge
    each one's blink/hair/gaze/smile independently. Build persons with
    portrait_qc_persons(analysis), so every item carries its immutable
    subject_index from Call A. Results are merged by subject_index, never by
    response-array position.
    """
    return (
        _PORTRAIT_QC_PERSONA_HEADER
        + "\nPeople to review, in order:\n"
        + _person_list_text(persons)
        + "\n\n"
        + _PORTRAIT_QC_FACE_CROP_EVIDENCE_SECTION
        + "\n"
        + _BLINK_DETECTION_LUNA_SECTION
        + _HAIR_ANALYSIS_SECTION
        + _HAIR_CORRECTION_HANDOFF_SECTION
        + "\n"
        + _GAZE_DETECTION_LUNA_SECTION
        + "\n"
        + _SMILE_ANALYSIS_SECTION
        + "\n## Portrait-QC responsibilities\n"
        + _ANALYSIS_RESPONSIBILITIES_PORTRAIT_QC
    )


def build_eye_repair_planning_instruction(targets):
    """Optional Luna Call C. Only people who passed the deterministic blink
    and/or gaze gate are included, so this prompt plans execution without
    re-deciding whether either fix is approved.
    """
    compact_targets = []
    for target in targets:
        needs_gaze = bool(target.get("needs_gaze_repair"))
        needs_blink = bool(target.get("needs_blink_repair"))
        gaze_issue = target.get("gaze_issue") or {}
        blink_issue = target.get("blink_issue") or {}
        entry = {
            "subject_index": target.get("subject_index"),
            "local_detection_index": target.get("local_detection_index", -1),
            "label": target.get("label") or "person",
            "box": target.get("box") or {},
            "needs_gaze_repair": needs_gaze,
            "needs_blink_repair": needs_blink,
        }
        if needs_gaze:
            entry["gaze"] = {
                "gaze_observability": gaze_issue.get("gaze_observability"),
                "eye_detail_quality": gaze_issue.get("eye_detail_quality"),
                "head_pose": gaze_issue.get("head_pose"),
                "left_eye_direction": gaze_issue.get("left_eye_direction"),
                "right_eye_direction": gaze_issue.get("right_eye_direction"),
                "binocular_alignment": gaze_issue.get("binocular_alignment"),
                "gaze_state": gaze_issue.get("gaze_state"),
                "reason": gaze_issue.get("reason"),
            }
        if needs_blink:
            entry["blink"] = {
                "blink_observability": blink_issue.get("blink_observability"),
                "eyelid_detail_quality": blink_issue.get("eyelid_detail_quality"),
                "photograph_left_eye_state": blink_issue.get("left_eye_state"),
                "photograph_right_eye_state": blink_issue.get("right_eye_state"),
                "physical_eye_state": blink_issue.get("physical_eye_state"),
                "eye_state": blink_issue.get("eye_state"),
                "reason": blink_issue.get("reason"),
            }
        compact_targets.append(entry)
    return """You are planning local eye repairs for only the people listed below.

Their blink and/or gaze detection is final and was already gated by deterministic Python. Do not reclassify it, remove a target, add another person, or change should_fix. Return exactly one repair plan per listed subject_index. Each plan must include both gaze_repair_strategy and blink_repair_strategy. For whichever of the two a target's needs_gaze_repair/needs_blink_repair is false, fill that one with its "not applicable" placeholder shape (strategy_type/target_eye_state "not_safe_to_repair"/"不適用", every other field "不適用" or an empty list) instead of omitting it.

Every free-text field you write (edit_instruction, target_eye_state, target_gaze_read, success_condition, preservation rules, failure modes, etc.) will be copied verbatim into a separate prompt for a different model later -- one that sees only the full photo plus this same target's label and box, and nothing else about this analysis. So each field must stand on its own: describe the person only by their label wording or frame position (e.g. "the person on the left", the label given below), and describe the required change only in terms visible in the final photo. Never reference "Face crop N", subject_index, local_detection_index, detection_index, or any other identifier, numbering, or artifact of this analysis session -- that other model has never seen any of them and will not know what they mean.

Gaze repair (only for a target with needs_gaze_repair true):
Use the full photo and matching Face crop N to choose an anatomically plausible method that produces an unmistakable, normally visible correction to the exact current lens. The correction must be obvious at normal photo-viewing size, not only when the face is highly enlarged:
- eyes_plus_head_face_alignment is used only when the captured head yaw/pitch or face orientation itself materially prevents natural direct lens contact. Require a visible but bounded head/face alignment together with the iris/pupil correction;
- eyes_only is the correct method whenever the head and face are already naturally aligned to the current lens and both visible eyes can reach exact lens contact without side-eye, strain, crossed axes, or excessive sclera. The miss need not be tiny: a frontal face looking sideways may still need a clearly visible iris/pupil movement while its eyelids, eye shape, head pose, and facial projection remain fixed;
- not_safe_to_repair only when the pixels or geometry cannot support a reliable correction. In that case write a conservative plan and explicitly name the limitation.
For each gaze strategy, provide concrete English instructions. Name the current pupil/iris direction, exact target, required pupil/iris movement, natural sclera/catchlight rebalancing, any genuinely required bounded head/face alignment, repair strength, success condition, preservation rules, and likely failure modes. Describe this as repositioning the person's existing irises and pupils inside unchanged eye openings—never use reconstruct, rebuild, replace, regenerate, or redraw for the eyes or face. Use moderate strength by default; use subtle only for a genuinely tiny eyes_only miss whose completed correction remains obvious at normal viewing size. Preserve identity, eyelid openness, eye shape/color, canthi, lash lines, expression, glasses, skin, head pose unless the selected method explicitly requires it, clothing, scene, and every unlisted person. Do not stop at near-camera gaze.
Do not write any instruction that preserves, freezes, or restores the named person's torso, shoulders, body pose, stance, gesture, interaction points, or camera framing. Those properties are outside this local gaze plan and may be owned by a later user-selected pose or framing override.

Blink repair (only for a target with needs_blink_repair true):
The input fields photograph_left_eye_state and photograph_right_eye_state are diagnostic planner inputs only; they always mean the photograph's left and right sides. Do not repeat these source-state labels in target_eye_state, edit_instruction, eyelid_adjustment, or success_condition. Write target-forward instructions so the editor's attention stays on the desired open pair rather than the captured defect.

Treat both eyes as one coupled bilateral structure. Establish one shared final eyelid geometry before describing either eye. Only an eye detected as open may supply final geometry. When exactly one eye is fully_closed and the opposite eye is open, name the photograph side of that open eye as the finished-shape reference. When the opposite eye is narrowed_open or half_closed, neither captured aperture is a reference: require both upper-lid margins to move to one naturally-open target instead of averaging their source openings.

Use these visible match criteria: aperture-height-to-eye-width ratio; upper/lower-lid contour; iris exposure; and double-eyelid crease type, path, peak, visible length, and crease-to-lash width. Preserve fixed canthi and eye-globe identity. For a frontal face, use the nose bridge/facial midline as the bilateral comparison axis; for a turned face, compare in each eye's local axes and allow only unavoidable foreshortening. Resolve closed-lid compression into a smooth crease without adding upper-lid mass or puffiness.

When the two diagnostic source states differ and neither eye is a valid open-eye reference, make eye-fissure geometry and double-eyelid-crease geometry two independent final matches. Do not compensate for a mismatch in one by changing the other, and do not preserve the blink-induced left/right difference as natural asymmetry. Check the two matches separately, then confirm they still hold together. This stricter rule is local to that mixed-state blink pair and must not symmetrize the rest of the face.

Keep each model-facing field concise: target_eye_state and success_condition are one final-state sentence each; edit_instruction and eyelid_adjustment are at most two sentences each. Do not narrate the repair history, repeat requirements between fields, or ask the editor to reason through a checklist. Use strong repair strength when either source eye is fully closed or both eyes require opening. Preserve identity, expression, skin texture, glasses, makeup, lighting, every unlisted person, and intentional closed-eye expressions. When gaze repair is also required, describe one coordinated local eye edit.

Targets:
""" + json.dumps(compact_targets, ensure_ascii=False, indent=2)




def build_analysis_instruction(aspect_ratio_pref="auto", advanced_options=None, analysis_model=None,
                              source_size=None):
    """Build analysis instructions with one authoritative aspect-ratio policy.

    advanced_options: the Upload page's Advanced Settings. Detection/repair
    toggles do not change analysis; each person's portrait findings are always
    recorded, with hair execution kept in hair_issue.correction_plan. The
    edit stage applies the toggles. calm_hair and smile settings are also
    edit-only transformations.

    analysis_model: which analysis model this prompt is being built for.
    ANALYSIS_INSTRUCTION already carries the gpt-5.6-luna blink wording
    (_BLINK_ANALYSIS_LUNA / _BLINK_WINK_DEFAULT_BULLET_LUNA); every other
    model (including analysis_model=None, for callers that don't pass one)
    gets it swapped back to the original wording those models were actually
    calibrated against.
    """
    advanced_options = advanced_options or {}
    instruction = ANALYSIS_INSTRUCTION
    if analysis_model != "gpt-5.6-luna":
        instruction = instruction.replace(_BLINK_ANALYSIS_LUNA, _BLINK_ANALYSIS_LEGACY)
        instruction = instruction.replace(_BLINK_WINK_DEFAULT_BULLET_LUNA, _BLINK_WINK_DEFAULT_BULLET_LEGACY)
    if not body_reshape_enabled():
        instruction = _without_body_reshape_prompt_lines(instruction)
    return instruction + _aspect_ratio_policy_suffix(aspect_ratio_pref, source_size)


# ── Structured output schema for Step 2 (photo analysis) ──────────────────────
#
# Mirrors the "Return exactly this JSON shape" block in ANALYSIS_INSTRUCTION
# above field-for-field. Passed as the Responses API's text.format
# (type=json_schema, strict=true) so the model's output is constrained at
# decode time to valid JSON matching this shape -- added after a production
# gpt-5-mini call returned syntactically-broken JSON (an object closed with
# `]` instead of `}` partway through a long, deeply-nested response), which
# prompt wording alone can ask for but not guarantee. Deliberately does NOT
# constrain the enum-like fields (eye_state, hair_state, gaze_state, ...) to
# their documented value sets: those are guidance in the prompt, not
# contract, and a value this schema didn't anticipate should surface as a
# judgement question, not a hard 400 from the API on every future call.
# Values our own code actually branches on (should_fix) are still literal
# structural fields here regardless.
#
# additionalProperties: false and every property listed in required are both
# mandatory for OpenAI's strict structured-output mode, not just style --
# the API rejects a schema that omits either.


def _schema_string():
    return {"type": "string"}


def _schema_enum(values):
    return {"type": "string", "enum": list(values)}


def _schema_number():
    return {"type": "number"}


def _schema_integer():
    return {"type": "integer"}


def _schema_boolean():
    return {"type": "boolean"}


def _schema_string_array():
    return {"type": "array", "items": _schema_string()}


def _schema_object(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _schema_array_of(item_schema):
    return {"type": "array", "items": item_schema}


# {"x","y","w","h"} normalized box, reused verbatim by subjects and
# gaze_target_in_frame.
_BOX_SCHEMA = _schema_object({
    "x": _schema_number(), "y": _schema_number(),
    "w": _schema_number(), "h": _schema_number(),
})

# Same four fields plus "reason" -- roi_box/subject_extent_box's shape.
_BOX_WITH_REASON_SCHEMA = _schema_object({
    "x": _schema_number(), "y": _schema_number(),
    "w": _schema_number(), "h": _schema_number(),
    "reason": _schema_string(),
})

# blink_issue/hair_issue/gaze_issue/smile_issue below deliberately carry no
# subject_label or box of their own -- they are nested inside one
# _SUBJECT_SCHEMA entry, so they always inherit that same subject's label and
# box by construction. There is no cross-array label-matching step left for
# the model to get wrong.

_EYE_BLINK_ISSUE_SCHEMA = _schema_object({
    "physical_eye_state": _schema_string(),
    "eye_state": _schema_string(),
    "severity": _schema_string(),
    "intent_cues": _schema_string_array(),
    "closure_intent": _schema_string(),
    "expression_coherence": _schema_string(),
    "blink_failure_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
    "reason": _schema_string(),
})

# Luna-only: a Call-C-authored (or deterministic-fallback) repair strategy for
# an approved blink fix, the same role _GAZE_REPAIR_STRATEGY_SCHEMA plays for
# gaze. Added only to _BLINK_DETECTION_LUNA_SCHEMA below, never to
# _EYE_BLINK_ISSUE_SCHEMA above -- non-luna models must see zero shape change.
_BLINK_REPAIR_STRATEGY_SCHEMA = _schema_object({
    "target_eye_state": _schema_string(),
    "eyelid_adjustment": _schema_string(),
    "repair_strength": _schema_string(),
    "edit_instruction": _schema_string(),
    "success_condition": _schema_string(),
    "preserve_during_repair": _schema_string_array(),
    "failure_to_avoid": _schema_string_array(),
})

# Luna Call B reports recall-first physical evidence only.  The legacy fields
# remain in the shape for downstream compatibility, but their final values are
# overwritten by derive_luna_blink_decisions; the model is not authoritative
# for blink_candidate or should_fix.
_BLINK_DETECTION_LUNA_SCHEMA = _schema_object({
    "blink_observability": _schema_enum(("both_eyes", "one_eye", "unreadable")),
    "eyelid_detail_quality": _schema_enum(("readable", "borderline", "unreadable")),
    "left_eye_state": _schema_enum((
        "open", "narrowed_open", "half_closed", "fully_closed", "not_visible", "unclear",
    )),
    "right_eye_state": _schema_enum((
        "open", "narrowed_open", "half_closed", "fully_closed", "not_visible", "unclear",
    )),
    "physical_eye_state": _schema_enum((
        "open", "narrowed_open", "half_closed", "fully_closed", "one_eye_closed", "unclear",
    )),
    "eye_closure_confidence": _schema_number(),
    "mouth_laughter_state": _schema_enum((
        "not_laughing", "smile_or_broad_smile", "active_high_intensity_laughter", "unclear",
    )),
    "periocular_contraction": _schema_enum((
        "none_or_mild", "strong_bilateral", "unclear",
    )),
    "closure_expression_dependence": _schema_enum((
        "integral_to_expression", "independent_or_transitional", "unclear",
    )),
    "explicit_closure_context": _schema_enum((
        "none", "deliberate_wink", "sleep", "prayer_or_meditation", "savoring",
        "crying_or_pain_or_strong_emotion", "downward_at_visible_target",
        "other_clear_activity", "unclear",
    )),
    "intent_evidence_confidence": _schema_number(),
    "overall_demeanor": _schema_enum((
        "alert_or_camera_ready", "calm_or_at_ease", "unclear",
    )),
    "intent_cues": _schema_string_array(),
    "blink_candidate": _schema_boolean(),
    "eye_state": _schema_string(),
    "severity": _schema_string(),
    "closure_intent": _schema_string(),
    "expression_coherence": _schema_string(),
    "blink_failure_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
    "reason": _schema_string(),
    "blink_repair_strategy": _BLINK_REPAIR_STRATEGY_SCHEMA,
})

_HAIR_ISSUE_SCHEMA = _schema_object({
    "hair_state": _schema_enum((
        "tidy", "styled_messy", "styled_bangs", "styled_face_framing",
        "motion", "natural_texture", "disheveled", "windblown", "strands_across_face",
        "bangs_disturbed", "bangs_clumped", "lock_out_of_place",
        "frizz_halo", "tuft_or_flattened", "occluded_or_unclear",
        "uncertain", "不適用",
    )),
    "visible_evidence": _schema_string_array(),
    "front_hair": _schema_enum(("none", "even", "clumped", "unclear", "不適用")),
    "fix_action": _schema_enum((
        "none", "lay_back", "remove", "settle", "redistribute", "不適用",
    )),
    "mess_intent": _schema_enum((
        "intentional_style", "accidental_capture", "ambiguous", "不適用",
    )),
    "hair_mess_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
    "correction_plan": _schema_string(),
    "reason": _schema_string(),
})

_GAZE_TARGET_IN_FRAME_SCHEMA = _schema_object({
    "present": _schema_boolean(),
    "description": _schema_string(),
    "box": _BOX_SCHEMA,
})

_GAZE_REPAIR_STRATEGY_SCHEMA = _schema_object({
    "strategy_type": _schema_string(),
    "original_gaze_read": _schema_string(),
    "target_gaze_read": _schema_string(),
    "eye_adjustment": _schema_string(),
    "sclera_catchlight_adjustment": _schema_string(),
    "head_face_adjustment": _schema_string(),
    "repair_strength": _schema_string(),
    "edit_instruction": _schema_string(),
    "success_condition": _schema_string(),
    "preserve_during_repair": _schema_string_array(),
    "failure_to_avoid": _schema_string_array(),
})

_GAZE_CONTACT_ISSUE_SCHEMA = _schema_object({
    "gaze_state": _schema_string(),
    "eye_focus_quality": _schema_string(),
    "gaze_target_in_frame": _GAZE_TARGET_IN_FRAME_SCHEMA,
    "lens_contact_decision": _schema_string(),
    "lens_contact_confidence": _schema_number(),
    "not_looking_at_lens_confidence": _schema_number(),
    "gaze_correction_reasonableness": _schema_number(),
    "gaze_repair_strategy": _GAZE_REPAIR_STRATEGY_SCHEMA,
    "should_fix": _schema_boolean(),
    "reason": _schema_string(),
})

# Luna Call B deliberately stops at physical observation and exact-lens
# classification.  should_fix and the full repair strategy are added by
# deterministic Python and an optional repair-planning Call C respectively.
_GAZE_DETECTION_LUNA_SCHEMA = _schema_object({
    "gaze_observability": _schema_enum(("both_eyes", "one_eye", "unreadable")),
    "eye_detail_quality": _schema_enum(("readable", "borderline", "unreadable")),
    "head_pose": _schema_enum(("frontal", "slight_yaw", "strong_yaw", "profile", "rear_or_hidden", "unclear")),
    "left_eye_direction": _schema_enum((
        "toward_exact_lens", "near_lens_left", "near_lens_right", "above_lens", "below_lens",
        "off_left", "off_right", "up", "down", "unclear", "not_visible",
    )),
    "right_eye_direction": _schema_enum((
        "toward_exact_lens", "near_lens_left", "near_lens_right", "above_lens", "below_lens",
        "off_left", "off_right", "up", "down", "unclear", "not_visible",
    )),
    "binocular_alignment": _schema_enum((
        "exact_lens_convergent", "near_lens_convergent", "off_lens_convergent",
        "mismatched", "unverifiable",
    )),
    "gaze_state": _schema_enum((
        "direct_camera", "near_camera", "off_camera", "downward", "upward_side",
        "unfocused_camera", "cross_focused", "ambiguous", "unclear",
    )),
    "gaze_target_in_frame": _GAZE_TARGET_IN_FRAME_SCHEMA,
    "lens_contact_decision": _schema_enum(("looking_at_lens", "not_looking_at_lens", "unclear")),
    "lens_contact_confidence": _schema_number(),
    "not_looking_at_lens_confidence": _schema_number(),
    "reason": _schema_string(),
})

_SMILE_STATE_SCHEMA = _schema_object({
    # Keep this constrained at the API boundary as well as in the prose
    # instruction.  A plain string allowed structured-output models to
    # translate values (for example gentle -> "溫和微笑"), which made
    # the downstream resolver discard an otherwise readable face.
    "smile_intensity": _schema_enum(("neutral", "gentle", "broad", "laughing", "unclear")),
    "adjust_safe": _schema_boolean(),
    "reason": _schema_string(),
})

_SUBJECT_SCHEMA = _schema_object({
    "label": _schema_string(),
    "box": _BOX_SCHEMA,
    "prominence": _schema_integer(),
    "is_person": _schema_boolean(),
    # -1 when this subject was added from the model's own reading of the full
    # photo rather than from a "Local face detector reference" detection, or
    # when is_person=false (the face detector never applies to those) -- see
    # the "Subject inclusion rule" and "Zoomed face-crop images" sections of
    # ANALYSIS_INSTRUCTION. Cross-references local_face_review[].
    # matched_subject_index in the other direction.
    "local_detection_index": _schema_integer(),
    "skin": _schema_string(),
    "reason": _schema_string(),
    "blink_issue": _EYE_BLINK_ISSUE_SCHEMA,
    "hair_issue": _HAIR_ISSUE_SCHEMA,
    "gaze_issue": _GAZE_CONTACT_ISSUE_SCHEMA,
    "smile_issue": _SMILE_STATE_SCHEMA,
})

# One entry per locally-detected face the model was handed (as a "Face crop
# N" image and/or a "Local face detector reference N" coordinate) -- lets a
# caller see, for every detection the local face detector produced, whether
# the analysis model treated it as a real, includable person and which
# subjects[] entry (if any) it became; or, when it didn't become one, why
# (detector false positive vs. a real but excluded incidental bystander).
_LOCAL_FACE_REVIEW_SCHEMA = _schema_object({
    "detection_index": _schema_integer(),
    "is_real_face": _schema_boolean(),
    "matched_subject_index": _schema_integer(),
    "reason": _schema_string(),
})

# Flat x/y/w/h (no nested "box"), unlike the five schemas above.
_PROTECTED_REGION_SCHEMA = _schema_object({
    "label": _schema_string(),
    "x": _schema_number(), "y": _schema_number(),
    "w": _schema_number(), "h": _schema_number(),
    "reason": _schema_string(),
})

# Top-level crop_box has "reason"; the copy nested in each crop_candidates[]
# entry does not -- two distinct shapes, not one reused across both.
_CROP_BOX_SCHEMA = _schema_object({
    "enabled": _schema_boolean(),
    "x": _schema_number(), "y": _schema_number(),
    "w": _schema_number(), "h": _schema_number(),
    "aspect_ratio": _schema_string(),
    "reason": _schema_string(),
})

_CROP_CANDIDATE_CROP_BOX_SCHEMA = _schema_object({
    "enabled": _schema_boolean(),
    "x": _schema_number(), "y": _schema_number(),
    "w": _schema_number(), "h": _schema_number(),
    "aspect_ratio": _schema_string(),
})

_CROP_CANDIDATE_SCHEMA = _schema_object({
    "id": _schema_string(),
    "label": _schema_string(),
    "crop_box": _CROP_CANDIDATE_CROP_BOX_SCHEMA,
    "composition_intent": _schema_string(),
    "tradeoffs": _schema_string(),
})

_CROP_INTENT_SCHEMA = _schema_object({
    "trim_left": _schema_boolean(),
    "trim_right": _schema_boolean(),
    "trim_top": _schema_boolean(),
    "trim_bottom": _schema_boolean(),
    "subject_anchor": _schema_string(),
    "looking_direction": _schema_string(),
    "remove_edge_distractions": _schema_string_array(),
})

_CROP_RISK_SCHEMA = _schema_object({
    "needs_validation": _schema_boolean(),
    "risk_level": _schema_string(),
    "reasons": _schema_string_array(),
})

ANALYSIS_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "name": "photo_analysis",
    "strict": True,
    "schema": _schema_object({
        "photo_summary": _schema_string(),
        "subject_category": _schema_string(),
        "scene_category": _schema_string(),
        "main_subject": _schema_string(),
        "scene_type": _schema_string(),
        "aesthetic_diagnosis": _schema_string(),
        "detected_issues": _schema_string_array(),
        "edit_philosophy": _schema_string(),
        "edit_intent": _schema_string(),
        "composition_mood": _schema_string(),
        "composition_intent": _schema_string(),
        "framing_intent": _schema_string(),
        "layout_method": _schema_string(),
        "portrait_framing": _schema_string(),
        "composition_strategy": _schema_string(),
        "visual_balance_strategy": _schema_string(),
        "empty_space_strategy": _schema_string(),
        "alignment_strategy": _schema_string(),
        "distraction_strategy": _schema_string(),
        "scene_preservation_strategy": _schema_string(),
        "roi_anchor": _schema_string(),
        "recommended_aspect_ratio": _schema_string(),
        "aspect_ratio_reason": _schema_string(),
        "subjects": _schema_array_of(_SUBJECT_SCHEMA),
        "local_face_review": _schema_array_of(_LOCAL_FACE_REVIEW_SCHEMA),
        "roi_box": _BOX_WITH_REASON_SCHEMA,
        "subject_extent_box": _BOX_WITH_REASON_SCHEMA,
        "protected_regions": _schema_array_of(_PROTECTED_REGION_SCHEMA),
        "lighting_strategy": _schema_string(),
        "memory_color_strategy": _schema_string(),
        "evaluation_criteria": _schema_string_array(),
        "crop_recommendation": _schema_string(),
        "reframe_mode": _schema_enum(("crop", "expand")),
        "reframe_mode_reason": _schema_string(),
        "crop_box": _CROP_BOX_SCHEMA,
        "crop_candidates": _schema_array_of(_CROP_CANDIDATE_SCHEMA),
        "crop_intent": _CROP_INTENT_SCHEMA,
        "crop_risk": _CROP_RISK_SCHEMA,
        "correction_plan": _schema_string_array(),
        "correction_axes": _schema_array_of(_schema_string_array()),
        "correction_inverse": _schema_string_array(),
        "retouch_recipe": _schema_string_array(),
        "preserve_rules": _schema_string_array(),
    }),
}

# Call A's schema for the gpt-5.6-luna two-pass split (see
# build_overall_analysis_instruction) -- identical to ANALYSIS_RESPONSE_SCHEMA
# except subjects[] carries none of the four per-person issue fields, since
# Call A never judges them. Derived from ANALYSIS_RESPONSE_SCHEMA itself
# (deepcopy + swap the subjects item schema) so any future top-level field
# added there is automatically reflected here too.
_SUBJECT_SCHEMA_OVERALL = _schema_object({
    "label": _schema_string(),
    "box": _BOX_SCHEMA,
    "prominence": _schema_integer(),
    "is_person": _schema_boolean(),
    "local_detection_index": _schema_integer(),
    "skin": _schema_string(),
    "reason": _schema_string(),
})

ANALYSIS_RESPONSE_SCHEMA_OVERALL = copy.deepcopy(ANALYSIS_RESPONSE_SCHEMA)
ANALYSIS_RESPONSE_SCHEMA_OVERALL["name"] = "photo_analysis_overall"
ANALYSIS_RESPONSE_SCHEMA_OVERALL["schema"]["properties"]["subjects"] = _schema_array_of(_SUBJECT_SCHEMA_OVERALL)

# Call B's per-person output shape (build_portrait_qc_instruction) -- one
# entry per person handed to Call B. subject_index is the stable merge key;
# local_detection_index validates the Face crop N association.
_PERSON_ISSUES_SCHEMA = _schema_object({
    "subject_index": _schema_integer(),
    "local_detection_index": _schema_integer(),
    "blink_issue": _BLINK_DETECTION_LUNA_SCHEMA,
    "hair_issue": _HAIR_ISSUE_SCHEMA,
    "gaze_issue": _GAZE_DETECTION_LUNA_SCHEMA,
    "smile_issue": _SMILE_STATE_SCHEMA,
})


def build_portrait_qc_response_schema(person_count):
    """person_count pins person_issues[] to exactly that many entries via
    minItems/maxItems, so a length mismatch against the `persons` list
    build_portrait_qc_instruction was given is an API-level schema violation.
    Identity and merge correctness are validated separately by subject_index
    and local_detection_index; array order is not used as identity."""
    return {
        "type": "json_schema",
        "name": "portrait_qc",
        "strict": True,
        "schema": _schema_object({
            "person_issues": {
                "type": "array",
                "items": _PERSON_ISSUES_SCHEMA,
                "minItems": person_count,
                "maxItems": person_count,
            },
        }),
    }


_EYE_REPAIR_PLAN_ITEM_SCHEMA = _schema_object({
    "subject_index": _schema_integer(),
    "local_detection_index": _schema_integer(),
    "gaze_repair_strategy": _GAZE_REPAIR_STRATEGY_SCHEMA,
    "blink_repair_strategy": _BLINK_REPAIR_STRATEGY_SCHEMA,
})


def build_eye_repair_planning_response_schema(person_count):
    return {
        "type": "json_schema",
        "name": "eye_repair_planning",
        "strict": True,
        "schema": _schema_object({
            "repair_plans": {
                "type": "array",
                "items": _EYE_REPAIR_PLAN_ITEM_SCHEMA,
                "minItems": person_count,
                "maxItems": person_count,
            },
        }),
    }



def _default_person_issues():
    """The "不適用" shape ANALYSIS_INSTRUCTION already documents for
    is_person=false subjects (see the Coordinate and geometry rules bullet on
    blink_issue/hair_issue/gaze_issue/smile_issue) -- reused as the two-pass
    split's pre-merge baseline in openai_client._analyze_photo_two_pass, so
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


_HAIR_ACTION_BY_STATE = {
    "disheveled": "settle",
    "windblown": "settle",
    "strands_across_face": "lay_back",
    "bangs_disturbed": "lay_back",
    "bangs_clumped": "redistribute",
    "lock_out_of_place": "lay_back",
    "frizz_halo": "settle",
    "tuft_or_flattened": "settle",
}

_HAIR_FALLBACK_PLAN_BY_STATE = {
    "disheveled": "整理分析指出的多處失序髮流，以同一人仍然連貫的分線、長度、髮量、蓬度與髮流作為終點參照，不重新設計髮型。",
    "windblown": "整理可見的受風紊亂區域，以同一人未受干擾的分線、長度、髮量與髮流作為終點參照。",
    "strands_across_face": "只將分析指出、偏離原髮流並落在臉上的髮絲順回相鄰的既有髮流，保留原本髮型與可見五官。",
    "bangs_disturbed": "只將被翻起或推離原方向的既有瀏海順回其原本分線與髮流，不增減瀏海。",
    "bangs_clumped": "降低既有瀏海局部油亮並恢復髮絲細節，再將同一批結成束髮絲梳散成通透、較均衡的既有瀏海，不改變長度、髮量、分線或髮際線。",
    "lock_out_of_place": "只將分析指出、脫離固定處或周圍髮流的髮束順回相鄰的既有結構。",
    "frizz_halo": "只收整分析指出、與其餘穩定輪廓不一致的局部毛躁區，依同一人相鄰髮絲的方向與質感完成。",
    "tuft_or_flattened": "只修正分析指出的局部翹起或壓扁區，使其接回相鄰既有髮流、蓬度與質感。",
}


def _forced_visible_hair_plan(target):
    """Raise an explicit manual hair request above a subtle/no-op cleanup."""
    return (
        "User-forced hair-only edit: " + target + " Use only existing strands: do not add hair, increase hair mass, "
        "or create a new lock. Preserve strand roots and endpoints. Keep the head, face, eyelids, expression, and skin "
        "unchanged. Make the target visibly tidier."
    )


def resolve_user_hair_request(issue):
    """Return the executable hair issue after a per-person manual opt-in.

    Analysis truth stays untouched: ``should_fix`` still records the automatic
    verdict.  This thin adapter only supplies a conservative, renderable target
    when the user explicitly enables a should_fix=false row.  It deliberately
    reuses the existing action vocabulary and state-specific behavior.
    """
    resolved = dict(issue or {})
    if not resolved.get("_user_forced") or resolved.get("should_fix"):
        return resolved

    state = resolved.get("hair_state") or "uncertain"
    front = resolved.get("front_hair") or "unclear"
    level = resolved.get("_user_level") or "dynamic"

    if level == "tidy":
        resolved.update({
            "fix_action": "settle",
            "correction_plan": (
                "User-forced hair-only edit: comb the existing hairstyle into visibly neater versions of its current "
                "sections, settling loose, lifted, crossed, or uneven strands. Rearrange source-visible hair only; do not "
                "add hair, create a new lock, or change strand roots, endpoints, head pose, face, eyelids, expression, or skin."
            ),
        })
        return resolved

    if state == "bangs_clumped" or front == "clumped":
        resolved.update({
            "hair_state": "bangs_clumped",
            "front_hair": "clumped",
            "fix_action": "redistribute",
            "correction_plan": _forced_visible_hair_plan(
                "Within the existing fringe only, split thick stuck clumps and redistribute those same strands into finer, "
                "more even sections across abnormal gaps; reduce greasy shine without changing fringe length or density."
            ),
        })
    elif state in {"windblown", "motion"}:
        resolved.update({
            "fix_action": "lay_back",
            "correction_plan": _forced_visible_hair_plan(
                "Align existing stray, crossing, tangled, and directionless strands with the visible wind or body-motion "
                "flow while preserving its lifted volume and motion."
            ),
        })
    elif state in _HAIR_ACTION_BY_STATE:
        action = resolved.get("fix_action")
        if action not in {"lay_back", "remove", "settle", "redistribute"}:
            action = _HAIR_ACTION_BY_STATE[state]
        plan = str(resolved.get("correction_plan") or "").strip()
        resolved.update({
            "fix_action": action,
            "correction_plan": _forced_visible_hair_plan(
                plan if plan and plan != "不適用" else _HAIR_FALLBACK_PLAN_BY_STATE[state]
            ),
        })
    elif state in {"styled_bangs"} or front == "even":
        resolved.update({
            "fix_action": "lay_back",
            "correction_plan": _forced_visible_hair_plan(
                "Edit only the existing front fringe above the eyes: separate thick or crossing sections and redistribute "
                "those same strands into finer, more even spacing. Preserve its airy type, length, density, roots, and lower "
                "endpoints; leave the crown, sides, and rear hair unchanged."
            ),
        })
    elif state in {"styled_messy", "styled_face_framing", "natural_texture"}:
        resolved.update({
            "fix_action": "lay_back",
            "correction_plan": _forced_visible_hair_plan(
                "Edit only the source-visible front and face-framing strands: remove crossings and align each strand with "
                "its nearest existing flow while preserving the exact number, roots, endpoints, length, and curve of those "
                "locks. Leave crown, side lengths, and rear hair unchanged."
            ),
        })
    else:
        resolved.update({
            "fix_action": "lay_back",
            "correction_plan": _forced_visible_hair_plan(
                "Comb all visible loose, crossed, uneven, and directionless strands into coherent sections following the "
                "existing part and hair flow, and clean up the outer silhouette."
            ),
        })
    return resolved


# Unlike hair's per-hair_state branching, blink/gaze force requests are a
# single fixed instruction regardless of the analyzer's own eye_state/
# gaze_state reading -- there is no "how messy" spectrum for an eyelid or a
# gaze direction to modulate against, just open-vs-closed and on-vs-off
# camera, so one canned repair target covers every forced row.
_FORCED_BLINK_STRATEGY = {
    "edit_instruction": (
        "User-forced eye-open edit: open both eyes clearly and comfortably from their captured states "
        "into one natural open resting state with the same eye-opening height, iris exposure, and visible "
        "double-eyelid crease-to-lash band width."
    ),
    "target_eye_state": (
        "both eyes clearly open with the same eye-opening height, iris exposure, and visible "
        "double-eyelid crease-to-lash band width"
    ),
    "eyelid_adjustment": (
        "balance eyelid openness and visible crease-to-lash band width as separate requirements; move the "
        "upper-lid margin over the existing globe without pushing or bunching the whole upper-lid tissue toward the brow"
    ),
    "repair_strength": "moderate",
    "success_condition": "both eyes read clearly open and equally awake, with neither eye visibly smaller",
    "preserve_during_repair": ["identity", "iris color", "expression", "skin", "head pose"],
    "failure_to_avoid": ["oversized or doll-like eyes", "mismatched gaze direction", "altered identity"],
}

_FORCED_GAZE_STRATEGY = {
    "edit_instruction": (
        "User-forced gaze-redirect edit: turn and/or tilt this person's head/face naturally "
        "toward the exact current lens and reposition their pupils and irises so both eyes "
        "clearly and directly look into the camera lens, with natural sclera balance and "
        "catchlights consistent with the scene lighting. Go all the way to direct lens contact, "
        "not partway there -- a subtle, ambiguous shift that still reads as off-camera fails "
        "this edit."
    ),
    # eyes_only reads as "keep the head/face angle unchanged" downstream (see
    # build_gaze_only_edit_prompt's execution-by-method rule), which is only
    # valid for an already near-frontal head with a tiny miss -- too narrow a
    # licence for a user-forced request where the actual starting gaze/head
    # angle is unknown and may be far off-camera. eyes_plus_head_face_alignment
    # explicitly permits the head/face turn needed to actually reach direct
    # lens contact instead of leaving the edit model too little room to move.
    "strategy_type": "eyes_plus_head_face_alignment",
    "repair_strength": "strong",
    "eye_adjustment": "reposition pupils/irises fully toward the camera lens, going all the way to direct contact",
    "head_face_adjustment": "visibly but naturally turn and/or tilt the head/face toward the exact current lens while preserving facial proportions",
    "sclera_catchlight_adjustment": "rebalance naturally around the new eye position",
    "target_gaze_read": "both eyes clearly and directly looking at the camera lens",
    "preserve_during_repair": ["identity", "expression", "skin"],
    "failure_to_avoid": ["a partial or timid correction that still reads as off-camera", "mismatched pupil size", "altered identity"],
}


def resolve_user_blink_request(issue):
    """Return the executable blink issue after a per-person manual opt-in.

    Mirrors resolve_user_hair_request: should_fix keeps recording the
    automatic verdict, and this only supplies a repair target for a
    manually-enabled should_fix=false row, via the same blink_repair_strategy
    key the rich "Accidental blink repair priority" block (build_edit_prompt)
    already reads for a Luna-derived repair. The fixed strategy always wins
    over whatever partial dict the analyzer wrote for a should_fix=false row,
    since the point is a deterministic, user-authored instruction rather than
    a refinement of the analyzer's own (here, negative) judgement.
    """
    resolved = dict(issue or {})
    if not resolved.get("_user_forced") or resolved.get("should_fix"):
        return resolved
    resolved["blink_repair_strategy"] = dict(_FORCED_BLINK_STRATEGY)
    return resolved


def resolve_user_gaze_request(issue):
    """Return the executable gaze issue after a per-person manual opt-in.

    Mirrors resolve_user_blink_request: a fixed "look at the camera" repair
    target via gaze_repair_strategy, applied only when the user manually
    enables a should_fix=false row. Used both by build_edit_prompt's own
    gaze_block and by pipeline._run_gaze_repair_pass's independent second
    pass, which re-derives its own item list from analysis rather than
    reusing build_edit_prompt's -- both call sites need this applied, or a
    forced gaze fix silently never executes under the two-stage
    (single_pass_edit=False) flow.
    """
    resolved = dict(issue or {})
    if not resolved.get("_user_forced") or resolved.get("should_fix"):
        return resolved
    resolved["gaze_repair_strategy"] = dict(_FORCED_GAZE_STRATEGY)
    return resolved


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


def derive_luna_blink_decisions(analysis):
    """Turn Luna Call B's recall-first eyelid observations into the legacy
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

    Ports the wording build_edit_prompt's blink_block used to hardcode
    (reopen/relax naturally, avoid doll-like/oversized/symmetrical eyes,
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


def derive_luna_gaze_decisions(analysis):
    """Convert Luna Call B's observation-only gaze result into the legacy
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

        # Cross-check against derive_luna_blink_decisions, which runs first
        # (see openai_client.py's two-pass call order) and already wrote the
        # deterministically-derived eyelid state onto this same subject's
        # blink_issue. The hard gaze rule (see ANALYSIS_INSTRUCTION) requires
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
    the shared gate for Luna Call C, which now plans both topics per person
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


# ---------------------------------------------------------------------------
# Per-person Body Reshape hand-off
# ---------------------------------------------------------------------------


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


def _normalize_body_subject_plan(subject, subject_index, body):
    if not isinstance(body, dict):
        return None
    should_edit = body.get("should_edit") is not False
    label = subject.get("label") if isinstance(subject, dict) else None
    normalized = {"should_edit": should_edit}
    if body.get("analysis_status") == "error":
        normalized["analysis_status"] = "error"
    if should_edit:
        prompt = body.get("edit_prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            points = body.get("key_points")
            if isinstance(points, list):
                prompt = "；".join(str(point).strip() for point in points if str(point).strip())
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        normalized["edit_prompt"] = normalize_body_reshape_edit_prompt(
            prompt, fallback=_body_reshape_fallback_prompt(label, True)
        )
        display = body.get("prompt_display")
        normalized["prompt_display"] = normalize_body_reshape_prompt_display(
            display,
            fallback=body_reshape_prompt_display_fallback(prompt, label or ""),
        )
    report = body.get("report")
    if isinstance(report, str) and report.strip():
        normalized["report"] = normalize_body_reshape_report(
            report, subject_label=label or "", should_edit=should_edit
        )
    normalized["subject_index"] = subject_index
    normalized["local_detection_index"] = body.get(
        "local_detection_index", subject.get("local_detection_index", -1)
    )
    return normalized


def sanitize_body_reshape_analysis(analysis):
    """Keep per-person plans and review opt-outs across the edit boundary."""
    if not isinstance(analysis, dict):
        return analysis
    if not body_reshape_enabled():
        return strip_body_reshape(analysis)
    cleaned = dict(analysis)
    # A legacy top-level prompt remains readable for history; fresh Analyze
    # responses put their prompts on each subject instead.
    body = cleaned.get("body_reshape")
    if isinstance(body, dict) and isinstance(body.get("edit_prompt"), str):
        cleaned["body_reshape"] = {"edit_prompt": normalize_body_reshape_edit_prompt(body["edit_prompt"])}
    elif not isinstance(body, dict):
        cleaned.pop("body_reshape", None)

    subjects = cleaned.get("subjects")
    if isinstance(subjects, list):
        sanitized_subjects = []
        for index, subject in enumerate(subjects):
            if not isinstance(subject, dict):
                sanitized_subjects.append(subject)
                continue
            copy_subject = dict(subject)
            normalized = _normalize_body_subject_plan(subject, index, subject.get("body_reshape"))
            if normalized and subject.get("is_person") is True:
                copy_subject["body_reshape"] = normalized
            else:
                copy_subject.pop("body_reshape", None)
            sanitized_subjects.append(copy_subject)
        cleaned["subjects"] = sanitized_subjects

    if isinstance(cleaned.get("_body_reshape_enabled"), bool):
        cleaned["_body_reshape_enabled"] = bool(cleaned["_body_reshape_enabled"])
    else:
        cleaned.pop("_body_reshape_enabled", None)
    disabled = cleaned.get("_disabled_body_reshape_subjects")
    if isinstance(disabled, list):
        cleaned["_disabled_body_reshape_subjects"] = sorted({
            value for value in disabled
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        })
    else:
        cleaned.pop("_disabled_body_reshape_subjects", None)
    for key in _BODY_RESHAPE_LEGACY_KEYS:
        cleaned.pop(key, None)
    return cleaned


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


def strip_body_reshape(analysis):
    """Remove both new per-person and legacy Body data."""
    if not isinstance(analysis, dict):
        return analysis
    cleaned = dict(analysis)
    cleaned.pop("body_reshape", None)
    cleaned.pop("_body_reshape_status", None)
    cleaned.pop("_body_reshape_error", None)
    cleaned.pop("_body_reshape_partial_failures", None)
    cleaned.pop("_body_reshape_enabled", None)
    cleaned.pop("_disabled_body_reshape_subjects", None)
    raw_subjects = analysis.get("subjects")
    if isinstance(raw_subjects, list):
        cleaned["subjects"] = [
            ({key: value for key, value in subject.items() if key != "body_reshape"}
             if isinstance(subject, dict) else subject)
            for subject in raw_subjects
        ]
    for key in _BODY_RESHAPE_LEGACY_KEYS:
        cleaned.pop(key, None)
    prompts = cleaned.get("prompts")
    if isinstance(prompts, dict):
        prompts = dict(prompts)
        prompts["photo_analysis_body_reshape"] = None
        cleaned["prompts"] = prompts
    return cleaned


def body_reshape_items(analysis):
    """Return actionable per-person plans for the Editor hand-off."""
    return [entry for entry in body_reshape_plan_entries(analysis, include_disabled=False)
            if entry.get("active")]


def build_body_reshape_edit_block(analysis):
    """Render detailed, independently identified body commands."""
    targets = body_reshape_items(analysis)
    if not targets:
        return ""
    target_lines = []
    for item in targets:
        target_lines.append(
            f'- Subject: {item["subject_label"]}\n'
            f'  Approximate location in frame: {json.dumps(item["box"], ensure_ascii=False)}\n'
            f'  Approved body and outer-contour adjustment:\n    {item["edit_prompt"]}'
        )
    return """Body reshape execution instructions (analysis-approved):
- Execute every target for its named person, but only the body/head/neck contours explicitly named there. Labels and boxes identify the person; they are not target positions or silhouettes. Do not infer another body change. Separately approved non-body or style endpoints—including gaze-driven face orientation or head alignment—remain mandatory.
- Apply each printed direction and amount exactly. A percentage is the total change of that named visible local 2D measurement, not per side or of the whole person/canvas. Apply overlapping waist/abdomen measurements once as one continuous contour. Tonal changes, blur, sharpening, or redrawn folds cannot substitute for the required geometry.
- Change head or neck geometry only when the target explicitly names it. Scale an approved head outer contour uniformly in width and height; adjust only the named visible neck width, keeping its centerline and attachments coherent. Use each printed amount independently and add no unlisted compensation.
- Move the existing garment silhouette, texture, seams, folds, and clothing/skin boundary continuously with an approved contour while preserving garment identity, design, material, pattern, construction, layering, openings, coverage, and wearing method. Never add/remove fabric or change coverage to fake the result.
- Continue only the narrow adjacent strip of the same background where an outer contour moves. Do not infer extra body geometry from this block, and do not warp, smear, duplicate, or displace content as a consequence of that local contour repair. This local artifact constraint never freezes or overrides another approved feature or style endpoint.

Targets:
""" + "\n".join(target_lines)


# The photo-specific target and approved plan already name the operations. This
# block tells the image model how to execute them without adding a second grade.
COLOR_CRAFT_DIRECTION = (
    "Realize the Finished light and color target through the approved correction plan and any selected style as one coherent photographic grade. "
    "Make every named light and color adjustment clearly visible, with natural tonal transitions and believable material rendering. "
    "Let the approved scene palette change independently from complexion; skin follows the dedicated skin instructions in this prompt."
)

# A short end-state check is more useful than repeating the adjustment recipe.
COLOR_GRADE_CHECK = (
    "Before finishing, verify that the result matches the Finished light and color target, every approved light/color adjustment is visible, "
    "and scene structures and materials remain recognizable."
)


# ── Rejected-axis gating ──────────────────────────────────────────────────────
#
# Dropping an unchecked item from correction_plan is not enough to stop the edit
# model from making that change, because the same change is requested in several
# other places the checkbox never touched: the analysis prose (aesthetic_diagnosis,
# detected_issues, lighting_strategy, memory_color_strategy) still describes the
# problem and the preset can still describe that change. One negative sentence
# loses that argument on volume.
#
# So each correction_plan item carries axis tags (correction_axes), and unchecking
# it removes generic numbered priorities that ask for distraction cleanup or
# horizon straightening. The light/color execution sentence now authorizes only
# named approved work, so it needs no parallel list of axis-specific operations.
# Removal is the whole job: saying what to preserve
# instead is correction_inverse's, written per item by the analysis model so it
# carries that item's scope. Several axes -- skin,
# sharpen, other -- gate nothing today and are kept in the vocabulary only
# because the model reaches for them naturally; an axis that gates nothing costs
# nothing.
#
# A wrong tag degrades to the pre-feature behavior rather than breaking: the
# clause stays and correction_inverse still holds the line. The vocabulary lives
# at the top of this file (AXIS_DEFINITIONS), since ANALYSIS_INSTRUCTION has to
# hand the same list to the analysis model.

def build_color_craft_direction(rejected_axes=()):
    # This authorizes only changes already named by the approved plan or style,
    # so it cannot recreate an unchecked correction through generic craft prose.
    return COLOR_CRAFT_DIRECTION


def build_color_grade_check(rejected_axes=()):
    return COLOR_GRADE_CHECK


def resolve_rejected_axes(analysis):
    """Axes the user switched off, as tagged on the unchecked correction_plan items.

    An axis only counts as rejected when NO kept item still needs it: a kept
    "darken the busy background" and a rejected "brighten the whole frame" can
    both carry `contrast`, and locking contrast to the original there would break
    the correction the user did approve. Falls back to an empty list whenever the
    tags are missing or untrustworthy, which leaves the previous behavior --
    rejected item text only -- intact.
    """
    forced_original_composition = bool(analysis.get("_preserve_original_composition"))
    rejected = _axis_set(analysis.get("rejected_axes"))
    if not rejected or not _axes_aligned(analysis):
        return ["crop"] if forced_original_composition else []
    kept = _axis_set(analysis.get("correction_axes"))
    # An unrecognized tag on a KEPT item is not symmetric with one on a rejected
    # item: unknown rejected axes simply fall out of the CORRECTION_AXES loop
    # below, but an unknown kept axis means an approved correction has silently
    # lost its ability to protect itself, and we cannot tell whether it overlaps
    # something being rejected. Give up on clause removal for this edit rather
    # than emit a prompt that forbids what it also asks for -- the per-item
    # correction_inverse text still holds the line, it just isn't reinforced.
    unknown_kept = kept - set(CORRECTION_AXES)
    if unknown_kept:
        logger.warning(
            "approved corrections carry unrecognized axes %s; skipping axis gating for this edit "
            "(add them to AXIS_DEFINITIONS or AXIS_ALIASES)", sorted(unknown_kept),
        )
        return ["crop"] if forced_original_composition else []
    return [
        axis for axis in CORRECTION_AXES
        if (axis in rejected and axis not in kept) or (axis == "crop" and forced_original_composition)
    ]


_ORIGINAL_COMPOSITION_PRESERVE = (
    "維持原圖的長寬比、完整畫面邊界、主體大小與位置及留白平衡；"
    "不做構圖裁切、不擴圖，也不重新構圖。水平與透視由獨立的校正項目決定。"
)


def apply_rejected_reframing(analysis, aspect_ratio):
    """Turn an unchecked crop recipe into one effective original-frame edit.

    Removing the crop item from ``correction_plan`` is insufficient: the
    analyzer's crop_box and recommended_aspect_ratio would still drive the
    provider canvas and local crop path. Resolve the rejection once at the
    finish-stage boundary so every editor, QA, and local-only execution sees
    the same no-reframe decision.
    """
    # This decision is narrower than the general resolve_rejected_axes gate:
    # an unrelated kept item carrying an unknown model-invented axis must not
    # allow a clearly aligned, explicitly rejected crop to survive.  Only a
    # still-approved crop item can keep reframing active.
    rejected_crop = "crop" in _axis_set(analysis.get("rejected_axes"))
    kept_crop = "crop" in _axis_set(analysis.get("correction_axes"))
    prior_source = analysis.get("_rejected_reframing_source")
    if kept_crop and not rejected_crop and isinstance(prior_source, dict):
        # Re-checking the recipe has to restore the proposal that was active
        # before the declined run. Merely removing rejected_axes leaves the
        # previous result's crop_box.enabled=false and recommended ratio at
        # "original", which turns the newly-approved item into a no-op.
        effective = copy.deepcopy(analysis)
        effective.update(copy.deepcopy(prior_source))
        effective.pop("_rejected_reframing_source", None)
        effective.pop("_preserve_original_composition", None)
        effective.pop("applied_crop_plan", None)
        effective["_aspect_ratio_pref_at_edit"] = aspect_ratio
        return effective, aspect_ratio
    if not (_axes_aligned(analysis) and rejected_crop and not kept_crop):
        effective = dict(analysis)
        effective["_aspect_ratio_pref_at_edit"] = aspect_ratio
        return effective, aspect_ratio

    effective = copy.deepcopy(analysis)
    effective["_rejected_reframing_source"] = copy.deepcopy(prior_source) if isinstance(prior_source, dict) else {
        key: copy.deepcopy(analysis.get(key))
        for key in (
            "recommended_aspect_ratio", "aspect_ratio_reason", "crop_box",
            "crop_candidates", "crop_intent", "crop_recommendation",
        )
        if key in analysis
    }
    effective["recommended_aspect_ratio"] = "original"
    effective["aspect_ratio_reason"] = "使用者取消「重新構圖」，本次維持原圖長寬比與完整畫面邊界。"
    source_crop = effective.get("crop_box") or {}
    effective["crop_box"] = {
        **source_crop,
        "enabled": False,
        "aspect_ratio": "original",
        "reason": _ORIGINAL_COMPOSITION_PRESERVE,
    }
    effective["crop_candidates"] = []
    effective["crop_intent"] = {
        "trim_left": False,
        "trim_right": False,
        "trim_top": False,
        "trim_bottom": False,
        "subject_anchor": "center",
        "looking_direction": "unknown",
        "remove_edge_distractions": [],
    }
    effective["crop_recommendation"] = _ORIGINAL_COMPOSITION_PRESERVE
    effective["applied_crop_plan"] = None
    effective["_preserve_original_composition"] = True
    effective["_aspect_ratio_pref_at_edit"] = "original"
    return effective, "original"


def _axes_aligned(analysis):
    """Whether correction_axes really is one-to-one with correction_plan.

    The analysis prompt demands it, but this is LLM output, and the frontend maps
    a recipe checkbox to its axes purely by list index -- a length mismatch means
    unchecking item 2 would suppress item 3's axes, which is worse than not using
    the tags at all. Checked after the frontend has already split both lists, so
    the test is that each axis list still matches its text list.
    """
    for axes_key, text_key in (("correction_axes", "correction_plan"),
                               ("rejected_axes", "rejected_corrections")):
        axes_len, text_len = len(analysis.get(axes_key) or []), len(analysis.get(text_key) or [])
        if axes_len != text_len:
            logger.warning(
                "correction_axes not aligned with correction_plan (%s=%d, %s=%d); "
                "ignoring axis tags for this edit", axes_key, axes_len, text_key, text_len,
            )
            return False
    return True


def _axis_set(value):
    """Flatten the ["exposure", ["exposure", "highlight"]] shapes into a set.

    correction_axes is a list-of-lists per the schema, rejected_axes a flat list
    from the frontend, and a model that ignores the nesting sends bare strings --
    all three normalize here so callers never have to care. Synonyms are folded
    to their canonical axis via AXIS_ALIASES; anything still unrecognized is kept
    as-is so callers can notice it rather than have it vanish.
    """
    axes = set()
    for entry in value or []:
        for axis in ([entry] if isinstance(entry, str) else (entry or [])):
            if isinstance(axis, str) and axis.strip():
                name = axis.strip().lower()
                axes.add(AXIS_ALIASES.get(name, name))
    return axes


_ALIGNMENT_CORRECTION_TERMS = (
    "rotate", "rotation", "straighten", "level the horizon",
    "horizon correction", "perspective correction", "keystone",
    "旋轉", "水平校正", "修正水平", "校正地平", "地平線校正", "透視校正",
    "垂直校正", "拉直", "扶正",
)


def has_approved_alignment_correction(analysis):
    """Whether the reviewed plan still approves whole-frame alignment work.

    New analyses use the dedicated ``alignment`` axis. The crop+keyword branch
    keeps historical analyses executable without making ordinary crop items an
    implicit licence to straighten the image.
    """
    axes = analysis.get("correction_axes") or []
    for index, raw_item in enumerate(analysis.get("correction_plan") or []):
        item_axes = _axis_set([axes[index]]) if index < len(axes) else set()
        if "alignment" in item_axes:
            return True
        item = str(raw_item or "").strip().lower()
        if "crop" in item_axes and any(term in item for term in _ALIGNMENT_CORRECTION_TERMS):
            return True
    return False


_LIGHT_TARGET_AXES = {"exposure", "shadow", "highlight", "contrast", "lighting"}
_COLOR_TARGET_AXES = {"white_balance", "hue", "saturation", "tint", "skin", "color"}


def build_reviewed_light_color_target(analysis, fallback_intent="", lighting_style=None):
    """Build the effective target after the user reviews correction_plan.

    Analyzer target fields are free prose and can combine several adjustments
    in one sentence. When the user rejects a light/color item, suppress the
    affected strategy wholesale instead of trying to cut a clause by keyword;
    the still-approved correction_plan remains the executable target.
    """
    has_rejected = bool(analysis.get("rejected_corrections"))
    rejected_items = list(analysis.get("rejected_corrections") or [])
    rejected_axis_items = list(analysis.get("rejected_axes") or [])
    rejected_axes = (
        _axis_set(rejected_axis_items)
        if len(rejected_axis_items) == len(rejected_items) else set()
    )
    # edit_intent is unstructured prose and may summarize any recipe item, so
    # it is stale as soon as the user changes that recipe. Axis tags let the two
    # narrower strategy fields survive only when their own domain is untouched.
    # Old analyses without axis tags take the safe path and discard both.
    unknown_rejection = has_rejected and not rejected_axes
    light_rejected = unknown_rejection or bool(rejected_axes & _LIGHT_TARGET_AXES)
    color_rejected = unknown_rejection or bool(rejected_axes & _COLOR_TARGET_AXES)
    target_rejected = has_rejected

    fields = []
    if target_rejected:
        fields.append((
            "Reviewed finished result",
            "Use the approved correction plan below as the effective light-and-color target, together with any selected style.",
        ))
    elif not lighting_style:
        # The analyzer's edit_intent predates the user's lighting selection and
        # may ask for a competing warm or bright finish.
        intent = analysis.get("edit_intent") or fallback_intent
        if intent:
            fields.append(("Finished visual result", intent))

    if lighting_style:
        lighting_label = STYLE_OPTION_VALUE_LABELS["lighting"].get(
            lighting_style, lighting_style
        )
        fields.append((
            "Lighting and color plan",
            f"Use the selected {lighting_label} style as the final atmosphere.",
        ))
    else:
        if not light_rejected and analysis.get("lighting_strategy"):
            fields.append(("Lighting plan", analysis.get("lighting_strategy")))
        if not color_rejected and analysis.get("memory_color_strategy"):
            fields.append(("Color plan", analysis.get("memory_color_strategy")))

    if not fields:
        fields.append((
            "Reviewed finished result",
            "Use the approved correction plan below as the complete light-and-color target.",
        ))
    lines = [f"- {label}: {str(value).strip()}" for label, value in fields]
    closing = (
        "- User-preserved choices below override the original analysis for their named regions and properties."
        if analysis.get("rejected_corrections") else
        "- Treat these as one target. The approved correction plan below must realize it."
    )
    return "Finished light and color target:\n" + "\n".join(lines + [closing])


def build_user_preserved_choices(analysis):
    """Render unchecked recipe items as positive source-state instructions."""
    rejected = list(analysis.get("rejected_corrections") or [])
    if not rejected:
        return ""
    inverses = list(analysis.get("rejected_preserve") or [])
    if len(inverses) == len(rejected) and all(str(item).strip() for item in inverses):
        choices = [str(item).strip() for item in inverses]
    else:
        choices = [
            f"Preserve the original state in the exact region and properties covered by this declined item: {item}"
            for item in rejected
        ]
    if analysis.get("_preserve_original_composition"):
        # Deterministic backstop for old/vague correction_inverse values.  The
        # finish-stage resolver also disables crop geometry and forces the
        # original canvas ratio; this line makes the same decision explicit to
        # both legacy and compact edit prompts.
        choices.insert(
            0,
            "Keep the source photo's exact aspect ratio and complete outer boundaries. Reproduce the original "
            "subject scale, placement, and empty-space balance; do not apply a composition crop, expand, or "
            "recompose the frame. A separately approved alignment correction may still change tilt or perspective "
            "and trim only the exposed outer wedges it creates. When such an alignment correction remains approved, "
            "it overrides any tilt/perspective-preservation wording inherited from the declined reframing item.",
        )
    return (
        "User-preserved choices (highest priority for these scopes):\n"
        + "\n".join(f"- {item}" for item in choices)
        + "\n- Apply the approved correction plan everywhere else."
    )


# The geometric invariant, in one sentence. Deliberately short and free of
# concrete scene nouns: an earlier version listed "handrail, staircase, rail
# track, roofline, horizon" as examples of edges that must keep their angle, but
# an image model reads content words as content, so a café portrait was being
# handed a rail track. Covers the three things a stretch destroys -- build,
# tilt, and how elements sit relative to each other.
CONTENT_SHAPE_RULE = (
    "Everything inside the frame keeps the proportions, the tilt, and the relative sizes and spacing it has in "
    "the input photograph."
)

BODY_RESHAPE_CONTENT_SHAPE_RULE = (
    "Everything inside the frame keeps its tilt, relative placement, and unlisted proportions from the input photograph. "
    "The one holistic body instruction may adjust the visible figure as a continuous, anatomically natural contour, "
    "with only the necessary local garment and adjacent-background follow-through."
)


def _content_shape_rule(has_body_reshape=False):
    return BODY_RESHAPE_CONTENT_SHAPE_RULE if has_body_reshape else CONTENT_SHAPE_RULE

# The resize half of the answer, stated as a permission first. "Do not stretch"
# on its own is a weak instruction -- it names the failure without naming a legal
# move, so the model is left to find one. Naming uniform scaling as the available
# operation gives it somewhere to go, and only then is the one-axis case ruled
# out.
UNIFORM_SCALE_ONLY = (
    "Where the output's pixel size differs from the input's, scale the whole photograph uniformly -- one and the "
    "same factor on width and height. Resizing a single axis is not available: the canvas is never filled by "
    "squeezing or stretching the picture sideways or vertically."
)

# The reframe half. Kept separate from the sentence above because the no-crop
# branch needs the scaling permission without an invitation to move edges.
FRAME_SHAPE_SOURCE = (
    "Two operations, either or both, get the photograph onto this canvas shape: cropping -- choosing where the "
    "frame's edges fall, tighter or taking in more of the surrounding scene -- and uniform scaling. "
    + UNIFORM_SCALE_ONLY
)

# Same sentence for the branch that may extend: "taking in more of the
# surrounding scene" reads as widening the crop inside the photograph, which is
# not on offer once the frame already reaches its edges, so the third operation
# is named outright instead of left to be inferred.
FRAME_SHAPE_SOURCE_EXPAND = (
    "Three operations, in any combination, get the photograph onto this canvas shape: cropping -- choosing where "
    "the frame's edges fall -- continuing the scene past the photograph's own edge, and uniform scaling. "
    + UNIFORM_SCALE_ONLY
)


def _parse_ratio_label(label):
    """"1088:1456" / "4:5" -> a float. None for "original", blank, or anything
    unparseable.

    A local five-liner rather than image_tools.parse_aspect_ratio: prompts.py is
    pure text assembly and importing image_tools would drag PIL and pillow_heif
    in behind it.
    """
    if not label:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)\s*", str(label))
    if not match:
        return None
    w, h = float(match.group(1)), float(match.group(2))
    return (w / h) if w > 0 and h > 0 else None


# Same threshold as image_tools.GEOMETRY_RATIO_TOLERANCE: below this the two
# shapes are the provider's raster grid disagreeing with a named ratio, not a
# real difference the model has to resolve.
_CANVAS_SHAPE_TOLERANCE = 0.02


def _canvas_shape_difference(aspect_label, source_size):
    """"narrower" / "wider" / "same" -- how the requested canvas differs in shape
    from the input photograph. None when it cannot be determined.

    This is the one piece of the framing constraint that is arithmetic rather
    than taste, and it is why an edit can come back squeezed while every stated
    rule was followed: when the canvas is not the source's shape, part of the
    photograph cannot be in the output, no matter how well framed it already is.
    Left unsaid, "the canvas must be 3:4" and "don't crop much" look jointly
    satisfiable, and the model settles it by scaling one axis.

    Derived from the two ratios only -- it does not depend on where the analysis
    put its box, which is the unreliable part.
    """
    target = _parse_ratio_label(aspect_label)
    if not target or not source_size:
        return None
    try:
        source_w, source_h = float(source_size[0]), float(source_size[1])
    except (TypeError, ValueError, IndexError):
        return None
    if source_w <= 0 or source_h <= 0:
        return None

    drift = target / (source_w / source_h) - 1
    if abs(drift) <= _CANVAS_SHAPE_TOLERANCE:
        return "same"
    return "wider" if drift > 0 else "narrower"


# What the shape difference means for the finished picture, said as something the
# model can see rather than as an amount it would have to measure. "Give up about
# 7% of the width" is the same kind of instruction as "x=0.120, w=0.600": an
# image model has no ruler. "The left and right edges cannot both be inside this
# frame" is a fact it can check by looking.
_CANVAS_SHAPE_CONSEQUENCE = {
    "narrower": (
        "This frame is narrower than the photograph you were given: the photograph's left and right edges cannot "
        "both be inside it, so something along the sides ends up outside the final frame. You choose what, guided "
        "by the subject. Where the composition notes below ask for side breathing room this frame cannot hold, let "
        "part of the scene go rather than narrowing the people and objects in it."
    ),
    "wider": (
        "This frame is wider than the photograph you were given: the photograph's top and bottom edges cannot both "
        "be inside it, so something along the top or bottom ends up outside the final frame. You choose what, "
        "guided by the subject. Where the composition notes below ask for headroom this frame cannot hold, let part "
        "of the scene go rather than flattening the people and objects in it."
    ),
    "same": (
        "This frame is the same shape as the photograph you were given, so nothing has to be given up to reach it. "
        "If the photograph is already well framed, a small trim is a fine answer."
    ),
}


def _crop_has_visible_trim(w, h, axis_threshold=0.08, area_threshold=0.12):
    """A composition crop is visible by either axis span or total removed area."""
    width = max(0.0, min(1.0, w))
    height = max(0.0, min(1.0, h))
    return (
        (1 - width) >= axis_threshold
        or (1 - height) >= axis_threshold
        or (1 - width * height) >= area_threshold
    )


def _describe_crop_tightness(w, h):
    """How tight the proposed region is, qualitatively.

    Only tightness survives from the proposed rectangle -- not its position, and
    not which edges it trimmed. Those are exactly what the analysis pass gets
    wrong (a box beside the real subject trims the wrong edges), and a wrong
    direction stated vaguely is still wrong, just harder to notice. How much of
    the frame is kept is the one part that stays roughly true even when the box
    is misplaced.
    """
    kept = max(0.0, w) * max(0.0, h)
    if not _crop_has_visible_trim(w, h):
        return "close to the full frame; little more than the outer edges come off"
    if kept >= 0.6:
        return "a moderate tightening of the original frame"
    if kept >= 0.35:
        return "a noticeably tighter frame than the original"
    return "a tight frame filled by the subject"


# An upper bound on the retained fraction, not the decision. At 1.0 the analysis
# is asked to choose for every requested shape that differs from the
# photograph's at all, which is the intent: how much a crop would cost is only
# half the question, and the other half -- whether the subject still has room to
# lose -- is not arithmetic. A square filled edge to edge by its subject can
# want extending at 3:4, where a crop costs only 25%. Lower it to ask only on
# wide gaps; 0 keeps every ratio crop-only.
EXPAND_PERMISSION_RETAINED_THRESHOLD = 1.0

# Whether "auto" and "original" get the permission too. Normally they do not:
# under auto the model chose the shape itself and can choose one the photograph
# already fits, and original has no shape difference to bridge.
EXPAND_PERMISSION_ANY_PREFERENCE = False


def _cross_direction_retained(aspect_label, source_size):
    """Fraction of the source frame a crop to `aspect_label` can keep at most.

    min(r_target/r_source, r_source/r_target): 1.0 when the shapes match, and
    it falls as the two diverge, in either direction. None when undeterminable.
    """
    target = _parse_ratio_label(aspect_label)
    if not target or not source_size:
        return None
    try:
        source_w, source_h = float(source_size[0]), float(source_size[1])
    except (TypeError, ValueError, IndexError):
        return None
    if source_w <= 0 or source_h <= 0 or target <= 0:
        return None
    source = source_w / source_h
    return min(target / source, source / target)


def expand_permitted(aspect_ratio_pref, aspect_label, source_size):
    """Whether this output shape is far enough from the source's to let the
    edit model extend the scene rather than crop its way there.

    Normally only a shape the user named explicitly -- see
    EXPAND_PERMISSION_ANY_PREFERENCE. A same-shape canvas never reaches the
    expand wording regardless, since there is no axis to extend.
    """
    if not EXPAND_PERMISSION_RETAINED_THRESHOLD:
        return False
    pref = str(aspect_ratio_pref or "auto").strip().lower()
    if pref in {"auto", "original"} and not EXPAND_PERMISSION_ANY_PREFERENCE:
        return False
    # A canvas of the photograph's own shape has no axis to extend, so there is
    # nothing to decide. Same tolerance as every other shape comparison here.
    if _canvas_shape_difference(aspect_label, source_size) not in {"narrower", "wider"}:
        return False
    retained = _cross_direction_retained(aspect_label, source_size)
    return retained is not None and retained < EXPAND_PERMISSION_RETAINED_THRESHOLD


# Replaces the crop-only consequence when extending is permitted: reaching the
# shape no longer forces content out, so the choice -- give up the sides, or
# carry the scene further -- is stated as the choice it now is.
_EXPAND_SHAPE_CONSEQUENCE = {
    "narrower": (
        "This frame is narrower than the photograph you were given, so the photograph's full width and the "
        "frame's shape cannot both survive as they are. Cropping is the default answer: let something along the "
        "sides fall outside the final frame, guided by the subject, rather than narrowing the people and objects "
        "in it. If the subject already fills the width, so that cropping to this shape would cut into it or "
        "collapse the composition, carry the scene further above and below instead and keep the subject whole."
    ),
    "wider": (
        "This frame is wider than the photograph you were given, so the photograph's full height and the frame's "
        "shape cannot both survive as they are. Cropping is the default answer: let something along the top or "
        "bottom fall outside the final frame, guided by the subject, rather than flattening the people and objects "
        "in it. If the subject already fills the height, so that cropping to this shape would cut into it or "
        "collapse the composition, carry the scene further out to the sides instead and keep the subject whole."
    ),
}

_EXPAND_ALLOWANCE = (
    "Extending means continuing the same real scene past the photograph's edge with matching perspective, "
    "lighting, depth, materials, and background logic -- never blank borders, transparent or checkerboard "
    "padding, empty bands, smeared or stretched edges, or scenery that was not already part of this place. "
    "Extend only as far as the shape needs, and only on the one side or pair of sides that reaching it requires; "
    "everything the photograph already shows stays in the final frame at its own proportions."
)


def _expand_plan_lines(plan):
    """The analysis decided this frame is reached by extending, so it is stated
    as the instruction it is, not as a permission to weigh. `plan` is
    image_tools.plan_reframe_extension's output."""
    axis, edges = plan["axis"], plan["edges"]
    other = "width" if axis == "height" else "height"
    grown = round((plan["extension"] - 1) * 100)
    lines = [
        f"This frame is reached by continuing the scene past the photograph's {edges}, not by cropping into it: "
        f"the subject already fills the {other}, so there is nothing there to give up. Carry the scene about "
        f"{grown}% further in {axis} across the {edges}, keeping everything the photograph already shows inside "
        f"the final frame at its own proportions.",
    ]
    if plan["kept_other_axis"] < 0.97:
        lines.append(
            f"That extension does not cover the whole shape difference on its own, so about "
            f"{round((1 - plan['kept_other_axis']) * 100)}% of the {other} still leaves the frame -- take it from "
            f"the {other}'s outer edges, guided by the subject, and never by narrowing what stays."
        )
    lines.append(_EXPAND_ALLOWANCE)
    return lines


def _build_crop_instruction_block(
    crop_plan, require_tighter=False, source_size=None, has_body_reshape=False,
    allow_expand=False, expand_plan=None, target_ratio_label=None,
):
    # Before the no-crop branch below: an extension that covers the whole shape
    # difference needs no crop at all -- the best case -- and that branch would
    # answer it with "do not reframe, expand canvas, add borders" over a canvas
    # of a different shape, which is the one instruction pair that guarantees a
    # squeezed result.
    if expand_plan:
        aspect = (
            (crop_plan or {}).get("aspectRatio") or (crop_plan or {}).get("aspect_ratio")
            or target_ratio_label or "the stated output ratio"
        )
        lines = [
            "Reframing:",
            f"- Target output aspect ratio: {aspect}. The output canvas has to be this shape.",
        ]
        if crop_plan:
            lines.append(
                f"- Roughly how tight: {_describe_crop_tightness(crop_plan['w'], crop_plan['h'])}."
            )
        lines.extend(_expand_plan_lines(expand_plan))
        lines.append(f"{FRAME_SHAPE_SOURCE_EXPAND} {_content_shape_rule(has_body_reshape)}")
        return "\n".join(lines)

    if not crop_plan:
        return (
            "No crop. The supplied image boundaries are the final frame. Do not reframe, expand canvas, add "
            "borders, or invent empty side bands. " + UNIFORM_SCALE_ONLY + " "
            + _content_shape_rule(has_body_reshape)
        )

    aspect = crop_plan.get("aspectRatio") or crop_plan.get("aspect_ratio") or "keep the resulting region's natural ratio"

    # The "a small trim is a fine answer" reassurance is true only when the canvas
    # is the source's own shape. On a canvas of a different shape it is arithmetic
    # nonsense -- there is no zero-trim solution -- and issuing it anyway is what
    # let a 4:5 photo come back as a 3:4 canvas with the full frame squeezed into
    # it. So it now lives in the "same" branch alone.
    shape_difference = _canvas_shape_difference(aspect, source_size)
    lines = [
        "Reframing:",
        f"- Target output aspect ratio: {aspect}. The output canvas has to be this shape.",
        f"- Roughly how tight: {_describe_crop_tightness(crop_plan['w'], crop_plan['h'])}.",
    ]
    expanding = allow_expand and shape_difference in _EXPAND_SHAPE_CONSEQUENCE
    if shape_difference:
        consequence = (
            _EXPAND_SHAPE_CONSEQUENCE if expanding else _CANVAS_SHAPE_CONSEQUENCE
        )[shape_difference]
        if shape_difference == "same" and _crop_has_visible_trim(crop_plan["w"], crop_plan["h"]):
            consequence = (
                "This frame keeps the photograph's canvas shape while visibly tightening the composition. "
                "Execute the approved crop and let the selected outer content leave the final frame."
            )
        if has_body_reshape:
            consequence = consequence.replace(
                "rather than narrowing the people and objects in it.",
                "rather than narrowing other people, objects, or unrelated geometry; the holistic body instruction remains "
                "the only deliberate figure-geometry exception.",
            ).replace(
                "rather than flattening the people and objects in it.",
                "rather than flattening other people, objects, or unrelated geometry; the holistic body instruction remains "
                "the only deliberate figure-geometry exception.",
            )
        lines.append(consequence)

    # No coordinates, and no argument about coordinates. An edit model cannot
    # weigh one instruction source against another, so the misplaced rectangle is
    # left out rather than included and annotated as unreliable -- and nothing
    # here describes a bad outcome ("slicing through a face"), which a framing
    # instruction can read as content to render.
    guidance = (
        "Frame the photograph on what it is actually of. Find the main subject and the elements that make the "
        "scene readable, and place the frame so they sit comfortably inside it, with the edges landing clear of "
        "faces, hands, and held objects. The tightness above is only a hint; the subject you can see decides "
        "where the edges go."
    )
    if require_tighter:
        guidance += (
            " Closer framing was requested, so come in closer than the original frame; you choose where the edges land."
        )
    lines.append(guidance)
    if expanding:
        lines.append(_EXPAND_ALLOWANCE)
        lines.append(f"{FRAME_SHAPE_SOURCE_EXPAND} {_content_shape_rule(has_body_reshape)}")
    else:
        lines.append(f"{FRAME_SHAPE_SOURCE} {_content_shape_rule(has_body_reshape)}")
    return "\n".join(lines)


def _box_note(box):
    """The `reason` an analysis box carries -- the part of it that describes
    what matters. The x/y/w/h alongside it are the part that keeps landing
    somewhere else, so the edit prompt takes the words and leaves the numbers."""
    if isinstance(box, dict):
        return (box.get("reason") or "").strip()
    return ""


def _build_framing_intent_block(analysis):
    """What the analysis wanted protected, stated semantically.

    This used to be three lines of raw JSON boxes (ROI, subject extent,
    protected regions). Handing the edit model coordinates it cannot verify
    against a photograph it can see produced the contradiction this block now
    avoids: a box over empty background presented as "the subject", next to a
    scene description naming someone standing elsewhere.
    """
    lines = []
    anchor = (analysis.get("roi_anchor") or "").strip()
    if anchor:
        lines.append(f"- Where the eye should land: {anchor}")

    roi_note = _box_note(analysis.get("roi_box"))
    if roi_note:
        lines.append(f"- Most important thing to keep in frame: {roi_note}")

    extent_note = _box_note(analysis.get("subject_extent_box"))
    if extent_note:
        lines.append(f"- How much of the subject the framing needs: {extent_note}")

    protected = []
    for region in analysis.get("protected_regions") or []:
        if not isinstance(region, dict):
            continue
        label = (region.get("label") or "").strip()
        reason = (region.get("reason") or "").strip()
        if label and reason:
            protected.append(f"{label}（{reason}）")
        elif label or reason:
            protected.append(label or reason)
    if protected:
        lines.append(f"- Also worth keeping: {'; '.join(protected)}")

    if not lines:
        return ""

    return (
        "What matters in this photograph, described rather than located -- find each one in the image itself:\n"
        + "\n".join(lines)
    )


# "鏡頭"/camera-lens language is a reliable gaze-repair signal on its own --
# blink-repair items ask to reopen/relax eyes and never reference the camera
# lens or gaze direction, so this does not need to be more clever than that.
_GAZE_LEAK_KEYWORDS = ("鏡頭", "眼神", "視線", "目光", "eye contact", "gaze", "camera lens", "looking at the camera", "look at the camera")
_BLINK_LEAK_KEYWORDS = ("睜開", "張開眼", "半閉", "閉眼", "眼皮", "reopen", "half-closed", "half closed", "blink")
# "髮"/"hair" alone, rather than enumerating 瀏海/髮絲/碎髮/髮型/... -- every
# hair-tidying correction_plan item names the hair itself somewhere, and the
# same "nothing legitimate to false-positive against" reasoning below applies
# here too, so the broad match costs nothing extra.
_HAIR_LEAK_KEYWORDS = ("髮", "hair")
# 笑容調整 is the only thing in this prompt that touches a mouth's expression,
# so with it off there is nothing legitimate for these to match against --
# see strip_disabled_topic_items on why that is what makes a keyword test
# safe enough here.
_SMILE_LEAK_KEYWORDS = ("笑", "嘴角", "smile", "grin")


def _is_disabled_topic_text(text, gaze_enabled, blink_enabled, hair_enabled, smile_enabled=True):
    """True if `text` names a topic whose Advanced Settings toggle is off.

    Factored out of strip_disabled_topic_items so build_edit_prompt can apply
    the same keyword match to detected_issues -- free-text analysis notes
    that, unlike correction_plan, strip_disabled_topic_items never touches,
    but that still argue for the disabled repair just as loudly (see the
    "aesthetic_diagnosis, detected_issues, ..." comment above
    build_color_craft_direction).
    """
    lowered = str(text or "").lower()
    if not gaze_enabled and any(kw.lower() in lowered for kw in _GAZE_LEAK_KEYWORDS):
        return True
    if not blink_enabled and any(kw.lower() in lowered for kw in _BLINK_LEAK_KEYWORDS):
        return True
    if not hair_enabled and any(kw.lower() in lowered for kw in _HAIR_LEAK_KEYWORDS):
        return True
    if not smile_enabled and any(kw.lower() in lowered for kw in _SMILE_LEAK_KEYWORDS):
        return True
    return False


def strip_disabled_topic_items(analysis):
    """Drops correction_plan items (and their correction_axes/correction_inverse/
    retouch_recipe counterparts at the same index) that describe gaze, blink,
    hair, or smile work when the matching Advanced Settings toggle is off.

    Exists because should_fix=false on the structured gaze_issue/blink_issue/
    hair_issue fields, and even an explicit "do not add this to
    correction_plan for any reason" instruction in the disabled-toggle
    prompt override, have both been observed not to stop the analysis model
    from writing a free-text correction_plan item anyway -- a real run with
    gaze_contact_detection off still produced three gaze-redirection
    correction_plan items, none backed by an approved gaze_issue because
    there wasn't supposed to be one.

    There is no structured field to check here -- correction_plan is free
    text with no subject/kind tag -- so this is a keyword match, deliberately
    scoped to run only when the corresponding toggle is off: with the toggle
    off, a matching item is never legitimate, so the usual false-positive
    risk of keyword matching (harmless here even if a match is coincidental,
    since there is nothing legitimate for it to be a false positive against)
    does not apply the way it would if this ran unconditionally.
    """
    advanced_options = analysis.get("_advanced_options") or {}
    gaze_enabled = advanced_options.get("gaze_contact_detection", True)
    blink_enabled = advanced_options.get("eye_closed_detection", True)
    hair_enabled = advanced_options.get("hair_mess_detection", True)
    smile_enabled = advanced_options.get("smile_adjustment", True)
    if gaze_enabled and blink_enabled and hair_enabled and smile_enabled:
        return analysis

    plan = list(analysis.get("correction_plan") or [])
    if not plan:
        return analysis

    keep_indices = [
        i for i, item in enumerate(plan)
        if not _is_disabled_topic_text(item, gaze_enabled, blink_enabled, hair_enabled, smile_enabled)
    ]
    if len(keep_indices) == len(plan):
        return analysis

    logger.warning(
        "correction_plan contained %d gaze/blink/hair/smile item(s) despite the matching Advanced "
        "Settings toggle being off; removing them",
        len(plan) - len(keep_indices),
    )
    cleaned = dict(analysis)
    cleaned["correction_plan"] = [plan[i] for i in keep_indices]
    # Positionally aligned with correction_plan but not guaranteed the same
    # length -- an untrustworthy analysis output already falls back gracefully
    # elsewhere (see _axes_aligned) -- so only reindex a list that actually
    # has an entry at every kept position.
    for key in ("correction_axes", "correction_inverse", "retouch_recipe"):
        values = analysis.get(key) or []
        if len(values) == len(plan):
            cleaned[key] = [values[i] for i in keep_indices]
    return cleaned


def subject_issue_items(analysis, key):
    """Flatten analysis["subjects"][i][key] (one of "blink_issue"/"hair_issue"/
    "gaze_issue"/"smile_issue") into the flat {subject_label, box, ...} shape
    the rest of this module (and pipeline.py's gaze-repair pass) expects from
    the pre-nesting schema, merging in the parent subject's own label and box.

    Nesting these four fields inside subjects[] (instead of four separate
    top-level arrays each carrying its own subject_label/box) removes the
    label-matching failure mode entirely -- a nested field always belongs to
    the subject it lives on -- but every downstream consumer still wants one
    flat item per person, so this is the single place that reassembles it.
    """
    items = []
    for index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict):
            continue
        issue = subject.get(key)
        if not isinstance(issue, dict):
            continue
        item = dict(issue)
        item["subject_label"] = subject.get("label")
        item["box"] = subject.get("box")
        item["_subject_index"] = index
        items.append(item)
    return items


def _disabled_issue_subject_indices(analysis, category):
    """Subject indices the user explicitly opted out of for `category`
    (one of "blink_issue"/"hair_issue"/"gaze_issue"/"smile_issue"), via the
    per-person toggle on the analysis-result face view.

    This is a hard exclusion, checked in addition to should_fix/adjust_safe
    rather than instead of them: it is what actually blocks smile, since
    resolve_smile_targets ignores adjust_safe once the user picks an explicit
    smile level (see its own docstring), and it is redundant-but-harmless
    reinforcement for blink/hair/gaze, which already exclude a should_fix=false
    item from their own targets list.
    """
    disabled = analysis.get("_disabled_face_issues") or []
    return {
        entry.get("subject_index") for entry in disabled
        if isinstance(entry, dict) and entry.get("category") == category
        and isinstance(entry.get("subject_index"), int)
    }


def expand_subject_boxes_to_contain_faces(analysis, face_boxes):
    """Deterministic backstop for the Coordinate rules' face-detector
    containment requirement: the model is asked to make a person subject's
    box fully contain its verified, correctly-matched local face detection,
    but that is a request to an LLM, not a guarantee -- observed in practice
    to return a box that starts around the chin/mouth and misses the face
    entirely, which then breaks the frontend's box-overlap face<->subject ID
    matching (bestSubjectMatch in results.js) since there is nothing left to
    overlap.

    face_boxes: the same tight, unpadded {x,y,w,h} boxes detect_face_crops
    already computed and handed to the model as its "Local face detector
    reference" text -- no extra detection work here, just reusing it to
    enforce the rule the prompt could only ask for.

    Matching a face to a subject can't reuse a plain area-overlap test here:
    the exact failure this backstops (a box that already excludes most of
    the face) is also the case where area overlap is smallest, so a
    containment- or IoU-style match would frequently miss the very box that
    needs fixing. Two people standing side by side are reliably separated on
    the x axis even when one person's box is vertically wrong, so faces and
    subjects are paired by nearest horizontal center instead, greedily,
    nearest pairs first, capped at a generous distance so an unrelated face
    (a bystander, a false-positive detection) never gets unioned into the
    wrong person's box.
    """
    subjects = analysis.get("subjects")
    if not isinstance(subjects, list) or not face_boxes:
        return analysis

    def x_center(b):
        return b["x"] + b["w"] / 2

    candidates = []
    for si, subject in enumerate(subjects):
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        box = subject.get("box")
        if not isinstance(box, dict) or not all(isinstance(box.get(k), (int, float)) for k in ("x", "y", "w", "h")):
            continue
        for fi, fb in enumerate(face_boxes):
            if not isinstance(fb, dict) or not all(isinstance(fb.get(k), (int, float)) for k in ("x", "y", "w", "h")):
                continue
            candidates.append((abs(x_center(box) - x_center(fb)), si, fi))
    candidates.sort(key=lambda c: c[0])

    MAX_CENTER_DISTANCE = 0.2  # generous: well under a typical shoulder-to-shoulder gap
    matched_subjects, matched_faces = set(), set()
    pairs = []
    for distance, si, fi in candidates:
        if distance > MAX_CENTER_DISTANCE or si in matched_subjects or fi in matched_faces:
            continue
        matched_subjects.add(si)
        matched_faces.add(fi)
        pairs.append((si, fi))

    if not pairs:
        return analysis

    new_subjects = list(subjects)
    expanded_labels = []
    for si, fi in pairs:
        box = subjects[si]["box"]
        fb = face_boxes[fi]
        x0 = min(box["x"], fb["x"])
        y0 = min(box["y"], fb["y"])
        x1 = max(box["x"] + box["w"], fb["x"] + fb["w"])
        y1 = max(box["y"] + box["h"], fb["y"] + fb["h"])
        if x0 == box["x"] and y0 == box["y"] and x1 == box["x"] + box["w"] and y1 == box["y"] + box["h"]:
            continue  # already contains the matched face; nothing to fix
        new_subjects[si] = {
            **subjects[si],
            "box": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
        }
        expanded_labels.append(subjects[si].get("label") or "unnamed subject")

    if not expanded_labels:
        return analysis

    logger.warning(
        "subjects[].box did not contain its matched local face detection for %s; expanding to fit "
        "(see ANALYSIS_INSTRUCTION's face-detector containment rule)",
        "、".join(expanded_labels),
    )
    return {**analysis, "subjects": new_subjects}


def _build_skin_identity_lines(analysis):
    """Whose skin each face is, one line per person, from subjects[].skin."""
    lines = []
    for subject in analysis.get("subjects") or []:
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        skin = (subject.get("skin") or "").strip()
        label = (subject.get("label") or "").strip()
        if skin and label and skin not in {"不適用", "N/A"}:
            lines.append(f"- {label}: {skin}")
    if not lines:
        return "- Whose skin each face is, you read from the photograph itself: the depth and undertone a face already has are what it keeps."
    return "\n".join(lines)


# Trigger on is_person too, not just subject_category: a 旅行 /
# 街拍 photo still has faces, and both the skin rules and the smile block are
# about faces rather than about what the photo is nominally of.
def _has_person(analysis):
    subjects = analysis.get("subjects") or []
    return analysis.get("subject_category") == "人物" or any(
        isinstance(s, dict) and s.get("is_person") for s in subjects
    )


# ── Smile adjustment (人像設定「笑容調整」) ─────────────────────────────────────
#
# The user picks the level; the analysis only records where each face's mouth
# already sits (each subject's smile_issue). Keeping those two apart is what lets the level
# be changed on the results page without re-analyzing -- a target resolved at
# analyze time would go stale the moment the user picked a different level,
# and the readings themselves stay valid whatever the level becomes.
SMILE_LEVEL_LABELS = {
    "auto": "自動（不笑的人加上自然笑容，已經在笑的人維持原樣）",
    "neutral": "不笑",
    "gentle": "微笑",
    "broad": "開朗笑",
    "laughing": "大笑",
}

SMILE_TARGET_DESCRIPTIONS = {
    "neutral": "a neutral or relaxed expression with no apparent smile",
    "gentle": "a small, clearly readable natural smile achieved with minimal anatomically plausible change while preserving the person's original mouth shape, proportions, and natural asymmetry",
    "broad": "a strong, cheerful, composed smile that clearly remains below laughter intensity",
    "laughing": "an intense, joyful expression of laughter",
}

_SMILE_INTENSITIES = ("neutral", "gentle", "broad", "laughing")
_SMILE_INTENSITY_RANK = {level: rank for rank, level in enumerate(_SMILE_INTENSITIES)}
_SMILING_INTENSITIES = ("gentle", "broad", "laughing")
_SMILE_INTENSITY_ALIASES = {
    "none": "neutral", "no_smile": "neutral", "no smile": "neutral",
    "not_smiling": "neutral", "unsmiling": "neutral", "resting": "neutral",
    "serious": "neutral", "neutral_expression": "neutral",
    "faint_smile": "gentle", "slight_smile": "gentle", "subtle_smile": "gentle",
    "soft_smile": "gentle", "mild_smile": "gentle", "closed_smile": "gentle",
    "closed_lip_smile": "gentle", "smiling": "gentle", "smile": "gentle",
    "bright_smile": "broad", "toothy_smile": "broad", "open_smile": "broad",
    "grin": "broad", "grinning": "broad", "wide_smile": "broad", "big_smile": "broad",
    "laugh": "laughing", "laughter": "laughing", "laughing_smile": "laughing",
    # Compatibility for already-saved analyses and defensive handling of a
    # model translating the enum despite the response schema.
    "不笑": "neutral", "無笑容": "neutral", "无笑容": "neutral", "中性": "neutral",
    "微笑": "gentle", "溫和微笑": "gentle", "温和微笑": "gentle", "輕微微笑": "gentle", "轻微微笑": "gentle",
    "開朗笑": "broad", "开朗笑": "broad", "燦爛笑容": "broad", "灿烂笑容": "broad",
    "大笑": "laughing", "大笑表情": "laughing",
}


def _normalize_smile_intensity(value):
    """One of the four intensity levels, or None when no usable reading exists."""
    state = str(value or "").strip().lower().replace("-", "_")
    if state in _SMILE_INTENSITIES:
        return state
    resolved = _SMILE_INTENSITY_ALIASES.get(state) or _SMILE_INTENSITY_ALIASES.get(state.replace("_", " "))
    if resolved:
        return resolved
    if state and "unclear" not in state and "occluded" not in state and "uncertain" not in state:
        logger.warning("smile_issue carried an unrecognized smile_intensity %r; ignoring that face", value)
    return None


def _smile_target_state(level, current):
    """The target intensity for one face, or None to leave it as captured."""
    if level == "auto":
        return "gentle" if current == "neutral" else None
    return level if level in _SMILE_INTENSITIES and current != level else None


def smile_level(analysis):
    """The user's chosen level, or None when 笑容調整 is off for this edit."""
    advanced_options = analysis.get("_advanced_options") or {}
    if not advanced_options.get("smile_adjustment", True):
        return None
    level = advanced_options.get("smile_level") or "auto"
    level = {
        "no_smile": "neutral", "closed_smile": "gentle",
        "toothy_smile": "broad", "bright_smile": "broad",
    }.get(level, level)
    return level if level in SMILE_LEVEL_LABELS else "auto"


def resolve_smile_targets(analysis):
    """Per-person {label, box, current, target} for the faces
    the chosen level actually moves -- empty when the switch is off, when no
    face was read, or when every face that was read already sits at that
    level.

    Auto leaves faces marked adjust_safe=false unchanged. An explicit level is
    a user override and targets every readable face, including a face the
    analyzer conservatively marked unsafe; unreadable/unclear faces never
    enter this list because _read_smile_states filters them out.
    """
    level = smile_level(analysis)
    if level is None:
        return []
    disabled_indices = _disabled_issue_subject_indices(analysis, "smile_issue")
    targets = []
    for item in _read_smile_states(analysis):
        if item["_subject_index"] in disabled_indices:
            continue
        item_level = item.get("user_level") or level
        item_level = item_level if item_level in SMILE_LEVEL_LABELS else level
        if item_level == "auto" and item["adjust_safe"] is False and not item.get("user_forced"):
            continue
        target = _smile_target_state(item_level, item["current"])
        if not target:
            continue
        targets.append({**item, "target": target})
    return targets


def _smile_craft_rules(target_levels=()):
    """Short execution rules, adding level-specific craft only when relevant."""
    rules = [
        "- Render each named target as a natural, coordinated expression.",
        "- Preserve identity, facial anatomy and proportions, gaze direction, head pose, skin texture, lighting, "
        "and all unrelated image content.",
    ]
    if "gentle" in target_levels:
        rules.append(
            "- For gentle, use the minimum anatomically plausible change for a small but clearly readable smile. "
            "Preserve mouth anatomy, proportions, and natural asymmetry—not the captured neutral or transitional "
            "expression. Allow enough movement to reach gentle; keep curvature smooth, corner movement modest, and "
            "the surrounding expression coordinated. When the source is caught in speech or has incidentally parted "
            "lips, resolve that transitional state into a stable smile rather than retaining the separation and merely "
            "reshaping its edges. Settle the lips as appropriate for this face and let the surrounding expression "
            "respond subtly, so the result reads as an intentional gentle smile rather than speech, surprise, or a "
            "vacant pose. Modify the existing mouth, not a generic smile shape."
        )
    if "laughing" in target_levels:
        rules.append(
            "- For laughing, make joy coherent across the whole face. Relax brow tension only when it conflicts with "
            "laughter; preserve brow anatomy, natural asymmetry, and compatible movement. Do not leave a worried, "
            "stern, or angry brow."
        )
    rules.extend([
        "- Keep facial texture continuous and expression folds soft and proportional to the person's anatomy and "
        "target intensity; do not add or deepen lines to signal the smile.",
    ])
    rules.append(
        "- Reach the exact target. This block overrides generic instructions to preserve the original expression or "
        "mouth for named subjects; preserve everyone and everything else."
    )
    return "\n".join(rules)


SMILE_PRESERVATION_BLOCK = """Smile preservation instruction (highest priority):
- No smile change is approved for this image. Preserve every person's smile intensity and expression as captured.
- Approved blink or gaze repair may still change only the eye properties authorized elsewhere in this prompt."""


def _read_smile_states(analysis):
    """Readable faces as {label, box, current, adjust_safe}.

    Legacy smile_state values are accepted so previously analyzed photos can
    use the new intensity controls without being analyzed again.
    """
    read = []
    for item in subject_issue_items(analysis, "smile_issue"):
        current = _normalize_smile_intensity(
            item.get("smile_intensity") or item.get("smile_state")
        )
        if not current:
            continue
        read.append({
            "label": (item.get("subject_label") or "").strip() or "畫面中的人",
            "box": item.get("box") or {},
            "current": current,
            "adjust_safe": item.get("adjust_safe"),
            "_subject_index": item.get("_subject_index"),
            "user_level": item.get("_user_level"),
            "user_forced": bool(item.get("_user_forced")),
        })
    return read


def smile_qa_expectation(analysis):
    """What this edit's smile setting was asked to produce, in one line, or ""
    when it asked for no change -- the same three branches build_smile_block
    emits, so QA is never sent looking for a change the edit prompt did not
    request."""
    if not _has_person(analysis):
        return ""
    level = smile_level(analysis)
    if level is None:
        return ""
    targets = resolve_smile_targets(analysis)
    if targets:
        return "; ".join(f'{t["label"]}: {t["current"]} -> {t["target"]}' for t in targets)
    if _read_smile_states(analysis):
        return ""
    if level == "auto":
        return "every person at neutral -> gentle; anyone already smiling is unchanged"
    return f"every person whose expression is readable -> {level}"


def _smile_target_line(target):
    """One person's current and target intensity for the edit model."""
    where = json.dumps(target["box"], ensure_ascii=False)
    current = target["current"]
    target_level = target["target"]
    if current == "neutral" and target_level == "gentle":
        direction = (
            "Make the minimum natural change that clearly reads as gentle rather than neutral at normal viewing "
            "size; an ambiguous pleasant or neutral result is a failed no-op."
        )
    elif current == "laughing" and target_level == "broad":
        direction = (
            "Settle active laughter into a strong, composed cheerful smile. Reduce laughter-specific intensity "
            "across the whole expression while preserving clear joy; the result must read as broad, not laughing."
        )
    elif _SMILE_INTENSITY_RANK[target_level] < _SMILE_INTENSITY_RANK[current]:
        direction = (
            f'Reduce the smile until it clearly reads as {target_level}, not {current}; do not preserve the higher '
            f'intensity.'
        )
    else:
        direction = (
            f'Increase the smile until it clearly reads as {target_level}, not {current}.'
        )
    return (
        f'- {target["label"]} (approximately at {where}): '
        f'current_smile: {current}; target_smile: {target_level} '
        f'({SMILE_TARGET_DESCRIPTIONS[target_level]}). Direction: {direction}'
    )


def build_smile_block(analysis):
    """The 笑容調整 instruction for one edit, or "" for a photo with no people.

    Three shapes: the named-person execution block when the chosen level moves
    someone's mouth; the level stated for the photograph as a whole when the
    analysis reported on no mouth at all, so a missing reading cannot silently
    drop a setting the user chose; and the preservation block when the switch
    is off or no face the analysis did read has anywhere left to move.
    """
    if not _has_person(analysis):
        return ""
    level = smile_level(analysis)
    if level is None:
        return SMILE_PRESERVATION_BLOCK

    targets = resolve_smile_targets(analysis)
    if targets:
        lines = "\n".join(_smile_target_line(t) for t in targets)
        target_levels = {t["target"] for t in targets}
        requested_label = (
            SMILE_LEVEL_LABELS[next(iter(target_levels))]
            if len(target_levels) == 1 else "個別人物設定（見下列目標）"
        )
        return f"""Smile adjustment (user-selected, mandatory). Requested level: {requested_label}
- Apply the requested smile intensity to each person named below.
{lines}
{_smile_craft_rules(target_levels)}"""

    if _read_smile_states(analysis):
        return SMILE_PRESERVATION_BLOCK

    if level == "auto":
        goal = (
            f'- Infer each readable person\'s current overall smile intensity. Change neutral to gentle '
            f'({SMILE_TARGET_DESCRIPTIONS["gentle"]}); preserve anyone already smiling.'
        )
    else:
        goal = (
            f'- For every person whose expression is readable: target_smile: {level} '
            f'({SMILE_TARGET_DESCRIPTIONS[level]}).'
        )
    return f"""Smile adjustment (user-selected, mandatory). Requested level: {SMILE_LEVEL_LABELS[level]}
{goal}
{_smile_craft_rules({'gentle' if level == 'auto' else level})}"""


def build_edit_prompt(analysis, preset, preset_prompt, user_direction, crop_plan, revision_feedback=None, aspect_ratio_pref="auto", source_size=None, single_pass_edit=False, return_sections=False):
    analysis = strip_disabled_topic_items(analysis)
    if not body_reshape_enabled():
        analysis = strip_body_reshape(analysis)
    rejected_axes = resolve_rejected_axes(analysis)
    color_craft_direction = build_color_craft_direction(rejected_axes)
    color_grade_check = build_color_grade_check(rejected_axes)

    # Computed once, here, rather than separately next to each of the
    # blink/hair/gaze/smile blocks below: detected_issues (just below) needs
    # them just as much as those blocks do, since a toggle being off does
    # not stop the analysis model from still describing that problem in its
    # free-text notes -- should_fix=false and the "don't add a correction_plan
    # item" instruction only ever governed the structured issue arrays.
    advanced_options = analysis.get("_advanced_options") or {}
    gaze_contact_detection_enabled = advanced_options.get("gaze_contact_detection", True)
    eye_closed_detection_enabled = advanced_options.get("eye_closed_detection", True)
    hair_mess_detection_enabled = advanced_options.get("hair_mess_detection", True)
    smile_adjustment_enabled = advanced_options.get("smile_adjustment", True)

    user_instruction = analysis.get("user_instruction") or {}
    effective_user_direction = (
        user_instruction.get("normalized_user_direction") or user_direction or ""
    )

    # "取代最終送給編輯模型的完整指令" checkbox (index.html, beside 補充指令):
    # send the user's raw text verbatim as the WHOLE edit-model prompt,
    # bypassing every analysis-derived block built below (style/crop/color-
    # grade/retouch-priority/...).
    if user_instruction.get("overwrite_prompt") and effective_user_direction.strip():
        prompt = effective_user_direction.strip()
        return (prompt, {"crop_instruction": "", "style_instruction": ""}) if return_sections else prompt

    # Same leak strip_disabled_topic_items guards against in correction_plan,
    # applied here too: detected_issues is free text the analysis model writes
    # regardless of any toggle, so "右側男性雙眼閉合未鏡頭對焦" or "眼神皆未對鏡頭"
    # can and does show up here even when the matching toggle is off. Filtered
    # only for this edit-prompt string -- the analysis dict itself (and
    # whatever review screen reads it) keeps the full list, since the
    # disabled-toggle overrides explicitly ask for these fields to still be
    # filled in "for reference only".
    detected_issues = "; ".join(
        item for item in (analysis.get("detected_issues") or [])
        if not _is_disabled_topic_text(item, gaze_contact_detection_enabled, eye_closed_detection_enabled, hair_mess_detection_enabled, smile_adjustment_enabled)
    )
    correction_plan = "; ".join(analysis.get("correction_plan") or [])
    preserve_rules = "; ".join(analysis.get("preserve_rules") or [])
    eye_blink_issues = subject_issue_items(analysis, "blink_issue")
    hair_issues = subject_issue_items(analysis, "hair_issue")
    gaze_contact_issues = subject_issue_items(analysis, "gaze_issue")
    body_reshape_block = build_body_reshape_edit_block(analysis)
    has_body_reshape = bool(body_reshape_block)
    approved_edit_sources = (
        "the user-approved correction plan, the analysis-approved body reshape instructions, and user's extra direction"
        if has_body_reshape else
        "the user-approved correction plan and user's extra direction"
    )
    repair_allowance = (
        "If the analysis approved a gaze, blink, and/or body repair for a named subject, follow the analysis-defined method and allowances for that subject."
        if has_body_reshape else
        "If the analysis approved a gaze and/or blink repair for a named subject, follow the analysis-defined method and allowances for that subject."
    )
    faithful_repair_allowance = (
        "If an approved gaze, blink, and/or body repair exists for a named subject, follow the analysis-defined repair method and allowances for that subject; this is not permission for unrelated pose or viewpoint changes."
        if has_body_reshape else
        "If an approved gaze and/or blink repair exists for a named subject, follow the analysis-defined repair method and allowances for that subject; this is not permission for unrelated pose or viewpoint changes."
    )
    faithful_edit_sources = (
        "the approved correction plan, analysis-approved body reshape instructions, preset finish, and user's extra direction"
        if has_body_reshape else
        "the approved correction plan, preset finish, and user's extra direction"
    )
    definitive_edit_sources = (
        "The user-approved correction plan, analysis-approved body reshape instructions, and any user-selected style overrides above"
        if has_body_reshape else
        "The user-approved correction plan and any user-selected style overrides above"
    )
    definitive_edit_sources_faithful = (
        "The user-approved correction plan and analysis-approved body reshape instructions"
        if has_body_reshape else
        "The user-approved correction plan"
    )
    framing_intent_block = _build_framing_intent_block(analysis)
    crop_intent = json.dumps(analysis.get("crop_intent") or {}, ensure_ascii=False)
    if aspect_ratio_pref == "auto" and analysis.get("_aspect_ratio_pref_at_analysis") == "original":
        # The analysis was run while the user had "keep original" selected, so
        # its recommended_aspect_ratio/crop_box.aspect_ratio are forced to
        # "original" by build_analysis_instruction -- not a genuine model
        # judgment. The user has since switched to "Auto" for this edit, so
        # that forced value must not leak in as if it were a real
        # recommendation (it would otherwise silently keep pinning the output
        # to the original ratio even though "Auto" was chosen).
        rec_aspect = "no strong preference -- choose whichever aspect ratio best suits the subject and composition"
        aspect_ratio_reason = "使用者於本次修圖選擇「Auto」；先前分析階段的原圖比例建議是使用者當時選擇「維持原圖比例」所致，非模型本身的判斷，故本次不採用。"
    else:
        rec_aspect = (
            analysis.get("recommended_aspect_ratio") or
            (analysis.get("crop_box") or {}).get("aspect_ratio") or
            "original"
        )
        aspect_ratio_reason = analysis.get("aspect_ratio_reason") or ""

    user_block = ""
    if user_instruction.get("has_user_direction"):
        explicit_priorities = "; ".join(user_instruction.get("explicit_priorities") or [])
        explicit_prohibitions = "; ".join(user_instruction.get("explicit_prohibitions") or [])
        agent_freedom = "; ".join(user_instruction.get("unspecified_agent_freedom") or [])
        conflicts = "; ".join(user_instruction.get("conflicts") or [])
        warning = user_instruction.get("warning") or ""
        user_block = f"""User instruction priority:
- User intent summary: {user_instruction.get("user_intent_summary") or ""}
- Explicit priorities: {explicit_priorities}
- Explicit prohibitions: {explicit_prohibitions}
- Agent may also improve unspecified aspects conservatively: {agent_freedom}
- Warning/conflicts: {conflicts} {warning}

The user's explicit instruction is the top editing goal. Agent aesthetic improvements are allowed only when they support or do not conflict with the user's request."""

    style_lines = [f"- {short_label}: {description}" for short_label, description in style_option_entries(analysis)]

    has_style_overrides = bool(style_lines)
    style_options = analysis.get("style_options") or {}
    size_override_value = style_options.get("size")
    has_pose_override = bool(style_options.get("pose") and style_options.get("pose") != "default")
    angle_override_value = style_options.get("angle")
    has_vertical_angle_override = angle_override_value in {"high", "low", "eye"}
    has_size_override = bool(size_override_value and size_override_value != "default")
    has_wider_size_override = size_override_value in {"wide", "wider"}
    has_closer_size_override = size_override_value in {"close", "closer"}

    selected_lighting_style = style_options.get("lighting")
    light_color_target_block = build_reviewed_light_color_target(
        analysis,
        fallback_intent=preset.get("intent", ""),
        lighting_style=(
            selected_lighting_style
            if selected_lighting_style and selected_lighting_style != "default"
            else None
        ),
    )

    size_ratio_block = ""
    if size_override_value == "wide":
        size_ratio_block = (
            "Quantified wide-framing target: make the main subject's visible frame ratio about 20% smaller than in the original image. "
            "Aim for the subject's bounding-box height or dominant dimension to be roughly 80% of its original frame-relative size; "
            "its area share may become roughly 65% of the original. "
        )
    elif size_override_value == "wider":
        size_ratio_block = (
            "Quantified wider-framing target: make the main subject's visible frame ratio about 40% smaller than in the original image. "
            "Aim for the subject's bounding-box height or dominant dimension to be roughly 60% of its original frame-relative size; "
            "its area share may become roughly 35% to 40% of the original. "
        )

    # The "original subject extent estimate: width=.., height=.., area=.." that
    # used to be appended here was read straight off subject_extent_box/roi_box --
    # the same estimated geometry that misses the subject. Nothing replaces it:
    # the targets above are already stated relative to the original image, which
    # the model can see, and size_ratio_block is interpolated three times, so any
    # sentence added here costs triple.

    style_block = ""
    if has_style_overrides:
        angle_reshoot_line = (
            "For a selected high, low, or eye-level angle, keep this same photograph and its composition and change only "
            "the height it is seen from, so it reads as the very same shot viewed from higher or lower, not a minor perspective tweak. "
            if has_vertical_angle_override else
            "For a selected angle override, the output should look like a new photograph taken from the selected camera angle, "
            "not like a minor perspective correction. "
        )
        style_block = (
            "User-selected style overrides are mandatory same-scene reshoot requirements, not optional retouch suggestions.\n"
            "Apply every selected item visibly and deliberately. When an override conflicts with default preservation of pose, "
            "camera angle, lighting, or framing size, the override wins for that selected aspect.\n"
            + angle_reshoot_line
            + "For selected pose, lighting, or framing-size overrides, make the change "
            "clearly visible while keeping it photographically believable.\n"
            + "\n".join(style_lines)
            + (
                "\n\nKeep the result faithful as a reshoot of the same original scene: same subject identity, same location, "
                "same environment, same clothing, and same props. Do not change unselected aspects, add new people, invent new props, "
                "or replace the scene."
            )
        )
        if has_pose_override:
            style_block += (
                "\n\nPose override requirement: Do not preserve the original pose. "
                "Replace the original body language with a visibly different pose matching the selected pose style. "
                "The change must affect posture, gesture, hand placement, body direction, weight shift, or head direction. "
                "A result with only lighting, color, skin, or minor expression changes and no clear pose change is a failure."
            )
        if has_vertical_angle_override:
            style_block += (
                "\n\nAngle override requirement (vertical only): Change only the height the shot is seen from, keeping the same horizontal camera direction. "
                "Every background and scene element stays in its original place, on the same side of the frame as before, just seen from higher or lower, "
                "and the subject stays in roughly the same part of the frame (any selected pose change still applies to the subject's own body language). "
                "The result should stay recognizable as the very same framing, only tilted up or down. "
                "A result where background elements move to the opposite side, or that looks taken from a different horizontal direction, fails this requirement."
            )
        if has_wider_size_override:
            style_block += (
                "\n\nFraming-size override requirement: This is a true wider-framing transformation. "
                + size_ratio_block +
                "Do not preserve the original image boundaries. Make the subject visibly smaller in the frame and show more surrounding same-scene environment. "
                "If the original image lacks enough context, extend/outpaint the canvas with plausible continuation of the same location, lighting, perspective, depth, and materials. "
                "Expanded areas must be photographic scene content, not blank borders, stretched edges, duplicated patterns, or unrelated invented scenery. "
                "Wide and wider must be visibly different: wide is a moderate pull-back, wider is a much stronger environmental pull-back."
            )
        elif has_closer_size_override:
            style_block += (
                "\n\nFraming-size override requirement: This is a true closer-framing transformation. "
                "Make the subject visibly larger in the frame while preserving essential identity, object form, role cues, and clean breathing room."
            )

    correction_plan_block = ""
    if analysis.get("correction_plan"):
        correction_plan_block = f"""User-approved correction plan (highest priority -- this list has already been reviewed and confirmed by the user; apply every item below and do not add corrective or stylistic changes beyond this list, the style overrides above, and the user's extra direction, even if other analysis notes above suggest more):
{correction_plan}

Approved-repair override:
- Items in this correction plan are commands, not optional suggestions.
- The Finished light and color target above and this reviewed correction plan form one effective target. User-preserved choices override the original analyzer target for their named regions and properties.
- Generic preservation, faithful-retouch, and minimal-change instructions must not cancel any item in this correction plan.
- For an approved gaze repair, the editing method is defined by the analysis output itself: the matching correction_plan item, correction_inverse item, retouch_recipe item, and any per-subject gaze_repair_strategy.
- Do not substitute a different generic gaze-repair method here. If the analysis explicitly allows local eye-direction change and/or local head/face alignment for a named subject, that allowance wins for that subject only."""

    rejected_corrections = analysis.get("rejected_corrections") or []
    negative_block = build_user_preserved_choices(analysis)

    # Skin is the one color in a photo that viewers have an absolute reference
    # for, so a global grade that reads fine on the background can still fail on
    # skin.
    has_person = _has_person(analysis)
    blink_block = ""
    blink_disabled_indices = _disabled_issue_subject_indices(analysis, "blink_issue")
    resolved_blink_issues = [resolve_user_blink_request(item) for item in eye_blink_issues]
    blink_items_to_fix = [
        {k: v for k, v in item.items() if k != "_subject_index"}
        for item in resolved_blink_issues
        if isinstance(item, dict) and (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in blink_disabled_indices
    ] if eye_closed_detection_enabled else []
    # blink_repair_strategy otherwise only exists on luna's
    # _BLINK_DETECTION_LUNA_SCHEMA (derive_luna_blink_decisions always sets it
    # once Call B has run) -- a non-luna model's blink_issue never carries
    # this key on its own, so absent a forced row this stays False there and
    # the generic template below is byte-for-byte unchanged. A user-forced
    # row (resolve_user_blink_request, above) always carries the key, on any
    # model, since it is the one place asking for it regardless of analysis.
    blink_has_repair_strategy = any(
        isinstance(item.get("blink_repair_strategy"), dict) for item in blink_items_to_fix
    )
    if blink_items_to_fix and blink_has_repair_strategy:
        blink_targets = []
        for item in blink_items_to_fix:
            label = item.get("subject_label", "unnamed subject")
            box = item.get("box") or {}
            eye_state = item.get("eye_state", "unknown")
            strategy = item.get("blink_repair_strategy") or {}
            edit_instruction = strategy.get("edit_instruction") if isinstance(strategy, dict) else ""
            target_eye_state = strategy.get("target_eye_state", "") if isinstance(strategy, dict) else ""
            eyelid_adjustment = strategy.get("eyelid_adjustment", "") if isinstance(strategy, dict) else ""
            repair_strength = strategy.get("repair_strength", "") if isinstance(strategy, dict) else ""
            success_condition = strategy.get("success_condition", "") if isinstance(strategy, dict) else ""
            preserve = strategy.get("preserve_during_repair") if isinstance(strategy, dict) else None
            avoid = strategy.get("failure_to_avoid") if isinstance(strategy, dict) else None
            photograph_states = {
                "photograph-left": item.get("left_eye_state"),
                "photograph-right": item.get("right_eye_state"),
            }
            source_state_text = "; ".join(
                f"{side}={state or 'unclear'}" for side, state in photograph_states.items()
            )
            fully_closed_sides = [
                side for side, state in photograph_states.items() if state == "fully_closed"
            ]
            match_requirement = (
                "Follow the analysis-authored Target eye state and Eyelid adjustment below as the final-state authority."
            )
            paired_requirement = (
                "Both eyes are blink-affected, including one that is only narrowed or half closed: open them together "
                "to the single naturally open final eyelid state named by the analysis. Both upper lids gain "
                "aperture from the photograph-side source states above -- an eye left at its captured narrowed or "
                "half-closed opening fails this repair. Their final eye-opening height, iris exposure, lid "
                "contours, double-eyelid fold shape, and visible crease-to-lash band width must match. "
                + FOLD_MATCH_RULE
            )
            if len(fully_closed_sides) == 1:
                closed_side = fully_closed_sides[0]
                reference_side = "photograph-right" if closed_side == "photograph-left" else "photograph-left"
                reference_state = photograph_states[reference_side]
                if reference_state == "open":
                    match_requirement = (
                        f"Use the {reference_side} (open) eye as the reference. The repaired {closed_side} "
                        "eye must match that reference eye's final eye-opening height, iris exposure, lid contours, "
                        "and visible double-eyelid crease-to-lash band width. If another approved edit changes the "
                        "reference eye, match its finished result rather than its captured closure amount."
                    )
                    repair_strength = "strong"
                elif reference_state in {"half_closed", "narrowed_open"}:
                    match_requirement = paired_requirement
                    # Older saved Call-C plans often call this blink-affected
                    # eye a "source reference".  Do not forward that stale
                    # method beside the deterministic rule that explicitly
                    # says neither captured aperture is the endpoint.
                    eyelid_adjustment = "Use the coordinated paired-eye method in the mandatory match below."
                    repair_strength = "strong"
            elif (
                all(state in {"fully_closed", "half_closed", "narrowed_open"} for state in photograph_states.values())
                and any(state in {"fully_closed", "half_closed"} for state in photograph_states.values())
            ):
                match_requirement = paired_requirement
                eyelid_adjustment = "Use the coordinated paired-eye method in the mandatory match below."
                repair_strength = "strong"
            blink_targets.append(
                f'- Subject: {label}\n'
                f'  Subject box: {json.dumps(box, ensure_ascii=False)}\n'
                f'  Current eye state: {eye_state}\n'
                f'  Photograph-side source states: {source_state_text}\n'
                f'  Analysis edit instruction: {edit_instruction or "Naturally reopen or relax the closed/half-closed eyes to match the existing expression."}\n'
                f'  Target eye state: {target_eye_state or "open, matching the existing expression"}\n'
                f'  Eyelid adjustment: {eyelid_adjustment or "reopen or relax the eyelids naturally"}\n'
                f'  Mandatory photograph-side match: {match_requirement}\n'
                f'  Repair strength: {repair_strength or "moderate"}\n'
                f'  Success condition: {success_condition or "eyes read naturally open at normal photo-viewing size"}\n'
                f'  Preserve during repair: {json.dumps(preserve or [], ensure_ascii=False)}\n'
                f'  Avoid during repair: {json.dumps(avoid or [], ensure_ascii=False)}'
            )
        blink_targets_block = "\n".join(blink_targets)
        blink_block = """Accidental blink repair priority:
- The analysis found one or more people whose eyes appear accidentally closed or half-closed at the capture moment under an expression-intent failure-detection rule.
- Scope: this block governs eyelid state only -- whether an eye reads open, closed, or half-closed. Where an open eye's pupil/iris points is a different property, governed separately by the gaze-repair instructions elsewhere in this prompt when present; they may still approve redirecting a person's pupils/iris even though that person is not named below.
- Work only on the eyelid state of the person(s) named below. Every other visible person's eyelid state, and everything else about the named person(s) besides that eyelid state, come through exactly as captured.
- Execute the approved repair using only the execution instructions below. Do not read confidence scores or reasons to decide whether to be more conservative.
- The analysis-authored Target eye state and Eyelid adjustment below are the final-state authority and must reach the result. When exactly one photograph-side eye is fully closed and the opposite eye is open, repair the fully-closed eye to match the opposite eye's final eye-opening height, iris exposure, lid contours, and visible double-eyelid crease-to-lash band width; if another approved edit changes that reference eye, match its finished result rather than its captured closure amount. When the opposite eye is itself half-closed or narrowed, it is blink-affected too: open both eyes together to one naturally open final state, and match them to each other on those same properties instead of reproducing either captured aperture. Treat eye-opening height and double-eyelid band width as separate mandatory matches. Compare final size and shape in face-relative geometry, not by duplicating pixels: preserve fixed canthi and horizontal span, match aperture-height-to-eye-width ratio and the upper/lower-lid profile at the inner, central, and outer thirds, and allow only head-perspective differences. Both eyes must also finish with the same double-eyelid fold shape, not only the same band width: the same fold type, the crease visible along the same portion of the lid, the same crease path from inner to outer corner, and the same fold arc and peak height. Before finishing, inspect both completed eyes together and refine both as one pair if aperture, three-point contour, iris exposure, or fold path still differs. Open by moving the upper-lid margin over the existing globe and resolving closed-lid tissue into a smooth contour; do not retain a closed-lid mound or add puffiness, swelling, or bulging skin.
- When the same named person also has an approved gaze repair in this edit call, execute the eyelid opening and final gaze/head-face alignment as one coherent local eye reconstruction. Do not first invent one reopened-eye gaze and then replace it with another; render the final approved eyelid openness, pupil/iris direction, sclera balance, catchlights, and head/face relationship together.
- Never convert an intentional wink, laugh-squint, crying/pained/dramatic closed-eye reaction, expressive closed-eye reaction, sleeping/napping person's closed eyes, or deliberate closed-eye expression into neutral open eyes -- including for people not named below, who are not part of this edit at all.

Execution instructions:
""" + blink_targets_block
    elif blink_items_to_fix:
        blink_block = """Accidental blink repair priority:
- The analysis found one or more people whose eyes appear accidentally closed or half-closed at the capture moment under an expression-intent failure-detection rule.
- Scope: this block governs eyelid state only -- whether an eye reads open, closed, or half-closed. Where an open eye's pupil/iris points is a different property, governed separately by the gaze-repair instructions elsewhere in this prompt when present; they may still approve redirecting a person's pupils/iris even though that person is not named below.
- Work only on the eyelid state of the person(s) named below. Every other visible person's eyelid state, and everything else about the named person(s) besides that eyelid state, come through exactly as captured.
- For each named person, obey the photograph-left and photograph-right source states carried in the item. When exactly one side is fully closed and the opposite side is open, make the fully-closed side match the opposite side's final eye-opening height, iris exposure, lid contours, and visible double-eyelid crease-to-lash band width. Otherwise, including when the opposite side is itself half-closed or narrowed, finish both blink-affected eyes naturally open at one coherent eyelid endpoint matched on those same properties. Treat eye-opening height and double-eyelid band width as separate mandatory matches. Compare final size and shape in face-relative geometry, not by duplicating pixels: preserve fixed canthi and horizontal span, match aperture-height-to-eye-width ratio and the upper/lower-lid profile at the inner, central, and outer thirds, and allow only head-perspective differences. Both eyes must also finish with the same double-eyelid fold shape, not only the same band width: the same fold type, the crease visible along the same portion of the lid, the same crease path from inner to outer corner, and the same fold arc and peak height. Before finishing, inspect both completed eyes together and refine both as one pair if aperture, three-point contour, iris exposure, or fold path still differs. Open by moving the upper-lid margin over the existing globe and resolving closed-lid tissue into a smooth contour without puffiness or added eyelid volume.
- Render realistic eyelids, iris, pupil, catchlights, and eyelashes, with gaze direction matching the person's head angle and the photo's lighting -- unless a separately approved gaze repair for this same person specifies a different target direction, in which case follow that instead. Do not reshape the face, smooth the skin, or otherwise beautify the person while doing this.
- When the same named person also has an approved gaze repair in this edit call, execute the eyelid opening and final gaze/head-face alignment as one coherent local eye reconstruction. Do not first invent one reopened-eye gaze and then replace it with another; render the final approved eyelid openness, pupil/iris direction, sclera balance, catchlights, and head/face relationship together.
- Never convert an intentional wink, laugh-squint, crying/pained/dramatic closed-eye reaction, expressive closed-eye reaction, sleeping/napping person's closed eyes, or deliberate closed-eye expression into neutral open eyes -- including for people not named below, who are not part of this edit at all.
- Eyes to work on: """ + json.dumps(blink_items_to_fix, ensure_ascii=False)
    else:
        # Mirrors gaze_block's own empty-case fallback below: the notes above
        # (aesthetic_diagnosis, detected_issues, edit_philosophy, edit_intent)
        # can still say a subject's eyes are closed or half-closed -- and can
        # phrase it as an instruction already decided ("修復閉眼問題" / "repair
        # the closed-eye issue"), not just a diagnosis -- even when there is
        # no approved repair, whether because eye_closed_detection is off or
        # because nothing here needed fixing. "highest priority" and the named
        # fields match negative_block's own phrasing below on purpose: this is
        # the same kind of override, so it needs the same declared weight to
        # outargue directive-sounding prose instead of merely descriptive prose.
        blink_block = """Eye-state preservation instruction (highest priority):
- No accidental-blink or closed-eye repair is approved for this image.
- This overrides every place above that still argues for one -- the aesthetic diagnosis, the detected issues, the edit philosophy, and the edit intent were all written before this decision and were not updated by it, even where they phrase it as an instruction already decided ("修復閉眼問題" / "repair the closed eyes") rather than a diagnosis.
- Scope: this instruction governs eyelid state only -- whether an eye reads open, closed, or half-closed. It does not govern where an open eye's pupil/iris points; that is a different property, governed separately by the gaze-repair instructions elsewhere in this prompt, which may still approve moving a person's pupils/iris even though no blink repair is approved for anyone.
- Do not reopen, relax, or enlarge any person's closed or half-closed eyelids on the strength of any of that text alone.
- Preserve every visible person's eyelid state exactly as captured, unless a person is explicitly named above under "Eyes to work on"."""

    # Gated on the user's recipe choice as well as on the analysis. hair_issues
    # still says should_fix=true after the user unchecks the hair item -- it is
    # analysis output and the review screen never rewrites it -- so without this
    # the block would hand the edit model hair to tidy while the rejected-
    # corrections block above tells it to leave that same hair alone.
    #
    # Reads the raw axis tags rather than resolve_rejected_axes on purpose: that
    # function returns an empty list whenever the tags look untrustworthy, which
    # for a color clause only costs some reinforcement, but here would emit the
    # contradiction. Being wrong in this direction instead leaves hair untouched,
    # which is what the unchecked item asked for.
    hair_rejected = (
        "hair" in _axis_set(analysis.get("rejected_axes"))
        and "hair" not in _axis_set(analysis.get("correction_axes"))
    )
    # The selected level is resolved per approved detected issue below. It is
    # never an independent licence to groom an unflagged person's hair.
    # Only automatically approved or explicitly user-enabled items are handed
    # over. Manual items are converted to the same small action vocabulary and
    # preservation contract as detected repairs.
    hair_block = ""
    hair_disabled_indices = _disabled_issue_subject_indices(analysis, "hair_issue")
    resolved_hair_issues = [resolve_user_hair_request(item) for item in hair_issues]
    hair_items_to_fix = [
        {k: v for k, v in item.items() if k != "_subject_index"}
        for item in resolved_hair_issues
        if isinstance(item, dict) and (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in hair_disabled_indices
    ] if hair_mess_detection_enabled else []
    calm_hair_items = [
        {k: v for k, v in item.items() if k != "_subject_index"}
        for item in resolved_hair_issues
        if isinstance(item, dict) and (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in hair_disabled_indices
        and (item.get("_user_level") == "tidy" or (
            item.get("_user_level") not in {"dynamic", "tidy"}
            and advanced_options.get("calm_hair")
        ))
    ] if hair_mess_detection_enabled and not hair_rejected else []
    calm_hair_active = bool(calm_hair_items)
    if hair_items_to_fix and not hair_rejected:
        hair_block = """Hair tidying priority:
- Work only on the hair named below, on the person named below. Every other part of that person's hair, and everyone else's hair, comes through exactly as captured.
- Each item names its action, and only four exist. Each is a picture of how that hair ends up, and the picture is what you render towards. lay_back: the named strands lie along the flow of the hair beside them, moved rather than drawn. remove: they are gone, and the skin, feature, or background behind them continues unbroken through its own texture, tone and grain. settle: the hair hangs in the hairstyle's own shape, its outline continuous. redistribute: the fringe reads as one even band. Everything below bounds how you reach that picture; none of it is the goal, and a result that breaks none of these limits while leaving the hair as it was has not done this edit.
- For lay_back and remove, a strand is a thin line, so the work is a thin line: continue what the photograph already shows on either side of it, straight across, and change nothing outside its own footprint. Where a strand crosses an eye, brow, lash line, nostril, or lip, that feature is almost entirely visible around the strand -- every visible part of it stays pixel-faithful and is what defines the sliver underneath. Do not re-render the eye, nose, or mouth region, do not re-place or re-shape the feature, and do not improve it: the same eye shape, the same lash line, the same iris and catchlight position, the same lip edge and volume as the original. A re-rendered feature is a different person, which is far worse than the strand you were asked to move.
- settle works across a whole disturbed area rather than one strand, and this is the only place that wider scope is licensed. Its target comes from the same head: the part the wind did not disturb shows the length, parting, volume, direction, and silhouette that the lifted, opened, or sideways-blown area is combed back into, so the outline reads continuous again. Lower the raised hair back onto the head and cover the gaps the wind opened with the hair that is already there. Give the area no more length, volume, or coverage than the rest of the head has, and leave the person's hairline where it is. Everything outside the disturbed area, face included, still comes through as captured.
- redistribute is a clumped fringe combed out, and it is two operations, both required, across the whole fringe rather than on one strand. First, de-grease it: the oily specular streak goes matte, down to the sheen the rest of that person's hair has, with per-strand detail visible again inside where the shine was -- and never by darkening, flattening, or blurring the fringe. Second, comb it out: the thick clumps separate into a curtain of even width, and the strands that formed those clumps now span the bare gaps, so no bald patch of forehead is left between them.
- Combing hair across a gap is the operation, not a violation of "add no hair". Fringe hair ends up where forehead skin used to show -- that is what closing a gap means, and it is required. What is forbidden is a fringe with MORE hair in it than the original had: no extra density, no greater length, no lowered hairline, no widened fringe, and no second layer invented behind the first. The same hair, spread evenly, covers more area than the same hair bunched into cords; that redistribution is the whole edit.
- A fringe comes out as one even band, whichever action was named for it. What disappears is the uneven thick-cord-beside-bald-gap pattern, never the fringe's own transparency -- a real fringe shows some forehead through it -- and its length, outer edge, parting, and direction are unchanged. Taking strands out of a fringe builds that same pattern from the other direction and fails the same way.
- Settled is not sculpted, and neither is an evened-out fringe. Combed-down real hair still shows strand direction, slight separation, its own unevenness, and how it catches light. A settled area that reads as one glossy sheet, a painted block, a wig, or a helmet fails this edit even where every trace of the wind is gone -- that failure is likelier here than anywhere else in this instruction, because a smooth mass is the easiest way to look tidy.
- remove takes away hair that has left the hairstyle, never hair the hairstyle puts where it is. However an item is worded, taking hair off must not uncover forehead or scalp that this person's hair covers in the original: the hairline stays exactly where it is, a fringe keeps its coverage and its outer edge, and no more forehead is visible at the end than at the start. If carrying out a remove item would expose more forehead, it is the wrong operation on that hair -- lay it back or even it out instead, and if neither is possible leave it. An instruction to clear hair off a forehead is never an instruction to remove the fringe.
- No action has a partial version. Do not fade, blur, soften, desaturate, or half-erase hair: a ghosted, greyed, or smeared remnant reads as damage, where the same strand cleanly gone reads as a better photograph. What replaces a removed strand leaves no dark trace, no blur patch, and no smoothed plastic area.
- Identical to the original afterwards: hairstyle shape and silhouette, parting, length, volume, density, hairline position, curl pattern, color, roots, and how the hair meets the scalp and ears -- with individual strands still resolvable and the hair's own irregular texture intact. The amount of hair never increases anywhere. For settle and redistribute, "unchanged" means the hairstyle the settled or un-clumped hair shows, not the wind-blown or corded arrangement it is being brought back from -- both of them move existing hair into places it was not covering, which is their purpose and not an addition.
- Do not tidy the face/skin/expression along with the hair, tuck a fallen lock back into an updo, ponytail, braid, or clip, or close a parted fringe. Where the named hair cannot be laid back, cleanly taken off, or settled within these limits, leave it exactly as captured -- that is the correct outcome here, not a missed instruction.
- Hair to work on: """ + json.dumps(hair_items_to_fix, ensure_ascii=False)
    elif not calm_hair_active:
        # Mirrors gaze_block's own empty-case fallback below: the notes above
        # (aesthetic_diagnosis, detected_issues, edit_philosophy, edit_intent)
        # can still call a subject's hair messy, windblown, or clumped -- and
        # can phrase it as an instruction already decided ("修復髮絲" / "fix the
        # hair"), not just a diagnosis -- even when there is no approved
        # repair, whether because hair_mess_detection is off, the user
        # rejected the item on review, or nothing here needed fixing.
        # "highest priority" and the named fields match negative_block's own
        # phrasing below on purpose: same kind of override, same declared weight.
        hair_block = """Hair preservation instruction (highest priority):
- No hair tidying is approved for this image.
- This overrides every place above that still argues for one -- the aesthetic diagnosis, the detected issues, the edit philosophy, and the edit intent were all written before this decision and were not updated by it, even where they phrase it as an instruction already decided ("修復髮絲" / "fix the hair") rather than a diagnosis.
- Do not lay back, remove, settle, or redistribute any strand of hair on the strength of any of that text alone.
- Preserve every visible person's hair exactly as captured: style, strands, flyaways, and all, unless hair is explicitly named above under "Hair to work on"."""

    # A user-elected transformation, so it is written with the same force as a
    # style override rather than as a defect repair: unconditional, and stated as
    # the outcome to reach instead of as a permission to tidy. The detection
    # blocks above hedge on purpose -- an unrequested hair edit is a risk -- but
    # hedging is wrong here, since the user asked for exactly this.
    calm_hair_block = ""
    if calm_hair_active:
        calm_hair_block = """Still-air hair (user-selected, mandatory):
- Fully groom the entire visible hairstyle of each person named below, not merely the originally detected defect footprint. Other people and unapproved hair remain exactly as captured.
- For each named person, this full-hairstyle scope overrides the earlier local-only hair boundary: the detected item identifies whom to groom, not the maximum edit footprint.
- Mandatory visible change: the result must look clearly tidier at normal viewing size. An unchanged result or a barely perceptible cleanup fails this user request.
- Comb loose, lifted, crossed, uneven, and directionless strands into clean coherent sections following the existing haircut. Neaten the fringe, part, surface flow, and outer silhouette so the finished hair looks deliberately combed and portrait-ready.
- Bring lifted, fanned, streaming, and sideways-blown hair back down onto the head and shoulders. Close the gaps the wind opened, using the hair that is already in the photograph. No wind-driven or body-motion disorder remains in the finished hairstyle.
- Same person, same haircut: length, volume, density, parting, hairline, curl pattern, and color are unchanged, no hair is added, and the face is untouched. Hair falls, but its owner does not change.
- Settled is not sculpted. Hair hanging still keeps strand separation, its own unevenness, and the way it catches light. A smooth glossy sheet, a painted block, or a wig fails this even though the wind is gone.
- Everything else in the photograph stays exactly as captured -- the same clothing, the same flags, leaves, water, and other people, still showing whatever the weather was doing to them. This settles hair only; it does not calm the scene.
- Detected hair to tidy: """ + json.dumps(calm_hair_items, ensure_ascii=False)

    gaze_disabled_indices = _disabled_issue_subject_indices(analysis, "gaze_issue")
    resolved_gaze_issues = [resolve_user_gaze_request(item) for item in gaze_contact_issues]
    gaze_items_to_fix = [
        item for item in resolved_gaze_issues
        if isinstance(item, dict) and (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in gaze_disabled_indices
    ] if gaze_contact_detection_enabled else []
    gaze_block = ""
    # Two-stage flow (single_pass_edit=False): any approved gaze
    # repair is 100% Step 5b's job (pipeline._run_gaze_repair_pass calls
    # build_gaze_only_edit_prompt as an isolated second pass on this call's own
    # output), so this call must leave gaze untouched even when a fix is
    # approved -- attempting it here too would let this pass's own no-op or
    # partial attempt slip through as the final result if Step 5b is ever
    # skipped/best-effort-fails, and gives no benefit since Step 5b always
    # re-examines and re-edits the whole image regardless of what this pass
    # did. single_pass_edit=True has no Step 5b to defer to, so it keeps the
    # original bundled-attempt behavior below.
    if gaze_items_to_fix and not single_pass_edit:
        gaze_block = """Gaze preservation instruction (highest priority):
- Do not perform any gaze/eye-direction correction in this pass, even though the analysis approved one for a named subject below -- that repair is deliberately deferred to a separate, later edit pass and is completely out of scope for this pass.
- This overrides every place above that still argues for a gaze change now -- the aesthetic diagnosis, the detected issues, the edit philosophy, the edit intent, and correction_plan were all written to describe the approved fix, not to authorize performing it in this pass.
- Scope: this instruction governs where an open eye's pupil/iris points -- eye direction, eye focus, pupil/iris placement, sclera balance, and catchlights. It does not govern whether an eye is open or closed; that is a different property, governed separately by the blink-repair instructions elsewhere in this prompt, which may still approve reopening a person's closed or half-closed eyelids even though gaze redirection is out of scope for this pass.
- Preserve the original eye direction, eye focus, pupil/iris placement, sclera balance, catchlights, and expression exactly as captured, for every subject, including the one(s) named below.
- Do not redirect anyone's gaze toward or away from the camera in this pass.
"""
    elif gaze_items_to_fix:
        gaze_targets = []
        for item in gaze_items_to_fix:
            label = item.get("subject_label", "unnamed subject")
            box = item.get("box") or {}
            gaze_state = item.get("gaze_state", "unknown")
            lens_decision = item.get("lens_contact_decision", "not_looking_at_lens")
            strategy = item.get("gaze_repair_strategy") or {}
            edit_instruction = strategy.get("edit_instruction") if isinstance(strategy, dict) else ""
            strategy_type = strategy.get("strategy_type", "") if isinstance(strategy, dict) else ""
            repair_strength = strategy.get("repair_strength", "") if isinstance(strategy, dict) else ""
            preserve = strategy.get("preserve_during_repair") if isinstance(strategy, dict) else None
            avoid = strategy.get("failure_to_avoid") if isinstance(strategy, dict) else None
            head_face = strategy.get("head_face_adjustment", "") if isinstance(strategy, dict) else ""
            eye_adjustment = strategy.get("eye_adjustment", "") if isinstance(strategy, dict) else ""
            sclera_catchlight = strategy.get("sclera_catchlight_adjustment", "") if isinstance(strategy, dict) else ""
            target_read = strategy.get("target_gaze_read", "") if isinstance(strategy, dict) else ""
            gaze_targets.append(
                f'- Subject: {label}\n'
                f'  Subject box: {json.dumps(box, ensure_ascii=False)}\n'
                f'  Required endpoint: redirect gaze from {gaze_state} to direct_camera.\n'
                f'  Analysis edit instruction: {edit_instruction or "Use the matching correction_plan item as the authoritative gaze repair instruction."}\n'
                f'  Strategy type: {strategy_type or "specified in correction_plan"}\n'
                f'  Repair strength: {repair_strength or "specified in correction_plan"}\n'
                f'  Eye adjustment: {eye_adjustment or "specified in correction_plan"}\n'
                f'  Head/face adjustment: {head_face or "specified in correction_plan"}\n'
                f'  Sclera/catchlight adjustment: {sclera_catchlight or "preserve naturally unless correction_plan says otherwise"}\n'
                f'  Target viewer read: {target_read or "direct camera-lens contact"}\n'
                f'  Preserve during repair: {json.dumps(preserve or [], ensure_ascii=False)}\n'
                f'  Avoid during repair: {json.dumps(avoid or [], ensure_ascii=False)}'
            )
        gaze_targets_block = "\n".join(gaze_targets)

        gaze_block = """Approved gaze repair execution instructions:
- Approved gaze repair exists for the subjects listed below.
- Do not diagnose gaze again in the edit stage.
- Do not read confidence scores, reasons, or analysis records to decide whether to be more conservative.
- Execute the approved repair using only the execution instructions below plus the matching correction_plan and correction_inverse.
- Generic preservation, faithful-retouch, and minimal-change instructions must not block any local eye-direction, eye-focus, sclera-balance, catchlight, or head/face-alignment changes that the execution instructions explicitly approve for the named subject.
- Do not broaden the edit beyond what is approved below. Apply only the subject-local changes explicitly described there, while preserving all unrelated identity, expression, body pose, clothing, object interaction, scene, and background details. Any person in the frame who is not named in the execution instructions below is completely out of scope: do not touch their eyes, gaze, expression, pose, or anything else about them.
- If a named subject has an approved gaze repair, that repair is required; do not leave the original gaze unchanged.

Execution instructions:
""" + gaze_targets_block + """

Execution rule:
- Follow the Analysis edit instruction for each subject literally.
- If Strategy type is eyes_only, keep the head/face angle unchanged. This method is valid only for an extremely small miss when the head and face are already naturally aligned to the current lens.
- If Strategy type is eyes_plus_head_face_alignment, the described head/face adjustment is mandatory, not optional. Visibly but naturally turn and/or tilt the head/face toward the exact current lens while keeping the torso, shoulders, body pose, and framing unchanged. A pupil-only change with the original incompatible head direction is a failed execution.
- Use the listed Repair strength for the named subject.
- Preserve everything listed in Preserve during repair and in the matching correction_inverse item.
- Avoid every failure mode listed in Avoid during repair.
- Do not weaken a moderate or strong instruction into a subtle eye nudge.
- The completed gaze correction must be immediately readable at normal photo-viewing size; a change visible only in a highly enlarged face crop is insufficient.
"""
    else:
        # Same "highest priority" + named-fields treatment as blink_block's
        # and hair_block's own empty-case fallbacks above, for the same
        # reason: aesthetic_diagnosis/detected_issues/edit_philosophy/
        # edit_intent can phrase an unapproved gaze fix as a decision already
        # made ("修復眼神" / "fix the gaze"), which needs to be named and
        # outranked here rather than left for a bare "not approved" to out-
        # argue.
        gaze_block = """Gaze preservation instruction (highest priority):
- No gaze correction is approved for this image.
- This overrides every place above that still argues for one -- the aesthetic diagnosis, the detected issues, the edit philosophy, and the edit intent were all written before this decision and were not updated by it, even where they phrase it as an instruction already decided ("修復眼神" / "fix the gaze") rather than a diagnosis.
- Scope: this instruction governs where an open eye's pupil/iris points -- eye direction, eye focus, pupil/iris placement, sclera balance, and catchlights. It does not govern whether an eye is open or closed; that is a different property, governed separately by the blink-repair instructions elsewhere in this prompt, which may still approve reopening a person's closed or half-closed eyelids even though no gaze correction is approved for anyone.
- Do not redirect anyone's gaze toward the camera.
- Preserve the original eye direction, eye focus, pupil/iris placement, sclera balance, and catchlights.
- If a subject is looking off-camera, near-camera, upward, downward, unfocused, or away from the lens, keep that original gaze unchanged unless a future approved gaze repair item explicitly requests a change.
- Do not invent any gaze-repair method unless the analysis explicitly approved one.
"""

    smile_block = build_smile_block(analysis)

    # The skin block's opening rule points at these; without them it names an
    # identity the edit model was never told, so the generic line stands in.
    skin_block = ""
    if has_person:
        # As with hair, use the raw axis tags: when the user rejects the only
        # skin correction, the generic skin guidance must not recreate it.
        skin_rejected = (
            "skin" in _axis_set(analysis.get("rejected_axes"))
            and "skin" not in _axis_set(analysis.get("correction_axes"))
        )
        skin_adjustment_rule = (
            "- Keep skin color as captured, allowing only its natural response to approved whole-scene exposure and illumination changes."
            if skin_rejected else
            "- Apply a local skin-color change only when the approved correction plan names an unwanted cast; stop at that person's recorded native complexion."
        )
        skin_block = f"""Skin in the finished photograph (this photo contains people):
{_build_skin_identity_lines(analysis)}
- Keep each person's native complexion depth, undertone, and chroma while skin follows the final shared exposure and actual illumination.
{skin_adjustment_rule}
- Apply scene white balance, hue, and saturation to the non-skin palette independently from complexion.
- Keep tone continuous across face, neck, ears, and visible hands, with natural texture and modelling."""

    revision_block = ""
    if revision_feedback:
        revision_block = f"""Revision feedback from the previous QA:
{revision_feedback}

This is a retry. Directly fix the QA failure. Do not repeat the failed crop/edit behavior. Preserve the subject, required framing, scene context, and protected details more conservatively than the previous output."""

    # Whether the Crop instruction below may offer extending the scene as an
    # alternative to cropping. Read here as well as inside the block so the
    # retouch priorities stop forbidding the very thing it offers.
    # Decided during analysis and carried on the analysis object; the flag
    # below is the older threshold-only permission, kept for an analysis that
    # predates the decision.
    expand_plan = (
        None if has_wider_size_override or has_closer_size_override
        else analysis.get("reframe_extension")
    )
    # Once the analysis has answered, the edit model is not asked again: an
    # analysis that chose "crop" must not also be handed the conditional
    # permission, which would reopen a decision already made -- and offering
    # that choice to this model was measured to change nothing anyway.
    mode_decided = bool(str(analysis.get("reframe_mode") or "").strip())
    allow_expand = not mode_decided and not has_wider_size_override and not has_closer_size_override and expand_permitted(
        aspect_ratio_pref,
        (crop_plan or {}).get("aspectRatio") or (crop_plan or {}).get("aspect_ratio"),
        source_size,
    )

    framing_focus = analysis.get("_framing_focus") or {}
    focus_block = ""
    if framing_focus.get("size") == "closer":
        focus_label = framing_focus.get("label") or "the single most prominent subject"
        focus_block = f"""Single-subject close-up override (highest framing priority):
Feature one subject: {focus_label}. Identify that subject in the photograph and frame the close-up around what you see there. The photo summary and scene description above may mention several people or elements -- for this close-up, every other person, object, or element left outside the frame is intentionally and correctly cropped out. Do NOT reintroduce them. Fill the frame with the chosen subject; preserve that subject's identity, expression, and key details."""

    if has_wider_size_override:
        # The consequence sentence used for a plain crop ("the sides cannot both
        # be inside this frame") is false here: this mode is allowed to outpaint,
        # so a differently-shaped canvas is reached by adding scene unevenly
        # rather than by giving anything up.
        outpaint_shape_line = {
            "narrower": "This frame is narrower than the photograph you were given, so take in more scene above and "
                        "below than at the sides to reach its shape. ",
            "wider": "This frame is wider than the photograph you were given, so take in more scene at the sides "
                     "than above and below to reach its shape. ",
        }.get(_canvas_shape_difference(
            (crop_plan or {}).get("aspectRatio") or (crop_plan or {}).get("aspect_ratio"), source_size
        ), "")
        crop_instruction_block = (
            "Wider framing requested. Do not treat the original image boundaries as final. "
            + size_ratio_block +
            "Create a wider same-scene composition where the subject appears smaller and more surrounding environment is visible. "
            "Decide the new frame from the photograph's own content and subject. "
            + outpaint_shape_line +
            "Canvas expansion/outpainting is allowed only to continue the same real scene with matching perspective, lighting, depth, materials, and background logic. "
            "Do not add blank borders, transparent/checkerboard padding, artificial empty bands, stretched edges, or unrelated scenery. "
            + UNIFORM_SCALE_ONLY + " " + _content_shape_rule(has_body_reshape)
        )
    else:
        crop_instruction_block = _build_crop_instruction_block(
            crop_plan, require_tighter=has_closer_size_override, source_size=source_size,
            has_body_reshape=has_body_reshape, allow_expand=allow_expand,
            expand_plan=expand_plan, target_ratio_label=rec_aspect,
        )
    crop_priority_line_style = (
        "Create the requested wider same-scene framing. The original image boundaries are not final for wide/wider size overrides; expand/outpaint plausible same-scene context if needed. "
        + size_ratio_block +
        "Wide should be a moderate pull-back; wider should be a much stronger pull-back with a clearly smaller subject and more environmental context. "
        "Never add blank borders, transparent/checkerboard padding, artificial empty side bands, stretched edges, or unrelated scenery."
        if has_wider_size_override else
        (
            "Reframe as described in the Crop instruction above, judging the frame's edges from the photograph's "
            "own content and subject rather than from stated coordinates. Beyond the frame you settle on, do not "
            "add blank borders, transparent/checkerboard padding, or artificial empty side bands"
            + (
                "; where that instruction allows the scene to be carried past the photograph's edge, what you add "
                "is continued scene, never padding."
                if allow_expand or expand_plan else
                ", unless a selected closer framing-size override explicitly asks for closer framing within it."
            )
            if crop_plan or expand_plan else
            "Treat the supplied image boundaries as final unless a selected closer framing-size override explicitly asks for closer framing. "
            "Never add blank borders, transparent/checkerboard padding, or artificial empty side bands."
        )
    )
    crop_priority_line_faithful = (
        "Reframe as described in the Crop instruction above, judging the frame's edges from the photograph's own "
        "content and subject rather than from stated coordinates. Beyond the frame you settle on, do not "
        + (
            "add borders, create transparent/checkerboard padding, or invent empty side bands; canvas beyond the "
            "photograph's edge is added only as the continued scene that instruction describes, and only as far as "
            "the output shape needs."
            if allow_expand or expand_plan else
            "additionally expand canvas, add borders, create transparent/checkerboard padding, or invent empty side bands."
        )
        if crop_plan or expand_plan else
        "Treat the supplied image boundaries as final. Do not re-crop, expand canvas, add borders, create "
        "transparent/checkerboard padding, or invent empty side bands."
    )

    # Two numbered priorities ask for exactly what a rejected `distraction` or
    # `crop` item asked for -- found in a real run whose negative block said
    # "leave every element as it is" three paragraphs above a priority saying
    # "reduce distractions ... subtle blur, or small cleanup". Drop the offending
    # clause rather than replace it with a prohibition: the scoped
    # correction_inverse line does the pinning, and a blanket "never reduce any
    # distraction" would over-suppress a rejection that was only about one corner
    # of the frame.
    distraction_priority = (
        "Reduce distractions locally through tonal control, local contrast, saturation, subtle blur, or small "
        "cleanup. Do not remove, replace, rebuild, or simplify contextual/neutral scene elements just to make "
        "the image cleaner."
        if "distraction" not in rejected_axes else
        "Do not remove, replace, rebuild, simplify, blur, or clean up scene elements. Distraction cleanup was "
        "removed from this edit's plan by the user; the scene's elements stay as the original presents them, "
        "except where an approved correction above asks otherwise."
    )
    alignment_approved = has_approved_alignment_correction(analysis)
    if alignment_approved:
        alignment_priority = (
            "Apply the separately approved whole-frame alignment correction subtly and completely. Change only "
            "the requested tilt, horizon/vertical alignment, or perspective; use only the minimal edge trim needed "
            "to remove exposed wedges, while retaining the selected aspect ratio and composition as closely as possible."
        )
    elif "alignment" in rejected_axes:
        alignment_priority = (
            "Preserve the original whole-frame alignment exactly. Do not level horizons, straighten verticals, "
            "correct tilt, or adjust perspective -- the user removed the independent alignment item from this edit's plan."
        )
    else:
        alignment_priority = (
            "No whole-frame alignment correction is approved. Preserve the original tilt, horizon/vertical alignment, "
            "and perspective; do not infer one from a crop/reframing item. A user-selected camera-angle style may still "
            "change viewpoint within its own explicit scope."
        )

    if has_style_overrides:
        opening_preservation_rule = (
            "Preserve the subject identity, recognizable person/object, clothing, location, environment, props, "
            "and photographic realism. This is a same-scene reshoot: selected pose, angle, lighting, or framing-size "
            "overrides are allowed and expected to visibly change those selected aspects."
        )
        edit_mode_block = f"""Edit mode:
STYLE OVERRIDE MODE. The user selected one or more pose/angle/lighting/framing options, so treat this as a controlled same-scene reshoot rather than a conservative retouch only.

Priority order:
1. Preserve safety, identity, same subject, same location, same environment, same clothing, same props, and photographic realism.
2. Apply user-selected style overrides visibly and deliberately for the selected aspects.
3. Apply {approved_edit_sources}. {repair_allowance} Generic preservation rules must yield only where the analysis explicitly permits the local change.
4. Use the preset style as a supporting finish.
5. Apply default preservation rules only to aspects not selected by the user."""
        retouch_priorities = f"""Retouch priorities:
1. {crop_priority_line_style}
2. Preserve the original subject identity, semantic subject extent, protected regions, role cues, product/object identity, scene structure, and believable spatial continuity. For selected style overrides, allow the corresponding pose, camera viewpoint, lighting direction, or framing size to change visibly while keeping the scene physically plausible.
3. Apply every user-selected style override in full. Angle overrides should clearly move the camera to the selected viewpoint by following that angle's own instruction above; pose overrides must replace the original pose with clearly different body language, changing posture, gesture, hand placement, body direction, weight shift, or head direction; lighting overrides should visibly change illumination mood; framing-size overrides should visibly change subject scale and environmental context. For wide/wider, follow the quantified subject-size target: wide should reduce the subject's visible frame ratio by about 20%, while wider should reduce it by about 40%. The scene must expand with plausible same-location context, not just a tiny crop or minor zoom change.
4. {color_craft_direction} Apply this only where it supports the selected overrides and the approved correction plan.
5. {distraction_priority}
6. Preserve natural texture in skin, hair, fabric, food, products, architecture, shadows, and background materials. Avoid plastic smoothing, synthetic sharpening, or heavy filters.
7. Keep unselected aspects faithful to the original. If angle is not selected, preserve the original camera viewpoint; if pose is not selected, preserve the original pose; if size is not selected, preserve the original framing size.
8. Keep the final result faithful to the same subject, identity, location, environment, clothing, props, and photographic realism. When an angle override is selected, change the camera viewpoint clearly as described in that angle's own instruction above, while keeping a believable same-scene result.
9. {definitive_edit_sources} are the definitive specification of what to change -- apply them in full and do not perform additional corrections beyond them. For the user-rejected corrections above, reproduce the original's version of those properties -- that is the required output, not merely a change you may skip."""
    else:
        opening_preservation_rule = (
            "Preserve the original subject, identity, pose, camera angle, scene, product shape, clothing, "
            + (
                "facial structure, every unrelated body area, and important details. Apply only the single holistic body-reshape instruction above."
                if has_body_reshape else
                "facial structure, body shape, and important details."
            )
        )
        edit_mode_block = f"""Edit mode:
FAITHFUL RETOUCH MODE. The user did not select any style override, so keep the original pose, camera viewpoint, lighting logic, framing size, scene, and moment. Improve only {faithful_edit_sources}. {faithful_repair_allowance}"""
        retouch_priorities = f"""Retouch priorities:
1. {crop_priority_line_faithful}
2. Preserve the original subject, semantic subject extent, protected regions, role cues, interaction points, product/object geometry, scene structure, perspective, and spatial relationships.
3. {color_craft_direction}
4. {distraction_priority}
5. Preserve natural texture in skin, hair, fabric, food, products, architecture, shadows, and background materials. Avoid plastic smoothing, synthetic sharpening, or heavy filters.
6. Respect the selected composition. Preserve meaningful breathing room and empty space when it supports direction, story, scale, calmness, graphic balance, rhythm, environment, or subject dignity.
7. {alignment_priority}
8. Keep the final result faithful: the same moment, same camera viewpoint, same environment, and same subject, finished like a skilled retoucher handled it.
9. {definitive_edit_sources_faithful} are the definitive specification of what to change -- apply them in full and do not perform additional corrections beyond them, the user's extra direction, and the preset finish. For the user-rejected corrections above, reproduce the original's version of those properties -- that is the required output, not merely a change you may skip."""

    # Analyzer prose can restate a correction outside correction_plan or ask
    # for a lighting mood that predates the user's style selection. In either
    # case, keep only descriptive context; the reviewed plan and selected style
    # carry the edit commands.
    if rejected_corrections or (
        selected_lighting_style and selected_lighting_style != "default"
    ):
        analysis_block = f"""Photo context after user review:
- Summary: {analysis.get("photo_summary") or ""}
- Main subject: {analysis.get("main_subject") or ""}
- Scene type: {analysis.get("scene_type") or ""}
- Must preserve where compatible with the user instruction: {preserve_rules}"""
    else:
        analysis_block = f"""Photo analysis:
- Summary: {analysis.get("photo_summary") or ""}
- Main subject: {analysis.get("main_subject") or ""}
- Scene type: {analysis.get("scene_type") or ""}
- Aesthetic diagnosis: {analysis.get("aesthetic_diagnosis") or ""}
- Detected issues: {detected_issues}
- Edit philosophy: {analysis.get("edit_philosophy") or ""}
- Desired mood: {analysis.get("composition_mood") or preset.get("label", "")}
- Layout method: {analysis.get("layout_method") or ""}
- Composition strategy: {analysis.get("composition_strategy") or ""}
- Visual balance strategy: {analysis.get("visual_balance_strategy") or ""}
- Empty space strategy: {analysis.get("empty_space_strategy") or ""}
- Alignment strategy: {analysis.get("alignment_strategy") or ""}
- Distraction strategy: {analysis.get("distraction_strategy") or ""}
- Scene preservation strategy: {analysis.get("scene_preservation_strategy") or ""}
- Recommended aspect ratio: {rec_aspect}
- Aspect ratio reason: {aspect_ratio_reason}
- Crop/reframe recommendation (advisory): {analysis.get("crop_recommendation") or ""}
- Crop intent (advisory): {crop_intent}
- Must preserve where compatible with the user instruction: {preserve_rules}"""

    task_intro = (
        "You are performing an image-to-image same-scene reshoot transformation, not a conservative retouch. "
        "Keep the same subject identity, scene, clothing, props, and photographic realism, but visibly change the subject's pose according to the selected pose override."
        if has_pose_override else
        "You are editing the supplied image, not creating a new image.\n"
        "Act like a high-end human retoucher: restrained, photographic, detail-aware, and realistic."
    )

    shape_preservation_rule = (
        "The photographed content keeps its true shape except for the one holistic body-reshape instruction below. "
        "Apply that instruction as a continuous, anatomically natural whole-person adjustment; preserve face, object, background structure, camera/tilt, relative spatial relationships, and unrelated people/regions. "
        if has_body_reshape else
        "The photographed content keeps its true shape throughout: every person, face, object, and background structure comes through with the proportions, the tilt, and the relative sizes and distances it has in the input photograph. "
    )

    prompt = f"""{task_intro}

{opening_preservation_rule}
{shape_preservation_rule}The output canvas may be a different shape than the input; that new shape comes from cropping and uniform scaling alone, never from resizing one axis on its own.
Apply explicitly requested additions. Otherwise, preserve the original content without adding text, logos, watermarks, people, props, fantasy elements, or a different background.
Do not make the result look synthetic, plastic, or heavily filtered.

{edit_mode_block}

Crop instruction:
{crop_instruction_block}

{focus_block}

{light_color_target_block}

{analysis_block}

{framing_intent_block}

Style direction:
{preset_prompt}

User extra direction:
{effective_user_direction or "沒有額外指令。"}

{user_block}

{style_block}

{correction_plan_block}

{body_reshape_block}

{blink_block}

{hair_block}

{calm_hair_block}

{negative_block}

{gaze_block}

{smile_block}

{skin_block}

{revision_block}

{retouch_priorities}

{color_grade_check}
"""
    if return_sections:
        return prompt, {
            "crop_instruction": crop_instruction_block,
            "style_instruction": style_block,
        }
    return prompt


def build_gaze_only_edit_prompt(gaze_items):
    """Prompt for the Step 5b gaze-only repair pass (pipeline._run_gaze_repair_pass):
    a second, full-image edit call on Step 5's own output, asking for nothing
    but the approved gaze fix(es).

    Step 5 bundles every gaze fix into one call together with exposure/crop/
    skin/style/etc, and at low image quality that call reliably applies the
    coarse, whole-frame changes but often no-ops the fine, localized iris/
    pupil movement -- confirmed by comparing several before/after test photos
    where the analysis's edit_instruction was just as clear and specific in
    the cases that failed as in the ones that worked. A second call whose
    prompt contains nothing but the gaze fix gives the same quality tier's
    budget and the model's full attention to just this one change, for every
    approved subject at once.

    An earlier version of this pass cropped to each face and pasted the
    edited crop back in. Workable in principle, but the crop's own edit call
    also regenerates its background (hair, collar, whatever the padding
    captured), and that regeneration didn't reliably match the base image
    closely enough to paste back invisibly -- it produced a doubled face and
    a warped background pattern on real test photos, even after restricting
    the paste to a re-detected eye region. A second whole-image call has no
    seam to paste and nothing separate to keep consistent: the model edits
    and returns the entire photo itself.
    """
    targets = []
    for item in gaze_items:
        label = item.get("subject_label") or "the subject"
        box = item.get("box") or {}
        strategy = item.get("gaze_repair_strategy") or {}
        edit_instruction = strategy.get("edit_instruction") or "Redirect this person's gaze to the exact camera lens using pupil/iris repositioning plus a visible, bounded head/face alignment."
        eye_adjustment = strategy.get("eye_adjustment") or "as needed to reach direct lens contact"
        head_face_adjustment = strategy.get("head_face_adjustment") or "visibly but naturally align the head/face toward the exact current lens while keeping the torso and shoulders unchanged"
        sclera_catchlight_adjustment = strategy.get("sclera_catchlight_adjustment") or "rebalance naturally around the new eye position"
        target_gaze_read = strategy.get("target_gaze_read") or "both eyes clearly and directly looking at the camera lens"
        strategy_type = strategy.get("strategy_type") or "eyes_plus_head_face_alignment"
        repair_strength = strategy.get("repair_strength") or "moderate"
        targets.append(
            f"- Subject: {label}\n"
            f"  Approximate location in frame: {json.dumps(box, ensure_ascii=False)}\n"
            f"  Required change: {edit_instruction}\n"
            f"  Eye/pupil adjustment: {eye_adjustment}\n"
            f"  Head/face adjustment: {head_face_adjustment}\n"
            f"  Sclera/catchlight adjustment: {sclera_catchlight_adjustment}\n"
            f"  Target result: {target_gaze_read}\n"
            f"  Approved repair method: {strategy_type} at {repair_strength} strength."
        )
    targets_block = "\n".join(targets)

    return f"""You are editing the supplied image, which is already a finished, fully-graded photograph. Do not treat it as a draft.

This is the ONLY task for this edit: redirect the named subject's or subjects' gaze so they clearly and directly look at the camera lens. Nothing else about this image should change -- not exposure, color, crop, framing, background, clothing, pose, expression, or any person other than the ones named below.

{targets_block}

Scope is limited to the named subject's eye direction and the exact bounded head/face alignment explicitly approved for that subject. If the photo contains other people who are not named above, they are completely out of scope: do not touch their eyes, gaze, expression, pose, hair, or anything else about them, even if their gaze also looks imperfect. For the named subject, preserve eyebrows, eyelid openness and crease shape, mouth, expression, identity, neck anatomy, torso, shoulders, and body pose. The head/face may turn or tilt only when the approved repair method is eyes_plus_head_face_alignment; never rotate the shoulders or torso to simulate that head correction.

Execution by method:
- eyes_plus_head_face_alignment: the head/face adjustment is required, not optional. Visibly but naturally turn and/or tilt the head/face toward the exact current lens, then align both pupils/irises to that lens. Do not merely shift the pupils while retaining an incompatible original head direction.
- eyes_only: keep the head/face angle unchanged and reposition only the pupils/irises. This is reserved for an extremely small miss with an already lens-aligned frontal head.

Go all the way to direct lens contact, not partway there. The completed correction must be immediately readable at normal photo-viewing size, without zooming into the face. A gaze that only moved closer to the camera without fully arriving is the same failure as not moving it at all -- "improved" is not the target, "looking straight into the lens" is. If you are unsure whether the pupils and, when required, the head/face have moved far enough, move them further within natural anatomical limits rather than settling for a subtle, ambiguous shift. A clearly visible correction that unmistakably reads as direct eye contact is the right outcome; a timid one that still reads as near-camera or off-camera is not. Do not leave any named subject's gaze unchanged, and do not stop at a partial correction.

Preserve everything else in the photograph exactly as supplied, especially: every other person in the frame, in full and completely unmodified; the named subject's expression, mouth, and eyebrows; and the entire background. Also preserve identity, face shape, skin texture, skin tone, hairstyle, glasses, makeup, lighting, clothing, pose, and composition. Do not add text, logos, watermarks, or new people. Do not crop, reframe, resize, or change the composition -- the output must be the same framing as the input, at the same size.
"""


def build_quality_check_prompt(analysis):
    user_instruction = analysis.get("user_instruction") or {}

    user_block = ""
    if user_instruction.get("has_user_direction"):
        explicit_priorities = "; ".join(user_instruction.get("explicit_priorities") or [])
        explicit_prohibitions = "; ".join(user_instruction.get("explicit_prohibitions") or [])
        qa_focus = "; ".join(user_instruction.get("qa_focus") or [])
        user_block = f"""User instruction priority:
- User intent summary: {user_instruction.get("user_intent_summary") or ""}
- Normalized user direction: {user_instruction.get("normalized_user_direction") or ""}
- Explicit priorities: {explicit_priorities}
- Explicit prohibitions: {explicit_prohibitions}
- QA focus: {qa_focus}

Evaluate whether the output satisfies the user's explicit instruction. If the user requested a specific change and it is not achieved, lower the score. If the user prohibited an action and the output does it anyway, treat that as a major failure. For aspects the user did not mention, judge Agent improvements normally but conservatively."""

    preserve_rules = "; ".join(analysis.get("preserve_rules") or [])
    roi_box = json.dumps(analysis.get("roi_box") or {}, ensure_ascii=False)
    subject_extent_box = json.dumps(analysis.get("subject_extent_box") or {}, ensure_ascii=False)
    protected_regions = json.dumps(analysis.get("protected_regions") or [], ensure_ascii=False)
    rec_aspect = (
        analysis.get("recommended_aspect_ratio") or
        (analysis.get("crop_box") or {}).get("aspect_ratio") or
        "original"
    )
    retouch_recipe = analysis.get("retouch_recipe") or []
    recipe_list_text = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(retouch_recipe)) or "（無修圖建議項目）"

    style_qa_lines = [
        f'- item text to use: "{short_label}" | what to check: {description}'
        for short_label, description in style_option_entries(analysis)
    ]

    if style_qa_lines:
        style_qa_block = "\nUser-selected style overrides (the user explicitly chose these on the review screen -- the output should reflect them):\n" + "\n".join(style_qa_lines) + (
            '\nJudge each style override independently against its "what to check" description, and put the result in recipe_adherence.style_items with "followed": true/false (use null only if it could not be judged). Set each entry\'s "item" field to exactly the given "item text to use" -- do not restate the full description there.'
        )
    else:
        style_qa_block = "\nThe user did not select any style overrides for this photo. Return recipe_adherence.style_items as an empty array -- do not invent style overrides."

    rejected_corrections = analysis.get("rejected_corrections") or []
    if rejected_corrections:
        # Judge against the same scoped instruction the edit prompt was held to,
        # not the QA model's own reading of a free-text recipe line. Scope is the
        # whole point: if the rejected item was "brighten the subject", a brighter
        # background is not a violation of it.
        inverses = analysis.get("rejected_preserve") or []
        if len(inverses) == len(rejected_corrections):
            rejected_lines = "\n".join(
                f"{i + 1}. {item}\n   Required instead: {inverse}"
                for i, (item, inverse) in enumerate(zip(rejected_corrections, inverses))
            )
        else:
            rejected_lines = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(rejected_corrections))
        rejected_block = f"""
User-rejected corrections (the user explicitly unchecked these from the suggested recipe -- the output must NOT show these changes):
{rejected_lines}
Judge each rejected item independently and put the result in recipe_adherence.rejected_items, with "violated": true/false (true = the forbidden change is visible in the output anyway, false = correctly avoided; use null only if the item does not apply to this photo). Judge only within the scope each item names: if an item was about the subject, a change elsewhere in the frame is not a violation of it."""
    else:
        rejected_block = "\nThe user did not reject any corrections for this photo. Return recipe_adherence.rejected_items as an empty array -- do not invent rejected items."

    # 笑容調整 has no recipe item to score against (the level is a 人像設定
    # choice applied at edit time, not a correction the analysis proposed), so
    # QA is told the expected endpoint here or it reads the new expression as
    # an unrequested change to the moment and caps the score for it.
    smile_expectations = smile_qa_expectation(analysis)
    smile_qa_line = ""
    if smile_expectations:
        smile_qa_line = (
            "- User-selected smile adjustment: the user asked for these people's smiles to be changed, so the new "
            f"expression is requested, not a lost moment -- {smile_expectations}. Check that each named person "
            "reached that overall intensity through a natural, coordinated facial expression. Identity, facial "
            "anatomy, skin texture, gaze direction, head pose, and all unrelated image content remain faithful "
            "to the original. A changed expression on anyone not named fails this check.\n"
        )

    body_prompt = get_body_reshape_edit_prompt(analysis)
    body_qa_block = "" if not body_prompt else (
        "- Holistic Body Reshape instruction (copied from Analyze; judge this sentence's visible intent as written): "
        f"{body_prompt}\n"
        "  Compare the visible figure with the original and judge whether the requested overall contour/proportion "
        "improvement was carried out naturally. Check anatomy continuity, identity, face, clothing identity and "
        "semantic coverage, and nearby scene geometry. Do not require key points, regions, or numeric measurements; "
        "uncertainty should be reported rather than converted into a fabricated percentage.\n"
    )
    body_qa_score_cap = "" if not body_prompt else (
        "- For Body Reshape, redesigning clothing, changing semantic coverage, damaging anatomy continuity, or "
        "warping nearby content is a major preservation failure: pass must be false and score must be 3 or lower.\n"
    )
    framing_focus = analysis.get("_framing_focus") or {}
    closer_block = ""
    if framing_focus.get("size") == "closer":
        focus_label = framing_focus.get("label") or "the single most prominent subject"
        closer_block = f"""
Framing intent -- CLOSER single-subject close-up:
The user requested an extreme close-up that features one subject only: {focus_label}. Cropping every other person, object, or element entirely out of frame is intended and correct here. Do NOT penalize the output for missing the other people/elements, for a tight or partial-person crop, or for lost background context -- those are the point. Only judge whether the one chosen subject is preserved cleanly (identity, expression, key details), the crop is not awkward (no ugly cuts through eyes, mouth, or joints), and the retouch quality is good."""

    head = f"""You are the QA agent for a photo retouching demo.
Compare the original photo and the edited output. Judge whether the output is safe to show to the user as a faithful enhancement.
Be strict. Your score must reflect the worst preservation or composition failure, not the average of good and bad qualities.
{closer_block}

{user_block}

Evaluate in this order:
1. Fatal preservation failures: wrong subject/scene, identity or anatomy/shape/material damage, important subject/protected content accidentally missing, unnatural perspective, artificial borders, transparent/checkerboard padding, or rebuilt environment.
2. Major composition failures: final crop contradicts framing intent, cuts required subject/role cues awkwardly, leaves the frame cramped, makes the subject undersized, or keeps/removes context in a way that weakens the scene.
3. Moderate quality failures: visible overprocessing, weak color/light, unstable but not destructive alignment, unclear empty-space function, or distraction handling that is only partially successful.
4. Successful demo qualities: faithful scene, intentional framing, believable light/color, natural texture, useful breathing room/context, and no preservation damage.

Check dimensions:
- Subject and scene preservation: same person/object/moment/environment, no unwanted identity, geometry, material, or perspective changes.
- Framing and crop safety: the crop respects the intended framing. Penalize missing body/object/context only when it is required by that framing, role, action, interaction, product completeness, or scene story.
- Protected regions and role cues: required subject parts, structural edges, fine details, connected components, props, supporting subjects, and role-identifying details are not accidentally clipped or pressed against edges.
- Composition: aspect ratio, layout method, subject placement, empty space, breathing room, context, and visual balance feel intentional for the subject type and scene intent.
- Alignment and edges: credible horizontals/verticals feel stable unless slant has a clear purpose; output edges are fully photographic with no blank, transparent, checkerboard, or artificial canvas areas.
- Distraction and scene truth: harmful distractions are reduced, but contextual/neutral elements are preserved unless they still compete strongly. The scene must not be rebuilt, over-simplified, or turned into a different style.
- Retouch quality: light, color, contrast, texture, and finish improve the original without looking synthetic, plastic, or heavily filtered. Where there are people, judge skin separately from the overall grade: each person keeps their own depth, undertone, and chroma while retaining plausible scene illumination; face, neck, ears, and hands remain continuous with no patch, band, unexplained hue mismatch, or saturation increase.
- Approved accidental blink repair: if the retouch recipe or style/correction plan asked to fix a named person's accidental closed or half-closed eyes, verify that the eyelids are naturally corrected while identity, face shape, glasses, makeup, and expression style remain believable. Check two independent mandatory outcomes at normal viewing size: (1) the repaired eye has the same final eye-opening height and iris exposure as its photograph-side reference eye; (2) it has the same double-eyelid fold shape as that reference eye -- the same fold type, crease visible along the same portion of the lid, the same crease path from inner to outer corner, the same fold arc and peak height, and the same visible crease-to-lash band width. Compare size in face-relative geometry: fixed canthi and horizontal span, matching aperture-height-to-eye-width ratio, and matching upper/lower-lid profiles at the inner, central, and outer thirds, allowing only head-perspective differences rather than demanding duplicated pixels. When exactly one photograph-side source eye was fully closed and the opposite source eye was open, the opposite eye is the reference; compare against its finished state if another approved edit also changed it. When the opposite source eye was itself half-closed or narrowed, both eyes were to be opened, so check the two requirements between the two finished eyes and reject a result that leaves either eye still narrowed or half closed, or that finishes the two eyes at visibly different eye-opening heights. Do not accept a result that improves only one requirement or compensates unequal eye opening with unequal crease width. A formerly closed eye whose crease disappears against the lash line while the reference eye retains a visible double lid fails this check. The result must not be a no-op, merely sharpened eyelids, oversized/doll-like eyes, copied-looking eyes, puffy lids, or an unrelated expression change. If that same person also had an approved gaze repair, verify one coherent final result: the required eyelid match plus exact-lens pupil/iris alignment with natural sclera and catchlights, not two conflicting eye states. If no gaze repair was approved, preserve a natural gaze consistent with the original head direction instead of forcing camera contact.
- Approved hair tidying: if the plan asked to tidy a named person's stray strands, check the named strands first, then check what else moved. Hairstyle shape, parting, length, volume, density, hairline, curl pattern, and color must be unchanged, individual strands must still be resolvable with real hair texture, and nobody else's hair may have been touched. Hair that is tidier but rebuilt, smoothed into a solid mass, straightened, thickened, or given a moved hairline is a failure of this item, not a success. Where a strand was taken off skin or background, check what replaced it: a faded or blurred remnant of the strand, a grey or darkened trace, or a smoothed plastic patch with the surrounding skin texture missing all fail this item, the same as leaving the strand would. Then check the face itself against the original, since hair repair is where identity slips: where a strand was taken off a feature, that feature must still be this person's own -- same eye shape, lash line, iris and catchlight position, nostril, lip edge and volume -- and the parts of it that were never covered must be untouched. A re-rendered, re-placed, re-shaped, or prettified feature, or a face that no longer reads as the same person, fails this item outright and caps the whole score at 3 as an identity change. If the plan asked to settle a wind-disturbed area, judge that area against the part of the same head the wind did not disturb: same length, volume, and silhouette, hairline where it was, gaps closed with the hair that was already there rather than with added hair, and strand direction, separation, and unevenness still visible. A settled area that reads as one glossy sheet, a painted block, or a wig fails this item, as does one carrying more length, volume, or coverage than the rest of that head. If the plan asked to de-grease and comb out a clumped fringe, check both halves separately, and judge it unfollowed if either is missing: the oily streak is matte with strand detail visible where the shine was, AND the thick cords are combed into an even-width curtain with the bare forehead gaps between them now covered by hair. A fringe still showing bald gaps between cords has not been combed out, however tidy the rest of the retouch is. Then check the limits: the fringe keeps its own length, outer edge, parting, and see-through quality, the hairline did not move, and the fringe carries no more hair, density, or width than the original. A fringe turned into a solid opaque curtain, thickened, lengthened, darkened, or blurred flat fails this item. If the user selected still-air hair, check that no hair is still streaming, fanned, or lifted by wind, that each hairstyle's outline reads settled and continuous, and that the rest of the scene -- clothing, flags, foliage, water, other people -- still shows the weather it was captured in. Hair that stayed windblown fails this, and so does a scene whose wind was calmed everywhere. Whatever the hair item asked for, compare the forehead and hairline directly against the original: a fringe that is gone, thinner, or shorter, a raised hairline, or more forehead or scalp on show than the original had is a hairstyle change, fails this item outright, and caps the whole score at 3.
{body_qa_block}
{smile_qa_line}- Approved gaze-contact repair: if a subject's gaze_issue.should_fix=true, or if the retouch recipe or correction plan asked to make a named person's gaze look toward the camera, verify that a clearly visible gaze-direction change actually occurred for the named subject and that the final gaze immediately and unmistakably reads as direct contact with the exact current lens at normal photo-viewing size. The iris/pupil positions and visual axes must aim naturally at the lens, with believable sclera balance and gaze-consistent catchlights. If the approved strategy is eyes_plus_head_face_alignment, verify that the head/face also visibly turned and/or tilted toward the lens by the planned bounded amount; unchanged head direction with pupil-only movement is a failure. If the approved strategy is eyes_only, verify that the original head was already frontal and lens-aligned and that the miss was genuinely tiny. In all cases preserve eyelid openness, identity, expression structure, facial anatomy, neck anatomy, body pose, shoulder direction, torso direction, glasses/reflections, and every unlisted person. Fail the check for a no-op, sharpen/brighten-only eyes, a change visible only in a highly enlarged crop, residual off-camera/near-camera/past-camera/slightly-beside-lens gaze, crossed or strained visual axes, excessive sclera, incompatible head/eye geometry, rotated shoulders/torso, or an unplanned identity/expression change.

Strict scoring rubric:
- 9-10: excellent, faithful, polished, intentional framing, no meaningful preservation/crop issues.
- 7-8: good demo output; minor taste, color, or small composition issues only.
- 5-6: borderline; noticeable flaws, but subject, scene, and required framing remain believable.
- 3-4: failed demo output; required subject/context is clipped, cramped, distorted, unstable, or the composition is clearly worse.
- 1-2: severe failure; preservation damage, important missing content, artificial borders, distorted shape/perspective, or wrong/rebuilt scene.

Score caps:
{body_qa_score_cap}
- If an approved blink repair leaves either eye closed/narrowed, or leaves visibly unequal normalized aperture, three-point lid contour, iris exposure, or double-eyelid fold path/width, score must be 4 or lower and pass must be false.
- If required main subject content, protected_regions, role cues, important props, structural edges, fine details, or connected components are accidentally cut off or incomplete, score must be 3 or lower.
- Apply the previous cap only to content required by the intended framing. Do not penalize partial portrait/person crops when they are intentional, clean, and preserve the role/story.
- If subject shape, identity, material, anatomy, object geometry, structural continuity, fine-detail structure, or perspective relationship is changed unnaturally, score must be 3 or lower.
- If an important main or supporting person has required head/face/role-identifying details accidentally clipped, score must be 4 or lower; if this prevents understanding the person's role or event, score must be 3 or lower.
- If both cropping damage and shape/perspective distortion are present, score must be 2 or lower.
- If the output contains transparent padding, checkerboard areas, blank borders, artificial canvas extension, or empty side bands, score must be 3 or lower.
- If the original scene is visibly rebuilt, replaced, over-simplified, or converted into a different environment/style, score must be 3 or lower.
- If contextual or neutral elements are removed/rebuilt just for cleanliness and this changes scene truth, score must be 3 or lower; if scene truth remains but context is weakened, score must be 5 or lower.
- If composition method, aspect ratio, empty space, alignment, or context choice visibly weakens the image, score must be 6 or lower; if it also harms required subject completeness or scene intent, score must be 3 or lower.
- If a person's complexion identity is lost -- lightened toward a paler complexion, flattened, made more saturated, pushed toward yellow, orange, or red without scene-light justification, or broken by a patch or band -- score must be 3 or lower. Coherent warm, cool, or colored light from the scene is not a failure.
- If output is not safe to show as a demo result, pass must be false and overall_status must be "需人工複查" or "失敗".
- Preservation failures dominate the score. Do not give a generous middle score just because color or background improved.
- The numeric score, pass value, overall_status, summary, warnings, and recommendation must be internally consistent.

Recipe adherence scoring (separate from the main score above, does not affect pass/overall_status/score):
Retouch recipe (the user-facing suggestions the edit was meant to follow):
{recipe_list_text}
These items describe the *direction* of an intended adjustment, not an exact required outcome -- the output does not need to literally match the wording, but each item's underlying direction should be visibly reflected in the output to count as followed.
Judge each item independently and put the result in recipe_adherence.items, with "followed": true/false (use null only if the item does not apply to this photo).
{style_qa_block}
{rejected_block}
Then set recipe_adherence.score from 0 to 10 weighing how many recipe items and style overrides above were followed and whether any rejected item below was violated -- a violated rejection is a severe adherence failure, so cap recipe_adherence.score at 3 or lower if any rejected item was clearly performed anyway. Give a one-line recipe_adherence.reason covering all three.

Original analysis:
- Main subject: {analysis.get("main_subject") or ""}
- Scene type: {analysis.get("scene_type") or ""}
- Edit intent: {analysis.get("edit_intent") or ""}
- Preserve rules where compatible with the user instruction: {preserve_rules}
- ROI box: {roi_box}
- Subject extent box: {subject_extent_box}
- Protected regions: {protected_regions}
- Portrait framing: {analysis.get("portrait_framing") or ""}
- Layout method: {analysis.get("layout_method") or ""}
- Composition strategy: {analysis.get("composition_strategy") or ""}
- Empty space strategy: {analysis.get("empty_space_strategy") or ""}
- Alignment strategy: {analysis.get("alignment_strategy") or ""}
- Distraction strategy: {analysis.get("distraction_strategy") or ""}
- Scene preservation strategy: {analysis.get("scene_preservation_strategy") or ""}
- Crop recommendation: {analysis.get("crop_recommendation") or ""}
- Recommended aspect ratio: {rec_aspect}
- Aspect ratio reason: {analysis.get("aspect_ratio_reason") or ""}

Return only valid JSON. User-visible values must be Traditional Chinese.
"""
    schema = """{
  "pass": true,
  "overall_status": "可展示",
  "score": 8,
  "failure_level": "none / minor / borderline / major / severe",
  "score_reason": "說明為什麼給這個分數，尤其要對照是否有主體裁切、保護區域破壞、形狀失真或透視不自然。",
  "summary": "一句話說明輸出是否符合原始照片與修圖目標。",
  "checks": {
    "subject_preserved": true,
    "user_intent_followed": true,
    "crop_safe": true,
    "protected_regions_safe": true,
    "framing_intent_respected": true,
    "aspect_ratio_feels_intentional": true,
    "composition_method_matches": true,
    "empty_space_has_function": true,
    "alignment_stable_or_intentional": true,
    "distractions_reassessed_after_crop": true,
    "scene_preserved": true,
    "frame_breathing_room_ok": true,
    "not_overedited": true,
    "light_color_improved": true
  },
  "warnings": [],
  "recommendation": "可直接展示，或說明需要人工複查的原因。",
  "recipe_adherence": {
    "score": 8,
    "reason": "說明修圖建議項目、風格設定的達成情況，以及使用者不要的項目是否有被誤觸。",
    "items": [
      {"item": "讓照片更明亮", "followed": true, "note": "已提升整體亮度。"}
    ],
    "style_items": [
      {"item": "Pose: Playful", "followed": true, "note": "姿勢已呈現輕鬆自然感。"}
    ],
    "rejected_items": [
      {"item": "移除背景雜物", "violated": false, "note": "背景雜物仍保留，未被誤刪。"}
    ]
  }
}"""
    return head + schema
