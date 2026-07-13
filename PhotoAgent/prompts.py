import json


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
  "memory_color_strategy": "膚色、天空、綠色、食物、材質等記憶色策略。",
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
  "correction_plan": ["3 到 7 項，實際會做的具體技術調整，供編修模型執行；例如「提升整體曝光 0.3 EV 並拉回高光」。"],
  "retouch_recipe": ["與 correction_plan 一一對應、順序相同、項數相同，但改寫成給使用者看的簡潔白話版本；例如對應上例可寫「讓照片更明亮」。"],
  "preserve_rules": ["必須保留的內容。"]
}

correction_plan and retouch_recipe must describe the exact same set of adjustments, in the same order, one-to-one. Write correction_plan first as the precise technical plan the editing model will follow; then write retouch_recipe as a simplified, user-facing paraphrase of each corresponding correction_plan item -- not a separate or reworded set of steps.

Analysis responsibilities:
- Observe the image, identify semantic roles, and describe why the photo can improve.
- Define the main subject, useful supporting elements, optional context, harmful distractions, ROI, subject_extent_box, and protected_regions.
- Decide the composition intent and framing intent before proposing any crop. Centering, thirds, symmetry, leading lines, and negative space are candidate strategies, not fixed rules.
- Propose AI-authored crop_candidates only when reframing may improve the photo. Each candidate must explain its composition intent and tradeoffs. Include a safer/context-preserving option when the decision is ambiguous, and include original-frame when preserving the original may be best.
- Do not score output quality here. Do not write QA penalties here. Leave candidate ranking to the crop judge and final scoring to QA.

Coordinate rules:
- crop_box, roi_box, subject_extent_box, and protected_regions use normalized original-image coordinates. x/y are top-left, w/h are width/height.
- roi_box is the focal area. subject_extent_box is the semantic subject area required by the intended framing; it is not automatically every visible body part, foreground object, furniture piece, or background element.
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


