"""Transactional email: templates, a queue, and a delivery worker.

Requests never talk to SMTP. They add a row to email_outbox and return; the
background worker sends it (with retries). Without SMTP credentials in
development, messages are saved to data/outbox/ instead so nothing is lost.
"""
import html
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from .config import settings
from .db import all_rows, iso, iso_in
from .money import rupees
from .security import order_access_token

BRAND = "#B85C38"
INK = "#23201D"
PAPER = "#FAF6F0"
RULE = "#EADFD3"


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def link(path: str) -> str:
    return f"{settings.public_base_url}{path}"


def order_link(order_id: str) -> str:
    return link(f"/order/{order_id}?t={order_access_token(order_id)}")


def enqueue(conn, kind: str, to_email: str, subject: str, html_body: str, text_body: str = "") -> None:
    if not to_email:
        return
    now = iso()
    conn.execute(
        "INSERT INTO email_outbox (kind, to_email, subject, html, text_body, send_after, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, to_email, subject, html_body, text_body or _html_to_text(html_body), now, now),
    )


# ---------------------------------------------------------------- layout

def _layout(heading: str, body: str, preheader: str = "") -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{e(heading)}</title></head>
<body style="margin:0;background:{PAPER};font-family:Helvetica,Arial,sans-serif;color:{INK};">
<span style="display:none;max-height:0;overflow:hidden;">{e(preheader)}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid {RULE};border-radius:14px;">
<tr><td style="padding:22px 28px;border-bottom:1px solid {RULE};">
<div style="font-family:Georgia,serif;font-size:22px;font-weight:bold;color:{INK};">Rangdhara</div>
<div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:{BRAND};margin-top:2px;">Texture &amp; Clay Art Studio</div>
</td></tr>
<tr><td style="padding:26px 28px 8px;">
<h1 style="font-family:Georgia,serif;font-size:22px;line-height:1.3;margin:0 0 14px;color:{INK};">{e(heading)}</h1>
{body}
</td></tr>
<tr><td style="padding:18px 28px 24px;border-top:1px solid {RULE};font-size:12px;color:#7A6F66;line-height:1.6;">
Questions? WhatsApp us on +{e(settings.whatsapp_number)} or reply to this email.<br>
Instagram: @{e(settings.instagram_handle)}
</td></tr>
</table></td></tr></table></body></html>"""


def _p(text: str) -> str:
    return f'<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">{text}</p>'


def _button(label: str, url: str) -> str:
    return (
        f'<p style="margin:20px 0 22px;"><a href="{e(url)}" style="display:inline-block;background:{BRAND};'
        f'color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;padding:12px 22px;border-radius:10px;">'
        f"{e(label)}</a></p>"
    )


def _items_table(items: list[dict], order: dict) -> str:
    rows = "".join(
        f'<tr><td style="padding:9px 0;border-bottom:1px solid {RULE};font-size:14px;">{e(i["title"])}'
        + (f'<div style="font-size:12px;color:#7A6F66;">{e(i["variant_label"])}</div>' if i.get("variant_label") else "")
        + f'</td><td style="padding:9px 0;border-bottom:1px solid {RULE};font-size:14px;text-align:center;">×{i["qty"]}</td>'
        f'<td style="padding:9px 0;border-bottom:1px solid {RULE};font-size:14px;text-align:right;">{rupees(i["line_total_paise"])}</td></tr>'
        for i in items
    )
    totals = f'<tr><td colspan="2" style="padding:8px 0 2px;font-size:13px;color:#7A6F66;">Subtotal</td><td style="text-align:right;font-size:13px;">{rupees(order["subtotal_paise"])}</td></tr>'
    if order["discount_paise"]:
        totals += f'<tr><td colspan="2" style="padding:2px 0;font-size:13px;color:#2F6B4F;">Discount ({e(order["coupon_code"])})</td><td style="text-align:right;font-size:13px;color:#2F6B4F;">-{rupees(order["discount_paise"])}</td></tr>'
    if order["requires_shipping"]:
        shipping = rupees(order["shipping_paise"]) if order["shipping_paise"] else "Free"
        totals += f'<tr><td colspan="2" style="padding:2px 0;font-size:13px;color:#7A6F66;">Shipping</td><td style="text-align:right;font-size:13px;">{shipping}</td></tr>'
    totals += f'<tr><td colspan="2" style="padding:10px 0 0;font-size:15px;font-weight:bold;">Total</td><td style="padding-top:10px;text-align:right;font-size:15px;font-weight:bold;">{rupees(order["total_paise"])}</td></tr>'
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;">{rows}{totals}</table>'


def _address_block(order: dict) -> str:
    if not order["requires_shipping"]:
        return ""
    lines = [order["ship_name"], order["ship_line1"], order["ship_line2"],
             f'{order["ship_city"]}, {order["ship_state"]} {order["ship_pincode"]}', f'Phone {order["ship_phone"]}']
    inner = "<br>".join(e(x) for x in lines if x and x.strip())
    return (f'<div style="background:{PAPER};border:1px solid {RULE};border-radius:10px;padding:12px 14px;'
            f'font-size:13px;line-height:1.6;margin:0 0 16px;"><strong>Delivering to</strong><br>{inner}</div>')


def _delivery_line(order: dict) -> str:
    if not order["requires_shipping"] or not order.get("delivery_min_date"):
        return ""
    return _p(f'<strong>Estimated delivery:</strong> {e(_nice_date(order["delivery_min_date"]))} – '
              f'{e(_nice_date(order["delivery_max_date"]))}. We\'ll email you the tracking number when it ships.')


def _nice_date(value: str) -> str:
    from datetime import date

    try:
        return date.fromisoformat(value[:10]).strftime("%a %d %b")
    except ValueError:
        return value


def _html_to_text(markup: str) -> str:
    text = re.sub(r"<(br|/p|/tr|/h1|/div)[^>]*>", "\n", markup, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ---------------------------------------------------------------- account emails

OTP_SUBJECTS = {
    "login": "{code} is your Rangdhara sign-in code",
    "signup": "{code} is your Rangdhara verification code",
    "reset": "{code} is your Rangdhara password reset code",
}


def send_otp(conn, email: str, name: str, code: str, purpose: str) -> None:
    intro = {
        "login": "Use this code to sign in to your Rangdhara account.",
        "signup": "Use this code to confirm your email and finish creating your account.",
        "reset": "Use this code to set a new password for your Rangdhara account.",
    }[purpose]
    body = (
        _p(f"Hi {e(name) or 'there'},")
        + _p(intro)
        + f'<div style="font-family:Courier New,monospace;font-size:32px;font-weight:bold;letter-spacing:8px;'
        f'text-align:center;background:{PAPER};border:1px dashed {BRAND};border-radius:10px;padding:14px;margin:6px 0 18px;">{e(code)}</div>'
        + _p("The code expires in 10 minutes and works once. If you didn't ask for it, you can ignore this "
             "email — nobody can get into your account without the code.")
    )
    enqueue(conn, f"otp_{purpose}", email, OTP_SUBJECTS[purpose].format(code=code), _layout("Your one-time code", body, intro))
    if not settings.smtp_configured and not settings.is_production:
        print(f"\n  [dev] One-time {purpose} code for {email}: {code}\n", flush=True)


def send_signup_existing(conn, email: str, name: str) -> None:
    body = (
        _p(f"Hi {e(name) or 'there'},")
        + _p("Someone — hopefully you — just tried to create a new Rangdhara account with this email address. "
             "You already have one, so no new account was created.")
        + _button("Sign in instead", link("/?signin=1"))
        + _p("Forgotten your password? Choose <em>Email me a code</em> on the sign-in screen.")
    )
    enqueue(conn, "signup_existing", email, "You already have a Rangdhara account", _layout("You already have an account", body))


def send_welcome(conn, user: dict) -> None:
    body = (
        _p(f"Hi {e(user['full_name']) or 'there'},")
        + _p("Your account is ready. Your saved address fills in at checkout, and every order and course you buy "
             "shows up in one place.")
        + '<ul style="font-size:15px;line-height:1.7;padding-left:20px;margin:0 0 14px;">'
        "<li><strong>Ready-to-buy art</strong> — textured canvases and handcrafted clay decor, packed by hand.</li>"
        "<li><strong>DIY kits</strong> — everything you need to paint your own piece at home.</li>"
        "<li><strong>Rangdhara Art Academy</strong> — video masterclasses and live weekend workshops.</li></ul>"
        + _button("Visit the studio", link("/"))
    )
    enqueue(conn, "welcome", user["email"], "Welcome to Rangdhara", _layout("Welcome to Rangdhara", body))


# ---------------------------------------------------------------- order emails

def send_order_awaiting_payment(conn, order: dict, items: list[dict]) -> None:
    body = (
        _p(f"Hi {e(order['customer_name']) or 'there'},")
        + _p(f"We've received order <strong>#{e(order['id'])}</strong>. It isn't confirmed yet — please pay "
             f"<strong>{rupees(order['total_paise'])}</strong> by UPI to <strong>{e(settings.upi_vpa)}</strong> "
             f"and add <strong>{e(order['id'])}</strong> as the payment note.")
        + _p("We'll confirm your order as soon as we've matched the payment, usually within a few hours.")
        + _items_table(items, order)
        + _button("View payment details", order_link(order["id"]))
    )
    enqueue(conn, "order_awaiting_payment", order["email"], f"Order #{order['id']} received — payment pending",
            _layout("Complete your UPI payment", body))


def send_order_confirmed(conn, order: dict, items: list[dict], access: list[dict]) -> None:
    access_html = ""
    if access:
        rows = "".join(
            f'<li style="margin-bottom:6px;"><a href="{e(a["url"])}" style="color:{BRAND};font-weight:bold;">{e(a["label"])}</a>'
            + (f' — {e(a["note"])}' if a.get("note") else "") + "</li>"
            for a in access
        )
        access_html = (f'<div style="background:{PAPER};border:1px solid {RULE};border-radius:10px;padding:12px 16px;margin:0 0 16px;">'
                       f'<strong style="font-size:14px;">Your Academy access</strong>'
                       f'<ul style="font-size:14px;line-height:1.6;padding-left:18px;margin:8px 0 0;">{rows}</ul></div>')
    body = (
        _p(f"Hi {e(order['customer_name']) or 'there'},")
        + _p(f"Payment received — thank you. Order <strong>#{e(order['id'])}</strong> is confirmed."
             + (" We're preparing it now." if order["requires_shipping"] else ""))
        + access_html
        + _delivery_line(order)
        + _items_table(items, order)
        + _address_block(order)
        + _button("View order & receipt", order_link(order["id"]))
    )
    enqueue(conn, "order_confirmed", order["email"], f"Order #{order['id']} confirmed", _layout("Your order is confirmed", body))


def send_order_shipped(conn, order: dict) -> None:
    tracking = ""
    if order.get("awb"):
        tracking = _p(f"<strong>Courier:</strong> {e(order.get('courier') or '—')}<br><strong>Tracking number:</strong> "
                      f"<span style=\"font-family:Courier New,monospace;\">{e(order['awb'])}</span><br>"
                      "Track it on the courier's website with this number.")
    body = (
        _p(f"Hi {e(order['customer_name']) or 'there'},")
        + _p(f"Order <strong>#{e(order['id'])}</strong> is on its way.")
        + tracking
        + _delivery_line(order)
        + _button("Track your order", order_link(order["id"]))
    )
    enqueue(conn, "order_shipped", order["email"], f"Order #{order['id']} has shipped", _layout("Your order has shipped", body))


def send_order_delivered(conn, order: dict) -> None:
    body = (
        _p(f"Hi {e(order['customer_name']) or 'there'},")
        + _p(f"Order <strong>#{e(order['id'])}</strong> has been delivered. We hope it looks right at home.")
        + _p(f"If anything isn't right, WhatsApp us on +{e(settings.whatsapp_number)} with your order number and a photo.")
    )
    enqueue(conn, "order_delivered", order["email"], f"Order #{order['id']} delivered", _layout("Delivered", body))


def send_order_refunded(conn, order: dict, amount_paise: int) -> None:
    body = (
        _p(f"Hi {e(order['customer_name']) or 'there'},")
        + _p(f"We've issued a refund of <strong>{rupees(amount_paise)}</strong> for order <strong>#{e(order['id'])}</strong>.")
        + _p("It usually reaches your account in 5–7 business days, depending on your bank. Any course access "
             "or workshop seat from this order has been released.")
    )
    enqueue(conn, "order_refunded", order["email"], f"Refund issued for order #{order['id']}", _layout("Refund issued", body))


def send_abandoned_cart(conn, order: dict, items: list[dict], restore_url: str) -> None:
    body = (
        _p(f"Hi {e(order['customer_name']) or 'there'},")
        + _p("You left a few things in your cart. Handmade pieces are often one of a kind, so we can't hold them "
             "for long — here's your cart if you'd like to finish.")
        + _items_table(items, order)
        + _button("Return to your cart", restore_url)
    )
    enqueue(conn, "abandoned_cart", order["email"], "Your Rangdhara cart is waiting", _layout("Still thinking it over?", body))


def send_enrollment(conn, user: dict, course_title: str, course_id: int, reason: str = "purchase") -> None:
    intro = ("You've been given access to" if reason == "manual" else "You're enrolled in")
    body = (
        _p(f"Hi {e(user['full_name']) or 'there'},")
        + _p(f"{intro} <strong>{e(course_title)}</strong>. Your progress saves as you watch, so you can pick up "
             "exactly where you left off on any device.")
        + _button("Start learning", link(f"/my-courses/{course_id}"))
        + _p("Sign in with this email address — if you don't have a password yet, choose <em>Email me a code</em>.")
    )
    enqueue(conn, "enrollment", user["email"], f"You're enrolled: {course_title}", _layout("Welcome to the Academy", body))


def send_admin_new_order(conn, order: dict, items: list[dict]) -> None:
    if not settings.store_admin_email:
        return
    body = (
        _p(f"<strong>#{e(order['id'])}</strong> · {rupees(order['total_paise'])} · {e(order['customer_name'])} "
           f"({e(order['email'])}, {e(order['phone'])})")
        + _items_table(items, order)
        + _address_block(order)
        + _button("Open in admin", link(f"/admin#orders/{order['id']}"))
    )
    enqueue(conn, "admin_new_order", settings.store_admin_email, f"New order #{order['id']} · {rupees(order['total_paise'])}",
            _layout("New paid order", body))


def send_admin_alert(conn, subject: str, lines: list[str], order_id: str | None = None) -> None:
    if not settings.store_admin_email:
        return
    body = "".join(_p(e(line)) for line in lines)
    if order_id:
        body += _button("Review in admin", link(f"/admin#orders/{order_id}"))
    enqueue(conn, "admin_alert", settings.store_admin_email, f"Needs review: {subject}", _layout(subject, body))


# ---------------------------------------------------------------- delivery worker

def deliver_pending(conn, limit: int = 20) -> int:
    rows = all_rows(
        conn,
        "SELECT * FROM email_outbox WHERE status = 'queued' AND send_after <= ? ORDER BY id LIMIT ?",
        (iso(), limit),
    )
    if not rows:
        return 0
    smtp = None
    delivered = 0
    try:
        for row in rows:
            try:
                if settings.smtp_configured:
                    if smtp is None:
                        smtp = _smtp_connect()
                    smtp.send_message(_build_message(row))
                    status = "sent"
                else:
                    _save_locally(row)
                    status = "logged"
                conn.execute(
                    "UPDATE email_outbox SET status = ?, attempts = attempts + 1, sent_at = ?, last_error = NULL WHERE id = ?",
                    (status, iso(), row["id"]),
                )
                delivered += 1
            except Exception as exc:  # keep going; retry this one later with backoff
                attempts = row["attempts"] + 1
                if attempts >= 5:
                    conn.execute("UPDATE email_outbox SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
                                 (attempts, str(exc)[:500], row["id"]))
                else:
                    conn.execute("UPDATE email_outbox SET attempts = ?, last_error = ?, send_after = ? WHERE id = ?",
                                 (attempts, str(exc)[:500], iso_in(minutes=2 ** attempts), row["id"]))
                if smtp is not None:
                    try:
                        smtp.quit()
                    except Exception:
                        pass
                    smtp = None
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return delivered


def _smtp_connect():
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        smtp.starttls(context=context)
    smtp.login(settings.smtp_user, settings.smtp_password)
    return smtp


def _build_message(row: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = row["subject"]
    msg["From"] = settings.mail_from or settings.smtp_user
    msg["To"] = row["to_email"]
    msg["Message-ID"] = make_msgid(domain="rangdhaara")
    msg.set_content(row["text_body"] or _html_to_text(row["html"]))
    msg.add_alternative(row["html"], subtype="html")
    return msg


def _save_locally(row: dict) -> None:
    outbox = settings.data_dir / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    safe_kind = re.sub(r"[^a-z0-9_]+", "_", row["kind"].lower())
    (outbox / f"{row['id']:05d}-{safe_kind}.html").write_text(
        f"<!-- To: {e(row['to_email'])} | Subject: {e(row['subject'])} -->\n{row['html']}", encoding="utf-8"
    )
