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
};
type Libheif = { HeifDecoder: new () => { decode(buf: Uint8Array): HeifImage[] } };

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

// A JPEG blob for HEIC, the file itself for anything else. Never throws: if
// decoding fails the preview just won't render, but the pick still goes
// through and the backend can still process it.
export async function previewBlob(file: File): Promise<Blob> {
  if (!isHeic(file)) return file;
  try {
    const lib = await libheif();
    const images = new lib.HeifDecoder().decode(new Uint8Array(await file.arrayBuffer()));
    const image = images[0];
    if (!image) return file;
    const canvas = document.createElement("canvas");
    canvas.width = image.get_width();
    canvas.height = image.get_height();
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    const data = ctx.createImageData(canvas.width, canvas.height);
    await new Promise<void>((resolve, reject) =>
      image.display(data, (d) => (d ? resolve() : reject(new Error("HEIC 解碼失敗")))),
    );
    ctx.putImageData(data, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    return blob || file;
  } catch {
    return file;
  }
}
