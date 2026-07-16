window.PhotoAgentDom = (() => {
  const els = {
    sections: {
      upload: document.querySelector("#upload-section"),
      processing: document.querySelector("#processing-section"),
      results: document.querySelector("#results-section")
    },
    fileInput: document.querySelector("#file-input"),
    dropZone: document.querySelector("#drop-zone"),
    dzPreview: document.querySelector("#dz-preview"),
    dzPreviewImg: document.querySelector("#dz-preview-img"),
    dzMulti: document.querySelector("#dz-multi"),
    changePhotoBtn: document.querySelector("#change-photo-btn"),
    selectedFileName: document.querySelector("#selected-file-name"),
    procThumb: document.querySelector("#proc-thumb"),
    startButton: document.querySelector("#start-btn"),
    resetButton: document.querySelector("#reset-btn"),
    viewResultsWrap: document.querySelector("#view-results-wrap"),
    viewResultsBtn: document.querySelector("#view-results-btn"),
    backToProcessBtn: document.querySelector("#back-to-process-btn"),
    analysisPreview: document.querySelector("#analysis-preview"),
    apSubjectTags: document.querySelector("#ap-subject-tags"),
    apSubject: document.querySelector("#ap-subject"),
    apDiagnosis: document.querySelector("#ap-diagnosis"),
    apComposition: document.querySelector("#ap-composition"),
    apRecipeList: document.querySelector("#ap-recipe-list"),
    apOptionsToggle: document.querySelector("#ap-options-toggle"),
    apGroupPose: document.querySelector("#ap-group-pose"),
    apGroupAngle: document.querySelector("#ap-group-angle"),
    apPoseList: document.querySelector("#ap-pose-list"),
    apAngleList: document.querySelector("#ap-angle-list"),
    apLightingList: document.querySelector("#ap-lighting-list"),
    apSizeList: document.querySelector("#ap-size-list"),
    errorMsg: document.querySelector("#error-msg"),
    promptInput: document.querySelector("#prompt"),
    processingPathSelect: document.querySelector("#processing-path"),
    aiEditToggle: document.querySelector("#ai-edit-toggle"),
    imageQualitySelect: document.querySelector("#image-quality"),
    imageModelSelect: document.querySelector("#image-model"),
    apImageQualitySelect: document.querySelector("#ap-image-quality"),
    apImageModelSelect: document.querySelector("#ap-image-model"),
    originalImg: document.querySelector("#original-img"),
    generatedImg: document.querySelector("#generated-img"),
    downloadLink: document.querySelector("#download-link"),
    modelName: document.querySelector("#model-name"),
    origDim: document.querySelector("#orig-dim"),
    resultSummary: document.querySelector("#res-summary"),
    batchProgress: document.querySelector("#batch-progress"),
    resultsGridWrap: document.querySelector("#results-grid-wrap"),
    resultsGrid: document.querySelector("#results-grid"),
    resultsDetail: document.querySelector("#results-detail"),
    detailNav: document.querySelector("#detail-nav"),
    detailPrev: document.querySelector("#detail-prev"),
    detailNext: document.querySelector("#detail-next"),
    detailIndex: document.querySelector("#detail-index"),
    runTabs: document.querySelector("#run-tabs"),
    runCountSelect: document.querySelector("#run-count"),
    backToGridBtn: document.querySelector("#back-to-grid-btn"),
    downloadAllBtn: document.querySelector("#download-all-btn"),
    batchContinueAllBtn: document.querySelector("#batch-continue-all-btn"),
    batchPromptRow: document.querySelector("#batch-prompt-row"),
    batchPromptInput: document.querySelector("#batch-prompt"),
    batchOptionsRow: document.querySelector("#batch-options-row"),
    batchPoseList: document.querySelector("#batch-pose-list"),
    batchAngleList: document.querySelector("#batch-angle-list"),
    batchLightingList: document.querySelector("#batch-lighting-list"),
    batchSizeList: document.querySelector("#batch-size-list"),
    batchGroupPose: document.querySelector("#batch-group-pose"),
    batchGroupAngle: document.querySelector("#batch-group-angle"),
    batchGroupLighting: document.querySelector("#batch-group-lighting"),
    batchGroupSize: document.querySelector("#batch-group-size"),
    batchPoseLockRow: document.querySelector("#batch-pose-lock-row"),
    batchAngleLockRow: document.querySelector("#batch-angle-lock-row"),
    batchLightingLockRow: document.querySelector("#batch-lighting-lock-row"),
    batchSizeLockRow: document.querySelector("#batch-size-lock-row"),
    batchPoseLock: document.querySelector("#batch-pose-lock"),
    batchAngleLock: document.querySelector("#batch-angle-lock"),
    batchLightingLock: document.querySelector("#batch-lighting-lock"),
    batchSizeLock: document.querySelector("#batch-size-lock"),
    batchReviewNav: document.querySelector("#batch-review-nav"),
    batchReviewPrev: document.querySelector("#batch-review-prev"),
    batchReviewNext: document.querySelector("#batch-review-next"),
    batchReviewIndex: document.querySelector("#batch-review-index"),
    batchReviewBack: document.querySelector("#batch-review-back"),
    batchReviewContinue: document.querySelector("#batch-review-continue"),
    errorModal: document.querySelector("#error-modal"),
    errorModalBox: document.querySelector(".error-modal-box"),
    errorModalClose: document.querySelector("#error-modal-close"),
    errorModalFile: document.querySelector("#error-modal-file"),
    errorModalMsg: document.querySelector("#error-modal-msg"),
    subjectText: document.querySelector("#r-subject"),
    subjectTags: document.querySelector("#r-subject-tags"),
    diagnosisText: document.querySelector("#diagnosis-text"),
    philosophyText: document.querySelector("#philosophy-text"),
    moodText: document.querySelector("#mood-text"),
    compositionText: document.querySelector("#composition-text"),
    roiText: document.querySelector("#roi-text"),
    toolText: document.querySelector("#tool-text"),
    lightColorText: document.querySelector("#light-color-text"),
    styleOptionsText: document.querySelector("#style-options-text"),
    cropText: document.querySelector("#crop-text"),
    qualityText: document.querySelector("#quality-text"),
    qaScore: document.querySelector("#qa-score"),
    recipeVerdictScore: document.querySelector("#recipe-verdict-score"),
    qaList: document.querySelector("#qa-list"),
    cropCandidatesList: document.querySelector("#crop-candidates-list"),
    issuesList: document.querySelector("#issues-list"),
    recipeList: document.querySelector("#recipe-list"),
    recipeScoreText: document.querySelector("#recipe-score-text"),
    recipeScoreList: document.querySelector("#recipe-score-list"),
    correctionList: document.querySelector("#correction-list"),
    preserveList: document.querySelector("#preserve-list"),
    promptAnalysis: document.querySelector("#prompt-analysis"),
    promptReview: document.querySelector("#prompt-review"),
    promptCrop: document.querySelector("#prompt-crop"),
    promptEdit: document.querySelector("#prompt-edit"),
    promptQa: document.querySelector("#prompt-qa")
  };

  function showSection(name) {
    Object.values(els.sections).forEach((section) => section.classList.remove("visible"));
    els.sections[name]?.classList.add("visible");
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function showError(message) {
    els.errorMsg.textContent = message;
    els.errorMsg.hidden = false;
  }

  function hideError() {
    els.errorMsg.textContent = "";
    els.errorMsg.hidden = true;
  }

  const stepStartTimes = {};
  const completedTimings = []; // {label, duration (ms)}

  function setStep(index, state, detail) {
    const step = document.querySelector(`#step-${index}`);
    const detailEl = document.querySelector(`#d${index}`);
    if (!step || !detailEl) return;

    const timeEl = document.querySelector(`#t${index}`);
    if (state === "active") {
      stepStartTimes[index] = Date.now();
      if (timeEl) timeEl.textContent = "";
    } else if (state === "done" && stepStartTimes[index] != null) {
      const duration = Date.now() - stepStartTimes[index];
      if (timeEl) timeEl.textContent = `${(duration / 1000).toFixed(1)}s`;
      const label = document.querySelector(`#step-${index} .step-name`)?.textContent || `步驟 ${index}`;
      completedTimings.push({ label, duration });
      delete stepStartTimes[index];
    } else if (!state) {
      if (timeEl) timeEl.textContent = "";
      delete stepStartTimes[index];
    }

    step.classList.remove("active", "done");
    if (state) step.classList.add(state);

    if (detail && typeof detail === "object" && detail.list) {
      detailEl.innerHTML = "";
      if (detail.summary) {
        const p = document.createElement("p");
        p.textContent = detail.summary;
        detailEl.appendChild(p);
      }
      if (detail.list.length) {
        const ul = document.createElement("ul");
        ul.className = "step-issues-list";
        detail.list.forEach((item) => {
          const li = document.createElement("li");
          li.textContent = item;
          ul.appendChild(li);
        });
        detailEl.appendChild(ul);
      }
    } else {
      detailEl.textContent = typeof detail === "string" ? detail : "";
    }
  }

  function resetSteps() {
    completedTimings.length = 0;
    for (let i = 1; i <= 4; i += 1) {
      setStep(i, "", "等待中");
    }
  }

  function addTiming(label, duration, children) {
    completedTimings.push({ label, duration, children: children || [] });
  }

  function getTimings() {
    return [...completedTimings];
  }

  return { els, showSection, showError, hideError, setStep, resetSteps, addTiming, getTimings };
})();
