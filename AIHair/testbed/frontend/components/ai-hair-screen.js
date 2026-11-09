/**
 * aiHair screen — upload a photo, pick one of aiHair's nine features, Apply.
 *
 *   ┌──────────────────────────────────────────────┬────────────────┐
 *   │ [Frizz Care][Plump] … [Stray Hair]           │ Edit History   │
 *   │  毛躁修護    髮量豐盈     碎髮修整             │  0 Original    │
 *   │ Level [Lv1][Lv2]              (hairline only) │  1 Frizz Care  │
 *   │ [Undo][Redo] [Original]  [Discard][⬇][Change] │  2 Soft ◀      │
 *   │ ┌──────────────────────────────────────────┐ ├────────────────┤
 *   │ │                  image                   │ │ Current Step   │
 *   │ └──────────────────────────────────────────┘ │  Task ID …     │
 *   │                  [ Apply ]                    │  [Report Issue]│
 *   │                 status line                   │                │
 *   └──────────────────────────────────────────────┴────────────────┘
 *
 * The edit history is the model. Every Apply runs the selected feature on the
 * step the history is at and pushes the result as a new step (the steps after
 * it — the redo branch — are dropped), so edits chain. Undo / Redo (also
 * Ctrl/⌘+Z, Ctrl/⌘+Shift+Z, Ctrl+Y) move along it; clicking a step in Edit
 * History jumps to it; Original shows the uploaded photo (hold to peek, click
 * to toggle); Discard Edits drops every step back to the original photo.
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
// A press on Original shorter than this is a click (toggle); longer is a peek.
const PEEK_HOLD_MS = 300;

const STAGE_LABELS = {
  preparing:   'Preparing…',
  uploading:   'Uploading photo…',
  queued:      'Queued…',
  processing:  'Processing…',
  downloading: 'Downloading result…',
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
  // The status line under Apply: { text, kind: 'hint'|'ok'|'notice'|'error' }
  status:    null,
  // Task of a run that left no step (failed / skipped / cancelled), so
  // Report Issue can still report it while its message is showing.
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
  .ah-chip-main { font-size: 0.875rem; font-weight: 600; }
  .ah-chip-sub { font-size: 0.75rem; color: var(--color-muted-foreground); }
  .ah-chip[aria-checked="true"] {
    background: var(--color-primary); color: var(--color-primary-foreground);
    border-color: var(--color-primary);
  }
  .ah-chip[aria-checked="true"] .ah-chip-sub { color: inherit; opacity: 0.75; }

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

  /* Vivid primary button (Apply), colourful in both themes. */
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
  .ah-info dd { overflow-wrap: anywhere; }
  .ah-info .mono { font-family: ui-monospace, monospace; font-size: 0.75rem; user-select: all; }
  .ah-info a { color: inherit; }
