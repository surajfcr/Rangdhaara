// Catalogue: add, edit and remove products, and keep the number of pieces in stock up to date.
import { api, upload } from "../lib/api.js";
import { busy, html, render, toast } from "../lib/dom.js";
import { dateOnly, rupees } from "../lib/format.js";
import { KIND_LABEL } from "../lib/status.js";
import { back, emptyRow, field, formDialog, pageHead, removeProductDialog } from "./ui.js";

const toPaise = (value) => Math.round(parseFloat(String(value).replace(/[₹,\s]/g, "")) * 100);
const lines = (text) => String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
const liveChip = (p) => (p.archived_at ? html`<span class="chip chip-muted">Archived</span>`
  : p.is_active ? html`<span class="chip chip-ok">Live</span>` : html`<span class="chip chip-muted">Draft</span>`);

export default async function catalogue(el, ctx) {
  const [id] = ctx.params;
  return id ? detail(el, ctx, id) : list(el, ctx);
}

// ------------------------------------------------------------------ add a product

async function addProduct(ctx, categories) {
  const created = await formDialog({
    title: "Add a product",
    description: "Fill in the basics and add a photo. You can add more photos, sizes and details afterwards.",
    submitLabel: "Add product",
    wide: true,
    body: html`
      ${field("Product name", html`<input class="input" name="title" required autofocus placeholder="e.g. Sunflower 3D Relief Canvas">`)}
      <div class="grid sm:grid-cols-2 gap-4">
        ${field("Type", html`<select class="input" name="kind"><option value="physical">Ready-to-buy piece</option><option value="kit">DIY kit</option></select>`)}
        <div data-physical-only>${field("Category", html`<select class="input" name="category"><option value="">None</option>${categories.map((c) => html`<option value="${c.id}">${c.name}</option>`)}</select>`, "Groups the piece in the shop's filter row.")}</div>
      </div>
      <div class="grid grid-cols-3 gap-4">
        ${field("Price (₹)", html`<input class="input" name="price" inputmode="decimal" required placeholder="1499">`)}
        ${field("Pieces in stock", html`<input class="input" type="number" min="0" name="on_hand" value="1" required>`)}
        ${field("Size", html`<input class="input" name="variant_label" placeholder="12 x 16 in">`)}
      </div>
      ${field("Description", html`<textarea class="input" name="description" rows="3" placeholder="What it is, how it's made, what makes it special"></textarea>`)}
      ${field("Photo", html`<input class="input !py-2" type="file" name="photo" accept="image/jpeg,image/png,image/webp">`, "JPG, PNG or WebP, up to 8 MB. The first photo is the cover.")}
      <label class="flex items-start gap-2.5 text-sm"><input type="checkbox" class="checkbox mt-0.5" name="publish" checked>
        <span>Show it in the store straight away<span class="block hint">Needs a price and a photo. Leave unticked to save it as a draft.</span></span></label>`,
    onSubmit: async (v, form) => {
      const price = toPaise(v.price);
      if (!price || price <= 0) throw Object.assign(new Error("Enter a price above ₹0."), { fields: { price: "Enter a price" } });
      let product = await api("/admin/products", { method: "POST", body: {
        kind: v.kind, title: v.title, category: v.kind === "physical" ? v.category : "", description: v.description,
        price_paise: price, on_hand: Number(v.on_hand) || 0, variant_label: v.variant_label } });
      const notes = [];
      const photo = form.photo.files[0];
      if (photo) {
        const data = new FormData();
        data.append("file", photo);
        data.append("alt", v.title);
        try { product = await upload(`/admin/products/${product.id}/media`, data); } catch (err) { notes.push(`The photo didn't upload: ${err.message}`); }
      }
      if (v.publish) {
        try { product = await api(`/admin/products/${product.id}`, { method: "PATCH", body: { is_active: true } }); }
        catch (err) { notes.push(`Saved as a draft: ${(err.data && err.data.problems ? err.data.problems.join(" ") : err.message)}`); }
      }
      return { product, notes };
    },
  });
  if (!created) return;
  toast(created.notes.length ? created.notes.join(" ") : created.product.is_active ? "Product added and live in the store." : "Product saved as a draft.",
    created.notes.length ? "error" : "success");
  ctx.navigate(`#catalogue/${created.product.id}`);
}

// ------------------------------------------------------------------ list

