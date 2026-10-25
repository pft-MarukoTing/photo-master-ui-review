// The language the page is shown in: Traditional Chinese (the source), or a
// language with a catalog in ./i18n/. The Chinese text is the key: t() returns
// it as it is in Chinese, so the Chinese page never goes through a catalog.
//
// Chosen by ?lang= (remembered), else the remembered choice, else Chinese.
// Changing it reloads the page. The backend gets it with every request
// (X-PhotoAgent-Lang) and writes its replies and analyses in it; what was
// written before stays in the language it was written in.
//
// Adding a language: a catalog in ./i18n/ shaped like en.ts, listed in
// CATALOGS and LANGS, plus the backend's photo_agent/i18n catalog.
//
// Values the page sends back as they are -- the buttons named in Screen, a
// tapped chip's text the backend compares against -- stay Chinese: show them
// with t(), send them without.

import { EN } from "./i18n/en";

export const SOURCE = "zh-TW";
// What the language picker offers, each named in its own language.
export const LANGS: { code: string; name: string }[] = [
  { code: SOURCE, name: "繁體中文" },
  { code: "en", name: "English" },
];
const CATALOGS: Record<string, Record<string, string>> = { en: EN };
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
if (catalog) document.title = document.title.replace("對話", catalog["對話"] || "對話");

/** `text` (Chinese) in the page's language, with {name} filled from `values`.
 *  A key may end in "@@context" when the same Chinese means two things (關閉:
 *  the close button, and a switch's off state); the context is never shown.
 *  A catalog's text may write {name|one|other}: "one" when values[name] is 1. */
export function t(text: string, values?: Record<string, string | number>): string {
  const out = (catalog && catalog[text]) || text.split("@@")[0];
  if (!values) return out;
  return out.replace(/\{(\w+)(?:\|([^|}]*)\|([^}]*))?\}/g, (m, k, one, other) =>
    !(k in values) ? m : one === undefined ? String(values[k]) : Number(values[k]) === 1 ? one : other);
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
