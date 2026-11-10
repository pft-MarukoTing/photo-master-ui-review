// The language the page is shown in: Traditional Chinese (the source), or a
// language with a catalog in ./i18n/ or virtual:l10n (scripts/l10n_prepare.py).
// The Chinese text is the key: t() returns it as it is in Chinese, so the
// Chinese page never goes through a catalog; text a language has not
// translated shows in English, then in Chinese.
//
// Chosen by ?lang= (remembered), else the remembered choice, else Chinese.
// Changing it reloads the page. The backend gets it with every request
// (X-PhotoAgent-Lang) and writes its replies and analyses in it; what was
// written before stays in the language it was written in.
//
// Adding a language: enable it in i18n/locales.json (see scripts/l10n_prepare.py).
//
// Values the page sends back as they are -- the buttons named in Screen, a
// tapped chip's text the backend compares against -- stay Chinese: show them
// with t(), send them without.

import { EN } from "./i18n/en";
// Empty until scripts/l10n_prepare.py runs: then Chinese and English only.
import { catalogs as MADE_CATALOGS, languages as MADE_LANGUAGES } from "virtual:l10n";

export const SOURCE = "zh-TW";
// What the language picker offers, each named in its own language.
const isChinese = (code: string) => code.split("-")[0] === "zh";
export const LANGS: { code: string; name: string; fallback?: string; dir?: string }[] = [
  { code: SOURCE, name: "繁體中文" },
  ...MADE_LANGUAGES.filter((l) => isChinese(l.code)),
  { code: "en", name: "English" },
  ...MADE_LANGUAGES.filter((l) => !isChinese(l.code)),
];
const KEY = "photoagent.lang";

// Subtags that make a Chinese tag Traditional; any other Chinese (zh, zh-CN, zh-SG) is Simplified.
const TRADITIONAL = new Set(["hant", "tw", "hk", "mo"]);

function normalize(value: string | null | undefined): string {
  const wanted = (value || "").trim().toLowerCase().replace(/_/g, "-");
  if (!wanted) return "";
  const parts = wanted.split("-");
  const exact = LANGS.find((l) => l.code.toLowerCase() === wanted);
  if (exact) return exact.code;
  if (parts[0] === "zh") {
    const simplified = LANGS.some((l) => l.code === "zh-Hans");
    return simplified && !parts.some((p) => TRADITIONAL.has(p)) ? "zh-Hans" : SOURCE;
  }
  const hit = LANGS.find((l) => l.code.toLowerCase().split("-")[0] === parts[0]);
  return hit ? hit.code : SOURCE;
}

