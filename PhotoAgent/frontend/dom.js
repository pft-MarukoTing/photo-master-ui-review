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
    dzClearAllBtn: document.querySelector("#dz-clear-all-btn"),
    procThumb: document.querySelector("#proc-thumb"),
    startButton: document.querySelector("#start-btn"),
    startDirectBtn: document.querySelector("#start-direct-btn"),
    resetButton: document.querySelector("#reset-btn"),
    logoHomeBtn: document.querySelector("#logo-home-btn"),
    backToUploadBtn: document.querySelector("#back-to-upload-btn"),
    viewResultsWrap: document.querySelector("#view-results-wrap"),
    viewResultsBtn: document.querySelector("#view-results-btn"),
    backToProcessBtn: document.querySelector("#back-to-process-btn"),
    analysisPreview: document.querySelector("#analysis-preview"),
    analysisPreviewHome: document.querySelector("#analysis-preview-home"),
    inlineAnalysisActions: document.querySelector("#inline-analysis-actions"),
    inlineAnalysisContinueBtn: document.querySelector("#inline-analysis-continue-btn"),
    inlineAnalysisCancelBtn: document.querySelector("#inline-analysis-cancel-btn"),
    resultsCanvasRow: document.querySelector("#results-canvas-row"),
    resultsCanvasCol: document.querySelector("#results-canvas-col"),
    resultsCanvasTop: document.querySelector("#results-canvas-top"),
    resultsAnalysisSlot: document.querySelector("#results-analysis-slot"),
    canvasLoading: document.querySelector("#canvas-loading"),
    apSubjectTags: document.querySelector("#ap-subject-tags"),
    apSubject: document.querySelector("#ap-subject"),
    apDiagnosis: document.querySelector("#ap-diagnosis"),
    apComposition: document.querySelector("#ap-composition"),
    apRecipeList: document.querySelector("#ap-recipe-list"),
    apRecipePicker: document.querySelector("#ap-recipe-picker"),
    apRecipeModal: document.querySelector("#ap-recipe-modal"),
    apRecipeModalClose: document.querySelector("#ap-recipe-modal-close"),
    apStylePicker: document.querySelector("#ap-style-picker"),
    apStylePickerValue: document.querySelector("#ap-style-picker-value"),
    apStyleModal: document.querySelector("#ap-style-modal"),
    apStyleModalClose: document.querySelector("#ap-style-modal-close"),
    apStyleTypeRow: document.querySelector("#ap-style-type-row"),
    apStyleOptionsFrame: document.querySelector("#ap-style-options-frame"),
    apStyleOptionsTitle: document.querySelector("#ap-style-options-title"),
    apStyleExitBtn: document.querySelector("#ap-style-exit-btn"),
    apGroupPose: document.querySelector("#ap-group-pose"),
    apGroupAngle: document.querySelector("#ap-group-angle"),
    apGroupLighting: document.querySelector("#ap-group-lighting"),
    apGroupSize: document.querySelector("#ap-group-size"),
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
    imageModelQualitySelect: document.querySelector("#image-model-quality"),
    imageAspectRatioSelect: document.querySelector("#image-aspect-ratio"),
    imageRunCountSelect: document.querySelector("#image-run-count"),
    imageModelQualityPicker: document.querySelector("#image-model-quality-picker"),
    imageAspectRatioPicker: document.querySelector("#image-aspect-ratio-picker"),
    imageRunCountPicker: document.querySelector("#image-run-count-picker"),
    apImageModelQualitySelect: document.querySelector("#ap-image-model-quality"),
    apImageModelQualityPicker: document.querySelector("#ap-image-model-quality-picker"),
    apRunCountWrap: document.querySelector("#ap-run-count-wrap"),
    apRunCountSelect: document.querySelector("#ap-run-count"),
    apRunCountPicker: document.querySelector("#ap-run-count-picker"),
    originalImg: document.querySelector("#original-img"),
    generatedImg: document.querySelector("#generated-img"),
    compareCard: document.querySelector("#compare-card"),
    compareToggleBtn: document.querySelector("#compare-toggle-btn"),
    compareHint: document.querySelector("#compare-hint"),
    compareFullscreenBtn: document.querySelector("#compare-fullscreen-btn"),
    compareFullscreenBackdrop: document.querySelector("#compare-fullscreen-backdrop"),
    recipeTags: document.querySelector("#recipe-tags"),
    downloadLink: document.querySelector("#download-link"),
    downloadAllRunsLink: document.querySelector("#download-all-runs-link"),
    modelName: document.querySelector("#model-name"),
    origDim: document.querySelector("#orig-dim"),
    resultSummary: document.querySelector("#res-summary"),
    resultsBody: document.querySelector("#results-body"),
    batchSidebar: document.querySelector("#batch-sidebar"),
    batchSidebarTitle: document.querySelector("#batch-sidebar-title"),
    batchDownloadAllBtn: document.querySelector("#batch-download-all-btn"),
    batchSidebarList: document.querySelector("#batch-sidebar-list"),
    resultsDetail: document.querySelector("#results-detail"),
    batchMainLoading: document.querySelector("#batch-main-loading"),
    batchMainLoadingThumb: document.querySelector("#batch-main-loading-thumb"),
    batchMainLoadingEyebrow: document.querySelector("#batch-main-loading-eyebrow"),
    batchMainLoadingTitle: document.querySelector("#batch-main-loading-title"),
    batchMainLoadingText: document.querySelector("#batch-main-loading-text"),
    batchReviewContinueBtn: document.querySelector("#batch-review-continue-btn"),
    runTabs: document.querySelector("#run-tabs"),
    runTabsIndex: document.querySelector("#run-tabs-index"),
    runTabsRow: document.querySelector("#run-tabs-row"),
    runTabsRowHome: document.querySelector("#run-tabs-row-home"),
    apOptionsCardInline: document.querySelector("#ap-options-card-inline"),
    apOptionsCardHome: document.querySelector("#ap-options-card-home"),
    runTabsScrollWrap: document.querySelector("#run-tabs-scroll-wrap"),
    runTabsPrev: document.querySelector("#run-tabs-prev"),
    runTabsNext: document.querySelector("#run-tabs-next"),
    runCountSelect: document.querySelector("#run-count"),
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
    errorModal: document.querySelector("#error-modal"),
    errorModalBox: document.querySelector(".error-modal-box"),
    errorModalClose: document.querySelector("#error-modal-close"),
    errorModalFile: document.querySelector("#error-modal-file"),
    errorModalMsg: document.querySelector("#error-modal-msg"),
    noticeModal: document.querySelector("#notice-modal"),
    noticeModalMsg: document.querySelector("#notice-modal-msg"),
    noticeModalOkBtn: document.querySelector("#notice-modal-ok-btn"),
    settingsBtn: document.querySelector("#settings-btn"),
    settingsModal: document.querySelector("#settings-modal"),
    settingsModalClose: document.querySelector("#settings-modal-close"),
    apSettingsBtn: document.querySelector("#ap-settings-btn"),
    apSettingsModal: document.querySelector("#ap-settings-modal"),
    apSettingsModalClose: document.querySelector("#ap-settings-modal-close"),
    downloadProgressToast: document.querySelector("#download-progress-toast"),
    downloadProgressLabel: document.querySelector("#download-progress-label"),
    downloadProgressFill: document.querySelector("#download-progress-fill"),
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
    // #reset-btn lives in the global header, but only makes sense on the
    // results screen -- "上傳照片" doesn't apply while already on the upload
    // screen, and the processing screen has no completed result yet to
    // discard either.
    if (els.resetButton) els.resetButton.hidden = name !== "results";
    // #logo-home-btn resets the whole app (see resetApp, app.js) -- fine as
    // a stray click target on the upload/results screens, but analysis is
    // actively running/in-flight on the processing screen, so clicking it
    // there would silently abandon that work.
    if (els.logoHomeBtn) els.logoHomeBtn.disabled = name === "processing";
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
