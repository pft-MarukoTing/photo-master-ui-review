// The chat page: home (intro + gallery), the pre-session chat that picks a
// photo and settings, and a session (compare canvas + chat timeline built
// from the server's messages). Every action is one of the chat API calls;
// typed messages go to /ask, whose replies may carry a plan change to confirm.
import { useEffect, useRef, useState } from "preact/hooks";
import { api, ApiError, setUser, urls, type ReviewTurn, type Screen } from "./api";
import { isHeic, previewBlob } from "./heic";
import {
  AssistantMsg,
  BriefCard,
  ChatPanel,
  Chip,
  ClickBubble,
  Composer,
  FaceChip,
  faceText,
  FacePicker,
  FootBar,
  PROPOSAL_CHOICES,
  ProposalCard,
  QuickBar,
  ResultCard,
  SamplePicker,
  UploadBubble,
  UserMsg,
  WorkingCard,
  useRotatingText,
  type Change,
  type FaceRef,
  type Proposal,
  type ProposalChoice,
} from "./chat";
import { facePeople, useBoxThumbs, useDetectedFaces, useFaceThumbs, type Box } from "./face";
import { Icon } from "./icons";
import { Gallery, Header, HistoryDrawer, HomeIntro, type GallerySelection, type GalleryTab } from "./home";
import { resolveUser } from "./identity";
import { ResultsCanvas, StepIndicator, stepState, versionJob } from "./results";
import {
  categoriesFor,
  introFor,
  CATEGORIES,
  categoryOf,
  OptionsCard,
  PROMPT_MAX,
  topicLead,
  topicRecap,
  type Category,
  type Hot,
} from "./options";
import { defaultSettings, sameSettings, SettingsEditor, type Settings } from "./settings";
import type {
  Analysis,
  Capabilities,
  Dict,
  Message,
  Photo,
  Plan,
  PromptFace,
  Session,
  SessionSummary,
  Version,
} from "./types";
import {
  clockLabel,
  rememberRenderLabel,
  renderLabelFor,
  renderMeta,
  renderSubMessages,
  timeLabel,
  versionsFromMessages,
} from "./util";

type Phase = "home" | "chat" | "session";
type PreStep = "start" | "picked" | "settings";
type Source = { kind: "file"; file: File; url: string } | { kind: "demo"; name: string };
// A face picked on the upload page: no name yet, so where it stands and its box.
type PrePerson = FaceRef & { where: string; box: Box };

// A line typed before the session exists, with the people the user had pointed
// at when they sent it, in the order they were tapped. The name and the where are taken at that moment: the
// picker belongs to the photo that was on screen, and swapping the photo drops
// them (choose) rather than letting them rename themselves onto strangers.
// `reply` is what the AI said back to it, and `kept` is false for a line the
// AI would not take -- it stays in the conversation, under the sentence saying
// why, but it is not part of 個人化需求. `dropped` is the same line taken back
// out of the card by the user afterwards: what was said was said, and the AI
// did answer it, so the turn stays where it happened -- only its claim on the
// prompt is withdrawn. Where it was withdrawn is recorded too: `after` is how
// many lines had been said by then, so the AI's answer to the removal lands at
// the point in the thread where the removal actually happened, and `batch`
// groups the lines one click took out, so 清除所有需求 is answered once and not
// once per line.
type Note = {
  text: string;
  persons: PrePerson[];
  reply?: string;
  kept?: boolean;
  dropped?: { after: number; batch: number };
  // The AI's reply was a question back instead of a decision; `options` are
  // the short answers it offered to tap.
  asked?: { options: string[] };
  // The AI asked whether to start 分析／一鍵美化 for them instead of starting
  // it. Not a question about the line: the answer is a line of its own.
  // `later`: the line is a question only the analysis can answer (「這張適合
  // 什麼文案？」), so a yes carries it into the session to be answered there.
  offered?: { options: string[]; later?: boolean };
  // What goes in the list when this line only makes sense next to an earlier
  // one: the answer to that question completed (「頭髮換顏色」＋「棕色」→
  // 「頭髮換成棕色」), or a line carrying on from an earlier wish written out
  // (「褲子也是」→「把男孩的褲子也換成白色」).
  // `text` stays what was typed -- that is what the bubble shows.
  wish?: string;
};

// 點頭像回答「哪一位」、直接送出時 AI 回的那句。沒問模型，所以話是這裡寫好的。
const WHO_ANSWERED_REPLY = "好，這個我記下來了。還有其他想改的地方嗎？沒有的話，可以從下面選「分析」或「一鍵美化」喔！";

// What a note asks for, as the list shows it and the prompt carries it.
const wishText = (n: Note) => n.wish ?? n.text;

const keptNotes = (ns: Note[]) => ns.filter((n) => n.kept !== false && !n.dropped);
// Only a line that is actually in 個人化需求 can be taken out of it: 清除所有需求
// sweeps the card, and the card never held the lines the AI turned away.
const dropNotes = (ns: Note[], gone: (n: Note) => boolean) => {
  const batch = ns.reduce((m, n) => (n.dropped ? Math.max(m, n.dropped.batch + 1) : m), 0);
  return ns.map((n) =>
    n.kept !== false && !n.dropped && gone(n) ? { ...n, dropped: { after: ns.length, batch } } : n,
  );
};

// 對話是照發生的順序排的，而拿掉需求也是使用者說的一句話，所以它跟打字一樣
// 自成一輪，排在當下的位置——拿掉第一句之後又講了第三句的話，AI 的回答要在第
// 三句之前。`left` 是那一刻清單裡還剩幾句，回話才講得出「其他的照舊」。
type Turn = { ask: Note } | { gone: Note[]; left: number };

function conversation(ns: Note[]): Turn[] {
  const batches = new Map<number, { after: number; gone: Note[]; left: number }>();
  for (const n of ns) {
    if (!n.dropped) continue;
    const seen = batches.get(n.dropped.batch);
    if (seen) seen.gone.push(n);
    else batches.set(n.dropped.batch, { after: n.dropped.after, gone: [n], left: 0 });
  }
  for (const [batch, b] of batches) {
    b.left = ns
      .slice(0, b.after)
      .filter((n) => n.kept !== false && (!n.dropped || n.dropped.batch > batch)).length;
  }
  const ordered = [...batches.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b);
  const out: Turn[] = [];
  ns.forEach((n, i) => {
    out.push({ ask: n });
    for (const b of ordered) if (b.after === i + 1) out.push({ gone: b.gone, left: b.left });
  });
  return out;
}

// 使用者從清單裡拿掉需求，AI 就回一句。把使用者打的字劃掉是在改畫面，回話才
// 是在對話——上面那句「我記下來了」還在原地，是真的講過的；底下這句接著說現在
// 不算數了，兩句合起來才是完整的經過。
function goneReply(t: { gone: Note[]; left: number }) {
  if (t.gone.length > 1) return "好，個人化需求都清掉了，美化時就不另外加了。";
  const what = `「${wishText(t.gone[0])}」`;
  return t.left ? `好，${what}就不做了，其他的照舊。` : `好，${what}就不做了，目前沒有其他個人化需求。`;
}

// How a person is written into plan.user_prompt. Nothing has been analyzed yet,
// so there is no subject_index to send with the line -- the person has to
// survive as words, and where they stand in the frame is the part the model can
// resolve on its own.
// What the prompt calls this person. Before an analysis there is no name, so
// the position is the whole of it; the server swaps the same string for
// 「<名字>（<位置>）」 once it knows who that face is.
const faceRef = (p: PrePerson) => (p.name ? `${p.name}（${p.where}）` : p.where);
// Several people on one line: each written out, joined by 「、」, so the server
// still finds and renames every one of them.
const facesRef = (ps: PrePerson[]) => ps.map(faceRef).join("、");
const whoPrefix = (ps: PrePerson[]) => (ps.length ? `${facesRef(ps)}：` : "");
// What one note says while it is the line being checked.
const noteLine = (n: Note) => whoPrefix(n.persons) + n.text;

// 上傳頁面答不出來、AI 說分析完再回答的那句問題：AI 剛問完要不要先分析，現在
// 開始了（分析或一鍵美化都算），這句就跟著進 session，照片好了再回答。
function laterNote(ns: Note[]): Note | null {
  const last = ns[ns.length - 1];
  return last?.offered?.later ? last : null;
}

// 一個人一條：同一張臉講了三句就併成一條，照第一次提到的順序排。後端的
// BUILD user_prompt FROM SCRATCH 要的就是這個形狀（一條裡面用「、」接，條跟條
// 之間用「；」），上傳頁面自己先併好，模型、畫面、送出去的 user_prompt 看到的
// 才是同一份。沒指定對象的句子各自成一條——它們講的不見得是同一件事。同時點了
// 好幾位的句子，就是那一組人一條。
type NoteGroup = { persons: PrePerson[]; notes: Note[] };

function noteGroups(ns: Note[]): NoteGroup[] {
  const out: NoteGroup[] = [];
  const byRef = new Map<string, NoteGroup>();
  for (const n of ns) {
    const ref = n.persons.length ? facesRef(n.persons) : null;
    const seen = ref ? byRef.get(ref) : undefined;
    if (seen) {
      seen.notes.push(n);
      continue;
    }
    const group: NoteGroup = { persons: n.persons, notes: [n] };
    if (ref) byRef.set(ref, group);
    out.push(group);
  }
  return out;
}

const groupLine = (g: NoteGroup) => whoPrefix(g.persons) + g.notes.map(wishText).join("、");
const promptText = (ns: Note[]) => noteGroups(ns).map(groupLine).join("；");

// 上傳頁面的個人化需求跟 session 裡一樣是一整段字，使用者可以直接改。改過之後
// 那段字就是準：`upTo` 之前的句子都已經寫在 `text` 裡（或被使用者刪掉了），之後
// 才收下的句子接在後面。
type PromptEdit = { text: string; upTo: number };

function notesPrompt(ns: Note[], edit: PromptEdit | null) {
  if (!edit) return promptText(keptNotes(ns));
  return [edit.text, promptText(keptNotes(ns.slice(edit.upTo)))].filter(Boolean).join("；");
}

// 上傳頁面的整段對話，給模型看的版本：它才分得出這句是在回答它剛問的，還是另一
// 個新需求。使用者拿掉需求那一輪，模型看到的是 AI 回的那句。
function reviewHistory(ns: Note[]): ReviewTurn[] {
  const out: ReviewTurn[] = [];
  for (const t of conversation(ns)) {
    if ("gone" in t) {
      out.push({ role: "assistant", text: goneReply(t) });
      continue;
    }
    out.push({ role: "user", text: noteLine(t.ask) });
    if (t.ask.reply)
      out.push({
        role: "assistant",
        text: t.ask.reply,
        ...(t.ask.asked ? { question: true, options: t.ask.asked.options } : {}),
        ...(t.ask.offered ? { question: true, offer: true, options: t.ask.offered.options } : {}),
      });
  }
  return out.slice(-20);
}

// 上傳頁面的整段對話，搬進 session 當開頭：使用者講過的話、AI 回的話都照原樣
// 留著，session 的 AI 接著聊時也看得到前面講到哪。選項跟動作鍵不跟過去——那是
// 上傳頁面的按鍵，到了 session 按不到。指過的人還沒有名字，帶著框跟位置過去，
// 泡泡上的頭像從框裁，AI 讀的是位置。`later` 那句就是等著回答的問題本身，標成
// waiting 放在它原本的位置，分析好了再回答。
function carriedLog(ns: Note[], photoId: string, later: Note | null) {
  const out: { role: "user" | "assistant"; text?: string; card: Dict }[] = [];
  const reply = (text: string) => out.push({ role: "assistant", card: { type: "llm_reply", photo_id: photoId, pre: true, text } });
  for (const t of conversation(ns)) {
    if ("gone" in t) {
      reply(goneReply(t));
      continue;
    }
    const n = t.ask;
    const card: Dict = { type: "ask", photo_id: photoId, pre: true };
    if (n.persons.length) card.faces = n.persons.map((p) => ({ box: p.box, where: p.where }));
    if (n === later) card.waiting = true;
    out.push({ role: "user", text: n.text, card });
    if (n.reply) reply(n.reply);
  }
  return out.slice(-100);
}

// AI 問了還沒人回的那一句：只有對話的最後一輪是它的提問才算——中間拿掉過需求
// 或又講了別的，那個問題就過去了。
function openAsk(ns: Note[]): Note | null {
  const log = conversation(ns);
  const last = log[log.length - 1];
  return last && "ask" in last && last.ask.asked ? last.ask : null;
}

// The same words, for the server to find and replace. A position is not a name
// and the analysis will have one; the box is what survives the handover, so the
// pick travels alongside the prompt as plan.prompt_faces and the server settles
// the name once it has an analysis.
const groupFaces = (g: NoteGroup): PromptFace[] =>
  g.persons.map((p) => ({ box: p.box, ref: faceRef(p), where: p.where }));

