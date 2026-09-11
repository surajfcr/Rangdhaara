// Customer account: orders, courses, saved addresses, profile, password and signed-in devices.
import { api } from "../lib/api.js";
import { openAuth } from "../lib/auth.js";
import { busy, clearFieldErrors, closeLayer, formError, html, on, openLayer, render, showFieldErrors, toast, values } from "../lib/dom.js";
import { dateOnly, dayLabel, relative, rupees, sessionLabel } from "../lib/format.js";
import { getStore, getUser, setUser, signOut } from "../lib/session.js";
import { statusChip } from "../lib/status.js";

const TABS = [
  ["orders", "Orders", "package"],
  ["courses", "Courses", "play-circle"],
  ["addresses", "Addresses", "map-pin"],
  ["profile", "Profile", "user"],
  ["security", "Password & devices", "shield"],
];

let layer;
let tab = "orders";
let editing = null;
let addresses = [];

export function openAccount(initial = "orders") {
  if (!getUser()) {
    openAuth({ onDone: () => openAccount(initial) });
    return;
  }
  ensure();
  tab = initial;
  editing = null;
  paintShell();
  openLayer(layer);
  loadTab();
}

function ensure() {
  if (layer) return;
  layer = document.createElement("div");
  layer.className = "layer";
  layer.hidden = true;
  layer.setAttribute("role", "dialog");
  layer.setAttribute("aria-modal", "true");
  layer.setAttribute("aria-labelledby", "account-title");
  layer.innerHTML = '<div class="layer-backdrop" data-close></div><div class="layer-panel" style="max-width:52rem"></div>';
  document.body.appendChild(layer);

  on(layer, "click", "[data-close]", () => closeLayer(layer));
  on(layer, "click", "[data-tab]", (e, el) => {
    tab = el.dataset.tab;
    editing = null;
    paintShell();
    loadTab();
  });
  on(layer, "click", "[data-signout]", async () => {
    await signOut();
    closeLayer(layer);
    toast("You're signed out.", "success");
  });
  on(layer, "click", "[data-address-edit]", (e, el) => { editing = el.dataset.addressEdit; loadTab(); });
  on(layer, "click", "[data-address-cancel]", () => { editing = null; loadTab(); });
  on(layer, "click", "[data-address-delete]", async (e, el) => {
    if (!confirm("Delete this address?")) return;
    try {
      await api(`/me/addresses/${el.dataset.addressDelete}`, { method: "DELETE" });
      toast("Address deleted.", "success");
      loadTab();
    } catch (err) { toast(err.message, "error"); }
  });
  on(layer, "click", "[data-address-default]", async (e, el) => {
    const address = addresses.find((a) => String(a.id) === el.dataset.addressDefault);
    try {
      await api(`/me/addresses/${address.id}`, { method: "PATCH", body: { ...address, is_default: true } });
      loadTab();
    } catch (err) { toast(err.message, "error"); }
  });
  on(layer, "click", "[data-sign-out-others]", async (e, el) => {
    busy(el, true, "Signing out…");
    try {
      await api("/me/sessions/sign-out-others", { method: "POST" });
      toast("Every other device has been signed out.", "success");
      loadTab();
    } catch (err) {
      busy(el, false);
      toast(err.message, "error");
    }
  });
  layer.addEventListener("submit", submit);
}

function paintShell() {
  const user = getUser();
  render(layer.querySelector(".layer-panel"), html`
    <div class="flex items-start justify-between gap-4 px-6 pt-6">
      <div class="flex items-center gap-3 min-w-0">
        <span class="w-12 h-12 rounded-full bg-terracotta text-white grid place-items-center font-serif text-lg font-bold shrink-0" aria-hidden="true">${(user.full_name || user.email)[0].toUpperCase()}</span>
        <div class="min-w-0">
          <h2 id="account-title" class="font-serif text-xl font-bold truncate">${user.full_name || "Your account"}</h2>
          <p class="text-sm text-charcoal-light truncate">${user.email}</p>
        </div>
      </div>
      <button type="button" class="icon-btn" data-close aria-label="Close"><i data-lucide="x" class="w-5 h-5"></i></button>
    </div>
    <nav class="flex gap-1 overflow-x-auto px-4 sm:px-6 mt-5 border-b border-sand" role="tablist" aria-label="Account sections">
      ${TABS.map(([id, label, icon]) => html`<button type="button" role="tab" class="acct-tab" aria-selected="${tab === id}" data-tab="${id}"><i data-lucide="${icon}" class="w-4 h-4"></i>${label}</button>`)}
    </nav>
    <div data-tab-body class="p-6 min-h-[18rem]" role="tabpanel"><div class="skeleton h-28"></div></div>
    <footer class="px-6 py-4 flex justify-between items-center border-t border-sand">
      <a href="/my-courses" class="link text-sm">Go to My Courses</a>
      <button type="button" class="btn btn-ghost btn-sm text-red-700" data-signout><i data-lucide="log-out" class="w-4 h-4"></i>Sign out</button>
    </footer>`);
}

