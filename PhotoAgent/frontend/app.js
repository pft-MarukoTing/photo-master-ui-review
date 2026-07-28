(() => {
  const { els, showSection, showError, hideError, resetSteps, addTiming, getTimings } = window.PhotoAgentDom;
  const { presets } = window.PhotoAgentConfig;
  const {
    submitJob,
    pollJobStatus,
    getJobResult,
    resultImageUrl,
    getUiConfig,
    analyzeJob,
    analyzeJobBatch,
    getAnalyzeResult,
    continueJob,
    analyzeAipoc,
    analyzeAipocBatch
  } = window.PhotoAgentApi;
  const { renderResult, renderAnalysisPreview, renderBatchStyleOptions, syncStyleTrigger, setCompareView, renderTags } = window.PhotoAgentResults;
  renderBatchStyleOptions();

  // Keeps the run-tabs preview strip capped at 1/3 of #results-canvas-row's
  // own rendered width (the canvas + analysis-panel row, not just the
  // canvas) -- CSS alone can't size one element against an unrelated
  // sibling's width, so this tracks that row's actual box (which changes
  // with the image's natural size, the analysis panel opening/closing,
  // window resizes, etc.) and mirrors 1/3 of it onto #run-tabs-row. Beyond
  // that width, .run-tabs' own overflow-x:auto (plus the scroll arrows)
  // makes it scrollable. That 1/3 cap only applies in #run-tabs-row's normal
  // (unnested) position -- while the analysis panel is open it's moved
  // under the photo instead (openInlineAnalysisPanel), where CSS's plain
  // width:100% against its new parent already fits it to the photo's own
  // column, so the inline cap is cleared rather than fighting that.
  if (els.resultsCanvasRow && els.runTabsRow && typeof ResizeObserver !== "undefined") {
    const runTabsWidthObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width;
      const nested = els.resultsCanvasCol && els.runTabsRow.parentElement === els.resultsCanvasCol;
      els.runTabsRow.style.maxWidth = (width && !nested) ? `${Math.round(width / 3)}px` : "";
      updateRunTabsArrows();
    });
    runTabsWidthObserver.observe(els.resultsCanvasRow);
  }

  // Shows/hides the run-tabs scroll arrows (hidden entirely when the strip
  // doesn't overflow its 2/3-width cap) and disables whichever end is
  // already fully scrolled to. Called after (re)building the tabs, on
  // scroll, and whenever the cap above changes what fits.
  function updateRunTabsArrows() {
    if (!els.runTabs || !els.runTabsPrev || !els.runTabsNext) return;
    const canScroll = els.runTabs.scrollWidth > els.runTabs.clientWidth + 1;
    els.runTabsPrev.hidden = !canScroll;
    els.runTabsNext.hidden = !canScroll;
    if (!canScroll) return;
    // Disabled state tracks whether there's a selectable ("done") RUN on
    // that side of the current one, not raw scroll position -- since the
    // arrows now switch RUNs (selectAdjacentRunTab), not just scroll the
    // strip, and the current RUN can already be fully in view (nothing left
    // to physically scroll) while still having an earlier/later RUN to
    // switch to.
    const tabs = Array.from(els.runTabs.children);
    const idx = currentRunTabsIndex;
    const hasPrevRun = idx != null && tabs.slice(0, idx).some((t) => !t.disabled);
    const hasNextRun = idx != null && tabs.slice(idx + 1).some((t) => !t.disabled);
    els.runTabsPrev.disabled = !hasPrevRun;
    els.runTabsNext.disabled = !hasNextRun;
  }

  // Steps to the next/previous *selectable* (status "done") RUN, skipping
  // over any still-processing/errored tabs, and brings it into view -- so
  // the arrows switch the displayed image itself, not just the strip's
  // scroll position (which used to be all they did).
  function selectAdjacentRunTab(delta) {
    if (!els.runTabs || currentRunTabsOnSelect == null || currentRunTabsIndex == null) return;
    const tabs = Array.from(els.runTabs.children);
    let idx = currentRunTabsIndex + delta;
    while (idx >= 0 && idx < tabs.length && tabs[idx].disabled) idx += delta;
    if (idx < 0 || idx >= tabs.length) return;
    currentRunTabsOnSelect(idx);
    const newTab = els.runTabs.children[idx];
    if (newTab) newTab.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
  }

  if (els.runTabs) els.runTabs.addEventListener("scroll", updateRunTabsArrows);
  if (els.runTabsPrev) {
    els.runTabsPrev.addEventListener("click", () => selectAdjacentRunTab(-1));
  }
  if (els.runTabsNext) {
    els.runTabsNext.addEventListener("click", () => selectAdjacentRunTab(1));
  }

  let selectedFile = null;
  let previewUrl = "";
  // Bumped by every setSelectedFile/setSelectedFileBatch call so a stale,
  // still-converting HEIC preview can't clobber a newer selection's UI once
  // its async conversion finally resolves.
  let fileSelectionToken = 0;
  let lastResult = null; // { file, resultText, jobId, path } -- for the zip download
  // Set once at startup by applyDebugUiVisibility -- gates whether the
  // result card shows the raw model string (e.g. "gpt-4.1-mini + gpt-image-2")
  // or a plain-language stand-in a regular user actually cares about.
  let isDebugUiActive = false;

  // Single-image flow: kept after analyze so "回到流程" (from the results page)
  // can re-show the analysis preview and resubmit steps 3-6 without re-running
  // step 2. lastSingleSelections holds the last submitted prompt/options/recipe
  // checkboxes so re-opening the preview restores them instead of resetting.
  let singleFlowState = null; // { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, aiPhotoEditing, processingPath }
  let lastSingleSelections = null; // { userPrompt, options, recipeChecked }

  // Single-image RUN N times (mirrors batch's per-image runs): populated only
  // when the RUN count on the analysis-preview screen is > 1. singleRuns holds
  // each run's {run, jobId, status, imageUrl, result, timings, error};
  // singleMirrorIndex is which one is currently shown in the results view (the
  // single-image counterpart of a batch record's mirrorIndex).
  let singleRuns = null;
  let singleMirrorIndex = null;

  // Which `runs` array buildRunTabs last drew from -- lets it tell "still
  // browsing the same photo's runs" (keep the strip's scroll position) apart
  // from "switched to a different photo" (scroll back to the start), since
  // both cases fully rebuild the tab buttons from scratch.
  let lastRunTabsRuns = null;

  // The currently-active tab index and its select callback, refreshed by
  // every buildRunTabs call -- lets the prev/next arrows (defined once, up
  // top, before any runs exist) step to the adjacent RUN via whichever
  // handler is current (batch vs single-image) instead of just scrolling
  // the strip without changing what's shown.
  let currentRunTabsIndex = null;
  let currentRunTabsOnSelect = null;

  // Batch mode: populated only when more than one file is selected. While
  // batchRecords is empty the app behaves exactly as the single-image flow.
  const POLL_CONCURRENCY = 4;
  // Batch-level Pose/Angle/Lighting/Size controls, and the per-category lock
  // toggle each shows once any image in the batch has been individually
  // adjusted for that category (see updateBatchOptionsLocks).
  const BATCH_OPTION_CATEGORIES = [
    { key: "pose", group: "batchGroupPose", list: "batchPoseList", lockRow: "batchPoseLockRow", lock: "batchPoseLock", lockBadge: "batchPoseLockBadge" },
    { key: "angle", group: "batchGroupAngle", list: "batchAngleList", lockRow: "batchAngleLockRow", lock: "batchAngleLock", lockBadge: "batchAngleLockBadge" },
    { key: "lighting", group: "batchGroupLighting", list: "batchLightingList", lockRow: "batchLightingLockRow", lock: "batchLightingLock", lockBadge: "batchLightingLockBadge" },
    { key: "size", group: "batchGroupSize", list: "batchSizeList", lockRow: "batchSizeLockRow", lock: "batchSizeLock", lockBadge: "batchSizeLockBadge" }
  ];
  let batchRecords = [];   // see setSelectedFileBatch for record shape
  let batchSettings = null;
  // Direct reference to whichever record the sidebar currently has selected --
  // not a status-filtered position (unlike doneBatchRecords()/analyzedBatchRecords()
  // in the old grid+detail-pagination design), since the sidebar shows every
  // record regardless of status and any of them can be clicked at any time.
  let currentBatchRecord = null;

  const procVisual = document.querySelector(".proc-visual");
  const procTitle = document.querySelector(".proc-title");
  const procSub = document.querySelector(".proc-sub");
  const timingDetails = document.querySelector("#timing-details");
  const timingList = document.querySelector("#timing-list");
  const processingPathRow = document.querySelector("#processing-path-row");
  const apAnalysisToggle = document.querySelector("#ap-analysis-toggle");
  const resultsDetailExtra = document.querySelector("#results-detail-extra");

  // 生成張數 (RUN count) selects offer 1-20 in the markup, but letting a
  // regular user fire off up to 20 paid generations per photo is a debug/
  // testing convenience, not something to expose by default -- cap the
  // visible options at GENERAL_MAX_RUN_COUNT for everyone else.
  const GENERAL_MAX_RUN_COUNT = 8;

  function applyRunCountOptionLimit(select, isDebug) {
    if (!select) return;
    Array.from(select.options).forEach((opt) => {
      const value = parseInt(opt.value, 10);
      if (Number.isNaN(value)) return;
      opt.hidden = !isDebug && value > GENERAL_MAX_RUN_COUNT;
    });
    if (!isDebug && parseInt(select.value, 10) > GENERAL_MAX_RUN_COUNT) {
      select.value = String(GENERAL_MAX_RUN_COUNT);
    }
  }

  // Testbed-only UI: some rows/panels are hidden unless the backend was
  // started with `--debug-ui` (or the page URL contains "debug", for quick
  // local checks without a real backend). isDebug=false is the shape a
  // normal user sees -- the processing-path and QA-verification rows (true
  // internal/testing knobs) and every deep-dive section (analysis breakdown,
  // workflow steps, timing, prompts) disappear. Image quality/model stay
  // visible in both modes -- they're choices a regular user cares about, not
  // debug-only. Elements this hides always keep their own independent
  // show/hide logic underneath; this only ever adds an extra "and also not
  // debug" gate on top; it never replaces that logic.
  function applyDebugUiVisibility(isDebug) {
    isDebugUiActive = isDebug;
    if (processingPathRow) processingPathRow.style.display = isDebug ? "" : "none";
    if (isDebug && els.processingPathSelect) els.processingPathSelect.value = "local";
    if (els.runQaRow) els.runQaRow.style.display = isDebug ? "" : "none";
    if (apAnalysisToggle) apAnalysisToggle.hidden = !isDebug;
    if (resultsDetailExtra) resultsDetailExtra.hidden = !isDebug;
    applyRunCountOptionLimit(els.imageRunCountSelect, isDebug);
    applyRunCountOptionLimit(els.apRunCountSelect, isDebug);
    applyRunCountOptionLimit(els.runCountSelect, isDebug);
  }

  if (window.location.href.includes("debug")) {
    applyDebugUiVisibility(true);
  } else {
    getUiConfig().then((cfg) => {
      applyDebugUiVisibility(!!cfg.debugUi);
      if (!cfg.debugUi && cfg.aipocDefault && els.processingPathSelect) {
        // Non-debug deployment started with `--aipoc`: the selector stays
        // hidden, but its value still drives the default processing path.
        els.processingPathSelect.value = "aipoc";
      }
    });
  }

  // Merged 修圖模型/影像品質 selector -- one option per valid model+quality
  // combination (window.PhotoAgentConfig.imageModelQualityOptions is the
  // single source of truth), appearing on both the upload screen
  // (#image-model-quality) and the analysis-preview screen
  // (#ap-image-model-quality, shown mid-flow so the choice can be revised
  // right before the AI edit fires). Keep both selects mirrored, and push
  // the live {model, quality} into whichever flow state is active so
  // finishSingleFlow/editBatchItem (which read singleFlowState/batchSettings)
  // pick up a change made on the preview screen without any other wiring.
  function findModelQualityOption(value) {
    const options = window.PhotoAgentConfig.imageModelQualityOptions || [];
    return options.find((o) => o.value === value) || options[0] || null;
  }

  function populateModelQualitySelect(select) {
    if (!select) return;
    select.innerHTML = "";
    (window.PhotoAgentConfig.imageModelQualityOptions || []).forEach((opt) => {
      const optionEl = document.createElement("option");
      optionEl.value = opt.value;
      optionEl.textContent = opt.label;
      select.appendChild(optionEl);
    });
    select.value = window.PhotoAgentConfig.defaultImageModelQuality || select.value;
  }
  populateModelQualitySelect(els.imageModelQualitySelect);
  populateModelQualitySelect(els.apImageModelQualitySelect);

  function applyModelQualityChange(value) {
    const opt = findModelQualityOption(value);
    if (!opt) return;
    if (els.imageModelQualitySelect) els.imageModelQualitySelect.value = opt.value;
    if (els.apImageModelQualitySelect) els.apImageModelQualitySelect.value = opt.value;
    if (singleFlowState) { singleFlowState.imageModel = opt.model; singleFlowState.imageQuality = opt.quality; }
    if (batchSettings) { batchSettings.imageModel = opt.model; batchSettings.imageQuality = opt.quality; }
  }

  if (els.imageModelQualitySelect) {
    els.imageModelQualitySelect.addEventListener("change", () =>
      applyModelQualityChange(els.imageModelQualitySelect.value));
  }
  if (els.apImageModelQualitySelect) {
    els.apImageModelQualitySelect.addEventListener("change", () =>
      applyModelQualityChange(els.apImageModelQualitySelect.value));
  }

  // Aspect-ratio preference -- "auto" (default) leaves recommended_aspect_ratio
  // entirely up to the analysis step; "original" tells the analysis model (at
  // analyze time, via build_analysis_instruction on the backend) and the
  // edit-model API (size/imageConfig.aspectRatio) to keep the source photo's
  // own proportions. Upload-page only, read once when analysis starts
  // (startSubmit/startSubmitBatch) and carried unchanged into singleFlowState/
  // batchSettings for every later edit -- unlike model/quality/run-count,
  // this has no analyze-preview counterpart to mirror, since changing it
  // after analysis already ran would silently desync from what the analysis
  // step was actually told (see build_edit_prompt's
  // _aspect_ratio_pref_at_analysis handling on the backend).
  function populateAspectRatioSelect(select) {
    if (!select) return;
    select.innerHTML = "";
    (window.PhotoAgentConfig.aspectRatioOptions || []).forEach((opt) => {
      const optionEl = document.createElement("option");
      optionEl.value = opt.value;
      optionEl.textContent = opt.label;
      select.appendChild(optionEl);
    });
    select.value = window.PhotoAgentConfig.defaultAspectRatio || select.value;
  }
  populateAspectRatioSelect(els.imageAspectRatioSelect);

  // 生成張數 (single-image RUN count) can now be preset on the upload screen,
  // ahead of analysis -- keep it and the analysis-preview's own #ap-run-count
  // mirrored the same way quality/model already are, so whichever one the
  // user touches (before or after analyzing) carries over to the other.
  function applyRunCountChange(runCount) {
    const value = String(Math.max(1, parseInt(runCount, 10) || 1));
    if (els.imageRunCountSelect) els.imageRunCountSelect.value = value;
    if (els.apRunCountSelect) els.apRunCountSelect.value = value;
    if (els.runCountSelect) els.runCountSelect.value = value;
  }

  if (els.imageRunCountSelect) {
    els.imageRunCountSelect.addEventListener("change", () =>
      applyRunCountChange(els.imageRunCountSelect.value));
  }
  if (els.apRunCountSelect) {
    els.apRunCountSelect.addEventListener("change", () =>
      applyRunCountChange(els.apRunCountSelect.value));
  }
  if (els.runCountSelect) {
    els.runCountSelect.addEventListener("change", () =>
      applyRunCountChange(els.runCountSelect.value));
  }

  // Backend step timings are seconds (float) or null (step skipped);
  // addTiming/appendTimingRow expect ms or a non-number sentinel.
  function secToMs(seconds) {
    return typeof seconds === "number" ? seconds * 1000 : null;
  }

  function appendTimingRow(container, label, duration, { sub = false } = {}) {
    const row = document.createElement("div");
    row.className = sub ? "timing-row timing-sub" : "timing-row";
    const name = document.createElement("span");
    name.textContent = sub ? `↳ ${label}` : label;
    const val = document.createElement("span");
    val.className = "timing-value";
    val.textContent = typeof duration === "number" ? `${(duration / 1000).toFixed(1)}s` : "未執行";
    row.appendChild(name);
    row.appendChild(val);
    container.appendChild(row);
  }

  function populateTimingSection(timings = getTimings()) {
    if (!timingList || !timingDetails || !timings.length) return;
    timingList.innerHTML = "";
    let total = 0;
    timings.forEach(({ label, duration, children }) => {
      total += duration;
      appendTimingRow(timingList, label, duration);
      (children || []).forEach(({ label: childLabel, duration: childDuration }) => {
        appendTimingRow(timingList, childLabel, childDuration, { sub: true });
      });
    });
    const totalRow = document.createElement("div");
    totalRow.className = "timing-row timing-total";
    const totalName = document.createElement("span");
    totalName.textContent = "合計";
    const totalVal = document.createElement("span");
    totalVal.className = "timing-value";
    totalVal.textContent = `${(total / 1000).toFixed(1)}s`;
    totalRow.appendChild(totalName);
    totalRow.appendChild(totalVal);
    timingList.appendChild(totalRow);
    timingDetails.hidden = false;
  }

  // ── Pause-for-review between analyze and finish ────────────────────────
  let continueHandler = null;

  function clearContinueWait() {
    if (continueHandler) {
      els.viewResultsBtn.removeEventListener("click", continueHandler);
      continueHandler = null;
    }
    els.viewResultsWrap.hidden = true;
  }

  // Disables the analysis-preview panel's controls (recipe checklist, style
  // option checkboxes, quality/model/RUN-count selects, prompt textarea) plus
  // the back button, while finishSingleFlow's edit/QA job is in flight --
  // none of it applies to the already-submitted job, so leaving it live just
  // invites confusing edits. Re-enabled by showAnalysisPreview each time the
  // panel is (re)shown for a fresh round.
  function setAnalysisPreviewDisabled(disabled) {
    if (els.backToUploadBtn) els.backToUploadBtn.disabled = disabled;
    if (els.analysisPreview) {
      els.analysisPreview.querySelectorAll("input, select, textarea, button").forEach((el) => {
        el.disabled = disabled;
      });
    }
  }

  function waitForContinue() {
    return new Promise((resolve) => {
      clearContinueWait(); // drop any stale listener from an abandoned prior run
      continueHandler = () => {
        clearContinueWait();
        resolve();
      };
      els.viewResultsBtn.addEventListener("click", continueHandler);
      els.viewResultsWrap.hidden = false;
    });
  }

  // ── Inline analysis-review panel (results page) ─────────────────────────
  // Lets the user revise an existing result's recipe/style/prompt and re-run
  // without leaving the results page: #analysis-preview is physically moved
  // between its normal home (processing section, first-time flow) and
  // #results-analysis-slot (this flow), since both reuse the very same
  // #ap-* markup/IDs and can't have two live copies in the DOM at once.
  function moveAnalysisPreviewTo(container) {
    if (!els.analysisPreview || !container) return;
    if (els.analysisPreview.parentElement !== container) {
      container.appendChild(els.analysisPreview);
    }
  }

  function restoreAnalysisPreviewHome() {
    if (els.analysisPreviewHome && els.analysisPreview.previousElementSibling !== els.analysisPreviewHome) {
      els.analysisPreviewHome.after(els.analysisPreview);
    }
  }

  // #run-tabs-row moves under the photo (inside #results-canvas-col) while
  // the panel is open, so the image and its RUN previews read as one
  // aligned column next to the panel, instead of staying a full-width row
  // that now spans underneath both the (narrowed) photo and the panel.
  function moveRunTabsRowTo(container) {
    if (!els.runTabsRow || !container) return;
    if (els.runTabsRow.parentElement !== container) {
      container.appendChild(els.runTabsRow);
      updateRunTabsArrows();
    }
  }

  function restoreRunTabsRowHome() {
    if (els.runTabsRowHome && els.runTabsRow.previousElementSibling !== els.runTabsRowHome) {
      els.runTabsRowHome.after(els.runTabsRow);
      updateRunTabsArrows();
    }
  }

  function openInlineAnalysisPanel() {
    moveAnalysisPreviewTo(els.resultsAnalysisSlot);
    moveRunTabsRowTo(els.resultsCanvasCol);
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = false;
    els.analysisPreview.hidden = false;
    setAnalysisPreviewDisabled(false);
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.add("showing-analysis");
  }

  function closeInlineAnalysisPanel() {
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.remove("showing-analysis");
    els.analysisPreview.hidden = true;
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = true;
    restoreRunTabsRowHome();
  }

  let inlineAnalysisContinueHandler = null;
  let inlineAnalysisCancelHandler = null;

  function clearInlineAnalysisWait() {
    if (inlineAnalysisContinueHandler) {
      els.inlineAnalysisContinueBtn.removeEventListener("click", inlineAnalysisContinueHandler);
      inlineAnalysisContinueHandler = null;
    }
    if (inlineAnalysisCancelHandler) {
      els.inlineAnalysisCancelBtn.removeEventListener("click", inlineAnalysisCancelHandler);
      inlineAnalysisCancelHandler = null;
    }
  }

  // Resolves "continue" or "cancel" depending on which inline button the user
  // clicks -- same one-shot-listener shape as waitForContinue, just with two
  // possible outcomes instead of one.
  function waitForInlineAnalysisAction() {
    return new Promise((resolve) => {
      clearInlineAnalysisWait();
      inlineAnalysisContinueHandler = () => { clearInlineAnalysisWait(); resolve("continue"); };
      inlineAnalysisCancelHandler = () => { clearInlineAnalysisWait(); resolve("cancel"); };
      els.inlineAnalysisContinueBtn.addEventListener("click", inlineAnalysisContinueHandler);
      els.inlineAnalysisCancelBtn.addEventListener("click", inlineAnalysisCancelHandler);
    });
  }

  function showAnalysisPreview(analysis, preset) {
    restoreAnalysisPreviewHome();
    renderAnalysisPreview({ analysis, preset });
    if (els.analysisPreview) els.analysisPreview.hidden = false;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = false;
    // RUN count only applies to the single-image flow -- batch mode never
    // shows this panel for its own "analyzed" state at all anymore (only for
    // startInlineBatchReview's per-image revise, which sets this itself).
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    setAnalysisPreviewDisabled(false);
    procVisual.classList.add("done");
    if (procTitle) procTitle.textContent = "初步分析已完成";
    if (procSub) procSub.textContent = "已完成初步分析，請確認後繼續處理。";
  }

  // Some browsers/OSes don't map .heic/.heif to an image/* MIME type (file.type
  // often comes back empty for these), even though the backend (Pillow +
  // pillow-heif) decodes them fine -- fall back to the extension so a real
  // HEIC photo isn't rejected just because the browser didn't recognize it.
  function isSupportedImageFile(file) {
    if (!file) return false;
    if (file.type.startsWith("image/")) return true;
    return /\.(heic|heif)$/i.test(file.name || "");
  }

  function isHeicFile(file) {
    if (!file) return false;
    if (/^image\/hei[cf]/i.test(file.type || "")) return true;
    return /\.(heic|heif)$/i.test(file.name || "");
  }

  // libheif-js (vendored, LGPL-3.0) -- ~1.4MB, so it's loaded on demand the
  // first time a HEIC/HEIF file is actually selected rather than paying that
  // cost on every page load. Decodes on the main thread (no Web Worker), which
  // matters here: an earlier attempt with heic2any (MIT), which runs libheif
  // inside a Worker, silently hung forever on pages that aren't cross-origin
  // isolated (no SharedArrayBuffer) -- true of this app's plain Flask
  // responses, so it would have hung for real users too, not just in testing.
  let libheifLoadPromise = null;
  function loadLibheifFactory() {
    if (window.libheif) return Promise.resolve(window.libheif);
    if (!libheifLoadPromise) {
      libheifLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "libheif-bundle.js";
        script.onload = () => resolve(window.libheif);
        script.onerror = () => {
          libheifLoadPromise = null; // allow retrying on a later selection
          reject(new Error("HEIC 預覽元件載入失敗"));
        };
        document.head.appendChild(script);
      });
    }
    return libheifLoadPromise;
  }

  // The libheif() factory call does synchronous WASM instantiation (the
  // binary is embedded in the bundle, no extra fetch) -- cache the resulting
  // instance so converting several HEIC files in one batch selection doesn't
  // re-instantiate the WASM module per file.
  let libheifInstancePromise = null;
  function getLibheifInstance() {
    if (!libheifInstancePromise) {
      libheifInstancePromise = loadLibheifFactory().then((factory) => factory());
    }
    return libheifInstancePromise;
  }

  // Most desktop browsers (Chrome/Firefox/Edge) can't render HEIC/HEIF in an
  // <img> tag at all, even though the backend decodes it fine -- decode to a
  // canvas and re-encode as a JPEG blob purely for on-page preview/thumbnail
  // purposes. The original file (never this converted copy) is still what
  // gets uploaded/processed.
  async function previewableBlob(file) {
    if (!isHeicFile(file)) return file;
    try {
      const instance = await getLibheifInstance();
      const buffer = await file.arrayBuffer();
      const decoder = new instance.HeifDecoder();
      const images = decoder.decode(new Uint8Array(buffer));
      if (!images.length) throw new Error("HEIC 檔案沒有可解碼的影像");
      const image = images[0];
      const width = image.get_width();
      const height = image.get_height();

      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      const imageData = context.createImageData(width, height);

      await new Promise((resolve, reject) => {
        image.display(imageData, (displayData) => {
          if (!displayData) reject(new Error("HEIC 解碼失敗"));
          else resolve();
        });
      });
      context.putImageData(imageData, 0, 0);

      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
      return blob || file;
    } catch (error) {
      return file; // preview may not render, but selection still succeeds
    }
  }

  async function setSelectedFile(file) {
    hideError();
    if (!isSupportedImageFile(file)) {
      showError("請選擇 JPG、PNG、WebP 或 HEIC/HEIF 圖片。");
      return;
    }

    const token = ++fileSelectionToken;
    selectedFile = file;
    const blobForPreview = await previewableBlob(file);
    if (token !== fileSelectionToken) return; // superseded by a newer selection while converting

    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blobForPreview);

    els.dzPreviewImg.src = previewUrl;
    els.procThumb.src = previewUrl;
    els.originalImg.src = previewUrl;
    els.dropZone.hidden = true;
    els.dzPreview.hidden = false;

    const image = new Image();
    image.onload = () => {
      els.origDim.textContent = `${image.naturalWidth} x ${image.naturalHeight}`;
    };
    image.src = previewUrl;
  }

  // ── Download source + result image (+ JSON in --debug-ui mode) bundled as
  // one zip ────────────────────────────────────────────────────────────────
  function buildDownloadZipFilename() {
    const pad = (n) => String(n).padStart(2, "0");
    const now = new Date();
    const dateStr = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
    const timeStr = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    return `photoMuse-${dateStr}-${timeStr}.zip`;
  }

  const PROMPT_KEYS = ["photo_analysis", "user_instruction_review", "crop_validation", "image_edit", "quality_check"];

  function buildPromptsJson(prompts) {
    const ordered = {};
    PROMPT_KEYS.forEach((key) => {
      const text = prompts[key];
      // Split into an array of lines instead of one string -- a JSON string
      // can't contain a literal newline, so a multi-line prompt would
      // otherwise render as one line full of escaped "\n"s when opened in a
      // text editor. An array of lines pretty-prints with real line breaks.
      ordered[key] = typeof text === "string" ? text.split("\n") : null;
    });
    return JSON.stringify(ordered, null, 2);
  }

  // File names for one RUN's result inside a download zip -- numbered by the
  // RUN's own .run index (not its position within whatever subset is being
  // zipped), so the same RUN always gets the same file name whether it's
  // downloaded alone or as part of "all RUNs"/a whole batch.
  function resultFileNames(runNumber) {
    return {
      image: `result_${runNumber}.png`,
      json: `result_${runNumber}.json`,
      prompts: `result_${runNumber}_prompts.json`
    };
  }

  // Fetches the raw bytes needed for one image's download bundle -- shared
  // between the two layouts below so the network fetching isn't duplicated.
  async function fetchImageDownloadData({ file, path }, runs) {
    const sourceBuf = await file.arrayBuffer();
    const runData = await Promise.all(runs.map(async (run) => {
      // Fetch the image via our own backend, not the presigned S3/AIPoC URL --
      // that URL has no CORS headers, so a browser-side fetch() of it is
      // blocked even though the same URL displays fine in an <img> tag.
      const imageBuf = await fetch(resultImageUrl(run.jobId, { path })).then((response) => response.arrayBuffer());
      return { run, imageBuf };
    }));
    return { sourceBuf, runData };
  }

  // Normal mode: source + one result_N.png per RUN, flat under `prefix` --
  // no per-RUN subfolder, no JSON. Everyday users just want the pictures.
  function buildFlatImageFiles({ file, prefix }, sourceBuf, runData) {
    const files = [{ name: prefix + (file.name || "source.jpg"), data: new Uint8Array(sourceBuf) }];
    runData.forEach(({ run, imageBuf }) => {
      files.push({ name: prefix + resultFileNames(run.run || 1).image, data: new Uint8Array(imageBuf) });
    });
    return files;
  }

  // --debug-ui mode: source at `prefix`, each RUN under its own `run_N/`
  // subfolder alongside the raw result.json/prompts.json -- the original
  // nested layout, kept for the internal analysis/QA data this mode exists
  // to expose.
  function buildNestedImageFiles({ file, prefix }, sourceBuf, runData) {
    const files = [{ name: prefix + (file.name || "source.jpg"), data: new Uint8Array(sourceBuf) }];
    runData.forEach(({ run, imageBuf }) => {
      const runPrefix = `${prefix}run_${run.run || 1}/`;
      const jsonBytes = new TextEncoder().encode(JSON.stringify(run.result, null, 2));
      const promptsBytes = new TextEncoder().encode(buildPromptsJson(run.result.analysis?.prompts || {}));
      files.push(
        { name: runPrefix + "result.png", data: new Uint8Array(imageBuf) },
        { name: runPrefix + "result.json", data: jsonBytes },
        { name: runPrefix + "prompts.json", data: promptsBytes }
      );
    });
    return files;
  }

  // Builds one image's full set of download entries -- flat (images only)
  // in normal use, nested with the raw result/prompt JSON in --debug-ui mode.
  async function buildImageFiles({ file, path, prefix = "" }, runs) {
    const { sourceBuf, runData } = await fetchImageDownloadData({ file, path }, runs);
    return isDebugUiActive
      ? buildNestedImageFiles({ file, prefix }, sourceBuf, runData)
      : buildFlatImageFiles({ file, prefix }, sourceBuf, runData);
  }

  function lastResultRuns() {
    if (Array.isArray(lastResult.runs) && lastResult.runs.length) return lastResult.runs;
    return [{ run: 1, result: lastResult.resultText, jobId: lastResult.jobId, status: "done" }];
  }

  async function buildResultZip() {
    const runs = lastResultRuns();
    const current = runs.find((run) => run.jobId === lastResult.jobId) || runs[0];
    return window.PhotoAgentZip.createZip(
      await buildImageFiles({ file: lastResult.file, path: lastResult.path }, [current])
    );
  }

  async function buildAllRunsZip() {
    const doneRuns = lastResultRuns().filter((run) => run.status === "done");
    return window.PhotoAgentZip.createZip(
      await buildImageFiles({ file: lastResult.file, path: lastResult.path }, doneRuns)
    );
  }

  // The all-RUNs download link only makes sense once there's more than one
  // finished RUN to bundle -- hidden otherwise so it doesn't duplicate the
  // single-preview download link for the common single-RUN case.
  function updateDownloadLinks(runs) {
    // download-link only makes sense once a real edited result exists to zip
    // up -- lastResult is set right before every call to this function (and
    // cleared in reset()), so its presence is the single source of truth.
    els.downloadLink.hidden = !lastResult;
    if (!els.downloadAllRunsLink) return;
    const doneCount = (runs || []).filter((run) => run.status === "done").length;
    els.downloadAllRunsLink.hidden = doneCount <= 1;
    // Icon-only button -- the RUN count lives in the hover hint (title/
    // aria-label) instead of a permanently visible label.
    if (doneCount > 1) {
      const hint = `全部下載（${doneCount} 個 RUN）`;
      els.downloadAllRunsLink.title = hint;
      els.downloadAllRunsLink.setAttribute("aria-label", hint);
    }
  }

  async function downloadZip(buildZip, filename) {
    try {
      const zipBlob = await buildZip();
      const url = URL.createObjectURL(zipBlob);
      const tempLink = document.createElement("a");
      tempLink.href = url;
      tempLink.download = filename;
      document.body.appendChild(tempLink);
      tempLink.click();
      tempLink.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      showError("打包下載失敗，請稍後再試。");
    }
  }

  function handleDownloadClick(event) {
    event.preventDefault();
    if (!lastResult) return;
    downloadZip(buildResultZip, buildDownloadZipFilename());
  }

  function handleDownloadAllRunsClick(event) {
    event.preventDefault();
    if (!lastResult) return;
    downloadZip(buildAllRunsZip, buildDownloadZipFilename());
  }

  // Batch counterpart of setSelectedFile: filter to images (same rule/message),
  // build one record per file, and render the multi-thumbnail strip.
  async function setSelectedFileBatch(files) {
    hideError();
    resetBatchState();
    const images = files.filter((file) => isSupportedImageFile(file));
    if (!images.length) {
      showError("請選擇 JPG、PNG、WebP 或 HEIC/HEIF 圖片。");
      return;
    }
    if (images.length !== files.length) {
      showError("已略過非圖片檔案。");
    }

    const token = ++fileSelectionToken;
    selectedFile = null;
    const previewBlobs = await Promise.all(images.map((file) => previewableBlob(file)));
    if (token !== fileSelectionToken) return; // superseded by a newer selection while converting

    batchRecords = images.map((file, i) => ({
      localId: `f${i}`,
      file,
      previewUrl: URL.createObjectURL(previewBlobs[i]),
      // pending -> analyzing -> analyzed -> processing -> done / error
      status: "pending",
      analysis: null,       // step-1 result (shown in the step-2 review panel)
      analyzeJobId: null,   // local path only: the analyze job, reused by continue
      options: null,        // per-image style options {pose,angle,lighting,size} (UI-only)
      optionsTouched: {},   // per-category: did the user actually change this image's own radio (vs. just viewing the batch-inherited default)
      recipeChecked: null,  // per-image recipe checklist state (UI-only)
      userPrompt: "",       // per-image 補充指令 (entered in the step-2 review)
      analyzeTimings: [],   // step-1 timing rows, merged into the detail view
      jobId: null,          // step-2 edit job/task id
      result: null,
      imageUrl: null,
      error: null,
      timings: []
    }));

    els.dropZone.hidden = true;
    els.dzPreview.hidden = true;
    els.dzMulti.hidden = false;
    renderDzMultiThumbs();
  }

  // Rebuilds the multi-file thumbnail strip (with a per-thumb remove button)
  // from the current batchRecords. Called on initial selection and again
  // whenever a thumbnail is removed.
  function renderDzMultiThumbs() {
    els.dzMulti.innerHTML = "";
    batchRecords.forEach((r) => {
      const thumb = document.createElement("div");
      thumb.className = "dz-thumb";
      const im = document.createElement("img");
      im.src = r.previewUrl;
      im.alt = r.file.name;
      thumb.appendChild(im);

      const name = document.createElement("span");
      name.className = "dz-thumb-name";
      name.textContent = r.file.name;
      thumb.appendChild(name);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "dz-thumb-remove";
      removeBtn.setAttribute("aria-label", "移除照片");
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        removeBatchFile(r);
      });
      thumb.appendChild(removeBtn);

      els.dzMulti.appendChild(thumb);
    });
    const count = document.createElement("p");
    count.className = "dz-count";
    count.textContent = `已選擇 ${batchRecords.length} 張`;
    els.dzMulti.appendChild(count);
  }

  // Removes one photo from the pending batch selection (only reachable
  // before Start is pressed, while dz-multi is showing). Falls back to the
  // single-photo preview if exactly one image remains, or to the empty drop
  // zone if none remain.
  function removeBatchFile(record) {
    const index = batchRecords.indexOf(record);
    if (index === -1) return;
    batchRecords.splice(index, 1);
    if (record.previewUrl) URL.revokeObjectURL(record.previewUrl);

    if (!batchRecords.length) {
      resetApp();
      return;
    }
    if (batchRecords.length === 1) {
      const soleFile = batchRecords[0].file;
      resetBatchState();
      setSelectedFile(soleFile);
      return;
    }
    renderDzMultiThumbs();
  }

  // Drop batch state and restore the results view to its single-image default,
  // so the untouched single flow always starts from a known layout.
  function resetBatchState() {
    batchRecords.forEach((r) => { if (r.previewUrl) URL.revokeObjectURL(r.previewUrl); });
    batchRecords = [];
    batchSettings = null;
    currentBatchRecord = null;
    // Any prior single-image run's "回到流程" state refers to a different
    // photo (or no longer applies once batch mode starts) -- drop it.
    singleFlowState = null;
    lastSingleSelections = null;
    if (els.backToProcessBtn) els.backToProcessBtn.hidden = true;
    if (els.dzMulti) { els.dzMulti.hidden = true; els.dzMulti.innerHTML = ""; }
    if (els.batchSidebarList) els.batchSidebarList.innerHTML = "";
    if (els.batchSidebar) els.batchSidebar.hidden = true;
    // Undo whatever hideAllBatchMainViews left mid-batch, so a subsequent
    // single-image run (which never touches these) isn't left with the
    // canvas/run-tabs hidden from a prior batch session.
    if (els.batchMainLoading) { els.batchMainLoading.hidden = true; els.batchMainLoading.classList.remove("is-error", "is-analyzed"); }
    if (els.batchReviewContinueBtn) els.batchReviewContinueBtn.hidden = true;
    restoreAnalysisPreviewHome();
    if (els.resultsCanvasRow) { els.resultsCanvasRow.hidden = false; els.resultsCanvasRow.classList.remove("showing-analysis"); }
    if (els.runTabsRow) els.runTabsRow.hidden = false;
    if (els.runTabs) { els.runTabs.hidden = true; els.runTabs.innerHTML = ""; }
    if (els.runTabsIndex) els.runTabsIndex.hidden = true;
    currentRunTabsIndex = null;
    currentRunTabsOnSelect = null;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    if (els.batchProgress) els.batchProgress.hidden = true;
    if (els.batchDownloadAllBtn) els.batchDownloadAllBtn.hidden = true;
    if (els.batchContinueAllBtn) els.batchContinueAllBtn.hidden = true;
    if (els.batchPromptRow) els.batchPromptRow.hidden = true;
    if (els.batchPromptInput) els.batchPromptInput.value = "";
    if (els.batchOptionsRow) { els.batchOptionsRow.hidden = true; els.batchOptionsRow.open = false; }
    renderBatchStyleOptions();
    updateBatchOptionsLocks(); // batchRecords is now empty, so this clears every category's lock/toggle
  }

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return "無法連線到 Photo Master 伺服器，請確認後端服務是否已啟動。";
    }
    // OpenAI's images/edits API rejects requests its safety system flags --
    // in practice this fires reliably for photos containing a recognizable
    // copyrighted/trademarked character (branded plush toys, mascots, etc.),
    // a platform content policy we can't bypass, not a transient failure
    // worth retrying with the same photo.
    if (/rejected by the safety system/i.test(error.message || "")) {
      return "AI 修圖模型拒絕處理這張照片——這通常是因為照片中包含可辨識的受著作權保護角色或商標（例如卡通玩偶、品牌吉祥物等），基於平台政策無法編輯這類內容。請嘗試更換照片，或改用其他修圖模型再試一次。";
    }
    return error.message || "發生未知錯誤。";
  }

  // Analyze+edit as ONE backend job (submitJob, no analysis param -> the
  // worker runs analyze_stage then finish_stage itself, see job_processor.py)
  // instead of the two-stage analyzeJob+continueJob dance. Used only by the
  // "auto" (skip-review) flows on the local path, where there's nothing to
  // review/adjust between the two stages anyway -- the analysis-preview DOM
  // is never shown, so its recipe/style defaults are always what gets
  // applied. Collapsing to one job means one poll instead of two, so a phone
  // sleeping mid-flow only ever has to resume across a single wait instead of
  // getting stuck between two separately-submitted jobs.
  // AIPoC's own analyze already runs synchronously (no poll at all), so it
  // never had this problem and isn't touched here; the manual review flow
  // still needs the real two-stage analyze->adjust->continue path.
  async function runCombinedEditRun({ file, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt = "", onStage }) {
    const jobId = await submitJob({ file, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, path: "local" });
    await pollJobStatus(jobId, {
      path: "local",
      onStatus: (data) => { if (onStage && data.stage) onStage(data.stage); }
    });
    const { image_url: imageUrl, result } = await getJobResult(jobId, { path: "local" });
    return { jobId, imageUrl, result };
  }

  async function startSubmit({ skipReview = false } = {}) {
    hideError();
    if (batchRecords.length > 1) {
      await startSubmitBatch(skipReview);
      return;
    }
    if (!selectedFile) {
      showError("請先上傳一張照片。");
      return;
    }

    const presetKey = "natural";
    const preset = presets[presetKey] || presets.natural;
    const modelQualityOpt = findModelQualityOption(els.imageModelQualitySelect?.value) || {};
    const imageQuality = modelQualityOpt.quality || "medium";
    const imageModel = modelQualityOpt.model || "gpt-image-2";
    const aspectRatio = els.imageAspectRatioSelect?.value || window.PhotoAgentConfig.defaultAspectRatio || "auto";
    const aiPhotoEditing = els.aiEditToggle.checked;
    const runQa = els.runQaToggle ? els.runQaToggle.checked : false;
    const processingPath = els.processingPathSelect?.value || "local";

    els.startButton.disabled = true;
    if (els.startDirectBtn) els.startDirectBtn.disabled = true;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    resetSteps(); // clears completedTimings so each run's total starts fresh
    clearContinueWait();
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = true;
    if (els.promptInput) els.promptInput.value = "";
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    if (procTitle) procTitle.textContent = "正在分析照片";
    if (procSub) procSub.textContent = "AI 正在分析照片，請稍候…";
    showSection("processing");

    try {
      let stepStart = Date.now();
      let analysis = null;
      let analyzeJobId; // only set for the "local" two-stage path, needed by continueJob

      // Also collected into analyzeTimings (not just pushed via addTiming) so
      // the RUN-count > 1 path can prepend Step 2's time onto each RUN's own
      // timings -- that path renders a run's timings array directly instead
      // of the global getTimings() timeline addTiming feeds.
      const analyzeTimings = [];
      const recordAnalyzeTiming = (label, duration, children) => {
        analyzeTimings.push({ label, duration, children });
        addTiming(label, duration, children);
      };

      // Skip-review + local path: analyze and edit run as one combined job
      // (runCombinedEditRun), so there's no separate analyze step to run
      // here at all -- finishSingleFlow fills singleFlowState.analysis in
      // from the combined job's own result once it completes.
      const useCombinedJob = skipReview && processingPath === "local";

      if (!useCombinedJob) {
        if (processingPath === "aipoc") {
          const analyzeData = await analyzeAipoc({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing });
          analysis = analyzeData.analysis;
          const t = analysis.timings || {};
          recordAnalyzeTiming("初步分析", Date.now() - stepStart, [
            { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) }
          ]);
        } else {
          analyzeJobId = await analyzeJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing });
          recordAnalyzeTiming("送出分析任務", Date.now() - stepStart);

          stepStart = Date.now();
          await pollJobStatus(analyzeJobId, { path: "local" });
          const analyzeWaitDuration = Date.now() - stepStart;

          stepStart = Date.now();
          const analyzeResult = await getAnalyzeResult(analyzeJobId);
          analysis = analyzeResult.result.analysis;
          const t = analysis.timings || {};
          recordAnalyzeTiming("分析中", analyzeWaitDuration, [
            { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) }
          ]);
          recordAnalyzeTiming("取得分析結果", Date.now() - stepStart);
        }
      }

      singleFlowState = { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, processingPath, analyzeTimings, useCombinedJob };

      if (useCombinedJob) {
        await finishSingleFlow();
      } else if (skipReview) {
        // Straight through, no stop: still populate the analysis-preview DOM
        // (finishSingleFlow reads its recipe checkboxes/style picks/prompt
        // straight from there) but never show the panel or wait for a click.
        renderAnalysisPreview({ analysis, preset });
        await finishSingleFlow();
      } else {
        showAnalysisPreview(analysis, preset);
        await waitForContinue();
        await finishSingleFlow();
      }
    } catch (error) {
      showSection("upload");
      showError(friendlyError(error));
    } finally {
      els.startButton.disabled = false;
      if (els.startDirectBtn) els.startDirectBtn.disabled = false;
      clearContinueWait();
      if (els.analysisPreview) els.analysisPreview.hidden = true;
    }
  }

  // Steps 3-6 for the single-image flow, reusable both for the first run
  // (right after analyze, called from startSubmit) and for a later
  // re-adjustment run (called from backToProcessFromResults) -- reads
  // singleFlowState (set once by analyze) plus whatever the user currently
  // has checked/typed in the analysis-preview DOM.
  async function finishSingleFlow() {
    const { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, processingPath, analyzeTimings, useCombinedJob } = singleFlowState;
    const selections = readStyleSelectionsFromDom();
    const userPrompt = els.promptInput.value.trim();
    lastSingleSelections = { userPrompt, ...selections };
    // No analysis yet in combined mode -- nothing to fold recipe/style
    // adjustments into, since the combined job computes its own from scratch.
    const effectiveAnalysis = useCombinedJob ? null : buildEffectiveAnalysis(analysis, selections);
    const runCount = Math.max(1, parseInt(els.apRunCountSelect?.value, 10) || 1);

    setAnalysisPreviewDisabled(true);
    procVisual.classList.remove("done");
    if (procTitle) procTitle.textContent = useCombinedJob ? "正在分析照片" : (aiPhotoEditing ? "AI 正在修圖" : "正在處理照片");
    if (procSub) {
      procSub.textContent = useCombinedJob
        ? "AI 正在分析照片，請稍候…"
        : (aiPhotoEditing
            ? `AI 正在使用 ${window.PhotoAgentConfig.imageModels[imageModel] || imageModel} 修圖，請稍候…`
            : "正在進行本機影像處理，請稍候…");
    }
    // Live progress messaging for a combined job, driven by the backend's
    // reported stage (job_processor.py's set_job_info(..., results={"stage": ...})
    // calls) -- keeps the same two-phase "分析中 -> 修圖中" wording the old
    // two-job flow showed, even though it's now one job/one poll.
    const onCombinedStage = (stage) => {
      if (stage !== "edit") return;
      if (procTitle) procTitle.textContent = aiPhotoEditing ? "AI 正在修圖" : "正在處理照片";
      if (procSub) {
        procSub.textContent = aiPhotoEditing
          ? `AI 正在使用 ${window.PhotoAgentConfig.imageModels[imageModel] || imageModel} 修圖，請稍候…`
          : "正在進行本機影像處理，請稍候…";
      }
    };

    if (runCount === 1) {
      // Single RUN: unchanged from before RUN-count support existed, so the
      // common case keeps its exact step timeline.
      singleRuns = null;
      singleMirrorIndex = null;

      let stepStart = Date.now();
      let finalJobId, imageUrl, result;

      if (useCombinedJob) {
        const combined = await runCombinedEditRun({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, onStage: onCombinedStage });
        finalJobId = combined.jobId;
        imageUrl = combined.imageUrl;
        result = combined.result;
        addTiming("分析並修圖", Date.now() - stepStart);
        // Remember this job + its analysis so a later re-run (回到流程) goes
        // through the normal two-stage continueJob path instead of combining
        // again -- continueJob only needs a job id whose Redis record has
        // valid srcKeys, which this combined job's does.
        singleFlowState.analysis = result.analysis;
        singleFlowState.analyzeJobId = finalJobId;
        singleFlowState.useCombinedJob = false;
      } else {
        if (processingPath === "aipoc") {
          finalJobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, analysis: effectiveAnalysis, path: "aipoc" });
        } else {
          finalJobId = await continueJob(analyzeJobId, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt });
        }
        addTiming("提交後續處理", Date.now() - stepStart);

        stepStart = Date.now();
        await pollJobStatus(finalJobId, { path: processingPath });
        const finishWaitDuration = Date.now() - stepStart;

        stepStart = Date.now();
        ({ image_url: imageUrl, result } = await getJobResult(finalJobId, { path: processingPath }));
        const t = (result["extra-info"] || {}).timings || {};
        addTiming("AI 修圖中", finishWaitDuration, [
          { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
          { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
          { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
          { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
        ]);
        addTiming("取得結果", Date.now() - stepStart);
      }

      const extraInfo = result["extra-info"] || {};
      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : "本機處理";
      const usedModelLabel = isDebugUiActive
      ? `${window.PhotoAgentConfig.analysisModel} + ${usedImageModelLabel}`
      : "AI 智能修圖";

      const data = {
        image: imageUrl,
        model: usedModelLabel,
        usage: result.usage,
        qualityCheck: result.quality_check,
        localOnly: !aiPhotoEditing,
        retryCount: 0
      };

      lastResult = { file: selectedFile, resultText: result, jobId: finalJobId, path: processingPath };
      updateDownloadLinks();
      renderResult({ data, analysis: result.analysis, preset, imageModel: data.model });
      populateTimingSection();
      if (els.runTabs) { els.runTabs.hidden = true; els.runTabs.innerHTML = ""; }
      if (els.runTabsIndex) els.runTabsIndex.hidden = true;
      currentRunTabsIndex = null;
      currentRunTabsOnSelect = null;
    } else {
      // N independent RUNs from the same analysis, same pattern as batch's
      // continueBatchRecords/editBatchItem -- each RUN is its own job, run
      // concurrently, and the finished report mirrors the first one done
      // (switchable via the RUN tabs, same UI batch uses).
      singleRuns = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing",
        imageUrl: null, result: null, timings: [], error: null
      }));
      singleMirrorIndex = null;

      let doneCount = 0;
      const updateRunProgress = () => {
        if (procSub) {
          procSub.textContent = aiPhotoEditing
            ? `AI 正在使用 ${window.PhotoAgentConfig.imageModels[imageModel] || imageModel} 修圖中（已完成 ${doneCount}/${runCount} RUN）…`
            : `本機影像處理中（已完成 ${doneCount}/${runCount} RUN）…`;
        }
      };
      updateRunProgress();

      await runBatchPool(singleRuns, async (run) => {
        await runSingleItem(run, { analyzeJobId, effectiveAnalysis, userPrompt, presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, processingPath, analyzeTimings, useCombinedJob });
        doneCount += 1;
        updateRunProgress();
      }, POLL_CONCURRENCY);

      if (!singleRuns.some((r) => r.status === "done")) {
        throw new Error(singleRuns.find((r) => r.error)?.error || "所有 RUN 皆失敗，請稍後再試。");
      }
      // Combined mode: remember the first successfully-done run's analysis +
      // job id, same reasoning as the runCount===1 branch above, so a later
      // re-run goes through the normal two-stage continueJob path.
      if (useCombinedJob) {
        const doneRun = singleRuns.find((r) => r.status === "done");
        if (doneRun) {
          singleFlowState.analysis = doneRun.result.analysis;
          singleFlowState.analyzeJobId = doneRun.jobId;
          singleFlowState.useCombinedJob = false;
        }
      }
      showSingleResult();
    }

    if (els.backToProcessBtn) els.backToProcessBtn.hidden = false;
    showSection("results");
  }

  // One RUN of steps 3-6 for the single-image flow's RUN-count > 1 path --
  // mirrors editBatchItem, but against selectedFile/singleFlowState instead
  // of a batch record.
  async function runSingleItem(run, { analyzeJobId, effectiveAnalysis, userPrompt, presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, processingPath, analyzeTimings, useCombinedJob }) {
    try {
      let stepStart = Date.now();

      if (useCombinedJob) {
        const combined = await runCombinedEditRun({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt });
        const combinedMs = Date.now() - stepStart;
        run.jobId = combined.jobId;
        run.status = "done";
        run.imageUrl = combined.imageUrl;
        run.result = combined.result;
        const t = (combined.result["extra-info"] || {}).timings || {};
        run.timings = [
          { label: "分析並修圖", duration: combinedMs, children: [
            { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) },
            { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
            { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
            { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
            { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
          ] }
        ];
        return;
      }

      if (processingPath === "aipoc") {
        run.jobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, analysis: effectiveAnalysis, path: "aipoc" });
      } else {
        run.jobId = await continueJob(analyzeJobId, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt });
      }
      const submitMs = Date.now() - stepStart;

      stepStart = Date.now();
      await pollJobStatus(run.jobId, { path: processingPath });
      const pollMs = Date.now() - stepStart;

      stepStart = Date.now();
      const { image_url: imageUrl, result } = await getJobResult(run.jobId, { path: processingPath });
      const fetchMs = Date.now() - stepStart;

      run.status = "done";
      run.imageUrl = imageUrl;
      run.result = result;
      const t = (result["extra-info"] || {}).timings || {};
      run.timings = [
        ...(analyzeTimings || []),
        { label: "提交後續處理", duration: submitMs },
        { label: "AI 修圖中", duration: pollMs, children: [
          { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
          { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
          { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
          { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
        ] },
        { label: "取得結果", duration: fetchMs }
      ];
    } catch (error) {
      run.status = "error";
      run.error = friendlyError(error);
    }
  }

  // Renders whichever singleRuns entry is mirrored (first done, or the
  // user's chosen tab) into the shared results DOM -- the RUN-count > 1
  // counterpart of finishSingleFlow's single-RUN render tail.
  function showSingleResult() {
    const runs = singleRuns || [];
    const pick = (singleMirrorIndex != null && runs[singleMirrorIndex])
      ? runs[singleMirrorIndex]
      : (runs.find((x) => x.status === "done") || runs[0]);
    if (!pick || pick.status !== "done") return;

    const { preset, aiPhotoEditing } = singleFlowState;
    const result = pick.result;
    const extraInfo = result["extra-info"] || {};
    const usedImageModelLabel = extraInfo.image_model
      ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
      : "本機處理";
    const usedModelLabel = isDebugUiActive
      ? `${window.PhotoAgentConfig.analysisModel} + ${usedImageModelLabel}`
      : "AI 智能修圖";

    const data = {
      image: pick.imageUrl,
      model: usedModelLabel,
      usage: result.usage,
      qualityCheck: result.quality_check,
      localOnly: !aiPhotoEditing,
      retryCount: 0
    };

    lastResult = { file: selectedFile, resultText: result, jobId: pick.jobId, path: singleFlowState.processingPath, runs };
    updateDownloadLinks(runs);
    renderResult({ data, analysis: result.analysis, preset, imageModel: data.model });
    populateTimingSection(pick.timings);
    renderSingleRunTabs();
  }

  // "檢視分析結果": lets the user come back to the recipe checklist/style
  // options/prompt (restored from their last submission) and regenerate,
  // without leaving the results page -- the panel slides in beside the
  // photo canvas instead of navigating to the processing section.
  async function backToProcessFromResults() {
    if (!singleFlowState) return;
    hideError();
    clearInlineAnalysisWait();
    if (timingDetails) timingDetails.hidden = true;
    resetSteps(); // clears completedTimings so this run's timings don't append to the previous run's

    renderAnalysisPreview({ analysis: singleFlowState.analysis, preset: singleFlowState.preset });
    if (lastSingleSelections) {
      if (els.promptInput) els.promptInput.value = lastSingleSelections.userPrompt || "";
      applyStyleSelections(lastSingleSelections);
    }
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    openInlineAnalysisPanel();

    const action = await waitForInlineAnalysisAction();
    closeInlineAnalysisPanel();
    if (action === "cancel") return;

    setAnalysisPreviewDisabled(true);
    if (els.backToProcessBtn) els.backToProcessBtn.disabled = true;
    if (els.canvasLoading) els.canvasLoading.hidden = false;
    try {
      await finishSingleFlow();
    } catch (error) {
      showError(friendlyError(error));
    } finally {
      if (els.canvasLoading) els.canvasLoading.hidden = true;
      if (els.backToProcessBtn) els.backToProcessBtn.disabled = false;
    }
  }

  // ── Batch step 1: analyze every image, then show the review grid ─────────
  async function startSubmitBatch(skipReview = false) {
    // The batch-wide 生成張數 control (#run-count, shown later in the sidebar)
    // starts out matching whatever the user picked on the upload page
    // (#image-run-count) -- otherwise it's left at its own independent
    // default (1) and batch-auto mode (which never pauses at the sidebar for
    // the user to notice/adjust it) would silently run everyone once instead
    // of the count actually configured before clicking Run.
    if (els.runCountSelect && els.imageRunCountSelect) {
      els.runCountSelect.value = els.imageRunCountSelect.value;
    }
    const presetKey = "natural";
    const modelQualityOpt = findModelQualityOption(els.imageModelQualitySelect?.value) || {};
    batchSettings = {
      presetKey,
      preset: presets[presetKey] || presets.natural,
      // Prompt is entered per-image (or batch-level) at step 2, not at upload.
      imageQuality: modelQualityOpt.quality || "medium",
      imageModel: modelQualityOpt.model || "gpt-image-2",
      aspectRatio: els.imageAspectRatioSelect?.value || window.PhotoAgentConfig.defaultAspectRatio || "auto",
      aiPhotoEditing: els.aiEditToggle.checked,
      runQa: els.runQaToggle ? els.runQaToggle.checked : false,
      path: els.processingPathSelect?.value || "local"
    };

    els.startButton.disabled = true;
    if (els.startDirectBtn) els.startDirectBtn.disabled = true;
    resetSteps(); // clears completedTimings so each run's total starts fresh
    batchRecords.forEach((r) => {
      r.status = "analyzing";
      r.analysis = null;
      r.analyzeJobId = null;
      r.analyzeTimings = [];
      r.jobId = null;
      r.result = null;
      r.imageUrl = null;
      r.error = null;
      r.timings = [];
      r.runs = null;
      r.mirrorIndex = null;
    });

    els.batchProgress.hidden = false;
    els.batchSidebar.hidden = false;
    currentBatchRecord = batchRecords[0] || null;
    // Before rendering: renderBatchMain measures the sidebar's real on-page
    // position (updateBatchSidebarMaxHeight) to cap its height, which reads
    // as all-zero while the section is still display:none.
    showSection("results");
    renderBatchSidebarList();
    renderBatchMain(currentBatchRecord); // shows record 1's "analyzing" placeholder immediately
    updateBatchProgress();

    const analyzeArgs = {
      files: batchRecords.map((r) => r.file),
      preset: presetKey,
      imageQuality: batchSettings.imageQuality,
      imageModel: batchSettings.imageModel,
      aspectRatio: batchSettings.aspectRatio,
      aiPhotoEditing: batchSettings.aiPhotoEditing
    };

    try {
      if (skipReview && batchSettings.path === "local") {
        // Batch-auto + local path: analyze and edit run as one combined job
        // per RUN, per image (runCombinedEditRun) -- skips the separate
        // analyzeJobBatch/analyzeBatchItem phase below entirely, since
        // batch-auto never pauses at the sidebar for the user to review/
        // adjust between the two stages anyway.
        await runCombinedBatchAuto();
        return;
      }

      if (batchSettings.path === "aipoc") {
        // AIPoC analyze is synchronous local -- one call returns all analyses.
        let entries;
        try {
          entries = await analyzeAipocBatch(analyzeArgs);
        } catch (error) {
          batchRecords.forEach((r) => { r.status = "error"; r.error = friendlyError(error); });
          renderBatchSidebarList();
          renderBatchMain(currentBatchRecord);
          updateBatchProgress();
          showError(friendlyError(error));
          return;
        }
        batchRecords.forEach((r, i) => {
          const entry = entries[i];
          if (entry && entry.analysis) {
            r.analysis = entry.analysis;
            const t = entry.analysis.timings || {};
            r.analyzeTimings = [{ label: "初步分析", duration: secToMs(t["Step 2 - photo_analysis"]) }];
            r.status = "analyzed";
          } else {
            r.status = "error";
            r.error = (entry && entry.error) || "分析失敗";
          }
          updateBatchSidebarItem(r);
          refreshIfSelected(r);
        });
      } else {
        // Local analyze is queued -- fan out job ids, then poll each.
        let entries;
        try {
          entries = await analyzeJobBatch(analyzeArgs);
        } catch (error) {
          batchRecords.forEach((r) => { r.status = "error"; r.error = friendlyError(error); });
          renderBatchSidebarList();
          renderBatchMain(currentBatchRecord);
          updateBatchProgress();
          showError(friendlyError(error));
          return;
        }
        batchRecords.forEach((r, i) => {
          const entry = entries[i];
          if (entry && entry.job_id) {
            r.analyzeJobId = entry.job_id;
          } else {
            r.status = "error";
            r.error = (entry && entry.error) || "送出分析失敗";
          }
          updateBatchSidebarItem(r);
          refreshIfSelected(r);
        });
        const analyzing = batchRecords.filter((r) => r.status === "analyzing" && r.analyzeJobId);
        await runBatchPool(analyzing, analyzeBatchItem, POLL_CONCURRENCY);
      }

      updateBatchProgress();
      if (skipReview) {
        // Straight through, no stop: same as clicking "全部繼續處理" myself,
        // so every analyzed image goes straight to editing with its default
        // recipe/style options (no per-image review ever happened to set
        // overrides). Records that failed to analyze are simply excluded --
        // continueBatchRecords already filters to status === "analyzed".
        await continueBatchRecords(analyzedBatchRecords());
      } else {
        updateBatchHeaderControls();
      }
    } finally {
      els.startButton.disabled = false;
      if (els.startDirectBtn) els.startDirectBtn.disabled = false;
    }
  }

  // Batch-auto (skip-review) + local path: analyze and edit run as one
  // combined job per RUN, per image (runCombinedEditRun) -- skips the
  // separate analyzeJobBatch/analyzeBatchItem phase entirely, since
  // batch-auto never pauses at the sidebar for the user to review/adjust
  // between the two stages anyway.
  async function runCombinedBatchAuto() {
    const runCount = Math.max(1, parseInt(els.runCountSelect?.value, 10) || 1);
    batchRecords.forEach((r) => {
      r.runs = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing", imageUrl: null, result: null, timings: [], error: null
      }));
      r.mirrorIndex = null;
    });

    const workItems = batchRecords.flatMap((r) => r.runs.map((run) => ({ record: r, run })));
    await runBatchPool(workItems, analyzeAndEditBatchItem, POLL_CONCURRENCY);

    updateBatchProgress();
    updateBatchHeaderControls();
  }

  // Combined analyze+edit for one (record, run) pair -- batch-auto
  // counterpart of runSingleItem's combined-job branch. Only used for a
  // batch record's very first edit under skip-review + local path;
  // per-image revise (startInlineBatchReview -> editBatchItem) always uses
  // the normal continueJob path, reusing record.analysis/record.analyzeJobId
  // populated below once the first run for a record completes.
  async function analyzeAndEditBatchItem({ record, run }) {
    try {
      const userPrompt = effectivePrompt(record);
      const stepStart = Date.now();
      const combined = await runCombinedEditRun({
        file: record.file,
        preset: batchSettings.presetKey,
        imageQuality: batchSettings.imageQuality,
        imageModel: batchSettings.imageModel,
        aspectRatio: batchSettings.aspectRatio,
        aiPhotoEditing: batchSettings.aiPhotoEditing,
        runQa: batchSettings.runQa,
        userPrompt,
        onStage: (stage) => {
          // First of this record's RUNs to reach "edit" flips the whole
          // record from "analyzing" to "processing" -- later RUNs' callbacks
          // see the status already changed and skip the redundant re-render.
          if (stage === "edit" && record.status === "analyzing") {
            record.status = "processing";
            updateBatchSidebarItem(record);
            refreshIfSelected(record);
            updateBatchProgress();
          }
        }
      });
      const combinedMs = Date.now() - stepStart;
      run.jobId = combined.jobId;
      run.status = "done";
      run.imageUrl = combined.imageUrl;
      run.result = combined.result;
      const t = (combined.result["extra-info"] || {}).timings || {};
      run.timings = [
        { label: "分析並修圖", duration: combinedMs, children: [
          { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) },
          { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
          { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
          { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
          { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
        ] }
      ];
    } catch (error) {
      run.status = "error";
      run.error = friendlyError(error);
    }
    // Remember the first successfully-done run's analysis + job id so a
    // later per-image revise reuses the normal two-stage continueJob path
    // instead of needing its own combined submission.
    if (!record.analysis) {
      const doneRun = (record.runs || []).find((r) => r.status === "done");
      if (doneRun) {
        record.analysis = doneRun.result.analysis;
        record.analyzeJobId = doneRun.jobId;
      }
    }
    syncRecordMirror(record);
    updateBatchSidebarItem(record);
    updateBatchProgress();
    refreshIfSelected(record);
  }

  // Poll one local analyze job and fetch its analysis (step 1).
  async function analyzeBatchItem(record) {
    try {
      const stepStart = Date.now();
      await pollJobStatus(record.analyzeJobId, { path: "local" });
      const analyzeResult = await getAnalyzeResult(record.analyzeJobId);
      record.analysis = analyzeResult.result.analysis;
      const t = (record.analysis && record.analysis.timings) || {};
      record.analyzeTimings = [{ label: "分析中", duration: Date.now() - stepStart, children: [
        { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) }
      ] }];
      record.status = "analyzed";
    } catch (error) {
      record.status = "error";
      record.error = friendlyError(error);
    }
    updateBatchSidebarItem(record);
    refreshIfSelected(record);
    updateBatchProgress();
  }

  // Shows/hides the bulk "全部繼續處理" button + batch-wide prompt/options rows
  // in the sidebar, based on whether there's at least one record still
  // awaiting its first edit -- live-updated as analyses/edits complete, not
  // just once at the end of a whole-batch phase.
  function updateBatchHeaderControls() {
    const ready = analyzedBatchRecords().length > 0;
    els.batchContinueAllBtn.hidden = !ready;
    els.batchPromptRow.hidden = !ready;
    if (els.batchOptionsRow) els.batchOptionsRow.hidden = !ready;
  }

  // Effective per-image prompt: the image's own (from its review) wins; else the
  // batch-level shared prompt applies.
  function effectivePrompt(record) {
    const own = (record.userPrompt || "").trim();
    if (own) return own;
    return (els.batchPromptInput && els.batchPromptInput.value.trim()) || "";
  }

  // Effective per-image style options: same override rule as effectivePrompt,
  // but merged key-by-key (pose/angle/lighting/size) since each is an
  // independent pick rather than one all-or-nothing string. Whether "own"
  // wins is decided by optionsTouched (did the user actually change this
  // image's radio), not by own's value -- merely displaying the batch's
  // inherited pick (applyBatchReviewSelections) writes that same value into
  // record.options on nav-away, and if we compared values instead, a later
  // change to the batch-level pick would silently stop reaching any image
  // that had simply been viewed once.
  function effectiveOptions(record) {
    const own = record.options || {};
    const touched = record.optionsTouched || {};
    const batchOptions = readBatchStyleOptionsFromDom();
    const merged = {};
    ["pose", "angle", "lighting", "size"].forEach((key) => {
      merged[key] = touched[key] ? (own[key] || "default") : (batchOptions[key] || "default");
    });
    return merged;
  }

  // Reads the batch-level Pose/Angle/Lighting/Size radios straight from the DOM.
  function readBatchStyleOptionsFromDom() {
    const checked = (name) => document.querySelector(`input[name="${name}"]:checked`);
    return {
      pose: checked("batch-pose")?.value || "default",
      angle: checked("batch-angle")?.value || "default",
      lighting: checked("batch-lighting")?.value || "default",
      size: checked("batch-size")?.value || "default"
    };
  }

  // Fires only on a genuine user click on one of the per-image ap-*-list
  // radios (programmatic .checked writes from renderAnalysisPreview /
  // applyStyleSelections don't dispatch "change"). Marks that category as
  // individually adjusted for the currently-open batch record, then re-locks
  // the matching batch-level control.
  function markOptionTouched(key) {
    const record = currentBatchRecord;
    if (!record) return;
    if (!record.optionsTouched) record.optionsTouched = {};
    record.optionsTouched[key] = true;
    updateBatchOptionsLocks();
  }

  // Disables a batch-level category's radios (and shows its unlock toggle)
  // once any image in the batch has its own adjustment for that category --
  // otherwise changing the batch default there would silently do nothing for
  // the already-adjusted images while still being presented as "applies to
  // all". The unlock checkbox lets the user override that guard deliberately.
  function updateBatchOptionsLocks() {
    BATCH_OPTION_CATEGORIES.forEach(({ key, group, list, lockRow, lock, lockBadge }) => {
      const hasConflict = batchRecords.some((r) => r.optionsTouched && r.optionsTouched[key]);
      const lockRowEl = els[lockRow];
      const lockEl = els[lock];
      if (lockRowEl) lockRowEl.hidden = !hasConflict;
      if (!hasConflict && lockEl) lockEl.checked = false;
      const locked = hasConflict && !(lockEl && lockEl.checked);
      if (els[group]) els[group].classList.toggle("is-locked", locked);
      if (els[lockBadge]) els[lockBadge].hidden = !locked;
      const listEl = els[list];
      if (listEl) {
        listEl.querySelectorAll("input[type=radio]").forEach((input) => { input.disabled = locked; });
      }
    });
  }

  // Run step 2 (edit) for the given analyzed records via the same single-image
  // endpoints, so each image carries its own prompt -- flow identical to single.
  async function continueBatchRecords(records) {
    const targets = records.filter((r) => r.status === "analyzed");
    if (!targets.length) return;

    // N independent edit runs per image from the same analysis. Each run is
    // its own job; the pool works through (record, run) pairs.
    const runCount = Math.max(1, parseInt(els.runCountSelect?.value, 10) || 1);
    targets.forEach((r) => {
      r.runs = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing",
        imageUrl: null, result: null, timings: [], error: null
      }));
      r.mirrorIndex = null; // auto: mirror follows the first finished run
    });

    targets.forEach((r) => {
      r.status = "processing";
      updateBatchSidebarItem(r);
      refreshIfSelected(r); // no canvas yet for a first-time edit -- shows the loading placeholder
    });
    updateBatchProgress();
    // Only after every target has actually left "analyzed" -- otherwise this
    // still sees them and leaves the bulk prompt/options/continue-all button
    // visible for the whole editing run instead of hiding immediately.
    updateBatchHeaderControls();

    const workItems = targets.flatMap((r) => r.runs.map((run) => ({ record: r, run })));
    await runBatchPool(workItems, editBatchItem, POLL_CONCURRENCY);

    updateBatchHeaderControls(); // some records may still be "analyzed" if only a subset was continued
  }

  function batchContinueAll() {
    continueBatchRecords(analyzedBatchRecords());
  }

  // Step 2 for one (record, run) pair: submit via the single-image
  // endpoint (so it carries the image's own effective prompt) -> poll ->
  // fetch. Records analyze + edit sub-timings, matching the single flow's
  // nested timing display. Results land on the run entry `run`;
  // syncRecordMirror keeps the record's legacy fields pointing at the
  // selected/first-done run so all existing readers stay unchanged.
  async function editBatchItem({ record, run }) {
    try {
      const userPrompt = effectivePrompt(record);
      const effectiveAnalysis = buildEffectiveAnalysis(record.analysis, {
        options: effectiveOptions(record),
        recipeChecked: record.recipeChecked
      });
      let stepStart = Date.now();
      if (batchSettings.path === "aipoc") {
        run.jobId = await submitJob({
          file: record.file,
          preset: batchSettings.presetKey,
          imageQuality: batchSettings.imageQuality,
          imageModel: batchSettings.imageModel,
          aspectRatio: batchSettings.aspectRatio,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          runQa: batchSettings.runQa,
          userPrompt,
          analysis: effectiveAnalysis,
          path: "aipoc"
        });
      } else {
        run.jobId = await continueJob(record.analyzeJobId, {
          analysis: effectiveAnalysis,
          preset: batchSettings.presetKey,
          imageQuality: batchSettings.imageQuality,
          imageModel: batchSettings.imageModel,
          aspectRatio: batchSettings.aspectRatio,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          runQa: batchSettings.runQa,
          userPrompt
        });
      }
      const submitMs = Date.now() - stepStart;

      stepStart = Date.now();
      await pollJobStatus(run.jobId, { path: batchSettings.path });
      const pollMs = Date.now() - stepStart;

      stepStart = Date.now();
      const { image_url: imageUrl, result } = await getJobResult(run.jobId, { path: batchSettings.path });
      const fetchMs = Date.now() - stepStart;

      run.status = "done";
      run.imageUrl = imageUrl;
      run.result = result;
      // Finish-stage timings moved into result["extra-info"] (matches single).
      const t = (result["extra-info"] || {}).timings || {};
      run.timings = [
        ...(record.analyzeTimings || []),
        { label: "提交後續處理", duration: submitMs },
        { label: "AI 修圖中", duration: pollMs, children: [
          { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
          { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
          { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
          { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
        ] },
        { label: "取得結果", duration: fetchMs }
      ];
    } catch (error) {
      run.status = "error";
      run.error = friendlyError(error);
    }
    syncRecordMirror(record);
    updateBatchSidebarItem(record);
    updateBatchProgress();
    refreshIfSelected(record);
  }

  function syncRecordMirror(record) {
    const runs = record.runs || [];
    if (!runs.length) return;
    const pick = (record.mirrorIndex != null && runs[record.mirrorIndex])
      ? runs[record.mirrorIndex]
      : (runs.find((x) => x.status === "done") || runs[0]);
    record.jobId = pick.jobId;
    record.imageUrl = pick.imageUrl;
    record.result = pick.result;
    record.timings = pick.timings;
    const anyDone = runs.some((x) => x.status === "done");
    const anyRunning = runs.some((x) => x.status === "processing");
    record.status = anyDone ? "done" : (anyRunning ? "processing" : "error");
    record.error = record.status === "error" ? (runs.find((x) => x.error) || {}).error : null;
  }

  // Run `worker` over `items` with at most `concurrency` in flight.
  async function runBatchPool(items, worker, concurrency) {
    let idx = 0;
    const runners = [];
    const n = Math.min(concurrency, items.length);
    for (let c = 0; c < n; c += 1) {
      runners.push((async () => {
        while (idx < items.length) {
          const i = idx;
          idx += 1;
          await worker(items[i]);
        }
      })());
    }
    await Promise.all(runners);
  }

  function updateBatchProgress() {
    const total = batchRecords.length;
    const failed = batchRecords.filter((r) => r.status === "error").length;
    const failText = failed ? `，失敗 ${failed}` : "";
    // Once any image has entered/finished editing, report edit progress;
    // otherwise report analysis progress.
    const inEdit = batchRecords.some((r) => r.status === "processing" || r.status === "done");
    if (inEdit) {
      const done = batchRecords.filter((r) => r.status === "done").length;
      const runs = batchRecords.flatMap((r) => r.runs || []);
      const runText = batchRecords.some((r) => (r.runs || []).length > 1)
        ? `（共 ${runs.filter((x) => x.status === "done").length}/${runs.length} RUN）`
        : "";
      els.batchProgress.textContent = `完成 ${done}/${total}${runText}${failText}`;
    } else {
      const analyzed = batchRecords.filter((r) => r.status === "analyzed").length;
      els.batchProgress.textContent = `已分析 ${analyzed}/${total}${failText}`;
    }
    updateBatchDownloadAllVisibility();
  }

  // Batch counterpart of buildResultZip/buildAllRunsZip: bundles every done
  // image's *every done RUN* into one zip, each image under its own
  // "image N/" folder (1-indexed by position in the batch) -- if an image
  // was revised with a higher RUN count, all of those RUNs are included, not
  // just the currently mirrored one, so the zip's file count actually
  // matches how many results were generated instead of silently dropping to
  // one per image.
  function doneBatchRecordsWithResult() {
    return batchRecords.filter((r) => r.status === "done" && r.result);
  }

  // Same "fall back to a synthetic single run" shape as lastResultRuns(),
  // for a batch record instead of the single-image flow's lastResult.
  function batchRecordRuns(record) {
    if (Array.isArray(record.runs) && record.runs.length) return record.runs;
    return [{ run: 1, result: record.result, jobId: record.jobId, status: "done" }];
  }

  async function buildAllBatchResultsZip() {
    const records = doneBatchRecordsWithResult();
    const groups = await Promise.all(records.map((r, i) => {
      const doneRuns = batchRecordRuns(r).filter((run) => run.status === "done");
      return buildImageFiles(
        { file: r.file, path: batchSettings.path, prefix: `image ${i + 1}/` },
        doneRuns
      );
    }));
    return window.PhotoAgentZip.createZip(groups.flat());
  }

  function updateBatchDownloadAllVisibility() {
    if (!els.batchDownloadAllBtn) return;
    const count = doneBatchRecordsWithResult().length;
    els.batchDownloadAllBtn.hidden = count === 0;
    if (count > 0) {
      const hint = `下載所有已完成的結果（${count} 張）`;
      els.batchDownloadAllBtn.title = hint;
      els.batchDownloadAllBtn.setAttribute("aria-label", hint);
    }
  }

  function handleDownloadAllBatchClick() {
    if (!doneBatchRecordsWithResult().length) return;
    downloadZip(buildAllBatchResultsZip, buildDownloadZipFilename());
  }

  function renderBatchSidebarList() {
    if (els.batchSidebarTitle) els.batchSidebarTitle.textContent = `批次圖片（${batchRecords.length}）`;
    els.batchSidebarList.innerHTML = "";
    batchRecords.forEach((record) => els.batchSidebarList.appendChild(buildBatchSidebarItem(record)));
  }

  // Every status is clickable now (unlike the old grid, which only let you
  // click done/analyzed/error cards) -- selectBatchRecord dispatches to
  // whatever main-area view matches that record's current status.
  function buildBatchSidebarItem(record) {
    const item = document.createElement("div");
    item.className = `batch-sidebar-item ${record.status}${record === currentBatchRecord ? " selected" : ""}`;
    item.dataset.id = record.localId;

    const media = document.createElement("div");
    media.className = "rc-media";
    const img = document.createElement("img");
    img.src = record.imageUrl || record.previewUrl;
    media.appendChild(img);
    const runs = record.runs || [];
    const runsRunning = runs.filter((x) => x.status === "processing").length;
    if (record.status === "analyzing" || record.status === "processing" || runsRunning) {
      const spinner = document.createElement("div");
      spinner.className = "rc-spinner";
      media.appendChild(spinner);
    }
    const badge = document.createElement("span");
    badge.className = "rc-badge";
    badge.textContent = {
      analyzing: "分析中",
      analyzed: "已分析",
      processing: "處理中",
      done: "完成",
      error: "失敗"
    }[record.status] || "";
    if (runs.length > 1) {
      const runsDone = runs.filter((x) => x.status === "done").length;
      if (runsRunning) badge.textContent = `處理中 RUN ${runsDone}/${runs.length}`;
      else if (runsDone === runs.length) badge.textContent = `完成 ${runs.length} RUN`;
      else if (runsDone) badge.textContent = `完成 ${runsDone}/${runs.length} RUN`;
    }

    // Unlike the old grid (badge overlaid on a large square thumbnail), the
    // sidebar's thumbnail is small -- the badge sits beside the filename
    // instead of inside it, where there's enough width for the text.
    const textCol = document.createElement("div");
    textCol.className = "batch-sidebar-text";
    const name = document.createElement("p");
    name.className = "rc-name";
    name.textContent = record.file.name;
    textCol.appendChild(name);
    textCol.appendChild(badge);

    item.appendChild(media);
    item.appendChild(textCol);
    if (record.status === "error") item.title = record.error || "處理失敗";
    item.addEventListener("click", () => selectBatchRecord(record));
    return item;
  }

  function updateBatchSidebarItem(record) {
    const existing = els.batchSidebarList.querySelector(`[data-id="${record.localId}"]`);
    if (existing) existing.replaceWith(buildBatchSidebarItem(record));
  }

  function openBatchError(record) {
    if (!els.errorModal) return;
    els.errorModalFile.textContent = record.file.name;
    els.errorModalMsg.textContent = record.error || "處理失敗，請稍後再試。";
    els.errorModal.hidden = false;
  }

  function closeErrorModal() {
    if (els.errorModal) els.errorModal.hidden = true;
  }

  // "重新分析": wipe every record's analysis/edit state and re-run step 1
  // from scratch, same as the initial submit -- for when the recipe needs a
  // clean re-analyze rather than a manual revise.
  function reloadBatch() {
    startSubmitBatch();
  }

  // ── Core dispatcher: sidebar click -> whatever main-area view matches the
  // selected record's current status. Replaces the old grid/detail-pagination/
  // separate-review-screen split entirely -- one page, one selection model. ──
  function selectBatchRecord(record) {
    if (!record) return;
    // If a done record's inline revise-panel is open, close it directly
    // rather than simulating a click on its cancel button -- that click's
    // continuation runs as a microtask (via waitForInlineAnalysisAction's
    // promise), which would race against the render we're about to do for
    // the newly selected record.
    if (els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis")) {
      clearInlineAnalysisWait();
      closeInlineAnalysisPanel();
    }
    const previous = currentBatchRecord;
    currentBatchRecord = record;
    if (previous && previous !== record) updateBatchSidebarItem(previous);
    updateBatchSidebarItem(record);
    renderBatchMain(record);
    if (record.status === "error") openBatchError(record); // only on explicit click, never from a background refresh
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  // Re-renders the main area only if `record` is still the selected one --
  // called from every place a record's status changes in the background
  // (analyze/edit completing), so the visible view stays live without
  // requiring the user to re-click the sidebar.
  function refreshIfSelected(record) {
    if (record === currentBatchRecord) renderBatchMain(record);
  }

  // The topbar's subject/scene tags (#ap-subject-tags) are shared with the
  // single-image flow, so switching which batch image is selected has to
  // refresh them too -- otherwise they're left showing whichever record was
  // selected before, regardless of which sub-view (loading/review/done/error)
  // ends up rendering. "analyzed" already refreshes them via
  // renderAnalysisPreview; this covers every other status too.
  function updateApSubjectTags(record) {
    if (!els.apSubjectTags) return;
    const analysis = record && record.analysis;
    renderTags(els.apSubjectTags, analysis ? [
      analysis.subject_category || "未知類型",
      analysis.scene_category || "未知場景"
    ] : []);
  }

  // Caps .batch-sidebar so align-items:stretch never grows it past the
  // viewport (per this app's "avoid making the user scroll the window"
  // policy), even when the main content column is naturally very tall.
  // Measured from the sidebar's own actual (pre-scroll) rendered top --
  // a static CSS calc() can't know the results-topbar's real height, which
  // is what actually pushes that starting position down, not just the fixed
  // header. Deliberately NOT re-measured on scroll: this uses the sidebar's
  // worst-case (unstuck, further down the page) position, which is the
  // binding constraint -- once scrolled enough to actually stick higher up,
  // capping to the same height just leaves a little unused room at the
  // bottom rather than growing (which would require re-measuring mid-scroll).
  function updateBatchSidebarMaxHeight() {
    if (!els.batchSidebar || els.batchSidebar.hidden) return;
    const top = els.batchSidebar.getBoundingClientRect().top;
    const maxHeight = Math.max(200, window.innerHeight - top - 24);
    els.batchSidebar.style.maxHeight = `${maxHeight}px`;
  }
  window.addEventListener("resize", updateBatchSidebarMaxHeight);

  function renderBatchMain(record) {
    if (!record) return;
    updateApSubjectTags(record);
    updateBatchSidebarMaxHeight();
    if (record.status === "analyzed") showBatchAnalyzedMain(record);
    // Guard against the brief window (mid-revise, with N new runs in flight
    // alongside older already-done ones) where syncRecordMirror's aggregate
    // "any run done" check flips record.status to "done" before the specific
    // mirrored run has actually resolved -- record.result would still be
    // null then, so fall through to the loading view instead of crashing.
    else if (record.status === "done" && record.result) showBatchDoneMain(record);
    else if (record.status === "error") showBatchMainError(record);
    else showBatchMainLoading(record); // "analyzing" / "processing"
  }

  function hideAllBatchMainViews() {
    if (els.batchMainLoading) {
      els.batchMainLoading.hidden = true;
      els.batchMainLoading.classList.remove("is-error", "is-analyzed");
    }
    if (els.batchReviewContinueBtn) els.batchReviewContinueBtn.hidden = true;
    restoreAnalysisPreviewHome(); // vacates #results-analysis-slot if a done record's revise panel is open
    if (els.resultsCanvasRow) els.resultsCanvasRow.hidden = true;
    if (els.runTabsRow) els.runTabsRow.hidden = true;
  }

  function showBatchMainLoading(record) {
    hideAllBatchMainViews();
    if (!els.batchMainLoading) return;
    els.batchMainLoading.hidden = false;
    if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.previewUrl;
    if (els.batchMainLoadingEyebrow) els.batchMainLoadingEyebrow.textContent = "Photo Master is working";
    const analyzing = record.status === "analyzing";
    if (els.batchMainLoadingTitle) {
      els.batchMainLoadingTitle.textContent = analyzing ? "正在分析照片" : "AI 正在修圖";
    }
    if (els.batchMainLoadingText) {
      els.batchMainLoadingText.textContent = analyzing
        ? "AI 正在分析照片，請稍候…"
        : "AI 正在修圖，請稍候…";
    }
  }

  function showBatchMainError(record) {
    hideAllBatchMainViews();
    if (!els.batchMainLoading) return;
    els.batchMainLoading.hidden = false;
    els.batchMainLoading.classList.add("is-error");
    if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.previewUrl;
    if (els.batchMainLoadingEyebrow) els.batchMainLoadingEyebrow.textContent = "Photo Master hit a snag";
    if (els.batchMainLoadingTitle) els.batchMainLoadingTitle.textContent = "處理失敗";
    if (els.batchMainLoadingText) {
      els.batchMainLoadingText.textContent = record.error || "處理失敗，請稍後再試。";
    }
  }

  // Pre-first-edit review: the recipe/style-options/prompt panel, shown full
  // width in the main area (no canvas yet -- nothing has been edited for this
  // image). Reuses the same #analysis-preview DOM the beside-canvas revise
  // panel does, just relocated to a different destination.
  // "Analyzed, not yet edited" -- no per-image recipe/options/prompt review
  // anymore (that's only decided via the bulk batch-prompt-row/batch-options-
  // row in the sidebar now); just a static preview of the source photo plus a
  // way to continue this one image specifically, same info-block language as
  // showBatchMainLoading/showBatchMainError but without the spinning rings
  // (nothing is actually in progress for this record yet).
  function showBatchAnalyzedMain(record) {
    hideAllBatchMainViews();
    if (els.batchMainLoading) {
      els.batchMainLoading.hidden = false;
      els.batchMainLoading.classList.add("is-analyzed");
      if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.previewUrl;
      if (els.batchMainLoadingEyebrow) els.batchMainLoadingEyebrow.textContent = "Photo Master is ready";
      if (els.batchMainLoadingTitle) els.batchMainLoadingTitle.textContent = "已完成分析";
      if (els.batchMainLoadingText) {
        els.batchMainLoadingText.textContent = "可調整左側的整批補充指令或風格選項，或針對這張圖片單獨繼續處理。";
      }
    }
    if (els.batchReviewContinueBtn) els.batchReviewContinueBtn.hidden = false;
  }

  // Tab strip for one image's N edit results. Hidden unless there's more than
  // one run. Shared by the batch detail view and the single-image results
  // view; `onSelectDone(idx)` decides what re-pointing to that run means for
  // the caller (batch re-renders via syncRecordMirror+showBatchDoneMain, single
  // via showSingleResult).
  function buildRunTabs(runs, currentIdx, onSelectDone) {
    const samePhoto = runs === lastRunTabsRuns;
    const prevScrollLeft = els.runTabs.scrollLeft;
    els.runTabs.hidden = false;
    els.runTabs.innerHTML = "";
    runs.forEach((run, idx) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `run-tab ${run.status}${idx === currentIdx ? " active" : ""}`;
      tab.setAttribute("aria-label", `RUN ${run.run}`);

      const media = document.createElement("span");
      media.className = "run-tab-media";
      if (run.status === "done") {
        const im = document.createElement("img");
        im.src = run.imageUrl;
        media.appendChild(im);
      } else if (run.status === "processing") {
        const spin = document.createElement("span");
        spin.className = "run-tab-spin";
        media.appendChild(spin);
      }
      tab.appendChild(media);

      if (run.status === "done") {
        tab.addEventListener("click", () => onSelectDone(idx));
      } else {
        tab.disabled = true;
        if (run.status === "error") tab.title = run.error || "此 RUN 修圖失敗";
      }
      els.runTabs.appendChild(tab);
    });
    els.runTabs.scrollLeft = samePhoto ? prevScrollLeft : 0;
    lastRunTabsRuns = runs;
    currentRunTabsIndex = currentIdx;
    currentRunTabsOnSelect = onSelectDone;
    if (els.runTabsIndex) {
      els.runTabsIndex.hidden = false;
      els.runTabsIndex.textContent = `${currentIdx + 1} / ${runs.length}`;
    }
    updateRunTabsArrows();
  }

  // Clicking a finished tab re-points the record's mirror fields at that run
  // and re-renders the whole report.
  function renderRunTabs(record) {
    if (!els.runTabs) return;
    const runs = record.runs || [];
    if (runs.length <= 1) {
      els.runTabs.hidden = true;
      els.runTabs.innerHTML = "";
      if (els.runTabsIndex) els.runTabsIndex.hidden = true;
      currentRunTabsIndex = null;
      currentRunTabsOnSelect = null;
      return;
    }

    const doneIdx = runs.findIndex((x) => x.status === "done");
    const current = (record.mirrorIndex != null && runs[record.mirrorIndex]) ? record.mirrorIndex : doneIdx;
    buildRunTabs(runs, current, (idx) => {
      record.mirrorIndex = idx;
      syncRecordMirror(record);
      showBatchDoneMain(record);
    });
  }

  // Single-image counterpart of renderRunTabs: reads/writes the module-level
  // singleRuns/singleMirrorIndex instead of a batch record.
  function renderSingleRunTabs() {
    if (!els.runTabs) return;
    const runs = singleRuns || [];
    if (runs.length <= 1) {
      els.runTabs.hidden = true;
      els.runTabs.innerHTML = "";
      if (els.runTabsIndex) els.runTabsIndex.hidden = true;
      currentRunTabsIndex = null;
      currentRunTabsOnSelect = null;
      return;
    }

    const doneIdx = runs.findIndex((x) => x.status === "done");
    const current = (singleMirrorIndex != null && runs[singleMirrorIndex]) ? singleMirrorIndex : doneIdx;
    buildRunTabs(runs, current, (idx) => {
      singleMirrorIndex = idx;
      showSingleResult();
    });
  }

  // Reuse the single-image report DOM (renderResult) for the selected record.
  // Set the original image first so resolveCropBoxForDisplay sees its dims.
  function showBatchDoneMain(record) {
    // Skip the teardown when the canvas is already showing -- this runs
    // again every time a RUN tab is clicked (same record, just a different
    // run), and hideAllBatchMainViews' restoreAnalysisPreviewHome() would
    // otherwise yank the inline revise panel out from beside the canvas
    // (if the user has it open) even though nothing about which record/view
    // is displayed actually changed.
    const alreadyShowing = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
    if (!alreadyShowing) hideAllBatchMainViews();
    els.resultsCanvasRow.hidden = false;
    els.runTabsRow.hidden = false;
    if (els.backToProcessBtn) els.backToProcessBtn.hidden = false;
    renderRunTabs(record);

    const applyRender = () => {
      els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
      // Report the models that actually ran, same label logic as single.
      const extraInfo = record.result["extra-info"] || {};
      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : "本機處理";
      const usedModelLabel = isDebugUiActive
      ? `${window.PhotoAgentConfig.analysisModel} + ${usedImageModelLabel}`
      : "AI 智能修圖";
      const data = {
        image: record.imageUrl,
        model: usedModelLabel,
        usage: record.result.usage,
        qualityCheck: record.result.quality_check,
        localOnly: !batchSettings.aiPhotoEditing,
        retryCount: 0
      };
      // Point the zip download at the currently-viewed image, same shape the
      // single flow sets so handleDownloadClick works here too. The extra
      // runs field switches the download to an all-runs bundle when this
      // image was edited more than once.
      lastResult = { file: record.file, resultText: record.result, jobId: record.jobId, path: batchSettings.path, runs: record.runs };
      updateDownloadLinks(record.runs);
      renderResult({
        data,
        analysis: record.result.analysis,
        preset: batchSettings.preset,
        imageModel: data.model
      });
      populateTimingSection(record.timings);
    };
    if (els.originalImg.src === record.previewUrl && els.originalImg.complete) {
      applyRender();
    } else {
      els.originalImg.onload = () => {
        els.originalImg.onload = null;
        applyRender();
      };
      els.originalImg.src = record.previewUrl;
    }
  }

  // Is a single already-finished batch record currently open in the main
  // area? Returns null for the single-image flow (or a batch record that
  // isn't done yet), so callers can branch between the whole-record batch
  // path and the single-image path with one check.
  function currentDetailBatchRecord() {
    return (currentBatchRecord && currentBatchRecord.status === "done") ? currentBatchRecord : null;
  }

  // "檢視分析結果" for one already-finished batch photo: reuses the same
  // inline slide-in panel as the single-image flow (backToProcessFromResults),
  // but re-runs just this one record via editBatchItem instead of
  // finishSingleFlow -- editBatchItem never touches showSection, so this
  // stays fully inline with no processing-section detour at all.
  async function startInlineBatchReview(record) {
    hideError();
    clearInlineAnalysisWait();

    renderAnalysisPreview({ analysis: record.analysis, preset: batchSettings.preset });
    applyBatchReviewSelections(record);
    if (els.promptInput) els.promptInput.value = record.userPrompt || "";
    // Pre-fill with this image's own current RUN count (matching whatever was
    // configured when it last ran) instead of hiding the control -- the
    // bulk #run-count in the sidebar only ever applies to a *first* continue,
    // so a revise needs its own visible/adjustable count, same as single-image.
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    if (els.apRunCountSelect) els.apRunCountSelect.value = String((record.runs || []).length || 1);
    openInlineAnalysisPanel();

    const action = await waitForInlineAnalysisAction();
    closeInlineAnalysisPanel();
    if (action === "cancel") return;

    captureBatchReviewSelections(record);
    setAnalysisPreviewDisabled(true);

    // N fresh runs (N = the count just confirmed above) -- drop the previous
    // result(s) entirely and regenerate from scratch, same as re-running the
    // single-image flow (finishSingleFlow always replaces singleRuns rather
    // than appending to it), instead of piling new runs up alongside old ones.
    const runCount = Math.max(1, parseInt(els.apRunCountSelect?.value, 10) || 1);
    const newRuns = Array.from({ length: runCount }, (_, k) => ({
      run: k + 1, jobId: null, status: "processing",
      imageUrl: null, result: null, timings: [], error: null
    }));
    record.runs = newRuns;
    record.mirrorIndex = newRuns.length - 1;
    record.status = "processing";
    updateBatchSidebarItem(record);
    // Replaces the canvas/run-tabs with the same processing view a first-time
    // edit shows (renderBatchMain routes "processing" there) -- rather than
    // leaving the old (now-stale) canvas up with a spinner overlaid on it.
    refreshIfSelected(record);

    await runBatchPool(newRuns.map((run) => ({ record, run })), editBatchItem, POLL_CONCURRENCY);
    // Re-point the mirror at the newest run that actually succeeded, in case
    // it wasn't the last one (a slower earlier run finished first) -- other-
    // wise record.status could read "done" while the mirrored pick is a
    // still-processing/failed run, leaving record.result null (see
    // renderBatchMain's guard, which falls back to the processing view then).
    for (let i = record.runs.length - 1; i >= 0; i -= 1) {
      if (record.runs[i].status === "done") { record.mirrorIndex = i; break; }
    }
    syncRecordMirror(record);
    updateBatchSidebarItem(record);
    refreshIfSelected(record);

    const failed = newRuns.filter((run) => run.status === "error");
    if (failed.length) showError(failed[0].error || "重新處理失敗，請稍後再試。");
  }

  // Dispatches "檢視分析結果" to whichever flow is actually active: a single
  // finished batch photo (per-item revise) or the single-image flow.
  function handleViewAnalysisClick() {
    // Toggle: if the panel is already open, pressing the same button again
    // closes it -- simulate the cancel button's click instead of duplicating
    // its logic, since backToProcessFromResults/startInlineBatchReview are
    // both mid-`await waitForInlineAnalysisAction()` at that point, and only
    // that click actually resolves their pending promise.
    if (els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis")) {
      if (els.inlineAnalysisCancelBtn) els.inlineAnalysisCancelBtn.click();
      return;
    }
    const record = currentDetailBatchRecord();
    if (record) {
      startInlineBatchReview(record);
    } else {
      backToProcessFromResults();
    }
  }

  function analyzedBatchRecords() {
    return batchRecords.filter((r) => r.status === "analyzed");
  }

  // Reads the current recipe checkboxes + pose/angle/lighting/size radios
  // straight from the DOM (shared by the single-image flow and batch review).
  function readStyleSelectionsFromDom() {
    const checked = (name) => document.querySelector(`input[name="${name}"]:checked`);
    const options = {
      pose: checked("ap-pose")?.value || "default",
      angle: checked("ap-angle")?.value || "default",
      lighting: checked("ap-lighting")?.value || "default",
      size: checked("ap-size")?.value || "default"
    };
    const recipeChecked = els.apRecipeList
      ? Array.from(els.apRecipeList.querySelectorAll("input[type=checkbox]")).map((c) => c.checked)
      : null;
    return { options, recipeChecked };
  }

  // Applies the user's recipe checkbox edits and style-option overrides onto a
  // copy of `analysis` before it's sent to the backend. This is what makes the
  // user's revisions the highest-priority version of the recipe: unchecked
  // items are dropped from correction_plan/retouch_recipe (correction_plan is
  // what the edit model actually reads) AND recorded as rejected_corrections,
  // which the edit prompt turns into an explicit "do not do this" instruction
  // -- simply omitting them isn't enough, since other analysis fields (e.g.
  // aesthetic_diagnosis, lighting_strategy) could still lead the model to make
  // the same change anyway. Any non-default pose/angle/lighting/size pick is
  // attached as style_options for the edit prompt.
  function buildEffectiveAnalysis(analysis, { options, recipeChecked } = {}) {
    const effective = { ...analysis };

    if (Array.isArray(recipeChecked) && recipeChecked.length) {
      const keepChecked = (arr) =>
        Array.isArray(arr) ? arr.filter((_, i) => recipeChecked[i] !== false) : arr;
      effective.correction_plan = keepChecked(analysis.correction_plan);
      effective.retouch_recipe = keepChecked(analysis.retouch_recipe);
      effective.rejected_corrections = Array.isArray(analysis.correction_plan)
        ? analysis.correction_plan.filter((_, i) => recipeChecked[i] === false)
        : [];
    }

    const styleOptions = {};
    Object.entries(options || {}).forEach(([key, value]) => {
      if (value && value !== "default") styleOptions[key] = value;
    });
    if (Object.keys(styleOptions).length) effective.style_options = styleOptions;

    return effective;
  }

  // Read the radio/checkbox selections back onto the record so per-image choices
  // persist across nav/return, and so editBatchItem can build each record's
  // effective analysis before submission.
  function captureBatchReviewSelections(record) {
    if (!record) return;
    if (els.promptInput) record.userPrompt = els.promptInput.value.trim();
    const { options, recipeChecked } = readStyleSelectionsFromDom();
    record.options = options;
    if (recipeChecked) record.recipeChecked = recipeChecked;
  }

  // Restores previously-captured pose/angle/lighting/size radios + recipe
  // checkboxes onto the just-rendered analysis-preview DOM. Shared by the
  // batch review screen (applyBatchReviewSelections) and the single-image
  // "回到流程" flow (backToProcessFromResults), since both reuse the same
  // #ap-* markup.
  function applyStyleSelections({ options, recipeChecked } = {}) {
    if (options) {
      const map = { "ap-pose": "pose", "ap-angle": "angle", "ap-lighting": "lighting", "ap-size": "size" };
      Object.entries(map).forEach(([name, key]) => {
        const input = document.querySelector(`input[name="${name}"][value="${options[key]}"]`);
        if (input) input.checked = true;
      });
      [els.apPoseList, els.apAngleList, els.apLightingList, els.apSizeList].forEach(syncStyleTrigger);
    }
    if (Array.isArray(recipeChecked) && els.apRecipeList) {
      const boxes = els.apRecipeList.querySelectorAll("input[type=checkbox]");
      recipeChecked.forEach((isChecked, i) => { if (boxes[i]) boxes[i].checked = isChecked; });
    }
  }

  // Pre-fills this record's own pick if it has one, else the batch-level
  // shared pick -- so the per-image Pose/Angle/Lighting/Size radios always
  // show what will actually be submitted, same merge rule as effectiveOptions.
  function applyBatchReviewSelections(record) {
    applyStyleSelections({ options: effectiveOptions(record), recipeChecked: record.recipeChecked });
  }

  function resetApp() {
    hideError();
    selectedFile = null;
    lastResult = null;
    singleRuns = null;
    singleMirrorIndex = null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";
    resetBatchState();

    els.fileInput.value = "";
    els.dzPreviewImg.removeAttribute("src");
    els.procThumb.removeAttribute("src");
    els.originalImg.removeAttribute("src");
    els.generatedImg.removeAttribute("src");
    els.downloadLink.removeAttribute("href");
    updateDownloadLinks();
    els.dropZone.hidden = false;
    els.dzPreview.hidden = true;
    els.origDim.textContent = "";
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    clearContinueWait();
    // If the last thing the user did was an inline analysis review (single or
    // batch), #analysis-preview and #run-tabs-row are currently on loan inside
    // the results section (#results-analysis-slot / #results-canvas-col) --
    // move them back to the processing section before the next photo's
    // analyze tries to show #analysis-preview there, or it renders invisibly
    // inside the now-hidden results section instead.
    restoreAnalysisPreviewHome();
    restoreRunTabsRowHome();
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.remove("showing-analysis");
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = true;
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = true;
    // startSubmit()'s own `finally` never runs if the user resets instead of
    // clicking continue (waitForContinue's promise is left pending), so
    // re-enable Start explicitly here too.
    els.startButton.disabled = false;
    if (els.startDirectBtn) els.startDirectBtn.disabled = false;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    resetSteps();
    showSection("upload");
  }

  // ── Event listeners ────────────────────────────────────────────
  // One file -> single flow (setSelectedFile); more than one -> batch flow.
  function routeSelectedFiles(fileList) {
    const files = Array.from(fileList || []);
    // Some browsers fire "change" with an empty FileList when the user
    // cancels a re-opened file picker (e.g. by clicking the photo zone again)
    // -- treat that as a no-op so whatever was already selected (single
    // photo, batch, or the empty drop zone) stays exactly as it was.
    if (!files.length) return;
    if (files.length > 1) {
      setSelectedFileBatch(files);
    } else {
      resetBatchState();
      setSelectedFile(files[0]);
    }
  }

  els.fileInput.addEventListener("change", () => routeSelectedFiles(els.fileInput.files));
  // Once a photo (or batch) is selected it replaces the drop-zone label, so
  // clicking the preview/thumbnail-strip re-opens the file picker instead.
  els.dzPreview.addEventListener("click", () => els.fileInput.click());
  els.dzMulti.addEventListener("click", (e) => {
    if (e.target.closest(".dz-thumb-remove")) return;
    els.fileInput.click();
  });
  els.startButton.addEventListener("click", startSubmit);
  if (els.startDirectBtn) els.startDirectBtn.addEventListener("click", () => startSubmit({ skipReview: true }));
  els.backToProcessBtn.addEventListener("click", handleViewAnalysisClick);
  els.resetButton.addEventListener("click", resetApp);
  if (els.backToUploadBtn) els.backToUploadBtn.addEventListener("click", resetApp);
  if (els.compareToggleBtn) {
    els.compareToggleBtn.addEventListener("click", () =>
      setCompareView(!els.compareCard?.classList.contains("showing-generated")));
  }
  els.downloadLink.addEventListener("click", handleDownloadClick);
  if (els.downloadAllRunsLink) els.downloadAllRunsLink.addEventListener("click", handleDownloadAllRunsClick);
  if (els.reloadBatchBtn) els.reloadBatchBtn.addEventListener("click", reloadBatch);
  if (els.errorModalClose) els.errorModalClose.addEventListener("click", closeErrorModal);
  if (els.errorModal) {
    els.errorModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeErrorModal();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els.errorModal && !els.errorModal.hidden) closeErrorModal();
  });
  if (els.batchContinueAllBtn) els.batchContinueAllBtn.addEventListener("click", batchContinueAll);
  if (els.batchDownloadAllBtn) els.batchDownloadAllBtn.addEventListener("click", handleDownloadAllBatchClick);
  if (els.batchReviewContinueBtn) {
    els.batchReviewContinueBtn.addEventListener("click", () => {
      if (!currentBatchRecord) return;
      continueBatchRecords([currentBatchRecord]);
    });
  }

  // Per-image ap-*-list radios: their <ul> elements are stable across
  // renderAnalysisPreview's re-fills (only the <li> children get replaced),
  // so a delegated listener attached once here keeps working for every
  // future render.
  [["apPoseList", "pose"], ["apAngleList", "angle"], ["apLightingList", "lighting"], ["apSizeList", "size"]]
    .forEach(([elKey, key]) => {
      if (els[elKey]) {
        els[elKey].addEventListener("change", () => {
          markOptionTouched(key);
          syncStyleTrigger(els[elKey]);
          const details = els[elKey].closest(".ap-style-item");
          if (details) details.open = false;
        });
      }
    });

  BATCH_OPTION_CATEGORIES.forEach(({ lock }) => {
    if (els[lock]) els[lock].addEventListener("change", updateBatchOptionsLocks);
  });

  // Batch-level default pills: same close-on-pick + label-sync behavior as
  // the per-image ones, minus markOptionTouched (that only applies to a
  // per-image record, not the batch-wide default itself).
  [["batchPoseList"], ["batchAngleList"], ["batchLightingList"], ["batchSizeList"]]
    .forEach(([elKey]) => {
      if (els[elKey]) {
        els[elKey].addEventListener("change", () => {
          syncStyleTrigger(els[elKey]);
          const details = els[elKey].closest(".ap-style-item");
          if (details) details.open = false;
        });
      }
    });

  for (const eventName of ["dragenter", "dragover"]) {
    els.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    els.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropZone.classList.remove("dragging");
    });
  }
  els.dropZone.addEventListener("drop", (event) => {
    routeSelectedFiles(event.dataTransfer.files);
  });
})();
