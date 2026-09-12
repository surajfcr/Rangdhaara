"""Admin API.

Staff accounts run fulfilment (orders, packing, tracking, stock counts).
Only the owner's admin account can change prices, money, course content,
student access and roles. Every write lands in the audit log.
"""
import csv
import io
import json
import re
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import audit, emails, orders
from ..academy import active_enrollment, course_with_product, grant_enrollment, revoke_enrollment, summary
from ..auth import client_ip, require_admin, require_staff
from ..catalogue import available_qty, list_products, serialize_product
from ..config import settings
from ..db import all_rows, get_db, iso, iso_in, jloads, kv_get, one, parse_iso, scalar, transaction, utcnow
from ..errors import ApiError, bad_request, conflict, forbidden, not_found
from ..jobs import TASKS
from ..payments import get_provider
from ..payments.service import confirm_with_gateway
from ..security import clean_text, is_email, normalize_email, random_token
from ..shipping import IST
from ..storage import RESOURCE_TYPES, VIDEO_TYPES, delete_private, delete_public_upload, save_private, save_public_image
from ..users import create_user

router = APIRouter()
MB = 1024 * 1024


def _audit(conn, request: Request, actor: dict, action: str, entity: str, entity_id=None, detail=None) -> None:
    audit.record(conn, actor, action, entity, entity_id, detail, client_ip(request))


def _ist_day_start(days_ago: int = 0) -> datetime:
    now = datetime.now(IST)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)


# ================================================================ Today

