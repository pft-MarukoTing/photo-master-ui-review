import type { Analysis, Capabilities, Dict, Message, Plan, Version } from "./types";

const pad = (n: number) => String(n).padStart(2, "0");

// 今天 HH:MM / 昨天 HH:MM / M/D, from epoch seconds.
// Calendar days apart, not 24-hour spans: 23:50 and 00:10 are 昨天/今天.
function daysAgo(d: Date): number {
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  return Math.round((midnight(new Date()) - midnight(d)) / 86400000);
}

export function timeLabel(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  const diff = daysAgo(d);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (diff === 0) return `今天 ${hm}`;
  if (diff === 1) return `昨天 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

// The heading over a day's worth of history tiles -- same words as the old
// UI's formatHistoryDayLabel (app.js), so both histories read the same.
export function dayLabel(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  const diff = daysAgo(d);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function clockLabel(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── Photo summary (same wording rules as the old review card) ──────────────
function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function stripFieldLabel(value: unknown, labels: string[]): string {
  let text = typeof value === "string" ? value.trim() : "";
  if (!text) return "";
  const re = new RegExp(`^\\s*(${labels.map(escapeRe).join("|")})\\s*[：:]\\s*`);
  text = text.replace(re, "").replace(/[。．.]+$/, "").trim();
  return text;
}

const joinParts = (parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export function photoSummaryText(a: Analysis): string {
  const summary = stripFieldLabel(a.photo_summary, ["照片內容", "照片摘要", "內容"]);
  const subject = stripFieldLabel(a.main_subject, ["主體", "主要主體"]);
  const scene = stripFieldLabel(a.scene_type, ["場景", "場景類型"]);
  if (summary) {
    return joinParts([
      `${summary}。`,
      subject && !summary.includes(subject) && `主體：${subject}。`,
      scene && !summary.includes(scene) && `場景：${scene}。`,
    ]);
  }
  return joinParts([subject ? `主體：${subject}。` : "已完成照片總覽辨識。", scene && `場景：${scene}。`]);
}

export function photoTags(a: Analysis): string[] {
  return [a.subject_category, a.scene_category].filter((t) => typeof t === "string" && t.trim()) as string[];
}

export function photoIssues(a: Analysis): string[] {
  const list = Array.isArray(a.detected_issues) ? a.detected_issues : [];
  return list.filter((x: unknown) => typeof x === "string").map((x: string) => x.trim()).filter(Boolean);
}

export function recipeItems(a: Analysis): string[] {
  const list = Array.isArray(a.retouch_recipe) ? a.retouch_recipe : [];
  return list.filter((x) => typeof x === "string" && x.trim());
}

export const REFRAME_RE = /^(重新構圖\s*[:：])\s*(.+)$/;
// 固定的 W:H 比例只能靠重新構圖做到，所以選了它，「重新構圖」那一項就一定要勾。
export const FIXED_RATIO_RE = /^\d+(?:\.\d+)?:\d+(?:\.\d+)?$/;

// 重新構圖中的那一句，和舊版 app.js 的 reframeLoadingMessage 同一份文案。
export function reframeLoadingMessage(ratio?: string): string {
  if (!ratio || ratio === "auto") return "由 AI 選擇比例並重新構圖中…";
  if (ratio === "original") return "依原圖比例重新構圖中…";
  return `依 ${ratio} 重新構圖中…`;
}

// ── Settings labels ─────────────────────────────────────────────────────────
export function renderOptionLabel(caps: Capabilities, id?: string): string {
  const opt = caps.render.options.find((o) => o.id === id);
  return opt ? opt.label.replace(/^GPT Image 2\.5\s*/, "") : id || "";
}

// 「Sunburst」-- 這一版用的影像模型，前綴和 renderOptionLabel 砍掉的同一個，
// 聊天版從頭到尾都是這樣叫它。
export function imageModelLabel(caps: Capabilities, renderOption?: string): string {
  const opt = caps.render.options.find((o) => o.id === renderOption);
  const model = caps.render.image_models?.find((m) => m.id === opt?.image_model);
  return (model?.label || opt?.image_model || "").replace(/^GPT Image 2\.5\s*/, "");
}

export function aspectLabel(caps: Capabilities, value?: string): string {
  return caps.render.aspect_ratios.find((a) => a.value === value)?.label || value || "";
}

// 「＿重新構圖」前面那一段：依 4:5、依原圖比例、由 AI 選比例。
export function reframeBy(ratio?: string): string {
  if (!ratio || ratio === "auto") return "由 AI 選比例";
  if (ratio === "original") return "依原圖比例";
  return `依 ${ratio} `;
}

export function analysisModelLabel(caps: Capabilities, id?: string): string {
  return caps.render.analysis_models.find((m) => m.id === id)?.label || id || "";
}

// 美化中的副標：和舊版 app.js 的 editSubMessages（AI 美化那一支）同一份文案，
// 只有模型名稱跟著聊天版的叫法。舊版沒有一句在講第幾版 -- 第幾版是美化紀錄的
// 事，等的人想知道的是現在在做什麼。
// 舊版那句「（已完成 X/N 張）」這裡拿掉：聊天版只輪詢整張照片的狀態，拿不到
// 單一 RUN 的進度，寫了也是假的。
export function renderSubMessages(caps: Capabilities, plan: Plan): string[] {
  const model = imageModelLabel(caps, plan.render_option);
  return [
    model ? `AI 正在使用 ${model} 美化，請稍候…` : "AI 正在美化，請稍候…",
    "AI 正在調整光影與細節，請稍候…",
    "AI 正在細修色調，請稍候…",
    "AI 正在校正白平衡與色彩，請稍候…",
    "AI 正在優化背景與構圖，請稍候…",
    "AI 正在強化畫面細節，請稍候…",
    "美化仍在進行中，感謝您的耐心等候…",
    "AI 正在進行最後的品質檢查…",
    "快完成了，請再稍候一下…",
    "即將完成，請再稍候一下…",
  ];
}

export function portraitIsDefault(caps: Capabilities, plan: Plan): boolean {
  return caps.advanced_options.every((o) => (plan.advanced_options?.[o.key] ?? o.default) === o.default);
}

// "Sunburst · Enhanced · Auto" -- the meta line under a 美化 bubble.
export function renderMeta(caps: Capabilities, plan: Plan): string {
  const opt = renderOptionLabel(caps, plan.render_option).replace(/^Sunburst\s*·\s*/, "");
  return ["Sunburst", opt, aspectLabel(caps, plan.aspect_ratio)].filter(Boolean).join(" · ");
}

// ── What a finished version was actually made with ─────────────────
// Both read the effective analysis the finish job stored with its result, not
// the photo's plan as it is now -- a photo's plan moves on with every round of
// adjusting, while these describe the one version on screen.

// Port of the old result row's read-out (results.js actualEditorPromptText /
// buildExecutedStyleOptionTagLabels / buildExecutedRecipeItems): a label only
// counts once its command reached the Editor prompt the finish job stored on
// analysis.prompts, not merely because it was picked or recommended. The
// gaze-only second pass is included because a two-pass run executes gaze there.
export function editorPromptText(a: Analysis | null | undefined): string {
  const prompts = a?.prompts;
  if (!prompts || typeof prompts !== "object") return "";
  return [prompts.image_edit, prompts.gaze_repair_pass]
    .filter((v): v is string => typeof v === "string" && !!v.trim())
    .map((v) => v.trim())
    .join("\n");
}

// A prompt written over by the user (overwrite_prompt) no longer carries any
// of the commands below, so it reads as nothing executed rather than a guess.
export function executedPrompt(a: Analysis | null | undefined): string {
  if ((a?.user_instruction as Dict | undefined)?.overwrite_prompt) return "";
  return editorPromptText(a);
}

const EXECUTED_STYLE_MARKERS: Record<string, RegExp> = {
  pose: /(?:^|\n)-\s*Pose\s*:/i,
  angle: /(?:^|\n)-\s*Angle\s*:/i,
  lighting: /(?:^|\n)-\s*Lighting\s*:/i,
  size: /(?:^|\n)-\s*Framing size\s*:/i,
};

// The non-default 修圖風格 picks the Editor prompt actually carried, as bare
// option labels ("俯拍", "夜色") -- the labels the option cards offered.
export function executedStyleLabels(caps: Capabilities, a: Analysis | null | undefined): string[] {
  const prompt = executedPrompt(a);
  const picks = (a?.style_options || {}) as Dict;
  if (!prompt) return [];
  return caps.style_options
    .map((g) => {
      const value = picks[g.id];
      const marker = EXECUTED_STYLE_MARKERS[g.id];
      if (!value || value === g.default || !marker || !marker.test(prompt)) return "";
      return g.choices.find((c) => c.value === value)?.label || "";
    })
    .filter(Boolean);
}

// The approved 美化項目 whose command appears in the Editor prompt, named by
// their retouch_recipe line (falling back to the command itself).
export function executedRecipeItems(a: Analysis | null | undefined): string[] {
  const prompt = executedPrompt(a);
  if (!prompt) return [];
  const plan: unknown[] = Array.isArray(a?.correction_plan) ? a!.correction_plan : [];
  const recipes: unknown[] = Array.isArray(a?.retouch_recipe) ? a!.retouch_recipe : [];
  const out = plan.flatMap((command, i) => {
    if (typeof command !== "string" || !command.trim() || !prompt.includes(command.trim())) return [];
    const label = recipes[i];
    return [typeof label === "string" && label.trim() ? label.trim() : command.trim()];
  });
  return [...new Set(out)];
}

// The 個人化需求 this version was submitted with. pipeline.py keeps the user's
// own wording as user_instruction.original_user_direction at the edit
// boundary; results made before that field existed only carry the reviewed
// (normalized) form, so fall back to it rather than dropping the pill for a
// version that genuinely had a request.
export function personalizationText(a: Analysis | null | undefined): string {
  const instruction = (a?.user_instruction || {}) as Dict;
  const text = instruction.original_user_direction || instruction.normalized_user_direction || "";
  return typeof text === "string" ? text.trim() : "";
}

// ── Versions (one per render_done message) ─────────────────────────────────
export function versionsFromMessages(messages: Message[], photoId: string): Version[] {
  const out: Version[] = [];
  for (const m of messages) {
    const c = m.card;
    if (c?.type === "render_done" && c.photo_id === photoId && Array.isArray(c.job_ids)) {
      const failed: string[] = c.failed_job_ids || [];
      if (c.job_ids.some((j: string) => !failed.includes(j))) {
        out.push({ version_id: c.version_id, job_ids: c.job_ids, failed_job_ids: failed, ordinal: out.length + 1 });
      }
    }
  }
  return out;
}

// ── Labels for user render bubbles, remembered per action id ────────────────
// The server's render_requested card only says a render was asked for; which
// chip the user clicked (美化 / 再美化一次 / 照建議直接美化 ...) is UI-only.
const LABEL_KEY = "photomaster-chat-render-labels";
type RenderLabel = { label: string; icon: string; meta?: string };

function readLabels(): Record<string, RenderLabel> {
  try {
    return JSON.parse(localStorage.getItem(LABEL_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

export function rememberRenderLabel(keys: string[], value: RenderLabel) {
  try {
    const all = readLabels();
    for (const k of keys) all[k] = value;
    const entries = Object.entries(all).slice(-300);
    localStorage.setItem(LABEL_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch {
    /* per-browser nicety only */
  }
}

export function renderLabelFor(keys: string[]): RenderLabel | null {
  const all = readLabels();
  for (const k of keys) if (all[k]) return all[k];
  return null;
}

export function qaText(qc: { pass?: boolean; overall_status?: string } | undefined | null): string {
  if (!qc) return "";
  return qc.pass === false ? qc.overall_status || "需人工複查" : qc.overall_status || "可展示";
}