function remembered(): string {
  try {
    return localStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
}

function resolve(): string {
  const asked = normalize(new URLSearchParams(location.search).get("lang"));
  if (asked) {
    try {
      localStorage.setItem(KEY, asked);
    } catch {
      /* private mode: the URL still says it */
    }
    return asked;
  }
  return normalize(remembered()) || SOURCE;
}

export const lang = resolve();
document.documentElement.lang = lang === SOURCE ? "zh-Hant" : lang;
document.documentElement.dir = LANGS.find((l) => l.code === lang)?.dir || "ltr";
// A right-to-left page mirrors the layout, so text naming a side of the screen names the other one.
export const rtl = document.documentElement.dir === "rtl";
// Only the chosen language's catalog is downloaded; every module importing t() waits for it.
async function loadCatalog(): Promise<Record<string, string> | undefined> {
  if (lang === "en") return EN;
  if (!MADE_CATALOGS[lang]) return undefined;
  try {
    return (await MADE_CATALOGS[lang]()).default;
  } catch {
    // Download failed: the page shows the fallback language instead.
    return {};
  }
}
const catalog = await loadCatalog();
// A language whose fallback is "source" shows what it lacks in Chinese, not English.
const english: Record<string, string> = LANGS.find((l) => l.code === lang)?.fallback === "source" ? {} : EN;
if (catalog) document.title = document.title.replace("對話", catalog["對話"] || english["對話"] || "對話");
if (lang === "zh-Hans") {
  const font = document.createElement("link");
  font.rel = "stylesheet";
  font.href = "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@500;600&display=swap";
  document.head.appendChild(font);
}

const pluralRules = new Intl.PluralRules(lang === SOURCE ? "zh-Hant" : lang);

/** ICU plurals in `text`: {name, plural, =1{…} one{…} other{…}}, the form
 *  picked by values[name] (an exact =N first, then the language's category). */
function fillPlurals(text: string, values: Record<string, string | number>): string {
  const head = /\{(\w+),\s*plural\s*,/y;
  const form = /\s*(=\d+|zero|one|two|few|many|other)\s*\{/y;
  const end = /\s*\}/y;
  let out = "";
  for (let i = 0; i < text.length; ) {
    head.lastIndex = i;
    const m = head.exec(text);
    if (!m || !(m[1] in values)) {
      out += text[i++];
      continue;
    }
    const forms: Record<string, string> = {};
    let j = head.lastIndex;
    for (let f; (form.lastIndex = j), (f = form.exec(text)); ) {
      let k = form.lastIndex;
      for (let depth = 1; k < text.length && depth; k++) depth += text[k] === "{" ? 1 : text[k] === "}" ? -1 : 0;
      forms[f[1]] = text.slice(form.lastIndex, k - 1);
      j = k;
    }
    end.lastIndex = j;
    if (!end.exec(text)) {
      out += text[i++];
      continue;
    }
    const n = Number(values[m[1]]);
    out += fillPlurals((forms[`=${n}`] ?? forms[pluralRules.select(n)] ?? forms.other ?? "").replace(/#/g, String(n)), values);
    i = end.lastIndex;
  }
  return out;
}

/** `text` (Chinese) in the page's language, with {name} filled from `values`.
 *  A key may end in "@@context" when the same Chinese means two things (關閉:
 *  the close button, and a switch's off state); the context is never shown.
 *  A catalog's text may hold ICU plurals (fillPlurals). */
export function t(text: string, values?: Record<string, string | number>): string {
  const out = (catalog && (catalog[text] || english[text])) || text.split("@@")[0];
  if (!values) return out;
  return fillPlurals(out, values).replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : m));
}

/** A button name the page keeps in Chinese (it is matched and sent as data)
 *  as the user reads it, counted ones included: 「分析其餘 3」「再美化 2 張」. */
export function shownLabel(label: string): string {
  if (!catalog) return label;
  const m = /^(分析其餘|一鍵美化其餘) (\d+)$/.exec(label) || /^(再美化) (\d+) 張$/.exec(label);
  if (m) return t(m[1] === "再美化" ? "再美化 {n} 張" : `${m[1]} {n}`, { n: m[2] });
  return t(label);
}

/** A recipe item as shown: its 「重新構圖：」 prefix (data the code reads) in the page's language. */
export function shownRecipe(text: string): string {
  return catalog ? text.replace(/^\s*重新構圖\s*[:：]\s*/, `${t("重新構圖：")} `) : text;
}

/** Items read as one list: 「甲、乙、丙」 in Chinese, "a, b and c" in English. */
export function listOf(items: string[]): string {
  if (!catalog || items.length < 2) return items.join(t("、"));
  return t("{v1}和{v2}", { v1: items.slice(0, -1).join(t("、")), v2: items[items.length - 1] });
}

// Languages whose nouns keep their capital letter inside a sentence.
const KEEPS_CASE = new Set(["de"]);

/** A button name as it reads inside a sentence: lowercased by the language's rules, all-caps words (AI) kept. */
export function midSentence(label: string): string {
  if (lang === SOURCE || KEEPS_CASE.has(lang.split("-")[0])) return label;
  return label.split(" ").map((w) => (/^\p{Lu}{2,}$/u.test(w) ? w : w.toLocaleLowerCase(lang))).join(" ");
}

/** Switches the page's language: remembered, then the page reloads in it. */
export function setLang(code: string) {
  try {
    localStorage.setItem(KEY, code);
  } catch {
    /* falls back to the URL below */
  }
  const url = new URL(location.href);
  url.searchParams.set("lang", code);
  location.replace(url.toString());
}
