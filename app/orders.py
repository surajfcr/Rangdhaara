"""Order lifecycle: creation with stock holds, the status state machine,
payment confirmation, fulfilment, refunds and housekeeping.

Rules this module enforces:
  * An order is only ever `paid` through mark_paid(), which the payment
    service calls after the gateway itself confirms the money.
  * Payment is committed before fulfilment runs, so a fulfilment bug can
    never lose the record that a customer paid.
  * Every status change is written to order_events.
"""
import secrets
import traceback
from datetime import datetime

from . import emails
from .academy import grant_enrollment, revoke_enrollment
from .config import settings
from .db import all_rows, iso, iso_in, jloads, one, scalar, transaction
from .errors import bad_request, conflict
from .money import rupees
from .pricing import price_cart
from .security import check_order_token, clean_text, is_email, is_phone, is_pincode, normalize_email, normalize_phone
from .shipping import IST, estimate_delivery
from .users import add_address, create_user, phone_taken

STATUS_LABELS = {
    "pending_payment": "Awaiting payment",
    "paid": "Paid",
    "packed": "Packed",
    "shipped": "Shipped",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "refunded": "Refunded",
    "expired": "Payment not completed",
}

TRANSITIONS = {
    "pending_payment": {"paid", "cancelled", "expired"},
    "expired": {"paid", "cancelled"},
    "paid": {"packed", "shipped", "delivered", "refunded"},
    "packed": {"shipped", "delivered", "refunded"},
    "shipped": {"delivered", "refunded"},
    "delivered": {"refunded"},
    "cancelled": set(),
    "refunded": set(),
}
PAID_STATES = {"paid", "packed", "shipped", "delivered"}
_TIMESTAMP = {s: f"{s}_at" for s in ("paid", "packed", "shipped", "delivered", "cancelled", "refunded", "expired")}

# Manual UPI needs the customer to leave the site and the studio to check the bank, so it holds stock longer.
HOLD_MINUTES = {"manual_upi": 12 * 60}
EXPIRE_AFTER_MINUTES = {"manual_upi": 48 * 60}
DEFAULT_EXPIRE_AFTER_MINUTES = 60

_ID_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_order_id(conn) -> str:
    stamp = datetime.now(IST).strftime("%y%m%d")
    for _ in range(20):
        order_id = f"RANG-{stamp}-{''.join(secrets.choice(_ID_ALPHABET) for _ in range(5))}"
        if not scalar(conn, "SELECT 1 FROM orders WHERE id = ?", (order_id,)):
            return order_id
    raise RuntimeError("Could not allocate a unique order id")


def get_order(conn, order_id: str) -> dict | None:
    return one(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))