</style>
`;

const SCREEN_HTML = `
  <!-- ── Upload view ─────────────────────────────────────────────── -->
  <section class="ah-upload" id="ah-upload">
    <div class="ah-dropzone" id="ah-dropzone" tabindex="0" role="button" aria-label="Upload photo">
      ${IconUpload('icon-lg')}
      <span class="ah-dropzone-title">Drop a photo here or click to browse</span>
      <span class="ah-dropzone-sub">JPG, PNG, WebP (other formats are converted to JPG)</span>
    </div>
    <input type="file" id="ah-file-input" accept="image/*" hidden />
    <div class="ah-upload-msg" id="ah-upload-msg" role="status"></div>

    <div class="ah-card" id="ah-samples" hidden>
      <div class="ah-card-title">Sample Photos</div>
      <div class="ah-samples-strip" id="ah-samples-strip"></div>
    </div>
  </section>

  <!-- ── Editor view ─────────────────────────────────────────────── -->
  <section class="ah-editor" id="ah-editor" hidden>
    <div class="ah-card ah-main">
      <div class="ah-features" id="ah-features" role="radiogroup" aria-label="Hair features"></div>
      <div class="ah-level-row" id="ah-level-row" hidden>
        <span>Level</span>
        <div class="ah-seg" id="ah-level-seg" role="radiogroup" aria-label="Level"></div>
      </div>

      <div class="ah-toolbar" role="toolbar" aria-label="Edit tools">
        <button class="ah-tool" id="ah-undo-btn" type="button" title="Undo (Ctrl/⌘+Z)">${IconUndo('icon-sm')}<span>Undo</span></button>
        <button class="ah-tool" id="ah-redo-btn" type="button" title="Redo (Ctrl/⌘+Shift+Z)">${IconRedo('icon-sm')}<span>Redo</span></button>
        <span class="ah-toolbar-sep" aria-hidden="true"></span>
        <button class="ah-tool" id="ah-compare-btn" type="button" aria-pressed="false"
                title="Hold to view the original; click to toggle original / current">${IconCompare('icon-sm')}<span>Original</span></button>
        <span class="ah-toolbar-spacer"></span>
        <button class="ah-tool" id="ah-discard-btn" type="button" title="Discard all edits and return to the original">${IconX('icon-sm')}<span>Discard Edits</span></button>
        <button class="ah-tool" id="ah-download-btn" type="button" title="Download the current image">${IconDownload('icon-sm')}<span>Download</span></button>
        <button class="ah-tool" id="ah-change-btn" type="button" title="Use a different photo">${IconReplace('icon-sm')}<span>Change Photo</span></button>
      </div>

      <div class="ah-stage" id="ah-stage">
        <img id="ah-image" alt="" draggable="false" />
        <span class="ah-badge" id="ah-badge"></span>
        <div class="ah-busy" id="ah-busy" hidden role="status" aria-live="polite">
          <div class="ah-spinner" aria-hidden="true"></div>
          <div class="ah-busy-title" id="ah-busy-title"></div>
          <div class="ah-busy-stage" id="ah-busy-stage"></div>
          <div class="ah-busy-task" id="ah-busy-task"></div>
          <button class="btn btn-outline btn-sm" id="ah-cancel-run-btn" type="button">Cancel</button>
        </div>
      </div>

      <div class="ah-apply-row">
        <button class="btn ah-btn-vivid" id="ah-apply-btn" type="button" disabled>Apply</button>
        <div class="ah-status" id="ah-status" role="status" aria-live="polite"></div>
      </div>
    </div>

    <aside class="ah-side">
      <div class="ah-card">
        <div class="ah-card-title">Edit History</div>
        <ol class="ah-history-list" id="ah-history-list"></ol>
      </div>
      <div class="ah-card">
        <div class="ah-card-title">Current Step</div>
        <dl class="ah-info" id="ah-info"></dl>
        <button class="btn btn-outline btn-sm" id="ah-feedback-btn" type="button" disabled>Report Issue</button>
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
      if (!res.ok) throw new Error(`Failed to load the sample photo (HTTP ${res.status})`);
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
  setUploadMessage('Reading photo…');
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
    throw new Error(meta.feature ? 'The result image could not be read' : 'This photo could not be read');
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
  if (index === 0) return 'Original';
  const f = getFeature(step.feature);
  const name = f ? f.en : step.feature;
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

/** Original: hold to peek at the original, a quick click toggles it. */
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
    title: 'Discard all edits?',
    description: `This drops all ${n} edit step${n === 1 ? '' : 's'} (including any redo steps) and returns to the original photo. This can't be undone.`,
    confirmLabel: 'Discard Edits',
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
      title: 'Change photo?',
      description: 'The edit history of the current photo will be cleared.',
      confirmLabel: 'Change Photo',
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

// ─── Features + Apply ───────────────────────────────────────────────────────

