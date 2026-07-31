import json
import logging

from config import STYLE_OPTION_INSTRUCTIONS, STYLE_OPTION_LABELS, STYLE_OPTION_VALUE_LABELS

logger = logging.getLogger(__name__)


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
    "crop": "reframing, straightening",
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
    "saturation_level": "saturation", "color": "saturation",
    "skin_tone": "skin", "complexion": "skin",
    "distractions": "distraction", "cleanup": "distraction",
    "cropping": "crop", "framing": "crop", "alignment": "crop", "straighten": "crop",
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


ANALYSIS_INSTRUCTION = """
You are a senior photography director, colorist, and high-end retoucher.
Analyze the uploaded photo and return only valid JSON. Keep the JSON keys in English. Every user-visible string value must be Traditional Chinese.

Demo goal:
- Help a user understand how their photo can be improved.
- Recommend a tasteful retouch that preserves the same subject, same scene, and same moment.
- Decide whether a simple crop/reframe is useful.
- Separate local deterministic corrections from AI image editing needs.

Hard limits:
- Do not change the subject into something else.
- Do not add text, logos, watermarks, extra people, fantasy elements, or a new scene.
- Do not rebuild, replace, or materially simplify the original environment. Preserve scene truth: recognizable background structure, surface material, perspective, lighting direction, and real spatial relationships.
- Cleanup means reducing distraction within the same scene, not turning a casual/lifestyle/documentary scene into a studio, catalog, or different location unless the user explicitly asks for that.
- For people, preserve identity, face, body shape, pose, and natural skin texture.
- For products, food, pets, architecture, and landscapes, preserve shape, material, and scene truth.

Return exactly this JSON shape:
{
  "photo_summary": "一句話描述照片內容與整體觀感。",
  "subject_category": "人物 / 食物 / 產品 / 風景 / 建築 / 寵物 / 生活紀錄 / 其他",
  "scene_category": "室內 / 戶外 / 街拍 / 商業 / 旅行 / 日常 / 其他",
  "main_subject": "主要主體是誰或什麼。",
  "scene_type": "更具體的場景類型。",
  "aesthetic_diagnosis": "說明照片目前不夠精緻的原因。",
  "detected_issues": ["2 到 6 個具體問題。"],
  "edit_philosophy": "整體修圖哲學。",
  "edit_intent": "這次修圖要達成的結果。",
  "composition_mood": "完成後希望呈現的氛圍。",
  "composition_intent": "這張照片最適合的構圖意圖，例如主體展示、生活敘事、動作瞬間、環境紀實、圖形平衡、負空間、親密肖像等。",
  "framing_intent": "取景意圖，例如 detail / close-up / upper-body / half-body / three-quarter / full-body / environmental / object-complete / context-preserving。",
  "layout_method": "center / thirds / symmetry / diagonal / vertical-horizontal / leading-lines / pattern / negative-space，加上中文理由。",
  "portrait_framing": "若不是人像可寫不適用；若是人像，說明 close-up, upper-body, half-body, full-body 等框法。",
  "composition_strategy": "構圖與裁切策略。",
  "visual_balance_strategy": "左右與上下視覺重量如何處理。",
  "empty_space_strategy": "畫面空白、留白或大面積背景的構圖功能；若沒有功能，說明如何縮減或重新平衡。",
  "alignment_strategy": "水平線、垂直線與透視穩定性；說明是否需要輕微旋轉或透視校正，或為何保留傾斜。",
  "distraction_strategy": "裁切後重新評估背景/前景元素：哪些是真正干擾、哪些是場景脈絡、哪些只是中性背景，以及如何處理。",
  "scene_preservation_strategy": "如何保留原始場景真實性、背景結構、材質、透視與光線關係。",
  "roi_anchor": "主視覺焦點應落在哪裡，以及為什麼。",
  "recommended_aspect_ratio": "original / 1:1 / 4:5 / 3:4 / 5:4 / 4:3 / 3:2 / 16:9",
  "aspect_ratio_reason": "為什麼這個輸出比例最適合主體、陪襯元素、留白與畫面意圖。",
  "subjects": [
    {
      "label": "主體描述,例如 左二女性 / 中間男性 / 蛋糕。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "prominence": 1,
      "is_person": true,
      "reason": "為何突出:最大 / 最中央 / 最清晰 / 互動焦點。"
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
  "perception_scores": {
    "clarity": 7,
    "noise": 3,
    "exposure": 6,
    "color_temperature": 6,
    "layout": 6,
    "lighting": 6,
    "color_harmony": 6,
    "aesthetic_finish": 6
  },
  "tool_strategy": "哪些由本機裁切、曝光、白平衡、對比處理，哪些才交給 AI。",
  "lighting_strategy": "光線與明暗層次調整方向。",
  "memory_color_strategy": "膚色、天空、綠色、食物、材質等記憶色策略。膚色以還原真實為目標，不要以「更暖、更紅潤、更健康」為方向。",
  "restoration_needs": ["可由傳統影像處理改善的項目。"],
  "evaluation_criteria": ["完成後用來判斷成功的標準。"],
  "crop_recommendation": "若需要裁切，說明裁哪裡與為什麼；不需要則說明保留原構圖。",
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
  "preserve_rules": ["必須保留的內容。"]
}

correction_plan, correction_axes, correction_inverse, and retouch_recipe must describe the exact same set of adjustments, in the same order, one-to-one -- same number of entries in all four. Write correction_plan first as the precise technical plan the editing model will follow; then write retouch_recipe as a simplified, user-facing paraphrase of each corresponding correction_plan item -- not a separate or reworded set of steps.

correction_inverse[i] is what the editing model will be told instead if the user unchecks correction_plan[i] on the review screen. Write it as a positive preservation instruction -- name what to reproduce from the original -- rather than as a prohibition, and hold it to the EXACT same scope as the item it inverts:
- If the item only brightens the subject, the inverse pins the SUBJECT's brightness and says nothing about the rest of the frame. Pinning the whole frame there would suppress corrections the user did approve.
- If the item bundles several changes, the inverse must cover every one of them.
- Name the same region, element, or 整體 that the item named.
- It has to stand on its own, because the editing model may be shown it without the original item next to it.
This is the only thing standing between an unchecked item and the editing model carrying it out anyway -- the rest of this analysis still describes the problem the item was going to fix, and the editing model reads all of it. A vague inverse fails to prevent the change; an over-broad one suppresses work the user wanted.

""" + f"""correction_axes[i] is the machine-readable tag set for correction_plan[i]: which properties of the photograph that item changes. Pick every axis the item actually moves, from exactly this vocabulary, and nothing else: {_AXIS_VOCABULARY}. Most items need one or two axes; use the narrowest set that is true. The example above shows an item that raises overall brightness and recovers the bright end, hence ["exposure", "highlight"]. These tags let the user's recipe choices switch the corresponding instructions off precisely, so an inaccurate tag causes the wrong part of the edit to be suppressed.
""" + """
Analysis responsibilities:
- Observe the image, identify semantic roles, and describe why the photo can improve.
- Define the main subject, useful supporting elements, optional context, harmful distractions, ROI, subject_extent_box, and protected_regions.
- Decide the composition intent and framing intent before proposing any crop. Centering, thirds, symmetry, leading lines, and negative space are candidate strategies, not fixed rules.
- Size correction_plan to what the photo actually needs. A one- or two-item plan for an already well-balanced photo is a correct and valued answer; stay near the low end of the allowed range unless the photo genuinely needs more. Listing a correction that the photo does not need is a worse outcome than listing fewer, because the editing model will carry it out and push the result past what the photo called for. This applies especially to color: do not add an exposure, white-balance, saturation, or warmth item unless you can point to a specific visible problem.
- Skin should read naturally in the finished photo -- pushed neither toward orange or ruddy red, nor toward yellow or sallow. In particular, do not write a correction_plan item whose goal is warmer, rosier, or healthier-looking skin; if skin does look wrong, name which direction it is off.
- Propose AI-authored crop_candidates only when reframing may improve the photo. Each candidate must explain its composition intent and tradeoffs. Include a safer/context-preserving option when the decision is ambiguous, and include original-frame when preserving the original may be best.
- Do not score output quality here. Do not write QA penalties here. Leave candidate ranking to the crop judge and final scoring to QA.

Coordinate rules:
- crop_box, roi_box, subject_extent_box, and protected_regions use normalized original-image coordinates. x/y are top-left, w/h are width/height.
- roi_box is the focal area. subject_extent_box is the semantic subject area required by the intended framing; it is not automatically every visible body part, foreground object, furniture piece, or background element.
- subjects lists the candidate main subjects (people, animals, products, food, objects) ordered so index 0 is the photo's true main subject -- what the photo is actually about, consistent with main_subject, roi_box, and the composition intent. prominence means semantic importance as the subject, NOT which element is merely brightest, most colorful, or most saturated. Incidental background or decor -- flowers, plants, props, tableware, posters, scenery -- must rank lower than the real subject even when it is visually eye-catching; e.g. in a food photo the dish is the subject, not a bright vase of flowers behind it. Always output at least one entry, even for a single-subject photo.
- Each subjects[].box must tightly frame EXACTLY ONE individual person/animal/object, never a group. In a multi-person or multi-object scene, output one separate entry per individual, each with its own tight box; do NOT return a single box that spans several subjects or most of the frame. If there are four people, subjects must contain four entries, each boxing one person -- index 0 is only the single most prominent one, not all four. This holds even when main_subject or photo_summary describe the group as a whole; subjects is always per-individual so a downstream close-up can isolate exactly one.
- protected_regions are important subject parts, structural edges, fine details, connected components, role cues, props, or supporting elements needed for the intended story, action, presentation, or scene truth.
- If no reframing is beneficial or the crop is almost full frame, set recommended_aspect_ratio to "original" and crop_box.enabled to false.
- The output does not need to preserve the original aspect ratio. Choose aspect ratio from subject type, scene intent, supporting elements, empty-space function, visual balance, and breathing room.
- Set crop_risk.needs_validation to true when composition is ambiguous, crop is aggressive, ROI is near an edge, protected regions are near edges, framing could be misread, or alignment/context tradeoffs need judgment.

Composition decision framework:
- For completeness-first presentation, catalog-like, symmetrical, circular, stacked, shape-critical, food/product/object/still-life images, centered or near-centered balance is often competitive.
- For lifestyle, story, portrait, street, travel, action, gaze, gesture, interaction, leading lines, asymmetry, or intentional negative space, off-center, thirds, diagonal, symmetry, or context-preserving framing may be stronger.
- Empty space must have a visible job: direction, story, scale, calmness, graphic balance, leading lines, room for action, environmental context, rhythm, or intentional negative space. Preserve generous breathing room when it serves those jobs; reduce or rebalance empty/background areas when they do not.
- Reassess distractions after the proposed crop. A visible element may be harmful, contextual, or neutral depending on the final frame. Preserve contextual/neutral elements and reduce emphasis through subtle retouching rather than rebuilding the scene.
- Check alignment when credible horizontal/vertical references exist. Correct unintended tilt or keystone only when it feels unstable or physically implausible; preserve intentional slant only when it has a clear visual purpose.
- Preserve scene truth: background structure, surface material, perspective, lighting direction, spatial relationships, and real-world context. Do not turn casual, lifestyle, documentary, travel, street, or memory photos into studio/catalog scenes unless requested.
- For people and animals, the face ROI alone is not automatically the whole subject. Protect the semantic subject extent and role cues required by the framing. Partial-person crops are acceptable only when intentional, clean, and supportive of the scene.
- For action, eating, gesture, interaction, or expression-driven moments, protect expression, hands, held objects, interaction points, and relevant context. Preserve lower body, furniture, foreground objects, or peripheral context only when they support posture, story, scale, balance, or scene intent.
- For fashion, posture, full-body portrait, dance, sports, product shape, architecture, or object-completeness tasks, preserve the full visible form more conservatively.
- Judge real-image breathing room from actual photo content, not transparent padding, UI checkerboard, or empty canvas.
"""


