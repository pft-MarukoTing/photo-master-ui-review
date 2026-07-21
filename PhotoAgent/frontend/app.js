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
  const { renderResult, renderAnalysisPreview, renderBatchStyleOptions, syncStyleTrigger } = window.PhotoAgentResults;
  renderBatchStyleOptions();

  let selectedFile = null;
  let previewUrl = "";
  let lastResult = null; // { file, resultText, jobId, path } -- for the zip download

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

  // Batch mode: populated only when more than one file is selected. While
  // batchRecords is empty the app behaves exactly as the single-image flow.
  const POLL_CONCURRENCY = 4;
  // Batch-level Pose/Angle/Lighting/Size controls, and the per-category lock
  // toggle each shows once any image in the batch has been individually
  // adjusted for that category (see updateBatchOptionsLocks).
  const BATCH_OPTION_CATEGORIES = [
    { key: "pose", group: "batchGroupPose", list: "batchPoseList", lockRow: "batchPoseLockRow", lock: "batchPoseLock" },
    { key: "angle", group: "batchGroupAngle", list: "batchAngleList", lockRow: "batchAngleLockRow", lock: "batchAngleLock" },
    { key: "lighting", group: "batchGroupLighting", list: "batchLightingList", lockRow: "batchLightingLockRow", lock: "batchLightingLock" },
    { key: "size", group: "batchGroupSize", list: "batchSizeList", lockRow: "batchSizeLockRow", lock: "batchSizeLock" }
  ];
  let batchRecords = [];   // see setSelectedFileBatch for record shape
  let batchSettings = null;
  let detailPos = 0;       // index into the done-records list for the results detail view
  let reviewPos = 0;       // index into the analyzed-records list for the step-2 review view
  let gridSummary = "";    // grid-view summary text, saved so backToBatchGrid can restore it

  const procVisual = document.querySelector(".proc-visual");
  const procTitle = document.querySelector(".proc-title");
  const procSub = document.querySelector(".proc-sub");
  const timingDetails = document.querySelector("#timing-details");
  const timingList = document.querySelector("#timing-list");
  const processingPathRow = document.querySelector("#processing-path-row");

  // Shown in the URL-debug case (?debug etc.) or when the backend was
  // started with `--debug-ui` / DEBUG_UI=true (testbed deployments).
  // Default to "local" in both debug cases -- AIPoC stays the default for
  // everyone else, but debug sessions are almost always testing the local
  // worker path.
  if (processingPathRow) {
    if (window.location.href.includes("debug")) {
      processingPathRow.style.display = "";
      if (els.processingPathSelect) els.processingPathSelect.value = "local";
      if (els.runQaRow) els.runQaRow.style.display = "";
    } else {
      getUiConfig().then((cfg) => {
        if (cfg.debugUi) {
          processingPathRow.style.display = "";
          if (els.processingPathSelect) els.processingPathSelect.value = "local";
          if (els.runQaRow) els.runQaRow.style.display = "";
        }
      });
    }
  }

  // Quality/model selects appear on both the upload screen (#image-quality,
  // #image-model) and the analysis-preview screen (#ap-image-quality,
  // #ap-image-model, shown mid-flow so the choice can be revised right
  // before the AI edit fires). Keep both pairs mirrored, and push the live
  // value into whichever flow state is active so finishSingleFlow/
  // editBatchItem (which read singleFlowState/batchSettings) pick up a
  // change made on the preview screen without any other wiring.
  // GPT Image models are noticeably slower/costlier at higher quality, so
  // "low" is the sensible default; Nano Banana's cost/latency profile doesn't
  // scale the same way, so "medium" is a better default there.
  function defaultQualityForModel(model) {
    return model === "nano-banana-2" || model === "nano-banana-lite" ? "medium" : "low";
  }

  function applyQualityModelChange({ quality, model }) {
    // A model change with no explicit quality (i.e. the user just picked a
    // different model, not the quality dropdown) resets quality to that
    // model's sensible default.
    if (model !== undefined && quality === undefined) {
      quality = defaultQualityForModel(model);
    }
    if (quality !== undefined) {
      if (els.imageQualitySelect) els.imageQualitySelect.value = quality;
      if (els.apImageQualitySelect) els.apImageQualitySelect.value = quality;
      if (singleFlowState) singleFlowState.imageQuality = quality;
      if (batchSettings) batchSettings.imageQuality = quality;
    }
    if (model !== undefined) {
      if (els.imageModelSelect) els.imageModelSelect.value = model;
      if (els.apImageModelSelect) els.apImageModelSelect.value = model;
      if (singleFlowState) singleFlowState.imageModel = model;
      if (batchSettings) batchSettings.imageModel = model;
    }
  }

  if (els.imageQualitySelect) {
    els.imageQualitySelect.addEventListener("change", () =>
      applyQualityModelChange({ quality: els.imageQualitySelect.value }));
  }
  if (els.apImageQualitySelect) {
    els.apImageQualitySelect.addEventListener("change", () =>
      applyQualityModelChange({ quality: els.apImageQualitySelect.value }));
  }
  if (els.imageModelSelect) {
    els.imageModelSelect.addEventListener("change", () =>
      applyQualityModelChange({ model: els.imageModelSelect.value }));
  }
  if (els.apImageModelSelect) {
    els.apImageModelSelect.addEventListener("change", () =>
      applyQualityModelChange({ model: els.apImageModelSelect.value }));
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

  function showAnalysisPreview(analysis, preset) {
    renderAnalysisPreview({ analysis, preset });
    if (els.analysisPreview) els.analysisPreview.hidden = false;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = false;
    // RUN count only applies to the single-image flow -- batch has its own
    // batch-wide control (#run-count), and showBatchReview hides this again
    // since it reuses this same #analysis-preview panel per image.
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    setAnalysisPreviewDisabled(false);
    procVisual.classList.add("done");
    if (procTitle) procTitle.textContent = "初步分析已完成";
    if (procSub) procSub.textContent = "已完成初步分析，請確認後繼續處理。";
  }

  function setSelectedFile(file) {
    hideError();
    if (!file || !file.type.startsWith("image/")) {
      showError("請選擇 JPG、PNG 或 WebP 圖片。");
      return;
    }

    selectedFile = file;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);

    els.dzPreviewImg.src = previewUrl;
    els.procThumb.src = previewUrl;
    els.originalImg.src = previewUrl;
    els.selectedFileName.textContent = file.name;
    els.dropZone.hidden = true;
    els.dzPreview.hidden = false;
    if (els.changePhotoBtn) els.changePhotoBtn.hidden = false;

    const image = new Image();
    image.onload = () => {
      els.origDim.textContent = `${image.naturalWidth} x ${image.naturalHeight}`;
    };
    image.src = previewUrl;
  }

  // ── Download source + result image + result JSON bundled as one zip ────
  function buildResultBaseName(jobId) {
    const pad = (n) => String(n).padStart(2, "0");
    const now = new Date();
    const dateStr = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
    // AIPoC task ids contain "/" and "+", which aren't safe in a filename.
    const safeJobId = String(jobId).replace(/[^a-zA-Z0-9._-]/g, "_");
    return `photoMuse-${dateStr}-${safeJobId}`;
  }

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

  async function buildResultFiles(results, prefix = "") {
    const { file, resultText, jobId, path } = results;
    // Fetch the image via our own backend, not the presigned S3/AIPoC URL --
    // that URL has no CORS headers, so a browser-side fetch() of it is
    // blocked even though the same URL displays fine in an <img> tag.
    const [sourceBuf, imageBuf] = await Promise.all([
      file.arrayBuffer(),
      fetch(resultImageUrl(jobId, { path })).then((response) => response.arrayBuffer())
    ]);
    const jsonBytes = new TextEncoder().encode(JSON.stringify(resultText, null, 2));
    const promptsBytes = new TextEncoder().encode(buildPromptsJson(resultText.analysis?.prompts || {}));

    return [
      { name: prefix + (file.name || "source.jpg"), data: new Uint8Array(sourceBuf) },
      { name: prefix + "result.png", data: new Uint8Array(imageBuf) },
      { name: prefix + "result.json", data: jsonBytes },
      { name: prefix + "prompts.json", data: promptsBytes }
    ];
  }

  // The shared per-image bundle used by every download: source.jpg at the
  // image level, each finished run under run_N/ (result.png/result.json/
  // prompts.json) -- a lone run lands in run_1/, so the layout is identical
  // whether there's one run or many.
  // Reuses buildResultFiles per run and drops its per-call source entry
  // (always the first item) in favor of a single shared one.
  async function buildRunFiles(base, runs, prefix = "") {
    const groups = await Promise.all(runs.map((run) =>
      buildResultFiles(
        { file: base.file, resultText: run.result, jobId: run.jobId, path: base.path },
        `${prefix}run_${run.run}/`
      ).then((files) => files.slice(1))
    ));
    const sourceBuf = await base.file.arrayBuffer();
    return [
      { name: prefix + (base.file.name || "source.jpg"), data: new Uint8Array(sourceBuf) },
      ...groups.flat()
    ];
  }

  function lastResultRuns() {
    if (Array.isArray(lastResult.runs) && lastResult.runs.length) return lastResult.runs;
    return [{ run: 1, result: lastResult.resultText, jobId: lastResult.jobId, status: "done" }];
  }

  // Every download shares the same per-image folder name.
  function imageFolderPrefix(jobId) {
    return `${buildResultBaseName(jobId)}/`;
  }

  async function buildResultZip() {
    const runs = lastResultRuns();
    const current = runs.find((run) => run.jobId === lastResult.jobId) || runs[0];
    return window.PhotoAgentZip.createZip(
      await buildRunFiles(lastResult, [current], imageFolderPrefix(lastResult.jobId))
    );
  }

  async function buildAllRunsZip() {
    const doneRuns = lastResultRuns().filter((run) => run.status === "done");
    return window.PhotoAgentZip.createZip(
      await buildRunFiles(lastResult, doneRuns, imageFolderPrefix(lastResult.jobId))
    );
  }

  // The all-RUNs download link only makes sense once there's more than one
  // finished RUN to bundle -- hidden otherwise so it doesn't duplicate the
  // single-preview download link for the common single-RUN case.
  function updateDownloadLinks(runs) {
    if (!els.downloadAllRunsLink) return;
    const doneCount = (runs || []).filter((run) => run.status === "done").length;
    els.downloadAllRunsLink.hidden = doneCount <= 1;
    if (doneCount > 1) els.downloadAllRunsLink.textContent = `全部下載（${doneCount} 個 RUN）`;
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

  async function handleDownloadAllClick() {
    const done = doneBatchRecords();
    if (!done.length) return;

    els.downloadAllBtn.disabled = true;
    try {
      const groups = await Promise.all(done.map((record) => {
        // Same per-image layout as the single-image downloads: a
        // photoMuse-{date}-{jobId}/ folder with source.jpg + run_N/ subfolders.
        const doneRuns = (record.runs || []).filter((run) => run.status === "done");
        return buildRunFiles({ file: record.file, path: batchSettings.path }, doneRuns, imageFolderPrefix(record.jobId));
      }));
      const zipBlob = window.PhotoAgentZip.createZip(groups.flat());
      const url = URL.createObjectURL(zipBlob);
      const tempLink = document.createElement("a");
      tempLink.href = url;
      tempLink.download = buildDownloadZipFilename();
      document.body.appendChild(tempLink);
      tempLink.click();
      tempLink.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      showError("打包下載失敗，請稍後再試。");
    } finally {
      els.downloadAllBtn.disabled = false;
    }
  }

  // Batch counterpart of setSelectedFile: filter to images (same rule/message),
  // build one record per file, and render the multi-thumbnail strip.
  function setSelectedFileBatch(files) {
    hideError();
    resetBatchState();
    const images = files.filter((file) => file && file.type.startsWith("image/"));
    if (!images.length) {
      showError("請選擇 JPG、PNG 或 WebP 圖片。");
      return;
    }
    if (images.length !== files.length) {
      showError("已略過非圖片檔案。");
    }

    selectedFile = null;
    batchRecords = images.map((file, i) => ({
      localId: `f${i}`,
      file,
      previewUrl: URL.createObjectURL(file),
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
    if (els.changePhotoBtn) els.changePhotoBtn.hidden = false;
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
      thumb.appendChild(im);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "dz-thumb-remove";
      removeBtn.setAttribute("aria-label", "移除照片");
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", (event) => {
        event.preventDefault();
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
    detailPos = 0;
    reviewPos = 0;
    // Any prior single-image run's "回到流程" state refers to a different
    // photo (or no longer applies once batch mode starts) -- drop it.
    singleFlowState = null;
    lastSingleSelections = null;
    if (els.backToProcessBtn) els.backToProcessBtn.hidden = true;
    if (els.dzMulti) { els.dzMulti.hidden = true; els.dzMulti.innerHTML = ""; }
    if (els.resultsGrid) els.resultsGrid.innerHTML = "";
    if (els.resultsGridWrap) els.resultsGridWrap.hidden = true;
    if (els.resultsDetail) els.resultsDetail.hidden = false;
    if (els.detailNav) els.detailNav.hidden = true;
    if (els.runTabs) { els.runTabs.hidden = true; els.runTabs.innerHTML = ""; }
    if (els.backToGridBtn) els.backToGridBtn.hidden = true;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = true;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    if (els.batchProgress) els.batchProgress.hidden = true;
    if (els.batchContinueAllBtn) els.batchContinueAllBtn.hidden = true;
    if (els.batchPromptRow) els.batchPromptRow.hidden = true;
    if (els.batchPromptInput) els.batchPromptInput.value = "";
    if (els.batchOptionsRow) { els.batchOptionsRow.hidden = true; els.batchOptionsRow.open = false; }
    renderBatchStyleOptions();
    updateBatchOptionsLocks(); // batchRecords is now empty, so this clears every category's lock/toggle
    if (els.batchReviewNav) els.batchReviewNav.hidden = true;
  }

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return "無法連線到 Photo Master 伺服器，請確認後端服務是否已啟動。";
    }
    return error.message || "發生未知錯誤。";
  }

  async function startSubmit() {
    hideError();
    if (batchRecords.length > 1) {
      await startSubmitBatch();
      return;
    }
    if (!selectedFile) {
      showError("請先上傳一張照片。");
      return;
    }

    const presetKey = "natural";
    const preset = presets[presetKey] || presets.natural;
    const imageQuality = els.imageQualitySelect?.value || "medium";
    const imageModel = els.imageModelSelect?.value || "gpt-image-2";
    const aiPhotoEditing = els.aiEditToggle.checked;
    const runQa = els.runQaToggle ? els.runQaToggle.checked : true;
    const processingPath = els.processingPathSelect?.value || "local";

    els.startButton.disabled = true;
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
      let analysis;
      let analyzeJobId; // only set for the "local" path, needed by continueJob

      // Also collected into analyzeTimings (not just pushed via addTiming) so
      // the RUN-count > 1 path can prepend Step 2's time onto each RUN's own
      // timings -- that path renders a run's timings array directly instead
      // of the global getTimings() timeline addTiming feeds.
      const analyzeTimings = [];
      const recordAnalyzeTiming = (label, duration, children) => {
        analyzeTimings.push({ label, duration, children });
        addTiming(label, duration, children);
      };

      if (processingPath === "aipoc") {
        const analyzeData = await analyzeAipoc({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing });
        analysis = analyzeData.analysis;
        const t = analysis.timings || {};
        recordAnalyzeTiming("初步分析", Date.now() - stepStart, [
          { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) }
        ]);
      } else {
        analyzeJobId = await analyzeJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing });
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

      singleFlowState = { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, aiPhotoEditing, runQa, processingPath, analyzeTimings };

      showAnalysisPreview(analysis, preset);
      await waitForContinue();
      await finishSingleFlow();
    } catch (error) {
      showSection("upload");
      showError(friendlyError(error));
    } finally {
      els.startButton.disabled = false;
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
    const { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, aiPhotoEditing, runQa, processingPath, analyzeTimings } = singleFlowState;
    const selections = readStyleSelectionsFromDom();
    const userPrompt = els.promptInput.value.trim();
    lastSingleSelections = { userPrompt, ...selections };
    const effectiveAnalysis = buildEffectiveAnalysis(analysis, selections);
    const runCount = Math.max(1, parseInt(els.apRunCountSelect?.value, 10) || 1);

    setAnalysisPreviewDisabled(true);
    procVisual.classList.remove("done");
    if (procTitle) procTitle.textContent = aiPhotoEditing ? "AI 正在修圖" : "正在處理照片";
    if (procSub) {
      procSub.textContent = aiPhotoEditing
        ? `AI 正在使用 ${window.PhotoAgentConfig.imageModels[imageModel] || imageModel} 修圖，請稍候…`
        : "正在進行本機影像處理，請稍候…";
    }

    if (runCount === 1) {
      // Single RUN: unchanged from before RUN-count support existed, so the
      // common case keeps its exact step timeline.
      singleRuns = null;
      singleMirrorIndex = null;

      let stepStart = Date.now();
      let finalJobId;
      if (processingPath === "aipoc") {
        finalJobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, runQa, userPrompt, analysis: effectiveAnalysis, path: "aipoc" });
      } else {
        finalJobId = await continueJob(analyzeJobId, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, runQa, userPrompt });
      }
      addTiming("提交後續處理", Date.now() - stepStart);

      stepStart = Date.now();
      await pollJobStatus(finalJobId, { path: processingPath });
      const finishWaitDuration = Date.now() - stepStart;

      stepStart = Date.now();
      const { image_url: imageUrl, result } = await getJobResult(finalJobId, { path: processingPath });
      const extraInfo = result["extra-info"] || {};
      const t = extraInfo.timings || {};
      addTiming("AI 修圖中", finishWaitDuration, [
        { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
        { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
        { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
        { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
      ]);
      addTiming("取得結果", Date.now() - stepStart);

      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : "本機處理";
      const usedModelLabel = `${window.PhotoAgentConfig.analysisModel} + ${usedImageModelLabel}`;

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
        await runSingleItem(run, { analyzeJobId, effectiveAnalysis, userPrompt, presetKey, imageQuality, imageModel, aiPhotoEditing, runQa, processingPath, analyzeTimings });
        doneCount += 1;
        updateRunProgress();
      }, POLL_CONCURRENCY);

      if (!singleRuns.some((r) => r.status === "done")) {
        throw new Error(singleRuns.find((r) => r.error)?.error || "所有 RUN 皆失敗，請稍後再試。");
      }
      showSingleResult();
    }

    if (els.backToProcessBtn) els.backToProcessBtn.hidden = false;
    showSection("results");
  }

  // One RUN of steps 3-6 for the single-image flow's RUN-count > 1 path --
  // mirrors editBatchItem, but against selectedFile/singleFlowState instead
  // of a batch record.
  async function runSingleItem(run, { analyzeJobId, effectiveAnalysis, userPrompt, presetKey, imageQuality, imageModel, aiPhotoEditing, runQa, processingPath, analyzeTimings }) {
    try {
      let stepStart = Date.now();
      if (processingPath === "aipoc") {
        run.jobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, runQa, userPrompt, analysis: effectiveAnalysis, path: "aipoc" });
      } else {
        run.jobId = await continueJob(analyzeJobId, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, runQa, userPrompt });
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
    const usedModelLabel = `${window.PhotoAgentConfig.analysisModel} + ${usedImageModelLabel}`;

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

  // "回到流程": lets the user come back from the results page to the
  // analysis-preview screen, re-adjust the recipe checklist/style
  // options/prompt (restored from their last submission), and regenerate.
  async function backToProcessFromResults() {
    if (!singleFlowState) return;
    hideError();
    clearContinueWait();
    if (timingDetails) timingDetails.hidden = true;
    resetSteps(); // clears completedTimings so this run's timings don't append to the previous run's
    procVisual.classList.remove("done");
    procVisual.style.height = "";

    showAnalysisPreview(singleFlowState.analysis, singleFlowState.preset);
    if (lastSingleSelections) {
      if (els.promptInput) els.promptInput.value = lastSingleSelections.userPrompt || "";
      applyStyleSelections(lastSingleSelections);
    }
    showSection("processing");

    try {
      await waitForContinue();
      await finishSingleFlow();
    } catch (error) {
      showSection("upload");
      showError(friendlyError(error));
    } finally {
      clearContinueWait();
      if (els.analysisPreview) els.analysisPreview.hidden = true;
    }
  }

  // ── Batch step 1: analyze every image, then show the review grid ─────────
  async function startSubmitBatch() {
    const presetKey = "natural";
    batchSettings = {
      presetKey,
      preset: presets[presetKey] || presets.natural,
      // Prompt is entered per-image (or batch-level) at step 2, not at upload.
      imageQuality: els.imageQualitySelect?.value || "medium",
      imageModel: els.imageModelSelect?.value || "gpt-image-2",
      aiPhotoEditing: els.aiEditToggle.checked,
      runQa: els.runQaToggle ? els.runQaToggle.checked : true,
      path: els.processingPathSelect?.value || "local"
    };

    els.startButton.disabled = true;
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

    els.resultsDetail.hidden = true;
    els.detailNav.hidden = true;
    els.backToGridBtn.hidden = true;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = true;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    els.batchContinueAllBtn.hidden = true;
    els.resultsGridWrap.hidden = false;
    els.batchProgress.hidden = false;
    els.resultSummary.textContent = "批次分析中，先完成的會先顯示。";
    renderBatchGrid();
    updateBatchProgress();
    showSection("results");

    const analyzeArgs = {
      files: batchRecords.map((r) => r.file),
      preset: presetKey,
      imageQuality: batchSettings.imageQuality,
      imageModel: batchSettings.imageModel,
      aiPhotoEditing: batchSettings.aiPhotoEditing
    };

    try {
      if (batchSettings.path === "aipoc") {
        // AIPoC analyze is synchronous local -- one call returns all analyses.
        let entries;
        try {
          entries = await analyzeAipocBatch(analyzeArgs);
        } catch (error) {
          batchRecords.forEach((r) => { r.status = "error"; r.error = friendlyError(error); });
          renderBatchGrid();
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
          updateBatchCard(r);
        });
      } else {
        // Local analyze is queued -- fan out job ids, then poll each.
        let entries;
        try {
          entries = await analyzeJobBatch(analyzeArgs);
        } catch (error) {
          batchRecords.forEach((r) => { r.status = "error"; r.error = friendlyError(error); });
          renderBatchGrid();
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
          updateBatchCard(r);
        });
        const analyzing = batchRecords.filter((r) => r.status === "analyzing" && r.analyzeJobId);
        await runBatchPool(analyzing, analyzeBatchItem, POLL_CONCURRENCY);
      }

      updateBatchProgress();
      enterBatchReview();
    } finally {
      els.startButton.disabled = false;
    }
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
    updateBatchCard(record);
    updateBatchProgress();
  }

  // ── Batch step 2 gate: analyzed grid + the advance-all default button ────
  function enterBatchReview() {
    els.resultsDetail.hidden = true;
    els.detailNav.hidden = true;
    els.backToGridBtn.hidden = true;
    // Entering/re-entering review means no image is "done" yet (either the
    // very first analyze, or a reload that reset every record) -- the
    // done-only actions don't apply until continueBatchRecords produces
    // fresh results.
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = true;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    els.batchReviewNav.hidden = true;
    els.resultsGridWrap.hidden = false;
    const ready = analyzedBatchRecords().length > 0;
    els.batchContinueAllBtn.hidden = !ready;
    els.batchPromptRow.hidden = !ready;
    if (els.batchOptionsRow) els.batchOptionsRow.hidden = !ready;
    els.resultSummary.textContent = ready
      ? "分析完成。可點單張分析給各自補充指令，或在下方填寫整批補充指令後「全部繼續處理」。"
      : "分析未成功，請重試。";
    showSection("results");
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
    const record = currentReviewRecord();
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
    BATCH_OPTION_CATEGORIES.forEach(({ key, group, list, lockRow, lock }) => {
      const hasConflict = batchRecords.some((r) => r.optionsTouched && r.optionsTouched[key]);
      const lockRowEl = els[lockRow];
      const lockEl = els[lock];
      if (lockRowEl) lockRowEl.hidden = !hasConflict;
      if (!hasConflict && lockEl) lockEl.checked = false;
      const locked = hasConflict && !(lockEl && lockEl.checked);
      if (els[group]) els[group].classList.toggle("is-locked", locked);
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

    els.batchContinueAllBtn.hidden = true;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = true;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    els.batchPromptRow.hidden = true;
    if (els.batchOptionsRow) els.batchOptionsRow.hidden = true;
    els.batchReviewNav.hidden = true;
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    els.resultsDetail.hidden = true;
    els.resultsGridWrap.hidden = false;
    showSection("results");

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

    targets.forEach((r) => { r.status = "processing"; updateBatchCard(r); });
    updateBatchProgress();
    els.resultSummary.textContent = batchSettings.aiPhotoEditing
      ? `批次使用 ${window.PhotoAgentConfig.imageModels[batchSettings.imageModel] || batchSettings.imageModel} 修圖中，先完成的會先顯示。`
      : "批次本機影像處理中，先完成的會先顯示。";

    const workItems = targets.flatMap((r) => r.runs.map((run) => ({ record: r, run })));
    await runBatchPool(workItems, editBatchItem, POLL_CONCURRENCY);

    const done = batchRecords.filter((r) => r.status === "done").length;
    els.resultSummary.textContent = `批次完成：${done}/${batchRecords.length} 張成功。`;
    // Keep the advance-all controls if images are still awaiting review.
    const remaining = analyzedBatchRecords().length > 0;
    els.batchContinueAllBtn.hidden = !remaining;
    els.batchPromptRow.hidden = !remaining;
    if (els.batchOptionsRow) els.batchOptionsRow.hidden = !remaining;
    const anyDone = doneBatchRecords().length > 0;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = !anyDone;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = !anyDone;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = !anyDone;
  }

  function batchContinueAll() {
    captureBatchReviewSelections(currentReviewRecord());
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
    updateBatchCard(record);
    updateBatchProgress();
    if (els.detailNav && !els.detailNav.hidden && doneBatchRecords()[detailPos] === record) {
      renderRunTabs(record);
    }
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
  }

  function renderBatchGrid() {
    els.resultsGrid.innerHTML = "";
    batchRecords.forEach((record) => els.resultsGrid.appendChild(buildBatchCard(record)));
  }

  function buildBatchCard(record) {
    const card = document.createElement("div");
    card.className = `result-card ${record.status}`;
    card.dataset.id = record.localId;

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
    media.appendChild(badge);

    const name = document.createElement("p");
    name.className = "rc-name";
    name.textContent = record.file.name;

    card.appendChild(media);
    card.appendChild(name);

    if (record.status === "done") {
      card.classList.add("clickable");
      card.addEventListener("click", () => openBatchDetail(record));
    } else if (record.status === "analyzed") {
      card.classList.add("clickable");
      card.addEventListener("click", () => openBatchAnalysisReview(record));
    } else if (record.status === "error") {
      card.classList.add("clickable");
      card.title = record.error || "處理失敗";
      card.addEventListener("click", () => openBatchError(record));
    }
    return card;
  }

  function updateBatchCard(record) {
    const existing = els.resultsGrid.querySelector(`[data-id="${record.localId}"]`);
    if (existing) existing.replaceWith(buildBatchCard(record));
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

  function doneBatchRecords() {
    return batchRecords.filter((r) => r.status === "done");
  }

  function openBatchDetail(record) {
    gridSummary = els.resultSummary.textContent;
    const done = doneBatchRecords();
    detailPos = Math.max(0, done.indexOf(record));
    showBatchDetail();
  }

  // "回到分析結果": from the finished batch list, send every record that has
  // an analysis back to "analyzed" (without re-calling the analyze API) and
  // reopen the customize/review grid -- lets the user revise the recipe/
  // style options/prompt and re-run continueBatchRecords instead of being
  // stuck with the first edit.
  function backToAnalyzedBatch() {
    batchRecords.forEach((r) => { if (r.analysis) r.status = "analyzed"; });
    renderBatchGrid();
    enterBatchReview();
  }

  // "重新分析": wipe every record's analysis/edit state and re-run step 1
  // from scratch, same as the initial submit -- for when the recipe needs a
  // clean re-analyze rather than a manual revise.
  function reloadBatch() {
    startSubmitBatch();
  }

  // Tab strip for one image's N edit results. Hidden unless there's more than
  // one run. Shared by the batch detail view and the single-image results
  // view; `onSelectDone(idx)` decides what re-pointing to that run means for
  // the caller (batch re-renders via syncRecordMirror+showBatchDetail, single
  // via showSingleResult).
  function buildRunTabs(runs, currentIdx, onSelectDone) {
    els.runTabs.hidden = false;
    els.runTabs.innerHTML = "";
    runs.forEach((run, idx) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `run-tab ${run.status}${idx === currentIdx ? " active" : ""}`;

      if (run.status === "done") {
        const im = document.createElement("img");
        im.src = run.imageUrl;
        tab.appendChild(im);
      } else if (run.status === "processing") {
        const spin = document.createElement("span");
        spin.className = "run-tab-spin";
        tab.appendChild(spin);
      }

      const label = document.createElement("span");
      label.textContent = run.status === "error" ? `RUN ${run.run} ✕` : `RUN ${run.run}`;
      tab.appendChild(label);

      if (run.status === "done") {
        tab.addEventListener("click", () => onSelectDone(idx));
      } else {
        tab.disabled = true;
        if (run.status === "error") tab.title = run.error || "此 RUN 修圖失敗";
      }
      els.runTabs.appendChild(tab);
    });
  }

  // Clicking a finished tab re-points the record's mirror fields at that run
  // and re-renders the whole report.
  function renderRunTabs(record) {
    if (!els.runTabs) return;
    const runs = record.runs || [];
    if (runs.length <= 1) {
      els.runTabs.hidden = true;
      els.runTabs.innerHTML = "";
      return;
    }

    const doneIdx = runs.findIndex((x) => x.status === "done");
    const current = (record.mirrorIndex != null && runs[record.mirrorIndex]) ? record.mirrorIndex : doneIdx;
    buildRunTabs(runs, current, (idx) => {
      record.mirrorIndex = idx;
      syncRecordMirror(record);
      showBatchDetail();
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
      return;
    }

    const doneIdx = runs.findIndex((x) => x.status === "done");
    const current = (singleMirrorIndex != null && runs[singleMirrorIndex]) ? singleMirrorIndex : doneIdx;
    buildRunTabs(runs, current, (idx) => {
      singleMirrorIndex = idx;
      showSingleResult();
    });
  }

  // Reuse the single-image report DOM (renderResult) for the opened record.
  // Set the original image first so resolveCropBoxForDisplay sees its dims.
  function showBatchDetail() {
    const done = doneBatchRecords();
    if (!done.length) return;
    detailPos = Math.min(Math.max(detailPos, 0), done.length - 1);
    const record = done[detailPos];

    els.resultsGridWrap.hidden = true;
    els.resultsDetail.hidden = false;
    els.detailNav.hidden = false;
    els.backToGridBtn.hidden = false;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = true;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    els.detailIndex.textContent = `${detailPos + 1} / ${done.length}`;
    els.detailPrev.disabled = detailPos === 0;
    els.detailNext.disabled = detailPos === done.length - 1;
    renderRunTabs(record);

    const applyRender = () => {
      els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
      // Report the models that actually ran, same label logic as single.
      const extraInfo = record.result["extra-info"] || {};
      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : "本機處理";
      const usedModelLabel = `${window.PhotoAgentConfig.analysisModel} + ${usedImageModelLabel}`;
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
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function backToBatchGrid() {
    els.resultsDetail.hidden = true;
    els.detailNav.hidden = true;
    if (els.runTabs) els.runTabs.hidden = true;
    els.backToGridBtn.hidden = true;
    const anyDone = doneBatchRecords().length > 0;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = !anyDone;
    if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.hidden = !anyDone;
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = !anyDone;
    els.resultsGridWrap.hidden = false;
    if (gridSummary) els.resultSummary.textContent = gridSummary;
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  // ── Batch step-2 per-image review (reuses the single analysis panel) ─────
  function analyzedBatchRecords() {
    return batchRecords.filter((r) => r.status === "analyzed");
  }

  function currentReviewRecord() {
    const list = analyzedBatchRecords();
    if (!list.length) return null;
    return list[Math.min(Math.max(reviewPos, 0), list.length - 1)];
  }

  function openBatchAnalysisReview(record) {
    reviewPos = Math.max(0, analyzedBatchRecords().indexOf(record));
    showBatchReview();
  }

  function showBatchReview() {
    const list = analyzedBatchRecords();
    if (!list.length) { enterBatchReview(); return; }
    reviewPos = Math.min(Math.max(reviewPos, 0), list.length - 1);
    const record = list[reviewPos];

    // Reuse the processing screen's analysis-preview panel verbatim.
    clearContinueWait();
    els.viewResultsWrap.hidden = true;
    procVisual.style.height = "";
    els.procThumb.src = record.previewUrl;
    procVisual.classList.add("done");
    if (procTitle) procTitle.textContent = "初步分析已完成";
    if (procSub) procSub.textContent = "審核初步分析，可調整後繼續。";

    renderAnalysisPreview({ analysis: record.analysis, preset: batchSettings.preset });
    if (els.analysisPreview) els.analysisPreview.hidden = false;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = false;
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = true;
    applyBatchReviewSelections(record);
    // The 補充指令 textarea lives inside #analysis-preview; restore this image's.
    if (els.promptInput) els.promptInput.value = record.userPrompt || "";

    els.batchReviewNav.hidden = false;
    els.batchReviewIndex.textContent = `${reviewPos + 1} / ${list.length}`;
    els.batchReviewPrev.disabled = reviewPos === 0;
    els.batchReviewNext.disabled = reviewPos === list.length - 1;

    showSection("processing");
    window.scrollTo({ top: 0, behavior: "instant" });
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

  function batchReviewBack() {
    captureBatchReviewSelections(currentReviewRecord());
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    els.batchReviewNav.hidden = true;
    enterBatchReview();
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
    if (els.changePhotoBtn) els.changePhotoBtn.hidden = true;
    els.selectedFileName.textContent = "";
    els.origDim.textContent = "";
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    clearContinueWait();
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = true;
    // startSubmit()'s own `finally` never runs if the user resets instead of
    // clicking continue (waitForContinue's promise is left pending), so
    // re-enable Start explicitly here too.
    els.startButton.disabled = false;
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
    // cancels a re-opened file picker (e.g. via #change-photo-btn) -- treat
    // that as a no-op so whatever was already selected (single photo, batch,
    // or the empty drop zone) stays exactly as it was.
    if (!files.length) return;
    if (files.length > 1) {
      setSelectedFileBatch(files);
    } else {
      resetBatchState();
      setSelectedFile(files[0]);
    }
  }

  els.fileInput.addEventListener("change", () => routeSelectedFiles(els.fileInput.files));
  if (els.changePhotoBtn) els.changePhotoBtn.addEventListener("click", () => els.fileInput.click());
  els.startButton.addEventListener("click", startSubmit);
  els.backToProcessBtn.addEventListener("click", backToProcessFromResults);
  els.resetButton.addEventListener("click", resetApp);
  if (els.backToUploadBtn) els.backToUploadBtn.addEventListener("click", resetApp);
  els.downloadLink.addEventListener("click", handleDownloadClick);
  if (els.downloadAllRunsLink) els.downloadAllRunsLink.addEventListener("click", handleDownloadAllRunsClick);
  if (els.backToGridBtn) els.backToGridBtn.addEventListener("click", backToBatchGrid);
  if (els.backToAnalyzedBtn) els.backToAnalyzedBtn.addEventListener("click", backToAnalyzedBatch);
  if (els.reloadBatchBtn) els.reloadBatchBtn.addEventListener("click", reloadBatch);
  if (els.downloadAllBtn) els.downloadAllBtn.addEventListener("click", handleDownloadAllClick);
  if (els.errorModalClose) els.errorModalClose.addEventListener("click", closeErrorModal);
  if (els.errorModal) {
    els.errorModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeErrorModal();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els.errorModal && !els.errorModal.hidden) closeErrorModal();
  });
  if (els.detailPrev) {
    els.detailPrev.addEventListener("click", () => {
      if (detailPos > 0) { detailPos -= 1; showBatchDetail(); }
    });
  }
  if (els.detailNext) {
    els.detailNext.addEventListener("click", () => {
      detailPos += 1;
      showBatchDetail();
    });
  }
  if (els.batchContinueAllBtn) els.batchContinueAllBtn.addEventListener("click", batchContinueAll);
  if (els.batchReviewBack) els.batchReviewBack.addEventListener("click", batchReviewBack);
  if (els.batchReviewPrev) {
    els.batchReviewPrev.addEventListener("click", () => {
      if (reviewPos > 0) {
        captureBatchReviewSelections(currentReviewRecord());
        reviewPos -= 1;
        showBatchReview();
      }
    });
  }
  if (els.batchReviewNext) {
    els.batchReviewNext.addEventListener("click", () => {
      if (reviewPos < analyzedBatchRecords().length - 1) {
        captureBatchReviewSelections(currentReviewRecord());
        reviewPos += 1;
        showBatchReview();
      }
    });
  }
  if (els.batchReviewContinue) {
    els.batchReviewContinue.addEventListener("click", () => {
      const record = currentReviewRecord();
      if (!record) return;
      captureBatchReviewSelections(record);
      if (els.analysisPreview) els.analysisPreview.hidden = true;
      els.batchReviewNav.hidden = true;
      continueBatchRecords([record]);
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
