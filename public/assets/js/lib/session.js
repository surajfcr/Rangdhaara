// Who is signed in, plus store settings, fetched once per page load.
import { api } from "./api.js";

const state = { store: null, user: null };
const listeners = new Set();

// The old site kept accounts (with plain-text passwords) and carts in localStorage. Remove them.
["rangdhaara_users_db", "rangdhaara_active_user", "rangdhaara_cart", "rangdhaara_admin_session"].forEach((key) => {
  try { localStorage.removeItem(key); } catch { /* storage unavailable */ }
});

export async function boot() {
  state.store = await api("/store");
  state.user = state.store.user;
  emit();
  return state.store;
}

export const getStore = () => state.store;
export const getUser = () => state.user;

export function setUser(user) {
  state.user = user;
  emit();
}

export function onUserChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  listeners.forEach((fn) => fn(state.user));
}

export async function signOut() {
  await api("/auth/logout", { method: "POST" });
  setUser(null);
}

export function whatsappLink(message = "") {
  const number = (state.store && state.store.whatsapp_number) || "918080007684";
  return `https://wa.me/${number}${message ? `?text=${encodeURIComponent(message)}` : ""}`;
}
