import { lang } from "./i18n";
import type { Capabilities, Dict, Message, Plan, Session, SessionSummary, SourceResult } from "./types";
import type { SideAnswer, SideQuestion } from "./side-reference";
import type { ReportPage } from "./report-page";

// The chat page is served at <BASE>/chat/, next to the old UI at <BASE>/.
export const BASE = location.pathname.replace(/\/chat(\/.*)?$/, "");
const API = `${BASE}/api`;
const CHAT = `${API}/photo-agent/chat`;

export class ApiError extends Error {
  constructor(message: string, public status: number, public body: Dict) {
    super(message);
  }
}

// One turn of the upload page's conversation, as the model reads it.
// bar: the buttons under the chat; topics: the option panels that can be opened.
export type Screen = { bar: string[]; topics: string[]; pending?: string[]; panels?: string[] };
export type AskFace = { box: { x: number; y: number; w: number; h: number }; where: string };
export type ReviewTurn = {
  role: "user" | "assistant";
  text: string;
  question?: boolean;
  offer?: boolean;
  options?: string[];
};

let currentUser = "";
export function setUser(user: string) {
  currentUser = user;
}
// What 回報 records about the chat on screen; any of them may be absent (首頁,
// the upload page, a batch overview before its sessions exist).
export interface ReportChat {
  sessionId?: string | null;
  photoId?: string | null;
  batchId?: string | null;
  // 首頁、上傳頁還沒有對話：送出時改記當下頁面上有什麼（report-page.ts）。
  page?: () => Promise<ReportPage>;
}

// 回報本身（/chat/report-session 跟對話一起回來的那幾欄）。ts 是秒。
export interface ReportRef {
  report_id: string;
  user: string;
  owner: string;
  ts: number;
  note?: string | null;
  job_id?: string | null;
  session_id?: string | null;
  photo_id?: string | null;
  batch_id?: string | null;
  status?: ReportStatus;
  // 在首頁、上傳頁送的（沒有對話，記的是頁面）。
  page?: ReportPage["page"] | null;
}

// 回報清單（舊版回報抽屜同一支 /reports/<user>）的一筆。source 是 "chat" 才是聊天
// 模式送的，帶得到對話；version_params 是 job_id 那個版本當時的設定。
export type ReportStatus = "unread" | "read" | "solved";
export interface ReportEntry extends ReportRef {
  source?: string;
  path?: string;
  status?: ReportStatus;
  user_prompt?: string | null;
  version_params?: Dict;
  // 這個 job 只做了分析、沒有結果圖，縮圖改用它的原圖。
  analyze_job_id?: string | null;
  // 沒有 job 的聊天回報，後端從對話裡挑一張當縮圖（_chat_report_thumbnail, api.py）；run 是 null 就是原圖。
  // 在上傳頁送的，src 是當時第一張照片的小圖（data URL）。
  thumbnail?: { job_id: string; run: number | null } | { src: string } | null;
}

export function getUser() {
  return currentUser;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  // The backend writes its replies, errors and analyses in the page's language.
  const headers = new Headers(init.headers);
  headers.set("X-PhotoAgent-Lang", lang);
  const res = await fetch(url, { ...init, headers });
  let body: Dict = {};
  try {
    body = await res.json();
  } catch {
    body = {};
  }
  if (!res.ok) throw new ApiError(body.error || `HTTP ${res.status}`, res.status, body);
  return body as T;
}

function q(extra: Dict = {}) {
  const params = new URLSearchParams({ user: currentUser });
  for (const [k, v] of Object.entries(extra)) if (v != null) params.set(k, String(v));
  return `?${params}`;
}

function json(method: string, body: Dict): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: currentUser, ...body }),
  };
}

// Allow the backend Chat wait (45s model + 15s queue allowance) to finish.
const PREVIEW_CONVERSATION_TIMEOUT_MS = 65000;

const sid = (id: string) => `${CHAT}/sessions/${encodeURIComponent(id)}`;