const GREETING = "嗨！先選一張想美化的照片（一次一張）。要自己上傳，還是從範例照片挑？";
// 個人化需求存檔後自動送出的那一句。chat_assistant.INSTRUCTION 會認這句話，改字要兩邊一起改。
const PROMPT_REVIEW_ASK = "我改了個人化需求，幫我確認一下內容。";

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.body?.error === "photos_not_ready") return "照片還在處理中，請稍候再試。";
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

function sessionHash(): string | null {
  const m = location.hash.match(/session=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function setSessionHash(id: string | null) {
  const url = `${location.pathname}${location.search}${id ? `#session=${encodeURIComponent(id)}` : ""}`;
  history.replaceState(null, "", url);
}

function sourceSrc(s: Source) {
  return s.kind === "file" ? s.url : urls.demoThumb(s.name);
}

function activePhoto(s: Session | null): Photo | null {
  if (!s || !s.photos.length) return null;
  return s.photos.find((p) => p.photo_id === s.active_photo_id) || s.photos[0];
}

const isBusy = (p: Photo) => p.status === "analyzing" || p.status === "rendering";

export function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [debugUi, setDebugUi] = useState(false);
  const [bootError, setBootError] = useState("");
  const [demos, setDemos] = useState<string[] | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);

  const [phase, setPhase] = useState<Phase>("home");
  const [tab, setTab] = useState<GalleryTab>("recent");
  const [selection, setSelection] = useState<GallerySelection>(null);
  const [drawer, setDrawer] = useState(false);

  // Pre-session chat
  const [step, setStep] = useState<PreStep>("start");
  const [via, setVia] = useState<"upload" | "sample" | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  // What the user typed in the composer before the session starts, one entry
  // per message. Together they become the new photo's plan.user_prompt, so the
  // 個人化需求 card in the session opens with them.
  const [notes, setNotes] = useState<Note[]>([]);
  // The user's own edit of the 個人化需求 text, once there is one.
  const [preEdit, setPreEdit] = useState<PromptEdit | null>(null);
  const [samplesOpen, setSamplesOpen] = useState(false);
  // Bumped when a photo lands. The composer is the one control here that looks
  // like chrome rather than a step, so it has to say "type here" itself: the
  // field bobs for a moment on every device, and the caret rides along only
  // where it is free -- on touch, focusing throws the keyboard over the panel.
  const [pickHint, setPickHint] = useState(0);
  // The faces picked in the upload page's own picker, by position among the
  // locally detected faces -- the session's pointed, before there is a session.
  const [prePointed, setPrePointed] = useState<number[]>([]);
  // The line that is with the AI right now, shown the way the session shows a
  // question it has not answered yet.
  const [preAsk, setPreAsk] = useState<Note | null>(null);
  // Bumped to empty the composer: a line it still holds has just become part
  // of the conversation.
  const [preClear, setPreClear] = useState(0);

  // Session
  const [session, setSession] = useState<Session | null>(null);
  const [analyses, setAnalyses] = useState<Record<string, Analysis>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [pendingAsk, setPendingAsk] = useState<{ text: string; subjectIndices: number[] } | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // ── The upload page's own face picker ─────────────────────────────────────
  // The local detector runs on the picked photo right away, so the faces are
  // there to point at while the first request is still being typed -- the same
  // picker, in the same place, as the one the session has.
  const preFull = !source ? "" : source.kind === "file" ? source.url : urls.demoImage(source.name);
  const preScan = useDetectedFaces(source ? preFull : null, async () => {
    if (!source) throw new Error("no source");
    if (source.kind === "file") return { blob: source.file, name: source.file.name };
    const res = await fetch(urls.demoImage(source.name));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { blob: await res.blob(), name: source.name };
  });
  const preFaces = preScan.people;
  const preThumbs = useBoxThumbs(preFaces.map((p) => p.box), preFull);
  // The detector hands faces back largest first, an order nobody looking at the
  // photo can see. Counting from the left is the one order the photo itself
  // shows, so that is how a face gets put into words here.
  const preOrder = [...preFaces].sort((a, b) => a.box.x + a.box.w / 2 - (b.box.x + b.box.w / 2));
  const preWhere = (subjectIndex: number) =>
    `照片中從左邊數來第 ${preOrder.findIndex((q) => q.subjectIndex === subjectIndex) + 1} 位`;
  // Nothing has been analysed, so nobody has a name: the chip is the face, and
  // the position is only there for the tooltip and for anyone who cannot see it.
  const prePickable = preFaces.map((p) => ({
    name: "",
    where: preWhere(p.subjectIndex),
    thumb: preThumbs[p.subjectIndex] || "",
    subjectIndex: p.subjectIndex,
  }));
  // 照片剛進來、偵測還沒回來時，畫面跟「沒有人」長得一模一樣。先問「想怎麼修」
  // 的話，使用者會對著還沒出現的頭像做決定，等人臉補上來那句話又整句換掉。所以
  // 等偵測跟頭像都到齊再往下走——頭像也要等，不然選人的那排會先閃一輪空圈。
  const preScanning = !!source && (!preScan.ready || preThumbs.length !== preFaces.length);
  useEffect(() => {
    if (source && !preScanning) setPickHint((n) => n + 1);
  }, [source, preScanning]);
  const preRefs = (subjectIndices: number[]): PrePerson[] =>
    subjectIndices.flatMap((i) => {
      const p = preFaces.find((q) => q.subjectIndex === i);
      return p ? [{ name: p.name, thumb: preThumbs[p.subjectIndex] || "", where: preWhere(p.subjectIndex), box: p.box }] : [];
    });

  // ── Boot ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const user = await resolveUser();
        setUser(user);
        const [c, cfg] = await Promise.all([api.capabilities(), api.config()]);
        setCaps(c);
        setSettings(defaultSettings(c));
        setDebugUi(!!cfg.debugUi);
        api
          .demoImages()
          .then((r) => setDemos(r.images || []))
          .catch(() => setDemos([]));
        const list = await refreshSessions();
        if (!list.length) setTab("sample");
        const id = sessionHash();
        if (id) await openSession(id);
      } catch (e) {
        setBootError(errorText(e));
      }
    })();
  }, []);

  async function refreshSessions(): Promise<SessionSummary[]> {
    try {
      const r = await api.listSessions(30);
      const list = (r.sessions || []).filter((s) => s.photo_count > 0);
      setSessions(list);
      return list;
    } catch {
      setSessions([]);
      return [];
    }
  }

  // ── Session loading and polling ───────────────────────────────────────────
  async function openSession(id: string) {
    setDrawer(false);
    setError("");
    try {
      const s = await api.getSession(id);
      if (!s.photos.length) throw new Error("這個對話沒有照片");
      setSession(s);
      setSelected(null);
      setPhase("session");
      setSessionHash(id);
      window.scrollTo(0, 0);
    } catch (e) {
      setSessionHash(null);
      if (phase === "session") goHome();
      setError(errorText(e));
    }
  }

  const photo = activePhoto(session);
  const messages = session?.messages || [];
  const polling = !!session && session.photos.some(isBusy);
  const sessionRef = useRef(session);
  sessionRef.current = session;

  useEffect(() => {
    if (!session || !polling) return;
    const id = session.session_id;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const st = await api.status(id);
        if (stop) return;
        const prev = sessionRef.current;
        const shape = (s: Session | null) => (s?.photos || []).map((p) => `${p.photo_id}:${p.status}`).join(",");
        if (st.new_messages?.length || shape(prev) !== shape(st.session)) {
          // Another tab may have advanced the photo; refetch to get its messages too.
          const full = await api.getSession(id);
          if (!stop) setSession(full);
        } else {
          setSession((p) => (p && p.session_id === id ? { ...st.session, messages: p.messages } : p));
        }
      } catch {
        /* keep polling; a transient failure should not end the session view */
      }
      if (!stop) timer = window.setTimeout(tick, 2000);
    };
    timer = window.setTimeout(tick, 2000);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [session?.session_id, polling]);

  // The analysis, once the server said it is ready. A ready or rendering photo
  // has one even if that message is no longer in the log.
  const analysisReady =
    !!photo &&
    (photo.status === "ready" ||
      photo.status === "rendering" ||
      messages.some((m) => m.card?.type === "analysis_ready" && m.card.photo_id === photo.photo_id));
  useEffect(() => {
    if (!session || !photo || !analysisReady || analyses[photo.photo_id]) return;
    const pid = photo.photo_id;
    api
      .analysis(session.session_id, pid)
      .then((r) => {
        if (r.status === "success" && r.result?.analysis) setAnalyses((a) => ({ ...a, [pid]: r.result.analysis }));
      })
      .catch(() => {});
  }, [session?.session_id, photo?.photo_id, analysisReady]);

  // ── Navigation ────────────────────────────────────────────────────────────
  function resetPre() {
    setStep("start");
    setVia(null);
    setSource(null);
    setNotes([]);
    setPreEdit(null);
    setPrePointed([]);
    setPreAsk(null);
    setSamplesOpen(false);
    if (caps) setSettings(defaultSettings(caps));
  }

  function goHome() {
    setPhase("home");
    setSession(null);
    setSelection(null);
    setError("");
    setSessionHash(null);
    resetPre();
    refreshSessions();
  }

  function newChat(pick?: Source) {
    setSession(null);
    setError("");
    setSessionHash(null);
    resetPre();
    setPhase("chat");
    if (pick) choose(pick);
    window.scrollTo(0, 0);
  }

  // Browsing samples is no longer a turn in the conversation: it opens a
  // dialog over whatever is on screen. Nothing here touches via/source/step,
  // so opening it to swap an already-chosen photo leaves the stage -- and the
  // two-column layout -- exactly where they were until a new photo lands.
  function showSamples() {
    setSamplesOpen(true);
    setError("");
  }

  function choose(s: Source) {
    setSamplesOpen(false);
    setSource(s);
    // A new photo has its own people: what was typed stays, who it was aimed
    // at does not.
    setPrePointed([]);
    setNotes((n) => (n.some((x) => x.persons.length) ? n.map((x) => ({ ...x, persons: [] })) : n));
    setVia(s.kind === "file" ? "upload" : "sample");
    setStep("picked");
    setError("");
  }

  // HEIC has to be decoded before it can be shown, which takes a moment; a
  // second pick made meanwhile wins over the first.
  const lastPick = useRef(0);
  async function pickFile(file: File) {
    if (!file.type.startsWith("image/") && !isHeic(file)) {
      setError("請選擇圖片檔。");
      return;
    }
    const pick = ++lastPick.current;
    const preview = await previewBlob(file);
    if (pick !== lastPick.current) return;
    const s: Source = { kind: "file", file, url: URL.createObjectURL(preview) };
    if (phase === "session") newChat(s);
    else {
      if (phase !== "chat") setPhase("chat");
      choose(s);
    }
  }

  function onGallerySelect(sel: GallerySelection) {
    if (!sel) return;
    // A past chat opens right away; a sample on the home page is only selected.
    if (phase === "chat" || sel.kind === "session") {
      if (sel.kind === "session") openSession(sel.id);
      else choose({ kind: "demo", name: sel.name });
      return;
    }
    setSelection(sel);
  }

  function onGalleryConfirm() {
    if (selection?.kind === "demo") newChat({ kind: "demo", name: selection.name });
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  async function run(fn: () => Promise<void>): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError("");
    try {
      await fn();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  // After a render POST: reload and label the new render_requested card with
  // the chip the user clicked (the server does not know it).
  async function afterRender(id: string, label: string, icon: string, plan: Plan) {
    const full = await api.getSession(id);
    const last = [...(full.messages || [])].reverse().find((m) => m.card?.type === "render_requested");
    if (last && caps) rememberRenderLabel([last.id], { label, icon, meta: renderMeta(caps, plan) });
    setSession(full);
  }

  // 上傳頁面打的每一句，跟 session 裡一樣先讓 AI 看過。收下的才算個人化需求，
  // 不是在講這張照片的就留在對話裡、底下附上 AI 回的那一句，不會跟著送出去。
  // 回傳收下後的 notes，被擋下來就回 null。
  // `typed` 是這句從輸入框送出的；點選項回答時不是，輸入框裡打到一半的字要留著。
  async function reviewNote(
    text: string,
    subjectIndices: number[],
    base: Note[],
    typed = true,
  ): Promise<Note[] | null> {
    // 回答 AI 的提問時沒再點人，就沿用被問的那句指的人：「想換成什麼顏色？」
    // 「棕色」講的還是同一位。
    const asking = openAsk(base);
    const picked = preRefs(subjectIndices);
    const note: Note = { text, persons: picked.length ? picked : asking?.persons || [] };
    // 要靠前一句才看得懂的話（回答追問的「棕色」、接著講的「褲子也是」），模型
    // 會補成完整的一句，連同它接過來的人；清單裡每一句才能單獨讀、單獨刪。回答
    // 追問時模型沒拼回來（或根本沒問到），就自己接上：至少看得出是在講哪件事。
    const completed = (line?: string, wheres: string[] = []): Partial<Note> => {
      const carried = wheres.flatMap((w) => preOrder.filter((p) => preWhere(p.subjectIndex) === w));
      const persons = note.persons.length ? note.persons : preRefs(carried.map((p) => p.subjectIndex));
      const wish = line || (asking ? `${wishText(asking)}，${text}` : undefined);
      return { persons, ...(wish && wish !== text ? { wish } : {}) };
    };
    setPreAsk(note);
    if (typed) setPreClear((n) => n + 1);
    try {
      const r = await api.promptReview(
        noteLine(note),
        notesPrompt(base, preEdit),
        reviewHistory(base),
        preOrder.map((p) => preWhere(p.subjectIndex)),
      );
      const settled: Note = {
        ...note,
        reply: r.reply,
        kept: r.accepted,
        ...(r.ask ? { asked: { options: r.options || [] } } : {}),
        ...(r.offer ? { offered: { options: r.options || [], ...(r.later ? { later: true } : {}) } } : {}),
        ...(r.accepted ? completed(r.line, r.persons) : {}),
      };
      setNotes([...base, settled]);
      // 「幫我美化」「幫我分析」就是底下那兩顆鍵，照按：已經收下的需求一起帶進去。
      if (r.start) startSession(r.start, laterNote(base), [...base, settled]);
      return r.accepted ? [...base, settled] : null;
    } catch {
      // 問不到就放行：一次模型失敗不該讓人卡在上傳頁面，而且進了 session，
      // 個人化需求面板按下確認時還會再過一次。
      const kept = { ...note, ...completed() };
      setNotes([...base, kept]);
      return [...base, kept];
    } finally {
      setPreAsk(null);
    }
  }

  // AI 問「你說的是哪一位？」（沒給選項）的那一句：點頭像先選人，可以選好幾位，
  // 選好什麼都不打直接送出就是回答。
  const preWhoAsk = (() => {
    const asking = openAsk(notes);
    return asking && !asking.asked!.options.length ? asking : null;
  })();

  // 選好人直接送出：那句需求接到這幾位身上收下來，不用重打一次，也不用再問模型
  // ——缺的只有人，人已經點了。
  function answerWho(subjectIndices: number[]) {
    const persons = preRefs(subjectIndices);
    if (!preWhoAsk || !persons.length) return;
    const text = persons.length > 1 ? "這幾位" : "這位";
    setNotes([...notes, { text, wish: wishText(preWhoAsk), persons, reply: WHO_ANSWERED_REPLY, kept: true }]);
  }

  // `later` 是要帶進 session、照片好了再回答的那句；沒給就看最後一句是不是。
  // `log` 是到這一刻為止的對話：打字要求開始的那句剛收下，還沒進 state。
  function startSession(mode: "analyze" | "render", later = laterNote(notes), log = notes) {
    if (!source || !caps || !settings) return;
    // 個人化需求只認已經送出、也已經讓 AI 看過的那幾句。輸入框裡打了沒送的字
    // 就當沒打過：它沒進過對話，也沒過把關，不該從這裡偷渡進 user_prompt。
    const kept = keptNotes(log);
    run(async () => {
      const s = await api.createSession();
      const id = s.session_id;
      const userPrompt = notesPrompt(log, preEdit).slice(0, PROMPT_MAX);
      // Only the picks the prompt still spells out: one entry per person
      // already names each face once, and PROMPT_MAX can still cut the last
      // entry off.
      const promptFaces = noteGroups(kept)
        .flatMap(groupFaces)
        .filter((f) => userPrompt.includes(f.ref));
      // The defaults a photo added next starts from, so the upload below picks
      // the 個人化需求 up without a second patch.
      if (userPrompt || !sameSettings(settings, defaultSettings(caps))) {
        await api.patchPlan(id, { ...settings, ...(userPrompt ? { user_prompt: userPrompt } : {}) });
      }
      let blob: Blob;
      let name: string;
      if (source.kind === "file") {
        blob = source.file;
        name = source.file.name;
      } else {
        const res = await fetch(urls.demoImage(source.name));
        if (!res.ok) throw new Error(`無法讀取範例照片（HTTP ${res.status}）`);
        blob = await res.blob();
        name = source.name;
      }
      const up = await api.uploadPhoto(id, blob, name);
      const added = up.photos?.[0];
      if (!added) throw new Error(up.errors?.[0]?.error || "上傳失敗");
      // On the photo, not the session defaults: a second photo has its own
      // faces and must not inherit these.
      if (promptFaces.length) await api.patchPhotoPlan(id, added.photo_id, { prompt_faces: promptFaces });
      // 上傳頁面聊過的接在照片後面。等著回答的那句也在裡面：分析一好，session
      // 的 AI 就回答它，使用者不用再問一次。
      const carried = carriedLog(log, added.photo_id, later);
      if (carried.length) await api.postMessages(id, carried);
      if (mode === "render") {
        await api.render(id, [added.photo_id]);
        await afterRender(id, "一鍵美化", "spark", { ...settings });
      } else {
        setSession(await api.getSession(id));
      }
      setSelected(null);
      setPhase("session");
      setSessionHash(id);
      refreshSessions();
    });
  }

  function renderPhoto(label: string, icon: string, patch?: Plan) {
    if (!session || !photo) return;
    const id = session.session_id;
    const pid = photo.photo_id;
    run(async () => {
      let plan = photo.plan;
      if (patch) plan = (await api.patchPhotoPlan(id, pid, patch)).plan;
      await api.render(id, [pid]);
      await afterRender(id, label, icon, plan);
      setSelected(null);
    });
  }

  function askPhoto(text: string, subjectIndices: number[], screen?: Screen) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    setPendingAsk({ text, subjectIndices });
    return run(async () => {
      try {
        await api.ask(id, pid, text, subjectIndices, screen);
        setSession(await api.getSession(id));
      } finally {
        setPendingAsk(null);
      }
    });
  }

  // 從上傳頁面帶過來的問題，分析完回答它。打字中的那三個點照樣出現，只是不再
  // 重複一次問題泡泡——它已經在對話裡了。
  function answerWaiting(messageId: string, screen: Screen) {
    if (!session || !photo) return;
    const id = session.session_id;
    const pid = photo.photo_id;
    setPendingAsk({ text: "", subjectIndices: [] });
    run(async () => {
      try {
        await api.answerWaiting(id, pid, messageId, screen);
        setSession(await api.getSession(id));
      } finally {
        setPendingAsk(null);
      }
    });
  }

  function patchFromChat(patch: Plan) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    return run(async () => {
      await api.patchPhotoPlan(id, pid, patch);
      setSession(await api.getSession(id));
    });
  }

  // 個人化需求是手打的，一條一條往下加，同一個人身上很容易留著兩種說法。存下來之後
  // 再問一次 AI，讓它把同一個人的事併成一句、矛盾的留後面那句，整理結果照常走提案卡，
  // 要不要換還是使用者決定。
  function savePrompt(text: string) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    setPendingAsk({ text: PROMPT_REVIEW_ASK, subjectIndices: [] });
    return run(async () => {
      try {
        await api.patchPhotoPlan(id, pid, { user_prompt: text });
        await api.ask(id, pid, PROMPT_REVIEW_ASK);
        setSession(await api.getSession(id));
      } finally {
        setPendingAsk(null);
      }
    });
  }

  // A chat step the server only records (options_opened, adjust_opened, ...),
  // after an optional plan patch. merge: when the newest message is the same
  // step for this photo, it is rewritten instead of adding another.
  function postFromChat(card: Dict, patch?: Plan, merge = false) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    const last = session.messages?.[session.messages.length - 1];
    // A card the assistant opened itself (via) is an answer in the timeline,
    // not a step to rewrite.
    const into =
      merge && last?.role === "user" && last.card?.type === card.type && last.card?.photo_id === pid && !last.card?.via
        ? last
        : null;
    return run(async () => {
      if (patch) await api.patchPhotoPlan(id, pid, patch);
      const next = { ...card, photo_id: pid };
      // Something landed after it (a render finishing): add a new one after all.
      if (!into || !(await api.replaceMessage(id, into.id, next).then(() => true, () => false))) {
        await api.postMessage(id, next);
      }
      setSession(await api.getSession(id));
    });
  }

  // startRound: 套用 after a render opens a round of adjusting, so the
  // change shows in the options and 美化 makes the next version.
  function resolveProposal(
    proposal: Proposal,
    choice: ProposalChoice,
    startRound = false,
    edit?: { field: string; value: string },
  ) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    return run(async () => {
      let plan = photo.plan;
      // The round this opens starts where the last render left off, same as
      // 調整後再美化 -- the proposal's own patch is its first change.
      const before = snapshotOf(photo.plan);
      if (choice === "undo") {
        plan = (await api.patchPhotoPlan(id, pid, (proposal.undo || {}) as Plan)).plan;
      } else if (choice !== "revise") {
        plan = (await api.patchPhotoPlan(id, pid, proposal.patch as Plan)).plan;
      }
      // 卡片本身不能改寫（replaceMessage 只收最新那則），所以使用者改過的字留在
      // 這張卡上，重新整理後那段建議也還是顯示真正套用的內容。
      await api.postMessage(id, {
        type: "proposal_resolved", photo_id: pid, proposal_id: proposal.id, choice,
        ...(edit ? { edit } : {}),
      });
      if (startRound && choice === "apply") {
        await api.postMessage(id, { type: "adjust_opened", photo_id: pid, via: "proposal", plan: before });
      }
      if (choice === "apply_render") {
        await api.render(id, [pid]);
        await afterRender(id, PROPOSAL_CHOICES.apply_render.label, PROPOSAL_CHOICES.apply_render.icon, plan);
        setSelected(null);
      } else {
        setSession(await api.getSession(id));
      }
    });
  }

  function selectVersion(v: Version) {
    if (!session || !photo) return;
    setSelected(v.version_id);
    api
      .setActiveVersion(session.session_id, photo.photo_id, { version_id: v.version_id, job_id: versionJob(v), run: 1 })
      .catch(() => {});
  }

  // ── Render ────────────────────────────────────────────────────────────────
  if (bootError) {
    return (
      <>
        <Header onHome={() => location.reload()} onHistory={() => {}} showHome={false} />
        <main>
          <div class="container chat-boot-error">無法載入：{bootError}</div>
        </main>
      </>
    );
  }
  if (!caps || !settings) return <Header onHome={() => {}} onHistory={() => {}} showHome={false} />;

  const hiddenInput = (
    <input
      ref={fileInput}
      type="file"
      accept="image/*,.heic,.heif"
      hidden
      onChange={(e) => {
        const f = e.currentTarget.files?.[0];
        e.currentTarget.value = "";
        if (f) pickFile(f);
      }}
    />
  );

  const header = (
    <Header
      onHome={goHome}
      onHistory={() => {
        refreshSessions();
        setDrawer(true);
      }}
      showHome={phase !== "home"}
      showHistory={phase === "session"}
    />
  );
  const drawerEl = (
    <HistoryDrawer
      open={drawer}
      sessions={sessions}
      currentId={session?.session_id || null}
      onOpen={openSession}
      onClose={() => setDrawer(false)}
    />
  );
  // Both returns mount it; it draws nothing until opened.
  const samplesEl = (
    <SamplePicker
      open={samplesOpen}
      demos={demos}
      picked={source?.kind === "demo" ? source.name : null}
      onPick={(name) => choose({ kind: "demo", name })}
      onClose={() => setSamplesOpen(false)}
    />
  );

  if (phase === "session" && session && photo) {
    return (
      <>
        {header}
        <main>
          <SessionView
            caps={caps}
            debugUi={debugUi}
            session={session}
            photo={photo}
            analysis={analyses[photo.photo_id] || null}
            selected={selected}
            pendingAsk={pendingAsk}
            busy={busy}
            error={error}
            onSelectVersion={selectVersion}
            onRender={renderPhoto}
            onFile={pickFile}
            onAsk={askPhoto}
            onAnswerWaiting={answerWaiting}
            onPatch={patchFromChat}
            onSavePrompt={savePrompt}
            onPost={postFromChat}
            onResolve={resolveProposal}
          />
        </main>
        {drawerEl}
        {samplesEl}
      </>
    );
  }

  const gallery = (
    <Gallery
      tab={tab}
      onTab={setTab}
      sessions={sessions}
      demos={demos}
      selection={phase === "chat" ? null : selection}
      onSelect={onGallerySelect}
      onConfirm={onGalleryConfirm}
    />
  );

  return (
    <>
      {header}
      <main>
        <section id="upload-section" class="section visible">
          <div class="container upload-container">
            <div class="upload-grid">
              {/* is-chat drops the 360px styles.css reserves on the right for the
                  fixed gallery rail -- with the rail unmounted it is padding
                  around nothing, and it was what pushed the panel off centre. */}
              <div class={`upload-content-row${phase === "chat" ? " is-chat" : ""}`}>
                {phase === "home" ? (
                  <HomeIntro onStart={() => newChat()} />
                ) : (
                  <>
                  {/* Nothing to stage until a photo is picked: before that the
                      conversation is the whole page, centred, and ChatPanel
                      itself is the drop target. */}
                  {source && <PreStage source={source} />}
                  <ChatPanel
                    class={`home-chat${source ? "" : " is-solo"}`}
                    sub="新對話"
                    onDropFile={busy ? undefined : pickFile}
                    footer={
                      <>
                        <FootBar
                          faces={
                            prePickable.length > 1 && (
                              <FacePicker people={prePickable} pointed={prePointed} onPoint={setPrePointed} />
                            )
                          }
                        />
                        <Composer
                          onFile={pickFile}
                          // Only once there is a photo to replace; before that the
                          // greeting's two chips are the choice, right above.
                          onSample={source ? showSamples : undefined}
                          disabled={busy || !!preAsk || preScanning}
                          // Live once a photo is picked: what is typed here is the
                          // 個人化需求 the session starts with.
                          onSend={
                            source
                              ? async (text, si) => {
                                  if (!text) answerWho(si);
                                  else await reviewNote(text, si, notes);
                                  // 收不收都已經在對話裡了，輸入框不用再留著。
                                  return true;
                                }
                              : undefined
                          }
                          clearKey={preClear}
                          people={prePickable}
                          pointed={prePointed}
                          onPoint={setPrePointed}
                          sendPicked={!!preWhoAsk}
                          focusKey={matchMedia("(hover:hover) and (pointer:fine)").matches ? pickHint : 0}
                          hintKey={pickHint}
                          // Left to the composer once someone is pointed at, so the
                          // field says whose turn it is instead of this line --
                          // unless the pick is the answer, and then it says so.
                          placeholder={
                            prePointed.length && preWhoAsk
                              ? "選好了就直接送出，也可以再打字補充"
                              : !source || prePointed.length || preScanning
                              ? undefined
                              : openAsk(notes)
                                ? openAsk(notes)!.asked!.options.length
                                  ? "直接打字回答，或點上面的選項"
                                  : prePickable.length > 1
                                    ? "點頭像選人（可以選好幾位），或直接打字回答"
                                    : "直接打字回答"
                                : prePickable.length > 1
                                ? "想怎麼美化這張照片？點頭像可以指定要改誰"
                                : "想怎麼美化這張照片？也可以留空直接開始"
                          }
                        />
                      </>
                    }
                  >
                    <PreChat
                      caps={caps}
                      people={prePickable}
                      step={step}
                      via={via}
                      source={source}
                      settings={settings}
                      notes={notes}
                      prompt={notesPrompt(notes, preEdit)}
                      pending={preAsk}
                      scanning={preScanning}
                      faceCount={prePickable.length}
                      busy={busy}
                      error={error}
                      onUpload={() => fileInput.current?.click()}
                      onSamples={showSamples}
                      onSettings={setSettings}
                      onClear={() => {
                        setNotes(dropNotes(notes, () => true));
                        setPreEdit(null);
                      }}
                      onPrompt={(text, upTo) => {
                        // 改成空的就是清掉，跟按「清除所有需求」同一件事。
                        if (!text) {
                          setNotes(dropNotes(notes, () => true));
                          setPreEdit(null);
                        } else setPreEdit({ text, upTo });
                      }}
                      onEdit={() => setStep("settings")}
                      onStart={startSession}
                      onAnswer={(text) => void reviewNote(text, [], notes, false)}
                    />
                  </ChatPanel>
                  </>
                )}
                {phase === "home" && gallery}
              </div>
            </div>
          </div>
        </section>
        {hiddenInput}
      </main>
      {drawerEl}
      {samplesEl}
    </>
  );
}

