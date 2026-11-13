import { t } from "./i18n";
// The theme palette is global: both UIs write body[data-theme] /
// body[data-theme-mode] and persist to the same localStorage key, so a theme
// picked here is the one the old page opens with, and vice versa. The maps
// below mirror THEME_CYCLE / THEME_LABELS / THEME_SWATCH_CLASS / THEME_MODE /
// THEME_SCROLLBAR_RGB in frontend/app.js -- that file is the source of truth
// for which themes are selectable (ocean/forest/paper/peach still have their
// CSS in styles.css but are deliberately absent from the cycle). Keep the two
// in step, together with the first-paint boot script in index.html.

export const THEME_KEY = "photomaster-theme";

// sky (the default) is light only; every other family ships a dark and a
// light palette, walked in pairs so one click is the small step (same hue,
// other brightness).
export const THEME_CYCLE = [
  "sky",
  "default",
  "purple-light",
  "lilac-dark",
  "lilac",
  "caramel-dark",
  "caramel",
] as const;

export type ThemeKey = (typeof THEME_CYCLE)[number];

// The button's label is the theme's own name -- no two share a glyph, and all
// seven are two glyphs wide (styles.css width-locks .btn-label off that fact).
export const THEME_LABELS: Record<ThemeKey, string> = {
  sky: t("YCO"),
  default: t("極光"),
  "purple-light": t("鳶尾"),
  "lilac-dark": t("夜霞"),
  lilac: t("藕荷"),
  "caramel-dark": t("燼火"),
  caramel: t("暖陽"),
};

export const THEME_SWATCH_CLASS: Record<ThemeKey, string> = {
  sky: "theme-swatch-sky",
  default: "theme-swatch-purple",
  "purple-light": "theme-swatch-purple-light",
  "lilac-dark": "theme-swatch-lilac-dark",
  lilac: "theme-swatch-lilac",
  "caramel-dark": "theme-swatch-caramel-dark",
  caramel: "theme-swatch-caramel",
};

// Every light-only patch in styles.css and chat.css keys off
// body[data-theme-mode], so a theme listed above but missing here would render
// with the dark-tuned black shadows over its light cards.
export const THEME_MODE: Record<ThemeKey, "dark" | "light"> = {
  sky: "light",
  default: "dark",
  "purple-light": "light",
  "lilac-dark": "dark",
  lilac: "light",
  "caramel-dark": "dark",
  caramel: "light",
};

export const THEME_SCROLLBAR_RGB: Record<ThemeKey, string> = {
  sky: "3,173,226",
  default: "147,51,234",
  "purple-light": "124,58,237",
  "lilac-dark": "201,103,162",
  lilac: "136,71,136",
  "caramel-dark": "241,170,66",
  caramel: "185,81,27",
};

// Same fallback as app.js: anything the cycle doesn't name -- a hidden theme a
// browser still has saved, or nothing at all -- lands on sky.
export function readTheme(): ThemeKey {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(THEME_KEY);
  } catch {
    /* storage may be blocked */
  }
  return (THEME_CYCLE as readonly string[]).includes(saved || "") ? (saved as ThemeKey) : "sky";
}

// What the page is showing right now. body[data-theme] is absent for "default"
// (that palette lives on :root), which is why this can't just read the dataset.
export function currentTheme(): ThemeKey {
  const attr = document.body.dataset.theme || "default";
  return (THEME_CYCLE as readonly string[]).includes(attr) ? (attr as ThemeKey) : "sky";
}

export function nextTheme(theme: ThemeKey): ThemeKey {
  const i = THEME_CYCLE.indexOf(theme);
  return THEME_CYCLE[(i + 1) % THEME_CYCLE.length];
}

export function applyTheme(theme: ThemeKey, persist = true) {
  if (theme === "default") delete document.body.dataset.theme;
  else document.body.dataset.theme = theme;
  // Set for dark themes too, rather than only when light: "no attribute" would
  // otherwise mean both "dark theme" and "this never ran".
  document.body.dataset.themeMode = THEME_MODE[theme];
  document.documentElement.style.setProperty("--scrollbar-accent-rgb", THEME_SCROLLBAR_RGB[theme]);
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* storage may be blocked */
    }
  }
}
