// Demo mode: answers every /api/* call in the browser, so the UI can be shown (e.g. on Vercel)
// without a PhotoAgent backend. Nothing here talks to a server: sessions live in memory, an
// "analysis" is a canned sample, and a "retouch" is a colour tweak done with a canvas.
//
// On when: the page is on *.vercel.app, or the URL has ?demo=1 (remembered in localStorage);
// ?demo=0 turns it off again. Shared by the Chat page (/chat/) and the Editor page (/).
(function () {
  "use strict";
  var qs = new URLSearchParams(location.search);
  var flag = qs.get("demo");
  var KEY = "photomaster-demo";
  var stored = null;
  try { stored = localStorage.getItem(KEY); } catch (e) { /* private mode */ }
  try {
    if (flag === "1") localStorage.setItem(KEY, "1");
    if (flag === "0") localStorage.removeItem(KEY);
  } catch (e) { /* ignore */ }
  var on = flag === "1" || (flag !== "0" && (stored === "1" || /\.vercel\.app$/.test(location.hostname)));

  var IS_CHAT = /\/chat(\/|$)/.test(location.pathname);
  var ROOT = IS_CHAT ? location.pathname.replace(/\/chat(\/.*)?$/, "") : location.pathname.replace(/\/[^/]*$/, "");
  var api = { on: on, root: ROOT };
  window.PhotoAgentDemo = api;
  if (!on) return;

  // The first page opened in a tab is the Chat home (the one with the A/B switch), not the Editor's upload
  // screen: the root URL hands over to /chat/ once. Moving between Chat and Editor afterwards is untouched.
  var VISITED = "pm-demo-visited";
  var visited = false;
  try { visited = sessionStorage.getItem(VISITED) === "1"; } catch (e) { /* storage blocked */ }
  try { sessionStorage.setItem(VISITED, "1"); } catch (e) { /* storage blocked */ }
  if (!IS_CHAT && !visited && !location.hash.replace("#", "")) {
    location.replace(ROOT + "/chat/" + location.search);
    return;
  }

  var DATA = window.__DEMO_DATA__ || {};
  var origFetch = window.fetch.bind(window);

  // ── helpers ────────────────────────────────────────────────────────────
  function hex(n) {
    var s = "";
    while (s.length < n) s += Math.random().toString(16).slice(2);
    return s.slice(0, n);
  }
  function stamp() {
    var d = new Date();
    var p = function (v) { return String(v).padStart(2, "0"); };
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "_" + p(d.getHours()) + "_" + p(d.getMinutes()) + "_" + p(d.getSeconds());
  }
  function jobId() { return stamp() + "_" + hex(8) + "-" + hex(4) + "-" + hex(4) + "-" + hex(4) + "-" + hex(12); }
  function now() { return Date.now() / 1000; }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function J(obj, status) {
    return new Response(JSON.stringify(obj), { status: status || 200, headers: { "Content-Type": "application/json" } });
  }
  function fail(msg, status) { return J({ error: msg }, status || 400); }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function langOf(init, input) {
    var h = (init && init.headers) || (input && input.headers);
    var v = "";
    if (h) v = h.get ? h.get("X-PhotoAgent-Lang") : h["X-PhotoAgent-Lang"];
    return v || "en";
  }
  var isEn = function (lang) { return !lang || /^en/i.test(lang); };
  function pick(lang, en, zh) { return isEn(lang) ? en : zh; }

  // ── state ──────────────────────────────────────────────────────────────
  var S = {
    sessions: {},       // session_id -> session (with .messages, ._pending)
    blobs: {},          // source/preview job id -> Blob (the photo as uploaded)
    urls: {},           // "<kind>:<id>" -> object URL
    results: {},        // result job id -> { blob, source }
    previews: {},       // preview job id -> { blob, name }
    overviews: {},      // batch id -> overview
  };

  function objectUrl(key, blob) {
    if (!S.urls[key]) S.urls[key] = URL.createObjectURL(blob);
    return S.urls[key];
  }

  // ── image URLs (<img src> never goes through fetch, so the page asks here) ──
  api.imageUrl = function (kind, id, run) {
    if (kind === "demo" || kind === "demoThumb") return ROOT + "/assets/demo-images/" + encodeURIComponent(id);
    if (kind === "preview") { var pv = S.previews[id]; return pv ? objectUrl("p:" + id, pv.blob) : ""; }
    if (kind === "source" || kind === "sourceThumb" || kind === "preEdit") {
      var b = S.blobs[id]; return b ? objectUrl("s:" + id, b) : "";
    }
    // result / resultThumb / resultPreview
    var r = S.results[id];
    if (r) return objectUrl("r:" + id, r.blob);
    var src = S.blobs[id];
    return src ? objectUrl("s:" + id, src) : "";
  };

  // ── the "retouch": a gentle colour lift done on a canvas ────────────────────
  function retouch(blob, variant) {
    return createImageBitmap(blob).then(function (bmp) {
      var max = 1600;
      var k = Math.min(1, max / Math.max(bmp.width, bmp.height));
      var w = Math.max(1, Math.round(bmp.width * k));
      var h = Math.max(1, Math.round(bmp.height * k));
      var c = document.createElement("canvas");
      c.width = w; c.height = h;
      var x = c.getContext("2d");
      x.drawImage(bmp, 0, 0, w, h);
      var img = x.getImageData(0, 0, w, h);
      var d = img.data;
      var bright = 1.05 + 0.02 * (variant % 3);
      var contrast = 1.06;
      var sat = 1.16 + 0.04 * (variant % 3);
      var warm = variant % 2 === 0 ? 1.02 : 0.99;
      for (var i = 0; i < d.length; i += 4) {
        var r = d[i] * bright * warm, g = d[i + 1] * bright, b = d[i + 2] * bright / warm;
        r = (r - 128) * contrast + 128; g = (g - 128) * contrast + 128; b = (b - 128) * contrast + 128;
        var l = 0.299 * r + 0.587 * g + 0.114 * b;
        r = l + (r - l) * sat; g = l + (g - l) * sat; b = l + (b - l) * sat;
        d[i] = r < 0 ? 0 : r > 255 ? 255 : r;
        d[i + 1] = g < 0 ? 0 : g > 255 ? 255 : g;
        d[i + 2] = b < 0 ? 0 : b > 255 ? 255 : b;
      }
      x.putImageData(img, 0, 0);
      return new Promise(function (res) { c.toBlob(function (o) { res(o || blob); }, "image/jpeg", 0.9); });
    }).catch(function () { return blob; });
  }

  // ── sample content ─────────────────────────────────────────────────────
  var DEFAULTS = {
    advanced_options: { calm_hair: false, eye_closed_detection: true, gaze_contact_detection: true, hair_mess_detection: true, smile_adjustment: true, smile_level: "auto" },
    analysis_model: "gpt-5.6-luna", aspect_ratio: "auto", detailed_explanation: true, output_scale: "2K", overwrite_prompt: false,
    preset: "natural", render_option: "gpt-image-2.5-sunburst:medium", run_count: 1, style_options: {}, user_prompt: ""
  };
  function planFor() {
    var p = clone(DEFAULTS);
    p.body_reshape_enabled = null; p.face_issue_overrides = {}; p.face_requests = {}; p.pose_face_changes = null; p.prompt_faces = []; p.recipe_checked = [];
    return p;
  }
  function peopleOf(name) { return /couple|friends|picnic|pair/i.test(name || "") ? 2 : 1; }
  function analysisFor(lang, people) {
    var key = "analysis_" + (isEn(lang) ? "en" : "zh-Hans") + "_" + (people >= 2 ? 2 : 1);
    var a = clone(DATA[key] || DATA["analysis_en_1"] || {});
    var n = (a.correction_plan || []).length;
    var ran = []; for (var i = 0; i < n; i++) ran.push(true);
    a.executed = { recipe: ran, style: {}, face: { blink_issue: false, gaze_issue: true, hair_issue: false, smile_issue: false } };
    return a;
  }
  function replyFor(lang, text) {
    return pick(lang,
      "Got it — in the full app I would apply this to your photo. This is a demo preview, so try “One-Tap Retouch” to see how a result looks.",
      "收到：正式版會把這個需求套用到照片上。這是展示用的預覽版，可以按「一鍵美化」看看結果的樣子。");
  }

  // ── sessions ───────────────────────────────────────────────────────────
  function msg(role, card, text) {
    return { card: card, created_at: now(), id: "m_" + hex(32), role: role, text: text || "" };
  }
  function newSession(user, batchId, lang) {
    var id = "s_" + hex(32);
    var s = { active_photo_id: null, batch_id: batchId || null, created_at: now(), defaults: clone(DEFAULTS), lang: lang, photo_order: [], photos: [], session_id: id, updated_at: now(), user: user || "demo", messages: [], _pending: [] };
    S.sessions[id] = s;
    return s;
  }
  function publicSession(s, withMessages, limit) {
    var o = {};
    Object.keys(s).forEach(function (k) { if (k !== "messages" && k !== "_pending") o[k] = s[k]; });
    o.photos = s.photos.map(publicPhoto);
    if (withMessages) o.messages = clone(s.messages.slice(-(limit || 500)));
    return o;
  }
  function publicPhoto(p) {
    var o = {};
    Object.keys(p).forEach(function (k) { if (k.charAt(0) !== "_") o[k] = p[k]; });
    return o;
  }
  function flush(s) {
    var out = s._pending.splice(0);
    out.forEach(function (m) { s.messages.push(m); });
    if (out.length) s.updated_at = now();
    return out;
  }
  function find(sid) { return S.sessions[sid] || null; }
  function photoOf(s, pid) { return s.photos.filter(function (p) { return p.photo_id === pid; })[0] || null; }

  function addPhotos(s, files, lang, previewId) {
    var added = [];
    files.forEach(function (f) {
      var pid = "p_" + hex(12);
      var job = jobId();
      var sha = hex(64);
      S.blobs[job] = f.blob;
      var photo = {
        active_version: null, analysis_skipped: false, analyze_job_id: job, case_id: "demo/photoAgent/input/" + job + "/src.jpg", error: null,
        filename: f.name || "photo.jpg", pending_brief: null, pending_note: null, pending_render: null, photo_id: pid, plan: planFor(), planned_ratio: "auto",
        queued_render: null, source_sha256: sha, src_keys: ["demo/photoAgent/input/" + job + "/src.jpg"], status: "analyzing", _people: peopleOf(f.name)
      };
      s.photos.push(photo); s.photo_order.push(pid);
      if (!s.active_photo_id) s.active_photo_id = pid;
      added.push(photo);
    });
    s.messages.push(msg("user", { photo_ids: added.map(function (p) { return p.photo_id; }), type: "photos_added" }));
    // the "analysis" lands a moment later, as the worker would deliver it
    added.forEach(function (photo, i) {
      setTimeout(function () {
        var a = analysisFor(lang, photo._people);
        if (photo.status === "analyzing") photo.status = "ready";   // a retouch asked for meanwhile keeps the photo "rendering"
        s._pending.push(msg("assistant", { photo_id: photo.photo_id, type: "analysis_ready" }));
        s._pending.push(msg("assistant", { issues: (a.detected_issues || []).slice(0, 3), photo_id: photo.photo_id, summary: a.photo_summary || "", type: "analysis_brief" }));
        s.updated_at = now();
      }, 1400 + i * 500);
    });
    s.updated_at = now();
    return added;
  }

  function renderPhotos(s, photoIds, lang, base) {
    var renders = [];
    photoIds.forEach(function (pid, i) {
      var photo = photoOf(s, pid);
      if (!photo) return;
      var job = jobId();
      photo.status = "rendering";
      photo.pending_render = { job_ids: [job] };
      renders.push({ action_id: job, job_ids: [job], photo_id: pid });
      var variant = s.messages.filter(function (m) { return m.card && m.card.type === "render_done" && m.card.photo_id === pid; }).length + 1;
      var source = S.blobs[photo.analyze_job_id];
      // sometimes the previous result is the base: just retouch whatever we hold
      retouch(source, variant).then(function (blob) {
        S.results[job] = { blob: blob, source: photo.analyze_job_id };
        return wait(3500 + i * 600);
      }).then(function () {
        photo.status = "ready";
        photo.pending_render = null;
        photo.active_version = { at: now(), job_id: job, run: 1, version_id: job };
        s._pending.push(msg("assistant", { failed_job_ids: [], job_ids: [job], photo_id: pid, plan: clone(photo.plan), type: "render_done", version_id: job }));
        s.updated_at = now();
      });
    });
    s.messages.push(msg("user", { base: base || "original", queued: [], renders: renders, type: "render_requested" }));
    s.updated_at = now();
    return renders;
  }

  function summary(s) {
    var rendered = s.photos.filter(function (p) { return !!p.active_version; });
    var first = s.photos[0];
    var thumb = rendered[0];
    var st = "ready";
    if (s.photos.some(function (p) { return p.status === "rendering"; })) st = "rendering";
    else if (s.photos.some(function (p) { return p.status === "analyzing"; })) st = "analyzing";
    else if (rendered.length) st = "done";
    return {
      batch_id: s.batch_id, case_ids: s.photos.map(function (p) { return p.case_id; }), created_at: s.created_at, photo_count: s.photos.length,
      rendered_count: rendered.length, session_id: s.session_id, status: st, thumbnail_job_id: first ? first.analyze_job_id : null,
      thumbnail_result: thumb ? { job_id: thumb.active_version.job_id, run: 1 } : null, thumbnails: [], updated_at: s.updated_at
    };
  }

  // ── request bodies ─────────────────────────────────────────────────────
  function bodyOf(init) {
    var b = init && init.body;
    if (!b) return {};
    if (typeof b === "string") { try { return JSON.parse(b); } catch (e) { return {}; } }
    return {};
  }
  function filesOf(init) {
    var b = init && init.body;
    var out = [];
    if (b && typeof FormData !== "undefined" && b instanceof FormData) {
      b.getAll("image").forEach(function (f) { if (f && f.size != null) out.push({ blob: f, name: f.name }); });
    }
    return out;
  }
  function formOf(init, k) {
    var b = init && init.body;
    return b && typeof FormData !== "undefined" && b instanceof FormData ? b.get(k) : null;
  }

  // ── router ─────────────────────────────────────────────────────────────
  function previewStatus(id, name) {
    var two = peopleOf(name) === 2;
    function person(i, x) {
      return { box: { x: x, y: 0.1, w: two ? 0.3 : 0.5, h: 0.8 }, detection_index: null, face_box: null, face_readable: null, label: pickLabel(i), preview_person_id: "pp_" + id.slice(-6) + i,
        role: "included", sent_to_model: true, source: "full_image", thumbnail_box: { x: x + 0.02, y: 0.1, w: two ? 0.26 : 0.46, h: 0.4 } };
    }
    function pickLabel(i) { return two ? (i === 0 ? "Person on the left" : "Person on the right") : "Person in the photo"; }
    var people = two ? [person(0, 0.15), person(1, 0.55)] : [person(0, 0.25)];
    return { augmentation_state: "unavailable", completed_at: now(), faces: [], format_version: 2, low_confidence_state: "disabled", people: people,
      preview_job_id: id, reusable: true, source_sha256: hex(64), state: "ready", status: "success" };
  }

  function handle(method, path, params, init, input) {
    var lang = langOf(init, input);
    var m;
    // ---- app basics
    if (path === "/version") return J({ frontend: "demo", worker: null });
    if (path === "/capabilities") return J(DATA["caps_" + (isEn(lang) ? "en" : "zh-Hans")] || DATA.caps_en);
    if (path === "/config") return J(DATA.config || {});
    if (path === "/photo-agent/decrypt-user-id") return J({ user: "demo_guest" });
    if (path === "/photo-agent/demo-images") return J({ images: DATA.demo_images || [] });
    if ((m = path.match(/^\/photo-agent\/demo-images\/([^/]+)(\/thumbnail)?$/))) return origFetch(api.imageUrl("demo", decodeURIComponent(m[1])));
    if (path === "/photo-agent/report") return J({ reported: true, report_id: "demo-" + hex(8) });
    if (path === "/photo-agent/detect-faces") return J({ faces: [] });
    if (/^\/photo-agent\/reports\//.test(path)) return fail("forbidden", 403);

    // ---- chat: previews
    if (path === "/photo-agent/chat/previews" && method === "POST") {
      var pf = filesOf(init)[0];
      var pid0 = jobId();
      if (pf) S.previews[pid0] = { blob: pf.blob, name: pf.name };
      return J({ preview_job_id: pid0, provisional: true, source_sha256: hex(64), status: "queued" });
    }
    if ((m = path.match(/^\/photo-agent\/chat\/previews\/([^/]+)$/))) {
      var pv = S.previews[decodeURIComponent(m[1])];
      return pv ? J(previewStatus(decodeURIComponent(m[1]), pv.name)) : fail("Preview not found or expired", 404);
    }
    if ((m = path.match(/^\/photo-agent\/chat\/previews\/([^/]+)\/image$/))) {
      var pv2 = S.previews[decodeURIComponent(m[1])];
      return pv2 ? Promise.resolve(new Response(pv2.blob)) : fail("not found", 404);
    }
    if (path === "/photo-agent/chat/prompt-review") {
      var b0 = bodyOf(init);
      return J({ accepted: true, literal_texts: [b0.text || ""], reply: replyFor(lang, b0.text), verbatim: true, interpreted: false, request_semantics: [] });
    }

    // ---- chat: sessions
    if (path === "/photo-agent/chat/sessions" && method === "POST") {
      var bs = bodyOf(init);
      var created = newSession(bs.user || params.get("user"), bs.batch_id, lang);
      return J(publicSession(created, false));
    }
    if (path === "/photo-agent/chat/sessions" && method === "GET") {
      var list = Object.keys(S.sessions).map(function (k) { return S.sessions[k]; })
        .filter(function (s) { return s.photos.length; })
        .sort(function (a, b) { return b.updated_at - a.updated_at; }).map(summary);
      return J({ sessions: list.slice(0, Number(params.get("limit")) || 30) });
    }
    if (path === "/photo-agent/chat/sessions/delete") {
      var bd = bodyOf(init);
      if (bd.all) { S.sessions = {}; return J({ deleted: "all" }); }
      (bd.rows || []).forEach(function (r) { delete S.sessions[r]; });
      return J({ deleted: bd.rows || [] });
    }
    if (path === "/photo-agent/chat/sessions/from-case") {
      var cid = bodyOf(init).case_id;
      var hit = Object.keys(S.sessions).map(function (k) { return S.sessions[k]; }).filter(function (s) {
        return s.photos.some(function (p) { return p.case_id === cid; });
      })[0];
      return hit ? J({ session_id: hit.session_id, created: false }) : fail("This photo can't be opened in Chat.", 409);
    }
    if ((m = path.match(/^\/photo-agent\/chat\/batches\/([^/]+)\/overview$/))) {
      var bid = decodeURIComponent(m[1]);
      if (method === "PUT") { S.overviews[bid] = bodyOf(init).overview || null; return J({ ok: true }); }
      return J({ overview: S.overviews[bid] || null });
    }
    if ((m = path.match(/^\/photo-agent\/chat\/sessions\/([^/]+)(\/.*)?$/))) {
      var s = find(decodeURIComponent(m[1]));
      if (!s) return fail("Session not found", 404);
      var rest = m[2] || "";
      if (rest === "" && method === "GET") { flush(s); return J(publicSession(s, true, Number(params.get("messages_limit")) || 500)); }
      if (rest === "/status") { var nm = flush(s); return J({ jobs: {}, new_messages: nm, session: publicSession(s, false) }); }
      if (rest === "/photos" && method === "POST") {
        var files = filesOf(init);
        if (!files.length) return fail("no image", 400);
        var added = addPhotos(s, files, lang);
        return J({ errors: [], photos: added.map(publicPhoto) });
      }
      if (rest === "/messages" && method === "POST") {
        var bm = bodyOf(init);
        if (bm.messages) {
          var made = bm.messages.map(function (x) { return msg(x.role, x.card, x.text); });
          made.forEach(function (x) { s.messages.push(x); });
          return J({ messages: made });
        }
        var one = msg(bm.role || "user", bm.card, bm.text);
        s.messages.push(one);
        return J(one);
      }
      if ((m = rest.match(/^\/messages\/([^/]+)$/)) && method === "PUT") {
        var target = s.messages.filter(function (x) { return x.id === decodeURIComponent(m[1]); })[0];
        if (!target) return fail("not found", 404);
        target.card = bodyOf(init).card; return J(target);
      }
      if (rest === "/plan" && method === "PATCH") { s.defaults = Object.assign(s.defaults, bodyOf(init).plan || {}); return J({ plan: s.defaults }); }
      if (rest === "/render" && method === "POST") {
        var br = bodyOf(init);
        var renders = renderPhotos(s, br.photo_ids || [], lang, br.base);
        return J({ queued: [], renders: renders });
      }
      if ((m = rest.match(/^\/photos\/([^/]+)(\/.*)$/))) {
        var photo = photoOf(s, decodeURIComponent(m[1]));
        if (!photo) return fail("Photo not found", 404);
        var sub = m[2];
        if (sub === "/analysis") {
          return J({ status: "success", job_id: photo.analyze_job_id, result: { analysis: analysisFor(lang, photo._people) } });
        }
        if (sub === "/plan" && method === "PATCH") { photo.plan = Object.assign(photo.plan, bodyOf(init).plan || {}); return J({ plan: photo.plan, photo: publicPhoto(photo) }); }
        if (sub === "/analyze" && method === "POST") {
          photo.status = "analyzing"; photo.analysis_skipped = false;
          setTimeout(function () { photo.status = "ready"; s._pending.push(msg("assistant", { photo_id: photo.photo_id, type: "analysis_ready" })); }, 1200);
          return J({ photo: publicPhoto(photo), status: "analyzing" });
        }
        if (sub === "/active-version" && method === "PUT") {
          var v = bodyOf(init); photo.active_version = { at: now(), job_id: v.job_id, run: v.run || 1, version_id: v.version_id }; return J(publicSession(s, true));
        }
        if (sub === "/continuation-draft" && method === "PUT") {
          var cd = bodyOf(init); photo.continuation_draft = { revision: cd.revision || 1, text: cd.text || "", style_options: cd.style_options || {}, aspect_ratio: cd.aspect_ratio || "auto", source_result: cd.source_result };
          return J(publicSession(s, true));
        }
        if (sub === "/ask" && method === "POST") {
          var ba = bodyOf(init);
          var asked = msg("user", { photo_id: photo.photo_id, plan: clone(photo.plan), type: "ask" }, ba.text || "");
          var answered = msg("assistant", { photo_id: photo.photo_id, text: replyFor(lang, ba.text), options: [], type: "llm_reply" });
          if (ba.waiting) { s.messages.push(answered); return J({ messages: [answered] }); }
          s.messages.push(asked); s.messages.push(answered);
          return J({ messages: [asked, answered] });
        }
      }
    }

    // ---- results (chat: the "made with" details of a version)
    if ((m = path.match(/^\/photo-agent\/result\/([^/]+)$/)) && method === "GET") {
      var rid = decodeURIComponent(m[1]);
      var ej = (S.ed || {})[rid];
      var a = analysisFor(lang, ej ? ej.people : 1);
      return J({ status: "success", model: "demo", image_url: api.imageUrl("result", rid), result: { analysis: a, quality_check: {}, usage: {} }, timings: [] });
    }
    if (/^\/photo-agent\/(result|source)\//.test(path)) return origFetch(api.imageUrl(path.indexOf("/source/") > 0 ? "source" : "result", decodeURIComponent(path.split("/")[3] || "")));

    if (api.handleEditor) {
      var viaEditor = api.handleEditor(method, path, params, init, input, lang);
      if (viaEditor) return viaEditor;
    }
    if (window.console) console.warn("[demo] unhandled", method, path);
    return method === "GET" ? J({}) : J({ ok: true });
  }


  // ── Editor page (the older, single-photo API) ──────────────────────────
  // Same behaviour as backend/dev_ui_mock.py: an analysis answers with the sample, a retouch is a
  // job that "runs" for a few seconds and then hands back a colour-tweaked copy of the photo.
  var RETOUCH_SECONDS = 8;
  S.ed = {};
  S.cases = {};
  S.caseOf = {};
  function edNew(blob, name) {
    var id = "mock-" + hex(12);
    S.blobs[id] = blob;
    S.ed[id] = { people: peopleOf(name), caseId: id, born: 0 };
    // History (the Editor's version strip reads it): one photo = one case; the first version is the
    // analysis, every retouch after that adds one.
    S.cases[id] = { image_key: id, created_at: now(), versions: [{ version_id: id, kind: "analyze", created_at: now(), based_on: null, params: { stage: "analyze" }, path: "local", runs: [id], results: [] }] };
    S.caseOf[id] = id;
    return id;
  }
  api.handleEditor = function (method, path, params, init, input, lang) {
    var m;
    if (method === "POST" && path === "/photo-agent/analyze-batch") {
      var ids = filesOf(init).map(function (f) { return { job_id: edNew(f.blob, f.name) }; });
      return J({ jobs: ids });
    }
    if (method === "POST" && path === "/photo-agent/analyze") {
      var f = filesOf(init)[0];
      return J({ job_id: f ? edNew(f.blob, f.name) : "mock-" + hex(12) });
    }
    if (method === "POST" && (/^\/photo-agent\/continue\//.test(path) || /^\/photo-agent\/analyze-and-finish\//.test(path) ||
        path === "/photo-agent/continue-version" || path === "/photo-agent/rerun")) {
      var body = bodyOf(init);
      var hay = path + " " + JSON.stringify(body);
      var src = Object.keys(S.ed).filter(function (k) { return hay.indexOf(k) >= 0; }).pop();
      var nid = "mock-" + hex(12);
      var base = src ? S.ed[src] : { people: 1, caseId: nid };
      S.ed[nid] = { people: base.people, caseId: base.caseId, born: Date.now(), source: src };
      var kase = S.cases[S.caseOf[src] || base.caseId];
      if (kase) {
        var prev = kase.versions[kase.versions.length - 1];
        kase.versions.push({ version_id: nid, kind: "finish", created_at: now(), based_on: prev ? prev.version_id : null,
          params: { stage: "finish", user_prompt: body.user_prompt || "", run_count: 1 }, path: "local", runs: [nid], results: [{ job_id: nid, run: 1 }] });
        S.caseOf[nid] = kase.image_key;
      }
      if (src && S.blobs[src]) {
        S.blobs[nid] = S.blobs[src];
        var variant = Object.keys(S.ed).filter(function (k) { return S.ed[k].caseId === base.caseId && S.ed[k].source; }).length;
        retouch(S.results[src] ? S.results[src].blob : S.blobs[src], variant).then(function (b) { S.results[nid] = { blob: b }; });
      }
      return J({ job_id: nid }, 202);
    }
    // History: the Editor's version strip and "retouch again" read it. Demo history lives in memory only.
    if ((m = path.match(/^\/photo-agent\/history\/([^/]+)(\/case|\/all)?$/))) {
      var user = decodeURIComponent(m[1]);
      if (m[2] === "/case") {
        var key = params.get("image_key") || S.caseOf[params.get("job_id")];
        var kc = S.cases[key];
        return kc ? J({ user: user, case: { image_key: kc.image_key, created_at: kc.created_at, versions: clone(kc.versions) } }) : fail("not found", 404);
      }
      if (method === "GET") {
        var jobs = Object.keys(S.cases).map(function (k2) { return S.cases[k2]; })
          .filter(function (c) { return c.versions.some(function (v) { return v.kind !== "analyze"; }); })
          .sort(function (a, b) { return b.created_at - a.created_at; })
          .map(function (c) { var l = c.versions[c.versions.length - 1]; return { job_id: l.runs[0], path: "local", ts: c.created_at, preset: "natural", image_key: c.image_key, runs: l.runs }; });
        return J({ user: user, jobs: jobs, has_more: false });
      }
      return J({ ok: true });
    }
    if ((m = path.match(/^\/photo-agent\/status\/([^/]+)$/))) {
      var j = S.ed[decodeURIComponent(m[1])];
      if (j && j.born && Date.now() - j.born < RETOUCH_SECONDS * 1000) return J({ status: "processing" });
      return J({ status: "success" });
    }
    if ((m = path.match(/^\/photo-agent\/analyze\/result\/([^/]+)$/))) {
      var aj = S.ed[decodeURIComponent(m[1])];
      return J({ status: "success", result: { analysis: analysisFor(lang, aj ? aj.people : 1), compatible: true } });
    }
    return null;
  };

  // ── install ────────────────────────────────────────────────────────────
  api.helpers = { J: J, fail: fail, hex: hex, jobId: jobId, now: now, clone: clone, wait: wait, retouch: retouch, analysisFor: analysisFor, peopleOf: peopleOf, S: S, objectUrl: objectUrl, isEn: isEn, bodyOf: bodyOf, filesOf: filesOf, formOf: formOf, DATA: DATA };
  window.fetch = function (input, init) {
    var url;
    try { url = new URL(typeof input === "string" ? input : input.url, location.href); } catch (e) { return origFetch(input, init); }
    var i = url.pathname.indexOf("/api/");
    if (i < 0 || url.origin !== location.origin) return origFetch(input, init);
    var method = String((init && init.method) || (typeof input !== "string" && input.method) || "GET").toUpperCase();
    return wait(60 + Math.random() * 120).then(function () {
      return handle(method, url.pathname.slice(i + 4), url.searchParams, init, typeof input === "string" ? null : input);
    });
  };
})();
