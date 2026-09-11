// Sign-in dialog shared by every page: password sign-in, emailed-code sign-in,
// verified sign-up and password reset. It builds its own markup on first use.
import { api } from "./api.js";
import { busy, clearFieldErrors, closeLayer, formError, html, on, openLayer, refreshIcons, render, showFieldErrors, toast, values } from "./dom.js";
import { firstName } from "./format.js";
import { getStore, setUser } from "./session.js";

let root;
let panel;
let flow = {};

function ensureRoot() {
  if (root) return;
  root = document.createElement("div");
  root.className = "layer";
  root.hidden = true;
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-labelledby", "auth-title");
  root.innerHTML = '<div class="layer-backdrop" data-close></div><div class="layer-panel dialog" style="max-width:28rem"></div>';
  panel = root.querySelector(".layer-panel");
  document.body.appendChild(root);

  on(root, "click", "[data-close]", () => closeLayer(root));
  on(root, "click", "[data-go]", (e, el) => {
    e.preventDefault();
    const form = root.querySelector("form");
    const typed = form && form.identifier ? form.identifier.value.trim() : flow.identifier;
    go(el.dataset.go, { purpose: el.dataset.purpose || flow.purpose, identifier: typed });
  });
  on(root, "click", "[data-toggle-password]", (e, el) => {
    const input = el.parentElement.querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    el.setAttribute("aria-label", show ? "Hide password" : "Show password");
    el.innerHTML = `<i data-lucide="${show ? "eye-off" : "eye"}" class="w-4 h-4"></i>`;
    refreshIcons();
  });
  on(root, "click", "[data-resend]", resend);
  on(root, "input", 'input[name="code"]', (e, el) => { el.value = el.value.replace(/\D/g, "").slice(0, 6); });
  root.addEventListener("submit", submit);
}

export function openAuth({ mode = "signin", reason = "", identifier = "", onDone } = {}) {
  ensureRoot();
  flow = { mode, reason, identifier, onDone, purpose: mode === "code" ? "login" : null, requestId: null };
  paint();
  openLayer(root);
}

function go(mode, extra = {}) {
  flow = { ...flow, ...extra, mode };
  paint();
  const focus = panel.querySelector("input:not([type=hidden])");
  if (focus) focus.focus();
}

// ------------------------------------------------------------------ views

const head = (title, subtitle) => html`
  <div class="flex items-start justify-between gap-4 mb-5">
    <div>
      <h2 id="auth-title" class="font-serif text-2xl font-bold text-charcoal leading-tight">${title}</h2>
      ${subtitle ? html`<p class="text-sm text-charcoal-light mt-1.5 leading-relaxed">${subtitle}</p>` : ""}
    </div>
    <button type="button" class="icon-btn -mr-2 -mt-1" data-close aria-label="Close"><i data-lucide="x" class="w-5 h-5"></i></button>
  </div>`;

const tabs = (active) => html`
  <div class="seg mb-5" role="tablist" aria-label="Sign in or create an account">
    <button type="button" role="tab" class="seg-btn" aria-selected="${active === "signin"}" data-go="signin">Sign in</button>
    <button type="button" role="tab" class="seg-btn" aria-selected="${active === "signup"}" data-go="signup">Create account</button>
  </div>`;

const passwordField = (name, label, autocomplete) => html`
  <label class="field">
    <span class="field-label">${label}</span>
    <span class="relative block">
      <input class="input pr-11" type="password" name="${name}" autocomplete="${autocomplete}" required>
      <button type="button" class="pw-toggle icon-btn" data-toggle-password aria-label="Show password"><i data-lucide="eye" class="w-4 h-4"></i></button>
    </span>
  </label>`;

const identifierField = (value) => html`
  <label class="field">
    <span class="field-label">Email or mobile number</span>
    <input class="input" name="identifier" autocomplete="username" required value="${value || ""}" autofocus>
  </label>`;

const errorBox = html`<div class="form-error" role="alert" hidden></div>`;