async function loadTab() {
  const body = layer.querySelector("[data-tab-body]");
  const requested = tab;
  try {
    let content;
    if (tab === "orders") content = ordersView((await api("/me/orders")).orders);
    else if (tab === "courses") content = coursesView(await api("/me/courses"));
    else if (tab === "addresses") {
      addresses = (await api("/me/addresses")).addresses;
      content = addressesView();
    } else if (tab === "profile") content = profileView(getUser());
    else content = securityView(getUser(), (await api("/me/sessions")).sessions);
    if (requested === tab) render(body, content);
  } catch (err) {
    if (err.status === 401) {
      closeLayer(layer);
      setUser(null);
      openAuth({ reason: "Your session has ended. Sign in again to open your account." });
      return;
    }
    render(body, html`<p class="form-error">${err.message}</p>`);
  }
}

const empty = (icon, title, text, action = "") => html`
  <div class="text-center py-10">
    <div class="mx-auto w-12 h-12 rounded-full bg-terracotta-light text-terracotta grid place-items-center mb-3"><i data-lucide="${icon}" class="w-5 h-5"></i></div>
    <p class="font-semibold">${title}</p>
    <p class="text-sm text-charcoal-light mt-1 max-w-sm mx-auto">${text}</p>
    ${action}
  </div>`;

function ordersView(orders) {
  if (!orders.length) {
    return empty("package", "No orders yet", "When you buy something it appears here, with its delivery status.",
      html`<a href="/#shop" class="btn btn-outline mt-4" data-close>Browse the shop</a>`);
  }
  return html`<div class="space-y-3">${orders.map((o) => html`
    <article class="rounded-2xl border border-sand p-4">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div><p class="font-semibold tabular-nums">#${o.id}</p><p class="text-xs text-charcoal-light">${dateOnly(o.created_at)} · ${rupees(o.total_paise)}</p></div>
        ${statusChip(o.status, o.status_label)}
      </div>
      <div class="flex items-center gap-2 mt-3">
        ${o.items.slice(0, 3).map((i) => html`<img src="${i.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-11 h-11 rounded-lg object-cover bg-sand shrink-0">`)}
        <p class="text-sm text-charcoal-light line-clamp-2 ml-1">${o.items.map((i) => `${i.title} ×${i.qty}`).join(", ")}</p>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-2 mt-3 text-sm">
        <span class="text-charcoal-light">${o.awb ? `${o.courier || "Courier"} tracking number ${o.awb}`
          : o.delivery && ["paid", "packed", "shipped"].includes(o.status) ? `Estimated ${dayLabel(o.delivery.min_date)} – ${dayLabel(o.delivery.max_date)}` : ""}</span>
        <a href="/order/${encodeURIComponent(o.id)}" class="link">View order</a>
      </div>
    </article>`)}</div>`;
}

function coursesView({ courses, workshops }) {
  if (!courses.length && !workshops.length) {
    return empty("play-circle", "No courses yet", "Masterclasses and live workshops you join appear here.",
      html`<a href="/#academy" class="btn btn-outline mt-4" data-close>Explore the Academy</a>`);
  }
  return html`<div class="space-y-3">
    ${courses.map((c) => html`
      <a href="/my-courses/${c.course_id}" class="flex gap-4 items-center rounded-2xl border border-sand p-3 hover:border-terracotta transition-colors ${c.active ? "" : "opacity-60"}">
        <img src="${c.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-20 h-16 rounded-xl object-cover bg-sand shrink-0">
        <span class="flex-1 min-w-0">
          <span class="block font-semibold truncate">${c.title}</span>
          ${c.active && c.progress ? html`
            <span class="progress mt-2" role="progressbar" aria-label="Progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${c.progress.percent}"><span style="width:${c.progress.percent}%"></span></span>
            <span class="block text-xs text-charcoal-light mt-1">${c.progress.completed} of ${c.progress.total} lessons${c.progress.is_complete ? " · complete" : ""}</span>`
            : html`<span class="block text-xs text-charcoal-light mt-1">Access has ended</span>`}
        </span>
        <i data-lucide="chevron-right" class="w-5 h-5 text-charcoal-light"></i>
      </a>`)}
    ${workshops.map((w) => html`
      <div class="rounded-2xl border border-sand p-4">
        <p class="font-semibold">${w.title}</p>
        <p class="text-sm text-charcoal-light">${sessionLabel(w.starts_at)} · ${w.duration_min} min · ${w.seats} seat${w.seats === 1 ? "" : "s"}</p>
        ${w.meeting_url ? html`<a href="${w.meeting_url}" target="_blank" rel="noopener" class="btn btn-primary btn-sm mt-3"><i data-lucide="video" class="w-4 h-4"></i>Join the session</a>`
          : html`<p class="hint mt-2">${w.ended ? "This session has ended." : w.link_note}</p>`}
      </div>`)}
  </div>`;
}

