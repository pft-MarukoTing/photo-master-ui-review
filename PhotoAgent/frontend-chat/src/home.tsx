// Header, the home page's intro, the 最近對話 / 範例照片 gallery and the
// 最近照片 drawer -- old markup and classes throughout.
import type { ComponentChildren } from "preact";
import { createPortal } from "preact/compat";
import { useEffect, useState } from "preact/hooks";
import { api, getUser, urls } from "./api";
import heroAfter from "./assets/hero_after.jpg";
import heroBefore from "./assets/hero_before.jpg";
import { Icon } from "./icons";
import {
  applyTheme,
  currentTheme,
  nextTheme,
  THEME_LABELS,
  THEME_SWATCH_CLASS,
  type ThemeKey,
} from "./theme";
import type { SessionState, SessionSummary } from "./types";
import { dayLabel, timeLabel } from "./util";

// One click advances the fixed cycle (theme.ts) -- no picker, the way the old
// header does it. The swatch says which colour, the label names which of the
// six, so the button needs no separate noun for 主題; the title carries the
// affordance the label used to. At narrow widths styles.css hides .btn-label
// across .header-actions and this collapses to the swatch alone.
// Its own chat-* id, not the old #theme-toggle-btn: the old index.html <style>
// (inlined by vite.config.ts) turns that id into a 32px pill, which made it
// the odd one out next to 回到首頁 / 面板模式's plain .btn-sm.
function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeKey>(currentTheme);
  return (
    <button
      id="chat-theme-btn"
      class="btn btn-secondary btn-sm"
      type="button"
      title="點擊切換主題"
      aria-label={`主題：${THEME_LABELS[theme]}（點擊切換）`}
      onClick={() => {
        const next = nextTheme(theme);
        applyTheme(next);
        setTheme(next);
      }}
    >
      <span class={`theme-swatch theme-toggle-swatch ${THEME_SWATCH_CLASS[theme]}`} aria-hidden="true" />
      <span class="btn-label">{THEME_LABELS[theme]}</span>
    </button>
  );
}

