// Building blocks of the chat panel: the panel shell, message rows, chips and
// the few cards that are new to the chat (working / result / collapsed).
import { rtl, shownLabel, shownRecipe, t } from "./i18n";
import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { api, urls } from "./api";
import { Icon } from "./icons";
import { markLeavingForPanel } from "./pre-handoff";
import { qaText } from "./util";

// 新進來的 AI 訊息一字一字打出來：一則打完才換下一則。每 TYPE_MS 出幾個字，
// 長的那則加快，一則最多約 TYPE_MAX_MS 打完；兩則之間停 TYPE_GAP_MS。
const TYPE_MS = 30;
const TYPE_MAX_MS = 1600;
const TYPE_GAP_MS = 200;
const TYPE_TEXT = ".chat-text, [data-chat-typewriter-text]";

// 打字是給 AI 這次回的話用的，不是給舊紀錄。沒有傳 live 的面板（首頁預覽）
// 用這個當備案：這一頁開起來之後使用者動過手（點、按鍵），之後冒出來的才算新的。
let userActed = false;
for (const type of ["pointerdown", "keydown"]) addEventListener(type, () => { userActed = true; }, { capture: true, once: true });

// 逐字顯示正文及標記的人物描述；人物頭像隨所屬描述依序進場。同一則裡的卡片、
// 按鈕等其他東西先佔位不顯示，字打到它們的位置才出來，不會字還在打、底下的
// 按鈕已經能按。
// 文字是直接改 DOM 的：Preact 只在內容變了才重寫文字節點，所以每次寫入前先
// 比對，被 Preact 改過的節點就不再碰。回傳的函式立刻全部顯示完。
function finishMessage(message: HTMLElement) {
  message.classList.remove("is-waiting");
  // 打完（或直接顯示）的字記在段落上：同一則訊息之後換了內容（摘要、這版的說明
  // 寫好了換掉「正在整理…」），只打換上來的字，已經看過的不重打。記的是字本身，
  // 因為 Preact 常沿用同一個 <p> 只換字。
  message.querySelectorAll<HTMLElement>(TYPE_TEXT).forEach((p) => { p.dataset.typed = p.textContent || ""; });
  message.querySelectorAll(".is-typewriter-pending").forEach(row => row.classList.remove("is-typewriter-pending"));
  message.querySelectorAll(".is-typewriter-revealed").forEach(row => row.classList.remove("is-typewriter-revealed"));
  message.querySelectorAll('[data-typewriter-hold=""]').forEach(block => block.removeAttribute("data-typewriter-hold"));
  if (message.isConnected) message.dispatchEvent(new CustomEvent("chat-typewriter-complete"));
}

// 訊息裡不是打字對象的區塊（卡片、按鈕列……），照畫面順序。含有要打的字的就
// 往裡面找；人物清單有自己的進場方式，不算在內。
function holdBlocks(parent: Element, out: HTMLElement[] = []): HTMLElement[] {
  for (const child of Array.from(parent.children) as HTMLElement[]) {
    if (child.matches(TYPE_TEXT) || child.matches("[data-chat-typewriter-group], .subject-preview-person")) continue;
    if (child.querySelector(TYPE_TEXT)) holdBlocks(child, out);
    else out.push(child);
  }
  return out;
}

// revised：畫面上本來就有、這次只是換了內容的訊息 -- 不藏起來排隊、不重播進場，
// 只打還沒顯示過的字。
function typeMessages(msgs: HTMLElement[], revised: Set<Element> = new Set()): () => void {
  type Item = { node: Text; chars: string[]; shown: string; row: HTMLElement | null };
  let i = 0;
  let items: Item[] = [];
  let blocks: HTMLElement[] = [];
  let b = 0;
  let k = 0;
  let n = 0;
  let step = 1;
  let raf = 0;
  let next = 0;
  let rowRevealUntil = 0;
  for (const m of msgs) if (!revised.has(m)) m.classList.add("is-waiting");
  const start = () => {
    const m = msgs[i];
    m.classList.remove("is-waiting");
    if (!revised.has(m)) {
      m.classList.add("is-entering");
      m.addEventListener("animationend", () => m.classList.remove("is-entering"), { once: true });
    }
    items = [];
    blocks = [];
    rowRevealUntil = 0;
    // 排在 AI 訊息後面的使用者泡泡沒有字要打，輪到時直接進場。
    if (m.classList.contains("is-user")) return;
    if (!revised.has(m)) {
      m.querySelectorAll("[data-chat-typewriter-group]:has(.subject-preview-person)").forEach(group => group.classList.add("is-typewriter-pending"));
      m.querySelectorAll(".subject-preview-person").forEach(row => row.classList.add("is-typewriter-pending"));
    }
    const body = m.querySelector(".chat-body");
    blocks = body ? holdBlocks(body) : [];
    b = 0;
    for (const p of Array.from(m.querySelectorAll<HTMLElement>(TYPE_TEXT))) {
      if (p.dataset.typed !== undefined && p.dataset.typed === p.textContent) continue;
      const walk = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
      for (let t = walk.nextNode() as Text | null; t; t = walk.nextNode() as Text | null) {
        if (!t.data) continue;
        items.push({ node: t, chars: Array.from(t.data), shown: "", row: p.closest<HTMLElement>(".subject-preview-person") });
        t.data = "";
      }
    }
    // 第一段要打的字之前的卡片、按鈕本來就該看得到（換內容的訊息裡是已經顯示
    // 過的），不先藏起來。
    if (items.length) blocks = blocks.filter((block) => block.compareDocumentPosition(items[0].node) & Node.DOCUMENT_POSITION_PRECEDING);
    else blocks = [];
    for (const block of blocks) block.setAttribute("data-typewriter-hold", "");
    k = 0;
    n = 0;
    const total = items.reduce((sum, it) => sum + it.chars.length, 0);
    step = Math.max(1, Math.ceil((total * TYPE_MS) / TYPE_MAX_MS));
  };
  const tick = (now: number) => {
    if (now >= next) {
      next = now + TYPE_MS;
      let left = step;
      while (left > 0 && k < items.length) {
        const it = items[k];
        // 打到下一段字之前，夾在中間的卡片、按鈕先出來，停一下再接著打。
        let shown = false;
        while (b < blocks.length && blocks[b].compareDocumentPosition(it.node) & Node.DOCUMENT_POSITION_FOLLOWING) {
          blocks[b++].setAttribute("data-typewriter-hold", "shown");
          shown = true;
        }
        if (shown) {
          next = now + TYPE_GAP_MS;
          break;
        }
        if (it.row?.classList.contains("is-typewriter-pending")) {
          it.row.closest("[data-chat-typewriter-group]")?.classList.remove("is-typewriter-pending");
          it.row.classList.remove("is-typewriter-pending");
          it.row.classList.add("is-typewriter-revealed");
          rowRevealUntil = now + 160;
        }
        if (it.node.data !== it.shown) {
          k++;
          n = 0;
          continue;
        }
        n = Math.min(it.chars.length, n + left);
        left -= n - Array.from(it.shown).length;
        it.shown = it.node.data = it.chars.slice(0, n).join("");
        if (n >= it.chars.length) {
          k++;
          n = 0;
        }
      }
      if (k >= items.length && now >= rowRevealUntil) {
        while (b < blocks.length) blocks[b++].setAttribute("data-typewriter-hold", "shown");
        finishMessage(msgs[i]);
        if (++i >= msgs.length) {
          raf = 0;
          return;
        }
        next = now + TYPE_GAP_MS;
        start();
      }
    }
    raf = requestAnimationFrame(tick);
  };
  start();
  raf = requestAnimationFrame(tick);
  return () => {
    if (!raf) return;
    cancelAnimationFrame(raf);
    raf = 0;
    for (const it of items.slice(k)) if (it.node.data === it.shown) it.node.data = it.chars.join("");
    for (const m of msgs.slice(i)) finishMessage(m);
  };
}

// The way between this page and 面板模式: 對話 is where you are, 面板 the link
// over. The switch stays available while work is running: changing views only
// carries the current photo and handoff state across. onLeave hands the photo's
// adjustments over right before.
// Styles in styles.css (.mode-switch), shared with 面板模式's own copy.
// onLeave 回傳 Promise 時（上傳頁要先把照片存起來），等它做完才換頁。
export type PanelMode = { href: string; busy: boolean; onLeave: () => void | Promise<void> };

// mode 沒給：還沒有可以開的面板（還沒分析或美化），「面板」那段灰掉、不能按。
export function ModeSwitch({ mode, class: cls }: { mode?: PanelMode; class?: string }) {
  return (
    <nav class={`mode-switch${cls ? ` ${cls}` : ""}`} aria-label={t("檢視方式")}>
      <span class="mode-switch-opt is-active" aria-current="page">
        <Icon name="messages-circle" size={14} />
        {t("對話")}
      </span>
      {!mode ? (
        // 還沒選照片就沒有東西能帶到面板；一有照片後，任何流程狀態都能切換。
        <span class="mode-switch-opt" aria-disabled="true" title={t("選好照片後就能切換")}>
          <Icon name="layout" size={14} />
          {t("編輯器")}
        </span>
      ) : (
        <a
          class="mode-switch-opt"
          href={mode.href}
          title={t("切換到面板模式")}
          onClick={(e) => {
            // 切到面板模式會把東西帶過去（handoff），不跳「離開網站？」的提醒。
            markLeavingForPanel();
            const done = mode.onLeave();
            if (done && typeof (done as Promise<void>).then === "function") {
              e.preventDefault();
              (done as Promise<void>).finally(() => location.assign(mode.href));
            }
          }}
        >
          <Icon name="layout" size={14} />
          {t("編輯器")}
        </a>
      )}
    </nav>
  );
}

