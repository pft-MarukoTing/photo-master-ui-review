// The language the page is shown in: Traditional Chinese (the source), or a
// language with a catalog in ./i18n/ or ./generated/ (scripts/l10n_prepare.py).
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

type Made = { languages: { code: string; name: string }[]; catalogs: Record<string, Record<string, string>> };
// Absent until scripts/l10n_prepare.py runs: then Chinese and English only.
const MADE: Made = Object.values(import.meta.glob<Made>("./generated/l10n.json", { eager: true, import: "default" }))[0]
  || { languages: [], catalogs: {} };

export const SOURCE = "zh-TW";
// What the language picker offers, each named in its own language.
export const LANGS: { code: string; name: string }[] = [
  { code: SOURCE, name: "繁體中文" },
  { code: "en", name: "English" },
  ...MADE.languages,
];
const CATALOGS: Record<string, Record<string, string>> = { en: EN, ...MADE.catalogs };
const KEY = "photoagent.lang";

function normalize(value: string | null | undefined): string {
  const wanted = (value || "").trim().toLowerCase();
  if (!wanted) return "";
  const hit = LANGS.find((l) => l.code.toLowerCase() === wanted || l.code.toLowerCase().split("-")[0] === wanted.split("-")[0]);
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
const catalog = CATALOGS[lang];
if (catalog) document.title = document.title.replace("對話", catalog["對話"] || EN["對話"] || "對話");

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
  const out = (catalog && (catalog[text] || EN[text])) || text.split("@@")[0];
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
