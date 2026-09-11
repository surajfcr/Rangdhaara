// The cart holds only variant ids and quantities. Every price shown comes back from the server.
import { api } from "../lib/api.js";
import { closeLayer, html, on, openLayer, render, toast } from "../lib/dom.js";
import { rupees, sessionLabel } from "../lib/format.js";
import { KIND_LABEL } from "../lib/status.js";

const KEY = "rg_cart";
const DIGITAL = new Set(["course", "workshop"]);
const listeners = new Set();

let items = read();
let pricing = null;
let latest = null;
let drawer;

function read() {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(stored) ? stored.filter((i) => Number.isInteger(i.variant_id) && Number.isInteger(i.qty) && i.qty > 0) : [];
  } catch {
    return [];
  }
}

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* storage unavailable */ }
}

function emit() {
  listeners.forEach((fn) => fn());
}

export const cartItems = () => items.map((i) => ({ ...i }));
export const cartCount = () => items.reduce((sum, i) => sum + i.qty, 0);
export const cartPricing = () => pricing;
export const onCartChange = (fn) => listeners.add(fn);

export async function refreshCart() {
  if (!items.length) {
    pricing = null;
    emit();
    paint();
    return null;
  }
  const request = api("/cart/price", { method: "POST", body: { items } });
  latest = request;
  try {
    const result = await request;
    if (latest !== request) return pricing;
    pricing = result;
    items = result.lines.map((line) => ({ variant_id: line.variant_id, qty: line.qty }));
    save();
  } catch (err) {
    if (latest === request) toast(err.message, "error");
  }
  emit();
  paint();
  return pricing;
}

export async function addToCart(variantId, qty = 1) {
  const existing = items.find((i) => i.variant_id === variantId);
  if (existing) existing.qty += qty;
  else items.push({ variant_id: variantId, qty });
  save();
  emit();
  openCart(false);
  await refreshCart();
  const line = pricing && pricing.lines.find((l) => l.variant_id === variantId);
  if (!line) toast("That item isn't available any more.", "error");
  else if (line.issue) toast(line.issue_message, "error");
}

export function setQty(variantId, qty) {
  if (qty <= 0) items = items.filter((i) => i.variant_id !== variantId);
  else items = items.map((i) => (i.variant_id === variantId ? { ...i, qty } : i));
  save();
  emit();
  paint();
  refreshCart();
}

export function clearCart() {
  items = [];
  pricing = null;
  save();
  emit();
  paint();
}

// "12x1,15x2" from an abandoned-cart email link
export function restoreCart(spec) {
  const parsed = String(spec || "")
    .split(",")
    .map((pair) => pair.split("x").map(Number))
    .filter(([variant, qty]) => Number.isInteger(variant) && variant > 0 && Number.isInteger(qty) && qty > 0)
    .map(([variant, qty]) => ({ variant_id: variant, qty: Math.min(qty, 10) }));
  if (!parsed.length) return false;
  items = parsed;
  save();
  emit();
  return true;
}

// ------------------------------------------------------------------ drawer

function ensureDrawer() {
  if (drawer) return;
  drawer = document.createElement("div");
  drawer.className = "layer drawer";
  drawer.hidden = true;
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-label", "Your cart");
  drawer.innerHTML = '<div class="layer-backdrop" data-close></div><aside class="layer-panel flex flex-col"></aside>';
  document.body.appendChild(drawer);
  on(drawer, "click", "[data-close]", () => closeLayer(drawer));
  on(drawer, "click", "[data-qty]", (e, el) => setQty(Number(el.dataset.variant), Number(el.dataset.qty)));
  on(drawer, "click", "[data-remove]", (e, el) => setQty(Number(el.dataset.remove), 0));
  on(drawer, "click", "[data-checkout]", () => {
    closeLayer(drawer);
    document.dispatchEvent(new CustomEvent("rg:checkout"));
  });
}

export function openCart(refresh = true) {
  ensureDrawer();
  paint();
  openLayer(drawer);
  if (refresh) refreshCart();
}

