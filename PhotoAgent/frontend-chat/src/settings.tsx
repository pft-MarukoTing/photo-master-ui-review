import { createPortal } from "preact/compat";
import { useEffect } from "preact/hooks";
import type { Capabilities, Plan } from "./types";
import { renderOptionLabel } from "./util";

export type Settings = Pick<Plan, "analysis_model" | "render_option" | "aspect_ratio" | "advanced_options" | "detailed_explanation">;

// header 設定裡的那一項：跟著使用者走，不跟著照片走。換照片、開新對話都不會
// 清掉，重新整理也還在；引擎、長寬比跟人像修正才是每張照片各自的。
export type Prefs = Pick<Settings, "detailed_explanation">;

const PREFS_KEY = "chat-prefs";

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
    advanced_options: Object.fromEntries(caps.advanced_options.map((o) => [o.key, o.default])),
    detailed_explanation: true,
    ...prefs,
  };
}

export function settingsOf(plan: Plan): Settings {
  const { analysis_model, render_option, aspect_ratio, advanced_options } = plan;
  return {
    analysis_model, render_option, aspect_ratio, advanced_options: { ...(advanced_options || {}) },
    detailed_explanation: !!plan.detailed_explanation,
  };
}

export function sameSettings(a: Settings, b: Settings) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function SettingsEditor({
  caps,
  settings,
  onChange,
}: {
  caps: Capabilities;
  settings: Settings;
  onChange: (s: Settings) => void;
}) {
  const set = (patch: Partial<Settings>) => onChange({ ...settings, ...patch });
  const val = (e: Event) => (e.currentTarget as HTMLSelectElement).value;
  return (
    <div class="chat-card chat-settings-editor">
      <div class="chat-settings-fields">
        <label class="chat-field">
          <span class="chat-meta">分析引擎</span>
          <select class="quality-select" value={settings.analysis_model} onChange={(e) => set({ analysis_model: val(e) })}>
            {caps.render.analysis_models.map((m) => (
              <option value={m.id}>{m.label}</option>
            ))}
          </select>
        </label>
        <label class="chat-field">
          <span class="chat-meta">美化引擎</span>
          <select class="quality-select" value={settings.render_option} onChange={(e) => set({ render_option: val(e) })}>
            {caps.render.options.map((o) => (
              <option value={o.id}>{renderOptionLabel(caps, o.id)}</option>
            ))}
          </select>
        </label>
        <label class="chat-field">
          <span class="chat-meta">長寬比</span>
          <select class="quality-select" value={settings.aspect_ratio} onChange={(e) => set({ aspect_ratio: val(e) })}>
            {caps.render.aspect_ratios.map((a) => (
              <option value={a.value}>{a.label}</option>
            ))}
          </select>
        </label>
      </div>
      <div class="chat-settings-portrait">
        <span class="chat-meta">人像修正</span>
        {/* What the switches are for is otherwise only in our heads: they are a
            standing policy set before any photo is analysed, not a reaction to
            one. */}
        <p class="chat-note">這幾項預設會偵測並修正，不想被改到的先關掉。</p>
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
          <span id="chat-prefs-title" class="settings-modal-title">設定</span>
          <button class="error-modal-close" type="button" aria-label="關閉" onClick={onClose}>
            ×
          </button>
        </div>
        {/* 跟面板 #ap-settings-modal 的開關列同一套 markup：全域設定兩邊長得一樣，
            跟這張照片的「美化設定」（選單＋開關群組）一眼就分得出來。 */}
        <div class="settings-modal-body chat-prefs-body">
          <label class="option-row">
            <div class="option-text">
              <span class="option-label">詳細解說</span>
              <span class="option-desc">開啟後，助理會更詳細地說明分析、建議與每一版的成果。</span>
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
        </div>
      </div>
    </div>,
    document.body,
  );
}
