import copy
import json
import logging
import re

import config as _photoagent_config
from aspect_ratio_rules import allowed_auto_ratio_labels
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


# ── Correction axes ───────────────────────────────────────────────────────────
#
# The vocabulary for correction_axes: which property of the photograph a
# correction_plan item moves. Defined here, above the analysis instructions, because
# it has to be the single source for two things that must not drift apart --
# the vocabulary the analysis model is given and the axes the edit side gates
# on. An axis the analysis model invents but the edit prompt does not know is dropped
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

_AXIS_VOCABULARY = ", ".join(f'"{axis}" ({desc})' for axis, desc in AXIS_DEFINITIONS.items())

# Near-misses the analysis model actually produces despite being handed the
# vocabulary -- "noise" showed up on 3 of 6 photos in the first batch, tagging
# denoise items that belong to "sharpen". Left unmapped, an item tagged with a
# near-miss is invisible to the axis gates (_axis_set). Cheaper and more robust
# to absorb the synonyms than to keep tightening the instruction.
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


# Spliced into build_portrait_qc_instruction's Call B prompt.
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


# ---------------------------------------------------------------------------
# Analysis split: Call A (overall photo direction + subject identification)
# and Call B (dedicated portrait-QC pass judging blink/hair/gaze/smile only for
# the people Call A already identified). See openai_client._analyze_photo_two_pass.
#
# Call A is told to leave correction_plan free of eye/gaze/hair items: keeping
# every should_fix issue in sync with hand-authored correction_plan/axes/
# inverse/recipe entries is bookkeeping layered on top of the visual judgment,
# and a plausible contributor to low blink recall (see project memory
# project_luna_eyeclose_recall.md / the 20260828 eval). Call B authors the hair
# handoff directly inside subjects[].hair_issue. Blink and gaze execute directly
# from their structured repair strategies, while smile executes directly from
# smile_issue, so none of those three needs a correction_plan backfill.
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

_BLINK_DETECTION_SECTION = """## Blink / eyelid physical-observation pass
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

_GAZE_DETECTION_SECTION = """## Exact lens-contact detection
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

- Select recommended_aspect_ratio only from this supported output set: {ratios}. It holds only the shapes a crop can reach while keeping most of this photograph, so the output stays close to the shape it was shot in.
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
- {ratio} may differ sharply from the source frame, so reaching it can cost much more of the photo than a freely chosen ratio would. Keep the ROI, subject extent and protected regions with breathing room. If they do not fit, resolve crop versus expand below first; only for crop, keep the ROI and name the loss in crop_box.reason.
- Downstream deterministic geometry still fits the rectangle to {ratio} exactly, so a proposal that is already exactly that shape is the one that survives unchanged.
"""

# Appended to the fixed-ratio policy only when the gap between the source and
# the requested shape is wide enough that a crop has to eat into the subject
# (EXPAND_PERMISSION_RETAINED_THRESHOLD). The mode is decided here, during
# composition, rather than by the edit model: only the analysis can see whether
# the subject still has room to lose, and a mode chosen after the composition
# notes are written would contradict them. The 35% mirrors
# image_tools.REFRAME_MAX_EXTENSION.
_ASPECT_RATIO_POLICY_EXPAND = """
- A {ratio} crop removes {lost_pct}% of the photograph's {lost_axis}. Decide reframe_mode before sacrificing subjects to fit the ratio.
- First identify a jointly portrayed group or a single focal subject, using the settled subjects and their shared activity. Spectators at the same event are not automatically co-subjects.
- For a jointly portrayed group, choose "expand" when the best {ratio} crop excludes an included member's face, expression or participation. Keep the group as the photographic subject; a narrower central subgroup is not an equivalent replacement. Member preservation takes priority over central ROI emphasis.
- For a single focal subject, choose "crop" when the face, defining expression and primary action remain clear. An intentional portrait trim may remove peripheral body parts and incidental background.
- Expansion continues the scene beyond the {grow_edges}, adds at most 35% to the {grow_axis}, and reduces the crop needed from the {lost_axis}. Retain as many group members and shared actions as this plan allows; residual cropping does not itself cancel the expand choice. Use "crop" if continuation would require inventing a person's body instead of extending the existing scene.
- In reframe_mode_reason, name the group members a pure crop excludes, or the focal expression/action a crop retains.
- For "expand", compose crop_box in the source shape; for "crop", compose the best exact {ratio} frame.
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
- Preserve scene identity, material, perspective, and spatial relationships outside approved local edits. A real fixture diagnosed as obstructing the subject or their view is an approved local removal when the covered scene can be plausibly continued.
- Cleanup may remove a harmful obstruction locally while keeping the same scene; do not turn a casual/lifestyle/documentary scene into a studio, catalog, or different location unless the user explicitly asks for that.
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
  "distraction_strategy": "裁切後重新評估背景/前景元素：哪些是真正干擾、哪些是場景脈絡、哪些只是中性背景。場景脈絡須在這張照片裡發揮可見作用（場所辨識、尺度、敘事、引導線或平衡）；只是實際存在、卻切過畫面而無此作用的元素是干擾。針對每個干擾元素，說明具體且可執行的處理方式。",
  "scene_preservation_strategy": "如何保留原始場景內容、背景結構、材質身分、透視與空間關係；不要在此限制曝光、白平衡、對比、色相或飽和度。",
  "roi_anchor": "主視覺焦點應落在哪裡，以及為什麼。",
  "recommended_aspect_ratio": "original 或 Aspect-ratio policy 列出的比例之一",
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

_COORDINATE_FRAME_RULE = """- crop_box, roi_box, subject_extent_box, and protected_regions use normalized original-image coordinates. x/y are top-left, w/h are width/height: the origin (x=0, y=0) is the photo's own top-left corner, x increases toward the photo's right edge, y increases toward its bottom edge.
"""

