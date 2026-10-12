export type Dict = Record<string, any>;

export type FaceOverride = false | { allowed: true; level?: string };

// A face the upload page let the user point at before there was an analysis
// to name people by. `ref` is the exact text that pick wrote into
// user_prompt; the box is what the server matches against the analysis once
// it lands, so it can swap that text for the name the face panel uses.
export interface PromptFace {
  box: { x: number; y: number; w: number; h: number };
  ref: string;
  where: string;
}

export interface Plan {
  preset?: string;
  render_option?: string;
  analysis_model?: string;
  aspect_ratio?: string;
  run_count?: number;
  user_prompt?: string;
  overwrite_prompt?: boolean;
  // 設定 → 詳細解說: how the assistant's replies sound.
  detailed_explanation?: boolean;
  style_options?: Record<string, string>;
  advanced_options?: Record<string, boolean>;
  recipe_checked?: boolean[];
  face_issue_overrides?: Record<string, FaceOverride>;
  body_reshape_enabled?: boolean | null;
  prompt_faces?: PromptFace[];
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
  photo_id: string;
  filename?: string;
  case_id?: string;
  analyze_job_id: string;
  status: "analyzing" | "ready" | "reframing" | "rendering" | "failed";
  error?: string | null;
  // 改了長寬比後重新構圖的那支 job；完成後分析改從它讀（analysis_job_id）。
  pending_reframe?: { job_id: string; aspect_ratio: string; previous: string } | null;
  analysis_job_id?: string | null;
  // 上傳頁選「只照需求改」：沒有跑分析，只量了尺寸；「幫我分析」才補跑真的分析。
  analysis_skipped?: boolean;
  // 補跑中的分析是為了什麼："check" 給使用者看，"render" 是排在它後面的美化。
  reanalysis?: "check" | "render" | null;
  planned_ratio?: string | null;
  pending_render?: { action_id: string; job_ids: string[]; base?: string } | null;
  queued_render?: Dict | null;
  active_version?: ActiveVersion | null;
  plan: Plan;
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
  defaults: Plan;
  photos: Photo[];
  messages?: Message[];
}

export type SessionState = "analyzing" | "rendering" | "failed" | "done" | "ready";

// A row of the history list: a live session, or a history case no session
// holds (edited only in 自訂模式, or its session expired) -- session_id null,
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
  status: SessionState;
  rendered_count: number;
}

export interface Choice {
  label: string;
  value: string;
}

export interface Capabilities {
  render: {
    analysis_models: { id: string; label: string }[];
    aspect_ratios: Choice[];
    defaults: Dict;
    max_run_count: number;
    options: { id: string; label: string; image_model: string; quality: string }[];
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
}
