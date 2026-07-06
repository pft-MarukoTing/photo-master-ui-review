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

  return { submitJob, pollJobStatus, getJobResult };
})();
