// Order status page. After checkout every payment path lands here; this page asks the
// server (which asks the gateway) whether the payment went through — it never assumes.
import { api } from "../lib/api.js";
import { openAuth } from "../lib/auth.js";
import { $, busy, html, on, raw, render, toast } from "../lib/dom.js";
import { dateTime, dayLabel, rupees, sessionLabel } from "../lib/format.js";
import { launchPayment, tokenFor } from "../lib/payments.js";
import { boot, getUser, whatsappLink } from "../lib/session.js";
import { KIND_LABEL, statusChip } from "../lib/status.js";

const orderId = decodeURIComponent(location.pathname.split("/")[2] || "");
const token = tokenFor(orderId);
const root = $("#order-root");
const PAID = ["paid", "packed", "shipped", "delivered"];
const WAITS_FOR_STUDIO = ["manual_upi", "legacy"];

let order = null;
let polls = 0;
let timer = null;

async function load({ refresh = false } = {}) {
  try {
    order = await api(`/orders/${encodeURIComponent(orderId)}${refresh ? "/refresh" : ""}`, {
      method: refresh ? "POST" : "GET",
      query: { t: token || undefined },
    });
  } catch (err) {
    clearTimeout(timer);
    notFound(err);
    return;
  }
  paint();
  schedule();
}

function schedule() {
  clearTimeout(timer);
  if (!order || order.status !== "pending_payment" || WAITS_FOR_STUDIO.includes(order.payment.provider)) return;
  if (polls > 60) return;
  polls += 1;
  const delay = polls <= 20 ? 3000 : 10000;
  // Mostly read our own record (webhooks usually land within seconds); every fourth time, ask the gateway directly.
  timer = setTimeout(() => load({ refresh: polls % 4 === 0 }), delay);
}

// ------------------------------------------------------------------ copy

function headline(o) {
  switch (o.status) {
    case "pending_payment": return WAITS_FOR_STUDIO.includes(o.payment.provider) ? "Complete your UPI payment" : "Confirming your payment";
    case "paid":
    case "packed": return o.requires_shipping ? "Thank you — your order is confirmed" : "Payment received";
    case "shipped": return "Your order is on its way";
    case "delivered": return o.requires_shipping ? "Delivered" : "You're all set";
    case "expired": return "This payment wasn't completed";
    case "cancelled": return "This order was cancelled";
    case "refunded": return "This order was refunded";
    default: return o.status_label;
  }
}

function subline(o) {
  if (o.is_legacy) return "This order came from the old website. The studio will confirm it once the payment has been matched.";
  if (o.status === "pending_payment") {
    return o.payment.provider === "manual_upi"
      ? "Send the amount below by UPI. We'll confirm your order as soon as the payment reaches us."
      : "We're checking with the payment gateway. This page updates by itself, so there's no need to pay again.";
  }
  if (PAID.includes(o.status)) return `A receipt is on its way to ${o.customer.email}.`;
  if (o.status === "expired") return "The payment window closed before a payment was confirmed. If you did pay, it will appear here once the gateway confirms it.";
  if (o.status === "cancelled") return "Nothing was charged, and the pieces are back on sale. Rebuild the cart below to order again.";
  if (o.status === "refunded") return `A refund of ${rupees(o.total_paise)} was issued on ${dateTime(o.refunded_at)}. Banks usually take 5–7 business days to show it.`;
  return "";
}

// ------------------------------------------------------------------ panels

function upiPanel(o) {
  const c = o.payment.client;
  if (!c || !c.vpa) return "";
  return html`
    <section class="rounded-3xl border border-sand bg-white p-6 grid sm:grid-cols-[auto_1fr] gap-6 items-center">
      <div class="qr-frame w-44 h-44 mx-auto rounded-2xl border border-sand grid place-items-center bg-white p-2" aria-label="UPI QR code">${raw(c.qr_svg)}</div>
      <div class="space-y-3">
        <div><p class="field-label">Amount</p><p class="text-3xl font-bold">${rupees(c.amount_paise)}</p></div>
        <div>
          <p class="field-label">UPI ID</p>
          <div class="flex items-center gap-2"><code class="text-base font-semibold">${c.vpa}</code>
            <button type="button" class="btn btn-outline btn-sm" data-copy="${c.vpa}"><i data-lucide="copy" class="w-4 h-4"></i>Copy</button></div>
        </div>
        <p class="text-sm text-charcoal-light">Add <strong class="text-charcoal">${c.note}</strong> as the payment note so we can match it to your order.</p>
        <a href="${c.upi_uri}" class="btn btn-primary sm:hidden w-full"><i data-lucide="smartphone" class="w-4 h-4"></i>Open in a UPI app</a>
      </div>
    </section>`;
}

