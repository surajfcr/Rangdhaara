// Students (course access) and customers (every client account, with a full record for each).
import { api } from "../lib/api.js";
import { html, render, toast } from "../lib/dom.js";
import { dateOnly, relative, rupees, sessionLabel } from "../lib/format.js";
import { statusChip } from "../lib/status.js";
import { back, emptyRow, field, formDialog, pageHead } from "./ui.js";

export default async function people(el, ctx) {
  if (ctx.route === "customers") return ctx.params[0] ? customer(el, ctx, ctx.params[0]) : customers(el, ctx);
  return students(el, ctx);
}

// ------------------------------------------------------------------ students

async function students(el, ctx) {
  const owner = ctx.user.role === "admin";
  const state = { courseId: ctx.params[0] ? Number(ctx.params[0]) : "", q: "" };
  const { courses } = await api("/admin/courses");
  if (!ctx.isCurrent()) return;

  render(el, html`
    ${pageHead("Students", "Who has access to which course, and how far they've got.",
      owner ? html`<button type="button" class="btn btn-primary btn-sm" data-grant><i data-lucide="user-plus" class="w-4 h-4"></i>Give course access</button>` : "")}
    <div class="flex flex-wrap gap-3 mb-4">
      <label class="field"><span class="sr-only">Course</span>
        <select class="input !py-2 w-72" data-course><option value="">All courses</option>${courses.map((c) => html`<option value="${c.course_id}" ${c.course_id === state.courseId ? "selected" : ""}>${c.title}</option>`)}</select></label>
      <label class="relative"><span class="sr-only">Search students</span>
        <i data-lucide="search" class="w-4 h-4 text-charcoal-light absolute left-3 top-1/2 -translate-y-1/2"></i>
        <input type="search" class="input !py-2 !pl-9 w-64" placeholder="Name, email or phone" data-search></label>
    </div>
    <div class="panel overflow-hidden" data-table><div class="skeleton h-48 m-4"></div></div>`);

  async function load() {
    const { enrollments } = await api("/admin/enrollments", { query: { course_id: state.courseId, q: state.q } });
    if (!ctx.isCurrent()) return;
    render(el.querySelector("[data-table]"), html`<div class="table-wrap"><table class="data-table">
      <thead><tr><th>Student</th><th>Course</th><th>How</th><th>Since</th><th>Progress</th><th>Status</th>${owner ? html`<th></th>` : ""}</tr></thead>
      <tbody>${enrollments.length ? enrollments.map((e) => html`<tr>
        <td><span class="block font-medium">${e.full_name || e.email}</span><span class="block text-xs text-charcoal-light">${e.email}${e.phone ? ` · ${e.phone}` : ""}</span></td>
        <td>${e.course_title}</td>
        <td>${e.source === "purchase" ? html`<a class="link" href="#orders/${e.order_id}">Purchase</a>` : html`<span title="${e.grant_reason || ""}">Given by the studio</span>`}</td>
        <td class="whitespace-nowrap">${dateOnly(e.granted_at)}</td>
        <td class="min-w-[10rem]">${e.progress ? html`<span class="progress block" role="progressbar" aria-valuenow="${e.progress.percent}" aria-valuemin="0" aria-valuemax="100" aria-label="Progress"><span style="width:${e.progress.percent}%"></span></span>
          <span class="text-xs text-charcoal-light">${e.progress.completed} of ${e.progress.total} lessons</span>` : "—"}</td>
        <td>${e.revoked_at ? html`<span class="chip chip-muted" title="${e.revoke_reason || ""}">Revoked</span>` : html`<span class="chip chip-ok">Active</span>`}</td>
        ${owner ? html`<td class="text-right">${e.revoked_at ? "" : html`<button type="button" class="btn btn-ghost btn-sm text-red-700" data-revoke="${e.id}" data-name="${e.email}">Revoke</button>`}</td>` : ""}
      </tr>`) : emptyRow(owner ? 7 : 6, "No students match.")}</tbody>
    </table></div>`);
  }

  let timer;
  el.querySelector("[data-search]").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value; load(); }, 300); });
  el.querySelector("[data-course]").addEventListener("change", (e) => { state.courseId = e.target.value ? Number(e.target.value) : ""; load(); });
  el.onclick = async (e) => {
    let b;
    if ((b = e.target.closest("[data-grant]"))) {
      const result = await formDialog({
        title: "Give course access",
        description: "Use this for gifts, competition winners or fixing a payment problem. They get an email with a link to start.",
        submitLabel: "Give access",
        body: html`
          ${field("Course", html`<select class="input" name="course_id" required>${courses.map((c) => html`<option value="${c.course_id}" ${c.course_id === state.courseId ? "selected" : ""}>${c.title}</option>`)}</select>`)}
          ${field("Student's email", html`<input class="input" type="email" name="email" required autofocus>`)}
          ${field("Student's name", html`<input class="input" name="full_name">`, "Only needed if they don't have an account yet.")}
          ${field("Reason", html`<input class="input" name="reason" required placeholder="e.g. Instagram giveaway winner">`, "Kept in the audit log.")}`,
        onSubmit: (v) => api("/admin/enrollments", { method: "POST", body: { course_id: Number(v.course_id), email: v.email, full_name: v.full_name, reason: v.reason } }),
      });
      if (result) { toast("Access given and the student has been emailed.", "success"); load(); }
    } else if ((b = e.target.closest("[data-revoke]"))) {
      const result = await formDialog({
        title: "Revoke access?",
        description: `${b.dataset.name} will no longer be able to watch this course. This doesn't refund any payment.`,
        submitLabel: "Revoke access",
        danger: true,
        body: field("Reason", html`<input class="input" name="reason" required autofocus>`),
        onSubmit: (v) => api(`/admin/enrollments/${b.dataset.revoke}/revoke`, { method: "POST", body: { reason: v.reason } }),
      });
      if (result) { toast("Access revoked.", "success"); load(); }
    }
  };
  await load();
}

