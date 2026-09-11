// Shared admin building blocks: page headings, loading states and modal forms.
import { busy, clearFieldErrors, closeLayer, formError, html, on, openLayer, refreshIcons, showFieldErrors, values } from "../lib/dom.js";

export const pageHead = (title, subtitle = "", actions = "") => html`
  <div class="flex flex-wrap items-end justify-between gap-4 mb-6">
    <div class="min-w-0">
      <h1 class="font-serif text-3xl font-bold leading-tight">${title}</h1>
      ${subtitle ? html`<p class="text-sm text-charcoal-light mt-1">${subtitle}</p>` : ""}
    </div>
    ${actions ? html`<div class="flex flex-wrap gap-2">${actions}</div>` : ""}
  </div>`;

export const back = (href, label) => html`<a href="${href}" class="link text-sm inline-flex items-center gap-1"><i data-lucide="arrow-left" class="w-4 h-4"></i>${label}</a>`;

export const loading = () => html`<div class="space-y-4"><div class="skeleton h-9 w-72"></div><div class="skeleton h-28"></div><div class="skeleton h-72"></div></div>`;

export const errorBlock = (err) => html`<div class="form-error">${err.message}</div>`;

export const emptyRow = (cols, text) => html`<tr><td colspan="${cols}" class="text-center text-charcoal-light !py-12">${text}</td></tr>`;

export const field = (label, control, hint = "") => html`
  <label class="field"><span class="field-label">${label}</span>${control}${hint ? html`<span class="hint block mt-1">${hint}</span>` : ""}</label>`;

/**
 * Open a modal form. Resolves with whatever onSubmit returns (or the form values),
 * or null when cancelled. Errors thrown by onSubmit are shown inside the dialog.
 */
export function formDialog({ title, description = "", body = "", submitLabel = "Save", danger = false, wide = false, onSubmit }) {
  return new Promise((resolve) => {
    const layer = document.createElement("div");
    layer.className = "layer";
    layer.hidden = true;
    layer.setAttribute("role", "dialog");
    layer.setAttribute("aria-modal", "true");
    layer.setAttribute("aria-labelledby", "dialog-title");
    layer.innerHTML = html`
      <div class="layer-backdrop" data-cancel></div>
      <div class="layer-panel dialog" style="max-width:${wide ? "40rem" : "30rem"}">
        <form novalidate class="space-y-4">
          <div>
            <h2 id="dialog-title" class="font-serif text-2xl font-bold leading-tight">${title}</h2>
            ${description ? html`<p class="text-sm text-charcoal-light mt-1.5 leading-relaxed">${description}</p>` : ""}
          </div>
          ${body}
          <div class="form-error" role="alert" hidden></div>
          <div class="flex justify-end gap-2 pt-1">
            <button type="button" class="btn btn-ghost" data-cancel>Cancel</button>
            <button type="submit" class="btn ${danger ? "bg-red-700 text-white hover:bg-red-800" : "btn-primary"}">${submitLabel}</button>
          </div>
        </form>
      </div>`.value;
    document.body.appendChild(layer);
    refreshIcons();

    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      closeLayer(layer);
      setTimeout(() => layer.remove(), 300);
      resolve(result);
    };
    on(layer, "click", "[data-cancel]", () => finish(null));
    const form = layer.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearFieldErrors(form);
      const data = values(form);
      const button = form.querySelector('button[type="submit"]');
      busy(button, true, "Working…");
      try {
        const result = onSubmit ? await onSubmit(data, form) : data;
        finish(result === undefined ? data : result);
      } catch (err) {
        busy(button, false);
        showFieldErrors(form, err.fields || {});
        const problems = err.data && err.data.problems ? ` ${err.data.problems.join(" ")}` : "";
        formError(form, `${err.message}${problems}`);
      }
    });
    openLayer(layer, { onClose: () => finish(null) });
  });
}

/** Ask before removing a product or course. Resolves with the server's result, or null if cancelled. */
export function removeProductDialog(product, api) {
  const noun = { course: "course", workshop: "workshop" }[product.kind] || "product";
  return formDialog({
    title: `Remove “${product.title}”?`,
    description: `If this ${noun} has never been ordered, it's deleted permanently along with its photos${product.kind === "course" ? " and lesson videos" : ""}. `
      + `If anyone has bought it, it's archived instead: hidden from the store, with past orders, receipts and student access kept. You can restore an archived item later.`,
    submitLabel: "Remove",
    danger: true,
    onSubmit: () => api(`/admin/products/${encodeURIComponent(product.id)}`, { method: "DELETE" }),
  });
}

export const REVIEW_LABELS = {
  amount_mismatch: "Amount mismatch",
  oversold: "Oversold",
  fulfilment_failed: "Fulfilment failed",
  payment_after_close: "Paid after closing",
  legacy_unverified_payment: "Unverified old-site payment",
};

export const PROVIDER_LABELS = {
  mock: "Test gateway",
  razorpay: "Razorpay",
  cashfree: "Cashfree",
  manual_upi: "UPI transfer",
  legacy: "Old website",
};
