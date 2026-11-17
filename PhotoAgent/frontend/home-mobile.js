/* 手機版首頁：只留一顆「開始對話」，放在照片列正下方，選了幾張直接寫在後面（「開始對話（3 張）」）。
 *
 * 原本手機首頁有兩顆：照片列下的「使用所選照片（N 張）」和固定在底部的「開始對話」。
 * 這支把兩顆原本的藏起來，在「使用所選照片」的位置放一顆新的，點了轉給原本的按鈕：
 *   有選照片 → 按原本的「使用所選照片」（多張的流程照舊）
 *   沒選照片 → 按原本的「開始對話」
 * 不搬動頁面自己的按鈕（那些是 Preact 管的），只藏起來再代按，拿掉這支就回到原狀。
 * 只在手機版面、只在首頁生效。
 * 掛法：frontend-chat/index.html 加 <script src="../home-mobile.js" defer></script>
 * 之後要正式做進 frontend-chat（home.tsx）時，照這裡的行為改，然後拿掉這支。
 */
(function () {
  // 跟 chat.css 裡手機／直拿平板那段 @media 同一條（側欄變成橫向一排的寬度）。
  // 只有手機（≤600）換成這顆在照片列上方的「開始對話」；平板（601 起）照桌機的做法，用頁面原本放在文案正下方的那顆。
  var MOBILE = "(max-width:600px)";
  var HOME = "#upload-section:has(.home-intro)";

  var css = [
    ".hm-start{display:none}",
    "@media " + MOBILE + "{",
    "  " + HOME + " .gallery-confirm-btn{display:none !important}",
    "  " + HOME + " .home-start{display:none !important}",
    "  " + HOME + " .hm-start{display:flex;flex-direction:column;align-items:stretch;gap:8px;width:100%;margin:16px 0 8px}",
    /* 手機首頁是 display:contents 加 order 排的：放在前後對照圖（order 3）正下方、「最近／範例」分頁（order 4）上面，
       不用捲到照片列下面才看得到。同樣是 order 3，DOM 上它在後面，所以排在對照圖後面。 */
    "  " + HOME + " .upload-gallery-col>.hm-start{order:3}",
    "  .hm-start-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;height:52px;padding:0 20px;border:0;border-radius:999px;",
    "    background:var(--mv-grad,linear-gradient(90deg,#16a6d9,#4cd07d));color:#fff;font:700 17px/1 Roboto,var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;",
    "    box-shadow:0 8px 20px rgba(22,166,217,.28);cursor:pointer}",
    "  .hm-start-btn:active{transform:translateY(1px)}",
    "  .hm-start-btn:focus-visible{outline:2px solid var(--stroke-brand-strong,#1a8fd6);outline-offset:3px}",
    "  .hm-start-btn svg{flex:none}",
    /* 張數：跟桌機（home-pick-count.js 的 .abg-count）一樣，一顆圓形徽章放在字後面；底色用 fill-overlay-weak（比 fill-overlay 淺）。 */
    "  .hm-start-count{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:22px;padding:0 6px;box-sizing:border-box;border-radius:999px;",
    "    background:var(--fill-overlay-weak,rgba(0,0,0,.45));color:#fff;font:700 13px/1 Roboto,var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif}",
    "  .hm-start-hint{margin:0;text-align:center;font:400 13px/1.4 Roboto,var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;color:var(--text-weak,#5b6a6e)}",
    "  .hm-start-btn:disabled{background:var(--fill-disabled,#d5dbdd);color:var(--text-disabled,#fff);box-shadow:none;cursor:default}",
    /* 首頁的「全部」範例照片面板：底部同樣只留一顆「開始對話（N 張）」。
       只在首頁開的時候換（對話頁也會開這個面板，那裡是加照片，不動）。 */
    "  :root:has(.home-intro) .sample-modal-actions{display:none !important}",
    "  :root:has(.home-intro) .sample-modal-foot{flex-direction:column-reverse;align-items:stretch;gap:6px}",
    "  :root:has(.home-intro) .sample-modal-foot.hm-has-count .sample-modal-count{display:none}",
    "  :root:has(.home-intro) .sample-modal-count{text-align:center}",
    "}",
    ".hm-modal-start{display:none}",
    "@media " + MOBILE + "{:root:has(.home-intro) .hm-modal-start{display:flex}}"
  ].join("\n");

  function isEn() {
    var q = new URLSearchParams(location.search).get("lang");
    if (q) return /^en/i.test(q);
    try { var v = localStorage.getItem("photoagent.lang"); if (v) return /^en/i.test(v); } catch (e) {}
    return /^en/i.test(document.documentElement.lang || "");
  }
  // 跟桌機「開始對話」同一顆圖（Lucide messages-circle，兩個對話框），不是原本單一個圓框。
  var ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M19.95 10.05a7 7 0 0 1 1.412 7.872 1 1 0 0 0-.058.787l.675 2.089a1 1 0 0 1-1.236 1.168l-2.155-.631a1 1 0 0 0-.745.06 7 7 0 0 1-7.793-1.445"/>' +
    '<path d="M2.696 12.708a1 1 0 0 0-.058-.785 7 7 0 1 1 3.518 3.473 1 1 0 0 0-.744-.061l-2.155.63a1 1 0 0 1-1.236-1.167z"/></svg>';

  function origStart() { return document.querySelector(".home-start .home-start-btn"); }
  function startLabel(btn) {
    var text = "";
    btn.childNodes.forEach(function (node) { if (node.nodeType === 3) text += node.textContent; });
    return text.trim();
  }
  function origUse() { return document.querySelector(".gallery-confirm-btn"); }

  // 選了幾張：從原本「使用所選照片（N 張）」的字讀；讀不到就數選取中的照片。
  function count() {
    var use = origUse();
    if (!use || use.disabled) return 0;
    var m = (use.textContent || "").match(/(\d+)/);
    if (m) return +m[1];
    return document.querySelectorAll(".upload-gallery-col .is-selected").length || 1;
  }

  function build() {
    var box = document.createElement("div");
    box.className = "hm-start";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "hm-start-btn";
    var hint = document.createElement("p");
    hint.className = "hm-start-hint";
    box.appendChild(btn);
    box.appendChild(hint);
    btn.addEventListener("click", function () {
      var use = origUse();
      if (use && !use.disabled) use.click();
      else if (origStart()) origStart().click();
    });
    return box;
  }

  var lastLabel = "";
  function sync() {
    var col = document.querySelector(".upload-gallery-col");
    if (!col || !document.querySelector(".home-intro")) return;
    var box = col.querySelector(".hm-start");
    if (!box) {
      box = build();
      var use = origUse();
      if (use && use.parentElement === col) use.insertAdjacentElement("afterend", box);
      else col.appendChild(box);
    }
    // 位置（order）由上面的 CSS 定，不再照抄原本「使用所選照片」那顆的 order（那顆在照片列下面，會把這顆拉回去）。
    var start = origStart();
    var n = count();
    // 只取按鈕本身的字：home-pick-count.js 會把張數徽章（.abg-count）塞進原按鈕，整顆 textContent 會變成「Start a Chat3」。
    var base = (start && startLabel(start)) || (isEn() ? "Start a Chat" : "開始對話");
    var EN = isEn();
    var label = base + "|" + n + "|" + EN;
    if (label === lastLabel && box.querySelector(".hm-start-btn").childNodes.length) return;
    lastLabel = label;
    var countText = n > 0 ? String(n) : "";
    box.querySelector(".hm-start-btn").innerHTML = ICON + "<span>" + base + "</span>" +
      (countText ? '<span class="hm-start-count">' + countText + "</span>" : "");
    var meta = document.querySelector(".home-start .chat-meta");
    box.querySelector(".hm-start-hint").textContent = meta ? meta.textContent : "";
  }

  // 「全部」面板：原本的「選好了」在首頁就是帶著選的照片開始對話，這裡換個名字、把張數放進去。
  function syncModal() {
    var foot = document.querySelector(".sample-modal-foot");
    if (!foot) return;
    var ok = foot.querySelector(".sample-modal-actions .variant_primary");
    if (!ok) return;
    var btn = foot.querySelector(".hm-modal-start");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hm-start-btn hm-modal-start";
      btn.addEventListener("click", function () {
        var o = document.querySelector(".sample-modal-foot .sample-modal-actions .variant_primary");
        if (o && !o.disabled) o.click();
      });
      foot.insertBefore(btn, foot.firstChild);
    }
    // 沒選時「選好了」是 disabled、旁邊寫「可以選多張，最多 100 張」；選了才變成「已選 N 張」。
    var m = ((foot.querySelector(".sample-modal-count") || {}).textContent || "").match(/(\d+)/);
    var n = ok.disabled || !m ? 0 : +m[1];
    var EN = isEn();
    var base = EN ? "Start a Chat" : "開始對話";
    var countText = n > 0 ? String(n) : "";
    var html = ICON + "<span>" + base + "</span>" + (countText ? '<span class="hm-start-count">' + countText + "</span>" : "");
    // 用自己記的 key 比，不比 innerHTML（瀏覽器會重排 SVG 的寫法，比了永遠不同 → 無限觸發 MutationObserver）。
    var key = base + "|" + n;
    if (btn.getAttribute("data-key") !== key) { btn.setAttribute("data-key", key); btn.innerHTML = html; }
    if (btn.disabled !== ok.disabled) btn.disabled = ok.disabled;
    if (foot.classList.contains("hm-has-count") !== n > 0) foot.classList.toggle("hm-has-count", n > 0);
  }

  function mount() {
    var style = document.createElement("style");
    style.id = "home-mobile-style";
    style.textContent = css;
    document.head.appendChild(style);
    sync();
    syncModal();
    // 選照片時會改 class 和「使用所選照片」的字，換頁時會換掉整塊：都跟著更新。
    new MutationObserver(function () { sync(); syncModal(); }).observe(document.body, {
      childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["class", "disabled"]
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
