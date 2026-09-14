// Storefront catalogue: grids, filters, and the product dialog.
import { api } from "../lib/api.js";
import { $, closeLayer, html, on, openLayer, render, toast } from "../lib/dom.js";
import { durationText, rupees, sessionLabel } from "../lib/format.js";
import { whatsappLink } from "../lib/session.js";
import { addToCart } from "./cart.js";

const PIN_KEY = "rg_pincode";
const state = { products: [], categories: [], category: "all", query: "", sort: "featured" };
const view = { product: null, variantId: null, media: 0, preview: null, pinNote: "" };
let dialog;

export async function loadCatalogue() {
  try {
    const data = await api("/catalogue");
    state.products = data.products;
    state.categories = data.categories;
  } catch (err) {
    ["#shop-grid", "#kits-grid"].forEach((sel) => render($(sel), html`<p class="col-span-full form-error">${err.message}</p>`));
    return;
  }
  paintAll();
  const slug = new URLSearchParams(location.search).get("item");
  if (slug) openProduct(slug);
}

export function setQuery(query) {
  state.query = query.trim().toLowerCase();
  paintShop();
  paintKits();
}

const byKind = (kind) => state.products.filter((p) => p.kind === kind);

function matches(p) {
  if (!state.query) return true;
  return [p.title, p.description, p.category_name, p.material].join(" ").toLowerCase().includes(state.query);
}

function sorted(list) {
  const copy = [...list];
  if (state.sort === "price-low") copy.sort((a, b) => a.price_paise - b.price_paise);
  if (state.sort === "price-high") copy.sort((a, b) => b.price_paise - a.price_paise);
  if (state.sort === "popular") copy.sort((a, b) => b.reviews_count - a.reviews_count);
  return copy;
}

function paintAll() {
  paintTabs();
  paintShop();
  paintKits();
  paintAcademy();
}

// ------------------------------------------------------------------ pieces

const compareAt = (p, price) => (p.compare_at_paise && p.compare_at_paise > price
  ? html`<span class="text-xs text-charcoal-light line-through tabular-nums">${rupees(p.compare_at_paise)}</span>` : "");

function stockChip(p) {
  if (!p.in_stock) return html`<span class="chip chip-bad">Sold out</span>`;
  const tracked = p.variants.map((v) => v.available).filter((a) => a !== null);
  const left = tracked.reduce((a, b) => a + b, 0);
  return tracked.length && left <= 3 ? html`<span class="chip chip-warn">Only ${left} left</span>` : "";
}

function priceLine(p) {
  const several = new Set(p.variants.map((v) => v.price_paise)).size > 1;
  return html`<span class="flex items-baseline gap-1.5"><span class="text-lg font-bold text-charcoal tabular-nums">${several ? "From " : ""}${rupees(p.price_paise)}</span>${compareAt(p, p.price_paise)}</span>`;
}

function rating(p) {
  if (!p.rating) return "";
  return html`<span class="flex items-center gap-1 text-xs font-semibold text-amber-700" aria-label="Rated ${p.rating} out of 5 from ${p.reviews_count} reviews">
    <i data-lucide="star" class="w-3.5 h-3.5 fill-current"></i>${p.rating.toFixed(1)}<span class="font-normal text-charcoal-light">(${p.reviews_count})</span></span>`;
}