function addressForm(a = {}) {
  const states = (getStore() && getStore().states) || [];
  return html`
    <form data-address-form data-id="${a.id || ""}" class="grid sm:grid-cols-6 gap-4 rounded-2xl border border-terracotta/40 bg-cream p-4" novalidate>
      <label class="field sm:col-span-2"><span class="field-label">PIN code</span><input class="input tabular-nums" name="pincode" inputmode="numeric" maxlength="6" value="${a.pincode || ""}" required></label>
      <label class="field sm:col-span-2"><span class="field-label">City</span><input class="input" name="city" value="${a.city || ""}" required></label>
      <label class="field sm:col-span-2"><span class="field-label">State</span><select class="input" name="state" required><option value="">Choose</option>${states.map((st) => html`<option ${st === a.state ? "selected" : ""}>${st}</option>`)}</select></label>
      <label class="field sm:col-span-6"><span class="field-label">House or flat number, building and street</span><input class="input" name="line1" value="${a.line1 || ""}" required></label>
      <label class="field sm:col-span-6"><span class="field-label">Area or landmark (optional)</span><input class="input" name="line2" value="${a.line2 || ""}"></label>
      <label class="field sm:col-span-3"><span class="field-label">Name on the parcel</span><input class="input" name="full_name" value="${a.full_name || (getUser() && getUser().full_name) || ""}" required></label>
      <label class="field sm:col-span-3"><span class="field-label">Phone for the courier</span><input class="input" type="tel" name="phone" inputmode="numeric" value="${a.phone || (getUser() && getUser().phone) || ""}" required></label>
      <label class="sm:col-span-6 flex items-center gap-2.5 text-sm text-charcoal-light"><input type="checkbox" name="is_default" class="checkbox" ${a.is_default ? "checked" : ""}>Use as my default address</label>
      <div class="form-error sm:col-span-6" role="alert" hidden></div>
      <div class="sm:col-span-6 flex gap-2"><button type="submit" class="btn btn-primary btn-sm">Save address</button><button type="button" class="btn btn-ghost btn-sm" data-address-cancel>Cancel</button></div>
    </form>`;
}

function addressesView() {
  return html`<div class="space-y-3">
    ${addresses.map((a) => (editing === String(a.id) ? addressForm(a) : html`
      <article class="rounded-2xl border border-sand p-4 flex flex-wrap justify-between gap-3">
        <div class="text-sm">
          <p class="font-semibold text-charcoal">${a.full_name}${a.is_default ? html` <span class="chip chip-brand ml-1">Default</span>` : ""}</p>
          <p class="text-charcoal-light">${a.line1}${a.line2 ? `, ${a.line2}` : ""}</p>
          <p class="text-charcoal-light">${a.city}, ${a.state} ${a.pincode} · ${a.phone}</p>
        </div>
        <div class="flex items-start gap-1">
          ${a.is_default ? "" : html`<button type="button" class="btn btn-ghost btn-sm" data-address-default="${a.id}">Make default</button>`}
          <button type="button" class="btn btn-ghost btn-sm" data-address-edit="${a.id}">Edit</button>
          <button type="button" class="btn btn-ghost btn-sm text-red-700" data-address-delete="${a.id}">Delete</button>
        </div>
      </article>`))}
    ${editing === "new" ? addressForm({ is_default: !addresses.length })
      : html`<button type="button" class="btn btn-outline" data-address-edit="new"><i data-lucide="plus" class="w-4 h-4"></i>Add an address</button>`}
  </div>`;
}

