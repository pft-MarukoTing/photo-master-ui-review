// Left side of a session: the flow breadcrumb, Before/After compare card
// and 美化紀錄 version strip, with the old markup and classes so styles.css
// applies unchanged.
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { api, urls } from "./api";
import type { Capabilities, Dict, Photo, Version } from "./types";
import { useBoxThumbs, executedPortraits, type PortraitDone } from "./face";
import { downloadImage, executedRecipeItems, executedStyleLabels, personalizationText } from "./util";
import { Icon } from "./icons";
import { ConfirmModal } from "./home";
import type { VNode } from "preact";

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
// pre：上傳頁（還沒進 session）也掛同一列，桌機上下面那排才會跟 session 一樣高；
// 窄螢幕上傳頁的版面另有一套，那邊不出現（chat.css）。
export function StepIndicator({ step, state, pre }: { step: number; state: StepState; pre?: boolean }) {
  return (
    <div class={`container chat-flow-slot${pre ? " is-pre" : ""}`} id="step-indicator-slot">
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

// 手機和直拿的平板（跟 chat.css 那段 @media 同一個條件）：點照片開大圖檢視。
const SPLIT_LAYOUT = "(max-width:600px),(max-width:1200px) and (orientation:portrait)";

// The job to show for a version: the first one that did not fail.
export function versionJob(v: Version): string {
  return v.job_ids.find((j) => !v.failed_job_ids.includes(j)) || v.job_ids[0];
}

// The floating read-out row keeps to one line (styles.css: .recipe-tags is
// flex-wrap:nowrap): labels while they fit, and only the icons once the row is
// too narrow for them -- the old result row's fitRecipeTagsRow.
const TAG_BASE_FONT = 12;
const TAG_GAP = 8;
// How far the bubble's tail is kept from either rounded end of its body.
const TAIL_EDGE_INSET = 18;

// The four read-outs of what a version was made with, in the old row's order.
// Each is a button that opens the bubble with its details; the icons are the
// old review tabs' (人像檢查 / 畫面建議 / 美化風格 / 個人化需求).
type Detail = "face" | "recipe" | "style" | "prompt";

interface MadeWith {
  portraits: PortraitDone[];
  recipes: string[];
  styles: string[];
  hint: string;
}

const NO_DETAILS: MadeWith = { portraits: [], recipes: [], styles: [], hint: "" };

const DETAIL_LABEL: Record<Detail, string> = { face: "人像調整", recipe: "畫面調整", style: "美化風格", prompt: "個人化" };
// The bubble's heading: 個人化 is the pill's short form of 個人化需求.
const DETAIL_TITLE: Record<Detail, string> = { ...DETAIL_LABEL, prompt: "個人化需求" };

function DetailIcon({ detail }: { detail: Detail }) {
  const line = { stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" } as const;
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {detail === "face" && (
        <>
          <circle cx="12" cy="8" r="4" {...line} />
          <path d="M4 20c0-4 3.5-6 8-6s8 2 8 6" {...line} />
        </>
      )}
      {detail === "recipe" && (
        <>
          <rect x="5" y="4" width="14" height="17" rx="2.5" {...line} />
          <path d="M8.5 10.5l1.8 1.8L14.5 8" {...line} />
          <path d="M8.5 16l1.8 1.8L14.5 13.5" {...line} />
        </>
      )}
      {detail === "style" && (
        <>
          <path d="M3.5 20.5L12 12" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" />
          <path d="M16 2.5l1.7 4L21.5 8l-3.8 1.5L16 13.5l-1.7-4L10.5 8l3.8-1.5L16 2.5z" fill="currentColor" />
        </>
      )}
      {detail === "prompt" && <path d="M4 4h16v12H8l-4 4V4z" {...line} />}
    </svg>
  );
}

interface CanvasProps {
  caps: Capabilities;
  photo: Photo;
  versions: Version[];
  current: Version | null;
  // 這一輪已經改過設定、但還沒按美化。紀錄最後面補一格空位，螢幕上這張就不會
  // 看起來是最新的設定。
  pending?: boolean;
  // 分析中／美化中壓在轉環底下的那行字。在 app.tsx 和聊天那張處理中卡片一起算，
  // 兩邊才不會各講各的（也不會各自跑一套輪播）。
  busy?: { title: string } | null;
  onSelect: (v: Version) => void;
  // 手機和直拿的平板：右上角那顆「第 N 版 ⌄」和點照片本身，都開大圖檢視切版本。桌機用不到，
  // 那邊有下面的美化紀錄；按鈕一直都在，只是桌機的 CSS 把它收起來。
  onViewer?: () => void;
}

// 下載後跟面板模式一樣：成功跳「已下載完成。」，失敗跳「下載失敗，請稍後再試。」。
function useDownload(): [(job: string, base: string) => void, VNode | null] {
  const [notice, setNotice] = useState<string | null>(null);
  const start = (job: string, base: string) =>
    void downloadImage(urls.resultImage(job), base).then((ok) =>
      setNotice(ok ? "已下載完成。" : "下載失敗，請稍後再試。"),
    );
  const el = notice ? <ConfirmModal message={notice} confirmLabel="確定" onAnswer={() => setNotice(null)} /> : null;
  return [start, el];
}

export function ResultsCanvas({ caps, photo, versions, current, pending, busy, onSelect, onViewer }: CanvasProps) {
  const [showGenerated, setShowGenerated] = useState(true);
  const [download, downloadNotice] = useDownload();
  // 在美化紀錄點了「原圖」：大圖停在原圖，Before/After 比較這時沒有對象，
  // 比較鈕就先停用；再點任何一版才回到那一版的 After。
  const [originPicked, setOriginPicked] = useState(false);
  // 紀錄縮圖 hover 時浮出的大一點的預覽，沿用面板模式的 .version-tile-preview。
  const previewRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  // What each finished job was made with, keyed by job id -- see the fetch
  // below for why this is read back per version rather than taken from the plan.
  const [madeWith, setMadeWith] = useState<Record<string, MadeWith>>({});
  // Which read-out the bubble is showing, if any.
  const [open, setOpen] = useState<Detail | null>(null);
  const topRef = useRef<HTMLDivElement>(null);
  const tagsRef = useRef<HTMLDivElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  // Result images that have finished loading (or failed to), keyed by job id.
  const [loaded, setLoaded] = useState<Record<string, true>>({});
  const job = current ? versionJob(current) : null;
  // 結果圖和底下那排按鈕是兩條各自回來的請求：圖片和 result 都到齊才翻到 After，
  // 兩者才會一起出現，而不是圖先冒出來、按鈕慢半拍才補上。
  const ready = !!job && !!loaded[job] && !!madeWith[job];
  // 版本之間切換時，新的那版還沒到齊就先留著上一版的成果（圖和按鈕一起），
  // 到齊再整組換上 -- 不然中間會閃一下原圖。只留同一張照片的：換照片就從原圖開始。
  const [held, setHeld] = useState<{ photo: string; job: string } | null>(null);
  useEffect(() => {
    if (ready) setHeld({ photo: photo.photo_id, job: job! });
  }, [ready, job, photo.photo_id]);
  const shownJob = ready ? job : held && held.photo === photo.photo_id ? held.job : null;
  // 處理中一律看原圖：美化中擺上一版的成果，會讓人以為那就是正在等的這一張。
  const generated = !!shownJob && showGenerated && !busy && !originPicked;
  // 剛進來、這一版的成果還沒到齊，手上也沒有上一版可以先擺：原圖先藏著（位置留著），
  // 不然會先看到原圖、再跳成成果，像是畫面換了一張。
  const arriving = !!job && !shownJob && showGenerated && !busy && !originPicked;

  // A new version always opens on the After side.
  useEffect(() => {
    setShowGenerated(true);
    setOriginPicked(false);
  }, [current?.version_id, photo.photo_id]);

  // 新版本的結果圖在背景先載好、解碼完，畫面上那張 <img> 換 src 時才不會空一拍。
  useEffect(() => {
    if (!job || loaded[job]) return;
    let dropped = false;
    const img = new Image();
    img.src = urls.resultImage(job);
    const done = () => {
      if (!dropped) setLoaded((l) => ({ ...l, [job]: true }));
    };
    img.decode().then(done, done);
    return () => {
      dropped = true;
    };
  }, [job]);

  // What this version was rendered with: the 人像 fixes, 畫面調整 and 修圖風格
  // picks that reached the Editor prompt, and its 個人化需求. They come
  // from the finish job's own stored result -- the photo's plan has usually
  // moved on by now (every adjust round rewrites it), and the message log
  // never carried them. Cached per job: a version's record never changes.
  useEffect(() => {
    if (!job || madeWith[job]) return;
    let dropped = false;
    api
      .result(job)
      .then((r: Dict) => {
        if (dropped) return;
        const analysis = r.result?.analysis;
        const entry: MadeWith = analysis
          ? {
              portraits: executedPortraits(analysis),
              recipes: executedRecipeItems(analysis),
              styles: executedStyleLabels(caps, analysis),
              hint: personalizationText(analysis),
            }
          : NO_DETAILS;
        setMadeWith((m) => ({ ...m, [job]: entry }));
      })
      .catch(() => {
        // The row is extra: a result that will not load just leaves it off --
        // but still settles, so the After side is not held back waiting for it.
        if (!dropped) setMadeWith((m) => ({ ...m, [job]: NO_DETAILS }));
      });
    return () => {
      dropped = true;
    };
  }, [job]);

  // Whatever the bubble was quoting belongs to the version (and the side) that
  // was on screen when it opened.
  useEffect(() => setOpen(null), [current?.version_id, showGenerated, originPicked]);

  useEffect(() => {
    if (!fullscreen && !open) return;
    // One Escape chain, innermost first: the bubble floats over the photo, so
    // dismissing it should not also drop the fullscreen view behind it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (open) setOpen(null);
      else setFullscreen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen, open]);

  // Dismiss the bubble by clicking anywhere outside it. The row's buttons are
  // excluded because mousedown lands before their own click, which would
  // otherwise close and immediately reopen (or switch) the bubble.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (bubbleRef.current?.contains(target) || tagsRef.current?.contains(target)) return;
      setOpen(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Keep the column from collapsing while the card is position:fixed.
  useEffect(() => {
    const top = topRef.current;
    if (!top) return;
    if (fullscreen) top.style.height = `${top.getBoundingClientRect().height}px`;
    else top.style.height = "";
  }, [fullscreen]);

  const tags = (shownJob && madeWith[shownJob]) || null;
  const details: Detail[] = [];
  if (tags?.portraits.length) details.push("face");
  if (tags?.recipes.length) details.push("recipe");
  if (tags?.styles.length) details.push("style");
  if (tags?.hint) details.push("prompt");
  const shown = open && details.includes(open) ? open : null;
  const source = urls.sourceImage(photo.analyze_job_id);
  const faceThumbs = useBoxThumbs(tags?.portraits.map((p) => p.box) || [], source);

  // Labels while every button's full text fits side by side, icons only once it
  // does not: measured at the base size with the labels showing, since flex
  // shrinking would otherwise hide the overflow being measured. Then aim the
  // bubble's tail at the open button, which moves with every re-fit -- the row
  // is centred, so its positions depend on what it holds.
  function fitTags() {
    const row = tagsRef.current;
    const chips = Array.from(row?.children || []) as HTMLElement[];
    if (!row || !chips.length) return;
    row.classList.remove("is-icon-only");
    chips.forEach((chip) => (chip.style.fontSize = `${TAG_BASE_FONT}px`));
    const required = chips.reduce((sum, chip) => sum + chip.scrollWidth, 0) + (chips.length - 1) * TAG_GAP;
    row.classList.toggle("is-icon-only", required > row.clientWidth);
  }

  function positionTail() {
    const bubble = bubbleRef.current;
    const pill = shown && tagsRef.current?.querySelector<HTMLElement>(`[data-detail="${shown}"]`);
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
  }, [tags, shown, generated, fullscreen]);

  // Window resize, rotating a phone and the fullscreen toggle all change how
  // much room the row has, independent of any re-render -- and each of those
  // moves the button under an open bubble, so the tail follows the re-fit.
  useEffect(() => {
    const row = tagsRef.current;
    if (!row || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      fitTags();
      positionTail();
    });
    observer.observe(row);
    return () => observer.disconnect();
  }, [details.length > 0]);

  const idx = current ? versions.findIndex((v) => v.version_id === current.version_id) : -1;

  // 和面板模式的 showVersionTilePreview 同一套算法：預設浮在縮圖正上方，
  // 上面沒位置就翻到下方，左右夾在視窗內。直接用縮圖本身的 src，不多打一次請求。
  function showPreview(tile: HTMLElement, src: string) {
    const popup = previewRef.current;
    const img = popup?.firstElementChild as HTMLImageElement | null;
    if (!popup || !img) return;
    img.src = src;
    const rect = tile.getBoundingClientRect();
    const popupW = popup.offsetWidth || 168;
    const popupH = popup.offsetHeight || 168;
    const gap = 10;
    let top = rect.top - popupH - gap;
    if (top < 8) top = rect.bottom + gap;
    let left = rect.left + rect.width / 2 - popupW / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - popupW - 8));
    popup.style.top = `${Math.round(top)}px`;
    popup.style.left = `${Math.round(left)}px`;
    popup.classList.add("is-visible");
  }

  function hidePreview() {
    previewRef.current?.classList.remove("is-visible");
  }

  // 紀錄整排消失（換照片、清空）時，跟著收掉還浮著的預覽。
  useEffect(() => hidePreview, [versions.length > 0, photo.photo_id]);

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
              {/* 手機上「第 N 版」和比較、下載併成一顆膠囊，固定在照片那段的右上角；桌機 display:contents，
                  canvas-actions 照舊是標頭那排的一格。 */}
              <div class="canvas-tools">
                {onViewer && (
                  <button type="button" class="canvas-version-btn" onClick={onViewer} aria-label="看大圖、切換版本">
                    {generated && current ? `第 ${current.ordinal} 版` : "原圖"}
                    <Icon name="caret" size={14} />
                  </button>
                )}
                <div class="canvas-actions">
                  {/* 處理中這兩顆指的都是上一版，但畫面上蓋著的是原圖 + 處理中那一層，
                      按了看不出任何變化 -- 這段期間就收起來。全螢幕留著，它和哪一版無關。 */}
                  {job && !busy && (
                    <button
                      id="compare-toggle-btn"
                      class="canvas-btn"
                      type="button"
                      disabled={originPicked}
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
                  {/* 看原圖時這顆下載的還是那一版的成果，對不上畫面 -- 先收起來。 */}
                  {job && !busy && !originPicked && (
                    <div class="canvas-btn-wrap" id="download-menu-wrap">
                      <a
                        id="download-link"
                        class="canvas-btn"
                        href={urls.resultImage(job)}
                        download={`photomaster_${photo.photo_id}_v${current!.ordinal}.jpg`}
                        onClick={(e) => {
                          e.preventDefault();
                          download(job, `photomaster_${photo.photo_id}_v${current!.ordinal}`);
                        }}
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
            </div>
            <div class="photo-frame" onClick={() => onViewer && matchMedia(SPLIT_LAYOUT).matches && onViewer()}>
              <img
                id="original-img"
                class="compare-img compare-img-original"
                src={source}
                alt="原始照片"
                style={arriving ? { visibility: "hidden" } : undefined}
              />
              {shownJob && (
                <img id="generated-img" class="compare-img compare-img-generated" src={urls.resultImage(shownJob)} alt="美化結果" />
              )}
              {busy && (
                <div class="canvas-busy" role="status" aria-live="polite">
                  <div class="canvas-busy-body">
                    <div class="proc-visual canvas-busy-rings" aria-hidden="true">
                      <div class="think-ring" />
                      <div class="think-ring" />
                      <div class="think-ring" />
                    </div>
                    <p class="canvas-busy-title">{busy.title}</p>
                  </div>
                </div>
              )}
            </div>

            {/* What this version was actually made with, floating at the
                bottom of the photo -- the old result row's buttons: 人像調整,
                畫面調整 and 美化風格 for what reached the Editor prompt, and
                個人化 for the request it carried. Each opens the bubble below
                with its details. Hidden by styles.css while the Original is
                showing, since all of them describe the result. */}
            {details.length > 0 && (
              <div id="recipe-tags" class="recipe-tags" ref={tagsRef}>
                {details.map((d) => (
                  <button
                    type="button"
                    class="portrait-check-toggle result-detail-toggle recipe-tag-personalize"
                    data-detail={d}
                    title={DETAIL_LABEL[d]}
                    aria-label={DETAIL_LABEL[d]}
                    aria-controls="personalize-popover"
                    aria-expanded={shown === d}
                    onClick={() => setOpen(shown === d ? null : d)}
                  >
                    <DetailIcon detail={d} />
                    <b class="result-detail-label">{DETAIL_LABEL[d]}</b>
                  </button>
                ))}
              </div>
            )}
            {/* The buttons have no room for the details themselves, so they
                live in this speech bubble over the photo. Dismissed by its X,
                an outside click, Escape, or any version / Before-After switch. */}
            {details.length > 0 && (
              <div
                ref={bubbleRef}
                id="personalize-popover"
                class="personalize-popover"
                role="dialog"
                aria-label={shown ? DETAIL_TITLE[shown] : undefined}
                hidden={!shown}
              >
                <div class="personalize-popover-head">
                  <span class="personalize-popover-title">{shown && DETAIL_TITLE[shown]}</span>
                  <button
                    class="personalize-popover-close"
                    type="button"
                    aria-label="關閉"
                    onClick={() => setOpen(null)}
                  >
                    <svg width="14" height="14" fill="none" viewBox="0 0 24 24">
                      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
                    </svg>
                  </button>
                </div>
                <div class="personalize-popover-text">
                  {shown === "face" && tags && (
                    <div class="portrait-adjustment-list" role="list">
                      {tags.portraits.map((p, i) => (
                        <div class="portrait-adjustment-row" role="listitem">
                          <img class="portrait-adjustment-thumbnail" src={faceThumbs[i] || undefined} alt={p.label} title={p.label} />
                          <div class="portrait-adjustment-items">
                            {p.tags.map((t) => (
                              <span class="portrait-adjustment-item" aria-label={`${t}，已完成`}>
                                <span class="portrait-check-complete" aria-hidden="true">
                                  ✓
                                </span>
                                {t}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {(shown === "recipe" || shown === "style") && tags && (
                    <ul class="result-detail-list">
                      {(shown === "recipe" ? tags.recipes : tags.styles).map((item) => (
                        <li>{item}</li>
                      ))}
                    </ul>
                  )}
                  {shown === "prompt" && tags?.hint}
                </div>
              </div>
            )}
          </div>

          {versions.length > 0 && (
            <div id="version-strip" class="version-strip">
              <div class="version-strip-head">
                <span class="version-strip-title">美化紀錄</span>
                <span id="version-strip-count" class="version-strip-count">
                  {originPicked ? "原圖" : `第 ${idx + 1}／${versions.length} 組`}
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
                    <button
                      type="button"
                      class={`version-origin-tile${originPicked ? " is-current" : ""}`}
                      role="tab"
                      aria-selected={originPicked}
                      title="原圖"
                      onClick={() => setOriginPicked(true)}
                      onMouseEnter={(e) => showPreview(e.currentTarget, urls.sourceThumb(photo.analyze_job_id))}
                      onMouseLeave={hidePreview}
                    >
                      <img src={urls.sourceThumb(photo.analyze_job_id)} alt="" />
                    </button>
                    <span class="version-group-label">原圖</span>
                  </div>
                  <div class="version-strip-divider" />
                </div>
                <div class="version-strip-versions">
                  {versions.map((v) => {
                    const on = !originPicked && v.version_id === current?.version_id;
                    return (
                      <div class={`version-group${on ? " is-current" : ""}`} data-version-id={v.version_id}>
                        <div class="version-group-tiles">
                          <button
                            type="button"
                            class={`version-tile${on ? " is-current" : ""}`}
                            role="tab"
                            aria-selected={on}
                            title={`第 ${v.ordinal} 版`}
                            onClick={() => {
                              // 點的若是同一版，version_id 沒變、上面的 effect 不會跑，這裡自己切回 After。
                              setOriginPicked(false);
                              setShowGenerated(true);
                              onSelect(v);
                            }}
                            onMouseEnter={(e) => showPreview(e.currentTarget, urls.resultThumb(versionJob(v)))}
                            onMouseLeave={hidePreview}
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
      <div ref={previewRef} class="version-tile-preview" aria-hidden="true">
        <img alt="" />
      </div>
      <div
        id="compare-fullscreen-backdrop"
        class="compare-fullscreen-backdrop"
        hidden={!fullscreen}
        onClick={() => setFullscreen(false)}
      />
      {downloadNotice}
    </div>
  );
}

// 手機和直拿的平板沒有左邊那張比較卡，點照片列或對話裡的成品就開這個：整頁深色、
// 只有照片。按住「按住看原圖」換成原圖、放開回來；底下一排是美化紀錄，點了就切到
// 那一版（跟桌機點紀錄一樣，會記成目前這版）。看原圖時下載收起來，跟比較卡同一個道理。
export function VersionViewer({ photo, versions, current, open, onClose, onSelect }: {
  photo: Photo;
  versions: Version[];
  current: Version | null;
  open: boolean;
  onClose: () => void;
  onSelect: (v: Version) => void;
}) {
  const [originPicked, setOriginPicked] = useState(false);
  const [holding, setHolding] = useState(false);
  const [download, downloadNotice] = useDownload();
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setOriginPicked(!current);
    setHolding(false);
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [open]);

  // 目前這一格捲進畫面裡：版本一多，最新的那格會在右邊看不到的地方。
  useLayoutEffect(() => {
    if (!open) return;
    const track = trackRef.current;
    const on = track?.querySelector<HTMLElement>(".version-viewer-tile.is-current");
    if (!track || !on) return;
    const left = on.offsetLeft - (track.clientWidth - on.offsetWidth) / 2;
    track.scrollLeft = Math.max(0, left);
  }, [open, current?.version_id, originPicked]);

  if (!open) return null;
  const source = urls.sourceImage(photo.analyze_job_id);
  const job = current && !originPicked ? versionJob(current) : null;
  const title = job ? `第 ${current!.ordinal} 版` : "原圖";
  const release = () => setHolding(false);

  return (
    <div class="version-viewer" role="dialog" aria-modal="true" aria-label={`${title}大圖`}>
      <div class="version-viewer-head">
        <span class="version-viewer-title">
          {holding ? "原圖" : title}
          {versions.length > 0 && <small>共 {versions.length} 版</small>}
        </span>
        <button type="button" class="version-viewer-btn" aria-label="關閉" onClick={onClose}>
          <Icon name="x" size={18} />
        </button>
      </div>
      <div class="version-viewer-stage" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <img src={source} alt="原始照片" hidden={!!job && !holding} />
        {job && <img src={urls.resultImage(job)} alt={`${title}美化結果`} hidden={holding} />}
      </div>
      <div class="version-viewer-foot">
        {job && (
          <div class="version-viewer-actions">
            <button
              type="button"
              class={`version-viewer-hold${holding ? " is-on" : ""}`}
              aria-pressed={holding}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                setHolding(true);
              }}
              onPointerUp={release}
              onPointerCancel={release}
              onLostPointerCapture={release}
              onKeyDown={(e) => (e.key === " " || e.key === "Enter") && setHolding(true)}
              onKeyUp={release}
              onBlur={release}
              onContextMenu={(e) => e.preventDefault()}
            >
              按住看原圖
            </button>
            <a
              class="version-viewer-btn"
              href={urls.resultImage(job)}
              download={`photomaster_${photo.photo_id}_v${current!.ordinal}.jpg`}
              onClick={(e) => {
                e.preventDefault();
                download(job, `photomaster_${photo.photo_id}_v${current!.ordinal}`);
              }}
              aria-label={`下載${title}`}
              title="下載"
            >
              <Icon name="down" size={18} />
            </a>
          </div>
        )}
        {versions.length > 0 && (
          <div class="version-viewer-track" ref={trackRef} role="tablist" aria-label="這張照片的美化紀錄">
            <button
              type="button"
              class={`version-viewer-tile${!job ? " is-current" : ""}`}
              role="tab"
              aria-selected={!job}
              onClick={() => setOriginPicked(true)}
            >
              <img src={urls.sourceThumb(photo.analyze_job_id)} alt="" />
              <span>原圖</span>
            </button>
            {versions.map((v) => {
              const on = !!job && v.version_id === current?.version_id;
              return (
                <button
                  type="button"
                  class={`version-viewer-tile${on ? " is-current" : ""}`}
                  role="tab"
                  aria-selected={on}
                  onClick={() => {
                    setOriginPicked(false);
                    if (v.version_id !== current?.version_id) onSelect(v);
                  }}
                >
                  <img src={urls.resultThumb(versionJob(v))} alt="" />
                  <span>第 {v.ordinal} 版</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
      {downloadNotice}
    </div>
  );
}

// 手機和直拿的平板：照片在上、對話在下，中間這條把手拖動調整比例。放開會吸到最近的
// 一段（照片佔 25／45／70%），聚焦後 ↑↓ 一段一段跳。
// 比例寫在 .results-canvas-row 的 --split 上，記在 localStorage，下次進來沿用。
const SPLIT_STOPS = [0.25, 0.45, 0.7];
const SPLIT_MIN = 0.2;
const SPLIT_MAX = 0.75;
const SPLIT_KEY = "chat-split";

function readSplit(): number {
  try {
    const v = parseFloat(localStorage.getItem(SPLIT_KEY) || "");
    if (v >= SPLIT_MIN && v <= SPLIT_MAX) return v;
  } catch {}
  return SPLIT_STOPS[1];
}

export function SplitGrip() {
  const ref = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(readSplit);
  const drag = useRef<{ y: number; from: number; h: number } | null>(null);
  const row = () => ref.current?.closest<HTMLElement>(".results-canvas-row") || null;

  // 照片縮到最小那段時，底部那排畫面調整等標籤先收起來（CSS 看 is-split-small）。
  useLayoutEffect(() => {
    const r = row();
    r?.style.setProperty("--split", String(split));
    r?.classList.toggle("is-split-small", split < 0.32);
  }, [split]);
  useEffect(
    () => () => {
      const r = row();
      r?.style.removeProperty("--split");
      r?.classList.remove("is-split-small", "is-resizing");
    },
    [],
  );

  const clamp = (v: number) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, v));
  const settle = (v: number) => {
    const snapped = SPLIT_STOPS.reduce((a, b) => (Math.abs(b - v) < Math.abs(a - v) ? b : a));
    setSplit(snapped);
    try {
      localStorage.setItem(SPLIT_KEY, String(snapped));
    } catch {}
  };
  const end = () => {
    if (!drag.current) return;
    drag.current = null;
    row()?.classList.remove("is-resizing");
    settle(split);
  };

  return (
    <div
      ref={ref}
      class="split-grip"
      role="separator"
      aria-orientation="horizontal"
      aria-label="拖動調整照片和對話的大小"
      aria-valuemin={SPLIT_MIN * 100}
      aria-valuemax={SPLIT_MAX * 100}
      aria-valuenow={Math.round(split * 100)}
      tabIndex={0}
      onPointerDown={(e) => {
        const r = row();
        if (!r) return;
        // 不擋的話，滑鼠按著往下拖會被瀏覽器當成選字或拖曳，拖到一半就 pointercancel。
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { y: e.clientY, from: split, h: r.getBoundingClientRect().height };
        r.classList.add("is-resizing");
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (d && d.h) setSplit(clamp(d.from + (e.clientY - d.y) / d.h));
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={(e) => {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        const i = SPLIT_STOPS.findIndex((s) => Math.abs(s - split) < 0.01);
        const next = (i < 0 ? 1 : i) + (e.key === "ArrowUp" ? -1 : 1);
        settle(SPLIT_STOPS[Math.max(0, Math.min(SPLIT_STOPS.length - 1, next))]);
      }}
    >
      <i />
    </div>
  );
}
