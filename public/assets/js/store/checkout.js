// Two-column checkout: contact, PIN-code-first delivery, then a hand-off to the payment gateway.
// Totals always come from the server; the browser only sends ids, quantities and details.
import { api } from "../lib/api.js";
import { openAuth } from "../lib/auth.js";
import { busy, clearFieldErrors, closeLayer, formError, html, on, openLayer, render, showFieldErrors, toast, values } from "../lib/dom.js";
import { rupees, sessionLabel } from "../lib/format.js";
import { launchPayment, orderUrl } from "../lib/payments.js";
import { getStore, getUser } from "../lib/session.js";
import { KIND_LABEL } from "../lib/status.js";
import { cartItems, clearCart, refreshCart } from "./cart.js";

const PIN_KEY = "rg_pincode";
const COUPON_KEY = "rg_coupon";
const PAY_COPY = {
  mock: ["flask-conical", "Test payment", "Development mode — on the next page you choose whether the payment succeeds. No money moves."],
  razorpay: ["shield-check", "Pay with Razorpay", "UPI, cards, netbanking and wallets, in Razorpay's secure window."],
  cashfree: ["shield-check", "Pay with Cashfree", "UPI, cards, netbanking and wallets, on Cashfree's secure page."],
  manual_upi: ["qr-code", "Pay by UPI", "After you place the order we'll show our UPI ID and QR code. Your order is confirmed once the payment reaches us."],
};

const s = { pricing: null, coupon: "", addresses: [], addressId: "new" };
let layer;
let sequence = 0;
let pinTimer;

function store(key, value) {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch { /* storage unavailable */ }
}

function savedPin() {
  try { return localStorage.getItem(PIN_KEY) || ""; } catch { return ""; }
}

function ensureLayer() {
  if (layer) return;
  layer = document.createElement("div");
  layer.className = "layer layer-sheet";
  layer.hidden = true;
  layer.setAttribute("role", "dialog");
  layer.setAttribute("aria-modal", "true");
  layer.setAttribute("aria-labelledby", "checkout-title");
  layer.innerHTML = '<div class="layer-backdrop" data-close></div><div class="layer-panel sheet"></div>';
  document.body.appendChild(layer);

  on(layer, "click", "[data-close]", () => closeLayer(layer));
  on(layer, "click", "[data-signin]", () => {
    closeLayer(layer);
    openAuth({ reason: "Sign in to use a saved address and keep this order in your account.", onDone: () => openCheckout() });
  });
  on(layer, "change", 'input[name="address_id"]', (e, el) => {
    s.addressId = el.value;
    layer.querySelector("[data-new-address]").hidden = s.addressId !== "new";
    refreshEstimate();
    reprice();
  });
  on(layer, "input", 'input[name="address.pincode"]', onPinInput);
  on(layer, "input", 'input[name="address.city"]', (e, el) => { delete el.dataset.auto; });
  on(layer, "input", 'input[name="contact.name"], input[name="contact.phone"]', (e, el) => {
    const target = layer.querySelector(el.name === "contact.name" ? 'input[name="address.full_name"]' : 'input[name="address.phone"]');
    if (target && !target.dataset.touched) target.value = el.value;
  });
  on(layer, "input", 'input[name="address.full_name"], input[name="address.phone"]', (e, el) => { el.dataset.touched = "1"; });
  on(layer, "click", "[data-remove-coupon]", () => {
    s.coupon = "";
    store(COUPON_KEY, "");
    reprice();
  });
  layer.addEventListener("submit", submit);
}

export async function openCheckout() {
  if (!cartItems().length) {
    toast("Your cart is empty.");
    return;
  }
  ensureLayer();
  const user = getUser();
  try { s.coupon = sessionStorage.getItem(COUPON_KEY) || ""; } catch { s.coupon = ""; }
  s.addresses = [];
  if (user) {
    try { s.addresses = (await api("/me/addresses")).addresses; } catch { /* fall back to a blank address form */ }
  }
  const preferred = s.addresses.find((a) => a.is_default) || s.addresses[0];
  s.addressId = preferred ? String(preferred.id) : "new";
  const priced = await reprice({ paint: false });
  if (!priced) return;
  if (!priced.lines.length) {
    toast("Your cart is empty.");
    return;
  }
  paintForm(user);
  paintSummary();
  const name = layer.querySelector('input[name="contact.name"]');
  const phone = layer.querySelector('input[name="contact.phone"]');
  const shipName = layer.querySelector('input[name="address.full_name"]');
  const shipPhone = layer.querySelector('input[name="address.phone"]');
  if (shipName && name) shipName.value = name.value;
  if (shipPhone && phone) shipPhone.value = phone.value;
  openLayer(layer);
  refreshEstimate();
}