# Shared with the results-page reframing pass (build_reframe_instruction).
_CROP_GEOMETRY_RULES = """- protected_regions contain only indispensable subject details and role cues retained by the selected composition. When crop_box.enabled=true, keep every protected region inside crop_box with breathing room; leave optional background structure and intentionally trimmed subject/body edges outside these boxes.
- Choose "original" with crop_box.enabled=false when the captured boundaries support the intended subject emphasis, balance, context, and edge cleanliness as well as any viable crop.
- A same-ratio or near-full composition crop remains valid when it removes an edge distraction, strengthens the subject anchor, or gives the frame a cleaner visual endpoint. Do not enable crop_box only to straighten the horizon or correct perspective; alignment owns its own minimal cleanup trim.
- The output does not need to preserve the original canvas aspect ratio in auto mode. Choose exactly one value from the supported output set in the Aspect-ratio policy below.
- Changing the outer canvas ratio never permits changing the internal proportions of the photographed content. Plan the new frame only by cropping/recomposition; never stretch or squash the original scene to fill it.
- The crop_box.aspect_ratio and every crop_candidates[].crop_box.aspect_ratio must match recommended_aspect_ratio exactly. Do not describe one ratio while returning crop geometry intended for another ratio.
- This is not just a label match: each crop_box's own x/y/w/h must itself compute to that same ratio -- (w * original_image_width) : (h * original_image_height) must equal the stated aspect_ratio, for both crop_box and every crop_candidates[].crop_box. A correctly-labeled aspect_ratio paired with geometry sized for a different ratio is an error, not a valid answer.
- Choose aspect ratio from subject type, scene intent, supporting elements, empty-space function, visual balance, and breathing room.
- Set crop_risk.needs_validation to true when composition is ambiguous, crop is aggressive, ROI is near an edge, protected regions are near edges, framing could be misread, or alignment/context tradeoffs need judgment.
"""

_COORDINATE_GEOMETRY_RULES_OVERALL = """## Coordinate and geometry rules
Intent: Produce normalized subject and crop geometry that downstream framing logic can use safely and consistently.

""" + _COORDINATE_FRAME_RULE + """- Any "left"/"right"/"middle" wording -- in a subjects[].label, a reason field, or anywhere else describing where someone or something sits in the frame -- is always from the viewer's perspective looking at the photo (the side with the smaller x is "left"), never that person's own anatomical left/right. Keep this the same reference frame you use when writing box x/y/w/h for that same entry, so the coordinates and the words describing them never disagree about which side of the frame something is on.
- roi_box is the focal area. subject_extent_box is the semantic subject area required by the intended framing; it is not automatically every visible body part, foreground object, furniture piece, or background element.
- subjects lists the candidate main subjects (people, animals, products, food, objects) ordered so index 0 is the photo's true main subject -- what the photo is actually about, consistent with main_subject, roi_box, and the composition intent. prominence means semantic importance as the subject, NOT which element is merely brightest, most colorful, or most saturated. Incidental background or decor -- flowers, plants, props, tableware, posters, scenery -- must rank lower than the real subject even when it is visually eye-catching; e.g. in a food photo the dish is the subject, not a bright vase of flowers behind it. Always output at least one entry, even for a single-subject photo.
- Each subjects[].box must tightly frame EXACTLY ONE individual person/animal/object, never a group. In a multi-person or multi-object scene, output one separate entry per individual, each with its own tight box; do NOT return a single box that spans several subjects or most of the frame. If there are four people, subjects must contain four entries, each boxing one person -- index 0 is only the single most prominent one, not all four. This holds even when main_subject or photo_summary describe the group as a whole; subjects is always per-individual so a downstream close-up can isolate exactly one.
- For a person-type subject whose face is visible anywhere in the photo, subjects[].box must include that entire visible head. A box that starts at or below the chin/neck/shoulders and excludes the face -- even if it otherwise fits the rest of the visible body tightly and looks "correct" by itself -- is wrong and must be corrected; the head is not an optional part of a person subject's box.
""" + "\n" + """- For every "Local face detector reference" you relied on to create a subjects[] entry (see "Zoomed face-crop images" above and its local_face_review requirement), that person's subjects[].box must fully contain the detected face box -- its top/left/right/bottom edges at or beyond the detected face box's own edges on every side, never cutting into or excluding it. A subjects[].box that does not contain the face detection it was created from is wrong and must be corrected before finalizing the analysis; this containment check is mandatory whenever such a detection exists, not merely a suggestion for when your own estimate looks "noticeably off". subjects[].box must still extend beyond just the face to cover that individual's full semantic extent -- containing the face box is a minimum, not the whole box.
""" + _CROP_GEOMETRY_RULES

_COMPOSITION_DECISION_FRAMEWORK = """## Composition decision framework
Intent: Choose framing from subject meaning, scene context, visual balance, useful empty space, and preservation needs rather than a fixed composition formula.

- For completeness-first presentation, catalog-like, symmetrical, circular, stacked, shape-critical, food/product/object/still-life images, centered or near-centered balance is often competitive.
- For lifestyle, story, portrait, street, travel, action, gaze, gesture, interaction, leading lines, asymmetry, or intentional negative space, off-center, thirds, diagonal, symmetry, or context-preserving framing may be stronger.
- Empty space must have a visible job: direction, story, scale, calmness, graphic balance, leading lines, room for action, environmental context, rhythm, or intentional negative space. Preserve generous breathing room when it serves those jobs; reduce or rebalance empty/background areas when they do not.
- Reassess foreground elements after the proposed crop. An element separating the subject from what they view or engage with is an obstruction, not contextual framing. Crop only if its entire connected visible footprint leaves the final frame while subjects and useful context remain; otherwise keep the frame viable for full local removal.
- Check alignment when credible horizontal/vertical references exist. Correct unintended tilt or keystone only when it feels unstable or physically implausible; preserve intentional slant only when it has a clear visual purpose.
- Preserve scene identity, background structure, surface material, perspective, spatial relationships, and real-world context outside approved local edits. The captured outer boundaries remain independently editable.
- For people and animals, the face ROI alone is not automatically the whole subject. Protect the semantic subject extent and role cues required by the framing. Partial-person crops are acceptable only when intentional, clean, and supportive of the scene.
- For action, eating, gesture, interaction, or expression-driven moments, protect expression, hands, held objects, interaction points, and relevant context. Preserve lower body, furniture, foreground objects, or peripheral context only when they support posture, story, scale, balance, or scene intent.
- For fashion, posture, full-body portrait, dance, sports, product shape, architecture, or object-completeness tasks, preserve the full visible form more conservatively.
- Judge real-image breathing room from actual photo content, not transparent padding, UI checkerboard, or empty canvas.
"""

