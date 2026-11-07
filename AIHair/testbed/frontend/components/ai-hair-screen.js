/**
 * aiHair screen — upload a photo, pick one of aiHair's nine features, 套用.
 *
 *   ┌──────────────────────────────────────────┬──────────────┐
 *   │ [毛躁修護][髮量豐盈] … [碎髮修整]           │ 編輯紀錄       │
 *   │ 強度 [Lv1][Lv2]            (hairline only) │  0 原圖        │
 *   │ [↶復原][↷重做] [◐原圖]  [✕取消編輯][⬇][⟳] │  1 毛躁修護    │
 *   │ ┌──────────────────────────────────────┐ │  2 柔順 ◀      │
 *   │ │                image                 │ ├──────────────┤
 *   │ └──────────────────────────────────────┘ │ 目前步驟       │
 *   │                [ 套用 ]                   │  Task ID …     │
 *   │              status line                  │  [回報問題]    │
 *   └──────────────────────────────────────────┴──────────────┘
 *
 * The edit history is the model. Every 套用 runs the selected feature on the
 * step the history is at and pushes the result as a new step (the steps after
 * it — the redo branch — are dropped), so edits chain. 復原 / 重做 (also
 * Ctrl/⌘+Z, Ctrl/⌘+Shift+Z, Ctrl+Y) move along it; clicking a step in 編輯紀錄
 * jumps to it; 原圖 shows the uploaded photo (hold to peek, click to toggle);
 * 取消編輯 drops every step back to the original photo.
 */

import {
  FEATURES,
  getFeature,
  applyHairFeature,
  toEngineImage,
  listSamples,
} from '../api/ai-hair-api.js';
import { showFeedbackDialog } from './dialogs/feedback-dialog.js';
import { showConfirmDialog } from './dialogs/confirmation-dialogs.js';
import { IconUndo, IconRedo, IconDownload, IconX, IconReplace, IconUpload } from '../utils/icons.js';

const PAGE = 'ai-hair';
// A press on 原圖 shorter than this is a click (toggle); longer is a peek.
const PEEK_HOLD_MS = 300;

const STAGE_LABELS = {
  preparing:   '準備中…',
  uploading:   '上傳照片中…',
  queued:      '排隊中…',
  processing:  '處理中…',
  downloading: '下載結果中…',
};

// ─── State ─────────────────────────────────────────────────────────────────

/**
 * @typedef {Object} Step
 * @property {Blob}   blob
 * @property {string} url        object URL of blob
 * @property {number} width
 * @property {number} height
 * @property {string|null} feature   null for the original photo
 * @property {number|null} level
 * @property {string|null} taskId
 * @property {number|null} durationMs
 * @property {string|null} remoteUrl AIPoC's URL of the result, for debugging
 */

const state = {
  fileName:  '',
  /** @type {Step[]} */
  history:   [],
  cursor:    -1,
  selected:  null,   // feature name
  levels:    Object.fromEntries(FEATURES.filter((f) => f.levels).map((f) => [f.name, f.levels[0]])),
  // The in-flight run: { abort, feature, level, startedAt, taskId, timer }
  run:       null,
  compare:   false,  // showing the original photo instead of the current step
  loadingInput: false,
  // The status line under 套用: { text, kind: 'hint'|'ok'|'notice'|'error' }
  status:    null,
  // Task of a run that left no step (failed / skipped / cancelled), so
  // 回報問題 can still report it while its message is showing.
  statusTaskId: null,
};

let $root = null;
const $ = (sel) => $root.querySelector(sel);

// ─── Mount ─────────────────────────────────────────────────────────────────

