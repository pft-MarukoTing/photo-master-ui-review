(() => {
  const { els, showSection, showError, hideError, resetSteps, addTiming, getTimings } = window.PhotoAgentDom;
  const { presets } = window.PhotoAgentConfig;
  const {
    fetchWithRetry,
    submitJob,
    pollJobStatus,
    getJobResult,
    getJobRuns,
    resultImageUrl,
    resultThumbnailUrl,
    sourceImageUrl,
    sourceThumbnailUrl,
    getDemoImages,
    demoImageUrl,
    demoImageThumbnailUrl,
    getUiConfig,
    analyzeJob,
    analyzeJobBatch,
    getAnalyzeResult,
    continueJob,
    rerunJob,
    analyzeAipoc,
    analyzeAipocBatch,
    createAccount,
    getHistory,
    deleteHistoryEntry,
    clearHistory,
    getJobStatusOnce,
    reportJob,
    getReports,
    updateReportStatus,
    deleteReport,
    reportScreenshotUrl
  } = window.PhotoAgentApi;
  const { initFromUrl, getCurrentUser, setCurrentUser, clearCurrentUser, normalizeUsername } = window.PhotoAgentAccount;
  const { renderResult, renderAnalysisPreview, refreshRecipeList, renderBatchStyleOptions, syncStyleTrigger, updateStyleTriggerLabel, clearStylePick, setCompareView, renderTags, toggleAnalysisBoxes, drawAnalysisBoxes, setDebugUi: setResultsDebugUi, previewAspectRatio } = window.PhotoAgentResults;
  renderBatchStyleOptions();

  // Groups every job submitted from ONE "Run" click into the same history
  // entry (backend/services/account.py's action_id) -- needed only where a
  // single click fires N *separate* submitJob/continueJob calls (a
  // runCount>1 revise, two-stage manual path: runSingleItem/editBatchItem),
  // since the backend can't otherwise tell those apart from N unrelated
  // later revises of the same photo (they'd all share the same prior job_id
  // too). Not cryptographic -- just needs to not collide with another id
  // generated in the same session.
  function generateActionId() {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  // Disables whichever arrow has no selectable ("done") RUN left on its
  // side of the current one -- called after every updateRunSwitcher and
  // whenever the current index changes.
  function updateRunSwitcherArrows() {
    if (!els.runSwitchPrev || !els.runSwitchNext || !currentRunList || currentRunIndex == null) return;
    const hasPrevRun = currentRunList.slice(0, currentRunIndex).some((r) => r.status === "done");
    const hasNextRun = currentRunList.slice(currentRunIndex + 1).some((r) => r.status === "done");
    els.runSwitchPrev.disabled = !hasPrevRun;
    els.runSwitchNext.disabled = !hasNextRun;
  }

  // Steps to the next/previous *selectable* (status "done") RUN, skipping
  // over any still-processing/errored ones. Shows #run-switch-loading over
  // the still-visible current RUN (and disables both arrows) for as long as
  // onSelectDone takes -- its target RUN's image can take a moment to fetch,
  // and without this the button press looked like it did nothing until the
  // new image suddenly popped in.
  async function selectAdjacentRun(delta) {
    if (!currentRunList || currentRunOnSelect == null || currentRunIndex == null) return;
    let idx = currentRunIndex + delta;
    while (idx >= 0 && idx < currentRunList.length && currentRunList[idx].status !== "done") idx += delta;
    if (idx < 0 || idx >= currentRunList.length) return;
    if (els.runSwitchLoading) els.runSwitchLoading.hidden = false;
    if (els.runSwitchPrev) els.runSwitchPrev.disabled = true;
    if (els.runSwitchNext) els.runSwitchNext.disabled = true;
    try {
      await currentRunOnSelect(idx);
    } finally {
      if (els.runSwitchLoading) els.runSwitchLoading.hidden = true;
      // Recomputes each arrow's real enabled/disabled state for the RUN we
      // landed on -- may differ from what we forced above, and must still
      // run even if onSelectDone above threw or bailed early.
      updateRunSwitcherArrows();
    }
  }

  if (els.runSwitchPrev) els.runSwitchPrev.addEventListener("click", () => selectAdjacentRun(-1));
  if (els.runSwitchNext) els.runSwitchNext.addEventListener("click", () => selectAdjacentRun(1));

  let selectedFile = null;
  // Set only by viewHistoryRecord (local-path history revisit): selectedFile
  // stays null there (there's no local File object for a job pulled back
  // from the backend), so this is what showSingleResult/finishSingleFlow
  // fall back to for the zip download's source entry instead.
  let selectedFileSourceUrl = null;
  // Bumped by actually navigating to a different record (viewHistoryRecord/
  // viewProcessingHistoryRecord) -- NOT by merely opening the history drawer
  // (openHistoryPage), which just lists records without selecting one. This
  // both lets a pending viewProcessingHistoryRecord poll (which can run for
  // minutes) notice the user has since navigated elsewhere and skip its
  // "done, render it" step, and lets finishSingleFlow's own completion
  // (captured as its own snapshot of this token when it starts) recognize
  // the user has since switched to a different record and skip re-applying
  // ITS result to the now-unrelated screen -- unlike the batch flow's
  // editBatchItem, which guards its own re-render with
  // refreshIfSelected(record), the single-image flow has no per-record
  // identity to check against, just "is the screen still showing what I
  // started with."
  let historyViewToken = 0;
  let previewUrl = "";
  // Small client-side JPEG counterpart of previewUrl, used only for the
  // "AI is working" processing-animation thumbnails (#proc-thumb,
  // #canvas-loading-thumb) -- those animate (blur/pulse) for the whole
  // duration of a job, so repeatedly compositing a multi-megapixel photo
  // every frame is real, avoidable cost compared to a few-hundred-px
  // thumbnail. previewUrl itself stays full-res: #original-img (crop math
  // via naturalWidth/naturalHeight) and the drop-zone preview still need it.
  let previewThumbUrl = "";
  // Mirrors whatever #original-img.src currently is (previewThumbUrl for a
  // fresh upload, or sourceThumbnailUrl(jobId) for a history revisit) so
  // backToProcessFromResults' canvas-loading-thumb doesn't have to guess
  // which of those two produced #original-img's current full-res src.
  let originalImgThumbUrl = "";
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

  // The runs array/current index/select-callback last handed to
  // updateRunSwitcher -- lets the prev/next arrows (defined once, up top,
  // before any runs exist) step to the adjacent RUN via whichever handler is
  // current (batch vs single-image).
  let currentRunList = null;
  let currentRunIndex = null;
  let currentRunOnSelect = null;

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
  let batchRecords = [];   // see makeBatchRecord for record shape
  // Monotonic, never reused even across removals/resets -- batchRecords'
  // array index alone isn't a safe key (records get spliced out, and
  // addFilesToBatch appends more after the initial batch already handed out
  // indices), so each record's localId comes from this counter instead.
  let nextBatchLocalId = 0;
  // Set right before #dz-add-more's click reopens the file picker, checked
  // in routeSelectedFiles -- distinguishes "add more to the existing batch"
  // from a normal fresh selection, which otherwise re-routes based on the
  // new file count alone (a lone added photo would switch to single-image
  // mode and discard the rest of the batch).
  let addingMoreToBatch = false;
  let batchSettings = null;
  // Direct reference to whichever record the sidebar currently has selected --
  // not a status-filtered position (unlike doneBatchRecords()/analyzedBatchRecords()
  // in the old grid+detail-pagination design), since the sidebar shows every
  // record regardless of status and any of them can be clicked at any time.
  let currentBatchRecord = null;
  // Set once the user explicitly clicks a sidebar/drawer item -- gates
  // maybeAutoAdvanceToBatchResult so it only ever jumps the view on its own
  // while the user is still looking at the default first-record placeholder,
  // never overriding a deliberate manual pick.
  let userPickedBatchRecord = false;

  const procVisual = document.querySelector(".proc-visual");
  const procTitle = document.querySelector(".proc-title");
  const procSub = document.querySelector(".proc-sub");
  // Mirrors procSub's text into the results-page canvas overlay
  // (#canvas-loading) too -- finishSingleFlow (below) is shared by the
  // initial upload-flow processing screen and the results-page "revise"
  // regenerate (backToProcessFromResults), but #processing-section stays
  // hidden the whole time during the latter, so procSub's own updates are
  // otherwise invisible to a user watching the results page. Also mirrors
  // into #batch-main-loading-text (showBatchMainLoading) -- batch mode
  // drives the same rotation (see startBatchLoadingRotation below) through
  // this same setter rather than a parallel one, since only one of these
  // three targets is ever actually visible at a time (single-image and
  // batch mode never render simultaneously).
  function setProcSub(text) {
    if (procSub) procSub.textContent = text;
    if (els.canvasLoadingStatus) els.canvasLoadingStatus.textContent = text;
    if (els.batchMainLoadingText) els.batchMainLoadingText.textContent = text;
  }

  // Same mirroring as setProcSub, for the title line -- finishSingleFlow sets
  // procTitle directly (not through this) in the couple of spots that only
  // ever run before #canvas-loading can even be visible (the initial
  // analyze-preview screens), so those are left as plain assignments.
  function setProcTitle(text) {
    if (procTitle) procTitle.textContent = text;
    if (els.canvasLoadingTitle) els.canvasLoadingTitle.textContent = text;
  }

  // Cycles procSub through a list of phrasings for the current stage
  // (analyze/edit) so a long-running RUN doesn't sit on the exact same
  // sentence for minutes and read as frozen. getMessages() is re-invoked on
  // every tick (not captured once) so live info baked into a message --
  // e.g. "已完成 X/N RUN" -- never goes stale mid-rotation. Once the full
  // list has played through once, rotation settles into alternating just
  // the last two entries (the "almost there" phrasings) instead of looping
  // back to message 0 -- a stall lasting long enough to exhaust the whole
  // list should keep sounding like it's near the end, not restart at
  // "AI 正在分析照片" again.
  const PROC_SUB_ROTATE_MS = 5000;
  // How fast the catch-up plays once a stage genuinely finishes before its
  // message list has cycled through once -- see finishProcSubRotation.
  const PROC_SUB_FINISH_MS = 500;
  let procSubRotateTimer = null;
  let procSubRotateIndex = 0;
  let procSubGetMessages = null;
  // Which batch record (see showBatchMainLoading) the rotation currently
  // running, if any, actually belongs to -- null whenever it's either idle
  // or driving the single-image flow instead. There's only one shared
  // rotation timer/index for however many batch records may be mid-
  // analyze/edit at once, so switching the visible record away from this
  // one pauses (not stops) it: showBatchMainLoading saves procSubRotateIndex
  // onto the outgoing record's own procSubIndex first, so switching back to
  // it later resumes from there instead of restarting at message 1. Kept in
  // sync by stopProcSubRotation/finishProcSubRotation below (both null it
  // out alongside their own state, the same instant the rotation itself
  // genuinely stops), so it never points at a rotation that isn't running.
  let activeBatchRotationRecord = null;
  function procSubIndexFor(messages, rotateIndex) {
    const lastTwoStart = Math.max(0, messages.length - 2);
    return rotateIndex < messages.length
      ? rotateIndex
      : lastTwoStart + ((rotateIndex - messages.length) % 2);
  }
  // startIndex resumes a paused batch record's rotation exactly where it
  // left off (see showBatchMainLoading) -- every single-image call site
  // omits it and starts fresh at 0, same as before.
  function startProcSubRotation(getMessages, startIndex = 0) {
    stopProcSubRotation();
    procSubGetMessages = getMessages;
    procSubRotateIndex = startIndex;
    const show = () => {
      const messages = getMessages();
      if (!messages || !messages.length) return;
      setProcSub(messages[procSubIndexFor(messages, procSubRotateIndex)]);
      procSubRotateIndex += 1;
    };
    show();
    procSubRotateTimer = setInterval(show, PROC_SUB_ROTATE_MS);
  }
  function stopProcSubRotation() {
    clearInterval(procSubRotateTimer);
    procSubRotateTimer = null;
    procSubRotateIndex = 0;
    procSubGetMessages = null;
    activeBatchRotationRecord = null;
  }
  // Re-renders whatever message is currently on screen with fresh data
  // (e.g. an updated "已完成 X/N RUN" count) without advancing or resetting
  // the rotation -- lets a real progress event (a RUN finishing) show up
  // immediately in place, instead of restarting the whole phrase sequence
  // back at message 1 the way calling startProcSubRotation again would.
  function refreshProcSubRotation() {
    if (!procSubGetMessages) return;
    const messages = procSubGetMessages();
    if (!messages || !messages.length) return;
    const shownIdx = Math.max(0, procSubRotateIndex - 1);
    setProcSub(messages[procSubIndexFor(messages, shownIdx)]);
  }
  // Called at a stage's genuine completion (not a mid-stall rotation swap):
  // if the message list hasn't played through once yet, plays a quick
  // catch-up before resolving so the screen doesn't switch away mid-list.
  // Capped at the list's last two ("almost done") entries no matter how far
  // behind the rotation actually was -- a job that finishes right after
  // message 1 of 10 shouldn't have to fast-play through 8 more messages
  // just to reach the end; it should land on the same closing phrasing(s) a
  // slow job would have settled into. Resolves immediately if the list
  // already finished its first pass (or nothing was rotating at all).
  function finishProcSubRotation() {
    return new Promise((resolve) => {
      clearInterval(procSubRotateTimer);
      procSubRotateTimer = null;
      const messages = procSubGetMessages ? procSubGetMessages() : null;
      const startIdx = procSubRotateIndex;
      procSubRotateIndex = 0;
      procSubGetMessages = null;
      activeBatchRotationRecord = null;
      if (!messages || !messages.length || startIdx >= messages.length) {
        resolve();
        return;
      }
      const lastTwoStart = Math.max(0, messages.length - 2);
      const remaining = messages.slice(Math.max(startIdx, lastTwoStart));
      let i = 0;
      const step = () => {
        if (i >= remaining.length) { resolve(); return; }
        setProcSub(remaining[i]);
        i += 1;
        setTimeout(step, PROC_SUB_FINISH_MS);
      };
      step();
    });
  }
  function analyzeSubMessages() {
    return [
      "AI 正在分析照片，請稍候…",
      "正在辨識照片中的人物、光線與構圖…",
      "正在讀取照片的曝光與色彩資訊…",
      "正在比對適合的修圖建議…",
      "正在偵測照片中的細節與紋理…",
      "正在計算最佳的裁切與構圖建議…",
      "分析仍在進行中，可能需要多一點時間…",
      "正在整理分析結果…",
      "快好了，請再稍候一下…",
      "即將完成，請稍候…"
    ];
  }
  function editSubMessages({ aiPhotoEditing, imageModel, doneCount, runCount }) {
    const modelLabel = window.PhotoAgentConfig.imageModels[imageModel] || imageModel;
    const progress = runCount > 1 ? `（已完成 ${doneCount}/${runCount} RUN）` : "";
    return aiPhotoEditing
      ? [
          `AI 正在使用 ${modelLabel} 修圖${progress}，請稍候…`,
          `AI 正在調整光影與細節${progress}，請稍候…`,
          `AI 正在細修色調${progress}，請稍候…`,
          `AI 正在校正白平衡與色彩${progress}，請稍候…`,
          `AI 正在優化背景與構圖${progress}，請稍候…`,
          `AI 正在強化畫面細節${progress}，請稍候…`,
          `修圖仍在進行中${progress}，感謝您的耐心等候…`,
          `AI 正在進行最後的品質檢查${progress}…`,
          `快完成了，請再稍候一下${progress}…`,
          `即將完成，請再稍候一下${progress}…`
        ]
      : [
          `正在進行本機影像處理${progress}，請稍候…`,
          `影像處理仍在進行中${progress}，請稍候…`,
          `正在套用影像調整${progress}，請稍候…`,
          `正在進行色彩與亮度校正${progress}，請稍候…`,
          `正在處理照片細節${progress}，請稍候…`,
          `本機處理仍在進行中${progress}，請稍候…`,
          `正在完成最後的影像調整${progress}…`,
          `處理即將完成${progress}，請稍候…`,
          `快完成了，請再稍候一下${progress}…`,
          `即將完成，請再稍候一下${progress}…`
        ];
  }

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

  // 長寬比: Auto and 維持原圖比例 are the choices a regular user needs. The fixed
  // W:H frames are a testbed convenience -- useful for checking how one photo
  // reframes across shapes, not something to put in front of everyone.
  const GENERAL_ASPECT_RATIOS = ["auto", "original"];

  function applyAspectRatioOptionLimit(select, isDebug) {
    if (!select) return;
    Array.from(select.options).forEach((opt) => {
      opt.hidden = !isDebug && !GENERAL_ASPECT_RATIOS.includes(opt.value);
    });
    if (!isDebug && !GENERAL_ASPECT_RATIOS.includes(select.value)) {
      select.value = window.PhotoAgentConfig.defaultAspectRatio || GENERAL_ASPECT_RATIOS[0];
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
    // #boxes-toggle-btn draws the raw analysis geometry over the photo, and
    // the recipe-tags ratio pill reports recommended_aspect_ratio -- both are
    // testbed inspection aids, not something a regular user has any use for.
    // setResultsDebugUi gates the overlay + pill themselves (results.js); this
    // only hides the button that opens the overlay.
    if (els.boxesToggleBtn) els.boxesToggleBtn.hidden = !isDebug;
    setResultsDebugUi(isDebug);
    // Re-running an exported result folder is a testbed workflow; the drop
    // handler is gated on isDebugUiActive too, so this hint and the behavior
    // it advertises appear together.
    if (els.dzFolderHint) els.dzFolderHint.hidden = !isDebug;
    // #results-detail-extra (QA verdict, recipe adherence, technical
    // details, prompts, ...) no longer shows on the analyze/results page at
    // all, debug UI or not -- #show-prompt-btn's modal covers the one part
    // of it (prompts) a testbed session actually needs day to day. It still
    // exists in the DOM purely as the data source renderPrompts (results.js)
    // keeps in sync for that modal to read from, and can still be revealed
    // for a specific record via viewHistoryRecord's revealDetailExtra (admin
    // reports drawer) -- untouched here.
    // #analysis-preview's own remaining content (ap-summary-row's subject
    // tags, the 取消 cancel button, 分析結果 detail toggle above) is
    // testbed-only -- but #ap-options-card-inline (修圖建議/風格/模型/生成
    // 張數/補充指令/重新處理) always gets relocated OUT of #analysis-preview
    // into #results-canvas-top as soon as the panel opens
    // (openInlineAnalysisForCurrentContext still auto-opens for every user),
    // so hiding the now-empty-for-general-users wrapper here doesn't touch
    // it. #show-prompt-btn opens the GPT-prompts modal (openPromptsModal),
    // a testbed-only view, so it stays debug-only by default here -- this is
    // just the fallback for whatever's on screen right now (typically the
    // upload page, nothing to show prompts for yet); showSingleResult/
    // showBatchDoneMain recompute it per-record whenever a result actually
    // renders, since a record opened via viewHistoryRecord's revealDetailExtra
    // (admin reports drawer) must show the button regardless of debug mode.
    if (els.showPromptBtn) els.showPromptBtn.hidden = !isDebug;
    if (els.analysisPreview) els.analysisPreview.style.display = isDebug ? "" : "none";
    // #results-analysis-slot only ever hosts #analysis-preview, which (like
    // #results-detail-extra above) no longer shows on the analyze/results
    // page at all -- keep it hidden unconditionally rather than sitting
    // there as a blank 35%-wide column once .showing-analysis adds its
    // flex-grow, and let #results-canvas-col recenter at a fixed width
    // instead of stretching to fill the whole freed-up row (900px matches
    // .proc-container, the same single-column canvas width used pre-results
    // on the processing screen).
    if (els.resultsAnalysisSlot) els.resultsAnalysisSlot.style.display = "none";
    if (els.resultsCanvasRow) els.resultsCanvasRow.style.justifyContent = "center";
    if (els.resultsCanvasCol) els.resultsCanvasCol.style.maxWidth = "900px";
    applyRunCountOptionLimit(els.imageRunCountSelect, isDebug);
    applyRunCountOptionLimit(els.apRunCountSelect, isDebug);
    applyRunCountOptionLimit(els.runCountSelect, isDebug);
    applyAspectRatioOptionLimit(els.imageAspectRatioSelect, isDebug);
    applyAspectRatioOptionLimit(els.apImageAspectRatioSelect, isDebug);
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

  // Account bootstrap: ?user_id=<ciphertext>, decrypted via a backend round
  // trip (account.js), identifies who's using the tool when linked in from
  // elsewhere. Not shown automatically when absent --
  // applyAccountUi() just shows #register-account-btn instead, and clicking
  // it (see the listener below) is what opens #account-modal on demand.
  (async () => {
    await initFromUrl();
    applyAccountUi();
    checkAdminAccess();
  })();

  // Upload-page option pills (#image-model-quality-picker etc.): a
  // details/summary popover (same visual language as the analysis-preview's
  // .ap-style-item/.ap-style-trigger/.ap-style-panel Pose/Angle/Lighting/Size
  // pickers) instead of the browser's native <option> list, which can't be
  // styled or spaced away from the trigger the way the rest of this app
  // looks. `select` stays in the DOM (just hidden) as the real form control --
  // existing code that reads/writes .value or listens for "change" on it
  // keeps working untouched; this only mirrors its options into a nicer-
  // looking front end. Skips options the select itself has hidden (e.g.
  // applyRunCountOptionLimit trimming the non-debug run-count choices).
  function wireOptionPicker(details, select) {
    if (!details || !select) return;
    const summary = details.querySelector("summary");
    const valueEl = details.querySelector(".ap-style-value");
    const list = details.querySelector(".step-inline-list");
    const panel = details.querySelector(".ap-style-panel");
    if (!valueEl || !list) return;
    const name = `${select.id}-picker`;

    // Flips the panel to open above the trigger when there isn't enough
    // room below it in the viewport (e.g. the upload page's option pills
    // sitting near the bottom of the screen) -- otherwise it renders past
    // window.innerHeight and the user has to scroll the page to see it.
    const repositionPanel = () => {
      if (!panel) return;
      panel.classList.remove("open-upward");
      const anchorRect = details.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const spaceBelow = window.innerHeight - anchorRect.bottom;
      const spaceAbove = anchorRect.top;
      if (spaceBelow < panelRect.height + 16 && spaceAbove > spaceBelow) {
        panel.classList.add("open-upward");
      }
    };

    const rebuild = () => {
      list.innerHTML = "";
      Array.from(select.options).filter((opt) => !opt.hidden).forEach((opt) => {
        const li = document.createElement("li");
        const label = document.createElement("label");
        const cb = document.createElement("span");
        cb.className = "step-cb";
        const input = document.createElement("input");
        input.type = "radio";
        input.name = name;
        input.value = opt.value;
        input.checked = opt.value === select.value;
        const box = document.createElement("span");
        box.className = "step-cb-box";
        cb.appendChild(input);
        cb.appendChild(box);
        const text = document.createElement("span");
        text.className = "step-cb-label";
        text.textContent = opt.textContent;
        label.appendChild(cb);
        label.appendChild(text);
        li.appendChild(label);
        list.appendChild(li);

        input.addEventListener("change", () => {
          select.value = opt.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          valueEl.textContent = opt.textContent;
          details.open = false;
        });
      });
      valueEl.textContent = select.selectedOptions[0]?.textContent || "";
    };

    rebuild();
    // Deciding open-upward from the "toggle" event (fired *after* <details>
    // already shows the panel in its default, downward position) meant the
    // panel visibly flashed open-downward for a frame before jumping
    // upward. Intercepting the summary's own click instead lets us rebuild
    // + measure the panel while it's still forced invisible, decide the
    // direction, and only then actually open <details> -- it's already
    // correctly positioned on the very first paint. Only opening needs this;
    // closing has nothing to reposition, so it's left to the native click.
    if (summary) {
      summary.addEventListener("click", (e) => {
        if (details.open || !panel) return;
        e.preventDefault();
        rebuild();
        panel.style.visibility = "hidden";
        panel.style.display = "block";
        repositionPanel();
        panel.style.visibility = "";
        panel.style.display = "";
        details.open = true;
      });
    }
    // Native toggle still fires for both directions (including this same
    // click-driven open) -- harmless double rebuild/reposition on open, and
    // the only path at all for a same-name sibling picker the browser's own
    // exclusive-<details> grouping closes on our behalf.
    details.addEventListener("toggle", () => {
      if (details.open) rebuild();
      repositionPanel();
    });
    window.addEventListener("resize", () => { if (details.open) repositionPanel(); });
    // Native <details> only closes on picking an option (the "change"
    // listener above sets details.open = false itself) or re-clicking its
    // own summary -- clicking anywhere else on the page left it sitting
    // open. details.contains(e.target) covers both the summary and the
    // panel/list (both live inside <details>), so only a genuine outside
    // click closes it.
    document.addEventListener("click", (e) => {
      if (details.open && !details.contains(e.target)) details.open = false;
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

  // Matched on the option's own model/quality fields rather than a composed
  // "model:quality" key -- not every option uses that form (nano-banana-2 has
  // no quality tier), and a miss here would silently swap the model. Same
  // matching rule as importedRunSettings' inline lookup below.
  function findModelQualityOptionByPair(model, quality) {
    const options = window.PhotoAgentConfig.imageModelQualityOptions || [];
    return options.find((o) => o.model === model && o.quality === quality)
      || options.find((o) => o.model === model)
      || options[0] || null;
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
  wireOptionPicker(els.imageModelQualityPicker, els.imageModelQualitySelect);
  wireOptionPicker(els.apImageModelQualityPicker, els.apImageModelQualitySelect);
  wireOptionPicker(els.apRunCountPicker, els.apRunCountSelect);

  // wireOptionPicker only refreshes a picker's own visible label
  // (.ap-style-value) when THAT picker itself is opened (rebuild runs on
  // its "toggle") -- so picking a model on the upload page kept the
  // underlying <select>s in sync (above) but left #ap-image-model-quality-
  // picker's label showing the old pick on the results page until the user
  // happened to open it there too. Sync both selects and both pickers'
  // visible labels here directly instead of waiting for that. Split out
  // from applyModelQualityChange so a historical record (viewHistoryRecord)
  // can sync the pickers to what it actually used without also pushing that
  // onto singleFlowState/batchSettings, which may not be (or may be the
  // wrong) live session's state at that point.
  function applyModelQualityOption(opt) {
    if (!opt) return;
    if (els.imageModelQualitySelect) els.imageModelQualitySelect.value = opt.value;
    if (els.apImageModelQualitySelect) els.apImageModelQualitySelect.value = opt.value;
    [els.imageModelQualityPicker, els.apImageModelQualityPicker].forEach((picker) => {
      const valueEl = picker?.querySelector(".ap-style-value");
      if (valueEl) valueEl.textContent = opt.label;
    });
  }

  function applyModelQualityChange(value) {
    const opt = findModelQualityOption(value);
    if (!opt) return;
    applyModelQualityOption(opt);
    if (singleFlowState) { singleFlowState.imageModel = opt.model; singleFlowState.imageQuality = opt.quality; }
    // A change made from #ap-image-model-quality while one batch case's
    // revise panel is open belongs to that case alone -- write it onto the
    // record being revised, not the batch-wide `batchSettings` every other
    // case also reads (see makeBatchRecord's imageModel/imageQuality doc).
    // currentDetailBatchRecord() is null pre-submission (upload screen's own
    // #image-model-quality shares this handler), so that path still falls
    // through to batchSettings exactly as before.
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.imageModel = opt.model;
      batchRecord.imageQuality = opt.quality;
    } else if (batchSettings) {
      batchSettings.imageModel = opt.model;
      batchSettings.imageQuality = opt.quality;
    }
  }

  if (els.imageModelQualitySelect) {
    els.imageModelQualitySelect.addEventListener("change", () =>
      applyModelQualityChange(els.imageModelQualitySelect.value));
  }
  if (els.apImageModelQualitySelect) {
    els.apImageModelQualitySelect.addEventListener("change", () =>
      applyModelQualityChange(els.apImageModelQualitySelect.value));
  }

  // 分析模型 selector (Steps 2/3/6's shared model) -- only #analysis-model
  // (upload page) is actually selectable; #ap-analysis-model (analyze-preview
  // page) is shown read-only (`is-locked` in its own HTML, no unlock toggle)
  // since Step 2 has already run by the time that page is reachable and
  // changing the analysis model there would not re-analyze. Still populated/
  // synced like a normal picker (mirrors applyModelQualityOption's own "shown
  // but not actually inert" pattern for read-only history views) so it always
  // displays whichever model actually ran -- it just never drives a change of
  // its own, since nothing wires a "change" listener on it below.
  function findAnalysisModelOption(value) {
    const options = window.PhotoAgentConfig.analysisModelOptions || [];
    return options.find((o) => o.value === value) || options[0] || null;
  }

  function populateAnalysisModelSelect(select) {
    if (!select) return;
    select.innerHTML = "";
    (window.PhotoAgentConfig.analysisModelOptions || []).forEach((opt) => {
      const optionEl = document.createElement("option");
      optionEl.value = opt.value;
      optionEl.textContent = opt.label;
      select.appendChild(optionEl);
    });
    select.value = window.PhotoAgentConfig.defaultAnalysisModel || select.value;
  }
  populateAnalysisModelSelect(els.analysisModelSelect);
  populateAnalysisModelSelect(els.apAnalysisModelSelect);
  wireOptionPicker(els.analysisModelPicker, els.analysisModelSelect);
  wireOptionPicker(els.apAnalysisModelPicker, els.apAnalysisModelSelect);

  function applyAnalysisModelOption(opt) {
    if (!opt) return;
    if (els.analysisModelSelect) els.analysisModelSelect.value = opt.value;
    if (els.apAnalysisModelSelect) els.apAnalysisModelSelect.value = opt.value;
    [els.analysisModelPicker, els.apAnalysisModelPicker].forEach((picker) => {
      const valueEl = picker?.querySelector(".ap-style-value");
      if (valueEl) valueEl.textContent = opt.label;
    });
  }

  function applyAnalysisModelChange(value) {
    const opt = findAnalysisModelOption(value);
    if (!opt) return;
    applyAnalysisModelOption(opt);
    if (singleFlowState) singleFlowState.analysisModel = opt.value;
    if (batchSettings) batchSettings.analysisModel = opt.value;
  }

  // Only #analysis-model (upload page) gets a "change" listener --
  // #ap-analysis-model has no radio inputs a user can actually click
  // (`is-locked` blocks pointer events on its option list), so it never
  // fires one.
  if (els.analysisModelSelect) {
    els.analysisModelSelect.addEventListener("change", () =>
      applyAnalysisModelChange(els.analysisModelSelect.value));
  }

  // Aspect-ratio preference -- "auto" (default) takes the output ratio from
  // the framing the analysis proposed (measured off its crop rectangle, not
  // picked by the analysis model); "original" keeps the source photo's own
  // proportions, fitting that same rectangle into them; a fixed W:H forces
  // that frame and fits the rectangle into it the same way. Offered on the
  // upload screen (#image-aspect-ratio) and, mirrored the same way
  // model/quality is, on the analysis-preview screen (#ap-image-aspect-ratio).
  //
  // Changing it never re-analyzes: the analysis is ratio-neutral (it is not
  // told what the user picked) and the backend settles the ratio per edit in
  // resolve_output_aspect_ratio. That matters because a second analysis of the
  // same photo comes back with different subjects, different protected regions
  // and sometimes the opposite exposure diagnosis -- so re-analyzing to change
  // a ratio silently changed the retouch too.
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
  populateAspectRatioSelect(els.apImageAspectRatioSelect);
  // The options only exist now, after applyDebugUiVisibility already ran, so
  // apply the gate here too rather than waiting for the next debug-mode change.
  applyAspectRatioOptionLimit(els.imageAspectRatioSelect, isDebugUiActive);
  applyAspectRatioOptionLimit(els.apImageAspectRatioSelect, isDebugUiActive);
  wireOptionPicker(els.imageAspectRatioPicker, els.imageAspectRatioSelect);
  wireOptionPicker(els.apImageAspectRatioPicker, els.apImageAspectRatioSelect);
  wireOptionPicker(els.imageRunCountPicker, els.imageRunCountSelect);

  // Same mirroring contract as applyModelQualityChange above: keep both
  // selects and both pickers' visible labels in sync, and push the live value
  // into whichever flow state is active so finishSingleFlow/editBatchItem
  // (which read singleFlowState/batchSettings at call time) pick up a change
  // made on the results screen with no other wiring.
  function findAspectRatioOption(value) {
    const options = window.PhotoAgentConfig.aspectRatioOptions || [];
    return options.find((o) => o.value === value) || options[0] || null;
  }

  // Split out for the same reason as applyModelQualityOption above -- a
  // historical record just needs the pickers to show what it actually used,
  // not a state push or a live re-fit against whatever's on screen.
  function applyAspectRatioOption(opt) {
    if (!opt) return;
    if (els.imageAspectRatioSelect) els.imageAspectRatioSelect.value = opt.value;
    if (els.apImageAspectRatioSelect) els.apImageAspectRatioSelect.value = opt.value;
    [els.imageAspectRatioPicker, els.apImageAspectRatioPicker].forEach((picker) => {
      const valueEl = picker?.querySelector(".ap-style-value");
      if (valueEl) valueEl.textContent = opt.label;
    });
  }

  function applyAspectRatioChange(value) {
    const opt = findAspectRatioOption(value);
    if (!opt) return;
    applyAspectRatioOption(opt);
    if (singleFlowState) singleFlowState.aspectRatio = opt.value;
    // Same per-case routing as applyModelQualityChange above -- a change from
    // #ap-image-aspect-ratio while one batch case's revise panel is open
    // belongs to that case alone, not every other case reading the shared
    // batchSettings.
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.aspectRatio = opt.value;
    } else if (batchSettings) {
      batchSettings.aspectRatio = opt.value;
    }
    // Re-fit what's on screen for the newly picked ratio: the crop that would
    // be sent to the edit model, and the report lines quoting it. The result
    // photo itself can't change without a re-run, so the preview says so.
    previewAspectRatio(opt.value);
  }

  if (els.imageAspectRatioSelect) {
    els.imageAspectRatioSelect.addEventListener("change", () =>
      applyAspectRatioChange(els.imageAspectRatioSelect.value));
  }
  if (els.apImageAspectRatioSelect) {
    els.apImageAspectRatioSelect.addEventListener("change", () =>
      applyAspectRatioChange(els.apImageAspectRatioSelect.value));
  }

  // 生成張數 (single-image RUN count) can now be preset on the upload screen,
  // ahead of analysis -- keep it and the analysis-preview's own #ap-run-count
  // mirrored the same way quality/model already are, so whichever one the
  // user touches (before or after analyzing) carries over to the other.
  function applyRunCountChange(runCount) {
    const value = String(Math.max(1, parseInt(runCount, 10) || 1));
    if (els.imageRunCountSelect) els.imageRunCountSelect.value = value;
    if (els.apRunCountSelect) els.apRunCountSelect.value = value;
    if (els.runCountSelect) els.runCountSelect.value = value;
    // wireOptionPicker only refreshes a picker's own visible label
    // (.ap-style-value) when THAT picker itself is opened (rebuild runs on
    // its "toggle") -- so picking a count on one picker left the other
    // picker's label showing the old value until the user happened to open
    // it too. Sync both pickers' visible labels here directly instead of
    // waiting for that (same fix as applyModelQualityChange above).
    [els.imageRunCountPicker, els.apRunCountPicker].forEach((picker) => {
      const valueEl = picker?.querySelector(".ap-style-value");
      const select = picker?.querySelector("select");
      const label = select?.querySelector(`option[value="${value}"]`)?.textContent;
      if (valueEl && label != null) valueEl.textContent = label;
    });
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

  // Advanced Settings (人像進階設定) toggles live in ONE shared modal
  // (#advanced-settings-modal), opened from either the upload page's
  // #settings-modal or the results page's #ap-settings-modal -- reading them
  // here (instead of inlining the same object literal at every call site)
  // keeps the initial-submit read (startSubmit/startSubmitBatch) and a later
  // revise's re-read (applyAdvancedOptionsChange below) sharing one source
  // of truth.
  function readAdvancedOptionsFromDom() {
    return {
      eye_closed_detection: els.eyeClosedDetectionToggle ? els.eyeClosedDetectionToggle.checked : true,
      gaze_contact_detection: els.eyeGazeFocusToggle ? els.eyeGazeFocusToggle.checked : true,
      hair_mess_detection: els.hairMessDetectionToggle ? els.hairMessDetectionToggle.checked : true,
      calm_hair: els.calmHairToggle ? els.calmHairToggle.checked : false,
    };
  }

  // Mirrors prompts.py's _GAZE_LEAK_KEYWORDS/_BLINK_LEAK_KEYWORDS/
  // _HAIR_LEAK_KEYWORDS + _is_disabled_topic_text exactly (keep both in sync
  // if either changes). The backend force-strips any correction_plan item
  // matching these once the corresponding toggle is off -- both at analyze
  // time (pipeline.py's analyze_stage) and again defensively in
  // build_edit_prompt -- so without the same filter here, 修圖建議 would show
  // (and let the user leave checked) items the backend has already silently
  // dropped, or is about to.
  const GAZE_LEAK_KEYWORDS = ["鏡頭", "眼神", "視線", "目光", "eye contact", "gaze", "camera lens", "looking at the camera", "look at the camera"];
  const BLINK_LEAK_KEYWORDS = ["睜開", "張開眼", "半閉", "閉眼", "眼皮", "reopen", "half-closed", "half closed", "blink"];
  const HAIR_LEAK_KEYWORDS = ["髮", "hair"];

  function isDisabledTopicText(text, advancedOptions) {
    // Historical/imported singleFlowState reconstructions (viewHistoryRecord,
    // importAsSingle) never set .advancedOptions -- undefined here defaults
    // to "everything enabled" (same as the Python side's `analysis.get(...)
    // or {}`), a safe no-op since those records' analysis already had its
    // disabled-topic items stripped once, backend-side, at their own analyze
    // time.
    const options = advancedOptions || {};
    const lowered = String(text || "").toLowerCase();
    const gazeEnabled = options.gaze_contact_detection !== false;
    const blinkEnabled = options.eye_closed_detection !== false;
    const hairEnabled = options.hair_mess_detection !== false;
    if (!gazeEnabled && GAZE_LEAK_KEYWORDS.some((kw) => lowered.includes(kw.toLowerCase()))) return true;
    if (!blinkEnabled && BLINK_LEAK_KEYWORDS.some((kw) => lowered.includes(kw.toLowerCase()))) return true;
    if (!hairEnabled && HAIR_LEAK_KEYWORDS.some((kw) => lowered.includes(kw.toLowerCase()))) return true;
    return false;
  }

  // Client-side mirror of prompts.py's strip_disabled_topic_items: drops any
  // correction_plan item whose text names a topic the current toggles have
  // disabled, and re-indexes retouch_recipe/correction_axes/correction_inverse
  // (aligned 1:1 with correction_plan) to match -- same guard as the Python
  // version, only touching an array if its length still matches plan's own
  // (skips anything already out of sync rather than mis-aligning it further).
  // Called with the SAME advancedOptions both when rendering 修圖建議
  // (renderAnalysisPreview's callers, refreshInlineRecipeList) and when
  // building the submitted analysis (buildEffectiveAnalysis) -- so
  // recipeChecked (read straight off however many checkboxes actually got
  // rendered) always lines up with correction_plan's post-strip length on
  // both ends, with no separate index-remapping needed.
  function stripDisabledTopicItems(analysis, advancedOptions) {
    const plan = (analysis && analysis.correction_plan) || [];
    if (!plan.length) return analysis;
    const keepIndices = [];
    plan.forEach((item, i) => { if (!isDisabledTopicText(item, advancedOptions)) keepIndices.push(i); });
    if (keepIndices.length === plan.length) return analysis;
    const pick = (arr) => (Array.isArray(arr) && arr.length === plan.length) ? keepIndices.map((i) => arr[i]) : arr;
    return {
      ...analysis,
      correction_plan: pick(analysis.correction_plan),
      retouch_recipe: pick(analysis.retouch_recipe),
      correction_axes: pick(analysis.correction_axes),
      correction_inverse: pick(analysis.correction_inverse)
    };
  }

  // Syncs the toggles' displayed checked state to `advancedOptions` without
  // pushing it onto any state -- same split as applyModelQualityOption vs.
  // applyModelQualityChange, so switching which batch record's revise panel
  // is open (startInlineBatchReview) can show that record's own effective
  // settings without also overwriting them.
  function applyAdvancedOptionsOption(advancedOptions) {
    if (!advancedOptions) return;
    if (els.eyeClosedDetectionToggle) els.eyeClosedDetectionToggle.checked = !!advancedOptions.eye_closed_detection;
    if (els.eyeGazeFocusToggle) els.eyeGazeFocusToggle.checked = !!advancedOptions.gaze_contact_detection;
    if (els.hairMessDetectionToggle) els.hairMessDetectionToggle.checked = !!advancedOptions.hair_mess_detection;
    if (els.calmHairToggle) els.calmHairToggle.checked = !!advancedOptions.calm_hair;
  }

  // Lets a revise on the results page change these toggles and have it
  // actually apply to the next edit -- same override rule as
  // applyModelQualityChange: writes onto whichever batch record's revise
  // panel is currently open, else the batch-wide default, else the
  // single-image flow's own state. A no-op before any submit, same as
  // applyModelQualityChange in that window (singleFlowState/batchSettings
  // are both still null).
  function applyAdvancedOptionsChange() {
    const advancedOptions = readAdvancedOptionsFromDom();
    if (singleFlowState) singleFlowState.advancedOptions = advancedOptions;
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.advancedOptions = advancedOptions;
    } else if (batchSettings) {
      batchSettings.advancedOptions = advancedOptions;
    }
    refreshInlineRecipeList();
  }

  // Re-filters whichever analysis the (currently open) inline revise panel
  // is showing against the JUST-changed toggles, and re-fills #ap-recipe-list
  // from that -- so turning e.g. 亂髮偵測 off immediately drops any hair-mess
  // items from 修圖建議 instead of leaving them checked and showing as if
  // they'll still be applied, when build_edit_prompt is actually about to
  // strip them server-side regardless (see stripDisabledTopicItems's own
  // doc). A no-op if the panel isn't open or there's no analysis yet -- same
  // "harmless before/without a live session" shape as applyAdvancedOptionsChange
  // itself. Same "always resets to checked" behavior as every other
  // renderAnalysisPreview/refreshRecipeList call in the app -- any recipe
  // items the user had unchecked by hand are re-checked along with this, same
  // trade-off as switching batch records or revisiting history already makes.
  function refreshInlineRecipeList() {
    if (!els.analysisPreview || els.analysisPreview.hidden) return;
    const batchRecord = currentDetailBatchRecord();
    const analysis = batchRecord ? batchRecord.analysis : (singleFlowState && singleFlowState.analysis);
    if (!analysis) return;
    refreshRecipeList(stripDisabledTopicItems(analysis, readAdvancedOptionsFromDom()));
  }

  [els.eyeClosedDetectionToggle, els.eyeGazeFocusToggle, els.hairMessDetectionToggle, els.calmHairToggle].forEach((toggle) => {
    if (toggle) toggle.addEventListener("change", applyAdvancedOptionsChange);
  });

  // Backend step timings are seconds (float) or null (step skipped);
  // addTiming/appendTimingRow expect ms or a non-number sentinel.
  function secToMs(seconds) {
    return typeof seconds === "number" ? seconds * 1000 : null;
  }

  // Loads `src` directly into `imgEl` while it's invisible, only revealing
  // it once the image has actually finished (successfully or not) -- rather
  // than fetching into a throwaway Image() first and hoping the browser
  // reuses that response for the real element once its own src is set to
  // the same URL. That reuse only happens if the server actually lets the
  // response be cached; for a non-cacheable result/history endpoint it
  // would silently refetch, showing the browser's built-in broken-image
  // icon plus alt text for however long that takes -- and since
  // .photo-frame shrink-wraps to whatever the image's current rendered size
  // is, that gap collapses the whole frame down to a narrow sliver too.
  // Loading straight into the real (hidden) element sidesteps caching
  // entirely: there's only ever one fetch, and nothing is visible until it
  // resolves either way. Resolves (never rejects) even on error, since the
  // caller still has to proceed and show whatever it's got.
  async function setImgSrcPreloaded(imgEl, src) {
    if (imgEl.src === src && imgEl.complete) return;
    imgEl.style.visibility = "hidden";
    await new Promise((resolve) => {
      imgEl.onload = () => { imgEl.onload = null; imgEl.onerror = null; resolve(); };
      imgEl.onerror = () => { imgEl.onload = null; imgEl.onerror = null; resolve(); };
      imgEl.src = src;
    });
    imgEl.style.visibility = "";
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
    if (!timingList || !timingDetails) return;
    // Historical playback (viewHistoryRecord) has no locally-recorded timings
    // for a job that may have run in a different session entirely -- hide the
    // panel instead of leaving whatever an earlier live render populated it
    // with (the live flow always calls this with a non-empty list, so this
    // branch is otherwise never hit in practice).
    if (!timings.length) { timingDetails.hidden = true; return; }
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
        // #ap-recipe-picker just opens a read-only view of the recipe list
        // (#ap-recipe-modal) -- unlike the other controls here, it doesn't
        // change anything about the pending/in-flight request, so it should
        // stay clickable even while the rest of the panel is disabled.
        if (el === els.apRecipePicker) return;
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

  // #ap-options-card-inline (模型/生成張數 pills) moves below .compare-card
  // (inside #results-canvas-top, the wrapper around just the photo -- see
  // its own comment in index.html) while the panel is open, flattening into
  // the same plain horizontal pill row the upload page uses -- .ap-col-right
  // (its normal home, still used as-is on the processing screen, which has
  // no .compare-card to move below) squeezes it into a narrow column instead.
  function moveOptionsCardTo(container) {
    if (!els.apOptionsCardInline || !container) return;
    if (els.apOptionsCardInline.parentElement !== container) {
      container.appendChild(els.apOptionsCardInline);
    }
  }

  function restoreOptionsCardHome() {
    if (els.apOptionsCardHome && els.apOptionsCardInline.previousElementSibling !== els.apOptionsCardHome) {
      els.apOptionsCardHome.after(els.apOptionsCardInline);
    }
  }

  // readOnly (used for historical playback, viewHistoryRecord) shows the same
  // panel purely as information -- no revise/re-run controls, since that
  // doesn't apply to a past job. Must disable (or re-enable) BEFORE
  // moveOptionsCardTo, same reasoning as the comment below: once
  // #ap-options-card-inline is moved out of #analysis-preview,
  // setAnalysisPreviewDisabled's els.analysisPreview.querySelectorAll(...)
  // can no longer find its controls to toggle.
  function openInlineAnalysisPanel(readOnly = false) {
    moveAnalysisPreviewTo(els.resultsAnalysisSlot);
    // Before moveOptionsCardTo below -- setAnalysisPreviewDisabled re-enables
    // via els.analysisPreview.querySelectorAll(...), which only finds
    // #ap-options-card-inline's controls (incl. #inline-analysis-continue-btn)
    // while that card is still nested inside #analysis-preview. Called after
    // the move, it silently found nothing there anymore and those controls
    // stayed disabled forever after the first disable/regenerate cycle.
    setAnalysisPreviewDisabled(readOnly);
    moveOptionsCardTo(els.resultsCanvasTop);
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = readOnly;
    // Moved out of #inline-analysis-actions (now lives at the end of
    // #ap-options-card-inline) -- toggled alongside it here since it lost
    // that shared parent's hidden gating.
    if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = readOnly;
    els.analysisPreview.hidden = false;
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.add("showing-analysis");
    // Mirrored onto #results-body too -- #batch-sidebar's width split (see
    // styles.css) needs this state and isn't a descendant of #results-canvas-row.
    if (els.resultsBody) els.resultsBody.classList.add("showing-analysis");
    updateAnalysisPreviewMaxHeight();
  }

  function closeInlineAnalysisPanel() {
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.remove("showing-analysis");
    if (els.resultsBody) els.resultsBody.classList.remove("showing-analysis");
    els.analysisPreview.hidden = true;
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = true;
    if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = true;
    restoreOptionsCardHome();
  }

  let inlineAnalysisContinueHandler = null;
  let inlineAnalysisCancelHandler = null;
  // Recipe/style/prompt state as of the moment the revise panel opened --
  // captured via captureInlineReviseSnapshot() right before
  // waitForInlineAnalysisAction() starts watching for a click, since both
  // backToProcessFromResults and startInlineBatchReview can open the panel
  // with a prior round's non-default picks already restored (applyStyleSelections/
  // applyBatchReviewSelections), not just fresh defaults.
  let inlineReviseSnapshot = null;

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

  // Snapshots the panel's current recipe/style/prompt state so a later
  // inlineReviseHasNoChanges() call can tell "changed since the panel opened"
  // apart from "left at default" -- must run after renderAnalysisPreview/
  // applyStyleSelections/applyBatchReviewSelections have already populated the
  // DOM for this round. Reads via readStyleSelectionsFromDom, defined below
  // but hoisted. Also covers #ap-settings-modal's own two pickers (模型 and
  // 生成張數) -- those aren't part of readStyleSelectionsFromDom since they're
  // not "style" picks, but changing either is just as much a real edit as the
  // recipe/style/prompt fields.
  function captureInlineReviseSnapshot() {
    const { options, recipeChecked } = readStyleSelectionsFromDom();
    inlineReviseSnapshot = {
      options: { ...options },
      recipeChecked: Array.isArray(recipeChecked) ? [...recipeChecked] : recipeChecked,
      prompt: els.promptInput ? els.promptInput.value.trim() : "",
      overwritePrompt: els.promptOverwriteToggle ? els.promptOverwriteToggle.checked : false,
      modelQuality: els.apImageModelQualitySelect ? els.apImageModelQualitySelect.value : null,
      runCount: els.apRunCountSelect ? els.apRunCountSelect.value : null,
      // 人像進階設定 (#advanced-settings-modal, reopenable from #ap-settings-btn
      // now too) -- just as real an edit as the picks above, so it needs the
      // same before/after snapshot to unblock the Run button.
      advancedOptions: readAdvancedOptionsFromDom()
    };
  }

  // True if the inline revise panel's Run button would submit an unchanged
  // re-run: recipe checkboxes, Pose/Angle/Lighting/Size picks, 補充指令 text,
  // and the 模型/生成張數 settings all still match inlineReviseSnapshot (the
  // state captured when the panel opened) -- NOT simply "everything at
  // default", since a second revise round (or a history revisit) can open
  // with a prior round's non-default picks already restored. Falls back to
  // allowing the run (no block) if no snapshot was captured, rather than
  // blocking blind.
  function inlineReviseHasNoChanges() {
    if (!inlineReviseSnapshot) return false;
    const { options, recipeChecked } = readStyleSelectionsFromDom();
    const prompt = els.promptInput ? els.promptInput.value.trim() : "";
    const modelQuality = els.apImageModelQualitySelect ? els.apImageModelQualitySelect.value : null;
    const runCount = els.apRunCountSelect ? els.apRunCountSelect.value : null;
    const sameOptions = Object.keys(options).every((key) => options[key] === inlineReviseSnapshot.options[key]);
    const sameRecipe = Array.isArray(recipeChecked) && Array.isArray(inlineReviseSnapshot.recipeChecked)
      ? recipeChecked.length === inlineReviseSnapshot.recipeChecked.length
        && recipeChecked.every((checked, i) => checked === inlineReviseSnapshot.recipeChecked[i])
      : recipeChecked === inlineReviseSnapshot.recipeChecked;
    const samePrompt = prompt === inlineReviseSnapshot.prompt;
    const sameModelQuality = modelQuality === inlineReviseSnapshot.modelQuality;
    const sameRunCount = runCount === inlineReviseSnapshot.runCount;
    const advancedOptions = readAdvancedOptionsFromDom();
    const sameAdvancedOptions = Object.keys(advancedOptions).every(
      (key) => advancedOptions[key] === inlineReviseSnapshot.advancedOptions[key]);
    return sameOptions && sameRecipe && samePrompt && sameModelQuality && sameRunCount && sameAdvancedOptions;
  }

  // Resolves "continue" or "cancel" depending on which inline button the user
  // clicks -- same one-shot-listener shape as waitForContinue, just with two
  // possible outcomes instead of one.
  function waitForInlineAnalysisAction() {
    return new Promise((resolve) => {
      clearInlineAnalysisWait();
      inlineAnalysisContinueHandler = () => {
        // Nothing to actually change vs. the existing result -- rather than
        // silently spend another edit call reproducing it, ask the user to
        // either uncheck/pick something or leave via Cancel instead. Doesn't
        // clear the listener/resolve, so the panel just stays open.
        if (inlineReviseHasNoChanges()) {
          showNoticeModal("請至少修改一項修圖建議、風格選項、補充指令或設定後再執行。");
          return;
        }
        clearInlineAnalysisWait();
        resolve("continue");
      };
      inlineAnalysisCancelHandler = () => { clearInlineAnalysisWait(); resolve("cancel"); };
      els.inlineAnalysisContinueBtn.addEventListener("click", inlineAnalysisContinueHandler);
      els.inlineAnalysisCancelBtn.addEventListener("click", inlineAnalysisCancelHandler);
    });
  }

  async function showAnalysisPreview(analysis, preset) {
    restoreAnalysisPreviewHome();
    // singleFlowState is already set (with this same `analysis`) by the time
    // startSubmit calls this -- stripping disabled-topic items here keeps
    // 修圖建議 matching what the backend already stripped from correction_plan
    // (analyze_stage ran strip_disabled_topic_items with these exact toggles).
    renderAnalysisPreview({ analysis: stripDisabledTopicItems(analysis, singleFlowState.advancedOptions), preset });
    if (els.analysisPreview) els.analysisPreview.hidden = false;
    if (els.backToUploadBtn) els.backToUploadBtn.hidden = false;
    // RUN count only applies to the single-image flow -- batch mode never
    // shows this panel for its own "analyzed" state at all anymore (only for
    // startInlineBatchReview's per-image revise, which sets this itself).
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    setAnalysisPreviewDisabled(false);
    updateAnalysisPreviewMaxHeight();
    // Lets any not-yet-shown analyze-stage phrasings finish playing (fast-
    // forwarded) before flipping the processing card to its "done" state.
    await finishProcSubRotation();
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

  // html2canvas (vendored, MIT) -- ~195KB, loaded on demand the first time
  // the report sheet actually opens rather than paying that cost on every
  // page load, same lazy-load reasoning as loadLibheifFactory above.
  let html2canvasLoadPromise = null;
  function loadHtml2Canvas() {
    if (window.html2canvas) return Promise.resolve(window.html2canvas);
    if (!html2canvasLoadPromise) {
      html2canvasLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "html2canvas.min.js";
        script.onload = () => resolve(window.html2canvas);
        script.onerror = () => {
          html2canvasLoadPromise = null; // allow retrying next time the sheet opens
          reject(new Error("截圖元件載入失敗"));
        };
        document.head.appendChild(script);
      });
    }
    return html2canvasLoadPromise;
  }

  // ---------- Screenshot encoding helpers (insert near loadHtml2Canvas) ----------
  const MAX_SCREENSHOT_BYTES = 7_500_000; // keep a margin under the backend 8 MB limit

  function canvasToBlobAsync(canvas, type = "image/jpeg", quality = 0.85) {
    return new Promise((resolve) => {
      if (canvas.toBlob) {
        canvas.toBlob((b) => resolve(b), type, quality);
      } else {
        // Fallback for environments without toBlob
        const dataUrl = canvas.toDataURL(type, quality);
        const parts = dataUrl.split(",");
        const m = parts[0].match(/:(.*?);/);
        const mime = m ? m[1] : type;
        const binary = atob(parts[1]);
        const len = binary.length;
        const ub = new Uint8Array(len);
        for (let i = 0; i < len; i++) ub[i] = binary.charCodeAt(i);
        resolve(new Blob([ub], { type: mime }));
      }
    });
  }

  async function encodeUnderCap(canvas, maxBytes) {
    // Try quality-only passes first
    let lastBlob = null;
    for (const quality of [0.85, 0.75, 0.6]) {
      const blob = await canvasToBlobAsync(canvas, "image/jpeg", quality);
      if (!blob) continue;
      lastBlob = blob;
      if (blob.size <= maxBytes) return blob;
    }

    // If still too big, downscale and retry
    for (const factor of [0.75, 0.5]) {
      const small = document.createElement("canvas");
      small.width = Math.max(1, Math.round(canvas.width * factor));
      small.height = Math.max(1, Math.round(canvas.height * factor));
      const ctx = small.getContext("2d");
      if (!ctx) break;
      ctx.drawImage(canvas, 0, 0, small.width, small.height);
      const blob = await canvasToBlobAsync(small, "image/jpeg", 0.7);
      if (!blob) continue;
      lastBlob = blob;
      if (blob.size <= maxBytes) return blob;
    }

    // Return best attempt even if over cap
    return lastBlob;
  }

  async function captureReportScreenshot(jobId, path, runNumber) {
    try {
      const html2canvas = await loadHtml2Canvas();
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = document.documentElement.clientHeight;

      // Conservative DPR cap on iOS to avoid OOM/timeouts on mobile Safari.
      const isiOS = /iP(hone|od|ad)/.test(navigator.platform) || /iPhone|iPad|iPod/.test(navigator.userAgent);
      const maxScale = isiOS ? 1.5 : 2;
      const scale = Math.min(maxScale, Math.max(1, window.devicePixelRatio || 1));

      // x/y/width/height crop the render down to just the current viewport
      // (in document coordinates -- window.scrollX/scrollY is exactly
      // "where the viewport currently starts" in that space) instead of
      // rendering document.body's entire scrollable height and cropping
      // afterward: that full-page render was the actual cause of the
      // capture silently failing on iPhone (Safari's canvas size/memory
      // ceiling is far tighter than desktop browsers', easily exceeded by a
      // whole scrollable page at 1.5-2x scale). html2canvas's own default
      // scrollX/scrollY is already the real window.scrollX/scrollY read
      // fresh at call time, so no separate scroll override is needed here.
      // windowWidth/windowHeight are still pinned so responsive @media
      // rules (e.g. this app's own icon-only header collapse below 560px)
      // match the real page. Deliberately not allowTaint: true -- that
      // would let any stray cross-origin image without CORS headers taint
      // the whole canvas, and a tainted canvas.toBlob() below resolves null
      // silently (no thrown error, so the catch below never fires) instead
      // of the single #generated-img swap this onclone already handles.
      const canvas = await Promise.race([
        html2canvas(document.body, {
          backgroundColor: "#09090b",
          useCORS: true,
          scale,
          windowWidth: viewportWidth,
          windowHeight: viewportHeight,
          x: window.scrollX,
          y: window.scrollY,
          width: viewportWidth,
          height: viewportHeight,
          logging: false,
          // openReportModal kicks this capture off before revealing
          // #report-modal, but html2canvas's own DOM read happens somewhere
          // inside its (possibly slow, first-load-fetches-the-library) async
          // chain -- there's no guarantee that read still happens before
          // #report-modal.hidden flips to false. Excluding it (and its own
          // #report-screenshot-modal preview popup) outright removes the
          // race entirely: this is a capture of what the reporter was
          // looking at *before* clicking 回報, never the report sheet itself.
          ignoreElements: (el) => el.id === "report-modal" || el.id === "report-screenshot-modal",
          // #generated-img's real src is always a presigned S3 URL with no
          // CORS headers (see resultImageUrl's own comment) -- reading its
          // pixels back via canvas.toBlob below would taint the whole canvas
          // and throw/resolve-null, silently killing the entire capture, not
          // just that one image. html2canvas clones the target subtree
          // before rendering it (the real on-screen <img> is never touched),
          // so this only has to redirect the clone's copy to this app's own
          // same-origin image proxy -- the exact same one buildImageFiles/
          // the zip download already rely on to sidestep this same CORS gap.
          onclone: (clonedDoc) => {
            // Every element using background-clip:text for a gradient fill
            // (.logo em's "Master" half, .upload-headline,
            // .notice-modal-title em) -- html2canvas can't rasterize that
            // clip, so it was rendering the full gradient rectangle behind
            // the text instead of the text itself. Flattened to plain,
            // visible text -- purely cosmetic to this clone.
            clonedDoc.querySelectorAll(".logo em, .upload-headline, .notice-modal-title em").forEach((el) => {
              el.style.background = "none";
              el.style.webkitBackgroundClip = "unset";
              el.style.backgroundClip = "unset";
              el.style.webkitTextFillColor = "unset";
              el.style.color = "var(--accent)";
            });
            // Demo-picker thumbnails (renderDemoImagesGrid) set img.loading
            // = "lazy" for real on-page perf. That's fine live, but
            // WebKit/iOS Safari never starts a lazy <img>'s load at all
            // while its ancestor is display:none (e.g. #demo-images-modal
            // closed) -- html2canvas has to wait for every <img> in the
            // clone to finish loading before it can rasterize anything, so
            // that image never resolves and never errors, hanging the whole
            // capture forever. Forcing eager here (only on this throwaway
            // clone) makes the browser fetch them immediately regardless of
            // visibility.
            clonedDoc.querySelectorAll("img[loading='lazy']").forEach((img) => {
              img.loading = "eager";
            });
            const clonedImg = clonedDoc.getElementById("generated-img");
            if (!clonedImg || !jobId) return undefined;
            return new Promise((resolve) => {
              clonedImg.onload = resolve;
              clonedImg.onerror = resolve;
              clonedImg.src = resultImageUrl(jobId, { path, run: runNumber });
            });
          }
        }),
        // Past ~8s something is stuck, not just slow -- reportModal's
        // existing "無法擷取畫面預覽" fallback is a fine outcome; an
        // indefinitely hung 回報 modal is not.
        new Promise((_, reject) => setTimeout(() => reject(new Error("擷取畫面逾時")), 8000))
      ]);

      // Encode JPEG under size cap (quality-first then downscale).
      const jpegBlob = await encodeUnderCap(canvas, MAX_SCREENSHOT_BYTES);
      if (jpegBlob) return jpegBlob;
      // If encoding produced nothing (very unlikely), fall back to PNG.
      return await new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/png"));
    } catch (err) {
      console.error("回報截圖擷取失敗", err);
      return null;
    }
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

  // Small JPEG thumbnail for the processing-animation elements (see
  // previewThumbUrl's own doc above) -- blob is already browser-renderable
  // at this point (previewableBlob converted HEIC above), so createImageBitmap
  // decodes it directly, no libheif involvement needed a second time. Returns
  // null (never throws) on any failure -- callers fall back to the full-res
  // preview, same as previewableBlob's own HEIC-conversion failure fallback.
  const PROC_THUMB_MAX_SIDE = 320;

  async function createProcThumbUrl(blob) {
    try {
      const bitmap = await createImageBitmap(blob);
      const scale = Math.min(1, PROC_THUMB_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));

      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
      bitmap.close?.();

      const thumbBlob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.82));
      return thumbBlob ? URL.createObjectURL(thumbBlob) : null;
    } catch {
      return null;
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
    // The skip-review "Run" path (useCombinedJob, startSubmit) calls
    // finishSingleFlow() straight away, with no renderAnalysisPreview for
    // this photo in between -- readStyleSelectionsFromDom() there would
    // otherwise read whatever recipe checkboxes/style radios the *previous*
    // photo's review left sitting in the DOM and stash them into
    // lastSingleSelections, which backToProcessFromResults then reapplies
    // over this photo's own (freshly rendered, correctly all-checked)
    // checklist once its results are ready. Clearing them now, before any of
    // that runs, means a stale pre-render read finds nothing instead.
    if (els.apRecipeList) els.apRecipeList.innerHTML = "";
    [els.apPoseList, els.apAngleList, els.apLightingList, els.apSizeList].forEach((list) => {
      if (list) list.innerHTML = "";
    });

    // Switch to the preview shell right away, with its loading hover up --
    // a HEIC photo's decode (previewableBlob's libheif WASM call, below) can
    // take a moment, and #dz-preview-img has no src at all until it's done,
    // so leaving the empty drop zone on screen for that whole gap looks like
    // the selection did nothing.
    els.dropZone.hidden = true;
    els.dzPreview.hidden = false;
    if (els.dzPreviewLoading) els.dzPreviewLoading.hidden = false;

    const blobForPreview = await previewableBlob(file);
    if (token !== fileSelectionToken) return; // superseded by a newer selection while converting

    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blobForPreview);
    if (previewThumbUrl) URL.revokeObjectURL(previewThumbUrl);
    previewThumbUrl = await createProcThumbUrl(blobForPreview);
    if (token !== fileSelectionToken) {
      // Superseded by a newer selection while the thumbnail was generating --
      // that newer call already owns previewUrl/previewThumbUrl now, so only
      // clean up this now-orphaned thumbnail blob, nothing else.
      if (previewThumbUrl) URL.revokeObjectURL(previewThumbUrl);
      return;
    }
    originalImgThumbUrl = previewThumbUrl;

    els.dzPreviewImg.src = previewUrl;
    els.procThumb.src = previewThumbUrl || previewUrl;
    els.originalImg.src = previewUrl;
    if (els.dzPreviewLoading) els.dzPreviewLoading.hidden = true;

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

  function buildDownloadImageFilename() {
    const pad = (n) => String(n).padStart(2, "0");
    const now = new Date();
    const dateStr = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
    const timeStr = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    return `photoMuse-${dateStr}-${timeStr}.png`;
  }

  const PROMPT_KEYS = ["photo_analysis", "user_instruction_review", "crop_validation", "image_edit", "gaze_repair_pass", "quality_check"];

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

  function buildTokenUsageJson(tokenUsage) {
    return JSON.stringify(tokenUsage || {}, null, 2);
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
  // onItemDone (if given) fires once per completed RUN fetch -- a plain
  // "one more thing finished" tick, not told how many total, so callers
  // decide what "done" means at whatever scope they're aggregating at
  // (a single image's RUNs, or a whole batch's).
  async function fetchImageDownloadData({ file, sourceUrl, path }, runs, onItemDone) {
    // file (a local File object) is only ever absent for historical playback
    // (viewHistoryRecord) -- sourceUrl (our own /source/<job_id>/image, local
    // path only) is its fallback there, and if even that's unavailable
    // (AIPoC-path history) sourceBuf stays null and the zip just omits the
    // source photo entirely (see buildFlatImageFiles/buildNestedImageFiles).
    // fetchWithRetry (not a bare fetch) -- a burst of many of these firing
    // at once (multi-select download especially) can trip a transient
    // net::ERR_HTTP2_PROTOCOL_ERROR against the CDN/backend; retrying once
    // or twice clears it instead of failing the whole zip over one blip.
    const sourceBuf = file
      ? await file.arrayBuffer()
      : sourceUrl
        ? await fetchWithRetry(sourceUrl).then((response) => response.arrayBuffer())
        : null;
    const runData = await Promise.all(runs.map(async (run) => {
      // Fetch the image via our own backend, not the presigned S3/AIPoC URL --
      // that URL has no CORS headers, so a browser-side fetch() of it is
      // blocked even though the same URL displays fine in an <img> tag.
      const imageBuf = await fetchWithRetry(resultImageUrl(run.jobId, { path, run: run.run })).then((response) => response.arrayBuffer());
      if (onItemDone) onItemDone();
      return { run, imageBuf };
    }));
    return { sourceBuf, runData };
  }

  // Normal mode: source + one result_N.png per RUN, flat under `prefix` --
  // no per-RUN subfolder, no JSON. Everyday users just want the pictures.
  function buildFlatImageFiles({ file, prefix }, sourceBuf, runData) {
    const files = sourceBuf ? [{ name: prefix + (file?.name || "source.jpg"), data: new Uint8Array(sourceBuf) }] : [];
    runData.forEach(({ run, imageBuf }) => {
      files.push({ name: prefix + resultFileNames(run.run || 1).image, data: new Uint8Array(imageBuf) });
    });
    return files;
  }

  // --debug-ui mode: source at `prefix`, each RUN under its own `run_N/`
  // subfolder alongside the raw result.json/prompts.json/token-usage.json --
  // the original nested layout, kept for the internal analysis/QA data this
  // mode exists to expose.
  function buildNestedImageFiles({ file, prefix }, sourceBuf, runData) {
    const files = sourceBuf ? [{ name: prefix + (file?.name || "source.jpg"), data: new Uint8Array(sourceBuf) }] : [];
    runData.forEach(({ run, imageBuf }) => {
      const runPrefix = `${prefix}run_${run.run || 1}/`;
      // token_usage gets its own file (token-usage.json) instead of sitting
      // in result.json, so pull it out of the copy written there.
      const { token_usage: tokenUsage, ...resultForFile } = run.result;
      const jsonBytes = new TextEncoder().encode(JSON.stringify(resultForFile, null, 2));
      const promptsBytes = new TextEncoder().encode(buildPromptsJson(run.result.analysis?.prompts || {}));
      const tokenUsageBytes = new TextEncoder().encode(buildTokenUsageJson(tokenUsage));
      files.push(
        { name: runPrefix + "result.png", data: new Uint8Array(imageBuf) },
        { name: runPrefix + "result.json", data: jsonBytes },
        { name: runPrefix + "prompts.json", data: promptsBytes },
        { name: runPrefix + "token-usage.json", data: tokenUsageBytes }
      );
    });
    return files;
  }

  // Builds one image's full set of download entries -- flat (images only)
  // in normal use, nested with the raw result/prompt JSON in --debug-ui mode.
  async function buildImageFiles({ file, sourceUrl, path, prefix = "" }, runs, onItemDone) {
    const { sourceBuf, runData } = await fetchImageDownloadData({ file, sourceUrl, path }, runs, onItemDone);
    return isDebugUiActive
      ? buildNestedImageFiles({ file, prefix }, sourceBuf, runData)
      : buildFlatImageFiles({ file, prefix }, sourceBuf, runData);
  }

  function lastResultRuns() {
    if (Array.isArray(lastResult.runs) && lastResult.runs.length) return lastResult.runs;
    return [{ run: 1, result: lastResult.resultText, jobId: lastResult.jobId, status: "done" }];
  }

  async function buildResultZip(onProgress) {
    const runs = lastResultRuns();
    // A combined multi-RUN job (job_processor.py's run_count loop) shares ONE
    // jobId across every RUN of that image, so jobId alone no longer
    // identifies "the currently displayed RUN" -- match on the RUN number
    // lastResult was set with instead (see showSingleResult/syncRecordMirror).
    const current = runs.find((run) => run.run === lastResult.runNumber) || runs[0];
    let done = 0;
    const files = await buildImageFiles({ file: lastResult.file, sourceUrl: lastResult.sourceUrl, path: lastResult.path }, [current], () => {
      done += 1;
      if (onProgress) onProgress(done, 1);
    });
    return window.PhotoAgentZip.createZip(files);
  }

  async function buildAllRunsZip(onProgress) {
    const doneRuns = lastResultRuns().filter((run) => run.status === "done");
    let done = 0;
    const files = await buildImageFiles({ file: lastResult.file, sourceUrl: lastResult.sourceUrl, path: lastResult.path }, doneRuns, () => {
      done += 1;
      if (onProgress) onProgress(done, doneRuns.length);
    });
    return window.PhotoAgentZip.createZip(files);
  }

  // How many RUNs of the currently-displayed result have actually finished
  // -- handleDownloadClick uses this to decide whether clicking #download-link
  // should just download the current image directly (<=1 done RUN) or first
  // ask the user to choose between that and all RUNs (see toggleDownloadMenu).
  let lastDoneRunsCount = 0;

  function updateDownloadLinks(runs) {
    // download-link only makes sense once a real edited result exists to zip
    // up -- lastResult is set right before every call to this function (and
    // cleared in reset()), so its presence is the single source of truth.
    els.downloadLink.hidden = !lastResult;
    lastDoneRunsCount = (runs || []).filter((run) => run.status === "done").length;
    closeDownloadMenu();
    const hint = lastDoneRunsCount > 1 ? `下載選項（${lastDoneRunsCount} 個 RUN）` : "下載結果";
    els.downloadLink.title = hint;
    els.downloadLink.setAttribute("aria-label", hint);
  }

  // ── Floating, draggable #report-btn ──────────────────────────────────────
  // #report-btn (index.html) is a free-floating position:fixed button --
  // CSS gives it a top-right default (just below .site-header), and the
  // user can drag it anywhere else for the rest of this page view so it's
  // not permanently blocking whatever's underneath it. Deliberately NOT
  // persisted anywhere (no localStorage) -- a page refresh always resets it
  // back to the top-right default, on the theory that "always reachable in
  // a known spot after a reload" beats "remembers a spot that might now be
  // sitting over completely different content." Dragging is clamped
  // (clampReportBtnPosition) so it can never be moved up into .site-header's
  // own band, or past either side/the bottom of the viewport. Also owns
  // #report-btn's click -> openReportModal wiring (rather than a plain
  // addEventListener elsewhere), since it needs to suppress the click that
  // still fires right after a real drag's pointerup -- without that, every
  // drag would pop the report sheet open the moment the user let go.
  function clampReportBtnPosition(left, top) {
    const margin = 8;
    const headerBottom = els.siteHeader ? els.siteHeader.getBoundingClientRect().bottom : 0;
    const minTop = headerBottom + margin;
    const maxTop = Math.max(minTop, window.innerHeight - els.reportBtn.offsetHeight - margin);
    const maxLeft = Math.max(margin, window.innerWidth - els.reportBtn.offsetWidth - margin);
    return {
      left: Math.min(Math.max(left, margin), maxLeft),
      top: Math.min(Math.max(top, minTop), maxTop)
    };
  }

  function applyReportBtnPosition(left, top) {
    const clamped = clampReportBtnPosition(left, top);
    els.reportBtn.style.left = `${clamped.left}px`;
    els.reportBtn.style.top = `${clamped.top}px`;
    els.reportBtn.style.right = "auto";
    els.reportBtn.style.bottom = "auto";
  }

  function initReportBtnDrag() {
    if (!els.reportBtn) return;
    let dragging = false;
    let justDragged = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    els.reportBtn.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      dragging = false;
      const rect = els.reportBtn.getBoundingClientRect();
      startX = e.clientX;
      startY = e.clientY;
      startLeft = rect.left;
      startTop = rect.top;
      els.reportBtn.setPointerCapture(e.pointerId);
    });

    els.reportBtn.addEventListener("pointermove", (e) => {
      if (!els.reportBtn.hasPointerCapture(e.pointerId)) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      // A few px of slack before this counts as a drag -- otherwise a plain
      // click (pointerdown+pointerup with no real movement) would still
      // reposition the button by whatever sub-pixel jitter the pointer had.
      if (!dragging && Math.hypot(dx, dy) < 4) return;
      dragging = true;
      applyReportBtnPosition(startLeft + dx, startTop + dy);
    });

    const endDrag = (e) => {
      if (!els.reportBtn.hasPointerCapture(e.pointerId)) return;
      els.reportBtn.releasePointerCapture(e.pointerId);
      if (dragging) {
        // Consumed by the click listener below -- pointerup still fires a
        // native click right after this, which must NOT reopen/toggle
        // anything just because the drag happened to end over the button.
        justDragged = true;
      }
      dragging = false;
    };
    els.reportBtn.addEventListener("pointerup", endDrag);
    els.reportBtn.addEventListener("pointercancel", endDrag);

    els.reportBtn.addEventListener("click", (e) => {
      if (justDragged) {
        justDragged = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      openReportModal();
    });

    // Re-clamp on resize -- only matters once it's been moved off the CSS
    // top/right default (that default already tracks --header-h and the
    // viewport on its own, needs no help). Without this, a dragged position
    // could otherwise end up stranded under a taller .site-header after a
    // breakpoint change, or past the new viewport edge after a resize.
    window.addEventListener("resize", () => {
      if (els.reportBtn.style.left && els.reportBtn.style.left !== "auto") {
        const rect = els.reportBtn.getBoundingClientRect();
        applyReportBtnPosition(rect.left, rect.top);
      }
    });
  }

  // Captured when #report-modal opens (not re-read from lastResult at
  // submit time) -- lastResult could in principle move on to a different
  // RUN/photo while the sheet is still open (e.g. the user switches RUNs
  // behind it), and the report should still land on whatever was actually
  // being looked at when 回報 was clicked.
  let reportModalTarget = null;

  // The blob: URL backing #report-modal-screenshot-img -- tracked so it can
  // be revoked (URL.revokeObjectURL) once it's no longer needed, rather
  // than leaking one every time the sheet opens.
  let reportModalScreenshotObjectUrl = null;

  // Whatever captureReportScreenshot last resolved to for the currently
  // open sheet -- null until the reporter actually presses
  // #report-modal-screenshot-capture-btn (attaching a screenshot is
  // optional; nothing is captured just from opening the sheet). Read
  // directly by handleReportSubmit at send time instead of re-awaiting a
  // promise, since capture is now a discrete user action rather than
  // something kicked off automatically on open.
  let reportModalScreenshotBlob = null;

  // Idle state (no screenshot attached, whether that's because nothing's
  // been captured yet or because one was just deleted) -- also wired
  // directly as #report-modal-screenshot-delete-btn's click handler, since
  // "delete the screenshot" and "reset back to idle" are the same thing.
  function resetReportModalScreenshotPreview() {
    reportModalScreenshotBlob = null;
    if (reportModalScreenshotObjectUrl) {
      URL.revokeObjectURL(reportModalScreenshotObjectUrl);
      reportModalScreenshotObjectUrl = null;
    }
    // Idle state has nothing to report yet -- no status line, and
    // #report-modal-screenshot (the preview box) stays hidden rather than
    // sitting there empty until the reporter actually presses capture.
    if (els.reportModalScreenshotStatus) {
      els.reportModalScreenshotStatus.textContent = "";
      els.reportModalScreenshotStatus.hidden = true;
    }
    if (els.reportModalScreenshot) els.reportModalScreenshot.hidden = true;
    if (els.reportModalScreenshotPreviewBtn) els.reportModalScreenshotPreviewBtn.hidden = true;
    if (els.reportModalScreenshotDeleteBtn) els.reportModalScreenshotDeleteBtn.hidden = true;
    if (els.reportModalScreenshotImg) els.reportModalScreenshotImg.removeAttribute("src");
    // No screenshot attached -- #report-modal-screenshot-capture-btn is the
    // only way back in, so it has to be visible here (applyReportModalScreenshotPreview's
    // success branch is what hides it again).
    if (els.reportModalScreenshotCaptureBtn) {
      els.reportModalScreenshotCaptureBtn.hidden = false;
      els.reportModalScreenshotCaptureBtn.disabled = false;
      els.reportModalScreenshotCaptureBtn.textContent = "擷取目前畫面";
    }
  }

  // Applies whatever captureReportScreenshot resolved to for one on-demand
  // capture -- guarded against a stale capture from an already-closed/
  // reopened sheet still resolving in the background (myTarget no longer
  // being the live reportModalTarget means the user moved on already).
  function applyReportModalScreenshotPreview(myTarget, blob) {
    if (reportModalTarget !== myTarget) return;
    reportModalScreenshotBlob = blob;
    if (!blob) {
      // Capture failed -- nothing to delete, so the capture button (not the
      // delete button) is still the way to try again.
      if (els.reportModalScreenshotCaptureBtn) {
        els.reportModalScreenshotCaptureBtn.hidden = false;
        els.reportModalScreenshotCaptureBtn.disabled = false;
        els.reportModalScreenshotCaptureBtn.textContent = "重新擷取";
      }
      if (els.reportModalScreenshotStatus) {
        els.reportModalScreenshotStatus.textContent = "無法擷取畫面預覽，回報仍可送出。";
        els.reportModalScreenshotStatus.hidden = false;
      }
      if (els.reportModalScreenshot) els.reportModalScreenshot.hidden = true;
      if (els.reportModalScreenshotPreviewBtn) els.reportModalScreenshotPreviewBtn.hidden = true;
      return;
    }
    reportModalScreenshotObjectUrl = URL.createObjectURL(blob);
    if (els.reportModalScreenshotStatus) els.reportModalScreenshotStatus.hidden = true;
    if (els.reportModalScreenshotImg) els.reportModalScreenshotImg.src = reportModalScreenshotObjectUrl;
    if (els.reportModalScreenshotPreviewBtn) els.reportModalScreenshotPreviewBtn.hidden = false;
    // Only reveal the preview box once there's actually an image ready to
    // show in it.
    if (els.reportModalScreenshot) els.reportModalScreenshot.hidden = false;
    // A screenshot now exists -- #report-modal-screenshot-delete-btn (hover/
    // focus-revealed, top-right of the preview) is the only way to get rid
    // of it, so the capture button steps aside instead of offering a
    // redundant "重新擷取" alongside it.
    if (els.reportModalScreenshotCaptureBtn) els.reportModalScreenshotCaptureBtn.hidden = true;
    if (els.reportModalScreenshotDeleteBtn) els.reportModalScreenshotDeleteBtn.hidden = false;
  }

  // Wired to #report-modal-screenshot-capture-btn -- the only place
  // captureReportScreenshot is ever invoked from #report-modal now (see
  // openReportModal's own comment). #report-modal is already open/visible
  // by the time this can run, but captureReportScreenshot's ignoreElements
  // excludes #report-modal/#report-screenshot-modal from the render
  // regardless, so the sheet itself never leaks into its own screenshot.
  async function handleReportModalScreenshotCapture() {
    if (!reportModalTarget) return;
    const myTarget = reportModalTarget;
    if (els.reportModalScreenshotCaptureBtn) {
      els.reportModalScreenshotCaptureBtn.disabled = true;
      els.reportModalScreenshotCaptureBtn.textContent = "擷取中…";
    }
    // No image ready yet for this attempt -- keep the preview box hidden
    // and surface progress via the status line instead.
    if (els.reportModalScreenshot) els.reportModalScreenshot.hidden = true;
    if (els.reportModalScreenshotPreviewBtn) els.reportModalScreenshotPreviewBtn.hidden = true;
    if (els.reportModalScreenshotDeleteBtn) els.reportModalScreenshotDeleteBtn.hidden = true;
    if (els.reportModalScreenshotStatus) {
      els.reportModalScreenshotStatus.textContent = "擷取畫面中…";
      els.reportModalScreenshotStatus.hidden = false;
    }
    const blob = await captureReportScreenshot(myTarget.jobId, myTarget.path, myTarget.runNumber);
    applyReportModalScreenshotPreview(myTarget, blob);
  }

  // Opens the report sheet instead of reporting immediately -- lets the
  // user confirm/edit which name it's attributed to (prefilled from the
  // current account, blank if there isn't one -- a name is required to
  // submit either way, see handleReportSubmit), preview the screenshot
  // that's about to be attached, and add an optional note describing the
  // issue.
  function openReportModal() {
    if (!els.reportModal) return;
    // #report-btn is always clickable, with or without a current result --
    // job-tied fields below are simply absent for a general "something's
    // wrong" report with nothing on screen to point at (reportJob/
    // record_report both already treat job_id as optional).
    // Whatever 補充指令 text actually produced the result currently on
    // screen: currentBatchRecord's own effectivePrompt (its per-image
    // override, or the batch-wide prompt) when a batch result is showing,
    // else lastSingleSelections.userPrompt -- set by finishSingleFlow for a
    // live single-image submission, or restored from job.user_prompt
    // (restoreReviseSelections) when viewing a history/report record.
    const userPrompt = lastResult
      ? (currentBatchRecord ? effectivePrompt(currentBatchRecord) : (lastSingleSelections?.userPrompt || ""))
      : "";
    // Attaching a screenshot is entirely optional and reporter-triggered
    // (handleReportModalScreenshotCapture, wired to
    // #report-modal-screenshot-capture-btn) -- nothing is captured just
    // from opening the sheet, so this only resets the preview UI to its
    // idle state.
    resetReportModalScreenshotPreview();
    const target = {
      jobId: lastResult?.jobId || null,
      path: lastResult?.path || "local",
      runNumber: lastResult?.runNumber || null,
      userPrompt
    };
    reportModalTarget = target;
    if (els.reportModalTitle) els.reportModalTitle.textContent = lastResult ? "回報此結果" : "回報問題";
    if (els.reportModalNote) {
      els.reportModalNote.placeholder = lastResult ? "請描述這筆結果的問題…" : "請描述遇到的問題…";
    }
    if (els.reportModalName) els.reportModalName.value = getCurrentUser() || "";
    if (els.reportModalNote) els.reportModalNote.value = "";
    if (els.reportModalError) els.reportModalError.hidden = true;
    els.reportModal.hidden = false;
  }

  function closeReportModal() {
    if (els.reportModal) els.reportModal.hidden = true;
    reportModalTarget = null;
    resetReportModalScreenshotPreview();
  }

  function showReportModalError(message) {
    if (!els.reportModalError) return;
    els.reportModalError.textContent = message;
    els.reportModalError.hidden = false;
  }

  async function handleReportSubmit() {
    if (!reportModalTarget) return;
    const name = normalizeUsername(els.reportModalName ? els.reportModalName.value : "");
    if (!name) {
      showReportModalError("請輸入你的名稱。");
      return;
    }
    const note = (els.reportModalNote ? els.reportModalNote.value : "").trim();
    const target = reportModalTarget;
    if (els.reportModalSubmitBtn) els.reportModalSubmitBtn.disabled = true;
    try {
      // reportModalScreenshotBlob is null whenever the reporter never
      // pressed #report-modal-screenshot-capture-btn, or a capture attempt
      // failed (captureReportScreenshot swallows its own errors, resolving
      // null) -- either way the report still goes through, just without a
      // screenshot attached.
      await reportJob(name, target.jobId, target.path, note, target.userPrompt, reportModalScreenshotBlob);
      closeReportModal();
      showNoticeModal("已回報，我們會盡快確認這筆結果。");
    } catch (err) {
      showReportModalError(err.message || "回報失敗，請稍後再試。");
    } finally {
      if (els.reportModalSubmitBtn) els.reportModalSubmitBtn.disabled = false;
    }
  }

  // Centered toast shown while buildZip() is fetching each result image over
  // the network -- otherwise a batch download with many images just looks
  // like nothing is happening until the browser's save dialog finally
  // appears. A single-image download only ever reports one "done" tick, so
  // without help the fill would otherwise jump straight from 0% to 100%
  // (and the toast close right behind it) with no visible motion at all --
  // showDownloadProgress bumps to a small interim value right away so it's
  // moving immediately, and hideDownloadProgress holds briefly at 100% on
  // success so the "reaching completion" moment is actually seen.
  function showDownloadProgress() {
    if (!els.downloadProgressToast) return;
    if (els.downloadProgressFill) els.downloadProgressFill.style.width = "0%";
    if (els.downloadProgressLabel) els.downloadProgressLabel.textContent = "打包下載中…";
    els.downloadProgressToast.hidden = false;
    // A short delay so the 0% state is painted before transitioning to 25%
    // -- rAF would be the "proper" way to wait for that paint, but it never
    // fires in a backgrounded/inactive tab (or some headless contexts),
    // silently dropping this bump; a plain timeout always eventually runs.
    setTimeout(() => {
      if (els.downloadProgressFill && els.downloadProgressFill.style.width === "0%") {
        els.downloadProgressFill.style.width = "25%";
      }
    }, 30);
  }

  function updateDownloadProgress(done, total) {
    if (!els.downloadProgressToast) return;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    if (els.downloadProgressFill) els.downloadProgressFill.style.width = `${pct}%`;
    if (els.downloadProgressLabel) {
      els.downloadProgressLabel.textContent = total > 1
        ? `打包下載中…（${done}/${total}）`
        : "打包下載中…";
    }
  }

  function hideDownloadProgress(success) {
    if (!els.downloadProgressToast) return;
    if (success && els.downloadProgressFill) els.downloadProgressFill.style.width = "100%";
    setTimeout(() => {
      if (els.downloadProgressToast) els.downloadProgressToast.hidden = true;
    }, success ? 300 : 0);
  }

  async function downloadZip(buildZip, filename) {
    showDownloadProgress();
    let success = false;
    try {
      const zipBlob = await buildZip(updateDownloadProgress);
      const url = URL.createObjectURL(zipBlob);
      const tempLink = document.createElement("a");
      tempLink.href = url;
      tempLink.download = filename;
      document.body.appendChild(tempLink);
      tempLink.click();
      tempLink.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      success = true;
    } catch (error) {
      showError("打包下載失敗，請稍後再試。");
    } finally {
      hideDownloadProgress(success);
    }
    // Confirms the zip actually landed -- otherwise the only feedback is the
    // progress toast quietly fading out, easy to miss for a multi-select
    // download that took a while to build.
    if (success) showNoticeModal("已下載完成。");
  }

  // Single current-result download: just the generated image itself, not a
  // zip -- there's only ever one file involved here, so bundling it into a
  // zip (source image + result) was unnecessary overhead for the common
  // case. --debug-ui mode still gets the full zip (source + result.json +
  // prompts.json via buildResultZip), since exposing that raw data is the
  // whole point of that mode.
  async function downloadCurrentResultImage() {
    const runs = lastResultRuns();
    const current = runs.find((run) => run.run === lastResult.runNumber) || runs[0];
    try {
      const imageBuf = await fetch(resultImageUrl(current.jobId, { path: lastResult.path, run: current.run })).then((response) => response.arrayBuffer());
      const blob = new Blob([imageBuf], { type: "image/png" });
      const url = URL.createObjectURL(blob);
      const tempLink = document.createElement("a");
      tempLink.href = url;
      tempLink.download = buildDownloadImageFilename();
      document.body.appendChild(tempLink);
      tempLink.click();
      tempLink.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      // Same completion notice downloadZip shows -- this path bypasses that
      // shared function (a single PNG needs no zip), so it needs its own.
      showNoticeModal("已下載完成。");
    } catch (error) {
      showError("下載失敗，請稍後再試。");
    }
  }

  function downloadCurrentResultOnly() {
    if (isDebugUiActive) {
      downloadZip(buildResultZip, buildDownloadZipFilename());
    } else {
      downloadCurrentResultImage();
    }
  }

  // #download-link only asks "current RUN or all RUNs?" when there's an
  // actual choice to make -- with one (or zero) finished RUNs it just
  // downloads the current result directly, same as before this menu existed.
  function handleDownloadClick(event) {
    event.preventDefault();
    if (!lastResult) return;
    if (lastDoneRunsCount > 1) {
      toggleDownloadMenu();
      return;
    }
    downloadCurrentResultOnly();
  }

  function handleDownloadCurrentBtnClick() {
    closeDownloadMenu();
    if (!lastResult) return;
    downloadCurrentResultOnly();
  }

  function handleDownloadAllRunsBtnClick() {
    closeDownloadMenu();
    if (!lastResult) return;
    downloadZip(buildAllRunsZip, buildDownloadZipFilename());
  }

  function openDownloadMenu() {
    if (!els.downloadMenu) return;
    els.downloadMenu.hidden = false;
    document.addEventListener("click", handleDownloadMenuOutsideClick);
  }

  function closeDownloadMenu() {
    if (!els.downloadMenu || els.downloadMenu.hidden) return;
    els.downloadMenu.hidden = true;
    document.removeEventListener("click", handleDownloadMenuOutsideClick);
  }

  function toggleDownloadMenu() {
    if (!els.downloadMenu) return;
    if (els.downloadMenu.hidden) openDownloadMenu(); else closeDownloadMenu();
  }

  function handleDownloadMenuOutsideClick(event) {
    if (els.downloadMenuWrap && !els.downloadMenuWrap.contains(event.target)) closeDownloadMenu();
  }

  // Shared record shape for both a fresh batch (setSelectedFileBatch) and
  // photos appended to an already-selected one (addFilesToBatch). previewUrl/
  // thumbUrl start null -- convertBatchRecord fills them in once this one
  // photo's own HEIC decode/thumbnail generation finishes, independently of
  // every other record (see its own doc for why that's not done up front,
  // all at once, here).
  function makeBatchRecord(file) {
    return {
      localId: `f${nextBatchLocalId++}`,
      file,
      previewUrl: null,
      // Small JPEG counterpart of previewUrl, for the batch "processing"
      // main-thumb animation only -- see previewThumbUrl's own doc above for
      // why. May stay null (createProcThumbUrl failed) -- callers fall back
      // to previewUrl itself.
      thumbUrl: null,
      // Sets renderDzMultiThumbs' tile to a spinner placeholder instead of
      // the (not yet available) photo -- separate from `status` below, which
      // only starts once the user presses Start and has nothing to do with
      // this purely-local preview-rendering step.
      previewPending: true,
      // pending -> analyzing -> analyzed -> processing -> done / error
      status: "pending",
      analysis: null,       // step-1 result (shown in the step-2 review panel)
      analyzeJobId: null,   // local path only: the analyze job, reused by continue
      options: null,        // per-image style options {pose,angle,lighting,size} (UI-only)
      optionsTouched: {},   // per-category: did the user actually change this image's own radio (vs. just viewing the batch-inherited default)
      recipeChecked: null,  // per-image recipe checklist state (UI-only)
      userPrompt: "",       // per-image 補充指令 (entered in the step-2 review)
      overwritePrompt: false, // "取代最終送給編輯模型的完整指令" checkbox, alongside userPrompt
      analyzeTimings: [],   // step-1 timing rows, merged into the detail view
      jobId: null,          // step-2 edit job/task id
      result: null,
      imageUrl: null,
      error: null,
      timings: [],
      // Which `status` procSubIndex below was last saved for, and how far
      // the rotating processing-sub-text (showBatchMainLoading) had gotten
      // -- lets switching the sidebar selection away and back resume this
      // record's own rotation in place instead of restarting it at message
      // 1, since only one of these can actually be showing/ticking at once.
      // procSubStage !== status (a genuinely new stage, e.g. analyzing ->
      // processing) is what tells showBatchMainLoading to start fresh
      // instead of resuming this stale index.
      procSubStage: null,
      procSubIndex: 0,
      // Per-image 模型/畫質 + 長寬比 overrides (UI-only), set only once this
      // image's own revise panel is used to change them -- null means
      // "still inherits the batch-level default", same override rule as
      // options/optionsTouched above (see effectiveModelQuality/
      // effectiveAspectRatio). Without their own slot here, every case's
      // revise panel wrote straight into the single shared `batchSettings`,
      // so picking a model on case 1 silently carried over to case 2.
      imageModel: null,
      imageQuality: null,
      aspectRatio: null
    };
  }

  // Filters `files` down to supported images, showing the same rule/message
  // either caller (setSelectedFileBatch/addFilesToBatch) needs -- returns
  // null (already handled) if nothing usable was left.
  function filterBatchImageFiles(files) {
    const images = files.filter((file) => isSupportedImageFile(file));
    if (!images.length) {
      showError("請選擇 JPG、PNG、WebP 或 HEIC/HEIF 圖片。");
      return null;
    }
    if (images.length !== files.length) {
      showError("已略過非圖片檔案。");
    }
    return images;
  }

  // Converts one batch photo's own preview (HEIC decode + thumbnail) --
  // called once per record, independently, instead of Promise.all-ing the
  // whole batch, so one slow HEIC photo can't hold up every other
  // already-ready thumbnail from appearing. Updates just this record's own
  // tile in place once done, rather than re-rendering the whole strip.
  //
  // Bails at each step if `record` is no longer part of the current batch --
  // deliberately checking that instead of comparing fileSelectionToken. That
  // counter is shared with addFilesToBatch's own later calls, which append
  // rather than replace the batch, and would otherwise wrongly cancel an
  // earlier still-converting photo's result just because a second "add
  // more" click happened first. Removal (removeBatchFile) or the whole
  // batch being replaced by an unrelated new selection both take `record`
  // out of batchRecords, which this still correctly catches.
  async function convertBatchRecord(record) {
    const blob = await previewableBlob(record.file);
    if (!batchRecords.includes(record)) return;

    const thumbUrl = await createProcThumbUrl(blob);
    if (!batchRecords.includes(record)) {
      if (thumbUrl) URL.revokeObjectURL(thumbUrl);
      return;
    }

    record.previewUrl = URL.createObjectURL(blob);
    record.thumbUrl = thumbUrl;
    record.previewPending = false;

    const tile = els.dzMulti.querySelector(`[data-local-id="${record.localId}"]`);
    if (tile) fillDzThumb(tile, record);
  }

  // Batch counterpart of setSelectedFile: filter to images (same rule/message),
  // build one record per file, and render the multi-thumbnail strip. Each
  // photo's own preview then converts independently (convertBatchRecord) --
  // the strip shows every tile immediately, as spinner placeholders, rather
  // than waiting for every single one to finish before showing any of them.
  async function setSelectedFileBatch(files) {
    hideError();
    resetBatchState();
    const images = filterBatchImageFiles(files);
    if (!images) return;

    fileSelectionToken++; // invalidates any still-in-flight single-photo (setSelectedFile) conversion
    selectedFile = null;
    batchRecords = images.map((file) => makeBatchRecord(file));

    els.dropZone.hidden = true;
    els.dzPreview.hidden = true;
    els.dzMulti.hidden = false;
    renderDzMultiThumbs();

    batchRecords.forEach((record) => convertBatchRecord(record));
  }

  // Appends photos to the batch already shown in #dz-multi (triggered only
  // by #dz-add-more, see routeSelectedFiles) instead of replacing it --
  // setSelectedFileBatch always starts from a fresh resetBatchState(), which
  // would otherwise discard whatever was already selected. Same
  // immediate-placeholder-then-convert-independently shape as
  // setSelectedFileBatch, just concatenated onto the existing batchRecords
  // instead of replacing them.
  async function addFilesToBatch(files) {
    hideError();
    const images = filterBatchImageFiles(files);
    if (!images) return;

    fileSelectionToken++; // invalidates any still-in-flight single-photo (setSelectedFile) conversion
    const newRecords = images.map((file) => makeBatchRecord(file));
    batchRecords = batchRecords.concat(newRecords);
    renderDzMultiThumbs();

    newRecords.forEach((record) => convertBatchRecord(record));
  }

  // Fills one #dz-multi tile with `record`'s current state -- either a
  // spinner placeholder (still converting) or the real photo + remove
  // button. Shared by renderDzMultiThumbs (initial render, and rebuilding
  // after a removal) and convertBatchRecord (in-place update of just the one
  // tile whose own conversion just finished).
  function fillDzThumb(thumb, record) {
    thumb.innerHTML = "";
    thumb.classList.toggle("is-converting", !!record.previewPending);
    if (record.previewPending) {
      const spinner = document.createElement("div");
      spinner.className = "rc-spinner";
      thumb.appendChild(spinner);
      return;
    }

    const im = document.createElement("img");
    im.src = record.previewUrl;
    im.alt = record.file.name;
    thumb.appendChild(im);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "dz-thumb-remove";
    removeBtn.setAttribute("aria-label", "移除照片");
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      removeBatchFile(record);
    });
    thumb.appendChild(removeBtn);
  }

  // Rebuilds the multi-file thumbnail strip from the current batchRecords.
  // Called on initial selection and again whenever a thumbnail is removed --
  // a single record finishing its own conversion updates its tile directly
  // instead (convertBatchRecord), without rebuilding every other tile too.
  function renderDzMultiThumbs() {
    els.dzMulti.innerHTML = "";
    batchRecords.forEach((r) => {
      const thumb = document.createElement("div");
      thumb.className = "dz-thumb";
      thumb.dataset.localId = r.localId;
      fillDzThumb(thumb, r);
      els.dzMulti.appendChild(thumb);
    });

    // Trailing "add more" tile, same grid cell size as a real thumbnail --
    // clicking it falls through to #dz-multi's own click handler (reopens
    // the file picker), same as clicking anywhere else in the strip.
    const addMore = document.createElement("div");
    addMore.className = "dz-add-more";
    addMore.setAttribute("role", "button");
    addMore.setAttribute("aria-label", "新增照片");
    addMore.innerHTML = `
      <span class="dz-add-more-icon">+</span>
      <span class="dz-add-more-label">新增照片</span>
    `;
    els.dzMulti.appendChild(addMore);
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
    if (record.thumbUrl) URL.revokeObjectURL(record.thumbUrl);

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
    // A record other than whichever one was on screen can still have its
    // rotation "paused" (see showBatchMainLoading) rather than genuinely
    // stopped -- its getMessages closure reads batchSettings live on every
    // tick, so nulling that out below without stopping the timer first would
    // throw the next time it fires.
    if (activeBatchRotationRecord) stopProcSubRotation();
    batchRecords.forEach((r) => {
      if (r.previewUrl) URL.revokeObjectURL(r.previewUrl);
      if (r.thumbUrl) URL.revokeObjectURL(r.thumbUrl);
    });
    batchRecords = [];
    batchSettings = null;
    currentBatchRecord = null;
    userPickedBatchRecord = false;
    // Any prior single-image run's "回到流程" state refers to a different
    // photo (or no longer applies once batch mode starts) -- drop it.
    singleFlowState = null;
    lastSingleSelections = null;
    if (els.showPromptBtn) els.showPromptBtn.hidden = true;
    if (els.dzMulti) { els.dzMulti.hidden = true; els.dzMulti.innerHTML = ""; }
    if (els.batchSidebarList) els.batchSidebarList.innerHTML = "";
    if (els.batchSidebar) els.batchSidebar.hidden = true;
    if (els.resultsBody) els.resultsBody.classList.remove("batch-mode");
    closeBatchDrawer();
    // Undo whatever hideAllBatchMainViews left mid-batch, so a subsequent
    // single-image run (which never touches these) isn't left with the
    // canvas/run switcher hidden from a prior batch session.
    if (els.batchMainLoading) { els.batchMainLoading.hidden = true; els.batchMainLoading.classList.remove("is-error", "is-analyzed"); }
    if (els.batchReviewContinueBtn) els.batchReviewContinueBtn.hidden = true;
    restoreAnalysisPreviewHome();
    if (els.resultsCanvasRow) { els.resultsCanvasRow.hidden = false; els.resultsCanvasRow.classList.remove("showing-analysis"); }
    if (els.resultsBody) els.resultsBody.classList.remove("showing-analysis");
    resetRunSwitcher();
    if (els.reloadBatchBtn) els.reloadBatchBtn.hidden = true;
    if (els.batchDownloadAllBtn) els.batchDownloadAllBtn.hidden = true;
    if (els.batchContinueAllBtn) els.batchContinueAllBtn.hidden = true;
    if (els.batchPromptRow) els.batchPromptRow.hidden = true;
    if (els.batchPromptInput) els.batchPromptInput.value = "";
    if (els.batchOptionsRow) { els.batchOptionsRow.hidden = true; els.batchOptionsRow.open = false; }
    renderBatchStyleOptions();
    updateBatchOptionsLocks(); // batchRecords is now empty, so this clears every category's lock/toggle
  }

  // Labels for the categories OpenAI's safety system has been observed to
  // tag (safety_violations=[...] in the raw error) -- keyed lowercase since
  // the exact casing isn't guaranteed. Anything not listed here (including a
  // category OpenAI adds later) falls back to a generic explanation rather
  // than guessing a specific reason that might be wrong.
  const SAFETY_VIOLATION_LABELS = {
    sexual: "照片可能包含裸露或性暗示內容",
    violence: "照片可能包含暴力或血腥內容",
    "self-harm": "照片可能包含自我傷害相關內容",
    self_harm: "照片可能包含自我傷害相關內容",
    hate: "照片可能包含仇恨或歧視相關內容",
    harassment: "照片可能包含騷擾相關內容"
  };

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return "無法連線到 Photo Master 伺服器，請確認後端服務是否已啟動。";
    }
    const msg = error.message || "";
    // OpenAI's images/edits API rejects requests its safety system flags --
    // a platform content policy we can't bypass, not a transient failure
    // worth retrying with the same photo. The specific reason varies (seen
    // both a recognizable copyrighted/trademarked character -- branded
    // plush toys, mascots, etc. -- and sexual-content flags), so read the
    // actual safety_violations=[...] category out of the error instead of
    // always assuming it's about copyright.
    if (/rejected by the safety system/i.test(msg)) {
      const violationMatch = msg.match(/safety_violations=\[([^\]]*)\]/i);
      const violations = violationMatch
        ? violationMatch[1].split(",").map((v) => v.trim().replace(/^['"]|['"]$/g, "").toLowerCase()).filter(Boolean)
        : [];
      const knownReasons = violations.map((v) => SAFETY_VIOLATION_LABELS[v]).filter(Boolean);
      const reasonText = knownReasons.length
        ? knownReasons.join("、") + "，"
        : "這通常是因為照片中包含平台內容政策不允許編輯的元素（例如可辨識的受著作權保護角色/商標，或其他敏感內容），";
      return `AI 修圖模型拒絕處理這張照片——${reasonText}基於平台政策無法編輯這類內容。請嘗試更換照片，或改用其他修圖模型再試一次。`;
    }
    return msg || "發生未知錯誤。";
  }

  // Analyze once + edit `runCount` times, ALL inside ONE backend job
  // (submitJob with run_count, no analysis param -> the worker runs
  // analyze_stage then finish_stage `runCount` times itself, see
  // job_processor.py) instead of the two-stage analyzeJob+continueJob dance
  // OR driving each RUN as its own client-submitted job. Used only by the
  // "auto" (skip-review) flows on the local path, where there's nothing to
  // review/adjust between RUNs anyway -- the analysis-preview DOM is never
  // shown, so its recipe/style defaults are always what gets applied.
  //
  // Critically, every RUN's generation happens SERVER-SIDE within this one
  // job, entirely independent of whether this tab is open/foregrounded to
  // poll it -- a phone can sleep for the whole duration and every RUN still
  // finishes; this tab just catches up on however many are done once it
  // resumes. onRunDone fires (in order) for each RUN as soon as this tab
  // notices it landed (via runs_done ticking up in /status), so the UI can
  // still render RUNs incrementally while actively polling -- but nothing
  // about the actual generation depends on that happening promptly, or at
  // all, before the job finishes.
  //
  // AIPoC's own analyze already runs synchronously (no poll at all), so it
  // never had this problem and isn't touched here; the manual review flow
  // still needs the real two-stage analyze->adjust->continue path (and its
  // own per-RUN edit jobs, since each RUN there can carry different
  // per-image style/recipe overrides picked during review).
  async function runCombinedMultiRun({ file, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, advancedOptions, userPrompt = "", overwritePrompt = false, runCount = 1, onStage, onRunDone }) {
    const jobId = await submitJob({ file, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, advancedOptions, userPrompt, overwritePrompt, runCount, user: getCurrentUser(), path: "local" });
    await watchCombinedJobRuns(jobId, runCount, { onStage, onRunDone });
  }

  // The polling half of runCombinedMultiRun, split out so a still-processing
  // combined job can be RESUMED (e.g. from viewProcessingHistoryRecord, after
  // navigating away and back through history) without resubmitting it --
  // this just watches an already-submitted jobId's own progress.
  async function watchCombinedJobRuns(jobId, runCount, { onStage, onRunDone } = {}) {
    // Tracks which RUN *numbers* have already been delivered (a Set, not a
    // "highest number seen" watermark) -- /result can transiently return
    // fewer/out-of-order entries than the true count if one RUN's metadata
    // download hiccups server-side (see api.py's _runs_from_meta, which just
    // skips a RUN it can't read yet rather than failing the whole response).
    // A watermark would let a LATER tick's higher run number permanently
    // strand the skipped one; a Set delivers each RUN exactly once no matter
    // what order/gaps show up across polls. lastRunsDoneSeen is separate --
    // just a cheap "did anything change" gate so a poll tick with no new
    // progress doesn't bother calling /result at all.
    let lastRunsDoneSeen = 0;
    let lastStageSeen = null;
    const deliveredRuns = new Set();
    const fetchNewRuns = async () => {
      const { runs } = await getJobRuns(jobId, { path: "local" });
      (runs || []).forEach((runEntry) => {
        if (!deliveredRuns.has(runEntry.run)) {
          deliveredRuns.add(runEntry.run);
          if (onRunDone) onRunDone({ ...runEntry, jobId });
        }
      });
    };

    // A job covering `runCount` sequential edit calls needs a proportionally
    // larger budget than the single-RUN default -- otherwise a legitimately
    // slow-but-progressing multi-RUN job would look "timed out" long before
    // the server actually finishes it.
    await pollJobStatus(jobId, {
      path: "local",
      timeoutMs: runCount * 5 * 60 * 1000,
      onStatus: async (data) => {
        // data.stage stays "edit" for the entire edit phase (job_processor.py
        // sets it once and never clears it), so this must only fire on the
        // actual analyze->edit transition -- calling onStage on every tick
        // re-applied its generic "請稍候" wording every ~1.5s, stomping the
        // "已完成 X/N RUN" progress text updateRunProgress had just set below.
        if (onStage && data.stage && data.stage !== lastStageSeen) {
          lastStageSeen = data.stage;
          onStage(data.stage);
        }
        if (typeof data.runs_done === "number" && data.runs_done > lastRunsDoneSeen) {
          lastRunsDoneSeen = data.runs_done;
          await fetchNewRuns();
        }
      }
    });
    // Catches up on any RUN(s) that finished between the last poll tick and
    // the job being marked done -- also the only path taken at all if this
    // tab never had a chance to poll mid-flight (e.g. it only just resumed
    // from sleep as the job was already complete).
    await fetchNewRuns();
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
    const analysisModel = els.analysisModelSelect?.value || window.PhotoAgentConfig.defaultAnalysisModel || "gpt-4.1-mini";
    const aspectRatio = els.imageAspectRatioSelect?.value || window.PhotoAgentConfig.defaultAspectRatio || "auto";
    const aiPhotoEditing = els.aiEditToggle.checked;
    const runQa = els.runQaToggle ? els.runQaToggle.checked : false;
    // Advanced Settings toggles (Eyes category, and whatever joins it later)
    // -- bundled into one object instead of one variable per toggle, so a new
    // toggle only needs a new key here, not a new argument threaded through
    // analyzeAipoc/analyzeJob/singleFlowState/runCombinedMultiRun/submitJob.
    const advancedOptions = readAdvancedOptionsFromDom();
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
    startProcSubRotation(analyzeSubMessages);
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

      // Skip-review + local path: analyze and all RUNs' edits happen inside
      // one combined job (runCombinedMultiRun), so there's no separate
      // analyze step to run here at all -- finishSingleFlow fills
      // singleFlowState.analysis in from the combined job's own result once
      // it completes.
      const useCombinedJob = skipReview && processingPath === "local";

      if (!useCombinedJob) {
        if (processingPath === "aipoc") {
          const analyzeData = await analyzeAipoc({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, advancedOptions });
          analysis = analyzeData.analysis;
          const t = analysis.timings || {};
          recordAnalyzeTiming("初步分析", Date.now() - stepStart, [
            { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) }
          ]);
        } else {
          analyzeJobId = await analyzeJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, advancedOptions });
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

      singleFlowState = { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, advancedOptions, processingPath, analyzeTimings, useCombinedJob };

      if (useCombinedJob) {
        await finishSingleFlow();
      } else if (skipReview) {
        // Straight through, no stop: still populate the analysis-preview DOM
        // (finishSingleFlow reads its recipe checkboxes/style picks/prompt
        // straight from there) but never show the panel or wait for a click.
        renderAnalysisPreview({ analysis: stripDisabledTopicItems(analysis, singleFlowState.advancedOptions), preset });
        await finishSingleFlow();
      } else {
        showAnalysisPreview(analysis, preset);
        await waitForContinue();
        await finishSingleFlow();
      }
    } catch (error) {
      // Lets any not-yet-shown phrasings finish playing (fast-forwarded)
      // before bailing back to the upload screen.
      await finishProcSubRotation();
      showSection("upload");
      showError(friendlyError(error));
      // Back to the upload screen -- unlike a successful finish (which now
      // leaves analysis-preview open by default beside the results canvas),
      // there's no results view here for it to belong to.
      if (els.analysisPreview) els.analysisPreview.hidden = true;
    } finally {
      els.startButton.disabled = false;
      if (els.startDirectBtn) els.startDirectBtn.disabled = false;
      clearContinueWait();
      // Safety net for the isCurrent()===false edge case, where
      // revealResultsIfNeeded/showAnalysisPreview bail out without ever
      // consuming the rotation themselves -- instant, not fast-forwarded,
      // since there's no longer a screen for it to play out on.
      stopProcSubRotation();
    }
  }

  // Steps 3-6 for the single-image flow, reusable both for the first run
  // (right after analyze, called from startSubmit) and for a later
  // re-adjustment run (called from backToProcessFromResults) -- reads
  // singleFlowState (set once by analyze) plus whatever the user currently
  // has checked/typed in the analysis-preview DOM.
  async function finishSingleFlow() {
    // Snapshot of historyViewToken as of when THIS revise started -- if it's
    // changed by the time (part of) this job's result comes back, the user
    // has since switched to viewing a different record (viewHistoryRecord/
    // viewProcessingHistoryRecord both bump it), and this job's result must
    // NOT be applied to the screen: there's no per-record identity here (the
    // batch flow guards its own re-render with refreshIfSelected(record)
    // instead) to check against, just "is the screen still showing what this
    // job started from."
    const myHistoryToken = historyViewToken;
    const isCurrent = () => historyViewToken === myHistoryToken;
    const { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, advancedOptions, processingPath, analyzeTimings, useCombinedJob } = singleFlowState;
    const selections = readStyleSelectionsFromDom();
    const userPrompt = els.promptInput.value.trim();
    const overwritePrompt = els.promptOverwriteToggle ? els.promptOverwriteToggle.checked : false;
    lastSingleSelections = { userPrompt, overwritePrompt, ...selections };
    // No analysis yet in combined mode -- nothing to fold recipe/style
    // adjustments into, since the combined job computes its own from scratch.
    const effectiveAnalysis = useCombinedJob ? null : buildEffectiveAnalysis(analysis, { ...selections, advancedOptions });
    const runCount = Math.max(1, parseInt(els.apRunCountSelect?.value, 10) || 1);
    // Declared here (not inside either branch below) so every edit-stage
    // rotation closure -- the initial one right below, onCombinedStage, and
    // both updateRunProgress functions further down -- shares this single
    // live binding instead of each closing over its own frozen snapshot.
    let doneCount = 0;

    setAnalysisPreviewDisabled(true);
    procVisual.classList.remove("done");
    setProcTitle(useCombinedJob ? "正在分析照片" : (aiPhotoEditing ? "AI 正在修圖" : "正在處理照片"));
    if (useCombinedJob) {
      startProcSubRotation(analyzeSubMessages);
    } else {
      startProcSubRotation(() => editSubMessages({ aiPhotoEditing, imageModel, doneCount, runCount }));
    }
    // Live progress messaging for a combined job, driven by the backend's
    // reported stage (job_processor.py's set_job_info(..., results={"stage": ...})
    // calls) -- keeps the same two-phase "分析中 -> 修圖中" wording the old
    // two-job flow showed, even though it's now one job/one poll.
    const onCombinedStage = (stage) => {
      if (stage !== "edit") return;
      setProcTitle(aiPhotoEditing ? "AI 正在修圖" : "正在處理照片");
      startProcSubRotation(() => editSubMessages({ aiPhotoEditing, imageModel, doneCount, runCount }));
    };

    // Switches from the processing screen to the results screen the first
    // time ANY RUN finishes, instead of waiting for every RUN -- called
    // again (as a no-op) at the very end for whichever RUN-count/path
    // branch below doesn't already reveal incrementally. showSection resets
    // scroll to the top, so this must only actually fire once: re-running it
    // on every later RUN would keep yanking the view back up while the user
    // is looking at an already-shown result.
    // Also flips #canvas-loading/#photo-frame-wrap back here (not just in
    // backToProcessFromResults's finally, which only runs after every RUN
    // settles) -- a results-page re-run must swap its ring-animation loading
    // card for the actual photo the moment RUN 1 lands, same as the upload
    // flow does, instead of leaving that card up (stacked awkwardly over the
    // #run-progress bar, which starts showing as soon as any RUN is done)
    // for the whole multi-RUN job. A no-op for the original upload flow,
    // where neither element is ever toggled away from its default state.
    // #ap-options-card-inline itself stays closed the whole time regardless
    // (see showSingleResult's autoOpenPanel doc) -- opening it early would
    // let the user fire a second, overlapping revise session sharing this
    // same singleRuns/singleFlowState while these still-processing RUNs are
    // still writing into it. It only reopens once every RUN has settled, via
    // the plain (autoOpenPanel default true) showSingleResult() call below.
    let resultsRevealed = false;
    const revealResultsIfNeeded = async () => {
      if (resultsRevealed || !isCurrent()) return;
      resultsRevealed = true;
      // Lets any not-yet-shown edit-stage phrasings finish playing (fast-
      // forwarded) before switching away from the processing screen.
      await finishProcSubRotation();
      // Live session, not a historical revealDetailExtra view -- debug UI
      // alone decides it here.
      if (els.showPromptBtn) els.showPromptBtn.hidden = !isDebugUiActive;
      showSection("results");
      if (els.canvasLoading) els.canvasLoading.hidden = true;
      if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
    };

    if (useCombinedJob) {
      // Analysis + all `runCount` RUNs happen server-side within ONE job
      // (runCombinedMultiRun) -- unlike the two-stage branches below, this
      // doesn't special-case runCount === 1 at all: a single RUN is just the
      // degenerate case of the same server-side loop, and
      // showSingleResult()/renderSingleRunSwitcher() already render a lone
      // RUN identically to how the old inline single-result code did
      // (renderSingleRunSwitcher hides the switcher whenever there's only one).
      // Captured once here, then read/mutated as myRuns throughout this
      // branch instead of the bare singleRuns identifier -- onRunDone (and
      // the post-loop checks below) run after this async job's awaits, by
      // which point singleRuns may have been reassigned to a totally
      // different record's array (viewHistoryRecord). Reading the global
      // fresh each time would silently write this job's results into (or
      // read completion state from) that unrelated array.
      const myRuns = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing",
        imageUrl: null, result: null, timings: [], error: null
      }));
      singleRuns = myRuns;
      singleMirrorIndex = null;

      const updateRunProgress = () => refreshProcSubRotation();

      const stepStart = Date.now();
      try {
        await runCombinedMultiRun({
          file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, advancedOptions, userPrompt, overwritePrompt, runCount,
          onStage: onCombinedStage,
          onRunDone: async (runData) => {
            const run = myRuns[runData.run - 1];
            if (!run) return;
            run.jobId = runData.jobId;
            run.status = "done";
            run.imageUrl = runData.image_url;
            run.result = runData.result;
            const t = (runData.result["extra-info"] || {}).timings || {};
            run.timings = [
              { label: "分析並修圖", duration: Date.now() - stepStart, children: [
                { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) },
                { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
                { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
                { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
                { label: "Step 5b：眼神修正", duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
                { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
              ] }
            ];
            doneCount += 1;
            updateRunProgress();
            // autoOpenPanel: false -- this fires per-RUN, not just once at
            // the end (see showSingleResult's own doc on this param).
            // showSingleResult now preloads the image itself before rendering,
            // so the processing screen (still up until revealResultsIfNeeded
            // runs) stays visible for the whole download instead of the
            // results canvas appearing with a half-painted image.
            if (isCurrent()) { await showSingleResult({ autoOpenPanel: false }); revealResultsIfNeeded(); }
          }
        });
      } catch (error) {
        // The job itself failed (e.g. analyze_stage threw) -- whichever RUNs
        // never got an onRunDone callback can't proceed, since there's no
        // shared analysis for them to have used anyway.
        myRuns.forEach((run) => {
          if (run.status !== "done") { run.status = "error"; run.error = friendlyError(error); }
        });
      }

      if (!myRuns.some((r) => r.status === "done")) {
        throw new Error(myRuns.find((r) => r.error)?.error || "所有 RUN 皆失敗，請稍後再試。");
      }
      // Remember the first successfully-done RUN's analysis + job id so a
      // later re-run (回到流程) goes through the normal two-stage continueJob
      // path instead of submitting a combined job again -- continueJob only
      // needs a job id whose Redis record has valid srcKeys, which this
      // job's does. Skipped entirely if stale: singleFlowState may by now
      // belong to a different record, and must not be overwritten with this
      // job's own.
      if (isCurrent()) {
        const doneRun = myRuns.find((r) => r.status === "done");
        singleFlowState.analysis = doneRun.result.analysis;
        singleFlowState.analyzeJobId = doneRun.jobId;
        singleFlowState.useCombinedJob = false;
        await showSingleResult();
      }
    } else if (runCount === 1) {
      // Single RUN, two-stage manual/AIPoC path: unchanged from before
      // RUN-count support existed, so the common case keeps its exact step
      // timeline.
      singleRuns = null;
      singleMirrorIndex = null;

      let stepStart = Date.now();
      let finalJobId, imageUrl, result;

      if (processingPath === "aipoc") {
        finalJobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, analysis: effectiveAnalysis, user: getCurrentUser(), path: "aipoc" });
      } else {
        finalJobId = await submitFinishJob(analyzeJobId, selectedFile, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, user: getCurrentUser() });
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
        { label: "Step 5b：眼神修正", duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
        { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
      ]);
      addTiming("取得結果", Date.now() - stepStart);

      const extraInfo = result["extra-info"] || {};
      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : "本機處理";
      const usedAnalysisModelLabel = extraInfo.analysis_model
        ? (window.PhotoAgentConfig.analysisModels[extraInfo.analysis_model] || extraInfo.analysis_model)
        : window.PhotoAgentConfig.analysisModel;
      const usedModelLabel = isDebugUiActive
      ? `${usedAnalysisModelLabel} + ${usedImageModelLabel}`
      : "AI 智能修圖";

      const data = {
        image: imageUrl,
        model: usedModelLabel,
        usage: result.token_usage,
        qualityCheck: result.quality_check,
        localOnly: !aiPhotoEditing,
        retryCount: 0
      };

      // Skipped entirely if the user has since switched to viewing a
      // different record -- this branch renders straight to the DOM (not
      // through showSingleResult, which at least re-derives from whatever's
      // currently global), so applying it unconditionally would overwrite
      // that other record's canvas with this job's own result.
      if (isCurrent()) {
        // Same reset showSingleResult now does itself -- this branch renders
        // straight to the DOM instead of going through it, so it needs its
        // own copy: otherwise #batch-main-loading (left visible by
        // viewProcessingHistoryRecord on some *other* still-processing
        // history record) would stay stuck covering the screen instead of
        // revealing this actual result.
        const alreadyShowing = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
        if (!alreadyShowing) hideAllBatchMainViews();
        if (els.resultsCanvasRow) els.resultsCanvasRow.hidden = false;

        lastResult = { file: selectedFile, sourceUrl: selectedFileSourceUrl, resultText: result, jobId: finalJobId, runNumber: 1, path: processingPath };
        updateDownloadLinks();
        renderResult({ data, analysis: result.analysis, preset, imageModel: data.model });
        populateTimingSection();
        resetRunSwitcher();
        // Analysis-preview shows by default alongside the canvas (same as
        // showSingleResult/showBatchDoneMain -- this is the single-RUN direct
        // path, which renders the result itself instead of going through
        // showSingleResult).
        openInlineAnalysisForCurrentContext();
      }
    } else {
      // N independent RUNs from the same analysis, two-stage manual path:
      // each RUN is its own client-submitted job, run concurrently, and the
      // finished report mirrors the first one done (switchable via the RUN
      // tabs, same UI batch uses).
      // Captured once (see the useCombinedJob branch's own comment on this) --
      // runBatchPool itself is safe (its `items` param is this same array
      // reference, not re-read from the global), but the post-loop checks
      // below aren't unless they read myRuns instead of the (possibly
      // reassigned-by-then) global singleRuns.
      const myRuns = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing",
        imageUrl: null, result: null, timings: [], error: null
      }));
      singleRuns = myRuns;
      singleMirrorIndex = null;

      const updateRunProgress = () => refreshProcSubRotation();

      const actionId = generateActionId();
      await runBatchPool(myRuns, async (run) => {
        await runSingleItem(run, { analyzeJobId, effectiveAnalysis, userPrompt, overwritePrompt, presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, processingPath, analyzeTimings, actionId });
        doneCount += 1;
        updateRunProgress();
        // autoOpenPanel: false -- this fires per-RUN, not just once at the
        // end (see showSingleResult's own doc on this param). showSingleResult
        // preloads the image before rendering, so revealResultsIfNeeded below
        // (which flips away from the processing screen, the first time only)
        // never runs before the image is actually ready to show.
        if (run.status === "done" && isCurrent()) {
          await showSingleResult({ autoOpenPanel: false });
          revealResultsIfNeeded();
        }
      }, POLL_CONCURRENCY);

      if (!myRuns.some((r) => r.status === "done")) {
        throw new Error(myRuns.find((r) => r.error)?.error || "所有 RUN 皆失敗，請稍後再試。");
      }
      if (isCurrent()) await showSingleResult();
    }

    revealResultsIfNeeded();
  }

  // One RUN of steps 3-6 for the single-image flow's RUN-count > 1 path --
  // mirrors editBatchItem, but against selectedFile/singleFlowState instead
  // of a batch record.
  // Local-path stage 2. A normal run has an analyze job holding the uploaded
  // photo, so /continue can reuse it; an imported result folder has no job
  // behind it, so /rerun re-uploads the photo alongside the same analysis.
  // Everything the backend does from there is identical.
  function submitFinishJob(analyzeJobId, file, payload) {
    return analyzeJobId
      ? continueJob(analyzeJobId, payload)
      : rerunJob({ file, ...payload });
  }

  async function runSingleItem(run, { analyzeJobId, effectiveAnalysis, userPrompt, overwritePrompt, presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, processingPath, analyzeTimings, actionId }) {
    try {
      let stepStart = Date.now();

      if (processingPath === "aipoc") {
        run.jobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, analysis: effectiveAnalysis, user: getCurrentUser(), actionId, path: "aipoc" });
      } else {
        run.jobId = await submitFinishJob(analyzeJobId, selectedFile, { analysis: effectiveAnalysis, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, user: getCurrentUser(), actionId });
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
          { label: "Step 5b：眼神修正", duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
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
  // autoOpenPanel: false is for finishSingleFlow's own per-RUN progress
  // calls during a runCount>1 revise (see its call sites) -- the panel was
  // already closed when "Run" was clicked, so calling this with the default
  // (true) on an early RUN's completion would re-trigger a second,
  // overlapping backToProcessFromResults/finishSingleFlow session sharing
  // this same singleRuns/singleFlowState while the original submission is
  // still in flight, silently corrupting whichever one writes last. Every
  // OTHER caller (viewing a record from History, switching RUN tabs, or the
  // final call once every RUN has actually settled) wants the normal
  // default -- this must NOT be a shared/global suppression flag, since
  // showSingleResult is also how an entirely unrelated, already-finished
  // record gets its own settings panel shown when the user switches to view
  // it while some *other* record is still mid-revise in the background.
  async function showSingleResult({ autoOpenPanel = true, forceReopen = false, revealDetailExtra = false } = {}) {
    const runs = singleRuns || [];
    const pick = (singleMirrorIndex != null && runs[singleMirrorIndex])
      ? runs[singleMirrorIndex]
      : (runs.find((x) => x.status === "done") || runs[0]);
    if (!pick || pick.status !== "done") return;

    // Load the RUN's image into #generated-img itself while it's hidden,
    // before touching anything else -- whatever was previously on screen (an
    // earlier RUN, or the processing animation) stays up for the whole wait
    // instead of getting torn down and replaced by a partially-painted/
    // broken <img>. renderResult below re-sets the same src once this
    // resolves, which is a no-op once it's already loaded. If the user
    // switches RUN tabs (or a new record loads) while this is in flight,
    // `runs`/`pick` are stale by the time it resolves -- bail and let
    // whichever call actually reflects the current selection render instead.
    await setImgSrcPreloaded(els.generatedImg, pick.imageUrl);
    if (singleRuns !== runs) return;
    const stillPicked = (singleMirrorIndex != null && runs[singleMirrorIndex])
      ? runs[singleMirrorIndex]
      : (runs.find((x) => x.status === "done") || runs[0]);
    if (stillPicked !== pick) return;

    // Same "already showing" optimization as showBatchDoneMain (avoids
    // needlessly tearing down -- and losing -- an already-open inline
    // revise panel on every RUN-switch/completion re-render), and the same
    // reason for it: #batch-main-loading (shown by viewProcessingHistoryRecord
    // while a *different* history record is still processing) must never
    // stay stuck covering the screen once a real "done" result needs to
    // render here instead -- this function used to assume #results-canvas-row
    // was already visible, which was always true before the History page
    // existed but no longer is.
    const alreadyShowing = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
    if (!alreadyShowing) hideAllBatchMainViews();
    if (els.resultsCanvasRow) els.resultsCanvasRow.hidden = false;
    // revealDetailExtra: true only for viewHistoryRecord's admin-reports-drawer
    // call -- shows the prompts button for that one record regardless of
    // debug mode, same override resultsDetailExtra itself already gets.
    if (els.showPromptBtn) els.showPromptBtn.hidden = !(isDebugUiActive || revealDetailExtra);

    const { preset, aiPhotoEditing } = singleFlowState;
    const result = pick.result;
    const extraInfo = result["extra-info"] || {};
    const usedImageModelLabel = extraInfo.image_model
      ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
      : "本機處理";
    const usedAnalysisModelLabel = extraInfo.analysis_model
      ? (window.PhotoAgentConfig.analysisModels[extraInfo.analysis_model] || extraInfo.analysis_model)
      : window.PhotoAgentConfig.analysisModel;
    const usedModelLabel = isDebugUiActive
      ? `${usedAnalysisModelLabel} + ${usedImageModelLabel}`
      : "AI 智能修圖";

    const data = {
      image: pick.imageUrl,
      model: usedModelLabel,
      usage: result.token_usage,
      qualityCheck: result.quality_check,
      localOnly: !aiPhotoEditing,
      retryCount: 0
    };

    lastResult = { file: selectedFile, sourceUrl: selectedFileSourceUrl, resultText: result, jobId: pick.jobId, runNumber: pick.run, path: singleFlowState.processingPath, runs };
    updateDownloadLinks(runs);
    renderResult({ data, analysis: result.analysis, preset, imageModel: data.model });
    populateTimingSection(pick.timings);
    renderSingleRunSwitcher();
    // Analysis-preview shows by default alongside the canvas -- no-ops if
    // it's already open (e.g. just switching RUNs). See autoOpenPanel's own
    // doc above for when/why this is suppressed.
    if (autoOpenPanel) openInlineAnalysisForCurrentContext(forceReopen);
  }

  // "檢視分析結果": lets the user come back to the recipe checklist/style
  // options/prompt (restored from their last submission) and regenerate,
  // without leaving the results page -- the panel slides in beside the
  // photo canvas instead of navigating to the processing section.
  async function backToProcessFromResults() {
    // singleFlowState.analysis is briefly null for a combined job (skip-review
    // + local path) -- it's only filled in from the first done RUN's result
    // after the whole multi-RUN job resolves (see finishSingleFlow), but
    // showSingleResult() (and this auto-open) already fires once per RUN as
    // each one completes. Bail rather than handing renderAnalysisPreview a
    // null analysis -- the final showSingleResult() call, once analysis is
    // actually set, opens it for real.
    if (!singleFlowState || !singleFlowState.analysis) return;
    hideError();
    clearInlineAnalysisWait();
    if (timingDetails) timingDetails.hidden = true;
    resetSteps(); // clears completedTimings so this run's timings don't append to the previous run's

    renderAnalysisPreview({ analysis: stripDisabledTopicItems(singleFlowState.analysis, singleFlowState.advancedOptions), preset: singleFlowState.preset });
    if (lastSingleSelections) {
      if (els.promptInput) els.promptInput.value = lastSingleSelections.userPrompt || "";
      if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = !!lastSingleSelections.overwritePrompt;
      applyStyleSelections(lastSingleSelections);
    }
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    openInlineAnalysisPanel();
    captureInlineReviseSnapshot();

    const action = await waitForInlineAnalysisAction();
    closeInlineAnalysisPanel();
    if (action === "cancel") return;

    setAnalysisPreviewDisabled(true);
    if (els.showPromptBtn) els.showPromptBtn.disabled = true;
    // Same thinking-rings animation as the initial upload-flow processing
    // screen (#processing-section's .proc-visual), replacing the photo
    // (hidden outright, not dimmed underneath) instead of navigating away
    // from the results page. originalImgThumbUrl mirrors whichever full-res
    // source #original-img is currently showing (a fresh upload's own
    // previewThumbUrl, or sourceThumbnailUrl(jobId) for a history revisit) --
    // falls back to the full-res src itself only if that thumbnail somehow
    // never got set.
    if (els.canvasLoadingThumb) els.canvasLoadingThumb.src = originalImgThumbUrl || els.originalImg.src;
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = true;
    if (els.canvasLoading) els.canvasLoading.hidden = false;
    try {
      await finishSingleFlow();
    } catch (error) {
      showError(friendlyError(error));
    } finally {
      // finishSingleFlow's own revealResultsIfNeeded already flips these back
      // as soon as RUN 1 lands, so this is only load-bearing when every RUN
      // failed (revealResultsIfNeeded never ran) -- restoring the frame lets
      // the user see the error against last known image instead of being
      // stuck behind the loading card. Harmless no-op otherwise, and still
      // harmless even if the user has since switched to a different record
      // (viewHistoryRecord/viewProcessingHistoryRecord reset these same two
      // elements themselves whenever they show anything) -- either way, the
      // end state is "loading hidden, frame visible", which is always
      // correct for whatever is currently on screen. Only actually plays a
      // fast-forward here in that same all-RUNs-failed case -- the success
      // path already consumed it via revealResultsIfNeeded, so this resolves
      // instantly then.
      await finishProcSubRotation();
      if (els.canvasLoading) els.canvasLoading.hidden = true;
      if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
      if (els.showPromptBtn) els.showPromptBtn.disabled = false;
      stopProcSubRotation();
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
      analysisModel: els.analysisModelSelect?.value || window.PhotoAgentConfig.defaultAnalysisModel || "gpt-4.1-mini",
      aspectRatio: els.imageAspectRatioSelect?.value || window.PhotoAgentConfig.defaultAspectRatio || "auto",
      aiPhotoEditing: els.aiEditToggle.checked,
      runQa: els.runQaToggle ? els.runQaToggle.checked : false,
      advancedOptions: readAdvancedOptionsFromDom(),
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

    els.batchSidebar.hidden = false;
    if (els.resultsBody) els.resultsBody.classList.add("batch-mode");
    currentBatchRecord = batchRecords[0] || null;
    userPickedBatchRecord = false;
    // Before rendering: renderBatchMain measures the sidebar's real on-page
    // position (updateBatchSidebarMaxHeight) to cap its height, which reads
    // as all-zero while the section is still display:none.
    showSection("results");
    renderBatchSidebarList();
    renderBatchMain(currentBatchRecord); // shows record 1's "analyzing" placeholder immediately
    updateBatchDownloadAllVisibility();

    const analyzeArgs = {
      files: batchRecords.map((r) => r.file),
      preset: presetKey,
      imageQuality: batchSettings.imageQuality,
      imageModel: batchSettings.imageModel,
      analysisModel: batchSettings.analysisModel,
      aspectRatio: batchSettings.aspectRatio,
      aiPhotoEditing: batchSettings.aiPhotoEditing,
      advancedOptions: batchSettings.advancedOptions
    };

    try {
      if (skipReview && batchSettings.path === "local") {
        // Batch-auto + local path: analyze once and edit all RUNs, per
        // image, inside one combined job (runCombinedMultiRun) -- skips the
        // separate analyzeJobBatch/analyzeBatchItem phase below entirely, since
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
          updateBatchDownloadAllVisibility();
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
          updateBatchDownloadAllVisibility();
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

      updateBatchDownloadAllVisibility();
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

  // Batch-auto (skip-review) + local path: skips the separate
  // analyzeJobBatch/analyzeBatchItem phase entirely, since batch-auto never
  // pauses at the sidebar for the user to review/adjust between RUNs anyway.
  // Each image's analysis + all its RUNs happen server-side within ONE job
  // (runCombinedMultiRun) -- every image's job runs fully independently of
  // every other image's, with no client-side concurrency limiter needed:
  // the actual generation work happens on the backend's own worker pool, not
  // anything this tab orchestrates, so a phone can sleep for the whole
  // batch's duration and every image's every RUN still finishes.
  async function runCombinedBatchAuto() {
    const runCount = Math.max(1, parseInt(els.runCountSelect?.value, 10) || 1);
    batchRecords.forEach((r) => {
      r.runs = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing", imageUrl: null, result: null, timings: [], error: null
      }));
      r.mirrorIndex = null;
    });

    // Unlike every other batch phase in this file, this used to fire every
    // record's combined analyze+edit job at once via Promise.all -- with no
    // submission throttling, a large batch queues far more jobs than the
    // server's fixed worker pool can run concurrently (5 worker threads, see
    // server.py's ENGINE_WORKER_NUM), so later images sit queued long enough
    // to blow past pollJobStatus's own timeout budget before a worker even
    // picks them up. Routing through the same runBatchPool/POLL_CONCURRENCY
    // used elsewhere keeps queue depth bounded to what the server can
    // actually work through concurrently.
    await runBatchPool(batchRecords, (record) => analyzeAndEditBatchRecord(record, runCount), POLL_CONCURRENCY);

    updateBatchDownloadAllVisibility();
    updateBatchHeaderControls();
  }

  // One image's full multi-RUN generation for batch-auto -- analysis + all
  // `runCount` RUNs happen server-side within ONE job (runCombinedMultiRun).
  // Only used for a batch record's very first edit under skip-review + local
  // path; per-image revise (startInlineBatchReview -> editBatchItem) always
  // uses the normal continueJob path, reusing record.analysis/record.analyzeJobId
  // populated below once the first RUN lands.
  async function analyzeAndEditBatchRecord(record, runCount) {
    const userPrompt = effectivePrompt(record);
    const overwritePrompt = effectiveOverwritePrompt(record);
    const stepStart = Date.now();
    try {
      await runCombinedMultiRun({
        file: record.file,
        preset: batchSettings.presetKey,
        imageQuality: batchSettings.imageQuality,
        imageModel: batchSettings.imageModel,
        analysisModel: batchSettings.analysisModel,
        aspectRatio: batchSettings.aspectRatio,
        aiPhotoEditing: batchSettings.aiPhotoEditing,
        runQa: batchSettings.runQa,
        advancedOptions: batchSettings.advancedOptions,
        userPrompt,
        overwritePrompt,
        runCount,
        onStage: (stage) => {
          // The whole job flips from "analyzing" to "processing" once it
          // reaches the edit stage, regardless of runCount.
          if (stage === "edit" && record.status === "analyzing") {
            record.status = "processing";
            updateBatchSidebarItem(record);
            refreshIfSelected(record);
            updateBatchDownloadAllVisibility();
          }
        },
        onRunDone: (runData) => {
          const run = record.runs[runData.run - 1];
          if (!run) return;
          run.jobId = runData.jobId;
          run.status = "done";
          run.imageUrl = runData.image_url;
          run.result = runData.result;
          const t = (runData.result["extra-info"] || {}).timings || {};
          run.timings = [
            { label: "分析並修圖", duration: Date.now() - stepStart, children: [
              { label: "Step 2：照片分析", duration: secToMs(t["Step 2 - photo_analysis"]) },
              { label: "Step 3：使用者指令審查", duration: secToMs(t["Step 3 - user_instruction_review"]) },
              { label: "Step 4：裁切驗證", duration: secToMs(t["Step 4 - crop_validation"]) },
              { label: "Step 5：修圖執行", duration: secToMs(t["Step 5 - image_edit"]) },
              { label: "Step 5b：眼神修正", duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
              { label: "Step 6：品質檢查", duration: secToMs(t["Step 6 - quality_check"]) }
            ] }
          ];
          // Remember the first successfully-done RUN's analysis + job id so
          // a later per-image revise reuses the normal two-stage continueJob
          // path instead of needing its own combined submission.
          if (!record.analysis) {
            record.analysis = runData.result.analysis;
            record.analyzeJobId = runData.jobId;
          }
          syncRecordMirror(record);
          updateBatchSidebarItem(record);
          updateBatchDownloadAllVisibility();
          refreshIfSelected(record);
        }
      });
    } catch (error) {
      // The job itself failed (e.g. analyze_stage threw) -- whichever RUNs
      // never got an onRunDone callback can't proceed, since there's no
      // shared analysis for them to have used anyway.
      record.runs.forEach((run) => {
        if (run.status !== "done") { run.status = "error"; run.error = friendlyError(error); }
      });
      syncRecordMirror(record);
      updateBatchSidebarItem(record);
      updateBatchDownloadAllVisibility();
      refreshIfSelected(record);
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
    updateBatchSidebarItem(record);
    refreshIfSelected(record);
    updateBatchDownloadAllVisibility();
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

  // "取代" checkbox only exists beside the per-image 補充指令 field, not the
  // batch-shared one -- so it only ever applies when the image's own prompt
  // (set during its review) is what's actually being sent.
  function effectiveOverwritePrompt(record) {
    const own = (record.userPrompt || "").trim();
    return own ? !!record.overwritePrompt : false;
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

  // Effective per-image 模型/畫質 + 長寬比: this case's own pick (set only
  // once its revise panel is used to change one) if it has one, else the
  // batch-level default -- same override rule as effectiveOptions above.
  function effectiveModelQuality(record) {
    if (record.imageModel && record.imageQuality) {
      return { model: record.imageModel, quality: record.imageQuality };
    }
    return { model: batchSettings.imageModel, quality: batchSettings.imageQuality };
  }

  // Same override rule as effectiveModelQuality above, for the analysis
  // model (Steps 2/3/6) instead of the edit model (Step 5).
  function effectiveAnalysisModel(record) {
    return record.analysisModel || batchSettings.analysisModel;
  }

  function effectiveAspectRatio(record) {
    return record.aspectRatio || batchSettings.aspectRatio;
  }

  function effectiveAdvancedOptions(record) {
    return record.advancedOptions || batchSettings.advancedOptions;
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
    updateBatchDownloadAllVisibility();
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
  async function editBatchItem({ record, run, actionId }) {
    try {
      const userPrompt = effectivePrompt(record);
      const overwritePrompt = effectiveOverwritePrompt(record);
      const effectiveAnalysis = buildEffectiveAnalysis(record.analysis, {
        options: effectiveOptions(record),
        recipeChecked: record.recipeChecked,
        advancedOptions: effectiveAdvancedOptions(record)
      });
      const modelQuality = effectiveModelQuality(record);
      const analysisModel = effectiveAnalysisModel(record);
      const aspectRatio = effectiveAspectRatio(record);
      let stepStart = Date.now();
      if (batchSettings.path === "aipoc") {
        run.jobId = await submitJob({
          file: record.file,
          preset: batchSettings.presetKey,
          imageQuality: modelQuality.quality,
          imageModel: modelQuality.model,
          analysisModel,
          aspectRatio,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          runQa: batchSettings.runQa,
          userPrompt,
          overwritePrompt,
          analysis: effectiveAnalysis,
          user: getCurrentUser(),
          actionId,
          path: "aipoc"
        });
      } else {
        run.jobId = await submitFinishJob(record.analyzeJobId, record.file, {
          analysis: effectiveAnalysis,
          preset: batchSettings.presetKey,
          imageQuality: modelQuality.quality,
          imageModel: modelQuality.model,
          analysisModel,
          aspectRatio,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          runQa: batchSettings.runQa,
          userPrompt,
          overwritePrompt,
          user: getCurrentUser(),
          actionId
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
          { label: "Step 5b：眼神修正", duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
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
    updateBatchDownloadAllVisibility();
    refreshIfSelected(record);
  }

  function syncRecordMirror(record) {
    const runs = record.runs || [];
    if (!runs.length) return;
    const pick = (record.mirrorIndex != null && runs[record.mirrorIndex])
      ? runs[record.mirrorIndex]
      : (runs.find((x) => x.status === "done") || runs[0]);
    record.jobId = pick.jobId;
    record.runNumber = pick.run;
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

  async function buildAllBatchResultsZip(onProgress) {
    const records = doneBatchRecordsWithResult();
    const runsPerRecord = records.map((r) => batchRecordRuns(r).filter((run) => run.status === "done"));
    // Aggregate across every image's RUNs, not just one image's -- each
    // image's fetches run in parallel, so this is a shared counter rather
    // than a per-image local count.
    const total = runsPerRecord.reduce((sum, runs) => sum + runs.length, 0);
    let done = 0;
    const tick = () => {
      done += 1;
      if (onProgress) onProgress(done, total);
    };

    // mapWithConcurrency (not a bare Promise.all) -- same reasoning as
    // buildSelectedHistoryZip's own: a large batch firing every image's
    // fetches at once can overwhelm the browser's connection/TLS handling
    // (net::ERR_HTTP2_PROTOCOL_ERROR, net::ERR_CERT_VERIFIER_CHANGED),
    // failing the whole zip over what's really just too much concurrency.
    const groups = await mapWithConcurrency(records, 3, (r, i) =>
      buildImageFiles(
        { file: r.file, path: batchSettings.path, prefix: `image ${i + 1}/` },
        runsPerRecord[i],
        tick
      )
    );
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
    if (els.batchSidebarTitle) els.batchSidebarTitle.textContent = `我的照片（${batchRecords.length}）`;
    if (els.batchDrawerTriggerCount) {
      els.batchDrawerTriggerCount.textContent = String(batchRecords.length);
      els.batchDrawerTriggerCount.hidden = batchRecords.length === 0;
    }
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
      if (runsRunning) badge.textContent = `處理中 (${runsDone}/${runs.length})`;
      else if (runsDone) badge.textContent = `完成 (${runsDone}/${runs.length})`;
    }

    // Unlike the old grid (badge overlaid on a large square thumbnail), the
    // sidebar's thumbnail is small -- the badge sits in its own column
    // beside the thumbnail instead of inside it, where there's enough width
    // for the text. No filename shown here (or in the upload-page multi-file
    // thumbnail strip) -- the thumbnail image itself is enough to tell
    // images apart.
    const textCol = document.createElement("div");
    textCol.className = "batch-sidebar-text";
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

  // Generic in-app notice popup -- same open/close shape as the error modal
  // above, styled with the PhotoMaster wordmark instead since it's not
  // reporting a failure (e.g. the inline revise panel's Run button when
  // nothing was actually changed).
  function showNoticeModal(message) {
    if (!els.noticeModal) return;
    if (els.noticeModalMsg) els.noticeModalMsg.textContent = message;
    els.noticeModal.hidden = false;
  }

  function closeNoticeModal() {
    if (els.noticeModal) els.noticeModal.hidden = true;
  }

  // App-styled stand-in for window.confirm() -- same backdrop+box shape as
  // the modals above, resolving true/false instead of blocking the thread.
  // Backdrop-click/Escape (wired in the shared modal-dismiss block below)
  // resolve false, same as Cancel.
  let confirmModalHandlers = null;

  function clearConfirmModalWait() {
    if (!confirmModalHandlers) return;
    els.confirmModalOkBtn?.removeEventListener("click", confirmModalHandlers.ok);
    els.confirmModalCancelBtn?.removeEventListener("click", confirmModalHandlers.cancel);
    confirmModalHandlers = null;
  }

  // confirmLabel/danger default to the original delete-history-entry wording
  // (btn-danger red "刪除") -- callers for a non-destructive confirmation
  // (e.g. handleLogout) override both so the button doesn't misleadingly
  // read "刪除" for an action that isn't deleting anything.
  function showConfirmModal(message, { confirmLabel = "刪除", danger = true } = {}) {
    return new Promise((resolve) => {
      if (!els.confirmModal) {
        resolve(window.confirm(message));
        return;
      }
      clearConfirmModalWait(); // drop any stale listener from an abandoned prior prompt
      if (els.confirmModalMsg) els.confirmModalMsg.textContent = message;
      if (els.confirmModalOkBtn) {
        els.confirmModalOkBtn.textContent = confirmLabel;
        els.confirmModalOkBtn.classList.toggle("btn-danger", danger);
        els.confirmModalOkBtn.classList.toggle("btn-secondary", !danger);
      }
      const finish = (result) => {
        clearConfirmModalWait();
        els.confirmModal.hidden = true;
        resolve(result);
      };
      confirmModalHandlers = { ok: () => finish(true), cancel: () => finish(false) };
      els.confirmModalOkBtn?.addEventListener("click", confirmModalHandlers.ok);
      els.confirmModalCancelBtn?.addEventListener("click", confirmModalHandlers.cancel);
      els.confirmModal.hidden = false;
    });
  }

  // Account creation prompt -- shown at startup whenever ?user_id= is absent
  // (see the bootstrap call near applyDebugUiVisibility above). Skipping just
  // closes it; the app stays fully usable without an account, just without
  // processing history being recorded/recalled.
  function openAccountModal() {
    if (els.accountModal) els.accountModal.hidden = false;
  }

  function closeAccountModal() {
    if (els.accountModal) els.accountModal.hidden = true;
    if (els.accountModalError) els.accountModalError.hidden = true;
    if (els.accountModalInput) els.accountModalInput.value = "";
  }

  function showAccountModalError(message) {
    if (!els.accountModalError) return;
    els.accountModalError.textContent = message;
    els.accountModalError.hidden = false;
  }

  // Reflects the current account (if any) into the header badge, which also
  // doubles as the history-drawer open button (see its click listener below)
  // -- stays hidden until an account exists. #register-account-btn is the
  // mirror image of it: the only way back to #account-modal once its initial
  // prompt has been skipped, so it shows exactly when the badge is hidden
  // (no account yet) and hides once one exists.
  function applyAccountUi() {
    const user = getCurrentUser();
    if (els.accountBadge) {
      // .textContent (not on accountBadge itself) so the clock icon markup
      // beside #account-badge-label survives every call to this.
      if (els.accountBadgeLabel) els.accountBadgeLabel.textContent = user || "";
      els.accountBadge.hidden = !user;
      els.accountBadge.title = user ? "查看處理紀錄" : "";
      els.accountBadge.setAttribute("aria-label", user ? `${user}｜查看處理紀錄` : "");
    }
    if (els.registerAccountBtn) els.registerAccountBtn.hidden = !!user;
  }

  async function handleCreateAccount() {
    const name = normalizeUsername(els.accountModalInput ? els.accountModalInput.value : "");
    if (!name) {
      showAccountModalError("請輸入使用者名稱。");
      return;
    }
    if (els.accountModalCreateBtn) els.accountModalCreateBtn.disabled = true;
    try {
      await createAccount(name);
      setCurrentUser(name);
      applyAccountUi();
      checkAdminAccess();
      closeAccountModal();
    } catch (err) {
      showAccountModalError(err.message || "建立帳號失敗，請再試一次。");
    } finally {
      if (els.accountModalCreateBtn) els.accountModalCreateBtn.disabled = false;
    }
  }

  // Renders one row per past job returned by getHistory -- fires a one-shot
  // status check per job (getJobStatusOnce) in parallel so each row's
  // thumbnail/badge reflects its actual current state, same "success"/
  // "error"/still-processing states the batch sidebar already shows
  // per-image, just re-derived here since this list survives page reloads
  // (unlike the in-memory batch records that sidebar reads from).
  // "今天"/"昨天" for the two most recent calendar days (local time), else
  // "M月D日" -- jobs arrive newest-first (ts desc, see get_history/
  // account.py), so consecutive entries sharing this label naturally cluster
  // into one slot without needing to re-sort anything.
  function formatHistoryDayLabel(ts) {
    const d = new Date(ts * 1000);
    const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const diffDays = Math.round((startOfDay(new Date()) - startOfDay(d)) / 86400000);
    if (diffDays === 0) return "今天";
    if (diffDays === 1) return "昨天";
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }

  // Removes one tile from the DOM after a successful delete -- also drops
  // its now-possibly-empty day-group heading, and shows the empty state if
  // that was the last entry left, without needing to re-fetch the whole list.
  function removeHistoryCubeFromDom(li) {
    const grid = li.parentElement;
    li.remove();
    if (grid && !grid.children.length) grid.parentElement?.remove(); // the day-group
    if (els.historyList && els.historyEmpty) {
      const empty = els.historyList.children.length === 0;
      els.historyEmpty.hidden = !empty;
      if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = empty;
      if (els.historySelectBtn) els.historySelectBtn.hidden = empty;
    }
  }

  async function handleDeleteHistoryEntry(job, li) {
    const user = getCurrentUser();
    if (!user || !job.image_key) return;
    const confirmed = await showConfirmModal("確定要刪除這筆處理紀錄嗎？");
    if (!confirmed) return;
    try {
      await deleteHistoryEntry(user, job.image_key);
      removeHistoryCubeFromDom(li);
      historyJobsByKey.delete(job.image_key);
      historyTileElsByKey.delete(job.image_key);
      // Nothing left to browse -- back out to a fresh upload screen instead
      // of leaving the drawer open over whatever page happened to be
      // showing behind it once its last entry is gone.
      if (els.historyList && !els.historyList.children.length) {
        closeHistoryDrawer();
        resetApp();
      }
    } catch (err) {
      showNoticeModal(err.message || "刪除紀錄失敗，請稍後再試。");
    }
  }

  // One request (clearHistory) instead of looping handleDeleteHistoryEntry
  // per tile -- always leaves the list empty afterward, so this can go
  // straight to the same "nothing left to browse" reset that
  // handleDeleteHistoryEntry only reaches conditionally.
  async function handleClearAllHistory() {
    const user = getCurrentUser();
    if (!user) return;
    const confirmed = await showConfirmModal("確定要刪除所有處理紀錄嗎？此操作無法復原。");
    if (!confirmed) return;
    try {
      await clearHistory(user);
      if (els.historyList) els.historyList.innerHTML = "";
      if (els.historyEmpty) els.historyEmpty.hidden = false;
      if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = true;
      closeHistoryDrawer();
      resetApp();
    } catch (err) {
      showNoticeModal(err.message || "刪除所有紀錄失敗，請稍後再試。");
    }
  }

  // A full reload (rather than manually resetting every module-level piece
  // of state -- batchRecords, singleFlowState, lastResult, ...) is the
  // simplest way to guarantee none of it survives into the logged-out UI,
  // same reasoning as clearCurrentUser only owning the in-memory identity
  // itself, not the rest of the app's state (account.js). Has no effect if
  // the page was linked in with ?user_id= -- that identity is re-resolved
  // from the URL on every load, same as before logout.
  async function handleLogout() {
    const confirmed = await showConfirmModal(
      "登出後，這個瀏覽器將不再記得目前的使用者名稱，需要重新輸入才能再次查看處理紀錄。確定要登出嗎？",
      { confirmLabel: "登出", danger: false }
    );
    if (!confirmed) return;
    clearCurrentUser();
    window.location.reload();
  }

  // Multi-select download state for the history drawer -- module-level
  // like demoSelectedNames (the demo-image picker's own Set-based
  // selection), reset on every fresh render/drawer close rather than
  // surviving across them (see exitHistorySelectMode). Keyed by
  // job.image_key, NOT job.job_id -- a "continue"/multi-run action can
  // produce several history entries that all point back at the same
  // underlying job_id (handleDeleteHistoryEntry already relies on
  // image_key, not job_id, to identify one entry for exactly this reason).
  // Keying this Set by job_id let two different tiles collide on the same
  // key, so selecting tile A then clicking tile B looked like it silently
  // deselected A (really: B's toggle saw the shared key already in the Set
  // and deleted it). historyJobsByKey maps image_key -> the job object
  // itself, populated only for finished (.selectable) tiles, since only
  // those have anything to download.
  let historySelectMode = false;
  const selectedHistoryKeys = new Set();
  const historyJobsByKey = new Map();
  // image_key -> that tile's <li>, so a bulk delete (handleDeleteSelectedHistoryClick)
  // can remove exactly the right DOM nodes without having to re-query for
  // them -- populated/cleared alongside historyJobsByKey.
  const historyTileElsByKey = new Map();

  function updateHistorySelectionButtons() {
    const count = selectedHistoryKeys.size;
    if (els.historyDownloadSelectedBtn) {
      els.historyDownloadSelectedBtn.disabled = count === 0;
      const dlHint = count > 0 ? `下載所選（${count} 張）` : "下載所選";
      els.historyDownloadSelectedBtn.title = dlHint;
      els.historyDownloadSelectedBtn.setAttribute("aria-label", dlHint);
    }
    if (els.historyDownloadSelectedCount) els.historyDownloadSelectedCount.textContent = count > 0 ? String(count) : "";
    if (els.historyDeleteSelectedBtn) {
      els.historyDeleteSelectedBtn.disabled = count === 0;
      const delHint = count > 0 ? `刪除所選（${count} 張）` : "刪除所選";
      els.historyDeleteSelectedBtn.title = delHint;
      els.historyDeleteSelectedBtn.setAttribute("aria-label", delHint);
    }
  }

  // Marks one tile as eligible for multi-select (done, failed, or
  // "couldn't fetch status" -- anything with an image_key to act on)
  // and registers it in the two by-key lookups the bulk download/delete
  // handlers read from. job.job_id is NOT used as the key here -- it's not
  // guaranteed unique per history entry (see selectedHistoryKeys' own doc);
  // a job with no image_key at all (very old entries) is simply left
  // unmarked -- there'd be nothing to look it back up by anyway.
  function markHistoryTileSelectable(li, job) {
    if (!job.image_key) return;
    li.classList.add("selectable");
    historyJobsByKey.set(job.image_key, job);
    historyTileElsByKey.set(job.image_key, li);
  }

  function toggleHistorySelection(imageKey, li) {
    if (selectedHistoryKeys.has(imageKey)) {
      selectedHistoryKeys.delete(imageKey);
      li.classList.remove("selected");
    } else {
      selectedHistoryKeys.add(imageKey);
      li.classList.add("selected");
    }
    updateHistorySelectionButtons();
  }

  function enterHistorySelectMode() {
    if (!els.historyList) return;
    historySelectMode = true;
    els.historyList.classList.add("select-mode");
    if (els.historySelectBtn) els.historySelectBtn.hidden = true;
    if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = true;
    if (els.historySelectCancelBtn) els.historySelectCancelBtn.hidden = false;
    if (els.historyDownloadSelectedBtn) els.historyDownloadSelectedBtn.hidden = false;
    if (els.historyDeleteSelectedBtn) els.historyDeleteSelectedBtn.hidden = false;
    updateHistorySelectionButtons();
  }

  // Also the reset path used by a fresh renderHistoryList and by
  // closeHistoryDrawer -- both cases want select mode (and whatever was
  // checked) to not carry over, same reasoning as demoSelectedNames.clear()
  // on the demo-image modal's own close.
  function exitHistorySelectMode() {
    historySelectMode = false;
    selectedHistoryKeys.clear();
    const hasItems = !!(els.historyList && els.historyList.children.length);
    if (els.historyList) {
      els.historyList.classList.remove("select-mode");
      els.historyList.querySelectorAll(".history-cube.selected").forEach((li) => li.classList.remove("selected"));
    }
    if (els.historySelectCancelBtn) els.historySelectCancelBtn.hidden = true;
    if (els.historyDownloadSelectedBtn) els.historyDownloadSelectedBtn.hidden = true;
    if (els.historyDeleteSelectedBtn) els.historyDeleteSelectedBtn.hidden = true;
    // Restored here (not just left as renderHistoryList's own initial
    // hidden=jobs.length===0) so cancelling mid-selection -- without a
    // fresh render -- also brings these back instead of leaving the header
    // stuck with neither button pair visible.
    if (els.historySelectBtn) els.historySelectBtn.hidden = !hasItems;
    if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = !hasItems;
  }

  // Runs `fn` over `items` with at most `limit` in flight at once, instead
  // of firing every item's work simultaneously via a bare Promise.all --
  // buildSelectedHistoryZip's whole-selection fetches (unlike the single-
  // image download paths above) can multiply into dozens of concurrent
  // requests to the same origin once several jobs are selected, which is
  // what was tripping a transient net::ERR_HTTP2_PROTOCOL_ERROR under load.
  // Order of `results` matches `items`, regardless of finish order.
  async function mapWithConcurrency(items, limit, fn) {
    const results = new Array(items.length);
    let next = 0;
    async function worker() {
      while (next < items.length) {
        const i = next++;
        results[i] = await fn(items[i], i);
      }
    }
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    return results;
  }

  // History counterpart of buildAllBatchResultsZip: bundles every selected
  // job's done runs into one zip, each under its own "image N/" folder.
  // Reuses fetchAllHistoryRuns (the same per-job_id fetch viewHistoryRecord
  // itself calls) to get each job's flattened, run-numbered result list,
  // rather than duplicating that fetch-and-flatten logic here.
  async function buildSelectedHistoryZip(onProgress) {
    const jobs = Array.from(selectedHistoryKeys).map((key) => historyJobsByKey.get(key)).filter(Boolean);
    const CONCURRENCY = 3;
    const runsPerJob = await mapWithConcurrency(jobs, CONCURRENCY, (job) => fetchAllHistoryRuns(job).catch(() => []));
    const total = runsPerJob.reduce((sum, runs) => sum + runs.length, 0);
    let done = 0;
    const tick = () => {
      done += 1;
      if (onProgress) onProgress(done, total);
    };
    const groups = await mapWithConcurrency(jobs, CONCURRENCY, (job, i) => {
      const runs = runsPerJob[i];
      // sourceImageUrl is local-path only (see its own doc, photo-agent-api.js)
      // -- an AIPoC-path job's zip entry simply omits the source photo, same
      // fallback fetchImageDownloadData already applies to a live
      // viewHistoryRecord download.
      const sourceUrl = job.path === "local" && runs.length ? sourceImageUrl(runs[0].jobId) : null;
      return buildImageFiles({ file: null, sourceUrl, path: job.path, prefix: `image ${i + 1}/` }, runs, tick);
    });
    return window.PhotoAgentZip.createZip(groups.flat());
  }

  function handleDownloadSelectedHistoryClick() {
    if (!selectedHistoryKeys.size) return;
    downloadZip(buildSelectedHistoryZip, buildDownloadZipFilename());
  }

  // Bulk counterpart of handleDeleteHistoryEntry -- there's no batch delete
  // endpoint (deleteHistoryEntry only ever takes one image_key), so this
  // just fires one DELETE per selected entry in parallel and reconciles
  // whichever ones actually succeeded, rather than failing the whole
  // operation if one entry errors.
  async function handleDeleteSelectedHistoryClick() {
    const keys = Array.from(selectedHistoryKeys);
    if (!keys.length) return;
    const user = getCurrentUser();
    if (!user) return;
    const confirmed = await showConfirmModal(`確定要刪除這 ${keys.length} 筆處理紀錄嗎？`);
    if (!confirmed) return;
    if (els.historyDeleteSelectedBtn) els.historyDeleteSelectedBtn.disabled = true;
    const results = await Promise.allSettled(keys.map((key) => deleteHistoryEntry(user, key)));
    let anyFailed = false;
    keys.forEach((key, i) => {
      if (results[i].status !== "fulfilled") {
        anyFailed = true;
        return;
      }
      const li = historyTileElsByKey.get(key);
      if (li) removeHistoryCubeFromDom(li);
      historyJobsByKey.delete(key);
      historyTileElsByKey.delete(key);
    });
    exitHistorySelectMode();
    if (anyFailed) showNoticeModal("部分紀錄刪除失敗，請稍後再試。");
    // Same "nothing left to browse" reset as handleDeleteHistoryEntry/
    // handleClearAllHistory once the last entry is gone.
    if (els.historyList && !els.historyList.children.length) {
      closeHistoryDrawer();
      resetApp();
    }
  }

  async function renderHistoryList(jobs) {
    if (!els.historyList) return;
    exitHistorySelectMode();
    historyJobsByKey.clear();
    historyTileElsByKey.clear();
    els.historyList.innerHTML = "";
    if (els.historyEmpty) els.historyEmpty.hidden = jobs.length > 0;
    if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = jobs.length === 0;
    if (els.historySelectBtn) els.historySelectBtn.hidden = jobs.length === 0;
    if (!jobs.length) return;

    // One heading + grid per calendar day, cube tiles instead of full-width
    // rows -- jobs is already grouped by construction (see this function's
    // own doc above), so a fresh group starts only when the label changes.
    let currentLabel = null;
    let currentGrid = null;
    const rows = jobs.map((job) => {
      const label = formatHistoryDayLabel(job.ts);
      if (label !== currentLabel) {
        currentLabel = label;
        const group = document.createElement("div");
        group.className = "history-day-group";
        const heading = document.createElement("h3");
        heading.className = "history-day-heading";
        heading.textContent = label;
        group.appendChild(heading);
        currentGrid = document.createElement("ul");
        currentGrid.className = "history-grid";
        group.appendChild(currentGrid);
        els.historyList.appendChild(group);
      }

      const li = document.createElement("li");
      // history-cube layers the square/grid-cell layout on top of
      // batch-sidebar-item's existing status-driven styling (done/error/
      // processing colors, dimming) -- reused as-is rather than duplicated.
      li.className = "history-cube batch-sidebar-item processing";
      // Local-path jobs keep their srcKeys forever (see account.py), so the
      // original photo is available immediately -- no need to wait for the
      // status check below just to show *something* here. AIPoC-path jobs
      // have no recoverable original (same reason the before/after compare
      // skips them elsewhere), so those stay spinner-only. The .processing
      // class already dims .rc-media img (see styles.css), so this reads as
      // "not final yet" rather than the actual done result.
      const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
      const referenceThumb = job.path === "local" ? `<img src="${sourceThumbnailUrl(jobIds[0])}" alt="">` : "";
      li.innerHTML = `
        <div class="rc-media">${referenceThumb}<div class="rc-spinner"></div></div>
        <span class="rc-badge">處理中</span>
        <button type="button" class="history-cube-remove" aria-label="刪除紀錄">×</button>
        <span class="history-cube-check">
          <svg width="12" height="12" fill="none" viewBox="0 0 24 24">
            <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </span>
      `;
      currentGrid.appendChild(li);
      const removeBtn = li.querySelector(".history-cube-remove");
      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          // Stop this from also bubbling into the tile's own click handler
          // (viewHistoryRecord/viewProcessingHistoryRecord), attached below
          // once the status check resolves.
          e.stopPropagation();
          handleDeleteHistoryEntry(job, li);
        });
      }
      return { job, li };
    });

    await Promise.all(rows.map(async ({ job, li }) => {
      const badge = li.querySelector(".rc-badge");
      const media = li.querySelector(".rc-media");
      // job.runs holds every job_id from the same action (see
      // backend/services/account.py's action_id grouping) -- job.job_id is
      // the fallback for an entry recorded before `runs` existed.
      const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
      try {
        const statuses = await Promise.all(
          jobIds.map((id) => getJobStatusOnce(id, { path: job.path }).catch(() => ({ status: "error" })))
        );
        const doneIdx = statuses.findIndex((s) => s.status === "success");
        if (doneIdx !== -1) {
          li.className = "history-cube batch-sidebar-item done";
          markHistoryTileSelectable(li, job);
          const doneCount = statuses.filter((s) => s.status === "success").length;
          if (badge) badge.textContent = jobIds.length > 1 ? `完成（${doneCount}/${jobIds.length}）` : "完成";
          // Thumbnail route only exists for the local path (see
          // photo-agent-api.js's resultThumbnailUrl) -- AIPoC-path jobs fall
          // back to the full-res image, same as before this change.
          const doneThumbSrc = job.path === "local"
            ? resultThumbnailUrl(jobIds[doneIdx])
            : resultImageUrl(jobIds[doneIdx], { path: job.path });
          if (media) media.innerHTML = `<img src="${doneThumbSrc}" alt="">`;
          // viewHistoryRecord fetches the full record and preloads its result
          // image before showing anything (see its own/showSingleResult's
          // preload guard) -- without feedback here, the tap on this tile
          // reads as dead until the results page suddenly appears. .opening
          // reuses the same dim treatment as .processing (see the CSS rule
          // above) without touching the "done" class/badge itself. Also
          // shows #run-switch-loading over the main canvas, same as
          // selectBatchRecord/selectAdjacentRun -- the history drawer opens
          // over whatever's already showing (openHistoryDrawer's own doc),
          // so there's very often a previous record's photo underneath this
          // click that should stay visible, dimmed, instead of disappearing
          // until the newly-picked one is ready.
          li.addEventListener("click", async () => {
            if (historySelectMode) {
              // No image_key -- nothing this tile could be looked back up
              // by in historyJobsByKey, so there's nothing to actually
              // select/download; leave the click a no-op rather than
              // toggling on some other falsy key shared by other such
              // entries.
              if (job.image_key) toggleHistorySelection(job.image_key, li);
              return;
            }
            if (li.classList.contains("opening")) return;
            li.classList.add("opening");
            const spinner = document.createElement("div");
            spinner.className = "rc-spinner";
            media?.appendChild(spinner);
            const showingCanvas = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
            if (showingCanvas && els.runSwitchLoading) els.runSwitchLoading.hidden = false;
            try {
              await viewHistoryRecord(job);
            } finally {
              li.classList.remove("opening");
              spinner.remove();
              if (els.runSwitchLoading) els.runSwitchLoading.hidden = true;
            }
          });
        } else if (statuses.every((s) => s.status === "error")) {
          li.className = "history-cube batch-sidebar-item error";
          if (badge) badge.textContent = "失敗";
          // Drop only the spinner -- keep the reference thumb (if any) so the
          // tile still shows a recognizable photo instead of going blank; the
          // badge/border already communicate the failure.
          media?.querySelector(".rc-spinner")?.remove();
          // Nothing to download here, but a failed entry is exactly the kind
          // of thing a user wants to bulk-clear -- .selectable lets it join
          // a multi-select same as a done tile (buildSelectedHistoryZip
          // already skips a job with no successful runs, so an accidental
          // mixed done+failed download selection stays harmless). No "view"
          // action exists for a failed tile, so unlike the done branch's own
          // listener above, this one only ever does the select-mode toggle.
          markHistoryTileSelectable(li, job);
          li.addEventListener("click", () => {
            if (historySelectMode && job.image_key) toggleHistorySelection(job.image_key, li);
          });
        } else {
          if (badge) badge.textContent = "處理中";
          // Still clickable -- otherwise there's no way to come back and
          // check on a record that was still processing last time this list
          // was fetched (see viewProcessingHistoryRecord).
          li.addEventListener("click", () => viewProcessingHistoryRecord(job));
        }
      } catch {
        li.className = "history-cube batch-sidebar-item error";
        if (badge) badge.textContent = "無法取得狀態";
        markHistoryTileSelectable(li, job);
        li.addEventListener("click", () => {
          if (historySelectMode && job.image_key) toggleHistorySelection(job.image_key, li);
        });
      }
    }));
  }

  // Opens the drawer over whatever's currently showing -- not a
  // showSection() screen, since History is a panel the user glances at and
  // dismisses, not a destination that replaces the whole page.
  function openHistoryDrawer() {
    if (els.historyDrawer) els.historyDrawer.classList.add("drawer-open");
    if (els.historyDrawerBackdrop) {
      els.historyDrawerBackdrop.hidden = false;
      els.historyDrawerBackdrop.classList.add("drawer-open");
    }
  }

  function closeHistoryDrawer() {
    exitHistorySelectMode();
    if (els.historyDrawer) els.historyDrawer.classList.remove("drawer-open");
    if (els.historyDrawerBackdrop) {
      els.historyDrawerBackdrop.classList.remove("drawer-open");
      els.historyDrawerBackdrop.hidden = true;
    }
  }

  async function openHistoryPage() {
    // Does NOT bump historyViewToken -- that's reserved for actually
    // navigating to a different record (viewHistoryRecord/
    // viewProcessingHistoryRecord already do it themselves). Bumping it here
    // too would invalidate isCurrent() for any in-flight processing job the
    // instant the user so much as glances at the drawer, even if they close
    // it again without selecting anything -- silently preventing that job's
    // own completion from ever revealing the results page.
    openHistoryDrawer();
    const user = getCurrentUser();
    if (els.historyCurrentUser) els.historyCurrentUser.textContent = user || "";
    if (!user) {
      renderHistoryList([]);
      return;
    }
    try {
      const { jobs } = await getHistory(user);
      renderHistoryList(jobs || []);
    } catch {
      renderHistoryList([]);
    }
  }

  // Secret admin path: ?admin in the URL (no value needed) requests access
  // as the "admin" account itself -- PHOTO_AGENT_ADMIN_USERS' default
  // (backend/services/report.py) -- without needing any real account/login.
  // Same no-real-auth model already documented there: knowing the flag is
  // the whole check, and the backend's is_admin() still has the final say.
  function resolveAdminUser() {
    if (new URLSearchParams(window.location.search).has("admin")) return "admin";
    return getCurrentUser();
  }

  // Shows #admin-reports-btn only for an account the backend recognizes as
  // an admin -- there's no separate "am I admin" endpoint, GET /reports/user
  // itself 403s a non-admin account (backend/services/report.py's is_admin),
  // so a failure here just means "stay hidden", same as any other account-
  // gated check in this file. Re-run after every account change (init,
  // handleCreateAccount) since a fresh login could be either.
  async function checkAdminAccess() {
    if (els.adminReportsBtn) els.adminReportsBtn.hidden = true;
    stopReportsPolling();
    setNewReportsAlert(false);
    const user = resolveAdminUser();
    if (!user) return;
    try {
      const { reports } = await getReports(user);
      if (els.adminReportsBtn) els.adminReportsBtn.hidden = false;
      // This call is also the baseline for pollForNewReports below -- a
      // report that already existed before this session started shouldn't
      // itself count as "new" the moment polling kicks in.
      seedKnownReports(reports);
      startReportsPolling();
    } catch {
      // 403 (not an admin) or a request failure -- either way, stay hidden.
    }
  }

  // Baseline for pollForNewReports -- reset every time the drawer actually
  // loads fresh data (checkAdminAccess, openReportsPage, refreshReportsList),
  // never by the poll itself, so a report the poll has flagged keeps showing
  // the alert on every subsequent tick until an admin actually goes and
  // looks (rather than the alert quietly clearing itself on the next poll).
  let knownReportIds = null;
  let hasNewReports = false;
  let reportsPollTimer = null;
  const REPORTS_POLL_INTERVAL_MS = 30000;

  function seedKnownReports(reports) {
    knownReportIds = new Set((reports || []).map((r) => r.report_id));
  }

  // Puts the "unseen report" dot on whichever entry point is actually
  // reachable right now -- #admin-reports-btn while the drawer is closed
  // (that's the only way back in), #reports-refresh-btn while it's open
  // (the drawer's own list is already on screen; the dot just says it's
  // stale). Re-evaluated on every drawer open/close too, not just here, so
  // an alert that arrived while the drawer was open still relocates to
  // #admin-reports-btn if the drawer gets closed without a refresh click.
  function setNewReportsAlert(show) {
    hasNewReports = show;
    const drawerOpen = !!(els.reportsDrawer && els.reportsDrawer.classList.contains("drawer-open"));
    if (els.adminReportsBtn) els.adminReportsBtn.classList.toggle("has-report-alert", show && !drawerOpen);
    if (els.reportsRefreshBtn) els.reportsRefreshBtn.classList.toggle("has-report-alert", show && drawerOpen);
  }

  // Doesn't touch #reports-list -- only ever raises the alert dot, never
  // silently rewrites whatever an admin currently has open (that's what
  // the explicit refresh button/drawer reopen are for). A transient
  // failure just tries again next tick; nothing to surface for that.
  async function pollForNewReports() {
    const user = resolveAdminUser();
    if (!user || !knownReportIds) return;
    try {
      const { reports } = await getReports(user);
      if ((reports || []).some((r) => !knownReportIds.has(r.report_id))) setNewReportsAlert(true);
    } catch {
      // ignore -- see comment above
    }
  }

  function stopReportsPolling() {
    if (reportsPollTimer) {
      clearInterval(reportsPollTimer);
      reportsPollTimer = null;
    }
  }

  function startReportsPolling() {
    stopReportsPolling();
    reportsPollTimer = setInterval(pollForNewReports, REPORTS_POLL_INTERVAL_MS);
  }

  function openReportsDrawer() {
    if (els.reportsDrawer) els.reportsDrawer.classList.add("drawer-open");
    if (els.reportsDrawerBackdrop) {
      els.reportsDrawerBackdrop.hidden = false;
      els.reportsDrawerBackdrop.classList.add("drawer-open");
    }
    setNewReportsAlert(hasNewReports);
  }

  function closeReportsDrawer() {
    if (els.reportsDrawer) els.reportsDrawer.classList.remove("drawer-open");
    if (els.reportsDrawerBackdrop) {
      els.reportsDrawerBackdrop.classList.remove("drawer-open");
      els.reportsDrawerBackdrop.hidden = true;
    }
    setNewReportsAlert(hasNewReports);
  }

  function formatReportTimestamp(ts) {
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function openReportScreenshotModal(url) {
    if (!els.reportScreenshotModal || !els.reportScreenshotModalImg) return;
    els.reportScreenshotModalImg.src = url;
    els.reportScreenshotModal.hidden = false;
  }

  function closeReportScreenshotModal() {
    if (els.reportScreenshotModal) els.reportScreenshotModal.hidden = true;
    // Drops the loaded image immediately rather than leaving last report's
    // screenshot sitting in memory/the DOM until the next one overwrites it.
    if (els.reportScreenshotModalImg) els.reportScreenshotModalImg.removeAttribute("src");
  }

  // "unread" is never a manual click target -- opening a report (see
  // renderReportsList's row click below) auto-marks it "read" the moment an
  // admin looks at it, so the status icon's own click only ever needs to
  // toggle between the two triage states an admin actually sets by hand:
  // read <-> solved. An item that's somehow still "unread" when its icon is
  // clicked directly (without opening the record first) also goes to
  // "solved" here, same as "read" would -- there's no third click target.
  const REPORT_STATUS_LABELS = { unread: "未讀", read: "待解決", solved: "已解決" };
  const REPORT_STATUS_ORDER = ["unread", "read", "solved"];
  function nextReportStatus(status) {
    return status === "solved" ? "read" : "solved";
  }
  function applyReportStatus(statusBtn, status) {
    // classList add/remove (not a hard className reassignment) -- callers
    // other than the drawer's own freshly-created pill (e.g. the result
    // page's #result-report-status, see updateResultStatusPill) carry an
    // extra class of their own (positioning) that a plain `className = ...`
    // would silently wipe out.
    statusBtn.classList.add("report-item-status");
    REPORT_STATUS_ORDER.forEach((s) => statusBtn.classList.toggle(`status-${s}`, s === status));
    statusBtn.textContent = REPORT_STATUS_LABELS[status] || status;
    statusBtn.setAttribute("aria-label", `回報狀態：${REPORT_STATUS_LABELS[status] || status}（點擊切換）`);
  }

  // Best-effort, not awaited by callers -- a failed status write here just
  // means the icon didn't update, never something that should block or
  // error out whatever the caller was actually doing (opening a report,
  // clicking the icon itself).
  async function setReportStatus(r, statusBtn, status) {
    const user = resolveAdminUser();
    if (!user) return;
    // r.status is genuinely undefined (not the string "unread") for any
    // report fetched before it had a status at all -- falling back here too
    // (applyReportStatus's initial render already does) keeps a failed PATCH
    // reverting to the same "unread" pill instead of a literal "undefined" one.
    const previous = r.status || "unread";
    r.status = status;
    applyReportStatus(statusBtn, status);
    try {
      await updateReportStatus(user, r.report_id, status);
    } catch {
      r.status = previous;
      applyReportStatus(statusBtn, previous);
    }
  }

  // Mirrors the drawer's own status pill onto the result page itself, for a
  // record opened via viewHistoryRecord's `report` option (renderReportsList's
  // row click) -- lets an admin triage without reopening the drawer. Shares
  // the same `report` object reference the drawer list holds, so a click here
  // updates that list's in-memory state too, same as the drawer's own pill.
  // report === null hides it -- every other viewHistoryRecord caller (plain
  // history, not opened from a report) and resetApp() pass/imply that.
  function updateResultStatusPill(report) {
    const pill = els.resultReportStatus;
    if (!pill) return;
    if (!report) {
      pill.hidden = true;
      pill.onclick = null;
      return;
    }
    pill.hidden = false;
    applyReportStatus(pill, report.status || "unread");
    pill.onclick = () => setReportStatus(report, pill, nextReportStatus(report.status || "unread"));
  }

  async function handleDeleteReport(r, li) {
    const user = resolveAdminUser();
    if (!user) return;
    const confirmed = await showConfirmModal("確定要刪除這筆回報嗎？");
    if (!confirmed) return;
    try {
      await deleteReport(user, r.report_id);
      li.remove();
      if (els.reportsList && els.reportsEmpty) {
        els.reportsEmpty.hidden = els.reportsList.children.length > 0;
      }
    } catch (err) {
      showNoticeModal(err.message || "刪除回報失敗，請稍後再試。");
    }
  }

  // One row per flagged job -- clicking a row reuses viewHistoryRecord
  // (the same history-entry playback the processing-history drawer uses)
  // against a minimal {path, runs} stand-in, since a report only ever
  // records job_id/path, not the rest of a real history entry's fields
  // (preset/image_quality/...) -- viewHistoryRecord already falls back to
  // sensible defaults for those when absent.
  function renderReportsList(reports) {
    if (!els.reportsList) return;
    els.reportsList.innerHTML = "";
    if (els.reportsEmpty) els.reportsEmpty.hidden = reports.length > 0;
    reports.forEach((r) => {
      const li = document.createElement("li");
      li.className = "report-item";

      const thumb = document.createElement("div");
      thumb.className = "report-item-thumb";
      // r.job_id is absent for a general report (#report-btn is clickable
      // with nothing on screen -- record_report, backend/services/report.py)
      // -- nothing to thumbnail in that case, same as an AIPoC-path report
      // already has no thumbnail route to use.
      if (r.job_id && r.path === "local") {
        const img = document.createElement("img");
        img.alt = "";
        img.src = resultThumbnailUrl(r.job_id);
        img.addEventListener("error", () => img.remove());
        thumb.appendChild(img);
      }

      const info = document.createElement("div");
      info.className = "report-item-info";
      const userLine = document.createElement("span");
      userLine.className = "report-item-user";
      userLine.textContent = r.user;
      const metaLine = document.createElement("span");
      metaLine.className = "report-item-meta";
      metaLine.textContent = r.job_id
        ? `${formatReportTimestamp(r.ts)} · ${r.path === "aipoc" ? "AIPoC" : "Local"}`
        : `${formatReportTimestamp(r.ts)} · 一般回報`;
      info.appendChild(userLine);
      info.appendChild(metaLine);
      // r.user_prompt (what the original submission actually asked for) is
      // distinct from r.note (the reporter's own description of what's
      // wrong) -- shown as its own labeled line so the two aren't confused.
      if (r.user_prompt) {
        const promptLine = document.createElement("span");
        promptLine.className = "report-item-note";
        promptLine.textContent = `補充指令：${r.user_prompt}`;
        info.appendChild(promptLine);
      }
      if (r.note) {
        const noteLine = document.createElement("span");
        noteLine.className = "report-item-note";
        noteLine.textContent = `回報說明：${r.note}`;
        info.appendChild(noteLine);
      }
      // The reporter's own screen capture of the whole results panel at the
      // moment they clicked 回報 (captureReportScreenshot) -- separate from
      // the job_id thumbnail above (that's this job's actual output image;
      // this is what the reporter's browser was showing, which can differ:
      // a layout glitch, a different RUN on screen, ...). Its own button
      // (not the row's own click, which opens the job's real result) since
      // a report can have one without the other -- opens #report-screenshot-
      // modal in-app rather than a new tab.
      if (r.has_screenshot) {
        const screenshotBtn = document.createElement("button");
        screenshotBtn.type = "button";
        screenshotBtn.className = "report-item-screenshot-link";
        screenshotBtn.textContent = "查看回報截圖";
        screenshotBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          openReportScreenshotModal(reportScreenshotUrl(r.report_id));
        });
        info.appendChild(screenshotBtn);
      }

      li.appendChild(thumb);
      li.appendChild(info);

      // Click toggles read <-> solved only (see nextReportStatus); opening
      // the row (see the job_id click handler below) separately marks an
      // unread report "read" the moment it's opened, so unread never needs
      // to be a manual click target here.
      const statusBtn = document.createElement("button");
      statusBtn.type = "button";
      applyReportStatus(statusBtn, r.status || "unread");
      statusBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        setReportStatus(r, statusBtn, nextReportStatus(r.status || "unread"));
      });
      li.appendChild(statusBtn);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "report-item-remove";
      removeBtn.setAttribute("aria-label", "刪除回報");
      removeBtn.textContent = "×";
      removeBtn.addEventListener("click", (e) => {
        // Stop this from also bubbling into the row's own click handler
        // (viewHistoryRecord), attached below for a job-tied report.
        e.stopPropagation();
        handleDeleteReport(r, li);
      });
      li.appendChild(removeBtn);

      // A general report has no job_id -- nothing for the row's own click
      // to open (the screenshot button above is the only thing to look at
      // for one of these), so it stays a plain non-interactive row instead
      // of a dead click target.
      if (r.job_id) {
        li.addEventListener("click", () => {
          // Fire-and-forget -- opening a still-unread report marks it read,
          // same as any inbox; never awaited since it must not delay
          // opening the actual result the admin clicked through for.
          if ((r.status || "unread") === "unread") setReportStatus(r, statusBtn, "read");
          closeReportsDrawer();
          // revealDetailExtra: true -- an admin checking a flagged case
          // should see the full QA verdict/recipe-adherence/prompts panel,
          // normally debug-only, for this specific record. user_prompt:
          // r.user_prompt -- a real history entry carries its own
          // job.user_prompt (see restoreReviseSelections), but this
          // minimal report-derived stand-in has nowhere else to get it
          // from; without this, #prompt would come back empty even though
          // the report captured the original 補充指令.
          viewHistoryRecord({ path: r.path, runs: [r.job_id], user_prompt: r.user_prompt }, { revealDetailExtra: true, report: r });
        });
      } else {
        li.classList.add("is-static");
      }
      els.reportsList.appendChild(li);
    });
  }

  async function openReportsPage() {
    const user = resolveAdminUser();
    if (!user) return;
    openReportsDrawer();
    try {
      const { reports } = await getReports(user);
      renderReportsList(reports || []);
      // Fresh data just loaded -- resets pollForNewReports' baseline and
      // clears whatever alert brought the admin in here in the first place.
      seedKnownReports(reports);
      setNewReportsAlert(false);
    } catch {
      renderReportsList([]);
    }
  }

  // Reloads the list in place without closing/reopening the drawer -- the
  // manual counterpart to openReportsPage's own fetch, for an admin who's
  // already in the drawer when pollForNewReports raises the alert on
  // #reports-refresh-btn (see setNewReportsAlert).
  async function refreshReportsList() {
    const user = resolveAdminUser();
    if (!user) return;
    try {
      const { reports } = await getReports(user);
      renderReportsList(reports || []);
      seedKnownReports(reports);
      setNewReportsAlert(false);
    } catch {
      showNoticeModal("重新載入回報清單失敗，請稍後再試。");
    }
  }

  // A live single-image session never re-renders the recipe checklist from a
  // filtered analysis -- singleFlowState.analysis is pinned to the original,
  // untouched analyze-stage result for the whole session, so retouch_recipe
  // never shrinks and lastSingleSelections.recipeChecked always stays index-
  // aligned to it. Historical playback (viewHistoryRecord) has no choice but
  // to start from result.analysis, which -- if the last revise unchecked
  // anything -- is already that filtered, shrunk version (buildEffectiveAnalysis
  // drops unchecked items from correction_plan/retouch_recipe entirely before
  // submitting). Left as-is, a previously-unchecked item would just be gone
  // for good the next time this job's history is revisited. This restores it:
  // merges each rejected_* array back onto its kept counterpart (order:
  // kept items first, then whatever was rejected -- the original interleaving
  // isn't recoverable, only membership) and returns a recipeChecked/options
  // pair matching that restored order, for lastSingleSelections to apply.
  // userPrompt is the history entry's own stored job.user_prompt (absent for
  // an entry recorded before that field existed, or if none was typed) --
  // passed through as-is rather than read from analysis, since the user's
  // raw typed instruction isn't part of it.
  function restoreReviseSelections(analysis, userPrompt, overwritePrompt) {
    const rejectedRecipe = analysis.rejected_retouch_recipe || [];
    const rejectedPlan = analysis.rejected_corrections || [];
    const rejectedAxes = analysis.rejected_axes || [];
    const hasRejected = rejectedRecipe.length > 0 || rejectedPlan.length > 0;
    const keptCount = (analysis.retouch_recipe || []).length;
    const restoredAnalysis = hasRejected
      ? {
          ...analysis,
          retouch_recipe: [...(analysis.retouch_recipe || []), ...rejectedRecipe],
          correction_plan: [...(analysis.correction_plan || []), ...rejectedPlan],
          correction_axes: [...(analysis.correction_axes || []), ...rejectedAxes]
        }
      : analysis;
    const recipeChecked = hasRejected
      ? restoredAnalysis.retouch_recipe.map((_, i) => i < keptCount)
      : null;
    const styleOptions = analysis.style_options || {};
    const options = {
      pose: styleOptions.pose || "default",
      angle: styleOptions.angle || "default",
      lighting: styleOptions.lighting || "default",
      size: styleOptions.size || "default"
    };
    return { analysis: restoredAnalysis, selections: { userPrompt: userPrompt || "", overwritePrompt: !!overwritePrompt, options, recipeChecked } };
  }

  // A "Run" click with run_count > 1 (two-stage manual path) submits N
  // *separate* job_ids (runSingleItem/editBatchItem), all recorded together
  // under one history entry's `runs` list by backend/services/account.py's
  // action_id grouping -- fetch every one of them (each of which might
  // itself be a combined job with its own internal multi-RUN shape too) and
  // flatten into one run-numbered list, same shape either way. job.job_id is
  // kept as a fallback for a history entry recorded before `runs` existed.
  async function fetchAllHistoryRuns(job) {
    const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
    const perJobData = await Promise.all(jobIds.map((id) => getJobRuns(id, { path: job.path }).catch(() => null)));
    const flat = [];
    perJobData.forEach((data, idx) => {
      if (!data) return;
      const jobId = jobIds[idx];
      const rawRuns = Array.isArray(data.runs) && data.runs.length
        ? data.runs
        : [{ image_url: data.image_url, result: data.result }];
      rawRuns.forEach((r) => {
        if (!r.result) return; // this job/RUN hasn't actually finished
        flat.push({ jobId, imageUrl: r.image_url, result: r.result });
      });
    });
    // Re-numbered sequentially regardless of each job's own internal RUN
    // numbering -- these may come from entirely separate job_ids that each
    // think of themselves as "RUN 1".
    return flat.map((r, i) => ({ run: i + 1, status: "done", timings: [], error: null, ...r }));
  }

  // Lets the user come back and check on a history record that was still
  // processing last time the list was fetched -- previously such a row had
  // no click handler at all, so there was no way back to it short of
  // reopening the history page over and over until it finished. Shows the
  // same "AI is working" placeholder the batch flow uses, then polls every
  // RUN in this action and renders the real result (via viewHistoryRecord)
  // as soon as any one of them succeeds. historyViewToken guards against the
  // user navigating elsewhere while this poll (which can run for minutes) is
  // still in flight -- it must not yank them back to a results page for a
  // job they've since stopped looking at.
  async function viewProcessingHistoryRecord(job) {
    const myToken = ++historyViewToken;
    showSection("results");
    if (els.batchSidebar) els.batchSidebar.hidden = true;
    if (els.resultsBody) els.resultsBody.classList.remove("batch-mode");
    currentBatchRecord = null;
    // No result -- and so no prompts -- yet for #show-prompt-btn to show.
    if (els.showPromptBtn) els.showPromptBtn.hidden = true;
    // In case some OTHER record's live revise (finishSingleFlow) left these
    // toggled -- showBatchMainLoading below hides #results-canvas-row
    // entirely (its parent), so this is mostly cheap insurance, not load-
    // bearing here the way it is in viewHistoryRecord.
    if (els.canvasLoading) els.canvasLoading.hidden = true;
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
    // job.job_id doesn't exist on a history entry (see record_history/
    // account.py) -- every job from the same action lives in job.runs
    // instead, with job.job_id kept only as a fallback for an entry recorded
    // before that field existed.
    const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
    const previewUrl = job.path === "local" ? sourceImageUrl(jobIds[0]) : "";
    showBatchMainLoading({ previewUrl, status: "processing" });

    // A combined multi-RUN job (runCombinedMultiRun) is ONE job_id covering
    // every RUN (job_processor.py runs finish_stage `run_count` times inside
    // it), unlike the two-stage manual path's N *separate* job_ids below --
    // resume watching it incrementally (same watchCombinedJobRuns the live
    // in-page flow uses) instead of only finding out once EVERYTHING is
    // done, so the progress bar/RUN switcher/canvas come back the same way
    // they would have if the user had just stayed on the page.
    if (job.path === "local" && jobIds.length === 1) {
      const combinedJobId = jobIds[0];
      const runCount = Math.max(1, parseInt(job.run_count, 10) || 1);

      // hideAllBatchMainViews (called by showBatchMainLoading above) only
      // repositions #analysis-preview home (restoreAnalysisPreviewHome), not
      // closes it -- if it was left open from viewing some other, already-
      // FINISHED record earlier this session, it would otherwise keep
      // showing #ap-options-card-inline over this still-processing one too.
      // The live in-page flow never opens this panel until every RUN is
      // actually done (finishSingleFlow always passes autoOpenPanel:false
      // for its own per-RUN progress calls); match that here.
      closeInlineAnalysisPanel();

      selectedFile = null;
      selectedFileSourceUrl = sourceImageUrl(combinedJobId);
      originalImgThumbUrl = sourceThumbnailUrl(combinedJobId);
      const myRuns = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: combinedJobId, status: "processing",
        imageUrl: null, result: null, timings: [], error: null
      }));
      singleRuns = myRuns;
      singleMirrorIndex = null;

      let revealed = false;
      const renderIfPossible = () => {
        if (historyViewToken !== myToken) return; // navigated away meanwhile
        const doneRun = myRuns.find((r) => r.status === "done");
        if (!doneRun) return; // nothing landed yet -- leave the loading placeholder up
        if (!singleFlowState || singleFlowState.analyzeJobId !== doneRun.jobId) {
          const extraInfo = doneRun.result["extra-info"] || {};
          const { analysis: restoredAnalysis, selections } = restoreReviseSelections(doneRun.result.analysis, job.user_prompt, job.overwrite_prompt);
          singleFlowState = {
            analysis: restoredAnalysis,
            analyzeJobId: doneRun.jobId,
            presetKey: job.preset || "natural",
            preset: presets[job.preset] || presets.natural,
            imageQuality: job.image_quality || extraInfo.image_quality || "medium",
            imageModel: job.image_model || extraInfo.image_model || "gpt-image-2",
            analysisModel: job.analysis_model || extraInfo.analysis_model || "gpt-4.1-mini",
            aspectRatio: job.aspect_ratio || "auto",
            aiPhotoEditing: job.ai_photo_editing != null ? job.ai_photo_editing : !!extraInfo.image_model,
            runQa: job.run_qa != null ? job.run_qa : true,
            processingPath: "local",
            analyzeTimings: [],
            useCombinedJob: false
          };
          lastSingleSelections = selections;
        }
        const applyRender = () => {
          els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
          showSection("results");
          showSingleResult({ autoOpenPanel: false, forceReopen: !revealed });
          // First RUN to actually land -- keep the drawer open until now so
          // it doesn't disappear during the still-processing wait, then close
          // it now that a result page is actually visible behind it.
          if (!revealed) closeHistoryDrawer();
          revealed = true;
        };
        if (els.originalImg.src === selectedFileSourceUrl) {
          // A load from an EARLIER call to this same function may already be
          // pending (a later RUN landing before the image from an earlier
          // RUN finished loading) -- overwriting .onload here would silently
          // discard that pending render instead of ever showing it. Since
          // applyRender reads myRuns/singleRuns live (not a stale closure),
          // that already-pending handler will naturally reflect however many
          // RUNs are done by the time it actually fires -- nothing to do here
          // but wait for it, unless the image is already fully loaded.
          if (els.originalImg.complete) applyRender();
        } else {
          els.originalImg.onload = () => { els.originalImg.onload = null; applyRender(); };
          els.originalImg.onerror = () => { els.originalImg.onload = null; applyRender(); };
          els.originalImg.src = selectedFileSourceUrl;
        }
      };

      try {
        await watchCombinedJobRuns(combinedJobId, runCount, {
          onRunDone: (runData) => {
            const run = myRuns[runData.run - 1];
            if (!run) return;
            run.status = "done";
            run.imageUrl = runData.image_url;
            run.result = runData.result;
            renderIfPossible();
          }
        });
      } catch (error) {
        if (historyViewToken !== myToken) return; // navigated away meanwhile
        if (!myRuns.some((r) => r.status === "done")) {
          showBatchMainError({ previewUrl, error: "所有 RUN 皆處理失敗，請稍後再試。" });
        }
        // Otherwise at least one RUN already rendered -- leave it displayed
        // rather than clobbering it with an error for the whole record.
        return;
      }
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      // Finalizes exactly like a normally-finished record (revise button,
      // etc.) now that every RUN is confirmed done.
      await viewHistoryRecord(job);
      return;
    }

    // Two-stage manual path (N separate job_ids) or the AIPoC path (no
    // incremental combined-job concept, synchronous analyze) -- unchanged:
    // wait for any ONE of them to succeed, then render whatever's done.
    try {
      await Promise.any(jobIds.map((id) => pollJobStatus(id, { path: job.path })));
    } catch {
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      showBatchMainError({ previewUrl, error: "所有 RUN 皆處理失敗，請稍後再試。" });
      return;
    }
    if (historyViewToken !== myToken) return; // navigated away meanwhile
    await viewHistoryRecord(job);
  }

  // Reconstructs the full single-photo results view (recipe/analysis/QA
  // panel, RUN switcher, before/after compare, download) for a finished
  // history entry, pulling this job's data fresh from the backend instead of
  // in-memory batch/single state.
  //
  // Local-path jobs keep their srcKeys on the job's own Redis hash forever
  // (see backend/services/account.py) -- there's nothing stopping a further
  // revise/continue on one, so this seeds the single-image flow's own module
  // state (singleRuns/singleFlowState/...) as if this job were the current
  // session's result, then hands off to showSingleResult/
  // backToProcessFromResults/finishSingleFlow completely unchanged. AIPoC-
  // path jobs have no recoverable original file to resubmit with (the remote
  // gateway is the only place that ever had it, and even its in-memory task
  // mapping doesn't survive a restart) -- those fall back to showBatchDoneMain
  // in its read-only (reviewable:false) mode instead.
  // revealDetailExtra: true and report: &lt;the report object&gt; only for the
  // admin reports drawer (see renderReportsList) -- revealDetailExtra lets an
  // admin checking a flagged case see the normally-hidden
  // #results-detail-extra panel (QA verdict, recipe adherence, workflow
  // steps, ...) for that one record specifically. report drives updateResultStatusPill's
  // own triage pill on the result page. Neither is persisted -- the very
  // next viewHistoryRecord call (a different record) or resetApp() hides
  // #results-detail-extra again and hides the status pill.
  async function viewHistoryRecord(job, { revealDetailExtra = false, report = null } = {}) {
    historyViewToken += 1;
    const myToken = historyViewToken;
    const runs = await fetchAllHistoryRuns(job);
    if (!runs.length) {
      showNoticeModal("無法取得這筆處理紀錄，請稍後再試。");
      return;
    }
    // Drawer stays open (not closed here) until the result page it's opening
    // has actually finished rendering below -- closing it immediately after
    // this fetch left a visible gap where neither the drawer nor the result
    // was on screen while the rest of this function awaited.
    if (resultsDetailExtra) resultsDetailExtra.hidden = !revealDetailExtra;
    updateResultStatusPill(report);

    // A live batch (if one happened to be running in this same session) has
    // nothing to do with a historical record -- make sure its sidebar/
    // batch-mode layout doesn't bleed into this single-photo view.
    if (els.batchSidebar) els.batchSidebar.hidden = true;
    if (els.resultsBody) els.resultsBody.classList.remove("batch-mode");
    currentBatchRecord = null;
    // Some OTHER record's live revise (finishSingleFlow) may have left these
    // toggled to show its own "AI is working" overlay -- reset unconditionally
    // so that stale overlay never covers up whatever's actually being shown
    // here (finishSingleFlow's own completion no longer touches the DOM at
    // all once historyViewToken has moved on, see its own comment).
    if (els.canvasLoading) els.canvasLoading.hidden = true;
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;

    if (job.path === "local") {
      selectedFile = null;
      // job.job_id doesn't exist on a history entry (job ids live in
      // job.runs instead, see record_history/account.py) -- runs[0].jobId is
      // the actual resolved job_id fetchAllHistoryRuns already looked up,
      // and every job in this action shares the same source image anyway.
      selectedFileSourceUrl = sourceImageUrl(runs[0].jobId);
      originalImgThumbUrl = sourceThumbnailUrl(runs[0].jobId);
      singleRuns = runs;
      singleMirrorIndex = null;
      const firstDone = singleRuns[0];
      // job.* comes from the history entry (backend/services/account.py) --
      // absent (rather than falsy-but-present) only for an entry recorded
      // before that field existed. Falling back to undefined here would
      // silently submit ai_photo_editing=false on continue (the cheap local-
      // only pass, not a real AI edit) -- extra-info.image_model (set only
      // when an AI edit actually ran, see pipeline.py's finish_stage) is a
      // more accurate fallback than a blind default for that one specifically.
      const extraInfo = firstDone.result["extra-info"] || {};
      const { analysis: restoredAnalysis, selections } = restoreReviseSelections(firstDone.result.analysis, job.user_prompt, job.overwrite_prompt);
      singleFlowState = {
        analysis: restoredAnalysis,
        analyzeJobId: firstDone.jobId,
        presetKey: job.preset || "natural",
        preset: presets[job.preset] || presets.natural,
        imageQuality: job.image_quality || extraInfo.image_quality || "medium",
        imageModel: job.image_model || extraInfo.image_model || "gpt-image-2",
        analysisModel: job.analysis_model || extraInfo.analysis_model || "gpt-4.1-mini",
        aspectRatio: job.aspect_ratio || "auto",
        aiPhotoEditing: job.ai_photo_editing != null ? job.ai_photo_editing : !!extraInfo.image_model,
        runQa: job.run_qa != null ? job.run_qa : true,
        processingPath: "local",
        analyzeTimings: [],
        useCombinedJob: false
      };
      lastSingleSelections = selections;

      // Awaited (not fire-and-forget) so this function's own promise only
      // resolves once the result photo is actually showing -- callers like
      // the history-list click handler use that to keep their own "opening…"
      // spinner up for the whole wait instead of dropping it the instant the
      // network fetch above resolved. setImgSrcPreloaded (not a bare
      // src assignment) keeps #original-img itself from ever showing the
      // browser's broken-image icon/alt text while this record's own photo
      // is a different one than whatever was on screen before.
      await setImgSrcPreloaded(els.originalImg, selectedFileSourceUrl);
      els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
      showSection("results");
      // forceReopen: true -- this may be a genuinely different record than
      // whatever the analysis panel was last showing, even if it was already
      // open (see openInlineAnalysisForCurrentContext's own doc on this).
      // showSingleResult itself preloads the RUN's image before painting, so
      // this doesn't return until that's done either.
      await showSingleResult({ forceReopen: true, revealDetailExtra });
      // #ap-options-card-inline's model/quality and aspect-ratio pickers are
      // otherwise left showing whatever a previous record (or the upload
      // page) had picked -- sync them to what THIS record actually used.
      // Only the picker, not applyModelQualityChange/applyAspectRatioChange's
      // full side effects (pushing onto singleFlowState -- already correct,
      // just set above -- and, for aspect ratio, re-fitting the preview,
      // which needs to run after showSingleResult's own render regardless).
      applyModelQualityOption(findModelQualityOptionByPair(singleFlowState.imageModel, singleFlowState.imageQuality));
      applyAnalysisModelOption(findAnalysisModelOption(singleFlowState.analysisModel));
      applyAspectRatioOption(findAspectRatioOption(singleFlowState.aspectRatio));
      // singleFlowState.advancedOptions itself is left unset by the historical
      // reconstruction above (unlike imageModel/aspectRatio) -- reading off
      // singleFlowState.analysis._advanced_options directly, the same key a
      // later revise's buildEffectiveAnalysis falls back to, keeps this in
      // sync with what a revise would actually use if left untouched.
      applyAdvancedOptionsOption(singleFlowState.analysis?._advanced_options);
      if (historyViewToken === myToken) closeHistoryDrawer();
      return;
    }

    const first = runs[0];
    const extraInfo = first.result["extra-info"] || {};

    const record = {
      file: null,
      previewUrl: null, // no recoverable original for an AIPoC-path job
      jobId: first.jobId,
      runNumber: first.run,
      result: first.result,
      imageUrl: first.imageUrl,
      runs,
      mirrorIndex: 0,
      timings: [],
      status: "done"
    };

    showSection("results");
    // Awaited for the same reason as the local-path branch above --
    // showBatchDoneMain preloads record.imageUrl before painting, so this
    // keeps viewHistoryRecord's own promise open (and the history-list
    // click handler's spinner up) for that whole wait too.
    await showBatchDoneMain(record, {
      preset: presets[job.preset] || presets.natural,
      // job.ai_photo_editing is only absent for a history entry recorded
      // before that field existed -- fall back to inferring it the same way
      // renderResult's own "本機處理" label does.
      aiPhotoEditing: job.ai_photo_editing != null ? job.ai_photo_editing : !!extraInfo.image_model,
      path: "aipoc",
      reviewable: false,
      revealDetailExtra
    });
    // #ap-options-card-inline's pickers are shown (and, despite reviewable:
    // false, not actually inert -- see applyModelQualityOption's own doc) for
    // this read-only view too, and need the same sync as the local-path
    // branch above.
    applyModelQualityOption(findModelQualityOptionByPair(
      job.image_model || extraInfo.image_model || "gpt-image-2",
      job.image_quality || extraInfo.image_quality || "medium"
    ));
    applyAnalysisModelOption(findAnalysisModelOption(job.analysis_model || extraInfo.analysis_model || "gpt-4.1-mini"));
    applyAspectRatioOption(findAspectRatioOption(job.aspect_ratio || "auto"));
    applyAdvancedOptionsOption(record.result.analysis?._advanced_options);
    if (historyViewToken === myToken) closeHistoryDrawer();
  }

  // Demo photo picker -- lazily fetched once (getDemoImages) and cached here,
  // since the list is static server-side content that can't change mid-session.
  let demoImagesCache = null;
  // Filenames currently toggled on (see toggleDemoImageSelection) -- persists
  // across the modal being closed and reopened (e.g. the user backed out
  // without confirming, or wants to review/adjust the picks they already
  // confirmed into the upload area), and is only cleared by resetApp -- a
  // real fresh start, not just a closed dialog.
  let demoSelectedNames = new Set();

  function renderDemoImagesGrid(names) {
    if (!els.demoImagesGrid) return;
    els.demoImagesGrid.innerHTML = "";
    if (els.demoImagesEmpty) els.demoImagesEmpty.hidden = names.length > 0;
    names.forEach((name) => {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = demoSelectedNames.has(name) ? "demo-image-tile selected" : "demo-image-tile";
      tile.title = name;
      // Always a real JPEG regardless of the source format (backend runs it
      // through the same Pillow/pillow-heif decode ensure_jpeg_bytes uses
      // elsewhere) -- unlike a plain <img> pointed straight at the original
      // file, this renders fine even for the HEIC demo photos.
      const img = document.createElement("img");
      img.src = demoImageThumbnailUrl(name);
      img.alt = name;
      img.loading = "lazy";
      tile.appendChild(img);
      const check = document.createElement("span");
      check.className = "demo-image-tile-check";
      check.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      tile.appendChild(check);
      tile.addEventListener("click", () => toggleDemoImageSelection(name, tile));
      els.demoImagesGrid.appendChild(tile);
    });
  }

  // Enables/labels #demo-images-confirm-btn off however many tiles are
  // currently toggled on -- called after every toggle, so the button always
  // reflects the live selection instead of only updating on open/confirm.
  function updateDemoImagesConfirmBtn() {
    if (!els.demoImagesConfirmBtn) return;
    const count = demoSelectedNames.size;
    els.demoImagesConfirmBtn.disabled = count === 0;
    els.demoImagesConfirmBtn.textContent = count > 0 ? `使用所選照片 (${count})` : "使用所選照片";
  }

  function toggleDemoImageSelection(name, tile) {
    if (demoSelectedNames.has(name)) {
      demoSelectedNames.delete(name);
      tile.classList.remove("selected");
    } else {
      demoSelectedNames.add(name);
      tile.classList.add("selected");
    }
    updateDemoImagesConfirmBtn();
  }

  function openDemoImagesModal() {
    if (!els.demoImagesModal) return;
    updateDemoImagesConfirmBtn();
    els.demoImagesModal.hidden = false;
    if (demoImagesCache) {
      renderDemoImagesGrid(demoImagesCache);
      return;
    }
    getDemoImages().then((names) => {
      demoImagesCache = names;
      // The modal may have been closed again before this resolves.
      if (!els.demoImagesModal.hidden) renderDemoImagesGrid(names);
    });
  }

  function closeDemoImagesModal() {
    if (els.demoImagesModal) els.demoImagesModal.hidden = true;
  }

  // Fetches every currently-selected demo photo's bytes and hands them all
  // to the same routeSelectedFiles path a real file-picker selection goes
  // through, so it gets the exact same single-vs-batch handling, HEIC
  // conversion, preview, etc. as an actual upload -- nothing else needs to
  // know these files didn't come from the user's own device.
  async function confirmDemoImageSelection() {
    const names = Array.from(demoSelectedNames);
    if (!names.length) return;
    els.demoImagesConfirmBtn.disabled = true;
    if (els.demoImagesGrid) els.demoImagesGrid.classList.add("is-loading");
    try {
      const files = await Promise.all(names.map(async (name) => {
        const response = await fetch(demoImageUrl(name));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        return new File([blob], name, { type: blob.type || "image/jpeg" });
      }));
      closeDemoImagesModal();
      routeSelectedFiles(files);
    } catch (error) {
      showError(`載入範例照片失敗：${error.message || error}`);
    } finally {
      if (els.demoImagesGrid) els.demoImagesGrid.classList.remove("is-loading");
      updateDemoImagesConfirmBtn();
    }
  }

  // Upload-page settings overlay -- same open/close shape as the error modal
  // above, just toggling #settings-modal's own hidden attribute. Holds the
  // 模型/長寬比/生成張數 pickers (already populated/wired above via
  // populateModelQualitySelect/populateAspectRatioSelect/wireOptionPicker),
  // so opening it doesn't need to re-render anything itself either.
  function openSettingsModal() {
    if (els.settingsModal) els.settingsModal.hidden = false;
  }

  function closeSettingsModal() {
    if (els.settingsModal) els.settingsModal.hidden = true;
  }

  // Advanced settings overlay, opened from within #settings-modal itself --
  // same open/close shape as openSettingsModal/closeSettingsModal above, just
  // toggling #advanced-settings-modal's own hidden attribute. Holds the
  // 眼睛 category (閉眼偵測/眼神對焦 toggles).
  function openAdvancedSettingsModal() {
    if (els.advancedSettingsModal) els.advancedSettingsModal.hidden = false;
  }

  function closeAdvancedSettingsModal() {
    if (els.advancedSettingsModal) els.advancedSettingsModal.hidden = true;
  }

  // Results/analysis-preview page settings overlay -- same shape as
  // openSettingsModal/closeSettingsModal above, just toggling
  // #ap-settings-modal's own hidden attribute instead. Its 模型/長寬比/生成張數
  // pickers mirror the upload page's (applyModelQualityChange/
  // applyAspectRatioChange/applyRunCountChange), so a change made here applies
  // to the next edit without re-analyzing.
  function openApSettingsModal() {
    if (els.apSettingsModal) els.apSettingsModal.hidden = false;
  }

  function closeApSettingsModal() {
    if (els.apSettingsModal) els.apSettingsModal.hidden = true;
  }

  // #header-settings-btn (index.html) is the single, always-visible entry
  // point into 設定 now -- picks #settings-modal or #ap-settings-modal based
  // on which section is actually showing, since the two pages' pickers
  // (模型/長寬比/生成張數) are independent state, not a shared modal like
  // #advanced-settings-modal. Falls back to the results-page modal for
  // "processing" too, on the assumption the results modal is the closer
  // match once analysis is running/done than the upload modal.
  function openContextSettingsModal() {
    if (els.sections.upload?.classList.contains("visible")) {
      openSettingsModal();
    } else {
      openApSettingsModal();
    }
  }

  // 修圖建議 overlay -- same open/close shape as the error modal above,
  // just toggling #ap-recipe-modal's own hidden attribute. #ap-recipe-list
  // is already populated by fillCheckList (results.js) whenever analysis
  // renders; opening this modal doesn't need to re-render anything itself.
  function openRecipeModal() {
    if (els.apRecipeModal) els.apRecipeModal.hidden = false;
  }

  function closeRecipeModal() {
    if (els.apRecipeModal) els.apRecipeModal.hidden = true;
  }

  // 修圖風格選項 overlay: two-step picker inside #ap-style-modal -- a row of
  // Pose/Angle/Lighting/Size buttons (ids ap-group-pose/-angle/-lighting/
  // -size), then that type's #ap-*-list image-option grid underneath.
  // showStyleGroup just flips which button/list pair is visible; it doesn't
  // touch which radio is checked in any of them (that's still plain radio
  // input behavior, untouched by this).
  const STYLE_GROUPS = ["pose", "angle", "lighting", "size"];

  function styleGroupListEl(group) {
    return els[`ap${group[0].toUpperCase()}${group.slice(1)}List`];
  }

  // group === null hides every list and clears every button's active state
  // (no g in STYLE_GROUPS ever equals null) -- also swaps which of
  // #ap-style-type-row (the 4 pills) / #ap-style-options-frame is visible,
  // so picking a category hands the whole modal over to that category's
  // option grid instead of splitting space with the pills. Three ways back
  // to null: #ap-style-exit-btn (manual, no pick made), picking an actual
  // option in the grid (the per-image ap-*-list change handler below,
  // automatic), or -- for either -- reopening the modal fresh (openStyleModal
  // always starts at null). #ap-style-options-title (top of that frame) is
  // set to the same Pose/Angle/Lighting/Size text as the now-hidden pill's
  // own .option-pill-label, since STYLE_GROUPS entries are already
  // lowercase versions of those labels.
  function showStyleGroup(group) {
    STYLE_GROUPS.forEach((g) => {
      const btn = document.getElementById(`ap-group-${g}`);
      const list = styleGroupListEl(g);
      if (btn) btn.classList.toggle("is-active", g === group);
      if (list) list.hidden = g !== group;
    });
    if (els.apStyleTypeRow) els.apStyleTypeRow.hidden = !!group;
    if (els.apStyleOptionsTitle) {
      els.apStyleOptionsTitle.textContent = group ? group[0].toUpperCase() + group.slice(1) : "";
    }
    if (els.apStyleOptionsFrame) els.apStyleOptionsFrame.hidden = !group;
    // #ap-style-options-frame has its own #ap-style-exit-btn (top-right of
    // its header) for returning to the pills-only view while it's showing --
    // hide the modal's own close button whenever that frame is showing so
    // there aren't two dismiss/back buttons competing for the same corner.
    if (els.apStyleModalClose) els.apStyleModalClose.hidden = !!group;
  }

  // Always opens on just the 4 Pose/Angle/Lighting/Size pills -- no
  // auto-picked or remembered-from-last-time category, so
  // #ap-style-options-frame stays hidden until the user clicks one of them
  // (picking an option inside it returns here on its own -- see the
  // per-image ap-*-list change handler below).
  function openStyleModal() {
    if (!els.apStyleModal) return;
    showStyleGroup(null);
    els.apStyleModal.hidden = false;
  }

  function closeStyleModal() {
    if (els.apStyleModal) els.apStyleModal.hidden = true;
  }

  // Full-window compare view: toggles .is-fullscreen on the real
  // #compare-card in place (CSS switches it to position:fixed) instead of
  // moving it to a separate overlay -- #compare-fullscreen-btn/#download-link
  // etc. keep working via their existing listeners since the node never
  // moves. #compare-fullscreen-btn (inside .canvas-actions inside the card)
  // doubles as the close control while open -- toggleCompareFullscreen swaps
  // its icon/label to a "restore" glyph instead of adding a second close
  // button. The card's parent gets its rendered height frozen inline for the
  // duration (position:fixed pulls the card out of flow, which would
  // otherwise collapse the parent and visibly shift the options panel below
  // it up, then back down on close) so nothing around it moves.
  const COMPARE_FULLSCREEN_EXPAND_ICON = `<svg width="18" height="18" fill="none" viewBox="0 0 24 24">
    <path d="M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
  const COMPARE_FULLSCREEN_RESTORE_ICON = `<svg width="18" height="18" fill="none" viewBox="0 0 24 24">
    <path d="M9 3v4a2 2 0 0 1-2 2H3M15 3v4a2 2 0 0 0 2 2h4M9 21v-4a2 2 0 0 0-2-2H3M15 21v-4a2 2 0 0 1 2-2h4"
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;

  function setCompareFullscreenBtnState(isFullscreen) {
    if (!els.compareFullscreenBtn) return;
    els.compareFullscreenBtn.innerHTML = isFullscreen ? COMPARE_FULLSCREEN_RESTORE_ICON : COMPARE_FULLSCREEN_EXPAND_ICON;
    els.compareFullscreenBtn.title = isFullscreen ? "back to original window" : "full screen";
    els.compareFullscreenBtn.setAttribute("aria-label", isFullscreen ? "返回原視窗" : "全螢幕檢視比較");
  }

  function isCompareFullscreen() {
    return !!els.compareCard?.classList.contains("is-fullscreen");
  }

  function openCompareFullscreen() {
    if (!els.compareCard || isCompareFullscreen()) return;
    const parent = els.compareCard.parentElement;
    if (parent) parent.style.height = `${parent.getBoundingClientRect().height}px`;
    els.compareCard.classList.add("is-fullscreen");
    if (els.compareFullscreenBackdrop) els.compareFullscreenBackdrop.hidden = false;
    setCompareFullscreenBtnState(true);
  }

  function closeCompareFullscreen() {
    if (!els.compareCard || !isCompareFullscreen()) return;
    els.compareCard.classList.remove("is-fullscreen");
    const parent = els.compareCard.parentElement;
    if (parent) parent.style.height = "";
    if (els.compareFullscreenBackdrop) els.compareFullscreenBackdrop.hidden = true;
    setCompareFullscreenBtnState(false);
  }

  function toggleCompareFullscreen() {
    if (isCompareFullscreen()) closeCompareFullscreen();
    else openCompareFullscreen();
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
  async function selectBatchRecord(record) {
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
    userPickedBatchRecord = true;
    if (previous && previous !== record) updateBatchSidebarItem(previous);
    updateBatchSidebarItem(record);
    // Same hover-and-dim treatment as the RUN-switch arrows
    // (selectAdjacentRun) -- only worth showing when a photo is already on
    // screen to keep visible while the newly-selected record's image
    // downloads (showBatchDoneMain now preloads it before painting); every
    // other target view (analyzing/error/analyzed) replaces the whole
    // canvas outright regardless, so there's nothing to hover over there.
    const showingCanvas = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
    if (showingCanvas && els.runSwitchLoading) els.runSwitchLoading.hidden = false;
    try {
      await renderBatchMain(record);
    } finally {
      if (els.runSwitchLoading) els.runSwitchLoading.hidden = true;
    }
    if (record.status === "error") openBatchError(record); // only on explicit click, never from a background refresh
    window.scrollTo({ top: 0, behavior: "instant" });
    // Picking an image is the drawer's whole purpose on narrow viewports --
    // close it back up so the just-selected main content is visible instead
    // of staying hidden behind the open drawer. Only fires from an actual
    // click (see buildBatchSidebarItem), never a background status refresh.
    closeBatchDrawer();
  }

  // Re-renders the main area only if `record` is still the selected one --
  // called from every place a record's status changes in the background
  // (analyze/edit completing), so the visible view stays live without
  // requiring the user to re-click the sidebar.
  function refreshIfSelected(record) {
    if (maybeAutoAdvanceToBatchResult(record)) return;
    if (record === currentBatchRecord) renderBatchMain(record);
  }

  // While the user is still looking at the default first-record placeholder
  // (never having clicked another sidebar/drawer item yet -- see
  // selectBatchRecord/userPickedBatchRecord), jump the main view to the
  // first record anywhere in the batch that actually finishes, instead of
  // leaving the analyzing/processing animation on screen for a record that
  // may still take a while, when a result is already sitting there ready to
  // look at. Returns true if it performed the switch (and rendered), so
  // refreshIfSelected can skip its own now-redundant render.
  function maybeAutoAdvanceToBatchResult(record) {
    if (userPickedBatchRecord) return false;
    if (record.status !== "done" || !record.result) return false;
    if (!currentBatchRecord || currentBatchRecord === record) return false;
    if (currentBatchRecord.status === "done" && currentBatchRecord.result) return false;
    const previous = currentBatchRecord;
    currentBatchRecord = record;
    updateBatchSidebarItem(previous);
    updateBatchSidebarItem(record);
    renderBatchMain(record);
    return true;
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
  // a static CSS calc() can't know the fixed header's real height plus
  // whatever else (results-body-actions, margins) pushes that starting
  // position down. Deliberately NOT re-measured on scroll: this uses the sidebar's
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

  // Narrow-viewport-only slide-in drawer for .batch-sidebar (see the
  // max-width:900px rules in index.html) -- .drawer-open is what the CSS
  // transform keys off of. A no-op look-wise on wide viewports since those
  // rules never apply there, so callers don't need to check width first.
  function openBatchDrawer() {
    if (els.batchSidebar) els.batchSidebar.classList.add("drawer-open");
    if (els.batchSidebarBackdrop) {
      els.batchSidebarBackdrop.hidden = false;
      els.batchSidebarBackdrop.classList.add("drawer-open");
    }
  }
  function closeBatchDrawer() {
    if (els.batchSidebar) els.batchSidebar.classList.remove("drawer-open");
    if (els.batchSidebarBackdrop) {
      els.batchSidebarBackdrop.classList.remove("drawer-open");
      els.batchSidebarBackdrop.hidden = true;
    }
  }

  // Same "avoid making the user scroll the window" policy as
  // updateBatchSidebarMaxHeight above -- caps #analysis-preview to the
  // viewport (long recipe lists/style options scroll inside the panel
  // instead of pushing the whole page taller), whether it's shown mid-
  // processing (full width) or inline beside the results canvas.
  function updateAnalysisPreviewMaxHeight() {
    if (!els.analysisPreview || els.analysisPreview.hidden) return;
    const top = els.analysisPreview.getBoundingClientRect().top;
    const maxHeight = Math.max(200, window.innerHeight - top - 24);
    els.analysisPreview.style.maxHeight = `${maxHeight}px`;
  }
  window.addEventListener("resize", updateAnalysisPreviewMaxHeight);

  async function renderBatchMain(record) {
    if (!record) return;
    updateApSubjectTags(record);
    updateBatchSidebarMaxHeight();
    // Only this record's OWN rotation (if it was the one actively showing)
    // ever gets the graceful finish -- switching the view to a record that
    // simply isn't loading, while some OTHER record's rotation is the one
    // currently running, must leave that other one alone (it hasn't
    // finished; it's just not on screen), not fast-play it to completion as
    // if it had. showBatchMainLoading is what pauses/resumes a rotation
    // that's merely switched away from and back.
    const wasThisRecordRotating = activeBatchRotationRecord === record;
    if (record.status === "analyzed") {
      if (wasThisRecordRotating) await finishProcSubRotation();
      showBatchAnalyzedMain(record);
    // Guard against the brief window (mid-revise, with N new runs in flight
    // alongside older already-done ones) where syncRecordMirror's aggregate
    // "any run done" check flips record.status to "done" before the specific
    // mirrored run has actually resolved -- record.result would still be
    // null then, so fall through to the loading view instead of crashing.
    } else if (record.status === "done" && record.result) {
      if (wasThisRecordRotating) await finishProcSubRotation();
      await showBatchDoneMain(record);
    } else if (record.status === "error") {
      if (wasThisRecordRotating) await finishProcSubRotation();
      showBatchMainError(record);
    } else {
      showBatchMainLoading(record); // "analyzing" / "processing"
    }
  }

  function hideAllBatchMainViews() {
    if (els.batchMainLoading) {
      els.batchMainLoading.hidden = true;
      els.batchMainLoading.classList.remove("is-error", "is-analyzed");
    }
    if (els.batchReviewContinueBtn) els.batchReviewContinueBtn.hidden = true;
    restoreAnalysisPreviewHome(); // vacates #results-analysis-slot if a done record's revise panel is open
    if (els.resultsCanvasRow) els.resultsCanvasRow.hidden = true;
  }

  function showBatchMainLoading(record) {
    hideAllBatchMainViews();
    if (!els.batchMainLoading) return;
    els.batchMainLoading.hidden = false;
    if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.thumbUrl || record.previewUrl;
    if (els.batchMainLoadingEyebrow) els.batchMainLoadingEyebrow.textContent = "Photo Master is working";
    const analyzing = record.status === "analyzing";
    if (els.batchMainLoadingTitle) {
      els.batchMainLoadingTitle.textContent = analyzing ? "正在分析照片" : "AI 正在修圖";
    }
    // Same rotating phrasings as the single-image flow (startProcSubRotation
    // et al.), just targeting #batch-main-loading-text through setProcSub
    // instead of .proc-sub/#canvas-loading-status. There's only one shared
    // rotation for however many records may be mid-analyze/edit at once, so:
    // - Same record, same stage as whatever's already rotating (a re-render
    //   for some unrelated reason -- another record's background progress,
    //   a resize) -- leave it running untouched, don't restart it.
    // - A different record than whatever was rotating -- that other
    //   record's progress is saved onto its own procSubIndex first (it
    //   hasn't finished, it's just not on screen anymore), then this one
    //   starts fresh or resumes below.
    // - Same record, but its own stage just changed (analyzing ->
    //   processing) -- always starts fresh at message 1; a genuinely new
    //   stage shouldn't continue an old stage's rotation position.
    // - Same record, same stage, resuming after having been switched away
    //   from earlier -- resumes from its saved procSubIndex instead of
    //   restarting, which is the whole point of saving it above.
    // doneCount/runCount stay at 0/1 (no "已完成 X/N RUN" clause) --
    // record.status flips to "done" the instant any one RUN finishes
    // (syncRecordMirror), so this loading view never actually has a real,
    // changing doneCount to show; a permanently-static "0/N" would just be
    // noise.
    if (activeBatchRotationRecord !== record || record.procSubStage !== record.status) {
      if (activeBatchRotationRecord && activeBatchRotationRecord !== record) {
        activeBatchRotationRecord.procSubIndex = procSubRotateIndex;
      }
      const startIndex = record.procSubStage === record.status ? (record.procSubIndex || 0) : 0;
      record.procSubStage = record.status;
      startProcSubRotation(analyzing
        ? analyzeSubMessages
        : () => editSubMessages({
            aiPhotoEditing: batchSettings.aiPhotoEditing,
            imageModel: batchSettings.imageModel,
            doneCount: 0,
            runCount: 1
          }), startIndex);
      activeBatchRotationRecord = record;
    }
  }

  function showBatchMainError(record) {
    hideAllBatchMainViews();
    if (!els.batchMainLoading) return;
    els.batchMainLoading.hidden = false;
    els.batchMainLoading.classList.add("is-error");
    if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.thumbUrl || record.previewUrl;
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
      if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.thumbUrl || record.previewUrl;
      if (els.batchMainLoadingEyebrow) els.batchMainLoadingEyebrow.textContent = "Photo Master is ready";
      if (els.batchMainLoadingTitle) els.batchMainLoadingTitle.textContent = "已完成分析";
      if (els.batchMainLoadingText) {
        els.batchMainLoadingText.textContent = "可調整左側的整批補充指令或風格選項，或針對這張圖片單獨繼續處理。";
      }
    }
    if (els.batchReviewContinueBtn) els.batchReviewContinueBtn.hidden = false;
  }

  // Shows a "產生中… N/total RUN" progress bar under the photo for as long
  // as any RUN of the current multi-RUN batch is still "processing" -- the
  // switcher (arrows/count label) only ever displays the first *done* RUN,
  // so without this there's no feedback at all that RUN 2..N are still
  // generating in the background while the user is looking at RUN 1.
  function updateRunProgressBar(runs) {
    if (!els.runProgress) return;
    const runningCount = runs.filter((r) => r.status === "processing").length;
    if (!runningCount) {
      els.runProgress.hidden = true;
      return;
    }
    const doneCount = runs.filter((r) => r.status === "done").length;
    els.runProgress.hidden = false;
    if (els.runProgressLabel) els.runProgressLabel.textContent = `產生中… ${doneCount}/${runs.length} RUN`;
    if (els.runProgressFill) els.runProgressFill.style.width = `${Math.round((doneCount / runs.length) * 100)}%`;
  }

  // Hides/clears the prev/next arrows, the "N / total" label, and the
  // progress bar, and drops the arrows' current run list/index/select-callback.
  function resetRunSwitcher() {
    if (els.runSwitchPrev) els.runSwitchPrev.hidden = true;
    if (els.runSwitchNext) els.runSwitchNext.hidden = true;
    if (els.runCountLabel) { els.runCountLabel.hidden = true; els.runCountLabel.textContent = ""; }
    if (els.runProgress) els.runProgress.hidden = true;
    currentRunList = null;
    currentRunIndex = null;
    currentRunOnSelect = null;
  }

  // Prev/next arrows + "N / total" label for one image's N edit results.
  // Hidden unless there's more than one run. Shared by the batch detail view
  // and the single-image results view; `onSelectDone(idx)` decides what
  // re-pointing to that run means for the caller (batch re-renders via
  // syncRecordMirror+showBatchDoneMain, single via showSingleResult).
  function updateRunSwitcher(runs, currentIdx, onSelectDone) {
    if (runs.length <= 1) {
      resetRunSwitcher();
      return;
    }
    currentRunList = runs;
    currentRunIndex = currentIdx;
    currentRunOnSelect = onSelectDone;
    if (els.runSwitchPrev) els.runSwitchPrev.hidden = false;
    if (els.runSwitchNext) els.runSwitchNext.hidden = false;
    if (els.runCountLabel) {
      els.runCountLabel.hidden = false;
      els.runCountLabel.textContent = `${currentIdx + 1} / ${runs.length}`;
    }
    updateRunProgressBar(runs);
    updateRunSwitcherArrows();
  }

  // Selecting an adjacent run re-points the record's mirror fields at it and
  // re-renders the whole report. `settings` is threaded straight through to
  // the re-render so switching RUN tabs on a historical record (see
  // viewHistoryRecord) keeps using that record's own settings instead of
  // falling back to showBatchDoneMain's batchSettings default.
  function renderRunSwitcher(record, settings = batchSettings) {
    const runs = record.runs || [];
    if (runs.length <= 1) {
      resetRunSwitcher();
      return;
    }

    const doneIdx = runs.findIndex((x) => x.status === "done");
    const current = (record.mirrorIndex != null && runs[record.mirrorIndex]) ? record.mirrorIndex : doneIdx;
    updateRunSwitcher(runs, current, (idx) => {
      record.mirrorIndex = idx;
      syncRecordMirror(record);
      return showBatchDoneMain(record, settings);
    });
  }

  // Single-image counterpart of renderRunSwitcher: reads/writes the
  // module-level singleRuns/singleMirrorIndex instead of a batch record.
  function renderSingleRunSwitcher() {
    const runs = singleRuns || [];
    if (runs.length <= 1) {
      resetRunSwitcher();
      return;
    }

    const doneIdx = runs.findIndex((x) => x.status === "done");
    const current = (singleMirrorIndex != null && runs[singleMirrorIndex]) ? singleMirrorIndex : doneIdx;
    updateRunSwitcher(runs, current, (idx) => {
      singleMirrorIndex = idx;
      return showSingleResult();
    });
  }

  // Reuse the single-image report DOM (renderResult) for the selected record.
  // Set the original image first so resolveCropBoxForDisplay sees its dims.
  // `settings` defaults to the shared live batch state (batchSettings) so
  // every existing call site (renderRunSwitcher/selectBatchRecord) is
  // unaffected -- viewHistoryRecord is the only caller that passes its own,
  // since a historical job's own preset/path/aiPhotoEditing has nothing to do
  // with whatever batch might currently be live in this same session.
  // settings.reviewable (default true) additionally gates whether this looks
  // like a live, revisable result (back-to-process + inline analysis) or a
  // read-only historical one -- re-running a job from another session
  // through the live single/batch revise flow doesn't apply here.
  async function showBatchDoneMain(record, settings = batchSettings) {
    // Load this record/RUN's image into #generated-img itself while it's
    // hidden, before touching anything else -- same reasoning as
    // showSingleResult's identical guard: keeps whatever was on screen (a
    // different record, or an earlier RUN of this one) visible for the
    // whole download instead of tearing it down and repainting broken/
    // progressively. Guards against two kinds of staleness that can happen
    // while this await is in flight: a later RUN landing on this same
    // record (record.imageUrl reassigned by syncRecordMirror), or the user
    // selecting an altogether different record/history entry
    // (currentBatchRecord/historyViewToken) -- either way, whichever call
    // actually reflects the current selection should be the one to render.
    const targetImageUrl = record.imageUrl;
    const guardToken = historyViewToken;
    await setImgSrcPreloaded(els.generatedImg, targetImageUrl);
    if (record.imageUrl !== targetImageUrl) return;
    if (currentBatchRecord !== null && currentBatchRecord !== record) return;
    if (historyViewToken !== guardToken) return;

    // Skip the teardown when the canvas is already showing -- this runs
    // again every time a RUN tab is clicked (same record, just a different
    // run), and hideAllBatchMainViews' restoreAnalysisPreviewHome() would
    // otherwise yank the inline revise panel out from beside the canvas
    // (if the user has it open) even though nothing about which record/view
    // is displayed actually changed.
    const alreadyShowing = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
    if (!alreadyShowing) hideAllBatchMainViews();
    els.resultsCanvasRow.hidden = false;
    const reviewable = settings.reviewable !== false;
    // settings.revealDetailExtra: true only for viewHistoryRecord's admin-
    // reports-drawer call -- shows the prompts button for that one record
    // regardless of debug mode, same override resultsDetailExtra itself
    // already gets. Deliberately not tied to `reviewable` -- that gates the
    // separate live-revise-vs-read-only-preview choice below, not this.
    if (els.showPromptBtn) els.showPromptBtn.hidden = !(isDebugUiActive || settings.revealDetailExtra);
    renderRunSwitcher(record, settings);

    const hasOriginal = !!record.previewUrl;
    const applyRender = () => {
      els.origDim.textContent = hasOriginal
        ? `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`
        : "-";
      // Report the models that actually ran, same label logic as single.
      const extraInfo = record.result["extra-info"] || {};
      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : "本機處理";
      const usedAnalysisModelLabel = extraInfo.analysis_model
        ? (window.PhotoAgentConfig.analysisModels[extraInfo.analysis_model] || extraInfo.analysis_model)
        : window.PhotoAgentConfig.analysisModel;
      const usedModelLabel = isDebugUiActive
      ? `${usedAnalysisModelLabel} + ${usedImageModelLabel}`
      : "AI 智能修圖";
      const data = {
        image: record.imageUrl,
        model: usedModelLabel,
        usage: record.result.token_usage,
        qualityCheck: record.result.quality_check,
        localOnly: !settings.aiPhotoEditing,
        retryCount: 0
      };
      // Point the zip download at the currently-viewed image, same shape the
      // single flow sets so handleDownloadClick works here too. The extra
      // runs field switches the download to an all-runs bundle when this
      // image was edited more than once. sourceUrl is buildImageFiles'
      // fallback for when there's no local File object (record.file) to read
      // straight from -- see fetchImageDownloadData.
      lastResult = { file: record.file, sourceUrl: record.previewUrl, resultText: record.result, jobId: record.jobId, runNumber: record.runNumber, path: settings.path, runs: record.runs };
      updateDownloadLinks(record.runs);
      renderResult({
        data,
        analysis: record.result.analysis,
        preset: settings.preset,
        imageModel: data.model,
        hasOriginal
      });
      populateTimingSection(record.timings);
      // Analysis-preview shows by default alongside the canvas -- no-ops if
      // it's already open (e.g. just switching RUN tabs on the same record).
      // Read-only historical playback shows the very same panel (recipe/
      // style picks, diagnosis) but with every control disabled and no
      // Run/Cancel actions -- re-running a past job through this flow
      // doesn't apply, but the information itself is exactly what's asked
      // for. Re-rendered on every RUN switch too, since each RUN of a
      // multi-RUN job carries its own analysis.
      if (reviewable) {
        openInlineAnalysisForCurrentContext();
      } else {
        renderAnalysisPreview({ analysis: record.result.analysis, preset: settings.preset });
        openInlineAnalysisPanel(true);
      }
    };
    if (!hasOriginal) {
      els.originalImg.removeAttribute("src");
    } else {
      // setImgSrcPreloaded (not a bare src assignment) keeps #original-img
      // from ever showing the browser's broken-image icon/alt text while
      // this record's own photo is a different one than whatever was on
      // screen before.
      await setImgSrcPreloaded(els.originalImg, record.previewUrl);
    }
    applyRender();
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

    renderAnalysisPreview({ analysis: stripDisabledTopicItems(record.analysis, effectiveAdvancedOptions(record)), preset: batchSettings.preset });
    applyBatchReviewSelections(record);
    // Sync (display-only, no state push -- same as viewHistoryRecord below)
    // #ap-image-model-quality/#ap-image-aspect-ratio to THIS case's own
    // effective pick, not whatever another case's revise panel last left
    // them showing.
    const effMq = effectiveModelQuality(record);
    applyModelQualityOption(findModelQualityOptionByPair(effMq.model, effMq.quality));
    applyAnalysisModelOption(findAnalysisModelOption(effectiveAnalysisModel(record)));
    applyAspectRatioOption(findAspectRatioOption(effectiveAspectRatio(record)));
    applyAdvancedOptionsOption(effectiveAdvancedOptions(record));
    if (els.promptInput) els.promptInput.value = record.userPrompt || "";
    if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = !!record.overwritePrompt;
    // Pre-fill with this image's own current RUN count (matching whatever was
    // configured when it last ran) instead of hiding the control -- the
    // bulk #run-count in the sidebar only ever applies to a *first* continue,
    // so a revise needs its own visible/adjustable count, same as single-image.
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = false;
    if (els.apRunCountSelect) els.apRunCountSelect.value = String((record.runs || []).length || 1);
    openInlineAnalysisPanel();
    captureInlineReviseSnapshot();

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
    // Replaces the canvas/run switcher with the same processing view a
    // first-time edit shows (renderBatchMain routes "processing" there) --
    // rather than leaving the old (now-stale) canvas up with a spinner
    // overlaid on it.
    refreshIfSelected(record);

    const actionId = generateActionId();
    await runBatchPool(newRuns.map((run) => ({ record, run, actionId })), editBatchItem, POLL_CONCURRENCY);
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
  // No-ops if the panel is already open -- also called automatically
  // whenever a "done" result first renders, since the panel now shows by
  // default there (see showSingleResult/showBatchDoneMain), not just on an
  // explicit click.
  // forceReopen bypasses the "already open, no-op" guard below -- needed
  // when the record being shown has actually changed (viewHistoryRecord)
  // even though the panel itself was already open from whichever record was
  // shown before. Without it, switching from record A to record B while A's
  // panel was open left it showing A's own recipe checkboxes/style picks
  // (renderAnalysisPreview/applyStyleSelections never re-ran for B), which
  // reads as B "inheriting" A's checked/unchecked state and style pick, and
  // as A's own chip never reappearing when switching back to it later.
  // Every other caller (plain RUN-tab switching, a per-RUN progress update)
  // wants the normal guarded behavior, since nothing about which record is
  // shown actually changed there.
  function openInlineAnalysisForCurrentContext(forceReopen = false) {
    if (!forceReopen && els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis")) return;
    const record = currentDetailBatchRecord();
    if (record) {
      startInlineBatchReview(record);
    } else {
      backToProcessFromResults();
    }
  }

  // Turns whatever populateTimingSection last rendered into #timing-list
  // (kept in sync the same way as the #prompt-* elements below, regardless
  // of #timing-details' own hidden state) into plain aligned lines, since
  // #prompts-modal-content is a <pre> -- padEnd keeps the seconds column
  // lined up the same way appendTimingRow's flex row does visually. The
  // total row gets its own blank-line gap in place of appendTimingRow's
  // border-top separator.
  function timingListAsText() {
    if (!timingList) return "";
    const rows = Array.from(timingList.querySelectorAll(".timing-row"));
    if (!rows.length) return "";
    const lineFor = (row) => {
      const [nameEl, valEl] = row.querySelectorAll("span");
      return `${(nameEl?.textContent || "").padEnd(24)}${valEl?.textContent || ""}`;
    };
    const totalRow = rows.find((r) => r.classList.contains("timing-total"));
    const lines = rows.filter((r) => r !== totalRow).map(lineFor);
    if (totalRow) lines.push("", lineFor(totalRow));
    return lines.join("\n");
  }

  // #show-prompt-btn (debug UI only): lists every pipeline stage that can
  // call GPT, reading each one's prompt text straight off the hidden
  // #prompt-* <pre> elements (renderPrompts, results.js keeps those in sync
  // with whatever result is on screen, batch or single) -- so this needs no
  // separate lookup into batchRecords/singleRuns to figure out which result
  // is currently showing. The trailing 處理時間 tab piggybacks on the very
  // same #timing-list element populateTimingSection keeps in sync instead of
  // its own prompt element, hence `text` instead of `el` -- see
  // timingListAsText above.
  const PROMPT_MODAL_STAGES = [
    { label: "照片分析", el: () => els.promptAnalysis },
    { label: "使用者指令審查", el: () => els.promptReview },
    { label: "裁切驗證", el: () => els.promptCrop },
    { label: "修圖", el: () => els.promptEdit },
    { label: "眼神修正（第二段）", el: () => els.promptGazeRepair },
    { label: "品質檢查", el: () => els.promptQa },
    { label: "處理時間", text: timingListAsText, emptyText: "（尚無處理時間記錄）" },
    { label: "分析原始 JSON", text: analyzeRawJsonText, emptyText: "（尚無分析結果）" }
  ];

  // Raw analyze-stage JSON, exactly as the backend returned it (including
  // internal _advanced_options/_aspect_ratio_pref_at_analysis fields) -- for
  // checking what the analysis actually produced, as opposed to the derived
  // prompt text the other tabs show. Same batch-record/single-flow lookup as
  // currentDetailBatchRecord's other callers, since analysis lives on either
  // depending on which flow is active.
  function analyzeRawJsonText() {
    const batchRecord = currentDetailBatchRecord();
    const analysis = batchRecord ? batchRecord.analysis : (singleFlowState && singleFlowState.analysis);
    return analysis ? JSON.stringify(analysis, null, 2) : "";
  }

  // One tab per stage; clicking a tab is the only thing that changes
  // #prompts-modal-content, so exactly one stage's text is ever shown.
  function renderPromptsModalTabs() {
    if (!els.promptsModalTabs || !els.promptsModalContent) return;
    els.promptsModalTabs.innerHTML = "";
    const stages = PROMPT_MODAL_STAGES.map(({ label, el, text: getText, emptyText }) => {
      const fallback = emptyText || "（此步驟未呼叫 GPT）";
      const text = (getText ? getText() : el()?.textContent)?.trim();
      const empty = !text || text === "–" || text === fallback;
      return { label, text: empty ? fallback : text, empty };
    });
    const buttons = stages.map((stage) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.className = "prompts-modal-tab" + (stage.empty ? " is-empty" : "");
      btn.textContent = stage.label;
      btn.addEventListener("click", () => {
        buttons.forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        els.promptsModalContent.textContent = stage.text;
      });
      els.promptsModalTabs.appendChild(btn);
      return btn;
    });
    if (buttons.length) {
      buttons[0].classList.add("is-active");
      els.promptsModalContent.textContent = stages[0].text;
    }
  }

  function openPromptsModal() {
    if (!els.promptsModal) return;
    renderPromptsModalTabs();
    els.promptsModal.hidden = false;
  }

  function closePromptsModal() {
    if (els.promptsModal) els.promptsModal.hidden = true;
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
  function buildEffectiveAnalysis(analysis, { options, recipeChecked, advancedOptions } = {}) {
    // Applied BEFORE the recipeChecked split below, and on the same
    // `advancedOptions` #ap-recipe-list was just rendered/refreshed with
    // (renderAnalysisPreview's callers, refreshInlineRecipeList) -- so
    // recipeChecked (read straight off however many checkboxes actually got
    // rendered) lines up positionally with `base`'s correction_plan, same as
    // it always has with the raw `analysis` before this existed.
    const base = advancedOptions ? stripDisabledTopicItems(analysis, advancedOptions) : analysis;
    const effective = { ...base };

    // `analysis` already carries its own _advanced_options, baked in at
    // analyze time (pipeline.py's analyze_stage) -- overwrite it with
    // whatever the toggles in #advanced-settings-modal currently read as, so
    // a revise from the results page (where these can now be reopened and
    // changed, see #ap-advanced-settings-btn) actually reaches build_edit_prompt's
    // detected_issues filtering and the gaze-repair pass, both of which read
    // this key off the analysis object at finish time rather than off a
    // fresh request field.
    if (advancedOptions) effective._advanced_options = advancedOptions;

    if (Array.isArray(recipeChecked) && recipeChecked.length) {
      const keepChecked = (arr) =>
        Array.isArray(arr) ? arr.filter((_, i) => recipeChecked[i] !== false) : arr;
      const dropChecked = (arr) =>
        Array.isArray(arr) ? arr.filter((_, i) => recipeChecked[i] === false) : [];
      effective.correction_plan = keepChecked(base.correction_plan);
      effective.retouch_recipe = keepChecked(base.retouch_recipe);
      effective.rejected_corrections = dropChecked(base.correction_plan);
      // retouch_recipe's own dropped text (correction_plan's is the internal/
      // technical description build_edit_prompt reads; this is the
      // human-readable one fillCheckList actually displays) -- without this,
      // a later re-render fed this same (now-shrunk) analysis back -- e.g.
      // viewHistoryRecord revisiting a job whose last revise unchecked
      // something -- has no way to show that item again at all, checked or
      // not; it's just gone.
      effective.rejected_retouch_recipe = dropChecked(base.retouch_recipe);
      // Same index split for the axis tags (correction_axes[i] pairs with
      // correction_plan[i]). These are what let build_edit_prompt strip the
      // matching "do X" clauses out of its own light/color direction and add a
      // "reproduce the original's X" rule -- the rejected item text alone can't
      // drive that, being free prose. The kept axes matter too:
      // resolve_rejected_axes subtracts them, so a rejected "brighten the frame"
      // doesn't lock contrast for an approved "darken the busy background" that
      // shares the tag. It also validates the alignment and flattens the
      // nesting, so nothing here needs to inspect the values.
      effective.correction_axes = keepChecked(base.correction_axes);
      effective.rejected_axes = dropChecked(base.correction_axes);
      // The analysis model also wrote, per item, what to preserve instead if
      // that item gets unchecked (correction_inverse). Only the dropped ones
      // are of any use, and they carry the item's own scope -- "brighten the
      // subject" inverts to pinning the subject, where the generic per-axis
      // rule pins the whole frame and over-suppresses.
      effective.rejected_preserve = dropChecked(base.correction_inverse);
    }

    const styleOptions = {};
    Object.entries(options || {}).forEach(([key, value]) => {
      if (value && value !== "default") styleOptions[key] = value;
    });
    // Always assign (even {}), not just when non-empty -- `effective` starts
    // as a spread of `analysis`, which already has its own style_options
    // from whatever was picked last time (a prior revise, or a historical
    // record's own baked-in pick). Only conditionally assigning meant
    // clearing every pick back to Default left that stale object in place
    // instead of erasing it, so the "cleared" style silently kept applying.
    effective.style_options = styleOptions;

    return effective;
  }

  // Read the radio/checkbox selections back onto the record so per-image choices
  // persist across nav/return, and so editBatchItem can build each record's
  // effective analysis before submission.
  function captureBatchReviewSelections(record) {
    if (!record) return;
    if (els.promptInput) record.userPrompt = els.promptInput.value.trim();
    if (els.promptOverwriteToggle) record.overwritePrompt = els.promptOverwriteToggle.checked;
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
      updateStyleTriggerLabel();
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
    // Undoes viewHistoryRecord's own revealDetailExtra override (admin
    // reviewing a flagged case) -- a fresh upload afterward shouldn't
    // inherit that one record's forced visibility.
    if (resultsDetailExtra) resultsDetailExtra.hidden = true;
    updateResultStatusPill(null);
    selectedFile = null;
    selectedFileSourceUrl = null;
    lastResult = null;
    singleRuns = null;
    singleMirrorIndex = null;
    // A real fresh start (unlike just closing #demo-images-modal without
    // confirming, which leaves the picks in place -- see demoSelectedNames'
    // own doc above) -- next time the picker opens it should show nothing
    // checked, not whatever an unrelated earlier upload had picked.
    demoSelectedNames.clear();
    updateDemoImagesConfirmBtn();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";
    if (previewThumbUrl) URL.revokeObjectURL(previewThumbUrl);
    previewThumbUrl = "";
    originalImgThumbUrl = "";
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
    stopProcSubRotation();
    clearContinueWait();
    // If the last thing the user did was an inline analysis review (single or
    // batch), #analysis-preview and #ap-options-card-inline are currently on
    // loan inside the results section (#results-analysis-slot /
    // #results-canvas-top) -- move them back to the processing section
    // before the next photo's analyze tries to show #analysis-preview there,
    // or it renders invisibly inside the now-hidden results section instead.
    restoreAnalysisPreviewHome();
    restoreOptionsCardHome();
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.remove("showing-analysis");
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = true;
    if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = true;
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
    // Consumed unconditionally (even if the picker was cancelled below) --
    // otherwise a cancelled "add more" click would leave this stuck true for
    // the next unrelated selection.
    const wasAddingMoreToBatch = addingMoreToBatch;
    addingMoreToBatch = false;
    // Some browsers fire "change" with an empty FileList when the user
    // cancels a re-opened file picker (e.g. by clicking the photo zone again)
    // -- treat that as a no-op so whatever was already selected (single
    // photo, batch, or the empty drop zone) stays exactly as it was.
    if (!files.length) return;
    // #dz-add-more's click always means "append to the batch already shown",
    // regardless of how many photos were just picked -- otherwise picking
    // exactly one more would fall through to the single-image branch below
    // and discard the rest of the batch.
    if (wasAddingMoreToBatch) {
      addFilesToBatch(files);
      return;
    }
    if (files.length > 1) {
      setSelectedFileBatch(files);
    } else {
      resetBatchState();
      setSelectedFile(files[0]);
    }
  }

  // ── Importing an exported result folder ───────────────────────────────────
  //
  // Dropping a folder shaped like the debug-UI zip (source photo at the root,
  // run_N/{result.png,result.json} beneath it) loads that run's report and
  // image straight into the results view, and wires "重新處理" to re-run the
  // edit from the SAME analysis. Steps 3-6 then run again exactly as they
  // would for a live run -- including resolve_crop_box_with_report off the
  // stored crop_box -- so a geometry or prompt change can be re-tested against
  // a recorded run without paying for (or re-rolling) the analysis step.

  function resultFolderEntry(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    for (const item of items) {
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry && entry.isDirectory) return entry;
    }
    return null;
  }

  function readDirEntries(dirEntry) {
    const reader = dirEntry.createReader();
    const all = [];
    return new Promise((resolve, reject) => {
      // readEntries returns at most ~100 per call and signals the end with an
      // empty batch, so it has to be drained in a loop.
      const step = () => reader.readEntries((entries) => {
        if (!entries.length) { resolve(all); return; }
        all.push(...entries);
        step();
      }, reject);
      step();
    });
  }

  function entryToFile(fileEntry) {
    return new Promise((resolve, reject) => fileEntry.file(resolve, reject));
  }

  function runNumberOf(name) {
    const match = /^run_(\d+)$/i.exec(name);
    return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
  }

  // Folders exported before analysis.applied_crop_plan existed still recorded
  // the crop that was sent -- as text, in the "Mandatory crop region"
  // paragraph of the edit prompt. Recovering it from there is what lets the
  // box overlay draw 實際送出裁切 for an older folder, instead of silently
  // omitting the one box the whole comparison is about. Returns null for a
  // "No crop." prompt, where there genuinely was no crop to draw.
  function cropPlanFromEditPrompt(promptsJson) {
    const lines = promptsJson?.image_edit;
    const text = Array.isArray(lines) ? lines.join("\n") : (typeof lines === "string" ? lines : "");
    const geometry = /^-\s*x=([\d.]+),\s*y=([\d.]+),\s*w=([\d.]+),\s*h=([\d.]+)/m.exec(text);
    if (!geometry) return null;
    const [x, y, w, h] = geometry.slice(1, 5).map(Number);
    if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;
    const ratio = /^-\s*Target output aspect ratio:\s*([^\n.]+)/m.exec(text);
    // Printed to 3 decimals in the prompt, so these are the sent values to
    // within a rounding step -- fine for drawing, not for arithmetic.
    return { x, y, w, h, aspectRatio: (ratio ? ratio[1] : "").trim() };
  }

  // One image's worth of exported results: the source photo at `dirEntry`'s
  // root plus its run_N subfolders. Returns null when the folder isn't that
  // shape, so the batch walk below can tell "not an image folder" apart from
  // "a broken one".
  async function readImageResultFolder(dirEntry) {
    const root = await readDirEntries(dirEntry);
    // The source photo is the one root-level image that isn't an annotated
    // render (debug.*) or a result copy.
    const sourceEntry = root.find((e) =>
      e.isFile && /\.(jpe?g|png|webp|heic|heif)$/i.test(e.name) && !/^(debug|result)/i.test(e.name));
    const runDirs = root
      .filter((e) => e.isDirectory && /^run_\d+$/i.test(e.name))
      .sort((a, b) => runNumberOf(a.name) - runNumberOf(b.name));
    if (!sourceEntry || !runDirs.length) return { root, image: null };

    const runs = [];
    for (const dir of runDirs) {
      const entries = await readDirEntries(dir);
      const jsonEntry = entries.find((e) => e.isFile && e.name === "result.json");
      if (!jsonEntry) continue;
      const result = JSON.parse(await (await entryToFile(jsonEntry)).text());
      if (!result?.analysis?.subject_category) continue;

      const promptsEntry = entries.find((e) => e.isFile && e.name === "prompts.json");
      if (!result.analysis.applied_crop_plan && promptsEntry) {
        try {
          const prompts = JSON.parse(await (await entryToFile(promptsEntry)).text());
          const recovered = cropPlanFromEditPrompt(prompts);
          if (recovered) result.analysis.applied_crop_plan = recovered;
        } catch {
          // An unreadable prompts.json only costs the one overlay box.
        }
      }

      const imageEntry = entries.find((e) => e.isFile && /^result\.(png|jpe?g|webp)$/i.test(e.name));
      runs.push({
        run: runNumberOf(dir.name),
        jobId: null,
        status: "done",
        imageUrl: imageEntry ? URL.createObjectURL(await entryToFile(imageEntry)) : null,
        result,
        timings: [],
        error: null
      });
    }
    if (!runs.length) return { root, image: null };

    return { root, image: { name: dirEntry.name, file: await entryToFile(sourceEntry), runs } };
  }

  // Accepts either export shape: one image's folder (source + run_N/), or a
  // batch folder whose children are those -- which is what a multi-image
  // download produces ("image 1/", "image 2/", ... each with its own source
  // photo and runs). Returns the images in folder order.
  async function readResultFolder(dirEntry) {
    const { root, image } = await readImageResultFolder(dirEntry);
    if (image) return [image];

    const childDirs = root.filter((e) => e.isDirectory).sort((a, b) => collateName(a.name, b.name));
    if (!childDirs.length) {
      throw new Error("資料夾裡找不到原圖與 run_N 子資料夾。");
    }

    const images = [];
    const skipped = [];
    for (const child of childDirs) {
      try {
        const nested = await readImageResultFolder(child);
        if (nested.image) images.push(nested.image);
        else skipped.push(child.name);
      } catch {
        skipped.push(child.name);
      }
    }
    if (!images.length) {
      throw new Error("子資料夾裡都找不到可用的 result.json（需含 analysis）。");
    }
    // Say what was left out rather than quietly importing a subset.
    if (skipped.length) {
      showError(`已略過 ${skipped.length} 個無法讀取的子資料夾：${skipped.slice(0, 5).join("、")}${skipped.length > 5 ? " …" : ""}`);
    }
    return images;
  }

  // "image 2" before "image 10" -- plain string sort puts them the other way,
  // and these folders are numbered, not zero-padded.
  function collateName(a, b) {
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  }

  // The conditions an imported run was produced under, recovered from its own
  // result.json so a re-run reproduces them instead of picking up whatever the
  // UI happens to be set to. Shared by both import paths.
  function importedRunSettings(result) {
    const extraInfo = result["extra-info"] || {};
    // Matched on the option's own model/quality fields rather than a composed
    // "model:quality" key -- not every option uses that form (nano-banana-2 has
    // no quality tier), and a miss here would silently swap the model.
    const options = window.PhotoAgentConfig.imageModelQualityOptions || [];
    const modelQualityOpt =
      options.find((o) => o.model === extraInfo.image_model && o.quality === extraInfo.image_quality)
      || options.find((o) => o.model === extraInfo.image_model)
      || {};
    const presetKey = "natural";
    return {
      modelQualityOpt,
      presetKey,
      preset: presets[presetKey] || presets.natural,
      imageQuality: modelQualityOpt.quality || extraInfo.image_quality || "medium",
      imageModel: modelQualityOpt.model || extraInfo.image_model || "gpt-image-2",
      analysisModel: extraInfo.analysis_model || "gpt-4.1-mini",
      // The pref this run's EDIT was made under (recorded by finish_stage).
      // _aspect_ratio_pref_at_analysis is the pre-ratio-neutral-analysis field,
      // read only so folders exported back then still re-run as they were.
      aspectRatio: result.analysis?._aspect_ratio_pref_at_edit
        || result.analysis?._aspect_ratio_pref_at_analysis
        || "auto",
      // extra-info.image_model is null exactly when the run was local-only.
      aiPhotoEditing: Boolean(extraInfo.image_model),
      // A skipped QA writes an empty `checks` object; a QA that ran (or ran and
      // failed into the fallback report) always has keys in it.
      runQa: Object.keys(result.quality_check?.checks || {}).length > 0,
      path: "local"                  // /rerun is a local-queue route only
    };
  }

  async function importResultFolder(dirEntry) {
    hideError();
    try {
      const images = await readResultFolder(dirEntry);
      resetBatchState();
      resetSteps();
      clearContinueWait();
      if (images.length > 1) await importAsBatch(images);
      else await importAsSingle(images[0], dirEntry.name);
    } catch (error) {
      showError(`無法讀取結果資料夾：${friendlyError(error)}`);
    }
  }

  async function importAsSingle(image, folderName) {
    const analysis = image.runs[0].result.analysis;
    const settings = importedRunSettings(image.runs[0].result);

    // setSelectedFile rejects an unsupported type by showing its own error
    // and returning, rather than throwing -- so check it actually took.
    await setSelectedFile(image.file);
    if (selectedFile !== image.file) return;

    // Mirrors the picks into both selects (and their picker labels) so the
    // review panel opens showing the model/長寬比 this folder was produced with.
    if (settings.modelQualityOpt.value) applyModelQualityChange(settings.modelQualityOpt.value);
    applyAnalysisModelChange(settings.analysisModel);
    applyAspectRatioChange(settings.aspectRatio);
    singleFlowState = {
      analysis,
      analyzeJobId: null,            // no prior job -> rerunJob, not continueJob
      importedFrom: folderName,
      presetKey: settings.presetKey,
      preset: settings.preset,
      imageQuality: settings.imageQuality,
      imageModel: settings.imageModel,
      analysisModel: settings.analysisModel,
      aspectRatio: settings.aspectRatio,
      aiPhotoEditing: settings.aiPhotoEditing,
      runQa: settings.runQa,
      processingPath: settings.path,
      analyzeTimings: [],
      useCombinedJob: false
    };
    // Restores the style picks and prompt the imported run was produced with,
    // so the review panel opens showing them rather than defaults.
    lastSingleSelections = {
      userPrompt: analysis.user_instruction?.normalized_user_direction || "",
      options: analysis.style_options || undefined,
      recipeChecked: null
    };

    singleRuns = image.runs;
    singleMirrorIndex = 0;
    showSection("results");
    showSingleResult();
  }

  // A multi-image export lands in the batch view already finished: one sidebar
  // record per image, each holding its own runs. From there the per-image
  // revise panel re-runs it exactly as a live batch would, via /rerun (each
  // record's analyzeJobId stays null).
  async function importAsBatch(images) {
    const settings = importedRunSettings(images[0].runs[0].result);
    batchSettings = {
      presetKey: settings.presetKey,
      preset: settings.preset,
      imageQuality: settings.imageQuality,
      imageModel: settings.imageModel,
      analysisModel: settings.analysisModel,
      aspectRatio: settings.aspectRatio,
      aiPhotoEditing: settings.aiPhotoEditing,
      runQa: settings.runQa,
      path: settings.path
    };
    if (settings.modelQualityOpt.value) applyModelQualityChange(settings.modelQualityOpt.value);
    applyAspectRatioChange(settings.aspectRatio);

    batchRecords = [];
    for (const image of images) {
      const analysis = image.runs[0].result.analysis;
      const record = makeBatchRecord(image.file, await previewableBlob(image.file));
      record.analysis = analysis;
      record.analyzeJobId = null;    // -> rerunJob
      // effectiveOptions only reads a record's own options for categories
      // marked touched -- otherwise it falls back to the batch-level radios
      // (all "default"), which would drop the picks this run was made with.
      record.options = analysis.style_options || null;
      record.optionsTouched = {};
      Object.entries(analysis.style_options || {}).forEach(([key, value]) => {
        if (value && value !== "default") record.optionsTouched[key] = true;
      });
      record.userPrompt = analysis.user_instruction?.normalized_user_direction || "";
      record.runs = image.runs;
      record.mirrorIndex = 0;
      syncRecordMirror(record);      // fills status/result/imageUrl/jobId from run 1
      batchRecords.push(record);
    }

    els.batchSidebar.hidden = false;
    if (els.resultsBody) els.resultsBody.classList.add("batch-mode");
    currentBatchRecord = batchRecords[0] || null;
    // showSection first: renderBatchMain measures the sidebar's real on-page
    // position to cap its height, which reads as all-zero while the section
    // is still display:none.
    showSection("results");
    renderBatchSidebarList();
    renderBatchMain(currentBatchRecord);
    updateBatchDownloadAllVisibility();
  }

  els.fileInput.addEventListener("change", () => routeSelectedFiles(els.fileInput.files));
  // Once a photo (or batch) is selected it replaces the drop-zone label, so
  // clicking the preview re-opens the file picker instead.
  els.dzPreview.addEventListener("click", () => els.fileInput.click());
  // Only the trailing #dz-add-more tile reopens the file picker -- clicking
  // an existing thumbnail does nothing (previously the whole strip did,
  // which read as "click anywhere to start over" rather than "click here to
  // add more"). addingMoreToBatch tells routeSelectedFiles to append instead
  // of replacing/switching to single-image mode.
  els.dzMulti.addEventListener("click", (e) => {
    if (!e.target.closest(".dz-add-more")) return;
    addingMoreToBatch = true;
    els.fileInput.click();
  });
  if (els.dzClearAllBtn) els.dzClearAllBtn.addEventListener("click", resetApp);
  if (els.demoImagesBtn) els.demoImagesBtn.addEventListener("click", openDemoImagesModal);
  if (els.demoImagesModalClose) els.demoImagesModalClose.addEventListener("click", closeDemoImagesModal);
  if (els.demoImagesConfirmBtn) els.demoImagesConfirmBtn.addEventListener("click", confirmDemoImageSelection);
  if (els.demoImagesModal) {
    els.demoImagesModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeDemoImagesModal();
    });
  }
  els.startButton.addEventListener("click", startSubmit);
  if (els.startDirectBtn) els.startDirectBtn.addEventListener("click", () => startSubmit({ skipReview: true }));
  els.showPromptBtn.addEventListener("click", openPromptsModal);
  if (els.promptsModalClose) els.promptsModalClose.addEventListener("click", closePromptsModal);
  if (els.promptsModal) {
    els.promptsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closePromptsModal();
    });
  }
  els.resetButton.addEventListener("click", resetApp);
  if (els.logoHomeBtn) els.logoHomeBtn.addEventListener("click", resetApp);
  if (els.backToUploadBtn) els.backToUploadBtn.addEventListener("click", resetApp);
  if (els.compareToggleBtn) {
    els.compareToggleBtn.addEventListener("click", () =>
      setCompareView(!els.compareCard?.classList.contains("showing-generated")));
  }
  if (els.compareFullscreenBtn) els.compareFullscreenBtn.addEventListener("click", toggleCompareFullscreen);
  if (els.compareFullscreenBackdrop) els.compareFullscreenBackdrop.addEventListener("click", closeCompareFullscreen);
  initReportBtnDrag();
  if (els.reportModalClose) els.reportModalClose.addEventListener("click", closeReportModal);
  if (els.reportModalCancelBtn) els.reportModalCancelBtn.addEventListener("click", closeReportModal);
  if (els.reportModalSubmitBtn) els.reportModalSubmitBtn.addEventListener("click", handleReportSubmit);
  if (els.reportModalScreenshotCaptureBtn) {
    els.reportModalScreenshotCaptureBtn.addEventListener("click", handleReportModalScreenshotCapture);
  }
  if (els.reportModalScreenshotPreviewBtn) {
    // Reuses the admin side's own #report-screenshot-modal for the
    // enlarged view -- the local blob: URL works there exactly like the
    // backend's reportScreenshotUrl does, it's still just an <img src>.
    els.reportModalScreenshotPreviewBtn.addEventListener("click", () => {
      if (reportModalScreenshotObjectUrl) openReportScreenshotModal(reportModalScreenshotObjectUrl);
    });
  }
  if (els.reportModalScreenshotDeleteBtn) {
    // "Delete the screenshot" and "reset the preview back to idle" are the
    // same end state, so this reuses resetReportModalScreenshotPreview
    // directly rather than a separate handler.
    els.reportModalScreenshotDeleteBtn.addEventListener("click", resetReportModalScreenshotPreview);
  }
  if (els.reportModal) {
    els.reportModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeReportModal();
    });
  }
  if (els.boxesToggleBtn) {
    // The boxes are in original-image coordinates, so turning them on also
    // flips the canvas back to Before -- drawing them over the result would
    // point at the wrong content (see drawAnalysisBoxes in results.js).
    els.boxesToggleBtn.addEventListener("click", () => {
      const visible = toggleAnalysisBoxes();
      if (visible) setCompareView(false);
      els.boxesToggleBtn.classList.toggle("is-active", visible);
      els.boxesToggleBtn.setAttribute("aria-label", visible ? "隱藏分析框" : "顯示分析框");
    });
  }
  // The overlay is positioned against the photo's rendered rect, which moves
  // whenever the image or the layout changes.
  if (els.originalImg) els.originalImg.addEventListener("load", () => drawAnalysisBoxes());
  window.addEventListener("resize", () => drawAnalysisBoxes());
  els.downloadLink.addEventListener("click", handleDownloadClick);
  if (els.downloadCurrentBtn) els.downloadCurrentBtn.addEventListener("click", handleDownloadCurrentBtnClick);
  if (els.downloadAllRunsBtn) els.downloadAllRunsBtn.addEventListener("click", handleDownloadAllRunsBtnClick);
  if (els.reloadBatchBtn) els.reloadBatchBtn.addEventListener("click", reloadBatch);
  if (els.errorModalClose) els.errorModalClose.addEventListener("click", closeErrorModal);
  if (els.errorModal) {
    els.errorModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeErrorModal();
    });
  }
  if (els.noticeModalOkBtn) els.noticeModalOkBtn.addEventListener("click", closeNoticeModal);
  if (els.noticeModal) {
    els.noticeModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeNoticeModal();
    });
  }
  if (els.confirmModal) {
    els.confirmModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) confirmModalHandlers?.cancel();
    });
  }
  if (els.accountBadge) els.accountBadge.addEventListener("click", openHistoryPage);
  if (els.adminReportsBtn) els.adminReportsBtn.addEventListener("click", openReportsPage);
  if (els.reportsRefreshBtn) els.reportsRefreshBtn.addEventListener("click", refreshReportsList);
  if (els.reportsDrawerCloseBtn) els.reportsDrawerCloseBtn.addEventListener("click", closeReportsDrawer);
  if (els.reportsDrawerBackdrop) els.reportsDrawerBackdrop.addEventListener("click", closeReportsDrawer);
  if (els.reportScreenshotModalClose) els.reportScreenshotModalClose.addEventListener("click", closeReportScreenshotModal);
  if (els.reportScreenshotModal) {
    els.reportScreenshotModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeReportScreenshotModal();
    });
  }
  if (els.historyDrawerCloseBtn) els.historyDrawerCloseBtn.addEventListener("click", closeHistoryDrawer);
  if (els.historyDrawerBackdrop) els.historyDrawerBackdrop.addEventListener("click", closeHistoryDrawer);
  if (els.historyLogoutBtn) els.historyLogoutBtn.addEventListener("click", handleLogout);
  if (els.historyClearAllBtn) els.historyClearAllBtn.addEventListener("click", handleClearAllHistory);
  if (els.historySelectBtn) els.historySelectBtn.addEventListener("click", enterHistorySelectMode);
  if (els.historySelectCancelBtn) els.historySelectCancelBtn.addEventListener("click", exitHistorySelectMode);
  if (els.historyDownloadSelectedBtn) els.historyDownloadSelectedBtn.addEventListener("click", handleDownloadSelectedHistoryClick);
  if (els.historyDeleteSelectedBtn) els.historyDeleteSelectedBtn.addEventListener("click", handleDeleteSelectedHistoryClick);
  if (els.registerAccountBtn) els.registerAccountBtn.addEventListener("click", openAccountModal);
  if (els.accountModalSkipBtn) els.accountModalSkipBtn.addEventListener("click", closeAccountModal);
  if (els.accountModalCreateBtn) els.accountModalCreateBtn.addEventListener("click", handleCreateAccount);
  if (els.accountModal) {
    els.accountModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeAccountModal();
    });
  }
  if (els.headerSettingsBtn) els.headerSettingsBtn.addEventListener("click", openContextSettingsModal);
  if (els.settingsModalClose) els.settingsModalClose.addEventListener("click", closeSettingsModal);
  if (els.settingsModal) {
    els.settingsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeSettingsModal();
    });
  }
  if (els.advancedSettingsBtn) els.advancedSettingsBtn.addEventListener("click", openAdvancedSettingsModal);
  if (els.advancedSettingsModalClose) els.advancedSettingsModalClose.addEventListener("click", closeAdvancedSettingsModal);
  if (els.advancedSettingsModal) {
    els.advancedSettingsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeAdvancedSettingsModal();
    });
  }
  if (els.apSettingsModalClose) els.apSettingsModalClose.addEventListener("click", closeApSettingsModal);
  if (els.apSettingsModal) {
    els.apSettingsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeApSettingsModal();
    });
  }
  if (els.apAdvancedSettingsBtn) els.apAdvancedSettingsBtn.addEventListener("click", openAdvancedSettingsModal);
  if (els.apRecipePicker) els.apRecipePicker.addEventListener("click", openRecipeModal);
  if (els.apRecipeModalClose) els.apRecipeModalClose.addEventListener("click", closeRecipeModal);
  if (els.apRecipeModal) {
    els.apRecipeModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeRecipeModal();
    });
  }
  if (els.apStylePicker) els.apStylePicker.addEventListener("click", openStyleModal);
  if (els.apStyleModalClose) els.apStyleModalClose.addEventListener("click", closeStyleModal);
  if (els.apStyleModal) {
    els.apStyleModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeStyleModal();
    });
  }
  document.querySelectorAll(".ap-style-type-btn").forEach((btn) => {
    btn.addEventListener("click", () => showStyleGroup(btn.dataset.styleGroup));
  });
  if (els.apStyleExitBtn) els.apStyleExitBtn.addEventListener("click", () => showStyleGroup(null));
  // The in-modal clear "x" on each Pose/Angle/Lighting/Size pill (a sibling
  // of the pill's own button, not nested inside it -- see the CSS comment
  // in index.html for why) -- same clearStylePick reset-to-default path the
  // picked-chip row's own "x" uses.
  document.querySelectorAll(".ap-style-type-clear").forEach((btn) => {
    btn.addEventListener("click", () => clearStylePick(`ap-${btn.dataset.styleGroup}`));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (els.errorModal && !els.errorModal.hidden) closeErrorModal();
    if (els.noticeModal && !els.noticeModal.hidden) closeNoticeModal();
    if (els.confirmModal && !els.confirmModal.hidden) confirmModalHandlers?.cancel();
    if (els.accountModal && !els.accountModal.hidden) closeAccountModal();
    if (els.reportModal && !els.reportModal.hidden) closeReportModal();
    if (els.reportScreenshotModal && !els.reportScreenshotModal.hidden) closeReportScreenshotModal();
    if (els.advancedSettingsModal && !els.advancedSettingsModal.hidden) {
      closeAdvancedSettingsModal();
      return;
    }
    if (els.settingsModal && !els.settingsModal.hidden) closeSettingsModal();
    if (els.demoImagesModal && !els.demoImagesModal.hidden) closeDemoImagesModal();
    if (els.apSettingsModal && !els.apSettingsModal.hidden) closeApSettingsModal();
    if (els.apRecipeModal && !els.apRecipeModal.hidden) closeRecipeModal();
    if (els.apStyleModal && !els.apStyleModal.hidden) closeStyleModal();
    if (els.promptsModal && !els.promptsModal.hidden) closePromptsModal();
    if (isCompareFullscreen()) closeCompareFullscreen();
    if (els.batchSidebar && els.batchSidebar.classList.contains("drawer-open")) closeBatchDrawer();
    if (els.historyDrawer && els.historyDrawer.classList.contains("drawer-open")) closeHistoryDrawer();
    if (els.reportsDrawer && els.reportsDrawer.classList.contains("drawer-open")) closeReportsDrawer();
  });
  if (els.batchContinueAllBtn) els.batchContinueAllBtn.addEventListener("click", batchContinueAll);
  if (els.batchDownloadAllBtn) els.batchDownloadAllBtn.addEventListener("click", handleDownloadAllBatchClick);
  if (els.batchDrawerTrigger) els.batchDrawerTrigger.addEventListener("click", openBatchDrawer);
  if (els.batchSidebarCloseBtn) els.batchSidebarCloseBtn.addEventListener("click", closeBatchDrawer);
  if (els.batchSidebarBackdrop) els.batchSidebarBackdrop.addEventListener("click", closeBatchDrawer);
  if (els.batchReviewContinueBtn) {
    els.batchReviewContinueBtn.addEventListener("click", () => {
      if (!currentBatchRecord) return;
      continueBatchRecords([currentBatchRecord]);
    });
  }

  // Per-image ap-*-list radios: their <ul> elements are stable across
  // renderAnalysisPreview's re-fills (only the <li> children get replaced),
  // so a delegated listener attached once here keeps working for every
  // future render. Picking an actual option returns straight to the
  // pills-only view (showStyleGroup(null)), same as #ap-style-type-row's own
  // click-away -- the modal itself stays open so the user can go pick
  // another category next. Clearing one back to "Auto" instead (a card/
  // chip's own "x" -- clearStylePick, results.js, dispatches this same
  // "change" event with value "default") deliberately stays in this grid,
  // so the user can immediately pick a different option in it.
  [["apPoseList", "pose"], ["apAngleList", "angle"], ["apLightingList", "lighting"], ["apSizeList", "size"]]
    .forEach(([elKey, key]) => {
      if (els[elKey]) {
        els[elKey].addEventListener("change", (e) => {
          markOptionTouched(key);
          syncStyleTrigger(els[elKey]);
          updateStyleTriggerLabel();
          if (e.target.value !== "default") showStyleGroup(null);
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
    // A dropped DIRECTORY doesn't appear in dataTransfer.files at all, so the
    // entry API is the only way to see it. An exported result folder goes
    // down the re-run path; anything else falls through to the normal
    // photo-selection path.
    const dirEntry = isDebugUiActive ? resultFolderEntry(event.dataTransfer) : null;
    if (dirEntry) {
      importResultFolder(dirEntry);
      return;
    }
    routeSelectedFiles(event.dataTransfer.files);
  });
})();
