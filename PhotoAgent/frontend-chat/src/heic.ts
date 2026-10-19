// Most desktop browsers (Chrome/Firefox/Edge) can't render HEIC/HEIF in an
// <img> at all, even though the backend (Pillow + pillow-heif) decodes it
// fine. Decode it here to a JPEG purely for on-page preview; the original file
// is still what gets uploaded. Same approach as the old page's previewableBlob
// in frontend/app.js, and the same vendored libheif-js bundle (LGPL-3.0),
// which lives one level up next to styles.css.

type HeifImage = {
  get_width(): number;
  get_height(): number;
  display(data: ImageData, cb: (d: ImageData | null) => void): void;
  free(): void;
};
type HeifDecoder = { decoder: unknown; decode(buf: Uint8Array): HeifImage[] };
type Libheif = { HeifDecoder: new () => HeifDecoder; heif_context_free?: (ctx: unknown) => void };

declare global {
  interface Window {
    libheif?: () => Libheif;
  }
}

export function isHeic(file: File): boolean {
  return /^image\/hei[cf]/i.test(file.type) || /\.(heic|heif)$/i.test(file.name);
}

// ~1.4MB, so it's loaded the first time a HEIC is actually picked. It decodes
// on the main thread on purpose: a Worker-based decoder hangs on pages that
// aren't cross-origin isolated, which this one isn't (see app.js).
let instance: Promise<Libheif> | null = null;
function libheif(): Promise<Libheif> {
  if (!instance) {
    instance = new Promise<() => Libheif>((resolve, reject) => {
      if (window.libheif) return resolve(window.libheif);
      const script = document.createElement("script");
      script.src = "../libheif-bundle.js";
      script.onload = () => (window.libheif ? resolve(window.libheif) : reject(new Error("libheif missing")));
      script.onerror = () => reject(new Error("HEIC 預覽元件載入失敗"));
      document.head.appendChild(script);
    }).then((factory) => factory());
    instance.catch(() => (instance = null)); // let a later pick retry
  }
  return instance;
}

// A full-size decode holds width x height x 4 bytes several times over (the
// WASM buffer, the ImageData, the canvas), and WASM memory never shrinks once
// grown. So decodes run one at a time -- they block the main thread anyway, so
// running them "together" was never faster -- and each frees what it made
// before the next starts.
let queue: Promise<unknown> = Promise.resolve();
function serial<T>(job: () => Promise<T>): Promise<T> {
  const run = queue.then(() => new Promise((r) => setTimeout(r)), () => undefined).then(job);
  queue = run.catch(() => undefined);
  return run;
}

// Long side of the small copy used where a photo is shown as a tile (batch
// sidebar, overview, upload bubbles). An <img> decodes its src at full size
// however small it is drawn, so twenty originals in a grid is twenty
// full-size bitmaps; twenty of these is ~16MB.
const THUMB_SIDE = 512;

const jpeg = (canvas: HTMLCanvasElement, quality: number) =>
  new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));

function release(canvas: HTMLCanvasElement) {
  canvas.width = 0;
  canvas.height = 0;
}

async function scaled(src: CanvasImageSource, w: number, h: number): Promise<Blob | null> {
  const k = Math.min(1, THUMB_SIDE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w * k));
  canvas.height = Math.max(1, Math.round(h * k));
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
  const blob = await jpeg(canvas, 0.8);
  release(canvas);
  return blob;
}

// HEIC decoded once into a full-size JPEG (what the page shows and crops faces
// from, same as before) and, from the same pixels, a tile-size one.
async function decodeHeic(file: File, withThumb: boolean): Promise<{ full: Blob; thumb: Blob | null }> {
  const lib = await libheif();
  const decoder = new lib.HeifDecoder();
  const images = decoder.decode(new Uint8Array(await file.arrayBuffer()));
  const canvas = document.createElement("canvas");
  try {
    const image = images[0];
    if (!image) return { full: file, thumb: null };
    canvas.width = image.get_width();
    canvas.height = image.get_height();
    const ctx = canvas.getContext("2d");
    if (!ctx) return { full: file, thumb: null };
    const data = ctx.createImageData(canvas.width, canvas.height);
    await new Promise<void>((resolve, reject) =>
      image.display(data, (d) => (d ? resolve() : reject(new Error("HEIC 解碼失敗")))),
    );
    ctx.putImageData(data, 0, 0);
    const full = (await jpeg(canvas, 0.85)) || file;
    const thumb = withThumb ? await scaled(canvas, canvas.width, canvas.height) : null;
    return { full, thumb };
  } finally {
    for (const image of images) image.free();
    // The decoder frees its context only on its next decode(); this one has none.
    if (decoder.decoder && lib.heif_context_free) lib.heif_context_free(decoder.decoder);
    decoder.decoder = null;
    release(canvas);
  }
}

// A JPEG blob for HEIC, the file itself for anything else. Never throws: if
// decoding fails the preview just won't render, but the pick still goes
// through and the backend can still process it.
export function previewBlob(file: File): Promise<Blob> {
  if (!isHeic(file)) return Promise.resolve(file);
  return serial(() => decodeHeic(file, false)).then((r) => r.full, () => file);
}

// For a photo going into the batch grid: `full` as previewBlob gives it, plus a
// tile-size `thumb` (null if one couldn't be made -- show `full` instead).
// Never throws, for the same reason as previewBlob.
export function previewBlobs(file: File): Promise<{ full: Blob; thumb: Blob | null }> {
  if (isHeic(file)) return serial(() => decodeHeic(file, true)).catch(() => ({ full: file, thumb: null }));
  return serial(async () => {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    try {
      return { full: file, thumb: await scaled(bitmap, bitmap.width, bitmap.height) };
    } finally {
      bitmap.close();
    }
  }).catch(() => ({ full: file, thumb: null }));
}
