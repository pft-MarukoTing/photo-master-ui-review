// Feedback dialog: collect an issue description from the user and POST it to
// /api/feedback so the server can persist the task's source photo, result
// photo and the typed description into feedback/issue_<task_id>/.

import { openDialog, closeDialog } from './dialog.js';
import { createDialogFooter } from './confirmation-dialogs.js';

const FEEDBACK_DIALOG_ID = 'feedback-dialog';

function getApiBase() {
  const m = window.location.pathname.match(/^(\/[^/]+)/);
  if (m && m[1] !== '/web' && m[1] !== '/api') return `${m[1]}/api`;
  return '/api';
}

async function submitFeedback({ taskId, description, page }) {
  const res = await fetch(`${getApiBase()}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      task_id: taskId,
      description,
      page,
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Feedback request failed (${res.status}): ${text.slice(0, 200)}`);
  }
  return res.json();
}

/**
 * Show the "Give Feedback" dialog.
 * @param {{ taskId: string, page?: string }} ctx — context required to submit feedback
 */
export function showFeedbackDialog({ taskId, page }) {
  if (!taskId) {
    window.alert('No task to attach feedback to yet — apply a feature first.');
    return;
  }

  // Body
  const body = document.createElement('div');
  body.style.display = 'flex';
  body.style.flexDirection = 'column';
  body.style.gap = '0.5rem';
  body.style.marginTop = '0.5rem';

  const label = document.createElement('label');
  label.htmlFor = 'feedback-description';
  label.textContent = 'Description';
  label.style.fontSize = '0.875rem';
  label.style.fontWeight = '500';

  const textarea = document.createElement('textarea');
  textarea.id = 'feedback-description';
  textarea.rows = 4;
  textarea.placeholder = 'Describe the issue in as much detail as possible…';
  textarea.style.width = '100%';
  textarea.style.padding = '0.625rem 0.75rem';
  textarea.style.borderRadius = '6px';
  textarea.style.border = '1px solid var(--color-border)';
  textarea.style.background = 'var(--color-background)';
  textarea.style.color = 'var(--color-foreground)';
  textarea.style.fontFamily = 'inherit';
  textarea.style.fontSize = '0.875rem';
  textarea.style.resize = 'vertical';
  textarea.style.minHeight = '7rem';
  textarea.style.boxSizing = 'border-box';

  const statusLine = document.createElement('div');
  statusLine.style.fontSize = '0.75rem';
  statusLine.style.color = 'var(--color-muted-foreground)';
  statusLine.style.minHeight = '1rem';

  body.appendChild(label);
  body.appendChild(textarea);
  body.appendChild(statusLine);

  let submitting = false;
  let cancelBtn;
  let submitBtn;

  const onCancel = () => {
    if (submitting) return;
    closeDialog(FEEDBACK_DIALOG_ID);
  };

  const onSubmit = async () => {
    if (submitting) return;
    const description = textarea.value.trim();
    if (!description) {
      statusLine.style.color = 'var(--color-destructive)';
      statusLine.textContent = 'Please describe the issue before submitting.';
      textarea.focus();
      return;
    }
    submitting = true;
    statusLine.style.color = 'var(--color-muted-foreground)';
    statusLine.textContent = 'Saving feedback…';
    textarea.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting…';
    }
    try {
      await submitFeedback({ taskId, description, page });
      statusLine.style.color = 'var(--color-foreground)';
      statusLine.textContent = 'Submitted! Thank you for helping us improve.';
      setTimeout(() => closeDialog(FEEDBACK_DIALOG_ID), 600);
    } catch (err) {
      submitting = false;
      textarea.disabled = false;
      if (cancelBtn) cancelBtn.disabled = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit';
      }
      statusLine.style.color = 'var(--color-destructive)';
      statusLine.textContent = err?.message || 'Failed to submit feedback.';
    }
  };

  const footer = createDialogFooter([
    {
      label: 'Cancel',
      className: 'btn btn-outline btn-default-size',
      onClick: onCancel,
    },
    {
      label: 'Submit',
      className: 'btn btn-primary btn-default-size',
      onClick: onSubmit,
    },
  ]);
  cancelBtn = footer.children[0];
  submitBtn = footer.children[1];

  openDialog({
    id: FEEDBACK_DIALOG_ID,
    title: 'Report Issue',
    description: "This step's input photo and result are saved together with your report.",
    body,
    footer,
  });

  setTimeout(() => textarea.focus(), 0);
}
