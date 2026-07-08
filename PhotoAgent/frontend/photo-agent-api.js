window.PhotoAgentApi = (() => {
  // When deployed behind a reverse-proxy prefix (e.g. /mt-testbed-PhotoAgent),
  // absolute "/api/..." fetches would bypass the proxy's location match and 404.
  // Derive the prefix from the page's own directory instead of hardcoding it,
  // so the same build works unprefixed (local) and prefixed (prod).
  const BASE_URL = window.PhotoAgentConfig.apiBaseUrl || window.location.pathname.replace(/\/[^/]*$/, "");

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // "local" -> /api/photo-agent/... (Redis queue + docker worker)
  // "aipoc" -> /api/photo-agent/aipoc/... (direct call to the shared AIPoC gateway)
  function pathPrefix(path) {
    return path === "aipoc" ? "/api/photo-agent/aipoc" : "/api/photo-agent";
  }

  async function submitJob({ file, preset, imageQuality, aiPhotoEditing, userPrompt, path = "local" }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetch(`${BASE_URL}${pathPrefix(path)}/submit`, {
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

  async function pollJobStatus(jobId, { path = "local", intervalMs = 1500, timeoutMs = 5 * 60 * 1000 } = {}) {
    const deadline = Date.now() + timeoutMs;
    // AIPoC task_ids contain "/" and "+" and must be percent-encoded, or the
    // request either 404s or gets misrouted.
    const url = `${BASE_URL}${pathPrefix(path)}/status/${encodeURIComponent(jobId)}`;
    while (Date.now() < deadline) {
      const response = await fetch(url);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(`${data.error || `查詢狀態失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
      }
      if (data.status === "success") return;
      if (data.status === "error") throw new Error(`${data.error || "AI 修圖失敗。"}（Job ID: ${jobId}）`);
      await sleep(intervalMs);
    }
    throw new Error(`處理逾時，請稍後再試。（Job ID: ${jobId}）`);
  }

  async function getJobResult(jobId, { path = "local" } = {}) {
    const response = await fetch(`${BASE_URL}${pathPrefix(path)}/result/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== "success") {
      throw new Error(`${data.error || `取得結果失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data; // { status, image_url, result: { analysis, quality_check, usage } }
  }

  // Same-origin URL for the result image (vs. the presigned S3/AIPoC URL
  // getJobResult returns, which has no CORS headers for fetch()). Used only
  // when the browser needs to read the image's bytes itself (e.g. bundling
  // it into a zip) -- for plain <img> display, the presigned URL is fine.
  function resultImageUrl(jobId, { path = "local" } = {}) {
    return `${BASE_URL}${pathPrefix(path)}/result/${encodeURIComponent(jobId)}/image`;
  }

  // ── Two-stage local flow: analyze now, review, /continue later ─────────
  // (AIPoC's own two-stage equivalent is analyzeAipoc() below + the existing
  // submitJob/pollJobStatus/getJobResult with path="aipoc" for stage 2.)

  async function analyzeJob({ file, preset, imageQuality, aiPhotoEditing, userPrompt }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetch(`${BASE_URL}/api/photo-agent/analyze`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `送出分析失敗：HTTP ${response.status}`);
    }
    return data.job_id;
  }

  async function getAnalyzeResult(jobId) {
    const response = await fetch(`${BASE_URL}/api/photo-agent/analyze/result/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== "success") {
      throw new Error(`${data.error || `取得分析結果失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data; // { status, result: { analysis } }
  }

  async function continueJob(jobId, { analysis, preset, imageQuality, aiPhotoEditing, userPrompt }) {
    const response = await fetch(`${BASE_URL}/api/photo-agent/continue/${encodeURIComponent(jobId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analysis,
        preset,
        image_quality: imageQuality,
        ai_photo_editing: aiPhotoEditing ? "true" : "false",
        user_prompt: userPrompt || ""
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`${data.error || `送出後續處理失敗：HTTP ${response.status}`}（Job ID: ${jobId}）`);
    }
    return data.job_id;
  }

  async function analyzeAipoc({ file, preset, imageQuality, aiPhotoEditing, userPrompt }) {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    form.append("preset", preset);
    form.append("image_quality", imageQuality);
    form.append("ai_photo_editing", aiPhotoEditing ? "true" : "false");
    if (userPrompt) form.append("user_prompt", userPrompt);

    const response = await fetch(`${BASE_URL}/api/photo-agent/aipoc/analyze`, {
      method: "POST",
      body: form
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== "success") {
      throw new Error(data.error || `AIPoC 分析失敗：HTTP ${response.status}`);
    }
    return data; // { status, analysis }
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
    resultImageUrl,
    getUiConfig,
    analyzeJob,
    getAnalyzeResult,
    continueJob,
    analyzeAipoc
  };
})();
