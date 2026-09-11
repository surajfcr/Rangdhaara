// Orders: filtered lists with bulk actions, and the order detail with every fulfilment step.
import { api } from "../lib/api.js";
import { busy, html, on, render, toast } from "../lib/dom.js";
import { dateTime, dayLabel, relative, rupees } from "../lib/format.js";
import { KIND_LABEL, statusChip } from "../lib/status.js";
import { PROVIDER_LABELS, REVIEW_LABELS, back, emptyRow, field, formDialog, pageHead } from "./ui.js";

const FILTERS = [
  ["to_pack", "To pack"],
  ["packed", "To ship"],
  ["shipped", "Shipped"],
  ["pending_payment", "Awaiting payment"],
  ["review", "Needs review"],
  ["delivered", "Delivered"],
  ["all", "All"],
];
const COURIERS = ["DTDC", "Blue Dart", "Delhivery", "India Post", "Shiprocket", "Other"];
const GATEWAY_REFUNDS = new Set(["razorpay", "cashfree", "mock"]);
const REVIEW_HELP = {
  amount_mismatch: "The gateway reported a different amount from the order total, so nothing was fulfilled. Compare the payment in your gateway dashboard before doing anything else.",
  oversold: "This order was paid after its stock hold lapsed and there wasn't enough stock left. Contact the customer about a remake date or a refund.",
  fulfilment_failed: "The payment is recorded, but course access or the confirmation email failed. Retry fulfilment below.",
  payment_after_close: "Money arrived for an order that was already cancelled or refunded. Refund it from your gateway dashboard.",
  legacy_unverified_payment: "The old website marked this order confirmed without checking payment. Match it against your UPI records, then mark it paid or cancel it.",
};

export default async function orders(el, ctx) {
  const [param] = ctx.params;
  if (param && !FILTERS.some(([id]) => id === param)) return detail(el, ctx, param);
  return list(el, ctx, param || "to_pack");
}

// ------------------------------------------------------------------ list

