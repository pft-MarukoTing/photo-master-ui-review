/**
 * aiHair API service — connects to the Flask backend (backend/routes/api.py),
 * which runs the AIPoC "aiHair" effect.
 *
 * One run = one feature on one image:
 *   get_job_id -> upload (proxied) -> process { feature, level? } -> poll -> image
 */

/**
 * Detect the API base path from the current URL, so the page works under an
 * nginx sub-path such as /mt-testbed-aihair/.
 * @returns {string}
 */
function getApiBase() {
  const m = window.location.pathname.match(/^(\/[^/]+)/);
  if (m && m[1] !== '/web' && m[1] !== '/api' && !m[1].includes('.')) return `${m[1]}/api`;
  return '/api';
}

export const API_BASE = getApiBase();

/**
 * aiHair's nine features, in the engine's order (ai_hair/features.py
 * FEATURE_NAMES). `name` is what the API's `feature` param takes; `levels` is
 * set for the features that take a `level`.
 */
export const FEATURES = [
  { name: 'frizzCare',   label: '毛躁修護' },
  { name: 'plump',       label: '髮量豐盈' },
  { name: 'fluffy',      label: '髮根蓬鬆' },
  { name: 'hairline',    label: '髮際線', levels: [1, 2] },
  { name: 'soft',        label: '柔順' },
  { name: 'hairpart',    label: '補髮縫' },
  { name: 'grayHair',    label: '白髮修飾' },
  { name: 'rootRetouch', label: '補染髮根' },
  { name: 'strayHair',   label: '碎髮修整' },
];

export function getFeature(name) {
  return FEATURES.find((f) => f.name === name) || null;
}

// The photo formats the engine reads. Anything else is re-encoded as JPEG.
const ENGINE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

const SERVER_HEARTBEAT_SLA_MS = 600_000;

/**
 * `blob` in a format the engine reads: JPEG / PNG / WebP as they are, any
 * other image the browser can decode (HEIC on Safari, AVIF, BMP, GIF …)
 * re-encoded as JPEG. Throws when the browser can't decode it.
 * @param {Blob} blob
 * @returns {Promise<Blob>}
 */
export async function toEngineImage(blob) {
  if (ENGINE_TYPES.includes(blob.type)) return blob;
  let bitmap;
  try {
    bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
  } catch {
    throw new Error('無法讀取這個檔案格式，請使用 JPG、PNG 或 WebP 照片');
  }
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext('2d').drawImage(bitmap, 0, 0);
  bitmap.close?.();
  const out = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.95));
  if (!out) throw new Error('照片轉換為 JPEG 失敗');
  return out;
}

async function readError(res, what) {
  let msg = '';
  try {
    const body = await res.json();
    msg = body?.error || '';
  } catch {
    msg = await res.text().catch(() => '');
  }
  return new Error(`${what} (${res.status})${msg ? `: ${String(msg).slice(0, 300)}` : ''}`);
}

/**
 * @param {string} contentType
 * @param {AbortSignal} [signal]
 * @returns {Promise<{job_id: string}>}
 */
async function getJobId(contentType, signal) {
  const res = await fetch(`${API_BASE}/hair/get_job_id?content_type=${encodeURIComponent(contentType)}`, { signal });
  if (!res.ok) throw await readError(res, 'Failed to get job ID');
  return res.json();
}

/**
 * PUT the photo to the backend's upload proxy. XHR so upload progress is
 * reportable.
 * @param {string} jobId
 * @param {Blob} blob
 * @param {(pct: number) => void} [onProgress]
 * @param {AbortSignal} [signal]
 */
function uploadPhoto(jobId, blob, onProgress, signal) {
  const url = `${API_BASE}/hair/upload/${encodeURIComponent(jobId)}`;
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const xhr = new XMLHttpRequest();
    const onAbort = () => xhr.abort();
    signal?.addEventListener('abort', onAbort, { once: true });
    const done = (fn) => (arg) => { signal?.removeEventListener('abort', onAbort); fn(arg); };

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.addEventListener('load', done(() => {
      if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
      let msg = xhr.responseText?.slice(0, 300) || '(empty body)';
      try { msg = JSON.parse(xhr.responseText).error || msg; } catch { /* not JSON */ }
      reject(new Error(`Upload failed (${xhr.status}): ${msg}`));
    }));
    xhr.addEventListener('error', done(() => reject(new Error('Upload network error — is the Flask server reachable?'))));
    xhr.addEventListener('abort', done(() => reject(new DOMException('Aborted', 'AbortError'))));

    xhr.open('PUT', url);
    xhr.setRequestHeader('Content-Type', blob.type || 'application/octet-stream');
    xhr.send(blob);
  });
}

/**
 * @param {string} jobId
 * @param {string} feature
 * @param {number|null} level
 * @param {AbortSignal} [signal]
 * @returns {Promise<{task_id: string, job_id: string, feature: string, level: number|null}>}
 */