export const api = {
  bodySideSourceHash(file: Blob, filename: string) {
    const form = new FormData();
    form.append("user", currentUser); form.append("image", file, filename);
    return request<{ source_sha256: string }>(`${CHAT}/side-reference/source`, { method: "POST", body: form, signal: AbortSignal.timeout(30000) });
  },
  createPreview(file: Blob, filename: string, analysisModel: string): Promise<import("./subject-preview").PreviewStatus> {
    const form = new FormData();
    form.append("user", currentUser);
    form.append("image", file, filename);
    form.append("analysis_model", analysisModel);
    return request(`${CHAT}/previews`, { method: "POST", body: form, signal: AbortSignal.timeout(30000) });
  },
  previewStatus(jobId: string): Promise<import("./subject-preview").PreviewStatus> {
    return request(`${CHAT}/previews/${encodeURIComponent(jobId)}${q()}`, { signal: AbortSignal.timeout(10000) });
  },
  previewConversation: (id: string, body: Dict) =>
    request<import("./preview-conversation").PreviewConversationResult>(`${CHAT}/previews/${encodeURIComponent(id)}/conversation-reply`, { ...json("POST", body), signal: AbortSignal.timeout(PREVIEW_CONVERSATION_TIMEOUT_MS) }),
  revisePreviewConversation: (id: string, body: Dict) =>
    request<import("./preview-conversation").PreviewConversationResult & { changed: boolean; revision_mode?: string; affected_request_ids?: string[] }>(`${CHAT}/previews/${encodeURIComponent(id)}/conversation-revise`, { ...json("POST", body), signal: AbortSignal.timeout(PREVIEW_CONVERSATION_TIMEOUT_MS) }),
  confirmPreviewConversation: (id: string, body: Dict) =>
    request<import("./preview-conversation").PreviewConversationResult>(`${CHAT}/previews/${encodeURIComponent(id)}/conversation-confirm`, { ...json("POST", body), signal: AbortSignal.timeout(PREVIEW_CONVERSATION_TIMEOUT_MS) }),
  capabilities: () => request<Capabilities>(`${API}/capabilities`),
  config: () => request<Dict>(`${API}/config`).catch(() => ({} as Dict)),
  decryptUserId: (userId: string) =>
    request<{ user: string }>(`${API}/photo-agent/decrypt-user-id?user_id=${encodeURIComponent(userId)}`),
  // 回報: the same endpoint and fields as the old page's #report-btn
  // (photo-agent-api.js reportJob), minus the screenshot. job_id is left out
  // for a general report with no photo on screen. The chat fields point the
  // admin at the conversation itself: owner is whose session it is (name is
  // whatever the reporter typed, which may not be), and the ids are what is
  // on screen.
  report: (name: string, jobId: string | null, note: string, chat: ReportChat, page?: ReportPage) => {
    const form = new FormData();
    form.append("user", name);
    if (jobId) form.append("job_id", jobId);
    form.append("path", "local");
    if (note) form.append("note", note);
    form.append("source", "chat");
    if (currentUser) form.append("owner", currentUser);
    if (chat.sessionId) form.append("session_id", chat.sessionId);
    if (chat.photoId) form.append("photo_id", chat.photoId);
    if (chat.batchId) form.append("batch_id", chat.batchId);
    if (page) {
      form.append("page", page.page);
      // 檔案，不是一般欄位：後端（Werkzeug）對一般欄位的大小限制，十幾張小圖就會超過。
      form.append("page_state", new Blob([JSON.stringify(page)], { type: "application/json" }), "page.json");
    }
    return request<{ reported: boolean; report_id: string }>(`${API}/photo-agent/report`, { method: "POST", body: form });
  },
  demoImages: () => request<{ images: string[] }>(`${API}/photo-agent/demo-images`),
  // The local face detector on a photo that has no session yet, so the upload
  // page can offer the same 指定對象 picker the session has. Boxes only -- the
  // browser already holds the photo and crops its own thumbnails from it.
  detectFaces: (file: Blob, filename: string) => {
    const form = new FormData();
    form.append("image", file, filename);
    return request<{ faces: Dict[] }>(`${API}/photo-agent/detect-faces`, { method: "POST", body: form });
  },

  // A line typed on the upload page, where there is no session to ask in yet:
  // the same model call /ask makes, against the lines taken down so far.
  // `accepted` is whether it belongs in 個人化需求 at all; `reply` is the one
  // sentence to show either way. `history` is the conversation on the page so
  // far and `people` the faces found on the photo, left to right. `ask` means
  // the reply is a question back (`options` are tap-to-answer replies to it),
  // and `line` is the whole wish when this line answered that question.
  // `start` means the line asked for 一鍵美化 or 分析 itself; `offer` means the
  // reply only asks whether to start one (「要不要我先幫你分析？」), with
  // `options` to answer it.
  // `sections` 是個人化需求的分段：每個指定的人一段，沒指定對象的一段（`who`
  // 是空字串）。`merged`：新的一句跟某幾段衝突時，那幾段拿掉衝突部分後的整段
  // 文字（`section` 是在 `sections` 裡的索引）。`withdrawn`：這句只是把前面那
  // 項撤回（「帽子拿掉」→「帽子留著」），照 `merged` 改完就好，這句不進清單。
  // `batch`：從好幾張的總覽打的（張數、這批的「分析／美化」鍵名稱、照片清單在哪），
  // AI 就講「這幾張」、只講某一張的話請人去那張改；單張的上傳頁不帶。
  // `continuation`：「接著這張調整」打的，AI 只指向「美化」，不提分析、不改比例設定。
  promptReview: (text: string, prompt: string, history: ReviewTurn[], people: string[],
    sections: { who: string; text: string }[] = [],
    batch?: { photos: number; unrendered: number; analyze: string; render: string; where: string },
    sideContext?: { source_sha256: string; analysis_model: string; previous_token?: string }, sideAnswer?: SideAnswer,
    continuation = false) =>
    request<{
      accepted: boolean;
      clarification_subtype?: "body_side_reference";
      side_question?: SideQuestion;
      side_reference_token?: string;
      literal_texts?: string[];
      reply: string;
      verbatim?: boolean;
      interpreted?: boolean;
      semantic_pending?: boolean;
      request_semantics?: { complete: boolean; source: string; target: string; action: string;
        target_kind?: string; owner_required?: boolean; scope?: string; intent?: string;
        literal_text?: string; feature?: string }[];
      ask?: boolean;
      question_kind?: "person" | "meaning" | "content";
      offer?: boolean;
      // The offer is to analyze first and answer the line itself afterwards.
      later?: boolean;
      options?: string[];
      line?: string;
      persons?: string[];
      start?: "render" | "analyze";
      aspect_ratio?: string;
      // 「閉眼不用修」：要撥的人像修正開關，{key: on}。
      advanced_options?: { [key: string]: boolean };
      merged?: { section: number; text: string }[];
      withdrawn?: boolean;
    }>(
      `${CHAT}/prompt-review`,
      { ...json("POST", { text, prompt, history, people, sections,
        ...(batch ? {batch} : {}), ...(sideContext ? {side_context:sideContext} : {}),
        ...(sideAnswer ? {side_answer:sideAnswer} : {}), ...(continuation ? {continuation} : {}) }), signal: AbortSignal.timeout(65000) },
    ),

  // batchId：多張上傳的同一批，最近照片會把它們合成一格。
  // clientKey: 重送同一個請求時帶同一把，後端已經建過就回那一個，不會多一段（多張排著傳時的自動重試）。
  createSession: (batchId?: string, opts: { signal?: AbortSignal; clientKey?: string } = {}) =>
    request<Session>(`${CHAT}/sessions`, {
      ...json("POST", { ...(batchId ? { batch_id: batchId } : {}), ...(opts.clientKey ? { client_key: opts.clientKey } : {}) }),
      signal: opts.signal,
    }),
  listSessions: (limit = 30) =>
    request<{ sessions: SessionSummary[] }>(`${CHAT}/sessions${q({ limit })}`),
  // 多張上傳那一批的總覽對話：每一張的對話各存在自己的 session，總覽那串另外存，
  // 離開這批再從最近照片打開時接回來。內容的格式前端自己定（app.tsx SavedOverview）。
  getBatchOverview: (batchId: string) =>
    request<{ overview: Dict | null }>(`${CHAT}/batches/${encodeURIComponent(batchId)}/overview${q()}`),
  saveBatchOverview: (batchId: string, overview: Dict) =>
    request<{ ok: boolean }>(`${CHAT}/batches/${encodeURIComponent(batchId)}/overview`, json("PUT", { overview })),
  // 最近照片的刪除：幾格（對話的 id，或 `case:<case_id>`），或 "all" 全部。
  deleteRows: (rows: string[] | "all") =>
    request<{ deleted: string[] | "all" }>(`${CHAT}/sessions/delete`, json("POST", rows === "all" ? { all: true } : { rows })),
  // The session a history case opens in: its own if still live, else one
  // rebuilt from history. 409 when the chat page cannot open it at all.
  fromCase: (caseId: string) =>
    request<{ session_id: string; created: boolean }>(`${CHAT}/sessions/from-case`, json("POST", { case_id: caseId })),
  postMessage: (id: string, card: Dict, text = "") =>
    request<Message>(`${sid(id)}/messages`, json("POST", { role: "user", card, ...(text ? { text } : {}) })),
  // Several at once, in order: the upload page's conversation, carried into the session it starts.
  postMessages: (id: string, messages: { role: "user" | "assistant"; text?: string; card: Dict }[], signal?: AbortSignal) =>
    request<{ messages: Message[] }>(`${sid(id)}/messages`, { ...json("POST", { messages }), signal }),
  // Only the newest message, if the user posted it; 409 once anything came after.
  replaceMessage: (id: string, messageId: string, card: Dict) =>
    request<Message>(`${sid(id)}/messages/${encodeURIComponent(messageId)}`, json("PUT", { card })),
  // The whole log (the server keeps at most 500): the page reads the analysis,
  // versions and rounds from it, so a cut-off start breaks them.
  getSession: (id: string, messagesLimit = 500, signal?: AbortSignal) =>
    request<Session>(`${sid(id)}${q({ messages_limit: messagesLimit })}`, { signal }),
  status: (id: string) => request<Dict>(`${sid(id)}/status${q()}`),
  uploadPhoto: (id: string, file: Blob, filename: string, preview?: { preview_job_id: string; source_sha256: string; conversation_token?: string; conversation_wishes_digest?: string }, wishes?: import("./preview-conversation").PreviewWish[], opts: { signal?: AbortSignal; clientKey?: string; sideToken?: string } = {}) => {
    const form = new FormData();
    form.set("user", currentUser);
    // 同 createSession：已經傳上去的回 200 帶那張；同一把還在傳的回 409 upload_in_progress。
    if (opts.clientKey) form.set("client_key", opts.clientKey);
    if (opts.sideToken) form.set("side_reference_token", opts.sideToken);
    form.append("image", file, filename);
    if (preview) {
      form.append("preview_job_id", preview.preview_job_id);
      form.append("source_sha256", preview.source_sha256);
      if (preview.conversation_token) form.append("conversation_token", preview.conversation_token);
      if (preview.conversation_wishes_digest) form.append("conversation_wishes_digest", preview.conversation_wishes_digest);
    }
    if (wishes?.length) form.append("conversation_wishes", JSON.stringify(wishes));
    return request<{ photos: Dict[]; errors: Dict[] }>(`${sid(id)}/photos`, { method: "POST", body: form, signal: opts.signal });
  },
  // 一次傳好幾張：同一個請求、同一張 photos_added，跟單張走同一個端點。多張時
  // 不帶人物預覽，也沒有上傳頁的個人化需求。
  uploadPhotos: (id: string, files: { blob: Blob; name: string }[]) => {
    const form = new FormData();
    form.set("user", currentUser);
    for (const f of files) form.append("image", f.blob, f.name);
    return request<{ photos: Dict[]; errors: Dict[] }>(`${sid(id)}/photos`, { method: "POST", body: form });
  },
  confirmPhotoConversation: (id: string, photoId: string, body: Dict) =>
    request<Dict>(`${sid(id)}/photos/${encodeURIComponent(photoId)}/conversation-confirm`, { ...json("POST", body), signal: AbortSignal.timeout(PREVIEW_CONVERSATION_TIMEOUT_MS) }),
  // 管理者從回報打開那段對話（唯讀）：照原樣讀對話主人的 session，不推進、不同步。
  // 多張上傳的回報帶回同一批的每個 session，和總覽存下來的對話（沒有就是 null）。
  // 預設是回報當下存下來的那份；live 改讀對話現在的樣子。kept：這筆有沒有存當下那份
  // （這功能上線前的回報沒有，只能看現在的）。
  reportSession: (reportId: string, live = false) =>
    request<{ sessions: Session[]; overview: Dict | null; report: ReportRef; live: boolean; kept: boolean; page?: ReportPage | null }>(
      `${CHAT}/report-session${q({ report: reportId, ...(live ? { live: "1" } : {}) })}`,
    ),
  // 回報清單，只有管理者讀得到（不是管理者就 403，順便當成「是不是管理者」的判斷）。
  reports: (offset = 0, limit = 50) =>
    request<{ reports: ReportEntry[]; has_more: boolean }>(
      `${API}/photo-agent/reports/${encodeURIComponent(currentUser)}?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`,
    ),
  setReportStatus: (reportId: string, status: ReportStatus) =>
    request<Dict>(`${API}/photo-agent/reports/${encodeURIComponent(currentUser)}/${encodeURIComponent(reportId)}`, json("PATCH", { status })),
  reportAnalysis: (reportId: string, sessionId: string, photoId: string, live = false) =>
    request<Dict>(
      `${CHAT}/report-session/analysis${q({ report: reportId, session_id: sessionId, photo_id: photoId, ...(live ? { live: "1" } : {}) })}`,
    ),
  analysis: (id: string, photoId: string) =>
    request<Dict>(`${sid(id)}/photos/${encodeURIComponent(photoId)}/analysis${q()}`),
  // A typed message about one photo; subjectIndices are the faces the user tapped.
  // screen: the buttons the user can press right now, so the reply can point at them.
  // faces: a photo with no analysis has no subjects to index, so the taps go
  // as the local detector's boxes and where each stands, as on the upload page.
  ask: (id: string, photoId: string, text: string, subjectIndices: number[] = [], screen?: Screen, faces?: AskFace[], answerTo?: string, sideAnswer?: SideAnswer) =>
    request<{ messages: Message[] }>(
      `${sid(id)}/photos/${encodeURIComponent(photoId)}/ask`,
      { ...json("POST", { text, ...(subjectIndices.length ? { subject_indices: subjectIndices } : {}),
        ...(faces?.length ? { faces } : {}), ...(screen ? { screen } : {}), ...(answerTo ? { answer_to: answerTo } : {}),
        ...(sideAnswer || {}) }),
        signal: AbortSignal.timeout(65000) },
    ),
  // Answers a question that was posted as a waiting ask, once the photo is analyzed.
  answerWaiting: (id: string, photoId: string, messageId: string, screen?: Screen) =>
    request<{ messages: Message[] }>(
      `${sid(id)}/photos/${encodeURIComponent(photoId)}/ask`,
      json("POST", { waiting: messageId, ...(screen ? { screen } : {}) }),
    ),
  patchPlan: (id: string, plan: Plan, signal?: AbortSignal) =>
    request<Dict>(`${sid(id)}/plan`, { ...json("PATCH", { plan }), signal }),
  patchPhotoPlan: (id: string, photoId: string, plan: Plan, signal?: AbortSignal) =>
    request<Dict>(`${sid(id)}/photos/${encodeURIComponent(photoId)}/plan`, { ...json("PATCH", { plan }), signal }),
  // 「幫我分析」 on a photo uploaded with 「只照需求改」, whose analysis was skipped.
  analyze: (id: string, photoId: string) =>
    request<Dict>(`${sid(id)}/photos/${encodeURIComponent(photoId)}/analyze`, json("POST", {})),
  render: (id: string, photoIds: string[], signal?: AbortSignal) =>
    request<{ renders: Dict[]; queued: string[] }>(
      `${sid(id)}/render`,
      { ...json("POST", { photo_ids: photoIds, base: "original", defer_unconfirmed: true }), signal },
    ),
  // 接著某一版調整：先存這一輪的需求，再用存下的那份去產生。
  saveContinuationDraft: (id: string, photoId: string, source: SourceResult, text: string,
    styles: Record<string, string>, revision: number) =>
    request<Session>(`${sid(id)}/photos/${encodeURIComponent(photoId)}/continuation-draft`,
      json("PUT", { source_result: source, text, style_options: styles, revision })),
  renderFromVersion: (id: string, photoId: string, source: SourceResult, draftRevision: number) =>
    request<{ renders: Dict[]; queued: string[] }>(
      `${sid(id)}/render`,
      json("POST", { photo_ids: [photoId], base: "version", source_result: source, draft_revision: draftRevision }),
    ),
  setActiveVersion: (id: string, photoId: string, v: { version_id: string; job_id: string; run: number }) =>
    request<Session>(`${sid(id)}/photos/${encodeURIComponent(photoId)}/active-version`, json("PUT", v)),
  result: (jobId: string) => {
    let pending = results.get(jobId);
    if (!pending) {
      pending = request<Dict>(`${API}/photo-agent/result/${encodeURIComponent(jobId)}`).then((r) => {
        if (r?.status !== "success") results.delete(jobId);
        return r;
      });
      pending.catch(() => results.delete(jobId));
      results.set(jobId, pending);
    }
    return pending;
  },
};