def get_items(conn, order_id: str) -> list[dict]:
    return all_rows(conn, "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,))


def can_view(order: dict, user: dict | None, token: str | None) -> bool:
    if user and (user["role"] in ("staff", "admin") or order["user_id"] == user["id"]):
        return True
    if user and order["email"] and normalize_email(user["email"]) == normalize_email(order["email"]):
        return True
    return check_order_token(order["id"], token)


def transition(conn, order: dict, to_status: str, *, actor_id: str | None = None, note: str | None = None, **fields) -> dict:
    current = order["status"]
    if to_status not in TRANSITIONS[current]:
        raise conflict(
            f"An order that is “{STATUS_LABELS[current]}” can't be marked “{STATUS_LABELS[to_status]}”.",
            code="invalid_transition",
        )
    now = iso()
    updates = {"status": to_status, "updated_at": now, **fields}
    stamp = _TIMESTAMP.get(to_status)
    if stamp and stamp not in fields:
        updates[stamp] = now
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE orders SET {assignments} WHERE id = ?", (*updates.values(), order["id"]))
    conn.execute(
        "INSERT INTO order_events (order_id, from_status, to_status, actor_user_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (order["id"], current, to_status, actor_id, note, now),
    )
    order.update(updates)
    return order


def add_note(conn, order_id: str, note: str, actor_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO order_events (order_id, from_status, to_status, actor_user_id, note, created_at) VALUES (?, NULL, NULL, ?, ?, ?)",
        (order_id, actor_id, note[:500], iso()),
    )


# ---------------------------------------------------------------- creation

def validate_contact(contact: dict) -> dict:
    email = normalize_email(contact.get("email"))
    phone = normalize_phone(contact.get("phone"))
    name = clean_text(contact.get("name"), 80)
    fields = {}
    if not name:
        fields["contact.name"] = "Enter your name."
    if not is_email(email):
        fields["contact.email"] = "Enter a valid email address — your receipt goes here."
    if not is_phone(phone):
        fields["contact.phone"] = "Enter a 10-digit Indian mobile number."
    if fields:
        raise bad_request("Check your contact details.", fields=fields)
    return {"name": name, "email": email, "phone": phone}


def validate_address(address: dict | None) -> dict:
    address = address or {}
    clean = {
        "full_name": clean_text(address.get("full_name"), 80),
        "phone": normalize_phone(address.get("phone")),
        "line1": clean_text(address.get("line1"), 160),
        "line2": clean_text(address.get("line2"), 160),
        "city": clean_text(address.get("city"), 60),
        "state": clean_text(address.get("state"), 60),
        "pincode": (address.get("pincode") or "").strip(),
    }
    fields = {}
    if not clean["full_name"]:
        fields["address.full_name"] = "Enter the name to deliver to."
    if not is_phone(clean["phone"]):
        fields["address.phone"] = "Enter a 10-digit mobile number for the courier."
    if len(clean["line1"]) < 5:
        fields["address.line1"] = "Enter the house or flat number and street."
    if not clean["city"]:
        fields["address.city"] = "Enter the city."
    if not clean["state"]:
        fields["address.state"] = "Choose the state."
    if not is_pincode(clean["pincode"]):
        fields["address.pincode"] = "Enter a valid 6-digit PIN code."
    if fields:
        raise bad_request("Check the delivery address.", fields=fields)
    return clean


def create_order(conn, *, items: list[dict], coupon_code: str | None, contact: dict, address: dict | None,
                 user: dict | None, marketing_opt_in: bool, provider_name: str) -> tuple[dict, dict]:
    contact = validate_contact(contact)
    with transaction(conn):
        pricing = price_cart(conn, items, coupon_code, (address or {}).get("pincode"), user["id"] if user else None)
        if not pricing["lines"]:
            raise bad_request("Your cart is empty.", code="empty_cart")
        if not pricing["ok"]:
            raise conflict("Something in your cart changed. Review it and try again.", code="cart_changed", pricing=pricing)
        if coupon_code and pricing["coupon"] and not pricing["coupon"]["applied"]:
            raise conflict(pricing["coupon"]["message"], code="coupon_invalid", pricing=pricing)

        ship = {k: "" for k in ("full_name", "phone", "line1", "line2", "city", "state", "pincode")}
        delivery = None
        if pricing["requires_shipping"]:
            ship = validate_address(address)
            delivery = estimate_delivery(ship["pincode"])

        order_id = new_order_id(conn)
        now = iso()
        conn.execute(
            "INSERT INTO orders (id, user_id, email, phone, customer_name, requires_shipping, ship_name, ship_phone, "
            "ship_line1, ship_line2, ship_city, ship_state, ship_pincode, subtotal_paise, discount_paise, shipping_paise, "
            "total_paise, coupon_code, status, payment_provider, delivery_min_date, delivery_max_date, marketing_opt_in, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', ?, ?, ?, ?, ?, ?)",
            (
                order_id, user["id"] if user else None, contact["email"], contact["phone"], contact["name"],
                1 if pricing["requires_shipping"] else 0, ship["full_name"], ship["phone"], ship["line1"], ship["line2"],
                ship["city"], ship["state"], ship["pincode"], pricing["subtotal_paise"], pricing["discount_paise"],
                pricing["shipping_paise"], pricing["total_paise"],
                pricing["coupon"]["code"] if pricing["coupon"] and pricing["coupon"]["applied"] else None,
                provider_name, delivery["min_date"] if delivery else None, delivery["max_date"] if delivery else None,
                1 if marketing_opt_in else 0, now, now,
            ),
        )
        hold_until = iso_in(minutes=HOLD_MINUTES.get(provider_name, settings.reservation_minutes))
        for line in pricing["lines"]:
            conn.execute(
                "INSERT INTO order_items (order_id, product_id, variant_id, kind, title, variant_label, image, "
                "unit_price_paise, qty, line_total_paise) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, line["product_id"], line["variant_id"], line["kind"], line["title"], line["variant_label"],
                 line["image"], line["unit_price_paise"], line["qty"], line["line_total_paise"]),
            )
            if line["available"] is not None:
                conn.execute(
                    "INSERT INTO stock_reservations (order_id, variant_id, qty, status, expires_at, created_at) "
                    "VALUES (?, ?, ?, 'active', ?, ?)",
                    (order_id, line["variant_id"], line["qty"], hold_until, now),
                )
        conn.execute(
            "INSERT INTO order_events (order_id, from_status, to_status, note, created_at) VALUES (?, NULL, 'pending_payment', ?, ?)",
            (order_id, "Checkout started", now),
        )
        if user and pricing["requires_shipping"] and address and address.get("save"):
            add_address(conn, user["id"], ship)
    return get_order(conn, order_id), pricing


