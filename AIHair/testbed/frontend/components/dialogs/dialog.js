// Custom dialog: Escape close, overlay click close, focus trap, scroll lock

import { IconX } from '../../utils/icons.js';

const activeDialogs = new Map(); // id → { overlay, content, previousFocus, onClose }

function trapFocus(e, contentEl) {
  const focusable = contentEl.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.key === 'Tab') {
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }
}

function onKeyDown(e) {
  if (e.key === 'Escape') {
    // Close the topmost dialog
    const ids = [...activeDialogs.keys()];
    if (ids.length > 0) {
      closeDialog(ids[ids.length - 1]);
    }
  }
}

export function openDialog({ id, title, description, body, footer, onClose, large }) {
  // Prevent duplicates
  if (activeDialogs.has(id)) closeDialog(id);

  const previousFocus = document.activeElement;

  // Overlay
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.addEventListener('click', () => closeDialog(id));

  // Content wrapper
  const content = document.createElement('div');
  content.className = 'dialog-content' + (large ? ' dialog-content-lg' : '');
  content.setAttribute('role', 'dialog');
  content.setAttribute('aria-modal', 'true');
  content.tabIndex = -1;

  // Close button
  const closeBtn = document.createElement('button');
  closeBtn.className = 'dialog-close-btn';
  closeBtn.innerHTML = IconX('icon-sm') + '<span class="sr-only">Close</span>';
  closeBtn.addEventListener('click', () => closeDialog(id));
  content.appendChild(closeBtn);

  // Header
  if (title || description) {
    const header = document.createElement('div');
    header.className = 'dialog-header';
    if (title) {
      const h = document.createElement('h2');
      h.className = 'dialog-title';
      h.textContent = title;
      header.appendChild(h);
    }
    if (description) {
      const p = document.createElement('p');
      p.className = 'dialog-description';
      p.textContent = description;
      header.appendChild(p);
    }
    content.appendChild(header);
  }

  // Body
  if (body) {
    if (typeof body === 'string') {
      const div = document.createElement('div');
      div.innerHTML = body;
      content.appendChild(div);
    } else {
      content.appendChild(body);
    }
  }

  // Footer
  if (footer) {
    const foot = document.createElement('div');
    foot.className = 'dialog-footer';
    if (typeof footer === 'string') {
      foot.innerHTML = footer;
    } else {
      foot.appendChild(footer);
    }
    content.appendChild(foot);
  }

  // Focus trap
  content.addEventListener('keydown', (e) => trapFocus(e, content));

  // Append to DOM
  document.body.appendChild(overlay);
  document.body.appendChild(content);
  document.body.classList.add('scroll-lock');

  // Store
  activeDialogs.set(id, { overlay, content, previousFocus, onClose });

  // Add global keydown if first dialog
  if (activeDialogs.size === 1) {
    document.addEventListener('keydown', onKeyDown);
  }

  // Focus the content
  content.focus();
}

export function closeDialog(id) {
  const entry = activeDialogs.get(id);
  if (!entry) return;

  entry.overlay.remove();
  entry.content.remove();
  activeDialogs.delete(id);

  if (activeDialogs.size === 0) {
    document.body.classList.remove('scroll-lock');
    document.removeEventListener('keydown', onKeyDown);
  }

  // Restore focus
  if (entry.previousFocus && typeof entry.previousFocus.focus === 'function') {
    entry.previousFocus.focus();
  }

  if (entry.onClose) entry.onClose();
}