const lineRow = (line) => html`
  <article class="cart-line ${line.issue ? "has-issue" : ""}">
    <img src="${line.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-16 h-16 rounded-xl object-cover bg-sand shrink-0">
    <div class="flex-1 min-w-0">
      <p class="eyebrow">${KIND_LABEL[line.kind]}</p>
      <h3 class="text-sm font-semibold text-charcoal leading-snug mt-0.5">${line.title}</h3>
      ${line.kind === "workshop" ? html`<p class="text-xs text-charcoal-light">${sessionLabel(line.starts_at)}</p>`
        : line.variant_label ? html`<p class="text-xs text-charcoal-light">${line.variant_label}</p>` : ""}
      <div class="mt-2.5 flex items-center justify-between gap-2">
        ${DIGITAL.has(line.kind)
          ? html`<span class="text-xs text-charcoal-light">1 ${line.kind === "workshop" ? "seat" : "enrolment"}</span>`
          : html`<div class="stepper" role="group" aria-label="Quantity">
              <button type="button" data-qty="${line.qty - 1}" data-variant="${line.variant_id}" aria-label="One fewer">−</button>
              <span class="tabular-nums" aria-live="polite">${line.qty}</span>
              <button type="button" data-qty="${line.qty + 1}" data-variant="${line.variant_id}" aria-label="One more"
                ${(line.available !== null && line.qty >= line.available) || line.qty >= 10 ? "disabled" : ""}>+</button>
            </div>`}
        <span class="text-sm font-semibold tabular-nums">${rupees(line.line_total_paise)}</span>
      </div>
      ${line.issue ? html`<p class="field-error">${line.issue_message}</p>`
        : line.available !== null && line.available <= 3 && !DIGITAL.has(line.kind) ? html`<p class="text-xs text-amber-800 mt-1.5">Only ${line.available} left</p>` : ""}
    </div>
    <button type="button" class="icon-btn self-start -mr-2" data-remove="${line.variant_id}" aria-label="Remove ${line.title}"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
  </article>`;

function paint() {
  if (!drawer) return;
  const panel = drawer.querySelector(".layer-panel");
  const lines = pricing ? pricing.lines : [];
  let body;
  if (!items.length) {
    body = html`<div class="text-center py-16 px-6">
      <div class="mx-auto w-14 h-14 rounded-full bg-terracotta-light text-terracotta grid place-items-center mb-4"><i data-lucide="shopping-bag" class="w-6 h-6"></i></div>
      <p class="font-serif text-lg font-bold">Your cart is empty</p>
      <p class="text-sm text-charcoal-light mt-1">Handmade pieces, DIY kits and masterclasses are waiting.</p>
      <button type="button" class="btn btn-outline mt-5" data-close>Keep browsing</button>
    </div>`;
  } else if (!pricing) {
    body = html`<div class="space-y-3">${[1, 2].map(() => html`<div class="skeleton h-24"></div>`)}</div>`;
  } else {
    body = lines.map(lineRow);
  }
  render(panel, html`
    <header class="flex items-center justify-between px-5 py-4 border-b border-sand">
      <h2 class="font-serif text-xl font-bold">Your cart ${items.length ? html`<span class="text-sm font-sans font-medium text-charcoal-light">(${cartCount()})</span>` : ""}</h2>
      <button type="button" class="icon-btn" data-close aria-label="Close cart"><i data-lucide="x" class="w-5 h-5"></i></button>
    </header>
    <div class="flex-1 overflow-y-auto px-5 py-4 space-y-3">${body}</div>
    ${pricing && lines.length ? html`
      <footer class="border-t border-sand px-5 py-4 space-y-3 bg-cream">
        <div class="flex justify-between text-sm"><span class="text-charcoal-light">Subtotal</span><span class="font-semibold tabular-nums">${rupees(pricing.subtotal_paise)}</span></div>
        <p class="hint">${pricing.requires_shipping
          ? "Your delivery date appears at checkout once you enter a PIN code."
          : "Instant access after payment. Nothing to ship."}</p>
        ${pricing.ok ? "" : html`<p class="form-error">Remove or adjust the items marked above to continue.</p>`}
        <button type="button" class="btn btn-primary w-full" data-checkout ${pricing.ok ? "" : "disabled"}>
          <i data-lucide="lock" class="w-4 h-4"></i>Checkout
        </button>
      </footer>` : ""}`);
}
