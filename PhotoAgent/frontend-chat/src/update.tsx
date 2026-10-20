// The old UI's update-check.js for the chat page: poll /api/version and show
// the same banner once the frontend or worker build differs from the one
// this tab loaded with. Unlike the old UI there is no need to hold the
// banner back while a job runs -- a chat's jobs live in its server-side
// session, so a reload just reopens it.
import { useEffect, useState } from "preact/hooks";
import { BASE } from "./api";

const POLL_INTERVAL_MS = 30 * 1000;

const MESSAGES = {
  frontend: "網站有新版本，請重新整理頁面以取得最新內容。",
  worker: "後端處理伺服器已更新，執行中的工作可能已中斷，請立即重新整理頁面。",
};

type Kind = keyof typeof MESSAGES;
type BuildVersion = { frontend: string; worker: string | null };

export function UpdateBanner() {
  const [kind, setKind] = useState<Kind | null>(null);

  useEffect(() => {
    let baseline: BuildVersion | null = null;
    let done = false;

    async function check() {
      if (done) return;
      let data: BuildVersion;
      try {
        const res = await fetch(`${BASE}/api/version`, { cache: "no-store" });
        if (!res.ok) return;
        data = await res.json();
      } catch {
        return; // transient network error -- wait for the next poll
      }
      if (!baseline) {
        baseline = data;
        return;
      }
      // A null worker just means Redis was briefly unreachable, not a restart.
      const workerChanged = baseline.worker !== null && data.worker !== null && data.worker !== baseline.worker;
      // Worker first: it is the more urgent case.
      const next: Kind | null = workerChanged ? "worker" : data.frontend !== baseline.frontend ? "frontend" : null;
      if (next) {
        done = true;
        clearInterval(timer);
        setKind(next);
      }
    }

    const onVisible = () => document.visibilityState === "visible" && check();
    check();
    const timer = setInterval(check, POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (!kind) return null;
  return (
    <div id="update-banner" class="update-banner">
      <span id="update-banner-text" class="update-banner-text">
        {MESSAGES[kind]}
      </span>
      <button type="button" id="update-banner-refresh" class="update-banner-refresh" onClick={() => location.reload()}>
        重新整理
      </button>
    </div>
  );
}