# ---------------------------------------------------------------- payment and fulfilment

def mark_paid(conn, order_id: str, *, payment_id: str | None = None, amount_paise: int | None = None,
              source: str = "gateway", actor: dict | None = None, reference: str | None = None) -> str:
    alert = None
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order:
            return "not_found"
        if order["status"] in PAID_STATES:
            return "already_paid"
        if order["status"] in ("cancelled", "refunded"):
            conn.execute("UPDATE orders SET needs_review = 'payment_after_close', gateway_payment_id = COALESCE(?, gateway_payment_id), "
                         "updated_at = ? WHERE id = ?", (payment_id, iso(), order_id))
            add_note(conn, order_id, f"Payment {payment_id or ''} arrived after the order was {order['status']}.")
            alert = (f"Payment for closed order #{order_id}",
                     [f"The gateway reports payment {payment_id or '(no id)'} for an order that is {order['status']}.",
                      "Refund the customer from the gateway dashboard or reopen the sale manually."])
            outcome = "closed_order"
        elif amount_paise is not None and amount_paise != order["total_paise"]:
            conn.execute("UPDATE orders SET needs_review = 'amount_mismatch', gateway_payment_id = COALESCE(?, gateway_payment_id), "
                         "updated_at = ? WHERE id = ?", (payment_id, iso(), order_id))
            add_note(conn, order_id, f"Amount mismatch: gateway {rupees(amount_paise)}, order {rupees(order['total_paise'])}.")
            alert = (f"Amount mismatch on #{order_id}",
                     [f"Gateway says {rupees(amount_paise)} was paid; the order total is {rupees(order['total_paise'])}.",
                      "The order was not fulfilled. Check the payment before doing anything else."])
            outcome = "amount_mismatch"
        else:
            note = f"Payment confirmed via {source}" + (f" · ref {reference}" if reference else "")
            transition(conn, order, "paid", actor_id=actor["id"] if actor else None, note=note,
                       gateway_payment_id=payment_id or order["gateway_payment_id"],
                       payment_reference=reference or order["payment_reference"], needs_review=None)
            oversold = _commit_stock(conn, order)
            if order["coupon_code"]:
                conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (order["coupon_code"],))
            if oversold:
                conn.execute("UPDATE orders SET needs_review = 'oversold' WHERE id = ?", (order_id,))
                alert = (f"Oversold on #{order_id}",
                         [f"Paid after its stock hold lapsed, and there wasn't enough stock for: {', '.join(oversold)}.",
                          "Contact the customer about a remake date or a refund."])
            outcome = "paid"
        if alert:
            emails.send_admin_alert(conn, alert[0], alert[1], order_id)
    if outcome == "paid":
        fulfil_order(conn, order_id)
    return outcome


