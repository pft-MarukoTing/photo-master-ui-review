import { nextDisplayOrder } from "./display-order";
import type { FaceRef } from "./chat";
import type { Settings } from "./settings";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "./api";
import type { ReviewTurn } from "./api";
import type { PreviewStatus } from "./subject-preview";

export type PreviewWish = { wish_id: string; text: string; source_text?: string; target_ids: string[]; revision?: number;
  selection_source?: "none" | "user_selection" | "model_suggestion" };
export type PreviewClause = { wish_id: string; request_id: string; text: string; source_text: string;
  status: "pending" | "matched" | "ambiguous" | "unmatched" | "not_person" | "cancelled" | "superseded";
  display_text?: string; display_target_ids?: string[]; edit_scope_pending?: boolean; cancelled_from?: {text: string};
  scope: "edit" | "protection" | "query"; target_ids: string[]; protected_ids: string[]; referent: string };
export type PreviewConversationResult = { token: string; revision: number; wishes_digest: string; reply: string;
  clauses: PreviewClause[]; effective_prompt: string;
  confirmation_handled?: boolean;
  question: { question_id: string; wish_ids: string[]; text: string; kind?: "scope" | "wish" | "edit_scope"; wish_options?: {wish_id: string; text: string}[]; options?: string[] } | null };
export type PreviewDisplayPerson = FaceRef & { previewRole?: string; preview_person_id?: string };
export type PreviewReplyEvent = { id: string; after: number; text: string; showPeople: boolean; role?: "user" | "assistant"; order?: number; generation?: string | null; kind?: "settings"; settings?: Settings; people?: PreviewDisplayPerson[] };

type Input = { photoKey?: string; people?: PreviewDisplayPerson[]; identity: string | null; preview?: PreviewStatus; model: string; history: ReviewTurn[];
  historyWithEvents?: (events: PreviewReplyEvent[]) => ReviewTurn[]; wishes: PreviewWish[]; prompt: string; busy: boolean; blocked: boolean; turns: number; active: boolean };

// A reply is accepted only against the exact source and complete conversation
// revision that requested it. Draft typing and thumbnail loading are not revisions.
export function usePreviewConversation(input: Input) {
  const context = JSON.stringify([input.history, input.wishes, input.prompt]);
  const wishesKey = JSON.stringify(input.wishes);
  const key = input.identity;
  const photoKey = input.photoKey ?? key;
  const people = useRef(input.people || []);
  people.current = input.people || [];
  const current = useRef({ key, context, active: input.active });
  current.current = { key, context, active: input.active };
  useEffect(() => () => { current.current.active = false; }, []);
  const [state, setState] = useState<{ key: string | null; photoKey: string | null; context: string; wishesKey?: string; result?: PreviewConversationResult; events: PreviewReplyEvent[] }>({ key, photoKey, context, events: [] });
  const budget = useRef({ key, attempts: 0, revision: 0, completed: false, published: false, lastContext: "", lastWishes: "", flight: false });
  const [tick, setTick] = useState(0);
  if (budget.current.key !== key) budget.current = { key, attempts: 0, revision: 0, completed: false, published: false, lastContext: "", lastWishes: "", flight: false };
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
    const initial = !b.published;
    // Existing bindings belong to the same exact source clause, not a new
    // wish that happened to occupy its array position after an editor change.
    const previousToken = state.key === key ? state.result?.token : undefined;
    void api.previewConversation(preview.preview_job_id, {
      generation: key, source_sha256: preview.source_sha256, analysis_model: input.model,
      revision, wishes: input.wishes, history: (input.historyWithEvents?.(state.photoKey === photoKey ? state.events : []) || input.history), effective_prompt: input.prompt,
      previous_token: previousToken,
    }).then(result => {
      if (current.current.key !== key || current.current.context !== context || !current.current.active || b.revision !== revision) return;
      b.completed = true;
      setState(old => {
        const events = old.photoKey === photoKey ? old.events : [];
        const text = (result.question?.text?.trim() || (initial ? result.reply?.trim() : "") || "");
        if (text) b.published = true;
        return { key, photoKey, context, wishesKey, result, events: text ? [...events, {
          id: result.token, generation: key, order: nextDisplayOrder(), after: input.turns, text, showPeople: initial,
          ...(initial ? { people: people.current.map(p => ({ ...p })) } : {}),
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
  async function confirm(ids: string[], text?: string, protection_scope?: "features" | "all", selected_wish?: string, edit_scope?: "visible_hair" | "cancel") {
    if (!result?.question || !input.preview || confirming.current) return false;
    confirming.current = true;
    const userOrder = nextDisplayOrder();
    try {
      const updated = await api.confirmPreviewConversation(input.preview.preview_job_id, {
        source_sha256: input.preview.source_sha256, analysis_model: input.model,
        token: result.token, revision: result.revision, question_id: result.question.question_id,
        wish_ids: result.question.wish_ids, target_ids: ids, ...(text ? { text } : {}), ...(protection_scope ? { protection_scope } : {}), ...(selected_wish ? { selected_wish } : {}), ...(edit_scope ? { edit_scope } : {}),
      });
      if (current.current.key !== key || current.current.context !== context || !current.current.active) return false;
      if (updated.confirmation_handled === false) return false;
      budget.current.revision = Math.max(budget.current.revision, updated.revision);
      const replyText = updated.question?.text?.trim() || updated.reply?.trim() || "";
      const showPeople = !budget.current.published && !!replyText;
      if (replyText) budget.current.published = true;
      setState(old => ({ ...old, result: updated, events: [...old.events, { id: `${updated.token}-answer-${old.events.length}`, order: userOrder, after: input.turns,
        text: text || `指定：${ids.map(id => input.preview?.people?.find(p => p.preview_person_id === id)?.label || id).join("、")}`,
        showPeople: false, role: "user" }, ...(replyText ? [{
        id: `${updated.token}-${old.events.length}`, generation: key, order: nextDisplayOrder(), after: input.turns, text: replyText, showPeople,
        ...(showPeople ? { people: people.current.map(p => ({ ...p })) } : {}),
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
      source_sha256: input.preview.source_sha256, analysis_model: input.model,
      token: previous.token, revision: previous.revision, next_revision: nextRevision, generation: key, wishes: input.wishes, text, target_ids, selection_source: target_ids.length ? "user_selection" : "none",
    });
    if (current.current.key !== key || current.current.context !== context || !current.current.active) return undefined;
    if (budget.current.revision !== nextRevision) return updated.changed ? updated : undefined;
    if (!updated.changed) {
      if (updated.token) setState(old => ({ ...old, context, wishesKey, result: updated }));
      return undefined;
    }
    budget.current.revision = Math.max(budget.current.revision, updated.revision);
    const replyText = updated.question?.text?.trim() || updated.reply?.trim() || "";
    const showPeople = !budget.current.published && !!replyText;
    if (replyText) budget.current.published = true;
    setState(old => ({ ...old, context, wishesKey, result: updated, events: [...old.events,
      { id: `${updated.token}-user`, order: userOrder, after: input.turns, text, showPeople: false, role: "user" },
      ...(replyText ? [{ id: updated.token, generation: key, order: nextDisplayOrder(), after: input.turns, text: replyText, showPeople,
        ...(showPeople ? { people: people.current.map(p => ({ ...p })) } : {}) }] : []),
    ] }));
    return updated;
  }
  const peoplePublished = live && state.photoKey === photoKey && state.events.some(event =>
    event.generation === key && event.showPeople && !!event.text.trim());
  return { peoplePublished, result, canRevise: live && !!state.result, events: state.photoKey === photoKey ? state.events : [], confirm, revise };
}
