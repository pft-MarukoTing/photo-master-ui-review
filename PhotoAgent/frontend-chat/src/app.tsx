import { nextDisplayOrder } from "./display-order";
// The chat page: home (intro + gallery), the pre-session chat that picks a
// photo and settings, and a session (compare canvas + chat timeline built
// from the server's messages). Every action is one of the chat API calls;
// typed messages go to /ask, whose replies may carry a plan change to confirm.
import { useEffect, useRef, useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { api, ApiError, setUser, urls, type AskFace, type ReviewTurn, type Screen } from "./api";
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
  fillNoteBox,
  noteBoxKeys,
  readNoteBox,
  FacePicker,
  FootBar,
  PROPOSAL_CHOICES,
  ProposalCard,
  ModeSwitch,
  ResultCard,
  PhotoViewer,
  SamplePicker,
  UploadBubble,
  UserMsg,
  WorkingCard,
  useWorkLog,
  type Change,
  type FaceRef,
  type PanelMode,
  type Proposal,
  type ProposalChoice,
} from "./chat";
import { facePeople, seedDetectedFaces, useBoxThumbs, useDetectedFaces, useFaceThumbs, type Box } from "./face";
import { useSubjectPreview } from "./subject-preview";
import { usePreviewConversation } from "./preview-conversation";
import type { PreviewClause, PreviewReplyEvent, PreviewWish, PreviewConversationResult, TargetQuestionSnapshot } from "./preview-conversation";
import { Icon } from "./icons";
import { CASE_PREFIX, ConfirmModal, Gallery, Header, HistoryDrawer, HomeIntro, type GallerySelection, type GalleryTab } from "./home";
import { handoffPatch, takeHandoff, writeHandoff } from "./handoff";
import { resolveUser } from "./identity";
import { ResultsCanvas, SplitGrip, StepIndicator, VersionViewer, stepState, versionJob } from "./results";
import {
  categoriesFor,
  introFor,
  CATEGORIES,
  categoryOf,
  OptionsCard,
  PROMPT_MAX,
  termOf,
  topicLead,
  topicRecap,
  type Category,
  type Hot,
} from "./options";
import { defaultSettings, loadPrefs, PrefsModal, savePrefs, sameSettings, SettingsEditor, type Prefs, type Settings } from "./settings";
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
  aspectLabel,
  clockLabel,
  rememberRenderLabel,
  renderLabelFor,
  reframeBy,
  jobErrorText,
  renderMeta,
  reframeLoadingMessage,
  renderSubMessages,
  promptSubMessages,
  analyzeSubMessages,
  timeLabel,
  versionsFromMessages,
} from "./util";

type Phase = "home" | "chat" | "session";
type PreStep = "start" | "picked" | "settings";
type Source = { kind: "file"; file: File; url: string } | { kind: "demo"; name: string };
// A face picked on the upload page: no name yet, so where it stands and its box.
type PrePerson = FaceRef & { where: string; box: Box; thumbnail_box?: Box; preview_person_id?: string; detection_index?: number; preview_job_id?: string; source_sha256?: string };

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
  order?: number;
  replyOrder?: number;
  // Freeze presentation references independently of executable person bindings.
  replyPeople?: FaceRef[];
  resolvedWish?: string;
  // 沒點頭像的句子，preview 對完人之後，每一段動作各自落在哪幾位身上（沒有人
  // 的就是空的）。字是去掉「左邊的女孩」這種指稱的動作本身，誰是誰由頭像表示。
  placed?: { persons: PrePerson[]; text: string }[];
  actionPending?: boolean;
  withdrawalText?: string;
  text: string;
  persons: PrePerson[];
  selectionSource?: "user_selection" | "model_suggestion";
  reply?: string;
  kept?: boolean;
  dropped?: { after: number; batch: number; order?: number; text?: string };
  // The AI's reply was a question back instead of a decision; `options` are
  // the short answers it offered to tap.
  asked?: { options: string[] };
  askedResolved?: boolean;
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
  // 這句說了比例（「做成限動」），「設定」的長寬比就照它改了。`ratioBack`：這句
  // 後來不在了，長寬比也已經改回 AI 建議。
  ratio?: string;
  ratioBack?: boolean;
};

// 句子裡指人的那段拿掉，剩下要做的事：「把左側女孩的頭髮染成金髮」→「把頭髮染成金髮」。
function clauseAction(c: PreviewClause, persons: PrePerson[]): string {
  const text = c.display_text || c.text;
  const names = [persons.map(p => p.name || p.where).join("、"), c.referent].filter(Boolean);
  const referent = names.find(name => text.includes(name));
  const at = referent ? text.indexOf(referent) : -1;
  if (at < 0) return text;
  const before = text.slice(0, at), after = text.slice(at + referent!.length).replace(/^[：:]?的?/, "");
  // 指稱只抓到好幾位裡的第一位（後面還接著「、右邊那位」），切不乾淨就整句照原樣。
  if (!after.trim() || /^[、，,和跟與及]/.test(after)) return text;
  return /^(?:請|麻煩|幫我|請幫我)*把?$/.test(before) ? (before.endsWith("把") ? "把" : "") + after : before + after;
}

function resolvedNotes(notes: Note[], result: PreviewConversationResult, who: (ids: string[]) => PrePerson[] = () => []): Note[] {
  return notes.map((note, index) => {
    const clauses = result.clauses.filter(c => c.request_id === `note-${index}`);
    const semantic = clauses.length > 0 && clauses.every(c => c.semantic_required);
    const actionPending = clauses.some(c => !["cancelled", "superseded"].includes(c.status) &&
      (c.action_status === "incomplete" || c.action_status === "pending" || (semantic &&
        (c.edit_scope_pending || c.edit_description_pending || ["pending", "ambiguous", "unmatched"].includes(c.status)))));
    const effective = clauses.filter(c => ["matched", "not_person", "outside_confirmed"].includes(c.status) && c.scope !== "query" && !c.edit_scope_pending && !c.edit_description_pending && c.action_status !== "incomplete" && c.action_status !== "pending");
    if (actionPending && !effective.length)
      return note.actionPending && note.resolvedWish === "" ? note : { ...note, resolvedWish: "", actionPending: true };
    if (!clauses.length || (!semantic && clauses.some(c => c.edit_scope_pending || c.edit_description_pending || ["pending", "ambiguous", "unmatched"].includes(c.status)))) return note;
    const text = effective.map(c => c.display_text || c.text).join("；");
    const withdrawalText = text || Array.from(new Set(clauses.map(c => c.cancelled_from?.text || c.display_text).filter(Boolean))).join("；") || note.withdrawalText;
    const placed = effective.map(c => {
      const ids = c.display_target_ids?.length ? c.display_target_ids : c.target_ids.length ? c.target_ids : c.protected_ids;
      const persons = who(ids);
      return persons.length === ids.length && ids.length ? { persons, text: clauseAction(c, persons) } : { persons: [], text: c.display_text || c.text };
    });
    const samePlaced = JSON.stringify(note.placed?.map(x => [x.persons.map(faceRef), x.text])) === JSON.stringify(placed.map(x => [x.persons.map(faceRef), x.text]));
    if (note.resolvedWish === text && note.withdrawalText === withdrawalText && samePlaced && !!note.actionPending === actionPending) return note;
    return { ...note, ...(actionPending ? { actionPending: true } : note.actionPending ? { actionPending: false } : {}), resolvedWish: text, withdrawalText, placed };
  });
}

// 點頭像回答「哪一位」、直接送出時 AI 回的那句。沒問模型，所以話是這裡寫好的。
// The whole 最近照片 list: every live session plus every history case (the
// backend's LISTED_SESSIONS_MAX + HISTORY_MAX_LEN), since 面板模式's 最近照片
// reaches all of a user's cases too.
const LISTED_MAX = 300;

const WHO_ANSWERED_REPLY = "好，這個我記下來了。還有其他想改的地方嗎？沒有的話，可以從下面選「分析」或「一鍵美化」喔！";

// What a note asks for, as the list shows it and the prompt carries it.
const wishText = (n: Note) => n.resolvedWish ?? n.wish ?? n.text;
function previewWish(n: Note, index: number): PreviewWish {
  return { wish_id: `note-${index}`, text: n.wish ?? n.text, source_text: n.text,
    selection_source: n.selectionSource || "none",
    target_ids: n.selectionSource === "user_selection"
      ? n.persons.flatMap(p => p.preview_person_id ? [p.preview_person_id] : []) : [] };
}

// 一段直接指定給某幾位的需求交給 preview：手改或模型重寫過的那一段整段，或一句
// 指了好幾位、其中有人的段已經改過，剩下那幾位的部分。人照樣指到那幾位，改字也
// 不會把指定的人弄丟；沒有 preview 的人就把誰寫在字裡。
function directWish(id: string, persons: PrePerson[], text: string): PreviewWish {
  const ids = persons.flatMap(p => p.preview_person_id ? [p.preview_person_id] : []);
  const line = ids.length && ids.length === persons.length ? text : whoPrefix(persons) + text;
  return { wish_id: id, text: line, source_text: line,
    selection_source: ids.length ? "user_selection" : "none", target_ids: ids };
}

const keptNotes = (ns: Note[]) => ns.filter((n) => n.kept !== false && !n.dropped && n.resolvedWish !== "");
// Only a line that is actually in 個人化需求 can be taken out of it: 清除所有需求
// sweeps the card, and the card never held the lines the AI turned away.
const dropNotes = (ns: Note[], gone: (n: Note) => boolean) => {
  const batch = ns.reduce((m, n) => (n.dropped ? Math.max(m, n.dropped.batch + 1) : m), 0);
  const order = nextDisplayOrder();
  return ns.map((n) =>
    n.kept !== false && !n.dropped && n.resolvedWish !== "" && gone(n)
      ? { ...n, dropped: { after: ns.length, batch, order, text: n.withdrawalText || wishText(n) } } : n,
  );
};

// 對話是照發生的順序排的，而拿掉需求也是使用者說的一句話，所以它跟打字一樣
// 自成一輪，排在當下的位置——拿掉第一句之後又講了第三句的話，AI 的回答要在第
// 三句之前。`left` 是那一刻清單裡還剩幾句，回話才講得出「其他的照舊」。
type Turn = { ask: Note; part?: "user" | "reply" } | { gone: Note[]; left: number } | { previewReply: PreviewReplyEvent };

function conversation(ns: Note[], events: PreviewReplyEvent[] = []): Turn[] {
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
      .filter((n) => n.kept !== false && n.resolvedWish !== "" && (!n.dropped || n.dropped.batch > batch)).length;
  }
  const ordered = [...batches.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b);
  const out: Turn[] = [];
  ns.forEach((n, i) => {
    out.push({ ask: n });
    for (const b of ordered) if (b.after === i + 1) out.push({ gone: b.gone, left: b.left });
  });
  const merged: Turn[] = [];
  for (let i = 0; i <= out.length; i++) {
    for (const event of events) if (Math.min(event.after, out.length) === i) merged.push({ previewReply: event });
    if (i < out.length) merged.push(out[i]);
  }
  // Legacy unstamped records retain their original ordering. New records split
  // the user input from its eventual reply so a settings click cannot move.
  const chronological: { turn: Turn; order: number; index: number }[] = [];
  merged.forEach((turn, index) => {
    if ("ask" in turn && turn.ask.order != null) {
      chronological.push({ turn: { ask: turn.ask, part: "user" }, order: turn.ask.order, index });
      if (turn.ask.reply || turn.ask.replyOrder != null) chronological.push({ turn: { ask: turn.ask, part: "reply" }, order: turn.ask.replyOrder ?? turn.ask.order, index });
    } else {
      const order = "previewReply" in turn ? turn.previewReply.order
        : "gone" in turn ? turn.gone[0].dropped?.order : undefined;
      chronological.push({ turn, order: order ?? index, index });
    }
  });
  return chronological.sort((a, b) => a.order - b.order || a.index - b.index).map(x => x.turn);
}

// 使用者從清單裡拿掉需求，AI 就回一句。把使用者打的字劃掉是在改畫面，回話才
// 是在對話——上面那句「我記下來了」還在原地，是真的講過的；底下這句接著說現在
// 不算數了，兩句合起來才是完整的經過。
function goneReply(t: { gone: Note[]; left: number }) {
  const what = `「${t.gone[0].dropped?.text || t.gone[0].withdrawalText || wishText(t.gone[0])}」`;
  const back = t.gone.some((n) => n.ratioBack) ? "長寬比也改回「AI 建議」了。" : "";
  if (t.gone.length > 1) return `好，個人化需求都清掉了，美化時就不另外加了。${back}`;
  return t.left ? `好，${what}就不做了，其他的照舊。${back}` : `好，${what}就不做了，目前沒有其他個人化需求。${back}`;
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

// 個人化需求分段：一個人一段，沒指定對象的全部一段，照第一次提到的順序排。一句
// 指了好幾位（點了好幾個頭像，或「大家」被 preview 對到每一位），就拆進每個人
// 自己那段，之後再針對其中一位改，才比得出衝突。段裡只寫動作，是誰由段首的頭像
// 表示，不會「名字＋內容」一句一句疊上去。後端的 BUILD user_prompt FROM SCRATCH 要
// 的就是這個形狀（一段裡面用「、」接，段跟段之間用「；」），上傳頁面自己先併好，
// 模型、畫面、送出去的 user_prompt 看到的才是同一份。
//
// 每一段都可以整段改（使用者手改，或新的一句跟它衝突時模型重寫）。改過之後那段
// 字就是準：`upTo` 之前屬於這段的句子都已經寫在 `text` 裡（或被刪掉了），之後才
// 收下的句子接在後面。各段各改各的，指定的人跟著段走，不會因為改字就掉了。
type PromptEdit = { text: string; upTo: number };
type PromptEdits = Record<string, PromptEdit>;
type Section = { key: string; persons: PrePerson[]; text: string; first: number };
type NotePart = { key: string; persons: PrePerson[]; text: string };

// 一句需求落在哪幾段、各段寫什麼。
function noteParts(n: Note): NotePart[] {
  const own = n.wish ?? n.text;
  if (n.placed?.length) return n.placed.flatMap(x => x.persons.length
    ? x.persons.map(p => ({ key: faceRef(p), persons: [p], text: x.text })) : [{ key: "", persons: [], text: x.text }]);
  if (n.persons.length) return n.persons.map(p => ({ key: faceRef(p), persons: [p], text: own }));
  return [{ key: "", persons: [], text: own }];
}
const liveNote = (n: Note) => n.kept !== false && !n.dropped && n.resolvedWish !== "";
const coveredBy = (edits: PromptEdits, key: string, i: number) => !!edits[key] && i < edits[key].upTo;

function promptSections(ns: Note[], edits: PromptEdits = {}): Section[] {
  const out = new Map<string, { persons: PrePerson[]; first: number; parts: string[] }>();
  ns.forEach((n, i) => {
    const live = liveNote(n);
    for (const part of noteParts(n)) {
      const covered = coveredBy(edits, part.key, i);
      if (!live && !covered) continue;
      let sec = out.get(part.key);
      if (!sec) out.set(part.key, sec = { persons: part.persons, first: i, parts: [] });
      if (!covered && !sec.parts.includes(part.text)) sec.parts.push(part.text);
    }
  });
  return [...out.entries()].flatMap(([key, sec]) => {
    const parts = [edits[key]?.text.trim() || "", ...sec.parts].filter(Boolean);
    const text = parts.join(key ? "、" : "；");
    return text ? [{ key, persons: sec.persons, text, first: sec.first }] : [];
  });
}

// 一段在 user_prompt 裡的樣子。指定了人的那段前面寫上是誰，段裡不能有「；」，
// 不然後面那半句就不知道是在講誰。
const sectionLine = (sec: Section) => sec.key
  ? whoPrefix(sec.persons) + sec.text.replace(/；/g, "、") : sec.text;

const notesPrompt = (ns: Note[], edits: PromptEdits = {}) =>
  promptSections(ns, edits).map(sectionLine).join("；");

// 上傳頁面的整段對話，給模型看的版本：它才分得出這句是在回答它剛問的，還是另一
// 個新需求。使用者拿掉需求那一輪，模型看到的是 AI 回的那句。
function reviewHistory(ns: Note[], events: PreviewReplyEvent[] = []): ReviewTurn[] {
  const groups: ReviewTurn[][] = [];
  const noteFor = new Map<ReviewTurn, Note>();
  for (const t of conversation(ns, events)) {
    if ("previewReply" in t) {
      groups.push(t.previewReply.kind === "settings"
        ? [{ role: "user", text: "設定" }, { role: "assistant", text: t.previewReply.text }]
        : [{ role: t.previewReply.role || "assistant", text: t.previewReply.text }]);
    } else if ("gone" in t) {
      groups.push([{ role: "assistant", text: goneReply(t) }]);
    } else {
      const group: ReviewTurn[] = t.part === "reply" ? [] : [{ role: "user", text: noteLine(t.ask) }];
      if (t.part !== "user" && t.ask.reply) group.push({ role: "assistant", text: t.ask.reply,
        ...(t.ask.asked ? { question: true, options: t.ask.asked.options } : {}),
        ...(t.ask.offered ? { question: true, offer: true, options: t.ask.offered.options } : {}),
      });
      for (const message of group) noteFor.set(message, t.ask);
      const previous = groups[groups.length - 1];
      if (t.part === "reply" && previous?.length && noteFor.get(previous[0]) === t.ask) previous.push(...group);
      else groups.push(group);
    }
  }
  const recent: ReviewTurn[][] = [];
  let messages = 0, characters = 0;
  for (const group of [...groups].reverse()) {
    const size = group.reduce((n, turn) => n + turn.text.length, 0);
    if (messages + group.length > 20 || characters + size > 12000) break;
    recent.unshift(group);
    messages += group.length;
    characters += size;
  }
  const messagesKept = recent.flat();
  const userNotes = new Set(messagesKept.filter(message => message.role === "user").map(message => noteFor.get(message)));
  // A history window may cut between an input and its interleaved completion.
  // Never hand the model an orphaned reply from that round.
  return messagesKept.filter(message => message.role !== "assistant" || !noteFor.has(message) || userNotes.has(noteFor.get(message)));
}

// 上傳頁面的整段對話，搬進 session 當開頭：使用者講過的話、AI 回的話都照原樣
// 留著，session 的 AI 接著聊時也看得到前面講到哪。選項跟動作鍵不跟過去——那是
// 上傳頁面的按鍵，到了 session 按不到。指過的人還沒有名字，帶著框跟位置過去，
// 泡泡上的頭像從框裁，AI 讀的是位置。`later` 那句就是等著回答的問題本身，標成
// waiting 放在它原本的位置，分析好了再回答。
function carriedLog(ns: Note[], photoId: string, later: Note | null, events: PreviewReplyEvent[] = []) {
  const out: { role: "user" | "assistant"; text?: string; card: Dict }[] = [];
  const reply = (text: string) => out.push({ role: "assistant", card: { type: "llm_reply", photo_id: photoId, pre: true, text } });
  for (const t of conversation(ns, events)) {
    if ("previewReply" in t) {
      if (t.previewReply.kind === "settings") out.push({ role: "user", text: "設定", card: { type: "ask", pre: true, photo_id: photoId } });
      if (t.previewReply.role === "user") out.push({ role: "user", text: t.previewReply.text, card: { type: "ask", pre: true, photo_id: photoId } });
      else reply(t.previewReply.text);
      continue;
    }
    if ("gone" in t) {
      reply(goneReply(t));
      continue;
    }
    const n = t.ask;
    const card: Dict = { type: "ask", photo_id: photoId, pre: true };
    if (n.persons.length) card.faces = n.persons.map((p) => ({ box: p.box, thumbnail_box: p.thumbnail_box, preview_person_id: p.preview_person_id, where: p.where }));
    if (n === later) card.waiting = true;
    if (t.part !== "reply") out.push({ role: "user", text: n.text, card });
    if (t.part !== "user" && n.reply) reply(n.reply);
  }
  return out.slice(-100);
}

// AI 問了還沒人回的那一句：只有對話的最後一輪是它的提問才算——中間拿掉過需求
// 或又講了別的，那個問題就過去了。
function openAsk(ns: Note[]): Note | null {
  const log = conversation(ns);
  const last = log[log.length - 1];
  return last && "ask" in last && last.ask.asked && !last.ask.askedResolved ? last.ask : null;
}

// The same words, for the server to find and replace. A position is not a name
// and the analysis will have one; the box is what survives the handover, so the
// pick travels alongside the prompt as plan.prompt_faces and the server settles
// the name once it has an analysis.
const groupFaces = (g: { persons: PrePerson[] }): PromptFace[] =>
  g.persons.map((p) => ({ box: p.box, ref: faceRef(p), where: p.where,
    ...(p.preview_person_id ? { preview_person_id: p.preview_person_id,
      preview_job_id: p.preview_job_id, source_sha256: p.source_sha256 } :
      p.detection_index ? { detection_index: p.detection_index,
      preview_job_id: p.preview_job_id, source_sha256: p.source_sha256 } : {}) }));

function withoutPreviewIdentity({ preview_person_id, detection_index, preview_job_id,
  source_sha256, ...geometry }: PrePerson): PrePerson {
  return geometry;
}

const GREETING = "嗨！先選一張想美化的照片（一次一張）。要自己上傳，還是從範例照片挑？";

// 帶著個人化需求按一鍵美化時問的兩條路。僅依需求修改 = overwrite_prompt：修圖
// 只拿個人化需求當指令；整體美化 = 照分析修完，需求一起加上去（原本的做法）。
// 僅依需求修改那條也是 session 裡那張美化卡的標題。按鈕是使用者的話、說明是
// AI 的話，兩邊都不用「我」，主詞才不會打架。說明的順序跟按鈕一樣，整體美化在前。
const ONLY_PROMPT_LABEL = "僅依需求修改";
const WITH_ANALYSIS_LABEL = "整體美化";

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.body?.error === "photos_not_ready") return "照片還在處理中，請稍候再試。";
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