async function list(el, ctx, filter) {
  const state = { q: "", page: 1, selected: new Set(), data: null };
  const bulkStatus = { to_pack: "packed", shipped: "delivered" }[filter];

  async function load() {
    const query = { page: state.page, per_page: 30, q: state.q };
    if (filter === "review") query.review = "true";
    else if (filter !== "all") query.status = filter === "to_pack" ? "to_pack" : filter;
    state.data = await api("/admin/orders", { query });
    if (ctx.isCurrent()) paintTable();
  }

  function paintTable() {
    const { orders: rows, total, page, per_page: perPage } = state.data;
    const slot = el.querySelector("[data-table]");
    render(slot, html`
      ${bulkStatus ? html`<div class="panel-head !py-2.5 ${state.selected.size ? "" : "hidden"}" data-bulkbar>
        <span class="text-sm font-semibold">${state.selected.size} selected</span>
        <button type="button" class="btn btn-primary btn-sm" data-bulk="${bulkStatus}">Mark ${bulkStatus}</button>
      </div>` : ""}
      <div class="table-wrap"><table class="data-table">
        <thead><tr>
          ${bulkStatus ? html`<th class="w-8"><input type="checkbox" class="checkbox" data-select-all aria-label="Select all"></th>` : ""}
          <th>Order</th><th>Customer</th><th>Items</th><th>Ship to</th><th>Status</th><th class="num">Total</th><th>Placed</th>
        </tr></thead>
        <tbody>${rows.length ? rows.map((o) => html`
          <tr data-href="#orders/${o.id}">
            ${bulkStatus ? html`<td><input type="checkbox" class="checkbox" data-select="${o.id}" ${state.selected.has(o.id) ? "checked" : ""} aria-label="Select order ${o.id}"></td>` : ""}
            <td class="font-semibold whitespace-nowrap">#${o.id}</td>
            <td><span class="block max-w-[12rem] truncate">${o.customer_name}</span><span class="block text-xs text-charcoal-light max-w-[12rem] truncate">${o.email}</span></td>
            <td><span class="block max-w-[16rem] truncate">${o.items_summary}</span></td>
            <td class="whitespace-nowrap text-charcoal-light">${o.requires_shipping ? `${o.ship_city} ${o.ship_pincode}` : "Digital"}</td>
            <td><div class="flex flex-col gap-1">${statusChip(o.status, o.status_label)}${o.needs_review ? html`<span class="chip chip-bad">${REVIEW_LABELS[o.needs_review] || "Review"}</span>` : ""}</div></td>
            <td class="num">${rupees(o.total_paise)}</td>
            <td class="whitespace-nowrap text-charcoal-light">${relative(o.created_at)}</td>
          </tr>`) : emptyRow(bulkStatus ? 8 : 7, state.q ? "No orders match that search." : "Nothing here right now.")}
        </tbody>
      </table></div>
      ${total > perPage ? html`<div class="panel-head !border-t !border-b-0">
        <span class="text-sm text-charcoal-light">${(page - 1) * perPage + 1}–${Math.min(page * perPage, total)} of ${total}</span>
        <span class="flex gap-2"><button type="button" class="btn btn-outline btn-sm" data-page="${page - 1}" ${page === 1 ? "disabled" : ""}>Previous</button>
        <button type="button" class="btn btn-outline btn-sm" data-page="${page + 1}" ${page * perPage >= total ? "disabled" : ""}>Next</button></span>
      </div>` : ""}`);
  }

  render(el, html`
    ${pageHead("Orders", "Pack, ship and resolve orders. Click a row for details.")}
    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
      <nav class="filter-row" aria-label="Order filters">${FILTERS.map(([id, label]) => html`<a href="#orders/${id}" class="filter-btn" aria-current="${id === filter}">${label}</a>`)}</nav>
      <label class="relative"><span class="sr-only">Search orders</span>
        <i data-lucide="search" class="w-4 h-4 text-charcoal-light absolute left-3 top-1/2 -translate-y-1/2"></i>
        <input type="search" class="input !py-2 !pl-9 w-72" placeholder="Order, name, email, phone or AWB" data-search></label>
    </div>
    <div class="panel overflow-hidden" data-table><div class="skeleton h-64 m-4"></div></div>`);

  let searchTimer;
  el.querySelector("[data-search]").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value; state.page = 1; load(); }, 300);
  });
  el.onclick = async (e) => {
    const pageBtn = e.target.closest("[data-page]");
    if (pageBtn) { state.page = Number(pageBtn.dataset.page); load(); return; }
    const bulk = e.target.closest("[data-bulk]");
    if (bulk) {
      busy(bulk, true, "Updating…");
      try {
        const res = await api("/admin/orders/bulk-status", { method: "POST", body: { order_ids: [...state.selected], status: bulk.dataset.bulk } });
        toast(`${res.updated.length} order${res.updated.length === 1 ? "" : "s"} marked ${bulk.dataset.bulk}.${res.failed.length ? ` ${res.failed.length} couldn't be updated.` : ""}`, res.failed.length ? "error" : "success");
        state.selected.clear();
        ctx.refreshCounts();
        load();
      } catch (err) {
        busy(bulk, false);
        toast(err.message, "error");
      }
    }
  };
  el.onchange = (e) => {
    const box = e.target.closest("[data-select]");
    const all = e.target.closest("[data-select-all]");
    if (box) {
      if (box.checked) state.selected.add(box.dataset.select);
      else state.selected.delete(box.dataset.select);
    } else if (all) {
      state.data.orders.forEach((o) => (all.checked ? state.selected.add(o.id) : state.selected.delete(o.id)));
      el.querySelectorAll("[data-select]").forEach((b) => { b.checked = all.checked; });
    } else return;
    const bar = el.querySelector("[data-bulkbar]");
    if (bar) {
      bar.classList.toggle("hidden", !state.selected.size);
      bar.querySelector("span").textContent = `${state.selected.size} selected`;
    }
  };
  await load();
}

// ------------------------------------------------------------------ detail

