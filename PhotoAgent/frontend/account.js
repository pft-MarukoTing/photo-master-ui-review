// Username-only "account" identity: read from ?user=NAME on load, or set
// once the user creates one via the account modal (app.js). No auth/login --
// this is purely a namespace for grouping a person's own job history
// (backend/services/account.py), so it deliberately holds nothing more than
// the plain username string.
window.PhotoAgentAccount = (() => {
  let currentUser = null;

  function normalizeUsername(raw) {
    return (raw || "").trim();
  }

  function initFromUrl() {
    const raw = new URLSearchParams(window.location.search).get("user");
    currentUser = normalizeUsername(raw) || null;
    return currentUser;
  }

  function getCurrentUser() {
    return currentUser;
  }

  // Reflects the newly-created account into the URL (no reload) so a
  // refresh/bookmark/share of this tab keeps the same identity, matching how
  // it would have arrived via ?user=NAME from another page in the first
  // place.
  function setCurrentUser(user) {
    currentUser = user;
    const url = new URL(window.location.href);
    url.searchParams.set("user", user);
    window.history.replaceState({}, "", url);
  }

  // Mirror image of setCurrentUser -- drops the ?user= identity so a
  // refresh/bookmark/share of this tab no longer carries it either. Callers
  // still need to reload/re-render the app themselves afterward (this module
  // only owns the identity, not the rest of the app's state).
  function clearCurrentUser() {
    currentUser = null;
    const url = new URL(window.location.href);
    url.searchParams.delete("user");
    window.history.replaceState({}, "", url);
  }

  return { initFromUrl, getCurrentUser, setCurrentUser, clearCurrentUser, normalizeUsername };
})();
