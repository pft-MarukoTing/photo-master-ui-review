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
    getAnalyzeResult,
    continueJob,
    analyzeAipoc
  } = window.PhotoAgentApi;
  const { renderResult, renderAnalysisPreview } = window.PhotoAgentResults;

  let selectedFile = null;
  let previewUrl = "";
  let lastResult = null; // { file, resultText, jobId, path } -- for the zip download

  const procVisual = document.querySelector(".proc-visual");
  const stepsGrid = document.querySelector(".steps-grid");
  const procSub = document.querySelector(".proc-sub");
  const timingDetails = document.querySelector("#timing-details");
  const timingList = document.querySelector("#timing-list");
  const processingPathRow = document.querySelector("#processing-path-row");

  // Shown in the URL-debug case (?debug etc.) or when the backend was
  // started with `--debug-ui` / DEBUG_UI=true (testbed deployments).
  if (processingPathRow) {
    if (window.location.href.includes("debug")) {
      processingPathRow.style.display = "";
    } else {
      getUiConfig().then((cfg) => {
        if (cfg.debugUi) processingPathRow.style.display = "";
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

  function populateTimingSection() {
    const timings = getTimings();
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
  function buildZipFilename(jobId) {
    const pad = (n) => String(n).padStart(2, "0");
    const now = new Date();
    const dateStr = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
    // AIPoC task ids contain "/" and "+", which aren't safe in a filename.
    const safeJobId = String(jobId).replace(/[^a-zA-Z0-9._-]/g, "_");
    return `photoMuse-${dateStr}-${safeJobId}.zip`;
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

  async function buildResultZip() {
    const { file, resultText, jobId, path } = lastResult;
    // Fetch the image via our own backend, not the presigned S3/AIPoC URL --
    // that URL has no CORS headers, so a browser-side fetch() of it is
    // blocked even though the same URL displays fine in an <img> tag.
    const [sourceBuf, imageBuf] = await Promise.all([
      file.arrayBuffer(),
      fetch(resultImageUrl(jobId, { path })).then((response) => response.arrayBuffer())
    ]);
    const jsonBytes = new TextEncoder().encode(JSON.stringify(resultText, null, 2));
    const promptsBytes = new TextEncoder().encode(buildPromptsJson(resultText.analysis?.prompts || {}));

    return window.PhotoAgentZip.createZip([
      { name: file.name || "source.jpg", data: new Uint8Array(sourceBuf) },
      { name: "result.png", data: new Uint8Array(imageBuf) },
      { name: "result.json", data: jsonBytes },
      { name: "prompts.json", data: promptsBytes }
    ]);
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

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return "無法連線到 Photo Master 伺服器，請確認後端服務是否已啟動。";
    }
    return error.message || "發生未知錯誤。";
  }

  async function startSubmit() {
    hideError();
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
      if (procSub) procSub.textContent = "AI 正在修圖，請稍候…";

      stepStart = Date.now();
      let finalJobId;
      if (processingPath === "aipoc") {
        finalJobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, userPrompt, path: "aipoc" });
      } else {
        finalJobId = await continueJob(analyzeJobId, { analysis, preset: presetKey, imageQuality, imageModel, aiPhotoEditing, userPrompt });
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

  function resetApp() {
    hideError();
    selectedFile = null;
    lastResult = null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";

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
  els.fileInput.addEventListener("change", () => setSelectedFile(els.fileInput.files?.[0]));
  els.startButton.addEventListener("click", startSubmit);
  els.backToProcessBtn.addEventListener("click", () => {
    showSection("processing");
  });
  els.resetButton.addEventListener("click", resetApp);
  els.downloadLink.addEventListener("click", handleDownloadClick);

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
    setSelectedFile(event.dataTransfer.files?.[0]);
  });
})();