def build_analysis_instruction(aspect_ratio_pref="auto"):
    """ANALYSIS_INSTRUCTION, plus a hard aspect-ratio directive when the user
    explicitly asked to keep the original photo's proportions -- the schema's
    own free-choice recommended_aspect_ratio only applies to "auto"."""
    if aspect_ratio_pref != "original":
        return ANALYSIS_INSTRUCTION
    return ANALYSIS_INSTRUCTION + """
User aspect ratio requirement:
- The user explicitly requested that the final output keep exactly the same aspect ratio as the original photo. Set recommended_aspect_ratio to "original". If crop_box.enabled is true, crop_box.aspect_ratio must also be "original" (or left blank) -- do not choose a crop_box or crop_candidates aspect_ratio that changes the original proportions. Any cropping must stay within the original image's aspect ratio.
"""


# The light/color direction shared by both retouch modes. The sentence this
# replaced ("Improve the existing photograph through light, contrast, color
# harmony, ...") was too abstract to steer an image model: with no definition of
# "better" and no stopping condition, the model fell back on its training prior
# for an appealing photo -- more warmth, more saturation -- which is exactly the
# overshoot we see. So name the craft, and say where the image's presence should
# come from (light first, color second) rather than capping how much it may move.
COLOR_CRAFT_DIRECTION = (
    "Improve the existing photograph the way a skilled colorist would: open the shadows enough to reveal "
    "detail without flattening them, hold the highlights so they roll off instead of clipping, and build "
    "depth through tonal separation between subject, midground, and background. Draw the image's presence "
    "primarily from light -- exposure, contrast placement, micro-contrast -- and only secondarily from "
    "color; a modest saturation or warmth adjustment is welcome where it helps, but it should refine the "
    "palette the photograph already has rather than impose a new one."
)