def build_crop_validation_prompt(analysis, candidates, revision_feedback=None, previous_output_available=False):
    user_instruction = analysis.get("user_instruction") or {}

    user_block = ""
    if user_instruction.get("has_user_direction"):
        conflicts = "; ".join(user_instruction.get("conflicts") or [])
        warning = user_instruction.get("warning") or ""
        explicit_priorities = "; ".join(user_instruction.get("explicit_priorities") or [])
        explicit_prohibitions = "; ".join(user_instruction.get("explicit_prohibitions") or [])
        user_block = f"""User instruction priority:
- User intent summary: {user_instruction.get("user_intent_summary") or ""}
- Normalized user direction: {user_instruction.get("normalized_user_direction") or ""}
- Explicit priorities: {explicit_priorities}
- Explicit prohibitions: {explicit_prohibitions}
- Crop guidance: {user_instruction.get("crop_guidance") or ""}
- Warning/conflicts: {conflicts} {warning}

Respect explicit user crop/framing requirements first. If the user did not mention crop/framing, you may still choose a conservative Agent crop when it clearly improves the photo and does not conflict with the user goal. Do not let an automatic crop weaken the user's requested edit."""

    revision_block = ""
    if revision_feedback:
        prev_note = (
            "The previous failed output image is attached after the original photo. "
            "Use it as negative evidence: identify what failed visually, but choose the new framing "
            "from the original photo and the current candidates."
            if previous_output_available else ""
        )
        revision_block = f"""Revision feedback from the previous QA:
{revision_feedback}

This is a retry. Prioritize fixing the QA failure over defending the previous crop. If the previous result failed because of crop damage, cramped framing, missing context, or scene-preservation loss, choose a safer or revised crop that protects the required subject, action, props, and scene context.
{prev_note}"""

    candidates_str = "\n".join(
        f"{i + 1}. {c['id']}: {c['label']} "
        f"requested={json.dumps(c.get('cropBox') or c.get('crop_box'), ensure_ascii=False)} "
        f"adjusted={json.dumps((c.get('localGeometry') or {}).get('cropBox'), ensure_ascii=False)} "
        f"adjustments={json.dumps((c.get('localGeometry') or {}).get('adjustmentReasons') or [], ensure_ascii=False)} "
        f"{('Intent: ' + c['compositionIntent']) if c.get('compositionIntent') else ''} "
        f"{('Tradeoffs: ' + c['tradeoffs']) if c.get('tradeoffs') else ''}"
        for i, c in enumerate(candidates or [])
    )

    crop_intent = json.dumps(analysis.get("crop_intent") or {}, ensure_ascii=False)
    roi_box = json.dumps(analysis.get("roi_box") or {}, ensure_ascii=False)
    subject_extent_box = json.dumps(analysis.get("subject_extent_box") or {}, ensure_ascii=False)
    protected_regions = json.dumps(analysis.get("protected_regions") or [], ensure_ascii=False)
    rec_aspect = (
        analysis.get("recommended_aspect_ratio") or
        (analysis.get("crop_box") or {}).get("aspect_ratio") or
        "original"
    )

    head = f"""You are validating a crop decision for a photo editing agent.
Choose the final framing. The candidates are AI-authored composition options rendered by local geometry.
Local geometry may clamp, normalize, or slightly expand boxes only for bounds, aspect fitting, and hard safety. Treat its adjustment report as evidence, not as an aesthetic decision.
You may select a candidate, select original-frame, or provide a revised_crop_box if every candidate is flawed.

{user_block}

Judge each candidate through these layers:
1. Subject safety: main subject, ROI, required subject_extent_box, protected_regions, role cues, important props, and interaction points remain complete for the intended framing.
2. Framing intent: the crop matches close-up, upper-body, full-body, object-complete, environmental, detail, action, or context-preserving intent. Do not require content that the framing intent does not need.
3. Composition and aspect: centering, thirds, symmetry, leading lines, negative space, or another layout actually improves balance. The aspect ratio should serve the subject and scene, not habit.
4. Empty space and context: blank/background areas should have a clear role; meaningful breathing room and scene context should be preserved when they improve mood, scale, story, dignity, rhythm, or environmental truth.
5. Distraction handling: reassess distractions after the crop. Preserve contextual or neutral elements; remove or crop only elements that still strongly compete.
6. Edge and alignment integrity: avoid cramped tops, awkward cuts, accidental head/face/hand/object clipping, unstable horizon/verticals, and transparent/checkerboard/artificial borders.
7. Local adjustment impact: if local geometry had to expand, clamp, or relax aspect ratio to protect content, prefer the adjusted-safe crop or a revised crop over the unsafe original request.

Downgrade candidates that trade away subject completeness, scene truth, breathing room, alignment stability, or visual balance without a clear compositional gain.
Upgrade original-frame when the proposed crop no longer improves the image after post-crop distraction and context are reassessed.

{revision_block}

Original analysis:
- Main subject: {analysis.get("main_subject") or ""}
- Scene type: {analysis.get("scene_type") or ""}
- Composition intent: {analysis.get("composition_intent") or ""}
- Framing intent: {analysis.get("framing_intent") or analysis.get("portrait_framing") or ""}
- ROI box: {roi_box}
- Subject extent box: {subject_extent_box}
- Protected regions: {protected_regions}
- Portrait framing: {analysis.get("portrait_framing") or ""}
- Layout method: {analysis.get("layout_method") or ""}
- Composition strategy: {analysis.get("composition_strategy") or ""}
- Visual balance strategy: {analysis.get("visual_balance_strategy") or ""}
- Empty space strategy: {analysis.get("empty_space_strategy") or ""}
- Alignment strategy: {analysis.get("alignment_strategy") or ""}
- Distraction strategy: {analysis.get("distraction_strategy") or ""}
- Scene preservation strategy: {analysis.get("scene_preservation_strategy") or ""}
- Crop recommendation: {analysis.get("crop_recommendation") or ""}
- Recommended aspect ratio: {rec_aspect}
- Aspect ratio reason: {analysis.get("aspect_ratio_reason") or ""}
- Crop intent: {crop_intent}

Candidates:
{candidates_str}

Return only valid JSON. User-visible values must be Traditional Chinese.
"""
    schema = """{
  "pass": true,
  "severity": "ok",
  "selected_candidate_id": "ai-plan",
  "reason": "選擇原因。",
  "problems": [],
  "candidate_scores": [
    { "id": "ai-plan", "score": 8, "comment": "評語。" }
  ],
  "revised_crop_box": {
    "enabled": false,
    "x": 0,
    "y": 0,
    "w": 1,
    "h": 1,
    "aspect_ratio": "",
    "reason": "若不需要修正裁切，保持 disabled。"
  },
  "aspect_ratio_judgment": "說明所選比例是否符合主體、陪襯元素、留白與畫面意圖。"
}"""
    return head + schema