async function submitFeature(jobId, feature, level, signal) {
  const body = { job_id: jobId, feature };
  if (level != null) body.level = level;
  const res = await fetch(`${API_BASE}/hair/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await readError(res, 'Failed to submit job');
  return res.json();
}

async function getStatus(taskId, signal) {
  const res = await fetch(`${API_BASE}/hair/status/${encodeURIComponent(taskId)}`, { signal });
  if (!res.ok) {
    if (res.status === 404) return { status: 'error', error: 'Job not found' };
    throw await readError(res, 'Failed to get status');
  }
  return res.json();
}

async function getResult(taskId, signal) {
  const res = await fetch(`${API_BASE}/hair/result/${encodeURIComponent(taskId)}`, { signal });
  if (!res.ok) throw await readError(res, 'Failed to get result');
  return res.json();
}

/**
 * The result image of a finished task, as a same-origin blob.
 * @param {string} taskId
 * @param {AbortSignal} [signal]
 * @returns {Promise<Blob>}
 */
async function fetchResultImage(taskId, signal) {
  const res = await fetch(`${API_BASE}/hair/image/${encodeURIComponent(taskId)}`, { signal });
  if (!res.ok) throw await readError(res, 'Failed to download the result image');
  return res.blob();
}

/**
 * @param {number} ms
 * @param {AbortSignal} [signal]
 */
function abortableSleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const tid = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(tid);
      reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true });
  });
}

/**
 * Poll a task until it finishes; resolves with its /hair/result body.
 * Network errors back off and retry; engine errors throw.
 * @param {string} taskId
 * @param {(status: string, queuePos?: number) => void} [onStatus]
 * @param {{ interval?: number, signal?: AbortSignal }} [options]
 */
async function pollUntilComplete(taskId, onStatus, { interval = 2000, signal } = {}) {
  let backoff = interval;
  while (true) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');

    let status;
    try {
      status = await getStatus(taskId, signal);
      backoff = interval;
    } catch (err) {
      if (err?.name === 'AbortError' || signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      console.warn('[aihair-poll] network error, backing off:', err.message);
      await abortableSleep(backoff, signal);
      backoff = Math.min(backoff * 2, 16_000);
      continue;
    }

    onStatus?.(status.status || 'pending', status.queuePos);

    if (['completed', 'done', 'success'].includes(status.status)) {
      const result = await getResult(taskId, signal);
      if (result.status === 'error' || result.error) throw new Error(result.error || 'Job failed');
      return result;
    }
    if (status.status === 'error' || status.error) {
      throw new Error(status.error || status.errorCode || 'Job failed');
    }
    if (status.updatedAt && Date.now() - Number(status.updatedAt) > SERVER_HEARTBEAT_SLA_MS) {
      throw new Error('Server heartbeat stale — the job may have crashed');
    }

    await abortableSleep(interval, signal);
  }
}

/**
 * Run one aiHair feature on an image: upload it, submit the task, poll until
 * done and download the result.
 *
 * @param {Blob} imageBlob - JPEG / PNG / WebP (see toEngineImage)
 * @param {{ feature: string, level?: number|null }} params
 * @param {{
 *   onStage?: (stage: 'preparing'|'uploading'|'queued'|'processing'|'downloading', info?: {pct?: number, queuePos?: number}) => void,
 *   onTaskId?: (taskId: string) => void,
 *   signal?: AbortSignal,
 * }} [callbacks]
 * @returns {Promise<{blob: Blob, taskId: string, skipped: string, remoteUrl: string|null}>}
 */
export async function applyHairFeature(imageBlob, { feature, level = null }, callbacks = {}) {
  const { onStage, onTaskId, signal } = callbacks;

  onStage?.('preparing');
  const { job_id } = await getJobId(imageBlob.type || 'image/jpeg', signal);

  onStage?.('uploading', { pct: 0 });
  await uploadPhoto(job_id, imageBlob, (pct) => onStage?.('uploading', { pct }), signal);

  onStage?.('processing');
  const { task_id } = await submitFeature(job_id, feature, level, signal);
  onTaskId?.(task_id);

  const result = await pollUntilComplete(task_id, (status, queuePos) => {
    if (typeof queuePos === 'number' && queuePos > 0) onStage?.('queued', { queuePos });
    else onStage?.('processing');
  }, { signal });

  onStage?.('downloading');
  const blob = await fetchResultImage(task_id, signal);
  return {
    blob,
    taskId: task_id,
    skipped: result.skipped || '',
    remoteUrl: result.remote_url || null,
  };
}

/**
 * The sample photos in the testbed's example_samples/ directory.
 * @returns {Promise<Array<{name: string, url: string}>>}
 */
export async function listSamples() {
  const res = await fetch(`${API_BASE}/samples`);
  if (!res.ok) throw await readError(res, 'Failed to list samples');
  const { samples } = await res.json();
  return (samples || []).map((s) => ({
    name: s.name,
    url: `${API_BASE}/sample_file/${encodeURIComponent(s.name)}`,
  }));
}
