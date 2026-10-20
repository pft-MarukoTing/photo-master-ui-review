// Left side of a session: the flow breadcrumb, Before/After compare card
// and 美化紀錄 version strip, with the old markup and classes so styles.css
// applies unchanged.
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { api, urls } from "./api";
import type { Capabilities, Dict, Photo, Version } from "./types";
import { personalizationText, styleTagLabels } from "./util";

const STEP_NAMES = ["上傳", "分析", "美化", "完成"];

// done 這一步做完了、busy 正在跑、pending 停在這裡等使用者。
export type StepState = "done" | "busy" | "pending";

// (current step 1-4, how that step stands)
export function stepState(photo: Photo, versions: Version[], adjusting = false): [number, StepState] {
  if (photo.status === "rendering") return [3, "busy"];
  // 調整到一半：畫面上是有成果，但那是上一版的。流程要退回「美化」等著被按 --
  // 停在「完成」的話，離開再回來時整個左欄都在說這張已經做完了。
  if (adjusting) return [3, "pending"];
  if (versions.length) return [4, "done"];
  if (photo.status === "analyzing") return [2, "busy"];
  return [2, "done"];
}

// One quiet line of breadcrumb rather than four pills with numbered discs and
// connector rails: on this page the photo is the subject, and the flow is
// orientation, not a control. The busy dot keeps the one signal the old
// spinner carried -- this step is still running.
export function StepIndicator({ step, state }: { step: number; state: StepState }) {
  return (
    <div class="container chat-flow-slot" id="step-indicator-slot">
      <nav class="chat-flow" aria-label="美化流程">
        <ol>
          {STEP_NAMES.map((name, i) => {
            const n = i + 1;
            const now = n === step;
            const cls = ["chat-flow-step"];
            if (n < step || (now && state === "done")) cls.push("is-done");
            if (now) cls.push("is-now");
            if (now && state === "busy") cls.push("is-busy");
            return (
              <li class={cls.join(" ")} aria-current={now ? "step" : undefined}>
                {name}
                {now && state === "busy" && <span class="chat-flow-dot" aria-hidden="true" />}
              </li>
            );
          })}
        </ol>
      </nav>
    </div>
  );
}

const EXPAND = "M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4";
const RESTORE = "M9 3v4a2 2 0 0 1-2 2H3M15 3v4a2 2 0 0 0 2 2h4M9 21v-4a2 2 0 0 0-2-2H3M15 21v-4a2 2 0 0 1 2-2h4";

// The job to show for a version: the first one that did not fail.
export function versionJob(v: Version): string {
  return v.job_ids.find((j) => !v.failed_job_ids.includes(j)) || v.job_ids[0];
}

// The floating read-out row keeps to one line (styles.css: .recipe-tags is
// flex-wrap:nowrap) however many picks it carries -- rather than clipping a
// cramped label with an ellipsis, shrink every chip's font-size in lockstep
// until each one's full text fits, so the labels stay readable, just smaller.
const TAG_BASE_FONT = 12;
const TAG_MIN_FONT = 8;
// How far the bubble's tail is kept from either rounded end of its body.
const TAIL_EDGE_INSET = 18;

interface CanvasProps {
  caps: Capabilities;
  photo: Photo;
  versions: Version[];
  current: Version | null;
  // 這一輪已經改過設定、但還沒按美化。紀錄最後面補一格空位，螢幕上這張就不會
  // 看起來是最新的設定。
  pending?: boolean;
  // 分析中／美化中壓在轉環底下的那行字。在 app.tsx 和聊天那張處理中卡片一起算，
  // 兩邊才不會各講各的（也不會各自跑一套 5 秒輪播）。
  busy?: { title: string } | null;
  onSelect: (v: Version) => void;
}