def _commit_stock(conn, order: dict) -> list[str]:
    """Turn this order's holds into real stock movement. Returns titles that went below zero."""
    oversold = []
    lines = all_rows(
        conn,
        "SELECT oi.variant_id, oi.title, SUM(oi.qty) AS qty, v.on_hand FROM order_items oi "
        "JOIN product_variants v ON v.id = oi.variant_id WHERE oi.order_id = ? AND v.on_hand IS NOT NULL "
        "GROUP BY oi.variant_id",
        (order["id"],),
    )
    for line in lines:
        conn.execute("UPDATE stock_reservations SET status = 'converted' WHERE order_id = ? AND variant_id = ? AND status = 'active'",
                     (order["id"], line["variant_id"]))
        remaining = line["on_hand"] - line["qty"]
        if remaining < 0:
            oversold.append(line["title"])
            remaining = 0
        conn.execute("UPDATE product_variants SET on_hand = ?, updated_at = ? WHERE id = ?", (remaining, iso(), line["variant_id"]))
    return oversold


def _customer_for(conn, order: dict) -> dict:
    if order["user_id"]:
        user = one(conn, "SELECT * FROM users WHERE id = ?", (order["user_id"],))
        if user:
            return user
    user = one(conn, "SELECT * FROM users WHERE email = ?", (order["email"],))
    if user:
        if not user["phone"] and order["phone"] and not phone_taken(conn, order["phone"]):
            conn.execute("UPDATE users SET phone = ?, updated_at = ? WHERE id = ?", (order["phone"], iso(), user["id"]))
        return user
    phone = order["phone"] if not phone_taken(conn, order["phone"]) else None
    user = create_user(conn, email=order["email"], full_name=order["customer_name"], phone=phone,
                       marketing=bool(order["marketing_opt_in"]))
    return user


def fulfil_order(conn, order_id: str) -> None:
    try:
        with transaction(conn):
            order = get_order(conn, order_id)
            if not order or order["fulfilled_at"] or order["status"] not in PAID_STATES:
                return
            items = get_items(conn, order_id)
            user = _customer_for(conn, order)
            if order["requires_shipping"] and order["ship_line1"]:
                add_address(conn, user["id"], {
                    "full_name": order["ship_name"], "phone": order["ship_phone"], "line1": order["ship_line1"],
                    "line2": order["ship_line2"], "city": order["ship_city"], "state": order["ship_state"],
                    "pincode": order["ship_pincode"],
                })
            access = []
            for item in items:
                if item["kind"] == "course":
                    course = one(conn, "SELECT c.id, p.title FROM courses c JOIN products p ON p.id = c.product_id WHERE c.product_id = ?",
                                 (item["product_id"],))
                    if not course:
                        raise RuntimeError(f"Course record missing for product {item['product_id']}")
                    _, created = grant_enrollment(conn, user["id"], course["id"], order_id=order_id, source="purchase")
                    if not created:
                        add_note(conn, order_id, f"Customer already had access to {course['title']}.")
                    access.append({"label": f"Start “{course['title']}”", "url": emails.link(f"/my-courses/{course['id']}")})
                elif item["kind"] == "workshop":
                    conn.execute(
                        "INSERT INTO workshop_bookings (variant_id, user_id, order_id, seats, status, created_at) VALUES (?, ?, ?, ?, 'confirmed', ?)",
                        (item["variant_id"], user["id"], order_id, item["qty"], iso()),
                    )
                    access.append({"label": f"{item['title']} · {item['variant_label']}", "url": emails.link("/my-courses"),
                                   "note": "your join link appears in My Courses"})
            conn.execute("UPDATE orders SET user_id = ?, fulfilled_at = ?, fulfilment_error = NULL, updated_at = ? WHERE id = ?",
                         (user["id"], iso(), iso(), order_id))
            order["user_id"] = user["id"]
            if not order["requires_shipping"]:
                transition(conn, order, "delivered", note="Digital access granted")
            emails.send_order_confirmed(conn, order, items, access)
            emails.send_admin_new_order(conn, order, items)
    except Exception as exc:
        traceback.print_exc()
        conn.execute("UPDATE orders SET fulfilment_error = ?, needs_review = 'fulfilment_failed', updated_at = ? WHERE id = ?",
                     (str(exc)[:500], iso(), order_id))
        emails.send_admin_alert(conn, f"Fulfilment failed for #{order_id}",
                                ["The payment is recorded, but access or confirmation could not be completed.", str(exc)[:300]],
                                order_id)