@router.get("/overview")
def overview(user=Depends(require_staff), conn=Depends(get_db)):
    today = iso(_ist_day_start())
    pending = one(conn, "SELECT COUNT(*) AS count, MIN(created_at) AS oldest FROM orders WHERE status = 'pending_payment' AND is_legacy = 0")
    low_stock = []
    for v in all_rows(conn, "SELECT v.id, v.label, v.on_hand, p.id AS product_id, p.title FROM product_variants v "
                            "JOIN products p ON p.id = v.product_id WHERE v.on_hand IS NOT NULL AND v.is_active = 1 "
                            "AND p.is_active = 1 AND p.kind IN ('physical', 'kit') ORDER BY v.on_hand"):
        available = available_qty(conn, v)
        if available <= 2:
            low_stock.append({"product_id": v["product_id"], "title": v["title"], "label": v["label"], "available": available})
    next_session = one(
        conn,
        "SELECT w.starts_at, w.seats_total, v.id AS variant_id, v.label, p.title, "
        "(SELECT COALESCE(SUM(seats), 0) FROM workshop_bookings b WHERE b.variant_id = v.id AND b.status = 'confirmed') AS seats_taken "
        "FROM workshop_sessions w JOIN product_variants v ON v.id = w.variant_id JOIN products p ON p.id = v.product_id "
        "WHERE w.starts_at > ? AND v.is_active = 1 ORDER BY w.starts_at LIMIT 1",
        (iso(),),
    )
    paid_today = one(conn, "SELECT COUNT(*) AS count, COALESCE(SUM(total_paise), 0) AS total FROM orders "
                           "WHERE paid_at >= ? AND status != 'refunded'", (today,))
    return {
        "actions": {
            "to_pack": scalar(conn, "SELECT COUNT(*) FROM orders WHERE status = 'paid' AND requires_shipping = 1"),
            "to_ship": scalar(conn, "SELECT COUNT(*) FROM orders WHERE status = 'packed'"),
            "needs_review": scalar(conn, "SELECT COUNT(*) FROM orders WHERE needs_review IS NOT NULL AND status != 'cancelled'"),
            "stuck_payments": scalar(conn, "SELECT COUNT(*) FROM orders WHERE status = 'pending_payment' AND is_legacy = 0 "
                                           "AND (payment_provider = 'manual_upi' OR created_at <= ?)", (iso_in(minutes=-20),)),
        },
        "paid_today": paid_today,
        "pending_payment": {
            "count": pending["count"],
            "oldest_minutes": int((utcnow() - parse_iso(pending["oldest"])).total_seconds() // 60) if pending["oldest"] else None,
        },
        "low_stock": low_stock[:12],
        "enrollments_week": scalar(conn, "SELECT COUNT(*) FROM enrollments WHERE granted_at >= ? AND revoked_at IS NULL",
                                   (iso_in(days=-7),)),
        "next_workshop": next_session,
        "recent_orders": [_order_row(conn, o) for o in all_rows(conn, "SELECT * FROM orders ORDER BY created_at DESC LIMIT 8")],
        "role": user["role"],
    }


# ================================================================ Orders

def _order_row(conn, order: dict) -> dict:
    items = orders.get_items(conn, order["id"])
    return {
        "id": order["id"],
        "status": order["status"],
        "status_label": orders.STATUS_LABELS[order["status"]],
        "created_at": order["created_at"],
        "paid_at": order["paid_at"],
        "customer_name": order["customer_name"],
        "email": order["email"],
        "phone": order["phone"],
        "total_paise": order["total_paise"],
        "requires_shipping": bool(order["requires_shipping"]),
        "ship_city": order["ship_city"],
        "ship_pincode": order["ship_pincode"],
        "needs_review": order["needs_review"],
        "payment_provider": order["payment_provider"],
        "courier": order["courier"],
        "awb": order["awb"],
        "is_legacy": bool(order["is_legacy"]),
        "item_count": sum(i["qty"] for i in items),
        "items_summary": ", ".join(f"{i['title']} ×{i['qty']}" for i in items),
        "kinds": sorted({i["kind"] for i in items}),
    }


@router.get("/orders")
def list_orders(status: str | None = None, q: str | None = None, review: bool = False, page: int = 1, per_page: int = 25,
                user=Depends(require_staff), conn=Depends(get_db)):
    where, params = ["1 = 1"], []
    if status == "to_pack":
        where.append("status = 'paid' AND requires_shipping = 1")
    elif status == "open":
        where.append("status IN ('paid', 'packed', 'shipped')")
    elif status in orders.STATUS_LABELS:
        where.append("status = ?")
        params.append(status)
    if review:
        where.append("needs_review IS NOT NULL AND status != 'cancelled'")
    if q and q.strip():
        like = f"%{q.strip()[:60]}%"
        where.append("(id LIKE ? OR customer_name LIKE ? OR email LIKE ? OR phone LIKE ? OR IFNULL(awb, '') LIKE ?)")
        params.extend([like] * 5)
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    clause = " AND ".join(where)
    total = scalar(conn, f"SELECT COUNT(*) FROM orders WHERE {clause}", params)
    rows = all_rows(conn, f"SELECT * FROM orders WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    [*params, per_page, (page - 1) * per_page])
    return {"orders": [_order_row(conn, o) for o in rows], "total": total, "page": page, "per_page": per_page}


def _order_or_404(conn, order_id: str) -> dict:
    order = orders.get_order(conn, order_id)
    if not order:
        raise not_found("Order not found.")
    return order


@router.get("/orders/{order_id}")
def order_detail(order_id: str, user=Depends(require_staff), conn=Depends(get_db)):
    return orders.serialize(conn, _order_or_404(conn, order_id), admin=True)


class StatusIn(BaseModel):
    status: Literal["packed", "shipped", "delivered"]
    courier: str | None = Field(default=None, max_length=40)
    awb: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=300)


@router.post("/orders/{order_id}/status")
def set_status(order_id: str, body: StatusIn, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    order = orders.advance_status(conn, order_id, body.status, user, courier=body.courier, awb=body.awb, note=body.note)
    _audit(conn, request, user, f"order.{body.status}", "order", order_id, {"courier": body.courier, "awb": body.awb})
    return orders.serialize(conn, order, admin=True)


class BulkStatusIn(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=100)
    status: Literal["packed", "delivered"]


@router.post("/orders/bulk-status")
def bulk_status(body: BulkStatusIn, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    updated, failed = [], []
    for order_id in body.order_ids:
        try:
            orders.advance_status(conn, order_id, body.status, user)
            updated.append(order_id)
        except ApiError as exc:
            failed.append({"order_id": order_id, "message": exc.message})
    _audit(conn, request, user, "order.bulk_status", "order", None, {"status": body.status, "updated": updated})
    return {"updated": updated, "failed": failed}


class TrackingIn(BaseModel):
    courier: str | None = Field(default=None, max_length=40)
    awb: str | None = Field(default=None, max_length=40)


@router.post("/orders/{order_id}/tracking")
def set_tracking(order_id: str, body: TrackingIn, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    _order_or_404(conn, order_id)
    orders.update_tracking(conn, order_id, body.courier, body.awb, user)
    _audit(conn, request, user, "order.tracking", "order", order_id, body.model_dump())
    return orders.serialize(conn, orders.get_order(conn, order_id), admin=True)


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/orders/{order_id}/notes")
def add_order_note(order_id: str, body: NoteIn, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    _order_or_404(conn, order_id)
    orders.add_note(conn, order_id, clean_text(body.note, 500), user["id"])
    _audit(conn, request, user, "order.note", "order", order_id)
    return orders.serialize(conn, orders.get_order(conn, order_id), admin=True)


@router.post("/orders/{order_id}/check-payment")
def check_payment(order_id: str, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    order = _order_or_404(conn, order_id)
    outcome = confirm_with_gateway(conn, order)
    _audit(conn, request, user, "order.check_payment", "order", order_id, {"outcome": outcome})
    return {"outcome": outcome, "order": orders.serialize(conn, orders.get_order(conn, order_id), admin=True)}


@router.post("/orders/{order_id}/retry-fulfilment")
def retry_fulfilment(order_id: str, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    order = _order_or_404(conn, order_id)
    if not order["fulfilment_error"]:
        raise bad_request("This order hasn't failed fulfilment.")
    orders.retry_fulfilment(conn, order_id, user)
    _audit(conn, request, user, "order.retry_fulfilment", "order", order_id)
    return orders.serialize(conn, orders.get_order(conn, order_id), admin=True)


@router.post("/orders/{order_id}/clear-review")
def clear_review(order_id: str, body: NoteIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    order = _order_or_404(conn, order_id)
    with transaction(conn):
        conn.execute("UPDATE orders SET needs_review = NULL, updated_at = ? WHERE id = ?", (iso(), order_id))
        orders.add_note(conn, order_id, f"Review cleared ({order['needs_review']}): {clean_text(body.note, 400)}", user["id"])
        _audit(conn, request, user, "order.clear_review", "order", order_id, {"reason": order["needs_review"], "note": body.note})
    return orders.serialize(conn, orders.get_order(conn, order_id), admin=True)


class MarkPaidIn(BaseModel):
    reference: str = Field(min_length=3, max_length=80)


@router.post("/orders/{order_id}/mark-paid")
def mark_paid(order_id: str, body: MarkPaidIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    order = _order_or_404(conn, order_id)
    if order["payment_provider"] not in ("manual_upi", "legacy"):
        raise bad_request("Gateway payments are confirmed by the gateway. Use “Check payment” instead.")
    outcome = orders.mark_paid(conn, order_id, source="manual confirmation", actor=user, reference=clean_text(body.reference, 80))
    if outcome not in ("paid", "already_paid"):
        raise conflict(f"The order couldn't be marked paid ({outcome.replace('_', ' ')}).")
    _audit(conn, request, user, "order.mark_paid", "order", order_id, {"reference": body.reference})
    return orders.serialize(conn, orders.get_order(conn, order_id), admin=True)


class RefundIn(BaseModel):
    note: str = Field(default="", max_length=300)
    via_gateway: bool = True
    restock: bool = True


@router.post("/orders/{order_id}/refund")
def refund(order_id: str, body: RefundIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    order = orders.refund_order(conn, order_id, actor=user, note=clean_text(body.note, 300), via_gateway=body.via_gateway,
                                restock=body.restock)
    _audit(conn, request, user, "order.refund", "order", order_id, body.model_dump())
    return orders.serialize(conn, order, admin=True)


class CancelIn(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: str, body: CancelIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    order = orders.cancel_order(conn, order_id, user, clean_text(body.note, 300) or "Cancelled by the studio")
    _audit(conn, request, user, "order.cancel", "order", order_id, body.model_dump())
    return orders.serialize(conn, order, admin=True)


# ================================================================ Catalogue

ProductKind = Literal["physical", "kit", "course", "workshop"]


def _slug(conn, title: str, exclude_id: str = "") -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "item"
    slug, n = base, 2
    while scalar(conn, "SELECT 1 FROM products WHERE slug = ? AND id != ?", (slug, exclude_id)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _product_or_404(conn, product_id: str) -> dict:
    product = one(conn, "SELECT * FROM products WHERE id = ?", (product_id,))
    if not product:
        raise not_found("Product not found.")
    return product


def publish_problems(conn, product: dict) -> list[str]:
    if product.get("archived_at"):
        return ["Restore this item from the archive before publishing it."]
    problems = []
    variants = all_rows(conn, "SELECT * FROM product_variants WHERE product_id = ? AND is_active = 1", (product["id"],))
    if product["kind"] == "workshop":
        if not scalar(conn, "SELECT 1 FROM workshop_sessions w JOIN product_variants v ON v.id = w.variant_id "
                            "WHERE v.product_id = ? AND v.is_active = 1 AND w.starts_at > ?", (product["id"], iso())):
            problems.append("Schedule at least one upcoming session.")
    elif not variants:
        # These name the panel and the button, because the price lives away from the Details form
        # it sits next to, and "add a price option" sent the owner hunting for the wrong field.
        problems.append("Under “Price & pieces in stock”, add a size, tick Offered, and press Save on that row.")
    if any(v["price_paise"] <= 0 for v in variants):
        problems.append("Under “Price & pieces in stock”, fill in Price (₹) for every size, then press Save on that row.")
    if not scalar(conn, "SELECT 1 FROM product_media WHERE product_id = ? AND kind = 'image'", (product["id"],)):
        problems.append("Add at least one photo.")
    if product["kind"] == "course" and not scalar(
        conn, "SELECT 1 FROM lessons l JOIN course_modules m ON m.id = l.module_id JOIN courses c ON c.id = m.course_id "
              "WHERE c.product_id = ? AND l.video_key IS NOT NULL", (product["id"],)):
        problems.append("Upload at least one lesson video.")
    return problems


def _admin_product(conn, product_id: str) -> dict:
    product = _product_or_404(conn, product_id)
    data = serialize_product(conn, product, admin=True)
    data["publish_problems"] = publish_problems(conn, product)
    if product["kind"] == "course":
        data["course_id"] = scalar(conn, "SELECT id FROM courses WHERE product_id = ?", (product_id,))
    return data


@router.get("/products")
def products(kind: str | None = None, archived: bool = False, user=Depends(require_staff), conn=Depends(get_db)):
    kinds = [k for k in (kind or "").split(",") if k in ("physical", "kit", "course", "workshop")] or None
    items = list_products(conn, kinds, admin=True, archived=archived)
    for item in items:
        item["publish_problems"] = publish_problems(conn, _product_or_404(conn, item["id"])) if not item["is_active"] else []
    return {"products": items, "categories": all_rows(conn, "SELECT id, name FROM categories ORDER BY sort_order")}


@router.get("/products/{product_id}")
def product_detail(product_id: str, user=Depends(require_staff), conn=Depends(get_db)):
    return _admin_product(conn, product_id)


class ProductCreateIn(BaseModel):
    kind: ProductKind
    title: str = Field(min_length=2, max_length=140)
    category: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=4000)
    price_paise: int = Field(default=0, ge=0, le=10_000_000)
    on_hand: int | None = Field(default=None, ge=0, le=100_000)
    variant_label: str = Field(default="", max_length=80)


@router.post("/products")
def create_product(body: ProductCreateIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    product_id = f"prd-{random_token(6).lower().replace('_', '').replace('-', '')[:8]}"
    now = iso()
    category = body.category or {"course": "masterclasses", "workshop": "workshops", "kit": "diy-kits"}.get(body.kind, "")
    with transaction(conn):
        conn.execute(
            "INSERT INTO products (id, slug, kind, category, title, description, is_active, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM products), ?, ?)",
            (product_id, _slug(conn, body.title), body.kind, category, clean_text(body.title, 140),
             body.description.strip(), now, now),
        )
        if body.kind != "workshop":
            conn.execute(
                "INSERT INTO product_variants (product_id, label, price_paise, on_hand, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?)",
                (product_id, clean_text(body.variant_label, 80), body.price_paise,
                 None if body.kind == "course" else (body.on_hand if body.on_hand is not None else 0), now, now),
            )
        if body.kind == "course":
            conn.execute("INSERT INTO courses (product_id, instructor) VALUES (?, ?)", (product_id, "Rangdhara Studio"))
        _audit(conn, request, user, "product.create", "product", product_id, {"title": body.title, "kind": body.kind})
    return _admin_product(conn, product_id)


class ProductPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=140)
    slug: str | None = Field(default=None, max_length=70)
    category: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=4000)
    details: list[str] | None = Field(default=None, max_length=20)
    includes: list[str] | None = Field(default=None, max_length=20)
    tools_info: str | None = Field(default=None, max_length=400)
    material: str | None = Field(default=None, max_length=200)
    badge: str | None = Field(default=None, max_length=40)
    compare_at_paise: int | None = Field(default=None, ge=0, le=10_000_000)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews_count: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None
    is_featured: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: ProductPatchIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    product = _product_or_404(conn, product_id)
    sent = body.model_fields_set
    updates = {}
    for field in ("description", "tools_info", "material", "badge", "category"):
        if field in sent and getattr(body, field) is not None:
            updates[field] = getattr(body, field).strip()
    if "title" in sent and body.title:
        updates["title"] = clean_text(body.title, 140)
    if "slug" in sent and body.slug:
        updates["slug"] = _slug(conn, body.slug, product_id)
    for field in ("details", "includes"):
        if field in sent and getattr(body, field) is not None:
            updates[field] = json.dumps([clean_text(x, 300) for x in getattr(body, field) if x.strip()])
    for field in ("compare_at_paise", "rating", "reviews_count", "sort_order"):
        if field in sent:
            updates[field] = getattr(body, field)
    if "is_featured" in sent and body.is_featured is not None:
        updates["is_featured"] = 1 if body.is_featured else 0
    if "is_active" in sent and body.is_active is not None:
        if body.is_active and not product["is_active"]:
            problems = publish_problems(conn, product)
            if problems:
                raise bad_request("This item isn't ready to go live yet.", code="not_publishable", problems=problems)
        updates["is_active"] = 1 if body.is_active else 0
    if updates:
        updates["updated_at"] = iso()
        with transaction(conn):
            conn.execute(f"UPDATE products SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?", (*updates.values(), product_id))
            _audit(conn, request, user, "product.update", "product", product_id,
                   {k: v for k, v in updates.items() if k not in ("updated_at", "description")})
    return _admin_product(conn, product_id)


class VariantIn(BaseModel):
    label: str = Field(default="", max_length=80)
    price_paise: int = Field(ge=0, le=10_000_000)
    on_hand: int | None = Field(default=None, ge=0, le=100_000)
    sku: str | None = Field(default=None, max_length=60)


@router.post("/products/{product_id}/variants")
def add_variant(product_id: str, body: VariantIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    product = _product_or_404(conn, product_id)
    if product["kind"] == "workshop":
        raise bad_request("Workshops get their price options from sessions. Schedule a session instead.")
    now = iso()
    with transaction(conn):
        conn.execute(
            "INSERT INTO product_variants (product_id, label, sku, price_paise, on_hand, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM product_variants WHERE product_id = ?), ?, ?)",
            (product_id, clean_text(body.label, 80), body.sku or None, body.price_paise,
             None if product["kind"] == "course" else (body.on_hand or 0), product_id, now, now),
        )
        _audit(conn, request, user, "variant.create", "product", product_id, body.model_dump())
    return _admin_product(conn, product_id)


class VariantPatchIn(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    price_paise: int | None = Field(default=None, ge=0, le=10_000_000)
    on_hand: int | None = Field(default=None, ge=0, le=100_000)
    sku: str | None = Field(default=None, max_length=60)
    is_active: bool | None = None
    position: int | None = Field(default=None, ge=0, le=1000)


@router.patch("/variants/{variant_id}")
def update_variant(variant_id: int, body: VariantPatchIn, request: Request, user=Depends(require_staff), conn=Depends(get_db)):
    variant = one(conn, "SELECT v.*, p.kind FROM product_variants v JOIN products p ON p.id = v.product_id WHERE v.id = ?", (variant_id,))
    if not variant:
        raise not_found("Price option not found.")
    sent = body.model_fields_set
    if user["role"] != "admin" and sent - {"on_hand"}:
        raise forbidden("Staff accounts can update stock counts, but not prices or options.")
    updates = {}
    if "label" in sent and body.label is not None:
        updates["label"] = clean_text(body.label, 80)
    if "price_paise" in sent and body.price_paise is not None:
        updates["price_paise"] = body.price_paise
    if "on_hand" in sent:
        if variant["kind"] == "course":
            raise bad_request("Courses don't have stock.")
        if body.on_hand is None:
            raise bad_request("Enter a stock count (0 or more).")
        updates["on_hand"] = body.on_hand
    if "sku" in sent:
        updates["sku"] = body.sku or None
    if "is_active" in sent and body.is_active is not None:
        updates["is_active"] = 1 if body.is_active else 0
    if "position" in sent and body.position is not None:
        updates["position"] = body.position
    if updates:
        updates["updated_at"] = iso()
        detail = {k: {"from": variant[k], "to": v} for k, v in updates.items() if k != "updated_at"}
        with transaction(conn):
            conn.execute(f"UPDATE product_variants SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                         (*updates.values(), variant_id))
            _audit(conn, request, user, "variant.update", "product", variant["product_id"], {"variant_id": variant_id, **detail})
    return _admin_product(conn, variant["product_id"])


@router.delete("/products/{product_id}")
def delete_product(product_id: str, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    """Delete an item that has never been sold. Anything with order history, students or
    workshop bookings is archived instead, so past orders, receipts and access stay intact."""
    product = _product_or_404(conn, product_id)
    has_history = scalar(
        conn,
        "SELECT 1 FROM order_items WHERE product_id = ? "
        "UNION SELECT 1 FROM enrollments e JOIN courses c ON c.id = e.course_id WHERE c.product_id = ? "
        "UNION SELECT 1 FROM workshop_bookings b JOIN product_variants v ON v.id = b.variant_id WHERE v.product_id = ? LIMIT 1",
        (product_id, product_id, product_id),
    )
    if has_history:
        with transaction(conn):
            conn.execute("UPDATE products SET is_active = 0, is_featured = 0, archived_at = ?, updated_at = ? WHERE id = ?",
                         (iso(), iso(), product_id))
            _audit(conn, request, user, "product.archive", "product", product_id, {"title": product["title"]})
        return {"result": "archived", "message": f"“{product['title']}” has past orders or students, so it was archived instead of deleted. "
                                                 "It's hidden from the store and its order history is kept. Restore it any time from Archived."}

    media = [r["url"] for r in all_rows(conn, "SELECT url FROM product_media WHERE product_id = ?", (product_id,))]
    private_keys = [r["k"] for r in all_rows(
        conn,
        "SELECT l.video_key AS k FROM lessons l JOIN course_modules m ON m.id = l.module_id JOIN courses c ON c.id = m.course_id "
        "WHERE c.product_id = ? AND l.video_key IS NOT NULL "
        "UNION ALL SELECT r.storage_key FROM course_resources r JOIN courses c ON c.id = r.course_id WHERE c.product_id = ?",
        (product_id, product_id),
    )]
    with transaction(conn):
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        _audit(conn, request, user, "product.delete", "product", product_id, {"title": product["title"], "kind": product["kind"]})
    for url in media:
        if not scalar(conn, "SELECT 1 FROM product_media WHERE url = ?", (url,)):
            delete_public_upload(url)
    for key in private_keys:
        delete_private(key)
    return {"result": "deleted", "message": f"“{product['title']}” was deleted."}


@router.post("/products/{product_id}/restore")
def restore_product(product_id: str, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    product = _product_or_404(conn, product_id)
    if product["archived_at"]:
        with transaction(conn):
            conn.execute("UPDATE products SET archived_at = NULL, updated_at = ? WHERE id = ?", (iso(), product_id))
            _audit(conn, request, user, "product.restore", "product", product_id, {"title": product["title"]})
    return _admin_product(conn, product_id)


@router.delete("/variants/{variant_id}")
def delete_variant(variant_id: int, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    variant = one(conn, "SELECT * FROM product_variants WHERE id = ?", (variant_id,))
    if not variant:
        raise not_found("Price option not found.")
    others = scalar(conn, "SELECT COUNT(*) FROM product_variants WHERE product_id = ? AND id != ? AND is_active = 1",
                    (variant["product_id"], variant_id))
    with transaction(conn):
        if scalar(conn, "SELECT 1 FROM order_items WHERE variant_id = ?", (variant_id,)):
            conn.execute("UPDATE product_variants SET is_active = 0, updated_at = ? WHERE id = ?", (iso(), variant_id))
            action = "variant.deactivate"
        else:
            conn.execute("DELETE FROM product_variants WHERE id = ?", (variant_id,))
            action = "variant.delete"
        if not others:
            conn.execute("UPDATE products SET is_active = 0, updated_at = ? WHERE id = ?", (iso(), variant["product_id"]))
        _audit(conn, request, user, action, "product", variant["product_id"], {"variant_id": variant_id})
    return _admin_product(conn, variant["product_id"])


@router.post("/products/{product_id}/media")
def upload_media(product_id: str, request: Request, file: UploadFile = File(...), alt: str = Form(""),
                 user=Depends(require_admin), conn=Depends(get_db)):
    _product_or_404(conn, product_id)
    url = save_public_image(file)
    with transaction(conn):
        conn.execute(
            "INSERT INTO product_media (product_id, url, kind, alt, position) VALUES (?, ?, 'image', ?, "
            "(SELECT COALESCE(MAX(position), -1) + 1 FROM product_media WHERE product_id = ?))",
            (product_id, url, clean_text(alt, 140), product_id),
        )
        _audit(conn, request, user, "media.upload", "product", product_id, {"url": url})
    return _admin_product(conn, product_id)


@router.delete("/media/{media_id}")
def delete_media(media_id: int, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    media = one(conn, "SELECT * FROM product_media WHERE id = ?", (media_id,))
    if not media:
        raise not_found("Photo not found.")
    with transaction(conn):
        conn.execute("DELETE FROM product_media WHERE id = ?", (media_id,))
        _audit(conn, request, user, "media.delete", "product", media["product_id"], {"url": media["url"]})
    if not scalar(conn, "SELECT 1 FROM product_media WHERE url = ?", (media["url"],)):
        delete_public_upload(media["url"])
    return _admin_product(conn, media["product_id"])


class OrderIdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/products/{product_id}/media/order")
def reorder_media(product_id: str, body: OrderIdsIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    with transaction(conn):
        for position, media_id in enumerate(body.ids):
            conn.execute("UPDATE product_media SET position = ? WHERE id = ? AND product_id = ?", (position, media_id, product_id))
        _audit(conn, request, user, "media.reorder", "product", product_id)
    return _admin_product(conn, product_id)


# ================================================================ Coupons

class CouponIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9]{3,30}$")
    kind: Literal["percent", "flat"]
    value: int = Field(gt=0, le=10_000_000)
    min_subtotal_paise: int = Field(default=0, ge=0)
    max_discount_paise: int | None = Field(default=None, ge=0)
    usage_limit: int | None = Field(default=None, ge=1)
    ends_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    is_active: bool = True


def _end_of_day(date_text: str | None) -> str | None:
    if not date_text:
        return None
    day = datetime.strptime(date_text, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=IST)
    return iso(day)


@router.get("/coupons")
def coupons(user=Depends(require_admin), conn=Depends(get_db)):
    return {"coupons": all_rows(conn, "SELECT * FROM coupons ORDER BY created_at DESC")}


@router.post("/coupons")
def create_coupon(body: CouponIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    code = body.code.upper()
    if body.kind == "percent" and body.value > 90:
        raise bad_request("Percentage discounts can't exceed 90%.", fields={"value": "Max 90"})
    if scalar(conn, "SELECT 1 FROM coupons WHERE code = ?", (code,)):
        raise conflict("A code with that name already exists.")
    with transaction(conn):
        conn.execute(
            "INSERT INTO coupons (code, kind, value, min_subtotal_paise, max_discount_paise, usage_limit, ends_at, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, body.kind, body.value, body.min_subtotal_paise, body.max_discount_paise, body.usage_limit,
             _end_of_day(body.ends_on), 1 if body.is_active else 0, iso()),
        )
        _audit(conn, request, user, "coupon.create", "coupon", code, body.model_dump())
    return coupons(user, conn)


class CouponPatchIn(BaseModel):
    is_active: bool | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    ends_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.patch("/coupons/{code}")
def update_coupon(code: str, body: CouponPatchIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    if not scalar(conn, "SELECT 1 FROM coupons WHERE code = ?", (code,)):
        raise not_found("Code not found.")
    sent = body.model_fields_set
    updates = {}
    if "is_active" in sent and body.is_active is not None:
        updates["is_active"] = 1 if body.is_active else 0
    if "usage_limit" in sent:
        updates["usage_limit"] = body.usage_limit
    if "ends_on" in sent:
        updates["ends_at"] = _end_of_day(body.ends_on)
    if updates:
        with transaction(conn):
            conn.execute(f"UPDATE coupons SET {', '.join(f'{k} = ?' for k in updates)} WHERE code = ?", (*updates.values(), code))
            _audit(conn, request, user, "coupon.update", "coupon", code, updates)
    return coupons(user, conn)


# ================================================================ Academy

def _course_or_404(conn, course_id: int) -> dict:
    course = course_with_product(conn, course_id)
    if not course:
        raise not_found("Course not found.")
    return course


@router.get("/courses")
def courses(user=Depends(require_staff), conn=Depends(get_db)):
    result = []
    for product in list_products(conn, ["course"], admin=True):
        course_id = product["course"]["id"] if product.get("course") else None
        stats = one(conn, "SELECT COUNT(*) AS students FROM enrollments WHERE course_id = ? AND revoked_at IS NULL", (course_id,))
        product["students"] = stats["students"]
        product["course_id"] = course_id
        product["publish_problems"] = publish_problems(conn, _product_or_404(conn, product["id"]))
        result.append(product)
    return {"courses": result}


@router.get("/courses/{course_id}")
def course_detail(course_id: int, user=Depends(require_staff), conn=Depends(get_db)):
    course = _course_or_404(conn, course_id)
    modules = []
    for module in all_rows(conn, "SELECT * FROM course_modules WHERE course_id = ? ORDER BY position, id", (course_id,)):
        lessons = all_rows(conn, "SELECT id, title, description, position, duration_s, is_preview, video_size, "
                                 "video_key IS NOT NULL AS has_video FROM lessons WHERE module_id = ? ORDER BY position, id",
                           (module["id"],))
        for lesson in lessons:
            lesson["has_video"] = bool(lesson["has_video"])
            lesson["is_preview"] = bool(lesson["is_preview"])
        modules.append({"id": module["id"], "title": module["title"], "lessons": lessons})
    return {
        "course": {
            "id": course["id"], "product_id": course["product_id"], "title": course["title"], "level": course["level"],
            "instructor": course["instructor"], "outcomes": jloads(course["outcomes"]),
            "certificate_enabled": bool(course["certificate_enabled"]), "access_days": course["access_days"],
        },
        "product": _admin_product(conn, course["product_id"]),
        "modules": modules,
        "resources": all_rows(conn, "SELECT id, title, filename, size_bytes, lesson_id, created_at FROM course_resources "
                                    "WHERE course_id = ? ORDER BY id", (course_id,)),
        "students": scalar(conn, "SELECT COUNT(*) FROM enrollments WHERE course_id = ? AND revoked_at IS NULL", (course_id,)),
    }


class CoursePatchIn(BaseModel):
    level: str | None = Field(default=None, max_length=40)
    instructor: str | None = Field(default=None, max_length=80)
    outcomes: list[str] | None = Field(default=None, max_length=12)
    certificate_enabled: bool | None = None
    access_days: int | None = Field(default=None, ge=1, le=3650)


@router.patch("/courses/{course_id}")
def update_course(course_id: int, body: CoursePatchIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    _course_or_404(conn, course_id)
    sent = body.model_fields_set
    updates = {}
    if "level" in sent and body.level is not None:
        updates["level"] = clean_text(body.level, 40)
    if "instructor" in sent and body.instructor is not None:
        updates["instructor"] = clean_text(body.instructor, 80)
    if "outcomes" in sent and body.outcomes is not None:
        updates["outcomes"] = json.dumps([clean_text(x, 200) for x in body.outcomes if x.strip()])
    if "certificate_enabled" in sent and body.certificate_enabled is not None:
        updates["certificate_enabled"] = 1 if body.certificate_enabled else 0
    if "access_days" in sent:
        updates["access_days"] = body.access_days
    if updates:
        with transaction(conn):
            conn.execute(f"UPDATE courses SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?", (*updates.values(), course_id))
            _audit(conn, request, user, "course.update", "course", course_id, updates)
    return course_detail(course_id, user, conn)


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=140)


@router.post("/courses/{course_id}/modules")
def add_module(course_id: int, body: TitleIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    _course_or_404(conn, course_id)
    with transaction(conn):
        conn.execute("INSERT INTO course_modules (course_id, title, position) VALUES (?, ?, "
                     "(SELECT COALESCE(MAX(position), -1) + 1 FROM course_modules WHERE course_id = ?))",
                     (course_id, clean_text(body.title, 140), course_id))
        _audit(conn, request, user, "module.create", "course", course_id, {"title": body.title})
    return course_detail(course_id, user, conn)


def _module_or_404(conn, module_id: int) -> dict:
    module = one(conn, "SELECT * FROM course_modules WHERE id = ?", (module_id,))
    if not module:
        raise not_found("Module not found.")
    return module


@router.patch("/modules/{module_id}")
def rename_module(module_id: int, body: TitleIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    module = _module_or_404(conn, module_id)
    with transaction(conn):
        conn.execute("UPDATE course_modules SET title = ? WHERE id = ?", (clean_text(body.title, 140), module_id))
        _audit(conn, request, user, "module.rename", "course", module["course_id"], {"module_id": module_id, "title": body.title})
    return course_detail(module["course_id"], user, conn)


@router.delete("/modules/{module_id}")
def delete_module(module_id: int, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    module = _module_or_404(conn, module_id)
    keys = [r["video_key"] for r in all_rows(conn, "SELECT video_key FROM lessons WHERE module_id = ? AND video_key IS NOT NULL", (module_id,))]
    with transaction(conn):
        conn.execute("DELETE FROM course_modules WHERE id = ?", (module_id,))
        _audit(conn, request, user, "module.delete", "course", module["course_id"], {"module_id": module_id, "title": module["title"]})
    for key in keys:
        delete_private(key)
    return course_detail(module["course_id"], user, conn)


@router.post("/courses/{course_id}/modules/order")
def reorder_modules(course_id: int, body: OrderIdsIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    with transaction(conn):
        for position, module_id in enumerate(body.ids):
            conn.execute("UPDATE course_modules SET position = ? WHERE id = ? AND course_id = ?", (position, module_id, course_id))
        _audit(conn, request, user, "module.reorder", "course", course_id)
    return course_detail(course_id, user, conn)


class LessonIn(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    description: str = Field(default="", max_length=2000)
    is_preview: bool = False


@router.post("/modules/{module_id}/lessons")
def add_lesson(module_id: int, body: LessonIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    module = _module_or_404(conn, module_id)
    now = iso()
    with transaction(conn):
        conn.execute(
            "INSERT INTO lessons (module_id, title, description, is_preview, position, created_at, updated_at) VALUES (?, ?, ?, ?, "
            "(SELECT COALESCE(MAX(position), -1) + 1 FROM lessons WHERE module_id = ?), ?, ?)",
            (module_id, clean_text(body.title, 140), body.description.strip(), 1 if body.is_preview else 0, module_id, now, now),
        )
        _audit(conn, request, user, "lesson.create", "course", module["course_id"], {"title": body.title})
    return course_detail(module["course_id"], user, conn)


def _lesson_or_404(conn, lesson_id: int) -> dict:
    lesson = one(conn, "SELECT l.*, m.course_id FROM lessons l JOIN course_modules m ON m.id = l.module_id WHERE l.id = ?", (lesson_id,))
    if not lesson:
        raise not_found("Lesson not found.")
    return lesson


class LessonPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=140)
    description: str | None = Field(default=None, max_length=2000)
    is_preview: bool | None = None
    duration_s: int | None = Field(default=None, ge=0, le=6 * 3600)
    module_id: int | None = None


@router.patch("/lessons/{lesson_id}")
def update_lesson(lesson_id: int, body: LessonPatchIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    lesson = _lesson_or_404(conn, lesson_id)
    sent = body.model_fields_set
    updates = {}
    if "title" in sent and body.title:
        updates["title"] = clean_text(body.title, 140)
    if "description" in sent and body.description is not None:
        updates["description"] = body.description.strip()
    if "is_preview" in sent and body.is_preview is not None:
        updates["is_preview"] = 1 if body.is_preview else 0
    if "duration_s" in sent and body.duration_s is not None:
        updates["duration_s"] = body.duration_s
    if "module_id" in sent and body.module_id:
        target = _module_or_404(conn, body.module_id)
        if target["course_id"] != lesson["course_id"]:
            raise bad_request("Lessons can only move between modules of the same course.")
        updates["module_id"] = body.module_id
    if updates:
        updates["updated_at"] = iso()
        with transaction(conn):
            conn.execute(f"UPDATE lessons SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?", (*updates.values(), lesson_id))
            _audit(conn, request, user, "lesson.update", "course", lesson["course_id"], {"lesson_id": lesson_id, **updates})
    return course_detail(lesson["course_id"], user, conn)


@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    lesson = _lesson_or_404(conn, lesson_id)
    with transaction(conn):
        conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        _audit(conn, request, user, "lesson.delete", "course", lesson["course_id"], {"lesson_id": lesson_id, "title": lesson["title"]})
    delete_private(lesson["video_key"])
    return course_detail(lesson["course_id"], user, conn)


@router.post("/modules/{module_id}/lessons/order")
def reorder_lessons(module_id: int, body: OrderIdsIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    module = _module_or_404(conn, module_id)
    with transaction(conn):
        for position, lesson_id in enumerate(body.ids):
            conn.execute("UPDATE lessons SET position = ? WHERE id = ? AND module_id = ?", (position, lesson_id, module_id))
        _audit(conn, request, user, "lesson.reorder", "course", module["course_id"])
    return course_detail(module["course_id"], user, conn)


@router.post("/lessons/{lesson_id}/video")
def upload_video(lesson_id: int, request: Request, file: UploadFile = File(...), duration_s: int = Form(0),
                 user=Depends(require_admin), conn=Depends(get_db)):
    lesson = _lesson_or_404(conn, lesson_id)
    info = save_private(file, "videos", VIDEO_TYPES, settings.max_video_upload_mb * MB)
    with transaction(conn):
        conn.execute("UPDATE lessons SET video_key = ?, video_size = ?, duration_s = CASE WHEN ? > 0 THEN ? ELSE duration_s END, "
                     "updated_at = ? WHERE id = ?",
                     (info["key"], info["size"], duration_s, min(max(duration_s, 0), 6 * 3600), iso(), lesson_id))
        _audit(conn, request, user, "lesson.video", "course", lesson["course_id"],
               {"lesson_id": lesson_id, "filename": info["filename"], "size": info["size"]})
    delete_private(lesson["video_key"])
    return course_detail(lesson["course_id"], user, conn)


@router.delete("/lessons/{lesson_id}/video")
def remove_video(lesson_id: int, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    lesson = _lesson_or_404(conn, lesson_id)
    with transaction(conn):
        conn.execute("UPDATE lessons SET video_key = NULL, video_size = NULL, updated_at = ? WHERE id = ?", (iso(), lesson_id))
        _audit(conn, request, user, "lesson.video_remove", "course", lesson["course_id"], {"lesson_id": lesson_id})
    delete_private(lesson["video_key"])
    return course_detail(lesson["course_id"], user, conn)


@router.post("/courses/{course_id}/resources")
def upload_resource(course_id: int, request: Request, file: UploadFile = File(...), title: str = Form(""),
                    user=Depends(require_admin), conn=Depends(get_db)):
    _course_or_404(conn, course_id)
    info = save_private(file, "resources", RESOURCE_TYPES, 100 * MB)
    with transaction(conn):
        conn.execute("INSERT INTO course_resources (course_id, title, filename, storage_key, content_type, size_bytes, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (course_id, clean_text(title, 140) or info["filename"], info["filename"], info["key"],
                      info["content_type"], info["size"], iso()))
        _audit(conn, request, user, "resource.upload", "course", course_id, {"filename": info["filename"]})
    return course_detail(course_id, user, conn)


@router.delete("/resources/{resource_id}")
def delete_resource(resource_id: int, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    resource = one(conn, "SELECT * FROM course_resources WHERE id = ?", (resource_id,))
    if not resource:
        raise not_found("File not found.")
    with transaction(conn):
        conn.execute("DELETE FROM course_resources WHERE id = ?", (resource_id,))
        _audit(conn, request, user, "resource.delete", "course", resource["course_id"], {"filename": resource["filename"]})
    delete_private(resource["storage_key"])
    return course_detail(resource["course_id"], user, conn)


# ================================================================ Workshops

@router.get("/workshops")
def workshops(user=Depends(require_staff), conn=Depends(get_db)):
    result = []
    for product in list_products(conn, ["workshop"], admin=True):
        sessions = all_rows(
            conn,
            "SELECT v.id AS variant_id, v.label, v.price_paise, v.on_hand, v.is_active, w.starts_at, w.duration_min, w.seats_total, "
            "w.meeting_url, w.notes, (SELECT COALESCE(SUM(seats), 0) FROM workshop_bookings b WHERE b.variant_id = v.id "
            "AND b.status = 'confirmed') AS seats_taken FROM product_variants v JOIN workshop_sessions w ON w.variant_id = v.id "
            "WHERE v.product_id = ? ORDER BY w.starts_at",
            (product["id"],),
        )
        product["sessions"] = sessions
        product["publish_problems"] = publish_problems(conn, _product_or_404(conn, product["id"]))
        result.append(product)
    return {"workshops": result}


class SessionIn(BaseModel):
    starts_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", description="Local India time")
    duration_min: int = Field(default=120, ge=15, le=600)
    seats_total: int = Field(ge=1, le=500)
    price_paise: int = Field(gt=0, le=10_000_000)
    meeting_url: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=500)


def _parse_local(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=IST)


def _session_label(start: datetime) -> str:
    return start.strftime("%a %d %b · %I:%M %p").replace(" 0", " ")


def _check_meeting_url(url: str) -> str:
    url = url.strip()
    if url and not url.startswith("https://"):
        raise bad_request("The meeting link must start with https://", fields={"meeting_url": "Use an https:// link"})
    return url


@router.post("/products/{product_id}/sessions")
def add_session(product_id: str, body: SessionIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    product = _product_or_404(conn, product_id)
    if product["kind"] != "workshop":
        raise bad_request("Sessions can only be added to workshops.")
    start = _parse_local(body.starts_at)
    if start <= datetime.now(IST):
        raise bad_request("Choose a start time in the future.", fields={"starts_at": "Must be in the future"})
    now = iso()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO product_variants (product_id, label, price_paise, on_hand, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM product_variants WHERE product_id = ?), ?, ?)",
            (product_id, _session_label(start), body.price_paise, body.seats_total, product_id, now, now),
        )
        conn.execute("INSERT INTO workshop_sessions (variant_id, starts_at, duration_min, seats_total, meeting_url, notes) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (cur.lastrowid, iso(start), body.duration_min, body.seats_total, _check_meeting_url(body.meeting_url),
                      body.notes.strip()))
        _audit(conn, request, user, "session.create", "product", product_id, body.model_dump())
    return workshops(user, conn)


class SessionPatchIn(BaseModel):
    starts_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    duration_min: int | None = Field(default=None, ge=15, le=600)
    seats_total: int | None = Field(default=None, ge=1, le=500)
    meeting_url: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


@router.patch("/sessions/{variant_id}")
def update_session(variant_id: int, body: SessionPatchIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    session = one(conn, "SELECT w.*, v.product_id, v.on_hand FROM workshop_sessions w JOIN product_variants v ON v.id = w.variant_id "
                        "WHERE w.variant_id = ?", (variant_id,))
    if not session:
        raise not_found("Session not found.")
    sent = body.model_fields_set
    with transaction(conn):
        if "starts_at" in sent and body.starts_at:
            start = _parse_local(body.starts_at)
            conn.execute("UPDATE workshop_sessions SET starts_at = ? WHERE variant_id = ?", (iso(start), variant_id))
            conn.execute("UPDATE product_variants SET label = ?, updated_at = ? WHERE id = ?", (_session_label(start), iso(), variant_id))
        if "duration_min" in sent and body.duration_min:
            conn.execute("UPDATE workshop_sessions SET duration_min = ? WHERE variant_id = ?", (body.duration_min, variant_id))
        if "seats_total" in sent and body.seats_total:
            delta = body.seats_total - session["seats_total"]
            conn.execute("UPDATE workshop_sessions SET seats_total = ? WHERE variant_id = ?", (body.seats_total, variant_id))
            conn.execute("UPDATE product_variants SET on_hand = MAX(0, on_hand + ?), updated_at = ? WHERE id = ?", (delta, iso(), variant_id))
        if "meeting_url" in sent and body.meeting_url is not None:
            conn.execute("UPDATE workshop_sessions SET meeting_url = ? WHERE variant_id = ?", (_check_meeting_url(body.meeting_url), variant_id))
        if "notes" in sent and body.notes is not None:
            conn.execute("UPDATE workshop_sessions SET notes = ? WHERE variant_id = ?", (body.notes.strip(), variant_id))
        if "is_active" in sent and body.is_active is not None:
            conn.execute("UPDATE product_variants SET is_active = ?, updated_at = ? WHERE id = ?", (1 if body.is_active else 0, iso(), variant_id))
        _audit(conn, request, user, "session.update", "product", session["product_id"], {"variant_id": variant_id, **body.model_dump(exclude_unset=True)})
    return workshops(user, conn)


@router.get("/sessions/{variant_id}/bookings")
def session_bookings(variant_id: int, user=Depends(require_staff), conn=Depends(get_db)):
    return {"bookings": all_rows(
        conn,
        "SELECT b.id, b.seats, b.status, b.attended, b.created_at, b.order_id, u.full_name, u.email, u.phone "
        "FROM workshop_bookings b JOIN users u ON u.id = b.user_id WHERE b.variant_id = ? ORDER BY b.created_at",
        (variant_id,),
    )}


# ================================================================ Students & customers

@router.get("/enrollments")
def enrollments(course_id: int | None = None, q: str | None = None, user=Depends(require_staff), conn=Depends(get_db)):
    where, params = ["1 = 1"], []
    if course_id:
        where.append("e.course_id = ?")
        params.append(course_id)
    if q and q.strip():
        like = f"%{q.strip()[:60]}%"
        where.append("(u.full_name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?)")
        params.extend([like] * 3)
    rows = all_rows(
        conn,
        f"SELECT e.*, u.full_name, u.email, u.phone, p.title AS course_title FROM enrollments e JOIN users u ON u.id = e.user_id "
        f"JOIN courses c ON c.id = e.course_id JOIN products p ON p.id = c.product_id WHERE {' AND '.join(where)} "
        f"ORDER BY e.granted_at DESC LIMIT 300",
        params,
    )
    for row in rows:
        row["progress"] = summary(conn, row) if not row["revoked_at"] else None
    return {"enrollments": rows}


class GrantIn(BaseModel):
    email: str = Field(max_length=120)
    full_name: str = Field(default="", max_length=80)
    course_id: int
    reason: str = Field(min_length=3, max_length=200)


@router.post("/enrollments")
def grant(body: GrantIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    email = normalize_email(body.email)
    if not is_email(email):
        raise bad_request("Enter a valid email address.", fields={"email": "Invalid email"})
    course = _course_or_404(conn, body.course_id)
    with transaction(conn):
        student = one(conn, "SELECT * FROM users WHERE email = ?", (email,))
        if not student:
            student = create_user(conn, email=email, full_name=clean_text(body.full_name, 80) or email.split("@")[0])
        enrollment, created = grant_enrollment(conn, student["id"], course["id"], source="manual", reason=clean_text(body.reason, 200))
        if not created:
            raise conflict(f"{email} already has access to this course.")
        emails.send_enrollment(conn, student, course["title"], course["id"], reason="manual")
        _audit(conn, request, user, "enrollment.grant", "course", course["id"], {"email": email, "reason": body.reason})
    return enrollments(course["id"], None, user, conn)


class RevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/enrollments/{enrollment_id}/revoke")
def revoke(enrollment_id: int, body: RevokeIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    enrollment = one(conn, "SELECT * FROM enrollments WHERE id = ?", (enrollment_id,))
    if not enrollment:
        raise not_found("Enrolment not found.")
    with transaction(conn):
        revoke_enrollment(conn, enrollment_id, clean_text(body.reason, 200))
        _audit(conn, request, user, "enrollment.revoke", "course", enrollment["course_id"],
               {"enrollment_id": enrollment_id, "reason": body.reason})
    return enrollments(enrollment["course_id"], None, user, conn)


@router.get("/customers")
def customers(q: str | None = None, page: int = 1, user=Depends(require_admin), conn=Depends(get_db)):
    where, params = ["1 = 1"], []
    if q and q.strip():
        like = f"%{q.strip()[:60]}%"
        where.append("(u.full_name LIKE ? OR u.email LIKE ? OR IFNULL(u.phone, '') LIKE ?)")
        params.extend([like] * 3)
    page = max(1, page)
    clause = " AND ".join(where)
    rows = all_rows(
        conn,
        f"SELECT u.id, u.full_name, u.email, u.phone, u.role, u.created_at, u.email_verified_at IS NOT NULL AS verified, "
        f"u.password_hash IS NOT NULL AS has_password, u.must_reset_password, "
        f"(SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id AND o.status IN ('paid', 'packed', 'shipped', 'delivered')) AS orders, "
        f"(SELECT COALESCE(SUM(total_paise), 0) FROM orders o WHERE o.user_id = u.id AND o.status IN ('paid', 'packed', 'shipped', 'delivered')) AS spent_paise, "
        f"(SELECT COUNT(*) FROM enrollments e WHERE e.user_id = u.id AND e.revoked_at IS NULL) AS courses, "
        f"(SELECT MAX(created_at) FROM orders o WHERE o.user_id = u.id) AS last_order_at, "
        f"(SELECT city FROM addresses a WHERE a.user_id = u.id ORDER BY a.is_default DESC, a.updated_at DESC LIMIT 1) AS city "
        f"FROM users u WHERE {clause} ORDER BY u.created_at DESC LIMIT 50 OFFSET ?",
        [*params, (page - 1) * 50],
    )
    for row in rows:
        for key in ("verified", "has_password", "must_reset_password"):
            row[key] = bool(row[key])
    return {"customers": rows, "total": scalar(conn, f"SELECT COUNT(*) FROM users u WHERE {clause}", params), "page": page}


PAID_SQL = "('paid', 'packed', 'shipped', 'delivered')"


@router.get("/customers.csv")
def customers_csv(request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    rows = all_rows(
        conn,
        f"SELECT u.*, (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id AND o.status IN {PAID_SQL}) AS orders, "
        f"(SELECT COALESCE(SUM(total_paise), 0) FROM orders o WHERE o.user_id = u.id AND o.status IN {PAID_SQL}) AS spent_paise, "
        f"(SELECT COUNT(*) FROM enrollments e WHERE e.user_id = u.id AND e.revoked_at IS NULL) AS courses "
        f"FROM users u ORDER BY u.created_at DESC",
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Name", "Email", "Phone", "Role", "Joined (IST)", "Email verified", "Marketing emails", "Paid orders",
                     "Total spent (INR)", "Active courses", "Address", "City", "State", "PIN code"])
    for u in rows:
        address = one(conn, "SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, updated_at DESC LIMIT 1", (u["id"],)) or {}
        joined = parse_iso(u["created_at"]).astimezone(IST).strftime("%Y-%m-%d %H:%M")
        writer.writerow([_csv_cell(x) for x in (
            u["full_name"], u["email"], u["phone"] or "", {"admin": "Owner"}.get(u["role"], u["role"].title()), joined,
            "Yes" if u["email_verified_at"] else "No", "Yes" if u["marketing_opt_in"] else "No", u["orders"],
            f"{u['spent_paise'] / 100:.2f}", u["courses"],
            ", ".join(x for x in (address.get("line1"), address.get("line2")) if x), address.get("city", ""),
            address.get("state", ""), address.get("pincode", ""))])
    with transaction(conn):
        _audit(conn, request, user, "customer.export", "customer", None, {"rows": len(rows)})
    filename = f"rangdhara-customers-{datetime.now(IST):%Y%m%d}.csv"
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/customers/{user_id}")
def customer_detail(user_id: str, user=Depends(require_admin), conn=Depends(get_db)):
    c = one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
    if not c:
        raise not_found("Customer not found.")
    order_rows = all_rows(conn, "SELECT * FROM orders WHERE user_id = ? OR (user_id IS NULL AND email = ?) ORDER BY created_at DESC",
                          (user_id, c["email"]))
    paid = [o for o in order_rows if o["status"] in orders.PAID_STATES]
    enrollments = all_rows(
        conn,
        "SELECT e.*, p.title AS course_title FROM enrollments e JOIN courses c ON c.id = e.course_id "
        "JOIN products p ON p.id = c.product_id WHERE e.user_id = ? ORDER BY e.granted_at DESC",
        (user_id,),
    )
    for e in enrollments:
        e["progress"] = summary(conn, e) if not e["revoked_at"] else None
    return {
        "customer": {
            "id": c["id"], "full_name": c["full_name"], "email": c["email"], "phone": c["phone"] or "", "role": c["role"],
            "created_at": c["created_at"], "email_verified": bool(c["email_verified_at"]), "has_password": bool(c["password_hash"]),
            "must_reset_password": bool(c["must_reset_password"]), "marketing_opt_in": bool(c["marketing_opt_in"]),
            "imported_from_old_site": bool(c["legacy_id"]),
        },
        "stats": {
            "paid_orders": len(paid),
            "spent_paise": sum(o["total_paise"] for o in paid),
            "last_order_at": order_rows[0]["created_at"] if order_rows else None,
            "last_seen_at": scalar(conn, "SELECT MAX(last_seen_at) FROM sessions WHERE user_id = ?", (user_id,)),
        },
        "addresses": all_rows(conn, "SELECT full_name, phone, line1, line2, city, state, pincode, is_default FROM addresses "
                                    "WHERE user_id = ? ORDER BY is_default DESC, updated_at DESC", (user_id,)),
        "orders": [_order_row(conn, o) for o in order_rows],
        "enrollments": enrollments,
        "bookings": all_rows(
            conn,
            "SELECT b.seats, b.status, b.order_id, w.starts_at, p.title FROM workshop_bookings b "
            "JOIN workshop_sessions w ON w.variant_id = b.variant_id JOIN product_variants v ON v.id = b.variant_id "
            "JOIN products p ON p.id = v.product_id WHERE b.user_id = ? ORDER BY w.starts_at DESC",
            (user_id,),
        ),
    }


class RoleIn(BaseModel):
    role: Literal["customer", "staff", "admin"]


@router.post("/users/{user_id}/role")
def set_role(user_id: str, body: RoleIn, request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    if user_id == user["id"]:
        raise bad_request("You can't change your own role.")
    target = one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
    if not target:
        raise not_found("Customer not found.")
    if body.role in ("staff", "admin") and not target["password_hash"]:
        raise bad_request("This person needs to set a password before they can be given admin access.")
    with transaction(conn):
        conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (body.role, iso(), user_id))
        _audit(conn, request, user, "user.role", "user", user_id, {"from": target["role"], "to": body.role, "email": target["email"]})
    return customers(target["email"], 1, user, conn)


# ================================================================ Revenue

PRODUCT_KINDS = ("physical", "kit")


def _revenue_rows(conn, period: str, span: int) -> tuple[list[dict], list[dict]]:
    span = max(1, min(span, 366 if period == "day" else 36))
    if period == "day":
        start = _ist_day_start(span - 1)
        keys = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(span)]
    else:
        today = datetime.now(IST)
        year, month = today.year, today.month
        keys = []
        for _ in range(span):
            keys.insert(0, f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                year, month = year - 1, 12
        start = datetime(int(keys[0][:4]), int(keys[0][5:]), 1, tzinfo=IST)
    since = iso(start)

    def bucket(ts: str) -> str:
        local = parse_iso(ts).astimezone(IST)
        return local.strftime("%Y-%m-%d") if period == "day" else local.strftime("%Y-%m")

    series = {k: {"bucket": k, "products_paise": 0, "academy_paise": 0, "refunds_paise": 0, "orders": 0} for k in keys}
    detail = []
    for order in all_rows(conn, "SELECT * FROM orders WHERE paid_at IS NOT NULL AND paid_at >= ? ORDER BY paid_at", (since,)):
        lines = orders.get_items(conn, order["id"])
        goods = sum(l["line_total_paise"] for l in lines if l["kind"] in PRODUCT_KINDS)
        ratio = (order["subtotal_paise"] - order["discount_paise"]) / order["subtotal_paise"] if order["subtotal_paise"] else 0
        products_net = round(goods * ratio) + order["shipping_paise"]
        academy_net = order["total_paise"] - products_net
        key = bucket(order["paid_at"])
        if key in series:
            series[key]["products_paise"] += products_net
            series[key]["academy_paise"] += academy_net
            series[key]["orders"] += 1
        detail.append({"order": order, "products": products_net, "academy": academy_net})
    for order in all_rows(conn, "SELECT id, refunded_at, total_paise FROM orders WHERE refunded_at IS NOT NULL AND refunded_at >= ?", (since,)):
        key = bucket(order["refunded_at"])
        if key in series:
            series[key]["refunds_paise"] += order["total_paise"]
    rows = list(series.values())
    for row in rows:
        row["net_paise"] = row["products_paise"] + row["academy_paise"] - row["refunds_paise"]
    return rows, detail


@router.get("/revenue")
def revenue(period: Literal["day", "month"] = "day", span: int = 30, user=Depends(require_admin), conn=Depends(get_db)):
    rows, _ = _revenue_rows(conn, period, span)
    totals = {k: sum(r[k] for r in rows) for k in ("products_paise", "academy_paise", "refunds_paise", "net_paise", "orders")}
    return {"period": period, "series": rows, "totals": totals}


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text  # stop spreadsheet formula injection


@router.get("/revenue.csv")
def revenue_csv(period: Literal["day", "month"] = "day", span: int = 30, user=Depends(require_admin), conn=Depends(get_db)):
    _, detail = _revenue_rows(conn, period, span)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Paid (IST)", "Order", "Status", "Customer", "Email", "Products (INR)", "Academy (INR)", "Shipping (INR)",
                     "Discount (INR)", "Total (INR)", "Refunded (IST)", "Payment method", "Payment reference"])
    for row in detail:
        o = row["order"]
        paid = parse_iso(o["paid_at"]).astimezone(IST).strftime("%Y-%m-%d %H:%M")
        refunded = parse_iso(o["refunded_at"]).astimezone(IST).strftime("%Y-%m-%d %H:%M") if o["refunded_at"] else ""
        writer.writerow([_csv_cell(x) for x in (
            paid, o["id"], o["status"], o["customer_name"], o["email"], f"{row['products'] / 100:.2f}", f"{row['academy'] / 100:.2f}",
            f"{o['shipping_paise'] / 100:.2f}", f"{o['discount_paise'] / 100:.2f}", f"{o['total_paise'] / 100:.2f}", refunded,
            o["payment_provider"], o["gateway_payment_id"] or o["payment_reference"] or "")])
    filename = f"rangdhara-revenue-{datetime.now(IST):%Y%m%d}.csv"
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ================================================================ Audit & system

@router.get("/audit")
def audit_log(page: int = 1, user=Depends(require_admin), conn=Depends(get_db)):
    page = max(1, page)
    return {"entries": all_rows(conn, "SELECT * FROM audit_log ORDER BY id DESC LIMIT 100 OFFSET ?", ((page - 1) * 100,)),
            "total": scalar(conn, "SELECT COUNT(*) FROM audit_log"), "page": page}


@router.get("/system")
def system(user=Depends(require_admin), conn=Depends(get_db)):
    provider = get_provider()
    backups_dir = settings.data_dir / "backups"
    return {
        "environment": settings.app_env,
        "public_base_url": settings.public_base_url,
        "payment_provider": provider.name,
        "payment_label": provider.label,
        "smtp_configured": settings.smtp_configured,
        "store_admin_email": settings.store_admin_email,
        "outbox": {r["status"]: r["count"] for r in all_rows(conn, "SELECT status, COUNT(*) AS count FROM email_outbox GROUP BY status")},
        "failed_emails": all_rows(conn, "SELECT id, kind, to_email, subject, attempts, last_error, created_at FROM email_outbox "
                                        "WHERE status = 'failed' ORDER BY id DESC LIMIT 10"),
        "jobs": {name: kv_get(conn, f"job:{name}") for name, interval, _ in TASKS if interval},
        "backups": sorted((p.name for p in backups_dir.glob("rangdhaara-*.db")), reverse=True)[:14] if backups_dir.exists() else [],
        "webhook_urls": {name: f"{settings.public_base_url}/api/v1/webhooks/{name}" for name in ("razorpay", "cashfree")},
        "production_blockers": settings.problems(assume_production=True),
    }


@router.post("/system/test-email")
def test_email(request: Request, user=Depends(require_admin), conn=Depends(get_db)):
    emails.enqueue(conn, "test", user["email"], "Rangdhara test email",
                   "<p>If you can read this, email delivery from your store is working.</p>")
    delivered = emails.deliver_pending(conn)
    _audit(conn, request, user, "system.test_email", "system", None, {"to": user["email"]})
    row = one(conn, "SELECT status, last_error FROM email_outbox WHERE kind = 'test' ORDER BY id DESC LIMIT 1")
    return {"status": row["status"] if row else "queued", "error": row["last_error"] if row else None, "delivered": delivered}
