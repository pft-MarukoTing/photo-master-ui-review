window.PhotoAgentApi = (() => {
  // When deployed behind a reverse-proxy prefix (e.g. /mt-testbed-PhotoAgent),
  // absolute "/api/..." fetches would bypass the proxy's location match and 404.
  // Derive the prefix from the page's own directory instead of hardcoding it,
  // so the same build works unprefixed (local) and prefixed (prod).
  const BASE_URL = window.PhotoAgentConfig.apiBaseUrl || window.location.pathname.replace(/\/[^/]*$/, "");

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // A one-shot API call (submit/analyze/continue/get-result) is just as
  // exposed to a mobile phone's sleep/wake network blip as the long-running
  // pollJobStatus loop below -- if fetch() itself happens to fire right as
  // the tab backgrounds or right as it wakes (network not yet reconnected),
  // it throws "Failed to fetch" with no chance to recover, since there was
  // never a retry here. Retries only a connectivity-level failure (fetch()
  // itself rejecting) -- a real HTTP error response still surfaces
  // immediately to the caller, same as before.
  async function fetchWithRetry(url, options, { retries = 5, delayMs = 2000 } = {}) {
    for (let attempt = 0; ; attempt++) {
      try {
        return await fetch(url, options);
      } catch (error) {
        if (attempt >= retries) throw error;
        await sleep(delayMs);
      }
    }
  }

  // "local" -> /api/photo-agent/... (Redis queue + docker worker)
  // "aipoc" -> /api/photo-agent/aipoc/... (direct call to the shared AIPoC gateway)
  function pathPrefix(path) {
    return path === "aipoc" ? "/api/photo-agent/aipoc" : "/api/photo-agent";
  }

  async function submitJob({ file, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, analysis, advancedOptions, runCount, user, actionId, path = "local" }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("analysis_model", analysisModel || "gpt-4.1-mini");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("run_qa", runQa === false ? "false" : "true");
    // Upload page's Advanced Settings toggles (Eyes category, and whatever
    // joins it later) -- sent as one JSON field instead of one form field per
    // toggle, so a new toggle never needs a new form.append call here.
    form.append("advanced_options", JSON.stringify(advancedOptions || {}));
    if (userPrompt) form.append("user_prompt", userPrompt);
    // "取代最終送給編輯模型的完整指令" checkbox beside 補充指令 (index.html) --
    // tells the backend to send user_prompt verbatim as the whole edit-model
    // prompt instead of folding it into the normal analysis-driven template.
    form.append("overwrite_prompt", overwritePrompt ? "true" : "false");
    // AIPoC-only: the analysis already shown on the preview screen, so the
    // remote worker can reuse it (stage="finish") instead of re-analyzing.
    if (analysis) form.append("analysis", JSON.stringify(analysis));
    // Local path only: how many independent edit variations job_processor.py
    // generates from ONE shared analysis, entirely within this single job.
    if (runCount) form.append("run_count", String(runCount));
    // Account username (account.js) -- lets the backend record this job into
    // that user's processing history (backend/services/account.py). Absent
    // entirely when no account is set, same as every other optional field
    // here.
    if (user) form.append("user", user);
    // Groups every job submitted from ONE "Run" click (runSingleItem/
    // editBatchItem submitting N separate AIPoC jobs for a runCount>1 revise)
    // into the same history entry instead of each overwriting the last --
    // see backend/services/account.py's action_id grouping. Absent for a
    // single-job submission, which just groups under its own id.
    if (actionId) form.append("action_id", actionId);

    const response = await fetchWithRetry(`${BASE_URL}${pathPrefix(path)}/submit`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `提交失敗：HTTP ${response.status}`);
    }
    // The local path returns job_id; the AIPoC path returns task_id.
    return data.job_id || data.task_id;
  }

  // Tells update-check.js a job is being waited on -- see its beginJob/endJob
  // for why: refreshing away from an in-flight job mid-poll would abandon
  // jobId (only ever held in an in-memory JS variable) with no way to
  // recover the result, so an update banner found mid-job waits for this to
  // clear rather than showing immediately.
  async function pollJobStatus(jobId, { path = "local", intervalMs = 1500, timeoutMs = 5 * 60 * 1000, finalCheckMs = 1 * 60 * 1000, onStatus } = {}) {
    window.PhotoAgentUpdateCheck?.beginJob();
    try {
      return await pollJobStatusInner(jobId, { path, intervalMs, timeoutMs, finalCheckMs, onStatus });
    } finally {
      window.PhotoAgentUpdateCheck?.endJob();
    }
  }

  async function pollJobStatusInner(jobId, { path, intervalMs, timeoutMs, finalCheckMs, onStatus }) {
    const deadline = Date.now() + timeoutMs;
    // AIPoC task_ids contain "/" and "+" and must be percent-encoded, or the
    // request either 404s or gets misrouted.
    const url = `${BASE_URL}${pathPrefix(path)}/status/${encodeURIComponent(jobId)}`;
    // Tracks a fetch()-level failure (e.g. "Failed to fetch") separately from
    // a real HTTP/job-status error below -- mobile browsers throttle or drop
    // a backgrounded tab's network access while the user is in another app,
    // so a single connectivity blip here doesn't mean the backend is down.
    let lastConnectivityError = null;

    // One status check: returns true once the job is actually done, throws
    // on a genuine job failure or HTTP error, or returns false for "still
    // processing" / a connectivity blip worth retrying (left in
    // lastConnectivityError for the caller to decide what to do with).
    async function checkOnce() {
      let response;
      try {
        response = await fetch(url);
      } catch (error) {
        lastConnectivityError = error;
        return false;
      }
      lastConnectivityError = null;
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(`${data.error || `查詢狀態失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
      }
      if (data.status === "success") return true;
      if (data.status === "error") throw new Error(`${data.error || "AI 修圖失敗。"}（Job ID: ${jobId}）`);
      // "stage" is only present for a combined analyze+edit job (see
      // job_processor.py) -- lets a single-job submission still show
      // "analyzing" -> "editing" progress instead of one flat message.
      // "runs_done" (also combined-job only) similarly lets a multi-RUN
      // submission notice a newly-landed RUN -- awaited since a caller may
      // need to fetch that RUN's data (getJobRuns) before the next tick.
      if (onStatus) await onStatus(data);
      return false;
    }

    while (Date.now() < deadline) {
      if (await checkOnce()) return;
      await sleep(intervalMs);
    }

    // The client's own budget is up, but the backend job runs independently
    // of this tab and we don't expect it to take longer than timeoutMs --
    // rather than assume failure off a single client-side deadline (which
    // may have elapsed largely while the tab was backgrounded/the phone was
    // asleep, not because the job itself is actually stuck), spend one more
    // short window asking the server directly for a definitive answer before
    // giving up.
    const finalCheckDeadline = Date.now() + finalCheckMs;
    while (Date.now() < finalCheckDeadline) {
      if (await checkOnce()) return;
      await sleep(intervalMs);
    }

    // If we still couldn't even reach the server, surface that connectivity
    // error as-is (friendlyError() turns it into the "無法連線" message)
    // rather than the generic timeout below, which implies the job itself is
    // just taking unusually long.
    if (lastConnectivityError) throw lastConnectivityError;
    throw new Error(`處理逾時，請稍後再試。（Job ID: ${jobId}）`);
  }

  async function getJobResult(jobId, { path = "local" } = {}) {
    const response = await fetchWithRetry(`${BASE_URL}${pathPrefix(path)}/result/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== "success") {
      throw new Error(`${data.error || `取得結果失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data; // { status, image_url, result: { analysis, quality_check, usage } }
  }

  // Multi-RUN counterpart of getJobResult -- a combined job submitted with
  // run_count > 1 (see job_processor.py) reports whichever RUNs have
  // completed SO FAR under "runs", even while status is still "processing"
  // (each RUN lands in Redis as soon as it's done, not only once every RUN
  // is). Safe to call repeatedly while polling to pick up newly-landed RUNs.
  async function getJobRuns(jobId, { path = "local" } = {}) {
    const response = await fetchWithRetry(`${BASE_URL}${pathPrefix(path)}/result/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`${data.error || `取得結果失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    if (data.status === "error") {
      throw new Error(`${data.error || "AI 修圖失敗。"}（Job ID: ${jobId}）`);
    }
    return data; // { status, runs: [{ run, image_url, result }], run_count }
  }

  // Same-origin URL for a result image (vs. the presigned S3/AIPoC URL
  // getJobResult/getJobRuns return, which has no CORS headers for fetch()).
  // Used only when the browser needs to read the image's bytes itself (e.g.
  // bundling it into a zip) -- for plain <img> display, the presigned URL is
  // fine. `run` (1-based) selects which RUN's image for a multi-RUN job;
  // omit for a single-run job.
  function resultImageUrl(jobId, { path = "local", run } = {}) {
    const base = `${BASE_URL}${pathPrefix(path)}/result/${encodeURIComponent(jobId)}/image`;
    return run ? `${base}?run=${encodeURIComponent(run)}` : base;
  }

  // Small JPEG counterpart of resultImageUrl -- local-path only (see
  // backend/routes/api.py's result_thumbnail()). Used for the history grid,
  // which only ever renders this at a few dozen CSS pixels and shouldn't pay
  // for a full-res download to do it.
  function resultThumbnailUrl(jobId, { run } = {}) {
    const base = `${BASE_URL}/api/photo-agent/result/${encodeURIComponent(jobId)}/thumbnail`;
    return run ? `${base}?run=${encodeURIComponent(run)}` : base;
  }

  // Local-path only -- see backend/routes/api.py's source_image() docstring
  // for why there's no AIPoC equivalent. Used for the history page's
  // before/after compare when revisiting a past job.
  function sourceImageUrl(jobId) {
    return `${BASE_URL}/api/photo-agent/source/${encodeURIComponent(jobId)}/image`;
  }

  // Small JPEG counterpart of sourceImageUrl -- used for the history grid's
  // still-processing tiles instead of downloading the full-res upload.
  function sourceThumbnailUrl(jobId) {
    return `${BASE_URL}/api/photo-agent/source/${encodeURIComponent(jobId)}/thumbnail`;
  }

  // ── Two-stage local flow: analyze now, review, /continue later ─────────
  // (AIPoC's own two-stage equivalent is analyzeAipoc() below + the existing
  // submitJob/pollJobStatus/getJobResult with path="aipoc" for stage 2.)

  async function analyzeJob({ file, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, userPrompt, advancedOptions }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("analysis_model", analysisModel || "gpt-4.1-mini");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("advanced_options", JSON.stringify(advancedOptions || {}));
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/analyze`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `送出分析失敗：HTTP ${response.status}`);
    }
    return data.job_id;
  }

  async function analyzeJobBatch({ files, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, userPrompt, advancedOptions }) {
    const form = new FormData();
    files.forEach((file) => form.append("image", file, file.name || "photo.jpg"));
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("analysis_model", analysisModel || "gpt-4.1-mini");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("advanced_options", JSON.stringify(advancedOptions || {}));
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/analyze-batch`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `送出分析失敗：HTTP ${response.status}`);
    }
    // Entries align 1:1 with files by order; each has job_id or an error.
    return data.jobs || [];
  }

  async function getAnalyzeResult(jobId) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/analyze/result/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== "success") {
      throw new Error(`${data.error || `取得分析結果失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data; // { status, result: { analysis } }
  }

  async function continueJob(jobId, { analysis, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, user, actionId }) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/continue/${encodeURIComponent(jobId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analysis,
        preset,
        image_quality: imageQuality,
        image_model: imageModel || "gpt-image-2",
        analysis_model: analysisModel || "gpt-4.1-mini",
        aspect_ratio: aspectRatio || "auto",
        ai_photo_editing: aiPhotoEditing ? "true" : "false",
        run_qa: runQa === false ? "false" : "true",
        user_prompt: userPrompt || "",
        overwrite_prompt: !!overwritePrompt,
        user: user || "",
        // Groups every job submitted from ONE "Run" click (runSingleItem/
        // editBatchItem submitting N separate continueJob calls for a
        // runCount>1 revise) into the same history entry instead of each
        // overwriting the last -- see backend/services/account.py's
        // action_id grouping. Absent for a single-RUN continue, which just
        // groups under its own new job id.
        action_id: actionId || ""
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`${data.error || `送出後續處理失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data.job_id;
  }

  // continueJob's sibling for an imported result folder: same stage="finish"
  // job, but the photo rides along with the request because there is no
  // earlier job holding it.
  async function rerunJob({ file, analysis, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, overwritePrompt, user, actionId }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("analysis", JSON.stringify(analysis));
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("analysis_model", analysisModel || "gpt-4.1-mini");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("run_qa", runQa === false ? "false" : "true");
    if (userPrompt) form.append("user_prompt", userPrompt);
    form.append("overwrite_prompt", overwritePrompt ? "true" : "false");
    // Same history bookkeeping continueJob does -- a rerun is a real edit and
    // belongs in the user's history like any other, and actionId groups the N
    // parallel RUNs of one runCount>1 click into a single entry.
    form.append("user", user || "");
    form.append("action_id", actionId || "");

    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/rerun`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `送出重跑失敗：HTTP ${response.status}`);
    }
    return data.job_id;
  }

  async function analyzeAipoc({ file, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, userPrompt, advancedOptions }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("analysis_model", analysisModel || "gpt-4.1-mini");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("advanced_options", JSON.stringify(advancedOptions || {}));
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/aipoc/analyze`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== "success") {
      throw new Error(data.error || `AIPoC 分析失敗：HTTP ${response.status}`);
    }
    return data; // { status, analysis }
  }

  async function analyzeAipocBatch({ files, preset, imageQuality, imageModel, analysisModel, aspectRatio, aiPhotoEditing, userPrompt, advancedOptions }) {
    const form = new FormData();
    files.forEach((file) => form.append("image", file, file.name || "photo.jpg"));
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("analysis_model", analysisModel || "gpt-4.1-mini");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("advanced_options", JSON.stringify(advancedOptions || {}));
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/aipoc/analyze-batch`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `AIPoC 分析失敗：HTTP ${response.status}`);
    }
    // Entries align 1:1 with files by order; each has analysis (inline) or error.
    return data.jobs || [];
  }

  // Demo photos bundled server-side (./demo_images) -- lets a user try the
  // app without a photo of their own. Falls back to [] on any failure, same
  // reasoning as getUiConfig() below: a demo-picker glitch shouldn't block
  // the rest of the upload page.
  async function getDemoImages() {
    try {
      const response = await fetch(`${BASE_URL}/api/photo-agent/demo-images`);
      if (!response.ok) return [];
      const data = await response.json().catch(() => ({}));
      return data.images || [];
    } catch {
      return [];
    }
  }

  function demoImageUrl(filename) {
    return `${BASE_URL}/api/photo-agent/demo-images/${encodeURIComponent(filename)}`;
  }

  // Small JPEG counterpart of demoImageUrl -- used for the picker grid's
  // tiles (some demo photos are multi-MB, and a couple are HEIC, which no
  // browser can render in a plain <img>). Selecting a tile still fetches the
  // real file via demoImageUrl, so the actual upload/analysis always runs
  // against the full-resolution source, never this thumbnail.
  function demoImageThumbnailUrl(filename) {
    return `${BASE_URL}/api/photo-agent/demo-images/${encodeURIComponent(filename)}/thumbnail`;
  }

  // Server-side UI flags (e.g. `--debug-ui` on run_backend.py). Falls back to
  // {} on any failure so a missing/unreachable backend never blocks the page.
  async function getUiConfig() {
    try {
      const response = await fetch(`${BASE_URL}/api/config`);
      if (!response.ok) return {};
      return await response.json();
    } catch {
      return {};
    }
  }

  // Decrypts a ?user_id= ciphertext (account.js's fallback when a page is
  // linked in without a plain ?user=NAME) via backend/services/decrypt_user_id.py --
  // the AES key/IV live server-side only, so this can't be done in-browser.
  async function resolveUserId(userId) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/decrypt-user-id?user_id=${encodeURIComponent(userId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `解析帳號失敗：HTTP ${response.status}`);
    }
    return data.user; // decrypted username
  }

  // ── Account / processing history (account.js owns the client-side identity;
  // these just talk to backend/services/account.py's two endpoints) ────────
  async function createAccount(user) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/account`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `建立帳號失敗：HTTP ${response.status}`);
    }
    return data; // { user, created }
  }

  async function getHistory(user) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/history/${encodeURIComponent(user)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `取得處理紀錄失敗：HTTP ${response.status}`);
    }
    return data; // { user, jobs: [{ job_id, path, ts, preset, image_key }] }
  }

  // imageKey (job.image_key from getHistory) identifies which entry to
  // remove -- it's an S3 key path (contains "/"), so it's a query param
  // rather than a URL path segment.
  async function deleteHistoryEntry(user, imageKey) {
    const response = await fetchWithRetry(
      `${BASE_URL}/api/photo-agent/history/${encodeURIComponent(user)}?image_key=${encodeURIComponent(imageKey)}`,
      { method: "DELETE" }
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `刪除紀錄失敗：HTTP ${response.status}`);
    }
    return data;
  }

  // Clears every entry in one request instead of looping deleteHistoryEntry
  // once per job.image_key -- see backend/routes/api.py's delete_all_history.
  async function clearHistory(user) {
    const response = await fetchWithRetry(
      `${BASE_URL}/api/photo-agent/history/${encodeURIComponent(user)}/all`,
      { method: "DELETE" }
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `刪除所有紀錄失敗：HTTP ${response.status}`);
    }
    return data;
  }

  // Flags job_id (whatever RUN/photo is currently on screen) as needing
  // review, attributed to the given name -- backend/services/report.py.
  // jobId is optional -- #report-btn (app.js) is always clickable even with
  // no result on screen, for a general "something's wrong" report with
  // nothing to point at. Re-reporting the same job by the same name is a
  // no-op refresh, not an error, so this never needs to check "did I
  // already report this" first (a job-less report has no such dedup key at
  // all -- see record_report). `note` is the reporter's optional free-text
  // description of the issue (report-modal, app.js); `userPrompt` is the
  // 補充指令 text the original submission actually used
  // (lastSingleSelections.userPrompt, app.js) -- there's nowhere
  // server-side this could be looked up from otherwise, so the frontend has
  // to hand it over at report time. `screenshotBlob` is
  // captureReportScreenshot's own best-effort capture of whatever's
  // currently visible (app.js) -- null whenever that failed, in which case
  // this just omits the field like every other optional one here. FormData
  // (not JSON) is required once a Blob is in the mix; backend/routes/api.py's
  // report_job already falls back to request.form for a non-JSON body, so
  // this needs no change on that side beyond reading request.files too.
  async function reportJob(user, jobId, path = "local", note = "", userPrompt = "", screenshotBlob = null) {
    const form = new FormData();
    form.append("user", user);
    if (jobId) form.append("job_id", jobId);
    form.append("path", path);
    if (note) form.append("note", note);
    if (userPrompt) form.append("user_prompt", userPrompt);
    if (screenshotBlob) form.append("screenshot", screenshotBlob, "report-screenshot.png");

    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/report`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `回報失敗：HTTP ${response.status}`);
    }
    return data; // { reported: true, report_id }
  }

  // Every flagged job across every user -- backend 403s unless `user` is a
  // recognized admin account (backend/services/report.py's is_admin), which
  // checkAdminAccess (app.js) also relies on as its "am I admin" check.
  async function getReports(user) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/reports/${encodeURIComponent(user)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(data.error || `取得回報清單失敗：HTTP ${response.status}`);
      err.status = response.status;
      throw err;
    }
    return data; // { reports: [{ report_id, job_id, user, path, ts, status, note?, user_prompt?, has_screenshot }] }
  }

  // Admin-only, same is_admin gate the backend applies to getReports above
  // -- sets a report's triage status ("unread"/"read"/"solved").
  async function updateReportStatus(user, reportId, status) {
    const response = await fetchWithRetry(
      `${BASE_URL}/api/photo-agent/reports/${encodeURIComponent(user)}/${encodeURIComponent(reportId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status })
      }
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `更新回報狀態失敗：HTTP ${response.status}`);
    }
    return data; // { report: {...} }
  }

  // Admin-only, same is_admin gate the backend applies to getReports above
  // -- removes just this report entry, not the job/result it points to.
  async function deleteReport(user, reportId) {
    const response = await fetchWithRetry(
      `${BASE_URL}/api/photo-agent/reports/${encodeURIComponent(user)}/${encodeURIComponent(reportId)}`,
      { method: "DELETE" }
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `刪除回報失敗：HTTP ${response.status}`);
    }
    return data;
  }

  // Same-origin URL for a report's screenshot (see backend/routes/api.py's
  // report_screenshot) -- used as an <img> src in the admin reports drawer,
  // shown only when that report's has_screenshot is true. Keyed by
  // report_id (not job_id/user) since a job-less report has no job_id at
  // all -- see record_report, backend/services/report.py.
  function reportScreenshotUrl(reportId) {
    return `${BASE_URL}/api/photo-agent/reports/screenshot/${encodeURIComponent(reportId)}`;
  }

  // One-shot status check (unlike pollJobStatus, which loops until done) --
  // used by the history page to render each past job's current state without
  // starting a whole poll loop for it.
  async function getJobStatusOnce(jobId, { path = "local" } = {}) {
    const response = await fetch(`${BASE_URL}${pathPrefix(path)}/status/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `查詢狀態失敗：HTTP ${response.status}`);
    }
    return data;
  }

  return {
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
    resolveUserId,
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
  };
})();
