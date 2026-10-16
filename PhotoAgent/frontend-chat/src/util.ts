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

// ── Job errors ──────────────────────────────────────────────────────────────
// 失敗卡上的 error 是 worker 的原始錯誤文字，error_code 是它的短代碼；和舊版
// app.js 的 friendlyError 同一套說法。OpenAI 安全審查擋下的不是暫時性錯誤，
// 同一張照片再試多半一樣，所以另外標出來，卡片好接「換一張照片」。
const SAFETY_VIOLATION_LABELS: Record<string, string> = {
  sexual: "照片可能包含裸露或性暗示內容",
  violence: "照片可能包含暴力或血腥內容",
  "self-harm": "照片可能包含自我傷害相關內容",
  self_harm: "照片可能包含自我傷害相關內容",
  hate: "照片可能包含仇恨或歧視相關內容",
  harassment: "照片可能包含騷擾相關內容",
};

export function jobErrorText(error?: string, code?: string): { text: string; safety: boolean } {
  const msg = error || "";
  if (/rejected by the safety system/i.test(msg)) {
    const listed = msg.match(/safety_violations=\[([^\]]*)\]/i)?.[1] || "";
    const reasons = listed
      .split(",")
      .map((v) => SAFETY_VIOLATION_LABELS[v.trim().replace(/^['"]|['"]$/g, "").toLowerCase()])
      .filter(Boolean);
    return {
      text: reasons.length
        ? `${[...new Set(reasons)].join("、")}，平台政策不允許編輯這類內容`
        : "照片裡可能有平台政策不允許編輯的內容（例如受著作權保護的角色、商標，或其他敏感內容）",
      safety: true,
    };
  }
  if (code === "ai_service_timeout") return { text: "系統回應逾時", safety: false };
  // 卡片自己接句號；後端寫好的中文句子本身就帶了。
  return { text: msg.replace(/[。.\s]+$/, "") || "發生未知錯誤", safety: false };
}

// ── Settings labels ─────────────────────────────────────────────────────────
export function renderOptionLabel(caps: Capabilities, id?: string): string {
  const opt = caps.render.options.find((o) => o.id === id);
  return opt ? opt.label.replace(/^GPT Image 2\.5\s*/, "") : id || "";
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

// 處理中聊天卡上的工作紀錄，隔幾秒多一句、講過的留著（useWorkLog）。
// 美化那份從舊版 app.js 的 editSubMessages（AI 美化那一支）來，但不講用哪個模型
// 修 -- 那是設定裡的事，等的人不需要在這裡看到引擎名稱。疊成紀錄後每句都掛「請稍候」會一路重複，所以拿掉，「美化仍在進行中」
// 和「即將完成」這種只是換句話等的也拿掉。
// 舊版那句「（已完成 X/N 張）」也不放：聊天版只輪詢整張照片的狀態，拿不到單一
// RUN 的進度，寫了也是假的。
export function renderSubMessages(): string[] {
  return [
    "AI 正在美化照片…",
    "正在調整光影與細節…",
    "正在細修色調…",
    "正在校正白平衡與色彩…",
    "正在優化背景與構圖…",
    "正在強化畫面細節…",
    "正在做最後的品質檢查…",
    "快完成了，再等一下…",
  ];
}

// 「只照需求改」那份。這條路只照個人化需求修，沒有分析、也不動其他地方，所以
// 不能講光影、白平衡、構圖那些 -- 使用者只說要換背景，卻看到「正在校正白平衡」
// 會以為整張都被動了。需求內容不知道是什麼，每句都只講「你寫的」、不猜項目。
export function promptSubMessages(): string[] {
  return [
    "正在讀你寫的需求…",
    "正在照你的需求調整照片…",
    "正在處理你指定的地方…",
    "其他地方會保持原樣…",
    "正在讓改過的地方跟周圍自然接上…",
    "正在做最後的品質檢查…",
    "快完成了，再等一下…",
  ];
}

// 分析中的那份。第一句講完分析之後接著做什麼（一鍵美化會自動美化、有問題等著
// 回答），後面照分析結果欄位的順序（prompts.py 的分析 schema：類型與場景 → 問題
// 診斷 → 光影目標 → 構圖 → 水平透視 → 干擾元素 → 長寬比裁切 → 色彩 → 修復需求 →
// 美化配方）一項一句，講的都是它真的有在看的東西。分析大約要一分鐘，12 句平均 5 秒
// 一句剛好鋪滿，不會太早講到「快好了」。
// 照片裡不一定有人（風景、商品、食物），所以每一句都不提人、臉、膚色 -- 拍一盤
// 菜卻看到「正在找出照片裡的每個人」很怪。
export function analyzeSubMessages(renderQueued: boolean, waiting: boolean): string[] {
  const then = renderQueued ? "，分析完會自動開始美化" : waiting ? "，分析完就回答你的問題" : "";
  return [
    `正在辨識照片中的主體、光線與構圖${then}…`,
    "正在判斷照片的類型與場景…",
    "正在找出畫面目前不夠精緻的地方…",
    "正在決定成品的光影與色彩方向…",
    "正在評估構圖與取景方式…",
    "正在檢查水平線與透視有沒有歪…",
    "正在找出背景裡可能干擾的元素…",
    "正在評估裁切範圍與長寬比…",
    "正在檢查曝光、白平衡與色彩…",
    "正在評估清晰度與雜訊…",
    "正在整理建議調整的項目…",
    "快分析好了，再等一下…",
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

// 這則訊息是不是在講這張照片。大多數卡片帶一個 photo_id；好幾張共用的那幾種
// 改列一串（photos_added 的 photo_ids、render_requested 的 renders 和 queued）；
// 什麼都沒列的是整個對話的事。跟後端 routes/chat.py 的 _about 同一套。
export function cardAbout(card: Dict | null | undefined, photoId: string): boolean {
  if (!card) return true;
  if (card.photo_id) return card.photo_id === photoId;
  const ids: string[] = [
    ...(card.photo_ids || []),
    ...(card.queued || []),
    ...((card.renders || []) as Dict[]).map((r) => r?.photo_id),
  ];
  return !ids.length || ids.includes(photoId);
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

// 下載成品：跟面板模式（app.js downloadCurrentResultImage）一樣看檔頭決定副檔名。
// 成品現在存成 JPEG，但較早的工作 /result/<job>/image 還是回 PNG，寫死副檔名會
// 讓舊檔名不對。iPhone／iPad 走分享面板才存得進照片，其他照舊用 <a download>。
// 回傳下載成不成功，給畫面跳「已下載完成。」或錯誤；分享面板按取消也算完成，
// 跟面板模式一樣 -- 頁面分不出使用者在面板裡到底存了沒。
function imageFormatOf(buf: ArrayBuffer): { ext: string; mime: string } {
  const h = new Uint8Array(buf, 0, Math.min(4, buf.byteLength));
  return h[0] === 0x89 && h[1] === 0x50 && h[2] === 0x4e && h[3] === 0x47
    ? { ext: "png", mime: "image/png" }
    : { ext: "jpg", mime: "image/jpeg" };
}

const isIOS = () =>
  /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

function saveVia(href: string, name: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

declare global {
  interface Window {
    PhotoAgentZip?: { createZip: (files: { name: string; data: Uint8Array }[]) => Blob };
  }
}

// 「全部下載」：跟面板模式的批次全部下載（app.js buildAllBatchResultsZip）同一個 ZIP
// 長相 -- 每張一個「image N/」資料夾，裡面放原圖，美化過的再加 result_<run>。
// 一次只抓三張，跟面板模式一樣，一大批同時抓瀏覽器連線會撐不住。
export type ZipPhoto = { source: string; result?: { url: string; run: number } | null };

export async function downloadPhotosZip(photos: ZipPhoto[]): Promise<boolean> {
  const zip = window.PhotoAgentZip;
  if (!zip) return false;
  const fetchBytes = async (url: string) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  };
  const ext = (data: Uint8Array) => imageFormatOf(data.buffer as ArrayBuffer).ext;
  const groups: { name: string; data: Uint8Array }[][] = new Array(photos.length);
  let next = 0;
  const worker = async () => {
    while (next < photos.length) {
      const i = next++;
      const p = photos[i];
      const prefix = `image ${i + 1}/`;
      const source = await fetchBytes(p.source);
      const files = [{ name: `${prefix}source.${ext(source)}`, data: source }];
      if (p.result) {
        const result = await fetchBytes(p.result.url);
        files.push({ name: `${prefix}result_${p.result.run}.${ext(result)}`, data: result });
      }
      groups[i] = files;
    }
  };
  let blob: Blob;
  try {
    await Promise.all(Array.from({ length: Math.min(3, photos.length) }, worker));
    blob = zip.createZip(groups.flat());
  } catch {
    return false;
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  const d = new Date();
  const name = `PhotoMaster-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.zip`;
  const href = URL.createObjectURL(blob);
  saveVia(href, name);
  setTimeout(() => URL.revokeObjectURL(href), 10000);
  return true;
}

export async function downloadImage(url: string, base: string): Promise<boolean> {
  let file: File;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    const { ext, mime } = imageFormatOf(buf);
    file = new File([buf], `${base}.${ext}`, { type: mime });
  } catch {
    return false;
  }
  if (isIOS() && navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file] });
      return true;
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return true;
    }
  }
  const href = URL.createObjectURL(file);
  saveVia(href, file.name);
  setTimeout(() => URL.revokeObjectURL(href), 10000);
  return true;
}
