"""HTML routes: the static app shells, plus server-rendered receipts, packing
slips, the development payment page, and signed lesson video streaming."""
import html
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import orders
from .academy import active_enrollment
from .auth import current_user
from .config import PUBLIC_DIR, settings
from .db import get_db, one
from .money import rupees
from .shipping import IST
from .storage import VIDEO_TYPES, playback_allowed, resolve_key

router = APIRouter(include_in_schema=False)

NO_CACHE = {"Cache-Control": "no-cache"}


def _shell(name: str) -> FileResponse:
    return FileResponse(PUBLIC_DIR / name, media_type="text/html", headers=NO_CACHE)


@router.get("/")
def index():
    return _shell("index.html")


@router.get("/index.html")
def index_legacy():
    return RedirectResponse("/", status_code=301)


@router.get("/admin")
def admin():
    return _shell("admin.html")


@router.get("/admin.html")
def admin_legacy():
    return RedirectResponse("/admin", status_code=301)


@router.get("/my-courses")
@router.get("/my-courses/{course_id}")
def learn(course_id: int | None = None):
    return _shell("learn.html")


@router.get("/order/{order_id}")
def order_page(order_id: str):
    return _shell("order.html")


@router.get("/manifest.json")
def manifest():
    return FileResponse(PUBLIC_DIR / "manifest.json", media_type="application/manifest+json")


@router.get("/robots.txt")
def robots():
    return FileResponse(PUBLIC_DIR / "robots.txt", media_type="text/plain")


@router.get("/favicon.ico")
def favicon():
    return FileResponse(PUBLIC_DIR / "assets" / "images" / "profile_avatar.jpg", media_type="image/jpeg")


# ---------------------------------------------------------------- printable documents