export function createAiHairScreen(container) {
  container.innerHTML = '';
  $root = document.createElement('div');
  $root.className = 'ah-screen';
  $root.innerHTML = SCREEN_STYLE + SCREEN_HTML;
  container.appendChild($root);

  buildFeatureChips();
  wireUpload();
  wireToolbar();
  wireCompare();
  wireApply();
  wireShortcuts();
  wireFeedback();

  window.addEventListener('beforeunload', (e) => {
    if (state.history.length > 1 || state.run) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  loadSamples();
  render();
}

// ─── Static markup ─────────────────────────────────────────────────────────

const IconCompare = (cls) =>
  `<svg class="${cls}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18"/><path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7z" fill="currentColor" fill-opacity="0.35"/></svg>`;

const SCREEN_STYLE = `
<style>
  .ah-screen {
    flex: 1;
    display: flex; flex-direction: column;
    padding: 1.25rem 1.5rem;
    min-height: 0;
  }
  .ah-screen [hidden] { display: none !important; }
  @media (max-width: 600px) {
    .ah-screen { padding: 1rem; }
    .ah-card { padding: 0.875rem 1rem; }
  }

  .ah-card {
    background: var(--color-card);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-xl);
    padding: 1rem 1.25rem;
    display: flex; flex-direction: column; gap: 0.75rem;
    min-width: 0;
  }
  .ah-card-title {
    font-size: 0.75rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--color-muted-foreground);
  }

  /* ── Upload view ─────────────────────────────────────────────── */
  .ah-upload {
    width: 100%; max-width: 760px; margin: 2rem auto;
    display: flex; flex-direction: column; gap: 1rem;
  }
  .ah-dropzone {
    border: 2px dashed var(--color-border);
    border-radius: var(--radius-lg);
    padding: 3rem 1.5rem;
    display: flex; flex-direction: column; align-items: center; gap: 0.5rem;
    text-align: center; cursor: pointer;
    transition: border-color .15s, background-color .15s;
  }
  .ah-dropzone:hover, .ah-dropzone:focus-visible, .ah-dropzone.drag-over {
    border-color: var(--color-ring); background: var(--color-muted); outline: none;
  }
  .ah-dropzone.loading { cursor: progress; opacity: 0.7; }
  .ah-dropzone .icon-lg { color: var(--color-muted-foreground); width: 2.5rem; height: 2.5rem; }
  .ah-dropzone-title { font-size: 1rem; font-weight: 500; }
  .ah-dropzone-sub { font-size: 0.8125rem; color: var(--color-muted-foreground); }
  .ah-upload-msg { font-size: 0.875rem; min-height: 1.25rem; text-align: center; color: var(--color-muted-foreground); }
  .ah-upload-msg.error { color: var(--color-destructive); }

  .ah-samples-strip { display: flex; gap: 0.5rem; overflow-x: auto; padding-bottom: 0.5rem; }
  .ah-sample {
    flex: 0 0 auto; width: 6rem; height: 6rem; padding: 0;
    border-radius: var(--radius-md); border: 2px solid transparent;
    overflow: hidden; cursor: pointer; background: var(--color-muted);
  }
  .ah-sample img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .ah-sample:hover, .ah-sample:focus-visible { border-color: var(--color-ring); outline: none; }

  /* ── Editor view ─────────────────────────────────────────────── */
  .ah-editor {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 18rem;
    gap: 1rem;
    align-items: start;
  }
  @media (max-width: 1000px) {
    .ah-editor { grid-template-columns: minmax(0, 1fr); }
  }
  .ah-side { display: flex; flex-direction: column; gap: 1rem; min-width: 0; }

  .ah-features { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .ah-chip {
    display: inline-flex; flex-direction: column; align-items: center; gap: 0.125rem;
    padding: 0.4375rem 0.875rem;
    border: 1px solid var(--color-input); border-radius: 999px;
    background: var(--color-background); color: var(--color-foreground);
    cursor: pointer; line-height: 1.15;
    transition: background .12s, border-color .12s, color .12s;
  }
  .ah-chip:hover { border-color: var(--color-ring); background: var(--color-muted); }
  .ah-chip:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 1px; }
  .ah-chip-zh { font-size: 0.875rem; font-weight: 600; }
  .ah-chip-en { font-size: 0.6875rem; color: var(--color-muted-foreground); font-family: ui-monospace, monospace; }
  .ah-chip[aria-checked="true"] {
    background: var(--color-primary); color: var(--color-primary-foreground);
    border-color: var(--color-primary);
  }
  .ah-chip[aria-checked="true"] .ah-chip-en { color: inherit; opacity: 0.75; }

  .ah-level-row { display: flex; align-items: center; gap: 0.5rem; font-size: 0.8125rem; }
  .ah-level-row > span { color: var(--color-muted-foreground); }
  .ah-seg {
    display: inline-flex; padding: 0.1875rem; gap: 0.125rem;
    background: var(--color-background);
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
  }
  .ah-seg button {
    appearance: none; border: none; cursor: pointer;
    background: transparent; color: var(--color-foreground);
    padding: 0.25rem 0.75rem; border-radius: calc(var(--radius-md) - 3px);
    font-size: 0.8125rem; font-weight: 500;
  }
  .ah-seg button:hover:not([aria-checked="true"]) { background: var(--color-muted); }
  .ah-seg button[aria-checked="true"] { background: var(--color-primary); color: var(--color-primary-foreground); }

  .ah-toolbar { display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap; }
  .ah-toolbar-sep { width: 1px; height: 1.5rem; background: var(--color-border); margin: 0 0.25rem; }
  .ah-toolbar-spacer { flex: 1; }
  .ah-tool {
    display: inline-flex; align-items: center; gap: 0.375rem;
    height: 2.25rem; padding: 0 0.75rem;
    border: 1px solid var(--color-input); border-radius: var(--radius-md);
    background: var(--color-background); color: var(--color-foreground);
    font-size: 0.8125rem; font-weight: 500; cursor: pointer;
    transition: background .12s, color .12s, opacity .12s;
    user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
  }
  .ah-tool:hover:not(:disabled) { background: var(--color-accent); color: var(--color-accent-foreground); }
  .ah-tool:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 1px; }
  .ah-tool:disabled { opacity: 0.45; cursor: not-allowed; }
  .ah-tool[aria-pressed="true"] { background: #f59e0b; border-color: #f59e0b; color: #111; }
  #ah-compare-btn { touch-action: none; }

  .ah-stage {
    position: relative;
    height: min(64vh, 820px); min-height: 18rem;
    background: #111; border-radius: var(--radius-lg);
    overflow: hidden;
  }
  .ah-stage img {
    width: 100%; height: 100%; object-fit: contain; display: block;
    user-select: none; -webkit-user-drag: none;
  }
  .ah-badge {
    position: absolute; left: 0.75rem; top: 0.75rem;
    padding: 0.25rem 0.625rem; border-radius: 999px;
    background: rgba(0, 0, 0, 0.6); color: #fff;
    font-size: 0.75rem; font-weight: 500; pointer-events: none;
  }
  .ah-stage.comparing .ah-badge { background: #f59e0b; color: #111; }

  .ah-busy {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.5rem;
    background: rgba(0, 0, 0, 0.6); color: #fff; text-align: center; padding: 1rem;
  }
  .ah-busy-title { font-size: 1rem; font-weight: 600; }
  .ah-busy-stage { font-size: 0.875rem; }
  .ah-busy-task { font-size: 0.6875rem; font-family: ui-monospace, monospace; opacity: 0.75; word-break: break-all; max-width: 90%; }
  .ah-busy .btn { margin-top: 0.5rem; }
  @keyframes ah-spin { to { transform: rotate(360deg); } }
  .ah-spinner {
    width: 2rem; height: 2rem; border-radius: 50%;
    border: 3px solid rgba(255, 255, 255, 0.35); border-top-color: #fff;
    animation: ah-spin 0.7s linear infinite;
  }

  .ah-apply-row { display: flex; flex-direction: column; align-items: center; gap: 0.5rem; }
  #ah-apply-btn { min-width: 12rem; height: 2.75rem; padding: 0 2rem; font-size: 1rem; }
  .ah-status { font-size: 0.875rem; text-align: center; min-height: 1.25rem; word-break: break-word; }
  .ah-status.hint   { color: var(--color-muted-foreground); }
  .ah-status.ok     { color: #16a34a; }
  .ah-status.notice { color: #d97706; }
  .ah-status.error  { color: var(--color-destructive); }
  .dark .ah-status.ok     { color: #4ade80; }
  .dark .ah-status.notice { color: #fbbf24; }

  /* Vivid primary button (套用), colourful in both themes. */
  .ah-btn-vivid {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    color: #fff; border: none;
    box-shadow: 0 2px 8px rgba(99, 102, 241, 0.35);
  }
  .ah-btn-vivid:hover:not(:disabled) {
    background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.5);
  }
  .dark .ah-btn-vivid { background: linear-gradient(135deg, #818cf8 0%, #a78bfa 100%); }

  /* ── Side panel ──────────────────────────────────────────────── */
  .ah-history-list { list-style: none; display: flex; flex-direction: column; gap: 0.25rem; max-height: 22rem; overflow-y: auto; }
  .ah-history-item {
    width: 100%; display: flex; align-items: center; gap: 0.625rem;
    padding: 0.375rem; border: 1px solid transparent; border-radius: var(--radius-md);
    background: transparent; color: var(--color-foreground); text-align: left; cursor: pointer;
  }
  .ah-history-item:hover:not(:disabled) { background: var(--color-muted); }
  .ah-history-item:focus-visible { outline: 2px solid var(--color-ring); outline-offset: -2px; }
  .ah-history-item:disabled { cursor: not-allowed; }
  .ah-history-item.current { border-color: var(--color-ring); background: var(--color-muted); }
  .ah-history-item.future { opacity: 0.45; }
  .ah-history-item img { width: 2.5rem; height: 2.5rem; object-fit: cover; border-radius: var(--radius-sm); background: #111; flex: 0 0 auto; }
  .ah-history-text { display: flex; flex-direction: column; min-width: 0; }
  .ah-history-name { font-size: 0.8125rem; font-weight: 500; }
  .ah-history-meta { font-size: 0.6875rem; color: var(--color-muted-foreground); }

  .ah-info { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 0.25rem 0.75rem; font-size: 0.8125rem; }
  .ah-info dt { color: var(--color-muted-foreground); }
  .ah-info dd { word-break: break-all; }
  .ah-info .mono { font-family: ui-monospace, monospace; font-size: 0.75rem; user-select: all; }
  .ah-info a { color: inherit; }
</style>
`;

const SCREEN_HTML = `
  <!-- ── Upload view ─────────────────────────────────────────────── -->
  <section class="ah-upload" id="ah-upload">
    <div class="ah-dropzone" id="ah-dropzone" tabindex="0" role="button" aria-label="上傳照片">
      ${IconUpload('icon-lg')}
      <span class="ah-dropzone-title">拖曳照片到這裡，或點擊選擇照片</span>
      <span class="ah-dropzone-sub">JPG、PNG、WebP（其他格式會先轉成 JPG）</span>
    </div>
    <input type="file" id="ah-file-input" accept="image/*" hidden />
    <div class="ah-upload-msg" id="ah-upload-msg" role="status"></div>

    <div class="ah-card" id="ah-samples" hidden>
      <div class="ah-card-title">範例照片</div>
      <div class="ah-samples-strip" id="ah-samples-strip"></div>
    </div>
  </section>

  <!-- ── Editor view ─────────────────────────────────────────────── -->
  <section class="ah-editor" id="ah-editor" hidden>
    <div class="ah-card ah-main">
      <div class="ah-features" id="ah-features" role="radiogroup" aria-label="髮型功能"></div>
      <div class="ah-level-row" id="ah-level-row" hidden>
        <span>強度</span>
        <div class="ah-seg" id="ah-level-seg" role="radiogroup" aria-label="強度"></div>
      </div>

      <div class="ah-toolbar" role="toolbar" aria-label="編輯工具">
        <button class="ah-tool" id="ah-undo-btn" type="button" title="復原（Ctrl/⌘+Z）">${IconUndo('icon-sm')}<span>復原</span></button>
        <button class="ah-tool" id="ah-redo-btn" type="button" title="重做（Ctrl/⌘+Shift+Z）">${IconRedo('icon-sm')}<span>重做</span></button>
        <span class="ah-toolbar-sep" aria-hidden="true"></span>
        <button class="ah-tool" id="ah-compare-btn" type="button" aria-pressed="false"
                title="按住檢視原圖，點一下切換原圖／目前結果">${IconCompare('icon-sm')}<span>原圖</span></button>
        <span class="ah-toolbar-spacer"></span>
        <button class="ah-tool" id="ah-discard-btn" type="button" title="捨棄所有編輯，回到原圖">${IconX('icon-sm')}<span>取消編輯</span></button>
        <button class="ah-tool" id="ah-download-btn" type="button" title="下載目前的圖片">${IconDownload('icon-sm')}<span>下載</span></button>
        <button class="ah-tool" id="ah-change-btn" type="button" title="換一張照片">${IconReplace('icon-sm')}<span>換照片</span></button>
      </div>

      <div class="ah-stage" id="ah-stage">
        <img id="ah-image" alt="" draggable="false" />
        <span class="ah-badge" id="ah-badge"></span>
        <div class="ah-busy" id="ah-busy" hidden role="status" aria-live="polite">
          <div class="ah-spinner" aria-hidden="true"></div>
          <div class="ah-busy-title" id="ah-busy-title"></div>
          <div class="ah-busy-stage" id="ah-busy-stage"></div>
          <div class="ah-busy-task" id="ah-busy-task"></div>
          <button class="btn btn-outline btn-sm" id="ah-cancel-run-btn" type="button">取消</button>
        </div>
      </div>

      <div class="ah-apply-row">
        <button class="btn ah-btn-vivid" id="ah-apply-btn" type="button" disabled>套用</button>
        <div class="ah-status" id="ah-status" role="status" aria-live="polite"></div>
      </div>
    </div>

    <aside class="ah-side">
      <div class="ah-card">
        <div class="ah-card-title">編輯紀錄</div>
        <ol class="ah-history-list" id="ah-history-list"></ol>
      </div>
      <div class="ah-card">
        <div class="ah-card-title">目前步驟</div>
        <dl class="ah-info" id="ah-info"></dl>
        <button class="btn btn-outline btn-sm" id="ah-feedback-btn" type="button" disabled>回報問題</button>
      </div>
    </aside>
  </section>
`;

// ─── Upload view ───────────────────────────────────────────────────────────

function wireUpload() {
  const zone  = $('#ah-dropzone');
  const input = $('#ah-file-input');

  zone.addEventListener('click', () => { if (!state.loadingInput) input.click(); });
  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (!state.loadingInput) input.click();
    }
  });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer?.files?.[0];
    if (file) openPhoto(async () => file, file.name);
  });
  input.addEventListener('change', () => {
    const file = input.files[0];
    // Reset first so picking the same file again still fires `change`.
    input.value = '';
    if (file) openPhoto(async () => file, file.name);
  });
}

