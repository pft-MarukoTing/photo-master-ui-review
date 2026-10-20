import type { Capabilities, Plan } from "./types";
import { renderOptionLabel } from "./util";

export type Settings = Pick<Plan, "analysis_model" | "render_option" | "aspect_ratio" | "advanced_options">;

export function defaultSettings(caps: Capabilities): Settings {
  const d = caps.render.defaults;
  return {
    analysis_model: d.analysis_model,
    render_option: d.render_option,
    aspect_ratio: d.aspect_ratio,
    advanced_options: Object.fromEntries(caps.advanced_options.map((o) => [o.key, o.default])),
  };
}

export function settingsOf(plan: Plan): Settings {
  const { analysis_model, render_option, aspect_ratio, advanced_options } = plan;
  return { analysis_model, render_option, aspect_ratio, advanced_options: { ...(advanced_options || {}) } };
}

export function sameSettings(a: Settings, b: Settings) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function SettingsEditor({
  caps,
  settings,
  onChange,
  lockAnalysis,
}: {
  caps: Capabilities;
  settings: Settings;
  onChange: (s: Settings) => void;
  lockAnalysis?: boolean;
}) {
  const set = (patch: Partial<Settings>) => onChange({ ...settings, ...patch });
  const val = (e: Event) => (e.currentTarget as HTMLSelectElement).value;
  return (
    <div class="chat-card chat-settings-editor">
      <div class="chat-settings-fields">
        <label class="chat-field">
          <span class="chat-meta">分析引擎</span>
          <select
            class="quality-select"
            value={settings.analysis_model}
            disabled={lockAnalysis}
            onChange={(e) => set({ analysis_model: val(e) })}
          >
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
