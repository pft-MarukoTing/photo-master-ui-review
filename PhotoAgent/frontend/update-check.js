// Polls /api/version and shows a persistent banner once the server reports a
// different frontend or worker build than the one this page loaded with --
// frontend/ is served straight off disk with no cache-busted build hash, and
// the docker worker is a separate process/container from this Flask app, so
// neither kind of update is otherwise visible to an already-open tab.
window.PhotoAgentUpdateCheck = (() => {
  const BASE_URL = window.PhotoAgentConfig.apiBaseUrl || window.location.pathname.replace(/\/[^/]*$/, "");
  const POLL_INTERVAL_MS = 30 * 1000;
  // Consecutive mismatched polls required before treating it as a real
  // update. A Redis blip while the worker container restarts can make its
  // version key briefly disappear, which would otherwise misread as "a new
  // update" during a plain worker restart -- raise this above 1 to require
  // it to stick across multiple polls before alerting.
  const MISMATCH_THRESHOLD = 1;

  const MESSAGES = {
    frontend: tr("網站有新版本，請重新整理頁面以取得最新內容。"),
    // A destroyed worker container orphans any job it had already pulled off
    // Redis (see job_processor.py/queue.py -- there's no requeue-on-crash),
    // so a job the user is still waiting on will never finish. Worded more
    // urgently than the frontend-only case, and shown immediately instead of
    // waiting for that job to "complete" -- it can't.
    worker: tr("後端處理伺服器已更新，執行中的工作可能已中斷，請立即重新整理頁面。")
  };

  let baselineVersion = null;
  let frontendMismatchStreak = 0;
  let workerMismatchStreak = 0;
  let pollTimer = null;

  // jobId only ever lives in an in-memory JS variable (app.js) -- refreshing
  // mid-job abandons it with no way to recover the result even if the job
  // itself finishes server-side. A frontend-only update is harmless to keep
  // running on, so that case defers the banner until no job is in flight
  // (tracked here via photo-agent-api.js's pollJobStatus calling
  // beginJob/endJob). A worker update skips this deferral entirely --
  // waiting doesn't help when the job in flight is already lost.
  let activeJobs = 0;
  let pendingKind = null; // "frontend" | "worker" | null

  function showBanner(kind) {
    pendingKind = null;
    clearInterval(pollTimer);
    pollTimer = null;
    const banner = document.querySelector("#update-banner");
    const text = document.querySelector("#update-banner-text");
    if (text) text.textContent = MESSAGES[kind] || MESSAGES.frontend;
    if (banner) banner.hidden = false;
  }

  function requestShow(kind) {
    if (kind === "worker" || activeJobs === 0) {
      showBanner(kind);
    } else {
      pendingKind = kind;
    }
  }

  function beginJob() {
    activeJobs += 1;
  }

  function endJob() {
    activeJobs = Math.max(0, activeJobs - 1);
    if (pendingKind && activeJobs === 0) {
      showBanner(pendingKind);
    }
  }

  async function checkVersion() {
    let data;
    try {
      const response = await fetch(`${BASE_URL}/api/version`, { cache: "no-store" });
      if (!response.ok) return;
      data = await response.json();
    } catch (error) {
      return; // transient network error -- just wait for the next poll
    }

    if (!baselineVersion) {
      baselineVersion = data;
      return;
    }

    const frontendChanged = data.frontend !== baselineVersion.frontend;
    // Only count a worker change once we actually had a confirmed baseline
    // AND a confirmed new value to compare -- either side being null just
    // means Redis was briefly unreachable, not that the worker restarted.
    const workerChanged = baselineVersion.worker !== null && data.worker !== null
      && data.worker !== baselineVersion.worker;

    frontendMismatchStreak = frontendChanged ? frontendMismatchStreak + 1 : 0;
    workerMismatchStreak = workerChanged ? workerMismatchStreak + 1 : 0;

    // Worker takes priority: it's the more urgent, less recoverable case, so
    // a deploy that touches both at once shouldn't show the softer
    // frontend-only message instead.
    if (workerMismatchStreak >= MISMATCH_THRESHOLD) {
      requestShow("worker");
    } else if (frontendMismatchStreak >= MISMATCH_THRESHOLD) {
      requestShow("frontend");
    }
  }

  function start() {
    checkVersion();
    pollTimer = setInterval(checkVersion, POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") checkVersion();
    });
    document.querySelector("#update-banner-refresh")?.addEventListener("click", () => window.location.reload());
  }

  return { start, beginJob, endJob };
})();

window.PhotoAgentUpdateCheck.start();