async function loadSamples() {
  let samples;
  try {
    samples = await listSamples();
  } catch (err) {
    console.warn('[aihair] failed to list samples:', err.message);
    return;
  }
  if (!samples.length) return;

  const strip = $('#ah-samples-strip');
  strip.innerHTML = '';
  for (const s of samples) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ah-sample';
    btn.title = s.name;
    btn.innerHTML = `<img src="${escapeHtml(s.url)}" alt="${escapeHtml(s.name)}" loading="lazy" />`;
    btn.addEventListener('click', () => openPhoto(async () => {
      const res = await fetch(s.url);
      if (!res.ok) throw new Error(`範例照片載入失敗（HTTP ${res.status}）`);
      return res.blob();
    }, s.name));
    strip.appendChild(btn);
  }
  $('#ah-samples').hidden = false;
}

/**
 * Open a photo as the original of a new edit session.
 * @param {() => Promise<Blob>} getBlob
 * @param {string} name
 */
async function openPhoto(getBlob, name) {
  if (state.loadingInput || state.run) return;
  state.loadingInput = true;
  $('#ah-dropzone').classList.add('loading');
  setUploadMessage('讀取照片中…');
  try {
    const image = await toEngineImage(await getBlob());
    const original = await makeStep(image);
    resetHistory([original]);
    state.fileName = name;
    updateFilenameHeader(name);
    clearStatus();
    setUploadMessage('');
  } catch (err) {
    setUploadMessage(err.message || String(err), 'error');
  } finally {
    state.loadingInput = false;
    $('#ah-dropzone').classList.remove('loading');
    render();
  }
}

