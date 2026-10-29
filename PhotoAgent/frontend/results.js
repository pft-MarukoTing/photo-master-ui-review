window.PhotoAgentResults = (() => {
  const { els } = window.PhotoAgentDom;
  const { resolveCropBoxForDisplay, resolveCropBoxWithReport } = window.PhotoAgentImageTools;

  // Mirrors app.js's isAnalysisPreviewLocked (same ap-readonly-locked class,
  // set by setAnalysisPreviewDisabled) -- checked here too because
  // fillCheckList/buildPersonIssueItems below tear down and rebuild their
  // checkboxes from scratch on every re-render (a recipe/advanced-options
  // change, switching 人像檢查 tabs, ...), which would otherwise silently
  // undo a one-time disable sweep the next time either rebuilds.
  function isReadOnlyLocked() {
    return !!(els.analysisPreview?.classList.contains("ap-readonly-locked")
      || els.apOptionsCardInline?.classList.contains("ap-readonly-locked"));
  }

  const DEFAULT_OPTION = { value: "default", label: tr("不指定") };
  const POSE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "playful", label: tr("俏皮") },
    { value: "candid", label: tr("自然抓拍") },
    { value: "strong", label: tr("強勢") },
    { value: "elegant", label: tr("優雅") },
    { value: "dynamic", label: tr("動感") },
    { value: "cool", label: tr("酷帥") },
    { value: "vsign", label: tr("勝利手勢") },
    { value: "cheer", label: tr("歡呼") }
  ];
  const ANGLE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "high", label: tr("俯拍") },
    { value: "low", label: tr("仰拍") },
    { value: "eye", label: tr("平視") }
  ];
  const LIGHTING_OPTIONS = [
    DEFAULT_OPTION,
    { value: "gold", label: tr("金色光調") },
    { value: "soft", label: tr("柔光") },
    { value: "cool", label: tr("藍調光線") },
    { value: "night", label: tr("夜色") }
  ];
  const SIZE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "wide", label: tr("遠景") },
    { value: "wider", label: tr("廣角") },
    { value: "close", label: tr("近景") },
    { value: "closer", label: tr("特寫") }
  ];

  // Maps each non-default option value to its example image in
  // assets/optional_style/<group>/ -- used by fillStyleOptionList to show the
  // 修圖風格 tab's option buttons as image thumbnails instead of plain text.
  // A value with no entry here -- "default"/Auto -- renders as a text-only
  // card showing its label instead.
  const STYLE_OPTION_IMAGES = {
    pose: {
      playful: "pose-playful.png", candid: "pose-candid.png", strong: "pose-strong.png",
      elegant: "pose-elegant.png", dynamic: "pose-dynamic.png", cool: "pose-cool.png",
      vsign: "pose-vsing.png", cheer: "pose-cheer.png"
    },
    angle: { high: "angle-highshot.png", low: "angle-lowshot.png", eye: "angle-eyelevel.png" },
    lighting: { gold: "lighting-goldtone.png", soft: "lighting-soft.png", cool: "lighting-cool.png", night: "lighting-night.png" },
    size: { wide: "size-wide.png", wider: "size-wider.png", close: "size-close.png", closer: "size-closer.png" }
  };

  // category -> {options list, display label}, used to turn analysis.style_options
  // (the raw {pose, angle, lighting, size} values the user picked on the review
  // screen) back into human-readable text on the results page.
  const STYLE_OPTION_GROUPS = {
    pose: { options: POSE_OPTIONS, label: tr("姿勢") },
    angle: { options: ANGLE_OPTIONS, label: tr("拍攝角度") },
    lighting: { options: LIGHTING_OPTIONS, label: tr("光線") },
    size: { options: SIZE_OPTIONS, label: tr("取景範圍") }
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
      .join(tr("、"));
  }

  // Same non-default-only filtering as buildStyleOptionsText, but as plain
  // option labels ("Playful", "Close") for the floating recipe-tags pills on
  // the result photo itself -- no category prefix, since the pills sit
  // directly on the image rather than in a labeled text field. Portrait
  // execution labels are appended separately after the actual Editor prompt
  // is inspected below.
  function buildStyleOptionTagLabels(styleOptions) {
    if (!styleOptions) return [];
    return Object.entries(styleOptions)
      .map(([key, value]) => {
        const group = STYLE_OPTION_GROUPS[key];
        if (!group || !value || value === "default") return "";
        const opt = group.options.find((o) => o.value === value);
        return opt ? opt.label : "";
      })
      .filter(Boolean);
  }

  // Result labels describe work that reached an actual image-editor prompt,
  // not merely a review toggle or an analyzer recommendation.  The prompt is
  // stored on analysis.prompts by pipeline.finish_stage; the second prompt is
  // included because a two-pass run executes gaze in Step 5b.
  function actualEditorPromptText(analysis) {
    const prompts = analysis?.prompts;
    if (!prompts || typeof prompts !== "object") return "";
    return [prompts.image_edit, prompts.gaze_repair_pass]
      .filter((value) => typeof value === "string" && value.trim())
      .map((value) => value.trim())
      .join("\n");
  }

  const EXECUTED_STYLE_PROMPT_MARKERS = {
    pose: /(?:^|\n)-\s*Pose\s*:/i,
    angle: /(?:^|\n)-\s*Angle\s*:/i,
    lighting: /(?:^|\n)-\s*Lighting\s*:/i,
    size: /(?:^|\n)-\s*Framing size\s*:/i,
  };

  // These markers identify positive execution blocks.  Preservation blocks
  // intentionally use different headings, so an analyzer note or a disabled
  // topic cannot create a completed label by itself.
  const EXECUTED_FACE_PROMPT_TAGS = [
    {
      field: "blink_issue",
      option: "eye_closed_detection",
      label: tr("閉眼偵測"),
      markers: [
        /Accidental blink repair priority:/i,
        /PRIMARY EYE-PAIR REPAIR/i,
        /\beyelid correction\b/i,
        /^\s*-\s*Eye-pair repair\s*\([^\n]*\):\s*(?:open|use)\b/im,
      ],
    },
    {
      field: "gaze_issue",
      option: "gaze_contact_detection",
      label: tr("眼神對焦"),
      markers: [
        /Approved gaze repair execution instructions:/i,
        /ONLY task for this edit:[\s\S]{0,180}redirect[^\n]*gaze/i,
        /Use a .* eyes-(?:only|plus-head\/face) correction[\s\S]{0,240}direct lens contact/i,
        /Required change:[\s\S]{0,900}(?:direct(?:ly)?|camera lens)[^\n]*/i,
        /^\s*-\s*Use a [^\n]*eyes-(?:only|plus-head\/face) correction[^\n]*;\s*reach clear direct lens contact\./im,
      ],
    },
    {
      field: "hair_issue",
      option: "hair_mess_detection",
      label: tr("亂髮偵測"),
      markers: [
        /Hair tidying priority:/i,
        /Still-air hair \(user-selected, mandatory\):/i,
        /Hair level — (?:local cleanup|fully groomed|preserve motion)/i,
        /Hair (?:correction plan|target) —?/i,
      ],
    },
    {
      field: "smile_issue",
      option: "smile_adjustment",
      label: tr("笑容調整"),
      markers: [
        /Smile adjustment \(user-selected, mandatory\)/i,
        /target_smile:/i,
        /(?:Transform this person's existing|Render) .*? smile on the existing face/i,
      ],
    },
  ];

  function promptMatchesAny(prompt, markers) {
    return markers.some((marker) => marker.test(prompt));
  }

  function buildExecutedStyleOptionTagLabels(styleOptions, analysis, pending = false) {
    // 只照需求改也會把挑的風格接在需求後面送出，照樣以實際 prompt 為準。
    if (pending) return [];
    const prompt = actualEditorPromptText(analysis);
    if (!prompt) return [];
    return Object.entries(styleOptions || {})
      .map(([key, value]) => {
        if (!value || value === "default") return "";
        const group = STYLE_OPTION_GROUPS[key];
        const marker = EXECUTED_STYLE_PROMPT_MARKERS[key];
        if (!group || !marker || !marker.test(prompt)) return "";
        const option = group.options.find((item) => item.value === value);
        return option ? option.label : "";
      })
      .filter(Boolean);
  }

  // Only retained, approved items whose command appears in this run's Editor
  // prompt qualify. Do not infer execution from the original review list.
  function buildExecutedRecipeItems(analysis, pending = false) {
    if (pending || analysis?.user_instruction?.overwrite_prompt) return [];
    const prompt = actualEditorPromptText(analysis);
    if (!prompt) return [];
    const plan = Array.isArray(analysis?.correction_plan) ? analysis.correction_plan : [];
    const recipes = Array.isArray(analysis?.retouch_recipe) ? analysis.retouch_recipe : [];
    return [...new Set(plan.flatMap((command, index) => {
      if (typeof command !== "string" || !command.trim() || !prompt.includes(command.trim())) return [];
      const label = recipes[index];
      return [typeof label === "string" && label.trim() ? shownRecipe(label.trim()) : command.trim()];
    }))];
  }

  function buildExecutedFaceEditTagLabels(analysis, pending = false) {
    if (pending || analysis?.user_instruction?.overwrite_prompt) return [];
    const prompt = actualEditorPromptText(analysis);
    if (!prompt) return [];
    const advanced = analysis?._advanced_options || {};
    return EXECUTED_FACE_PROMPT_TAGS
      .filter((entry) => advanced[entry.option] !== false && promptMatchesAny(prompt, entry.markers))
      .map((entry) => entry.label);
  }

  // Set by applyDebugUiVisibility (app.js). Both inspection aids on the result
  // canvas -- the ratio pill below and the #analysis-boxes overlay -- are
  // testbed-only; a regular user's result photo shouldn't carry either.
  let debugUiActive = false;
  function setDebugUi(active) {
    debugUiActive = !!active;
    drawAnalysisBoxes();
    renderFaceReviewCards();
  }

  // Body Reshape is an internal feature switch owned by the backend.  The
  // value in config.js is only an initial default; app.js replaces it with
  // /api/config once the page bootstraps.  Keep the gate here as well as in
  // app.js so stale analysis objects cannot render a body prompt when the
  // feature is disabled.
  let bodyReshapeEnabled = window.PhotoAgentConfig?.enableBodyReshape !== false;
  // This is the review decision for the current analysis, not a second body
  // plan.  The Analyze sentence remains visible when the user switches it
  // off, so they can turn it back on without re-running analysis; app.js
  // serializes the boolean at the edit boundary.
  let bodyReshapeReviewEnabled = true;
  let bodyReshapeReviewAnalysis = null;

  function setBodyReshapeEnabled(enabled) {
    const next = enabled !== false;
    const changed = bodyReshapeEnabled !== next;
    bodyReshapeEnabled = next;
    if (changed) {
      renderFaceReviewCards();
      renderBodyReshapeControl(bodyReshapeReviewAnalysis, { resetState: false });
    }
  }

  // Body Reshape's Analyze sentence is the exact text that will be copied to
  // Editor when the review switch is on.  It is intentionally independent of
  // the switch state here: an off switch suppresses execution, not
  // transparency about what Analyze proposed.
  function bodyReshapePromptForDisplay(analysis) {
    if (!bodyReshapeEnabled) return "";
    const body = analysis?.body_reshape;
    if (body?.should_edit === false) return "";
    if (body?.analysis_status === "error") return "";
    const prompt = body?.edit_prompt;
    return typeof prompt === "string" && prompt.trim() ? prompt.trim() : "";
  }

  function setBodyReshapeReviewAnalysis(analysis, { resetState = false } = {}) {
    bodyReshapeReviewAnalysis = analysis || null;
    if (resetState || bodyReshapeReviewAnalysis) {
      bodyReshapeReviewEnabled = bodyReshapeReviewAnalysis?._body_reshape_enabled !== false;
    }
    renderBodyReshapeControl(bodyReshapeReviewAnalysis, { resetState: false });
  }

  function setBodyReshapeReviewEnabled(enabled) {
    bodyReshapeReviewEnabled = enabled !== false;
    if (bodyReshapeReviewAnalysis && typeof bodyReshapeReviewAnalysis === "object") {
      if (bodyReshapeReviewEnabled) delete bodyReshapeReviewAnalysis._body_reshape_enabled;
      else bodyReshapeReviewAnalysis._body_reshape_enabled = false;
    }
    renderBodyReshapeControl(bodyReshapeReviewAnalysis, { resetState: false });
  }

  function getBodyReshapeReviewEnabled() {
    return bodyReshapeEnabled && bodyReshapeReviewEnabled !== false;
  }

  // Reuses the same compact iOS switch language as the per-person 「笑容調整」
  // rows.  There are no region controls or percentage/strength sliders here:
  // this one switch is the complete user decision for the holistic Analyze
  // sentence, and the sentence itself is shown directly underneath it.
  function renderBodyReshapeControl(analysis, { resetState = false } = {}) {
    const host = els.apBodyReshapeControl;
    if (!host) return;
    if (resetState) {
      bodyReshapeReviewEnabled = analysis?._body_reshape_enabled !== false;
    }
    const prompt = bodyReshapePromptForDisplay(analysis);
    host.replaceChildren();
    host.hidden = !prompt;
    if (!prompt) return;

    const row = document.createElement("div");
    row.className = "ap-body-reshape-row";
    const toggle = document.createElement("label");
    toggle.className = "ios-switch ap-body-reshape-toggle";
    toggle.title = tr("決定是否將身形調整指令送給 Editor");
    const input = document.createElement("input");
    input.type = "checkbox";
    const track = document.createElement("span");
    track.className = "ios-track";
    const thumb = document.createElement("span");
    thumb.className = "ios-thumb";
    track.appendChild(thumb);
    toggle.appendChild(input);
    toggle.appendChild(track);
    row.appendChild(toggle);

    const name = document.createElement("span");
    name.className = "ap-body-reshape-name";
    name.textContent = tr("身形調整");
    row.appendChild(name);
    const icon = document.createElement("span");
    row.appendChild(icon);
    const verdict = document.createElement("span");
    verdict.className = "step-cb-label ap-body-reshape-verdict";
    row.appendChild(verdict);

    const refresh = () => {
      const active = bodyReshapeReviewEnabled !== false;
      input.checked = active;
      icon.className = `adherence-icon ${active ? "is-pending" : "is-done"}`;
      icon.textContent = active ? "!" : "✓";
      verdict.textContent = active ? tr("可以調整") : tr("已關閉");
      toggle.setAttribute("aria-label", active ? tr("關閉身形調整") : tr("開啟身形調整"));
    };
    input.addEventListener("change", () => {
      setBodyReshapeReviewEnabled(input.checked);
    });
    refresh();
    host.appendChild(row);

    const promptLine = document.createElement("p");
    promptLine.className = "ap-body-reshape-prompt";
    const promptLabel = document.createElement("span");
    promptLabel.className = "ap-body-reshape-prompt-label";
    promptLabel.textContent = "";
    promptLine.appendChild(promptLabel);
    promptLine.appendChild(document.createTextNode(prompt));
    host.appendChild(promptLine);
  }

  // recommended_aspect_ratio on a RESULT is the ratio that result was actually
  // made at: resolve_output_aspect_ratio (backend) settles it from the user's
  // 長寬比 choice plus the proposed crop, and writes it onto the analysis
  // before the edit runs. So rendering a result just reads it.
  //
  // previewAspectRatio answers the other question -- what the NEXT run would do
  // with the choice now selected, without waiting for that run: the ratio, the
  // crop the edit model would actually be sent, and the report lines quoting
  // them.
  //
  // It mirrors the backend's own decision rather than re-deriving it: "auto"
  // is whatever analyze_stage measured off the crop rectangle (kept as
  // auto_aspect_ratio precisely so it survives a "keep original" run), and
  // "original" is the source frame. resolveCropBoxWithReport then fits the
  // proposed rectangle to it exactly as resolve_crop_box_with_report does
  // server-side, so the red box here is the box that will be sent.
  function aspectRatioForPref(analysis, pref) {
    if (pref === "original") return "original";
    // A fixed W:H pick is already the answer; only "auto" has to be looked up.
    if (pref && pref !== "auto") return pref;
    return analysis?.auto_aspect_ratio || analysis?.recommended_aspect_ratio || "original";
  }

  const REFRAME_AXIS_NAMES = new Set(["crop", "cropping", "framing", "reframing"]);
  const RATIO_TOKEN_PATTERN = "\\d+\\s*[:：]\\s*\\d+";

  function axesIncludeReframing(entry) {
    const axes = Array.isArray(entry) ? entry : [entry];
    return axes.some((axis) => REFRAME_AXIS_NAMES.has(String(axis || "").trim().toLowerCase()));
  }

  // The starts syncPreviewReframingText writes, in another language: taken
  // off too, so a row rebuilt for a new ratio does not say it twice.
  const REFRAME_LEADS = [...trLeads("維持原圖比例，{detail}"), ...trLeads("調整為{ratioLabel}，{detail}")];

  function reframingDetail(text) {
    let value = String(text || "").trim().replace(/^\s*重新構圖\s*[:：]\s*/, "");
    REFRAME_LEADS.forEach((lead) => { value = value.replace(lead, ""); });
    value = value.replace(
      new RegExp(`^\\s*(?:以\\s*${RATIO_TOKEN_PATTERN}\\s*輸出比例|維持原圖輸出比例並)\\s*重新構圖\\s*[；;，,]*\\s*`),
      ""
    );
    value = value.replace(/^\s*維持原圖(?:輸出)?比例\s*[，,；;]*\s*/, "");
    value = value.replace(
      new RegExp(`(?:裁(?:切)?成|調整(?:為|成)|改(?:為|成)|輸出(?:為|成)?|採用|選擇|使用)\\s*(?:比例\\s*)?${RATIO_TOKEN_PATTERN}(?:\\s*(?:比例|畫幅|構圖))?`, "gi"),
      ""
    );
    value = value.replace(new RegExp(RATIO_TOKEN_PATTERN, "g"), "");
    value = value.replace(/(?:改變|更改|調整|選擇|指定)(?:輸出)?(?:長寬比|畫幅比例)|(?:長寬比|畫幅比例)(?:改變|調整)/g, "");
    return value
      .replace(/\s*([，,；;。])\s*\1+/g, "$1")
      .replace(/^[\s，,；;。:：]+|[\s，,；;]+$/g, "")
      .replace(/\s{2,}/g, " ");
  }

  function syncPreviewReframingText(analysis, ratioLabel, cropPlan) {
    const recipe = Array.isArray(analysis.retouch_recipe) ? [...analysis.retouch_recipe] : [];
    const plan = Array.isArray(analysis.correction_plan) ? [...analysis.correction_plan] : [];
    const axes = analysis.correction_axes || [];
    const expanding = Boolean(analysis.reframe_extension) && ratioLabel !== "original";
    axes.forEach((entry, index) => {
      if (!axesIncludeReframing(entry) || index >= recipe.length) return;
      const detail = reframingDetail(recipe[index])
        || reframingDetail(plan[index])
        || reframingDetail(analysis.crop_box?.reason)
        || tr("依解析後的裁切框改善主體集中度與邊緣落點。");
      if (!cropPlan && !expanding) {
        recipe[index] = "重新構圖：" + tr("此比例下維持完整原圖（沒有有效裁切）");
      } else if (ratioLabel === "original") {
        recipe[index] = "重新構圖：" + tr("維持原圖比例，{detail}", { detail });
      } else {
        recipe[index] = "重新構圖：" + tr("調整為{ratioLabel}，{detail}", { ratioLabel, detail });
      }
    });
    return { ...analysis, retouch_recipe: recipe };
  }

  // Mirror of prompts._REFRAME_STALE_KEYS -- keep the two lists in sync.
  const REFRAME_STALE_KEYS = [
    "_rejected_reframing_source", "_preserve_original_composition", "applied_crop_plan",
    "rejected_corrections", "rejected_retouch_recipe", "rejected_axes", "rejected_preserve",
    "_reframing_geometry_source", "reframe_extension", "reframe_mode_override",
    "auto_aspect_ratio", "aspect_ratio_source", "crop_box_measured_ratio"
  ];

  function restoreCompositionState(analysis, state) {
    const restored = { ...analysis };
    REFRAME_STALE_KEYS.forEach((key) => delete restored[key]);
    Object.entries(state || {}).forEach(([key, value]) => {
      if (value == null) delete restored[key];
      else restored[key] = value;
    });
    return restored;
  }

  // The result on screen takes a reframed composition too, so the ratio
  // preview below fits the new crop rather than the one the photo was made with.
  function applyCompositionToRender(state) {
    if (lastRender) lastRender = { ...lastRender, analysis: restoreCompositionState(lastRender.analysis, state) };
  }

  function previewAspectRatio(pref) {
    if (!lastRender || !pref) return;
    const resultAnalysis = lastRender.analysis;
    const recipeInputs = Array.from(
      els.apRecipeList?.querySelectorAll('input[type="checkbox"]') || []
    );
    const recipeChecked = recipeInputs.map((input) => input.checked);
    const reframeChecked = FIXED_RATIO_RE.test(pref) || Array.from(els.apRecipeList?.querySelectorAll("li") || []).some(
      (item) => REFRAME_LABEL_RE.test(item.querySelector(".step-cb-label")?.textContent || "")
        && item.querySelector('input[type="checkbox"]')?.checked
    );
    // A rejected run keeps the pre-rejection crop proposal privately. When
    // the user checks that recipe again, preview the same restored geometry
    // the backend will execute instead of previewing the rejected run's
    // deliberately disabled crop_box.
    const savedReframing = resultAnalysis?._rejected_reframing_source;
    const analysis = reframeChecked && savedReframing
      ? { ...resultAnalysis, ...savedReframing }
      : resultAnalysis;
    const label = aspectRatioForPref(analysis, pref);
    const width = els.originalImg?.naturalWidth;
    const height = els.originalImg?.naturalHeight;
    if (!width || !height) return;

    let shown = { ...analysis, recommended_aspect_ratio: label, _aspect_ratio_preview: pref };
    const cropSource = analysis?._reframing_geometry_source || analysis?.crop_box;
    if (cropSource) shown.crop_box = { ...cropSource, aspect_ratio: label };
    // The crop the edit model would be handed, recomputed for this ratio --
    // null when nothing would be cropped, same as the backend's own contract.
    const geometryRatio = label === "original" ? `${width}:${height}` : label;
    const geometryAnalysis = {
      ...shown,
      recommended_aspect_ratio: geometryRatio,
      crop_box: shown.crop_box ? { ...shown.crop_box, aspect_ratio: geometryRatio } : shown.crop_box
    };
    let cropPlan = resolveCropBoxWithReport(
      geometryAnalysis.crop_box, geometryAnalysis, width, height
    ).cropBox || null;
    if (!cropPlan && label !== "original") {
      const fallbackCrop = {
        enabled: true, x: 0, y: 0, w: 1, h: 1, aspect_ratio: geometryRatio
      };
      cropPlan = resolveCropBoxWithReport(
        fallbackCrop, { ...geometryAnalysis, crop_box: fallbackCrop }, width, height
      ).cropBox || null;
    }
    shown.applied_crop_plan = cropPlan;
    if (reframeChecked) shown = syncPreviewReframingText(shown, label, cropPlan);

    renderResult({ ...lastRender, analysis: shown }, { keepView: true });
    Array.from(els.apRecipeList?.querySelectorAll('input[type="checkbox"]') || [])
      .forEach((input, index) => {
        if (index < recipeChecked.length) input.checked = recipeChecked[index];
      });
    syncReframingRatioLock();
  }

  function aspectRatioDisplayValue(value) {
    const text = String(value || "").trim();
    return text.toLowerCase() === "original" ? tr("原圖") : text;
  }

  // One more recipe-tag pill (debug UI only): the resolved output ratio. Not
  // quite the ratio finally requested from the edit API -- each provider snaps
  // it onto its own pixel grid, and that lands in applied_aspect_ratio / the
  // overlay's "實際送出裁切" box instead. Values
  // are W:H (width first), so 16:9 is landscape and 9:16 is portrait --
  // spelled out as 橫/直/方 so it doesn't have to be read off the numbers.
  function buildAspectRatioTagLabel(analysis) {
    const value = String(analysis?.recommended_aspect_ratio || "").trim();
    if (!value) return "";
    if (value.toLowerCase() === "original") return tr("比例 原圖");
    const match = value.match(/(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)/);
    if (!match) return tr("比例 {value}", { value });
    const w = Number(match[1]);
    const h = Number(match[2]);
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return tr("比例 {value}", { value });
    return `${tr("比例")} ${value}${tr("（")}${w > h ? tr("橫") : w < h ? tr("直") : tr("方")}${tr("）")}`;
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
      li.textContent = shownRecipe(item);
      element.appendChild(li);
    });
  }

  const REFRAME_RECIPE_RE = /^重新構圖\s*[:：]/;
  const FIXED_RATIO_RE = /^\d+(?:\.\d+)?:\d+(?:\.\d+)?$/;

  // A user-specified ratio cannot be reached without reframing, so its
  // reframing item stays checked until the ratio itself is changed.
  function syncReframingRatioLock() {
    hideReframeLockTip();
    const pref = String(els.apImageAspectRatioSelect?.value || "").trim();
    const ratio = FIXED_RATIO_RE.test(pref) ? pref : "";
    els.apRecipeList?.querySelectorAll("li").forEach((li) => {
      const input = li.querySelector('input[type="checkbox"]');
      if (!input || !REFRAME_LABEL_RE.test(li.querySelector(".step-cb-label")?.textContent || "")) return;
      li.classList.toggle("is-ratio-locked", Boolean(ratio));
      if (ratio) {
        input.checked = true;
        input.disabled = true;
        input.dataset.ratioLocked = "1";
        li.dataset.lockTip = tr("指定比例{ratio}需重新構圖，若需取消請把長寬比改成「原圖比例」", { ratio });
      } else if (input.dataset.ratioLocked) {
        delete input.dataset.ratioLocked;
        delete li.dataset.lockTip;
        input.disabled = isReadOnlyLocked();
      }
    });
  }

  // Lives on <body> so the recipe list's own scroll clipping cannot cut it off.
  let reframeLockTip = null;
  let reframeLockTipTimer = 0;

  function hideReframeLockTip() {
    clearTimeout(reframeLockTipTimer);
    if (reframeLockTip) reframeLockTip.hidden = true;
  }

  function showReframeLockTip(li, { autoHide = false } = {}) {
    const anchor = li.querySelector(".step-cb");
    if (!anchor || !li.dataset.lockTip) return;
    if (!reframeLockTip) {
      reframeLockTip = document.createElement("div");
      reframeLockTip.className = "reframe-lock-tip";
      reframeLockTip.setAttribute("role", "tooltip");
      document.body.appendChild(reframeLockTip);
    }
    reframeLockTip.textContent = li.dataset.lockTip;
    reframeLockTip.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = reframeLockTip;
    const below = rect.bottom + 8;
    reframeLockTip.style.left = `${Math.max(8, Math.min(rect.left - 6, window.innerWidth - width - 8))}px`;
    reframeLockTip.style.top = `${below + height > window.innerHeight - 8 ? rect.top - height - 8 : below}px`;
    clearTimeout(reframeLockTipTimer);
    if (autoHide) reframeLockTipTimer = setTimeout(hideReframeLockTip, 3000);
  }

  // Mouse hover shows the tip on desktop; a tap shows it on touch screens.
  if (els.apRecipeList) {
    const lockedAnchor = (target) => target?.closest?.("li.is-ratio-locked .step-cb");
    els.apRecipeList.addEventListener("pointerover", (event) => {
      if (event.pointerType === "mouse" && lockedAnchor(event.target)) {
        showReframeLockTip(event.target.closest("li"));
      }
    });
    els.apRecipeList.addEventListener("pointerout", (event) => {
      if (event.pointerType === "mouse" && lockedAnchor(event.target)
        && !lockedAnchor(event.relatedTarget)) hideReframeLockTip();
    });
    els.apRecipeList.addEventListener("click", (event) => {
      const li = event.target.closest?.("li.is-ratio-locked");
      if (li) showReframeLockTip(li, { autoHide: true });
    });
    els.apRecipeList.addEventListener("scroll", hideReframeLockTip, { passive: true });
    window.addEventListener("scroll", hideReframeLockTip, { passive: true, capture: true });
  }

  function fillCheckList(element, items, fallbackItems) {
    if (!element) return;
    element.innerHTML = "";
    const source = Array.isArray(items) && items.length ? items : fallbackItems;
    // Display order only: a reframing item always leads the list so its
    // combined ratio/crop result is the first thing reviewed, but every
    // checkbox still carries its *original* array position in
    // data-recipe-index. app.js's recipeChecked read/restore and
    // applyRejectedReframingRatio key off that attribute rather than DOM
    // order, since correction_plan/correction_axes/correction_inverse stay
    // aligned to retouch_recipe's original (unsorted) index.
    const order = source.map((_, index) => index);
    order.sort((a, b) => {
      const aReframe = REFRAME_RECIPE_RE.test(String(source[a] || "")) ? 0 : 1;
      const bReframe = REFRAME_RECIPE_RE.test(String(source[b] || "")) ? 0 : 1;
      return aReframe !== bReframe ? aReframe - bReframe : a - b;
    });
    order.forEach((index) => {
      const item = source[index];
      const li = document.createElement("li");
      const label = document.createElement("label");

      const cb = document.createElement("span");
      cb.className = "step-cb";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.id = `${element.id}-${index}`;
      input.dataset.recipeIndex = String(index);
      // The item's own text: switching to the chat page carries checks over
      // by text, since its list is ordered differently (app.js writeModeHandoff).
      input.dataset.recipeText = String(item || "");
      input.checked = true;
      input.disabled = isReadOnlyLocked();
      const box = document.createElement("span");
      box.className = "step-cb-box";
      cb.appendChild(input);
      cb.appendChild(box);

      const text = document.createElement("span");
      text.className = "step-cb-label";
      const reframe = String(item || "").match(/^(重新構圖\s*[:：])\s*(.+)$/);
      if (reframe) {
        // Keep the framing decision visually recognizable as one recipe row:
        // the label stays plain and the combined ratio/crop result is bold.
        text.appendChild(document.createTextNode(`${tr("重新構圖：")} `));
        const detail = document.createElement("strong");
        detail.textContent = reframe[2];
        text.appendChild(detail);
      } else {
        text.textContent = item;
      }

      label.appendChild(cb);
      label.appendChild(text);
      li.appendChild(label);
      element.appendChild(li);
    });
    if (element === els.apRecipeList) syncReframingRatioLock();
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
          label: tr("不應出現：{item}", { item: it.item }),
          checked: it.violated !== true,
          note: it.note
        }))
      : [];

    if (!followedEntries.length && !styleEntries.length && !rejectedEntries.length) {
      const li = document.createElement("li");
      li.textContent = tr("尚未取得修圖項目。");
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
      text.textContent = entry.note ? tr("{label}（{note}）", { label: entry.label, note: entry.note }) : entry.label;

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

  // The downscaled webp copies, not the ~2 MB source PNGs beside them -- all
  // 18 are fetched on every render, for tiles at most 140px wide.
  function styleThumbSrc(group, imageFile) {
    return `assets/optional_style/${group}/thumbs/${imageFile.replace(/\.png$/, ".webp")}`;
  }

  // Renders a grid of image-thumbnail buttons -- used only by the per-image
  // 修圖風格 tab's Pose/Angle/Lighting/Size lists (assets live in
  // assets/optional_style/<group>/, see STYLE_OPTION_IMAGES above).
  function fillStyleOptionList(element, options, name, defaultValue, group) {
    if (!element) return;
    element.innerHTML = "";
    const images = STYLE_OPTION_IMAGES[group] || {};
    options.forEach((opt, index) => {
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = opt.value;
      input.id = `${name}-${index}`;
      input.checked = opt.value === defaultValue;
      input.className = "ap-style-option-input";

      // "Auto"/default no longer gets a card of its own in the grid -- it
      // has no example image, and now that clearStylePick (app.js) resets to
      // it from the picked-chip/pill "x"s instead, a dedicated card is just
      // dead weight sitting alongside the real style choices. The radio
      // itself still needs to exist in the DOM (hidden, in its own <li> so
      // markup stays valid) so it stays a normal, always-present member of
      // the name="ap-pose" etc radio group -- clearStylePick and the rest of
      // the selection-reading code (readStyleSelectionsFromDom,
      // syncStylePicks) all depend on it being there to check/query.
      if (opt.value === "default") {
        const hiddenLi = document.createElement("li");
        hiddenLi.hidden = true;
        hiddenLi.appendChild(input);
        element.appendChild(hiddenLi);
        return;
      }

      const li = document.createElement("li");
      const label = document.createElement("label");
      label.className = "ap-style-option-btn";

      const imageFile = images[opt.value];
      let thumb;
      if (imageFile) {
        thumb = document.createElement("img");
        thumb.className = "ap-style-option-thumb";
        thumb.src = styleThumbSrc(group, imageFile);
        thumb.alt = opt.label;
      } else {
        thumb = document.createElement("span");
        thumb.className = "ap-style-option-thumb ap-style-option-thumb-default";
        thumb.textContent = opt.label;
      }

      const text = document.createElement("span");
      text.className = "ap-style-option-label";
      text.textContent = opt.label;

      label.appendChild(input);
      label.appendChild(thumb);
      label.appendChild(text);

      // A small "x" overlaid on whichever card is currently selected (CSS
      // shows it via :has(.ap-style-option-input:checked), so it only ever
      // appears on the checked one), letting the user clear straight from
      // the card they're already looking at instead of confirming first and
      // only then reaching for the pill's clear "x" above the grid. A
      // <button> nested in a <label> is valid HTML (unlike button-in-button
      // elsewhere in this modal), but its click still bubbles to the
      // label's own "activate my radio" behavior, hence the
      // preventDefault/stopPropagation below.
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "ap-style-option-cancel";
      cancelBtn.setAttribute("aria-label", tr("清除{label}", { label: opt.label }));
      cancelBtn.textContent = "×";
      cancelBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        clearStylePick(name);
      });
      label.appendChild(cancelBtn);

      li.appendChild(label);
      element.appendChild(li);
    });
  }

  // Checks one ap-pose/-angle/-lighting/-size radio and fires a real `change`
  // event on it -- app.js's listener for those is delegated onto the enclosing
  // #ap-*-list <ul>, so this one dispatch runs the same syncStylePicks/
  // applyPoseSuggestions path a click on the option's own modal card would.
  function selectStylePick(name, value) {
    const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (!input || input.checked) return;
    input.checked = true;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function clearStylePick(name) {
    selectStylePick(name, "default");
  }

  // While a 修圖風格 card has no pick yet, its front thumbnail cycles
  // through every example image for that category (pose has 8, angle/
  // lighting/size fewer) like a looping slideshow, instead of sitting on
  // the same one or two examples forever -- the back photo always shows
  // whichever example comes up *next*, so the "stack of photos" look still
  // reads as one card away from flipping over rather than as a frozen
  // pair. The step itself is a 3D swing rather than a src swap (see
  // advanceStyleCarousel below and the .is-advancing rules in index.html's
  // <style>) -- swapping a visible <img>'s src outright made the card blink
  // on every tick, because the browser has nothing to show for the frames
  // it spends decoding the new photo.
  const STYLE_CAROUSEL_INTERVAL_MS = 2600;
  // How long the swing runs before the step commits. This lives in the CSS
  // (.ap-style-card-* transitions, index.html's <style>) and is read back
  // from there rather than kept as a second copy here -- a duplicated
  // number silently goes stale the moment anyone retunes the CSS, and the
  // commit would then land mid-animation and jump. The constant is only the
  // fallback for when the duration can't be read.
  const STYLE_CAROUSEL_STEP_FALLBACK_MS = 620;
  // At least three example images, because the step needs three layers to
  // move at once (leaving, arriving at the front, entering the back slot).
  // A category with fewer just shows a static card, as it did before the
  // carousel existed.
  const STYLE_CAROUSEL_MIN_IMAGES = 3;
  const styleCarouselIndex = {};
  const styleCarouselBusy = {};
  // Every unpicked card advances off ONE shared ticker rather than a timer
  // of its own. With a timer each, a card that got picked and then cleared
  // again restarted counting from that moment and turned over permanently
  // out of step with the cards that never stopped -- joining and leaving
  // this map instead leaves the phase shared, so all four turn over
  // together no matter what was picked or cleared along the way. (A
  // left-to-right stagger was tried here and taken back out: the row reads
  // better flipping as one.)
  const styleCarouselRunning = new Map();
  let styleCarouselTicker = null;
  // Bumped by stopStyleCarousel so an advance that's mid-await (a pick made
  // while the photo is still swinging away, a re-render, another record
  // loading) bails at its next checkpoint instead of writing its own srcs
  // over whatever replaced it.
  const styleCarouselGen = {};

  function styleCardParts(group) {
    return {
      front: document.querySelector(`.ap-style-card-front[data-style-group="${group}"]`),
      back: document.querySelector(`.ap-style-card-back[data-style-group="${group}"]`),
      deep: document.querySelector(`.ap-style-card-deep[data-style-group="${group}"]`),
      stack: document.querySelector(`.ap-style-card[data-style-group="${group}"] .ap-style-card-stack`)
    };
  }

  function resetStyleCarouselClasses(group) {
    const { stack } = styleCardParts(group);
    if (stack) stack.classList.remove("is-advancing", "no-anim");
  }

  function stopStyleCarousel(group) {
    styleCarouselGen[group] = (styleCarouselGen[group] || 0) + 1;
    styleCarouselRunning.delete(group);
    // The ticker only exists while at least one card is riding it -- with
    // every category picked there is nothing left to advance.
    if (!styleCarouselRunning.size && styleCarouselTicker) {
      clearInterval(styleCarouselTicker);
      styleCarouselTicker = null;
    }
    resetStyleCarouselClasses(group);
  }

  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function setStyleThumb(img, src) {
    if (!img) return;
    img.loading = "lazy";
    img.decoding = "async";
    img.src = src;
  }

  // Fetch AND decode off-DOM, so the later `img.src = src` paints straight
  // from cache in the same frame instead of leaving the <img> blank while
  // the decode runs.
  function preloadStyleThumb(src) {
    return new Promise((resolve) => {
      const img = new Image();
      const done = () => resolve();
      img.onload = () => { (img.decode ? img.decode() : Promise.resolve()).then(done, done); };
      img.onerror = done;
      img.src = src;
    });
  }

  // A load/decode that never settles (a stalled fetch, the tab hidden
  // mid-step) would otherwise leave styleCarouselBusy stuck true and freeze
  // that card's carousel for good -- cap every wait in a step instead.
  function withTimeout(promise, ms) {
    return Promise.race([promise, new Promise((resolve) => setTimeout(resolve, ms))]);
  }

  function styleCarouselSrc(group, withImage, images, idx) {
    return styleThumbSrc(group, images[withImage[idx % withImage.length].value]);
  }

  // Longest transition on the layer, in ms, straight from the stylesheet.
  // getComputedStyle reports e.g. "0.62s, 0.62s" (or "620ms"), one entry per
  // transitioned property; the swing is over once the slowest one is.
  function styleCarouselStepMs(front) {
    const raw = front ? getComputedStyle(front).transitionDuration : "";
    const longest = String(raw).split(",").reduce((max, part) => {
      const value = parseFloat(part);
      if (!Number.isFinite(value)) return max;
      return Math.max(max, part.trim().endsWith("ms") ? value : value * 1000);
    }, 0);
    return longest > 0 ? longest : STYLE_CAROUSEL_STEP_FALLBACK_MS;
  }

  // Paints a frame outright, no animation -- the carousel's starting state.
  // (Every later frame arrives through advanceStyleCarousel's own commit;
  // a card that is off-screen or under reduced motion simply holds this one
  // rather than being repainted.)
  function renderStyleCarouselFrame(group, withImage, images) {
    const { front, back, deep } = styleCardParts(group);
    const len = withImage.length;
    const idx = (styleCarouselIndex[group] || 0) % len;
    if (front) {
      front.hidden = false;
      setStyleThumb(front, styleCarouselSrc(group, withImage, images, idx));
    }
    if (back) {
      back.hidden = len < 2;
      if (len >= 2) setStyleThumb(back, styleCarouselSrc(group, withImage, images, idx + 1));
    }
    // Transparent at rest, so this only preloads what the next step will
    // bring in -- it never adds a third visible photo to the card.
    if (deep) {
      deep.hidden = len < 3;
      if (len >= 3) setStyleThumb(deep, styleCarouselSrc(group, withImage, images, idx + 2));
    }
  }

  // One carousel step, all three layers moving at once: the top photo swings
  // away in 3D, the tilted one behind it rises into its place, and the deep
  // layer -- transparent at rest, holding the photo after that -- takes over
  // the tilt. All three srcs then move one forward under .no-anim while the
  // front photo is still faded out and every image involved is decoded, so
  // the snap back to rest paints exactly like the last animated frame.
  // Nothing appears afterwards: what the deep layer brought in IS the new
  // back photo, already in place, and the deep layer goes transparent again
  // holding the next one.
  async function advanceStyleCarousel(group, withImage, images) {
    // stopStyleCarousel drops a picked category from the map synchronously,
    // so this covers any caller reaching a card that is no longer meant to
    // be moving; the generation checks further down only catch a stop that
    // lands once this step is already under way.
    if (!styleCarouselRunning.has(group)) return;
    if (styleCarouselBusy[group]) return;
    const { front, back, deep, stack } = styleCardParts(group);
    const len = withImage.length;
    if (!front || !back || !deep || !stack || len < STYLE_CAROUSEL_MIN_IMAGES) return;

    const idx = (styleCarouselIndex[group] || 0) % len;
    const nextIdx = (idx + 1) % len;
    // What the deep layer will hold once this step is done -- one past the
    // photo it is carrying into the back slot right now.
    const afterSrc = styleCarouselSrc(group, withImage, images, idx + 3);

    // Hold the card exactly where it is, leaving the index alone so it stays
    // in step with what is on screen and simply carries on from the same
    // photo later. Two reasons to do that: nobody can see the card
    // (background tab, review panel closed, another section showing), so
    // cycling photos -- and decoding their replacements -- would be work
    // done into a void; or the viewer asked for reduced motion, and a photo
    // swapping itself every few seconds unprompted is exactly the kind of
    // movement that setting exists to stop, with or without a transition on
    // it. Either way the card stays a perfectly good static example.
    if (document.hidden || !stack.offsetParent || prefersReducedMotion()) return;

    const gen = styleCarouselGen[group] || 0;
    const stale = () => (styleCarouselGen[group] || 0) !== gen;
    styleCarouselBusy[group] = true;
    try {
      await withTimeout(preloadStyleThumb(afterSrc), 1500);
      if (stale()) return;

      // Both of these are on screen for the whole step, so they are decoded
      // by the time the commit below hands them to the layer in front.
      const risenSrc = back.src;
      const arrivedSrc = deep ? deep.src : null;
      stack.classList.add("is-advancing");
      await new Promise((resolve) => setTimeout(resolve, styleCarouselStepMs(front)));
      if (stale()) { resetStyleCarouselClasses(group); return; }

      // Last chance to wait for anything: the commit below must not contain
      // a single await. While .is-advancing is still on, the back layer is
      // sitting at the FRONT position at full opacity -- so a paint landing
      // between "back.src = the next back photo" and the class swap shows
      // that photo full size up front for a frame or two, which is the
      // occasional flash this ordering exists to prevent. (risenSrc is
      // already on screen, so this resolves off cache.)
      await withTimeout(preloadStyleThumb(risenSrc), 400);
      if (stale()) { resetStyleCarouselClasses(group); return; }

      // One synchronous block: every src and class moves together, so the
      // browser can only ever paint the before or the after, never a mix.
      stack.classList.add("no-anim");
      front.src = risenSrc;
      if (arrivedSrc) back.src = arrivedSrc;
      // Back to transparent, now holding the photo the NEXT step brings in.
      if (deep) deep.src = afterSrc;
      stack.classList.remove("is-advancing");
      void stack.offsetWidth; // flush the snap-back before transitions come back on
      stack.classList.remove("no-anim");
      styleCarouselIndex[group] = nextIdx;
    } finally {
      styleCarouselBusy[group] = false;
    }
  }

  // A card joining mid-cycle picks up whatever phase the ticker is already
  // on (so its first turn may come in well under a full interval) -- that's
  // the point: from then on it turns over with the others.
  function startStyleCarousel(group, withImage, images) {
    if (withImage.length < STYLE_CAROUSEL_MIN_IMAGES) return;
    styleCarouselRunning.set(group, { withImage, images });
    if (!styleCarouselTicker) {
      styleCarouselTicker = setInterval(() => {
        styleCarouselRunning.forEach((entry, runningGroup) => {
          advanceStyleCarousel(runningGroup, entry.withImage, entry.images);
        });
      }, STYLE_CAROUSEL_INTERVAL_MS);
    }
  }

  // Puts each 修圖風格 card into its category's current state: the picked
  // option's own thumbnail and label (carousel stopped, pinned to that one
  // choice), or -- while nothing is picked there -- the looping example
  // carousel above. Called whenever those radios change (app.js) or the
  // analysis preview re-renders.
  function syncStylePicks() {
    Object.entries(STYLE_OPTION_GROUPS).forEach(([group, { options, label }]) => {
      const checked = document.querySelector(`input[name="ap-${group}"]:checked`);
      const opt = checked && checked.value !== "default"
        ? options.find((o) => o.value === checked.value)
        : null;
      const images = STYLE_OPTION_IMAGES[group] || {};
      const withImage = options.filter((o) => images[o.value]);

      const card = document.querySelector(`.ap-style-card[data-style-group="${group}"]`);
      if (card) card.classList.toggle("is-picked", !!opt);
      const valueEl = document.querySelector(`.ap-style-card-value[data-style-group="${group}"]`);
      if (valueEl) valueEl.textContent = opt ? opt.label : tr("選擇{label}", { label });
      const clearBtn = document.querySelector(`.ap-style-card-clear[data-style-group="${group}"]`);
      if (clearBtn) clearBtn.hidden = !opt;

      if (opt) {
        // A choice is made -- stop the carousel and pin the card to exactly
        // that option, back photo hidden (unchanged from the original
        // picked-state behavior).
        stopStyleCarousel(group);
        const { front: frontImg, back: backImg, deep: deepImg } = styleCardParts(group);
        // An option with no example image of its own falls back to the
        // category's first one, and to showing nothing at all if the
        // category has no images -- styleThumbSrc calls .replace on what it
        // is handed, so passing undefined throws. The pre-carousel code had
        // this fallback (`(opt && images[opt.value]) ? opt : withImage[0]`);
        // it was lost in the rewrite.
        const pickedFile = images[opt.value]
          || (withImage[0] ? images[withImage[0].value] : null);
        if (frontImg) {
          frontImg.hidden = !pickedFile;
          if (pickedFile) setStyleThumb(frontImg, styleThumbSrc(group, pickedFile));
        }
        if (backImg) backImg.hidden = true;
        if (deepImg) deepImg.hidden = true;
        return;
      }

      if (!withImage.length) return;
      // Don't restart an already-running carousel just because this ran
      // again (a re-render, or a change on a DIFFERENT category) -- that
      // would reset it back to frame 0 and cut the loop short every time.
      if (!styleCarouselRunning.has(group)) {
        styleCarouselIndex[group] = styleCarouselIndex[group] || 0;
        renderStyleCarouselFrame(group, withImage, images);
        startStyleCarousel(group, withImage, images);
      }
    });
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
    return text ? tr("{label}：{text}。", { label, text }) : "";
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
    if (!qa) return tr("未檢查");
    if (qa.pass === false) return qa.overall_status || tr("需人工複查");
    return qa.overall_status || tr("可展示");
  }

  // A tag is normally just its label. The object form -- { label, onClick,
  // className, title, controls } -- is for a pill that is also a control (the
  // "個人化" pill in #recipe-tags, below): still a <span> so every
  // .recipe-tags span rule and fitRecipeTagsRow's shrink loop apply to it
  // unchanged, made operable via role/tabindex rather than by swapping in a
  // <button> those rules miss. `title` is the hover tooltip that spells out
  // what the pill does for anyone who reads the row before clicking it;
  // `controls` names the panel it toggles, which also gets the pill an
  // aria-expanded a screen reader can announce -- the visual cues that it is
  // pressable (icon, solid border) say nothing to one.
  function renderTags(element, tags) {
    element.innerHTML = "";
    tags.forEach((tag) => {
      const spec = typeof tag === "string" ? { label: tag } : (tag || {});
      const label = typeof spec.label === "string" ? spec.label.trim() : "";
      if (!label) return;
      const chip = document.createElement("span");
      // subject_category / scene_category are fixed Chinese values: shown translated.
      chip.textContent = tr(label);
      if (spec.className) chip.className = spec.className;
      if (spec.title) chip.title = spec.title;
      if (spec.onClick) {
        chip.setAttribute("role", "button");
        chip.setAttribute("tabindex", "0");
        if (spec.controls) {
          chip.setAttribute("aria-controls", spec.controls);
          chip.setAttribute("aria-expanded", "false");
        }
        chip.addEventListener("click", spec.onClick);
        chip.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          spec.onClick(event);
        });
      }
      element.appendChild(chip);
    });
  }

  // The 個人化需求 this run was actually submitted with. pipeline.py keeps the
  // user's own wording as user_instruction.original_user_direction at the edit
  // boundary; records made before that field existed only carry the reviewed
  // (normalized) form, so fall back to it rather than dropping the pill for
  // runs that genuinely had a request.
  function getPersonalizationText(analysis) {
    const instruction = (analysis && analysis.user_instruction) || {};
    const text = instruction.original_user_direction || instruction.normalized_user_direction || "";
    return typeof text === "string" ? text.trim() : "";
  }

  // #personalize-popover: the pill has no room for a whole sentence, so the
  // request itself lives in a translucent panel floating over the photo.
  // Dismissed by its X, by a click anywhere outside it, or by Escape -- that
  // last one via app.js's single Escape chain (which also closes the
  // fullscreen compare view), so a second document-level keydown listener
  // here doesn't dismiss both at once. The pill is excluded from the outside
  // check because mousedown lands before the pill's own click, which would
  // otherwise close and immediately reopen the panel.
  function handlePersonalizePopoverOutsideClick(event) {
    if (els.personalizePopover && els.personalizePopover.contains(event.target)) return;
    if (event.target.closest && event.target.closest(".recipe-tag-personalize")) return;
    closePersonalizePopover();
  }

  // The panel is drawn as a speech bubble whose tail (styles.css:
  // .personalize-popover::after) should point at the pill that opened it --
  // but the pill's position moves with every re-render (the row is centred
  // and its widths depend on how many style tags there are) and with every
  // resize of #recipe-tags, so the tail's x is measured here and handed to
  // CSS as --personalize-tail-x. Kept as an offset from the panel's own left
  // edge, and clamped away from both ends so the tail can never hang off a
  // rounded corner with nothing behind its root.
  const PERSONALIZE_TAIL_EDGE_INSET = 18;
  let personalizeAnchor = null;

  function positionPersonalizeTail() {
    const panel = els.personalizePopover;
    if (!panel || panel.hidden || !personalizeAnchor || !personalizeAnchor.isConnected) return;
    const panelRect = panel.getBoundingClientRect();
    if (!panelRect.width) return;
    const anchorRect = personalizeAnchor.getBoundingClientRect();
    const anchorCenter = anchorRect.left + anchorRect.width / 2 - panelRect.left;
    const min = PERSONALIZE_TAIL_EDGE_INSET;
    const max = Math.max(min, panelRect.width - PERSONALIZE_TAIL_EDGE_INSET);
    panel.style.setProperty("--personalize-tail-x", `${Math.min(Math.max(anchorCenter, min), max)}px`);
  }

  function openPersonalizePopover(text, anchor, title = tr("個人化需求")) {
    if (!els.personalizePopover) return;
    closePersonalizePopover();
    els.personalizePopover.setAttribute("aria-label", title);
    const heading = els.personalizePopover.querySelector(".personalize-popover-title");
    if (heading) heading.textContent = title;
    if (els.personalizePopoverText) {
      els.personalizePopoverText.replaceChildren();
      if (text instanceof Node) {
        els.personalizePopoverText.appendChild(text);
      } else if (Array.isArray(text)) {
        const list = document.createElement("ul");
        list.className = "result-detail-list";
        text.forEach((value) => {
          const item = document.createElement("li");
          item.textContent = value;
          list.appendChild(item);
        });
        els.personalizePopoverText.appendChild(list);
      } else els.personalizePopoverText.textContent = text;
    }
    personalizeAnchor = anchor || null;
    if (personalizeAnchor) personalizeAnchor.setAttribute("aria-expanded", "true");
    els.personalizePopover.hidden = false;
    // After unhiding, so the panel actually has a box to measure against.
    positionPersonalizeTail();
    document.addEventListener("mousedown", handlePersonalizePopoverOutsideClick);
  }

  function closePersonalizePopover() {
    if (!els.personalizePopover || els.personalizePopover.hidden) return;
    els.personalizePopover.hidden = true;
    if (personalizeAnchor) personalizeAnchor.setAttribute("aria-expanded", "false");
    personalizeAnchor = null;
    // Drop the measured tail x with it: the next open belongs to a different
    // row (or a different record), and a stale inline value would win over
    // the CSS fallback if that open can't measure its own pill.
    els.personalizePopover.style.removeProperty("--personalize-tail-x");
    document.removeEventListener("mousedown", handlePersonalizePopoverOutsideClick);
  }

  if (els.personalizePopoverClose) {
    els.personalizePopoverClose.addEventListener("click", closePersonalizePopover);
  }

  // Prefer readable labels; collapse controls to their tab icons when the
  // actual result rail is too narrow (including embedded/fullscreen changes).
  const RECIPE_TAG_BASE_FONT_SIZE = 12;
  function fitRecipeTagsRow(container) {
    if (!container) return;
    const chips = Array.from(container.children).filter((chip) => !chip.hidden);
    if (!chips.length) return;
    container.classList.remove("is-icon-only");
    chips.forEach((chip) => { chip.style.fontSize = `${RECIPE_TAG_BASE_FONT_SIZE}px`; });
    // Measure intrinsic button widths; flex shrinking would conceal overflow.
    const required = chips.reduce((sum, chip) => sum + chip.scrollWidth, 0)
      + Math.max(0, chips.length - 1) * 8;
    container.classList.toggle("is-icon-only", required > container.clientWidth);

  }

  // Re-fit whenever #recipe-tags itself changes width -- window resize,
  // rotating a phone, or #compare-fullscreen-btn's toggle (app.js) all change
  // how much room the row actually has, independent of any fresh render.
  if (els.recipeTags && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      fitRecipeTagsRow(els.recipeTags);
      // Same trigger list moves the 個人化 pill under an open bubble, so the
      // tail has to follow it -- after the re-fit, whose font-size changes
      // are themselves part of where the pill ends up.
      positionPersonalizeTail();
    }).observe(els.recipeTags);
  }

  function buildPhotoContentSummary(analysis) {
    const summary = stripFieldLabel(analysis.photo_summary, [tr("照片內容"), tr("照片摘要"), tr("內容")]);
    const subject = stripFieldLabel(analysis.main_subject, [tr("主體"), tr("主要主體")]);
    const scene = stripFieldLabel(analysis.scene_type, [tr("場景"), tr("場景類型")]);
    if (summary) {
      return joinParts([
        tr("{summary}。", { summary }),
        subject && !summary.includes(subject) ? tr("主體：{subject}。", { subject }) : "",
        scene && !summary.includes(scene) ? tr("場景：{scene}。", { scene }) : ""
      ]);
    }
    return joinParts([
      subject ? tr("主體：{subject}。", { subject }) : tr("已完成照片總覽辨識。"),
      scene ? tr("場景：{scene}。", { scene }) : ""
    ]);
  }

  // detected_issues is a plain string array with no category/severity/etc.
  // -- see getValidIssues's callers for why this stays a flat trim+filter
  // instead of any keyword-based grouping. Non-string entries (a stale/
  // malformed analysis result) are dropped rather than displayed raw.
  function getValidIssues(analysis) {
    const raw = Array.isArray(analysis?.detected_issues) ? analysis.detected_issues : [];
    return raw
      .filter((item) => typeof item === "string")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  let currentValidIssues = [];
  const ISSUES_DEFAULT_VISIBLE = 3;

  // Renders #ap-photo-issues-list from the first three valid issues and
  // keeps #ap-photo-summary-badge's "N 項值得調整" count in sync with the
  // rows currently shown.
  function renderPhotoIssues() {
    if (!els.apPhotoIssuesList) return;
    const total = currentValidIssues.length;
    const visible = currentValidIssues.slice(0, ISSUES_DEFAULT_VISIBLE);
    if (els.apPhotoSummaryBadge) {
      els.apPhotoSummaryBadge.hidden = visible.length === 0;
      els.apPhotoSummaryBadge.textContent = visible.length
        ? tr("{length} 項值得調整", { length: visible.length })
        : "";
    }
    if (els.apPhotoIssues) els.apPhotoIssues.hidden = total === 0;
    els.apPhotoIssuesList.innerHTML = "";
    if (total === 0) {
      return;
    }
    visible.forEach((text, index) => {
      const li = document.createElement("li");
      li.className = "ap-photo-issue-row";
      const num = document.createElement("span");
      num.className = "ap-photo-issue-num";
      num.textContent = String(index + 1).padStart(2, "0");
      const body = document.createElement("span");
      body.className = "ap-photo-issue-text";
      body.textContent = text;
      li.appendChild(num);
      li.appendChild(body);
      els.apPhotoIssuesList.appendChild(li);
    });
  }

  // 照片亮點: composition_mood/main_subject only, one row each, no fallback
  // text and no other field ever feeds this -- see the spec this implements
  // for why (no aesthetic_diagnosis excerpting, no rewriting).
  function renderPhotoHighlights(analysis) {
    const mood = typeof analysis?.composition_mood === "string" ? analysis.composition_mood.trim() : "";
    const subject = typeof analysis?.main_subject === "string" ? analysis.main_subject.trim() : "";
    if (els.apHighlightMood) els.apHighlightMood.hidden = !mood;
    if (els.apHighlightMoodValue) els.apHighlightMoodValue.textContent = mood;
    if (els.apHighlightSubject) els.apHighlightSubject.hidden = !subject;
    if (els.apHighlightSubjectValue) els.apHighlightSubjectValue.textContent = subject;
    if (els.apPhotoHighlights) els.apPhotoHighlights.hidden = !mood && !subject;
  }

  const PHOTO_SUMMARY_COMPACT_HEIGHT = 280;
  const PHOTO_SUMMARY_MOBILE_RATIO = 1 / 3;
  let photoSummaryExpanded = false;

  // In the desktop side-panel layout, reserve at least 60% of the shared
  // results-canvas-row height for the analysis shell. The overview gets the
  // remaining space, minus the action row and a small spacing allowance.
  // This is based on the real panel height rather than a viewport-height
  // breakpoint, so browser chrome and the step indicator are accounted for.
  function syncPhotoSummaryCompactState() {
    const summary = els.apPhotoSummary;
    const toggle = els.apPhotoSummaryToggle;
    if (!summary || !toggle) return;
    const isMobile = typeof window.matchMedia === "function"
      && window.matchMedia("(max-width: 900px)").matches;
    const isDesktopSidePanel = !isMobile && (
      document.body.classList.contains("side-panel-layout")
      || !!els.resultsAnalysisSlot?.contains(els.apOptionsCardInline)
    );
    const shouldCompactSummary = isMobile || isDesktopSidePanel;

    summary.style.removeProperty("--ap-photo-summary-compact-height");
    summary.classList.remove("ap-photo-summary--compact", "ap-photo-summary--expanded");
    if (!shouldCompactSummary) {
      photoSummaryExpanded = false;
      toggle.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      return;
    }

    const naturalHeight = summary.scrollHeight;
    let compactHeight = PHOTO_SUMMARY_COMPACT_HEIGHT;
    if (isMobile) {
      compactHeight = Math.ceil(naturalHeight * PHOTO_SUMMARY_MOBILE_RATIO);
    } else {
      const panelHeight = els.resultsAnalysisSlot?.clientHeight
        || els.apOptionsCardInline?.clientHeight
        || 0;
      if (!panelHeight) {
        toggle.hidden = true;
        return;
      }
      const actionHeight = els.apOptionsCardInline?.querySelector(".ap-actions-row")?.offsetHeight || 0;
      const analysisMinHeight = Math.max(260, Math.ceil(panelHeight * 0.6));
      const availableHeight = panelHeight - analysisMinHeight - actionHeight - 18;
      // The column's flex-shrink can leave the card shorter than this estimate; its laid-out height is the real limit.
      const renderedHeight = Math.floor(summary.getBoundingClientRect().height);
      compactHeight = Math.max(110, Math.min(PHOTO_SUMMARY_COMPACT_HEIGHT, availableHeight, renderedHeight));
    }

    const needsCompactToggle = naturalHeight > compactHeight;
    if (!needsCompactToggle) {
      photoSummaryExpanded = false;
      toggle.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      return;
    }

    summary.style.setProperty("--ap-photo-summary-compact-height", `${compactHeight}px`);
    toggle.hidden = false;
    // 展開/收合, not 查看更多/收合: #page-scroll-hint (index.html) owns
    // "查看更多" on these same screens, where it means "scroll the page",
    // not "open this block in place". Two centered pills a screen apart
    // reading the same words for different actions is the whole reason this
    // one moved -- 展開 names what actually happens here, and pairs
    // symmetrically with 收合 instead of leaving one half of the toggle
    // phrased as a destination and the other as a verb. The pill's mark moved
    // off the chevron for the same split (see .ap-photo-summary-toggle).
    toggle.textContent = photoSummaryExpanded ? tr("收合") : tr("展開");
    toggle.setAttribute("aria-expanded", String(photoSummaryExpanded));
    if (photoSummaryExpanded) {
      summary.classList.add("ap-photo-summary--expanded");
    } else {
      summary.classList.add("ap-photo-summary--compact");
    }
  }

  if (els.apPhotoSummaryToggle) {
    els.apPhotoSummaryToggle.addEventListener("click", () => {
      photoSummaryExpanded = !photoSummaryExpanded;
      syncPhotoSummaryCompactState();
      if (!photoSummaryExpanded && els.apOptionsCardInline) {
        els.apOptionsCardInline.scrollTop = 0;
      }
    });
  }
  if (els.apPhotoSummary && typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => syncPhotoSummaryCompactState());
    observer.observe(els.apPhotoSummary);
    if (els.apOptionsCardInline) observer.observe(els.apOptionsCardInline);
    if (els.resultsAnalysisSlot) observer.observe(els.resultsAnalysisSlot);
  }
  window.addEventListener("resize", syncPhotoSummaryCompactState);

  function formatCandidate(candidate, validation) {
    const selected = validation?.selected_candidate_id === candidate.id;
    const score = (validation?.candidate_scores || []).find((item) => item.id === candidate.id);
    const label = candidate.label || candidate.label_en || candidate.id;
    const verdict = selected ? tr("最終選擇") : score ? tr("候選評分 {score}/10", { score: score.score }) : tr("候選方案");
    const comment = score?.comment || candidate.compositionIntent || candidate.tradeoffs || "";
    return `${verdict} - ${label}${comment ? `${tr("：")}${comment}` : ""}`;
  }

  function checksToList(qa) {
    if (!qa?.checks) return [];
    const labels = {
      subject_preserved: tr("主體與身分保留"),
      user_intent_followed: tr("使用者目標達成"),
      crop_safe: tr("裁切安全"),
      protected_regions_safe: tr("保護區域安全"),
      framing_intent_respected: tr("取景意圖一致"),
      aspect_ratio_feels_intentional: tr("比例有構圖意義"),
      composition_method_matches: tr("構圖方法吻合"),
      empty_space_has_function: tr("留白有功能"),
      alignment_stable_or_intentional: tr("水平/透視穩定"),
      distractions_reassessed_after_crop: tr("裁切後重新評估干擾"),
      scene_preserved: tr("場景真實保留"),
      frame_breathing_room_ok: tr("邊緣呼吸感合理"),
      not_overedited: tr("未過度美化"),
      light_color_improved: tr("光色改善")
    };
    return Object.entries(qa.checks)
      .slice(0, 8)
      .map(([key, value]) => tr("{v1}：{v2}", { v1: value === true ? tr("通過") : value === false ? tr("注意") : tr("未定"), v2: labels[key] || key }));
  }

  // ── Per-person face review tabs (#ap-face-tabs / #ap-face-detail) ──
  // detect_face_crops (openai_client.analyze_photo) hands back every locally-
  // detected face in analysis._local_face_boxes, normalized against the
  // original image -- same convention as subjects[].box (prompts.py's shared
  // _BOX_SCHEMA). Only the first <=3 (largest first) got an actual zoomed
  // crop sent to the model as an image (sent_to_model=true, also mirrored
  // into analysis._face_crop_boxes for the debug box overlay); the rest are
  // still shown here (localFaceEntries/renderFaceThumbnails below), just
  // visually marked as local-detector-only since they never got that deeper
  // per-face judgement.
  // blink_issue/gaze_issue/hair_issue/smile_issue live nested inside each
  // subjects[] entry (not as separate top-level arrays), so a face thumbnail
  // is matched once against subjects[] rather than once per issue category.
  // subjects[].box is required to "tightly frame EXACTLY ONE individual
  // person" -- i.e. that person's own bounding box, not necessarily just
  // their face -- while a face crop's box is padded only ~60%/40% around the
  // detected face itself (image_tools.py's detect_face_crops), so the two
  // conventions describe different-sized regions of the same person. No
  // face/person id is ever shared between the two (openai_client never
  // correlates them -- see pipeline.py's own note on why gaze repair
  // deliberately avoids that same cross-referencing problem), so overlap is
  // the only signal available: this is a best-effort match, same spirit as
  // detect_face_crops itself, and can misfire in a tightly packed group
  // photo where people's boxes overlap each other.
  // flagField/flagWhen/flagText/okText control which issues get the "allow
  // AI to fix this" toggle in buildPersonIssueItems below: flagged means there
  // is a proposed action for the user to accept or decline. The first three
  // flag on should_fix (true = a fix is proposed). smile_issue has no
  // should_fix at all -- its equivalent field is adjust_safe, and adjust_
  // safe=true is the case with a proposed action (auto mode may adjust the
  // smile), so it flags at true, not false. adjust_safe=false is shown as a
  // warning with the switch initially off, but smile alone remains manually
  // controllable so the user can explicitly override that recommendation.
  const FACE_ISSUE_CATEGORIES = [
    { field: "blink_issue", label: tr("閉眼偵測"), flagField: "should_fix", flagWhen: true, flagText: tr("需要修正"), okText: tr("無需修正") },
    { field: "gaze_issue", label: tr("眼神對焦"), flagField: "should_fix", flagWhen: true, flagText: tr("需要修正"), okText: tr("無需修正") },
    { field: "hair_issue", label: tr("亂髮偵測"), flagField: "should_fix", flagWhen: true, flagText: tr("需要修正"), okText: tr("無需修正") },
    { field: "smile_issue", label: tr("笑容調整@@人像檢查"), flagField: "adjust_safe", flagWhen: true, flagText: tr("可以調整"), okText: tr("不建議調整") },
    { field: "body_reshape", label: tr("身形調整"), flagField: "should_edit", flagWhen: true, flagText: tr("可以調整"), okText: tr("無須修正") }
  ];

  // Every category the user can manually switch on even when the analyzer
  // itself found nothing to fix -- photo_agent/selections.py's apply_selections sets
  // issue._user_forced for the face categories when a toggle is flipped on
  // an unflagged row, and prompts.py's resolve_user_hair_request/
  // resolve_user_blink_request/resolve_user_gaze_request (plus smile's own
  // explicit-level path) turn that into a real edit instruction. Unlike hair,
  // blink/gaze have no per-person style level -- see LEVEL_SELECT_CATEGORIES
  // below -- so they don't appear in that second list.
  const MANUAL_OVERRIDE_CATEGORIES = ["blink_issue", "gaze_issue", "hair_issue", "smile_issue"];
  const LEVEL_SELECT_CATEGORIES = ["hair_issue", "smile_issue"];
  const FACE_ISSUE_LEVEL_CHOICES = {
    hair_issue: [["dynamic", tr("保留髮絲動態")], ["tidy", tr("梳整俐落")]],
    smile_issue: [["auto", tr("自動")], ["neutral", tr("不笑")], ["gentle", tr("微笑")], ["broad", tr("開朗笑")], ["laughing", tr("大笑")]]
  };

  // Intersection over the SMALLER of the two boxes' own area (the
  // Szymkiewicz-Simpson overlap coefficient), not plain IoU -- faceBox and
  // a subject's own box are rarely similar in size (see the comment above),
  // so requiring most of the *larger* box to be covered would almost never
  // trigger. This instead asks "does nearly all of whichever box is smaller
  // sit inside the other one", which holds whichever way the size
  // difference actually goes.
  function boxOverlapCoefficient(a, b) {
    const ix = Math.max(a.x, b.x);
    const iy = Math.max(a.y, b.y);
    const ix2 = Math.min(a.x + a.w, b.x + b.w);
    const iy2 = Math.min(a.y + a.h, b.y + b.h);
    const iw = ix2 - ix;
    const ih = iy2 - iy;
    if (iw <= 0 || ih <= 0) return 0;
    const smallerArea = Math.min(a.w * a.h, b.w * b.h);
    return smallerArea > 0 ? (iw * ih) / smallerArea : 0;
  }

  function bestSubjectMatch(subjects, faceBox) {
    let best = null;
    let bestIndex = -1;
    let bestScore = 0.5; // most of the smaller of the two boxes has to fall inside the other
    (Array.isArray(subjects) ? subjects : []).forEach((subject, index) => {
      const box = subject && subject.box;
      if (!box || ![box.x, box.y, box.w, box.h].every(Number.isFinite)) return;
      const score = boxOverlapCoefficient(faceBox, box);
      if (score > bestScore) { bestScore = score; best = subject; bestIndex = index; }
    });
    return { subject: best, index: bestIndex };
  }

  // Prefers the analysis model's own authoritative answer (analysis.
  // local_face_review[].matched_subject_index, prompts.py's ANALYSIS_
  // RESPONSE_SCHEMA) over the box-overlap heuristic above: the model was
  // told exactly which subjects[] entry each locally-detected face became,
  // if any, so there is no guessing left to do once that field exists.
  // detectionIndex is 1-based, matching local_face_review[].detection_index
  // and the "Face crop N" / "Local face detector reference N" numbering
  // (openai_client.py's analyze_photo) -- i.e. the face's own 1-based
  // position in analysis._local_face_boxes.
  // matched=true means local_face_review had an entry for this detection
  // (even when it resolved to "no subject" -- a bystander or detector false
  // positive); matched=false means no local_face_review data exists at all
  // (an analysis saved before this field existed), so the caller should fall
  // back to bestSubjectMatch's box-overlap guess instead.
  function subjectForDetection(analysis, detectionIndex, faceBox) {
    const review = (analysis?.local_face_review || []).find((r) => r?.detection_index === detectionIndex);
    if (review) {
      const idx = review.matched_subject_index;
      const subjects = Array.isArray(analysis?.subjects) ? analysis.subjects : [];
      const subject = Number.isInteger(idx) && idx >= 0 && idx < subjects.length ? subjects[idx] : null;
      return { subject, index: subject ? idx : -1, matched: true };
    }
    return { ...bestSubjectMatch(analysis?.subjects, faceBox), matched: false };
  }

  // subjectIndex/category identify the underlying analysis.subjects[i][category]
  // field this entry came from -- the toggle in buildPersonIssueItems writes
  // back through them (getFaceIssueOverrides), and apply_selections (photo_agent/selections.py)
  // uses the same pair to flip should_fix/adjust_safe before submission.
  function faceIssueEntries(subject, index, bodyDisabled = false) {
    if (!subject) return [];
    return FACE_ISSUE_CATEGORIES.map((category) => {
      const match = subject[category.field];
      if (!match || typeof match !== "object") return null;
      const isBodyReshape = category.field === "body_reshape";
      const bodyAnalysisStatus = isBodyReshape ? match.analysis_status || "" : "";
      // User opt-out preserves the executable plan for a later edit.
      const bodyAdjustable = match.should_edit !== false || !!match._user_disabled;
      const bodyPrompt = isBodyReshape && bodyAdjustable
        && typeof match.edit_prompt === "string"
        ? match.edit_prompt.trim()
        : "";
      // A no-op body plan intentionally has no edit_prompt: keep its row long
      // enough for the renderer to suppress the whole no-op item. An analysis
      // error remains available for diagnosis, even when it has no command.
      if (isBodyReshape && subject.is_person !== true) return null;
      if (isBodyReshape && bodyAnalysisStatus !== "error"
        && match.should_edit !== false && !bodyPrompt) return null;
      const isManualOverride = MANUAL_OVERRIDE_CATEGORIES.includes(category.field);
      // apply_selections (photo_agent/selections.py) writes _user_forced/_user_level onto
      // this same subject[category.field] object at submit time, and the
      // backend echoes analysis back verbatim -- so a completed record (or a
      // freshly re-rendered revise) carries the *previous* manual-on decision
      // right here, not just the analyzer's own should_fix/adjust_safe. Without
      // this, reopening a record with a forced fix would recompute allowed
      // straight from the untouched original verdict and silently show the
      // toggle as off again.
      const wasUserForced = !!match._user_forced;
      // The mirror of _user_forced (apply_selections, photo_agent/selections.py): the user
      // turned OFF something the analyzer HAD flagged. Needed because that
      // same write overwrites the verdict it overrides -- should_fix goes
      // true -> false -- so without this a later version could not tell "the
      // analyzer found nothing" (無需修正) from "the user said don't"
      // (已設定不修正), and smile, whose adjust_safe is left flagged instead,
      // came back looking as though the choice had never been made.
      const wasUserDisabled = !!match._user_disabled;
      // The "set all" global lock (#advanced-settings-fold's toggle for this
      // category) is applied at render time in buildPersonIssueItems below,
      // via faceIssueGloballyEnabled/reviewAdvancedOptions -- not baked in
      // here. So this stays the plain per-analysis verdict.
      const flagged = isBodyReshape
        ? bodyAnalysisStatus === "error" || bodyAdjustable
        : match[category.flagField] === category.flagWhen || wasUserDisabled;
      const allowed = isBodyReshape
        ? bodyAdjustable && !wasUserDisabled && !bodyDisabled
        : (match[category.flagField] === category.flagWhen || wasUserForced) && !wasUserDisabled;
      return {
        label: category.label,
        flagged,
        flagText: category.flagText,
        okText: category.okText,
        reason: isBodyReshape ? (match.report || match.reason || "") : (match.reason || ""),
        report: isBodyReshape && typeof match.report === "string" ? match.report.trim() : "",
        promptDisplay: isBodyReshape && typeof match.prompt_display === "string"
          ? match.prompt_display.trim() : "",
        editPrompt: isBodyReshape ? bodyPrompt : "",
        analysisStatus: bodyAnalysisStatus,
        category: category.field,
        subjectIndex: index,
        // Body Reshape can only be toggled when Analyze supplied an executable
        // command. A should_edit=false plan has no command to force on.
        controllable: isBodyReshape
          ? bodyAnalysisStatus !== "error" && bodyAdjustable && Boolean(bodyPrompt)
          : match[category.flagField] === category.flagWhen || isManualOverride,
        allowed,
        level: category.field === "hair_issue"
          ? (match._user_level || "dynamic")
          : category.field === "smile_issue"
            ? (match._user_level || "auto")
            : null,
        levelUserTouched: !!match._user_level,
        allowedTouchVersion: 0,
        levelTouchVersion: 0,
        poseSuggestedFields: {}
      };
    }).filter(Boolean);
  }

  const EXECUTED_BODY_RESHAPE_PROMPT_MARKER = /Body reshape execution instructions \(analysis-approved\):/i;

  // Build the same person-to-face association used by 人像檢查, but without
  // mutating the review tabs.  Result labels need this lightweight read even
  // when the inline review panel has not been opened yet.
  function portraitInspectionSources(analysis) {
    const subjects = Array.isArray(analysis?.subjects) ? analysis.subjects : [];
    const sources = [];
    const represented = new Set();
    localFaceEntries(analysis).forEach((face, index) => {
      // `box` is the detector crop sent to Analyze and can include shoulders;
      // `face_box` is the tighter facial rectangle and is the right anchor for
      // quadrant placement and face-collision avoidance.
      const detectionBox = face?.box || face?.face_box;
      const box = face?.face_box || detectionBox;
      if (!detectionBox || !box
        || ![detectionBox.x, detectionBox.y, detectionBox.w, detectionBox.h,
          box.x, box.y, box.w, box.h].every(Number.isFinite)) return;
      const match = subjectForDetection(analysis, index + 1, detectionBox);
      const subject = match.subject;
      if (!subject || subject.is_person !== true || !Number.isInteger(match.index) || match.index < 0) return;
      if (represented.has(match.index)) return;
      represented.add(match.index);
      sources.push({ subject, subjectIndex: match.index, box, thumbnailBox: detectionBox });
    });
    subjects.forEach((subject, subjectIndex) => {
      if (represented.has(subjectIndex) || !recoverableSubject(analysis, subject)) return;
      const box = subject.box;
      if (!box || ![box.x, box.y, box.w, box.h].every(Number.isFinite)) return;
      represented.add(subjectIndex);
      // No detector match: the subject box includes the torso. Use its upper
      // center as a conservative head proxy so labels stay near the person
      // instead of being pushed to a distant image edge.
      const headBox = {
        x: box.x + box.w * 0.18,
        y: Math.max(0, box.y - box.h * 0.08),
        w: box.w * 0.64,
        h: box.h * 0.32,
      };
      const thumbnail = analysis?._subject_thumbnail_boxes?.[String(subjectIndex)];
      sources.push({ subject, subjectIndex, box: headBox, thumbnailBox:
        thumbnail && [thumbnail.x, thumbnail.y, thumbnail.w, thumbnail.h].every(Number.isFinite) ? thumbnail : box });
    });
    return sources;
  }

  function escapePromptRegExp(value) {
    return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // Editor prompts group commands under `Subject: <label>` (the gaze-only
  // pass uses the same heading with a leading dash).  Restrict marker checks
  // to that subject block so a command for one person cannot become a label
  // on every other person's face merely because their names occur elsewhere
  // in the holistic prompt.
  function promptSubjectSection(prompt, subject) {
    const label = String(subject?.label || "").trim();
    if (!label) return "";
    const heading = new RegExp(
      `(?:^|\\n)[ \t]*(?:-\\s*)?Subject:[ \t]*${escapePromptRegExp(label)}[ \t]*(?:\\n|$)`,
      "i"
    );
    const match = heading.exec(prompt);
    if (!match) return "";
    const start = match.index;
    const restStart = start + match[0].length;
    const rest = prompt.slice(restStart);
    const end = rest.search(
      /\n[ \t]*(?:-\s*)?Subject:[ \t]*|\n[ \t]*(?:Shared facial execution rules|Body reshape execution instructions|Targets|Other approved corrections|Finished light and color target|Scope is limited|Execution by method):/i
    );
    return prompt.slice(start, end >= 0 ? restStart + end : prompt.length);
  }

  function promptHasSubjectMarker(prompt, subject, markers, allowGeneric = false, subjectCount = 1) {
    const scopedPrompt = promptSubjectSection(prompt, subject)
      || (subjectCount <= 1 ? prompt : "");
    if (promptMatchesAny(scopedPrompt, markers)) return true;
    return allowGeneric && subjectCount <= 1
      && /For every person whose expression is readable:\s*target_smile:/i.test(prompt);
  }

  // Return only items that both have an allowed per-person verdict and are
  // represented in the actual Editor prompt.  A global prompt marker alone
  // is not enough: one person may have been opted out while another remains
  // active, and Body Reshape is independently approved per person.
  function buildPortraitInspectionEntries(analysis, pending = false) {
    if (pending || analysis?.user_instruction?.overwrite_prompt) return [];
    const prompt = actualEditorPromptText(analysis);
    if (!prompt) return [];
    const advanced = analysis?._advanced_options || {};
    const bodyEnabled = bodyReshapeEnabled && analysis?._body_reshape_enabled !== false;
    const disabledBodies = new Set(
      (Array.isArray(analysis?._disabled_body_reshape_subjects)
        ? analysis._disabled_body_reshape_subjects : [])
        .filter((index) => Number.isInteger(index) && index >= 0)
    );
    const sources = portraitInspectionSources(analysis);
    return sources.map(({ subject, subjectIndex, box, thumbnailBox }) => {
      const issues = faceIssueEntries(subject, subjectIndex, disabledBodies.has(subjectIndex));
      const tags = [];
      EXECUTED_FACE_PROMPT_TAGS.forEach((definition) => {
        const issue = issues.find((item) => item.category === definition.field);
        if (!issue || !issue.allowed || advanced[definition.option] === false) return;
        const genericSmile = definition.field === "smile_issue";
        if (promptHasSubjectMarker(prompt, subject, definition.markers, genericSmile, sources.length)) {
          tags.push(definition.label);
        }
      });
      const body = subject.body_reshape;
      const bodyIssue = issues.find((item) => item.category === "body_reshape");
      const bodyPromptText = typeof body?.edit_prompt === "string" ? body.edit_prompt.trim() : "";
      const bodyLabelInPrompt = String(subject.label || "").trim();
      // The body block names every active subject, so a label-only check would
      // mark a second person's body row merely because that person's face
      // correction appears elsewhere in the same Editor prompt. Prefer the
      // exact approved body sentence; retain the label fallback for old
      // records whose saved prompt was normalized without the sentence.
      const bodyTargetInPrompt = bodyPromptText
        ? prompt.includes(bodyPromptText)
        : (bodyLabelInPrompt ? prompt.includes(bodyLabelInPrompt) : true);
      if (
        bodyEnabled
        && bodyIssue?.allowed
        && bodyIssue.controllable
        && body
        && body.should_edit !== false
        && typeof body.edit_prompt === "string"
        && body.edit_prompt.trim()
        && EXECUTED_BODY_RESHAPE_PROMPT_MARKER.test(prompt)
        && bodyTargetInPrompt
      ) {
        tags.push(tr("身形調整"));
      }
      return {
        subject,
        subjectIndex,
        box,
        thumbnailBox,
        label: subject.label || tr("人物 {v1}", { v1: subjectIndex + 1 }),
        tags: [...new Set(tags)]
      };
    }).filter((entry) => entry.tags.length);
  }

  // Loading text describes the submitted UI choices, not past execution or
  // worker diagnostics. Reuse the same verdicts/labels as the review cards.
  function buildPlannedEditMessages({ analysis, selections = {}, userPrompt = "", overwritePrompt = false, aiPhotoEditing = true } = {}) {
    const sentence = (text) => {
      const value = typeof text === "string" ? text.trim() : "";
      return value ? (/[。！？.!?…]$/.test(value) ? value : value + tr("。")) : "";
    };
    const direction = typeof userPrompt === "string" ? userPrompt.trim() : "";
    if (!aiPhotoEditing) return []; // These controls describe AI editing, not local processing.
    if (overwritePrompt && direction) return [sentence(tr("依照您的個人化需求修圖：") + direction)];
    const advanced = selections.advanced_options || analysis?._advanced_options || {};
    const overrides = selections.face_issue_overrides || {};
    const optionFor = { blink_issue: "eye_closed_detection", gaze_issue: "gaze_contact_detection",
      hair_issue: "hair_mess_detection", smile_issue: "smile_adjustment" };
    const disabledBodies = new Set(analysis?._disabled_body_reshape_subjects || []);
    const people = (Array.isArray(analysis?.subjects) ? analysis.subjects : []).map((subject, index) => {
      if (!subject || subject.is_person !== true) return [];
      return faceIssueEntries(subject, index, disabledBodies.has(index)).flatMap((issue) => {
        const override = overrides[`${index}:${issue.category}`];
        const allowed = override === false ? false
          : override && typeof override === "object" ? override.allowed !== false : issue.allowed;
        const level = override?.level || issue.level;
        if (!allowed || level === "none" || advanced[optionFor[issue.category]] === false) return [];
        if (issue.category === "body_reshape" && (!bodyReshapeEnabled
          || selections.body_reshape_enabled === false || (selections.body_reshape_enabled == null && analysis?._body_reshape_enabled === false)
          || !issue.controllable)) return [];
        const levelLabel = FACE_ISSUE_LEVEL_CHOICES[issue.category]?.find(([value]) => value === level)?.[1];
        // Lowercase so the fix reads mid-sentence in English ("Adjusting hair: ...").
        return [sentence(tr("為{who}進行{fix}", { who: subject.label || tr("人物 {n}", { n: index + 1 }), fix: issue.label.toLowerCase() }) + (levelLabel ? tr("（{level}）", { level: levelLabel }) : ""))];
      });
    });
    const messages = [];
    for (let i = 0; i < Math.max(0, ...people.map((items) => items.length)); i += 1) {
      people.forEach((items) => { if (items[i]) messages.push(items[i]); });
    }
    const checked = selections.recipe_checked;
    const recipes = (Array.isArray(analysis?.retouch_recipe) ? analysis.retouch_recipe : [])
      .filter((_, index) => !Array.isArray(checked) || checked[index] === true)
      .filter((item) => typeof item === "string" && item.trim());
    recipes.sort((a, b) => Number(/^重新構圖\s*[:：]/.test(b)) - Number(/^重新構圖\s*[:：]/.test(a)));
    messages.push(...recipes.map((item) => sentence(shownRecipe(item))));
    const options = selections.options || {};
    Object.entries(options).forEach(([key, value]) => {
      const group = STYLE_OPTION_GROUPS[key];
      const option = group?.options.find((item) => item.value === value && value !== "default");
      if (option) messages.push(sentence(tr("設定{label}為「{v2}」", { label: group.label, v2: option.label })));
    });
    if (direction) messages.push(sentence(tr("也依照您的個人化需求修圖：") + direction));
    return messages;
  }

  // Flattened { "subjectIndex:category": false } for every flagged issue the
  // user turned off in the face modal -- read once at submit time
  // (buildEditSelections, app.js) rather than kept in sync continuously,
  // matching how recipeChecked is read fresh off the recipe checkboxes.
  function getFaceIssueOverrides() {
    const overrides = {};
    faceThumbEntries.forEach((entry) => {
      (entry.issues || []).forEach((issue) => {
        if (!issue.flagged && !MANUAL_OVERRIDE_CATEGORIES.includes(issue.category)) return;
        if (issue.allowed === false) overrides[`${issue.subjectIndex}:${issue.category}`] = false;
        else if (issue.level && (issue.levelUserTouched || issue.poseSuggestedFields?.level)) {
          overrides[`${issue.subjectIndex}:${issue.category}`] = { allowed: true, level: issue.level };
        }
        // blink/gaze have no level (LEVEL_SELECT_CATEGORIES) -- an on toggle
        // with nothing else to record still has to reach app.js, or a
        // manually-enabled unflagged row would silently vanish at submit time.
        else if (issue.allowed) overrides[`${issue.subjectIndex}:${issue.category}`] = { allowed: true };
      });
    });
    return overrides;
  }

  // Restores a previously captured getFaceIssueOverrides() result onto the
  // just-rebuilt faceThumbEntries -- called after a re-render (backToProcess-
  // FromResults' revise flow, or reopening a batch record's review panel) so
  // switching away from and back to this photo doesn't silently re-allow a
  // fix the user had turned off.
  function applyFaceIssueOverrides(overrides) {
    if (!overrides) return;
    faceThumbEntries.forEach((entry) => {
      (entry.issues || []).forEach((issue) => {
        const override = overrides[`${issue.subjectIndex}:${issue.category}`];
        if (override === false) {
          issue.allowed = false;
          issue.allowedTouchVersion = (issue.allowedTouchVersion || 0) + 1;
        }
        else if (override && typeof override === "object") {
          issue.allowed = override.allowed !== false;
          issue.allowedTouchVersion = (issue.allowedTouchVersion || 0) + 1;
          if (override.level) {
            issue.level = override.level;
            issue.levelUserTouched = true;
            issue.levelTouchVersion = (issue.levelTouchVersion || 0) + 1;
          }
        }
      });
    });
  }

  // "Set all" action for one category's own #advanced-settings-fold toggle,
  // called from that toggle's own "change" handler (app.js's
  // ADVANCED_OPTION_FACE_CATEGORIES wiring) right before applyAdvancedOptionsChange
  // -- which reads the fresh DOM state and calls syncFaceReviewAdvancedOptions
  // below, the thing that actually keeps reviewAdvancedOptions (and so
  // faceIssueGloballyEnabled/globallyEnabled at every render) in sync. This
  // function only owns the DATA side of the toggle, `issue.allowed`: turning
  // the category OFF forces every person's allowed to false immediately, so
  // getFaceIssueOverrides (read at submit time) is correct even before the
  // next render; turning it back ON restores every person's allowed to the
  // analyzer's own verdict (issue.flagged, i.e. should_fix/adjust_safe) --
  // discarding the bulk-off and any earlier manual per-person pick, rather
  // than leaving everyone still unchecked, which is what merely unlocking
  // without resetting would do. Never touches `issue.controllable` -- that
  // stays purely the analyzer/manual-override verdict faceIssueEntries
  // already computed; the lock itself is applied at render time via
  // globallyEnabled (buildPersonIssueItems below), combined with
  // controllable at each use site, not baked into controllable itself.
  function setFaceIssueCategoryLock(category, enabled) {
    faceThumbEntries.forEach((entry) => {
      (entry.issues || []).forEach((issue) => {
        if (issue.category !== category) return;
        issue.allowed = enabled ? issue.flagged : false;
        issue.allowedTouchVersion = (issue.allowedTouchVersion || 0) + 1;
      });
    });
  }

  function cropFaceThumbnail(img, box) {
    const nw = img?.naturalWidth;
    const nh = img?.naturalHeight;
    if (!nw || !nh) return "";
    const sx = Math.max(0, Math.round(box.x * nw));
    const sy = Math.max(0, Math.round(box.y * nh));
    const sw = Math.min(nw - sx, Math.max(1, Math.round(box.w * nw)));
    const sh = Math.min(nh - sy, Math.max(1, Math.round(box.h * nh)));
    const canvas = document.createElement("canvas");
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext("2d");
    try {
      ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
      return canvas.toDataURL("image/jpeg", 0.85);
    } catch (err) {
      // Tainted canvas (a cross-origin image with no CORS headers) --
      // best-effort thumbnail only, never worth failing the whole render.
      return "";
    }
  }

  function buildPortraitAdjustmentList(entries) {
    const list = document.createElement("div");
    list.className = "portrait-adjustment-list";
    list.setAttribute("role", "list");
    const original = els.originalImg;
    const source = original?.src;
    const thumbnails = [];
    entries.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "portrait-adjustment-row";
      row.setAttribute("role", "listitem");
      const thumbnail = document.createElement("img");
      thumbnail.className = "portrait-adjustment-thumbnail";
      thumbnail.alt = entry.label;
      thumbnail.title = entry.label;
      thumbnails.push({ thumbnail, box: entry.thumbnailBox });
      const items = document.createElement("div");
      items.className = "portrait-adjustment-items";
      entry.tags.forEach((tag) => {
        const item = document.createElement("span");
        item.className = "portrait-adjustment-item";
        item.setAttribute("aria-label", tr("{tag}，已完成", { tag }));
        const check = document.createElement("span");
        check.className = "portrait-check-complete";
        check.setAttribute("aria-hidden", "true");
        check.textContent = "✓";
        item.append(check, document.createTextNode(tag));
        items.appendChild(item);
      });
      row.append(thumbnail, items);
      list.appendChild(row);
    });
    const fillThumbnails = () => {
      if (!original || original.src !== source) return;
      thumbnails.forEach(({ thumbnail, box }) => {
        const dataUrl = cropFaceThumbnail(original, box);
        if (dataUrl) thumbnail.src = dataUrl;
      });
    };
    if (original?.complete) fillThumbnails();
    else original?.addEventListener("load", fillThumbnails, { once: true });
    return list;
  }

  // { box, issues }[] for the face tabs currently on screen -- kept
  // module-level (not just local to renderFaceThumbnails) because
  // getFaceIssueOverrides (app.js, at submit time) reads the in-memory
  // issue.allowed state each face toggle mutates directly, not the DOM.
  let faceThumbEntries = [];

  // Drops any face-review state left over from a previously selected photo.
  // Needed because renderFaceThumbnails (which normally repopulates
  // faceThumbEntries from scratch) doesn't run until the review panel is
  // actually built for the new photo -- the skip-review "一鍵" flow instead
  // calls getFaceIssueOverrides() from finishSingleFlow() before that ever
  // happens, so without this it silently returns the previous photo's
  // allowed/flagged state and gets stamped onto the new one's (see
  // setSelectedFile's call to this in app.js).
  function resetFaceReviewState() {
    faceThumbEntries = [];
  }

  // The global 人像修正設定 currently in force for the face-review rows. Kept
  // separately from issue.allowed: manually turning a whole global category
  // off should make every row look disabled without erasing each person's
  // own allow/deny choice, so it can reappear unchanged when restored.
  let reviewAdvancedOptions = {};

  // Pose-owned changes inside the currently rendered per-person review.
  // Kept separate from reviewAdvancedOptions because these values are the
  // individual allow/level choices that get submitted as face overrides.
  let facePoseSuggestionState = null;

  const FACE_ISSUE_OPTION_KEYS = {
    blink_issue: "eye_closed_detection",
    gaze_issue: "gaze_contact_detection",
    hair_issue: "hair_mess_detection",
    smile_issue: "smile_adjustment"
  };

  function faceIssueGloballyEnabled(category) {
    const key = FACE_ISSUE_OPTION_KEYS[category];
    return !key || reviewAdvancedOptions[key] !== false;
  }

  // Called when the shared 人像修正設定 modal changes after faceThumbEntries was
  // already built. Re-render only the selected person's detail: rebuilding
  // from analysis would discard in-memory overrides, while rebuilding every
  // thumbnail would needlessly re-crop all faces for a text/toggle change.
  function syncFaceReviewAdvancedOptions(advancedOptions) {
    reviewAdvancedOptions = { ...(advancedOptions || {}) };
    renderFaceDetailPanel();
  }

  function restoreFaceReviewPoseSuggestion() {
    const state = facePoseSuggestionState;
    if (!state) return { pose: null, changed: false };
    let changed = false;
    (state.changes || []).forEach(({ issue, fields }) => {
      Object.entries(fields || {}).forEach(([field, saved]) => {
        const versionKey = field === "allowed" ? "allowedTouchVersion" : "levelTouchVersion";
        // Preserve a choice made by the user after Pose supplied its value.
        if ((issue[versionKey] || 0) !== saved.touchVersion || issue[field] !== saved.applied) return;
        issue[field] = saved.before;
        changed = true;
      });
      if (issue.poseSuggestedFields) {
        Object.keys(fields || {}).forEach((field) => delete issue.poseSuggestedFields[field]);
      }
    });
    facePoseSuggestionState = null;
    return { pose: state.pose, changed };
  }

  // Applies the Pose recommendation only to each person's own controls; the
  // shared 人像修正設定 modal is deliberately untouched. Gaze-off poses switch
  // every person's gaze item off; explicit smile poses opt every person in
  // at that smile level; Dynamic changes hair level only for people whose
  // own hair item is already enabled. Returns only net-visible category
  // changes: a category restored and then immediately set to the same value
  // by the new Pose is omitted, which keeps the combined notice accurate.
  // The chat mirrors these rules in frontend-chat/src/face.ts POSE_FACE_RULES.
  function syncFaceReviewPoseSuggestion(pose) {
    const valuesBeforeSwitch = new Map();
    faceThumbEntries.forEach((entry) => {
      (entry.issues || []).forEach((issue) => {
        valuesBeforeSwitch.set(issue, { allowed: issue.allowed, level: issue.level });
      });
    });
    const restored = restoreFaceReviewPoseSuggestion();
    const rules = ({
      playful: [{ category: "smile_issue", values: { allowed: true, level: "broad" } }],
      candid: [{ category: "gaze_issue", values: { allowed: false } }],
      elegant: [{ category: "smile_issue", values: { allowed: true, level: "gentle" } }],
      dynamic: [
        { category: "gaze_issue", values: { allowed: false } },
        { category: "hair_issue", values: { level: "dynamic" }, onlyWhenAllowed: true }
      ],
      cool: [{ category: "smile_issue", values: { allowed: true, level: "neutral" } }],
      vsign: [{ category: "smile_issue", values: { allowed: true, level: "broad" } }],
      cheer: [{ category: "smile_issue", values: { allowed: true, level: "broad" } }]
    })[pose] || [];

    const changes = [];
    const appliedCategoryCandidates = new Set();
    rules.forEach(({ category, values, onlyWhenAllowed = false }) => {
      faceThumbEntries.forEach((entry) => {
        const issue = (entry.issues || []).find((item) => item.category === category);
        if (!issue || (onlyWhenAllowed && !issue.allowed)) return;
        const fields = {};
        Object.entries(values).forEach(([field, value]) => {
          if (issue[field] === value) return;
          const versionKey = field === "allowed" ? "allowedTouchVersion" : "levelTouchVersion";
          fields[field] = {
            before: issue[field],
            applied: value,
            touchVersion: issue[versionKey] || 0
          };
          issue[field] = value;
          issue.poseSuggestedFields = issue.poseSuggestedFields || {};
          issue.poseSuggestedFields[field] = true;
        });
        if (Object.keys(fields).length) {
          changes.push({ issue, fields });
          appliedCategoryCandidates.add(category);
        }
      });
    });
    facePoseSuggestionState = changes.length ? { pose, changes } : null;
    const netChangedCategories = new Set();
    valuesBeforeSwitch.forEach((before, issue) => {
      if (issue.allowed !== before.allowed || issue.level !== before.level) {
        netChangedCategories.add(issue.category);
      }
    });
    const appliedCategories = [...netChangedCategories].filter((category) =>
      appliedCategoryCandidates.has(category));
    const restoredCategories = [...netChangedCategories].filter((category) =>
      !appliedCategoryCandidates.has(category));
    if (restored.changed || changes.length) renderFaceDetailPanel();
    return {
      previousPose: restored.pose,
      restoredCategories,
      appliedCategories
    };
  }

  // Which faceThumbEntries index #ap-face-detail currently shows -- reset to
  // 0 whenever renderFaceThumbnails rebuilds faceThumbEntries from a fresh
  // analysis (a different photo/person set makes any other index stale),
  // otherwise left alone so re-renders triggered elsewhere (restoring
  // overrides, a style pick) don't silently switch which person is showing.
  let selectedFaceIndex = 0;

  // Whether #ap-review-panel (the merged 修圖建議/人像檢查 <details>) should
  // start open -- set by renderAnalysisPreview's own `expanded` param, read
  // by renderFaceReviewCards. See renderAnalysisPreview's doc for the full
  // reasoning.
  let reviewExpanded = true;

  // Which top-level tab of #ap-review-panel is showing -- "recipe" for
  // #ap-recipe-list, "face" for the per-person avatar row (#ap-face-tabs)
  // plus #ap-face-detail. Reset to "face" on every fresh analysis
  // (renderAnalysisPreview) so a new photo always lands back on the
  // per-person review first, same as selectedFaceIndex resetting to 0
  // below -- renderFaceReviewCards falls back to "recipe" right after if
  // this particular photo has no faces to show there.
  let activeReviewTab = "face";

  // Every locally-detected face (openai_client.analyze_photo's
  // _local_face_boxes), not just the <=3 that got a zoomed crop sent to the
  // model (_face_crop_boxes) -- detect_face_crops' own token-cost cap. Falls
  // back to _face_crop_boxes for an older saved analysis (history record)
  // that predates _local_face_boxes, treating every one of those as
  // sent_to_model=true since that field's whole population always was.
  function localFaceEntries(analysis) {
    if (Array.isArray(analysis?._local_face_boxes)) return analysis._local_face_boxes;
    if (Array.isArray(analysis?._face_crop_boxes)) {
      return analysis._face_crop_boxes.map((box) => ({ box, sent_to_model: true }));
    }
    return [];
  }

  function recoverableSubject(analysis, subject) {
    const box = subject?.box;
    const fixed = Array.isArray(analysis?._fixed_preview_person_ids)
      && analysis._fixed_preview_person_ids.includes(subject?.preview_person_id);
    if ((!fixed && subject?.is_person !== true) || !box
      || ![box.x, box.y, box.w, box.h].every(Number.isFinite)
      || box.x < 0 || box.y < 0 || box.w <= 0 || box.h <= 0
      || box.x + box.w > 1 || box.y + box.h > 1) return false;
    const detection = subject.local_detection_index;
    return !(Number.isInteger(detection) && detection > 0
      && (analysis.local_face_review || []).some(review => review?.detection_index === detection
        && (review.matched_subject_index === -1 || review.is_real_face === false)));
  }

  function renderFaceThumbnails(analysis) {
    reviewAdvancedOptions = { ...(analysis?._advanced_options || {}) };
    facePoseSuggestionState = null;
    // A different photo/person set makes whatever tab was previously
    // selected meaningless -- always land back on the first person.
    selectedFaceIndex = 0;
    const faces = localFaceEntries(analysis);
    const recoveredSubjects = (Array.isArray(analysis?.subjects) ? analysis.subjects : [])
      .map((subject, subjectIndex) => ({ subject, subjectIndex }))
      .filter(({ subject }) => recoverableSubject(analysis, subject));
    const bodyDisabledSubjects = new Set(
      (Array.isArray(analysis?._disabled_body_reshape_subjects)
        ? analysis._disabled_body_reshape_subjects : [])
        .filter((index) => Number.isInteger(index) && index >= 0)
    );
    if (!faces.length && !recoveredSubjects.length) {
      // No 人像檢查 tab to show -- renderFaceReviewCards (below) hides that
      // tab button and falls back to the 修圖建議 tab, it just needs
      // faceThumbEntries cleared first.
      faceThumbEntries = [];
      renderFaceReviewCards();
      return;
    }
    const img = els.originalImg;
    // #original-img's src is swapped in independently of this render (see
    // renderResult/showPendingResults in app.js) -- if it hasn't finished
    // loading yet, naturalWidth/Height (needed to crop in real pixels) isn't
    // available yet either. Re-render once it is, against whichever
    // analysis is current at that point.
    if (img && !img.complete) {
      img.addEventListener("load", () => renderFaceThumbnails(analysis), { once: true });
      return;
    }
    const detectedSubjectIndexes = new Set();
    faceThumbEntries = faces
      // detectionIndex is 1-based and must reflect each face's own position
      // in the original (unfiltered) faces/_local_face_boxes array -- it has
      // to line up with local_face_review[].detection_index, which the
      // model assigned from that same "Face crop N" / "Local face detector
      // reference N" ordering -- so this runs before the geometry filter
      // below, not after.
      .map((f, i) => ({ f, detectionIndex: i + 1 }))
      .filter(({ f }) => f?.box && [f.box.x, f.box.y, f.box.w, f.box.h].every(Number.isFinite))
      .map(({ f, detectionIndex }) => {
        const sentToModel = f.sent_to_model !== false;
        // Matched once here (rather than left to faceIssueEntries' own
        // internal match) so the subject's own label -- used as this
        // person's review-card name below -- comes from the exact same
        // match its issues do, not a second, potentially different one.
        const { subject, index, matched } = subjectForDetection(analysis, detectionIndex, f.box);
        // When the analysis has local_face_review, its verdict is
        // authoritative: no subject means the model itself already decided
        // this detection is a detector false positive or an excluded
        // bystander (see the Subject inclusion rule in ANALYSIS_
        // INSTRUCTION), so there is nothing meaningful to show -- drop it.
        // Without local_face_review (an analysis saved before this field
        // existed), fall back to the old heuristic: keep a local-only face
        // (beyond the crop cap) only when box-overlap still finds a subject
        // for it, since the model may have described this exact person from
        // its coordinate-only text hint alone.
        if (!subject && (matched || !sentToModel)) return null;
        if (index >= 0) {
          if (detectedSubjectIndexes.has(index)) return null;
          detectedSubjectIndexes.add(index);
        }
        return {
          box: f.box,
          issues: faceIssueEntries(subject, index, bodyDisabledSubjects.has(index)),
          subjectIndex: index,
          sentToModel,
          label: (subject && subject.label) || "",
        };
      })
      .filter(Boolean);

    // Append formally analyzed people missed by the detector, including body
    // plans, without manufacturing a face box or changing their subject index.
    const representedSubjectIndexes = new Set(
      faceThumbEntries.map((entry) => entry.subjectIndex)
        .filter((index) => Number.isInteger(index) && index >= 0)
    );
    recoveredSubjects.forEach(({ subject, subjectIndex }) => {
      if (representedSubjectIndexes.has(subjectIndex)) return;
      const thumbnail = analysis?._subject_thumbnail_boxes?.[String(subjectIndex)];
      const box = thumbnail && [thumbnail.x, thumbnail.y, thumbnail.w, thumbnail.h].every(Number.isFinite)
        ? thumbnail : subject.box;
      if (!box || ![box.x, box.y, box.w, box.h].every(Number.isFinite)) return;
      faceThumbEntries.push({
        box,
        issues: faceIssueEntries(subject, subjectIndex, bodyDisabledSubjects.has(subjectIndex)),
        subjectIndex,
        sentToModel: null,
        label: subject.label || "",
      });
    });

    renderFaceReviewCards();
  }

  // Rebuilds #ap-face-tabs (and #ap-face-detail for whichever tab is
  // selected) from whatever faceThumbEntries currently holds -- split out
  // from renderFaceThumbnails (which rebuilds faceThumbEntries itself from a
  // fresh analysis) so a caller that mutates entries in place *after*
  // renderAnalysisPreview already ran -- restoring a previous round's picks
  // via applyFaceIssueOverrides (app.js) -- can
  // re-render to reflect that restored state without rebuilding
  // faceThumbEntries itself (which would discard those very overrides right
  // back out). Exported under this name for that reason -- kept even though
  // it now renders tabs, not per-person cards. Also owns showing/hiding the
  // 人像檢查 top-level tab button itself (#ap-review-tab-face) -- there's no
  // longer a separate box to hide when there's nothing to review, so falling
  // back to the 修圖建議 tab when the active one just lost its content is
  // this function's job too.
  function renderFaceReviewCards() {
    const tabs = els.apFaceTabs;
    if (!tabs) return;
    if (els.apReviewTabFace) els.apReviewTabFace.hidden = !faceThumbEntries.length;
    if (!faceThumbEntries.length) {
      tabs.innerHTML = "";
      if (els.apFaceDetail) els.apFaceDetail.innerHTML = "";
      if (activeReviewTab === "face") activeReviewTab = "recipe";
      applyActiveReviewTab();
      return;
    }
    if (els.apAnalysisShell) els.apAnalysisShell.open = true;
    if (els.apReviewPanel) els.apReviewPanel.open = reviewExpanded;
    if (selectedFaceIndex >= faceThumbEntries.length) selectedFaceIndex = 0;

    const img = els.originalImg;
    tabs.innerHTML = "";
    faceThumbEntries.forEach((entry, index) => {
      const dataUrl = img ? cropFaceThumbnail(img, entry.box) : "";
      entry.thumbUrl = dataUrl;

      // wrap holds the active-state ring and the badge (see .ap-face-tab-wrap's
      // own CSS doc for why those can't live on/in .ap-face-tab itself, which
      // needs overflow:hidden to clip its thumbnail into a circle).
      const wrap = document.createElement("span");
      wrap.className = "ap-face-tab-wrap";
      if (index === selectedFaceIndex) wrap.classList.add("is-active");

      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "ap-face-tab";
      // sentToModel is false for a locally-detected face beyond the crop cap
      // (only a coordinate hint reached the model, see detect_face_crops/
      // analyze_photo) and null for a subjects[] entry the model found on
      // its own with no backing local detection at all (see the
      // subjects fallback in renderFaceThumbnails above). Either way, no
      // zoomed crop of this exact face ever reached the model, so any
      // findings it shows came from reading the full photo alone -- dimmed
      // here so that lower-confidence reading is visible at a glance (see
      // buildPersonIssueItems' own note li for the same caveat spelled out
      // in words once this tab is selected). Debug-only, same as the other
      // sentToModel caveats below -- release users see every face tab
      // rendered the same way regardless of how it was found.
      if (entry.sentToModel !== true && debugUiActive) tab.classList.add("ap-face-tab--local-only");
      const label = entry.label || tr("人物 {v1}", { v1: index + 1 });
      tab.title = label;
      tab.setAttribute("aria-label", label);
      if (dataUrl) {
        const thumb = document.createElement("img");
        thumb.className = "ap-face-tab-img";
        thumb.src = dataUrl;
        thumb.alt = label;
        tab.appendChild(thumb);
      }
      // This tab row lives inside #ap-review-panel's own <summary> (see its
      // markup comment) so the thumbnails stay visible even while the box
      // is collapsed -- without stopPropagation, a click here would also
      // bubble up to the summary's native click-to-toggle, sometimes
      // collapsing the box right as the user picks a person instead of just
      // switching tabs. Force-opening it explicitly means picking any tab
      // always reveals that person's detail below, whether the box was
      // already open or not -- otherwise, on the 結果 page (reviewExpanded
      // false, box starts collapsed), picking a person here would silently
      // do nothing visible at all.
      tab.addEventListener("click", (event) => {
        event.stopPropagation();
        if (els.apReviewPanel && !els.apReviewPanel.open) els.apReviewPanel.open = true;
        if (selectedFaceIndex === index) return;
        const prevActive = tabs.querySelector(".ap-face-tab-wrap.is-active");
        if (prevActive) prevActive.classList.remove("is-active");
        wrap.classList.add("is-active");
        selectedFaceIndex = index;
        renderFaceDetailPanel();
        revealFaceTab(wrap);
      });
      wrap.appendChild(tab);
      // Lets the user spot which people have a flagged issue without
      // stepping through every tab -- reflects the model's own verdict, not
      // the allow/deny toggle in #ap-face-detail, so it stays put even after
      // the user turns a flagged fix off (it's still something the analysis
      // found, just no longer approved).
      if ((entry.issues || []).some((issue) => issue.flagged)) {
        const badge = document.createElement("span");
        badge.className = "ap-face-tab-badge";
        badge.textContent = "!";
        wrap.appendChild(badge);
      }
      tabs.appendChild(wrap);
    });

    renderFaceDetailPanel();
    applyActiveReviewTab();
  }

  // Reflects activeReviewTab onto the four top-level tab buttons' active
  // state and shows/hides each one's content panel to match -- called after
  // anything that could change which tab is selected or available: a tab
  // click, or renderFaceReviewCards rebuilding faceThumbEntries (a fresh
  // analysis, or the 人像檢查 tab's content disappearing out from under it).
  function applyActiveReviewTab() {
    if (els.apReviewTabRecipe) els.apReviewTabRecipe.classList.toggle("is-active", activeReviewTab === "recipe");
    if (els.apReviewTabFace) els.apReviewTabFace.classList.toggle("is-active", activeReviewTab === "face");
    if (els.apReviewTabStyle) els.apReviewTabStyle.classList.toggle("is-active", activeReviewTab === "style");
    if (els.apReviewTabPrompt) els.apReviewTabPrompt.classList.toggle("is-active", activeReviewTab === "prompt");
    if (els.apRecipeList) els.apRecipeList.hidden = activeReviewTab !== "recipe";
    if (els.apFaceDetail) els.apFaceDetail.hidden = activeReviewTab !== "face";
    if (els.apFaceTabs) els.apFaceTabs.hidden = activeReviewTab !== "face";
    if (els.apReviewStyle) els.apReviewStyle.hidden = activeReviewTab !== "style";
    if (els.apReviewPrompt) els.apReviewPrompt.hidden = activeReviewTab !== "prompt";
  }

  // If a tab is only partly visible at the edge of a horizontal scrolling
  // tab strip, reveal the whole item after selection without moving the
  // page itself. Shared by both #ap-review-tabs and #ap-face-tabs -- same
  // strip-of-scrollable-buttons shape, just a different item selector.
  function revealScrollingTab(strip, item, itemSelector) {
    if (!strip || !item) return;
    requestAnimationFrame(() => {
      const stripRect = strip.getBoundingClientRect();
      const itemRect = item.getBoundingClientRect();
      const edgePadding = 4;
      const visibleItems = [...strip.querySelectorAll(itemSelector)];
      const itemIndex = visibleItems.indexOf(item);
      const hasPreviousItem = itemIndex > 0;
      const hasNextItem = itemIndex >= 0 && itemIndex < visibleItems.length - 1;
      const previousItemPeek = hasPreviousItem ? 18 : edgePadding;
      const nextItemPeek = hasNextItem ? 18 : edgePadding;
      const visibleLeft = stripRect.left + previousItemPeek;
      const visibleRight = stripRect.right - nextItemPeek;
      let scrollDelta = 0;
      if (itemRect.left < visibleLeft) {
        scrollDelta = itemRect.left - visibleLeft;
      } else if (itemRect.right > visibleRight) {
        scrollDelta = itemRect.right - visibleRight;
      }
      if (Math.abs(scrollDelta) < 0.5) return;
      const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      strip.scrollBy({ left: scrollDelta, behavior: reduceMotion ? "auto" : "smooth" });
    });
  }
  function revealReviewTab(button) {
    revealScrollingTab(els.apReviewTabs, button, ".ap-review-tab:not([hidden])");
  }
  function revealFaceTab(wrap) {
    revealScrollingTab(els.apFaceTabs, wrap, ".ap-face-tab-wrap");
  }

  // Switches #ap-review-panel's active top-level tab -- wired once below to
  // the four static tab buttons (they're plain markup, not rebuilt per
  // render like the per-person avatar tabs above, so their listeners only
  // need attaching once).
  function setActiveReviewTab(tab) {
    if (activeReviewTab === tab) return;
    activeReviewTab = tab;
    applyActiveReviewTab();
  }
  [
    [els.apReviewTabRecipe, "recipe"],
    [els.apReviewTabFace, "face"],
    [els.apReviewTabStyle, "style"],
    [els.apReviewTabPrompt, "prompt"]
  ].forEach(([button, tab]) => {
    if (!button) return;
    // stopPropagation so picking a tab never also triggers the summary's
    // own native click-to-toggle; force-opening the box means picking a tab
    // always reveals its content, whether the box was already open or not
    // (same reasoning as the per-person avatar tabs' own click handler).
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (els.apReviewPanel && !els.apReviewPanel.open) els.apReviewPanel.open = true;
      setActiveReviewTab(tab);
      revealReviewTab(button);
    });
  });

  // #ap-analysis-shell and #ap-review-panel are two independent native
  // <details> (the outer wraps the inner's whole markup -- see
  // #ap-analysis-shell's own doc comment in index.html), so opening one does
  // not natively open the other. renderAnalysisPreview/renderFaceReviewCards
  // only sync them at render time; a manual click on the outer summary (e.g.
  // re-opening it on the 結果 page) is a pure native toggle with no render in
  // between, so without this listener it revealed #ap-review-panel's own
  // <summary> (the tab row, always visible, already showing whichever tab
  // renderFaceReviewCards last picked as active) while the still-closed
  // inner <details> kept that tab's actual content hidden underneath --
  // looking like an already-selected tab with nothing under it. Mirrored
  // both ways so the two always behave as one control, never drifting apart.
  if (els.apAnalysisShell && els.apReviewPanel) {
    els.apAnalysisShell.addEventListener("toggle", () => {
      if (!els.apAnalysisShell.open) {
        els.apAnalysisShell.open = true;
        return;
      }
      els.apReviewPanel.open = true;
    });
  }
  if (els.apAnalysisShell) {
    const shellSummary = els.apAnalysisShell.querySelector(":scope > summary");
    if (shellSummary) {
      const preventAnalysisShellCollapse = (event) => {
        event.preventDefault();
        event.stopPropagation();
        els.apAnalysisShell.open = true;
      };
      shellSummary.addEventListener("click", preventAnalysisShellCollapse);
      shellSummary.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          preventAnalysisShellCollapse(event);
        }
      });
    }
  }

  // Fills #ap-face-detail with whichever one person renderFaceReviewCards
  // last selected (selectedFaceIndex) -- the name heading plus the same
  // buildPersonIssueItems output a per-person card used to show directly.
  function renderFaceDetailPanel() {
    const detail = els.apFaceDetail;
    if (!detail) return;
    // Keep the Body Reshape host stable while replacing the selected person's
    // face rows.  The host starts as a placeholder inside #ap-face-detail and
    // is reattached after the list so it always appears immediately below
    // the smile row instead of returning to the photo-summary card.
    const bodyControl = els.apBodyReshapeControl;
    detail.innerHTML = "";
    const entry = faceThumbEntries[selectedFaceIndex];
    if (!entry) {
      if (bodyControl) detail.appendChild(bodyControl);
      return;
    }
    const name = document.createElement("p");
    name.className = "ap-face-detail-name";
    name.textContent = entry.label || tr("人物 {v1}", { v1: selectedFaceIndex + 1 });
    detail.appendChild(name);
    const list = document.createElement("ul");
    list.className = "step-inline-list step-inline-list--readonly";
    buildPersonIssueItems(entry).forEach((li) => list.appendChild(li));
    detail.appendChild(list);
    // New analyses render Body Reshape as this person's own issue row below
    // 笑容調整. Keep the old holistic host only for legacy top-level records,
    // so it cannot duplicate a per-person switch or prompt.
    const hasPersonBodyRow = (entry.issues || []).some((issue) => issue.category === "body_reshape");
    if (bodyControl && !hasPersonBodyRow) detail.appendChild(bodyControl);
  }

  // Builds every <li> row for one person's issue review: the not-analyzed-
  // in-closeup caveat (sentToModel !== true) and one row per blink/gaze/hair/
  // smile issue -- was #ap-face-modal's
  // fillFaceIssueList, rebuilt into a single shared list for whichever one
  // person's thumbnail was clicked; now called once per person so every
  // detected person's own review renders inline at once (renderFaceThumbnails
  // above). Flagged items get an "allow AI to fix this" toggle; unsafe smile
  // gets the same toggle off by default so the user can override the
  // warning. Changing it updates issue.allowed in place on the
  // faceThumbEntries entry itself, which
  // getFaceIssueOverrides reads back at submit time.
  function buildPersonIssueItems(entry) {
    const items = [];
    if (entry && entry.sentToModel !== true && debugUiActive) {
      // This person still has their subjects[] match's findings below (see
      // renderFaceThumbnails) -- just a caveat that they came from the model
      // reading the full photo alone, not this face's own zoomed crop.
      // Worded around the *result* (no close-up look) rather than the
      // *source* (detector cap vs. no detection at all) -- that source
      // distinction is debug-only (gated on debugUiActive, same flag
      // setDebugUi/app.js's applyDebugUiVisibility drives elsewhere in this
      // file), since a regular user has no use for an internal detector-cap
      // detail and no way to act on it.
      const note = document.createElement("li");
      note.className = "ap-face-issue-note";
      note.textContent = tr("此人物的分析結果來自整張照片的判讀，未使用特寫照片進行細節分析，準確度可能略低。");
      items.push(note);
    }
    const issues = (entry?.issues || []).filter((issue) => {
      if (!bodyReshapeEnabled) return issue.category !== "body_reshape";
      // A no-op body analysis must not leave a misleading 身形調整 row,
      // verdict, report disclosure, or switch in the person's review list.
      // Keep an actual analysis error visible so it can still be diagnosed.
      if (issue.category === "body_reshape"
        && issue.analysisStatus !== "error"
        && !issue.controllable) return false;
      return true;
    });
    if (!issues.length) {
      const li = document.createElement("li");
      li.textContent = tr("此人像沒有可顯示的分析結果。");
      items.push(li);
      return items;
    }
    issues.forEach((issue) => {
      // Sole source of truth for whether this category is globally locked --
      // read fresh every render off reviewAdvancedOptions (kept in sync by
      // syncFaceReviewAdvancedOptions, called from app.js whenever a
      // #advanced-settings-fold toggle changes or a record/photo is
      // (re)opened), not a flag baked onto `issue` itself. `issue.controllable`
      // stays purely the analyzer/manual-override verdict faceIssueEntries
      // computed -- the lock is combined with it at each use site below
      // instead, so it can never leave `issue` itself in a stale locked
      // state the way mutating `controllable` here would.
      const globallyEnabled = faceIssueGloballyEnabled(issue.category);
      const li = document.createElement("li");
      const row = document.createElement("div");
      row.className = "ap-face-issue-row";

      // Row order: toggle -> issue label -> should_fix icon -> should_fix
      // result label. Actionable rows keep the fixed toggle slot; a Body
      // Reshape error row intentionally hides that slot because there is no
      // command the user can allow or deny.
      const isManualOverride = MANUAL_OVERRIDE_CATEGORIES.includes(issue.category);
      // isReadOnlyLocked() is a separate reason from !globallyEnabled/
      // !issue.controllable below (compatibility_version.py, not the
      // 人像修正設定 fold) -- combined here since every guard below treats
      // all three identically, but left out of toggle.title/is-locked just
      // below so that tooltip keeps naming its own specific (unrelated)
      // reason instead of misattributing a version-outdated lock to it.
      const rowLocked = !globallyEnabled || !issue.controllable || isReadOnlyLocked();
      // A manual-override category (blink/gaze/hair/smile/body reshape) is only ever
      // locked when its #advanced-settings-fold toggle is off -- build_edit_
      // prompt's global veto would ignore a per-person re-enable regardless,
      // so this says so instead of the normal "仍可手動開啟", which would be
      // untrue here.
      const toggle = document.createElement("label");
      toggle.className = "ios-switch ap-face-issue-toggle";
      // An error Body Reshape row remains visible for diagnosis, but it has
      // no actionable command and therefore no per-person switch.
      toggle.hidden = issue.category === "body_reshape" && !issue.controllable;
      // Explicit class (styles.css), not relying on :has(input:disabled)
      // alone -- see that rule's own comment for why.
      toggle.classList.toggle("is-locked", !globallyEnabled);
      toggle.title = !globallyEnabled
        ? tr("此項目已在人像修正設定中關閉，需先在設定中開啟才能個別調整")
        : issue.analysisStatus === "error"
          ? tr("身形調整分析失敗，已停用；請重新分析")
        : issue.flagged
          ? tr("允許 AI 修正此項目")
          : isManualOverride
            ? tr("分析判定無需或不建議調整；仍可手動開啟")
            : tr("模型未判定此項目需要修正，無需操作");
      // Capture-phase click guard on the LABEL itself (not just input.disabled
      // below) -- a click anywhere in this label (including the track/thumb
      // spans appended below, which aren't the input) normally activates its
      // associated checkbox even though the click target isn't the input. A
      // disabled input is supposed to make the browser skip that activation
      // entirely, but capturing here and calling preventDefault/stopPropagation
      // whenever the row is locked guarantees it regardless of that native
      // behavior -- this row must be completely unclickable while locked, not
      // just visually dimmed. Same combined condition as input.disabled below.
      toggle.addEventListener("click", (event) => {
        if (rowLocked) { event.preventDefault(); event.stopPropagation(); }
      }, true);
      const input = document.createElement("input");
      input.type = "checkbox";
      input.disabled = rowLocked;
      input.checked = globallyEnabled && issue.controllable && issue.allowed;
      input.addEventListener("change", () => {
        // input.disabled (and the capture-phase click guard above) already
        // block a real user click while locked -- this is the last backstop
        // so a locked row can never end up allowed=true no matter how the
        // change event got dispatched.
        if (rowLocked) { input.checked = false; return; }
        issue.allowed = input.checked;
        issue.allowedTouchVersion = (issue.allowedTouchVersion || 0) + 1;
        refreshVerdict();
      });
      const track = document.createElement("span");
      track.className = "ios-track";
      const thumb = document.createElement("span");
      thumb.className = "ios-thumb";
      track.appendChild(thumb);
      toggle.appendChild(input);
      toggle.appendChild(track);
      row.appendChild(toggle);

      const issueLabel = document.createElement("span");
      issueLabel.className = "ap-face-issue-name";
      issueLabel.textContent = issue.label;
      row.appendChild(issueLabel);

      const icon = document.createElement("span");
      const resultLabel = document.createElement("span");
      resultLabel.className = "step-cb-label";
      const refreshVerdict = () => {
        const active = globallyEnabled && issue.controllable && issue.allowed;
        // Locked (globally disabled) is neither "done"/no-issue (green ✓)
        // nor "pending"/will-fix (red !) -- a third, muted state so it
        // doesn't misread as either while the row itself is uninteractive.
        const locked = !globallyEnabled;
        const failed = issue.analysisStatus === "error";
        icon.className = `adherence-icon ${locked ? "is-locked" : failed ? "is-pending" : active ? "is-pending" : "is-done"}`;
        icon.textContent = locked ? "–" : failed ? "!" : active ? "!" : "✓";
        resultLabel.textContent = locked
          ? tr("已於人像修正設定關閉")
          : failed
            ? tr("分析失敗")
          : !issue.flagged
            ? (isManualOverride && issue.allowed
                ? tr("已手動開啟")
                : issue.okText)
            : (issue.allowed ? issue.flagText : tr("已設定不修正"));
      };
      refreshVerdict();
      row.appendChild(icon);
      row.appendChild(resultLabel);

      // Hair and smile both remain available as explicit user transformations
      // even when the analyzer recommends preserving the captured state.
      // CSS keeps the selector inline after the verdict text while the
      // panel is wide enough for it and wraps it onto its own
      // verdict-aligned line when it isn't. Hide it whenever this person's
      // edit is off.
      let levelRow = null;
      if (LEVEL_SELECT_CATEGORIES.includes(issue.category)) {
        li.classList.add("ap-face-issue-item--with-level");
        row.classList.toggle("ap-face-issue-row--with-level", globallyEnabled && issue.allowed);
        levelRow = document.createElement("div");
        levelRow.className = "ap-face-issue-level-row";
        const select = document.createElement("select");
        select.className = "quality-select ap-face-issue-level-select";
        const choices = FACE_ISSUE_LEVEL_CHOICES[issue.category];
        choices.forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          select.appendChild(option);
        });
        select.value = issue.level || choices[0][0];
        select.disabled = !globallyEnabled || !issue.allowed || isReadOnlyLocked();
        select.addEventListener("change", () => {
          issue.level = select.value;
          issue.levelUserTouched = true;
          issue.levelTouchVersion = (issue.levelTouchVersion || 0) + 1;
          // Picking a smile level is also an explicit choice to keep this
          // person's smile adjustment enabled. If Pose originally enabled
          // the row, clearing Pose must not make the user's new level inert.
          if (issue.category === "smile_issue") {
            issue.allowed = true;
            issue.allowedTouchVersion = (issue.allowedTouchVersion || 0) + 1;
          }
        });
        input.addEventListener("change", () => {
          select.disabled = !input.checked || isReadOnlyLocked();
          levelRow.hidden = !input.checked;
          row.classList.toggle("ap-face-issue-row--with-level", input.checked);
        });
        levelRow.hidden = !globallyEnabled || !issue.allowed;
        levelRow.appendChild(select);
      }

      if (levelRow) {
        // Wrapper so the row and its selector wrap against each other alone --
        // the debug reason line below must stay out of that flex flow (see
        // .ap-face-issue-main in styles.css).
        const main = document.createElement("div");
        main.className = "ap-face-issue-main";
        main.appendChild(row);
        main.appendChild(levelRow);
        li.appendChild(main);
      } else {
        li.appendChild(row);
      }
      if (issue.category === "body_reshape" && issue.editPrompt) {
        const prompt = document.createElement("div");
        prompt.className = "ap-body-reshape-prompt ap-face-issue-prompt";
        // Historical analyses have no prompt_display; retain their old text.
        // The Editor still receives editPrompt, never this UI-only sentence.
        prompt.textContent = issue.promptDisplay || issue.editPrompt;
        li.appendChild(prompt);
      }

      if (issue.category === "body_reshape" && issue.report && debugUiActive) {
        const report = document.createElement("details");
        report.className = "ap-body-reshape-report";
        report.open = !!issue.reportExpanded;
        const toggle = document.createElement("summary");
        toggle.className = "ap-body-reshape-report-toggle";
        toggle.textContent = report.open ? tr("收合") : tr("展開");
        const content = document.createElement("div");
        content.className = "ap-body-reshape-report-text";
        content.textContent = issue.report;
        report.append(toggle, content);
        report.addEventListener("toggle", () => {
          issue.reportExpanded = report.open;
          toggle.textContent = report.open ? tr("收合") : tr("展開");
        });
        li.appendChild(report);
      }

      // The model's own reasoning is debug-only detail (same gate as the
      // box overlay's legend) -- a regular user just needs the verdict, not
      // the analysis model's internal justification for it. Body Reshape
      // uses the debug-only disclosure above, while
      // edit_prompt remains the separate sentence that can be sent to Editor.
      if (issue.category !== "body_reshape" && issue.flagged && issue.reason && debugUiActive) {
        const reason = document.createElement("div");
        reason.className = "ap-face-issue-reason";
        reason.textContent = issue.reason;
        li.appendChild(reason);
      }

      items.push(li);
    });

    return items;
  }

  // ── Analysis box overlay (#boxes-toggle-btn, debug UI) ────────────────────
  // roi_box / subject_extent_box / crop_box are normalized against the
  // ORIGINAL image, so they're only meaningful drawn over the Before view --
  // the generated image has already been cropped/reframed and the same
  // coordinates no longer point at the same content. Hence the overlay hides
  // itself whenever the canvas is showing the result.
  //
  // applied_crop_plan (set by pipeline.finish_stage from the edit client) is
  // the crop the edit model was actually given, which can differ from the
  // analysis's own crop_box: framing size close/closer rewrites it, ROI
  // protection can grow it, the aspect fit moves it, and a disabled crop_box
  // still yields a full-frame crop when a non-original output ratio is
  // requested. Drawn alongside so the gap between proposal and request is
  // visible.
  const ANALYSIS_BOX_SPECS = [
    { field: "roi_box", color: "#22c55e", label: "ROI", corner: "tl" },
    { field: "subject_extent_box", color: "#38bdf8", label: tr("主體範圍"), corner: "tr" },
    // subjects contains both people and non-person subjects. Only surface the
    // former here: the per-person diagnostics below use the same person box,
    // while product/animal/object subjects are outside this debug view's
    // purpose. `personOnly` is applied by analysisBoxList before reading box.
    { field: "subjects", color: "#6366f1", label: tr("人物主體（subjects）"), corner: "br", list: true, itemBox: true, personOnly: true },
    { field: "crop_box", color: "#f59e0b", label: tr("crop_box（分析建議）"), corner: "bl", dashed: true },
    { field: "applied_crop_plan", color: "#f43f5e", label: tr("實際送出裁切"), corner: "br" },
    // detect_face_crops (openai_client.analyze_photo) -- zero, one, or several
    // boxes, unlike every other spec above which is a single box. Same
    // normalized-against-original-image convention as roi_box, so it slots
    // into the same overlay with no separate coordinate handling.
    { field: "_face_crop_boxes", color: "#a855f7", label: tr("臉部裁切"), corner: "tl", list: true },
    // These four live nested inside each subjects[] entry (subject.blink_issue/
    // gaze_issue/hair_issue/smile_issue), not as separate top-level arrays --
    // perSubject tells analysisBoxList to walk analysis.subjects and read
    // subject[spec.field] plus subject.box/subject.label directly, rather than
    // treating analysis[spec.field] itself as a list (unlike _face_crop_boxes
    // above, whose entries already ARE plain {x,y,w,h}). box here is that
    // PERSON's own bounding box (subjects[].box), not a face crop -- drawn
    // alongside 臉部裁切 so it's visible on the original photo which analysis-
    // reported person box renderFaceThumbnails' box-overlap matching (below)
    // is actually comparing each face thumbnail against. flagField/flagWhen/
    // flagText control the per-person "worth flagging" suffix: the first
    // three use should_fix (true = needs fixing), but smile_issue has no
    // should_fix at all -- its equivalent field is adjust_safe, and there the
    // notable value is false (unsafe to adjust), the opposite polarity, hence
    // a per-spec flagWhen instead of a hardcoded should_fix check.
    { field: "blink_issue", color: "#eab308", label: tr("閉眼偵測"), corner: "tl", perSubject: true, flagField: "should_fix", flagWhen: true, flagText: tr("（需修正）") },
    { field: "gaze_issue", color: "#ec4899", label: tr("眼神對焦"), corner: "tr", perSubject: true, flagField: "should_fix", flagWhen: true, flagText: tr("（需修正）") },
    { field: "hair_issue", color: "#14b8a6", label: tr("亂髮偵測"), corner: "bl", perSubject: true, flagField: "should_fix", flagWhen: true, flagText: tr("（需修正）") },
    { field: "smile_issue", color: "#84cc16", label: tr("笑容調整"), corner: "br", perSubject: true, flagField: "adjust_safe", flagWhen: false, flagText: tr("（不建議調整）") }
  ];

  let boxesAnalysis = null;

  // Which box types (#boxes-type-menu, one checkbox per ANALYSIS_BOX_SPECS
  // entry) are currently drawn -- starts all-off, same as the old single
  // boxesVisible flag defaulted to false, so nothing appears until the user
  // actually opens the menu and picks something (renderBoxTypeMenu/
  // toggleBoxesTypeMenu in app.js). Persists across renders/photos on
  // purpose (a debug preference, not per-result state), same lifetime as
  // debugUiActive below.
  const boxTypeVisibility = Object.fromEntries(ANALYSIS_BOX_SPECS.map((spec) => [spec.field, false]));

  function boxEntryFromRect(spec, label, box) {
    const x = Number(box.x);
    const y = Number(box.y);
    const w = Number(box.w);
    const h = Number(box.h);
    if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;
    // specLabel keeps the plain category name (e.g. "閉眼偵測") around even
    // though `label` below overrides it with a per-person one for itemBox
    // specs (subject_label + should_fix) -- the legend collapses those back
    // down to one row per category and needs the un-suffixed version.
    return { ...spec, label, specLabel: spec.label, x, y, w, h, ratio: box.aspect_ratio || box.aspectRatio || "" };
  }

  function analysisBoxList(analysis) {
    if (!analysis) return [];
    return ANALYSIS_BOX_SPECS.flatMap((spec) => {
      if (!boxTypeVisibility[spec.field]) return [];
      if (spec.perSubject) {
        const subjects = Array.isArray(analysis.subjects) ? analysis.subjects : [];
        return subjects
          .map((subject) => {
            const item = subject && subject[spec.field];
            const box = subject && subject.box;
            if (!item || typeof item !== "object" || !box) return null;
            const flagged = spec.flagField && item[spec.flagField] === spec.flagWhen;
            const subjectLabel = subject.label;
            const label = `${spec.label}${subjectLabel ? ` · ${subjectLabel}` : ""}${flagged ? spec.flagText : ""}`;
            return boxEntryFromRect(spec, label, box);
          })
          .filter(Boolean);
      }
      if (spec.list) {
        const items = Array.isArray(analysis[spec.field]) ? analysis[spec.field] : [];
        return items
          .map((item, i) => {
            if (spec.personOnly && item?.is_person !== true) return null;
            const box = spec.itemBox ? item?.box : item;
            if (!box) return null;
            const flagged = spec.flagField && item[spec.flagField] === spec.flagWhen;
            const subjectLabel = spec.field === "subjects" ? item.label : item.subject_label;
            const label = spec.itemBox
              ? `${spec.label}${subjectLabel ? ` · ${subjectLabel}` : ""}${flagged ? spec.flagText : ""}`
              : (items.length > 1 ? `${spec.label} ${i + 1}` : spec.label);
            return boxEntryFromRect(spec, label, box);
          })
          .filter(Boolean);
      }
      const box = analysis[spec.field];
      if (!box) return [];
      // Same box, different promise: after a preview it is what WILL be sent.
      const label = (spec.field === "applied_crop_plan" && analysis._aspect_ratio_preview
        && analysis._aspect_ratio_preview !== analysis._aspect_ratio_pref_at_edit)
        ? tr("將送出裁切（預覽）") : spec.label;
      // crop_box carries its own on/off switch; an analysis that recommends no
      // reframe still ships the field with enabled:false.
      if (spec.field === "crop_box" && box.enabled !== true) return [];
      const entry = boxEntryFromRect(spec, label, box);
      return entry ? [entry] : [];
    });
  }

  function drawAnalysisBoxes() {
    const overlay = els.analysisBoxes;
    const legend = els.analysisBoxesLegend;
    if (!overlay) return;

    const showingGenerated = !!els.compareCard?.classList.contains("showing-generated");
    const boxes = debugUiActive && !showingGenerated
      ? analysisBoxList(boxesAnalysis)
      : [];
    overlay.hidden = boxes.length === 0;
    if (legend) legend.hidden = boxes.length === 0;
    if (!boxes.length) {
      overlay.innerHTML = "";
      if (legend) legend.innerHTML = "";
      return;
    }

    // The photo is centered inside a fixed-height .photo-frame at its own
    // aspect ratio (no object-fit stretch), so the overlay has to be sized to
    // the image's rendered rect rather than the frame's -- then every box is
    // just a percentage of that rect.
    const frame = els.originalImg?.parentElement;
    const frameRect = frame?.getBoundingClientRect();
    const imgRect = els.originalImg?.getBoundingClientRect();
    if (!frameRect || !imgRect || !imgRect.width || !imgRect.height) return;
    overlay.style.left = `${imgRect.left - frameRect.left}px`;
    overlay.style.top = `${imgRect.top - frameRect.top}px`;
    overlay.style.width = `${imgRect.width}px`;
    overlay.style.height = `${imgRect.height}px`;

    overlay.innerHTML = "";
    boxes.forEach((box) => {
      const el = document.createElement("div");
      el.className = `ab-box${box.dashed ? " is-dashed" : ""}`;
      el.style.borderColor = box.color;
      el.style.left = `${box.x * 100}%`;
      el.style.top = `${box.y * 100}%`;
      el.style.width = `${box.w * 100}%`;
      el.style.height = `${box.h * 100}%`;
      const tag = document.createElement("span");
      tag.className = `ab-box-label ab-${box.corner}`;
      tag.style.background = box.color;
      tag.textContent = box.label;
      el.appendChild(tag);
      overlay.appendChild(el);
    });

    if (!legend) return;
    legend.innerHTML = "";
    // itemBox categories (subjects) and perSubject categories (blink_issue/
    // gaze_issue/hair_issue/smile_issue) can draw one box per detected
    // person -- the legend just needs to say "this color is 閉眼偵測", not
    // repeat that line once per person, so only the first box seen per such
    // field gets a legend row.
    const seenItemBoxFields = new Set();
    boxes.forEach((box) => {
      if (box.itemBox || box.perSubject) {
        if (seenItemBoxFields.has(box.field)) return;
        seenItemBoxFields.add(box.field);
      }
      const row = document.createElement("div");
      row.className = "ab-legend-item";
      const swatch = document.createElement("span");
      swatch.className = `ab-swatch${box.dashed ? " is-dashed" : ""}`;
      swatch.style.borderColor = box.color;
      const text = document.createElement("span");
      // itemBox entries carry that PERSON's box, not a crop/ROI decision --
      // the coordinates aren't something a reviewer needs to read off, unlike
      // roi_box/crop_box/applied_crop_plan where the exact numbers are the
      // point, and specLabel (not the per-person label) keeps this row a
      // plain category name instead of one specific person's verdict.
      const label = box.itemBox ? box.specLabel : box.label;
      const coords = box.itemBox
        ? ""
        : tr("　x {v1} · y {v2} · w {v3} · h {v4}", { v1: box.x.toFixed(3), v2: box.y.toFixed(3), v3: box.w.toFixed(3), v4: box.h.toFixed(3) });
      text.textContent = `${label}${coords}${box.ratio ? `　(${box.ratio})` : ""}`;
      row.append(swatch, text);
      legend.appendChild(row);
    });
  }

  // Fills #boxes-type-list with one checkbox row per ANALYSIS_BOX_SPECS
  // entry -- called once at startup (app.js), not per-render, since the spec
  // list is static and boxTypeVisibility already persists on its own.
  function renderBoxTypeMenu() {
    const list = els.boxesTypeList;
    if (!list) return;
    list.innerHTML = "";
    ANALYSIS_BOX_SPECS.forEach((spec) => {
      const li = document.createElement("li");
      const label = document.createElement("label");
      label.className = "box-type-row";

      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = boxTypeVisibility[spec.field];
      input.addEventListener("change", () => {
        boxTypeVisibility[spec.field] = input.checked;
        drawAnalysisBoxes();
      });

      const swatch = document.createElement("span");
      swatch.className = `box-type-swatch${spec.dashed ? " is-dashed" : ""}`;
      swatch.style.borderColor = spec.color;

      const text = document.createElement("span");
      text.textContent = spec.label;

      label.append(input, swatch, text);
      li.appendChild(label);
      list.appendChild(li);
    });
  }

  // Before/After now share one canvas, switched via #compare-toggle-btn --
  // reset to showing the result each time a (re)render brings in a fresh
  // image, so switching photos/runs doesn't leave a stale "original" view up.
  function setCompareView(showGenerated) {
    // #recipe-tags (and with it the 個人化 pill this panel belongs to) is
    // hidden while the Original is showing -- leave the panel up and it
    // floats over the photo with nothing that opened it in sight.
    if (!showGenerated) closePersonalizePopover();
    if (els.compareCard) els.compareCard.classList.toggle("showing-generated", showGenerated);
    const label = showGenerated ? tr("查看原始照片") : tr("查看輸出結果");
    if (els.compareToggleBtn) els.compareToggleBtn.setAttribute("aria-label", label);
    if (els.compareHint) els.compareHint.textContent = showGenerated ? "After" : "Before";
    drawAnalysisBoxes();
  }

  // Remembered so previewAspectRatio can re-render this same result under a
  // different aspect-ratio choice without the caller having to hand it all
  // back. keepView then skips the parts that would fight the user: resetting
  // Before/After and re-assigning the image src.
  let lastRender = null;

  // pending: analyze stage just finished, edit stage hasn't run yet -- no
  // generated image/QA data exists. Renders every analysis-only field the
  // same way a finished result would, but skips the fields that only make
  // sense once there's an actual edited image (generatedImg/downloadLink
  // src, forcing the "After" view, model name).
  function renderResult({ data, analysis, preset, imageModel, hasOriginal = true }, { keepView = false, pending = false } = {}) {
    if (!keepView) lastRender = { data, analysis, preset, imageModel, hasOriginal };
    // Keep the review switch and its Analyze -> Editor sentence in sync with
    // whichever result/run is currently being displayed.  `keepView` is only
    // a photo-view optimization; the analysis object may still be new.
    setBodyReshapeReviewAnalysis(analysis, { resetState: false });
    const qa = data.qualityCheck || null;
    const validation = analysis.crop_validation || null;
    // previewAspectRatio re-rendered this result under an aspect-ratio choice
    // the photo on screen was NOT made with -- so the ratio, the crop and the
    // lines quoting them describe the next run, and have to say so.
    const ratioIsPreview = !!analysis._aspect_ratio_preview
      && analysis._aspect_ratio_preview !== analysis._aspect_ratio_pref_at_edit;

    // hasOriginal is false only for historical playback of a job with no
    // original photo to compare against (see viewHistoryRecord, app.js) --
    // every live flow always has one, so this defaults to true and every
    // existing caller is unaffected. Reset unconditionally (not just when
    // false) so a later live render doesn't inherit a hidden state left over
    // from an earlier historical one.
    // pending: no generated image exists yet, so there's nothing to toggle
    // "before/after" between -- hide the toggle. Fullscreen just enlarges
    // whichever photo is currently shown (the original, in pending mode) and
    // has no such dependency, so it stays available.
    if (els.compareToggleBtn) els.compareToggleBtn.hidden = pending || !hasOriginal;
    if (els.compareFullscreenBtn) els.compareFullscreenBtn.hidden = !hasOriginal;

    // Keep whatever the overlay toggle was set to, but bind it to this run's
    // analysis -- switching RUN tabs should redraw the boxes for the run now
    // on screen, not leave the previous one's up.
    boxesAnalysis = analysis;
    if (pending) {
      // No generated image to show yet -- pin the compare-card to the
      // original photo instead of forcing an "After" view with nothing in it.
      setCompareView(false);
    } else if (!keepView) {
      setCompareView(true);
      els.generatedImg.src = data.image;
      els.downloadLink.href = data.image;
    } else {
      drawAnalysisBoxes();
    }
    els.modelName.textContent = pending ? "" : (data.model || imageModel);
    if (els.recipeTags) {
      // A fresh render means a different run/record -- whatever request the
      // panel was quoting is no longer the one on screen.
      closePersonalizePopover();
      const personalization = getPersonalizationText(analysis);
      const portraitEntries = buildPortraitInspectionEntries(analysis, pending)
        .filter((entry) => entry.thumbnailBox);
      const recipeItems = buildExecutedRecipeItems(analysis, pending);
      const styleItems = buildExecutedStyleOptionTagLabels(analysis.style_options, analysis, pending);
      renderTags(els.recipeTags, [
        debugUiActive ? tr("{v1}{v2}", { v1: buildAspectRatioTagLabel(analysis), v2: ratioIsPreview ? "（預覽）" : "" }) : ""
      ]);
      const addControl = (label, tabId, content) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "portrait-check-toggle result-detail-toggle recipe-tag-personalize";
        button.title = label;
        button.setAttribute("aria-label", label);
        button.setAttribute("aria-expanded", "false");
        button.setAttribute("aria-controls", "personalize-popover");
        const icon = document.querySelector(`#${tabId} svg`);
        if (icon) {
          const copy = icon.cloneNode(true);
          copy.setAttribute("aria-hidden", "true");
          button.appendChild(copy);
        }
        const caption = document.createElement("b");
        caption.className = "result-detail-label";
        caption.textContent = label;
        button.appendChild(caption);
        button.addEventListener("click", (event) => {
          event.preventDefault();
          if (personalizeAnchor === button && !els.personalizePopover.hidden) closePersonalizePopover();
          else openPersonalizePopover(
            tabId === "ap-review-tab-face" ? buildPortraitAdjustmentList(content) : content,
            button, tabId === "ap-review-tab-prompt" ? tr("個人化需求") : label);
        });
        els.recipeTags.appendChild(button);
      };
      if (!pending && portraitEntries.length) addControl(tr("人像調整"), "ap-review-tab-face", portraitEntries);
      if (recipeItems.length) addControl(tr("畫面調整"), "ap-review-tab-recipe", recipeItems);
      if (styleItems.length) addControl(tr("美化風格"), "ap-review-tab-style", styleItems);
      if (!pending && personalization) addControl(tr("個人化"), "ap-review-tab-prompt", personalization);
      fitRecipeTagsRow(els.recipeTags);
    }
    els.resultSummary.textContent = analysis.photo_summary || tr("已完成影像分析與輸出。");
    els.qaScore.textContent = Number.isFinite(Number(qa?.score)) ? `${qa.score}/10` : "-";
    if (els.recipeVerdictScore) {
      const recipeScore = qa?.recipe_adherence?.score;
      els.recipeVerdictScore.textContent = Number.isFinite(Number(recipeScore)) ? `${recipeScore}/10` : "-";
    }

    els.subjectText.textContent = buildPhotoContentSummary(analysis);
    renderTags(els.subjectTags, [
      analysis.subject_category,
      analysis.scene_category
    ]);

    // #r-intent is now a <ul> — build list items from intent data
    const userInstruction = analysis.user_instruction || {};
    const intentItems = userInstruction.has_user_direction
      ? [
          userInstruction.user_intent_summary ? tr("使用者目標：{user_intent_summary}。", { user_intent_summary: userInstruction.user_intent_summary }) : "",
          userInstruction.normalized_user_direction ? tr("執行方向：{normalized_user_direction}。", { normalized_user_direction: userInstruction.normalized_user_direction }) : "",
          userInstruction.warning ? tr("提醒：{warning}", { warning: userInstruction.warning }) : ""
        ].filter(Boolean)
      : [analysis.edit_intent || preset.intent || tr("保留原照並提升質感。")];
    fillList(document.querySelector("#r-intent"), intentItems, [tr("保留原照並提升質感。")]);

    document.querySelector("#r-status").textContent = formatStatus(qa);

    // main_subject/scene_type are already surfaced by buildPhotoContentSummary
    // just above (els.subjectText, "照片總覽") -- repeating them here as well
    // read as the same "主體：.../場景：..." sentence printed twice in a row.
    els.diagnosisText.textContent = analysis.aesthetic_diagnosis
      || tr("照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。");

    els.philosophyText.textContent = joinParts([
      userInstruction.has_user_direction
        ? tr("優先遵守使用者目標：{v1}。", { v1: userInstruction.normalized_user_direction || userInstruction.user_intent_summary })
        : "",
      analysis.edit_philosophy || tr("以保留原照內容為前提，讓照片看起來更乾淨、更有完成度。"),
      analysis.scene_preservation_strategy ? tr("場景保留：{scene_preservation_strategy}", { scene_preservation_strategy: analysis.scene_preservation_strategy }) : ""
    ]);

    els.moodText.textContent = joinParts([
      qa?.score_reason || qa?.summary || tr("尚未執行輸出品質檢查。"),
      qa?.recommendation || ""
    ]);

    const bodyPrompt = bodyReshapePromptForDisplay(analysis);
    els.compositionText.textContent = joinParts([
      analysis.composition_strategy ? sentence(tr("原圖觀察"), analysis.composition_strategy) : "",
      analysis.composition_intent ? sentence(tr("建議構圖"), analysis.composition_intent) : "",
      analysis.framing_intent ? sentence(tr("建議取景"), analysis.framing_intent) : "",
      analysis.recommended_aspect_ratio
        ? tr("{what}：{ratio}", { what: ratioIsPreview ? tr("下次輸出比例（預覽）") : tr("輸出比例"), ratio: aspectRatioDisplayValue(analysis.recommended_aspect_ratio) }) + (!ratioIsPreview && analysis.aspect_ratio_reason ? tr("，") + stripFieldLabel(analysis.aspect_ratio_reason, [tr("比例"), tr("建議比例"), tr("比例理由")]) : "") + tr("。")
        : "",
      analysis.layout_method ? sentence(tr("布局方法"), analysis.layout_method) : "",
      bodyPrompt ? tr("身形調整：{bodyPrompt}", { bodyPrompt }) : ""
    ]);

    els.roiText.textContent = joinParts([
      analysis.visual_balance_strategy ? sentence(tr("平衡"), analysis.visual_balance_strategy) : "",
      analysis.empty_space_strategy ? sentence(tr("留白"), analysis.empty_space_strategy) : "",
      analysis.alignment_strategy ? sentence(tr("水平/透視"), analysis.alignment_strategy) : ""
    ]);

    els.toolText.textContent = joinParts([
      analysis.roi_anchor || tr("ROI 是主體最重要、不可被裁掉的視覺區域。"),
      analysis.roi_box ? tr("{v1}。", { v1: formatBox("ROI box", analysis.roi_box) }) : "",
      analysis.subject_extent_box ? tr("{v1}。", { v1: formatBox(tr("主體範圍"), analysis.subject_extent_box) }) : "",
      Array.isArray(analysis.protected_regions) && analysis.protected_regions.length
        ? tr("保護區域：{v1}。", { v1: analysis.protected_regions.slice(0, 5).map((r) => r.label || r.reason || tr("重要區域")).join(tr("、")) })
        : "",
      pending ? tr("尚未執行美化，確認後將開始 AI 影像美化。") : (data.localOnly ? tr("目前使用本機處理，未執行 AI 影像生成。") : tr("目前已執行 AI 影像美化。"))
    ]);

    els.lightColorText.textContent = joinParts([
      analysis.lighting_strategy || tr("改善亮度層次與主體分離，避免過曝、死黑或平面化。"),
      analysis.memory_color_strategy || tr("維持膚色、天空、綠色、食物與材質的可信記憶色。")
    ]);

    if (els.styleOptionsText) {
      const styleOptionsText = buildStyleOptionsText(analysis.style_options);
      els.styleOptionsText.hidden = !styleOptionsText;
      els.styleOptionsText.textContent = styleOptionsText ? tr("使用者風格設定：{styleOptionsText}", { styleOptionsText }) : "";
    }

    const cropBox = resolveCropBoxForDisplay(
      analysis.crop_box,
      analysis,
      els.originalImg.naturalWidth,
      els.originalImg.naturalHeight
    );
    const cropApplied = cropBox
      ? `${ratioIsPreview ? tr("下次裁切（預覽）") : tr("已套用裁切")}${cropBox.aspectRatio ? tr("，比例 {ratio}", { ratio: cropBox.aspectRatio }) : ""}${tr("：")}x:${cropBox.x.toFixed(2)} y:${cropBox.y.toFixed(2)} w:${cropBox.w.toFixed(2)} h:${cropBox.h.toFixed(2)}${tr("。")}`
      : tr("保留原始構圖。");
    const validationText = validation
      ? tr("AI 最終選擇：{v1}。{v2}", { v1: validation.selected_candidate_label || validation.selected_candidate_id || tr("最佳候選"), v2: validation.reason || "" })
      : "";
    els.cropText.textContent = joinParts([
      analysis.recommended_aspect_ratio
        ? tr("{what}：{ratio}", { what: ratioIsPreview ? tr("下次輸出比例（預覽）") : tr("輸出比例"), ratio: aspectRatioDisplayValue(analysis.recommended_aspect_ratio) }) + (!ratioIsPreview && analysis.aspect_ratio_reason ? tr("（{reason}）", { reason: analysis.aspect_ratio_reason }) : "") + tr("。")
        : "",
      analysis.crop_recommendation || tr("未提供裁切建議。"),
      cropApplied,
      validationText
    ]);

    els.qualityText.textContent = qa
      ? joinParts([
          (qa.overall_status || (qa.pass ? tr("可展示") : tr("需複查"))) + (Number.isFinite(Number(qa.score)) ? tr("，分數 {score}/10", { score: qa.score }) : "") + tr("。"),
          qa.summary || "",
          Array.isArray(qa.warnings) && qa.warnings.length ? tr("提醒：{v1}", { v1: qa.warnings.join(tr("、")) }) : "",
          qa.recommendation || ""
        ])
      : tr("尚未執行輸出品質檢查。");

    fillList(els.issuesList, (analysis.detected_issues || []).filter(Boolean), [
      tr("光線或對比仍可更有層次。"),
      tr("色彩關係可以更穩定。"),
      tr("背景或邊緣干擾可再降低。")
    ]);

    const candidates = Array.isArray(analysis.crop_candidates) ? analysis.crop_candidates.slice(0, 5) : [];
    fillList(
      els.cropCandidatesList,
      candidates.map((c) => formatCandidate(c, validation)),
      [validationText || cropApplied]
    );

    fillList(els.recipeList, analysis.retouch_recipe || [], [
      tr("先校正曝光與白平衡。"),
      tr("調整局部對比與主體清晰度。"),
      tr("控制背景干擾並保留自然質感。")
    ]);

    const recipeAdherence = qa?.recipe_adherence || null;
    if (els.recipeScoreText) {
      els.recipeScoreText.textContent = recipeAdherence && Number.isFinite(Number(recipeAdherence.score))
        ? joinParts([tr("畫面建議達成度：{score}/10。", { score: recipeAdherence.score }), recipeAdherence.reason || ""])
        : tr("尚未取得畫面建議達成度。");
    }
    fillRecipeAdherenceList(els.recipeScoreList, recipeAdherence?.items, recipeAdherence?.style_items, recipeAdherence?.rejected_items, analysis.retouch_recipe, analysis.style_options);

    fillList(els.qaList, [
      ...checksToList(qa),
      ...(Array.isArray(qa?.warnings) ? qa.warnings.map((w) => tr("提醒：{w}", { w })) : [])
    ], [tr("尚未取得 QA 檢查結果。")]);
    fillList(els.correctionList, analysis.correction_plan, [
      tr("套用必要裁切或保留原構圖。"),
      tr("提升整體光線與色彩完成度。"),
      tr("讓主體更清楚但不過度銳化。")
    ]);
    fillList(els.preserveList, analysis.preserve_rules, [
      tr("保留主體、場景與原始拍攝角度。"),
      tr("不新增文字、標誌、人物或不相關物件。"),
      tr("避免塑膠感、重濾鏡與不自然改造。")
    ]);

    renderPrompts(analysis.prompts || {});
  }

  function renderPrompts(prompts) {
    const setPrompt = (el, text) => {
      if (el) el.textContent = text || tr("（此步驟未呼叫 GPT）");
    };
    setPrompt(els.promptAnalysis, prompts.photo_analysis);
    setPrompt(els.promptReview, prompts.user_instruction_review);
    setPrompt(els.promptCrop, prompts.crop_validation);
    const resolution = prompts.image_edit_resolution;
    setPrompt(els.promptEdit, prompts.image_edit);
    setPrompt(
      els.promptEditResolution,
      resolution ? JSON.stringify({
        prompt_strategy: prompts.image_edit_strategy,
        ...resolution,
      }, null, 2) : null
    );
    setPrompt(els.promptGazeRepair, prompts.gaze_repair_pass);
    setPrompt(els.promptQa, prompts.quality_check);
  }

  // Shown mid-processing, right after analyze_stage finishes and before the
  // user clicks "continue" -- a lightweight subset of renderResult's fields
  // (no image/QA yet, those only exist after finish_stage runs).
  // expanded controls whether #ap-review-panel (the merged 修圖建議/人像檢查
  // <details>) starts open or collapsed to just its summary line. Result-page
  // views and the first-time 分析 review both pass true so the analysis is
  // immediately visible; reviewExpanded is stored at module level so
  // renderFaceReviewCards, called both from here and later re-renders (e.g.
  // applyBatchReviewSelections), can read the same setting without it being
  // re-threaded through every call.
  function renderAnalysisPreview({ analysis, preset, expanded = true }) {
    // This is a full render for a newly selected photo/RUN. The overview's
    // disclosure state is view-local, so it must not carry an expanded card
    // (or that card's scroll position) over from the previously shown image.
    photoSummaryExpanded = false;
    if (els.apOptionsCardInline) els.apOptionsCardInline.scrollTop = 0;
    setBodyReshapeReviewAnalysis(analysis, { resetState: true });
    reviewExpanded = expanded;
    // A fresh analysis always lands back on the 人像檢查 tab first (falls
    // back to 修圖建議 in renderFaceReviewCards if this photo has no faces),
    // same as selectedFaceIndex resetting to 0 in renderFaceThumbnails below.
    activeReviewTab = "face";
    // A 「只照需求改」 case has only the stub analysis: nothing to summarize
    // or recommend, so #ap-skip-hint stands in for the 照片總覽 card (its
    // 幫我分析 is app.js's, beside 美化), the
    // empty 畫面建議 tab goes, and the panel opens on 個人化需求 -- the
    // part that still applies. 風格調整 stays: the picked style is sent after
    // the user's request. (人像檢查 hides itself: the stub has no faces.)
    const skipped = Boolean(analysis?._skipped_analysis);
    if (els.apSkipHint) els.apSkipHint.hidden = !skipped;
    if (els.apPhotoSummary) els.apPhotoSummary.hidden = skipped;
    if (els.apReviewTabRecipe) els.apReviewTabRecipe.hidden = skipped;
    if (els.apReviewTabStyle) els.apReviewTabStyle.hidden = false;
    // The shell's heading speaks of AI suggestions this case doesn't have.
    if (els.apAnalysisShellTitleText) {
      els.apAnalysisShellTitleText.textContent = skipped ? tr("需求與調整") : tr("分析與調整");
    }
    if (els.apAnalysisShellDesc) {
      els.apAnalysisShellDesc.textContent = skipped
        ? tr("這個版本依照你的個人化需求修圖，可以在這裡修改需求、調整風格或設定")
        : tr("這是 AI 分析後提供的建議，你可依此在這裡進一步調整設定");
    }
    if (skipped) activeReviewTab = "prompt";
    // #ap-analysis-shell (the outer title+collapse frame) mirrors
    // #ap-review-panel's own open state -- both are opened by default for
    // the first-time 分析 review and the 結果 page/a revise.
    if (els.apAnalysisShell) els.apAnalysisShell.open = true;
    if (els.apReviewPanel) els.apReviewPanel.open = expanded;
    if (els.apSubject) els.apSubject.textContent = buildPhotoContentSummary(analysis);
    if (els.apSubjectTags) {
      renderTags(els.apSubjectTags, [
        analysis.subject_category,
        analysis.scene_category
      ]);
    }
    if (els.apPhotoSummaryTags) {
      renderTags(els.apPhotoSummaryTags, [analysis.subject_category, analysis.scene_category]);
    }
    if (els.apPhotoSummaryText) {
      // Keep the model-provided summary intact. This display-only selector
      // only removes surrounding whitespace so it never rewrites the text
      // or derives a sentence from another analysis field.
      const summary = typeof analysis?.photo_summary === "string"
        ? analysis.photo_summary.trim()
        : "";
      els.apPhotoSummaryText.textContent = summary;
    }
    renderPhotoHighlights(analysis);
    currentValidIssues = getValidIssues(analysis);
    renderPhotoIssues();
    syncPhotoSummaryCompactState();

    // main_subject/scene_type are already surfaced by buildPhotoContentSummary
    // just above (els.apSubject, "照片總覽") -- repeating them here as well
    // read as the same "主體：.../場景：..." sentence printed twice in a row.
    if (els.apDiagnosis) {
      els.apDiagnosis.textContent = analysis.aesthetic_diagnosis
        || tr("照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。");
    }

    if (els.apComposition) {
      const userInstruction = analysis.user_instruction || {};
      const bodyPrompt = bodyReshapePromptForDisplay(analysis);
      els.apComposition.textContent = joinParts([
        userInstruction.has_user_direction
          ? tr("使用者目標：{v1}。", { v1: userInstruction.user_intent_summary || userInstruction.normalized_user_direction || "" })
          : (analysis.edit_intent || preset?.intent || ""),
        analysis.composition_intent ? sentence(tr("建議構圖"), analysis.composition_intent) : "",
        bodyPrompt ? tr("身形調整：{bodyPrompt}", { bodyPrompt }) : ""
      ]);
    }

    fillCheckList(els.apRecipeList, analysis.retouch_recipe, [tr("尚未取得畫面建議。")]);
    renderFaceThumbnails(analysis);

    // Rebuild the style-option groups each run (defaults to "Default
    // (analyzed)"), and re-evaluate which apply. Pose only makes sense for a
    // person subject; Angle applies to any subject (product/scene shots
    // benefit from camera-angle framing too). The actual retouch behavior
    // for these selections is not wired up yet -- this is UI only for now.
    // #ap-style-modal's full-size cards hold the radios that own the pick;
    // the tab's own four cards are rendered from the same option lists by
    // syncStylePicks.
    fillStyleOptionList(els.apPoseList, POSE_OPTIONS, "ap-pose", "default", "pose");
    fillStyleOptionList(els.apAngleList, ANGLE_OPTIONS, "ap-angle", "default", "angle");
    fillStyleOptionList(els.apLightingList, LIGHTING_OPTIONS, "ap-lighting", "default", "lighting");
    fillStyleOptionList(els.apSizeList, SIZE_OPTIONS, "ap-size", "default", "size");
    syncStylePicks();
    // Mirrors prompts._has_person, so these show whenever the edit applies portrait work.
    const isPerson = analysis.subject_category === "人物"
      || (analysis.subjects || []).some((subject) => Boolean(subject?.is_person));
    if (els.apStyleCardPose) els.apStyleCardPose.hidden = !isPerson;
    if (els.apAdvancedSettingsFold) {
      els.apAdvancedSettingsFold.hidden = !isPerson;
      if (!isPerson) els.apAdvancedSettingsFold.open = false;
    }
  }

  // Narrow re-fill of just #ap-recipe-list -- for when the panel is already
  // open and only the recipe items need to change (a reframe swaps in a new
  // composition item), without re-running the rest of
  // renderAnalysisPreview and disturbing whatever Pose/Angle/Lighting/Size
  // pick the user already made.
  function refreshRecipeList(analysis) {
    fillCheckList(els.apRecipeList, analysis.retouch_recipe, [tr("尚未取得畫面建議。")]);
  }

  return {
    renderResult, renderAnalysisPreview, refreshRecipeList,
    syncStylePicks, clearStylePick, setCompareView, renderTags,
    closePersonalizePopover,
    renderBoxTypeMenu, drawAnalysisBoxes, setDebugUi, previewAspectRatio, syncReframingRatioLock,
    restoreCompositionState, applyCompositionToRender,
    getFaceIssueOverrides, applyFaceIssueOverrides, setFaceIssueCategoryLock, resetFaceReviewState,
    buildPlannedEditMessages,
    setBodyReshapeEnabled, getBodyReshapeReviewEnabled, setBodyReshapeReviewEnabled,
    renderFaceReviewCards,
    syncFaceReviewAdvancedOptions, syncFaceReviewPoseSuggestion
  };
})();