function paint() {
  const views = {
    signin: () => html`
      ${head("Welcome back", flow.reason || "Sign in to track orders, reuse saved addresses and open your courses.")}
      ${tabs("signin")}
      <form data-form="signin" class="space-y-4" novalidate>
        ${identifierField(flow.identifier)}
        ${passwordField("password", "Password", "current-password")}
        ${errorBox}
        <button type="submit" class="btn btn-primary w-full">Sign in</button>
        <div class="flex flex-wrap items-center justify-between gap-2 text-sm">
          <button type="button" class="link" data-go="code" data-purpose="reset">Forgot password?</button>
          <button type="button" class="link" data-go="code" data-purpose="login">Email me a code instead</button>
        </div>
      </form>`,
    code: () => html`
      ${head(flow.purpose === "reset" ? "Reset your password" : "Sign in with a code",
        flow.purpose === "reset" ? "We'll email a 6-digit code to the address on your account." : "No password needed. We'll email you a 6-digit code — handy if you checked out as a guest.")}
      <form data-form="code" class="space-y-4" novalidate>
        ${identifierField(flow.identifier)}
        ${errorBox}
        <button type="submit" class="btn btn-primary w-full">Email me a code</button>
        <button type="button" class="link text-sm" data-go="signin">Back to sign in</button>
      </form>`,
    verify: () => html`
      ${head("Check your email", flow.message || "Enter the 6-digit code we sent you.")}
      <form data-form="verify" class="space-y-4" novalidate>
        <label class="field">
          <span class="field-label">6-digit code</span>
          <input class="input code-input" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required autofocus>
        </label>
        ${getStore() && getStore().email_dev_mode ? html`<p class="dev-note"><i data-lucide="terminal" class="w-4 h-4 shrink-0"></i><span>Development mode: no email is sent. The code is printed in the server window.</span></p>` : ""}
        ${errorBox}
        <button type="submit" class="btn btn-primary w-full">${flow.purpose === "reset" ? "Continue" : "Verify and sign in"}</button>
        <div class="flex flex-wrap items-center justify-between gap-2 text-sm">
          <button type="button" class="link" data-resend>Send a new code</button>
          <button type="button" class="link" data-go="${flow.purpose === "signup" ? "signup" : "code"}">${flow.purpose === "signup" ? "Change details" : "Use a different email"}</button>
        </div>
        <p class="hint">Codes expire after 10 minutes. Check your spam folder if it hasn't arrived.</p>
      </form>`,
    signup: () => html`
      ${head("Create your account", "Save your address, follow every order and keep your courses in one place.")}
      ${tabs("signup")}
      <form data-form="signup" class="space-y-4" novalidate>
        <label class="field"><span class="field-label">Full name</span><input class="input" name="full_name" autocomplete="name" required autofocus value="${(flow.draft && flow.draft.full_name) || ""}"></label>
        <div class="grid sm:grid-cols-2 gap-4">
          <label class="field"><span class="field-label">Email</span><input class="input" type="email" name="email" autocomplete="email" required value="${(flow.draft && flow.draft.email) || ""}"></label>
          <label class="field"><span class="field-label">Mobile number</span><input class="input" type="tel" name="phone" inputmode="numeric" autocomplete="tel-national" placeholder="10 digits" required value="${(flow.draft && flow.draft.phone) || ""}"></label>
        </div>
        ${passwordField("password", "Password", "new-password")}
        ${passwordField("confirm_password", "Confirm password", "new-password")}
        <p class="hint -mt-2">At least 8 characters. Avoid common passwords.</p>
        <label class="flex items-start gap-2.5 text-sm text-charcoal-light">
          <input type="checkbox" name="marketing_opt_in" class="checkbox mt-0.5">
          <span>Email me when new pieces and masterclasses launch</span>
        </label>
        ${errorBox}
        <button type="submit" class="btn btn-primary w-full">Continue</button>
        <p class="hint text-center">We'll email a code to confirm it's your address.</p>
      </form>`,
    newpass: () => html`
      ${head("Choose a new password", "You'll stay signed in here, and every other device will be signed out.")}
      <form data-form="newpass" class="space-y-4" novalidate>
        ${passwordField("password", "New password", "new-password")}
        ${passwordField("confirm_password", "Confirm new password", "new-password")}
        <p class="hint -mt-2">At least 8 characters.</p>
        ${errorBox}
        <button type="submit" class="btn btn-primary w-full">Save password</button>
      </form>`,
  };
  render(panel, (views[flow.mode] || views.signin)());
}

