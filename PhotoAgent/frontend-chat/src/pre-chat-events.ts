import type { Settings } from "./settings";
import type { HistoricalSection } from "./personalized-history";

// A turn on the upload page that is not a typed line: a settings round
// ("settings") or a snapshot of 個人化需求 ("notes"). `after` is how many
// conversation turns came before it.
export type PreviewReplyEvent = { id: string; after: number; text: string; role?: "user" | "assistant"; order?: number;
  kind?: "settings" | "notes"; sections?: HistoricalSection[]; settings?: Settings };
