window.PhotoAgentResults = (() => {
  const { els } = window.PhotoAgentDom;
  const { resolveCropBoxForDisplay } = window.PhotoAgentImageTools;

  const DEFAULT_OPTION = { value: "default", label: "Auto" };
  const POSE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "playful", label: "Playful" },
    { value: "candid", label: "Candid" },
    { value: "strong", label: "Strong" }
  ];
  const ANGLE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "high", label: "High Shot" },
    { value: "low", label: "Low Shot" },
    { value: "eye", label: "Eye Level" },
    { value: "side", label: "Side Shot" }
  ];
  const LIGHTING_OPTIONS = [
    DEFAULT_OPTION,
    { value: "gold", label: "Gold Tone" },
    { value: "soft", label: "Soft Glow" }
  ];
  const SIZE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "wide", label: "Wide" },
    { value: "wider", label: "Wider" },
    { value: "close", label: "Close" },
    { value: "closer", label: "Closer" }
  ];

  // category -> {options list, display label}, used to turn analysis.style_options
  // (the raw {pose, angle, lighting, size} values the user picked on the review
  // screen) back into human-readable text on the results page.
  const STYLE_OPTION_GROUPS = {
    pose: { options: POSE_OPTIONS, label: "Pose" },
    angle: { options: ANGLE_OPTIONS, label: "Angle" },
    lighting: { options: LIGHTING_OPTIONS, label: "Lighting" },
    size: { options: SIZE_OPTIONS, label: "Framing size" }
  };

  function buildStyleOptionsText(styleOptions) {
    if (!styleOptions) return "";
    return Object.entries(styleOptions)
      .map(([key, value]) => {
        const group = STYLE_OPTION_GROUPS[key];
        if (!group || !value || value === "default") return "";
        const opt = group.options.find((o) => o.value === value);
        return opt ? `${group.label}: ${opt.label}` : "";
      })
      .filter(Boolean)
      .join("、");
  }

  // fillRecipeAdherenceList's fallback for styleEntries when QA didn't run
  // (or didn't report recipe_adherence.style_items) -- the user's Pose/Angle/
  // Lighting/Size picks (analysis.style_options) were still sent to the edit
  // step and applied, so they should still show up here, same as how
  // fallbackRecipe keeps the retouch_recipe items visible without QA. checked
  // defaults to true for the same reason fallbackRecipe's entries do: there's
  // no QA judgment to say otherwise, so this just reflects "was requested."
  function buildFallbackStyleEntries(styleOptions) {
    if (!styleOptions) return [];
    return Object.entries(styleOptions)
      .map(([key, value]) => {
        const group = STYLE_OPTION_GROUPS[key];
        if (!group || !value || value === "default") return null;
        const opt = group.options.find((o) => o.value === value);
        return opt ? { label: `${group.label}: ${opt.label}`, checked: true, note: "" } : null;
      })
      .filter(Boolean);
  }

  function fillList(element, items, fallbackItems) {
    element.innerHTML = "";
    const source = Array.isArray(items) && items.length ? items : fallbackItems;
    source.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      element.appendChild(li);
    });
  }

  function fillCheckList(element, items, fallbackItems) {
    if (!element) return;
    element.innerHTML = "";
    const source = Array.isArray(items) && items.length ? items : fallbackItems;
    source.forEach((item, index) => {
      const li = document.createElement("li");
      const label = document.createElement("label");

      const cb = document.createElement("span");
      cb.className = "step-cb";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.id = `${element.id}-${index}`;
      input.checked = true;
      const box = document.createElement("span");
      box.className = "step-cb-box";
      cb.appendChild(input);
      cb.appendChild(box);

      const text = document.createElement("span");
      text.className = "step-cb-label";
      text.textContent = item;

      label.appendChild(cb);
      label.appendChild(text);
      li.appendChild(label);
      element.appendChild(li);
    });
  }

  // Read-only variant of fillCheckList -- checked state reflects whether the
  // QA step judged that recipe item as followed, not a user-toggleable choice.
  // styleItems are the Pose/Angle/Lighting/Size overrides the user picked on
  // the review screen -- grouped with followedEntries since both describe
  // what the output *should* show. rejectedItems are the recipe items the
  // user unchecked before submission -- "checked" here means the QA step
  // confirmed the output correctly avoided them, so a violated rejection
  // shows up as an unchecked/failed row.
  function fillRecipeAdherenceList(element, adherenceItems, styleItems, rejectedItems, fallbackRecipe, fallbackStyleOptions) {
    if (!element) return;
    element.innerHTML = "";
    const followedEntries = Array.isArray(adherenceItems) && adherenceItems.length
      ? adherenceItems.map((it) => ({ label: it.item, checked: it.followed === true, note: it.note }))
      : (fallbackRecipe || []).map((label) => ({ label, checked: true, note: "" }));
    const styleEntries = Array.isArray(styleItems) && styleItems.length
      ? styleItems.map((it) => ({ label: it.item, checked: it.followed === true, note: it.note }))
      : buildFallbackStyleEntries(fallbackStyleOptions);
    const rejectedEntries = Array.isArray(rejectedItems)
      ? rejectedItems.map((it) => ({
          label: `不應出現：${it.item}`,
          checked: it.violated !== true,
          note: it.note
        }))
      : [];

    if (!followedEntries.length && !styleEntries.length && !rejectedEntries.length) {
      const li = document.createElement("li");
      li.textContent = "尚未取得修圖建議項目。";
      element.appendChild(li);
      return;
    }

    const renderEntry = (entry) => {
      const li = document.createElement("li");
      const label = document.createElement("label");

      const icon = document.createElement("span");
      icon.className = `adherence-icon ${entry.checked ? "is-done" : "is-pending"}`;
      icon.textContent = entry.checked ? "✓" : "✕";

      const text = document.createElement("span");
      text.className = "step-cb-label";
      text.textContent = entry.note ? `${entry.label}（${entry.note}）` : entry.label;

      label.appendChild(icon);
      label.appendChild(text);
      li.appendChild(label);
      element.appendChild(li);
    };

    followedEntries.forEach(renderEntry);
    styleEntries.forEach(renderEntry);
    // Separate "items to adjust" (recipe + style overrides) from "items that
    // must not be touched" so the two groups read as distinct, not one
    // undifferentiated checklist.
    if ((followedEntries.length || styleEntries.length) && rejectedEntries.length) {
      const divider = document.createElement("li");
      divider.className = "adherence-divider";
      element.appendChild(divider);
    }
    rejectedEntries.forEach(renderEntry);
  }

  // Same checkbox-list look as fillCheckList, but backed by radio inputs
  // sharing one `name` -- mutually exclusive selection within the group,
  // enforced natively by the browser rather than custom JS.
  function fillRadioList(element, options, name, defaultValue) {
    if (!element) return;
    element.innerHTML = "";
    options.forEach((opt, index) => {
      const li = document.createElement("li");
      const label = document.createElement("label");

      const cb = document.createElement("span");
      cb.className = "step-cb";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = opt.value;
      input.id = `${name}-${index}`;
      input.checked = opt.value === defaultValue;
      const box = document.createElement("span");
      box.className = "step-cb-box";
      cb.appendChild(input);
      cb.appendChild(box);

      const text = document.createElement("span");
      text.className = "step-cb-label";
      text.textContent = opt.label;

      label.appendChild(cb);
      label.appendChild(text);
      li.appendChild(label);
      element.appendChild(li);
    });
  }

  // Updates a style-option pill's displayed value (e.g. "Playful") to match
  // whichever radio in its list is currently checked -- shared by the initial
  // fillRadioList render and by app.js after it restores a saved pick or the
  // user selects a new one. No-ops quietly if the list isn't wrapped in the
  // .ap-style-item/.ap-style-value pill structure (e.g. the batch options
  // row, which still uses the always-visible list layout).
  function syncStyleTrigger(listElement) {
    if (!listElement) return;
    const valueEl = listElement.closest(".ap-style-item")?.querySelector(".ap-style-value");
    if (!valueEl) return;
    const checked = listElement.querySelector("input:checked");
    const label = checked?.closest("label")?.querySelector(".step-cb-label")?.textContent;
    valueEl.textContent = label || "Default";
  }

  function joinParts(parts) {
    return parts.filter(Boolean).join(" ");
  }

  function stripFieldLabel(value, labels) {
    if (!value) return "";
    const escaped = labels.map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
    return String(value)
      .replace(new RegExp(`^\\s*(${escaped})\\s*[：:]\\s*`), "")
      .replace(/[。．.]+$/g, "");
  }

  function sentence(label, value) {
    const text = stripFieldLabel(value, [label]);
    return text ? `${label}：${text}。` : "";
  }

  function numberOrDash(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(2) : "-";
  }

  function formatBox(label, box) {
    if (!box) return "";
    return `${label}: x:${numberOrDash(box.x)} y:${numberOrDash(box.y)} w:${numberOrDash(box.w)} h:${numberOrDash(box.h)}`;
  }

  function formatStatus(qa) {
    if (!qa) return "未檢查";
    if (qa.pass === false) return qa.overall_status || "需人工複查";
    return qa.overall_status || "可展示";
  }

  function renderTags(element, tags) {
    element.innerHTML = "";
    tags.filter(Boolean).forEach((tag) => {
      const chip = document.createElement("span");
      chip.textContent = tag;
      element.appendChild(chip);
    });
  }

  function buildPhotoContentSummary(analysis) {
    const summary = stripFieldLabel(analysis.photo_summary, ["照片內容", "照片摘要", "內容"]);
    const subject = stripFieldLabel(analysis.main_subject, ["主體", "主要主體"]);
    const scene = stripFieldLabel(analysis.scene_type, ["場景", "場景類型"]);
    if (summary) {
      return joinParts([
        `${summary}。`,
        subject && !summary.includes(subject) ? `主體：${subject}。` : "",
        scene && !summary.includes(scene) ? `場景：${scene}。` : ""
      ]);
    }
    return joinParts([
      subject ? `主體：${subject}。` : "已完成照片內容辨識。",
      scene ? `場景：${scene}。` : ""
    ]);
  }

  function formatCandidate(candidate, validation) {
    const selected = validation?.selected_candidate_id === candidate.id;
    const score = (validation?.candidate_scores || []).find((item) => item.id === candidate.id);
    const label = candidate.label || candidate.label_en || candidate.id;
    const verdict = selected ? "最終選擇" : score ? `候選評分 ${score.score}/10` : "候選方案";
    const comment = score?.comment || candidate.compositionIntent || candidate.tradeoffs || "";
    return `${verdict} - ${label}${comment ? `：${comment}` : ""}`;
  }

  function checksToList(qa) {
    if (!qa?.checks) return [];
    const labels = {
      subject_preserved: "主體與身分保留",
      user_intent_followed: "使用者目標達成",
      crop_safe: "裁切安全",
      protected_regions_safe: "保護區域安全",
      framing_intent_respected: "取景意圖一致",
      aspect_ratio_feels_intentional: "比例有構圖意義",
      composition_method_matches: "構圖方法吻合",
      empty_space_has_function: "留白有功能",
      alignment_stable_or_intentional: "水平/透視穩定",
      distractions_reassessed_after_crop: "裁切後重新評估干擾",
      scene_preserved: "場景真實保留",
      frame_breathing_room_ok: "邊緣呼吸感合理",
      not_overedited: "未過度修圖",
      light_color_improved: "光色改善"
    };
    return Object.entries(qa.checks)
      .slice(0, 8)
      .map(([key, value]) => `${value === true ? "通過" : value === false ? "注意" : "未定"}：${labels[key] || key}`);
  }

  function renderResult({ data, analysis, preset, imageModel }) {
    const qa = data.qualityCheck || null;
    const validation = analysis.crop_validation || null;

    els.generatedImg.src = data.image;
    els.downloadLink.href = data.image;
    els.modelName.textContent = data.model || imageModel;
    els.resultSummary.textContent = analysis.photo_summary || "已完成影像分析與輸出。";
    els.qaScore.textContent = Number.isFinite(Number(qa?.score)) ? `${qa.score}/10` : "-";
    if (els.recipeVerdictScore) {
      const recipeScore = qa?.recipe_adherence?.score;
      els.recipeVerdictScore.textContent = Number.isFinite(Number(recipeScore)) ? `${recipeScore}/10` : "-";
    }

    els.subjectText.textContent = buildPhotoContentSummary(analysis);
    renderTags(els.subjectTags, [
      analysis.subject_category || "未知類型",
      analysis.scene_category || "未知場景"
    ]);

    // #r-intent is now a <ul> — build list items from intent data
    const userInstruction = analysis.user_instruction || {};
    const intentItems = userInstruction.has_user_direction
      ? [
          userInstruction.user_intent_summary ? `使用者目標：${userInstruction.user_intent_summary}。` : "",
          userInstruction.normalized_user_direction ? `執行方向：${userInstruction.normalized_user_direction}。` : "",
          userInstruction.warning ? `提醒：${userInstruction.warning}` : ""
        ].filter(Boolean)
      : [analysis.edit_intent || preset.intent || "保留原照並提升質感。"];
    fillList(document.querySelector("#r-intent"), intentItems, ["保留原照並提升質感。"]);

    document.querySelector("#r-status").textContent = formatStatus(qa);

    els.diagnosisText.textContent = joinParts([
      analysis.aesthetic_diagnosis || "照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。",
      analysis.main_subject ? `主體：${analysis.main_subject}。` : "",
      analysis.scene_type ? `場景：${analysis.scene_type}。` : ""
    ]);

    els.philosophyText.textContent = joinParts([
      userInstruction.has_user_direction
        ? `優先遵守使用者目標：${userInstruction.normalized_user_direction || userInstruction.user_intent_summary}。`
        : "",
      analysis.edit_philosophy || "以保留原照內容為前提，讓照片看起來更乾淨、更有完成度。",
      analysis.scene_preservation_strategy ? `場景保留：${analysis.scene_preservation_strategy}` : ""
    ]);

    els.moodText.textContent = joinParts([
      qa?.score_reason || qa?.summary || "尚未執行輸出品質檢查。",
      qa?.recommendation || ""
    ]);

    els.compositionText.textContent = joinParts([
      analysis.composition_strategy ? sentence("原圖觀察", analysis.composition_strategy) : "",
      analysis.composition_intent ? sentence("建議構圖", analysis.composition_intent) : "",
      analysis.framing_intent ? sentence("建議取景", analysis.framing_intent) : "",
      analysis.recommended_aspect_ratio
        ? `建議比例：${analysis.recommended_aspect_ratio}${analysis.aspect_ratio_reason ? `，${stripFieldLabel(analysis.aspect_ratio_reason, ["比例", "建議比例", "比例理由"])}` : ""}。`
        : "",
      analysis.layout_method ? sentence("布局方法", analysis.layout_method) : ""
    ]);

    els.roiText.textContent = joinParts([
      analysis.visual_balance_strategy ? sentence("平衡", analysis.visual_balance_strategy) : "",
      analysis.empty_space_strategy ? sentence("留白", analysis.empty_space_strategy) : "",
      analysis.alignment_strategy ? sentence("水平/透視", analysis.alignment_strategy) : ""
    ]);

    els.toolText.textContent = joinParts([
      analysis.roi_anchor || "ROI 是主體最重要、不可被裁掉的視覺區域。",
      analysis.roi_box ? `${formatBox("ROI box", analysis.roi_box)}。` : "",
      analysis.subject_extent_box ? `${formatBox("主體範圍", analysis.subject_extent_box)}。` : "",
      Array.isArray(analysis.protected_regions) && analysis.protected_regions.length
        ? `保護區域：${analysis.protected_regions.slice(0, 5).map((r) => r.label || r.reason || "重要區域").join("、")}。`
        : "",
      analysis.tool_strategy ? `工具分工：${analysis.tool_strategy}` : "",
      data.localOnly ? "目前使用本機處理，未執行 AI 影像生成。" : "目前已執行 AI 影像修圖。"
    ]);

    els.lightColorText.textContent = joinParts([
      analysis.lighting_strategy || "改善亮度層次與主體分離，避免過曝、死黑或平面化。",
      analysis.memory_color_strategy || "維持膚色、天空、綠色、食物與材質的可信記憶色。"
    ]);

    if (els.styleOptionsText) {
      const styleOptionsText = buildStyleOptionsText(analysis.style_options);
      els.styleOptionsText.hidden = !styleOptionsText;
      els.styleOptionsText.textContent = styleOptionsText ? `使用者風格設定：${styleOptionsText}` : "";
    }

    const cropBox = resolveCropBoxForDisplay(
      analysis.crop_box,
      analysis,
      els.originalImg.naturalWidth,
      els.originalImg.naturalHeight
    );
    const cropApplied = cropBox
      ? `已套用裁切${cropBox.aspectRatio ? `，比例 ${cropBox.aspectRatio}` : ""}：x:${cropBox.x.toFixed(2)} y:${cropBox.y.toFixed(2)} w:${cropBox.w.toFixed(2)} h:${cropBox.h.toFixed(2)}。`
      : "保留原始構圖。";
    const validationText = validation
      ? `AI 最終選擇：${validation.selected_candidate_label || validation.selected_candidate_id || "最佳候選"}。${validation.reason || ""}`
      : "";
    els.cropText.textContent = joinParts([
      analysis.recommended_aspect_ratio
        ? `AI 建議輸出比例：${analysis.recommended_aspect_ratio}${analysis.aspect_ratio_reason ? `（${analysis.aspect_ratio_reason}）` : ""}。`
        : "",
      analysis.crop_recommendation || "未提供裁切建議。",
      cropApplied,
      validationText
    ]);

    els.qualityText.textContent = qa
      ? joinParts([
          `${qa.overall_status || (qa.pass ? "可展示" : "需複查")}${Number.isFinite(Number(qa.score)) ? `，分數 ${qa.score}/10` : ""}。`,
          qa.summary || "",
          Array.isArray(qa.warnings) && qa.warnings.length ? `提醒：${qa.warnings.join("、")}` : "",
          qa.recommendation || ""
        ])
      : "尚未執行輸出品質檢查。";

    fillList(els.issuesList, (analysis.detected_issues || []).filter(Boolean), [
      "光線或對比仍可更有層次。",
      "色彩關係可以更穩定。",
      "背景或邊緣干擾可再降低。"
    ]);

    const candidates = Array.isArray(analysis.crop_candidates) ? analysis.crop_candidates.slice(0, 5) : [];
    fillList(
      els.cropCandidatesList,
      candidates.map((c) => formatCandidate(c, validation)),
      [validationText || cropApplied]
    );

    fillList(els.recipeList, analysis.retouch_recipe || [], [
      "先校正曝光與白平衡。",
      "調整局部對比與主體清晰度。",
      "控制背景干擾並保留自然質感。"
    ]);

    const recipeAdherence = qa?.recipe_adherence || null;
    if (els.recipeScoreText) {
      els.recipeScoreText.textContent = recipeAdherence && Number.isFinite(Number(recipeAdherence.score))
        ? joinParts([`修圖建議達成度：${recipeAdherence.score}/10。`, recipeAdherence.reason || ""])
        : "尚未取得修圖建議達成度。";
    }
    fillRecipeAdherenceList(els.recipeScoreList, recipeAdherence?.items, recipeAdherence?.style_items, recipeAdherence?.rejected_items, analysis.retouch_recipe, analysis.style_options);

    fillList(els.qaList, [
      ...checksToList(qa),
      ...(Array.isArray(qa?.warnings) ? qa.warnings.map((w) => `提醒：${w}`) : [])
    ], ["尚未取得 QA 檢查結果。"]);
    fillList(els.correctionList, analysis.correction_plan, [
      "套用必要裁切或保留原構圖。",
      "提升整體光線與色彩完成度。",
      "讓主體更清楚但不過度銳化。"
    ]);
    fillList(els.preserveList, analysis.preserve_rules, [
      "保留主體、場景與原始拍攝角度。",
      "不新增文字、標誌、人物或不相關物件。",
      "避免塑膠感、重濾鏡與不自然改造。"
    ]);

    renderPrompts(analysis.prompts || {});
  }

  function renderPrompts(prompts) {
    const setPrompt = (el, text) => {
      if (el) el.textContent = text || "（此步驟未呼叫 GPT）";
    };
    setPrompt(els.promptAnalysis, prompts.photo_analysis);
    setPrompt(els.promptReview, prompts.user_instruction_review);
    setPrompt(els.promptCrop, prompts.crop_validation);
    setPrompt(els.promptEdit, prompts.image_edit);
    setPrompt(els.promptQa, prompts.quality_check);
  }

  // Shown mid-processing, right after analyze_stage finishes and before the
  // user clicks "continue" -- a lightweight subset of renderResult's fields
  // (no image/QA yet, those only exist after finish_stage runs).
  function renderAnalysisPreview({ analysis, preset }) {
    if (els.apSubject) els.apSubject.textContent = buildPhotoContentSummary(analysis);
    if (els.apSubjectTags) {
      renderTags(els.apSubjectTags, [
        analysis.subject_category || "未知類型",
        analysis.scene_category || "未知場景"
      ]);
    }

    if (els.apDiagnosis) {
      els.apDiagnosis.textContent = joinParts([
        analysis.aesthetic_diagnosis || "照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。",
        analysis.main_subject ? `主體：${analysis.main_subject}。` : "",
        analysis.scene_type ? `場景：${analysis.scene_type}。` : ""
      ]);
    }

    if (els.apComposition) {
      const userInstruction = analysis.user_instruction || {};
      els.apComposition.textContent = joinParts([
        userInstruction.has_user_direction
          ? `使用者目標：${userInstruction.user_intent_summary || userInstruction.normalized_user_direction || ""}。`
          : (analysis.edit_intent || preset?.intent || ""),
        analysis.composition_intent ? sentence("建議構圖", analysis.composition_intent) : ""
      ]);
    }

    fillCheckList(els.apRecipeList, analysis.retouch_recipe, ["尚未取得修圖建議。"]);

    // Rebuild the style-option groups each run (defaults to "Default
    // (analyzed)"), and re-evaluate which apply. Pose only makes sense for a
    // person subject; Angle applies to any subject (product/scene shots
    // benefit from camera-angle framing too). The actual retouch behavior
    // for these selections is not wired up yet -- this is UI only for now.
    if (els.apOptionsToggle) els.apOptionsToggle.open = true;
    fillRadioList(els.apPoseList, POSE_OPTIONS, "ap-pose", "default");
    fillRadioList(els.apAngleList, ANGLE_OPTIONS, "ap-angle", "default");
    fillRadioList(els.apLightingList, LIGHTING_OPTIONS, "ap-lighting", "default");
    fillRadioList(els.apSizeList, SIZE_OPTIONS, "ap-size", "default");
    [els.apPoseList, els.apAngleList, els.apLightingList, els.apSizeList].forEach(syncStyleTrigger);
    const isPerson = analysis.subject_category === "人物";
    if (els.apGroupPose) els.apGroupPose.hidden = !isPerson;
    if (els.apGroupAngle) els.apGroupAngle.hidden = false;
  }

  // Fills the batch-level Pose/Angle/Lighting/Size radio groups with the same
  // option lists as the single-image review screen. Called once at startup --
  // unlike renderAnalysisPreview, this isn't re-run per image, since it holds
  // one shared default for the whole batch rather than a per-image choice.
  function renderBatchStyleOptions() {
    fillRadioList(els.batchPoseList, POSE_OPTIONS, "batch-pose", "default");
    fillRadioList(els.batchAngleList, ANGLE_OPTIONS, "batch-angle", "default");
    fillRadioList(els.batchLightingList, LIGHTING_OPTIONS, "batch-lighting", "default");
    fillRadioList(els.batchSizeList, SIZE_OPTIONS, "batch-size", "default");
    [els.batchPoseList, els.batchAngleList, els.batchLightingList, els.batchSizeList].forEach(syncStyleTrigger);
  }

  return { renderResult, renderAnalysisPreview, renderBatchStyleOptions, syncStyleTrigger };
})();