export function ChatPanel(props: {
  sub: string;
  class?: string;
  footer: ComponentChildren;
  children: ComponentChildren;
  // Changes when an answer lands at the end: go back to the bottom and stick
  // there again, whether or not the user had scrolled up.
  toBottom?: number;
  // Changes when a result lands (the opening after the analysis, 第 N 版完成):
  // go to the start of what just arrived, whether or not the user had scrolled
  // up. The start, not the bottom -- a result card is often taller than the
  // timeline, and the bottom would show only the end of its note.
  toStart?: string | null;
  // 整串對話還沒畫完：有些訊息要等晚到的資料（照片的分析）才出得來。那時才冒
  // 出來的是舊紀錄，不是新的一輪 -- 等畫完再當成第一次載入，停在最底。
  settling?: boolean;
  // 這串對話打開之後，AI 有沒有講了新的話。重新整理或從清單打開時整串是分好
  // 幾次畫出來的（session、分析各自晚到），那段時間冒出來的都是舊紀錄，不打字。
  live?: boolean;
  onDropFile?: (file: File) => void;
  // 有給就整批丟進來的檔案都交給它（上傳頁可以一次多張）；沒給就只取第一張。
  onDropFiles?: (files: File[]) => void;
  // The photo being worked on, before the session starts. On a phone, once its
  // big copy in the timeline (UploadBubble with onOpen) scrolls off the top,
  // the head gives its place to a bar showing it; tapping either opens it.
  // always: the session page, where there is no such copy -- the bar stays up
  // from the start, titled with the version on screen.
  photo?: { src: string; onOpen: () => void; title?: string; note?: string; always?: boolean };
  // The session page: the head's title becomes a 對話｜面板 switch, so the way
  // over to 面板模式 sits on the conversation itself instead of among the
  // header's buttons. On the phone the head is put away and the photo carries
  // the same switch instead (ResultsCanvas).
  panelMode?: PanelMode;
  // Above the conversation and outside its scroll: stays in view while the
  // timeline follows its newest message (the batch overview's photo grid).
  top?: ComponentChildren;
  // 桌機：放在對話卡片裡、標題列底下左邊的預覽區（上傳頁的照片）；沒給就不佔位，對話撐滿。
  stage?: ComponentChildren;
}) {
  const timeline = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [photoOut, setPhotoOut] = useState(false);
  const photoSrc = props.photo?.src;
  const photoUp = !!props.photo && (photoOut || !!props.photo.always);
  useEffect(() => {
    const el = timeline.current;
    const shot = el?.querySelector(".chat-upload.is-zoom .chat-upload-shot");
    setPhotoOut(false);
    if (!el || !shot) return;
    // Out past the top only: below the fold it has not been seen yet.
    const io = new IntersectionObserver(
      ([e]) => setPhotoOut(!e.isIntersecting && e.boundingClientRect.top < (e.rootBounds?.top ?? 0)),
      { root: el },
    );
    io.observe(shot);
    return () => io.disconnect();
  }, [photoSrc]);

  // Stick to the bottom as messages arrive, unless the user scrolled up.
  const pinned = useRef(true);
  // The first message of a result that just landed (toStart), or of the AI's
  // reply: the timeline stays on its start until the user scrolls or sends.
  const anchor = useRef<HTMLElement | null>(null);
  // 看過的訊息，連同當時的 data-chat-revision：同一則換了 revision 就是內容換了。
  const seen = useRef(new WeakMap<Element, string | null>());
  const lastStart = useRef(props.toStart);
  const typing = useRef<(() => void) | null>(null);
  useEffect(() => () => typing.current?.(), []);
  const align = () => {
    const el = timeline.current;
    const a = anchor.current;
    if (!el || !a || !a.isConnected) return;
    el.scrollTop += a.getBoundingClientRect().top - el.getBoundingClientRect().top - 8;
  };
  useLayoutEffect(() => {
    const el = timeline.current;
    if (!el) return;
    if (props.settling) {
      if (pinned.current) el.scrollTop = el.scrollHeight;
      return;
    }
    const msgs = Array.from(el.querySelectorAll(":scope > .chat-msg, :scope > .settings-turn > .chat-msg, :scope > .note-turn > .chat-msg"));
    const fresh = msgs.filter((m) => !seen.current.has(m));
    const revised = msgs.filter((m) => seen.current.has(m) && seen.current.get(m) !== m.getAttribute("data-chat-revision"));
    if (props.toStart !== lastStart.current) {
      lastStart.current = props.toStart;
      anchor.current = (fresh[0] || msgs[msgs.length - 1] || null) as HTMLElement | null;
    } else if (fresh.length && fresh.length < msgs.length) {
      // 使用者送出新的一句：回到跟著末端。AI 接著回話：停在這一輪回覆的開頭，
      // 讓人從上往下讀 -- 長回覆捲到底只看得到結尾。同一輪後面再補的卡片不搶走
      // 開頭；「正在回覆」的泡泡被換掉時，改對齊到換上來的那則。
      // (fresh 等於全部 = 第一次載入或換了一串對話，照舊停在最底。)
      const reply = fresh.find((m) => !m.classList.contains("is-user")) as HTMLElement | undefined;
      if (fresh.some((m) => m.classList.contains("is-user"))) {
        anchor.current = null;
        pinned.current = true;
      }
      if (reply && (pinned.current || anchor.current) && !anchor.current?.isConnected) anchor.current = reply;
    }
    // 新進來的 AI 訊息一字一字打出來（typeMessages）。只在對話進行中：第一次
    // 載入或換一串對話（fresh 等於全部）直接整串顯示。又有新訊息進來時，還沒
    // 打完的先一口氣補完，再打新的。使用者自己的訊息不等。打開之後陸續補上的
    // 舊紀錄（live 還是 false）一樣直接顯示。
    if ((fresh.length || revised.length) && fresh.length < msgs.length && (props.live ?? userActed)) {
      typing.current?.();
      typing.current = null;
      const typed = (m: Element) => !m.classList.contains("is-user") && (!m.hasAttribute("data-chat-atomic") || m.hasAttribute("data-chat-typewriter"));
      // 使用者的泡泡本來不用等；但排在要打字的 AI 訊息後面的（總覽發過來的那
      // 句，切到這張時跟開場白一起進來）要照順序排，不然它先冒出來，開場白才
      // 打在它上面。
      const first = fresh.findIndex(typed);
      const queued = first < 0 ? [] : fresh.slice(first).filter((m) => typed(m) || m.classList.contains("is-user"));
      // 換了內容的訊息排在同一輪，照畫面順序。
      const ai = msgs.filter((m) => queued.includes(m) || (revised.includes(m) && typed(m))) as HTMLElement[];
      if (ai.length && !matchMedia("(prefers-reduced-motion: reduce)").matches) typing.current = typeMessages(ai, new Set(revised));
      else ai.forEach(finishMessage);
    } else if (fresh.length || revised.length) {
      // Restored history has already happened; publish its controls without replay.
      [...fresh, ...revised].filter(m => !m.classList.contains("is-user")).forEach(m => finishMessage(m as HTMLElement));
    }
    for (const m of msgs) seen.current.set(m, m.getAttribute("data-chat-revision"));
    if (anchor.current) align();
    else if (pinned.current) el.scrollTop = el.scrollHeight;
  });
  useLayoutEffect(() => {
    anchor.current = null;
    pinned.current = true;
    const el = timeline.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [props.toBottom]);
  useEffect(() => {
    const el = timeline.current;
    if (!el) return;
    // Late-loading images and cards change the height after render.
    const ro = new ResizeObserver(() => {
      if (anchor.current) align();
      else if (pinned.current) el.scrollTop = el.scrollHeight;
    });
    for (const child of Array.from(el.children)) ro.observe(child);
    const mo = new MutationObserver(() => {
      for (const child of Array.from(el.children)) ro.observe(child);
    });
    mo.observe(el, { childList: true });
    return () => {
      ro.disconnect();
      mo.disconnect();
    };
  }, []);

  const drop = props.onDropFiles || props.onDropFile;
  return (
    <div
      class={`chat-panel${props.class ? ` ${props.class}` : ""}${dragging ? " is-dragging" : ""}${
        props.photo ? " has-photo" : ""
      }${props.stage ? " has-stage" : ""}${photoUp ? " is-photo-out" : ""}`}
      onDragOver={
        drop &&
        ((e) => {
          e.preventDefault();
          setDragging(true);
        })
      }
      onDragLeave={drop && (() => setDragging(false))}
      onDrop={
        drop &&
        ((e) => {
          e.preventDefault();
          setDragging(false);
          const files = Array.from(e.dataTransfer?.files || []);
          if (props.onDropFiles) {
            if (files.length) props.onDropFiles(files);
          } else if (files[0]) props.onDropFile?.(files[0]);
        })
      }
    >
      {/* 左邊固定是「對話」標題；右邊是副標（新對話…）和「對話｜面板」切換。
          還沒有面板可開（沒給 panelMode）時切換照樣在，「面板」灰掉。 */}
      <div class="chat-panel-head">
        <Icon name="messages-circle" size={14} />
        <span class="chat-panel-title">{t("對話")}</span>
        {/* 標題旁的副標（New Chat／日期·張數／第 N 張…）沒有實際用處，不顯示。 */}
        <ModeSwitch mode={props.panelMode} />
      </div>
      {props.photo && (
        <button type="button" class="chat-panel-photo" onClick={props.photo.onOpen} tabIndex={photoUp ? 0 : -1}>
          <img src={props.photo.src} alt="" />
          <span class="chat-panel-photo-text">
            <b>{props.photo.title || t("這次要修的照片")}</b>
            <small>{props.photo.note || t("點一下看大圖")}</small>
          </span>
          <Icon name="expand" size={16} />
        </button>
      )}
      {props.stage && <div class="chat-panel-stage">{props.stage}</div>}
      {props.top && <div class="chat-panel-top">{props.top}</div>}
      <div
        class="chat-timeline"
        ref={timeline}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        // The user taking the scroll over lets go of the result's start.
        onWheel={() => (anchor.current = null)}
        onTouchStart={() => (anchor.current = null)}
      >
        {props.children}
      </div>
      {props.footer}
    </div>
  );
}

export function UserMsg({ meta, children }: { meta?: string; children: ComponentChildren }) {
  return (
    <div class="chat-msg is-user">
      <div class="chat-body">
        {children}
        {meta && <span class="chat-meta">{meta}</span>}
      </div>
    </div>
  );
}

export function AssistantMsg({ children, atomic, animate, onTypingComplete, revision }: {
  children: ComponentChildren; atomic?: boolean; animate?: boolean; onTypingComplete?: () => void;
  // 內容會在原地換掉的訊息（「正在整理…」換成摘要或這版的說明）：換了就換這個值，
  // ChatPanel 把換上來的字打出來。
  revision?: string;
}) {
  const element = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const message = element.current;
    if (!message || !onTypingComplete) return;
    message.addEventListener("chat-typewriter-complete", onTypingComplete);
    return () => message.removeEventListener("chat-typewriter-complete", onTypingComplete);
  }, [onTypingComplete]);
  return (
    <div ref={element} class="chat-msg" data-chat-atomic={atomic ? "true" : undefined} data-chat-typewriter={animate ? "true" : undefined}
      data-chat-revision={revision}>
      {/* 簡報 p6 C05：YouCam AI Agent 頭像（品牌漸層圓底＋白色機器人，Lucide「bot」，ISC）。不另外寫名字。 */}
      <div class="chat-avatar" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 8V4H8" />
          <rect width="16" height="12" x="4" y="8" rx="2" />
          <path d="M2 14h2" /><path d="M20 14h2" /><path d="M15 13v2" /><path d="M9 13v2" />
        </svg>
      </div>
      <div class="chat-body">{children}</div>
    </div>
  );
}