// ------------------------------------------------------------------ customers

const roleSelect = (c, me) => (c.id === me
  ? html`<span class="chip chip-brand">You (owner)</span>`
  : html`<select class="input !py-1.5 !text-sm w-32" data-role="${c.id}" data-email="${c.email}" aria-label="Role for ${c.email}">
      ${[["customer", "Customer"], ["staff", "Staff"], ["admin", "Owner"]].map(([value, label]) => html`<option value="${value}" ${c.role === value ? "selected" : ""}>${label}</option>`)}
    </select>`);

async function changeRole(select) {
  const label = select.options[select.selectedIndex].text;
  if (!confirm(`Make ${select.dataset.email} ${label === "Owner" ? "an owner with full access" : label.toLowerCase()}?`)) return false;
  try {
    await api(`/admin/users/${select.dataset.role}/role`, { method: "POST", body: { role: select.value } });
    toast("Role updated.", "success");
  } catch (err) {
    toast(err.message, "error");
  }
  return true;
}

async function customers(el, ctx) {
  const state = { q: "", page: 1 };
  render(el, html`
    ${pageHead("Customers", "Every client account: contact details, where they are, what they've bought. Click a row for the full record.",
      html`<a class="btn btn-outline btn-sm" href="/api/v1/admin/customers.csv"><i data-lucide="download" class="w-4 h-4"></i>Download all as CSV</a>`)}
    <label class="relative inline-block mb-4"><span class="sr-only">Search customers</span>
      <i data-lucide="search" class="w-4 h-4 text-charcoal-light absolute left-3 top-1/2 -translate-y-1/2"></i>
      <input type="search" class="input !py-2 !pl-9 w-72" placeholder="Name, email or phone" data-search></label>
    <div class="panel overflow-hidden" data-table><div class="skeleton h-48 m-4"></div></div>
    <p class="hint mt-3">Customer details are personal information. The CSV download is recorded in the audit log — store the file somewhere private and delete it when you're done.</p>`);

  async function load() {
    const data = await api("/admin/customers", { query: { q: state.q, page: state.page } });
    if (!ctx.isCurrent()) return;
    render(el.querySelector("[data-table]"), html`<div class="table-wrap"><table class="data-table">
      <thead><tr><th>Customer</th><th>Phone</th><th>City</th><th>Account</th><th class="num">Paid orders</th><th class="num">Spent</th><th class="num">Courses</th><th>Last order</th><th>Joined</th><th>Role</th></tr></thead>
      <tbody>${data.customers.length ? data.customers.map((c) => html`<tr data-href="#customers/${c.id}">
        <td><a href="#customers/${c.id}" class="block font-medium hover:text-terracotta-dark">${c.full_name || "—"}</a><span class="block text-xs text-charcoal-light">${c.email}</span></td>
        <td class="whitespace-nowrap">${c.phone || "—"}</td>
        <td>${c.city || "—"}</td>
        <td><div class="flex flex-wrap gap-1">
          ${c.verified ? html`<span class="chip chip-ok">Verified</span>` : html`<span class="chip chip-muted">Unverified</span>`}
          ${c.must_reset_password ? html`<span class="chip chip-warn">Password reset due</span>` : ""}
        </div></td>
        <td class="num">${c.orders}</td><td class="num">${rupees(c.spent_paise)}</td><td class="num">${c.courses}</td>
        <td class="whitespace-nowrap">${c.last_order_at ? relative(c.last_order_at) : "—"}</td>
        <td class="whitespace-nowrap">${dateOnly(c.created_at)}</td>
        <td>${roleSelect(c, ctx.user.id)}</td>
      </tr>`) : emptyRow(10, "No customers match.")}</tbody>
    </table></div>
    <div class="panel-head !border-t !border-b-0"><span class="text-sm text-charcoal-light">${data.total} customer${data.total === 1 ? "" : "s"}${data.total > 50 ? ` · page ${data.page} of ${Math.ceil(data.total / 50)}` : ""}</span>
      ${data.total > 50 ? html`<span class="flex gap-2"><button type="button" class="btn btn-outline btn-sm" data-page="${data.page - 1}" ${data.page === 1 ? "disabled" : ""}>Previous</button>
      <button type="button" class="btn btn-outline btn-sm" data-page="${data.page + 1}" ${data.page * 50 >= data.total ? "disabled" : ""}>Next</button></span>` : ""}</div>`);
  }

  let timer;
  el.querySelector("[data-search]").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value; state.page = 1; load(); }, 300); });
  el.onclick = (e) => {
    const b = e.target.closest("[data-page]");
    if (b) { state.page = Number(b.dataset.page); load(); }
  };
  el.onchange = async (e) => {
    const select = e.target.closest("[data-role]");
    if (select) { await changeRole(select); load(); }
  };
  await load();
}

