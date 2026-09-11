"""The only place a price is decided.

The browser sends variant ids, quantities and a coupon code. Everything else —
unit prices, discounts, shipping, the total — is computed here from the
database, so nothing a customer edits in their browser can change what they pay.
"""
from .catalogue import available_qty
from .config import settings
from .db import all_rows, iso, one, scalar
from .shipping import estimate_delivery

MAX_QTY_PER_LINE = 10
DIGITAL_KINDS = {"course", "workshop"}
SHIPPABLE_KINDS = {"physical", "kit"}


def _normalise_items(items: list[dict]) -> list[tuple[int, int]]:
    merged: dict[int, int] = {}
    for raw in items[:50]:
        try:
            variant_id = int(raw.get("variant_id"))
            qty = int(raw.get("qty", 1))
        except (TypeError, ValueError, AttributeError):
            continue
        if variant_id <= 0 or qty <= 0:
            continue
        merged[variant_id] = merged.get(variant_id, 0) + qty
    return list(merged.items())


def price_cart(conn, items: list[dict], coupon_code: str | None = None, pincode: str | None = None,
               user_id: str | None = None) -> dict:
    lines = []
    for variant_id, qty in _normalise_items(items):
        row = one(
            conn,
            "SELECT v.id AS variant_id, v.label, v.price_paise, v.on_hand, v.is_active AS variant_active, "
            "p.id AS product_id, p.slug, p.kind, p.title, p.compare_at_paise, p.is_active AS product_active, "
            "w.starts_at, "
            "(SELECT url FROM product_media m WHERE m.product_id = p.id AND m.kind = 'image' ORDER BY m.position, m.id LIMIT 1) AS image "
            "FROM product_variants v JOIN products p ON p.id = v.product_id "
            "LEFT JOIN workshop_sessions w ON w.variant_id = v.id WHERE v.id = ?",
            (variant_id,),
        )
        if not row:
            continue  # unknown ids are dropped silently; the client re-syncs from the response
        if row["kind"] in DIGITAL_KINDS:
            qty = 1
        qty = min(qty, MAX_QTY_PER_LINE)
        issue = None
        available = available_qty(conn, {"id": row["variant_id"], "on_hand": row["on_hand"]})
        if not (row["variant_active"] and row["product_active"]):
            issue = ("unavailable", "This item is no longer available.")
        elif row["kind"] == "workshop" and (not row["starts_at"] or row["starts_at"] <= iso()):
            issue = ("session_started", "This workshop session has already started.")
        elif available is not None and available <= 0:
            issue = ("sold_out", "Sold out — someone got to it first.")
        elif available is not None and qty > available:
            issue = ("insufficient_stock", f"Only {available} left. Reduce the quantity to continue.")
        elif row["kind"] == "course" and user_id and _is_enrolled(conn, user_id, row["product_id"]):
            issue = ("already_enrolled", "You already own this course.")
        lines.append({
            "variant_id": row["variant_id"],
            "product_id": row["product_id"],
            "slug": row["slug"],
            "kind": row["kind"],
            "title": row["title"],
            "variant_label": row["label"],
            "image": row["image"] or "",
            "unit_price_paise": row["price_paise"],
            "compare_at_paise": row["compare_at_paise"],
            "qty": qty,
            "line_total_paise": row["price_paise"] * qty,
            "available": available,
            "starts_at": row["starts_at"],
            "issue": issue[0] if issue else None,
            "issue_message": issue[1] if issue else None,
        })

    priced = [line for line in lines if not line["issue"]]
    subtotal = sum(line["line_total_paise"] for line in priced)
    coupon = _apply_coupon(conn, coupon_code, subtotal) if coupon_code else None
    discount = coupon["discount_paise"] if coupon and coupon["applied"] else 0
    requires_shipping = any(line["kind"] in SHIPPABLE_KINDS for line in lines)
    shipping = 0
    if requires_shipping and settings.shipping_flat_paise:
        threshold = settings.free_shipping_threshold_paise
        shipping = 0 if threshold and subtotal - discount >= threshold else settings.shipping_flat_paise
    delivery = estimate_delivery(pincode) if requires_shipping and pincode else None

    return {
        "lines": lines,
        "item_count": sum(line["qty"] for line in lines),
        "subtotal_paise": subtotal,
        "discount_paise": discount,
        "shipping_paise": shipping,
        "total_paise": max(0, subtotal - discount) + shipping,
        "coupon": coupon,
        "requires_shipping": requires_shipping,
        "delivery": delivery,
        "ok": bool(lines) and all(not line["issue"] for line in lines),
    }


def _is_enrolled(conn, user_id: str, product_id: str) -> bool:
    return bool(scalar(
        conn,
        "SELECT 1 FROM enrollments e JOIN courses c ON c.id = e.course_id "
        "WHERE e.user_id = ? AND c.product_id = ? AND e.revoked_at IS NULL AND (e.expires_at IS NULL OR e.expires_at > ?)",
        (user_id, product_id, iso()),
    ))


def _apply_coupon(conn, code: str, subtotal: int) -> dict:
    code = (code or "").strip().upper()[:40]
    result = {"code": code, "applied": False, "discount_paise": 0, "message": ""}
    coupon = one(conn, "SELECT * FROM coupons WHERE code = ?", (code,))
    now = iso()
    if not coupon or not coupon["is_active"]:
        result["message"] = "That code isn't valid."
    elif coupon["starts_at"] and coupon["starts_at"] > now:
        result["message"] = "That code isn't active yet."
    elif coupon["ends_at"] and coupon["ends_at"] <= now:
        result["message"] = "That code has expired."
    elif coupon["usage_limit"] is not None and coupon["used_count"] >= coupon["usage_limit"]:
        result["message"] = "That code has been fully redeemed."
    elif subtotal <= 0:
        result["message"] = "Add an item to use this code."
    elif subtotal < coupon["min_subtotal_paise"]:
        from .money import rupees

        result["message"] = f"Spend {rupees(coupon['min_subtotal_paise'])} or more to use this code."
    else:
        if coupon["kind"] == "percent":
            discount = subtotal * min(coupon["value"], 100) // 100
        else:
            discount = coupon["value"]
        if coupon["max_discount_paise"]:
            discount = min(discount, coupon["max_discount_paise"])
        discount = min(discount, subtotal)
        label = f"{coupon['value']}% off" if coupon["kind"] == "percent" else "Flat discount"
        result.update(applied=True, discount_paise=discount, message=f"{label} applied.")
    return result