export function ClickBubble({ icon, label }: { icon: string; label: string }) {
  return (
    <div class="chat-bubble is-click">
      <Icon name={icon} />
      {shownLabel(label)}
    </div>
  );
}

// With onOpen the photo is a button that opens it big -- on a phone it is
// also drawn big here, since there is no stage beside the conversation.
export function UploadBubble({ src, text, onOpen }: { src: string; text: string; onOpen?: () => void }) {
  return (
    <div class={`chat-upload${onOpen ? " is-zoom" : ""}`}>
      {onOpen ? (
        <button type="button" class="chat-upload-shot" aria-label={t("看大圖")} onClick={onOpen}>
          <img src={src} alt="" />
          <span class="chat-upload-zoom" aria-hidden="true">
            <Icon name="expand" size={14} />
          </span>
        </button>
      ) : (
        <img src={src} alt="" />
      )}
      <div class="chat-bubble">{text}</div>
    </div>
  );
}

// 多張一起上傳：前幾張並排示意，多的寫 +N。簡報 p4 S05：點 +N 就在同一則訊息裡展開全部（格狀），
// 底下一顆「顯示較少」收回去。
export function BatchUploadBubble({ srcs, text, fullSrcs }: { srcs: string[]; text: string; fullSrcs?: string[] }) {
  const [open, setOpen] = useState(false);
  // 點縮圖放大：fullSrcs 是跟 srcs 同順序的原圖網址；沒給就放大縮圖本身。
  const [viewing, setViewing] = useState<number | null>(null);
  const bigOf = (i: number) => (fullSrcs && fullSrcs[i]) || srcs[i];
  const shown = srcs.slice(0, 4);
  const more = srcs.length - shown.length;
  const viewer = (
    <PhotoViewer
      items={viewing === null ? undefined : srcs.map((s, i) => ({ src: bigOf(i), thumb: s }))}
      index={viewing ?? 0}
      onClose={() => setViewing(null)}
    />
  );
  if (open && more > 0) {
    return (
      <div class="chat-upload is-batch is-open">
        <div class="chat-upload-card">
          <p class="chat-upload-card-title">{text}</p>
          <div class="chat-upload-grid">
            {srcs.map((s, i) => (
              <button key={s} type="button" class="chat-upload-thumb" aria-label={t("看大圖")} onClick={() => setViewing(i)}>
                <img src={s} alt="" loading="lazy" />
              </button>
            ))}
          </div>
          {/* YCO Button：Secondary · Neutral · Tiny。 */}
          <button type="button" class="ycoButton variant_secondary tone_neutral size_tiny chat-upload-less" onClick={() => setOpen(false)}>
            <span class="label">{t("顯示較少")}</span>
          </button>
        </div>
        {viewer}
      </div>
    );
  }
  return (
    <div class="chat-upload is-batch">
      {shown.length > 0 && (
        <div class="chat-upload-stack">
          {shown.map((s, i) => (
            <button key={s} type="button" class="chat-upload-thumb" aria-label={t("看大圖")} onClick={() => setViewing(i)}>
              <img src={s} alt="" />
            </button>
          ))}
          {more > 0 && (
            <button type="button" class="chat-upload-more" aria-label={t("顯示全部 {n} 張", { n: srcs.length })} onClick={() => setOpen(true)}>
              +{more}
            </button>
          )}
        </div>
      )}
      <div class="chat-bubble">{text}</div>
      {viewer}
    </div>
  );
}

// 大圖檢視：外觀和操作都在 frontend/photo-lightbox.js（window.PMLightbox），
// Chat 和 Editor 共用同一份。單張給 src；多張給 items + index（箭頭、縮圖列、←／→ 都由它處理）。
declare global {
  interface Window {
    PMLightbox?: { open(o: unknown): void; close(): void; isOpen(): boolean };
  }
}
export function PhotoViewer({ src, items, index = 0, tag, onClose }: {
  src?: string | null;
  items?: { src: string; thumb?: string; tag?: string; group?: string | number }[];
  index?: number;
  tag?: string;
  onClose: () => void;
}) {
  const list = items && items.length ? items : src ? [{ src, tag }] : [];
  const key = list.map((x) => x.src).join("|") + "#" + index;
  useEffect(() => {
    if (!list.length) return;
    window.PMLightbox?.open({
      items: list.map((x) => ({ ...x, tag: x.tag || tag, download: { href: x.src, name: "photomaster.jpg" } })),
      index,
      label: t("照片大圖"),
      onClose,
    });
    return () => window.PMLightbox?.close();
  }, [key]);
  return null;
}

export function Chip(props: {
  label: string;
  icon?: string;
  kind?: "primary" | "ghost" | "picked";
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
  // The turn this chip belonged to has been answered. It stays in the
  // timeline as the record of what was offered and what was taken, so it is
  // no longer a control at all -- not a disabled one, which would read as
  // "temporarily unavailable" rather than as something already past.
  settled?: boolean;
  // 有東西等著這顆鍵處理（例如改了還沒美化）：右上角一顆小圓點，跟選項裡「建議
  // 調整」的點同一個意思。字放在 title 和 aria-label，滑過去或報讀時才說。
  badge?: string;
}) {
  const cls = `chat-chip${props.kind ? ` is-${props.kind}` : ""}${props.settled ? " is-static" : ""}`;
  const title = props.badge || props.title;
  const badge = props.badge && <span class="chat-chip-badge" role="img" aria-label={props.badge} />;
  if (props.settled) {
    return (
      <span class={cls} title={title}>
        {props.icon && <Icon name={props.icon} />}
        {shownLabel(props.label)}
        {badge}
      </span>
    );
  }
  return (
    <button type="button" class={cls} disabled={props.disabled} title={title} onClick={props.onClick}>
      {props.icon && <Icon name={props.icon} />}
      {shownLabel(props.label)}
      {badge}
    </button>
  );
}

// 簡報 p5 A01：「分析」和「一鍵美化」同等份量的兩顆 YCO Button（Primary · Neutral：黑底白字＋圖示）。
// settled：這一輪答過了，留在對話裡當紀錄，不再是按鈕；picked 是當時按的那顆（維持黑底），沒選的那顆變淡。
export function ActionChoice(props: {
  label: string;
  icon: string;
  title?: string;
  picked?: boolean;
  settled?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  const dim = props.settled && !props.picked;
  const cls = `ycoButton ${dim ? "variant_secondary tone_neutral" : "variant_primary tone_neutral"} size_small chat-action-btn${props.settled ? " is-static" : ""}`;
  const inner = (
    <>
      <span class="buttonIcon"><Icon name={props.icon} size={16} /></span>
      <span class="label">{shownLabel(props.label)}</span>
    </>
  );
  return (
    <div class={`chat-action-choice${props.picked ? " is-picked" : ""}`}>
      {props.settled ? (
        <span class={cls} title={props.title}>{inner}</span>
      ) : (
        <button type="button" class={cls} title={props.title} disabled={props.disabled} onClick={props.onClick}>
          {inner}
        </button>
      )}
    </div>
  );
}

// 輸入框上面那條只剩主角挑選。「調整／美化」那些動作改掛在最新一則回話底下，
// 跟其他選項一樣是對話的一部分，不再釘在這裡。
export function FootBar({ faces }: { faces?: ComponentChildren }) {
  if (!faces) return null;
  return <div class="chat-footbar">{faces}</div>;
}

// The faces sit in a row while they fit. When they do not, the row folds into
// one button -- a couple of faces and +N -- that opens every face upward in a
// grid: a scroller hid the rest without a hint and left the last face cut in
// half. The fit is measured on the picker's own width, which is whatever the
// strip leaves after the actions, so it does not depend on what is inside.
// Each tap adds that face to the pick or takes it back out; the pick keeps the
// order the faces were tapped in. Picking several is the point, so a tap does
// not close the panel -- its X, the button again, Esc or a tap outside do.
const FACE_GAP = 8; // --sp-2
const FOLDED_FACES = 2;

export function FacePicker(props: {
  people: (FaceRef & { subjectIndex: number })[];
  pointed: number[];
  onPoint?: (subjectIndices: number[]) => void;
}) {
  const { people } = props;
  const box = useRef<HTMLDivElement>(null);
  const label = useRef<HTMLSpanElement>(null);
  const [folded, setFolded] = useState(false);
  const [open, setOpen] = useState(false);
  // The pick when the panel opened. Closing it on purpose (X, the button, Esc)
  // moves to the field the way a tap in the row does, if the pick changed; a
  // tap outside landed on something else, so it does not.
  const opened = useRef("");
  const key = props.pointed.join(",");
  const close = (focus: boolean) => {
    setOpen(false);
    if (focus && key && key !== opened.current) {
      box.current?.closest(".chat-panel")?.querySelector<HTMLInputElement>(".chat-composer-field.is-live input")?.focus();
    }
  };

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    // 一顆頭像的寬：--footbar-face（chat.css，桌機 40、手機 28）加上 3px 的邊和 1px 的框，左右各一。
    const fit = () => {
      const face = (parseFloat(getComputedStyle(el).getPropertyValue("--footbar-face")) || 40) + 8;
      const need = people.length * face + (people.length - 1) * FACE_GAP;
      setFolded(need > el.clientWidth - (label.current?.offsetWidth || 0) - FACE_GAP);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [people.length]);

  useEffect(() => {
    if (!folded) setOpen(false);
  }, [folded]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      if (!box.current?.contains(e.target as Node)) close(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close(true);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", esc);
    };
  }, [open, key]);

  const faces = people.map((p) => {
    const picked = props.pointed.includes(p.subjectIndex);
    return (
      <FaceChip
        face={p}
        avatarOnly
        preview
        picked={picked}
        title={picked ? t("取消指定{v1}", { v1: faceLabel(p) }) : t("指定{v1}", { v1: faceLabel(p) })}
        onClick={() =>
          props.onPoint?.(
            picked ? props.pointed.filter((i) => i !== p.subjectIndex) : [...props.pointed, p.subjectIndex],
          )
        }
      />
    );
  });
  const more = people.length - FOLDED_FACES;

  return (
    <div class="chat-face-picker" ref={box}>
      <span class="chat-face-picker-label" ref={label}>
        {t("指定對象")}
      </span>
      {folded ? (
        <>
          <button
            type="button"
            class={`chat-face-fold${open ? " is-open" : ""}`}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-label={t("指定對象，共 {length} 位", { length: people.length })}
            onClick={() => {
              if (open) return close(true);
              opened.current = key;
              setOpen(true);
            }}
          >
            <span class="chat-face-fold-faces">
              {people.slice(0, FOLDED_FACES).map((p) => (
                <FaceChip face={p} avatarOnly />
              ))}
            </span>
            {more > 0 && <span class="chat-face-fold-more">+{more}</span>}
            <Icon name="chevron-up" size={14} />
          </button>
          {open && (
            <div class="chat-face-panel" role="dialog" aria-label={t("指定對象")}>
              <div class="chat-face-panel-head">
                <span>{t("指定對象")}</span>
                <button type="button" class="chat-face-panel-x" aria-label={t("關閉")} onClick={() => close(true)}>
                  <Icon name="x" size={16} />
                </button>
              </div>
              <div class="chat-face-grid">{faces}</div>
            </div>
          )}
        </>
      ) : (
        <div class="chat-face-track">{faces}</div>
      )}
    </div>
  );
}