function setUploadMessage(text, kind = '') {
  const el = $('#ah-upload-msg');
  el.textContent = text;
  el.className = `ah-upload-msg${kind ? ` ${kind}` : ''}`;
}

// ─── History ───────────────────────────────────────────────────────────────

/**
 * A history step for `blob`, with its size read by decoding it.
 * @param {Blob} blob
 * @param {Partial<Step>} [meta]
 * @returns {Promise<Step>}
 */
async function makeStep(blob, meta = {}) {
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.src = url;
  try {
    await img.decode();
  } catch {
    URL.revokeObjectURL(url);
    throw new Error(meta.feature ? '結果圖片無法讀取' : '無法讀取這張照片');
  }
  return {
    blob, url,
    width: img.naturalWidth, height: img.naturalHeight,
    feature: null, level: null, taskId: null, durationMs: null, remoteUrl: null,
    ...meta,
  };
}

/** Replace the whole history with `steps` (cursor at the last one). */
function resetHistory(steps) {
  const keep = new Set(steps);
  for (const s of state.history) if (!keep.has(s)) URL.revokeObjectURL(s.url);
  state.history = steps;
  state.cursor = steps.length - 1;
  state.compare = false;
}

/** Push `step` after the cursor, dropping the redo branch. */
function pushStep(step) {
  const dropped = state.history.splice(state.cursor + 1);
  for (const s of dropped) URL.revokeObjectURL(s.url);
  state.history.push(step);
  state.cursor = state.history.length - 1;
  state.compare = false;
}

