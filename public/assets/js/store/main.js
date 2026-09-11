// Storefront entry point.
import { openAuth } from "../lib/auth.js";
import { $, on, refreshIcons, toast } from "../lib/dom.js";
import { firstName } from "../lib/format.js";
import { boot, getUser, onUserChange } from "../lib/session.js";
import { openAccount } from "./account.js";
import { addToCart, cartCount, onCartChange, openCart, refreshCart, restoreCart } from "./cart.js";
import { loadCatalogue, openProduct, setQuery, wireCatalogue } from "./catalogue.js";
import { openCheckout } from "./checkout.js";

function paintHeader(user) {
  const label = $("#account-label");
  if (label) label.textContent = user ? firstName(user) : "Sign in";
  const button = $("#account-btn");
  if (button) button.setAttribute("aria-label", user ? "Your account" : "Sign in");
  const courses = $("#my-courses-link");
  if (courses) courses.hidden = !user;
}

function paintCount() {
  const count = cartCount();
  ["#cart-count", "#cart-fab-count"].forEach((selector) => {
    const badge = $(selector);
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = count === 0;
  });
}

function dropParams(...names) {
  const url = new URL(location.href);
  names.forEach((name) => url.searchParams.delete(name));
  history.replaceState(null, "", url);
}

async function start() {
  refreshIcons();
  paintCount();
  onCartChange(paintCount);
  onUserChange(paintHeader);
  wireCatalogue();

  on(document, "click", "[data-open-product]", (e, el) => { e.preventDefault(); openProduct(el.dataset.openProduct); });
  on(document, "click", "[data-add]", (e, el) => { e.preventDefault(); addToCart(Number(el.dataset.add)); });
  on(document, "click", "[data-open-cart]", (e) => { e.preventDefault(); openCart(); });
  on(document, "click", "[data-account]", (e) => {
    e.preventDefault();
    if (getUser()) openAccount();
    else openAuth();
  });
  document.addEventListener("rg:checkout", () => openCheckout());

  const search = $("#shop-search");
  if (search) search.addEventListener("input", () => setQuery(search.value));
  const year = $("#year");
  if (year) year.textContent = String(new Date().getFullYear());

  try {
    await boot();
  } catch (err) {
    toast(err.message, "error");
  }
  paintHeader(getUser());

  const params = new URLSearchParams(location.search);
  const restoring = params.has("cart");
  if (restoring) {
    const restored = restoreCart(params.get("cart"));
    dropParams("cart");
    if (restored) {
      await refreshCart();
      openCart(false);
      toast("Your cart is back where you left it.", "success");
    }
  }
  if (params.has("signin")) {
    dropParams("signin");
    if (!getUser()) openAuth();
  }
  await loadCatalogue();
  if (!restoring) refreshCart();
}

start();