def retry_fulfilment(conn, order_id: str, actor: dict) -> None:
    conn.execute("UPDATE orders SET fulfilled_at = NULL WHERE id = ? AND fulfilment_error IS NOT NULL", (order_id,))
    add_note(conn, order_id, "Fulfilment retried from admin", actor["id"])
    fulfil_order(conn, order_id)


# ---------------------------------------------------------------- admin actions

def advance_status(conn, order_id: str, to_status: str, actor: dict, *, courier: str | None = None,
                   awb: str | None = None, note: str | None = None) -> dict:
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order:
            raise bad_request("Order not found.", code="not_found")
        if to_status not in ("packed", "shipped", "delivered"):
            raise bad_request("Use refund or cancel for that change.")
        fields = {}
        if to_status == "shipped":
            courier = clean_text(courier, 40)
            awb = clean_text(awb, 40)
            if not awb:
                raise bad_request("Add the courier tracking number before marking it shipped.", fields={"awb": "Required"})
            fields.update(courier=courier or None, awb=awb)
        if not order["requires_shipping"] and to_status in ("packed", "shipped"):
            raise bad_request("This order has nothing to ship.")
        transition(conn, order, to_status, actor_id=actor["id"], note=note, **fields)
        if to_status == "shipped":
            emails.send_order_shipped(conn, order)
        elif to_status == "delivered" and order["requires_shipping"]:
            emails.send_order_delivered(conn, order)
    return get_order(conn, order_id)


def update_tracking(conn, order_id: str, courier: str | None, awb: str | None, actor: dict) -> None:
    conn.execute("UPDATE orders SET courier = ?, awb = ?, updated_at = ? WHERE id = ?",
                 (clean_text(courier, 40) or None, clean_text(awb, 40) or None, iso(), order_id))
    add_note(conn, order_id, f"Tracking updated: {courier or '—'} {awb or ''}".strip(), actor["id"])


def cancel_order(conn, order_id: str, actor: dict | None, note: str) -> dict:
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order:
            raise bad_request("Order not found.", code="not_found")
        transition(conn, order, "cancelled", actor_id=actor["id"] if actor else None, note=note or "Cancelled")
        conn.execute("UPDATE stock_reservations SET status = 'released' WHERE order_id = ? AND status = 'active'", (order_id,))
    return get_order(conn, order_id)


def refund_order(conn, order_id: str, *, actor: dict, note: str, via_gateway: bool, restock: bool) -> dict:
    order = get_order(conn, order_id)
    if not order:
        raise bad_request("Order not found.", code="not_found")
    if order["status"] not in PAID_STATES:
        raise conflict("Only paid orders can be refunded.", code="invalid_transition")
    refund_ref = None
    if via_gateway:
        from .payments import PaymentError, get_provider

        provider = get_provider(order["payment_provider"])
        if not provider.refunds or not order["gateway_payment_id"]:
            raise bad_request("This payment can't be refunded through the gateway. Refund the customer directly, "
                              "then record the refund here with the gateway option turned off.")
        try:
            refund_ref = provider.refund(conn, order, order["total_paise"], note or "Refund")
        except PaymentError as exc:
            raise bad_request(f"The gateway refused the refund: {exc}", code="gateway_error")
    with transaction(conn):
        order = get_order(conn, order_id)
        transition(conn, order, "refunded", actor_id=actor["id"],
                   note=f"Refunded {rupees(order['total_paise'])}" + (f" · gateway ref {refund_ref}" if refund_ref else " · recorded manually")
                   + (f" · {note}" if note else ""))
        for item in get_items(conn, order_id):
            variant = one(conn, "SELECT id, on_hand FROM product_variants WHERE id = ?", (item["variant_id"],)) if item["variant_id"] else None
            if variant and variant["on_hand"] is not None and (restock or item["kind"] == "workshop"):
                conn.execute("UPDATE product_variants SET on_hand = on_hand + ?, updated_at = ? WHERE id = ?",
                             (item["qty"], iso(), variant["id"]))
        for enrollment in all_rows(conn, "SELECT id FROM enrollments WHERE order_id = ? AND revoked_at IS NULL", (order_id,)):
            revoke_enrollment(conn, enrollment["id"], "Order refunded")
        conn.execute("UPDATE workshop_bookings SET status = 'cancelled' WHERE order_id = ?", (order_id,))
        emails.send_order_refunded(conn, order, order["total_paise"])
    return get_order(conn, order_id)