async function detail(el, ctx, id) {
  let o = await api(`/admin/orders/${encodeURIComponent(id)}`);
  const owner = ctx.user.role === "admin";

  const act = async (button, label, request, success) => {
    busy(button, true, label);
    try {
      const res = await request();
      o = res.order || res;
      if (success) toast(typeof success === "function" ? success(res) : success, "success");
      ctx.refreshCounts();
      paint();
    } catch (err) {
      busy(button, false);
      toast(err.message, "error");
    }
  };

  function actions() {
    const buttons = [];
    const paid = ["paid", "packed", "shipped", "delivered"].includes(o.status);
    if (["pending_payment", "expired"].includes(o.status)) {
      if (!["manual_upi", "legacy"].includes(o.payment.provider)) buttons.push(html`<button type="button" class="btn btn-outline btn-sm" data-act="check"><i data-lucide="refresh-cw" class="w-4 h-4"></i>Check payment with gateway</button>`);
      if (owner && ["manual_upi", "legacy"].includes(o.payment.provider)) buttons.push(html`<button type="button" class="btn btn-primary btn-sm" data-act="mark-paid"><i data-lucide="badge-check" class="w-4 h-4"></i>Mark as paid</button>`);
      if (owner) buttons.push(html`<button type="button" class="btn btn-ghost btn-sm text-red-700" data-act="cancel">Cancel order</button>`);
    }
    if (o.status === "paid" && o.requires_shipping) buttons.push(html`<button type="button" class="btn btn-primary btn-sm" data-act="packed"><i data-lucide="package" class="w-4 h-4"></i>Mark packed</button>`);
    if (["paid", "packed"].includes(o.status) && o.requires_shipping) buttons.push(html`<button type="button" class="btn ${o.status === "packed" ? "btn-primary" : "btn-outline"} btn-sm" data-act="shipped"><i data-lucide="truck" class="w-4 h-4"></i>Mark shipped</button>`);
    if (o.status === "shipped") buttons.push(html`<button type="button" class="btn btn-primary btn-sm" data-act="delivered"><i data-lucide="check-circle-2" class="w-4 h-4"></i>Mark delivered</button>`);
    if (o.requires_shipping && paid) buttons.push(html`<a class="btn btn-outline btn-sm" href="/admin/orders/${encodeURIComponent(o.id)}/packing-slip" target="_blank" rel="noopener"><i data-lucide="printer" class="w-4 h-4"></i>Packing slip</a>`);
    if (owner && paid) buttons.push(html`<button type="button" class="btn btn-ghost btn-sm text-red-700" data-act="refund">Refund</button>`);
    buttons.push(html`<button type="button" class="btn btn-ghost btn-sm" data-act="note"><i data-lucide="message-square-plus" class="w-4 h-4"></i>Add note</button>`);
    return buttons;
  }

  function paint() {
    const address = o.shipping_address;
    render(el, html`
      ${back("#orders/all", "Orders")}
      <div class="flex flex-wrap items-start justify-between gap-4 mt-3 mb-6">
        <div>
          <h1 class="font-serif text-3xl font-bold">#${o.id}</h1>
          <p class="text-sm text-charcoal-light mt-1">Placed ${dateTime(o.created_at)} · ${rupees(o.total_paise)} · ${PROVIDER_LABELS[o.payment.provider] || o.payment.provider}</p>
          <div class="flex flex-wrap gap-2 mt-3">
            ${statusChip(o.status, o.status_label)}
            ${o.needs_review ? html`<span class="chip chip-bad"><i data-lucide="alert-triangle" class="w-3.5 h-3.5"></i>${REVIEW_LABELS[o.needs_review] || o.needs_review}</span>` : ""}
            ${o.requires_shipping ? "" : html`<span class="chip chip-muted">Digital only</span>`}
          </div>
        </div>
        <div class="flex flex-wrap gap-2 justify-end max-w-2xl">${actions()}</div>
      </div>

      ${o.needs_review ? html`
        <div class="panel panel-body mb-4 border-red-200 bg-[#FFFBFA] flex flex-wrap items-start justify-between gap-4">
          <div class="max-w-3xl"><p class="font-semibold text-red-800">${REVIEW_LABELS[o.needs_review] || "Needs review"}</p><p class="text-sm mt-1">${REVIEW_HELP[o.needs_review] || ""}</p>
          ${o.fulfilment_error ? html`<p class="text-xs text-charcoal-light mt-2 font-mono">${o.fulfilment_error}</p>` : ""}</div>
          <div class="flex gap-2">
            ${o.fulfilment_error ? html`<button type="button" class="btn btn-primary btn-sm" data-act="retry">Retry fulfilment</button>` : ""}
            ${owner ? html`<button type="button" class="btn btn-outline btn-sm" data-act="clear-review">Mark resolved</button>` : ""}
          </div>
        </div>` : ""}

      <div class="grid xl:grid-cols-[minmax(0,1fr)_23rem] gap-4 items-start">
        <div class="space-y-4">
          <section class="panel">
            <div class="panel-head"><h2 class="font-semibold">Items</h2><span class="text-sm text-charcoal-light">${o.items.reduce((n, i) => n + i.qty, 0)} total</span></div>
            <div class="table-wrap"><table class="data-table">
              <thead><tr><th>Item</th><th>Type</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Amount</th></tr></thead>
              <tbody>${o.items.map((i) => html`<tr>
                <td><div class="flex items-center gap-3"><img src="${i.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-11 h-11 rounded-lg object-cover bg-sand">
                  <span><span class="block font-medium">${i.title}</span>${i.variant_label ? html`<span class="block text-xs text-charcoal-light">${i.variant_label}</span>` : ""}</span></div></td>
                <td class="text-charcoal-light whitespace-nowrap">${KIND_LABEL[i.kind] || i.kind}</td>
                <td class="num">${i.qty}</td><td class="num">${rupees(i.unit_price_paise)}</td><td class="num">${rupees(i.line_total_paise)}</td>
              </tr>`)}</tbody>
            </table></div>
            <dl class="totals !m-0 px-4 pb-4">
              <div><dt>Subtotal</dt><dd>${rupees(o.subtotal_paise)}</dd></div>
              ${o.discount_paise ? html`<div><dt>Discount ${o.coupon_code ? `(${o.coupon_code})` : ""}</dt><dd>−${rupees(o.discount_paise)}</dd></div>` : ""}
              ${o.requires_shipping ? html`<div><dt>Shipping</dt><dd>${o.shipping_paise ? rupees(o.shipping_paise) : "Free"}</dd></div>` : ""}
              <div class="total"><dt>Total</dt><dd>${rupees(o.total_paise)}</dd></div>
            </dl>
          </section>

          ${o.requires_shipping && ["paid", "packed", "shipped", "delivered"].includes(o.status) ? html`
            <section class="panel">
              <div class="panel-head"><h2 class="font-semibold">Shipping</h2>
                ${o.delivery ? html`<span class="text-sm text-charcoal-light">Promised ${dayLabel(o.delivery.min_date)} – ${dayLabel(o.delivery.max_date)}</span>` : ""}</div>
              <form class="panel-body grid sm:grid-cols-[12rem_1fr_auto] gap-3 items-end" data-tracking-form>
                ${field("Courier", html`<select class="input" name="courier"><option value="">Choose</option>${COURIERS.map((c) => html`<option ${c === o.courier ? "selected" : ""}>${c}</option>`)}</select>`)}
                ${field("Tracking number (AWB)", html`<input class="input" name="awb" value="${o.awb || ""}">`)}
                <button type="submit" class="btn btn-outline">Save tracking</button>
              </form>
            </section>` : ""}

          <section class="panel">
            <div class="panel-head"><h2 class="font-semibold">Timeline</h2></div>
            <ol class="timeline panel-body">${[...o.events].reverse().map((e) => html`
              <li><span class="font-medium">${e.to_status ? `${e.from_status ? "Status changed to" : "Created as"} ${e.to_status.replace("_", " ")}` : "Note"}</span>
                <span class="text-charcoal-light"> · ${dateTime(e.created_at)}${e.actor_email ? ` · ${e.actor_email}` : ""}</span>
                ${e.note ? html`<p class="text-charcoal-light mt-0.5">${e.note}</p>` : ""}</li>`)}
            </ol>
          </section>

          ${o.payment_events.length ? html`
            <section class="panel">
              <div class="panel-head"><h2 class="font-semibold">Gateway events</h2><span class="text-xs text-charcoal-light">Every webhook received for this order</span></div>
              <div class="table-wrap"><table class="data-table">
                <thead><tr><th>Received</th><th>Event</th><th>Signature</th><th class="num">Amount</th><th>Outcome</th></tr></thead>
                <tbody>${o.payment_events.map((p) => html`<tr>
                  <td class="whitespace-nowrap">${dateTime(p.received_at)}</td><td>${p.event_type}</td>
                  <td>${p.signature_valid ? html`<span class="chip chip-ok">Valid</span>` : html`<span class="chip chip-bad">Invalid</span>`}</td>
                  <td class="num">${p.amount_paise !== null ? rupees(p.amount_paise) : "—"}</td><td>${(p.outcome || "").replace(/_/g, " ")}</td>
                </tr>`)}</tbody>
              </table></div>
            </section>` : ""}
        </div>

        <div class="space-y-4">
          <section class="panel panel-body">
            <h2 class="subhead mb-3">Customer</h2>
            <p class="font-semibold">${o.customer.name}</p>
            <p class="text-sm"><a class="link" href="mailto:${o.customer.email}">${o.customer.email}</a></p>
            <p class="text-sm"><a class="link" href="tel:+91${o.customer.phone}">+91 ${o.customer.phone}</a> · <a class="link" target="_blank" rel="noopener" href="https://wa.me/91${o.customer.phone}">WhatsApp</a></p>
            <p class="text-xs text-charcoal-light mt-2">${o.marketing_opt_in ? "Opted in to marketing emails" : "Not opted in to marketing emails"}</p>
          </section>
          ${address ? html`
            <section class="panel panel-body">
              <div class="flex items-center justify-between"><h2 class="subhead">Ship to</h2><button type="button" class="link text-xs" data-copy-address>Copy</button></div>
              <address class="not-italic text-sm mt-3 leading-relaxed">
                <strong>${address.full_name}</strong><br>${address.line1}${address.line2 ? html`<br>${address.line2}` : ""}<br>${address.city}, ${address.state}<br>PIN ${address.pincode}<br>Phone ${address.phone}
              </address>
            </section>` : ""}
          ${o.access.length ? html`
            <section class="panel panel-body">
              <h2 class="subhead mb-3">Academy access</h2>
              <ul class="space-y-2 text-sm">${o.access.map((a) => html`<li class="flex items-center justify-between gap-2"><span>${a.title}${a.label ? ` · ${a.label}` : ""}</span>${a.active ? html`<span class="chip chip-ok">Active</span>` : html`<span class="chip chip-muted">Revoked</span>`}</li>`)}</ul>
            </section>` : ""}
          <section class="panel panel-body">
            <h2 class="subhead mb-3">Payment</h2>
            <dl class="kv">
              <dt>Method</dt><dd>${PROVIDER_LABELS[o.payment.provider] || o.payment.provider}</dd>
              ${o.paid_at ? html`<dt>Paid</dt><dd>${dateTime(o.paid_at)}</dd>` : ""}
              ${o.gateway_order_id ? html`<dt>Gateway order</dt><dd class="font-mono text-xs">${o.gateway_order_id}</dd>` : ""}
              ${o.gateway_payment_id ? html`<dt>Payment ID</dt><dd class="font-mono text-xs">${o.gateway_payment_id}</dd>` : ""}
              ${o.payment_reference ? html`<dt>Reference</dt><dd>${o.payment_reference}</dd>` : ""}
              ${o.refunded_at ? html`<dt>Refunded</dt><dd>${dateTime(o.refunded_at)}</dd>` : ""}
            </dl>
          </section>
        </div>
      </div>`);
  }

  el.onclick = async (e) => {
    if (e.target.closest("[data-copy-address]")) {
      const a = o.shipping_address;
      const text = [a.full_name, a.line1, a.line2, `${a.city}, ${a.state} ${a.pincode}`, `Phone ${a.phone}`].filter(Boolean).join("\n");
      try { await navigator.clipboard.writeText(text); toast("Address copied.", "success"); } catch { toast("Couldn't copy — select the address instead.", "error"); }
      return;
    }
    const button = e.target.closest("[data-act]");
    if (!button) return;
    const path = `/admin/orders/${encodeURIComponent(o.id)}`;
    const action = button.dataset.act;

    if (action === "check") {
      return act(button, "Checking…", () => api(`${path}/check-payment`, { method: "POST" }),
        (res) => (res.outcome === "paid" ? "Payment confirmed and the order is now paid." : `Gateway says: ${res.outcome.replace(/_/g, " ")}.`));
    }
    if (action === "packed" || action === "delivered") {
      return act(button, "Updating…", () => api(`${path}/status`, { method: "POST", body: { status: action } }), `Marked ${action}.`);
    }
    if (action === "retry") return act(button, "Retrying…", () => api(`${path}/retry-fulfilment`, { method: "POST" }), "Fulfilment retried.");

    let result = null;
    if (action === "shipped") {
      result = await formDialog({
        title: "Mark as shipped",
        description: "The customer gets an email with the courier and tracking number.",
        submitLabel: "Mark shipped",
        body: html`
          ${field("Courier", html`<select class="input" name="courier" required><option value="">Choose</option>${COURIERS.map((c) => html`<option ${c === o.courier ? "selected" : ""}>${c}</option>`)}</select>`)}
          ${field("Tracking number (AWB)", html`<input class="input" name="awb" required value="${o.awb || ""}" autofocus>`)}`,
        onSubmit: (v) => api(`${path}/status`, { method: "POST", body: { status: "shipped", courier: v.courier, awb: v.awb } }),
      });
      if (result) toast("Marked shipped. The customer has been emailed.", "success");
    } else if (action === "mark-paid") {
      result = await formDialog({
        title: `Mark #${o.id} as paid`,
        description: `Only do this after you've seen ${rupees(o.total_paise)} arrive in your account. The customer is emailed a confirmation.`,
        submitLabel: "Confirm payment",
        body: field("UPI transaction reference", html`<input class="input" name="reference" required minlength="3" placeholder="e.g. 4521 8890 3310" autofocus>`, "Kept on the order so the payment can be traced later."),
        onSubmit: (v) => api(`${path}/mark-paid`, { method: "POST", body: { reference: v.reference } }),
      });
      if (result) toast("Payment recorded.", "success");
    } else if (action === "cancel") {
      result = await formDialog({
        title: `Cancel #${o.id}?`,
        description: "Its stock hold is released. No email is sent.",
        submitLabel: "Cancel order",
        danger: true,
        body: field("Reason", html`<input class="input" name="note" placeholder="e.g. Customer asked to cancel" autofocus>`),
        onSubmit: (v) => api(`${path}/cancel`, { method: "POST", body: { note: v.note } }),
      });
      if (result) toast("Order cancelled.", "success");
    } else if (action === "refund") {
      const canGateway = GATEWAY_REFUNDS.has(o.payment.provider) && Boolean(o.gateway_payment_id);
      const shipped = ["shipped", "delivered"].includes(o.status);
      result = await formDialog({
        title: `Refund ${rupees(o.total_paise)}?`,
        description: "Course access and workshop seats from this order are removed, and the customer is emailed.",
        submitLabel: "Refund order",
        danger: true,
        body: html`
          ${field("Reason", html`<input class="input" name="note" placeholder="e.g. Arrived damaged" autofocus>`)}
          <label class="flex items-start gap-2.5 text-sm"><input type="checkbox" class="checkbox mt-0.5" name="via_gateway" ${canGateway ? "checked" : "disabled"}>
            <span>Send the money back through ${PROVIDER_LABELS[o.payment.provider] || "the gateway"}${canGateway ? "" : html`<span class="block hint">Not available for this payment. Refund the customer yourself — this only records it.</span>`}</span></label>
          <label class="flex items-start gap-2.5 text-sm"><input type="checkbox" class="checkbox mt-0.5" name="restock" ${shipped ? "" : "checked"}>
            <span>Put the items back into stock<span class="block hint">Leave this off if the piece hasn't come back to the studio.</span></span></label>`,
        onSubmit: (v) => api(`${path}/refund`, { method: "POST", body: { note: v.note, via_gateway: Boolean(v.via_gateway), restock: Boolean(v.restock) } }),
      });
      if (result) toast("Refund recorded.", "success");
    } else if (action === "clear-review") {
      result = await formDialog({
        title: "Mark this as resolved",
        description: "Explain what you did. The note is saved on the order's timeline.",
        submitLabel: "Mark resolved",
        body: field("What did you do?", html`<textarea class="input" name="note" rows="3" required autofocus></textarea>`),
        onSubmit: (v) => api(`${path}/clear-review`, { method: "POST", body: { note: v.note } }),
      });
    } else if (action === "note") {
      result = await formDialog({
        title: "Add a note",
        description: "Only visible to staff, on this order's timeline.",
        submitLabel: "Add note",
        body: field("Note", html`<textarea class="input" name="note" rows="3" required autofocus></textarea>`),
        onSubmit: (v) => api(`${path}/notes`, { method: "POST", body: { note: v.note } }),
      });
    }
    if (result) {
      o = result;
      ctx.refreshCounts();
      paint();
    }
  };

  el.onsubmit = async (e) => {
    const form = e.target.closest("[data-tracking-form]");
    if (!form) return;
    e.preventDefault();
    const button = form.querySelector("button");
    busy(button, true, "Saving…");
    try {
      o = await api(`/admin/orders/${encodeURIComponent(o.id)}/tracking`, { method: "POST", body: { courier: form.courier.value, awb: form.awb.value } });
      toast("Tracking saved.", "success");
      paint();
    } catch (err) {
      busy(button, false);
      toast(err.message, "error");
    }
  };

  paint();
}