function moveTo(index) {
  if (state.run) return;
  if (index < 0 || index >= state.history.length || index === state.cursor) return;
  state.cursor = index;
  state.compare = false;
  clearStatus();
  render();
}

const undo = () => moveTo(state.cursor - 1);
const redo = () => moveTo(state.cursor + 1);

function stepLabel(step, index) {
  if (index === 0) return '原圖';
  const f = getFeature(step.feature);
  const name = f ? f.label : step.feature;
  return step.level != null ? `${name} Lv${step.level}` : name;
}

// ─── Toolbar ───────────────────────────────────────────────────────────────

function wireToolbar() {
  $('#ah-undo-btn').addEventListener('click', undo);
  $('#ah-redo-btn').addEventListener('click', redo);
  $('#ah-discard-btn').addEventListener('click', discardEdits);
  $('#ah-download-btn').addEventListener('click', downloadCurrent);
  $('#ah-change-btn').addEventListener('click', changePhoto);
}

/** 原圖: hold to peek at the original, a quick click toggles it. */
function wireCompare() {
  const btn = $('#ah-compare-btn');
  let press = null;   // { at, before } while the pointer is down

  btn.addEventListener('pointerdown', (e) => {
    if (btn.disabled || e.button !== 0) return;
    btn.setPointerCapture?.(e.pointerId);
    press = { at: performance.now(), before: state.compare };
    setCompare(true);
  });
  const release = () => {
    if (!press) return;
    const quick = performance.now() - press.at < PEEK_HOLD_MS;
    setCompare(quick ? !press.before : press.before);
    press = null;
  };
  btn.addEventListener('pointerup', release);
  btn.addEventListener('pointercancel', release);
  btn.addEventListener('lostpointercapture', release);
  // Keyboard (Enter / Space) fires a click with detail 0; pointer clicks are
  // already handled above.
  btn.addEventListener('click', (e) => { if (e.detail === 0) setCompare(!state.compare); });
  btn.addEventListener('contextmenu', (e) => e.preventDefault());
}

