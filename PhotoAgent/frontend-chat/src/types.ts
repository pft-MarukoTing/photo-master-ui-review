export type Dict = Record<string, any>;

export type FaceOverride = false | { allowed: true; level?: string };

// The per-person overrides a 姿勢 pick wrote, each with the value it replaced.
export interface PoseFaceChanges {
  pose: string;
  changes: Record<string, { before: FaceOverride | null; after: FaceOverride }>;
}

// A face the upload page let the user point at before there was an analysis
// to name people by. `ref` is the exact text that pick wrote into
// user_prompt; the box is what the server matches against the analysis once
// it lands, so it can swap that text for the name the face panel uses.
export interface PromptFace {
  preview_person_id?: string;
  detection_index?: number;
  preview_job_id?: string;
  source_sha256?: string;
  box: { x: number; y: number; w: number; h: number };
  ref: string;
  where: string;
}

export interface Plan {
  preset?: string;
  render_option?: string;
  analysis_model?: string;
  aspect_ratio?: string;
  output_scale?: string;
  run_count?: number;
  user_prompt?: string;
  literal_texts?: string[];
  overwrite_prompt?: boolean;
  // 設定 → 詳細解說: how the assistant's replies sound.
  detailed_explanation?: boolean;
  style_options?: Record<string, string>;
  advanced_options?: Record<string, boolean>;
  recipe_checked?: boolean[];
  face_issue_overrides?: Record<string, FaceOverride>;
  body_reshape_enabled?: boolean | null;
  prompt_faces?: PromptFace[];
  pose_face_changes?: PoseFaceChanges | null;
}

export interface ActiveVersion {
  version_id: string;
  job_id: string;
  run: number;
  // When this became the photo's result. Written by the server; absent on a
  // photo last rendered before the field existed.
  at?: number;
}

export interface Photo {
  preview_conversation?: import("./preview-conversation").PreviewConversationResult;
  photo_id: string;
  filename?: string;
  case_id?: string;
  analyze_job_id: string;
  status: "analyzing" | "ready" | "reframing" | "rendering" | "failed";
  error?: string | null;
  // 改了長寬比後重新構圖的那支 job；完成後分析改從它讀（analysis_job_id）。
  pending_reframe?: { job_id: string; aspect_ratio: string; previous: string; handed?: boolean } | null;
  analysis_job_id?: string | null;
  // 上傳頁選「只照需求改」：沒有跑分析，只量了尺寸；「幫我分析」才補跑真的分析。
  analysis_skipped?: boolean;
  // 上傳時帶的 client_key（api.uploadPhoto），重試時靠它認出已經傳上去的那張。
  client_key?: string;
  // 補跑中的分析是為了什麼："check" 給使用者看，"render" 是排在它後面的美化。
  reanalysis?: "check" | "render" | null;
  planned_ratio?: string | null;
  // base "version"：接著某一版調整，source_result 是當底的那一版成品。
  pending_render?: { action_id: string; job_ids: string[]; base?: string; source_result?: SourceResult } | null;
  // 接著某一版調整時這一輪的需求，存在伺服器上；revision 防舊分頁蓋掉新的。
  continuation_draft?: {
    source_result: SourceResult;
    text: string;
    style_options?: Record<string, string>;
    revision: number;
  } | null;
  // 照片本身已經好了，AI 還在寫的開場摘要／這一版的說明（開了「詳細解說」才有）。
  pending_brief?: { job_id: string } | null;
  pending_note?: { version_id: string; job_id?: string } | null;
  queued_render?: Dict | null;
  active_version?: ActiveVersion | null;
  plan: Plan;
}

// 一版成品裡的一張：接著調整時當底的那張。
export interface SourceResult {
  version_id: string;
  job_id: string;
  run: number;
}

export interface Card {
  type: string;
  [key: string]: any;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  created_at: number;
  text: string;
  card: Card | null;
}

export interface Session {
  session_id: string;
  user: string;
  created_at: number;
  updated_at: number;
  photo_order: string[];
  active_photo_id: string | null;
  // 多張上傳的同一批：每張各自一個對話，靠這個認出是同一批。
  batch_id?: string | null;
  defaults: Plan;
  photos: Photo[];
  messages?: Message[];
}

export type SessionState = "analyzing" | "rendering" | "failed" | "done" | "ready";

// A row of the history list: a live session, or a history case no session
// holds (edited only in 面板模式, or its session expired) -- session_id null,
// opened through api.fromCase.
export interface SessionSummary {
  session_id: string | null;
  case_id?: string;
  // The history cases the row holds.
  case_ids?: string[];
  created_at: number;
  updated_at: number;
  photo_count: number;
  // The first photo's original: the tile's image until the session has a
  // result, and its fallback if that result's thumbnail will not load.
  thumbnail_job_id: string | null;
  // The newest result in the session, which is what the tile shows.
  thumbnail_result: { job_id: string; run: number } | null;
  // A session of several photos: its first four, each its result (run) or
  // else its original (run null). Empty for one photo.
  thumbnails?: { job_id: string; run: number | null }[];
  status: SessionState;
  rendered_count: number;
  // 多張上傳的同一批合成一格：session_id 是最後動過的那張，這裡是整批（上傳順序）。
  session_ids?: string[];
  batch_id?: string | null;
}

export interface Choice {
  label: string;
  value: string;
}

export interface Capabilities {
  features?: { subject_preview?: boolean; preview_introduction?: boolean; subject_preview_format_version?: number; low_confidence_candidates?: boolean; render_from_version?: boolean };
  render: {
    analysis_models: { id: string; label: string }[];
    aspect_ratios: Choice[];
    // sizes: "WxH" per fixed ratio; megapixels for auto/original, whose ratio is not known yet.
    output_scales?: (Choice & { sizes?: Record<string, string>; megapixels?: number })[];
    defaults: Dict;
    max_run_count: number;
    // scalable: the engine honours output_scale; the others render at their own size.
    options: { id: string; label: string; image_model: string; quality: string; scalable?: boolean }[];
    image_models: { id: string; label: string }[];
  };
  style_options: { id: string; label: string; default: string; choices: Choice[] }[];
  advanced_options: { key: string; label: string; default: boolean }[];
  portrait_fixes: Dict[];
  presets: Dict[];
}

export interface Analysis {
  subjects?: Dict[];
  local_face_review?: Dict[];
  _local_face_boxes?: Dict[];
  _face_crop_boxes?: number[][];
  retouch_recipe?: string[];
  [key: string]: any;
}

export interface Version {
  version_id: string;
  job_ids: string[];
  failed_job_ids: string[];
  ordinal: number;
  // 接著哪一版調整出來的（那一版的 ordinal）；從原圖產生的沒有。
  from?: number;
}
