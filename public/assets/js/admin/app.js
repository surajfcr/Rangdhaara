// Admin shell: sign-in gate, role-aware navigation and a hash router that loads each view on demand.
import { api } from "../lib/api.js";
import { openAuth } from "../lib/auth.js";
import { $, html, on, render, toast } from "../lib/dom.js";
import { boot, getUser, onUserChange, setUser, signOut } from "../lib/session.js";
import { errorBlock, loading } from "./ui.js";

const root = $("#admin-root");
const STAFF = ["staff", "admin"];
const OWNER = ["admin"];

const NAV = [
  { group: "Run the shop", items: [
    { id: "today", label: "Today", icon: "sun", roles: STAFF, view: "./today.js" },
    { id: "orders", label: "Orders", icon: "package", roles: STAFF, view: "./orders.js" },
    { id: "catalogue", label: "Products & stock", icon: "shapes", roles: STAFF, view: "./catalogue.js" },
  ] },
  { group: "Academy", items: [
    { id: "academy", label: "Courses", icon: "play-circle", roles: STAFF, view: "./academy.js" },
    { id: "workshops", label: "Workshops", icon: "calendar-days", roles: STAFF, view: "./academy.js" },
    { id: "students", label: "Students", icon: "graduation-cap", roles: STAFF, view: "./people.js" },
  ] },
  { group: "Business", items: [
    { id: "revenue", label: "Revenue", icon: "chart-column", roles: OWNER, view: "./money.js" },
    { id: "customers", label: "Customers", icon: "users", roles: OWNER, view: "./people.js" },
    { id: "coupons", label: "Discount codes", icon: "tag", roles: OWNER, view: "./money.js" },
  ] },
  { group: "Settings", items: [
    { id: "system", label: "System health", icon: "activity", roles: OWNER, view: "./system.js" },
    { id: "audit", label: "Audit log", icon: "scroll-text", roles: OWNER, view: "./system.js" },
  ] },
];
const ITEMS = NAV.flatMap((g) => g.items);

let user = null;
let signInNote = "";
let sequence = 0;

export function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

export async function refreshCounts() {
  if (!user) return;
  try {
    const overview = await api("/admin/overview");
    const pending = overview.actions.to_pack + overview.actions.to_ship + overview.actions.needs_review;
    const badge = root.querySelector('[data-count="orders"]');
    if (badge) {
      badge.textContent = pending;
      badge.hidden = pending === 0;
    }
  } catch { /* counts are a convenience */ }
}

function signInScreen() {
  render(root, html`
    <div class="min-h-screen grid place-items-center p-6">
      <div class="panel p-8 max-w-sm w-full text-center">
        <img src="/assets/images/profile_avatar.jpg" alt="" class="w-14 h-14 rounded-full mx-auto object-cover">
        <h1 class="font-serif text-2xl font-bold mt-4">Rangdhara admin</h1>
        <p class="text-sm text-charcoal-light mt-2">${signInNote || "Sign in with your staff or owner account."}</p>
        <button type="button" class="btn btn-primary w-full mt-6" data-admin-signin>Sign in</button>
        <a href="/" class="link text-sm mt-4 inline-block">Back to the store</a>
      </div>
    </div>`);
}

function noAccessScreen(current) {
  render(root, html`
    <div class="min-h-screen grid place-items-center p-6">
      <div class="panel p-8 max-w-sm w-full text-center">
        <h1 class="font-serif text-2xl font-bold">No admin access</h1>
        <p class="text-sm text-charcoal-light mt-2">You're signed in as ${current.email}, which is a customer account. Ask the store owner to give it staff access, or sign in with a different account.</p>
        <div class="flex justify-center gap-2 mt-6">
          <button type="button" class="btn btn-outline" data-admin-signout>Sign out</button>
          <a href="/" class="btn btn-primary">Go to the store</a>
        </div>
      </div>
    </div>`);
}