def build_edit_prompt(analysis, preset, preset_prompt, user_direction, crop_applied, revision_feedback=None):
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
    rec_aspect = (
        analysis.get("recommended_aspect_ratio") or
        (analysis.get("crop_box") or {}).get("aspect_ratio") or
        "original"
    )

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

    revision_block = ""
    if revision_feedback:
        revision_block = f"""Revision feedback from the previous QA:
{revision_feedback}

This is a retry. Directly fix the QA failure. Do not repeat the failed crop/edit behavior. Preserve the subject, required framing, scene context, and protected details more conservatively than the previous output."""

    return f"""You are editing the supplied image, not creating a new image.
Act like a high-end human retoucher: restrained, photographic, detail-aware, and realistic.

Preserve the original subject, identity, pose, camera angle, scene, product shape, clothing, facial structure, body shape, and important details.
Do not add text, logos, watermarks, extra people, new props, fantasy elements, or a different background.
Do not make the result look synthetic, plastic, or heavily filtered.

Photo analysis:
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
- Aspect ratio reason: {analysis.get("aspect_ratio_reason") or ""}
- Tool strategy: {analysis.get("tool_strategy") or ""}
- Lighting strategy: {analysis.get("lighting_strategy") or ""}
- Memory color strategy: {analysis.get("memory_color_strategy") or ""}
- Crop/reframe recommendation: {analysis.get("crop_recommendation") or ""}
- Crop intent: {crop_intent}
- Applied crop before editing: {"Yes. Continue from this cropped composition and do not expand back to the wider frame." if crop_applied else "No."}
- Correction plan: {correction_plan}
- Must preserve: {preserve_rules}

Style direction:
{preset_prompt}

User extra direction:
{effective_user_direction or "沒有額外指令。"}

{user_block}

{revision_block}

Retouch priorities:
1. Treat the supplied image boundaries as final. Do not re-crop, expand canvas, add borders, create transparent/checkerboard padding, or invent empty side bands.
2. Preserve the original subject, semantic subject extent, protected regions, role cues, interaction points, product/object geometry, scene structure, perspective, lighting direction, and spatial relationships.
3. Improve the existing photograph through light, contrast, color harmony, highlight rolloff, texture, and restrained local cleanup.
4. Reduce distractions through tonal control, local contrast, saturation, subtle blur, or small cleanup. Do not remove, replace, rebuild, or simplify contextual/neutral scene elements just to make the image cleaner.
5. Preserve natural texture in skin, hair, fabric, food, products, architecture, shadows, and background materials. Avoid plastic smoothing, synthetic sharpening, or heavy filters.
6. Respect the selected composition. Preserve meaningful breathing room and empty space when it supports direction, story, scale, calmness, graphic balance, rhythm, environment, or subject dignity.
7. If credible horizontal/vertical references are unintentionally unstable, correct them subtly. Preserve intentional slant only when it clearly supports motion, intimacy, tension, or expression.
8. Keep the final result faithful: the same moment, same camera viewpoint, same environment, and same subject, finished like a skilled retoucher handled it.
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

    head = f"""You are the QA agent for a photo retouching demo.
Compare the original photo and the edited output. Judge whether the output is safe to show to the user as a faithful enhancement.
Be strict. Your score must reflect the worst preservation or composition failure, not the average of good and bad qualities.

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
  "recommendation": "可直接展示，或說明需要人工複查的原因。"
}"""
    return head + schema