_ANALYSIS_RESPONSIBILITIES_OVERALL = """## Analysis responsibilities
Intent: Translate photographic problems and opportunities for aesthetic improvement into an actionable, internally consistent correction_plan, leaving every eye, gaze, and hair judgment to the dedicated portrait-QC pass that runs after this one.

- Observe the image, identify semantic roles, and describe why the photo can improve.
- Define the main subject, useful context, harmful distractions, ROI, subject_extent_box, and protected_regions. A foreground element named in detected_issues as blocking subjects or their interaction stays a harmful distraction throughout the plan, even if it has a structural role.
- Decide the composition intent and framing intent before proposing any crop. Centering, thirds, symmetry, leading lines, and negative space are candidate strategies, not fixed rules.
- Treat edit_intent as the single finished light-and-color target. Define a specific overall brightness, tonal relationship, and scene palette that make this photograph polished and visually compelling.
- Make lighting_strategy and memory_color_strategy implementation plans for that same target. They must strengthen edit_intent rather than introduce a different mood, brightness target, or instruction to retain the captured scene color.
- Translate the target into correction_plan. Each item names its region, concrete adjustment, useful strength, and visible contribution to the finished result. Include the light and color work needed to create the target, not only defect repair. Word it concretely and executably -- if an item only conveys an abstract feeling or impression rather than an operation someone could actually carry out, rewrite it to name that operation.
- For each foreground obstruction left between the subject and relevant context, write one correction_plan item for complete removal and plausible continuation of the covered scene. Infer unseen pixels from adjacent visible edges; preserve subject identity and scene geometry outside the footprint. Do not retain a segment or substitute de-emphasis because the object is real or its exact hidden background is unknown.
- Protect skin color independently according to subjects[].skin. Scene exposure, white balance, contrast, hue, and saturation remain available to achieve edit_intent.
- Size correction_plan to what the photo actually needs, choosing the few adjustments with the greatest visual benefit. An already successful photograph may need none.
- correction_plan, correction_axes, correction_inverse, and retouch_recipe must not include any eye/blink repair, gaze/eye-contact repair, or hair correction. A separate portrait-QC pass judges each person's blink_issue/hair_issue/gaze_issue/smile_issue after this analysis; any approved blink/gaze repair is executed directly from that person's own blink_issue/gaze_issue, never through correction_plan. Write correction_plan here as if every subject's eyes, gaze, and hair are already fine, covering composition/crop/color/skin/product corrections and aesthetic improvements supported by the photo.
- Keep harmful obstructions out of preserve_rules; protect identity, content, geometry, and scene/material identity outside approved edit areas. Do not use preserve_rules to retain scene exposure, white balance, contrast, hue, saturation, or captured color atmosphere; those are governed by edit_intent and correction_plan. Keep eyes, blink, eye contact, gaze, hair, and smile out of preserve_rules because the portrait-QC pass governs them.
- Prioritize natural-looking skin over global color neutrality. Preserve each person's complexion, skin chroma, and intentional scene illumination; correct only a cast that is distracting, inconsistent across connected skin, or not plausibly explained by the light. Never write a correction_plan item that makes skin warmer, yellower, rosier, healthier-looking, more vivid, or more saturated. If correction is needed, name the unwanted hue, affected skin area, and corrective direction. When subjects[].skin says no unexpected cast, write no skin temperature, hue, or saturation adjustment. A global white-balance, hue, or saturation adjustment may refine the image palette, but it must not force skin to follow the background grade.
- Choose the strongest framing independently of the output aspect-ratio preference. Compare the complete frame with at least one materially different viable crop that conforms to the selected ratio, treating both as equally eligible. Compare subject emphasis, edge distractions, empty-space function, balance, and protected content; select the crop only when it produces a clearly stronger composition. Return crop_candidates for viable alternatives, with each candidate explaining its composition intent and tradeoffs.
- Make an approved composition crop visibly consequential: normally remove at least 8% of the original span on one axis (w <= 0.92 or h <= 0.92) and change subject scale, placement, or edge content. Use original for smaller cosmetic trims; such a trim is not executed, so it removes no distraction—plan local removal instead. Alignment may still use its minimal cleanup crop.
- Populate subject_extent_box and protected_regions for the selected final composition. Cover the semantic subject and must-retain role cues only; keep optional context and intended-to-remove edge content outside these protection boxes so crop validation can honor the chosen frame.
- When crop_box.enabled=true, verify that every protected_regions box fits inside crop_box with breathing room. Resolve any conflict by repositioning the crop or limiting protection to the indispensable detail; background structure and intentionally trimmed subject/body edges remain outside protected_regions.
- Local_face_review completeness check: before finalizing analysis, verify local_face_review has exactly one entry per "Face crop N" / "Local face detector reference N" you were given, and that every entry's matched_subject_index (when >= 0) points at a subjects[] entry whose own local_detection_index equals that same detection_index. If a detection was skipped, or the cross-reference does not agree in both directions, fix it before finalizing.
- Subject inclusion recall check: before finalizing analysis, re-apply the Subject inclusion rule to every person subjects[] entry, not only the ones that came from a local detection. If a subject you added yourself from the full photo actually reads as an incidental bystander, remove that subjects[] entry (and any correction_plan/correction_axes/correction_inverse/retouch_recipe entries that named them) rather than leaving it in.
- is_person recall check: for every remaining person subjects[] entry, re-apply the is_person definition above. If it is currently true but this person's face is not actually legible enough to judge (back of head, silhouette, fully obscured), flip it to false and reset skin/blink_issue/hair_issue/gaze_issue/smile_issue to their is_person=false placeholders instead of leaving stale judged values in place.
- Do not score output quality here. Do not write QA penalties here. Leave candidate ranking to the crop judge and final scoring to QA.
- Make the composition-framing decision decisive and consistent. When composition_strategy, distraction_strategy, or detected_issues identifies a composition crop/reframe to execute, express it through crop_box and a crop-tagged correction_plan item. When crop_box.enabled is false, describe the original frame as the selected final composition with a definitive preserve decision. An alignment_strategy correction alone must not enable crop_box.
- Consolidate the entire composition-framing decision into that one crop-tagged item: its crop/trim/frame-relative repositioning or scale actions and any change to recommended_aspect_ratio must never become separate review items. The paired retouch_recipe starts with "重新構圖：" and contains no numeric ratio; local deterministic geometry adds the resolved ratio later. Its correction_inverse explicitly restores the original ratio and composition but says nothing about tilt or perspective.
- Keep alignment independently reviewable. If alignment_strategy calls for whole-frame rotation, horizon/vertical straightening, or keystone/perspective correction, create a separate alignment-tagged item whose retouch_recipe starts with "水平／透視校正：". Do not merge it into the crop item; any minimal wedge-removal trim is part of alignment and must preserve the selected ratio and composition as closely as possible.
- Before returning JSON, compare detected_issues, crop_box, correction_plan, retouch_recipe, and preserve_rules. If a foreground barrier, or an element distraction_strategy crops away, remains in crop_box, require full removal and background fill in the plan and recipe; a crop counts only if it excludes the whole element. Delete preservation or de-emphasis for that element. Exact hidden detail need not be known: infer it locally while protecting visible identity and scene outside the footprint. Align distraction_strategy, correction_axes, and correction_inverse.
- Read edit_intent, lighting_strategy, memory_color_strategy, correction_plan, and preserve_rules together. Make them one consistent target and execution plan; remove or rewrite any statement that weakens or preserves away an approved light or color adjustment.

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


def _auto_ratio_choices(source_size):
    try:
        source_ratio = float(source_size[0]) / float(source_size[1])
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        source_ratio = None
    return ", ".join(("original",) + allowed_auto_ratio_labels(source_ratio))


def _aspect_ratio_policy_suffix(aspect_ratio_pref, source_size=None):
    """source_size: the photograph's own (width, height). Without it the
    expand clause is left out -- the choice it describes depends on how far
    this shape is from that one, so an unknown source keeps crop-only."""
    pref = str(aspect_ratio_pref or "auto").strip().lower()
    if pref == "original":
        return _ASPECT_RATIO_POLICY_ORIGINAL
    if not _FIXED_ASPECT_RATIO_PREF.match(pref):
        return _ASPECT_RATIO_POLICY_AUTO.format(ratios=_auto_ratio_choices(source_size))

    policy = _ASPECT_RATIO_POLICY_FIXED.format(ratio=pref)
    if not expand_permitted(pref, pref, source_size):
        return policy
    narrower = _canvas_shape_difference(pref, source_size) == "narrower"
    return policy + _ASPECT_RATIO_POLICY_EXPAND.format(
        ratio=pref,
        lost_pct=round((1 - _cross_direction_retained(pref, source_size)) * 100),
        lost_axis="width" if narrower else "height",
        grow_edges="top and bottom edges" if narrower else "left and right edges",
        grow_axis="height" if narrower else "width",
    )


def build_overall_analysis_instruction(aspect_ratio_pref="auto", source_size=None):
    """Call A of the analysis split (see the module comment above
    _FACE_CROP_EVIDENCE_SECTION): overall photo direction and subject
    identification. Blink/hair/gaze/smile are judged afterward by the
    portrait-QC pass (build_portrait_qc_instruction) from this call's own
    finalized subjects[] list.
    """
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
    return instruction + _aspect_ratio_policy_suffix(aspect_ratio_pref, source_size)


def build_portrait_qc_instruction(persons):
    """Call B of the analysis split: given Call A's already-
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
        + _BLINK_DETECTION_SECTION
        + _HAIR_ANALYSIS_SECTION
        + _HAIR_CORRECTION_HANDOFF_SECTION
        + "\n"
        + _GAZE_DETECTION_SECTION
        + "\n"
        + _SMILE_ANALYSIS_SECTION
        + "\n## Portrait-QC responsibilities\n"
        + _ANALYSIS_RESPONSIBILITIES_PORTRAIT_QC
    )