function shell() {
  render(root, html`
    <div class="admin-shell">
      <aside class="admin-side p-4 gap-6" aria-label="Admin navigation">
        <a href="#today" class="flex items-center gap-2.5 px-2 pt-1">
          <img src="/assets/images/profile_avatar.jpg" alt="" class="w-9 h-9 rounded-full object-cover">
          <span><span class="block font-serif text-lg font-bold text-white leading-none">Rangdhara</span>
          <span class="block text-[10px] uppercase tracking-[0.16em] text-white/50 mt-1">Admin</span></span>
        </a>
        <nav class="admin-nav flex-1 space-y-5 overflow-y-auto">
          ${NAV.map((group) => {
            const items = group.items.filter((item) => item.roles.includes(user.role));
            return items.length ? html`<div>
              <p class="px-3 mb-1.5 text-[10px] font-bold uppercase tracking-[0.14em] text-white/40">${group.group}</p>
              ${items.map((item) => html`<a href="#${item.id}" data-nav="${item.id}"><i data-lucide="${item.icon}" class="w-4 h-4"></i>${item.label}<span class="count" data-count="${item.id}" hidden></span></a>`)}
            </div>` : "";
          })}
        </nav>
        <div class="border-t border-white/10 pt-4 px-2 text-sm">
          <p class="text-white truncate">${user.full_name || user.email}</p>
          <p class="text-white/50 text-xs">${user.role === "admin" ? "Owner" : "Staff"}</p>
          <div class="flex gap-2 mt-3">
            <a href="/" target="_blank" rel="noopener" class="btn btn-sm bg-white/10 text-white hover:bg-white/20"><i data-lucide="external-link" class="w-3.5 h-3.5"></i>Store</a>
            <button type="button" class="btn btn-sm bg-white/10 text-white hover:bg-white/20" data-admin-signout>Sign out</button>
          </div>
        </div>
      </aside>
      <div class="admin-main">
        <div class="admin-top lg:hidden px-4 h-14 flex items-center gap-3">
          <button type="button" class="icon-btn" data-menu aria-label="Open menu"><i data-lucide="menu" class="w-5 h-5"></i></button>
          <span class="font-serif font-bold">Rangdhara admin</span>
        </div>
        <main id="view" class="p-4 sm:p-8 max-w-[92rem]"></main>
      </div>
    </div>`);
}

function parseHash() {
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  return { name: name || "today", params: rest.filter(Boolean).map(decodeURIComponent) };
}

async function route() {
  if (!user) return;
  const view = $("#view");
  if (!view) return;
  const { name, params } = parseHash();
  const item = ITEMS.find((i) => i.id === name);
  root.querySelectorAll("[data-nav]").forEach((a) => a.setAttribute("aria-current", a.dataset.nav === name ? "page" : "false"));
  const side = root.querySelector(".admin-side");
  if (side) side.classList.remove("is-open");
  if (!item || !item.roles.includes(user.role)) {
    render(view, html`<div class="form-error">That page isn't available for your account.</div>`);
    return;
  }
  const mine = ++sequence;
  render(view, loading());
  window.scrollTo(0, 0);
  try {
    const module = await import(item.view);
    if (mine !== sequence) return;
    await module.default(view, { user, route: name, params, isCurrent: () => mine === sequence, navigate, refreshCounts });
  } catch (err) {
    if (err.status === 401) {
      signInNote = err.message;
      setUser(null);
      return;
    }
    if (mine === sequence) render(view, errorBlock(err));
  }
}

function gate() {
  user = getUser();
  if (!user) return signInScreen();
  if (!STAFF.includes(user.role)) return noAccessScreen(user);
  signInNote = "";
  shell();
  route();
  refreshCounts();
}

on(root, "click", "[data-admin-signin]", () => openAuth({ reason: "Use your staff or owner account." }));
on(root, "click", "[data-admin-signout]", async () => {
  await signOut();
  toast("Signed out.", "success");
});
on(root, "click", "[data-menu]", () => root.querySelector(".admin-side").classList.toggle("is-open"));
on(root, "click", "tr[data-href]", (e, row) => {
  if (e.target.closest("a, button, input, label, select")) return;
  navigate(row.dataset.href);
});
window.addEventListener("hashchange", route);

(async () => {
  try {
    await boot();
  } catch (err) {
    render(root, html`<div class="p-8">${errorBlock(err)}</div>`);
    return;
  }
  onUserChange(gate);
  gate();
})();
