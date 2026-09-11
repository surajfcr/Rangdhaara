// Hands the customer to whichever gateway the server started a payment with.
// The browser never reports success: every path ends on the order page, which asks the server.

const TOKENS_KEY = "rg_order_tokens";

function readTokens() {
  try { return JSON.parse(localStorage.getItem(TOKENS_KEY) || "{}"); } catch { return {}; }
}

export function rememberOrder(orderId, token) {
  if (!orderId || !token) return;
  const tokens = readTokens();
  tokens[orderId] = token;
  const recent = Object.entries(tokens).slice(-30);
  try { localStorage.setItem(TOKENS_KEY, JSON.stringify(Object.fromEntries(recent))); } catch { /* storage unavailable */ }
}

export function tokenFor(orderId) {
  const fromUrl = new URLSearchParams(location.search).get("t");
  if (fromUrl) {
    rememberOrder(orderId, fromUrl);
    return fromUrl;
  }
  return readTokens()[orderId] || "";
}

export function orderUrl(orderId, token) {
  return `/order/${encodeURIComponent(orderId)}${token ? `?t=${encodeURIComponent(token)}` : ""}`;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error("The payment window couldn't load. Check your connection, or pause any ad blocker for this site, then try again."));
    document.head.appendChild(script);
  });
}

export async function launchPayment({ provider, client, orderId, token }) {
  const back = orderUrl(orderId, token);
  rememberOrder(orderId, token);
  if (!client || provider === "manual_upi") {
    location.href = back;
    return;
  }
  if (provider === "mock") {
    location.href = client.redirect_url;
    return;
  }
  if (provider === "razorpay") {
    await loadScript("https://checkout.razorpay.com/v1/checkout.js");
    const checkout = new window.Razorpay({
      key: client.key_id,
      order_id: client.order_id,
      amount: client.amount,
      currency: client.currency,
      name: client.name,
      description: client.description,
      prefill: client.prefill,
      theme: { color: "#C86D51" },
      handler: () => { location.href = back; },
      modal: { ondismiss: () => { location.href = back; } },
    });
    checkout.open();
    return;
  }
  if (provider === "cashfree") {
    await loadScript("https://sdk.cashfree.com/js/v3/cashfree.js");
    const cashfree = window.Cashfree({ mode: client.mode });
    await cashfree.checkout({ paymentSessionId: client.payment_session_id, redirectTarget: "_self" });
    return;
  }
  location.href = back;
}
