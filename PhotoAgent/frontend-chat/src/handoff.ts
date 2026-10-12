// 對話模式 ⇄ 自訂模式切換時，把這張照片還沒美化的調整一起帶過去。
//
// 兩邊是同一個 origin、同一個分頁，所以放 sessionStorage：要走的那一頁寫，
// 打開同一個 case 的那一頁讀了就刪。格式兩邊共用，自訂模式那邊的讀寫在
// frontend/app.js（MODE_HANDOFF_KEY），改這裡要一起改。
//
// 用兩邊都講得通的詞，不用任何一邊自己的：美化引擎是 image_model + quality
// （對話模式的 render_option 是 id，自訂模式是 "model:quality"）；修圖建議
// 用文字對勾選，不用位置——兩邊列的清單順序、有沒有濾掉關閉的人像修正都不一樣。
import type { Analysis, Capabilities, FaceOverride, Plan } from "./types";
import { recipeItems } from "./util";

const KEY = "photoagent:mode-handoff";
// 只接剛剛切過來的：留太久的（例如中途關掉、隔天再開）不算數。
const MAX_AGE_MS = 2 * 60 * 1000;

export interface Handoff {
  case_id: string;
  at: number;
  image_model?: string;
  image_quality?: string;
  aspect_ratio?: string;
  run_count?: number;
  advanced_options?: Record<string, boolean>;
  style_options?: Record<string, string>;
  face_issue_overrides?: Record<string, FaceOverride>;
  body_reshape_enabled?: boolean | null;
  user_prompt?: string;
  overwrite_prompt?: boolean;
  recipe?: Record<string, boolean>;
}

export function writeHandoff(caseId: string, plan: Plan, caps: Capabilities, analysis?: Analysis) {
  const option = caps.render.options.find((o) => o.id === plan.render_option);
  const items = analysis ? recipeItems(analysis) : [];
  const h: Handoff = {
    case_id: caseId,
    at: Date.now(),
    image_model: option?.image_model,
    image_quality: option?.quality,
    aspect_ratio: plan.aspect_ratio,
    run_count: plan.run_count,
    advanced_options: plan.advanced_options,
    style_options: plan.style_options,
    face_issue_overrides: plan.face_issue_overrides,
    body_reshape_enabled: plan.body_reshape_enabled,
    user_prompt: plan.user_prompt,
    overwrite_prompt: plan.overwrite_prompt,
    recipe: items.length ? Object.fromEntries(items.map((t, i) => [t, (plan.recipe_checked || [])[i] !== false])) : undefined,
  };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(h));
  } catch {
    // 存不了就照舊切過去，只是調整不會跟著走。
  }
}

// 這個 case 的 handoff，讀了就刪；不是這個 case 的也刪，免得之後誤套。
export function takeHandoff(caseId: string): Handoff | null {
  let h: Handoff | null = null;
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    h = raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
  if (!h || h.case_id !== caseId || !(Date.now() - (h.at || 0) < MAX_AGE_MS)) return null;
  return h;
}

// handoff 換成這張照片 plan 的 patch。修圖建議要等分析到了才對得上，所以
// analysis 沒有時就先不帶那一項。
export function handoffPatch(h: Handoff, current: Plan, caps: Capabilities, analysis?: Analysis | null): Plan {
  const patch: Plan = {};
  const option = caps.render.options.find((o) => o.image_model === h.image_model && o.quality === h.image_quality);
  if (option) patch.render_option = option.id;
  if (h.aspect_ratio && caps.render.aspect_ratios.some((a) => a.value === h.aspect_ratio)) patch.aspect_ratio = h.aspect_ratio;
  if (typeof h.run_count === "number") patch.run_count = Math.max(1, Math.min(h.run_count, caps.render.max_run_count));
  if (h.advanced_options) patch.advanced_options = h.advanced_options;
  if (h.style_options) patch.style_options = h.style_options;
  // face_issue_overrides 在後端是逐鍵合併的，所以這邊沒有的鍵要明確送 null 清掉，
  // 對話模式原本的設定才不會殘留下來。
  if (h.face_issue_overrides) {
    const cleared = Object.keys(current.face_issue_overrides || {}).map((k) => [k, null]);
    patch.face_issue_overrides = { ...Object.fromEntries(cleared), ...h.face_issue_overrides };
  }
  if (h.body_reshape_enabled !== undefined) patch.body_reshape_enabled = h.body_reshape_enabled;
  if (typeof h.user_prompt === "string") patch.user_prompt = h.user_prompt;
  if (typeof h.overwrite_prompt === "boolean") patch.overwrite_prompt = h.overwrite_prompt;
  if (h.recipe && analysis) {
    const recipe = h.recipe;
    patch.recipe_checked = recipeItems(analysis).map((t) => recipe[t] !== false);
  }
  return patch;
}
