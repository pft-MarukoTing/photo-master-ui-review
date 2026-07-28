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

  async function submitJob({ file, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt, analysis, runCount, path = "local" }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    form.append("run_qa", runQa === false ? "false" : "true");
    if (userPrompt) form.append("user_prompt", userPrompt);
    // AIPoC-only: the analysis already shown on the preview screen, so the
    // remote worker can reuse it (stage="finish") instead of re-analyzing.
    if (analysis) form.append("analysis", JSON.stringify(analysis));
    // Local path only: how many independent edit variations job_processor.py
    // generates from ONE shared analysis, entirely within this single job.
    if (runCount) form.append("run_count", String(runCount));

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

  async function pollJobStatus(jobId, { path = "local", intervalMs = 1500, timeoutMs = 5 * 60 * 1000, finalCheckMs = 1 * 60 * 1000, onStatus } = {}) {
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

  // ── Two-stage local flow: analyze now, review, /continue later ─────────
  // (AIPoC's own two-stage equivalent is analyzeAipoc() below + the existing
  // submitJob/pollJobStatus/getJobResult with path="aipoc" for stage 2.)

  async function analyzeJob({ file, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, userPrompt }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
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

  async function analyzeJobBatch({ files, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, userPrompt }) {
    const form = new FormData();
    files.forEach((file) => form.append("image", file, file.name || "photo.jpg"));
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
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

  async function continueJob(jobId, { analysis, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, runQa, userPrompt }) {
    const response = await fetchWithRetry(`${BASE_URL}/api/photo-agent/continue/${encodeURIComponent(jobId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analysis,
        preset,
        image_quality: imageQuality,
        image_model: imageModel || "gpt-image-2",
        aspect_ratio: aspectRatio || "auto",
        ai_photo_editing: aiPhotoEditing ? "true" : "false",
        run_qa: runQa === false ? "false" : "true",
        user_prompt: userPrompt || ""
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`${data.error || `送出後續處理失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data.job_id;
  }

  async function analyzeAipoc({ file, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, userPrompt }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
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

  async function analyzeAipocBatch({ files, preset, imageQuality, imageModel, aspectRatio, aiPhotoEditing, userPrompt }) {
    const form = new FormData();
    files.forEach((file) => form.append("image", file, file.name || "photo.jpg"));
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("image_model", imageModel || "gpt-image-2");
    form.append("aspect_ratio", aspectRatio || "auto");
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
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

  return {
    submitJob,
    pollJobStatus,
    getJobResult,
    getJobRuns,
    resultImageUrl,
    getUiConfig,
    analyzeJob,
    analyzeJobBatch,
    getAnalyzeResult,
    continueJob,
    analyzeAipoc,
    analyzeAipocBatch
  };
})();