// 一版成品的結果做完就不會再變，成品卡、比較檢視、prompt 視窗各自要也只抓一次；
// 還沒做完或抓失敗的不留，下次照常重抓。
const results = new Map<string, Promise<Dict>>();

const enc = encodeURIComponent;
const withRun = (url: string, run?: number) => (run ? `${url}?run=${run}` : url);
export const urls = {
  previewImage: (job: string) => `${CHAT}/previews/${enc(job)}/image${q()}`,
  sourceImage: (job: string) => `${API}/photo-agent/source/${enc(job)}/image`,
  sourceThumb: (job: string) => `${API}/photo-agent/source/${enc(job)}/thumbnail`,
  resultImage: (job: string, run?: number) => withRun(`${API}/photo-agent/result/${enc(job)}/image`, run),
  resultThumb: (job: string, run?: number) => withRun(`${API}/photo-agent/result/${enc(job)}/thumbnail`, run),
  resultPreview: (job: string, run?: number) => withRun(`${API}/photo-agent/result/${enc(job)}/preview`, run),
  preEditImage: (job: string) => `${API}/photo-agent/result/${enc(job)}/pre_edit_image`,
  demoImage: (f: string) => `${API}/photo-agent/demo-images/${enc(f)}`,
  demoThumb: (f: string) => `${API}/photo-agent/demo-images/${enc(f)}/thumbnail`,
  styleThumb: (group: string, value: string) => `${BASE}/assets/optional_style/${group}/thumbs/${styleThumbFile(group, value)}.webp`,
  // With a case, 面板模式 opens straight on it.
  oldUi: (caseId?: string | null) => `${BASE}/${location.search}${caseId ? `#case=${encodeURIComponent(caseId)}` : ""}`,
  // 回報的唯讀檢視切過去：舊版用管理者身分打開這筆回報，跟在它的回報清單點這筆一樣。
  // job / analyze：批次裡不是回報那張的照片，指定要開哪個 job（見 app.js openReportFromHash）。
  oldUiReport: (reportId: string, open?: { job?: string; analyze?: string }) =>
    `${BASE}/${location.search}#${new URLSearchParams({ report: reportId, ...open })}`,
};

const STYLE_FILE_ALIAS: Record<string, Record<string, string>> = {
  pose: { vsign: "vsing" },
  angle: { eye: "eyelevel", high: "highshot", low: "lowshot", side: "sideshot" },
  lighting: { gold: "goldtone" },
};
function styleThumbFile(group: string, value: string) {
  return `${group}-${STYLE_FILE_ALIAS[group]?.[value] ?? value}`;
}
