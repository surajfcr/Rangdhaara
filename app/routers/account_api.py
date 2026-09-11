"""The signed-in customer's own account: profile, addresses, password,
signed-in devices, order history and Academy access."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import orders
from ..academy import summary
from ..auth import current_user, public_user, require_user, revoke_all_sessions
from ..db import all_rows, get_db, iso, iso_in, one, scalar, transaction
from ..errors import bad_request, not_found
from ..security import clean_text, hash_password, is_phone, normalize_phone, password_problem, verify_password
from ..users import add_address, phone_taken

router = APIRouter()


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=20)
    marketing_opt_in: bool | None = None


class PasswordIn(BaseModel):
    current_password: str = Field(default="", max_length=200)
    new_password: str = Field(max_length=200)
    confirm_password: str = Field(max_length=200)


class AddressIn(BaseModel):
    full_name: str = Field(max_length=80)
    phone: str = Field(max_length=20)
    line1: str = Field(max_length=160)
    line2: str = Field(default="", max_length=160)
    city: str = Field(max_length=60)
    state: str = Field(max_length=60)
    pincode: str = Field(max_length=10)
    is_default: bool = False


def _addresses(conn, user_id: str) -> list[dict]:
    return all_rows(conn, "SELECT id, full_name, phone, line1, line2, city, state, pincode, is_default FROM addresses "
                          "WHERE user_id = ? ORDER BY is_default DESC, updated_at DESC", (user_id,))


@router.get("")
def me(user=Depends(require_user), conn=Depends(get_db)):
    return {"user": public_user(user), "addresses": _addresses(conn, user["id"])}


@router.patch("")
def update_profile(body: ProfileIn, user=Depends(require_user), conn=Depends(get_db)):
    fields, updates = {}, {}
    if body.full_name is not None:
        name = clean_text(body.full_name, 80)
        if name:
            updates["full_name"] = name
        else:
            fields["full_name"] = "Enter your name."
    if body.phone is not None:
        phone = normalize_phone(body.phone)
        if not is_phone(phone):
            fields["phone"] = "Enter a 10-digit Indian mobile number."
        elif phone_taken(conn, phone, user["id"]):
            fields["phone"] = "That number is linked to another account."
        else:
            updates["phone"] = phone
    if body.marketing_opt_in is not None:
        updates["marketing_opt_in"] = 1 if body.marketing_opt_in else 0
    if fields:
        raise bad_request("Some details need fixing.", fields=fields)
    if updates:
        updates["updated_at"] = iso()
        conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?", (*updates.values(), user["id"]))
    return {"user": public_user(one(conn, "SELECT * FROM users WHERE id = ?", (user["id"],)))}


@router.post("/password")
def change_password(body: PasswordIn, user=Depends(require_user), conn=Depends(get_db)):
    if user["password_hash"] and not verify_password(user["password_hash"], body.current_password):
        raise bad_request("Your current password isn't right.", fields={"current_password": "Incorrect password."})
    problem = password_problem(body.new_password, user["email"])
    if problem:
        raise bad_request(problem, fields={"new_password": problem})
    if body.new_password != body.confirm_password:
        raise bad_request("The two new passwords don't match.", fields={"confirm_password": "Doesn't match."})
    new_hash = hash_password(body.new_password)
    with transaction(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_reset_password = 0, updated_at = ? WHERE id = ?",
                     (new_hash, iso(), user["id"]))
        revoke_all_sessions(conn, user["id"], keep_token_hash=user["session_token_hash"])
    return {"user": public_user(one(conn, "SELECT * FROM users WHERE id = ?", (user["id"],))),
            "message": "Password saved. Any other devices have been signed out."}


@router.get("/addresses")
def list_addresses(user=Depends(require_user), conn=Depends(get_db)):
    return {"addresses": _addresses(conn, user["id"])}


@router.post("/addresses")
def create_address(body: AddressIn, user=Depends(require_user), conn=Depends(get_db)):
    clean = orders.validate_address(body.model_dump())
    with transaction(conn):
        add_address(conn, user["id"], clean, make_default=body.is_default)
    return {"addresses": _addresses(conn, user["id"])}


def _own_address(conn, user_id: str, address_id: int) -> dict:
    row = one(conn, "SELECT * FROM addresses WHERE id = ? AND user_id = ?", (address_id, user_id))
    if not row:
        raise not_found("That address isn't saved on your account.")
    return row


@router.patch("/addresses/{address_id}")
def edit_address(address_id: int, body: AddressIn, user=Depends(require_user), conn=Depends(get_db)):
    _own_address(conn, user["id"], address_id)
    clean = orders.validate_address(body.model_dump())
    with transaction(conn):
        if body.is_default:
            conn.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user["id"],))
        conn.execute(
            "UPDATE addresses SET full_name = ?, phone = ?, line1 = ?, line2 = ?, city = ?, state = ?, pincode = ?, "
            "is_default = MAX(is_default, ?), updated_at = ? WHERE id = ?",
            (clean["full_name"], clean["phone"], clean["line1"], clean["line2"], clean["city"], clean["state"],
             clean["pincode"], 1 if body.is_default else 0, iso(), address_id),
        )
    return {"addresses": _addresses(conn, user["id"])}


@router.delete("/addresses/{address_id}")
def delete_address(address_id: int, user=Depends(require_user), conn=Depends(get_db)):
    row = _own_address(conn, user["id"], address_id)
    with transaction(conn):
        conn.execute("DELETE FROM addresses WHERE id = ?", (address_id,))
        if row["is_default"]:
            conn.execute("UPDATE addresses SET is_default = 1 WHERE id = (SELECT id FROM addresses WHERE user_id = ? "
                         "ORDER BY updated_at DESC LIMIT 1)", (user["id"],))
    return {"addresses": _addresses(conn, user["id"])}


@router.get("/orders")
def my_orders(user=Depends(require_user), conn=Depends(get_db)):
    rows = all_rows(
        conn,
        "SELECT * FROM orders WHERE (user_id = ? OR (user_id IS NULL AND email = ?)) "
        "AND NOT (status = 'cancelled' AND paid_at IS NULL AND gateway_order_id IS NULL) ORDER BY created_at DESC LIMIT 100",
        (user["id"], user["email"]),
    )
    result = []
    for order in rows:
        items = orders.get_items(conn, order["id"])
        result.append({
            "id": order["id"],
            "status": order["status"],
            "status_label": orders.STATUS_LABELS[order["status"]],
            "created_at": order["created_at"],
            "total_paise": order["total_paise"],
            "requires_shipping": bool(order["requires_shipping"]),
            "delivery": {"min_date": order["delivery_min_date"], "max_date": order["delivery_max_date"]}
            if order["delivery_min_date"] else None,
            "courier": order["courier"],
            "awb": order["awb"],
            "items": [{"title": i["title"], "qty": i["qty"], "image": i["image"], "kind": i["kind"]} for i in items],
        })
    return {"orders": result}


@router.get("/sessions")
def my_sessions(user=Depends(require_user), conn=Depends(get_db)):
    rows = all_rows(conn, "SELECT token_hash, created_at, last_seen_at, ip, user_agent FROM sessions "
                          "WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ? ORDER BY last_seen_at DESC",
                    (user["id"], iso()))
    return {"sessions": [{
        "current": r["token_hash"] == user["session_token_hash"],
        "created_at": r["created_at"],
        "last_seen_at": r["last_seen_at"],
        "ip": r["ip"],
        "device": _device(r["user_agent"] or ""),
    } for r in rows]}


def _device(agent: str) -> str:
    browser = next((b for b in ("Edg", "OPR", "Chrome", "Firefox", "Safari") if b in agent), "Browser")
    browser = {"Edg": "Edge", "OPR": "Opera"}.get(browser, browser)
    system = next((s for s, key in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
                                     ("Mac", "Macintosh"), ("Linux", "Linux")) if key in agent), "")
    return f"{browser} on {system}" if system else browser


@router.post("/sessions/sign-out-others")
def sign_out_others(user=Depends(require_user), conn=Depends(get_db)):
    revoke_all_sessions(conn, user["id"], keep_token_hash=user["session_token_hash"])
    return {"ok": True}


@router.get("/courses")
def my_courses(user=Depends(require_user), conn=Depends(get_db)):
    enrollments = all_rows(
        conn,
        "SELECT e.*, p.title, p.slug, c.level, c.instructor, c.certificate_enabled, "
        "(SELECT url FROM product_media m WHERE m.product_id = p.id AND m.kind = 'image' ORDER BY m.position, m.id LIMIT 1) AS image "
        "FROM enrollments e JOIN courses c ON c.id = e.course_id JOIN products p ON p.id = c.product_id "
        "WHERE e.user_id = ? ORDER BY e.granted_at DESC",
        (user["id"],),
    )
    now = iso()
    courses = []
    for e in enrollments:
        active = not e["revoked_at"] and (not e["expires_at"] or e["expires_at"] > now)
        progress = summary(conn, e) if active else None
        courses.append({
            "course_id": e["course_id"],
            "title": e["title"],
            "image": e["image"] or "",
            "level": e["level"],
            "instructor": e["instructor"],
            "active": active,
            "granted_at": e["granted_at"],
            "expires_at": e["expires_at"],
            "progress": progress,
            "certificate_available": bool(active and e["certificate_enabled"] and progress and progress["is_complete"]),
        })
    workshops = []
    for b in all_rows(
        conn,
        "SELECT b.seats, b.status, w.starts_at, w.duration_min, w.meeting_url, w.notes, p.title, v.label FROM workshop_bookings b "
        "JOIN workshop_sessions w ON w.variant_id = b.variant_id JOIN product_variants v ON v.id = b.variant_id "
        "JOIN products p ON p.id = v.product_id WHERE b.user_id = ? AND b.status = 'confirmed' ORDER BY w.starts_at",
        (user["id"],),
    ):
        link_open = b["starts_at"] <= iso_in(hours=48)
        ended = iso_in(minutes=-(b["duration_min"] or 120)) > b["starts_at"]
        workshops.append({
            "title": b["title"],
            "label": b["label"],
            "starts_at": b["starts_at"],
            "duration_min": b["duration_min"],
            "seats": b["seats"],
            "notes": b["notes"],
            "meeting_url": b["meeting_url"] if link_open and not ended else None,
            "link_note": None if link_open else "Your join link appears here 48 hours before the session.",
            "ended": ended,
        })
    return {"courses": courses, "workshops": workshops}
