// Revenue (stacked columns: products and Academy) and discount codes.
import { api } from "../lib/api.js";
import { html, render, toast } from "../lib/dom.js";
import { dateOnly, rupees } from "../lib/format.js";
import { emptyRow, field, formDialog, pageHead } from "./ui.js";

// Validated with the dataviz palette checker against the white chart surface (see README).
const SERIES = [
  { key: "products_paise", label: "Products", color: "var(--series-products)" },
  { key: "academy_paise", label: "Academy", color: "var(--series-academy)" },
];
const RANGES = [["day-30", "Last 30 days"], ["day-90", "Last 90 days"], ["month-12", "Last 12 months"]];
const SVG_NS = "http://www.w3.org/2000/svg";

export default async function money(el, ctx) {
  return ctx.route === "coupons" ? coupons(el, ctx) : revenue(el, ctx);
}

// ------------------------------------------------------------------ revenue

function compact(paise) {
  const r = paise / 100;
  if (r >= 1e7) return `₹${+(r / 1e7).toFixed(1)}Cr`;
  if (r >= 1e5) return `₹${+(r / 1e5).toFixed(1)}L`;
  if (r >= 1e3) return `₹${+(r / 1e3).toFixed(1)}k`;
  return `₹${Math.round(r)}`;
}

function niceTicks(maxPaise) {
  const max = Math.max(maxPaise, 100000);
  const rough = max / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough);
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function bucketLabel(bucket, period, long = false) {
  if (period === "month") {
    const [y, m] = bucket.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: long ? "long" : "short", year: long ? "numeric" : "2-digit" });
  }
  return new Date(`${bucket}T12:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short", ...(long ? { weekday: "short", year: "numeric" } : {}) });
}

function roundedTop(x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + radius}Q${x},${y} ${x + radius},${y}H${x + w - radius}Q${x + w},${y} ${x + w},${y + radius}V${y + h}Z`;
}