// ── Pre-session stage ───────────────────────────────────────────
// The half of this step that is not conversation: the photo being worked on,
// in the same place the session's canvas will appear. It mounts only once a
// photo exists -- an empty frame beside an empty conversation was two empty
// halves, so until then the conversation holds the page on its own.
function PreStage({ source }: { source: Source }) {
  return (
    <div class="prechat-stage">
      <div class="prechat-shot">
        <img src={source.kind === "file" ? source.url : urls.demoImage(source.name)} alt="待美化的照片" />
      </div>
    </div>
  );
}

// ── Pre-session chat ────────────────────────────────────────────────────────
// 文字裡寫著「照片中從左邊數來第 2 位：」的地方換回那個人的頭像，跟對話裡指定
// 對象時看到的同一顆。冒號跟著拿掉——頭像本身就是「這位：」。
function withFaces(text: string, faces: PrePerson[]) {
  return faceText(text, faces.map((f) => ({ ref: faceRef(f), face: f })), true);
}

// 跟 session 的個人化需求面板同一套：先用看的，要改再打開輸入框。`upTo` 是打開
// 那一刻有幾句——改的期間又收下的句子不在框裡，存檔後會接在後面，不會被蓋掉。
function NotesEditor(props: {
  prompt: string;
  upTo: number;
  faces: PrePerson[];
  held: boolean;
  onSave: (text: string, upTo: number) => void;
}) {
  const [editing, setEditing] = useState<{ draft: string; upTo: number } | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const open = !!editing;
  useEffect(() => {
    if (open) box.current?.focus();
  }, [open]);
  if (!editing) {
    return (
      <div class="chat-opt-prompt">
        <p class="chat-opt-prompt-text">{withFaces(props.prompt, props.faces)}</p>
        <div class="chat-choices">
          <button
            type="button"
            class="chat-chip chat-opt-chip is-primary"
            disabled={props.held}
            onClick={() => setEditing({ draft: props.prompt, upTo: props.upTo })}
          >
            修改
          </button>
        </div>
      </div>
    );
  }
  const draft = editing.draft;
  return (
    <div class="chat-opt-prompt">
      <textarea
        rows={3}
        maxLength={PROMPT_MAX}
        value={draft}
        disabled={props.held}
        ref={box}
        onInput={(e) => setEditing({ ...editing, draft: (e.target as HTMLTextAreaElement).value })}
      />
      <div class="chat-choices">
        <button
          type="button"
          class="chat-chip chat-opt-chip is-primary"
          disabled={props.held || draft.trim() === props.prompt}
          onClick={() => {
            props.onSave(draft.trim(), editing.upTo);
            setEditing(null);
          }}
        >
          儲存
        </button>
        <button type="button" class="chat-chip chat-opt-chip is-ghost" disabled={props.held} onClick={() => setEditing(null)}>
          取消
        </button>
      </div>
    </div>
  );
}

