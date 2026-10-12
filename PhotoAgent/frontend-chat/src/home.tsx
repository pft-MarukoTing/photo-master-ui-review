// Header, the home page's intro, the 最近對話 / 範例照片 gallery and the
// 對話紀錄 drawer -- old markup and classes throughout.
import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { urls } from "./api";
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
// the odd one out next to 回到首頁 / 自訂模式's plain .btn-sm.
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

// showHistory is off while the gallery (with its 最近對話 tab) is on screen.
export function Header(props: { onHome: () => void; onHistory: () => void; showHome: boolean; showHistory?: boolean }) {
  return (
    <header class="site-header">
      <div class="header-inner">
        <button type="button" class="logo" id="logo-home-btn" onClick={props.onHome}>
          Photo<em>Master</em>
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
              <Icon name="chat" size={14} />
              <span class="btn-label">對話紀錄</span>
            </button>
          )}
          <a class="btn btn-secondary btn-sm" href={`${urls.oldUi()}${location.search}`} title="切換到自訂模式">
            <Icon name="layout" size={14} />
            <span class="btn-label">自訂模式</span>
          </a>
          <ThemeToggle />
        </div>
      </div>
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

export function SessionTile(props: { s: SessionSummary; selected: boolean; onClick: () => void }) {
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
    </Tile>
  );
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
}) {
  return (
    <>
      {byDay(props.sessions).map((day) => (
        <div class="history-day-group" key={day.label}>
          <h3 class="history-day-heading">{day.label}</h3>
          <div class={props.gridClass}>
            {day.rows.map((s) => (
              <SessionTile
                key={s.session_id}
                s={s}
                selected={s.session_id === props.selectedId}
                onClick={() => props.onPick(s.session_id)}
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

export function HistoryDrawer(props: {
  open: boolean;
  sessions: SessionSummary[] | null;
  currentId: string | null;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
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
          </div>
        </div>
        <div class="history-list">
          <SessionDays
            sessions={props.sessions || []}
            gridClass="demo-images-grid chat-history-grid"
            selectedId={props.currentId}
            onPick={props.onOpen}
          />
        </div>
        <p class="history-empty" hidden={!props.sessions || props.sessions.length > 0}>
          目前還沒有對話。
        </p>
      </aside>
    </>
  );
}