# Stopping condition, not a priority -- hence its own block at the very end of
# the edit prompt rather than another numbered item, and phrased as properties
# the model can verify rather than "don't overdo it".
#
# Every clause compares against the original on purpose. An earlier version
# mixed judgment types -- it asked whether surfaces "that should be neutral"
# carried a tint, an absolute test -- and that misfired on photos whose grade is
# deliberately cool: the model read the intentionally cool whites as a defect,
# neutralized them, and warmed the whole frame. Same root cause as it desaturating
# stained glass and lightening deep skin: with an external notion of "correct"
# color it will edit toward that instead of toward a better rendering of this
# photograph. One comparative rule covers those cases without having to enumerate
# the subject matter they involve.
COLOR_GRADE_CHECK = """Before finishing, compare your result against the original. The original defines what the colors are meant to be; your job is to render them better, not to change what they are:
- a cool or warm character that was clearly deliberate is still there, pointing the same way;
- what was vividly colored is at least as vivid; what was neutral is still neutral;
- skin keeps each person's own depth and undertone, neither lightened nor flattened.
Improving exposure, cleanliness, and separation is the work. Shifting the photograph's color character is not -- that reads as a filter rather than a finish."""


# ── Rejected-axis gating ──────────────────────────────────────────────────────
#
# Dropping an unchecked item from correction_plan is not enough to stop the edit
# model from making that change, because the same change is requested in several
# other places the checkbox never touched: the analysis prose (aesthetic_diagnosis,
# detected_issues, lighting_strategy, memory_color_strategy) still describes the
# problem, the preset still asks for a good finish, and COLOR_CRAFT_DIRECTION /
# COLOR_GRADE_CHECK ask for shadow opening, exposure work and highlight control
# by name. One negative sentence loses that argument on volume.
#
# So each correction_plan item carries axis tags (correction_axes), and unchecking
# it removes that axis's "do X" clauses from the light/color direction, the grade
# check, and the two numbered priorities that ask for distraction cleanup and
# horizon straightening. Removal is the whole job: saying what to preserve
# instead is correction_inverse's, written per item by the analysis model so it
# carries that item's scope (see _build_rejected_lines). Several axes -- skin,
# sharpen, other -- gate nothing today and are kept in the vocabulary only
# because the model reaches for them naturally; an axis that gates nothing costs
# nothing.
#
# A wrong tag degrades to the pre-feature behavior rather than breaking: the
# clause stays and correction_inverse still holds the line. The vocabulary lives
# at the top of this file (AXIS_DEFINITIONS), since ANALYSIS_INSTRUCTION has to
# hand the same list to the analysis model.