function pendingPanel(o) {
  if (o.is_legacy) return "";
  if (o.payment.provider === "manual_upi") return upiPanel(o);
  return html`
    <section class="rounded-3xl border border-sand bg-white p-6 space-y-4">
      <p class="flex items-center gap-3 font-semibold"><span class="spinner text-terracotta" aria-hidden="true"></span>Checking payment status…</p>
      ${polls > 12 ? html`<p class="text-sm text-charcoal-light">Still waiting. UPI payments can take a couple of minutes to confirm.</p>` : ""}
      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn btn-outline" data-refresh><i data-lucide="refresh-cw" class="w-4 h-4"></i>Check again</button>
        ${o.payment.can_pay && o.payment.client ? html`<button type="button" class="btn btn-ghost" data-retry>Payment didn't go through? Try again</button>` : ""}
      </div>
      <p class="text-sm text-charcoal-light border-t border-sand pt-4">Changed your mind?
        <button type="button" class="link" data-cancel-order>Cancel this order</button> and the piece goes straight back on sale.</p>
    </section>`;
}

function closedPanel(o) {
  if (!o.restore_cart) return "";
  return html`<a href="/?cart=${o.restore_cart}" class="btn btn-primary"><i data-lucide="rotate-ccw" class="w-4 h-4"></i>Rebuild this cart</a>`;
}

function progressPanel(o) {
  if (!PAID.includes(o.status)) return "";
  const steps = o.requires_shipping
    ? [["paid", "Order confirmed", o.paid_at], ["packed", "Packed", o.packed_at], ["shipped", "Shipped", o.shipped_at], ["delivered", "Delivered", o.delivered_at]]
    : [["paid", "Payment confirmed", o.paid_at], ["delivered", "Access granted", o.delivered_at]];
  const reached = steps.map(([, , at]) => Boolean(at));
  const lastReached = reached.lastIndexOf(true);
  return html`
    <section class="rounded-3xl border border-sand bg-white p-6">
      <h2 class="subhead mb-5">Progress</h2>
      <ol class="grid gap-5 ${o.requires_shipping ? "sm:grid-cols-4" : "sm:grid-cols-2"}">
        ${steps.map(([key, label, at], i) => html`
          <li class="flex sm:flex-col gap-3 sm:gap-2">
            <span class="w-9 h-9 rounded-full grid place-items-center shrink-0 ${at ? "bg-terracotta text-white" : i === lastReached + 1 ? "border-2 border-terracotta text-terracotta" : "border border-sand text-charcoal-light"}">
              ${at ? html`<i data-lucide="check" class="w-4 h-4"></i>` : html`<span class="text-sm font-semibold">${i + 1}</span>`}
            </span>
            <span><span class="block text-sm font-semibold ${at ? "" : "text-charcoal-light"}">${label}</span>
              <span class="block text-xs text-charcoal-light">${at ? dateTime(at) : ""}</span></span>
          </li>`)}
      </ol>
      ${o.awb ? html`<p class="mt-5 text-sm flex flex-wrap items-center gap-2"><i data-lucide="truck" class="w-4 h-4 text-terracotta"></i>${o.courier || "Courier"} tracking number <code class="font-semibold">${o.awb}</code> — track it on the courier's website.</p>` : ""}
      ${o.requires_shipping && o.delivery && o.status !== "delivered" ? html`<p class="mt-3 estimate w-fit"><i data-lucide="calendar" class="w-4 h-4"></i>Estimated delivery ${dayLabel(o.delivery.min_date)} – ${dayLabel(o.delivery.max_date)}</p>` : ""}
    </section>`;
}

function accessPanel(o) {
  const user = getUser();
  const signedInAsBuyer = user && user.email.toLowerCase() === o.customer.email.toLowerCase();
  return html`
    <section class="rounded-3xl border border-terracotta/30 bg-terracotta-light/50 p-6 space-y-4">
      <h2 class="subhead">Your Academy access</h2>
      ${o.access.map((a) => (a.type === "course"
        ? html`<div class="flex flex-wrap items-center justify-between gap-3">
            <p class="font-semibold">${a.title}</p>
            ${a.active ? (signedInAsBuyer
              ? html`<a class="btn btn-primary btn-sm" href="${a.url}"><i data-lucide="play" class="w-4 h-4"></i>Start learning</a>`
              : html`<button type="button" class="btn btn-primary btn-sm" data-signin data-next="${a.url}">Sign in to watch</button>`)
              : html`<span class="chip chip-muted">Access removed</span>`}
          </div>`
        : html`<div><p class="font-semibold">${a.title}</p><p class="text-sm text-charcoal-light">${sessionLabel(a.starts_at)} · ${a.duration_min} min · your join link appears in My Courses 48 hours before</p></div>`))}
      ${signedInAsBuyer ? "" : html`<p class="hint">Sign in with ${o.customer.email}. If you haven't set a password, choose “Email me a code”.</p>`}
    </section>`;
}

const itemsPanel = (o) => html`
  <section class="rounded-3xl border border-sand bg-white p-6">
    <h2 class="subhead mb-4">Items</h2>
    <ul class="divide-y divide-sand">${o.items.map((i) => html`
      <li class="py-3 flex gap-4 items-center">
        <img src="${i.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-16 h-16 rounded-xl object-cover bg-sand shrink-0">
        <span class="flex-1 min-w-0">
          <span class="block font-semibold leading-snug">${i.title}</span>
          <span class="block text-sm text-charcoal-light">${[KIND_LABEL[i.kind], i.variant_label].filter(Boolean).join(" · ")}</span>
          <span class="block text-sm text-charcoal-light tabular-nums">${i.qty} × ${rupees(i.unit_price_paise)}</span>
        </span>
        <span class="font-semibold tabular-nums">${rupees(i.line_total_paise)}</span>
      </li>`)}
    </ul>
  </section>`;

function summaryCard(o) {
  const receipt = PAID.includes(o.status) || o.status === "refunded";
  return html`
    <section class="rounded-3xl border border-sand bg-white p-6">
      <h2 class="subhead">Summary</h2>
      <dl class="totals !mt-4 !pt-0 !border-0">
        <div><dt>Placed</dt><dd class="!font-normal">${dateTime(o.created_at)}</dd></div>
        <div><dt>Subtotal</dt><dd>${rupees(o.subtotal_paise)}</dd></div>
        ${o.discount_paise ? html`<div class="text-emerald-800"><dt>Discount${o.coupon_code ? ` (${o.coupon_code})` : ""}</dt><dd>−${rupees(o.discount_paise)}</dd></div>` : ""}
        ${o.requires_shipping ? html`<div><dt>Shipping</dt><dd>${o.shipping_paise ? rupees(o.shipping_paise) : "Free"}</dd></div>` : ""}
        <div class="total"><dt>Total</dt><dd>${rupees(o.total_paise)}</dd></div>
      </dl>
      ${receipt ? html`<a class="btn btn-outline w-full mt-5" href="/order/${encodeURIComponent(o.id)}/receipt${token ? `?t=${encodeURIComponent(token)}` : ""}" target="_blank" rel="noopener"><i data-lucide="file-text" class="w-4 h-4"></i>View receipt</a>` : ""}
    </section>`;
}

function addressCard(o) {
  const a = o.shipping_address;
  return html`
    <section class="rounded-3xl border border-sand bg-white p-6 text-sm">
      <h2 class="subhead mb-3">Delivering to</h2>
      <p class="font-semibold">${a.full_name}</p>
      <p class="text-charcoal-light">${a.line1}</p>
      ${a.line2 ? html`<p class="text-charcoal-light">${a.line2}</p>` : ""}
      <p class="text-charcoal-light">${a.city}, ${a.state} ${a.pincode}</p>
      <p class="text-charcoal-light">Phone ${a.phone}</p>
    </section>`;
}

const helpCard = (o) => {
  const chat = whatsappLink(`Hi Rangdhara, I have a question about order #${o.id}.`);
  return html`
  <section class="rounded-3xl border border-sand bg-cream p-6 text-sm space-y-3">
    <h2 class="subhead">Need help?</h2>
    <p class="text-charcoal-light">${chat ? "Message the studio with your order number and we'll get back to you."
      : "Reply to your order email with your order number and we'll get back to you."}</p>
    ${chat ? html`<a class="btn btn-outline w-full" target="_blank" rel="noopener" href="${chat}"><i data-lucide="message-circle" class="w-4 h-4"></i>WhatsApp the studio</a>` : ""}
  </section>`;
};

