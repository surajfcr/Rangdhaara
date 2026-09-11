// System health (go-live checklist, email, jobs, backups) and the audit log.
import { api } from "../lib/api.js";
import { busy, html, render, toast } from "../lib/dom.js";
import { dateTime, relative } from "../lib/format.js";
import { emptyRow, pageHead } from "./ui.js";

const JOB_LABELS = {
  holds: "Release expired stock holds",
  expire: "Close unpaid checkouts",
  reconcile: "Re-check payments with the gateway",
  abandoned: "Send cart reminders",
  cleanup: "Clear expired codes and sessions",
  backup: "Database backup",
};

export default async function system(el, ctx) {
  return ctx.route === "audit" ? audit(el, ctx) : health(el, ctx);
}

async function health(el, ctx) {
  const s = await api("/admin/system");
  if (!ctx.isCurrent()) return;
  render(el, html`
    ${pageHead("System health", "Configuration, email delivery, background jobs and backups.")}

    <section class="panel mb-4">
      <div class="panel-head"><h2 class="font-semibold">Ready to take real orders?</h2>
        ${s.production_blockers.length ? html`<span class="chip chip-warn">${s.production_blockers.length} to fix</span>` : html`<span class="chip chip-ok">Ready</span>`}</div>
      <div class="panel-body">
        ${s.production_blockers.length ? html`
          <p class="text-sm text-charcoal-light mb-3">The server refuses to start with <code>APP_ENV=production</code> until these are set in <code>.env</code>:</p>
          <ul class="space-y-2">${s.production_blockers.map((b) => html`<li class="flex gap-2 text-sm"><i data-lucide="circle-alert" class="w-4 h-4 text-amber-700 shrink-0 mt-0.5"></i>${b}</li>`)}</ul>`
          : html`<p class="text-sm">Production settings are complete.</p>`}
      </div>
    </section>

    <div class="grid lg:grid-cols-2 gap-4">
      <section class="panel">
        <div class="panel-head"><h2 class="font-semibold">Configuration</h2></div>
        <dl class="kv panel-body">
          <dt>Environment</dt><dd>${s.environment}</dd>
          <dt>Public address</dt><dd>${s.public_base_url}</dd>
          <dt>Payments</dt><dd>${s.payment_label}</dd>
          <dt>Email</dt><dd>${s.smtp_configured ? "Sending through SMTP" : html`Not configured — emails are saved to <code>data/outbox</code>`}</dd>
          <dt>Order alerts go to</dt><dd>${s.store_admin_email || "Not set"}</dd>
        </dl>
        <div class="panel-body border-t border-sand">
          <p class="subhead mb-2">Webhook addresses</p>
          <p class="hint mb-3">Paste the one for your gateway into its dashboard so payments confirm instantly.</p>
          <dl class="kv">${Object.entries(s.webhook_urls).map(([name, url]) => html`<dt class="capitalize">${name}</dt><dd><code class="text-xs">${url}</code></dd>`)}</dl>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h2 class="font-semibold">Email delivery</h2>
          <button type="button" class="btn btn-outline btn-sm" data-test-email><i data-lucide="send" class="w-4 h-4"></i>Send me a test email</button></div>
        <div class="panel-body flex flex-wrap gap-2">
          ${["sent", "logged", "queued", "failed"].map((k) => html`<span class="chip ${k === "failed" && s.outbox[k] ? "chip-bad" : k === "sent" ? "chip-ok" : "chip-muted"}">${s.outbox[k] || 0} ${{ sent: "sent", logged: "saved locally", queued: "waiting", failed: "failed" }[k]}</span>`)}
        </div>
        ${s.failed_emails.length ? html`<div class="table-wrap border-t border-sand"><table class="data-table">
          <thead><tr><th>To</th><th>Subject</th><th>Error</th></tr></thead>
          <tbody>${s.failed_emails.map((f) => html`<tr><td>${f.to_email}</td><td>${f.subject}</td><td class="text-xs text-red-700">${f.last_error}</td></tr>`)}</tbody>
        </table></div>` : html`<p class="panel-body border-t border-sand text-sm text-charcoal-light">No failed emails.</p>`}
      </section>

      <section class="panel">
        <div class="panel-head"><h2 class="font-semibold">Background jobs</h2></div>
        <ul class="divide-y divide-sand">${Object.entries(s.jobs).map(([name, at]) => html`
          <li class="px-4 py-2.5 flex justify-between gap-3 text-sm"><span>${JOB_LABELS[name] || name}</span><span class="text-charcoal-light">${at ? relative(at) : "Not run yet"}</span></li>`)}
        </ul>
      </section>

      <section class="panel">
        <div class="panel-head"><h2 class="font-semibold">Backups</h2><span class="text-xs text-charcoal-light">data/backups · last 14 days kept</span></div>
        ${s.backups.length ? html`<ul class="divide-y divide-sand">${s.backups.map((b) => html`<li class="px-4 py-2.5 text-sm font-mono">${b}</li>`)}</ul>`
          : html`<p class="panel-body text-sm text-charcoal-light">The first backup is written within a minute of the server starting.</p>`}
      </section>
    </div>`);

  el.onclick = async (e) => {
    const button = e.target.closest("[data-test-email]");
    if (!button) return;
    busy(button, true, "Sending…");
    try {
      const res = await api("/admin/system/test-email", { method: "POST" });
      if (res.status === "sent") toast(`Sent to ${ctx.user.email}.`, "success");
      else if (res.status === "logged") toast("SMTP isn't set up, so it was saved to data/outbox instead.");
      else toast(`Not sent: ${res.error || "still waiting"}`, "error");
    } catch (err) {
      toast(err.message, "error");
    }
    busy(button, false);
  };
}

async function audit(el, ctx) {
  const page = Math.max(1, Number(ctx.params[0]) || 1);
  const data = await api("/admin/audit", { query: { page } });
  if (!ctx.isCurrent()) return;
  const pages = Math.max(1, Math.ceil(data.total / 100));
  render(el, html`
    ${pageHead("Audit log", "Every change made from the admin panel or manage.py: who, what and when.")}
    <div class="panel overflow-hidden"><div class="table-wrap"><table class="data-table">
      <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Details</th></tr></thead>
      <tbody>${data.entries.length ? data.entries.map((a) => html`<tr>
        <td class="whitespace-nowrap" title="${dateTime(a.created_at)}">${relative(a.created_at)}</td>
        <td class="whitespace-nowrap">${a.actor_email || "system"}</td>
        <td class="whitespace-nowrap font-medium">${a.action}</td>
        <td class="whitespace-nowrap">${a.entity === "order" && a.entity_id ? html`<a class="link" href="#orders/${a.entity_id}">#${a.entity_id}</a>` : `${a.entity}${a.entity_id ? ` ${a.entity_id}` : ""}`}</td>
        <td><code class="text-xs text-charcoal-light break-all line-clamp-2 max-w-md block">${a.detail || ""}</code></td>
      </tr>`) : emptyRow(5, "Nothing has been changed yet.")}</tbody>
    </table></div>
    ${pages > 1 ? html`<div class="panel-head !border-t !border-b-0"><span class="text-sm text-charcoal-light">Page ${page} of ${pages}</span>
      <span class="flex gap-2"><a class="btn btn-outline btn-sm ${page === 1 ? "pointer-events-none opacity-50" : ""}" href="#audit/${page - 1}">Newer</a>
      <a class="btn btn-outline btn-sm ${page >= pages ? "pointer-events-none opacity-50" : ""}" href="#audit/${page + 1}">Older</a></span></div>` : ""}
    </div>`);
}
