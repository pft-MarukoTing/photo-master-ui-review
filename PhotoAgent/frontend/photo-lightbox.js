/* 共用的大圖檢視（Chat 和 Editor 同一份）：window.PMLightbox。
 *
 * 版面照 Figma（lightbox 桌機 3778:200399／手機 3778:202920）：
 *   深色底（黑 80% + 3px 模糊）、中間一張 20px 圓角的照片、右上白色圓形關閉、右下半透明黑圓形動作（下載）、
 *   多張時左右各一顆箭頭，底下一排縮圖（一般 60px、目前這張 80px 加 3px 白框）。
 *   手機箭頭縮小並貼著照片左右邊，縮圖列可橫向捲。
 *
 * 用法：
 *   PMLightbox.open({
 *     items: [{ src, thumb?, tag?, group?, download?: { href, name, onClick? } }],
 *       group：同一張照片的原圖和各版（A Av1 Av2）給同一個值；group 換了，縮圖列就畫一條 | 分隔線（A Av1 Av2 | B | C Cv1 | D）
 *     index: 0,
 *     label: "照片大圖",
 *     onClose(), onIndex(i)
 *   });
 *   PMLightbox.close();
 * 只有一張時沒有箭頭和縮圖列。Esc 關閉、←／→ 切換、點照片以外的地方關閉。
 * 要改外觀只改這支，Chat 和 Editor 會一起變。
 */
