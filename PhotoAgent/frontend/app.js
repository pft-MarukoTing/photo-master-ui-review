(() => {
  const { els, showSection, showError, hideError, addTiming, getTimings } = window.PhotoAgentDom;
  const { presets } = window.PhotoAgentConfig;
  const { submitJob, pollJobStatus, getJobResult, getUiConfig } = window.PhotoAgentApi;
  const { renderResult } = window.PhotoAgentResults;

  let selectedFile = null;
  let previewUrl = "";

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

  function populateTimingSection() {
    const timings = getTimings();
    if (!timingList || !timingDetails || !timings.length) return;
    timingList.innerHTML = "";
    let total = 0;
    timings.forEach(({ label, duration }) => {
      total += duration;
      const row = document.createElement("div");
      row.className = "timing-row";
      const name = document.createElement("span");
      name.textContent = label;
      const val = document.createElement("span");
      val.className = "timing-value";
      val.textContent = `${(duration / 1000).toFixed(1)}s`;
      row.appendChild(name);
      row.appendChild(val);
      timingList.appendChild(row);
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

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return "無法連線到 Photo Agent 伺服器，請確認後端服務是否已啟動。";
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
    const userPrompt = els.promptInput.value.trim();
    const imageQuality = els.imageQualitySelect?.value || "medium";
    const aiPhotoEditing = els.aiEditToggle.checked;
    const processingPath = els.processingPathSelect?.value || "local";

    els.startButton.disabled = true;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    if (stepsGrid) stepsGrid.hidden = true;
    els.viewResultsWrap.hidden = true;
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    if (procSub) procSub.textContent = "AI 正在分析並修圖，請稍候…";
    showSection("processing");

    const start = Date.now();
    try {
      const jobId = await submitJob({
        file: selectedFile,
        preset: presetKey,
        imageQuality,
        aiPhotoEditing,
        userPrompt,
        path: processingPath
      });
      await pollJobStatus(jobId, { path: processingPath });
      const { image_url: imageUrl, result } = await getJobResult(jobId, { path: processingPath });

      const data = {
        image: imageUrl,
        model: processingPath === "aipoc" ? "Photo Agent (AIPoC)" : "Photo Agent (Local Worker)",
        usage: result.usage,
        qualityCheck: result.quality_check,
        localOnly: !aiPhotoEditing,
        retryCount: 0
      };

      addTiming("AI 修圖", Date.now() - start);
      renderResult({ data, analysis: result.analysis, preset, imageModel: data.model });
      populateTimingSection();
      showSection("results");
    } catch (error) {
      showSection("upload");
      showError(friendlyError(error));
    } finally {
      els.startButton.disabled = false;
    }
  }

  function resetApp() {
    hideError();
    selectedFile = null;
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
    els.viewResultsWrap.hidden = true;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    showSection("upload");
  }

  // ── Event listeners ────────────────────────────────────────────
  els.fileInput.addEventListener("change", () => setSelectedFile(els.fileInput.files?.[0]));
  els.startButton.addEventListener("click", startSubmit);
  els.backToProcessBtn.addEventListener("click", () => {
    showSection("processing");
  });
  els.resetButton.addEventListener("click", resetApp);

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
