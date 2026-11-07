// SVG icon helpers — each returns an SVG string.
// Use CSS classes .icon-sm (16px), .icon-md (24px), .icon-lg (48px) to size.

const attrs = (cls = 'icon-md') =>
  `class="${cls}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"`;

export function IconUpload(cls) {
  return `<svg ${attrs(cls)}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`;
}

export function IconPlay(cls) {
  return `<svg ${attrs(cls)}><polygon points="6 3 20 12 6 21 6 3"/></svg>`;
}

export function IconPause(cls) {
  return `<svg ${attrs(cls)}><rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/></svg>`;
}

export function IconDownload(cls) {
  return `<svg ${attrs(cls)}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
}

export function IconUploadCloud(cls) {
  return `<svg ${attrs(cls)}><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="M12 12v9"/><path d="m16 16-4-4-4 4"/></svg>`;
}

export function IconMoon(cls) {
  return `<svg ${attrs(cls)}><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>`;
}

export function IconSun(cls) {
  return `<svg ${attrs(cls)}><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>`;
}

export function IconPanelLeftOpen(cls) {
  return `<svg ${attrs(cls)}><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="m14 9 3 3-3 3"/></svg>`;
}

export function IconPanelLeftClose(cls) {
  return `<svg ${attrs(cls)}><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="m16 15-3-3 3-3"/></svg>`;
}

export function IconLoader2(cls) {
  return `<svg ${attrs(cls)}><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`;
}

export function IconX(cls) {
  return `<svg ${attrs(cls)}><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`;
}

export function IconMenu(cls) {
  return `<svg ${attrs(cls)}><line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="18" y2="18"/></svg>`;
}

export function IconChevronUp(cls) {
  return `<svg ${attrs(cls)}><path d="m18 15-6-6-6 6"/></svg>`;
}

export function IconChevronDown(cls) {
  return `<svg ${attrs(cls)}><path d="m6 9 6 6 6-6"/></svg>`;
}

export function IconReplace(cls) {
  return `<svg ${attrs(cls)}><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>`;
}

export function IconType(cls) {
  return `<svg ${attrs(cls)}><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>`;
}

export function IconMaximize(cls) {
  return `<svg ${attrs(cls)}><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>`;
}

export function IconEdit(cls) {
  return `<svg ${attrs(cls)}><path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>`;
}

export function IconUndo(cls) {
  return `<svg ${attrs(cls)}><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/></svg>`;
}

export function IconRedo(cls) {
  return `<svg ${attrs(cls)}><path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"/></svg>`;
}

export function IconCircleCheck(cls) {
  return `<svg ${attrs(cls)}><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>`;
}

export function IconHome(cls) {
  return `<svg ${attrs(cls)}><path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`;
}

export function IconEraser(cls) {
  return `<svg ${attrs(cls)}><path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/></svg>`;
}

export function IconSticker(cls) {
  return `<svg ${attrs(cls)}><path d="M15.5 3H5a2 2 0 0 0-2 2v14c0 1.1.9 2 2 2h14a2 2 0 0 0 2-2V8.5L15.5 3Z"/><path d="M14 3v6h7"/><circle cx="10" cy="13" r="2"/><path d="m20 17-1.09-1.09a2 2 0 0 0-2.82 0L10 22"/></svg>`;
}
