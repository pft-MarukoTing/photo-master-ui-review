import { nextDisplayOrder } from "./display-order";
import { sideChoice, type SideAnswer, type SideQuestion } from "./side-reference";
// The chat page: home (intro + gallery), the pre-session chat that picks a
// photo and settings, and a session (compare canvas + chat timeline built
// from the server's messages). Every action is one of the chat API calls;
// typed messages go to /ask, whose replies may carry a plan change to confirm.
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { api, ApiError, setUser, urls, type AskFace, type ReviewTurn, type Screen } from "./api";
import { isHeic, previewBlob, previewBlobs } from "./heic";
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
  flushPromptDraft,
  PROMPT_SAVE_MS,
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
  BatchUploadBubble,
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
import { routeReviewQuestion } from "./review-question-owner";
import { appendNotesHistory, sectionsIdentity, type HistoricalSection } from "./personalized-history";
import type { PreviewClause, PreviewReplyEvent, PreviewWish, PreviewConversationResult, TargetQuestionSnapshot } from "./preview-conversation";
import { submissionPreviewEvents } from "./preview-conversation";
import { Icon } from "./icons";
import { CASE_PREFIX, rowId, ConfirmModal, Gallery, Header, HistoryDrawer, HomeIntro, type GallerySelection, type GalleryTab } from "./home";
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
import { defaultSettings, loadPrefs, PrefsModal, savePrefs, sameSettings, SettingsEditor, settingsChanges, settingsOf, type Prefs, type Settings } from "./settings";
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
  cardAbout,
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
  downloadPhotosZip,
} from "./util";

type Phase = "home" | "chat" | "session";
type PreStep = "start" | "picked" | "settings";
// remote：已經傳上去的照片（它那批的另一個對話）。重新整理後從最近照片接回整批時用，
// 檔案本身已經不在瀏覽器裡了。
// url 是這張的識別（drafts、batchRuns 都拿它當 key），也是大圖和裁人臉用的那張；thumb 是格子用的小圖。
type Source = { kind: "file"; file: File; url: string; thumb?: string } | { kind: "demo"; name: string } | { kind: "remote"; job: string };
// 一次最多幾張，跟後端 MAX_PHOTOS_PER_SESSION 一樣。
const MAX_BATCH = 100;
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
  sideToken?: string;
  verbatim?: boolean;
  interpreted?: boolean;
  literalTexts?: string[];
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
  asked?: { options: string[]; kind?: "person" | "meaning" | "content";
    subtype?: "body_side_reference"; side_question?: SideQuestion };
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
};