function PreChat(props: {
  caps: Capabilities;
  // Everyone the detector found, so the AI's words about where someone stands
  // can show that face instead.
  people: FaceRef[];
  step: PreStep;
  via: "upload" | "sample" | null;
  source: Source | null;
  settings: Settings;
  notes: Note[];
  // The line the AI is looking at right now, if there is one.
  pending: Note | null;
  // The photo is in, the local detector has not come back yet. Asking what to
  // do belongs after this: who is in the photo is half of the answer.
  scanning: boolean;
  // How many people the local detector found in the picked photo: the picker
  // under the composer only appears from two up, and so does the line about it.
  faceCount: number;
  busy: boolean;
  error: string;
  onUpload: () => void;
  onSamples: () => void;
  onSettings: (s: Settings) => void;
  // The 個人化需求 as it will be sent: the kept lines, or the user's edit of them.
  prompt: string;
  onClear: () => void;
  // The text saved from the editor; it covers the first `upTo` notes.
  onPrompt: (text: string, upTo: number) => void;
  onEdit: () => void;
  onStart: (mode: "analyze" | "render") => void;
  // One of the options under the AI's question, tapped.
  onAnswer: (text: string) => void;
}) {
  const { caps, step, via, source, busy, notes, pending, scanning } = props;
  // 還沒分析，沒人有名字，AI 只能講站在哪裡（「從左邊數來第 2 位」）；那幾個字
  // 一樣換成頭像。
  const said = (text: string) =>
    faceText(
      text,
      props.people.flatMap((f) => [
        { ref: f.where || "", face: f },
        { ref: (f.where || "").replace(/^照片中/, ""), face: f },
      ]),
    );
  // 整段對話，照發生順序：每句需求一輪，使用者拿掉需求也一輪。
  const log = conversation(notes);
  // 最後一輪 AI 回完了，對話就算收在那裡，動作鍵接在它底下；AI 沒回（問不到）
  // 時才退回原本那句總結。
  const last = log[log.length - 1];
  const settled = !pending && !!last && ("gone" in last || !!last.ask.reply);
  // 換一張照片時需求會留著，所以這排按鍵在下一張還在偵測時也還在畫面上——那段
  // 期間一樣不能按，不然等於跳過剛換上來的這些人。
  const held = busy || scanning;
  // 送出前的最後一眼。對話往上捲是整段過程——被擋下來的句子、AI 回的話、設定
  // 卡夾在中間；這張只放真的會跟著進 session 的那段字，而且就擺在動作鍵正上方，
  // 要確認的東西跟要按下去的地方是同一處。跟 session 的個人化需求一樣是一整段，
  // 看到的就是送出去的 user_prompt；要刪要改直接改字。
  const kept = !!props.prompt;
  const review = kept && (
    <div class="chat-card chat-notes">
      <span class="chat-meta">個人化需求</span>
      <NotesEditor
        prompt={props.prompt}
        upTo={notes.length}
        faces={notes.flatMap((n) => n.persons)}
        held={held}
        onSave={props.onPrompt}
      />
    </div>
  );
  const startChips = (
    <div class="chat-choices chat-actions">
      {/* Reading order follows the order of operations: adjust the settings
          first, then run. 分析 and 一鍵美化 are peers -- both are "go", they
          differ only in how far they go -- so they carry the same weight. */}
      {(step === "picked" || kept) && (
        <div class="chat-actions-aux">
          {step === "picked" && <Chip label="設定" icon="gear" kind="ghost" disabled={held} onClick={props.onEdit} />}
          {/* 清掉的是需求，不是對話：上面那幾句連同 AI 的回覆都還在，只是不再算
              數。所以沒有需求可清時這顆就不該在——按下去畫面不會有任何變化。 */}
          {kept && (
            <Chip
              label="清除所有需求"
              icon="x"
              kind="ghost"
              disabled={held}
              onClick={props.onClear}
            />
          )}
        </div>
      )}
      <div class="chat-actions-main">
        <Chip label="分析" icon="search" kind="primary" disabled={held} onClick={() => props.onStart("analyze")} />
        <Chip label="一鍵美化" icon="spark" kind="primary" disabled={held} onClick={() => props.onStart("render")} />
      </div>
    </div>
  );
  // 清單跟著動作鍵走，所以只會出現一次：三個落點都是同一組收尾。
  const actions = (
    <>
      {review}
      {startChips}
    </>
  );

  return (
    <>
      <AssistantMsg>
        <p class="chat-text">{GREETING}</p>
        {/* Once one of these has been taken the turn is over: the chips freeze
            into the record of the choice, and changing photo goes forward
            through the composer's attach button instead of back up here.
            Dropping a file works too -- ChatPanel takes a drop anywhere on
            itself -- but that is a shortcut, not the advertised path. */}
        <div class="chat-choices">
          <Chip
            label="自行上傳照片"
            icon="up"
            kind={via === "upload" ? "picked" : undefined}
            settled={!!via}
            disabled={busy}
            onClick={props.onUpload}
          />
          <Chip
            label="從範例照片挑選"
            icon="image"
            kind={via === "sample" ? "picked" : undefined}
            settled={!!via}
            disabled={busy}
            onClick={props.onSamples}
          />
        </div>
      </AssistantMsg>

      {source && (
        <>
          <UserMsg>
            <UploadBubble src={sourceSrc(source)} text={source.kind === "file" ? "上傳了這張" : "選了這張"} />
          </UserMsg>
          {/* 偵測跑完才問「想怎麼修」：那句話要先數清楚照片裡有幾個人，底下選人
              的那排也要同時在位，使用者才有得點。 */}
          {scanning && (
            <AssistantMsg>
              <p class="chat-text chat-muted">先看一下照片裡有誰…</p>
            </AssistantMsg>
          )}
          {!scanning && (
          <AssistantMsg>
            {/* The picker below already says 指定對象; this line says what it is
                for, and says it in the same breath as the rest, so the greeting
                stays two thoughts instead of three announcements. */}
            <p class="chat-text">
              {props.faceCount > 1
                ? `照片選好了，裡面有 ${props.faceCount} 個人。想怎麼修就打在下面的對話框；只想改其中一位，點一下頭像就行。`
                : "照片選好了。想怎麼修，直接打在下面的對話框告訴我。"}
            </p>
            {/* 底下那三顆鍵都只有兩四個字，差在哪得自己猜。這裡一顆補一句：分
                析是先看過再決定，一鍵美化是現在就動手，設定是動手前先換工具。寫
                「讓你看」不寫「跟你確認」——分析完照樣可以直接按美化，一項一項
                挑是可以做的事，不是非做不可。 */}
            <p class="chat-text">要先看我對這張照片的分析，還是直接幫你美化？</p>
            <p class="chat-text chat-muted">分析會先讓你看我打算修什麼，一鍵美化就直接修完；想換引擎或長寬比，先按設定。</p>
            {step === "picked" && !notes.length && !pending && actions}
          </AssistantMsg>
          )}
        </>
      )}

      {source && step === "settings" && (
        <>
          <UserMsg>
            <ClickBubble icon="gear" label="設定" />
          </UserMsg>
          <AssistantMsg>
            <p class="chat-text">好，調整下面的設定，完成後選擇要分析還是一鍵美化。</p>
            <SettingsEditor caps={caps} settings={props.settings} onChange={props.onSettings} />
            {!notes.length && !pending && actions}
          </AssistantMsg>
        </>
      )}

      {/* 沒進清單的句子留在原位——刪掉整輪對話的話，畫面上就看不出曾經講過這
          件事、AI 又回了什麼，往上捲等於看到一份被修過的紀錄。AI 收不下的那
          句，它自己的回覆就說了為什麼；使用者自己拿掉的，底下接一句 AI 的回
          話。兩種都不動使用者打的字。 */}
      {log.map((t, i) => {
        // AI 還在等使用者回答它的提問時，那句需求還沒講完，清單跟開始鍵都先不出
        // 來——畫面上只留問題跟回答的方式，答完才接回收尾。問「要不要幫你分析」
        // 時也一樣：選項本身就是那兩顆鍵，再擺一次只是重複。
        const asking = "ask" in t && !!(t.ask.asked || t.ask.offered);
        const tail = i === log.length - 1 && settled && !asking && actions;
        if ("gone" in t)
          return (
            <AssistantMsg>
              <p class="chat-text">{goneReply(t)}</p>
              {tail}
            </AssistantMsg>
          );
        // AI 問回來的那句底下接選項。答過了（或話題已經往下走）就定格成紀錄，
        // 點過的那顆維持選取的樣子，跟開頭兩顆上傳鍵一樣。
        const options = t.ask.asked?.options || t.ask.offered?.options || [];
        const open = i === log.length - 1 && !pending;
        const next = notes[notes.indexOf(t.ask) + 1];
        return (
          <>
            <AskBubble text={t.ask.text} faces={t.ask.persons} />
            {t.ask.reply && (
              <AssistantMsg>
                <p class="chat-text">{said(t.ask.reply)}</p>
                {options.length > 0 && (
                  <div class="chat-choices">
                    {options.map((o) => (
                      <Chip
                        label={o}
                        kind={!open && next?.text === o ? "picked" : undefined}
                        settled={!open}
                        disabled={held}
                        onClick={() => props.onAnswer(o)}
                      />
                    ))}
                  </div>
                )}
                {tail}
              </AssistantMsg>
            )}
          </>
        );
      })}
      {pending && (
        <>
          <AskBubble text={pending.text} faces={pending.persons} />
          <AssistantMsg>
            <div class="chat-typing" aria-label="AI 正在回覆">
              <span />
              <span />
              <span />
            </div>
          </AssistantMsg>
        </>
      )}
      {notes.length > 0 && !pending && !settled && (
        <AssistantMsg>
          <p class="chat-text">
            {kept ? "好，這些會當成個人化需求，美化時一起套用。" : "好，那就先不加個人化需求。"}
          </p>
          {actions}
        </AssistantMsg>
      )}

      {busy && source && !pending && !scanning && (
        <AssistantMsg>
          <p class="chat-text chat-muted">正在上傳照片…</p>
        </AssistantMsg>
      )}
      {props.error && <ErrorMsg text={props.error} />}
    </>
  );
}

