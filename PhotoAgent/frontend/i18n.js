// The language panel mode is shown in: Traditional Chinese (the source), or a
// language with a catalog (i18n-en.js, and generated/l10n.js made by
// scripts/l10n_prepare.py). The same choice as the chat page
// (frontend-chat/src/i18n.ts): ?lang= (remembered) or the remembered choice.
//
// The Chinese text is the key: tr() returns it as it is in Chinese, so the
// Chinese page never goes through a catalog; text a language has not
// translated shows in English, then in Chinese. Loaded before every other script
// of index.html, so they can call tr() while they load. It also:
// - sends the language with every request to the backend (X-PhotoAgent-Lang),
//   which writes its errors and the analyses it starts in that language;
// - translates the static text of index.html once, before app.js runs;
// - adds a language picker to both settings dialogs.
//
// Values the code compares stay Chinese: a reframing recipe item keeps its
// 「重新構圖：」 prefix in the data and is shown with the translated one, so a
// row is found by REFRAME_LABEL_RE whichever it shows.
(function () {
  const SOURCE = "zh-TW";
  const L10N = window.PHOTOAGENT_L10N || { languages: [], catalogs: {} };
  const isChinese = (code) => code.split("-")[0] === "zh";
  const LANGS = [
    { code: SOURCE, name: "繁體中文" },
    ...L10N.languages.filter((l) => isChinese(l.code)),
    { code: "en", name: "English" },
    ...L10N.languages.filter((l) => !isChinese(l.code)),
  ];
  const CATALOGS = { ...(window.PHOTOAGENT_CATALOGS || {}), ...L10N.catalogs };
  const ENGLISH = CATALOGS.en || {};
  const KEY = "photoagent.lang";
  let debugUi = false;
  const isReleaseLanguage = (code) => code === SOURCE || code === "en";

  function populateLanguageSelect(select) {
    const languages = LANGS.filter((l) => debugUi || isReleaseLanguage(l.code));
    select.replaceChildren(...languages.map((l) => new Option(l.name, l.code)));
    select.value = lang;
  }

  function setLanguageDebugUi(isDebug) {
    debugUi = !!isDebug;
    for (const id of ["language-row", "ap-language-row"]) {
      const select = document.querySelector(`#${id} select`);
      if (!select) continue;
      populateLanguageSelect(select);
    }
  }

  // Subtags that make a Chinese tag Traditional; any other Chinese (zh, zh-CN, zh-SG) is Simplified.
  const TRADITIONAL = new Set(["hant", "tw", "hk", "mo"]);

  function normalize(value) {
    const wanted = String(value || "").trim().toLowerCase().replace(/_/g, "-");
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

  function resolve() {
    const asked = normalize(new URLSearchParams(location.search).get("lang"));
    if (asked) {
      try { localStorage.setItem(KEY, asked); } catch { /* the URL still says it */ }
      return asked;
    }
    let kept = "";
    try { kept = localStorage.getItem(KEY) || ""; } catch { /* storage may be blocked */ }
    return normalize(kept) || SOURCE;
  }

  const lang = resolve();
  const catalog = CATALOGS[lang] || null;
  // A language whose fallback is "source" shows what it lacks in Chinese, not English.
  const toEnglish = (LANGS.find((l) => l.code === lang) || {}).fallback !== "source";
  const lookup = (key) => (catalog ? catalog[key] || (toEnglish ? ENGLISH[key] : undefined) : undefined);
  document.documentElement.lang = lang === SOURCE ? "zh-Hant" : lang;
  document.documentElement.dir = (LANGS.find((l) => l.code === lang) || {}).dir || "ltr";
  if (lang === "zh-Hans") {
    const font = document.createElement("link");
    font.rel = "stylesheet";
    font.href = "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap";
    document.head.appendChild(font);
  }

  const PLURAL_RULES = new Intl.PluralRules(lang === SOURCE ? "zh-Hant" : lang);

  // ICU plurals in `text`: {name, plural, =1{…} one{…} other{…}}, the form
  // picked by values[name] (an exact =N first, then the language's category).
  function fillPlurals(text, values) {
    const head = /\{(\w+),\s*plural\s*,/y;
    const form = /\s*(=\d+|zero|one|two|few|many|other)\s*\{/y;
    const end = /\s*\}/y;
    let out = "";
    for (let i = 0; i < text.length;) {
      head.lastIndex = i;
      const m = head.exec(text);
      if (!m || !(m[1] in values)) { out += text[i++]; continue; }
      const forms = {};
      let j = head.lastIndex;
      for (let f; (form.lastIndex = j), (f = form.exec(text));) {
        let k = form.lastIndex;
        for (let depth = 1; k < text.length && depth; k++) depth += text[k] === "{" ? 1 : text[k] === "}" ? -1 : 0;
        forms[f[1]] = text.slice(form.lastIndex, k - 1);
        j = k;
      }
      end.lastIndex = j;
      if (!end.exec(text)) { out += text[i++]; continue; }
      const n = Number(values[m[1]]);
      out += fillPlurals((forms[`=${n}`] ?? forms[PLURAL_RULES.select(n)] ?? forms.other ?? "").replace(/#/g, String(n)), values);
      i = end.lastIndex;
    }
    return out;
  }

  // `text` (Chinese) in the page's language, with {name} filled from `values`.
  // A key may end in "@@context" when the same Chinese means two things; a
  // catalog's text may hold ICU plurals (fillPlurals).
  function tr(text, values) {
    const out = lookup(text) || String(text).split("@@")[0];
    if (!values) return out;
    return fillPlurals(out, values).replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : m));
  }

  // A reframing recipe row's label, in the source or the page's language.
  const prefixes = ["重新構圖", ...Object.values(CATALOGS).map((c) => String(c["重新構圖："] || "").replace(/[:：]\s*$/, "")).filter(Boolean)];
  const REFRAME_LABEL_RE = new RegExp(`^(?:${prefixes.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\s*[:：]`);

  // The start of `template` (before {tail}) as each catalog writes it, its
  // other placeholders matching anything: so text written in any language can
  // have that start taken off again. The source is left to the code's patterns.
  function trLeads(template, tail = "detail") {
    const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return Object.values(CATALOGS).flatMap((c) => {
      const text = c[template];
      if (!text || !text.includes(`{${tail}}`)) return [];
      const lead = text.split(`{${tail}}`)[0];
      return lead.trim() ? [new RegExp("^\\s*" + lead.split(/\{\w+\}/).map(escape).join("(.+?)") + "\\s*", "i")] : [];
    });
  }

  // A recipe item as shown: its 「重新構圖：」 prefix (data the code reads) in
  // the page's language.
  function shownRecipe(text) {
    if (!catalog || typeof text !== "string") return text;
    return text.replace(/^\s*重新構圖\s*[:：]\s*/, `${tr("重新構圖：")} `);
  }

  // Every request to this backend's API carries the language.
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : input && input.url;
    if (url && /\/api\//.test(url) && (!/^https?:/i.test(url) || url.startsWith(location.origin))) {
      const headers = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
      headers.set("X-PhotoAgent-Lang", lang);
      return nativeFetch(input, { ...(init || {}), headers });
    }
    return nativeFetch(input, init);
  };

  // The static text of index.html: text nodes and the attributes people read.
  const ATTRS = ["title", "aria-label", "placeholder", "alt", "data-lock-tip"];
  function translateStatic(root) {
    if (!catalog) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || parent.closest("script, style")) continue;
      // Text that wraps over several lines of index.html is keyed with its
      // whitespace runs as single spaces.
      const raw = node.nodeValue;
      const key = raw.trim().replace(/\s+/g, " ");
      if (key && lookup(key)) node.nodeValue = raw.match(/^\s*/)[0] + lookup(key) + raw.match(/\s*$/)[0];
    }
    for (const el of root.querySelectorAll("*")) {
      for (const attr of ATTRS) {
        const value = (el.getAttribute(attr) || "").trim().replace(/\s+/g, " ");
        if (value && lookup(value)) el.setAttribute(attr, lookup(value));
      }
    }
  }

  function languageRow(id) {
    const row = document.createElement("label");
    row.className = "option-row";
    row.id = id;
    row.innerHTML = `
      <div class="option-text">
        <span class="option-label"></span>
        <span class="option-desc"></span>
      </div>
      <select class="quality-select"></select>`;
    row.querySelector(".option-label").textContent = tr("介面語言");
    row.querySelector(".option-desc").textContent = tr("切換後頁面會重新載入；之前的對話和分析維持原本的語言。");
    const select = row.querySelector("select");
    populateLanguageSelect(select);
    select.addEventListener("change", () => {
      try { localStorage.setItem(KEY, select.value); } catch { /* falls back to the URL */ }
      const url = new URL(location.href);
      url.searchParams.set("lang", select.value);
      location.replace(url.toString());
    });
    return row;
  }

  translateStatic(document.body);
  for (const [body, id] of [["settings-modal-body", "language-row"], ["ap-settings-modal-body", "ap-language-row"]]) {
    const el = document.getElementById(body);
    if (el) el.appendChild(languageRow(id));
  }

  // Languages whose nouns keep their capital letter inside a sentence.
  const KEEPS_CASE = new Set(["de"]);
  // A button name as it reads inside a sentence: lowercased by the language's rules, all-caps words (AI) kept.
  function midSentence(label) {
    if (lang === SOURCE || KEEPS_CASE.has(lang.split("-")[0])) return label;
    return String(label).split(" ").map((w) => (/^\p{Lu}{2,}$/u.test(w) ? w : w.toLocaleLowerCase(lang))).join(" ");
  }

  window.tr = tr;
  window.midSentence = midSentence;
  window.trLeads = trLeads;
  window.shownRecipe = shownRecipe;
  window.REFRAME_LABEL_RE = REFRAME_LABEL_RE;
  window.PHOTOAGENT_LANG = lang;
  window.setLanguageDebugUi = setLanguageDebugUi;
})();
