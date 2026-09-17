// Username-only "account" identity: read from ?user_id=<ciphertext> on load
// (decrypted server-side, since the AES key/IV in
// backend/services/decrypt_user_id.py live there only), or set once the user
// creates one via the account modal (app.js). No auth/login -- this is
// purely a namespace for grouping a person's own job history
// (backend/services/account.py), so it deliberately holds nothing more than
// the plain username string.
//
// A plain ?user=NAME is deliberately NOT read (or written back to the URL)
// -- anyone could type that in to view/act as any other username, whereas
// ?user_id= requires a legitimately encrypted value. A self-created account
// (setCurrentUser below) therefore only lives for the current page's JS
// state; it does not survive a refresh/bookmark.
window.PhotoAgentAccount = (() => {
  let currentUser = null;
  // True only for the synthetic fallback identity below (a broken/
  // undecryptable ?user_id=), never for a real resolved or self-created
  // account -- app.js's upload gallery reads this to keep a guest on the
  // plain 範例照片 view (no 最近照片 tab: a disposable per-session bucket has
  // no real history worth switching to), while account-badge/history still
  // treat a guest as signed in like anyone else.
  let isGuest = false;

  function normalizeUsername(raw) {
    return (raw || "").trim();
  }

  async function initFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const userId = params.get("user_id");
    isGuest = false;
    if (!userId) {
      // Secret admin path (?admin, no value needed -- app.js's
      // resolveAdminUser already grants this same identity for the reports
      // drawer): giving it here too, as the actual account identity, means
      // photos processed while accessing via ?admin get a real `user` sent
      // on every submit/continue/analyze call, so their processing history
      // is recorded and recallable like any other account instead of
      // getCurrentUser() staying null and every history write silently
      // no-op'ing (backend's _record_history_safely).
      currentUser = params.has("admin") ? "admin" : null;
      return currentUser;
    }
    try {
      currentUser = normalizeUsername(await window.PhotoAgentApi.resolveUserId(userId)) || null;
    } catch {
      // Broken/undecryptable ?user_id= (stale or malformed value from the
      // linking site) -- fall back to a private, session-only guest identity
      // instead of leaving the app in the no-account state, so the rest of
      // the UI (account-badge, etc.) behaves the same as any other signed-in
      // visit rather than silently degrading. Random suffix keeps two
      // different broken links from sharing one guest's history bucket.
      currentUser = `guest_${Math.random().toString(36).slice(2, 10)}`;
      isGuest = true;
    }
    return currentUser;
  }

  function getCurrentUser() {
    return currentUser;
  }

  function isGuestUser() {
    return isGuest;
  }

  function setCurrentUser(user) {
    currentUser = user;
    isGuest = false;
  }

  function clearCurrentUser() {
    currentUser = null;
    isGuest = false;
  }

  return { initFromUrl, getCurrentUser, isGuestUser, setCurrentUser, clearCurrentUser, normalizeUsername };
})();
