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

  const DEFAULT_OPTION = { value: "default", label: "不指定" };
  const POSE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "playful", label: "俏皮" },
    { value: "candid", label: "自然抓拍" },
    { value: "strong", label: "強勢" },
    { value: "elegant", label: "優雅" },
    { value: "dynamic", label: "動感" },
    { value: "cool", label: "酷帥" },
    { value: "vsign", label: "勝利手勢" },
    { value: "cheer", label: "歡呼" }
  ];
  const ANGLE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "high", label: "俯拍" },
    { value: "low", label: "仰拍" },
    { value: "eye", label: "平視" }
  ];
  const LIGHTING_OPTIONS = [
    DEFAULT_OPTION,
    { value: "gold", label: "金色光調" },
    { value: "soft", label: "柔光" }
  ];
  const SIZE_OPTIONS = [
    DEFAULT_OPTION,
    { value: "wide", label: "遠景" },
    { value: "wider", label: "廣角" },
    { value: "close", label: "近景" },
    { value: "closer", label: "特寫" }
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
    lighting: { gold: "lighting-goldtone.png", soft: "lighting-soft.png" },
    size: { wide: "size-wide.png", wider: "size-wider.png", close: "size-close.png", closer: "size-closer.png" }
  };

  // category -> {options list, display label}, used to turn analysis.style_options
  // (the raw {pose, angle, lighting, size} values the user picked on the review
  // screen) back into human-readable text on the results page.
  const STYLE_OPTION_GROUPS = {
    pose: { options: POSE_OPTIONS, label: "姿勢" },
    angle: { options: ANGLE_OPTIONS, label: "拍攝角度" },
    lighting: { options: LIGHTING_OPTIONS, label: "光線" },
    size: { options: SIZE_OPTIONS, label: "取景範圍" }
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

  // Same non-default-only filtering as buildStyleOptionsText, but as plain
  // option labels ("Playful", "Close") for the floating recipe-tags pills on
  // the result photo itself -- no category prefix, since the pills sit
  // directly on the image rather than in a labeled text field.
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

  // Set by applyDebugUiVisibility (app.js). Both inspection aids on the result
  // canvas -- the ratio pill below and the #analysis-boxes overlay -- are
  // testbed-only; a regular user's result photo shouldn't carry either.
  let debugUiActive = false;
  function setDebugUi(active) {
    debugUiActive = !!active;
    drawAnalysisBoxes();
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
    const prompt = analysis?.body_reshape?.edit_prompt;
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
    toggle.title = "決定是否將身形調整指令送給 Editor";
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
    name.textContent = "身形調整";
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
      verdict.textContent = active ? "可以調整" : "不執行";
      toggle.setAttribute("aria-label", active ? "關閉身形調整" : "開啟身形調整");
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
    promptLabel.textContent = "送給 Editor：";
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

  function reframingDetail(text) {
    let value = String(text || "").trim().replace(/^\s*重新構圖\s*[:：]\s*/, "");
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
    axes.forEach((entry, index) => {
      if (!axesIncludeReframing(entry) || index >= recipe.length) return;
      const detail = reframingDetail(recipe[index])
        || reframingDetail(plan[index])
        || reframingDetail(analysis.crop_box?.reason)
        || "依解析後的裁切框改善主體集中度與邊緣落點。";
      if (!cropPlan) {
        recipe[index] = "重新構圖：此比例下維持完整原圖（沒有有效裁切）";
      } else if (ratioLabel === "original") {
        recipe[index] = `重新構圖：維持原圖比例，${detail}`;
      } else {
        recipe[index] = `重新構圖：裁成${ratioLabel}，${detail}`;
      }
    });
    return { ...analysis, retouch_recipe: recipe };
  }

  function previewAspectRatio(pref) {
    if (!lastRender || !pref) return;
    const resultAnalysis = lastRender.analysis;
    const recipeInputs = Array.from(
      els.apRecipeList?.querySelectorAll('input[type="checkbox"]') || []
    );
    const recipeChecked = recipeInputs.map((input) => input.checked);
    const reframeChecked = Array.from(els.apRecipeList?.querySelectorAll("li") || []).some(
      (item) => /^重新構圖\s*[:：]/.test(item.querySelector(".step-cb-label")?.textContent || "")
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
  }

  function aspectRatioDisplayValue(value) {
    const text = String(value || "").trim();
    return text.toLowerCase() === "original" ? "原圖" : text;
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
    if (value.toLowerCase() === "original") return "比例 原圖";
    const match = value.match(/(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)/);
    if (!match) return `比例 ${value}`;
    const w = Number(match[1]);
    const h = Number(match[2]);
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return `比例 ${value}`;
    return `比例 ${value}（${w > h ? "橫" : w < h ? "直" : "方"}）`;
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

  const REFRAME_RECIPE_RE = /^重新構圖\s*[:：]/;

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
        text.appendChild(document.createTextNode(`${reframe[1]} `));
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
      // syncStyleTrigger, updateStyleTriggerLabel) all depend on it being
      // there to check/query.
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
        thumb.src = `assets/optional_style/${group}/${imageFile}`;
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
      cancelBtn.setAttribute("aria-label", `清除${opt.label}`);
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

  // Updates a style-option pill's displayed value (e.g. "Playful") to match
  // whichever radio in its list is currently checked -- called by app.js
  // after the per-image 修圖風格 tab's Pose/Angle/Lighting/Size lists restore
  // a saved pick or the user selects a new one. The type button and its
  // option grid are siblings rather than nested, so the pill is found via
  // the list's data-style-group attribute matching a .ap-style-type-btn.
  function syncStyleTrigger(listElement) {
    if (!listElement) return;
    const group = listElement.dataset.styleGroup;
    const valueEl = group && document.querySelector(`.ap-style-type-btn[data-style-group="${group}"] .ap-style-value`);
    if (!valueEl) return;
    const checked = listElement.querySelector("input:checked");
    const label = checked?.closest("label")?.querySelector(".step-cb-label, .ap-style-option-label")?.textContent;
    valueEl.textContent = label || "不指定";
  }

  // Resets one ap-pose/-angle/-lighting/-size radio group back to its
  // "default" option and fires a real `change` event on it -- app.js's
  // per-image radio change listener is delegated onto the enclosing
  // #ap-*-list <ul> (see the wireup at the bottom of app.js), so this single
  // dispatch is all that's needed to run the exact same
  // syncStyleTrigger/updateStyleTriggerLabel path a normal click on the
  // "Auto" card would -- no separate copy of that logic here.
  function clearStylePick(name) {
    const defaultInput = document.querySelector(`input[name="${name}"][value="default"]`);
    if (!defaultInput || defaultInput.checked) return;
    defaultInput.checked = true;
    defaultInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Renders #ap-style-picker-value's picked-label row as removable chips
  // (label + an "x" button) instead of plain renderTags spans -- each chip's
  // "x" calls clearStylePick so a pick can be undone from here directly,
  // without switching down into #ap-style-type-row and finding "Auto" in
  // the grid.
  function renderStylePickedChips(element, picks) {
    element.innerHTML = "";
    picks.forEach(({ name, label }) => {
      const chip = document.createElement("span");
      chip.className = "ap-style-picked-chip";

      const text = document.createElement("span");
      text.textContent = label;
      chip.appendChild(text);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "ap-style-picked-chip-remove";
      removeBtn.setAttribute("aria-label", `清除${label}`);
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", () => clearStylePick(name));
      chip.appendChild(removeBtn);

      element.appendChild(chip);
    });
  }

  // Keeps #ap-style-picker-value (the removable-chip row at the top of the
  // 修圖風格 tab body) in sync with whatever's currently picked across the 4
  // ap-pose/-angle/-lighting/-size radio groups -- called whenever those
  // radios change (app.js) or the analysis preview re-renders. Hidden
  // entirely while everything's still Default/Auto, same non-default-only
  // filtering as buildStyleOptionsText/buildStyleOptionTagLabels above.
  // Reads the radios directly (same `input[name="..."]:checked` lookup as
  // readStyleSelectionsFromDom, app.js) rather than depending on that
  // function, since results.js has no access to app.js's internals.
  // Also drives the .ap-style-type-clear "x" sitting on each Pose/Angle/
  // Lighting/Size pill in #ap-style-type-row -- same non-default check as
  // the picked-chip row, just a per-category on/off instead of building a
  // label, so the two never disagree about which categories are currently
  // overridden.
  function updateStyleTriggerLabel() {
    if (!els.apStylePickerValue) return;
    const names = ["ap-pose", "ap-angle", "ap-lighting", "ap-size"];
    const picks = [];
    names.forEach((name) => {
      const group = name.replace("ap-", "");
      const checked = document.querySelector(`input[name="${name}"]:checked`);
      const isNonDefault = !!checked && checked.value !== "default";
      const clearBtn = document.querySelector(`.ap-style-type-clear[data-style-group="${group}"]`);
      if (clearBtn) clearBtn.hidden = !isNonDefault;
      // .ap-style-type-btn.is-picked (styles.css) recolors its own
      // .ap-style-value so a category reading e.g. "Playful" instead of
      // "Default" is visually distinct at a glance, same non-default check
      // as the clear "x" above.
      const groupBtn = document.getElementById(`ap-group-${group}`);
      if (groupBtn) groupBtn.classList.toggle("is-picked", isNonDefault);
      if (!isNonDefault) return;
      const opt = STYLE_OPTION_GROUPS[group]?.options.find((o) => o.value === checked.value);
      if (opt) picks.push({ name, label: opt.label });
    });
    renderStylePickedChips(els.apStylePickerValue, picks);
    els.apStylePickerValue.hidden = picks.length === 0;
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
    tags.filter((tag) => typeof tag === "string" && tag.trim()).forEach((tag) => {
      const chip = document.createElement("span");
      chip.textContent = tag.trim();
      element.appendChild(chip);
    });
  }

  // #recipe-tags stays on one row (styles.css: flex-wrap:nowrap) no matter
  // how many/how long the picks are -- rather than truncating a cramped
  // pill's text with "...", shrink every pill's font-size in lockstep until
  // each one's full label actually fits (offsetWidth < scrollWidth is the
  // standard "this element's text is being clipped" check), so the labels
  // stay fully readable, just smaller. Resets to the base size first so a
  // later re-render with fewer/shorter tags isn't left stuck at a previous
  // shrink. -- shared recipeTagsResizeObserver (bottom of file) re-runs this
  // whenever #recipe-tags itself changes width (window resize, fullscreen
  // toggle, etc.), not just on a fresh render.
  const RECIPE_TAG_BASE_FONT_SIZE = 12;
  const RECIPE_TAG_MIN_FONT_SIZE = 8;
  function fitRecipeTagsRow(container) {
    if (!container) return;
    const chips = Array.from(container.children);
    if (!chips.length) return;
    let size = RECIPE_TAG_BASE_FONT_SIZE;
    chips.forEach((chip) => { chip.style.fontSize = `${size}px`; });
    const isClipped = () => chips.some((chip) => chip.scrollWidth > chip.clientWidth + 1);
    while (size > RECIPE_TAG_MIN_FONT_SIZE && isClipped()) {
      size -= 1;
      chips.forEach((chip) => { chip.style.fontSize = `${size}px`; });
    }
  }

  // Re-fit whenever #recipe-tags itself changes width -- window resize,
  // rotating a phone, or #compare-fullscreen-btn's toggle (app.js) all change
  // how much room the row actually has, independent of any fresh render.
  if (els.recipeTags && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => fitRecipeTagsRow(els.recipeTags)).observe(els.recipeTags);
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
      subject ? `主體：${subject}。` : "已完成照片總覽辨識。",
      scene ? `場景：${scene}。` : ""
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
        ? `${visible.length} 項值得調整`
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
  const PHOTO_SUMMARY_COMPACT_MEDIA = "(max-width: 900px), (min-width: 901px) and (max-height: 760px)";
  let photoSummaryExpanded = false;

  // Short desktop screens keep the summary readable without allowing it to
  // consume the analysis shell's entire height. The button is only exposed
  // when the natural summary actually overflows the compact height.
  function syncPhotoSummaryCompactState() {
    const summary = els.apPhotoSummary;
    const toggle = els.apPhotoSummaryToggle;
    if (!summary || !toggle) return;
    const isMobile = typeof window.matchMedia === "function"
      && window.matchMedia("(max-width: 900px)").matches;
    const shouldCompactSummary = typeof window.matchMedia === "function"
      && window.matchMedia(PHOTO_SUMMARY_COMPACT_MEDIA).matches;

    summary.style.removeProperty("--ap-photo-summary-compact-height");
    summary.classList.remove("ap-photo-summary--compact", "ap-photo-summary--expanded");
    if (!shouldCompactSummary) {
      photoSummaryExpanded = false;
      toggle.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      return;
    }

    const needsCompactToggle = summary.scrollHeight > PHOTO_SUMMARY_COMPACT_HEIGHT;
    if (!needsCompactToggle) {
      photoSummaryExpanded = false;
      toggle.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      return;
    }

    if (isMobile) {
      summary.style.setProperty(
        "--ap-photo-summary-compact-height",
        `${Math.ceil(summary.scrollHeight * PHOTO_SUMMARY_MOBILE_RATIO)}px`
      );
    }
    toggle.hidden = false;
    toggle.textContent = photoSummaryExpanded ? "收合" : "查看更多";
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
    });
  }
  if (els.apPhotoSummary && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => syncPhotoSummaryCompactState()).observe(els.apPhotoSummary);
  }
  window.addEventListener("resize", syncPhotoSummaryCompactState);

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
    { field: "blink_issue", label: "閉眼偵測", flagField: "should_fix", flagWhen: true, flagText: "需要修正", okText: "無需修正" },
    { field: "gaze_issue", label: "眼神對焦", flagField: "should_fix", flagWhen: true, flagText: "需要修正", okText: "無需修正" },
    { field: "hair_issue", label: "亂髮偵測", flagField: "should_fix", flagWhen: true, flagText: "需要修正", okText: "無需修正" },
    { field: "smile_issue", label: "笑容調整", flagField: "adjust_safe", flagWhen: true, flagText: "可以調整", okText: "不建議調整" },
    { field: "body_reshape", label: "身形調整", flagField: "should_edit", flagWhen: true, flagText: "可以調整", okText: "不執行" }
  ];

  // Every category the user can manually switch on even when the analyzer
  // itself found nothing to fix -- app.js's buildEffectiveAnalysis sets
  // issue._user_forced for the face categories when a toggle is flipped on
  // an unflagged row, and prompts.py's resolve_user_hair_request/
  // resolve_user_blink_request/resolve_user_gaze_request (plus smile's own
  // explicit-level path) turn that into a real edit instruction. Unlike hair,
  // blink/gaze have no per-person style level -- see LEVEL_SELECT_CATEGORIES
  // below -- so they don't appear in that second list.
  const MANUAL_OVERRIDE_CATEGORIES = ["blink_issue", "gaze_issue", "hair_issue", "smile_issue", "body_reshape"];
  const LEVEL_SELECT_CATEGORIES = ["hair_issue", "smile_issue"];

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
  // back through them (getFaceIssueOverrides), and buildEffectiveAnalysis (app.js)
  // uses the same pair to flip should_fix/adjust_safe before submission.
  function faceIssueEntries(subject, index, bodyDisabled = false) {
    if (!subject) return [];
    return FACE_ISSUE_CATEGORIES.map((category) => {
      const match = subject[category.field];
      if (!match || typeof match !== "object") return null;
      const isBodyReshape = category.field === "body_reshape";
      const bodyPrompt = isBodyReshape && typeof match.edit_prompt === "string"
        ? match.edit_prompt.trim()
        : "";
      // A body row is available whenever this person's original analysis JSON
      // contains an editor command.  `should_edit` is optional in older
      // records, so an omitted value keeps the historical default enabled.
      if (isBodyReshape && (subject.is_person !== true || !bodyPrompt)) return null;
      const isManualOverride = MANUAL_OVERRIDE_CATEGORIES.includes(category.field);
      // buildEffectiveAnalysis (app.js) writes _user_forced/_user_level onto
      // this same subject[category.field] object at submit time, and the
      // backend echoes analysis back verbatim -- so a completed record (or a
      // freshly re-rendered revise) carries the *previous* manual-on decision
      // right here, not just the analyzer's own should_fix/adjust_safe. Without
      // this, reopening a record with a forced fix would recompute allowed
      // straight from the untouched original verdict and silently show the
      // toggle as off again.
      const wasUserForced = !!match._user_forced;
      // The "set all" global lock (#advanced-settings-fold's toggle for this
      // category) is applied at render time in buildPersonIssueItems below,
      // via faceIssueGloballyEnabled/reviewAdvancedOptions -- not baked in
      // here. So this stays the plain per-analysis verdict.
      const flagged = isBodyReshape
        ? match.should_edit !== false
        : match[category.flagField] === category.flagWhen;
      const allowed = isBodyReshape
        ? match.should_edit !== false && !bodyDisabled
        : match[category.flagField] === category.flagWhen || wasUserForced;
      return {
        label: category.label,
        flagged,
        flagText: category.flagText,
        okText: category.okText,
        reason: isBodyReshape ? (match.report || match.reason || "") : (match.reason || ""),
        editPrompt: isBodyReshape ? bodyPrompt : "",
        category: category.field,
        subjectIndex: index,
        // Hair, smile, blink, gaze, and body reshape are all user-selectable transformations
        // even when the analyzer recommends preserving them.
        controllable: isBodyReshape || match[category.flagField] === category.flagWhen || isManualOverride,
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

  // Flattened { "subjectIndex:category": false } for every flagged issue the
  // user turned off in the face modal -- read once at submit time
  // (buildEffectiveAnalysis, app.js) rather than kept in sync continuously,
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

  function renderFaceThumbnails(analysis) {
    reviewAdvancedOptions = { ...(analysis?._advanced_options || {}) };
    facePoseSuggestionState = null;
    // A different photo/person set makes whatever tab was previously
    // selected meaningless -- always land back on the first person.
    selectedFaceIndex = 0;
    const faces = localFaceEntries(analysis);
    const bodySubjects = bodyReshapeEnabled
      ? (Array.isArray(analysis?.subjects) ? analysis.subjects : [])
        .map((subject, subjectIndex) => ({ subject, subjectIndex }))
        .filter(({ subject }) => subject?.is_person === true
          && subject.body_reshape && typeof subject.body_reshape === "object"
          && typeof subject.body_reshape.edit_prompt === "string"
          && subject.body_reshape.edit_prompt.trim())
      : [];
    const bodyDisabledSubjects = new Set(
      (Array.isArray(analysis?._disabled_body_reshape_subjects)
        ? analysis._disabled_body_reshape_subjects : [])
        .filter((index) => Number.isInteger(index) && index >= 0)
    );
    if (!faces.length && !bodySubjects.length) {
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
        return {
          box: f.box,
          issues: faceIssueEntries(subject, index, bodyDisabledSubjects.has(index)),
          subjectIndex: index,
          sentToModel,
          label: (subject && subject.label) || "",
        };
      })
      .filter(Boolean);

    // Body plans are attached to subjects[], while the face tab list is based
    // on local face detections. Add a lightweight person-box fallback so a
    // valid body command is still switchable when that person has no local
    // face crop.
    const representedSubjectIndexes = new Set(
      faceThumbEntries.map((entry) => entry.subjectIndex)
        .filter((index) => Number.isInteger(index) && index >= 0)
    );
    bodySubjects.forEach(({ subject, subjectIndex }) => {
      if (representedSubjectIndexes.has(subjectIndex)) return;
      const box = subject.box;
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
      // in words once this tab is selected).
      if (entry.sentToModel !== true) tab.classList.add("ap-face-tab--local-only");
      const label = entry.label || `人物 ${index + 1}`;
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

  // If a tab is only partly visible at the edge of the horizontal tab strip,
  // reveal the whole button after selection without moving the page itself.
  function revealReviewTab(button) {
    const tabs = els.apReviewTabs;
    if (!tabs || !button) return;
    requestAnimationFrame(() => {
      const tabsRect = tabs.getBoundingClientRect();
      const buttonRect = button.getBoundingClientRect();
      const edgePadding = 4;
      const visibleTabs = [...tabs.querySelectorAll(".ap-review-tab:not([hidden])")];
      const buttonIndex = visibleTabs.indexOf(button);
      const hasPreviousTab = buttonIndex > 0;
      const hasNextTab = buttonIndex >= 0 && buttonIndex < visibleTabs.length - 1;
      const previousTabPeek = hasPreviousTab ? 18 : edgePadding;
      const nextTabPeek = hasNextTab ? 18 : edgePadding;
      const visibleLeft = tabsRect.left + previousTabPeek;
      const visibleRight = tabsRect.right - nextTabPeek;
      let scrollDelta = 0;
      if (buttonRect.left < visibleLeft) {
        scrollDelta = buttonRect.left - visibleLeft;
      } else if (buttonRect.right > visibleRight) {
        scrollDelta = buttonRect.right - visibleRight;
      }
      if (Math.abs(scrollDelta) < 0.5) return;
      const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      tabs.scrollBy({ left: scrollDelta, behavior: reduceMotion ? "auto" : "smooth" });
    });
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
    name.textContent = entry.label || `人物 ${selectedFaceIndex + 1}`;
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
      note.textContent = "此人物的分析結果來自整張照片的判讀，未使用特寫照片進行細節分析，準確度可能略低。";
      items.push(note);
    }
    const issues = (entry?.issues || []).filter((issue) =>
      bodyReshapeEnabled || issue.category !== "body_reshape"
    );
    if (!issues.length) {
      const li = document.createElement("li");
      li.textContent = "此人像沒有可顯示的分析結果。";
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
      // result label. The toggle itself always renders now, disabled when
      // this issue isn't flagged (nothing to allow/deny) -- keeps every
      // row's own fixed 34px-wide slot regardless of flagged state, so the
      // label/icon/text after it land in the same column on every row
      // instead of an unflagged row (previously no toggle at all) sitting
      // ~44px left of a flagged one right above/below it.
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
      // Explicit class (styles.css), not relying on :has(input:disabled)
      // alone -- see that rule's own comment for why.
      toggle.classList.toggle("is-locked", !globallyEnabled);
      toggle.title = !globallyEnabled
        ? "此項目已在人像修正設定中關閉，需先在設定中開啟才能個別調整"
        : issue.flagged
          ? "允許 AI 修正此項目"
          : isManualOverride
            ? "分析判定無需或不建議調整；仍可手動開啟"
            : "模型未判定此項目需要修正，無需操作";
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
        icon.className = `adherence-icon ${locked ? "is-locked" : active ? "is-pending" : "is-done"}`;
        icon.textContent = locked ? "–" : active ? "!" : "✓";
        resultLabel.textContent = locked
          ? "已於人像修正設定關閉"
          : !issue.flagged
            ? (isManualOverride && issue.allowed
                ? `已手動開啟`
                : issue.okText)
            : (issue.allowed ? issue.flagText : "已設定不修正");
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
        const choices = issue.category === "hair_issue"
          ? [["dynamic", "保留髮絲動態"], ["tidy", "梳整俐落"]]
          : [["auto", "自動"], ["neutral", "不笑"], ["gentle", "微笑"], ["broad", "開朗笑"], ["laughing", "大笑"]];
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
        prompt.textContent = `送給 Editor：${issue.editPrompt}`;
        li.appendChild(prompt);
      }

      // The model's own reasoning is debug-only detail (same gate as the
      // box overlay's legend) -- a regular user just needs the verdict, not
      // the analysis model's internal justification for it.
      if (issue.flagged && issue.reason && debugUiActive) {
        const reason = document.createElement("div");
        reason.className = "ap-face-issue-reason";
        reason.textContent = issue.reason;
        li.appendChild(reason);
      }

      items.push(li);
    });

    if (bodyReshapeEnabled && entry?.bodyAnalysisAvailable) {
      const section = document.createElement("li");
      section.className = "ap-body-reshape-section";
      const masterRow = document.createElement("div");
      masterRow.className = "ap-face-issue-row";

      const toggle = document.createElement("label");
      toggle.className = "ios-switch ap-face-issue-toggle";
      const actionableBodyPoints = bodyKeyPoints.filter((point) => point.selectable !== false);
      toggle.title = actionableBodyPoints.length ? "允許 AI 執行這位人物的身形調整" : "沒有可執行的身形調整";
      const masterInput = document.createElement("input");
      masterInput.type = "checkbox";
      masterInput.disabled = !actionableBodyPoints.length || isReadOnlyLocked();
      masterInput.checked = actionableBodyPoints.length && entry.bodyReshapeEnabled !== false;
      const track = document.createElement("span");
      track.className = "ios-track";
      const thumb = document.createElement("span");
      thumb.className = "ios-thumb";
      track.appendChild(thumb);
      toggle.appendChild(masterInput);
      toggle.appendChild(track);
      masterRow.appendChild(toggle);

      const name = document.createElement("span");
      name.className = "ap-face-issue-name";
      name.textContent = "身形調整";
      masterRow.appendChild(name);

      const icon = document.createElement("span");
      const verdict = document.createElement("span");
      verdict.className = "step-cb-label";
      masterRow.appendChild(icon);
      masterRow.appendChild(verdict);
      section.appendChild(masterRow);

      const list = document.createElement("div");
      list.className = "ap-body-reshape-list";
      const pointControls = [];
      if (bodyKeyPoints.length) {
        bodyKeyPoints.forEach((point) => {
          const row = document.createElement("div");
          row.className = "ap-body-reshape-option";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.disabled = point.selectable === false || isReadOnlyLocked();
          input.checked = point.selectable !== false && point.allowed !== false;
          input.addEventListener("change", () => {
            point.allowed = input.checked;
            if (typeof refreshBodyReshape === "function") refreshBodyReshape();
          });
          const textLabel = document.createElement("label");
          textLabel.className = "ap-body-reshape-option-label";
          textLabel.appendChild(input);
          const text = document.createElement("span");
          text.textContent = bodyReshapeDisplayText(point.text);
          textLabel.appendChild(text);
          row.appendChild(textLabel);
          const sliderRow = document.createElement("div");
          sliderRow.className = "ap-body-reshape-slider-row";
          const sliderLabel = document.createElement("span");
          sliderLabel.className = "ap-body-reshape-slider-label";
          sliderLabel.textContent = "調整幅度";
          const strengthIndex = bodyReshapeStrengthIndex(point.targetPct);
          const selectedStrength = bodyReshapeStrengthAt(strengthIndex);
          const slider = document.createElement("input");
          slider.type = "range";
          slider.className = "ap-body-reshape-slider";
          slider.min = "0";
          slider.max = String(BODY_RESHAPE_STRENGTHS.length - 1);
          slider.step = "1";
          slider.value = String(strengthIndex);
          slider.setAttribute("aria-label", `${bodyReshapeDisplayText(point.text)} 調整幅度`);
          slider.setAttribute("aria-valuetext", `${selectedStrength.label}強度`);
          const sliderControl = document.createElement("div");
          sliderControl.className = "ap-body-reshape-slider-control";
          sliderControl.appendChild(slider);
          const strengthScale = document.createElement("div");
          strengthScale.className = "ap-body-reshape-slider-scale";
          BODY_RESHAPE_STRENGTHS.forEach((strength) => {
            const mark = document.createElement("span");
            mark.textContent = strength.label;
            strengthScale.appendChild(mark);
          });
          sliderControl.appendChild(strengthScale);
          slider.addEventListener("input", () => {
            const strength = bodyReshapeStrengthAt(slider.value);
            const nextIndex = BODY_RESHAPE_STRENGTHS.indexOf(strength);
            slider.value = String(nextIndex);
            point.targetPct = strength.targetPct;
            point.amountTouched = true;
            slider.setAttribute("aria-valuetext", `${strength.label}強度`);
          });
          sliderRow.appendChild(sliderLabel);
          sliderRow.appendChild(sliderControl);
          row.appendChild(sliderRow);
          pointControls.push({ point, input, slider, row });
          if (point.selectable === false && point.reason) {
            row.title = `此項不會送入 edit：${point.reason}`;
            row.classList.add("is-unavailable");
            const reason = document.createElement("small");
            reason.className = "ap-body-reshape-option-meta";
            reason.textContent = `不執行：${point.reason}`;
            row.appendChild(reason);
          }
          list.appendChild(row);
        });
      }
      section.appendChild(list);

      const refreshBodyReshape = () => {
        const active = actionableBodyPoints.length > 0 && entry.bodyReshapeEnabled !== false;
        masterInput.checked = active;
        icon.className = `adherence-icon ${active ? "is-pending" : "is-done"}`;
        icon.textContent = active ? "!" : "✓";
        verdict.textContent = !actionableBodyPoints.length
          ? "無需調整"
          : (active ? "可以調整" : "不執行");
        list.hidden = actionableBodyPoints.length > 0 ? !active : false;
        pointControls.forEach(({ point, input, slider }) => {
          const enabled = active && point.selectable !== false && !isReadOnlyLocked();
          input.disabled = !enabled;
          input.checked = enabled && point.allowed !== false;
          slider.disabled = !enabled || point.allowed === false;
        });
      };
      masterInput.addEventListener("change", () => {
        entry.bodyReshapeEnabled = masterInput.checked;
        refreshBodyReshape();
      });
      refreshBodyReshape();
      items.push(section);
    }
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
    { field: "subject_extent_box", color: "#38bdf8", label: "主體範圍", corner: "tr" },
    // subjects contains both people and non-person subjects. Only surface the
    // former here: the per-person diagnostics below use the same person box,
    // while product/animal/object subjects are outside this debug view's
    // purpose. `personOnly` is applied by analysisBoxList before reading box.
    { field: "subjects", color: "#6366f1", label: "人物主體（subjects）", corner: "br", list: true, itemBox: true, personOnly: true },
    { field: "crop_box", color: "#f59e0b", label: "crop_box（分析建議）", corner: "bl", dashed: true },
    { field: "applied_crop_plan", color: "#f43f5e", label: "實際送出裁切", corner: "br" },
    // detect_face_crops (openai_client.analyze_photo) -- zero, one, or several
    // boxes, unlike every other spec above which is a single box. Same
    // normalized-against-original-image convention as roi_box, so it slots
    // into the same overlay with no separate coordinate handling.
    { field: "_face_crop_boxes", color: "#a855f7", label: "臉部裁切", corner: "tl", list: true },
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
    { field: "blink_issue", color: "#eab308", label: "閉眼偵測", corner: "tl", perSubject: true, flagField: "should_fix", flagWhen: true, flagText: "（需修正）" },
    { field: "gaze_issue", color: "#ec4899", label: "眼神對焦", corner: "tr", perSubject: true, flagField: "should_fix", flagWhen: true, flagText: "（需修正）" },
    { field: "hair_issue", color: "#14b8a6", label: "亂髮偵測", corner: "bl", perSubject: true, flagField: "should_fix", flagWhen: true, flagText: "（需修正）" },
    { field: "smile_issue", color: "#84cc16", label: "笑容調整", corner: "br", perSubject: true, flagField: "adjust_safe", flagWhen: false, flagText: "（不建議調整）" }
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
        ? "將送出裁切（預覽）" : spec.label;
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
        : `　x ${box.x.toFixed(3)} · y ${box.y.toFixed(3)} · w ${box.w.toFixed(3)} · h ${box.h.toFixed(3)}`;
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
    if (els.compareCard) els.compareCard.classList.toggle("showing-generated", showGenerated);
    const label = showGenerated ? "查看原始照片" : "查看輸出結果";
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
      renderTags(els.recipeTags, [
        ...buildStyleOptionTagLabels(analysis.style_options),
        debugUiActive ? `${buildAspectRatioTagLabel(analysis)}${ratioIsPreview ? "（預覽）" : ""}` : ""
      ]);
      fitRecipeTagsRow(els.recipeTags);
    }
    els.resultSummary.textContent = analysis.photo_summary || "已完成影像分析與輸出。";
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
          userInstruction.user_intent_summary ? `使用者目標：${userInstruction.user_intent_summary}。` : "",
          userInstruction.normalized_user_direction ? `執行方向：${userInstruction.normalized_user_direction}。` : "",
          userInstruction.warning ? `提醒：${userInstruction.warning}` : ""
        ].filter(Boolean)
      : [analysis.edit_intent || preset.intent || "保留原照並提升質感。"];
    fillList(document.querySelector("#r-intent"), intentItems, ["保留原照並提升質感。"]);

    document.querySelector("#r-status").textContent = formatStatus(qa);

    // main_subject/scene_type are already surfaced by buildPhotoContentSummary
    // just above (els.subjectText, "照片總覽") -- repeating them here as well
    // read as the same "主體：.../場景：..." sentence printed twice in a row.
    els.diagnosisText.textContent = analysis.aesthetic_diagnosis
      || "照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。";

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

    const bodyPrompt = bodyReshapePromptForDisplay(analysis);
    els.compositionText.textContent = joinParts([
      analysis.composition_strategy ? sentence("原圖觀察", analysis.composition_strategy) : "",
      analysis.composition_intent ? sentence("建議構圖", analysis.composition_intent) : "",
      analysis.framing_intent ? sentence("建議取景", analysis.framing_intent) : "",
      analysis.recommended_aspect_ratio
        ? `${ratioIsPreview ? "下次輸出比例（預覽）" : "輸出比例"}：${aspectRatioDisplayValue(analysis.recommended_aspect_ratio)}${!ratioIsPreview && analysis.aspect_ratio_reason ? `，${stripFieldLabel(analysis.aspect_ratio_reason, ["比例", "建議比例", "比例理由"])}` : ""}。`
        : "",
      analysis.layout_method ? sentence("布局方法", analysis.layout_method) : "",
      bodyPrompt ? `身形調整：${bodyPrompt}` : ""
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
      pending ? "尚未執行修圖，確認後將開始 AI 影像修圖。" : (data.localOnly ? "目前使用本機處理，未執行 AI 影像生成。" : "目前已執行 AI 影像修圖。")
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
      ? `${ratioIsPreview ? "下次裁切（預覽）" : "已套用裁切"}${cropBox.aspectRatio ? `，比例 ${cropBox.aspectRatio}` : ""}：x:${cropBox.x.toFixed(2)} y:${cropBox.y.toFixed(2)} w:${cropBox.w.toFixed(2)} h:${cropBox.h.toFixed(2)}。`
      : "保留原始構圖。";
    const validationText = validation
      ? `AI 最終選擇：${validation.selected_candidate_label || validation.selected_candidate_id || "最佳候選"}。${validation.reason || ""}`
      : "";
    els.cropText.textContent = joinParts([
      analysis.recommended_aspect_ratio
        ? `${ratioIsPreview ? "下次輸出比例（預覽）" : "輸出比例"}：${aspectRatioDisplayValue(analysis.recommended_aspect_ratio)}${!ratioIsPreview && analysis.aspect_ratio_reason ? `（${analysis.aspect_ratio_reason}）` : ""}。`
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
    // The legacy raw prompt is only a meaningful "before" when another
    // strategy replaced it. Do not show a duplicate prompt on legacy runs.
    const resolution = prompts.image_edit_resolution;
    setPrompt(els.promptEditRaw, resolution ? prompts.image_edit_raw : null);
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
    setBodyReshapeReviewAnalysis(analysis, { resetState: true });
    reviewExpanded = expanded;
    // A fresh analysis always lands back on the 人像檢查 tab first (falls
    // back to 修圖建議 in renderFaceReviewCards if this photo has no faces),
    // same as selectedFaceIndex resetting to 0 in renderFaceThumbnails below.
    activeReviewTab = "face";
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
        || "照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。";
    }

    if (els.apComposition) {
      const userInstruction = analysis.user_instruction || {};
      const bodyPrompt = bodyReshapePromptForDisplay(analysis);
      els.apComposition.textContent = joinParts([
        userInstruction.has_user_direction
          ? `使用者目標：${userInstruction.user_intent_summary || userInstruction.normalized_user_direction || ""}。`
          : (analysis.edit_intent || preset?.intent || ""),
        analysis.composition_intent ? sentence("建議構圖", analysis.composition_intent) : "",
        bodyPrompt ? `身形調整：${bodyPrompt}` : ""
      ]);
    }

    fillCheckList(els.apRecipeList, analysis.retouch_recipe, ["尚未取得修圖建議。"]);
    renderFaceThumbnails(analysis);

    // Rebuild the style-option groups each run (defaults to "Default
    // (analyzed)"), and re-evaluate which apply. Pose only makes sense for a
    // person subject; Angle applies to any subject (product/scene shots
    // benefit from camera-angle framing too). The actual retouch behavior
    // for these selections is not wired up yet -- this is UI only for now.
    // The 修圖風格 tab renders these as image-thumbnail buttons
    // (fillStyleOptionList).
    fillStyleOptionList(els.apPoseList, POSE_OPTIONS, "ap-pose", "default", "pose");
    fillStyleOptionList(els.apAngleList, ANGLE_OPTIONS, "ap-angle", "default", "angle");
    fillStyleOptionList(els.apLightingList, LIGHTING_OPTIONS, "ap-lighting", "default", "lighting");
    fillStyleOptionList(els.apSizeList, SIZE_OPTIONS, "ap-size", "default", "size");
    [els.apPoseList, els.apAngleList, els.apLightingList, els.apSizeList].forEach(syncStyleTrigger);
    updateStyleTriggerLabel();
    const isPerson = analysis.subject_category === "人物";
    if (els.apGroupPose) els.apGroupPose.hidden = !isPerson;
    if (els.apGroupAngle) els.apGroupAngle.hidden = false;
  }

  // Narrow re-fill of just #ap-recipe-list -- for when the panel is already
  // open and only the recipe items need to change (flipping a 人像修正設定
  // toggle re-filters correction_plan/retouch_recipe, see
  // refreshInlineRecipeList in app.js), without re-running the rest of
  // renderAnalysisPreview and disturbing whatever Pose/Angle/Lighting/Size
  // pick the user already made.
  function refreshRecipeList(analysis) {
    fillCheckList(els.apRecipeList, analysis.retouch_recipe, ["尚未取得修圖建議。"]);
  }

  return {
    renderResult, renderAnalysisPreview, refreshRecipeList, syncStyleTrigger,
    updateStyleTriggerLabel, clearStylePick, setCompareView, renderTags,
    renderBoxTypeMenu, drawAnalysisBoxes, setDebugUi, previewAspectRatio,
    getFaceIssueOverrides, applyFaceIssueOverrides, setFaceIssueCategoryLock, resetFaceReviewState,
    setBodyReshapeEnabled, getBodyReshapeReviewEnabled, setBodyReshapeReviewEnabled,
    renderFaceReviewCards,
    syncFaceReviewAdvancedOptions, syncFaceReviewPoseSuggestion
  };
})();
