import { api } from "./api";

const GUEST_KEY = "photomaster-chat-guest";

function randomGuest() {
  return `guest_${Math.random().toString(36).slice(2, 10)}`;
}

// Same rules as the old UI's account.js (?user_id= decrypted, ?admin), except
// that a visit with neither gets a guest identity remembered in this browser:
// chat sessions are stored per user, so without one there would be nowhere to
// keep them.
export async function resolveUser(): Promise<string> {
  const params = new URLSearchParams(location.search);
  const userId = params.get("user_id");
  if (userId) {
    try {
      const { user } = await api.decryptUserId(userId);
      if (user && user.trim()) return user.trim();
    } catch {
      /* fall through to a throwaway guest, as account.js does */
    }
    return randomGuest();
  }
  if (params.has("admin")) return "admin";
  try {
    const saved = localStorage.getItem(GUEST_KEY);
    if (saved) return saved;
    const guest = randomGuest();
    localStorage.setItem(GUEST_KEY, guest);
    return guest;
  } catch {
    return randomGuest();
  }
}
