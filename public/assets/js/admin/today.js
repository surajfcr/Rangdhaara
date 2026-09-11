// Today: the action queue first, then the day's numbers.
import { api } from "../lib/api.js";
import { html, render } from "../lib/dom.js";
import { firstName, relative, rupees, sessionLabel } from "../lib/format.js";
import { statusChip } from "../lib/status.js";
import { pageHead } from "./ui.js";

const greeting = () => {
  const hour = Number(new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "numeric", hour12: false }));
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
};

const actionTile = (label, value, sub, href, tone) => html`
  <a href="${href}" class="panel stat ${value ? tone : ""}">
    <p class="stat-label">${label}</p>
    <p class="stat-value">${value}</p>
    <p class="stat-sub">${value ? sub : "Nothing waiting"}</p>
  </a>`;

export default async function today(el, ctx) {
  const d = await api("/admin/overview");
  if (!ctx.isCurrent()) return;
  const a = d.actions;
  render(el, html`
    ${pageHead(`${greeting()}, ${firstName(ctx.user)}`, "What needs attention in the studio right now.")}

    <section aria-label="Needs action" class="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
      ${actionTile("To pack", a.to_pack, "Paid orders waiting to be packed", "#orders/to_pack", "is-warn")}
      ${actionTile("To ship", a.to_ship, "Packed and waiting for a courier", "#orders/packed", "is-warn")}
      ${actionTile("Needs review", a.needs_review, "Payment or fulfilment problems", "#orders/review", "is-alert")}
      ${actionTile("Payments to confirm", a.stuck_payments, "UPI transfers, or checkouts unpaid for 20+ minutes", "#orders/pending_payment", "is-warn")}
    </section>

    <section class="grid md:grid-cols-3 gap-4 mt-4">
      <div class="panel stat">
        <p class="stat-label">Paid today</p>
        <p class="stat-value">${rupees(d.paid_today.total)}</p>
        <p class="stat-sub">${d.paid_today.count} order${d.paid_today.count === 1 ? "" : "s"}${d.pending_payment.count ? ` · ${d.pending_payment.count} checkout${d.pending_payment.count === 1 ? "" : "s"} awaiting payment` : ""}</p>
      </div>
      <div class="panel stat">
        <p class="stat-label">Enrolments in the last 7 days</p>
        <p class="stat-value">${d.enrollments_week}</p>
        <p class="stat-sub">Across all masterclasses</p>
      </div>
      <div class="panel stat">
        <p class="stat-label">Next workshop</p>
        ${d.next_workshop ? html`
          <p class="stat-value !text-xl">${sessionLabel(d.next_workshop.starts_at)}</p>
          <p class="stat-sub">${d.next_workshop.title} · ${d.next_workshop.seats_taken} of ${d.next_workshop.seats_total} seats booked</p>`
          : html`<p class="stat-sub !mt-3">No sessions scheduled. <a class="link" href="#workshops">Schedule one</a></p>`}
      </div>
    </section>

    <section class="grid xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)] gap-4 mt-4">
      <div class="panel">
        <div class="panel-head"><h2 class="font-semibold">Latest orders</h2><a href="#orders/all" class="link text-sm">All orders</a></div>
        <div class="table-wrap"><table class="data-table">
          <thead><tr><th>Order</th><th>Customer</th><th>Status</th><th class="num">Total</th><th>Placed</th></tr></thead>
          <tbody>${d.recent_orders.length ? d.recent_orders.map((o) => html`
            <tr data-href="#orders/${o.id}">
              <td class="font-semibold whitespace-nowrap">#${o.id}</td>
              <td><span class="block truncate max-w-[14rem]">${o.customer_name}</span><span class="block text-xs text-charcoal-light truncate max-w-[14rem]">${o.items_summary}</span></td>
              <td>${statusChip(o.status, o.status_label)}</td>
              <td class="num">${rupees(o.total_paise)}</td>
              <td class="whitespace-nowrap text-charcoal-light">${relative(o.created_at)}</td>
            </tr>`) : html`<tr><td colspan="5" class="text-center text-charcoal-light !py-10">No orders yet.</td></tr>`}
          </tbody>
        </table></div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2 class="font-semibold">Low stock</h2><a href="#catalogue" class="link text-sm">Catalogue</a></div>
        ${d.low_stock.length ? html`<ul class="divide-y divide-sand">${d.low_stock.map((s) => html`
          <li class="px-4 py-3 flex items-center justify-between gap-3 text-sm">
            <a href="#catalogue/${s.product_id}" class="min-w-0 hover:text-terracotta-dark"><span class="block truncate font-medium">${s.title}</span>${s.label ? html`<span class="block text-xs text-charcoal-light truncate">${s.label}</span>` : ""}</a>
            <span class="chip ${s.available === 0 ? "chip-bad" : "chip-warn"}">${s.available === 0 ? "Sold out" : `${s.available} left`}</span>
          </li>`)}</ul>`
          : html`<p class="panel-body text-sm text-charcoal-light">Every live piece has more than 2 in stock.</p>`}
      </div>
    </section>`);
}
