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
    { value: "eye", label: "Eye Level" }
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

  // Maps each non-default option value to its example image in
  // assets/optional_style/<group>/ -- used by fillStyleOptionList to show the
  // #ap-style-modal overlay's option buttons as image thumbnails instead of
  // plain text. "default"/"Auto" has no file (there's nothing to show an
  // example image of), so it renders as a text-only placeholder card instead.
  const STYLE_OPTION_IMAGES = {
    pose: { playful: "pose-playful.png", candid: "pose-candid.png", strong: "pose-strong.png" },
    angle: { high: "angle-highshot.png", low: "angle-lowshot.png", eye: "angle-eyelevel.png" },
    lighting: { gold: "lighting-goldtone.png", soft: "lighting-soft.png" },
    size: { wide: "size-wide.png", wider: "size-wider.png", close: "size-close.png", closer: "size-closer.png" }
  };

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

  // Same picker as fillRadioList, but rendered as a grid of image-thumbnail
  // buttons instead of a plain text radio list -- used only by the per-image
  // #ap-style-modal overlay's Pose/Angle/Lighting/Size lists (assets live in
  // assets/optional_style/<group>/, see STYLE_OPTION_IMAGES above). The batch
  // sidebar's equivalent lists keep using fillRadioList/plain text unchanged.
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
        thumb.textContent = "Auto";
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
  // whichever radio in its list is currently checked -- shared by the initial
  // fillRadioList/fillStyleOptionList render and by app.js after it restores
  // a saved pick or the user selects a new one. Looks for the value pill two
  // different ways: nested inside a .ap-style-item (the batch options row's
  // always-visible list layout) or, for the per-image #ap-style-modal
  // overlay where the type button and its option grid are siblings rather
  // than nested, via the list's data-style-group attribute matching a
  // .ap-style-type-btn. No-ops quietly if neither matches.
  function syncStyleTrigger(listElement) {
    if (!listElement) return;
    const group = listElement.dataset.styleGroup;
    const valueEl = listElement.closest(".ap-style-item")?.querySelector(".ap-style-value")
      || (group && document.querySelector(`.ap-style-type-btn[data-style-group="${group}"] .ap-style-value`));
    if (!valueEl) return;
    const checked = listElement.querySelector("input:checked");
    const label = checked?.closest("label")?.querySelector(".step-cb-label, .ap-style-option-label")?.textContent;
    valueEl.textContent = label || "Default";
  }

  // Resets one ap-pose/-angle/-lighting/-size radio group back to its
  // "default" option and fires a real `change` event on it -- app.js's
  // per-image radio change listener is delegated onto the enclosing
  // #ap-*-list <ul> (see the wireup at the bottom of app.js), so this single
  // dispatch is all that's needed to run the exact same markOptionTouched/
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
  // without reopening #ap-style-modal and finding "Auto" in the grid.
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

  // Keeps #ap-style-picker-value (the removable-chip row right after the
  // #ap-style-picker button) in sync with whatever's currently picked across
  // the 4 ap-pose/-angle/-lighting/-size radio groups -- called whenever
  // those radios change (app.js) or the analysis preview re-renders. Hidden
  // entirely while everything's still Default/Auto -- the button itself
  // always just reads "修圖風格選項", same non-default-only filtering as
  // buildStyleOptionsText/buildStyleOptionTagLabels above. Reads the radios
  // directly (same `input[name="..."]:checked` lookup as
  // readStyleSelectionsFromDom, app.js) rather than depending on that
  // function, since results.js has no access to app.js's internals.
  // Also drives the .ap-style-type-clear "x" sitting on each Pose/Angle/
  // Lighting/Size pill inside #ap-style-modal (index.html) -- same
  // non-default check as the picked-chip row, just a per-category on/off
  // instead of building a label, so the in-modal clear affordance and the
  // outside-the-modal chip row never disagree about which categories are
  // currently overridden.
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
    tags.filter(Boolean).forEach((tag) => {
      const chip = document.createElement("span");
      chip.textContent = tag;
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
    { field: "crop_box", color: "#f59e0b", label: "crop_box（分析建議）", corner: "bl", dashed: true },
    { field: "applied_crop_plan", color: "#f43f5e", label: "實際送出裁切", corner: "br" }
  ];

  let boxesAnalysis = null;
  let boxesVisible = false;

  function analysisBoxList(analysis) {
    if (!analysis) return [];
    return ANALYSIS_BOX_SPECS.map((spec) => {
      const box = analysis[spec.field];
      if (!box) return null;
      // Same box, different promise: after a preview it is what WILL be sent.
      const label = (spec.field === "applied_crop_plan" && analysis._aspect_ratio_preview
        && analysis._aspect_ratio_preview !== analysis._aspect_ratio_pref_at_edit)
        ? "將送出裁切（預覽）" : spec.label;
      // crop_box carries its own on/off switch; an analysis that recommends no
      // reframe still ships the field with enabled:false.
      if (spec.field === "crop_box" && box.enabled !== true) return null;
      const x = Number(box.x);
      const y = Number(box.y);
      const w = Number(box.w);
      const h = Number(box.h);
      if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;
      return {
        ...spec,
        label,
        x, y, w, h,
        ratio: box.aspect_ratio || box.aspectRatio || ""
      };
    }).filter(Boolean);
  }

  function drawAnalysisBoxes() {
    const overlay = els.analysisBoxes;
    const legend = els.analysisBoxesLegend;
    if (!overlay) return;

    const showingGenerated = !!els.compareCard?.classList.contains("showing-generated");
    const boxes = debugUiActive && boxesVisible && !showingGenerated
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
    boxes.forEach((box) => {
      const row = document.createElement("div");
      row.className = "ab-legend-item";
      const swatch = document.createElement("span");
      swatch.className = `ab-swatch${box.dashed ? " is-dashed" : ""}`;
      swatch.style.borderColor = box.color;
      const text = document.createElement("span");
      const coords = `x ${box.x.toFixed(3)} · y ${box.y.toFixed(3)} · w ${box.w.toFixed(3)} · h ${box.h.toFixed(3)}`;
      text.textContent = `${box.label}　${coords}${box.ratio ? `　(${box.ratio})` : ""}`;
      row.append(swatch, text);
      legend.appendChild(row);
    });
  }

  // Returns the new visibility so the caller can reflect it on the button.
  function toggleAnalysisBoxes() {
    boxesVisible = !boxesVisible;
    drawAnalysisBoxes();
    return boxesVisible;
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

  function renderResult({ data, analysis, preset, imageModel, hasOriginal = true }) {
    const qa = data.qualityCheck || null;
    const validation = analysis.crop_validation || null;

    // hasOriginal is false only for historical playback of a job with no
    // original photo to compare against (see viewHistoryRecord, app.js) --
    // every live flow always has one, so this defaults to true and every
    // existing caller is unaffected. Reset unconditionally (not just when
    // false) so a later live render doesn't inherit a hidden state left over
    // from an earlier historical one.
    if (els.compareToggleBtn) els.compareToggleBtn.hidden = !hasOriginal;
    if (els.compareFullscreenBtn) els.compareFullscreenBtn.hidden = !hasOriginal;

    // Keep whatever the overlay toggle was set to, but bind it to this run's
    // analysis -- switching RUN tabs should redraw the boxes for the run now
    // on screen, not leave the previous one's up.
    boxesAnalysis = analysis;
    setCompareView(true);
    els.generatedImg.src = data.image;
    els.downloadLink.href = data.image;
    els.modelName.textContent = data.model || imageModel;
    if (els.recipeTags) {
      renderTags(els.recipeTags, buildStyleOptionTagLabels(analysis.style_options));
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
    // #ap-style-picker now opens #ap-style-modal, a real overlay window (see
    // index.html) -- these lists render as image-thumbnail buttons
    // (fillStyleOptionList) instead of the batch sidebar's plain text
    // fillRadioList.
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

  return {
    renderResult, renderAnalysisPreview, renderBatchStyleOptions, syncStyleTrigger,
    updateStyleTriggerLabel, clearStylePick, setCompareView, renderTags,
    toggleAnalysisBoxes, drawAnalysisBoxes, setDebugUi
  };
})();