export function ResultsCanvas({ caps, photo, versions, current, pending, busy, onSelect }: CanvasProps) {
  const [showGenerated, setShowGenerated] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  // What each finished job was made with, keyed by job id -- see the fetch
  // below for why this is read back per version rather than taken from the plan.
  const [madeWith, setMadeWith] = useState<Record<string, { styles: string[]; hint: string }>>({});
  const [hintOpen, setHintOpen] = useState(false);
  const topRef = useRef<HTMLDivElement>(null);
  const tagsRef = useRef<HTMLDivElement>(null);
  const pillRef = useRef<HTMLSpanElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const job = current ? versionJob(current) : null;
  // 處理中一律看原圖：美化中擺上一版的成果，會讓人以為那就是正在等的這一張。
  const generated = !!job && showGenerated && !busy;

  // A new version always opens on the After side.
  useEffect(() => setShowGenerated(true), [current?.version_id]);

  // The 修圖風格 picks and 個人化需求 this version was rendered with. They come
  // from the finish job's own stored result -- the photo's plan has usually
  // moved on by now (every adjust round rewrites it), and the message log
  // never carried them. Cached per job: a version's record never changes.
  useEffect(() => {
    if (!job || madeWith[job]) return;
    let dropped = false;
    api
      .result(job)
      .then((r: Dict) => {
        const analysis = r.result?.analysis;
        if (dropped || !analysis) return;
        const entry = {
          styles: styleTagLabels(caps, analysis.style_options),
          hint: personalizationText(analysis),
        };
        setMadeWith((m) => ({ ...m, [job]: entry }));
      })
      .catch(() => {
        /* the row is extra: a result that will not load just leaves it off */
      });
    return () => {
      dropped = true;
    };
  }, [job]);

  // Whatever request the bubble was quoting belongs to the version (and the
  // side) that was on screen when it opened.
  useEffect(() => setHintOpen(false), [current?.version_id, showGenerated]);

  useEffect(() => {
    if (!fullscreen && !hintOpen) return;
    // One Escape chain, innermost first: the bubble floats over the photo, so
    // dismissing it should not also drop the fullscreen view behind it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (hintOpen) setHintOpen(false);
      else setFullscreen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen, hintOpen]);

  // Dismiss the bubble by clicking anywhere outside it. The pill is excluded
  // because mousedown lands before its own click, which would otherwise close
  // and immediately reopen the bubble.
  useEffect(() => {
    if (!hintOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (bubbleRef.current?.contains(target) || pillRef.current?.contains(target)) return;
      setHintOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [hintOpen]);

  // Keep the column from collapsing while the card is position:fixed.
  useEffect(() => {
    const top = topRef.current;
    if (!top) return;
    if (fullscreen) top.style.height = `${top.getBoundingClientRect().height}px`;
    else top.style.height = "";
  }, [fullscreen]);

  const tags = (job && madeWith[job]) || null;
  const styleLabel = tags ? tags.styles.join(" / ") : "";
  const hint = tags ? tags.hint : "";
  const hasTags = !!(styleLabel || hint);

  // Shrink the row's chips until nothing is clipped (offsetWidth < scrollWidth
  // is the standard "this text is being cut off" check), resetting to the base
  // size first so a version with fewer picks is not left at an earlier shrink.
  // Then aim the bubble's tail at the pill, whose position moves with every
  // re-fit -- the row is centred, so its widths depend on what it holds.
  function fitTags() {
    const chips = Array.from(tagsRef.current?.children || []) as HTMLElement[];
    if (!chips.length) return;
    let size = TAG_BASE_FONT;
    const apply = () => chips.forEach((chip) => (chip.style.fontSize = `${size}px`));
    apply();
    const clipped = () => chips.some((chip) => chip.scrollWidth > chip.clientWidth + 1);
    while (size > TAG_MIN_FONT && clipped()) {
      size -= 1;
      apply();
    }
  }

  function positionTail() {
    const bubble = bubbleRef.current;
    const pill = pillRef.current;
    if (!bubble || !pill) return;
    const bubbleRect = bubble.getBoundingClientRect();
    // Zero while the bubble is hidden -- nothing to aim yet.
    if (!bubbleRect.width) return;
    const pillRect = pill.getBoundingClientRect();
    const center = pillRect.left + pillRect.width / 2 - bubbleRect.left;
    const max = Math.max(TAIL_EDGE_INSET, bubbleRect.width - TAIL_EDGE_INSET);
    bubble.style.setProperty("--personalize-tail-x", `${Math.min(Math.max(center, TAIL_EDGE_INSET), max)}px`);
  }

  useLayoutEffect(() => {
    fitTags();
    positionTail();
  }, [styleLabel, hint, hintOpen, generated, fullscreen]);

  // Window resize, rotating a phone and the fullscreen toggle all change how
  // much room the row has, independent of any re-render -- and each of those
  // moves the pill under an open bubble, so the tail follows the re-fit.
  useEffect(() => {
    const row = tagsRef.current;
    if (!row || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      fitTags();
      positionTail();
    });
    observer.observe(row);
    return () => observer.disconnect();
  }, [hasTags]);

  const source = urls.sourceImage(photo.analyze_job_id);
  const idx = current ? versions.findIndex((v) => v.version_id === current.version_id) : -1;

  return (
    <div class="results-canvas-col" id="results-canvas-col">
      <div class="results-canvas-top" id="results-canvas-top" ref={topRef}>
        <div
          class={`compare-card${generated ? " showing-generated" : ""}${fullscreen ? " is-fullscreen" : ""}`}
          id="compare-card"
        >
          <div class="photo-frame-wrap" id="photo-frame-wrap">
            <div class="canvas-header-row">
              <div id="compare-hint" class="compare-hint" hidden={!!busy}>
                {generated ? "After" : "Before"}
              </div>
              <div class="canvas-actions">
                {/* 處理中這兩顆指的都是上一版，但畫面上蓋著的是原圖 + 處理中那一層，
                    按了看不出任何變化 -- 這段期間就收起來。全螢幕留著，它和哪一版無關。 */}
                {job && !busy && (
                  <button
                    id="compare-toggle-btn"
                    class="canvas-btn"
                    type="button"
                    title="compare"
                    aria-label={generated ? "查看原始照片" : "查看美化結果"}
                    onClick={() => setShowGenerated(!showGenerated)}
                  >
                    <svg width="18" height="18" fill="none" viewBox="0 0 24 24">
                      <rect x="3" y="4" width="18" height="16" rx="3" stroke="currentColor" stroke-width="2" />
                      <path d="M12 4v16" stroke="currentColor" stroke-width="2" />
                      <path d="M3 7h9V4" fill="currentColor" opacity=".9" stroke="none" />
                    </svg>
                  </button>
                )}
                <button
                  id="compare-fullscreen-btn"
                  class="canvas-btn"
                  type="button"
                  title={fullscreen ? "back to original window" : "full screen"}
                  aria-label={fullscreen ? "返回原視窗" : "全螢幕檢視比較"}
                  onClick={() => setFullscreen(!fullscreen)}
                >
                  <svg width="18" height="18" fill="none" viewBox="0 0 24 24">
                    <path
                      d={fullscreen ? RESTORE : EXPAND}
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
                {job && !busy && (
                  <div class="canvas-btn-wrap" id="download-menu-wrap">
                    <a
                      id="download-link"
                      class="canvas-btn"
                      href={urls.resultImage(job)}
                      download={`photomaster_${photo.photo_id}_v${current!.ordinal}.png`}
                      title="下載結果"
                      aria-label="下載結果"
                    >
                      <svg width="18" height="18" fill="none" viewBox="0 0 24 24">
                        <path
                          d="M12 3v13m0 0l-4.5-4.5M12 16l4.5-4.5M4 21h16"
                          stroke="currentColor"
                          stroke-width="2"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        />
                      </svg>
                    </a>
                  </div>
                )}
              </div>
            </div>
            <div class="photo-frame">
              <img id="original-img" class="compare-img compare-img-original" src={source} alt="原始照片" />
              {job && (
                <img id="generated-img" class="compare-img compare-img-generated" src={urls.resultImage(job)} alt="美化結果" />
              )}
              {busy && (
                <div class="canvas-busy" role="status" aria-live="polite">
                  <div class="proc-visual canvas-busy-rings" aria-hidden="true">
                    <div class="think-ring" />
                    <div class="think-ring" />
                    <div class="think-ring" />
                  </div>
                  <p class="canvas-busy-title">{busy.title}</p>
                </div>
              )}
            </div>

            {/* What this version was actually asked for, floating at the
                bottom of the photo: the non-default 修圖風格 picks read out as
                one quiet label, and -- when the run carried a 個人化需求 -- a
                pressable 個人化 pill that quotes it. Hidden by styles.css while
                the Original is showing, since both describe the result. */}
            {hasTags && (
              <div id="recipe-tags" class="recipe-tags" ref={tagsRef}>
                {styleLabel && <span>{styleLabel}</span>}
                {hint && (
                  <span
                    ref={pillRef}
                    class="recipe-tag-personalize"
                    role="button"
                    tabIndex={0}
                    title="點一下看這次填寫的個人化需求"
                    aria-controls="personalize-popover"
                    aria-expanded={hintOpen}
                    onClick={() => setHintOpen(!hintOpen)}
                    onKeyDown={(e: KeyboardEvent) => {
                      if (e.key !== "Enter" && e.key !== " ") return;
                      e.preventDefault();
                      setHintOpen(!hintOpen);
                    }}
                  >
                    個人化
                  </span>
                )}
              </div>
            )}
            {/* The pill has no room for a whole sentence, so the request
                itself lives in this speech bubble over the photo. Dismissed
                by its X, an outside click, Escape, or any version /
                Before-After switch. */}
            {hint && (
              <div
                ref={bubbleRef}
                id="personalize-popover"
                class="personalize-popover"
                role="dialog"
                aria-label="個人化需求"
                hidden={!hintOpen}
              >
                <div class="personalize-popover-head">
                  <span class="personalize-popover-title">個人化需求</span>
                  <button
                    class="personalize-popover-close"
                    type="button"
                    aria-label="關閉"
                    onClick={() => setHintOpen(false)}
                  >
                    <svg width="14" height="14" fill="none" viewBox="0 0 24 24">
                      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
                    </svg>
                  </button>
                </div>
                <p class="personalize-popover-text">{hint}</p>
              </div>
            )}
          </div>

          {versions.length > 0 && (
            <div id="version-strip" class="version-strip">
              <div class="version-strip-head">
                <span class="version-strip-title">美化紀錄</span>
                <span id="version-strip-count" class="version-strip-count">
                  第 {idx + 1}／{versions.length} 組
                </span>
                <span class="version-strip-note">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" />
                    <path d="M12 11v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                    <circle cx="12" cy="7.8" r="1.1" fill="currentColor" />
                  </svg>
                  每張美化結果皆由原圖重新產生
                </span>
              </div>
              <div id="version-strip-track" class="version-strip-track" role="tablist" aria-label="這張照片的美化紀錄">
                <div class="version-strip-origin">
                  <div class="version-origin">
                    <div class="version-origin-tile">
                      <img src={urls.sourceThumb(photo.analyze_job_id)} alt="" />
                    </div>
                    <span class="version-group-label">原圖</span>
                  </div>
                  <div class="version-strip-divider" />
                </div>
                <div class="version-strip-versions">
                  {versions.map((v) => {
                    const on = v.version_id === current?.version_id;
                    return (
                      <div class={`version-group${on ? " is-current" : ""}`} data-version-id={v.version_id}>
                        <div class="version-group-tiles">
                          <button
                            type="button"
                            class={`version-tile${on ? " is-current" : ""}`}
                            role="tab"
                            aria-selected={on}
                            title={`第 ${v.ordinal} 版`}
                            onClick={() => onSelect(v)}
                          >
                            <img src={urls.resultThumb(versionJob(v))} alt="" />
                          </button>
                        </div>
                        <span class="version-group-label">第 {v.ordinal} 版</span>
                      </div>
                    );
                  })}
                  {pending && (
                    <div class="version-group is-pending">
                      <div class="version-group-tiles">
                        <div class="version-tile is-pending">
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M4 8h5M13 8h7M4 16h9M17 16h3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                            <circle cx="11" cy="8" r="2.2" stroke="currentColor" stroke-width="1.8" />
                            <circle cx="15" cy="16" r="2.2" stroke="currentColor" stroke-width="1.8" />
                          </svg>
                        </div>
                      </div>
                      <span class="version-group-label">調整中</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      <div
        id="compare-fullscreen-backdrop"
        class="compare-fullscreen-backdrop"
        hidden={!fullscreen}
        onClick={() => setFullscreen(false)}
      />
    </div>
  );
}