async function customer(el, ctx, id) {
  const d = await api(`/admin/customers/${encodeURIComponent(id)}`);
  if (!ctx.isCurrent()) return;
  const c = d.customer;
  const s = d.stats;
  const phone = c.phone ? c.phone.replace(/\D/g, "") : "";

  render(el, html`
    ${back("#customers", "Customers")}
    <div class="flex flex-wrap items-start justify-between gap-4 mt-3 mb-6">
      <div class="flex items-center gap-4 min-w-0">
        <span class="w-14 h-14 rounded-full bg-terracotta text-white grid place-items-center font-serif text-2xl font-bold shrink-0" aria-hidden="true">${(c.full_name || c.email)[0].toUpperCase()}</span>
        <div class="min-w-0">
          <h1 class="font-serif text-3xl font-bold leading-tight">${c.full_name || c.email}</h1>
          <div class="flex flex-wrap gap-1.5 mt-2">
            <span class="chip ${c.role === "customer" ? "chip-muted" : "chip-brand"}">${{ admin: "Owner", staff: "Staff", customer: "Customer" }[c.role]}</span>
            ${c.email_verified ? html`<span class="chip chip-ok">Email verified</span>` : html`<span class="chip chip-muted">Email not verified</span>`}
            ${c.marketing_opt_in ? html`<span class="chip chip-info">Wants marketing emails</span>` : ""}
            ${c.must_reset_password ? html`<span class="chip chip-warn">Password reset due</span>` : c.has_password ? "" : html`<span class="chip chip-muted">Signs in with codes</span>`}
            ${c.imported_from_old_site ? html`<span class="chip chip-muted">From the old website</span>` : ""}
          </div>
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <a class="btn btn-outline btn-sm" href="mailto:${c.email}"><i data-lucide="mail" class="w-4 h-4"></i>Email</a>
        ${phone ? html`<a class="btn btn-outline btn-sm" href="tel:+91${phone}"><i data-lucide="phone" class="w-4 h-4"></i>Call</a>
          <a class="btn btn-outline btn-sm" href="https://wa.me/91${phone}" target="_blank" rel="noopener"><i data-lucide="message-circle" class="w-4 h-4"></i>WhatsApp</a>` : ""}
      </div>
    </div>

    <section class="grid sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
      <div class="panel stat"><p class="stat-label">Paid orders</p><p class="stat-value">${s.paid_orders}</p><p class="stat-sub">${d.orders.length} in total, including unpaid</p></div>
      <div class="panel stat"><p class="stat-label">Total spent</p><p class="stat-value">${rupees(s.spent_paise)}</p><p class="stat-sub">Paid orders only</p></div>
      <div class="panel stat"><p class="stat-label">Courses</p><p class="stat-value">${d.enrollments.filter((e) => !e.revoked_at).length}</p><p class="stat-sub">${d.bookings.length} workshop booking${d.bookings.length === 1 ? "" : "s"}</p></div>
      <div class="panel stat"><p class="stat-label">Customer since</p><p class="stat-value !text-xl">${dateOnly(c.created_at)}</p><p class="stat-sub">${s.last_seen_at ? `Last signed in ${relative(s.last_seen_at)}` : "Hasn't signed in yet"}</p></div>
    </section>

    <div class="grid xl:grid-cols-[minmax(0,1fr)_24rem] gap-4 items-start">
      <div class="space-y-4">
        <section class="panel">
          <div class="panel-head"><h2 class="font-semibold">Orders</h2></div>
          <div class="table-wrap"><table class="data-table">
            <thead><tr><th>Order</th><th>Items</th><th>Status</th><th class="num">Total</th><th>Placed</th></tr></thead>
            <tbody>${d.orders.length ? d.orders.map((o) => html`<tr data-href="#orders/${o.id}">
              <td class="font-semibold whitespace-nowrap">#${o.id}</td>
              <td><span class="block max-w-[11rem] truncate" title="${o.items_summary}">${o.items_summary}</span></td>
              <td>${statusChip(o.status, o.status_label)}</td>
              <td class="num">${rupees(o.total_paise)}</td>
              <td class="whitespace-nowrap">${dateOnly(o.created_at)}</td>
            </tr>`) : emptyRow(5, "No orders yet.")}</tbody>
          </table></div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2 class="font-semibold">Courses & workshops</h2></div>
          ${d.enrollments.length || d.bookings.length ? html`<ul class="divide-y divide-sand">
            ${d.enrollments.map((e) => html`<li class="px-4 py-3 flex flex-wrap items-center justify-between gap-3 text-sm">
              <span><span class="block font-medium">${e.course_title}</span><span class="text-xs text-charcoal-light">${e.source === "purchase" ? "Bought" : "Given by the studio"} ${dateOnly(e.granted_at)}</span></span>
              ${e.revoked_at ? html`<span class="chip chip-muted">Access revoked</span>` : html`<span class="w-40"><span class="progress block" role="progressbar" aria-label="Progress" aria-valuenow="${e.progress.percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${e.progress.percent}%"></span></span>
                <span class="text-xs text-charcoal-light">${e.progress.completed} of ${e.progress.total} lessons</span></span>`}
            </li>`)}
            ${d.bookings.map((b) => html`<li class="px-4 py-3 flex items-center justify-between gap-3 text-sm">
              <span><span class="block font-medium">${b.title}</span><span class="text-xs text-charcoal-light">${sessionLabel(b.starts_at)} · ${b.seats} seat${b.seats === 1 ? "" : "s"}</span></span>
              <span class="chip ${b.status === "confirmed" ? "chip-ok" : "chip-muted"}">${b.status === "confirmed" ? "Booked" : "Cancelled"}</span>
            </li>`)}
          </ul>` : html`<p class="panel-body text-sm text-charcoal-light">No courses or workshops.</p>`}
        </section>
      </div>

      <div class="space-y-4">
        <section class="panel panel-body">
          <h2 class="subhead mb-3">Contact</h2>
          <dl class="kv">
            <dt>Email</dt><dd><a class="link" href="mailto:${c.email}">${c.email}</a></dd>
            <dt>Phone</dt><dd>${c.phone ? html`<a class="link" href="tel:+91${phone}">+91 ${c.phone}</a>` : "Not given"}</dd>
            <dt>Marketing</dt><dd>${c.marketing_opt_in ? "Opted in" : "Not opted in"}</dd>
          </dl>
        </section>
        <section class="panel panel-body">
          <h2 class="subhead mb-3">Saved addresses</h2>
          ${d.addresses.length ? html`<ul class="space-y-4">${d.addresses.map((a) => html`<li class="text-sm leading-relaxed">
            <p class="font-semibold">${a.full_name}${a.is_default ? html` <span class="chip chip-brand ml-1">Default</span>` : ""}</p>
            <p>${a.line1}${a.line2 ? html`<br>${a.line2}` : ""}<br>${a.city}, ${a.state} ${a.pincode}</p>
            ${a.phone ? html`<p class="text-charcoal-light">Phone ${a.phone}</p>` : ""}
          </li>`)}</ul>` : html`<p class="text-sm text-charcoal-light">No saved addresses.</p>`}
        </section>
        ${c.id !== ctx.user.id ? html`<section class="panel panel-body">
          <h2 class="subhead mb-3">Admin access</h2>
          <p class="text-sm text-charcoal-light mb-3">Staff can pack and ship orders and update stock. Owners can do everything.</p>
          ${roleSelect(c, ctx.user.id)}
        </section>` : ""}
      </div>
    </div>`);

  el.onchange = async (e) => {
    const select = e.target.closest("[data-role]");
    if (select && await changeRole(select)) ctx.navigate(`#customers/${encodeURIComponent(id)}`);
    else if (select) select.value = c.role;
  };
}