// ------------------------------------------------------------------ actions

async function submit(e) {
  const form = e.target.closest("form[data-form]");
  if (!form) return;
  e.preventDefault();
  clearFieldErrors(form);
  const data = values(form);
  const button = form.querySelector('button[type="submit"]');
  busy(button, true, "Please wait…");
  try {
    if (form.dataset.form === "signin") {
      if (!data.identifier || !data.password) {
        formError(form, "Enter your email or mobile number and your password.");
        return;
      }
      try {
        const res = await api("/auth/login", { method: "POST", body: { identifier: data.identifier, password: data.password } });
        finish(res.user, `Welcome back, ${firstName(res.user)}.`);
      } catch (err) {
        if (err.code !== "password_reset_required") throw err;
        go("verify", { purpose: "reset", requestId: err.data.request_id, identifier: data.identifier,
          message: `For your security, please choose a new password. We've emailed a code to ${err.data.destination}.` });
      }
    } else if (form.dataset.form === "code") {
      if (!data.identifier) { formError(form, "Enter your email or mobile number."); return; }
      const res = await api("/auth/code", { method: "POST", body: { identifier: data.identifier, purpose: flow.purpose || "login" } });
      go("verify", { requestId: res.request_id, identifier: data.identifier, message: res.message });
    } else if (form.dataset.form === "signup") {
      if (data.password !== data.confirm_password) {
        showFieldErrors(form, { confirm_password: "The two passwords don't match." });
        return;
      }
      const body = { full_name: data.full_name, email: data.email, phone: data.phone, password: data.password,
        confirm_password: data.confirm_password, marketing_opt_in: data.marketing_opt_in };
      const res = await api("/auth/signup", { method: "POST", body });
      go("verify", { purpose: "signup", requestId: res.request_id, identifier: data.email, signup: body,
        draft: { full_name: data.full_name, email: data.email, phone: data.phone }, message: `Enter the 6-digit code we emailed to ${data.email}.` });
    } else if (form.dataset.form === "verify") {
      if (!/^\d{6}$/.test(data.code || "")) { showFieldErrors(form, { code: "Enter all 6 digits." }); return; }
      const res = await api("/auth/code/verify", { method: "POST", body: { request_id: flow.requestId, code: data.code } });
      if (res.reset_token) {
        go("newpass", { resetToken: res.reset_token });
      } else {
        finish(res.user, flow.purpose === "signup" ? `Welcome to Rangdhaara, ${firstName(res.user)}.` : `Signed in as ${res.user.email}.`);
      }
    } else if (form.dataset.form === "newpass") {
      if (data.password !== data.confirm_password) {
        showFieldErrors(form, { confirm_password: "The two passwords don't match." });
        return;
      }
      const res = await api("/auth/password/reset", { method: "POST", body: { reset_token: flow.resetToken, password: data.password, confirm_password: data.confirm_password } });
      finish(res.user, "Password saved. You're signed in.");
    }
  } catch (err) {
    if (err.code === "reset_expired") {
      go("code", { purpose: "reset" });
      toast(err.message, "error");
      return;
    }
    if (!showFieldErrors(form, err.fields)) formError(form, err.message);
    else formError(form, err.message);
  } finally {
    if (button.isConnected) busy(button, false);
  }
}

async function resend(e, el) {
  el.disabled = true;
  try {
    const res = flow.purpose === "signup" && flow.signup
      ? await api("/auth/signup", { method: "POST", body: flow.signup })
      : await api("/auth/code", { method: "POST", body: { identifier: flow.identifier, purpose: flow.purpose || "login" } });
    flow.requestId = res.request_id;
    toast("A new code is on its way.", "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setTimeout(() => { if (el.isConnected) el.disabled = false; }, 20000);
  }
}

function finish(user, message) {
  const done = flow.onDone;
  setUser(user);
  closeLayer(root);
  toast(message, "success");
  flow = {};
  if (done) done(user);
}