async function list(el, ctx) {
  const owner = ctx.user.role === "admin";
  let filter = "all";
  let data;
  let archived;
  const load = async () => {
    [data, archived] = await Promise.all([
      api("/admin/products", { query: { kind: "physical,kit" } }),
      api("/admin/products", { query: { kind: "physical,kit", archived: "true" } }),
    ]);
  };
  await load();
  if (!ctx.isCurrent()) return;

  const activeRows = (rows) => (rows.length ? rows.map((p) => {
    const variants = p.variants.length ? p.variants : [null];
    return variants.map((v, i) => html`
      <tr ${i === 0 ? html`data-href="#catalogue/${p.id}"` : ""}>
        ${i === 0 ? html`
          <td rowspan="${variants.length}">
            <div class="flex items-center gap-3"><img src="${p.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-12 h-12 rounded-lg object-cover bg-sand shrink-0">
            <span class="min-w-0"><a href="#catalogue/${p.id}" class="block font-semibold hover:text-terracotta-dark truncate max-w-[14rem]">${p.title}</a>
            <span class="block text-xs text-charcoal-light">${KIND_LABEL[p.kind]}${p.category_name ? ` · ${p.category_name}` : ""}</span></span></div>
          </td>
          <td rowspan="${variants.length}"><div class="flex flex-col gap-1">${liveChip(p)}${!p.is_active && p.publish_problems.length ? html`<span class="text-xs text-charcoal-light">${p.publish_problems.length} to fix before publishing</span>` : ""}</div></td>` : ""}
        ${v ? html`
          <td class="text-charcoal-light min-w-[7rem] max-w-[10rem]">${v.label || "Standard"}${v.is_active ? "" : " (hidden)"}</td>
          <td class="num">${v.price_paise ? rupees(v.price_paise) : html`<span class="text-red-700">Not set</span>`}</td>
          <td class="num"><input type="number" min="0" class="inline-input" value="${v.on_hand}" data-stock="${v.id}" aria-label="Pieces in stock for ${p.title} ${v.label}"></td>
          <td class="text-sm whitespace-nowrap ${v.available === 0 ? "text-red-700 font-semibold" : "text-charcoal-light"}">${v.available === 0 ? "Sold out" : `${v.available} available`}${v.on_hand > v.available ? html`<span class="block text-xs">${v.on_hand - v.available} held at checkout</span>` : ""}</td>`
          : html`<td colspan="4" class="text-charcoal-light">No size or price set yet</td>`}
        ${i === 0 && owner ? html`<td rowspan="${variants.length}" class="text-right whitespace-nowrap">
          <button type="button" class="icon-btn text-red-700" data-remove="${p.id}" aria-label="Remove ${p.title}" title="Remove"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        </td>` : ""}
      </tr>`);
  }) : emptyRow(owner ? 7 : 6, "No products in this view."));

  function paint() {
    const rows = data.products.filter((p) => filter === "all" || (filter === "drafts" ? !p.is_active : p.kind === filter));
    render(el, html`
      ${pageHead("Products", "Add, edit and remove ready-to-buy pieces and DIY kits, and keep stock counts up to date.",
        owner ? html`<button type="button" class="btn btn-primary btn-sm" data-new><i data-lucide="plus" class="w-4 h-4"></i>Add product</button>` : "")}
      <div class="filter-row mb-4">${[["all", "All"], ["physical", "Pieces"], ["kit", "DIY kits"], ["drafts", "Drafts"], ["archived", `Archived (${archived.products.length})`]].map(([key, label]) => html`
        <button type="button" class="filter-btn" aria-pressed="${filter === key}" data-filter="${key}">${label}</button>`)}</div>
      <div class="panel overflow-hidden"><div class="table-wrap"><table class="data-table">
        ${filter === "archived" ? html`
          <thead><tr><th>Product</th><th>Archived</th><th class="num">Price</th>${owner ? html`<th></th>` : ""}</tr></thead>
          <tbody>${archived.products.length ? archived.products.map((p) => html`<tr data-href="#catalogue/${p.id}">
            <td><div class="flex items-center gap-3"><img src="${p.image || "/assets/images/profile_avatar.jpg"}" alt="" class="w-12 h-12 rounded-lg object-cover bg-sand opacity-70">
              <span><span class="block font-semibold">${p.title}</span><span class="block text-xs text-charcoal-light">${KIND_LABEL[p.kind]}</span></span></div></td>
            <td class="whitespace-nowrap">${dateOnly(p.archived_at)}</td>
            <td class="num">${rupees(p.price_paise)}</td>
            ${owner ? html`<td class="text-right"><button type="button" class="btn btn-outline btn-sm" data-restore="${p.id}"><i data-lucide="archive-restore" class="w-4 h-4"></i>Restore</button></td>` : ""}
          </tr>`) : emptyRow(owner ? 4 : 3, "Nothing archived.")}</tbody>`
        : html`
          <thead><tr><th>Product</th><th>Status</th><th>Size</th><th class="num">Price</th><th class="num">Pieces in stock</th><th>Available</th>${owner ? html`<th></th>` : ""}</tr></thead>
          <tbody>${activeRows(rows)}</tbody>`}
      </table></div></div>
      <p class="hint mt-3">${filter === "archived"
        ? "Archived products have past orders, so they're kept for your records. Restoring one brings it back as a draft."
        : "Type a new stock number and press Enter (or click away) to save it. “Held at checkout” is stock reserved for customers who are paying right now."}</p>`);
  }

  el.onclick = async (e) => {
    let b;
    if ((b = e.target.closest("[data-filter]"))) { filter = b.dataset.filter; paint(); return; }
    if (e.target.closest("[data-new]")) { await addProduct(ctx, data.categories); return; }
    if ((b = e.target.closest("[data-remove]"))) {
      const product = data.products.find((p) => p.id === b.dataset.remove);
      const result = await removeProductDialog(product, api);
      if (result) { toast(result.message, "success"); await load(); paint(); }
      return;
    }
    if ((b = e.target.closest("[data-restore]"))) {
      busy(b, true, "Restoring…");
      try {
        await api(`/admin/products/${b.dataset.restore}/restore`, { method: "POST" });
        toast("Restored as a draft. Publish it when you're ready.", "success");
        await load();
        filter = "drafts";
        paint();
      } catch (err) { busy(b, false); toast(err.message, "error"); }
    }
  };
  el.onchange = async (e) => {
    const input = e.target.closest("[data-stock]");
    if (!input) return;
    const value = Number(input.value);
    if (!Number.isInteger(value) || value < 0) { toast("Stock must be a whole number, 0 or more.", "error"); return; }
    input.disabled = true;
    try {
      const updated = await api(`/admin/variants/${input.dataset.stock}`, { method: "PATCH", body: { on_hand: value } });
      const index = data.products.findIndex((p) => p.id === updated.id);
      if (index >= 0) data.products[index] = { ...data.products[index], ...updated };
      toast(`Stock saved: ${value} piece${value === 1 ? "" : "s"}.`, "success");
      paint();
    } catch (err) {
      input.disabled = false;
      toast(err.message, "error");
    }
  };
  paint();
}