function paint() {
  const o = order;
  document.title = `Order #${o.id} · Rangdhara`;
  render(root, html`
    <div class="grid lg:grid-cols-[minmax(0,1fr)_22rem] gap-8 items-start">
      <div class="space-y-6">
        <header>
          <p class="eyebrow">Order #${o.id}</p>
          <h1 class="font-serif text-3xl sm:text-4xl font-bold mt-2 leading-tight">${headline(o)}</h1>
          <p class="text-charcoal-light mt-2 max-w-2xl leading-relaxed">${subline(o)}</p>
          <div class="mt-4">${statusChip(o.status, o.status_label)}</div>
        </header>
        ${o.status === "pending_payment" ? pendingPanel(o) : ""}
        ${["expired", "cancelled"].includes(o.status) ? closedPanel(o) : ""}
        ${progressPanel(o)}
        ${o.access.length ? accessPanel(o) : ""}
        ${itemsPanel(o)}
      </div>
      <aside class="space-y-4 lg:sticky lg:top-6">
        ${summaryCard(o)}
        ${o.shipping_address ? addressCard(o) : ""}
        ${helpCard(o)}
      </aside>
    </div>`);
}

function notFound(err) {
  const user = getUser();
  render(root, html`
    <div class="max-w-lg mx-auto text-center py-12">
      <h1 class="font-serif text-3xl font-bold">We couldn't open this order</h1>
      <p class="text-charcoal-light mt-3">${err.status === 404 ? "Open it from the link in your confirmation email, or sign in with the email you used at checkout." : err.message}</p>
      <div class="flex flex-wrap justify-center gap-2 mt-6">
        ${user ? "" : html`<button type="button" class="btn btn-primary" data-signin>Sign in</button>`}
        <a href="/" class="btn btn-outline">Back to the shop</a>
      </div>
    </div>`);
}

