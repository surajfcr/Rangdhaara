"""First-run setup: the starting catalogue, and a one-time import of the old
JSON files (data/legacy/users.json and orders.json)."""
import json
import re
from pathlib import Path

from . import seed_data
from .config import PUBLIC_DIR, settings
from .db import iso, kv_get, kv_set, one, scalar, transaction
from .security import hash_password, is_email, is_phone, normalize_email, normalize_phone
from .shipping import INDIAN_STATES, state_for_pincode
from .users import add_address, new_user_id, phone_taken


def ensure_seeded(conn) -> dict:
    report = {}
    if not kv_get(conn, "seed:catalogue"):
        with transaction(conn):
            if not scalar(conn, "SELECT COUNT(*) FROM products"):
                report["products"] = _seed_catalogue(conn)
            _seed_coupons(conn)
            kv_set(conn, "seed:catalogue", iso())
    legacy = settings.data_dir / "legacy"
    if not kv_get(conn, "seed:legacy") and ((legacy / "users.json").exists() or (legacy / "orders.json").exists()):
        report["legacy"] = import_legacy(conn, legacy)
        kv_set(conn, "seed:legacy", json.dumps(report["legacy"]))
    return report


def _slug(conn, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    slug, n = base, 2
    while scalar(conn, "SELECT 1 FROM products WHERE slug = ?", (slug,)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _insert_product(conn, item: dict, *, kind: str, category: str, active: bool, sort: int, on_hand) -> None:
    now = iso()
    conn.execute(
        "INSERT INTO products (id, slug, kind, category, title, description, details, includes, tools_info, material, badge, "
        "rating, reviews_count, compare_at_paise, is_active, is_featured, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item["id"], _slug(conn, item["title"]), kind, category, item["title"], item.get("description", ""),
            json.dumps(item.get("details", [])), json.dumps(item.get("includes", [])), item.get("tools_info", ""),
            item.get("material", ""), item.get("badge", ""), item.get("rating"), item.get("reviews", 0),
            item["compare_at"] * 100 if item.get("compare_at") else None, 1 if active else 0,
            1 if item.get("featured") else 0, sort, now, now,
        ),
    )
    if kind != "workshop":
        conn.execute(
            "INSERT INTO product_variants (product_id, label, sku, price_paise, on_hand, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (item["id"], item.get("size", ""), item["id"], item.get("price", 0) * 100, on_hand, now, now),
        )
    images_dir = PUBLIC_DIR / "assets" / "images"
    present = [image for image in item.get("images", []) if (images_dir / image).exists()]
    for position, image in enumerate(present):
        conn.execute("INSERT INTO product_media (product_id, url, kind, alt, position) VALUES (?, ?, 'image', ?, ?)",
                     (item["id"], f"/assets/images/{image}", item["title"], position))


def _seed_catalogue(conn) -> int:
    for cat_id, name, order in seed_data.CATEGORIES:
        conn.execute("INSERT OR IGNORE INTO categories (id, name, sort_order) VALUES (?, ?, ?)", (cat_id, name, order))
    sort = 0
    for item in seed_data.PRODUCTS:
        sort += 1
        _insert_product(conn, item, kind=item["kind"], category=item["category"], active=True, sort=sort,
                        on_hand=seed_data.DEFAULT_STOCK)
    for item in seed_data.DRAFTS:
        sort += 1
        _insert_product(conn, item, kind=item["kind"], category=item["category"], active=False, sort=sort,
                        on_hand=seed_data.DEFAULT_STOCK)
    for item in seed_data.COURSES:
        sort += 1
        _insert_product(conn, item, kind="course", category="masterclasses", active=False, sort=sort, on_hand=None)
        conn.execute("INSERT INTO courses (product_id, level, instructor) VALUES (?, ?, ?)",
                     (item["id"], item["level"], "Rangdhara Studio"))
    for item in seed_data.WORKSHOPS:
        sort += 1
        _insert_product(conn, item, kind="workshop", category="workshops", active=False, sort=sort, on_hand=None)
    return sort


def _seed_coupons(conn) -> None:
    for coupon in seed_data.COUPONS:
        conn.execute("INSERT OR IGNORE INTO coupons (code, kind, value, created_at) VALUES (?, ?, ?, ?)",
                     (coupon["code"], coupon["kind"], coupon["value"], iso()))


def _state_name(raw: str, pincode: str) -> str:
    wanted = (raw or "").strip().lower()
    for state in INDIAN_STATES:
        if state.lower() == wanted:
            return state
    return state_for_pincode(pincode) or (raw or "").strip().title()


def import_legacy(conn, directory: Path) -> dict:
    """Bring customers and orders over from the old JSON files.

    Old passwords were stored in plain text and exposed, so they're hashed and
    every imported account must set a new password on its next sign-in.
    Old orders were "confirmed" without any payment check, so they arrive as
    awaiting payment and flagged for review.
    """
    report = {"users": 0, "orders": 0, "skipped": 0}
    users_file, orders_file = directory / "users.json", directory / "orders.json"
    legacy_users = json.loads(users_file.read_text(encoding="utf-8")) if users_file.exists() else []
    legacy_orders = json.loads(orders_file.read_text(encoding="utf-8")) if orders_file.exists() else []
    hashes = {id(u): hash_password(u["password"]) for u in legacy_users if u.get("password")}

    with transaction(conn):
        for record in legacy_users:
            email = normalize_email(record.get("email"))
            if not is_email(email) or one(conn, "SELECT 1 FROM users WHERE email = ?", (email,)):
                report["skipped"] += 1
                continue
            phone = normalize_phone(record.get("phone"))
            phone = phone if is_phone(phone) and not phone_taken(conn, phone) else None
            user_id = new_user_id()
            created = record.get("createdAt", "")
            created = created[:19] + "Z" if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", created) else iso()
            has_password = id(record) in hashes
            conn.execute(
                "INSERT INTO users (id, email, phone, full_name, password_hash, must_reset_password, legacy_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, email, phone, (record.get("fullName") or "").strip(), hashes.get(id(record)),
                 1 if has_password else 0, record.get("id"), created, iso()),
            )
            pincode = (record.get("pincode") or "").strip()
            if record.get("address") and pincode:
                add_address(conn, user_id, {
                    "full_name": (record.get("fullName") or "").strip(),
                    "phone": phone or "",
                    "line1": record["address"].strip(),
                    "line2": "",
                    "city": (record.get("city") or "").strip().title(),
                    "state": _state_name(record.get("state", ""), pincode),
                    "pincode": pincode,
                })
            report["users"] += 1

        for record in legacy_orders:
            order_id = (record.get("orderId") or "").strip()
            if not order_id or one(conn, "SELECT 1 FROM orders WHERE id = ?", (order_id,)):
                report["skipped"] += 1
                continue
            items = record.get("items") or []
            subtotal = sum(int(i.get("price", 0)) * int(i.get("quantity", 1)) for i in items) * 100
            total = int(record.get("totalAmount") or 0) * 100 or subtotal
            email = normalize_email(record.get("email"))
            owner = one(conn, "SELECT id FROM users WHERE email = ?", (email,))
            pincode = (record.get("pincode") or "").strip()
            now = iso()
            conn.execute(
                "INSERT INTO orders (id, user_id, email, phone, customer_name, requires_shipping, ship_name, ship_phone, ship_line1, "
                "ship_city, ship_state, ship_pincode, subtotal_paise, discount_paise, shipping_paise, total_paise, status, needs_review, "
                "payment_provider, is_legacy, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'pending_payment', 'legacy_unverified_payment', 'legacy', 1, ?, ?)",
                (order_id, owner["id"] if owner else None, email, normalize_phone(record.get("phone")), record.get("customerName", ""),
                 record.get("customerName", ""), normalize_phone(record.get("phone")), record.get("address", ""),
                 (record.get("city") or "").title(), state_for_pincode(pincode) or "", pincode, subtotal,
                 max(0, subtotal - total), total, now, now),
            )
            for item in items:
                qty = max(1, int(item.get("quantity", 1)))
                price = int(item.get("price", 0)) * 100
                conn.execute(
                    "INSERT INTO order_items (order_id, kind, title, unit_price_paise, qty, line_total_paise) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, "kit" if "DIY" in (item.get("typeLabel") or "") else "physical", item.get("title", "Item"),
                     price, qty, price * qty),
                )
            conn.execute(
                "INSERT INTO order_events (order_id, from_status, to_status, note, created_at) VALUES (?, NULL, 'pending_payment', ?, ?)",
                (order_id, "Imported from the old website. That site confirmed orders without checking payment — match this "
                           "against your UPI records, then mark it paid or cancel it.", now),
            )
            report["orders"] += 1
    return report