// #session=<id>, or #case=<case_id> from 面板模式 (a history case, which
// openSession resolves to its session) -- in openSession's row-id form.
function sessionHash(): string | null {
  const m = location.hash.match(/session=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  const c = location.hash.match(/case=([^&]+)/);
  return c ? `${CASE_PREFIX}${decodeURIComponent(c[1])}` : null;
}

function setSessionHash(id: string | null) {
  const url = `${location.pathname}${location.search}${id ? `#session=${encodeURIComponent(id)}` : ""}`;
  history.replaceState(null, "", url);
}

function sourceSrc(s: Source) {
  return s.kind === "file" ? s.url : urls.demoThumb(s.name);
}

function fullSrc(s: Source) {
  return s.kind === "file" ? s.url : urls.demoImage(s.name);
}

function activePhoto(s: Session | null): Photo | null {
  if (!s || !s.photos.length) return null;
  return s.photos.find((p) => p.photo_id === s.active_photo_id) || s.photos[0];
}

const isBusy = (p: Photo) => p.status === "analyzing" || p.status === "reframing" || p.status === "rendering";
// 分析放在哪支 job：分析那支，或後來改比例時重新構圖的那支。
const analysisJob = (p: Photo) => p.analysis_job_id || p.analyze_job_id;

export function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  // 開機時打開的 #case= 連結也要用到 caps，那時 state 還沒更新到 closure 裡。
  const capsRef = useRef<Capabilities | null>(null);
  const [debugUi, setDebugUi] = useState(false);
  const [bootError, setBootError] = useState("");
  const [demos, setDemos] = useState<string[] | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);

  // #new: 面板模式的新對話，直接落在新對話的選照片畫面（開機時清掉這個 hash）。
  const [phase, setPhase] = useState<Phase>(() => (location.hash === "#new" ? "chat" : "home"));
  // Landing on a #session / #case link (the other mode's 對話模式 button):
  // the home page stays undrawn until that session is open, so the switch
  // goes straight from one mode's photo to the other's.
  const [opening, setOpening] = useState(() => !!sessionHash());
  const [tab, setTab] = useState<GalleryTab>("recent");
  const [selection, setSelection] = useState<GallerySelection>(null);
  const [drawer, setDrawer] = useState(false);
  const [leaveAsk, setLeaveAsk] = useState<{
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    answer: (ok: boolean) => void;
  } | null>(null);
  // 刪除前的那一問（notice：刪不掉時只有「確定」的那句）。
  const [deleteAsk, setDeleteAsk] = useState<{ message: string; notice?: boolean; done: (ok: boolean) => void } | null>(null);

  // Pre-session chat
  const [step, setStep] = useState<PreStep>("start");
  const [settingsEvents, setSettingsEvents] = useState<PreviewReplyEvent[]>([]);
  const lastSettingsClick = useRef(-1);
  const [via, setVia] = useState<"upload" | "sample" | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [sourceGeneration, setSourceGeneration] = useState(0);
  const [settings, setSettings] = useState<Settings | null>(null);
  // header 設定裡的對話風格；settings 每次重來都從這裡起跳。
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [prefsOpen, setPrefsOpen] = useState(false);
  // What the user typed in the composer before the session starts, one entry
  // per message. Together they become the new photo's plan.user_prompt, so the
  // 個人化需求 card in the session opens with them.
  const [notes, setNotes] = useState<Note[]>([]);
  // The user's (or the model's) edit of each 個人化需求 section, by its key (faceRef, or "" for 未指定).
  const [segEdits, setSegEdits] = useState<PromptEdits>({});
  const [samplesOpen, setSamplesOpen] = useState(false);
  // 看大圖：上傳頁對話裡那張照片，或手機上頂端的照片列。
  const [viewing, setViewing] = useState(false);
  const closeViewer = () => setViewing(false);
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
  // 輸入框裡打了還沒送出的字：換照片前要知道有沒有東西會被清掉。
  const preDraft = useRef("");
  // 帶著個人化需求按一鍵美化：先問要只改使用者說的，還是整體美化再加上需求。
  // 留著按下去那一刻的 later / log，選好之後照原樣開始；`overwrite` 是選了哪
  // 一個，上傳中那排選項定格在它身上，失敗了還能重選。
  const [renderAsk, setRenderAsk] = useState<{ later: Note | null; log: Note[]; overwrite?: boolean } | null>(null);
  // 按了分析／一鍵美化到進 session 之間：建 session、上傳、送出，
  // 前後可以好幾秒。做到哪一步就多講一句，按下去當下畫面就有回應。
  const [starting, setStarting] = useState<StartProgress | null>(null);

  // Session
  const [session, setSession] = useState<Session | null>(null);
  // 照片的分析，連同它是哪支 job 的：重新構圖後換一支，就再抓一次；抓到之前
  // 先沿用舊的，面板才不會收起來又打開。
  const [analyses, setAnalyses] = useState<Record<string, { job: string; analysis: Analysis }>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [pendingAsk, setPendingAsk] = useState<{ text: string; subjectIndices: number[] } | null>(null);

  const [submitting, setSubmitting] = useState<{ source: Source; mode: "analyze" | "render"; since: number } | null>(null);
  const submissionLock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // ── The upload page's own face picker ─────────────────────────────────────
  // A separate worker preview starts on selection; feature-off retains the
  // original local-detector path. Neither creates a formal Chat session yet.
  const preFull = !source ? "" : source.kind === "file" ? source.url : urls.demoImage(source.name);
  const previewEnabled = !!caps?.features?.subject_preview;
  const loadSource = async () => {
    if (!source) throw new Error("no source");
    if (source.kind === "file") return { blob: source.file, name: source.file.name };
    const res = await fetch(urls.demoImage(source.name));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { blob: await res.blob(), name: source.name };
  };
  const preview = useSubjectPreview(source && phase === "chat" && previewEnabled && !submitting ? preFull : null,
    settings?.analysis_model || caps?.render.defaults.analysis_model || "", loadSource);
  const previewReviewIdentity = useRef(preview.identity);
  previewReviewIdentity.current = preview.identity;
  const selectionIdentity = `${preFull}#${settings?.analysis_model || caps?.render.defaults.analysis_model || ""}`;
  const sourceIdentity = useRef(selectionIdentity);
  sourceIdentity.current = selectionIdentity;
  const reviewedSource = useRef(preFull);
  // 長寬比是跟著說了比例的那句需求改的，那句不在個人化需求裡了（拿掉、清掉，或
  // 編輯時刪掉），長寬比就回到 AI 建議。之後使用者在「設定」自己改過的不動。
  useEffect(() => {
    if (!caps) return;
    const prompt = notesPrompt(notes, segEdits);
    const live = (n: Note) => liveNote(n) && noteParts(n).some((x) => !!x.text && prompt.includes(x.text));
    const gone = notes.filter((n) => n.ratio && !n.ratioBack && !live(n));
    if (!gone.length) return;
    const current = settings?.aspect_ratio;
    const back = gone.some((n) => n.ratio === current) && !notes.some((n) => n.ratio === current && live(n));
    setNotes((ns) => ns.map((n) => (!gone.includes(n) ? n : back && n.ratio === current ? { ...n, ratioBack: true } : { ...n, ratio: undefined })));
    if (back) setSettings((s) => s && { ...s, aspect_ratio: caps.render.defaults.aspect_ratio });
  }, [notes, segEdits]);
  useEffect(() => {
    const samePhoto = reviewedSource.current === preFull;
    reviewedSource.current = preFull;
    setPrePointed([]);
    setPreAsk(null);
    // A model change keeps same-photo instructions as ordinary geometry;
    // leaving preview temporarily for submission must not clear any notes.
    setNotes((n) => n.some((x) => x.persons.length || x.placed) ? n.map((x) => ({
      ...x, persons: samePhoto ? x.persons.map(withoutPreviewIdentity) : [],
      placed: samePhoto ? x.placed?.map((y) => ({ ...y, persons: y.persons.map(withoutPreviewIdentity) })) : undefined,
    })) : n);
    if (!samePhoto) setSegEdits({});
  }, [selectionIdentity]);
  // Feature-off alone uses Flask detection. The successful preview path must
  // never issue a second /detect-faces request while creating the worker job.
  const preScan = useDetectedFaces(source && caps && !previewEnabled ? preFull : null, loadSource);
  const preFaces: { subjectIndex: number; name: string; box: Box; role?: string;
    thumbnail_box?: Box; preview_person_id?: string; detection_index?: number }[] = previewEnabled
    ? (preview.result?.format_version === 2 || preview.result?.format_version === 3)
      ? (preview.result.people || []).map((p, index) => ({
          subjectIndex: index, name: p.label, box: p.box, role: p.role, preview_person_id: p.preview_person_id, thumbnail_box: p.thumbnail_box,
        }))
      : (preview.result?.faces || []).filter((f) => f.role !== "not_real_face").map((f) => ({
          subjectIndex: f.detection_index - 1, name: f.label, box: f.box, role: f.role,
          detection_index: f.detection_index,
        })) : preScan.people;
  const preThumbs = useBoxThumbs(preFaces.map((p) => p.thumbnail_box || p.box),
    previewEnabled && preview.result ? urls.previewImage(preview.result.preview_job_id) : preFull);
  // The detector hands faces back largest first, an order nobody looking at the
  // photo can see. Counting from the left is the one order the photo itself
  // shows, so that is how a face gets put into words here.
  const preOrder = [...preFaces].sort((a, b) => a.box.x + a.box.w / 2 - (b.box.x + b.box.w / 2));
  const preWhere = (subjectIndex: number) =>
    `照片中從左邊數來第 ${preOrder.findIndex((q) => q.subjectIndex === subjectIndex) + 1} 位`;
  // Picker indices are local display positions. References serialize opaque
  // server person IDs, independent of formal subject order.
  const prePickable = preFaces.map((p) => ({
    previewRole: "role" in p ? String(p.role) : "",
    name: p.name,
    where: preWhere(p.subjectIndex),
    thumb: preThumbs[preFaces.indexOf(p)] || "",
    subjectIndex: p.subjectIndex,
    preview_person_id: "preview_person_id" in p ? p.preview_person_id : undefined,
  }));
  // Preview loading is informational, not a lock on ordinary chat. Legacy
  // mode keeps its existing detector/thumbnail readiness gate unchanged.
  const preScanning = !!source && (previewEnabled ? !preview.ready : (!preScan.ready || preThumbs.length !== preFaces.length));
  const preHeld = previewEnabled ? false : preScanning;
  useEffect(() => {
    if (source && !preScanning) setPickHint((n) => n + 1);
  }, [source, preScanning]);
  const preRefs = (subjectIndices: number[]): PrePerson[] =>
    subjectIndices.flatMap((i) => {
      const p = preFaces.find((q) => q.subjectIndex === i);
      return p ? [{ name: p.name, thumb: preThumbs[preFaces.indexOf(p)] || "", where: preWhere(p.subjectIndex), box: p.box, thumbnail_box: p.thumbnail_box,
        ...(previewEnabled && preview.result ? { ...(p.preview_person_id
          ? { preview_person_id: p.preview_person_id } : { detection_index: p.detection_index }),
          preview_job_id: preview.result.preview_job_id, source_sha256: preview.result.source_sha256 } : {}) }] : [];
    });

  const preSections = promptSections(notes, segEdits);
  // 改過的段整段送一次（在那段第一句的位置）；一句有一部分落在改過的段，就只送
  // 其他人的那部分，不然改掉的內容又會從原句跑回來。
  const editedSent = new Set<string>();
  const previewWishes: PreviewWish[] = notes.flatMap((n, i) => {
    const parts = noteParts(n);
    const out: PreviewWish[] = [];
    for (const part of parts) {
      const edit = segEdits[part.key];
      if (!coveredBy(segEdits, part.key, i) || editedSent.has(part.key)) continue;
      editedSent.add(part.key);
      const sec = preSections.findIndex(x => x.key === part.key);
      if (sec >= 0 && edit.text.trim()) out.push(part.key
        ? directWish(`edited-${i}-${sec}`, part.persons, edit.text.trim())
        : { wish_id: `edited-${i}-${sec}`, text: edit.text.trim(), source_text: edit.text.trim(), selection_source: "none", target_ids: [] });
    }
    const wanted = (n.kept === true || n === openAsk(notes) && n.asked && !n.asked.options.length) && !n.dropped && (n.resolvedWish !== "" || n.actionPending);
    if (!wanted) return out;
    const rest = parts.filter(x => !coveredBy(segEdits, x.key, i));
    if (rest.length === parts.length) return [...out, previewWish(n, i)];
    return [...out, ...rest.map((x, k) => x.key ? directWish(`note-${i}-part-${k}`, x.persons, x.text)
      : { wish_id: `note-${i}-part-${k}`, text: x.text, source_text: x.text, selection_source: "none" as const, target_ids: [] })];
  });
  const bridge = usePreviewConversation({ identity: preview.identity ? `${preview.identity}#${sourceGeneration}` : null, preview: preview.result,
    photoKey: `${selectionIdentity}#${sourceGeneration}`, people: prePickable,
    model: settings?.analysis_model || "", history: reviewHistory(notes), historyWithEvents: events => reviewHistory(notes, [...events, ...settingsEvents]), wishes: previewWishes,
    prompt: notesPrompt(notes, segEdits), turns: conversation(notes).length,
    busy: !!preAsk || busy, blocked: !!openAsk(notes)?.asked?.options.length || !!renderAsk,
    active: phase === "chat" && !submitting && previewEnabled });
  // A completed presentation opens the picker only for its exact preview generation.
  // Formal submission never waits for this presentation gate.
  const previewPresentationKey = preview.identity ? `${preview.identity}#${sourceGeneration}` : null;
  const currentPresentation = useRef(previewPresentationKey);
  currentPresentation.current = previewPresentationKey;
  const [presentedGeneration, setPresentedGeneration] = useState<string | null>(null);
  const prePeopleReady = bridge.peoplePublished && !!previewPresentationKey && presentedGeneration === previewPresentationKey;
  const preDisplayPeople = !previewEnabled || prePeopleReady ? prePickable : [];
  useEffect(() => {
    const mapped = bridge.result;
    if (!mapped) return;
    setNotes(current => {
      let changed = false;
      const projected = resolvedNotes(current, mapped, ids => preRefs(ids.flatMap(id => {
        const p = prePickable.find(q => q.preview_person_id === id);
        return p ? [p.subjectIndex] : [];
      })));
      if (projected.some((n,i) => n !== current[i])) changed = true;
      const updated = projected.map((n, i) => {
        const clauses = mapped.clauses.filter(c => c.request_id === `note-${i}`);
        if (n.asked && !n.asked.options.length && !n.askedResolved && clauses.length &&
            clauses.every(c => (c.status === "matched" || c.status === "not_person") && c.action_status !== "incomplete" && c.action_status !== "pending")) {
          changed = true;
          return { ...n, kept: true, askedResolved: true };
        }
        return n;
      });
      return changed ? updated : current;
    });
  }, [bridge.result?.token]);
  const bridgeQuestion = bridge.result?.question;
  const [confirmationPointed, setConfirmationPointed] = useState<number[]>([]);
  useEffect(() => setConfirmationPointed([]), [bridgeQuestion?.question_id, bridge.result?.token]);

  // ── Boot ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const user = await resolveUser();
        setUser(user);
        const [c, cfg] = await Promise.all([api.capabilities(), api.config()]);
        setCaps(c);
        capsRef.current = c;
        const p = loadPrefs();
        setPrefs(p);
        setSettings(defaultSettings(c, p));
        setDebugUi(!!cfg.debugUi);
        api
          .demoImages()
          .then((r) => setDemos(r.images || []))
          .catch(() => setDemos([]));
        const list = await refreshSessions();
        if (!list.length) setTab("sample");
        if (location.hash === "#new") setSessionHash(null);
        const id = sessionHash();
        // Staying undrawn when it hands the case to 面板模式 instead.
        if (id && (await openSession(id)) === "left") return;
      } catch (e) {
        setBootError(errorText(e));
      }
      setOpening(false);
    })();
  }, []);

  async function refreshSessions(): Promise<SessionSummary[]> {
    try {
      const r = await api.listSessions(LISTED_MAX);
      const list = (r.sessions || []).filter((s) => s.photo_count > 0);
      setSessions(list);
      return list;
    } catch {
      setSessions([]);
      return [];
    }
  }

  // ── Session loading and polling ───────────────────────────────────────────
  // `id` is a session, or `case:<case_id>` for a history case: that case's
  // own session, or one rebuilt from history. A case the chat page cannot
  // open (no analysis left, or the AIPoC path) opens in 面板模式 instead.
  async function openSession(id: string): Promise<"left" | void> {
    setDrawer(false);
    setError("");
    try {
      const caseId = id.startsWith(CASE_PREFIX) ? id.slice(CASE_PREFIX.length) : null;
      if (caseId) {
        try {
          const r = await api.fromCase(caseId);
          id = r.session_id;
          if (r.created) refreshSessions();
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) {
            location.href = urls.oldUi(caseId);
            return "left";
          }
          throw e;
        }
      }
      let s = await api.getSession(id);
      if (!s.photos.length) throw new Error("這個對話沒有照片");
      if (caseId) s = await applyHandoff(s, caseId);
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

  // 從面板模式切過來時，把那邊還沒美化的調整寫進這張照片的 plan（handoff.ts）。
  // 帶不過去就算了，照片照樣打開，用的是這邊原本的 plan。
  async function applyHandoff(s: Session, caseId: string): Promise<Session> {
    const h = takeHandoff(caseId);
    const c = capsRef.current;
    const p = h && c && s.photos.find((x) => x.case_id === caseId);
    if (!h || !c || !p || isBusy(p)) return s;
    try {
      const r = await api.analysis(s.session_id, p.photo_id).catch(() => null);
      const analysis = r?.status === "success" ? (r.result?.analysis as Analysis) : null;
      const patch = handoffPatch(h, p.plan, c, analysis);
      // 引擎在一般帳號可能不給改，被擋就不帶這項，其他照樣帶。
      await api.patchPhotoPlan(s.session_id, p.photo_id, patch).catch(() => {
        const { render_option, ...rest } = patch;
        return api.patchPhotoPlan(s.session_id, p.photo_id, rest);
      });
      return await api.getSession(s.session_id);
    } catch {
      return s;
    }
  }

  // 上傳頁挑好的照片、打的字、選的設定都只在這一頁，離開就沒了；開始對話後
  // 每個調整都存在後端，不用問。關分頁、重新整理用瀏覽器自己的離開提示（文字
  // 改不了），頁面裡換照片、回首頁用 confirmLeave。
  const unsentHome = phase !== "session" && (!!source || notes.length > 0);
  function confirmLeave(where: string): Promise<boolean> {
    if (!unsentHome) return Promise.resolve(true);
    const what = notes.length ? "剛剛選的照片和打的需求" : "剛剛選的照片";
    return new Promise((resolve) =>
      setLeaveAsk({
        message: `${what}還沒開始美化，${where}就會清掉。確定嗎？`,
        answer: (ok) => {
          setLeaveAsk(null);
          resolve(ok);
        },
      }),
    );
  }
  // 對話都存在後端，離開不會掉東西；問一下只是避免誤按。
  function askNewChat() {
    setLeaveAsk({
      message: "要開新對話處理另一張照片嗎？目前這段對話可以從「最近照片」再打開。",
      confirmLabel: "開新對話",
      answer: (ok) => {
        setLeaveAsk(null);
        if (ok) newChat();
      },
    });
  }
  async function openOtherSession(id: string) {
    if (await confirmLeave("打開另一張")) openSession(id);
  }
  useEffect(() => {
    if (!unsentHome) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsentHome]);

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
  // has one even if that message is no longer in the log. 「幫我分析」補跑時
  // 不算：略過分析那時就有一張 analysis_ready 了，分析中先抓會拿不到，等它
  // 回到 ready 才會再抓一次，不然一直停在略過時的那份空分析。
  const analysisReady =
    !!photo &&
    photo.status !== "analyzing" &&
    (photo.status === "ready" ||
      photo.status === "reframing" ||
      photo.status === "rendering" ||
      messages.some((m) => m.card?.type === "analysis_ready" && m.card.photo_id === photo.photo_id));
  useEffect(() => {
    if (!session || !photo || !analysisReady || analyses[photo.photo_id]?.job === analysisJob(photo)) return;
    const pid = photo.photo_id;
    const job = analysisJob(photo);
    api
      .analysis(session.session_id, pid)
      .then((r) => {
        if (r.status === "success" && r.result?.analysis)
          setAnalyses((a) => ({ ...a, [pid]: { job, analysis: r.result.analysis } }));
      })
      .catch(() => {});
  }, [session?.session_id, photo?.photo_id, analysisReady, photo && analysisJob(photo)]);

  // ── Navigation ────────────────────────────────────────────────────────────
  function resetPre() {
    setStep("start");
    setSettingsEvents([]);
    lastSettingsClick.current = -1;
    setVia(null);
    setSource(null);
    setNotes([]);
    preDraft.current = "";
    setSegEdits({});
    setPrePointed([]);
    setPreAsk(null);
    setRenderAsk(null);
    setSamplesOpen(false);
    if (caps) setSettings(defaultSettings(caps, prefs || undefined));
  }

  function ask(message: string, notice = false): Promise<boolean> {
    return new Promise((done) =>
      setDeleteAsk({
        message,
        notice,
        done: (ok) => {
          setDeleteAsk(null);
          done(ok);
        },
      }),
    );
  }

  // 刪最近照片裡的格子，跟面板模式一樣：先問一聲；刪的是紀錄（對話，連同它照片在
  // 面板模式的紀錄），已經產生的圖不動。刪到正開著的這段對話、或刪到一格不剩，就
  // 收起抽屜回首頁。回傳真的刪了沒。
  async function removeRows(ids: string[] | "all"): Promise<boolean> {
    const message =
      ids === "all"
        ? "確定要刪除所有處理紀錄嗎？此操作無法復原。"
        : ids.length > 1
          ? `確定要刪除這 ${ids.length} 筆處理紀錄嗎？`
          : "確定要刪除這筆處理紀錄嗎？";
    if (!(await ask(message))) return false;
    try {
      await api.deleteRows(ids);
    } catch {
      await ask(ids === "all" ? "刪除所有紀錄失敗，請稍後再試。" : "刪除紀錄失敗，請稍後再試。", true);
      return false;
    }
    const gone = (id: string) => ids === "all" || ids.includes(id);
    const openGone =
      !!session && (gone(session.session_id) || session.photos.some((p) => !!p.case_id && gone(`${CASE_PREFIX}${p.case_id}`)));
    if (selection?.kind === "session" && gone(selection.id)) setSelection(null);
    const list = await refreshSessions();
    if (openGone || !list.length) {
      setDrawer(false);
      if (phase === "session") goHome();
    }
    return true;
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
    setSettingsEvents([]);
    lastSettingsClick.current = -1;
    setSourceGeneration(n => n + 1);
    ++lastPick.current; // a demo pick also supersedes an in-flight HEIC decode
    setSamplesOpen(false);
    setSource(s);
    // A new photo has its own people. (Swapping one on the upload page already
    // cleared what was typed in swapSource; this keeps the pointing clean.)
    setPrePointed([]);
    setRenderAsk(null);
    setNotes((n) => (n.some((x) => x.persons.length) ? n.map((x) => ({ ...x, persons: [] })) : n));
    setVia(s.kind === "file" ? "upload" : "sample");
    setStep("picked");
    setError("");
  }

  // 上傳頁已經有照片時再挑一張：換了就是重新開始，這張打的需求、指定的人、選的
  // 設定都跟著清掉，不帶到新照片上。有東西會被清掉（打過字、改過設定）才先問。
  async function swapSource(s: Source) {
    if (phase !== "chat" || !source) return choose(s);
    if (s.kind === "demo" && source.kind === "demo" && s.name === source.name) {
      setSamplesOpen(false);
      return;
    }
    setSamplesOpen(false);
    const typed = notes.length > 0 || Object.keys(segEdits).length > 0 || !!preAsk || !!preDraft.current.trim();
    const tuned = !!caps && !!settings && !sameSettings(settings, defaultSettings(caps, prefs || undefined));
    const ok = !(typed || tuned) || await new Promise<boolean>((resolve) =>
      setLeaveAsk({
        message: "換成這張照片的話，剛剛打的需求和選的設定都會清掉。確定要換嗎？",
        confirmLabel: "更換",
        cancelLabel: "取消",
        answer: (yes) => {
          setLeaveAsk(null);
          resolve(yes);
        },
      }),
    );
    if (!ok) {
      if (s.kind === "file") URL.revokeObjectURL(s.url);
      return;
    }
    setNotes([]);
    setSegEdits({});
    setPreAsk(null);
    setPreClear((n) => n + 1);
    if (caps) setSettings(defaultSettings(caps, prefs || undefined));
    choose(s);
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
    else if (phase !== "chat") {
      setPhase("chat");
      choose(s);
    } else swapSource(s);
  }

  function onGallerySelect(sel: GallerySelection) {
    if (!sel) return;
    // A past chat opens right away; a sample on the home page is only selected.
    if (phase === "chat" || sel.kind === "session") {
      if (sel.kind === "session") openOtherSession(sel.id);
      else swapSource({ kind: "demo", name: sel.name });
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
    let actionTried = false;
    // A short correction such as 改成張開 answers the active action question;
    // it must not revise another completed wish and leave this one unresolved.
    if ((bridgeQuestion?.kind === "action" || bridgeQuestion?.kind === "edit_description") && base === notes && !/取消|撤回|不做了|不用了|不要了/.test(text)) {
      actionTried = true;
      if (typed) setPreClear(n => n + 1);
      setPreAsk({ order: nextDisplayOrder(), text, persons: [] });
      try {
        if (await bridge.confirm([], text)) return base;
      } catch (error) {
        setError(errorText(error));
        return null;
      } finally { setPreAsk(null); }
    }
    if ((bridge.canRevise || bridge.result) && /不是|取消|撤回|保持原樣|不要|不整理|改成|改為|改看/.test(text)) {
      setPreAsk({ order: nextDisplayOrder(), text, persons: [] });
      try {
        const correction = await bridge.revise(text, preRefs(subjectIndices).flatMap(p => p.preview_person_id ? [p.preview_person_id] : []));
        if (correction) {
          if (typed) setPreClear(n => n + 1);
          return base;
        }
      } catch {
        // 這一步只是在判斷「是不是在改之前那句指定的人」。判斷不出來就當成新的
        // 一句，照一般流程檢查，不把後端的錯誤代碼丟給使用者。
      } finally { setPreAsk(null); }
    }
    if (!actionTried && bridgeQuestion && bridgeQuestion.kind !== "semantic" && base === notes) {
      const ids = preRefs(confirmationPointed).flatMap(p => p.preview_person_id ? [p.preview_person_id] : []);
      if (typed) setPreClear(n => n + 1);
      setPreAsk({ order: nextDisplayOrder(), text, persons: [] });
      let handled = false;
      try { handled = await bridge.confirm(ids, text); } finally { setPreAsk(null); }
      if (handled) return base;
    }
    // 回答 AI 的提問時沒再點人，就沿用被問的那句指的人：「想換成什麼顏色？」
    // 「棕色」講的還是同一位。
    const expectedPreview = preview.identity;
    const stillCurrent = () => previewReviewIdentity.current === expectedPreview;
    const asking = openAsk(base);
    const picked = preRefs(subjectIndices);
    const note: Note = { order: nextDisplayOrder(), text, persons: picked.length ? picked : asking?.persons || [],
      selectionSource: picked.length ? "user_selection" : asking?.selectionSource };
    // 要靠前一句才看得懂的話（回答追問的「棕色」、接著講的「褲子也是」），模型
    // 會補成完整的一句，連同它接過來的人；清單裡每一句才能單獨讀、單獨刪。回答
    // 追問時模型沒拼回來（或根本沒問到），就自己接上：至少看得出是在講哪件事。
    const completed = (line?: string, wheres: string[] = []): Partial<Note> => {
      const carried = wheres.flatMap((w) => preOrder.filter((p) => preWhere(p.subjectIndex) === w));
      const persons = note.persons.length ? note.persons : preRefs(carried.map((p) => p.subjectIndex));
      const wish = line || (asking ? `${asking.wish ?? asking.text}，${text}` : undefined);
      return { persons, selectionSource: note.selectionSource || (persons.length ? "model_suggestion" : undefined), ...(wish && wish !== text ? { wish } : {}) };
    };
    setPreAsk(note);
    // 又多講了一句，需求變了，剛才那個問題就不算數，等講完再按一次。
    setRenderAsk(null);
    if (typed) setPreClear((n) => n + 1);
    // 分段送：哪一段有衝突，模型就只重寫那一段，指定的人跟著段留住。
    const sent = promptSections(base, segEdits);
    try {
      const r = await api.promptReview(
        noteLine(note),
        bridge.result?.effective_prompt ?? notesPrompt(base, segEdits),
        reviewHistory(base, [...bridge.events, ...settingsEvents]),
        preOrder.map((p) => preWhere(p.subjectIndex)),
        sent.map((x) => ({ who: x.key, text: x.text })),
      );
      if (!stillCurrent()) return null;
      const settled: Note = {
        ...note,
        replyOrder: nextDisplayOrder(),
        reply: r.reply || (r.accepted ? "好，這些會當成個人化需求，美化時一起套用。" : "好，那就先不加個人化需求。"),
        replyPeople: prePickable.map(p => ({ ...p })),
        // 只是撤回前面那項的一句不進清單：前面那項拿掉，就是照原樣了。
        kept: r.accepted && !r.withdrawn,
        ...(r.semantic_pending && preview.identity ? { actionPending: true, resolvedWish: "" } : {}),
        ...(r.ask ? { asked: { options: r.options || [] } } : {}),
        ...(r.offer ? { offered: { options: r.options || [], ...(r.later ? { later: true } : {}) } } : {}),
        ...(r.accepted && !r.withdrawn ? completed(r.line, r.persons) : {}),
        ...(r.accepted && r.aspect_ratio ? { ratio: r.aspect_ratio } : {}),
      };
      // 跟前面的需求衝突（同一個人、同一件事不能兩個都做）：模型把衝突的那幾段
      // 拿掉被這句取代的部分，跟使用者手改那段字走同一條路；這句自己接在它那段
      // 後面。指定了人的句子只動那個人的段；沒指定的句子講的是所有人或整個畫面，
      // 每個人的段都可能要改。
      setNotes([...base, settled]);
      if (r.accepted && r.merged?.length) {
        const own = settled.persons.map(faceRef);
        const merged = r.merged;
        setSegEdits((e) => {
          const next = { ...e };
          for (const m of merged) {
            const sec = sent[m.section];
            if (sec && (!own.length || own.includes(sec.key))) next[sec.key] = { text: m.text, upTo: base.length };
          }
          return next;
        });
      }
      // 「做成限動」這種說了比例的需求，同步改「設定」的長寬比
      if (r.aspect_ratio) { const ratio = r.aspect_ratio; setSettings((s) => s && { ...s, aspect_ratio: ratio }); }
      // 「幫我美化」「幫我分析」就是底下那兩顆鍵，照按：已經收下的需求一起帶進去。
      if (r.start) startSession(r.start, laterNote(base), [...base, settled]);
      return r.accepted ? [...base, settled] : null;
    } catch {
      // 問不到就放行：一次模型失敗不該讓人卡在上傳頁面，而且進了 session，
      // 個人化需求面板按下確認時還會再過一次。
      if (!stillCurrent()) return null;
      const kept = { ...note, ...completed(), kept: true, ...(preview.identity ? { actionPending: true, resolvedWish: "" } : {}), replyOrder: nextDisplayOrder(),
        reply: "好，這些會當成個人化需求，美化時一起套用。",
        replyPeople: prePickable.map(p => ({ ...p })) };
      setNotes([...base, kept]);
      return [...base, kept];
    } finally {
      if (stillCurrent()) setPreAsk(null);
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
    setNotes([...notes, { order: nextDisplayOrder(), replyOrder: nextDisplayOrder(), text, wish: preWhoAsk.wish ?? preWhoAsk.text, persons, selectionSource: "user_selection", reply: WHO_ANSWERED_REPLY, kept: true }]);
  }

  // `later` 是要帶進 session、照片好了再回答的那句；沒給就看最後一句是不是。
  // `log` 是到這一刻為止的對話：打字要求開始的那句剛收下，還沒進 state。
  // `overwrite` 是一鍵美化前問到的答案：true 只照個人化需求改，false 整體美化
  // 再加上需求。有需求卻還沒問過，就先問，不開始。
  function startSession(mode: "analyze" | "render", later = laterNote(notes), log = notes, overwrite?: boolean) {
    if (!source || !caps || !settings || busy || submissionLock.current) return;
    if (mode === "render" && bridgeQuestion) {
      setError("請先確認個人化需求指定的人物，再開始美化。");
      return;
    }
    if (mode === "render" && overwrite === undefined && (bridge.result?.effective_prompt ?? notesPrompt(log, segEdits)).trim()) {
      setRenderAsk({ later, log });
      return;
    }
    setRenderAsk(mode === "render" && overwrite !== undefined ? { later, log, overwrite } : null);
    // 個人化需求只認已經送出、也已經讓 AI 看過的那幾句。輸入框裡打了沒送的字
    // 就當沒打過：它沒進過對話，也沒過把關，不該從這裡偷渡進 user_prompt。
    submissionLock.current = true;
    const selectedSource = selectionIdentity;
    // Call before leaving preview: this promise contains an already-frozen,
    // nonblocking decision and cannot adopt a result arriving during upload.
    const previewDecision = previewEnabled ? preview.forSubmission() : Promise.resolve(undefined);
    const since = Date.now();
    const say = (line: string) => setStarting((s) => s && { ...s, lines: [...s.lines, line] });
    setStarting({ mode, overwrite, since, lines: ["正在上傳照片…"] });
    setSubmitting({ source, mode, since });
    void run(async () => {
      const previewIdentity = await previewDecision;
      const submissionLog = previewEnabled && !previewIdentity ? log.map((note) => ({
        ...note, persons: note.persons.map(withoutPreviewIdentity),
      })) : log;
      const kept = keptNotes(submissionLog);
      if (sourceIdentity.current !== selectedSource) throw new Error("照片已更換，請重新選擇分析或一鍵美化。");
      const s = await api.createSession();
      const id = s.session_id;
      const userPrompt = (previewIdentity && bridge.result ? bridge.result.effective_prompt : notesPrompt(submissionLog, segEdits)).slice(0, PROMPT_MAX);
      // Only the picks the prompt still spells out: one entry per person
      // already names each face once, and PROMPT_MAX can still cut the last
      // entry off.
      const promptFaces = promptSections(kept)
        .flatMap(groupFaces)
        .filter((f) => userPrompt.includes(f.ref));
      // The defaults a photo added next starts from, so the upload below picks
      // the 個人化需求 up without a second patch.
      if (userPrompt || !sameSettings(settings, defaultSettings(caps))) {
        await api.patchPlan(id, {
          ...settings,
          ...(userPrompt ? { user_prompt: userPrompt, overwrite_prompt: !!overwrite } : {}),
        });
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
      if (sourceIdentity.current !== selectedSource) throw new Error("照片已更換，請重新選擇分析或一鍵美化。");
      const mapping = bridge.result;
      const up = await api.uploadPhoto(id, blob, name, previewIdentity && {
        ...previewIdentity, ...(mapping ? { conversation_token: mapping.token,
          conversation_wishes_digest: mapping.wishes_digest } : {}),
      }, previewEnabled ? (previewIdentity ? previewWishes : previewWishes.map(w => ({ ...w, target_ids: [] }))) : undefined);
      const added = up.photos?.[0];
      if (!added) throw new Error(up.errors?.[0]?.error || "上傳失敗");
      if (sourceIdentity.current !== selectedSource) return;
      say(mode === "analyze" ? "照片傳好了，準備開始分析…" : overwrite ? "照片傳好了，準備開始調整…" : "照片傳好了，準備開始美化…");
      // 上傳頁面已經偵測過這張的人臉，session 頁沒有分析時就直接用，不再偵測一次。
      if (!previewEnabled && preScan.ready) seedDetectedFaces(urls.sourceImage(added.analyze_job_id), preScan.people.map((p) => p.box));
      const uploadedSession = await api.getSession(id);
      if (sourceIdentity.current !== selectedSource) return;
      setSession(uploadedSession);
      setSelected(null);
      setPhase("session");
      setSessionHash(id);
      setSubmitting(null);
      // On the photo, not the session defaults: a second photo has its own
      // faces and must not inherit these.
      if (promptFaces.length) await api.patchPhotoPlan(id, added.photo_id, { prompt_faces: promptFaces });
      // 上傳頁面聊過的接在照片後面。等著回答的那句也在裡面：分析一好，session
      // 的 AI 就回答它，使用者不用再問一次。
      const carried = carriedLog(submissionLog, added.photo_id, later, [...bridge.events, ...settingsEvents]);
      if (carried.length) await api.postMessages(id, carried);
      if (sourceIdentity.current !== selectedSource) return;
      if (mode === "render") {
        await api.render(id, [added.photo_id]);
        if (sourceIdentity.current !== selectedSource) return;
        await afterRender(id, overwrite ? ONLY_PROMPT_LABEL : "一鍵美化", "spark", { ...settings });
      } else {
        const completedSession = await api.getSession(id);
        if (sourceIdentity.current !== selectedSource) return;
        setSession(completedSession);
      }
      if (sourceIdentity.current !== selectedSource) return;
      setSelected(null);
      setPhase("session");
      setSessionHash(id);
      refreshSessions();
    }).finally(() => {
      submissionLock.current = false;
      setSubmitting(null);
      setStarting(null);
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

  function askPhoto(text: string, subjectIndices: number[], screen?: Screen, faces?: AskFace[]) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    setPendingAsk({ text, subjectIndices });
    return run(async () => {
      try {
        await api.ask(id, pid, text, faces ? [] : subjectIndices, screen, faces);
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
  function savePrompt(text: string, screen?: Screen) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    // 沒有使用者泡泡，只顯示 AI 正在回。
    setPendingAsk({ text: "", subjectIndices: [] });
    return run(async () => {
      try {
        await api.patchPhotoPlan(id, pid, { user_prompt: text });
        await api.reviewPrompt(id, pid, screen);
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

  // 「幫我分析」 on a photo uploaded with 「只照需求改」: its analysis was
  // skipped, so run it now. Back to 整體美化 first -- the next render follows
  // the analysis too. The status polls post the check card when it lands.
  function analyzePhoto() {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    return run(async () => {
      await api.patchPhotoPlan(id, pid, { overwrite_prompt: false });
      await api.analyze(id, pid);
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
  if (!caps || !settings || opening) return <Header onHome={() => {}} onHistory={() => {}} showHome={false} />;

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

  // Right before leaving for 面板模式 by the conversation's 對話｜面板 switch:
  // hands this photo's adjustments over.
  const toOldUi = () => {
    if (caps && photo?.case_id) writeHandoff(photo.case_id, photo.plan, caps, analyses[photo.photo_id]?.analysis);
  };
  const header = (
    <Header
      jobId={phase === "session" ? photo?.active_version?.job_id || photo?.analyze_job_id || null : null}
      onHome={async () => {
        if (await confirmLeave("回到首頁")) goHome();
      }}
      onHistory={() => {
        refreshSessions();
        setDrawer(true);
      }}
      showHome={phase !== "home"}
      onNewChat={phase === "session" ? askNewChat : undefined}
      showHistory={phase !== "home"}
      showChangelog={phase === "home"}
      onSettings={phase === "home" ? undefined : () => setPrefsOpen(true)}
    />
  );
  // 還沒進對話的話，上傳頁的設定跟著一起換，這張照片就照新的跑。對話中也當下生效：
  // 後端每次回話都讀照片 plan 上的值，直接改照片的 plan 就好。不走
  // run()：美化跑到一半也要能切，也不該把畫面鎖住。
  const prefsEl = prefsOpen && prefs && (
    <PrefsModal
      prefs={prefs}
      onChange={(p) => {
        setPrefs(p);
        savePrefs(p);
        setSettings((s) => s && { ...s, ...p });
        if (phase === "session" && session && p.detailed_explanation !== prefs.detailed_explanation) {
          const id = session.session_id;
          Promise.all(
            session.photos
              .filter((ph) => ph.status !== "failed")
              .map((ph) => api.patchPhotoPlan(id, ph.photo_id, { detailed_explanation: p.detailed_explanation })),
          ).catch((e) => setError(errorText(e)));
        }
      }}
      onClose={() => setPrefsOpen(false)}
    />
  );
  const drawerEl = (
    <HistoryDrawer
      open={drawer}
      sessions={sessions}
      currentId={session?.session_id || null}
      onOpen={openOtherSession}
      onClose={() => setDrawer(false)}
      onRemove={removeRows}
      onClearAll={() => removeRows("all")}
    />
  );
  const confirmEl = leaveAsk && (
    <ConfirmModal
      message={leaveAsk.message}
      confirmLabel={leaveAsk.confirmLabel || "繼續"}
      cancelLabel={leaveAsk.cancelLabel || "留在這裡"}
      onAnswer={leaveAsk.answer}
    />
  );
  const askEl = deleteAsk && (
    <ConfirmModal
      message={deleteAsk.message}
      confirmLabel={deleteAsk.notice ? "確定" : "刪除"}
      cancelLabel={deleteAsk.notice ? undefined : "取消"}
      onAnswer={deleteAsk.done}
    />
  );
  // Both returns mount it; it draws nothing until opened.
  const samplesEl = (
    <SamplePicker
      open={samplesOpen}
      demos={demos}
      picked={source?.kind === "demo" ? source.name : null}
      onPick={(name) => swapSource({ kind: "demo", name })}
      onClose={() => setSamplesOpen(false)}
    />
  );

  if (submitting) {
    return (
      <>
        {header}
        <main>
          <section id="upload-section" class="section visible">
            <div class="container upload-container">
              <div class="upload-grid">
              <div class="upload-content-row is-chat">
                <PreStage source={submitting.source} />
                <ChatPanel class="home-chat has-photo" sub={submitting.mode === "analyze" ? "分析" : "一鍵美化"} footer={null}>
                  <AssistantMsg>
                    <p class="chat-text">{submitting.mode === "analyze" ? "正在準備分析照片" : "正在準備一鍵美化"}</p>
                    <WorkingCard done={starting?.lines.slice(0, -1) || []}
                      now={starting?.lines[starting.lines.length - 1] || "正在上傳照片…"}
                      since={submitting.since} />
                  </AssistantMsg>
                </ChatPanel>
              </div>
              </div>
            </div>
          </section>
        </main>
        {drawerEl}{prefsEl}{confirmEl}{askEl}
      </>
    );
  }

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
            analysis={analyses[photo.photo_id]?.analysis || null}
            analysisPending={analysisReady && !analyses[photo.photo_id]}
            selected={selected}
            pendingAsk={pendingAsk}
            busy={busy}
            error={error}
            onSelectVersion={selectVersion}
            onRender={renderPhoto}
            onFile={pickFile}
            onConfirmPeople={async (indices, protection_scope, selected_wish, edit_scope, target_selection, text) => {
              let newMessage = false;
              const ok = await run(async () => {
                const state = photo.preview_conversation;
                if (!state?.question) return;
                const response = await api.confirmPhotoConversation(session.session_id, photo.photo_id, {
                  revision: state.revision, question_id: state.question.question_id,
                  wish_ids: state.question.wish_ids, subject_indices: indices, protection_scope, selected_wish, edit_scope, target_selection, text,
                });
                if (response.confirmation_handled === false) { newMessage = true; return; }
                setSession(await api.getSession(session.session_id));
              });
              return newMessage ? "new_message" : ok;
            }}
            onAsk={askPhoto}
            onAnswerWaiting={answerWaiting}
            onPatch={patchFromChat}
            onSavePrompt={savePrompt}
            onPost={postFromChat}
            onAnalyze={analyzePhoto}
            panelMode={{ href: urls.oldUi(photo.case_id), busy: isBusy(photo), onLeave: toOldUi }}
            onResolve={resolveProposal}
          />
        </main>
        {drawerEl}{prefsEl}
        {samplesEl}
        {confirmEl}
        {askEl}
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
      onRemove={(id) => removeRows([id])}
      onAll={(t) => {
        if (t === "sample") return showSamples();
        refreshSessions();
        setDrawer(true);
      }}
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
                  // 範例照片點了但沒按「使用所選照片」就直接開始對話，就拿點的那張。
                  <HomeIntro onStart={() => newChat(selection?.kind === "demo" ? { kind: "demo", name: selection.name } : undefined)} />
                ) : (
                  <>
                  {/* Nothing to stage until a photo is picked: before that the
                      conversation is the whole page, centred, and ChatPanel
                      itself is the drop target. */}
                  {source && <PreStage source={source} />}
                  <ChatPanel
                    class={`home-chat${source ? "" : " is-solo"}`}
                    sub="新對話"
                    photo={source ? { src: sourceSrc(source), onOpen: () => setViewing(true) } : undefined}
                    onDropFile={busy ? undefined : pickFile}
                    footer={
                      <>
                        <FootBar
                          faces={
                            !bridgeQuestion && !preWhoAsk && preDisplayPeople.length > 1 &&
                              <FacePicker people={preDisplayPeople} pointed={prePointed} onPoint={setPrePointed} />
                          }
                        />
                        <Composer
                          onFile={pickFile}
                          // Only once there is a photo to replace; before that the
                          // greeting's two chips are the choice, right above.
                          onSample={source ? showSamples : undefined}
                          onText={(t) => (preDraft.current = t)}
                          disabled={busy || !!preAsk || preHeld}
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
                          people={preDisplayPeople}
                          pointed={preDisplayPeople.length ? prePointed : []}
                          onPoint={setPrePointed}
                          sendPicked={!!preWhoAsk}
                          focusKey={matchMedia("(hover:hover) and (pointer:fine)").matches ? pickHint : 0}
                          hintKey={pickHint}
                          // Left to the composer once someone is pointed at, so the
                          // field says whose turn it is instead of this line --
                          // unless the pick is the answer, and then it says so.
                          // 由長到短，Composer 挑輸入框放得下的那句。
                          placeholder={
                            (preDisplayPeople.length ? prePointed.length : 0) && preWhoAsk
                              ? ["選好了就直接送出，也可以再打字補充", "選好就送出，或再打字補充"]
                              : !source || (preDisplayPeople.length ? prePointed.length : 0) || preScanning
                              ? undefined
                              : openAsk(notes)
                                ? openAsk(notes)!.asked!.options.length
                                  ? ["直接打字回答，或點上面的選項", "打字回答，或點上面的選項"]
                                  : preDisplayPeople.length > 1
                                    ? ["點上面的頭像選人，或直接打字回答", "點上面的頭像，或打字回答", "點頭像或打字回答"]
                                    : "直接打字回答"
                                : preDisplayPeople.length > 1
                                ? ["想怎麼美化這張照片？點頭像可以指定要改誰", "想怎麼美化？點頭像可以指定人", "想怎麼美化這張照片？"]
                                : ["想怎麼美化這張照片？也可以留空直接開始", "想怎麼美化？留空也可以直接開始", "想怎麼美化這張照片？"]
                          }
                        />
                      </>
                    }
                  >
                    <PreChat
                      previewEvents={[...bridge.events, ...settingsEvents]}
                      activeTargetQuestion={bridge.result?.question && bridge.events.some(e => e.questionSnapshot?.token === bridge.result?.token) ? { ...bridge.result.question, token: bridge.result.token, revision: bridge.result.revision } : null}
                      selectedTargetIds={preRefs(confirmationPointed).flatMap(p => p.preview_person_id ? [p.preview_person_id] : [])}
                      onTargetPoint={id => {
                        const person = prePickable.find(p => p.preview_person_id === id);
                        if (person) setConfirmationPointed(old => old.includes(person.subjectIndex) ? old.filter(i => i !== person.subjectIndex) : [...old, person.subjectIndex]);
                      }}
                      onTargetSelection={(...args) => void bridge.confirm(...args).then(() => setConfirmationPointed([])).catch(e => setError(errorText(e)))}
                      onPeoplePresented={generation => {
                        if (generation && currentPresentation.current === generation) setPresentedGeneration(generation);
                      }}
                      caps={caps}
                      people={prePickable}
                      step={step}
                      via={via}
                      source={source}
                      settings={settings}
                      notes={notes}
                      sections={preSections.map((x) => ({ key: x.key, line: sectionLine(x), text: x.text, persons: x.persons }))}
                      pending={preAsk}
                      scanning={preScanning}
                      previewActive={previewEnabled}
                      previewError={preview.error}
                      faceCount={prePickable.length}
                      busy={busy}
                      starting={starting}
                      error={error}
                      onUpload={() => fileInput.current?.click()}
                      onView={() => setViewing(true)}
                      onSamples={showSamples}
                      onSettings={next => {
                        setSettings(next);
                        setSettingsEvents(events => events.map((event, i) => i === events.length - 1
                          ? { ...event, settings: { ...next, advanced_options: { ...next.advanced_options } } } : event));
                      }}
                      onClear={() => {
                        setNotes(dropNotes(notes, () => true));
                        setSegEdits({});
                        setRenderAsk(null);
                      }}
                      onPrompt={(edits, upTo) => {
                        setRenderAsk(null);
                        // 整段刪光就是把這段的需求拿掉，跟按「清除所有需求」一樣算拿掉，
                        // 只是只動這幾段。同一句也指了別人的，那幾位的部分還在，句子
                        // 不拿掉，只把這段清空。
                        const cleared = new Set(edits.filter((x) => !x.text).map((x) => x.key));
                        if (cleared.size) {
                          const gone = new Set(notes.slice(0, upTo).filter((n) => noteParts(n).every((x) => cleared.has(x.key))));
                          setNotes(dropNotes(notes, (n) => gone.has(n)));
                        }
                        setSegEdits((e) => ({ ...e, ...Object.fromEntries(edits.map((x) => [x.key, { text: x.text, upTo }])) }));
                      }}
                      onEdit={() => {
                        const interaction = Math.max(0, ...bridge.events.map(e => e.order || 0), ...notes.map(n => Math.max(n.replyOrder || n.order || 0, n.dropped?.order || 0)));
                        if (lastSettingsClick.current === interaction) return;
                        lastSettingsClick.current = interaction;
                        const latest = settingsEvents[settingsEvents.length - 1];
                        const latestOther = interaction;
                        if (step === "settings" && latest && (latest.order || 0) > latestOther) return;
                        const order = nextDisplayOrder();
                        setSettingsEvents(events => [...events, { id: `settings-${order}`, order,
                          after: conversation(notes).length, kind: "settings", settings: { ...settings, advanced_options: { ...settings.advanced_options } },
                          text: "好，調整下面的設定，完成後選擇要分析還是一鍵美化。", showPeople: false }]);
                        setStep("settings");
                      }}
                      onStart={startSession}
                      renderAsk={renderAsk && { overwrite: renderAsk.overwrite }}
                      onRenderMode={(overwrite) =>
                        renderAsk && startSession("render", renderAsk.later, renderAsk.log, overwrite)
                      }
                      onAnswer={(text) => void reviewNote(text, [], notes, false)}
                      onWho={(where) => answerWho(prePickable.filter((p) => p.where === where).map((p) => p.subjectIndex))}
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
      {drawerEl}{prefsEl}
      {samplesEl}
      {confirmEl}
      {askEl}
      <PhotoViewer src={viewing && source ? fullSrc(source) : null} onClose={closeViewer} />
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
        <img src={fullSrc(source)} alt="待美化的照片" />
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

// 跟 session 的個人化需求面板同一套：先用看的，要改再打開輸入框。整張就是一段字，
// 不同對象之間用「；」隔開，指定了人的那段前面掛著那個人的頭像：
// 「(頭像)頭髮染黑；天空變藍；(頭像)頭髮紅」。
//
// 改的時候也是同一段，頭像嵌在字裡、不能打字進去（只能整顆刪掉），不會變回
// 「從左邊數來第 2 位：」。存的時候從頭讀：遇到頭像就開始那個人的段，遇到「；」
// 就結束目前這段，後面沒接頭像的字算沒指定對象。整段刪光的對象就是拿掉。
// `upTo` 是打開那一刻有幾句——改的期間又收下的句子不在框裡，存檔後會接在後面。
type NoteSection = { key: string; line: string; text: string; persons: PrePerson[] };

const sectionShown = (sec: NoteSection) => (sec.key ? sec.line.slice(whoPrefix(sec.persons).length) : sec.line);

function NotesEditor(props: {
  sections: NoteSection[];
  upTo: number;
  held: boolean;
  onSave: (edits: { key: string; text: string }[], upTo: number) => void;
}) {
  const { sections, held } = props;
  const [editing, setEditing] = useState<{ upTo: number; dirty: boolean } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const open = !!editing;
  // 框裡的內容只在打開時建一次，之後交給使用者打字，Preact 不碰。
  useEffect(() => {
    const el = box.current;
    if (!open || !el) return;
    fillNoteBox(el, sections.map((sec) => ({ key: sec.key, faces: sec.persons, text: sec.key ? sectionShown(sec) : sec.text })));
  }, [open]);
  if (!editing) {
    return (
      <>
        <p class="chat-opt-prompt-text">
          {sections.map((sec, i) => [
            i ? "；" : "",
            sec.key ? withFaces(whoPrefix(sec.persons), sec.persons) : null,
            sectionShown(sec),
          ])}
        </p>
        <div class="chat-choices">
          <button
            type="button"
            class="chat-chip chat-opt-chip is-primary"
            disabled={held}
            onClick={() => setEditing({ upTo: props.upTo, dirty: false })}
          >
            修改
          </button>
        </div>
      </>
    );
  }
  return (
    <>
      <div
        class="chat-notes-box"
        ref={box}
        role="textbox"
        aria-multiline="true"
        aria-label="個人化需求"
        contentEditable={!held}
        onInput={() => !editing.dirty && setEditing({ ...editing, dirty: true })}
        {...noteBoxKeys}
      />
      <div class="chat-choices">
        <button
          type="button"
          class="chat-chip chat-opt-chip is-primary"
          disabled={held || !editing.dirty}
          onClick={() => {
            const read = box.current ? readNoteBox(box.current, sections.map((x) => x.key)) : [];
            const before = Object.fromEntries(sections.map((x) => [x.key, x.text]));
            const changed = read
              .filter((x) => x.text.length <= PROMPT_MAX && x.text !== before[x.key])
              .map((x) => ({ key: x.key, text: x.text }));
            if (changed.length) props.onSave(changed, editing.upTo);
            setEditing(null);
          }}
        >
          儲存
        </button>
        <button type="button" class="chat-chip chat-opt-chip is-ghost" disabled={held} onClick={() => setEditing(null)}>
          取消
        </button>
      </div>
    </>
  );
}

function PreChat(props: {
  activeTargetQuestion?: TargetQuestionSnapshot | null;
  selectedTargetIds?: string[];
  onTargetPoint?: (id: string) => void;
  onTargetSelection?: (ids: string[], text?: string, scope?: "features" | "all", wish?: string, edit?: "visible_hair" | "cancel", selection?: "listed" | "outside_preview") => void;
  onPeoplePresented?: (generation: string | null | undefined) => void;
  previewEvents?: PreviewReplyEvent[];
  caps: Capabilities;
  // Everyone the detector found, so the AI's words about where someone stands
  // can show that face instead.
  people: (FaceRef & { previewRole?: string })[];
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
  previewActive: boolean;
  previewError?: string;
  // How many people the local detector found in the picked photo: the picker
  // under the composer only appears from two up, and so does the line about it.
  faceCount: number;
  busy: boolean;
  starting: StartProgress | null;
  error: string;
  onUpload: () => void;
  onView: () => void;
  onSamples: () => void;
  onSettings: (s: Settings) => void;
  // The 個人化需求, one section per person (or group tapped together) plus one
  // for wishes with no one tapped: `line` as shown, `text` as edited.
  sections: { key: string; line: string; text: string; persons: PrePerson[] }[];
  onClear: () => void;
  // The sections changed in one save of the editor; each covers the first
  // `upTo` notes.
  onPrompt: (edits: { key: string; text: string }[], upTo: number) => void;
  onEdit: () => void;
  onStart: (mode: "analyze" | "render") => void;
  // 一鍵美化前在問需求怎麼套用；`overwrite` 是已經選了的那個（上傳中）。
  renderAsk: { overwrite?: boolean } | null;
  onRenderMode: (overwrite: boolean) => void;
  // One of the options under the AI's question, tapped.
  onAnswer: (text: string) => void;
  // 問「哪一位」時，對話裡那排頭像點了其中一位（`where` 是那位的位置）。
  onWho?: (where: string) => void;
}) {
  const { caps, step, via, source, busy, notes, pending, scanning, starting } = props;
  // 還沒分析，沒人有名字，AI 只能講站在哪裡（「從左邊數來第 2 位」）；那幾個字
  // 一樣換成頭像。
  const said = (text: string, people: FaceRef[]) =>
    faceText(
      text,
      people.flatMap((f) => [
        { ref: f.where || "", face: f },
        { ref: (f.where || "").replace(/^照片中/, ""), face: f },
      ]),
    );
  // 整段對話，照發生順序：每句需求一輪，使用者拿掉需求也一輪。
  const events = props.previewEvents || [];
  // Compatibility for an existing settings mode without a recorded event.
  const displayEvents = step === "settings" && !events.some(e => e.kind === "settings")
    ? [...events, { id: "settings-current", after: conversation(notes).length, kind: "settings" as const,
        text: "好，調整下面的設定，完成後選擇要分析還是一鍵美化。", showPeople: false }]
    : events;
  const log = conversation(pending ? [...notes, pending] : notes, displayEvents);
  const targetControls = (event: PreviewReplyEvent) => {
    const q = event.questionSnapshot;
    if (!q) return null;
    const active = !q.answered && q.token === props.activeTargetQuestion?.token && q.question_id === props.activeTargetQuestion?.question_id && q.revision === props.activeTargetQuestion?.revision;
    const disabled = !active || busy;
    const ids = active ? (props.selectedTargetIds || []) : [];
    return <div class="chat-target-confirmation" data-question-id={q.question_id}>
      {q.answered ? <p class="chat-meta">已確認：{q.answer}</p> : <div class="chat-choices">
        {(!q.kind || q.kind === "person") && <>
          {(event.people || []).map(person => person.preview_person_id && <button type="button"
            class={`chat-chip chat-target-person${ids.includes(person.preview_person_id) ? " is-picked" : ""}`}
            disabled={disabled} aria-pressed={ids.includes(person.preview_person_id)} onClick={() => props.onTargetPoint?.(person.preview_person_id!)}>
            {person.thumb && <img src={person.thumb} alt="" />}<span>{person.name || person.where}</span>
          </button>)}
          <Chip label="確認這筆需求的對象" disabled={disabled || !ids.length} onClick={() => props.onTargetSelection?.(ids)} />
          <Chip label="不在已辨識的人物清單中" disabled={disabled} onClick={() => props.onTargetSelection?.([], "不在已辨識的人物清單中", undefined, undefined, undefined, "outside_preview")} />
        </>}
        {q.kind === "semantic" && <>
          <Chip label="重新確認這項需求" disabled={disabled} onClick={() => props.onTargetSelection?.([], "重新確認這項需求")} />
          <Chip label="分析" icon="search" disabled={disabled} onClick={() => props.onStart("analyze")} />
        </>}
        {q.kind === "wish" && q.wish_options?.map(option => <Chip label={option.text} disabled={disabled}
          onClick={() => props.onTargetSelection?.([], option.text, undefined, option.wish_id)} />)}
        {q.kind === "scope" && q.options?.map((label, i) => <Chip label={label} disabled={disabled}
          onClick={() => props.onTargetSelection?.([], label, i === 0 ? "features" : "all")} />)}
        {q.kind === "edit_scope" && q.options?.map((label, i) => <Chip label={label} disabled={disabled}
          onClick={() => props.onTargetSelection?.([], label, undefined, undefined, i === 0 ? "visible_hair" : "cancel")} />)}
      </div>}
    </div>;
  };

  const settingsTurns = displayEvents.filter(e => e.kind === "settings");
  const activeSettingsId = settingsTurns[settingsTurns.length - 1]?.id;
  // 動作鍵跟在目前對話後面；沒有模型回覆的確認文字屬於原本那一輪。
  const last = log[log.length - 1];
  // 舊偵測流程保留原等待條件；2.0 預覽不阻擋正式分析。
  const held = busy || (scanning && !props.previewActive);
  // 送出前的最後一眼。對話往上捲是整段過程——被擋下來的句子、AI 回的話、設定
  // 卡夾在中間；這張只放真的會跟著進 session 的那段字，而且就擺在動作鍵正上方，
  // 要確認的東西跟要按下去的地方是同一處。跟 session 的個人化需求一樣是一整段，
  // 看到的就是送出去的 user_prompt；要刪要改直接改字。
  const kept = props.sections.length > 0;
  const asking = props.renderAsk;
  // 按下去就算選了：這排定格成紀錄，按的那顆維持選取的樣子；失敗了再解開。
  const went = !asking && starting ? starting.mode : null;
  const review = kept && (
    <div class="chat-card chat-notes">
      <span class="chat-meta">個人化需求</span>
      <NotesEditor sections={props.sections} upTo={notes.length} held={held} onSave={props.onPrompt} />
    </div>
  );
  const startChips = !props.activeTargetQuestion && (
    <div class="chat-choices chat-actions">
      {/* Reading order follows the order of operations: adjust the settings
          first, then run. 分析 and 一鍵美化 are peers -- both are "go", they
          differ only in how far they go -- so they carry the same weight. */}
      {!asking && !went && (step === "picked" || step === "settings" || kept) && (
        <div class="chat-actions-aux">
          {(step === "picked" || step === "settings") && <Chip label="設定" icon="tune" kind="ghost" disabled={held} onClick={props.onEdit} />}
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
        {/* 按了一鍵美化、正在問怎麼套用時，這排就是已經做過的選擇：定格，
            一鍵美化維持選取的樣子，跟回答過的其他提問一樣。 */}
        <Chip
          label="分析"
          icon="search"
          kind={went === "analyze" ? "picked" : asking || went ? undefined : "primary"}
          settled={!!asking || !!went}
          disabled={held}
          onClick={() => props.onStart("analyze")}
        />
        <Chip
          label="一鍵美化"
          icon="spark"
          kind={asking || went === "render" ? "picked" : went ? undefined : "primary"}
          settled={!!asking || !!went}
          disabled={held}
          onClick={() => props.onStart("render")}
        />
      </div>
    </div>
  );
  // 需求與操作鍵只在對話末尾呈現一次，不嵌入歷史訊息或設定面板。
  const actions = (
    <>
      {review}
      {startChips}
    </>
  );

  const peopleCard = (people: typeof props.people) => people.length > 0 && (
    <div class="preview-selection" aria-label="已辨識人物">
              <div class="subject-preview" aria-label="人物初步辨識">
                <div class="subject-preview-people">
                  {people.map((person) => (
                    <div class="subject-preview-person">
                      <span class="subject-preview-avatar">{person.thumb && <img src={person.thumb} alt="" />}</span>
                      <span data-chat-typewriter-text>{person.name || person.where}</span>
                      {person.previewRole === "bystander" && <small class="chat-muted">背景人物</small>}
                      {person.previewRole === "uncertain" && <small class="chat-muted">待確認</small>}
                    </div>
                  ))}
                </div>
              </div>
    </div>
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
            <UploadBubble
              src={sourceSrc(source)}
              text={source.kind === "file" ? "上傳了這張" : "選了這張"}
              onOpen={props.onView}
            />
          </UserMsg>

          {(!scanning || props.previewActive) && (
          <AssistantMsg>
            <p class="chat-text">照片選好了。想怎麼修，直接打在下面的對話框告訴我。</p>
            {/* 底下那三顆鍵都只有兩四個字，差在哪得自己猜。這裡一顆補一句：分
                析是先看過再決定，一鍵美化是現在就動手，設定是動手前先換工具。寫
                「讓你看」不寫「跟你確認」——分析完照樣可以直接按美化，一項一項
                挑是可以做的事，不是非做不可。 */}
            <p class="chat-text">要先看我對這張照片的分析，還是直接幫你美化？</p>
            <p class="chat-text chat-muted">分析會先讓你看我打算修什麼，一鍵美化就直接修完；想換引擎或長寬比，先按設定。</p>
          </AssistantMsg>
          )}
          {props.previewError && <AssistantMsg><p class="chat-text chat-muted">{props.previewError}</p></AssistantMsg>}

        </>
      )}

      {/* 沒進清單的句子留在原位——刪掉整輪對話的話，畫面上就看不出曾經講過這
          件事、AI 又回了什麼，往上捲等於看到一份被修過的紀錄。AI 收不下的那
          句，它自己的回覆就說了為什麼；使用者自己拿掉的，底下接一句 AI 的回
          話。兩種都不動使用者打的字。 */}
      {log.map((t, i) => {
        if ("ask" in t && t.ask === pending) return <div key={`note-${t.ask.order ?? notes.length}-user`} class="note-turn">
          <AskBubble text={pending.text} faces={pending.persons} />
          <AssistantMsg><div class="chat-typing" aria-label="AI 正在回覆"><span /><span /><span /></div></AssistantMsg>
        </div>;
        // AI 還在等使用者回答它的提問時，那句需求還沒講完，清單跟開始鍵都先不出
        // 來——畫面上只留問題跟回答的方式，答完才接回收尾。問「要不要幫你分析」
        // 時也一樣：選項本身就是那兩顆鍵，再擺一次只是重複。
        const asking = "ask" in t && !!(t.ask.asked || t.ask.offered);
        if ("previewReply" in t && t.previewReply.kind === "settings") return <div key={t.previewReply.id} class="settings-turn">
          <UserMsg><ClickBubble icon="tune" label="設定" /></UserMsg>
          <AssistantMsg>
            <p class="chat-text">{t.previewReply.text}</p>
            {t.previewReply.id === activeSettingsId
              ? <SettingsEditor caps={caps} settings={props.settings} onChange={props.onSettings} />
              : <div class="chat-card chat-settings-history"><span class="chat-meta">當時的設定</span>
                  <p>{t.previewReply.settings?.analysis_model} · {t.previewReply.settings?.render_option} · {t.previewReply.settings?.aspect_ratio}</p>
                  <p>詳細解說：{t.previewReply.settings?.detailed_explanation ? "開啟" : "關閉"}</p>
                  {t.previewReply.settings && <p>{caps.advanced_options.map(option => `${option.label}：${t.previewReply.settings?.advanced_options?.[option.key] ?? option.default ? "開啟" : "關閉"}`).join(" · ")}</p>}
                </div>}
          </AssistantMsg>
        </div>;
        if ("previewReply" in t && t.previewReply.role === "user") return <AskBubble key={t.previewReply.id} text={t.previewReply.text} faces={[]} />;
        if ("previewReply" in t) return <AssistantMsg key={t.previewReply.id} atomic={t.previewReply.showPeople} animate={t.previewReply.showPeople}
          onTypingComplete={t.previewReply.showPeople ? () => props.onPeoplePresented?.(t.previewReply.generation) : undefined}>
          {t.previewReply.showPeople && t.previewReply.presentation ? <>
            <p class="chat-text">{t.previewReply.presentation.opening}</p>
            <div data-chat-typewriter-group>{peopleCard(t.previewReply.people || [])}</div>
            <p class="chat-text">{t.previewReply.presentation.closing}</p>
          </> : <>
            {t.previewReply.showPeople && peopleCard(t.previewReply.people || [])}
            <p class="chat-text">{t.previewReply.text}</p>
          </>}
          {targetControls(t.previewReply)}
        </AssistantMsg>;
        if ("gone" in t)
          return (
            <AssistantMsg key={`gone-${t.gone[0].dropped?.order ?? i}`}>
              <p class="chat-text">{goneReply(t)}</p>
            </AssistantMsg>
          );
        // AI 問回來的那句底下接選項。答過了（或話題已經往下走）就定格成紀錄，
        // 點過的那顆維持選取的樣子，跟開頭兩顆上傳鍵一樣。
        const options = t.ask.asked?.options || t.ask.offered?.options || [];
        const open = i === log.length - 1 && !pending;
        const next = notes[notes.indexOf(t.ask) + 1];
        // 問「哪一位」：人就擺在問題底下，點一下就是回答，不用再到輸入框旁邊
        // 選、再按送出。答過了跟選項一樣定格，點的那位維持選取的樣子。
        const who = t.ask.asked && !options.length ? t.ask.replyPeople || [] : [];
        return (
          <div key={`note-${t.ask.order ?? notes.indexOf(t.ask)}-${t.part || "both"}`} class="note-turn">
            {t.part !== "reply" && <AskBubble text={t.ask.text} faces={t.ask.persons} />}
            {t.part !== "user" && t.ask !== pending && (
              <AssistantMsg>
                <p class="chat-text">{said(t.ask.reply || (t.ask.kept !== false ? "好，這些會當成個人化需求，美化時一起套用。" : "好，那就先不加個人化需求。"), t.ask.replyPeople || t.ask.persons)}</p>
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
                {who.length > 1 && (
                  <div class="chat-choices">
                    {who.map((person) => {
                      const label = (person.name || person.where || "").replace(/^照片中/, "");
                      const picked = !open && next?.selectionSource === "user_selection" && next.persons.some((p) => p.where === person.where);
                      const body = <>{person.thumb ? <img src={person.thumb} alt="" /> : <span class="chat-face-blank" />}<span>{label}</span></>;
                      const cls = `chat-chip chat-target-person${picked ? " is-picked" : ""}${open ? "" : " is-static"}`;
                      return open
                        ? <button type="button" class={cls} disabled={held} onClick={() => person.where && props.onWho?.(person.where)}>{body}</button>
                        : <span class={cls}>{body}</span>;
                    })}
                  </div>
                )}
              </AssistantMsg>
            )}
          </div>
        );
      })}
      {source && (!scanning || props.previewActive) && !pending && !(last && "ask" in last && (last.ask.asked || last.ask.offered)) && <div class="prechat-actions">{actions}</div>}

      {/* 需求要怎麼套用，只有使用者自己知道：「把背景換成海邊」可能是只想換
          背景，也可能是想順便整張修好。兩條路一樣重，不替人預設。 */}
      {asking && (
        <>
          <UserMsg>
            <ClickBubble icon="spark" label="一鍵美化" />
          </UserMsg>
          <AssistantMsg>
            <p class="chat-text">要整體美化，還是僅依你的需求修改？</p>
            <p class="chat-text chat-muted">
              {WITH_ANALYSIS_LABEL}：照你的需求修改，光線、人像、構圖也一起調整。{ONLY_PROMPT_LABEL}：只動你寫到的地方，其他保持原樣。
            </p>
            <div class="chat-choices">
              {[
                { label: WITH_ANALYSIS_LABEL, overwrite: false },
                { label: ONLY_PROMPT_LABEL, overwrite: true },
              ].map((o) => (
                <Chip
                  label={o.label}
                  kind={asking.overwrite === o.overwrite ? "picked" : undefined}
                  settled={busy && asking.overwrite !== undefined}
                  disabled={held}
                  onClick={() => props.onRenderMode(o.overwrite)}
                />
              ))}
            </div>
          </AssistantMsg>
        </>
      )}
      {starting && (
        <>
          <UserMsg>
            {starting.mode === "analyze" ? (
              <ClickBubble icon="search" label="分析" />
            ) : asking ? (
              <ClickBubble icon="spark" label={starting.overwrite ? ONLY_PROMPT_LABEL : WITH_ANALYSIS_LABEL} />
            ) : (
              <ClickBubble icon="spark" label="一鍵美化" />
            )}
          </UserMsg>
          <AssistantMsg>
            <WorkingCard
              done={starting.lines.slice(0, -1)}
              now={starting.lines[starting.lines.length - 1]}
              since={starting.since}
            />
          </AssistantMsg>
        </>
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

// 上傳頁按了分析／一鍵美化之後、進 session 之前，已經做到哪幾步。
type StartProgress = { mode: "analyze" | "render"; overwrite?: boolean; since: number; lines: string[] };

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
  "advanced_options", "preset", "run_count", "user_prompt", "pose_face_changes",
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
  // 只照需求改 → 整體美化 也是一種改動：同樣的設定修出來會不一樣。只記 true，
  // 舊的快照沒有這個欄位，false 和沒寫要算同一件事。
  if (plan.overwrite_prompt) out.overwrite_prompt = true;
  return canonical(out) as Dict;
}

function SessionView(props: {
  onConfirmPeople: (indices: number[], scope?: "features" | "all", selectedWish?: string, editScope?: "visible_hair" | "cancel", selection?: "listed" | "outside_preview", text?: string) => Promise<boolean | "new_message">;
  caps: Capabilities;
  debugUi: boolean;
  session: Session;
  photo: Photo;
  analysis: Analysis | null;
  // 分析好了、但還在路上（整頁剛載入時要另外抓）。這段期間有些訊息還畫不出來。
  analysisPending: boolean;
  selected: string | null;
  pendingAsk: { text: string; subjectIndices: number[] } | null;
  busy: boolean;
  error: string;
  onSelectVersion: (v: Version) => void;
  onRender: (label: string, icon: string, patch?: Plan) => void;
  // 只剩拖放進畫布這條路會用到 -- 丟一張新的照片進來等於開一個新對話。
  onFile: (f: File) => void;
  // faces 只在沒有分析時用：指的人是本地偵測的框和位置，取代 subjectIndices 送出。
  onAsk: (text: string, subjectIndices: number[], screen?: Screen, faces?: AskFace[]) => Promise<boolean>;
  onAnswerWaiting: (messageId: string, screen: Screen) => void;
  onPatch: (patch: Plan) => Promise<boolean>;
  onSavePrompt: (text: string, screen?: Screen) => Promise<boolean>;
  onPost: (card: Dict, patch?: Plan, merge?: boolean) => Promise<boolean>;
  onAnalyze: () => Promise<boolean>;
  panelMode: PanelMode;
  onResolve: (
    proposal: Proposal,
    choice: ProposalChoice,
    startRound?: boolean,
    edit?: { field: string; value: string },
  ) => Promise<boolean>;
}) {
  const { caps, session, photo, analysis, busy } = props;
  const pendingPerson = photo.preview_conversation?.question;
  const [confirmationIndices, setConfirmationIndices] = useState<number[]>([]);
  useEffect(() => setConfirmationIndices([]), [pendingPerson?.question_id]);
  const pid = photo.photo_id;
  const messages = (session.messages || []).filter((m) => !m.card?.photo_id || m.card.photo_id === pid);
  // 打開這串對話時最新的那則；之後才有比它新的訊息，才是 AI 這次回的話（打字動畫）。
  const newest = (session.messages || []).reduce((t, m) => Math.max(t, m.created_at), 0);
  const openedAt = useRef<{ id: string; at: number } | null>(null);
  if (openedAt.current?.id !== session.session_id) openedAt.current = { id: session.session_id, at: newest };
  const live = newest > openedAt.current.at;
  const versions = versionsFromMessages(messages, pid);
  const current =
    versions.find((v) => v.version_id === props.selected) ||
    versions.find((v) => v.version_id === photo.active_version?.version_id) ||
    versions[versions.length - 1] ||
    null;
  const thumb = urls.sourceThumb(photo.analyze_job_id);
  const source = urls.sourceImage(photo.analyze_job_id);
  // 手機／直拿平板的大圖檢視：照片左上的版本鈕和對話裡的成品都開這個。
  const [viewing, setViewing] = useState(false);
  const closeViewer = () => setViewing(false);
  const openVersion = (v: Version) => {
    if (v.version_id !== current?.version_id) props.onSelectVersion(v);
    setViewing(true);
  };

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
  // 改比例後重新構圖時還在同一輪裡：面板留著，只是美化要等它做完。
  const reframing = photo.status === "reframing";
  const settled = (ready || reframing) && !!analysis;
  const fresh = settled && readyIdx >= 0 && lastRender < 0;
  const roundOpen = settled && lastAdjust > lastRender && lastAdjust > lastCancel;
  const unchanged =
    roundOpen && !!roundPlan && JSON.stringify(canonical(roundPlan)) === JSON.stringify(snapshotOf(photo.plan));
  // 使用者說「不用了」時 AI 會把回覆標成 close：這一輪什麼都沒改，就收起調整，
  // 回到美化完的樣子。改過的話還有東西等著美化，維持調整中。
  const adjusting = roundOpen && !(unchanged && closedAfter(messages, lastAdjust));
  const editing = fresh || adjusting;
  const roundStart = adjusting ? lastAdjust : readyIdx;
  // 麵包屑要等 adjusting 算出來才知道這張到底算不算做完了。
  const [stepN, stepHow] = stepState(photo, versions, adjusting);
  // 設定 is offered while adjusting, and before the first render only
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
  // 只照需求改的那一版再調整：美化只看個人化需求，人像、畫面、風格、設定
  // 都碰不到結果，所以只給這一項。看的是這一輪開的時候有沒有寫需求，中途清掉
  // 也不會突然冒出其他項目；「幫我分析」開的那輪會把 overwrite_prompt 關掉，
  // 回到完整清單。
  const promptOnlyRound =
    adjusting && !!photo.plan.overwrite_prompt && !!String(roundPlan?.user_prompt || "").trim();
  const cats: Category[] = promptOnlyRound
    ? ["prompt"]
    : adjusting || opened.includes("settings")
      ? [...baseCats, "settings"]
      : baseCats;
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
  // 這一輪寫了（或改了）個人化需求，美化前先問：只照需求改，還是順便整張美化。
  // 記著按的是哪顆美化，問完照那顆的字送出去。
  const [askMode, setAskMode] = useState<{ label: string; icon: string } | null>(null);
  // Bumped when an answer lands at the end of the talk and the timeline should
  // go there, even if the user had scrolled up.
  const [toBottom, setToBottom] = useState(0);
  // 分析或美化一完成（分析後的開場、第 N 版完成），對話捲到這一段的開頭（ChatPanel toStart）。
  const lastDone =
    [...messages].reverse().find((m) => ["analysis_brief", "render_done", "render_failed"].includes(m.card?.type as string))
      ?.id ?? null;
  useEffect(() => {
    setPointed([]);
    setHot({});
  }, [pid]);
  useEffect(() => {
    setConfirmRerun(false);
    setAskMode(null);
  }, [pid, messages.length, messages[messages.length - 1]?.card?.category, unchanged]);
  const promptNow = (photo.plan.user_prompt || "").trim();
  const promptChanged =
    !!promptNow && (fresh || (adjusting && String(roundPlan?.user_prompt || "").trim() !== promptNow));
  // 美化（第一輪、調整後）都走這裡。需求是這一輪新寫的就先問；沒動需求、
  // 上一版又是只照需求改的話，這一輪改的是別的項目，要回到整體美化才改得到。
  const renderRound = (label: string, icon: string) => {
    if (promptChanged) return setAskMode({ label, icon });
    props.onRender(label, icon, photo.plan.overwrite_prompt ? { overwrite_prompt: false } : undefined);
  };

  // 上傳頁面選了「只照需求改」：真的沒有分析，只量了尺寸，美化直接照需求修。
  // 說要分析時（「幫我分析」）才補跑，結果落在 adjust_opened via check 那張卡。
  const skippedAnalysis = analysis ? !!analysis._skipped_analysis : !!photo.analysis_skipped;
  // 沒有分析就沒有 subjects 可指，頭像改跟上傳頁面一樣用本地偵測，從左數來
  // 講位置；送出時帶框和位置（askFaces），不帶 subject_indices。人物預覽開著時
  // 那條路另外處理，這裡不碰。
  const localPick = skippedAnalysis && !caps.features?.subject_preview;
  const scan = useDetectedFaces(localPick ? source : null, async () => {
    const res = await fetch(source);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { blob: await res.blob(), name: "source.jpg" };
  });
  const scanOrder = [...scan.people].sort((a, b) => a.box.x + a.box.w / 2 - (b.box.x + b.box.w / 2));
  const scanWhere = (i: number) => `照片中從左邊數來第 ${scanOrder.findIndex((q) => q.subjectIndex === i) + 1} 位`;
  const people = localPick ? scan.people : facePeople(analysis);
  const analysisThumbs = useFaceThumbs(localPick ? null : analysis, source);
  const scanThumbs = useBoxThumbs(scan.people.map((p) => p.box), source);
  const thumbs = localPick ? scanThumbs : analysisThumbs;
  const askFaces = (si: number[]) =>
    localPick
      ? si.flatMap((i) => {
          const p = scan.people.find((q) => q.subjectIndex === i);
          return p ? [{ box: p.box, where: scanWhere(i) }] : [];
        })
      : undefined;
  // 上傳頁面帶過來的句子，指的人當時還沒有名字：頭像照它帶來的框裁，名字留白，
  // 跟上傳頁面上那顆泡泡長得一樣。
  // 一句可以指好幾位（`faces`）；更早的訊息只帶一個 `box`/`where`。
  const preFacesOf = (card: Dict | null | undefined): { box: Box; thumbnail_box?: Box; preview_person_id?: string; where?: string }[] =>
    Array.isArray(card?.faces) ? card!.faces : card?.box ? [{ box: card.box, where: card.where }] : [];
  const preAsks = messages.filter((m) => m.card?.type === "ask" && preFacesOf(m.card).length);
  const preBoxes = preAsks.flatMap((m) => preFacesOf(m.card));
  const preThumbs = useBoxThumbs(preBoxes.map((f) => {
    const index = f.preview_person_id ? analysis?._preview_person_bindings?.[f.preview_person_id] : undefined;
    const samePerson = Number.isInteger(index) && analysis?.subjects?.[index]?.preview_person_id === f.preview_person_id;
    return (samePerson ? analysis?._subject_thumbnail_boxes?.[String(index)] : undefined) || f.thumbnail_box || f.box;
  }), source);
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
  const faces: Record<number, FaceRef> = {};
  const pickable: (FaceRef & { subjectIndex: number })[] = [];
  people.forEach((p, i) => {
    if (p.subjectIndex < 0 || faces[p.subjectIndex]) return;
    faces[p.subjectIndex] = { name: p.name, thumb: thumbs[i] || "", ...(localPick ? { where: scanWhere(p.subjectIndex) } : {}) };
    pickable.push({ ...faces[p.subjectIndex], subjectIndex: p.subjectIndex });
  });
  // 聊到人的時候用頭像講，不念名字：AI 的話、引導的那幾句裡寫到某人的名字，
  // 就換成那個人的頭像。
  const said = (text: string) => faceText(text, pickable.map((p) => ({ ref: p.name || p.where || "", face: p })));
  // An answer under the AI's question that is just someone's name.
  // 沒分析時頭像沒有名字，AI 拿位置當答案（「左邊那位」），一樣對得回去。
  const faceOption = (o: string) => {
    const want = o.trim().replace(/^照片中/, "");
    return pickable.find((p) => (p.name && p.name === want) || (!!p.where && p.where.replace(/^照片中/, "") === want)) || null;
  };
  // AI 問「哪一位」卻沒附頭像當選項：照片裡有好幾個人時，對話裡就直接排出全部的
  // 頭像給人點，不用再去點輸入框上面那排再按送出。
  const asksWho = (card: Dict | null | undefined) =>
    card?.type === "llm_reply" &&
    !card.options?.length &&
    /[？?]\s*$/.test(card.text || "") &&
    /哪一?位|哪幾位|誰/.test(card.text || "") &&
    pickable.length > 1;

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
    // 那一項的面板還是對話的最後一則才就地閃；後面已經又聊過（例如再加了需求），
    // 就跟點項目按鈕一樣在最底下開一張新的，不跳回上面舊的那張。
    const atEnd = messages.slice(lastOptionsIdx + 1).every((m) => m.card?.auto);
    if (liveCat === cat && atEnd) flash(cat, [field], true);
    else openCategory(cat).then((ok) => ok && flash(cat, [field], true));
  };
  // 掛按鈕的那則訊息裡如果有項目按鈕，「美化」直接接在同一排最後，不另起一行。
  const catChips = (except?: Category | null) => {
    const chips = cats
      .filter((c) => c !== except)
      .map((c) => (
        <Chip label={CATEGORIES[c].label} icon={CATEGORIES[c].icon} disabled={busy} onClick={() => openCategory(c)} />
      ));
    if (rendering !== endIdx || !endRow || placed) return chips;
    placed = true;
    return [...chips, actionChips];
  };
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
            label={`看${termOf(nextCat)}`}
            icon={CATEGORIES[nextCat].icon}
            disabled={busy}
            onClick={() => openCategory(nextCat)}
          />
          <Chip
            label={guideQueue[1] ? `略過，看${termOf(guideQueue[1])}` : "不用看了"}
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
  // 只照需求改完、要看原圖問題：開一輪調整，同時回到整體美化，下一版才會照
  // 分析修。快照在 patch 之前拍，這一輪一開就算「改過」，不會被當成原設定重跑。
  // 上傳時就跳過分析的照片手上沒有分析，要先補跑（onAnalyze），出來後後端貼同一張 check 卡。
  // 訊息列表 render 時就會用到，所以要宣告在列表之前。
  const checkOriginal = () =>
    skippedAnalysis
      ? props.onAnalyze()
      : props.onPost({ type: "adjust_opened", plan: snapshotOf(photo.plan), via: "check" }, { overwrite_prompt: false });

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
  // 頭像已經排在 AI 那句底下可以直接點時，輸入框上面那排就先收起來，免得兩處都能選。
  const facesInline =
    !props.pendingAsk && (asksWho(lastCard) || (lastCard?.type === "llm_reply" && !lastCard.proposal && (lastCard.options || []).some((o: string) => !!faceOption(o))));
  async function send(text: string, si: number[]) {
    if (pendingPerson?.kind === "edit_description" || pendingPerson?.kind === "action") {
      const answer = await props.onConfirmPeople([], undefined, undefined, undefined, undefined, text);
      if (answer !== "new_message") return answer;
    }
    if (pendingProposal && YES.test(text.trim())) {
      await choose(pendingProposal, "apply");
      return true;
    }
    return props.onAsk(text || (si.length > 1 ? "這幾位" : "這位"), si, screen, askFaces(si));
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
        {/* 直接套用之後還是在調整中：跟一般回覆一樣把項目接在底下，不然只剩最後
            那顆「美化」能按。後面跟著的只有套用自己寫進來的紀錄時，這則就是最後一句。 */}
        {c.proposal?.direct &&
          idx + 1 !== guideAt &&
          editing &&
          cats.length > 0 &&
          !props.pendingAsk &&
          messages.slice(idx + 1).every((x) => x.card?.auto) && <div class="chat-choices">{catChips()}</div>}
        {!c.proposal && (() => {
          // 對話不停在一句「再跟我說」。AI 問回來的話，底下是它給的答案，點了就
          // 當成使用者打的下一句送出，指著誰問的就繼續指著誰；答過了定格成紀錄，
          // 點過的那顆標起來。沒問問題時，調整中就把能打開的項目接在底下（AI 的
          // 話會指著它們講）；修完一版時要按的「看美化項目／再美化」由 endActions 接在最後。
          const open = idx === messages.length - 1 && !props.pendingAsk;
          if (c.options?.length > 0) {
            const next = messages[idx + 1];
            const asked = messages[idx - 1]?.card;
            const si = asked?.type === "ask" ? askedIndices(asked) : [];
            // 沒有分析時指的人是框加位置，回答一樣繼續指著那幾位。
            const sf = localPick && asked?.type === "ask" && Array.isArray(asked.faces) ? asked.faces : undefined;
            if (c.lines)
              return (
                <LinesCard
                  lines={c.options as string[]}
                  picked={next?.card?.type === "ask" ? next.text || "" : null}
                  open={open}
                  busy={busy}
                  onPick={(t) => props.onAsk(t, si, screen, sf)}
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
                        title={face.name ? `就是${face.name}` : "就是這位"}
                        onClick={open && !busy ? () => props.onAsk("這位", [face.subjectIndex], screen, askFaces([face.subjectIndex])) : undefined}
                      />
                    );
                  }
                  return (
                    <Chip
                      label={o}
                      kind={!open && next?.card?.type === "ask" && next.text === o ? "picked" : undefined}
                      settled={!open}
                      disabled={busy}
                      onClick={() => props.onAsk(o, si, screen, sf)}
                    />
                  );
                })}
              </div>
            );
          }
          if (asksWho(c)) {
            const next = messages[idx + 1];
            const nextSi = next?.card?.type === "ask" ? askedIndices(next.card) : [];
            return (
              <div class="chat-choices">
                {pickable.map((face) => (
                  <FaceChip
                    face={face}
                    avatarOnly
                    picked={!open && nextSi.length === 1 && nextSi[0] === face.subjectIndex}
                    title={face.name ? `就是${face.name}` : "就是這位"}
                    onClick={open && !busy ? () => props.onAsk("這位", [face.subjectIndex], screen, askFaces([face.subjectIndex])) : undefined}
                  />
                ))}
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

  // 一鍵美化 queues the render before analysis finishes.
  const renderQueued = !!photo.queued_render && (photo.status === "analyzing" || reframing);

  // 這一步能按的「看美化項目／美化」接在最新一則 AI 的話底下，不另外在輸入框上面擺
  // 一排：話講到哪，按鈕就在哪。bar 是這幾顆上的字，問 AI 時一起送過去，它的
  // 回覆才指得出現在能按什麼。
  const enterAdjust = () => props.onPost({ type: "adjust_opened", plan: snapshotOf(photo.plan) });
  let bar: string[] = [];
  let actionChips: ComponentChildren = null;
  // 只照需求改的那一版（或這一輪）：除了改需求、美化，每一步都把「幫我分析」
  // 擺著，讓人知道還可以看整張照片有哪些地方能修。
  const offerAnalyze = editing ? promptOnlyRound : !!photo.plan.overwrite_prompt && !!promptNow;
  const analyzeChip = offerAnalyze ? (
    <Chip label="幫我分析" icon="search" disabled={busy || !ready} onClick={checkOriginal} />
  ) : null;
  if (editing && (photo.status === "ready" || (reframing && !renderQueued))) {
    // 調整中只放美化：項目在對話裡點，「看美化項目」再按一次會把這輪的起點重設掉。
    // 改了還沒美化的提示掛在美化鍵上，不再另外寫一行字。
    const label = fresh && !adjusting && !lastOpened ? "照建議直接美化" : "美化";
    const pending = dirtyCats.length ? `改了${dirtyCats.map((c) => CATEGORIES[c].label).join("、")}，還沒美化` : undefined;
    actionChips = (
      <>
        {analyzeChip}
        <Chip
          label={label}
          icon="spark"
          kind="primary"
          badge={pending}
          disabled={busy || reframing}
          onClick={() => (adjusting && unchanged ? setConfirmRerun(true) : renderRound(label, "spark"))}
        />
      </>
    );
    bar = offerAnalyze ? ["幫我分析", label] : [label];
  } else if (photo.status !== "ready") {
    // 分析中、美化中：處理中那張卡已經講了接下來做什麼，不擺按不下去的鍵。
  } else if (versions.length && current) {
    actionChips = (
      <>
        <Chip label="看美化項目" icon="pencil" disabled={busy || !ready} onClick={enterAdjust} />
        {analyzeChip}
        <Chip label="再美化" icon="redo" kind="primary" disabled={busy} onClick={() => setConfirmRerun(true)} />
      </>
    );
    bar = offerAnalyze ? ["看美化項目", "幫我分析", "再美化"] : ["看美化項目", "再美化"];
  } else if (lastOutcome?.card?.type === "render_failed") {
    actionChips = (
      <>
        <Chip label="看美化項目" icon="pencil" disabled={busy || !ready} onClick={enterAdjust} />
        <Chip label="再試一次" icon="redo" kind="primary" disabled={busy} onClick={() => props.onRender("再試一次", "redo")} />
      </>
    );
    bar = ["看美化項目", "再試一次"];
  }
  // 掛在哪一則：最後一則畫得出來的 AI 訊息。直接套用自己寫進來的紀錄（auto）、
  // 不顯示的摘要都跳過；回答接在分析摘要／成品裡的話，就掛在那則上。
  let endIdx = messages.length - 1;
  while (endIdx >= 0 && (messages[endIdx].card?.auto || SILENT.includes(messages[endIdx].card?.type || ""))) endIdx--;
  const endReply = messages[endIdx]?.card;
  if (endReply?.type === "llm_reply" && endReply.answers && HOSTS.includes(messages[step(endIdx, -1)]?.card?.type || ""))
    endIdx = step(endIdx, -1);
  // 視情況收起來：處理中、AI 正在回、正在問美化方式或要不要照原設定重跑，或
  // AI 剛問了一句、底下已經有它給的答案（提案、選項）-- 先把那件事答完，下一則
  // 話再把按鈕帶回來。
  const endAsks =
    endReply?.type === "llm_reply" &&
    ((!!endReply.proposal && !resolvedChoice[endReply.proposal.id]) || endReply.options?.length > 0);
  const endRow =
    actionChips && !busy && !props.pendingAsk && photo.status === "ready" && !askMode && !confirmRerun && !endAsks && !pendingPerson ? (
      <div class="chat-choices">{actionChips}</div>
    ) : null;
  // 每則 AI 訊息的最後都呼叫一次；只有掛的那則拿得到。quiet：那則自己正在問
  // 別的事（例如要不要幫你分析），這次先不放。都沒掛上的話，列表後面補一則。
  let placed = false;
  let rendering = -1;
  const endActions = (idx: number, quiet = false) => {
    if (idx !== endIdx || !endRow || placed) return null;
    placed = true;
    return quiet ? null : endRow;
  };

  const formalTargetControls = (question: NonNullable<PreviewConversationResult["question"]>, active: boolean) => (
    <div class="chat-target-confirmation"><div class="chat-choices">
      {(!question.kind || question.kind === "person") && <>
        {active && <FacePicker people={pickable} pointed={confirmationIndices} onPoint={setConfirmationIndices} />}
        <Chip label="確認這筆需求的對象" disabled={!active || busy || !confirmationIndices.length} onClick={() => void props.onConfirmPeople(confirmationIndices)} />
        <Chip label="不在已辨識的人物清單中" disabled={!active || busy} onClick={() => void props.onConfirmPeople([], undefined, undefined, undefined, "outside_preview")} />
      </>}
      {question.kind === "semantic" && <Chip label="重新確認這項需求" disabled={!active || busy} onClick={() => void props.onConfirmPeople([])} />}
      {question.kind === "scope" && question.options?.map((label, i) => <Chip label={label} disabled={!active || busy}
        onClick={() => void props.onConfirmPeople([], i === 0 ? "features" : "all")} />)}
      {question.kind === "wish" && question.wish_options?.map(option => <Chip label={option.text} disabled={!active || busy}
        onClick={() => void props.onConfirmPeople([], undefined, option.wish_id)} />)}
      {question.kind === "edit_scope" && question.options?.map((label, i) => <Chip label={label} disabled={!active || busy}
        onClick={() => void props.onConfirmPeople([], undefined, undefined, i === 0 ? "visible_hair" : "cancel")} />)}
    </div></div>
  );
  // 開始美化那顆鍵上現在寫的字（「美化」或「照建議直接美化」），話裡照著念。
  const goLabel = bar.find((b) => b.includes("美化") && !b.includes("項目")) || "美化";
  const items = messages.map((m, idx) => {
    rendering = idx;
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
        // 沒分析的那次只是量完尺寸，沒什麼好報的，直接接著修。
        if (c.skipped) {
          const answer = answerIn(idx);
          return answer ? <AssistantMsg>{answer}{endActions(idx)}</AssistantMsg> : null;
        }
        if (!analysis) return <AssistantMsg><p class="chat-text">分析完成。</p>{endActions(idx)}</AssistantMsg>;
        return (
          <AssistantMsg>
            {skippedAnalysis ? null : brief ? (
              <BriefCard brief={brief} />
            ) : (
              <p class="chat-text">分析完成。</p>
            )}
            {answerIn(idx)}
            {fresh && !holding(idx) && !deferGuide && lastStepIdx < 0 && firstGuide()}
            {endActions(idx)}
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
            ? `先看剛套用的${termOf(cat)}，改動的地方已經標起來了。`
            : `好，已套用。這是更新後的${termOf(cat)}，改動的地方已經標起來了。`;
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
                    onSavePrompt={(t) => props.onSavePrompt(t, screen)}
                  />
                  {fresh ? (
                    idx === lastStepIdx && guideTail({ except: cat })
                  ) : (
                    <>
                      <p class="chat-text">
                        {cats.length > 1 ? "調整好後，可以接著看其他項目，或按下面的「美化」我就開始" : "改好後按下面的「美化」我就開始"}
                        {fromApply && renders ? "，之前的版本會留在美化紀錄" : ""}。
                        {offerAnalyze && "想看整張照片還有哪些地方可以修，也可以按「幫我分析」。"}
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
                        label={`再看${termOf(cat)}`}
                        icon={CATEGORIES[cat].icon}
                        kind="ghost"
                        disabled={busy}
                        onClick={() => openCategory(cat)}
                      />
                    </div>
                  )}
                </>
              )}
              {endActions(idx)}
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
              <ClickBubble icon="arrow" label={`略過${termOf(cat)}`} />
            </UserMsg>
            {fresh && idx === lastStepIdx && (
              <AssistantMsg>
                {guideTail({ lead: "好，" })}
                {endActions(idx)}
              </AssistantMsg>
            )}
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
        const check = c.via === "check";
        const openLine =
          c.via === "proposal"
            ? showChips
              ? "已套用，想直接美化，還是再調下面這幾項？"
              : "已套用，開始新的一輪調整。"
            : check
              ? "好，這是我分析整張照片的結果："
              : "好，下面是目前的美化設定。";
        return (
          <>
            {/* requested：補跑分析等出來的，按「幫我分析」那一下在 analysis_requested 卡上了。 */}
            {c.via !== "proposal" && !c.requested && (
              <UserMsg meta={clockLabel(m.created_at)}>
                <ClickBubble icon={check ? "search" : "pencil"} label={check ? "幫我分析" : "看美化項目"} />
              </UserMsg>
            )}
            <AssistantMsg>
              <p class="chat-text">{openLine}</p>
              {check && brief && <BriefCard brief={brief} />}
              {check && live && <p class="chat-text chat-muted">你的需求會留著，這次美化會照分析一起修，再加上你的需求。</p>}
              {live && (
                <>
                  {/* 再進來的人最容易搞混兩件事：建議是對哪張照片講的、開關又是從哪來的。
                      建議永遠是看原圖分析的，不會因為已經修過一版就換；開關則是上一版的。 */}
                  {c.via !== "proposal" && !check && promptOnlyRound && (
                    <p class="chat-text chat-muted">
                      這版只照你的需求改，所以這輪只要改個人化需求就好。想整張一起美化的話，按「幫我分析」。
                    </p>
                  )}
                  {c.via !== "proposal" && !check && !promptOnlyRound && (
                    <p class="chat-text chat-muted">
                      裡面的項目都是看原圖分析出來的，有小圓點的是建議調整的地方；哪些開著、哪些關著，則沿用上次美化的設定。
                    </p>
                  )}
                  {showChips && c.via !== "proposal" && (
                    <p class="chat-text">
                      {cats.length > 1
                        ? `可以調整${cats.map((x) => CATEGORIES[x].label).join("、")}，想先看哪一項？`
                        : `點下面的「${CATEGORIES[cats[0]].label}」就可以改。`}
                    </p>
                  )}
                  {showChips && (
                    <div class="chat-choices">
                      {catChips()}
                    </div>
                  )}
                  <p class="chat-text chat-muted">
                    改好按下面的「美化」我就開始{n ? "，之前的版本會留在美化紀錄" : ""}。
                  </p>
                </>
              )}
              {endActions(idx)}
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
              {endActions(idx)}
            </AssistantMsg>
          </>
        );
      case "analysis_requested":
        return (
          <UserMsg meta={clockLabel(m.created_at)}>
            <ClickBubble icon="search" label="幫我分析" />
          </UserMsg>
        );
      case "analysis_failed":
        // 補跑的分析失敗：照片還是好的，照需求修的那幾版也都在，再試一次就好。
        if (c.reanalysis)
          return (
            <AssistantMsg>
              <p class="chat-text chat-error">這次分析沒有成功：{jobErrorText(c.error, c.error_code).text}</p>
              <p class="chat-text">
                {c.queued_render_cancelled ? "排定的美化也先取消了。" : ""}照你需求修的版本都還在，可以再試一次分析。
              </p>
              {idx === messages.length - 1 && !busy && (
                <div class="chat-choices">
                  <Chip label="再試一次" icon="search" disabled={!ready} onClick={checkOriginal} />
                </div>
              )}
              {endActions(idx)}
            </AssistantMsg>
          );
        return (
          <AssistantMsg>
            <p class="chat-text chat-error">
              這張照片分析沒有成功：{jobErrorText(c.error, c.error_code).text}
              {c.queued_render_cancelled ? "。排定的美化也一併取消了。" : ""}
            </p>
            {endActions(idx)}
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
        // 最新這版只照需求改：分析找到的那些都還沒動，問要不要攤開來看。
        // 答過（好 → 開一輪調整；不用了 → check_declined）就不再問。上傳時就跳過
        // 分析的照片手上可能還沒有分析，照樣要問（checkOriginal 會先補跑）。這版
        // 之後才補做的分析（自訂模式按了「幫我分析」）也算問過了：摘要接在後面。
        const offerCheck =
          idx === lastRender &&
          !adjusting &&
          !!photo.plan.overwrite_prompt &&
          (skippedAnalysis || !!analysis) &&
          !messages.slice(idx + 1).some((x) => x.card?.type === "check_declined" || x.card?.type === "analysis_brief");
        return (
          <AssistantMsg>
            <ResultCard
              job={versionJob(v)}
              ordinal={v.ordinal}
              failed={(c.failed_job_ids || []).length}
              next={idx === endIdx && !!endRow && !adjusting && !offerCheck}
              note={typeof c.note === "string" ? c.note : undefined}
              onOpen={() => openVersion(v)}
            />
            {answerIn(idx)}
            {offerCheck && (
              <>
                <p class="chat-text">這版只照你的需求改，沒有分析整張照片。需要我幫你分析，看看還有哪些地方可以修嗎？</p>
                <div class="chat-choices">
                  <Chip label="好，幫我分析" icon="search" disabled={busy || !ready} onClick={checkOriginal} />
                  <Chip label="不用了" icon="x" disabled={busy} onClick={() => props.onPost({ type: "check_declined" })} />
                </div>
              </>
            )}
            {endActions(idx, offerCheck)}
          </AssistantMsg>
        );
      }
      case "check_declined": {
        const live = idx === messages.length - 1 && !busy;
        return (
          <>
            <UserMsg meta={clockLabel(m.created_at)}>
              <ClickBubble icon="x" label="不用了" />
            </UserMsg>
            <AssistantMsg>
              <p class="chat-text">好。還有想改的地方，直接輸入告訴我就行；想讓我分析整張照片，也隨時跟我說。</p>
              {live && (
                <div class="chat-choices">
                  <Chip label="幫我分析" icon="search" disabled={!ready} onClick={checkOriginal} />
                </div>
              )}
              {endActions(idx)}
            </AssistantMsg>
          </>
        );
      }
      case "render_failed": {
        const err = jobErrorText(c.error, c.error_code);
        return (
          <AssistantMsg>
            <p class="chat-text chat-error">這次美化沒有成功：{err.text}。</p>
            {/* 被安全審查擋下的，同一張照片再試多半還是一樣。 */}
            {err.safety && idx === messages.length - 1 && !busy && (
              <p class="chat-text">可以回到首頁換一張照片，或按「看美化項目」換個方式再試。</p>
            )}
            {endActions(idx)}
          </AssistantMsg>
        );
      }
      case "reframe_done":
        return (
          <AssistantMsg>
            <p class="chat-text">
              已{reframeBy(c.aspect_ratio)}重新構圖。
              {idx === messages.length - 1 && !isBusy(photo)
                ? editing
                  ? cats.length
                    ? `還想調整其他項目可以點下面的項目，或按「${goLabel}」我就開始。`
                    : `按下面的「${goLabel}」我就開始。`
                  : "按下面的按鈕就會用新構圖美化。"
                : ""}
            </p>
            {/* 調整中構圖好了：跟一般回覆一樣把項目接在底下，美化接在最後。 */}
            {idx === messages.length - 1 && !isBusy(photo) && editing && cats.length > 0 && (
              <div class="chat-choices">{catChips()}</div>
            )}
            {endActions(idx)}
          </AssistantMsg>
        );
      case "reframe_failed":
        return (
          <AssistantMsg>
            <p class="chat-text chat-error">
              {reframeBy(c.aspect_ratio)}重新構圖沒有成功：{jobErrorText(c.error, c.error_code).text}。比例先改回「{aspectLabel(caps, c.previous)}」
              {c.queued_render_cancelled ? "，排定的美化也一併取消了" : ""}。
            </p>
            {idx === messages.length - 1 && !isBusy(photo) && (
              <p class="chat-text">可以再選一次比例試試，或照現在的構圖按下面的按鈕美化。</p>
            )}
            {endActions(idx)}
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
        return (
          <AssistantMsg>
            {replyBody(m, idx)}
            {c.target_question && formalTargetControls(c.target_question, c.target_question.question_id === pendingPerson?.question_id && c.question_revision === photo.preview_conversation?.revision)}
            {endActions(idx)}
          </AssistantMsg>
        );
      case "llm_error":
        return (
          <AssistantMsg>
            <p class="chat-text chat-muted">AI 暫時無法回覆，可以直接點選項調整。</p>
            {endActions(idx)}
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
            {idx === guideAt && (
              <AssistantMsg>
                {firstGuide(lead)}
                {endActions(idx)}
              </AssistantMsg>
            )}
            {dangling && idx !== guideAt && (
              <AssistantMsg>
                <p class="chat-text">
                  {c.choice === "undo"
                    ? "好，已復原。想怎麼改再告訴我。"
                    : c.choice !== "apply"
                    ? "好，這次不套用。想怎麼改再告訴我。"
                    : editing && cats.length
                      ? "好，已套用。想看改了什麼、或接著調整其他項目，點下面的項目；改好按「美化」我就開始。"
                      : "好，已套用。想再改什麼直接告訴我，或按下面的「美化」我就開始。"}
                </p>
                {c.choice === "apply" && editing && !!cats.length && <div class="chat-choices">{catChips()}</div>}
                {endActions(idx)}
              </AssistantMsg>
            )}
          </>
        );
      }
      default:
        return null;
    }
  });
  rendering = -1;

  // First words, before the server's own messages start.
  const intro = (
    <AssistantMsg>
      <p class="chat-text">嗨！先選一張想美化的照片（一次一張）。</p>
    </AssistantMsg>
  );

  // 處理中這一份文案兩邊分工：title 壓在左邊畫布的轉環底下講「在做什麼」，
  // lines 進聊天那張卡，一句句講細節、講過的留著。算在同一個地方，兩邊才不會各講各的。
  // 「只照需求改」上傳那次的 analyzing 只是在量尺寸，對使用者來說已經在修了；
  // stage 跟美化同一個，接下去 rendering 時工作紀錄才是同一段。
  // 只照需求改的那幾句不講光影色彩，見 promptSubMessages。
  const skipping = photo.status === "analyzing" && !!photo.analysis_skipped && !photo.reanalysis;
  const promptOnly = !!photo.plan.overwrite_prompt && !!photo.plan.user_prompt?.trim();
  const busyCopy = skipping
    ? { stage: "rendering", title: "正在調整照片", lines: promptSubMessages() }
    : photo.status === "analyzing"
      ? { stage: "analyzing", title: "正在分析照片", lines: analyzeSubMessages(renderQueued, !!waiting) }
      : reframing
        ? {
            stage: "reframing",
            title: "正在重新構圖",
            lines: [
              renderQueued ? "重新構圖完成後會自動開始美化…" : reframeLoadingMessage(photo.pending_reframe?.aspect_ratio),
            ],
          }
        : photo.status === "rendering"
          ? {
              stage: "rendering",
              title: promptOnly ? "正在調整照片" : "正在美化照片",
              lines: promptOnly ? promptSubMessages() : renderSubMessages(),
            }
          : null;
  const workLog = useWorkLog(busyCopy?.stage ?? null, busyCopy?.lines ?? []);

  let working = busyCopy ? (
    <AssistantMsg>
      <WorkingCard done={workLog.done} now={workLog.now} since={workLog.since} />
    </AssistantMsg>
  ) : null;

  if (askMode && photo.status === "ready" && editing) {
    // 跟上傳頁面那一題同一套字，兩邊才不會各講各的。
    working = (
      <AssistantMsg>
        <p class="chat-text">要整體美化，還是僅依你的需求修改？</p>
        <p class="chat-text chat-muted">
          {WITH_ANALYSIS_LABEL}：照你的需求修改，光線、人像、構圖也一起調整。{ONLY_PROMPT_LABEL}：只動你寫到的地方，其他保持原樣。
        </p>
        {/* 問題的答案直接接在問題底下。 */}
        <div class="chat-choices">
          <Chip label="先不美化" icon="x" disabled={busy} onClick={() => setAskMode(null)} />
          <Chip
            label={WITH_ANALYSIS_LABEL}
            icon={askMode.icon}
            disabled={busy}
            onClick={() => props.onRender(WITH_ANALYSIS_LABEL, askMode.icon, { overwrite_prompt: false })}
          />
          <Chip
            label={ONLY_PROMPT_LABEL}
            icon={askMode.icon}
            disabled={busy}
            onClick={() => props.onRender(ONLY_PROMPT_LABEL, askMode.icon, { overwrite_prompt: true })}
          />
        </div>
      </AssistantMsg>
    );
    bar = ["先不美化", WITH_ANALYSIS_LABEL, ONLY_PROMPT_LABEL];
  } else if (confirmRerun && photo.status === "ready" && adjusting) {
    working = (
      <AssistantMsg>
        <p class="chat-text">這一輪還沒改任何設定，美化會用和上一版相同的設定重新產生，結果可能差不多。要直接美化，還是繼續看美化項目？</p>
        <div class="chat-choices">
          <Chip label="繼續看" icon="pencil" disabled={busy} onClick={() => setConfirmRerun(false)} />
          <Chip label="照原設定美化" icon="spark" kind="primary" disabled={busy} onClick={() => props.onRender("美化", "spark")} />
        </div>
      </AssistantMsg>
    );
    bar = ["繼續看", "照原設定美化"];
  } else if (confirmRerun && photo.status === "ready" && !adjusting) {
    working = (
      <AssistantMsg>
        <p class="chat-text">設定和上一版一樣，再美化一次會用相同的設定重新產生，結果可能和上一版差不多。要直接再美化，還是先看美化項目？</p>
        <div class="chat-choices">
          <Chip label="先看美化項目" icon="pencil" disabled={busy} onClick={enterAdjust} />
          <Chip label="照原設定再美化" icon="redo" kind="primary" disabled={busy} onClick={() => props.onRender("再美化", "redo")} />
        </div>
      </AssistantMsg>
    );
    bar = ["先看美化項目", "照原設定再美化"];
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
                onViewer={() => setViewing(true)}
              />
              <div class="results-analysis-slot" id="results-analysis-slot">
                <SplitGrip />
                {/* 手機上對話那張不放標頭，「對話｜面板」縮小放在把手那排右邊；桌機收起來，用標頭上那顆。 */}
                <ModeSwitch mode={props.panelMode} class="sheet-mode-switch" />
                <ChatPanel
                  sub={sub}
                  panelMode={props.panelMode}
                  toBottom={toBottom}
                  toStart={lastDone}
                  settling={props.analysisPending}
                  live={live}
                  onDropFile={busy ? undefined : props.onFile}
                  footer={
                    <>
                      <FootBar
                        faces={
                          !pendingPerson && !facesInline && pickable.length > 1 && <FacePicker people={pickable} pointed={pointed} onPoint={setPointed} />
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
                  {!placed && endRow && <AssistantMsg>{endRow}</AssistantMsg>}
                  {pendingPerson && !messages.some(m => m.card?.target_question?.question_id === pendingPerson.question_id && m.card?.question_revision === photo.preview_conversation?.revision) &&
                    <AssistantMsg><p class="chat-text">{pendingPerson.text}</p>{formalTargetControls(pendingPerson, true)}</AssistantMsg>}
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
      <VersionViewer
        photo={photo}
        versions={versions}
        current={current}
        open={viewing}
        onClose={closeViewer}
        onSelect={props.onSelectVersion}
      />
    </>
  );
}


// AI 幫照片想的文案：跟畫面建議一樣一列一句，點了就是選這句；最後一列留給自己寫。
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
