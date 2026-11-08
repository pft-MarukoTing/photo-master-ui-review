(() => {
  // Product default: analysis followed by ONE image-edit call.  Step 5b is an
  // optional second image edit, not the analysis stage. Keep this explicit
  // instead of deriving the default from an HTML checkbox, since browsers may
  // restore checkbox state across reloads.
  const DEFAULT_SINGLE_PASS_EDIT = true;

  const { els, showSection, showError, hideError, resetSteps, addTiming, getTimings } = window.PhotoAgentDom;
  const { presets } = window.PhotoAgentConfig;
  const {
    fetchWithRetry,
    submitJob,
    pollJobStatus,
    getJobResult,
    getJobRuns,
    resultImageUrl,
    preEditImageUrl,
    resultThumbnailUrl,
    sourceImageUrl,
    sourceThumbnailUrl,
    getDemoImages,
    demoImageUrl,
    demoImageThumbnailUrl,
    getUiConfig,
    analyzeJob,
    analyzeJobBatch,
    analyzeExistingJob,
    analyzeAndFinishExistingJob,
    getAnalyzeResult,
    continueJob,
    continueVersionJob,
    rerunJob,
    reframeJob,
    analyzeAipoc,
    analyzeAipocBatch,
    createAccount,
    getHistory,
    getHistoryCase,
    deleteHistoryEntry,
    clearHistory,
    listChatSessions,
    getChatSession,
    deleteChatSessions,
    versionNote: fetchVersionNote,
    getJobStatusOnce,
    reportJob,
    getReport,
    getReports,
    updateReportStatus,
    deleteReport,
    reportScreenshotUrl,
    getCapabilities
  } = window.PhotoAgentApi;
  const { initFromUrl, getCurrentUser, isGuestUser, setCurrentUser, clearCurrentUser, normalizeUsername } = window.PhotoAgentAccount;
  const { renderResult, renderAnalysisPreview, refreshRecipeList, syncStylePicks, clearStylePick, setCompareView, renderTags, closePersonalizePopover, renderBoxTypeMenu, drawAnalysisBoxes, setDebugUi: setResultsDebugUi, setBodyReshapeEnabled, getBodyReshapeReviewEnabled, setBodyReshapeReviewEnabled, previewAspectRatio, syncReframingRatioLock, restoreCompositionState, applyCompositionToRender, isReviewSkipped, getFaceIssueOverrides, applyFaceIssueOverrides, resetFaceReviewState, renderFaceReviewCards, setFindingsCardsShown, setReviewSuggestionsHeld, setFaceIssueCategoryLock, syncFaceReviewAdvancedOptions, syncFaceReviewPoseSuggestion } = window.PhotoAgentResults;
  renderBoxTypeMenu();

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

  // A history version the user hasn't stepped onto yet carries no image or
  // result at all ("unloaded", see fetchHistoryVersionRuns) but is still
  // somewhere the arrows can go -- loadHistoryRun fetches it on arrival.
  // Deliberately distinct from a live revise's "processing" RUN, which is
  // genuinely not selectable yet because its image does not exist anywhere.
  function isSelectableRun(run) {
    return !!run && (run.status === "done" || run.status === "unloaded");
  }

  // The slice of the run list the arrows are allowed to walk: the N images
  // of ONE edit (a run_count>1 result), never across into another version.
  // Moving between versions is the version strip's job -- an arrow that
  // crossed a version boundary would swap the settings, the revise panel and
  // the restored style picks out from under the user while looking like it
  // had only stepped to the next picture.
  //
  // An untagged list (a live revise, a batch record) has no versions, so the
  // range is simply the whole thing and nothing changes for those.
  function arrowRunRange(runs, idx) {
    const current = runs[idx];
    if (!current || !current.versionId) return { from: 0, to: runs.length - 1 };
    let from = idx;
    let to = idx;
    while (from > 0 && runs[from - 1].versionId === current.versionId) from -= 1;
    while (to < runs.length - 1 && runs[to + 1].versionId === current.versionId) to += 1;
    return { from, to };
  }

  // The N images of the NEWEST edit in a run list -- the version-tagged tail
  // for a list rebuilt from a case's history (fetchHistoryVersionRuns /
  // refreshBatchVersionsAfterEdit), the whole list for an untagged one (a
  // live revise). Everything that has always meant "this photo's current
  // edit" -- the sidebar's 美化完成 (n/N) badge, 下載全部, the revise panel's
  // pre-filled 生成張數 -- reads this instead of the full list, so growing
  // a batch record's list into its whole history changes none of them.
  function latestVersionRuns(runs) {
    if (!Array.isArray(runs) || !runs.length) return [];
    const latestId = runs[runs.length - 1].versionId;
    return latestId ? runs.filter((r) => r.versionId === latestId) : runs;
  }

  // The run a batch record is currently showing (its mirror), or null.
  function batchRecordShownRun(record) {
    const runs = record && record.runs;
    if (!Array.isArray(runs) || !runs.length || record.mirrorIndex == null) return null;
    return runs[record.mirrorIndex] || null;
  }

  // The images of whichever edit a batch record is currently showing -- its
  // version's slice of a version-tagged list, the whole list otherwise.
  function batchRecordShownVersionRuns(record) {
    const runs = (record && record.runs) || [];
    const shown = batchRecordShownRun(record);
    return shown && shown.versionId ? runs.filter((r) => r.versionId === shown.versionId) : runs;
  }

  // Is this batch record currently showing an image from an OLDER version
  // than its newest one (stepped onto via the 美化紀錄 strip)? The sidebar
  // keys off this to keep showing what the photo currently IS rather than
  // whatever older version the main view happens to be on.
  function batchRecordOnOlderVersion(record) {
    const runs = record && record.runs;
    const shown = batchRecordShownRun(record);
    const latestId = shown ? runs[runs.length - 1].versionId : null;
    return !!(shown && shown.versionId && latestId && shown.versionId !== latestId);
  }

  // An older version from before a compatibility_version bump can be looked
  // at but not continued from -- continue_job rejects its analysis. The
  // single-image flow's own counterpart is showArchivedAnalysisNotice, whose
  // 重新分析/重新一鍵美化 actions drive the single flow and don't apply here.
  const BATCH_ARCHIVED_VERSION_NOTICE = tr("這一組是用舊版分析產生的，無法以它繼續美化。請在「美化紀錄」切回最新一組後再美化。");

  // Shows/hides and enables/disables the arrows for whatever the current RUN
  // is -- called after every updateRunSwitcher and whenever the index changes.
  function updateRunSwitcherArrows() {
    if (!els.runSwitchPrev || !els.runSwitchNext || !currentRunList || currentRunIndex == null) return;
    const { from, to } = arrowRunRange(currentRunList, currentRunIndex);
    // Hidden outright, not just disabled, when this version produced a single
    // image: most versions do, and a permanently dead pair of arrows sitting
    // over every one of them reads as something being broken.
    const usable = to > from;
    els.runSwitchPrev.hidden = !usable;
    els.runSwitchNext.hidden = !usable;
    // The counter labels what the arrows do, so it appears and disappears
    // with them. Which VERSION is on screen is the strip's job -- it names
    // every one of them and highlights the current -- so repeating it over
    // the photo was only noise.
    if (els.runCountLabel) {
      els.runCountLabel.hidden = !usable;
      els.runCountLabel.textContent = usable ? runSwitcherLabel(currentRunList, currentRunIndex) : "";
    }
    if (!usable) return;
    els.runSwitchPrev.disabled = !currentRunList.slice(from, currentRunIndex).some(isSelectableRun);
    els.runSwitchNext.disabled = !currentRunList.slice(currentRunIndex + 1, to + 1).some(isSelectableRun);
  }

  // Steps to the next/previous *selectable* RUN (see isSelectableRun),
  // skipping over any still-processing/errored ones. Shows #run-switch-loading
  // over the still-visible current RUN (and disables both arrows) for as long
  // as onSelectDone takes -- its target RUN's image can take a moment to fetch,
  // and for a not-yet-loaded history version onSelectDone is what goes and
  // fetches it, so without this the button press looked like it did nothing
  // until the new image suddenly popped in.
  // Switches to the RUN at `idx`, whatever asked for it -- an arrow stepping
  // one place (selectAdjacentRun below) or a version-strip tile jumping
  // straight there. Everything both need in common lives here: the loading
  // overlay, locking the controls for the duration, and recomputing the
  // arrows for wherever we actually landed.
  // Raises #run-switch-loading only once a switch outlasts the delay, so a fast
  // image load doesn't flash it; returns the function that takes it down.
  const RUN_SWITCH_LOADING_DELAY_MS = 250;
  function showRunSwitchLoadingIfSlow() {
    const timer = setTimeout(() => {
      if (els.runSwitchLoading) els.runSwitchLoading.hidden = false;
    }, RUN_SWITCH_LOADING_DELAY_MS);
    return () => {
      clearTimeout(timer);
      if (els.runSwitchLoading) els.runSwitchLoading.hidden = true;
    };
  }

  async function selectRunAt(idx) {
    if (!currentRunList || currentRunOnSelect == null || currentRunIndex == null) return;
    if (idx < 0 || idx >= currentRunList.length || idx === currentRunIndex) return;
    if (!isSelectableRun(currentRunList[idx])) return;
    // Nothing unsent is asked about: while 調整中 the version landed on is
    // only looked at, the panel staying as it is (renderSingleRunSwitcher),
    // and otherwise there is nothing unsent.
    // Any real switch (tile or arrow) leaves the 原圖 pick -- the run it
    // lands on renders on its After side (renderResult -> setCompareView).
    clearVersionStripOriginPick();
    const hideSwitchLoading = showRunSwitchLoadingIfSlow();
    if (els.runSwitchPrev) els.runSwitchPrev.disabled = true;
    if (els.runSwitchNext) els.runSwitchNext.disabled = true;
    if (els.versionStrip) els.versionStrip.classList.add("is-busy");
    try {
      await currentRunOnSelect(idx);
      syncVersionStripCurrent();
    } finally {
      hideSwitchLoading();
      if (els.versionStrip) els.versionStrip.classList.remove("is-busy");
      // Recomputes each arrow's real enabled/disabled state for the RUN we
      // landed on -- may differ from what we forced above, and must still
      // run even if onSelectDone above threw or bailed early.
      updateRunSwitcherArrows();
    }
  }

  async function selectAdjacentRun(delta) {
    if (!currentRunList || currentRunIndex == null) return;
    const { from, to } = arrowRunRange(currentRunList, currentRunIndex);
    let idx = currentRunIndex + delta;
    while (idx >= from && idx <= to && !isSelectableRun(currentRunList[idx])) idx += delta;
    if (idx < from || idx > to) return;
    return selectRunAt(idx);
  }

  if (els.runSwitchPrev) els.runSwitchPrev.addEventListener("click", () => selectAdjacentRun(-1));
  if (els.runSwitchNext) els.runSwitchNext.addEventListener("click", () => selectAdjacentRun(1));

  let selectedFile = null;
  // Set only by viewHistoryRecord (local-path history revisit): selectedFile
  // stays null there (there's no local File object for a job pulled back
  // from the backend), so this is what showSingleResult/finishSingleFlow
  // fall back to for the zip download's source entry instead.
  let selectedFileSourceUrl = null;
  // The analysis 「幫我分析」 attached to the history case on screen
  // (api.py's analyze_existing). A 「只照需求改」 case's versions all carry
  // the skipped stub, so applyHistoryVersionContext shows this in its place,
  // whichever version is stepped to. Set by viewHistoryRecord and by
  // adoptSkippedCaseAnalysis.
  let historyCaseAnalysis = null;
  // The job that analysis came from. A 接著這張調整 version's own job edits the
  // finished version it was based on (chat._start_render's srcKeys), so the
  // next 美化 off it with the full analysis starts from this job's original
  // photo instead.
  let historyCaseAnalysisJobId = null;
  // "Who owns the screen right now." Every long-running flow snapshots this
  // when it starts and re-checks it before touching the DOM again, so a job
  // whose result lands after the user has moved on quietly drops its render
  // instead of yanking the screen back to itself. Unlike the batch flow's
  // editBatchItem, which guards its own re-render with
  // refreshIfSelected(record), the single-image flow has no per-record
  // identity to check against -- just "is the screen still showing what I
  // started with."
  //
  // Bumped by everything that hands the screen to something else:
  //   - navigating to a different record (viewHistoryRecord /
  //     viewProcessingHistoryRecord / viewAnalyzeHistoryRecord)
  //   - resetApp -- 回到首頁/the logo abandon whatever is still running for
  //     display purposes
  //   - startSubmit / startSubmitBatch -- a new run owns the screen from the
  //     moment it's submitted
  // The last two matter for a case that otherwise looks like two unrelated
  // actions: revise photo A from the results page, go 回到首頁 mid-job, then
  // 一鍵修圖 photo B. Without a bump in between, A's still-running job would
  // still see its own snapshot as current when it finished and would render
  // A's result over B's screen.
  //
  // NOT bumped by merely opening the history drawer (openHistoryPage), which
  // lists records without selecting one, and so hands the screen to nobody.
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
  // Lifecycle metadata for the case currently shown in the shared result
  // canvas. Backend history uses snake_case; live records use camelCase.
  // Values are Unix seconds so both paths can share the same renderer.
  let currentCaseTimes = { uploadedAt: null, modifiedAt: null, compatibilityVersion: null, compatible: null, revealExtra: false };
  // History entries are keyed by image_key. Keep the currently viewed key so
  // a finished history case can be marked as archived when its revise panel
  // discovers that its analysis is no longer compatible.
  let currentCaseHistoryImageKey = null;
  // Adjustments carried over from the chat page for the case #case= opened,
  // applied once that case's review controls are on screen (applyPendingModeHandoff).
  let pendingModeHandoff = null;
  // ENABLE_CHAT_RENDER_FROM_VERSION (/api/config's renderFromVersion): offers
  // 接著這張調整 in 美化紀錄.
  let renderFromVersionEnabled = false;
  // 接著這張調整 on the revise panel: the next 美化 edits this one version's
  // output with the request alone. Lives as long as the singleFlowState it
  // was entered on (owner) -- another version or case replaces that and so
  // ends it. { owner, source: {version_id, job_id, run}, versionNumber,
  // thumbUrl, prefill: {text, style_options} } -- prefill is what the panel
  // opens with: a chat handoff's request, or what was typed before 取消.
  let panelContinuation = null;
  // The request a 從原圖美化 switch left behind: it stays the 個人化需求 there,
  // and switching back to 接著這張調整 on the same version picks it up again.
  // engine is a 設定 change made meanwhile (continuationEngineChanged), in
  // readReviewState's terms: it stays picked on 原圖 too, as the chat's plan does.
  // { owner, versionId, prefill, engine, applied }; prefill null when nothing was typed.
  let panelContinuationStash = null;
  // The other way round: what 從原圖美化 had besides the request (修圖建議
  // checks, 人像 picks, 僅依需求修改, the model…) when a version was picked as
  // the base, put back once the panel is on 原圖 again. For the case on
  // screen (historyViewToken), whichever version that then is: the analysis
  // those picks are on is the case's. { token, review } in readReviewState's terms.
  let panelOriginStash = null;
  // The version the revise panel was last painted for, and the base it
  // opens on there: a version made from another one carries on from itself
  // (從第 N 版美化), any other starts from the original (defaultRun null).
  // While 調整中 the panel stays on it, whatever version is stepped to
  // (renderSingleRunSwitcher). { owner, run, defaultRun }.
  let panelBase = null;
  // Set by enterPanelContinuation while it steps to the version picked as the
  // base: that step is not 調整中's look-only one, the panel goes with it.
  let panelFollowsStep = false;
  // While the base is being switched (switchPanelBase): the steps on the way
  // -- the version's own default base, 原圖 before its edits are put back --
  // are not painted on #ap-continue-base or #ap-editing-hint.
  let panelBaseSwitching = false;
  // The review controls as this case first showed them, to tell whether
  // leaving the page would throw away edits not yet sent with 美化
  // (isReviewDirty). Tied to the singleFlowState it was taken for; dropped
  // once 美化 sends the edits.
  let reviewBaseline = null;
  // Set once the user has already agreed to leave (or the edits went along
  // to the chat page), so beforeunload does not ask a second time.
  let leaveConfirmed = false;

  function timestampValue(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function formatCaseTimestamp(timestamp) {
    const date = new Date(timestamp * 1000);
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function renderCurrentCaseTimes() {
    if (!els.resultCaseTimes) return;
    const { uploadedAt, modifiedAt, compatibilityVersion, compatible, revealExtra } = currentCaseTimes;
    // Upload/modified timestamps are testbed/admin detail, not something a
    // regular user's own result view needs -- same isDebugUiActive-or-
    // revealDetailExtra gate #show-prompt-btn/#results-detail-extra already
    // use elsewhere, so an admin reviewing a flagged case (viewHistoryRecord's
    // admin-reports-drawer call) still sees it outside debug mode.
    els.resultCaseTimes.hidden = !uploadedAt || !(isDebugUiActive || revealExtra);
    if (els.resultUploadedTime) {
      els.resultUploadedTime.textContent = uploadedAt ? formatCaseTimestamp(uploadedAt) : "";
      els.resultUploadedTime.dateTime = uploadedAt ? new Date(uploadedAt * 1000).toISOString() : "";
    }
    if (els.resultModifiedTimeItem) els.resultModifiedTimeItem.hidden = !modifiedAt;
    if (els.resultModifiedTime) {
      els.resultModifiedTime.textContent = modifiedAt ? formatCaseTimestamp(modifiedAt) : "";
      els.resultModifiedTime.dateTime = modifiedAt ? new Date(modifiedAt * 1000).toISOString() : "";
    }
    if (els.resultCompatibilityVersionItem) els.resultCompatibilityVersionItem.hidden = compatibilityVersion == null;
    if (els.resultCompatibilityVersion) {
      if (compatibilityVersion == null) {
        els.resultCompatibilityVersion.textContent = "";
      } else {
        // "產生版本" is this case's own analysis.compatibility_version (stamped
        // when it was analyzed); "系統版本" is what compatibility_version.py's
        // CURRENT_COMPATIBILITY_VERSION this deployment is running now
        // (currentCompatibilityVersion, from /api/config) -- shown side by
        // side so a mismatch is obvious at a glance. compatible is only
        // known where the backend actually told us (viewAnalyzeHistoryRecord's
        // /analyze/result call) -- null elsewhere (fresh analyze, imported
        // result folders, ...), where the two numbers alone still tell the story.
        const parts = [tr("產生版本 v{compatibilityVersion}", { compatibilityVersion })];
        if (currentCompatibilityVersion != null) parts.push(tr("系統版本 v{currentCompatibilityVersion}", { currentCompatibilityVersion }));
        els.resultCompatibilityVersion.textContent = parts.join(" / ") + (compatible === false ? tr("（已過舊，建議重新處理）") : "");
      }
    }
  }

  function setCurrentCaseTimes(source = {}, revealExtra = false) {
    currentCaseHistoryImageKey = source?.image_key || null;
    currentCaseTimes = {
      uploadedAt: timestampValue(source.uploaded_at ?? source.uploadedAt ?? source.ts),
      modifiedAt: timestampValue(source.modified_at ?? source.modifiedAt),
      compatibilityVersion: null,
      compatible: null,
      revealExtra
    };
    renderCurrentCaseTimes();
    syncCaseHash();
  }

  // Keeps #case=<image_key> on the URL while a photo is on screen, so a
  // reload reopens it (openCaseFromHash) instead of landing on the upload
  // page. replaceState, not a new history entry: 上一頁 is unchanged. A
  // photo uploaded on this page has only its jobs until /case resolves one
  // (findCaseForJob), same as the 對話模式 button.
  let caseHashLookupJobId = null;
  function syncCaseHash() {
    const setHash = (key) => {
      const hash = key ? `#case=${encodeURIComponent(key)}` : "";
      if (location.hash === hash) return;
      history.replaceState(history.state, "", location.pathname + location.search + hash);
    };
    if (currentSectionName === "upload") { setHash(null); return; }
    if (currentCaseHistoryImageKey) { setHash(currentCaseHistoryImageKey); return; }
    const user = getCurrentUser();
    const jobId = lastResult?.jobId || singleFlowState?.analyzeJobId;
    if (!user || !jobId || jobId === caseHashLookupJobId) return;
    caseHashLookupJobId = jobId;
    getHistoryCase(user, { jobId })
      .then((res) => {
        const key = res?.case?.image_key;
        // Only if that photo is still the one on screen.
        if (key && currentSectionName !== "upload" && !currentCaseHistoryImageKey
          && (lastResult?.jobId || singleFlowState?.analyzeJobId) === jobId) setHash(key);
      })
      .catch(() => { caseHashLookupJobId = null; }); // not in history yet; try on the next screen change
  }

  // Records which compatibility_version (compatibility_version.py) the case now
  // shown in the result canvas was produced with -- called once that case's
  // analysis object is actually in hand, separately from setCurrentCaseTimes
  // above (which fires first, from the job/history row, before the analysis
  // itself has been fetched). compatible is the compatible flag
  // /analyze/result returns, when the caller has it; omitted callers (fresh
  // analyze, imported result folders) just show the bare version number.
  function setCurrentCaseAnalysisVersion(analysis, { compatible = null } = {}) {
    currentCaseTimes.compatibilityVersion = analysis ? (analysis.compatibility_version ?? 0) : null;
    currentCaseTimes.compatible = compatible;
    renderCurrentCaseTimes();
  }

  function markCurrentCaseUploaded(timestamp = Date.now() / 1000) {
    if (!currentCaseTimes.uploadedAt) currentCaseTimes.uploadedAt = timestamp;
    renderCurrentCaseTimes();
  }

  function markCurrentCaseModified(timestamp = Date.now() / 1000) {
    markCurrentCaseUploaded(timestamp);
    currentCaseTimes.modifiedAt = timestamp;
    renderCurrentCaseTimes();
  }
  // Set once at startup by applyDebugUiVisibility -- gates whether the
  // result card shows the raw model string (e.g. "gpt-4.1-mini + gpt-image-2")
  // or a plain-language stand-in a regular user actually cares about.
  let isDebugUiActive = false;
  // Internal Body Reshape switch. This initial value mirrors config.js and is
  // replaced by the backend's /api/config response below. The server still
  // enforces the flag for every analysis/edit request; this value only controls
  // feature visibility; apply_selections reads the same flag server-side.
  let isBodyReshapeEnabled = window.PhotoAgentConfig?.enableBodyReshape !== false;
  // This deployment's own compatibility_version (compatibility_version.py), fetched
  // via /api/config below as compatibilityVersion -- "compatibility" (not
  // just "version") to stay distinct from /api/version's unrelated frontend/
  // worker build version and from a client's own appVer. Shown in debug mode
  // next to a case's own compatibility version (setCurrentCaseAnalysisVersion)
  // so the two can be compared directly. null until that fetch resolves.
  let currentCompatibilityVersion = null;
  // The floor below which continue_job's own check (backend/routes/api.py)
  // rejects a resumed analysis -- mirrored here so finishSingleFlow (this
  // app's single choke point for every "Run"/revise click, regardless of
  // which history-view path set singleFlowState.analysis) can show a
  // friendly hint before submitting, instead of only surfacing continue_job's
  // generic error after the fact. null until /api/config resolves; treated
  // as "unknown, assume compatible" until then -- see isCompatible.
  let minCompatibleVersion = null;
  let compatibilityConfigRequest = null;

  async function ensureCompatibilityConfigLoaded() {
    if (minCompatibleVersion != null) return;
    if (!compatibilityConfigRequest) {
      compatibilityConfigRequest = getUiConfig().then((cfg) => {
        if (typeof cfg.compatibilityVersion === "number") {
          currentCompatibilityVersion = cfg.compatibilityVersion;
        }
        if (typeof cfg.minCompatibleVersion === "number") {
          minCompatibleVersion = cfg.minCompatibleVersion;
        }
      });
    }
    await compatibilityConfigRequest;
  }

  // Same rule as compatibility_version.py's is_compatible(), evaluated client-side.
  // Not authoritative (continue_job's own check is), just lets the UI bail
  // out early with a specific hint instead of a generic submit error.
  function isCompatible(analysis) {
    if (minCompatibleVersion == null) return true; // config not loaded yet -- fail open, continue_job still gates it
    const compatibilityVersion = (analysis && analysis.compatibility_version) || 0;
    return compatibilityVersion >= minCompatibleVersion;
  }

  function applyBodyReshapeVisibility(enabled) {
    isBodyReshapeEnabled = enabled !== false;
    setBodyReshapeEnabled(isBodyReshapeEnabled);
  }

  // Single-image flow: kept after analyze so "回到流程" (from the results page)
  // can re-show the analysis preview and resubmit steps 3-6 without re-running
  // step 2. lastSingleSelections holds the last submitted prompt/options/recipe
  // checkboxes so re-opening the preview restores them instead of resetting.
  let singleFlowState = null; // { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, aiPhotoEditing, processingPath }
  let lastSingleSelections = null; // { userPrompt, options, recipeChecked, bodyReshapeEnabled }

  // True between showPendingResults and closePendingResults -- #download-link
  // is shared with the finished-result state (gated there on lastResult), so
  // this tells handleDownloadClick to route to the source-photo-only download
  // instead while there's no edited result yet.
  let pendingResultsActive = false;

  // Single-image RUN N times (mirrors batch's per-image runs): populated only
  // when the RUN count on the analysis-preview screen is > 1. singleRuns holds
  // each run's {run, jobId, status, imageUrl, result, timings, error};
  // singleMirrorIndex is which one is currently shown in the results view (the
  // single-image counterpart of a batch record's mirrorIndex).
  let singleRuns = null;
  let singleMirrorIndex = null;
  // Re-sync hook for setupCompactUploadSelectors' own wrap/compact state --
  // set once that function runs (see its own call site below). resetApp
  // calls this after moving #advanced-settings-fold back so the compact
  // selector logic re-measures instead of trusting a stale "compact" flag;
  // see the comment on that call in resetApp for the bug this fixes.
  let refreshCompactUploadSelectors = null;

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
  let batchRecords = [];   // see makeBatchRecord for record shape
  let batchSidebarFilter = "all"; // 「我的照片」列表上面選的狀態篩選（renderBatchSidebarFilters）
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
    if (progressFeedState) {
      progressFeedState.rows.push({ text, id: ++progressFeedState.sequence });
      if (progressFeedState.rows.length > 5) progressFeedState.rows.shift();
      progressFeedState.revision += 1;
      renderProgressFeed();
      return;
    }
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

  // All playback text is local: analysis tips or a snapshot of the UI's
  // submitted review choices. Status polling only watches job lifecycle.
  // Every sentence the feed shows -- analysis tips, edit tips, and the edit
  // plan built from the submitted choices -- goes through
  // progressFeedDisplayText: no leading "正在" (the title above already says
  // it), and the closing "。" becomes "…".
  const PROGRESS_FEED_TIPS = {
    analysis: [
      tr("分析背景的光線與明暗分布"), tr("檢視照片的構圖與取景細節"),
      tr("分析畫面主體與背景的視覺關係"), tr("檢視照片整體的色彩與色調"),
      tr("分析亮部與暗部的細節層次"), tr("檢視畫面中的視覺焦點"),
      tr("分析照片邊緣的留白與干擾元素"), tr("檢視背景細節對主體的影響"),
      tr("分析光線方向與畫面的整體氛圍"), tr("檢視構圖中的空間與視覺平衡"),
      tr("整理照片中值得保留的特色"), tr("整理光線、色彩與構圖的改善建議"),
      tr("檢視前景、中景與遠景的層次關係"), tr("分析畫面中冷色與暖色的搭配"),
      tr("檢視陰影區域的細節與可見度"), tr("分析高光與反光對畫面的影響"),
      tr("檢視主體輪廓與周圍環境的區隔"), tr("分析畫面中線條的延伸與視線引導"),
      tr("檢視不同區域的色彩飽和度"), tr("分析照片中材質與紋理的呈現"),
      tr("檢視取景角度與透視的視覺效果"), tr("分析畫面中光色變化的協調程度"),
      tr("檢視重要細節是否受到邊緣裁切影響"), tr("分析背景元素的排列與疏密關係")
    ],
    edit: [
      tr("調整整體光線與明暗層次"), tr("平衡亮部與暗部的細節"),
      tr("校正白平衡與整體色溫"), tr("細修畫面的色調與飽和度"),
      tr("優化光線，讓畫面更自然"), tr("處理陰影區域的細節表現"),
      tr("讓色彩過渡更加平順"), tr("提升主體的清晰度與立體感"),
      tr("強化畫面中的細節與質感"), tr("修飾主體的質感與邊緣"),
      tr("調整背景與主體的層次關係"), tr("保留原圖中的重要細節"),
      tr("檢查整體畫面的協調與完整")
    ]
  };
  // Edit tips play once; then this one sentence stays on top until the edit ends.
  const PROGRESS_FEED_EDIT_WAIT = tr("美化仍在進行中，請稍候");
  let progressFeedState = null;
  const progressFeedRecords = new WeakMap();
  const progressFeedViews = new WeakMap();
  // Keep five history entries; show the newest three in either orientation,
  // newest on top so each new sentence pushes the older ones down.
  let procSubRotateTimer = null;
  let procSubRotateIndex = 0;
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
  function progressFeedDelay(text) {
    return Math.max(3500, 1500 + Array.from(text.replace(/\s/g, "")).length * 150);
  }
  function progressFeedVisible() {
    return !document.hidden && [procSub, els.canvasLoadingStatus, els.batchMainLoadingText]
      .some((node) => node?.getClientRects().length);
  }
  function progressFeedDisplayText(text) {
    const body = String(text || "").trim().replace(/^正在/, "").replace(/[。.]+$/, "");
    // A sentence that already ends in its own ！？… (say, quoted from the
    // personal direction) keeps it rather than getting a second mark.
    return /[！？!?…]$/.test(body) ? body : body + "…";
  }
  // Stacked (one-column) screens center the feed as a block of left-aligned
  // sentences. The block is as wide as the longest sentence this playback can
  // show -- measured once from the whole pool, not from the three on screen,
  // so its left edge stays put as sentences come and go. CSS uses the width
  // only in the stacked layout. The probe sits on <body> so it measures even
  // while the feed's own section is still hidden; a row's font size depends
  // only on its class and the viewport, the same everywhere it is shown.
  function measureProgressFeedWidth(texts) {
    const probe = document.createElement("span");
    probe.className = "progress-feed-row progress-feed-probe";
    probe.setAttribute("aria-hidden", "true");
    document.body.append(probe);
    let widest = 0;
    for (const text of texts) {
      probe.textContent = text;
      widest = Math.max(widest, probe.getBoundingClientRect().width);
    }
    probe.remove();
    return Math.ceil(widest);
  }
  function progressFeedWidth(state) {
    const texts = [...new Set([...(state.pool || []), ...state.rows.map((row) => row.text)])]
      .map(progressFeedDisplayText);
    const key = `${state.mode}|${texts.length}|${window.innerWidth}`;
    if (state.widthKey !== key) {
      // At most 17 full-width characters: longer sentences wrap onto a full
      // first line, so the block stays centered with room on both sides.
      // English sentences run two to three times longer, so outside Chinese
      // the cap is about 48 letters instead, or nearly every one would wrap.
      const cap = window.PHOTOAGENT_LANG === "zh-TW" ? "國".repeat(17) : "n".repeat(48);
      state.width = Math.min(measureProgressFeedWidth(texts), measureProgressFeedWidth([cap]));
      state.widthKey = key;
    }
    return state.width;
  }
  window.addEventListener("resize", () => renderProgressFeed());
  function renderProgressFeed() {
    const state = progressFeedState;
    if (!state) return;
    const rowLimit = 3;
    const visibleRows = state.rows.slice(-rowLimit).reverse();
    const feedWidth = progressFeedWidth(state);
    [procSub, els.canvasLoadingStatus, els.batchMainLoadingText].forEach((element) => {
      if (!element) return;
      element.classList.add("has-progress-feed");
      let view = progressFeedViews.get(element);
      if (!view || view.state !== state) {
        const head = document.createElement("span");
        head.className = "progress-feed-badge";
        const list = document.createElement("span");
        list.className = "progress-feed-list";
        list.setAttribute("role", "log");
        list.setAttribute("aria-live", "off");
        element.replaceChildren(head, list);
        view = { state, head, list, nodes: new Map(), revision: -1 };
        progressFeedViews.set(element, view);
      }
      if (feedWidth) element.style.setProperty("--progress-feed-width", `${feedWidth}px`);
      // No badge on either feed, so the sentences start at the top.
      view.head.hidden = true;
      view.head.textContent = "";
      if (view.revision === state.revision && view.rowLimit === rowLimit) return;
      const tops = new Map([...view.nodes].map(([id, node]) => [id, node.getBoundingClientRect().top]));
      const nodes = visibleRows.map((entry, index) => {
        let node = view.nodes.get(entry.id);
        if (!node) {
          node = document.createElement("span");
          node.className = "progress-feed-row";
          node.classList.toggle("is-waiting", entry.text === PROGRESS_FEED_EDIT_WAIT);
          const text = document.createElement("span");
          text.className = "progress-feed-text";
          text.textContent = progressFeedDisplayText(entry.text);
          node.replaceChildren(text);
        }
        node.classList.toggle("is-latest", index === 0);
        return node;
      });
      view.list.replaceChildren(...nodes);
      view.nodes = new Map(visibleRows.map((entry, index) => [entry.id, nodes[index]]));
      if (!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) view.nodes.forEach((node, id) => {
        const before = tops.get(id), after = node.getBoundingClientRect().top;
        if (Number.isFinite(before) && before !== after) node.animate?.(
          [{ transform: `translateY(${before - after}px)` }, { transform: "translateY(0)" }],
          { duration: 350, easing: "cubic-bezier(.2,.7,.2,1)" });
      });
      view.revision = state.revision;
      view.rowLimit = rowLimit;
    });
  }
  function nextProgressFeedTip(state) {
    const tips = PROGRESS_FEED_TIPS[state.mode];
    if (state.mode !== "edit") return tips[state.tipIndex++ % tips.length];
    if (state.tipIndex < tips.length) return tips[state.tipIndex++];
    if (state.waitShown) return null;
    state.waitShown = true;
    return PROGRESS_FEED_EDIT_WAIT;
  }
  function scheduleProgressFeed() {
    const state = progressFeedState;
    if (!state || procSubRotateTimer || !progressFeedVisible()) return;
    if (state.waitShown && !state.queue.length) return;
    procSubRotateTimer = setTimeout(() => {
      procSubRotateTimer = null;
      if (state !== progressFeedState || !progressFeedVisible()) return;
      const next = state.queue.shift() || nextProgressFeedTip(state);
      if (!next) return;
      setProcSub(next);
      procSubRotateIndex = ++state.index;
      scheduleProgressFeed();
    }, progressFeedDelay(state.rows.at(-1).text));
  }
  // startIndex resumes a paused batch record's rotation exactly where it
  // left off (see showBatchMainLoading) -- every single-image call site
  // omits it and starts fresh at 0, same as before.
  function startProcSubRotation(getMessages, startIndex = 0, owner = null) {
    const previous = progressFeedState;
    stopProcSubRotation();
    const mode = getMessages === analyzeSubMessages ? "analysis" : "edit";
    const generation = owner?.modifiedAt || owner?.analyzeJobId || null;
    let state = owner && progressFeedRecords.get(owner);
    if (!state || state.generation !== generation) state = null;
    if (!owner && previous?.owner === null && previous.mode === "analysis" && mode === "edit") state = previous;
    if (!state) state = { owner, generation, mode: null, queue: [], rows: [], tipIndex: 0, index: startIndex, sequence: 0, revision: 0 };
    if (state.mode !== mode) {
      state.mode = mode;
      state.queue = mode === "edit" ? [...getMessages()] : [];
      state.tipIndex = 0;
      state.waitShown = false;
      // Everything this playback can show, for the stacked layout's width.
      state.pool = [...state.queue, ...PROGRESS_FEED_TIPS[mode], ...(mode === "edit" ? [PROGRESS_FEED_EDIT_WAIT] : [])];
    }
    progressFeedState = state;
    if (owner) progressFeedRecords.set(owner, state);
    activeBatchRotationRecord = owner;
    procSubRotateIndex = state.index;
    if (!state.rows.length) {
      setProcSub(state.queue.shift() || nextProgressFeedTip(state));
      procSubRotateIndex = ++state.index;
    } else renderProgressFeed();
    scheduleProgressFeed();
    window.requestAnimationFrame?.(scheduleProgressFeed); // Caller may reveal the section after starting.
  }
  function stopProcSubRotation() {
    clearTimeout(procSubRotateTimer);
    procSubRotateTimer = null;
    procSubRotateIndex = 0;
    activeBatchRotationRecord = null;
    progressFeedState = null;
  }
  // Re-renders without advancing, rebuilding the submission snapshot, or
  // restarting the current sentence's reading allowance.
  function refreshProcSubRotation() {
    renderProgressFeed();
    scheduleProgressFeed();
  }
  // Genuine completion ends playback immediately; it never delays results.
  function finishProcSubRotation() {
    stopProcSubRotation();
    return Promise.resolve(); // Results must not wait for decorative text to drain.
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(procSubRotateTimer); procSubRotateTimer = null; }
    else scheduleProgressFeed();
  });
  function analyzeSubMessages() {
    return PROGRESS_FEED_TIPS.analysis;
  }
  function editSubMessages({ aiPhotoEditing, doneCount = 0, runCount = 1, analysis, selections, userPrompt, overwritePrompt, tipsOnly = false }) {
    // One-click editing uses only the scheduler's local edit tips, even on
    // paths where an analysis or personal direction is already available.
    if (tipsOnly) return [];
    const progress = runCount > 1 ? tr("（已完成 {doneCount}/{runCount} 張）", { doneCount, runCount }) : "";
    const messages = window.PhotoAgentResults.buildPlannedEditMessages({
      analysis, selections: selections || {}, userPrompt, overwritePrompt, aiPhotoEditing });
    // Play the submitted choices first, without a generic preamble. The
    // scheduler only cycles edit tips once this entire snapshot is consumed.
    if (messages.length) return messages;
    return [aiPhotoEditing ? tr("AI 正在產生美化結果{progress}。", { progress }) : tr("正在進行本機影像處理{progress}。", { progress })];
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


  // Luna is the only offered analysis model in every mode.
  const DEBUG_ONLY_ANALYSIS_MODELS = [];

  // Sunburst tiers are release options; these candidates are debug-only.
  const DEBUG_ONLY_IMAGE_MODELS = [
    "gpt-image-2.6-sunburst:low",
    "gpt-image-2.6-sunburst:medium",
    "gpt-image-2.6-sunburst:high",
    "gpt-image-2.6-flare:low",
    "gpt-image-2.6-flare:medium",
    "gpt-image-2.6-flare:high",
    "nano-banana-2",
    "MAI-Image-2.6",
  ];

  function applyImageModelOptionLimit(select, isDebug) {
    if (!select) return;
    Array.from(select.options).forEach((opt) => {
      opt.hidden = !isDebug && DEBUG_ONLY_IMAGE_MODELS.includes(opt.value);
    });
    if (!isDebug && DEBUG_ONLY_IMAGE_MODELS.includes(select.value)) {
      select.value = window.PhotoAgentConfig.defaultImageModelQuality || select.value;
    }
  }

  // Outside debug mode, filtering debug-only candidates out of 分析模型/修圖
  // 模型 can leave exactly one visible option -- nothing left to actually
  // choose, so mark the picker `.is-single-option` (index.html CSS drops its
  // arrow and disables the trigger) instead of leaving an openable panel
  // with one, already-selected row in it.
  function applyPickerSingleOptionState(picker, select) {
    if (!picker || !select) return;
    const visibleCount = Array.from(select.options).filter((opt) => !opt.hidden).length;
    picker.classList.toggle("is-single-option", visibleCount <= 1);
  }

  function applyAnalysisModelOptionLimit(select, isDebug) {
    if (!select) return;
    Array.from(select.options).forEach((opt) => {
      opt.hidden = !isDebug && DEBUG_ONLY_ANALYSIS_MODELS.includes(opt.value);
    });
    if (!isDebug && DEBUG_ONLY_ANALYSIS_MODELS.includes(select.value)) {
      select.value = window.PhotoAgentConfig.defaultAnalysisModel || select.value;
    }
  }

  // Testbed-only UI: some rows/panels are hidden unless the backend was
  // started with `--debug-ui` (or the page URL contains "debug", for quick
  // local checks without a real backend). isDebug=false is the shape a
  // normal user sees -- the processing-path and QA-verification rows (true
  // internal/testing knobs) and every deep-dive section (analysis breakdown,
  // workflow steps, timing, prompts) disappear. Image quality/model choices
  // stay visible in both modes for the approved options -- they're choices a
  // regular user cares about, not debug-only -- except for debug-only
  // candidates like MAI-Image-2.6 (see DEBUG_ONLY_IMAGE_MODELS above), which
  // applyImageModelOptionLimit strips from the release picker below.
  // Elements this hides always keep their own independent
  // show/hide logic underneath; this only ever adds an extra "and also not
  // debug" gate on top; it never replaces that logic.
  function applyDebugUiVisibility(isDebug) {
    isDebugUiActive = isDebug;
    if (processingPathRow) processingPathRow.style.display = isDebug ? "" : "none";
    if (isDebug && els.processingPathSelect) els.processingPathSelect.value = "local";
    if (els.runQaRow) els.runQaRow.style.display = isDebug ? "" : "none";
    if (els.twoStageEditRow) els.twoStageEditRow.style.display = isDebug ? "" : "none";
    if (els.findingsCardsRow) els.findingsCardsRow.style.display = isDebug ? "" : "none";
    applyFindingsCardsOption();
    // 生成張數 (RUN count > 1) is being pulled from the release build for now
    // -- hide both pickers (upload-page settings-modal + results-page
    // ap-settings-modal) outside debug mode and pin the value to 1, rather
    // than just capping the visible options like applyRunCountOptionLimit
    // already does below.
    if (els.imageRunCountWrap) els.imageRunCountWrap.style.display = isDebug ? "" : "none";
    if (els.apRunCountWrap && !isDebug) els.apRunCountWrap.hidden = true;
    if (!isDebug) {
      if (els.imageRunCountSelect) els.imageRunCountSelect.value = "1";
      if (els.apRunCountSelect) els.apRunCountSelect.value = "1";
    }
    // 分析引擎 is read-only on the results page in the release build -- Step 2
    // has already run by the time that page exists, so the picker is there to
    // report which model produced the analysis, not to change it. Debug mode
    // is the only build that offers other analysis models at all, and there
    // comparing them is the entire point, so it unlocks the picker -- picking
    // one re-analyzes the photo rather than relabeling an analysis that model
    // never produced (see els.apAnalysisModelSelect's change listener).
    if (els.apAnalysisModelPicker) els.apAnalysisModelPicker.classList.toggle("is-locked", !isDebug);
    // 分析引擎 / 美化引擎 are only offered in debug mode at all (same as the
    // chat page, frontend-chat settings.tsx enginesVisible): a regular user
    // always gets the default engines, so the pickers -- upload page and the
    // results page's 設定 modal alike -- are not shown outside it.
    [els.analysisModelPicker, els.apAnalysisModelPicker, els.imageModelQualityPicker, els.apImageModelQualityPicker]
      .forEach((picker) => {
        const group = picker?.closest(".option-pill-group");
        if (group) group.style.display = isDebug ? "" : "none";
      });
    // Row 1's tracks are laid out for the engine pickers too; without them
    // its two remaining pills get their own centred two-column grid.
    document.querySelector(".upload-inline-settings-row-1")?.classList.toggle("is-engines-hidden", !isDebug);
    // The upload row's compact layout depends on which groups are shown.
    refreshCompactUploadSelectors?.();
    if (els.apAnalysisModelSelect) els.apAnalysisModelSelect.disabled = !isDebug;
    if (apAnalysisToggle) apAnalysisToggle.hidden = !isDebug;
    // #boxes-toggle-btn draws the raw analysis geometry over the photo, and
    // the recipe-tags ratio pill reports recommended_aspect_ratio -- both are
    // testbed inspection aids, not something a regular user has any use for.
    // setResultsDebugUi gates the overlay + pill themselves (results.js); this
    // only hides the button that opens the overlay.
    if (els.boxesToggleBtn) els.boxesToggleBtn.hidden = !isDebug;
    if (!isDebug) closeBoxesTypeMenu();
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
    // #report-modal-screenshot-row's on-demand capture button is the only
    // entry point into the screenshot-attachment feature (handleReportModal
    // ScreenshotCapture); hiding the row outside debug mode keeps the whole
    // feature testbed-only rather than just its label.
    if (els.reportModalScreenshotRow) els.reportModalScreenshotRow.style.display = isDebug ? "" : "none";
    // #analysis-preview's own display (like #results-analysis-slot's, right
    // below) depends on 分析面板顯示於預覽右側 too, not just isDebug -- see
    // syncResultsAnalysisSlotForSidePanelLayout's own doc comment, which sets
    // it alongside the slot so both stay in sync however either input changes.
    // #results-analysis-slot otherwise only hosts #analysis-preview, which
    // (like #results-detail-extra above) no longer shows on the analyze/
    // results page at all -- keep it hidden and let #results-canvas-col
    // recenter at a fixed width instead of stretching to fill the whole
    // freed-up row (900px matches .proc-container, the same single-column
    // canvas width used pre-results on the processing screen), UNLESS
    // 分析面板顯示於預覽右側 relocates #ap-options-card-inline into that same
    // slot for real -- see syncResultsAnalysisSlotForSidePanelLayout's own
    // doc comment.
    syncResultsAnalysisSlotForSidePanelLayout();
    applyRunCountOptionLimit(els.imageRunCountSelect, isDebug);
    applyRunCountOptionLimit(els.apRunCountSelect, isDebug);
    applyAnalysisModelOptionLimit(els.analysisModelSelect, isDebug);
    applyAnalysisModelOptionLimit(els.apAnalysisModelSelect, isDebug);
    applyImageModelOptionLimit(els.imageModelQualitySelect, isDebug);
    applyImageModelOptionLimit(els.apImageModelQualitySelect, isDebug);
    applyPickerSingleOptionState(els.analysisModelPicker, els.analysisModelSelect);
    applyPickerSingleOptionState(els.apAnalysisModelPicker, els.apAnalysisModelSelect);
    applyPickerSingleOptionState(els.imageModelQualityPicker, els.imageModelQualitySelect);
    applyPickerSingleOptionState(els.apImageModelQualityPicker, els.apImageModelQualitySelect);
  }

  // Per-deployment stamp ("DEV") from /api/config's envLabel -- drawn by
  // styles.css's html[data-env-label] .logo::after, and prefixed onto the
  // tab title. Empty on deployments without ENV_LABEL.
  function applyEnvLabel(label) {
    if (!label) return;
    document.documentElement.dataset.envLabel = label;
    document.documentElement.style.setProperty("--env-label", JSON.stringify(label));
    document.title = `[${label}] ${document.title}`;
  }

  // Fetch the feature flag even on a ?debug page.  Debug mode may expose
  // inspection controls, but it must not bypass an internally disabled Body
  // Reshape feature.
  if (window.location.href.includes("debug")) {
    applyDebugUiVisibility(true);
    getUiConfig().then((cfg) => {
      applyEnvLabel(cfg.envLabel);
      if (typeof cfg.enableBodyReshape === "boolean") {
        applyBodyReshapeVisibility(cfg.enableBodyReshape);
      }
      if (typeof cfg.compatibilityVersion === "number") {
        currentCompatibilityVersion = cfg.compatibilityVersion;
        renderCurrentCaseTimes();
      }
      if (typeof cfg.minCompatibleVersion === "number") {
        minCompatibleVersion = cfg.minCompatibleVersion;
      }
      renderFromVersionEnabled = cfg.renderFromVersion === true;
      syncVersionStripCurrent();
    });
  } else {
    getUiConfig().then((cfg) => {
      applyEnvLabel(cfg.envLabel);
      applyDebugUiVisibility(!!cfg.debugUi);
      if (typeof cfg.enableBodyReshape === "boolean") {
        applyBodyReshapeVisibility(cfg.enableBodyReshape);
      }
      if (typeof cfg.compatibilityVersion === "number") {
        currentCompatibilityVersion = cfg.compatibilityVersion;
        renderCurrentCaseTimes();
      }
      if (typeof cfg.minCompatibleVersion === "number") {
        minCompatibleVersion = cfg.minCompatibleVersion;
      }
      renderFromVersionEnabled = cfg.renderFromVersion === true;
      syncVersionStripCurrent();
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
    await openCaseFromHash();
    await openReportFromHash();
  })();

  // #report=<id>: the 面板 switch on the chat page's read-only report view.
  // Opened like a click on that report in the reports drawer, so the admin
  // sees the panel as it was reported. Once it's on screen syncCaseHash
  // turns the hash into that photo's #case=, so a reload just reopens it.
  async function openReportFromHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const reportId = params.get("report");
    if (!reportId) return;
    // job / analyze: the chat report view's 面板模式 for another photo of
    // the reported batch (or any photo of a report filed from 總覽, which
    // has no job of its own) -- see urls.oldUiReport.
    const otherJob = params.get("job");
    const otherAnalyze = params.get("analyze");
    const reveal = () => document.body.classList.remove("is-opening-case");
    const onSection = (e) => {
      if (e.detail?.name === "upload") return;
      reveal();
      document.removeEventListener("photoagent:sectionchange", onSection);
    };
    document.addEventListener("photoagent:sectionchange", onSection);
    try {
      const user = resolveAdminUser();
      const report = user ? await getReport(user, reportId, { jobId: otherJob }).catch(() => null) : null;
      if (report && otherAnalyze) {
        await viewAnalyzeHistoryRecord({ job_id: otherAnalyze, stage: "analyze", path: "local" });
      } else if (report?.job_id || report?.analyze_job_id) {
        await openReportInPanel(report, els.resultReportStatus);
      } else {
        showNoticeModal(tr("打不開這筆回報：可能沒有管理者權限，或回報已經刪掉了。"));
      }
    } finally {
      document.removeEventListener("photoagent:sectionchange", onSection);
      reveal();
      if (currentSectionName === "upload") syncCaseHash();
    }
  }

  // #case=<image_key>: the chat page's 面板模式 link for the photo it has
  // open, or the photo this page had open before a reload (syncCaseHash).
  // Opened like a click on that case in the history drawer.
  async function openCaseFromHash() {
    const caseKey = new URLSearchParams(location.hash.slice(1)).get("case");
    if (!caseKey) return;
    pendingModeHandoff = takeModeHandoff(caseKey);
    // Reveal as soon as the case is on screen, not when openCaseByKey
    // resolves: a 待美化 case's runPendingReviewFlow only resolves once the
    // user acts on it, which kept the page hidden until a reload.
    const reveal = () => document.body.classList.remove("is-opening-case");
    const onSection = (e) => {
      if (e.detail?.name === "upload") return;
      reveal();
      document.removeEventListener("photoagent:sectionchange", onSection);
    };
    document.addEventListener("photoagent:sectionchange", onSection);
    try {
      await openCaseByKey(caseKey);
    } finally {
      document.removeEventListener("photoagent:sectionchange", onSection);
      reveal();
      // Not opened (signed out, case gone): don't keep retrying on reload.
      if (currentSectionName === "upload") syncCaseHash();
    }
  }

  // 對話模式 ⇄ 面板模式: the photo's not-yet-rendered adjustments go along
  // with the switch. Same tab, same origin, so sessionStorage: the page being
  // left writes, the page opening that same case reads and deletes. The
  // format is shared with frontend-chat/src/handoff.ts -- change both
  // together. Neutral terms only: the engine is image_model + image_quality,
  // and 修圖建議 checks go by item text, since the two pages list them in
  // different orders.
  const MODE_HANDOFF_KEY = "photoagent:mode-handoff";
  const MODE_HANDOFF_MAX_AGE_MS = 2 * 60 * 1000;

  // The single-photo review as it stands, in the handoff's own terms; null
  // when no single photo's review is up.
  function readReviewState() {
    const state = singleFlowState;
    if (!state?.analysis || currentDetailBatchRecord()) return null;
    const { options } = readStyleSelectionsFromDom();
    const recipe = {};
    if (els.apRecipeList) {
      els.apRecipeList.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        if (cb.dataset.recipeText) recipe[cb.dataset.recipeText] = cb.checked;
      });
    }
    const review = {
      image_model: state.imageModel,
      image_quality: state.imageQuality,
      aspect_ratio: state.aspectRatio,
      output_scale: state.outputScale || els.apImageOutputScaleSelect?.value,
      advanced_options: readAdvancedOptionsFromDom("ap"),
      style_options: options,
      face_issue_overrides: getFaceIssueOverrides(),
      body_reshape_enabled: getBodyReshapeReviewEnabled(),
      user_prompt: els.promptInput ? els.promptInput.value.trim() : "",
      overwrite_prompt: !!els.promptOverwriteToggle?.checked,
      recipe: Object.keys(recipe).length ? recipe : undefined,
    };
    if (isDebugUiActive && els.apRunCountSelect) review.run_count = parseInt(els.apRunCountSelect.value, 10) || 1;
    return review;
  }

  // Taken the first time a case's review controls are up, and again after
  // each 美化 (which clears it). Reopening the revise panel after 取消 keeps
  // the old one, so edits kept across 取消 still count as unsent.
  function markReviewBaseline() {
    if (reviewBaseline && reviewBaseline.owner === singleFlowState) return;
    const review = readReviewState();
    reviewBaseline = review ? { owner: singleFlowState, key: JSON.stringify(review) } : null;
  }

  function isReviewDirty() {
    const cont = activeContinuation();
    if (cont) {
      // A request or a 設定 other than what the base version was made with.
      return continuationRequestDirty(cont) || continuationEngineChanged(cont);
    }
    if (!reviewBaseline || reviewBaseline.owner !== singleFlowState) return false;
    const review = readReviewState();
    return !!review && JSON.stringify(review) !== reviewBaseline.key;
  }

  // Before something on this same page replaces the revise panel (another
  // version, another photo): true when there is nothing unsent to lose, or
  // the user agreed to drop it.
  async function confirmDropReview(message) {
    if (!isReviewDirty()) return true;
    return showConfirmModal(message, { confirmLabel: tr("繼續"), cancelLabel: tr("留在這裡"), danger: false });
  }
  const DROP_REVIEW_FOR_PHOTO = tr("這張照片還沒美化的調整會清掉。確定要打開另一張嗎？");

  // The review fields changed since its baseline: what the version on screen
  // was made with is no edit, and handed over it would overwrite the chat
  // page's plan with one version's settings.
  function readUnsentReview() {
    const review = readReviewState();
    if (!review || !reviewBaseline || reviewBaseline.owner !== singleFlowState) return review;
    const base = JSON.parse(reviewBaseline.key);
    return Object.fromEntries(Object.entries(review)
      .filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(base[k])));
  }

  // The unsent edits as the request the chat keeps across a switch -- the
  // 個人化需求 and the styles moved off default -- and the names of the rest.
  function splitUnsentReview() {
    const { user_prompt: text = "", style_options: style = {}, ...rest } = readUnsentReview() || {};
    return {
      request: { text, style_options: Object.fromEntries(Object.entries(style).filter(([, v]) => v && v !== "default")) },
      rest,
    };
  }

  // True when the edits were handed over and the chat page will pick them up.
  // A handoff from the chat page this one never got to apply goes back as it
  // came: handing back this page's own controls would undo what was done there.
  // compositions is the analysis's _composition_by_ratio, so a ratio already
  // planned here is adopted there, not planned again.
  // Nothing unsent still goes over, as idle: the chat page then closes the
  // round it may still have open (one cleared here), and either way opens on
  // version_id, the version on screen here.
  function writeModeHandoff(caseKey) {
    if (!caseKey) return false;
    const write = (handoff, edits = !handoff.idle) => {
      try {
        sessionStorage.setItem(MODE_HANDOFF_KEY, JSON.stringify({ version_id: panelRun()?.versionId,
          showing_version_id: singleRuns?.[singleMirrorIndex]?.versionId, ...handoff, case_id: caseKey, at: Date.now() }));
        return edits;
      } catch {
        return false; // nothing carried over -- the switch itself still goes ahead
      }
    };
    // 接著這張調整 goes over as itself: the chat page carries on from the
    // same version, with the request so far.
    // With nothing written yet it is not an edit, but the base still goes
    // over: the chat page carries on from the same version, nothing typed.
    // Only while adjusting, though: a 接著這張調整 version only looked at has
    // itself as its default base (ensurePanelBase), and the panel is open
    // alongside any result anyway, so that alone is no more carried on from
    // than a version from 原圖. One the chat page handed over (handed) still
    // goes back as it came.
    // The engine goes along too: 美化 on the version carried on from uses
    // the picker's, as the chat's uses its plan's.
    const cont = activeContinuation();
    if (cont) {
      const engine = { image_model: singleFlowState?.imageModel, image_quality: singleFlowState?.imageQuality,
        output_scale: singleFlowState?.outputScale || els.apImageOutputScaleSelect?.value };
      // Only 設定 changed: still nothing typed, so the base goes over as
      // above, but as an edit -- the engine is what is unsent.
      if (!continuationRequestDirty(cont)) {
        return write(cont.handed || isAdjusting() ? { idle: true, base: cont.source, ...engine } : { idle: true },
          continuationEngineChanged(cont));
      }
      const { text, style_options, aspect_ratio } = pendingContinuationRequest();
      return write({ continuation: { ...cont.source, text, style_options, aspect_ratio }, ...engine });
    }
    const unapplied = pendingModeHandoff?.case_id === caseKey ? pendingModeHandoff : null;
    if (unapplied) return write(unapplied);
    // No single review up (still loading, a batch): nothing to tell either
    // way, so the chat page's round is left as it is.
    const review = readUnsentReview();
    if (!review) return write({}, false);
    if (!Object.keys(review).length) return write({ idle: true });
    return write({ ...review, compositions: singleFlowState.analysis._composition_by_ratio });
  }

  // Read-and-delete, whatever case it was for, so a stale one never applies later.
  function takeModeHandoff(caseKey) {
    let handoff = null;
    try {
      const raw = sessionStorage.getItem(MODE_HANDOFF_KEY);
      sessionStorage.removeItem(MODE_HANDOFF_KEY);
      handoff = raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
    if (!handoff || handoff.case_id !== caseKey) return null;
    if (!(Date.now() - (handoff.at || 0) < MODE_HANDOFF_MAX_AGE_MS)) return null;
    return handoff;
  }

  // Puts the chat page's adjustments onto the review controls of the case
  // now on screen, the way the user's own clicks would. Called right after
  // those controls are built: the 待美化 review (runPendingReviewFlow) or a
  // finished case's revise panel (backToProcessFromResults).
  function applyPendingModeHandoff() {
    const handoff = pendingModeHandoff;
    if (!handoff || handoff.case_id !== currentCaseHistoryImageKey || currentDetailBatchRecord()) return;
    pendingModeHandoff = null;

    // The chat page was carrying on from a version: so does this panel,
    // reopened on that version once the caller is done building this one.
    if (handoff.continuation) {
      const c = handoff.continuation;
      const runs = singleRuns || [];
      const run = runs.find((r) => r.versionId === c.version_id && r.jobId === c.job_id && r.sourceRun === c.run)
        || runs.find((r) => r.versionId === c.version_id);
      const prefill = { text: c.text || "", style_options: c.style_options || {},
        aspect_ratio: continuationAspectRatio(c.aspect_ratio) };
      if (run) Promise.resolve().then(() => enterPanelContinuation(run, prefill));
      return;
    }
    applyReviewState(handoff);
  }

  // Review fields in readReviewState's terms onto the panel; a field left
  // out keeps what is there.
  function applyReviewState(handoff) {
    const model = findModelQualityOptionByPair(handoff.image_model, handoff.image_quality);
    if (model && model.model === handoff.image_model && model.quality === handoff.image_quality) {
      applyModelQualityChange(model.value);
    }
    applyHandedOutputScale(handoff);
    if (isDebugUiActive && typeof handoff.run_count === "number") applyRunCountChange(handoff.run_count);
    if (handoff.advanced_options) {
      // As if each switch were flipped by hand: its per-person lock follows,
      // and 修圖建議 re-filters (applyAdvancedOptionsChange).
      const keys = {
        eye_closed_detection: els.apEyeClosedDetectionToggle,
        gaze_contact_detection: els.apEyeGazeFocusToggle,
        hair_mess_detection: els.apHairMessDetectionToggle,
        smile_adjustment: els.apSmileAdjustmentToggle,
      };
      Object.entries(keys).forEach(([key, control]) => {
        const want = handoff.advanced_options[key];
        if (!control || typeof want !== "boolean" || control.checked === want) return;
        control.checked = want;
        const face = ADVANCED_OPTION_FACE_CATEGORIES.find((c) => c.control === control);
        if (face) setFaceIssueCategoryLock(face.category, want);
      });
      applyAdvancedOptionsChange();
    }
    if (els.promptInput && typeof handoff.user_prompt === "string") els.promptInput.value = handoff.user_prompt;
    if (els.promptOverwriteToggle && typeof handoff.overwrite_prompt === "boolean") {
      els.promptOverwriteToggle.checked = handoff.overwrite_prompt;
    }
    applyFaceIssueOverrides(handoff.face_issue_overrides);
    if (typeof handoff.body_reshape_enabled === "boolean") setBodyReshapeReviewEnabled(handoff.body_reshape_enabled);
    renderFaceReviewCards();
    // Recipe checks by text; an item the chat page never listed keeps its default.
    let recipeChecked = null;
    if (handoff.recipe && els.apRecipeList) {
      recipeChecked = [];
      els.apRecipeList.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        const index = Number(cb.dataset.recipeIndex);
        if (!Number.isInteger(index) || index < 0) return;
        recipeChecked[index] = handoff.recipe[cb.dataset.recipeText] !== false;
      });
    }
    // The chat page leaves a style at its default by leaving it out.
    const style = handoff.style_options || null;
    const options = style && Object.fromEntries(
      ["pose", "angle", "lighting", "size"].map((k) => [k, style[k] || "default"]));
    applyStyleSelections({ options, recipeChecked });
    updateInlineAnalysisContinueBtnState();
    // Every ratio the chat page already planned joins _composition_by_ratio --
    // not just the one handed over -- so picking any of them later is adopted
    // as is, not planned again (the case reloads its analysis from history).
    const analysis = singleFlowState?.analysis;
    const handed = Object.entries(handoff.compositions || {})
      .filter(([r, state]) => state?._aspect_ratio_pref_at_analysis === r);
    if (analysis && handed.length) {
      analysis._composition_by_ratio = { ...(analysis._composition_by_ratio || {}), ...Object.fromEntries(handed) };
    }
    // Last: a ratio this photo has no composition for asks before reframing,
    // the same confirm a pick from the picker gets.
    const ratio = handoff.aspect_ratio;
    if (ratio && ratio !== singleFlowState?.aspectRatio && findAspectRatioOption(ratio)?.value === ratio) {
      handleUserAspectRatioPick(ratio);
    }
  }

  async function openCaseByKey(caseKey) {
    const user = getCurrentUser();
    if (!user) return;
    let entry = null;
    try {
      entry = (await getHistoryCase(user, { imageKey: caseKey }))?.case || null;
    } catch {
      entry = null;
    }
    if (!entry) {
      showNoticeModal(tr("找不到這張照片的處理紀錄。"));
      return;
    }
    try {
      await openHistoryCase(entry);
    } catch (error) {
      showNoticeModal(friendlyError(error));
    }
  }

  // The same choice a click on the case's history-drawer tile makes
  // (renderTileStatus): 待美化 / 分析中 opens the analysis, a finished
  // edit opens its result, one still running opens its progress.
  async function openHistoryCase(job) {
    if (isAnalyzedAfterEdits(job)) {
      await viewHistoryRecord(job);
      return;
    }
    const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
    const statuses = await Promise.all(
      jobIds.map((id) => getJobStatusCached(id, { path: job.path }).catch((err) => ({ status: "error", error: err?.code, error_message: err?.message })))
    );
    const failure = (s) => friendlyError({ message: s?.error_message || s?.error, code: s?.error });
    if (job.stage === "analyze") {
      if (statuses[0]?.status === "error") {
        showNoticeModal(failure(statuses[0]));
        return;
      }
      await viewAnalyzeHistoryRecord(job, { alreadyAnalyzed: statuses[0]?.status === "success" });
      return;
    }
    if (statuses.some((s) => s.status === "success")) {
      await viewHistoryRecord(job);
    } else if (statuses.every((s) => s.status === "error")) {
      showNoticeModal(failure(statuses[0]));
    } else {
      await viewProcessingHistoryRecord(job);
    }
  }

  // A 「只照需求改」 case analyzed afterwards (幫我分析, from either mode):
  // the analysis is its newest version, so the summary reads stage
  // "analyze" like an upload paused for review -- but the case already has
  // edits to show. It opens on those, with the analysis as the strip's
  // trailing 待美化 tile (viewHistoryRecord), not as a fresh upload's review.
  function isAnalyzedAfterEdits(job) {
    return job?.stage === "analyze" && !!job.modified_at;
  }

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
  function wireOptionPicker(details, select, { renderList, renderValue } = {}) {
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
    const isInlinePanel = () => panel && getComputedStyle(panel).position === "static";

    const repositionPanel = () => {
      if (!panel) return;
      if (isInlinePanel()) {
        panel.classList.remove("open-upward");
        panel.style.left = panel.style.top = panel.style.bottom = panel.style.maxHeight = "";
        return;
      }
      if (details.closest(".upload-selector-modal-body")) {
        const rect = details.getBoundingClientRect();
        panel.classList.remove("open-upward");
        panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 8))}px`;
        // Keep the panel below the trigger when possible, but reserve a
        // small minimum viewport area so even a trigger near the bottom can
        // never place the panel outside the window.
        const panelTop = Math.min(rect.bottom + 8, window.innerHeight - 96);
        panel.style.top = `${Math.max(8, panelTop)}px`;
        panel.style.bottom = "auto";
        panel.style.maxHeight = `${Math.max(40, window.innerHeight - Math.max(8, panelTop) - 16)}px`;
        return;
      }
      panel.classList.remove("open-upward");
      panel.style.left = "";
      const anchorRect = details.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      // Inside a settings modal the body scrolls and clips the panel, so
      // measure the room against it rather than the viewport.
      const clip = details.closest(".settings-modal-body")?.getBoundingClientRect();
      const spaceBelow = Math.min(window.innerHeight, clip ? clip.bottom : Infinity) - anchorRect.bottom;
      const spaceAbove = anchorRect.top - Math.max(0, clip ? clip.top : 0);
      if (spaceBelow < panelRect.height + 16 && spaceAbove > spaceBelow) {
        panel.classList.add("open-upward");
      }
      const overflowRight = panelRect.right - (window.innerWidth - 8);
      if (overflowRight > 0) {
        panel.style.left = `${-Math.min(overflowRight, Math.max(0, panelRect.left - 8))}px`;
      }
    };

    const showValue = (opt) => {
      if (renderValue) renderValue(details, opt?.value);
      else valueEl.textContent = opt?.dataset.displayLabel || opt?.textContent || "";
    };

    const createInput = (opt) => {
      const input = document.createElement("input");
      input.type = "radio";
      input.name = name;
      input.value = opt.value;
      input.checked = opt.value === select.value;
      // Rebuilt from scratch on every open (see this function's own doc
      // below) -- a plain setAnalysisPreviewDisabled(true) sweep run once,
      // earlier, would already be gone by the time the user next opens
      // this picker. isAnalysisPreviewLocked() re-checks the live lock
      // state at creation time instead, so the panel stays fully openable
      // (the content itself is never hidden) while picking a new option
      // here just doesn't do anything.
      input.disabled = isAnalysisPreviewLocked();
      input.addEventListener("change", () => {
        select.value = opt.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        showValue(opt);
        details.open = false;
      });
      return input;
    };

    const rebuild = () => {
      list.innerHTML = "";
      const options = Array.from(select.options).filter((opt) => !opt.hidden);
      if (renderList) {
        renderList(list, options, createInput);
      } else {
        options.forEach((opt) => {
          const li = document.createElement("li");
          const label = document.createElement("label");
          const cb = document.createElement("span");
          cb.className = "step-cb";
          const box = document.createElement("span");
          box.className = "step-cb-box";
          cb.appendChild(createInput(opt));
          cb.appendChild(box);
          const text = document.createElement("span");
          text.className = "step-cb-label";
          text.textContent = opt.textContent;
          label.appendChild(cb);
          label.appendChild(text);
          li.appendChild(label);
          list.appendChild(li);
        });
      }
      showValue(select.selectedOptions[0]);
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
      if (details.open && isInlinePanel()) panel.scrollIntoView({ block: "nearest" });
    });
    window.addEventListener("resize", () => { if (details.open) repositionPanel(); });
    window.addEventListener("scroll", () => { if (details.open) repositionPanel(); }, true);
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

  // When the upload selectors no longer fit on one line, move every group
  // but 長寬比 (models, 輸出尺寸 -- with their live picker state) into one
  // compact menu; 長寬比 stays its own pill. They move back once the row has room.
  // Outside debug mode the engines are hidden and only 輸出尺寸 is left, so it
  // stays a pill of its own instead (see useMenu).
  function setupCompactUploadSelectors() {
    const row = document.querySelector(".upload-inline-settings-row-1");
    const secondRow = document.querySelector("#upload-inline-settings-row-2");
    const menu = document.querySelector("#upload-inline-settings-menu");
    const portraitFold = document.querySelector("#advanced-settings-fold");
    const panel = menu?.querySelector(".upload-inline-settings-menu-panel");
    const modal = document.querySelector("#upload-selector-modal");
    const modalTitle = document.querySelector("#upload-selector-modal-title");
    const modalBody = document.querySelector("#upload-selector-modal-body");
    if (!row || !secondRow || !menu || !panel || !portraitFold || !modal || !modalBody) return;
    const groups = Array.from(row.children).filter((el) => el.classList.contains("option-pill-group"));
    if (!groups.length) return;
    const aspectGroup = groups.find((group) => group.querySelector("#image-aspect-ratio-picker"));
    const menuGroups = groups.filter((group) => group !== aspectGroup);
    // applyDebugUiVisibility hides the engine groups outside debug mode; a
    // hidden group has no position to measure and is nothing to collect.
    const shown = (group) => group.style.display !== "none";
    let compact = false;
    // Whether compact mode collected its groups into the 模型設定 menu: only
    // when more than one is shown -- a lone 輸出尺寸 (engines hidden) stays its
    // own pill next to 長寬比 instead of sitting alone in a menu.
    let useMenu = false;

    const closeModal = () => {
      if (modal.hidden) return;
      portraitFold.open = false;
      portraitFold.classList.remove("standalone-modal");
      if (useMenu) menuGroups.forEach((group) => panel.appendChild(group));
      if (compact) {
        row.appendChild(portraitFold);
        const runControl = row.querySelector(".split-run") || secondRow.querySelector(".split-run");
        if (runControl) row.appendChild(runControl);
      }
      else {
        const runControl = row.querySelector(".split-run") || secondRow.querySelector(".split-run");
        if (runControl && runControl.parentElement !== secondRow) secondRow.appendChild(runControl);
        if (runControl) secondRow.insertBefore(portraitFold, runControl);
        else secondRow.appendChild(portraitFold);
      }
      modal.hidden = true;
    };
    const openModal = (kind) => {
      closeModal();
      modalTitle.textContent = kind === "model" ? tr("模型設定") : tr("人像修正設定");
      if (kind === "model") menuGroups.forEach((group) => modalBody.appendChild(group));
      else {
        modalBody.appendChild(portraitFold);
        // The standalone portrait-settings modal is an editor window, so
        // show its setting rows immediately instead of requiring a second
        // click on the fold summary.
        portraitFold.open = true;
        portraitFold.classList.add("standalone-modal");
      }
      modal.hidden = false;
    };

    const restoreGroups = () => {
      closeModal();
      groups.forEach((group) => row.insertBefore(group, menu));
      const runControl = row.querySelector(".split-run") || secondRow.querySelector(".split-run");
      if (runControl && runControl.parentElement !== secondRow) secondRow.appendChild(runControl);
      if (portraitFold.parentElement !== secondRow) {
        if (runControl) secondRow.insertBefore(portraitFold, runControl);
        else secondRow.appendChild(portraitFold);
      }
      menu.open = false;
      // Hide the compact menu before measuring the restored groups. If it
      // remains visible during measurement, it consumes row width and can
      // falsely make the groups wrap again, causing compact/expanded flicker
      // while the window is being resized.
      menu.hidden = true;
      compact = false;
      useMenu = false;
    };
    const refresh = () => {
      if (compact) restoreGroups();
      const visible = groups.filter(shown);
      if (!visible.length) return;
      const firstTop = visible[0].getBoundingClientRect().top;
      const wrapped = visible.some((group) => group.getBoundingClientRect().top > firstTop + 2);
      useMenu = wrapped && menuGroups.filter(shown).length > 1;
      if (wrapped) {
        if (useMenu) menuGroups.forEach((group) => panel.appendChild(group));
        else menuGroups.forEach((group) => row.appendChild(group));
        // In compact mode 長寬比, the portrait control and Run join the
        // compact selector button (or the lone 輸出尺寸 pill) in the first
        // row, in that order.
        if (aspectGroup) row.appendChild(aspectGroup);
        row.appendChild(portraitFold);
        const runControl = secondRow.querySelector(".split-run");
        if (runControl) row.appendChild(runControl);
        compact = true;
      }
      menu.hidden = !useMenu;
    };

    menu.querySelector("summary")?.addEventListener("click", (e) => {
      if (compact) {
        e.preventDefault();
        openModal("model");
      }
    });
    portraitFold.querySelector("summary")?.addEventListener("click", (e) => {
      if (compact && portraitFold.parentElement === row) {
        e.preventDefault();
        openModal("portrait");
      }
    });
    modal.querySelector("#upload-selector-modal-close")?.addEventListener("click", closeModal);
    modal.querySelector(".error-modal-backdrop")?.addEventListener("click", closeModal);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });
    window.addEventListener("resize", refresh);
    refresh();
    // Exposed so resetApp can re-run this after it reparents
    // #advanced-settings-fold back out of #ap-settings-modal-body -- that
    // move happens outside this closure, so `compact` can go stale relative
    // to the fold's real parent unless something forces a re-measure.
    refreshCompactUploadSelectors = refresh;
  }

  // 窄 header（手機，以及攤開放不下的平板；斷點見 index.html <style>）跟對話模式一樣：新對話、最近照片兩顆圖示留在外面（代按
  // #new-chat-btn / #account-badge），其他按鈕收進 ⋯ 展開的選單（index.html <style>）。
  // 選了哪一項選單就收起來；點外面或按 Esc 也收。
  function setupHeaderMenu() {
    const row = els.headerActions;
    const more = document.getElementById("header-more-btn");
    if (!row || !more) return;
    const setOpen = (open) => {
      row.classList.toggle("is-open", open);
      more.setAttribute("aria-expanded", String(open));
    };
    more.addEventListener("click", () => setOpen(!row.classList.contains("is-open")));
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, a")) setOpen(false);
    });
    document.addEventListener("pointerdown", (e) => {
      if (row.classList.contains("is-open") && !e.target.closest?.(".header-actions, #header-more-btn")) setOpen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && row.classList.contains("is-open")) setOpen(false);
    });
    const proxy = (id, target) => {
      const btn = document.getElementById(id);
      if (btn && target) btn.addEventListener("click", () => target.click());
    };
    proxy("header-new-icon", els.newChatButton);
    proxy("header-history-icon", els.accountBadge);
    proxy("header-menu-report-btn", els.reportBtn);
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
      if (opt.displayLabel) optionEl.dataset.displayLabel = opt.displayLabel;
      select.appendChild(optionEl);
    });
    select.value = window.PhotoAgentConfig.defaultImageModelQuality || select.value;
  }
  populateModelQualitySelect(els.imageModelQualitySelect);
  populateModelQualitySelect(els.apImageModelQualitySelect);
  // The options only exist now, after applyDebugUiVisibility already ran, so
  // apply the gate here too rather than waiting for the next debug-mode change.
  applyImageModelOptionLimit(els.imageModelQualitySelect, isDebugUiActive);
  applyImageModelOptionLimit(els.apImageModelQualitySelect, isDebugUiActive);
  applyPickerSingleOptionState(els.imageModelQualityPicker, els.imageModelQualitySelect);
  applyPickerSingleOptionState(els.apImageModelQualityPicker, els.apImageModelQualitySelect);
  wireOptionPicker(els.imageModelQualityPicker, els.imageModelQualitySelect);
  wireOptionPicker(els.apImageModelQualityPicker, els.apImageModelQualitySelect);
  wireOptionPicker(els.apRunCountPicker, els.apRunCountSelect);

  // ── Case settings trigger (#ap-case-settings-btn, index.html) ─────────────
  // The controls it opens live in #ap-case-settings-modal; this is the part
  // that makes them readable without opening it. The button is one icon on a
  // row that belongs to 美化, so the reading happens in its tooltip rather
  // than on its face. Two jobs:
  //
  //   1. Say what the version currently on screen actually ran with. Showing
  //      a case writes the results-page copy of every picker (applyXOption
  //      below, scope "ap"), so the live DOM already holds exactly that set
  //      -- the summary just reads it back out.
  //   2. Say when that stopped being true. A pick made here applies to the
  //      NEXT edit, never to the image already rendered, so the instant the
  //      user changes one the pill stops describing what they are looking at.
  //      apCaseSettingsBaseline holds the last programmatically-applied set
  //      (i.e. what this version ran with); any divergence raises the 已變更
  //      flag, and changing back clears it again.
  //
  // 生成張數 is deliberately in neither: it is debug-only AND shared with the
  // upload page's own picker (applyRunCountChange writes both copies), so it
  // is not a value "this version" owns the way the others are.
  let apCaseSettingsBaseline = null;

  // The live results-page copies, i.e. what the next edit of this case would
  // be submitted with. Also what a debug-mode re-analysis carries forward
  // (restartArchivedCase's `settings`), so that restart changes only the one
  // value that triggered it instead of silently reverting the rest to product
  // defaults on the way through resetApp.
  function apCaseSettingsValues() {
    return {
      analysisModel: els.apAnalysisModelSelect?.value || "",
      imageModelQuality: els.apImageModelQualitySelect?.value || "",
      aspectRatio: els.apImageAspectRatioSelect?.value || "",
      outputScale: els.apImageOutputScaleSelect?.value || "",
      advancedOptions: readAdvancedOptionsFromDom("ap"),
    };
  }

  function snapshotApCaseSettings() {
    return JSON.stringify(apCaseSettingsValues());
  }

  // While 接著這張調整, #ap-image-aspect-ratio holds the round's output shape
  // (apRatioShowsContinuation), not the case's the baseline has: a shape is
  // a change only when it is neither "original" (keep the base's) nor the
  // one a continued base was made with.
  function apCaseSettingsDirty() {
    if (!apCaseSettingsBaseline) return false;
    const baseline = JSON.parse(apCaseSettingsBaseline);
    const cont = apRatioShowsContinuation && activeContinuation();
    if (cont) {
      const base = singleRuns?.find((run) => isContinuationSource(cont, run));
      const madeWith = isContinuationRun(base) ? continuationAspectRatio(madeWithRequest(base).aspect_ratio) : null;
      const shown = els.apImageAspectRatioSelect?.value || "";
      baseline.aspectRatio = shown === "original" || shown === madeWith ? shown : "original";
    }
    return JSON.stringify(baseline) !== snapshotApCaseSettings();
  }

  function apCaseSettingsSummaryText() {
    const model = findModelQualityOption(els.apImageModelQualitySelect?.value);
    // The engine is named only where it can be picked (debug mode).
    const parts = [isDebugUiActive && (model?.displayLabel || model?.label)].filter(Boolean);
    const ratio = els.apImageAspectRatioSelect?.value;
    // "auto" is the default and says nothing; anything else was a deliberate
    // choice and belongs in the tooltip. 人像設定 stays out of it entirely --
    // four toggles don't summarise into a phrase, and the dirty dot already
    // covers the case that matters (someone changed them since this version
    // ran), with the modal one click away for the detail.
    if (ratio && ratio !== "auto") {
      parts.push(findAspectRatioOption(ratio)?.label || ratio);
    }
    // Same for 輸出尺寸: only a tier other than the default, and only where the
    // engine actually renders at it.
    const scale = findOutputScaleOption(els.apImageOutputScaleSelect?.value);
    if (scale && scale.value !== window.PhotoAgentConfig.defaultOutputScale
      && engineIsScalable(els.apImageModelQualitySelect?.value)) {
      parts.push(scale.label);
    }
    return parts.join(" · ") || tr("設定");
  }

  function updateApCaseSettingsTrigger() {
    const btn = els.apCaseSettingsBtn;
    if (!btn) return;
    // Only meaningful next to a live action: #inline-analysis-continue-btn (a
    // case that can still be edited) or #ap-restart-actions (an archived one,
    // whose settings are still worth reading even though it can only be
    // restarted). Read-only historical playback shows neither, and gets no
    // settings pill either -- there is no "next edit" for it to describe.
    const runVisible = !!(els.inlineAnalysisContinueBtn && !els.inlineAnalysisContinueBtn.hidden);
    const restartVisible = !!(els.apRestartActions && !els.apRestartActions.hidden);
    btn.hidden = !(runVisible || restartVisible);
    if (btn.hidden) closeApCaseSettingsModal();
    syncApContinueBase();
    // 幫我分析 runs ahead of a 美化, so only beside one -- and only while the
    // case still has nothing but 「只照需求改」's stub analysis. On a version
    // base too: it analyzes the original photo (analyzeJobId) all the same.
    if (els.apSkipAnalyzeBtn) {
      els.apSkipAnalyzeBtn.hidden = !(runVisible && caseUnanalyzed());
    }
    const dirty = apCaseSettingsDirty();
    if (els.apCaseSettingsDirty) els.apCaseSettingsDirty.hidden = !dirty;
    // The tooltip carries what the button used to say on its face. It can
    // afford to name which of the two things it means -- the version on
    // screen, or the next edit -- where a pill on this row could only have
    // shown the values and left the user to work out which.
    const summary = apCaseSettingsSummaryText();
    const label = dirty
      ? tr("開啟設定：{summary}（設定與目前結果不同）", { summary })
      : tr("開啟設定：{summary}", { summary });
    btn.title = label;
    btn.setAttribute("aria-label", label);
  }

  // fromUser separates the two writers of these controls: showing a case
  // applies its settings programmatically (applyXOption), which is what the
  // baseline must follow, while the user's own picks arrive through
  // applyXChange and must NOT move it -- that divergence is the entire signal.
  function refreshApCaseSettings({ fromUser = false } = {}) {
    if (!fromUser) apCaseSettingsBaseline = snapshotApCaseSettings();
    updateApCaseSettingsTrigger();
  }

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
  // Writes the RESULTS page's copy only. Showing a case must never disturb the
  // upload page's own pick: those controls belong to the next, unrelated
  // upload, and startSubmit/startSubmitBatch read straight off them. The
  // upload picker keeps itself in sync without help (wireOptionPicker sets its
  // select's value and its own label before dispatching "change"), so the only
  // caller that needs includeUpload is resetSettingsToDefaults.
  function applyModelQualityOption(opt, { includeUpload = false, fromUser = false } = {}) {
    if (!opt) return;
    if (includeUpload && els.imageModelQualitySelect) els.imageModelQualitySelect.value = opt.value;
    if (els.apImageModelQualitySelect) els.apImageModelQualitySelect.value = opt.value;
    const pickers = includeUpload
      ? [els.imageModelQualityPicker, els.apImageModelQualityPicker]
      : [els.apImageModelQualityPicker];
    pickers.forEach((picker) => {
      const valueEl = picker?.querySelector(".ap-style-value");
      if (valueEl) valueEl.textContent = opt.displayLabel || opt.label;
    });
    syncOutputScalePickers();
    refreshApCaseSettings({ fromUser });
  }

  // The upload page's controls are its own (see applyModelQualityOption), so
  // includeUpload is only for a programmatic change made while the user is
  // actually looking at the upload screen -- applyTwoStageEditChange's
  // quality bump, which no picker interaction has already applied.
  function applyModelQualityChange(value, { includeUpload = false } = {}) {
    const opt = findModelQualityOption(value);
    if (!opt) return;
    applyModelQualityOption(opt, { includeUpload, fromUser: true });
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
    els.apImageModelQualitySelect.addEventListener("change", () => {
      applyModelQualityChange(els.apImageModelQualitySelect.value);
      updateInlineAnalysisContinueBtnState();
    });
  }

  // Unchecking 兩階段修圖 (i.e. enabling single_pass_edit) skips Step 5b's
  // isolated gaze-repair re-pass -- see pipeline.finish_stage's docstring --
  // which is only trusted to be handled reliably by Step 5's own bundled
  // call at gpt-image-2 medium+, not gpt-image-2 low. Bump the picker to
  // medium the moment single-pass is switched on if it's currently sitting
  // on gpt-image-2 low -- a display nicety, not a guarantee of what will
  // actually run: pipeline.finish_stage only bumps its own edit call to
  // medium when this photo's analysis has an approved gaze fix to bundle,
  // so a photo with none stays on low even though the picker now shows
  // medium here. Never downgrades a HIGHER quality/model choice the user
  // already made. Re-checking 兩階段
  // 修圖 reverses this: gpt-image-2 medium is only needed for the single-call
  // path's own gaze fix, so switch back down to low, the cheaper tier that's
  // sufficient once Step 5b's own dedicated pass is doing the gaze repair
  // again -- same "only touch the exact combo the other side would have set"
  // rule, mirrored. Step 5b itself always runs on a separate, fixed model
  // (config.MODELS["gaze_repair"], currently nano-banana-2) regardless of
  // what Step 5's own model/quality picker says -- untouched by this toggle
  // either way.
  // #two-stage-edit-desc describes the two-stage split (Step 5's model +
  // Step 5b's fixed nano-banana-2 pass) -- meaningless once single_pass_edit
  // collapses that into one call, so it only makes sense to show while the
  // toggle is actually checked.
  function applyTwoStageEditDescVisibility() {
    if (els.twoStageEditDesc) els.twoStageEditDesc.hidden = !els.twoStageEditToggle?.checked;
  }

  function applySinglePassEditOption(singlePassEdit) {
    const normalized = singlePassEdit !== false;
    const twoStageEnabled = !normalized;
    if (els.twoStageEditToggle) els.twoStageEditToggle.checked = twoStageEnabled;
    applyTwoStageEditDescVisibility();
    return normalized;
  }

  function resolvedSinglePassEdit(...candidates) {
    const stored = candidates.find((value) => typeof value === "boolean");
    return stored == null ? DEFAULT_SINGLE_PASS_EDIT : stored;
  }

  function readSinglePassEditOption() {
    if (!els.twoStageEditToggle) return DEFAULT_SINGLE_PASS_EDIT;
    return applySinglePassEditOption(!els.twoStageEditToggle.checked);
  }

  function applyTwoStageEditChange(enabled) {
    const singlePassEdit = applySinglePassEditOption(!enabled);

    if (singleFlowState) singleFlowState.singlePassEdit = singlePassEdit;
    // Per-case routing, same rule as applyModelQualityChange below. With both
    // branches running unconditionally, flipping this on one case's revise
    // panel also moved the batch-wide default that startInlineBatchReview
    // falls back to -- so the next case opened, having no pick of its own,
    // showed (and would run) the other case's flip.
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.singlePassEdit = singlePassEdit;
    } else if (batchSettings) {
      batchSettings.singlePassEdit = singlePassEdit;
    }

    // Nothing has touched a picker here, so this bump is the only thing that
    // will move the visible control -- it has to land on whichever page the
    // user is actually on.
    const onUpload = !!els.sections.upload?.classList.contains("visible");
    const current = findModelQualityOption(
      (onUpload ? els.imageModelQualitySelect : els.apImageModelQualitySelect)?.value);
    if (!current || current.model !== "gpt-image-2") return;
    if (!enabled && current.quality === "low") {
      const medium = findModelQualityOptionByPair("gpt-image-2", "medium");
      if (medium) applyModelQualityChange(medium.value, { includeUpload: onUpload });
    } else if (enabled && current.quality === "medium") {
      const low = findModelQualityOptionByPair("gpt-image-2", "low");
      if (low) applyModelQualityChange(low.value, { includeUpload: onUpload });
    }
  }

  // Always start from the product default. Do not trust browser-restored form
  // state or one checkbox's HTML default to decide whether Step 5b runs.
  applySinglePassEditOption(DEFAULT_SINGLE_PASS_EDIT);
  els.twoStageEditToggle?.addEventListener("change", () =>
    applyTwoStageEditChange(els.twoStageEditToggle.checked));

  // 分析卡片 (debug only): shows analysis.findings in the review panel's own tab.
  function applyFindingsCardsOption() {
    setFindingsCardsShown(isDebugUiActive && !!els.findingsCardsToggle?.checked);
  }
  if (els.findingsCardsToggle) els.findingsCardsToggle.checked = false;
  els.findingsCardsToggle?.addEventListener("change", applyFindingsCardsOption);

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
  // The options only exist now, after applyDebugUiVisibility already ran, so
  // apply the gate here too rather than waiting for the next debug-mode change.
  applyAnalysisModelOptionLimit(els.analysisModelSelect, isDebugUiActive);
  applyAnalysisModelOptionLimit(els.apAnalysisModelSelect, isDebugUiActive);
  applyPickerSingleOptionState(els.analysisModelPicker, els.analysisModelSelect);
  applyPickerSingleOptionState(els.apAnalysisModelPicker, els.apAnalysisModelSelect);
  wireOptionPicker(els.analysisModelPicker, els.analysisModelSelect);
  wireOptionPicker(els.apAnalysisModelPicker, els.apAnalysisModelSelect);

  // Results page's copy only, same rule as applyModelQualityOption above.
  function applyAnalysisModelOption(opt, { includeUpload = false, fromUser = false } = {}) {
    if (!opt) return;
    if (includeUpload && els.analysisModelSelect) els.analysisModelSelect.value = opt.value;
    if (els.apAnalysisModelSelect) els.apAnalysisModelSelect.value = opt.value;
    const pickers = includeUpload
      ? [els.analysisModelPicker, els.apAnalysisModelPicker]
      : [els.apAnalysisModelPicker];
    pickers.forEach((picker) => {
      const valueEl = picker?.querySelector(".ap-style-value");
      if (valueEl) valueEl.textContent = opt.label;
    });
    refreshApCaseSettings({ fromUser });
  }

  function applyAnalysisModelChange(value) {
    const opt = findAnalysisModelOption(value);
    if (!opt) return;
    applyAnalysisModelOption(opt);
    if (singleFlowState) singleFlowState.analysisModel = opt.value;
    // Same per-case routing as applyModelQualityChange/applyAspectRatioChange
    // above -- a change made while one batch case's revise panel is open
    // belongs to that case alone, not every other case reading the shared
    // batchSettings.
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.analysisModel = opt.value;
    } else if (batchSettings) {
      batchSettings.analysisModel = opt.value;
    }
  }

  if (els.analysisModelSelect) {
    els.analysisModelSelect.addEventListener("change", () =>
      applyAnalysisModelChange(els.analysisModelSelect.value));
  }

  // #ap-analysis-model only becomes clickable in debug mode (applyDebugUiVisibility
  // drops `is-locked` there; the release build's picker has no reachable radio
  // inputs at all, so this never fires), and even then it cannot behave like
  // its neighbours: 美化引擎/長寬比/人像設定 are read at edit time, so changing
  // them just changes the next edit -- but the analysis has already run. The
  // only honest response is to analyze the photo again. That goes through the
  // same restart path an archived case uses, carrying this case's other
  // settings so the re-analysis differs in exactly the one value just picked.
  if (els.apAnalysisModelSelect) {
    els.apAnalysisModelSelect.addEventListener("change", async () => {
      if (!isDebugUiActive) return;
      const value = els.apAnalysisModelSelect.value;
      const previous = singleFlowState?.analysisModel
        || currentDetailBatchRecord()?.analysisModel
        || null;
      if (!previous || value === previous) return;
      // Put the picker back whenever the restart doesn't happen -- leaving it
      // on the new value would claim this analysis came from a model it did
      // not, which is the exact misreport the restart exists to avoid.
      const restore = () => applyAnalysisModelOption(findAnalysisModelOption(previous));
      const sourceFile = selectedFile;
      const sourceUrl = selectedFileSourceUrl;
      if (!sourceFile && !sourceUrl) {
        showError(tr("這筆紀錄沒有可重新分析的原始照片。"));
        restore();
        return;
      }
      const confirmed = await showConfirmModal(
        tr("換分析引擎必須重新分析這張照片，目前的分析結果和建議都會被取代。要繼續嗎？"),
        { confirmLabel: tr("重新分析"), danger: false, costSteps: [analysisPreflightStep()] }
      );
      if (!confirmed) {
        restore();
        return;
      }
      const settings = { ...apCaseSettingsValues(), analysisModel: value };
      closeApCaseSettingsModal();
      hideError();
      try {
        await restartArchivedCase(sourceFile, sourceUrl, false, { settings });
      } catch (error) {
        showError(tr("無法重新載入原始照片，請稍後再試。") + "\n" + friendlyError(error));
        restore();
      }
    });
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

  const FIXED_ASPECT_RATIO_RE = /^(\d+):(\d+)$/;

  function aspectRatioIconSvg(value, size) {
    const ratio = FIXED_ASPECT_RATIO_RE.exec(value || "");
    let body;
    if (ratio) {
      const scale = 18 / Math.max(Number(ratio[1]), Number(ratio[2]));
      const w = Number(ratio[1]) * scale;
      const h = Number(ratio[2]) * scale;
      body = `<rect x="${(24 - w) / 2}" y="${(24 - h) / 2}" width="${w}" height="${h}" rx="2"/>`;
    } else if (value === "original") {
      body = '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5"/>';
    } else {
      body = '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>';
    }
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }

  function renderAspectRatioValue(details, value) {
    const opt = findAspectRatioOption(value);
    const icon = details?.querySelector(".ap-style-trigger .option-pill-icon");
    const valueEl = details?.querySelector(".ap-style-value");
    if (icon) icon.innerHTML = aspectRatioIconSvg(opt?.value, 15);
    if (valueEl) valueEl.textContent = opt?.label || "";
  }

  function renderAspectRatioList(list, options, createInput) {
    const smartRow = document.createElement("li");
    smartRow.className = "ar-smart-row";
    const shapeRow = document.createElement("li");
    shapeRow.className = "ar-shape-row";
    options.forEach((opt) => {
      const config = findAspectRatioOption(opt.value);
      const fixed = FIXED_ASPECT_RATIO_RE.test(opt.value);
      const label = document.createElement("label");
      label.className = fixed ? "ar-shape-option" : "ar-smart-option";
      label.appendChild(createInput(opt));
      label.insertAdjacentHTML("beforeend", aspectRatioIconSvg(opt.value, fixed ? 30 : 22));
      const text = document.createElement("span");
      text.className = "ar-option-text";
      const name = document.createElement("b");
      name.textContent = config?.label || opt.textContent;
      text.appendChild(name);
      const note = fixed ? config?.hint : config?.description;
      if (note) {
        const small = document.createElement("small");
        small.textContent = note;
        text.appendChild(small);
      }
      label.appendChild(text);
      (fixed ? shapeRow : smartRow).appendChild(label);
    });
    if (smartRow.childElementCount) list.appendChild(smartRow);
    if (shapeRow.childElementCount) {
      const scale = document.createElement("li");
      scale.className = "ar-scale";
      [tr("← 直式"), tr("方形"), tr("橫式 →")].forEach((text) => {
        const span = document.createElement("span");
        span.textContent = text;
        scale.appendChild(span);
      });
      list.appendChild(scale);
      list.appendChild(shapeRow);
    }
  }

  populateAspectRatioSelect(els.imageAspectRatioSelect);
  populateAspectRatioSelect(els.apImageAspectRatioSelect);
  const aspectRatioPickerLayout = { renderList: renderAspectRatioList, renderValue: renderAspectRatioValue };
  wireOptionPicker(els.imageAspectRatioPicker, els.imageAspectRatioSelect, aspectRatioPickerLayout);
  wireOptionPicker(els.apImageAspectRatioPicker, els.apImageAspectRatioSelect, aspectRatioPickerLayout);
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
  // Results page's copy only, same rule as applyModelQualityOption above.
  function applyAspectRatioOption(opt, { includeUpload = false, fromUser = false } = {}) {
    if (!opt) return;
    if (includeUpload && els.imageAspectRatioSelect) els.imageAspectRatioSelect.value = opt.value;
    if (els.apImageAspectRatioSelect) els.apImageAspectRatioSelect.value = opt.value;
    const pickers = includeUpload
      ? [els.imageAspectRatioPicker, els.apImageAspectRatioPicker]
      : [els.apImageAspectRatioPicker];
    pickers.forEach((picker) => renderAspectRatioValue(picker, opt.value));
    syncReframingRatioLock();
    syncOutputScalePickers();
    refreshApCaseSettings({ fromUser });
  }

  function applyAspectRatioChange(value) {
    const opt = findAspectRatioOption(value);
    if (!opt) return;
    applyAspectRatioOption(opt, { fromUser: true });
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
    updateInlineAnalysisContinueBtnState();
  }

  // Only composition reframing owns the aspect-ratio picker. Whole-frame
  // alignment/straightening is a separate review item; its unavoidable wedge
  // trim must not make unchecking it behave like rejecting a composition crop.
  const CROP_AXIS_NAMES = new Set(["crop", "cropping", "framing", "reframing"]);

  function correctionAxesIncludeCrop(entry) {
    const axes = Array.isArray(entry) ? entry : [entry];
    return axes.some((axis) => CROP_AXIS_NAMES.has(String(axis || "").trim().toLowerCase()));
  }

  // Reframing and a changed output ratio are presented as one recipe item.
  // Keep the visible ratio control in sync as soon as that item is declined;
  // the backend independently enforces the same decision at finish time.
  function applyRejectedReframingRatio(event) {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "checkbox") return;
    // Keyed by data-recipe-index (the item's position in retouch_recipe/
    // correction_axes), not DOM order -- fillCheckList visually pins a
    // reframing item to the top of #ap-recipe-list, which no longer matches
    // its original array index.
    const index = Number(input.dataset.recipeIndex);
    if (!Number.isInteger(index) || index < 0) return;
    const batchRecord = currentDetailBatchRecord();
    const rawAnalysis = batchRecord ? batchRecord.analysis : singleFlowState?.analysis;
    if (!rawAnalysis) return;
    if (!correctionAxesIncludeCrop((rawAnalysis.correction_axes || [])[index])) return;
    const owner = batchRecord || singleFlowState;
    if (!input.checked) {
      if (owner) {
        owner.reframingAspectRatioBeforeReject =
          els.apImageAspectRatioSelect?.value || owner.aspectRatio || "auto";
      }
      applyAspectRatioChange("original");
      return;
    }

    // Re-approving the combined framing item restores the choice that was in
    // force before it was declined. A restored/history result has no in-memory
    // value, but its saved source proposal still tells us that Auto should be
    // active again instead of leaving the forced "original" selection behind.
    const savedProposal = rawAnalysis._rejected_reframing_source || {};
    let restoredPref = owner?.reframingAspectRatioBeforeReject;
    if (!restoredPref) {
      restoredPref = savedProposal.recommended_aspect_ratio
        && savedProposal.recommended_aspect_ratio !== "original"
        ? "auto"
        : (rawAnalysis._aspect_ratio_pref_at_analysis || "auto");
    }
    applyAspectRatioChange(restoredPref);
  }

  if (els.imageAspectRatioSelect) {
    els.imageAspectRatioSelect.addEventListener("change", () =>
      applyAspectRatioChange(els.imageAspectRatioSelect.value));
  }
  if (els.apImageAspectRatioSelect) {
    els.apImageAspectRatioSelect.addEventListener("change", () => {
      // 接著這張調整: the pick is this round's output shape only, so it neither
      // reframes nor becomes the case's ratio.
      if (activeContinuation()) {
        applyAspectRatioOption(findAspectRatioOption(continuationAspectRatio(els.apImageAspectRatioSelect.value)),
          { fromUser: true });
        updateInlineAnalysisContinueBtnState();
        return;
      }
      handleUserAspectRatioPick(els.apImageAspectRatioSelect.value);
    });
  }

  // ── 輸出尺寸 (#image-output-scale / #ap-image-output-scale) ───────────────
  // Same two-copy arrangement as 美化引擎/長寬比 above: the upload page's
  // select belongs to the next upload, the results page's to the case on
  // screen, and a change is pushed into whichever flow state is live so the
  // submit paths read it from there. Each row names the size it renders at
  // for the ratio picked beside it ("2K · 2512×1408"); auto/original only
  // know their ratio after analysis, so they get the pixel count instead.
  // An engine that renders at a fixed size (config.js `scalable: false`)
  // ignores the tier, so the picker is disabled and shows the default while
  // it is picked -- the select keeps the user's own tier for when they
  // switch back to one that honours it.
  let outputScaleSizes = {};

  function findOutputScaleOption(value) {
    const config = window.PhotoAgentConfig;
    const options = config.outputScaleOptions || [];
    const defaultOpt = options.find((o) => o.value === config.defaultOutputScale) || options[0] || null;
    if (!value) return defaultOpt;
    // A tier no longer offered (3K/4K, saved before they were dropped) falls
    // back to the largest remaining one, as the backend does.
    return options.find((o) => o.value === value) || options[options.length - 1] || defaultOpt;
  }

  function engineIsScalable(modelQualityValue) {
    return findModelQualityOption(modelQualityValue)?.scalable !== false;
  }

  function outputScaleRowText(opt, ratio) {
    const info = outputScaleSizes[opt.value];
    const size = info?.sizes?.[ratio];
    const detail = size
      ? size.replace("x", "×")
      : (info?.megapixels ? tr("約 {mp}MP", { mp: info.megapixels }) : opt.description);
    return detail ? `${opt.label} · ${detail}` : opt.label;
  }

  function populateOutputScaleSelect(select) {
    if (!select) return;
    select.innerHTML = "";
    (window.PhotoAgentConfig.outputScaleOptions || []).forEach((opt) => {
      const optionEl = document.createElement("option");
      optionEl.value = opt.value;
      optionEl.textContent = opt.label;
      optionEl.dataset.displayLabel = opt.label;
      select.appendChild(optionEl);
    });
    select.value = findOutputScaleOption(null)?.value || select.value;
  }

  // Each copy is read against its own page's 美化引擎 and 長寬比. The rows'
  // text is rewritten in place; wireOptionPicker rebuilds its list from the
  // select's options on every open, so the next open shows it.
  const outputScaleCopies = () => [
    [els.imageOutputScalePicker, els.imageOutputScaleSelect, els.imageModelQualitySelect, els.imageAspectRatioSelect],
    [els.apImageOutputScalePicker, els.apImageOutputScaleSelect, els.apImageModelQualitySelect, els.apImageAspectRatioSelect],
  ];

  function syncOutputScalePickers() {
    outputScaleCopies().forEach(([picker, select, engineSelect, ratioSelect]) => {
      if (!picker || !select) return;
      const ratio = ratioSelect?.value;
      Array.from(select.options).forEach((optionEl) => {
        const opt = findOutputScaleOption(optionEl.value);
        if (opt) optionEl.textContent = outputScaleRowText(opt, ratio);
      });
      const scalable = engineIsScalable(engineSelect?.value);
      picker.classList.toggle("is-single-option", !scalable);
      if (!scalable) picker.open = false;
      picker.title = scalable ? "" : tr("這個美化引擎的輸出尺寸是固定的");
      const shown = scalable ? findOutputScaleOption(select.value) : findOutputScaleOption(null);
      const valueEl = picker.querySelector(".ap-style-value");
      if (valueEl && shown) valueEl.textContent = shown.label;
    });
  }

  // Results page's copy only, same rule as applyModelQualityOption.
  function applyOutputScaleOption(opt, { includeUpload = false, fromUser = false } = {}) {
    if (!opt) return;
    if (includeUpload && els.imageOutputScaleSelect) els.imageOutputScaleSelect.value = opt.value;
    if (els.apImageOutputScaleSelect) els.apImageOutputScaleSelect.value = opt.value;
    syncOutputScalePickers();
    refreshApCaseSettings({ fromUser });
  }

  function applyOutputScaleChange(value) {
    const opt = findOutputScaleOption(value);
    if (!opt) return;
    applyOutputScaleOption(opt, { fromUser: true });
    if (singleFlowState) singleFlowState.outputScale = opt.value;
    // Per-case routing, same as applyModelQualityChange.
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.outputScale = opt.value;
    } else if (batchSettings) {
      batchSettings.outputScale = opt.value;
    }
  }

  populateOutputScaleSelect(els.imageOutputScaleSelect);
  populateOutputScaleSelect(els.apImageOutputScaleSelect);
  wireOptionPicker(els.imageOutputScalePicker, els.imageOutputScaleSelect);
  wireOptionPicker(els.apImageOutputScalePicker, els.apImageOutputScaleSelect);
  syncOutputScalePickers();
  if (els.imageOutputScaleSelect) {
    els.imageOutputScaleSelect.addEventListener("change", () =>
      applyOutputScaleChange(els.imageOutputScaleSelect.value));
  }
  if (els.apImageOutputScaleSelect) {
    els.apImageOutputScaleSelect.addEventListener("change", () => {
      applyOutputScaleChange(els.apImageOutputScaleSelect.value);
      updateInlineAnalysisContinueBtnState();
    });
  }
  // The engine and ratio pickers change what this one can offer and what
  // its rows say. Programmatic sets go through applyModelQualityOption /
  // applyAspectRatioOption, which call syncOutputScalePickers themselves;
  // these cover the upload copies a user pick changes directly.
  [els.imageModelQualitySelect, els.imageAspectRatioSelect].forEach((select) =>
    select?.addEventListener("change", syncOutputScalePickers));
  getCapabilities()
    .then((caps) => {
      (caps?.render?.output_scales || []).forEach((s) => {
        outputScaleSizes[s.value] = { sizes: s.sizes || {}, megapixels: s.megapixels };
      });
      syncOutputScalePickers();
    })
    .catch(() => {});

  // Owner (batch record or singleFlowState) -> the ratio it is being reframed for.
  const reframesInFlight = new Map();
  // Batch record -> ratio of a reframe that finished while another photo was shown.
  const pendingReframeOffers = new Map();

  // A 長寬比 picked on the results page re-plans the composition for it
  // (pipeline.reframe_stage) once the user accepts the extra analysis call.
  // A ratio this photo already has a composition for is swapped in for free.
  // Internal ratio writes (applyRejectedReframingRatio) bypass this.
  async function handleUserAspectRatioPick(value) {
    const batchRecord = currentDetailBatchRecord();
    const owner = batchRecord || singleFlowState;
    const previous = batchRecord ? effectiveAspectRatio(batchRecord) : (singleFlowState?.aspectRatio || "auto");
    const analysis = owner?.analysis;
    const plannedFor = analysis?._aspect_ratio_pref_at_analysis || "auto";
    if (!analysis || analysis._skipped_analysis || value === previous || value === plannedFor) {
      applyAspectRatioChange(value);
      return;
    }
    const saved = analysis._composition_by_ratio?.[value];
    if (saved) {
      adoptReframedAnalysis(owner, restoreCompositionState(analysis, saved), saved);
      applyAspectRatioChange(value);
      return;
    }

    const restore = () => applyAspectRatioOption(findAspectRatioOption(previous), { fromUser: true });
    if (reframesInFlight.has(owner)) {
      restore();
      return;
    }
    const path = batchRecord ? (batchSettings?.path || "local") : (singleFlowState.processingPath || "local");
    const jobId = batchRecord ? batchRecord.analyzeJobId : singleFlowState.analyzeJobId;
    const file = batchRecord ? batchRecord.file : selectedFile;
    if (!(path === "local" && jobId) && !file) {
      showError(tr("這筆紀錄沒有可重新構圖的原始照片。"));
      restore();
      return;
    }
    const confirmed = await showConfirmModal(
      tr("改變長寬比需要依新比例重新構圖，會再呼叫一次分析模型並產生額外費用；畫面建議的其他項目維持不變。要繼續嗎？"),
      { confirmLabel: tr("重新構圖"), danger: false }
    );
    if (!confirmed) {
      restore();
      return;
    }

    reframesInFlight.set(owner, value);
    pendingReframeOffers.delete(owner);
    syncReframeBusy();
    hideError();
    let reframedOnScreen = false;
    try {
      const reframedAnalysis = await requestReframe({
        jobId, file, analysis, aspectRatio: value, path,
        analysisModel: owner.analysisModel || batchSettings?.analysisModel
      });
      if (owner.analysis !== analysis) return;
      if (reframeOwnerOnScreen() === owner) {
        adoptReframedAnalysis(owner, reframedAnalysis, reframedAnalysis._composition_by_ratio?.[value]);
        applyAspectRatioChange(value);
        reframedOnScreen = true;
      } else {
        adoptReframedAnalysisOffScreen(owner, reframedAnalysis, value);
        pendingReframeOffers.set(owner, value);
      }
    } catch (error) {
      showError(tr("重新構圖失敗：{v1}", { v1: error.message || error }));
      if (reframeOwnerOnScreen() === owner) restore();
    } finally {
      reframesInFlight.delete(owner);
      syncReframeBusy();
    }
    if (reframedOnScreen) await offerEditAfterReframe(owner, value);
  }

  function reframeOwnerOnScreen() {
    return currentDetailBatchRecord() || singleFlowState;
  }

  // A batch photo the user switched away from mid-reframe: its panel is rebuilt
  // from record.analysis/recipeChecked when reopened, so remap the saved checks
  // by recipe text onto the new list (the reframing item may move or vanish).
  function adoptReframedAnalysisOffScreen(record, analysis, pref) {
    if (Array.isArray(record.recipeChecked) && record.analysis) {
      const oldRecipes = record.analysis.retouch_recipe || [];
      const checkedByText = new Map(oldRecipes.map((text, i) => [text, record.recipeChecked[i]]));
      const newRecipes = analysis.retouch_recipe || [];
      record.recipeChecked = newRecipes.map((text) =>
        /^重新構圖\s*[:：]/.test(text) ? true : checkedByText.get(text) !== false);
    }
    record.analysis = analysis;
    record.aspectRatio = pref;
  }

  function offerPendingReframeEdit(record) {
    const pref = pendingReframeOffers.get(record);
    if (!pref) return;
    pendingReframeOffers.delete(record);
    offerEditAfterReframe(record, pref);
  }

  // Asks whether to run 美化 on the new composition now; 立即美化 presses the
  // panel's own 美化 button so the usual cost confirm and submit path apply.
  async function offerEditAfterReframe(owner, pref) {
    const done = pref === "auto" ? tr("已由 AI 選擇比例並完成重新構圖。")
      : pref === "original" ? tr("已依原圖比例完成重新構圖。")
        : tr("已依 {pref} 完成重新構圖。", { pref });
    const editNow = await showConfirmModal(
      tr("{done}要直接以新構圖美化照片，還是先繼續調整其他建議？", { done }),
      { confirmLabel: tr("立即美化"), cancelLabel: tr("繼續調整"), danger: false }
    );
    const btn = els.inlineAnalysisContinueBtn;
    if (!editNow || !btn || btn.hidden || btn.disabled) return;
    if ((currentDetailBatchRecord() || singleFlowState) !== owner) return;
    btn.click();
  }

  async function requestReframe({ jobId, file, analysis, aspectRatio, analysisModel, path }) {
    const data = await reframeJob({ jobId, file, analysis, aspectRatio, analysisModel, path });
    if (path === "aipoc") return data.analysis;
    await pollJobStatus(data.job_id, { path: "local" });
    return (await getAnalyzeResult(data.job_id)).result.analysis;
  }

  // The recipe list is rebuilt from the new analysis; every item the reframe
  // left unchanged keeps the checked state the user gave it.
  function adoptReframedAnalysis(owner, analysis, state) {
    const labelOf = (li) => li.querySelector(".step-cb-label")?.textContent || "";
    const checkedByLabel = new Map();
    els.apRecipeList?.querySelectorAll("li").forEach((li) => {
      const input = li.querySelector('input[type="checkbox"]');
      if (input) checkedByLabel.set(labelOf(li), input.checked);
    });
    owner.analysis = analysis;
    if (state) applyCompositionToRender(state);
    refreshRecipeList(analysis);
    els.apRecipeList?.querySelectorAll("li").forEach((li) => {
      const input = li.querySelector('input[type="checkbox"]');
      const label = labelOf(li);
      if (input && checkedByLabel.has(label) && !REFRAME_LABEL_RE.test(label)) {
        input.checked = checkedByLabel.get(label);
      }
    });
  }

  function reframeLoadingMessage(pref) {
    if (pref === "auto") return tr("由 AI 選擇比例並重新構圖中…");
    if (pref === "original") return tr("依原圖比例重新構圖中…");
    return tr("依 {pref} 重新構圖中…", { pref });
  }

  // Shows the reframing indicators only for the photo on screen; the batch-wide
  // run button stays held while any photo is still being reframed.
  function syncReframeBusy() {
    const owner = reframeOwnerOnScreen();
    const busy = !!owner && reframesInFlight.has(owner);
    const pref = busy ? reframesInFlight.get(owner) : "";
    if (els.reframeLoading) els.reframeLoading.hidden = !busy;
    if (els.reframeLoadingText) els.reframeLoadingText.textContent = busy ? reframeLoadingMessage(pref) : "";
    els.apRecipeList?.querySelectorAll("li").forEach((li) => {
      const reframeItem = REFRAME_LABEL_RE.test(li.querySelector(".step-cb-label")?.textContent || "");
      const reframing = busy && reframeItem;
      li.classList.toggle("is-reframing", reframing);
      li.querySelector(".reframe-busy-label")?.remove();
      if (reframing) {
        const note = document.createElement("span");
        note.className = "reframe-busy-label";
        note.textContent = tr("重新構圖中…");
        li.querySelector("label")?.appendChild(note);
      }
    });
    [
      [els.inlineAnalysisContinueBtn, busy],
      [els.inlineAnalysisBatchRunBtn, reframesInFlight.size > 0]
    ].forEach(([btn, hold]) => {
      if (!btn) return;
      if (hold) {
        if (!("reframeHeld" in btn.dataset)) btn.dataset.reframeHeld = btn.disabled ? "1" : "";
        btn.disabled = true;
      } else if ("reframeHeld" in btn.dataset) {
        btn.disabled = btn.dataset.reframeHeld === "1";
        delete btn.dataset.reframeHeld;
      }
    });
  }

  // #ap-skip-analyze-btn (a 「只照需求改」 case, see #ap-skip-hint): runs the
  // analysis the case skipped, right here, and fills the panel with it.
  if (els.apSkipAnalyzeBtn) {
    els.apSkipAnalyzeBtn.addEventListener("click", () => analyzeSkippedCase());
  }

  // 美化 doesn't come through here: it runs the analysis and the edit as one
  // combined job (onContinue in waitForInlineAnalysisAction).
  async function analyzeSkippedCase() {
    const state = singleFlowState;
    if (!caseUnanalyzed() || !state.analyzeJobId) return;
    const confirmed = await showConfirmModal(
      tr("AI 會分析整張照片，整理出修圖建議；你寫的個人化需求會保留。要繼續嗎？"),
      { confirmLabel: tr("開始分析"), danger: false, costSteps: [analysisPreflightStep()] }
    );
    if (!confirmed || singleFlowState !== state) return;
    // The case on screen, not singleFlowState: stepping to another version
    // replaces that object but keeps the case, and the result still belongs.
    const myToken = historyViewToken;
    const continuing = !!activeContinuation();
    setSkipHintBusy(true);
    hideError();
    const restoreCanvas = showSkippedCaseAnalyzing();
    let landed = false;
    try {
      const jobId = await analyzeExistingJob({
        jobId: state.analyzeJobId,
        preset: state.presetKey,
        imageQuality: state.imageQuality,
        imageModel: state.imageModel,
        analysisModel: state.analysisModel,
        aspectRatio: state.aspectRatio,
        outputScale: state.outputScale,
        aiPhotoEditing: state.aiPhotoEditing,
        runQa: state.runQa,
        singlePassEdit: state.singlePassEdit,
        advancedOptions: readAdvancedOptionsFromDom("ap"),
        userPrompt: els.promptInput?.value.trim() || "",
        user: getCurrentUser()
      });
      await pollJobStatus(jobId, { path: "local" });
      const analysis = (await getAnalyzeResult(jobId)).result.analysis;
      if (historyViewToken !== myToken) return;
      adoptSkippedCaseAnalysis(analysis, jobId);
      landed = true;
    } catch (error) {
      if (historyViewToken === myToken) showError(tr("分析失敗：{v1}", { v1: error.message || error }));
    } finally {
      // Left alone once another record took the screen: opening it already
      // tore this loading card down and put its own view up.
      if (historyViewToken === myToken) restoreCanvas();
      setSkipHintBusy(false);
    }
    // After the restore, which puts the strip back as it was: the analysis
    // is a new version, so the strip comes up with it at the end even for a
    // case that had only one edit to show before.
    if (landed && historyViewToken === myToken) {
      if (continuing) showAnalysisFromOriginal();
      refreshVersionsAfterEdit({ pendingAnalysis: true }).catch(() => {});
    }
  }

  // 幫我分析 asked on a version base: the analysis is the photo's, so the
  // panel moves to 從原圖美化 to show it, carrying the request over the way
  // switching there by hand does (exitPanelContinuation).
  function showAnalysisFromOriginal() {
    const run = singleRuns?.[singleMirrorIndex];
    if (!run) return;
    const stash = continuationStashOf(activeContinuation(), run.versionId);
    applyHistoryVersionContext(run, null);
    // Asked for, so it is put to use: 僅依需求修改 off, as adoptSkippedCaseAnalysis does.
    lastSingleSelections = { ...lastSingleSelections, overwritePrompt: false };
    if (singleFlowState.origin) singleFlowState.origin.selections.overwritePrompt = false;
    const defaultRun = isContinuationRun(run) && canContinueFromRun(run) ? run : null;
    panelBase = { owner: singleFlowState, run, defaultRun };
    panelContinuation = null;
    panelContinuationStash = stash && { ...stash, owner: singleFlowState };
    syncVersionStripCurrent();
    openInlineAnalysisForCurrentContext(true);
  }

  // The same 正在分析照片 card (thinking rings + analyze progress feed) a
  // fresh upload shows, played in place over the photo the way a revise's
  // 美化 does, instead of leaving the analysis to a line of text in the
  // panel. Returns the undo. The panel is put away while it plays, as a
  // revise's 美化 does, and reopened with the analysis in it. The version
  // strip is only hidden, not torn down (hideVersionStrip): the analysis is
  // a version without an image to show there, so it comes back as it was.
  function showSkippedCaseAnalyzing() {
    const prevStage = flowStage;
    const stripWasHidden = els.versionStrip ? els.versionStrip.hidden : true;
    const panelWasOpen = !!els.resultsCanvasRow?.classList.contains("showing-analysis");
    // Straight into #canvas-loading, no photo for the close to animate
    // against -- same as backToProcessFromResults' 美化.
    if (panelWasOpen) {
      els.resultsCanvasRow.classList.add("no-transition");
      closeInlineAnalysisPanel();
    }
    setFlowStage("analyzing");
    setAnalysisPreviewDisabled(true);
    if (els.canvasLoadingThumb) els.canvasLoadingThumb.src = originalImgThumbUrl || els.originalImg.src;
    setProcTitle(tr("正在分析照片"));
    startProcSubRotation(analyzeSubMessages);
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = true;
    if (els.versionStrip) els.versionStrip.hidden = true;
    if (els.canvasLoading) els.canvasLoading.hidden = false;
    if (panelWasOpen) requestAnimationFrame(() => els.resultsCanvasRow.classList.remove("no-transition"));
    return () => {
      stopProcSubRotation();
      if (els.canvasLoading) els.canvasLoading.hidden = true;
      if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
      if (els.versionStrip) els.versionStrip.hidden = stripWasHidden;
      // backToProcessFromResults' wait for 美化 is still listening: only the
      // panel was put away, so it is put back the same way it opened.
      if (panelWasOpen) openInlineAnalysisPanel(false);
      setAnalysisPreviewDisabled(false);
      setFlowStage(prevStage);
      updateInlineAnalysisContinueBtnState();
    };
  }

  // Fills the panel the way opening an analyzed case would, keeping what the
  // user typed into 個人化需求 meanwhile. 只照需求改 is turned off: asking for
  // the analysis is asking for the whole photo to be worked on. jobId is the
  // analysis' own job, now the case's newest version (api.analyze_existing):
  // the next 美化 continues from it.
  function adoptSkippedCaseAnalysis(analysis, jobId) {
    historyCaseAnalysis = analysis;
    historyCaseAnalysisJobId = jobId || null;
    // On a version base the panel is rebuilt on 原圖 once the canvas is back
    // (showAnalysisFromOriginal); this one keeps showing the version.
    if (activeContinuation()) return;
    const userPrompt = els.promptInput?.value || "";
    // renderAnalysisPreview rebuilds the style groups at their defaults.
    const { options: styleOptions } = readStyleSelectionsFromDom();
    singleFlowState.analysis = analysis;
    if (jobId) singleFlowState.analyzeJobId = jobId;
    setCurrentCaseAnalysisVersion(analysis);
    lastSingleSelections = { ...(lastSingleSelections || {}), userPrompt, overwritePrompt: false };
    renderAnalysisPreview({ analysis, preset: singleFlowState.preset, expanded: true,
      requestOnly: isContinuationRun(singleRuns?.[singleMirrorIndex]), versionCard: overviewOnVersion() });
    applyAdvancedOptionsOption(recordAdvancedOptions(singleFlowState.advancedOptions, analysis), "ap");
    applyStyleSelections({ options: styleOptions, recipeChecked: null });
    if (els.promptInput) els.promptInput.value = userPrompt;
    if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = false;
    updateInlineAnalysisContinueBtnState();
  }

  const SKIP_HINT_TEXT = tr("這個版本只依照個人化需求修圖，沒有分析整張照片，所以沒有 AI 的修圖建議。可以直接在下方修改個人化需求；想要 AI 給建議的話，請按「美化」左邊的「幫我分析」。");

  // #ap-skip-hint for the version on screen: a 接著這張調整 version is named
  // like the chat names it (第 N 版完成・由第 M 版調整) and summed up by the
  // chat's note on it, or failing that by the request it was made with.
  // Even once the case has been analyzed, such a version keeps its
  // request-only panel (renderAnalysisPreview's requestOnly) while it is the
  // base: its note in place of 照片總覽, no 人像檢查/畫面建議.
  function isContinuationRun(run) {
    return Boolean(run?.result?.analysis?._continuation) && run.versionNumber != null;
  }

  // Its own analysis is a stub: an analysis done since is the case's, so its
  // overview stays the version's card (#ap-skip-hint), not 照片總覽.
  function madeWithoutAnalysis(run) {
    return isContinuationRun(run) || Boolean(run?.result?.analysis?._skipped_analysis);
  }

  // The case has nothing but 「只照需求改」's stub analysis: not this version
  // alone, which keeps its stub even once another version was analyzed.
  // The photo pinned to 原圖 with the next 美化 from it too: the overview is
  // the original's -- 照片總覽 once analyzed, else #ap-skip-hint saying so --
  // not the card of the version stepped off. Picked on the strip with a
  // version as the base, the card stays that version's.
  function overviewOnOrigin() {
    return versionStripOriginPicked && !activeContinuation();
  }

  function overviewOnVersion() {
    return madeWithoutAnalysis(panelRun()) && !overviewOnOrigin();
  }

  // On a 原圖 pick or leaving one, without rebuilding the panel (which would
  // drop what is unsent): which of the two cards is up, and its words.
  function syncOverviewCard() {
    // A batch photo's panel has no versions: its card is renderAnalysisPreview's.
    if (currentDetailBatchRecord()) return;
    if (els.apSkipHint && els.apPhotoSummary && !isReviewSkipped()) {
      const version = overviewOnVersion();
      els.apSkipHint.hidden = !version;
      els.apPhotoSummary.hidden = version;
    }
    paintSkipHint();
  }

  function caseUnanalyzed() {
    return Boolean(singleFlowState?.analysis?._skipped_analysis) && !historyCaseAnalysis;
  }

  // #ap-image-aspect-ratio holds a 接著這張調整 round's shape, not the case's.
  let apRatioShowsContinuation = false;

  function activeContinuation() {
    return panelContinuation && panelContinuation.owner === singleFlowState ? panelContinuation : null;
  }

  function panelRun() {
    return panelBase?.owner === singleFlowState ? panelBase.run : singleRuns?.[singleMirrorIndex];
  }

  function continuationOn(run, prefill = null) {
    return {
      owner: singleFlowState,
      source: { version_id: run.versionId, job_id: run.jobId, run: run.sourceRun },
      versionNumber: run.versionNumber,
      thumbUrl: resultThumbnailUrl(run.jobId, { run: run.sourceRun }),
      prefill: prefill || { text: "", style_options: {}, aspect_ratio: "original" },
    };
  }

  // On each repaint of the panel: a new singleFlowState (another version or
  // case, a 美化) starts on its version's default base, unless a mode was
  // already put on it. Either way the panel shows what the version was made
  // with: 從原圖美化 its settings, 從第 N 版美化 its request.
  // A chat handoff with something unsent puts the chat's base on it instead:
  // 接著這張調整 on this very version is taken here, with its request; any
  // other is 從原圖美化 for now, so applyPendingModeHandoff, which runs only
  // then, puts the edits in or steps to the version carried on from.
  function ensurePanelBase() {
    if (panelBase?.owner === singleFlowState) return;
    const run = singleRuns?.[singleMirrorIndex];
    const defaultRun = isContinuationRun(run) && canContinueFromRun(run) ? run : null;
    panelBase = { owner: singleFlowState, run, defaultRun };
    if (activeContinuation()) return;
    const handoff = pendingModeHandoff?.case_id === currentCaseHistoryImageKey && !currentDetailBatchRecord()
      ? pendingModeHandoff : null;
    if (handoff?.idle) pendingModeHandoff = null;
    // Nothing unsent, but the chat page was on a version as its base: so is
    // this panel, with nothing typed -- the request it was made with if that
    // is this version's default base, as when it is opened here.
    const b = handoff?.idle ? handoff.base : null;
    if (b && run && canContinueFromRun(run) && run.versionId === b.version_id) {
      panelContinuation = { ...continuationOn(run, run === defaultRun ? madeWithRequest(run) : null), handed: true };
      applyHandedEngine(handoff);
      return;
    }
    if (!handoff || handoff.idle) {
      panelContinuation = defaultRun ? continuationOn(defaultRun, madeWithRequest(defaultRun)) : null;
      return;
    }
    const c = handoff.continuation;
    if (c && run && canContinueFromRun(run) && run.versionId === c.version_id) {
      pendingModeHandoff = null;
      panelContinuation = { ...continuationOn(run, { text: c.text || "", style_options: c.style_options || {},
        aspect_ratio: continuationAspectRatio(c.aspect_ratio) }), handed: true };
      applyHandedEngine(handoff);
      return;
    }
    panelContinuation = null;
  }

  // Carrying on from the chat's version: with the chat's engine too, picked
  // in its 設定 there, not the one that version was made with.
  function applyHandedEngine(handoff) {
    const model = findModelQualityOptionByPair(handoff.image_model, handoff.image_quality);
    if (!model || model.model !== handoff.image_model || model.quality !== handoff.image_quality) return;
    applyHandedOutputScale(handoff);
    if (singleFlowState?.imageModel === model.model && singleFlowState?.imageQuality === model.quality) return;
    applyModelQualityChange(model.value);
  }

  // 輸出尺寸 rides along with the engine; only a tier this page offers is
  // taken, so a value the panel can't show is left where it is.
  function applyHandedOutputScale(handoff) {
    const opt = findOutputScaleOption(handoff?.output_scale);
    if (!handoff?.output_scale || opt?.value !== handoff.output_scale) return;
    if (els.apImageOutputScaleSelect?.value === opt.value && singleFlowState?.outputScale === opt.value) return;
    applyOutputScaleChange(opt.value);
  }

  // 調整中: something unsent in the panel, or, while it is open, a base
  // picked other than its default. Looking at 原圖 with it as the base is
  // not: that is the default base of what is on screen (pickOriginAsBase).
  function isAdjusting() {
    if (isReviewDirty()) return true;
    if (panelBase?.owner !== singleFlowState) return false;
    if (!els.resultsCanvasRow?.classList.contains("showing-analysis")) return false;
    const cont = activeContinuation();
    if (!cont && versionStripOriginPicked) return false;
    return panelBase.defaultRun ? !isContinuationSource(cont, panelBase.defaultRun) : !!cont;
  }

  // A continuation's output shape: "original" keeps the version's own, and
  // "auto" has no analysis to decide from, so it keeps it too.
  function continuationAspectRatio(value) {
    return !value || value === "auto" ? "original" : value;
  }

  // What 接著這張調整 sends: the 補充指令 text, only the styles moved off
  // their default (one left out reads as default on the backend), and the
  // output shape, which #ap-image-aspect-ratio holds while continuing.
  function continuationRequestFromDom() {
    const { options } = readStyleSelectionsFromDom();
    return {
      text: els.promptInput ? els.promptInput.value.trim() : "",
      style_options: Object.fromEntries(Object.entries(options).filter(([, v]) => v && v !== "default")),
      aspect_ratio: continuationAspectRatio(els.apImageAspectRatioSelect?.value),
    };
  }

  // The request a 接著這張調整 version was made with, as the panel shows it.
  function madeWithRequest(run) {
    const style = run?.result?.analysis?.style_options || {};
    return {
      text: (run?.versionParams?.user_prompt || "").trim(),
      style_options: Object.fromEntries(Object.entries(style).filter(([, v]) => v && v !== "default")),
      aspect_ratio: continuationAspectRatio(run?.versionParams?.aspect_ratio),
    };
  }

  function continuationRequestKey(request) {
    const style = request?.style_options || {};
    return JSON.stringify([(request?.text || "").trim(), Object.keys(style).sort().map((k) => [k, style[k]]),
      continuationAspectRatio(request?.aspect_ratio)]);
  }

  function isEmptyContinuationRequest(request) {
    return !request || (!request.text && !Object.keys(request.style_options || {}).length
      && continuationAspectRatio(request.aspect_ratio) === "original");
  }

  // Unsent is a request other than the one the base version was made with,
  // whichever base is picked: another version's request carried onto it
  // counts, even when the base's own was not empty.
  function continuationRequestDirty(cont) {
    const base = singleRuns?.find((run) => isContinuationSource(cont, run));
    return continuationRequestKey(pendingContinuationRequest()) !== continuationRequestKey(madeWithRequest(base));
  }

  // 美化 on a 接著這張調整 version with the request it was made with, unchanged,
  // is the chat's 再美化: that request again from the version it was made
  // from, for another take -- not the same request a second time on top of
  // it. The version to send from then; null when the request was changed or
  // is empty, or that version is gone.
  function continuationRedoSource(cont) {
    const from = singleRuns?.find((run) => isContinuationSource(cont, run))?.versionParams?.source_result;
    if (!from || continuationRequestDirty(cont) || isEmptyContinuationRequest(pendingContinuationRequest())) return null;
    return singleRuns.some((run) => run.versionId === from.version_id) ? from : null;
  }

  // A 設定 change is asked for too: the engine or 輸出尺寸 美化 would use,
  // against what the base version was made with. A version with no params
  // on record has nothing to tell against, so it reads as unchanged.
  function continuationEngineChanged(cont) {
    const params = singleRuns?.find((run) => isContinuationSource(cont, run))?.versionParams;
    if (!params?.image_model || !singleFlowState) return false;
    const { imageModel, imageQuality, outputScale } = singleFlowState;
    if (params.image_model !== imageModel || (params.image_quality || imageQuality) !== imageQuality) return true;
    if (!engineIsScalable(findModelQualityOptionByPair(imageModel, imageQuality)?.value)) return false;
    return findOutputScaleOption(params.output_scale)?.value !== findOutputScaleOption(outputScale)?.value;
  }

  // The panel open: what is in it; closed by 取消: what was kept from it.
  function pendingContinuationRequest() {
    const cont = activeContinuation();
    if (!cont) return null;
    const panelShowing = !!els.resultsCanvasRow?.classList.contains("showing-analysis");
    return panelShowing ? continuationRequestFromDom() : cont.prefill;
  }

  function canContinueFromRun(run) {
    return renderFromVersionEnabled && !currentDetailBatchRecord() && !!run?.versionId && isSelectableRun(run);
  }

  async function switchPanelBase(fn) {
    panelBaseSwitching = true;
    try {
      await fn();
    } finally {
      panelBaseSwitching = false;
      syncApContinueBase();
    }
  }

  // Puts the revise panel into 接著這張調整 on `run`, stepping to it first
  // when it is not the one on screen (a chat handoff can name any version).
  function enterPanelContinuation(run, prefill = null) {
    return switchPanelBase(() => stepIntoContinuation(run, prefill));
  }

  async function stepIntoContinuation(run, prefill) {
    if (!singleRuns || !canContinueFromRun(run)) return;
    // From one version to another (#ap-continue-base): the request goes
    // along unchanged, as the chat's does when its base is switched.
    if (!prefill && activeContinuation()) prefill = pendingContinuationRequest();
    // From 從原圖美化: the unsent 個人化需求 and styles come along as the
    // request, and the rest waits for 原圖 to be picked again (panelOriginStash).
    let carried = null;
    if (!activeContinuation() && isReviewDirty()) {
      const { request, rest } = splitUnsentReview();
      carried = request;
      panelOriginStash = Object.keys(rest).length ? { token: historyViewToken, review: rest } : null;
    }
    const idx = singleRuns.indexOf(run);
    if (idx < 0) return;
    if (idx !== singleMirrorIndex) {
      // Picked as the base, not just looked at: the panel moves onto it even
      // while 調整中 (renderSingleRunSwitcher), its card naming that version.
      // What is unsent came along above (prefill, panelOriginStash).
      panelFollowsStep = true;
      try {
        await selectRunAt(idx);
      } finally {
        panelFollowsStep = false;
      }
      if (singleRuns[singleMirrorIndex] !== run || run.status !== "done") return;
    }
    const stash = panelContinuationStash;
    panelContinuationStash = null;
    if (!prefill && !isEmptyContinuationRequest(carried)) prefill = carried;
    if (!prefill && stash?.owner === singleFlowState && stash.versionId === run.versionId) prefill = stash.prefill || null;
    panelContinuation = continuationOn(run, prefill);
    // Off 原圖 onto the version already on screen: no step to put the photo
    // back on its After side, so that happens here (as the strip's tile does).
    if (versionStripOriginPicked) {
      clearVersionStripOriginPick();
      setCompareView(true);
    }
    syncVersionStripCurrent();
    openInlineAnalysisForCurrentContext(true);
  }

  function exitPanelContinuation() {
    return switchPanelBase(stepOutOfContinuation);
  }

  // What leaving 接著這張調整 for 從原圖美化 carries (panelContinuationStash,
  // less its owner): the unsent request and any 設定 change. Null when neither.
  function continuationStashOf(cont, versionId) {
    const request = pendingContinuationRequest();
    const prefill = isEmptyContinuationRequest(request) ? null : request;
    const { imageModel, imageQuality, outputScale } = singleFlowState || {};
    const engine = cont && continuationEngineChanged(cont)
      ? { image_model: imageModel, image_quality: imageQuality, output_scale: outputScale } : null;
    return prefill || engine ? { versionId, prefill, engine } : null;
  }

  function stepOutOfContinuation() {
    const cont = activeContinuation();
    if (!cont) return;
    const stash = continuationStashOf(cont, cont.source.version_id);
    panelContinuationStash = stash && { ...stash, owner: singleFlowState };
    if (stash?.engine) resetToVersionEngine(cont);
    panelContinuation = null;
    // The next 美化 starts from the original, so the photo shows it, as the
    // strip's 原圖 tile does.
    pickVersionStripOrigin();
    openInlineAnalysisForCurrentContext(true);
  }

  // Back to the engine `cont`'s version was made with, as showing it puts it
  // (applyHistoryVersionContext). The change is put back on 原圖 as the
  // user's (applyContinuationStash), so there it reads as one -- 調整中 and
  // 設定's 已變更 -- instead of going into that side's baselines.
  function resetToVersionEngine(cont) {
    const params = singleRuns?.find((run) => isContinuationSource(cont, run))?.versionParams;
    if (!params?.image_model || !singleFlowState) return;
    singleFlowState.imageModel = params.image_model;
    if (params.image_quality) singleFlowState.imageQuality = params.image_quality;
    singleFlowState.outputScale = findOutputScaleOption(params.output_scale)?.value;
    applyModelQualityOption(findModelQualityOptionByPair(singleFlowState.imageModel, singleFlowState.imageQuality));
    applyOutputScaleOption(findOutputScaleOption(singleFlowState.outputScale));
  }

  // Like the chat, one request across both modes: what 接著這張調整 had is put
  // in 從原圖美化 as unsent edits, once (a reopen after 取消 keeps its own).
  function applyContinuationStash() {
    const stash = panelContinuationStash;
    if (!stash || stash.owner !== singleFlowState || stash.applied) return;
    stash.applied = true;
    if (stash.engine) {
      const { image_model: model, image_quality: quality } = stash.engine;
      const opt = findModelQualityOptionByPair(model, quality);
      if (opt && opt.model === model && opt.quality === quality) applyModelQualityChange(opt.value);
      applyHandedOutputScale(stash.engine);
    }
    if (!stash.prefill) return;
    const { text, style_options: style, aspect_ratio: ratio } = stash.prefill;
    if (text && els.promptInput) els.promptInput.value = text;
    if (Object.keys(style).length) applyStyleSelections({ options: { ...readStyleSelectionsFromDom().options, ...style } });
    // A shape picked for the version is one for the original too; "original"
    // (keep the version's) has no counterpart there, so the case's stays.
    const shape = continuationAspectRatio(ratio);
    if (shape !== "original" && findAspectRatioOption(shape)?.value === shape) applyAspectRatioChange(shape);
  }

  function applyOriginStash() {
    const stash = panelOriginStash;
    panelOriginStash = null;
    if (stash?.token === historyViewToken) applyReviewState(stash.review);
  }

  // #ap-continue-base, above the tabs: which image the
  // next 美化 starts from -- 原圖, or any version to adjust -- as the chat's
  // pill above its composer offers it. Every version of the case is listed,
  // not just the one on screen; picking one steps to it.
  function apContinueRuns() {
    return singleRuns ? singleRuns.filter(canContinueFromRun) : [];
  }

  function apContinueOriginThumb() {
    return els.versionStripTrack?.querySelector(".version-origin-tile img")?.src || originalImgThumbUrl;
  }

  function isContinuationSource(cont, run) {
    return !!cont && cont.source.version_id === run.versionId
      && cont.source.job_id === run.jobId && cont.source.run === run.sourceRun;
  }

  const AP_CONTINUE_TICK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function apContinueMenuItem({ picked, thumb, label, sub, onPick }) {
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitemradio");
    item.setAttribute("aria-checked", String(picked));
    if (picked) item.className = "is-picked";
    const tick = document.createElement("span");
    tick.className = "ap-continue-tick";
    if (picked) tick.innerHTML = AP_CONTINUE_TICK;
    const img = document.createElement("img");
    img.className = "ap-continue-shot";
    img.src = thumb;
    img.alt = "";
    img.loading = "lazy";
    item.append(tick, img, label);
    if (sub) {
      const subEl = document.createElement("span");
      subEl.className = "ap-continue-sub";
      subEl.textContent = sub;
      item.append(subEl);
    }
    item.addEventListener("click", async () => {
      closeApContinueMenu();
      if (picked) return;
      await onPick();
      // Either way can be turned down (the confirm modals): show what holds.
      syncApContinueBase();
    });
    return item;
  }

  function renderApContinueMenu() {
    const menu = els.apContinueMenu;
    const cont = activeContinuation();
    menu.textContent = "";
    menu.append(apContinueMenuItem({
      picked: !cont, thumb: apContinueOriginThumb(), label: tr("原圖"), onPick: exitPanelContinuation,
    }));
    for (const run of apContinueRuns()) {
      menu.append(apContinueMenuItem({
        picked: isContinuationSource(cont, run),
        thumb: resultThumbnailUrl(run.jobId, { run: run.sourceRun }),
        label: tr("第 {n} 版", { n: run.versionNumber }),
        // A version of several images lists each, told apart like the tiles.
        sub: run.runsInVersion > 1
          ? tr("第 {runInVersion} / {runsInVersion} 張", { runInVersion: run.runInVersion, runsInVersion: run.runsInVersion })
          : "",
        onPick: () => enterPanelContinuation(run),
      }));
    }
  }

  function closeApContinueMenu() {
    if (!els.apContinueMenu || els.apContinueMenu.hidden) return;
    els.apContinueMenu.hidden = true;
    els.apContinuePick?.setAttribute("aria-expanded", "false");
  }

  // Shown only while 美化 is offered, with a version to pick besides 原圖.
  function syncApContinueBase() {
    if (!els.apContinueBase || panelBaseSwitching) return;
    syncRedoLabel();
    syncApPromptIntro();
    const runVisible = !!(els.inlineAnalysisContinueBtn && !els.inlineAnalysisContinueBtn.hidden);
    const show = runVisible && apContinueRuns().length > 0;
    els.apContinueBase.hidden = !show;
    if (!show) {
      closeApContinueMenu();
      return;
    }
    // A version just stepped to, before ensurePanelBase put its default base
    // on it: no base to show yet, which is not 原圖.
    if (panelBase?.owner !== singleFlowState) return;
    const cont = activeContinuation();
    els.apContinueShot.src = cont ? cont.thumbUrl : apContinueOriginThumb();
    els.apContinueLabel.textContent = cont ? tr("第 {n} 版", { n: cont.versionNumber }) : tr("原圖");
    // The words around the pill, from one template so a language can put
    // them on either side of it.
    const [lead, tail = ""] = tr("下一次從 {base} 美化").split("{base}");
    els.apContinueLead.textContent = lead.trim();
    els.apContinueTail.textContent = tail.trim();
    if (!els.apContinueMenu.hidden) renderApContinueMenu();
    syncApContinueExit();
  }

  // Once there are versions, the 個人化需求 kept from them reads like edits on
  // the latest; on 原圖 it is a redo from the photo with all of them, which
  // the intro says, as the chat's does.
  function syncApPromptIntro() {
    if (!els.apPromptIntro) return;
    const fromOriginal = !activeContinuation() && apContinueRuns().length > 0;
    els.apPromptIntro.textContent = fromOriginal
      ? tr("從原圖美化會從原圖重做一版，這裡的需求會一起套用，要調整的話可以先編輯。")
      : tr("說出你的想法，打造專屬美化效果");
  }

  // The X and #ap-editing-hint (編輯中, with 返回) show together.
  function syncApContinueExit() {
    if (panelBaseSwitching) return;
    const show = !!(els.apContinueBase && !els.apContinueBase.hidden
      && singleRuns?.[singleMirrorIndex]?.versionId && isAdjusting());
    if (els.apContinueExit) els.apContinueExit.hidden = !show;
    if (els.apEditingHint) els.apEditingHint.hidden = !show;
  }

  for (const btn of [els.apContinueExit, els.apEditingBack]) {
    if (btn) btn.addEventListener("click", () => {
      closeApContinueMenu();
      leaveAdjusting();
    });
  }

  // A photo picked on the strip or in the photo viewer while the panel is
  // open takes the base along, as the chat's preview does (pickPreview):
  // 調整中, the picked one becomes the base, with what is unsent; otherwise
  // the base is the default of what is now on screen -- 原圖 for 原圖.
  function panelBaseFollowsPick() {
    return versionStripRuns === singleRuns && !currentDetailBatchRecord()
      && !!els.resultsCanvasRow?.classList.contains("showing-analysis");
  }

  // 原圖 picked: false when it is only looked at.
  function pickOriginAsBase() {
    if (!panelBaseFollowsPick() || !activeContinuation()) return false;
    if (isAdjusting()) {
      exitPanelContinuation();
      return true;
    }
    // Not exitPanelContinuation: nothing is being carried, so the version's
    // own request must not turn into unsent edits on 原圖.
    panelContinuation = null;
    panelContinuationStash = null;
    pickVersionStripOrigin();
    openInlineAnalysisForCurrentContext(true);
    return true;
  }

  // Version `idx` picked: a promise when the base went with it, else false.
  function pickRunAsBase(idx) {
    if (!panelBaseFollowsPick()) return false;
    const run = singleRuns[idx];
    if (isAdjusting()) {
      if (!canContinueFromRun(run) || isContinuationSource(activeContinuation(), run)) return false;
      return enterPanelContinuation(run);
    }
    // Off 原圖 back onto the version on screen: its default base again.
    if (versionStripOriginPicked && idx === currentRunIndex && !activeContinuation()
      && panelBase?.owner === singleFlowState && panelBase.defaultRun === run) {
      panelContinuation = continuationOn(run, madeWithRequest(run));
      clearVersionStripOriginPick();
      setCompareView(true);
      openInlineAnalysisForCurrentContext(true);
      return Promise.resolve();
    }
    return false;
  }

  if (els.apContinuePick && els.apContinueMenu) {
    els.apContinuePick.addEventListener("click", () => {
      if (!els.apContinueMenu.hidden) {
        closeApContinueMenu();
        return;
      }
      renderApContinueMenu();
      els.apContinueMenu.hidden = false;
      els.apContinuePick.setAttribute("aria-expanded", "true");
    });
    // Clicking anywhere else or Esc closes it, like the chat's.
    document.addEventListener("mousedown", (event) => {
      if (!els.apContinueBase.contains(event.target)) closeApContinueMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeApContinueMenu();
    });
  }

  // ensureVersionNote's bookkeeping, declared ahead of paintSkipHint, which
  // reads it and may run before the code below it has.
  const VERSION_NOTE_POLL_MS = 3000;
  const VERSION_NOTE_MAX_POLLS = 40;
  const versionNoteAsks = new Map();
  const versionNotes = new Map();

  // Always the version the panel is for, never the next 美化's base: that
  // one is #ap-continue-base's (and the shell's description) to name.
  function paintSkipHint() {
    const run = panelRun();
    const continuation = isContinuationRun(run);
    const skipped = Boolean(run?.result?.analysis?._skipped_analysis);
    const named = run?.versionNumber != null && (continuation || skipped || !!activeContinuation());
    if (overviewOnOrigin()) {
      if (els.apSkipHintTitle) els.apSkipHintTitle.textContent = tr("原圖");
      if (els.apSkipHintText) paintSkipHintText(caseUnanalyzed()
        ? tr("{text}這張照片還沒分析過。想先看 AI 的修圖建議，請按「幫我分析」；或取消勾選「僅依需求修改」，按「美化」時會一起分析。", { text: "" }).trim()
        : SKIP_HINT_TEXT);
      return;
    }
    if (els.apSkipHintTitle) {
      els.apSkipHintTitle.textContent = !named ? tr("這張照片沒有做分析")
        : run.versionFrom ? tr("第 {n} 版完成・由第 {m} 版調整", { n: run.versionNumber, m: run.versionFrom })
        : tr("第 {n} 版完成", { n: run.versionNumber });
    }
    if (!els.apSkipHintText) return;
    const request = (run?.versionParams?.user_prompt || "").trim();
    if (!named) {
      paintSkipHintText(SKIP_HINT_TEXT);
      return;
    }
    if (!run.versionNote && versionNotes.has(run.versionId)) run.versionNote = versionNotes.get(run.versionId);
    if (!run.versionNote && !["pending", "processing", "error"].includes(run.status)) {
      ensureVersionNote(run.caseKey || currentCaseHistoryImageKey, run.versionId);
    }
    const waiting = !run.versionNote && versionNoteAsks.get(run.versionId) === "asking";
    let text = run.versionNote || (request ? tr("依你的需求調整：「{request}」", { request })
      : continuation || skipped ? tr("依你挑的風格調整。") : tr("依 AI 分析的建議美化。"));
    let tail = "";
    // The case not analyzed yet: 幫我分析 sits beside 美化 on either base.
    if (caseUnanalyzed()) {
      if (!/[。！？.!?]$/.test(text)) text += tr("。@@句尾");
      const full = activeContinuation()
        ? tr("{text}這張照片還沒分析過。「幫我分析」會分析原圖並整理修圖建議，完成後會換成從原圖美化。", { text })
        : tr("{text}這張照片還沒分析過。想先看 AI 的修圖建議，請按「幫我分析」；或取消勾選「僅依需求修改」，按「美化」時會一起分析。", { text });
      // What to press next goes on a line of its own, apart from the note.
      tail = full.slice(full.indexOf(text) + text.length).trim();
      text = full.slice(0, full.indexOf(text) + text.length);
    }
    paintSkipHintText(waiting ? null : text, tail);
  }

  // #ap-skip-hint-text while the version's note is still being written holds
  // the room a note usually takes, so the 個人化需求 below doesn't jump when it
  // lands; the note (or, with none coming, the short line) then fades in and
  // the box eases to its real height. `text` null = still waiting.
  function paintSkipHintText(text, tail = "") {
    const el = els.apSkipHintText;
    const wasWaiting = el.classList.contains("is-note-waiting");
    const before = el.getBoundingClientRect().height;
    el.classList.toggle("is-note-waiting", text == null);
    // The tail waits too: it follows the note, so it shows up with it.
    if (text == null) {
      const line = document.createElement("span");
      line.className = "ap-skip-hint-waiting";
      line.textContent = tr("正在整理這版的修圖說明…");
      el.replaceChildren(line);
      return;
    }
    const next = document.createElement("span");
    next.className = "ap-skip-hint-next";
    next.textContent = tail;
    el.replaceChildren(text, ...(tail ? [next] : []));
    if (!wasWaiting || !before || typeof el.animate !== "function"
        || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const after = el.getBoundingClientRect().height;
    el.animate([{ height: `${before}px`, opacity: 0 }, { height: `${after}px`, opacity: 1 }],
      { duration: 260, easing: "cubic-bezier(.2,.7,.3,1)" });
  }

  // A version made in the panel has no note until one is asked for: the
  // backend writes it on the first ask and answers "pending" until the
  // worker is done. Asked as soon as the version's result is in
  // (refreshVersionsAfterEdit), so the note is usually there before anyone
  // looks; and again from paintSkipHint for one opened from history. Each
  // version is asked about once per page load -- "asking" while polling,
  // then "done" / "none" for good, so repainting never re-asks -- and the
  // note lands on every run of that version, single view and batch alike.
  function ensureVersionNote(caseKey, versionId) {
    const user = getCurrentUser();
    if (!user || !caseKey || !versionId || versionNoteAsks.has(versionId)) return;
    versionNoteAsks.set(versionId, "asking");
    const settle = (note) => {
      versionNoteAsks.set(versionId, note ? "done" : "none");
      if (note) {
        versionNotes.set(versionId, note);
        const runLists = [singleRuns, ...batchRecords.map((record) => record.runs)];
        runLists.forEach((runs) => (runs || []).forEach((r) => {
          if (r.versionId === versionId) r.versionNote = note;
        }));
      }
      if (panelRun()?.versionId === versionId) paintSkipHint();
    };
    (async () => {
      for (let attempt = 0; attempt < VERSION_NOTE_MAX_POLLS; attempt += 1) {
        let answer;
        try {
          answer = await fetchVersionNote(user, caseKey, versionId);
        } catch (error) {
          return settle(null); // an older backend without the endpoint, or a gone case
        }
        if (answer?.status === "none") return settle(null);
        if (answer?.status === "done" && answer.note) return settle(answer.note);
        await new Promise((resolve) => setTimeout(resolve, VERSION_NOTE_POLL_MS));
      }
      settle(null);
    })();
  }

  function setSkipHintBusy(busy) {
    if (els.apSkipAnalyzeBtn) {
      els.apSkipAnalyzeBtn.disabled = busy;
      els.apSkipAnalyzeBtn.textContent = busy ? tr("分析中…") : tr("幫我分析");
    }
    if (busy && els.apSkipHintText) {
      els.apSkipHintText.textContent = tr("正在分析整張照片，完成後建議會顯示在這裡。");
    } else if (!busy) {
      paintSkipHint();
    }
    const btn = els.inlineAnalysisContinueBtn;
    if (!btn) return;
    if (busy) {
      btn.dataset.skipHintHeld = btn.disabled ? "1" : "";
      btn.disabled = true;
    } else if ("skipHintHeld" in btn.dataset) {
      btn.disabled = btn.dataset.skipHintHeld === "1";
      delete btn.dataset.skipHintHeld;
    }
  }

  // 生成張數 (single-image RUN count) can now be preset on the upload screen,
  // ahead of analysis -- keep it and the analysis-preview's own #ap-run-count
  // mirrored the same way quality/model already are, so whichever one the
  // user touches (before or after analyzing) carries over to the other.
  function applyRunCountChange(runCount) {
    const value = String(Math.max(1, parseInt(runCount, 10) || 1));
    if (els.imageRunCountSelect) els.imageRunCountSelect.value = value;
    if (els.apRunCountSelect) els.apRunCountSelect.value = value;
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
    els.apRunCountSelect.addEventListener("change", () => {
      applyRunCountChange(els.apRunCountSelect.value);
      updateInlineAnalysisContinueBtnState();
    });
  }

  // Advanced Settings (人像設定) exist as TWO independent copies, the same way
  // 分析模型/修圖模型/長寬比 do: #advanced-settings-fold on the upload page
  // (what a fresh submit sends) and #ap-advanced-settings-fold in the results
  // page's own settings modal (what a case's revise panel shows and sends).
  // Every read and write names which one it means, so displaying a case's
  // settings can never disturb what the upload page is set to. They used to be
  // one fold reparented between the two modals, which is exactly how a case's
  // values ended up carried into the next, unrelated upload.
  function advancedOptionControls(scope) {
    return scope === "ap"
      ? {
        eyeClosed: els.apEyeClosedDetectionToggle,
        gaze: els.apEyeGazeFocusToggle,
        hair: els.apHairMessDetectionToggle,
        smile: els.apSmileAdjustmentToggle,
      }
      : {
        eyeClosed: els.eyeClosedDetectionToggle,
        gaze: els.eyeGazeFocusToggle,
        hair: els.hairMessDetectionToggle,
        smile: els.smileAdjustmentToggle,
      };
  }

  // `scope` is deliberately required, not defaulted: a call site that forgot to
  // say which copy it means would silently read the other page's toggles, which
  // is the whole class of bug this split exists to remove.
  function readAdvancedOptionsFromDom(scope) {
    const controls = advancedOptionControls(scope);
    return {
      eye_closed_detection: controls.eyeClosed ? controls.eyeClosed.checked : true,
      gaze_contact_detection: controls.gaze ? controls.gaze.checked : true,
      hair_mess_detection: controls.hair ? controls.hair.checked : true,
      smile_adjustment: controls.smile ? controls.smile.checked : true,
    };
  }

  // Pose choices suggest compatible settings for every person in this photo.
  // They deliberately do not touch the shared 人像修正設定 modal; "-" is simply
  // absent here and therefore leaves all individual values unchanged.
  const POSE_PERSON_SUGGESTIONS = {
    playful: {
      notices: [{
        categories: ["smile_issue"],
        setting: tr("笑容：開朗笑"),
        reason: tr("讓表情與活潑、輕鬆的姿勢一致")
      }]
    },
    candid: {
      notices: [{
        categories: ["gaze_issue"],
        setting: tr("眼神對焦：關閉"),
        reason: tr("保留自然、非擺拍的鏡頭外視線")
      }]
    },
    elegant: {
      notices: [{
        categories: ["smile_issue"],
        setting: tr("笑容：微笑"),
        reason: tr("讓表情維持優雅、自然且不過度張揚")
      }]
    },
    dynamic: {
      notices: [
        {
          categories: ["gaze_issue"],
          setting: tr("眼神對焦：關閉"),
          reason: tr("讓視線配合自然的動態瞬間")
        },
        {
          categories: ["hair_issue"],
          setting: tr("亂髮偵測：保留髮絲動態"),
          reason: tr("保留人物移動時自然的髮絲方向")
        }
      ]
    },
    cool: {
      notices: [{
        categories: ["smile_issue"],
        setting: tr("笑容：不笑"),
        reason: tr("維持冷靜、俐落的姿態氛圍")
      }]
    },
    vsign: {
      notices: [{
        categories: ["smile_issue"],
        setting: tr("笑容：開朗笑"),
        reason: tr("讓表情與 V-Sign 的輕鬆、開朗氛圍一致")
      }]
    },
    cheer: {
      notices: [{
        categories: ["smile_issue"],
        setting: tr("笑容：開朗笑"),
        reason: tr("讓表情與開放、歡呼的姿勢一致")
      }]
    }
  };

  const POSE_OPTION_LABELS = {
    playful: tr("俏皮"), candid: tr("自然抓拍"), strong: tr("強勢"), elegant: tr("優雅"),
    dynamic: tr("動感"), cool: tr("酷帥"), vsign: tr("勝利手勢"), cheer: tr("歡呼")
  };

  const POSE_RESTORE_NOTICES = {
    gaze_issue: tr("眼神對焦：已還原為原本設定"),
    hair_issue: tr("亂髮偵測：已還原為原本設定"),
    smile_issue: tr("笑容：已還原為原本設定")
  };

  function showPoseSuggestionNotice(pose, definition, changes) {
    const restoredCategories = changes.restoredCategories || [];
    const appliedCategories = changes.appliedCategories || [];
    const applicable = (definition?.notices || []).filter((notice) =>
      notice.categories.some((category) => appliedCategories.includes(category)));
    if (!restoredCategories.length && !applicable.length) return;
    const poseLabel = POSE_OPTION_LABELS[pose] || pose;
    const previousPoseLabel = POSE_OPTION_LABELS[changes.previousPose] || changes.previousPose;
    let title;
    if (!applicable.length && previousPoseLabel) {
      title = pose === "default"
        ? tr("已取消 {previousPoseLabel} 的自動調整", { previousPoseLabel })
        : tr("已切換為 {poseLabel}", { poseLabel });
    } else if (restoredCategories.length) {
      title = tr("已配合 {poseLabel} 調整個人設定", { poseLabel });
    } else {
      title = tr("已配合 {poseLabel} 自動調整", { poseLabel });
    }
    const lines = [title];
    restoredCategories.forEach((category) => {
      const notice = POSE_RESTORE_NOTICES[category];
      if (notice) lines.push(notice);
    });
    applicable.forEach((notice) => {
      lines.push(notice.setting, tr("原因：{reason}", { reason: notice.reason }));
    });
    showNoticeModal(lines.join("\n"));
  }

  function applyPoseSuggestions(pose, { notify = true } = {}) {
    const definition = POSE_PERSON_SUGGESTIONS[pose];
    const changes = syncFaceReviewPoseSuggestion(definition ? pose : null);
    if (notify && (changes.restoredCategories.length || changes.appliedCategories.length)) {
      showPoseSuggestionNotice(pose, definition, changes);
    }
  }

  // renderAnalysisPreview rebuilds the Pose radios, then applyStyleSelections
  // restores a saved choice with `.checked = true`. Programmatic checked
  // changes do not emit `change`, so explicitly rebuild the per-person
  // suggestions too. Restoring an already-saved pick is silent.
  function syncRestoredPoseSuggestions(pose) {
    if (!pose) return;
    applyPoseSuggestions(pose, { notify: false });
  }

  // Syncs the toggles' displayed checked state to `advancedOptions` without
  // pushing it onto any state -- same split as applyModelQualityOption vs.
  // applyModelQualityChange, so switching which batch record's revise panel
  // is open (startInlineBatchReview) can show that record's own effective
  // settings without also overwriting them.
  function applyAdvancedOptionsOption(advancedOptions, scope) {
    if (!advancedOptions) return;
    const controls = advancedOptionControls(scope);
    if (controls.eyeClosed) controls.eyeClosed.checked = !!advancedOptions.eye_closed_detection;
    if (controls.gaze) controls.gaze.checked = !!advancedOptions.gaze_contact_detection;
    if (controls.hair) controls.hair.checked = !!advancedOptions.hair_mess_detection;
    // Defaulted the way the backend defaults it (`.get(key, True)`) rather
    // than with !!: a record analyzed before 笑容調整 existed carries no such
    // key, and showing the switch off there would misreport what a revise of
    // it would actually do.
    if (controls.smile) controls.smile.checked = advancedOptions.smile_adjustment !== false;
    // The per-person review these lock rows in only exists on the results
    // page, so only that copy drives it.
    if (scope === "ap") {
      syncFaceReviewAdvancedOptions(advancedOptions);
      // Programmatic apply (showing a case, or resetSettingsToDefaults) --
      // this IS the case's own set, so it becomes the baseline the 已變更
      // flag measures against.
      refreshApCaseSettings();
    }
  }

  // Lets a revise on the results page change these toggles and have it
  // actually apply to the next edit -- same override rule as
  // applyModelQualityChange: writes onto whichever batch record's revise
  // panel is currently open, else the batch-wide default, else the
  // single-image flow's own state. A no-op before any submit, same as
  // applyModelQualityChange in that window (singleFlowState/batchSettings
  // are both still null).
  function applyAdvancedOptionsChange() {
    const advancedOptions = readAdvancedOptionsFromDom("ap");
    if (singleFlowState) singleFlowState.advancedOptions = advancedOptions;
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) {
      batchRecord.advancedOptions = advancedOptions;
    } else if (batchSettings) {
      batchSettings.advancedOptions = advancedOptions;
    }
    syncFaceReviewAdvancedOptions(advancedOptions);
    updateInlineAnalysisContinueBtnState();
    refreshApCaseSettings({ fromUser: true });
  }

  function defaultAdvancedOptions() {
    return { ...((window.PhotoAgentConfig || {}).defaultAdvancedOptions || {}) };
  }

  // One case's own 人像設定, in the order they become known: what its job was
  // submitted with, else what its analysis froze at analyze time
  // (_advanced_options, the same key a revise's apply_selections falls
  // back to), else the product defaults. Never "whatever the toggles happen to
  // hold" -- for a record restored from history that is the previously-viewed
  // case's, which is exactly what showing a case must not inherit. An empty
  // object counts as "not recorded": a job predating advanced_options sends
  // {}, and displaying that literally would show every switch off.
  function recordAdvancedOptions(advancedOptions, analysis) {
    if (advancedOptions && Object.keys(advancedOptions).length) return advancedOptions;
    const frozen = analysis?._advanced_options;
    if (frozen && Object.keys(frozen).length) return frozen;
    return defaultAdvancedOptions();
  }

  // Puts both copies of every per-request setting back to the product default
  // on a real fresh start, the same reason resetApp already re-does it for
  // 生成張數 via applyRunCountChange(1). The upload page's own controls are the
  // ones that matter here -- startSubmit/startSubmitBatch read straight off
  // them -- but the results page's copies are reset too so the next case
  // opened doesn't flash the last one's settings before its own are applied.
  //
  // This is also the ONLY writer of the upload-page controls besides the user's
  // own picks there: showing a case (applyModelQualityOption et al., scope
  // "ap") deliberately never touches them.
  function resetSettingsToDefaults() {
    const config = window.PhotoAgentConfig || {};
    applyModelQualityOption(findModelQualityOption(config.defaultImageModelQuality), { includeUpload: true });
    applyAnalysisModelOption(findAnalysisModelOption(config.defaultAnalysisModel), { includeUpload: true });
    applyAspectRatioOption(findAspectRatioOption(config.defaultAspectRatio), { includeUpload: true });
    applyOutputScaleOption(findOutputScaleOption(config.defaultOutputScale), { includeUpload: true });
    applyAdvancedOptionsOption(defaultAdvancedOptions(), "upload");
    applyAdvancedOptionsOption(defaultAdvancedOptions(), "ap");
    applySinglePassEditOption(DEFAULT_SINGLE_PASS_EDIT);
  }

  // "Set all" concept: flipping one of these OFF is the same as the user
  // opening the per-person review and disabling that category for every
  // person currently shown there. Whether a row is actually LOCKED is
  // results.js's own concern -- faceIssueGloballyEnabled/reviewAdvancedOptions,
  // kept in sync by applyAdvancedOptionsChange below (which reads the fresh
  // DOM state and calls syncFaceReviewAdvancedOptions) -- this handler only
  // owns the DATA side via setFaceIssueCategoryLock: OFF forces everyone's
  // issue.allowed to false immediately (so getFaceIssueOverrides is correct
  // even before the next render); ON restores every person's allow-toggle to
  // the analyzer's own verdict (should_fix/adjust_safe), discarding the
  // bulk-off and any earlier manual per-person pick -- the only way back is
  // flipping this same toggle back on. renderFaceReviewCards redraws the
  // (already mutated) faceThumbEntries in place -- it does not re-derive
  // them from `analysis`, so it never touches any other category's unsaved
  // per-person picks the way a full renderAnalysisPreview/renderFaceThumbnails
  // would.
  //
  // The results page's copy only. The upload page's four toggles drive nothing
  // live -- there is no analysis on screen and no case to push onto yet, so
  // they are simply read at submit time by readAdvancedOptionsFromDom("upload").
  const ADVANCED_OPTION_FACE_CATEGORIES = [
    { control: els.apEyeClosedDetectionToggle, category: "blink_issue" },
    { control: els.apEyeGazeFocusToggle, category: "gaze_issue" },
    { control: els.apHairMessDetectionToggle, category: "hair_issue" },
    { control: els.apSmileAdjustmentToggle, category: "smile_issue" },
  ];
  ADVANCED_OPTION_FACE_CATEGORIES.forEach(({ control, category }) => {
    if (!control) return;
    control.addEventListener("change", () => {
      setFaceIssueCategoryLock(category, control.checked);
      renderFaceReviewCards();
      applyAdvancedOptionsChange();
    });
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
  // Keyed by imgEl -- if a second setImgSrcPreloaded call for the same
  // shared element (els.generatedImg/originalImg are singletons reused
  // across every case, not per-record) starts before an earlier one's
  // onload/onerror has fired, that earlier call's own resolve is about to
  // be clobbered below and would then never run, permanently orphaning its
  // mask (a switched-away-from case's photo, stuck floating over
  // .photo-frame-wrap forever). Handing the still-pending mask off to
  // whichever call is actually going to finish next, instead of letting
  // each call only ever clean up its own, guarantees it gets removed at
  // the very next load/error on this element regardless of how many
  // switches raced in the meantime.
  const pendingPreloadMasks = new WeakMap();
  // Settles an element's in-flight preload once a newer call on the same
  // element takes over. Both calls assign imgEl.onload, so without this the
  // older call's promise never resolved: e.g. a history record still loading
  // its (multi-MB) original when the user opened a 待美化 record hung
  // viewHistoryRecord forever, never handing #results-canvas-row back
  // (blank results page) and leaving its tile stuck "opening".
  const pendingPreloadSettlers = new WeakMap();

  async function setImgSrcPreloaded(imgEl, src) {
    if (imgEl.src === src && imgEl.complete) return;
    // .photo-frame shrink-wraps to whatever imgEl is currently showing (see
    // #run-switch-loading's own doc, index.html) -- reassigning imgEl.src
    // below collapses it to a sliver the instant the fetch starts, well
    // before the replacement is ready, which is exactly why imgEl itself is
    // hidden for the whole wait. That leaves nothing but .compare-card's own
    // background showing through for however long the fetch takes, though --
    // a clone of whatever imgEl was just displaying, absolutely positioned
    // over the stable .photo-frame-wrap (not .photo-frame, see .compare-img's
    // own rule for it in styles.css), keeps that old photo on screen for the
    // whole gap instead. It's the same already-decoded image already on
    // screen, so this costs no extra fetch even though the real result/
    // history endpoint isn't cacheable (see below).
    let mask = pendingPreloadMasks.get(imgEl) || null;
    const wrap = imgEl.closest(".photo-frame-wrap");
    if (!mask && wrap && imgEl.complete && imgEl.currentSrc) {
      mask = imgEl.cloneNode(true);
      mask.removeAttribute("id");
      mask.setAttribute("aria-hidden", "true");
      wrap.appendChild(mask);
    }
    if (mask) pendingPreloadMasks.set(imgEl, mask);
    imgEl.style.visibility = "hidden";
    pendingPreloadSettlers.get(imgEl)?.(false);
    let settle;
    const loaded = await new Promise((resolve) => {
      settle = resolve;
      pendingPreloadSettlers.set(imgEl, resolve);
      imgEl.onload = () => { imgEl.onload = null; imgEl.onerror = null; resolve(true); };
      imgEl.onerror = () => { imgEl.onload = null; imgEl.onerror = null; resolve(true); };
      imgEl.src = src;
    });
    if (pendingPreloadSettlers.get(imgEl) === settle) pendingPreloadSettlers.delete(imgEl);
    // Superseded: the newer call now owns imgEl's visibility and the mask --
    // revealing here would show its half-loaded image.
    if (!loaded) return;
    imgEl.style.visibility = "";
    if (mask && pendingPreloadMasks.get(imgEl) === mask) pendingPreloadMasks.delete(imgEl);
    if (mask) mask.remove();
  }

  // Fetches a URL into the browser's image cache before it's ever assigned to
  // a visible <img> -- unlike setImgSrcPreloaded above, this isn't tied to any
  // particular DOM element; it's for delaying a page transition itself until
  // the loading placeholder's own thumbnail is ready, so the two appear
  // together instead of the placeholder popping in first and the thumbnail
  // filling in a moment later. Resolves on error too (never rejects) so a
  // broken/slow thumbnail URL can't hang the navigation it's blocking.
  function preloadImage(src) {
    if (!src) return Promise.resolve();
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve();
      img.onerror = () => resolve();
      img.src = src;
    });
  }

  function appendTimingRow(container, label, duration, { sub = false } = {}) {
    const row = document.createElement("div");
    row.className = sub ? "timing-row timing-sub" : "timing-row";
    const name = document.createElement("span");
    name.textContent = sub ? `↳ ${label}` : label;
    const val = document.createElement("span");
    val.className = "timing-value";
    val.textContent = typeof duration === "number" ? `${(duration / 1000).toFixed(1)}s` : tr("未執行");
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
    totalName.textContent = tr("合計");
    const totalVal = document.createElement("span");
    totalVal.className = "timing-value";
    totalVal.textContent = `${(total / 1000).toFixed(1)}s`;
    totalRow.appendChild(totalName);
    totalRow.appendChild(totalVal);
    timingList.appendChild(totalRow);
    timingDetails.hidden = false;
  }

  // Disables the analysis-preview panel's controls (recipe checklist, style
  // option checkboxes, quality/model/RUN-count selects, prompt textarea)
  // while finishSingleFlow's edit/QA job is in flight -- none of it applies
  // to the already-submitted job, so leaving it live just invites confusing
  // edits. Re-enabled once the panel is shown again for a fresh round.
  // Exempts #ap-review-panel's own tab buttons (.ap-review-tab, the
  // per-person avatar tabs .ap-face-tab) -- those only switch which content
  // is visible, they don't change anything about the submitted job, so
  // disabling them alongside the real data controls would leave the user
  // unable to even browse 人像檢查/修圖建議/修圖風格/補充指令 or open a
  // person's own card while a job is in flight or already finished.
  // #inline-analysis-continue-btn's OWN "nothing changed yet" state
  // (updateInlineAnalysisContinueBtnState) is a separate, purely visual
  // .is-blocked class rather than the native disabled attribute -- see that
  // function's own comment for why -- so this blanket disabled=true/false
  // toggle applies to it normally, same as every other control here.
  // #ap-options-card-inline (the actual model/quality/style pickers + Run)
  // does NOT stay inside #analysis-preview -- openInlineAnalysisPanel moves
  // it out to sit beside the photo (#results-canvas-top) whenever the
  // already-edited flow's panel is open, and only moves it back on close.
  // A lock scoped to #analysis-preview alone would stop covering it the
  // moment that move happens, so every lock/unlock/click-guard below treats
  // these two as one combined surface regardless of where the second one
  // currently lives in the DOM.
  function analysisPreviewLockTargets() {
    return [els.analysisPreview, els.apOptionsCardInline].filter(Boolean);
  }

  // Whether the panel is currently locked read-only -- checked by
  // wireOptionPicker's rebuild() below, not just set once here. The user
  // must still be able to open/read everything (recipe, diagnosis, a
  // picker's own option list) -- this only gates whether picking a NEW
  // option actually takes, never visibility.
  function isAnalysisPreviewLocked() {
    return analysisPreviewLockTargets().some((target) => target.classList.contains("ap-readonly-locked"));
  }

  function setAnalysisPreviewDisabled(disabled) {
    if (els.analysisPreview) {
      els.analysisPreview.querySelectorAll("input, select, textarea, button").forEach((el) => {
        if (el.classList.contains("ap-review-tab") || el.classList.contains("ap-face-tab")) return;
        // This is a display-only overview expander. Archived cases remain
        // read-only for editing controls, but the user must still be able to
        // expand/collapse the photo summary.
        if (el.id === "ap-photo-summary-toggle") {
          el.disabled = false;
          return;
        }
        // #ap-restart-analysis-btn/#ap-restart-full-btn (index.html) live
        // inside #ap-options-card-inline (one of analysisPreviewLockTargets
        // below) but ARE this archived case's one legitimate action -- a
        // blanket disabled=true here would make showArchivedAnalysisNotice's
        // own canRestart toggle pointless.
        if (el.closest("#ap-restart-actions")) {
          el.disabled = false;
          return;
        }
        el.disabled = disabled;
      });
    }
    // ap-readonly-locked (CSS opacity cue, styles.css) also gates
    // wireOptionPicker's rebuild(): a .ap-style-item picker's option list
    // (pose/angle/lighting/size, model quality, aspect ratio, analysis
    // model) is torn down and rebuilt from scratch every time it's opened,
    // which would otherwise silently undo the .disabled sweep above the
    // next time the user opens one -- see isAnalysisPreviewLocked. Content
    // stays fully visible/openable either way; only picking a new option is
    // what this actually blocks.
    analysisPreviewLockTargets().forEach((target) => {
      target.classList.toggle("ap-readonly-locked", disabled);
    });
    syncReframingRatioLock();
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
  // #ap-step-indicator used to be dragged along into the same container here
  // (prepended, so it landed above .compare-card) -- it isn't any more. It
  // lives in #step-indicator-slot above every screen and never moves; see
  // that slot's markup comment in index.html. This function moves the
  // options card and nothing else.
  function moveOptionsCardTo(container) {
    if (!container) return;
    if (els.apOptionsCardInline && els.apOptionsCardInline.parentElement !== container) {
      container.appendChild(els.apOptionsCardInline);
    }
  }

  // #ap-analysis-shell/#ap-review-panel are both <details> elements, and a
  // <details>'s own non-summary child ignores percentage heights too, not
  // just flex-grow/grid 1fr (the part of this Chromium limitation the
  // rest of this codebase already knew about) -- it turns out
  // height:calc(100% - X) on such a child resolves "100%" against the
  // <details>'s flex-basis (494px, #ap-analysis-shell's own rule further
  // down), not its actual post-flex-shrink rendered height (368px on a
  // shortish viewport) -- confirmed directly via computed styles: shell
  // height 368px, .ap-analysis-shell-body computed to 395px, which only
  // matches 494(basis) - 71(summary) - 28(this element's own padding), not
  // 368 - 71 - 28. So on any viewport short enough to actually shrink the
  // shell, every "overshoot-then-clip" percentage trick down this whole
  // chain was silently computing against the WRONG (pre-shrink) height,
  // no matter how it was written -- calc(), plain height:100%, a CSS
  // custom property, none of it changes which base value Chromium uses.
  // The only way to actually get this right is to sidestep percentage/
  // calc() entirely and set real pixel heights from JS, using each
  // element's own true getBoundingClientRect() -- .ap-analysis-shell-body
  // gets (shell's real height - its own <summary>'s real height),
  // #ap-review-panel then gets (that body's real content-box height -
  // its own <summary>'s real height), and .ap-toggle-card-body is left to
  // its own height:100% (styles.css) since it's a plain div, not a
  // <details>, so ordinary percentage-height rules actually apply to it.
  // All three levels (.ap-analysis-shell-body, #ap-review-panel,
  // .ap-toggle-card-body) get a real JS-computed pixel height, not a CSS
  // percentage/calc() at any level -- .ap-toggle-card-body is a plain div
  // (not a <details>), so ordinary percentage math would actually work
  // fine for IT specifically, but computing it here too keeps the whole
  // chain in one place instead of splitting it across JS and CSS in a way
  // that's easy to lose track of again.
  function syncAnalysisPanelHeights() {
    const shell = els.apAnalysisShell;
    const panel = els.apReviewPanel;
    if (!shell || !panel) return;
    const shellSummary = shell.querySelector(":scope > summary");
    const shellBody = shell.querySelector(":scope > .ap-analysis-shell-body");
    const panelSummary = panel.querySelector(":scope > summary");
    const toggleBody = panel.querySelector(":scope > .ap-toggle-card-body");
    if (!shellSummary || !shellBody || !panelSummary || !toggleBody) return;
    if (!shell.open) {
      shellBody.style.height = "";
      panel.style.height = "";
      toggleBody.style.height = "";
      return;
    }
    const shellHeight = shell.getBoundingClientRect().height;
    const shellSummaryHeight = shellSummary.getBoundingClientRect().height;
    const bodyHeight = Math.max(0, shellHeight - shellSummaryHeight);
    shellBody.style.height = `${bodyHeight}px`;

    const shellBodyStyle = getComputedStyle(shellBody);
    const shellBodyPadding =
      (parseFloat(shellBodyStyle.paddingTop) || 0) + (parseFloat(shellBodyStyle.paddingBottom) || 0);
    // Whatever sits above the panel inside the body (#ap-continue-base, when
    // it's showing) takes its share first -- measured from the real layout so
    // its margin and any wrapping come along.
    const aboveHeight = Math.max(
      0,
      panel.getBoundingClientRect().top - shellBody.getBoundingClientRect().top
        - (parseFloat(shellBodyStyle.paddingTop) || 0)
    );
    const panelHeight = Math.max(0, bodyHeight - shellBodyPadding - aboveHeight);
    panel.style.height = `${panelHeight}px`;

    const panelSummaryHeight = panelSummary.getBoundingClientRect().height;
    toggleBody.style.height = `${Math.max(0, panelHeight - panelSummaryHeight)}px`;

    // #ap-recipe-list/#ap-face-detail/.ap-style-option-grid each size
    // themselves off THIS element's real height (max-height:100%,
    // index.html) -- their own has-more-below/-above cue (updateListScrollCue/
    // updateStyleOptionScrollCue, further down) only ever recomputes on a
    // content mutation or a scroll, neither of which fires just because
    // toggleBody's real height changed (e.g. the panel going from hidden to
    // visible for the first time, well after its content was already
    // injected). Without this, a list's cue could get computed once against
    // a stale (often 0, pre-first-layout) toggleBody height and never again,
    // permanently stuck showing "more below" even once the real height
    // (and thus real available space) made that untrue. Harmless to call on
    // whichever tab/category isn't currently visible -- a hidden list's own
    // scrollHeight/clientHeight are both 0, so canScroll is false and
    // neither cue class gets set. updateListScrollCue/updateStyleOptionScrollCue
    // are plain function declarations (hoisted), safe to call here even
    // though this function itself runs once before their own source line is
    // reached -- unlike STYLE_GROUPS itself (a const, still in its temporal
    // dead zone at that point), which is why this reaches every style grid
    // via a plain class query instead of iterating that array.
    [els.apRecipeList, els.apFaceDetail].forEach(updateListScrollCue);
    document.querySelectorAll(".ap-style-option-grid").forEach(updateStyleOptionScrollCue);
  }
  // ResizeObserver on both <summary> elements (not a handful of manual
  // calls from whichever moment happened to seem right -- an earlier
  // version of this did that, and missed real-world cases) -- catches
  // every reason either one's real height can change: #ap-analysis-shell's
  // own collapsed <-> open swap (a completely different summary layout/
  // height), a live re-render, font loading, anything -- without needing
  // to know every possible cause in advance.
  // Deliberately NOT also observing #ap-analysis-shell itself -- an
  // earlier version of this did, on the theory that its own flex-shrink
  // on a short viewport needed watching too, but this function sets an
  // inline height on shell's own CHILD (.ap-analysis-shell-body), and on
  // narrow/mobile viewports (more frequent reflow from text wrapping,
  // scrollbar-width changes, etc.) that occasionally nudged shell's own
  // rendered height by a sub-pixel amount, which re-fired this same
  // observer, which set the child's height again, which nudged shell
  // again -- a slow-motion ResizeObserver feedback loop that read as the
  // panel's height "continuously growing". A plain resize listener still
  // catches the actual reason shell's size needs re-checking (the
  // viewport changing) without ever being triggered by this function's
  // own writes.
  (() => {
    const shell = els.apAnalysisShell;
    const panel = els.apReviewPanel;
    if (!shell || !panel) return;
    const shellSummary = shell.querySelector(":scope > summary");
    const panelSummary = panel.querySelector(":scope > summary");
    if (shellSummary) new ResizeObserver(syncAnalysisPanelHeights).observe(shellSummary);
    if (panelSummary) new ResizeObserver(syncAnalysisPanelHeights).observe(panelSummary);
    // Shell's siblings (#ap-skip-hint/#ap-photo-summary above it, the Run
    // row below) share its flex column under side-panel-layout, so one of
    // them showing up or re-wrapping its text shrinks shell without any
    // summary or the viewport changing -- the inner heights stayed stale and
    // the panel's bottom border got clipped. None of them size off shell,
    // so watching them can't loop back the way watching shell itself did.
    const siblingObserver = new ResizeObserver(syncAnalysisPanelHeights);
    for (const sibling of shell.parentElement?.children || []) {
      if (sibling !== shell) siblingObserver.observe(sibling);
    }
    // #ap-continue-base, the row above the panel inside the body: showing,
    // hiding or re-wrapping it changes what's left for the panel. It doesn't
    // size off the panel, so this can't loop either.
    if (els.apContinueBase) siblingObserver.observe(els.apContinueBase);
    window.addEventListener("resize", syncAnalysisPanelHeights);
    syncAnalysisPanelHeights();
  })();

  // ── 分析面板顯示於預覽右側 (side-panel-layout-toggle, #ap-settings-modal) ───────
  // A display preference, not a per-request option -- persisted client-side
  // rather than sent to the backend. When on, moveOptionsCardTo's two call
  // sites (showPendingResults/openInlineAnalysisPanel below) target
  // #results-analysis-slot instead of #results-canvas-top, so
  // #ap-options-card-inline (the #ap-analysis-shell/#ap-review-panel tabs)
  // lands beside the photo instead of stacked below it -- the step
  // indicator isn't involved either way any more, it sits in
  // #step-indicator-slot above this whole section (index.html). The
  // actual repositioning is CSS (styles.css's "side-panel layout" section,
  // gated to width > 900px), this just decides which container to move
  // into. Below 900px (and with the toggle off) nothing here changes
  // anything: #results-canvas-top stays the target, same as today.
  const SIDE_PANEL_LAYOUT_KEY = "photoagent.sidePanelLayout";

  // Defaults on (a user who's never touched this setting gets the side
  // panel) -- distinguishes "never set" (null) from an explicit "0" (user
  // turned it off), so that opt-out still sticks across reloads instead of
  // this default silently overriding it every time.
  function isSidePanelLayoutEnabled() {
    try {
      const stored = localStorage.getItem(SIDE_PANEL_LAYOUT_KEY);
      return stored === null ? true : stored === "1";
    } catch {
      return true;
    }
  }

  function setSidePanelLayoutEnabled(enabled) {
    try {
      localStorage.setItem(SIDE_PANEL_LAYOUT_KEY, enabled ? "1" : "0");
    } catch {
      // Private-browsing/storage-disabled: falls back to session-only via
      // the body class below, just doesn't survive a reload.
    }
    document.body.classList.toggle("side-panel-layout", enabled);
  }

  // ── 分析／美化前顯示預計花費 (preflight-cost-toggle, both settings modals) ──
  // Gates every #preflight-cost-modal open (showPreflightCostModal) and the
  // folded-in cost rows in the "沒有做任何修改" confirm (showConfirmModal's
  // costSteps option) -- a display/friction preference, not a per-request
  // option, so it's persisted client-side like SIDE_PANEL_LAYOUT_KEY above.
  const PREFLIGHT_COST_ESTIMATE_KEY = "photoagent.preflightCostEstimate";

  // Same null-vs-"1" shape as isSidePanelLayoutEnabled, but default OFF --
  // a user who's never touched this setting gets straight-through submits,
  // same as before this feature existed; only an explicit "1" turns it on.
  function isPreflightCostEstimateEnabled() {
    try {
      return localStorage.getItem(PREFLIGHT_COST_ESTIMATE_KEY) === "1";
    } catch {
      return false;
    }
  }

  // Updates every matching toggle (shared class, not id -- this setting is
  // duplicated into both #settings-modal and #ap-settings-modal, see their
  // own markup comments) so flipping it in one modal is immediately
  // reflected if the user later opens the other one in the same session.
  function setPreflightCostEstimateEnabled(enabled) {
    try {
      localStorage.setItem(PREFLIGHT_COST_ESTIMATE_KEY, enabled ? "1" : "0");
    } catch {
      // Private-browsing/storage-disabled: falls back to session-only.
    }
    document.querySelectorAll(".preflight-cost-toggle").forEach((toggle) => {
      toggle.checked = enabled;
    });
  }

  function getOptionsCardTarget() {
    return isSidePanelLayoutEnabled() && els.resultsAnalysisSlot ? els.resultsAnalysisSlot : els.resultsCanvasTop;
  }

  // ── 最近照片／範例照片排列方式 (gallery-layout-mode-btn, #settings-modal) ──
  // Three-way layout choice for the upload page's 最近照片／範例照片 gallery:
  // 格狀 (the dense mosaic grid), 交錯 (a scattered, overlapping card stack),
  // or 預設, which picks between those two automatically off the same
  // <=1200px breakpoint the two CSS skins are already scoped to. Same
  // persisted-client-side display-preference pattern as
  // isSidePanelLayoutEnabled/setSidePanelLayoutEnabled just above: not sent
  // to the backend, just a body class (styles.css's "gallery-card-style"
  // section) that CSS-overrides the mosaic by selector specificity alone
  // (one extra class beats the mosaic's own selectors), not by removing
  // anything -- both skins stay intact in the stylesheet, and switching (or,
  // in 預設, the viewport crossing 1200px) is just this class toggling on
  // <body>. The desktop mosaic (>=1201px) switches to a vertical card stack,
  // and the narrow horizontal strip (<=1200px) to its own horizontal card
  // version (separate @media (max-width: 1200px) gallery-card-style block in
  // styles.css) -- which is exactly why 預設 can defer to width: each width
  // already has both skins defined.
  const GALLERY_CARD_STYLE_KEY = "photoagent.galleryCardStyle";
  const GALLERY_LAYOUT_MODES = ["auto", "grid", "collage"];
  // One shared MediaQueryList rather than calling matchMedia() per read.
  // Wrapped in try/catch and null-guarded everywhere below because this one
  // runs at module scope, not inside a setup function: an exception in one of
  // those would cost that one feature, an exception
  // here would abort the rest of this file's top-level evaluation and take
  // the whole app down with it. Null just means 預設 resolves as 格狀 and
  // stops following width -- both explicit modes, and everything else on
  // the page, still work.
  let galleryLayoutNarrowMql = null;
  try {
    galleryLayoutNarrowMql = window.matchMedia("(max-width: 1200px)");
  } catch {
    /* no matchMedia: 預設 falls back to 格狀, see applyGalleryLayoutMode */
  }

  // Reads the persisted mode, migrating this setting's older on/off form:
  // "1" (卡片散落排列 was on) becomes a fixed 交錯 choice, "0" (explicitly
  // off) a fixed 格狀 one, and never-set (null) the new "auto" default -- so
  // someone who never touched this keeps today's look at >=1201px (格狀,
  // unchanged) and now gets 交錯 at <=1200px, which is the point of 預設.
  function getGalleryLayoutMode() {
    let raw = null;
    try { raw = localStorage.getItem(GALLERY_CARD_STYLE_KEY); } catch { /* storage may be blocked */ }
    if (raw === "1") return "collage";
    if (raw === "0") return "grid";
    return GALLERY_LAYOUT_MODES.includes(raw) ? raw : "auto";
  }

  // Flips the body class the CSS actually keys off. "auto" reads the current
  // viewport against the same <=1200px breakpoint the narrow-width skin is
  // itself scoped to, so mode switches and resizes both land on whichever
  // skin the CSS would pick anyway.
  function applyGalleryLayoutMode(mode) {
    const collage = mode === "collage" || (mode === "auto" && !!galleryLayoutNarrowMql?.matches);
    document.body.classList.toggle("gallery-card-style", collage);
  }

  function syncGalleryLayoutModeButtons(mode) {
    document.querySelectorAll(".gallery-layout-mode-btn").forEach((btn) => {
      const active = btn.dataset.galleryLayoutMode === mode;
      btn.classList.toggle("is-selected", active);
      btn.setAttribute("aria-pressed", String(active));
    });
  }

  function setGalleryLayoutMode(mode) {
    const resolved = GALLERY_LAYOUT_MODES.includes(mode) ? mode : "auto";
    try {
      localStorage.setItem(GALLERY_CARD_STYLE_KEY, resolved);
    } catch {
      // Private-browsing/storage-disabled: falls back to session-only via
      // the body class below, just doesn't survive a reload.
    }
    applyGalleryLayoutMode(resolved);
    syncGalleryLayoutModeButtons(resolved);
  }

  // applyDebugUiVisibility (above in this file) otherwise hides
  // #results-analysis-slot with an inline style permanently, on the
  // assumption that it only ever holds the now-debug-only #analysis-preview
  // leftovers -- undo that (and the compensating centered/900px-capped
  // canvas column) while this setting is on, since getOptionsCardTarget
  // then relocates #ap-options-card-inline into that same slot for real.
  // Called both from applyDebugUiVisibility itself (so it's right from
  // first paint) and from the toggle's own change handler (so flipping it
  // mid-session doesn't leave yesterday's inline styles stuck until reload).
  //
  // #analysis-preview itself is also gated here, not just its slot: with
  // this layout on, moveAnalysisPreviewTo/moveOptionsCardTo (both above)
  // leave #analysis-preview appended into #results-analysis-slot BEFORE
  // #ap-options-card-inline, so a debug session (where applyDebugUiVisibility
  // lifts display:none off #analysis-preview for its own leftover content --
  // the 取消 button, subject tags) would otherwise render that leftover strip
  // above 分析與調整 instead of beside the photo. In the stacked/vertical
  // layout (this function's else branch) #results-analysis-slot itself is
  // display:none, which already hides #analysis-preview regardless of its
  // own display value -- keeping it hidden here too when the side panel is on
  // just keeps that same "not actually shown" position instead of exposing it
  // above the real panel now that the slot is visible.
  function syncResultsAnalysisSlotForSidePanelLayout() {
    const sidePanelOn = isSidePanelLayoutEnabled();
    if (els.analysisPreview) els.analysisPreview.style.display = isDebugUiActive && !sidePanelOn ? "" : "none";
    if (sidePanelOn) {
      if (els.resultsAnalysisSlot) els.resultsAnalysisSlot.style.display = "";
      if (els.resultsCanvasRow) els.resultsCanvasRow.style.justifyContent = "";
      if (els.resultsCanvasCol) els.resultsCanvasCol.style.maxWidth = "";
    } else {
      if (els.resultsAnalysisSlot) els.resultsAnalysisSlot.style.display = "none";
      if (els.resultsCanvasRow) els.resultsCanvasRow.style.justifyContent = "center";
      if (els.resultsCanvasCol) els.resultsCanvasCol.style.maxWidth = "900px";
    }
  }

  function restoreOptionsCardHome() {
    if (els.apOptionsCardHome && els.apOptionsCardInline.previousElementSibling !== els.apOptionsCardHome) {
      els.apOptionsCardHome.after(els.apOptionsCardInline);
    }
  }

  // ── Flow stage: the single source of truth for the step indicator ────────
  // Every screen that shows progress derives what it shows from this one
  // value, instead of each transition point remembering to push a step
  // number of its own. That distinction is the whole point: the step number
  // is a rendering detail, the stage is a fact about what the app is doing,
  // and the second one is something every entry point already knows about
  // itself. An entry point that declares its stage cannot "forget" to update
  // the indicator, and can't advance it ahead of the work either -- both of
  // which is exactly what used to go wrong (一鍵修圖 sat on 分析 for the whole
  // edit because no call site on that path ever pushed 修圖; pushing it at
  // the top of the branch instead skipped 分析 entirely, because that branch
  // starts before the analysis does).
  //
  // Stages, in flow order:
  //   idle             nothing running -- upload screen (the slot is hidden
  //                    there anyway, dom.js; this just leaves a sane state
  //                    behind for the next run)
  //   analyzing        分析 is running now
  //   awaiting-review  分析 finished, waiting for the user to hit Run
  //   editing          修圖 is running now
  //   done             結果 is on screen
  //
  // Resuming a record from 最近照片 needs no indicator bookkeeping of its own:
  // each of those paths already routes through the same view function as the
  // live flow (showBatchMainLoading / showBatchAnalyzedMain / showSingleResult
  // / showBatchDoneMain), and the record's status is what picked the view.
  const FLOW_STAGE_STEPS = {
    idle: { step: 2, done: false },
    analyzing: { step: 2, done: false },
    "awaiting-review": { step: 2, done: true },
    editing: { step: 3, done: false },
    done: { step: 4, done: true }
  };

  let flowStage = "idle";

  function setFlowStage(stage) {
    if (!FLOW_STAGE_STEPS[stage]) return;
    flowStage = stage;
    renderStepIndicator();
    // The ways to 對話模式 (#chat-ui-btn in the header, and the 對話 half of
    // the step row's 對話｜面板 switch) are held while 分析/修圖 is running:
    // the chat page would open the case on a half-done job. They're links, so
    // aria-disabled (styled in index.html / styles.css) plus the click
    // handler's guard.
    const busy = stage === "analyzing" || stage === "editing";
    for (const id of ["chat-ui-btn", "chat-mode-switch-link"]) {
      const link = document.getElementById(id);
      if (!link) continue;
      link.toggleAttribute("aria-disabled", busy);
      link.title = busy ? tr("分析或美化完成後才能切換") : tr("切換到對話模式");
    }
    // #page-scroll-hint gates on this stage (PAGE_SCROLL_HINT_STAGES), and
    // entering/leaving 修圖中 happens in place on the results page -- no
    // section change, and swapping the photo for the loading card need not
    // change the document height, so neither of that cue's own triggers is
    // guaranteed to fire. Tell it directly.
    updatePageScrollHint();
  }

  function renderStepIndicator() {
    const { step, done } = FLOW_STAGE_STEPS[flowStage];
    setStepIndicatorStep(step, { done });
  }

  // The DOM writer behind renderStepIndicator -- call setFlowStage, not this.
  //
  // step: 1 (上傳), 2 (分析), 3 (修圖), or 4 (結果). Only the current step gets
  // .ap-step--active (highlighted); every step before it gets .ap-step--done
  // (muted, with a checkmark) so earlier steps read as "done" rather than
  // reverting to a plain ring once the flow moves past them. Connector
  // lines up to the current step stay .ap-step-line--active.
  //
  // `done` covers the current step itself: true means the work behind this
  // step has already finished by the time we're switching to it -- e.g.
  // showing the pending-review panel only happens once analysis returned,
  // and reaching 結果 only happens once editing is done -- so it gets a
  // checkmark right away, same as an earlier step. false means the step's
  // own work is what's running right now (分析/修圖 in flight): the current
  // step shows a spinner instead, until the stage moves on.
  function setStepIndicatorStep(step, { done = true } = {}) {
    if (!els.apStepIndicator) return;
    els.apStepIndicator.querySelectorAll(".ap-step").forEach((el, i) => {
      el.classList.toggle("ap-step--done", i < step - 1);
      el.classList.toggle("ap-step--active", i === step - 1);
      el.classList.toggle("ap-step--checked", i === step - 1 && done);
    });
    els.apStepIndicator.querySelectorAll(".ap-step-line").forEach((el, i) => {
      el.classList.toggle("ap-step-line--active", i < step - 1);
    });
  }

  // ── Pause-for-review between analyze and finish, shown on results-section ──
  // Same "analyze done, wait for Run before editing" pause the two-stage flow
  // has always had, but now rendered on #results-section itself (via
  // renderResult's pending mode) instead of a separate-looking panel on the
  // processing screen -- so it looks exactly like the finished-result page,
  // just with no generated image/QA yet. Reuses moveOptionsCardTo, the same
  // relocation backToProcessFromResults' revise flow already uses to park
  // #ap-options-card-inline (recipe/style/prompt + Run button) below
  // .compare-card.
  async function showPendingResults(analysis, preset) {
    // This flow never relocates #ap-options-card-inline itself (unlike
    // openInlineAnalysisPanel's already-edited case, which moves it out to
    // sit beside the photo) -- it's assumed to already be nested inside
    // #analysis-preview throughout. That assumption breaks if the user
    // previously viewed an already-edited case in this same session and its
    // read-only branch is what's active now (setAnalysisPreviewDisabled
    // below silently finds nothing to disable once the card has been moved
    // out elsewhere) -- restore it home first so the assumption holds
    // regardless of what the screen showed right before this.
    restoreOptionsCardHome();
    // Drop a revise-panel wait a results page may have left on the shared
    // 美化 button (e.g. a 待美化 record opened straight from the history
    // drawer, which doesn't go through resetApp).
    clearInlineAnalysisWait();
    renderAnalysisPreview({ analysis, preset });
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = !isDebugUiActive;
    // Step 2 already ran by the time this review screen shows -- populate
    // #timing-details/#timing-list with just that much now (finishSingleFlow
    // repopulates it with the full Steps 2-6 timeline once the rest runs),
    // same reasoning as tokenUsageAsText's fallback to analysis.usage below.
    populateTimingSection(singleFlowState.analyzeTimings);
    // Before renderResult -- drawAnalysisBoxes (called via setCompareView)
    // reads #original-img's laid-out size, which is all zeros while
    // #results-section is still display:none.
    showSection("results");
    // The history drawer is a full-viewport overlay independent of which
    // section is showing underneath -- if the user opened it (without
    // clicking into any record, which would already have its own explicit
    // close) while this analysis was still running in the background, this
    // reveal would otherwise happen invisibly behind it. closeHistoryDrawer
    // is a no-op when it's already closed.
    closeHistoryDrawer();
    // Same "already showing" guard showSingleResult/showBatchDoneMain/
    // showBatchAnalyzedMain use, and the same reason: #batch-main-loading
    // (shown by viewProcessingHistoryRecord/viewAnalyzeHistoryRecord while a
    // DIFFERENT history record is still processing/analyzing) must never
    // stay stuck covering the screen once this record's own review needs to
    // render here instead -- clicking an already-analyzed ("待修圖") history
    // tile skips straight to this function without ever calling
    // showBatchMainLoading/hideAllBatchMainViews itself, so without this the
    // old placeholder was left on top of the newly-built (but still hidden)
    // #results-canvas-row.
    const alreadyShowing = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
    if (!alreadyShowing) hideAllBatchMainViews();
    if (els.resultsCanvasRow) els.resultsCanvasRow.hidden = false;
    renderResult(
      { data: {}, analysis, preset, imageModel: singleFlowState.imageModel, hasOriginal: true },
      { pending: true }
    );
    moveOptionsCardTo(getOptionsCardTarget());
    // Analysis is in and the user is being asked to Run -- this is also what
    // resets the indicator for a fresh image after a previous one's flow had
    // advanced it to 修圖/結果.
    setFlowStage("awaiting-review");
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.add("showing-analysis");
    if (els.resultsBody) els.resultsBody.classList.add("showing-analysis");
    if (els.inlineAnalysisContinueBtn) {
      els.inlineAnalysisContinueBtn.hidden = false;
      // No baseline to diff against yet for a fresh image's very first
      // review -- accepting the AI's own suggestions as-is is the normal
      // first action here, not something updateInlineAnalysisContinueBtnState
      // should block. Also clears any stale .is-blocked state a prior
      // image's finished revise round left on this same shared button.
      els.inlineAnalysisContinueBtn.classList.remove("is-blocked");
      inlineReviseSnapshot = null;
    }
    // Follows Run's own visibility everywhere it is toggled (see
    // updateApCaseSettingsTrigger's own doc) -- the settings pill has nothing
    // to configure on a screen with no action on it.
    updateApCaseSettingsTrigger();
    pendingResultsActive = true;
    if (els.downloadLink) {
      // selectedFile is null for a history record reopened at this same
      // pending-review stage (viewAnalyzeHistoryRecord) -- selectedFileSourceUrl
      // is its own source photo in that case, so only hide the button when
      // neither is available.
      els.downloadLink.hidden = !selectedFile && !selectedFileSourceUrl;
      els.downloadLink.title = tr("下載原圖");
      els.downloadLink.setAttribute("aria-label", tr("下載原圖"));
    }
    // Same debug-only gate revealResultsIfNeeded applies once the edit stage
    // finishes -- the analyze-stage prompts (photo_analysis/
    // user_instruction_review) are already in the DOM via
    // renderResult's renderPrompts call above, so there's no reason to make
    // debug users wait for the edit stage just to read those.
    if (els.showPromptBtn) els.showPromptBtn.hidden = !isDebugUiActive;
    // currentTokenUsage()'s lastResult branch is checked BEFORE the
    // singleFlowState.analysis fallback (see its own comment: it prefers the
    // "finished" shape first) -- an assumption that only holds when
    // lastResult and singleFlowState describe the SAME case. Nothing else
    // clears lastResult when a fresh analyze starts (only resetApp/
    // resetBatchState do, and neither runs on every path that can reach
    // here), so without this, a photo that has only ever been analyzed --
    // Step 5 has not run for it at all -- could still show a PREVIOUS,
    // unrelated case's edit-stage cost. null is safe here: nothing else in
    // this function (or the pending-review screen generally) reads
    // lastResult, only the finished-result screens set it again.
    lastResult = null;
    updateCostBtn();
  }

  // Module-level (mirroring clearInlineAnalysisWait) so every exit from the
  // pause screen can drop the listener, not just a real click: the button is
  // shared with the revise panel, and a leaked handler re-runs its dead flow's
  // finishSingleFlow on that panel's own click -- one extra edit job per leak.
  let pendingResultsContinueHandler = null;

  function clearPendingResultsWait() {
    if (!pendingResultsContinueHandler) return;
    els.inlineAnalysisContinueBtn.removeEventListener("click", pendingResultsContinueHandler);
    pendingResultsContinueHandler = null;
  }

  function closePendingResults() {
    clearPendingResultsWait();
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.remove("showing-analysis");
    if (els.resultsBody) els.resultsBody.classList.remove("showing-analysis");
    if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = true;
    updateApCaseSettingsTrigger();
    if (els.downloadLink) els.downloadLink.hidden = true;
    pendingResultsActive = false;
    restoreOptionsCardHome();
  }

  // Same one-shot-listener shape as waitForInlineAnalysisAction.
  function waitForPendingResultsAction() {
    return new Promise((resolve) => {
      clearPendingResultsWait(); // drop any stale listener from an abandoned prior run
      // Whatever takes over the screen next (a history record, a new upload,
      // a batch) bumps historyViewToken -- a click arriving after that belongs
      // to whatever is on screen now, not to this abandoned review.
      const myToken = historyViewToken;
      // 美化's own pre-flight cost estimate -- singleFlowState.imageModel/
      // .imageQuality are kept live-synced with the visible picker
      // (applyModelQualityChange), so they already reflect whatever the user
      // has selected by the time this click fires. A Cancel here just
      // leaves the review panel open for another click, same as before this
      // confirm existed.
      async function continueHandler() {
        if (historyViewToken !== myToken) { clearPendingResultsWait(); return; }
        const est = preflightEditEstimate(singleFlowState.imageModel, singleFlowState.imageQuality);
        const confirmed = await showPreflightCostModal([{ label: tr("美化花費"), modelLabel: est.label, cost: est.cost }]);
        if (!confirmed) return;
        clearPendingResultsWait();
        resolve();
      }
      pendingResultsContinueHandler = continueHandler;
      els.inlineAnalysisContinueBtn.addEventListener("click", continueHandler);
    });
  }

  // Shared pause-for-review tail: show the panel, wait for the user's
  // continue/back click, then either leave the flow or run finishSingleFlow.
  // Used both by startSubmit's own manual path and by resuming an "awaiting
  // review" history record (viewAnalyzeHistoryRecord) -- the two reach this
  // exact same paused state by different routes.
  async function runPendingReviewFlow(analysis, preset) {
    // A "待修圖" history record (viewAnalyzeHistoryRecord) whose analysis no
    // longer meets compatibility_version.py's floor opens read-only here too
    // -- same reasoning/mechanism as backToProcessFromResults' already-edited
    // case: finishSingleFlow would reject it anyway, so there's nothing here
    // for Continue/any picker to legitimately do. A fresh analyze (the other
    // caller, startSubmit's manual path) is always compatible, so this is a
    // no-op for it. Computed and applied BEFORE showPendingResults below --
    // that call rebuilds this case's own recipe/face-review checkboxes
    // (results.js's fillCheckList/buildPersonIssueItems), which read the
    // lock class at creation time, not just once afterward. Setting it only
    // after would leak whatever the PREVIOUS case left the class as into
    // this case's initial build (and leave this case unlocked until the
    // NEXT case's build finally catches up).
    const readOnly = !isCompatible(analysis);
    setAnalysisPreviewDisabled(readOnly);
    // Snapshot of the token as of entry -- both callers bump it right before
    // handing this screen over to us (startSubmit, viewAnalyzeHistoryRecord),
    // so it stays ours until the user opens some other record. The retry
    // loop below checks it before re-rendering anything of this case's.
    const myHistoryToken = historyViewToken;
    // Reset here, before the branch below -- a previous case left showing
    // (readOnly=true, still on screen since 維持現況 doesn't clear it) could
    // otherwise leak #ap-restart-actions into this one's normal edit flow.
    // showArchivedAnalysisNotice (below) re-shows it for this case's own
    // readOnly branch based on ITS OWN canRestart, not this case's.
    if (els.apRestartActions) els.apRestartActions.hidden = true;
    await showPendingResults(analysis, preset);
    if (!readOnly) {
      markReviewBaseline();
      applyPendingModeHandoff();
    }
    if (readOnly) {
      if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = true;
      updateApCaseSettingsTrigger();
      // Shown once the (already read-only-locked) screen has actually
      // finished loading in, not before -- the lock itself doesn't announce
      // itself otherwise, and a dimmed panel alone is easy to miss as "why
      // can't I click anything" rather than "this case is outdated."
      showArchivedAnalysisNotice();
      return; // no Continue button exists to wait for in this mode
    }
    // Loops rather than running once: a 美化 that fails comes straight back
    // to this same review page (see the catch below) and waits for another
    // click, instead of dead-ending on an error card. The analysis is
    // already paid for and still valid, so retrying it is one button press
    // -- and for the two callers this is shared by, the review page IS the
    // screen the user pressed 美化 from, which makes it the only honest place
    // to land them back on.
    for (;;) {
      await waitForPendingResultsAction();
      reviewBaseline = null; // sent with this 美化
      // Always proceeds straight into #canvas-loading below (no "cancel" branch
      // here, unlike closeInlineAnalysisPanel's callers) -- skip
      // .results-canvas-row's panel-close animation (styles.css) since there's
      // no photo on screen for it to slide/grow against; removed again once
      // canvas-loading is actually showing so a later, normal panel toggle
      // still animates.
      if (els.resultsCanvasRow) els.resultsCanvasRow.classList.add("no-transition");
      closePendingResults();
      setFlowStage("editing"); // finishSingleFlow below moves to "done" once it finishes
      // Same "editing" loading overlay backToProcessFromResults' revise flow
      // uses -- swaps the photo for the thinking-rings animation instead of
      // navigating away from the results page.
      setAnalysisPreviewDisabled(true);
      if (els.canvasLoadingThumb) els.canvasLoadingThumb.src = originalImgThumbUrl || els.originalImg.src;
      if (els.photoFrameWrap) els.photoFrameWrap.hidden = true;
      hideVersionStrip(); // see the matching call in backToProcessFromResults
      if (els.canvasLoading) els.canvasLoading.hidden = false;
      if (els.resultsCanvasRow) {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => els.resultsCanvasRow.classList.remove("no-transition"));
        });
      }
      let failure = null;
      try {
        await finishSingleFlow();
      } catch (error) {
        // Only handled here while this review is still what's on screen. If
        // the user has moved on to another record mid-edit, rethrowing hands
        // the failure to whichever caller started this flow -- both of their
        // catches are token-guarded and will correctly drop it (the history
        // tile reports it instead), and re-rendering this page over the
        // record they're now looking at is exactly what must not happen.
        if (historyViewToken !== myHistoryToken) throw error;
        failure = error;
      } finally {
        if (els.canvasLoading) els.canvasLoading.hidden = true;
        if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
        setAnalysisPreviewDisabled(false);
        // Re-derives Run's own .is-blocked state -- finishSingleFlow's own
        // auto-opened revise panel (showSingleResult -> backToProcessFromResults)
        // already set this correctly moments ago, but this covers the rarer
        // case where that never ran (e.g. an error before analysis.data landed).
        updateInlineAnalysisContinueBtnState();
      }
      if (!failure) return;
      // The edit stage died. Its rotation is still running at this point
      // (nothing in finishSingleFlow's failure path drops it) and writes
      // through setProcSub every couple of seconds, so it has to go before
      // anything else is put on screen.
      stopProcSubRotation();
      // Back to the review page this click came from -- rebuilt through
      // showPendingResults rather than left half-torn-down by
      // closePendingResults above, which means this case's recipe/style
      // controls come back at the analysis' own defaults; a failed attempt
      // is a fresh review, not a resumed one.
      await showPendingResults(analysis, preset);
      if (historyViewToken !== myHistoryToken) return; // navigated away while it rebuilt
      // Same modal, same friendlyError wrapping this job gets from its
      // history tile once it settles as 失敗 -- shown over the review page so
      // dismissing it leaves the user exactly where they can try again.
      showNoticeModal(friendlyError(failure));
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
    // Restores the invariant setAnalysisPreviewDisabled below depends on
    // (#ap-options-card-inline nested inside #analysis-preview) regardless of
    // how the PREVIOUS cycle ended. Every other exit from this panel already
    // goes through closeInlineAnalysisPanel -> restoreOptionsCardHome, but the
    // read-only path (backToProcessFromResults returns right after opening,
    // with no Run/Cancel to eventually trigger that close) never did --
    // leaving the card stranded at its moved-out target and silently
    // breaking every later open's disable/enable call. A no-op when the card
    // is already home.
    restoreOptionsCardHome();
    moveAnalysisPreviewTo(els.resultsAnalysisSlot);
    // Before moveOptionsCardTo below -- setAnalysisPreviewDisabled re-enables
    // via els.analysisPreview.querySelectorAll(...), which only finds
    // #ap-options-card-inline's controls (incl. #inline-analysis-continue-btn)
    // while that card is still nested inside #analysis-preview. Called after
    // the move, it silently found nothing there anymore and those controls
    // stayed disabled forever after the first disable/regenerate cycle.
    setAnalysisPreviewDisabled(readOnly);
    // The step indicator describes a live "about to (re)run from here" flow
    // -- read-only historical playback (viewHistoryRecord) reopens this same
    // panel for an already-finished job, where that would be factually
    // wrong, so hide it in that one case. The SLOT, not the indicator
    // inside it: the slot carries the spacing (index.html), so hiding only
    // the indicator would leave its padding behind as a gap. Every
    // showSection back into a live flow re-shows it (dom.js).
    if (els.stepIndicatorSlot) els.stepIndicatorSlot.hidden = readOnly;
    moveOptionsCardTo(getOptionsCardTarget());
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = readOnly;
    // Moved out of #inline-analysis-actions (now lives at the end of
    // #ap-options-card-inline) -- toggled alongside it here since it lost
    // that shared parent's hidden gating.
    if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = readOnly;
    // Reset unconditionally, same reasoning as runPendingReviewFlow above --
    // a readOnly caller that wants #ap-restart-actions shown (currently only
    // backToProcessFromResults) calls showArchivedAnalysisNotice right after
    // this, which re-shows it based on that case's own canRestart. A caller
    // that doesn't (e.g. the plain "reviewable:false" historical-playback
    // path) correctly leaves it hidden.
    if (els.apRestartActions) els.apRestartActions.hidden = true;
    updateApCaseSettingsTrigger();
    els.analysisPreview.hidden = false;
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.add("showing-analysis");
    // Mirrored onto #results-body too -- #batch-sidebar's width split (see
    // styles.css) needs this state and isn't a descendant of #results-canvas-row.
    if (els.resultsBody) els.resultsBody.classList.add("showing-analysis");
    updateAnalysisPreviewMaxHeight();
    syncAdjustingTile();
  }

  function closeInlineAnalysisPanel() {
    if (els.resultsCanvasRow) els.resultsCanvasRow.classList.remove("showing-analysis");
    if (els.resultsBody) els.resultsBody.classList.remove("showing-analysis");
    syncAdjustingTile();
    els.analysisPreview.hidden = true;
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = true;
    if (els.inlineAnalysisContinueBtn) els.inlineAnalysisContinueBtn.hidden = true;
    if (els.apRestartActions) els.apRestartActions.hidden = true;
    updateApCaseSettingsTrigger();
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
  // Which batch record the inline revise panel was last rendered for
  // (startInlineBatchReview) -- see openInlineAnalysisForCurrentContext's
  // "already showing" guard for why that has to be checked, not assumed.
  let inlinePanelBatchRecord = null;

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
  // but hoisted. Also covers two of #ap-case-settings-modal's own pickers
  // (模型 and 生成張數) -- those aren't part of readStyleSelectionsFromDom
  // since they're not "style" picks, but changing either is just as much a
  // real edit as the recipe/style/prompt fields.
  function captureInlineReviseSnapshot() {
    const { options, recipeChecked } = readStyleSelectionsFromDom();
    inlineReviseSnapshot = {
      options: { ...options },
      recipeChecked: Array.isArray(recipeChecked) ? [...recipeChecked] : recipeChecked,
      prompt: els.promptInput ? els.promptInput.value.trim() : "",
      overwritePrompt: els.promptOverwriteToggle ? els.promptOverwriteToggle.checked : false,
      modelQuality: els.apImageModelQualitySelect ? els.apImageModelQualitySelect.value : null,
      runCount: els.apRunCountSelect ? els.apRunCountSelect.value : null,
      aspectRatio: els.apImageAspectRatioSelect ? els.apImageAspectRatioSelect.value : null,
      outputScale: els.apImageOutputScaleSelect ? els.apImageOutputScaleSelect.value : null,
      // 人像修正設定 (#advanced-settings-fold, folded into 設定 on either page) --
      // just as real an edit as the picks above, so it needs the same
      // before/after snapshot to unblock the Run button.
      advancedOptions: readAdvancedOptionsFromDom("ap"),
      // Per-person "允許 AI 修正此項目" toggles (#ap-face-detail) -- same
      // reasoning as advancedOptions above, otherwise flipping only a face
      // toggle (nothing else) leaves inlineReviseHasNoChanges thinking
      // nothing changed and blocks the Run click.
      faceIssueOverrides: getFaceIssueOverrides(),
      // The holistic Body Reshape approval switch is a real edit choice too:
      // changing only this switch must unblock Run just like a face toggle.
      bodyReshapeEnabled: getBodyReshapeReviewEnabled()
    };
  }

  // Each value here is either the primitive `false`, or a flat one-level
  // object like {allowed:true} / {allowed:true, level:"dynamic"} -- see
  // getFaceIssueOverrides (results.js). A plain
  // `===` on those object values compared a fresh instance built just now
  // against the one captured in the snapshot, which are never the same
  // reference even when their contents match -- silently reporting "changed"
  // on every revise that involved any face-issue override at all, not just an
  // actual change to one. sameValue below compares that one
  // level of object contents instead of identity.
  function sameFaceIssueOverrides(a, b) {
    const sameValue = (va, vb) => {
      if (va === vb) return true;
      if (!va || !vb || typeof va !== "object" || typeof vb !== "object") return false;
      const vKeysA = Object.keys(va);
      const vKeysB = Object.keys(vb);
      return vKeysA.length === vKeysB.length && vKeysA.every((k) => va[k] === vb[k]);
    };
    const keysA = Object.keys(a || {});
    const keysB = Object.keys(b || {});
    return keysA.length === keysB.length && keysA.every((key) => sameValue(a[key], (b || {})[key]));
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
    // 接著這張調整 edits only what is asked: nothing asked, nothing to do.
    // What the version was made with, unchanged, is asked again (再美化).
    const cont = activeContinuation();
    if (cont) return isEmptyContinuationRequest(continuationRequestFromDom()) && !continuationEngineChanged(cont);
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
    const sameAspectRatio = (els.apImageAspectRatioSelect ? els.apImageAspectRatioSelect.value : null)
      === inlineReviseSnapshot.aspectRatio;
    const sameOutputScale = (els.apImageOutputScaleSelect ? els.apImageOutputScaleSelect.value : null)
      === inlineReviseSnapshot.outputScale;
    const advancedOptions = readAdvancedOptionsFromDom("ap");
    const sameAdvancedOptions = Object.keys(advancedOptions).every(
      (key) => advancedOptions[key] === inlineReviseSnapshot.advancedOptions[key]);
    const sameFaceIssues = sameFaceIssueOverrides(getFaceIssueOverrides(), inlineReviseSnapshot.faceIssueOverrides);
    const sameBodyReshape = getBodyReshapeReviewEnabled() === inlineReviseSnapshot.bodyReshapeEnabled;
    return sameOptions && sameRecipe && samePrompt && sameModelQuality && sameRunCount && sameAspectRatio
      && sameOutputScale && sameAdvancedOptions && sameFaceIssues && sameBodyReshape && sameCompositionAsShownResult();
  }

  // Against the shown result rather than the snapshot: a reframe stays a change
  // even after the panel is reopened (and re-snapshotted) on the new composition.
  function sameCompositionAsShownResult() {
    const record = currentDetailBatchRecord();
    const resultAnalysis = record ? record.result?.analysis : lastResult?.resultText?.analysis;
    const panelAnalysis = reframeOwnerOnScreen()?.analysis;
    if (!resultAnalysis || !panelAnalysis) return true;
    const plannedFor = (analysis) => String(analysis._aspect_ratio_pref_at_analysis || "auto").toLowerCase();
    return plannedFor(panelAnalysis) === plannedFor(resultAnalysis);
  }

  // Keeps the Run button visually flagged whenever inlineReviseHasNoChanges()
  // would block it, instead of only surfacing that at click time (the alert
  // in waitForInlineAnalysisAction below) with no warning beforehand. A CSS
  // class (.is-blocked), not the native disabled attribute -- a genuinely
  // disabled button can't dispatch "click" at all, which would silently
  // swallow that same alert instead of ever showing it. Wired onto every
  // control inlineReviseHasNoChanges reads (see call sites below), plus once
  // right after captureInlineReviseSnapshot itself. A no-op (stays clear)
  // outside a revise round -- inlineReviseHasNoChanges already returns false
  // with no snapshot, so there's nothing to flag for an image's very first
  // review.
  function updateInlineAnalysisContinueBtnState() {
    syncPromptOverwriteState();
    syncAdjustingTile();
    syncRedoLabel();
    if (!els.inlineAnalysisContinueBtn) return;
    els.inlineAnalysisContinueBtn.classList.toggle("is-blocked", inlineReviseHasNoChanges());
  }

  // The run button says 再美化, as the chat's does, while it would redo the
  // version on the panel from its source (continuationRedoSource); its
  // tooltip names that version, since the base pill still shows this one.
  function syncRedoLabel() {
    const btn = els.inlineAnalysisContinueBtn;
    const label = btn?.querySelector("span");
    if (!label) return;
    const cont = activeContinuation();
    const redo = cont && continuationRedoSource(cont);
    const from = redo && singleRuns.find((run) => run.versionId === redo.version_id)?.versionNumber;
    label.textContent = redo ? tr("再美化") : tr("美化");
    if (redo && from != null) btn.title = tr("需求沒改，會從第 {n} 版照一樣的需求再做一版", { n: from });
    else btn.removeAttribute("title");
  }

  // 僅依需求修改 (#prompt-overwrite) only means something with a request
  // typed, so it is held off without one; #prompt-overwrite-hint says what
  // this 美化 will do, and #ap-review-panel's is-request-only dims the
  // suggestions the edit then leaves out. The box keeps its tick while held
  // off, so typing the request back brings the choice back with it.
  // Hidden while 接著這張調整: that edit is request-only whatever the box
  // says (continuation_form), as the shell's description already tells.
  function syncPromptOverwriteState() {
    const toggle = els.promptOverwriteToggle;
    if (!toggle) return;
    const continuing = !!activeContinuation();
    const hasPrompt = !!els.promptInput?.value.trim();
    const on = !continuing && hasPrompt && toggle.checked;
    // The tray shows only once there is a request to keep to, and never
    // while continuing: that edit is request-only regardless.
    const showTray = !continuing && hasPrompt;
    toggle.disabled = !showTray;
    if (els.promptOverwriteTray) els.promptOverwriteTray.hidden = !showTray;
    if (els.apReviewPanel) els.apReviewPanel.classList.toggle("is-request-only", on);
    // Made without an analysis and still kept to the request: the case's
    // suggestions come up only once the box is unticked.
    setReviewSuggestionsHeld(on && madeWithoutAnalysis(panelRun()));
    if (!showTray || !els.promptOverwriteHint) return;
    els.promptOverwriteHint.textContent = on ? tr("只動你寫到的地方（加上你挑的風格），其他保持原樣。")
      : skippedCaseNeedsAnalysis() ? tr("沒勾的話會整體美化：先分析整張照片，再依照分析結果和你的需求一起修圖。")
      : tr("沒勾的話會整體美化：照你的需求修改，光線、人像、構圖也一起調整。");
  }

  // A 「只照需求改」 case (only the stub analysis) can be edited as it is only
  // under 僅依需求修改 with a request; anything else needs the analysis first.
  // Not while 接著這張調整: that edits from the version, no analysis needed.
  function skippedCaseNeedsAnalysis() {
    if (!singleFlowState?.analysis?._skipped_analysis || activeContinuation()) return false;
    return !(els.promptOverwriteToggle?.checked && els.promptInput?.value.trim());
  }

  // Resolves "continue" or "cancel" depending on which inline button the user
  // clicks -- same one-shot-listener shape as waitForPendingResultsAction,
  // just with two possible outcomes instead of one. getModelQuality feeds
  // the cost estimate: the single-image flow's live pick by default; batch
  // passes the record's own (singleFlowState is null in batch mode, so
  // reading it there threw and 美化 did nothing).
  // getBlockedNotice: optional -- returns a message when 美化 must not run at
  // all right now (shown instead, panel stays open), null otherwise.
  function waitForInlineAnalysisAction(
    getModelQuality = () => ({ model: singleFlowState.imageModel, quality: singleFlowState.imageQuality }),
    getBlockedNotice = () => null
  ) {
    return new Promise((resolve) => {
      clearInlineAnalysisWait();
      // Same guard waitForPendingResultsAction has: whatever takes over the
      // screen next (Home, a new upload, a history record) bumps
      // historyViewToken, and a click after that belongs to it, not to this
      // revise panel. The button is shared with the pending-review wait, so
      // a handler left over from a results page would otherwise fire
      // alongside the next photo's own 美化 handler and submit a second,
      // paid /continue for it.
      const myToken = historyViewToken;
      inlineAnalysisContinueHandler = async function onContinue() {
        if (historyViewToken !== myToken) { clearInlineAnalysisWait(); return; }
        // 整體美化 on a case that skipped its analysis: one go, the same
        // combined job 一鍵美化 runs (finishSingleFlow's useCombinedJob) --
        // the analysis first and the edit with its suggestions, no stop to
        // review them in between, and done on its own if the user leaves.
        if (skippedCaseNeedsAnalysis()) {
          const state = singleFlowState;
          const { model, quality } = getModelQuality();
          const est = preflightEditEstimate(model, quality);
          const confirmed = await showPreflightCostModal(
            [analysisPreflightStep(), { label: tr("美化花費"), modelLabel: est.label, cost: est.cost }]);
          // Still this panel's 美化 to give: not cancelled or replaced meanwhile.
          if (!confirmed || singleFlowState !== state || inlineAnalysisContinueHandler !== onContinue) return;
          state.useCombinedJob = true;
          state.combinedFromJobId = state.analyzeJobId;
          clearInlineAnalysisWait();
          resolve("continue");
          return;
        }
        const blockedNotice = getBlockedNotice();
        if (blockedNotice) { showNoticeModal(blockedNotice); return; }
        // Nothing to actually change vs. the existing result -- confirm the
        // user really wants to spend another edit call reproducing it rather
        // than silently blocking them, since re-running as-is (e.g. to redo
        // a failed generation) is a legitimate reason to proceed. A Cancel
        // here doesn't clear the listener/resolve, so the panel just stays
        // open, same as before this confirmation was added.
        // Same pre-flight cost estimate as the first-time continue
        // (waitForPendingResultsAction) -- this button is the same 美化
        // action, just for a later revise round, and still spends a real
        // edit call each time it's pressed.
        const { model, quality } = getModelQuality();
        const est = preflightEditEstimate(model, quality);
        if (inlineReviseHasNoChanges()) {
          // Folded into the same "nothing changed, are you sure" prompt
          // (per the user's own request) instead of showing it and then a
          // separate cost-estimate modal right after -- costSteps renders it
          // with #preflight-cost-modal's own row styling rather than a
          // plain-text sentence.
          const confirmed = await showConfirmModal(
            tr("你沒有做任何修改，確定要再執行一次美化嗎？"),
            { confirmLabel: tr("美化"), danger: false, costSteps: [{ label: tr("美化花費"), modelLabel: est.label, cost: est.cost }] });
          if (!confirmed) return;
        } else {
          const costConfirmed = await showPreflightCostModal([{ label: tr("美化花費"), modelLabel: est.label, cost: est.cost }]);
          if (!costConfirmed) return;
        }
        clearInlineAnalysisWait();
        resolve("continue");
      };
      inlineAnalysisCancelHandler = () => { clearInlineAnalysisWait(); resolve("cancel"); };
      els.inlineAnalysisContinueBtn.addEventListener("click", inlineAnalysisContinueHandler);
      els.inlineAnalysisCancelBtn.addEventListener("click", inlineAnalysisCancelHandler);
    });
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
          reject(new Error(tr("HEIC 預覽元件載入失敗")));
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
          reject(new Error(tr("截圖元件載入失敗")));
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
      // Fallback fill for anything html2canvas can't capture (e.g. a
      // layer it fails to render) -- read from the live theme instead of
      // hardcoding the default dark theme's bg, which showed through as a
      // mismatched dark patch on the light paper/peach themes.
      const themeBg = getComputedStyle(document.body).getPropertyValue("--bg").trim() || "#09090b";
      const canvas = await Promise.race([
        html2canvas(document.body, {
          backgroundColor: themeBg,
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
            // while its ancestor is display:none (e.g. any closed modal) --
            // html2canvas has to wait for every <img> in the
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
        new Promise((_, reject) => setTimeout(() => reject(new Error(tr("擷取畫面逾時"))), 8000))
      ]);

      // Encode JPEG under size cap (quality-first then downscale).
      const jpegBlob = await encodeUnderCap(canvas, MAX_SCREENSHOT_BYTES);
      if (jpegBlob) return jpegBlob;
      // If encoding produced nothing (very unlikely), fall back to PNG.
      return await new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/png"));
    } catch (err) {
      console.error(tr("回報截圖擷取失敗"), err);
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
      if (!images.length) throw new Error(tr("HEIC 檔案沒有可解碼的影像"));
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
          if (!displayData) reject(new Error(tr("HEIC 解碼失敗")));
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
      showError(tr("請選擇 JPG、PNG、WebP 或 HEIC/HEIF 圖片。"));
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
    // Same class of bug as above, but for face-issue overrides:
    // that state lives in results.js's faceThumbEntries (an in-memory array,
    // not the DOM), which finishSingleFlow's getFaceIssueOverrides() reads
    // directly. Without this, the previous photo's allowed/flagged state
    // (e.g. an ordinary should_fix:true item, never touched by the user)
    // gets captured as this photo's "override" and later stamped onto its
    // own freshly-analyzed (and possibly should_fix:false) issues, showing
    // as a phantom "已手動開啟".
    resetFaceReviewState();

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
    return `PhotoMaster-${dateStr}-${timeStr}.zip`;
  }

  // Extension and content type of already-encoded image bytes, read from
  // their magic number (the browser-side counterpart of image_tools.py's
  // sniff_image_mime). Results are stored as JPEG now, but /result/<job_id>
  // /image still serves genuine PNG bytes for jobs created before that, so
  // download names and blob types follow the bytes instead of assuming one.
  function imageFormatOf(buffer) {
    const head = new Uint8Array(buffer, 0, Math.min(4, buffer.byteLength));
    if (head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47) {
      return { ext: "png", mime: "image/png" };
    }
    return { ext: "jpg", mime: "image/jpeg" };
  }

  function buildDownloadImageFilename(extension = "jpg") {
    const pad = (n) => String(n).padStart(2, "0");
    const now = new Date();
    const dateStr = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
    const timeStr = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    return `PhotoMaster-${dateStr}-${timeStr}.${extension}`;
  }

  const PROMPT_KEYS = [
    "photo_analysis", "photo_analysis_portrait_qc", "photo_analysis_eye_repair_planning",
    "photo_analysis_body_reshape",
    "user_instruction_review",
    "image_edit",
    "image_edit_resolution", "image_edit_strategy",
    "gaze_repair_pass", "quality_check"
  ];

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
  function resultFileNames(runNumber, imageExt = "jpg") {
    return {
      image: `result_${runNumber}.${imageExt}`,
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
      // run.sourceRun, not run.run -- see fetchVersionRuns.
      const imageBuf = await fetchWithRetry(resultImageUrl(run.jobId, { path, run: run.sourceRun != null ? run.sourceRun : run.run })).then((response) => response.arrayBuffer());
      if (onItemDone) onItemDone();
      return { run, imageBuf };
    }));
    return { sourceBuf, runData };
  }

  // Normal mode: source + one result_N image per RUN, flat under `prefix` --
  // no per-RUN subfolder, no JSON. Everyday users just want the pictures.
  function buildFlatImageFiles({ file, prefix }, sourceBuf, runData) {
    const files = sourceBuf ? [{ name: prefix + (file?.name || "source.jpg"), data: new Uint8Array(sourceBuf) }] : [];
    runData.forEach(({ run, imageBuf }) => {
      files.push({ name: prefix + resultFileNames(run.run || 1, imageFormatOf(imageBuf).ext).image, data: new Uint8Array(imageBuf) });
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
        { name: `${runPrefix}result.${imageFormatOf(imageBuf).ext}`, data: new Uint8Array(imageBuf) },
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

  // pendingResultsActive's own download bundle -- source photo only (the
  // edit stage hasn't run yet, so there's no result image/token usage to
  // include), plus whatever the analyze stage itself already produced in
  // --debug-ui mode (the raw analysis + its own prompts, same JSON shape
  // buildNestedImageFiles writes per-RUN, just without a run_N/ subfolder
  // since there's only ever one of these per photo). selectedFile is null
  // for a history record reopened at this same pending-review stage
  // (viewAnalyzeHistoryRecord) -- falls back to fetching selectedFileSourceUrl
  // instead, same as fetchImageDownloadData's own file/sourceUrl fallback.
  async function buildPendingSourceZip() {
    const sourceBuf = selectedFile
      ? await selectedFile.arrayBuffer()
      : selectedFileSourceUrl
        ? await fetchWithRetry(selectedFileSourceUrl).then((response) => response.arrayBuffer())
        : null;
    const files = sourceBuf ? [{ name: selectedFile?.name || "source.jpg", data: new Uint8Array(sourceBuf) }] : [];
    if (isDebugUiActive && singleFlowState?.analysis) {
      const analysisBytes = new TextEncoder().encode(JSON.stringify(singleFlowState.analysis, null, 2));
      const promptsBytes = new TextEncoder().encode(buildPromptsJson(singleFlowState.analysis.prompts || {}));
      files.push(
        { name: "analysis.json", data: analysisBytes },
        { name: "prompts.json", data: promptsBytes }
      );
    }
    return window.PhotoAgentZip.createZip(files);
  }

  // iOS Safari has no web API to write directly into the Photos app (no
  // requestable "save to camera roll" permission for a plain page, unlike
  // PHPhotoLibrary for a native app) -- its <a download> handling for an
  // image just lands the file in the Files app's Downloads folder instead.
  // The Web Share sheet's own "储存影像/Save Image" action is the only path
  // a web page has into Photos, so on iOS this is tried first; the existing
  // blob-anchor download is now the fallback (still used as-is on every
  // other platform, and here whenever sharing isn't available/fails).
  function isIOSDevice() {
    const ua = navigator.userAgent || "";
    // iPadOS 13+ reports as "Macintosh" but, unlike a real Mac, exposes touch
    // points -- maxTouchPoints is what tells the two apart.
    return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
  }

  function downloadBlobViaAnchor(blob, filename) {
    const url = URL.createObjectURL(blob);
    const tempLink = document.createElement("a");
    tempLink.href = url;
    tempLink.download = filename;
    document.body.appendChild(tempLink);
    tempLink.click();
    tempLink.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Never rejects except on a genuine unexpected error -- a user-cancelled
  // share sheet (AbortError) is treated the same as a normal completed
  // download, since the page has no way to tell whether "Save Image" was
  // actually tapped inside that sheet either way.
  async function saveImageToDevice(blob, filename) {
    if (isIOSDevice() && navigator.canShare) {
      const file = new File([blob], filename, { type: blob.type || "image/jpeg" });
      if (navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({ files: [file] });
          return;
        } catch (error) {
          if (error && error.name === "AbortError") return;
          // Any other failure falls through to the same anchor download
          // every other platform uses.
        }
      }
    }
    downloadBlobViaAnchor(blob, filename);
  }

  async function downloadPendingSource() {
    if (!selectedFile && !selectedFileSourceUrl) return;
    // --debug-ui mode still zips (source + analysis.json + prompts.json),
    // same reasoning as downloadCurrentResultOnly's split below -- plain
    // mode just wants the one photo, no zip overhead for a single file.
    if (isDebugUiActive) {
      await downloadZip(buildPendingSourceZip, buildDownloadZipFilename());
      return;
    }
    try {
      // selectedFile is null for a history record reopened at this same
      // pending-review stage (viewAnalyzeHistoryRecord) -- fetch its own
      // source photo (selectedFileSourceUrl) into a blob instead, same
      // fallback buildPendingSourceZip's debug-mode path applies.
      const blob = selectedFile || await fetchWithRetry(selectedFileSourceUrl).then((response) => response.blob());
      await saveImageToDevice(blob, (selectedFile && selectedFile.name) || buildDownloadImageFilename());
      showNoticeModal(tr("已下載完成。"));
    } catch (error) {
      showError(tr("下載失敗，請稍後再試。"));
    }
  }

  // downloadPendingSource's batch counterpart -- for a batch record still at
  // "analyzed" (reviewed but not yet edited), #download-link has the same
  // "only the source photo exists" situation as the single-image flow's own
  // pending-review screen, just keyed off this record's file/analysis
  // instead of selectedFile/singleFlowState. Reuses buildBatchSourceFiles
  // (buildAllBatchResultsZip's own per-image fallback) for the debug-mode
  // zip so the analysis.json/prompts.json inclusion logic isn't duplicated.
  async function downloadPendingBatchSource(record) {
    if (!record.file) return;
    if (isDebugUiActive) {
      await downloadZip(
        async () => window.PhotoAgentZip.createZip(await buildBatchSourceFiles(record, "", () => {})),
        buildDownloadZipFilename()
      );
      return;
    }
    try {
      await saveImageToDevice(record.file, record.file.name || buildDownloadImageFilename());
      showNoticeModal(tr("已下載完成。"));
    } catch (error) {
      showError(tr("下載失敗，請稍後再試。"));
    }
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
    const hint = lastDoneRunsCount > 1 ? tr("下載選項（共 {lastDoneRunsCount} 張）", { lastDoneRunsCount }) : tr("下載結果");
    els.downloadLink.title = hint;
    els.downloadLink.setAttribute("aria-label", hint);
  }

  // #report-btn is position:fixed (see its own markup comment, index.html)
  // so it keeps winning against modals/drawers, which also takes it out of
  // .header-actions' own flex flow -- #report-btn-spacer is a same-sized,
  // invisible stand-in left in that flow instead, purely to reserve the
  // slot #reset-btn/#header-settings-btn/#admin-reports-btn/#account-badge
  // lay out around. This copies the spacer's live position onto the real
  // button, so it always renders exactly where that reserved slot actually
  // is. Called whenever anything in that row could have changed size
  // (account/admin state, window resize) -- see its own call sites.
  function syncReportBtnPosition() {
    if (!els.reportBtn || !els.reportBtnSpacer) return;
    const rect = els.reportBtnSpacer.getBoundingClientRect();
    // .header-inner has no flex-wrap, and neither .logo nor .header-actions'
    // own children shrink below their content size -- on a narrow enough
    // phone, .logo + up to four header buttons (#reset-btn,
    // #header-settings-btn, #admin-reports-btn, #account-badge) can still
    // add up to more than the viewport's width despite the 560px
    // tightening, pushing the spacer itself (and therefore #report-btn,
    // which just copies its position) partway or fully off-screen.
    // Clamping here guarantees #report-btn always stays fully inside the
    // viewport regardless -- at the cost of no longer lining up with the
    // spacer exactly in that overflow case, which only matters visually,
    // not functionally (nothing else is positioned relative to
    // #report-btn).
    const margin = 8;
    const maxLeft = window.innerWidth - els.reportBtn.offsetWidth - margin;
    const clampedLeft = Math.min(Math.max(rect.left, margin), Math.max(margin, maxLeft));
    els.reportBtn.style.top = `${rect.top}px`;
    els.reportBtn.style.left = `${clampedLeft}px`;
    // Starts visibility:hidden (index.html <style>) so it never flashes at
    // its own static-flow position (flush left, right after </header>)
    // before this function's first call places it correctly -- reveal it
    // now that top/left are actually set.
    els.reportBtn.style.visibility = "visible";
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
      els.reportModalScreenshotCaptureBtn.textContent = tr("擷取目前畫面");
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
        els.reportModalScreenshotCaptureBtn.textContent = tr("重新擷取");
      }
      if (els.reportModalScreenshotStatus) {
        els.reportModalScreenshotStatus.textContent = tr("無法擷取畫面預覽，回報仍可送出。");
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
      els.reportModalScreenshotCaptureBtn.textContent = tr("擷取中…");
    }
    // No image ready yet for this attempt -- keep the preview box hidden
    // and surface progress via the status line instead.
    if (els.reportModalScreenshot) els.reportModalScreenshot.hidden = true;
    if (els.reportModalScreenshotPreviewBtn) els.reportModalScreenshotPreviewBtn.hidden = true;
    if (els.reportModalScreenshotDeleteBtn) els.reportModalScreenshotDeleteBtn.hidden = true;
    if (els.reportModalScreenshotStatus) {
      els.reportModalScreenshotStatus.textContent = tr("擷取畫面中…");
      els.reportModalScreenshotStatus.hidden = false;
    }
    const blob = await captureReportScreenshot(myTarget.jobId, myTarget.path, myTarget.sourceRun);
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
    // screen: currentBatchRecord's own userPrompt when a batch result is
    // showing, else lastSingleSelections.userPrompt -- set by
    // finishSingleFlow for a live single-image submission, or restored from
    // job.user_prompt (restoreReviseSelections) when viewing a
    // history/report record.
    const userPrompt = lastResult
      ? (currentBatchRecord ? (currentBatchRecord.userPrompt || "") : (lastSingleSelections?.userPrompt || ""))
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
      // What captureReportScreenshot's own same-origin image proxy needs --
      // see fetchVersionRuns on why that isn't runNumber.
      sourceRun: lastResult?.sourceRun != null ? lastResult.sourceRun : (lastResult?.runNumber || null),
      userPrompt
    };
    reportModalTarget = target;
    if (els.reportModalTitle) els.reportModalTitle.textContent = lastResult ? tr("回報此結果") : tr("回報問題");
    if (els.reportModalNote) {
      els.reportModalNote.placeholder = lastResult ? tr("請描述這筆結果的問題…") : tr("請描述遇到的問題…");
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

  // The case on screen, for the admin to open its conversation from /chat
  // (photo-agent-api.js reportJob). Same lookup as the 對話模式 switch: a
  // photo uploaded on this page has only its jobs until /case resolves one.
  async function reportCaseRef(target) {
    const owner = getCurrentUser();
    if (!owner) return {};
    let caseId = currentCaseHistoryImageKey;
    if (!caseId && target.jobId) {
      try {
        caseId = (await getHistoryCase(owner, { jobId: target.jobId }))?.case?.image_key || null;
      } catch {
        caseId = null; // not in history yet -- a plain panel report
      }
    }
    return caseId ? { owner, caseId } : {};
  }

  async function handleReportSubmit() {
    if (!reportModalTarget) return;
    const name = normalizeUsername(els.reportModalName ? els.reportModalName.value : "");
    if (!name) {
      showReportModalError(tr("請輸入你的名稱。"));
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
      await reportJob(name, target.jobId, target.path, note, target.userPrompt, reportModalScreenshotBlob, await reportCaseRef(target));
      closeReportModal();
      showNoticeModal(tr("已回報，我們會盡快確認這筆結果。"));
    } catch (err) {
      showReportModalError(err.message || tr("回報失敗，請稍後再試。"));
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
    if (els.downloadProgressLabel) els.downloadProgressLabel.textContent = tr("打包下載中…");
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
        ? tr("打包下載中…（{done}/{total}）", { done, total })
        : tr("打包下載中…");
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
      showError(tr("打包下載失敗，請稍後再試。"));
    } finally {
      hideDownloadProgress(success);
    }
    // Confirms the zip actually landed -- otherwise the only feedback is the
    // progress toast quietly fading out, easy to miss for a multi-select
    // download that took a while to build.
    if (success) showNoticeModal(tr("已下載完成。"));
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
      const imageBuf = await fetch(resultImageUrl(current.jobId, { path: lastResult.path, run: current.sourceRun != null ? current.sourceRun : current.run })).then((response) => response.arrayBuffer());
      const format = imageFormatOf(imageBuf);
      const blob = new Blob([imageBuf], { type: format.mime });
      await saveImageToDevice(blob, buildDownloadImageFilename(format.ext));
      // Same completion notice downloadZip shows -- this path bypasses that
      // shared function (a single image needs no zip), so it needs its own.
      showNoticeModal(tr("已下載完成。"));
    } catch (error) {
      showError(tr("下載失敗，請稍後再試。"));
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
    if (pendingResultsActive) {
      downloadPendingSource();
      return;
    }
    // Batch counterpart of pendingResultsActive: the currently-viewed record
    // is still at its pre-edit analysis review (showBatchAnalyzedMain), so
    // there's no result yet to route through the lastResult path below --
    // same "only the source photo exists" situation, just per-record instead
    // of a single global flag.
    if (currentBatchRecord && currentBatchRecord.status === "analyzed") {
      downloadPendingBatchSource(currentBatchRecord);
      return;
    }
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

  // #boxes-toggle-btn's type-picker popover -- same open/close/outside-click
  // shape as #download-menu above.
  function openBoxesTypeMenu() {
    if (!els.boxesTypeMenu) return;
    els.boxesTypeMenu.hidden = false;
    if (els.boxesToggleBtn) els.boxesToggleBtn.setAttribute("aria-expanded", "true");
    document.addEventListener("click", handleBoxesTypeMenuOutsideClick);
  }

  function closeBoxesTypeMenu() {
    if (!els.boxesTypeMenu || els.boxesTypeMenu.hidden) return;
    els.boxesTypeMenu.hidden = true;
    if (els.boxesToggleBtn) els.boxesToggleBtn.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", handleBoxesTypeMenuOutsideClick);
  }

  function toggleBoxesTypeMenu() {
    if (!els.boxesTypeMenu) return;
    if (els.boxesTypeMenu.hidden) openBoxesTypeMenu(); else closeBoxesTypeMenu();
  }

  function handleBoxesTypeMenuOutsideClick(event) {
    if (els.boxesTypeMenuWrap && !els.boxesTypeMenuWrap.contains(event.target)) closeBoxesTypeMenu();
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
      progressTipsOnly: false, // One-click display policy; never sent to the backend.
      uploadedAt: null,
      modifiedAt: null,
      analysis: null,       // step-1 result (shown in the step-2 review panel)
      analyzeJobId: null,   // local path only: the analyze job, reused by continue
      options: null,        // per-image style options {pose,angle,lighting,size} (UI-only)
      recipeChecked: null,  // per-image recipe checklist state (UI-only)
      bodyReshapeEnabled: null, // per-image holistic Body review switch (UI-only)
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
      // Per-image 模型/畫質 + 長寬比 + 分析模型 overrides (UI-only), set only
      // once this image's own revise panel is used to change them -- null
      // means "still inherits the batch-level default", same override rule
      // as options above (see effectiveModelQuality/effectiveAspectRatio/
      // effectiveAnalysisModel). Without their own slot here, every case's
      // revise panel wrote straight into the single shared `batchSettings`,
      // so picking a model on case 1 silently carried over to case 2.
      imageModel: null,
      imageQuality: null,
      aspectRatio: null,
      outputScale: null,
      analysisModel: null
    };
  }

  // Filters `files` down to supported images, showing the same rule/message
  // either caller (setSelectedFileBatch/addFilesToBatch) needs -- returns
  // null (already handled) if nothing usable was left.
  function filterBatchImageFiles(files) {
    const images = files.filter((file) => isSupportedImageFile(file));
    if (!images.length) {
      showError(tr("請選擇 JPG、PNG、WebP 或 HEIC/HEIF 圖片。"));
      return null;
    }
    if (images.length !== files.length) {
      showError(tr("已略過非圖片檔案。"));
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
    removeBtn.setAttribute("aria-label", tr("移除照片"));
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
    addMore.setAttribute("aria-label", tr("新增照片"));
    addMore.innerHTML = `
      <span class="dz-add-more-icon">+</span>
      <span class="dz-add-more-label">${tr("新增照片")}</span>
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
    reviewBaseline = null;
    lastSingleSelections = null;
    if (els.showPromptBtn) els.showPromptBtn.hidden = true;
    if (els.costBtn) els.costBtn.hidden = true;
    if (els.dzMulti) { els.dzMulti.hidden = true; els.dzMulti.innerHTML = ""; }
    if (els.batchSidebarList) els.batchSidebarList.innerHTML = "";
    if (els.batchSidebarFilters) { els.batchSidebarFilters.hidden = true; els.batchSidebarFilters.innerHTML = ""; }
    hideCaseGroupSidebar();
    // Undo whatever hideAllBatchMainViews left mid-batch, so a subsequent
    // single-image run (which never touches these) isn't left with the
    // canvas/run switcher hidden from a prior batch session.
    if (els.batchMainLoading) { els.batchMainLoading.hidden = true; els.batchMainLoading.classList.remove("is-error"); }
    restoreAnalysisPreviewHome();
    if (els.resultsCanvasRow) { els.resultsCanvasRow.hidden = false; els.resultsCanvasRow.classList.remove("showing-analysis"); }
    if (els.resultsBody) els.resultsBody.classList.remove("showing-analysis");
    resetRunSwitcher();
    if (els.batchDownloadAllBtn) els.batchDownloadAllBtn.hidden = true;
    if (els.batchContinueAllBtn) els.batchContinueAllBtn.hidden = true;
  }

  // Labels for the categories OpenAI's safety system has been observed to
  // tag (safety_violations=[...] in the raw error) -- keyed lowercase since
  // the exact casing isn't guaranteed. Anything not listed here (including a
  // category OpenAI adds later) falls back to a generic explanation rather
  // than guessing a specific reason that might be wrong.
  const SAFETY_VIOLATION_LABELS = {
    sexual: tr("照片可能包含裸露或性暗示內容"),
    violence: tr("照片可能包含暴力或血腥內容"),
    "self-harm": tr("照片可能包含自我傷害相關內容"),
    self_harm: tr("照片可能包含自我傷害相關內容"),
    hate: tr("照片可能包含仇恨或歧視相關內容"),
    harassment: tr("照片可能包含騷擾相關內容")
  };

  function friendlyError(error) {
    if (error.message === "Failed to fetch") {
      return tr("目前無法連線到伺服器，請稍後再試，或確認網路連線是否正常。");
    }
    // error.code is the backend's short error code (see server.py's
    // process_for_api except block); ai_service_timeout means the AI
    // service itself (OpenAI/Gemini) didn't respond in time after retries
    // -- error.message for this case is a raw requests.Timeout string,
    // too technical to show as-is.
    if (error.code === "ai_service_timeout") {
      return tr("系統回應逾時，請稍後再試一次。");
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
        ? knownReasons.join(tr("、")) + tr("，")
        : tr("這通常是因為照片中包含平台內容政策不允許編輯的元素（例如可辨識的受著作權保護角色/商標，或其他敏感內容），");
      return tr("這張照片目前無法完成美化——{reasonText}基於平台政策無法編輯這類內容。請嘗試更換照片，或調整美化風格後再試一次。", { reasonText });
    }
    return msg || tr("發生未知錯誤。");
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
  //
  // fromJobId: instead of uploading `file`, runs on the photo that job
  // already holds -- 美化 on a 「只照需求改」 case (analyzeAndFinishExistingJob),
  // with the panel's `selections` for the worker to apply.
  async function runCombinedMultiRun({ file, fromJobId = null, selections = null, preset, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, advancedOptions, userPrompt = "", overwritePrompt = false, runCount = 1, onStage, onRunDone }) {
    const jobId = fromJobId
      ? await analyzeAndFinishExistingJob({ jobId: fromJobId, preset, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, advancedOptions, selections, userPrompt, overwritePrompt, runCount, user: getCurrentUser() })
      : await submitJob({ file, preset, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, advancedOptions, userPrompt, overwritePrompt, runCount, user: getCurrentUser(), path: "local" });
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
          await onStage(data.stage);
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
    // This new run owns the screen from here on -- bumped BEFORE the
    // snapshot below so this run's own guard checks against its own token,
    // while any job still finishing from an earlier one now fails its check
    // and drops its render (historyViewToken's own comment has the case).
    historyViewToken += 1;
    // Snapshot of historyViewToken as of when this upload started -- the
    // manual (two-stage, non-skip-review) path below awaits a real analyze
    // job that's now recorded into history the instant it's submitted (see
    // api.py's /analyze), so the user can navigate to a different history
    // record and then resume THIS SAME one via viewAnalyzeHistoryRecord
    // while this original call is still in flight. Without this guard, this
    // orphaned call would clobber whatever viewAnalyzeHistoryRecord already
    // rendered once its own analyze wait finished -- same isCurrent()
    // pattern finishSingleFlow already uses, just checked before this
    // function's OWN direct showPendingResults/renderAnalysisPreview calls,
    // which aren't behind finishSingleFlow's guard at all.
    const myHistoryToken = historyViewToken;
    if (batchRecords.length > 1) {
      await startSubmitBatch(skipReview);
      return;
    }
    if (!selectedFile) {
      showError(tr("請先上傳一張照片。"));
      return;
    }
    setCurrentCaseTimes({ uploadedAt: Date.now() / 1000 });

    const presetKey = "natural";
    const preset = presets[presetKey] || presets.natural;
    const modelQualityOpt = findModelQualityOption(els.imageModelQualitySelect?.value) || {};
    const imageQuality = modelQualityOpt.quality || "medium";
    const imageModel = modelQualityOpt.model || "gpt-image-2";
    const analysisModel = els.analysisModelSelect?.value || window.PhotoAgentConfig.defaultAnalysisModel || "gpt-5.6-luna";
    const aspectRatio = els.imageAspectRatioSelect?.value || window.PhotoAgentConfig.defaultAspectRatio || "auto";
    const outputScale = els.imageOutputScaleSelect?.value || window.PhotoAgentConfig.defaultOutputScale || "2K";
    const aiPhotoEditing = els.aiEditToggle.checked;
    const runQa = els.runQaToggle ? els.runQaToggle.checked : false;
    const singlePassEdit = readSinglePassEditOption();
    // Advanced Settings toggles (Eyes category, and whatever joins it later)
    // -- bundled into one object instead of one variable per toggle, so a new
    // toggle only needs a new key here, not a new argument threaded through
    // analyzeAipoc/analyzeJob/singleFlowState/runCombinedMultiRun/submitJob.
    const advancedOptions = readAdvancedOptionsFromDom("upload");
    const processingPath = els.processingPathSelect?.value || "local";

    els.startButton.disabled = true;
    if (els.startFullButton) els.startFullButton.disabled = true;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    resetSteps(); // clears completedTimings so each run's total starts fresh
    closePendingResults(); // drop any stale pending-review state from an abandoned prior run
    setFlowStage("analyzing"); // analysis is only just starting -- not finished yet
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    if (els.promptInput) els.promptInput.value = "";
    // Same shared control as #prompt above -- a new photo starts with it off.
    if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = false;
    procVisual.classList.remove("done");
    procVisual.style.height = "";
    if (procTitle) procTitle.textContent = tr("正在分析照片");
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
          const analyzeData = await analyzeAipoc({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, advancedOptions });
          analysis = analyzeData.analysis;
          const t = analysis.timings || {};
          recordAnalyzeTiming(tr("初步分析"), Date.now() - stepStart, [
            { label: tr("Step 2：照片分析"), duration: secToMs(t["Step 2 - photo_analysis"]) }
          ]);
        } else {
          analyzeJobId = await analyzeJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, advancedOptions, user: getCurrentUser() });
          recordAnalyzeTiming(tr("送出分析任務"), Date.now() - stepStart);

          stepStart = Date.now();
          await pollJobStatus(analyzeJobId, { path: "local" });
          const analyzeWaitDuration = Date.now() - stepStart;

          stepStart = Date.now();
          const analyzeResult = await getAnalyzeResult(analyzeJobId);
          analysis = analyzeResult.result.analysis;
          const t = analysis.timings || {};
          recordAnalyzeTiming(tr("分析中"), analyzeWaitDuration, [
            { label: tr("Step 2：照片分析"), duration: secToMs(t["Step 2 - photo_analysis"]) }
          ]);
          recordAnalyzeTiming(tr("取得分析結果"), Date.now() - stepStart);
        }
      }

      singleFlowState = { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, advancedOptions, processingPath, analyzeTimings, useCombinedJob, progressTipsOnly: skipReview };
      // A fresh analyze always runs the current analyze_stage, so there's
      // nothing to flag as incompatible here -- just the version number, for
      // debug mode. useCombinedJob has no `analysis` yet at this point
      // (finishSingleFlow fills it in once the combined job completes).
      if (analysis) setCurrentCaseAnalysisVersion(analysis);
      if (historyViewToken !== myHistoryToken) return; // navigated away meanwhile (see this function's own comment above)

      if (useCombinedJob) {
        await finishSingleFlow();
      } else if (skipReview) {
        // Straight through, no stop: still populate the analysis-preview DOM
        // (finishSingleFlow reads its recipe checkboxes/style picks/prompt
        // straight from there) but never show the panel or wait for a click.
        renderAnalysisPreview({ analysis, preset });
        await finishSingleFlow();
      } else {
        await runPendingReviewFlow(analysis, preset);
      }
    } catch (error) {
      // Only reported on screen while this run still owns it. The user can
      // walk away from the processing screen mid-job by opening a history
      // record (which bumps historyViewToken), and this failure is then news
      // about a job they've stopped watching -- announcing it here means a
      // hard navigation to the upload screen (#error-msg lives nowhere else),
      // tearing them off the record they had just opened. The history tile
      // carries it instead, and already does: pollHistoryTiles re-renders
      // this job's still-美化中 tile as 失敗 the moment it settles, and
      // clicking that tile opens this same reason in a modal
      // (renderTileStatus's failed-tile branch, friendlyError and all). So
      // staying quiet here loses nothing but the ambush.
      if (historyViewToken === myHistoryToken) {
        // Lets any not-yet-shown phrasings finish playing (fast-forwarded)
        // before bailing back to the upload screen.
        await finishProcSubRotation();
        showSection("upload");
        setFlowStage("idle"); // nothing running any more
        // Same modal the history tile uses for this very job once it settles
        // as 失敗 (renderTileStatus's failed-tile branch) -- one failure, one
        // presentation, whether the user hears about it here or comes back to
        // it later. #error-msg is still the right home for the upload page's
        // own inline validation ("請先上傳一張照片。"), which sits next to the
        // button it is about; a job that died minutes after being submitted
        // is not that.
        showNoticeModal(friendlyError(error));
        // Back to the upload screen -- unlike a successful finish (which now
        // leaves analysis-preview open by default beside the results canvas),
        // there's no results view here for it to belong to.
        if (els.analysisPreview) els.analysisPreview.hidden = true;
      }
      // Whichever branch ran, the rotation itself is dropped by the finally
      // below -- fast-forwarded above when there's still a processing screen
      // to play it out on, instantly otherwise.
    } finally {
      els.startButton.disabled = false;
      if (els.startFullButton) els.startFullButton.disabled = false;
      // Only a safety net for an abandoned pending-review (e.g. an error
      // thrown while showPendingResults/waitForPendingResultsAction was still
      // in play) -- the normal "continue" path already calls this itself
      // before finishSingleFlow runs. Calling it unconditionally here would
      // also fire on every successful finish, undoing the revise panel that
      // finishSingleFlow's own tail (openInlineAnalysisForCurrentContext)
      // just legitimately opened -- moving #ap-options-card-inline back home
      // and re-hiding #download-link right after they were shown.
      //
      // Guarded by this run's own token for the same reason every render in
      // here is: pendingResultsActive is screen-wide state, so once the user
      // has navigated to a DIFFERENT record while this job was still running
      // (viewAnalyzeHistoryRecord on a 待美化 case, which sets the flag again
      // for ITS review panel), the flag no longer describes this run's
      // abandoned panel at all -- closing it here tore down that other
      // record's freshly-opened review panel the moment this job finished,
      // leaving its photo on screen with no analysis beside it. Nothing is
      // leaked by skipping it: whoever owns the screen now opened that panel
      // itself and closes it on its own exits.
      if (pendingResultsActive && historyViewToken === myHistoryToken) closePendingResults();
      // Safety net for the isCurrent()===false edge case, where
      // revealResultsIfNeeded/showPendingResults bail out without ever
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
  // 接著這張調整's 美化: one continue-version job on the chosen version, then
  // the case's versions re-read so the new one lands in 美化紀錄 like any
  // other. Runs inside backToProcessFromResults, whose finally reveals the
  // photo again and whose catch reports a failure -- the mode stays on then,
  // holding the request, so reopening the panel tries again from it.
  async function finishPanelContinuation(isCurrent) {
    const cont = activeContinuation();
    const request = continuationRequestFromDom();
    cont.prefill = request;
    const redoFrom = continuationRedoSource(cont);
    const { imageModel, imageQuality } = singleFlowState;
    markCurrentCaseModified();
    setAnalysisPreviewDisabled(true);
    procVisual.classList.remove("done");
    setProcTitle(tr("AI 正在美化"));
    setFlowStage("editing");
    startProcSubRotation(() => editSubMessages({ tipsOnly: true }));

    const jobId = await continueVersionJob({
      caseId: currentCaseHistoryImageKey,
      sourceResult: redoFrom || cont.source,
      userPrompt: request.text,
      styleOptions: request.style_options,
      aspectRatio: request.aspect_ratio,
      outputScale: singleFlowState.outputScale || els.apImageOutputScaleSelect?.value,
      imageQuality,
      imageModel,
      user: getCurrentUser(),
      actionId: generateActionId(),
      analysisJobId: historyCaseAnalysisJobId,
    });
    await pollJobStatus(jobId, { path: "local", timeoutMs: editJobTimeoutMs({ aiPhotoEditing: true, runQa: false }) });
    const { image_url: imageUrl, result } = await getJobResult(jobId, { path: "local" });
    if (!isCurrent()) return;
    if (panelContinuation === cont) panelContinuation = null;
    // Untagged, so refreshVersionsAfterEdit rebuilds the case's version list
    // around it and lands on it.
    singleRuns = [{ run: 1, jobId, sourceRun: 1, status: "done", imageUrl, result, timings: [], error: null }];
    singleMirrorIndex = 0;
    try {
      await refreshVersionsAfterEdit();
    } catch (error) {
      console.warn("Could not re-read the case's versions", error);
    }
    if (!isCurrent()) return;
    const run = singleRuns[singleMirrorIndex];
    if (run?.versionId) {
      await loadContinuationBase(singleRuns, run);
      if (!isCurrent()) return;
      applyHistoryVersionContext(run, null);
    }
    await showSingleResult({ forceReopen: true });
  }

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
    if (activeContinuation()) return finishPanelContinuation(isCurrent);
    // This is the single choke point every "Run"/revise click submits
    // through, regardless of which history-view path set singleFlowState
    // (viewAnalyzeHistoryRecord already hints earlier for its own path, but
    // viewHistoryRecord/viewProcessingHistoryRecord/importAsSingle don't) --
    // so it's also where a resumed old analysis gets caught for all of them
    // alike, before wasting a submit continue_job would reject anyway.
    // useCombinedJob has no analysis yet at this point (a fresh analyze,
    // always compatible) -- nothing to check.
    if (!singleFlowState.useCombinedJob && !isCompatible(singleFlowState.analysis)) {
      // Keep the history tile in sync with the read-only state shown here.
      // This is reached for completed history cases, whose compatibility is
      // checked from the restored result rather than /analyze/result.
      markHistoryCaseArchived();
      showArchivedAnalysisNotice();
      return;
    }
    // This is the common entry point for the first edit and every later
    // revise. Analysis-only views intentionally never set modifiedAt.
    markCurrentCaseModified();
    const { analysis, analyzeJobId, presetKey, preset, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, advancedOptions, processingPath, analyzeTimings, useCombinedJob, combinedFromJobId } = singleFlowState;
    // Read the live checkbox at submission time. The result-page control can
    // change after singleFlowState was created during analysis; the visible
    // selection, not that earlier snapshot, is authoritative for this edit.
    const singlePassEdit = readSinglePassEditOption();
    singleFlowState.singlePassEdit = singlePassEdit;
    const selections = readStyleSelectionsFromDom();
    const faceIssueOverrides = getFaceIssueOverrides();
    const bodyReshapeReviewEnabled = getBodyReshapeReviewEnabled();
    const userPrompt = els.promptInput.value.trim();
    const overwritePrompt = els.promptOverwriteToggle ? els.promptOverwriteToggle.checked : false;
    lastSingleSelections = { userPrompt, overwritePrompt, ...selections, faceIssueOverrides, bodyReshapeEnabled: bodyReshapeReviewEnabled };
    // No analysis yet in combined mode -- nothing to fold recipe/style
    // adjustments into, since the combined job computes its own from scratch.
    const editAnalysis = useCombinedJob ? null : analysis;
    const editSelections = useCombinedJob ? null : buildEditSelections(
      { ...selections, advancedOptions, faceIssueOverrides, bodyReshapeEnabled: bodyReshapeReviewEnabled });
    // Except on a 「只照需求改」 case (combinedFromJobId): its panel already
    // offered the styles, so those go along for the worker to apply onto the
    // analysis it makes. Only what that panel shows -- no recipes, faces or
    // body to pick there yet.
    const combinedSelections = combinedFromJobId
      ? buildEditSelections({ options: selections.options, advancedOptions })
      : null;
    const editTipsOnly = useCombinedJob || singleFlowState.progressTipsOnly === true;
    // Consume the initial one-click policy; later user-reviewed edits show
    // their own submitted choices, including after an AIPoC one-click run.
    singleFlowState.progressTipsOnly = false;
    const runCount = Math.max(1, parseInt(els.apRunCountSelect?.value, 10) || 1);
    // Declared here (not inside either branch below) so every edit-stage
    // rotation closure -- the initial one right below, onCombinedStage, and
    // both updateRunProgress functions further down -- shares this single
    // live binding instead of each closing over its own frozen snapshot.
    let doneCount = 0;

    setAnalysisPreviewDisabled(true);
    procVisual.classList.remove("done");
    setProcTitle(useCombinedJob ? tr("正在分析照片") : (aiPhotoEditing ? tr("AI 正在美化") : tr("正在處理照片")));
    // Same split the title above already makes, so the two can't drift: a
    // combined job (一鍵修圖 on the local path) does its OWN analysis inside
    // this one job and is still analyzing at this point -- onCombinedStage
    // below moves it on when the backend says the edit has actually started.
    // Every other path arrives here with analysis already finished, so the
    // edit is what's starting right now.
    setFlowStage(useCombinedJob ? "analyzing" : "editing");
    if (useCombinedJob) {
      startProcSubRotation(analyzeSubMessages);
    } else {
      startProcSubRotation(() => editSubMessages({ aiPhotoEditing, doneCount, runCount,
        analysis: editAnalysis, selections: editSelections, userPrompt, overwritePrompt, tipsOnly: editTipsOnly }));
    }
    // Live progress messaging for a combined job, driven by the backend's
    // reported stage (job_processor.py's set_job_info(..., results={"stage": ...})
    // calls) -- keeps the same two-phase "分析中 -> 修圖中" wording the old
    // two-job flow showed, even though it's now one job/one poll.
    const onCombinedStage = (stage) => {
      if (stage !== "edit") return;
      // Same isCurrent guard revealResultsIfNeeded below uses, for the same
      // reason: this fires from a poll that keeps running after the user may
      // have moved on, and both writes below are to screen-wide state (the
      // processing screen's title, and the shared step indicator) that would
      // otherwise describe THIS job on whatever the user is now looking at.
      if (!isCurrent()) return;
      setProcTitle(aiPhotoEditing ? tr("AI 正在美化") : tr("正在處理照片"));
      // The backend reporting its analyze->edit switch is the only honest
      // moment to move the indicator on for a combined job: there's no
      // client-side transition to hang it off, and anything earlier (e.g.
      // the top of this function's useCombinedJob branch) would claim 修圖
      // while the job is demonstrably still analyzing.
      setFlowStage("editing");
      startProcSubRotation(() => editSubMessages({ tipsOnly: true }));
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
      updateCostBtn();
      showSection("results");
      // Same reasoning as showPendingResults' own closeHistoryDrawer call --
      // the drawer is a full-viewport overlay independent of this section, so
      // it would otherwise hide this reveal if the user opened it while the
      // edit was still running in the background. No-op if already closed.
      closeHistoryDrawer();
      setFlowStage("done");
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
          file: selectedFile, fromJobId: combinedFromJobId || null, selections: combinedSelections,
          preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, advancedOptions, userPrompt, overwritePrompt, runCount,
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
              { label: tr("分析並美化"), duration: Date.now() - stepStart, children: [
                { label: tr("Step 2：照片分析"), duration: secToMs(t["Step 2 - photo_analysis"]) },
                { label: tr("Step 3：使用者補充指令"), duration: secToMs(t["Step 3 - user_instruction_review"]) },
                { label: tr("Step 5：美化執行"), duration: secToMs(t["Step 5 - image_edit"]) },
                { label: tr("Step 5b：眼部修正"), duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
                { label: tr("Step 6：品質檢查"), duration: secToMs(t["Step 6 - quality_check"]) }
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

      // A 「只照需求改」 case's combined 美化 is one go, done or not: a failed
      // one leaves the case as it was, still skipped, and the next 美化 asks
      // again (onContinue) instead of re-running this one unasked.
      if (combinedFromJobId && isCurrent()) {
        singleFlowState.useCombinedJob = false;
        singleFlowState.combinedFromJobId = null;
      }
      if (!myRuns.some((r) => r.status === "done")) {
        throw new Error(myRuns.find((r) => r.error)?.error || tr("全部處理失敗，請稍後再試。"));
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
        // The case's analysis from now on, as adoptSkippedCaseAnalysis sets
        // it: the job is attached to the case (api.analyze_and_finish).
        if (combinedFromJobId) {
          historyCaseAnalysis = doneRun.result.analysis;
          historyCaseAnalysisJobId = doneRun.jobId;
          setCurrentCaseAnalysisVersion(doneRun.result.analysis);
        }
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
        finalJobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, userPrompt, overwritePrompt, analysis: editAnalysis, selections: editSelections, user: getCurrentUser(), path: "aipoc" });
      } else {
        finalJobId = await submitFinishJob(analyzeJobId, selectedFile, { analysis: editAnalysis, selections: editSelections, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, userPrompt, overwritePrompt, user: getCurrentUser() });
      }
      // Timings go into one shared list (dom.js) that belongs to whichever
      // record is on screen -- once the user has moved to another record,
      // this job's entries would show up in THAT record's 處理時間.
      if (isCurrent()) addTiming(tr("提交後續處理"), Date.now() - stepStart);

      stepStart = Date.now();
      await pollJobStatus(finalJobId, { path: processingPath });
      const finishWaitDuration = Date.now() - stepStart;

      stepStart = Date.now();
      ({ image_url: imageUrl, result } = await getJobResult(finalJobId, { path: processingPath }));
      const t = (result["extra-info"] || {}).timings || {};
      if (isCurrent()) addTiming(tr("AI 美化中"), finishWaitDuration, [
        { label: tr("Step 3：使用者補充指令"), duration: secToMs(t["Step 3 - user_instruction_review"]) },
        { label: tr("Step 5：美化執行"), duration: secToMs(t["Step 5 - image_edit"]) },
        { label: tr("Step 5b：眼部修正"), duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
        { label: tr("Step 6：品質檢查"), duration: secToMs(t["Step 6 - quality_check"]) }
      ]);
      if (isCurrent()) addTiming(tr("取得結果"), Date.now() - stepStart);

      const extraInfo = result["extra-info"] || {};
      const usedImageModelLabel = extraInfo.image_model
        ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
        : tr("本機處理");
      const usedAnalysisModelLabel = extraInfo.analysis_model
        ? (window.PhotoAgentConfig.analysisModels[extraInfo.analysis_model] || extraInfo.analysis_model)
        : window.PhotoAgentConfig.analysisModel;
      const usedModelLabel = isDebugUiActive
      ? `${usedAnalysisModelLabel} + ${usedImageModelLabel}`
      : tr("AI 智能美化");

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
        await runSingleItem(run, { analyzeJobId, editAnalysis, editSelections, userPrompt, overwritePrompt, presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, processingPath, analyzeTimings, actionId });
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
        throw new Error(myRuns.find((r) => r.error)?.error || tr("全部處理失敗，請稍後再試。"));
      }
      if (isCurrent()) await showSingleResult();
    }

    revealResultsIfNeeded();
    // Last, and only if this is still the record on screen: the strip it
    // restores describes THIS case, and the fetch inside it gives the user
    // time to navigate away.
    if (isCurrent()) await refreshVersionsAfterEdit();
  }

  // Mirrors the backend's own per-request HTTP timeouts (openai_client.py /
  // gemini_client.py: Step 3 review and Step 6 QA use a 120s socket timeout,
  // Step 5 edit and Step 5b gaze repair use 300s) so a single-RUN poll
  // budget scales with which steps a job's own flags will actually execute,
  // instead of one flat number that's too tight when every optional step
  // fires and wastefully loose when none do.
  const STEP_TIMEOUT_MS = { instructionReview: 120000, imageEdit: 300000, gazeRepair: 300000, qualityCheck: 120000 };
  function editJobTimeoutMs({ aiPhotoEditing, runQa, runCount = 1 }) {
    let perRunMs = STEP_TIMEOUT_MS.instructionReview;
    if (aiPhotoEditing) {
      perRunMs += STEP_TIMEOUT_MS.imageEdit + STEP_TIMEOUT_MS.gazeRepair;
      if (runQa) perRunMs += STEP_TIMEOUT_MS.qualityCheck;
    }
    return perRunMs * runCount;
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

  async function runSingleItem(run, { analyzeJobId, editAnalysis, editSelections, userPrompt, overwritePrompt, presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, processingPath, analyzeTimings, actionId }) {
    try {
      let stepStart = Date.now();

      if (processingPath === "aipoc") {
        run.jobId = await submitJob({ file: selectedFile, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, userPrompt, overwritePrompt, analysis: editAnalysis, selections: editSelections, user: getCurrentUser(), actionId, path: "aipoc" });
      } else {
        run.jobId = await submitFinishJob(analyzeJobId, selectedFile, { analysis: editAnalysis, selections: editSelections, preset: presetKey, imageQuality, imageModel, analysisModel, aspectRatio, outputScale, aiPhotoEditing, runQa, singlePassEdit, userPrompt, overwritePrompt, user: getCurrentUser(), actionId });
      }
      const submitMs = Date.now() - stepStart;

      stepStart = Date.now();
      await pollJobStatus(run.jobId, { path: processingPath, timeoutMs: editJobTimeoutMs({ aiPhotoEditing, runQa }) });
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
        { label: tr("提交後續處理"), duration: submitMs },
        { label: tr("AI 美化中"), duration: pollMs, children: [
          { label: tr("Step 3：使用者補充指令"), duration: secToMs(t["Step 3 - user_instruction_review"]) },
          { label: tr("Step 5：美化執行"), duration: secToMs(t["Step 5 - image_edit"]) },
          { label: tr("Step 5b：眼部修正"), duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
          { label: tr("Step 6：品質檢查"), duration: secToMs(t["Step 6 - quality_check"]) }
        ] },
        { label: tr("取得結果"), duration: fetchMs }
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
    setFlowStage("done"); // covers both a live RUN-tab switch and viewHistoryRecord jumping straight here for a finished case
    // revealDetailExtra: true only for viewHistoryRecord's admin-reports-drawer
    // call -- shows the prompts button for that one record regardless of
    // debug mode, same override resultsDetailExtra itself already gets.
    if (els.showPromptBtn) els.showPromptBtn.hidden = !(isDebugUiActive || revealDetailExtra);

    const { preset, aiPhotoEditing } = singleFlowState;
    const result = pick.result;
    const extraInfo = result["extra-info"] || {};
    const usedImageModelLabel = extraInfo.image_model
      ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
      : tr("本機處理");
    const usedAnalysisModelLabel = extraInfo.analysis_model
      ? (window.PhotoAgentConfig.analysisModels[extraInfo.analysis_model] || extraInfo.analysis_model)
      : window.PhotoAgentConfig.analysisModel;
    const usedModelLabel = isDebugUiActive
      ? `${usedAnalysisModelLabel} + ${usedImageModelLabel}`
      : tr("AI 智能美化");

    const data = {
      image: pick.imageUrl,
      model: usedModelLabel,
      usage: result.token_usage,
      qualityCheck: result.quality_check,
      localOnly: !aiPhotoEditing,
      retryCount: 0
    };

    // "下載全部" and the N-images badge have always meant the N images of
    // ONE edit. singleRuns can now span a whole case (fetchHistoryVersionRuns),
    // and bundling every version into one zip is a different feature with a
    // different folder layout -- until that exists, both stay scoped to the
    // version being looked at. A live revise's runs carry no versionId and
    // are unaffected.
    const versionRuns = pick.versionId ? runs.filter((r) => r.versionId === pick.versionId) : runs;
    lastResult = { file: selectedFile, sourceUrl: selectedFileSourceUrl, resultText: result, jobId: pick.jobId, runNumber: pick.run, sourceRun: pick.sourceRun, path: singleFlowState.processingPath, runs: versionRuns };
    // currentTokenUsage() reads lastResult -- must run after it's (re)assigned
    // above, or this shows whatever record was last displayed (or nothing).
    updateCostBtn();
    updateDownloadLinks(versionRuns);
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
    // Snapshot of the token as of entry, for the failure report at the tail --
    // the record this panel belongs to is whatever put it on screen, and it
    // stops being ours the moment the user opens a different one.
    const myHistoryToken = historyViewToken;
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

    // A case resumed from history whose analysis no longer meets
    // compatibility_version.py's floor (backend/services/compatibility.py)
    // opens read-only -- same reasoning/mechanism as viewHistoryRecord's
    // AIPoC branch (showBatchDoneMain's reviewable:false): finishSingleFlow
    // would reject it anyway (isCompatible, same rule as continue_job's own
    // check), so there is nothing here for Run/any picker to legitimately do.
    // Computed and applied BEFORE renderAnalysisPreview/renderFaceReviewCards
    // below -- those rebuild this case's own recipe/face-review checkboxes
    // (results.js), which read the lock class at creation time, not just
    // once afterward via openInlineAnalysisPanel further down. Setting it
    // only there would leak the PREVIOUS case's lock state into this case's
    // initial build (and leave this case's own lock one case late).
    const readOnly = !isCompatible(singleFlowState.analysis);
    if (readOnly) markHistoryCaseArchived();
    setAnalysisPreviewDisabled(readOnly);
    if (!readOnly) ensurePanelBase();
    const continuing = readOnly ? null : activeContinuation();
    // On 原圖 off a continuation version: the analysis it traces back to,
    // in full (人像檢查/畫面建議 too). Without one, request-only as before.
    // Guard origin itself: with no origin stash and no panel run (single-RUN
    // 美化 off 待美化 nulls singleRuns), undefined === undefined would match.
    const originStash = singleFlowState.origin;
    const origin = !continuing && originStash && originStash.run === panelRun() ? originStash.selections : null;
    const selections = origin || lastSingleSelections;
    if (!continuing && apRatioShowsContinuation) {
      // Back from 接著這張調整, whose shape the picker was showing.
      apRatioShowsContinuation = false;
      applyAspectRatioOption(findAspectRatioOption(singleFlowState.aspectRatio || "auto"), { fromUser: true });
    }
    renderAnalysisPreview({ analysis: singleFlowState.analysis, preset: singleFlowState.preset, expanded: true,
      requestOnly: !!continuing || (!origin && isContinuationRun(panelRun())),
      versionCard: overviewOnVersion() });
    paintSkipHint();
    // Paint this record's global 人像修正設定 independently before restoring any
    // per-person choices. Pose below changes only the latter.
    applyAdvancedOptionsOption(
      recordAdvancedOptions(singleFlowState.advancedOptions, singleFlowState.analysis), "ap");
    if (continuing) {
      // The request only: the version's other settings are already in it.
      // On its default base that is what the version was made with
      // (ensurePanelBase), otherwise this round's.
      const { text = "", style_options: style = {}, aspect_ratio: ratio } = continuing.prefill || {};
      if (els.promptInput) els.promptInput.value = text;
      applyAspectRatioOption(findAspectRatioOption(continuationAspectRatio(ratio)), { fromUser: true });
      apRatioShowsContinuation = true;
      if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = false;
      applyStyleSelections({ options: Object.fromEntries(
        ["pose", "angle", "lighting", "size"].map((k) => [k, style[k] || "default"])), recipeChecked: null });
      if (els.apAnalysisShellDesc) {
        els.apAnalysisShellDesc.textContent = tr("只改你要求的地方，其他保持第 {n} 版的樣子。", { n: continuing.versionNumber });
      }
    } else if (selections) {
      if (els.promptInput) els.promptInput.value = selections.userPrompt || "";
      if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = !!selections.overwritePrompt;
      applyFaceIssueOverrides(selections.faceIssueOverrides);
      if (typeof selections.bodyReshapeEnabled === "boolean") {
        setBodyReshapeReviewEnabled(selections.bodyReshapeEnabled);
      }
      // The face review cards were already built (renderAnalysisPreview just
      // above) before these overrides existed on faceThumbEntries -- rebuild
      // them so the toggles on screen reflect the restored picks instead of
      // each issue's plain default state.
      renderFaceReviewCards();
      // Apply Pose last: it is a recommendation over this photo's restored
      // individual values, never an edit to the global 人像修正設定 above.
      applyStyleSelections(selections);
    }
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = !isDebugUiActive;
    openInlineAnalysisPanel(readOnly);
    if (readOnly) {
      // Shown once the (already read-only-locked) screen has actually
      // finished loading in, not before -- the lock itself doesn't announce
      // itself otherwise, and a dimmed panel alone is easy to miss as "why
      // can't I click anything" rather than "this case is outdated."
      showArchivedAnalysisNotice();
      return; // no Run/Cancel controls exist to wait for in this mode
    }

    captureInlineReviseSnapshot();
    // After the snapshot: what the chat page changed counts as a change here
    // too, so 美化 is not blocked as if nothing had been touched.
    if (!continuing) {
      // Not while 從第 N 版美化: the fields then hold that request, and a
      // baseline taken off them would make the same request, carried back to
      // 從原圖美化, read as nothing changed (no 調整中 tile).
      markReviewBaseline();
      applyPendingModeHandoff();
      applyOriginStash();
      applyContinuationStash();
    }
    updateInlineAnalysisContinueBtnState();

    const action = await waitForInlineAnalysisAction(undefined, () => (
      continuing && activeContinuation() === continuing
        && isEmptyContinuationRequest(continuationRequestFromDom()) && !continuationEngineChanged(continuing)
        ? tr("先寫下想調整的地方，或挑一個風格。") : null));
    if (action !== "cancel") {
      reviewBaseline = null; // sent with this 美化
      panelContinuationStash = null;
      panelOriginStash = null;
    }
    // Closing straight into #canvas-loading (below) rather than back onto
    // the photo -- skip .results-canvas-row's panel-close animation (styles.
    // css) here, since there's no photo on screen for it to slide/grow
    // against. The cancel branch below stays on the photo, so it keeps the
    // animation; removed again (finally block below) once canvas-loading is
    // actually showing, so a later, normal panel toggle still animates.
    if (action !== "cancel" && els.resultsCanvasRow) els.resultsCanvasRow.classList.add("no-transition");
    if (action === "cancel" && continuing) {
      // Still 接著這張調整 next time the panel opens, with what was typed.
      continuing.prefill = continuationRequestFromDom();
      closeInlineAnalysisPanel();
      return;
    }
    closeInlineAnalysisPanel();
    if (action === "cancel") {
      // Preserve unsent local review edits for the next time the panel is
      // opened; this does not submit a request.
      const currentSelections = readStyleSelectionsFromDom();
      lastSingleSelections = {
        userPrompt: els.promptInput ? els.promptInput.value.trim() : "",
        overwritePrompt: els.promptOverwriteToggle ? els.promptOverwriteToggle.checked : false,
        ...currentSelections,
        advancedOptions: readAdvancedOptionsFromDom("ap"),
        faceIssueOverrides: getFaceIssueOverrides(),
        bodyReshapeEnabled: getBodyReshapeReviewEnabled()
      };
      return;
    }
    setFlowStage("editing"); // finishSingleFlow below moves to "done" once it finishes

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
    // The strip describes results, and there is no result on screen for the
    // whole of this wait -- leaving it up next to the processing animation
    // invites a click that would land on a version the edit in flight is
    // about to make stale. refreshVersionsAfterEdit brings it back, with the
    // new version in it.
    hideVersionStrip();
    if (els.canvasLoading) els.canvasLoading.hidden = false;
    if (els.resultsCanvasRow) {
      // Two rAFs: the first lets the browser commit the instant
      // (transition-suppressed) layout the panel close + the hidden flips
      // above just produced; only then is it safe to remove "no-transition"
      // without a later, real panel toggle picking up this same instant
      // jump instead of its own animation.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => els.resultsCanvasRow.classList.remove("no-transition"));
      });
    }
    let failure = null;
    try {
      await finishSingleFlow();
    } catch (error) {
      // Held, not reported here: the finally below is what restores the photo
      // frame and settles the rotation, so reporting before it ran would put
      // the message over a loading card that is about to be torn down anyway.
      failure = error;
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
      // Only while this record still owns the screen: #canvas-loading, the
      // photo frame and the rotating status text are shared by every record.
      // Once the user has opened another one (and possibly started ITS
      // revise), tearing these down here hid that record's own loading card
      // and froze its status text while its edit was still running.
      if (historyViewToken === myHistoryToken) {
        await finishProcSubRotation();
        if (els.canvasLoading) els.canvasLoading.hidden = true;
        if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
        if (els.showPromptBtn) els.showPromptBtn.disabled = false;
        stopProcSubRotation();
      }
    }
    if (!failure) return;
    // This one reported through the notice modal rather than showError:
    // #error-msg lives inside #upload-section (index.html), so on this screen
    // it wrote the reason into a hidden element and the revise simply looked
    // like it had done nothing. The modal is also what this same job's
    // history tile shows once it settles as 失敗, so the two agree. The frame
    // restored by the finally above is the previous, still-valid result --
    // dismissing this leaves the user on it, free to reopen the panel and try
    // again.
    if (historyViewToken !== myHistoryToken) return; // another record owns the screen now; its tile reports this
    setFlowStage("done"); // back on the previous result, nothing running
    showNoticeModal(friendlyError(failure));
  }

  // ── Batch step 1: analyze every image, then show the review grid ─────────
  async function startSubmitBatch(skipReview = false) {
    // Same reason as startSubmit's own bump (which has already bumped by the
    // time it delegates here) -- kept so this stays safe to call directly.
    historyViewToken += 1;
    const presetKey = "natural";
    const modelQualityOpt = findModelQualityOption(els.imageModelQualitySelect?.value) || {};
    batchSettings = {
      presetKey,
      preset: presets[presetKey] || presets.natural,
      // Prompt is entered per-image (or batch-level) at step 2, not at upload.
      imageQuality: modelQualityOpt.quality || "medium",
      imageModel: modelQualityOpt.model || "gpt-image-2",
      analysisModel: els.analysisModelSelect?.value || window.PhotoAgentConfig.defaultAnalysisModel || "gpt-5.6-luna",
      aspectRatio: els.imageAspectRatioSelect?.value || window.PhotoAgentConfig.defaultAspectRatio || "auto",
      outputScale: els.imageOutputScaleSelect?.value || window.PhotoAgentConfig.defaultOutputScale || "2K",
      aiPhotoEditing: els.aiEditToggle.checked,
      runQa: els.runQaToggle ? els.runQaToggle.checked : false,
      singlePassEdit: readSinglePassEditOption(),
      advancedOptions: readAdvancedOptionsFromDom("upload"),
      path: els.processingPathSelect?.value || "local"
    };

    els.startButton.disabled = true;
    if (els.startFullButton) els.startFullButton.disabled = true;
    resetSteps(); // clears completedTimings so each run's total starts fresh
    const uploadedAt = Date.now() / 1000;
    batchRecords.forEach((r) => {
      if (!r.uploadedAt) r.uploadedAt = uploadedAt;
      r.status = "analyzing";
      r.progressTipsOnly = skipReview;
      // 分析模型 is settled here and only here: the analyze submission below
      // sends batchSettings.analysisModel for the whole batch, and the
      // results page's own #ap-analysis-model is read-only (changing it there
      // would not re-analyze). Stamping it now means each case reports the
      // model it was analyzed with even before it has been edited.
      r.analysisModel = batchSettings.analysisModel;
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
      outputScale: batchSettings.outputScale,
      aiPhotoEditing: batchSettings.aiPhotoEditing,
      advancedOptions: batchSettings.advancedOptions,
      user: getCurrentUser()
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
            r.analyzeTimings = [{ label: tr("初步分析"), duration: secToMs(t["Step 2 - photo_analysis"]) }];
            r.status = "analyzed";
          } else {
            r.status = "error";
            r.error = (entry && entry.error) || tr("分析失敗");
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
            // Stamped at submission, not when analyzeBatchItem's poll loop
            // gets a free slot (POLL_CONCURRENCY may lag behind how many
            // jobs the backend already started) -- otherwise "分析中"'s
            // wall-clock duration can come in shorter than its own "Step 2：
            // 照片分析" child, which reads straight from the backend's own
            // (always-accurate) timings dict.
            r.analyzeSubmittedAt = Date.now();
          } else {
            r.status = "error";
            r.error = (entry && entry.error) || tr("送出分析失敗");
          }
          updateBatchSidebarItem(r);
          refreshIfSelected(r);
        });
        const analyzing = batchRecords.filter((r) => r.status === "analyzing" && r.analyzeJobId);
        await runBatchPool(analyzing, analyzeBatchItem, POLL_CONCURRENCY);
      }

      updateBatchDownloadAllVisibility();
      if (skipReview) {
        // Straight through, no stop: same as clicking "Run all" myself,
        // so every analyzed image goes straight to editing with its default
        // recipe/style options (no per-image review ever happened to set
        // overrides). Records that failed to analyze are simply excluded --
        // continueBatchRecords already filters to status === "analyzed".
        await continueBatchRecords(analyzedBatchRecords(), { tipsOnly: true });
      } else {
        updateBatchHeaderControls();
      }
    } finally {
      els.startButton.disabled = false;
      if (els.startFullButton) els.startFullButton.disabled = false;
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
    const runCount = Math.max(1, parseInt(els.imageRunCountSelect?.value, 10) || 1);
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
    record.progressTipsOnly = true;
    const userPrompt = (record.userPrompt || "").trim();
    const overwritePrompt = userPrompt ? !!record.overwritePrompt : false;
    const stepStart = Date.now();
    if (!record.uploadedAt) record.uploadedAt = stepStart / 1000;
    record.modifiedAt = stepStart / 1000;
    // Same freeze as editBatchItem's -- batch-auto analyzes and edits inside
    // one job, so this is where this case's parameters are settled. Without it
    // a later per-case revise (which reads effective*(record)) would start from
    // whatever batchSettings holds by then rather than from what actually ran.
    record.imageModel = batchSettings.imageModel;
    record.imageQuality = batchSettings.imageQuality;
    record.analysisModel = batchSettings.analysisModel;
    record.aspectRatio = batchSettings.aspectRatio;
    record.outputScale = batchSettings.outputScale;
    record.advancedOptions = { ...batchSettings.advancedOptions };
    record.singlePassEdit = batchSettings.singlePassEdit;
    try {
      await runCombinedMultiRun({
        file: record.file,
        preset: batchSettings.presetKey,
        imageQuality: batchSettings.imageQuality,
        imageModel: batchSettings.imageModel,
        analysisModel: batchSettings.analysisModel,
        aspectRatio: batchSettings.aspectRatio,
        outputScale: batchSettings.outputScale,
        aiPhotoEditing: batchSettings.aiPhotoEditing,
        runQa: batchSettings.runQa,
        singlePassEdit: batchSettings.singlePassEdit,
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
            { label: tr("分析並美化"), duration: Date.now() - stepStart, children: [
              { label: tr("Step 2：照片分析"), duration: secToMs(t["Step 2 - photo_analysis"]) },
              { label: tr("Step 3：使用者補充指令"), duration: secToMs(t["Step 3 - user_instruction_review"]) },
              { label: tr("Step 5：美化執行"), duration: secToMs(t["Step 5 - image_edit"]) },
              { label: tr("Step 5b：眼部修正"), duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
              { label: tr("Step 6：品質檢查"), duration: secToMs(t["Step 6 - quality_check"]) }
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
      const stepStart = record.analyzeSubmittedAt || Date.now();
      await pollJobStatus(record.analyzeJobId, { path: "local" });
      const analyzeResult = await getAnalyzeResult(record.analyzeJobId);
      record.analysis = analyzeResult.result.analysis;
      const t = (record.analysis && record.analysis.timings) || {};
      record.analyzeTimings = [{ label: tr("分析中"), duration: Date.now() - stepStart, children: [
        { label: tr("Step 2：照片分析"), duration: secToMs(t["Step 2 - photo_analysis"]) }
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

  // Shows/hides the bulk "Run all" button, based on whether there's at
  // least one record still awaiting its first edit -- live-updated as
  // analyses/edits complete, not just once at the end of a whole-batch phase.
  // The label says how many will go when some are already edited, since
  // only the not-yet-edited ones are sent.
  function updateBatchHeaderControls() {
    const pending = analyzedBatchRecords().length;
    els.batchContinueAllBtn.hidden = !pending;
    if (els.batchContinueAllLabel) {
      els.batchContinueAllLabel.textContent =
        pending === batchRecords.length ? tr("全部美化") : tr("美化其餘 {pending} 張", { pending });
    }
  }

  // Effective per-image style options: each category just falls back to
  // "default" when the image's own review never set it.
  function effectiveOptions(record) {
    const own = record.options || {};
    const merged = {};
    ["pose", "angle", "lighting", "size"].forEach((key) => {
      merged[key] = own[key] || "default";
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

  function effectiveOutputScale(record) {
    return record.outputScale || batchSettings.outputScale;
  }

  function effectiveAdvancedOptions(record) {
    return record.advancedOptions || batchSettings.advancedOptions;
  }

  // Run step 2 (edit) for the given analyzed records via the same single-image
  // endpoints, so each image carries its own prompt -- flow identical to single.
  async function continueBatchRecords(records, { tipsOnly = false } = {}) {
    const targets = records.filter((r) => r.status === "analyzed");
    if (!targets.length) return;

    // N independent edit runs per image from the same analysis. Each run is
    // its own job; the pool works through (record, run) pairs.
    const runCount = Math.max(1, parseInt(els.imageRunCountSelect?.value, 10) || 1);
    const modifiedAt = Date.now() / 1000;
    targets.forEach((r) => {
      if (!r.uploadedAt) r.uploadedAt = modifiedAt;
      r.modifiedAt = modifiedAt;
      r.runs = Array.from({ length: runCount }, (_, k) => ({
        run: k + 1, jobId: null, status: "processing",
        imageUrl: null, result: null, timings: [], error: null
      }));
      r.mirrorIndex = null; // auto: mirror follows the first finished run
    });

    targets.forEach((r) => {
      r.progressTipsOnly = tipsOnly;
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
    // Same close-directly-not-via-cancel-click reasoning as selectBatchRecord:
    // if the currently selected "analyzed" record's own inline Run panel is
    // open (auto-opened by showBatchAnalyzedMain), its still-pending
    // waitForInlineAnalysisAction() promise would otherwise race against
    // continueBatchRecords already marking this same record "processing" and
    // submitting it below -- close it out first so every not-yet-edited case
    // (including whichever one is currently open) goes through this one path.
    if (els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis")) {
      if (currentBatchRecord) captureBatchReviewSelections(currentBatchRecord);
      clearInlineAnalysisWait();
      closeInlineAnalysisPanel();
    }
    // Belt-and-suspenders alongside updateBatchHeaderControls() hiding this
    // button once no "analyzed" record is left: if it's ever clicked with
    // nothing eligible (a stale render, a race with a per-item run), say so
    // instead of continueBatchRecords's own silent empty-targets return.
    const targets = analyzedBatchRecords();
    if (!targets.length) {
      showNoticeModal(tr("目前沒有可批次執行的案例。"));
      return;
    }
    continueBatchRecords(targets);
  }

  // Step 2 for one (record, run) pair: submit via the single-image
  // endpoint (so it carries the image's own prompt) -> poll ->
  // fetch. Records analyze + edit sub-timings, matching the single flow's
  // nested timing display. Results land on the run entry `run`;
  // syncRecordMirror keeps the record's legacy fields pointing at the
  // selected/first-done run so all existing readers stay unchanged.
  async function editBatchItem({ record, run, actionId }) {
    try {
      const userPrompt = (record.userPrompt || "").trim();
      const overwritePrompt = userPrompt ? !!record.overwritePrompt : false;
      const advancedOptions = effectiveAdvancedOptions(record);
      const editSelections = buildEditSelections({
        options: effectiveOptions(record),
        recipeChecked: record.recipeChecked,
        advancedOptions,
        faceIssueOverrides: record.faceIssueOverrides,
        bodyReshapeEnabled: record.bodyReshapeEnabled
      });
      const modelQuality = effectiveModelQuality(record);
      const analysisModel = effectiveAnalysisModel(record);
      const aspectRatio = effectiveAspectRatio(record);
      const outputScale = effectiveOutputScale(record);
      // Same live-value rule as finishSingleFlow: a per-image revise may
      // change the result-page toggle after batchSettings was first created.
      const singlePassEdit = readSinglePassEditOption();
      batchSettings.singlePassEdit = singlePassEdit;
      // Freeze onto this case what it is actually being submitted with. A case
      // that simply took the batch-level default otherwise left its own slots
      // null and re-read the live, batch-wide batchSettings every time its
      // panel was reopened or it was re-run -- so a pick made afterwards, from
      // another case's revise panel, retroactively changed both what this
      // finished case reported and what re-running it would use. Copied, not
      // aliased, so a later whole-batch change replaces nothing in place here
      // (mirrors pipeline.py stamping dict(advanced_options) onto the analysis).
      record.imageModel = modelQuality.model;
      record.imageQuality = modelQuality.quality;
      record.analysisModel = analysisModel;
      record.aspectRatio = aspectRatio;
      record.outputScale = outputScale;
      record.advancedOptions = { ...advancedOptions };
      record.singlePassEdit = singlePassEdit;
      let stepStart = Date.now();
      if (batchSettings.path === "aipoc") {
        run.jobId = await submitJob({
          file: record.file,
          preset: batchSettings.presetKey,
          imageQuality: modelQuality.quality,
          imageModel: modelQuality.model,
          analysisModel,
          aspectRatio,
          outputScale,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          runQa: batchSettings.runQa,
          singlePassEdit,
          userPrompt,
          overwritePrompt,
          analysis: record.analysis,
          selections: editSelections,
          user: getCurrentUser(),
          actionId,
          path: "aipoc"
        });
      } else {
        run.jobId = await submitFinishJob(record.analyzeJobId, record.file, {
          analysis: record.analysis,
          selections: editSelections,
          preset: batchSettings.presetKey,
          imageQuality: modelQuality.quality,
          imageModel: modelQuality.model,
          analysisModel,
          aspectRatio,
          outputScale,
          aiPhotoEditing: batchSettings.aiPhotoEditing,
          runQa: batchSettings.runQa,
          singlePassEdit,
          userPrompt,
          overwritePrompt,
          user: getCurrentUser(),
          actionId
        });
      }
      const submitMs = Date.now() - stepStart;

      stepStart = Date.now();
      await pollJobStatus(run.jobId, { path: batchSettings.path, timeoutMs: editJobTimeoutMs({ aiPhotoEditing: batchSettings.aiPhotoEditing, runQa: batchSettings.runQa }) });
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
        { label: tr("提交後續處理"), duration: submitMs },
        { label: tr("AI 美化中"), duration: pollMs, children: [
          { label: tr("Step 3：使用者補充指令"), duration: secToMs(t["Step 3 - user_instruction_review"]) },
          { label: tr("Step 5：美化執行"), duration: secToMs(t["Step 5 - image_edit"]) },
          { label: tr("Step 5b：眼部修正"), duration: secToMs(t["Step 5b - gaze_repair_pass"]) },
          { label: tr("Step 6：品質檢查"), duration: secToMs(t["Step 6 - quality_check"]) }
        ] },
        { label: tr("取得結果"), duration: fetchMs }
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

  // Batch counterpart of buildResultZip/buildAllRunsZip: bundles every
  // image's *every done RUN* into one zip, each image under its own
  // "image N/" folder (1-indexed by position in the batch) -- if an image
  // was revised with a higher RUN count, all of those RUNs are included, not
  // just the currently mirrored one, so the zip's file count actually
  // matches how many results were generated instead of silently dropping to
  // one per image. An image with no done RUN yet (still under analysis
  // review, or its edit is running/failed) falls back to just its original
  // source photo -- same reasoning as buildPendingSourceZip for the
  // single-image flow -- so "download all" has something to offer from the
  // analysis page onward instead of staying hidden until every image is
  // fully edited.
  function doneBatchRecordsWithResult() {
    return batchRecords.filter((r) => r.status === "done" && r.result);
  }

  // Same "fall back to a synthetic single run" shape as lastResultRuns(),
  // for a batch record instead of the single-image flow's lastResult. Only
  // safe to call on a record that's actually done (record.runs empty/absent
  // otherwise just means "no edit attempted yet", not "one implicit done
  // run") -- callers that iterate every batchRecords entry regardless of
  // status (buildAllBatchResultsZip) must check that themselves first.
  function batchRecordRuns(record) {
    if (Array.isArray(record.runs) && record.runs.length) return record.runs;
    return [{ run: 1, result: record.result, jobId: record.jobId, status: "done" }];
  }

  // done-only RUNs for one batch record, safe to call regardless of the
  // record's status -- a record with no runs array is only a real single
  // done RUN (batchRecordRuns' synthetic fallback) when it's actually done
  // with a result; otherwise (still pending/analyzing/processing/error) it
  // has none.
  function doneRunsForBatchRecord(record) {
    if (Array.isArray(record.runs) && record.runs.length) {
      // Newest edit only -- an older version stepped onto via the 美化紀錄
      // strip flips to "done" once loaded, and must not start riding along
      // in 下載全部 just because the user happened to look at it.
      return latestVersionRuns(record.runs).filter((run) => run.status === "done");
    }
    return (record.status === "done" && record.result) ? batchRecordRuns(record) : [];
  }

  // --debug-ui parity with buildPendingSourceZip's own source-only bundle:
  // an image with no done RUN yet still has whatever the analyze stage
  // already produced, so include that under this image's own prefix folder
  // instead of leaving debug mode's "download all" no more informative than
  // normal mode's.
  async function buildBatchSourceFiles(record, prefix, onItemDone) {
    const sourceBuf = await record.file.arrayBuffer();
    const files = [{ name: prefix + (record.file.name || "source.jpg"), data: new Uint8Array(sourceBuf) }];
    if (isDebugUiActive && record.analysis) {
      const analysisBytes = new TextEncoder().encode(JSON.stringify(record.analysis, null, 2));
      const promptsBytes = new TextEncoder().encode(buildPromptsJson(record.analysis.prompts || {}));
      files.push(
        { name: prefix + "analysis.json", data: analysisBytes },
        { name: prefix + "prompts.json", data: promptsBytes }
      );
    }
    onItemDone();
    return files;
  }

  async function buildAllBatchResultsZip(onProgress) {
    const runsPerRecord = batchRecords.map((r) => doneRunsForBatchRecord(r));
    // Aggregate across every image's RUNs (or the one source-photo fallback
    // "item" for an image with no done RUN yet), not just one image's --
    // each image's fetches run in parallel, so this is a shared counter
    // rather than a per-image local count.
    const total = runsPerRecord.reduce((sum, runs) => sum + (runs.length || 1), 0);
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
    const groups = await mapWithConcurrency(batchRecords, 3, (r, i) => {
      const prefix = `image ${i + 1}/`;
      const runs = runsPerRecord[i];
      return runs.length
        ? buildImageFiles({ file: r.file, path: batchSettings.path, prefix }, runs, tick)
        : buildBatchSourceFiles(r, prefix, tick);
    });
    return window.PhotoAgentZip.createZip(groups.flat());
  }

  function updateBatchDownloadAllVisibility() {
    if (!els.batchDownloadAllBtn) return;
    els.batchDownloadAllBtn.hidden = batchRecords.length === 0;
    if (batchRecords.length === 0) return;
    const doneCount = doneBatchRecordsWithResult().length;
    const hint = doneCount === 0
      ? tr("下載所有原圖（{length} 張）", { length: batchRecords.length })
      : doneCount === batchRecords.length
        ? tr("下載所有已完成的結果（{doneCount} 張）", { doneCount })
        : tr("下載全部（已完成 {doneCount} 張，其餘為原圖，共 {length} 張）", { doneCount, length: batchRecords.length });
    els.batchDownloadAllBtn.title = hint;
    els.batchDownloadAllBtn.setAttribute("aria-label", hint);
  }

  // 全部下載 for a batch listed from the chat page: same `image N/` layout as
  // buildAllBatchResultsZip, with the photo's current version as its result.
  async function buildCaseGroupZip(onProgress) {
    const photos = caseGroupSidebarPhotos;
    const total = photos.reduce((sum, p) => sum + (p.active_version?.job_id ? 2 : 1), 0);
    let done = 0;
    const tick = () => {
      done += 1;
      if (onProgress) onProgress(done, total);
    };
    const groups = await mapWithConcurrency(photos, 3, async (photo, i) => {
      const v = photo.active_version;
      const runs = v?.job_id ? [{ run: v.run || 1, jobId: v.job_id, status: "done" }] : [];
      const { sourceBuf, runData } = await fetchImageDownloadData(
        { sourceUrl: sourceImageUrl(photo.analyze_job_id), path: "local" }, runs, tick);
      tick();
      return buildFlatImageFiles({ prefix: `image ${i + 1}/` }, sourceBuf, runData);
    });
    return window.PhotoAgentZip.createZip(groups.flat());
  }

  function handleDownloadAllBatchClick() {
    if (caseGroupSidebarSession) {
      if (caseGroupSidebarPhotos.length) downloadZip(buildCaseGroupZip, buildDownloadZipFilename());
      return;
    }
    if (!batchRecords.length) return;
    downloadZip(buildAllBatchResultsZip, buildDownloadZipFilename());
  }

  function renderBatchSidebarList() {
    // A batch uploaded here takes the sidebar over from one listed from the
    // chat page (showCaseGroupSidebar).
    ++caseGroupSidebarToken;
    caseGroupSidebarSession = null;
    batchSidebarFilter = "all";
    if (els.batchDrawerTriggerCount) {
      els.batchDrawerTriggerCount.textContent = String(batchRecords.length);
      els.batchDrawerTriggerCount.hidden = batchRecords.length === 0;
    }
    els.batchSidebarList.innerHTML = "";
    batchRecords.forEach((record) => els.batchSidebarList.appendChild(buildBatchSidebarItem(record)));
    renderBatchSidebarFilters();
  }

  // ── 狀態篩選：列表上面那排 pill ──
  // 跟對話模式「我的照片」那欄（app.tsx 的 BatchPreSidebar／GRID_GROUPS）同樣的分組、字和順序；
  // 這頁一上傳就分析，沒有「未開始」。每個方塊帶 data-stage，這裡只數、只藏，
  // 點擊和逐張更新照舊。兩種以上狀態才出現；選的那組空了就回到全部。
  const SIDEBAR_STAGES = [
    { key: "error", label: "失敗" },
    { key: "analyzed", label: "待美化" },
    { key: "running", label: "處理中" },
    { key: "done", label: "美化完成" }
  ];
  function sidebarStage(statusKey, running = false) {
    if (statusKey === "error") return "error";
    if (running || statusKey === "analyzing" || statusKey === "processing") return "running";
    if (statusKey === "analyzed") return "analyzed";
    return "done"; // done, archived（可更新：已經美化過）
  }

  function renderBatchSidebarFilters() {
    const row = els.batchSidebarFilters;
    const list = els.batchSidebarList;
    if (!row || !list) return;
    const items = [...list.querySelectorAll(".batch-sidebar-item")];
    const counts = SIDEBAR_STAGES.map((g) => ({ g, n: items.filter((x) => x.dataset.stage === g.key).length })).filter((x) => x.n);
    if (!counts.some((x) => x.g.key === batchSidebarFilter)) batchSidebarFilter = "all";
    const pill = (key, text, cls) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `batch-grid-filter${cls ? ` ${cls}` : ""}${batchSidebarFilter === key ? " is-on" : ""}`;
      btn.setAttribute("aria-pressed", String(batchSidebarFilter === key));
      btn.textContent = text;
      btn.addEventListener("click", () => {
        batchSidebarFilter = key !== "all" && batchSidebarFilter === key ? "all" : key;
        renderBatchSidebarFilters();
      });
      return btn;
    };
    row.innerHTML = "";
    row.hidden = counts.length < 2;
    if (!row.hidden) {
      row.appendChild(pill("all", tr("全部 {n}", { n: items.length })));
      counts.forEach(({ g, n }) => row.appendChild(pill(g.key, `${tr(g.label)} ${n}`, `is-${g.key}`)));
    }
    items.forEach((x) => { x.hidden = batchSidebarFilter !== "all" && x.dataset.stage !== batchSidebarFilter; });
  }

  // Every status is clickable now (unlike the old grid, which only let you
  // click done/analyzed/error cards) -- selectBatchRecord dispatches to
  // whatever main-area view matches that record's current status.
  function buildBatchSidebarItem(record) {
    const item = document.createElement("div");
    item.className = `history-cube batch-sidebar-item ${record.status}${record === currentBatchRecord ? " selected" : ""}`;
    item.dataset.id = record.localId;

    const media = document.createElement("div");
    media.className = "rc-media";
    // Newest edit only (see latestVersionRuns) -- both for the n/N badge
    // below and for the thumbnail: stepping onto an older version in the
    // 美化紀錄 strip changes the main view, not what this photo currently is.
    const runs = latestVersionRuns(record.runs || []);
    const img = document.createElement("img");
    const latestDone = batchRecordOnOlderVersion(record) ? runs.find((x) => x.status === "done") : null;
    img.src = (latestDone && latestDone.imageUrl) || record.imageUrl || record.previewUrl;
    media.appendChild(img);
    const runsRunning = runs.filter((x) => x.status === "processing").length;
    item.dataset.stage = sidebarStage(record.status, runsRunning > 0);
    if (record.status === "analyzing" || record.status === "processing" || runsRunning) {
      const spinner = document.createElement("div");
      spinner.className = "rc-spinner";
      media.appendChild(spinner);
    }
    const badge = document.createElement("span");
    badge.className = "rc-badge";
    badge.textContent = {
      analyzing: tr("分析中"),
      analyzed: tr("已分析"),
      processing: tr("美化中"),
      done: tr("已美化"),
      error: tr("失敗"),
      archived: tr("可更新")
    }[record.status] || "";
    if (record.status !== "archived" && runs.length > 1) {
      const runsDone = runs.filter((x) => x.status === "done").length;
      if (runsRunning) badge.textContent = tr("美化中 ({runsDone}/{length})", { runsDone, length: runs.length });
      else if (runsDone) badge.textContent = tr("已美化 ({runsDone}/{length})", { runsDone, length: runs.length });
    }

    // A square .history-cube with the badge over its bottom-left corner,
    // the same tile 對話模式's own 我的照片 column (BatchSidebar, app.tsx)
    // shows. No filename -- the photo itself tells them apart.
    item.appendChild(media);
    item.appendChild(badge);
    if (record.status === "error") item.title = record.error || tr("處理失敗");
    item.addEventListener("click", async () => {
      if (record !== currentBatchRecord && !(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return;
      selectBatchRecord(record);
    });
    return item;
  }

  function updateBatchSidebarItem(record) {
    const existing = els.batchSidebarList.querySelector(`[data-id="${record.localId}"]`);
    if (!existing) return;
    existing.replaceWith(buildBatchSidebarItem(record));
    renderBatchSidebarFilters();
  }

  // ── Batches uploaded in 對話模式 ──
  // Several photos uploaded together on the chat page are one session
  // there, but each is still its own history case -- which is all this page
  // knows about. The session is what says they belong together, so it stays
  // the one place that decides it: 最近照片 shows such a session as one tile
  // (renderHistoryList) and opening any of its cases lists the rest in the
  // batch sidebar, the same way a batch uploaded here does. Only sessions
  // of more than one photo -- a single photo has nothing to group.
  // Every live session and every case: the backend clamps it to its own max.
  const CASE_GROUPS_LIMIT = 1000;
  let caseGroupsByKey = new Map(); // case_id (= history image_key) -> session row
  let caseGroupsLoad = null; // { user, promise }
  let caseGroupSidebarToken = 0;
  let caseGroupSidebarSession = null; // session_id the sidebar is listing, if any
  let caseGroupSidebarPhotos = []; // that session's photos, for 全部下載

  // Cached per user; refresh re-reads it (each fresh render of 最近照片), so a
  // batch made on the chat page since is picked up there.
  function loadCaseGroups({ refresh = false } = {}) {
    const user = getCurrentUser();
    if (!user) {
      caseGroupsByKey = new Map();
      return Promise.resolve(caseGroupsByKey);
    }
    if (!refresh && caseGroupsLoad?.user === user) return caseGroupsLoad.promise;
    const promise = listChatSessions(user, { limit: CASE_GROUPS_LIMIT })
      .then(({ sessions }) => {
        const groups = new Map();
        (sessions || []).forEach((row) => {
          if (!row.session_id || !(row.photo_count > 1)) return;
          (row.case_ids || []).forEach((caseId) => caseId && groups.set(caseId, row));
        });
        if (getCurrentUser() === user) caseGroupsByKey = groups;
        return groups;
      })
      // Without them every case just lists on its own, as before.
      .catch(() => caseGroupsByKey);
    caseGroupsLoad = { user, promise };
    return promise;
  }

  function forgetCaseGroup(sessionId) {
    for (const [caseId, row] of caseGroupsByKey) {
      if (row.session_id === sessionId) caseGroupsByKey.delete(caseId);
    }
    if (caseGroupSidebarSession === sessionId) hideCaseGroupSidebar();
  }

  function hideCaseGroupSidebar() {
    ++caseGroupSidebarToken;
    caseGroupSidebarSession = null;
    if (els.batchSidebar) els.batchSidebar.hidden = true;
    if (els.resultsBody) els.resultsBody.classList.remove("batch-mode");
    closeBatchDrawer();
  }

  // Called by every history view in place of just hiding the sidebar.
  // Opening another case of the batch already listed keeps the list where
  // it is and only moves the highlight, then refreshes the rows' states.
  function showCaseGroupSidebar(caseKey) {
    const listed = caseGroupSidebarSession
      && caseKey
      && caseGroupsByKey.get(caseKey)?.session_id === caseGroupSidebarSession;
    if (listed) {
      ++caseGroupSidebarToken;
      els.batchSidebarList?.querySelectorAll(".batch-sidebar-item").forEach((item) => {
        item.classList.toggle("selected", item.dataset.caseId === caseKey);
      });
    } else {
      hideCaseGroupSidebar();
    }
    const user = getCurrentUser();
    if (!caseKey || !user) return;
    const token = caseGroupSidebarToken;
    (async () => {
      const group = (await loadCaseGroups()).get(caseKey);
      if (token !== caseGroupSidebarToken) return;
      if (!group) {
        if (listed) hideCaseGroupSidebar();
        return;
      }
      // A batch uploaded on the chat page is one session per photo, listed
      // in session_ids in upload order; an older multi-photo upload is one
      // session holding them all. Either way the photos are those sessions'.
      const ids = group.session_ids?.length ? group.session_ids : [group.session_id];
      const sessions = await Promise.all(ids.map((id) => getChatSession(user, id).catch(() => null)));
      // expired, or a transient failure -- the photo still shows on its own
      if (!sessions.some(Boolean)) return;
      if (token !== caseGroupSidebarToken || getCurrentUser() !== user) return;
      const photos = sessions.flatMap((s) => s?.photos || []).filter((p) => p.case_id);
      if (photos.length < 2) return;
      renderCaseGroupSidebar(group.session_id, photos, caseKey);
    })();
  }

  function renderCaseGroupSidebar(sessionId, photos, caseKey) {
    if (caseGroupSidebarSession !== sessionId) batchSidebarFilter = "all";
    caseGroupSidebarSession = sessionId;
    if (els.batchDrawerTriggerCount) {
      els.batchDrawerTriggerCount.textContent = String(photos.length);
      els.batchDrawerTriggerCount.hidden = false;
    }
    // 全部美化 acts on a batch uploaded on this page (batchRecords), not on
    // one listed from the chat page. 全部下載 works for both -- here it zips
    // each photo's source plus its current version (buildCaseGroupZip).
    if (els.batchContinueAllBtn) els.batchContinueAllBtn.hidden = true;
    caseGroupSidebarPhotos = photos;
    if (els.batchDownloadAllBtn) {
      const doneCount = photos.filter((p) => p.active_version?.job_id).length;
      const hint = doneCount === 0
        ? tr("下載所有原圖（{length} 張）", { length: photos.length })
        : doneCount === photos.length
          ? tr("下載所有已完成的結果（{doneCount} 張）", { doneCount })
          : tr("下載全部（已完成 {doneCount} 張，其餘為原圖，共 {length} 張）", { doneCount, length: photos.length });
      els.batchDownloadAllBtn.hidden = false;
      els.batchDownloadAllBtn.title = hint;
      els.batchDownloadAllBtn.setAttribute("aria-label", hint);
    }
    const list = els.batchSidebarList;
    const keepScroll = list.scrollTop;
    list.innerHTML = "";
    photos.forEach((photo) => list.appendChild(buildCaseGroupSidebarItem(photo, caseKey)));
    renderBatchSidebarFilters();
    list.scrollTop = keepScroll;
    els.batchSidebar.hidden = false;
    if (els.resultsBody) els.resultsBody.classList.add("batch-mode");
  }

  // A chat-page photo's state in this sidebar's own vocabulary -- the same
  // classes and badges buildBatchSidebarItem gives a record here.
  function caseGroupPhotoState(photo) {
    if (photo.status === "failed") return { key: "error", text: tr("失敗") };
    if (photo.status === "rendering" || photo.queued_render || photo.pending_render) return { key: "processing", text: tr("美化中") };
    if (photo.status === "analyzing" || photo.status === "reframing" || photo.pending_reframe) return { key: "analyzing", text: tr("分析中") };
    if (photo.active_version?.job_id) return { key: "done", text: tr("已美化") };
    return { key: "analyzed", text: tr("已分析") };
  }

  function buildCaseGroupSidebarItem(photo, caseKey) {
    const state = caseGroupPhotoState(photo);
    const item = document.createElement("div");
    item.className = `history-cube batch-sidebar-item ${state.key}${photo.case_id === caseKey ? " selected" : ""}`;
    item.dataset.caseId = photo.case_id;
    item.dataset.stage = sidebarStage(state.key);

    const media = document.createElement("div");
    media.className = "rc-media";
    const img = document.createElement("img");
    const version = photo.active_version;
    img.src = version?.job_id
      ? resultThumbnailUrl(version.job_id, { run: version.run || 1 })
      : sourceThumbnailUrl(photo.analyze_job_id);
    img.alt = "";
    media.appendChild(img);
    if (state.key === "processing" || state.key === "analyzing") {
      const spinner = document.createElement("div");
      spinner.className = "rc-spinner";
      media.appendChild(spinner);
    }
    const badge = document.createElement("span");
    badge.className = "rc-badge";
    badge.textContent = state.text;

    item.appendChild(media);
    item.appendChild(badge);
    if (state.key === "error" && photo.error) item.title = photo.error;
    item.addEventListener("click", async () => {
      if (item.classList.contains("selected") || item.classList.contains("opening")) return;
      if (!(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return;
      closeBatchDrawer();
      item.classList.add("opening");
      try {
        await openCaseByKey(photo.case_id);
      } finally {
        item.classList.remove("opening");
      }
    });
    return item;
  }

  function openBatchError(record) {
    if (!els.errorModal) return;
    els.errorModalFile.textContent = record.file.name;
    els.errorModalMsg.textContent = record.error || tr("處理失敗，請稍後再試。");
    els.errorModal.hidden = false;
  }

  function closeErrorModal() {
    if (els.errorModal) els.errorModal.hidden = true;
  }

  // Generic in-app notice popup -- same open/close shape as the error modal
  // above, styled with the PhotoMaster wordmark instead since it's not
  // reporting a failure (e.g. the inline revise panel's Run button when
  // nothing was actually changed). Most callers use the dismiss-only form;
  // recoverable notices can supply one primary action without needing a
  // separate one-off modal.
  let noticeModalAction = null;
  // Second, optional action button -- so far only used by the archived-case
  // notice (重新分析 / 重新一鍵美化), but kept generic like the first one in
  // case another caller needs a two-choice notice later.
  let noticeModalAction2 = null;

  function showNoticeModal(message, {
    dismissLabel = "OK",
    actionLabel = "",
    onAction = null,
    action2Label = "",
    onAction2 = null,
  } = {}) {
    if (!els.noticeModal) return;
    if (els.noticeModalMsg) els.noticeModalMsg.textContent = message;
    noticeModalAction = typeof onAction === "function" ? onAction : null;
    noticeModalAction2 = typeof onAction2 === "function" ? onAction2 : null;
    const hasAnyAction = !!(noticeModalAction || noticeModalAction2);
    if (els.noticeModalOkBtn) {
      els.noticeModalOkBtn.textContent = dismissLabel;
      els.noticeModalOkBtn.classList.toggle("btn-grad", !hasAnyAction);
      els.noticeModalOkBtn.classList.toggle("btn-secondary", hasAnyAction);
    }
    if (els.noticeModalActionBtn) {
      els.noticeModalActionBtn.hidden = !noticeModalAction;
      els.noticeModalActionBtn.disabled = false;
      els.noticeModalActionBtn.textContent = actionLabel;
    }
    if (els.noticeModalAction2Btn) {
      els.noticeModalAction2Btn.hidden = !noticeModalAction2;
      els.noticeModalAction2Btn.disabled = false;
      els.noticeModalAction2Btn.textContent = action2Label;
    }
    els.noticeModal.hidden = false;
  }

  function closeNoticeModal() {
    noticeModalAction = null;
    noticeModalAction2 = null;
    if (els.noticeModal) els.noticeModal.hidden = true;
    if (els.noticeModalOkBtn) {
      els.noticeModalOkBtn.textContent = "OK";
      els.noticeModalOkBtn.classList.add("btn-grad");
      els.noticeModalOkBtn.classList.remove("btn-secondary");
    }
    if (els.noticeModalActionBtn) {
      els.noticeModalActionBtn.hidden = true;
      els.noticeModalActionBtn.disabled = false;
      els.noticeModalActionBtn.textContent = "";
    }
    if (els.noticeModalAction2Btn) {
      els.noticeModalAction2Btn.hidden = true;
      els.noticeModalAction2Btn.disabled = false;
      els.noticeModalAction2Btn.textContent = "";
    }
  }

  // Thrown by a notice action that shows its own nested confirm/estimate step
  // (e.g. the archived-case notice's preflight cost modal below) when the
  // user backs out of THAT step -- runNoticeModalAction treats this as
  // "quietly go back to the notice, try again", not a real failure worth
  // reporting via its usual error text.
  class NoticeActionCancelled extends Error {}

  // Shared by both action buttons -- `btn`/`action` pick which one is
  // running so the other stays untouched (and re-enabled on failure) while
  // its sibling shows "準備中…".
  async function runNoticeModalAction(action, btn) {
    if (!action || !btn || btn.disabled) return;
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = tr("準備中…");
    try {
      await action();
    } catch (error) {
      // Keep the modal open so the user can retry instead of dropping them
      // back onto the same read-only case with no explanation.
      if (!(error instanceof NoticeActionCancelled) && els.noticeModalMsg) {
        els.noticeModalMsg.textContent = tr("無法重新載入原始照片，請稍後再試。") + "\n" + friendlyError(error);
      }
      if (!els.noticeModal.hidden) {
        btn.disabled = false;
        btn.textContent = originalLabel;
      }
    }
  }

  function handleNoticeModalAction() {
    return runNoticeModalAction(noticeModalAction, els.noticeModalActionBtn);
  }

  function handleNoticeModalAction2() {
    return runNoticeModalAction(noticeModalAction2, els.noticeModalAction2Btn);
  }

  const ARCHIVED_ANALYSIS_NOTICE = tr("這張照片還沒套用最新版本，重新處理一次就能得到更好的美化結果。");

  // Re-enters through the ordinary new-upload path instead of continuing or
  // rerunning the archived job. Fetch the source before resetApp clears the
  // current case pointers, then let setSelectedFile/startSubmit establish a
  // fresh job, fresh analysis, and the normal downstream review pipeline.
  // skipReview mirrors startSubmit's own flag: false routes through 分析
  // (analyze, pause for review) same as the upload page's 分析 button;
  // true routes through the combined 一鍵美化 flow same as els.startFullButton.
  async function restartArchivedCase(sourceFile, sourceUrl, skipReview = false, { settings = null } = {}) {
    let file = sourceFile;
    if (!file) {
      if (!sourceUrl) throw new Error(tr("這筆紀錄沒有可重新上傳的原始照片。"));
      const response = await fetchWithRetry(sourceUrl);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      file = new File([blob], "reuploaded-photo.jpg", { type: blob.type || "image/jpeg" });
    }

    // Invalidate any history-view work that may still be finishing in the
    // background. The archived history entry itself is intentionally kept;
    // only the active client-side case state is discarded.
    historyViewToken += 1;
    closeNoticeModal();
    resetApp();
    // resetApp's own showSection("upload") is what a real Home/清除 click
    // wants, but here it's just a stepping stone -- setSelectedFile below
    // awaits createProcThumbUrl's createImageBitmap (a real async decode,
    // not just a microtask), so the browser gets a genuine paint of that
    // bare instant: results page -> upload screen's drop-zone loading
    // spinner -> processing screen once startSubmit finishes setting up,
    // reading as a stray flash of the wrong screen for a moment. Jumping
    // straight to "processing" here instead means startSubmit's OWN
    // showSection("processing") moments later is a same-section no-op, and
    // whatever it hasn't set up yet (procThumb's src, procTitle, the sub-
    // message rotation) still lands well within this same already-brief
    // window, same as before.
    // resetApp just put every control back to the product default
    // (resetSettingsToDefaults). For an archived case that is right -- it is
    // being re-uploaded from scratch. For a debug-mode analysis-engine swap
    // it is not: that restart is meant to change one value and hold the rest
    // still, so the caller hands them over and they go onto the UPLOAD-page
    // copies, which are the ones startSubmit actually reads.
    if (settings) {
      applyAnalysisModelOption(findAnalysisModelOption(settings.analysisModel), { includeUpload: true });
      applyModelQualityOption(findModelQualityOption(settings.imageModelQuality), { includeUpload: true });
      applyAspectRatioOption(findAspectRatioOption(settings.aspectRatio), { includeUpload: true });
      applyOutputScaleOption(findOutputScaleOption(settings.outputScale), { includeUpload: true });
      applyAdvancedOptionsOption(settings.advancedOptions, "upload");
    }
    showSection("processing");
    await setSelectedFile(file);
    void startSubmit({ skipReview });
  }

  function showArchivedAnalysisNotice() {
    // Capture these now so switching UI state while the modal is open cannot
    // make the action accidentally reuse a different case's source photo.
    const sourceFile = selectedFile;
    const sourceUrl = selectedFileSourceUrl;
    const canRestart = !!(sourceFile || sourceUrl);
    // 維持現況 only dismisses this one-time notice -- it doesn't undo the
    // read-only lock, so #ap-restart-actions (the same 重新分析/重新一鍵美化
    // split-run pair as this notice's own two actions, index.html) is left
    // showing in Run's old spot for as long as this archived case stays on
    // screen, instead of the case going dead-ended after the first dismiss.
    if (els.apRestartActions) els.apRestartActions.hidden = !canRestart;
    updateApCaseSettingsTrigger();
    // Each restart choice gets its OWN #preflight-cost-modal at click time
    // (same row builders #start-btn/#start-full-btn use, since both re-enter
    // through the exact same startSubmit({skipReview}) path) instead of a
    // single cost line folded into this notice -- 重新分析 and 重新一鍵美化
    // spend different amounts, so a single shared line can no longer speak
    // for both. A decline here throws NoticeActionCancelled so
    // runNoticeModalAction just quietly resets this button instead of
    // reporting it as a failure.
    showNoticeModal(ARCHIVED_ANALYSIS_NOTICE, canRestart ? {
      dismissLabel: tr("維持現況"),
      actionLabel: tr("重新分析"),
      onAction: async () => {
        const confirmed = await showPreflightCostModal([analysisPreflightStep()]);
        if (!confirmed) throw new NoticeActionCancelled();
        await restartArchivedCase(sourceFile, sourceUrl, false);
      },
      action2Label: tr("重新一鍵美化"),
      onAction2: async () => {
        const confirmed = await showPreflightCostModal([analysisPreflightStep(), editPreflightStep()]);
        if (!confirmed) throw new NoticeActionCancelled();
        await restartArchivedCase(sourceFile, sourceUrl, true);
      }
    } : undefined);
  }

  // #ap-restart-analysis-btn / #ap-restart-full-btn (index.html) -- the
  // lasting counterparts of this same notice's 重新分析/重新一鍵美化 actions,
  // for once the user has already dismissed the notice with 維持現況. Unlike
  // those, there's no notice text already asking "are you sure" alongside
  // them, so each click asks its own confirm question here, with the same
  // toggle-gated cost breakdown showConfirmModal's costSteps already knows
  // how to render (isPreflightCostEstimateEnabled, above).
  async function handleArchivedRestartClick(btn, skipReview) {
    if (!btn || btn.disabled) return;
    const sourceFile = selectedFile;
    const sourceUrl = selectedFileSourceUrl;
    if (!sourceFile && !sourceUrl) return;
    const steps = skipReview
      ? [analysisPreflightStep(), editPreflightStep()]
      : [analysisPreflightStep()];
    const confirmed = await showConfirmModal(
      skipReview ? tr("確定要用新版本重新一鍵美化一次嗎？") : tr("確定要用新版本重新分析一次嗎？"),
      { confirmLabel: skipReview ? tr("重新一鍵美化") : tr("重新分析"), danger: false, costSteps: steps }
    );
    if (!confirmed) return;
    // Restarting re-fetches the original photo from sourceUrl when there's no
    // in-memory sourceFile (the common case for a history record reopened
    // after a page reload) before resetApp/startSubmit can show the
    // analyzing/processing animation -- same "準備中…" busy-state swap
    // runNoticeModalAction uses for this notice's own two actions, so that
    // wait reads as "working" instead of the button just sitting there
    // unchanged. Scoped to the <span> (not btn.textContent) so the icon svg
    // stays in place.
    const label = btn.querySelector("span");
    const originalLabel = label ? label.textContent : "";
    btn.disabled = true;
    if (label) label.textContent = tr("準備中…");
    hideError();
    try {
      await restartArchivedCase(sourceFile, sourceUrl, skipReview);
    } catch (error) {
      showError(tr("無法重新載入原始照片，請稍後再試。") + "\n" + friendlyError(error));
      btn.disabled = false;
      if (label) label.textContent = originalLabel;
    }
  }
  if (els.apRestartAnalysisBtn) {
    els.apRestartAnalysisBtn.addEventListener("click", () => handleArchivedRestartClick(els.apRestartAnalysisBtn, false));
  }
  if (els.apRestartFullBtn) {
    els.apRestartFullBtn.addEventListener("click", () => handleArchivedRestartClick(els.apRestartFullBtn, true));
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
  // (e.g. handleLogout, or the 美化 no-changes prompt) override both so the
  // button doesn't misleadingly read "刪除" for an action that isn't
  // deleting anything. Either way this is the modal's one primary action
  // (btn-danger or btn-grad, matching every other modal's single highlighted
  // button) -- it must never fall back to btn-secondary, or it reads as a
  // second, equally-weighted Cancel with nothing marking which button
  // actually confirms.
  //
  // costSteps (optional) renders #preflight-cost-modal's own styled rows
  // (buildPreflightCostRow, further below) under the plain-text message
  // instead of a price folded into that text as another sentence -- used by
  // the 美化 no-changes prompt so its cost estimate gets the same
  // label/value/model hierarchy every other cost display in the app uses.
  // Gated by isPreflightCostEstimateEnabled the same way showPreflightCostModal
  // is, so a caller doesn't have to check the setting itself.
  function showConfirmModal(message, { confirmLabel = tr("刪除"), cancelLabel = tr("取消"), danger = true, costSteps = null } = {}) {
    return new Promise((resolve) => {
      if (!els.confirmModal) {
        resolve(window.confirm(message));
        return;
      }
      clearConfirmModalWait(); // drop any stale listener from an abandoned prior prompt
      if (els.confirmModalMsg) els.confirmModalMsg.textContent = message;
      if (els.confirmModalCostRows) {
        const steps = isPreflightCostEstimateEnabled() ? costSteps : null;
        els.confirmModalCostRows.innerHTML = "";
        if (steps?.length) {
          steps.forEach((step) => els.confirmModalCostRows.appendChild(buildPreflightCostRow(step)));
        }
        els.confirmModalCostRows.hidden = !steps?.length;
      }
      if (els.confirmModalCancelBtn) els.confirmModalCancelBtn.textContent = cancelLabel;
      if (els.confirmModalOkBtn) {
        els.confirmModalOkBtn.textContent = confirmLabel;
        els.confirmModalOkBtn.classList.toggle("btn-danger", danger);
        els.confirmModalOkBtn.classList.toggle("btn-grad", !danger);
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
      // beside #account-badge-label survives every call to this. The label
      // intentionally says 最近照片; the account name stays in aria-label
      // and in the history modal subtitle.
      if (els.accountBadgeLabel) els.accountBadgeLabel.textContent = user ? tr("最近照片") : "";
      const uploadVisible = !!els.sections.upload?.classList.contains("visible");
      els.accountBadge.hidden = !user || uploadVisible;
      els.accountBadge.title = user ? tr("查看處理紀錄") : "";
      els.accountBadge.setAttribute("aria-label", user ? tr("{user}｜查看處理紀錄", { user }) : "");
    }
    if (els.registerAccountBtn) els.registerAccountBtn.hidden = !!user;
    refreshUploadGallery(user);
    syncReportBtnPosition();
  }

  async function handleCreateAccount() {
    const name = normalizeUsername(els.accountModalInput ? els.accountModalInput.value : "");
    if (!name) {
      showAccountModalError(tr("請輸入使用者名稱。"));
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
      showAccountModalError(err.message || tr("建立帳號失敗，請再試一次。"));
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
    if (diffDays === 0) return tr("今天");
    if (diffDays === 1) return tr("昨天");
    return tr("{v1}月{v2}日", { v1: d.getMonth() + 1, v2: d.getDate() });
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
    const confirmed = await showConfirmModal(tr("確定要刪除這筆處理紀錄嗎？"));
    if (!confirmed) return;
    try {
      // Captured before removal -- li.closest/contains would already read
      // false once removeHistoryCubeFromDom takes it out of the tree below.
      const inDrawer = els.historyList?.contains(li);
      await deleteHistoryEntry(user, job.image_key);
      removeHistoryCubeFromDom(li);
      historyJobsByKey.delete(job.image_key);
      historyTileElsByKey.delete(job.image_key);
      archivedHistoryKeys.delete(job.image_key);
      // Keeps historyDrawerOffset in sync with the server-side list this
      // deleted entry came out of -- otherwise a later scroll-triggered
      // loadHistoryDrawerPage would request an offset one too high and
      // silently skip whatever entry is now in that spot.
      if (inDrawer) historyDrawerOffset = Math.max(0, historyDrawerOffset - 1);
      // Nothing left to browse -- back out to a fresh upload screen instead
      // of leaving the drawer open over whatever page happened to be
      // showing behind it once its last entry is gone.
      if (els.historyList && !els.historyList.children.length) {
        closeHistoryDrawer();
        resetApp();
      }
    } catch (err) {
      showNoticeModal(err.message || tr("刪除紀錄失敗，請稍後再試。"));
    }
  }

  // One request (clearHistory) instead of looping handleDeleteHistoryEntry
  // per tile -- always leaves the list empty afterward, so this can go
  // straight to the same "nothing left to browse" reset that
  // handleDeleteHistoryEntry only reaches conditionally.
  async function handleClearAllHistory() {
    const user = getCurrentUser();
    if (!user) return;
    const confirmed = await showConfirmModal(tr("確定要刪除所有處理紀錄嗎？此操作無法復原。"));
    if (!confirmed) return;
    try {
      await clearHistory(user);
      if (els.historyList) els.historyList.innerHTML = "";
      if (els.historyEmpty) els.historyEmpty.hidden = false;
      if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = true;
      archivedHistoryKeys.clear();
      historyDrawerOffset = 0;
      historyDrawerHasMore = false;
      closeHistoryDrawer();
      resetApp();
    } catch (err) {
      showNoticeModal(err.message || tr("刪除所有紀錄失敗，請稍後再試。"));
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
    const unsent = isReviewDirty() ? tr("這張照片還沒美化的調整也會清掉。") : "";
    const confirmed = await showConfirmModal(
      tr("登出後，這個瀏覽器將不再記得目前的使用者名稱，需要重新輸入才能再次查看處理紀錄。{unsent}確定要登出嗎？", { unsent }),
      { confirmLabel: tr("登出"), danger: false }
    );
    if (!confirmed) return;
    leaveConfirmed = true;
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
  // image_key -> that tile's <li>, so a compatibility decision can update the
  // tile's .rc-badge immediately and a bulk delete (handleDeleteSelected-
  // HistoryClick) can remove exactly the right DOM nodes without having to
  // re-query for them. All rendered tiles are indexed; historyJobsByKey still
  // contains only tiles eligible for selection/download.
  const historyTileElsByKey = new Map();
  // Compatibility is derived from each case's analysis while its history
  // tile renders (and may also be rediscovered when that case opens). Cache
  // the decision for this page so later rerenders keep the same badge without
  // introducing a synthetic status field in Redis.
  const archivedHistoryKeys = new Set();

  // "success"/"error" are terminal for a job (see pollJobStatus, which stops
  // on exactly those two) -- a finished case cannot become unfinished, so
  // asking the backend about it again can only ever return the same answer.
  // That matters because every history tile runs its own status check on
  // every render, and the gallery re-renders on every return to the upload
  // screen (resetApp -> refreshUploadGallery): 20 tiles meant 20+ requests
  // each time, almost all of them re-confirming jobs that finished days ago.
  // Only genuinely unfinished jobs go to the network now. Keyed by job id,
  // which is unique per job, so nothing here can go stale within a session.
  const terminalJobStatusCache = new Map();

  async function getJobStatusCached(jobId, opts) {
    const cached = terminalJobStatusCache.get(jobId);
    if (cached) return cached;
    const data = await getJobStatusOnce(jobId, opts);
    if (data && (data.status === "success" || data.status === "error")) {
      terminalJobStatusCache.set(jobId, data);
    }
    return data;
  }

  function applyHistoryArchivedState(job, li = historyTileElsByKey.get(job?.image_key)) {
    const archived = job?.image_key && archivedHistoryKeys.has(job.image_key);
    if (!job?.image_key || !archived || !li) return false;
    archivedHistoryKeys.add(job.image_key);
    li.classList.remove("analyzing", "processing", "analyzed", "done", "error");
    li.classList.add("archived");
    const badge = li.querySelector(".rc-badge");
    if (badge) badge.textContent = tr("可更新");
    return true;
  }

  function markHistoryCaseArchived(job = null) {
    const imageKey = job?.image_key || currentCaseHistoryImageKey;
    if (!imageKey) return;
    archivedHistoryKeys.add(imageKey);
    const historyJob = job || historyJobsByKey.get(imageKey) || { image_key: imageKey };
    applyHistoryArchivedState(historyJob);
  }

  // Per-job lookups for history rows that don't carry compatibility_version
  // themselves (recorded before the backend started storing it). A finished
  // job's analysis never changes, so one lookup per page load is enough --
  // this used to re-download every such job's full /result (~150KB each) on
  // every drawer open, Home and tab refocus.
  const legacyArchiveBadgeCache = new Map();

  // Resolve whether a history row's analysis is below the compatibility
  // floor, applying the exact same isCompatible() rule that makes the
  // analysis/result page read-only.
  async function historyJobNeedsArchiveBadge(job, jobIds, statuses) {
    try {
      await ensureCompatibilityConfigLoaded();
      const doneIdx = job.stage === "analyze"
        ? (statuses[0]?.status === "success" ? 0 : -1)
        : statuses.findIndex((status) => status.status === "success");
      if (doneIdx === -1) return false;
      // Rows recorded by the current backend carry the version of the
      // analysis they run on (see api.py's _history_compatibility_version),
      // so no per-row fetch is needed at all.
      if (typeof job.compatibility_version === "number") {
        return !isCompatible({ compatibility_version: job.compatibility_version });
      }
      // The pending lookup itself is cached, so the drawer and the upload
      // page's gallery rendering the same row at once share one request.
      const cacheKey = `${job.stage === "analyze" ? "analyze" : "result"}:${jobIds[doneIdx]}`;
      let lookup = legacyArchiveBadgeCache.get(cacheKey);
      if (!lookup) {
        lookup = legacyHistoryJobNeedsArchiveBadge(job, jobIds, doneIdx);
        legacyArchiveBadgeCache.set(cacheKey, lookup);
        lookup.catch(() => legacyArchiveBadgeCache.delete(cacheKey));
      }
      return await lookup;
    } catch {
      // Compatibility lookup is supplemental to the ordinary job-status
      // render. A transient metadata failure must not turn a valid history
      // tile into the unrelated "無法取得狀態" state (and isn't cached, so the
      // next render tries again).
      return false;
    }
  }

  // Older rows: read analysis.compatibility_version off the job's own result.
  async function legacyHistoryJobNeedsArchiveBadge(job, jobIds, doneIdx) {
    if (job.stage === "analyze") {
      const analyzeResult = await getAnalyzeResult(jobIds[0]);
      const analysis = analyzeResult.result?.analysis;
      // Prefer the backend's authoritative result when available; the
      // local check keeps this working with an older API response too.
      return analyzeResult.result?.compatible === false
        || (analysis ? !isCompatible(analysis) : false);
    }

    const result = await getJobRuns(jobIds[doneIdx], { path: job.path });
    const analysis = result.result?.analysis
      || result.runs?.find((run) => run.result?.analysis)?.result.analysis;
    return analysis ? !isCompatible(analysis) : false;
  }

  function updateHistorySelectionButtons() {
    const count = selectedHistoryKeys.size;
    if (els.historyDownloadSelectedBtn) {
      els.historyDownloadSelectedBtn.disabled = count === 0;
      const dlHint = count > 0 ? tr("下載所選（{count} 張）", { count }) : tr("下載所選");
      els.historyDownloadSelectedBtn.title = dlHint;
      els.historyDownloadSelectedBtn.setAttribute("aria-label", dlHint);
    }
    if (els.historyDownloadSelectedCount) els.historyDownloadSelectedCount.textContent = count > 0 ? String(count) : "";
    if (els.historyDeleteSelectedBtn) {
      els.historyDeleteSelectedBtn.disabled = count === 0;
      const delHint = count > 0 ? tr("刪除所選（{count} 張）", { count }) : tr("刪除所選");
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
    const confirmed = await showConfirmModal(tr("確定要刪除這 {length} 筆處理紀錄嗎？", { length: keys.length }));
    if (!confirmed) return;
    if (els.historyDeleteSelectedBtn) els.historyDeleteSelectedBtn.disabled = true;
    const results = await Promise.allSettled(keys.map((key) => deleteHistoryEntry(user, key)));
    let anyFailed = false;
    let removedFromDrawer = 0;
    keys.forEach((key, i) => {
      if (results[i].status !== "fulfilled") {
        anyFailed = true;
        return;
      }
      const li = historyTileElsByKey.get(key);
      if (li) {
        if (els.historyList?.contains(li)) removedFromDrawer++;
        removeHistoryCubeFromDom(li);
      }
      historyJobsByKey.delete(key);
      historyTileElsByKey.delete(key);
      archivedHistoryKeys.delete(key);
    });
    // Same offset bookkeeping as handleDeleteHistoryEntry, once per entry
    // actually removed from the drawer (a demo-gallery selection, if any,
    // has no such offset to keep in sync).
    if (removedFromDrawer) historyDrawerOffset = Math.max(0, historyDrawerOffset - removedFromDrawer);
    exitHistorySelectMode();
    if (anyFailed) showNoticeModal(tr("部分紀錄刪除失敗，請稍後再試。"));
    // Same "nothing left to browse" reset as handleDeleteHistoryEntry/
    // handleClearAllHistory once the last entry is gone.
    if (els.historyList && !els.historyList.children.length) {
      closeHistoryDrawer();
      resetApp();
    }
  }

  function historyMediaMarkup(src) {
    if (!src) return "";
    return `<img class="history-cube-bg" src="${src}" alt="" aria-hidden="true"><img class="history-cube-main" src="${src}" alt="">`;
  }

  function wireHistoryTileRemove(li, job) {
    const removeBtn = li.querySelector(".history-cube-remove");
    if (!removeBtn) return;
    removeBtn.addEventListener("click", (e) => {
      // Stop this from also bubbling into the tile's own click handler
      // (viewHistoryRecord/viewProcessingHistoryRecord), attached once the
      // status check in renderHistoryList resolves.
      e.stopPropagation();
      handleDeleteHistoryEntry(job, li);
    });
  }

  // ── Live 美化中/分析中 tiles ──
  // renderHistoryList checks each job's status once. Tiles still in
  // progress (the drawer's or the upload page's 最近照片) are re-checked every
  // few seconds -- status only, the cheap request -- and re-rendered in place
  // once their job settles, instead of staying 美化中 until the whole list is
  // reloaded (Home, tab refocus, reopening the drawer). Only while that list
  // is actually on screen and the tab is visible; stops once none are left.
  const HISTORY_TILE_POLL_MS = 4000;
  // A job that never settles (e.g. its worker died) shouldn't be polled for
  // the rest of the tab's life.
  const HISTORY_TILE_POLL_MAX_MS = 15 * 60 * 1000;
  const liveHistoryTiles = new Set(); // { job, li, container, rerender, since }
  let historyTilePollTimer = null;

  function historyTileInProgress(li) {
    return li.classList.contains("processing") || li.classList.contains("analyzing");
  }

  function historyListShowing(container) {
    if (container === els.historyList) return !!els.historyDrawer?.classList.contains("drawer-open");
    return !!els.sections?.upload?.classList.contains("visible") && uploadGalleryActiveTab === "recent";
  }

  function watchHistoryTile(entry) {
    liveHistoryTiles.add({ ...entry, since: Date.now() });
    if (!historyTilePollTimer) historyTilePollTimer = setTimeout(pollHistoryTiles, HISTORY_TILE_POLL_MS);
  }

  async function pollHistoryTiles() {
    historyTilePollTimer = null;
    if (!document.hidden) {
      await Promise.all([...liveHistoryTiles].map(async (entry) => {
        const { job, li, container, rerender, since } = entry;
        // Gone (the list was re-rendered) or given up on.
        if (!li.isConnected || Date.now() - since > HISTORY_TILE_POLL_MAX_MS) {
          liveHistoryTiles.delete(entry);
          return;
        }
        if (!historyListShowing(container)) return;
        const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
        let statuses;
        try {
          statuses = await Promise.all(jobIds.map((id) => getJobStatusCached(id, { path: job.path })));
        } catch {
          return; // transient -- try again next tick
        }
        // Same settle rules renderHistoryList's own branches use.
        const settled = job.stage === "analyze"
          ? statuses[0]?.status === "success" || statuses[0]?.status === "error"
          : statuses.some((s) => s.status === "success") || statuses.every((s) => s.status === "error");
        if (!settled || !li.isConnected) return;
        liveHistoryTiles.delete(entry);
        // A fresh copy of the still-in-progress tile (same markup, none of
        // its listeners), so renderTileStatus can attach the settled state's
        // own click handler without the 美化中 one also firing.
        const fresh = li.cloneNode(true);
        wireHistoryTileRemove(fresh, job);
        li.replaceWith(fresh);
        if (job.image_key && historyTileElsByKey.get(job.image_key) === li) historyTileElsByKey.set(job.image_key, fresh);
        await rerender({ job, li: fresh });
        if (historyTileInProgress(fresh)) watchHistoryTile({ job, li: fresh, container, rerender });
      }));
    }
    if (liveHistoryTiles.size && !historyTilePollTimer) {
      historyTilePollTimer = setTimeout(pollHistoryTiles, HISTORY_TILE_POLL_MS);
    }
  }

  // One tile per batch from 對話模式 (see loadCaseGroups), per list: the
  // drawer's and the upload page's 最近照片 each keep their own, across the
  // pages appended to them. container -> Map(session_id -> { group, li, keys }),
  // keys being the batch's cases this list has received so far.
  const historyGroupTiles = new Map();

  function historyGroupThumbUrl(thumb) {
    return thumb.run ? resultThumbnailUrl(thumb.job_id, { run: thumb.run }) : sourceThumbnailUrl(thumb.job_id);
  }

  // The session row's status (chat_session._summary) in this list's own
  // classes and badges -- the ones renderTileStatus gives a single case.
  function historyGroupState(group) {
    switch (group.status) {
      case "rendering": return { key: "processing", text: tr("美化中") };
      case "analyzing": return { key: "analyzing", text: tr("分析中") };
      case "failed": return { key: "error", text: tr("失敗") };
      case "done": return {
        key: "done",
        text: group.rendered_count < group.photo_count ? tr("美化完成（{rendered_count}/{photo_count}）", { rendered_count: group.rendered_count, photo_count: group.photo_count }) : tr("美化完成")
      };
      default: return { key: "analyzed", text: tr("待美化") };
    }
  }

  // Same cube as a single case, with its first few photos laid out in it the
  // way the chat page's own list does (SessionMosaic, home.tsx): two side by
  // side, three as one large and two small, four in a 2x2 grid, and "+N" over
  // the last when there are more. The first photo stays underneath as the
  // blurred backdrop the gaps show through.
  function buildHistoryGroupTile(group, job) {
    const state = historyGroupState(group);
    const shots = (group.thumbnails || []).slice(0, 4);
    const more = group.photo_count - shots.length;
    const backdrop = shots[0] ? historyGroupThumbUrl(shots[0]) : "";
    const cells = shots.map((thumb, i) => `
      <span class="history-cube-cell"><img src="${historyGroupThumbUrl(thumb)}" alt="" loading="lazy">${more > 0 && i === shots.length - 1 ? `<b>+${more}</b>` : ""}</span>`).join("");
    const busy = state.key === "processing" || state.key === "analyzing";
    const li = document.createElement("li");
    li.className = `history-cube history-cube-group batch-sidebar-item ${state.key}`;
    li.dataset.sessionId = group.session_id;
    li.innerHTML = `
      <div class="rc-media">${historyMediaMarkup(backdrop)}${shots.length > 1 ? `<span class="history-cube-mosaic is-${shots.length}" aria-hidden="true">${cells}</span>` : ""}${busy ? '<div class="rc-spinner"></div>' : ""}</div>
      <span class="history-cube-count">${tr("{n} 張", { n: group.photo_count })}</span>
      <span class="rc-badge">${state.text}</span>
      <button type="button" class="history-cube-remove" aria-label="${tr("刪除這組紀錄")}">×</button>
    `;
    return li;
  }

  // Opens the batch's newest case (the one its tile took the place of); the
  // batch sidebar then lists the rest. Not .selectable: 選取 downloads cases
  // one by one, which a batch tile does not stand for.
  function wireHistoryGroupTile(entry) {
    const { group, job, li } = entry;
    li.addEventListener("click", async () => {
      if (historySelectMode || li.classList.contains("opening")) return;
      if (!(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return;
      li.classList.add("opening");
      try {
        await openHistoryCase(job);
      } catch (error) {
        showNoticeModal(friendlyError(error));
      } finally {
        li.classList.remove("opening");
      }
    });
    li.querySelector(".history-cube-remove")?.addEventListener("click", (e) => {
      e.stopPropagation();
      handleDeleteHistoryGroup(entry, group);
    });
  }

  // The batch goes as a whole, the way the chat page's own × removes it:
  // the session and the history cases of all its photos.
  async function handleDeleteHistoryGroup(entry, group) {
    const user = getCurrentUser();
    if (!user) return;
    const confirmed = await showConfirmModal(tr("確定要刪除這組 {photo_count} 張照片的處理紀錄嗎？", { photo_count: group.photo_count }));
    if (!confirmed) return;
    try {
      const inDrawer = els.historyList?.contains(entry.li);
      // A chat-page batch is one session per photo: the whole group goes.
      await deleteChatSessions(user, group.session_ids?.length ? group.session_ids : [group.session_id]);
      removeHistoryCubeFromDom(entry.li);
      historyGroupTiles.forEach((tiles) => tiles.delete(group.session_id));
      // Same bookkeeping as handleDeleteHistoryEntry, once per case of the
      // batch this list had already paged in.
      if (inDrawer) historyDrawerOffset = Math.max(0, historyDrawerOffset - entry.keys.size);
      forgetCaseGroup(group.session_id);
      if (els.historyList && !els.historyList.children.length) {
        closeHistoryDrawer();
        resetApp();
      }
    } catch (err) {
      showNoticeModal(err.message || tr("刪除紀錄失敗，請稍後再試。"));
    }
  }

  async function renderHistoryList(jobs, { container = els.historyList, append = false } = {}) {
    if (!container) return;
    const isDrawer = container === els.historyList;
    if (isDrawer && !append) exitHistorySelectMode();
    if (!append) {
      // Appending a later page must keep the earlier pages' tiles indexed
      // (multi-select/delete look them up by image_key) -- only a fresh
      // render (reset, or the demo-gallery's own non-drawer usage) starts
      // these over.
      historyJobsByKey.clear();
      historyTileElsByKey.clear();
      historyGroupTiles.delete(container);
      container.innerHTML = "";
    }
    if (!historyGroupTiles.has(container)) historyGroupTiles.set(container, new Map());
    const groupTiles = historyGroupTiles.get(container);
    if (isDrawer && !append) {
      // jobs.length here is only this page's count -- on an append call an
      // empty (no-more) page doesn't mean the drawer itself is empty, so
      // these only make sense to (re)evaluate on the first page.
      if (els.historyEmpty) els.historyEmpty.hidden = jobs.length > 0;
      if (els.historyClearAllBtn) els.historyClearAllBtn.hidden = jobs.length === 0;
      if (els.historySelectBtn) els.historySelectBtn.hidden = jobs.length === 0;
    }
    if (!jobs.length) return;

    // One heading + grid per calendar day, cube tiles instead of full-width
    // rows. Keep existing date groups available during append so a page
    // boundary in the middle of "今天"/"昨天" does not create a duplicate
    // heading for the same calendar day.
    let currentLabel = null;
    let currentGrid = null;
    const existingGrids = new Map();
    if (append) {
      Array.from(container.children).forEach((group) => {
        if (!group.classList.contains("history-day-group")) return;
        const heading = group.querySelector(".history-day-heading");
        const grid = group.querySelector(".history-grid");
        if (heading && grid) existingGrids.set(heading.textContent, grid);
      });
    }
    const rows = jobs.map((job) => {
      // A case of a batch from 對話模式 whose tile is already up (its
      // newest case came first, possibly on an earlier page) adds nothing.
      const group = job.image_key ? caseGroupsByKey.get(job.image_key) : null;
      const groupTile = group ? groupTiles.get(group.session_id) : null;
      if (groupTile) {
        groupTile.keys.add(job.image_key);
        return null;
      }
      const label = formatHistoryDayLabel(job.ts);
      if (label !== currentLabel) {
        currentLabel = label;
        currentGrid = existingGrids.get(label) || null;
        if (!currentGrid) {
          const group = document.createElement("div");
          group.className = "history-day-group";
          const heading = document.createElement("h3");
          heading.className = "history-day-heading";
          heading.textContent = label;
          group.appendChild(heading);
          currentGrid = document.createElement("ul");
          currentGrid.className = "history-grid";
          group.appendChild(currentGrid);
          container.appendChild(group);
          existingGrids.set(label, currentGrid);
        }
      }

      if (group) {
        const entry = { group, job, li: buildHistoryGroupTile(group, job), keys: new Set([job.image_key]) };
        groupTiles.set(group.session_id, entry);
        currentGrid.appendChild(entry.li);
        wireHistoryGroupTile(entry);
        return null;
      }

      const li = document.createElement("li");
      // history-cube layers the square/grid-cell layout on top of
      // batch-sidebar-item's existing status-driven styling (done/error/
      // processing colors, dimming) -- reused as-is rather than duplicated.
      const isAnalyzingJob = job.stage === "analyze";
      const isArchivedJob = archivedHistoryKeys.has(job.image_key);
      li.className = `history-cube batch-sidebar-item ${isArchivedJob ? "archived" : (isAnalyzingJob ? "analyzing" : "processing")}`;
      // Local-path jobs keep their srcKeys forever (see account.py), so the
      // original photo is available immediately -- no need to wait for the
      // status check below just to show *something* here. AIPoC-path jobs
      // have no recoverable original (same reason the before/after compare
      // skips them elsewhere), so those stay image-empty while processing.
      // Analyze-stage and edit-stage jobs both get the cube-level spinner;
      // the status response below removes/replaces it once a result is ready.
      const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
      const referenceThumb = job.path === "local" ? historyMediaMarkup(sourceThumbnailUrl(jobIds[0])) : "";
      const processingSpinner = '<div class="rc-spinner"></div>';
      li.innerHTML = `
        <div class="rc-media">${referenceThumb}${processingSpinner}</div>
        <span class="rc-badge" hidden></span>
        <button type="button" class="history-cube-remove" aria-label="${tr("刪除紀錄")}">×</button>
        <span class="history-cube-check">
          <svg width="12" height="12" fill="none" viewBox="0 0 24 24">
            <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </span>
      `;
      currentGrid.appendChild(li);
      if (job.image_key) historyTileElsByKey.set(job.image_key, li);
      wireHistoryTileRemove(li, job);
      return { job, li };
    });

    // Named (rather than inline in the Promise.all below) so a 美化中/分析中
    // tile can be re-rendered in place once its job settles -- see
    // watchHistoryTile.
    const renderTileStatus = async ({ job, li }) => {
      const badge = li.querySelector(".rc-badge");
      const media = li.querySelector(".rc-media");
      // job.runs holds every job_id from the same action (see
      // backend/services/account.py's action_id grouping) -- job.job_id is
      // the fallback for an entry recorded before `runs` existed.
      const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
      try {
        const statuses = await Promise.all(
          jobIds.map((id) => getJobStatusCached(id, { path: job.path }).catch((err) => ({ status: "error", error: err?.code, error_message: err?.message })))
        );
        // Same reasoning as terminalJobStatusCache above: a case already
        // known archived can't become compatible again (compatibility_version
        // never moves backward), so re-fetching its full result/analyze
        // payload here on every gallery render just to re-derive "yes,
        // still archived" is pure waste -- skip straight to the badge.
        const alreadyArchived = job.image_key && archivedHistoryKeys.has(job.image_key);
        if (!alreadyArchived && await historyJobNeedsArchiveBadge(job, jobIds, statuses)) {
          markHistoryCaseArchived(job);
        }
        // getJobStatusOnce already asks the backend for `error`/`error_message`
        // (see /status/<job_id>'s keyToGet) -- the failed-tile click handlers
        // below run that through the same friendlyError() wrapping used for
        // every other run-status error, instead of showing the raw
        // error_message (often a bare Python exception string) as-is.
        const failureReason = (s) => friendlyError({ message: s?.error_message || s?.error, code: s?.error });
        const archived = applyHistoryArchivedState(job, li);
        // Only revealed once the status check above has actually resolved --
        // showing it earlier would guess at a state (e.g. from job.stage
        // alone) that this same check can immediately overturn.
        if (badge) badge.hidden = false;
        // job.stage === "analyze" (see api.py's /analyze, account.py's
        // record_history) -- a paused-for-review upload (#start-btn's own
        // analyze-only flow, see .split-run in index.html) that
        // hasn't been continued past yet. Its one job_id's own "success"
        // means "analysis ready", not "edit done", so it needs its own
        // badge/handler instead of falling into the done/processing checks
        // below, which assume a real generated result.
        if (job.stage === "analyze") {
          const analyzed = statuses[0]?.status === "success";
          const failed = statuses[0]?.status === "error";
          if (failed) {
            li.className = `history-cube batch-sidebar-item ${archived ? "archived" : "error"}`;
            if (badge) badge.textContent = archived ? tr("可更新") : tr("失敗");
            media?.querySelector(".rc-spinner")?.remove();
            markHistoryTileSelectable(li, job);
            li.addEventListener("click", () => {
              if (historySelectMode) {
                if (job.image_key) toggleHistorySelection(job.image_key, li);
                return;
              }
              showNoticeModal(failureReason(statuses[0]));
            });
          } else {
            if (analyzed) {
              li.className = `history-cube batch-sidebar-item ${archived ? "archived" : "analyzed"}`;
              if (badge) badge.textContent = archived ? tr("可更新") : tr("待美化");
              media?.querySelector(".rc-spinner")?.remove();
            } else if (badge) {
              badge.textContent = archived ? tr("可更新") : tr("分析中");
            }
            li.addEventListener("click", async () => {
              if (li.classList.contains("opening")) return;
              if (!(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return;
              li.classList.add("opening");
              try {
                if (isAnalyzedAfterEdits(job)) await viewHistoryRecord(job);
                else await viewAnalyzeHistoryRecord(job, { alreadyAnalyzed: analyzed });
              } finally {
                li.classList.remove("opening");
              }
            });
          }
          return;
        }
        const doneIdx = statuses.findIndex((s) => s.status === "success");
        if (doneIdx !== -1) {
          li.className = "history-cube batch-sidebar-item done";
          markHistoryTileSelectable(li, job);
          applyHistoryArchivedState(job, li);
          const doneCount = statuses.filter((s) => s.status === "success").length;
          if (badge) badge.textContent = archived
            ? tr("可更新")
            : (jobIds.length > 1 ? tr("美化完成（{doneCount}/{length}）", { doneCount, length: jobIds.length }) : tr("美化完成"));
          // Thumbnail route only exists for the local path (see
          // photo-agent-api.js's resultThumbnailUrl) -- AIPoC-path jobs fall
          // back to the full-res image, same as before this change.
          const doneThumbSrc = job.path === "local"
            ? resultThumbnailUrl(jobIds[doneIdx])
            : resultImageUrl(jobIds[doneIdx], { path: job.path });
          if (media) media.innerHTML = historyMediaMarkup(doneThumbSrc);
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
            if (!(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return;
            li.classList.add("opening");
            const showingCanvas = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
            const hideSwitchLoading = showingCanvas ? showRunSwitchLoadingIfSlow() : () => {};
            try {
              await viewHistoryRecord(job);
            } finally {
              li.classList.remove("opening");
              hideSwitchLoading();
            }
          });
        } else if (statuses.every((s) => s.status === "error")) {
          li.className = `history-cube batch-sidebar-item ${archived ? "archived" : "error"}`;
          if (badge) badge.textContent = archived ? tr("可更新") : tr("失敗");
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
            if (historySelectMode) {
              if (job.image_key) toggleHistorySelection(job.image_key, li);
              return;
            }
            const failed = statuses.find((s) => s.status === "error") || statuses[0];
            showNoticeModal(failureReason(failed));
          });
        } else {
          if (badge) badge.textContent = archived ? tr("可更新") : tr("美化中");
          // Still clickable -- otherwise there's no way to come back and
          // check on a record that was still processing last time this list
          // was fetched (see viewProcessingHistoryRecord). The opening guard
          // mirrors the done branch above -- viewProcessingHistoryRecord
          // itself now handles its own errors, but this still guards against
          // a double-click re-entering it mid-poll.
          li.addEventListener("click", async () => {
            if (li.classList.contains("opening")) return;
            if (!(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return;
            li.classList.add("opening");
            try {
              await viewProcessingHistoryRecord(job);
            } finally {
              li.classList.remove("opening");
            }
          });
        }
      } catch (err) {
        const archived = applyHistoryArchivedState(job, li);
        if (badge) badge.hidden = false;
        li.className = `history-cube batch-sidebar-item ${archived ? "archived" : "error"}`;
        if (badge) badge.textContent = archived ? tr("可更新") : tr("無法取得狀態");
        markHistoryTileSelectable(li, job);
        li.addEventListener("click", () => {
          if (historySelectMode) {
            if (job.image_key) toggleHistorySelection(job.image_key, li);
            return;
          }
          showNoticeModal(err ? friendlyError(err) : tr("無法取得處理狀態，請稍後再試。"));
        });
      }
    };
    // Every status check here is a one-off, so a tile still in progress
    // would otherwise keep saying 美化中 until the whole list is reloaded.
    // Watched as soon as its own status is in, not after the whole list's
    // (which can take seconds longer on a first render).
    await Promise.all(rows.filter(Boolean).map(async (row) => {
      await renderTileStatus(row);
      if (historyTileInProgress(row.li)) watchHistoryTile({ ...row, container, rerender: renderTileStatus });
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

  // Same paginate-on-scroll shape as loadUploadHistoryPage (the upload
  // screen's "recent photos" gallery) -- getHistory(user) alone only ever
  // returns its default limit=50 (photo-agent-api.js), so without this the
  // drawer silently stopped at 50 entries with no way to reach anything
  // older, unlike that gallery's own unbounded scroll.
  const HISTORY_DRAWER_PAGE_SIZE = 50;
  let historyDrawerOffset = 0;
  let historyDrawerRequestToken = 0;
  let historyDrawerLoading = false;
  let historyDrawerHasMore = false;

  async function loadHistoryDrawerPage({ reset = false } = {}) {
    const user = getCurrentUser();
    if (!user || !els.historyList || historyDrawerLoading) return;
    const token = ++historyDrawerRequestToken;
    if (reset) {
      historyDrawerOffset = 0;
      historyDrawerHasMore = false;
    }
    historyDrawerLoading = true;
    try {
      const [page] = await Promise.all([
        getHistory(user, { limit: HISTORY_DRAWER_PAGE_SIZE, offset: historyDrawerOffset }),
        // A fresh render re-reads which cases are batches from 對話模式.
        loadCaseGroups({ refresh: historyDrawerOffset === 0 })
      ]);
      if (token !== historyDrawerRequestToken || getCurrentUser() !== user) return;
      const jobs = Array.isArray(page.jobs) ? page.jobs : [];
      await renderHistoryList(jobs, { append: !reset && historyDrawerOffset > 0 });
      historyDrawerOffset += jobs.length;
      historyDrawerHasMore = page.has_more === true;
    } catch {
      if (token !== historyDrawerRequestToken) return;
      historyDrawerHasMore = false;
      if (!historyDrawerOffset) renderHistoryList([]);
    } finally {
      if (token === historyDrawerRequestToken) historyDrawerLoading = false;
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
    await loadHistoryDrawerPage({ reset: true });
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

  // Page size for the admin reports drawer (loadReportsPage) -- also what
  // checkAdminAccess/pollForNewReports fetch as their own first page.
  const REPORTS_PAGE_SIZE = 50;

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
    if (!user) {
      syncReportBtnPosition();
      return;
    }
    try {
      const { reports } = await getReports(user, { limit: REPORTS_PAGE_SIZE });
      if (els.adminReportsBtn) els.adminReportsBtn.hidden = false;
      // This call is also the baseline for pollForNewReports below -- a
      // report that already existed before this session started shouldn't
      // itself count as "new" the moment polling kicks in.
      seedKnownReports(reports);
      startReportsPolling();
    } catch {
      // 403 (not an admin) or a request failure -- either way, stay hidden.
    } finally {
      syncReportBtnPosition();
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

  // Same page-on-scroll shape as loadHistoryDrawerPage -- the drawer used
  // to fetch (and render a row + thumbnail for) every report ever filed in
  // one go.
  let reportsOffset = 0;
  let reportsRequestToken = 0;
  let reportsLoading = false;
  let reportsHasMore = false;

  // reset: true (drawer open/refresh) replaces the list and resets the
  // poll's baseline; otherwise appends the next page. Returns false only
  // when a reset load fails, for refreshReportsList's notice.
  async function loadReportsPage({ reset = false } = {}) {
    const user = resolveAdminUser();
    if (!user || !els.reportsList) return true;
    if (reportsLoading && !reset) return true;
    const token = ++reportsRequestToken;
    if (reset) {
      reportsOffset = 0;
      reportsHasMore = false;
    }
    reportsLoading = true;
    try {
      const page = await getReports(user, { limit: REPORTS_PAGE_SIZE, offset: reportsOffset });
      if (token !== reportsRequestToken) return true;
      const reports = Array.isArray(page.reports) ? page.reports : [];
      renderReportsList(reports, { append: !reset });
      reportsOffset += reports.length;
      reportsHasMore = page.has_more === true;
      if (reset) {
        // Fresh data just loaded -- resets pollForNewReports' baseline and
        // clears whatever alert brought the admin in here in the first place.
        seedKnownReports(reports);
        setNewReportsAlert(false);
      } else if (knownReportIds) {
        reports.forEach((r) => knownReportIds.add(r.report_id));
      }
      return true;
    } catch {
      if (token !== reportsRequestToken) return true;
      reportsHasMore = false;
      if (reset) renderReportsList([]);
      return !reset;
    } finally {
      if (token === reportsRequestToken) reportsLoading = false;
    }
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
      // First page only -- a new report is always the newest (ts), so it
      // always lands there.
      const { reports } = await getReports(user, { limit: REPORTS_PAGE_SIZE });
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
    return tr("{v1}月{v2}日 {v3}:{v4}", { v1: d.getMonth() + 1, v2: d.getDate(), v3: pad(d.getHours()), v4: pad(d.getMinutes()) });
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
  const REPORT_STATUS_LABELS = { unread: tr("未讀"), read: tr("待解決"), solved: tr("已解決") };
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
    statusBtn.setAttribute("aria-label", tr("回報狀態：{v1}（點擊切換）", { v1: REPORT_STATUS_LABELS[status] || status }));
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
    const confirmed = await showConfirmModal(tr("確定要刪除這筆回報嗎？"));
    if (!confirmed) return;
    try {
      await deleteReport(user, r.report_id);
      li.remove();
      // Everything after it shifted up one -- keeps the next page's offset
      // from skipping a report.
      if (reportsOffset > 0) reportsOffset -= 1;
      if (els.reportsList && els.reportsEmpty) {
        els.reportsEmpty.hidden = els.reportsList.children.length > 0;
      }
    } catch (err) {
      showNoticeModal(err.message || tr("刪除回報失敗，請稍後再試。"));
    }
  }

  // One row per flagged job -- clicking a row reuses viewHistoryRecord
  // (the same history-entry playback the processing-history drawer uses)
  // against a minimal {path, runs} stand-in, since a report only ever
  // records job_id/path, not the rest of a real history entry's fields
  // (preset/image_quality/...) -- viewHistoryRecord already falls back to
  // sensible defaults for those when absent.
  const REPORT_VERSION_PARAM_KEYS = [
    "preset", "image_quality", "image_model", "analysis_model", "aspect_ratio", "output_scale",
    "ai_photo_editing", "run_qa", "single_pass_edit", "overwrite_prompt", "advanced_options"
  ];

  function pickVersionParams(params) {
    const picked = {};
    REPORT_VERSION_PARAM_KEYS.forEach((key) => {
      if (params[key] != null) picked[key] = params[key];
    });
    return picked;
  }

  // Opens a job-tied report's result page -- a row click in the drawer
  // below, or the chat page's read-only report view through #report=<id>
  // (openReportFromHash). Resolves to whether the record made it on screen.
  async function openReportInPanel(r, statusBtn) {
    // Fire-and-forget -- opening a still-unread report marks it read,
    // same as any inbox; never awaited since it must not delay
    // opening the actual result the admin clicked through for.
    if ((r.status || "unread") === "unread") setReportStatus(r, statusBtn, "read");
    // revealDetailExtra: true -- an admin checking a flagged case
    // should see the full QA verdict/recipe-adherence/prompts panel,
    // normally debug-only, for this specific record. user_prompt:
    // r.user_prompt -- a real history entry carries its own
    // job.user_prompt (see restoreReviseSelections), but this
    // minimal report-derived stand-in has nowhere else to get it
    // from; without this, #prompt would come back empty even though
    // the report captured the original 補充指令.
    //
    // A report whose results expired leaves the admin where they were
    // (with viewHistoryRecord's notice) instead of on a stale page.
    if (!(await confirmDropReview(DROP_REVIEW_FOR_PHOTO))) return false;
    // The version's own settings (version_params) fill the panel the
    // way a real history record would -- picked key by key, never
    // stage, which isAnalyzedAfterEdits reads off this same object.
    const versionParams = r.version_params || {};
    if (r.analyze_job_id) {
      // Reported while the photo was only analyzed (對話模式's analysis
      // stage): no edit result to open, so it opens like a 待美化 case.
      return openAnalyzedReport({
        job_id: r.analyze_job_id,
        stage: "analyze",
        path: r.path || "local",
        ...pickVersionParams(versionParams),
        user_prompt: versionParams.user_prompt || r.user_prompt,
        uploaded_at: r.uploaded_at,
        modified_at: r.modified_at
      });
    }
    return viewHistoryRecord({
      path: r.path,
      runs: [r.job_id],
      ...pickVersionParams(versionParams),
      user_prompt: versionParams.user_prompt || r.user_prompt,
      uploaded_at: r.uploaded_at,
      modified_at: r.modified_at
    }, { revealDetailExtra: true, report: r });
  }

  // viewAnalyzeHistoryRecord only settles once the admin acts on the review
  // (runPendingReviewFlow), so this resolves as soon as the page leaves the
  // upload section instead -- or with false if it never got there.
  function openAnalyzedReport(job) {
    return new Promise((resolve) => {
      const onSection = (e) => {
        if (e.detail?.name === "upload") return;
        document.removeEventListener("photoagent:sectionchange", onSection);
        resolve(true);
      };
      document.addEventListener("photoagent:sectionchange", onSection);
      viewAnalyzeHistoryRecord(job).finally(() => {
        document.removeEventListener("photoagent:sectionchange", onSection);
        resolve(currentSectionName !== "upload");
      });
    });
  }

  function renderReportsList(reports, { append = false } = {}) {
    if (!els.reportsList) return;
    if (!append) els.reportsList.innerHTML = "";
    reports.forEach((r) => {
      const li = document.createElement("li");
      li.className = "report-item";

      const thumb = document.createElement("div");
      thumb.className = "report-item-thumb";
      // r.job_id is absent for a general report (#report-btn is clickable
      // with nothing on screen -- record_report, backend/services/report.py);
      // a chat one still has r.thumbnail when its conversation is known
      // (_chat_report_thumbnail, api.py). r.analyze_job_id: the job is only
      // an analysis, with no result to thumbnail -- show its original. An
      // AIPoC-path report has no thumbnail route to use.
      const thumbSrc = r.path !== "local" ? null
        : r.analyze_job_id ? sourceThumbnailUrl(r.analyze_job_id)
        : r.job_id ? resultThumbnailUrl(r.job_id)
        : !r.thumbnail ? null
        : r.thumbnail.run ? resultThumbnailUrl(r.thumbnail.job_id, { run: r.thumbnail.run })
        : sourceThumbnailUrl(r.thumbnail.job_id);
      if (thumbSrc) {
        const img = document.createElement("img");
        img.alt = "";
        img.src = thumbSrc;
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
      // r.source === "chat": filed from the chat UI (/chat/), which only runs
      // the local path, so its mode says more than "Local" would.
      const where = r.source === "chat" ? tr("聊天模式") : r.path === "aipoc" ? "AIPoC" : "Local";
      metaLine.textContent = r.job_id
        ? `${formatReportTimestamp(r.ts)} · ${where}`
        : tr("{v1} · 一般回報", { v1: formatReportTimestamp(r.ts) }) + (r.source === "chat" ? ` · ${tr("聊天模式")}` : "");
      info.appendChild(userLine);
      info.appendChild(metaLine);
      // r.owner is the account the chat session lives under (record_report,
      // backend/services/report.py); r.user is just the name typed into the
      // report form, so it only needs saying when the two differ.
      if (r.owner && r.owner !== r.user) {
        const ownerLine = document.createElement("span");
        ownerLine.className = "report-item-note";
        ownerLine.textContent = tr("對話帳號：{owner}", { owner: r.owner });
        info.appendChild(ownerLine);
      }
      // r.user_prompt (what the original submission actually asked for) is
      // distinct from r.note (the reporter's own description of what's
      // wrong) -- shown as its own labeled line so the two aren't confused.
      // r.version_params: the settings r.job_id's version was made with
      // (get_reports, backend/services/report.py) -- a chat report rarely
      // carries its own user_prompt, the version it points at does.
      const versionParams = r.version_params || {};
      const userPrompt = versionParams.user_prompt || r.user_prompt;
      if (userPrompt) {
        const promptLine = document.createElement("span");
        promptLine.className = "report-item-note";
        promptLine.textContent = tr("個人化需求：{user_prompt}", { user_prompt: userPrompt });
        info.appendChild(promptLine);
      }
      if (r.note) {
        const noteLine = document.createElement("span");
        noteLine.className = "report-item-note";
        noteLine.textContent = tr("回報說明：{note}", { note: r.note });
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
        screenshotBtn.textContent = tr("查看回報截圖");
        screenshotBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          openReportScreenshotModal(reportScreenshotUrl(r.report_id));
        });
        info.appendChild(screenshotBtn);
      }

      // A report that knows its conversation (owner + session_id, or just
      // batch_id when filed from a batch overview; record_report) -- a chat
      // report, or a panel one whose case has a conversation (api.py's
      // _panel_case_chat) -- opens it read-only on the chat page, in a new tab
      // so this list stays where it was. An <a> with the screenshot link's
      // look: it navigates.
      if (r.owner && (r.session_id || r.batch_id)) {
        const chatLink = document.createElement("a");
        chatLink.className = "report-item-screenshot-link";
        chatLink.href = `chat/${location.search}#report=${encodeURIComponent(r.report_id)}`;
        chatLink.target = "_blank";
        chatLink.rel = "noopener";
        chatLink.textContent = tr("查看對話");
        chatLink.addEventListener("click", (e) => e.stopPropagation());
        info.appendChild(chatLink);
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
      removeBtn.setAttribute("aria-label", tr("刪除回報"));
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
        li.addEventListener("click", async () => {
          if (await openReportInPanel(r, statusBtn)) closeReportsDrawer();
        });
      } else {
        li.classList.add("is-static");
      }
      els.reportsList.appendChild(li);
    });
    if (els.reportsEmpty) els.reportsEmpty.hidden = els.reportsList.children.length > 0;
  }

  async function openReportsPage() {
    const user = resolveAdminUser();
    if (!user) return;
    openReportsDrawer();
    await loadReportsPage({ reset: true });
  }

  // Reloads the list in place without closing/reopening the drawer -- the
  // manual counterpart to openReportsPage's own fetch, for an admin who's
  // already in the drawer when pollForNewReports raises the alert on
  // #reports-refresh-btn (see setNewReportsAlert).
  async function refreshReportsList() {
    const user = resolveAdminUser();
    if (!user) return;
    if (!(await loadReportsPage({ reset: true }))) {
      showNoticeModal(tr("重新載入回報清單失敗，請稍後再試。"));
    }
  }

  // A live single-image session never re-renders the recipe checklist from a
  // filtered analysis -- singleFlowState.analysis is pinned to the original,
  // untouched analyze-stage result for the whole session, so retouch_recipe
  // never shrinks and lastSingleSelections.recipeChecked always stays index-
  // aligned to it. Historical playback (viewHistoryRecord) has no choice but
  // to start from result.analysis, which -- if the last revise unchecked
  // anything -- is already that filtered, shrunk version (apply_selections
  // drops unchecked items from correction_plan/retouch_recipe entirely before
  // the edit runs). Left as-is, a previously-unchecked item would just be gone
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
    return {
      analysis: restoredAnalysis,
      selections: {
        userPrompt: userPrompt || "",
        overwritePrompt: !!overwritePrompt,
        options,
        recipeChecked,
        bodyReshapeEnabled: analysis?._body_reshape_enabled !== false
      }
    };
  }

  // One version's result images. A "Run" click with run_count > 1 (two-stage
  // manual path) submits N *separate* job_ids (runSingleItem/editBatchItem),
  // all recorded together under that one version's `runs` list by
  // backend/services/account.py's action_id grouping -- fetch every one of
  // them (each of which might itself be a combined job with its own internal
  // multi-RUN shape too) and flatten, same shape either way.
  async function fetchVersionRuns(jobIds, path) {
    const perJobData = await Promise.all(jobIds.map((id) => getJobRuns(id, { path }).catch(() => null)));
    const flat = [];
    perJobData.forEach((data, idx) => {
      if (!data) return;
      const jobId = jobIds[idx];
      const rawRuns = Array.isArray(data.runs) && data.runs.length
        ? data.runs
        : [{ image_url: data.image_url, result: data.result }];
      rawRuns.forEach((r, ri) => {
        if (!r.result) return; // this job/RUN hasn't actually finished
        // sourceRun is which RUN of its OWN job this image is -- the only
        // thing /result/<job>/image?run=N accepts. Distinct from the `run`
        // numberHistoryRuns assigns, which counts across the whole list and
        // therefore across separate job_ids. They agree only for a single
        // combined job, which is why this went unnoticed while that was the
        // only multi-image shape the results page ever saw.
        flat.push({ jobId, path, sourceRun: r.run != null ? r.run : ri + 1, imageUrl: r.image_url, result: r.result, status: "done" });
      });
    });
    return flat;
  }

  // Re-numbers a flat run list sequentially regardless of each job's own
  // internal RUN numbering -- these may come from entirely separate job_ids
  // that each think of themselves as "RUN 1".
  function numberHistoryRuns(flat) {
    return flat.map((r, i) => ({ run: i + 1, status: "done", timings: [], error: null, ...r }));
  }

  // Every version of one case flattened into the single list the prev/next
  // switcher walks -- oldest version first, so stepping right moves forward
  // in time (viewHistoryRecord starts on the newest).
  //
  // Only the newest version's images are fetched up front. Every other
  // version contributes placeholders (status "unloaded") carrying just the
  // job_id/RUN needed to go get them, which loadHistoryRun resolves when the
  // user actually steps onto one. That split is the whole point of the
  // endpoint: a case with 30 versions would otherwise cost 30 /result
  // fetches -- each a full analysis + quality-check payload -- every time it
  // is opened, for images nobody may ever look at.
  //
  // Returns null (not an empty list) for anything this can't describe --
  // no account, a backend without the /case endpoint, or a case whose
  // results have expired out of Redis -- so the caller falls back to the
  // summary's own `runs` rather than showing an empty switcher.
  async function fetchHistoryVersionRuns(job) {
    const user = getCurrentUser();
    if (!user || !job.image_key) return null;
    let versions = null;
    try {
      const data = await getHistoryCase(user, { imageKey: job.image_key });
      versions = data && data.case ? data.case.versions : null;
    } catch (error) {
      return null;
    }
    if (!Array.isArray(versions) || !versions.length) return null;
    const shown = browsableVersions(versions);
    if (!shown.length) return null;
    const latest = shown[shown.length - 1];
    const latestRuns = await fetchVersionRuns(latest.runs || [], latest.path || job.path);
    return buildVersionRunList(shown, latestRuns, job.path, job.image_key);
  }

  // An analyze-only version is a real part of the case's history (it is the
  // upload every later edit descends from) but has no result image, so there
  // is nothing for an image switcher to land on.
  function browsableVersions(versions) {
    return versions.filter((v) => v.kind !== "analyze" && Array.isArray(v.results) && v.results.length);
  }

  // versions (oldest first) + the already-fetched runs of the newest one ->
  // the flat, run-numbered list both the arrows and the version strip walk.
  // Every version but the newest contributes placeholders; see
  // fetchHistoryVersionRuns for why.
  function buildVersionRunList(shown, latestRuns, fallbackPath, caseKey) {
    if (!shown.length || !latestRuns.length) return null;
    const latest = shown[shown.length - 1];
    const flat = [];
    const fromNumber = (version) => {
      const sourceId = version.params?.source_result?.version_id;
      const index = sourceId ? shown.findIndex((v) => v.version_id === sourceId) : -1;
      return index >= 0 ? index + 1 : null;
    };
    shown.forEach((version, vi) => {
      const versionPath = version.path || fallbackPath;
      const entries = version === latest ? latestRuns : version.results.map((r) => ({
        jobId: r.job_id,
        path: versionPath,
        // Which RUN of its own job this image is -- kept separate from the
        // `run` numberHistoryRuns assigns below, which counts across the
        // whole case.
        sourceRun: r.run,
        imageUrl: null,
        result: null,
        status: "unloaded",
      }));
      entries.forEach((entry, ri) => {
        flat.push({
          ...entry,
          versionId: version.version_id,
          versionNumber: vi + 1,
          versionTotal: shown.length,
          // What this version was actually run with -- the per-case settings
          // button reads it back out when the user steps onto this version
          // (applyHistoryVersionContext).
          versionParams: version.params || null,
          versionCreatedAt: version.created_at || null,
          // A 接著這張調整 version: which version it was made from (null when
          // that one is gone), and the chat's note on it, if one was written.
          versionFrom: fromNumber(version),
          versionNote: version.note || null,
          // The case the version belongs to -- ensureVersionNote asks for the
          // note of a version the chat never wrote one for by it.
          caseKey: caseKey || null,
          runInVersion: ri + 1,
          runsInVersion: entries.length,
        });
      });
    });
    return numberHistoryRuns(flat);
  }

  // Fetches one case and returns its browsable versions only if that case
  // really is the one `jobId` just landed in -- its newest version has to
  // contain that job. The check is what makes the several ways of naming a
  // case below safe to just try in turn: a case key survives until the next
  // case is opened, so it can be stale, and answering with someone else's
  // history would file this result as a version of it.
  async function caseVersionsHolding(user, lookup, jobId) {
    let data;
    let versions;
    try {
      data = await getHistoryCase(user, lookup);
      versions = data && data.case ? data.case.versions : null;
    } catch (error) {
      return null; // no history row, or a backend that doesn't take this lookup
    }
    if (!Array.isArray(versions)) return null;
    const shown = browsableVersions(versions);
    const latest = shown.length ? shown[shown.length - 1] : null;
    if (!latest || !(latest.runs || []).includes(jobId)) return null;
    return { shown, latest, caseKey: data.case.image_key || lookup.imageKey || null };
  }

  // Which case does a just-finished job belong to? Three ways in, tried until
  // one comes back actually holding it:
  //   1. currentCaseHistoryImageKey -- set whenever a case is opened from
  //      history (setCurrentCaseTimes). Null for a photo uploaded and edited
  //      in this same session, which never had a case key on the client.
  //   2. the /case endpoint's job_id form, which resolves through the
  //      backend's job index in one lookup.
  //   3. the plain history list. Each summary carries image_key and its
  //      latest version's job ids -- and the edit that just finished IS that
  //      latest version, so the case is findable there. Slower (it reads
  //      every case's summary) and therefore last, but it asks nothing of the
  //      backend that was not already there, which is what keeps case 1's
  //      fresh upload working against a deployment predating (2).
  async function findCaseForJob(user, jobId) {
    const direct = [];
    if (currentCaseHistoryImageKey) direct.push({ imageKey: currentCaseHistoryImageKey });
    direct.push({ jobId });
    for (const lookup of direct) {
      const found = await caseVersionsHolding(user, lookup, jobId);
      if (found) return found;
    }
    let jobs;
    try {
      ({ jobs } = await getHistory(user, { limit: HISTORY_DRAWER_PAGE_SIZE }));
    } catch (error) {
      return null;
    }
    // Newest first, and this case's timestamp was just bumped by the edit --
    // so it is at the front of the first page, never off the end of it.
    const match = (jobs || []).find((entry) => (entry.runs || []).includes(jobId));
    return match && match.image_key
      ? caseVersionsHolding(user, { imageKey: match.image_key }, jobId)
      : null;
  }

  // Puts the case's whole history back around a result that was just
  // produced on this page. finishSingleFlow leaves singleRuns holding only
  // the live run it just built -- untagged, so the version strip disappears
  // at exactly the moment a new version has been created and there is most
  // to compare against.
  //
  // Resolved by the new job's id, not a case key: a photo uploaded and
  // edited in this same session never had one on the client. Bails without
  // touching anything unless the case really does have more than one version
  // -- a first-ever edit has nothing to show, and leaving the live list
  // alone there keeps its per-RUN timings, which a re-fetch would drop.
  //
  // pendingAnalysis: called after 幫我分析 instead (analyzeSkippedCase). The
  // analysis is the case's newest version but has no image of its own, so it
  // is the strip's trailing 待美化 tile (versionStripPendingRuns) rather
  // than an entry in the list -- and one edit before it is already enough to
  // show the strip for.
  async function refreshVersionsAfterEdit({ pendingAnalysis = false } = {}) {
    // singleRuns is null for the ordinary single-RUN edit -- that path never
    // builds a run list at all (see finishSingleFlow's runCount === 1
    // branch), and lastResult is the only record of what it just produced.
    // It is also the path most edits take, so it is the one that matters
    // most here.
    const live = singleRuns && singleRuns.length ? singleRuns : null;
    if (live && live.some((r) => r.versionId)) { // already showing versions
      if (pendingAnalysis) {
        versionStripPendingRuns = live;
        versionStripRuns = null; // same list, one more tile: rebuild the strip
        renderSingleRunSwitcher();
      }
      return;
    }
    const user = getCurrentUser();
    const anchor = live
      ? (live[singleMirrorIndex] || live.find((r) => r.status === "done") || live[0])
      : (lastResult && lastResult.jobId
          ? { jobId: lastResult.jobId, sourceRun: lastResult.sourceRun, run: lastResult.runNumber || 1 }
          : null);
    if (!user || !anchor || !anchor.jobId) return;

    const found = await findCaseForJob(user, anchor.jobId);
    if (found && !pendingAnalysis) ensureVersionNote(found.caseKey, found.latest.version_id);
    if (!found || found.shown.length < (pendingAnalysis ? 1 : 2)) return;
    const { shown, latest, caseKey } = found;
    const latestRuns = await fetchVersionRuns(latest.runs || [], latest.path || "local");
    const rebuilt = buildVersionRunList(shown, latestRuns, "local", caseKey);
    // Superseded while fetching -- the user opened a different record, or a
    // further edit replaced the list this was computed against.
    if (!rebuilt || (singleRuns && singleRuns.length ? singleRuns : null) !== live) return;
    // Carry the just-finished run's step timings across -- they exist only in
    // memory, and the re-fetched entries above came back without them.
    if (live) {
      rebuilt.forEach((entry) => {
        const match = live.find((r) => r.jobId === entry.jobId && (r.sourceRun || r.run) === entry.sourceRun);
        if (match && match.timings && match.timings.length) entry.timings = match.timings;
      });
    }
    singleRuns = rebuilt;
    if (pendingAnalysis) versionStripPendingRuns = rebuilt;
    const sameImage = rebuilt.findIndex((r) => r.jobId === anchor.jobId
      && r.sourceRun === (anchor.sourceRun || anchor.run));
    singleMirrorIndex = sameImage >= 0
      ? sameImage
      : rebuilt.findIndex((r) => r.versionId === latest.version_id);
    renderSingleRunSwitcher();
  }

  // allVersions: true walks the case's whole edit history (viewHistoryRecord);
  // false keeps the pre-v3 behaviour of returning only the latest version's
  // results, which is still what the bulk-zip download builds its folders
  // from. job.job_id is the fallback for a history entry recorded before
  // `runs` existed.
  async function fetchAllHistoryRuns(job, { allVersions = false } = {}) {
    if (allVersions) {
      const withVersions = await fetchHistoryVersionRuns(job);
      if (withVersions) return withVersions;
    }
    const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
    return numberHistoryRuns(await fetchVersionRuns(jobIds, job.path));
  }

  // Fills in one "unloaded" switcher entry (see fetchHistoryVersionRuns) in
  // place. A single /result fetch covers every entry sharing that job_id -- a
  // run_count>1 combined job returns all of its RUNs at once -- so siblings
  // are filled from the same response rather than re-fetched one at a time
  // when the user steps through them.
  async function loadHistoryRun(runs, run) {
    let data;
    try {
      data = await getJobRuns(run.jobId, { path: run.path || "local" });
    } catch (error) {
      run.status = "error";
      run.error = friendlyError(error);
      return false;
    }
    const rawRuns = Array.isArray(data.runs) && data.runs.length
      ? data.runs
      : [{ run: 1, image_url: data.image_url, result: data.result }];
    runs.forEach((sibling) => {
      if (sibling.jobId !== run.jobId || sibling.status !== "unloaded") return;
      // A job that reported no RUN numbering at all (the single-result shape
      // above) has exactly one image, and it is this sibling's.
      const match = rawRuns.find((r) => r.run === sibling.sourceRun)
        || (rawRuns.length === 1 ? rawRuns[0] : null);
      if (!match || !match.result) return;
      sibling.imageUrl = match.image_url;
      sibling.result = match.result;
      sibling.status = "done";
    });
    if (run.status !== "done") {
      // The history row outlived the job's result in Redis. Marking it errored
      // (rather than leaving it "unloaded") stops the arrows offering it again.
      run.status = "error";
      run.error = tr("這一版的結果已經無法取得。");
      return false;
    }
    return true;
  }

  // Resumes a paused-for-review upload (#start-btn's own analyze-only flow)
  // that had only reached /analyze last time the history list was fetched --
  // job.stage ===
  // "analyze" flags this (see api.py's /analyze, account.py's
  // record_history). Shows the same "正在分析照片" placeholder if analysis
  // hasn't finished yet, then reconstructs singleFlowState from the entry's
  // stored params and re-opens the review panel exactly where startSubmit's
  // own manual path would have left it, via the same runPendingReviewFlow
  // both now share. historyViewToken guards this the same way
  // viewProcessingHistoryRecord does -- see its own comment.
  async function viewAnalyzeHistoryRecord(job, { alreadyAnalyzed = false } = {}) {
    const myToken = ++historyViewToken;
    setCurrentCaseTimes(job);
    const analyzeJobId = Array.isArray(job.runs) && job.runs.length ? job.runs[0] : job.job_id;
    const previewUrl = sourceImageUrl(analyzeJobId);
    currentBatchRecord = null;
    showCaseGroupSidebar(job.image_key);
    if (els.showPromptBtn) els.showPromptBtn.hidden = true;
    if (els.costBtn) els.costBtn.hidden = true;
    if (els.canvasLoading) els.canvasLoading.hidden = true;
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
    if (!alreadyAnalyzed) {
      // This tile is clickable while the analyze job is still running (badge
      // "分析中") -- unlike viewProcessingHistoryRecord's own upfront
      // showBatchMainLoading, nothing here used to switch the visible section
      // until AFTER pollJobStatus below resolved, so a click on a
      // still-analyzing tile looked like a dead button for however long that
      // poll took. Navigate immediately instead. An already-analyzed tile
      // (badge "待修圖") skips this: pollJobStatus resolves for it almost
      // instantly, so flashing this "still analyzing" placeholder first would
      // just be a needless extra transition before the real review panel.
      const thumbUrl = sourceThumbnailUrl(analyzeJobId);
      // Waits for the small thumbnail itself (not previewUrl above, which is
      // the full-size source image) to finish loading BEFORE navigating, so
      // the placeholder and its photo appear together instead of the page
      // jumping first and the thumbnail visibly popping in a moment later.
      await preloadImage(thumbUrl);
      if (historyViewToken !== myToken) return; // navigated away while the thumbnail loaded
      showSection("results");
      showBatchMainLoading({ previewUrl, thumbUrl, status: "analyzing" });
      closeHistoryDrawer();
    }

    try {
      await pollJobStatus(analyzeJobId, { path: "local" });
    } catch (error) {
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      showBatchMainError({ previewUrl, error: friendlyError(error) });
      return;
    }
    if (historyViewToken !== myToken) return; // navigated away meanwhile

    let analysis;
    try {
      const analyzeResult = await getAnalyzeResult(analyzeJobId);
      analysis = analyzeResult.result.analysis;
      // compatible missing (older backend) is treated as compatible -- the
      // real gate is finishSingleFlow's own isCompatible check (shown once
      // the user actually presses Run, not here on page entry -- this screen
      // has no finished photo yet for a read-only view to show instead, so
      // there's nothing to gain by bouncing them before they've even looked).
      setCurrentCaseAnalysisVersion(analysis, { compatible: analyzeResult.result.compatible });
      if (analyzeResult.result.compatible === false || !isCompatible(analysis)) {
        markHistoryCaseArchived(job);
      }
    } catch (error) {
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      showBatchMainError({ previewUrl, error: friendlyError(error) });
      return;
    }
    if (historyViewToken !== myToken) return; // navigated away meanwhile

    const presetKey = job.preset || "natural";
    const preset = presets[presetKey] || presets.natural;
    selectedFile = null;
    selectedFileSourceUrl = previewUrl;
    originalImgThumbUrl = sourceThumbnailUrl(analyzeJobId);
    singleFlowState = {
      analysis,
      analyzeJobId,
      presetKey,
      preset,
      imageQuality: job.image_quality || "medium",
      imageModel: job.image_model || "gpt-image-2",
      analysisModel: job.analysis_model || "gpt-5.6-luna",
      aspectRatio: job.aspect_ratio || "auto",
      outputScale: findOutputScaleOption(job.output_scale)?.value,
      aiPhotoEditing: job.ai_photo_editing != null ? job.ai_photo_editing : true,
      runQa: job.run_qa != null ? job.run_qa : true,
      singlePassEdit: resolvedSinglePassEdit(job.single_pass_edit),
      advancedOptions: job.advanced_options || {},
      processingPath: "local",
      analyzeTimings: [],
      useCombinedJob: false
    };
    applySinglePassEditOption(singleFlowState.singlePassEdit);
    // Same reason viewHistoryRecord syncs these (see its own comment there):
    // the results page's pickers otherwise keep showing whatever the last
    // case -- or the upload page, before this split gave each its own copy --
    // had in them, while singleFlowState already holds THIS record's own
    // parameters, restored from its job above. Display-only: singleFlowState
    // is correct as-is and finishSingleFlow reads the four settings from it,
    // but 人像設定 IS read back off the DOM (applyAdvancedOptionsChange),
    // so leaving it stale changed what a revise of
    // this record would actually send.
    applyModelQualityOption(findModelQualityOptionByPair(singleFlowState.imageModel, singleFlowState.imageQuality));
    applyAnalysisModelOption(findAnalysisModelOption(singleFlowState.analysisModel));
    applyAspectRatioOption(findAspectRatioOption(singleFlowState.aspectRatio));
    applyOutputScaleOption(findOutputScaleOption(singleFlowState.outputScale));
    applyAdvancedOptionsOption(
      recordAdvancedOptions(singleFlowState.advancedOptions, singleFlowState.analysis), "ap");
    // #prompt / #prompt-overwrite are shared with the results page's
    // revise panel, which fills them from whatever record it last showed
    // (backToProcessFromResults). finishSingleFlow reads them straight off
    // the DOM, so without this a 待美化 record opened after another record
    // showed -- and on 美化 actually sent -- that record's 個人化需求.
    if (els.promptInput) els.promptInput.value = job.user_prompt || "";
    if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = !!job.overwrite_prompt;

    await setImgSrcPreloaded(els.originalImg, selectedFileSourceUrl);
    if (historyViewToken !== myToken) return; // navigated away meanwhile
    els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
    showSection("results");
    // showPendingResults will now switch to results only after the source
    // image has finished preloading, so the upload page remains intact until
    // the review view can be painted without a broken image flash.
    closeHistoryDrawer();
    try {
      await runPendingReviewFlow(analysis, preset);
    } catch (error) {
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      showBatchMainError({ previewUrl, error: friendlyError(error) });
    }
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
    setCurrentCaseTimes(job);
    // job.job_id doesn't exist on a history entry (see record_history/
    // account.py) -- every job from the same action lives in job.runs
    // instead, with job.job_id kept only as a fallback for an entry recorded
    // before that field existed.
    const jobIds = Array.isArray(job.runs) && job.runs.length ? job.runs : [job.job_id];
    const previewUrl = job.path === "local" ? sourceImageUrl(jobIds[0]) : "";
    const thumbUrl = job.path === "local" ? sourceThumbnailUrl(jobIds[0]) : "";
    // Waits for the small thumbnail itself (not previewUrl above, which is
    // the full-size source image) to finish loading BEFORE navigating, so
    // the loading placeholder and its photo appear together instead of the
    // page jumping first and the thumbnail visibly popping in a moment later.
    await preloadImage(thumbUrl);
    if (historyViewToken !== myToken) return; // navigated away while the thumbnail loaded

    showSection("results");
    currentBatchRecord = null;
    showCaseGroupSidebar(job.image_key);
    pendingResultsActive = false; // see viewHistoryRecord
    // No result -- and so no prompts -- yet for #show-prompt-btn to show.
    if (els.showPromptBtn) els.showPromptBtn.hidden = true;
    if (els.costBtn) els.costBtn.hidden = true;
    // In case some OTHER record's live revise (finishSingleFlow) left these
    // toggled -- showBatchMainLoading below hides #results-canvas-row
    // entirely (its parent), so this is mostly cheap insurance, not load-
    // bearing here the way it is in viewHistoryRecord.
    if (els.canvasLoading) els.canvasLoading.hidden = true;
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;
    // aiPhotoEditing/imageModel: batchSettings (what showBatchMainLoading's
    // rotating status text would otherwise fall back to) is only populated
    // during an actual multi-photo batch run, never for a single history
    // record resumed here -- see showBatchMainLoading's own comment.
    const loadingRecord = {
      previewUrl,
      thumbUrl,
      status: "processing",
      aiPhotoEditing: job.ai_photo_editing,
      imageModel: job.image_model,
      userPrompt: job.user_prompt,
      overwritePrompt: job.overwrite_prompt
    };
    showBatchMainLoading(loadingRecord);
    // Navigation to the loading view above is already visible now, so close
    // the drawer immediately instead of waiting for an actual result to land
    // further down (inside renderIfPossible/viewHistoryRecord): that deferred
    // close doesn't run on every path out of this function (an early-return
    // guard, viewHistoryRecord bailing on a still-catching-up /result fetch,
    // etc.), which silently left the drawer open on top of an already-
    // navigated page.
    closeHistoryDrawer();

    // A combined multi-RUN job (runCombinedMultiRun) is ONE job_id covering
    // every RUN (job_processor.py runs finish_stage `run_count` times inside
    // it), unlike the two-stage manual path's N *separate* job_ids below --
    // resume watching it incrementally (same watchCombinedJobRuns the live
    // in-page flow uses) instead of only finding out once EVERYTHING is
    // done, so the progress bar/RUN switcher/canvas come back the same way
    // they would have if the user had just stayed on the page.
    //
    // jobIds.length === 1 alone can't tell a combined job apart from a plain
    // single-RUN manual revise (a finish-stage /continue) -- both end up
    // recorded identically as {path:"local", runs:[<one id>]}, since
    // action_id only groups multiple ids together for an explicit multi-RUN
    // revise. Ask the backend instead: job_processor.py only ever sets
    // `stage`/`run_count` on a combined job's /status response (the same
    // signal pollJobStatus's onStatus relies on below) -- a finish-stage job
    // never has either. Misclassifying a finished single-RUN job here left
    // it stuck showing this loading placeholder: watchCombinedJobRuns's
    // fetchNewRuns reads /result's `runs` list, which a finish-stage job
    // never has, so onRunDone silently never fired.
    let isCombined = false;
    if (job.path === "local" && jobIds.length === 1) {
      try {
        const status = await getJobStatusOnce(jobIds[0], { path: job.path });
        isCombined = typeof status.stage === "string" || typeof status.run_count === "number";
        if (isCombined && historyViewToken === myToken) {
          loadingRecord.progressTipsOnly = true;
          loadingRecord.status = status.stage === "analyze" ? "analyzing" : "processing";
          showBatchMainLoading(loadingRecord);
        }
      } catch {
        // Falls through to the generic single-job path below, which already
        // handles a genuine status-check failure on its own.
      }
      if (historyViewToken !== myToken) return; // navigated away meanwhile
    }
    if (isCombined) {
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
            analysisModel: job.analysis_model || extraInfo.analysis_model || "gpt-5.6-luna",
            aspectRatio: job.aspect_ratio || "auto",
            outputScale: findOutputScaleOption(job.output_scale || extraInfo.output_scale)?.value,
            aiPhotoEditing: job.ai_photo_editing != null ? job.ai_photo_editing : !!extraInfo.image_model,
            runQa: job.run_qa != null ? job.run_qa : true,
            singlePassEdit: resolvedSinglePassEdit(job.single_pass_edit, extraInfo.single_pass_edit),
            processingPath: "local",
            analyzeTimings: [],
            useCombinedJob: false
          };
          applySinglePassEditOption(singleFlowState.singlePassEdit);
          // Same per-case picker sync as viewAnalyzeHistoryRecord/
          // viewHistoryRecord: this job finished while its history tile was
          // open, and showSingleResult below can auto-open the revise panel,
          // which must show THIS record's settings rather than the last
          // case's.
          applyModelQualityOption(findModelQualityOptionByPair(singleFlowState.imageModel, singleFlowState.imageQuality));
          applyAnalysisModelOption(findAnalysisModelOption(singleFlowState.analysisModel));
          applyAspectRatioOption(findAspectRatioOption(singleFlowState.aspectRatio));
          applyOutputScaleOption(findOutputScaleOption(singleFlowState.outputScale));
          applyAdvancedOptionsOption(
            recordAdvancedOptions(singleFlowState.advancedOptions, restoredAnalysis), "ap");
          lastSingleSelections = selections;
          setCurrentCaseAnalysisVersion(restoredAnalysis);
        }
        const applyRender = () => {
          els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
          showSection("results");
          showSingleResult({ autoOpenPanel: false, forceReopen: !revealed });
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
          onStage: (stage) => {
            if (historyViewToken !== myToken || revealed) return;
            loadingRecord.status = stage === "analyze" ? "analyzing" : "processing";
            showBatchMainLoading(loadingRecord);
          },
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
          showBatchMainError({ previewUrl, error: tr("全部處理失敗，請稍後再試。") });
        }
        // Otherwise at least one RUN already rendered -- leave it displayed
        // rather than clobbering it with an error for the whole record.
        return;
      }
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      // Finalizes exactly like a normally-finished record (revise button,
      // etc.) now that every RUN is confirmed done. Caught (unlike a plain
      // await) so a failure here -- e.g. a transient fetch error -- surfaces
      // as an error state instead of leaving #batch-main-loading stuck up
      // forever with nothing watching it anymore.
      try {
        await viewHistoryRecord(job);
      } catch (error) {
        if (historyViewToken !== myToken) return; // navigated away meanwhile
        showBatchMainError({ previewUrl, error: friendlyError(error) });
      }
      return;
    }

    // Two-stage manual path (N separate job_ids) or the AIPoC path (no
    // incremental combined-job concept, synchronous analyze) -- unchanged:
    // wait for any ONE of them to succeed, then render whatever's done.
    try {
      await Promise.any(jobIds.map((id) => pollJobStatus(id, { path: job.path })));
    } catch {
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      showBatchMainError({ previewUrl, error: tr("全部處理失敗，請稍後再試。") });
      return;
    }
    if (historyViewToken !== myToken) return; // navigated away meanwhile
    try {
      await viewHistoryRecord(job);
    } catch (error) {
      if (historyViewToken !== myToken) return; // navigated away meanwhile
      showBatchMainError({ previewUrl, error: friendlyError(error) });
    }
  }

  // #results-canvas-row is hidden while a history record loads (see
  // viewHistoryRecord) and used to be revealed only by that same call when it
  // was still the current one. A load superseded by anything other than
  // another history view (Home, a 待美化 record, a new upload) therefore
  // left the canvas invisible from then on -- blank results page until a
  // reload. Tracking which call holds it lets every exit hand it back unless
  // a newer history load has taken it over.
  let historyCanvasRowHider = 0;

  function hideHistoryCanvasRow(token) {
    if (!els.resultsCanvasRow) return;
    els.resultsCanvasRow.classList.add("no-transition");
    els.resultsCanvasRow.style.visibility = "hidden";
    historyCanvasRowHider = token;
  }

  // With a token: reveal only if that call still holds the row. Without one
  // (resetApp): reveal whoever holds it. A no-op when nobody does, so it
  // never touches a .no-transition some other flow added for itself.
  function releaseHistoryCanvasRow(token) {
    if (!historyCanvasRowHider) return;
    if (token !== undefined && historyCanvasRowHider !== token) return;
    historyCanvasRowHider = 0;
    if (!els.resultsCanvasRow) return;
    els.resultsCanvasRow.style.visibility = "";
    // The record is on screen now -- #page-scroll-hint was suppressed while
    // this hold was up (see updatePageScrollHint) and nothing else will ask
    // it to re-check, since revealing the row changes no layout.
    updatePageScrollHint();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => els.resultsCanvasRow.classList.remove("no-transition"));
    });
  }

  // Points the whole results page at ONE version of a case: the revise
  // panel's restored selections, singleFlowState (what a further edit would
  // be submitted with) and the pickers behind the per-case settings button
  // all come to describe that version rather than whichever one happens to
  // be newest. Called once by viewHistoryRecord for the version it opens on,
  // and again by the run switcher each time the user steps onto a different
  // one -- 「這一版跑過的設定」 is exactly what that button promises, so it
  // has to follow the image. Every applyXOption below is a programmatic
  // (fromUser: false) write, so they re-baseline that button on this version
  // rather than raising its 已變更 dot -- see refreshApCaseSettings's own doc.
  //
  // params: the version's own recorded params (fetchHistoryVersionRuns).
  // fallbackParams is the case summary, which flattens the LATEST version's
  // params onto itself -- right for the version viewHistoryRecord opens on,
  // and the only thing available at all for a case with no version list
  // (a pre-v3 entry, or a /case fetch that failed).
  // The params of the version a 接著這張調整 version was made from, past any
  // continuations in between. Read off versionParams (source_result marks a
  // continuation), since an older version's result isn't loaded yet. Its own
  // params when the source is gone.
  function continuationSourceParams(run) {
    let params = run.versionParams || {};
    const seen = new Set([run.versionNumber]);
    for (let from = run.versionFrom; params.source_result && from != null && !seen.has(from);) {
      seen.add(from);
      const source = (singleRuns || []).find((r) => r.versionNumber === from);
      if (!source?.versionParams) break;
      params = source.versionParams;
      from = source.versionFrom;
    }
    return params;
  }

  // The version a 接著這張調整 chain started from, past any continuations in
  // between -- the one whose analysis the case actually has when it was
  // analyzed up front (one analyzed later via 幫我分析 is historyCaseAnalysis
  // instead). Null when that version is gone.
  function continuationBaseRun(run, runs = singleRuns) {
    const seen = new Set([run.versionNumber]);
    let source = run;
    for (let from = run.versionFrom; source.versionParams?.source_result && from != null && !seen.has(from);) {
      seen.add(from);
      source = (runs || []).find((r) => r.versionNumber === from);
      if (!source) return null;
      from = source.versionFrom;
    }
    return source !== run && !source.versionParams?.source_result ? source : null;
  }

  // Older versions are fetched on arrival only, so that version's result
  // has to be fetched before applyHistoryVersionContext can read it.
  async function loadContinuationBase(runs, run) {
    const base = run?.versionParams?.source_result ? continuationBaseRun(run, runs) : null;
    if (base?.status === "unloaded") await loadHistoryRun(runs, base);
  }

  // A job on the case's original photo for anything that works off the
  // photo (幫我分析, 從原圖美化): a continuation's own job edits the version
  // it was made from. Any version made from the original serves.
  function originJobId(run) {
    if (!run?.versionParams?.source_result) return run?.jobId || null;
    const base = continuationBaseRun(run) || (singleRuns || []).find((r) => r.jobId && !r.versionParams?.source_result);
    return base?.jobId || run.jobId;
  }

  // {analysis, jobId} of that version, or null when it ran 只照需求改 itself.
  function continuationBaseAnalysis(run) {
    const base = continuationBaseRun(run);
    const analysis = base?.status === "done" ? base.result?.analysis : null;
    return analysis && !analysis._skipped_analysis ? { analysis, jobId: base.jobId } : null;
  }

  function applyHistoryVersionContext(run, fallbackParams) {
    const params = run.versionParams || fallbackParams || {};
    // params.* is absent (rather than falsy-but-present) only for an entry
    // recorded before that field existed. Falling back to undefined here
    // would silently submit ai_photo_editing=false on continue (the cheap
    // local-only pass, not a real AI edit) -- extra-info.image_model (set
    // only when an AI edit actually ran, see pipeline.py's finish_stage) is a
    // more accurate fallback than a blind default for that one specifically.
    const extraInfo = run.result["extra-info"] || {};
    const continuation = isContinuationRun(run);
    // A continuation's own analysis is a stub: the case's is either the one
    // analyzed later, or the one the version it was made from came with.
    const caseAnalysis = historyCaseAnalysis
      ? { analysis: historyCaseAnalysis, jobId: historyCaseAnalysisJobId }
      : continuation ? continuationBaseAnalysis(run) : null;
    const attached = run.result.analysis?._skipped_analysis ? caseAnalysis?.analysis || null : null;
    // A continuation's request reads like any version's: the 接著調整 text it
    // was made with, as if from the original. Its ratio ("original") and
    // 只照需求改 are how a continuation renders, no choice of the user's for
    // the photo: those are the version it was made from.
    const caseParams = continuation ? continuationSourceParams(run) : params;
    // Analyzed since, on a version made with 只照需求改: still 僅依需求修改,
    // as it was made -- unticking it is what brings the analysis in.
    const { analysis: restoredAnalysis, selections } = restoreReviseSelections(
      attached || run.result.analysis, params.user_prompt, caseParams.overwrite_prompt || (!continuation && !!attached));
    // 從原圖美化 off this version (#ap-continue-base on 原圖): the version
    // the chain started from's own request and styles, not this one's --
    // like the chat's plan, which a continuation never touches.
    const origin = continuation && attached ? {
      run,
      selections: { ...selections, options: { ...selections.options },
        userPrompt: caseParams.user_prompt || "", overwritePrompt: !!caseParams.overwrite_prompt },
    } : null;
    // Its styles too are the ones it was made with, kept on its own stub only.
    if (continuation && attached) {
      const own = run.result.analysis.style_options || {};
      selections.options = Object.fromEntries(
        ["pose", "angle", "lighting", "size"].map((k) => [k, own[k] || "default"]));
    }
    singleFlowState = {
      analysis: restoredAnalysis,
      analyzeJobId: attached && continuation && caseAnalysis.jobId ? caseAnalysis.jobId : originJobId(run),
      presetKey: params.preset || "natural",
      preset: presets[params.preset] || presets.natural,
      imageQuality: params.image_quality || extraInfo.image_quality || "medium",
      imageModel: params.image_model || extraInfo.image_model || "gpt-image-2",
      analysisModel: params.analysis_model || extraInfo.analysis_model || "gpt-5.6-luna",
      // Analyzed since: the ratio that analysis was planned for, or a new
      // ratio would re-plan its composition.
      aspectRatio: continuation && attached
        ? attached._aspect_ratio_pref_at_analysis || "auto" : caseParams.aspect_ratio || "auto",
      outputScale: findOutputScaleOption(params.output_scale || extraInfo.output_scale)?.value,
      aiPhotoEditing: params.ai_photo_editing != null ? params.ai_photo_editing : !!extraInfo.image_model,
      runQa: params.run_qa != null ? params.run_qa : true,
      singlePassEdit: resolvedSinglePassEdit(params.single_pass_edit, extraInfo.single_pass_edit),
      processingPath: "local",
      analyzeTimings: [],
      useCombinedJob: false,
      origin
    };
    applySinglePassEditOption(singleFlowState.singlePassEdit);
    lastSingleSelections = selections;
    setCurrentCaseAnalysisVersion(restoredAnalysis);

    // #ap-options-card-inline's model/quality and aspect-ratio pickers are
    // otherwise left showing whatever a previous record (or the upload page,
    // or the version stepped off of) had picked -- sync them to what THIS
    // version actually used. Only the picker, not applyModelQualityChange/
    // applyAspectRatioChange's full side effects (pushing onto
    // singleFlowState -- already correct, just set above -- and, for aspect
    // ratio, re-fitting the preview, which the caller's own render handles).
    applyModelQualityOption(findModelQualityOptionByPair(singleFlowState.imageModel, singleFlowState.imageQuality));
    applyAnalysisModelOption(findAnalysisModelOption(singleFlowState.analysisModel));
    applyAspectRatioOption(findAspectRatioOption(singleFlowState.aspectRatio));
    applyOutputScaleOption(findOutputScaleOption(singleFlowState.outputScale));
    // singleFlowState.advancedOptions itself is left unset by the historical
    // reconstruction above (unlike imageModel/aspectRatio) -- falling back to
    // singleFlowState.analysis._advanced_options, the same key a later
    // revise's apply_selections falls back to, keeps this in sync with
    // what a revise would actually use if left untouched. Kept last because
    // it is the only one of the four that refreshes the per-case settings
    // button unconditionally, even when handed a value that doesn't resolve
    // to a known option.
    applyAdvancedOptionsOption(
      recordAdvancedOptions(singleFlowState.advancedOptions, singleFlowState.analysis), "ap");
  }

  // The analysis a 「只照需求改」 case got afterwards (from 自訂模式's
  // 「幫我分析」 or the chat's), which get_history hands over as
  // analysis_job_id. One still running or failed reads as none: the case
  // opens on its stub and 幫我分析 is offered again. Fetched whenever the
  // case has one: only the newest version comes loaded, and it may be the
  // analyzed one while the 只照需求改 ones before it are not yet.
  async function fetchAttachedCaseAnalysis(job) {
    if (!job.analysis_job_id) return null;
    try {
      return (await getAnalyzeResult(job.analysis_job_id)).result.analysis || null;
    } catch {
      return null;
    }
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
    // Version browsing is local-path only for now: the AIPoC branch further
    // down renders through showBatchDoneMain/renderRunSwitcher, which has no
    // equivalent of applyHistoryVersionContext and would show older versions
    // without ever re-pointing the page at them.
    const runs = await fetchAllHistoryRuns(job, { allVersions: job.path === "local" });
    const attachedAnalysis = job.path === "local" ? await fetchAttachedCaseAnalysis(job) : null;
    // Opens on the newest version (see below); a continuation reads its
    // case analysis off the version its chain started from.
    if (job.path === "local" && !attachedAnalysis && runs.length) {
      await loadContinuationBase(runs, runs[runs.length - 1]);
    }
    // Superseded while the runs were loading -- don't hide (or render into)
    // a screen that now belongs to something else.
    if (historyViewToken !== myToken) return false;
    // Nothing on screen has changed yet -- a record that can't be fetched
    // (e.g. a reported job whose Redis job info expired with no report-time
    // copy to fall back on, see
    // backend/services/report.py) leaves the current screen as it was,
    // rather than stamping this record's times over someone else's photo.
    if (!runs.length) {
      showNoticeModal(report ? tr("這筆回報的結果已經過期，無法開啟。") : tr("無法取得這筆處理紀錄，請稍後再試。"));
      return false;
    }
    setCurrentCaseTimes(job, revealDetailExtra);
    // Drawer stays open (not closed here) until the result page it's opening
    // has actually finished rendering below -- closing it immediately after
    // this fetch left a visible gap where neither the drawer nor the result
    // was on screen while the rest of this function awaited.
    if (resultsDetailExtra) resultsDetailExtra.hidden = !revealDetailExtra;
    updateResultStatusPill(report);

    // .results-canvas-row/.results-canvas-col/.results-analysis-slot all
    // transition their own gap/flex-grow/opacity (styles.css) so a live
    // in-page toggle (opening/closing the revise panel) eases smoothly --
    // but opening a whole DIFFERENT historical record isn't a toggle the
    // user asked to watch animate. no-transition (below) guards against
    // that, same as elsewhere in this file.
    //
    // The bigger issue is earlier and plainer than a transition, though:
    // showSection("results")/showBatchDoneMain (further down) make this row
    // visible BEFORE this record's own generated/original image has finished
    // loading and BEFORE showSingleResult/showBatchDoneMain get around to
    // adding showing-analysis -- there's a real await in between (the image
    // fetch). Whatever this row's state happens to be at that exact instant
    // (no showing-analysis at all on a first-ever visit -- full width, no
    // panel -- or still the PREVIOUS record's own layout/content on a
    // later one) is what actually gets painted for however long that fetch
    // takes, before a second, later paint shows the finished thing. Hiding
    // the row via visibility (not its own `hidden` -- that would zero out
    // layout, and drawAnalysisBoxes/updateAnalysisPreviewMaxHeight below
    // need real measurements while this runs) keeps it fully laid out but
    // paints nothing until it's explicitly revealed again once the new
    // record is completely ready, further down each branch below.
    hideHistoryCanvasRow(myToken);

    // A live batch (if one happened to be running in this same session) has
    // nothing to do with a historical record -- make sure its sidebar/
    // batch-mode layout doesn't bleed into this single-photo view. A case
    // that was uploaded together with others in 對話模式 lists that batch
    // instead (showCaseGroupSidebar).
    currentBatchRecord = null;
    showCaseGroupSidebar(job.image_key);
    // A 待美化 review this record replaced (opened from the drawer without
    // going Home) left this set, and handleDownloadClick checks it first --
    // 下載結果 on this finished record then saved the review's original
    // photo instead of the result.
    pendingResultsActive = false;
    // Some OTHER record's live revise (finishSingleFlow) may have left these
    // toggled to show its own "AI is working" overlay -- reset unconditionally
    // so that stale overlay never covers up whatever's actually being shown
    // here (finishSingleFlow's own completion no longer touches the DOM at
    // all once historyViewToken has moved on, see its own comment).
    if (els.canvasLoading) els.canvasLoading.hidden = true;
    if (els.photoFrameWrap) els.photoFrameWrap.hidden = false;

    historyCaseAnalysis = attachedAnalysis;
    historyCaseAnalysisJobId = attachedAnalysis ? job.analysis_job_id : null;
    if (job.path === "local") {
      selectedFile = null;
      singleRuns = runs;
      if (isAnalyzedAfterEdits(job) && attachedAnalysis) versionStripPendingRuns = runs;
      // Open on the NEWEST version, which is what this page showed back when
      // the latest edit was the only one a case kept. runs is oldest-version-
      // first (fetchHistoryVersionRuns), so that is the last version in it,
      // and its FIRST image -- the same "RUN 1 of this edit" this used to land
      // on. A case with no version tagging at all (the fallback list) has only
      // one edit in it and resolves to index 0, exactly as before.
      const latestVersion = runs[runs.length - 1].versionNumber;
      singleMirrorIndex = latestVersion == null
        ? 0
        : runs.findIndex((r) => r.versionNumber === latestVersion);
      // Just switched over from the chat page: the version it had on screen
      // instead, so both modes show the same one (handoff's version_id). Only
      // the newest version comes loaded; another is loaded first, and one
      // whose result is gone leaves the newest on screen.
      // A version only looked at while adjusting another (showing_version_id)
      // is stepped to once the adjusted one is up, the way the arrows do.
      const handedVersion = pendingModeHandoff?.case_id === job.image_key ? pendingModeHandoff.version_id : null;
      const showingVersion = handedVersion ? pendingModeHandoff.showing_version_id : null;
      const handedIndex = handedVersion ? runs.findIndex((r) => r.versionId === handedVersion) : -1;
      const handedRun = runs[handedIndex];
      if (handedRun?.status === "unloaded") await loadHistoryRun(runs, handedRun);
      if (myToken !== historyViewToken) return;
      if (handedRun?.status === "done") singleMirrorIndex = handedIndex;
      const firstDone = singleRuns[singleMirrorIndex];
      // job.job_id doesn't exist on a history entry (job ids live in
      // job.runs instead, see record_history/account.py) -- firstDone.jobId is
      // the actual resolved job_id fetchAllHistoryRuns already looked up.
      // Every version of a case is re-run from the same original photo, so
      // which version's job serves it doesn't matter (see based_on in
      // account.py for where that stops being true).
      selectedFileSourceUrl = sourceImageUrl(firstDone.jobId);
      originalImgThumbUrl = sourceThumbnailUrl(firstDone.jobId);
      // Must run BEFORE showSingleResult below: showSingleResult can
      // auto-open the inline revise panel (openInlineAnalysisForCurrentContext,
      // fire-and-forget but its snapshot capture runs synchronously), which
      // snapshots #ap-image-model-quality's *current* value as the "nothing
      // changed yet" baseline -- syncing the pickers only after that snapshot
      // was taken left the baseline on the previous record's stale model/
      // quality, so picking a model that happened to match that leftover
      // value (not this record's own) was wrongly treated as "no changes"
      // and silently blocked Run.
      applyHistoryVersionContext(firstDone, job);

      // Awaited (not fire-and-forget) so this function's own promise only
      // resolves once the result photo is actually showing -- callers like
      // the history-list click handler use that to keep their own "opening…"
      // spinner up for the whole wait instead of dropping it the instant the
      // network fetch above resolved. setImgSrcPreloaded (not a bare
      // src assignment) keeps #original-img itself from ever showing the
      // browser's broken-image icon/alt text while this record's own photo
      // is a different one than whatever was on screen before.
      await setImgSrcPreloaded(els.originalImg, selectedFileSourceUrl);
      // Superseded while the photo loaded -- hand the row back (if no newer
      // history load holds it) instead of rendering this record over the
      // screen that has since taken over.
      if (historyViewToken !== myToken) { releaseHistoryCanvasRow(myToken); return false; }
      els.origDim.textContent = `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`;
      showSection("results");
      // forceReopen: true -- this may be a genuinely different record than
      // whatever the analysis panel was last showing, even if it was already
      // open (see openInlineAnalysisForCurrentContext's own doc on this).
      // showSingleResult itself preloads the RUN's image before painting, so
      // this doesn't return until that's done either.
      await showSingleResult({ forceReopen: true, revealDetailExtra });
      const showingIndex = showingVersion && showingVersion !== handedVersion
        ? runs.findIndex((r) => r.versionId === showingVersion) : -1;
      if (showingIndex >= 0 && historyViewToken === myToken) await selectRunAt(showingIndex);
      if (historyViewToken === myToken) closeHistoryDrawer();
      // Reveals only if no newer history load has hidden the row for itself
      // -- a superseded call would otherwise show this now-stale render on
      // top of what that newer call is still preparing behind it.
      releaseHistoryCanvasRow(myToken);
      return true;
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
      status: "done",
      uploadedAt: job.uploaded_at || job.ts || null,
      modifiedAt: job.modified_at || null
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
    applyAnalysisModelOption(findAnalysisModelOption(job.analysis_model || extraInfo.analysis_model || "gpt-5.6-luna"));
    applyAspectRatioOption(findAspectRatioOption(job.aspect_ratio || "auto"));
    applyOutputScaleOption(findOutputScaleOption(job.output_scale || extraInfo.output_scale));
    applyAdvancedOptionsOption(recordAdvancedOptions(null, record.result.analysis), "ap");
    if (historyViewToken === myToken) closeHistoryDrawer();
    releaseHistoryCanvasRow(myToken); // see the local-path branch above
    return true;
  }

  // Example-photo gallery (.upload-gallery-col, always visible) -- fetched
  // once (getDemoImages) and cached here, since the list is static
  // server-side content that can't change mid-session.
  let demoImagesCache = null;
  // Filenames currently toggled on (see toggleDemoImageSelection) -- persists
  // across toggling tiles back and forth before confirming (e.g. the user
  // wants to review/adjust the picks they already confirmed into the upload
  // area), and is only cleared by resetApp -- a real fresh start.
  let demoSelectedNames = new Set();
  const UPLOAD_HISTORY_PAGE_SIZE = 20;
  let uploadHistoryOffset = 0;
  let uploadHistoryRequestToken = 0;
  let uploadHistoryLoading = false;
  let uploadHistoryHasMore = false;
  // "recent" | "sample" while logged in (tabs visible), null while logged
  // out (the plain #upload-gallery-title label is showing instead).
  let uploadGalleryActiveTab = null;

  async function loadUploadHistoryPage({ reset = false, keepVisibleUntilReady = false } = {}) {
    const user = getCurrentUser();
    if (!user || !els.demoImagesGrid || uploadHistoryLoading) return;
    // Nothing may render history into the shared grid while 範例照片 owns it
    // (see the scroll listener's own comment). The request token already
    // stops a response that arrives after a switch; this stops a load from
    // starting after one, which is the case that produced tiles appended
    // below the sample gallery. Safe for every current caller:
    // setUploadGalleryTab assigns uploadGalleryActiveTab before calling in,
    // and refreshUploadGalleryIfVisible checks it first.
    if (uploadGalleryActiveTab !== "recent") return;
    const token = ++uploadHistoryRequestToken;
    if (reset) {
      uploadHistoryOffset = 0;
      uploadHistoryHasMore = false;
      // keepVisibleUntilReady: this is 最近照片 refreshing ITSELF (returning to
      // the upload screen, a bfcache restore), so what's on screen is the
      // previous render of the very same gallery -- emptying it here just
      // blanks the panel for the length of a round trip before the same
      // tiles come back. renderHistoryList's own !append clear swaps them
      // out once the new page has actually arrived, which reads as no
      // change at all when nothing changed. Without the flag (arriving from
      // 範例照片) the tiles on screen belong to the OTHER gallery and have
      // to go immediately -- same reasoning as fetchAndRenderDemoImages',
      // just in the opposite direction.
      if (!keepVisibleUntilReady) {
        els.demoImagesGrid.innerHTML = "";
        if (els.demoImagesEmpty) els.demoImagesEmpty.hidden = true;
      }
    }
    uploadHistoryLoading = true;
    try {
      const [page] = await Promise.all([
        getHistory(user, { limit: UPLOAD_HISTORY_PAGE_SIZE, offset: uploadHistoryOffset }),
        loadCaseGroups({ refresh: uploadHistoryOffset === 0 })
      ]);
      if (token !== uploadHistoryRequestToken || getCurrentUser() !== user) return;
      const returnedJobs = Array.isArray(page.jobs) ? page.jobs : [];
      // Keep the thumbnail cap effective even while an older backend is
      // still serving the endpoint and ignoring limit/offset. In that case
      // the response contains the full (or legacy-capped) list; slice the
      // requested window locally before renderHistoryList creates any img
      // elements. Once the paginated backend is live, returnedJobs is already
      // at most 20 and this is simply a no-op.
      const jobs = returnedJobs.length > UPLOAD_HISTORY_PAGE_SIZE
        ? returnedJobs.slice(uploadHistoryOffset, uploadHistoryOffset + UPLOAD_HISTORY_PAGE_SIZE)
        : returnedJobs;
      await renderHistoryList(jobs, { container: els.demoImagesGrid, append: !reset && uploadHistoryOffset > 0 });
      uploadHistoryOffset += jobs.length;
      const legacyHasMore = returnedJobs.length > UPLOAD_HISTORY_PAGE_SIZE
        && uploadHistoryOffset < returnedJobs.length;
      uploadHistoryHasMore = page.has_more === true || legacyHasMore;
      uploadHistoryLoading = false;
      if (els.demoImagesEmpty) els.demoImagesEmpty.hidden = uploadHistoryOffset > 0;
    } catch {
      if (token !== uploadHistoryRequestToken) return;
      uploadHistoryHasMore = false;
      uploadHistoryLoading = false;
      // Only when there is genuinely nothing on screen. keepVisibleUntilReady
      // resets the offset while deliberately leaving the previous render up,
      // so the offset alone no longer means "the gallery is empty" -- printing
      // 目前無法取得處理紀錄。 over a full gallery of tiles contradicts what the
      // user is looking at. A failed background refresh should leave what they
      // already have alone and say nothing.
      if (!uploadHistoryOffset && !els.demoImagesGrid.children.length && els.demoImagesEmpty) {
        els.demoImagesEmpty.hidden = false;
        els.demoImagesEmpty.textContent = tr("目前無法取得處理紀錄。");
      }
    }
  }

  function refreshUploadGallery(user) {
    if (!els.demoImagesGrid) return;
    // A guest identity (isGuestUser, account.js -- a broken/undecryptable
    // ?user_id=) is signed in enough for account-badge/history, but its
    // disposable per-session bucket never has real history worth a 最近照片
    // tab -- treat it like the no-account case here, same as an anonymous
    // visitor.
    const signedIn = !!user && !isGuestUser();
    if (els.uploadGalleryCol) els.uploadGalleryCol.classList.toggle("is-history", signedIn);
    if (signedIn) {
      // Always land back on 最近照片 on a real account change (login/logout/
      // switching accounts) -- setUploadGalleryTab below handles a signed-in
      // user then manually switching to 範例照片 from here.
      setUploadGalleryTab("recent");
      return;
    }
    uploadGalleryActiveTab = null;
    if (els.uploadGalleryCol) els.uploadGalleryCol.classList.remove("showing-recent");
    if (els.recentPhotosHistoryBtn) els.recentPhotosHistoryBtn.hidden = true;
    ++uploadHistoryRequestToken;
    uploadHistoryHasMore = false;
    uploadHistoryLoading = false;
    if (els.uploadGalleryTitle) els.uploadGalleryTitle.textContent = tr("範例照片");
    if (els.demoImagesConfirmBtn) els.demoImagesConfirmBtn.hidden = false;
    initDemoImagesGallery();
  }

  // Switches which gallery .upload-gallery-tabs (index.html) is showing,
  // while an account stays linked throughout -- unlike refreshUploadGallery
  // above, this never touches the .is-history class. Also driven directly
  // by refreshUploadGallery's own "recent" default whenever an account is
  // (re)linked, so the two stay in sync instead of duplicating this logic.
  function setUploadGalleryTab(tab) {
    // Whether 最近照片 is already what's on screen decides if its reload may
    // leave the current tiles up while it fetches (see loadUploadHistoryPage's
    // keepVisibleUntilReady). Coming from 範例照片 -- or from no gallery at
    // all -- it may not: those tiles belong to something else.
    const wasShowingRecent = uploadGalleryActiveTab === "recent";
    uploadGalleryActiveTab = tab;
    // .showing-recent (styles.css) is what actually drives #demo-images-grid's
    // layout -- keeps a logged-in user's 範例照片 tab on the exact same
    // grid/tile styling an anonymous visitor sees, instead of inheriting the
    // one-per-row/day-grouped history layout just because .is-history (an
    // account is linked) is also true.
    if (els.uploadGalleryCol) els.uploadGalleryCol.classList.toggle("showing-recent", tab === "recent");
    // Mobile/tablet only (styles.css) -- 開啟歷史紀錄 only makes sense for
    // 最近照片, not 範例照片.
    if (els.recentPhotosHistoryBtn) els.recentPhotosHistoryBtn.hidden = tab !== "recent";
    if (els.uploadGalleryTabRecent) {
      els.uploadGalleryTabRecent.classList.toggle("is-active", tab === "recent");
      els.uploadGalleryTabRecent.setAttribute("aria-selected", String(tab === "recent"));
    }
    if (els.uploadGalleryTabSample) {
      els.uploadGalleryTabSample.classList.toggle("is-active", tab === "sample");
      els.uploadGalleryTabSample.setAttribute("aria-selected", String(tab === "sample"));
    }
    if (tab === "sample") {
      ++uploadHistoryRequestToken;
      uploadHistoryHasMore = false;
      uploadHistoryLoading = false;
      if (els.demoImagesConfirmBtn) els.demoImagesConfirmBtn.hidden = false;
      fetchAndRenderDemoImages();
      return;
    }
    if (els.demoImagesConfirmBtn) els.demoImagesConfirmBtn.hidden = true;
    loadUploadHistoryPage({ reset: true, keepVisibleUntilReady: wasShowingRecent });
  }

  // applyAccountUi()'s initial call above and resetApp()'s call below cover
  // every IN-APP way back to the upload screen (Home, 清除, finishing a
  // result). But the user can also return to it WITHOUT any in-app action
  // firing -- switching browser tabs and back, minimizing/restoring the
  // window, or browser back/forward (bfcache) all leave the upload section
  // as the visible one without ever calling resetApp/showSection. Re-check
  // on both signals so either one catches it: "pageshow" covers bfcache
  // restores (event.persisted), "visibilitychange" covers plain tab/window
  // switching. Guarded to logged-in users only and only while the upload
  // section is actually the one on screen -- also only while 最近照片 is the
  // active tab (uploadGalleryActiveTab): 範例照片 issues a real request per
  // refresh and barely ever changes, so refreshing it on every tab switch
  // would be pure waste, and refreshing any gallery while a different
  // section is showing would be too. Deliberately does not reset which tab
  // is active -- unlike refreshUploadGallery, this fires on returning to an
  // already-open session, not on an account change.
  function refreshUploadGalleryIfVisible() {
    const user = getCurrentUser();
    if (!user) return;
    if (!els.sections?.upload?.classList.contains("visible")) return;
    if (uploadGalleryActiveTab !== "recent") return;
    // Already on 最近照片 by the guard above, so this is a silent background
    // refresh of what the user is looking at -- never blank it first.
    loadUploadHistoryPage({ reset: true, keepVisibleUntilReady: true });
  }
  window.addEventListener("pageshow", (event) => {
    // A plain first load fires "pageshow" too, right alongside
    // applyAccountUi()'s own initial refreshUploadGallery() call -- without
    // this guard every fresh page load would fetch the gallery twice.
    // event.persisted is true only for an actual bfcache restore, which is
    // the case this listener exists for.
    if (event.persisted) refreshUploadGalleryIfVisible();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshUploadGalleryIfVisible();
  });

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
      const thumbUrl = demoImageThumbnailUrl(name);
      // Blurred backdrop copy of the same photo (see .demo-image-tile-bg,
      // styles inline in index.html) so a non-square photo's letterbox bars
      // fill with the image itself instead of flat empty space.
      const bg = document.createElement("img");
      bg.className = "demo-image-tile-bg";
      bg.src = thumbUrl;
      bg.alt = "";
      bg.loading = "lazy";
      tile.appendChild(bg);
      const img = document.createElement("img");
      img.src = thumbUrl;
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
    els.demoImagesConfirmBtn.textContent = count > 0 ? tr("使用所選照片 ({count})", { count }) : tr("使用所選照片");
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

  // Gallery is always visible now (.upload-gallery-col, index.html) rather
  // than behind #demo-images-btn opening a modal -- fetches once on
  // startup instead of on first open.
  function initDemoImagesGallery() {
    if (!els.demoImagesGrid) return;
    // A linked account is identifiable from the URL before the async
    // decrypt/resolve round trip completes. Treat that as history mode
    // immediately, otherwise a slow account bootstrap can briefly start the
    // anonymous demo gallery and issue one request per demo thumbnail. This
    // guard only matters for that startup race -- setUploadGalleryTab calls
    // fetchAndRenderDemoImages directly for a signed-in user deliberately
    // switching to 範例照片, once login state is already settled.
    if (!isGuestUser() && (getCurrentUser() || new URLSearchParams(window.location.search).has("user_id"))) return;
    fetchAndRenderDemoImages();
  }

  function fetchAndRenderDemoImages() {
    if (!els.demoImagesGrid) return;
    updateDemoImagesConfirmBtn();
    // Cached: render right now, no request and no gap. demoImagesCache was
    // always written here but never actually read, so every switch to 範例照片
    // paid for a fresh round trip -- for content this function's own cache
    // comment already calls unchangeable mid-session.
    if (demoImagesCache) {
      renderDemoImagesGrid(demoImagesCache);
      return;
    }
    // Uncached: empty the grid BEFORE the request, not after it in
    // renderDemoImagesGrid. Both galleries render into this one element, so
    // leaving the previous tab's tiles up for the length of a round trip
    // showed 最近照片's thumbnails under the 範例照片 tab for a visible
    // moment on every switch. Same reset loadUploadHistoryPage does for the
    // other direction (which is why only this direction ever flashed),
    // including keeping #demo-images-empty down meanwhile so an empty grid
    // doesn't read as "no photos" mid-load.
    els.demoImagesGrid.innerHTML = "";
    if (els.demoImagesEmpty) els.demoImagesEmpty.hidden = true;
    getDemoImages().then((names) => {
      // Account bootstrap can finish, or the tab can be switched away, while
      // this was still in flight. Do not let a stale response replace
      // whichever gallery is actually showing by then. A guest never leaves
      // this gallery (refreshUploadGallery keeps it in the no-account state),
      // so it's exempt same as the initDemoImagesGallery guard above.
      if (getCurrentUser() && !isGuestUser() && uploadGalleryActiveTab !== "sample") return;
      // Only a non-empty list is worth remembering. getDemoImages (photo-
      // agent-api.js) swallows every failure -- bad response, bad JSON,
      // network down -- and resolves with [], so an empty list can't be told
      // apart from a genuinely empty gallery. Caching it would make one
      // transient failure stick for the rest of the session now that this
      // cache is actually read; leaving it unset just means the next switch
      // to 範例照片 tries again, which is what used to happen every time.
      if (names.length) demoImagesCache = names;
      renderDemoImagesGrid(names);
    });
  }

  // Version history -- lazily fetched once from changelog.json (a static
  // file shipped alongside index.html, not a backend endpoint) and cached
  // here, same reasoning as demoImagesCache above: content that can't
  // change mid-session, no need to re-fetch on every open.
  let changelogCache = null;

  function renderChangelog(entries) {
    if (!els.changelogModalBody) return;
    els.changelogModalBody.innerHTML = "";
    // <details> gives free fold/unfold with no click-handler JS; only the
    // newest entry (index 0 -- entries.json is kept newest-first) starts open.
    entries.forEach((entry, index) => {
      const block = document.createElement("details");
      block.className = "changelog-entry";
      block.open = index === 0;
      const date = document.createElement("summary");
      date.className = "changelog-entry-date";
      date.textContent = entry.date || "";
      block.appendChild(date);
      const list = document.createElement("ul");
      list.className = "changelog-entry-list";
      (entry.changes || []).forEach((change) => {
        const li = document.createElement("li");
        li.textContent = change;
        list.appendChild(li);
      });
      block.appendChild(list);
      els.changelogModalBody.appendChild(block);
    });
  }

  function openChangelogModal() {
    if (!els.changelogModal) return;
    els.changelogModal.hidden = false;
    if (changelogCache) {
      renderChangelog(changelogCache);
      return;
    }
    fetch("changelog.json")
      .then((res) => res.json())
      .then((entries) => {
        changelogCache = entries;
        // The modal may have been closed again before this resolves.
        if (!els.changelogModal.hidden) renderChangelog(entries);
      })
      .catch(() => {
        if (els.changelogModalBody) els.changelogModalBody.textContent = tr("無法載入版本紀錄。");
      });
  }

  function closeChangelogModal() {
    if (els.changelogModal) els.changelogModal.hidden = true;
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
      routeSelectedFiles(files);
    } catch (error) {
      showError(tr("載入範例照片失敗：{v1}", { v1: error.message || error }));
    } finally {
      if (els.demoImagesGrid) els.demoImagesGrid.classList.remove("is-loading");
      updateDemoImagesConfirmBtn();
    }
  }

  // Moves #advanced-settings-fold (人像設定) between its two UPLOAD-page homes,
  // if it isn't already in the one asked for: inline under the drop zone
  // (#upload-inline-settings-row-2, re-themed into a pill there -- see
  // index.html) or, on narrow viewports, row 1 alongside the collapsed
  // settings menu (setupCompactUploadSelectors). It no longer travels to
  // #ap-settings-modal: that page has its own #ap-advanced-settings-fold, so
  // showing a case's settings there leaves this one alone. Always collapsed
  // when moved -- being reparented (not rebuilt), its `open` state would
  // otherwise survive both a close/reopen and a move to the other home.
  function ensureAdvancedSettingsFoldIn(body) {
    if (!els.advancedSettingsFold) return;
    if (body && els.advancedSettingsFold.parentElement !== body) {
      // On the upload page this row also contains Run. Keep the portrait
      // settings pill first so the two controls read as one left-to-right
      // toolbar; modal bodies simply append the shared fold as before.
      const runControl = body.querySelector('.split-run');
      if (runControl) body.insertBefore(els.advancedSettingsFold, runControl);
      else body.appendChild(els.advancedSettingsFold);
    }
    els.advancedSettingsFold.open = false;
  }

  // One shared request setting, moved into whichever page's settings modal
  // is open. Keeping one checkbox avoids mirrored state and duplicate IDs.
  function ensureTwoStageEditRowIn(body) {
    if (!els.twoStageEditRow) return;
    if (body && els.twoStageEditRow.parentElement !== body) {
      body.appendChild(els.twoStageEditRow);
    }
    if (body && els.findingsCardsRow && els.findingsCardsRow.parentElement !== body) {
      body.appendChild(els.findingsCardsRow);
    }
  }

  // Upload-page settings overlay -- same open/close shape as the error modal
  // above, just toggling #settings-modal's own hidden attribute. Holds only
  // 生成張數 + debug-only rows now (already populated/wired above via
  // wireOptionPicker) -- 分析模型/生成模型/長寬比/人像修正 sit inline under the
  // drop zone instead (#upload-inline-settings), so opening this doesn't
  // need to reparent anything itself anymore.
  function openSettingsModal() {
    ensureTwoStageEditRowIn(els.settingsModalBody);
    if (els.settingsModal) els.settingsModal.hidden = false;
  }

  function closeSettingsModal() {
    if (els.settingsModal) els.settingsModal.hidden = true;
  }

  // Results/analysis-preview page settings overlay -- same shape as
  // openSettingsModal/closeSettingsModal above, just toggling
  // #ap-settings-modal's own hidden attribute instead. App preferences only:
  // every per-request control it used to hold (分析引擎/美化引擎/長寬比/
  // 生成張數/人像設定) now lives in #ap-case-settings-modal, opened from
  // the summary pill next to 美化 instead of from this shared gear. 兩階段美化
  // stays here deliberately -- it is one switch shared by both pages
  // (ensureTwoStageEditRowIn moves the single row between them), not a value
  // a case carries, so splitting it per-case would mean the same mirrored-
  // state problem 人像設定 already had to be split to avoid.
  function openApSettingsModal() {
    ensureTwoStageEditRowIn(els.apSettingsModalBody);
    if (els.apSettingsModal) els.apSettingsModal.hidden = false;
  }

  function closeApSettingsModal() {
    if (els.apSettingsModal) els.apSettingsModal.hidden = true;
  }

  // Per-case settings overlay (#ap-case-settings-modal) -- the pickers that
  // used to sit in openApSettingsModal's own body. Opened from the summary
  // pill beside 美化 rather than from the shared gear, because unlike the
  // preferences left behind in that modal, everything in here is stamped onto
  // this case's next edit and belongs next to the button that starts it.
  function openApCaseSettingsModal() {
    // Collapsed on every open, same as #ap-advanced-settings-fold gets in
    // openApSettingsModal -- nothing else resets an `open` left from last time.
    if (els.apAdvancedSettingsFold) els.apAdvancedSettingsFold.open = false;
    if (els.apCaseSettingsModal) els.apCaseSettingsModal.hidden = false;
  }

  function closeApCaseSettingsModal() {
    if (els.apCaseSettingsModal) els.apCaseSettingsModal.hidden = true;
  }

  // Theme palette is global (#theme-toggle-btn, header) and persists per
  // browser. THEME_CYCLE is the fixed click order, and these three objects are
  // the single source of truth for what counts as a selectable theme: a key
  // missing from THEME_LABELS is treated as invalid by applyTheme and falls
  // back to sky. ocean/forest/paper/peach keep their full body[data-theme]
  // CSS (styles.css) but are deliberately absent here -- hiding a theme means
  // dropping it from these lists, not deleting its stylesheet, so it can be
  // brought back by re-adding the key alone. peach ("淺色・暖橘") is hidden as
  // of this change; anyone who had it saved lands on sky at next load.
  //
  // sky (the default) comes first and is light only. Each of the three other
  // families ships both a light and a dark palette, and THEME_CYCLE walks them
  // in pairs (dark then light of one family, then the next family) so a single
  // click is always the small step -- same hue, other brightness -- and every
  // second click is the bigger one.
  const THEME_KEY = "photomaster-theme";
  const THEME_CYCLE = [
    "sky",
    "default", "purple-light",
    "lilac-dark", "lilac",
    "caramel-dark", "caramel",
  ];
  // The seven names are independent rather than three family names under a
  // 深色/淺色 prefix: the prefix scheme forced one noun to describe both halves
  // of a family, and the two halves are not the same palette -- purple-light is
  // violet+indigo where default is neon purple+hot pink, so "紫" was accurate
  // for neither. Each name now answers only for its own accent pair:
  //   極光  #A855F7 neon purple + #EC4899 hot pink, on near-black
  //   鳶尾  #7C3AED violet + #4F46E5 indigo -- the iris flower's own two colors
  //   夜霞  #C967A2 rose + #B788C8 light violet, floating on near-black
  //   藕荷  #9C539C mauve + #C967A2 rose -- the traditional colour name for it
  //   燼火  #F1AA42 gold + #D56723 orange, embers on near-black
  //   暖陽  #D56723 orange + #F1AA42 gold, warm sun on cream
  //   YCO   #06A6D9 cyan + #0E9BA6 blue-green (YCE's brand pair), clear sky on white
  // No two share a character, on purpose: the old 紫 / 紫丁香 pair was the
  // linguistic form of the same "these two look alike" complaint the colours
  // themselves drew. Keep that rule when editing this map -- caramel's first
  // name here was 蜜光, which collided with 極光 on 光 and had to be redone.
  // Brightness is no longer spelled out either, so the word has to carry it
  // (極光/夜霞/燼火 read dark, 鳶尾/藕荷/暖陽 read light) together with the
  // swatch beside the label. 燼火 vs 暖陽 is the pair that leans on this
  // hardest, since both are the same orange+gold: embers against sunlight.
  // Six are two glyphs and YCO is three Latin letters -- see the width lock in styles.css, which is
  // measured off that fact.
  const THEME_LABELS = {
    sky: tr("YCO"),
    default: tr("極光"),
    "purple-light": tr("鳶尾"),
    "lilac-dark": tr("夜霞"),
    lilac: tr("藕荷"),
    "caramel-dark": tr("燼火"),
    caramel: tr("暖陽"),
  };
  const THEME_SWATCH_CLASS = {
    sky: "theme-swatch-sky",
    default: "theme-swatch-purple",
    "purple-light": "theme-swatch-purple-light",
    "lilac-dark": "theme-swatch-lilac-dark",
    lilac: "theme-swatch-lilac",
    "caramel-dark": "theme-swatch-caramel-dark",
    caramel: "theme-swatch-caramel",
  };
  // Drives body[data-theme-mode], which every light-only patch in styles.css
  // keys off (see the note beside the palettes there) -- a theme added to the
  // maps above but missing here would render with :root's dark-tuned black
  // shadows over its light cards, so the two lists have to stay in step.
  const THEME_MODE = {
    sky: "light",
    default: "dark",
    "purple-light": "light",
    "lilac-dark": "dark",
    lilac: "light",
    "caramel-dark": "dark",
    caramel: "light",
  };
  const THEME_SCROLLBAR_RGB = {
    sky: "6,166,217",
    default: "147,51,234",
    "purple-light": "124,58,237",
    ocean: "14,165,194",
    forest: "16,185,129",
    paper: "29,78,216",
    peach: "207,81,69",
    "lilac-dark": "201,103,162",
    lilac: "136,71,136",
    "caramel-dark": "241,170,66",
    caramel: "185,81,27",
  };
  function applyTheme(theme, persist = true) {
    const selected = Object.prototype.hasOwnProperty.call(THEME_LABELS, theme) ? theme : "sky";
    document.body.dataset.theme = selected;
    if (selected === "default") delete document.body.dataset.theme;
    // Set even for dark themes (rather than only when light) so the attribute
    // always states the answer -- "no attribute" would otherwise mean both
    // "dark theme" and "applyTheme never ran".
    document.body.dataset.themeMode = THEME_MODE[selected];
    document.documentElement.style.setProperty(
      "--scrollbar-accent-rgb",
      THEME_SCROLLBAR_RGB[selected]
    );
    if (els.themeToggleBtn) {
      // The button's label IS the current theme's name -- the swatch says which
      // color, the text says which of the seven, and the pair needs no separate
      // noun for "theme". The seven names are not all
      // the same rendered width, so the label is still width-locked in CSS
      // (#theme-toggle-btn .btn-label) to the widest: this button sits
      // immediately left of #report-btn-spacer, and #report-btn is
      // position:fixed off that spacer's measured position, so letting the
      // label resize would shift the whole header -- and the fixed button with
      // it -- on every click.
      const label = els.themeToggleBtn.querySelector(".btn-label");
      if (label) label.textContent = THEME_LABELS[selected];
      // title no longer repeats the name now that it is visible; it carries the
      // affordance the noun-label used to imply instead.
      els.themeToggleBtn.setAttribute("aria-label", tr("主題：{v1}（點擊切換）", { v1: THEME_LABELS[selected] }));
      els.themeToggleBtn.title = tr("點擊切換主題");
    }
    const swatch = document.getElementById("theme-toggle-swatch");
    if (swatch) {
      Object.values(THEME_SWATCH_CLASS).forEach((cls) => swatch.classList.remove(cls));
      swatch.classList.add(THEME_SWATCH_CLASS[selected]);
    }
    if (persist) {
      try { localStorage.setItem(THEME_KEY, selected); } catch (_) { /* storage may be blocked */ }
    }
  }
  function setupThemeToggle() {
    let saved = "sky";
    try { saved = localStorage.getItem(THEME_KEY) || "sky"; } catch (_) { /* storage may be blocked */ }
    applyTheme(saved, false);
    if (els.themeToggleBtn) {
      els.themeToggleBtn.addEventListener("click", () => {
        const current = THEME_CYCLE.indexOf(document.body.dataset.theme || "default");
        const next = THEME_CYCLE[(current + 1) % THEME_CYCLE.length];
        applyTheme(next);
      });
    }
  }

  // #header-settings-btn (index.html) is the single, always-visible entry
  // point into 設定 now -- picks #settings-modal or #ap-settings-modal based
  // on which section is actually showing, since the two pages' pickers
  // (模型/長寬比/生成張數) are independent state, unlike the shared
  // #advanced-settings-fold reparented above. Falls back to the results-page
  // modal for "processing" too, on the assumption the results modal is the
  // closer match once analysis is running/done than the upload modal.
  function openContextSettingsModal() {
    if (els.sections.upload?.classList.contains("visible")) {
      openSettingsModal();
    } else {
      openApSettingsModal();
    }
  }

  // 修圖風格 picker (#ap-style-modal): the full-size example-image grid for
  // one category at a time, opened by clicking that category's card in the
  // review tab. showStyleGroup just flips which #ap-*-list is visible; it doesn't
  // touch which radio is checked in any of them (that's still plain radio
  // input behavior, untouched by this).
  const STYLE_GROUPS = ["pose", "angle", "lighting", "size"];
  // Chinese labels for #ap-style-options-title (showStyleGroup below) --
  // kept in sync by hand with each .ap-style-card-name (index.html) and with
  // STYLE_OPTION_GROUPS' own labels in results.js, since that map is private
  // to results.js's module closure and isn't exported for reuse here.
  const STYLE_GROUP_TITLES = { pose: tr("姿勢"), angle: tr("拍攝角度"), lighting: tr("光線"), size: tr("取景範圍") };
  const STYLE_GROUP_NOTE = tr("（圖例僅示意風格，實際呈現依原圖調整）");
  const STYLE_GROUP_NOTES = {
    pose: tr("（圖例僅示意風格，實際姿勢依原圖調整）"),
  };

  function styleGroupListEl(group) {
    return els[`ap${group[0].toUpperCase()}${group.slice(1)}List`];
  }

  // Generic has-more-above/has-more-below inset-shadow scroll cue -- reused
  // by #ap-recipe-list/#ap-face-detail (their own listeners, further down)
  // as well as .ap-style-option-grid (updateStyleOptionScrollCue below,
  // which also drives #ap-style-scroll-hint, a text cue specific to that
  // one picker -- kept split out so this generic version doesn't reach for
  // an element that has nothing to do with whichever list called it).
  function updateListScrollCue(list) {
    if (!list) return false;
    const canScroll = list.scrollHeight > list.clientHeight + 1;
    const hasMoreAbove = canScroll && list.scrollTop > 0;
    const hasMoreBelow = canScroll && list.scrollTop + list.clientHeight < list.scrollHeight - 1;
    list.classList.toggle("has-more-above", hasMoreAbove);
    list.classList.toggle("has-more-below", hasMoreBelow);
    return hasMoreBelow;
  }

  // .ap-style-option-grid's own inset-shadow scroll cue (index.html) is easy
  // to miss on its own, so #ap-style-scroll-hint spells it out in words --
  // both are driven off the same has-more-below/has-more-above read here.
  // Called whenever the visible list's scroll position changes (the `scroll`
  // listeners below) and whenever showStyleGroup swaps in a different list
  // (a different content height, freshly scrolled to the top).
  function updateStyleOptionScrollCue(list) {
    const hasMoreBelow = updateListScrollCue(list);
    if (els.apStyleScrollHint) els.apStyleScrollHint.hidden = !hasMoreBelow;
  }

  // ── #page-scroll-hint: the same "there's more below" cue, for the page ──
  // The 分析 (pending review) and 結果 screens both lead with a full-bleed
  // preview photo. On a short viewport that photo plus the header and the
  // step indicator is the entire first screenful, so the review panel /
  // analysis detail under it is invisible AND unannounced -- nothing on
  // screen distinguishes "scroll for the rest" from "that's the whole page".
  // This is the upload screen's one excluded case: it's a form, it has no
  // single element big enough to swallow the fold, and it isn't part of the
  // flow this cue belongs to.
  //
  // Both of those screens are #results-section: the pending review is
  // showPendingResults' state of it, and 結果 is the same section once the
  // edit lands. #processing-section is NOT the review screen -- it is the
  // 分析中 thinking-rings animation, which is why it's absent here.
  const PAGE_SCROLL_HINT_SECTIONS = new Set(["results"]);
  // ...and the section alone doesn't settle it, because 修圖中 is that same
  // #results-section with the photo swapped for the #canvas-loading /
  // #batch-main-loading animation. Both waiting screens are the same case:
  // whatever is below the fold is a placeholder for content that hasn't
  // arrived, so pointing at it promises something that isn't there, and the
  // cue reads as if the page were already finished. The flow stage is what
  // tells them apart -- 分析中 is "analyzing", 修圖中 is "editing", and it
  // covers the batch flow (which animates in place, with no section change
  // to key off) for free.
  const PAGE_SCROLL_HINT_STAGES = new Set(["awaiting-review", "done"]);
  // showSection's event is the only thing that knows which screen is up
  // (dom.js owns .visible, and there's no getter) -- mirror it here rather
  // than re-deriving it from the DOM on every scroll event.
  let currentSectionName = "upload";
  // Latched for the rest of this screen's visit the moment the user scrolls
  // the page or taps the cue: either one means they've found what's below,
  // so bringing it back every time they return to the top would be nagging
  // about something already answered. Cleared only on the next section
  // change -- showSection dispatches one even when the screen name doesn't
  // actually change, so each leg of 分析 → 修圖中 → 結果 (and every arrival
  // from 最近照片) counts as its own page and gets its own one chance.
  let pageScrollHintDismissed = false;

  // Mirrors the max-width the floating-Run rules are scoped to (styles.css);
  // above it .ap-actions-row is in normal flow and shares nothing with this cue.
  const FLOATING_RUN_MAX_WIDTH = 900;

  // How far to lift #page-scroll-hint so it clears the floating Run band
  // rather than centering itself straight through it -- see the
  // --page-scroll-hint-lift comment in styles.css for why it collides at all.
  // Measured, not a constant: the band holds 美化 (one pill) or
  // #ap-restart-actions' 重新分析/重新一鍵美化 pair, either one alongside the
  // settings icon, and that group wraps to a second row on a narrow screen --
  // so the one offset that would be wrong is a fixed one. Called from
  // updatePageScrollHint, which already forces a layout for scrollHeight, so
  // this measurement rides along with that flush instead of adding its own.
  let lastPageScrollHintLift = null;

  function syncPageScrollHintLift() {
    const group = els.resultsCanvasRow?.querySelector(".ap-actions-row .ap-settings-run-row");
    const floating = !!group
      && window.innerWidth <= FLOATING_RUN_MAX_WIDTH
      && Array.from(group.children).some((el) => !el.hidden);
    // +10 = .ap-actions-row's own gap, so the cue sits off the band by the
    // same distance the band's own contents sit off each other.
    const lift = floating ? `${Math.round(group.getBoundingClientRect().height) + 10}px` : "0px";
    // Only on an actual change: this runs on every scroll event, and writing
    // the same value back still invalidates style for the whole document.
    if (lift !== lastPageScrollHintLift) {
      lastPageScrollHintLift = lift;
      document.documentElement.style.setProperty("--page-scroll-hint-lift", lift);
    }
  }

  // A caller can land in the middle of a re-layout that finishes later in the
  // same task -- showPendingResults calls setFlowStage("awaiting-review")
  // before it adds .showing-analysis, so at that instant the review panel is
  // still unconstrained and the page measures thousands of px tall, then
  // shrinks to one screenful a few lines later. The body ends the task the
  // same size it started, so the ResizeObserver never fires and the cue stayed
  // up on a page with nothing below. Re-checking once in the next animation
  // frame -- after the task's remaining layout work, before anything is
  // painted -- settles it without a visible flash. Coalesced, so the scroll
  // and resize paths still cost one extra measurement per frame at most.
  let pageScrollHintRecheckFrame = 0;

  function updatePageScrollHint() {
    applyPageScrollHint();
    if (pageScrollHintRecheckFrame) return;
    pageScrollHintRecheckFrame = requestAnimationFrame(() => {
      pageScrollHintRecheckFrame = 0;
      applyPageScrollHint();
    });
  }

  function applyPageScrollHint() {
    const hint = els.pageScrollHint;
    if (!hint) return;
    // Dismissal, screen and load state first, content height last: this runs
    // on every scroll event and reading scrollHeight forces a layout, while
    // these three cost nothing.
    //
    // The visibility check: a history record opened from 最近照片 keeps
    // #results-canvas-row at visibility:hidden while it loads
    // (hideHistoryCanvasRow) -- deliberately still laid out, so the results
    // page has its full height while painting nothing. That is the one state
    // where "this page scrolls" is true and "there's something down there to
    // look at" isn't, and it's what put this cue under a blank screen during
    // the jump. Read off the inline style hideHistoryCanvasRow itself writes
    // (its only writer), not getComputedStyle, so it stays free. And
    // releaseHistoryCanvasRow calls back in here, because handing the row
    // back changes only paint, not layout, and so wakes no observer.
    if (pageScrollHintDismissed
      || els.resultsCanvasRow?.style.visibility === "hidden"
      || !PAGE_SCROLL_HINT_STAGES.has(flowStage)
      || !PAGE_SCROLL_HINT_SECTIONS.has(currentSectionName)) {
      hint.hidden = true;
      return;
    }
    // 24px of slack, not >0: a page overflowing by a hairline (sub-pixel
    // rounding, a shadow's bleed, the floating Run pill's reserved
    // padding-bottom landing a couple of px over) has nothing down there
    // worth pointing at, and a cue that appears for it reads as broken.
    syncPageScrollHintLift();
    hint.hidden = document.documentElement.scrollHeight - window.innerHeight - window.scrollY <= 24;
  }

  // The 8px floor keeps a rubber-band overscroll (or a browser restoring a
  // previous scroll offset) from counting as "the user scrolled". Latched
  // here rather than inside updatePageScrollHint because showSection clears
  // the flag BEFORE doing its own scroll-to-top, and re-reading a still-
  // stale scrollY in there would re-dismiss the screen we just reset.
  function handlePageScroll() {
    if (window.scrollY > 8) pageScrollHintDismissed = true;
    updatePageScrollHint();
  }

  // Tapping the cue does the scroll it's asking for -- on the viewports this
  // exists for, what it points at is a full swipe away. 72% of a screenful,
  // not a full one: leaving the bottom of the photo in view keeps the move
  // legible as "the page scrolled" rather than a jump to unrelated content.
  // Dismissed explicitly, not left to the scroll it triggers: at the very
  // bottom of a barely-scrollable page that scroll can be a no-op.
  function handlePageScrollHintClick() {
    pageScrollHintDismissed = true;
    updatePageScrollHint();
    window.scrollBy({ top: Math.round(window.innerHeight * 0.72), behavior: "smooth" });
  }

  // group === null hides every list (no g in STYLE_GROUPS ever equals null).
  // #ap-style-options-title (top of that frame) is set to the same
  // 姿勢/拍攝角度/光線/取景範圍 text as the category's own
  // .ap-style-card-name in the tab (see STYLE_GROUP_TITLES above).
  function showStyleGroup(group) {
    STYLE_GROUPS.forEach((g) => {
      const list = styleGroupListEl(g);
      if (list) list.hidden = g !== group;
    });
    if (els.apStyleOptionsTitle) {
      els.apStyleOptionsTitle.textContent = group ? (STYLE_GROUP_TITLES[group] || group) : "";
    }
    if (els.apStyleOptionsNote) {
      const note = group ? (STYLE_GROUP_NOTES[group] || STYLE_GROUP_NOTE) : "";
      els.apStyleOptionsNote.textContent = note || "";
      els.apStyleOptionsNote.hidden = !note;
    }
    if (els.apStyleOptionsFrame) els.apStyleOptionsFrame.hidden = !group;
    // Freshly shown list starts scrolled to the top regardless of where a
    // previous visit to this same category left it -- re-read the cue for
    // that state rather than leaving it holding whichever group's stale
    // has-more-below it last computed.
    if (group) {
      const list = styleGroupListEl(group);
      if (list) {
        list.scrollTop = 0;
        updateStyleOptionScrollCue(list);
      }
    } else if (els.apStyleScrollHint) {
      els.apStyleScrollHint.hidden = true;
    }
  }

  // The category buttons stay inline in the 修圖風格 tab. Opening one sends
  // only its example-image list to this standalone modal.
  function openStyleModal(group) {
    if (!els.apStyleModal) return;
    showStyleGroup(group || null);
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
    const label = isFullscreen ? tr("返回原視窗") : tr("全螢幕檢視比較");
    els.compareFullscreenBtn.title = label;
    els.compareFullscreenBtn.setAttribute("aria-label", label);
  }

  function isCompareFullscreen() {
    return !!els.compareCard?.classList.contains("is-fullscreen");
  }

  // Toggling .is-fullscreen changes the rendered image rectangle through CSS.
  // The overlay is positioned from getBoundingClientRect(), so redraw after
  // the class change has reached layout (and once more after the next frame).
  // The delayed pass also covers a responsive layout settling after the card
  // leaves/returns to normal flow.
  let compareOverlayRefreshTimer = null;
  function refreshCompareOverlaysAfterLayout() {
    const redraw = () => {
      drawAnalysisBoxes();
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(() => {
        redraw();
        window.requestAnimationFrame(redraw);
      });
    } else {
      redraw();
    }
    if (compareOverlayRefreshTimer !== null) {
      window.clearTimeout(compareOverlayRefreshTimer);
    }
    compareOverlayRefreshTimer = window.setTimeout(() => {
      compareOverlayRefreshTimer = null;
      redraw();
    }, 420);
  }

  function openCompareFullscreen() {
    if (!els.compareCard || isCompareFullscreen()) return;
    const parent = els.compareCard.parentElement;
    if (parent) parent.style.height = `${parent.getBoundingClientRect().height}px`;
    els.compareCard.classList.add("is-fullscreen");
    if (els.compareFullscreenBackdrop) els.compareFullscreenBackdrop.hidden = false;
    setCompareFullscreenBtnState(true);
    refreshCompareOverlaysAfterLayout();
  }

  function closeCompareFullscreen() {
    if (!els.compareCard || !isCompareFullscreen()) return;
    els.compareCard.classList.remove("is-fullscreen");
    const parent = els.compareCard.parentElement;
    if (parent) parent.style.height = "";
    if (els.compareFullscreenBackdrop) els.compareFullscreenBackdrop.hidden = true;
    setCompareFullscreenBtnState(false);
    refreshCompareOverlaysAfterLayout();
  }

  function toggleCompareFullscreen() {
    if (isCompareFullscreen()) closeCompareFullscreen();
    else openCompareFullscreen();
  }

  // ── Core dispatcher: sidebar click -> whatever main-area view matches the
  // selected record's current status. Replaces the old grid/detail-pagination/
  // separate-review-screen split entirely -- one page, one selection model. ──
  async function selectBatchRecord(record) {
    if (!record) return;
    // If a done record's inline revise-panel is open, cancel its pending
    // wait directly rather than simulating a click on its cancel button --
    // that click's continuation runs as a microtask (via
    // waitForInlineAnalysisAction's promise), which would race against the
    // render we're about to do for the newly selected record.
    // Deliberately NOT closeInlineAnalysisPanel() here (an earlier version
    // of this did) -- renderBatchMain(record, true) below reopens the panel
    // for the new record regardless, via forceReopen, so closing it first
    // just meant #results-body briefly lost then regained .showing-analysis
    // for no reason, and #batch-sidebar's width split keys off exactly that
    // class -- every case switch visibly flickered the sidebar wide (its
    // plain, panel-closed width) then narrow again as the new record's
    // panel reopened, purely from this now-removed round trip.
    if (els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis")) {
      // Preserve in-progress face review controls
      // before replacing the panel with another batch photo. This is a local
      // snapshot only; no analyze/edit request is triggered.
      if (currentBatchRecord) captureBatchReviewSelections(currentBatchRecord);
      clearInlineAnalysisWait();
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
    const hideSwitchLoading = showingCanvas ? showRunSwitchLoadingIfSlow() : () => {};
    try {
      await renderBatchMain(record, true);
    } finally {
      hideSwitchLoading();
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
    renderBatchMain(record, true);
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
      analysis.subject_category,
      analysis.scene_category
    ] : []);
  }


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
  // .batch-sidebar's fixed height (index.html) -- caps #analysis-preview to the
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
  // #report-btn-spacer's position/size can shift with viewport width (the
  // 560px icon-only breakpoint, .logo's own clamp()) -- keep #report-btn
  // following it.
  window.addEventListener("resize", syncReportBtnPosition);

  // forceReopen: true only from a genuine switch to a different record
  // (selectBatchRecord/maybeAutoAdvanceToBatchResult) -- threaded down to
  // showBatchDoneMain's own openInlineAnalysisForCurrentContext call so a
  // switch always loads the newly-selected record's own review state,
  // without selectBatchRecord having to closeInlineAnalysisPanel() first
  // just to defeat that function's "already showing, no-op" guard. That
  // close-then-reopen round trip was the actual cause of #batch-sidebar's
  // width visibly flickering wide-then-narrow on every case switch (see
  // that class's own comment, styles.css) -- #results-body loses and
  // immediately regains .showing-analysis, which is what its width split
  // keys off. refreshIfSelected's own background-refresh call leaves this
  // at its default false, since that's the SAME record re-rendering in
  // place, not a switch.
  async function renderBatchMain(record, forceReopen = false) {
    if (!record) return;
    setCurrentCaseTimes(record);
    updateApSubjectTags(record);
    // A photo still being reframed gets its indicators once its panel reopens.
    if (!reframesInFlight.has(record)) syncReframeBusy();
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
      await showBatchAnalyzedMain(record);
    // Guard against the brief window (mid-revise, with N new runs in flight
    // alongside older already-done ones) where syncRecordMirror's aggregate
    // "any run done" check flips record.status to "done" before the specific
    // mirrored run has actually resolved -- record.result would still be
    // null then, so fall through to the loading view instead of crashing.
    } else if (record.status === "done" && record.result) {
      if (wasThisRecordRotating) await finishProcSubRotation();
      await showBatchDoneMain(record, batchSettings, forceReopen);
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
      els.batchMainLoading.classList.remove("is-error");
    }
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
      els.batchMainLoadingTitle.textContent = analyzing ? tr("正在分析照片") : tr("AI 正在美化");
    }
    // Same split the title above makes, off the record's own status -- this
    // view only ever shows for a record that is running, and "running" here
    // means exactly one of those two stages. Also the path a still-going
    // record resumed from 最近照片 arrives through
    // (viewProcessingHistoryRecord), which is why that path needs no
    // indicator bookkeeping of its own.
    setFlowStage(analyzing ? "analyzing" : "editing");
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
      // batchSettings is only populated during an actual multi-photo batch
      // run -- a single-photo history record resumed via
      // viewProcessingHistoryRecord has no batch in flight (batchSettings is
      // null all session), so it passes aiPhotoEditing/imageModel directly on
      // record itself instead. Falling back to batchSettings unconditionally
      // here used to throw (Cannot read properties of null) the instant this
      // ran for such a record, which corrupted the shared rotation timer
      // (procSubGetMessages was already pointed at this crashing closure by
      // startProcSubRotation before the throw) and silently broke ANY other
      // rotation -- including the original upload's own -- reading it
      // afterward, well past the point this function's own caller gave up.
      startProcSubRotation(analyzing
        ? analyzeSubMessages
        : () => editSubMessages({
            aiPhotoEditing: record.aiPhotoEditing != null ? record.aiPhotoEditing : batchSettings?.aiPhotoEditing,
            imageModel: record.imageModel || batchSettings?.imageModel,
            doneCount: 0,
            runCount: 1,
            analysis: record.analysis,
            selections: buildEditSelections({ options: effectiveOptions(record), recipeChecked: record.recipeChecked,
              advancedOptions: record.advancedOptions || batchSettings?.advancedOptions,
              faceIssueOverrides: record.faceIssueOverrides, bodyReshapeEnabled: record.bodyReshapeEnabled }),
            userPrompt: record.userPrompt,
            overwritePrompt: record.overwritePrompt,
            tipsOnly: record.progressTipsOnly === true || !record.analysis
          }), startIndex, record);
      activeBatchRotationRecord = record;
    }
  }

  function showBatchMainError(record) {
    hideAllBatchMainViews();
    // The reason line below is written through the very same
    // #batch-main-loading-text the rotating phrasings target (see
    // setProcSub), so any rotation still running -- this record's own,
    // now-dead one, or a different record's still-live one -- simply
    // overwrote it a second or two later, leaving the card reading
    // "AI 正在美化…" underneath a 處理失敗 title. Paused rather than merely
    // stopped: saving the active record's position first (same bookkeeping
    // showBatchMainLoading does when the view switches away from a record
    // that hasn't finished) lets a genuinely still-running OTHER record pick
    // its rotation back up where it left off when the user returns to it.
    if (activeBatchRotationRecord) activeBatchRotationRecord.procSubIndex = procSubRotateIndex;
    stopProcSubRotation();
    if (!els.batchMainLoading) return;
    els.batchMainLoading.hidden = false;
    els.batchMainLoading.classList.add("is-error");
    if (els.batchMainLoadingThumb) els.batchMainLoadingThumb.src = record.thumbUrl || record.previewUrl;
    if (els.batchMainLoadingEyebrow) els.batchMainLoadingEyebrow.textContent = "Photo Master hit a snag";
    if (els.batchMainLoadingTitle) els.batchMainLoadingTitle.textContent = tr("處理失敗");
    if (els.batchMainLoadingText) {
      els.batchMainLoadingText.textContent = record.error || tr("處理失敗，請稍後再試。");
    }
  }

  // Pre-first-edit review, rendered exactly like a finished result (just with
  // no generated image/QA yet) via renderResult's pending mode -- same
  // treatment showPendingResults gives the single-image flow's equivalent
  // state, instead of the old #batch-main-loading placeholder card. Reuses
  // startInlineBatchReview (via openInlineAnalysisForCurrentContext, now that
  // currentDetailBatchRecord() recognizes "analyzed" too) for the actual
  // options panel + Run button -- its tail (runBatchPool + editBatchItem) is
  // the same submission path continueBatchRecords already used for this case.
  async function showBatchAnalyzedMain(record) {
    const hasOriginal = !!record.previewUrl;
    if (!hasOriginal) {
      els.originalImg.removeAttribute("src");
    } else {
      // setImgSrcPreloaded (not a bare src assignment) keeps #original-img
      // from ever showing the broken-image icon/alt text while this record's
      // own photo is a different one than whatever was on screen before --
      // same reasoning as showBatchDoneMain's identical call.
      await setImgSrcPreloaded(els.originalImg, record.previewUrl);
    }
    if (currentBatchRecord !== record) return; // switched to another case meanwhile

    const alreadyShowing = els.resultsCanvasRow && !els.resultsCanvasRow.hidden;
    if (!alreadyShowing) hideAllBatchMainViews();
    els.resultsCanvasRow.hidden = false;
    // Analysis is in, waiting for Run -- also what pulls the (shared)
    // indicator back for a freshly-analyzed record after a previous batch
    // record's flow had advanced it further.
    setFlowStage("awaiting-review");
    if (els.showPromptBtn) els.showPromptBtn.hidden = !isDebugUiActive;
    updateCostBtn();
    // Same "下載原圖" treatment as the single-image flow's own pending-review
    // screen (showPendingResults) -- no edit has run yet, so this can only
    // ever offer the source photo, handled by handleDownloadClick's
    // currentBatchRecord-is-"analyzed" branch above.
    if (els.downloadLink) {
      els.downloadLink.hidden = !record.file;
      els.downloadLink.title = tr("下載原圖");
      els.downloadLink.setAttribute("aria-label", tr("下載原圖"));
    }
    resetRunSwitcher(); // no RUNs exist yet for this record

    // Same partial-timing display as the single-flow review screen
    // (showPendingResults) -- record.analyzeTimings only has Step 2 so far.
    populateTimingSection(record.analyzeTimings);

    renderResult(
      { data: {}, analysis: record.analysis, preset: batchSettings.preset, imageModel: null, hasOriginal },
      { pending: true }
    );
    openInlineAnalysisForCurrentContext(true);
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
    if (els.runProgressLabel) els.runProgressLabel.textContent = tr("產生中… {doneCount}/{length} 張", { doneCount, length: runs.length });
    if (els.runProgressFill) els.runProgressFill.style.width = `${Math.round((doneCount / runs.length) * 100)}%`;
  }

  // Hides/clears the prev/next arrows, the "N / total" label, and the
  // progress bar, and drops the arrows' current run list/index/select-callback.
  function resetRunSwitcher() {
    if (els.runSwitchPrev) els.runSwitchPrev.hidden = true;
    if (els.runSwitchNext) els.runSwitchNext.hidden = true;
    if (els.runCountLabel) { els.runCountLabel.hidden = true; els.runCountLabel.textContent = ""; }
    if (els.runProgress) els.runProgress.hidden = true;
    hideVersionStrip();
    currentRunList = null;
    currentRunIndex = null;
    currentRunOnSelect = null;
  }

  // Rebuilt only when the run LIST itself changes -- every other render
  // (stepping between two images of one case) just moves .is-current, so the
  // tiles' <img>s are never re-created and never re-request their thumbnails.
  let versionStripRuns = null;
  // The run list a 幫我分析 landed on (refreshVersionsAfterEdit's
  // pendingAnalysis): that analysis is the case's newest version, shown as a
  // 待美化 tile after the list's last one. Tied to the list object rather
  // than a flag, so the next 美化 or opening another record -- both build a
  // new list -- drops it without anyone having to remember to.
  let versionStripPendingRuns = null;

  function hasPendingAnalysisTile(runs) {
    return !!runs && runs.length > 0 && versionStripPendingRuns === runs;
  }

  // The run switcher / 美化紀錄 has something to show: more than one image,
  // the pending tile, or -- as on the chat page -- a history case's versions,
  // even just the one (it is also where 接著這張調整 starts).
  function hasRunSwitcherContent(runs) {
    return !!runs && (runs.length > 1 || hasPendingAnalysisTile(runs)
      || runs.some((r) => r.versionTotal));
  }

  // Not a button: there is no image to land on yet. It is the version the
  // next 美化 produces from the analysis now in the panel -- the original
  // under a dashed accent frame, the way an empty slot waiting to be filled
  // reads.
  function buildPendingAnalysisTile(thumbUrl) {
    const wrap = document.createElement("div");
    wrap.className = "version-pending";
    const tile = document.createElement("div");
    tile.className = "version-pending-tile";
    tile.title = tr("已分析完成，按「美化」就會產生這一組");
    tile.setAttribute("aria-label", tile.title);
    const img = document.createElement("img");
    img.src = thumbUrl;
    img.alt = "";
    img.loading = "lazy";
    tile.append(img);
    const label = document.createElement("span");
    label.className = "version-group-label";
    label.textContent = tr("待美化");
    wrap.append(tile, label);
    return wrap;
  }

  // The 調整中 slot the chat page has (ResultsCanvas's pending): built hidden
  // with the strip, shown by syncAdjustingTile.
  function buildAdjustingTile() {
    const group = document.createElement("div");
    group.className = "version-group is-pending";
    group.hidden = true;
    const tiles = document.createElement("div");
    tiles.className = "version-group-tiles";
    const tile = document.createElement("div");
    tile.className = "version-tile is-pending";
    tile.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
      + '<path d="M4 8h5M13 8h7M4 16h9M17 16h3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
      + '<circle cx="11" cy="8" r="2.2" stroke="currentColor" stroke-width="1.8"/>'
      + '<circle cx="15" cy="16" r="2.2" stroke="currentColor" stroke-width="1.8"/></svg>';
    tiles.append(tile);
    const label = document.createElement("span");
    label.className = "version-group-label";
    label.textContent = tr("調整中");
    group.append(tiles, label);
    return group;
  }

  // 不調整了 (#ap-continue-exit): the unsent request, edits and base pick go --
  // the panel is back on the version on screen as it first opens there,
  // even when another version was being adjusted.
  async function leaveAdjusting() {
    const run = singleRuns?.[singleMirrorIndex];
    if (!run?.versionId) return;
    if (isReviewDirty() && !(await showConfirmModal(tr("還沒美化的調整會清掉。確定不調整了嗎？"),
      { confirmLabel: tr("不調整了"), cancelLabel: tr("留在這裡"), danger: false }))) return;
    if (singleRuns?.[singleMirrorIndex] !== run) return;
    panelContinuation = null;
    panelContinuationStash = null;
    panelOriginStash = null;
    panelBase = null;
    await loadContinuationBase(singleRuns, run);
    applyHistoryVersionContext(run, null);
    syncVersionStripCurrent();
    if (els.resultsCanvasRow?.classList.contains("showing-analysis")) openInlineAnalysisForCurrentContext(true);
    else syncAdjustingTile();
  }

  // Shown while 調整中 (isAdjusting), until a 美化, 不調整了, or a 取消 that
  // leaves nothing unsent. Not on top of a 待美化 tile, which already says
  // the next version is waiting.
  function syncAdjustingTile() {
    syncApContinueExit();
    const slot = els.versionStripTrack?.querySelector(".version-group.is-pending");
    if (!slot) return;
    const runs = versionStripRuns;
    slot.hidden = !(runs === singleRuns && !hasPendingAnalysisTile(runs) && isAdjusting());
  }
  // Mirrors whichever currentIdx the last renderVersionStrip call was given --
  // kept around purely so openVersionStripPreviewModal (mobile's tap
  // alternative to the hover preview, see below) can mark the right tile
  // .is-current without needing its own copy of that argument threaded in.
  let versionStripCurrentIdx = null;
  // True while the strip's 原圖 tile is the picked one: the photo is pinned
  // to the original, so #compare-toggle-btn has nothing to compare against
  // (disabled) and the download, which would still hand over the run's
  // result, is put away until a version tile is picked again.
  let versionStripOriginPicked = false;

  function pickVersionStripOrigin() {
    versionStripOriginPicked = true;
    setCompareView(false);
    if (els.compareToggleBtn) els.compareToggleBtn.disabled = true;
    if (els.downloadMenuWrap) els.downloadMenuWrap.hidden = true;
    syncVersionStripCurrent();
    syncOverviewCard();
    // On 原圖 with nothing as the base, nothing is being adjusted (isAdjusting).
    syncAdjustingTile();
  }

  function clearVersionStripOriginPick() {
    if (!versionStripOriginPicked) return;
    versionStripOriginPicked = false;
    if (els.compareToggleBtn) els.compareToggleBtn.disabled = false;
    if (els.downloadMenuWrap) els.downloadMenuWrap.hidden = false;
    syncVersionStripCurrent();
    syncOverviewCard();
    syncAdjustingTile();
  }

  // Positions #version-tile-preview over whichever tile the pointer is on,
  // reusing that tile's own <img src> (see the element's comment in
  // index.html for why that needs no extra fetch). Measured off
  // getBoundingClientRect, same reasoning as revealVersionTile below: the
  // tile's offsetParent is not the fixed-position popup's frame of
  // reference, so only viewport-relative coordinates line up.
  function showVersionTilePreview(tile, src) {
    if (!els.versionTilePreview || !els.versionTilePreviewImg) return;
    els.versionTilePreviewImg.src = src;
    const popup = els.versionTilePreview;
    const rect = tile.getBoundingClientRect();
    // Rendered once off-screen first so its own (fixed 168px) size is known
    // before the centring math below runs -- offsetWidth/Height on a
    // display:none-free, opacity:0 element are still real, so this is just
    // the popup's own box, not a layout read of the tile.
    const popupW = popup.offsetWidth || 168;
    const popupH = popup.offsetHeight || 168;
    const gap = 10;
    let top = rect.top - popupH - gap;
    // Flips below the tile once there isn't room above it -- the strip sits
    // near the bottom of the compare card, so "above" is right for most
    // tiles but wrong for one sitting near the very top of the viewport.
    if (top < 8) top = rect.bottom + gap;
    let left = rect.left + rect.width / 2 - popupW / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - popupW - 8));
    popup.style.top = `${Math.round(top)}px`;
    popup.style.left = `${Math.round(left)}px`;
    popup.classList.add("is-visible");
  }

  function hideVersionTilePreview() {
    if (els.versionTilePreview) els.versionTilePreview.classList.remove("is-visible");
  }

  function hideVersionStrip() {
    clearVersionStripOriginPick();
    if (!els.versionStrip) return;
    els.versionStrip.hidden = true;
    els.versionStrip.classList.remove("is-busy");
    if (els.versionStripTrack) els.versionStripTrack.textContent = "";
    versionStripRuns = null;
    versionStripCurrentIdx = null;
    hideVersionTilePreview();
    closeVersionStripPreviewModal();
    syncPhotoViewer();
  }

  // One tile per result image, grouped under its version's label, oldest
  // version on the left -- the same order and the same list the arrows walk,
  // so the two stay in lockstep by construction.
  //
  // Shown only for a version-tagged list (a history case, see
  // fetchHistoryVersionRuns). A live run_count>1 revise produces its images
  // over the following minutes and has no versions to compare, so it keeps
  // the arrows on their own rather than growing a strip that rearranges
  // itself while the user watches.
  // originThumbUrl: the 原圖 tile's image when the caller knows it better
  // than the page-wide originalImgThumbUrl -- a batch record passes its own
  // thumbnail, since that global tracks the single-image flow's photo.
  function renderVersionStrip(runs, currentIdx, originThumbUrl) {
    if (!els.versionStrip || !els.versionStripTrack) return;
    const pending = hasPendingAnalysisTile(runs);
    if (!hasRunSwitcherContent(runs) || !runs.some((r) => r.versionTotal)) {
      hideVersionStrip();
      return;
    }
    els.versionStrip.hidden = false;
    if (versionStripRuns !== runs) {
      // A new list is a new case (or a new version just landed): it opens on
      // its current run's After side, not on a 原圖 pick left from before.
      clearVersionStripOriginPick();
      versionStripRuns = runs;
      els.versionStripTrack.textContent = "";
      // The original is pinned in front of the versions and fenced off with a
      // divider, because every version is "original + that version's settings"
      // re-run from scratch -- never "the previous version, edited further".
      // /continue carries the prior job's srcKeys forward untouched, and srcKeys
      // has pointed at the original since the first upload. Without this tile a
      // flat row of thumbnails reads as a chain, which is the one thing it isn't.
      //
      // Clickable like the version tiles: picking it pins the photo to the
      // original (pickVersionStripOrigin), and picking any version returns.
      const origin = document.createElement("div");
      origin.className = "version-origin";
      const originTile = document.createElement("button");
      originTile.type = "button";
      originTile.className = "version-origin-tile";
      originTile.setAttribute("role", "tab");
      originTile.title = tr("原圖");
      originTile.setAttribute("aria-label", tr("原圖"));
      originTile.addEventListener("click", () => {
        if (!pickOriginAsBase()) pickVersionStripOrigin();
      });
      const originImg = document.createElement("img");
      // originalImgThumbUrl mirrors whatever #original-img is showing (a local blob
      // for a fresh upload, sourceThumbnailUrl when opened from history) and
      // survives version switching, since every version of a case shares one
      // original. The fallback covers a render that beats it being set.
      originImg.src = originThumbUrl || originalImgThumbUrl || sourceThumbnailUrl(runs[0].jobId);
      originImg.alt = "";
      originImg.loading = "lazy";
      originTile.append(originImg);
      originTile.addEventListener("mouseenter", () => showVersionTilePreview(originTile, originImg.src));
      originTile.addEventListener("mouseleave", hideVersionTilePreview);
      const originLabel = document.createElement("span");
      originLabel.className = "version-group-label";
      originLabel.textContent = tr("原圖");
      origin.append(originTile, originLabel);
      const divider = document.createElement("div");
      divider.className = "version-strip-divider";
      divider.setAttribute("aria-hidden", "true");
      // Origin and divider travel as one node so the fence can never drift away
      // from what it fences: this wrapper is what stays fixed at the left
      // edge while the version tiles scroll past it on a narrow screen -- it
      // lives as a plain sibling of .version-strip-versions below, outside
      // that lane's own scroll area, so it never needs to move at all.
      const originFence = document.createElement("div");
      originFence.className = "version-strip-origin";
      originFence.append(origin, divider);
      els.versionStripTrack.append(originFence);
      // Its own scroll container, separate from originFence above -- so the
      // native scrollbar this element grows only ever spans the versions,
      // never the origin tile sitting to its left.
      const versionsScroll = document.createElement("div");
      versionsScroll.className = "version-strip-versions";
      els.versionStripTrack.append(versionsScroll);
      let group = null;
      let groupTiles = null;
      let groupVersionId = null;
      runs.forEach((run, idx) => {
        if (!group || run.versionId !== groupVersionId) {
          groupVersionId = run.versionId;
          group = document.createElement("div");
          group.className = "version-group";
          group.dataset.versionId = run.versionId || "";
          groupTiles = document.createElement("div");
          groupTiles.className = "version-group-tiles";
          // One label per version, as the chat page has it: the N images of
          // one edit share it.
          const label = document.createElement("span");
          label.className = "version-group-label";
          label.textContent = tr("第 {n} 版", { n: run.versionNumber });
          group.append(groupTiles, label);
          versionsScroll.append(group);
        }
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "version-tile";
        tile.dataset.index = String(idx);
        tile.setAttribute("role", "tab");
        const img = document.createElement("img");
        // The job's own thumbnail endpoint, indexed by sourceRun (which RUN
        // of ITS job this image is) -- see fetchVersionRuns on why that is
        // not run.run. Needs no /result payload, so an unloaded version's
        // tile is just as real as the loaded one's.
        img.src = resultThumbnailUrl(run.jobId, { run: run.sourceRun });
        img.alt = "";
        img.loading = "lazy";
        tile.append(img);
        if (run.runsInVersion > 1) {
          const chip = document.createElement("span");
          chip.className = "version-tile-run";
          chip.textContent = String(run.runInVersion);
          tile.append(chip);
        }
        tile.title = runSwitcherLabel(runs, idx);
        tile.setAttribute("aria-label", tile.title);
        tile.addEventListener("click", () => {
          if (pickRunAsBase(idx)) return;
          // The run already on screen: selectRunAt would no-op, so leaving
          // the 原圖 pick and flipping back to After happens here instead.
          if (versionStripOriginPicked && idx === currentRunIndex) {
            clearVersionStripOriginPick();
            setCompareView(true);
            return;
          }
          selectRunAt(idx);
        });
        tile.addEventListener("mouseenter", () => showVersionTilePreview(tile, img.src));
        tile.addEventListener("mouseleave", hideVersionTilePreview);
        groupTiles.append(tile);
      });
      if (pending) versionsScroll.append(buildPendingAnalysisTile(originImg.src));
      versionsScroll.append(buildAdjustingTile());
    }
    versionStripCurrentIdx = currentIdx;
    syncVersionStripCurrent({ reveal: true });
  }

  // Marks whichever tile is in play -- the current run's, or the 原圖 tile
  // while it is picked -- and the count beside the title to match.
  function syncVersionStripCurrent({ reveal = false } = {}) {
    const runs = versionStripRuns;
    const currentIdx = versionStripCurrentIdx;
    if (!runs || !els.versionStripTrack) return;
    const current = runs[currentIdx];
    if (els.versionStripNoteText) {
      // With versions to continue from, where each came from is the top
      // card's to say, and the next base #ap-continue-base's.
      els.versionStripNoteText.parentElement.hidden = renderFromVersionEnabled;
      els.versionStripNoteText.textContent = runs.some((r) => r.versionParams?.source_result || isContinuationRun(r))
        ? tr("有幾版是接著別版調整的，其他皆由原圖重新產生")
        : tr("每張美化結果皆由原圖重新產生");
    }
    syncApContinueBase();
    if (els.versionStripCount) {
      els.versionStripCount.textContent = versionStripOriginPicked
        ? tr("原圖")
        : current && current.versionTotal
          ? tr("第 {versionNumber}／{versionTotal} 組", { versionNumber: current.versionNumber, versionTotal: current.versionTotal })
          : "";
    }
    const originTile = els.versionStripTrack.querySelector(".version-origin-tile");
    if (originTile) {
      originTile.classList.toggle("is-current", versionStripOriginPicked);
      originTile.setAttribute("aria-selected", versionStripOriginPicked ? "true" : "false");
    }
    els.versionStripTrack.querySelectorAll(".version-tile").forEach((tile) => {
      const idx = Number(tile.dataset.index);
      const run = runs[idx];
      const isCurrent = !versionStripOriginPicked && idx === currentIdx;
      tile.classList.toggle("is-current", isCurrent);
      tile.setAttribute("aria-selected", isCurrent ? "true" : "false");
      // Errored is the one state that outlives a re-render: loadHistoryRun
      // sets it when a version's result is gone for good.
      tile.disabled = !isSelectableRun(run);
      tile.closest(".version-group").classList.toggle("is-current", isCurrent);
      // The current version can sit well outside the visible part of the
      // track in a long case (opening one lands on the newest, i.e. the far
      // right). Deferred a frame because on a first render the tiles have
      // only just been appended and the track has no scroll extent yet.
      if (isCurrent && reveal) requestAnimationFrame(() => revealVersionTile(tile));
    });
    syncAdjustingTile();
    syncPhotoViewer();
  }

  // Touch has no hover, so .version-tile-preview turns itself off below the
  // 560px breakpoint (see styles.css) and #version-strip-preview-btn (shown
  // only at that same breakpoint, next to .version-strip-note) opens this
  // modal instead -- a grid of the same tiles at roughly 1.5x size, reusing
  // .version-tile/.version-origin-tile wholesale (only their width/height are
  // overridden, in .version-strip-preview-modal-grid) so current/disabled/
  // run-count styling all come for free. Rebuilt from scratch on every open
  // rather than kept in sync like the strip's own tiles -- it's a disposable
  // view of whatever versionStripRuns/versionStripCurrentIdx already hold, not
  // something that needs to track a running job the way the strip does.
  function renderVersionStripPreviewGrid() {
    if (!els.versionStripPreviewModalGrid || !versionStripRuns) return;
    const runs = versionStripRuns;
    els.versionStripPreviewModalGrid.textContent = "";
    // No origin tile here, unlike the strip: the strip keeps the original
    // pinned because it is the reference the row scrolls past, but this modal
    // is opened to pick a version, and the one tile that cannot be picked
    // just takes room from the ones that can.
    let group = null;
    let groupTiles = null;
    let groupVersionId = null;
    runs.forEach((run, idx) => {
      if (!group || run.versionId !== groupVersionId) {
        groupVersionId = run.versionId;
        group = document.createElement("div");
        group.className = "version-group";
        groupTiles = document.createElement("div");
        groupTiles.className = "version-group-tiles";
        const label = document.createElement("span");
        label.className = "version-group-label";
        label.textContent = tr("第 {n} 版", { n: run.versionNumber });
        group.append(groupTiles, label);
        els.versionStripPreviewModalGrid.append(group);
      }
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "version-tile";
      if (idx === versionStripCurrentIdx) tile.classList.add("is-current");
      const img = document.createElement("img");
      img.src = resultThumbnailUrl(run.jobId, { run: run.sourceRun });
      img.alt = "";
      img.loading = "lazy";
      tile.append(img);
      if (run.runsInVersion > 1) {
        const chip = document.createElement("span");
        chip.className = "version-tile-run";
        chip.textContent = String(run.runInVersion);
        tile.append(chip);
      }
      tile.title = runSwitcherLabel(runs, idx);
      tile.setAttribute("aria-label", tile.title);
      tile.disabled = !isSelectableRun(run);
      // Tapping both selects the version AND closes the modal -- there's no
      // reason to make a touch user close this, look at the (now current)
      // strip tile, then tap it again just to actually switch to it.
      tile.addEventListener("click", () => {
        selectRunAt(idx);
        closeVersionStripPreviewModal();
      });
      groupTiles.append(tile);
    });
  }

  function openVersionStripPreviewModal() {
    if (!els.versionStripPreviewModal || !versionStripRuns) return;
    renderVersionStripPreviewGrid();
    els.versionStripPreviewModal.hidden = false;
  }

  function closeVersionStripPreviewModal() {
    if (els.versionStripPreviewModal) els.versionStripPreviewModal.hidden = true;
  }

  // ── 手機（≤600）的照片檢視，跟對話模式的 VersionViewer 同一套 ──
  // 美化紀錄條在手機收起來，照片右上角的膠囊只剩「第 N 版 ⌄｜比較｜下載」；
  // 點照片或「第 N 版」打開 #photo-viewer：大圖、按住看原圖、下載，下排切換美化紀錄。
  // 選哪一張走的是美化紀錄條同一條路（pickVersionStripOrigin／selectRunAt），
  // 關掉檢視以後照片停在剛選的那張（或原圖），跟在檢視裡看到的一樣。
  const photoViewerMedia = window.matchMedia("(max-width: 600px)");
  let photoViewerOpen = false;
  let photoViewerHolding = false;
  let photoViewerBusy = false;

  // 分析完還沒美化（單張的 pendingResultsActive、批次的 analyzed）只有原圖；
  // 那時 #generated-img 可能還留著上一張的圖，不能拿來當結果。
  function photoViewerHasResult() {
    return !!els.generatedImg?.getAttribute("src")
      && !pendingResultsActive
      && !(currentBatchRecord && currentBatchRecord.status === "analyzed");
  }

  function photoViewerCurrentRun() {
    return versionStripRuns && versionStripCurrentIdx != null ? versionStripRuns[versionStripCurrentIdx] : null;
  }

  // 畫面上是不是原圖：從美化紀錄挑了原圖、還沒有結果，或 compare 切到 Before 都算。
  function photoViewerShowingOriginal() {
    return versionStripOriginPicked
      || !photoViewerHasResult()
      || !els.compareCard?.classList.contains("showing-generated");
  }

  // 膠囊上那顆的字：有美化紀錄時是「第 N 版」，畫面上是原圖時是「原圖」，沒有紀錄就是「看大圖」。
  function syncCanvasVersionBtn() {
    if (!els.canvasVersionLabel) return;
    const run = photoViewerCurrentRun();
    els.canvasVersionLabel.textContent = run && photoViewerShowingOriginal()
      ? tr("原圖")
      : run && run.versionTotal ? tr("第 {versionNumber} 版", { versionNumber: run.versionNumber }) : tr("看大圖");
  }

  function syncPhotoViewer() {
    syncCanvasVersionBtn();
    if (photoViewerOpen) renderPhotoViewer();
  }

  function buildPhotoViewerTile(src, label, isCurrent, onPick) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "version-viewer-tile";
    tile.classList.toggle("is-current", isCurrent);
    tile.setAttribute("role", "tab");
    tile.setAttribute("aria-selected", isCurrent ? "true" : "false");
    const img = document.createElement("img");
    img.src = src;
    img.alt = "";
    img.loading = "lazy";
    const span = document.createElement("span");
    span.textContent = label;
    tile.append(img, span);
    tile.addEventListener("click", onPick);
    return tile;
  }

  async function pickPhotoViewerRun(idx) {
    if (photoViewerBusy) return;
    const base = pickRunAsBase(idx);
    if (base) {
      photoViewerBusy = true;
      renderPhotoViewer();
      try {
        await base;
      } finally {
        photoViewerBusy = false;
        syncPhotoViewer();
      }
      return;
    }
    // 畫面上就是這張：selectRunAt 不會動，從原圖切回結果在這裡做（同美化紀錄條）。
    if (idx === currentRunIndex) {
      if (versionStripOriginPicked) clearVersionStripOriginPick();
      if (photoViewerShowingOriginal()) setCompareView(true);
      renderPhotoViewer();
      return;
    }
    photoViewerBusy = true;
    renderPhotoViewer();
    try {
      await selectRunAt(idx);
    } finally {
      photoViewerBusy = false;
      syncPhotoViewer();
    }
  }

  function renderPhotoViewer() {
    if (!els.photoViewer) return;
    const runs = versionStripRuns;
    const run = photoViewerCurrentRun();
    const hasResult = photoViewerHasResult();
    const showOriginal = photoViewerShowingOriginal();
    const originalSrc = els.originalImg?.getAttribute("src") || "";
    const resultSrc = hasResult ? els.generatedImg.getAttribute("src") : "";

    const title = showOriginal ? tr("原圖") : run && run.versionTotal ? tr("第 {versionNumber} 版", { versionNumber: run.versionNumber }) : tr("美化結果");
    els.photoViewerTitle.textContent = photoViewerHolding ? tr("原圖") : title;
    const notes = [];
    if (!showOriginal && run && run.runsInVersion > 1) notes.push(tr("第 {runInVersion} / {runsInVersion} 張", { runInVersion: run.runInVersion, runsInVersion: run.runsInVersion }));
    if (run && run.versionTotal) notes.push(tr("共 {versionTotal} 版", { versionTotal: run.versionTotal }));
    if (notes.length) {
      const small = document.createElement("small");
      small.textContent = notes.join(" · ");
      els.photoViewerTitle.append(small);
    }
    els.photoViewer.setAttribute("aria-label", tr("{title}大圖", { title }));

    els.photoViewerOriginal.src = originalSrc;
    els.photoViewerOriginal.hidden = !originalSrc || (!showOriginal && !photoViewerHolding);
    if (resultSrc) els.photoViewerResult.src = resultSrc;
    else els.photoViewerResult.removeAttribute("src");
    els.photoViewerResult.alt = tr("{title}美化結果", { title });
    els.photoViewerResult.hidden = !resultSrc || showOriginal || photoViewerHolding;

    // 看結果時才有「按住看原圖」和下載；停在原圖時兩顆都收起來（同對話模式）。
    const canHold = !showOriginal && !!originalSrc;
    const canDownload = !showOriginal && !!lastResult;
    els.photoViewerHold.hidden = !canHold;
    els.photoViewerHold.classList.toggle("is-on", photoViewerHolding);
    els.photoViewerHold.setAttribute("aria-pressed", photoViewerHolding ? "true" : "false");
    els.photoViewerDownload.hidden = !canDownload;
    els.photoViewerActions.hidden = !canHold && !canDownload;

    const track = els.photoViewerTrack;
    track.textContent = "";
    track.hidden = !runs;
    track.classList.toggle("is-busy", photoViewerBusy);
    if (!runs) return;
    const originThumb = els.versionStripTrack?.querySelector(".version-origin-tile img")?.getAttribute("src")
      || originalImgThumbUrl || originalSrc;
    track.append(buildPhotoViewerTile(originThumb, tr("原圖"), showOriginal, () => {
      if (!pickOriginAsBase() && !photoViewerShowingOriginal()) pickVersionStripOrigin();
      renderPhotoViewer();
    }));
    runs.forEach((r, idx) => {
      const isCurrent = !showOriginal && idx === versionStripCurrentIdx;
      const tile = buildPhotoViewerTile(
        resultThumbnailUrl(r.jobId, { run: r.sourceRun }),
        r.versionTotal ? tr("第 {n} 版", { n: r.versionNumber }) : tr("第 {n} 張", { n: idx + 1 }),
        isCurrent,
        () => pickPhotoViewerRun(idx),
      );
      tile.disabled = !isSelectableRun(r);
      tile.setAttribute("aria-label", r.versionTotal ? tr("第 {versionNumber} 版，{v2}", { versionNumber: r.versionNumber, v2: runSwitcherLabel(runs, idx) }) : runSwitcherLabel(runs, idx));
      if (r.runsInVersion > 1) {
        const chip = document.createElement("span");
        chip.className = "version-tile-run";
        chip.textContent = String(r.runInVersion);
        tile.append(chip);
      }
      track.append(tile);
    });
    // 目前那格捲到中間：版本一多，最新的那格會在右邊看不到的地方。
    const on = track.querySelector(".version-viewer-tile.is-current");
    if (on) track.scrollLeft = Math.max(0, on.offsetLeft - (track.clientWidth - on.offsetWidth) / 2);
  }

  function handlePhotoViewerKeydown(e) {
    if (e.key === "Escape") closePhotoViewer();
  }

  function openPhotoViewer() {
    if (!els.photoViewer || photoViewerOpen) return;
    if (!els.originalImg?.getAttribute("src") && !photoViewerHasResult()) return;
    closeDownloadMenu();
    photoViewerOpen = true;
    photoViewerHolding = false;
    els.photoViewer.hidden = false;
    document.documentElement.style.overflow = "hidden";
    document.addEventListener("keydown", handlePhotoViewerKeydown);
    renderPhotoViewer();
    els.photoViewerClose.focus({ preventScroll: true });
  }

  function closePhotoViewer() {
    if (!photoViewerOpen) return;
    photoViewerOpen = false;
    photoViewerHolding = false;
    els.photoViewer.hidden = true;
    document.documentElement.style.overflow = "";
    document.removeEventListener("keydown", handlePhotoViewerKeydown);
  }

  function setPhotoViewerHolding(on) {
    if (photoViewerHolding === on) return;
    photoViewerHolding = on;
    renderPhotoViewer();
  }

  // Scrolls the strip only when the tile isn't already fully in view, and
  // then centres it -- clicking a tile must never make the thing under the
  // cursor jump, and a re-render that changes nothing must not move it
  // either. Measured off getBoundingClientRect rather than offsetLeft: the
  // tiles' offsetParent is whatever positioned ancestor the results layout
  // happens to give them, which is not the scrolling track.
  // .version-strip-versions, not els.versionStripTrack, is the element that
  // actually scrolls -- the origin tile sits outside it as a fixed sibling.
  function revealVersionTile(tile) {
    const track = els.versionStripTrack
      && els.versionStripTrack.querySelector(".version-strip-versions");
    if (!track || !tile.isConnected) return;
    const trackRect = track.getBoundingClientRect();
    const tileRect = tile.getBoundingClientRect();
    if (tileRect.left >= trackRect.left && tileRect.right <= trackRect.right) return;
    track.scrollLeft += (tileRect.left + tileRect.width / 2) - (trackRect.left + trackRect.width / 2);
  }

  // Where the arrows currently are, within the one edit they can move in:
  // "第 1 / 2 張" for a version-tagged case, plain "1 / 2" for a live
  // run_count>1 revise or a batch record, which have no versions at all.
  function runSwitcherLabel(runs, idx) {
    const run = runs[idx];
    if (run && run.versionId) return tr("第 {runInVersion} / {runsInVersion} 張", { runInVersion: run.runInVersion, runsInVersion: run.runsInVersion });
    return `${idx + 1} / ${runs.length}`;
  }

  // Prev/next arrows + "N / total" label for one image's N edit results.
  // Hidden unless hasRunSwitcherContent. Shared by the batch detail view
  // and the single-image results view; `onSelectDone(idx)` decides what
  // re-pointing to that run means for the caller (batch re-renders via
  // syncRecordMirror+showBatchDoneMain, single via showSingleResult).
  function updateRunSwitcher(runs, currentIdx, onSelectDone, { originThumbUrl } = {}) {
    if (!hasRunSwitcherContent(runs)) {
      resetRunSwitcher();
      return;
    }
    currentRunList = runs;
    currentRunIndex = currentIdx;
    currentRunOnSelect = onSelectDone;
    // The arrows and their counter are updateRunSwitcherArrows' call below --
    // whether they are useful depends on the current version, not on the
    // length of the whole list.
    updateRunProgressBar(runs);
    updateRunSwitcherArrows();
    renderVersionStrip(runs, currentIdx, originThumbUrl);
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
    const showing = runs[current];
    updateRunSwitcher(runs, current, async (idx) => {
      const run = runs[idx];
      // Same lazy load as renderSingleRunSwitcher: a version-tagged list
      // (refreshBatchVersionsAfterEdit) only fetched its newest version up
      // front, so an older one's image/result has to be fetched on arrival.
      if (run.status === "unloaded" && !(await loadHistoryRun(runs, run))) {
        showNoticeModal(run.error || tr("無法取得這一版的結果。"));
        return;
      }
      // Superseded while loading -- a new revise replaced this record's list.
      if (record.runs !== runs) return;
      record.mirrorIndex = idx;
      syncRecordMirror(record);
      // Same rule as renderSingleRunSwitcher: crossing into a different
      // version points this record's revise settings at THAT version;
      // stepping between two images of one version changes none of them and
      // must not clobber picks the user has made in the panel meanwhile.
      // Live batch records only -- a read-only historical record shown
      // through showBatchDoneMain (viewHistoryRecord) has no revise to feed.
      const versionChanged = !!run.versionId && (!showing || run.versionId !== showing.versionId)
        && batchRecords.includes(record);
      if (versionChanged) applyBatchVersionContext(record, run);
      // forceReopen for the same reason renderSingleRunSwitcher passes it:
      // with the revise panel already open, openInlineAnalysisForCurrentContext
      // would otherwise no-op and leave the previous version's picks on screen
      // beside this version's photo.
      await showBatchDoneMain(record, settings, versionChanged);
    }, { originThumbUrl: record.thumbUrl || record.previewUrl });
  }

  // Batch counterpart of applyHistoryVersionContext: points one batch
  // record's per-image revise state -- the analysis a 美化 continues from,
  // recipe/style picks, 補充指令, Body decision, model/quality, analysis
  // model, aspect ratio, advanced options, single-pass edit -- at the version
  // `run` belongs to, from that version's own recorded params. Writes only to
  // this record (never singleFlowState, never another record), and only the
  // fields startInlineBatchReview/editBatchItem read per image; preset,
  // ai_photo_editing and run_qa stay batch-wide (batchSettings) as they
  // always have been. The panel itself is repainted from these by the
  // caller's forceReopen -> startInlineBatchReview.
  function applyBatchVersionContext(record, run) {
    if (!run || !run.result || !run.result.analysis) return;
    const params = run.versionParams || {};
    // Same fallbacks as applyHistoryVersionContext for an entry recorded
    // before a given param existed, except that the batch's own current pick
    // stands in where that function falls back to a hard-coded default.
    const extraInfo = run.result["extra-info"] || {};
    const { analysis: restoredAnalysis, selections } =
      restoreReviseSelections(run.result.analysis, params.user_prompt, params.overwrite_prompt);
    record.analysis = restoredAnalysis;
    // A finished edit's own job carries the original's srcKeys forward, so
    // /continue accepts it as the base for the next edit (see
    // applyHistoryVersionContext's analyzeJobId). Unused on the AIPoC path,
    // which resubmits record.file itself.
    record.analyzeJobId = run.jobId;
    record.options = selections.options;
    record.recipeChecked = selections.recipeChecked;
    record.userPrompt = selections.userPrompt;
    record.overwritePrompt = selections.overwritePrompt;
    record.bodyReshapeEnabled = selections.bodyReshapeEnabled;
    // Per-person 人像修正 overrides aren't recorded per version -- the
    // restored analysis's own issue states are the baseline, same as a
    // history case in the single flow (lastSingleSelections has none there).
    record.faceIssueOverrides = null;
    record.imageModel = params.image_model || extraInfo.image_model || batchSettings.imageModel;
    record.imageQuality = params.image_quality || extraInfo.image_quality || batchSettings.imageQuality;
    record.analysisModel = params.analysis_model || extraInfo.analysis_model || batchSettings.analysisModel;
    record.aspectRatio = params.aspect_ratio || batchSettings.aspectRatio;
    record.outputScale = findOutputScaleOption(params.output_scale || extraInfo.output_scale || batchSettings.outputScale)?.value;
    record.advancedOptions = recordAdvancedOptions(null, restoredAnalysis);
    record.singlePassEdit = resolvedSinglePassEdit(params.single_pass_edit, extraInfo.single_pass_edit);
  }

  // Single-image counterpart of renderRunSwitcher: reads/writes the
  // module-level singleRuns/singleMirrorIndex instead of a batch record.
  // Where renderRunSwitcher only ever steps between the N images of one edit,
  // this list can also span a history case's whole edit history
  // (fetchHistoryVersionRuns), which adds two things the batch one has no
  // equivalent of: the target's images may not be fetched yet, and landing on
  // a different version has to re-point the whole page at it.
  function renderSingleRunSwitcher() {
    const runs = singleRuns || [];
    if (!hasRunSwitcherContent(runs)) {
      resetRunSwitcher();
      return;
    }

    const doneIdx = runs.findIndex(isSelectableRun);
    const current = (singleMirrorIndex != null && runs[singleMirrorIndex]) ? singleMirrorIndex : doneIdx;
    const showing = runs[current];
    updateRunSwitcher(runs, current, async (idx) => {
      const run = runs[idx];
      // An older version's images were never fetched (fetchHistoryVersionRuns
      // loads only the newest up front) -- go get them now. selectAdjacentRun
      // already has its loading overlay up over this whole callback.
      if (run.status === "unloaded" && !(await loadHistoryRun(runs, run))) {
        // Leaves the current image alone: loadHistoryRun has marked the
        // target errored, so the arrows simply stop offering it.
        showNoticeModal(run.error || tr("無法取得這一版的結果。"));
        return;
      }
      await loadContinuationBase(runs, run);
      // 調整中, the version stepped to is only looked at: the settings
      // button, the revise panel and singleFlowState stay on the version
      // being adjusted.
      const adjusting = !panelFollowsStep && isAdjusting();
      singleMirrorIndex = idx;
      // Crossing into a different version makes the settings button, the
      // revise panel and singleFlowState describe THAT version. Stepping
      // between two images of the SAME version changes none of that, and
      // must not clobber edits the user has made to the pickers meanwhile.
      const versionChanged = !adjusting && !!run.versionId && (!showing || run.versionId !== showing.versionId);
      if (versionChanged) applyHistoryVersionContext(run, null);
      // forceReopen matters only when the revise panel is already open:
      // openInlineAnalysisForCurrentContext returns early in that case, and
      // backToProcessFromResults -- the only thing that repaints the recipe
      // checklist, the style picks and 補充指令 from singleFlowState/
      // lastSingleSelections -- never runs. Without it, editing the style on
      // 版1 to produce 版2 and then stepping back to 版1 showed 版1's photo
      // with 版2's style picks still in the panel beside it.
      return showSingleResult({ forceReopen: versionChanged });
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
  async function showBatchDoneMain(record, settings = batchSettings, forceReopen = false) {
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

    // Preloaded here -- before the canvas/panel below ever reveal anything
    // -- rather than after, like the rest of this function used to run.
    // Awaiting it in between left the canvas showing full-width, with no
    // analysis panel and none of this record's own content, for however
    // long this fetch took, before a second, visibly separate repaint
    // patched the rest in. Resolving every await up front means everything
    // from here down runs as one uninterrupted synchronous burst, so
    // whichever paint the browser actually shows is already the finished
    // layout, not a partial one.
    const hasOriginal = !!record.previewUrl;
    if (!hasOriginal) {
      els.originalImg.removeAttribute("src");
    } else {
      await setImgSrcPreloaded(els.originalImg, record.previewUrl);
    }
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
    setFlowStage("done"); // batch mode's own equivalent of the single-flow revealResultsIfNeeded update
    const reviewable = settings.reviewable !== false;
    // settings.revealDetailExtra: true only for viewHistoryRecord's admin-
    // reports-drawer call -- shows the prompts button for that one record
    // regardless of debug mode, same override resultsDetailExtra itself
    // already gets. Deliberately not tied to `reviewable` -- that gates the
    // separate live-revise-vs-read-only-preview choice below, not this.
    if (els.showPromptBtn) els.showPromptBtn.hidden = !(isDebugUiActive || settings.revealDetailExtra);
    renderRunSwitcher(record, settings);
    els.origDim.textContent = hasOriginal
      ? `${els.originalImg.naturalWidth} x ${els.originalImg.naturalHeight}`
      : "-";
    // Report the models that actually ran, same label logic as single.
    const extraInfo = record.result["extra-info"] || {};
    const usedImageModelLabel = extraInfo.image_model
      ? (window.PhotoAgentConfig.imageModels[extraInfo.image_model] || extraInfo.image_model)
      : tr("本機處理");
    const usedAnalysisModelLabel = extraInfo.analysis_model
      ? (window.PhotoAgentConfig.analysisModels[extraInfo.analysis_model] || extraInfo.analysis_model)
      : window.PhotoAgentConfig.analysisModel;
    const usedModelLabel = isDebugUiActive
    ? `${usedAnalysisModelLabel} + ${usedImageModelLabel}`
    : tr("AI 智能美化");
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
    // Scoped to the version on screen, same as showSingleResult's
    // versionRuns: once refreshBatchVersionsAfterEdit has grown record.runs
    // into the photo's whole history, "下載全部" still means the N images of
    // the one edit being looked at. An untagged (live) list is unaffected.
    // sourceRun is what the per-image download/report fetches index by for a
    // history entry (see fetchVersionRuns) -- undefined, as before, for a
    // live run.
    const shownRun = batchRecordShownRun(record);
    const versionRuns = shownRun && shownRun.versionId ? batchRecordShownVersionRuns(record) : record.runs;
    lastResult = { file: record.file, sourceUrl: record.previewUrl, resultText: record.result, jobId: record.jobId, runNumber: record.runNumber, sourceRun: shownRun && shownRun.versionId ? shownRun.sourceRun : undefined, path: settings.path, runs: versionRuns };
    // currentTokenUsage() reads currentBatchRecord/lastResult -- must run
    // after lastResult is (re)assigned above, since a historical AIPoC
    // record reached via viewHistoryRecord never sets currentBatchRecord,
    // leaving lastResult as the only correct source for this record's cost.
    updateCostBtn();
    updateDownloadLinks(versionRuns);
    renderResult({
      data,
      analysis: record.result.analysis,
      preset: settings.preset,
      imageModel: data.model,
      hasOriginal
    });
    populateTimingSection(record.timings);
    // Analysis-preview shows by default alongside the canvas -- no-ops if
    // it's already open (e.g. just switching RUN tabs on the same record),
    // UNLESS forceReopen (a genuine switch to a different record,
    // selectBatchRecord/maybeAutoAdvanceToBatchResult) says otherwise --
    // "already showing-analysis" there means the PREVIOUS record's panel,
    // not this one's, and needs a real reopen to load this record's own
    // recipe/style/prompt state instead of silently no-op-ing and leaving
    // the old record's picks on screen. Read-only historical playback
    // shows the very same panel (recipe/style picks, diagnosis) but with
    // every control disabled and no Run/Cancel actions -- re-running a
    // past job through this flow doesn't apply, but the information
    // itself is exactly what's asked for. Re-rendered on every RUN switch
    // too, since each RUN of a multi-RUN job carries its own analysis.
    if (reviewable) {
      openInlineAnalysisForCurrentContext(forceReopen);
    } else {
      renderAnalysisPreview({ analysis: record.result.analysis, preset: settings.preset, expanded: true });
      openInlineAnalysisPanel(true);
    }
  }

  // Is a batch record (either already-finished, or analyzed-but-not-yet-
  // edited) currently open in the main area? Returns null for the
  // single-image flow (or a batch record still analyzing/processing/
  // errored), so callers can branch between the whole-record batch path and
  // the single-image path with one check. "analyzed" is included alongside
  // "done" so this record's own revise/first-edit panel (openInlineAnalysis-
  // ForCurrentContext -> startInlineBatchReview) and the per-record setting
  // routing below (applyModelQualityChange et al.) both work identically
  // whether this is a first edit or a re-edit.
  function currentDetailBatchRecord() {
    return (currentBatchRecord && (currentBatchRecord.status === "done" || currentBatchRecord.status === "analyzed"))
      ? currentBatchRecord
      : null;
  }

  // "檢視分析結果" for one already-finished batch photo: reuses the same
  // inline slide-in panel as the single-image flow (backToProcessFromResults),
  // but re-runs just this one record via editBatchItem instead of
  // finishSingleFlow -- editBatchItem never touches showSection, so this
  // stays fully inline with no processing-section detour at all.
  async function startInlineBatchReview(record) {
    hideError();
    clearInlineAnalysisWait();
    inlinePanelBatchRecord = record;

    // Sync (display-only, no state push -- same as viewHistoryRecord below)
    // #ap-image-model-quality/#ap-image-aspect-ratio/人像修正設定 to THIS case's
    // own effective pick BEFORE rendering, not whatever another case's
    // revise panel last left them showing.
    const effMq = effectiveModelQuality(record);
    applySinglePassEditOption(resolvedSinglePassEdit(record.singlePassEdit, batchSettings?.singlePassEdit));
    applyModelQualityOption(findModelQualityOptionByPair(effMq.model, effMq.quality));
    applyAnalysisModelOption(findAnalysisModelOption(effectiveAnalysisModel(record)));
    applyAspectRatioOption(findAspectRatioOption(effectiveAspectRatio(record)));
    applyOutputScaleOption(findOutputScaleOption(effectiveOutputScale(record)));
    applyAdvancedOptionsOption(effectiveAdvancedOptions(record), "ap");

    // Unlike the single-image flow (separate showPendingResults vs
    // backToProcessFromResults functions), batch mode reuses this one
    // function for both the first-time "analyzed" review and a "done"
    // record's revise. Both are results-page analysis views, so keep the
    // panel expanded by default in either state.
    renderAnalysisPreview({
      analysis: record.analysis,
      preset: batchSettings.preset,
      expanded: true
    });
    // renderAnalysisPreview above (via renderFaceThumbnails, results.js)
    // just reset reviewAdvancedOptions to this analysis's own frozen
    // _advanced_options -- correct for a record analyzed and never revised,
    // but stale if a later revise on THIS record already changed a toggle
    // since. Re-sync to the live value here, before applyBatchReviewSelections
    // restores this record's own per-person picks, so 人像修正設定's globally-
    // locked check (results.js's faceIssueGloballyEnabled) reflects reality
    // rather than analyze-time history.
    syncFaceReviewAdvancedOptions(effectiveAdvancedOptions(record));
    applyBatchReviewSelections(record);
    // applyBatchReviewSelections restores the per-image Body decision after
    // renderAnalysisPreview rebuilt the shared review panel.
    if (els.promptInput) els.promptInput.value = record.userPrompt || "";
    if (els.promptOverwriteToggle) els.promptOverwriteToggle.checked = !!record.overwritePrompt;
    // Pre-fill with this image's own current RUN count (matching whatever was
    // configured when it last ran) instead of hiding the control -- the
    // bulk #run-count in the sidebar only ever applies to a *first* continue,
    // so a revise needs its own visible/adjustable count, same as single-image.
    if (els.apRunCountWrap) els.apRunCountWrap.hidden = !isDebugUiActive;
    // The shown edit's own count (batchRecordShownVersionRuns), not the length
    // of the whole 美化紀錄 list refreshBatchVersionsAfterEdit may have grown
    // this into -- same "what it was configured with when it ran" rule as the
    // rest of this panel, which applyBatchVersionContext points at whichever
    // version is on screen.
    if (els.apRunCountSelect) els.apRunCountSelect.value = String(batchRecordShownVersionRuns(record).length || 1);
    openInlineAnalysisPanel();
    // After the panel open: it re-enables 美化, which a reframe in flight holds.
    syncReframeBusy();
    // The "run with no changes just reproduces the same result" guard
    // (inlineReviseHasNoChanges) only makes sense when there's an existing
    // result to reproduce -- for "analyzed" (not yet edited) records this is
    // the first edit, so leave no snapshot behind (inlineReviseHasNoChanges
    // treats a null snapshot as "there are changes", i.e. never blocks) and
    // Run always goes through, same as the single-image pending flow.
    if (record.status === "done") {
      captureInlineReviseSnapshot();
    } else {
      inlineReviseSnapshot = null;
    }
    updateInlineAnalysisContinueBtnState();

    const actionPromise = waitForInlineAnalysisAction(
      () => effectiveModelQuality(record),
      // Only for an OLDER version stepped onto via the 美化紀錄 strip -- the
      // case this notice describes. A record's own current analysis keeps its
      // old behaviour: an imported folder goes through /rerun, which has no
      // compatibility gate, and /continue answers an outdated one itself.
      () => (batchRecordOnOlderVersion(record) && record.analysis && !isCompatible(record.analysis)
        ? BATCH_ARCHIVED_VERSION_NOTICE : null)
    );
    // After the wait is armed: 立即美化 presses the 美化 button it listens on.
    offerPendingReframeEdit(record);
    const action = await actionPromise;
    closeInlineAnalysisPanel();
    if (action === "cancel") {
      // Keep local review edits when the user closes the panel without
      // submitting; reopening this batch photo should not reset review state.
      captureBatchReviewSelections(record);
      return;
    }

    captureBatchReviewSelections(record);
    if (!record.uploadedAt) record.uploadedAt = Date.now() / 1000;
    record.modifiedAt = Date.now() / 1000;
    // No second argument, matching renderBatchMain's own setCurrentCaseTimes(record).
    // This read `settings.revealDetailExtra`, copied from showBatchDoneMain -- but
    // there `settings` is a PARAMETER (`showBatchDoneMain(record, settings = batchSettings)`)
    // and it does not exist in this function's scope, so the line threw
    // "ReferenceError: settings is not defined" on every batch revise. Because
    // startInlineBatchReview is dispatched unawaited (see openInlineAnalysis-
    // ForCurrentContext), that rejection was swallowed: the panel had already
    // closed one line earlier, the record never reached "processing", and the
    // edit never started -- the UI just sat there doing nothing. revealDetailExtra
    // is the admin history/report view flag (viewHistoryRecord); batchSettings
    // never carries it, so it is always false for a live revise anyway.
    setCurrentCaseTimes(record);
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
    record.progressTipsOnly = false;
    record.status = "processing";
    updateBatchSidebarItem(record);
    // Unlike continueBatchRecords, this path runs a single record outside any
    // "Run all" batch -- so it's the only place that can take the LAST
    // "analyzed" record off the board. Refresh here too, or "Run all" stays
    // visible (stale) after every case has already been run one by one.
    updateBatchHeaderControls();
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
    if (failed.length) showError(failed[0].error || tr("重新處理失敗，請稍後再試。"));

    // Last, same position as finishSingleFlow's refreshVersionsAfterEdit:
    // brings this photo's earlier edits back as the 美化紀錄 strip.
    await refreshBatchVersionsAfterEdit(record);
  }

  // Batch counterpart of refreshVersionsAfterEdit. startInlineBatchReview
  // leaves record.runs holding only the live runs it just built -- untagged,
  // so the strip (renderVersionStrip only shows for a version-tagged list)
  // never appeared for a batch photo however many times it was re-edited.
  // Rebuilds the list from the photo's case history, exactly like the single
  // flow, and bails without touching anything unless the case really has
  // more than one version.
  //
  // Only the revise path calls this, not "Run all" (continueBatchRecords):
  // a photo's first edit in a batch has one version by definition, so the
  // lookup would come back with nothing to show for every photo in it.
  async function refreshBatchVersionsAfterEdit(record) {
    const live = Array.isArray(record.runs) && record.runs.length ? record.runs : null;
    if (!live || live.some((r) => r.versionId)) return;
    const user = getCurrentUser();
    const mirrored = record.mirrorIndex != null ? live[record.mirrorIndex] : null;
    const anchor = mirrored && mirrored.status === "done" ? mirrored : live.find((r) => r.status === "done");
    if (!user || !anchor || !anchor.jobId) return;

    const found = await findCaseForJob(user, anchor.jobId);
    if (found) ensureVersionNote(found.caseKey, found.latest.version_id);
    if (!found || found.shown.length < 2) return;
    const { shown, latest, caseKey } = found;
    const path = batchSettings.path || "local";
    const latestRuns = await fetchVersionRuns(latest.runs || [], latest.path || path);
    const rebuilt = buildVersionRunList(shown, latestRuns, path, caseKey);
    // Superseded while fetching -- another revise of this same photo
    // replaced the list this was computed against.
    if (!rebuilt || record.runs !== live) return;

    // A live batch run and its rebuilt entry are the same image when they
    // share a job id -- editBatchItem submits one job per run, so the live
    // run's own `run` (its position, 1..N) is not that job's RUN number and
    // can't be compared against entry.sourceRun. Only when several live runs
    // share one job (a combined multi-RUN job) does the RUN number decide.
    const sameImage = (liveRun, entry) => {
      if (!liveRun || liveRun.jobId !== entry.jobId) return false;
      const siblings = live.filter((r) => r.jobId === liveRun.jobId).length;
      const liveSourceRun = liveRun.sourceRun != null ? liveRun.sourceRun : liveRun.run;
      return siblings === 1 || liveSourceRun === entry.sourceRun;
    };
    // Carry the just-finished runs' step timings across -- they exist only
    // in memory, and the re-fetched entries came back without them.
    rebuilt.forEach((entry) => {
      const match = live.find((r) => sameImage(r, entry));
      if (match && match.timings && match.timings.length) entry.timings = match.timings;
    });
    record.runs = rebuilt;
    const anchorIdx = rebuilt.findIndex((entry) => sameImage(anchor, entry));
    const latestIdx = rebuilt.findIndex((r) => r.versionId === latest.version_id);
    record.mirrorIndex = anchorIdx >= 0 ? anchorIdx : (latestIdx >= 0 ? latestIdx : null);
    syncRecordMirror(record);
    updateBatchSidebarItem(record);
    refreshIfSelected(record);
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
    const record = currentDetailBatchRecord();
    // "Already showing" only counts as "nothing to do" when the open panel is
    // THIS record's. Switching to a batch photo that is still processing
    // (showBatchMainLoading) hides the canvas row without removing
    // .showing-analysis from it, so the previously viewed photo's panel stays
    // "open" underneath -- when this photo's edit then finished,
    // refreshIfSelected's re-render (no forceReopen) hit this guard and left
    // the other photo's recipe/style/prompt on screen beside this photo's
    // result.
    const panelShowing = els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis");
    if (!forceReopen && panelShowing && (!record || inlinePanelBatchRecord === record)) return;
    if (record) {
      // Dispatched without await on purpose -- this function is called from
      // render paths that must not block on a panel the user may never submit.
      // But that also means an exception anywhere inside it (after the panel has
      // closed and before the edit is queued) becomes an unhandled rejection and
      // the UI is left sitting on a closed panel with nothing running, which is
      // exactly how the `settings is not defined` bug at setCurrentCaseTimes
      // presented: a dead Run button and no error anywhere the user could see.
      // Surface it and put the controls back instead of failing silently.
      startInlineBatchReview(record).catch((err) => {
        console.error("startInlineBatchReview failed", err);
        setAnalysisPreviewDisabled(false);
        if (record.status === "processing") record.status = "done";
        updateBatchSidebarItem(record);
        refreshIfSelected(record);
        showError(tr("重新處理時發生錯誤，請再試一次。"));
      });
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
    if (timingList) {
      const rows = Array.from(timingList.querySelectorAll(".timing-row"));
      if (rows.length) {
        const lineFor = (row) => {
          const [nameEl, valEl] = row.querySelectorAll("span");
          return `${(nameEl?.textContent || "").padEnd(24)}${valEl?.textContent || ""}`;
        };
        const totalRow = rows.find((r) => r.classList.contains("timing-total"));
        const lines = rows.filter((r) => r !== totalRow).map(lineFor);
        if (totalRow) lines.push("", lineFor(totalRow));
        return lines.join("\n");
      }
    }
    // #timing-list only gets populated by populateTimingSection once a job
    // fully finishes -- but on the review screen (before that), analysis.timings
    // already carries Step 2's own duration (see currentTokenUsage's comment
    // above for why), so fall back to reading that directly instead of staying
    // empty until the whole pipeline is done.
    const batchRecord = currentDetailBatchRecord();
    const timings = batchRecord ? batchRecord.analysis?.timings : singleFlowState?.analysis?.timings;
    if (!timings) return "";
    const entries = Object.entries(timings).filter(([, seconds]) => seconds != null);
    if (!entries.length) return "";
    const lines = entries.map(([stage, seconds]) => `${stage.padEnd(24)}${Number(seconds).toFixed(2)}s`);
    const total = entries.reduce((sum, [, seconds]) => sum + Number(seconds), 0);
    lines.push("", tr("{v1}{v2}s", { v1: "總計（尚未完成，僅分析階段）".padEnd(24), v2: total.toFixed(2) }));
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
    { label: tr("照片分析"), el: () => els.promptAnalysis },
    { label: tr("使用者補充指令"), el: () => els.promptReview },
    { label: tr("修圖（送給 Editor）"), el: () => els.promptEdit },
    { label: tr("Feature Resolver 結果"), el: () => els.promptEditResolution, omitWhenEmpty: true },
    { label: tr("眼部修正（第二段）"), el: () => els.promptGazeRepair },
    // Unlike every stage above, this one has no #prompt-* element to read --
    // it's a same-origin image lazy-fetched on click (see
    // loadPromptsModalImageTab below) from whichever job/run `lastResult`
    // currently points at, same target reportJob's target uses. Only exists
    // when Step 5b actually ran (pipeline.py's finish_stage), so a missing
    // image is a normal 404, handled by loadPromptsModalImageTab itself
    // rather than the empty-tab styling the text stages use.
    { label: tr("第一階段中間圖（眼部修正前）"), image: true },
    { label: tr("品質檢查"), el: () => els.promptQa },
    { label: tr("處理時間"), text: timingListAsText, emptyText: tr("（尚無處理時間記錄）") },
    { label: tr("API 用量與費用"), text: tokenUsageAsText, emptyText: tr("（尚無用量記錄）") },
    { label: tr("分析原始 JSON"), text: analyzeRawJsonText, emptyText: tr("（尚無分析結果）") }
  ];

  // Revoked whenever a new image loads or the modal closes, so switching
  // jobs or reopening the modal never leaks the previous blob URL.
  let promptsModalImageObjectUrl = null;

  function showPromptsModalText(text) {
    if (els.promptsModalImageWrap) els.promptsModalImageWrap.hidden = true;
    if (els.promptsModalContent) {
      els.promptsModalContent.hidden = false;
      els.promptsModalContent.textContent = text;
    }
  }

  // Resets the compare toggle back to "stage 1" (not "original") and drops
  // the original layer's src every time a fresh stage-1 image loads --
  // otherwise switching jobs while the tab is showing "original" would
  // carry the PREVIOUS job's original photo over, or leave a stale
  // showing-original class active for the new one.
  function showPromptsModalImage(objectUrl) {
    if (promptsModalImageObjectUrl) URL.revokeObjectURL(promptsModalImageObjectUrl);
    promptsModalImageObjectUrl = objectUrl;
    if (els.promptsModalContent) els.promptsModalContent.hidden = true;
    if (els.promptsModalImageWrap) {
      els.promptsModalImageWrap.hidden = false;
      els.promptsModalImageWrap.classList.remove("showing-original");
    }
    if (els.promptsModalImage) els.promptsModalImage.src = objectUrl;
    if (els.promptsModalImageOriginal) els.promptsModalImageOriginal.removeAttribute("src");
    if (els.promptsModalImageCompareBtn) els.promptsModalImageCompareBtn.textContent = tr("與原圖比較");
  }

  // #prompts-modal-image-compare-btn: same stacked-<img>-pair toggle as
  // #compare-toggle-btn on the results page, just scoped to this modal.
  // sourceImageUrl is local-path only (like preEditImageUrl above), but
  // this button only ever exists while a stage-1 image is already showing,
  // which itself already ruled out the aipoc case -- no separate check
  // needed here.
  function togglePromptsModalImageCompare() {
    if (!els.promptsModalImageWrap || !lastResult?.jobId) return;
    const showingOriginal = els.promptsModalImageWrap.classList.toggle("showing-original");
    if (showingOriginal && els.promptsModalImageOriginal && !els.promptsModalImageOriginal.getAttribute("src")) {
      els.promptsModalImageOriginal.src = sourceImageUrl(lastResult.jobId);
    }
    if (els.promptsModalImageCompareBtn) {
      els.promptsModalImageCompareBtn.textContent = showingOriginal ? tr("顯示第一階段圖") : tr("與原圖比較");
    }
  }

  // preEditImageUrl is local-path only (AIPoC's separate deployment of this
  // pipeline doesn't expose this key) -- an aipoc lastResult short-circuits
  // to a fixed message instead of firing a fetch() that can only 404.
  async function loadPromptsModalImageTab() {
    showPromptsModalText(tr("載入中…"));
    if (!lastResult?.jobId) {
      showPromptsModalText(tr("（尚無結果可查看）"));
      return;
    }
    if (lastResult.path === "aipoc") {
      showPromptsModalText(tr("（AIPoC 尚不支援查看第一階段中間圖）"));
      return;
    }
    try {
      const response = await fetch(preEditImageUrl(lastResult.jobId, { path: lastResult.path, run: lastResult.sourceRun != null ? lastResult.sourceRun : lastResult.runNumber }));
      if (!response.ok) {
        showPromptsModalText(tr("（此結果沒有第一階段中間圖，可能是單階段模式或未啟用 AI 修圖）"));
        return;
      }
      showPromptsModalImage(URL.createObjectURL(await response.blob()));
    } catch (err) {
      showPromptsModalText(tr("（載入第一階段中間圖時發生錯誤）"));
    }
  }

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

  // USD price per 1M tokens, keyed by the exact model name pipeline.py puts
  // into each token_usage[stage].model, split into text/image rates since
  // gpt-image-1.5/gpt-image-2's edit calls bill image tokens far higher than
  // text tokens (see normalizeStageUsage below). Standard (non-batch,
  // non-cached), short-context list rates as published on each provider's own
  // pricing page (checked 2026-08-31): gpt-4.1-mini/gpt-5.6-luna/gpt-5.6-sol/
  // gpt-image-1.5/gpt-image-2 at developers.openai.com/api/docs/pricing;
  // nano-banana-2/nano-banana-lite (gemini-3.1-flash-image /
  // gemini-3.1-flash-lite-image) at ai.google.dev/gemini-api/docs/pricing --
  // Gemini publishes one blended input rate across text/image, hence the
  // same number in both fields there. Chat-only models (gpt-4.1-mini/
  // gpt-5.6-*) never emit image tokens, so their own image rate is unused;
  // it's set equal to the text rate rather than 0 only so a future stage that
  // did send an image input wouldn't silently price it at zero. Re-check both
  // pages if these ever look off -- list prices change.
  const MODEL_PRICING = {
    "gpt-4.1-mini": { input: { text: 0.40, image: 0.40 }, output: { text: 1.60, image: 1.60 } },
    "gpt-5.6-luna": { input: { text: 0.20, image: 0.20 }, output: { text: 1.20, image: 1.20 } },
    "gpt-5.6-sol": { input: { text: 4.00, image: 4.00 }, output: { text: 20.00, image: 20.00 } },
    "gpt-image-2": { input: { text: 5.00, image: 8.00 }, output: { text: 0, image: 30.00 } },
    "gpt-image-1.5": { input: { text: 5.00, image: 8.00 }, output: { text: 10.00, image: 32.00 } },
    // developers.openai.com's gpt-image-2.5-sunburst/-flare model cards both
    // state "Token rates match GPT Image 2" -- no separate published rate.
    "gpt-image-2.5-sunburst": { input: { text: 5.00, image: 8.00 }, output: { text: 0, image: 30.00 } },
    "gpt-image-2.5-flare": { input: { text: 5.00, image: 8.00 }, output: { text: 0, image: 30.00 } },
    // GPT Image 2.6 Standard token rates, checked 2026-11-08.
    "gpt-image-2.6-sunburst": { input: { text: 5.00, image: 8.00 }, output: { text: 0, image: 30.00 } },
    "gpt-image-2.6-flare": { input: { text: 5.00, image: 8.00 }, output: { text: 0, image: 30.00 } },
    "nano-banana-2": { input: { text: 0.50, image: 0.50 }, output: { text: 60.00, image: 60.00 } },
    "nano-banana-lite": { input: { text: 0.25, image: 0.25 }, output: { text: 30.00, image: 30.00 } }
  };

  // token_usage[stage].usage is the provider's own raw usage object, and its
  // shape depends on which API answered: chat/Responses calls (Steps 2/3/6)
  // use prompt_tokens/completion_tokens with no modality split; OpenAI's
  // Images API (Step 5/5b on gpt-image-*) uses input_tokens/output_tokens
  // plus input_tokens_details/output_tokens_details {text_tokens,
  // image_tokens}; Gemini (Step 5/5b on nano-banana-*) uses promptTokenCount/
  // candidatesTokenCount plus promptTokensDetails/candidatesTokensDetails,
  // arrays of {modality, tokenCount}; MAI uses num_input_text_tokens,
  // num_input_image_tokens, and num_output_tokens. This normalizes all three
  // provider shapes to the same
  // {input: {text, image}, output: {text, image}, total} before display --
  // whichever shape is missing a breakdown gets counted as all-text.
  function normalizeStageUsage(raw) {
    if (!raw) return null;
    const sumModality = (details, modality) =>
      (details || []).reduce((sum, d) => (d.modality === modality ? sum + (d.tokenCount || 0) : sum), 0);

    let input = { text: 0, image: 0 };
    let output = { text: 0, image: 0 };
    if (raw.input_tokens !== undefined || raw.output_tokens !== undefined) {
      // OpenAI Images API
      const inImage = raw.input_tokens_details?.image_tokens ?? 0;
      const inText = raw.input_tokens_details?.text_tokens ?? Math.max((raw.input_tokens ?? 0) - inImage, 0);
      const outImage = raw.output_tokens_details?.image_tokens ?? 0;
      const outText = raw.output_tokens_details?.text_tokens ?? Math.max((raw.output_tokens ?? 0) - outImage, 0);
      input = { text: inText, image: inImage };
      output = { text: outText, image: outImage };
    } else if (raw.promptTokenCount !== undefined || raw.candidatesTokenCount !== undefined) {
      // Gemini generateContent
      const inImage = sumModality(raw.promptTokensDetails, "IMAGE");
      const outImage = sumModality(raw.candidatesTokensDetails, "IMAGE");
      input = { text: Math.max((raw.promptTokenCount ?? 0) - inImage, 0), image: inImage };
      output = { text: Math.max((raw.candidatesTokenCount ?? 0) - outImage, 0), image: outImage };
    } else if (
      raw.num_input_text_tokens !== undefined ||
      raw.num_input_image_tokens !== undefined ||
      raw.num_output_tokens !== undefined
    ) {
      // Microsoft Foundry MAI image edits API
      input = {
        text: raw.num_input_text_tokens ?? 0,
        image: raw.num_input_image_tokens ?? 0
      };
      output = { text: 0, image: raw.num_output_tokens ?? 0 };
    } else {
      // Chat Completions / Responses API -- text only
      input = { text: raw.prompt_tokens ?? 0, image: 0 };
      output = { text: raw.completion_tokens ?? 0, image: 0 };
    }
    const total = raw.total_tokens ?? raw.totalTokenCount ?? (input.text + input.image + output.text + output.image);
    // Only OpenAI's Responses API (Steps 2/3/6) ever reports this -- Images
    // API edit calls and Gemini calls have no reasoning phase, so this stays
    // null for them rather than a misleading 0 (used below to omit the
    // reasoning/non-reasoning label entirely for those stages).
    const reasoningTokens = raw.output_tokens_details?.reasoning_tokens ?? null;
    return { input, output, total, reasoningTokens };
  }

  // token_usage/extra-info/timings only reach their final (post-finish_stage)
  // shape once Step 5/5b/6 have all run -- but pipeline.py's analyze_stage
  // already stashes Step 2's own usage/timings on analysis.usage/.timings
  // (popped off only by finish_stage), so the review screen (batch status
  // "analyzed", or singleFlowState before finishSingleFlow ever populates
  // lastResult) already has a partial-but-real Step 2 entry to show. Each
  // getter here tries the finished shape first, falling back to that
  // in-progress one so the "API 用量與費用"/"處理時間" tabs show whatever is
  // available yet rather than staying empty until the whole job is done.
  function currentTokenUsage() {
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) return batchRecord.result?.token_usage || batchRecord.analysis?.usage;
    return lastResult?.resultText?.token_usage || singleFlowState?.analysis?.usage;
  }

  // Same finished-shape-first, in-progress-fallback lookup as
  // currentTokenUsage above, but for the timings dict -- so "API 用量與費用"
  // can show each stage's own duration alongside its usage/cost instead of
  // that living only on the separate "處理時間" tab.
  function currentTimings() {
    const batchRecord = currentDetailBatchRecord();
    if (batchRecord) return batchRecord.result?.["extra-info"]?.timings || batchRecord.analysis?.timings;
    return lastResult?.resultText?.["extra-info"]?.timings || singleFlowState?.analysis?.timings;
  }

  // extra-info only exists once finish_stage has assembled the final result
  // (it carries image_quality, the Step 5 edit call's actual quality --
  // possibly bumped up from the requested value, see pipeline.py's
  // single_pass_edit gaze-fix upgrade) -- nothing to fall back to at the
  // review-screen stage since Step 5 hasn't run yet there.
  function currentExtraInfo() {
    const batchRecord = currentDetailBatchRecord();
    return batchRecord ? batchRecord.result?.["extra-info"] : lastResult?.resultText?.["extra-info"];
  }

  // #show-prompt-btn's "API 用量與費用" tab: one row per pipeline stage that
  // actually called a model, with its model/reasoning state, processing
  // time, text/image token counts and cost. Folds in what used to be the
  // separate "處理時間" tab's numbers too, since they always describe the
  // same stage -- and unlike the raw analysis JSON (whose "timings"/"usage"
  // keys get popped off once finish_stage runs, see pipeline.py), this tab's
  // own lookups (currentTokenUsage/currentTimings) already fall back to
  // wherever those live at any point in the pipeline. Cost is only as
  // accurate as MODEL_PRICING above -- update it if a provider changes its
  // list prices.
  const ZERO_PRICING = { input: { text: 0, image: 0 }, output: { text: 0, image: 0 } };

  // Shared by tokenUsageAsText (per-stage breakdown) and currentCostBreakdown
  // (the 分析/修圖/總金額 split for #cost-modal) so the pricing formula only
  // lives in one place. Returns null for a stage that didn't call a model or
  // whose usage shape normalizeStageUsage couldn't make sense of.
  function stageCost(entry) {
    if (!entry || !entry.model) return null;
    const tokens = normalizeStageUsage(entry.usage);
    if (!tokens) return null;
    const pricing = MODEL_PRICING[entry.model] || ZERO_PRICING;
    const cost =
      (tokens.input.text / 1_000_000) * pricing.input.text +
      (tokens.input.image / 1_000_000) * pricing.input.image +
      (tokens.output.text / 1_000_000) * pricing.output.text +
      (tokens.output.image / 1_000_000) * pricing.output.image;
    return { tokens, cost };
  }

  // pipeline.py's own stage names (verbatim, see its "Step N - ..." usage[]
  // keys) bucketed into 分析 (understanding/planning the photo, before any
  // pixel is touched) vs 修圖 (the actual edit call plus its repair/QA
  // follow-ups) for #cost-modal.
  const ANALYSIS_COST_STAGES = ["Step 2.0 - photo_route", "Step 2 - photo_analysis", "Step 2b - photo_reframe", "Step 3 - user_instruction_review"];
  const EDIT_COST_STAGES = ["Step 5 - image_edit", "Step 5b - gaze_repair_pass", "Step 6 - quality_check"];

  // Raw model ids (MODEL_PRICING/entry.model's own id, e.g. "gpt-5.6-luna")
  // -> the same user-facing label showSingleResult/showBatchDoneMain already
  // show on the "輸出結果" card. analysisModels/imageModels (config.js) never
  // share a key, so trying both is safe regardless of which bucket an id
  // came from.
  function friendlyModelLabel(modelId) {
    const config = window.PhotoAgentConfig || {};
    return config.analysisModels?.[modelId] || config.imageModels?.[modelId] || modelId;
  }

  // Same idea, but for an edit-stage model -- includes its Standard/
  // Enhanced/Premium tier (imageModelQualityOptions, config.js), the same
  // label the upload page's own 修圖模型/影像品質 picker shows, since a bare
  // model name alone doesn't say which quality tier this photo actually
  // paid for. `quality` is extra-info.image_quality (Step 5's actual tier,
  // possibly upgraded by pipeline.py's gaze-fix bump -- see its own comment
  // in tokenUsageAsText above); falls back to the model's only tier when it
  // has just one (Nano Banana 2/MAI-Image-2.6), then to the plain model name
  // if neither matches (e.g. a Step 6 QA model with no quality tier at all).
  function friendlyEditModelLabel(modelId, quality) {
    const options = window.PhotoAgentConfig?.imageModelQualityOptions || [];
    const matches = options.filter((opt) => opt.model === modelId);
    const match = (quality && matches.find((opt) => opt.quality === quality)) || (matches.length === 1 ? matches[0] : null);
    return match ? match.label : friendlyModelLabel(modelId);
  }

  // Fixed pre-flight ESTIMATES for #preflight-cost-modal (shown before
  // 分析/美化/分析+美化 actually submit anything), keyed by the exact
  // analysis model id or edit model+quality `value` a normal run can pick.
  // imageModelQualityOptions' own comment notes only the three Sunburst
  // tiers are release-facing today, so that's all this needs to cover --
  // there's no real token usage yet at this point to price a combo from
  // (unlike stageCost's post-run numbers above), so a combo missing here
  // (a debug-only model/quality) just shows a dash, same as
  // formatCostValue's zero-cost dash.
  const PREFLIGHT_ANALYSIS_COST = { "gpt-5.6-luna": 0.0152 };
  const PREFLIGHT_EDIT_COST = {
    "gpt-image-2.5-sunburst:low": 0.0258,
    "gpt-image-2.5-sunburst:medium": 0.0328,
    "gpt-image-2.5-sunburst:high": 0.0679,
  };

  function preflightAnalysisEstimate(analysisModel) {
    return { label: friendlyModelLabel(analysisModel), cost: PREFLIGHT_ANALYSIS_COST[analysisModel] ?? null };
  }

  function preflightEditEstimate(imageModel, imageQuality) {
    const opt = findModelQualityOptionByPair(imageModel, imageQuality);
    return { label: opt?.label || friendlyModelLabel(imageModel), cost: PREFLIGHT_EDIT_COST[opt?.value] ?? null };
  }

  // Shared #preflight-cost-modal row builders for a fresh 分析/一鍵美化 about
  // to start (upload page's own picks, not an existing singleFlowState) --
  // used by #start-btn/#start-full-btn and by the archived-case notice's two
  // restart actions below, since both re-enter through the exact same
  // startSubmit({skipReview}) path and so spend the exact same thing.
  function analysisPreflightStep() {
    const analysisModel = els.analysisModelSelect?.value || window.PhotoAgentConfig.defaultAnalysisModel || "gpt-5.6-luna";
    const est = preflightAnalysisEstimate(analysisModel);
    return { label: tr("分析花費"), modelLabel: est.label, cost: est.cost };
  }

  function editPreflightStep() {
    const modelQualityOpt = findModelQualityOption(els.imageModelQualitySelect?.value) || {};
    const est = preflightEditEstimate(modelQualityOpt.model, modelQualityOpt.quality);
    return { label: tr("美化花費"), modelLabel: est.label, cost: est.cost };
  }

  function formatPreflightCostValue(cost) {
    return cost != null ? `$${cost.toFixed(4)}` : "—";
  }

  let preflightCostModalHandlers = null;
  function clearPreflightCostModalWait() {
    if (!preflightCostModalHandlers) return;
    els.preflightCostModalOkBtn?.removeEventListener("click", preflightCostModalHandlers.ok);
    els.preflightCostModalCancelBtn?.removeEventListener("click", preflightCostModalHandlers.cancel);
    preflightCostModalHandlers = null;
  }

  function buildPreflightCostRow({ label, modelLabel, cost }, extraClass = "") {
    const item = document.createElement("div");
    item.className = "cost-modal-item";
    const row = document.createElement("div");
    row.className = `cost-modal-row ${extraClass}`.trim();
    const labelEl = document.createElement("span");
    labelEl.className = "cost-modal-label";
    labelEl.textContent = label;
    const valueEl = document.createElement("span");
    valueEl.className = "cost-modal-value";
    valueEl.textContent = formatPreflightCostValue(cost);
    row.append(labelEl, valueEl);
    item.appendChild(row);
    if (modelLabel) {
      const modelEl = document.createElement("p");
      modelEl.className = "cost-modal-model";
      modelEl.textContent = modelLabel;
      item.appendChild(modelEl);
    }
    return item;
  }

  // Shows the estimated cost of the step(s) a 分析/美化/分析+美化 press is
  // about to submit and waits for the user to confirm or back out --
  // resolves true/false the same way showConfirmModal does. `steps` is
  // [{label, modelLabel, cost}], one entry per step about to run; a second
  // entry gets a summed 總金額 row underneath, same as #cost-modal's own
  // total row.
  function showPreflightCostModal(steps) {
    return new Promise((resolve) => {
      if (!isPreflightCostEstimateEnabled() || !els.preflightCostModal || !els.preflightCostModalRows) { resolve(true); return; }
      clearPreflightCostModalWait();
      els.preflightCostModalRows.innerHTML = "";
      steps.forEach((step) => els.preflightCostModalRows.appendChild(buildPreflightCostRow(step)));
      if (steps.length > 1) {
        const knownCosts = steps.filter((s) => s.cost != null);
        const total = knownCosts.length ? knownCosts.reduce((sum, s) => sum + s.cost, 0) : null;
        els.preflightCostModalRows.appendChild(
          buildPreflightCostRow({ label: tr("總金額"), cost: total }, "cost-modal-row-total"));
      }
      const finish = (result) => { clearPreflightCostModalWait(); els.preflightCostModal.hidden = true; resolve(result); };
      preflightCostModalHandlers = { ok: () => finish(true), cancel: () => finish(false) };
      els.preflightCostModalOkBtn?.addEventListener("click", preflightCostModalHandlers.ok);
      els.preflightCostModalCancelBtn?.addEventListener("click", preflightCostModalHandlers.cancel);
      els.preflightCostModal.hidden = false;
    });
  }

  // #cost-modal's three numbers, plus which model(s) actually ran each
  // bucket's stages, for whatever photo is currently on screen. null (button
  // stays hidden) until there's at least one priced stage; currentTokenUsage's
  // own in-progress fallback (see its comment above) means this already has
  // a partial Step 2 amount to show before the edit stage has even run.
  function currentCostBreakdown() {
    const usage = currentTokenUsage();
    if (!usage) return null;
    const imageQuality = currentExtraInfo()?.image_quality;
    let analysisCost = 0;
    let editCost = 0;
    let hasStage = false;
    const analysisModels = new Set();
    const editModels = new Set();
    Object.entries(usage).forEach(([stage, entry]) => {
      const priced = stageCost(entry);
      if (!priced) return;
      hasStage = true;
      if (ANALYSIS_COST_STAGES.includes(stage.replace(/ \(\d+\)$/, ""))) {
        analysisCost += priced.cost;
        if (entry.model) analysisModels.add(friendlyModelLabel(entry.model));
      } else if (EDIT_COST_STAGES.includes(stage)) {
        editCost += priced.cost;
        if (entry.model) editModels.add(friendlyEditModelLabel(entry.model, imageQuality));
      }
    });
    return hasStage
      ? { analysisCost, editCost, totalCost: analysisCost + editCost, analysisModels, editModels }
      : null;
  }

  // Keeps #cost-btn's visibility in sync with whatever result is currently
  // on screen -- called from the same spots that decide #show-prompt-btn's
  // hidden state, since both depend on "is there a result to show numbers
  // for" (see call sites below). The actual numbers are filled in lazily by
  // renderCostModal, only when the modal is opened.
  function updateCostBtn() {
    if (!els.costBtn) return;
    els.costBtn.hidden = !currentCostBreakdown();
  }

  // "$0.0000" reads like the stage genuinely cost nothing; a still-analyzed-
  // only case (or a local/no-AI photo) hasn't priced that stage at all, so a
  // dash is the more honest "n/a", not a real zero.
  function formatCostValue(cost) {
    return cost > 0 ? `$${cost.toFixed(4)}` : "—";
  }

  function renderModelCaption(el, models) {
    if (!el) return;
    // models is already a Set of final display labels (see
    // currentCostBreakdown) -- no further mapping needed here.
    const labels = Array.from(models || []).filter(Boolean);
    if (!labels.length) {
      el.hidden = true;
      return;
    }
    el.textContent = labels.join(tr("、"));
    el.hidden = false;
  }

  function renderCostModal() {
    const breakdown = currentCostBreakdown();
    const analysisCost = breakdown?.analysisCost || 0;
    const editCost = breakdown?.editCost || 0;
    const totalCost = breakdown?.totalCost || 0;
    if (els.costModalAnalysis) els.costModalAnalysis.textContent = formatCostValue(analysisCost);
    if (els.costModalEdit) els.costModalEdit.textContent = formatCostValue(editCost);
    if (els.costModalTotal) els.costModalTotal.textContent = formatCostValue(totalCost);
    renderModelCaption(els.costModalAnalysisModel, breakdown?.analysisModels);
    renderModelCaption(els.costModalEditModel, breakdown?.editModels);
  }

  function openCostModal() {
    renderCostModal();
    if (els.costModal) els.costModal.hidden = false;
  }

  function closeCostModal() {
    if (els.costModal) els.costModal.hidden = true;
  }

  function tokenUsageAsText() {
    const usage = currentTokenUsage();
    if (!usage) return "";
    const timings = currentTimings() || {};
    const imageQuality = currentExtraInfo()?.image_quality;
    const lines = [];
    let totalCost = 0;
    let totalTokens = 0;
    let totalSeconds = 0;
    Object.entries(usage).forEach(([stage, entry]) => {
      const priced = stageCost(entry);
      if (!priced) return;
      const { tokens, cost } = priced;
      totalCost += cost;
      totalTokens += tokens.total;
      const seconds = timings[stage];
      if (seconds != null) totalSeconds += Number(seconds);
      // Step 5b (gaze repair) always runs at the same quality as Step 5's own
      // edit call when it runs at all (pipeline.py's quality-upgrade only
      // fires under single_pass_edit, which is exactly when Step 5b is
      // skipped) -- so labeling just this one stage covers both.
      const modelLabel = stage === "Step 5 - image_edit" && imageQuality
        ? `${entry.model}, quality=${imageQuality}`
        : entry.model;
      // reasoningTokens is null for stages that have no reasoning phase at
      // all (image-edit calls, Gemini) -- only label the ones where it's a
      // real signal (OpenAI Responses API stages), and go off the actual
      // token count rather than config.py's ANALYSIS_MODEL_REASONING_EFFORT
      // mapping, so this stays correct even if that mapping changes. entry.
      // reasoning_effort (added alongside reasoningTokens, same stages only)
      // is the effort level config.py's ANALYSIS_MODEL_REASONING_EFFORT
      // actually had *at the time this call ran* -- shown alongside the
      // token count since a historical run's effort can differ from what
      // that mapping says today.
      const effortPart = entry.reasoning_effort ? `effort=${entry.reasoning_effort}` : null;
      const reasoningLabel = tokens.reasoningTokens == null
        ? ""
        : tokens.reasoningTokens > 0
          ? `, reasoning(${[tokens.reasoningTokens, effortPart].filter(Boolean).join(", ")})`
          : effortPart ? `, non-reasoning(${effortPart})` : ", non-reasoning";
      const timePart = seconds != null ? tr("{v1}s｜", { v1: Number(seconds).toFixed(2) }) : "";
      lines.push(
        `${stage} (${modelLabel}${reasoningLabel})`.padEnd(64) +
        tr("{timePart}輸入 文字{text}/圖片{image}｜輸出 文字{v4}/圖片{v5}｜共 {total} tokens ｜ ${v7}", { timePart, text: tokens.input.text, image: tokens.input.image, v4: tokens.output.text, v5: tokens.output.image, total: tokens.total, v7: cost.toFixed(4) })
      );
    });
    if (!lines.length) return "";
    lines.push("", `${"總計".padEnd(64)}${totalSeconds > 0 ? `${totalSeconds.toFixed(2)}s｜` : ""}共 ${totalTokens} tokens ｜ $${totalCost.toFixed(4)}`);
    return lines.join("\n");
  }

  // One tab per stage; clicking a tab is the only thing that changes
  // #prompts-modal-content, so exactly one stage's text is ever shown.
  function renderPromptsModalTabs() {
    if (!els.promptsModalTabs || !els.promptsModalContent) return;
    els.promptsModalTabs.innerHTML = "";
    const stages = PROMPT_MODAL_STAGES.map((stage) => {
      if (stage.image) return { label: stage.label, image: true, empty: !lastResult?.jobId };
      const { label, el, text: getText, emptyText } = stage;
      const fallback = emptyText || tr("（此步驟未呼叫 GPT）");
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
        if (stage.image) loadPromptsModalImageTab();
        else showPromptsModalText(stage.text);
      });
      els.promptsModalTabs.appendChild(btn);
      return btn;
    });
    if (buttons.length) {
      buttons[0].classList.add("is-active");
      if (stages[0].image) loadPromptsModalImageTab();
      else showPromptsModalText(stages[0].text);
    }
  }

  function openPromptsModal() {
    if (!els.promptsModal) return;
    renderPromptsModalTabs();
    els.promptsModal.hidden = false;
  }

  function closePromptsModal() {
    if (els.promptsModal) els.promptsModal.hidden = true;
    if (promptsModalImageObjectUrl) {
      URL.revokeObjectURL(promptsModalImageObjectUrl);
      promptsModalImageObjectUrl = null;
    }
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
    // Read back by each checkbox's own data-recipe-index rather than DOM
    // order -- fillCheckList visually pins a reframing item to the top of
    // #ap-recipe-list, but recipeChecked must still line up positionally
    // with base.correction_plan/correction_axes/correction_inverse, which
    // stay in retouch_recipe's original (unsorted) order.
    const recipeBoxes = els.apRecipeList
      ? Array.from(els.apRecipeList.querySelectorAll("input[type=checkbox]"))
      : [];
    let recipeChecked = null;
    if (recipeBoxes.length) {
      recipeChecked = [];
      recipeBoxes.forEach((cb) => {
        const index = Number(cb.dataset.recipeIndex);
        if (Number.isInteger(index) && index >= 0) recipeChecked[index] = cb.checked;
      });
    }
    return { options, recipeChecked };
  }

  // Packages the user's review choices (修圖建議 checkboxes, pose/angle/
  // lighting/size picks, 人像修正設定 toggles, per-person face overrides, the
  // Body Reshape switch) for the finish request. The backend applies them onto
  // the reviewed analysis (photo_agent/selections.py's apply_selections) --
  // this used to happen here, as buildEffectiveAnalysis, so only the rewritten
  // analysis ever reached the server. recipeChecked stays indexed against the
  // correction_plan list the review screen rendered.
  function buildEditSelections({
    options, recipeChecked, advancedOptions, faceIssueOverrides, bodyReshapeEnabled
  } = {}) {
    return {
      options: options || {},
      recipe_checked: Array.isArray(recipeChecked) ? recipeChecked : null,
      advanced_options: advancedOptions || null,
      face_issue_overrides: faceIssueOverrides || null,
      body_reshape_enabled: typeof bodyReshapeEnabled === "boolean" ? bodyReshapeEnabled : null
    };
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
    record.faceIssueOverrides = getFaceIssueOverrides();
    record.bodyReshapeEnabled = getBodyReshapeReviewEnabled();
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
      syncStylePicks();
      syncRestoredPoseSuggestions(options.pose);
    }
    if (Array.isArray(recipeChecked) && els.apRecipeList) {
      // Match by data-recipe-index, not DOM position -- see the matching
      // comment in readStyleSelectionsFromDom.
      const boxes = els.apRecipeList.querySelectorAll("input[type=checkbox]");
      boxes.forEach((box) => {
        const index = Number(box.dataset.recipeIndex);
        if (Number.isInteger(index) && index >= 0 && index < recipeChecked.length) {
          box.checked = recipeChecked[index];
        }
      });
      syncReframingRatioLock();
    }
  }

  // Pre-fills this record's own pick if it has one, else the batch-level
  // shared pick -- so the per-image Pose/Angle/Lighting/Size radios always
  // show what will actually be submitted, same merge rule as effectiveOptions.
  function applyBatchReviewSelections(record) {
    applyFaceIssueOverrides(record.faceIssueOverrides);
    if (typeof record.bodyReshapeEnabled === "boolean") {
      setBodyReshapeReviewEnabled(record.bodyReshapeEnabled);
    }
    // The face review cards were already built (renderAnalysisPreview, called
    // just before this function at its one call site) before these overrides
    // existed on faceThumbEntries -- rebuild them so the toggles on screen
    // reflect this record's restored picks instead of each issue's plain
    // default state.
    renderFaceReviewCards();
    // Pose is layered over the restored individual values and remains
    // independent from the batch/global 人像修正設定.
    applyStyleSelections({ options: effectiveOptions(record), recipeChecked: record.recipeChecked });
  }

  function resetApp() {
    hideError();
    historyCaseAnalysis = null;
    historyCaseAnalysisJobId = null;
    // Going back to the upload screen hands the screen to nobody -- whatever
    // job is still running in the background (a revise the user kicked off
    // from the results page, say) must not render itself here later, on top
    // of whatever the user has started doing since. See historyViewToken's
    // own comment for the exact sequence this covers.
    historyViewToken += 1;
    // Undoes viewHistoryRecord's own revealDetailExtra override (admin
    // reviewing a flagged case) -- a fresh upload afterward shouldn't
    // inherit that one record's forced visibility.
    if (resultsDetailExtra) resultsDetailExtra.hidden = true;
    updateResultStatusPill(null);
    selectedFile = null;
    selectedFileSourceUrl = null;
    lastResult = null;
    setCurrentCaseTimes();
    singleRuns = null;
    singleMirrorIndex = null;
    // The batch whose revise panel this pointed at is gone -- don't keep its
    // record alive, or compare the next batch's records against it.
    inlinePanelBatchRecord = null;
    // A stale 生成張數 (RUN count) left over from earlier in this same
    // session -- either the user's own explicit pick on the upload/review
    // picker (applyRunCountChange), or one restored from a multi-run history
    // record's revise panel (startInlineBatchReview setting
    // els.apRunCountSelect.value = record.runs.length) -- must not silently
    // carry into the NEXT, unrelated upload. finishSingleFlow reads this
    // value directly to decide how many independent edit jobs to submit, so
    // a leftover >1 here would make a normal upload -> analyze -> 美化 flow
    // quietly produce multiple real results instead of the one the user
    // expects. Reset both mirrored pickers (see applyRunCountChange) back to
    // "1" on every real fresh start; a user who wants more than one result
    // still chooses that explicitly again on the upload screen.
    applyRunCountChange(1);
    // A real fresh start (unlike just toggling tiles in the gallery without
    // confirming, which leaves the picks in place -- see demoSelectedNames'
    // own doc above) -- the gallery should show nothing checked afterward,
    // not whatever an unrelated earlier upload had picked.
    demoSelectedNames.clear();
    updateDemoImagesConfirmBtn();
    // #advanced-settings-fold (人像修正設定) may currently be sitting in the
    // upload page's compact row rather than its normal home under the drop
    // zone (setupCompactUploadSelectors moves it when the selectors wrap) --
    // put it back now that resetApp is returning there. The results page no
    // longer borrows it at all; that page has its own #ap-advanced-settings-fold.
    ensureAdvancedSettingsFoldIn(els.uploadInlineSettingsRow2);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";
    if (previewThumbUrl) URL.revokeObjectURL(previewThumbUrl);
    previewThumbUrl = "";
    originalImgThumbUrl = "";
    resetBatchState();
    // 分析模型/修圖模型/長寬比/人像設定 back to the product defaults, for the same
    // reason applyRunCountChange(1) above resets 生成張數: a pick made for one
    // upload must not silently become the next, unrelated one's. After
    // resetBatchState() so there is no live case state left to push onto.
    resetSettingsToDefaults();

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
    // If the last thing the user did was an inline analysis review (single or
    // batch), #analysis-preview and #ap-options-card-inline are currently on
    // loan inside the results section (#results-analysis-slot /
    // #results-canvas-top) -- move them back to the processing section
    // before the next photo's analyze tries to show #analysis-preview there,
    // or it renders invisibly inside the now-hidden results section instead.
    restoreAnalysisPreviewHome();
    closePendingResults();
    clearInlineAnalysisWait(); // results page's revise-panel listeners (see waitForInlineAnalysisAction)
    releaseHistoryCanvasRow(); // a history load cut short by Home (see viewHistoryRecord)
    setFlowStage("idle"); // back to the upload screen -- nothing running
    if (els.inlineAnalysisActions) els.inlineAnalysisActions.hidden = true;
    if (els.analysisPreview) els.analysisPreview.hidden = true;
    if (els.apRestartActions) els.apRestartActions.hidden = true;
    updateApCaseSettingsTrigger();
    // Clears whatever showArchivedAnalysisNotice's readOnly lock left behind
    // (setAnalysisPreviewDisabled(true) on #analysis-preview /
    // #ap-options-card-inline -- see isCompatible). Those are the same
    // singleton nodes the upload page's own 模型設定 picker reuses, not
    // recreated per case, so without this the disabled attributes/
    // ap-readonly-locked class from viewing an archived case would otherwise
    // silently carry over and leave a brand-new upload's model settings
    // stuck unselectable after Home/回到首頁.
    setAnalysisPreviewDisabled(false);
    // startSubmit()'s own `finally` never runs if the user resets instead of
    // clicking continue (waitForPendingResultsAction's promise is left
    // pending), so re-enable Start explicitly here too.
    els.startButton.disabled = false;
    if (els.startFullButton) els.startFullButton.disabled = false;
    if (timingDetails) timingDetails.hidden = true;
    if (timingList) timingList.innerHTML = "";
    resetSteps();
    showSection("upload");
    // setupCompactUploadSelectors (narrow viewports) tracks its own
    // "compact" flag and moves #advanced-settings-fold a SECOND time, into
    // row-1 alongside its collapsed 模型設定 menu, whenever the three
    // selector pills wrap. ensureAdvancedSettingsFoldIn above bypasses that
    // closure entirely, so if compact mode was active before this reset,
    // the fold lands back in row-2 while the closure still believes it's in
    // row-1 -- row-1 then shows only the menu button and row-2 shows the
    // fold+Run, splitting one row into two and growing
    // #upload-inline-settings from 40px to 70px tall. Re-running the same
    // refresh the resize handler uses re-measures and restores whichever
    // layout (compact or expanded) actually fits now -- this has to happen
    // AFTER showSection("upload") just above, not right after the reparent:
    // #upload-section is still hidden (display:none, "visible" class not
    // yet applied) until that call, and getBoundingClientRect() on hidden
    // elements measures everything as 0, which reads as "nothing wraps" and
    // wrongly leaves/restores the expanded (non-compact) layout.
    refreshCompactUploadSelectors?.();
    // Reload 最近照片 from the server instead of leaving whatever the
    // gallery last happened to show -- resetApp is how the user gets back
    // to the upload page from anywhere else (Home, 清除, finishing a
    // result), and by then a job started on a previous visit may have
    // finished, or a whole separate visit may have added new history
    // entries since this page was last on screen.
    refreshUploadGallery(getCurrentUser());
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
  // run_N/{result image,result.json} beneath it) loads that run's report and
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
      throw new Error(tr("資料夾裡找不到原圖與 run_N 子資料夾。"));
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
      throw new Error(tr("子資料夾裡都找不到可用的 result.json（需含 analysis）。"));
    }
    // Say what was left out rather than quietly importing a subset.
    if (skipped.length) {
      showError(tr("已略過 {length} 個無法讀取的子資料夾：{v2}{v3}", { length: skipped.length, v2: skipped.slice(0, 5).join(tr("、")), v3: skipped.length > 5 ? " …" : "" }));
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
      analysisModel: extraInfo.analysis_model || "gpt-5.6-luna",
      // The pref this run's EDIT was made under (recorded by finish_stage).
      // _aspect_ratio_pref_at_analysis is the pre-ratio-neutral-analysis field,
      // read only so folders exported back then still re-run as they were.
      aspectRatio: result.analysis?._aspect_ratio_pref_at_edit
        || result.analysis?._aspect_ratio_pref_at_analysis
        || "auto",
      outputScale: findOutputScaleOption(extraInfo.output_scale)?.value,
      // extra-info.image_model is null exactly when the run was local-only.
      aiPhotoEditing: Boolean(extraInfo.image_model),
      // A skipped QA writes an empty `checks` object; a QA that ran (or ran and
      // failed into the fallback report) always has keys in it.
      runQa: Object.keys(result.quality_check?.checks || {}).length > 0,
      // New exports record the actual mode directly. Older exports predate
      // that field and use the product default: one image-edit call, with no
      // implicit Step 5b merely because metadata is missing.
      singlePassEdit: resolvedSinglePassEdit(extraInfo.single_pass_edit),
      path: "local"                  // /rerun is a local-queue route only
    };
  }

  async function importResultFolder(dirEntry) {
    hideError();
    try {
      const images = await readResultFolder(dirEntry);
      resetBatchState();
      resetSteps();
      closePendingResults();
      if (images.length > 1) await importAsBatch(images);
      else await importAsSingle(images[0], dirEntry.name);
    } catch (error) {
      showError(tr("無法讀取結果資料夾：{v1}", { v1: friendlyError(error) }));
    }
  }

  async function importAsSingle(image, folderName) {
    const analysis = image.runs[0].result.analysis;
    const settings = importedRunSettings(image.runs[0].result);

    // setSelectedFile rejects an unsupported type by showing its own error
    // and returning, rather than throwing -- so check it actually took.
    await setSelectedFile(image.file);
    if (selectedFile !== image.file) return;

    // Into the results page's own pickers, so the review panel opens showing
    // the model/長寬比 this folder was produced with. Not the upload page's --
    // those keep whatever the user has set there for their next upload.
    if (settings.modelQualityOpt.value) applyModelQualityChange(settings.modelQualityOpt.value);
    applyAnalysisModelChange(settings.analysisModel);
    applyAspectRatioChange(settings.aspectRatio);
    applyOutputScaleChange(settings.outputScale);
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
      outputScale: settings.outputScale,
      aiPhotoEditing: settings.aiPhotoEditing,
      runQa: settings.runQa,
      singlePassEdit: settings.singlePassEdit,
      processingPath: settings.path,
      analyzeTimings: [],
      useCombinedJob: false
    };
    applySinglePassEditOption(singleFlowState.singlePassEdit);
    // 人像設定 is read back off the DOM by a revise, so it has to show this
    // folder's own frozen values rather than the last case's left-over ones.
    applyAdvancedOptionsOption(recordAdvancedOptions(null, analysis), "ap");
    // Restores the style picks and prompt the imported run was produced with,
    // so the review panel opens showing them rather than defaults.
    lastSingleSelections = {
      userPrompt: analysis.user_instruction?.normalized_user_direction || "",
      options: analysis.style_options || undefined,
      recipeChecked: null,
      bodyReshapeEnabled: analysis?._body_reshape_enabled !== false
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
      outputScale: settings.outputScale,
      aiPhotoEditing: settings.aiPhotoEditing,
      runQa: settings.runQa,
      singlePassEdit: settings.singlePassEdit,
      path: settings.path
    };
    applySinglePassEditOption(batchSettings.singlePassEdit);
    if (settings.modelQualityOpt.value) applyModelQualityChange(settings.modelQualityOpt.value);
    applyAspectRatioChange(settings.aspectRatio);
    applyOutputScaleChange(settings.outputScale);

    batchRecords = [];
    for (const image of images) {
      const analysis = image.runs[0].result.analysis;
      const record = makeBatchRecord(image.file, await previewableBlob(image.file));
      record.analysis = analysis;
      record.analyzeJobId = null;    // -> rerunJob
      record.options = analysis.style_options || null;
      record.bodyReshapeEnabled = analysis?._body_reshape_enabled !== false;
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
  if (els.demoImagesConfirmBtn) els.demoImagesConfirmBtn.addEventListener("click", confirmDemoImageSelection);
  if (els.uploadGalleryTabRecent) els.uploadGalleryTabRecent.addEventListener("click", () => setUploadGalleryTab("recent"));
  if (els.uploadGalleryTabSample) els.uploadGalleryTabSample.addEventListener("click", () => setUploadGalleryTab("sample"));
  if (els.demoImagesGrid) {
    els.demoImagesGrid.addEventListener("scroll", () => {
      // 最近照片 only. This grid is shared with 範例照片, and a history page
      // loaded while that tab owns it appends AFTER the sample tiles rather
      // than replacing them -- renderHistoryList's append path finds no
      // existing .history-day-group among them and creates a new one at the
      // end, so recent photos end up tacked onto the bottom of the sample
      // gallery. Every other caller of loadUploadHistoryPage already checks
      // which tab is showing (setUploadGalleryTab sets it before calling,
      // refreshUploadGalleryIfVisible tests it outright); this listener was
      // the one entry point that didn't.
      if (uploadGalleryActiveTab !== "recent") return;
      if (uploadHistoryLoading || !uploadHistoryHasMore) return;
      const nearBottom = els.demoImagesGrid.scrollTop + els.demoImagesGrid.clientHeight
        >= els.demoImagesGrid.scrollHeight - 24;
      if (nearBottom) loadUploadHistoryPage();
    });
  }
  // The whole drawer scrolls here (.history-drawer's own overflow-y), not
  // #reports-list itself like #history-list does.
  if (els.reportsDrawer) {
    els.reportsDrawer.addEventListener("scroll", () => {
      if (reportsLoading || !reportsHasMore) return;
      const nearBottom = els.reportsDrawer.scrollTop + els.reportsDrawer.clientHeight
        >= els.reportsDrawer.scrollHeight - 24;
      if (nearBottom) loadReportsPage();
    });
  }
  if (els.historyList) {
    els.historyList.addEventListener("scroll", () => {
      if (historyDrawerLoading || !historyDrawerHasMore) return;
      const nearBottom = els.historyList.scrollTop + els.historyList.clientHeight
        >= els.historyList.scrollHeight - 24;
      if (nearBottom) loadHistoryDrawerPage();
    });
  }
  // Account bootstrap above is asynchronous. Do not initialize the demo
  // gallery unconditionally here: doing so can render every demo thumbnail
  // before ?user_id has resolved, then waste requests even though the page
  // is about to switch this area to the user's recent photos. The resolved
  // account path calls refreshUploadGallery(), which chooses the right mode.
  // #advanced-settings-fold's static markup (index.html) still sits inside
  // #settings-modal-body as written -- move it to its real default home
  // (#upload-inline-settings-row-2, right after that row's own caption)
  // right away, since the app always starts on the upload screen.
  ensureAdvancedSettingsFoldIn(els.uploadInlineSettingsRow2);
  setupCompactUploadSelectors();
  // Keep the query string (env flags etc.) across the switch to the chat UI,
  // the same way the chat page's 面板模式 link does on the way back.
  // An open case goes along as #case=<image_key>: the chat page reopens it
  // in its conversation, or rebuilds one from history (routes/chat.py's
  // /sessions/from-case).
  // Both ways there -- the header's #chat-ui-btn and the 對話 half of the
  // step row's 對話｜面板 switch -- behave the same.
  for (const chatUiLink of [
    document.getElementById("chat-ui-btn"),
    document.getElementById("chat-mode-switch-link"),
  ]) {
    if (!chatUiLink) continue;
    chatUiLink.setAttribute("href", `chat/${location.search}`);
    chatUiLink.addEventListener("click", async (event) => {
      if (chatUiLink.hasAttribute("aria-disabled")) { event.preventDefault(); return; }
      // A new tab leaves this page as it is.
      if (event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      const user = getCurrentUser();
      // A photo uploaded on this page has no case key on the client -- only
      // its jobs, which /case resolves (see findCaseForJob).
      const jobId = lastResult?.jobId || (selectedFile && singleFlowState?.analyzeJobId);
      let caseKey = currentCaseHistoryImageKey;
      if (user && !caseKey && jobId) {
        try {
          caseKey = (await getHistoryCase(user, { jobId }))?.case?.image_key || null;
        } catch {
          caseKey = null; // not in history yet -- just open the chat page
        }
      }
      if (!writeModeHandoff(caseKey) && isReviewDirty()) {
        const leave = await showConfirmModal(
          tr("這張照片還沒美化的調整沒辦法帶到對話模式，切過去就會清掉。確定要切換嗎？"),
          { confirmLabel: tr("切換"), cancelLabel: tr("留在這裡"), danger: false }
        );
        if (!leave) return;
      }
      leaveConfirmed = true;
      location.href = `chat/${location.search}${caseKey ? `#case=${encodeURIComponent(caseKey)}` : ""}`;
    });
  }
  setupHeaderMenu();
  // Reload, close, or any other way off the page: the browser's own leave
  // prompt (its wording cannot be changed) when review edits are unsent.
  window.addEventListener("beforeunload", (event) => {
    if (leaveConfirmed || !isReviewDirty()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  // The portrait-settings panel is a popover on the upload page. Close it
  // when the user clicks anywhere outside the shared fold, while allowing
  // clicks inside the panel (including its toggles) to pass through.
  document.addEventListener("click", (e) => {
    const fold = els.advancedSettingsFold;
    if (fold && fold.open && !fold.contains(e.target)) fold.open = false;
  });
  els.startButton.addEventListener("click", async () => {
    // Nothing uploaded yet -- startSubmit's own "請先上傳一張照片" error is
    // the right response, not a cost estimate for a request that's about to
    // be rejected anyway (same condition startSubmit itself checks).
    if (!selectedFile && batchRecords.length <= 1) { startSubmit({ skipReview: false }); return; }
    const confirmed = await showPreflightCostModal([analysisPreflightStep()]);
    if (!confirmed) return;
    startSubmit({ skipReview: false });
  });
  if (els.startFullButton) els.startFullButton.addEventListener("click", async () => {
    if (!selectedFile && batchRecords.length <= 1) { startSubmit({ skipReview: true }); return; }
    const confirmed = await showPreflightCostModal([analysisPreflightStep(), editPreflightStep()]);
    if (!confirmed) return;
    startSubmit({ skipReview: true });
  });
  els.showPromptBtn.addEventListener("click", openPromptsModal);
  if (els.promptsModalClose) els.promptsModalClose.addEventListener("click", closePromptsModal);
  if (els.promptsModalImageCompareBtn) els.promptsModalImageCompareBtn.addEventListener("click", togglePromptsModalImageCompare);
  if (els.promptsModal) {
    els.promptsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closePromptsModal();
    });
  }
  if (els.changelogBtn) els.changelogBtn.addEventListener("click", openChangelogModal);
  if (els.changelogModalClose) els.changelogModalClose.addEventListener("click", closeChangelogModal);
  if (els.changelogModal) {
    els.changelogModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeChangelogModal();
    });
  }
  if (els.versionStripPreviewBtn) els.versionStripPreviewBtn.addEventListener("click", openVersionStripPreviewModal);
  if (els.canvasVersionBtn) els.canvasVersionBtn.addEventListener("click", openPhotoViewer);
  // compare 切 Before/After 的地方很多（按鈕、renderResult、挑原圖），直接看 class 變化。
  if (els.compareCard) {
    new MutationObserver(syncPhotoViewer).observe(els.compareCard, { attributes: true, attributeFilter: ["class"] });
  }
  // 手機上點照片本身也打開大圖（照片上的按鈕、分析框不算）。
  if (els.photoFrameWrap) {
    els.photoFrameWrap.addEventListener("click", (e) => {
      if (!photoViewerMedia.matches) return;
      if (!e.target.closest(".photo-frame") || e.target.closest("button, a, input, label")) return;
      if (els.compareCard?.classList.contains("is-fullscreen")) return;
      openPhotoViewer();
    });
  }
  // 轉成橫向或視窗拉寬，超過手機寬度就收起來，回到原本的版面。
  photoViewerMedia.addEventListener("change", (e) => {
    if (!e.matches) closePhotoViewer();
  });
  if (els.photoViewerClose) els.photoViewerClose.addEventListener("click", closePhotoViewer);
  if (els.photoViewerStage) {
    els.photoViewerStage.addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closePhotoViewer();
    });
  }
  if (els.photoViewerDownload) {
    els.photoViewerDownload.addEventListener("click", () => {
      if (lastResult) downloadCurrentResultOnly();
    });
  }
  if (els.photoViewerHold) {
    const hold = els.photoViewerHold;
    const release = () => setPhotoViewerHolding(false);
    hold.addEventListener("pointerdown", (e) => {
      hold.setPointerCapture(e.pointerId);
      setPhotoViewerHolding(true);
    });
    hold.addEventListener("pointerup", release);
    hold.addEventListener("pointercancel", release);
    hold.addEventListener("lostpointercapture", release);
    hold.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") setPhotoViewerHolding(true);
    });
    hold.addEventListener("keyup", release);
    hold.addEventListener("blur", release);
    hold.addEventListener("contextmenu", (e) => e.preventDefault());
  }
  if (els.versionStripPreviewModalClose) els.versionStripPreviewModalClose.addEventListener("click", closeVersionStripPreviewModal);
  if (els.versionStripPreviewModal) {
    els.versionStripPreviewModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeVersionStripPreviewModal();
    });
  }
  // 首頁 is the chat page's now -- this page only serves the analysis/result
  // screens, so 回到首頁 (and the logo) leave for it, keeping the query
  // string the same way #chat-ui-btn does.
  const goChatHome = async () => {
    if (isReviewDirty()) {
      const leave = await showConfirmModal(
        tr("這張照片還沒美化的調整會清掉。確定要回到首頁嗎？"),
        { confirmLabel: tr("回到首頁"), cancelLabel: tr("留在這裡"), danger: false }
      );
      if (!leave) return;
    }
    leaveConfirmed = true;
    location.href = `chat/${location.search}`;
  };
  els.resetButton.addEventListener("click", goChatHome);
  // 新對話: like 回到首頁, over to the chat page, but straight to its 新對話
  // photo-picking screen (#new) instead of its home.
  if (els.newChatButton) {
    els.newChatButton.addEventListener("click", async () => {
      if (isReviewDirty()) {
        const leave = await showConfirmModal(
          tr("這張照片還沒美化的調整會清掉。確定要開新對話嗎？"),
          { confirmLabel: tr("開新對話"), cancelLabel: tr("留在這裡"), danger: false }
        );
        if (!leave) return;
      }
      leaveConfirmed = true;
      location.href = `chat/${location.search}#new`;
    });
  }
  if (els.logoHomeBtn) els.logoHomeBtn.addEventListener("click", goChatHome);
  if (els.compareToggleBtn) {
    els.compareToggleBtn.addEventListener("click", () =>
      setCompareView(!els.compareCard?.classList.contains("showing-generated")));
  }
  if (els.compareFullscreenBtn) els.compareFullscreenBtn.addEventListener("click", toggleCompareFullscreen);
  if (els.compareFullscreenBackdrop) els.compareFullscreenBackdrop.addEventListener("click", closeCompareFullscreen);
  if (els.reportBtn) els.reportBtn.addEventListener("click", openReportModal);
  syncReportBtnPosition();
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
    // Opens #boxes-type-menu (a checkbox per box type) instead of directly
    // toggling a single on/off flag -- the boxes are in original-image
    // coordinates, so opening it also flips the canvas back to Before,
    // same reasoning as before (drawing them over the result would point at
    // the wrong content, see drawAnalysisBoxes in results.js).
    els.boxesToggleBtn.addEventListener("click", () => {
      toggleBoxesTypeMenu();
      setCompareView(false);
    });
  }
  // The overlay is positioned against the photo's rendered rect, which moves
  // whenever the image or the layout changes.
  if (els.originalImg) els.originalImg.addEventListener("load", () => drawAnalysisBoxes());
  window.addEventListener("resize", () => drawAnalysisBoxes());
  els.downloadLink.addEventListener("click", handleDownloadClick);
  if (els.downloadCurrentBtn) els.downloadCurrentBtn.addEventListener("click", handleDownloadCurrentBtnClick);
  if (els.downloadAllRunsBtn) els.downloadAllRunsBtn.addEventListener("click", handleDownloadAllRunsBtnClick);
  if (els.errorModalClose) els.errorModalClose.addEventListener("click", closeErrorModal);
  if (els.errorModal) {
    els.errorModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeErrorModal();
    });
  }
  if (els.noticeModalOkBtn) els.noticeModalOkBtn.addEventListener("click", closeNoticeModal);
  if (els.noticeModalActionBtn) els.noticeModalActionBtn.addEventListener("click", handleNoticeModalAction);
  if (els.noticeModalAction2Btn) els.noticeModalAction2Btn.addEventListener("click", handleNoticeModalAction2);
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
  if (els.recentPhotosHistoryBtn) els.recentPhotosHistoryBtn.addEventListener("click", openHistoryPage);
  document.addEventListener("photoagent:sectionchange", (event) => {
    if (els.accountBadge) {
      const user = getCurrentUser();
      els.accountBadge.hidden = !user || event.detail?.name === "upload";
    }
  });
  // #page-scroll-hint's inputs: which screen is up (this event), where the
  // page is scrolled to, and how tall its content is. The last one is the
  // one that needs an observer rather than an event -- on both screens this
  // covers, the content under the photo arrives (and the photo itself
  // resolves to its real height) well after the section change, so a
  // one-shot read at switch time would decide there's nothing below on
  // exactly the pages there is. #page-scroll-hint is position:fixed and so
  // contributes no height of its own, which is what keeps showing/hiding it
  // from re-triggering the observer that showed it.
  document.addEventListener("photoagent:sectionchange", (event) => {
    currentSectionName = event.detail?.name || "upload";
    syncCaseHash();
    pageScrollHintDismissed = false;
    updatePageScrollHint();
  });
  window.addEventListener("scroll", handlePageScroll, { passive: true });
  window.addEventListener("resize", updatePageScrollHint);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(updatePageScrollHint).observe(document.body);
  }
  if (els.pageScrollHint) els.pageScrollHint.addEventListener("click", handlePageScrollHintClick);
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
  if (els.costBtn) els.costBtn.addEventListener("click", openCostModal);
  if (els.costModalClose) els.costModalClose.addEventListener("click", closeCostModal);
  if (els.costModal) {
    els.costModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeCostModal();
    });
  }
  if (els.preflightCostModal) {
    els.preflightCostModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) preflightCostModalHandlers?.cancel();
    });
  }
  // Every gate that opens #preflight-cost-modal awaits it before doing
  // anything that would change section (showSection dispatches this event) --
  // so under normal use, by the time a section change happens, the modal has
  // already resolved and cleared its own handlers. The one case this still
  // catches is the user abandoning it mid-decision (e.g. opening a different
  // history record, whose own archived-case notice sits at a higher z-index
  // and would otherwise silently hide this modal behind it, only for
  // dismissing that notice to reveal it again). Force-resolving it as a
  // cancel here is a no-op whenever it's already closed.
  document.addEventListener("photoagent:sectionchange", () => preflightCostModalHandlers?.cancel());
  setupThemeToggle();
  if (els.headerSettingsBtn) els.headerSettingsBtn.addEventListener("click", openContextSettingsModal);
  if (els.settingsModalClose) els.settingsModalClose.addEventListener("click", closeSettingsModal);
  if (els.settingsModal) {
    els.settingsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeSettingsModal();
    });
  }
  if (els.apSettingsModalClose) els.apSettingsModalClose.addEventListener("click", closeApSettingsModal);
  if (els.apSettingsModal) {
    els.apSettingsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeApSettingsModal();
    });
  }
  if (els.apCaseSettingsBtn) els.apCaseSettingsBtn.addEventListener("click", openApCaseSettingsModal);
  if (els.apCaseSettingsModalClose) els.apCaseSettingsModalClose.addEventListener("click", closeApCaseSettingsModal);
  if (els.apCaseSettingsModal) {
    els.apCaseSettingsModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeApCaseSettingsModal();
    });
  }
  // Applied immediately on load (not just once the settings modal is first
  // opened) since a review panel can already be showing by the time the
  // user ever opens 設定.
  document.body.classList.toggle("side-panel-layout", isSidePanelLayoutEnabled());
  syncResultsAnalysisSlotForSidePanelLayout();
  if (els.sidePanelLayoutToggle) {
    els.sidePanelLayoutToggle.checked = isSidePanelLayoutEnabled();
    els.sidePanelLayoutToggle.addEventListener("change", () => {
      setSidePanelLayoutEnabled(els.sidePanelLayoutToggle.checked);
      syncResultsAnalysisSlotForSidePanelLayout();
      // An already-open review panel (first-time 分析 pause, or an
      // in-progress revise) re-parents right away instead of waiting for
      // the next time it opens.
      if (els.resultsCanvasRow && els.resultsCanvasRow.classList.contains("showing-analysis")) {
        moveOptionsCardTo(getOptionsCardTarget());
      }
    });
  }
  // Same "apply immediately on load" reasoning as side-panel-layout above --
  // the upload page's gallery can already be on screen by the time the user
  // ever opens 設定.
  {
    const initialGalleryLayoutMode = getGalleryLayoutMode();
    applyGalleryLayoutMode(initialGalleryLayoutMode);
    syncGalleryLayoutModeButtons(initialGalleryLayoutMode);
    document.querySelectorAll(".gallery-layout-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => setGalleryLayoutMode(btn.dataset.galleryLayoutMode));
    });
    // 預設 tracks the viewport live -- crossing the 1200px breakpoint while
    // the page is open (resizing a desktop window, rotating a tablet)
    // re-picks 格狀/交錯 right away instead of only on the next reload.
    // Purely an enhancement on top of the resolve-on-load above, so every
    // branch here degrades to "updates on next load" rather than failing:
    // addListener is the pre-Safari-14 spelling, and neither being present
    // just means no live updates.
    const onNarrowChange = () => {
      if (getGalleryLayoutMode() === "auto") applyGalleryLayoutMode("auto");
    };
    if (galleryLayoutNarrowMql?.addEventListener) {
      galleryLayoutNarrowMql.addEventListener("change", onNarrowChange);
    } else if (galleryLayoutNarrowMql?.addListener) {
      galleryLayoutNarrowMql.addListener(onNarrowChange);
    }
  }
  // Same shared-class pattern as the theme picker above -- this toggle is
  // duplicated into both #settings-modal and #ap-settings-modal (see their
  // own markup comments), so every matching checkbox gets the initial state
  // and its own change listener here.
  document.querySelectorAll(".preflight-cost-toggle").forEach((toggle) => {
    toggle.checked = isPreflightCostEstimateEnabled();
    toggle.addEventListener("change", () => setPreflightCostEstimateEnabled(toggle.checked));
  });
  // Resets to the pills-only view every time this tab is switched to --
  // same as this tab's old openStyleModal always starting there, so
  // whichever category grid was left open on a previous visit doesn't
  // linger. results.js's own generic tab-click listener (wired alongside
  // #ap-review-tab-recipe/-face) handles switching which tab is actually
  // active/visible; this is purely the extra style-specific reset.
  if (els.apReviewTabStyle) els.apReviewTabStyle.addEventListener("click", () => showStyleGroup(null));
  if (els.apStyleModalClose) els.apStyleModalClose.addEventListener("click", closeStyleModal);
  if (els.apStyleModal) {
    els.apStyleModal.addEventListener("click", (e) => {
      if (e.target.dataset.close) closeStyleModal();
    });
  }
  document.querySelectorAll(".ap-style-card").forEach((btn) => {
    btn.addEventListener("click", () => openStyleModal(btn.dataset.styleGroup));
  });
  // A sibling of the card rather than a child, since a <button> can't contain
  // another one -- see .ap-style-card-clear's own CSS (index.html).
  document.querySelectorAll(".ap-style-card-clear").forEach((btn) => {
    btn.addEventListener("click", () => clearStylePick(`ap-${btn.dataset.styleGroup}`));
  });
  // Keeps the has-more-above/-below scroll cue (and #ap-style-scroll-hint)
  // honest as the user actually scrolls each category's list, not just at
  // the moment showStyleGroup first shows it.
  STYLE_GROUPS.forEach((g) => {
    const list = styleGroupListEl(g);
    if (list) list.addEventListener("scroll", () => updateStyleOptionScrollCue(list));
  });
  // #ap-recipe-list/#ap-face-detail get the same cue, minus the text hint
  // (updateListScrollCue, not updateStyleOptionScrollCue -- see that
  // function's own comment) -- each is independently capped + scrollable
  // (see their own max-height comment, styles.css), so a long recipe list
  // or a person with several flagged issues reads as "scroll for more"
  // instead of looking like it's just cut off. MutationObserver, not a
  // call from results.js (a separate IIFE that can't reach into this
  // file's own scope) -- and not ResizeObserver either: both lists stay
  // pinned at their own max-height regardless of content (that's the whole
  // point), so their own border-box size never actually changes; what
  // needs watching is content being swapped in/out inside that same fixed
  // box (a fresh render replacing <li>s, or the `hidden` toggle when
  // applyActiveReviewTab switches tabs), which only a childList/attribute
  // observer -- not a resize one -- would ever see.
  [els.apRecipeList, els.apFaceDetail, els.apFindingsCards].forEach((list) => {
    if (!list) return;
    list.addEventListener("scroll", () => updateListScrollCue(list));
    new MutationObserver(() => updateListScrollCue(list)).observe(list, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden"],
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (els.errorModal && !els.errorModal.hidden) closeErrorModal();
    if (els.noticeModal && !els.noticeModal.hidden) closeNoticeModal();
    if (els.confirmModal && !els.confirmModal.hidden) confirmModalHandlers?.cancel();
    if (els.accountModal && !els.accountModal.hidden) closeAccountModal();
    if (els.costModal && !els.costModal.hidden) closeCostModal();
    if (els.preflightCostModal && !els.preflightCostModal.hidden) preflightCostModalHandlers?.cancel();
    if (els.reportModal && !els.reportModal.hidden) closeReportModal();
    if (els.reportScreenshotModal && !els.reportScreenshotModal.hidden) closeReportScreenshotModal();
    if (els.settingsModal && !els.settingsModal.hidden) closeSettingsModal();
    if (els.apSettingsModal && !els.apSettingsModal.hidden) closeApSettingsModal();
    if (els.apCaseSettingsModal && !els.apCaseSettingsModal.hidden) closeApCaseSettingsModal();
    if (els.promptsModal && !els.promptsModal.hidden) closePromptsModal();
    if (els.changelogModal && !els.changelogModal.hidden) closeChangelogModal();
    if (els.versionStripPreviewModal && !els.versionStripPreviewModal.hidden) closeVersionStripPreviewModal();
    if (els.apStyleModal && !els.apStyleModal.hidden) closeStyleModal();
    // Ahead of closeCompareFullscreen: the 個人化 panel floats inside the
    // compare card, so it's the innermost thing Escape should dismiss --
    // otherwise one press would close it and the fullscreen view together.
    if (els.personalizePopover && !els.personalizePopover.hidden) closePersonalizePopover();
    else if (isCompareFullscreen()) closeCompareFullscreen();
    if (els.batchSidebar && els.batchSidebar.classList.contains("drawer-open")) closeBatchDrawer();
    if (els.historyDrawer && els.historyDrawer.classList.contains("drawer-open")) closeHistoryDrawer();
    if (els.reportsDrawer && els.reportsDrawer.classList.contains("drawer-open")) closeReportsDrawer();
  });
  if (els.batchContinueAllBtn) els.batchContinueAllBtn.addEventListener("click", batchContinueAll);
  if (els.batchDownloadAllBtn) els.batchDownloadAllBtn.addEventListener("click", handleDownloadAllBatchClick);
  if (els.batchDrawerTrigger) els.batchDrawerTrigger.addEventListener("click", openBatchDrawer);
  if (els.batchSidebarCloseBtn) els.batchSidebarCloseBtn.addEventListener("click", closeBatchDrawer);
  if (els.batchSidebarBackdrop) els.batchSidebarBackdrop.addEventListener("click", closeBatchDrawer);

  // Per-image ap-*-list radios: their <ul> elements are stable across
  // renderAnalysisPreview's re-fills (only the <li> children get replaced),
  // so a delegated listener attached once here keeps working for every
  // future render. Picking an actual option closes the modal; clearing one
  // back to 不指定 (a card's own "x" -- clearStylePick, results.js, dispatches
  // this same "change" event) stays in it so another can be picked
  // immediately.
  [["apPoseList", "pose"], ["apAngleList", "angle"], ["apLightingList", "lighting"], ["apSizeList", "size"]]
    .forEach(([elKey, key]) => {
      if (els[elKey]) {
        els[elKey].addEventListener("change", (e) => {
          syncStylePicks();
          if (key === "pose") applyPoseSuggestions(e.target.value);
          if (e.target.value !== "default") closeStyleModal();
          updateInlineAnalysisContinueBtnState();
        });
      }
    });

  // Remaining pieces inlineReviseHasNoChanges reads that don't already have a
  // listener of their own: the recipe checklist (individual checkboxes are
  // rebuilt on every render, but this delegated listener on the stable
  // container survives that) and #ap-face-detail (delegated -- covers every
  // per-person face-issue toggle/level-select it contains, all of which mutate
  // faceThumbEntries in place
  // rather than exposing their own individual change hooks). 補充指令 and its
  // "取代" checkbox have no other listener at all yet.
  if (els.apRecipeList) {
    els.apRecipeList.addEventListener("change", (event) => {
      applyRejectedReframingRatio(event);
      updateInlineAnalysisContinueBtnState();
    });
  }
  if (els.apFaceDetail) {
    els.apFaceDetail.addEventListener("change", updateInlineAnalysisContinueBtnState);
  }
  if (els.apBodyReshapeControl) {
    // The switch is rebuilt with each analysis/result render, so listen on
    // its stable host rather than on the transient checkbox itself.
    els.apBodyReshapeControl.addEventListener("change", updateInlineAnalysisContinueBtnState);
  }
  if (els.promptInput) {
    els.promptInput.addEventListener("input", updateInlineAnalysisContinueBtnState);
  }
  if (els.promptOverwriteToggle) {
    els.promptOverwriteToggle.addEventListener("change", updateInlineAnalysisContinueBtnState);
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
