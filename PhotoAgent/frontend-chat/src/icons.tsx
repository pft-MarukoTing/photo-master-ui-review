import type { JSX } from "preact";

// mirror: points along the reading direction, so it flips in a right-to-left page.
const ICONS: Record<string, { sw: number; body: JSX.Element; mirror?: boolean }> = {
  chat: { sw: 2, body: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /> },
  "messages-circle": {
    sw: 2,
    body: (
      <>
        <path d="M19.95 10.05a7 7 0 0 1 1.412 7.872 1 1 0 0 0-.058.787l.675 2.089a1 1 0 0 1-1.236 1.168l-2.155-.631a1 1 0 0 0-.745.06 7 7 0 0 1-7.793-1.445" />
        <path d="M2.696 12.708a1 1 0 0 0-.058-.785 7 7 0 1 1 3.518 3.473 1 1 0 0 0-.744-.061l-2.155.63a1 1 0 0 1-1.236-1.167z" />
      </>
    ),
  },
  spark: { sw: 2, body: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /> },
  // Lucide「sparkles」（ISC，lucide.dev/icons/sparkles）。
  sparkles: {
    sw: 2,
    body: (
      <>
        <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />
        <path d="M5 3v4M3 5h4M19 17v4M17 19h4" />
      </>
    ),
  },
  redo: {
    sw: 2.2,
    mirror: true,
    body: (
      <>
        <path d="M20 11a8 8 0 1 0-2.34 5.66" />
        <path d="M20 4v7h-7" />
      </>
    ),
  },
  undo: {
    sw: 2.2,
    mirror: true,
    body: (
      <>
        <path d="M4 11a8 8 0 1 1 2.34 5.66" />
        <path d="M4 4v7h7" />
      </>
    ),
  },
  // 設定（這張照片的）：面板 #ap-case-settings-btn 那顆兩條線的滑桿。滑桿只代表
  // 設定；「看美化項目」用 pencil、「分析」用 search，齒輪是 header 的全域設定。
  tune: {
    sw: 1.8,
    body: (
      <>
        <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
        <circle cx="16" cy="7" r="2.2" />
        <circle cx="10" cy="17" r="2.2" />
      </>
    ),
  },
  // 看美化項目：回頭看分析、改這次的美化內容，是動作不是設定，所以不用滑桿。
  pencil: { sw: 2, body: <path d="M4 20h4L19 9a2.83 2.83 0 0 0-4-4L4 16v4zM13.5 6.5l4 4" /> },
  // 下載：跟編輯器畫布上的下載鈕（frontend/index.html #download-link）同一個圖。
  down: { sw: 2, body: <path d="M12 15V3M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5" /> },
  // 長寬比：Lucide「square-dimensions」（https://lucide.dev/icons/square-dimensions）。
  dimensions: {
    sw: 2,
    body: (
      <>
        <rect width="18" height="18" x="3" y="3" rx="2" />
        <path d="M12 7H7v5" />
        <path d="M12 17h5v-5" />
      </>
    ),
  },
  image: {
    sw: 2,
    body: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="3" />
        <circle cx="9" cy="10" r="2" />
        <path d="M21 16l-5-5-9 9" />
      </>
    ),
  },
  // 「我的照片」抽屜鈕：跟舊版 #batch-drawer-trigger 同一個疊兩張的圖。
  photos: {
    sw: 2,
    body: (
      <>
        <path d="M8 8V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-3" />
        <rect x="3" y="8" width="13" height="13" rx="2" />
      </>
    ),
  },
  up: { sw: 2, body: <path d="M12 19V6M6 11l6-6 6 6" /> },
  "chevron-up": { sw: 2, body: <path d="M6 15l6-6 6 6" /> },
  search: {
    sw: 2,
    body: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-4-4" />
      </>
    ),
  },
  x: { sw: 2, body: <path d="M6 6l12 12M18 6L6 18" /> },
  plus: { sw: 2, body: <path d="M12 5v14M5 12h14" /> },
  trash: {
    sw: 2.5,
    body: (
      <>
        <path d="M4 7h16" />
        <path d="M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7" />
        <path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
      </>
    ),
  },
  house: {
    sw: 2.5,
    body: (
      <>
        <path d="M3 11l9-8 9 8" />
        <path d="M5 10v9a1 1 0 001 1h4v-6h4v6h4a1 1 0 001-1v-9" />
      </>
    ),
  },
  check: { sw: 3, body: <path d="M20 6L9 17l-5-5" /> },
  arrow: { sw: 2.5, mirror: true, body: <path d="M5 12h14M13 6l6 6-6 6" /> },
  // The option categories, as the old UI's review tabs and 設定 button draw them.
  person: {
    sw: 1.8,
    body: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 20c0-4 3.5-6 8-6s8 2 8 6" />
      </>
    ),
  },
  recipe: {
    sw: 1.8,
    body: (
      <>
        <rect x="5" y="4" width="14" height="17" rx="2.5" />
        <path d="M8.5 10.5l1.8 1.8L14.5 8M8.5 16l1.8 1.8L14.5 13.5" />
      </>
    ),
  },
  wand: {
    sw: 2.6,
    body: (
      <>
        <path d="M3.5 20.5L12 12" />
        <path d="M16 2.5l1.7 4L21.5 8l-3.8 1.5L16 13.5l-1.7-4L10.5 8l3.8-1.5L16 2.5z" fill="currentColor" stroke="none" />
      </>
    ),
  },
  note: { sw: 1.8, body: <path d="M4 4h16v12H8l-4 4V4z" /> },
  gear: {
    sw: 2,
    body: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09a1.65 1.65 0 001.51-1.08 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51h.08a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" />
      </>
    ),
  },
  // 手機 header 的漢堡選單（lucide menu）。
  menu: {
    sw: 2,
    body: (
      <>
        <path d="M4 6h16" />
        <path d="M4 12h16" />
        <path d="M4 18h16" />
      </>
    ),
  },
  // 最近照片: the old header's history clock, so both modes draw it the same.
  clock: {
    sw: 2.5,
    body: (
      <>
        <path d="M12 8v4l3 3" />
        <path d="M3.5 9a8.5 8.5 0 1 1-1 4.5" />
        <path d="M2 5v4h4" />
      </>
    ),
  },
  // 版本紀錄: the old #changelog-btn's document with lines.
  changelog: {
    sw: 2,
    body: (
      <>
        <path d="M6 4h9l3 3v13a1 1 0 01-1 1H6a1 1 0 01-1-1V5a1 1 0 011-1z" />
        <path d="M9 10h6M9 14h6M9 18h3" />
      </>
    ),
  },
  // 回報: the old #report-btn's bubble with an exclamation mark.
  // 回報清單（管理者）：收件匣，跟「回報」那顆對話框分開。
  inbox: {
    sw: 2.2,
    body: (
      <>
        <path d="M3 13h5l1.5 3h5l1.5-3h5" />
        <path d="M5.5 5h13L21 13v6H3v-6z" />
      </>
    ),
  },
  report: {
    sw: 2.5,
    body: (
      <>
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
        <path d="M12 7.5v4.5" />
        <path d="M12 15.01h.01" />
      </>
    ),
  },
  layout: {
    sw: 2,
    body: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="3" />
        <path d="M9 4v16" />
      </>
    ),
  },
  // Lucide「info」（ISC，lucide.dev/icons/info）。
  info: {
    sw: 2,
    body: (
      <>
        <circle cx="12" cy="12" r="10" />
        <path d="M12 16v-4" />
        <path d="M12 8h.01" />
      </>
    ),
  },
  more: { sw: 3.2, body: <path d="M5 12h.01M12 12h.01M19 12h.01" /> },
  next: { sw: 2, mirror: true, body: <path d="M9 6l6 6-6 6" /> },
  prev: { sw: 2, mirror: true, body: <path d="M15 6l-6 6 6 6" /> },
  globe: {
    sw: 1.8,
    body: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9M12 3c-2.5 2.6-3.8 5.6-3.8 9s1.3 6.4 3.8 9" />
      </>
    ),
  },
  caret: { sw: 2, body: <path d="M6 9l6 6 6-6" /> },
  // 全螢幕／放大：右上箭頭＋左下箭頭（兩個模式、所有放大／全螢幕的地方都用這個）。
  expand: { sw: 2, body: <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" /> },
};

export function Icon({ name, size }: { name: keyof typeof ICONS | string; size?: number }) {
  const icon = ICONS[name];
  if (!icon) return null;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      stroke-width={icon.sw}
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      class={icon.mirror ? "icon-mirror" : undefined}
    >
      {icon.body}
    </svg>
  );
}
