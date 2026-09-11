"""Customer account lookups and creation shared by auth, checkout and admin."""
import secrets

from .db import iso, one
from .security import is_email, normalize_email, normalize_phone


def new_user_id() -> str:
    return f"usr_{secrets.token_hex(6)}"


def find_by_identifier(conn, identifier: str) -> dict | None:
    """Email or 10-digit Indian mobile number."""
    identifier = (identifier or "").strip()
    if "@" in identifier:
        email = normalize_email(identifier)
        return one(conn, "SELECT * FROM users WHERE email = ?", (email,)) if is_email(email) else None
    phone = normalize_phone(identifier)
    return one(conn, "SELECT * FROM users WHERE phone = ?", (phone,)) if len(phone) == 10 else None


def phone_taken(conn, phone: str, except_user_id: str | None = None) -> bool:
    if not phone:
        return False
    row = one(conn, "SELECT id FROM users WHERE phone = ?", (phone,))
    return bool(row) and row["id"] != except_user_id


def create_user(conn, *, email: str, full_name: str, phone: str | None = None, password_hash: str | None = None,
                verified: bool = False, marketing: bool = False, role: str = "customer") -> dict:
    user_id = new_user_id()
    now = iso()
    conn.execute(
        "INSERT INTO users (id, email, phone, full_name, password_hash, role, email_verified_at, marketing_opt_in, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, normalize_email(email), phone or None, full_name, password_hash, role,
         now if verified else None, 1 if marketing else 0, now, now),
    )
    return one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))


def add_address(conn, user_id: str, address: dict, make_default: bool = False) -> dict:
    """Insert unless an identical address is already saved; returns the stored row."""
    existing = one(
        conn,
        "SELECT * FROM addresses WHERE user_id = ? AND line1 = ? AND line2 = ? AND pincode = ? AND full_name = ?",
        (user_id, address["line1"], address.get("line2", ""), address["pincode"], address["full_name"]),
    )
    has_default = one(conn, "SELECT id FROM addresses WHERE user_id = ? AND is_default = 1", (user_id,))
    default = make_default or not has_default
    now = iso()
    if default:
        conn.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
    if existing:
        conn.execute("UPDATE addresses SET phone = ?, city = ?, state = ?, is_default = MAX(is_default, ?), updated_at = ? WHERE id = ?",
                     (address["phone"], address["city"], address["state"], 1 if default else 0, now, existing["id"]))
        return one(conn, "SELECT * FROM addresses WHERE id = ?", (existing["id"],))
    cur = conn.execute(
        "INSERT INTO addresses (user_id, full_name, phone, line1, line2, city, state, pincode, is_default, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, address["full_name"], address["phone"], address["line1"], address.get("line2", ""),
         address["city"], address["state"], address["pincode"], 1 if default else 0, now, now),
    )
    return one(conn, "SELECT * FROM addresses WHERE id = ?", (cur.lastrowid,))