// ------------------------------------------------------------------ actions

on(root, "click", "[data-refresh]", async (e, el) => {
  busy(el, true, "Checking…");
  await load({ refresh: true });
});

on(root, "click", "[data-cancel-order]", async (e, el) => {
  if (!confirm("Cancel this order? Nothing has been charged, and the piece goes back on sale straight away.")) return;
  busy(el, true, "Cancelling…");
  try {
    order = await api(`/orders/${encodeURIComponent(orderId)}/cancel`, { method: "POST", query: { t: token || undefined } });
    clearTimeout(timer);
    paint();
    toast("Order cancelled.", "success");
  } catch (err) {
    busy(el, false);
    toast(err.message, "error");
  }
});

on(root, "click", "[data-retry]", async (e, el) => {
  busy(el, true, "Opening payment…");
  try {
    await launchPayment({ provider: order.payment.provider, client: order.payment.client, orderId: order.id, token });
  } catch (err) {
    busy(el, false);
    toast(err.message, "error");
  }
});

on(root, "click", "[data-copy]", async (e, el) => {
  try {
    await navigator.clipboard.writeText(el.dataset.copy);
    toast("UPI ID copied.", "success");
  } catch {
    toast(`UPI ID: ${el.dataset.copy}`);
  }
});

on(root, "click", "[data-signin]", (e, el) => {
  const next = el.dataset.next;
  openAuth({
    reason: order ? `Sign in with ${order.customer.email} to open your purchase.` : "",
    identifier: order ? order.customer.email : "",
    onDone: () => { if (next) location.href = next; else load(); },
  });
});

(async () => {
  try { await boot(); } catch { /* the order page still works without session details */ }
  load();
})();
