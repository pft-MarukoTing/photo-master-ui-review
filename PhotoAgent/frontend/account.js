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

  function normalizeUsername(raw) {
    return (raw || "").trim();
  }

  async function initFromUrl() {
    const userId = new URLSearchParams(window.location.search).get("user_id");
    if (!userId) {
      currentUser = null;
      return currentUser;
    }
    try {
      currentUser = normalizeUsername(await window.PhotoAgentApi.resolveUserId(userId)) || null;
    } catch {
      currentUser = null;
    }
    return currentUser;
  }

  function getCurrentUser() {
    return currentUser;
  }

  function setCurrentUser(user) {
    currentUser = user;
  }

  function clearCurrentUser() {
    currentUser = null;
  }

  return { initFromUrl, getCurrentUser, setCurrentUser, clearCurrentUser, normalizeUsername };
})();