// 回報, the old page's #report-modal markup and classes (its <style> comes
// along, see vite.config.ts). No 畫面截圖 row: that one is html2canvas on the
// old page's own DOM. Without a jobId it files a general report, the same as
// the old button pressed with nothing on screen.
function ReportModal(props: { jobId: string | null; onClose: () => void }) {
  const { onClose } = props;
  const [name, setName] = useState(getUser);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [onClose]);

  const submit = async () => {
    if (!name.trim()) {
      setError("請輸入你的名稱。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.report(name.trim(), props.jobId, note.trim());
      setDone(true);
    } catch (err) {
      setError((err as Error).message || "回報失敗，請稍後再試。");
    } finally {
      setBusy(false);
    }
  };

  // Portalled to <body>: .site-header's backdrop-filter would otherwise be the
  // containing block and stacking context for this fixed overlay.
  return createPortal(
    <div class="error-modal" id="chat-report-modal">
      <div class="error-modal-backdrop" onClick={onClose} />
      <div class="error-modal-box" role="dialog" aria-modal="true" aria-labelledby="chat-report-title">
        <div class="error-modal-hdr">
          <span id="chat-report-title" class="notice-modal-title">{props.jobId ? "回報此結果" : "回報問題"}</span>
          <button class="error-modal-close" type="button" aria-label="關閉" onClick={onClose}>
            ×
          </button>
        </div>
        {done ? (
          <>
            <p class="chat-text">已回報，我們會盡快確認{props.jobId ? "這筆結果" : ""}。</p>
            <div class="account-modal-actions">
              <button class="chat-chip" type="button" onClick={onClose}>
                關閉
              </button>
            </div>
          </>
        ) : (
          <>
            <label class="report-modal-label" for="chat-report-name">
              你的名稱
            </label>
            <input
              id="chat-report-name"
              class="account-modal-input"
              type="text"
              placeholder="輸入你的名稱"
              maxLength={64}
              value={name}
              onInput={(e) => setName(e.currentTarget.value)}
            />
            <label class="report-modal-label" for="chat-report-note">
              問題描述（選填）
            </label>
            <textarea
              id="chat-report-note"
              class="report-modal-textarea"
              placeholder={props.jobId ? "請描述這筆結果的問題…" : "請描述遇到的問題…"}
              maxLength={500}
              value={note}
              onInput={(e) => setNote(e.currentTarget.value)}
            />
            {error && <p class="account-modal-error">{error}</p>}
            <div class="account-modal-actions">
              <button class="chat-chip is-ghost" type="button" onClick={onClose}>
                取消
              </button>
              <button class="chat-chip is-primary" type="button" disabled={busy} onClick={submit}>
                {busy ? "送出中…" : "送出回報"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}

// 面板模式 #confirm-modal 的版型：一句話、兩個選項。Esc 跟點背景都算留下。
export function ConfirmModal(props: {
  message: string;
  confirmLabel: string;
  // 沒給就只有一顆確認鍵（像刪不掉時的那句提醒）。
  cancelLabel?: string;
  onAnswer: (ok: boolean) => void;
}) {
  const { onAnswer } = props;
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onAnswer(false);
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [onAnswer]);

  return createPortal(
    <div class="error-modal" id="chat-confirm-modal">
      <div class="error-modal-backdrop" onClick={() => onAnswer(false)} />
      <div class="error-modal-box" role="alertdialog" aria-modal="true" aria-labelledby="chat-confirm-title" aria-describedby="chat-confirm-msg">
        <div class="error-modal-hdr">
          <span id="chat-confirm-title" class="notice-modal-title">
            Photo<em>Master</em>
          </span>
        </div>
        <p id="chat-confirm-msg" class="error-modal-msg">
          {props.message}
        </p>
        <div class="account-modal-actions">
          {props.cancelLabel && (
            <button class="chat-chip is-ghost" type="button" autoFocus onClick={() => onAnswer(false)}>
              {props.cancelLabel}
            </button>
          )}
          <button class="chat-chip is-primary" type="button" onClick={() => onAnswer(true)}>
            {props.confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// Same order as 面板模式's header, so switching modes moves nothing:
// 回到首頁 · 最近照片 · mode switch · 主題 · 回報.
// showHistory is off while the gallery (with its 最近對話 tab) is on screen.
// showOldUi likewise: 面板模式 only takes over a photo's analysis/result, so
// the switch exists only once a session is open, and is held while the photo
// is analyzing or rendering (the other mode would open on a half-done job).
export function Header(props: {
  onHome: () => void;
  onHistory: () => void;
  showHome: boolean;
  showHistory?: boolean;
  showOldUi?: boolean;
  oldUiBusy?: boolean;
  // Right before leaving for 面板模式: hands this photo's adjustments over.
  onOldUi?: () => void;
  // The photo on screen, which 面板模式 then opens on.
  caseId?: string | null;
  // The edit (or, before one, the analysis) on screen, which 回報 points at.
  jobId?: string | null;
}) {
  const [reporting, setReporting] = useState(false);
  return (
    <header class="site-header">
      <div class="header-inner">
        <button type="button" class="logo" id="logo-home-btn" onClick={props.onHome}>
          Photo<em>Master</em>
          <span class="logo-env-badge" id="logo-env-badge" title="開發環境">DEV</span>
        </button>
        <div class="header-actions">
          {props.showHome && (
            <button id="chat-home-btn" class="btn btn-secondary btn-sm" type="button" onClick={props.onHome}>
              <Icon name="house" size={14} />
              <span class="btn-label">回到首頁</span>
            </button>
          )}
          {props.showHistory && (
            <button id="chat-history-btn" class="btn btn-secondary btn-sm" type="button" onClick={props.onHistory}>
              <Icon name="clock" size={14} />
              <span class="btn-label">最近照片</span>
            </button>
          )}
          {props.showOldUi &&
            (props.oldUiBusy ? (
              <button class="btn btn-secondary btn-sm" type="button" disabled title="分析或美化完成後才能切換">
                <Icon name="layout" size={14} />
                <span class="btn-label">面板模式</span>
              </button>
            ) : (
              <a class="btn btn-secondary btn-sm" href={urls.oldUi(props.caseId)} title="切換到面板模式" onClick={props.onOldUi}>
                <Icon name="layout" size={14} />
                <span class="btn-label">面板模式</span>
              </a>
            ))}
          <ThemeToggle />
          <button
            id="chat-report-btn"
            class="btn btn-sm"
            type="button"
            title={props.jobId ? "回報此結果" : "回報問題"}
            onClick={() => setReporting(true)}
          >
            <Icon name="report" size={14} />
            <span class="btn-label">回報</span>
          </button>
        </div>
      </div>
      {reporting && <ReportModal jobId={props.jobId || null} onClose={() => setReporting(false)} />}
    </header>
  );
}

// Generated sample portrait (slightly messy hair, not smiling) and its 一鍵美化
// result, aligned and cropped to the same 4:5 frame so the slider lines up.
function HeroCompare() {
  const [pos, setPos] = useState(25);
  return (
    <div class="home-compare" style={{ "--pos": `${pos}%` }}>
      <img class="home-compare-img" src={heroBefore} alt="修圖前" />
      <img class="home-compare-img is-after" src={heroAfter} alt="修圖後" />
      <span class="home-compare-line" aria-hidden="true">
        <span class="home-compare-knob">
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 6l-6 6 6 6M15 6l6 6-6 6" />
          </svg>
        </span>
      </span>
      <span class="home-compare-tag">Before</span>
      <span class="home-compare-tag is-after">After</span>
      <input
        class="home-compare-range"
        type="range"
        min={0}
        max={100}
        value={pos}
        aria-label="拖曳比較修圖前後"
        onInput={(e) => setPos(Number(e.currentTarget.value))}
      />
    </div>
  );
}

export function HomeIntro({ onStart }: { onStart: () => void }) {
  return (
    <div class="home-intro">
      <div class="home-hero">
        <div class="home-hero-copy">
          <p class="home-eyebrow">AI 個人化美化分析</p>
          <h1 class="home-headline">
            <span>一張照片，</span>
            <span>
              開啟<em>專屬</em>美化體驗
            </span>
          </h1>
          <p class="home-lead">
            AI 先判讀人物、光線、構圖與畫面細節，給你幾個美化方向，表情、姿勢、視角也能幫你調；你也可以直接說想怎麼修，它就照你的意思來。
          </p>
          <div class="home-start">
            <button type="button" class="btn btn-primary home-start-btn" onClick={onStart}>
              <Icon name="chat" />
              開始對話
            </button>
            <span class="chat-meta">選一張照片，剩下的用聊的</span>
          </div>
        </div>
        <HeroCompare />
      </div>
    </div>
  );
}

const TILE_CHECK = (
  <span class="demo-image-tile-check">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
      <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </span>
);

function Tile(props: {
  src: string;
  title: string;
  selected: boolean;
  onClick: () => void;
  onImgError?: () => void;
  children?: ComponentChildren;
}) {
  return (
    <button
      type="button"
      class={`demo-image-tile${props.selected ? " is-selected" : ""}`}
      title={props.title}
      onClick={props.onClick}
    >
      <img class="demo-image-tile-bg" src={props.src} alt="" loading="lazy" />
      <img src={props.src} alt={props.title} loading="lazy" onError={props.onImgError} />
      {TILE_CHECK}
      {props.children}
    </button>
  );
}

// Same words and glyphs the old UI's history grid uses, so a conversation
// reads the same in both. A session is many photos, not one job, so the
// state is the whole conversation's: anything still running wins over a
// result, and 失敗 only when nothing in it survived.
const SESSION_BADGES: Record<SessionState, string> = {
  analyzing: "分析中",
  rendering: "美化中",
  ready: "待美化",
  done: "美化完成",
  failed: "失敗",
};

// 每格右上角的 ×，跟面板模式最近照片那顆同一個樣子（.history-cube-remove）。磚本身是
// <button>，裡面不能再放一顆按鈕，所以這顆用 span 當按鈕，點了不往外傳。
function RemoveMark({ onRemove }: { onRemove: () => void }) {
  const go = (e: Event) => {
    e.stopPropagation();
    e.preventDefault();
    onRemove();
  };
  return (
    <span
      class="history-cube-remove chat-tile-remove"
      role="button"
      tabIndex={0}
      aria-label="刪除紀錄"
      title="刪除紀錄"
      onClick={go}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && go(e)}
    >
      ×
    </span>
  );
}

export function SessionTile(props: { s: SessionSummary; selected: boolean; onClick: () => void; onRemove?: () => void }) {
  const { s } = props;
  // A result can outlive nothing else here, but its thumbnail is generated
  // on demand from job artifacts the session (30 days) can outlast -- so a
  // result that will not load falls back to the original rather than
  // leaving the tile blank. Keyed by job id so the next render of a
  // different session in this slot starts over.
  const [brokenJob, setBrokenJob] = useState<string | null>(null);
  const result = s.thumbnail_result;
  const showResult = result && result.job_id !== brokenJob;
  const src = showResult
    ? urls.resultThumb(result.job_id, result.run)
    : s.thumbnail_job_id
      ? urls.sourceThumb(s.thumbnail_job_id)
      : "";
  const label = s.status === "done" && s.photo_count > 1
    ? `${SESSION_BADGES.done}（${s.rendered_count}/${s.photo_count}）`
    : SESSION_BADGES[s.status];
  return (
    <Tile
      src={src}
      title={timeLabel(s.updated_at)}
      selected={props.selected}
      onClick={props.onClick}
      onImgError={showResult ? () => setBrokenJob(result.job_id) : undefined}
    >
      {label && <span class={`session-tile-badge is-${s.status}`}>{label}</span>}
      {props.onRemove && <RemoveMark onRemove={props.onRemove} />}
    </Tile>
  );
}

// What a row is picked by: its session, or for a history case with none,
// `case:<case_id>` -- openSession tells the two apart.
export const CASE_PREFIX = "case:";
export function rowId(s: SessionSummary): string {
  return s.session_id ?? `${CASE_PREFIX}${s.case_id}`;
}

// Sessions arrive newest first, so a day's run of them is contiguous: one
// group per calendar day, in that same order. The tiles carry no date of
// their own any more -- the heading is where the date lives, which is how
// the old UI's history list reads too (renderHistoryList, app.js).
function byDay(sessions: SessionSummary[]): { label: string; rows: SessionSummary[] }[] {
  const days: { label: string; rows: SessionSummary[] }[] = [];
  for (const s of sessions) {
    const label = dayLabel(s.updated_at);
    const open = days[days.length - 1];
    if (open && open.label === label) open.rows.push(s);
    else days.push({ label, rows: [s] });
  }
  return days;
}

// Each day gets its own grid rather than one grid with headings dropped in:
// the mosaic packs densely, and a shared grid would let tomorrow's photo
// backfill a hole today's left -- above its own heading. Separate grids also
// restart the enlarged-tile rhythm per day, as the old UI's .history-grid does.
function SessionDays(props: {
  sessions: SessionSummary[];
  gridClass: string;
  selectedId: string | null;
  onPick: (id: string) => void;
  // 多選刪除時，哪幾格是勾起來的（有給就不看 selectedId）。
  picked?: Set<string>;
  onRemove?: (id: string) => void;
}) {
  return (
    <>
      {byDay(props.sessions).map((day) => (
        <div class="history-day-group" key={day.label}>
          <h3 class="history-day-heading">{day.label}</h3>
          <div class={props.gridClass}>
            {day.rows.map((s) => (
              <SessionTile
                key={rowId(s)}
                s={s}
                selected={props.picked ? props.picked.has(rowId(s)) : rowId(s) === props.selectedId}
                onClick={() => props.onPick(rowId(s))}
                onRemove={props.onRemove && (() => props.onRemove!(rowId(s)))}
              />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

export type GalleryTab = "recent" | "sample";
export type GallerySelection = { kind: "session"; id: string } | { kind: "demo"; name: string } | null;

export function Gallery(props: {
  tab: GalleryTab;
  onTab: (t: GalleryTab) => void;
  sessions: SessionSummary[] | null;
  demos: string[] | null;
  selection: GallerySelection;
  onSelect: (s: GallerySelection) => void;
  onConfirm: () => void;
  onRemove?: (id: string) => void;
}) {
  const { tab, selection } = props;
  const recent = tab === "recent";
  const list = recent ? props.sessions : props.demos;
  return (
    <div class={`upload-gallery-col is-history${recent ? " showing-recent" : ""}`}>
      <div class="upload-gallery-heading">
        <div class="upload-gallery-tabs" id="upload-gallery-tabs" role="tablist">
          <div class="upload-gallery-tab-recent-group">
            <button
              type="button"
              class={`upload-gallery-tab${recent ? " is-active" : ""}`}
              id="upload-gallery-tab-recent"
              role="tab"
              aria-selected={recent}
              onClick={() => props.onTab("recent")}
            >
              <span class="upload-gallery-tab-icon" aria-hidden="true">
                <Icon name="chat" size={14} />
              </span>
              最近對話
            </button>
          </div>
          <button
            type="button"
            class={`upload-gallery-tab${recent ? "" : " is-active"}`}
            id="upload-gallery-tab-sample"
            role="tab"
            aria-selected={!recent}
            onClick={() => props.onTab("sample")}
          >
            <span class="upload-gallery-tab-icon" aria-hidden="true">
              <svg width="14" height="14" fill="none" viewBox="0 0 24 24">
                <rect x="4" y="4" width="16" height="16" rx="4" stroke="currentColor" stroke-width="1.8" />
                <path
                  d="M8 15l2.5-3 2 2 1.5-2 2 3M8 9.5h.01"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </span>
            範例照片
          </button>
        </div>
      </div>
      <div class="upload-gallery-scroll-frame">
        <div id="demo-images-grid" class="demo-images-grid">
          {recent
            ? (
                <SessionDays
                  sessions={props.sessions || []}
                  gridClass="history-grid"
                  selectedId={selection?.kind === "session" ? selection.id : null}
                  onPick={(id) => props.onSelect({ kind: "session", id })}
                  onRemove={props.onRemove}
                />
              )
            : (props.demos || []).map((name) => (
                <Tile
                  src={urls.demoThumb(name)}
                  title={name}
                  selected={selection?.kind === "demo" && selection.name === name}
                  onClick={() => props.onSelect({ kind: "demo", name })}
                />
              ))}
        </div>
      </div>
      <p id="demo-images-empty" class="history-empty" hidden={!list || list.length > 0}>
        {recent ? "還沒有對話，按「開始對話」挑一張照片吧。" : "目前沒有可顯示的照片。"}
      </p>
      {!recent && (
        <button
          id="demo-images-confirm-btn"
          class="btn btn-secondary btn-sm"
          type="button"
          disabled={selection?.kind !== "demo"}
          onClick={props.onConfirm}
        >
          使用所選照片
        </button>
      )}
    </div>
  );
}

// 跟面板模式的最近照片一樣：每格一顆 ×；標題旁「選取多筆」進入多選，勾好按「刪除所選」；
// 「全部刪除」一次清空。按鈕的 id 沿用舊頁面的，樣式（舊頁面 index.html 的那段 style，
// 建置時帶進來）就一模一樣。刪之前要不要確定、刪完怎麼辦，交給 app.tsx。
const TRASH_ICON = (
  <svg width="14" height="14" fill="none" viewBox="0 0 24 24">
    <path d="M4 7h16" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    <path
      d="M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7"
      stroke="currentColor"
      stroke-width="2.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <path
      d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13"
      stroke="currentColor"
      stroke-width="2.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
);

export function HistoryDrawer(props: {
  open: boolean;
  sessions: SessionSummary[] | null;
  currentId: string | null;
  onOpen: (id: string) => void;
  onClose: () => void;
  // 刪掉這幾格；回傳真的刪了沒（取消或失敗是 false）。
  onRemove: (ids: string[]) => Promise<boolean>;
  onClearAll: () => void;
}) {
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const stopPicking = () => {
    setPicking(false);
    setPicked(new Set());
  };
  // 抽屜收起來就不帶著多選狀態，下次打開是一般的樣子。
  useEffect(() => {
    if (!props.open) stopPicking();
  }, [props.open]);
  const toggle = (id: string) =>
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const hasRows = !!props.sessions?.length;
  const count = picked.size;
  const delHint = count ? `刪除所選（${count} 張）` : "刪除所選";
  return (
    <>
      <div class={`history-drawer-backdrop${props.open ? " drawer-open" : ""}`} onClick={props.onClose} />
      <aside class={`history-drawer${props.open ? " drawer-open" : ""}`} role="dialog" aria-label="對話紀錄">
        <button class="history-drawer-close-btn" type="button" aria-label="關閉" onClick={props.onClose}>
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24">
            <path
              d="M18 6l-4 4m0 0h4m-4 0V6M6 18l4-4m0 0H6m4 0v4"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div class="history-header">
          <div class="history-header-top">
            <h2>對話紀錄</h2>
            {hasRows && (
              <div class="history-header-actions">
                {picking ? (
                  <>
                    <button
                      id="history-delete-selected-btn"
                      class="btn btn-outline btn-sm"
                      type="button"
                      disabled={!count}
                      aria-label={delHint}
                      title={delHint}
                      onClick={async () => {
                        if (await props.onRemove([...picked])) stopPicking();
                      }}
                    >
                      {TRASH_ICON}
                    </button>
                    <button id="history-select-cancel-btn" class="btn btn-outline btn-sm" type="button" onClick={stopPicking}>
                      取消
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      id="history-select-btn"
                      class="btn btn-outline btn-sm"
                      type="button"
                      aria-label="選取多筆"
                      title="選取多筆"
                      onClick={() => setPicking(true)}
                    >
                      <svg width="14" height="14" fill="none" viewBox="0 0 24 24">
                        <rect x="3" y="3" width="18" height="18" rx="4" stroke="currentColor" stroke-width="2" />
                        <path d="M8 12.5l2.5 2.5L16 9.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                    </button>
                    <button
                      id="history-clear-all-btn"
                      class="btn btn-outline btn-sm"
                      type="button"
                      aria-label="全部刪除"
                      title="全部刪除"
                      onClick={props.onClearAll}
                    >
                      {TRASH_ICON}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
        <div class={`history-list${picking ? " chat-picking" : ""}`}>
          <SessionDays
            sessions={props.sessions || []}
            gridClass="demo-images-grid chat-history-grid"
            selectedId={props.currentId}
            onPick={picking ? toggle : props.onOpen}
            picked={picking ? picked : undefined}
            onRemove={picking ? undefined : (id) => props.onRemove([id])}
          />
        </div>
        <p class="history-empty" hidden={!props.sessions || props.sessions.length > 0}>
          目前還沒有對話。
        </p>
      </aside>
    </>
  );
}
