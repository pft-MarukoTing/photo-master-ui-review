(() => {
  const { els, showSection, showError, hideError, setStep, resetSteps, addTiming, getTimings } = window.PhotoAgentDom;
  const { presets } = window.PhotoAgentConfig;
  const { normalizeCropBox, cropValidationRisk, createLocalEnhancedDataUrl } = window.PhotoAgentImageTools;
  const { analyzePhoto, reviewUserInstruction, validateCrop, editPhoto, qualityCheckPhoto } = window.PhotoAgentApi;
  const { renderResult } = window.PhotoAgentResults;
  const imageModel = window.PhotoAgentPrompts.models.image;

  let selectedFile = null;
  let previewUrl = "";
  let lastAnalysis = null;
  let lastRunContext = null;
  let pendingContext = null; // holds step 1-4 results, waiting for user to click 查看修圖結果

  const procVisual = document.querySelector(".proc-visual");
  const timingDetails = document.querySelector("#timing-details");
  const timingList = document.querySelector("#timing-list");

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

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function setSelectedFile(file) {
    hideError();
    if (!file || !file.type.startsWith("image/")) {
      showError("請選擇 JPG、PNG 或 WebP 圖片。");
      return;
    }

    selectedFile = file;
    lastAnalysis = null;
    lastRunContext = null;
    pendingContext = null;
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

  async function runStep(index, text, delay = 420) {
    setStep(index, "active", text);
    await sleep(delay);
  }

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return "無法連線到 OpenAI API。Demo 請確認 local server、網路連線與 API key。";
    }
    return error.message || "發生未知錯誤。";
  }

  function qualityCheckFallback(error) {
    return {
      pass: false,
      overall_status: "QA 未完成",
      score: null,
      summary: "輸出已完成，但品質檢查階段未成功回傳可解析結果。",
      checks: { subject_preserved: null, crop_safe: null, not_overedited: null, light_color_improved: null },
      warnings: [error.message || "品質檢查失敗"],
      recommendation: "請人工快速比對原圖與輸出圖。"
    };
  }

  function effectiveUserDirection(analysis, rawDirection) {
    return analysis?.user_instruction?.normalized_user_direction || rawDirection || "";
  }

  function userDirectionAffectsCrop(userInstruction, rawDirection) {
    if (userInstruction?.crop_or_framing_requested === true) return true;
    const text = [
      rawDirection,
      userInstruction?.user_intent_summary,
      userInstruction?.normalized_user_direction,
      userInstruction?.crop_guidance,
      ...(userInstruction?.explicit_priorities || []),
      ...(userInstruction?.explicit_prohibitions || [])
    ].filter(Boolean).join(" ").toLowerCase();
    return /裁切|不要裁|別裁|構圖|取景|比例|留白|邊緣|邊界|全身|半身|特寫|置中|水平|透視|crop|framing|frame|reframe|aspect|composition|center|edge|border/.test(text);
  }

  // ── Steps 1–4: analysis pipeline ──────────────────────────────
  async function startDemo() {
    hideError();
    if (!selectedFile) {
      showError("請先上傳一張照片。");
      return;
    }

    const apiKey = els.apiKeyInput.value.trim();
    if (!apiKey) {
      showError("請輸入 OpenAI API Key。");
      return;
    }

    const presetKey = "natural";
    const preset = presets[presetKey] || presets.natural;
    const rawUserDirection = els.promptInput.value.trim();
    const imageQuality = els.imageQualitySelect?.value || "medium";
    lastRunContext = null;
    pendingContext = null;

    els.startButton.disabled = true;
    resetSteps();
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    els.viewResultsWrap.hidden = true;
    showSection("processing");

    try {
      await runStep(1, "讀取原圖與建立預覽。");
      setStep(1, "done", "原圖已載入");

      await runStep(2, "分析照片類型、主體、光線與色彩。", 300);
      lastAnalysis = await analyzePhoto({ file: selectedFile, apiKey });
      setStep(2, "done", `${lastAnalysis.subject_category || "未知"} / ${lastAnalysis.scene_category || "未知"}`);

      await runStep(3, rawUserDirection ? "解讀使用者指令並整理修圖方向。" : "整理美學診斷、修圖方向與保留原則。");
      const userInstruction = await reviewUserInstruction({
        userDirection: rawUserDirection,
        analysis: lastAnalysis,
        apiKey
      });
      lastAnalysis = { ...lastAnalysis, user_instruction: userInstruction };
      const issues = (lastAnalysis.detected_issues || []).filter(Boolean);
      const step3Detail = userInstruction.has_user_direction
        ? `使用者目標：${userInstruction.user_intent_summary || userInstruction.normalized_user_direction || "已套用"}`
        : {
            summary: `找到 ${issues.length} 個主要可改善點`,
            list: issues
          };
      setStep(3, "done", step3Detail);

      await runStep(4, "檢查裁切建議與 ROI 安全性。");
      const plannedCrop = normalizeCropBox(lastAnalysis.crop_box, lastAnalysis);
      const hasAiCropCandidate = Array.isArray(lastAnalysis.crop_candidates)
        && lastAnalysis.crop_candidates.some((c) => {
          const cropBox = c?.crop_box || c?.cropBox;
          return cropBox?.enabled === true;
        });
      const hasUserCropDirection = userDirectionAffectsCrop(lastAnalysis.user_instruction, rawUserDirection);
      const cropRisk = cropValidationRisk(lastAnalysis);
      if (plannedCrop || hasAiCropCandidate || hasUserCropDirection) {
        lastAnalysis = await validateCrop({
          file: selectedFile,
          analysis: lastAnalysis,
          apiKey,
          risk: hasUserCropDirection ? { level: "high", reasons: ["user-crop-instruction"] } : cropRisk,
          forceValidation: hasUserCropDirection
        });
      }
      const cropPreview = normalizeCropBox(lastAnalysis.crop_box, lastAnalysis) ? "套用裁切 / reframe" : "保留原始構圖";
      const validationPreview = lastAnalysis.crop_validation
        ? `已評選：${lastAnalysis.crop_validation.selected_candidate_label || lastAnalysis.crop_validation.selected_candidate_id || "最佳裁切"}`
        : "不需額外評選";
      const planPreview = [
        cropPreview,
        (plannedCrop || hasAiCropCandidate || hasUserCropDirection) ? validationPreview : "",
        ...(lastAnalysis.correction_plan || []).slice(0, 1)
      ].filter(Boolean).join(" / ");
      setStep(4, "done", planPreview || "構圖策略完成");

      // Store context for the edit step, then show the "查看修圖結果" button
      pendingContext = { file: selectedFile, apiKey, presetKey, preset, rawUserDirection, imageQuality };
      procVisual.classList.add("done");
      if (els.procThumb.naturalWidth > 0) {
        const h = Math.round(280 * els.procThumb.naturalHeight / els.procThumb.naturalWidth);
        procVisual.style.height = `${h}px`;
      }
      els.viewResultsWrap.hidden = false;
    } catch (error) {
      showSection("upload");
      showError(friendlyError(error));
    } finally {
      els.startButton.disabled = false;
    }
  }

  // ── Edit + QA: triggered by 查看修圖結果 button ───────────────
  async function startEdit() {
    // Already computed — just navigate to results
    if (lastRunContext) {
      showSection("results");
      return;
    }
    if (!pendingContext) return;

    const btn = els.viewResultsBtn;
    btn.disabled = true;
    const origBtnHtml = btn.innerHTML;
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style="animation:spin 1s linear infinite">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2.5" stroke-dasharray="40 20" opacity=".4"/>
        <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
      </svg>
      生成輸出圖像中…`;

    const { file, apiKey, presetKey, preset, rawUserDirection, imageQuality } = pendingContext;
    const useAiEdit = els.aiEditToggle.checked;
    const editStart = Date.now();

    try {
      const data = useAiEdit
        ? await editPhoto({
            file,
            analysis: lastAnalysis,
            apiKey,
            quality: imageQuality,
            presetKey,
            preset,
            userDirection: effectiveUserDirection(lastAnalysis, rawUserDirection)
          })
        : {
            image: await createLocalEnhancedDataUrl(file, lastAnalysis.crop_box, lastAnalysis),
            model: "Local Canvas",
            usage: null,
            localOnly: true
          };

      if (useAiEdit) {
        try {
          data.qualityCheck = await qualityCheckPhoto({
            file,
            outputImageDataUrl: data.image,
            analysis: lastAnalysis,
            apiKey
          });
        } catch (qualityError) {
          data.qualityCheck = qualityCheckFallback(qualityError);
        }
      } else {
        data.qualityCheck = {
          pass: true,
          overall_status: "本機預覽",
          score: null,
          summary: "本機 Canvas 模式只做簡易裁切與色彩增強，未執行 AI 輸出 QA。",
          checks: { subject_preserved: true, crop_safe: true, not_overedited: true, light_color_improved: true },
          warnings: [],
          recommendation: "若要檢查生成式修圖品質，請開啟 AI 修圖。"
        };
      }

      data.retryCount = 0;
      addTiming("生成輸出", Date.now() - editStart);
      renderResult({ data, analysis: lastAnalysis, preset, imageModel });
      populateTimingSection();
      lastRunContext = {
        file,
        apiKey,
        presetKey,
        preset,
        rawUserDirection,
        userDirection: effectiveUserDirection(lastAnalysis, rawUserDirection),
        imageQuality,
        analysis: lastAnalysis,
        data,
        retryCount: 0
      };
      els.viewResultsWrap.hidden = true;
      showSection("results");
    } catch (error) {
      showError(friendlyError(error));
    } finally {
      btn.disabled = false;
      btn.innerHTML = origBtnHtml;
    }
  }

  function resetApp() {
    hideError();
    selectedFile = null;
    lastAnalysis = null;
    lastRunContext = null;
    pendingContext = null;
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
    resetSteps();
    showSection("upload");
  }

  // ── Event listeners ────────────────────────────────────────────
  els.fileInput.addEventListener("change", () => setSelectedFile(els.fileInput.files?.[0]));
  els.startButton.addEventListener("click", startDemo);
  els.viewResultsBtn.addEventListener("click", startEdit);
  els.backToProcessBtn.addEventListener("click", () => {
    els.viewResultsWrap.hidden = false;
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
