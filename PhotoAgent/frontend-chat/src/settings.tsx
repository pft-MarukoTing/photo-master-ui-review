import { lang, LANGS, setLang, t } from "./i18n";
import { createPortal } from "preact/compat";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Capabilities, Plan } from "./types";
import { FIXED_RATIO_RE, renderOptionLabel } from "./util";

export type Settings = Pick<Plan, "analysis_model" | "render_option" | "aspect_ratio" | "output_scale" | "advanced_options" | "detailed_explanation">;

// header 設定裡的那一項：跟著使用者走，不跟著照片走。換照片、開新對話都不會
// 清掉，重新整理也還在；引擎、長寬比跟人像修正才是每張照片各自的。
export type Prefs = Pick<Settings, "detailed_explanation">;

const PREFS_KEY = "chat-prefs";

// 分析引擎、美化引擎只在 debug UI 給選；一般使用者一律用後端的預設引擎。
// App 讀到 /api/config 時設好，設定卡都等 caps 到了才畫，所以畫的時候已經定了。
let enginesShown = false;
export function setEnginesShown(on: boolean) {
  enginesShown = on;
}
export function enginesVisible(): boolean {
  return enginesShown;
}

export function loadPrefs(): Prefs {
  let saved: Partial<Prefs> = {};
  try {
    saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
  } catch {
    /* storage may be blocked */
  }
  return {
    detailed_explanation: typeof saved.detailed_explanation === "boolean" ? saved.detailed_explanation : true,
  };
}

export function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* storage may be blocked */
  }
}

// 沒給 prefs 是後端自己的預設：拿來判斷要不要 patch plan。給了是這個使用者的
// 起點：換照片、開新對話都回到這裡。
export function defaultSettings(caps: Capabilities, prefs?: Prefs): Settings {
  const d = caps.render.defaults;
  return {
    analysis_model: d.analysis_model,
    render_option: d.render_option,
    aspect_ratio: d.aspect_ratio,
    output_scale: d.output_scale,
    advanced_options: Object.fromEntries(caps.advanced_options.map((o) => [o.key, o.default])),
    detailed_explanation: true,
    ...prefs,
  };
}

export function settingsOf(plan: Plan): Settings {
  const { analysis_model, render_option, aspect_ratio, output_scale, advanced_options } = plan;
  return {
    analysis_model, render_option, aspect_ratio, output_scale, advanced_options: { ...(advanced_options || {}) },
    detailed_explanation: !!plan.detailed_explanation,
  };
}

export function sameSettings(a: Settings, b: Settings) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// 這一輪改了哪幾項，用畫面上的名字講：「長寬比改成 16:9」「關掉閉眼修正」。
export function settingsChanges(caps: Capabilities, from: Settings, to: Settings, model = true): string[] {
  const label = (list: { label: string }[], pick: (x: any) => boolean, v?: string) => list.find(pick)?.label || v || "";
  const out: string[] = [];
  if (model && from.analysis_model !== to.analysis_model)
    out.push(t("分析引擎換成 {v1}", { v1: label(caps.render.analysis_models, (m) => m.id === to.analysis_model, to.analysis_model) }));
  if (from.render_option !== to.render_option) out.push(t("美化引擎換成 {v1}", { v1: renderOptionLabel(caps, to.render_option) }));
  if (from.aspect_ratio !== to.aspect_ratio)
    out.push(t("長寬比改成 {v1}", { v1: label(caps.render.aspect_ratios, (a) => a.value === to.aspect_ratio, to.aspect_ratio) }));
  if ((from.output_scale ?? to.output_scale) !== to.output_scale)
    out.push(t("輸出尺寸改成 {v1}", { v1: label(caps.render.output_scales || [], (s) => s.value === to.output_scale, to.output_scale) }));
  for (const o of caps.advanced_options) {
    const was = from.advanced_options?.[o.key] ?? o.default;
    const now = to.advanced_options?.[o.key] ?? o.default;
    if (was !== now) out.push(t(now ? "打開{v1}" : "關掉{v1}", { v1: o.label }));
  }
  return out;
}