// 句子裡指人的那段拿掉，剩下要做的事：「把左側女孩的頭髮染成金髮」→「把頭髮染成金髮」。
function clauseAction(c: PreviewClause, persons: PrePerson[]): string {
  const text = c.display_text || c.text;
  // For object edits referent may name the object, not its owner. Only the
  // selected person's label may be replaced by an avatar in that case.
  const names = [persons.map(p => p.name || p.where).join("、"),
    c.semantic?.target_kind === "object" ? "" : c.referent].filter(Boolean);
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
  return { wish_id: `note-${index}`, text: n.wish ?? n.text, source_text: n.interpreted ? n.wish ?? n.text : n.text,
    ...(n.verbatim ? { verbatim: true } : {}),
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

// 上傳頁／總覽打的一句裡，設定做得到的部分（長寬比、人像修正開關）。
function withReviewSettings(s: Settings, r: { aspect_ratio?: string; advanced_options?: { [key: string]: boolean } }): Settings {
  return {
    ...s,
    ...(r.aspect_ratio ? { aspect_ratio: r.aspect_ratio } : {}),
    ...(r.advanced_options ? { advanced_options: { ...s.advanced_options, ...r.advanced_options } } : {}),
  };
}

// 使用者從清單裡拿掉需求，AI 就回一句。把使用者打的字劃掉是在改畫面，回話才
// 是在對話——上面那句「我記下來了」還在原地，是真的講過的；底下這句接著說現在
// 不算數了，兩句合起來才是完整的經過。
function goneReply(t: { gone: Note[]; left: number }) {
  const what = `「${t.gone[0].dropped?.text || t.gone[0].withdrawalText || wishText(t.gone[0])}」`;
  if (t.gone.length > 1) return "好，個人化需求都清掉了，美化時就不另外加了。";
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

function noteLiteralTexts(ns: Note[], prompt: string): string[] {
  return [...new Set(ns.filter(liveNote).flatMap(n => n.literalTexts || []))].filter(text => prompt.includes(text));
}

function pendingSideNote(ns: Note[]): Note | undefined {
  return [...ns].reverse().find(n => !n.dropped && !n.askedResolved && n.asked?.subtype === "body_side_reference");
}
function sideTokenFor(ns: Note[]): string | undefined {
  return pendingSideNote(ns)?.sideToken || [...ns].reverse().find(n => !n.dropped && n.kept && n.sideToken)?.sideToken;
}

// 上傳頁面的整段對話，給模型看的版本：它才分得出這句是在回答它剛問的，還是另一
// 個新需求。使用者拿掉需求那一輪，模型看到的是 AI 回的那句。
function reviewHistory(ns: Note[], events: PreviewReplyEvent[] = []): ReviewTurn[] {
  const groups: ReviewTurn[][] = [];
  const noteFor = new Map<ReviewTurn, Note>();
  for (const t of conversation(ns, events)) {
    if ("previewReply" in t) {
      if (t.previewReply.kind === "notes") continue; // Display history is not another instruction.
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
// 上傳頁的回覆會指著那頁的「分析」「一鍵美化」鍵（「沒有的話，可以從下面選…」），
// 到了 session 沒有這兩顆，指著它們的那句（連同前面那句「還有其他想改的地方嗎？」）
// 不跟過去；整則只剩那句就回一聲「好。」。
const UPLOAD_BUTTONS_RE = /(還有其他想改的地方嗎？)?[^。！？!?]*(「分析」|「一鍵美化」|分析還是一鍵美化)[^。！？!?]*[。！？!?]?/g;
const carriedReply = (text: string) => text.replace(UPLOAD_BUTTONS_RE, "").trim() || "好。";

function carriedLog(ns: Note[], photoId: string, later: Note | null, events: PreviewReplyEvent[] = []) {
  const out: { role: "user" | "assistant"; text?: string; card: Dict }[] = [];
  const reply = (text: string) =>
    out.push({ role: "assistant", card: { type: "llm_reply", photo_id: photoId, pre: true, text: carriedReply(text) } });
  for (const t of conversation(ns, events)) {
    if ("previewReply" in t) {
      if (t.previewReply.kind === "notes") {
        out.push({ role: "assistant", card: { type: "personalized_snapshot", photo_id: photoId,
          pre: true, snapshot_id: t.previewReply.id, sections: t.previewReply.sections || [] } });
        continue;
      }
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
  return out;
}

// AI 問了還沒人回的那一句：只有對話的最後一輪是它的提問才算——中間拿掉過需求
// 或又講了別的，那個問題就過去了。
function openAsk(ns: Note[]): Note | null {
  const log = conversation(ns);
  const last = log[log.length - 1];
  return last && "ask" in last && last.ask.asked && !last.ask.askedResolved ? last.ask : null;
}

// 送給 preview 對話的 wishes。改過的段整段送一次（在那段第一句的位置）；一句有一
// 部分落在改過的段，就只送其他人的那部分，不然改掉的內容又會從原句跑回來。
function wishesFor(notes: Note[], segEdits: PromptEdits): PreviewWish[] {
  const sections = promptSections(notes, segEdits);
  const editedSent = new Set<string>();
  return notes.flatMap((n, i) => {
    const parts = noteParts(n);
    const out: PreviewWish[] = [];
    for (const part of parts) {
      const edit = segEdits[part.key];
      if (!coveredBy(segEdits, part.key, i) || editedSent.has(part.key)) continue;
      editedSent.add(part.key);
      const sec = sections.findIndex(x => x.key === part.key);
      if (sec >= 0 && edit.text.trim()) out.push(part.key
        ? directWish(`edited-${i}-${sec}`, part.persons, edit.text.trim())
        : { wish_id: `edited-${i}-${sec}`, text: edit.text.trim(), source_text: edit.text.trim(), selection_source: "none", target_ids: [] });
    }
    const wanted = (n.kept === true || n === openAsk(notes) && n.asked?.kind === "person" && !n.asked.options.length) && !n.dropped && (n.resolvedWish !== "" || n.actionPending);
    if (!wanted) return out;
    const rest = parts.filter(x => !coveredBy(segEdits, x.key, i));
    if (rest.length === parts.length) return [...out, previewWish(n, i)];
    return [...out, ...rest.map((x, k) => x.key ? directWish(`note-${i}-part-${k}`, x.persons, x.text)
      : { wish_id: `note-${i}-part-${k}`, text: x.text, source_text: x.text, selection_source: "none" as const, target_ids: [] })];
  });
}

// 多張上傳頁上一張照片按下開始之後的進度；done 是已經送進 session 了。
type BatchRun = StartProgress & { sessionId?: string; photoId?: string; done?: boolean; error?: string };
// 多張上傳頁切到別張時，這張還沒開始的對話先收在這裡，切回來照原樣接上。
type BatchDraft = {
  step: PreStep;
  settingsEvents: PreviewReplyEvent[];
  notesHistory: PreviewReplyEvent[];
  lastSettingsClick: number;
  notes: Note[];
  segEdits: PromptEdits;
  settings: Settings;
  prePointed: number[];
  renderAsk: { later: Note | null; log: Note[]; overwrite?: boolean } | null;
  sourceGeneration: number;
  presentedGeneration: string | null;
  events: PreviewReplyEvent[];
  presentation: string | null;
  peopleReady: boolean;
};

// 多張上傳的總覽頁對話，一輪一筆，照發生的順序記：每次上傳／新增／移除的照片、一起送的需求、
// 開過的設定、一鍵美化時問的那句、一起開始的紀錄、跑完的那一刻。需求本身收進每張自己的草稿，
// 這裡只留畫面上那句話。
type OverviewTurn =
  | { id: number; kind: "upload"; srcs: string[] }
  | { id: number; kind: "remove"; count: number; left: number }
  | { id: number; kind: "running"; keys: string[]; verb: string }
  | { id: number; kind: "done"; rendered: number; analyzed: number; failed: number }
  // 總覽打的一句。AI 先看過才決定要不要發到每一張：pending 還在看；kept 是收下了，
  // line 是它整理過的那句（跟打的不一樣才有）；沒收下（問題、閒聊、撤回）就只有 reply，
  // options 是能點的回答。沒有 kept 的是 AI 沒回應、照原句發出去的。
  // gone 是之後被撤回或被新的一句取代，已經從每張拿掉了。
  | { id: number; kind: "wish"; text: string; count: number; skipped: number;
      pending?: boolean; kept?: boolean; reply?: string; options?: string[]; offer?: boolean; line?: string; gone?: boolean }
  // from 是打開時的設定，之後跟 settings 比，講這一輪改了什麼。
  | { id: number; kind: "settings"; settings?: Settings; from?: Settings; model?: boolean }
  | { id: number; kind: "ask"; overwrite?: boolean; label?: string }
  | { id: number; kind: "start"; mode: "analyze" | "render"; count: number; keys: string[]; overwrite?: boolean; label?: string };
// 總覽裡 AI 提議開始時給的兩個答案，點了就是按總覽的那顆鍵。
// 後端給的是總覽鍵上的名字（「全部分析」「一鍵美化剩餘 N」），舊的對話紀錄裡可能還是單張的名字。
function overviewStartOf(label: string): "analyze" | "render" | undefined {
  if (/^(幫我分析|全部分析|分析剩餘 \d+)$/.test(label)) return "analyze";
  if (/^(一鍵美化|全部一鍵美化|一鍵美化剩餘 \d+)$/.test(label)) return "render";
}
// 總覽說的需求放進每張對話時，AI 在那張裡回的話。
const OVERVIEW_NOTE_REPLY = "好，這句是在總覽說的，這張也會照著修。只想改這張的話，直接在這裡說。";
// 單張對話裡不重播總覽那幾句，連續的收成這一則；說了什麼看底下的需求卡。
const OVERVIEW_SUMMARY = "你在總覽提的需求，這張也會照著修，整理在下面。針對這張的改動，可以在這裡說。";
const fromOverview = (t: Turn) => "ask" in t && t.ask.reply === OVERVIEW_NOTE_REPLY;

// 「我的照片」每張的狀態：開始了照 session 裡真的跑到哪，還沒開始的看有沒有講過需求。
function batchItemState(r: BatchRun | undefined, p: Photo | null, wished: boolean, failed = false): { key: string; text: string } {
  return p
    ? photoState(p, failed)
    : r?.error
      ? { key: "error", text: "失敗" }
      : r?.done
        ? { key: "analyzed", text: "已開始" }
        : r
          ? { key: "uploading", text: "上傳中" }
          : wished
            ? { key: "", text: "有需求" }
            : { key: "", text: "未開始" };
}

// 總覽算進度用的：還沒開始、還在跑（上傳、分析、美化）、分析好、美化好、失敗。
type BatchProgress = "todo" | "running" | "analyzed" | "done" | "error";
type OverviewChips = { pending: boolean; renderable: boolean; labels?: { analyze: string; render: string } };
// 「全部…」只送還沒做過的。一張都還沒做時就是真的全部；有幾張做過了，字面就說是剩下幾張，
// 不會被當成整批重來。
function allLabel(mode: "analyze" | "render", n: number, total: number) {
  if (mode === "analyze") return n >= total ? "全部分析" : `分析剩餘 ${n}`;
  return n >= total ? "全部一鍵美化" : `一鍵美化剩餘 ${n}`;
}

function batchProgress(r: BatchRun | undefined, p: Photo | null, failed = false): BatchProgress {
  if (!r) return "todo";
  if (r.error) return "error";
  if (!r.done || !p) return "running";
  const k = photoState(p, failed).key;
  return k === "processing" || k === "analyzing" ? "running" : k === "error" ? "error" : k === "done" ? "done" : "analyzed";
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

// 上限不先講，選超過了再問（見 askOverLimit）。
const GREETING = "嗨！先選想美化的照片，可以一次選多張。要自己上傳，還是從範例照片挑？";

// 帶著個人化需求按一鍵美化時問的兩條路。僅依需求修改 = overwrite_prompt：修圖
// 只拿個人化需求當指令；整體美化 = 照分析修完，需求一起加上去（原本的做法）。
// 僅依需求修改那條也是 session 裡那張美化卡的標題。按鈕是使用者的話、說明是
// AI 的話，兩邊都不用「我」，主詞才不會打架。說明的順序跟按鈕一樣，整體美化在前。
const ONLY_PROMPT_LABEL = "僅依需求修改";
const WITH_ANALYSIS_LABEL = "整體美化";

// 送出照片時每個請求的時限（單張的 start、多張的 startBatchPhoto）。上傳要等傳完原圖、存進
// S3（後端一次只存一張，同時有別人在傳就得排），給得寬一點。
const STEP_TIMEOUT_MS = 30000;
// 按了「全部…」後兩顆最多定格多久；照片一張張排著傳，抓寬一點。
const ALL_HOLD_MAX_MS = 60000;
const UPLOAD_TIMEOUT_MS = 120000;
// 還沒傳上去就失敗、而且再送一次可能就好的（retriable），自動再試：每次等多久，試完還不行才算失敗。
const UPLOAD_RETRY_DELAYS_MS = [5000, 15000];
// 重試時上一次的上傳後端還在跑（409 upload_in_progress）：隔多久看一次傳好了沒、最多等多久。
const UPLOAD_POLL_MS = 4000;
const UPLOAD_WAIT_MS = 120000;

// 一次送出（含它的自動重試、再按一次）建 session、傳照片都帶的同一把 client_key（api.createSession、
// api.uploadPhoto）：上一次其實已經建好、傳好的，後端會認出來，不會多一段對話、多傳一張。
const newClientKey = () => `k_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

// 後端收到了照片但沒能加進 session（回 200 卻沒有 photos，多半是存 S3 失敗）。
class UploadFailed extends Error {}

// 再送一次可能就好的錯：逾時、連不上、伺服器出錯、上一次還在傳。其他 4xx 是請求本身的問題，再送也一樣。
function retriable(e: unknown): boolean {
  if (e instanceof ApiError) return e.status >= 500 || e.body?.error === "upload_in_progress";
  return e instanceof UploadFailed || e instanceof TypeError || (e instanceof DOMException && e.name === "TimeoutError");
}

type UploadArgs = Parameters<typeof api.uploadPhoto>;

// 帶著 clientKey 傳一張，回後端加進 session 的那張。重試時上一次的還在傳，就等它傳好拿它那張；
// 等太久當逾時，交給下一次重試。live() 變成 false 就不等了。
async function uploadKeyed(id: string, blob: Blob, name: string, preview: UploadArgs[3], wishes: UploadArgs[4],
  clientKey: string, live: () => boolean, sideToken?: string): Promise<{ photo_id: string; analyze_job_id: string }> {
  try {
    const up = await api.uploadPhoto(id, blob, name, preview, wishes, { signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS), clientKey, sideToken });
    const added = up.photos?.[0];
    if (!added) throw new UploadFailed(up.errors?.[0]?.error || "上傳失敗");
    return added as { photo_id: string; analyze_job_id: string };
  } catch (e) {
    if (!(e instanceof ApiError && e.body?.error === "upload_in_progress")) throw e;
  }
  const until = Date.now() + UPLOAD_WAIT_MS;
  while (Date.now() < until && live()) {
    await wait(UPLOAD_POLL_MS);
    const s = await api.getSession(id, 1, AbortSignal.timeout(STEP_TIMEOUT_MS));
    const found = s.photos.find((x) => x.client_key === clientKey);
    if (found) return found;
  }
  throw new DOMException("upload still in progress", "TimeoutError");
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409 && ["unresolved_people", "unresolved_visual_target", "protected_scope"].includes(e.body?.reason))
      return notice(e.message);
    if (e.body?.error === "photos_not_ready") return "照片還在處理中，請稍候再試。";
    return e.message;
  }
  if (e instanceof DOMException && e.name === "TimeoutError") return "系統回應逾時，請再試一次。";
  // fetch 連不上時瀏覽器丟的 TypeError（Chrome／Firefox／Safari 各自的字樣），別把英文原文秀出去。
  if (e instanceof TypeError && /Failed to fetch|NetworkError|Load failed/i.test(e.message))
    return "連不上伺服器，請確認網路後再試一次。";
  return e instanceof Error ? e.message : String(e);
}

function canShowTargetQuestion(photo: Photo): boolean {
  return photo.status === "ready" && !photo.queued_render && !photo.pending_render;
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
  return s.kind === "file" ? s.url : s.kind === "remote" ? urls.sourceThumb(s.job) : urls.demoThumb(s.name);
}

// 縮圖格子、上傳泡泡這些小圖用的：<img> 不管畫多小都會解成原尺寸，多張時不能每格都放原圖。
function thumbSrc(s: Source) {
  return s.kind === "file" ? s.thumb || s.url : sourceSrc(s);
}

function fileSource(file: File, preview: { full: Blob; thumb: Blob | null }): Source {
  return { kind: "file", file, url: URL.createObjectURL(preview.full), thumb: preview.thumb ? URL.createObjectURL(preview.thumb) : undefined };
}

// 一次選好幾張時打頭的那張：瀏覽器自己畫得出來的直接用原檔，不等縮小圖，選完馬上有畫面；
// 只有一格放原圖，不會像整批都放原圖那樣吃記憶體。HEIC 畫不出來，還是得先解碼。
async function firstSource(file: File): Promise<Source> {
  return fileSource(file, isHeic(file) ? await previewBlobs(file) : { full: file, thumb: null });
}

function dropSource(s: Source) {
  if (s.kind !== "file") return;
  URL.revokeObjectURL(s.url);
  if (s.thumb) URL.revokeObjectURL(s.thumb);
}

function fullSrc(s: Source) {
  return s.kind === "file" ? s.url : s.kind === "remote" ? urls.sourceImage(s.job) : urls.demoImage(s.name);
}

// 要上傳的檔案：自己選的直接用，範例和已經在伺服器上的去抓。
async function sourceBlob(s: Source, signal?: AbortSignal): Promise<{ blob: Blob; name: string }> {
  if (s.kind === "file") return { blob: s.file, name: s.file.name };
  const res = await fetch(fullSrc(s), { signal });
  if (!res.ok) throw new Error(`無法讀取範例照片（HTTP ${res.status}）`);
  return { blob: await res.blob(), name: s.kind === "demo" ? s.name : `${s.job}.jpg` };
}

function activePhoto(s: Session | null): Photo | null {
  if (!s || !s.photos.length) return null;
  return s.photos.find((p) => p.photo_id === s.active_photo_id) || s.photos[0];
}

// 這張最後一次美化沒有成功：失敗只記在對話裡（render_failed），照片本身還是 ready，
// 跟結果頁一樣看最後一則美化結果。
function renderFailedIn(s: Session, photoId: string): boolean {
  const last = [...(s.messages || [])].reverse().find((m) =>
    (m.card?.type === "render_done" || m.card?.type === "render_failed") && (!m.card.photo_id || m.card.photo_id === photoId));
  return last?.card?.type === "render_failed";
}

const isBusy = (p: Photo) => p.status === "analyzing" || p.status === "reframing" || p.status === "rendering";
// 分析放在哪支 job：分析那支，或後來改比例時重新構圖的那支。
const analysisJob = (p: Photo) => p.analysis_job_id || p.analyze_job_id;

export function App() {
  const drawerLayout = useMedia(DRAWER_LAYOUT);
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
  // 從最近照片打開一批時，左上角「我的照片」跳一下提示其他張在那裡；跳完就清掉，之後在同一批裡切換照片不再跳。
  const [hopTrigger, setHopTrigger] = useState(false);
  const [revealDrawer, setRevealDrawer] = useState(0);

  // Pre-session chat
  const [step, setStep] = useState<PreStep>("start");
  const [settingsEvents, setSettingsEvents] = useState<PreviewReplyEvent[]>([]);
  const [notesHistory, setNotesHistory] = useState<PreviewReplyEvent[]>([]);
  const lastSettingsClick = useRef(-1);
  const [via, setVia] = useState<"upload" | "sample" | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  // 一次選了好幾張：不偵測人臉、不收需求，只讓使用者挑「分析」或「一鍵美化」
  // 套到全部。單張還是走 source 那條（有人物和需求）。
  const [batch, setBatch] = useState<Source[] | null>(null);
  const batchRef = useRef(batch);
  batchRef.current = batch;
  // 選了的照片裡，還在讀縮圖、還沒放進多張頁的張數（見 addAsLoaded）。
  const batchLoading = useRef(0);
  // 正在讀的那一輪：總覽那筆上傳的 id、讀好幾張、一共幾張。讀完之前多張頁上什麼都不能按。
  const [photoLoad, setPhotoLoad] = useState<{ entry: number; done: number; total: number } | null>(null);
  const loadingPhotos = !!photoLoad;
  // 選了好幾張、打頭那張是 HEIC 還在解碼，多張頁還開不出來：先浮一句「正在讀取 N 張照片…」。
  const [readingFirst, setReadingFirst] = useState(0);
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
  // session 裡正在看哪張；null 就照 activePhoto 挑。
  const [activeId, setActiveId] = useState<string | null>(null);
  const [pendingAsk, setPendingAsk] = useState<{ photoId: string; text: string; subjectIndices: number[] } | null>(null);

  const [submitting, setSubmitting] = useState<{ sources: Source[]; mode: "analyze" | "render"; since: number } | null>(null);
  const submissionLock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // ── 多張上傳頁 ──
  // 每張一段自己的上傳頁對話；切到別張時，這張的狀態收進 drafts（以 sourceSrc 當
  // key），切回來再放回 state。source 是 null 就是在看「全部」。
  const drafts = useRef<Record<string, BatchDraft>>({});
  // 「全部」那頁的設定：還沒開過的照片從這裡起跳，改了就套到還沒開始的每一張。
  const [allSettings, setAllSettings] = useState<Settings | null>(null);
  const [batchRuns, setBatchRuns] = useState<Record<string, BatchRun>>({});
  // 已經開始的那幾張在背景跑到哪：結果頁只盯著正在看的那張，其他張另外定時問。
  const [batchLive, setBatchLive] = useState<Record<string, Photo>>({});
  // 每張最後一次美化有沒有失敗，跟 batchLive 一起從 session 抓回來時更新。
  const [batchFailed, setBatchFailed] = useState<Record<string, boolean>>({});
  // 按了全部一鍵美化、美化請求還沒回來的那幾張。手上的照片狀態還是舊的（分析好、沒在美化），
  // 不先記著，「一鍵美化剩餘 N」會跟著請求一張張回來慢慢往下數。
  const [renderSent, setRenderSent] = useState<ReadonlySet<string>>(new Set());
  // 剛按了「全部…」：送出的那幾張還沒全部有著落（照片狀態問到了、美化請求回來了）之前，
  // 兩顆「全部…」停在按下去時的字、不能按，等一起有結果再換成新的（通常就收掉了）。
  const [allHold, setAllHold] = useState<{ keys: string[]; pending: number; renderable: number } | null>(null);
  // 一張一張排著傳，全部一起按時不會同時塞好幾張給後端。每張各自一段 session。
  const batchQueue = useRef<Promise<unknown>>(Promise.resolve());
  // 每次重來（清掉、換一批、離開）加一，舊的那批還在跑的回呼就不再動畫面。
  const batchEpoch = useRef(0);
  // 這一批的 id：每張建自己的 session 時帶上，最近照片才會把它們合成一格。
  const batchId = useRef<string | null>(null);
  // 每張照片的 client_key（newClientKey）：自動重試、再按一次都帶同一把。
  // 單張的放 singleKey：同一張失敗後再按一次沿用，傳好了就清掉，之後再傳同一張是新的一段。
  const batchKeys = useRef<Record<string, string>>({});
  const singleKey = useRef<{ source: unknown; key: string } | null>(null);
  // 切照片時放回草稿：preFull 換了的那一次，不要把剛放回去的需求和人當成換照片清掉。
  const restoring = useRef<string | null>(null);
  const gen = useRef(0);
  // 進 session 時要停在哪張。
  const pendingActive = useRef<string | null>(null);
  // 剛按開始的那張：傳好就打開它的結果頁。中途切到別張就不跳了。
  const follow = useRef<string | null>(null);
  const batchKey = source ? sourceSrc(source) : null;
  // 總覽頁（多張、沒有打開哪一張）自己的對話：送出的共用需求、開過的設定、一起開始的紀錄。
  const [overview, setOverview] = useState<OverviewTurn[]>([]);
  // 總覽最後那則之後的下一步：那則一出現就照當下的進度定下來，之後照片跑完幾張也不改字，
  // 講過的話不會變；有新的進度（開始了、跑完了）會是新的一則，再講新的一句。
  // 桌機上那則底下放了「全部…」那排鍵時，連當時有哪幾顆一起記下來：那則之後不是最新的了，
  // 鍵還在原處當紀錄，只是不能再按。
  const turnHints = useRef<Record<string, { text: string; chips: OverviewChips | null; pending?: number; renderable?: boolean }>>({});
  // 這一輪總覽設定裡，選了「保留自己的」那幾張：同一輪再改就不再問。
  const keepOwn = useRef(new Set<string>());
  // 開始過、還沒美化的照片在總覽收到一句需求後，下次一鍵美化跟還沒開始的一樣先問怎麼套用：
  // liveAsk 是還沒問的，liveMode 是問過、美化前要寫進 plan 的那個選擇。
  const liveAsk = useRef(new Set<string>());
  const liveMode = useRef<Record<string, boolean>>({});

  // ── The upload page's own face picker ─────────────────────────────────────
  // A separate worker preview starts on selection; feature-off retains the
  // original local-detector path. Neither creates a formal Chat session yet.
  const preFull = !source ? "" : fullSrc(source);
  const previewEnabled = !!caps?.features?.subject_preview;
  const loadSource = async () => {
    if (!source) throw new Error("no source");
    return sourceBlob(source);
  };
  const preview = useSubjectPreview(source && phase === "chat" && previewEnabled && !submitting ? preFull : null,
    settings?.analysis_model || caps?.render.defaults.analysis_model || "", loadSource);
  const previewReviewIdentity = useRef(preview.identity);
  previewReviewIdentity.current = preview.identity;
  const selectionIdentity = `${preFull}#${settings?.analysis_model || caps?.render.defaults.analysis_model || ""}`;
  const sourceIdentity = useRef(selectionIdentity);
  sourceIdentity.current = selectionIdentity;
  const reviewedSource = useRef(preFull);
  useEffect(() => {
    if (restoring.current !== null && restoring.current === preFull) {
      restoring.current = null;
      reviewedSource.current = preFull;
      return;
    }
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
  const historySections = preSections.map(s => ({ key: s.key, line: sectionLine(s), text: s.text, persons: s.persons }));
  const historySectionsKey = sectionsIdentity(historySections);
  useEffect(() => {
    if (phase !== "chat" || !source || preAsk) return;
    setNotesHistory(events => appendNotesHistory(events, historySections, nextDisplayOrder(), conversation(notes).length));
  }, [historySectionsKey, !!preAsk, phase]);
  const previewWishes = wishesFor(notes, segEdits);
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
        if (n.asked?.kind === "person" && !n.asked.options.length && !n.askedResolved && clauses.length &&
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
      setHopTrigger(true);
      // 桌機那顆藏著、或系統設定減少動態時不會有 animationend，時間到一樣清掉。
      window.setTimeout(() => setHopTrigger(false), 3500);
      window.scrollTo(0, 0);
      if (s.batch_id) void rejoinBatch(s);
    } catch (e) {
      setSessionHash(null);
      if (phase === "session") goHome();
      setError(errorText(e));
    }
  }

  // 從最近照片（或重新整理後）打開多張上傳裡的一張：同一批的其他張接回左邊，點了
  // 就到它的結果頁。頁面上那批本來就在（從它自己的左邊點過來）就不用；那批還有沒
  // 開始或還在傳的照片時也不動，免得把它們清掉。
  async function rejoinBatch(s: Session) {
    const runs = batchRunsRef.current;
    if (Object.values(runs).some((r) => r.sessionId === s.session_id)) return;
    if (batch && batch.some((x) => !runs[sourceSrc(x)]?.done && !runs[sourceSrc(x)]?.error)) return;
    const epoch = ++batchEpoch.current;
    const ids = (await refreshSessions()).find((r) => r.session_ids?.includes(s.session_id))?.session_ids;
    if (!ids || epoch !== batchEpoch.current) return;
    const all = await Promise.all(ids.map((id) => (id === s.session_id ? s : api.getSession(id).catch(() => null))));
    if (epoch !== batchEpoch.current) return;
    const sources: Source[] = [];
    const nextRuns: Record<string, BatchRun> = {};
    const live: Record<string, Photo> = {};
    const failed: Record<string, boolean> = {};
    for (const x of all) {
      const p = x && activePhoto(x);
      if (!x || !p) continue;
      const src: Source = { kind: "remote", job: p.analyze_job_id };
      const key = sourceSrc(src);
      sources.push(src);
      nextRuns[key] = { mode: p.active_version ? "render" : "analyze", since: 0, lines: [], sessionId: x.session_id, photoId: p.photo_id, done: true };
      live[key] = p;
      failed[key] = renderFailedIn(x, p.photo_id);
    }
    if (sources.length < 2) return;
    drafts.current = {};
    batchQueue.current = Promise.resolve();
    batchKeys.current = {};
    batchId.current = s.batch_id ?? null;
    follow.current = null;
    setBatch(sources);
    setBatchRuns(nextRuns);
    setBatchLive(live);
    setBatchFailed(failed);
    setRenderSent(new Set());
    setAllHold(null);
    // 總覽的「設定」從這份開：照還沒美化的那張現在的設定，都美化過了就用預設。沒有這份，「設定」按了不會有反應。
    const open = Object.values(live).find((p) => !p.active_version);
    const c = capsRef.current;
    const base = open ? settingsOf(open.plan) : c ? defaultSettings(c, prefs || undefined) : null;
    if (base) {
      setAllSettings(base);
      setSettings(base);
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
  // 多張上傳裡已經開始的照片各有自己的對話；不管在總覽還是結果頁，那批裡還沒開始的才會掉。
  const parkedLeft = batch ? batch.filter((s) => !batchRuns[sourceSrc(s)]).length : 0;
  const unsentHome =
    phase === "session" ? parkedLeft > 0 : batch ? parkedLeft > 0 || notes.length > 0 : !!source || notes.length > 0;
  function confirmLeave(where: string): Promise<boolean> {
    if (!unsentHome) return Promise.resolve(true);
    const what = parkedLeft
      ? `還有 ${parkedLeft} 張照片`
      : notes.length ? "剛剛選的照片和打的需求" : "剛剛選的照片";
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
      message: parkedLeft
        ? `要開新對話處理另一張照片嗎？已經開始的照片可以從「最近照片」再打開，還沒開始的 ${parkedLeft} 張會清掉。`
        : "要開新對話處理另一張照片嗎？目前這段對話可以從「最近照片」再打開。",
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

  const photo = session?.photos.find((p) => p.photo_id === activeId) || activePhoto(session);
  const sessionId = session?.session_id;
  useEffect(() => {
    setActiveId(pendingActive.current);
    pendingActive.current = null;
  }, [sessionId]);
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
    setNotesHistory([]);
    lastSettingsClick.current = -1;
    setVia(null);
    setSource(null);
    setBatch(null);
    setPhotoLoad(null);
    setNotes([]);
    preDraft.current = "";
    setSegEdits({});
    setPrePointed([]);
    setPreAsk(null);
    setRenderAsk(null);
    setSamplesOpen(false);
    if (caps) setSettings(defaultSettings(caps, prefs || undefined));
    drafts.current = {};
    setBatchRuns({});
    setBatchLive({});
    setBatchFailed({});
    setRenderSent(new Set());
    setAllHold(null);
    setAllSettings(null);
    setOverview([]);
    keepOwn.current = new Set();
    batchQueue.current = Promise.resolve();
    batchKeys.current = {};
    batchEpoch.current++;
    batchId.current = null;
    follow.current = null;
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
    // 合成一格的那一批，刪的是整批的對話。
    if (ids !== "all") ids = ids.flatMap((id) => sessions?.find((r) => rowId(r) === id)?.session_ids ?? [id]);
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
    setNotesHistory([]);
    lastSettingsClick.current = -1;
    setSourceGeneration(n => n + 1);
    ++lastPick.current; // a demo pick also supersedes an in-flight HEIC decode
    setSamplesOpen(false);
    setSource(s);
    setBatch(null);
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
    if (!(await confirmSwap())) {
      dropSource(s);
      return;
    }
    setNotes([]);
    setSegEdits({});
    setPreAsk(null);
    setPreClear((n) => n + 1);
    if (caps) setSettings(defaultSettings(caps, prefs || undefined));
    choose(s);
  }

  // 換掉上傳頁上的照片前問一聲；沒打過字、沒改過設定就不用問。
  function confirmSwap(): Promise<boolean> {
    const typed = notes.length > 0 || Object.keys(segEdits).length > 0 || !!preAsk || !!preDraft.current.trim();
    const tuned = !!caps && !!settings && !sameSettings(settings, defaultSettings(caps, prefs || undefined));
    if (!(typed || tuned)) return Promise.resolve(true);
    return new Promise<boolean>((resolve) =>
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
  }

  // 選的張數超過還放得下的：問要先放最前面那幾張，還是重新選。重新選就什麼都不做，讓使用者再選一次。
  function askOverLimit(message: string, confirmLabel: string): Promise<boolean> {
    return new Promise<boolean>((resolve) =>
      setLeaveAsk({
        message,
        confirmLabel,
        cancelLabel: "重新選",
        answer: (yes) => {
          setLeaveAsk(null);
          resolve(yes);
        },
      }),
    );
  }

  // 一次選了好幾張。只有一張就照單張走（有人物、有需求）；兩張以上開一頁
  // 多張的上傳頁，只問要分析還是一鍵美化。
  async function pickFiles(files: File[]) {
    const images = files.filter((f) => f.type.startsWith("image/") || isHeic(f));
    if (images.length <= 1) return pickFile(images[0] || files[0]);
    if (
      images.length > MAX_BATCH &&
      !(await askOverLimit(
        `你選了 ${images.length} 張照片，一次最多 ${MAX_BATCH} 張。要先上傳最前面的 ${MAX_BATCH} 張，還是重新選？`,
        `上傳前 ${MAX_BATCH} 張`,
      ))
    ) return;
    const pick = ++lastPick.current;
    const taken = images.slice(0, MAX_BATCH);
    // 第一張好了就開多張頁，其他的讀好一張放一張，不等全部讀完才一次出現；還沒好的先放佔位格。
    const first = await readFirst(taken, pick);
    if (pick !== lastPick.current) return dropSource(first);
    const entry = await startBatch(
      [first],
      images.length < files.length
          ? notice(`有 ${files.length - images.length} 個檔案不是圖片，先略過。`)
          : "",
      taken.length,
    );
    if (entry !== null) await addAsLoaded(taken.slice(1), entry);
  }

  // 讀打頭那張；要解碼（HEIC）的那段先浮一句提示，一般照片瞬間就好，不閃那一下。
  // 誰掛上的誰收：中途又選了一批 HEIC，提示留給新的那批。
  const readingPick = useRef(0);
  async function readFirst(taken: File[], pick: number): Promise<Source> {
    if (isHeic(taken[0])) {
      readingPick.current = pick;
      setReadingFirst(taken.length);
    }
    try {
      return await firstSource(taken[0]);
    } finally {
      if (readingPick.current === pick) setReadingFirst(0);
    }
  }

  // 剩下的照片一張一張讀縮圖，讀好就接到多張頁後面，總覽那筆上傳的張數跟著長。
  // 中途換了另一批、整批清掉、回到單張、或已經選滿就停，沒放進去的不留。
  async function addAsLoaded(files: File[], entry: number) {
    const bid = batchId.current;
    let left = files.length;
    batchLoading.current += left;
    try {
      for (const f of files) {
        const preview = await previewBlobs(f);
        const b = batchRef.current;
        if (batchId.current !== bid || !b || b.length >= MAX_BATCH) return;
        const s = fileSource(f, preview);
        left--;
        batchLoading.current--;
        setBatch((cur) => (cur ? [...cur, s] : cur));
        setOverview((t) => t.map((x) => (x.id === entry && x.kind === "upload" ? { ...x, srcs: [...x.srcs, sourceSrc(s)] } : x)));
        setPhotoLoad((p) => (p?.entry === entry ? { ...p, done: p.done + 1 } : p));
      }
    } finally {
      batchLoading.current -= left;
      setPhotoLoad((p) => (p?.entry === entry ? null : p));
    }
  }

  // 範例照片彈窗送出來的：一張照單張走（換掉上傳頁上的照片），兩張以上跟上傳多張一樣開多張頁。
  function pickSamples(names: string[]) {
    if (batch) return appendSamples(names);
    if (names.length <= 1) return names[0] ? swapSource({ kind: "demo", name: names[0] }) : setSamplesOpen(false);
    setSamplesOpen(false);
    ++lastPick.current; // 蓋過還在解碼的上傳
    void startBatch(names.slice(0, MAX_BATCH).map((name) => ({ kind: "demo", name })), "");
  }

  // 多張頁上從範例照片再挑幾張：接在後面，已經在裡面的不重複加。
  function appendSamples(names: string[]) {
    setSamplesOpen(false);
    if (!batch) return;
    const have = new Set(batch.map(sourceSrc));
    const added: Source[] = names.map((name): Source => ({ kind: "demo", name }))
      .filter((s) => !have.has(sourceSrc(s))).slice(0, Math.max(0, MAX_BATCH - batch.length));
    if (added.length) {
      setBatch((b) => (b ? [...b, ...added] : b));
      logUpload(added);
    }
    setError(batch.length >= MAX_BATCH && names.length
      ? notice(`已經選滿 ${MAX_BATCH} 張了。想新增照片的話，建議先移除幾張，或按「全部清除」重新選，再新增其他照片。`) : "");
  }

  // 開多張的上傳頁，上傳多張和範例照片多選共用。message 是開好之後要提醒的話。
  // 開好了回總覽那筆上傳的 id（之後讀好的照片接在它裡面），沒開（使用者取消）回 null。
  // total 是這次一共選了幾張，比 sources 多就是後面還在讀，先記成讀取中。
  async function startBatch(sources: Source[], message: string, total = sources.length): Promise<number | null> {
    if (phase === "chat" && !(await confirmSwap())) {
      for (const s of sources) dropSource(s);
      return null;
    }
    setSession(null);
    setSessionHash(null);
    resetPre();
    setPreClear((n) => n + 1);
    setPhase("chat");
    setBatch(sources);
    batchId.current = `b_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    // 一進來先到總覽：可以全部一起開始、一起改設定、講一次套到每張；要個別調整再點那張。
    const base = caps ? defaultSettings(caps, prefs || undefined) : settings;
    if (base) {
      setAllSettings(base);
      applyDraft(null, null);
      setSettings(base);
    }
    const entry = nextDisplayOrder();
    setOverview([{ id: entry, kind: "upload", srcs: sources.map(sourceSrc) }]);
    if (total > sources.length) setPhotoLoad({ entry, done: sources.length, total });
    keepOwn.current = new Set();
    setError(message);
    window.scrollTo(0, 0);
    return entry;
  }

  // 多張上傳頁上的 ×：拿掉一張。剩一張就回單張的流程（有人物、有需求）。
  // 已經開始的那張拿不掉：它已經在對話裡了。
  function removeFromBatch(i: number) {
    if (!batch || busy || submitting || loadingPhotos) return;
    const gone = batch[i];
    if (!gone) return;
    const key = sourceSrc(gone);
    if (batchRuns[key]) return;
    const rest = batch.filter((_, j) => j !== i);
    delete drafts.current[key];
    dropSource(gone);
    // 後面還有照片在讀的話不回單張：讀好的會接著放進來。
    if (rest.length === 1 && !Object.keys(batchRuns).length && !batchLoading.current) {
      // 剩一張就回單張：它在多張頁上講過的話留著。
      const restKey = sourceSrc(rest[0]);
      if (restKey === batchKey) setBatch(null);
      else if (drafts.current[restKey]) {
        applyDraft(rest[0], drafts.current[restKey]);
        setBatch(null);
      } else choose(rest[0]);
    } else {
      // 拿掉的是畫面上這張，就回總覽。
      if (key === batchKey) applyDraft(null, null);
      setBatch(rest);
      // 連著移除好幾張併成一句。
      setOverview((t) => {
        const last = t[t.length - 1];
        return last?.kind === "remove"
          ? [...t.slice(0, -1), { ...last, count: last.count + 1, left: rest.length }]
          : [...t, { id: nextDisplayOrder(), kind: "remove", count: 1, left: rest.length }];
      });
    }
    setError("");
  }

  // 總覽記一筆新增的照片，一次加的算一輪，不去改最早那次上傳的張數。
  function logUpload(added: Source[]): number | null {
    if (!added.length) return null;
    const id = nextDisplayOrder();
    setOverview((t) => [...t, { id, kind: "upload", srcs: added.map(sourceSrc) }]);
    return id;
  }

  // 多張上傳頁上的「全部清除」：先問一聲，清掉就回到還沒選照片的上傳頁。
  function clearBatch() {
    if (!batch || busy || submitting || loadingPhotos) return;
    const n = batch.length;
    setLeaveAsk({
      message: `確定要清除這 ${n} 張照片嗎？`,
      confirmLabel: "清除",
      cancelLabel: "取消",
      answer: (yes) => {
        setLeaveAsk(null);
        if (!yes) return;
        for (const s of batch) dropSource(s);
        resetPre();
        setError("");
      },
    });
  }

  // 多張時對話框的上傳鈕和拖進來的照片：接在原本那幾張後面，不取代。
  async function appendFiles(files: File[]) {
    if (!batch) return pickFiles(files);
    if (loadingPhotos) return;
    const images = files.filter((f) => f.type.startsWith("image/") || isHeic(f));
    // 上一批還在讀的也算進去，不然兩批加起來會超過上限。
    const room = MAX_BATCH - batch.length - batchLoading.current;
    if (
      room > 0 &&
      images.length > room &&
      !(await askOverLimit(
        `你選了 ${images.length} 張照片，一次最多 ${MAX_BATCH} 張，現在還能再加 ${room} 張。要先加最前面的 ${room} 張，還是重新選？`,
        `加前 ${room} 張`,
      ))
    ) return;
    // 問的時候整批被清掉了就不加。
    if (!batchRef.current) return;
    const taken = images.slice(0, Math.max(0, room));
    const pick = ++lastPick.current;
    // 跟 pickFiles 一樣：讀好第一張就放上去，其他的讀好一張放一張。
    let entry: number | null = null;
    if (taken.length) {
      const s = await readFirst(taken, pick);
      if (pick !== lastPick.current) return dropSource(s);
      setBatch((b) => (b ? [...b, s] : b));
      entry = logUpload([s]);
      if (entry !== null && taken.length > 1) setPhotoLoad({ entry, done: 1, total: taken.length });
    }
    setError(
      images.length && !taken.length
        ? notice(`已經選滿 ${MAX_BATCH} 張了。想新增照片的話，建議先移除幾張，或按「全部清除」重新選，再新增其他照片。`)
        : images.length < files.length
          ? notice(`有 ${files.length - images.length} 個檔案不是圖片，先略過。`)
          : "",
    );
    if (entry !== null) await addAsLoaded(taken.slice(1), entry);
  }

  // ── 多張上傳頁：一張一段對話 ──
  const batchRunsRef = useRef(batchRuns);
  batchRunsRef.current = batchRuns;
  const unstarted = (key: string) => {
    const r = batchRunsRef.current[key];
    return !r || !!r.error;
  };
  // 開始過、照片已經進了 session、還能一鍵美化的那張（分析好的、還在分析的都算；正在看的那張
  // 用結果頁上的，其他張用背景問到的）。
  const livePhoto = (key: string): Photo | null => {
    const r = batchRuns[key];
    if (!r?.done || !r.sessionId || !r.photoId) return null;
    return r.sessionId === session?.session_id
      ? session.photos.find((x) => x.photo_id === r.photoId) || null : batchLive[key] || null;
  };
  const renderableLive = (key: string): Photo | null => {
    const p = livePhoto(key);
    return p && canRenderInAll(p) ? p : null;
  };
  // 每張現在跑到哪，總覽的提示和「跑完了」那句照這個算。
  const progress = batch ? batch.map((s) => batchProgress(batchRuns[sourceSrc(s)], livePhoto(sourceSrc(s)), batchFailed[sourceSrc(s)])) : [];
  // 總覽照時序記進度：有照片開始跑（在總覽按的已經有那句「開始了」，不再記），跑的從有變成沒有就記一句
  // 跑完了；換了一批不算。
  const runningKeys = batch ? batch.map(sourceSrc).filter((_, i) => progress[i] === "running") : [];
  const runningSig = runningKeys.join("\n");
  const wasRunning = useRef({ keys: [] as string[], epoch: 0 });
  useEffect(() => {
    const was = wasRunning.current;
    wasRunning.current = { keys: runningKeys, epoch: batchEpoch.current };
    if (!batch) return;
    // 換了一批（或剛建好這批）就從零算起，這次才開始跑的照樣記。
    const same = was.epoch === batchEpoch.current;
    // 分析好的那張換了長寬比，後端會重新構圖，那段也算在跑，但不是新開始的，不另外講一句「開始分析了」。
    const fresh = runningKeys.filter((k) => (!same || !was.keys.includes(k)) && livePhoto(k)?.status !== "reframing");
    if (fresh.length) {
      const verbs = new Set(fresh.map((k) => {
        const p = livePhoto(k);
        return (p ? photoState(p).key === "processing" : batchRuns[k]?.mode === "render") ? "美化" : "分析";
      }));
      const verb = verbs.size > 1 ? "處理" : [...verbs][0];
      setOverview((t) => (t[t.length - 1]?.kind === "start" ? t : [...t, { id: nextDisplayOrder(), kind: "running", keys: fresh, verb }]));
    }
    if (!same || !was.keys.length || runningKeys.length) return;
    const count = (k: BatchProgress) => progress.filter((x) => x === k).length;
    setOverview((t) => [...t, { id: nextDisplayOrder(), kind: "done", rendered: count("done"), analyzed: count("analyzed"), failed: count("error") }]);
  }, [runningSig]);
  // 送的是「分析」、照片狀態還沒拿到：還在上傳，或傳好了、背景還沒問到。後者不算進來的話，
  // 每傳好一張「全部一鍵美化」就會少一張，等問到了又加回去，按了全部分析之後字一直跳。
  const analyzeNotLoaded = (key: string) => {
    const r = batchRuns[key];
    return !!r && !r.error && r.mode === "analyze" && !livePhoto(key);
  };
  // 「全部一鍵美化」會送到的：沒開始的、分析中或分析好還沒美化的，美化過、正在美化或剛送去美化的不算。
  const needsRender = (key: string) =>
    !renderSent.has(key) && (unstarted(key) || analyzeNotLoaded(key) || !!renderableLive(key));
  // 「全部…」那兩顆顯示的張數：剛按過、還在等的時候用按下去那一刻的。
  const allPending = allHold ? allHold.pending : batch ? batch.filter((x) => unstarted(sourceSrc(x))).length : 0;
  const allRenderable = allHold ? allHold.renderable : batch ? batch.filter((x) => needsRender(sourceSrc(x))).length : 0;
  useEffect(() => {
    if (!allHold) return;
    const settled = (k: string) => !!batchRuns[k]?.error || (!!livePhoto(k) && !renderSent.has(k));
    if (allHold.keys.every(settled)) setAllHold(null);
  }, [allHold, batchRuns, batchLive, renderSent, session]);
  // 哪張一直問不到狀態也不要卡著：等一陣子就照現在的算。
  useEffect(() => {
    if (!allHold) return;
    const t = window.setTimeout(() => setAllHold((h) => (h === allHold ? null : h)), ALL_HOLD_MAX_MS);
    return () => window.clearTimeout(t);
  }, [allHold]);
  const markRenderSent = (keys: string[], sent: boolean) => setRenderSent((s) => {
    const next = new Set(s);
    for (const k of keys) if (sent) next.add(k); else next.delete(k);
    return next;
  });
  // 按了全部一鍵美化時還在上傳的那幾張，傳好就接著美化。
  const renderWhenUploaded = useRef(new Set<string>());

  // 畫面上這張的對話收進 drafts。
  function snapshotDraft() {
    if (!batchKey || !settings) return;
    drafts.current[batchKey] = {
      step, settingsEvents, notesHistory, lastSettingsClick: lastSettingsClick.current, notes, segEdits, settings, prePointed, renderAsk,
      sourceGeneration, presentedGeneration, events: bridge.events, presentation: previewPresentationKey, peopleReady: prePeopleReady,
    };
  }
  function freshDraft(base?: Settings): BatchDraft {
    gen.current = Math.max(gen.current, sourceGeneration) + 1;
    return {
      step: "picked", settingsEvents: [], notesHistory: [], lastSettingsClick: -1, notes: [], segEdits: {}, settings: base || allSettings || settings!,
      prePointed: [], renderAsk: null, sourceGeneration: gen.current, presentedGeneration: null, events: [], presentation: null, peopleReady: false,
    };
  }
  // 把一張的草稿放回畫面。
  function applyDraft(next: Source | null, d: BatchDraft | null) {
    restoring.current = next ? fullSrc(next) : "";
    setSource(next);
    setVia("upload");
    setStep(d?.step ?? "picked");
    setSettingsEvents(d?.settingsEvents ?? []);
    setNotesHistory(d?.notesHistory ?? []);
    lastSettingsClick.current = d?.lastSettingsClick ?? -1;
    setNotes(d?.notes ?? []);
    setSegEdits(d?.segEdits ?? {});
    if (d) setSettings(d.settings);
    else if (allSettings) setSettings(allSettings);
    setPrePointed(d?.prePointed ?? []);
    setRenderAsk(d?.renderAsk ?? null);
    if (d) setSourceGeneration(d.sourceGeneration);
    setPresentedGeneration(d?.presentedGeneration ?? null);
    setPreAsk(null);
    setPreClear((n) => n + 1);
    preDraft.current = "";
    setError("");
    setViewing(false);
  }
  function openBatchPhoto(next: Source) {
    if (!batch || preAsk || busy) return;
    const nextKey = sourceSrc(next);
    if (nextKey === batchKey) return;
    follow.current = null;
    snapshotDraft();
    applyDraft(next, drafts.current[nextKey] || freshDraft());
  }

  type BatchJob = {
    key: string;
    source: Source;
    mode: "analyze" | "render";
    overwrite?: boolean;
    later: Note | null;
    log: Note[];
    segEdits: PromptEdits;
    settings: Settings;
    identity: Promise<{ preview_job_id: string; source_sha256: string } | undefined>;
    mapping: PreviewConversationResult | null;
    effective?: string;
    wishes: PreviewWish[];
    events: PreviewReplyEvent[];
    faces?: Box[];
  };
  // 多張裡的一張開始：跟單張一樣建自己的 session、上傳、帶著對話和需求、分析或美化。
  // 一張一張排著傳（見 batchQueue）；傳好之後要不要跳到結果頁看 follow。
  // 每個請求都有時限：排著傳的話，一個卡住的請求會讓後面每一張都送不出去。傳上去之前
  // 逾時、斷線的，等一下排到最後再試（後面的照常接著傳），試完還不行才算失敗（可以再按一次）。
  function startBatchPhoto(p: BatchJob) {
    if (!caps) return;
    const c = caps;
    const epoch = batchEpoch.current;
    const live = () => epoch === batchEpoch.current;
    const patchRun = (f: (r: BatchRun) => BatchRun) => {
      if (live()) setBatchRuns((rs) => (rs[p.key] ? { ...rs, [p.key]: f(rs[p.key]) } : rs));
    };
    const say = (line: string) => patchRun((r) => (r.done || r.error ? r : { ...r, lines: [...r.lines, line] }));
    setBatchRuns((rs) => ({ ...rs, [p.key]: { mode: p.mode, overwrite: p.overwrite, since: Date.now(), lines: ["正在上傳照片…"] } }));
    let uploaded = "";
    let id = "";
    const bid = batchId.current || undefined;
    const clientKey = (batchKeys.current[p.key] ||= newClientKey());
    const step = () => AbortSignal.timeout(STEP_TIMEOUT_MS);
    const once = async () => {
      // 重試時沿用上一次拿到的 session；上一次建了卻沒等到回應的，後端認得 clientKey，回同一個。
      if (!id) id = (await api.createSession(bid, { signal: step(), clientKey })).session_id;
      const previewIdentity = await p.identity;
      const log = previewEnabled && !previewIdentity
        ? p.log.map((note) => ({ ...note, persons: note.persons.map(withoutPreviewIdentity) })) : p.log;
      const kept = keptNotes(log);
      const userPrompt = (previewIdentity && p.effective !== undefined ? p.effective : notesPrompt(log, p.segEdits)).slice(0, PROMPT_MAX);
      const promptFaces = promptSections(kept).flatMap(groupFaces).filter((f) => userPrompt.includes(f.ref));
      // 跟單張一樣：照片從 session 的預設起跳，先換成它自己的需求和設定再傳。
      await api.patchPlan(id, { ...p.settings, user_prompt: userPrompt, overwrite_prompt: !!p.overwrite && !!userPrompt,
        literal_texts: noteLiteralTexts(log, userPrompt) }, step());
      const { blob, name } = await sourceBlob(p.source, step());
      const added = await uploadKeyed(id, blob, name, previewIdentity && {
        ...previewIdentity, ...(p.mapping ? { conversation_token: p.mapping.token,
          conversation_wishes_digest: p.mapping.wishes_digest } : {}),
      }, previewEnabled ? (previewIdentity ? p.wishes : p.wishes.map((w) => ({ ...w, target_ids: [] }))) : undefined,
      clientKey, live, sideTokenFor(log));
      const pid = added.photo_id;
      uploaded = pid;
      say(p.mode === "analyze" ? "照片傳好了，準備開始分析…" : p.overwrite ? "照片傳好了，準備開始調整…" : "照片傳好了，準備開始美化…");
      if (p.faces) seedDetectedFaces(urls.sourceImage(added.analyze_job_id), p.faces);
      const carried = carriedLog(log, pid, p.later, p.events);
      if (carried.length) await api.postMessages(id, carried, step());
      if (promptFaces.length) await api.patchPhotoPlan(id, pid, { prompt_faces: promptFaces }, step());
      if (p.mode === "render") {
        try {
          await api.render(id, [pid], step());
        } catch (e) {
          // 跟單張一樣：缺人物確認是預期的，需求留著，進對話後分析好了再問。
          if (!(e instanceof ApiError) || e.status !== 409 ||
              !["unresolved_people", "unresolved_visual_target", "protected_scope"].includes(e.body?.reason)) throw e;
          return pid;
        }
        const full = await api.getSession(id, undefined, step());
        const last = [...(full.messages || [])].reverse().find((m) => m.card?.type === "render_requested" && m.card.photo_id === pid);
        if (last) rememberRenderLabel([last.id], { label: p.overwrite ? ONLY_PROMPT_LABEL : "一鍵美化", icon: "spark", meta: renderMeta(c, { ...p.settings }) });
      }
      return pid;
    };
    // 每一次都排到佇列最後：等著重試的這張不擋後面的。
    const attempt = (n: number): Promise<string> => {
      const run = batchQueue.current.then(once);
      batchQueue.current = run.catch(() => {});
      return run.catch(async (e) => {
        if (uploaded || n >= UPLOAD_RETRY_DELAYS_MS.length || !retriable(e) || !live()) throw e;
        if (n === 0) say("連線不穩，重新上傳中…");
        await wait(UPLOAD_RETRY_DELAYS_MS[n]);
        if (!live()) throw e;
        return attempt(n + 1);
      });
    };
    const job = attempt(0);
    // 上傳中按了全部一鍵美化的那張，傳好就接著在它自己的對話裡美化。
    const thenRender = (pid: string) => {
      if (renderWhenUploaded.current.delete(p.key) && live()) void renderStarted(p.key, id, pid, { ...p.settings });
    };
    job.then(
      (pid) => {
        patchRun((r) => ({ ...r, done: true, sessionId: id, photoId: pid }));
        thenRender(pid);
      },
      (e) => {
        // 照片已經進了 session，後面哪一步沒成功就到結果頁再弄，不重傳一次。
        if (uploaded) {
          patchRun((r) => ({ ...r, done: true, sessionId: id, photoId: uploaded }));
          thenRender(uploaded);
        } else {
          if (renderWhenUploaded.current.delete(p.key) && live()) markRenderSent([p.key], false);
          patchRun((r) => ({ ...r, error: errorText(e) }));
        }
      },
    );
  }

  // 畫面上這張照它現在的對話組成一份工作：帶著這張當下的人物預覽，跟它自己按開始一樣。
  function liveBatchJob(mode: "analyze" | "render", overwrite: boolean | undefined, later: Note | null, log: Note[]): BatchJob | null {
    if (!batchKey || !source || !settings) return null;
    return {
      key: batchKey, source, mode, overwrite, later, log, segEdits, settings,
      identity: previewEnabled ? preview.forSubmission() : Promise.resolve(undefined),
      mapping: bridge.result || bridge.committedResult || null, effective: bridge.result?.effective_prompt, wishes: previewWishes,
      events: [...submissionPreviewEvents(bridge.events, mode === "render" && overwrite === true && !prePeopleReady, previewPresentationKey), ...settingsEvents],
      faces: !previewEnabled && preScan.ready ? preScan.people.map((p) => p.box) : undefined,
    };
  }

  // 「我的照片」上面的全部分析／全部一鍵美化：還沒開始的每張照它自己的需求和設定送出。
  // 畫面上這張帶著當下的人物預覽；其他張收在草稿裡、沒有當下的預覽，指定的人進對話後再對一次。
  function startAllBatch(mode: "analyze" | "render", confirmed = false) {
    if (!batch || !caps || busy || preAsk || loadingPhotos) return;
    const base = allSettings || settings;
    if (!base) return;
    const pending = batch.map((s, i) => ({ s, i, key: sourceSrc(s) })).filter((x) => unstarted(x.key));
    // 一鍵美化也帶上開始過、還沒美化的那幾張（分析好的、分析中的），直接在它們各自的對話裡美化；
    // 還在上傳的等傳好再接著送。
    const started = mode === "render" ? batch.map(sourceSrc).filter((k) => renderableLive(k)) : [];
    const uploading = mode === "render" ? batch.map(sourceSrc).filter(analyzeNotLoaded) : [];
    if (!pending.length && !started.length && !uploading.length) return;
    // 在結果頁按的：上傳頁那張已經收進草稿，每張都照草稿送，人留在目前的結果頁。
    const onPage = phase === "chat" ? batchKey : null;
    if (mode === "render" && !confirmed) {
      const unasked = pending.filter((x) => {
        const d = x.key === onPage ? { notes, segEdits, renderAsk } : drafts.current[x.key];
        return !!d && !!notesPrompt(d.notes, d.segEdits).trim() && d.renderAsk?.overwrite === undefined;
      });
      const liveUnasked = started.filter((k) => liveAsk.current.has(k));
      const nums = [...unasked.map((x) => x.i), ...liveUnasked.map((k) => batch.findIndex((s) => sourceSrc(s) === k))]
        .sort((a, b) => a - b).map((i) => i + 1);
      if (nums.length) {
        setLeaveAsk({
          message: `第 ${nums.join("、")} 張有需求，還沒選要整體美化還是僅依需求修改，會用「整體美化」送出。要繼續嗎？`,
          confirmLabel: "繼續",
          cancelLabel: "先去選",
          // 先去選：回到總覽，在那裡問一次怎麼套用，選了就接著美化。
          answer: (ok) => {
            setLeaveAsk(null);
            if (ok) {
              for (const k of liveUnasked) {
                liveMode.current[k] = false;
                liveAsk.current.delete(k);
              }
              startAllBatch(mode, true);
            }
            else {
              openOverview();
              setOverview((t) => [...t, { id: nextDisplayOrder(), kind: "ask", label: renderLabelNow() }]);
            }
          },
        });
        return;
      }
    }
    setError("");
    setAllHold({ keys: [...pending.map((x) => x.key), ...started, ...uploading], pending: allPending, renderable: allRenderable });
    // 跳到第一張的結果頁，其他張在背景照排傳完。
    markRenderSent([...started, ...uploading], true);
    for (const key of started) {
      const r = batchRuns[key];
      const p = renderableLive(key);
      if (r?.sessionId && p) void renderStarted(key, r.sessionId, p.photo_id, p.plan);
    }
    for (const key of uploading) {
      // 傳好了、只是狀態還沒問到的那張直接送；還在傳的等傳好再接著送。
      const r = batchRuns[key];
      if (r?.done && r.sessionId && r.photoId)
        void renderStarted(key, r.sessionId, r.photoId, drafts.current[key]?.settings || base);
      else renderWhenUploaded.current.add(key);
    }
    follow.current = onPage !== null && pending.length ? pending[0].key : null;
    for (const x of pending) {
      if (x.key === onPage) {
        const overwrite = mode === "render" ? renderAsk?.overwrite ?? false : undefined;
        const later = laterNote(notes);
        setRenderAsk(mode === "render" ? { later, log: notes, overwrite } : null);
        const job = liveBatchJob(mode, overwrite, later, notes);
        if (job) startBatchPhoto(job);
        continue;
      }
      const d = drafts.current[x.key];
      const ns = d?.notes || [];
      const seg = d?.segEdits || {};
      const overwrite = mode === "render" ? d?.renderAsk?.overwrite ?? false : undefined;
      startBatchPhoto({
        key: x.key, source: x.s, mode, overwrite, later: laterNote(ns), log: ns, segEdits: seg, settings: d?.settings || base,
        identity: Promise.resolve(undefined), mapping: null, wishes: wishesFor(ns, seg),
        events: d ? [...submissionPreviewEvents(d.events, mode === "render" && overwrite === true && !d.peopleReady, d.presentation), ...d.settingsEvents] : [],
      });
    }
  }

  async function renderStarted(key: string, id: string, pid: string, plan: Parameters<typeof renderMeta>[1]) {
    const epoch = batchEpoch.current;
    try {
      await sendRender(key, id, pid, plan);
    } finally {
      if (epoch === batchEpoch.current) markRenderSent([key], false);
    }
  }
  async function sendRender(key: string, id: string, pid: string, plan: Parameters<typeof renderMeta>[1]) {
    try {
      const overwrite = liveMode.current[key];
      if (overwrite !== undefined) {
        await api.patchPhotoPlan(id, pid, { overwrite_prompt: overwrite });
        delete liveMode.current[key];
      }
      await api.render(id, [pid]);
    } catch (e) {
      // 缺人物確認跟單張一樣是預期的，進那張的結果頁再問。
      if (!(e instanceof ApiError) || e.status !== 409) setError(notice(errorText(e)));
    }
    const full = await api.getSession(id).catch(() => null);
    if (!full) return;
    const last = [...(full.messages || [])].reverse().find((m) => m.card?.type === "render_requested" && m.card.photo_id === pid);
    if (last && caps) rememberRenderLabel([last.id], { label: "一鍵美化", icon: "spark", meta: renderMeta(caps, plan) });
    if (session?.session_id === id) setSession(full);
    else {
      const np = full.photos.find((x) => x.photo_id === pid);
      if (np) setBatchLive((m) => ({ ...m, [key]: np }));
      setBatchFailed((m) => ({ ...m, [key]: renderFailedIn(full, pid) }));
    }
  }

  const batchWished = (key: string) => {
    const d = key === batchKey ? { notes, segEdits } : drafts.current[key];
    return !!d && !!notesPrompt(d.notes, d.segEdits).trim();
  };
  // 結果頁上看到的就是最新的，順手記下來，離開後左邊和一鍵美化才不會照舊的狀態算。
  useEffect(() => {
    if (!session) return;
    const key = Object.keys(batchRuns).find((k) => batchRuns[k].sessionId === session.session_id);
    const np = key && session.photos.find((x) => x.photo_id === batchRuns[key].photoId);
    if (key && np && batchLive[key] !== np) setBatchLive((m) => ({ ...m, [key]: np }));
    if (key && np) {
      const f = renderFailedIn(session, np.photo_id);
      if (!!batchFailed[key] !== f) setBatchFailed((m) => ({ ...m, [key]: f }));
    }
  }, [session, batchRuns]);
  // 還在跑的就隔幾秒再問一次，跑完（或失敗）就不再問。正在看的那張由結果頁自己更新。
  const viewingId = phase === "session" ? session?.session_id : undefined;
  useEffect(() => {
    const epoch = batchEpoch.current;
    const settled = (p?: Photo) => !!p && !isBusy(p) && !p.queued_render && !p.pending_render;
    const due = Object.entries(batchRuns).filter(
      ([k, r]) => r.done && r.sessionId && r.sessionId !== viewingId && !settled(batchLive[k]),
    );
    if (!due.length) return;
    const t = window.setTimeout(async () => {
      const got = await Promise.all(
        due.map(async ([k, r]) => {
          const s = await api.getSession(r.sessionId!).catch(() => null);
          const p = s && (s.photos.find((x) => x.photo_id === r.photoId) || activePhoto(s));
          return s && p ? ([k, p, renderFailedIn(s, p.photo_id)] as const) : null;
        }),
      );
      if (epoch !== batchEpoch.current) return;
      setBatchLive((m) => {
        const next = { ...m };
        for (const g of got) if (g) next[g[0]] = g[1];
        return next;
      });
      setBatchFailed((m) => {
        const next = { ...m };
        for (const g of got) if (g) next[g[0]] = g[2];
        return next;
      });
    }, Object.keys(batchLive).length ? 4000 : 800);
    return () => window.clearTimeout(t);
  }, [batchRuns, batchLive, viewingId]);
  // 開了的那張到它自己的結果頁。上傳頁原封不動留著（照片、草稿、進度），左邊的
  // 「我的照片」點還沒開始的就回來接著弄。
  function openBatchResult(key: string) {
    const sid = batchRunsRef.current[key]?.sessionId;
    if (!sid) return;
    follow.current = null;
    if (phase === "chat") snapshotDraft();
    void openSession(sid).then(() => refreshSessions());
  }
  // 從結果頁回上傳頁，打開點的那張。
  function backToBatch(next: Source) {
    setSession(null);
    setSelected(null);
    setSessionHash(null);
    setPhase("chat");
    const key = sourceSrc(next);
    if (key !== batchKey) applyDraft(next, drafts.current[key] || freshDraft());
    window.scrollTo(0, 0);
  }
  // ── 多張上傳的總覽頁：不打開哪一張，對全部一起下指令 ──
  // 左邊「總覽」那格：從某一張的對話或結果頁回總覽。
  function openOverview() {
    if (!batch || preAsk || busy) return;
    follow.current = null;
    if (phase === "session") {
      setSession(null);
      setSelected(null);
      setSessionHash(null);
      setPhase("chat");
    } else if (!batchKey) return;
    else snapshotDraft();
    applyDraft(null, null);
    window.scrollTo(0, 0);
  }
  // 總覽說的一句需求：放進每張還沒美化的照片自己的個人化需求，之後點那張還能再改。
  // 還沒開始的寫進草稿，原本選過的一鍵美化方式跟著作廢，下次一鍵美化再問一次；
  // 開始過、還沒美化的（分析中、分析好的）接在它 plan 的需求後面，美化時就照這句。
  // 總覽打的一句，跟上傳頁一樣先讓 AI 看過（/prompt-review）：是需求才發到每張還沒美化的照片，
  // 問題、閒聊、撤回只回話；「幫我美化」「幫我分析」照按那兩顆；說了比例就換總覽的長寬比。
  // AI 沒回應就照原句發出去，不讓一次失敗卡住整批。
  async function overviewSay(text: string) {
    if (!batch) return;
    const id = nextDisplayOrder();
    const said = overview.filter((t): t is Extract<OverviewTurn, { kind: "wish" }> => t.kind === "wish" && !t.pending);
    setOverview((t) => [...t, { id, kind: "wish", text, count: 0, skipped: 0, pending: true }]);
    setError("");
    const keptTurns = said.filter((t) => t.kept !== false && !t.gone);
    const kept = keptTurns.map((t) => t.line || t.text);
    const history: ReviewTurn[] = said.flatMap((t) => [
      { role: "user" as const, text: t.text },
      ...(t.reply ? [{ role: "assistant" as const, text: t.reply, ...(t.options?.length ? { question: true, ...(t.offer ? { offer: true } : {}), options: t.options } : {}) }] : []),
    ]);
    const done = (x: Partial<Extract<OverviewTurn, { kind: "wish" }>>) =>
      setOverview((t) => t.map((y) => (y.id === id && y.kind === "wish" ? { ...y, ...x, pending: false } : y)));
    // 跟總覽上那兩顆鍵同一個名字；沒有還沒分析的就不給「分析」，AI 也就不會提。
    const keys = batch.map(sourceSrc);
    const pending = keys.filter(unstarted).length;
    const unrendered = keys.filter(needsRender).length;
    const scope = {
      photos: keys.length,
      unrendered,
      analyze: pending ? allLabel("analyze", pending, keys.length) : "",
      render: unrendered ? allLabel("render", unrendered, keys.length) : "",
      where: drawerLayout ? "上面的照片" : "左邊的「我的照片」",
    };
    let r: Awaited<ReturnType<typeof api.promptReview>>;
    try {
      r = await api.promptReview(text, kept.join("；"), history, [], [], scope);
    } catch {
      done(spreadWish(text));
      return;
    }
    // 撤回、或新的一句取代了前面的：merged 是總覽的需求清單改完剩下的樣子。前面講過、
    // 已經發到每張的那幾句，不在裡面的就從每張拿掉；只剩一部分的，剩下那部分重新發一次。
    // 撤回卻沒給剩下的樣子：只當成撤回最近那一句，不把前面講過的全部拿掉。
    if (r.merged?.length || r.withdrawn) {
      const left = r.merged?.length ? r.merged[0].text?.trim() || "" : kept.slice(0, -1).join("；");
      const stay = new Set(keptTurns.filter((t) => left.includes(t.line || t.text)).map((t) => t.id));
      const gone = keptTurns.filter((t) => !stay.has(t.id));
      for (const t of gone) unspreadWish(t.line || t.text);
      const staying = new Set(keptTurns.filter((t) => stay.has(t.id)).map((t) => t.line || t.text));
      for (const part of left.split(/[；;\n]/).map((x) => x.trim()).filter((x) => x && !staying.has(x))) spreadWish(part);
      if (gone.length) setOverview((t) => t.map((y) => (gone.some((g) => g.id === y.id) && y.kind === "wish" ? { ...y, gone: true } : y)));
    }
    if (r.accepted && !r.withdrawn) {
      const line = r.line && r.line !== text ? r.line : undefined;
      // 回話照後端的：先回應這句，再講會套到哪幾張、下一步按哪顆。
      done({ ...spreadWish(line || text), kept: true, line, reply: r.reply || undefined });
    } else if (r.start) {
      // 「幫我美化」就是按那顆：接下來那則「開始了」會講，這裡不多回一句。
      done({ kept: false, reply: "" });
    } else {
      done({ kept: false, reply: r.reply || "好，那就先不加這句。", options: r.ask || r.offer ? r.options || [] : [], offer: !!r.offer });
    }
    // 說了比例、或開關人像修正的（「做成限動」「閉眼不用修」）改總覽的設定；只講這些的不進清單。
    if (allSettings && (r.aspect_ratio || r.advanced_options))
      overviewSettings(withReviewSettings(allSettings, r));
    if (r.start) overviewStart(r.start);
  }
  // 一句需求發到每張還沒美化的照片：還沒開始的進它的草稿，開始過、還沒美化的接在它的個人化需求後面。
  function spreadWish(text: string) {
    const keys = (batch || []).map(sourceSrc);
    const open = keys.filter(unstarted);
    const live = keys.filter((k) => !unstarted(k) && renderableLive(k));
    for (const k of open) {
      const d = drafts.current[k] || freshDraft();
      const note: Note = { order: nextDisplayOrder(), replyOrder: nextDisplayOrder(), text, persons: [], kept: true, reply: OVERVIEW_NOTE_REPLY };
      drafts.current[k] = { ...d, notes: [...d.notes, note], renderAsk: null };
    }
    for (const k of live) void wishLive(k, text);
    const count = open.length + live.length;
    return { count, skipped: keys.length - count };
  }
  // spreadWish 的反面：總覽發出去的那句，從還沒開始的草稿裡拿掉，開始過、還沒美化的從個人化需求刪掉那一行。
  function unspreadWish(text: string) {
    for (const k of (batch || []).map(sourceSrc)) {
      const d = drafts.current[k];
      if (unstarted(k)) {
        if (d) drafts.current[k] = { ...d, notes: d.notes.filter((n) => !(n.reply === OVERVIEW_NOTE_REPLY && n.text === text)), renderAsk: null };
        continue;
      }
      const r = batchRuns[k];
      const p = renderableLive(k);
      if (!r?.sessionId || !p) continue;
      const before = p.plan.user_prompt || "";
      const after = before.split("\n").filter((l) => l.trim() !== text).join("\n");
      if (after === before) continue;
      const id = r.sessionId;
      void api.patchPhotoPlan(id, p.photo_id, { user_prompt: after }).then(({ plan }) => {
        if (session?.session_id === id)
          setSession((s) => s && s.session_id === id ? { ...s, photos: s.photos.map((x) => (x.photo_id === p.photo_id ? { ...x, plan } : x)) } : s);
        else setBatchLive((m) => (m[k] ? { ...m, [k]: { ...m[k], plan } } : m));
      }).catch((e) => setError(notice(errorText(e))));
    }
  }
  async function wishLive(key: string, text: string) {
    const r = batchRuns[key];
    const p = renderableLive(key);
    if (!r?.sessionId || !p) return;
    const before = (p.plan.user_prompt || "").trim();
    const id = r.sessionId;
    try {
      const { plan } = await api.patchPhotoPlan(id, p.photo_id, { user_prompt: before ? `${before}\n${text}` : text });
      liveAsk.current.add(key);
      delete liveMode.current[key];
      if (session?.session_id === id)
        setSession((s) => s && s.session_id === id ? { ...s, photos: s.photos.map((x) => (x.photo_id === p.photo_id ? { ...x, plan } : x)) } : s);
      else setBatchLive((m) => (m[key] ? { ...m, [key]: { ...m[key], plan } } : m));
    } catch (e) {
      setError(notice(errorText(e)));
    }
  }
  // 總覽的「設定」：開一輪新的，上一輪選過「保留各自的」那幾張這輪再問一次。
  function overviewEditSettings() {
    if (!allSettings || overview[overview.length - 1]?.kind === "settings") return;
    keepOwn.current = new Set();
    // 分析引擎只有還沒開始的用得到；開的當下沒有這種照片就不擺，之後回頭看也照當時的。
    const model = !!batch?.some((s) => unstarted(sourceSrc(s)));
    setOverview((t) => [...t, { id: nextDisplayOrder(), kind: "settings", settings: allSettings, from: allSettings, model }]);
  }
  // 美化用得到的那幾項（分析引擎分析完就用不到了）。人像修正補上預設值，沒寫的跟寫了預設的才算一樣。
  const renderPart = (x: Partial<Settings>) => ({
    render_option: x.render_option,
    aspect_ratio: x.aspect_ratio,
    advanced_options: Object.fromEntries((caps?.advanced_options || []).map((o) => [o.key, x.advanced_options?.[o.key] ?? o.default])),
  });
  // 分析好、還沒美化的那張也換成總覽的設定：寫進它自己的 plan，之後一鍵美化就照這個。
  // 長寬比不一樣時後端會自己重新構圖。引擎在一般帳號可能不給改，被擋就不帶這項。
  async function patchLive(key: string, next: Settings) {
    const r = batchRuns[key];
    const p = renderableLive(key);
    if (!r?.sessionId || !p) return;
    const patch: Plan = renderPart(next);
    await api.patchPhotoPlan(r.sessionId, p.photo_id, patch).catch(() => {
      const { render_option, ...rest } = patch;
      return api.patchPhotoPlan(r.sessionId!, p.photo_id, rest);
    }).catch((e) => setError(notice(errorText(e))));
    const full = await api.getSession(r.sessionId).catch(() => null);
    if (!full) return;
    if (session?.session_id === r.sessionId) setSession(full);
    else {
      const np = full.photos.find((x) => x.photo_id === p.photo_id);
      if (np) setBatchLive((m) => ({ ...m, [key]: np }));
    }
  }
  // 總覽改的設定套到每張還沒美化的；在單張頁另外改過的，先問要換成總覽的還是留著。
  function overviewSettings(next: Settings) {
    const prev = allSettings;
    if (!batch || !prev) return;
    const renderChanged = JSON.stringify(renderPart(prev)) !== JSON.stringify(renderPart(next));
    setAllSettings(next);
    setSettings(next);
    setOverview((t) => t.map((x, i) => (i === t.length - 1 && x.kind === "settings" ? { ...x, settings: next } : x)));
    const own: { key: string; i: number }[] = [];
    batch.forEach((s, i) => {
      const key = sourceSrc(s);
      const d = drafts.current[key];
      const p = renderableLive(key);
      if (!unstarted(key) && p && renderChanged) {
        if (JSON.stringify(renderPart(p.plan)) === JSON.stringify(renderPart(prev))) void patchLive(key, next);
        else if (!keepOwn.current.has(key)) own.push({ key, i });
        return;
      }
      if (!d || !unstarted(key)) return;
      if (sameSettings(d.settings, prev)) d.settings = next;
      else if (!keepOwn.current.has(key)) own.push({ key, i });
    });
    if (!own.length) return;
    setLeaveAsk({
      message: `第 ${own.map((x) => x.i + 1).join("、")} 張在單張頁改過設定，要換成總覽的設定，還是保留它們自己的？`,
      confirmLabel: "換成總覽的",
      cancelLabel: "保留各自的",
      answer: (yes) => {
        setLeaveAsk(null);
        for (const x of own) {
          const d = drafts.current[x.key];
          if (yes && !unstarted(x.key)) void patchLive(x.key, next);
          else if (yes && d) d.settings = next;
          else keepOwn.current.add(x.key);
        }
      },
    });
  }
  // 總覽的全部分析／全部一鍵美化。一鍵美化時有需求、還沒選怎麼套用的，先在總覽問一次。
  function overviewStart(mode: "analyze" | "render") {
    if (!batch || busy || preAsk) return;
    if (mode === "render") {
      const unasked = batch.map(sourceSrc).filter((k) => unstarted(k)
        ? batchWished(k) && drafts.current[k]?.renderAsk?.overwrite === undefined
        : liveAsk.current.has(k) && !!renderableLive(k));
      if (unasked.length) {
        setOverview((t) => [...t, { id: nextDisplayOrder(), kind: "ask", label: renderLabelNow() }]);
        return;
      }
    }
    overviewGo(mode);
  }
  function overviewRenderMode(overwrite: boolean) {
    if (!batch) return;
    for (const k of batch.map(sourceSrc)) {
      const d = drafts.current[k];
      if (d && unstarted(k) && batchWished(k) && d.renderAsk?.overwrite === undefined)
        d.renderAsk = { later: laterNote(d.notes), log: d.notes, overwrite };
      if (!unstarted(k) && liveAsk.current.has(k)) {
        liveMode.current[k] = overwrite;
        liveAsk.current.delete(k);
      }
    }
    setOverview((t) => t.map((x, i) => (i === t.length - 1 && x.kind === "ask" ? { ...x, overwrite } : x)));
    overviewGo("render", overwrite);
  }
  const renderLabelNow = () => batch ? allLabel("render", batch.filter((x) => needsRender(sourceSrc(x))).length, batch.length) : undefined;
  function overviewGo(mode: "analyze" | "render", overwrite?: boolean) {
    if (!batch) return;
    const keys = batch.map(sourceSrc).filter((k) => (mode === "render" ? needsRender(k) : unstarted(k)));
    const label = allLabel(mode, keys.length, batch.length);
    setOverview((t) => [...t, { id: nextDisplayOrder(), kind: "start", mode, count: keys.length, keys, overwrite, label }]);
    startAllBatch(mode, true);
  }

  useEffect(() => {
    const key = follow.current;
    if (!key || phase !== "chat") return;
    const r = batchRuns[key];
    if (r?.error) follow.current = null;
    else if (r?.done && r.sessionId) openBatchResult(key);
  }, [batchRuns]);

  // HEIC has to be decoded before it can be shown, which takes a moment; a
  // second pick made meanwhile wins over the first.
  const lastPick = useRef(0);
  async function pickFile(file: File) {
    if (!file.type.startsWith("image/") && !isHeic(file)) {
      setError(notice("請選擇圖片檔。"));
      return;
    }
    const pick = ++lastPick.current;
    const preview = await previewBlob(file);
    if (pick !== lastPick.current) return;
    const s: Source = { kind: "file", file, url: URL.createObjectURL(preview) };
    if (phase === "session") {
      if (await confirmLeave("換這張")) newChat(s);
    } else if (phase !== "chat") {
      setPhase("chat");
      choose(s);
    } else swapSource(s);
  }

  function onGallerySelect(sel: GallerySelection) {
    // A past chat opens right away; a sample on the home page is only selected.
    if (sel && (phase === "chat" || sel.kind === "session")) {
      if (sel.kind === "session") openOtherSession(sel.id);
      else swapSource({ kind: "demo", name: sel.names[sel.names.length - 1] });
      return;
    }
    setSelection(sel);
  }

  // 首頁勾的範例照片：一張開單張的對話，兩張以上開多張頁。沒勾就是空的新對話。
  function startFromSelection() {
    const names = selection?.kind === "demo" ? selection.names : [];
    if (names.length > 1) {
      ++lastPick.current;
      void startBatch(names.slice(0, MAX_BATCH).map((name) => ({ kind: "demo", name })), "");
    } else newChat(names[0] ? { kind: "demo", name: names[0] } : undefined);
  }

  function onGalleryConfirm() {
    if (selection?.kind === "demo") startFromSelection();
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  async function run(fn: () => Promise<void>): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError("");
    try {
      // 個人化需求框裡還沒存的字先存好，動作才用得到。
      await flushPromptDraft();
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
  async function afterRender(id: string, label: string, icon: string, plan: Plan, signal?: AbortSignal) {
    const full = await api.getSession(id, undefined, signal);
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
    const sideTurn = base.some(n => !n.dropped && !n.askedResolved && n.asked?.subtype === "body_side_reference") || /[左右](?:側|邊|半)?(?:可見)?的?(?:那隻)?(?:手|腳|腿|眼|肩|臉|耳|眉|膝|胳膊)/.test(text);
    // A short correction such as 改成張開 answers the active action question;
    // it must not revise another completed wish and leave this one unresolved.
    if (!sideTurn && (bridgeQuestion?.kind === "action" || bridgeQuestion?.kind === "edit_description") && base === notes && !/取消|撤回|不做了|不用了|不要了/.test(text)) {
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
    if (!sideTurn && (bridge.canRevise || bridge.result) && /不是|取消|撤回|保持原樣|不要|不整理|改成|改為|改看/.test(text)) {
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
    if (!sideTurn && !actionTried && bridgeQuestion && bridgeQuestion.kind !== "semantic" && base === notes) {
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
    if (asking?.asked?.subtype === "body_side_reference" && !asking.asked.side_question && text === "重新確認左右需求") text = asking.text;
    const picked = preRefs(subjectIndices);
    const note: Note = { order: nextDisplayOrder(), text, persons: picked.length ? picked : asking?.persons || [],
      selectionSource: picked.length ? "user_selection" : asking?.selectionSource };
    // 要靠前一句才看得懂的話（回答追問的「棕色」、接著講的「褲子也是」），模型
    // 會補成完整的一句，連同它接過來的人；清單裡每一句才能單獨讀、單獨刪。回答
    // 追問回答必須由後端補成完整需求；未補全時繼續追問，不在前端拼接。
    const completed = (line?: string, wheres: string[] = []): Partial<Note> => {
      const carried = wheres.flatMap((w) => preOrder.filter((p) => preWhere(p.subjectIndex) === w));
      const persons = note.persons.length ? note.persons : preRefs(carried.map((p) => p.subjectIndex));
      const wish = line;
      return { persons, selectionSource: note.selectionSource || (persons.length ? "model_suggestion" : undefined), ...(wish && wish !== text ? { wish } : {}) };
    };
    setPreAsk(note);
    // 又多講了一句，需求變了，剛才那個問題就不算數，等講完再按一次。
    setRenderAsk(null);
    if (typed) setPreClear((n) => n + 1);
    // 分段送：哪一段有衝突，模型就只重寫那一段，指定的人跟著段留住。
    const sent = promptSections(base, segEdits);
    try {
      let sideContext: { source_sha256: string; analysis_model: string; previous_token?: string } | undefined;
      const sideQuestion = asking?.asked?.side_question;
      if ((sideQuestion || /[左右](?:側|邊|半)?(?:可見)?的?(?:那隻)?(?:手|腳|腿|眼|肩|臉|耳|眉|膝|胳膊)/.test(text)) && source && settings) {
        const { blob, name } = await sourceBlob(source, AbortSignal.timeout(STEP_TIMEOUT_MS));
        // WebCrypto is unavailable on ordinary HTTP deployments. The fallback
        // computes only a digest on the server, with no preview/model job.
        const sourceHash = globalThis.crypto?.subtle
          ? Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())), b => b.toString(16).padStart(2, "0")).join("")
          : (await api.bodySideSourceHash(blob, name)).source_sha256;
        sideContext = { source_sha256: sourceHash, analysis_model: settings.analysis_model || (caps ? defaultSettings(caps).analysis_model : "") || "", previous_token: sideTokenFor(base) };
      }
      const reviewed = await api.promptReview(
        noteLine(note),
        bridge.result?.effective_prompt ?? notesPrompt(base, segEdits),
        reviewHistory(base, [...bridge.events, ...settingsEvents]),
        preOrder.map((p) => preWhere(p.subjectIndex)),
        sent.map((x) => ({ who: x.key, text: x.text })),
        undefined, sideContext, sideQuestion && asking?.sideToken ? { ...sideChoice(sideQuestion, text), token: asking.sideToken } : undefined,
      );
      if (!stillCurrent()) return null;
      let r = routeReviewQuestion(reviewed, previewEnabled);
      if (asking && r.accepted && !r.withdrawn && !r.line && !r.verbatim) {
        r = { ...r, accepted: false, ask: true, options: [],
          reply: "這項需求還沒整理完整，請再說明想調整的效果。" };
      }
      const settled: Note = {
        ...note,
        replyOrder: nextDisplayOrder(),
        reply: r.reply || (r.accepted ? "好，這些會當成個人化需求，美化時一起套用。" : "好，那就先不加個人化需求。"),
        replyPeople: prePickable.map(p => ({ ...p })),
        // 只是撤回前面那項的一句不進清單：前面那項拿掉，就是照原樣了。
        kept: r.accepted && !r.withdrawn,
        ...(r.semantic_pending && (previewEnabled || preview.identity) ? { actionPending: true, resolvedWish: "" } : {}),
        ...(r.ask ? { asked: { options: r.options || [], kind: r.question_kind || "meaning",
          subtype: r.clarification_subtype, side_question: r.side_question } } : {}),
        ...(r.side_reference_token ? { sideToken: r.side_reference_token } : {}),
        ...(r.offer ? { offered: { options: r.options || [], ...(r.later ? { later: true } : {}) } } : {}),
        ...(r.accepted && !r.withdrawn ? completed(r.line, r.persons) : {}),
        ...(r.accepted && !r.withdrawn ? { literalTexts: r.literal_texts || r.request_semantics?.flatMap(s => s.literal_text ? [s.literal_text] : []) || [] } : {}),
        ...(r.verbatim ? { verbatim: true, wish: note.text, persons: [], selectionSource: undefined } : {}),
        ...(r.interpreted ? { interpreted: true,
          // A reviewed scene-wide revision is already complete. Forward that
          // revised text unchanged even if preview has not arrived. Person or
          // worn-object edits still need the regular identity bridge.
          verbatim: !!r.request_semantics?.length && r.request_semantics.every(s =>
            (s.target_kind === "image" || s.target_kind === "region" || s.feature === "text_overlay") && !s.owner_required) } : {}),
      };
      // 跟前面的需求衝突（同一個人、同一件事不能兩個都做）：模型把衝突的那幾段
      // 拿掉被這句取代的部分，跟使用者手改那段字走同一條路；這句自己接在它那段
      // 後面。指定了人的句子只動那個人的段；沒指定的句子講的是所有人或整個畫面，
      // 每個人的段都可能要改。
      const resolvedBase = r.clarification_subtype === "body_side_reference" &&
        (r.accepted || r.side_question?.id !== asking?.asked?.side_question?.id)
        ? base.map(n => n.asked?.subtype === "body_side_reference" ? { ...n, askedResolved: true } : n) : base;
      setNotes([...resolvedBase, settled]);
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
      // 「做成限動」「閉眼不用修」這種設定做得到的，同步改「設定」；只講這些的不進清單。
      if (r.aspect_ratio || r.advanced_options) setSettings((s) => s && withReviewSettings(s, r));
      // 「幫我美化」「幫我分析」就是底下那兩顆鍵，照按：已經收下的需求一起帶進去。
      if (r.start) startSession(r.start, laterNote(base), [...base, settled]);
      return r.accepted ? [...resolvedBase, settled] : null;
    } catch (error) {
      // 問不到就放行：一次模型失敗不該讓人卡在上傳頁面，而且進了 session，
      // 個人化需求面板按下確認時還會再過一次。
      if (!stillCurrent()) return null;
      if (sideTurn) {
        const stale = error instanceof ApiError && /side_(?:model|photo)_changed|stale_side_question/.test(error.message);
        const retryBase = stale ? base.map(n => n.asked?.subtype === "body_side_reference" ? { ...n, askedResolved: true } : n) : base;
        setNotes([...retryBase, { ...note, kept: false, replyOrder: nextDisplayOrder(),
          ...(stale ? { text: asking?.asked?.side_question?.original_text || asking?.text || note.text } : {}),
          asked: !stale && asking?.asked || { options: ["重新確認左右需求"], kind: "content", subtype: "body_side_reference" },
          sideToken: stale ? undefined : asking?.sideToken, reply: stale ? "照片或設定已更新，請重新確認這項左右需求。" : "左右確認尚未完成，請重試目前的選項。" }]);
        return null;
      }
      if (asking) {
        setNotes([...base, { ...note, kept: false, asked: { options: [], kind: "meaning" }, replyOrder: nextDisplayOrder(),
          reply: "這項需求還沒整理完整，請再說明想調整的效果。" }]);
        return null;
      }
      const kept = { ...note, ...completed(), kept: true, ...(preview.identity ? { actionPending: true, resolvedWish: "" } : {}), replyOrder: nextDisplayOrder(),
        reply: "好，這些會當成個人化需求，美化時一起套用。",
        replyPeople: prePickable.map(p => ({ ...p })) };
      setNotes([...base, kept]);
      return [...base, kept];
    } finally {
      if (stillCurrent()) setPreAsk(null);
    }
  }

  // 只有後端明確標為 person 的無選項追問可點頭像選人，可以選好幾位，
  // 選好什麼都不打直接送出就是回答。
  const preWhoAsk = (() => {
    const asking = openAsk(notes);
    return asking?.asked?.kind === "person" && !asking.asked.options.length ? asking : null;
  })();

  // 選好人直接送出：那句需求接到這幾位身上收下來，不用重打一次，也不用再問模型
  // ——缺的只有人，人已經點了。
  function answerWho(subjectIndices: number[]) {
    const persons = preRefs(subjectIndices);
    if (!preWhoAsk || !persons.length) return;
    if (preWhoAsk.asked?.subtype === "body_side_reference") {
      void reviewNote(persons.length > 1 ? "這幾位" : "這位", subjectIndices, notes, false);
      return;
    }
    const text = persons.length > 1 ? "這幾位" : "這位";
    setNotes([...notes, { order: nextDisplayOrder(), replyOrder: nextDisplayOrder(), text, wish: preWhoAsk.wish ?? preWhoAsk.text, persons, selectionSource: "user_selection", reply: WHO_ANSWERED_REPLY, kept: true }]);
  }

  // `later` 是要帶進 session、照片好了再回答的那句；沒給就看最後一句是不是。
  // `log` 是到這一刻為止的對話：打字要求開始的那句剛收下，還沒進 state。
  // `overwrite` 是一鍵美化前問到的答案：true 只照個人化需求改，false 整體美化
  // 再加上需求。有需求卻還沒問過，就先問，不開始。
  function startSession(mode: "analyze" | "render", later = laterNote(notes), log = notes, overwrite?: boolean) {
    if (!source || !caps || !settings || busy || submissionLock.current) return;
    const sidePending = [...log].reverse().find(n => !n.dropped && !n.askedResolved && n.asked?.subtype === "body_side_reference");
    if (sidePending && !sidePending.sideToken) {
      setError(notice("左右需求尚未保存，請先按「重新確認左右需求」重試。"));
      return;
    }
    if (mode === "render" && sidePending) {
      setError(notice("請先確認身體部位左右的參考角度，再開始美化；也可以先分析。"));
      return;
    }
    if (mode === "render" && bridgeQuestion && bridge.result?.clauses.some(c =>
        bridgeQuestion.wish_ids.includes(c.wish_id) && c.scope === "protection")) {
      setError(notice("請先確認個人化需求指定的人物，再開始美化。"));
      return;
    }
    if (mode === "render" && overwrite === undefined && (bridge.result?.effective_prompt ?? notesPrompt(log, segEdits)).trim()) {
      setRenderAsk({ later, log });
      return;
    }
    setRenderAsk(mode === "render" && overwrite !== undefined ? { later, log, overwrite } : null);
    // 多張裡的一張：自己開始，傳好就跳到它的結果頁，其他張留在上傳頁。
    if (batch && batchKey) {
      const job = unstarted(batchKey) && liveBatchJob(mode, overwrite, later, log);
      if (job) {
        follow.current = batchKey;
        startBatchPhoto(job);
      }
      return;
    }
    // 個人化需求只認已經送出、也已經讓 AI 看過的那幾句。輸入框裡打了沒送的字
    // 就當沒打過：它沒進過對話，也沒過把關，不該從這裡偷渡進 user_prompt。
    submissionLock.current = true;
    const selectedSource = selectionIdentity;
    const submittedEvents = [...submissionPreviewEvents(bridge.events,
      mode === "render" && overwrite === true && !prePeopleReady, previewPresentationKey), ...settingsEvents,
      ...appendNotesHistory(notesHistory, historySections, nextDisplayOrder(), conversation(log).length)];
    // Call before leaving preview: this promise contains an already-frozen,
    // nonblocking decision and cannot adopt a result arriving during upload.
    const previewDecision = previewEnabled ? preview.forSubmission() : Promise.resolve(undefined);
    const since = Date.now();
    const say = (line: string) => setStarting((s) => s && { ...s, lines: [...s.lines, line] });
    setStarting({ mode, overwrite, since, lines: ["正在上傳照片…"] });
    setSubmitting({ sources: [source], mode, since });
    void run(async () => {
      const previewIdentity = await previewDecision;
      const submissionLog = previewEnabled && !previewIdentity ? log.map((note) => ({
        ...note, persons: note.persons.map(withoutPreviewIdentity),
      })) : log;
      const kept = keptNotes(submissionLog);
      const userPrompt = (previewIdentity && bridge.result ? bridge.result.effective_prompt : notesPrompt(submissionLog, segEdits)).slice(0, PROMPT_MAX);
      // Only the picks the prompt still spells out: one entry per person
      // already names each face once, and PROMPT_MAX can still cut the last
      // entry off.
      const promptFaces = promptSections(kept)
        .flatMap(groupFaces)
        .filter((f) => userPrompt.includes(f.ref));
      // 跟多張一樣：每個請求都有時限，傳上去之前逾時、斷線的原地等一下再試（同一把 clientKey，
      // 不會多建一段、多傳一張），試完還不行才算失敗。換了照片就不再試。
      const same = () => sourceIdentity.current === selectedSource;
      const changed = () => new Error("照片已更換，請重新選擇分析或一鍵美化。");
      const step = () => AbortSignal.timeout(STEP_TIMEOUT_MS);
      if (singleKey.current?.source !== selectedSource) singleKey.current = { source: selectedSource, key: newClientKey() };
      const clientKey = singleKey.current.key;
      let id = "";
      let added: { photo_id: string; analyze_job_id: string };
      for (let n = 0; ; n++) {
        try {
          if (!same()) throw changed();
          if (!id) id = (await api.createSession(undefined, { signal: step(), clientKey })).session_id;
          // The defaults a photo added next starts from, so the upload below picks
          // the 個人化需求 up without a second patch.
          if (userPrompt || !sameSettings(settings, defaultSettings(caps))) {
            await api.patchPlan(id, {
              ...settings,
              ...(userPrompt ? { user_prompt: userPrompt, overwrite_prompt: !!overwrite,
                literal_texts: noteLiteralTexts(submissionLog, userPrompt) } : {}),
            }, step());
          }
          const { blob, name } = await sourceBlob(source, step());
          if (!same()) throw changed();
          const mapping = bridge.result || bridge.committedResult;
          added = await uploadKeyed(id, blob, name, previewIdentity && {
            ...previewIdentity, ...(mapping ? { conversation_token: mapping.token,
              conversation_wishes_digest: mapping.wishes_digest } : {}),
          }, previewEnabled ? (previewIdentity ? previewWishes : previewWishes.map(w => ({ ...w, target_ids: [] }))) : undefined,
          clientKey, same, sideTokenFor(submissionLog));
          singleKey.current = null;
          break;
        } catch (e) {
          if (n >= UPLOAD_RETRY_DELAYS_MS.length || !retriable(e) || !same()) throw e;
          if (n === 0) say("連線不穩，重新上傳中…");
          await wait(UPLOAD_RETRY_DELAYS_MS[n]);
        }
      }
      if (!same()) return;
      say(mode === "analyze" ? "照片傳好了，準備開始分析…" : overwrite ? "照片傳好了，準備開始調整…" : "照片傳好了，準備開始美化…");
      // 上傳頁面已經偵測過這張的人臉，session 頁沒有分析時就直接用，不再偵測一次。
      if (!previewEnabled && preScan.ready) seedDetectedFaces(urls.sourceImage(added.analyze_job_id), preScan.people.map((p) => p.box));
      // 上傳頁面聊過的接在照片後面。等著回答的那句也在裡面：分析一好，session
      // 的 AI 就回答它，使用者不用再問一次。要在切到 session 頁之前搬好：頁面以
      // 打開時最新的那則為界，之後進來的才打字，搬晚了舊對話會被當成新回覆重打一次。
      const carried = carriedLog(submissionLog, added.photo_id, later, submittedEvents);
      let uploadedSession: Session;
      try {
        for (let at = 0; at < carried.length; at += 100) {
          await api.postMessages(id, carried.slice(at, at + 100), step());
        }
        if (sourceIdentity.current !== selectedSource) return;
        uploadedSession = await api.getSession(id, undefined, step());
      } catch {
        // 照片已經進了 session：跟多張一樣不重傳，直接打開它的結果頁再弄（美化在那裡按）。
        // 沒搬完的上傳頁對話就算了。
        if (sourceIdentity.current === selectedSource) await openSession(id);
        return;
      }
      if (sourceIdentity.current !== selectedSource) return;
      setSession(uploadedSession);
      setSelected(null);
      setPhase("session");
      setSessionHash(id);
      setSubmitting(null);
      // On the photo, not the session defaults: a second photo has its own
      // faces and must not inherit these.
      if (promptFaces.length) await api.patchPhotoPlan(id, added.photo_id, { prompt_faces: promptFaces }, step());
      if (sourceIdentity.current !== selectedSource) return;
      if (mode === "render") {
        try {
          await api.render(id, [added.photo_id], step());
        } catch (e) {
          // Upload already started analysis. Missing identity is expected:
          // keep the wish and let the formal conversation ask after analysis.
          if (!(e instanceof ApiError) || e.status !== 409 ||
              !["unresolved_people", "unresolved_visual_target", "protected_scope"].includes(e.body?.reason)) throw e;
          const awaitingConfirmation = await api.getSession(id, undefined, step());
          if (sourceIdentity.current === selectedSource) setSession(awaitingConfirmation);
          return;
        }
        if (sourceIdentity.current !== selectedSource) return;
        await afterRender(id, overwrite ? ONLY_PROMPT_LABEL : "一鍵美化", "spark", { ...settings }, step());
      } else {
        const completedSession = await api.getSession(id, undefined, step());
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

  // 同一段對話裡還沒美化過的每一張一起一鍵美化，各自照自己目前的設定。
  function renderAll() {
    if (!session || !photo) return;
    const id = session.session_id;
    const ids = session.photos.filter(canRenderInAll).map((p) => p.photo_id);
    if (!ids.length) return;
    const plan = photo.plan;
    run(async () => {
      await api.render(id, ids);
      await afterRender(id, allLabel("render", ids.length, session.photos.length), "spark", plan);
      setSelected(null);
    });
  }

  function askPhoto(text: string, subjectIndices: number[], screen?: Screen, faces?: AskFace[], answerTo?: string, sideAnswer?: SideAnswer) {
    if (!session || !photo) return Promise.resolve(false);
    const id = session.session_id;
    const pid = photo.photo_id;
    setPendingAsk({ photoId: pid, text, subjectIndices });
    return run(async () => {
      try {
        await api.ask(id, pid, text, faces ? [] : subjectIndices, screen, faces, answerTo, sideAnswer);
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
    setPendingAsk({ photoId: pid, text: "", subjectIndices: [] });
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

  // 個人化需求打了就存：只改 plan，不留訊息、不問 AI。存不到就丟出錯誤，
  // 讓輸入框留著那段字、下一個動作前再存一次。
  function quietPrompt(text: string) {
    if (!session || !photo) return Promise.resolve();
    const id = session.session_id;
    const pid = photo.photo_id;
    return api.patchPhotoPlan(id, pid, { user_prompt: text }).then((r) => {
      setSession((s) =>
        s && s.session_id === id ? { ...s, photos: s.photos.map((p) => (p.photo_id === pid ? { ...p, plan: r.plan } : p)) } : s,
      );
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
      multiple
      hidden
      onChange={(e) => {
        const fs = Array.from(e.currentTarget.files || []);
        e.currentTarget.value = "";
        if (fs.length) pickFiles(fs);
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
      // 多張上傳的總覽（還在 chat、還沒進哪一張）也是一段對話，一樣能開新的。
      onNewChat={phase === "session" || (phase === "chat" && batch) ? askNewChat : undefined}
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
        setAllSettings((s) => s && { ...s, ...p });
        for (const d of Object.values(drafts.current)) d.settings = { ...d.settings, ...p };
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
  const readingEl = readingFirst > 0 && (
    <div class="photo-reading" role="status">
      <span class="chat-typing" aria-hidden="true"><span /><span /><span /></span>
      正在讀取 {readingFirst} 張照片…
    </div>
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
      // 進了 session 就只換一張，多張只在上傳頁開。多張頁上是再加幾張，上限算剩下的。
      max={phase === "session" ? 1 : batch ? Math.max(1, MAX_BATCH - batch.length) : MAX_BATCH}
      picked={batch ? null : source?.kind === "demo" ? source.name : null}
      taken={batch ? batch.flatMap((s) => (s.kind === "demo" ? [s.name] : [])) : undefined}
      onPick={pickSamples}
      onClose={() => setSamplesOpen(false)}
    />
  );

  // 上傳頁的對話：單張就是它；多張時是點開的那一張。
  const batchIndex = batch && batchKey ? batch.findIndex((x) => sourceSrc(x) === batchKey) : -1;
  const batchRun = batchKey && batch ? batchRuns[batchKey] : undefined;
  const batchActive = !!batchRun && !batchRun.error;
  const preChatPanel = (
    <ChatPanel
      key={batch ? preFull : undefined}
      class={`home-chat${source ? "" : " is-solo"}`}
      sub={batchIndex >= 0 ? `第 ${batchIndex + 1} 張` : "新對話"}
      photo={source ? { src: thumbSrc(source), onOpen: () => setViewing(true) } : undefined}
      onDropFiles={busy || loadingPhotos ? undefined : batch ? appendFiles : pickFiles}
      footer={
        <>
          <FootBar
            faces={
              !bridgeQuestion && !preWhoAsk && preDisplayPeople.length > 1 &&
                <FacePicker people={preDisplayPeople} pointed={prePointed} onPoint={setPrePointed} />
            }
          />
          <Composer
            // 多張頁上這裡是「再加幾張」，不是換掉這張。
            onFile={batch ? (f) => appendFiles([f]) : pickFile}
            onFiles={batch ? appendFiles : pickFiles}
            attachTitle={batch ? "新增照片" : undefined}
            // Only once there is a photo to replace; before that the
            // greeting's two chips are the choice, right above.
            // 多張頁上這顆是「再加幾張」，一樣問要上傳還是挑範例照片。
            onSample={source ? showSamples : undefined}
            onText={(t) => (preDraft.current = t)}
            disabled={busy || !!preAsk || preHeld || batchActive || loadingPhotos}
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
                    : preWhoAsk && preDisplayPeople.length > 1
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
        previewEvents={[...bridge.events, ...settingsEvents, ...notesHistory]}
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
        busy={busy || batchActive}
        starting={batch ? (batchActive ? batchRun! : null) : starting}
        error={batchRun?.error || error}
        batch={batchIndex >= 0 ? { index: batchIndex, done: !!batchRun?.done, onOpen: () => batchKey && openBatchResult(batchKey) } : undefined}
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
  );

  // 多張的上傳頁：左邊是「我的照片」，最上面那格是總覽，點一張就是那張自己的對話，跟只
  // 上傳一張時一樣；一進來先到總覽。最上面兩顆把還沒開始的每張一起送出。
  // 還在讀後面幾張時，先到的那一張也已經是多張頁，不要先閃一下單張的頁面。
  if (phase === "chat" && batch && (batch.length > 1 || (batch.length === 1 && loadingPhotos))) {
    const anyRun = Object.keys(batchRuns).length > 0;
    const pending = allPending;
    // 總覽的對話裡，「全部一鍵美化」只算現在沒在跑的：按了全部分析、還在分析的那幾張等跑完再給，
    // 但沒開始的、分析好還沒美化的照常給。左邊「我的照片」上那顆一律照常給。
    const renderableAll = allRenderable;
    const renderable = allHold ? allHold.renderable : batch.filter((x, i) => progress[i] !== "running" && needsRender(sourceSrc(x))).length;
    const pickBatch = (s: Source) => (batchRuns[sourceSrc(s)]?.sessionId ? openBatchResult(sourceSrc(s)) : openBatchPhoto(s));
    const lastTurn = overview[overview.length - 1];
    // 一鍵美化正在問怎麼套用：選項就是那兩顆，底下那排先收起來。
    const askOpen = lastTurn?.kind === "ask" && lastTurn.overwrite === undefined;
    const overviewHeld = busy || !!preAsk || loadingPhotos || !!allHold || overview.some((t) => t.kind === "wish" && t.pending);
    // 最早那次上傳；從結果頁接回來的一批沒有這筆，就拿現在這幾張當開頭。
    const first = overview[0];
    const firstUpload = first?.kind === "upload" ? first.srcs : batch.map(sourceSrc);
    const have = new Set(batch.map(sourceSrc));
    // 照片在哪：手機和直拿的平板在對話上面那格，桌機在左邊「我的照片」。
    const spot = drawerLayout ? "上面" : "左邊";
    // 叫人去點照片時講畫面上看得到的名字；手機上那格沒有標題，說「上面」就夠。
    const where = drawerLayout ? "上面" : "左邊的「我的照片」";
    const thumbOf = new Map(batch.map((s) => [sourceSrc(s), thumbSrc(s)]));
    const uploadThumbs = (srcs: string[]) => srcs.filter((s) => have.has(s)).map((s) => thumbOf.get(s) || s);
    // 最底下那句跟著現在的進度，點出接下來能做什麼：還在跑的等它們、沒開始的按哪顆、開始過一些
    // 但還沒做完的是哪幾張。剛上傳什麼都還沒做時，開場白已經講過，不再重複。
    // 只講張數，不列是哪幾張：一批可能很多張，要看哪張在左邊「我的照片」看得到。
    const count = (k: BatchProgress) => progress.filter((x) => x === k).length;
    const [running, todo, analyzed, failed] = (["running", "todo", "analyzed", "error"] as const).map(count);
    const notYet = todo + failed;
    const untouched = progress.every((x) => x === "todo");
    // 桌機上這則底下會放「全部…」那排鍵，句子裡的鍵就說「下面」；照片還是在左邊。
    const labels = { analyze: allLabel("analyze", pending, batch.length), render: allLabel("render", renderableAll, batch.length) };
    const chipsNow: OverviewChips | null = !drawerLayout && (pending || renderable) ? { pending: !!pending, renderable: !!renderable, labels } : null;
    const btn = chipsNow ? "下面" : spot;
    let statusHint = "";
    if (running) {
      statusHint = notYet
        ? `還有 ${notYet} 張沒開始，可以按${btn}的「${labels.analyze}」先看我打算怎麼修，或按${btn}的「${labels.render}」直接美化，也可以從${where}選取照片個別調整。`
        // 在跑的以外，分析好還沒美化的也給下一步，不用等前面那幾張跑完。
        : analyzed
          // 接在「開始了」後面才說「另外」；單獨一則（例如設定改完）先講還有幾張在跑。
          ? `${lastTurn?.kind === "running" ? "另外 " : `${running} 張還在處理，`}${analyzed} 張分析好了，還沒美化，按${btn}的「${labels.render}」就會接著美化，也可以從${where}選取照片個別調整。`
        // 剛說完「開始了」，那句已經講過跑完會說、去哪看進度，不再重複一次。
        : lastTurn?.kind === "running" || lastTurn?.kind === "start"
          ? ""
          : `還有 ${running} 張處理中，完成後我會在這裡說；想看哪張的進度，從${where}選取那張。`;
    } else if (!untouched) {
      // 剛報完「跑完了」，分析好、失敗幾張那句已經講過，這裡只補它沒提到的、還沒開始的那幾張。
      const afterDone = lastTurn?.kind === "done";
      const next = notYet
        ? `按${btn}的「${labels.render}」會把還沒美化的都美化好；只想先看分析，按${btn}的「${labels.analyze}」。`
        : `要接著美化，按${btn}的「${labels.render}」。`;
      if (afterDone) {
        statusHint = !todo && !analyzed && !failed ? "" : `${todo ? `另外還有 ${todo} 張還沒分析。` : ""}${next}`;
      } else {
        const left = [
          todo && `${todo} 張還沒分析`,
          analyzed && `${analyzed} 張分析好了，還沒美化`,
          failed && `${failed} 張沒有成功，可以再送一次`,
        ].filter(Boolean);
        statusHint = left.length ? `目前${left.join("；")}。${next}` : `每張都美化好了，從${where}選取照片就能看結果。`;
      }
    } else if (overview.length > 1) {
      statusHint = `可以按${btn}的「${labels.analyze}」先看我打算怎麼修，或按${btn}的「${labels.render}」直接美化；想怎麼修也可以直接打字告訴我，會套用到每一張。`;
    }
    // 最後一則是「開始了」或「跑完了」時，下一步接在同一句後面講，不另起一則。
    // 剛按了「全部…」、送出的那幾張還沒換成處理中，或照片還在載入時，進度還不準，先不講也不定下來；
    // 不然會把剛送去美化的那張也算成「分析好了，還沒美化」。
    const at = (k: string) => progress[batch.findIndex((x) => sourceSrc(x) === k)];
    // 打字問的那則還在等回話：先不定下來，不然下一步那句會搶在回話前面冒出來。
    const settling = loadingPhotos || !!allHold || (lastTurn?.kind === "wish" && !!lastTurn.pending)
      || (lastTurn?.kind === "start" && lastTurn.keys.some((k) =>
        at(k) === "todo" || (lastTurn.mode === "render" && at(k) === "analyzed")));
    const named = statusHint.includes(`「${labels.analyze}」`) || statusHint.includes(`「${labels.render}」`);
    // 打字問的那則，下一步跟著回話走：沒收下的，後端回話時就附好選項（答完沒問什麼的也補上開始鍵）；
    // 收下的，「全部…」那排接在回話底下。不再另起一則講一樣的話——不然每打一句，最底下就又多一句。
    const snap = lastTurn?.kind === "wish" ? { text: "", chips: chipsNow } : { text: statusHint, chips: named ? chipsNow : null };
    if (lastTurn && !settling && !(lastTurn.id in turnHints.current)) turnHints.current[lastTurn.id] = snap;
    const said = !lastTurn ? (settling ? undefined : snap) : turnHints.current[lastTurn.id];
    const hint = said?.text || "";
    const hintInTurn = !!hint && (lastTurn?.kind === "running" || lastTurn?.kind === "done");
    const hintFor = (t: OverviewTurn) => turnHints.current[t.id]?.text || "";
    // 桌機上「全部…」在左邊「我的照片」，離對話有段距離：講完能做什麼的那一則底下直接放同一組鍵，
    // 不用把視線移過去找。最新那一則的鍵照現在能按的給；之後有新的對話，鍵留在原處定格成紀錄。
    // 手機的照片格就在對話上面，不另外放。
    const chipRow = (c: OverviewChips | null | undefined, live: boolean) => {
      if (!c) return null;
      const has = live ? { pending: !!pending, renderable: !!renderable, labels } : c;
      const lb = has.labels || { analyze: "全部分析", render: "全部一鍵美化" };
      if (live && (askOpen || drawerLayout || !(has.pending || has.renderable))) return null;
      return (
        <div class="chat-choices chat-actions">
          <div class="chat-actions-aux">
            <Chip label="設定" icon="tune" kind="ghost" settled={!live} disabled={overviewHeld} onClick={overviewEditSettings} />
          </div>
          <div class="chat-actions-main">
            {has.pending ? (
              <Chip label={lb.analyze} icon="search" kind={live ? "primary" : undefined} settled={!live} disabled={overviewHeld}
                title="還沒分析過的每張照它自己的需求和設定分析，分析過或正在分析的不重送"
                onClick={() => overviewStart("analyze")} />
            ) : null}
            {has.renderable ? (
              <Chip label={lb.render} icon="spark" kind={live ? "primary" : undefined} settled={!live} disabled={overviewHeld}
                title="還沒美化過的每張都送：沒分析的先分析再美化，分析中或分析好的接著美化，美化過或正在美化的不重送"
                onClick={() => overviewStart("render")} />
            ) : null}
          </div>
        </div>
      );
    };
    // 開場白也一樣：第一次出現時照當下定下來，鍵跟在它底下。
    const introKey = `intro-${batchEpoch.current}`;
    if (!loadingPhotos && !(introKey in turnHints.current))
      turnHints.current[introKey] = { text: "", chips: chipsNow, pending, renderable: !!renderable };
    const intro = turnHints.current[introKey];
    const introPending = intro ? intro.pending : pending;
    const introBtn = (intro ? intro.chips : chipsNow) ? "下面" : spot;
    const introLive = !hint && overview.every((t) => t === first && t.kind === "upload");
    const settingsSaid = (t: Extract<OverviewTurn, { kind: "settings" }>) => {
      const changed = caps && t.from && t.settings ? settingsChanges(caps, t.from, t.settings, t.model) : [];
      return changed.length ? `好，${changed.join("、")}，會套用到每張還沒美化的照片。` : "設定沒有改動。";
    };
    // 總覽：沒有中間那格預覽，對話直接佔滿。桌機的照片和「全部…」都在左邊「我的照片」，
    // 對話上面不再擺一份；「我的照片」收成抽屜時，才在對話上面放成果格。
    const overviewPanel = !source && (
      <ChatPanel
        class="home-chat is-overview"
        sub="總覽"
        top={drawerLayout &&
          <BatchOverviewGrid
            key={sourceSrc(batch[0])}
            sources={batch}
            loading={photoLoad ? photoLoad.total - photoLoad.done : 0}
            runs={batchRuns}
            live={batchLive}
            failed={batchFailed}
            wished={batchWished}
            pending={pending}
            renderable={renderableAll}
            disabled={overviewHeld}
            // 一鍵美化正在問怎麼套用：先回答那一題，「全部…」那排等一下。
            actionsHeld={overviewHeld || askOpen}
            onPick={pickBatch}
            onStartAll={overviewStart}
            onSettings={overviewEditSettings}
            onMore={() => setRevealDrawer((n) => n + 1)}
            onAdd={busy || loadingPhotos ? undefined : appendFiles}
            onSamples={busy || loadingPhotos ? undefined : showSamples}
            onClear={busy || anyRun || loadingPhotos ? undefined : clearBatch}
            onRemove={busy || loadingPhotos ? undefined : removeFromBatch}
          />
        }
        onDropFiles={overviewHeld ? undefined : appendFiles}
        footer={
          <Composer
              onFile={(f) => appendFiles([f])}
              onFiles={appendFiles}
              attachTitle="新增照片"
              onSample={showSamples}
              disabled={overviewHeld || !(pending || renderable || running)}
              onSend={async (text) => {
                if (!text) return false;
                void overviewSay(text);
                return true;
              }}
              clearKey={preClear}
              placeholder={pending ? ["想怎麼美化這幾張？會套用到每一張", "想怎麼美化這幾張？"]
                : renderable ? ["還沒美化的這幾張想怎麼修？會套用到每一張", "還沒美化的這幾張想怎麼修？"]
                // 輸入框跟著現在的狀態走：還有在跑的說在跑，全部好了才說好了。
                : running ? `還有 ${running} 張處理中，從${where}選取照片看進度`
                : `每張都美化好了，從${where}選取照片看結果`}
            />
        }
      >
        <UserMsg>
          <BatchUploadBubble
            srcs={uploadThumbs(firstUpload)}
            text={`上傳了 ${photoLoad && first && photoLoad.entry === first.id ? photoLoad.total : firstUpload.length} 張照片`}
          />
        </UserMsg>
        {/* 先說收到幾張、這頁是什麼：一次處理全部的按鈕在上面的照片格底下，這裡只指過去，
            再講怎麼回來——使用者第一次看到總覽，要先知道這裡講的話會落到每一張上。 */}
        {photoLoad && first && photoLoad.entry === first.id ? <PhotoLoadMsg {...photoLoad} /> : <AssistantMsg>
          <p class="chat-text">收到你上傳的 {firstUpload.length} 張照片了。</p>
          {/* 「設定」跟著「全部…」出現，有那兩顆才提；都分析過了就只剩美化的設定好換。 */}
          <p class="chat-text">{introPending
            ? `這裡是這批照片的總覽：${introBtn}可以一次分析或美化全部，也可以先按「設定」統一換引擎或長寬比；只想改某一張，點那張就好。想怎麼修也可以直接打字告訴我，會套用到還沒開始的每一張。`
            : (intro ? intro.renderable : renderable)
              ? `這裡是這批照片的總覽：${introBtn}可以一次美化全部，也可以先按「設定」統一換美化引擎或長寬比；只想改某一張，從${where}選取那張就好。`
              : `這裡是這批照片的總覽：${introBtn}可以一次處理全部，也可以從${where}選取某一張個別調整。`}</p>
          <p class="chat-text chat-muted">之後想回來這裡，點{photosSpot()}「我的照片」最上面的「總覽」。</p>
          {chipRow(intro?.chips, introLive)}
        </AssistantMsg>}
        {overview.map((t, i) => {
          const latest = i === overview.length - 1;
          if (t === overview[0] && t.kind === "upload") return null;
          if (t.kind === "upload") return (
            <div key={t.id}>
              <UserMsg><BatchUploadBubble srcs={uploadThumbs(t.srcs)} text={`新增了 ${photoLoad?.entry === t.id ? photoLoad.total : t.srcs.length} 張照片`} /></UserMsg>
              {photoLoad?.entry === t.id ? <PhotoLoadMsg {...photoLoad} /> : <AssistantMsg>
                <p class="chat-text">{`好，加進來了，${t.srcs.length > 1 ? "這幾張" : "這張"}加到${where}裡了。`}</p>
              </AssistantMsg>}
            </div>
          );
          if (t.kind === "remove") return (
            <AssistantMsg key={t.id}>
              <p class="chat-text chat-muted">{`移除了 ${t.count} 張，現在一共 ${t.left} 張。`}</p>
            </AssistantMsg>
          );
          if (t.kind === "running") {
            const n = t.keys.filter((k) => have.has(k)).length;
            return n ? (
              <AssistantMsg key={t.id}>
                <p class="chat-text">{`有 ${n} 張開始${t.verb}了，完成後我會在這裡說；想看哪張的進度，從${where}選取那張。${hintFor(t)}`}</p>
                {chipRow(turnHints.current[t.id]?.chips, latest)}
              </AssistantMsg>
            ) : null;
          }
          if (t.kind === "done") {
            const parts = [
              t.rendered && `${t.rendered} 張美化好了`,
              t.analyzed && `${t.analyzed} 張分析好了`,
              t.failed && `${t.failed} 張沒有成功`,
            ].filter(Boolean);
            return (
              <AssistantMsg key={t.id}>
                <p class="chat-text">{`剛剛送出的都處理完成了${parts.length ? `，目前 ${parts.join("、")}` : ""}。${hintFor(t).replace(/。$/, "；")}${t.failed
                  ? `沒成功的在${where}會標「失敗」，選取那張可以看原因。`
                  : `想看哪張的結果，從${where}選取那張。`}`}</p>
                {chipRow(turnHints.current[t.id]?.chips, latest)}
              </AssistantMsg>
            );
          }
          if (t.kind === "wish") return (
            <div key={t.id} class="note-turn">
              <AskBubble text={t.text} />
              {t.pending ? (
                <AssistantMsg atomic><div class="chat-typing" role="status" aria-label="思考中"><span /><span /><span /></div></AssistantMsg>
              ) : t.kept === false ? (
                // 沒收下：問題、閒聊、撤回只回話；它問回來的就把答案排在底下，點了當成打的下一句。
                // 「幫我分析」「一鍵美化」就是總覽那兩顆鍵，點了直接按。開始了的那句由「開始了」那則講。
                t.reply ? (
                  <AssistantMsg>
                    <p class="chat-text">{t.reply}</p>
                    {!!t.options?.length && (() => {
                      const nx = overview[i + 1];
                      return (
                        <AnswerList options={t.options} picked={!latest && nx?.kind === "wish" ? nx.text : null} open={latest} busy={overviewHeld}
                          onPick={(o) => { const m = overviewStartOf(o); return m ? overviewStart(m) : void overviewSay(o); }}
                          onOwn={(o) => void overviewSay(o)} />
                      );
                    })()}
                  </AssistantMsg>
                ) : null
              ) : (
                <AssistantMsg>
                  <p class="chat-text">
                    {t.count && t.reply ? t.reply : <>
                    {t.line && `好，記下了：${t.line}。`}
                    {t.count
                      ? `${t.line ? "" : "好，"}這句會套用在 ${t.count} 張還沒美化的照片上。${t.skipped ? `已經在美化或美化好的 ${t.skipped} 張不會套用。` : ""}只想改某一張，從${where}選取那張調整。`
                      : "每張都已經在美化或美化好了，這句不會套用；要改哪一張，到那張的結果頁說就好。"}
                    </>}
                  </p>
                  {t.gone && <p class="chat-text chat-muted">這句後來撤回或被新的需求取代了，已經從每張拿掉。</p>}
                  {!t.gone && chipRow(turnHints.current[t.id]?.chips, latest)}
                </AssistantMsg>
              )}
            </div>
          );
          if (t.kind === "settings") return (
            <div key={t.id} class="settings-turn">
              <UserMsg><ClickBubble icon="tune" label="設定" /></UserMsg>
              {latest && caps && settings ? (
                <AssistantMsg>
                  <p class="chat-text">{t.model
                    ? "好，調整下面的設定，會套用到每張還沒美化的照片；分析引擎只有還沒分析的用得到。"
                    : "好，調整下面的設定，會套用到每張還沒美化的照片。"}</p>
                  <SettingsEditor caps={caps} settings={settings} onChange={overviewSettings} hideModel={!t.model} />
                </AssistantMsg>
              ) : (
                // 這輪過了就不再擺設定卡，用一句話講改了什麼。
                <AssistantMsg><p class="chat-text">{settingsSaid(t)}</p></AssistantMsg>
              )}
            </div>
          );
          if (t.kind === "ask") return (
            <div key={t.id}>
              <UserMsg><ClickBubble icon="spark" label={t.label || "全部一鍵美化"} /></UserMsg>
              <AssistantMsg>
                <p class="chat-text">有幾張講了需求，要整體美化，還是僅依需求修改？在單張頁已經選過的，照它自己選的。</p>
                <p class="chat-text chat-muted">
                  {WITH_ANALYSIS_LABEL}：照需求修改，光線、人像、構圖也一起調整。{ONLY_PROMPT_LABEL}：只動寫到的地方，其他保持原樣。
                </p>
                <div class="chat-choices">
                  {[
                    { label: WITH_ANALYSIS_LABEL, overwrite: false },
                    { label: ONLY_PROMPT_LABEL, overwrite: true },
                  ].map((o) => (
                    <Chip
                      label={o.label}
                      kind={t.overwrite === o.overwrite ? "picked" : undefined}
                      settled={t.overwrite !== undefined}
                      disabled={overviewHeld}
                      onClick={() => overviewRenderMode(o.overwrite)}
                    />
                  ))}
                </div>
              </AssistantMsg>
            </div>
          );
          // 選了怎麼套用就接著開始，按鈕上面那句已經是使用者的選擇，不再重複一顆。
          const asked = overview[i - 1]?.kind === "ask";
          return (
            <div key={t.id}>
              {!asked && <UserMsg><ClickBubble icon={t.mode === "analyze" ? "search" : "spark"} label={t.label || (t.mode === "analyze" ? "全部分析" : "全部一鍵美化")} /></UserMsg>}
              <AssistantMsg>
                <p class="chat-text">
                  {`好，${t.count} 張開始${t.mode === "analyze" ? "分析" : t.overwrite ? "調整" : "美化"}了，全部完成後我會在這裡說；想看哪張的進度，從${where}選取那張。`}
                </p>
              </AssistantMsg>
            </div>
          );
        })}
        {/* 換一句就換一個節點，才會跟新進來的訊息一樣接在後面打字，不會原地換字。 */}
        {!askOpen && hint && !hintInTurn && (
          <AssistantMsg key={hint}><p class="chat-text">{hint}</p>{chipRow(said?.chips, true)}</AssistantMsg>
        )}
        {/* 有照片還在跑，對話最底下就一直點點點，讓人知道還在做事；全部跑完、報完「跑完了」就收掉。 */}
        {running > 0 && (
          <AssistantMsg atomic><div class="chat-typing" role="status" aria-label={`還有 ${running} 張在處理`}><span /><span /><span /></div></AssistantMsg>
        )}
        {error && <ErrorMsg text={error} />}
      </ChatPanel>
    );
    return (
      <>
        {header}
        <main>
          <StepIndicator step={1} state="done" pre />
          <section id="upload-section" class="section visible">
            <div class="container upload-container has-flow">
              <div class="upload-grid">
                <div class="upload-content-row is-chat">
                  <BatchPreSidebar
                    sources={batch}
                    loading={photoLoad ? photoLoad.total - photoLoad.done : 0}
                    runs={batchRuns}
                    wished={batchWished}
                    current={batchKey}
                    live={batchLive}
                    failed={batchFailed}
                    pending={pending}
                    renderable={renderableAll}
                    disabled={!!preAsk || busy || loadingPhotos}
                    onPick={pickBatch}
                    overview={!source}
                    onOverview={openOverview}
                    // 在總覽按的，跟總覽底下那兩顆一樣記進總覽的對話。
                    onStartAll={(m) => (source ? startAllBatch(m) : overviewStart(m))}
                    // 一鍵美化正在問怎麼套用：先回答那一題，「全部…」等一下。剛按過「全部…」也等一下。
                    actionsHeld={(!source && askOpen) || !!allHold}
                    onSettings={source ? undefined : overviewEditSettings}
                    onRemove={busy || loadingPhotos ? undefined : removeFromBatch}
                    onClear={busy || anyRun || loadingPhotos ? undefined : clearBatch}
                    onAdd={busy || loadingPhotos ? undefined : appendFiles}
                    onSamples={busy || loadingPhotos ? undefined : showSamples}
                    reveal={revealDrawer}
                  />
                  {source ? (
                    <>
                      <PreStage source={source} />
                      {preChatPanel}
                    </>
                  ) : (
                    overviewPanel
                  )}
                </div>
              </div>
            </div>
          </section>
          {hiddenInput}
        </main>
        {drawerEl}{prefsEl}
        {samplesEl}
        {readingEl}{confirmEl}
        {askEl}
        <PhotoViewer src={viewing && source ? fullSrc(source) : null} onClose={closeViewer} />
      </>
    );
  }

  if (submitting) {
    return (
      <>
        {header}
        <main>
          <StepIndicator step={1} state="busy" pre />
          <section id="upload-section" class="section visible">
            <div class="container upload-container has-flow">
              <div class="upload-grid">
              <div class="upload-content-row is-chat">
                <PreStage source={submitting.sources[0]} />
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
        {drawerEl}{prefsEl}{readingEl}{confirmEl}{askEl}
      </>
    );
  }

  if (phase === "session" && session && photo) {
    // 多張上傳裡開始過的一張：左邊照樣是那批「我的照片」，點了切到別張的結果或回上傳頁。
    const batchHere = Object.keys(batchRuns).find((k) => batchRuns[k].sessionId === session.session_id) || null;
    return (
      <>
        {header}
        <main>
          <SessionView
            key={photo.photo_id}
            sidebar={
              batch && batchHere ? (
                <BatchPreSidebar
                  sources={batch}
                  loading={photoLoad ? photoLoad.total - photoLoad.done : 0}
                  runs={batchRuns}
                  wished={batchWished}
                  current={batchHere}
                  live={{ ...batchLive, [batchHere]: photo }}
                  failed={batchFailed}
                  pending={allPending}
                  renderable={allRenderable}
                  disabled={busy}
                  actionsHeld={!!allHold}
                  onPick={(s) => (batchRuns[sourceSrc(s)]?.sessionId ? openBatchResult(sourceSrc(s)) : backToBatch(s))}
                  onOverview={openOverview}
                  onStartAll={startAllBatch}
                  hop={hopTrigger}
                  onHopped={() => setHopTrigger(false)}
                />
              ) : session.photos.length > 1 && (
                <BatchSidebar
                  photos={session.photos}
                  messages={session.messages || []}
                  current={photo.photo_id}
                  hop={hopTrigger}
                  onHopped={() => setHopTrigger(false)}
                  onPick={(id) => {
                    setActiveId(id);
                    setSelected(null);
                  }}
                  busy={busy}
                  onRenderAll={renderAll}
                />
              )
            }
            caps={caps}
            debugUi={debugUi}
            session={session}
            photo={photo}
            analysis={analyses[photo.photo_id]?.analysis || null}
            analysisPending={analysisReady && !analyses[photo.photo_id]}
            selected={selected}
            pendingAsk={pendingAsk?.photoId === photo.photo_id ? pendingAsk : null}
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
            onQuietPrompt={quietPrompt}
            onPost={postFromChat}
            onAnalyze={analyzePhoto}
            panelMode={{ href: urls.oldUi(photo.case_id), busy: isBusy(photo), onLeave: toOldUi }}
            onResolve={resolveProposal}
          />
        </main>
        {drawerEl}{prefsEl}
        {samplesEl}
        {readingEl}{confirmEl}
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
      max={MAX_BATCH}
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
        {phase === "chat" && source && <StepIndicator step={1} state="done" pre />}
        <section id="upload-section" class="section visible">
          <div class={`container upload-container${phase === "chat" && source ? " has-flow" : ""}`}>
            <div class="upload-grid">
              {/* is-chat drops the 360px styles.css reserves on the right for the
                  fixed gallery rail -- with the rail unmounted it is padding
                  around nothing, and it was what pushed the panel off centre. */}
              <div class={`upload-content-row${phase === "chat" ? " is-chat" : ""}`}>
                {phase === "home" ? (
                  // 範例照片點了但沒按「使用所選照片」就直接開始對話，就拿點的那張。
                  <HomeIntro onStart={startFromSelection} />
                ) : (
                  <>
                  {/* Nothing to stage until a photo is picked: before that the
                      conversation is the whole page, centred, and ChatPanel
                      itself is the drop target. */}
                  {source && <PreStage source={source} />}
                  {preChatPanel}
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
      {readingEl}{confirmEl}
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

// 對話裡指「我的照片」在哪：手機和直拿的平板收成左上角那顆（跟 chat.css 的抽屜斷點同一組），
// 桌機是左邊那一欄。
const DRAWER_LAYOUT = "(max-width:900px),(max-width:1200px) and (orientation:portrait)";
function photosSpot() {
  return matchMedia(DRAWER_LAYOUT).matches ? "左上角" : "左邊";
}

// 跟著視窗變：轉平板或拉寬視窗時，版面和對話裡的「上面／左邊」一起換。
function useMedia(query: string) {
  const [on, setOn] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const mq = matchMedia(query);
    const sync = () => setOn(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [query]);
  return on;
}

// 多張上傳頁最左邊那欄「我的照片」：跟進了對話之後那欄同一套 class 和樣子，
// 點一張就打開那張的對話。最上面兩顆把還沒開始的每張照它自己的需求和設定送出；
// 標題列那顆 × 全部清除（有一張開始了就不出現），還沒開始的每張右上角也有 × 拿掉。
// 那批照片列表捲到哪：結果頁換一張就整個重掛（SessionView 以照片為 key），
// 列表跟著重來，不記的話每點一張就跳回最上面。記在哪一批（第一張）底下，
// 換一批就從頭開始。
let batchListScroll = { batch: "", top: 0 };

// 選了很多張、還在一張一張讀的時候，總覽裡回的那則：讀了幾張、百分之多少。讀完換成平常那段。
function PhotoLoadMsg({ done, total }: { done: number; total: number }) {
  const pct = Math.round((done / total) * 100);
  return (
    <AssistantMsg atomic>
      <p class="chat-text">收到你選的 {total} 張照片，正在上傳…</p>
      <div class="chat-upload-progress" role="progressbar" aria-label="上傳進度" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <p class="chat-text chat-muted">{`${done} / ${total} 張 · ${pct}%`}　上傳完就可以開始了，先稍等一下。</p>
    </AssistantMsg>
  );
}

// 讀好一張放上來的那格淡入一下。看過的記在模組裡：從單張那頁回總覽、側欄重掛時不會整排再閃一次。
const seenTiles = new Set<string>();
function useFreshTiles() {
  const fresh = useRef(new Set<string>()).current;
  return (key: string) => {
    if (!seenTiles.has(key)) {
      seenTiles.add(key);
      fresh.add(key);
    }
    return fresh.has(key);
  };
}

// 還在讀的照片先佔好位置，一格一格往後錯開閃，看得出後面還有幾張要來。
const ghostDelay = (i: number) => ({ animationDelay: `${(i % 6) * 90}ms` });

function BatchPreSidebar(props: {
  sources: Source[];
  // 選了還在讀、還沒放上來的張數，先放佔位格。
  loading?: number;
  runs: Record<string, BatchRun>;
  wished: (key: string) => boolean;
  current: string | null;
  pending: number;
  // 一鍵美化送得出去的張數：還沒開始的，加上分析好還沒美化的。
  renderable?: number;
  disabled: boolean;
  onPick: (s: Source) => void;
  // 列表最上面那格「總覽」：回到對全部一起下指令的那頁。overview 是現在就在總覽。
  onOverview?: () => void;
  overview?: boolean;
  onStartAll: (mode: "analyze" | "render") => void;
  // 總覽時「全部…」旁邊那顆「設定」：跟著「全部…」出現，統一換引擎或長寬比。
  onSettings?: () => void;
  // 「全部…」暫時不能按，但照片還能點（總覽正在問一鍵美化怎麼套用）。
  actionsHeld?: boolean;
  onRemove?: (i: number) => void;
  onClear?: () => void;
  onAdd?: (files: File[]) => void;
  onSamples?: () => void;
  // 結果頁上正在看的那張，狀態照它在 session 裡真的跑到哪。
  live?: Record<string, Photo>;
  // 最後一次美化沒有成功的那幾張。
  failed?: Record<string, boolean>;
  // 抽屜收起來時那顆按鈕跳一下（從最近照片打開時），跳完呼叫 onHopped。
  hop?: boolean;
  onHopped?: () => void;
  // 每加一就從外面把抽屜打開一次（總覽照片格的「顯示全部」）：那顆先亮一下再從它展開，
  // 收起來時那顆再跳幾下，讓人知道照片收在這裡、之後從這顆開闔。
  reveal?: number;
}) {
  const { sources, runs, current, pending, live } = props;
  const isFresh = useFreshTiles();
  const [open, setOpen] = useState(false);
  const [ping, setPing] = useState(false);
  const [home, setHome] = useState(false);
  const revealed = useRef(false);
  // 重掛時（例如從結果頁回來）不要因為帶著舊的數字又自己打開。
  const seenReveal = useRef(props.reveal);
  useEffect(() => {
    if (props.reveal === seenReveal.current) return;
    seenReveal.current = props.reveal;
    revealed.current = true;
    setHome(false);
    setPing(true);
    const t = setTimeout(() => {
      setPing(false);
      setOpen(true);
    }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 220);
    return () => clearTimeout(t);
  }, [props.reveal]);
  const close = () => {
    setOpen(false);
    if (!revealed.current) return;
    revealed.current = false;
    setHome(true);
  };
  const list = useRef<HTMLDivElement>(null);
  const batchKey = sources.length ? sourceSrc(sources[0]) : "";
  // 重掛時在畫出來之前捲回原本的位置，不會先閃一下最上面。
  useLayoutEffect(() => {
    if (list.current && batchListScroll.batch === batchKey) list.current.scrollTop = batchListScroll.top;
  }, []);
  // 正在看的那張不在看得到的範圍裡（例如一開就停在後面那張），就把列表捲到
  // 剛好露出它，跟 BatchSidebar 一樣只動列表自己的 scrollTop。
  useEffect(() => {
    const box = list.current;
    const on = box?.querySelector<HTMLElement>(".batch-sidebar-item.selected");
    if (!box || !on) return;
    const top = on.offsetTop - box.offsetTop;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (top + on.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top + on.offsetHeight - box.clientHeight;
  }, [current, open]);
  const full = sources.length >= MAX_BATCH;
  const pick = (s: Source) => {
    close();
    props.onPick(s);
  };
  const renderable = props.renderable ?? pending;
  // 各張到哪了：列表上面的篩選用這份，跟總覽照片格的篩選同樣的字，照要處理的先後排。最前面固定
  // 一顆「全部」；點一顆只列那種狀態的照片，再點一次或點「全部」回來，
  // 那種一張都不剩時自己回到全部。
  const stage = (s: Source) => batchProgress(runs[sourceSrc(s)], live?.[sourceSrc(s)] || null, !!props.failed?.[sourceSrc(s)]);
  const progress = GRID_GROUPS.map((g) => ({ g, n: sources.filter((x) => stage(x) === g.key).length })).filter((x) => x.n);
  const [picked, setFilter] = useState<BatchProgress | "all">("all");
  const filter = progress.some((x) => x.g.key === picked) ? picked : "all";
  const goOverview = () => {
    setOpen(false);
    props.onOverview!();
  };
  const held = props.disabled || !!props.actionsHeld;
  const startAll = (mode: "analyze" | "render") => {
    close();
    props.onStartAll(mode);
  };
  return (
    <>
      <div class={`batch-sidebar-backdrop${open ? " drawer-open" : ""}`} onClick={close} />
      <aside class={`batch-sidebar${open ? " drawer-open" : ""}${props.overview ? " is-overview" : ""}`} id="batch-sidebar">
        <button type="button" class="batch-sidebar-close-btn" aria-label="收起照片列表" onClick={close}>
          <Icon name="x" size={16} />
        </button>
        <div class="batch-sidebar-header">
          <h2 class="batch-sidebar-title">我的照片</h2>
          <div class="batch-sidebar-actions">
          {props.onAdd && <BatchAddButton full={full} onAdd={props.onAdd} onSamples={props.onSamples} onBefore={() => setOpen(false)} />}
          {props.onClear && (
            <button type="button" class="batch-download-all-btn" title="全部清除" aria-label="全部清除" data-danger onClick={props.onClear}>
              <Icon name="trash" size={14} />
            </button>
          )}
          </div>
        </div>
        {/* 「總覽」不是一張照片，所以不做成方塊混在照片裡：一格橫的，放在照片列表上面。
            整批的東西都收在這格裡：上面整列回總覽，下面是「全部…」那幾顆，一看就知道是對整批的。
            張數只寫在這裡，標題列不再寫一次。 */}
        <div class={`batch-overview-card${props.overview ? " is-on" : ""}`}>
          {props.onOverview && (
            <button
              type="button"
              aria-current={props.overview ? "page" : undefined}
              title="回到總覽，全部一起分析、美化或調整設定"
              class={`batch-overview-entry${props.overview ? " is-on" : ""}`}
              disabled={props.disabled && !props.overview}
              onClick={goOverview}
            >
              <span class={`batch-overview-mosaic is-${Math.min(sources.length, 4)}`} aria-hidden="true">
                {sources.slice(0, 4).map((s) => <img key={sourceSrc(s)} src={thumbSrc(s)} alt="" />)}
              </span>
              <span class="batch-overview-text">
                <b>總覽 · {sources.length} 張</b>
              </span>
              <Icon name="next" size={14} />
            </button>
          )}
          {pending || renderable ? (
            // 每次進到總覽重新掛一次，那兩顆就再跳一下，提示這裡可以一次處理全部。
            <div key={props.overview ? "overview" : "single"} class={`batch-sidebar-all${props.overview && !held ? " is-nudge" : ""}`}>
              {props.onSettings ? <Chip
                label="設定"
                icon="tune"
                kind="ghost"
                disabled={held}
                onClick={() => {
                  close();
                  props.onSettings!();
                }}
              /> : null}
              {pending ? <Chip
                label={allLabel("analyze", pending, props.sources.length)}
                icon="search"
                kind="primary"
                disabled={held}
                title="還沒分析過的每張照它自己的需求和設定分析，分析過或正在分析的不重送"
                onClick={() => startAll("analyze")}
              /> : null}
              {renderable ? <Chip
                label={allLabel("render", renderable, props.sources.length)}
                icon="spark"
                kind="primary"
                disabled={held}
                title="還沒美化過的每張都送：沒分析的先分析再美化，分析中或分析好的接著美化，美化過或正在美化的不重送"
                onClick={() => startAll("render")}
              /> : null}
            </div>
          ) : null}
        </div>
        {/* 篩的是底下的列表，就貼在列表上面、不跟著捲；只有一種狀態時沒得篩，不出現。 */}
        {progress.length > 1 && (
          <div class="batch-grid-filters batch-sidebar-filters" role="group" aria-label="依狀態篩選">
            <button type="button" class={`batch-grid-filter${filter === "all" ? " is-on" : ""}`} aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
              全部 {sources.length}
            </button>
            {progress.map((x) => (
              <button
                key={x.g.key}
                type="button"
                class={`batch-grid-filter is-${x.g.key}${filter === x.g.key ? " is-on" : ""}`}
                aria-pressed={filter === x.g.key}
                onClick={() => setFilter(filter === x.g.key ? "all" : x.g.key)}
              >
                {x.g.label} {x.n}
              </button>
            ))}
          </div>
        )}
        <div
          class="batch-sidebar-list"
          ref={list}
          role="tablist"
          aria-label="選好的照片"
          onScroll={(e) => (batchListScroll = { batch: batchKey, top: (e.currentTarget as HTMLDivElement).scrollTop })}
        >
          {sources.map((s, i) => {
            if (filter !== "all" && stage(s) !== filter) return null;
            const key = sourceSrc(s);
            const r = runs[key];
            const p = live?.[key] || null;
            const state = batchItemState(r, p, props.wished(key), !!props.failed?.[key]);
            const on = key === current;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={on}
                aria-label={`第 ${i + 1} 張，${state.text}`}
                class={`history-cube batch-sidebar-item${state.key ? ` ${state.key}` : ""}${on ? " selected" : ""}${isFresh(key) ? " is-fresh" : ""}`}
                disabled={props.disabled && !on}
                onClick={() => pick(s)}
              >
                <span class="rc-media">
                  <img src={p?.active_version ? urls.resultThumb(p.active_version.job_id, p.active_version.run) : thumbSrc(s)} alt="" />
                  {state.key === "uploading" || state.key === "processing" || state.key === "analyzing" ? <span class="rc-spinner" /> : null}
                </span>
                <span class="rc-badge">{state.text}</span>
                {props.onRemove && !r && <SidebarRemove index={i} onRemove={props.onRemove} />}
              </button>
            );
          })}
          {filter === "all" && Array.from({ length: props.loading || 0 }, (_, i) => (
            <span key={`ghost-${i}`} class="history-cube batch-sidebar-item is-ghost" aria-hidden="true">
              <span class="rc-media" style={ghostDelay(i)} />
            </span>
          ))}
        </div>
      </aside>
      <button
        type="button"
        class={`batch-drawer-trigger${props.hop ? " is-hop" : ""}${ping ? " is-ping" : ""}${home ? " is-home" : ""}`}
        aria-label={`我的照片（${sources.length} 張）`}
        onClick={() => setOpen(true)}
        onAnimationEnd={() => props.onHopped?.()}
      >
        <Icon name="photos" size={18} />
        <span class="batch-drawer-trigger-count">{sources.length}</span>
      </button>
    </>
  );
}

// 總覽最上面的成果格：每張現在的樣子（美化過的放目前版本）和狀態，點了到那張。
// 要處理的排前面（失敗、分析好還沒美化、處理中、還沒開始），美化好的最後；上面的
// 篩選只看某一種。格子固定只佔一兩列，對話的高度才不會被擠掉：放不下的由
// 「顯示全部」打開一個蓋住整頁的照片面板，在那裡挑。
// 用詞跟「最近照片」的磚一樣（home.tsx 的 SESSION_BADGES）：分析好還沒美化的叫待美化。
const GRID_GROUPS: { key: BatchProgress; label: string }[] = [
  { key: "error", label: "失敗" },
  { key: "analyzed", label: "待美化" },
  { key: "running", label: "處理中" },
  { key: "todo", label: "未開始" },
  { key: "done", label: "美化完成" },
];

// 每格左下的狀態：跟「最近照片」同一顆（.session-tile-badge），同樣的字、符號和顏色。
// 處理中照實際在做的說分析中、美化中或上傳中；還沒開始的沒有對應的磚，用不帶顏色的那顆。
function gridBadge(progress: BatchProgress, state: { key: string; text: string }): { cls: string; text: string } {
  switch (progress) {
    case "error": return { cls: "is-failed", text: "失敗" };
    case "analyzed": return { cls: "is-ready", text: "待美化" };
    case "done": return { cls: "is-done", text: "美化完成" };
    case "running":
      return state.key === "processing" ? { cls: "is-rendering", text: "美化中" }
        : state.key === "uploading" ? { cls: "is-analyzing", text: "上傳中" }
        : { cls: "is-analyzing", text: "分析中" };
    default: return { cls: "", text: "未開始" };
  }
}

// 收起時顯示幾列：手機上或矮的畫面一列，對話才留得下位置。
const GRID_ROWS = 2;
const GRID_ROWS_NARROW = 1;
const GRID_NARROW = 600;
const GRID_SHORT = 900;
// 格子最小寬度：手機上小一點排得下四欄，寬畫面放大一點，不會擠成一長排小格。
const GRID_MIN_CELL = 72;
const GRID_MIN_CELL_WIDE = 104;
const GRID_GAP = 8;
// 「我的照片」收成左邊抽屜的寬度，跟 chat.css 那兩段 @media 同一條。
const DRAWER_MEDIA = "(max-width: 900px), (max-width: 1200px) and (orientation: portrait)";

function BatchOverviewGrid(props: {
  sources: Source[];
  runs: Record<string, BatchRun>;
  live: Record<string, Photo>;
  failed: Record<string, boolean>;
  wished: (key: string) => boolean;
  // 選了還在讀、還沒放上來的張數，先放佔位格。
  loading?: number;
  pending: number;
  renderable: number;
  disabled: boolean;
  actionsHeld: boolean;
  onPick: (s: Source) => void;
  onStartAll: (mode: "analyze" | "render") => void;
  onSettings: () => void;
  // 手機上「我的照片」是抽屜：「顯示全部」改成打開抽屜，不另外開一個面板。
  onMore?: () => void;
  // 跟「我的照片」標題列那幾顆一樣：新增、全部清除、每格的 ×。不給就不出現（例如正在跑）。
  onAdd?: (files: File[]) => void;
  onSamples?: () => void;
  onClear?: () => void;
  onRemove?: (i: number) => void;
}) {
  const [picked, setFilter] = useState<BatchProgress | "all">("all");
  const [open, setOpen] = useState(false);
  const [cols, setCols] = useState(6);
  const isFresh = useFreshTiles();
  // 成品縮圖載不出來的那幾張（job 的檔案可能比對話先過期）改放原圖，跟最近照片的磚一樣。
  const [broken, setBroken] = useState<Set<string>>(() => new Set());
  const [rows, setRows] = useState(GRID_ROWS);
  const grid = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = grid.current;
    if (!el) return;
    const fit = () => {
      const narrow = el.clientWidth < GRID_NARROW;
      const cell = narrow ? GRID_MIN_CELL : GRID_MIN_CELL_WIDE;
      setCols(Math.max(3, Math.floor((el.clientWidth + GRID_GAP) / (cell + GRID_GAP))));
      setRows(narrow || window.innerHeight < GRID_SHORT ? GRID_ROWS_NARROW : GRID_ROWS);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const items = props.sources.map((s, i) => {
    const key = sourceSrc(s);
    const r = props.runs[key];
    const p = props.live[key] || null;
    const failed = !!props.failed[key];
    return { s, key, no: i + 1, p, progress: batchProgress(r, p, failed), state: batchItemState(r, p, props.wished(key), failed) };
  });
  const count = (k: BatchProgress) => items.filter((x) => x.progress === k).length;
  // 選的那種一張都不剩了（例如失敗的重送了），就回到全部，不留一個空的格子。
  const filter = picked === "all" || count(picked) ? picked : "all";
  const rank = (k: BatchProgress) => GRID_GROUPS.findIndex((g) => g.key === k);
  const listed = items
    .filter((x) => filter === "all" || x.progress === filter)
    .sort((a, b) => rank(a.progress) - rank(b.progress) || a.no - b.no);
  const cap = cols * rows;
  // 佔位格只在看全部時放，跟真的照片一起算進收起來的那一兩列。
  const ghosts = filter === "all" ? props.loading || 0 : 0;
  const folded = listed.length + ghosts > cap;
  const shown = folded ? listed.slice(0, cap - 1) : listed;
  const ghostsShown = folded ? Math.max(0, cap - 1 - shown.length) : ghosts;
  const ghostCells = (n: number) => Array.from({ length: n }, (_, i) => (
    <span key={`ghost-${i}`} class="batch-grid-cell is-ghost" style={ghostDelay(i)} aria-hidden="true" />
  ));
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [open]);
  const tools = (props.onAdd || props.onClear) && (
    <div class="batch-sidebar-actions batch-grid-tools">
      {props.onAdd && <BatchAddButton full={items.length >= MAX_BATCH} onAdd={props.onAdd} onSamples={props.onSamples} onBefore={() => setOpen(false)} end />}
      {props.onClear && (
        <button type="button" class="batch-download-all-btn" title="全部清除" aria-label="全部清除" data-danger onClick={props.onClear}>
          <Icon name="trash" size={14} />
        </button>
      )}
    </div>
  );
  const filters = (
    <div class="batch-grid-filters" role="group" aria-label="依狀態篩選">
      <button type="button" class={`batch-grid-filter${filter === "all" ? " is-on" : ""}`} aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
        全部 {items.length}
      </button>
      {GRID_GROUPS.filter((g) => count(g.key)).map((g) => (
        <button
          key={g.key}
          type="button"
          class={`batch-grid-filter is-${g.key}${filter === g.key ? " is-on" : ""}`}
          aria-pressed={filter === g.key}
          onClick={() => setFilter(filter === g.key ? "all" : g.key)}
        >
          {g.label} {count(g.key)}
        </button>
      ))}
    </div>
  );
  const cell = (x: (typeof items)[number]) => {
    const badge = gridBadge(x.progress, x.state);
    const v = x.p?.active_version;
    const result = v && !broken.has(x.key) ? urls.resultThumb(v.job_id, v.run) : null;
    return (
      <button
        key={x.key}
        type="button"
        class={`batch-grid-cell is-${x.progress}${isFresh(x.key) ? " is-fresh" : ""}`}
        aria-label={`第 ${x.no} 張，${badge.text}`}
        disabled={props.disabled}
        onClick={() => {
          setOpen(false);
          props.onPick(x.s);
        }}
      >
        <img
          src={result || thumbSrc(x.s)}
          alt=""
          loading="lazy"
          onError={result ? () => setBroken((b) => new Set(b).add(x.key)) : undefined}
        />
        {x.progress === "running" ? <span class="rc-spinner" /> : null}
        {props.onRemove && !props.runs[x.key] && <SidebarRemove index={x.no - 1} onRemove={props.onRemove} />}
        <span class={`session-tile-badge${badge.cls ? ` ${badge.cls}` : ""}`}>{badge.text}</span>
      </button>
    );
  };
  return (
    <section class="batch-grid" aria-label="這批照片">
      <div class="batch-grid-head">
        {filters}
        {tools}
      </div>
      <div class="batch-grid-cells" ref={grid} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
        {shown.map(cell)}
        {ghostCells(ghostsShown)}
        {folded ? (
          <button type="button" class="batch-grid-more" onClick={() => (props.onMore && matchMedia(DRAWER_MEDIA).matches ? props.onMore() : setOpen(true))}>
            +{listed.length + ghosts - shown.length - ghostsShown} 張<br />顯示全部
          </button>
        ) : null}
      </div>
      {open && (
        <>
          <div class="sample-modal-backdrop" onClick={() => setOpen(false)} />
          <div class="sample-modal batch-grid-sheet" role="dialog" aria-modal="true" aria-label="這批照片">
            <div class="sample-modal-head">
              <h2>這批照片 · {items.length} 張</h2>
              <button type="button" class="sample-modal-close" aria-label="關閉" onClick={() => setOpen(false)}>
                <Icon name="x" size={16} />
              </button>
            </div>
            <div class="sample-modal-body">
              {filters}
              <div class="batch-grid-cells is-all">{listed.map(cell)}{ghostCells(ghosts)}</div>
            </div>
          </div>
        </>
      )}
      {props.pending || props.renderable ? (
        // 總覽上一次處理全部的按鈕只放這裡：固定在對話上面，不會被新訊息捲走。
        <div class="batch-grid-actions">
          <Chip label="設定" icon="tune" kind="ghost" disabled={props.actionsHeld} onClick={props.onSettings} />
          {props.pending ? (
            <Chip label={allLabel("analyze", props.pending, props.sources.length)} icon="search" kind="primary" disabled={props.actionsHeld}
              title="還沒分析過的每張照它自己的需求和設定分析，分析過或正在分析的不重送"
              onClick={() => props.onStartAll("analyze")} />
          ) : null}
          {props.renderable ? (
            <Chip label={allLabel("render", props.renderable, props.sources.length)} icon="spark" kind="primary" disabled={props.actionsHeld}
              title="還沒美化過的每張都送：沒分析的先分析再美化，分析中或分析好的接著美化，美化過或正在美化的不重送"
              onClick={() => props.onStartAll("render")} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// 「我的照片」方塊左下角的狀態。
function photoState(p: Photo, renderFailed = false): { key: string; text: string } {
  return p.status === "failed"
    ? { key: "error", text: "失敗" }
    : p.status === "rendering" || p.queued_render || p.pending_render
      ? { key: "processing", text: "美化中" }
      : isBusy(p)
        ? { key: "analyzing", text: "分析中" }
        : renderFailed
          ? { key: "error", text: "失敗" }
          : p.active_version
          ? { key: "done", text: "已美化" }
          : { key: "analyzed", text: "已分析" };
}

// 方塊右上角的 ×，跟首頁磚上那顆同一個做法（home.tsx 的 RemoveMark）：方塊本身是
// <button>，裡面不能再放按鈕，所以用 span 當按鈕，點了不往外傳。
// 「我的照片」的 +：跟輸入框那顆一樣先問要自行上傳還是挑範例照片。抽屜標題列和總覽照片格上面共用。
// onBefore 在真的要加照片前呼叫（抽屜那邊先收起來）；end 是貼在右邊時選單往左對齊，不跑出畫面。
function BatchAddButton(props: { full: boolean; onAdd: (files: File[]) => void; onSamples?: () => void; onBefore?: () => void; end?: boolean }) {
  const { full } = props;
  const addInput = useRef<HTMLInputElement>(null);
  const addWrap = useRef<HTMLDivElement>(null);
  const [addMenu, setAddMenu] = useState(false);
  useEffect(() => {
    if (!addMenu) return;
    const away = (e: MouseEvent) => {
      if (!addWrap.current?.contains(e.target as Node)) setAddMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setAddMenu(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [addMenu]);
  return (
      <div class="batch-add-wrap" ref={addWrap}>
        <button
          type="button"
          class="batch-download-all-btn"
          title={full ? `已經選滿 ${MAX_BATCH} 張了` : "新增照片"}
          aria-label="新增照片"
          aria-haspopup={props.onSamples ? "menu" : undefined}
          aria-expanded={props.onSamples ? addMenu : undefined}
          disabled={full}
          onClick={() => (props.onSamples ? setAddMenu((v) => !v) : addInput.current?.click())}
        >
          <Icon name="plus" size={14} />
        </button>
        {addMenu && props.onSamples && (
          <div class={`chat-attach-menu is-down${props.end ? " is-end" : ""}`} role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setAddMenu(false);
                addInput.current?.click();
              }}
            >
              <Icon name="up" size={16} />
              自行上傳照片
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setAddMenu(false);
                props.onBefore?.();
                props.onSamples!();
              }}
            >
              <Icon name="photos" size={18} />
              從範例照片挑選
            </button>
          </div>
        )}
        <input
          ref={addInput}
          type="file"
          accept="image/*,.heic,.heif"
          multiple
          hidden
          onChange={(e) => {
            const el = e.currentTarget;
            const fs = Array.from(el.files || []);
            el.value = "";
            if (fs.length) {
              props.onBefore?.();
              props.onAdd(fs);
            }
          }}
        />
      </div>
  );
}

function SidebarRemove({ index, onRemove }: { index: number; onRemove: (i: number) => void }) {
  const go = (e: Event) => {
    e.stopPropagation();
    e.preventDefault();
    onRemove(index);
  };
  return (
    <span
      class="history-cube-remove"
      role="button"
      tabIndex={0}
      aria-label={`移除第 ${index + 1} 張`}
      title="移除"
      onClick={go}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && go(e)}
    >
      ×
    </span>
  );
}

// 同一段對話裡有好幾張時，最左邊那欄「我的照片」：點哪張就看哪張的對話和結果。
// 跟舊版批次的 .batch-sidebar 同一套 class 和樣子；每張是歷史紀錄那種方塊（.history-cube），
// 狀態疊在照片左下角。窄螢幕一樣收成從左邊拉出來的抽屜。
// 上面那顆「全部一鍵美化」（有幾張美化過就叫「一鍵美化剩餘 N」）把還沒美化過的每張照它自己目前的設定送出去；全部都美化過就不出現。
function BatchSidebar(props: {
  photos: Photo[];
  messages: Message[];
  current: string;
  onPick: (id: string) => void;
  busy: boolean;
  onRenderAll: () => void;
  hop?: boolean;
  onHopped?: () => void;
}) {
  const { photos, current } = props;
  const [open, setOpen] = useState(false);
  const [zipping, setZipping] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  // 每張美化過的照片要放進 ZIP 的所有版本。對話紀錄裡找不到版本（例如只在面板模式美化過）
  // 就退回目前那張結果。
  const resultsOf = (p: Photo) => {
    const versions = versionsFromMessages(props.messages, p.photo_id);
    if (versions.length) return versions.map((v) => ({ url: urls.resultImage(versionJob(v)), name: `result_v${v.ordinal}` }));
    const v = p.active_version;
    return v ? [{ url: urls.resultImage(v.job_id, v.run), name: `result_${v.run || 1}` }] : [];
  };
  // 提示字跟面板模式的全部下載一樣：看有幾張已經美化過，說清楚會拿到原圖還是結果。
  const doneCount = photos.filter((p) => p.active_version).length;
  const zipHint =
    doneCount === 0
      ? `下載所有原圖（${photos.length} 張）`
      : doneCount === photos.length
        ? `下載所有已完成的結果，含每一版（${photos.length} 張）`
        : `下載全部（已完成 ${doneCount} 張含每一版，其餘為原圖，共 ${photos.length} 張）`;
  const downloadAll = async () => {
    if (zipping) return;
    setZipping(true);
    const ok = await downloadPhotosZip(
      photos.map((p) => ({ source: urls.sourceImage(p.analyze_job_id), results: resultsOf(p) })),
    );
    setZipping(false);
    setNotice(ok ? "已下載完成。" : "打包下載失敗，請稍後再試。");
  };
  // 換到的那張不在列表看得到的範圍裡（例如從底下那排點進來、或一開就停在後面那張），
  // 就把列表捲到剛好露出它。只動列表自己的 scrollTop，不帶著整頁跑。
  useEffect(() => {
    const box = list.current;
    const on = box?.querySelector<HTMLElement>(".batch-sidebar-item.selected");
    if (!box || !on) return;
    const top = on.offsetTop - box.offsetTop;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (top + on.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = top + on.offsetHeight - box.clientHeight;
  }, [current, open]);
  // 有的美化過、有的還沒：只送還沒的。
  const todo = photos.filter(canRenderInAll);
  const pick = (id: string) => {
    setOpen(false);
    if (id !== current) props.onPick(id);
  };
  return (
    <>
      <div class={`batch-sidebar-backdrop${open ? " drawer-open" : ""}`} onClick={() => setOpen(false)} />
      <aside class={`batch-sidebar${open ? " drawer-open" : ""}`} id="batch-sidebar">
        <button type="button" class="batch-sidebar-close-btn" aria-label="收起照片列表" onClick={() => setOpen(false)}>
          <Icon name="x" size={16} />
        </button>
        <div class="batch-sidebar-header">
          <h2 class="batch-sidebar-title">我的照片</h2>
          <button
            type="button"
            class="batch-download-all-btn"
            title={zipping ? "打包中…" : zipHint}
            aria-label={zipping ? "打包中" : zipHint}
            disabled={zipping}
            aria-busy={zipping}
            onClick={() => void downloadAll()}
          >
            <Icon name="down" size={14} />
          </button>
        </div>
        {todo.length ? (
          <div class="batch-sidebar-all">
            <Chip
              label={allLabel("render", todo.length, photos.length)}
              icon="spark"
              kind="primary"
              disabled={props.busy}
              title="還沒美化的每張照目前的設定美化，美化過的不重送"
              onClick={props.onRenderAll}
            />
          </div>
        ) : null}
        <div class="batch-sidebar-list" ref={list} role="tablist" aria-label="這段對話的照片">
          {photos.map((p, i) => {
            const v = p.active_version;
            const state = photoState(p);
            const on = p.photo_id === current;
            return (
              <button
                key={p.photo_id}
                type="button"
                role="tab"
                aria-selected={on}
                aria-label={`第 ${i + 1} 張，${state.text}`}
                class={`history-cube batch-sidebar-item ${state.key}${on ? " selected" : ""}`}
                onClick={() => pick(p.photo_id)}
              >
                <span class="rc-media">
                  <img src={v ? urls.resultThumb(v.job_id, v.run) : urls.sourceThumb(p.analyze_job_id)} alt="" />
                  {state.key === "processing" || state.key === "analyzing" ? <span class="rc-spinner" /> : null}
                </span>
                <span class="rc-badge">{state.text}</span>
              </button>
            );
          })}
        </div>
      </aside>
      <button
        type="button"
        class={`batch-drawer-trigger${props.hop ? " is-hop" : ""}`}
        aria-label={`我的照片（${photos.length} 張）`}
        onClick={() => setOpen(true)}
        onAnimationEnd={() => props.onHopped?.()}
      >
        <Icon name="photos" size={18} />
        <span class="batch-drawer-trigger-count">{photos.length}</span>
      </button>
      {notice ? <ConfirmModal message={notice} confirmLabel="確定" onAnswer={() => setNotice(null)} /> : null}
    </>
  );
}

// 「全部一鍵美化」送得出去的：還沒美化過、沒失敗、也不在美化中或排著等美化。還在
// 分析的照送，後端會排著等分析好。
const canRenderInAll = (p: Photo) =>
  !p.active_version && p.status !== "failed" && p.status !== "rendering" && !p.queued_render && !p.pending_render;

// ── Pre-session chat ────────────────────────────────────────────────────────
// 文字裡寫著「照片中從左邊數來第 2 位：」的地方換回那個人的頭像，跟對話裡指定
// 對象時看到的同一顆。冒號跟著拿掉——頭像本身就是「這位：」。
function withFaces(text: string, faces: PrePerson[]) {
  return faceText(text, faces.map((f) => ({ ref: faceRef(f), face: f })), true);
}

// 跟 session 的個人化需求同一套：框一直開著，直接改，打字停一下或離開框就存，
// 不用按「修改」「儲存」。整張就是一段字，不同對象之間用「；」隔開，指定了人的
// 那段前面掛著那個人的頭像：「(頭像)頭髮染黑；天空變藍；(頭像)頭髮紅」。
//
// 頭像嵌在字裡、不能打字進去（只能整顆刪掉），不會變回「從左邊數來第 2 位：」。
// 存的時候從頭讀：遇到頭像就開始那個人的段，遇到「；」就結束目前這段，後面沒接
// 頭像的字算沒指定對象。整段刪光的對象就是拿掉。
type NoteSection = { key: string; line: string; text: string; persons: PrePerson[] };

function NotesSnapshot(props: { sections: HistoricalSection[] }) {
  return <div class="chat-card chat-notes" data-notes-snapshot>
    <span class="chat-meta">個人化需求（當時內容）</span>
    <p class="chat-text">{props.sections.length ? props.sections.map((s, i) => <span>
      {i ? "；" : ""}{s.key ? <>{s.persons.map(p => <FaceChip face={p} small />)}{s.text}</> : s.line}
    </span>) : "目前沒有個人化需求。"}</p>
  </div>;
}

const sectionShown = (sec: NoteSection) => (sec.key ? sec.line.slice(whoPrefix(sec.persons).length) : sec.line);

function NotesEditor(props: {
  sections: NoteSection[];
  upTo: number;
  held: boolean;
  onSave: (edits: { key: string; text: string }[], upTo: number) => void;
}) {
  const { sections, held } = props;
  const box = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  const timer = useRef<number>();
  const [saved, setSaved] = useState(false);
  const latest = useRef(props);
  latest.current = props;
  // 外面的需求變了（又收下一句、清除）就跟著換，但正在打字時不動。
  const shown = sections.map((sec) => `${sec.key}\u0001${sec.text}`).join("\u0002");
  useEffect(() => {
    const el = box.current;
    if (!el || focused.current) return;
    fillNoteBox(el, sections.map((sec) => ({ key: sec.key, faces: sec.persons, text: sec.key ? sectionShown(sec) : sec.text })), false);
  }, [shown]);
  useEffect(() => () => clearTimeout(timer.current), []);
  // 整段刪光（拿掉那個人的需求）等離開框才算：打到一半刪光重打，不會先把那句拿掉。
  const save = (leaving: boolean) => {
    clearTimeout(timer.current);
    const { sections: now, upTo, onSave } = latest.current;
    if (!box.current) return;
    const before = Object.fromEntries(now.map((x) => [x.key, x.text]));
    const changed = readNoteBox(box.current, now.map((x) => x.key))
      .filter((x) => x.text.length <= PROMPT_MAX && x.text !== before[x.key] && (leaving || x.text))
      .map((x) => ({ key: x.key, text: x.text }));
    if (!changed.length) return;
    onSave(changed, upTo);
    setSaved(true);
  };
  return (
    <>
      <div
        class="chat-notes-box"
        ref={box}
        role="textbox"
        aria-multiline="true"
        aria-label="個人化需求"
        contentEditable={!held}
        onFocus={() => {
          focused.current = true;
        }}
        onInput={() => {
          setSaved(false);
          clearTimeout(timer.current);
          timer.current = window.setTimeout(() => save(false), PROMPT_SAVE_MS);
        }}
        onBlur={() => {
          focused.current = false;
          save(true);
        }}
        {...noteBoxKeys}
      />
      {saved && (
        <div class="chat-prompt-foot">
          <span class="chat-meta">已儲存</span>
        </div>
      )}
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
  // 多張上傳頁上的其中一張：沒有開頭的招呼，開始之後人留在這頁。
  batch?: { index: number; done: boolean; onOpen: () => void };
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
  // 過了的那輪不再擺設定卡，用一句話講改了什麼：打開時的設定跟下一輪打開時（最後一輪就是現在的）比。
  const settingsSaid = (id: string, from?: Settings) => {
    const i = settingsTurns.findIndex(e => e.id === id);
    const to = settingsTurns[i + 1]?.settings || props.settings;
    const changed = from && to ? settingsChanges(caps, from, to) : [];
    return changed.length ? `好，${changed.join("、")}。` : "設定沒有改動。";
  };
  // 動作鍵跟在目前對話後面；沒有模型回覆的確認文字屬於原本那一輪。
  const last = log[log.length - 1];
  // 舊偵測流程保留原等待條件；2.0 預覽不阻擋正式分析。
  const held = busy || (scanning && !props.previewActive);
  // 「照片選好了」那段開場白：舊偵測流程等掃描完才講，2.0 預覽一選好就講。
  const greeted = !scanning || !!props.previewActive;
  // 送出前的最後一眼。對話往上捲是整段過程——被擋下來的句子、AI 回的話、設定
  // 卡夾在中間；這張只放真的會跟著進 session 的那段字，而且就擺在動作鍵正上方，
  // 要確認的東西跟要按下去的地方是同一處。跟 session 的個人化需求一樣是一整段，
  // 看到的就是送出去的 user_prompt；要刪要改直接改字。
  const kept = props.sections.length > 0;
  const hasWishes = kept || !!props.activeTargetQuestion;
  const asking = props.renderAsk;
  // 按下去就算選了：這排定格成紀錄，按的那顆維持選取的樣子；失敗了再解開。
  const went = !asking && starting ? starting.mode : null;
  const review = kept && (
    <div class="chat-card chat-notes">
      <span class="chat-meta">個人化需求</span>
      <NotesEditor sections={props.sections} upTo={notes.length} held={held} onSave={props.onPrompt} />
    </div>
  );
  const startChips = (
    <div class="chat-choices chat-actions">
      {/* Reading order follows the order of operations: adjust the settings
          first, then run. 分析 and 一鍵美化 are peers -- both are "go", they
          differ only in how far they go -- so they carry the same weight. */}
      {!asking && !went && (step === "picked" || step === "settings" || hasWishes) && (
        <div class="chat-actions-aux">
          {(step === "picked" || step === "settings") && <Chip label="設定" icon="tune" kind="ghost" disabled={held} onClick={props.onEdit} />}
          {/* 清掉的是需求，不是對話：上面那幾句連同 AI 的回覆都還在，只是不再算
              數。所以沒有需求可清時這顆就不該在——按下去畫面不會有任何變化。 */}
          {hasWishes && (
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
  // 歷史需求留在當輪；只有尚未建立快照時，編輯器才暫放在操作鍵前。
  const actions = (
    <>
      {!displayEvents.some(e => e.kind === "notes") && review}
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
      {!props.batch && <AssistantMsg>
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
      </AssistantMsg>}

      {source && (
        <>
          <UserMsg>
            <UploadBubble
              src={thumbSrc(source)}
              text={props.batch ? `第 ${props.batch.index + 1} 張` : source.kind === "file" ? "上傳了這張" : "選了這張"}
              onOpen={props.onView}
            />
          </UserMsg>

          {greeted && (
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
      {/* 開場白還在等掃描時，後面的對話也先不出來：總覽發過來的那句會比開場白
          早到，不擋的話切過來時它先整串出現，開場白掃完才補打在它上面。 */}
      {greeted && log.map((t, i) => {
        // 總覽發過來的句子：使用者那句不搬過來，連續幾句只在最後一句的位置總結一次。
        if (fromOverview(t)) {
          const shown = log.slice(i + 1).find((x) => !(fromOverview(x) && "part" in x && x.part === "user"));
          if (("part" in t && t.part === "user") || (shown && fromOverview(shown))) return null;
          return <AssistantMsg key={`overview-${i}`}><p class="chat-text">{OVERVIEW_SUMMARY}</p></AssistantMsg>;
        }
        if ("previewReply" in t && t.previewReply.kind === "notes") {
          const latest = displayEvents.filter(e => e.kind === "notes").slice(-1)[0];
          const editable = t.previewReply.id === latest?.id && !pending && kept &&
            sectionsIdentity(t.previewReply.sections || []) === sectionsIdentity(props.sections);
          return <AssistantMsg key={t.previewReply.id}>{editable ? review : <NotesSnapshot sections={t.previewReply.sections || []} />}</AssistantMsg>;
        }
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
          {t.previewReply.id === activeSettingsId
            ? <AssistantMsg>
                <p class="chat-text">{t.previewReply.text}</p>
                <SettingsEditor caps={caps} settings={props.settings} onChange={props.onSettings} />
              </AssistantMsg>
            : <AssistantMsg><p class="chat-text">{settingsSaid(t.previewReply.id, t.previewReply.settings)}</p></AssistantMsg>}
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
        const who = t.ask.asked?.kind === "person" && !options.length ? t.ask.replyPeople || [] : [];
        return (
          <div key={`note-${t.ask.order ?? notes.indexOf(t.ask)}-${t.part || "both"}`} class="note-turn">
            {t.part !== "reply" && <AskBubble text={t.ask.text} faces={t.ask.persons} />}
            {t.part !== "user" && t.ask !== pending && (
              <AssistantMsg>
                <p class="chat-text">{said(t.ask.reply || (t.ask.kept !== false ? "好，這些會當成個人化需求，美化時一起套用。" : "好，那就先不加個人化需求。"), t.ask.replyPeople || t.ask.persons)}</p>
                {options.length > 0 && (
                  <AnswerList options={options} picked={open ? null : next?.text ?? null} open={open} busy={held} onPick={props.onAnswer} />
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
      {source && greeted && !pending && !(last && "ask" in last && (last.ask.asked || last.ask.offered)) && <div class="prechat-actions">{actions}</div>}

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
          {props.batch?.done ? (
            <AssistantMsg>
              <p class="chat-text">
                這張{starting.mode === "analyze" ? "開始分析了" : starting.overwrite ? "開始調整了" : "開始美化了"}，結果頁裡可以看進度。其他照片從{photosSpot()}「我的照片」回來接著弄。
              </p>
              <div class="chat-choices">
                <Chip label="看結果" icon="image" onClick={props.batch.onOpen} />
              </div>
            </AssistantMsg>
          ) : (
            <AssistantMsg>
              <WorkingCard
                done={starting.lines.slice(0, -1)}
                now={starting.lines[starting.lines.length - 1]}
                since={starting.since}
              />
            </AssistantMsg>
          )}
        </>
      )}
      {props.error && <ErrorMsg text={props.error} />}
    </>
  );
}

// 有些話不是出錯，只是提醒（例如張數上限、選到不是圖片的檔案）。這些跟錯誤共用
// 同一個 error 欄位，但用 notice() 標起來，顯示時就照一般的話講，不加「出了點問題」。
const NOTICES = new Set<string>();
function notice(text: string) {
  NOTICES.add(text);
  return text;
}

function ErrorMsg({ text }: { text: string }) {
  return (
    <AssistantMsg>
      {NOTICES.has(text) ? (
        <p class="chat-text">{text}</p>
      ) : (
        <p class="chat-text chat-error">出了點問題：{text}</p>
      )}
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

// 從 was（某一輪開始時的設定快照）到 plan，動到了哪些項目。
function changedCats(was0: Dict, plan: Plan): Set<Category> {
  const was = canonical(was0) as Dict;
  const now = snapshotOf(plan);
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
  return touched;
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
  // 同一段對話有好幾張時，最左邊那欄照片列表（BatchSidebar）。
  sidebar?: ComponentChildren;
  busy: boolean;
  error: string;
  onSelectVersion: (v: Version) => void;
  onRender: (label: string, icon: string, patch?: Plan) => void;
  // 只剩拖放進畫布這條路會用到 -- 丟一張新的照片進來等於開一個新對話。
  onFile: (f: File) => void;
  // faces 只在沒有分析時用：指的人是本地偵測的框和位置，取代 subjectIndices 送出。
  onAsk: (text: string, subjectIndices: number[], screen?: Screen, faces?: AskFace[], answerTo?: string, sideAnswer?: SideAnswer) => Promise<boolean>;
  onAnswerWaiting: (messageId: string, screen: Screen) => void;
  onPatch: (patch: Plan) => Promise<boolean>;
  onQuietPrompt: (text: string) => Promise<unknown>;
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
  const messages = (session.messages || []).filter((m) => cardAbout(m.card, pid));
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
  const baseCats = analysis ? categoriesFor(analysis, caps, photo.plan) : [];
  const opened: Category[] = [];
  // 略過也是走過一步：跟開過的面板一起決定接下來還要談什麼，也讓重整後回到同
  // 一個位置。
  const skipped: Category[] = [];
  let lastOptionsIdx = -1;
  let lastStepIdx = -1;
  const offGuide = new Set<number>();
  if (editing) {
    messages.forEach((m, i) => {
      if (i <= roundStart) return;
      const cat = m.card?.category as Category;
      if (!CATEGORIES[cat]) return;
      if (m.card?.type === "options_opened") {
        // 引導下一個要談的不是這項（自己點了別的項目、按「修改」、再看一次）：
        // 這張面板是使用者自己要看的，後面不接引導的問句。
        const expected = baseCats.find((c) => !opened.includes(c) && !skipped.includes(c));
        if (cat !== expected) offGuide.add(i);
        opened.push(cat);
        lastOptionsIdx = i;
        lastStepIdx = i;
      } else if (m.card?.type === "topic_skipped") {
        skipped.push(cat);
        lastStepIdx = i;
      }
    });
  }
  // 只照需求改的那一版再調整：美化只看個人化需求，人像、畫面、風格都碰不到
  // 結果，所以不給。看的是這一輪開的時候有沒有寫需求，中途清掉也不會突然冒出
  // 其他項目；「幫我分析」開的那輪會把 overwrite_prompt 關掉，回到完整清單。
  const promptOnlyRound =
    adjusting && !!photo.plan.overwrite_prompt && !!String(roundPlan?.user_prompt || "").trim();
  // 設定（美化引擎、比例、張數）每次美化都會用到，只照需求改的那一版也是，
  // 所以能改的時候一律排在最後；不然第一輪想換引擎，連 AI 都找不到入口。
  const cats: Category[] = [...(promptOnlyRound ? ["prompt" as const] : baseCats), "settings"];
  const lastOpened = opened.length ? opened[opened.length - 1] : null;
  // 分析後的第一輪用漸進引導：排在最前面、還沒看過也還沒略過的那一項，就是
  // 下一句要談的。（調整後再美化那一輪維持原本的選單。）設定不是分析找到的
  // 項目，引導不談它，只留著按鈕。
  const guideQueue = cats.filter((c) => c !== "settings" && !opened.includes(c) && !skipped.includes(c));
  const nextCat: Category | null = fresh ? guideQueue[0] || null : null;
  // 這一輪動過、但還沒美化的項目。roundPlan 是開這輪時的設定，photo.plan 是
  // 現在的；把差出來的欄位換成項目名稱，離開再回來時才看得出自己剛剛改了
  // 什麼，而不是只看到一顆孤零零的「美化」。
  const dirtyCats: Category[] = [];
  if (adjusting && roundPlan && !unchanged) {
    const touched = changedCats(roundPlan, photo.plan);
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
  // 「幫我分析」開的那輪，開場先收起來只給「看美化項目／美化」；按了「看美化項目」
  // 就在同一則展開項目（記的是那則的 id）。只是畫面上的展開，不開新的一輪。
  const [expandedCheck, setExpandedCheck] = useState<string | null>(null);
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
  // 「照建議直接美化」只在什麼都還沒動時這樣叫：寫了需求、打開過項目、對話裡套用過
  // 改動，按下去就不只是照建議了，鍵和話裡都改說「美化」。
  const tookChange = messages
    .slice(readyIdx + 1)
    .some((m) => !!m.card?.proposal?.direct || (m.card?.type === "proposal_resolved" && m.card?.choice === "apply"));
  const startLabel = fresh && !adjusting && !lastOpened && !promptNow && !tookChange ? "照建議直接美化" : "美化";
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
    card.question_kind === "person" &&
    !card.options?.length &&
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
  // except：這則訊息裡已經有入口的項目（開著的那張面板、卡片上可以就地改的那一項），
  // 底下這排就不再放同一項。
  const catChips = (except?: Category | Category[] | null) => {
    const skip = Array.isArray(except) ? except : except ? [except] : [];
    const chips = cats
      .filter((c) => !skip.includes(c))
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
    const renderLabel = startLabel;
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
            ? `不想一項一項看的話，按下面的「${startLabel}」我就直接開始修；也可以打字告訴我想怎麼改。`
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
    lastCard?.type === "llm_reply" && lastCard.question_kind === "person" && !lastCard.options?.length;
  // 頭像已經排在 AI 那句底下可以直接點時，輸入框上面那排就先收起來，免得兩處都能選。
  const facesInline =
    !props.pendingAsk && (asksWho(lastCard) || (lastCard?.type === "llm_reply" && lastCard.question_kind === "person" && !lastCard.proposal && (lastCard.options || []).some((o: string) => !!faceOption(o))));
  async function send(text: string, si: number[]) {
    if (pendingPerson?.kind === "edit_description" || pendingPerson?.kind === "action" || pendingPerson?.kind === "visual") {
      const answer = await props.onConfirmPeople([], undefined, undefined, undefined, undefined, text);
      if (answer !== "new_message") return answer;
    }
    if (pendingProposal && YES.test(text.trim())) {
      await choose(pendingProposal, "apply");
      return true;
    }
    return props.onAsk(text || (si.length > 1 ? "這幾位" : "這位"), si, screen, askFaces(si),
      lastCard?.question_kind ? messages[messages.length - 1].id : undefined,
      lastCard?.clarification_subtype === "body_side_reference" ? sideChoice(lastCard.side_question, text) : undefined);
  }

  // 這張面板卡收起時的 plan。會收起它的那一步 -- 下一個開起來的面板，或下一輪
  // 的「調整後再美化」-- 身上都帶著當下的快照，所以往後找第一張帶 plan 的卡就
  // 是它。（略過不收面板，也就不帶 plan。）找不到就是還沒被任何一步收起來，用
  // 現在這份。舊 session 的卡沒有快照，退回同一條路，結果跟以前一樣。
  // adjust_opened 帶的是這一輪開始時的設定（套用前的樣子），不是離開某張卡時的
  // 樣子，往後找快照時跳過它。
  const leftWith = (card: Dict | null | undefined) => (card?.type === "adjust_opened" ? undefined : card?.plan);
  const planAt = (idx: number): Plan => {
    for (let i = idx + 1; i < messages.length; i++) {
      const snap = leftWith(messages[i].card);
      if (snap) return { ...photo.plan, ...(snap as Plan) };
    }
    return photo.plan;
  };
  // 某則訊息所在那一輪開始時的設定（「看美化項目」那張卡上的快照）。
  const roundPlanAt = (idx: number): Dict | undefined => {
    for (let i = idx - 1; i >= 0; i--) if (messages[i].card?.type === "adjust_opened") return messages[i].card?.plan;
    return undefined;
  };

  // 離開某張卡時（下一個帶設定快照的訊息）的個人化需求；中間又有別張卡改過需求，
  // 那個快照講的就不是這張卡了，回 undefined。後面還沒有快照（例如剛按美化、
  // 美化還在跑，完成的那則才會帶設定）就是現在的設定。
  const promptWhenLeft = (idx: number): string | undefined => {
    for (let i = idx + 1; i < messages.length; i++) {
      const card = messages[i].card;
      if (card?.proposal?.changes?.some((ch: Change) => ch.field === "user_prompt")) return undefined;
      const snap = leftWith(card);
      if (snap) return String(snap.user_prompt || "");
    }
    return photo.plan?.user_prompt || "";
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
            prompt={
              c.proposal.id === lastProposalId && idx > lastRender ? photo.plan?.user_prompt || "" : promptWhenLeft(idx)
            }
            onQuietPrompt={props.onQuietPrompt}
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
          messages.slice(idx + 1).every((x) => x.card?.auto) && (
            <div class="chat-choices">
              {/* 只有個人化需求那張卡還在、能就地改，它那一類才不必再列；其他的不畫卡，
                  改到的那一類就是要改回來的地方，照樣列出來。 */}
              {catChips(
                c.proposal.changes.length === 1 && c.proposal.changes[0].field === "user_prompt"
                  ? c.proposal.changes.map((ch: Change) => categoryOf(ch.field)).filter((x: Category | null): x is Category => !!x)
                  : null,
              )}
            </div>
          )}
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
            // 問的是哪一位時，答案就是那幾顆頭像（見下面）；其他答案一律排成清單。
            if (!(c.question_kind === "person" && (c.options as string[]).some((o) => !!faceOption(o))))
              return (
                <AnswerList
                  options={c.options as string[]}
                  copy={!!c.lines}
                  picked={next?.card?.type === "ask" ? next.text || "" : null}
                  open={open}
                  busy={busy}
                  onPick={(t) => props.onAsk(t, si, screen, sf, c.question_kind ? messages[idx].id : undefined)}
                />
              );
            // 問的是哪一位時，答案就是那幾顆頭像：點一顆等於指著他說「這位」，
            // 跟點輸入框上方的頭像再送出是同一句。
            const nextSi = next?.card?.type === "ask" ? askedIndices(next.card) : [];
            return (
              <div class="chat-choices">
                {(c.options as string[]).map((o) => {
                  const face = c.question_kind === "person" ? faceOption(o) : null;
                  if (face) {
                    const picked = !open && nextSi.length === 1 && nextSi[0] === face.subjectIndex;
                    return (
                      <FaceChip
                        face={face}
                        avatarOnly
                        picked={picked}
                        title={face.name ? `就是${face.name}` : "就是這位"}
                        onClick={open && !busy ? () => props.onAsk("這位", [face.subjectIndex], screen, askFaces([face.subjectIndex]), c.question_kind ? messages[idx].id : undefined) : undefined}
                      />
                    );
                  }
                  return (
                    <Chip
                      label={o}
                      kind={!open && next?.card?.type === "ask" && next.text === o ? "picked" : undefined}
                      settled={!open}
                      disabled={busy}
                      onClick={() => props.onAsk(o, si, screen, sf, c.question_kind ? messages[idx].id : undefined,
                        c.clarification_subtype === "body_side_reference" ? sideChoice(c.side_question, o) : undefined)}
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
                    onClick={open && !busy ? () => props.onAsk("這位", [face.subjectIndex], screen, askFaces([face.subjectIndex]), c.question_kind ? messages[idx].id : undefined) : undefined}
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
    const label = startLabel;
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
  const offerCheckAt = (idx: number) =>
    messages[idx]?.card?.type === "render_done" && idx === lastRender && !adjusting &&
    !!photo.plan.overwrite_prompt && (skippedAnalysis || !!analysis) &&
    !messages.slice(idx + 1).some(x => x.card?.type === "check_declined" || x.card?.type === "analysis_brief");
  const showActionRow = !!actionChips && !busy && !props.pendingAsk && photo.status === "ready" && !askMode && !confirmRerun && !endAsks && !pendingPerson && !offerCheckAt(endIdx);
  if (!showActionRow) bar = offerCheckAt(endIdx) ? ["好，幫我分析", "不用了"] : [];
  const endRow =
    showActionRow ? (
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
      {question.kind === "visual" && <>
        <Chip label="重新分析這筆需求" disabled={!active || busy} onClick={() => void props.onConfirmPeople([], undefined, undefined, undefined, undefined, "重新分析這筆需求")} />
        <Chip label="取消這筆需求" disabled={!active || busy} onClick={() => void props.onConfirmPeople([], undefined, undefined, undefined, undefined, "取消這筆需求")} />
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
      case "personalized_snapshot":
        return <AssistantMsg><NotesSnapshot sections={c.sections || []} /></AssistantMsg>;
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
        // 「這是上次的…」只在這一項還是上一版美化時的樣子才說；這一輪已經改過
        // （對話裡、卡片上、面板裡都算），就照一般的說法介紹現在的內容。
        // 收起來的面板看的是離開它時的設定，跟它自己那一輪的起點比；不然下一輪
        // 一開，舊面板的開場白就跟著換。
        const shownWith = (live ? photo.plan : planAt(idx)) as Plan;
        const startedWith = roundPlanAt(idx);
        const again =
          messages.slice(0, idx).some((x) => /^render_/.test(x.card?.type || "")) &&
          !(startedWith && changedCats(startedWith, shownWith).has(cat));
        const lead = !answersApply
          ? introFor(cat, analysis, planAt(idx), again)
          : fromApply
            ? `先看剛套用的${termOf(cat)}，改動的地方已經標起來了。`
            : `好，已套用。這是更新後的${termOf(cat)}，改動的地方已經標起來了。`;
        const recap = live ? "" : topicRecap(cat, analysis, caps, planAt(idx));
        // 後面的話跟著項目和「美化」鍵（回覆、提案、構圖好了都會帶），這張面板就
        // 不再接一次，不然同一排按鈕會連著出現兩次。
        const atEnd = messages.slice(idx + 1).every((x) => x.card?.auto);
        return (
          <>
            {/* via reply：AI 的回覆說了幫他打開，使用者沒按過，不畫他的點擊泡泡。 */}
            {!answersApply && c.via !== "reply" && (
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
                    onQuietPrompt={props.onQuietPrompt}
                  />
                  {!atEnd ? null : fresh && !offGuide.has(idx) ? (
                    idx === lastStepIdx && guideTail({ except: cat })
                  ) : fresh ? (
                    idx === lastStepIdx && (
                      <>
                        <p class="chat-text">
                          {cats.length > 1 ? "調整好後，可以接著看其他項目，或按下面的「美化」我就開始。" : "改好後按下面的「美化」我就開始。"}
                        </p>
                        {cats.length > 1 && <div class="chat-choices">{catChips(cat)}</div>}
                      </>
                    )
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
              {check && live && !!promptNow && <p class="chat-text chat-muted">你的需求會留著，這次會連同分析的建議一起美化。</p>}
              {/* 「幫我分析」想知道的是還有哪裡能修，摘要已經回答了；下一步多半是照著美化，
                  所以先只給「看美化項目／美化」，想細調再展開項目。後面已經接著別的話
                  （打開項目、打字問 AI），下一步由那則帶，這裡就不掛了。 */}
              {check && showChips && messages.slice(idx + 1).every((x) => x.card?.auto) &&
                (expandedCheck === m.id ? (
                  <>
                    <p class="chat-text">
                      {cats.length > 1
                        ? `想調整哪一項點下面，改好按「${goLabel}」我就開始。`
                        : cats.length
                          ? `點下面的「${CATEGORIES[cats[0]].label}」就可以改，改好按「${goLabel}」我就開始。`
                          : `按下面的「${goLabel}」我就開始。`}
                    </p>
                    <div class="chat-choices">{catChips()}</div>
                  </>
                ) : (
                  <>
                    {/* 跟成品卡「想調整美化項目，按「看美化項目」；想用一樣的設定再產生一版，按「再美化」」同一個說法。 */}
                    {!!cats.length && (
                      <p class="chat-text">想調整美化項目，按「看美化項目」；想直接開始美化，按「{goLabel}」。</p>
                    )}
                    <div class="chat-choices">
                      {!!cats.length && (
                        <Chip label="看美化項目" icon="pencil" disabled={busy} onClick={() => setExpandedCheck(m.id)} />
                      )}
                      {rendering === endIdx && endRow && !placed ? ((placed = true), actionChips) : null}
                    </div>
                  </>
                ))}
              {live && !check && (
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
                  {/* 「美化」跟著項目那排一起出來，這句要排在按鈕上面才對得上「下面」；問句留在最後，緊接著按鈕。 */}
                  <p class="chat-text chat-muted">
                    改好按下面的「美化」我就開始{n ? "，之前的版本會留在美化紀錄" : ""}。
                  </p>
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
        const offerCheck = offerCheckAt(idx);
        return (
          <AssistantMsg>
            <ResultCard
              job={versionJob(v)}
              ordinal={v.ordinal}
              failed={(c.failed_job_ids || []).length}
              next={idx === endIdx && !!endRow && !adjusting && !offerCheck}
              note={typeof c.note === "string" ? c.note : undefined}
              onOpen={() => openVersion(v)}
              shown={v.version_id === current?.version_id}
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
        return (
          <>
            <UserMsg meta={clockLabel(m.created_at)}>
              <ClickBubble icon="x" label="不用了" />
            </UserMsg>
            <AssistantMsg>
              <p class="chat-text">好。還有想改的地方，直接輸入告訴我就行；想讓我分析整張照片，也隨時跟我說。</p>
              {/* 「幫我分析」在 endActions 那排裡（offerAnalyze），這裡不再放一顆。 */}
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
      <p class="chat-text">嗨！先選想美化的照片，可以一次選多張。</p>
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
  // 回覆可以替使用者打開的面板：調整中就是列著的那些；美化完收進「看美化項目」時，
  // 是下一輪會列出的那些 -- 只照需求改的那版下一輪只有個人化需求和設定。
  const panels: Category[] = editing
    ? cats
    : !analysis
      ? []
      : photo.plan.overwrite_prompt && promptNow
        ? ["prompt", "settings"]
        : [...baseCats, "settings"];
  const screen: Screen = {
    bar: busy || props.pendingAsk || photo.status !== "ready" ? [] : bar,
    topics: editing ? cats.map((c) => CATEGORIES[c].label) : [],
    pending: [...(adjusting ? dirtyCats.map(c => CATEGORIES[c].label) : []), ...(pendingPerson ? [pendingPerson.text] : [])],
    panels: panels.map((c) => CATEGORIES[c].label),
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
        <div class={`container results-body showing-analysis${props.sidebar ? " batch-mode" : ""}`} id="results-body">
          {props.sidebar}
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
                debugUi={props.debugUi}
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
                  {canShowTargetQuestion(photo) && pendingPerson && !messages.some(m => m.card?.target_question?.question_id === pendingPerson.question_id && m.card?.question_revision === photo.preview_conversation?.revision) &&
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


// AI 問回來的那句底下的答案（包括幫照片想的文案）：跟畫面建議一樣一列一個，點了
// 就是選這個；最後一列留給自己輸入。答案長得跟底下的下一步按鈕（調整、再美化、
// 分析…）不一樣，一眼就分得出哪些是在回答它的問題、哪些是往下走。選過之後定格成
// 紀錄：選中的那列標起來，自己輸入的就換成打的那句。
// copy：答案是文案，自己寫的也是一句文案，字數照文案的上限。
const LINE_MAX = 40;
const ANSWER_MAX = 4000; // 跟輸入框一樣
function AnswerList(props: {
  options: string[];
  picked: string | null;
  open: boolean;
  busy: boolean;
  copy?: boolean;
  onPick: (text: string) => void;
  // 自己輸入的那句；沒給就跟點選項一樣處理。
  onOwn?: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const own = props.picked && !props.options.includes(props.picked) ? props.picked : null;
  const send = () => {
    const t = draft.trim();
    if (t && !props.busy) (props.onOwn || props.onPick)(t);
  };
  return (
    <div class="chat-card chat-options chat-answers">
      <ul class="chat-opt-list">
        {props.options.map((l) => (
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
                maxLength={props.copy ? LINE_MAX : ANSWER_MAX}
                value={draft}
                placeholder={props.copy ? "自己寫一句" : "或是自己輸入"}
                aria-label={props.copy ? "自己寫一句文案" : "自己輸入回答"}
                disabled={props.busy}
                onInput={(e) => setDraft((e.target as HTMLInputElement).value)}
              />
              <button type="submit" class="chat-line-send" disabled={props.busy || !draft.trim()} aria-label={props.copy ? "用這句" : "送出"} title={props.copy ? "用這句" : "送出"}>
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