# The light/color direction, decomposed so that with no rejected axis it
# reassembles into COLOR_CRAFT_DIRECTION verbatim (asserted below) -- that text is
# tuned, and a rewrite of the default path is not what this change is for.
# (axis, clause) for the three craft clauses of sentence 1, in order.
_CRAFT_CLAUSES = (
    ("shadow", "open the shadows enough to reveal detail without flattening them"),
    ("highlight", "hold the highlights so they roll off instead of clipping"),
    ("contrast", "build depth through tonal separation between subject, midground, and background"),
)
_CRAFT_LEAD = "Improve the existing photograph the way a skilled colorist would"
_CRAFT_LIGHT_FIRST = (
    "Draw the image's presence primarily from light -- exposure, contrast placement, micro-contrast -- "
    "and only secondarily from color"
)
_CRAFT_COLOR_TAIL = (
    "a modest {allowed} adjustment is welcome where it helps, but it should refine the "
    "palette the photograph already has rather than impose a new one."
)


def _join_clauses(clauses):
    """Oxford-comma join, matching the hand-written original's punctuation."""
    if len(clauses) == 1:
        return clauses[0]
    if len(clauses) == 2:
        return f"{clauses[0]} and {clauses[1]}"
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


def build_color_craft_direction(rejected_axes=()):
    rejected = set(rejected_axes or ())
    craft = [clause for axis, clause in _CRAFT_CLAUSES if axis not in rejected]

    sentences = []
    if craft:
        sentences.append(f"{_CRAFT_LEAD}: {_join_clauses(craft)}.")
    else:
        # All three tonal clauses rejected: keep the framing (retouch, not
        # reinvention) but stop naming tonal work as the job.
        sentences.append(f"{_CRAFT_LEAD}, within the preservation limits stated below.")

    # "saturation or warmth" collapses to whichever of the two survives; with
    # both rejected the color sentence goes away entirely.
    allowed_color = [
        label for axis, label in (("saturation", "saturation"), ("white_balance", "warmth"))
        if axis not in rejected
    ]
    color_tail = _CRAFT_COLOR_TAIL.format(allowed=" or ".join(allowed_color)) if allowed_color else ""

    if "exposure" not in rejected:
        sentences.append(f"{_CRAFT_LIGHT_FIRST}; {color_tail}" if color_tail else f"{_CRAFT_LIGHT_FIRST}.")
    elif color_tail:
        sentences.append(color_tail[0].upper() + color_tail[1:])

    return " ".join(sentences)


# The default path must stay byte-identical to the tuned constant; the
# decomposition above is easy to break with a stray comma otherwise.
assert build_color_craft_direction() == COLOR_CRAFT_DIRECTION, "COLOR_CRAFT_DIRECTION decomposition drifted"

# A clause tagged with an axis the vocabulary no longer has would never be gated.
assert all(axis in CORRECTION_AXES for axis, _ in _CRAFT_CLAUSES), "unknown axis in _CRAFT_CLAUSES"


# Same idea for the closing grade check: its last line names exposure and
# cleanliness as "the work", which contradicts a rejection of those axes.
_GRADE_CHECK_HEAD, _GRADE_CHECK_LAST = COLOR_GRADE_CHECK.rsplit("\n", 1)
_GRADE_WORK_ITEMS = (("exposure", "exposure"), ("distraction", "cleanliness"), ("contrast", "separation"))
assert all(axis in CORRECTION_AXES for axis, _ in _GRADE_WORK_ITEMS), "unknown axis in _GRADE_WORK_ITEMS"


def build_color_grade_check(rejected_axes=()):
    rejected = set(rejected_axes or ())
    if not rejected.intersection(axis for axis, _ in _GRADE_WORK_ITEMS):
        return COLOR_GRADE_CHECK
    work = [label for axis, label in _GRADE_WORK_ITEMS if axis not in rejected]
    opening = (
        f"Improving {_join_clauses(work)} is the work."
        if work else
        "Rendering this photograph faithfully, within the preservation limits above, is the work."
    )
    return (
        f"{_GRADE_CHECK_HEAD}\n{opening} Shifting the photograph's color character is not "
        "-- that reads as a filter rather than a finish."
    )


def resolve_rejected_axes(analysis):
    """Axes the user switched off, as tagged on the unchecked correction_plan items.

    An axis only counts as rejected when NO kept item still needs it: a kept
    "darken the busy background" and a rejected "brighten the whole frame" can
    both carry `contrast`, and locking contrast to the original there would break
    the correction the user did approve. Falls back to an empty list whenever the
    tags are missing or untrustworthy, which leaves the previous behavior --
    rejected item text only -- intact.
    """
    rejected = _axis_set(analysis.get("rejected_axes"))
    if not rejected or not _axes_aligned(analysis):
        return []
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
        return []
    return [axis for axis in CORRECTION_AXES if axis in rejected and axis not in kept]


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