function productCard(p) {
  const single = p.variants.length === 1;
  const action = !p.in_stock
    ? html`<button type="button" class="btn btn-outline btn-sm" disabled>Sold out</button>`
    : single
      ? html`<button type="button" class="btn btn-primary btn-sm" data-add="${p.variants[0].id}"><i data-lucide="shopping-bag" class="w-4 h-4"></i>Add</button>`
      : html`<button type="button" class="btn btn-outline btn-sm" data-open-product="${p.slug}">Choose size</button>`;
  return html`
    <article class="art-card rounded-2xl overflow-hidden flex flex-col">
      <button type="button" class="card-img-container relative aspect-square bg-sand block w-full" data-open-product="${p.slug}" aria-label="View ${p.title}">
        <img src="${p.image}" alt="${p.title}" loading="lazy" class="w-full h-full object-cover">
        ${p.badge ? html`<span class="absolute top-3 left-3 chip chip-surface">${p.badge}</span>` : ""}
      </button>
      <div class="p-5 flex-1 flex flex-col gap-2">
        <div class="flex items-center justify-between gap-2"><span class="eyebrow">${p.category_name || p.kind_label}</span>${rating(p)}</div>
        <h3 class="font-serif text-lg font-bold text-charcoal leading-snug">
          <button type="button" class="text-left hover:text-terracotta transition-colors" data-open-product="${p.slug}">${p.title}</button>
        </h3>
        ${single && p.variants[0].label ? html`<p class="text-xs text-charcoal-light flex items-center gap-1.5"><i data-lucide="ruler" class="w-3.5 h-3.5"></i>${p.variants[0].label}</p>` : ""}
        <p class="text-sm text-charcoal-light leading-relaxed line-clamp-2">${p.kind === "kit" && p.tools_info ? p.tools_info : p.description}</p>
        <div class="mt-auto pt-3 border-t border-sand flex items-end justify-between gap-3">
          <div class="flex flex-col gap-1.5">${priceLine(p)}${stockChip(p)}</div>
          ${action}
        </div>
      </div>
    </article>`;
}

function courseCard(p) {
  const c = p.course || {};
  const facts = [
    c.lesson_count ? `${c.lesson_count} lesson${c.lesson_count === 1 ? "" : "s"}` : "",
    durationText((c.total_minutes || 0) * 60),
    c.certificate_enabled ? "Certificate" : "",
  ].filter(Boolean).join(" · ");
  return html`
    <article class="art-card rounded-2xl overflow-hidden flex flex-col">
      <button type="button" class="card-img-container relative aspect-[4/3] bg-sand block w-full" data-open-product="${p.slug}" aria-label="View ${p.title}">
        <img src="${p.image}" alt="" loading="lazy" class="w-full h-full object-cover">
        <span class="absolute top-3 left-3 chip chip-surface"><i data-lucide="play-circle" class="w-3.5 h-3.5"></i>Masterclass</span>
      </button>
      <div class="p-5 flex-1 flex flex-col gap-2">
        <span class="eyebrow">${c.level || "All levels"}</span>
        <h3 class="font-serif text-lg font-bold leading-snug"><button type="button" class="text-left hover:text-terracotta" data-open-product="${p.slug}">${p.title}</button></h3>
        ${facts ? html`<p class="text-xs text-charcoal-light">${facts}</p>` : ""}
        <p class="text-sm text-charcoal-light leading-relaxed line-clamp-2">${p.description}</p>
        <div class="mt-auto pt-3 border-t border-sand flex items-center justify-between gap-3">
          ${priceLine(p)}
          <button type="button" class="btn btn-primary btn-sm" data-open-product="${p.slug}">View course</button>
        </div>
      </div>
    </article>`;
}

function workshopCard(p) {
  const next = p.variants[0];
  const seats = p.variants.reduce((sum, v) => sum + (v.available || 0), 0);
  return html`
    <article class="art-card rounded-2xl overflow-hidden flex flex-col sm:flex-row">
      <button type="button" class="card-img-container sm:w-2/5 aspect-[4/3] sm:aspect-auto bg-sand block" data-open-product="${p.slug}" aria-label="View ${p.title}">
        <img src="${p.image}" alt="" loading="lazy" class="w-full h-full object-cover">
      </button>
      <div class="p-5 flex-1 flex flex-col gap-2">
        <span class="eyebrow">Live online workshop</span>
        <h3 class="font-serif text-lg font-bold leading-snug">${p.title}</h3>
        ${next ? html`<p class="text-sm text-charcoal flex items-center gap-1.5"><i data-lucide="calendar" class="w-4 h-4 text-terracotta"></i>Next: ${sessionLabel(next.session.starts_at)}</p>` : ""}
        <p class="text-xs text-charcoal-light">${p.variants.length} session${p.variants.length === 1 ? "" : "s"} scheduled · ${seats} seat${seats === 1 ? "" : "s"} left</p>
        <div class="mt-auto pt-3 border-t border-sand flex items-center justify-between gap-3">
          ${priceLine(p)}
          <button type="button" class="btn btn-primary btn-sm" data-open-product="${p.slug}" ${p.in_stock ? "" : "disabled"}>${p.in_stock ? "Book a seat" : "Fully booked"}</button>
        </div>
      </div>
    </article>`;
}

