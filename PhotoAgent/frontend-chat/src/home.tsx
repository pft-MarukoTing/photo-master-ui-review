// Header, the home page's intro, the 最近照片 / 範例照片 gallery and the
// 最近照片 drawer -- old markup and classes throughout.
import { t, lang, LANGS, selectableLanguages, setLang } from "./i18n";
import type { ComponentChildren } from "preact";
import { createPortal } from "preact/compat";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, getUser, urls, type ReportChat } from "./api";
import heroAfter from "./assets/hero_after.jpg";
import heroBefore from "./assets/hero_before.jpg";
// YouCam Online Editor 的 logo，從 yco-collab-space 的 design-library 複製（NavigationHeader 用的同一份）。
import ycoLogoSymbol from "./assets/yco/logo-symbol.svg";
import ycoWordmarkSvg from "./assets/yco/logo-en-desktop.svg?raw";
import { readTheme, THEME_MODE } from "./theme";
import { Icon } from "./icons";
import { ReportsButton } from "./reports";
import {
  applyTheme,
  currentTheme,
  nextTheme,
  THEME_LABELS,
  THEME_SWATCH_CLASS,
  type ThemeKey,
} from "./theme";
import type { SessionState, SessionSummary } from "./types";
import { dayLabel, timeLabel } from "./util";

// One click advances the fixed cycle (theme.ts) -- no picker, the way the old
// header does it. The swatch says which colour, the label names which of the
// seven, so the button needs no separate noun for 主題; the title carries the
// affordance the label used to. At narrow widths styles.css hides .btn-label
// across .header-actions and this collapses to the swatch alone.
// Its own chat-* id, not the old #theme-toggle-btn: the old index.html <style>
// (inlined by vite.config.ts) turns that id into a 32px pill, which made it
// the odd one out next to 回到首頁 / 面板模式's plain .btn-sm.
function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeKey>(currentTheme);
  return (
    <button
      id="chat-theme-btn"
      class="btn btn-secondary btn-sm"
      type="button"
      title={t("點擊切換主題")}
      aria-label={t("主題：{v1}（點擊切換）", { v1: THEME_LABELS[theme] })}
      onClick={() => {
        const next = nextTheme(theme);
        applyTheme(next);
        setTheme(next);
      }}
    >
      <span class={`theme-swatch theme-toggle-swatch ${THEME_SWATCH_CLASS[theme]}`} aria-hidden="true" />
      <span class="btn-label">{THEME_LABELS[theme]}</span>
    </button>
  );
}

