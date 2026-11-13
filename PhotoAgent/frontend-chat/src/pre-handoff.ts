// 上傳頁（還沒分析、也還沒有 case）按「面板」：把選好的照片帶去面板模式，那邊用它自己的流程接著做。
//
// 照片可能是使用者自己的檔案（File），sessionStorage 放不下也存不了 Blob，所以放 IndexedDB
// （同一個 origin，面板模式讀得到）。範例照片和已經上傳過的照片只記網址，面板模式自己去抓。
// 面板模式那邊的讀取在 frontend/app.js（PRE_HANDOFF_DB），格式改這裡要一起改。
// 只接剛剛切過去的：超過 MAX_AGE_MS 的不算數（中途關掉、隔天再開）。

export const PRE_HANDOFF_DB = "photoagent-pre-handoff";
const STORE = "pending";
const KEY = "photos";

export type PreHandoffPhoto = { name: string; blob?: Blob; url?: string };
// prompt：上傳頁已經打好、AI 也收下的需求（一行一則），面板模式放進它的「你的需求」欄。
export type PreHandoff = { at: number; photos: PreHandoffPhoto[]; prompt?: string };

// 切到面板模式時照片和需求都帶過去了，不算「離開會遺失」：換頁前標記一下，
// 上傳頁的離開提醒（beforeunload）看到就不跳。
let leavingForPanel = false;
export function markLeavingForPanel() {
  leavingForPanel = true;
}
export function isLeavingForPanel() {
  return leavingForPanel;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(PRE_HANDOFF_DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function writePreHandoff(photos: PreHandoffPhoto[], prompt?: string): Promise<void> {
  try {
    const db = await open();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put({ at: Date.now(), photos, prompt: prompt || undefined } satisfies PreHandoff, KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {
    /* 存不了就只切過去，面板模式那邊沒有照片可接 */
  }
}

// 面板模式按「對話」、但還沒有 case（沒登入、或還沒進歷史）：面板把手上的照片放在這裡（key "back"），
// 對話頁一開就接回來，回到上傳頁選好照片的那一步，不掉回首頁。格式在 frontend/app.js（寫入端）。
export async function takeBackHandoff(): Promise<File[]> {
  try {
    if (!window.indexedDB) return [];
    const db = await open();
    const data = await new Promise<{ at?: number; photos?: { name: string; blob: Blob }[] } | null>((resolve) => {
      try {
        const tx = db.transaction(STORE, "readwrite");
        const store = tx.objectStore(STORE);
        const get = store.get("back");
        let value: any = null;
        get.onsuccess = () => { value = get.result || null; store.delete("back"); };
        tx.oncomplete = () => resolve(value);
        tx.onerror = () => resolve(null);
      } catch { resolve(null); }
    });
    db.close();
    if (!data || !Array.isArray(data.photos) || Date.now() - (data.at || 0) > 2 * 60 * 1000) return [];
    return data.photos.filter((x) => x && x.blob).map((x) => new File([x.blob], x.name || "photo.jpg", { type: x.blob.type || "image/jpeg" }));
  } catch {
    return [];
  }
}