// 處理中的工作紀錄：隔幾秒多講一句，講過的留在上面，不是換掉 -- 等的人往上
// 看得到已經做過哪些事。整份講完就停在最後一句，不再重複（舊版 app.js 會在最後
// 兩句之間來回，疊成紀錄後那樣會一直長出一樣的話）。
// stage 是現在在做哪一段（分析、重新構圖、美化）；一鍵美化會從分析直接接到美化，
// 前一段講過的照樣留著，只有整輪忙完（stage 變回 null）才清掉，下一輪從頭講。
// 每句隔多久是亂數，3～7 秒：固定 5 秒一跳，看起來就是計時器，不像在做事。平均
// 還是 5 秒，分析那份 12 句照樣大約一分鐘講完。
const STEP_MIN_MS = 3000;
const STEP_MAX_MS = 7000;

// 每一句出現在這一段開始後的第幾毫秒，換段時一次抽好。
function stepMarks(count: number): number[] {
  const marks: number[] = [];
  let at = 0;
  for (let i = 1; i < count; i++) {
    at += STEP_MIN_MS + Math.random() * (STEP_MAX_MS - STEP_MIN_MS);
    marks.push(at);
  }
  return marks;
}

// 講到第幾句照這一段開始了多久算，不靠計數器：輪詢每 2 秒就重畫一次，算時間的話
// 重畫幾次都不會多跳一句。
// 每一段各是一張卡：分析做完，它那張卡就收掉、換成分析結果，美化從新的一張卡重新
// 講、秒數也從頭算 -- 接著疊在同一張卡裡的話，分析結果會被一路往上擠。
export function useWorkLog(
  stage: string | null,
  lines: string[],
): { done: string[]; now: string; since: number } {
  const log = useRef({
    stage: null as string | null,
    start: 0,
    marks: [] as number[],
    lines: [] as string[],
  });
  const [, tick] = useState(0);
  const said = (l: typeof log.current) => {
    const elapsed = Date.now() - l.start;
    return Math.min(l.lines.length, 1 + l.marks.filter((m) => m <= elapsed).length);
  };
  const l = log.current;
  if (stage !== l.stage) {
    l.stage = stage;
    l.start = Date.now();
    l.marks = stepMarks(lines.length);
  }
  l.lines = lines;
  // 排到下一句該出現的時間點重畫一次，講完最後一句就不再排。
  useEffect(() => {
    if (!stage) return;
    let t = 0;
    const next = () => {
      const elapsed = Date.now() - log.current.start;
      const at = log.current.marks.find((m) => m > elapsed);
      if (at == null) return;
      t = window.setTimeout(() => {
        tick((k) => k + 1);
        next();
      }, at - elapsed + 20);
    };
    next();
    return () => clearTimeout(t);
  }, [stage]);
  const n = said(l);
  if (!stage || !n) return { done: [], now: "", since: 0 };
  return { done: lines.slice(0, n - 1), now: lines[n - 1], since: l.start };
}

// 處理中的那張卡。左邊畫布上壓著一層轉環，上面已經寫了「正在分析／美化照片」，
// 這裡不再放標題和進度條；整張卡的工作就是講細節。跟面板模式的進度句同一套：
// 只留最近三句，最新的在最下面、帶一道掃過的光，往上一句淡一點、再上一句更淡；
// 新的一句從下面滑進來，舊的往上讓位。最後一句講完還沒好，就每隔幾秒再掃一次光，
// 讓人知道還在跑。最新那句右邊是已經等了幾秒。
// 已經等了多久。自己每秒重畫，不拉著整個 SessionView 一起；從前端看到這輪開始
// 的那一刻算 -- 後端的照片狀態沒有帶開始時間，重新整理後跟工作紀錄一樣從頭算。
function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const sec = Math.max(0, Math.floor((now - since) / 1000));
  const text = sec < 60 ? t("{sec} 秒", { sec }) : t("{v1} 分 {v2} 秒", { v1: Math.floor(sec / 60), v2: String(sec % 60).padStart(2, "0") });
  return <span class="chat-working-time">{text}</span>;
}