// ------------------------------------------------------------------ detail

async function detail(el, ctx, id) {
  const owner = ctx.user.role === "admin";
  let [p, meta] = await Promise.all([api(`/admin/products/${encodeURIComponent(id)}`), api("/admin/products", { query: { kind: "none" } })]);
  if (!ctx.isCurrent()) return;
  const backHref = p.kind === "course" ? (p.course_id ? `#academy/${p.course_id}` : "#academy") : p.kind === "workshop" ? "#workshops" : "#catalogue";

  function paint() {
    render(el, html`
      ${back(backHref, p.kind === "course" ? "Course" : p.kind === "workshop" ? "Workshops" : "Products")}
      <div class="flex flex-wrap items-start justify-between gap-4 mt-3 mb-6">
        <div class="min-w-0">
          <h1 class="font-serif text-3xl font-bold leading-tight">${p.title}</h1>
          <div class="flex flex-wrap items-center gap-2 mt-2">${liveChip(p)}<span class="text-sm text-charcoal-light">${KIND_LABEL[p.kind]}</span></div>
        </div>
        <div class="flex flex-wrap gap-2">
          ${p.is_active ? html`<a class="btn btn-outline btn-sm" href="/?item=${p.slug}" target="_blank" rel="noopener"><i data-lucide="external-link" class="w-4 h-4"></i>View in store</a>` : ""}
          ${owner && !p.archived_at ? html`<button type="button" class="btn ${p.is_active ? "btn-outline" : "btn-primary"} btn-sm" data-publish="${!p.is_active}">${p.is_active ? "Hide from store" : "Publish"}</button>` : ""}
          ${owner && p.archived_at ? html`<button type="button" class="btn btn-primary btn-sm" data-restore><i data-lucide="archive-restore" class="w-4 h-4"></i>Restore</button>` : ""}
          ${owner && !p.archived_at ? html`<button type="button" class="btn btn-ghost btn-sm text-red-700" data-remove><i data-lucide="trash-2" class="w-4 h-4"></i>Remove</button>` : ""}
        </div>
      </div>

      ${p.archived_at ? html`
        <div class="panel panel-body mb-4 bg-[#F8F5F1]">
          <p class="font-semibold">Archived on ${dateOnly(p.archived_at)}</p>
          <p class="text-sm text-charcoal-light mt-1">It's hidden from the store because it has past orders or students. Restore it to edit and publish it again.</p>
        </div>` : ""}

      ${!p.is_active && !p.archived_at && p.publish_problems.length ? html`
        <div class="panel panel-body mb-4 bg-[#FFFBF3] border-amber-200">
          <p class="font-semibold text-amber-900">Before this can go live</p>
          <ul class="tick-list mt-2">${p.publish_problems.map((problem) => html`<li>${problem}</li>`)}</ul>
        </div>` : ""}

      <div class="grid xl:grid-cols-[minmax(0,1fr)_24rem] gap-4 items-start">
        <form class="panel" data-details>
          <div class="panel-head"><h2 class="font-semibold">Details</h2>${owner ? "" : html`<span class="text-xs text-charcoal-light">Only the owner can edit details</span>`}</div>
          <fieldset class="panel-body grid sm:grid-cols-2 gap-4" ${owner ? "" : "disabled"}>
            <div class="sm:col-span-2">${field("Name", html`<input class="input" name="title" value="${p.title}" required>`)}</div>
            ${["physical", "kit"].includes(p.kind) ? html`
              ${field("Type", html`<select class="input" name="kind">
                <option value="physical" ${p.kind === "physical" ? "selected" : ""}>Ready-to-buy piece</option>
                <option value="kit" ${p.kind === "kit" ? "selected" : ""}>DIY kit</option></select>`,
                "Which section of the shop it sells from.")}
              <div data-physical-only>${field("Category", html`<select class="input" name="category"><option value="">None</option>${meta.categories.map((c) => html`<option value="${c.id}" ${c.id === p.category ? "selected" : ""}>${c.name}</option>`)}</select>`, "Groups the piece in the shop's filter row.")}</div>` : ""}
            ${field("Badge", html`<input class="input" name="badge" value="${p.badge}" placeholder="e.g. Gift set">`, "A short label on the product photo.")}
            <div class="sm:col-span-2">${field("Description", html`<textarea class="input" name="description" rows="4">${p.description}</textarea>`)}</div>
            ${["physical", "kit"].includes(p.kind) ? html`
              <div class="sm:col-span-2">${field("Materials", html`<input class="input" name="material" value="${p.material}">`)}</div>
              <div class="sm:col-span-2">${field("Key details", html`<textarea class="input" name="details" rows="4">${p.details.join("\n")}</textarea>`, "One per line. Shown as a checklist on the product page.")}</div>` : ""}
            ${p.kind === "kit" ? html`
              <div class="sm:col-span-2">${field("What's in the box", html`<textarea class="input" name="includes" rows="5">${p.includes.join("\n")}</textarea>`, "One item per line.")}</div>
              <div class="sm:col-span-2">${field("Tools note", html`<input class="input" name="tools_info" value="${p.tools_info}">`)}</div>` : ""}
            ${["physical", "kit"].includes(p.kind) ? html`
              <div class="sm:col-span-2">${field("Original price, before discount (₹)", html`<input class="input" name="compare_at" inputmode="decimal" value="${p.compare_at_paise ? p.compare_at_paise / 100 : ""}">`,
                "Optional, and not the selling price — that goes under “Price & pieces in stock”. Shown struck through to make a discount visible.")}</div>` : ""}
          </fieldset>
          ${owner ? html`<div class="px-4 pb-4"><button type="submit" class="btn btn-primary">Save details</button></div>` : ""}
        </form>

        <div class="space-y-4">
          ${p.kind === "workshop" ? html`<section class="panel panel-body text-sm">Prices and seats for workshops are set per session under <a class="link" href="#workshops">Workshops</a>.</section>` : html`
          <section class="panel">
            <div class="panel-head"><h2 class="font-semibold">${p.kind === "course" ? "Price" : "Price & pieces in stock"}</h2></div>
            <div class="divide-y divide-sand">${p.variants.map((v) => html`
              <form class="panel-body grid grid-cols-2 gap-3" data-variant="${v.id}">
                ${p.kind === "course" ? "" : html`<div class="col-span-2">${field("Size or option", html`<input class="input" name="label" value="${v.label}" placeholder="e.g. 12 x 16 in" ${owner ? "" : "disabled"}>`)}</div>`}
                ${field("Price (₹)", html`<input class="input" name="price" inputmode="decimal" value="${v.price_paise ? v.price_paise / 100 : ""}" ${owner ? "" : "disabled"}>`)}
                ${p.kind === "course" ? html`<div></div>` : field("Pieces in stock", html`<input class="input" type="number" min="0" name="on_hand" value="${v.on_hand}">`,
                  v.on_hand > v.available ? `${v.available} can be bought now · ${v.on_hand - v.available} held at checkout` : v.on_hand === 0 ? "Shows as sold out in the store" : "")}
                <div class="col-span-2 flex flex-wrap items-center justify-between gap-2">
                  ${owner && p.kind !== "course" ? html`<label class="flex items-center gap-2 text-sm"><input type="checkbox" class="checkbox" name="is_active" ${v.is_active ? "checked" : ""}>Offered</label>` : html`<span></span>`}
                  <span class="flex gap-2">
                    ${owner && p.variants.length > 1 ? html`<button type="button" class="btn btn-ghost btn-sm text-red-700" data-delete-variant="${v.id}">Remove size</button>` : ""}
                    <button type="submit" class="btn btn-outline btn-sm">Save</button>
                  </span>
                </div>
              </form>`)}
            </div>
            ${owner && p.kind !== "course" ? html`<div class="panel-body border-t border-sand"><button type="button" class="btn btn-ghost btn-sm" data-add-variant><i data-lucide="plus" class="w-4 h-4"></i>Add another size</button></div>` : ""}
          </section>`}

          <section class="panel">
            <div class="panel-head"><h2 class="font-semibold">Photos</h2>
              ${owner ? html`<label class="btn btn-outline btn-sm cursor-pointer"><i data-lucide="upload" class="w-4 h-4"></i>Add photo<input type="file" accept="image/jpeg,image/png,image/webp" class="sr-only" data-photo></label>` : ""}</div>
            <div class="panel-body">
              ${p.media.length ? html`<ul class="grid grid-cols-3 gap-2">${p.media.map((m, i) => html`
                <li class="relative group">
                  <img src="${m.url}" alt="" class="aspect-square w-full object-cover rounded-lg bg-sand">
                  ${i === 0 ? html`<span class="absolute top-1 left-1 chip chip-surface !text-[10px]">Cover</span>` : ""}
                  ${owner ? html`<div class="absolute inset-x-1 bottom-1 flex justify-between opacity-0 group-hover:opacity-100 focus-within:opacity-100">
                    <button type="button" class="icon-btn !w-7 !h-7 bg-white" data-move="${m.id}" data-dir="-1" aria-label="Move earlier" ${i === 0 ? "disabled" : ""}><i data-lucide="chevron-left" class="w-4 h-4"></i></button>
                    <button type="button" class="icon-btn !w-7 !h-7 bg-white text-red-700" data-delete-photo="${m.id}" aria-label="Delete photo"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
                    <button type="button" class="icon-btn !w-7 !h-7 bg-white" data-move="${m.id}" data-dir="1" aria-label="Move later" ${i === p.media.length - 1 ? "disabled" : ""}><i data-lucide="chevron-right" class="w-4 h-4"></i></button>
                  </div>` : ""}
                </li>`)}</ul>` : html`<p class="text-sm text-charcoal-light">No photos yet. The first photo becomes the cover.</p>`}
            </div>
          </section>
        </div>
      </div>`);
  }

  const refresh = (updated, message) => {
    p = updated;
    if (message) toast(message, "success");
    paint();
  };

  el.onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    const button = form.querySelector('button[type="submit"]');
    busy(button, true, "Saving…");
    try {
      if (form.matches("[data-details]")) {
        const v = Object.fromEntries(new FormData(form));
        const body = { title: v.title, badge: v.badge, description: v.description };
        // The category field stays in the form when the type is switched to a kit — it's only
        // hidden — so the value is dropped here rather than trusted.
        if (form.kind) Object.assign(body, { kind: v.kind, category: v.kind === "physical" ? v.category : "" });
        if ("material" in v) Object.assign(body, { material: v.material, details: lines(v.details),
          compare_at_paise: v.compare_at ? toPaise(v.compare_at) : null });
        if ("includes" in v) Object.assign(body, { includes: lines(v.includes), tools_info: v.tools_info });
        refresh(await api(`/admin/products/${p.id}`, { method: "PATCH", body }), "Details saved.");
      } else if (form.matches("[data-variant]")) {
        const body = {};
        if (owner) {
          if (form.label) body.label = form.label.value;
          if (form.price.value) body.price_paise = toPaise(form.price.value);
          if (form.is_active) body.is_active = form.is_active.checked;
        }
        if (form.on_hand) body.on_hand = Number(form.on_hand.value);
        refresh(await api(`/admin/variants/${form.dataset.variant}`, { method: "PATCH", body }), "Saved.");
      }
    } catch (err) {
      busy(button, false);
      toast(err.message, "error");
    }
  };

  el.onchange = async (e) => {
    const input = e.target.closest("[data-photo]");
    if (!input || !input.files.length) return;
    const data = new FormData();
    data.append("file", input.files[0]);
    data.append("alt", p.title);
    toast("Uploading photo…");
    try {
      refresh(await upload(`/admin/products/${p.id}/media`, data), "Photo added.");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  el.onclick = async (e) => {
    let b;
    if ((b = e.target.closest("[data-publish]"))) {
      busy(b, true, "Saving…");
      try {
        refresh(await api(`/admin/products/${p.id}`, { method: "PATCH", body: { is_active: b.dataset.publish === "true" } }),
          b.dataset.publish === "true" ? "Published — it's live in the store." : "Hidden from the store.");
      } catch (err) {
        busy(b, false);
        toast(err.data && err.data.problems ? `${err.message} ${err.data.problems.join(" ")}` : err.message, "error");
      }
      return;
    }
    if (e.target.closest("[data-remove]")) {
      const result = await removeProductDialog(p, api);
      if (!result) return;
      toast(result.message, "success");
      if (result.result === "deleted") ctx.navigate(backHref.startsWith("#academy/") ? "#academy" : backHref);
      else refresh(await api(`/admin/products/${encodeURIComponent(p.id)}`));
      return;
    }
    if ((b = e.target.closest("[data-restore]"))) {
      busy(b, true, "Restoring…");
      try { refresh(await api(`/admin/products/${p.id}/restore`, { method: "POST" }), "Restored as a draft."); } catch (err) { busy(b, false); toast(err.message, "error"); }
      return;
    }
    if ((b = e.target.closest("[data-delete-photo]"))) {
      if (!confirm("Delete this photo?")) return;
      try { refresh(await api(`/admin/media/${b.dataset.deletePhoto}`, { method: "DELETE" }), "Photo deleted."); } catch (err) { toast(err.message, "error"); }
      return;
    }
    if ((b = e.target.closest("[data-move]"))) {
      const ids = p.media.map((m) => m.id);
      const from = ids.indexOf(Number(b.dataset.move));
      const to = from + Number(b.dataset.dir);
      [ids[from], ids[to]] = [ids[to], ids[from]];
      try { refresh(await api(`/admin/products/${p.id}/media/order`, { method: "POST", body: { ids } })); } catch (err) { toast(err.message, "error"); }
      return;
    }
    if ((b = e.target.closest("[data-delete-variant]"))) {
      if (!confirm("Remove this size? If it has been ordered before it's hidden instead of deleted.")) return;
      try { refresh(await api(`/admin/variants/${b.dataset.deleteVariant}`, { method: "DELETE" }), "Size removed."); } catch (err) { toast(err.message, "error"); }
      return;
    }
    if (e.target.closest("[data-add-variant]")) {
      const updated = await formDialog({
        title: "Add a size",
        submitLabel: "Add",
        body: html`
          ${field("Size or option", html`<input class="input" name="label" required autofocus placeholder="e.g. 24 x 36 in">`)}
          <div class="grid grid-cols-2 gap-4">
            ${field("Price (₹)", html`<input class="input" name="price" inputmode="decimal" required>`)}
            ${field("Pieces in stock", html`<input class="input" type="number" min="0" name="on_hand" value="0" required>`)}
          </div>`,
        onSubmit: (v) => api(`/admin/products/${p.id}/variants`, { method: "POST", body: { label: v.label, price_paise: toPaise(v.price) || 0, on_hand: Number(v.on_hand) || 0 } }),
      });
      if (updated) refresh(updated, "Size added.");
    }
  };

  paint();
}