# ---------------------------------------------------------------- housekeeping

def release_expired_holds(conn) -> int:
    cur = conn.execute("UPDATE stock_reservations SET status = 'released' WHERE status = 'active' AND expires_at <= ?", (iso(),))
    return cur.rowcount


def expire_unpaid(conn) -> int:
    count = 0
    for order in all_rows(conn, "SELECT * FROM orders WHERE status = 'pending_payment' AND is_legacy = 0"):
        minutes = EXPIRE_AFTER_MINUTES.get(order["payment_provider"], DEFAULT_EXPIRE_AFTER_MINUTES)
        if order["created_at"] > iso_in(minutes=-minutes):
            continue
        with transaction(conn):
            fresh = get_order(conn, order["id"])
            if fresh["status"] != "pending_payment":
                continue
            transition(conn, fresh, "expired", note="Payment window closed")
            conn.execute("UPDATE stock_reservations SET status = 'released' WHERE order_id = ? AND status = 'active'", (order["id"],))
            count += 1
    return count


def send_abandoned_cart_emails(conn) -> int:
    rows = all_rows(
        conn,
        "SELECT * FROM orders o WHERE o.status IN ('pending_payment', 'expired') AND o.is_legacy = 0 "
        "AND o.marketing_opt_in = 1 AND o.abandoned_email_sent_at IS NULL AND o.created_at <= ? AND o.created_at >= ? "
        "AND NOT EXISTS (SELECT 1 FROM orders p WHERE p.email = o.email AND p.created_at >= o.created_at "
        "AND p.status IN ('paid', 'packed', 'shipped', 'delivered'))",
        (iso_in(hours=-4), iso_in(hours=-48)),
    )
    for order in rows:
        items = get_items(conn, order["id"])
        restore = ",".join(f"{i['variant_id']}x{i['qty']}" for i in items if i["variant_id"])
        if not restore:
            continue
        with transaction(conn):
            emails.send_abandoned_cart(conn, order, items, emails.link(f"/?cart={restore}"))
            conn.execute("UPDATE orders SET abandoned_email_sent_at = ? WHERE id = ?", (iso(), order["id"]))
    return len(rows)


# ---------------------------------------------------------------- serialisation

