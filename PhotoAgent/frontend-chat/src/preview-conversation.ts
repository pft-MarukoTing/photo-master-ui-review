import { nextDisplayOrder } from "./display-order";
import type { FaceRef } from "./chat";
import type { Settings } from "./settings";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "./api";
import type { ReviewTurn } from "./api";
import type { PreviewStatus } from "./subject-preview";
import type { HistoricalSection } from "./personalized-history";

export type PreviewWish = { wish_id: string; text: string; source_text?: string; target_ids: string[]; revision?: number;
  verbatim?: boolean;
  selection_source?: "none" | "user_selection" | "model_suggestion" };
export type PreviewClause = { wish_id: string; request_id: string; text: string; source_text: string;
  status: "pending" | "matched" | "ambiguous" | "unmatched" | "not_person" | "outside_confirmed" | "cancelled" | "superseded";
  target_selection?: "outside_preview"; outside_reference?: string;
  semantic_required?: boolean; semantic?: {version: number; source: string; target: string; action: string; complete: boolean; target_kind?: string; literal_text?: string};
  action_status?: "complete" | "incomplete" | "pending"; part?: string | null; action_answer?: string;
  operation?: {feature: string; part: string; kind: string; intent: string; action: string | null; value: string | null};
  display_text?: string; display_target_ids?: string[]; edit_scope_pending?: boolean; edit_description_pending?: boolean; outside_description?: string; cancelled_from?: {text: string};
  scope: "edit" | "protection" | "query"; target_ids: string[]; protected_ids: string[]; referent: string };
export type PreviewPresentation = { version: 1; opening: string; closing: string };
export type PreviewConversationResult = { token: string; revision: number; wishes_digest: string; reply: string;
  clauses: PreviewClause[]; effective_prompt: string;
  presentation?: PreviewPresentation; confirmation_handled?: boolean;
  question: { question_id: string; wish_ids: string[]; text: string; kind?: "person" | "scope" | "wish" | "edit_scope" | "edit_description" | "action" | "semantic" | "visual"; wish_options?: {wish_id: string; text: string}[]; options?: string[] } | null };
export type PreviewDisplayPerson = FaceRef & { previewRole?: string; preview_person_id?: string };
export type TargetQuestionSnapshot = NonNullable<PreviewConversationResult["question"]> & { token: string; revision: number; answered?: boolean; answer?: string };
export type PreviewReplyEvent = { id: string; after: number; text: string; showPeople: boolean; role?: "user" | "assistant"; order?: number; generation?: string | null; kind?: "settings" | "notes"; sections?: HistoricalSection[]; settings?: Settings; presentation?: PreviewPresentation; people?: PreviewDisplayPerson[]; questionSnapshot?: TargetQuestionSnapshot };

export function submissionPreviewEvents(events: PreviewReplyEvent[], suppressIntroduction: boolean, generation?: string | null): PreviewReplyEvent[] {
  // Freeze display history at the click. Identity/token authority is separate.
  return events.filter(event => !suppressIntroduction || !event.showPeople ||
    event.role === "user" || event.kind === "settings" ||
    (generation !== undefined && event.generation !== generation));
}

type Input = { photoKey?: string; people?: PreviewDisplayPerson[]; identity: string | null; preview?: PreviewStatus; model: string; history: ReviewTurn[];
  historyWithEvents?: (events: PreviewReplyEvent[]) => ReviewTurn[]; wishes: PreviewWish[]; prompt: string; busy: boolean; blocked: boolean; turns: number; active: boolean };

type State = { key: string | null; photoKey: string | null; context: string; wishesKey?: string; result?: PreviewConversationResult; events: PreviewReplyEvent[] };
type Committed = { key: string | null; result?: PreviewConversationResult };
type Budget = { key: string | null; attempts: number; revision: number; completed: boolean; published: boolean; lastContext: string; lastWishes: string; flight: boolean };
const freshBudget = (key: string | null): Budget => ({ key, attempts: 0, revision: 0, completed: false, published: false, lastContext: "", lastWishes: "", flight: false });
// 每張照片（key）離開時的對話狀態。上限跟 subject-preview 的 cache 一樣，多張最多 20 張夠用。
const saved = new Map<string, { state?: State; committed: Committed; budget: Budget }>();
const SAVED_MAX = 50;
function remember(key: string, entry: { state?: State; committed: Committed; budget: Budget }) {
  saved.delete(key);
  saved.set(key, entry);
  while (saved.size > SAVED_MAX) saved.delete(saved.keys().next().value!);
}

