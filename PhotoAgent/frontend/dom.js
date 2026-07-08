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
    errorMsg: document.querySelector("#error-msg"),
    promptInput: document.querySelector("#prompt"),
    processingPathSelect: document.querySelector("#processing-path"),
    aiEditToggle: document.querySelector("#ai-edit-toggle"),
    imageQualitySelect: document.querySelector("#image-quality"),
    originalImg: document.querySelector("#original-img"),
    generatedImg: document.querySelector("#generated-img"),
    downloadLink: document.querySelector("#download-link"),
    modelName: document.querySelector("#model-name"),
    origDim: document.querySelector("#orig-dim"),
    resultSummary: document.querySelector("#res-summary"),
    subjectText: document.querySelector("#r-subject"),
    subjectTags: document.querySelector("#r-subject-tags"),
    diagnosisText: document.querySelector("#diagnosis-text"),
    philosophyText: document.querySelector("#philosophy-text"),
    moodText: document.querySelector("#mood-text"),
    compositionText: document.querySelector("#composition-text"),
    roiText: document.querySelector("#roi-text"),
    toolText: document.querySelector("#tool-text"),
    lightColorText: document.querySelector("#light-color-text"),
    cropText: document.querySelector("#crop-text"),
    qualityText: document.querySelector("#quality-text"),
    qaScore: document.querySelector("#qa-score"),
    qaList: document.querySelector("#qa-list"),
    cropCandidatesList: document.querySelector("#crop-candidates-list"),
    issuesList: document.querySelector("#issues-list"),
    recipeList: document.querySelector("#recipe-list"),
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

  function addTiming(label, duration) {
    completedTimings.push({ label, duration });
  }

  function getTimings() {
    return [...completedTimings];
  }

  return { els, showSection, showError, hideError, setStep, resetSteps, addTiming, getTimings };
})();
