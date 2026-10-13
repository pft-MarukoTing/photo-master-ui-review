// Building blocks of the chat panel: the panel shell, message rows, chips and
// the few cards that are new to the chat (working / result / collapsed).
import type { ComponentChildren } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { api, urls } from "./api";
import { Icon } from "./icons";
import { qaText } from "./util";

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
  onDropFile?: (file: File) => void;
  // The photo being worked on, before the session starts. On a phone, once its
  // big copy in the timeline (UploadBubble with onOpen) scrolls off the top,
  // the head gives its place to a bar showing it; tapping either opens it.
  // always: the session page, where there is no such copy -- the bar stays up
  // from the start, titled with the version on screen.
  photo?: { src: string; onOpen: () => void; title?: string; note?: string; always?: boolean };
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
  // The first message of a result that just landed (toStart): the timeline
  // stays on its start until the user scrolls or more messages follow it.
  const anchor = useRef<HTMLElement | null>(null);
  const seen = useRef(new WeakSet<Element>());
  const lastStart = useRef(props.toStart);
  const align = () => {
    const el = timeline.current;
    const a = anchor.current;
    if (!el || !a || !a.isConnected) return;
    el.scrollTop += a.getBoundingClientRect().top - el.getBoundingClientRect().top - 8;
  };
  useLayoutEffect(() => {
    const el = timeline.current;
    if (!el) return;
    const msgs = Array.from(el.querySelectorAll(":scope > .chat-msg"));
    const fresh = msgs.filter((m) => !seen.current.has(m));
    if (props.toStart !== lastStart.current) {
      lastStart.current = props.toStart;
      anchor.current = (fresh[0] || msgs[msgs.length - 1] || null) as HTMLElement | null;
    } else if (anchor.current && fresh.length) {
      // Talk went on after the result: back to following the end.
      anchor.current = null;
      pinned.current = true;
    }
    for (const m of msgs) seen.current.add(m);
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

  const drop = props.onDropFile;
  return (
    <div
      class={`chat-panel${props.class ? ` ${props.class}` : ""}${dragging ? " is-dragging" : ""}${
        props.photo ? " has-photo" : ""
      }${photoUp ? " is-photo-out" : ""}`}
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
          const f = e.dataTransfer?.files?.[0];
          if (f) drop(f);
        })
      }
    >
      <div class="chat-panel-head">
        <Icon name="chat" size={14} />
        <span class="chat-panel-title">對話</span>
        <span class="chat-panel-sub">{props.sub}</span>
      </div>
      {props.photo && (
        <button type="button" class="chat-panel-photo" onClick={props.photo.onOpen} tabIndex={photoUp ? 0 : -1}>
          <img src={props.photo.src} alt="" />
          <span class="chat-panel-photo-text">
            <b>{props.photo.title || "這次要修的照片"}</b>
            <small>{props.photo.note || "點一下看大圖"}</small>
          </span>
          <Icon name="expand" size={16} />
        </button>
      )}
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

export function AssistantMsg({ children }: { children: ComponentChildren }) {
  return (
    <div class="chat-msg">
      <div class="chat-avatar">P</div>
      <div class="chat-body">{children}</div>
    </div>
  );
}

export function ClickBubble({ icon, label }: { icon: string; label: string }) {
  return (
    <div class="chat-bubble is-click">
      <Icon name={icon} />
      {label}
    </div>
  );
}