function drawChart(wrap, rows, period) {
  wrap.querySelectorAll("svg, .chart-tip").forEach((n) => n.remove());
  const width = Math.max(wrap.clientWidth, 320);
  const height = 300;
  const m = { top: 14, right: 8, bottom: 30, left: 58 };
  const innerW = width - m.left - m.right;
  const innerH = height - m.top - m.bottom;
  const totals = rows.map((r) => r.products_paise + r.academy_paise);
  const ticks = niceTicks(Math.max(0, ...totals));
  const top = ticks[ticks.length - 1];
  const y = (v) => m.top + innerH - (v / top) * innerH;
  const band = innerW / rows.length;
  const barW = Math.max(3, Math.min(24, band * 0.62));
  const every = period === "month" ? 1 : Math.ceil(rows.length / 8);

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", String(height));
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Revenue by period, products and Academy stacked. The table below lists every value.");
  const add = (parent, tag, attrs, text) => {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text !== undefined) node.textContent = text;
    parent.appendChild(node);
    return node;
  };

  ticks.forEach((t) => {
    add(svg, "line", { x1: m.left, x2: width - m.right, y1: y(t), y2: y(t), stroke: t === 0 ? "var(--viz-axis)" : "var(--viz-grid)", "stroke-width": 1, "shape-rendering": "crispEdges" });
    add(svg, "text", { x: m.left - 10, y: y(t) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--viz-muted)", "font-variant-numeric": "tabular-nums" }, compact(t));
  });

  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  wrap.appendChild(tip);

  const showTip = (row, cx, cy) => {
    tip.replaceChildren();
    const title = document.createElement("div");
    title.style.color = "var(--viz-ink-2)";
    title.textContent = bucketLabel(row.bucket, period, true);
    tip.appendChild(title);
    const line = (label, value, color) => {
      const r = document.createElement("div");
      r.className = "tip-row";
      const name = document.createElement("span");
      if (color) {
        const key = document.createElement("span");
        key.className = "tip-key";
        key.style.background = color;
        name.appendChild(key);
      }
      name.appendChild(document.createTextNode(label));
      const strong = document.createElement("strong");
      strong.textContent = value;
      r.append(strong, name);
      r.style.flexDirection = "row-reverse";
      tip.appendChild(r);
    };
    SERIES.forEach((s) => line(s.label, rupees(row[s.key]), s.color));
    if (row.refunds_paise) line("Refunded", `−${rupees(row.refunds_paise)}`);
    line("Paid orders", String(row.orders));
    tip.hidden = false;
    const left = Math.min(Math.max(cx, 95), width - 95);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(cy, 70)}px`;
  };

  rows.forEach((row, i) => {
    const cx = m.left + band * i + band / 2;
    const x = cx - barW / 2;
    const g = add(svg, "g", { class: "chart-col", tabindex: "0", "aria-label": `${bucketLabel(row.bucket, period, true)}: products ${rupees(row.products_paise)}, Academy ${rupees(row.academy_paise)}` });
    add(g, "rect", { class: "chart-hit", x: m.left + band * i, y: m.top, width: band, height: innerH, fill: "transparent" });
    const products = row.products_paise;
    const academy = row.academy_paise;
    const baseY = y(0);
    const productsH = baseY - y(products);
    const academyH = y(products) - y(products + academy);
    const gap = products && academy ? 2 : 0;
    if (products) {
      const d = academy ? `M${x},${baseY}V${y(products)}H${x + barW}V${baseY}Z` : roundedTop(x, y(products), barW, productsH, 4);
      add(g, "path", { class: "chart-bar", d, fill: SERIES[0].color });
    }
    if (academy) {
      const h = Math.max(1, academyH - gap);
      add(g, "path", { class: "chart-bar", d: roundedTop(x, y(products + academy), barW, h, 4), fill: SERIES[1].color });
    }
    if (i % every === 0) {
      add(svg, "text", { x: cx, y: height - 10, "text-anchor": "middle", "font-size": 11, fill: "var(--viz-muted)" }, bucketLabel(row.bucket, period));
    }
    const stackTop = y(products + academy);
    g.addEventListener("pointerenter", () => showTip(row, cx, stackTop));
    g.addEventListener("focus", () => showTip(row, cx, stackTop));
    g.addEventListener("pointerleave", () => { tip.hidden = true; });
    g.addEventListener("blur", () => { tip.hidden = true; });
  });

  wrap.prepend(svg);
}

async function revenue(el, ctx) {
  const range = RANGES.some(([key]) => key === ctx.params[0]) ? ctx.params[0] : "day-30";
  const [period, span] = range.split("-");
  const data = await api("/admin/revenue", { query: { period, span } });
  if (!ctx.isCurrent()) return;
  const t = data.totals;
  const empty = !t.orders && !t.refunds_paise;

  render(el, html`
    ${pageHead("Revenue", "Counted when payment is confirmed, after discounts. Refunds are subtracted on the day they're issued.",
      html`<a class="btn btn-outline btn-sm" href="/api/v1/admin/revenue.csv?period=${period}&span=${span}"><i data-lucide="download" class="w-4 h-4"></i>Download CSV</a>`)}
    <nav class="filter-row mb-5" aria-label="Date range">${RANGES.map(([key, label]) => html`<a href="#revenue/${key}" class="filter-btn" aria-current="${key === range}">${label}</a>`)}</nav>

    <section class="grid sm:grid-cols-2 xl:grid-cols-5 gap-4">
      <div class="panel stat"><p class="stat-label">Net revenue</p><p class="stat-value">${rupees(t.net_paise)}</p><p class="stat-sub">After refunds</p></div>
      <div class="panel stat"><p class="stat-label">Products</p><p class="stat-value">${rupees(t.products_paise)}</p><p class="stat-sub">Pieces and kits, incl. shipping</p></div>
      <div class="panel stat"><p class="stat-label">Academy</p><p class="stat-value">${rupees(t.academy_paise)}</p><p class="stat-sub">Courses and workshops</p></div>
      <div class="panel stat"><p class="stat-label">Refunded</p><p class="stat-value">${rupees(t.refunds_paise)}</p><p class="stat-sub">Issued in this period</p></div>
      <div class="panel stat"><p class="stat-label">Paid orders</p><p class="stat-value">${t.orders}</p><p class="stat-sub">${t.orders ? `${rupees(Math.round((t.products_paise + t.academy_paise) / t.orders / 100) * 100)} average` : "None yet"}</p></div>
    </section>

    <section class="panel mt-4 viz-root" style="--series-products:#C86D51;--series-academy:#2A78D6">
      <div class="panel-head">
        <h2 class="font-semibold">${period === "month" ? "Monthly" : "Daily"} revenue</h2>
        <div class="flex items-center gap-4 text-sm" aria-label="Legend">
          ${SERIES.map((s) => html`<span class="flex items-center gap-1.5"><span class="legend-swatch" style="background:${s.color}"></span>${s.label}</span>`)}
        </div>
      </div>
      <div class="panel-body">
        ${empty ? html`<p class="text-sm text-charcoal-light py-16 text-center">No paid orders in this period yet.</p>` : html`<div class="chart-wrap relative" data-chart></div>`}
      </div>
      <details class="border-t border-sand">
        <summary class="px-4 py-3 text-sm font-semibold cursor-pointer">Show as a table</summary>
        <div class="table-wrap"><table class="data-table">
          <thead><tr><th>${period === "month" ? "Month" : "Day"}</th><th class="num">Products</th><th class="num">Academy</th><th class="num">Refunded</th><th class="num">Net</th><th class="num">Orders</th></tr></thead>
          <tbody>${[...data.series].reverse().map((r) => html`<tr>
            <td class="whitespace-nowrap">${bucketLabel(r.bucket, period, true)}</td>
            <td class="num">${rupees(r.products_paise)}</td><td class="num">${rupees(r.academy_paise)}</td>
            <td class="num">${r.refunds_paise ? `−${rupees(r.refunds_paise)}` : "—"}</td><td class="num">${rupees(r.net_paise)}</td><td class="num">${r.orders}</td>
          </tr>`)}</tbody>
        </table></div>
      </details>
    </section>`);

  const wrap = el.querySelector("[data-chart]");
  if (!wrap) return;
  drawChart(wrap, data.series, period);
  let lastWidth = wrap.clientWidth;
  const observer = new ResizeObserver(() => {
    if (!wrap.isConnected) { observer.disconnect(); return; }
    if (Math.abs(wrap.clientWidth - lastWidth) > 8) { lastWidth = wrap.clientWidth; drawChart(wrap, data.series, period); }
  });
  observer.observe(wrap);
}

// ------------------------------------------------------------------ coupons

async function coupons(el, ctx) {
  let { coupons: rows } = await api("/admin/coupons");
  if (!ctx.isCurrent()) return;

  const describe = (c) => (c.kind === "percent" ? `${c.value}% off${c.max_discount_paise ? `, up to ${rupees(c.max_discount_paise)}` : ""}` : `${rupees(c.value)} off`);

  function paint() {
    render(el, html`
      ${pageHead("Discount codes", "Checked on the server at checkout — a code only works while it's active, in date and under its usage limit.",
        html`<button type="button" class="btn btn-primary btn-sm" data-new><i data-lucide="plus" class="w-4 h-4"></i>New code</button>`)}
      <div class="panel overflow-hidden"><div class="table-wrap"><table class="data-table">
        <thead><tr><th>Code</th><th>Discount</th><th class="num">Minimum spend</th><th class="num">Used</th><th>Ends</th><th>Status</th></tr></thead>
        <tbody>${rows.length ? rows.map((c) => html`<tr>
          <td class="font-mono font-semibold">${c.code}</td>
          <td>${describe(c)}</td>
          <td class="num">${c.min_subtotal_paise ? rupees(c.min_subtotal_paise) : "—"}</td>
          <td class="num">${c.used_count}${c.usage_limit ? ` / ${c.usage_limit}` : ""}</td>
          <td class="whitespace-nowrap">${c.ends_at ? dateOnly(c.ends_at) : "No end date"}</td>
          <td><button type="button" class="chip ${c.is_active ? "chip-ok" : "chip-muted"}" data-toggle="${c.code}" data-active="${c.is_active ? 1 : 0}" title="Click to ${c.is_active ? "pause" : "activate"}">${c.is_active ? "Active" : "Paused"}</button></td>
        </tr>`) : emptyRow(6, "No discount codes yet.")}</tbody>
      </table></div></div>`);
  }

  el.onclick = async (e) => {
    const toggle = e.target.closest("[data-toggle]");
    if (toggle) {
      try {
        rows = (await api(`/admin/coupons/${encodeURIComponent(toggle.dataset.toggle)}`, { method: "PATCH", body: { is_active: toggle.dataset.active !== "1" } })).coupons;
        paint();
      } catch (err) { toast(err.message, "error"); }
      return;
    }
    if (!e.target.closest("[data-new]")) return;
    const result = await formDialog({
      title: "New discount code",
      submitLabel: "Create code",
      body: html`
        ${field("Code", html`<input class="input uppercase font-mono" name="code" required pattern="[A-Za-z0-9]{3,30}" autofocus>`, "Letters and numbers only.")}
        <div class="grid grid-cols-2 gap-3">
          ${field("Type", html`<select class="input" name="kind"><option value="percent">Percentage off</option><option value="flat">Fixed amount off</option></select>`)}
          ${field("Amount", html`<input class="input" name="value" inputmode="decimal" required>`, "Percent, or rupees for a fixed amount.")}
        </div>
        <div class="grid grid-cols-2 gap-3">
          ${field("Minimum spend (₹)", html`<input class="input" name="min" inputmode="decimal">`)}
          ${field("Largest discount (₹)", html`<input class="input" name="max" inputmode="decimal">`, "Percentage codes only.")}
        </div>
        <div class="grid grid-cols-2 gap-3">
          ${field("Usage limit", html`<input class="input" type="number" min="1" name="usage_limit" placeholder="Unlimited">`)}
          ${field("Last day", html`<input class="input" type="date" name="ends_on">`)}
        </div>`,
      onSubmit: (v) => {
        const rupeesToPaise = (x) => (x ? Math.round(parseFloat(x) * 100) : null);
        return api("/admin/coupons", { method: "POST", body: {
          code: v.code, kind: v.kind,
          value: v.kind === "percent" ? Math.round(parseFloat(v.value)) : rupeesToPaise(v.value),
          min_subtotal_paise: rupeesToPaise(v.min) || 0,
          max_discount_paise: v.kind === "percent" ? rupeesToPaise(v.max) : null,
          usage_limit: v.usage_limit ? Number(v.usage_limit) : null,
          ends_on: v.ends_on || null,
        } });
      },
    });
    if (result) { rows = result.coupons; toast("Code created.", "success"); paint(); }
  };
  paint();
}