// ------------------------------------------------------------------ sections

function paintTabs() {
  const target = $("#category-tabs");
  if (!target) return;
  const physical = byKind("physical");
  const tabs = [{ id: "all", name: "All pieces" }, ...state.categories.filter((c) => physical.some((p) => p.category === c.id))];
  render(target, tabs.map((t) => html`<button type="button" class="tab-btn px-4 py-2 rounded-full text-sm font-medium whitespace-nowrap ${state.category === t.id ? "active" : ""}" data-category="${t.id}" aria-pressed="${state.category === t.id}">${t.name}</button>`));
}

function paintShop() {
  const target = $("#shop-grid");
  if (!target) return;
  const list = sorted(byKind("physical").filter((p) => (state.category === "all" || p.category === state.category) && matches(p)));
  const count = $("#shop-count");
  if (count) count.textContent = `${list.length} piece${list.length === 1 ? "" : "s"}`;
  render(target, list.length ? list.map(productCard) : html`
    <div class="col-span-full text-center py-14">
      <p class="font-serif text-xl font-bold">Nothing matches that search</p>
      <p class="text-sm text-charcoal-light mt-1">Try another word, or browse every piece.</p>
      <button type="button" class="btn btn-outline mt-5" data-reset-filters>Show all pieces</button>
    </div>`);
}

function paintKits() {
  const target = $("#kits-grid");
  if (!target) return;
  const list = byKind("kit").filter(matches);
  render(target, list.length ? list.map(productCard) : html`<p class="col-span-full text-sm text-charcoal-light">No kits match that search.</p>`);
}

function paintAcademy() {
  const courses = byKind("course");
  const workshops = byKind("workshop");
  const empty = $("#academy-empty");
  if (empty) empty.hidden = Boolean(courses.length || workshops.length);
  const coursesBlock = $("#courses-block");
  const workshopsBlock = $("#workshops-block");
  if (coursesBlock) coursesBlock.hidden = !courses.length;
  if (workshopsBlock) workshopsBlock.hidden = !workshops.length;
  render($("#courses-grid"), courses.map(courseCard));
  render($("#workshops-grid"), workshops.map(workshopCard));
}

export function wireCatalogue() {
  on(document, "click", "[data-category]", (e, el) => {
    state.category = el.dataset.category;
    paintTabs();
    paintShop();
  });
  on(document, "click", "[data-reset-filters]", () => {
    state.category = "all";
    state.query = "";
    const search = $("#nav-search");
    if (search) search.value = "";
    paintTabs();
    paintShop();
    paintKits();
  });
  const sort = $("#sort-select");
  if (sort) sort.addEventListener("change", () => { state.sort = sort.value; paintShop(); });
}

// ------------------------------------------------------------------ product dialog

