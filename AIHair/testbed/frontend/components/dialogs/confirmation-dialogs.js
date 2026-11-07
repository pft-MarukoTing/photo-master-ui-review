// Confirmation dialog utilities

import { openDialog, closeDialog } from './dialog.js';

const CONFIRM_DIALOG_ID = 'confirm-dialog';

/**
 * Creates a dialog footer with the specified buttons.
 * @param {Array<{label: string, className: string, onClick: () => void, icon?: string}>} buttons
 * @returns {HTMLElement}
 */
export function createDialogFooter(buttons) {
  const footer = document.createElement('div');
  footer.className = 'dialog-footer-buttons';

  for (const { label, className, onClick, icon } of buttons) {
    const btn = document.createElement('button');
    btn.className = className;
    btn.innerHTML = icon ? `${icon}&nbsp;&nbsp;${label}` : label;
    btn.addEventListener('click', onClick);
    footer.appendChild(btn);
  }

  return footer;
}

/**
 * A two-button confirmation dialog.
 * @param {{
 *   title: string,
 *   description?: string,
 *   confirmLabel?: string,
 *   cancelLabel?: string,
 *   destructive?: boolean,
 *   onConfirm: () => void,
 * }} options
 */
export function showConfirmDialog({
  title, description, confirmLabel = '確定', cancelLabel = '取消', destructive = false, onConfirm,
}) {
  const footer = createDialogFooter([
    {
      label: cancelLabel,
      className: 'btn btn-outline btn-default-size',
      onClick: () => closeDialog(CONFIRM_DIALOG_ID),
    },
    {
      label: confirmLabel,
      className: `btn ${destructive ? 'btn-destructive' : 'btn-primary'} btn-default-size`,
      onClick: () => {
        closeDialog(CONFIRM_DIALOG_ID);
        onConfirm();
      },
    },
  ]);
  openDialog({ id: CONFIRM_DIALOG_ID, title, description, footer });
  footer.lastElementChild?.focus();
}