const profileView = (user) => html`
  <form data-profile-form class="space-y-4 max-w-md" novalidate>
    <label class="field"><span class="field-label">Full name</span><input class="input" name="full_name" autocomplete="name" value="${user.full_name}" required></label>
    <label class="field"><span class="field-label">Mobile number</span><input class="input" type="tel" name="phone" inputmode="numeric" autocomplete="tel-national" value="${user.phone}" required></label>
    <div><p class="field-label">Email</p><p class="text-sm">${user.email}</p><p class="hint">Your email is how you sign in, so it can't be changed here.</p></div>
    <label class="flex items-start gap-2.5 text-sm text-charcoal-light"><input type="checkbox" name="marketing_opt_in" class="checkbox mt-0.5" ${user.marketing_opt_in ? "checked" : ""}>Email me when new pieces and masterclasses launch</label>
    <div class="form-error" role="alert" hidden></div>
    <button type="submit" class="btn btn-primary">Save profile</button>
  </form>`;

const securityView = (user, sessions) => html`
  <div class="grid md:grid-cols-2 gap-8">
    <form data-password-form class="space-y-4" novalidate>
      <h3 class="subhead">${user.has_password ? "Change password" : "Set a password"}</h3>
      ${user.has_password ? "" : html`<p class="hint">You've been signing in with emailed codes. Add a password if you'd like another way in.</p>`}
      ${user.has_password ? html`<label class="field"><span class="field-label">Current password</span><input class="input" type="password" name="current_password" autocomplete="current-password" required></label>` : ""}
      <label class="field"><span class="field-label">New password</span><input class="input" type="password" name="new_password" autocomplete="new-password" required></label>
      <label class="field"><span class="field-label">Confirm new password</span><input class="input" type="password" name="confirm_password" autocomplete="new-password" required></label>
      <p class="hint">At least 8 characters. Saving signs out your other devices.</p>
      <div class="form-error" role="alert" hidden></div>
      <button type="submit" class="btn btn-primary">Save password</button>
    </form>
    <div class="space-y-3">
      <h3 class="subhead">Signed-in devices</h3>
      <ul class="divide-y divide-sand border border-sand rounded-2xl">${sessions.map((d) => html`
        <li class="p-3 text-sm flex items-center justify-between gap-3">
          <span><span class="font-semibold">${d.device}</span>${d.current ? html` <span class="chip chip-ok ml-1">This device</span>` : ""}
          <span class="block text-xs text-charcoal-light">Active ${relative(d.last_seen_at)} · signed in ${dateOnly(d.created_at)}</span></span>
        </li>`)}</ul>
      ${sessions.length > 1 ? html`<button type="button" class="btn btn-outline btn-sm" data-sign-out-others>Sign out all other devices</button>` : ""}
    </div>
  </div>`;

async function submit(e) {
  const form = e.target.closest("form");
  if (!form) return;
  e.preventDefault();
  clearFieldErrors(form);
  const v = values(form);
  const button = form.querySelector('button[type="submit"]');
  busy(button, true, "Saving…");
  try {
    if (form.matches("[data-address-form]")) {
      const body = { full_name: v.full_name, phone: v.phone, line1: v.line1, line2: v.line2 || "", city: v.city, state: v.state, pincode: v.pincode, is_default: v.is_default };
      const id = form.dataset.id;
      await api(id ? `/me/addresses/${id}` : "/me/addresses", { method: id ? "PATCH" : "POST", body });
      editing = null;
      toast("Address saved.", "success");
      loadTab();
    } else if (form.matches("[data-profile-form]")) {
      const res = await api("/me", { method: "PATCH", body: { full_name: v.full_name, phone: v.phone, marketing_opt_in: v.marketing_opt_in } });
      setUser(res.user);
      toast("Profile saved.", "success");
      paintShell();
      loadTab();
    } else if (form.matches("[data-password-form]")) {
      if (v.new_password !== v.confirm_password) {
        showFieldErrors(form, { confirm_password: "The two new passwords don't match." });
        return;
      }
      const res = await api("/me/password", { method: "POST", body: { current_password: v.current_password || "", new_password: v.new_password, confirm_password: v.confirm_password } });
      setUser(res.user);
      toast(res.message, "success");
      loadTab();
    }
  } catch (err) {
    showFieldErrors(form, err.fields);
    formError(form, err.message);
  } finally {
    if (button.isConnected) busy(button, false);
  }
}
