/* 首頁版本比較：
 *   A = 現行版（範例照片在右側欄）
 *   B = 滿版主視覺，範例照片一排放在下方
 *   C = 置中的「想美化什麼？」大輸入框＋建議，範例照片一排放在下方，兩側斜放幾張照片
 *
 * 只動首頁：所有 B／C 的規則都限定在 #upload-section:has(.home-intro)，對話頁等其他頁面不受影響。
 * 獨立的一支，重排首頁既有的 DOM（.home-intro、.upload-gallery-col）；C 版另外插入自己的主視覺。
 * 不改 frontend-chat 的原始碼，所以拿掉這支檔案就完全回到原狀。
 * 右下角的切換鈕只在首頁出現；選的版本記在 localStorage，也可以用網址 ?ab=a / ?ab=b / ?ab=c 指定。
 * 掛法：frontend-chat/index.html 加 <script src="../ab-home.js" defer></script>
 */
(function () {
  var KEY = "photomaster-home-ab";
  var VARIANTS = ["a", "b", "c"];
  var PROMPT_KEY = "photomaster-home-c-prompt";

  function read() {
    var q = new URLSearchParams(location.search).get("ab");
    if (VARIANTS.indexOf(q) >= 0) return q;
    try { var v = localStorage.getItem(KEY); return VARIANTS.indexOf(v) >= 0 ? v : "a"; } catch (e) { return "a"; }
  }
  function setClass(v) {
    document.documentElement.classList.toggle("ab-home-b", v === "b");
    document.documentElement.classList.toggle("ab-home-c", v === "c");
  }
  function apply(v) {
    setClass(v);
    try { localStorage.setItem(KEY, v); } catch (e) {}
    var btns = document.querySelectorAll(".ab-switch-opt");
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute("data-v") === v;
      btns[i].classList.toggle("is-on", on);
      btns[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  var css = [
    /* ── 切換鈕 ── */
    ".ab-switch{position:fixed;left:50%;transform:translateX(-50%);top:calc((var(--yco-header-h,64px) - 40px) / 2);z-index:2147483000;display:none;white-space:nowrap;align-items:center;gap:4px;padding:4px;",
    "  border-radius:999px;background:rgba(17,24,26,.88);box-shadow:0 8px 24px rgba(0,0,0,.25);backdrop-filter:blur(6px);",
    "  font:600 13px/1 Roboto,'Noto Sans TC',system-ui,sans-serif;color:#fff}",
    "@media (max-width:900px){.ab-switch{top:8px}}",
    "html.ab-on-home .ab-switch{display:flex}",
    ".ab-switch-opt{min-width:44px;height:32px;padding:0 12px;border:0;border-radius:999px;background:transparent;color:inherit;font:inherit;cursor:pointer}",
    ".ab-switch-opt:hover{background:rgba(255,255,255,.12)}",
    ".ab-switch-opt.is-on{background:#fff;color:#11181a}",
    ".ab-switch-opt:focus-visible{outline:2px solid #4fc3f7;outline-offset:2px}",

    /* ── B 版：只在桌機版面（側欄是右邊固定欄的那個寬度）重排 ──
       一個畫面剛好放下：上面滿版主視覺（before/after 當背景，文字壓在左邊），
       下面靠左的「最近／範例」分頁加一排橫向捲動的照片。 */
    "@media (min-width:1201px),(min-width:901px) and (max-width:1200px) and (orientation:landscape){",
    "  html.ab-home-b{--abg-gutter:clamp(24px,4vw,64px);--abg-tile-h:clamp(110px,19vh,190px)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-container{padding:0 !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-grid{height:100%}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-content-row{flex-direction:column;align-items:stretch;justify-content:flex-start;",
    "    height:100%;padding:0 !important;gap:0}",
    /* 主視覺：佔掉照片列以外的高度 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-intro{flex:1 1 auto;min-height:0;width:100%;padding:0 !important;margin:0}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero{position:relative;display:block;width:100%;height:100%;max-width:none;margin:0;padding:0;",
    "    background:var(--surface-default,#fff)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare{position:absolute !important;top:0;left:30%;margin:0 !important;width:70% !important;height:100% !important;",
    "    max-width:none;aspect-ratio:auto;border:0 !important;outline:0 !important;border-radius:0 !important;box-shadow:none !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-img{object-position:50% 28%}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag{top:24px}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag:not(.is-after){left:auto;right:calc(100% - var(--pos,50%) + 16px)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag.is-after{left:calc(var(--pos,50%) + 16px);right:auto}",
    /* 文字壓在左邊，底下墊一層往右淡掉的白，字才讀得清楚 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero-copy{position:relative;z-index:3;display:flex;flex-direction:column;justify-content:center;",
    "    width:min(600px,44%);height:100%;padding:0 0 0 var(--abg-gutter);pointer-events:none}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero-copy>*{pointer-events:auto}",
    /* 照片左緣淡進左邊的底色，文字和照片接在一起 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero::before{content:'';position:absolute;top:0;bottom:0;left:calc(30% - 4px);z-index:2;width:22%;pointer-events:none;",
    "    background:linear-gradient(90deg,var(--surface-default,#fff) 0%,rgba(255,255,255,0) 100%)}",
    "  html[data-theme=dark].ab-home-b #upload-section:has(.home-intro) .home-hero::before{background:linear-gradient(90deg,var(--surface-default,#12181a) 0%,rgba(18,24,26,0) 100%)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-headline{font-size:clamp(32px,3.6vw,56px)}",
    /* 照片列：從右邊固定欄變成主視覺下方的一條 */
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col{position:relative !important;inset:auto !important;flex:0 0 auto;width:100% !important;height:auto !important;",
    "    max-width:none;margin:0;padding:8px var(--abg-gutter) 20px !important;border:0 !important;background:var(--surface-default,#fff) !important;box-shadow:none !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-heading{width:100%;justify-content:flex-start;margin:0 0 16px;border-bottom:1px solid var(--stroke-weak,rgba(17,24,26,.1))}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-tabs{width:auto;flex:0 0 auto}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-tab-recent-group,html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-tab{flex:0 0 auto;min-width:0;padding-inline:20px}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-scroll-frame{height:auto !important;max-height:none !important;padding:0 !important;overflow:visible !important}",
    /* 一排、橫向捲動；卡片 3:2，高度跟著視窗高度走 */
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .demo-images-grid{display:flex !important;flex-wrap:nowrap;gap:16px !important;",
    "    height:auto !important;max-height:none !important;overflow-x:auto !important;overflow-y:hidden !important;padding:0 0 6px !important;scroll-snap-type:x proximity}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .demo-images-grid>.demo-image-tile{flex:0 0 auto !important;",
    "    width:calc(var(--abg-tile-h) * 1.5) !important;height:var(--abg-tile-h) !important;aspect-ratio:auto !important;scroll-snap-align:start}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .history-day-group{flex:0 0 auto;display:flex;flex-direction:row;align-items:stretch;gap:16px;width:auto}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .history-day-heading{display:none}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .history-grid{display:flex !important;gap:16px !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .history-grid>*{flex:0 0 auto;width:calc(var(--abg-tile-h) * 1.5) !important;height:var(--abg-tile-h) !important;aspect-ratio:auto !important}",
    /* 「使用所選照片」：放在分頁那一行的右邊 */
    "  html.ab-home-b #upload-section:has(.home-intro) .gallery-confirm-btn{position:absolute !important;top:8px;right:var(--abg-gutter);z-index:5;width:auto !important;",
    "    min-width:180px;margin:0 !important}",
    "}",

    /* ── C 版：置中大輸入框 ── */
    ".abc-hero{display:none}",
    "html.ab-home-c #upload-section:has(.home-intro) .home-hero{display:none !important}",
    "html.ab-home-c #upload-section:has(.home-intro) .abc-hero{position:relative;display:flex;flex-direction:column;align-items:center;width:100%;max-width:1100px;margin:0 auto;padding:0 16px;text-align:center}",
    /* 標題 Wix Madefor Display 粗體；副標和提示字 Wix Madefor Text 細體（中文落到 Noto Sans） */
    ".abc-title{margin:0;font:800 clamp(30px,3.4vw,52px)/1.2 'Wix Madefor Display',var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;letter-spacing:-.01em;color:var(--text-strong,#11181a)}",
    ".abc-sub{margin:12px 0 0;font:400 clamp(15px,1.3vw,19px)/1.5 'Wix Madefor Text',var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;color:var(--text-weak,#4b5a5e)}",
    ".abc-box{position:relative;z-index:2;display:flex;flex-direction:column;width:min(760px,100%);margin:clamp(20px,3.5vh,40px) 0 0;padding:20px 20px 16px 24px;",
    "  border:1px solid var(--stroke-weak,rgba(17,24,26,.12));border-radius:24px;background:var(--surface-default,#fff);box-shadow:0 10px 30px rgba(9,22,26,.08);text-align:start}",
    ".abc-box:focus-within{border-color:var(--stroke-brand-strong,#1a8fd6);box-shadow:0 0 0 3px var(--stroke-focus,rgba(26,143,214,.25)),0 10px 30px rgba(9,22,26,.08)}",
    ".abc-input{width:100%;min-height:56px;resize:none;border:0;outline:0;background:none;padding:0;font:400 18px/1.5 'Wix Madefor Text',var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;color:var(--text-strong,#11181a)}",
    ".abc-input::placeholder{color:var(--text-weak,#8a979a);font-weight:400}",
    /* 下面點選的照片直接顯示在輸入框裡（新增照片鈕旁邊），× 取消選取 */
    ".abc-attach{display:flex;align-items:center;gap:10px;flex:1 1 auto;min-width:0;overflow-x:auto;padding:2px 4px}",
    ".abc-left{display:flex;align-items:center;gap:10px;flex:1 1 auto;min-width:0}",
    ".abc-thumb{position:relative;flex:none;width:48px;height:48px;border-radius:12px;overflow:visible}",
    ".abc-thumb img{display:block;width:100%;height:100%;object-fit:cover;border-radius:12px;box-shadow:0 0 0 1px rgba(17,24,26,.08)}",
    ".abc-thumb-x{position:absolute;top:-6px;right:-6px;display:flex;align-items:center;justify-content:center;width:20px;height:20px;padding:0;border:0;border-radius:50%;",
    "  background:#11181a;color:#fff;font:600 12px/1 system-ui,sans-serif;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.3)}",
    ".abc-box-row{display:flex;align-items:center;justify-content:space-between;margin-top:12px}",
    ".abc-add{display:flex;align-items:center;justify-content:center;width:48px;height:48px;border:0;border-radius:14px;background:var(--fill-weak,#f1f4f5);color:var(--icon-strong,#11181a);cursor:pointer}",
    ".abc-add:hover{background:var(--fill-default,#e6ebec)}",
    ".abc-send{display:flex;align-items:center;justify-content:center;width:52px;height:52px;border:0;border-radius:50%;background:var(--fill-brand-strong,#1673c9);color:#fff;cursor:pointer;box-shadow:0 6px 14px rgba(22,115,201,.3)}",
    ".abc-send:hover{filter:brightness(1.08)}",
    ".abc-add:focus-visible,.abc-send:focus-visible,.abc-chip:focus-visible{outline:2px solid var(--stroke-brand-strong,#1a8fd6);outline-offset:2px}",
    ".abc-chips{position:relative;z-index:2;display:flex;flex-wrap:wrap;justify-content:center;gap:12px;margin-top:clamp(14px,2.4vh,24px)}",
    ".abc-chip{height:44px;padding:0 22px;border:0;border-radius:999px;background:var(--fill-weak,#eef1f3);color:var(--text-strong,#11181a);",
    "  font:500 15px/1 Roboto,var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;cursor:pointer}",
    ".abc-chip:hover{background:var(--fill-default,#e2e7e9)}",
    /* 兩側斜放的照片（拍立得白框） */
    ".abc-deco{position:fixed;z-index:1;width:clamp(120px,11vw,200px);aspect-ratio:4/5;object-fit:cover;padding:7px;background:#fff;border-radius:6px;",
    "  box-shadow:0 12px 30px rgba(9,22,26,.18);pointer-events:none}",
    ".abc-deco.is-l1{left:2vw;top:16vh;transform:rotate(-7deg)}",
    ".abc-deco.is-l2{left:7vw;top:36vh;transform:rotate(4deg)}",
    ".abc-deco.is-r1{right:3vw;top:26vh;transform:rotate(6deg)}",
    "@media (max-width:1100px){.abc-deco{display:none}}",
    /* C 版桌機：一個畫面放下，照片列在輸入框下方置中、一排 */
    "@media (min-width:1201px),(min-width:901px) and (max-width:1200px) and (orientation:landscape){",
    "  html.ab-home-c{--abc-tile-h:clamp(100px,19vh,200px)}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-container{padding:0 !important}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-grid{height:100%}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-content-row{flex-direction:column;align-items:stretch;justify-content:center;height:100%;padding:0 !important;gap:clamp(20px,4vh,44px)}",
    "  html.ab-home-c #upload-section:has(.home-intro) .home-intro{flex:0 0 auto;min-height:0;width:100%;padding:0 !important;margin:0}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col{position:relative !important;inset:auto !important;flex:0 0 auto;width:min(1240px,100%) !important;height:auto !important;",
    "    margin:0 auto;padding:0 24px !important;border:0 !important;background:transparent !important;box-shadow:none !important;z-index:2}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-heading{width:100%;justify-content:center;margin:0 0 16px;border:0}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-tabs{width:auto;flex:0 0 auto}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-tab-recent-group,html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-tab{flex:0 0 auto;min-width:0;padding-inline:24px}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-scroll-frame{height:auto !important;max-height:none !important;padding:0 !important;overflow:visible !important}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .demo-images-grid{display:flex !important;flex-wrap:nowrap;justify-content:flex-start;gap:20px !important;",
    "    width:min(100%,calc(var(--abc-tile-h) * 1.4 * 4 + 60px)) !important;margin:0 auto !important;",
    "    height:auto !important;max-height:none !important;overflow-x:auto !important;overflow-y:hidden !important;padding:0 0 6px !important;scroll-snap-type:x proximity}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .demo-images-grid:not(:has(.history-day-group)){",
    "    width:min(100%,calc(var(--abc-tile-h) * 1.4 * 4 + 60px)) !important;margin:0 auto !important;display:flex !important;flex:0 1 auto !important}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .demo-images-grid>.demo-image-tile{flex:0 0 auto !important;",
    "    width:calc(var(--abc-tile-h) * 1.4) !important;height:var(--abc-tile-h) !important;aspect-ratio:auto !important;border-radius:16px !important;scroll-snap-align:start}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .history-day-group{flex:0 0 auto;display:flex;flex-direction:row;align-items:stretch;gap:20px;width:auto}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .history-day-heading{display:none}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .history-grid{display:flex !important;gap:20px !important}",
    "  html.ab-home-c #upload-section:has(.home-intro) .upload-gallery-col .history-grid>*{flex:0 0 auto;width:calc(var(--abc-tile-h) * 1.4) !important;height:var(--abc-tile-h) !important;aspect-ratio:auto !important}",
    /* 點選範例照片後按輸入框的送出就會用那張開始，不另外放「使用所選照片」 */
    "  html.ab-home-c #upload-section:has(.home-intro) .gallery-confirm-btn{display:none !important}",
    "}"
  ].join("\n");

  // ── C 版的主視覺 ──────────────────────────────────────────────
  // 現在的介面語言：切語言時頁面會帶 ?lang= 重新載入，<html lang> 要等主程式跑起來才改，
  // 所以每次都重新判斷，不在載入時記死。
  function isEn() {
    var q = new URLSearchParams(location.search).get("lang");
    if (q) return /^en/i.test(q);
    try { var v = localStorage.getItem("photoagent.lang"); if (v) return /^en/i.test(v); } catch (e) {}
    return /^en/i.test(document.documentElement.lang || "");
  }
  var TXTS = { en: {
    title: "What would you like to retouch?",
    sub: "Add a photo and tell us what you'd like to change.",
    placeholder: "Describe an edit…",
    add: "Add a photo", send: "Start",
    chips: ["Fix the lighting", "Improve the framing", "Soften shadows"]
  }, zh: {
    title: "想美化什麼？",
    sub: "放一張照片，告訴我想怎麼修。",
    placeholder: "描述想怎麼修…",
    add: "新增照片", send: "開始",
    chips: ["修正光線", "調整構圖", "柔化陰影"]
  } };
  // 兩側斜放的照片：用範例照片的縮圖；檔名對不上就自己藏起來。
  var DECO = [
    { name: "27-cafe-window-auburn-portrait.jpg", cls: "is-l1" },
    { name: "11-outdoor-male-portrait.jpg", cls: "is-l2" },
    { name: "20-orange-tabby.jpg", cls: "is-r1" }
  ];

  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function buildC() {
    var lang = isEn() ? "en" : "zh";
    var TXT = TXTS[lang];
    var root = document.createElement("div");
    root.className = "abc-hero";
    root.setAttribute("data-lang", lang);
    var deco = DECO.map(function (d) {
      return '<img class="abc-deco ' + d.cls + '" alt="" aria-hidden="true" src="/api/photo-agent/demo-images/' + encodeURIComponent(d.name) + '/thumbnail" onerror="this.remove()">';
    }).join("");
    root.innerHTML = deco +
      '<h1 class="abc-title">' + esc(TXT.title) + '</h1>' +
      '<p class="abc-sub">' + esc(TXT.sub) + '</p>' +
      '<form class="abc-box">' +
      '  <textarea class="abc-input" rows="2" placeholder="' + esc(TXT.placeholder) + '" aria-label="' + esc(TXT.placeholder) + '"></textarea>' +
      '  <div class="abc-box-row">' +
      '   <div class="abc-left">' +
      '    <button type="button" class="abc-add" title="' + esc(TXT.add) + '" aria-label="' + esc(TXT.add) + '">' +
      '      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5"/></svg>' +
      '    </button>' +
      '    <div class="abc-attach" aria-live="polite"></div>' +
      '   </div>' +
      '    <button type="submit" class="abc-send" title="' + esc(TXT.send) + '" aria-label="' + esc(TXT.send) + '">' +
      '      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>' +
      '    </button>' +
      '  </div>' +
      '</form>' +
      '<div class="abc-chips">' + TXT.chips.map(function (c) { return '<button type="button" class="abc-chip">' + esc(c) + '</button>'; }).join("") + '</div>';

    var input = root.querySelector(".abc-input");
    var remember = function () {
      var t = input.value.trim();
      try { t ? sessionStorage.setItem(PROMPT_KEY, t) : sessionStorage.removeItem(PROMPT_KEY); } catch (e) {}
    };
    root.querySelectorAll(".abc-chip").forEach(function (b) {
      b.addEventListener("click", function () {
        var t = b.textContent;
        input.value = input.value.trim() ? input.value.trim() + "，" + t : t;
        input.focus();
      });
    });
    // 新增照片：打開頁面原本的選檔視窗（選好了頁面自己會進對話）。
    root.querySelector(".abc-add").addEventListener("click", function () {
      remember();
      var f = document.querySelector('input[type=file][accept*="image"]');
      if (f) f.click();
    });
    // 送出：照首頁原本的「開始對話」走（有點選範例照片就用那張），打的字帶到對話框裡。
    root.querySelector(".abc-box").addEventListener("submit", function (e) {
      e.preventDefault();
      remember();
      var start = document.querySelector(".home-start-btn");
      if (start) start.click();
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        root.querySelector(".abc-box").requestSubmit();
      }
    });
    return root;
  }

  // 照片列裡選取中的照片，畫成輸入框裡的小縮圖。用 src 串成的 key 比對，有變才重畫（避免觸發自己的 observer）。
  function syncAttach() {
    var box = document.querySelector(".abc-hero .abc-attach");
    if (!box) return;
    var picked = Array.prototype.slice.call(document.querySelectorAll(".upload-gallery-col .is-selected"));
    var items = picked.map(function (tile) {
      var img = tile.querySelector("img:not(.demo-image-tile-bg)") || tile.querySelector("img");
      return { tile: tile, src: img ? img.getAttribute("src") : "" };
    }).filter(function (x) { return x.src; });
    var key = items.map(function (x) { return x.src; }).join("|");
    if (box.getAttribute("data-key") === key) return;
    box.setAttribute("data-key", key);
    box.innerHTML = "";
    items.forEach(function (x, i) {
      var t = document.createElement("span");
      t.className = "abc-thumb";
      t.innerHTML = '<img alt="" src="' + esc(x.src) + '"><button type="button" class="abc-thumb-x" aria-label="remove">×</button>';
      t.querySelector(".abc-thumb-x").addEventListener("click", function () {
        var now = document.querySelectorAll(".upload-gallery-col .is-selected")[i];
        (now || x.tile).click();
      });
      box.appendChild(t);
    });
  }

  // 進到對話頁後，把 C 版輸入框打的字填進對話框（只填不送，讓人看過再按）。
  function fillPrompt() {
    var t;
    try { t = sessionStorage.getItem(PROMPT_KEY); } catch (e) { return; }
    if (!t || document.querySelector(".home-intro")) return;
    var field = document.querySelector(".chat-composer input:not([type=file]), .chat-composer textarea");
    if (!field) return;
    try { sessionStorage.removeItem(PROMPT_KEY); } catch (e) {}
    var proto = field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(field, t);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.focus();
  }

  function mount() {
    if (document.querySelector(".ab-switch")) return;
    var style = document.createElement("style");
    style.id = "ab-home-style";
    style.textContent = css;
    document.head.appendChild(style);
    var font = document.createElement("link");
    font.rel = "stylesheet";
    font.href = "https://fonts.googleapis.com/css2?family=Wix+Madefor+Display:wght@700;800&family=Wix+Madefor+Text:wght@400;500&display=swap";
    document.head.appendChild(font);

    var box = document.createElement("div");
    box.className = "ab-switch";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "首頁版本");
    box.innerHTML =
      '<button type="button" class="ab-switch-opt" data-v="a" title="A：範例照片在右側">A</button>' +
      '<button type="button" class="ab-switch-opt" data-v="b" title="B：滿版主視覺，範例照片在下方">B</button>' +
      '<button type="button" class="ab-switch-opt" data-v="c" title="C：置中輸入框，範例照片在下方">C</button>';
    box.addEventListener("click", function (e) {
      var b = e.target.closest(".ab-switch-opt");
      if (!b) return;
      apply(b.getAttribute("data-v"));
      window.scrollTo({ top: 0 });
    });
    document.body.appendChild(box);
    apply(read());

    // 只在首頁顯示切換鈕：首頁才有 .home-intro。頁面是 SPA，換頁時跟著更新。
    var sync = function () {
      var intro = document.querySelector(".home-intro");
      document.documentElement.classList.toggle("ab-on-home", !!intro);
      if (intro) {
        var hero = intro.querySelector(".abc-hero");
        // 語言跟現在的不同（主程式剛把介面切成英文）就整塊換掉，打到一半的字留著。
        if (hero && hero.getAttribute("data-lang") !== (isEn() ? "en" : "zh")) {
          var typed = hero.querySelector(".abc-input").value;
          hero.remove();
          hero = null;
          var fresh = buildC();
          fresh.querySelector(".abc-input").value = typed;
          intro.insertBefore(fresh, intro.firstChild);
        }
        if (!intro.querySelector(".abc-hero")) intro.insertBefore(buildC(), intro.firstChild);
      }
      fillPrompt();
      syncAttach();
    };
    sync();
    new MutationObserver(sync).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  }

  // 先套上 class，避免首屏先畫 A 再跳到別版。
  setClass(read());
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