def serialize(conn, order: dict, *, admin: bool = False) -> dict:
    items = get_items(conn, order["id"])
    holds_active = bool(scalar(conn, "SELECT 1 FROM stock_reservations WHERE order_id = ? AND status = 'active' AND expires_at > ?",
                               (order["id"], iso())))
    tracked = bool(scalar(conn, "SELECT 1 FROM stock_reservations WHERE order_id = ?", (order["id"],)))
    can_pay = order["status"] == "pending_payment" and not order["is_legacy"] and (holds_active or not tracked)
    data = {
        "id": order["id"],
        "status": order["status"],
        "status_label": STATUS_LABELS[order["status"]],
        "created_at": order["created_at"],
        "paid_at": order["paid_at"],
        "packed_at": order["packed_at"],
        "shipped_at": order["shipped_at"],
        "delivered_at": order["delivered_at"],
        "cancelled_at": order["cancelled_at"],
        "refunded_at": order["refunded_at"],
        "expired_at": order["expired_at"],
        "customer": {"name": order["customer_name"], "email": order["email"], "phone": order["phone"]},
        "requires_shipping": bool(order["requires_shipping"]),
        "shipping_address": {
            "full_name": order["ship_name"], "phone": order["ship_phone"], "line1": order["ship_line1"],
            "line2": order["ship_line2"], "city": order["ship_city"], "state": order["ship_state"],
            "pincode": order["ship_pincode"],
        } if order["requires_shipping"] else None,
        "items": [{k: i[k] for k in ("product_id", "variant_id", "kind", "title", "variant_label", "image",
                                      "unit_price_paise", "qty", "line_total_paise")} for i in items],
        "subtotal_paise": order["subtotal_paise"],
        "discount_paise": order["discount_paise"],
        "shipping_paise": order["shipping_paise"],
        "total_paise": order["total_paise"],
        "coupon_code": order["coupon_code"],
        "delivery": {"min_date": order["delivery_min_date"], "max_date": order["delivery_max_date"]}
        if order["delivery_min_date"] else None,
        "courier": order["courier"],
        "awb": order["awb"],
        "payment": {
            "provider": order["payment_provider"],
            "can_pay": can_pay,
            "client": jloads(order["gateway_client"], {}) if can_pay else None,
        },
        "restore_cart": ",".join(f"{i['variant_id']}x{i['qty']}" for i in items if i["variant_id"])
        if order["status"] in ("pending_payment", "expired", "cancelled") else None,
        "access": _access_for(conn, order) if order["status"] in PAID_STATES else [],
        "is_legacy": bool(order["is_legacy"]),
    }
    events = all_rows(
        conn,
        "SELECT e.*, u.email AS actor_email FROM order_events e LEFT JOIN users u ON u.id = e.actor_user_id "
        "WHERE e.order_id = ? ORDER BY e.id",
        (order["id"],),
    )
    if admin:
        data.update({
            "user_id": order["user_id"],
            "needs_review": order["needs_review"],
            "fulfilment_error": order["fulfilment_error"],
            "gateway_order_id": order["gateway_order_id"],
            "gateway_payment_id": order["gateway_payment_id"],
            "payment_reference": order["payment_reference"],
            "marketing_opt_in": bool(order["marketing_opt_in"]),
            "events": [{k: e[k] for k in ("from_status", "to_status", "note", "actor_email", "created_at")} for e in events],
            "payment_events": all_rows(
                conn,
                "SELECT provider, event_type, state, amount_paise, signature_valid, outcome, error, received_at "
                "FROM payment_events WHERE order_id = ? ORDER BY id",
                (order["id"],),
            ),
        })
    else:
        data["events"] = [{"to_status": e["to_status"], "created_at": e["created_at"]} for e in events if e["to_status"]]
    return data


def _access_for(conn, order: dict) -> list[dict]:
    access = [
        {"type": "course", "course_id": r["course_id"], "title": r["title"], "url": f"/my-courses/{r['course_id']}",
         "active": r["revoked_at"] is None}
        for r in all_rows(conn, "SELECT e.course_id, e.revoked_at, p.title FROM enrollments e JOIN courses c ON c.id = e.course_id "
                                "JOIN products p ON p.id = c.product_id WHERE e.order_id = ?", (order["id"],))
    ]
    for r in all_rows(conn, "SELECT b.status, w.starts_at, w.duration_min, p.title, v.label FROM workshop_bookings b "
                            "JOIN workshop_sessions w ON w.variant_id = b.variant_id JOIN product_variants v ON v.id = b.variant_id "
                            "JOIN products p ON p.id = v.product_id WHERE b.order_id = ?", (order["id"],)):
        access.append({"type": "workshop", "title": r["title"], "label": r["label"], "starts_at": r["starts_at"],
                       "duration_min": r["duration_min"], "url": "/my-courses", "active": r["status"] == "confirmed"})
    return access