(function () {
  if (window.PMLightbox) return;

  var CSS = [
    ".pmlb{position:fixed;inset:0;z-index:2000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;box-sizing:border-box;",
    "  padding:24px;background:rgba(0,0,0,.8);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);animation:pmlb-in .15s ease-out}",
    "@keyframes pmlb-in{from{opacity:0}}",
    ".pmlb-stage{position:relative;flex:1 1 auto;min-height:0;width:100%;display:flex;align-items:center;justify-content:center}",
    ".pmlb-frame{position:relative;max-width:100%;max-height:100%;display:flex;align-items:center;justify-content:center;box-sizing:border-box}\n.pmlb--multi .pmlb-img{width:min(calc(100vw - 224px),calc((100vh - 144px) * var(--ar,1)),calc((100dvh - 144px) * var(--ar,1)))}",
    ".pmlb-img{display:block;width:min(calc(100vw - 48px),calc((100vh - 48px) * var(--ar,1)),calc((100dvh - 48px) * var(--ar,1)));height:auto;aspect-ratio:var(--ar,1);max-width:100%;object-fit:contain;border-radius:var(--corner-radius-20,20px);background:none;border:0;box-shadow:none}",
    ".pmlb-tag{position:absolute;left:16px;top:16px;padding:4px 10px;border-radius:999px;background:var(--fill-overlay,rgba(0,0,0,.6));color:#fff;font:500 13px/1.2 Roboto,var(--cjk-sans,'Noto Sans TC'),system-ui,sans-serif;pointer-events:none}",
    ".pmlb-btn{position:absolute;display:flex;align-items:center;justify-content:center;width:36px;height:36px;padding:0;border-radius:50%;cursor:pointer;text-decoration:none;color:#fff;border:0}",
    ".pmlb-close{position:fixed;top:24px;right:40px;z-index:3;background:#fff;color:var(--text-strong,#11181a);box-shadow:inset 0 0 0 1px rgba(17,24,26,.2)}",
    ".pmlb-act{right:16px;bottom:16px;background:var(--fill-overlay,rgba(0,0,0,.6));box-shadow:0 2px 8px rgba(0,0,0,.24)}",
    ".pmlb-btn:hover{filter:brightness(.94)}.pmlb-act:hover{filter:brightness(1.25)}",
    ".pmlb-btn:focus-visible,.pmlb-arrow:focus-visible,.pmlb-thumb:focus-visible{outline:2px solid #fff;outline-offset:2px}",
    ".pmlb-btn svg,.pmlb-arrow svg{flex:none;display:block}",
    ".pmlb-arrow{position:absolute;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;width:56px;height:56px;padding:0;border-radius:50%;",
    "  background:var(--fill-strong,rgba(9,22,26,.95));color:#fff;border:1.167px solid var(--stroke-strong,rgba(255,255,255,.24));cursor:pointer}",
    ".pmlb-arrow:hover{filter:brightness(1.3)}",
    ".pmlb-prev{left:16px}.pmlb-next{right:16px}",
    ".pmlb-strip{flex:none;display:flex;align-items:center;justify-content:center;gap:8px;max-width:100%;min-height:80px;overflow-x:auto;scrollbar-width:none}",
    ".pmlb-strip::-webkit-scrollbar{display:none}",
    ".pmlb-sep{flex:none;align-self:center;width:1px;height:40px;margin:0 4px;background:rgba(255,255,255,.4);border-radius:1px}",
    ".pmlb-thumb{flex:none;position:relative;display:block;height:60px;min-width:32px;padding:0;border:0;border-radius:12px;overflow:hidden;opacity:.5;cursor:pointer;background:rgba(255,255,255,.08)}",
    ".pmlb-thumb img{display:block;height:100%;width:auto;max-width:none}",
    ".pmlb-thumb{transition:height .24s cubic-bezier(.3,.7,.2,1),opacity .24s ease,box-shadow .24s ease}",
    ".pmlb-thumb img{transition:height .24s cubic-bezier(.3,.7,.2,1)}",
    ".pmlb-img{transition:opacity .2s ease}.pmlb-img.is-swapping{opacity:0}",
    /* 多張時照片可以左右拖：游標是手，拖的時候整張（含標籤、下載鈕）跟著手走。 */
    ".pmlb--multi .pmlb-frame{cursor:grab;touch-action:pan-y;will-change:transform}.pmlb--multi .pmlb-frame.is-dragging{cursor:grabbing}",
    ".pmlb--multi .pmlb-frame .pmlb-btn{cursor:pointer}",
    ".pmlb-thumb:hover{opacity:.8}",
    ".pmlb-thumb.is-current{height:80px;opacity:1;box-shadow:inset 0 0 0 3px rgba(255,255,255,.6)}",
    ".pmlb-thumb.is-current img{height:80px;width:auto}",
    ".pmlb-thumb.is-current::after{content:'';position:absolute;inset:0;border-radius:12px;box-shadow:inset 0 0 0 3px rgba(255,255,255,.6)}",
    "@media (max-width:900px),(max-width:1200px) and (orientation:portrait){",
    "  .pmlb{-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px)}",
    /* 手機的版型跟 Editor 全螢幕一樣：照片四邊留 24px、依比例撐滿，縮圖列固定在底部；標籤和下載在照片裡，X 在右上角照片外，箭頭在照片左右邊裡面 */
    "  .pmlb--multi .pmlb-img{width:min(calc(100vw - 48px),calc((100vh - 144px) * var(--ar,1)),calc((100dvh - 144px) * var(--ar,1)))}",
    "  .pmlb-img{width:min(calc(100vw - 48px),calc((100vh - 48px) * var(--ar,1)),calc((100dvh - 48px) * var(--ar,1)))}",
    "  .pmlb-arrow{width:36px;height:36px;border-width:1px}.pmlb-arrow svg{width:20px;height:20px}",
    "  .pmlb-prev{left:16px}.pmlb-next{right:16px}.pmlb-close{top:16px;right:16px}",
    "  .pmlb-strip{justify-content:flex-start;width:100%}",
    "  .pmlb-strip.is-short{justify-content:center}",
    "}",
    "@media (prefers-reduced-motion:reduce){.pmlb{animation:none}.pmlb-thumb,.pmlb-thumb img,.pmlb-img{transition:none}}"
  ].join("\n");

  var ICON = {
    x: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    down: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15V3M7 10l5 5 5-5M5 21h14"/></svg>',
    prev: '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>',
    next: '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>'
  };

  function isEn() {
    var q = new URLSearchParams(location.search).get("lang");
    if (q) return /^en/i.test(q);
    try { var v = localStorage.getItem("photoagent.lang"); if (v) return /^en/i.test(v); } catch (e) {}
    return /^en/i.test(document.documentElement.lang || "");
  }
  function L(zh, en) { return isEn() ? en : zh; }

  var state = null;

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html) n.innerHTML = html;
    return n;
  }

  function ensureStyle() {
    if (document.getElementById("pmlb-style")) return;
    var s = document.createElement("style");
    s.id = "pmlb-style";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // 縮圖列（Lightbox 和 Editor 全螢幕共用）：回傳 { strip, thumbs }；同 group 連續排，group 換了畫「｜」。
  function buildStrip(items, onPick, opts) {
    ensureStyle();
    var strip = el("div", "pmlb-strip");
    strip.setAttribute("role", "tablist");
    // 每張照片都只有一格（還沒美化、沒有各版）時不畫分隔線，只有某組有 2 格以上才分組
    var grouped = !!(opts && opts.separators) || items.some(function (it, k) { return k > 0 && it.group === items[k - 1].group; });
    var thumbs = items.map(function (it, k) {
      if (grouped && k > 0 && it.group !== items[k - 1].group) {
        var sep = el("span", "pmlb-sep");
        sep.setAttribute("aria-hidden", "true");
        strip.appendChild(sep);
      }
      var b = el("button", "pmlb-thumb");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-label", it.label || L("第 " + (k + 1) + " 張", "Photo " + (k + 1)));
      var im = el("img");
      im.src = it.thumb || it.src;
      im.alt = "";
      im.loading = "lazy";
      b.appendChild(im);
      b.addEventListener("click", function (e) { e.stopPropagation(); onPick(k); });
      if (it.disabled) b.disabled = true;
      strip.appendChild(b);
      return b;
    });
    return { strip: strip, thumbs: thumbs };
  }

  // 標出目前這格並捲到中間
  function markCurrent(built, index) {
    built.thumbs.forEach(function (b, k) {
      var on = k === index;
      b.classList.toggle("is-current", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    var cur = built.thumbs[index];
    if (cur) built.strip.scrollTo({ left: Math.max(0, cur.offsetLeft - (built.strip.clientWidth - cur.offsetWidth) / 2), behavior: "smooth" });
    built.strip.classList.toggle("is-short", built.strip.scrollWidth <= built.strip.clientWidth);
  }

  // 左右滑（手指）或左右拖（滑鼠）：往左 onStep(+1)＝下一張，往右 onStep(-1)＝上一張。
  // 用 pointer 事件，手指、滑鼠、觸控筆同一套；水平移動夠多、而且比直向明顯才算，上下捲動不會誤觸；
  // 拖完放開那一下的 click 會被吃掉，不會被當成點背景。
  // opts.follow：拖的時候跟著手左右移動的元素（大圖用照片那張卡）。放開時拖得夠遠就換張——
  // 新的那張從拖過來的那一側滑回中間（slideIn）；不夠遠就彈回原位。不限拖多久（慢慢拖也算），
  // 確定是水平拖曳後就抓住指標，拖出範圍外才放開也照算。

  // 換張時新的那張從旁邊滑回中間：dir +1（下一張）從右邊進來，-1（上一張）從左邊進來。
  // 拖曳、上一張／下一張鈕、鍵盤左右鍵都用這一個，看起來一樣。減少動態效果時不滑。
  function slideIn(follow, dir) {
    if (!follow) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    follow.style.transition = "none";
    follow.style.transform = reduce ? "" : "translateX(" + dir * 60 + "px)";
    if (reduce) return;
    void follow.offsetWidth;
    follow.style.transition = "transform .22s cubic-bezier(.2,.8,.2,1)";
    follow.style.transform = "";
  }

  function attachSwipe(el, onStep, opts) {
    var ignore = (opts && opts.ignore) || "button, a, input, label";
    var follow = (opts && opts.follow) || null;
    el.style.touchAction = "pan-y";
    var down = null, moved = false;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function setX(x, animate) {
      if (!follow) return;
      follow.style.transition = animate && !reduce ? "transform .22s cubic-bezier(.2,.8,.2,1)" : "none";
      follow.style.transform = x ? "translateX(" + x + "px)" : "";
    }
    el.addEventListener("pointerdown", function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      if (e.target.closest && e.target.closest(ignore)) { down = null; return; }
      down = { x: e.clientX, y: e.clientY, id: e.pointerId };
      moved = false;
    });
    el.addEventListener("pointermove", function (e) {
      if (!down || e.pointerId !== down.id) return;
      var dx = e.clientX - down.x, dy = e.clientY - down.y;
      if (!moved && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) {
        moved = true;
        if (follow) follow.classList.add("is-dragging");
        // 確定是在拖了才抓住指標（拖出範圍外放開也照算）。一按下就抓的話，單純點照片的 click
        // 也會變成點在 el 上，被當成點背景而關掉大圖。
        try { el.setPointerCapture(e.pointerId); } catch (err) { /* old browsers */ }
      }
      if (moved) setX(dx, false);
    });
    function end(e, cancelled) {
      if (!down || (e && e.pointerId !== down.id)) return;
      var dx = cancelled ? 0 : e.clientX - down.x, dy = cancelled ? 0 : e.clientY - down.y;
      down = null;
      if (follow) follow.classList.remove("is-dragging");
      var far = Math.max(48, follow ? follow.offsetWidth * 0.12 : 48);
      if (!moved || Math.abs(dx) < far || Math.abs(dx) < Math.abs(dy) * 1.5) {
        setX(0, true);
        return;
      }
      var dir = dx < 0 ? 1 : -1;
      onStep(dir);
      // 新的那張從拖的方向那一側進來：往左拖（下一張）就從右邊滑回中間。
      slideIn(follow, dir);
    }
    el.addEventListener("pointerup", function (e) { end(e, false); });
    el.addEventListener("pointercancel", function (e) { end(e, true); });
    el.addEventListener("click", function (e) { if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; } }, true);
  }

  function close(silent) {
    if (!state) return;
    var s = state;
    state = null;
    document.removeEventListener("keydown", s.onKey, true);
    s.root.remove();
    if (s.prevFocus && s.prevFocus.focus) { try { s.prevFocus.focus({ preventScroll: true }); } catch (e) {} }
    if (!silent && s.opts.onClose) s.opts.onClose();
  }

  function show(i) {
    var s = state;
    if (!s) return;
    var n = s.items.length;
    s.index = ((i % n) + n) % n;
    var it = s.items[s.index];
    if (s.img.getAttribute("src") !== it.src && s.img.getAttribute("src")) {
      var img = s.img;
      img.classList.add("is-swapping");
      var done = function () { img.classList.remove("is-swapping"); img.removeEventListener("load", done); img.removeEventListener("error", done); };
      img.addEventListener("load", done);
      img.addEventListener("error", done);
      setTimeout(done, 600);
    }
    s.img.src = it.src;
    s.img.alt = it.alt || "";
    s.tag.textContent = it.tag || "";
    s.tag.hidden = !it.tag;
    var d = it.download;
    s.dl.hidden = !d;
    if (d) {
      s.dl.href = d.href || it.src;
      if (d.name) s.dl.setAttribute("download", d.name); else s.dl.setAttribute("download", "");
    }
    if (s.thumbs) {
      s.thumbs.forEach(function (b, k) {
        var on = k === s.index;
        b.classList.toggle("is-current", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
        if (on && b.scrollIntoView) {
          var strip = s.strip;
          var left = b.offsetLeft - (strip.clientWidth - b.offsetWidth) / 2;
          strip.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
        }
      });
    }
    if (s.opts.onIndex) s.opts.onIndex(s.index);
  }

  function open(opts) {
    opts = opts || {};
    var items = (opts.items || []).filter(function (x) { return x && x.src; });
    if (!items.length) return;
    close(true);
    ensureStyle();

    var root = el("div", "pmlb");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", opts.label || L("照片大圖", "Photo viewer"));

    var stage = el("div", "pmlb-stage");
    var frame = el("div", "pmlb-frame");
    var img = el("img", "pmlb-img");
    var tag = el("span", "pmlb-tag");
    img.addEventListener("load", function () { if (img.naturalWidth) img.style.setProperty("--ar", String(img.naturalWidth / img.naturalHeight)); });
    frame.appendChild(img);
    stage.appendChild(frame);

    var closeBtn = el("button", "pmlb-btn pmlb-close", ICON.x);
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", L("關閉", "Close"));
    var dl = el("a", "pmlb-btn pmlb-act", ICON.down);
    dl.setAttribute("aria-label", L("下載", "Download"));
    dl.title = L("下載", "Download");
    // 圖放在 frame 裡，按鈕和標籤貼著 frame 的四角（frame 會縮到照片大小）
    frame.appendChild(tag);
    root.appendChild(closeBtn);
    frame.appendChild(dl);

    var multi = items.length > 1;
    var s = { opts: opts, items: items, index: 0, root: root, img: img, tag: tag, dl: dl, thumbs: null, strip: null, prevFocus: document.activeElement };

    if (multi) {
      var prev = el("button", "pmlb-arrow pmlb-prev", ICON.prev);
      prev.type = "button";
      prev.setAttribute("aria-label", L("上一張", "Previous"));
      var next = el("button", "pmlb-arrow pmlb-next", ICON.next);
      next.type = "button";
      next.setAttribute("aria-label", L("下一張", "Next"));
      // 上一張／下一張鈕：跟拖曳一樣，新的那張從旁邊滑回中間。
      prev.addEventListener("click", function (e) { e.stopPropagation(); show(s.index - 1); slideIn(frame, -1); });
      next.addEventListener("click", function (e) { e.stopPropagation(); show(s.index + 1); slideIn(frame, 1); });
      stage.appendChild(prev);
      stage.appendChild(next);
    }
    root.appendChild(stage);

    if (multi) {
      root.classList.add("pmlb--multi");
      var built = buildStrip(items, function (k) { show(k); });
      s.strip = built.strip;
      s.thumbs = built.thumbs;
      root.appendChild(built.strip);
    }

    // 在照片上左右滑（手指）或左右拖（滑鼠）切換上一張／下一張；箭頭一直都在，這是另一種做法。
    if (multi) {
      img.draggable = false;
      attachSwipe(stage, function (d) { show(s.index + d); }, { ignore: ".pmlb-btn, .pmlb-arrow", follow: frame });
    }

    // 點照片、按鈕、箭頭、縮圖以外的地方關閉
    root.addEventListener("click", function (e) {
      var t = e.target;
      if (t === root || t === stage || t === frame || (s.strip && t === s.strip)) close();
    });
    closeBtn.addEventListener("click", function (e) { e.stopPropagation(); close(); });
    dl.addEventListener("click", function (e) {
      e.stopPropagation();
      var d = s.items[s.index].download;
      if (d && d.onClick) { e.preventDefault(); d.onClick(e); }
    });

    s.onKey = function (e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
      else if (multi && e.key === "ArrowLeft") { e.preventDefault(); show(s.index - 1); slideIn(frame, -1); }
      else if (multi && e.key === "ArrowRight") { e.preventDefault(); show(s.index + 1); slideIn(frame, 1); }
    };
    document.addEventListener("keydown", s.onKey, true);

    state = s;
    document.body.appendChild(root);
    show(Math.max(0, Math.min(items.length - 1, opts.index || 0)));
    if (s.strip) s.strip.classList.toggle("is-short", s.strip.scrollWidth <= s.strip.clientWidth);
    closeBtn.focus({ preventScroll: true });
  }

  window.PMLightbox = { open: open, buildStrip: buildStrip, attachSwipe: attachSwipe, markCurrent: markCurrent, close: function () { close(); }, isOpen: function () { return !!state; } };
})();