def build_user_instruction_review_prompt(user_direction, analysis):
    preserve_rules = "; ".join(analysis.get("preserve_rules") or [])
    head = f"""
You are reviewing a user's instruction for a faithful photo retouching agent.
The user's explicit instruction has priority over automatic aesthetic improvements.
For aspects the user did not mention, the agent may still make conservative complementary improvements if they do not conflict with the user's goal.

Do not treat user intent as a simple append-only note. Convert it into practical editing priorities for analysis, crop, edit, and QA.
If the user asks for a risky transformation, preserve the user's goal as much as possible while stating what must be softened to keep the result faithful.

Priority model:
1. Explicit user requirements and prohibitions have highest priority.
2. Agent may add low-risk complementary improvements for unspecified aspects.
3. Avoid risky transformations unless explicitly requested; if requested, describe the conflict and safer interpretation.

Original user instruction:
{user_direction or ""}

Photo context:
- Summary: {analysis.get("photo_summary") or ""}
- Main subject: {analysis.get("main_subject") or ""}
- Scene type: {analysis.get("scene_type") or ""}
- Edit intent: {analysis.get("edit_intent") or ""}
- Composition intent: {analysis.get("composition_intent") or ""}
- Crop recommendation: {analysis.get("crop_recommendation") or ""}
- Preserve rules: {preserve_rules}

Return only valid JSON. User-visible values must be Traditional Chinese.
"""
    schema = """{
  "has_user_direction": true,
  "user_intent_summary": "一句話說明使用者真正想要的結果。",
  "normalized_user_direction": "可直接交給修圖與裁切流程的整理後指令。",
  "explicit_priorities": ["使用者明確要求必須優先處理的事項。"],
  "explicit_prohibitions": ["使用者明確要求不要做的事項。"],
  "unspecified_agent_freedom": ["使用者未提到但 Agent 可以保守補完的面向。"],
  "crop_or_framing_requested": false,
  "crop_guidance": "user 指令對裁切/構圖的影響；若未提到裁切，說明 Agent 可以保守判斷。",
  "qa_focus": ["QA 應特別檢查 user 目標是否達成的面向。"],
  "conflicts": ["若 user 指令與保真/安全邊界衝突，列出衝突。"],
  "warning": "若有被降級或轉譯的部分，簡短說明；沒有則空字串。"
}"""
    return head + schema


def _build_rejected_lines(analysis, rejected_corrections):
    """(lines, has_inverse): each rejected item, with its preservation instruction.

    The instruction is correction_inverse, written by the analysis model next to
    the item it inverts, so it carries that item's scope: "brighten the subject"
    inverts to pinning the SUBJECT's brightness. A generic per-axis rule would
    have to say "keep the photograph's global exposure" and would suppress a
    global lift the user never objected to.

    Without it -- an analysis from before the field existed, or a model that
    skipped it -- the items go in as bare text, which is what the negative block
    said before correction_inverse and still beats emitting a mismatched pairing.
    """
    inverses = analysis.get("rejected_preserve") or []
    if len(inverses) != len(rejected_corrections):
        if inverses:
            logger.warning(
                "rejected_preserve does not match rejected_corrections (%d vs %d); "
                "listing rejected items without their preservation instructions",
                len(inverses), len(rejected_corrections),
            )
        return "\n".join(f"- {item}" for item in rejected_corrections), False

    return "\n".join(
        f"- {item}\n  Instead: {inverse}"
        for item, inverse in zip(rejected_corrections, inverses)
    ), True


def _build_crop_instruction_block(crop_plan):
    if not crop_plan:
        return (
            "No crop. Treat the supplied image boundaries as final. Do not crop, "
            "reframe, expand canvas, add borders, or invent empty side bands."
        )

    x, y, w, h = crop_plan["x"], crop_plan["y"], crop_plan["w"], crop_plan["h"]
    aspect = crop_plan.get("aspectRatio") or crop_plan.get("aspect_ratio") or "keep the resulting region's natural ratio"
    left_pct, top_pct = x * 100, y * 100
    right_pct, bottom_pct = (1 - (x + w)) * 100, (1 - (y + h)) * 100

    return f"""Mandatory crop region, decided by the crop-validation step and not up for reinterpretation. Coordinates are normalized (0-1) against the full original image, origin at the top-left:
- x={x:.3f}, y={y:.3f}, w={w:.3f}, h={h:.3f} -- i.e. trim approximately {left_pct:.0f}% off the left edge, {top_pct:.0f}% off the top edge, {right_pct:.0f}% off the right edge, and {bottom_pct:.0f}% off the bottom edge of the original image.
- Target output aspect ratio: {aspect}.
Perform exactly this crop/reframe as part of your edit. This is a hard constraint, not a stylistic suggestion: do not pick a different crop, do not soften or ignore it, and do not fall back to the full original frame. Do not include any content from outside this region in the final output. Within the kept region, still honor the ROI, subject extent, and protected regions below."""