function buildFeatureChips() {
  const wrap = $('#ah-features');
  for (const f of FEATURES) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'ah-chip';
    chip.dataset.feature = f.name;
    chip.setAttribute('role', 'radio');
    chip.setAttribute('aria-checked', 'false');
    chip.title = `feature: ${f.name}`;
    chip.innerHTML = `<span class="ah-chip-main">${escapeHtml(f.en)}</span><span class="ah-chip-sub" lang="zh-Hant">${escapeHtml(f.zh)}</span>`;
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
  const label = level != null ? `${f.en} Lv${level}` : f.en;
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
      setStatus(`${label} left the photo unchanged: ${result.skipped}`, 'notice', result.taskId);
    } else {
      pushStep(await makeStep(result.blob, {
        feature, level, durationMs, taskId: result.taskId, remoteUrl: result.remoteUrl,
      }));
      setStatus(`Applied ${label} (${formatDuration(durationMs)})`, 'ok');
    }
  } catch (err) {
    if (err?.name === 'AbortError') {
      setStatus("Cancelled. The remote task may still be running, but its result won't be applied.", 'notice', run.taskId);
    } else {
      setStatus(`${label} failed: ${err?.message || err}`, 'error', run.taskId);
    }
  } finally {
    clearInterval(run.timer);
    state.run = null;
    render();
  }
}

function setBusyStage(stage, info = {}) {
  let text = STAGE_LABELS[stage] || stage;
  if (stage === 'uploading' && typeof info.pct === 'number') text = `Uploading photo… ${info.pct}%`;
  if (stage === 'queued' && info.queuePos > 0) {
    text = `Queued — ${info.queuePos} task${info.queuePos === 1 ? '' : 's'} ahead…`;
  }
  $('#ah-busy-stage').textContent = text;
}

function renderBusyTitle() {
  if (!state.run) return;
  const elapsed = formatDuration(performance.now() - state.run.startedAt, true);
  $('#ah-busy-title').textContent = `Running ${state.run.label} · ${elapsed}`;
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
      ? `Press Apply to run ${f.en} on the current image${state.cursor > 0 ? ` (the result of step ${state.cursor})` : ''}.`
      : 'Pick a feature above, then press Apply.';
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

/** The task Report Issue reports: a failed/skipped run while its message shows,
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
  img.alt = state.compare ? 'Original' : stepLabel(current, state.cursor);
  $('#ah-stage').classList.toggle('comparing', state.compare);
  $('#ah-badge').textContent = state.compare
    ? 'Original'
    : (state.cursor === 0 ? 'Original' : `Step ${state.cursor}: ${stepLabel(current, state.cursor)}`);
  $('#ah-busy').hidden = !busy;
  if (busy) renderBusyTitle();

  // Toolbar
  $('#ah-undo-btn').disabled     = busy || state.cursor <= 0;
  $('#ah-redo-btn').disabled     = busy || state.cursor >= state.history.length - 1;
  $('#ah-compare-btn').disabled  = busy || state.cursor <= 0;
  $('#ah-compare-btn').setAttribute('aria-pressed', String(state.compare));
  $('#ah-discard-btn').disabled  = busy || state.history.length <= 1;
  $('#ah-change-btn').disabled   = busy;

  // Features + Apply
  for (const chip of $root.querySelectorAll('.ah-chip')) {
    chip.setAttribute('aria-checked', String(chip.dataset.feature === state.selected));
  }
  renderLevelRow();
  const applyBtn = $('#ah-apply-btn');
  applyBtn.disabled = busy || !state.selected;
  applyBtn.textContent = busy ? 'Processing…' : 'Apply';
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
    rows.push(['File', escapeHtml(state.fileName || '—')]);
  } else {
    const f = getFeature(step.feature);
    rows.push(['Feature', `${escapeHtml(stepLabel(step, state.cursor))}${f ? ` <span lang="zh-Hant">${escapeHtml(f.zh)}</span>` : ''} <span class="mono">(${escapeHtml(step.feature)})</span>`]);
    rows.push(['Task ID', `<span class="mono">${escapeHtml(step.taskId || '—')}</span>`]);
    rows.push(['Time', escapeHtml(formatDuration(step.durationMs))]);
  }
  rows.push(['Size', `${step.width} × ${step.height}`]);
  rows.push(['Format', escapeHtml(step.blob.type || '—')]);
  if (step.remoteUrl) {
    rows.push(['Raw result', `<a href="${escapeHtml(step.remoteUrl)}" target="_blank" rel="noopener">Open</a>`]);
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
