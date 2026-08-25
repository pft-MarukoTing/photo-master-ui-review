import json
import logging
import re

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
    "cropping": "crop", "framing": "crop", "alignment": "crop", "straighten": "crop",
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
  "recommended_aspect_ratio": "original / 1:1 / 3:2 / 2:3 / 3:4 / 4:3 / 4:5 / 5:4 / 9:16 / 16:9 / 21:9 / 1:4 / 4:1 / 1:8 / 8:1",
  "aspect_ratio_reason": "為什麼這個輸出比例最適合主體、陪襯元素、留白與畫面意圖。",
  "subjects": [
    {
      "label": "主體描述,例如 左二女性 / 中間男性 / 蛋糕。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "prominence": 1,
      "is_person": true,
      "skin": "is_person=true 時必填，否則寫「不適用」。依序寫：原生膚色身分（在受光平均處判讀深淺與底調，不讀高光或陰影）；場景光色（臉上實際受光的方向與色性，以及是否為應保留的情境光）；非預期偏色（僅限無法由場景光解釋，或與頸、耳、手不連貫的偏黃、偏綠、偏紅、偏洋紅、偏灰；沒有則寫無）。原生膚色身分不可改變。不可把背景、草地、服裝或其他高飽和物體的主色當成膚色受光；只有可見光源或反射確實落在皮膚上才算情境光。夕陽、鎢絲燈、舞台燈、霓虹與反射色等合理光色不可被中性化。",
      "reason": "為何突出:最大 / 最中央 / 最清晰 / 互動焦點。"
    }
  ],
  "eye_blink_issues": [
    {
      "subject_label": "對應 subjects 裡的人，例如 最右男性 / 前排中間小孩。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "eye_state": "open / natural_smiling_squint / intentional_wink / intentional_closed / expressive_closed / downward_gaze / sleeping / occluded_or_unclear / accidental_blink / accidental_half_blink / uncertain",
      "severity": "none / minor / moderate / severe",
      "intent_cues": ["列出支持這個判斷的可見線索，例如 wink 手勢、嘴角表情、眉毛情緒、看鏡頭、雙眼同步閉合、眼皮不對稱、低頭視線、眼鏡遮擋。"],
      "closure_intent": "intentional_expression / accidental_capture / ambiguous",
      "expression_coherence": "supports_expression / conflicts_with_expression / unclear",
      "blink_failure_confidence": 0.0,
      "should_fix": true,
      "reason": "說明為何判斷是不小心閉眼/半閉眼，或為何閉眼/眨眼其實是表情、情緒或手勢的一部分。"
    }
  ],
  "hair_issues": [
    {
      "subject_label": "對應 subjects 裡的人，例如 左二女性 / 抱小孩的男性。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "hair_state": "tidy / styled_messy / styled_bangs / styled_face_framing / motion / natural_texture / windblown / strands_across_face / bangs_disturbed / bangs_clumped / lock_out_of_place / frizz_halo / tuft_or_flattened / occluded_or_unclear / uncertain",
      "visible_evidence": ["只寫真的看得見的細節，例如 三四根髮絲橫過右眼 / 瀏海一撮向上翻起露出額頭 / 左側一撮從包頭鬆脫垂在耳前、右側沒有對應的一撮。"],
      "front_hair": "臉前或額前有頭髮的人一律必填，不分齊瀏海、旁分或長髮前段。none（臉前完全沒有頭髮）/ even（前方髮絲粗細相近、分佈均勻）/ clumped（髮絲粗細不一、有的黏成一束，束間露出光禿皮膚）",
      "fix_action": "lay_back / remove / settle / redistribute / none",
      "mess_intent": "intentional_style / accidental_capture / ambiguous",
      "hair_mess_confidence": 0.0,
      "should_fix": true,
      "reason": "說明為何判斷是拍攝當下被風吹亂或壓翹，或為何這個凌亂本來就屬於髮型、髮質或動作。"
    }
  ],
  "gaze_contact_issues": [
    {
      "subject_label": "對應 subjects 裡的人，例如 中間男性 / 左側女性。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
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
        "preserve_during_repair": ["body pose", "shoulder direction", "mouth expression", "identity", "scene"],
        "failure_to_avoid": ["near-camera only", "sharpen-only", "uncomfortable side-eye"]
      },
      "should_fix": true,
      "reason": "第一句先說明 no-eyes-visible rule 或 half-face rule 是否適用（單眼可見、側臉、背對鏡頭等），若適用就直接 unclear/should_fix=false 並停在這裡；若都不適用才繼續依序說明 lens_contact_decision、gaze_state、pupil/iris 位置是否觸發 direct-camera veto、三個分數、gaze_target_in_frame 是否成立（若成立，說明畫面中那個具體可指認的目標物是什麼）、gaze_repair_strategy 為何選擇 eyes_only 或 eyes_plus_head_face_alignment，以及 should_fix 是否由 hard should_fix rule 推導出來。gaze_correction_reasonableness 只評估眼神轉向鏡頭是否符合人體工學/解剖自然性，不評估是否保留原本敘事。不要先憑直覺決定 should_fix 再回填分數。"
    }
  ],
  "smile_states": [
    {
      "subject_label": "對應 subjects 裡的人，例如 中間男性 / 左側女性。",
      "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
      "smile_intensity": "neutral / gentle / broad / laughing / unclear",
      "adjust_safe": true,
      "reason": "簡短說明是否有明確可見的笑容證據、是否更可能是說話或其他表情動作，以及 adjust_safe 的依據。"
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
  "memory_color_strategy": "記憶色（膚色、天空、綠色、食物、材質）在這張照片的光線下應該呈現的狀態。有人物時先說明人物受光的方向與色性；整體白平衡、色相與飽和度可以調整畫面調性，但膚色須獨立保護，維持人物原生膚色與合理情境光，只移除非預期偏色。",
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
Zoomed face-crop images:
- After the main photo, you may be given one or more additional images labeled "Face crop N (zoomed, for judging eye state, blink, and gaze/pupil direction only)". Each one is a cropped, zoomed-in view of one visible person's face from the SAME photo above, not a different photo or a different moment.
- Match each face crop to the corresponding person in the main photo by appearance (position, clothing, hair, expression) and use it as your primary evidence for that person's eye_state, gaze_state, lens_contact_decision, and pupil/iris direction -- the main photo alone often does not have enough resolution on the eyes to judge this reliably, especially in a half-body, full-body, or group photo.
- Everything else about that person (composition role, pose, clothing, scene relationship, framing decisions) still comes from the main photo, not the crop.
- Face-crop detection is best-effort: it can miss a visible person (non-frontal angle, occlusion, small size) or occasionally crop something that is not a face. If a visible person has no matching crop, or a crop clearly does not show a usable face, fall back to judging that person's eyes directly from the main photo as usual -- do not treat a missing or unusable crop as evidence the eyes are unreadable.

Eye blink / half-closed-eye inspection:
This is an expression-intent classification task before it is a repair task. Do not judge only from eyelid openness. Judge whether the eye closure belongs to the person's expression, emotion, gesture, or moment.

Two-stage decision process:
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
- intentional_closed: eyes are deliberately closed as part of a pose, prayer/resting expression, savoring food, calm moment, or other intentional moment; this is not a failure.
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
- Include an eye_blink_issues entry for every visible person whose eyes are legible enough to judge, not only the ones with something to explain -- an ordinary open-eyed person with nothing unusual still gets a should_fix=false entry, the same as a wink or laugh does.
- Do not omit a visible person from eye_blink_issues just because their eyes look fine; the array should cover every legible face in the photo, not only the flagged or noteworthy ones.
- In eye_blink_issues, include should_fix=false entries when they explain why a closed-eye case is intentionally left unchanged, especially for winks, laughter, crying/emotional expressions, child expressions, or ambiguous cases.
- Add an eye-related correction_plan item only when should_fix=true.
- If should_fix=true, add a correction_plan item that names the exact person by position/role and asks the edit model to naturally reopen or relax only that person's eyes while preserving identity, gaze direction, facial structure, expression style, skin texture, glasses, makeup, and lighting.
- The matching correction_axes entry for that correction_plan item must include ["expression"].
- The matching correction_inverse entry should preserve the original eye state and facial expression for that same person if the user unchecks the item.
- The matching retouch_recipe item should be user-facing and concise, for example: "修正右側男生不小心半閉眼的表情。"
- If no accidental blink or half-blink needs correction, keep correction_plan free of eye-opening items.

Edit-model execution guardrail:
- When gaze_contact_issues.should_fix=true, do not leave the gaze unchanged.
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
- gaze_contact_issues.should_fix=true is not merely a recommendation; it authorizes the edit model to perform gaze redirection.
- For every approved gaze repair, the analysis must produce the same instruction in three places: gaze_contact_issues, correction_plan, and retouch_recipe.
- Do not rely on the edit model to infer gaze repair from JSON alone.
- Do not let a preservation instruction cancel an approved gaze repair. Preservation should protect all unrelated facial traits while allowing local gaze redirection.

Mini-model calibration:
- Do not reason as: "eyes closed means accidental blink."
- Do not reason as: "one eye closed means half-blink."
- Do not reason as: "emotional closed eyes should be opened."
- Do not reason as: "neutral mouth plus closed eyes means accidental blink" -- a genuinely sleeping person also has a neutral, relaxed mouth; check the sleeping guardrail's posture and eyelid cues before assuming a mistake.
- Do not reason as: "the person is looking down/away so it's fine" -- that is only true when there is a specific, visible, nameable target in this frame that the gaze is aimed at; check the downward-gaze guardrail before assuming a mistake.
- Always ask first: "Does the eye state support the visible mouth/brow/gesture/emotion?" If yes, do not fix.
- For one-eye-closed faces, run the wink guardrail before any blink-failure reasoning.
- For strong emotional faces, run the emotional closed-eye guardrail before any blink-failure reasoning.
- For any fully-closed-eyes case, run the sleeping guardrail before any blink-failure reasoning.
- For any downward/averted-gaze case, run the downward-gaze guardrail before any blink-failure reasoning.
- When the expression could reasonably be deliberate, emotional, charming, playful, or performative, choose should_fix=false.
- When the eye state clearly conflicts with the expression and looks like shutter mistiming, choose should_fix=true.

Hair tidiness inspection:
Intent before repair, as with the eyes, and stricter -- the editing model rebuilds whatever hair you point it at, and a rebuilt hairstyle is a worse failure than untidy hair. One reading decides most cases: disorder that is even and repeated across the head is style or texture, while disorder that is a single local contradiction on an otherwise settled head is an accident. Wind is the exception to that reading, since it can disturb a whole head at once: it displaces hair in one direction and against the hairstyle's own shape, on a body that is otherwise posed or still, and it usually shows elsewhere in the frame too -- clothing, foliage, other people's hair. Read wind as a flaw across the whole area it disturbed, not only where it landed on a face.
Not a flaw, so should_fix=false, and name which one: styled_messy (deliberate cut, tousle, or product separation), styled_bangs (center-parted, curtain/八字, or see-through fringe -- a designed parting is even on both sides and is not a split to close, and designed wisps are fine, similar in width, and evenly spaced; run the clumping test below before settling on this one), styled_face_framing (strands left out at the temples, as most updos and ponytails have), motion (hair mid-jump, spin, flip, throw, or turn, where the body itself is mid-action so the movement is the moment and tidying it would delete the action -- this is about what the person is doing, not about wind), natural_texture (curly, coily, fluffy, or baby hair -- flattening it changes who they are), occluded_or_unclear (head small in frame, motion-blurred, rim-lit, or dark on dark).
A flaw across the whole area it covers when the wind reading above fits: windblown (the hairstyle is lifted, opened up, blown sideways, or pushed out of its own shape while the person is posed or still). Treat the whole disturbed area as the work, not only the strands that landed on the face.
Front-hair check -- do this for every person with hair falling over the forehead or in front of the face, before choosing any state for them, and put the answer in visible_evidence. Whether that hair is a blunt fringe, a side sweep, or the front length of longer hair makes no difference here, and neither does whether skin shows through it or beside it; the question is only how those strands are distributed. Do they run at similar thickness and spacing, or are some stuck together into cords with bare skin between them? Record it in front_hair, required for anyone with hair in front of the face. It is a reading, not a state: never copy its value into hair_state, which only ever takes a value from its own list. front_hair="clumped" means hair_state="bangs_clumped" and fix_action="redistribute" -- not strands_across_face, whatever the strands look like individually, because the front of the hair is what is wrong and what gets worked on. front_hair="even" is not a flaw by itself. Judge the front of the hair as one shape, not by where its tips happen to fall -- hair whose ends reach the brows, eyes, or cheek is still the front of a hairstyle, and reading only those tips is what turns a clumped front into "a few strands near the eyes".
bangs_clumped: the fringe has separated into uneven clumps with bare gaps showing forehead between them, because the hair is oily, damp, or sweaty rather than because it was cut or styled that way. Confirm it with any two of these, coarsest first: the clumps differ visibly in thickness, with bare gaps beside thick strands; only the fringe changed, while the rest of the head still reads clean and smooth; the scene explains it -- heat, humidity, rain, sweat, a long day or a long outdoor event -- usually with the same sheen on the forehead and nose; the hair separates at the root and clings to the forehead, where styled bangs stay evenly distributed at the root and separate along their length; the fringe carries a hard specular streak and looks darker and slicker than the lengths below it. Two of those make it should_fix=true; the last two need a clear enough view of the strands to be worth reading at all.
Also a flaw, but only when local and fighting the hair around it: strands_across_face (hair that has left the hairstyle and come to rest on the face -- over an eye, nose, or mouth, or stuck to a lip or cheek. Hair that still belongs to the fringe, lying where the fringe lies, is a fringe state, never "a few loose strands"; this is the label that most often swallows a clumped fringe by mistake), bangs_disturbed (fringe flipped up or pushed against its own direction, exposing forehead it covered), lock_out_of_place (one lock escaped a tie, clip, or hat and hangs by the ear or below the fringe, with no match on the other side), frizz_halo (static outline on an otherwise smooth head), tuft_or_flattened (one tuft standing up or hat/sleep-pressed while its neighbours lie correctly). Set mess_intent="accidental_capture" only at hair_mess_confidence >= 0.75; mixed evidence means "ambiguous" and should_fix=false.
Hair over an eye, nose, or mouth is the case users most want fixed, so it stays should_fix=true. What being on a feature changes is the wording, not the verdict: moving a strand means whatever it covered has to come from the original photograph, so the item must say so -- reveal the person's own feature as the photograph already shows it on both sides of the strand, with its shape, position, and edges unchanged, rather than letting it be re-rendered. The single case that cannot be done is a section of hair thick enough to bury a whole feature, with nothing visible either side of it to continue from: there is no original left to reveal, so should_fix=false with the reason naming what is buried.
Forehead-or-cheek lock test, for the case most often misread -- run it before settling on any state above, since a deliberate lock and a fallen one look alike in a still frame. Deliberate: comes as a matched pair, lies along the cheekbone following the hairline's own direction, is combed out from the parting with the hair behind it intact, and stays clear of the eyes. Fallen: one side only, crossing that direction at its own angle, visibly pulled free leaving a gap or kink at the tie it escaped, landing on an eye, brow, or mouth. Two or more cues decide it; one each way means should_fix=false.
Judge only from strands you can actually resolve: if you cannot see individual strands you cannot see the flaw, so occluded_or_unclear, and require >= 0.9 on blurred, backlit, or small heads. Size and prominence change only how clearly you see the hair, never whether to fix it, and grooming the subject is not a goal of this retouch -- a false positive costs the person their own hair, where a miss costs nothing.
Every should_fix=true commits to a fix_action: lay_back where adjacent hair runs the same way and can take the strands back, remove where they are isolated on skin, on a feature, or on background with no flow to rejoin -- and remove is only ever for hair that has LEFT the hairstyle, never for hair that the hairstyle puts where it is. Hair lying on the forehead, along the hairline, or at the temples is a fringe or the hairstyle's own edge, so removing it deletes part of the haircut and uncovers forehead the person's hair is meant to cover. That hair takes redistribute or lay_back and never remove, whatever state you assigned it. Strands resting further down -- on a cheek, a jaw, a lip -- have left the hairstyle and are what remove is for. Writing "移除前額的碎髮" is how a fringe gets erased -- settle for a windblown area -- the one action whose scope is a region rather than a single strand, combing the disturbed hair back down into the hairstyle's own shape so the outline reads continuous again -- and redistribute for a clumped fringe, spreading the hair that is already there so the clumps even out and the bare gaps close. For settle, name the reference in the item: the part of the same head the wind did not disturb is what defines the length, parting, volume, direction, and silhouette the disturbed area returns to. Where the wind disturbed the whole head there is less of that reference left to read, so require >= 0.85 there and say that the hairstyle's own evident shape is the target. Never 淡化, 降低對比, 柔化, or 模糊 hair -- toning a strand down leaves a grey ghost of it on the skin, which reads as damage where the strand only read as untidy, and there is no partial version of this repair. Stray hair is never a distraction item for the same reason: that toolkit (tonal control, desaturation, blur) is the wrong instrument on hair and produces exactly that smear. The one tonal instruction that is right on hair is the oily sheen itself: a redistribute item should also say to bring the hard specular streak down to the soft even sheen the rest of that person's hair has, with strand detail recovered inside it -- that is recovering texture in a blown highlight, not fading hair away, and it never means darkening, flattening, or blurring the fringe.
Then add one correction_plan item per distinct hair problem on that person, tagged correction_axes ["hair"] -- a clumped fringe and strands resting on a cheek are two problems needing two different actions, not one item covering both.
Write it as the state that hair ends in, not as something to take away: an item that only names hair to be rid of leaves the editing model nothing to render towards, and what it renders instead is the hair it was given. Name them by position, name the strands/tuft/area, state the fix_action, and hold hairstyle shape, parting, length, volume, density, hairline, curl pattern, and color unchanged.
Write that item as the finished picture, in words that can be rendered. The editing model cannot act on 修飾, 整理, 調整, 重組, 精修, 順服, 自然分布, or 均勻連續: those name an intention rather than anything visible, and an item written that way comes back unchanged -- which is the most common reason a hair correction has no effect at all. Name the operation and the state it ends in. A clumped fringe needs both halves of the job named: 去油 -- 壓掉瀏海上的油亮反光，讓髮絲恢復霧面質感、不再黏成一束; and 梳整齊 -- 把結成一束一束的瀏海梳散成寬度一致的薄簾，並讓髮絲蓋過束與束之間露出額頭的空隙。Worked example of a whole item: 「把瀏海的油光壓掉、恢復霧面質感，並把結成束的瀏海梳散成寬度一致的薄簾，讓髮絲蓋過中間露出額頭的空隙；瀏海長度、髮際線與髮量不變。」
correction_inverse keeps that person's hair as captured; retouch_recipe stays short, for example "把瀏海去油、梳整齊。" or "把黏在臉頰上的兩三根碎髮清掉。". Keep should_fix=false entries when they record a decision worth explaining, but write no correction_plan item for them -- tidy hair is not a correction, and hair tidying is never a routine portrait enhancement.

Gaze contact / eye-focus inspection:
This is a strict lens-contact detection task first, and an edit-feasibility task second. The main question is not whether the face points toward the camera. The main question is whether the person's pupils/irises are actually focused on the camera lens/viewer.

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
For every gaze_contact_issues item, write gaze_repair_strategy as a concrete edit guide for this exact image, not a generic reminder. The edit model will follow this strategy literally, so vague or overly conservative wording will cause a weak edit.

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
- preserve_during_repair should list what must stay unchanged for this image, such as body pose, shoulder direction, hands, clothing, mouth expression, identity, background, and object interaction.
- failure_to_avoid should name likely failure modes for this image, such as "near-camera only", "pupil shift without face alignment", "pupil shift without direct lens contact", "uncomfortable side-eye", "sharpen-only", or "changed body pose".

Example strategy for a clear off-camera seated portrait:
{
  "strategy_type": "eyes_plus_head_face_alignment",
  "original_gaze_read": "The face is slightly angled and the pupils/irises read off-camera rather than lens-locked.",
  "target_gaze_read": "Both pupils/irises aim at the camera lens; viewer reads direct eye contact.",
  "eye_adjustment": "Move both pupils/irises toward the camera-lens center, not merely closer to it.",
  "sclera_catchlight_adjustment": "Rebalance sclera naturally around the new iris position and keep catchlights consistent with the original lighting.",
  "head_face_adjustment": "moderate yaw toward camera while preserving body pose and shoulders",
  "repair_strength": "moderate",
  "edit_instruction": "Redirect the named subject's gaze to the camera lens using pupil/iris repositioning plus a moderate face yaw toward camera. Do not stop at near-camera; the final viewer read must be direct eye contact.",
  "success_condition": "Subject clearly looks straight at the camera lens.",
  "preserve_during_repair": ["body pose", "shoulder direction", "mouth expression", "identity", "clothing", "scene"],
  "failure_to_avoid": ["near-camera only", "pupil shift without face alignment", "sharpen-only", "changed body pose"]
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
- Set gaze_contact_issues.should_fix=true if and only if all of these are true:
  1. lens_contact_decision=not_looking_at_lens;
  2. not_looking_at_lens_confidence >= 0.55;
  3. the eye direction is visually clear enough to correct safely;
  4. the visible-target exception below does not apply.
- gaze_correction_reasonableness is NOT part of this rule. A low reasonableness score changes strategy_type and repair_strength (see Stage 2/3 above), never should_fix. There is no reasonableness floor below which should_fix flips to false.
- For readable near-camera or slightly-beside-lens gaze, do not suppress should_fix merely because the miss is small. A small but clear lens miss is still a valid gaze-contact issue.
- Otherwise set gaze_contact_issues.should_fix=false.
- Do not override this rule just because the original gaze is natural, candid, acceptable, documentary, contextually appropriate, intentional, or important to the original story. None of those are valid grounds for should_fix=false when the eyes are readably not on the lens.
- Natural original gaze and anatomically feasible gaze correction are different questions.
- Do not use subject size, main-subject status, foreground/background status, pose naturalness, action type, or narrative preservation as a deciding factor. Those factors affect only uncertainty or repair feasibility, not whether the eyes are looking at the lens.
- No-eyes-visible rule: when a person's face is turned away from the camera far enough that neither eye is actually visible -- back of head, near-total rear view, a passerby seen only from behind, or a silhouette -- lens_contact_decision must be unclear and should_fix must be false, even if the head/shoulder/body orientation makes it obvious they are not facing the camera. Body or head orientation is never a substitute for seeing the eyes; do not infer, guess, or extrapolate a gaze direction for eyes you cannot actually see. This is most common with incidental bystanders in landscape, street, or documentary photos who are not the photo's subject -- they still get a gaze_contact_issues entry (per the output rules below), just with lens_contact_decision=unclear and should_fix=false, not a guessed not_looking_at_lens.
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
- Inspect gaze_contact_issues independently for each visible person when gaze is relevant.
- Incidental background person exception: skip gaze_contact_issues entirely (no entry at all, regardless of whether eyes are visible) for a visible person who is clearly not the photo's main subject and not one of its intentional subjects -- a passerby, bystander, or background figure who just happens to be caught in a landscape, street, architecture, product, food, or scenery shot, and who is not part of what the photo (per subject_category/main_subject/subjects above) is actually about. This exception does NOT apply to the main subject, a secondary posed subject, or anyone in an intentional group/portrait (family, friends, couple, event group), even if that person is smaller, softer-focus, or standing further back -- those still get full gaze_contact_issues treatment. When it is unclear whether a visible person is incidental background or an intentional subject, default to still including them in gaze_contact_issues rather than skipping them.
- Include a gaze_contact_issues entry for any visible person whose gaze is readable and not clearly direct_camera, even if should_fix=false.
- If a visible person's eyes are readable and the person is not unmistakably looking into the lens, do not omit them from gaze_contact_issues.
- Include should_fix=false entries only for lens_contact_decision=looking_at_lens or lens_contact_decision=unclear (eyes too blurry/occluded/small to judge safely, not visible at all because the person faces away from the camera, or only about half the face/a single eye is visible). A readable off-camera, near-camera, or unfocused-looking gaze is should_fix=true; do not use should_fix=false to mean "correctable but better left as-is."
- When any gaze_contact_issues item has should_fix=true, gaze repair is an approved edit request. It must not remain analysis-only.
- For every gaze_contact_issues item with should_fix=true, fill gaze_repair_strategy with a concrete, per-image guide. This is mandatory, not optional.
- Add a correction_plan item for every gaze_contact_issues item with should_fix=true. This is mandatory, not optional.
- Add a matching retouch_recipe item for every gaze_contact_issues item with should_fix=true. This is mandatory, not optional.
- Add a matching correction_axes entry including ["expression"] for every gaze_contact_issues item with should_fix=true.
- Add a matching correction_inverse entry that protects unrelated properties while explicitly allowing the named subject's pupil/iris direction, eye-focus direction, sclera balance, gaze-consistent catchlight placement, and, when needed, the exact head/face alignment change approved by the analysis.
- Treat the analysis output as the full repair specification. The edit model should not need to invent its own gaze-repair method.
- Therefore, for every approved gaze repair, the analysis must make the intended execution explicit in four places that all agree: gaze_contact_issues.gaze_repair_strategy, correction_plan, correction_inverse, and retouch_recipe.
- The correction_plan item must be self-sufficient: name the subject, state that the gaze should be redirected toward the camera lens / direct_camera, state the exact allowed method (for example, eyes only, or eyes plus slight/moderate head-face alignment), and include the same concrete method as gaze_repair_strategy.edit_instruction.
- The correction_inverse item must explicitly say what to preserve and what is allowed to change for that subject. Do not leave these allowances implicit. It should mirror preserve_during_repair and the allowed local changes in gaze_repair_strategy.
- The retouch_recipe item must stay concise and user-facing, but still clearly indicate that this is a gaze-to-camera correction for the named subject and whether face/head alignment may be involved.
- Phrase the gaze correction action as a concrete repair instruction, not as opening eyes and not as a vague "make eye contact better" suggestion.
- Do not describe gaze repair only as "improve eye contact" or "enhance gaze"; state that the named subject's gaze should be redirected toward direct_camera / the camera lens and whether head/face alignment is part of the approved method.
- Preserve identity, overall eye shape, eyelid openness, eye color, glasses/reflections, mouth expression, brow expression, facial structure, skin texture, and lighting, except where the approved analysis explicitly allows a local change.
- Never use blink repair wording for gaze repair.
- If the reason the eye direction cannot be judged is that the eyes may be closed or half-closed -- original_gaze_read says something like "eyes closed" or "gaze direction not observable" -- that is lens_contact_decision=unclear, not not_looking_at_lens, and should_fix must be false on gaze_contact_issues for that person no matter how confident the rest of the photo's gaze judgments are. Do not write a gaze_repair_strategy that reopens the eyes and redirects them in the same instruction; eyelid state is eye_blink_issues' question, not this one, and "the eyes might be closed" is never evidence that they are pointed away from the lens.

Analysis-to-edit handoff guardrail:
- When gaze_contact_issues.should_fix=true, the analysis must hand off a fully actionable repair, not just a diagnosis.
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

Smile reading:
Classify each visible face as neutral, gentle, broad, or laughing. A smile is never a defect, so do not add it to correction_plan, detected_issues, or retouch_recipe.
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

Analysis responsibilities:
- Observe the image, identify semantic roles, and describe why the photo can improve.
- Define the main subject, useful supporting elements, optional context, harmful distractions, ROI, subject_extent_box, and protected_regions.
- Decide the composition intent and framing intent before proposing any crop. Centering, thirds, symmetry, leading lines, and negative space are candidate strategies, not fixed rules.
- Size correction_plan to what the photo actually needs. A one- or two-item plan for an already well-balanced photo is a correct and valued answer; stay near the low end of the allowed range unless the photo genuinely needs more. Listing a correction that the photo does not need is a worse outcome than listing fewer, because the editing model will carry it out and push the result past what the photo called for. This applies especially to color: do not add an exposure, white-balance, saturation, or warmth item unless you can point to a specific visible problem.
- Eye-opening is not a routine portrait enhancement. Add eye or blink repair to correction_plan only when the expression-intent rules above say should_fix=true.
- Eye-contact repair is separate from blink repair. Analyze whether the eyes are truly lens-locked, not merely whether the face points forward. Derive gaze_contact_issues.should_fix from the hard gaze rule, then add gaze/contact correction only when should_fix=true; do not use blink repair to solve off-camera, near-camera, cross-focused, or unfocused gaze.
- Gaze detection recall check: before finalizing analysis, revisit every visible person's eyes. If any readable eyes are not unmistakably lens-locked and no gaze_contact_issues entry exists for that person, add one.
- Gaze analyze-to-edit consistency check: before finalizing analysis, verify that every gaze_contact_issues item with should_fix=true has a complete gaze_repair_strategy plus a matching correction_plan item and a matching retouch_recipe item using the same subject_label and the same goal: redirect gaze toward the camera lens / direct_camera. If any matching item or strategy detail is missing, add it.
- Reverse gaze consistency check -- run this in the other direction too, separately from the check above: before finalizing analysis, re-read every correction_plan item. If any of them describes redirecting someone's gaze, eye direction, or eye contact toward the camera, or fixing where someone is looking, that item is only valid when there is a gaze_contact_issues entry for the same person with should_fix=true and a matching gaze_repair_strategy. gaze_contact_issues is the only place a gaze judgment is allowed to originate -- do not decide independently, while writing correction_plan/detected_issues/aesthetic_diagnosis, that someone's gaze needs fixing and add it there as a shortcut. If a correction_plan item describes a gaze fix with no such backing gaze_contact_issues entry, delete that correction_plan item (and its correction_axes/correction_inverse/retouch_recipe entry at the same index) rather than leaving it in; add the missing gaze_contact_issues entry only if the hard should_fix rule actually supports should_fix=true for that person.
- gaze_contact_issues.should_fix=true authorizes the edit model to perform local gaze redirection. Do not leave approved gaze repair only inside analysis JSON.
- For one-eye-closed cases, intentional_wink is the default classification when the open eye faces the camera and the face/gesture/emotion looks controlled; do not add blink repair for that case.
- For crying, pained, laughing, cute, dramatic, or meme-like closed-eye expressions, classify as expressive_closed or natural_smiling_squint and do not add blink repair.
- For a person who is genuinely asleep or napping, classify as sleeping and do not add blink repair, even if the mouth looks neutral.
- Blink analyze-to-edit consistency check, same reasoning as the gaze one above: before finalizing analysis, verify that every eye_blink_issues item with should_fix=true has a matching correction_plan item, a matching correction_axes entry including ["expression"], a matching correction_inverse entry, and a matching retouch_recipe item, all using the same subject_label. If any matching item is missing, add it now, following the wording rules above.
- Reverse blink consistency check, same reasoning as the gaze one above: if any correction_plan item describes reopening, relaxing, or fixing someone's closed/half-closed eyes with no backing eye_blink_issues entry at should_fix=true, delete that correction_plan item (and its correction_axes/correction_inverse/retouch_recipe entry) rather than leaving it in.
- Hair tidying is not a routine portrait enhancement either. Add a hair item to correction_plan only when the hair inspection above says should_fix=true, and never for styled, moving, or naturally textured hair.
- Hair analyze-to-edit consistency check, same reasoning as the gaze one above: before finalizing analysis, verify that every hair_issues item with should_fix=true has a matching correction_plan item, a matching correction_axes entry including ["hair"], a matching correction_inverse entry, and a matching retouch_recipe item, all using the same subject_label. If any matching item is missing, add it now, following the fix_action-specific wording rules above (lay_back / remove / settle / redistribute, the forehead lock test, never 淡化/降低對比/柔化/模糊 on hair).
- Reverse hair consistency check, same reasoning as the gaze one above: if any correction_plan item describes tidying, settling, redistributing, or removing hair with no backing hair_issues entry at should_fix=true, delete that correction_plan item (and its correction_axes/correction_inverse/retouch_recipe entry) rather than leaving it in.
- Prioritize natural-looking skin over global color neutrality. Preserve each person's complexion, skin chroma, and intentional scene illumination; correct only a cast that is distracting, inconsistent across connected skin, or not plausibly explained by the light. Never write a correction_plan item that makes skin warmer, yellower, rosier, healthier-looking, more vivid, or more saturated. If correction is needed, name the unwanted hue, affected skin area, and corrective direction. When subjects[].skin says no unexpected cast, write no skin temperature, hue, or saturation adjustment. A global white-balance, hue, or saturation adjustment may refine the image palette, but it must not force skin to follow the background grade.
- Propose AI-authored crop_candidates only when reframing may improve the photo. Each candidate must explain its composition intent and tradeoffs. Include a safer/context-preserving option when the decision is ambiguous, and include original-frame when preserving the original may be best.
- Do not score output quality here. Do not write QA penalties here. Leave candidate ranking to the crop judge and final scoring to QA.

Coordinate rules:
- crop_box, roi_box, subject_extent_box, and protected_regions use normalized original-image coordinates. x/y are top-left, w/h are width/height.
- roi_box is the focal area. subject_extent_box is the semantic subject area required by the intended framing; it is not automatically every visible body part, foreground object, furniture piece, or background element.
- subjects lists the candidate main subjects (people, animals, products, food, objects) ordered so index 0 is the photo's true main subject -- what the photo is actually about, consistent with main_subject, roi_box, and the composition intent. prominence means semantic importance as the subject, NOT which element is merely brightest, most colorful, or most saturated. Incidental background or decor -- flowers, plants, props, tableware, posters, scenery -- must rank lower than the real subject even when it is visually eye-catching; e.g. in a food photo the dish is the subject, not a bright vase of flowers behind it. Always output at least one entry, even for a single-subject photo.
- Each subjects[].box must tightly frame EXACTLY ONE individual person/animal/object, never a group. In a multi-person or multi-object scene, output one separate entry per individual, each with its own tight box; do NOT return a single box that spans several subjects or most of the frame. If there are four people, subjects must contain four entries, each boxing one person -- index 0 is only the single most prominent one, not all four. This holds even when main_subject or photo_summary describe the group as a whole; subjects is always per-individual so a downstream close-up can isolate exactly one.
- protected_regions are important subject parts, structural edges, fine details, connected components, role cues, props, or supporting elements needed for the intended story, action, presentation, or scene truth.
- If no reframing is beneficial or the crop is almost full frame, set recommended_aspect_ratio to "original" and crop_box.enabled to false.
- The output does not need to preserve the original canvas aspect ratio in auto mode. Choose exactly one value from this supported output set: original, 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1.
- Changing the outer canvas ratio never permits changing the internal proportions of the photographed content. Plan the new frame only by cropping/recomposition; never stretch or squash the original scene to fill it.
- The crop_box.aspect_ratio and every crop_candidates[].crop_box.aspect_ratio must match recommended_aspect_ratio exactly. Do not describe one ratio while returning crop geometry intended for another ratio.
- This is not just a label match: each crop_box's own x/y/w/h must itself compute to that same ratio -- (w * original_image_width) : (h * original_image_height) must equal the stated aspect_ratio, for both crop_box and every crop_candidates[].crop_box. A correctly-labeled aspect_ratio paired with geometry sized for a different ratio is an error, not a valid answer.
- Prefer ordinary photographic ratios (1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9). Use 1:4, 4:1, 1:8, or 8:1 only when strongly justified and subject safety remains excellent.
- Choose aspect ratio from subject type, scene intent, supporting elements, empty-space function, visual balance, and breathing room.
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


# calm_hair is a request, not a detection toggle -- unlike eye_closed_detection/
# hair_mess_detection/gaze_contact_detection (which only gate the EDIT stage
# now, via strip_disabled_topic_items in build_edit_prompt; analysis itself
# always runs full detection so a later revise can turn one back on without
# re-analyzing), there is no "always detect" baseline for a user-elected style
# change to fall back to when the switch is off. So this one still only
# reaches the analysis model when the user actually asked for it.
CALM_HAIR_ENABLED_OVERRIDE = """
The user has asked for this person's hair to be laid down as if there were no wind:
- This is a request, not a defect you have to find. Wind in the hair does not need to reach any confidence threshold, and it does not matter whether the disorder would otherwise read as intentional.
- For every person whose hair is lifted, opened, or blown sideways by wind, add a hair_issues entry with hair_state="windblown", fix_action="settle", should_fix=true, and a reason naming the user request.
- Write the matching correction_plan item as laying that person's hair down into the hairstyle's own shape so it reads as a still day, tagged correction_axes ["hair"]. Keep retouch_recipe short, for example "把被風吹起的頭髮自然放下。"
- This does not extend to hair moving because the person is moving. A jump, spin, hair flip, or run still keeps its motion; laying that hair flat would contradict the body in the same photograph. Only wind-displaced hair is settled.
"""


def build_analysis_instruction(aspect_ratio_pref="auto", advanced_options=None):
    """Build analysis instructions with one authoritative aspect-ratio policy.

    advanced_options: the Upload page's Advanced Settings (e.g.
    {"eye_closed_detection": False}). Only calm_hair is read here -- the
    detection/repair toggles (eye_closed_detection, hair_mess_detection,
    gaze_contact_detection) no longer affect analysis at all, so eye_blink_issues/
    hair_issues/gaze_contact_issues/correction_plan always reflect the model's
    genuine judgement; strip_disabled_topic_items (build_edit_prompt, prompts.py)
    is what actually applies those three at edit time. smile_adjustment/
    smile_level are edit-only for the same reason, applied by build_smile_block
    against the smile_states the analysis always reads.
    """
    advanced_options = advanced_options or {}
    instruction = ANALYSIS_INSTRUCTION
    if advanced_options.get("calm_hair", False):
        instruction += CALM_HAIR_ENABLED_OVERRIDE
    if aspect_ratio_pref == "original":
        return instruction + """
User aspect ratio requirement:
- The user explicitly requested that the final output keep the original photo's aspect ratio.
- Set recommended_aspect_ratio to "original".
- If crop_box.enabled is true, crop_box.aspect_ratio must be "original" (or blank).
- Every crop candidate must preserve the source aspect ratio.
- Do not select a named ratio that changes the original proportions.
"""
    return instruction + """
Auto aspect ratio requirement:
- Select recommended_aspect_ratio only from the supported output set declared above.
- crop_box.aspect_ratio and every crop candidate aspect_ratio must equal recommended_aspect_ratio exactly.
- The normalized x/y/w/h values are a composition proposal, not permission to use a different ratio.
- Downstream deterministic geometry will fit the crop rectangle to this selected ratio, so protect the ROI, subject extent, and protected regions with enough breathing room.
"""


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


# {"x","y","w","h"} normalized box, reused verbatim by subjects/
# eye_blink_issues/hair_issues/gaze_contact_issues/gaze_target_in_frame/
# smile_states.
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

_SUBJECT_SCHEMA = _schema_object({
    "label": _schema_string(),
    "box": _BOX_SCHEMA,
    "prominence": _schema_integer(),
    "is_person": _schema_boolean(),
    "skin": _schema_string(),
    "reason": _schema_string(),
})

_EYE_BLINK_ISSUE_SCHEMA = _schema_object({
    "subject_label": _schema_string(),
    "box": _BOX_SCHEMA,
    "eye_state": _schema_string(),
    "severity": _schema_string(),
    "intent_cues": _schema_string_array(),
    "closure_intent": _schema_string(),
    "expression_coherence": _schema_string(),
    "blink_failure_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
    "reason": _schema_string(),
})

_HAIR_ISSUE_SCHEMA = _schema_object({
    "subject_label": _schema_string(),
    "box": _BOX_SCHEMA,
    "hair_state": _schema_string(),
    "visible_evidence": _schema_string_array(),
    "front_hair": _schema_string(),
    "fix_action": _schema_string(),
    "mess_intent": _schema_string(),
    "hair_mess_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
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
    "subject_label": _schema_string(),
    "box": _BOX_SCHEMA,
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

_SMILE_STATE_SCHEMA = _schema_object({
    "subject_label": _schema_string(),
    "box": _BOX_SCHEMA,
    "smile_intensity": _schema_string(),
    "adjust_safe": _schema_boolean(),
    "reason": _schema_string(),
})

# Flat x/y/w/h (no nested "box"), unlike the five schemas above.
_PROTECTED_REGION_SCHEMA = _schema_object({
    "label": _schema_string(),
    "x": _schema_number(), "y": _schema_number(),
    "w": _schema_number(), "h": _schema_number(),
    "reason": _schema_string(),
})

_PERCEPTION_SCORES_SCHEMA = _schema_object({
    "clarity": _schema_integer(),
    "noise": _schema_integer(),
    "exposure": _schema_integer(),
    "color_temperature": _schema_integer(),
    "layout": _schema_integer(),
    "lighting": _schema_integer(),
    "color_harmony": _schema_integer(),
    "aesthetic_finish": _schema_integer(),
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
        "eye_blink_issues": _schema_array_of(_EYE_BLINK_ISSUE_SCHEMA),
        "hair_issues": _schema_array_of(_HAIR_ISSUE_SCHEMA),
        "gaze_contact_issues": _schema_array_of(_GAZE_CONTACT_ISSUE_SCHEMA),
        "smile_states": _schema_array_of(_SMILE_STATE_SCHEMA),
        "roi_box": _BOX_WITH_REASON_SCHEMA,
        "subject_extent_box": _BOX_WITH_REASON_SCHEMA,
        "protected_regions": _schema_array_of(_PROTECTED_REGION_SCHEMA),
        "perception_scores": _PERCEPTION_SCORES_SCHEMA,
        "tool_strategy": _schema_string(),
        "lighting_strategy": _schema_string(),
        "memory_color_strategy": _schema_string(),
        "restoration_needs": _schema_string_array(),
        "evaluation_criteria": _schema_string_array(),
        "crop_recommendation": _schema_string(),
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
    "color; a modest saturation or warmth adjustment may refine the non-skin palette without imposing a new "
    "look. Grade the overall palette and skin separately: preserve each person's complexion and skin chroma, "
    "and correct only an unwanted skin cast."
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
COLOR_GRADE_CHECK = """Before finishing, compare your result against the original. The original defines the intended lighting and palette, not every unwanted color contamination; render the photograph better without changing its color intent:
- a cool or warm character that was clearly deliberate is still there, pointing the same way;
- what was vividly colored is still as vivid and what was moderate is still moderate; what was neutral is still neutral;
- skin keeps each person's depth, undertone, and chroma while retaining plausible scene light; never increase skin saturation or push it toward yellow, orange, or red. Correct only a distracting cast that the illumination does not explain.
Improving exposure, cleanliness, separation, and approved color correction is the work. Shifting the photograph's intentional color character is not -- that reads as a filter rather than a finish."""


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
    "a modest {allowed} adjustment may refine the non-skin palette without imposing a new look. Grade the "
    "overall palette and skin separately: preserve each person's complexion and skin chroma, and correct only "
    "an unwanted skin cast."
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
    if kept >= 0.85:
        return "close to the full frame; little more than the outer edges come off"
    if kept >= 0.6:
        return "a moderate tightening of the original frame"
    if kept >= 0.35:
        return "a noticeably tighter frame than the original"
    return "a tight frame filled by the subject"


def _build_crop_instruction_block(crop_plan, require_tighter=False, source_size=None):
    if not crop_plan:
        return (
            "No crop. The supplied image boundaries are the final frame. Do not reframe, expand canvas, add "
            "borders, or invent empty side bands. " + UNIFORM_SCALE_ONLY + " " + CONTENT_SHAPE_RULE
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
    if shape_difference:
        lines.append(_CANVAS_SHAPE_CONSEQUENCE[shape_difference])

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
    lines.append(f"{FRAME_SHAPE_SOURCE} {CONTENT_SHAPE_RULE}")
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

    Exists because should_fix=false on the structured gaze_contact_issues/
    eye_blink_issues/hair_issues entries, and even an explicit "do not add
    this to correction_plan for any reason" instruction in the disabled-
    toggle prompt override, have both been observed not to stop the analysis
    model from writing a free-text correction_plan item anyway -- a real run
    with gaze_contact_detection off still produced three gaze-redirection
    correction_plan items, none backed by an approved gaze_contact_issues
    entry because there wasn't supposed to be one.

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


def _subject_labels(issues):
    """De-duplicated, ordered subject_label list from a *_issues array, with
    a fallback for a missing/blank label -- never blocks a backfill just
    because the analysis omitted this one descriptive field."""
    return list(dict.fromkeys((i.get("subject_label") or "").strip() or "畫面中的人" for i in issues))


def backfill_missing_correction_items(analysis):
    """The mirror image of strip_disabled_topic_items: analysis is asked (the
    *_analyze-to-edit consistency check lines in ANALYSIS_INSTRUCTION) to keep
    hair_issues/eye_blink_issues/gaze_contact_issues in sync with
    correction_plan/correction_axes/correction_inverse/retouch_recipe, but
    that is a request to an LLM, not a guarantee -- observed in practice to
    set should_fix=true on an issue while never adding the matching
    correction_plan entry at all.

    Deliberately coarse: for a given topic, only backfills when correction_plan
    has ZERO items mentioning it at all, despite should_fix=true issues
    existing. Telling which correction_plan item (if any) already covers
    WHICH specific should_fix=true person, from free text alone, isn't
    reliable -- and duplicating a fix the model already wrote, just under
    different wording than subject_label, would be worse than occasionally
    missing a second person who shares a first person's already-covered
    topic. One combined item per topic (naming every affected subject) is
    the safe middle ground.

    Called unconditionally at analyze time (pipeline.analyze_stage), same as
    the toggle-independent detection it backstops -- advanced_options plays
    no part here; whether a backfilled item survives to the edit stage is
    entirely strip_disabled_topic_items's job in build_edit_prompt.
    """
    plan = list(analysis.get("correction_plan") or [])
    recipe = list(analysis.get("retouch_recipe") or [])
    axes = list(analysis.get("correction_axes") or [])
    inverse = list(analysis.get("correction_inverse") or [])
    # Already inconsistent in a way strip_disabled_topic_items's own re-index
    # guard also declines to touch -- appending here would only compound it.
    if not (len(recipe) == len(plan) and len(axes) == len(plan) and len(inverse) == len(plan)):
        return analysis

    backfilled = []

    def add(topic, plan_item, recipe_item, axis_tags, inverse_item):
        plan.append(plan_item)
        recipe.append(recipe_item)
        axes.append(list(axis_tags))
        inverse.append(inverse_item)
        backfilled.append(topic)

    hair_hits = [i for i in (analysis.get("hair_issues") or []) if isinstance(i, dict) and i.get("should_fix")]
    if hair_hits and not any(_is_disabled_topic_text(item, True, True, False) for item in plan):
        subjects = "、".join(_subject_labels(hair_hits))
        add(
            "hair",
            f"Tidy the disturbed hair for {subjects}: settle, redistribute, or remove only the strands the "
            "hair analysis flagged, following each person's own fix_action and hairstyle shape. Never fade, "
            "flatten, or blur hair; touch only the hair itself, not the face, skin, or lighting.",
            f"整理{subjects}被風吹亂、翹起或散落的頭髮。",
            ["hair"],
            f"保留{subjects}原本的頭髮狀態，不做調整。",
        )

    blink_hits = [i for i in (analysis.get("eye_blink_issues") or []) if isinstance(i, dict) and i.get("should_fix")]
    if blink_hits and not any(_is_disabled_topic_text(item, True, False, True) for item in plan):
        subjects = "、".join(_subject_labels(blink_hits))
        add(
            "blink",
            f"Naturally reopen or relax the closed/half-closed eyes for {subjects}, preserving identity, gaze "
            "direction, facial structure, expression style, skin texture, glasses, makeup, and lighting.",
            f"修正{subjects}不小心閉眼或半閉眼的表情。",
            ["expression"],
            f"保留{subjects}原本的眼部狀態與表情，不做調整。",
        )

    gaze_hits = [i for i in (analysis.get("gaze_contact_issues") or []) if isinstance(i, dict) and i.get("should_fix")]
    if gaze_hits and not any(_is_disabled_topic_text(item, False, True, True) for item in plan):
        subjects = "、".join(_subject_labels(gaze_hits))
        # gaze_repair_strategy.edit_instruction is written by the analysis
        # model specifically to be "1-2 sentences directly executable by the
        # edit model" (see the gaze_contact_issues schema) -- reuse it
        # verbatim per person instead of re-deriving a weaker instruction
        # from just subject_label.
        instructions = " ".join(
            (i.get("gaze_repair_strategy") or {}).get("edit_instruction")
            or f"Redirect {i.get('subject_label') or 'the subject'}'s gaze toward the camera lens, moving "
               "pupils/irises toward camera center, while preserving identity, facial structure, and expression."
            for i in gaze_hits
        )
        add(
            "gaze",
            instructions,
            f"修正{subjects}的眼神，讓視線望向鏡頭。",
            ["expression"],
            f"保留{subjects}原本的臉部結構、表情與場景，只調整眼神方向。",
        )

    if not backfilled:
        return analysis

    logger.warning(
        "should_fix=true on %s issue(s) with no matching correction_plan item at all; backfilling one "
        "combined item per topic",
        ", ".join(backfilled),
    )
    cleaned = dict(analysis)
    cleaned["correction_plan"] = plan
    cleaned["retouch_recipe"] = recipe
    cleaned["correction_axes"] = axes
    cleaned["correction_inverse"] = inverse
    return cleaned


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


# Trigger on is_person too, not just subject_category: a 生活紀錄 / 旅行 /
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
# already sits (smile_states). Keeping those two apart is what lets the level
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
        logger.warning("smile_states carried an unrecognized smile_intensity %r; ignoring that face", value)
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
    targets = []
    for item in _read_smile_states(analysis):
        if level == "auto" and item["adjust_safe"] is False:
            continue
        target = _smile_target_state(level, item["current"])
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
    for item in analysis.get("smile_states") or []:
        if not isinstance(item, dict):
            continue
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
        return f"""Smile adjustment (user-selected, mandatory). Requested level: {SMILE_LEVEL_LABELS[level]}
- Apply the requested smile intensity to each person named below.
{lines}
{_smile_craft_rules({t['target'] for t in targets})}"""

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


def build_edit_prompt(analysis, preset, preset_prompt, user_direction, crop_plan, revision_feedback=None, aspect_ratio_pref="auto", source_size=None):
    analysis = strip_disabled_topic_items(analysis)
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
        return effective_user_direction.strip()

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
    eye_blink_issues = analysis.get("eye_blink_issues") or []
    hair_issues = analysis.get("hair_issues") or []
    gaze_contact_issues = analysis.get("gaze_contact_issues") or []
    gaze_contact_issues_json = json.dumps(gaze_contact_issues, ensure_ascii=False)
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
- Generic preservation, faithful-retouch, and minimal-change instructions must not cancel any item in this correction plan.
- For an approved gaze repair, the editing method is defined by the analysis output itself: the matching correction_plan item, correction_inverse item, retouch_recipe item, and any per-subject gaze_repair_strategy.
- Do not substitute a different generic gaze-repair method here. If the analysis explicitly allows local eye-direction change and/or local head/face alignment for a named subject, that allowance wins for that subject only."""

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
    # skin.
    has_person = _has_person(analysis)
    blink_block = ""
    blink_items_to_fix = [
        item for item in eye_blink_issues
        if isinstance(item, dict) and item.get("should_fix")
    ] if eye_closed_detection_enabled else []
    if blink_items_to_fix:
        blink_block = """Accidental blink repair priority:
- The analysis found one or more people whose eyes appear accidentally closed or half-closed at the capture moment under an expression-intent failure-detection rule.
- Scope: this block governs eyelid state only -- whether an eye reads open, closed, or half-closed. Where an open eye's pupil/iris points is a different property, governed separately by the gaze-repair instructions elsewhere in this prompt when present; they may still approve redirecting a person's pupils/iris even though that person is not named below.
- Work only on the eyelid state of the person(s) named below. Every other visible person's eyelid state, and everything else about the named person(s) besides that eyelid state, come through exactly as captured.
- Reopen or relax the eyes naturally so they match the person's existing expression and the photo's moment. Avoid making the eyes too large, symmetrical, doll-like, or copied from another person.
- Render realistic eyelids, iris, pupil, catchlights, and eyelashes, with gaze direction matching the person's head angle and the photo's lighting -- unless a separately approved gaze repair for this same person specifies a different target direction, in which case follow that instead. Do not reshape the face, smooth the skin, or otherwise beautify the person while doing this.
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
    # calm_hair (see calm_hair_block below) is its own opt-in hair transformation,
    # unrelated to whether hair_mess_detection found anything -- computed here so
    # hair_block's own empty-case fallback just below can defer to it instead of
    # contradicting it with a blanket "leave hair untouched" instruction.
    calm_hair_active = bool(advanced_options.get("calm_hair")) and not hair_rejected
    # Only the should_fix items are handed over, unlike the blink block's full
    # list: the "everything else stays as captured" rule below already covers the
    # people and strands left out, and this json is the one place hair gets named,
    # so a second copy of it would only give the model more hair to touch.
    hair_block = ""
    hair_items_to_fix = [
        item for item in hair_issues
        if isinstance(item, dict) and item.get("should_fix")
    ] if hair_mess_detection_enabled else []
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
- The user asked for this photograph to look as though there were no wind. Every person's hair hangs down under its own weight, in the shape their haircut gives it, as it would on a still day indoors.
- Bring lifted, fanned, streaming, and sideways-blown hair back down onto the head and shoulders. Close the gaps the wind opened, using the hair that is already in the photograph. The outline of each hairstyle reads continuous and settled rather than caught mid-gust.
- Same person, same haircut: length, volume, density, parting, hairline, curl pattern, and color are unchanged, no hair is added, and the face is untouched. Hair falls, but its owner does not change.
- Settled is not sculpted. Hair hanging still keeps strand separation, its own unevenness, and the way it catches light. A smooth glossy sheet, a painted block, or a wig fails this even though the wind is gone.
- Everything else in the photograph stays exactly as captured -- the same clothing, the same flags, leaves, water, and other people, still showing whatever the weather was doing to them. This settles hair only; it does not calm the scene."""

    gaze_items_to_fix = [
        item for item in gaze_contact_issues
        if isinstance(item, dict) and item.get("should_fix")
    ] if gaze_contact_detection_enabled else []
    gaze_block = ""
    if gaze_items_to_fix:
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
- If Strategy type is eyes_only, keep the head/face angle unchanged.
- If Strategy type is eyes_plus_head_face_alignment, allow the exact level of head/face adjustment described in Head/face adjustment.
- Use the listed Repair strength for the named subject.
- Preserve everything listed in Preserve during repair and in the matching correction_inverse item.
- Avoid every failure mode listed in Avoid during repair.
- Do not weaken a moderate or strong instruction into a subtle eye nudge.
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
            "- Skin-specific color correction was rejected by the user. Preserve each person's skin color as captured; "
            "only let skin change insofar as an approved global lighting or color adjustment naturally changes the whole scene. "
            "Do not locally neutralize, retint, or balance skin."
            if skin_rejected else
            "- Natural skin is not necessarily neutral. Retain coherent warm, cool, or colored illumination from sunset, tungsten, "
            "stage light, neon, or reflected light on skin, teeth, and eye whites. Correct only a distracting hue cast that the "
            "scene light does not explain, or a mismatch across face, neck, ears, and hands. Skin chroma is protected: do not "
            "increase its saturation or push it warmer, yellower, redder, rosier, healthier-looking, or more neutral by default."
        )
        skin_block = f"""Skin in the finished photograph (this photo contains people):
{_build_skin_identity_lines(analysis)}
- Treat skin separately from the overall grade. Global white balance, hue, and saturation may refine the non-skin palette, but they do not redefine complexion or increase skin chroma. Each face keeps the recorded depth, undertone, and chroma while showing the light that plausibly falls on it.
{skin_adjustment_rule}
- Do not infer skin color from nearby grass, foliage, clothing, signs, or other saturated objects. Their color may remain vivid in the grade, but affects skin only when it is visibly cast onto the person as actual illumination.
- Color is the whole of what changes on a face. Everything on it stays where the photograph put it -- the same eye shape and lash line, the same iris and catchlight position, the same nostril, lip edge, and brow -- and the skin's own texture, pores and lines and all, is still resolvable afterwards. A re-rendered feature, or a face smoothed into an even surface, is a different person, which is a worse outcome than any color it corrected.
- A face is one surface: its tone changes only as light changes across a face, continuously and following the form, and nowhere on it do two colors meet at an edge. No part of a face is worked on apart from the rest -- skin corrected in one region carries that region's boundary with it, and a patch or seam on a face reads as damage where the color it replaced only read as unflattering. The same skin runs into the neck, ears, and hands, which match it.
- A deep skin tone brightens by showing more of itself: texture and modelling rise out of the shadowed side and the lit side reads bright through its own sheen, while the tone stays as deep as it was. Brightness reached by moving it toward a paler complexion is an identity change, not a retouch.
- A lighting override changes the scene light, and skin responds to that light like every other surface; it is never independently tinted to look healthier."""

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
            + UNIFORM_SCALE_ONLY + " " + CONTENT_SHAPE_RULE
        )
    else:
        crop_instruction_block = _build_crop_instruction_block(
            crop_plan, require_tighter=has_closer_size_override, source_size=source_size
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
            "add blank borders, transparent/checkerboard padding, or artificial empty side bands, unless a selected "
            "closer framing-size override explicitly asks for closer framing within it."
            if crop_plan else
            "Treat the supplied image boundaries as final unless a selected closer framing-size override explicitly asks for closer framing. "
            "Never add blank borders, transparent/checkerboard padding, or artificial empty side bands."
        )
    )
    crop_priority_line_faithful = (
        "Reframe as described in the Crop instruction above, judging the frame's edges from the photograph's own "
        "content and subject rather than from stated coordinates. Beyond the frame you settle on, do not "
        "additionally expand canvas, add borders, create transparent/checkerboard padding, or invent empty side bands."
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
3. Apply the user-approved correction plan and user's extra direction. If the analysis approved a gaze repair, follow the analysis-defined repair method and allowances for that named subject. Generic preservation rules must yield only where the analysis explicitly permits the local change.
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
FAITHFUL RETOUCH MODE. The user did not select any style override, so keep the original pose, camera viewpoint, lighting logic, framing size, scene, and moment. Improve only the approved correction plan, preset finish, and user's extra direction. If an approved gaze repair exists, follow the analysis-defined repair method and allowances for that named subject; this is not permission for unrelated pose or viewpoint changes."""
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
The photographed content keeps its true shape throughout: every person, face, object, and background structure comes through with the proportions, the tilt, and the relative sizes and distances it has in the input photograph. The output canvas may be a different shape than the input; that new shape comes from cropping and uniform scaling alone, never from resizing one axis on its own.
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
- Recommended aspect ratio: {rec_aspect}
- Aspect ratio reason: {aspect_ratio_reason}
- Tool strategy: {analysis.get("tool_strategy") or ""}
- Lighting strategy: {analysis.get("lighting_strategy") or ""}
- Memory color strategy: {analysis.get("memory_color_strategy") or ""}
- Crop/reframe recommendation (advisory): {analysis.get("crop_recommendation") or ""}
- Crop intent (advisory): {crop_intent}
- Correction plan: {correction_plan}
- Must preserve: {preserve_rules}

{framing_intent_block}

Style direction:
{preset_prompt}

User extra direction:
{effective_user_direction or "沒有額外指令。"}

{user_block}

{style_block}

{correction_plan_block}

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
        edit_instruction = strategy.get("edit_instruction") or "Redirect this person's gaze to the camera lens using pupil/iris repositioning."
        eye_adjustment = strategy.get("eye_adjustment") or "as needed to reach direct lens contact"
        head_face_adjustment = strategy.get("head_face_adjustment") or "none"
        sclera_catchlight_adjustment = strategy.get("sclera_catchlight_adjustment") or "rebalance naturally around the new eye position"
        target_gaze_read = strategy.get("target_gaze_read") or "both eyes clearly and directly looking at the camera lens"
        strategy_type = strategy.get("strategy_type") or "eyes_only"
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

Scope is limited to the eye/pupil region of the named subject(s) above, and nothing else. If the photo contains other people who are not named above, they are completely out of scope: do not touch their eyes, gaze, expression, pose, hair, or anything else about them, even if their gaze also looks imperfect -- only the subject(s) explicitly listed are being fixed here. For the named subject(s) themselves, do not let the eye/pupil movement drag along any other change to their face: eyebrows, eyelid crease shape, mouth, and overall expression must read exactly as they did before this edit, except for the direction the eyes are now pointing.

Go all the way to direct lens contact, not partway there. A gaze that only moved closer to the camera without fully arriving is the same failure as not moving it at all -- "improved" is not the target, "looking straight into the lens" is. If you are unsure whether the pupils have moved far enough, move them further rather than settling for a subtle, ambiguous shift; a slightly bold correction that unmistakably reads as direct eye contact is the right outcome, a timid one that still reads as near-camera or off-camera is not. Do not leave any named subject's gaze unchanged, and do not stop at a partial correction.

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
- Approved accidental blink repair: if the retouch recipe or style/correction plan asked to fix a named person's accidental closed or half-closed eyes, verify that the eyes are naturally corrected while identity, gaze, face shape, glasses, makeup, and expression style remain believable.
- Approved hair tidying: if the plan asked to tidy a named person's stray strands, check the named strands first, then check what else moved. Hairstyle shape, parting, length, volume, density, hairline, curl pattern, and color must be unchanged, individual strands must still be resolvable with real hair texture, and nobody else's hair may have been touched. Hair that is tidier but rebuilt, smoothed into a solid mass, straightened, thickened, or given a moved hairline is a failure of this item, not a success. Where a strand was taken off skin or background, check what replaced it: a faded or blurred remnant of the strand, a grey or darkened trace, or a smoothed plastic patch with the surrounding skin texture missing all fail this item, the same as leaving the strand would. Then check the face itself against the original, since hair repair is where identity slips: where a strand was taken off a feature, that feature must still be this person's own -- same eye shape, lash line, iris and catchlight position, nostril, lip edge and volume -- and the parts of it that were never covered must be untouched. A re-rendered, re-placed, re-shaped, or prettified feature, or a face that no longer reads as the same person, fails this item outright and caps the whole score at 3 as an identity change. If the plan asked to settle a wind-disturbed area, judge that area against the part of the same head the wind did not disturb: same length, volume, and silhouette, hairline where it was, gaps closed with the hair that was already there rather than with added hair, and strand direction, separation, and unevenness still visible. A settled area that reads as one glossy sheet, a painted block, or a wig fails this item, as does one carrying more length, volume, or coverage than the rest of that head. If the plan asked to de-grease and comb out a clumped fringe, check both halves separately, and judge it unfollowed if either is missing: the oily streak is matte with strand detail visible where the shine was, AND the thick cords are combed into an even-width curtain with the bare forehead gaps between them now covered by hair. A fringe still showing bald gaps between cords has not been combed out, however tidy the rest of the retouch is. Then check the limits: the fringe keeps its own length, outer edge, parting, and see-through quality, the hairline did not move, and the fringe carries no more hair, density, or width than the original. A fringe turned into a solid opaque curtain, thickened, lengthened, darkened, or blurred flat fails this item. If the user selected still-air hair, check that no hair is still streaming, fanned, or lifted by wind, that each hairstyle's outline reads settled and continuous, and that the rest of the scene -- clothing, flags, foliage, water, other people -- still shows the weather it was captured in. Hair that stayed windblown fails this, and so does a scene whose wind was calmed everywhere. Whatever the hair item asked for, compare the forehead and hairline directly against the original: a fringe that is gone, thinner, or shorter, a raised hairline, or more forehead or scalp on show than the original had is a hairstyle change, fails this item outright, and caps the whole score at 3.
{smile_qa_line}- Approved gaze-contact repair: if gaze_contact_issues.should_fix=true, or if the retouch recipe or correction plan asked to make a named person's gaze look toward the camera, verify that a visible gaze-direction change actually occurred for the named subject and that the final gaze unmistakably reads as direct lens contact. In simple terms, the subject should clearly look straight at the camera after the edit. The iris/pupil positions and visual axes should appear aimed at the camera lens, with natural sclera balance and gaze-consistent catchlights; if eye-only redirection would look strained, weak, or still off-camera, a moderate head/face orientation refinement is allowed and expected to make the result natural. Only the eye direction/focus, iris/pupil placement, sclera balance, visual-axis alignment, gaze-consistent catchlight placement, and bounded moderate head/face orientation refinement may change; eyelid openness, identity, expression structure, body pose, shoulder direction, glasses/reflections, and facial structure must remain believable. A no-op gaze result, a result that only sharpens/brightens/intensifies the eyes, or a result that still reads off-camera/near-camera/past-camera/slightly-beside-lens, fails this QA check.

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