// 長寬比的選項跟面板模式（frontend/app.js renderAspectRatioList）同一個樣子：「AI 建議」
// 「原圖比例」兩張卡，下面一排照比例畫出外框、由直到橫排好的固定比例。
// 選項的說明只有這兩張有；固定比例的用途寫在 capabilities 的名字括號裡。
const AR_NOTES: Record<string, string> = { auto: "依照片內容自動決定", original: "不改變照片長寬" };

function ratioParts(label: string): { name: string; hint?: string } {
  // 「9:16（限動）」, or 「9:16 (Stories)」 in another language.
  const m = /^(.+?)\s*[（(](.+)[）)]$/.exec(label);
  return m ? { name: m[1], hint: m[2] } : { name: label };
}

function RatioIcon({ value, size }: { value?: string; size: number }) {
  let body;
  if (value && FIXED_RATIO_RE.test(value)) {
    const [w0, h0] = value.split(":").map(Number);
    const k = 18 / Math.max(w0, h0);
    const w = w0 * k;
    const h = h0 * k;
    body = <rect x={(24 - w) / 2} y={(24 - h) / 2} width={w} height={h} rx="2" />;
  } else if (value === "original") {
    body = (
      <>
        <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
        <circle cx="9" cy="10" r="1.6" />
        <path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5" />
      </>
    );
  } else {
    body = (
      <>
        <path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z" />
        <path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
      </>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">
      {body}
    </svg>
  );
}

// 輸出尺寸：以長寬比算出來的尺寸再放大。只有能自訂尺寸的引擎吃得到，其他的照自己的尺寸出圖，
// 這裡就鎖住，免得選了沒作用。
export function OutputScaleField({
  caps,
  renderOption,
  aspectRatio,
  value,
  onChange,
  disabled,
  class: cls,
}: {
  caps: Capabilities;
  renderOption?: string;
  aspectRatio?: string;
  value?: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  class?: string;
}) {
  const choices = caps.render.output_scales || [];
  // 之前存的 3K／4K 已經拿掉了，拉回最大的 2K，免得畫面顯示的跟實際出圖的不一樣。
  const top = choices[choices.length - 1]?.value;
  const outOfRange = !!value && !!top && !choices.some((s) => s.value === value);
  useEffect(() => {
    if (outOfRange && !disabled) onChange(top!);
  }, [outOfRange, disabled]);
  // 「3K（實驗性）」→「3K · 3344×1888（實驗性）」：K 只是大概，實際尺寸跟著長寬比走。
  const text = (s: (typeof choices)[number]) => {
    const [, name, tag = ""] = s.label.match(/^(.*?)(\s*[（(].*[）)])?$/) || [, s.label];
    const size = aspectRatio && s.sizes?.[aspectRatio];
    const dims = size ? size.replace("x", "×") : s.megapixels ? t("約 {v1}MP", { v1: s.megapixels }) : "";
    return dims ? `${name} · ${dims}${tag}` : s.label;
  };
  if (!choices.length) return null;
  const scalable = !!caps.render.options.find((o) => o.id === renderOption)?.scalable;
  return (
    <label class={cls || "chat-field"} title={scalable ? undefined : t("這個美化引擎的輸出尺寸是固定的")}>
      <span class="chat-meta">{t("輸出尺寸")}</span>
      <select
        class="quality-select"
        value={scalable ? (outOfRange ? top : value || caps.render.defaults.output_scale) : caps.render.defaults.output_scale}
        disabled={disabled || !scalable}
        onChange={(e) => onChange((e.currentTarget as HTMLSelectElement).value)}
      >
        {choices.map((s) => (
          <option value={s.value}>{text(s)}</option>
        ))}
      </select>
    </label>
  );
}

// 鈕長得跟旁邊的 select 一樣；選單另開視窗（跟 header「設定」同一個外框），
// 手機上才有足夠的地方讓每個比例都好點。選了就關。
export function AspectRatioField({
  caps,
  value,
  onChange,
  disabled,
  class: cls,
  noAuto,
}: {
  caps: Capabilities;
  value?: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  class?: string;
  // 沒有分析可以依內容決定（接著某一版改）時不給 Auto。
  noAuto?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const isOpen = open && !disabled;
  useEffect(() => {
    if (!isOpen) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [isOpen]);
  const choices = caps.render.aspect_ratios.filter((a) => !(noAuto && a.value === "auto"));
  const current = choices.find((a) => a.value === value) || choices[0];
  const option = (a: (typeof choices)[number]) => {
    const fixed = FIXED_RATIO_RE.test(a.value);
    const { name, hint } = ratioParts(a.label);
    const note = fixed ? hint : AR_NOTES[a.value] && t(AR_NOTES[a.value]);
    return (
      <label class={fixed ? "chat-ar-shape" : "chat-ar-smart"}>
        <input
          type="radio"
          name="chat-aspect-ratio"
          value={a.value}
          checked={a.value === current?.value}
          onChange={() => {
            onChange(a.value);
            setOpen(false);
          }}
        />
        <RatioIcon value={a.value} size={fixed ? 30 : 22} />
        <span class="chat-ar-text">
          <b>{name}</b>
          {note && <small>{note}</small>}
        </span>
      </label>
    );
  };
  const smart = choices.filter((a) => !FIXED_RATIO_RE.test(a.value));
  const shapes = choices.filter((a) => FIXED_RATIO_RE.test(a.value));
  return (
    <>
      <div class={cls || "chat-field"}>
        <span class="chat-meta">{t("長寬比")}</span>
        <button
          type="button"
          class="quality-select chat-ar-trigger"
          disabled={disabled}
          aria-expanded={isOpen}
          onClick={() => setOpen((o) => !o)}
        >
          <RatioIcon value={current?.value} size={15} />
          <span>{current ? ratioParts(current.label).name : ""}</span>
        </button>
      </div>
      {isOpen &&
        createPortal(
          <div class="error-modal" id="chat-aspect-modal">
            <div class="error-modal-backdrop" onClick={() => setOpen(false)} />
            <div class="error-modal-box" role="dialog" aria-modal="true" aria-labelledby="chat-aspect-title">
              <div class="error-modal-hdr">
                <span id="chat-aspect-title" class="settings-modal-title">{t("長寬比")}</span>
                <button class="error-modal-close" type="button" aria-label={t("關閉")} onClick={() => setOpen(false)}>
                  ×
                </button>
              </div>
              <div class="chat-ar-panel" role="radiogroup" aria-labelledby="chat-aspect-title">
                {smart.length > 0 && <div class="chat-ar-smart-row">{smart.map(option)}</div>}
                {shapes.length > 0 && (
                  <>
                    <div class="chat-ar-scale" aria-hidden="true">
                      <span>{t("← 直式")}</span>
                      <span>{t("方形")}</span>
                      <span>{t("橫式 →")}</span>
                    </div>
                    <div class="chat-ar-shape-row">{shapes.map(option)}</div>
                  </>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

export function SettingsEditor({
  caps,
  settings,
  onChange,
  hideModel,
}: {
  caps: Capabilities;
  settings: Settings;
  onChange: (s: Settings) => void;
  // 總覽裡每張都分析過了：分析引擎改了也沒有照片用得到，不擺出來。
  hideModel?: boolean;
}) {
  const set = (patch: Partial<Settings>) => onChange({ ...settings, ...patch });
  const val = (e: Event) => (e.currentTarget as HTMLSelectElement).value;
  return (
    <div class="chat-card chat-settings-editor">
      <div class="chat-settings-fields">
        {!hideModel && enginesVisible() && <label class="chat-field">
          <span class="chat-meta">{t("分析引擎")}</span>
          <select class="quality-select" value={settings.analysis_model} onChange={(e) => set({ analysis_model: val(e) })}>
            {caps.render.analysis_models.map((m) => (
              <option value={m.id}>{m.label}</option>
            ))}
          </select>
        </label>}
        {enginesVisible() && <label class="chat-field">
          <span class="chat-meta">{t("美化引擎")}</span>
          <select class="quality-select" value={settings.render_option} onChange={(e) => set({ render_option: val(e) })}>
            {caps.render.options.map((o) => (
              <option value={o.id}>{renderOptionLabel(caps, o.id)}</option>
            ))}
          </select>
        </label>}
        <AspectRatioField caps={caps} value={settings.aspect_ratio} onChange={(aspect_ratio) => set({ aspect_ratio })} />
        <OutputScaleField
          caps={caps}
          renderOption={settings.render_option}
          aspectRatio={settings.aspect_ratio}
          value={settings.output_scale}
          onChange={(output_scale) => set({ output_scale })}
        />
      </div>
      <div class="chat-settings-portrait">
        <span class="chat-meta">{t("人像修正")}</span>
        {/* What the switches are for is otherwise only in our heads: they are a
            standing policy set before any photo is analysed, not a reaction to
            one. */}
        <p class="chat-note">{t("這幾項預設會偵測並修正，不想被改到的先關掉。")}</p>
        {caps.advanced_options.map((o) => {
          const on = settings.advanced_options?.[o.key] ?? o.default;
          return (
            <label class="chat-switch-row">
              <span class="ios-switch">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) =>
                    set({
                      advanced_options: {
                        ...settings.advanced_options,
                        [o.key]: (e.currentTarget as HTMLInputElement).checked,
                      },
                    })
                  }
                />
                <span class="ios-track">
                  <span class="ios-thumb" />
                </span>
              </span>
              <span>{o.label}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

// header 的「設定」：沿用面板模式 #settings-modal 的外框。改了就生效，沒有儲存鍵，
// 跟上傳頁的設定卡一樣；對話中切換也是當下就套用（app.tsx）。
export function PrefsModal(props: {
  prefs: Prefs;
  onChange: (p: Prefs) => void;
  onClose: () => void;
}) {
  const { prefs, onChange, onClose } = props;
  const set = (patch: Partial<Prefs>) => onChange({ ...prefs, ...patch });
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [onClose]);
  return createPortal(
    <div class="error-modal" id="chat-prefs-modal">
      <div class="error-modal-backdrop" onClick={onClose} />
      <div class="error-modal-box" role="dialog" aria-modal="true" aria-labelledby="chat-prefs-title">
        <div class="error-modal-hdr">
          <span id="chat-prefs-title" class="settings-modal-title">{t("設定")}</span>
          <button class="error-modal-close" type="button" aria-label={t("關閉")} onClick={onClose}>
            ×
          </button>
        </div>
        {/* 跟面板 #ap-settings-modal 的開關列同一套 markup：全域設定兩邊長得一樣，
            跟這張照片的「設定」（選單＋開關群組）一眼就分得出來。 */}
        <div class="settings-modal-body chat-prefs-body">
          <label class="option-row">
            <div class="option-text">
              <span class="option-label">{t("詳細解說")}</span>
              <span class="option-desc">{t("開啟後，助理會更詳細地說明分析、建議與每一版的成果。")}</span>
            </div>
            <span class="ios-switch">
              <input
                type="checkbox"
                checked={!!prefs.detailed_explanation}
                onChange={(e) => set({ detailed_explanation: (e.currentTarget as HTMLInputElement).checked })}
              />
              <span class="ios-track">
                <span class="ios-thumb" />
              </span>
            </span>
          </label>
          {/* 介面語言：每個語言用它自己的寫法列出來；換了就重新載入頁面。 */}
          <label class="option-row">
            <div class="option-text">
              <span class="option-label">{t("介面語言")}</span>
              <span class="option-desc">{t("切換後頁面會重新載入；之前的對話和分析維持原本的語言。")}</span>
            </div>
            <select class="quality-select" value={lang} onChange={(e) => setLang((e.currentTarget as HTMLSelectElement).value)}>
              {LANGS.map((l) => (
                <option value={l.code}>{l.name}</option>
              ))}
            </select>
          </label>
        </div>
      </div>
    </div>,
    document.body,
  );
}
