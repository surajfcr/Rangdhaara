// Order status presentation shared by the storefront, order page and admin.
import { html } from "./dom.js";

export const STATUS_TONE = {
  pending_payment: "chip-warn",
  paid: "chip-info",
  packed: "chip-info",
  shipped: "chip-info",
  delivered: "chip-ok",
  cancelled: "chip-muted",
  refunded: "chip-muted",
  expired: "chip-muted",
};

const STATUS_ICON = {
  pending_payment: "clock",
  paid: "badge-check",
  packed: "package",
  shipped: "truck",
  delivered: "check-circle-2",
  cancelled: "x-circle",
  refunded: "rotate-ccw",
  expired: "clock",
};

export const statusChip = (status, label) =>
  html`<span class="chip ${STATUS_TONE[status] || "chip-muted"}"><i data-lucide="${STATUS_ICON[status] || "circle"}" class="w-3.5 h-3.5"></i>${label}</span>`;

export const KIND_LABEL = { physical: "Ready to buy", kit: "DIY kit", course: "Masterclass", workshop: "Live workshop" };

// Categories that belong to a kind with its own section on the storefront, so they never
// belong in the finished-pieces filter row. Mirrors KIND_CATEGORY in app/routers/admin_api.py.
export const KIND_CATEGORY_IDS = ["diy-kits", "masterclasses", "workshops"];