function ensureDialog() {
  if (dialog) return;
  dialog = document.createElement("div");
  dialog.className = "layer";
  dialog.hidden = true;
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "product-title");
  dialog.innerHTML = '<div class="layer-backdrop" data-close></div><div class="layer-panel" style="max-width:62rem"></div>';
  document.body.appendChild(dialog);
  on(dialog, "click", "[data-close]", () => closeLayer(dialog));
  on(dialog, "click", "[data-media]", (e, el) => { view.media = Number(el.dataset.media); view.preview = null; paintDialog(); });
  on(dialog, "change", 'input[name="variant"]', (e, el) => { view.variantId = Number(el.value); paintDialog(); });
  on(dialog, "click", "[data-add-selected]", () => {
    closeLayer(dialog);
    addToCart(view.variantId);
  });
  on(dialog, "click", "[data-preview]", playPreview);
  dialog.addEventListener("submit", checkPin);
}

export function openProduct(slug) {
  const product = state.products.find((p) => p.slug === slug || p.id === slug);
  if (!product) {
    toast("That item isn't available any more.", "error");
    return;
  }
  const firstAvailable = product.variants.find((v) => v.available === null || v.available > 0) || product.variants[0];
  Object.assign(view, { product, variantId: firstAvailable ? firstAvailable.id : null, media: 0, preview: null, pinNote: "" });
  ensureDialog();
  paintDialog();
  const url = new URL(location.href);
  url.searchParams.set("item", product.slug);
  history.replaceState(null, "", url);
  openLayer(dialog, {
    onClose: () => {
      const clean = new URL(location.href);
      clean.searchParams.delete("item");
      history.replaceState(null, "", clean);
      dialog.querySelectorAll("video").forEach((v) => v.pause());
    },
  });
}

function availability(p, v) {
  if (p.kind === "course") return html`<p class="note-line"><i data-lucide="zap" class="w-4 h-4"></i>Instant access after payment · watch on any device</p>`;
  if (p.kind === "workshop") return html`<p class="note-line"><i data-lucide="video" class="w-4 h-4"></i>${v.session.duration_min} minute live online class · your join link appears in My Courses</p>`;
  if (v.available === null) return html`<p class="note-line"><i data-lucide="check" class="w-4 h-4"></i>In stock</p>`;
  if (v.available <= 0) return html`<p class="note-line text-red-700"><i data-lucide="x-circle" class="w-4 h-4"></i>Sold out</p>`;
  if (v.available <= 3) return html`<p class="note-line text-amber-800"><i data-lucide="hourglass" class="w-4 h-4"></i>Only ${v.available} left — made by hand in small batches</p>`;
  return html`<p class="note-line"><i data-lucide="check" class="w-4 h-4"></i>In stock</p>`;
}

function variantPicker(p, selected) {
  if (p.kind !== "workshop" && p.variants.length <= 1) {
    return selected && selected.label ? html`<p class="text-sm"><span class="text-charcoal-light">Size:</span> <strong>${selected.label}</strong></p>` : "";
  }
  return html`
    <fieldset>
      <legend class="subhead">${p.kind === "workshop" ? "Choose a session" : "Choose a size"}</legend>
      <div class="flex flex-wrap gap-2 mt-2">
        ${p.variants.map((v) => {
          const out = v.available !== null && v.available <= 0;
          return html`<label class="pill ${v.id === selected.id ? "is-active" : ""} ${out ? "is-disabled" : ""}">
            <input type="radio" name="variant" value="${v.id}" class="sr-only" ${v.id === selected.id ? "checked" : ""} ${out ? "disabled" : ""}>
            <span>${p.kind === "workshop" ? sessionLabel(v.session.starts_at) : v.label || "Standard"}</span>
            ${p.kind === "workshop" ? html`<span class="text-xs opacity-80">${out ? "Full" : `${v.available} left`}</span>`
              : new Set(p.variants.map((x) => x.price_paise)).size > 1 ? html`<span class="text-xs opacity-80 tabular-nums">${rupees(v.price_paise)}</span>` : ""}
          </label>`;
        })}
      </div>
    </fieldset>`;
}

