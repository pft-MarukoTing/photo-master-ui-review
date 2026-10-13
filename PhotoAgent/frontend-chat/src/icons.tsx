import type { JSX } from "preact";

const ICONS: Record<string, { sw: number; body: JSX.Element }> = {
  chat: { sw: 2, body: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /> },
  spark: { sw: 2, body: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /> },
  redo: {
    sw: 2.2,
    body: (
      <>
        <path d="M20 11a8 8 0 1 0-2.34 5.66" />
        <path d="M20 4v7h-7" />
      </>
    ),
  },
  undo: {
    sw: 2.2,
    body: (
      <>
        <path d="M4 11a8 8 0 1 1 2.34 5.66" />
        <path d="M4 4v7h7" />
      </>
    ),
  },
  sliders: {
    sw: 2.2,
    body: (
      <>
        <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" />
        <circle cx="16" cy="6" r="2" />
        <circle cx="10" cy="12" r="2" />
        <circle cx="18" cy="18" r="2" />
      </>
    ),
  },
  down: { sw: 2, body: <path d="M12 4v11M7 10l5 5 5-5M5 20h14" /> },
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
  up: { sw: 2, body: <path d="M12 19V6M6 11l6-6 6 6" /> },
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
  arrow: { sw: 2.5, body: <path d="M5 12h14M13 6l6 6-6 6" /> },
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
  // 回報: the old #report-btn's bubble with an exclamation mark.
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
  more: { sw: 3.2, body: <path d="M5 12h.01M12 12h.01M19 12h.01" /> },
  next: { sw: 2, body: <path d="M9 6l6 6-6 6" /> },
  expand: { sw: 2, body: <path d="M14 4h6v6M10 20H4v-6M20 4l-6 6M4 20l6-6" /> },
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
    >
      {icon.body}
    </svg>
  );
}
