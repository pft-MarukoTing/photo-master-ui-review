/* 首頁：選了範例／最近照片，就在「Start a Chat」後面顯示張數；新選的那張縮圖飛進那顆鍵（落下時張數跳一下）。
 *
 * 首頁定案為 A 版（範例照片在右側欄），原本的 A/B/C 比較（ab-home.js）已移除，只留下 A 版也在用的這一段。
 * 掛法：frontend-chat/index.html 加 <script src="../home-pick-count.js" defer></script>
 */
(function () {
  var css =
    ".home-start-btn .abg-count{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:22px;margin-inline-start:8px;padding:0 6px;box-sizing:border-box;" +
    "border-radius:999px;background:var(--fill-overlay-weak,rgba(0,0,0,.45));color:var(--text-inverse-strong,#fff);font-size:13px;font-weight:700;line-height:1}";

  var prevPicked = {};
  function syncPickCount() {
    var btn = document.querySelector(".home-start-btn");
    if (!btn) return;
    var badge = btn.querySelector(".abg-count");
    var picked = Array.prototype.slice.call(document.querySelectorAll(".upload-gallery-col .is-selected"));
    if (!picked.length) {
      if (badge) badge.remove();
      prevPicked = {};
      return;
    }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "abg-count";
      btn.appendChild(badge);
    }
    badge.textContent = String(picked.length);
    var next = {};
    picked.forEach(function (tile) {
      var img = tile.querySelector("img:not(.demo-image-tile-bg)") || tile.querySelector("img");
      var key = img && img.getAttribute("src");
      if (!key) return;
      next[key] = true;
      if (!prevPicked[key]) flyToButton(img, btn, badge);
    });
    prevPicked = next;
  }

  function flyToButton(img, btn, badge) {
    if (!img.animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var from = img.getBoundingClientRect(), to = badge.getBoundingClientRect();
    if (!from.width || !to.width) return;
    var ghost = img.cloneNode(false);
    ghost.removeAttribute("id");
    ghost.style.cssText = "position:fixed;z-index:2147483000;pointer-events:none;margin:0;object-fit:cover;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.25);" +
      "left:" + from.left + "px;top:" + from.top + "px;width:" + from.width + "px;height:" + from.height + "px";
    document.body.appendChild(ghost);
    var dx = to.left + to.width / 2 - (from.left + from.width / 2);
    var dy = to.top + to.height / 2 - (from.top + from.height / 2);
    var anim = ghost.animate([
      { transform: "translate(0,0) scale(1)", opacity: 1 },
      { transform: "translate(" + dx + "px," + dy + "px) scale(" + (22 / from.width) + ")", opacity: 0.35 }
    ], { duration: 480, easing: "cubic-bezier(.4,0,.2,1)", fill: "forwards" });
    var done = false;
    var finish = function () {
      if (done) return;
      done = true;
      ghost.remove();
      if (badge.isConnected) badge.animate([{ transform: "scale(1)" }, { transform: "scale(1.4)" }, { transform: "scale(1)" }], { duration: 260, easing: "ease-out" });
    };
    anim.onfinish = finish;
    // 分頁在背景時動畫不會跑完：保險起見，時間到就收掉飛行中的縮圖。
    setTimeout(finish, 900);
  }

  function mount() {
    var style = document.createElement("style");
    style.id = "home-pick-count-style";
    style.textContent = css;
    document.head.appendChild(style);
    syncPickCount();
    // 頁面是 SPA：DOM／class 一有變動就重新數一次；同一幀內的變動合併成一次，跑的時候先停止觀察，
    // 不會被自己加的徽章再觸發一輪。
    var opts = { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] };
    var pending = false;
    var observer = new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        observer.disconnect();
        try { syncPickCount(); } finally { observer.observe(document.body, opts); }
      });
    });
    observer.observe(document.body, opts);
  }

  // 以前 A/B 切換記在 localStorage 的版本，已經用不到了。
  try { localStorage.removeItem("photomaster-home-ab"); localStorage.removeItem("photomaster-home-c-prompt"); } catch (e) {}

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
