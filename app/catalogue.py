"""Product reads shared by the storefront, cart pricing and the admin panel."""
from .db import all_rows, iso, jloads, one, scalar

KIND_LABELS = {"physical": "Ready to buy", "kit": "DIY kit", "course": "Masterclass", "workshop": "Live workshop"}


def available_qty(conn, variant: dict) -> int | None:
    """Units that can still be sold right now: on hand minus live checkout holds. None = not tracked."""
    if variant["on_hand"] is None:
        return None
    held = scalar(
        conn,
        "SELECT COALESCE(SUM(qty), 0) FROM stock_reservations WHERE variant_id = ? AND status = 'active' AND expires_at > ?",
        (variant["id"], iso()),
    )
    return max(0, variant["on_hand"] - held)


def category_names(conn) -> dict[str, str]:
    return {r["id"]: r["name"] for r in all_rows(conn, "SELECT id, name FROM categories")}


def serialize_product(conn, product: dict, *, admin: bool = False, categories: dict | None = None) -> dict:
    categories = categories if categories is not None else category_names(conn)
    variant_sql = "SELECT v.*, w.starts_at, w.duration_min, w.seats_total, w.meeting_url, w.notes AS session_notes " \
                  "FROM product_variants v LEFT JOIN workshop_sessions w ON w.variant_id = v.id WHERE v.product_id = ?"
    if not admin:
        variant_sql += " AND v.is_active = 1"
    variants = []
    for v in all_rows(conn, variant_sql + " ORDER BY v.position, v.id", (product["id"],)):
        if product["kind"] == "workshop" and not admin and (not v["starts_at"] or v["starts_at"] <= iso()):
            continue  # past sessions aren't for sale
        item = {
            "id": v["id"],
            "label": v["label"],
            "price_paise": v["price_paise"],
            "available": available_qty(conn, v),
        }
        if v["starts_at"]:
            item["session"] = {"starts_at": v["starts_at"], "duration_min": v["duration_min"], "seats_total": v["seats_total"]}
        if admin:
            item.update(sku=v["sku"], on_hand=v["on_hand"], is_active=bool(v["is_active"]), position=v["position"])
            if v["starts_at"]:
                item["session"].update(meeting_url=v["meeting_url"], notes=v["session_notes"])
        variants.append(item)

    media = all_rows(conn, "SELECT id, url, kind, alt FROM product_media WHERE product_id = ? ORDER BY position, id", (product["id"],))
    images = [m for m in media if m["kind"] == "image"]
    prices = [v["price_paise"] for v in variants]
    data = {
        "id": product["id"],
        "slug": product["slug"],
        "kind": product["kind"],
        "kind_label": KIND_LABELS[product["kind"]],
        "category": product["category"],
        "category_name": categories.get(product["category"], ""),
        "title": product["title"],
        "description": product["description"],
        "details": jloads(product["details"]),
        "includes": jloads(product["includes"]),
        "tools_info": product["tools_info"],
        "material": product["material"],
        "badge": product["badge"],
        "rating": product["rating"],
        "reviews_count": product["reviews_count"],
        "compare_at_paise": product["compare_at_paise"],
        "price_paise": min(prices) if prices else None,
        # A photo isn't required to publish, so this can't be "" — every product card and cart
        # row on the site assumes an image src, and an empty one renders as a broken-image icon.
        "image": images[0]["url"] if images else "/assets/images/profile_avatar.jpg",
        "media": media,
        "variants": variants,
        "in_stock": any(v["available"] is None or v["available"] > 0 for v in variants),
        "is_featured": bool(product["is_featured"]),
    }
    if product["kind"] == "course":
        data["course"] = course_summary(conn, product["id"])
    if admin:
        data.update(is_active=bool(product["is_active"]), archived_at=product["archived_at"], sort_order=product["sort_order"],
                    created_at=product["created_at"], updated_at=product["updated_at"])
    return data


def course_summary(conn, product_id: str) -> dict | None:
    course = one(conn, "SELECT * FROM courses WHERE product_id = ?", (product_id,))
    if not course:
        return None
    stats = one(
        conn,
        "SELECT COUNT(DISTINCT m.id) AS modules, COUNT(l.id) AS lessons, COALESCE(SUM(l.duration_s), 0) AS seconds "
        "FROM course_modules m LEFT JOIN lessons l ON l.module_id = m.id WHERE m.course_id = ?",
        (course["id"],),
    )
    outline = []
    for m in all_rows(conn, "SELECT id, title FROM course_modules WHERE course_id = ? ORDER BY position, id", (course["id"],)):
        lessons = all_rows(
            conn,
            "SELECT id, title, duration_s, is_preview, video_key IS NOT NULL AS has_video FROM lessons "
            "WHERE module_id = ? ORDER BY position, id",
            (m["id"],),
        )
        outline.append({"id": m["id"], "title": m["title"], "lessons": [
            {"id": l["id"], "title": l["title"], "duration_s": l["duration_s"],
             "is_preview": bool(l["is_preview"] and l["has_video"])} for l in lessons]})
    return {
        "id": course["id"],
        "level": course["level"],
        "instructor": course["instructor"],
        "outcomes": jloads(course["outcomes"]),
        "certificate_enabled": bool(course["certificate_enabled"]),
        "access_days": course["access_days"],
        "module_count": stats["modules"],
        "lesson_count": stats["lessons"],
        "total_minutes": round(stats["seconds"] / 60),
        "outline": outline,
    }


def list_products(conn, kinds: list[str] | None = None, *, admin: bool = False, archived: bool = False) -> list[dict]:
    sql = f"SELECT * FROM products WHERE archived_at IS {'NOT NULL' if archived else 'NULL'}"
    params: list = []
    if not admin:
        sql += " AND is_active = 1"
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params.extend(kinds)
    sql += " ORDER BY sort_order, created_at"
    categories = category_names(conn)
    items = [serialize_product(conn, p, admin=admin, categories=categories) for p in all_rows(conn, sql, params)]
    if not admin:
        items = [p for p in items if p["variants"]]  # nothing purchasable, e.g. a workshop with no upcoming sessions
    return items


def get_product(conn, id_or_slug: str, *, admin: bool = False) -> dict | None:
    product = one(conn, "SELECT * FROM products WHERE id = ? OR slug = ?", (id_or_slug, id_or_slug))
    if not product or (not admin and (not product["is_active"] or product["archived_at"])):
        return None
    return serialize_product(conn, product, admin=admin)