def build_eye_repair_planning_instruction(targets):
    """Optional Call C. Only people who passed the deterministic blink
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




# ── Structured output schema for Step 2 (photo analysis) ──────────────────────
#
# Mirrors the "Return exactly this JSON shape" blocks in the analysis
# instructions above field-for-field. Passed as the Responses API's text.format
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
# subjects[] entry, so they always inherit that same subject's label and
# box by construction. There is no cross-array label-matching step left for
# the model to get wrong.

# A Call-C-authored (or deterministic-fallback) repair strategy for an
# approved blink fix, the same role _GAZE_REPAIR_STRATEGY_SCHEMA plays for gaze.
_BLINK_REPAIR_STRATEGY_SCHEMA = _schema_object({
    "target_eye_state": _schema_string(),
    "eyelid_adjustment": _schema_string(),
    "repair_strength": _schema_string(),
    "edit_instruction": _schema_string(),
    "success_condition": _schema_string(),
    "preserve_during_repair": _schema_string_array(),
    "failure_to_avoid": _schema_string_array(),
})

# Call B reports recall-first physical evidence only.  The legacy fields
# remain in the shape for downstream compatibility, but their final values are
# overwritten by derive_blink_decisions; the model is not authoritative
# for blink_candidate or should_fix.
_BLINK_DETECTION_SCHEMA = _schema_object({
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

# Call B deliberately stops at physical observation and exact-lens
# classification.  should_fix and the full repair strategy are added by
# deterministic Python and an optional repair-planning Call C respectively.
_GAZE_DETECTION_SCHEMA = _schema_object({
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

# Call A's subjects[] entry. The four per-person issue fields are added by
# Call B (build_portrait_qc_response_schema) and merged in afterwards.
_SUBJECT_SCHEMA_OVERALL = _schema_object({
    "label": _schema_string(),
    "box": _BOX_SCHEMA,
    "prominence": _schema_integer(),
    "is_person": _schema_boolean(),
    # -1 when this subject was added from the model's own reading of the full
    # photo rather than from a "Local face detector reference" detection, or
    # when is_person=false (the face detector never applies to those) -- see
    # _FACE_CROP_EVIDENCE_SECTION. Cross-references local_face_review[].
    # matched_subject_index in the other direction.
    "local_detection_index": _schema_integer(),
    "skin": _schema_string(),
    "reason": _schema_string(),
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

# Call A's schema (see build_overall_analysis_instruction).
ANALYSIS_RESPONSE_SCHEMA_OVERALL = {
    "type": "json_schema",
    "name": "photo_analysis_overall",
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
        "subjects": _schema_array_of(_SUBJECT_SCHEMA_OVERALL),
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


# ── Results-page reframing pass ────────────────────────────────────────────────
# Picking a different 長寬比 after analysis re-plans only the composition. The
# subjects, focal area and every non-framing retouch item stay as analyzed, so
# the retouch the user already reviewed is not rewritten.

REFRAME_FIELDS = (
    "composition_intent", "framing_intent", "layout_method", "portrait_framing",
    "composition_strategy", "visual_balance_strategy", "empty_space_strategy",
    "distraction_strategy", "recommended_aspect_ratio", "aspect_ratio_reason",
    "subject_extent_box", "protected_regions", "crop_recommendation",
    "reframe_mode", "reframe_mode_reason", "crop_box", "crop_candidates",
    "crop_intent", "crop_risk",
)

# One ratio's resolved composition. Writing these keys back restores it
# without another call; a None value means the key was absent.
COMPOSITION_STATE_KEYS = REFRAME_FIELDS + (
    "correction_plan", "correction_axes", "correction_inverse", "retouch_recipe",
    "_reframing_geometry_source", "auto_aspect_ratio", "reframe_extension",
    "reframe_mode_override", "aspect_ratio_source", "crop_box_measured_ratio",
    "_aspect_ratio_pref_at_analysis",
)

# Review and geometry state derived from the previous composition.
_REFRAME_STALE_KEYS = (
    "_rejected_reframing_source", "_preserve_original_composition", "applied_crop_plan",
    "rejected_corrections", "rejected_retouch_recipe", "rejected_axes", "rejected_preserve",
    "_reframing_geometry_source", "reframe_extension", "reframe_mode_override",
    "auto_aspect_ratio", "aspect_ratio_source", "crop_box_measured_ratio",
)

_REVIEW_ARRAY_KEYS = ("correction_plan", "correction_axes", "retouch_recipe", "correction_inverse")

_REFRAME_RECIPE_PREFIX_RE = re.compile(r"^\s*重新構圖\s*[:：]")

_REFRAME_PERSONA = """You are a senior photography director. This photograph has already been analyzed: its subjects, focal area and retouch plan below are settled. The user has since chosen a different output aspect-ratio preference, so the one decision to make again is the composition -- where the frame's edges fall for that preference.

