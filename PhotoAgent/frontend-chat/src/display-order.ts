// Shared monotonic sequence: requests, completions and UI operations use the
// same clock. Rendering, thumbnail loading and setting control edits do not.
let last = 0;
export function nextDisplayOrder(): number {
  last = Math.max(last + 1, Date.now());
  return last;
}