// With onOpen the photo is a button that opens it big -- on a phone it is
// also drawn big here, since there is no stage beside the conversation.
export function UploadBubble({ src, text, onOpen }: { src: string; text: string; onOpen?: () => void }) {
  return (
    <div class={`chat-upload${onOpen ? " is-zoom" : ""}`}>
      {onOpen ? (
        <button type="button" class="chat-upload-shot" aria-label="看大圖" onClick={onOpen}>
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

// The photo on its own over the page. Tapping anywhere but the photo, the
// close button or Esc puts it away.
export function PhotoViewer({ src, onClose }: { src: string | null; onClose: () => void }) {
  useEffect(() => {
    if (!src) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [src, onClose]);

  if (!src) return null;
  return (
    <div class="photo-viewer" role="dialog" aria-modal="true" aria-label="照片大圖" onClick={onClose}>
      <img src={src} alt="待美化的照片" onClick={(e) => e.stopPropagation()} />
      <button type="button" class="photo-viewer-close" aria-label="關閉" onClick={onClose}>
        <Icon name="x" size={18} />
      </button>
    </div>
  );
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
}) {
  const cls = `chat-chip${props.kind ? ` is-${props.kind}` : ""}${props.settled ? " is-static" : ""}`;
  if (props.settled) {
    return (
      <span class={cls} title={props.title}>
        {props.icon && <Icon name={props.icon} />}
        {props.label}
      </span>
    );
  }
  return (
    <button type="button" class={cls} disabled={props.disabled} title={props.title} onClick={props.onClick}>
      {props.icon && <Icon name={props.icon} />}
      {props.label}
    </button>
  );
}

// `cue`: the label only leads into the chips (下一步：), so a narrow strip can
// drop it; a label that says something the chips do not stays.
export function QuickBar({ label, cue, children }: { label?: ComponentChildren; cue?: boolean; children: ComponentChildren }) {
  return (
    <div class="chat-quick">
      {label && <span class={cue ? "chat-quick-label is-cue" : "chat-quick-label"}>{label}</span>}
      {children}
    </div>
  );
}

// The quick replies and the subject picker used to be two stacked strips, each
// with its own rule and padding -- ~95px of chrome above the composer for two
// short rows. One strip instead, split by a rule. The faces lead on the left,
// under the transcript they belong to and on the same edge as the composer's
// attach button; the actions are flushed right, where the send button is and
// where the eye already goes to commit.
export function FootBar({ quick, faces }: { quick?: ComponentChildren; faces?: ComponentChildren }) {
  if (!quick && !faces) return null;
  return (
    <div class="chat-footbar">
      {faces}
      {quick && faces && <span class="chat-footbar-sep" aria-hidden="true" />}
      {quick}
    </div>
  );
}

// The faces sit in a row while they fit. When they do not, the row folds into
// one button -- a couple of faces and +N -- that opens every face upward in a
// grid: a scroller hid the rest without a hint and left the last face cut in
// half. The fit is measured on the picker's own width, which is whatever the
// strip leaves after the actions, so it does not depend on what is inside.
// Each tap adds that face to the pick or takes it back out; the pick keeps the
// order the faces were tapped in. Picking several is the point, so a tap does
// not close the panel -- its X, the button again, Esc or a tap outside do.
const FACE_W = 48; // .chat-face-chip.is-avatar: 40 + 3px pad + 1px border, each side
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
    const need = people.length * FACE_W + (people.length - 1) * FACE_GAP;
    const fit = () => setFolded(need > el.clientWidth - (label.current?.offsetWidth || 0) - FACE_GAP);
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
        picked={picked}
        title={picked ? `取消指定${faceLabel(p)}` : `指定${faceLabel(p)}`}
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
        指定對象
      </span>
      {folded ? (
        <>
          <button
            type="button"
            class={`chat-face-fold${open ? " is-open" : ""}`}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-label={`指定對象，共 ${people.length} 位`}
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
            <div class="chat-face-panel" role="dialog" aria-label="指定對象">
              <div class="chat-face-panel-head">
                <span>指定對象</span>
                <button type="button" class="chat-face-panel-x" aria-label="關閉" onClick={() => close(true)}>
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
  const text = sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${String(sec % 60).padStart(2, "0")} 秒`;
  return <span class="chat-working-time">{text}</span>;
}

export function WorkingCard({ done, now, since }: { done: string[]; now: string; since: number }) {
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

// next：這是最新的一版、下方正擺著「調整」時才講下一步。舊的結果卡不講，不然
// 往上捲會看到每一版都在叫人去按一顆已經不在的按鈕。
// note：開了「詳細解說」時，後端對照原圖與成品寫的點評；沒有就不顯示。
export function ResultCard({ job, ordinal, failed, next, note, onOpen }: {
  job: string;
  ordinal: number;
  failed: number;
  next?: boolean;
  note?: string;
  // 手機上旁邊沒有比較卡，成品放大、點了開大圖檢視。
  onOpen?: () => void;
}) {
  const [qa, setQa] = useState(qaCache.get(job) || null);
  useEffect(() => {
    if (qa) return;
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
  }, [job]);

  // The finished photo is the moment the whole flow was for, so it gets a
  // real look at it rather than the 84x66 chip it used to be. Same URL the
  // canvas on the left already loaded, so this costs no extra fetch.
  return (
    <figure class="chat-result">
      {onOpen ? (
        <button type="button" class="chat-result-shot" aria-label={`看第 ${ordinal} 版大圖`} onClick={onOpen}>
          <img src={urls.resultImage(job)} alt={`第 ${ordinal} 版美化結果`} />
          <span class="chat-upload-zoom" aria-hidden="true">
            <Icon name="expand" size={14} />
          </span>
        </button>
      ) : (
        <img src={urls.resultImage(job)} alt={`第 ${ordinal} 版美化結果`} />
      )}
      <figcaption class="chat-result-cap">
        <h4>第 {ordinal} 版完成</h4>
        {note && <p class="chat-text">{note}</p>}
        {qa && (
          <div class="chat-verdict">
            輸出評語 <b class={qa.pass ? "" : "is-warn"}>{qa.verdict}</b>
            {qa.score && ` 品質分數 ${qa.score}`}
          </div>
        )}
        <p class="chat-text chat-muted">
          {failed > 0 && `有 ${failed} 張沒有成功，已略過。`}
          <span class="chat-result-hint-wide">左側可比較 Before / After，也可從「美化紀錄」切換版本。</span>
          <span class="chat-result-hint-narrow">上方照片可以跟原圖比，點左上的版本也能切換。</span>
        </p>
        {next && <p class="chat-text">還想改哪裡的話，按下方的「調整」，或直接打字跟我說。</p>}
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

const faceLabel = (f: FaceRef) => f.name || f.where || "這個人";

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

export function FaceChip(props: {
  face: FaceRef;
  picked?: boolean;
  small?: boolean;
  // The face alone, at picker size: no name beside it, the crop is the label.
  avatarOnly?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  const { face } = props;
  const img = face.thumb ? <img src={face.thumb} alt="" /> : <span class="chat-face-blank" />;
  const cls =
    `chat-face-chip${props.picked ? " is-picked" : ""}` +
    `${props.small ? " is-small" : ""}${props.avatarOnly ? " is-avatar" : ""}`;
  const name = !props.small && !props.avatarOnly && face.name;
  if (!props.onClick) {
    return (
      <span class={cls} title={faceLabel(face)}>
        {img}
        {name}
      </span>
    );
  }
  return (
    <button
      type="button"
      class={cls}
      title={props.title || faceLabel(face)}
      aria-label={props.title || faceLabel(face)}
      aria-pressed={props.picked}
      onClick={props.onClick}
    >
      {img}
      {name}
    </button>
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
  // With it, the attach button is the way back to picking a photo rather than
  // a file-dialog shortcut, so it offers both routes instead of one.
  onSample?: () => void;
  disabled?: boolean;
  // Without onSend the field is a placeholder (before a photo is picked).
  onSend?: (text: string, subjectIndices: number[]) => Promise<boolean>;
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
  const fits = useFits([field, still], !!onSend);

  const sendable = !!text.trim() || (!!props.sendPicked && who.length > 0);

  const send = async () => {
    if (!sendable || disabled || !onSend) return;
    if (await onSend(text.trim(), who.map((p) => p.subjectIndex))) {
      write("");
      props.onPoint?.([]);
    }
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
              title={onSample ? "換一張照片" : "上傳照片"}
              aria-haspopup={onSample ? "menu" : undefined}
              aria-expanded={onSample ? menu : undefined}
              disabled={disabled}
              onClick={() => (onSample ? setMenu((v) => !v) : input.current?.click())}
            >
              <Icon name="image" />
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
                  自行上傳照片
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
                  從範例照片挑選
                </button>
              </div>
            )}
            <input
              ref={input}
              type="file"
              accept="image/*,.heic,.heif"
              hidden
              onChange={(e) => {
                const f = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                if (f && onFile) onFile(f);
              }}
            />
          </div>
        )}
        {onSend ? (
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
                    <FaceChip key={p.subjectIndex} face={p} small />
                  ))}
                </span>
                {who.length > 1 && `${who.length} 位`}
                <button
                  type="button"
                  class="chat-composer-who-x"
                  title={one ? `取消指定${faceLabel(one)}` : "全部取消指定"}
                  aria-label={one ? `取消指定${faceLabel(one)}` : "全部取消指定"}
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
                  ? "想怎麼調整這幾位？"
                  : one
                    ? // The token in the field is a face, not a name, so the
                      // line beside it points at the face instead of naming it.
                      "想怎麼調整這位？"
                    : // With more than one face, the field is the one line everyone
                      // reads before typing, so it carries the picker's affordance.
                      people.length > 1
                      ? ["想調整什麼？點頭像可以指定要改誰", "想調整什麼？點頭像指定人", "想調整什麼？"]
                      : "想調整什麼？")
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
            {fits(props.placeholder || ["選好照片後就可以用文字告訴我想怎麼調整", "選好照片就可以打字告訴我"])}
          </div>
        )}
        <button type="button" class="chat-send" title="送出" disabled={disabled || !onSend || !sendable} onClick={send}>
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
  apply_render: { label: "套用並美化", icon: "spark" },
  apply: { label: "套用", icon: "check" },
  revise: { label: "取消", icon: "x" },
  undo: { label: "復原", icon: "undo" },
};
const OFFERED: ProposalChoice[] = ["apply", "revise"];

// Values too long to sit on one line (個人化需求) stack old over new instead.
const LONG_VALUE = 18;
const isLong = (c: Change) => String(c.before ?? "").length > LONG_VALUE || String(c.after ?? "").length > LONG_VALUE;

// 從沒有到有的時候沒有舊值可以劃掉，直接寫出要的是什麼就好。chat_assistant 把
// 空值送成「（無）」，舊 session 存下來的卡也是這樣，所以兩種都當成沒有。
const NO_VALUE = "（無）";
const hadBefore = (c: Change) => {
  const b = String(c.before ?? "").trim();
  return !!b && b !== NO_VALUE;
};

// 個人化需求就是一段自由文字，要改一個字不該把人丟回上面那張面板 -- 說過的話
// 就留在原地，要改的那句直接在這張卡裡改完再套用。其他欄位是開關或選項，沒有
// 「自己的文字框」可以改，仍然交給面板。
const TEXT_FIELD = "user_prompt";
const textChangeOf = (p: Proposal) =>
  p.changes.length === 1 && p.changes[0].field === TEXT_FIELD ? p.changes[0] : null;

// A face change's label is "<name>・<fix>"; the face crop stands in for the name.
function changeLabel(c: Change, face?: FaceRef) {
  if (!face) return c.label;
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
}) {
  const { proposal, resolved } = props;
  const live = resolved == null;
  const typed = live ? textChangeOf(proposal) : null;
  const [draft, setDraft] = useState(textChangeOf(proposal)?.after ?? "");
  // 只改一項、又不是針對某張臉時，標題直接寫那一項是什麼（例如「個人化需求」），
  // 底下那列就不必再把同一個詞念一次。
  const only = proposal.changes.length === 1 && proposal.changes[0].subject_index == null ? proposal.changes[0] : null;
  return (
    <div class="chat-card chat-proposal">
      <p class="chat-proposal-title">{only ? only.label : "建議的調整"}</p>
      <ul class="chat-proposal-list">
        {proposal.changes.map((c) => {
          const face = c.subject_index != null ? props.faces[c.subject_index] : undefined;
          // 可以就地改的那一列自己就是輸入框，不再是「點了跳到別處」的按鈕。
          const mine = c === typed;
          const after = props.edit?.field === c.field ? props.edit.value : c.after;
          // Answered proposals are history: shown, no longer clickable.
          const Row = live && !mine ? "button" : "div";
          return (
            <li>
              <Row
                type={live && !mine ? "button" : undefined}
                class="chat-proposal-row"
                title={live && !mine ? "在面板上顯示" : undefined}
                onClick={live && !mine ? () => props.onShow(c) : undefined}
              >
                {face && <FaceChip face={face} small />}
                {!only && (
                  <span class="chat-proposal-label">
                    {changeLabel(c, face)}
                  </span>
                )}
                <span class={`chat-proposal-diff${isLong(c) || mine ? " is-long" : ""}`}>
                  {hadBefore(c) && (
                    <>
                      <s>{c.before}</s>
                      <Icon name="arrow" size={12} />
                    </>
                  )}
                  {mine ? (
                    <textarea
                      class="chat-proposal-edit"
                      rows={3}
                      value={draft}
                      disabled={props.busy}
                      onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
                    />
                  ) : (
                    <b>{after}</b>
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
              onClick={() => props.onChoose(k, typed && k === "apply" ? { field: typed.field, value: draft.trim() } : undefined)}
            />
          ))}
        </div>
      ) : proposal.direct && resolved === "apply" ? (
        <>
          <p class="chat-meta">已套用</p>
          {props.undoable && (
            <div class="chat-choices">
              <Chip
                label={PROPOSAL_CHOICES.undo.label}
                icon={PROPOSAL_CHOICES.undo.icon}
                kind="ghost"
                disabled={props.busy}
                onClick={() => props.onChoose("undo")}
              />
            </div>
          )}
        </>
      ) : (
        <p class="chat-meta">
          {resolved === "stale"
            ? "已有新的建議，這個沒有套用。"
            : resolved === "undo"
              ? "已復原，沒有套用。"
              : `已選擇「${PROPOSAL_CHOICES[resolved].label}」`}
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
export function SamplePicker(props: {
  open: boolean;
  demos: string[] | null;
  picked: string | null;
  onPick: (name: string) => void;
  onClose: () => void;
}) {
  const { open, onClose } = props;
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [open, onClose]);

  if (!open) return null;
  const demos = props.demos || [];
  return (
    <>
      <div class="sample-modal-backdrop" onClick={onClose} />
      <div class="sample-modal" role="dialog" aria-modal="true" aria-label="範例照片">
        <div class="sample-modal-head">
          <h2>範例照片</h2>
          <button type="button" class="sample-modal-close" aria-label="關閉" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div class="sample-modal-body">
          {demos.length ? (
            <div class="sample-grid">
              {demos.map((name) => (
                <button
                  type="button"
                  key={name}
                  class={`sample-tile${props.picked === name ? " is-picked" : ""}`}
                  title={name}
                  onClick={() => props.onPick(name)}
                >
                  <img src={urls.demoThumb(name)} alt={name} loading="lazy" />
                </button>
              ))}
            </div>
          ) : (
            <p class="chat-text chat-muted">{props.demos ? "目前沒有可用的範例照片。" : "載入中…"}</p>
          )}
        </div>
      </div>
    </>
  );
}