// 回報, the old page's #report-modal markup and classes (its <style> comes
// along, see vite.config.ts). No 畫面截圖 row: that one is html2canvas on the
// old page's own DOM. Without a jobId it files a general report, the same as
// the old button pressed with nothing on screen.
function ReportModal(props: { jobId: string | null; chat: ReportChat; onClose: () => void }) {
  const { onClose } = props;
  const [name, setName] = useState(getUser);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [onClose]);

  const submit = async () => {
    if (!name.trim()) {
      setError(t("請輸入你的名稱。"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      // 頁面的樣子抄不到也照樣送，只是管理者那邊只看得到回報本身。
      const page = props.chat.page && (await props.chat.page().catch(() => undefined));
      await api.report(name.trim(), props.jobId, note.trim(), props.chat, page || undefined);
      setDone(true);
    } catch (err) {
      setError((err as Error).message || t("回報失敗，請稍後再試。"));
    } finally {
      setBusy(false);
    }
  };

  // Portalled to <body>: .site-header's backdrop-filter would otherwise be the
  // containing block and stacking context for this fixed overlay.
  return createPortal(
    <div class="error-modal" id="chat-report-modal">
      <div class="error-modal-backdrop" onClick={onClose} />
      <div class="error-modal-box" role="dialog" aria-modal="true" aria-labelledby="chat-report-title">
        <div class="error-modal-hdr">
          <span id="chat-report-title" class="notice-modal-title">{props.jobId ? t("回報此結果") : t("回報問題")}</span>
          <button class="error-modal-close" type="button" aria-label={t("關閉")} onClick={onClose}>
            ×
          </button>
        </div>
        {done ? (
          <>
            <p class="chat-text">{props.jobId ? t("已回報，我們會盡快確認這筆結果。") : t("已回報，我們會盡快確認。")}</p>
            <div class="account-modal-actions">
              <button class="chat-chip" type="button" onClick={onClose}>
                {t("關閉")}
              </button>
            </div>
          </>
        ) : (
          <>
            <label class="report-modal-label" for="chat-report-name">
              {t("你的名稱")}
            </label>
            <input
              id="chat-report-name"
              class="account-modal-input"
              type="text"
              placeholder={t("輸入你的名稱")}
              maxLength={64}
              value={name}
              onInput={(e) => setName(e.currentTarget.value)}
            />
            <label class="report-modal-label" for="chat-report-note">
              {t("問題描述（選填）")}
            </label>
            <textarea
              id="chat-report-note"
              class="report-modal-textarea"
              placeholder={props.jobId ? t("請描述這筆結果的問題…") : t("請描述遇到的問題…")}
              maxLength={500}
              value={note}
              onInput={(e) => setNote(e.currentTarget.value)}
            />
            {error && <p class="account-modal-error">{error}</p>}
            <div class="account-modal-actions">
              <button class="chat-chip is-ghost" type="button" onClick={onClose}>
                {t("取消")}
              </button>
              <button class="chat-chip is-primary" type="button" disabled={busy} onClick={submit}>
                {busy ? t("送出中…") : t("送出回報")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}

// 面板模式 #changelog-modal 的版型，內容一樣讀舊頁面旁邊那份 changelog.json（新的在最前面，
// 只有最新一筆預設展開）。讀一次就留著，同一次開頁內容不會變。
type ChangelogEntry = { date?: string; changes?: string[] };
let changelogCache: ChangelogEntry[] | null = null;

function ChangelogModal(props: { onClose: () => void }) {
  const { onClose } = props;
  const [entries, setEntries] = useState(changelogCache);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [onClose]);
  useEffect(() => {
    if (changelogCache) return;
    let live = true;
    fetch("../changelog.json")
      .then((res) => res.json())
      .then((list: ChangelogEntry[]) => {
        changelogCache = list;
        if (live) setEntries(list);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, []);

  return createPortal(
    <div class="error-modal" id="chat-changelog-modal">
      <div class="error-modal-backdrop" onClick={onClose} />
      <div class="error-modal-box changelog-modal-box" role="dialog" aria-modal="true" aria-labelledby="chat-changelog-title">
        <div class="error-modal-hdr">
          <span id="chat-changelog-title" class="notice-modal-title">{t("版本紀錄")}</span>
          <button class="error-modal-close" type="button" aria-label={t("關閉")} onClick={onClose}>
            ×
          </button>
        </div>
        <div class="changelog-modal-body">
          {failed
            ? t("無法載入版本紀錄。")
            : entries?.map((entry, i) => (
                <details class="changelog-entry" open={i === 0} key={entry.date || i}>
                  <summary class="changelog-entry-date">{entry.date || ""}</summary>
                  <ul class="changelog-entry-list">
                    {(entry.changes || []).map((change) => (
                      <li key={change}>{change}</li>
                    ))}
                  </ul>
                </details>
              ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// 面板模式 #confirm-modal 的版型：一句話、兩個選項。Esc 跟點背景都算留下；
// 兩個選項都會往下走的，給 onDismiss，關掉就是兩個都不選。
export function ConfirmModal(props: {
  title?: string;
  message: string;
  confirmLabel: string;
  // 沒給就只有一顆確認鍵（像刪不掉時的那句提醒）。
  cancelLabel?: string;
  onAnswer: (ok: boolean) => void;
  onDismiss?: () => void;
}) {
  const { onAnswer } = props;
  const dismiss = props.onDismiss || (() => onAnswer(false));
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    addEventListener("keydown", esc);
    return () => removeEventListener("keydown", esc);
  }, [dismiss]);

  return createPortal(
    <div class="error-modal" id="chat-confirm-modal">
      <div class="error-modal-backdrop" onClick={dismiss} />
      <div class="error-modal-box yco-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="chat-confirm-title" aria-describedby="chat-confirm-msg">
        <button type="button" class="yco-confirm-close" aria-label={t("關閉")} onClick={dismiss}>
          <Icon name="x" size={24} />
        </button>
        <div class="yco-confirm-content">
          <h2 id="chat-confirm-title" class="yco-confirm-title">
            {props.title || t("請確認")}
          </h2>
          <p id="chat-confirm-msg" class="yco-confirm-message">
            {props.message}
          </p>
        </div>
        <div class="yco-confirm-actions">
          {props.cancelLabel && (
            <button class="ycoButton variant_secondary tone_neutral size_medium" type="button" autoFocus onClick={() => onAnswer(false)}>
              <span class="label">{props.cancelLabel}</span>
            </button>
          )}
          <button class="ycoButton variant_primary tone_brand size_medium" type="button" onClick={() => onAnswer(true)}>
            <span class="label">{props.confirmLabel}</span>
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// Same order as 面板模式's header, so switching modes moves nothing:
// 回到首頁 · 最近照片 · 版本紀錄 · 主題 · 回報. 新對話 (chat only, right after
// 回到首頁) opens the photo-picking page for another photo. The way over to
// 面板模式 is not here: it is the 對話｜面板 switch on the conversation's head
// (ChatPanel's panelMode), as 面板模式's way back is the same switch beside its
// step indicator.
// showChangelog: 版本紀錄 only on 首頁, as 面板模式 shows it only on its upload screen.
// showHistory is off on 首頁, whose gallery has its own 最近照片 tab.
// 右上角的語言切換：一顆地球，點開列出每個語言（用它自己的寫法），選了就換並重新載入。
// 偏好設定裡的「介面語言」照樣在，這顆是給還沒進對話、找不到設定的人。
// YCO 字標：「YouCam」原檔是黑色，改成跟著文字色（深色模式下變白）；「Online Editor」維持品牌藍。
// 原檔沒有深色版，所以內嵌 SVG、只換那一個 fill，其他照原檔。
const YCO_WORDMARK = ycoWordmarkSvg.replace('fill="black"', 'fill="currentColor"');
export function YcoWordmark() {
  return <span class="yco-wordmark" role="img" aria-label="YouCam Online Editor" dangerouslySetInnerHTML={{ __html: YCO_WORDMARK }} />;
}

// 淺色／深色切換（太陽／月亮）。深色用 YCO 的 dark token（../assets/yco/yco-tokens-dark.css，<html data-theme="dark">），
// 記在 localStorage，index.html 在第一次繪製前就套上。body[data-theme-mode] 一起切，PhotoMaster 只給淺色用的補丁才會關掉。
export const COLOR_MODE_KEY = "yco-color-mode";
function ColorModeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  const toggle = () => {
    const next = !dark;
    setDark(next);
    if (next) document.documentElement.dataset.theme = "dark";
    else delete document.documentElement.dataset.theme;
    document.body.dataset.themeMode = next ? "dark" : THEME_MODE[readTheme()];
    try {
      localStorage.setItem(COLOR_MODE_KEY, next ? "dark" : "light");
    } catch {
      /* storage may be blocked */
    }
  };
  const label = dark ? t("切換成淺色模式") : t("切換成深色模式");
  return (
    <button type="button" class="yco-mode-toggle" aria-label={label} title={label} aria-pressed={dark} onClick={toggle}>
      {/* Lucide「sun」／「moon」（ISC）：淺色時顯示月亮（按了變深色），深色時顯示太陽。 */}
      {dark ? (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" />
          <path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" />
        </svg>
      ) : (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401" />
        </svg>
      )}
    </button>
  );
}

// 語言鈕上的短標：English → EN、繁體中文 → 繁中，其他語言用代碼的主標籤（ja → JA）。
function langCode(code: string) {
  if (code === "en") return "EN";
  if (/^zh/i.test(code)) return /hant|tw|hk|mo/i.test(code) ? "繁中" : "简中";
  return code.split("-")[0].toUpperCase();
}

function LangSwitch(props: { debugUi: boolean }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      if (!(e.target as Element).closest?.(".lang-switch")) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const current = LANGS.find((l) => l.code === lang)?.name || lang;
  return (
    <div class="lang-switch">
      <button
        type="button"
        class="lang-switch-btn"
        aria-label={`${t("介面語言")}${t("：")}${current}`}
        title={`${t("介面語言")}${t("：")}${current}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {/* YCO header 的語言鈕是文字（EN），不是地球圖示。 */}
        <span class="lang-switch-code">{langCode(lang)}</span>
      </button>
      {open && (
        <div class="lang-switch-menu" role="menu">
          {selectableLanguages(props.debugUi).map((l) => (
            <button
              key={l.code}
              type="button"
              role="menuitemradio"
              aria-checked={l.code === lang}
              class={`lang-switch-item${l.code === lang ? " is-on" : ""}`}
              onClick={() => {
                setOpen(false);
                if (l.code !== lang) setLang(l.code);
              }}
            >
              <span>{l.name}</span>
              {l.code === lang && <Icon name="check" size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Header(props: {
  onHome: () => void;
  onHistory: () => void;
  showHome: boolean;
  onNewChat?: () => void;
  showHistory?: boolean;
  showChangelog?: boolean;
  // 開 header 的「設定」（對話風格）；彈窗由 app 負責。
  onSettings?: () => void;
  // The edit (or, before one, the analysis) on screen, which 回報 points at.
  jobId?: string | null;
  // The conversation on screen, so the admin can open it from the report.
  reportChat?: ReportChat;
  // 管理者從「回報清單」換到某筆回報前先問一聲（還沒開始美化的照片會清掉）。
  beforeLeave?: () => Promise<boolean>;
  // 首頁右上那顆地球：選介面語言。
  showLang?: boolean;
  debugUi?: boolean;
}) {
  const [reporting, setReporting] = useState(false);
  const [changelog, setChangelog] = useState(false);
  // 手機上 header 只放得下 logo 跟一顆 ⋯，其他按鈕收進這裡展開的選單（chat.css 只在窄螢幕
  // 把 .header-actions 變成選單；寬螢幕這顆 ⋯ 不顯示，選單狀態也就沒有作用）。
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => {
      if (!(e.target as Element).closest?.(".header-actions, .yco-avatar")) setMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [menu]);
  return (
    // YCO header（yco-collab-space NavigationHeader 的登入狀態）：左邊 logo，右邊語言＋頭像。
    // 原本攤在 header 上的按鈕（回到首頁、設定、回報…）收進頭像點開的選單，功能都還在。
    <header class="site-header yco-header">
      <div class="header-inner">
        {/* 手機版（YCO Mobile ≤768px）：左邊是漢堡選單 + logo 圖示，右邊只有頭像。漢堡開「最近照片」，沒有最近照片的畫面就開頭像選單。 */}
        <button
          type="button"
          class="chat-header-menu"
          aria-label={t("選單")}
          title={t("選單")}
          onClick={props.showHistory ? props.onHistory : () => setMenu(!menu)}
        >
          <Icon name="menu" size={22} />
        </button>
        <button type="button" class="logo yco-brand" id="logo-home-btn" aria-label="YouCam Online Editor" onClick={props.onHome}>
          <img class="yco-logo-symbol" src={ycoLogoSymbol} alt="" aria-hidden="true" />
          <YcoWordmark />
        </button>
        {/* 收成 ⋯ 的寬度，新對話跟最近照片不進選單，留在 header 上一鍵就到（寬螢幕不顯示，用攤開的那顆）。 */}
        {props.onNewChat && (
          <button type="button" class="chat-header-icon" aria-label={t("新對話")} title={t("新對話")} onClick={props.onNewChat}>
            <Icon name="plus" size={22} />
          </button>
        )}
        {/* 最近照片不放在 header 上（從頭像選單開）。 */}
        {/* header 上不放換語言、換色彩模式（EN／月亮）。 */}
        {/* YCO 的頭像：深色圓底＋白色人像。每個寬度都用它開選單（取代原本只在手機出現的 ⋯）。 */}
        <button
          type="button"
          class="yco-avatar"
          aria-label={t("更多選項")}
          title={t("更多選項")}
          aria-haspopup="menu"
          aria-expanded={menu}
          aria-controls="chat-header-actions"
          onClick={() => setMenu(!menu)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="8" r="4" />
            <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
          </svg>
        </button>
        {/* 選了哪一項選單就收起來；點擊從按鈕冒泡上來，按鈕自己的動作已經先跑完了。 */}
        <div class={`header-actions${menu ? " is-open" : ""}`} id="chat-header-actions" onClick={() => setMenu(false)}>
          {props.showHome && (
            <button id="chat-home-btn" class="btn btn-secondary btn-sm" type="button" onClick={props.onHome}>
              <Icon name="house" size={14} />
              <span class="btn-label">{t("回到首頁")}</span>
            </button>
          )}
          {props.onNewChat && (
            <button id="chat-new-btn" class="btn btn-secondary btn-sm" type="button" onClick={props.onNewChat}>
              <Icon name="plus" size={14} />
              <span class="btn-label">{t("新對話")}</span>
            </button>
          )}
          {props.showHistory && (
            <button id="chat-history-btn" class="btn btn-secondary btn-sm" type="button" onClick={props.onHistory}>
              <Icon name="clock" size={14} />
              <span class="btn-label">{t("最近照片")}</span>
            </button>
          )}
          {props.showChangelog && (
            <button id="chat-changelog-btn" class="btn btn-secondary btn-sm" type="button" onClick={() => setChangelog(true)}>
              <Icon name="changelog" size={14} />
              <span class="btn-label">{t("版本紀錄")}</span>
            </button>
          )}
          {props.onSettings && (
            <button id="chat-settings-btn" class="btn btn-secondary btn-sm" type="button" onClick={props.onSettings}>
              <Icon name="gear" size={14} />
              <span class="btn-label">{t("設定")}</span>
            </button>
          )}
          <ReportsButton beforeLeave={props.beforeLeave} />
          <ThemeToggle />
          <button
            id="chat-report-btn"
            class="btn btn-sm"
            type="button"
            title={props.jobId ? t("回報此結果") : t("回報問題")}
            onClick={() => setReporting(true)}
          >
            <Icon name="report" size={14} />
            <span class="btn-label">{t("回報")}</span>
          </button>
        </div>
      </div>
      {reporting && <ReportModal jobId={props.jobId || null} chat={props.reportChat || {}} onClose={() => setReporting(false)} />}
      {changelog && <ChangelogModal onClose={() => setChangelog(false)} />}
    </header>
  );
}

// Generated sample portrait (slightly messy hair, not smiling) and its 一鍵美化
// result, aligned and cropped to the same 4:5 frame so the slider lines up.
// H02：分隔線預設停在臉中間（鼻樑約在 48%），兩邊各露出半張臉，不用拖就看得出差別。
function HeroCompare() {
  // 進場：先整張都是 Before（分隔線在最右），1 秒內滑到正中間（各半）。
  // 使用者一碰滑桿就停下來，不跟他搶。減少動態效果（prefers-reduced-motion）就直接停在正中間。
  const REST_POS = 50;
  const SWEEP_MS = 1000;
  const [pos, setPos] = useState(100);
  const frame = useRef(0);
  const stopSweep = () => cancelAnimationFrame(frame.current);
  const play = () => {
    stopSweep();
    const rest = REST_POS;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setPos(rest); return; }
    const t0 = performance.now();
    setPos(100);
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / SWEEP_MS);
      const eased = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      setPos(100 + (rest - 100) * eased);
      if (p < 1) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  };
  useEffect(() => {
    play();
    return stopSweep;
  }, []);
  // 拉到某一邊、那一邊的標籤會被蓋住時把它藏起來：Before 標籤在左，左邊剩下的寬度（pos）放不下它就藏；
  // After 同理看右邊。拉到底時只剩對的那一個（整張都是 After 就只剩 After）。
  const box = useRef<HTMLDivElement>(null);
  const beforeTag = useRef<HTMLSpanElement>(null);
  const afterTag = useRef<HTMLSpanElement>(null);
  const width = box.current?.clientWidth || 0;
  const split = (width * pos) / 100;
  const b = beforeTag.current;
  const a = afterTag.current;
  const hideBefore = !!b && width > 0 && split < b.offsetLeft + b.offsetWidth;
  const hideAfter = !!a && width > 0 && width - split < width - a.offsetLeft;
  return (
    <div class="home-compare" ref={box} style={{ "--pos": `${pos}%` }}>
      <img class="home-compare-img" src={heroBefore} alt={t("修圖前")} />
      <img class="home-compare-img is-after" src={heroAfter} alt={t("修圖後")} />
      <span class="home-compare-line" aria-hidden="true">
        <span class="home-compare-knob">
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 6l-6 6 6 6M15 6l6 6-6 6" />
          </svg>
        </span>
      </span>
      <span ref={beforeTag} class={`home-compare-tag${hideBefore ? " is-hidden" : ""}`} aria-hidden={hideBefore || undefined}>Before</span>
      <span ref={afterTag} class={`home-compare-tag is-after${hideAfter ? " is-hidden" : ""}`} aria-hidden={hideAfter || undefined}>After</span>
      <input
        class="home-compare-range"
        type="range"
        min={0}
        max={100}
        value={Math.round(pos)}
        aria-label={t("拖曳比較修圖前後")}
        onPointerDown={stopSweep}
        onInput={(e) => { stopSweep(); setPos(Number(e.currentTarget.value)); }}
      />
    </div>
  );
}

// The headline's middle words are emphasised: the translation marks them with <em>.
const heroLine = t("開啟<em>專屬</em>美化體驗").split(/<\/?em>/);

export function HomeIntro({ onStart }: { onStart: () => void }) {
  return (
    <div class="home-intro">
      <div class="home-hero">
        <div class="home-hero-copy">
          <p class="home-eyebrow">{t("AI 個人化美化分析")}</p>
          <h1 class="home-headline">
            <span>{t("一張照片，")}</span>
            <span>
              {heroLine[0]}<em>{heroLine[1]}</em>{heroLine[2]}
            </span>
          </h1>
          <p class="home-lead">
            {t("AI 先判讀人物、光線、構圖與畫面細節，給你幾個美化方向，表情、姿勢、視角也能幫你調；你也可以直接說想怎麼修，它就照你的意思來。")}
          </p>
          <div class="home-start">
            <button type="button" class="btn btn-primary home-start-btn" onClick={onStart}>
              <Icon name="messages-circle" />
              {t("開始對話")}
            </button>
            <span class="chat-meta">{t("選一張照片，剩下的用聊的")}</span>
          </div>
        </div>
        <HeroCompare />
      </div>
    </div>
  );
}

const TILE_CHECK = (
  <span class="demo-image-tile-check">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
      <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </span>
);

function Tile(props: {
  src: string;
  title: string;
  selected: boolean;
  // 多選時是第幾張，勾勾換成這個數字。
  order?: number;
  onClick: () => void;
  onImgError?: () => void;
  children?: ComponentChildren;
}) {
  return (
    <button
      type="button"
      class={`demo-image-tile${props.selected ? " is-selected" : ""}`}
      title={props.title}
      onClick={props.onClick}
    >
      <img class="demo-image-tile-bg" src={props.src} alt="" loading="lazy" />
      <img src={props.src} alt={props.title} loading="lazy" onError={props.onImgError} />
      {props.order ? <span class="demo-image-tile-check is-order">{props.order}</span> : TILE_CHECK}
      {props.children}
    </button>
  );
}

// Same words and glyphs the old UI's history grid uses, so a conversation
// reads the same in both. A session is many photos, not one job, so the
// state is the whole conversation's: anything still running wins over a
// result, and 失敗 only when nothing in it survived.
const SESSION_BADGES: Record<SessionState, string> = {
  analyzing: t("分析中"),
  rendering: t("美化中"),
  ready: t("待美化"),
  done: t("美化完成"),
  failed: t("失敗"),
};

// 每格右上角的 ×，跟面板模式最近照片那顆同一個樣子（.history-cube-remove）。磚本身是
// <button>，裡面不能再放一顆按鈕，所以這顆用 span 當按鈕，點了不往外傳。
function RemoveMark({ onRemove }: { onRemove: () => void }) {
  const go = (e: Event) => {
    e.stopPropagation();
    e.preventDefault();
    onRemove();
  };
  return (
    <span
      class="history-cube-remove chat-tile-remove"
      role="button"
      tabIndex={0}
      aria-label={t("刪除紀錄")}
      title={t("刪除紀錄")}
      onClick={go}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && go(e)}
    >
      ×
    </span>
  );
}

export function SessionTile(props: { s: SessionSummary; selected: boolean; onClick: () => void; onRemove?: () => void }) {
  const { s } = props;
  // A result can outlive nothing else here, but its thumbnail is generated
  // on demand from job artifacts the session (30 days) can outlast -- so a
  // result that will not load falls back to the original rather than
  // leaving the tile blank. Keyed by job id so the next render of a
  // different session in this slot starts over.
  const [brokenJob, setBrokenJob] = useState<string | null>(null);
  const result = s.thumbnail_result;
  const showResult = result && result.job_id !== brokenJob;
  const src = showResult
    ? urls.resultThumb(result.job_id, result.run)
    : s.thumbnail_job_id
      ? urls.sourceThumb(s.thumbnail_job_id)
      : "";
  const label = SESSION_BADGES[s.status];
  return (
    <Tile
      src={src}
      title={timeLabel(s.updated_at)}
      selected={props.selected}
      onClick={props.onClick}
      onImgError={showResult ? () => setBrokenJob(result.job_id) : undefined}
    >
      {s.photo_count > 1 && <SessionMosaic s={s} />}
      {label && <span class={`session-tile-badge is-${s.status}`}>{label}</span>}
      {props.onRemove && <RemoveMark onRemove={props.onRemove} />}
      {/* 只有手機首頁的那一排會顯示（chat.css）：那裡沒有日期標題，時間就寫在磚下面。 */}
      <span class="session-tile-when" aria-hidden="true">{timeLabel(s.updated_at)}</span>
    </Tile>
  );
}

// 一段對話有好幾張時，磚裡拼前幾張，一眼看得出是一組：兩張左右對分，三張一大兩小，
// 四張田字；超過四張，最後一格蓋上「+N」。角落再寫總共幾張。蓋在 Tile 自己那張圖上面，
// 那張照樣當模糊底圖。
function SessionMosaic({ s }: { s: SessionSummary }) {
  const shots = (s.thumbnails || []).slice(0, 4);
  const more = s.photo_count - shots.length;
  return (
    <>
      {shots.length > 1 && (
        <span class={`session-tile-mosaic is-${shots.length}`} aria-hidden="true">
          {shots.map((t, i) => (
            <span key={i} class="session-tile-cell">
              <img src={t.run ? urls.resultThumb(t.job_id, t.run) : urls.sourceThumb(t.job_id)} alt="" loading="lazy" />
              {more > 0 && i === shots.length - 1 && <b>+{more}</b>}
            </span>
          ))}
        </span>
      )}
      <span class="session-tile-count">{t("{n} 張", { n: s.photo_count })}</span>
    </>
  );
}

// What a row is picked by: its session, or for a history case with none,
// `case:<case_id>` -- openSession tells the two apart.
export const CASE_PREFIX = "case:";
export function rowId(s: SessionSummary): string {
  return s.session_id ?? `${CASE_PREFIX}${s.case_id}`;
}

// Sessions arrive newest first, so a day's run of them is contiguous: one
// group per calendar day, in that same order. The tiles carry no date of
// their own any more -- the heading is where the date lives, which is how
// the old UI's history list reads too (renderHistoryList, app.js).
function byDay(sessions: SessionSummary[]): { label: string; rows: SessionSummary[] }[] {
  const days: { label: string; rows: SessionSummary[] }[] = [];
  for (const s of sessions) {
    const label = dayLabel(s.updated_at);
    const open = days[days.length - 1];
    if (open && open.label === label) open.rows.push(s);
    else days.push({ label, rows: [s] });
  }
  return days;
}

// Each day gets its own grid rather than one grid with headings dropped in:
// the mosaic packs densely, and a shared grid would let tomorrow's photo
// backfill a hole today's left -- above its own heading. Separate grids also
// restart the enlarged-tile rhythm per day, as the old UI's .history-grid does.
function SessionDays(props: {
  sessions: SessionSummary[];
  gridClass: string;
  selectedId: string | null;
  onPick: (id: string) => void;
  // 多選刪除時，哪幾格是勾起來的（有給就不看 selectedId）。
  picked?: Set<string>;
  onRemove?: (id: string) => void;
}) {
  return (
    <>
      {byDay(props.sessions).map((day) => (
        <div class="history-day-group" key={day.label}>
          <h3 class="history-day-heading">{day.label}</h3>
          <div class={props.gridClass}>
            {day.rows.map((s) => (
              <SessionTile
                key={rowId(s)}
                s={s}
                selected={props.picked ? props.picked.has(rowId(s)) : rowId(s) === props.selectedId || (!!props.selectedId && !!s.session_ids?.includes(props.selectedId))}
                onClick={() => props.onPick(rowId(s))}
                onRemove={props.onRemove && (() => props.onRemove!(rowId(s)))}
              />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

export type GalleryTab = "recent" | "sample";
// 最近照片一次畫幾格。
const GALLERY_PAGE = 12;

// 最近照片一次只畫一段，捲到尾巴附近再接下一段。整份紀錄（最多幾百格、每格最多
// 四張縮圖）一起畫出來的話，lazy loading 的預載範圍很大，一進來就會排隊抓上百張
// 縮圖。首頁那一排（或那一欄）和抽屜都用這個：tail 掛在清單最後，看得到就再畫一段。
function usePages(total: number, active: boolean) {
  const [shown, setShown] = useState(GALLERY_PAGE);
  const tail = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = tail.current;
    if (!active || !el || shown >= total) return;
    const io = new IntersectionObserver((seen) => seen.some((e) => e.isIntersecting) && setShown((n) => n + GALLERY_PAGE), {
      rootMargin: "300px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, [active, shown, total]);
  return { shown, tail, more: shown < total };
}
// 範例照片可以多選，names 照點的順序排（多張頁就照這個順序）。
export type GallerySelection = { kind: "session"; id: string } | { kind: "demo"; names: string[] } | null;

export function Gallery(props: {
  tab: GalleryTab;
  onTab: (t: GalleryTab) => void;
  sessions: SessionSummary[] | null;
  demos: string[] | null;
  selection: GallerySelection;
  onSelect: (s: GallerySelection) => void;
  onConfirm: () => void;
  // 範例照片最多選幾張。
  max?: number;
  onRemove?: (id: string) => void;
  // 手機上那一排只放得下幾張，「全部」打開完整的清單：最近照片開側欄，範例照片開範例視窗。
  onAll?: (t: GalleryTab) => void;
  // 最近照片空狀態的「上傳照片」：打開選檔（跟首頁其他上傳同一個 input）。
  onUpload?: () => void;
  // 拖進空狀態那塊的照片，走跟選檔同一條路。
  onDropFiles?: (files: File[]) => void;
}) {
  const { tab, selection } = props;
  const [dragOver, setDragOver] = useState(false);
  const recent = tab === "recent";
  const list = recent ? props.sessions : props.demos;
  const sessions = props.sessions || [];
  const { shown, tail, more } = usePages(sessions.length, recent);
  const names = selection?.kind === "demo" ? selection.names : [];
  // 點一下選、再點一下取消；選滿了再點沒選的不會加。
  const toggle = (name: string) => {
    const next = names.includes(name) ? names.filter((n) => n !== name)
      : names.length >= (props.max ?? 1) ? names : [...names, name];
    props.onSelect(next.length ? { kind: "demo", names: next } : null);
  };
  return (
    <div class={`upload-gallery-col is-history${recent ? " showing-recent" : ""}`}>
      <div class="upload-gallery-heading">
        <div class="upload-gallery-tabs" id="upload-gallery-tabs" role="tablist">
          <div class="upload-gallery-tab-recent-group">
            <button
              type="button"
              class={`upload-gallery-tab${recent ? " is-active" : ""}`}
              id="upload-gallery-tab-recent"
              role="tab"
              aria-selected={recent}
              onClick={() => props.onTab("recent")}
            >
              {/* G01：YCO 分頁用短標籤、不放圖示（Recent／Samples）。 */}
              {t("最近")}
            </button>
          </div>
          <button
            type="button"
            class={`upload-gallery-tab${recent ? "" : " is-active"}`}
            id="upload-gallery-tab-sample"
            role="tab"
            aria-selected={!recent}
            onClick={() => props.onTab("sample")}
          >
            {t("範例")}
          </button>
        </div>
        {props.onAll && (
          <button type="button" class="upload-gallery-all" onClick={() => props.onAll!(tab)}>
            {t("全部")}
            <Icon name="next" size={16} />
          </button>
        )}
      </div>
      <div class="upload-gallery-scroll-frame">
        <div
          id="demo-images-grid"
          class={`demo-images-grid${list === null ? " is-loading" : ""}`}
          aria-busy={list === null}
          aria-label={list === null ? t("正在載入照片") : undefined}
        >
          {list === null
            ? Array.from({ length: 6 }, (_, index) => (
                <div class="demo-image-tile gallery-skeleton-tile" aria-hidden="true" key={index} />
              ))
            : recent
            ? (
                <>
                  <SessionDays
                    sessions={sessions.slice(0, shown)}
                    gridClass="history-grid"
                    selectedId={selection?.kind === "session" ? selection.id : null}
                    onPick={(id) => props.onSelect({ kind: "session", id })}
                    onRemove={props.onRemove}
                  />
                  {more && <span ref={tail} class="gallery-more" aria-hidden="true" />}
                </>
              )
            : (props.demos || []).map((name) => (
                <Tile
                  src={urls.demoThumb(name)}
                  title={name}
                  selected={names.includes(name)}
                  order={names.length > 1 && names.includes(name) ? names.indexOf(name) + 1 : undefined}
                  onClick={() => toggle(name)}
                />
              ))}
        </div>
      </div>
      {/* G03：最近照片是空的時候給下一步：上傳照片。 */}
      {recent && list && list.length === 0 ? (
        // 整塊都可以點（開選檔）、也可以把照片拖進來；鍵盤和報讀用裡面那顆「上傳」，這塊本身不另外搶焦點。
        <div
          id="demo-images-empty"
          class={`gallery-empty${props.onUpload ? " is-dropzone" : ""}${dragOver ? " is-dragover" : ""}`}
          title={props.onUpload ? t("拖曳照片到這裡，或點一下上傳") : undefined}
          onClick={(e) => {
            if (!props.onUpload || (e.target as Element).closest("button")) return;
            props.onUpload();
          }}
          onDragOver={(e) => {
            if (!props.onDropFiles || !e.dataTransfer?.types.includes("Files")) return;
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            if (!(e.currentTarget as Element).contains(e.relatedTarget as Node)) setDragOver(false);
          }}
          onDrop={(e) => {
            if (!props.onDropFiles) return;
            e.preventDefault();
            setDragOver(false);
            const files = Array.from(e.dataTransfer?.files || []);
            if (files.length) props.onDropFiles(files);
          }}
        >
          <span class="gallery-empty-icon" aria-hidden="true">
            {/* Lucide「upload」（ISC，lucide.dev/icons/upload）。 */}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v12" />
              <path d="m17 8-5-5-5 5" />
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            </svg>
          </span>
          <p class="gallery-empty-title">{t("還沒有最近照片")}</p>
          <p class="gallery-empty-sub">{t("修過的照片會出現在這裡。")}</p>
          {props.onUpload && (
            // YCO Button：Primary · Brand · Small。
            <button
              type="button"
              class="ycoButton variant_primary tone_brand size_small gallery-empty-cta"
              onClick={(e) => {
                e.stopPropagation();
                props.onUpload!();
              }}
            >
              <span class="label">{t("上傳")}</span>
            </button>
          )}
        </div>
      ) : (
        <p id="demo-images-empty" class="history-empty" hidden={!list || list.length > 0}>
          {t("目前沒有可顯示的照片。")}
        </p>
      )}
      {!recent && (
        // YCO Button（yco-collab-space platform/ui/button）：Primary · Brand · Medium（42px 高），class 名照它的 CSS
        // （../assets/yco/yco-button.css，原檔照抄）。id 不用舊的 #demo-images-confirm-btn：面板模式的
        // inline style 也會帶進這裡，那個 id 的底色會蓋過 YCO 的樣式。
        <button
          id="chat-use-selected-btn"
          class="ycoButton variant_primary tone_brand size_medium gallery-confirm-btn"
          type="button"
          disabled={!names.length}
          onClick={props.onConfirm}
        >
          <span class="label">
            {names.length > 1 ? t("使用所選照片（{length} 張）", { length: names.length }) : t("使用所選照片")}
          </span>
        </button>
      )}
    </div>
  );
}

// 跟面板模式的最近照片一樣：每格一顆 ×；標題旁「選取」進入多選。多選時是「全選／取消／刪除」
// 三顆文字按鈕，刪除放最後；要全部清掉就先全選再刪。按鈕的 id 沿用舊頁面的，樣式從舊頁面 index.html
// 的那段 style 帶進來，「全選」「刪除」改成文字的部分在 chat.css。刪之前要不要確定、刪完怎麼辦，交給 app.tsx。
export function HistoryDrawer(props: {
  open: boolean;
  sessions: SessionSummary[] | null;
  currentId: string | null;
  onOpen: (id: string) => void;
  onClose: () => void;
  // 刪掉這幾格；回傳真的刪了沒（取消或失敗是 false）。
  onRemove: (ids: string[]) => Promise<boolean>;
  onClearAll: () => Promise<boolean>;
}) {
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  // 收起來的抽屜只是縮小、透明擺在畫面上，裡面的縮圖照樣會被載入。第一次打開前先不
  // 畫清單；打開過就留著，收起的動畫和下次打開都不用重畫。
  const [opened, setOpened] = useState(props.open);
  if (props.open && !opened) setOpened(true);
  const drawn = usePages(props.sessions?.length || 0, props.open);
  const stopPicking = () => {
    setPicking(false);
    setPicked(new Set());
  };
  // 抽屜收起來就不帶著多選狀態，下次打開是一般的樣子。
  useEffect(() => {
    if (!props.open) stopPicking();
  }, [props.open]);
  const toggle = (id: string) =>
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const hasRows = !!props.sessions?.length;
  const count = picked.size;
  const allIds = (props.sessions || []).map(rowId);
  const allPicked = hasRows && allIds.every((id) => picked.has(id));
  const delHint = allPicked ? t("全部刪除") : count ? t("刪除所選（{count} 張）", { count }) : t("刪除所選");
  return (
    <>
      <div class={`history-drawer-backdrop${props.open ? " drawer-open" : ""}`} onClick={props.onClose} />
      <aside class={`history-drawer${props.open ? " drawer-open" : ""}`} role="dialog" aria-label={t("最近照片")}>
        <button class="history-drawer-close-btn" type="button" aria-label={t("關閉")} onClick={props.onClose}>
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24">
            <path
              d="M18 6l-4 4m0 0h4m-4 0V6M6 18l4-4m0 0H6m4 0v4"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div class="history-header">
          <div class="history-header-top">
            <h2>{t("最近照片")}</h2>
            {hasRows && (
              <div class="history-header-actions">
                {picking ? (
                  <>
                    <button
                      id="history-select-all-btn"
                      class="btn btn-outline btn-sm"
                      type="button"
                      aria-pressed={allPicked}
                      onClick={() => setPicked(allPicked ? new Set() : new Set(allIds))}
                    >
                      {allPicked ? t("取消全選") : t("全選")}
                    </button>
                    <button id="history-select-cancel-btn" class="btn btn-outline btn-sm" type="button" onClick={stopPicking}>
                      {t("取消")}
                    </button>
                    <button
                      id="history-delete-selected-btn"
                      class="btn btn-outline btn-sm"
                      type="button"
                      disabled={!count}
                      title={delHint}
                      onClick={async () => {
                        // 全選時走「全部刪除」：確認文字講清楚是全部，也是一次清空的那支 API。
                        if (allPicked) {
                          if (await props.onClearAll()) stopPicking();
                        } else if (await props.onRemove([...picked])) stopPicking();
                      }}
                    >
                      {t("刪除")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      id="history-select-btn"
                      class="btn btn-outline btn-sm"
                      type="button"
                      onClick={() => setPicking(true)}
                    >
                      <svg width="14" height="14" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                        <rect x="3" y="3" width="18" height="18" rx="4" stroke="currentColor" stroke-width="2" />
                        <path d="M8 12.5l2.5 2.5L16 9.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                      {t("選取")}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
        <div class={`history-list${picking ? " chat-picking" : ""}`}>
          {opened && (
            <>
              <SessionDays
                sessions={(props.sessions || []).slice(0, drawn.shown)}
                gridClass="demo-images-grid chat-history-grid"
                selectedId={props.currentId}
                onPick={picking ? toggle : props.onOpen}
                picked={picking ? picked : undefined}
                onRemove={picking ? undefined : (id) => props.onRemove([id])}
              />
              {drawn.more && <span ref={drawn.tail} class="gallery-more" aria-hidden="true" />}
            </>
          )}
        </div>
        <p class="history-empty" hidden={!props.sessions || props.sessions.length > 0}>
          {t("目前還沒有照片。")}
        </p>
      </aside>
    </>
  );
}