// title：簡報 p6 A03，詳細進度集中在對話裡 -- 卡片上方先講在做什麼（「正在分析照片…」）加一條進度條，
// 底下再一句句講細節；畫布上只留簡短的階段標籤。後端沒有百分比，進度條是不確定型（來回跑）。
export function WorkingCard({ done, now, since, title }: { done: string[]; now: string; since: number; title?: string }) {
  const rows = [...done.slice(-2), now];
  // 每一句在這一段裡是第幾句，當 key：同一句換位置時是同一個節點，才移得過去。
  const first = done.length + 1 - rows.length;
  const list = useRef<HTMLOListElement>(null);
  const tops = useRef(new Map<number, number>());
  const stage = useRef(since);
  useLayoutEffect(() => {
    const el = list.current;
    if (!el) return;
    // 換了一段（分析 → 美化）就從頭排，不拿上一段的位置來移。
    if (stage.current !== since) {
      stage.current = since;
      tops.current = new Map();
    }
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const next = new Map<number, number>();
    el.querySelectorAll<HTMLLIElement>("li[data-i]").forEach((li) => {
      const i = Number(li.dataset.i);
      next.set(i, li.offsetTop);
      const before = tops.current.get(i);
      if (!still && before != null && before !== li.offsetTop) {
        li.animate?.([{ transform: `translateY(${before - li.offsetTop}px)` }, { transform: "none" }], {
          duration: 350,
          easing: "cubic-bezier(.2,.7,.2,1)",
        });
      }
    });
    tops.current = next;
  }, [done.length, now, since]);
  // 一句停得比換句的最長間隔還久，就是講到最後一句了：改成循環掃光。
  const [holding, setHolding] = useState(false);
  useEffect(() => {
    setHolding(false);
    const t = window.setTimeout(() => setHolding(true), STEP_MAX_MS + 1500);
    return () => clearTimeout(t);
  }, [now]);
  return (
    <div class="chat-card chat-working">
      {title && (
        <div class="chat-working-head">
          <p class="chat-working-title">{title}…</p>
          <div class="chat-working-bar" role="progressbar" aria-label={title} />
        </div>
      )}
      <ol ref={list}>
        {rows.map((t, k) => {
          const age = rows.length - 1 - k;
          const cls = age === 0 ? (holding ? "is-now is-holding" : "is-now") : age === 1 ? "is-prev" : "is-old";
          return (
            <li key={first + k} data-i={first + k} class={cls}>
              <span class="chat-working-text">{t}</span>
              {age === 0 && <Elapsed since={since} />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const qaCache = new Map<string, { verdict: string; score: string; pass: boolean }>();

// next：這是最新的一版、底下正掛著「看美化項目」時才講下一步。舊的結果卡不講，不然
// 往上捲會看到每一版都在叫人去按一顆已經不在的按鈕。
// note：開了「詳細解說」時，後端對照原圖與成品寫的點評；沒有就不顯示。成品先出來，
// 點評隨後才到，noting 是還在寫：先放一行淡字佔位，操作提示和下一步等點評到了
// 再接在它後面，順序才不會先出提示、說明再插進來。
// shown：這一版正顯示在畫布上。是的話跟畫布用同一張大圖，不多抓；其他版只顯示成小卡，
// 用中尺寸預覽就夠清楚，不必每次打開都把每一版的大圖抓一遍。
export function ResultCard({ job, ordinal, from, failed, next, continuable, note, noting, onOpen, shown }: {
  job: string;
  ordinal: number;
  // 接著第幾版調整出來的；從原圖產生的不給。
  from?: number;
  failed: number;
  next?: boolean;
  // 能在輸入框上方換成某一版接著改。
  continuable?: boolean;
  note?: string;
  noting?: boolean;
  // 手機上旁邊沒有比較卡，成品放大、點了開大圖檢視。
  onOpen?: () => void;
  shown?: boolean;
}) {
  const src = shown ? urls.resultImage(job) : urls.resultPreview(job);
  const [qa, setQa] = useState(qaCache.get(job) || null);
  // 品質評語要抓整份結果（伺服器要讀一陣子），而且多半根本沒跑品質檢查：捲到這張卡
  // 附近才去抓，對話上面那幾版舊的就不用每次打開都抓一輪。
  const card = useRef<HTMLElement>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = card.current;
    if (near || !el) return;
    const io = new IntersectionObserver((seen) => seen.some((e) => e.isIntersecting) && setNear(true), { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  useEffect(() => {
    if (qa || !near) return;
    let alive = true;
    api
      .result(job)
      .then((r) => {
        const qc = r?.result?.qualityCheck;
        if (!qc) return;
        const v = {
          verdict: qaText(qc),
          score: qc.score != null ? `${qc.score}/10` : "",
          pass: qc.pass !== false,
        };
        qaCache.set(job, v);
        if (alive) setQa(v);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [job, near]);

  // The finished photo is the moment the whole flow was for, so it gets a
  // real look at it rather than the 84x66 chip it used to be.
  return (
    <figure class="chat-result" ref={card}>
      {onOpen ? (
        <button type="button" class="chat-result-shot" aria-label={t("看第 {ordinal} 版大圖", { ordinal })} onClick={onOpen}>
          <img src={src} alt={t("第 {ordinal} 版美化結果", { ordinal })} />
          <span class="chat-upload-zoom" aria-hidden="true">
            <Icon name="expand" size={14} />
          </span>
        </button>
      ) : (
        <img src={src} alt={t("第 {ordinal} 版美化結果", { ordinal })} />
      )}
      <figcaption class="chat-result-cap">
        <h4>{from ? t("第 {ordinal} 版完成・由第 {from} 版調整", { ordinal, from }) : t("第 {ordinal} 版完成", { ordinal })}</h4>
        {note ? <p class="chat-text">{note}</p> : noting && <p class="chat-text chat-muted">{from ? t("正在對照第 {from} 版，整理這版的變化…", { from }) : t("正在對照原圖，整理這版的變化…")}</p>}
        {qa && (
          <div class="chat-verdict">
            {t("輸出評語")} <b class={qa.pass ? "" : "is-warn"}>{qa.verdict}</b>
            {qa.score && t(" 品質分數 {score}", { score: qa.score })}
          </div>
        )}
        {!noting && <p class="chat-text chat-muted">
          {failed > 0 && t("有 {failed} 張沒有成功，已略過。", { failed })}
          <span class="chat-result-hint-wide">{rtl ? t("右側可比較 Before / After，也可從「美化紀錄」切換版本。") : t("左側可比較 Before / After，也可從「美化紀錄」切換版本。")}</span>
          <span class="chat-result-hint-narrow">{t("點照片可以放大，按住不放可以看原圖。")}</span>
        </p>}
        {next && !noting && <p class="chat-text">{continuable
          ? t("想調整美化項目，按「看美化項目」；想用一樣的設定再產生一版，按「再美化」；想接續某一版修改，在輸入框上方把「原圖」換成那一版。也可以直接打字跟我說。")
          : t("想調整美化項目，按「看美化項目」；想用一樣的設定再產生一版，按「再美化」。也可以直接打字跟我說。")}</p>}
      </figcaption>
    </figure>
  );
}

// A face the chat can name: its crop and the name the panel's tab uses.
export interface FaceRef {
  // Empty before anything has been analysed: there is no name to print, and the
  // crop says who this is anyway. `where` then stands in wherever a word is
  // unavoidable -- a tooltip, a screen reader -- so a row of faces is never a
  // row of unlabelled buttons.
  name: string;
  thumb: string;
  where?: string;
}

const faceLabel = (f: FaceRef) => f.name || f.where || t("這個人");

// Wherever a person is written out -- the name the panel gives them, or where
// they stand -- their crop stands in for the words: the chat talks about people
// by face, the same token the picker under it uses. `prefix` is for a line that
// starts with who it is for (「第 2 位：」): the colon goes with the name, and
// 「A、B：」 puts the two faces side by side.
export function faceText(text: string, refs: { ref: string; face: FaceRef }[], prefix = false) {
  const sorted = refs.filter((r) => r.ref).sort((a, b) => b.ref.length - a.ref.length);
  if (!sorted.length) return [text];
  const out: (string | preact.JSX.Element)[] = [];
  let plain = "";
  for (let i = 0; i < text.length; ) {
    const hit = sorted.find((r) => text.startsWith(r.ref, i));
    if (!hit) {
      plain += text[i++];
      continue;
    }
    if (plain) out.push(plain);
    plain = "";
    out.push(<FaceChip face={hit.face} small />);
    i += hit.ref.length;
    if (!prefix) continue;
    if (text[i] === "、" && sorted.some((r) => text.startsWith(r.ref, i + 1))) {
      i++;
      continue;
    }
    if (text[i] === "：" || text[i] === ":") i++;
  }
  if (plain) out.push(plain);
  return out;
}

// The enlarged face over a small avatar: on mouse hover, or after a tap on touch
// screens. One at a time, attached to <body> so a scrolling row or panel cannot clip it.
const PREVIEW_HOVER_MS = 250;
const PREVIEW_LEAVE_MS = 80;
const PREVIEW_MARGIN = 8;
const mouseQuery = typeof matchMedia === "function" ? matchMedia("(hover:hover) and (pointer:fine)") : null;
const usesMouse = () => !!mouseQuery?.matches;

let previewEl: HTMLDivElement | null = null;
let previewAnchor: HTMLElement | null = null;
let previewTimer = 0;
let previewHiddenAt = 0;

function placeFacePreview() {
  const el = previewEl;
  const anchor = previewAnchor;
  if (!el || !anchor) return;
  if (!anchor.isConnected) return hideFacePreview();
  const r = anchor.getBoundingClientRect();
  const left = Math.min(Math.max(PREVIEW_MARGIN, r.left + r.width / 2 - el.offsetWidth / 2), innerWidth - el.offsetWidth - PREVIEW_MARGIN);
  const above = r.top - el.offsetHeight - PREVIEW_MARGIN;
  el.style.left = `${left}px`;
  el.style.top = `${above >= PREVIEW_MARGIN ? above : r.bottom + PREVIEW_MARGIN}px`;
}

function hideFacePreview() {
  clearTimeout(previewTimer);
  if (!previewEl) return;
  previewEl.remove();
  previewEl = null;
  previewAnchor = null;
  previewHiddenAt = Date.now();
}

// `go` turns the preview into a confirmation: its button runs the tap's action.
function showFacePreview(anchor: HTMLElement, face: FaceRef, go?: { label: string; run: () => void }) {
  hideFacePreview();
  if (!face.thumb) return;
  const el = document.createElement("div");
  el.className = `chat-face-preview${go ? " has-go" : ""}`;
  if (!go) el.setAttribute("aria-hidden", "true");
  const img = document.createElement("img");
  img.src = face.thumb;
  img.alt = "";
  el.appendChild(img);
  const label = face.name || face.where;
  if (label) {
    const name = document.createElement("div");
    name.className = "chat-face-preview-name";
    name.textContent = label;
    el.appendChild(name);
  }
  if (go) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-chip is-primary";
    button.textContent = go.label;
    button.onclick = () => {
      hideFacePreview();
      go.run();
    };
    el.appendChild(button);
  }
  document.body.appendChild(el);
  previewEl = el;
  previewAnchor = anchor;
  placeFacePreview();
}

function hoverFacePreview(anchor: HTMLElement, face: FaceRef) {
  clearTimeout(previewTimer);
  if (previewAnchor === anchor) return;
  // Moving straight on to the next face swaps the preview without the wait.
  if (previewEl || Date.now() - previewHiddenAt < 300) return showFacePreview(anchor, face);
  previewTimer = window.setTimeout(() => showFacePreview(anchor, face), PREVIEW_HOVER_MS);
}

function leaveFacePreview() {
  clearTimeout(previewTimer);
  if (previewEl) previewTimer = window.setTimeout(hideFacePreview, PREVIEW_LEAVE_MS);
}

if (typeof document !== "undefined") {
  document.addEventListener(
    "pointerdown",
    (e) => {
      const target = e.target as Node;
      if (!previewEl || previewEl.contains(target)) return;
      if (e.pointerType === "mouse" && previewAnchor?.contains(target)) return;
      hideFacePreview();
    },
    true,
  );
  document.addEventListener("keydown", (e) => e.key === "Escape" && hideFacePreview(), true);
  document.addEventListener("input", hideFacePreview, true);
  document.addEventListener("scroll", placeFacePreview, true);
  addEventListener("resize", placeFacePreview);
  visualViewport?.addEventListener("resize", placeFacePreview);
  visualViewport?.addEventListener("scroll", placeFacePreview);
}

export function FaceChip(props: {
  face: FaceRef;
  picked?: boolean;
  small?: boolean;
  // The face alone, at picker size: no name beside it, the crop is the label.
  avatarOnly?: boolean;
  title?: string;
  onClick?: () => void;
  // Enlarges the face on hover; on touch, after a tap that picks it.
  preview?: boolean;
  // Touch only: a tap opens the preview, and its button (labelled `title`) runs onClick.
  confirmOnTouch?: boolean;
}) {
  const { face } = props;
  const ref = useRef<HTMLElement>(null);
  const previews = !!props.preview && !!face.thumb;
  useEffect(
    () => () => {
      if (ref.current && previewAnchor === ref.current) hideFacePreview();
    },
    [],
  );
  const img = face.thumb ? <img src={face.thumb} alt="" /> : <span class="chat-face-blank" />;
  const cls =
    `chat-face-chip${props.picked ? " is-picked" : ""}` +
    `${props.small ? " is-small" : ""}${props.avatarOnly ? " is-avatar" : ""}`;
  const name = !props.small && !props.avatarOnly && face.name;
  const hover = previews
    ? {
        onPointerEnter: (e: PointerEvent) => {
          if (e.pointerType === "mouse" && usesMouse()) hoverFacePreview(e.currentTarget as HTMLElement, face);
        },
        onPointerLeave: (e: PointerEvent) => {
          if (e.pointerType === "mouse") leaveFacePreview();
        },
        onFocus: (e: FocusEvent) => {
          const el = e.currentTarget as HTMLElement;
          if (el.matches(":focus-visible")) showFacePreview(el, face);
        },
        onBlur: (e: FocusEvent) => {
          if (usesMouse() && !(e.currentTarget as HTMLElement).matches(":hover")) leaveFacePreview();
        },
      }
    : {};
  if (!props.onClick) {
    return (
      <span
        ref={ref}
        class={cls}
        title={previews ? undefined : faceLabel(face)}
        onClick={previews ? () => !usesMouse() && ref.current && showFacePreview(ref.current, face) : undefined}
        {...hover}
      >
        {img}
        {name}
      </span>
    );
  }
  const run = props.onClick;
  const click = () => {
    const el = ref.current;
    if (el && props.confirmOnTouch && face.thumb && !usesMouse()) {
      return showFacePreview(el, face, { label: props.title || faceLabel(face), run });
    }
    run();
    if (el && previews && !props.picked && !usesMouse()) showFacePreview(el, face);
  };
  return (
    <button
      ref={ref as preact.RefObject<HTMLButtonElement>}
      type="button"
      class={cls}
      title={previews ? undefined : props.title || faceLabel(face)}
      aria-label={props.title || faceLabel(face)}
      aria-pressed={props.picked}
      onClick={click}
      {...hover}
    >
      {img}
      {name}
    </button>
  );
}

// 個人化需求的編輯框（上傳頁跟 session 面板同一個）：一段字，對象之間用「；」
// 隔開，指定了人的那段前面嵌著那個人的頭像。頭像不能打字進去，只能整顆刪掉。
export function noteAtom(key: string, faces: FaceRef[]) {
  const atom = document.createElement("span");
  atom.className = "chat-face-chip is-small chat-notes-atom";
  atom.contentEditable = "false";
  atom.dataset.key = key;
  for (const f of faces) {
    const img = document.createElement(f.thumb ? "img" : "span");
    if (f.thumb) (img as HTMLImageElement).src = f.thumb;
    else img.className = "chat-face-blank";
    img.setAttribute("alt", "");
    atom.appendChild(img);
  }
  return atom;
}

// 框裡的內容讀回每個對象的字：遇到頭像就開始那個人的段，遇到「；」或換行就結束
// 目前這段，後面沒接頭像的字算沒指定對象（key ""）。`keys` 裡沒出現的對象是整段
// 被刪掉，給空字串。
export function readNoteBox(box: HTMLElement, keys: string[]) {
  const parts: Record<string, string[]> = Object.fromEntries(keys.map((k) => [k, []]));
  let key = "";
  let buf = "";
  const end = () => {
    const t = buf.trim();
    if (t) (parts[key] ??= []).push(t);
    key = "";
    buf = "";
  };
  const walk = (node: Node) => {
    if (node instanceof HTMLElement && node.dataset.key !== undefined) {
      if (buf.trim()) end();
      key = node.dataset.key;
      buf = "";
      return;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      for (const ch of node.textContent || "") {
        if (ch === "；" || ch === ";" || ch === "\n") end();
        else buf += ch;
      }
      return;
    }
    if (node.nodeName === "BR") end();
    node.childNodes.forEach(walk);
    if (node !== box && (node.nodeName === "DIV" || node.nodeName === "P")) end();
  };
  walk(box);
  end();
  return Object.entries(parts).map(([k, ts]) => ({ key: k, text: ts.join(k ? t("、") : t("；")) }));
}

// 框裡打 Enter 就是換對象，直接打成「；」；貼上的換行也一樣。
export const noteBoxKeys = {
  onKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault();
      document.execCommand("insertText", false, t("；"));
    }
  },
  onPaste(e: ClipboardEvent) {
    e.preventDefault();
    const text = e.clipboardData?.getData("text/plain") || "";
    document.execCommand("insertText", false, text.replace(/\r?\n/g, t("；")));
  },
};

// 打開時把框填好，游標放到最後。
export function fillNoteBox(el: HTMLElement, sections: { key: string; faces: FaceRef[]; text: string }[], focus = true) {
  el.replaceChildren();
  sections.forEach((sec, i) => {
    if (i) el.appendChild(document.createTextNode(t("；")));
    if (sec.key) el.appendChild(noteAtom(sec.key, sec.faces));
    el.appendChild(document.createTextNode(sec.text));
  });
  if (!focus) return;
  el.focus();
  const r = document.createRange();
  r.selectNodeContents(el);
  r.collapse(false);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(r);
}

// session 的個人化需求跟上傳頁同一個樣子：一段字，對象之間用「；」隔開，寫到某人的
// 那段開頭的名字換成那個人的頭像。後端每段開頭就是名字（「左側男性頭髮染黑、瘦臉；
// 背景換成海邊」），所以從開頭比對名字就拆得出來，長的名字先比。
export type PromptSection = { key: string; faces: FaceRef[]; text: string };

export function promptSections(text: string, faces: Record<number, FaceRef>): PromptSection[] {
  const named = Object.values(faces).filter((f) => f.name).sort((a, b) => b.name.length - a.name.length);
  return text
    .split(/[；;\n]/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const hit = named.find((f) => part.startsWith(f.name));
      if (!hit) return { key: "", faces: [], text: part };
      return { key: hit.name, faces: [hit], text: part.slice(hit.name.length).replace(/^[的：:、，,\s]+/, "") };
    });
}

// 看的時候：一段字，名字換成頭像。
export function promptFaces(sections: PromptSection[]) {
  return sections.map((sec, i) => [i ? t("；") : "", sec.faces.map((f) => <FaceChip face={f} small />), sec.text]);
}

// 框裡的內容組回「名字＋需求；…」。
export function readPromptBox(box: HTMLElement, sections: PromptSection[]) {
  return readNoteBox(box, sections.map((x) => x.key))
    .filter((x) => x.text)
    .map((x) => x.key + x.text)
    .join(t("；"));
}

// 個人化需求不用按「儲存」：打字停一下、或離開輸入框，就存下來。還沒存的字在
// 任何動作開始前（美化、開別的項目、送出訊息…）會先存好 -- App 的 run() 開頭
// 呼叫 flushPromptDraft()。使用者手動改的字就照存，不再請 AI 整理。
let promptDraft: { owner: object; flush: () => Promise<void> } | null = null;
export function flushPromptDraft(): Promise<void> {
  return promptDraft ? promptDraft.flush() : Promise.resolve();
}
export const PROMPT_SAVE_MS = 1200;

export function PromptEditor(props: {
  value: string;
  faces: Record<number, FaceRef>;
  busy: boolean;
  label: string;
  placeholder?: string;
  class?: string;
  // 靜靜地存，不請 AI 看。
  onSave: (text: string) => Promise<unknown>;
  // 框下面跟「復原」排在一起的其他按鈕（例如「清除」）。
  extra?: ComponentChildren;
}) {
  const box = useRef<HTMLDivElement>(null);
  const filled = useRef<PromptSection[]>([]);
  const focused = useRef(false);
  const start = useRef(props.value);
  const pending = useRef<string | null>(null);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const timer = useRef<number>();
  const [undoTo, setUndoTo] = useState<string | null>(null);
  const [status, setStatus] = useState<"" | "saving" | "saved" | "failed">("");
  const owner = useRef({}).current;
  const live = useRef(true);
  const saveRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const fill = (text: string) => {
    if (!box.current) return;
    filled.current = promptSections(text, props.faces);
    fillNoteBox(box.current, filled.current, false);
  };
  // 外面換了內容（AI 整理過、別處改了）就跟著換，但正在打字時不動。
  useEffect(() => {
    if (!focused.current) fill(props.value);
  }, [props.value]);
  const read = () => (box.current ? readPromptBox(box.current, filled.current).slice(0, NOTE_MAX) : "");
  // 回傳這次存檔本身：存不到會 reject，動作前的 flush 就會停下來報錯，不會拿舊的
  // 需求去美化。沒存到的字留在 pending，下次再存。
  const save = (): Promise<void> => {
    clearTimeout(timer.current);
    const text = pending.current;
    if (text == null) return chain.current;
    pending.current = null;
    setStatus("saving");
    const done = chain.current.then(() => props.onSave(text)).then(() => {
      if (live.current) setStatus("saved");
    });
    chain.current = done.catch(() => {
      if (pending.current == null) pending.current = text;
      if (live.current) setStatus("failed");
    });
    return done;
  };
  const saveQuietly = () => {
    save().catch(() => {});
  };
  saveRef.current = save;
  // 收起來（例如開了別的項目）時，還沒存的字也存掉。
  useEffect(
    () => () => {
      live.current = false;
      saveRef.current().catch(() => {});
      if (promptDraft?.owner === owner) promptDraft = null;
    },
    [],
  );
  return (
    <div class={props.class}>
      <div
        class="chat-notes-box"
        ref={box}
        role="textbox"
        aria-multiline="true"
        aria-label={props.label}
        data-placeholder={props.placeholder}
        contentEditable={!props.busy}
        onFocus={() => {
          focused.current = true;
          start.current = props.value;
        }}
        onInput={() => {
          if (undoTo == null) setUndoTo(start.current);
          pending.current = read();
          promptDraft = { owner, flush: () => saveRef.current() };
          setStatus("");
          clearTimeout(timer.current);
          timer.current = window.setTimeout(saveQuietly, PROMPT_SAVE_MS);
        }}
        onBlur={() => {
          focused.current = false;
          saveQuietly();
        }}
        {...noteBoxKeys}
      />
      <div class="chat-prompt-foot">
        {undoTo != null && undoTo !== props.value && (
          <button
            type="button"
            class="chat-chip chat-opt-chip is-ghost"
            disabled={props.busy}
            onClick={() => {
              const back = undoTo;
              setUndoTo(null);
              fill(back);
              pending.current = back;
              saveQuietly();
            }}
          >
            {t("復原")}
          </button>
        )}
        {props.extra}
        {status && (
          <span class={`chat-meta${status === "failed" ? " chat-error" : ""}`}>
            {status === "saving" ? t("儲存中…") : status === "saved" ? t("已儲存") : t("儲存失敗，將自動重試")}
          </span>
        )}
      </div>
    </div>
  );
}

type Placeholder = string | string[];

// 輸入框的提示一行就要讀完，被切掉的半句比短句更難懂。給一串由長到短的說法，
// 量輸入框現在實際的字寬，挑第一句放得下的；都放不下就用最短那句。量的是框
// 本身不是螢幕，所以手機、桌機的窄欄、被頭像標籤擠掉一段的框都算得到。
function useFits(boxes: { current: HTMLElement | null }[], dep: unknown) {
  const [width, setWidth] = useState(Infinity);
  const [font, setFont] = useState("");
  useLayoutEffect(() => {
    const el = boxes.map((b) => b.current).find(Boolean);
    if (!el) return;
    const measure = () => {
      const cs = getComputedStyle(el);
      setWidth(el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
      setFont(`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [dep]);
  return (p: Placeholder) => {
    const all = typeof p === "string" ? [p] : p;
    if (all.length < 2 || !font || !(width > 0)) return all[0];
    const ctx = (measureCtx ||= document.createElement("canvas").getContext("2d"));
    if (!ctx) return all[0];
    ctx.font = font;
    return all.find((s) => ctx.measureText(s).width <= width) || all[all.length - 1];
  };
}
let measureCtx: CanvasRenderingContext2D | null = null;

export function Composer(props: {
  onFile?: (f: File) => void;
  // 給了就可以一次選好幾張，全部交給它。
  onFiles?: (fs: File[]) => void;
  // With it, the attach button is the way back to picking a photo rather than
  // a file-dialog shortcut, so it offers both routes instead of one.
  onSample?: () => void;
  // 按鈕的提示字，預設依 onSample 是「換一張照片」或「上傳照片」。
  attachTitle?: string;
  disabled?: boolean;
  // Without onSend nothing can be sent yet (before a photo is picked). The field is a real,
  // typeable input only with typeAhead: what is typed waits in the field (and in onText's draft)
  // for the photo; Send stays off until onSend arrives.
  onSend?: (text: string, subjectIndices: number[]) => Promise<boolean>;
  typeAhead?: boolean;
  people?: (FaceRef & { subjectIndex: number })[];
  pointed?: number[];
  onPoint?: (subjectIndices: number[]) => void;
  // The faces are the whole answer (to 「你說的是哪一位？」), so a pick with
  // nothing typed can be sent as it is.
  sendPicked?: boolean;
  // Bumped to move the cursor into the field.
  focusKey?: number;
  // 可以給一串說法，由長到短；輸入框放得下哪一句就用哪一句。
  placeholder?: Placeholder;
  // Every keystroke, so a caller can act on text that was typed but not sent.
  onText?: (text: string) => void;
  // Bumped to bob the field for a moment, so the eye finds it.
  hintKey?: number;
  // Bumped to empty the field: what was in it has been taken out from the
  // outside and is now part of the conversation.
  clearKey?: number;
}) {
  const { onFile, onSample, disabled, onSend } = props;
  const input = useRef<HTMLInputElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const still = useRef<HTMLDivElement>(null);
  const attach = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState(false);
  const [text, setText] = useState("");
  const people = props.people || [];
  const who = (props.pointed || []).flatMap((i) => people.filter((p) => p.subjectIndex === i));
  const one = who.length === 1 ? who[0] : null;
  const whoKey = who.map((p) => p.subjectIndex).join(",");

  // Not while the 指定對象 panel is open: on a phone every face tapped there
  // would pop the keyboard. The panel hands focus over when it closes.
  useEffect(() => {
    if (field.current?.closest(".chat-panel")?.querySelector(".chat-face-panel")) return;
    if (props.focusKey || whoKey) field.current?.focus();
  }, [props.focusKey, whoKey]);

  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => {
      if (!attach.current?.contains(e.target as Node)) setMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [menu]);

  const [hinting, setHinting] = useState(false);
  useEffect(() => {
    if (!props.hintKey) return;
    setHinting(true);
    // Matches the three .8s cycles in chat.css; longer and it reads as a
    // broken element rather than as a nudge.
    const t = setTimeout(() => setHinting(false), 2400);
    return () => clearTimeout(t);
  }, [props.hintKey]);

  const write = (t: string) => {
    setText(t);
    props.onText?.(t);
  };

  useEffect(() => {
    if (props.clearKey) write("");
  }, [props.clearKey]);

  // 有沒有 onSend 決定量的是 <input> 還是那個假框，換了要重新掛。
  const live = !!onSend || !!props.typeAhead;
  const fits = useFits([field, still], live);

  const sendable = !!text.trim() || (!!props.sendPicked && who.length > 0);

  const send = async () => {
    if (!sendable || disabled || !onSend) return;
    // onSend 要等 AI 回完才 resolve；話一送出就已經出現在對話裡，框不該跟著
    // 留到那時候。先清掉，沒送成再放回去 -- 除非這段時間又打了新的字。
    const sent = text;
    const indices = who.map((p) => p.subjectIndex);
    write("");
    const ok = await onSend(sent.trim(), indices).catch(() => false);
    if (ok) props.onPoint?.([]);
    else if (!field.current?.value) write(sent);
  };

  // The subject picker moved out to <FacePicker>, which the caller puts in the
  // shared foot bar; what stays here is the chip inside the field, which the
  // field's own placeholder and clear button are wired to.
  return (
    <>
      <div class="chat-composer">
        {/* 換照片只在還沒開始對話的上傳頁出現。對話中這顆按鈕指向的是「重開一
            個新對話」，跟它長得像個附加檔案的樣子對不起來，所以那裡不給。 */}
        {onFile && (
          <div class="chat-attach-wrap" ref={attach}>
            <button
              type="button"
              class="chat-attach"
              title={props.attachTitle || (onSample ? t("換一張照片") : t("上傳照片"))}
              aria-haspopup={onSample ? "menu" : undefined}
              aria-expanded={onSample ? menu : undefined}
              disabled={disabled}
              onClick={() => (onSample ? setMenu((v) => !v) : input.current?.click())}
            >
              <Icon name="image" />
              {/* 獨立的 composer 卡片裡，這顆是上面一排的膠囊（像 YCO AI Agent 的「Add Media」），帶文字。 */}
              <span class="chat-attach-label">{props.attachTitle || (onSample ? t("換一張照片") : t("上傳照片"))}</span>
            </button>
            {menu && onSample && (
              <div class="chat-attach-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenu(false);
                    input.current?.click();
                  }}
                >
                  <Icon name="up" size={16} />
                  {t("自行上傳照片")}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenu(false);
                    onSample();
                  }}
                >
                  <Icon name="image" size={16} />
                  {t("從範例照片挑選")}
                </button>
              </div>
            )}
            <input
              ref={input}
              type="file"
              accept="image/*,.heic,.heif"
              multiple={!!props.onFiles}
              hidden
              onChange={(e) => {
                const fs = Array.from(e.currentTarget.files || []);
                e.currentTarget.value = "";
                if (props.onFiles) {
                  if (fs.length) props.onFiles(fs);
                } else if (fs[0] && onFile) onFile(fs[0]);
              }}
            />
          </div>
        )}
        {live ? (
          <label class={`chat-composer-field is-live${hinting ? " is-hint" : ""}`}>
            {who.length > 0 && (
              <span class="chat-composer-who">
                {/* Nameless before an analysis, and then the crop is the whole
                    token: a face and a way to drop it. Several faces stack
                    and the name gives way to the count. */}
                <span class="chat-composer-who-faces">
                  {/* Three is enough to read as "these people"; the count
                      beside them says how many. */}
                  {who.slice(0, 3).map((p) => (
                    <FaceChip key={p.subjectIndex} face={p} small preview />
                  ))}
                </span>
                {who.length > 1 && t("{length} 位", { length: who.length })}
                <button
                  type="button"
                  class="chat-composer-who-x"
                  title={one ? t("取消指定{v1}", { v1: faceLabel(one) }) : t("全部取消指定")}
                  aria-label={one ? t("取消指定{v1}", { v1: faceLabel(one) }) : t("全部取消指定")}
                  onClick={(e) => {
                    // The token sits inside the field's <label>, so the click
                    // would also land on the label and refocus the input.
                    e.preventDefault();
                    e.stopPropagation();
                    props.onPoint?.([]);
                  }}
                >
                  <Icon name="x" size={12} />
                </button>
              </span>
            )}
            <input
              ref={field}
              type="text"
              maxLength={4000}
              value={text}
              disabled={disabled}
              placeholder={fits(
                props.placeholder ||
                (who.length > 1
                  ? t("想怎麼調整這幾位？")
                  : one
                    ? // The token in the field is a face, not a name, so the
                      // line beside it points at the face instead of naming it.
                      t("想怎麼調整這位？")
                    : // With more than one face, the field is the one line everyone
                      // reads before typing, so it carries the picker's affordance.
                      people.length > 1
                      ? [t("想調整什麼？點頭像可以指定要改誰"), t("想調整什麼？點頭像指定人"), t("想調整什麼？")]
                      : t("想調整什麼？"))
              )}
              onInput={(e) => write(e.currentTarget.value)}
              onKeyDown={(e) => {
                // Enter while an IME is composing picks a candidate; it must not send.
                if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
                  e.preventDefault();
                  send();
                }
              }}
            />
          </label>
        ) : (
          <div class="chat-composer-field" ref={still}>
            {fits(props.placeholder || [t("選好照片後就可以用文字告訴我想怎麼調整"), t("選好照片就可以打字告訴我")])}
          </div>
        )}
        <button type="button" class="chat-send" title={t("送出")} disabled={disabled || !onSend || !sendable} onClick={send}>
          <Icon name="up" />
        </button>
      </div>
    </>
  );
}

// ── LLM cards ───────────────────────────────────────────────────────────────
export interface Change {
  field: string;
  label: string;
  before: string;
  after: string;
  subject_index?: number;
}

export interface Proposal {
  id: string;
  patch: Record<string, unknown>;
  changes: Change[];
  // 使用者已經說清楚要什麼（選了 AI 給的答案、只加一句需求、只換一個設定），
  // 後端直接套用，卡上只留「復原」；undo 是放回原樣的 patch。
  direct?: boolean;
  undo?: Record<string, unknown>;
}

export type ProposalChoice = "apply_render" | "apply" | "revise" | "undo";

// apply_render is kept only to show older proposals; rendering is left to the
// 美化 button below the chat.
export const PROPOSAL_CHOICES: Record<ProposalChoice, { label: string; icon: string }> = {
  apply_render: { label: t("套用並美化"), icon: "spark" },
  apply: { label: t("套用"), icon: "check" },
  revise: { label: t("取消"), icon: "x" },
  undo: { label: t("復原"), icon: "undo" },
};
const OFFERED: ProposalChoice[] = ["apply", "revise"];

// Values too long to sit on one line (個人化需求) stack old over new instead.
const LONG_VALUE = 18;
const isLong = (c: Change) => String(c.before ?? "").length > LONG_VALUE || String(c.after ?? "").length > LONG_VALUE;

// 從沒有到有的時候沒有舊值可以劃掉，直接寫出要的是什麼就好。chat_assistant 把
// 空值送成「（無）」，舊 session 存下來的卡也是這樣，所以兩種都當成沒有。
const NO_VALUE = t("（無）");
const hadBefore = (c: Change) => {
  const b = String(c.before ?? "").trim();
  return !!b && b !== NO_VALUE;
};

// 個人化需求就是一段自由文字，要改一個字不該把人丟回上面那張面板 -- 說過的話
// 就留在原地，要改的那句直接在這張卡裡改完再套用。其他欄位是開關或選項，沒有
// 「自己的文字框」可以改，仍然交給面板。
const TEXT_FIELD = "user_prompt";
const NOTE_MAX = 2000; // options.PROMPT_MAX / chat_plan.USER_PROMPT_MAX_LEN
const textChangeOf = (p: Proposal) =>
  p.changes.length === 1 && p.changes[0].field === TEXT_FIELD ? p.changes[0] : null;

// A face change's label is "<name>・<fix>"; the face crop stands in for the name.
function changeLabel(c: Change, face?: FaceRef) {
  // A 畫面 item's label is its recipe text.
  if (!face) return shownRecipe(c.label);
  const prefix = `${face.name}・`;
  return c.label.startsWith(prefix) ? c.label.slice(prefix.length) : c.label;
}

export function ProposalCard(props: {
  proposal: Proposal;
  faces: Record<number, FaceRef>;
  // null while it can still be answered; otherwise what happened to it.
  resolved: ProposalChoice | "stale" | null;
  // 直接套用的卡，在還沒美化、也沒有更新的建議之前可以復原。
  undoable?: boolean;
  // 套用時使用者改過的文字。沒有就是照建議原樣套用。
  edit?: { field: string; value: string } | null;
  busy: boolean;
  onChoose: (choice: ProposalChoice, edit?: { field: string; value: string }) => void;
  onShow: (change: Change) => void;
  // 直接記下的個人化需求按「修改」就在這張卡上改：prompt 是現在的全文，存檔走
  // 跟面板同一條路（存下來再請 AI 整理）。沒給就照舊打開面板。
  prompt?: string;
  onQuietPrompt?: (text: string) => Promise<unknown>;
  // 就地改的框底下補一句說明（例如按「美化」會從哪裡做起）。
  note?: string;
}) {
  const { proposal, resolved } = props;
  const live = resolved == null;
  const typed = live ? textChangeOf(proposal) : null;
  // 個人化需求的前後都用頭像講人，就地改的框也跟面板同一個。
  const sectionsOf = (text: string) => promptSections(text, props.faces);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (typed && box.current) fillNoteBox(box.current, sectionsOf(typed.after), false);
  }, [!!typed]);
  // AI 直接套用的開關、選項不畫卡：回話已經念過改了什麼，要改就從底下的美化項目
  // 改。只有一段個人化需求的那張留著，那是可以就地改字的框。
  if (proposal.direct && !textChangeOf(proposal)) return null;
  // 只改一項、又不是針對某張臉時，標題直接寫那一項是什麼（例如「個人化需求」），
  // 底下那列就不必再把同一個詞念一次。
  const only = proposal.changes.length === 1 && proposal.changes[0].subject_index == null ? proposal.changes[0] : null;
  // AI 直接套用的調整就是一張記事：只給現在的值，不畫改掉的那段、不寫「已套用」。
  // 「復原」換成「修改」，打開那一項的面板自己調；改了好幾項時每一列也都能點。
  const noted = !!proposal.direct && resolved === "apply";
  const editable = noted && !!props.undoable;
  // 只記下一段個人化需求的卡：那列直接就是可以改的框，改了自己存。
  const inline = editable && only?.field === TEXT_FIELD && !!props.onQuietPrompt;
  // 已經不能改的那張：要是在卡上就地改過（對話裡沒有留下別的紀錄），顯示改完的
  // 樣子並標明改過，後面的美化結果才對得上。prompt 是離開這張卡時的需求。
  const settled = only?.field === TEXT_FIELD ? (props.edit?.value ?? only.after) : null;
  const reedited =
    noted && !inline && settled != null && props.prompt != null && props.prompt.trim() !== String(settled).trim()
      ? props.prompt
      : null;
  return (
    <div class="chat-card chat-proposal">
      <p class="chat-proposal-title">
        {only ? only.label : t("建議的調整")}
        {reedited != null && t("（已修改）")}
      </p>
      <ul class="chat-proposal-list">
        {proposal.changes.map((c) => {
          const face = c.subject_index != null ? props.faces[c.subject_index] : undefined;
          // 可以就地改的那一列自己就是輸入框，不再是「點了跳到別處」的按鈕。
          const mine = c === typed;
          const after = props.edit?.field === c.field ? props.edit.value : c.after;
          // Answered proposals are history: shown, no longer clickable -- except
          // a several-item note that can still be changed.
          const clickable = (live && !mine) || (editable && !only);
          const Row = clickable ? "button" : "div";
          return (
            <li>
              <Row
                type={clickable ? "button" : undefined}
                class="chat-proposal-row"
                title={clickable ? t("在面板上顯示") : undefined}
                disabled={clickable && !live ? props.busy : undefined}
                onClick={clickable ? () => props.onShow(c) : undefined}
              >
                {face && <FaceChip face={face} small />}
                {!only && (
                  <span class="chat-proposal-label">
                    {changeLabel(c, face)}
                  </span>
                )}
                <span class={`chat-proposal-diff${isLong(c) || mine || inline ? " is-long" : ""}`}>
                  {hadBefore(c) && !noted && (
                    <>
                      <s>{c.field === TEXT_FIELD ? promptFaces(sectionsOf(c.before)) : c.before}</s>
                      <Icon name="arrow" size={12} />
                    </>
                  )}
                  {inline ? (
                    <PromptEditor
                      class="chat-proposal-edit"
                      value={props.prompt ?? c.after}
                      faces={props.faces}
                      busy={props.busy}
                      label={c.label}
                      onSave={props.onQuietPrompt!}
                    />
                  ) : mine ? (
                    <div
                      class="chat-notes-box chat-proposal-edit"
                      ref={box}
                      role="textbox"
                      aria-multiline="true"
                      aria-label={c.label}
                      contentEditable={!props.busy}
                      {...noteBoxKeys}
                    />
                  ) : (
                    <b>{c.field === TEXT_FIELD ? promptFaces(sectionsOf(reedited ?? after)) : after}</b>
                  )}
                </span>
              </Row>
            </li>
          );
        })}
      </ul>
      {live ? (
        <div class="chat-choices">
          {OFFERED.map((k) => (
            <Chip
              label={PROPOSAL_CHOICES[k].label}
              icon={PROPOSAL_CHOICES[k].icon}
              kind={k === "apply" ? "primary" : "ghost"}
              disabled={props.busy}
              onClick={() =>
                props.onChoose(
                  k,
                  typed && k === "apply" && box.current
                    ? { field: typed.field, value: readPromptBox(box.current, sectionsOf(typed.after)) }
                    : undefined,
                )
              }
            />
          ))}
        </div>
      ) : noted ? (
        inline ? props.note ? <p class="chat-note">{props.note}</p> : null : editable && (
          <div class="chat-choices">
            {/* 跟個人化需求面板裡那顆「修改」同一個樣子。 */}
            <button
              type="button"
              class="chat-chip chat-opt-chip is-primary"
              disabled={props.busy}
              onClick={() => props.onShow(only ?? proposal.changes[0])}
            >
              {t("修改")}
            </button>
          </div>
        )
      ) : (
        <p class="chat-meta">
          {resolved === "stale"
            ? t("已有新的建議，這個沒有套用。")
            : resolved === "undo"
              ? t("已復原，沒有套用。")
              : t("已選擇「{v1}」", { v1: PROPOSAL_CHOICES[resolved].label })}
        </p>
      )}
    </div>
  );
}

export function BriefCard(props: {
  brief: { summary?: string; issues?: string[] };
}) {
  const { brief } = props;
  return (
    <>
      {brief.summary && <p class="chat-text">{brief.summary}</p>}
      {!!brief.issues?.length && (
        <ul class="chat-brief-issues">
          {brief.issues.map((t) => (
            <li>{t}</li>
          ))}
        </ul>
      )}
    </>
  );
}

// 範例照片, as a dialog. The grid used to sit in the timeline, which cost two
// things: every browse left a wall of thumbnails in the transcript, and --
// because picking again had to make that wall the last message -- opening it
// with a photo already chosen had to drop that photo, collapsing the
// two-column view. Over the page instead: what is behind stays put, and
// nothing is recorded until a photo is actually taken.
// 範例照片的彈窗。max 大於 1 時可以多選：點一下選、再點一下取消，選到的標上第幾張
// （多張照這個順序排），選好了再按底下的鍵送出。max 是 1 就跟以前一樣點了就選。
export function SamplePicker(props: {
  open: boolean;
  demos: string[] | null;
  picked: string | null;
  max?: number;
  // 已經在多張頁上的範例照片：淡下去不能再選，同一張不會加兩次。
  taken?: string[];
  onPick: (names: string[]) => void;
  onClose: () => void;
}) {
  const { open, onClose } = props;
  const max = props.max ?? 1;
  const multi = max > 1;
  const [chosen, setChosen] = useState<string[]>([]);
  useEffect(() => {
    if (!open) return;
    // 每次打開從目前這張起算，上次沒送出的不留著。
    setChosen(props.picked ? [props.picked] : []);
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [open, onClose]);

  if (!open) return null;
  const demos = props.demos || [];
  const full = chosen.length >= max;
  const taken = props.taken || [];
  const toggle = (name: string) => {
    if (taken.includes(name)) return;
    if (!multi) return props.onPick([name]);
    setChosen((c) => (c.includes(name) ? c.filter((n) => n !== name) : c.length >= max ? c : [...c, name]));
  };
  return (
    <>
      <div class="sample-modal-backdrop" onClick={onClose} />
      <div class="sample-modal" role="dialog" aria-modal="true" aria-label={t("範例照片")}>
        <div class="sample-modal-head">
          <h2>{t("範例照片")}</h2>
          <button type="button" class="sample-modal-close" aria-label={t("關閉")} onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div class="sample-modal-body">
          {demos.length ? (
            <div class="sample-grid">
              {demos.map((name) => {
                const at = multi ? chosen.indexOf(name) : props.picked === name ? 0 : -1;
                const off = taken.includes(name) || (multi && full && at < 0);
                return (
                  <button
                    type="button"
                    key={name}
                    class={`sample-tile${at >= 0 ? " is-picked" : ""}${off ? " is-full" : ""}`}
                    title={taken.includes(name) ? t("{name}（已經加入了）", { name }) : name}
                    aria-pressed={multi ? at >= 0 : undefined}
                    aria-disabled={off || undefined}
                    onClick={() => toggle(name)}
                  >
                    <img src={urls.demoThumb(name)} alt={name} loading="lazy" />
                    {multi && at >= 0 && <span class="sample-tile-order" aria-hidden="true">{at + 1}</span>}
                  </button>
                );
              })}
            </div>
          ) : (
            <p class="chat-text chat-muted">{props.demos ? t("目前沒有可用的範例照片。") : t("載入中…")}</p>
          )}
        </div>
        {multi && !!demos.length && (
          <div class="sample-modal-foot">
            <span class="sample-modal-count" aria-live="polite">
              {chosen.length ? (full ? t("已選 {length} 張（最多 {max} 張）", { length: chosen.length, max }) : t("已選 {length} 張", { length: chosen.length })) : t("可以選多張，最多 {max} 張", { max })}
            </span>
            {/* 簡報 p3 S01：取消／完成改用 YCO Button（Secondary · Neutral／Primary · Brand，Medium 42px）。 */}
            <div class="sample-modal-actions">
              <button type="button" class="ycoButton variant_secondary tone_neutral size_medium" onClick={onClose}>
                <span class="label">{t("取消")}</span>
              </button>
              <button type="button" class="ycoButton variant_primary tone_brand size_medium" disabled={!chosen.length} onClick={() => props.onPick(chosen)}>
                <span class="label">{t("選好了")}</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