Return only valid JSON. Keep the JSON keys in English. Every user-visible string value must be Traditional Chinese.

"""

_REFRAME_RETURN_SHAPE = """Return exactly this JSON shape:
{
  "composition_intent": "這個輸出比例下最適合的構圖意圖。",
  "framing_intent": "取景意圖，例如 close-up / upper-body / half-body / full-body / environmental / object-complete / context-preserving。",
  "layout_method": "center / thirds / symmetry / diagonal / vertical-horizontal / leading-lines / pattern / negative-space，加上中文理由。",
  "portrait_framing": "若不是人像可寫不適用；若是人像，說明框法。",
  "composition_strategy": "構圖與裁切策略。",
  "visual_balance_strategy": "左右與上下視覺重量如何處理。",
  "empty_space_strategy": "留白在新畫框中的功能，或如何縮減、重新平衡。",
  "distraction_strategy": "依新畫框重新判定哪些元素是干擾、脈絡或中性背景，以及每個干擾的具體處理方式。",
  "recommended_aspect_ratio": "依下方長寬比政策填寫。",
  "aspect_ratio_reason": "為什麼這個輸出比例與畫框最適合主體、陪襯、留白與畫面意圖。",
  "subject_extent_box": {"x": 0.15, "y": 0.08, "w": 0.70, "h": 0.82, "reason": "新畫框需要保留的完整語意主體。"},
  "protected_regions": [{"label": "", "x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1, "reason": "新畫框中不可裁掉或貼邊的區域。"}],
  "crop_recommendation": "若裁切，說明裁哪裡與為什麼；不裁切則說明保留原構圖。",
  "reframe_mode": "crop 或 expand，依下方長寬比政策；政策未要求選擇時填 \\"crop\\"。",
  "reframe_mode_reason": "選擇的理由；政策未要求選擇時填空字串。",
  "crop_box": {"enabled": true, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "aspect_ratio": "", "reason": "裁切理由。"},
  "crop_candidates": [{"id": "ai-primary", "label": "主要建議裁切", "crop_box": {"enabled": true, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "aspect_ratio": ""}, "composition_intent": "", "tradeoffs": ""}],
  "crop_intent": {"trim_left": false, "trim_right": false, "trim_top": false, "trim_bottom": false, "subject_anchor": "center", "looking_direction": "unknown", "remove_edge_distractions": []},
  "crop_risk": {"needs_validation": false, "risk_level": "low", "reasons": []},
  "reframe_item": {"enabled": true, "correction_plan": "給編修模型的構圖指令。", "retouch_recipe": "重新構圖：給使用者看的構圖結果。", "correction_inverse": "取消此項時維持原構圖的說法。"}
}

"""

_REFRAME_TASK = """## Reframing task
Intent: Choose the strongest final frame for the output-ratio preference stated after the photo, for this same retouch.

- Work from the settled subjects and focal area rather than re-identifying them. Decide the composition intent and framing intent for that preference first, then place crop_box to serve them. Compare the complete frame with at least one materially different viable crop, and return the viable alternatives in crop_candidates with their tradeoffs.
- A selected composition crop is visibly consequential: it normally removes at least 8% of the original span on one axis (w <= 0.92 or h <= 0.92) and changes subject scale, placement, or edge content.
- subject_extent_box and protected_regions start from the settled ones and end describing the selected frame: every indispensable subject detail and role cue stays, and only what this frame deliberately trims leaves them.
- Every composition field describes the selected frame alone. Alignment stays as settled; tilt and perspective are not part of this decision.
- reframe_item is the single review item that carries this framing decision, enabled exactly when the selected frame differs from the captured one -- a crop, or reframe_mode "expand". Its correction_plan is the precise editor instruction for the crop, trim, repositioning, or extension; its retouch_recipe starts with "重新構圖：" and names only the semantic result; its correction_inverse restores the original aspect ratio, outer boundaries, subject scale and placement, and empty-space balance, and says nothing about tilt or perspective. None of these, nor any other prose field, contains a numeric aspect ratio: local geometry inserts the resolved ratio afterwards.

"""


def _crop_tagged(entry):
    return "crop" in _axis_set([entry])


def _reframe_settled_facts(analysis, source_size):
    axes = analysis.get("correction_axes") or []
    plan = analysis.get("correction_plan") or []
    other_items = (
        [item for item, entry in zip(plan, axes) if not _crop_tagged(entry)]
        if len(plan) == len(axes) else []
    )
    subjects = [
        {key: subject.get(key) for key in ("label", "box", "is_person", "prominence")}
        for subject in analysis.get("subjects") or [] if isinstance(subject, dict)
    ]
    settled = {
        "photo_summary": analysis.get("photo_summary"),
        "main_subject": analysis.get("main_subject"),
        "roi_anchor": analysis.get("roi_anchor"),
        "alignment_strategy": analysis.get("alignment_strategy"),
        "subjects": subjects,
        "roi_box": analysis.get("roi_box"),
        "subject_extent_box": analysis.get("subject_extent_box"),
        "protected_regions": analysis.get("protected_regions") or [],
        "other_retouch_items": other_items,
    }
    size_line = (
        f"Original image size: {int(source_size[0])} x {int(source_size[1])} pixels.\n"
        if source_size else ""
    )
    return (
        "## Settled analysis\n"
        + size_line
        + json.dumps(settled, ensure_ascii=False, indent=2)
        + "\n"
    )


def build_reframe_instruction(analysis, aspect_ratio_pref="auto", source_size=None):
    """(before_photo, after_photo) text for the reframing pass.

    Everything that stays the same for one photo precedes the photo, and the
    ratio policy follows it, so repeated reframes of a photo share a cacheable
    prefix.
    """
    pref = str(aspect_ratio_pref or "auto").strip().lower()
    before_photo = (
        _REFRAME_PERSONA
        + _REFRAME_RETURN_SHAPE
        + _REFRAME_TASK
        + "## Coordinate and geometry rules\n"
        + _COORDINATE_FRAME_RULE
        + _CROP_GEOMETRY_RULES
        + "\n"
        + _COMPOSITION_DECISION_FRAMEWORK
        + "\n"
        + _reframe_settled_facts(analysis, source_size)
    )
    after_photo = (
        f"## Output aspect-ratio preference for this pass: {pref}\n"
        + _aspect_ratio_policy_suffix(pref, source_size)
    )
    return before_photo, after_photo


_ANALYSIS_PROPERTIES = ANALYSIS_RESPONSE_SCHEMA_OVERALL["schema"]["properties"]

REFRAME_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "name": "photo_reframe",
    "strict": True,
    "schema": _schema_object({
        **{field: copy.deepcopy(_ANALYSIS_PROPERTIES[field]) for field in REFRAME_FIELDS},
        "reframe_item": _schema_object({
            "enabled": _schema_boolean(),
            "correction_plan": _schema_string(),
            "retouch_recipe": _schema_string(),
            "correction_inverse": _schema_string(),
        }),
    }),
}


def with_reframing_proposal(analysis):
    """`analysis` with a declined reframe's saved proposal put back, so the
    composition it holds is the one the analyzer planned."""
    prior = analysis.get("_rejected_reframing_source")
    if not isinstance(prior, dict):
        return analysis
    restored = copy.deepcopy(analysis)
    restored.update(copy.deepcopy(prior))
    for key in ("_rejected_reframing_source", "_preserve_original_composition", "applied_crop_plan"):
        restored.pop(key, None)
    return restored


def composition_state(analysis):
    return {key: copy.deepcopy(analysis.get(key)) for key in COMPOSITION_STATE_KEYS}


def restore_composition_state(analysis, state):
    restored = copy.deepcopy(analysis)
    for key in _REFRAME_STALE_KEYS:
        restored.pop(key, None)
    for key, value in (state or {}).items():
        if value is None:
            restored.pop(key, None)
        else:
            restored[key] = copy.deepcopy(value)
    return restored


def _replace_reframe_item(analysis, item):
    """Swap the crop-tagged review item for the reframing pass's one, in every
    review array still aligned with correction_axes."""
    axes = list(analysis.get("correction_axes") or [])
    arrays = {key: list(analysis.get(key) or []) for key in _REVIEW_ARRAY_KEYS}
    aligned = [key for key in _REVIEW_ARRAY_KEYS if len(arrays[key]) == len(axes)]
    if "correction_plan" not in aligned or "retouch_recipe" not in aligned:
        logger.warning(
            "review arrays not aligned with correction_axes (%s); keeping the previous reframing item",
            {key: len(arrays[key]) for key in _REVIEW_ARRAY_KEYS},
        )
        return analysis

    crop_indices = [index for index, entry in enumerate(axes) if _crop_tagged(entry)]
    for index in reversed(crop_indices):
        remaining = [
            axis for axis in (axes[index] if isinstance(axes[index], list) else [axes[index]])
            if not _crop_tagged(axis)
        ]
        if remaining:
            arrays["correction_axes"][index] = remaining
        else:
            for key in aligned:
                del arrays[key][index]

    recipe = str(item.get("retouch_recipe") or "").strip()
    if item.get("enabled") and recipe:
        if not _REFRAME_RECIPE_PREFIX_RE.match(recipe):
            recipe = f"重新構圖：{recipe}"
        new_item = {
            "correction_plan": str(item.get("correction_plan") or "").strip() or recipe,
            "correction_axes": ["crop"],
            "retouch_recipe": recipe,
            "correction_inverse": str(item.get("correction_inverse") or "").strip(),
        }
        position = crop_indices[0] if crop_indices else len(arrays["correction_plan"])
        for key in aligned:
            arrays[key].insert(position, new_item[key])

    for key in _REVIEW_ARRAY_KEYS:
        if key in analysis or arrays[key]:
            analysis[key] = arrays[key]
    return analysis


def merge_reframe_result(analysis, reframe, aspect_ratio_pref):
    """`analysis` with its composition replaced by a reframing pass's answer,
    planned for `aspect_ratio_pref`. Local geometry still has to resolve it."""
    merged = copy.deepcopy(analysis)
    for key in _REFRAME_STALE_KEYS:
        merged.pop(key, None)
    for field in REFRAME_FIELDS:
        if field in reframe:
            merged[field] = copy.deepcopy(reframe[field])
    _replace_reframe_item(merged, reframe.get("reframe_item") or {})
    merged["_aspect_ratio_pref_at_analysis"] = str(aspect_ratio_pref or "auto").strip().lower()
    return merged

# Call B's per-person output shape (build_portrait_qc_instruction) -- one
# entry per person handed to Call B. subject_index is the stable merge key;
# local_detection_index validates the Face crop N association.
_PERSON_ISSUES_SCHEMA = _schema_object({
    "subject_index": _schema_integer(),
    "local_detection_index": _schema_integer(),
    "blink_issue": _BLINK_DETECTION_SCHEMA,
    "hair_issue": _HAIR_ISSUE_SCHEMA,
    "gaze_issue": _GAZE_DETECTION_SCHEMA,
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
    """The "不適用" shape for subjects with no portrait-QC reading -- the
    two-pass split's pre-merge baseline in openai_client._analyze_photo_two_pass, so
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
    # build_eye_repair_pass_prompt's execution-by-method rule), which is only
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
    key the compact blink contract (feature_resolver._blink_contracts)
    already reads for a Call-C-derived repair. The fixed strategy always wins
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
    enables a should_fix=false row. Used both by the compact gaze contract
    (feature_resolver._gaze_contracts) and by approved_gaze_items for the
    Step 5b second pass, which re-derives its own item list from analysis --
    both call sites need this applied, or a forced gaze fix silently never
    executes under the two-stage (single_pass_edit=False) flow.
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
    # A review opt-out is not an analyzer no-op. Preserve its approved plan
    # across saved versions; should_edit still gates Editor execution.
    user_disabled = body.get("_user_disabled") is True
    if user_disabled:
        normalized["_user_disabled"] = True
    if body.get("analysis_status") == "error":
        normalized["analysis_status"] = "error"
    if should_edit or user_disabled:
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
# at the top of this file (AXIS_DEFINITIONS), since the analysis instruction has
# to hand the same list to the analysis model.

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
    # This decision is deliberately narrow: an unrelated kept item carrying an unknown model-invented axis must not
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


OCCLUDED_PERSON_REMOVAL_RULE = (
    "During approved obstruction removal, preserve the main subjects. If an incidental background person is "
    "visible only as a narrow face or body fragment beside the obstruction, remove that entire person and fill "
    "their space with matching background; this local cleanup overrides generic instructions to preserve "
    "people's outlines. Otherwise reconstruct exposed people coherently from visible cues and natural overlap. "
    "Never leave a partial, floating, duplicated, or merged face."
)


def has_approved_distraction_removal(analysis):
    if "distraction" in _axis_set(analysis.get("rejected_axes")):
        return False
    plans = analysis.get("correction_plan") or []
    axes = analysis.get("correction_axes") or []
    for index, item in enumerate(plans):
        if index >= len(axes) or "distraction" not in _axis_set([axes[index]]):
            continue
        for clause in re.split(r"[，。；;,\n]", str(item)):
            if re.search(r"不(?:建議|應|要|可|必)?(?:完全|直接|局部)?移除|無需移除|避免移除|do not remove|never remove", clause, re.I):
                continue
            if re.search(r"移除|清除|消除|去除|刪除|remove|erase", clause, re.I):
                return True
    return False


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

    Shared by strip_disabled_topic_items' correction_plan, inverse and recipe
    filters.
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
        "(see the overall analysis instruction's face-detector containment rule)",
        "、".join(expanded_labels),
    )
    return {**analysis, "subjects": new_subjects}


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


def final_smile_by_subject(analysis):
    """Each person's smile intensity after Step 5: the target it was asked for, else the captured reading.

    Key None covers every person when no face was read but an explicit level was applied to the whole photo.
    """
    readings = _read_smile_states(analysis)
    if not readings:
        level = smile_level(analysis)
        return {None: level} if level not in (None, "auto") else {}
    final = {item["_subject_index"]: item["current"] for item in readings}
    final.update({t["_subject_index"]: t["target"] for t in resolve_smile_targets(analysis)})
    return final


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
    when it asked for no change -- built from the same resolve_smile_targets
    the edit prompt uses, so QA is never sent looking for a change the edit
    prompt did not request."""
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


def approved_blink_items(analysis):
    """Blink items the edit call is approved to repair, after the user's toggle and per-person overrides."""
    if not (analysis.get("_advanced_options") or {}).get("eye_closed_detection", True):
        return []
    disabled_indices = _disabled_issue_subject_indices(analysis, "blink_issue")
    return [
        {k: v for k, v in item.items() if k != "_subject_index"}
        for item in (resolve_user_blink_request(i) for i in subject_issue_items(analysis, "blink_issue"))
        if isinstance(item, dict) and (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in disabled_indices
    ]


def approved_gaze_items(analysis):
    """Gaze items the edit is approved to repair, after the user's toggle and per-person overrides.

    Keeps _subject_index, which Step 5b uses to attach each person's final smile.
    """
    if not (analysis.get("_advanced_options") or {}).get("gaze_contact_detection", True):
        return []
    disabled_indices = _disabled_issue_subject_indices(analysis, "gaze_issue")
    return [
        resolve_user_gaze_request(item) for item in subject_issue_items(analysis, "gaze_issue")
        if (item.get("should_fix") or item.get("_user_forced"))
        and item.get("_subject_index") not in disabled_indices
    ]


def build_framing_sections(analysis, crop_plan, aspect_ratio_pref="auto", source_size=None):
    """The edit prompt's crop and style sections: {"crop_instruction", "style_instruction"}.

    build_compact_editor_prompt parses these sentences (feature_resolver's
    _compact_raw_crop_instruction / _compact_raw_style_instruction), so their
    wording is a contract with that parser.
    """
    if not body_reshape_enabled():
        analysis = strip_body_reshape(analysis)
    has_body_reshape = bool(build_body_reshape_edit_block(analysis))
    if aspect_ratio_pref == "auto" and analysis.get("_aspect_ratio_pref_at_analysis") == "original":
        # The analysis was run while the user had "keep original" selected, so
        # its recommended_aspect_ratio/crop_box.aspect_ratio are forced to
        # "original" by the analysis instruction -- not a genuine model
        # judgment. The user has since switched to "Auto" for this edit, so
        # that forced value must not leak in as if it were a real
        # recommendation (it would otherwise silently keep pinning the output
        # to the original ratio even though "Auto" was chosen).
        rec_aspect = "no strong preference -- choose whichever aspect ratio best suits the subject and composition"
    else:
        rec_aspect = (
            analysis.get("recommended_aspect_ratio") or
            (analysis.get("crop_box") or {}).get("aspect_ratio") or
            "original"
        )

    style_lines = [f"- {short_label}: {description}" for short_label, description in style_option_entries(analysis)]
    has_style_overrides = bool(style_lines)
    style_options = analysis.get("style_options") or {}
    size_override_value = style_options.get("size")
    has_pose_override = bool(style_options.get("pose") and style_options.get("pose") != "default")
    has_vertical_angle_override = style_options.get("angle") in {"high", "low", "eye"}
    has_wider_size_override = size_override_value in {"wide", "wider"}
    has_closer_size_override = size_override_value in {"close", "closer"}

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
    return {"crop_instruction": crop_instruction_block, "style_instruction": style_block}


def build_eye_repair_pass_prompt(gaze_items, blink_targets=(), blink_rules=()):
    """Prompt for the two-stage eye pass (pipeline._run_eye_repair_pass): a second,
    full-image edit call on Step 5's own output, asking for nothing but the
    approved gaze and/or blink repair(s).

    Step 5 bundles every fix into one call together with exposure/crop/skin/
    style/etc, and that call reliably applies the coarse, whole-frame changes
    but often no-ops or under-applies the fine, localized eye work. A second
    call whose prompt contains nothing but the eye repairs gives the model its
    full attention for just those changes, for every approved subject at once.

    blink_targets/blink_rules come from feature_resolver.build_blink_pass_targets,
    so an eyelid repair is worded exactly as a single-pass Step 5 would word it.

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
    blink_by_subject = {t.get("subject_index"): t for t in blink_targets}
    targets = []
    smiling = False
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
        blink = blink_by_subject.pop(item.get("_subject_index"), None) if item.get("_subject_index") is not None else None
        expression = item.get("_final_smile")
        expression_line = ""
        if blink:
            expression_line = "\n  Eyelid repair (render together with the gaze change as one eye edit):\n" + "\n".join(
                f"  {line}" for line in blink["lines"]
            )
        elif expression in ("gentle", "broad", "laughing"):
            smiling = True
            expression_line = (
                f"\n  Expression in this image: {expression} ({SMILE_TARGET_DESCRIPTIONS[expression]}). "
                f"The eye opening this expression gives overrides anything the adjustments above imply about how open the eyes are."
            )
        targets.append(
            f"- Subject: {label}\n"
            f"  Approximate location in frame: {json.dumps(box, ensure_ascii=False)}\n"
            f"  Required change: {edit_instruction}\n"
            f"  Eye/pupil adjustment: {eye_adjustment}\n"
            f"  Head/face adjustment: {head_face_adjustment}\n"
            f"  Sclera/catchlight adjustment: {sclera_catchlight_adjustment}\n"
            f"  Target result: {target_gaze_read}\n"
            f"  Approved repair method: {strategy_type} at {repair_strength} strength."
            f"{expression_line}"
        )
    for blink in list(blink_by_subject.values()):
        targets.append(
            f"- Subject: {blink['label']}\n"
            f"  Approximate location in frame: {json.dumps(blink['box'], ensure_ascii=False)}\n"
            "  Eyelid repair:\n" + "\n".join(f"  {line}" for line in blink["lines"])
        )
    targets_block = "\n".join(targets)
    smile_eye_rule = (
        " A smiling subject's eyelids keep the narrowing their smile's cheek lift gives them, on both eyes together; "
        "direct contact reads through the iris and catchlight visible inside that narrowed opening."
        if smiling else ""
    )
    has_gaze, has_blink = bool(gaze_items), bool(blink_targets)

    if has_gaze and has_blink:
        task = "carry out the eye repairs listed for each named subject below -- redirecting the gaze so it clearly and directly looks at the camera lens, and/or opening accidentally closed or half-closed eyelids"
        scope = "Scope is limited to each named subject's listed repairs: eye direction and the exact bounded head/face alignment for a gaze repair, eyelid opening for an eyelid repair."
        expression_scope = "For the named subject, the expression as it appears in this image -- mouth, eyebrows, and, where no eyelid repair is listed, how far the eyelids are open -- is part of the finished result: redirect the gaze inside that eye opening, and preserve identity, neck anatomy, torso, shoulders, and body pose."
    elif has_blink:
        task = "open the named subject's or subjects' accidentally closed or half-closed eyelids as described below"
        scope = "Scope is limited to the named subject's eyelid opening and the eye details that opening moves."
        expression_scope = "For the named subject, preserve the mouth, eyebrows, expression, identity, head angle, neck anatomy, torso, shoulders, and body pose."
    else:
        task = "redirect the named subject's or subjects' gaze so they clearly and directly look at the camera lens"
        scope = "Scope is limited to the named subject's eye direction and the exact bounded head/face alignment explicitly approved for that subject."
        expression_scope = "For the named subject, the expression as it appears in this image -- mouth, eyebrows, and how far the eyelids are open -- is part of the finished result: redirect the gaze inside that eye opening, and preserve crease shape, identity, neck anatomy, torso, shoulders, and body pose."
    head_rule = " The head/face may turn or tilt only when the approved repair method is eyes_plus_head_face_alignment; never rotate the shoulders or torso to simulate that head correction." if has_gaze else ""
    gaze_sections = f"""

Execution by method:
- eyes_plus_head_face_alignment: the head/face adjustment is required, not optional. Visibly but naturally turn and/or tilt the head/face toward the exact current lens, then align both pupils/irises to that lens. Do not merely shift the pupils while retaining an incompatible original head direction.
- eyes_only: keep the head/face angle unchanged and reposition only the pupils/irises. This is reserved for an extremely small miss with an already lens-aligned frontal head.

Go all the way to direct lens contact, not partway there. The completed correction must be immediately readable at normal photo-viewing size, without zooming into the face. A gaze that only moved closer to the camera without fully arriving is the same failure as not moving it at all -- "improved" is not the target, "looking straight into the lens" is. If you are unsure whether the pupils and, when required, the head/face have moved far enough, move them further within natural anatomical limits -- never by opening the eyelids wider -- rather than settling for a subtle, ambiguous shift.{smile_eye_rule} A clearly visible correction that unmistakably reads as direct eye contact is the right outcome; a timid one that still reads as near-camera or off-camera is not. Do not leave any named subject's gaze unchanged, and do not stop at a partial correction.""" if has_gaze else ""
    blink_section = "\n\nEyelid repair rules:\n" + "\n".join(blink_rules) if has_blink and blink_rules else ""

    return f"""You are editing the supplied image, which is already a finished, fully-graded photograph. Do not treat it as a draft.

This is the ONLY task for this edit: {task}. Nothing else about this image should change -- not exposure, color, crop, framing, background, clothing, pose, expression, or any person other than the ones named below.

{targets_block}

{scope} If the photo contains other people who are not named above, they are completely out of scope: do not touch their eyes, gaze, expression, pose, hair, or anything else about them, even if their gaze also looks imperfect. {expression_scope}{head_rule}{gaze_sections}{blink_section}

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
1. Fatal preservation failures: wrong subject/scene, identity or anatomy/shape/material damage, important subject/protected content accidentally missing, unnatural perspective, artificial borders, transparent/checkerboard padding, or environment rebuilt outside an approved distraction-removal footprint.
2. Major composition failures: final crop contradicts framing intent, cuts required subject/role cues awkwardly, leaves the frame cramped, makes the subject undersized, or keeps/removes context in a way that weakens the scene.
3. Moderate quality failures: visible overprocessing, weak color/light, unstable but not destructive alignment, unclear empty-space function, or distraction handling that is only partially successful.
4. Successful demo qualities: faithful scene, intentional framing, believable light/color, natural texture, useful breathing room/context, and no preservation damage.

Check dimensions:
- Subject and scene preservation: same person/object/moment/environment, no unwanted identity, geometry, material, or perspective changes.
- Framing and crop safety: the crop respects the intended framing. Penalize missing body/object/context only when it is required by that framing, role, action, interaction, product completeness, or scene story.
- Protected regions and role cues: required subject parts, structural edges, fine details, connected components, props, supporting subjects, and role-identifying details are not accidentally clipped or pressed against edges.
- Composition: aspect ratio, layout method, subject placement, empty space, breathing room, context, and visual balance feel intentional for the subject type and scene intent.
- Alignment and edges: credible horizontals/verticals feel stable unless slant has a clear purpose; output edges are fully photographic with no blank, transparent, checkerboard, or artificial canvas areas.
- Distraction and scene truth: carry out the approved removal or de-emphasis for each harmful distraction. A planned removal leaves no visible remnant and continues the subject/background plausibly within its footprint. Preserve contextual/neutral elements and the scene outside approved edit areas; do not rebuild, over-simplify, or change its style.
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
- If the original scene is visibly rebuilt outside an approved distraction-removal footprint, replaced, over-simplified, or converted into a different environment/style, score must be 3 or lower.
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