function ErrorMsg({ text }: { text: string }) {
  return (
    <AssistantMsg>
      <p class="chat-text chat-error">出了點問題：{text}</p>
    </AssistantMsg>
  );
}

// ── Session ────────────────────────────────────────────────────────────────
// 對提案單純說好。
const YES = /^(好|好啊|好喔|好的|好呀|好哇|可以|可以啊|可以喔|ok|okay|嗯|嗯嗯|對|對啊|沒問題|就這樣|套用|確認|確定|麻煩了|麻煩你了)[。！!～~，,.\s]*$/i;

// AI 在 from 之後回過一句收尾（close）沒有。
function closedAfter(messages: Message[], from: number): boolean {
  return messages.some((m, i) => i > from && m.card?.type === "llm_reply" && !!m.card?.close);
}

// The plan fields a round of adjusting can change (by clicks or a typed
// request). 調整後再美化 keeps them, so 美化 can ask first when none changed.
const ADJUSTABLE = [
  "recipe_checked", "face_issue_overrides", "style_options", "render_option", "aspect_ratio",
  "advanced_options", "preset", "run_count", "user_prompt",
] as const;

// Key order and cleared (null) entries don't count as a change.
function canonical(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(canonical);
  if (v && typeof v === "object") {
    const out: Dict = {};
    for (const k of Object.keys(v).sort()) {
      const x = (v as Dict)[k];
      if (x != null) out[k] = canonical(x);
    }
    return out;
  }
  return v;
}

function snapshotOf(plan: Plan): Dict {
  const out: Dict = { user_prompt: plan.user_prompt || "" };
  for (const k of ADJUSTABLE) if (plan[k] !== undefined) out[k] = plan[k];
  return canonical(out) as Dict;
}

