window.PhotoAgentResults = (() => {
  const { els } = window.PhotoAgentDom;
  const { resolveCropBoxForDisplay, resolveCropBoxWithReport } = window.PhotoAgentImageTools;

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

  function previewAspectRatio(pref) {
    if (!lastRender || !pref) return;
    const { analysis } = lastRender;
    const label = aspectRatioForPref(analysis, pref);
    const width = els.originalImg?.naturalWidth;
    const height = els.originalImg?.naturalHeight;
    if (!width || !height) return;

    const shown = { ...analysis, recommended_aspect_ratio: label, _aspect_ratio_preview: pref };
    if (shown.crop_box) shown.crop_box = { ...shown.crop_box, aspect_ratio: label };
    // The crop the edit model would be handed, recomputed for this ratio --
    // null when nothing would be cropped, same as the backend's own contract.
    shown.applied_crop_plan =
      resolveCropBoxWithReport(shown.crop_box, shown, width, height).cropBox || null;

    renderResult({ ...lastRender, analysis: shown }, { keepView: true });
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

  // ── Face thumbnails (#ap-face-thumb-row) + should_fix modal (#ap-face-modal) ──
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
  // AI to fix this" toggle in fillFaceIssueList below: flagged means there
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
    { field: "smile_issue", label: "笑容調整", flagField: "adjust_safe", flagWhen: true, flagText: "可以調整", okText: "不建議調整" }
  ];

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
  // field this entry came from -- the toggle in fillFaceIssueList writes back
  // through them (getFaceIssueOverrides), and buildEffectiveAnalysis (app.js)
  // uses the same pair to flip should_fix/adjust_safe before submission.
  function faceIssueEntries(subject, index, advancedOptions = {}) {
    if (!subject) return [];
    return FACE_ISSUE_CATEGORIES.map((category) => {
      const match = subject[category.field];
      if (!match || typeof match !== "object") return null;
      const explicitSmileLevel = category.field === "smile_issue"
        && advancedOptions.smile_adjustment !== false
        && advancedOptions.smile_level
        && advancedOptions.smile_level !== "auto";
      const isHair = category.field === "hair_issue";
      return {
        label: category.label,
        flagged: match[category.flagField] === category.flagWhen,
        flagText: category.flagText,
        okText: category.okText,
        reason: match.reason || "",
        category: category.field,
        subjectIndex: index,
        // Hair and smile are user-selectable transformations even when the
        // analyzer recommends preserving them. An explicit upload-page smile level is already a manual opt-in,
        // including for a face the analyzer marked unsafe. Auto still starts
        // such a face off. Detection-based rows remain analysis-gated.
        controllable: match[category.flagField] === category.flagWhen || isHair || category.field === "smile_issue",
        allowed: match[category.flagField] === category.flagWhen || !!explicitSmileLevel,
        level: category.field === "hair_issue"
          ? (advancedOptions.calm_hair ? "tidy" : "dynamic")
          : category.field === "smile_issue"
            ? (advancedOptions.smile_level || "auto")
            : null
      };
    }).filter(Boolean);
  }

  function bodyReshapePointEntries(subject, subjectIndex, analysis) {
    const points = subject?.body_reshape?.key_points;
    if (!Array.isArray(points)) return [];
    const disabled = new Set(
      (Array.isArray(analysis?._disabled_body_reshape_key_points)
        ? analysis._disabled_body_reshape_key_points : [])
        .filter((item) => Number.isInteger(item?.subject_index) && Number.isInteger(item?.key_point_index))
        .map((item) => `${item.subject_index}:${item.key_point_index}`)
    );
    return points.map((point, keyPointIndex) => ({
      text: typeof point === "string" ? point.trim() : "",
      subjectIndex,
      keyPointIndex,
      allowed: !disabled.has(`${subjectIndex}:${keyPointIndex}`)
    })).filter((point) => point.text);
  }

  function bodyReshapeSubjectEnabled(subjectIndex, points, analysis) {
    if (!points.length) return false;
    const disabledSubjects = Array.isArray(analysis?._disabled_body_reshape_subjects)
      ? analysis._disabled_body_reshape_subjects : [];
    return !disabledSubjects.some(
      (index) => Number.isInteger(index) && index === subjectIndex
    );
  }

  // Flattened { "subjectIndex:category": false } for every flagged issue the
  // user turned off in the face modal -- read once at submit time
  // (buildEffectiveAnalysis, app.js) rather than kept in sync continuously,
  // matching how recipeChecked is read fresh off the recipe checkboxes.
  function getFaceIssueOverrides() {
    const overrides = {};
    faceThumbEntries.forEach((entry) => {
      (entry.issues || []).forEach((issue) => {
        if (!issue.flagged && !["hair_issue", "smile_issue"].includes(issue.category)) return;
        if (issue.allowed === false) overrides[`${issue.subjectIndex}:${issue.category}`] = false;
        else if (issue.level) overrides[`${issue.subjectIndex}:${issue.category}`] = { allowed: true, level: issue.level };
      });
    });
    return overrides;
  }

  // Flattened { "subjectIndex:keyPointIndex": false } for body key points
  // unchecked in the per-person modal. The full analysis remains intact so
  // an unchecked point can be shown and re-enabled on a later revise.
  function getBodyReshapeOverrides() {
    const overrides = {};
    faceThumbEntries.forEach((entry) => {
      if (entry.bodyAnalysisAvailable && entry.bodyReshapeEnabled === false) {
        overrides[`subject:${entry.subjectIndex}`] = false;
      }
      (entry.bodyKeyPoints || []).forEach((point) => {
        if (point.allowed === false) {
          overrides[`${point.subjectIndex}:${point.keyPointIndex}`] = false;
        }
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
        if (override === false) issue.allowed = false;
        else if (override && typeof override === "object") {
          issue.allowed = override.allowed !== false;
          if (override.level) issue.level = override.level;
        }
      });
    });
  }

  function applyBodyReshapeOverrides(overrides) {
    if (!overrides) return;
    faceThumbEntries.forEach((entry) => {
      if (entry.bodyAnalysisAvailable) {
        entry.bodyReshapeEnabled = overrides[`subject:${entry.subjectIndex}`] !== false;
      }
      (entry.bodyKeyPoints || []).forEach((point) => {
        point.allowed = overrides[`${point.subjectIndex}:${point.keyPointIndex}`] !== false;
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

  // { box, issues }[] for the face row currently on screen -- read by
  // fillFaceIssueList when a thumbnail button is clicked (app.js passes just
  // the clicked button's index, not the data itself).
  let faceThumbEntries = [];

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
    const row = els.apFaceThumbRow;
    if (!row) return;
    const faces = localFaceEntries(analysis);
    const bodySubjects = (Array.isArray(analysis?.subjects) ? analysis.subjects : [])
      .map((subject, subjectIndex) => ({ subject, subjectIndex }))
      .filter(({ subject }) => subject?.is_person && subject?.body_reshape && typeof subject.body_reshape === "object");
    if (!faces.length && !bodySubjects.length) {
      row.hidden = true;
      row.innerHTML = "";
      faceThumbEntries = [];
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
        // internal match) so the subject's own label -- used as the face
        // modal's title in fillFaceIssueList below -- comes from the exact
        // same match its issues do, not a second, potentially different one.
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
        const bodyKeyPoints = bodyReshapePointEntries(subject, index, analysis);
        return {
          box: f.box,
          issues: faceIssueEntries(subject, index, analysis?._advanced_options || {}),
          bodyKeyPoints,
          bodyAnalysisAvailable: !!(subject?.body_reshape && typeof subject.body_reshape === "object"),
          bodyReshapeEnabled: bodyReshapeSubjectEnabled(index, bodyKeyPoints, analysis),
          subjectIndex: index,
          sentToModel,
          label: (subject && subject.label) || "",
        };
      })
      .filter(Boolean);

    // Body planning is per subjects[] person, while this row historically
    // came only from the local face detector. A distant, turned-away, or
    // partially occluded person can therefore have a valid body_reshape plan
    // without a local face box. Give every such unmatched person a fallback
    // entry based on the model's person box, otherwise their key points could
    // reach edit without ever being exposed as user-selectable checkboxes.
    const representedSubjectIndexes = new Set(
      faceThumbEntries
        .map((entry) => entry.subjectIndex)
        .filter((index) => Number.isInteger(index) && index >= 0)
    );
    bodySubjects.forEach(({ subject, subjectIndex }) => {
      if (representedSubjectIndexes.has(subjectIndex)) return;
      const box = subject.box;
      if (!box || ![box.x, box.y, box.w, box.h].every(Number.isFinite)) return;
      const bodyKeyPoints = bodyReshapePointEntries(subject, subjectIndex, analysis);
      faceThumbEntries.push({
        box,
        issues: faceIssueEntries(subject, subjectIndex, analysis?._advanced_options || {}),
        bodyKeyPoints,
        bodyAnalysisAvailable: true,
        bodyReshapeEnabled: bodyReshapeSubjectEnabled(subjectIndex, bodyKeyPoints, analysis),
        subjectIndex,
        // null means this is a subjects[] fallback, not a local face that
        // missed the zoomed-crop cap (false retains that existing meaning).
        sentToModel: null,
        label: subject.label || "",
      });
    });
    row.innerHTML = "";
    faceThumbEntries.forEach((entry, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ap-face-thumb-btn";
      // A locally-detected face beyond the crop cap never got its own zoomed
      // image or "Local face detector reference" text in front of the model
      // (see detect_face_crops/analyze_photo) -- any findings it shows came
      // from the model describing this person off the full photo alone, not
      // the deeper zoomed-crop analysis every other thumbnail benefits from.
      // Dimmed + dashed so that distinction is visible at a glance.
      if (!entry.sentToModel) btn.classList.add("ap-face-thumb-btn--local-only");
      btn.dataset.faceIndex = String(index);
      btn.setAttribute("aria-label", `人物 ${index + 1}`);
      const dataUrl = img ? cropFaceThumbnail(img, entry.box) : "";
      entry.thumbUrl = dataUrl;
      if (dataUrl) {
        const thumb = document.createElement("img");
        thumb.className = "ap-face-thumb-img";
        thumb.src = dataUrl;
        thumb.alt = `人物 ${index + 1}`;
        btn.appendChild(thumb);
      }
      // .ap-face-thumb-btn itself is overflow:hidden (it clips the square
      // thumbnail into its round avatar shape) -- a badge placed straight on
      // it would get clipped along with everything else that crosses its
      // edge, so it goes on this unclipped wrapper instead, positioned over
      // the button rather than inside it.
      const wrap = document.createElement("span");
      wrap.className = "ap-face-thumb-wrap";
      wrap.appendChild(btn);
      // Lets the user spot which faces have an analysis-flagged issue
      // (should_fix/adjust_safe) without opening #ap-face-modal for every
      // thumbnail -- reflects the model's own verdict, not the allow/deny
      // toggle in the modal, so it stays put even after the user turns a fix
      // off (it's still something the analysis found, just no longer approved).
      if ((entry.issues || []).some((issue) => issue.flagged) || (entry.bodyKeyPoints || []).length) {
        const badge = document.createElement("span");
        badge.className = "ap-face-thumb-badge";
        badge.textContent = "!";
        wrap.appendChild(badge);
      }
      if (!entry.sentToModel) {
        const localBadge = document.createElement("span");
        localBadge.className = "ap-face-thumb-local-badge";
        localBadge.textContent = "本機";
        localBadge.title = "本機偵測到的人臉，未送入分析模型做特寫判讀";
        wrap.appendChild(localBadge);
      }
      row.appendChild(wrap);
    });
    row.hidden = faceThumbEntries.length === 0;
  }

  // Called by app.js's #ap-face-thumb-row click handler right before it
  // shows #ap-face-modal -- sets the modal title to that person's own
  // subjects[].label (renderFaceThumbnails' bestSubjectMatch), falling back
  // to a plain "人物 N" when no subject matched; mirrors that face's own
  // thumbnail into #ap-face-modal-thumb (so the modal doesn't rely on the
  // user remembering which small row button they clicked) and fills
  // #ap-face-issue-list with that face's should_fix/adjust_safe verdicts,
  // matched at renderFaceThumbnails time above. Flagged items get an
  // "allow AI to fix this" toggle; unsafe smile gets the same toggle off by
  // default so the user can override the warning. Changing it updates
  // issue.allowed in place on the
  // faceThumbEntries entry itself, which getFaceIssueOverrides reads back at
  // submit time.
  function fillFaceIssueList(index) {
    const entry = faceThumbEntries[index];
    if (els.apFaceModalTitle) {
      els.apFaceModalTitle.textContent = `${entry?.label || `人物 ${index + 1}`} 分析結果`;
    }
    if (els.apFaceModalThumb) {
      if (entry?.thumbUrl) {
        els.apFaceModalThumb.src = entry.thumbUrl;
        els.apFaceModalThumb.hidden = false;
      } else {
        els.apFaceModalThumb.hidden = true;
        els.apFaceModalThumb.removeAttribute("src");
      }
    }
    const element = els.apFaceIssueList;
    if (!element) return;
    element.innerHTML = "";
    if (entry && entry.sentToModel === false) {
      // This face still has its subjects[] match's findings below (see
      // renderFaceThumbnails) -- just a caveat that they came from the model
      // reading the full photo alone, not this face's own zoomed crop.
      const note = document.createElement("li");
      note.className = "ap-face-issue-note";
      note.textContent = "此人臉僅由本機偵測到，未送入分析模型做特寫判讀；以下為模型從整張照片判斷的結果。";
      element.appendChild(note);
    }
    const issues = entry?.issues || [];
    const bodyKeyPoints = entry?.bodyKeyPoints || [];
    if (!issues.length && !entry?.bodyAnalysisAvailable) {
      const li = document.createElement("li");
      li.textContent = "此人像沒有可顯示的分析結果。";
      element.appendChild(li);
      return;
    }
    issues.forEach((issue) => {
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
      const toggle = document.createElement("label");
      toggle.className = "ios-switch ap-face-issue-toggle";
      toggle.title = issue.flagged
        ? "允許 AI 修正此項目"
        : ["hair_issue", "smile_issue"].includes(issue.category)
          ? "分析判定無需或不建議調整；仍可手動開啟"
          : "模型未判定此項目需要修正，無需操作";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.disabled = !issue.controllable;
      input.checked = issue.controllable && issue.allowed;
      input.addEventListener("change", () => {
        issue.allowed = input.checked;
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
        const active = issue.controllable && issue.allowed;
        icon.className = `adherence-icon ${active ? "is-pending" : "is-done"}`;
        icon.textContent = active ? "!" : "✓";
        resultLabel.textContent = !issue.flagged
          ? (["hair_issue", "smile_issue"].includes(issue.category) && issue.allowed
              ? `已手動開啟`
              : issue.okText)
          : (issue.allowed ? issue.flagText : "已設定不修正");
      };
      refreshVerdict();
      row.appendChild(icon);
      row.appendChild(resultLabel);

      // Hair and smile both remain available as explicit user transformations
      // even when the analyzer recommends preserving the captured state.
      // CSS places the selector at the row's right edge on desktop and on a
      // second verdict-aligned row on mobile. Hide it whenever this person's
      // edit is off.
      let levelRow = null;
      if (["hair_issue", "smile_issue"].includes(issue.category)) {
        li.classList.add("ap-face-issue-item--with-level");
        row.classList.toggle("ap-face-issue-row--with-level", issue.allowed);
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
        select.disabled = !issue.allowed;
        select.addEventListener("change", () => { issue.level = select.value; });
        input.addEventListener("change", () => {
          select.disabled = !input.checked;
          levelRow.hidden = !input.checked;
          row.classList.toggle("ap-face-issue-row--with-level", input.checked);
        });
        levelRow.hidden = !issue.allowed;
        levelRow.appendChild(select);
      }

      li.appendChild(row);
      if (levelRow) li.appendChild(levelRow);

      // The model's own reasoning is debug-only detail (same gate as the
      // box overlay's legend) -- a regular user just needs the verdict, not
      // the analysis model's internal justification for it.
      if (issue.flagged && issue.reason && debugUiActive) {
        const reason = document.createElement("div");
        reason.className = "ap-face-issue-reason";
        reason.textContent = issue.reason;
        li.appendChild(reason);
      }

      element.appendChild(li);
    });

    if (entry?.bodyAnalysisAvailable) {
      const section = document.createElement("li");
      section.className = "ap-body-reshape-section";
      const masterRow = document.createElement("div");
      masterRow.className = "ap-face-issue-row";

      const toggle = document.createElement("label");
      toggle.className = "ios-switch ap-face-issue-toggle";
      toggle.title = bodyKeyPoints.length ? "允許 AI 執行這位人物的身形調整" : "沒有可執行的身形調整";
      const masterInput = document.createElement("input");
      masterInput.type = "checkbox";
      masterInput.disabled = !bodyKeyPoints.length;
      masterInput.checked = bodyKeyPoints.length && entry.bodyReshapeEnabled !== false;
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
      if (bodyKeyPoints.length) {
        bodyKeyPoints.forEach((point) => {
          const label = document.createElement("label");
          label.className = "ap-body-reshape-option";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.checked = point.allowed !== false;
          input.addEventListener("change", () => { point.allowed = input.checked; });
          const text = document.createElement("span");
          text.textContent = point.text;
          label.appendChild(input);
          label.appendChild(text);
          list.appendChild(label);
        });
      }
      section.appendChild(list);

      const refreshBodyReshape = () => {
        const active = bodyKeyPoints.length > 0 && entry.bodyReshapeEnabled !== false;
        masterInput.checked = active;
        icon.className = `adherence-icon ${active ? "is-pending" : "is-done"}`;
        icon.textContent = active ? "!" : "✓";
        verdict.textContent = !bodyKeyPoints.length
          ? "無需調整"
          : (active ? "可以調整" : "不執行");
        list.hidden = !active;
      };
      masterInput.addEventListener("change", () => {
        entry.bodyReshapeEnabled = masterInput.checked;
        refreshBodyReshape();
      });
      refreshBodyReshape();
      element.appendChild(section);
    }
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

    // main_subject/scene_type are already surfaced by buildPhotoContentSummary
    // just above (els.subjectText, "照片內容") -- repeating them here as well
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

    els.compositionText.textContent = joinParts([
      analysis.composition_strategy ? sentence("原圖觀察", analysis.composition_strategy) : "",
      analysis.composition_intent ? sentence("建議構圖", analysis.composition_intent) : "",
      analysis.framing_intent ? sentence("建議取景", analysis.framing_intent) : "",
      analysis.recommended_aspect_ratio
        ? `${ratioIsPreview ? "下次輸出比例（預覽）" : "輸出比例"}：${aspectRatioDisplayValue(analysis.recommended_aspect_ratio)}${!ratioIsPreview && analysis.aspect_ratio_reason ? `，${stripFieldLabel(analysis.aspect_ratio_reason, ["比例", "建議比例", "比例理由"])}` : ""}。`
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
  function renderAnalysisPreview({ analysis, preset }) {
    if (els.apSubject) els.apSubject.textContent = buildPhotoContentSummary(analysis);
    if (els.apSubjectTags) {
      renderTags(els.apSubjectTags, [
        analysis.subject_category || "未知類型",
        analysis.scene_category || "未知場景"
      ]);
    }

    // main_subject/scene_type are already surfaced by buildPhotoContentSummary
    // just above (els.apSubject, "照片內容") -- repeating them here as well
    // read as the same "主體：.../場景：..." sentence printed twice in a row.
    if (els.apDiagnosis) {
      els.apDiagnosis.textContent = analysis.aesthetic_diagnosis
        || "照片已有基本資訊，接下來以提升光線、色彩、構圖與視覺焦點為主。";
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
    renderFaceThumbnails(analysis);

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

  // Narrow re-fill of just #ap-recipe-list -- for when the panel is already
  // open and only the recipe items need to change (flipping a 人像設定
  // toggle re-filters correction_plan/retouch_recipe, see
  // refreshInlineRecipeList in app.js), without re-running the rest of
  // renderAnalysisPreview and disturbing whatever Pose/Angle/Lighting/Size
  // pick the user already made.
  function refreshRecipeList(analysis) {
    fillCheckList(els.apRecipeList, analysis.retouch_recipe, ["尚未取得修圖建議。"]);
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
    renderResult, renderAnalysisPreview, refreshRecipeList, renderBatchStyleOptions, syncStyleTrigger,
    updateStyleTriggerLabel, clearStylePick, setCompareView, renderTags,
    renderBoxTypeMenu, drawAnalysisBoxes, setDebugUi, previewAspectRatio,
    fillFaceIssueList, getFaceIssueOverrides, applyFaceIssueOverrides,
    getBodyReshapeOverrides, applyBodyReshapeOverrides
  };
})();
