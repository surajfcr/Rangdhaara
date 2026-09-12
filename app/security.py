"""Passwords, tokens, OTP codes, signatures, input normalisation and rate limits."""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import settings
from .db import iso, iso_in, scalar, utcnow

_hasher = PasswordHasher()  # argon2id with library defaults (memory-hard)

# A short list of the passwords that show up first in every credential-stuffing list.
COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "password123", "passw0rd",
    "qwerty123", "qwertyuiop", "11111111", "00000000", "abcd1234", "iloveyou", "welcome1",
    "admin123", "pass1234", "letmein1", "sunshine", "princess", "football", "baseball",
    "india123", "india@123", "rangdhaara", "rangdhaara123", "rangdhara", "rangdhara123",
    "12341234", "87654321", "asdfghjk",
}

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problem(password: str, email: str = "") -> str | None:
    """Return a human-readable reason the password is unacceptable, or None."""
    if len(password) < 8:
        return "Use at least 8 characters."
    if len(password) > 128:
        return "Use 128 characters or fewer."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        return "That password is too common. Choose something less predictable."
    local = email.split("@", 1)[0].lower() if email else ""
    if local and len(local) >= 4 and lowered == local:
        return "Your password can't be the same as your email name."
    if len(set(password)) < 4:
        return "Use a mix of more than a few different characters."
    return None


# ---------------------------------------------------------------- tokens and signatures

def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sign(message: str) -> str:
    digest = hmac.new(settings.secret_key.encode(), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def verify_signature(message: str, signature: str) -> bool:
    return hmac.compare_digest(sign(message), signature or "")


def order_access_token(order_id: str) -> str:
    """Derived rather than stored, so any later email (shipped, refunded) can carry a working order link."""
    return sign(f"order|{order_id}")[:32]


def check_order_token(order_id: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(order_access_token(order_id), token)


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_hash(request_id: str, code: str) -> str:
    """Keyed hash, so a leaked database can't be brute-forced offline across 10^6 codes."""
    return hmac.new(settings.secret_key.encode(), f"otp|{request_id}|{code}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- normalisation

def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def is_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or ""))


def is_phone(value: str) -> bool:
    return bool(PHONE_RE.match(value or ""))


def is_pincode(value: str) -> bool:
    return bool(PINCODE_RE.match(value or ""))


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "your email"
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def clean_text(value: str | None, max_len: int = 200) -> str:
    """Trim, collapse control characters, and cap length for free-text fields."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value or "").strip()
    return text[:max_len]


# ---------------------------------------------------------------- rate limiting

def rate_limited(conn, key: str, limit: int, window_seconds: int, record: bool = True) -> bool:
    """True when `key` has already hit `limit` events inside the window.
    Records this attempt when it is allowed (and `record` is set)."""
    since = iso(utcnow() - timedelta(seconds=window_seconds))
    count = scalar(conn, "SELECT COUNT(*) FROM rate_events WHERE key = ? AND created_at > ?", (key, since))
    if count >= limit:
        return True
    if record:
        conn.execute("INSERT INTO rate_events (key, created_at) VALUES (?, ?)", (key, iso()))
    return False


def purge_rate_events(conn) -> None:
    conn.execute("DELETE FROM rate_events WHERE created_at < ?", (iso_in(days=-2),))
