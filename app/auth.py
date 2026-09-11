"""Server-side sessions held in an HttpOnly cookie, plus role guards for routes.

The browser only ever holds an opaque random token; the database stores its
SHA-256, so a leaked database can't be replayed as live sessions.
"""
from fastapi import Depends, Request, Response

from .config import settings
from .db import get_db, iso, iso_in, one, parse_iso, utcnow
from .errors import forbidden, unauthorized
from .security import random_token, sha256_hex

SESSION_COOKIE = "rg_session"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def create_session(conn, user_id: str, request: Request, response: Response) -> str:
    token = random_token(32)
    token_hash = sha256_hex(token)
    now = iso()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            token_hash,
            user_id,
            now,
            now,
            iso_in(days=settings.session_days),
            client_ip(request),
            (request.headers.get("user-agent") or "")[:300],
        ),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return token_hash


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")


def current_user(request: Request, conn=Depends(get_db)) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 200:
        return None
    token_hash = sha256_hex(token)
    user = one(
        conn,
        "SELECT u.*, s.token_hash AS session_token_hash, s.last_seen_at AS session_last_seen "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.is_active = 1",
        (token_hash, iso()),
    )
    if not user:
        return None
    now = utcnow()
    idle = int((now - parse_iso(user["session_last_seen"])).total_seconds())
    if user["role"] in ("staff", "admin") and idle > settings.staff_idle_hours * 3600:
        # Staff sessions end after a long idle gap; revoke so the timeout can't be bypassed by a refresh.
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (iso(now), token_hash))
        request.state.session_timed_out = True
        return None
    if idle > 300:
        conn.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
            (iso(now), iso_in(days=settings.session_days), token_hash),
        )
    return user


def require_user(user=Depends(current_user)) -> dict:
    if not user:
        raise unauthorized()
    return user


def require_staff(request: Request, user=Depends(current_user)) -> dict:
    if not user:
        if getattr(request.state, "session_timed_out", False):
            raise unauthorized("Your admin session timed out. Please sign in again.", code="session_timeout")
        raise unauthorized("Sign in with a staff account to open the admin panel.")
    if user["role"] not in ("staff", "admin"):
        raise forbidden("This account doesn't have admin access.")
    return user


def require_admin(user=Depends(require_staff)) -> dict:
    if user["role"] != "admin":
        raise forbidden("Only the store owner can do this.")
    return user


def revoke_all_sessions(conn, user_id: str, keep_token_hash: str | None = None) -> None:
    conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL AND token_hash != ?",
        (iso(), user_id, keep_token_hash or ""),
    )


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "phone": user["phone"] or "",
        "full_name": user["full_name"],
        "role": user["role"],
        "email_verified": bool(user["email_verified_at"]),
        "has_password": bool(user["password_hash"]),
        "must_reset_password": bool(user["must_reset_password"]),
        "marketing_opt_in": bool(user["marketing_opt_in"]),
        "created_at": user["created_at"],
    }