function courseDetails(p) {
  const c = p.course;
  if (!c) return "";
  const facts = [
    ["signal", c.level],
    ["list-video", c.lesson_count ? `${c.lesson_count} lesson${c.lesson_count === 1 ? "" : "s"}` : ""],
    ["clock", durationText(c.total_minutes * 60)],
    ["award", c.certificate_enabled ? "Certificate on completion" : ""],
    ["infinity", c.access_days ? `${c.access_days} days of access` : "Lifetime access"],
  ].filter(([, text]) => text);
  return html`
    <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-sm text-charcoal">${facts.map(([icon, text]) => html`<span class="flex items-center gap-1.5"><i data-lucide="${icon}" class="w-4 h-4 text-terracotta"></i>${text}</span>`)}</div>
    ${c.outcomes.length ? html`<div><h3 class="subhead">What you'll learn</h3><ul class="tick-list mt-2">${c.outcomes.map((o) => html`<li>${o}</li>`)}</ul></div>` : ""}
    ${c.outline.length ? html`<div><h3 class="subhead">Course outline</h3><div class="mt-2 divide-y divide-sand border border-sand rounded-xl">
      ${c.outline.map((m, i) => html`<details class="group" ${i === 0 ? "open" : ""}>
        <summary class="flex items-center justify-between gap-3 px-4 py-3 cursor-pointer text-sm font-semibold">${m.title}<span class="text-xs font-normal text-charcoal-light">${m.lessons.length} lesson${m.lessons.length === 1 ? "" : "s"}</span></summary>
        <ul class="px-4 pb-3 space-y-1.5">${m.lessons.map((l) => html`<li class="flex items-center justify-between gap-3 text-sm">
          <span class="flex items-center gap-2 text-charcoal-light"><i data-lucide="${l.is_preview ? "play-circle" : "lock"}" class="w-3.5 h-3.5"></i>${l.title}</span>
          ${l.is_preview ? html`<button type="button" class="link text-xs" data-preview="${l.id}">Watch preview</button>` : html`<span class="text-xs text-charcoal-light tabular-nums">${durationText(l.duration_s)}</span>`}
        </li>`)}</ul>
      </details>`)}
    </div></div>` : ""}`;
}

function pinChecker() {
  let saved = "";
  try { saved = localStorage.getItem(PIN_KEY) || ""; } catch { /* storage unavailable */ }
  return html`
    <form data-pin-check class="rounded-xl border border-sand p-3.5 bg-cream">
      <label class="field-label" for="pin-check">When will it arrive?</label>
      <div class="flex gap-2">
        <input id="pin-check" name="pincode" class="input" inputmode="numeric" maxlength="6" placeholder="Your PIN code" value="${saved}" autocomplete="postal-code">
        <button type="submit" class="btn btn-outline shrink-0">Check</button>
      </div>
      ${view.pinNote ? html`<p class="text-sm mt-2 flex items-center gap-1.5" role="status"><i data-lucide="truck" class="w-4 h-4 text-terracotta"></i>${view.pinNote}</p>` : ""}
    </form>`;
}

function paintDialog() {
  const p = view.product;
  const selected = p.variants.find((v) => v.id === view.variantId) || p.variants[0];
  const media = p.media.length ? p.media : [{ url: p.image, kind: "image", alt: p.title }];
  const active = view.preview ? { kind: "video", url: view.preview } : media[Math.min(view.media, media.length - 1)];
  const unavailable = !selected || (selected.available !== null && selected.available <= 0);
  const cta = unavailable ? (p.kind === "workshop" ? "Session full" : "Sold out")
    : p.kind === "course" ? `Enrol · ${rupees(selected.price_paise)}` : p.kind === "workshop" ? "Book this session" : "Add to cart";

  render(dialog.querySelector(".layer-panel"), html`
    <button type="button" class="icon-btn absolute top-3 right-3 z-10 bg-white/90 shadow-sm" data-close aria-label="Close"><i data-lucide="x" class="w-5 h-5"></i></button>
    <div class="grid md:grid-cols-2">
      <div class="bg-sand">
        <div class="aspect-square overflow-hidden">
          ${active.kind === "video"
            ? html`<video src="${active.url}" controls playsinline ${view.preview ? "autoplay" : ""} class="w-full h-full object-cover bg-black"></video>`
            : html`<img src="${active.url}" alt="${active.alt || p.title}" class="w-full h-full object-cover">`}
        </div>
        ${media.length > 1 ? html`<div class="flex gap-2 p-3 overflow-x-auto">${media.map((m, i) => html`
          <button type="button" class="thumb ${i === view.media && !view.preview ? "is-active" : ""}" data-media="${i}" aria-label="Show photo ${i + 1} of ${media.length}">
            ${m.kind === "video" ? html`<i data-lucide="play" class="w-5 h-5"></i>` : html`<img src="${m.url}" alt="" class="w-full h-full object-cover">`}
          </button>`)}</div>` : ""}
      </div>
      <div class="p-6 md:p-8 flex flex-col gap-4">
        <div>
          <span class="eyebrow">${p.category_name || p.kind_label}</span>
          <h2 id="product-title" class="font-serif text-2xl md:text-3xl font-bold leading-tight mt-1 pr-8">${p.title}</h2>
          <div class="mt-2">${rating(p)}</div>
        </div>
        <div class="flex items-baseline gap-2">
          <span class="text-2xl font-bold tabular-nums">${selected ? rupees(selected.price_paise) : ""}</span>${selected ? compareAt(p, selected.price_paise) : ""}
        </div>
        ${selected ? variantPicker(p, selected) : ""}
        ${selected ? availability(p, selected) : ""}
        <p class="text-sm leading-relaxed text-charcoal-light">${p.description}</p>
        ${p.kind === "course" ? courseDetails(p) : ""}
        ${p.material ? html`<p class="text-sm"><span class="text-charcoal-light">Materials:</span> ${p.material}</p>` : ""}
        ${p.details.length ? html`<ul class="tick-list">${p.details.map((d) => html`<li>${d}</li>`)}</ul>` : ""}
        ${p.includes.length ? html`<div><h3 class="subhead">In the box</h3><ul class="tick-list mt-2">${p.includes.map((d) => html`<li>${d}</li>`)}</ul>
          ${p.tools_info ? html`<p class="hint mt-2">${p.tools_info}</p>` : ""}</div>` : ""}
        ${["physical", "kit"].includes(p.kind) ? pinChecker() : ""}
        <div class="mt-auto pt-2 flex flex-col gap-2">
          <button type="button" class="btn btn-primary btn-lg w-full" data-add-selected ${unavailable ? "disabled" : ""}>${cta}</button>
          <a class="btn btn-ghost w-full" target="_blank" rel="noopener" href="${whatsappLink(`Hi Rangdhara, I have a question about “${p.title}”.`)}">
            <i data-lucide="message-circle" class="w-4 h-4"></i>Ask a question on WhatsApp
          </a>
        </div>
      </div>
    </div>`);
}

async function checkPin(e) {
  const form = e.target.closest("form[data-pin-check]");
  if (!form) return;
  e.preventDefault();
  const pin = form.pincode.value.replace(/\D/g, "");
  if (!/^[1-9]\d{5}$/.test(pin)) {
    view.pinNote = "Enter a 6-digit PIN code.";
    paintDialog();
    return;
  }
  try {
    const info = await api(`/pincode/${pin}`);
    try { localStorage.setItem(PIN_KEY, pin); } catch { /* storage unavailable */ }
    view.pinNote = `${info.delivery.label} to ${info.city || info.state}`;
  } catch (err) {
    view.pinNote = err.message;
  }
  paintDialog();
}

async function playPreview(e, el) {
  try {
    const playback = await api(`/lessons/${el.dataset.preview}/playback`);
    view.preview = playback.url;
    paintDialog();
  } catch (err) {
    toast(err.message, "error");
  }
}
