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
    procThumb: document.querySelector("#proc-thumb"),
    startButton: document.querySelector("#start-btn"),
    startDirectBtn: document.querySelector("#start-direct-btn"),
    resetButton: document.querySelector("#reset-btn"),
    backToUploadBtn: document.querySelector("#back-to-upload-btn"),
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
    runQaRow: document.querySelector("#run-qa-row"),
    runQaToggle: document.querySelector("#run-qa-toggle"),
    imageQualitySelect: document.querySelector("#image-quality"),
    imageQualityWrap: document.querySelector("#image-quality-wrap"),
    imageModelSelect: document.querySelector("#image-model"),
    apImageQualitySelect: document.querySelector("#ap-image-quality"),
    apImageQualityWrap: document.querySelector("#ap-image-quality-wrap"),
    apImageModelSelect: document.querySelector("#ap-image-model"),
    apRunCountWrap: document.querySelector("#ap-run-count-wrap"),
    apRunCountSelect: document.querySelector("#ap-run-count"),
    originalImg: document.querySelector("#original-img"),
    generatedImg: document.querySelector("#generated-img"),
    downloadLink: document.querySelector("#download-link"),
    downloadAllRunsLink: document.querySelector("#download-all-runs-link"),
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
    backToAnalyzedBtn: document.querySelector("#back-to-analyzed-btn"),
    reloadBatchBtn: document.querySelector("#reload-batch-btn"),
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
    batchPoseLockBadge: document.querySelector("#batch-pose-lock-badge"),
    batchAngleLockBadge: document.querySelector("#batch-angle-lock-badge"),
    batchLightingLockBadge: document.querySelector("#batch-lighting-lock-badge"),
    batchSizeLockBadge: document.querySelector("#batch-size-lock-badge"),
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

  const completedTimings = []; // {label, duration (ms)}

  function resetSteps() {
    completedTimings.length = 0;
  }

  function addTiming(label, duration, children) {
    completedTimings.push({ label, duration, children: children || [] });
  }

  function getTimings() {
    return [...completedTimings];
  }

  return { els, showSection, showError, hideError, resetSteps, addTiming, getTimings };
})();
