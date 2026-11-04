// 對話模式 ⇄ 面板模式切換時，把這張照片還沒美化的調整一起帶過去。
//
// 兩邊是同一個 origin、同一個分頁，所以放 sessionStorage：要走的那一頁寫，
// 打開同一個 case 的那一頁讀了就刪。格式兩邊共用，面板模式那邊的讀寫在
// frontend/app.js（MODE_HANDOFF_KEY），改這裡要一起改。
//
// 用兩邊都講得通的詞，不用任何一邊自己的：美化引擎是 image_model + quality
// （對話模式的 render_option 是 id，面板模式是 "model:quality"）；修圖建議
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
  // 分析裡每個長寬比已經排好的構圖（_composition_by_ratio）。接手的那邊換到
  // 同一個長寬比時直接沿用，不重新規劃構圖。
  compositions?: Record<string, any>;
  // 正在接著某一版調整、還沒按美化：那一版（成品歷程裡的 version_id、job、
  // run）和這一輪打的字、挑的風格（只放不是預設的）。接手的那邊打開同一版、
  // 一樣是接著調整的樣子。
  continuation?: HandoffContinuation;
  // 沒有還沒美化的調整（idle），底卻不是原圖：那邊接著哪一版。接手的這邊一樣
  // 接著那一版、什麼都還沒打，跟剛美化完一樣。沒有就是原圖。
  base?: { version_id: string; job_id: string; run: number };
  // 接手的那邊要打開哪一版（成品歷程裡的 version_id）：接著調整時是調整的那一版，
  // 不然是畫面上那一版。
  version_id?: string;
  // 要走的那一頁畫面上是哪一版：跟 version_id 不同時（看著一版、接著另一版
  // 調整），接手的那邊打開 version_id 之後再切過去看這一版。
  showing_version_id?: string;
  // 沒有還沒美化的調整：那邊收起了調整中（或根本沒動），接手的這邊也收起來，
  // 不要還留著上次帶過去的調整。
  idle?: boolean;
}

export interface HandoffContinuation {
  version_id: string;
  job_id: string;
  run: number;
  text: string;
  style_options: Record<string, string>;
  // 這一輪的輸出長寬比；沒有或 "original" 是維持那一版的。
  aspect_ratio?: string;
}

// unsent 是 plan 裡還沒美化的欄位（unsentPlanKeys），null 是全部都算。其他
// 欄位不帶：那是某一版美化時用的設定，面板模式上那一版自己就有，帶過去反而
// 蓋掉它。
export function writeHandoff(caseId: string, plan: Plan, caps: Capabilities, analysis: Analysis | undefined,
  unsent: string[] | null, continuation?: HandoffContinuation, versionId?: string, base?: Handoff["base"]) {
  const has = (k: string) => !unsent || unsent.includes(k);
  const idle = !continuation && !!unsent && !unsent.length;
  // 有還沒美化的調整：那些調整是對原圖改的，接著哪一版就不算了，底不帶過去。
  if (!idle) base = undefined;
  const option = caps.render.options.find((o) => o.id === plan.render_option);
  const items = analysis ? recipeItems(analysis) : [];
  const h: Handoff = {
    case_id: caseId,
    at: Date.now(),
    image_model: has("render_option") ? option?.image_model : undefined,
    image_quality: has("render_option") ? option?.quality : undefined,
    aspect_ratio: has("aspect_ratio") ? plan.aspect_ratio : undefined,
    run_count: has("run_count") ? plan.run_count : undefined,
    advanced_options: has("advanced_options") ? plan.advanced_options : undefined,
    style_options: has("style_options") ? plan.style_options : undefined,
    face_issue_overrides: has("face_issue_overrides") ? plan.face_issue_overrides : undefined,
    body_reshape_enabled: has("body_reshape_enabled") ? plan.body_reshape_enabled : undefined,
    user_prompt: has("user_prompt") ? plan.user_prompt : undefined,
    overwrite_prompt: has("overwrite_prompt") ? plan.overwrite_prompt : undefined,
    recipe: items.length && has("recipe_checked")
      ? Object.fromEntries(items.map((t, i) => [t, (plan.recipe_checked || [])[i] !== false])) : undefined,
    compositions: analysis?._composition_by_ratio,
    continuation,
    base,
    version_id: continuation?.version_id || base?.version_id || versionId,
    showing_version_id: versionId,
    idle,
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
