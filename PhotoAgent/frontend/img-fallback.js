/* 圖片載不出來（網路斷了、檔案過期…）時的替代圖：不要讓瀏覽器顯示「?」圖示和檔名，
 * 換成一塊淺灰底、正中間一個深灰色「圖庫」圖示的佔位圖（樣子在 styles.css 的 img.img-fallback）。
 *
 * 對話模式和編輯器共用：兩邊的 index.html 都在 <head> 最前面載入這支，之後才出現的圖片也接得到
 * （error 不會冒泡，所以在 document 上用 capture 聽）。
 *
 * 有些元件自己有備案（例如美化後的縮圖載不出來就改放原圖）：先讓它們處理，等一下 src 還是同一個
 * 失敗的網址才換成佔位圖。之後這張又載入成功（元件換了網址、或重新連線後重試）就拿掉佔位圖的樣子。
 * 不想要佔位圖的圖片加 data-no-fallback。
 */
(function () {
  if (window.__imgFallback) return;
  window.__imgFallback = true;

  // 1×1 透明圖：<img> 換成它就不會再出現破圖示，底色和圖示由 CSS 的背景畫出來。
  var BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

  function isImg(el) {
    return el && el.tagName === "IMG" && !el.hasAttribute("data-no-fallback");
  }

  document.addEventListener("error", function (e) {
    var img = e.target;
    if (!isImg(img)) return;
    var failed = img.getAttribute("src");
    if (!failed || failed === BLANK) return;
    // 讓元件自己的 onError 先跑（它可能會換一張別的圖）。
    setTimeout(function () {
      if (!img.isConnected || img.getAttribute("src") !== failed) return;
      img.setAttribute("data-fallback-src", failed);
      img.classList.add("img-fallback");
      img.setAttribute("src", BLANK);
    }, 0);
  }, true);

  // 換成真的圖、載入成功了：拿掉佔位圖的樣子。
  document.addEventListener("load", function (e) {
    var img = e.target;
    if (!img || img.tagName !== "IMG" || !img.classList.contains("img-fallback")) return;
    if (img.getAttribute("src") === BLANK) return;
    img.classList.remove("img-fallback");
    img.removeAttribute("data-fallback-src");
  }, true);

  // 網路恢復時，把換成佔位圖的那些重新試一次；還是載不出來就會再換回佔位圖。
  window.addEventListener("online", function () {
    var imgs = document.querySelectorAll("img.img-fallback[data-fallback-src]");
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      var src = img.getAttribute("data-fallback-src");
      img.removeAttribute("data-fallback-src");
      img.setAttribute("src", src);
    }
  });
})();
