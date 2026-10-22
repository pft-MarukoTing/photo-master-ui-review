// 首頁、上傳頁送的回報：還沒有對話可以存，改記下當時頁面上有什麼，管理者的唯讀頁
// （report-view.tsx）照著排回去。上傳頁的訊息有單張、多張總覽兩套講法，這裡不照
// 狀態重組，直接抄聊天區已經畫好的每一則：誰說的、文字、圖、按鈕上的字。全部存成純
// 資料（圖縮成小張 JPEG 的 data URL），唯讀頁自己畫，不塞 HTML。

export interface ReportPageMsg {
  user: boolean;
  lines: string[];
  images: string[];
  chips: string[];
}

export interface ReportPage {
  page: "home" | "upload";
  // 左邊「我的照片」（只上傳一張就是那張），最多 MAX_PHOTOS 張；total 是實際張數。
  photos?: string[];
  total?: number;
  messages?: ReportPageMsg[];
  // 跟預設不一樣的設定，settingsChanges 的講法。
  settings?: string[];
}

const MAX_PHOTOS = 12;
const MAX_MESSAGES = 80;
const MAX_MSG_IMAGES = 4;
const THUMB_PX = 160;

// 任何一張圖（blob:、同源的縮圖網址）縮成長邊 THUMB_PX 的 JPEG。讀不到就是 null。
function tinyJpeg(src: string): Promise<string | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const scale = Math.min(1, THUMB_PX / Math.max(img.naturalWidth, img.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
        canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", 0.7));
      } catch {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

const BLOCKS = "p, div, li, br, h1, h2, h3, h4, h5, h6";

// 一則訊息的字，照畫面上的分行切開；按鈕、圖、打字中的點點不算。
function msgLines(body: Element): string[] {
  const copy = body.cloneNode(true) as Element;
  copy.querySelectorAll("button, img, svg, canvas, script, style, [hidden], .chat-typing").forEach((el) => el.remove());
  copy.querySelectorAll(BLOCKS).forEach((el) => el.after("\n"));
  return (copy.textContent || "").split("\n").map((s) => s.replace(/\s+/g, " ").trim()).filter(Boolean);
}

export async function captureUploadPage(photoSrcs: string[], settings: string[]): Promise<ReportPage> {
  const shrunk = new Map<string, Promise<string | null>>();
  const shrink = (src: string) => {
    if (!shrunk.has(src)) shrunk.set(src, tinyJpeg(src));
    return shrunk.get(src)!;
  };
  const timeline = document.querySelector("main .chat-timeline");
  const raw = [...(timeline?.querySelectorAll(".chat-msg") || [])].slice(-MAX_MESSAGES).map((m) => {
    const body = m.querySelector(".chat-body") || m;
    const chips = [...body.querySelectorAll("button")].map((b) => (b.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean);
    const images = [...body.querySelectorAll("img")].map((i) => i.currentSrc || i.src).filter(Boolean).slice(0, MAX_MSG_IMAGES);
    return { user: m.classList.contains("is-user"), lines: msgLines(body), images, chips: [...new Set(chips)] };
  });
  const [photos, messages] = await Promise.all([
    Promise.all(photoSrcs.slice(0, MAX_PHOTOS).map(shrink)),
    Promise.all(raw.map(async (m) => ({ ...m, images: (await Promise.all(m.images.map(shrink))).filter((x): x is string => !!x) }))),
  ]);
  return {
    page: "upload",
    photos: photos.filter((x): x is string => !!x),
    total: photoSrcs.length,
    messages: messages.filter((m) => m.lines.length || m.images.length || m.chips.length),
    settings,
  };
}