function pincode() {
  if (s.addressId !== "new") {
    const saved = s.addresses.find((a) => String(a.id) === s.addressId);
    return saved ? saved.pincode : null;
  }
  const input = layer && layer.querySelector('input[name="address.pincode"]');
  const value = input ? input.value.trim() : savedPin();
  return /^[1-9]\d{5}$/.test(value) ? value : null;
}

async function reprice({ paint = true } = {}) {
  const mine = ++sequence;
  try {
    const result = await api("/cart/price", { method: "POST", body: { items: cartItems(), coupon_code: s.coupon || null, pincode: pincode() } });
    if (mine !== sequence) return s.pricing;
    s.pricing = result;
    if (paint) paintSummary();
    return result;
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
}

// ------------------------------------------------------------------ rendering

const contactSection = (user, digitalOnly) => html`
  <section aria-labelledby="co-contact" class="space-y-4">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <h3 id="co-contact" class="co-heading">Contact</h3>
      ${user ? html`<span class="text-sm text-charcoal-light">Signed in as ${user.email}</span>`
        : html`<button type="button" class="link text-sm" data-signin>Have an account? Sign in</button>`}
    </div>
    <div class="grid sm:grid-cols-2 gap-4">
      <label class="field sm:col-span-2">
        <span class="field-label">Email</span>
        <input class="input" type="email" name="contact.email" autocomplete="email" required value="${user ? user.email : ""}" ${user ? "readonly" : ""}>
        <span class="hint block mt-1">${digitalOnly ? "Your receipt goes here, and you'll sign in with this email to watch." : "Your receipt and shipping updates go here."}</span>
      </label>
      <label class="field"><span class="field-label">Full name</span><input class="input" name="contact.name" autocomplete="name" required value="${user ? user.full_name : ""}"></label>
      <label class="field"><span class="field-label">Mobile number</span><input class="input" type="tel" name="contact.phone" inputmode="numeric" autocomplete="tel-national" placeholder="10 digits" required value="${user ? user.phone : ""}"></label>
    </div>
  </section>`;

function deliverySection(user, states) {
  return html`
    <section aria-labelledby="co-delivery" class="space-y-4">
      <h3 id="co-delivery" class="co-heading">Delivery</h3>
      ${s.addresses.length ? html`<div class="space-y-2">
        ${s.addresses.map((a) => html`<label class="choice">
          <input type="radio" name="address_id" value="${a.id}" ${String(a.id) === s.addressId ? "checked" : ""}>
          <span><strong class="text-charcoal">${a.full_name}</strong> · ${a.phone}
            <span class="block text-charcoal-light">${a.line1}${a.line2 ? `, ${a.line2}` : ""}, ${a.city}, ${a.state} ${a.pincode}</span></span>
        </label>`)}
        <label class="choice"><input type="radio" name="address_id" value="new" ${s.addressId === "new" ? "checked" : ""}><span>Deliver to a different address</span></label>
      </div>` : ""}
      <div data-new-address class="grid sm:grid-cols-6 gap-4" ${s.addressId === "new" ? "" : "hidden"}>
        <label class="field sm:col-span-2"><span class="field-label">PIN code</span>
          <input class="input tabular-nums" name="address.pincode" inputmode="numeric" maxlength="6" autocomplete="postal-code" value="${savedPin()}"></label>
        <label class="field sm:col-span-2"><span class="field-label">City</span><input class="input" name="address.city" autocomplete="address-level2"></label>
        <label class="field sm:col-span-2"><span class="field-label">State</span>
          <select class="input" name="address.state" autocomplete="address-level1"><option value="">Choose</option>${states.map((st) => html`<option>${st}</option>`)}</select></label>
        <label class="field sm:col-span-6"><span class="field-label">House or flat number, building and street</span><input class="input" name="address.line1" autocomplete="address-line1"></label>
        <label class="field sm:col-span-6"><span class="field-label">Area or landmark <span class="font-normal text-charcoal-light">(optional)</span></span><input class="input" name="address.line2" autocomplete="address-line2"></label>
        <label class="field sm:col-span-3"><span class="field-label">Name on the parcel</span><input class="input" name="address.full_name" autocomplete="name"></label>
        <label class="field sm:col-span-3"><span class="field-label">Phone for the courier</span><input class="input" type="tel" name="address.phone" inputmode="numeric" autocomplete="tel-national"></label>
        ${user ? html`<label class="sm:col-span-6 flex items-center gap-2.5 text-sm text-charcoal-light"><input type="checkbox" name="address.save" class="checkbox" checked>Save this address to my account</label>` : ""}
      </div>
      <p data-estimate class="estimate" role="status" hidden></p>
    </section>`;
}

const digitalNote = () => html`
  <section class="note-card">
    <i data-lucide="zap" class="w-5 h-5 text-terracotta shrink-0 mt-0.5"></i>
    <div><p class="font-semibold">Nothing to ship</p>
    <p class="text-sm text-charcoal-light mt-0.5">Your course or workshop opens in My Courses as soon as the payment is confirmed.</p></div>
  </section>`;

function paymentSection(settings) {
  const [icon, title, copy] = PAY_COPY[settings.payment ? settings.payment.provider : "razorpay"] || PAY_COPY.razorpay;
  return html`
    <section aria-labelledby="co-payment" class="space-y-4">
      <h3 id="co-payment" class="co-heading">Payment</h3>
      <div class="choice cursor-default">
        <i data-lucide="${icon}" class="w-5 h-5 text-terracotta shrink-0 mt-0.5"></i>
        <span><strong class="text-charcoal">${title}</strong><span class="block text-charcoal-light">${copy}</span></span>
      </div>
      <label class="flex items-start gap-2.5 text-sm text-charcoal-light"><input type="checkbox" name="marketing_opt_in" class="checkbox mt-0.5">Email me when new pieces and masterclasses launch</label>
    </section>`;
}

function paintForm(user) {
  const settings = getStore() || {};
  const digitalOnly = !s.pricing.requires_shipping;
  render(layer.querySelector(".layer-panel"), html`
    <header class="sticky top-0 z-10 bg-white/95 backdrop-blur border-b border-sand px-5 sm:px-8 py-4 flex items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <img src="/assets/images/profile_avatar.jpg" alt="" class="w-9 h-9 rounded-full object-cover">
        <div><h2 id="checkout-title" class="font-serif text-xl font-bold leading-none">Checkout</h2>
        <p class="hint mt-1 flex items-center gap-1"><i data-lucide="lock" class="w-3.5 h-3.5"></i>Secure checkout</p></div>
      </div>
      <button type="button" class="icon-btn" data-close aria-label="Close checkout"><i data-lucide="x" class="w-5 h-5"></i></button>
    </header>
    <div class="grid lg:grid-cols-[minmax(0,1fr)_25rem]">
      <form data-checkout-form class="p-5 sm:p-8 space-y-8" novalidate>
        ${contactSection(user, digitalOnly)}
        ${digitalOnly ? digitalNote() : deliverySection(user, settings.states || [])}
        ${paymentSection(settings)}
        <div class="form-error" role="alert" hidden></div>
        <button type="submit" class="btn btn-primary btn-lg w-full" data-pay>Pay ${rupees(s.pricing.total_paise)}</button>
      </form>
      <aside data-summary class="bg-cream border-t lg:border-t-0 lg:border-l border-sand p-5 sm:p-8" aria-label="Order summary"></aside>
    </div>`);
}

function paintSummary() {
  const aside = layer && layer.querySelector("[data-summary]");
  if (!aside || !s.pricing) return;
  const p = s.pricing;
  render(aside, html`
    <div class="lg:sticky lg:top-24">
      <h3 class="co-heading">Order summary</h3>
      <ul class="mt-4 space-y-3">${p.lines.map((l) => html`
        <li class="flex gap-3 items-start">
          <span class="relative shrink-0"><img src="${l.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-14 h-14 rounded-xl object-cover bg-sand"><span class="qty-badge">${l.qty}</span></span>
          <span class="flex-1 min-w-0">
            <span class="block text-sm font-semibold leading-snug">${l.title}</span>
            <span class="block text-xs text-charcoal-light">${l.kind === "workshop" ? sessionLabel(l.starts_at) : l.variant_label || KIND_LABEL[l.kind]}</span>
            ${l.issue ? html`<span class="field-error block">${l.issue_message}</span>` : ""}
          </span>
          <span class="text-sm font-semibold tabular-nums">${rupees(l.line_total_paise)}</span>
        </li>`)}
      </ul>
      <form data-coupon-form class="mt-5 flex gap-2" novalidate>
        <label class="sr-only" for="co-coupon">Discount code</label>
        <input id="co-coupon" name="coupon" class="input uppercase" placeholder="Discount code" value="${s.coupon}" autocomplete="off">
        <button type="submit" class="btn btn-outline shrink-0">Apply</button>
      </form>
      ${p.coupon ? (p.coupon.applied
        ? html`<p class="mt-2 flex items-center justify-between gap-2 text-sm"><span class="chip chip-ok"><i data-lucide="tag" class="w-3.5 h-3.5"></i>${p.coupon.code} · ${p.coupon.message}</span><button type="button" class="link text-xs" data-remove-coupon>Remove</button></p>`
        : html`<p class="field-error">${p.coupon.message}</p>`) : ""}
      <dl class="totals">
        <div><dt>Subtotal</dt><dd>${rupees(p.subtotal_paise)}</dd></div>
        ${p.discount_paise ? html`<div class="text-emerald-800"><dt>Discount</dt><dd>−${rupees(p.discount_paise)}</dd></div>` : ""}
        ${p.requires_shipping ? html`<div><dt>Shipping</dt><dd>${p.shipping_paise ? rupees(p.shipping_paise) : "Free"}</dd></div>` : ""}
        <div class="total"><dt>Total</dt><dd>${rupees(p.total_paise)}</dd></div>
      </dl>
      ${p.ok ? "" : html`<p class="form-error mt-4">Something in your cart changed. Close checkout and adjust your cart to continue.</p>`}
    </div>`);
  const pay = layer.querySelector("[data-pay]");
  if (pay && !pay.dataset.idleHtml) {
    pay.textContent = `Pay ${rupees(p.total_paise)}`;
    pay.disabled = !p.ok;
  }
}

// ------------------------------------------------------------------ delivery estimate

function onPinInput(e, input) {
  input.value = input.value.replace(/\D/g, "").slice(0, 6);
  clearTimeout(pinTimer);
  if (input.value.length === 6) pinTimer = setTimeout(() => lookupPin(input.value, true), 150);
  else {
    const note = layer.querySelector("[data-estimate]");
    if (note) note.hidden = true;
  }
}

function showEstimate(content, tone = "estimate") {
  const note = layer.querySelector("[data-estimate]");
  if (!note) return;
  note.className = tone;
  note.hidden = !content;
  if (content) render(note, content);
}

async function lookupPin(pin, fill) {
  try {
    const info = await api(`/pincode/${pin}`);
    try { localStorage.setItem(PIN_KEY, pin); } catch { /* storage unavailable */ }
    if (fill) {
      const city = layer.querySelector('input[name="address.city"]');
      const state = layer.querySelector('select[name="address.state"]');
      if (city && info.city && (!city.value || city.dataset.auto)) {
        city.value = info.city;
        city.dataset.auto = "1";
      }
      if (state && [...state.options].some((o) => o.value === info.state)) state.value = info.state;
    }
    showEstimate(html`<i data-lucide="truck" class="w-4 h-4 shrink-0"></i><span>${info.delivery.label}${info.city || info.state ? ` to ${info.city || info.state}` : ""}</span>`);
    if (fill) reprice();
  } catch (err) {
    showEstimate(html`${err.message}`, "form-error");
  }
}

function refreshEstimate() {
  if (!s.pricing || !s.pricing.requires_shipping) return;
  const pin = pincode();
  if (pin) lookupPin(pin, s.addressId === "new");
  else showEstimate(null);
}

// ------------------------------------------------------------------ submit

async function submit(e) {
  const couponForm = e.target.closest("form[data-coupon-form]");
  if (couponForm) {
    e.preventDefault();
    s.coupon = couponForm.coupon.value.trim().toUpperCase();
    store(COUPON_KEY, s.coupon);
    await reprice();
    return;
  }
  const form = e.target.closest("form[data-checkout-form]");
  if (!form) return;
  e.preventDefault();
  clearFieldErrors(form);
  const v = values(form);
  const body = {
    items: cartItems(),
    coupon_code: s.coupon || null,
    contact: { name: v["contact.name"], email: v["contact.email"], phone: v["contact.phone"] },
    marketing_opt_in: Boolean(v.marketing_opt_in),
    address: null,
  };
  if (s.pricing.requires_shipping) {
    const saved = s.addresses.find((a) => String(a.id) === s.addressId);
    body.address = saved && s.addressId !== "new"
      ? { full_name: saved.full_name, phone: saved.phone, line1: saved.line1, line2: saved.line2, city: saved.city, state: saved.state, pincode: saved.pincode, save: false }
      : { full_name: v["address.full_name"] || v["contact.name"], phone: v["address.phone"] || v["contact.phone"], line1: v["address.line1"],
          line2: v["address.line2"] || "", city: v["address.city"], state: v["address.state"], pincode: v["address.pincode"], save: Boolean(v["address.save"]) };
  }

  const button = form.querySelector("[data-pay]");
  busy(button, true, "Starting secure payment…");
  let result;
  try {
    result = await api("/checkout", { method: "POST", body });
  } catch (err) {
    busy(button, false);
    if (err.data && err.data.pricing) {
      s.pricing = err.data.pricing;
      paintSummary();
      refreshCart();
    }
    showFieldErrors(form, err.fields);
    formError(form, err.message);
    return;
  }
  clearCart();
  store(COUPON_KEY, "");
  try {
    await launchPayment({ provider: result.provider, client: result.client, orderId: result.order_id, token: result.token });
  } catch (err) {
    toast(err.message, "error");
    location.href = orderUrl(result.order_id, result.token);
  }
}
