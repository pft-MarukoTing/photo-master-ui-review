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
  const { renderResult, renderAnalysisPreview } = window.PhotoAgentResults;

  let selectedFile = null;
  let previewUrl = "";
  let lastResult = null; // { file, resultText, jobId, path } -- for the zip download

  // Batch mode: populated only when more than one file is selected. While
  // batchRecords is empty the app behaves exactly as the single-image flow.
  const POLL_CONCURRENCY = 4;
  let batchRecords = [];   // see setSelectedFileBatch for record shape
  let batchSettings = null;
  let detailPos = 0;       // index into the done-records list for the results detail view
  let reviewPos = 0;       // index into the analyzed-records list for the step-2 review view
  let gridSummary = "";    // grid-view summary text, saved so backToBatchGrid can restore it

  const procVisual = document.querySelector(".proc-visual");
  const stepsGrid = document.querySelector(".steps-grid");
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
    } else {
      getUiConfig().then((cfg) => {
        if (cfg.debugUi) {
          processingPathRow.style.display = "";
          if (els.processingPathSelect) els.processingPathSelect.value = "local";
        }
      });
    }
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

  function buildZipFilename(jobId) {
    return `${buildResultBaseName(jobId)}.zip`;
  }

  function buildBatchZipFilename() {
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

  async function buildResultZip() {
    return window.PhotoAgentZip.createZip(await buildResultFiles(lastResult));
  }

  async function handleDownloadClick(event) {
    event.preventDefault();
    if (!lastResult) return;

    try {
      const zipBlob = await buildResultZip();
      const url = URL.createObjectURL(zipBlob);
      const tempLink = document.createElement("a");
      tempLink.href = url;
      tempLink.download = buildZipFilename(lastResult.jobId);
      document.body.appendChild(tempLink);
      tempLink.click();
      tempLink.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      showError("打包下載失敗，請稍後再試。");
    }
  }

  async function handleDownloadAllClick() {
    const done = doneBatchRecords();
    if (!done.length) return;

    els.downloadAllBtn.disabled = true;
    try {
      const groups = await Promise.all(done.map((record) => {
        // Name each image's folder like its own single-download zip would be.
        const prefix = `${buildResultBaseName(record.jobId)}/`;
        const results = { file: record.file, resultText: record.result, jobId: record.jobId, path: batchSettings.path };
        return buildResultFiles(results, prefix);
      }));
      const zipBlob = window.PhotoAgentZip.createZip(groups.flat());
      const url = URL.createObjectURL(zipBlob);
      const tempLink = document.createElement("a");
      tempLink.href = url;
      tempLink.download = buildBatchZipFilename();
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
    els.dzMulti.innerHTML = "";
    batchRecords.forEach((r) => {
      const thumb = document.createElement("div");
      thumb.className = "dz-thumb";
      const im = document.createElement("img");
      im.src = r.previewUrl;
      thumb.appendChild(im);
      els.dzMulti.appendChild(thumb);
    });
    const count = document.createElement("p");
    count.className = "dz-count";
    count.textContent = `已選擇 ${batchRecords.length} 張`;
    els.dzMulti.appendChild(count);
  }

  // Drop batch state and restore the results view to its single-image default,
  // so the untouched single flow always starts from a known layout.
  function resetBatchState() {
    batchRecords.forEach((r) => { if (r.previewUrl) URL.revokeObjectURL(r.previewUrl); });
    batchRecords = [];
    batchSettings = null;
    detailPos = 0;
    reviewPos = 0;
    if (els.dzMulti) { els.dzMulti.hidden = true; els.dzMulti.innerHTML = ""; }
    if (els.resultsGrid) els.resultsGrid.innerHTML = "";
    if (els.resultsGridWrap) els.resultsGridWrap.hidden = true;
    if (els.resultsDetail) els.resultsDetail.hidden = false;
    if (els.detailNav) els.detailNav.hidden = true;
    if (els.backToGridBtn) els.backToGridBtn.hidden = true;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    if (els.batchProgress) els.batchProgress.hidden = true;
    if (els.batchContinueAllBtn) els.batchContinueAllBtn.hidden = true;
    if (els.batchPromptRow) els.batchPromptRow.hidden = true;
    if (els.batchPromptInput) els.batchPromptInput.value = "";
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
    const processingPath = els.processingPathSelect?.value || "local";

    els.startButton.disabled = true;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    resetSteps(); // clears completedTimings so each run's total starts fresh
    if (stepsGrid) stepsGrid.hidden = true;
    clearContinueWait();
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    if (els.promptInput) els.promptInput.value = "";
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    if (procSub) procSub.textContent = "AI 正在分析照片，請稍候…";
    showSection("processing");

    try {
      let stepStart = Date.now();
      let analysis;
      let analyzeJobId; // only set for the "local" path, needed by continueJob

      if (processingPath === "aipoc") {
        const analyzeData = await analyzeAipoc({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing });
        analysis = analyzeData.analysis;
        const t = analysis.timings || {};
        addTiming("初步分析", Date.now() - stepStart, [
          { label: "Step 2：照片分析", duration: secToMs(t.photo_analysis) }
        ]);
      } else {
        analyzeJobId = await analyzeJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing });
        addTiming("送出分析任務", Date.now() - stepStart);

        stepStart = Date.now();
        await pollJobStatus(analyzeJobId, { path: "local" });
        const analyzeWaitDuration = Date.now() - stepStart;

        stepStart = Date.now();
        const analyzeResult = await getAnalyzeResult(analyzeJobId);
        analysis = analyzeResult.result.analysis;
        const t = analysis.timings || {};
        addTiming("分析中", analyzeWaitDuration, [
          { label: "Step 2：照片分析", duration: secToMs(t.photo_analysis) }
        ]);
        addTiming("取得分析結果", Date.now() - stepStart);
      }

      showAnalysisPreview(analysis, preset);
      await waitForContinue();
      const userPrompt = els.promptInput.value.trim();
      const effectiveAnalysis = buildEffectiveAnalysis(analysis, readStyleSelectionsFromDom());
      if (procSub) {
        procSub.textContent = aiPhotoEditing
          ? `AI 正在使用 ${window.PhotoAgentConfig.imageModels[imageModel] || imageModel} 修圖，請稍候…`
          : "正在進行本機影像處理，請稍候…";
      }

      stepStart = Date.now();
      let finalJobId;
      if (processingPath === "aipoc") {
        finalJobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, userPrompt, analysis: effectiveAnalysis, path: "aipoc" });
      } else {
        finalJobId = await continueJob(analyzeJobId, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, userPrompt });
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
        { label: "Step 3：使用者指令審查", duration: secToMs(t.user_instruction_review) },
        { label: "Step 4：裁切驗證", duration: secToMs(t.crop_validation) },
        { label: "Step 5：修圖執行", duration: secToMs(t.image_edit) },
        { label: "Step 6：品質檢查", duration: secToMs(t.quality_check) }
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
      renderResult({ data, analysis: result.analysis, preset, imageModel: data.model });
      populateTimingSection();
      showSection("results");
    } catch (error) {
      showSection("upload");
      showError(friendlyError(error));
    } finally {
      els.startButton.disabled = false;
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
    });

    els.resultsDetail.hidden = true;
    els.detailNav.hidden = true;
    els.backToGridBtn.hidden = true;
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
            r.analyzeTimings = [{ label: "初步分析", duration: secToMs(t.photo_analysis) }];
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
        { label: "Step 2：照片分析", duration: secToMs(t.photo_analysis) }
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
    els.batchReviewNav.hidden = true;
    els.resultsGridWrap.hidden = false;
    const ready = analyzedBatchRecords().length > 0;
    els.batchContinueAllBtn.hidden = !ready;
    els.batchPromptRow.hidden = !ready;
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

  // Run step 2 (edit) for the given analyzed records via the same single-image
  // endpoints, so each image carries its own prompt -- flow identical to single.
  async function continueBatchRecords(records) {
    const targets = records.filter((r) => r.status === "analyzed");
    if (!targets.length) return;

    els.batchContinueAllBtn.hidden = true;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = true;
    els.batchPromptRow.hidden = true;
    els.batchReviewNav.hidden = true;
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    els.resultsDetail.hidden = true;
    els.resultsGridWrap.hidden = false;
    showSection("results");

    targets.forEach((r) => { r.status = "processing"; updateBatchCard(r); });
    updateBatchProgress();
    els.resultSummary.textContent = batchSettings.aiPhotoEditing
      ? `批次使用 ${window.PhotoAgentConfig.imageModels[batchSettings.imageModel] || batchSettings.imageModel} 修圖中，先完成的會先顯示。`
      : "批次本機影像處理中，先完成的會先顯示。";

    await runBatchPool(targets, editBatchItem, POLL_CONCURRENCY);

    const done = batchRecords.filter((r) => r.status === "done").length;
    els.resultSummary.textContent = `批次完成：${done}/${batchRecords.length} 張成功。`;
    // Keep the advance-all controls if images are still awaiting review.
    const remaining = analyzedBatchRecords().length > 0;
    els.batchContinueAllBtn.hidden = !remaining;
    els.batchPromptRow.hidden = !remaining;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = !doneBatchRecords().length;
  }

  function batchContinueAll() {
    captureBatchReviewSelections(currentReviewRecord());
    continueBatchRecords(analyzedBatchRecords());
  }

  // Step 2 for one record: submit via the single-image endpoint (so it carries
  // the image's own effective prompt) -> poll -> fetch. Records analyze + edit
  // sub-timings, matching the single flow's nested timing display.
  async function editBatchItem(record) {
    try {
      const userPrompt = effectivePrompt(record);
      const effectiveAnalysis = buildEffectiveAnalysis(record.analysis, {
        options: record.options,
        recipeChecked: record.recipeChecked
      });
      let stepStart = Date.now();
      if (batchSettings.path === "aipoc") {
        record.jobId = await submitJob({
          file: record.file,
          preset: batchSettings.presetKey,
          imageQuality: batchSettings.imageQuality,
          imageModel: batchSettings.imageModel,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          userPrompt,
          analysis: effectiveAnalysis,
          path: "aipoc"
        });
      } else {
        record.jobId = await continueJob(record.analyzeJobId, {
          analysis: effectiveAnalysis,
          preset: batchSettings.presetKey,
          imageQuality: batchSettings.imageQuality,
          imageModel: batchSettings.imageModel,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          userPrompt
        });
      }
      const submitMs = Date.now() - stepStart;

      stepStart = Date.now();
      await pollJobStatus(record.jobId, { path: batchSettings.path });
      const pollMs = Date.now() - stepStart;

      stepStart = Date.now();
      const { image_url: imageUrl, result } = await getJobResult(record.jobId, { path: batchSettings.path });
      const fetchMs = Date.now() - stepStart;

      record.status = "done";
      record.imageUrl = imageUrl;
      record.result = result;
      // Finish-stage timings moved into result["extra-info"] (matches single).
      const t = (result["extra-info"] || {}).timings || {};
      record.timings = [
        ...(record.analyzeTimings || []),
        { label: "提交後續處理", duration: submitMs },
        { label: "AI 修圖中", duration: pollMs, children: [
          { label: "Step 3：使用者指令審查", duration: secToMs(t.user_instruction_review) },
          { label: "Step 4：裁切驗證", duration: secToMs(t.crop_validation) },
          { label: "Step 5：修圖執行", duration: secToMs(t.image_edit) },
          { label: "Step 6：品質檢查", duration: secToMs(t.quality_check) }
        ] },
        { label: "取得結果", duration: fetchMs }
      ];
    } catch (error) {
      record.status = "error";
      record.error = friendlyError(error);
    }
    updateBatchCard(record);
    updateBatchProgress();
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
      els.batchProgress.textContent = `完成 ${done}/${total}${failText}`;
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
    if (record.status === "analyzing" || record.status === "processing") {
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
    els.detailIndex.textContent = `${detailPos + 1} / ${done.length}`;
    els.detailPrev.disabled = detailPos === 0;
    els.detailNext.disabled = detailPos === done.length - 1;

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
      // single flow sets so handleDownloadClick works here too.
      lastResult = { file: record.file, resultText: record.result, jobId: record.jobId, path: batchSettings.path };
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
    els.backToGridBtn.hidden = true;
    if (els.downloadAllBtn) els.downloadAllBtn.hidden = !doneBatchRecords().length;
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
    if (stepsGrid) stepsGrid.hidden = true;
    clearContinueWait();
    els.viewResultsWrap.hidden = true;
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    els.procThumb.src = record.previewUrl;
    if (procSub) procSub.textContent = "審核初步分析，可調整後繼續。";

    renderAnalysisPreview({ analysis: record.analysis, preset: batchSettings.preset });
    if (els.analysisPreview) els.analysisPreview.hidden = false;
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

  function applyBatchReviewSelections(record) {
    if (record.options) {
      const map = { "ap-pose": "pose", "ap-angle": "angle", "ap-lighting": "lighting", "ap-size": "size" };
      Object.entries(map).forEach(([name, key]) => {
        const input = document.querySelector(`input[name="${name}"][value="${record.options[key]}"]`);
        if (input) input.checked = true;
      });
    }
    if (Array.isArray(record.recipeChecked) && els.apRecipeList) {
      const boxes = els.apRecipeList.querySelectorAll("input[type=checkbox]");
      record.recipeChecked.forEach((isChecked, i) => { if (boxes[i]) boxes[i].checked = isChecked; });
    }
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
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";
    resetBatchState();

    els.fileInput.value = "";
    els.dzPreviewImg.removeAttribute("src");
    els.procThumb.removeAttribute("src");
    els.originalImg.removeAttribute("src");
    els.generatedImg.removeAttribute("src");
    els.downloadLink.removeAttribute("href");
    els.dropZone.hidden = false;
    els.dzPreview.hidden = true;
    els.selectedFileName.textContent = "";
    els.origDim.textContent = "";
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    clearContinueWait();
    if (els.analysisPreview) els.analysisPreview.hidden = true;
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
    if (files.length > 1) {
      setSelectedFileBatch(files);
    } else {
      resetBatchState();
      setSelectedFile(files[0]);
    }
  }

  els.fileInput.addEventListener("change", () => routeSelectedFiles(els.fileInput.files));
  els.startButton.addEventListener("click", startSubmit);
  els.backToProcessBtn.addEventListener("click", () => {
    showSection("processing");
  });
  els.resetButton.addEventListener("click", resetApp);
  els.downloadLink.addEventListener("click", handleDownloadClick);
  if (els.backToGridBtn) els.backToGridBtn.addEventListener("click", backToBatchGrid);
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
