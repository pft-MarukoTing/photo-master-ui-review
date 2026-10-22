// 管理者的回報清單，聊天頁版：header 的「回報清單」打開抽屜，點一筆聊天模式的回報就在
// 同一個分頁換成那段對話的唯讀檢視（#report=<id>），抽屜隨時能再打開換下一筆，「回到
// 聊天」回到進來前的那段對話。清單、狀態跟舊版的回報抽屜是同一支 API、同一份資料。
// 面板模式送的回報，那張照片有對話的話一樣打開對話（回報當下的面板畫面從唯讀檢視標頭
// 的「面板模式」切過去看）；沒有對話的照樣列出來，只是不能點。
import { createPortal } from "preact/compat";
import { useEffect, useState } from "preact/hooks";
import { api, getUser, setUser, urls, type ReportEntry, type ReportStatus } from "./api";
import { resolveUser } from "./identity";
import { Icon } from "./icons";
import { clockLabel } from "./util";

// ── 路由：#report=<id> 是唯讀檢視，其他照舊是聊天頁 ─────────────────────────
const RETURN_KEY = "photomaster-chat-report-return";
const ROUTE_EVENT = "chat-route";

export function reportIdFromHash(): string | null {
  const m = /(?:^#|&)report=([^&]+)/.exec(location.hash);
  return m ? decodeURIComponent(m[1]) : null;
}

function go(hash: string, replace: boolean) {
  const url = `${location.pathname}${location.search}${hash}`;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

// 從聊天頁進來時記下原本的 #session=…，「回到聊天」就回那段；已經在看回報時換一筆
// 不多疊一層上一頁。
export function openReport(reportId: string) {
  const from = reportIdFromHash();
  if (!from) {
    try {
      sessionStorage.setItem(RETURN_KEY, location.hash);
    } catch {
      /* 記不住就回首頁 */
    }
  }
  go(`#report=${encodeURIComponent(reportId)}`, !!from);
}

export function leaveReport() {
  let back = "";
  try {
    back = sessionStorage.getItem(RETURN_KEY) || "";
    sessionStorage.removeItem(RETURN_KEY);
  } catch {
    /* 回首頁 */
  }
  go(back, true);
}

export function useReportRoute(): string | null {
  const [id, setId] = useState(reportIdFromHash);
  useEffect(() => {
    const sync = () => setId(reportIdFromHash());
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    window.addEventListener(ROUTE_EVENT, sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
      window.removeEventListener(ROUTE_EVENT, sync);
    };
  }, []);
  return id;
}

// ── 是不是管理者：沒有專門的 API，讀得到回報清單就是（舊版 checkAdminAccess 同一招）──
let adminCheck: Promise<boolean> | null = null;
function isAdmin(): Promise<boolean> {
  adminCheck ||= (async () => {
    try {
      if (!getUser()) setUser(await resolveUser());
      await api.reports(0, 1);
      return true;
    } catch {
      return false;
    }
  })();
  return adminCheck;
}

function useIsAdmin() {
  const [admin, setAdmin] = useState(false);
  useEffect(() => {
    let alive = true;
    isAdmin().then((ok) => alive && setAdmin(ok));
    return () => {
      alive = false;
    };
  }, []);
  return admin;
}

// ── 狀態：未讀／待解決／已解決，跟舊版同一組。點了只在待解決和已解決之間切；打開
// 一筆未讀的就算讀過了。 ───────────────────────────────────────────────────
const STATUS_LABELS: Record<ReportStatus, string> = { unread: "未讀", read: "待解決", solved: "已解決" };
const nextStatus = (s: ReportStatus): ReportStatus => (s === "solved" ? "read" : "solved");

export async function saveStatus(r: ReportEntry, status: ReportStatus, apply: (s: ReportStatus) => void) {
  const before = r.status || "unread";
  r.status = status;
  apply(status);
  try {
    await api.setReportStatus(r.report_id, status);
  } catch {
    r.status = before;
    apply(before);
  }
}

export function StatusPill({ report, class: cls }: { report: ReportEntry; class?: string }) {
  const [status, setStatus] = useState<ReportStatus>(report.status || "unread");
  useEffect(() => setStatus(report.status || "unread"), [report]);
  return (
    <button
      type="button"
      class={`report-item-status status-${status}${cls ? ` ${cls}` : ""}`}
      aria-label={`回報狀態：${STATUS_LABELS[status]}（點擊切換）`}
      onClick={(e) => {
        e.stopPropagation();
        saveStatus(report, nextStatus(status), setStatus);
      }}
    >
      {STATUS_LABELS[status]}
    </button>
  );
}

// 記得是哪段對話的回報才打得開：聊天模式送的，或面板模式送、那張照片有對話的。
// 在首頁、上傳頁送的沒有對話，打開看的是當時頁面的樣子。
const opensHere = (r: ReportEntry) => !!r.page || (!!r.owner && !!(r.session_id || r.batch_id));

function when(ts: number) {
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${clockLabel(ts)}`;
}

function ReportRow({ r, current, onOpen }: { r: ReportEntry; current: boolean; onOpen: (r: ReportEntry) => void }) {
  const open = opensHere(r);
  const page = r.page === "home" ? " · 首頁" : r.page === "upload" ? " · 上傳頁" : "";
  const where = r.source === "chat" ? `聊天模式${page}` : r.path === "aipoc" ? "AIPoC" : "面板模式";
  const prompt = (r.version_params?.user_prompt as string | undefined) || r.user_prompt;
  // 跟面板模式的回報清單同一套挑法（renderReportsList, app.js）：只做了分析的 job 用原圖，
  // 有結果的用結果圖，沒有 job 的一般回報用後端從對話挑的那張。AIPoC 那條路沒有縮圖可用。
  const src =
    r.path !== "local" ? ""
    : r.analyze_job_id ? urls.sourceThumb(r.analyze_job_id)
    : r.job_id ? urls.resultThumb(r.job_id)
    : !r.thumbnail ? ""
    : "src" in r.thumbnail ? r.thumbnail.src
    : r.thumbnail.run ? urls.resultThumb(r.thumbnail.job_id, r.thumbnail.run)
    : urls.sourceThumb(r.thumbnail.job_id);
  const [broken, setBroken] = useState<string | null>(null);
  return (
    <li
      class={`report-item${open ? "" : " is-static"}${current ? " is-current" : ""}`}
      aria-current={current ? "true" : undefined}
      onClick={open ? () => onOpen(r) : undefined}
    >
      <div class="report-item-thumb">
        {src && src !== broken && <img alt="" src={src} loading="lazy" onError={() => setBroken(src)} />}
      </div>
      <div class="report-item-info">
        <span class="report-item-user">{r.owner || r.user}</span>
        <span class="report-item-meta">{`${when(r.ts)} · ${r.job_id ? where : `一般回報 · ${where}`}`}</span>
        {r.owner && r.owner !== r.user && <span class="report-item-note">{`回報人：${r.user}`}</span>}
        {prompt && <span class="report-item-note">{`個人化需求：${prompt}`}</span>}
        {r.note && <span class="report-item-note">{`回報說明：${r.note}`}</span>}
        {!open && <span class="report-item-note is-muted">{r.source === "chat" ? "沒有記到對話，打不開" : "這張照片沒有對話，到面板模式的回報清單看"}</span>}
        {open && r.source !== "chat" && <span class="report-item-note is-muted">在面板模式回報的，打開後按「面板模式」看回報當下的畫面</span>}
      </div>
      <StatusPill report={r} />
    </li>
  );
}

function ReportsDrawer(props: { open: boolean; currentId: string | null; onClose: () => void; onOpen: (r: ReportEntry) => void }) {
  const [rows, setRows] = useState<ReportEntry[] | null>(null);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const load = async (reset: boolean) => {
    setLoading(true);
    setFailed(false);
    try {
      const page = await api.reports(reset ? 0 : rows?.length || 0);
      setRows((prev) => (reset ? page.reports : [...(prev || []), ...page.reports]));
      setMore(page.has_more);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  };
  // 每次打開都重讀第一頁：別的管理者或舊版那邊可能剛改過狀態、剛有新的回報。
  useEffect(() => {
    if (props.open) load(true);
  }, [props.open]);
  useEffect(() => {
    if (!props.open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [props.open]);
  return createPortal(
    <>
      <div class={`history-drawer-backdrop${props.open ? " drawer-open" : ""}`} onClick={props.onClose} />
      <aside class={`history-drawer reports-drawer${props.open ? " drawer-open" : ""}`} role="dialog" aria-label="回報清單">
        <button class="history-drawer-close-btn" type="button" aria-label="關閉" onClick={props.onClose}>
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24">
            <path d="M18 6l-4 4m0 0h4m-4 0V6M6 18l4-4m0 0H6m4 0v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <div class="history-header">
          <div class="history-header-top">
            <h2>回報清單</h2>
            <button class="btn btn-outline btn-sm reports-refresh-btn" type="button" aria-label="重新載入" title="重新載入" disabled={loading} onClick={() => load(true)}>
              <Icon name="redo" size={14} />
            </button>
          </div>
        </div>
        <div class="history-list reports-scroll">
          {rows && rows.length > 0 && (
            <ul class="reports-list">
              {rows.map((r) => (
                <ReportRow key={r.report_id} r={r} current={r.report_id === props.currentId} onOpen={props.onOpen} />
              ))}
            </ul>
          )}
          {rows && !rows.length && !failed && <p class="history-empty">目前沒有回報。</p>}
          {!rows && loading && <p class="history-empty">正在讀取回報…</p>}
          {failed && <p class="history-empty">回報清單讀取失敗，請按重新載入再試一次。</p>}
          {more && !failed && (
            <button class="btn btn-outline btn-sm reports-more-btn" type="button" disabled={loading} onClick={() => load(false)}>
              {loading ? "讀取中…" : "載入更多"}
            </button>
          )}
        </div>
      </aside>
    </>,
    document.body,
  );
}

// header 上的「回報清單」：只有管理者看得到。beforeLeave 讓聊天頁先問「還沒開始美化的
// 照片會清掉，確定嗎？」，取消就留在原地。
export function ReportsButton(props: { currentId?: string | null; beforeLeave?: () => Promise<boolean> }) {
  const admin = useIsAdmin();
  const [open, setOpen] = useState(false);
  if (!admin) return null;
  const pick = async (r: ReportEntry) => {
    if (r.report_id === props.currentId) return setOpen(false);
    if (props.beforeLeave && !(await props.beforeLeave())) return;
    if ((r.status || "unread") === "unread") saveStatus(r, "read", () => {});
    setOpen(false);
    openReport(r.report_id);
  };
  return (
    <>
      <button id="chat-reports-btn" class="btn btn-secondary btn-sm" type="button" onClick={() => setOpen(true)}>
        <Icon name="inbox" size={14} />
        <span class="btn-label">回報清單</span>
      </button>
      <ReportsDrawer open={open} currentId={props.currentId || null} onClose={() => setOpen(false)} onOpen={pick} />
    </>
  );
}