function SessionView(props: {
  caps: Capabilities;
  debugUi: boolean;
  session: Session;
  photo: Photo;
  analysis: Analysis | null;
  selected: string | null;
  pendingAsk: { text: string; subjectIndices: number[] } | null;
  busy: boolean;
  error: string;
  onSelectVersion: (v: Version) => void;
  onRender: (label: string, icon: string, patch?: Plan) => void;
  // 只剩拖放進畫布這條路會用到 -- 丟一張新的照片進來等於開一個新對話。
  onFile: (f: File) => void;
  onAsk: (text: string, subjectIndices: number[], screen?: Screen) => Promise<boolean>;
  onAnswerWaiting: (messageId: string, screen: Screen) => void;
  onPatch: (patch: Plan) => Promise<boolean>;
  onSavePrompt: (text: string) => Promise<boolean>;
  onPost: (card: Dict, patch?: Plan, merge?: boolean) => Promise<boolean>;
  onResolve: (
    proposal: Proposal,
    choice: ProposalChoice,
    startRound?: boolean,
    edit?: { field: string; value: string },
  ) => Promise<boolean>;
}) {
  const { caps, session, photo, analysis, busy } = props;
  const pid = photo.photo_id;
  const messages = (session.messages || []).filter((m) => !m.card?.photo_id || m.card.photo_id === pid);
  const versions = versionsFromMessages(messages, pid);
  const current =
    versions.find((v) => v.version_id === props.selected) ||
    versions.find((v) => v.version_id === photo.active_version?.version_id) ||
    versions[versions.length - 1] ||
    null;
  const thumb = urls.sourceThumb(photo.analyze_job_id);
  const source = urls.sourceImage(photo.analyze_job_id);

  // The options can be changed in one round at a time: after analysis until
  // the first render, and from 調整後再美化 until its render. What was said or
  // done stays; changing it back is another step. (adjust_cancelled is only
  // in older sessions.)
  const lastIndex = (test: (type: string) => boolean) => {
    for (let i = messages.length - 1; i >= 0; i--) if (test(messages[i].card?.type || "")) return i;
    return -1;
  };
  const readyIdx = messages.findIndex((m) => m.card?.type === "analysis_ready");
  const lastRender = lastIndex((t) => /^render_/.test(t));
  const lastAdjust = lastIndex((t) => t === "adjust_opened");
  const lastCancel = lastIndex((t) => t === "adjust_cancelled");
  // Only rounds opened by 調整後再美化 carry the plan they started from.
  const roundPlan: Dict | undefined = lastAdjust >= 0 ? messages[lastAdjust].card?.plan : undefined;
  const lastOutcome = [...messages].reverse().find((m) => m.card?.type === "render_done" || m.card?.type === "render_failed");
  const ready = photo.status === "ready" && !!analysis;
  const fresh = ready && readyIdx >= 0 && lastRender < 0;
  const roundOpen = ready && lastAdjust > lastRender && lastAdjust > lastCancel;
  const unchanged =
    roundOpen && !!roundPlan && JSON.stringify(canonical(roundPlan)) === JSON.stringify(snapshotOf(photo.plan));
  // 使用者說「不用了」時 AI 會把回覆標成 close：這一輪什麼都沒改，就收起調整，
  // 回到美化完的樣子。改過的話還有東西等著美化，維持調整中。
  const adjusting = roundOpen && !(unchanged && closedAfter(messages, lastAdjust));
  const editing = fresh || adjusting;
  const roundStart = adjusting ? lastAdjust : readyIdx;
  // 麵包屑要等 adjusting 算出來才知道這張到底算不算做完了。
  const [stepN, stepHow] = stepState(photo, versions, adjusting);
  // 美化設定 is offered while adjusting, and before the first render only
  // once a typed request opened it.
  const baseCats = analysis ? categoriesFor(analysis, caps, photo.plan) : [];
  const opened: Category[] = [];
  // 略過也是走過一步：跟開過的面板一起決定接下來還要談什麼，也讓重整後回到同
  // 一個位置。
  const skipped: Category[] = [];
  let lastOptionsIdx = -1;
  let lastStepIdx = -1;
  if (editing) {
    messages.forEach((m, i) => {
      if (i <= roundStart) return;
      const cat = m.card?.category as Category;
      if (!CATEGORIES[cat]) return;
      if (m.card?.type === "options_opened") {
        opened.push(cat);
        lastOptionsIdx = i;
        lastStepIdx = i;
      } else if (m.card?.type === "topic_skipped") {
        skipped.push(cat);
        lastStepIdx = i;
      }
    });
  }
  const cats: Category[] = adjusting || opened.includes("settings") ? [...baseCats, "settings"] : baseCats;
  const lastOpened = opened.length ? opened[opened.length - 1] : null;
  // 分析後的第一輪用漸進引導：排在最前面、還沒看過也還沒略過的那一項，就是
  // 下一句要談的。（調整後再美化那一輪維持原本的選單。）
  const guideQueue = cats.filter((c) => !opened.includes(c) && !skipped.includes(c));
  const nextCat: Category | null = fresh ? guideQueue[0] || null : null;
  // 這一輪動過、但還沒美化的項目。roundPlan 是開這輪時的設定，photo.plan 是
  // 現在的；把差出來的欄位換成項目名稱，離開再回來時才看得出自己剛剛改了
  // 什麼，而不是只看到一顆孤零零的「美化」。
  const dirtyCats: Category[] = [];
  if (adjusting && roundPlan && !unchanged) {
    const was = canonical(roundPlan) as Dict;
    const now = snapshotOf(photo.plan);
    const touched = new Set<Category>();
    const sub = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) ? (v as Dict) : null);
    for (const k of new Set([...Object.keys(was), ...Object.keys(now)])) {
      if (JSON.stringify(was[k]) === JSON.stringify(now[k])) continue;
      // 整個 key 被加上或清掉時，另一邊當成空物件，子欄位才還認得出屬於誰。
      const a = sub(was[k]) || (sub(now[k]) ? {} : null);
      const b = sub(now[k]) || (sub(was[k]) ? {} : null);
      if (a && b) {
        for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
          if (JSON.stringify(a[key]) === JSON.stringify(b[key])) continue;
          const c = categoryOf(`${k}.${key}`);
          if (c) touched.add(c);
        }
      } else {
        const c = categoryOf(k);
        if (c) touched.add(c);
      }
    }
    for (const c of cats) if (touched.has(c)) dirtyCats.push(c);
  }

  // Who the composer is pointed at, and the option fields to flash.
  const [pointed, setPointed] = useState<number[]>([]);
  const [composerKey, setComposerKey] = useState(0);
  const [hot, setHot] = useState<Partial<Record<Category, Hot>>>({});
  // 再美化一次, or 美化 in a round with nothing changed, asks first; any new
  // message or change drops the question.
  const [confirmRerun, setConfirmRerun] = useState(false);
  // Bumped when an answer lands at the end of the talk and the timeline should
  // go there, even if the user had scrolled up.
  const [toBottom, setToBottom] = useState(0);
  useEffect(() => {
    setPointed([]);
    setHot({});
  }, [pid]);
  useEffect(() => setConfirmRerun(false), [pid, messages.length, messages[messages.length - 1]?.card?.category, unchanged]);

  const people = facePeople(analysis);
  const thumbs = useFaceThumbs(analysis, source);
  // 上傳頁面帶過來的句子，指的人當時還沒有名字：頭像照它帶來的框裁，名字留白，
  // 跟上傳頁面上那顆泡泡長得一樣。
  // 一句可以指好幾位（`faces`）；更早的訊息只帶一個 `box`/`where`。
  const preFacesOf = (card: Dict | null | undefined): { box: Box; where?: string }[] =>
    Array.isArray(card?.faces) ? card!.faces : card?.box ? [{ box: card.box, where: card.where }] : [];
  const preAsks = messages.filter((m) => m.card?.type === "ask" && preFacesOf(m.card).length);
  const preBoxes = preAsks.flatMap((m) => preFacesOf(m.card));
  const preThumbs = useBoxThumbs(preBoxes.map((f) => f.box), source);
  const preFaces = (m: (typeof messages)[number]): FaceRef[] => {
    const i = preAsks.indexOf(m);
    if (i < 0) return [];
    const at = preAsks.slice(0, i).reduce((n, q) => n + preFacesOf(q.card).length, 0);
    return preFacesOf(m.card).map((f, k) => ({ name: "", thumb: preThumbs[at + k] || "", where: f.where }));
  };
  // Who a sent line pointed at; `subject_index` is the one-face form older
  // messages carry.
  const askedIndices = (card: Dict | null | undefined): number[] =>
    Array.isArray(card?.subject_indices) ? card!.subject_indices : card?.subject_index != null ? [card.subject_index] : [];
  // 美化中的副標，和左邊畫布上的那一句同一份文案。
  const renderSub = useRotatingText(renderSubMessages(caps, photo.plan), photo.status === "rendering");
  const faces: Record<number, FaceRef> = {};
  const pickable: (FaceRef & { subjectIndex: number })[] = [];
  people.forEach((p, i) => {
    if (p.subjectIndex < 0 || faces[p.subjectIndex]) return;
    faces[p.subjectIndex] = { name: p.name, thumb: thumbs[i] || "" };
    pickable.push({ ...faces[p.subjectIndex], subjectIndex: p.subjectIndex });
  });
  // 聊到人的時候用頭像講，不念名字：AI 的話、引導的那幾句裡寫到某人的名字，
  // 就換成那個人的頭像。
  const said = (text: string) => faceText(text, pickable.map((p) => ({ ref: p.name, face: p })));
  // An answer under the AI's question that is just someone's name.
  const faceOption = (o: string) => pickable.find((p) => p.name === o.trim()) || null;

  const liveCat: Category | null = lastOptionsIdx >= 0 ? messages[lastOptionsIdx].card?.category : null;
  const openCategory = (cat: Category) => {
    setHot({});
    // Going from one category to the next is one step: one message -- except
    // in the guided first round, where each topic is a turn in the talk and
    // rewriting the last one would delete what was just agreed.
    // plan: 開下一個面板就是把上一個收起來，所以這張卡身上帶的，正好是上一段
    // 談完時的樣子 -- 收起後那句結論要講當時的事，不能跟著後來的修改一起變。
    const posted = props.onPost(
      { type: "options_opened", category: cat, plan: snapshotOf(photo.plan) },
      undefined,
      !fresh,
    );
    // 叫回來的面板一定在對話末端，所以捲到底就是捲到它 -- 不然在上面點「再看
    // 人像」，面板開在看不到的地方，等於沒反應。
    return posted.then((ok) => {
      if (ok) setToBottom((n) => n + 1);
      return ok;
    });
  };
  // reveal: scroll the card into view, for a card that may be further up.
  const flash = (cat: Category, fields: string[], reveal = false) =>
    setHot((h) => ({ ...h, [cat]: { fields, nonce: Date.now(), reveal } }));
  // Flash a field in the round's card for it, adding that card when there is none.
  const showOption = (field: string) => {
    const cat = categoryOf(field);
    if (!cat || !editing) return;
    if (liveCat === cat) flash(cat, [field], true);
    else openCategory(cat).then((ok) => ok && flash(cat, [field], true));
  };
  const catChips = (except?: Category | null) =>
    cats
      .filter((c) => c !== except)
      .map((c) => (
        <Chip label={CATEGORIES[c].label} icon={CATEGORIES[c].icon} disabled={busy} onClick={() => openCategory(c)} />
      ));
  const skipTopic = (cat: Category) => {
    setHot({});
    // 不 merge：連著略過兩項要留下兩筆，不然就不知道已經走到哪一項了。
    return props.onPost({ type: "topic_skipped", category: cat });
  };
  // 漸進引導的一句：講下一個類別偵測到什麼、會怎麼處理，再給「看」和「略過」
  // 兩條路。略過那顆直接把下一項的名字寫出來（「略過，看風格」），不寫「下一
  // 項」：跳過之後會跑到哪裡，應該在按之前就知道。每一步都要提到可以不逐項看、直接開始修 -- 那是這個流程最容易把人
  // 困住的地方。hint: null 用在開場，因為那則訊息自己有更完整的說明。
  const guideTail = (opts: { lead?: string; except?: Category; hint?: null } = {}) => {
    const pre = opts.lead || "";
    const renderLabel = lastOpened ? "美化" : "照建議直接美化";
    if (!analysis || !nextCat)
      return (
        <>
          <p class="chat-text">{said(pre)}這樣就可以了，按下面的「{renderLabel}」我就開始美化。想再看哪一項，點一下也行。</p>
          <div class="chat-choices">{catChips(opts.except)}</div>
        </>
      );
    return (
      <>
        <p class="chat-text">
          {said(pre)}
          {said(topicLead(nextCat, analysis, caps, photo.plan))}
        </p>
        <div class="chat-choices">
          <Chip
            label={`看${CATEGORIES[nextCat].label}`}
            icon={CATEGORIES[nextCat].icon}
            disabled={busy}
            onClick={() => openCategory(nextCat)}
          />
          <Chip
            label={guideQueue[1] ? `略過，看${CATEGORIES[guideQueue[1]].label}` : "不用看了"}
            icon="arrow"
            kind="ghost"
            disabled={busy}
            onClick={() => skipTopic(nextCat)}
          />
        </div>
        {opts.hint !== null && (
          <p class="chat-text chat-muted">不想一項一項看的話，按下面的「{renderLabel}」我就直接開始修。</p>
        )}
      </>
    );
  };

  // 分析後第一次的引導（下一項要不要看，加上「照建議直接美化」的提示）。
  const firstGuide = (lead?: string) => (
    <>
      {nextCat ? guideTail({ lead, hint: null }) : lead && <p class="chat-text">{said(lead)}</p>}
      {/* The face row above the composer is the only way to aim a message
          at one person, and a row of crops does not say so by itself. The
          chat says it once, here -- right after the user has been told who
          is in the photo, which is when they think to ask. */}
      {(baseCats.length > 0 || pickable.length > 1) && (
        <p class="chat-text chat-muted">
          {baseCats.length > 0
            ? `不想一項一項看的話，按下面的「${lastOpened ? "美化" : "照建議直接美化"}」我就直接開始修；也可以打字告訴我想怎麼改。`
            : "直接打字告訴我想怎麼改就可以。"}
          {pickable.length > 1 && "想只調整其中一位，點輸入框上方的頭像。"}
        </p>
      )}
    </>
  );

  // Only the newest proposal can still be acted on.
  const resolvedChoice: Record<string, ProposalChoice> = {};
  const resolvedEdit: Record<string, { field: string; value: string }> = {};
  for (const m of messages) {
    if (m.card?.type !== "proposal_resolved") continue;
    resolvedChoice[m.card.proposal_id] = m.card.choice;
    if (m.card.edit) resolvedEdit[m.card.proposal_id] = m.card.edit;
  }
  const lastProposalId = [...messages].reverse().find((m) => m.card?.type === "llm_reply" && m.card.proposal)?.card?.proposal.id;
  const brief: Dict | null = messages.find((m) => m.card?.type === "analysis_brief")?.card || null;

  // 按下「套用」的那一刻就已經確認了，所以不再自動把面板攤開要人再看一次。改到
  // 的是哪幾項先記下來，等使用者從下面的項目點進去時再標出來。
  async function choose(proposal: Proposal, choice: ProposalChoice, edit?: { field: string; value: string }) {
    // 文字框裡的字可能被改過；真的改了才算，照原樣套用就不必留下紀錄。
    const was = edit ? proposal.changes.find((ch) => ch.field === edit.field) : undefined;
    const typed = edit && choice === "apply" && was && was.after !== edit.value ? edit : undefined;
    const applied = typed
      ? {
          ...proposal,
          patch: { ...proposal.patch, [typed.field]: typed.value },
          changes: proposal.changes.map((ch) => (ch.field === typed.field ? { ...ch, after: typed.value } : ch)),
        }
      : proposal;
    if (!(await props.onResolve(applied, choice, ready && !editing && lastRender >= 0, typed))) return;
    if (choice === "revise") setComposerKey((k) => k + 1);
    if (choice !== "apply") return;
    const byCat: Partial<Record<Category, string[]>> = {};
    for (const ch of applied.changes) {
      const cat = categoryOf(ch.field);
      if (cat) (byCat[cat] ||= []).push(ch.field);
    }
    for (const cat of Object.keys(byCat) as Category[]) flash(cat, byCat[cat]!);
  }

  // AI 剛提出修改、最新一則就是那張還沒處理的提案時，打一個「好」就等於按「套用」。
  // 只認單純的同意詞；「好，但再亮一點」這種帶著新要求的照常交給 AI。
  const lastCard = messages[messages.length - 1]?.card;
  const pendingProposal: Proposal | null =
    lastCard?.type === "llm_reply" && lastCard.proposal && !resolvedChoice[lastCard.proposal.id] ? lastCard.proposal : null;
  // AI 剛問完一句（沒給選項）時，點好頭像不打字直接送出，就是回答「這幾位」。
  const whoAsked =
    lastCard?.type === "llm_reply" && !lastCard.options?.length && /[？?]\s*$/.test(lastCard.text || "");
  async function send(text: string, si: number[]) {
    if (pendingProposal && YES.test(text.trim())) {
      await choose(pendingProposal, "apply");
      return true;
    }
    return props.onAsk(text || (si.length > 1 ? "這幾位" : "這位"), si, screen);
  }

  // 這張面板卡收起時的 plan。會收起它的那一步 -- 下一個開起來的面板，或下一輪
  // 的「調整後再美化」-- 身上都帶著當下的快照，所以往後找第一張帶 plan 的卡就
  // 是它。（略過不收面板，也就不帶 plan。）找不到就是還沒被任何一步收起來，用
  // 現在這份。舊 session 的卡沒有快照，退回同一條路，結果跟以前一樣。
  const planAt = (idx: number): Plan => {
    for (let i = idx + 1; i < messages.length; i++) {
      const snap = messages[i].card?.plan;
      if (snap) return { ...photo.plan, ...(snap as Plan) };
    }
    return photo.plan;
  };

  function replyBody(m: (typeof messages)[number], idx: number) {
    const c = m.card!;
    return (
      <>
        {c.text && <p class="chat-text">{said(c.text)}</p>}
        {c.proposal && (
          <ProposalCard
            proposal={c.proposal}
            faces={faces}
            resolved={resolvedChoice[c.proposal.id] || (c.proposal.id === lastProposalId ? null : "stale")}
            undoable={c.proposal.id === lastProposalId && idx > lastRender}
            edit={resolvedEdit[c.proposal.id] || null}
            busy={busy}
            onChoose={(choice, edit) => choose(c.proposal, choice, edit)}
            onShow={(ch: Change) => showOption(ch.field)}
          />
        )}
        {/* 直接套用的卡後面沒有使用者的那一下，引導就接在同一則話裡。 */}
        {c.proposal && idx + 1 === guideAt && messages[idx + 1].card?.auto && firstGuide()}
        {!c.proposal && (() => {
          // 對話不停在一句「再跟我說」。AI 問回來的話，底下是它給的答案，點了就
          // 當成使用者打的下一句送出，指著誰問的就繼續指著誰；答過了定格成紀錄，
          // 點過的那顆標起來。沒問問題時，調整中就把能打開的項目接在底下（AI 的
          // 話會指著它們講）；修完一版時要按的「調整／再美化」本來就在下方那排。
          const open = idx === messages.length - 1 && !props.pendingAsk;
          if (c.options?.length > 0) {
            const next = messages[idx + 1];
            const asked = messages[idx - 1]?.card;
            const si = asked?.type === "ask" ? askedIndices(asked) : [];
            if (c.lines)
              return (
                <LinesCard
                  lines={c.options as string[]}
                  picked={next?.card?.type === "ask" ? next.text || "" : null}
                  open={open}
                  busy={busy}
                  onPick={(t) => props.onAsk(t, si, screen)}
                />
              );
            // 問的是哪一位時，答案就是那幾顆頭像：點一顆等於指著他說「這位」，
            // 跟點輸入框上方的頭像再送出是同一句。
            const nextSi = next?.card?.type === "ask" ? askedIndices(next.card) : [];
            return (
              <div class="chat-choices">
                {(c.options as string[]).map((o) => {
                  const face = faceOption(o);
                  if (face) {
                    const picked = !open && nextSi.length === 1 && nextSi[0] === face.subjectIndex;
                    return (
                      <FaceChip
                        face={face}
                        avatarOnly
                        picked={picked}
                        title={`就是${face.name}`}
                        onClick={open && !busy ? () => props.onAsk("這位", [face.subjectIndex], screen) : undefined}
                      />
                    );
                  }
                  return (
                    <Chip
                      label={o}
                      kind={!open && next?.card?.type === "ask" && next.text === o ? "picked" : undefined}
                      settled={!open}
                      disabled={busy}
                      onClick={() => props.onAsk(o, si, screen)}
                    />
                  );
                })}
              </div>
            );
          }
          if (idx === guideAt) return firstGuide();
          return open && editing && cats.length > 0 ? <div class="chat-choices">{catChips()}</div> : null;
        })()}
      </>
    );
  }

  const typing = (
    <div class="chat-typing" aria-label="AI 正在回覆">
      <span />
      <span />
      <span />
    </div>
  );

  // 上傳頁面帶過來、還沒回答的那句問題。
  const waiting = messages.find(
    (m) => m.card?.type === "ask" && m.card.waiting && !messages.some((x) => x.card?.answers === m.id),
  );
  const answered = useRef<string | null>(null);
  // 那句問題的回答接在照片好了的那則訊息裡（分析摘要，或一鍵美化的成品），同一顆
  // 頭像講完，不另起一段。回答還在路上時，那則訊息先停在摘要，底下是打字中的三個
  // 點；接下來的引導等回答到了再一起出現，版面才不會在中間被插一段。
  const HOSTS = ["analysis_ready", "render_done"];
  const holding = (idx: number) =>
    step(idx, 1) >= messages.length && ready && !!waiting && (!!props.pendingAsk || answered.current !== waiting.id);
  // 分析摘要（analysis_brief）跟在 analysis_ready 後面寫進來，但畫面上不另外顯示，
  // 找前後一則時要跳過它。
  const SILENT = ["analysis_brief"];
  const step = (idx: number, dir: 1 | -1) => {
    let i = idx + dir;
    while (SILENT.includes(messages[i]?.card?.type || "")) i += dir;
    return i;
  };
  const answerIn = (idx: number) => {
    if (holding(idx)) return typing;
    const i = step(idx, 1);
    const next = messages[i];
    return next?.card?.type === "llm_reply" && next.card.answers ? replyBody(next, i) : null;
  };

  // 帶過來的問題如果要使用者挑一個（文案、選項）或確認一個提案，先讓他把這件事
  // 做完，人像／修圖建議的引導等這段對話停下來再接上，不要兩件事同時丟給他。
  const carriedIdx = readyIdx >= 0 ? step(readyIdx, 1) : -1;
  const carried = carriedIdx >= 0 ? messages[carriedIdx]?.card : undefined;
  const deferGuide =
    carried?.type === "llm_reply" && !!carried.answers &&
    ((!!carried.proposal && !carried.proposal.direct) || carried.options?.length > 0);
  const tail = messages[messages.length - 1]?.card;
  const guideAt =
    fresh && deferGuide && lastStepIdx < 0 && !busy && !props.pendingAsk && messages.length - 1 > carriedIdx &&
    ((tail?.type === "llm_reply" && !tail.proposal && !(tail.options?.length > 0)) ||
      (tail?.type === "proposal_resolved" && !!PROPOSAL_CHOICES[tail.choice as ProposalChoice]))
      ? messages.length - 1
      : -1;

  const items = messages.map((m, idx) => {
    const c = m.card;
    switch (c?.type) {
      case "photos_added":
        return (
          <>
            <UserMsg meta={clockLabel(m.created_at)}>
              <UploadBubble src={thumb} text={`上傳了 ${c.photo_ids?.length || 1} 張照片`} />
            </UserMsg>
          </>
        );
      case "analysis_ready": {
        if (!analysis) return <AssistantMsg><p class="chat-text">分析完成。</p></AssistantMsg>;
        return (
          <AssistantMsg>
            {brief ? (
              <BriefCard brief={brief} />
            ) : (
              <p class="chat-text">分析完成。</p>
            )}
            {answerIn(idx)}
            {fresh && !holding(idx) && !deferGuide && lastStepIdx < 0 && firstGuide()}
          </AssistantMsg>
        );
      }
      case "options_opened": {
        const cat = c.category as Category;
        if (!CATEGORIES[cat] || !analysis) return null;
        const live = editing && idx === lastOptionsIdx;
        // via: 以前「套用」會順手把面板攤開來當作回覆，那種卡沒有點擊泡泡，開場
        // 白講的是剛剛落在面板裡的東西。現在套用只給項目按鈕，所以這條路只剩舊
        // session 的紀錄會走到，留著是為了那些對話讀起來還是順的。
        const answersApply = c.via === "proposal";
        // 套用開新一輪時，開場白本來在前一張 adjust_opened 卡上。那張卡現在不
        // 出聲，話接到這裡，一顆頭像把「已套用、開新一輪、先看這一項」講完。
        const fromApply = answersApply && messages[idx - 1]?.card?.type === "adjust_opened";
        const renders = messages.slice(0, idx).filter((x) => x.card?.type === "render_done").length;
        // 版號在右邊的美化紀錄裡看得到，話裡再數一次只是把人往回拉。這裡只說
        // 「又開了一輪」，以及舊的那版不會被蓋掉。
        const roundLine = fromApply ? "已套用，開始新的一輪調整。" : "";
        const lead = !answersApply
          ? introFor(cat, analysis, planAt(idx), messages.slice(0, idx).some((x) => /^render_/.test(x.card?.type || "")))
          : fromApply
            ? `先看剛套用的${CATEGORIES[cat].label}，改動的地方已經標起來了。`
            : `好，已套用。這是更新後的${CATEGORIES[cat].label}，改動的地方已經標起來了。`;
        const recap = live ? "" : topicRecap(cat, analysis, caps, planAt(idx));
        return (
          <>
            {!answersApply && (
              <UserMsg meta={clockLabel(m.created_at)}>
                <ClickBubble icon={CATEGORIES[cat].icon} label={CATEGORIES[cat].label} />
              </UserMsg>
            )}
            <AssistantMsg>
              {live ? (
                <>
                  {!!roundLine && <p class="chat-text">{roundLine}</p>}
                  <p class="chat-text">{said(lead)}</p>
                  <OptionsCard
                    category={cat}
                    analysis={analysis}
                    plan={photo.plan}
                    caps={caps}
                    faces={faces}
                    busy={busy}
                    hot={hot[cat] || null}
                    onPatch={(p) => props.onPatch(p)}
                    onSavePrompt={(t) => props.onSavePrompt(t)}
                  />
                  {fresh ? (
                    idx === lastStepIdx && guideTail({ except: cat })
                  ) : (
                    <>
                      <p class="chat-text">
                        {cats.length > 1 ? "調整好後，可以接著看其他項目，" : "調整好後，"}
                        或按下方的「美化」我就開始
                        {fromApply && renders ? "，之前的版本會留在美化紀錄" : ""}。
                      </p>
                      {cats.length > 1 && <div class="chat-choices">{catChips(cat)}</div>}
                    </>
                  )}
                </>
              ) : (
                // 收起的只是那一堆開關：說過的話、談成的結果都留著，要再看一眼
                // 的路就在同一則訊息裡。
                <>
                  {!!roundLine && <p class="chat-text chat-muted">{roundLine}</p>}
                  <p class="chat-text chat-muted">{said(lead)}</p>
                  {!!recap && <p class="chat-recap">{said(recap)}</p>}
                  {/* 再回去改只有這一輪還開著時做得到；美化完就只剩那句結論。 */}
                  {editing && idx > roundStart && (
                    <div class="chat-choices">
                      <Chip
                        label={`再看${CATEGORIES[cat].label}`}
                        icon={CATEGORIES[cat].icon}
                        kind="ghost"
                        disabled={busy}
                        onClick={() => openCategory(cat)}
                      />
                    </div>
                  )}
                </>
              )}
            </AssistantMsg>
          </>
        );
      }
      case "topic_skipped": {
        const cat = c.category as Category;
        if (!CATEGORIES[cat]) return null;
        return (
          <>
            <UserMsg meta={clockLabel(m.created_at)}>
              <ClickBubble icon="arrow" label={`略過${CATEGORIES[cat].label}`} />
            </UserMsg>
            {fresh && idx === lastStepIdx && <AssistantMsg>{guideTail({ lead: "好，" })}</AssistantMsg>}
          </>
        );
      }
      case "adjust_opened": {
        // 直接套用時順手開的這一輪，上面那則回覆已經交代過了。
        if (c.auto) return null;
        const live = adjusting && idx === lastAdjust;
        const n = messages.slice(0, idx).filter((x) => x.card?.type === "render_done").length;
        // 套用之後面板馬上就開在下一則訊息裡，那張卡會把這一輪的開場一起講掉。
        // 這裡再講一次就變成兩顆頭像說同一件事，還會問一個自己已經答了的問題。
        const opensPanel = messages[idx + 1]?.card;
        if (c.via === "proposal" && opensPanel?.type === "options_opened" && opensPanel.via === "proposal") return null;
        // 套用完的開場就把下一步問掉：直接美化，還是先改下面這幾項。項目一旦點
        // 開，問句沒了對象，話就收回成單純的一句交代。
        const showChips = live && !opened.length;
        const openLine =
          c.via === "proposal"
            ? showChips
              ? "已套用，想直接美化，還是再調下面這幾項？"
              : "已套用，開始新的一輪調整。"
            : "好，從上次的設定開始調整。";
        return (
          <>
            {c.via !== "proposal" && (
              <UserMsg meta={clockLabel(m.created_at)}>
                <ClickBubble icon="sliders" label="調整" />
              </UserMsg>
            )}
            <AssistantMsg>
              <p class="chat-text">{openLine}</p>
              {live && (
                <>
                  {/* 再進來的人最容易搞混兩件事：建議是對哪張照片講的、開關又是從哪來的。
                      建議永遠是看原圖分析的，不會因為已經修過一版就換；開關則是上一版的。 */}
                  {c.via !== "proposal" && (
                    <p class="chat-text chat-muted">
                      裡面的項目都是看原圖分析出來的，有小圓點的是建議調整的地方；哪些開著、哪些關著，則沿用上次美化的設定。
                    </p>
                  )}
                  {showChips && c.via !== "proposal" && (
                    <p class="chat-text">可以調整{cats.map((x) => CATEGORIES[x].label).join("、")}，想先看哪一項？</p>
                  )}
                  {showChips && <div class="chat-choices">{catChips()}</div>}
                  <p class="chat-text chat-muted">
                    改好按下方的「美化」我就開始{n ? "，之前的版本會留在美化紀錄" : ""}。
                  </p>
                </>
              )}
            </AssistantMsg>
          </>
        );
      }
      case "adjust_cancelled":
        return (
          <>
            <UserMsg meta={clockLabel(m.created_at)}>
              <ClickBubble icon="x" label="取消調整" />
            </UserMsg>
            <AssistantMsg>
              <p class="chat-text">好，設定已還原成調整前的樣子。</p>
            </AssistantMsg>
          </>
        );
      case "analysis_failed":
        return (
          <AssistantMsg>
            <p class="chat-text chat-error">
              這張照片分析沒有成功：{c.error}
              {c.queued_render_cancelled ? "。排定的美化也一併取消了。" : ""}
            </p>
          </AssistantMsg>
        );
      case "render_requested": {
        const meta = renderMeta(caps, photo.plan);
        const l = renderLabelFor([m.id]) || (c.label ? { label: c.label, icon: "spark", meta } : { label: "美化", icon: "spark", meta });
        return (
          <UserMsg meta={[l.meta, clockLabel(m.created_at)].filter(Boolean).join(" · ")}>
            <ClickBubble icon={l.icon} label={l.label} />
          </UserMsg>
        );
      }
      case "render_done": {
        const v = versions.find((x) => x.version_id === c.version_id);
        if (!v) return null;
        return (
          <AssistantMsg>
            <ResultCard
              job={versionJob(v)}
              ordinal={v.ordinal}
              failed={(c.failed_job_ids || []).length}
              next={idx === lastRender && !adjusting}
            />
            {answerIn(idx)}
          </AssistantMsg>
        );
      }
      case "render_failed":
        return (
          <AssistantMsg>
            <p class="chat-text chat-error">這次美化沒有成功：{c.error}</p>
          </AssistantMsg>
        );
      case "ask":
        return (
          <AskBubble
            text={m.text || c.text || ""}
            faces={askedIndices(c).length ? askedIndices(c).flatMap((i) => faces[i] || []) : preFaces(m)}
            meta={clockLabel(m.created_at)}
          />
        );
      case "llm_reply":
        if (c.answers && HOSTS.includes(messages[step(idx, -1)]?.card?.type || "")) return null;
        return <AssistantMsg>{replyBody(m, idx)}</AssistantMsg>;
      case "llm_error":
        return (
          <AssistantMsg>
            <p class="chat-text chat-muted">AI 暫時無法回覆，可以直接點選項調整。</p>
          </AssistantMsg>
        );
      case "proposal_resolved": {
        // 套用並美化 (older proposals) shows as the render_requested bubble that follows.
        if (c.choice === "apply_render" || !PROPOSAL_CHOICES[c.choice as ProposalChoice]) return null;
        // 直接套用不是使用者按的，不畫一顆他沒按過的按鈕；卡片自己寫著「已套用」。
        if (c.auto) return null;
        const l = PROPOSAL_CHOICES[c.choice as ProposalChoice];
        const lead = c.choice === "apply" ? "好，已套用。" : c.choice === "undo" ? "好，已復原。" : "好，這次不套用。";
        // 套用 is normally answered by the changed panel opening below it. When
        // nothing followed (no field the panels show, or the post failed), say
        // where to go next rather than leaving the bubble hanging.
        const dangling = idx === messages.length - 1 && !busy;
        return (
          <>
            <UserMsg meta={clockLabel(m.created_at)}>
              <ClickBubble icon={l.icon} label={l.label} />
            </UserMsg>
            {idx === guideAt && <AssistantMsg>{firstGuide(lead)}</AssistantMsg>}
            {dangling && idx !== guideAt && (
              <AssistantMsg>
                <p class="chat-text">
                  {c.choice === "undo"
                    ? "好，已復原。想怎麼改再告訴我。"
                    : c.choice !== "apply"
                    ? "好，這次不套用。想怎麼改再告訴我。"
                    : editing && cats.length
                      ? "好，已套用。想看改了什麼、或接著調整其他項目，點下面的項目；改好按下方的「美化」我就開始。"
                      : "好，已套用。想再改什麼直接告訴我，或按下方的「美化」我就開始。"}
                </p>
                {c.choice === "apply" && editing && !!cats.length && <div class="chat-choices">{catChips()}</div>}
              </AssistantMsg>
            )}
          </>
        );
      }
      default:
        return null;
    }
  });

  // First words, before the server's own messages start.
  const intro = (
    <AssistantMsg>
      <p class="chat-text">嗨！先選一張想美化的照片（一次一張）。</p>
    </AssistantMsg>
  );

  // 一鍵美化 queues the render before analysis finishes.
  const renderQueued = photo.status === "analyzing" && messages.some((m) => m.card?.type === "render_requested");

  // 處理中這一份文案兩邊分工：title 壓在左邊畫布的轉環底下講「在做什麼」，
  // text 進聊天那張卡講細節。算在同一個地方，兩邊才不會各講各的。
  const busyCopy =
    photo.status === "analyzing"
      ? {
          title: "正在分析照片",
          text: renderQueued
            ? waiting
              ? "分析完成後會自動開始美化，美化好就回答你的問題…"
              : "分析完成後會自動開始美化…"
            : waiting
              ? "正在辨識照片中的人物、光線與構圖，分析完就回答你的問題…"
              : "正在辨識照片中的人物、光線與構圖…",
        }
      : photo.status === "rendering"
        ? { title: "正在美化照片", text: renderSub }
        : null;

  let working = busyCopy ? (
    <AssistantMsg>
      <WorkingCard text={busyCopy.text} />
    </AssistantMsg>
  ) : null;

  // Quick replies for the current state; the render action is always last.
  const enterAdjust = () => props.onPost({ type: "adjust_opened", plan: snapshotOf(photo.plan) });
  let quick = null;
  // 下方那排按鈕上寫的字，問 AI 時一起送過去，它的回覆才指得出現在能按什麼。
  let bar: string[] = [];
  if (confirmRerun && photo.status === "ready" && adjusting) {
    working = (
      <AssistantMsg>
        <p class="chat-text">這一輪還沒改任何設定，美化會用和上一版相同的設定重新產生，結果可能差不多。要直接美化，還是繼續調整？</p>
      </AssistantMsg>
    );
    quick = (
      <QuickBar>
        <Chip label="繼續調整" icon="sliders" disabled={busy} onClick={() => setConfirmRerun(false)} />
        <Chip label="照原設定美化" icon="spark" kind="primary" disabled={busy} onClick={() => props.onRender("美化", "spark")} />
      </QuickBar>
    );
    bar = ["繼續調整", "照原設定美化"];
  } else if (confirmRerun && photo.status === "ready" && !adjusting) {
    working = (
      <AssistantMsg>
        <p class="chat-text">設定和上一版一樣，再美化一次會用相同的設定重新產生，結果可能和上一版差不多。要直接再美化，還是先調整設定？</p>
      </AssistantMsg>
    );
    quick = (
      <QuickBar>
        <Chip label="先調整" icon="sliders" disabled={busy} onClick={enterAdjust} />
        <Chip label="照原設定再美化" icon="redo" kind="primary" disabled={busy} onClick={() => props.onRender("再美化", "redo")} />
      </QuickBar>
    );
    bar = ["先調整", "照原設定再美化"];
  } else if (photo.status === "analyzing" && !renderQueued) {
    // 只有一顆還按不下去的「美化」，說的是分析完接下來做什麼。這裡曾經還有一顆
    // 「取消分析」，但後端沒有取消 job 的 API，它做的其實是清掉整個 session 重來 --
    // 一顆說自己會停下分析、實際上是丟掉這張照片的按鈕，不如不要。
    quick = (
      <QuickBar>
        <Chip label="美化" icon="spark" kind="primary" disabled />
      </QuickBar>
    );
  } else if (photo.status !== "ready") {
    quick = null;
  } else if (adjusting) {
    quick = (
      <QuickBar label={dirtyCats.length ? `改了${dirtyCats.map((c) => CATEGORIES[c].label).join("、")}，還沒美化：` : undefined}>
        <Chip
          label="美化"
          icon="spark"
          kind="primary"
          disabled={busy}
          onClick={() => (unchanged ? setConfirmRerun(true) : props.onRender("美化", "spark"))}
        />
      </QuickBar>
    );
    bar = ["美化"];
  } else if (fresh) {
    quick = (
      <QuickBar label={lastOpened ? undefined : "或直接選："}>
        {lastOpened ? (
          <Chip label="美化" icon="spark" kind="primary" disabled={busy} onClick={() => props.onRender("美化", "spark")} />
        ) : (
          <Chip label="照建議直接美化" icon="spark" kind="primary" disabled={busy} onClick={() => props.onRender("照建議直接美化", "spark")} />
        )}
      </QuickBar>
    );
    bar = [lastOpened ? "美化" : "照建議直接美化"];
  } else if (versions.length && current) {
    quick = (
      <QuickBar label="下一步：">
        <Chip label="調整" icon="sliders" disabled={busy || !ready} onClick={enterAdjust} />
        <Chip label="再美化" icon="redo" kind="primary" disabled={busy} onClick={() => setConfirmRerun(true)} />
      </QuickBar>
    );
    bar = ["調整", "再美化"];
  } else if (lastOutcome?.card?.type === "render_failed") {
    quick = (
      <QuickBar>
        <Chip label="調整" icon="sliders" disabled={busy || !ready} onClick={enterAdjust} />
        <Chip label="再試一次" icon="redo" kind="primary" disabled={busy} onClick={() => props.onRender("再試一次", "redo")} />
      </QuickBar>
    );
    bar = ["調整", "再試一次"];
  }
  const screen: Screen = {
    bar,
    topics: editing ? cats.map((c) => CATEGORIES[c].label) : [],
    ...(adjusting && dirtyCats.length ? { pending: dirtyCats.map((c) => CATEGORIES[c].label) } : {}),
  };

  // 照片一好（分析完；一鍵美化的話是美化完）就回答那句問題，一句只問一次；後端也只回答一次。
  useEffect(() => {
    if (!ready || !waiting || busy || props.pendingAsk || answered.current === waiting.id) return;
    answered.current = waiting.id;
    props.onAnswerWaiting(waiting.id, screen);
  });

  const sub = [timeLabel(session.created_at), `${session.photos.length} 張照片`, `${versions.length} 個版本`].join(" · ");

  return (
    <>
      <StepIndicator step={stepN} state={stepHow} />
      <section id="results-section" class="section visible">
        <div class="container results-body showing-analysis" id="results-body">
          <div class="results-container" id="results-detail">
            <div class="results-canvas-row showing-analysis" id="results-canvas-row">
              <ResultsCanvas
                caps={caps}
                photo={photo}
                versions={versions}
                current={current}
                pending={adjusting && !unchanged}
                busy={busyCopy}
                onSelect={props.onSelectVersion}
              />
              <div class="results-analysis-slot" id="results-analysis-slot">
                <ChatPanel
                  sub={sub}
                  toBottom={toBottom}
                  onDropFile={busy ? undefined : props.onFile}
                  footer={
                    <>
                      <FootBar
                        quick={quick}
                        faces={
                          pickable.length > 1 && (
                            <FacePicker people={pickable} pointed={pointed} onPoint={setPointed} />
                          )
                        }
                      />
                      <Composer
                        disabled={busy || photo.status === "failed"}
                        onSend={send}
                        people={pickable}
                        pointed={pointed}
                        onPoint={setPointed}
                        sendPicked={whoAsked}
                        focusKey={composerKey}
                      />
                    </>
                  }
                >
                  {intro}
                  {items}
                  {props.pendingAsk && !(holding(step(messages.length, -1)) && HOSTS.includes(messages[step(messages.length, -1)]?.card?.type || "")) && (
                    <>
                      {props.pendingAsk.text && (
                        <AskBubble
                          text={props.pendingAsk.text}
                          faces={props.pendingAsk.subjectIndices.flatMap((i) => faces[i] || [])}
                        />
                      )}
                      <AssistantMsg>{typing}</AssistantMsg>
                    </>
                  )}
                  {working}
                  {props.error && <ErrorMsg text={props.error} />}
                </ChatPanel>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}