// A reply is accepted only against the exact source and complete conversation
// revision that requested it. Draft typing and thumbnail loading are not revisions.
export function usePreviewConversation(input: Input) {
  const context = JSON.stringify([input.history, input.wishes, input.prompt]);
  const wishesKey = JSON.stringify(input.wishes);
  const key = input.identity;
  const photoKey = input.photoKey ?? key;
  const people = useRef(input.people || []);
  people.current = input.people || [];
  // Only a completed server roster proves zero people. Empty thumbnail slots
  // or absent legacy metadata do not. Keep this current across in-flight replies.
  const emptyRoster = !!input.preview?.reusable && Array.isArray(input.preview.people) && input.preview.people.length === 0;
  const current = useRef({ key, context, active: input.active, emptyRoster });
  current.current = { key, context, active: input.active, emptyRoster };
  useEffect(() => () => { current.current.active = false; }, []);
  const [kept, setState] = useState<State>({ key, photoKey, context, events: [] });
  let state = kept;
  const committed = useRef<Committed>({ key });
  const budget = useRef<Budget>(freshBudget(key));
  const [tick, setTick] = useState(0);
  if (budget.current.key !== key) {
    // 多張上傳頁切到別張再切回來，這張的對話要跟離開時一樣：離開時存一份，回來時拿回來。
    const gone = budget.current;
    if (gone.key) remember(gone.key, {
      state: kept.key === gone.key ? kept : undefined,
      committed: committed.current.key === gone.key ? committed.current : { key: gone.key },
      // 離開時還在等的那次回覆會被丟掉（key 已經不對），回來要重問一次。
      budget: gone.flight ? { ...gone, flight: false, lastContext: "", lastWishes: "", attempts: gone.completed ? gone.attempts : Math.max(0, gone.attempts - 1) } : { ...gone },
    });
    const back = key ? saved.get(key) : undefined;
    committed.current = back ? back.committed : { key };
    budget.current = back ? { ...back.budget } : freshBudget(key);
    if (back?.state) {
      state = back.state;
      setState(back.state);
    }
  } else if (committed.current.key !== key) committed.current = { key };
  useEffect(() => {
    const b = budget.current;
    const preview = input.preview;
    if (!key || !input.active || !preview?.reusable || input.busy || input.blocked || b.flight) return;
    if (!b.completed && b.attempts >= 2) return;
    if (b.lastContext === context || (b.completed && b.lastWishes === wishesKey)) return;
    b.lastWishes = wishesKey;
    b.lastContext = context;
    b.flight = true;
    if (!b.completed) b.attempts++;
    const revision = ++b.revision;
    const initial = !b.published && !emptyRoster;
    const silentZero = !b.completed;
    // Existing bindings belong to the same exact source clause, not a new
    // wish that happened to occupy its array position after an editor change.
    const previousToken = committed.current.key === key ? committed.current.result?.token : undefined;
    void api.previewConversation(preview.preview_job_id, {
      presentation_requested: initial, generation: key, source_sha256: preview.source_sha256, analysis_model: input.model,
      revision, wishes: input.wishes, history: (input.historyWithEvents?.(state.photoKey === photoKey ? state.events : []) || input.history), effective_prompt: input.prompt,
      previous_token: previousToken,
    }).then(result => {
      // Save successful authority even if newer text makes this reply stale
      // for presentation. Never let an older revision undo a confirmation.
      if (current.current.key === key && current.current.active &&
          result.revision >= (committed.current.result?.revision ?? -1)) committed.current = { key, result };
      if (current.current.key !== key || current.current.context !== context || !current.current.active || b.revision !== revision) return;
      b.completed = true;
      setState(old => {
        const events = old.photoKey === photoKey ? old.events : [];
        const introduce = initial && !current.current.emptyRoster;
        const text = current.current.emptyRoster && silentZero ? "" : ((introduce && result.presentation ? `${result.presentation.opening}\n\n${result.presentation.closing}` : "") || result.question?.text?.trim() || (initial ? result.reply?.trim() : "") || "");
        if (text) b.published = true;
        return { key, photoKey, context, wishesKey, result, events: text ? [...events, {
          id: result.token, generation: key, order: nextDisplayOrder(), after: input.turns, text, showPeople: introduce,
          ...(result.question ? { questionSnapshot: { ...result.question, token: result.token, revision: result.revision }, people: people.current.map(p => ({ ...p })) } : {}),
          ...(introduce ? { presentation: result.presentation, people: people.current.map(p => ({ ...p })) } : {}),
        }] : events };
      });
    }).catch(() => {
      // No technical warning or fixed completion bubble. Wait to publish the picker with prose.
      if (current.current.key === key && current.current.context === context && current.current.active) b.completed = true;
    }).finally(() => {
      b.flight = false;
      if (budget.current === b) setTick(n => n + 1);
    });
  }, [key, context, input.preview?.reusable, input.busy, input.blocked, input.active, tick]);

  // Late thumbnails may fill an empty slot only for the exact generation and
  // person ID. Names/order and old-model crops remain frozen.
  const thumbsKey = JSON.stringify(input.people?.map(p => [p.preview_person_id, p.thumb]) || []);
  useEffect(() => {
    setState(old => {
      if (old.key !== key || old.photoKey !== photoKey) return old;
      let changed = false;
      const events = old.events.map(event => {
        if (event.generation !== key || !event.people) return event;
        const snapshot = event.people.map(person => {
          const thumb = person.preview_person_id && people.current.find(p => p.preview_person_id === person.preview_person_id)?.thumb;
          if (person.thumb || !thumb) return person;
          changed = true;
          return { ...person, thumb };
        });
        return { ...event, people: snapshot };
      });
      return changed ? { ...old, events } : old;
    });
  }, [key, photoKey, thumbsKey, state.events.length]);

  const live = state.key === key;
  const result = live && state.wishesKey === wishesKey ? state.result : undefined;
  const confirming = useRef(false);
  async function confirm(ids: string[], text?: string, protection_scope?: "features" | "all", selected_wish?: string, edit_scope?: "visible_hair" | "cancel", target_selection?: "listed" | "outside_preview") {
    if (!result?.question || !input.preview || confirming.current) return false;
    confirming.current = true;
    const userOrder = nextDisplayOrder();
    try {
      const updated = await api.confirmPreviewConversation(input.preview.preview_job_id, {
        presentation_requested: !budget.current.published && !emptyRoster, source_sha256: input.preview.source_sha256, analysis_model: input.model,
        token: result.token, revision: result.revision, question_id: result.question.question_id,
        wish_ids: result.question.wish_ids, target_ids: ids, target_selection, ...(text ? { text } : {}), ...(protection_scope ? { protection_scope } : {}), ...(selected_wish ? { selected_wish } : {}), ...(edit_scope ? { edit_scope } : {}),
      });
      if (current.current.key !== key || current.current.context !== context || !current.current.active) return false;
      if (updated.confirmation_handled === false) return false;
      committed.current = { key, result: updated };
      budget.current.revision = Math.max(budget.current.revision, updated.revision);
      const replyText = (!budget.current.published && !current.current.emptyRoster && updated.presentation ? `${updated.presentation.opening}\n\n${updated.presentation.closing}` : "") || updated.question?.text?.trim() || updated.reply?.trim() || "";
      const showPeople = !budget.current.published && !current.current.emptyRoster && !!replyText;
      if (showPeople) budget.current.published = true;
      setState(old => ({ ...old, result: updated, events: [...old.events.map(event => event.questionSnapshot?.token === result.token ? { ...event, questionSnapshot: { ...event.questionSnapshot, answered: true, answer: text || ids.map(id => people.current.find(p => p.preview_person_id === id)?.name || id).join("、") } } : event), { id: `${updated.token}-answer-${old.events.length}`, order: userOrder, after: input.turns,
        text: text || `指定：${ids.map(id => input.preview?.people?.find(p => p.preview_person_id === id)?.label || id).join("、")}`,
        showPeople: false, role: "user" }, ...(replyText ? [{
        id: `${updated.token}-${old.events.length}`, generation: key, order: nextDisplayOrder(), after: input.turns, text: replyText, showPeople,
        ...(updated.question ? { questionSnapshot: { ...updated.question, token: updated.token, revision: updated.revision }, people: people.current.map(p => ({ ...p })) } : {}),
        ...(showPeople ? { presentation: updated.presentation, people: people.current.map(p => ({ ...p })) } : {}),
      }] : [])] }));
      return true;
    } finally { confirming.current = false; }
  }
  async function revise(text: string, target_ids: string[] = []) {
    const previous = live ? state.result : undefined;
    if (!previous || !input.preview) return undefined;
    const nextRevision = ++budget.current.revision;
    const userOrder = nextDisplayOrder();
    const updated = await api.revisePreviewConversation(input.preview.preview_job_id, {
      presentation_requested: !budget.current.published && !emptyRoster, source_sha256: input.preview.source_sha256, analysis_model: input.model,
      token: previous.token, revision: previous.revision, next_revision: nextRevision, generation: key, wishes: input.wishes, text, target_ids, selection_source: target_ids.length ? "user_selection" : "none",
    });
    if (current.current.key !== key || current.current.context !== context || !current.current.active) return undefined;
    if (updated.token) committed.current = { key, result: updated };
    if (budget.current.revision !== nextRevision) return updated.changed ? updated : undefined;
    if (!updated.changed) {
      if (updated.token) setState(old => {
        // Rebase may issue a new token without changing the question. Refresh
        // its live authority without replacing text, people or past answers.
        let currentQuestion = false;
        const events = old.events.map(event => {
          const q = event.questionSnapshot;
          if (!q || q.answered || q.token !== previous.token || !updated.question || q.question_id !== updated.question.question_id || q.text !== updated.question.text || JSON.stringify(q.wish_ids) !== JSON.stringify(updated.question.wish_ids)) return event;
          currentQuestion = true;
          return { ...event, questionSnapshot: { ...q, token: updated.token, revision: updated.revision } };
        });
        if (updated.question && !currentQuestion) events.push({ id: updated.token, generation: key, order: nextDisplayOrder(), after: input.turns,
          text: updated.question.text, showPeople: false, people: people.current.map(p => ({ ...p })),
          questionSnapshot: { ...updated.question, token: updated.token, revision: updated.revision } });
        return { ...old, context, wishesKey, result: updated, events };
      });
      return undefined;
    }
    budget.current.revision = Math.max(budget.current.revision, updated.revision);
    const replyText = (!budget.current.published && !current.current.emptyRoster && updated.presentation ? `${updated.presentation.opening}\n\n${updated.presentation.closing}` : "") || updated.question?.text?.trim() || updated.reply?.trim() || "";
    const showPeople = !budget.current.published && !current.current.emptyRoster && !!replyText;
    if (showPeople) budget.current.published = true;
    setState(old => ({ ...old, context, wishesKey, result: updated, events: [...old.events,
      { id: `${updated.token}-user`, order: userOrder, after: input.turns, text, showPeople: false, role: "user" },
      ...(replyText ? [{ id: updated.token, generation: key, order: nextDisplayOrder(), after: input.turns, text: replyText, showPeople,
        ...(updated.question ? { questionSnapshot: { ...updated.question, token: updated.token, revision: updated.revision }, people: people.current.map(p => ({ ...p })) } : {}),
        ...(showPeople ? { presentation: updated.presentation, people: people.current.map(p => ({ ...p })) } : {}) }] : []),
    ] }));
    return updated;
  }
  const peoplePublished = !emptyRoster && live && state.photoKey === photoKey && state.events.some(event =>
    event.generation === key && event.showPeople && !!event.text.trim());
  return { peoplePublished, result, committedResult: committed.current.key === key ? committed.current.result : undefined, canRevise: live && !!state.result, events: state.photoKey === photoKey ? state.events : [], confirm, revise };
}
