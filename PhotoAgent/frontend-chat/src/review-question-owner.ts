import { t } from "./i18n";
/** Route only unoptioned person clarifications to the enabled preview bridge.
 * Legacy/off mode returns the original result unchanged. Do not classify
 * questions by matching prose: use the review's validated semantic evidence.
 */
type ReviewSemantic = {
  target_kind?: string;
  complete?: boolean;
  owner_required?: boolean;
  scope?: string;
  intent?: string;
};
type Review = {
  accepted: boolean;
  reply: string;
  ask?: boolean;
  question_kind?: "person" | "meaning" | "content";
  clarification_subtype?: string;
  options?: string[];
  offer?: boolean;
  start?: string;
  withdrawn?: boolean;
  semantic_pending?: boolean;
  request_semantics?: ReviewSemantic[];
};

export function routeReviewQuestion<T extends Review>(review: T, previewEnabled: boolean): T {
  const semantics = review.request_semantics;
  if (!previewEnabled || !review.ask || review.clarification_subtype === "body_side_reference" || review.question_kind !== "person" || review.options?.length || review.offer || review.start || review.withdrawn ||
      !semantics?.length ||
      !semantics.every(s => s.complete === true && (s.scope === "edit" || s.scope === "protection") && (s.intent === "set" || s.intent === "constraint")) ||
      !semantics.some(s => s.owner_required === true || s.target_kind === "person" || s.target_kind === "outside_person")) return review;

  // Accepted for clarification, not approved for editing. The Note stays
  // pending until the bridge resolves both action and stable person ID.
  return { ...review, accepted: true, ask: false, options: [], semantic_pending: true,
    reply: t("收到，我先確認這項需求的對象與調整內容。") };
}