def build_edit_prompt(analysis, preset, preset_prompt, user_direction, crop_plan, revision_feedback=None, aspect_ratio_pref="auto"):
    rejected_axes = resolve_rejected_axes(analysis)
    color_craft_direction = build_color_craft_direction(rejected_axes)
    color_grade_check = build_color_grade_check(rejected_axes)

    user_instruction = analysis.get("user_instruction") or {}
    effective_user_direction = (
        user_instruction.get("normalized_user_direction") or user_direction or ""
    )

    detected_issues = "; ".join(analysis.get("detected_issues") or [])
    correction_plan = "; ".join(analysis.get("correction_plan") or [])
    preserve_rules = "; ".join(analysis.get("preserve_rules") or [])
    roi_box = json.dumps(analysis.get("roi_box") or {}, ensure_ascii=False)
    subject_extent_box = json.dumps(analysis.get("subject_extent_box") or {}, ensure_ascii=False)
    protected_regions = json.dumps(analysis.get("protected_regions") or [], ensure_ascii=False)
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

    subject_box_for_size = analysis.get("subject_extent_box") or analysis.get("roi_box") or {}
    try:
        subject_w = float(subject_box_for_size.get("w") or 0)
        subject_h = float(subject_box_for_size.get("h") or 0)
        subject_area = subject_w * subject_h if subject_w and subject_h else 0
    except (TypeError, ValueError):
        subject_w = subject_h = subject_area = 0

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

    if size_ratio_block and subject_w and subject_h:
        size_ratio_block += (
            f"Original subject extent estimate: width={subject_w:.2f}, height={subject_h:.2f}, area={subject_area:.2f} of the frame. "
            "Use these values as a visual guide, not a pixel-perfect measurement. "
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

    correction_plan_block = ""
    if analysis.get("correction_plan"):
        correction_plan_block = f"""User-approved correction plan (highest priority -- this list has already been reviewed and confirmed by the user; apply every item below and do not add corrective or stylistic changes beyond this list, the style overrides above, and the user's extra direction, even if other analysis notes above suggest more):
{correction_plan}"""

    rejected_corrections = analysis.get("rejected_corrections") or []
    negative_block = ""
    if rejected_corrections:
        # Phrased as preservation, not prohibition. "Do not brighten" still puts
        # brightening in front of the model as the thing under discussion, and an
        # image model given a described change tends to produce a softened version
        # of it; "reproduce the original's exposure" gives it a target it can
        # actually render instead.
        rejected_lines, has_inverse = _build_rejected_lines(analysis, rejected_corrections)
        # The header has to match what the list below actually contains -- promising
        # an "Instead" line that isn't there reads as a truncated prompt.
        instead_intro = (
            'so each one\'s "Instead" line is now the instruction for that aspect of the photograph'
            if has_inverse else
            "so the corresponding properties of the photograph must come through unchanged from the original"
        )
        instead_scope = (
            'Each "Instead" line is bounded to the region or property its item named; it does not license '
            "changes elsewhere, and it does not override the approved correction plan. "
            if has_inverse else
            "Each item is bounded to the region or property it names; leaving it alone does not license changes "
            "elsewhere, and does not override the approved correction plan. "
        )
        negative_block = f"""User-rejected corrections (highest priority). The user reviewed the suggested recipe and unchecked the items below, {instead_intro}. This overrides everything above that still argues for the change -- the photo analysis, the aesthetic diagnosis, the detected issues, the lighting and color strategy notes, and the preset style direction were all written before the user's review and were not updated by it:
{rejected_lines}

{instead_scope}Reproducing the original faithfully in these respects is the correct outcome, not a missed opportunity. A result that improves them anyway has failed this edit, however good it looks."""

    # Skin is the one color in a photo that viewers have an absolute reference
    # for, so a global grade that reads fine on the background can still fail on
    # skin. Trigger on is_person too, not just subject_category: a 生活紀錄 /
    # 旅行 / 街拍 photo still has faces that need protecting.
    subjects = analysis.get("subjects") or []
    has_person = analysis.get("subject_category") == "人物" or any(
        isinstance(s, dict) and s.get("is_person") for s in subjects
    )
    skin_block = ""
    if has_person:
        skin_block = """Skin-tone priority (this photo contains people):
- Skin is the reference point for every color decision in this edit. Judge white balance, saturation, warmth, and contrast by how the skin reads, not by how the background reads.
- Preserve the original hue relationship between skin and the scene's neutral surfaces (white walls, white clothing, paper, teeth, eye whites). Do not shift skin hue away from the rest of the frame.
- Veto rule: if a global color, warmth, or saturation adjustment improves the background but pushes skin toward orange, sallow yellow, sunburnt red, grey/lifeless, or magenta, abandon that global adjustment and treat the background locally instead.
- Keep natural tonal variation within skin. Forehead, cheeks, nose, and shadowed areas must not flatten into one uniform tone.
- This bounds the color portion of every instruction above, including the correction plan, the preset style direction, and any lighting override. A result whose overall grade looks appealing but whose skin looks unnatural is a failure, even if every other instruction was followed."""

    revision_block = ""
    if revision_feedback:
        revision_block = f"""Revision feedback from the previous QA:
{revision_feedback}

This is a retry. Directly fix the QA failure. Do not repeat the failed crop/edit behavior. Preserve the subject, required framing, scene context, and protected details more conservatively than the previous output."""

    framing_focus = analysis.get("_framing_focus") or {}
    focus_block = ""
    if framing_focus.get("size") == "closer":
        focus_label = framing_focus.get("label") or "the single most prominent subject"
        focus_block = f"""Single-subject close-up override (highest framing priority):
Feature one subject: {focus_label}. The photo summary and scene description above may mention several people or elements -- for this close-up, every other person, object, or element outside the mandatory crop region is intentionally and correctly cropped out. Do NOT reintroduce them. Fill the frame with the chosen subject; preserve that subject's identity, expression, and key details."""

    if has_wider_size_override:
        crop_instruction_block = (
            "Wider framing requested. Do not treat the original image boundaries as final. "
            + size_ratio_block +
            "Create a wider same-scene composition where the subject appears smaller and more surrounding environment is visible. "
            "If a crop plan exists, use it only as a subject-safety reference, not as a hard crop that prevents widening. "
            "Canvas expansion/outpainting is allowed only to continue the same real scene with matching perspective, lighting, depth, materials, and background logic. "
            "Do not add blank borders, transparent/checkerboard padding, artificial empty bands, stretched edges, or unrelated scenery."
        )
    else:
        crop_instruction_block = _build_crop_instruction_block(crop_plan)
    crop_priority_line_style = (
        "Create the requested wider same-scene framing. The original image boundaries are not final for wide/wider size overrides; expand/outpaint plausible same-scene context if needed. "
        + size_ratio_block +
        "Wide should be a moderate pull-back; wider should be a much stronger pull-back with a clearly smaller subject and more environmental context. "
        "Never add blank borders, transparent/checkerboard padding, artificial empty side bands, stretched edges, or unrelated scenery."
        if has_wider_size_override else
        (
            "Perform the mandatory crop specified in the Crop instruction above as part of this edit. "
            "Beyond that exact region, do not add blank borders, transparent/checkerboard padding, or "
            "artificial empty side bands, unless a selected closer framing-size override explicitly asks for closer framing within the cropped region."
            if crop_plan else
            "Treat the supplied image boundaries as final unless a selected closer framing-size override explicitly asks for closer framing. "
            "Never add blank borders, transparent/checkerboard padding, or artificial empty side bands."
        )
    )
    crop_priority_line_faithful = (
        "Perform the mandatory crop specified in the Crop instruction above as part of this edit. Beyond "
        "that exact region, do not additionally expand canvas, add borders, create transparent/checkerboard "
        "padding, or invent empty side bands."
        if crop_plan else
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
    alignment_priority = (
        "If credible horizontal/vertical references are unintentionally unstable, correct them subtly. Preserve "
        "intentional slant only when it clearly supports motion, intimacy, tension, or expression."
        if "crop" not in rejected_axes else
        "Preserve the original alignment exactly. Do not level horizons, straighten verticals, correct tilt, or "
        "adjust perspective, even where they read as unstable -- the user removed that from this edit's plan."
    )

    if has_style_overrides:
        opening_preservation_rule = (
            "Preserve the subject identity, recognizable person/object, clothing, location, environment, props, "
            "and photographic realism. This is a same-scene reshoot: selected pose, angle, lighting, or framing-size "
            "overrides are allowed and expected to visibly change those selected aspects."
        )
        edit_mode_block = """Edit mode:
STYLE OVERRIDE MODE. The user selected one or more pose/angle/lighting/framing options, so treat this as a controlled same-scene reshoot rather than a conservative retouch only.

Priority order:
1. Preserve safety, identity, same subject, same location, same environment, same clothing, same props, and photographic realism.
2. Apply user-selected style overrides visibly and deliberately for the selected aspects.
3. Apply the user-approved correction plan and user's extra direction.
4. Use the preset style as a supporting finish.
5. Apply default preservation rules only to aspects not selected by the user."""
        retouch_priorities = f"""Retouch priorities:
1. {crop_priority_line_style}
2. Preserve the original subject identity, semantic subject extent, protected regions, role cues, product/object identity, scene structure, and believable spatial continuity. For selected style overrides, allow the corresponding pose, camera viewpoint, lighting direction, or framing size to change visibly while keeping the scene physically plausible.
3. Apply every user-selected style override in full. Angle overrides should clearly move the camera to the selected viewpoint by following that angle's own instruction above; pose overrides must replace the original pose with clearly different body language, changing posture, gesture, hand placement, body direction, weight shift, or head direction; lighting overrides should visibly change illumination mood; framing-size overrides should visibly change subject scale and environmental context. For wide/wider, follow the quantified subject-size target: wide should reduce the subject's visible frame ratio by about 20%, while wider should reduce it by about 40%. The scene must expand with plausible same-location context, not just a tiny crop or minor zoom change.
4. {color_craft_direction} Apply this only where it supports the selected overrides and the approved correction plan.
5. {distraction_priority}
6. Preserve natural texture in skin, hair, fabric, food, products, architecture, shadows, and background materials. Avoid plastic smoothing, synthetic sharpening, or heavy filters.
7. Keep unselected aspects faithful to the original. If angle is not selected, preserve the original camera viewpoint; if lighting is not selected, preserve the original lighting logic; if pose is not selected, preserve the original pose; if size is not selected, preserve the original framing size.
8. Keep the final result faithful to the same subject, identity, location, environment, clothing, props, and photographic realism. When an angle override is selected, change the camera viewpoint clearly as described in that angle's own instruction above, while keeping a believable same-scene result.
9. The user-approved correction plan and any user-selected style overrides above are the definitive specification of what to change -- apply them in full and do not perform additional corrections beyond them. For the user-rejected corrections above, reproduce the original's version of those properties -- that is the required output, not merely a change you may skip."""
    else:
        opening_preservation_rule = (
            "Preserve the original subject, identity, pose, camera angle, scene, product shape, clothing, "
            "facial structure, body shape, and important details."
        )
        edit_mode_block = """Edit mode:
FAITHFUL RETOUCH MODE. The user did not select any style override, so keep the original pose, camera viewpoint, lighting logic, framing size, scene, and moment. Improve only the approved correction plan, preset finish, and user's extra direction."""
        retouch_priorities = f"""Retouch priorities:
1. {crop_priority_line_faithful}
2. Preserve the original subject, semantic subject extent, protected regions, role cues, interaction points, product/object geometry, scene structure, perspective, lighting direction, and spatial relationships.
3. {color_craft_direction}
4. {distraction_priority}
5. Preserve natural texture in skin, hair, fabric, food, products, architecture, shadows, and background materials. Avoid plastic smoothing, synthetic sharpening, or heavy filters.
6. Respect the selected composition. Preserve meaningful breathing room and empty space when it supports direction, story, scale, calmness, graphic balance, rhythm, environment, or subject dignity.
7. {alignment_priority}
8. Keep the final result faithful: the same moment, same camera viewpoint, same environment, and same subject, finished like a skilled retoucher handled it.
9. The user-approved correction plan is the definitive specification of what to change -- apply it in full and do not perform additional corrections beyond it, the user's extra direction, and the preset finish. For the user-rejected corrections above, reproduce the original's version of those properties -- that is the required output, not merely a change you may skip."""

    # The diagnosis lines below are the loudest remaining voice for a rejected
    # correction, and they sit ~40 lines above the negative block. Caveat them
    # where they are read, not only where they are overridden.
    analysis_header = (
        "Photo analysis (written during automatic analysis, BEFORE the user reviewed the recipe -- "
        "wherever it diagnoses a problem the user later chose not to fix, the user's decision wins and "
        "the diagnosis is stale):"
        if rejected_corrections else
        "Photo analysis:"
    )

    task_intro = (
        "You are performing an image-to-image same-scene reshoot transformation, not a conservative retouch. "
        "Keep the same subject identity, scene, clothing, props, and photographic realism, but visibly change the subject's pose according to the selected pose override."
        if has_pose_override else
        "You are editing the supplied image, not creating a new image.\n"
        "Act like a high-end human retoucher: restrained, photographic, detail-aware, and realistic."
    )

    return f"""{task_intro}

{opening_preservation_rule}
Do not add text, logos, watermarks, extra people, new props, fantasy elements, or a different background.
Do not make the result look synthetic, plastic, or heavily filtered.

{edit_mode_block}

Crop instruction:
{crop_instruction_block}

{focus_block}

{analysis_header}
- Summary: {analysis.get("photo_summary") or ""}
- Main subject: {analysis.get("main_subject") or ""}
- Scene type: {analysis.get("scene_type") or ""}
- Aesthetic diagnosis: {analysis.get("aesthetic_diagnosis") or ""}
- Detected issues: {detected_issues}
- Edit philosophy: {analysis.get("edit_philosophy") or ""}
- Edit intent: {analysis.get("edit_intent") or preset.get("intent", "")}
- Desired mood: {analysis.get("composition_mood") or preset.get("label", "")}
- Layout method: {analysis.get("layout_method") or ""}
- Composition strategy: {analysis.get("composition_strategy") or ""}
- Visual balance strategy: {analysis.get("visual_balance_strategy") or ""}
- Empty space strategy: {analysis.get("empty_space_strategy") or ""}
- Alignment strategy: {analysis.get("alignment_strategy") or ""}
- Distraction strategy: {analysis.get("distraction_strategy") or ""}
- Scene preservation strategy: {analysis.get("scene_preservation_strategy") or ""}
- ROI box: {roi_box}
- Subject extent box: {subject_extent_box}
- Protected regions: {protected_regions}
- Recommended aspect ratio: {rec_aspect}
- Aspect ratio reason: {aspect_ratio_reason}
- Tool strategy: {analysis.get("tool_strategy") or ""}
- Lighting strategy: {analysis.get("lighting_strategy") or ""}
- Memory color strategy: {analysis.get("memory_color_strategy") or ""}
- Crop/reframe recommendation: {analysis.get("crop_recommendation") or ""}
- Crop intent: {crop_intent}
- Correction plan: {correction_plan}
- Must preserve: {preserve_rules}

Style direction:
{preset_prompt}

User extra direction:
{effective_user_direction or "沒有額外指令。"}

{user_block}

{style_block}

{correction_plan_block}

{negative_block}

{skin_block}

{revision_block}

{retouch_priorities}

{color_grade_check}
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
- Retouch quality: light, color, contrast, texture, and finish improve the original without looking synthetic, plastic, or heavily filtered.

Strict scoring rubric:
- 9-10: excellent, faithful, polished, intentional framing, no meaningful preservation/crop issues.
- 7-8: good demo output; minor taste, color, or small composition issues only.
- 5-6: borderline; noticeable flaws, but subject, scene, and required framing remain believable.
- 3-4: failed demo output; required subject/context is clipped, cramped, distorted, unstable, or the composition is clearly worse.
- 1-2: severe failure; preservation damage, important missing content, artificial borders, distorted shape/perspective, or wrong/rebuilt scene.

Score caps:
- If required main subject content, protected_regions, role cues, important props, structural edges, fine details, or connected components are accidentally cut off or incomplete, score must be 3 or lower.
- Apply the previous cap only to content required by the intended framing. Do not penalize partial portrait/person crops when they are intentional, clean, and preserve the role/story.
- If subject shape, identity, material, anatomy, object geometry, structural continuity, fine-detail structure, or perspective relationship is changed unnaturally, score must be 3 or lower.
- If an important main or supporting person has required head/face/role-identifying details accidentally clipped, score must be 4 or lower; if this prevents understanding the person's role or event, score must be 3 or lower.
- If both cropping damage and shape/perspective distortion are present, score must be 2 or lower.
- If the output contains transparent padding, checkerboard areas, blank borders, artificial canvas extension, or empty side bands, score must be 3 or lower.
- If the original scene is visibly rebuilt, replaced, over-simplified, or converted into a different environment/style, score must be 3 or lower.
- If contextual or neutral elements are removed/rebuilt just for cleanliness and this changes scene truth, score must be 3 or lower; if scene truth remains but context is weakened, score must be 5 or lower.
- If composition method, aspect ratio, empty space, alignment, or context choice visibly weakens the image, score must be 6 or lower; if it also harms required subject completeness or scene intent, score must be 3 or lower.
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
- Preserve rules: {preserve_rules}
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