// AI 幫照片想的文案：跟美化建議一樣一列一句，點了就是選這句；最後一列留給自己寫。
// 一句文案動輒十幾個字，擠成一排藥丸會斷得七零八落，一列一句才讀得完整。選過之
// 後定格成紀錄：選中的那列標起來，自己寫的就換成寫下的那句。
const LINE_MAX = 40;
function LinesCard(props: { lines: string[]; picked: string | null; open: boolean; busy: boolean; onPick: (text: string) => void }) {
  const [draft, setDraft] = useState("");
  const own = props.picked && !props.lines.includes(props.picked) ? props.picked : null;
  const send = () => {
    const t = draft.trim();
    if (t && !props.busy) props.onPick(t);
  };
  return (
    <div class="chat-card chat-options">
      <ul class="chat-opt-list">
        {props.lines.map((l) => (
          <li>
            <button
              type="button"
              class={`chat-opt-row chat-line-row${l === props.picked ? " is-on" : ""}`}
              disabled={!props.open || props.busy}
              onClick={() => props.onPick(l)}
            >
              <span class="chat-line-text">{l}</span>
              {l === props.picked && <Icon name="check" size={14} />}
            </button>
          </li>
        ))}
        {props.open ? (
          <li>
            <form
              class="chat-line-own"
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <input
                type="text"
                maxLength={LINE_MAX}
                value={draft}
                placeholder="自己寫一句"
                aria-label="自己寫一句文案"
                disabled={props.busy}
                onInput={(e) => setDraft((e.target as HTMLInputElement).value)}
              />
              <button type="submit" class="chat-line-send" disabled={props.busy || !draft.trim()} aria-label="用這句" title="用這句">
                <Icon name="up" size={14} />
              </button>
            </form>
          </li>
        ) : (
          own && (
            <li>
              <div class="chat-opt-row chat-line-row is-on">
                <span class="chat-line-text">{own}</span>
                <Icon name="check" size={14} />
              </div>
            </li>
          )
        )}
      </ul>
    </div>
  );
}

function AskBubble({ text, faces, meta }: { text: string; faces?: FaceRef[]; meta?: string }) {
  return (
    <UserMsg meta={meta}>
      <div class="chat-bubble chat-ask">
        {!!faces?.length && (
          <span class="chat-composer-who-faces">
            {faces.map((f) => (
              <FaceChip face={f} small />
            ))}
          </span>
        )}
        <span>{text}</span>
      </div>
    </UserMsg>
  );
}
