/* 對話頁 UI 修改的「預覽版」：直接套在現在的 build 上，讓修改在正式 build 之前就看得到。
 *
 * 正式的修改在 handoff/chat-sidebar-tweaks.patch（改 frontend-chat 原始碼）。公司那份原始碼補上 patch、
 * 重新 build 之後，這支就可以拿掉——留著也不會衝突，只是重複。
 *
 * 這支涵蓋（patch 的第 1、3、4、5 項，加上截圖裡新標的一項）：
 *   1. 側欄的「總覽 · N 張」卡片（設定／全部分析／全部一鍵美化）拿掉
 *   3. 「上傳了 N 張照片」：照片一排，文字在下一排
 *   4. 對話輸入框卡片邊框 4px
 *   5. 明體（Noto Serif）改 Roboto
 *   +  標題旁的「· 總覽」副標拿掉
 * 第 2 項（單張／空狀態也用側欄）牽涉流程，只能靠 patch，這支做不到。
 * 掛法：frontend-chat/index.html 加 <script src="../chat-tweaks.js" defer></script>
 */
(function () {
  var css = [
    /* 1. 側欄的總覽卡片 */
    ".batch-overview-card{display:none !important}",
    /* 3. 上傳訊息：照片一排、文字在下一排，靠右 */
    ".chat-upload.is-batch{flex-direction:column;align-items:flex-end;gap:var(--sp-2,8px)}",
    /* 4. 輸入框卡片邊框 4px */
    ".chat-panel>.chat-composer{border-width:4px !important}",
    /* 5. 明體改 Roboto（用到 --chat-serif 的標題都跟著換） */
    ":root,:root:lang(zh-Hans){--chat-serif:Roboto,var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif}",
    /* +  「· 總覽」副標 */
    ".chat-panel-sub.ct-hide{display:none !important}"
  ].join("\n");

  var OVERVIEW = /^(總覽|总览|Overview)$/i;

  function mark() {
    var subs = document.querySelectorAll(".chat-panel-sub");
    for (var i = 0; i < subs.length; i++) {
      var hide = OVERVIEW.test((subs[i].textContent || "").trim());
      if (subs[i].classList.contains("ct-hide") !== hide) subs[i].classList.toggle("ct-hide", hide);
    }
  }

  function mount() {
    var font = document.createElement("link");
    font.rel = "stylesheet";
    font.href = "https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700&display=swap";
    document.head.appendChild(font);
    var style = document.createElement("style");
    style.id = "chat-tweaks-style";
    style.textContent = css;
    document.head.appendChild(style);
    mark();
    new MutationObserver(mark).observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
