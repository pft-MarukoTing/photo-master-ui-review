// Theme management: follows system preference, with manual toggle

import { IconSun, IconMoon } from './icons.js';

let buttonEl = null;

function getSystemDark() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function apply() {
  const isDark = document.documentElement.classList.contains('dark');
  if (buttonEl) {
    buttonEl.innerHTML = isDark ? IconMoon('icon-sm') : IconSun('icon-sm');
    buttonEl.title = isDark ? 'Dark mode' : 'Light mode';
  }
}

export function cycleTheme() {
  const isDark = document.documentElement.classList.contains('dark');
  document.documentElement.classList.toggle('dark', !isDark);
  apply();
}

export function initTheme(btn) {
  buttonEl = btn;

  // Apply system theme on load
  document.documentElement.classList.toggle('dark', getSystemDark());
  apply();

  // Follow system preference changes
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    document.documentElement.classList.toggle('dark', e.matches);
    apply();
  });
}
