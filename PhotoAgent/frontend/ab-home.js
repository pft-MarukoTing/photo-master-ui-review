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
  var VARIANTS = ["a", "b"]; // C 版已拿掉（程式還留著，沒有入口）
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
    swapHeroImages();
    syncNextArrow();
    requestAnimationFrame(fitHomeB);
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
    "  html.ab-home-b{--abg-gutter:9.5vw;--abg-tile-h:clamp(84px,13vh,132px)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-container{padding:0 !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-grid{height:100%}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-content-row{flex-direction:column;align-items:stretch;justify-content:flex-start;",
    "    height:100%;padding:0 !important;gap:0}",
    /* 主視覺：佔掉照片列以外的高度 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-intro{flex:1 1 auto;min-height:0;width:100%;padding:0 !important;margin:0}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero{position:relative;display:block;width:100%;height:100%;max-width:none;margin:0;padding:0;",
    "    background:var(--surface-default,#fff)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare{position:absolute !important;top:0;left:0;margin:0 !important;width:100% !important;height:100% !important;",
    "    max-width:none;aspect-ratio:auto;border:0 !important;outline:0 !important;border-radius:0 !important;box-shadow:none !important}",
    /* 把圖放大，讓人物大一點（頭頂約在 hero 的 15%）；以臉為中心放大，臉不會離開分割線。before／after 一起放大才對得上。 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-img{object-position:50% 28%;transform:scale(1.36);transform-origin:67% 54%}",
    /* after 圖的裁切寫在圖片本身，圖放大後裁切邊緣也被推走（會露出一條 before）：換算回放大前的座標，邊緣才會剛好在分割線上。 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-img.is-after{clip-path:inset(0 0 0 calc(67% + (var(--pos) - 67%) / 1.36))}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag{top:24px}",
    /* B 版的 Before / After 標籤跟著滑桿走，不會像 A 版那樣被滑桿蓋住，所以一直顯示、不淡出。 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag.is-hidden{opacity:1 !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag:not(.is-after){left:auto;right:calc(100% - var(--pos,50%) + 16px)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-compare-tag.is-after{left:calc(var(--pos,50%) + 16px);right:auto}",
    /* 文字壓在左邊，底下墊一層往右淡掉的白，字才讀得清楚 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero-copy{position:relative;z-index:3;display:flex;flex-direction:column;justify-content:center;",
    "    width:min(640px,46%);height:100%;padding:0 0 0 9.5vw;pointer-events:none}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero-copy>*{pointer-events:auto}",
    /* 照片左緣淡進左邊的底色，文字和照片接在一起 */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero::before{content:'';position:absolute;top:0;bottom:0;left:0;z-index:2;width:60%;pointer-events:none;",
    "    background:linear-gradient(90deg,rgba(255,255,255,.86) 0%,rgba(255,255,255,.82) 32%,rgba(255,255,255,0) 100%)}",
    "  html[data-theme=dark].ab-home-b #upload-section:has(.home-intro) .home-hero::before{background:linear-gradient(90deg,rgba(18,24,26,.86) 0%,rgba(18,24,26,.82) 32%,rgba(18,24,26,0) 100%)}",
    /* 照片底部往下淡成全白（跟下面的白底接在一起，底邊沒有細節、沒有硬邊） */
    "  html.ab-home-b #upload-section:has(.home-intro) .home-hero::after{content:'';position:absolute;left:0;right:0;bottom:0;z-index:2;height:34%;pointer-events:none;",
    "    background:linear-gradient(180deg,rgba(255,255,255,0) 0%,var(--surface-default,#fff) 100%)}",
    "  html[data-theme=dark].ab-home-b #upload-section:has(.home-intro) .home-hero::after{background:linear-gradient(180deg,rgba(18,24,26,0) 0%,var(--surface-default,#12181a) 100%)}",
    "  html.ab-home-b #upload-section:has(.home-intro) .home-headline{font-size:clamp(26px,min(3.6vw,6.2vh),56px)}",
    /* 照片列：從右邊固定欄變成主視覺下方的一條 */
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col{position:relative !important;inset:auto !important;flex:0 0 auto;width:100% !important;height:auto !important;",
    "    max-width:none;margin:0;padding:8px var(--abg-gutter) 24px !important;border:0 !important;background:var(--surface-default,#fff) !important;box-shadow:none !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-heading{width:100%;justify-content:flex-start;margin:0 0 16px;border-bottom:1px solid var(--stroke-weak,rgba(17,24,26,.1))}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-tabs{width:auto;flex:0 0 auto}",
    /* 分頁的灰色底線要跟分頁（hover 範圍）等寬：padding 只給分頁本身，外面那層 group 不要再加。 */
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-tab-recent-group{flex:0 0 auto;min-width:0;padding-inline:0}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-tab{flex:0 0 auto;min-width:0;padding-inline:20px}",
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
    /* 「最近」還沒有照片時的空狀態：不能比「範例」那一排高（切分頁時整頁會跳），高度＝一排照片（--abg-tile-h＋下面 6px）。
       橫向排成一列：左＝圖示、中＝標題和說明、右＝上傳鈕。 */
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .gallery-empty{display:grid !important;flex:none;grid-template-columns:auto auto auto;",
    "    grid-template-areas:'icon title cta' 'icon sub cta';column-gap:20px;row-gap:2px;align-items:center;justify-content:center;text-align:start;",
    "    height:calc(var(--abg-tile-h) + 6px);min-height:0;padding:0 !important;overflow:hidden}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .gallery-empty .gallery-empty-icon{grid-area:icon;width:48px;height:48px;margin:0}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .gallery-empty .gallery-empty-title{grid-area:title;align-self:end}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .gallery-empty .gallery-empty-sub{grid-area:sub;align-self:start}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .gallery-empty .gallery-empty-cta{grid-area:cta;margin:0 0 0 12px}",
    /* B 版：「最近」第一張要跟分頁、「範例」一樣靠左（原本日期群組的 ::before 在前面佔了一塊，整排被推開約 24px）；
       「範例」和「最近」的磚圓角一樣（8px）。 */
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .history-day-group::before{display:none !important}",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .demo-images-grid>.demo-image-tile,",
    "  html.ab-home-b #upload-section:has(.home-intro) .upload-gallery-col .history-grid>*{border-radius:var(--corner-radius-8,8px) !important}",
    /* 「使用所選照片」：放在分頁那一行的右邊 */
    /* B 版照片列最後面的向右箭頭：還有更多照片的提示，也能按它往右捲 */
    ".abg-next{position:absolute;z-index:6;display:none;align-items:center;justify-content:center;width:40px;height:40px;padding:0;border:1px solid var(--stroke-weak,rgba(17,24,26,.12));border-radius:50%;",
    "  background:var(--surface-default,#fff);color:var(--text-strong,#11181a);box-shadow:0 4px 12px rgba(0,0,0,.16);cursor:pointer}",
    ".abg-next:hover{background:var(--surface-default,#fff) linear-gradient(var(--fill-hover,rgba(17,24,26,.04)),var(--fill-hover,rgba(17,24,26,.04)))}",
    "html.ab-home-b .abg-next.is-on{display:flex}",
    /* 沒有「使用所選照片」了：選好的照片直接飛進「Start a Chat」，數量寫在那顆鍵後面 */
    "  html.ab-home-b #upload-section:has(.home-intro) .gallery-confirm-btn{display:none !important}",
    "  html.ab-home-b .home-start-btn .abg-count{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:22px;margin-inline-start:8px;padding:0 6px;box-sizing:border-box;",
    "    border-radius:999px;background:var(--fill-overlay-weak,rgba(0,0,0,.45));color:var(--text-inverse-strong,#fff);font-size:13px;font-weight:700;line-height:1}",
    /* 「使用所選照片」：右下角（固定在視窗角落，浮在照片列上） */
    "  html.ab-home-b #upload-section:has(.home-intro) .gallery-confirm-btn{position:fixed !important;top:auto !important;bottom:24px;right:var(--abg-gutter);z-index:5;width:auto !important;",
    "    min-width:180px;margin:0 !important;box-shadow:0 8px 24px rgba(0,0,0,.16)}",
    "}",

    /* ── C 版：置中大輸入框 ── */
    ".abc-hero{display:none}",
    "html.ab-home-c #upload-section:has(.home-intro) .home-hero{display:none !important}",
    "html.ab-home-c #upload-section:has(.home-intro) .abc-hero{position:relative;display:flex;flex-direction:column;align-items:center;width:100%;max-width:1100px;margin:0 auto;padding:0 16px;text-align:center}",
    /* 標題 Wix Madefor Display 粗體；副標和提示字 Wix Madefor Text 細體（中文落到 Noto Sans） */
    ".abc-title{margin:0;font:800 clamp(30px,3.4vw,52px)/1.2 'Wix Madefor Display',var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;letter-spacing:-.01em;color:transparent;background:var(--yco-gradient,linear-gradient(90deg,#03ade2,#3fd75b));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}",
    ".abc-sub{margin:12px 0 0;font:400 clamp(15px,1.3vw,19px)/1.5 'Wix Madefor Text',var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;color:var(--text-weak,#4b5a5e)}",
    ".abc-box{position:relative;z-index:2;display:flex;flex-direction:column;width:min(760px,100%);margin:clamp(20px,3.5vh,40px) 0 0;padding:20px 20px 16px 24px;",
    "  border:1px solid var(--stroke-weak,rgba(17,24,26,.12));border-radius:24px;background:var(--surface-default,#fff);box-shadow:0 10px 30px rgba(9,22,26,.08);text-align:start}",
    ".abc-box:focus-within{border-color:var(--stroke-brand-strong,#1a8fd6);box-shadow:0 0 0 3px var(--stroke-focus,rgba(26,143,214,.25)),0 10px 30px rgba(9,22,26,.08)}",
    ".abc-input{width:100%;min-height:56px;resize:none;border:0;outline:0;background:none;padding:0;font:400 18px/1.5 'Wix Madefor Text',var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;color:var(--text-strong,#11181a)}",
    ".abc-input::placeholder{color:var(--text-weak,#8a979a);font-weight:400}",
    /* 下面點選的照片直接顯示在輸入框裡（新增照片鈕旁邊），× 取消選取 */
    ".abc-attach{display:flex;align-items:center;gap:10px;flex:0 1 auto;min-width:0;overflow-x:auto;overflow-y:hidden;padding:8px 10px 2px 4px;margin:-8px 0 -2px}",
    /* 有選照片時，照片排在第一個位置，新增鈕接在照片後面。 */
    ".abc-left.has-photos .abc-attach{order:1}",
    ".abc-left.has-photos .abc-add{order:2}",
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
    var left = box.parentElement;
    if (left) left.classList.toggle("has-photos", items.length > 0);
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
    // 介面是英文時不出現中文（title／aria-label 也一樣）。
    var en = !/^zh/i.test(document.documentElement.lang || "zh");
    var T = en
      ? { a: "A: sample photos on the right", b: "B: full-bleed hero, sample photos below", c: "C: centered prompt box, sample photos below" }
      : { a: "A：範例照片在右側", b: "B：滿版主視覺，範例照片在下方", c: "C：置中輸入框，範例照片在下方" };
    box.setAttribute("aria-label", en ? "Home version" : "首頁版本");
    box.innerHTML =
      '<button type="button" class="ab-switch-opt" data-v="a" title="' + T.a + '">A</button>' +
      '<button type="button" class="ab-switch-opt" data-v="b" title="' + T.b + '">B</button>';
    box.addEventListener("click", function (e) {
      var b = e.target.closest(".ab-switch-opt");
      if (!b) return;
      apply(b.getAttribute("data-v"));
      window.scrollTo({ top: 0 });
    });
    document.body.appendChild(box);
    // 之後切換介面語言：title／aria-label 跟著換。
    new MutationObserver(function () {
      var e2 = !/^zh/i.test(document.documentElement.lang || "zh");
      var T2 = e2
        ? { a: "A: sample photos on the right", b: "B: full-bleed hero, sample photos below", c: "C: centered prompt box, sample photos below" }
        : { a: "A：範例照片在右側", b: "B：滿版主視覺，範例照片在下方", c: "C：置中輸入框，範例照片在下方" };
      box.setAttribute("aria-label", e2 ? "Home version" : "首頁版本");
      ["a", "b"].forEach(function (k) { var el = box.querySelector('[data-v="' + k + '"]'); if (el) el.title = T2[k]; });
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
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
      swapHeroImages();
      syncNextArrow();
      syncPickCount();
    };
    sync();
    // 整個頁面一有 DOM／class 變動就跑 sync，而 sync 裡有讀版面（getBoundingClientRect）、查一堆元素。
    // 切換 A／B／C 時整頁重排，會連續冒出大量變動，每一個都跑一次就會卡住。
    // 這裡改成：同一個畫面幀內的變動只跑一次（requestAnimationFrame 合併），而且跑的時候先停止觀察，
    // 不會被 sync 自己改的東西（箭頭鈕的 class、換圖、重畫 C 版）再觸發一輪。
    var observeOpts = { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] };
    var pendingSync = false;
    var observer = new MutationObserver(function () {
      if (pendingSync) return;
      pendingSync = true;
      requestAnimationFrame(function () {
        pendingSync = false;
        observer.disconnect();
        try { sync(); fitHomeB(); } finally { observer.observe(document.body, observeOpts); }
      });
    });
    observer.observe(document.body, observeOpts);
  }

  // B 版主視覺的 Before / After 換成這兩張（assets/home/，1860×846 的寬圖，配 B 版滿版的主視覺）；
  // 切回 A、C 版就還原成原本那組。原本的 src 記在 data-ab-orig，換版或重畫都會再檢查一次。
  var HERO_B = { before: "../assets/home/hero-b-before.jpg", after: "../assets/home/hero-b-after.jpg" };
  // B 版：照片列右邊有更多照片時，在列的右端放一顆向右箭頭（按下去往右捲一頁），捲到底就收起來。
  function syncNextArrow() {
    var col = document.querySelector(".upload-gallery-col");
    var grid = col && col.querySelector(".demo-images-grid");
    var b = document.documentElement.classList.contains("ab-home-b") && window.matchMedia("(min-width:901px) and (orientation:landscape)").matches;
    var btn = col && col.querySelector(".abg-next");
    // 不是 B 版桌機橫向（切回 A、視窗變窄）時，這顆鈕的樣式（在 media 裡）不會套用，要直接藏起來，不然會變成一條沒樣式的灰框。
    if (!b || !grid) { if (btn) { btn.classList.remove("is-on"); btn.style.display = "none"; } return; }
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "abg-next";
      btn.setAttribute("aria-label", "Next photos");
      btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
      btn.addEventListener("click", function () {
        var g = col.querySelector(".demo-images-grid");
        if (g) g.scrollBy({ left: Math.max(240, g.clientWidth * 0.8), behavior: "smooth" });
      });
      col.appendChild(btn);
      var scrollPending = false;
      grid.addEventListener("scroll", function () {
        if (scrollPending) return;
        scrollPending = true;
        requestAnimationFrame(function () { scrollPending = false; syncNextArrow(); });
      }, { passive: true });
    }
    var cr = col.getBoundingClientRect(), gr = grid.getBoundingClientRect();
    btn.style.display = "";
    btn.style.top = Math.round(gr.top - cr.top + gr.height / 2 - 20) + "px";
    btn.style.right = "calc(var(--abg-gutter) - 20px)";
    btn.classList.toggle("is-on", grid.scrollLeft + grid.clientWidth < grid.scrollWidth - 4);
  }

  // B 版：選了照片就在「Start a Chat」後面顯示張數；新選的那張縮圖飛進那顆鍵（落下時張數跳一下）。
  var prevPicked = {};
  function syncPickCount() {
    var b = document.documentElement.classList.contains("ab-home-b");
    var btn = document.querySelector(".home-start-btn");
    if (!btn) return;
    var badge = btn.querySelector(".abg-count");
    var picked = b ? Array.prototype.slice.call(document.querySelectorAll(".upload-gallery-col .is-selected")) : [];
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

  // B 版：視窗矮的時候文字區（標題、說明、按鈕）會被下面的 sample 列擠到。
  // 量文字實際需要的高度，放不下就一格一格把 sample 縮小（最小 48px），上下各留 20px 喘息空間。
  function fitHomeB() {
    var root = document.documentElement;
    if (!root.classList.contains("ab-home-b")) { root.style.removeProperty("--abg-tile-h"); return; }
    var copy = document.querySelector(".home-hero-copy");
    if (!copy || !copy.offsetParent || !copy.children.length) return;
    var t = Math.min(132, Math.max(84, window.innerHeight * 0.13));
    var need = function () {
      var first = copy.children[0].getBoundingClientRect();
      var last = copy.children[copy.children.length - 1].getBoundingClientRect();
      return last.bottom - first.top;
    };
    root.style.setProperty("--abg-tile-h", t + "px");
    var guard = 0;
    while (need() > copy.clientHeight - 40 && t > 48 && guard++ < 30) {
      t -= 4;
      root.style.setProperty("--abg-tile-h", t + "px");
    }
  }
  window.addEventListener("resize", function () { requestAnimationFrame(fitHomeB); });

  function swapHeroImages() {
    var b = document.documentElement.classList.contains("ab-home-b");
    var imgs = document.querySelectorAll(".home-compare-img");
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (!img.hasAttribute("data-ab-orig")) img.setAttribute("data-ab-orig", img.getAttribute("src") || "");
      var want = b ? (img.classList.contains("is-after") ? HERO_B.after : HERO_B.before) : img.getAttribute("data-ab-orig");
      if (want && img.getAttribute("src") !== want) img.setAttribute("src", want);
    }
  }

  // 先套上 class，避免首屏先畫 A 再跳到別版。
  setClass(read());
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