function setCompare(on) {
  const next = !!on && state.cursor > 0 && !state.run;
  if (next === state.compare) return;
  state.compare = next;
  render();
}

function discardEdits() {
  if (state.run || state.history.length <= 1) return;
  const n = state.history.length - 1;
  showConfirmDialog({
    title: '取消編輯？',
    description: `將捨棄全部 ${n} 個編輯步驟（包含可重做的步驟），回到原圖。這個動作無法復原。`,
    confirmLabel: '捨棄編輯',
    destructive: true,
    onConfirm: () => {
      resetHistory([state.history[0]]);
      clearStatus();
      render();
    },
  });
}

function changePhoto() {
  if (state.run) return;
  const go = () => {
    resetHistory([]);
    state.fileName = '';
    updateFilenameHeader('');
    clearStatus();
    setUploadMessage('');
    render();
  };
  if (state.history.length > 1) {
    showConfirmDialog({
      title: '更換照片？',
      description: '目前照片的編輯紀錄會被清除。',
      confirmLabel: '更換照片',
      destructive: true,
      onConfirm: go,
    });
  } else {
    go();
  }
}

function downloadCurrent() {
  const step = state.history[state.cursor];
  if (!step) return;
  const ext = { 'image/png': 'png', 'image/webp': 'webp' }[step.blob.type] || 'jpg';
  const stem = (state.fileName || 'photo').replace(/\.[^.]+$/, '') || 'photo';
  const chain = state.history.slice(1, state.cursor + 1)
    .map((s) => (s.level != null ? `${s.feature}-lv${s.level}` : s.feature));
  const name = chain.length ? `${stem}_aiHair_${chain.join('+')}.${ext}` : `${stem}.${ext}`;
  const a = document.createElement('a');
  a.href = step.url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ─── Features + 套用 ───────────────────────────────────────────────────────

function buildFeatureChips() {
  const wrap = $('#ah-features');
  for (const f of FEATURES) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'ah-chip';
    chip.dataset.feature = f.name;
    chip.setAttribute('role', 'radio');
    chip.setAttribute('aria-checked', 'false');
    chip.innerHTML = `<span class="ah-chip-zh">${escapeHtml(f.label)}</span><span class="ah-chip-en">${escapeHtml(f.name)}</span>`;
    chip.addEventListener('click', () => {
      state.selected = f.name;
      if (state.status?.kind === 'hint') clearStatus();
      render();
    });
    wrap.appendChild(chip);
  }
}

function renderLevelRow() {
  const f = getFeature(state.selected);
  const row = $('#ah-level-row');
  row.hidden = !f?.levels;
  if (!f?.levels) return;
  const seg = $('#ah-level-seg');
  if (seg.dataset.feature !== f.name) {
    seg.dataset.feature = f.name;
    seg.innerHTML = '';
    for (const lv of f.levels) {
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'radio');
      b.dataset.level = String(lv);
      b.textContent = `Lv${lv}`;
      b.addEventListener('click', () => { state.levels[f.name] = lv; render(); });
      seg.appendChild(b);
    }
  }
  for (const b of seg.children) {
    b.setAttribute('aria-checked', String(Number(b.dataset.level) === state.levels[f.name]));
  }
}

