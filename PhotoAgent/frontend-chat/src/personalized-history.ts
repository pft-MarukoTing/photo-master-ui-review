import type { FaceRef } from "./chat";
import type { PreviewReplyEvent } from "./pre-chat-events";

export type HistoricalSection = { key: string; line: string; text: string; persons: FaceRef[] };

// Thumbnail loading is not a new requirement version. Freeze every published
// version; later edits/cancellations affect execution, never these records.
export function sectionsIdentity(sections: HistoricalSection[]): string {
  return JSON.stringify(sections.map(s => [s.key, s.line, s.text,
    s.persons.map(p => [p.name, p.where])]));
}

export function appendNotesHistory(events: PreviewReplyEvent[], sections: HistoricalSection[],
  order: number, after: number): PreviewReplyEvent[] {
  const previous = events[events.length - 1];
  if ((!previous && !sections.length) ||
      (previous && sectionsIdentity(previous.sections || []) === sectionsIdentity(sections))) return events;
  return [...events, { id: `notes-${order}`, kind: "notes", order, after, text: "",
    sections: JSON.parse(JSON.stringify(sections)) }];
}
