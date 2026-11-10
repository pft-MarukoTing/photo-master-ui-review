// Saves several result images as separate files, shared by the panel page and the chat page.
// iPhone/iPad: the share sheet's "Save N Images" is a web page's only way into Photos;
// elsewhere each file gets its own <a download>.
window.PhotoAgentSave = (() => {
  const CONCURRENCY = 3;
  const DOWNLOAD_GAP_MS = 300;
  const SHARE_CHUNK = 10;

  function isIOS() {
    const ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
  }

  function formatOf(buf) {
    const h = new Uint8Array(buf, 0, Math.min(4, buf.byteLength));
    return h[0] === 0x89 && h[1] === 0x50 && h[2] === 0x4e && h[3] === 0x47
      ? { ext: "png", mime: "image/png" }
      : { ext: "jpg", mime: "image/jpeg" };
  }

  async function fetchFile({ url, name }) {
    // Retries network errors and 5xx only; a 4xx won't change on retry.
    let lastError;
    for (let attempt = 0; attempt < 3; attempt++) {
      let res;
      try {
        res = await fetch(url);
      } catch (error) {
        lastError = error;
        continue;
      }
      if (res.ok) {
        const buf = await res.arrayBuffer();
        const { ext, mime } = formatOf(buf);
        return new File([buf], `${name}.${ext}`, { type: mime });
      }
      lastError = new Error(`HTTP ${res.status}`);
      if (res.status < 500) break;
    }
    throw lastError;
  }

  // items: [{ url, name }] with name lacking the extension; resolves in item order, skipping failures.
  async function fetchImages(items, onProgress) {
    const results = new Array(items.length).fill(null);
    let next = 0;
    let done = 0;
    let failed = 0;
    const worker = async () => {
      while (next < items.length) {
        const i = next++;
        try {
          results[i] = await fetchFile(items[i]);
        } catch {
          failed += 1;
        }
        done += 1;
        if (onProgress) onProgress(done, items.length);
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, items.length) }, worker));
    return { files: results.filter(Boolean), failed };
  }

  function canShareFiles(files) {
    try {
      return sharesToPhotos() && navigator.canShare({ files: files.slice(0, 1) });
    } catch {
      return false;
    }
  }

  // Whether saveImages will end in the share sheet rather than downloads.
  function sharesToPhotos() {
    return isIOS() && !!navigator.canShare;
  }

  function shareChunks(files) {
    try {
      if (navigator.canShare({ files })) return [files];
    } catch {}
    const chunks = [];
    for (let i = 0; i < files.length; i += SHARE_CHUNK) chunks.push(files.slice(i, i + SHARE_CHUNK));
    return chunks;
  }

  function downloadOne(file) {
    const href = URL.createObjectURL(file);
    const a = document.createElement("a");
    a.href = href;
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
  }

  // Browsers drop rapid back-to-back download clicks, hence the gap.
  async function downloadEach(files) {
    for (let i = 0; i < files.length; i++) {
      if (i) await new Promise((resolve) => setTimeout(resolve, DOWNLOAD_GAP_MS));
      downloadOne(files[i]);
    }
  }

  // Must run synchronously inside a tap: navigator.share needs the user's gesture.
  function share(files) {
    return navigator.share({ files }).then(
      () => true,
      (error) => {
        if (error && error.name === "AbortError") return true;
        return downloadEach(files).then(() => true);
      }
    );
  }

  // askShare({ count, from, to, total, failed, chunked }, doShare) shows a button that calls doShare() on tap and
  // resolves with its result, or with false when the user dismisses it.
  // Resolves { saved, failed, shared, cancelled }.
  async function saveImages(items, { onProgress, askShare }) {
    const { files, failed } = await fetchImages(items, onProgress);
    if (!files.length) return { saved: 0, failed, shared: false, cancelled: false };
    if (askShare && canShareFiles(files)) {
      const chunks = shareChunks(files);
      let from = 1;
      for (const chunk of chunks) {
        const to = from + chunk.length - 1;
        const step = { count: chunk.length, from, to, total: files.length, failed, chunked: chunks.length > 1 };
        const ok = await askShare(step, () => share(chunk));
        if (!ok) return { saved: from - 1, failed, shared: true, cancelled: true };
        from = to + 1;
      }
      return { saved: files.length, failed, shared: true, cancelled: false };
    }
    await downloadEach(files);
    return { saved: files.length, failed, shared: false, cancelled: false };
  }

  return { saveImages, sharesToPhotos };
})();