def _doc(title: str, body: str) -> HTMLResponse:
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; color: #23201D; background: #F4EFE8; margin: 0; padding: 32px 16px; }}
  .sheet {{ max-width: 760px; margin: 0 auto; background: #fff; border: 1px solid #E4DACE; border-radius: 10px; padding: 36px 40px; }}
  h1 {{ font-family: Georgia, serif; font-size: 26px; margin: 0; }}
  .muted {{ color: #7A6F66; font-size: 13px; }}
  .row {{ display: flex; justify-content: space-between; gap: 24px; flex-wrap: wrap; margin: 22px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 14px; }}
  th {{ text-align: left; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #7A6F66; border-bottom: 1px solid #CFC1B2; padding: 8px 6px; }}
  td {{ padding: 10px 6px; border-bottom: 1px solid #EFE7DD; vertical-align: top; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .total td {{ font-weight: 700; border-bottom: none; font-size: 16px; }}
  .actions {{ max-width: 760px; margin: 0 auto 14px; display: flex; justify-content: flex-end; }}
  button {{ background: #B85C38; color: #fff; border: 0; border-radius: 8px; padding: 9px 16px; font-weight: 600; cursor: pointer; }}
  @media print {{ body {{ background: #fff; padding: 0; }} .sheet {{ border: 0; }} .actions {{ display: none; }} }}
</style></head><body>
<div class="actions"><button onclick="window.print()">Print or save as PDF</button></div>
<div class="sheet">{body}</div></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


def _date(value: str | None) -> str:
    if not value:
        return ""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").astimezone(IST).strftime("%d %b %Y, %I:%M %p")


@router.get("/order/{order_id}/receipt")
def receipt(order_id: str, t: str | None = None, conn=Depends(get_db), user=Depends(current_user)):
    order = orders.get_order(conn, order_id)
    if not order or not orders.can_view(order, user, t):
        return error_page(404)
    e = html.escape
    if order["status"] not in orders.PAID_STATES and order["status"] != "refunded":
        return _doc("Receipt unavailable", f"<h1>No receipt yet</h1><p>Order #{e(order_id)} hasn't been paid, so there's no receipt for it.</p>")
    items = orders.get_items(conn, order_id)
    rows = "".join(
        f"<tr><td>{e(i['title'])}<div class='muted'>{e(i['variant_label'])}</div></td>"
        f"<td class='num'>{i['qty']}</td><td class='num'>{rupees(i['unit_price_paise'])}</td><td class='num'>{rupees(i['line_total_paise'])}</td></tr>"
        for i in items
    )
    extra = ""
    if order["discount_paise"]:
        extra += f"<tr><td colspan='3'>Discount ({e(order['coupon_code'] or '')})</td><td class='num'>-{rupees(order['discount_paise'])}</td></tr>"
    if order["requires_shipping"]:
        extra += f"<tr><td colspan='3'>Shipping</td><td class='num'>{rupees(order['shipping_paise']) if order['shipping_paise'] else 'Free'}</td></tr>"
    ship = ""
    if order["requires_shipping"]:
        ship = (f"<div><div class='muted'>Delivered to</div><strong>{e(order['ship_name'])}</strong><br>{e(order['ship_line1'])}"
                f"{'<br>' + e(order['ship_line2']) if order['ship_line2'] else ''}<br>{e(order['ship_city'])}, {e(order['ship_state'])} "
                f"{e(order['ship_pincode'])}</div>")
    refunded = f"<p><strong>Refunded on {_date(order['refunded_at'])}.</strong></p>" if order["status"] == "refunded" else ""
    body = f"""
<div class="row"><div><h1>Payment receipt</h1><div class="muted">{e(settings.store_name)} · WhatsApp +{e(settings.whatsapp_number)}</div></div>
<div style="text-align:right"><div class="muted">Order</div><strong>#{e(order_id)}</strong><div class="muted">Paid {_date(order['paid_at'])}</div></div></div>
{refunded}
<div class="row"><div><div class="muted">Billed to</div><strong>{e(order['customer_name'])}</strong><br>{e(order['email'])}<br>{e(order['phone'])}</div>{ship}</div>
<table><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Amount</th></tr></thead>
<tbody>{rows}{extra}<tr class="total"><td colspan="3">Total paid</td><td class="num">{rupees(order['total_paise'])}</td></tr></tbody></table>
<p class="muted" style="margin-top:22px">Payment reference: {e(order['gateway_payment_id'] or order['payment_reference'] or '—')}. This is a payment receipt, not a tax invoice.</p>"""
    return _doc(f"Receipt #{order_id}", body)


@router.get("/admin/orders/{order_id}/packing-slip")
def packing_slip(order_id: str, conn=Depends(get_db), user=Depends(current_user)):
    if not user or user["role"] not in ("staff", "admin"):
        return RedirectResponse("/admin", status_code=303)
    order = orders.get_order(conn, order_id)
    if not order:
        return error_page(404)
    e = html.escape
    items = [i for i in orders.get_items(conn, order_id) if i["kind"] in ("physical", "kit")]
    rows = "".join(f"<tr><td style='width:40px;font-size:18px'>☐</td><td>{e(i['title'])}<div class='muted'>{e(i['variant_label'])}</div></td>"
                   f"<td class='num' style='font-size:18px'>{i['qty']}</td></tr>" for i in items)
    body = f"""
<div class="row"><div><h1>Packing slip</h1><div class="muted">{e(settings.store_name)}</div></div>
<div style="text-align:right"><strong style="font-size:20px">#{e(order_id)}</strong><div class="muted">Ordered {_date(order['created_at'])}</div></div></div>
<div class="row"><div style="font-size:17px;line-height:1.5"><div class="muted">Ship to</div><strong>{e(order['ship_name'])}</strong><br>
{e(order['ship_line1'])}{'<br>' + e(order['ship_line2']) if order['ship_line2'] else ''}<br>{e(order['ship_city'])}, {e(order['ship_state'])}<br>
<strong>PIN {e(order['ship_pincode'])}</strong><br>Phone {e(order['ship_phone'])}</div></div>
<table><thead><tr><th></th><th>Item</th><th class="num">Qty</th></tr></thead><tbody>{rows}</tbody></table>
<p class="muted" style="margin-top:28px">Thank you for supporting handmade art. Questions: WhatsApp +{e(settings.whatsapp_number)}</p>"""
    return _doc(f"Packing slip #{order_id}", body)


# ---------------------------------------------------------------- development payment page

@router.get("/pay/mock/{gateway_order_id}")
def mock_gateway(gateway_order_id: str, conn=Depends(get_db)):
    if settings.is_production:
        return error_page(404)
    row = one(conn, "SELECT * FROM mock_payments WHERE gateway_order_id = ?", (gateway_order_id,))
    if not row:
        return error_page(404)
    e = html.escape
    done = row["status"] == "paid"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test payment · {e(row['order_id'])}</title>
<style>
 body{{margin:0;font-family:"Segoe UI",system-ui,sans-serif;background:#1C2B3A;color:#0F172A;display:grid;place-items:center;min-height:100vh;padding:16px}}
 .card{{background:#fff;border-radius:14px;max-width:420px;width:100%;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.35)}}
 .tag{{display:inline-block;background:#FEF3C7;color:#92400E;font-size:12px;font-weight:700;padding:4px 10px;border-radius:99px;letter-spacing:.03em}}
 h1{{font-size:20px;margin:14px 0 4px}} .amt{{font-size:34px;font-weight:800;margin:10px 0 2px;font-variant-numeric:tabular-nums}}
 .muted{{color:#64748B;font-size:13px}} button{{display:block;width:100%;border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:700;margin-top:10px;cursor:pointer}}
 .ok{{background:#15803D;color:#fff}} .bad{{background:#FEE2E2;color:#991B1B}} .lost{{background:#E0E7FF;color:#3730A3}} .back{{background:#F1F5F9;color:#334155}}
 button:focus-visible{{outline:3px solid #F59E0B;outline-offset:2px}}
</style></head><body><main class="card">
<span class="tag">TEST GATEWAY · NO REAL MONEY</span>
<h1>Rangdhara order #{e(row['order_id'])}</h1>
<div class="amt">{rupees(row['amount_paise'])}</div>
<p class="muted">This page stands in for Razorpay or Cashfree during development. Pick what the bank does.</p>
{'<p><strong>This test payment is already complete.</strong></p>' if done else ''}
<div id="actions" {'hidden' if done else ''}>
<button class="ok" data-outcome="paid">Payment succeeds</button>
<button class="bad" data-outcome="failed">Payment fails</button>
<button class="lost" data-outcome="paid_no_webhook">Succeeds, but the webhook is lost</button>
</div>
<button class="back" data-outcome="cancel">{'View order' if done else 'Cancel and return to the store'}</button>
<p class="muted" id="msg" role="status"></p>
</main>
<script>
document.querySelectorAll('button[data-outcome]').forEach(btn => btn.addEventListener('click', async () => {{
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  document.getElementById('msg').textContent = 'Processing…';
  const res = await fetch('/api/v1/payments/mock/{e(gateway_order_id)}/settle', {{
    method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Requested-With': 'rangdhaara'}},
    body: JSON.stringify({{outcome: btn.dataset.outcome}})
  }});
  const data = await res.json();
  if (data.redirect_url) location.href = data.redirect_url;
  else document.getElementById('msg').textContent = (data.error && data.error.message) || 'Something went wrong.';
}}));
</script></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- lesson video

@router.get("/media/lessons/{lesson_id}")
def lesson_video(lesson_id: int, u: str = "", e: str = "", s: str = "", conn=Depends(get_db)):
    if not playback_allowed(lesson_id, u, e, s):
        return error_page(403)
    lesson = one(conn, "SELECT l.video_key, l.is_preview, m.course_id FROM lessons l JOIN course_modules m ON m.id = l.module_id WHERE l.id = ?",
                 (lesson_id,))
    if not lesson or not lesson["video_key"]:
        return error_page(404)
    if not lesson["is_preview"]:
        viewer = one(conn, "SELECT role FROM users WHERE id = ?", (u,))
        if not viewer or (viewer["role"] == "customer" and not active_enrollment(conn, u, lesson["course_id"])):
            return error_page(403)  # access was revoked (e.g. refunded) after the link was issued
    path = resolve_key(lesson["video_key"])
    if not path.exists():
        return error_page(404)
    return FileResponse(path, media_type=VIDEO_TYPES.get(path.suffix.lower(), "video/mp4"),
                        headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline"})


def error_page(status: int) -> HTMLResponse:
    messages = {
        403: ("This link has expired", "Go back and open it again from your account."),
        404: ("We couldn't find that page", "The link may be old or mistyped."),
    }
    title, detail = messages.get(status, ("Something went wrong", "Please try again in a moment."))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Rangdhara</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#FAF6F0;color:#23201D;
font-family:"Segoe UI",system-ui,sans-serif;padding:20px;text-align:center}}h1{{font-family:Georgia,serif;font-size:28px;margin:0 0 8px}}
a{{color:#B85C38;font-weight:600}}</style></head><body><main><h1>{title}</h1><p>{detail}</p><p><a href="/">Back to the studio</a></p></main></body></html>"""
    return HTMLResponse(page, status_code=status)