function wireApply() {
  $('#ah-apply-btn').addEventListener('click', applySelected);
  $('#ah-cancel-run-btn').addEventListener('click', () => state.run?.abort.abort());
}

async function applySelected() {
  if (state.run || !state.selected || state.cursor < 0) return;
  const feature = state.selected;
  const f = getFeature(feature);
  const level = f.levels ? state.levels[feature] : null;
  const label = level != null ? `${f.label} Lv${level}` : f.label;
  const source = state.history[state.cursor];

  const run = {
    abort: new AbortController(),
    feature, level, label,
    startedAt: performance.now(),
    taskId: null,
    timer: null,
  };
  state.run = run;
  state.compare = false;
  clearStatus();
  setBusyStage('preparing');
  $('#ah-busy-task').textContent = '';
  run.timer = setInterval(renderBusyTitle, 500);
  render();

  try {
    const result = await applyHairFeature(source.blob, { feature, level }, {
      signal: run.abort.signal,
      onStage: setBusyStage,
      onTaskId: (taskId) => {
        run.taskId = taskId;
        $('#ah-busy-task').textContent = `Task ID: ${taskId}`;
      },
    });
    const durationMs = performance.now() - run.startedAt;
    if (result.skipped) {
      // The engine left the photo as it was (grayHair with no grey hair,
      // hairline with the forehead covered): nothing to push.
      setStatus(`「${label}」沒有變更照片：${result.skipped}`, 'notice', result.taskId);
    } else {
      pushStep(await makeStep(result.blob, {
        feature, level, durationMs, taskId: result.taskId, remoteUrl: result.remoteUrl,
      }));
      setStatus(`已套用「${label}」（${formatDuration(durationMs)}）`, 'ok');
    }
  } catch (err) {
    if (err?.name === 'AbortError') {
      setStatus('已取消。遠端任務可能仍在執行，但結果不會套用。', 'notice', run.taskId);
    } else {
      setStatus(`套用「${label}」失敗：${err?.message || err}`, 'error', run.taskId);
    }
  } finally {
    clearInterval(run.timer);
    state.run = null;
    render();
  }
}

function setBusyStage(stage, info = {}) {
  let text = STAGE_LABELS[stage] || stage;
  if (stage === 'uploading' && typeof info.pct === 'number') text = `上傳照片中… ${info.pct}%`;
  if (stage === 'queued' && info.queuePos > 0) text = `排隊中，前面還有 ${info.queuePos} 個任務…`;
  $('#ah-busy-stage').textContent = text;
}

function renderBusyTitle() {
  if (!state.run) return;
  const elapsed = formatDuration(performance.now() - state.run.startedAt, true);
  $('#ah-busy-title').textContent = `「${state.run.label}」處理中 · ${elapsed}`;
}

// ─── Status line ───────────────────────────────────────────────────────────

function setStatus(text, kind = 'hint', taskId = null) {
  state.status = { text, kind };
  state.statusTaskId = taskId;
}

function clearStatus() {
  state.status = null;
  state.statusTaskId = null;
}

function renderStatus() {
  const el = $('#ah-status');
  let { text, kind } = state.status || {};
  if (!text && !state.run) {
    const f = getFeature(state.selected);
    kind = 'hint';
    text = f
      ? `按「套用」將「${f.label}」套用到目前的圖片${state.cursor > 0 ? `（第 ${state.cursor} 步的結果）` : ''}。`
      : '請先在上方選擇一個功能，再按「套用」。';
  }
  el.textContent = text || '';
  el.className = `ah-status ${kind || ''}`;
}

// ─── Keyboard shortcuts ────────────────────────────────────────────────────

function wireShortcuts() {
  document.addEventListener('keydown', (e) => {
    if ($('#ah-editor').hidden) return;
    if (e.target?.closest?.('input, textarea, select, [contenteditable="true"], .dialog-content')) return;
    const mod = (e.ctrlKey || e.metaKey) && !e.altKey;
    if (!mod) return;
    const key = e.key.toLowerCase();
    if (key === 'z') {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    } else if (key === 'y') {
      e.preventDefault();
      redo();
    }
  });
}

// ─── Feedback ──────────────────────────────────────────────────────────────

/** The task 回報問題 reports: a failed/skipped run while its message shows,
 *  else the current step's. */
function feedbackTaskId() {
  return state.statusTaskId || state.history[state.cursor]?.taskId || null;
}

function wireFeedback() {
  $('#ah-feedback-btn').addEventListener('click', () => {
    const taskId = feedbackTaskId();
    if (taskId) showFeedbackDialog({ taskId, page: PAGE });
  });
}

