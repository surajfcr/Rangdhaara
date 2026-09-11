// DOM helpers. `html` escapes every interpolated value unless it's wrapped in raw(),
// so product names, customer names and addresses can never inject markup.

export class Raw {
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

export const raw = (value) => new Raw(value ?? "");

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function part(value) {
  if (value === null || value === undefined) return "";
  if (value === true || value === false) return String(value); // aria-selected="false", data-publish="true"
  if (value instanceof Raw) return value.value;
  if (Array.isArray(value)) return value.map(part).join("");
  return esc(value);
}

export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i += 1) out += part(values[i]) + strings[i + 1];
  return new Raw(out);
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "stroke-width": 1.75 } });
}

export function render(target, template) {
  if (!target) return;
  target.innerHTML = part(template); // templates, arrays of templates, or plain text (escaped)
  refreshIcons();
}

export function on(root, event, selector, handler) {
  root.addEventListener(event, (e) => {
    const match = e.target.closest(selector);
    if (match && root.contains(match)) handler(e, match);
  });
}

// ------------------------------------------------------------------ toasts

let toastStack;

export function toast(message, tone = "info") {
  if (!toastStack) {
    toastStack = document.createElement("div");
    toastStack.className = "toast-stack";
    toastStack.setAttribute("role", "status");
    toastStack.setAttribute("aria-live", "polite");
    document.body.appendChild(toastStack);
  }
  const icon = { success: "check-circle-2", error: "alert-circle", info: "info" }[tone] || "info";
  const item = document.createElement("div");
  item.className = `toast toast-${tone}`;
  item.innerHTML = html`<i data-lucide="${icon}" class="w-4 h-4 shrink-0"></i><span>${message}</span>`.value;
  toastStack.appendChild(item);
  refreshIcons();
  requestAnimationFrame(() => item.classList.add("is-visible"));
  setTimeout(() => {
    item.classList.remove("is-visible");
    setTimeout(() => item.remove(), 250);
  }, tone === "error" ? 6000 : 4000);
}

// ------------------------------------------------------------------ layers (modals and drawers)

const layers = [];
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function openLayer(el, { onClose } = {}) {
  if (layers.some((l) => l.el === el)) return;
  layers.push({ el, onClose, returnFocus: document.activeElement });
  el.hidden = false;
  document.body.classList.add("has-layer");
  requestAnimationFrame(() => {
    el.classList.add("is-open");
    const target = el.querySelector("[autofocus]") || el.querySelector(".layer-panel " + FOCUSABLE);
    if (target) target.focus({ preventScroll: true });
  });
}

export function closeLayer(el) {
  const index = layers.findIndex((l) => l.el === el);
  if (index === -1) return;
  const [layer] = layers.splice(index, 1);
  el.classList.remove("is-open");
  setTimeout(() => { if (!el.classList.contains("is-open")) el.hidden = true; }, 220);
  if (!layers.length) document.body.classList.remove("has-layer");
  if (layer.returnFocus && layer.returnFocus.focus) layer.returnFocus.focus({ preventScroll: true });
  if (layer.onClose) layer.onClose();
}

export const isOpen = (el) => layers.some((l) => l.el === el);

document.addEventListener("keydown", (e) => {
  if (!layers.length) return;
  const top = layers[layers.length - 1].el;
  if (e.key === "Escape") {
    e.preventDefault();
    closeLayer(top);
  } else if (e.key === "Tab") {
    const items = [...top.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});

// ------------------------------------------------------------------ forms

export function busy(button, isBusy, label = "Working…") {
  if (!button) return;
  if (isBusy) {
    if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = html`<span class="spinner" aria-hidden="true"></span><span>${label}</span>`.value;
  } else {
    button.disabled = false;
    if (button.dataset.idleHtml) {
      button.innerHTML = button.dataset.idleHtml;
      delete button.dataset.idleHtml;
      refreshIcons();
    }
  }
}

export function clearFieldErrors(form) {
  $$(".field-error", form).forEach((n) => n.remove());
  $$('[aria-invalid="true"]', form).forEach((n) => n.removeAttribute("aria-invalid"));
  const box = $(".form-error", form);
  if (box) { box.hidden = true; box.textContent = ""; }
}

export function showFieldErrors(form, fields = {}, { stripPrefix = true } = {}) {
  let first = null;
  Object.entries(fields).forEach(([key, message]) => {
    const name = stripPrefix ? key.split(".").pop() : key;
    const input = form.querySelector(`[name="${CSS.escape(key)}"]`) || form.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!input) return;
    input.setAttribute("aria-invalid", "true");
    const note = document.createElement("p");
    note.className = "field-error";
    note.textContent = message;
    (input.closest(".field") || input.parentElement).appendChild(note);
    first = first || input;
  });
  if (first) first.focus();
  return Boolean(first);
}

export function formError(form, message) {
  const box = $(".form-error", form);
  if (!box) return;
  box.textContent = message || "";
  box.hidden = !message;
}

export function values(form) {
  const data = {};
  new FormData(form).forEach((value, key) => { data[key] = typeof value === "string" ? value.trim() : value; });
  $$('input[type="checkbox"][name]', form).forEach((box) => { data[box.name] = box.checked; });
  return data;
}
