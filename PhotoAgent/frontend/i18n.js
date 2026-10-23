// The language panel mode is shown in: Traditional Chinese (the source), or a
// language with a catalog (i18n-en.js). The same choice as the chat page
// (frontend-chat/src/i18n.ts): ?lang= (remembered) or the remembered choice.
//
// The Chinese text is the key: tr() returns it as it is in Chinese, so the
// Chinese page never goes through a catalog. Loaded before every other script
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
  const LANGS = [
    { code: SOURCE, name: "繁體中文" },
    { code: "en", name: "English" },
  ];
  const CATALOGS = window.PHOTOAGENT_CATALOGS || {};
  const KEY = "photoagent.lang";

  function normalize(value) {
    const wanted = String(value || "").trim().toLowerCase();
    if (!wanted) return "";
    const hit = LANGS.find((l) => l.code.toLowerCase() === wanted || l.code.toLowerCase().split("-")[0] === wanted.split("-")[0]);
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
  document.documentElement.lang = lang === SOURCE ? "zh-Hant" : lang;

  // `text` (Chinese) in the page's language, with {name} filled from `values`.
  // A key may end in "@@context" when the same Chinese means two things; a
  // catalog's text may write {name|one|other}, "one" when values[name] is 1.
  function tr(text, values) {
    const out = (catalog && catalog[text]) || String(text).split("@@")[0];
    if (!values) return out;
    return out.replace(/\{(\w+)(?:\|([^|}]*)\|([^}]*))?\}/g, (m, k, one, other) =>
      !(k in values) ? m : one === undefined ? String(values[k]) : Number(values[k]) === 1 ? one : other);
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
      if (key && catalog[key]) node.nodeValue = raw.match(/^\s*/)[0] + catalog[key] + raw.match(/\s*$/)[0];
    }
    for (const el of root.querySelectorAll("*")) {
      for (const attr of ATTRS) {
        const value = (el.getAttribute(attr) || "").trim().replace(/\s+/g, " ");
        if (value && catalog[value]) el.setAttribute(attr, catalog[value]);
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
    for (const l of LANGS) select.add(new Option(l.name, l.code, false, l.code === lang));
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

  window.tr = tr;
  window.trLeads = trLeads;
  window.shownRecipe = shownRecipe;
  window.REFRAME_LABEL_RE = REFRAME_LABEL_RE;
  window.PHOTOAGENT_LANG = lang;
})();