// ─── Render ────────────────────────────────────────────────────────────────

let _historyKey = '';

function render() {
  const hasPhoto = state.history.length > 0;
  $('#ah-upload').hidden = hasPhoto;
  $('#ah-editor').hidden = !hasPhoto;
  if (!hasPhoto) return;

  const busy = !!state.run;
  const current = state.history[state.cursor];
  const shown = state.compare ? state.history[0] : current;

  // Stage
  const img = $('#ah-image');
  if (img.getAttribute('src') !== shown.url) img.src = shown.url;
  img.alt = state.compare ? '原圖' : stepLabel(current, state.cursor);
  $('#ah-stage').classList.toggle('comparing', state.compare);
  $('#ah-badge').textContent = state.compare
    ? '原圖'
    : (state.cursor === 0 ? '原圖' : `第 ${state.cursor} 步：${stepLabel(current, state.cursor)}`);
  $('#ah-busy').hidden = !busy;
  if (busy) renderBusyTitle();

  // Toolbar
  $('#ah-undo-btn').disabled     = busy || state.cursor <= 0;
  $('#ah-redo-btn').disabled     = busy || state.cursor >= state.history.length - 1;
  $('#ah-compare-btn').disabled  = busy || state.cursor <= 0;
  $('#ah-compare-btn').setAttribute('aria-pressed', String(state.compare));
  $('#ah-discard-btn').disabled  = busy || state.history.length <= 1;
  $('#ah-change-btn').disabled   = busy;

  // Features + 套用
  for (const chip of $root.querySelectorAll('.ah-chip')) {
    chip.setAttribute('aria-checked', String(chip.dataset.feature === state.selected));
  }
  renderLevelRow();
  const applyBtn = $('#ah-apply-btn');
  applyBtn.disabled = busy || !state.selected;
  applyBtn.textContent = busy ? '處理中…' : '套用';
  renderStatus();

  // Side panel
  renderHistory(busy);
  renderInfo(current);
  $('#ah-feedback-btn').disabled = !feedbackTaskId();
}

function renderHistory(busy) {
  const key = `${state.history.map((s) => s.url).join('|')}#${state.cursor}#${busy}`;
  if (key === _historyKey) return;
  _historyKey = key;

  const list = $('#ah-history-list');
  list.innerHTML = '';
  state.history.forEach((step, i) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ah-history-item';
    if (i === state.cursor) {
      btn.classList.add('current');
      btn.setAttribute('aria-current', 'step');
    }
    if (i > state.cursor) btn.classList.add('future');
    btn.disabled = busy;
    const meta = [
      step.durationMs != null ? formatDuration(step.durationMs) : null,
      `${step.width}×${step.height}`,
    ].filter(Boolean).join(' · ');
    btn.innerHTML = `
      <img src="${step.url}" alt="" />
      <span class="ah-history-text">
        <span class="ah-history-name">${i}. ${escapeHtml(stepLabel(step, i))}</span>
        <span class="ah-history-meta">${escapeHtml(meta)}</span>
      </span>`;
    btn.addEventListener('click', () => moveTo(i));
    li.appendChild(btn);
    list.appendChild(li);
  });
  list.querySelector('.current')?.scrollIntoView({ block: 'nearest' });
}

function renderInfo(step) {
  const rows = [];
  if (state.cursor === 0) {
    rows.push(['檔名', escapeHtml(state.fileName || '—')]);
  } else {
    rows.push(['功能', `${escapeHtml(stepLabel(step, state.cursor))} <span class="mono">(${escapeHtml(step.feature)})</span>`]);
    rows.push(['Task ID', `<span class="mono">${escapeHtml(step.taskId || '—')}</span>`]);
    rows.push(['耗時', escapeHtml(formatDuration(step.durationMs))]);
  }
  rows.push(['尺寸', `${step.width} × ${step.height}`]);
  rows.push(['格式', escapeHtml(step.blob.type || '—')]);
  if (step.remoteUrl) {
    rows.push(['原始結果', `<a href="${escapeHtml(step.remoteUrl)}" target="_blank" rel="noopener">開啟</a>`]);
  }
  $('#ah-info').innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
}

// ─── Utility ───────────────────────────────────────────────────────────────

function updateFilenameHeader(name) {
  const el = document.getElementById('filename');
  if (el) el.textContent = name || '';
}

function formatDuration(ms, clock = false) {
  if (ms == null || !Number.isFinite(ms)) return '—';
  const s = ms / 1000;
  if (clock) {
    const whole = Math.floor(s);
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  }
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
