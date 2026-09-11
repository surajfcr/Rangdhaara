"""Sign in with a password or an emailed code, verified sign-up, and password reset.

Responses to "send me a code" never reveal whether an account exists.
Codes are single-use, expire after 10 minutes and allow 5 tries.
"""
import hmac
import json
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from .. import emails
from ..auth import SESSION_COOKIE, clear_session_cookie, client_ip, create_session, current_user, public_user, revoke_all_sessions
from ..config import settings
from ..db import get_db, iso, iso_in, one, transaction
from ..errors import bad_request, conflict, too_many, unauthorized
from ..security import (clean_text, generate_otp, hash_password, is_email, is_phone, mask_email, normalize_email,
                        normalize_phone, otp_hash, password_problem, random_token, rate_limited, sha256_hex, verify_password)
from ..users import create_user, find_by_identifier, phone_taken

router = APIRouter()

CODE_SENT = "If an account matches, we've emailed it a 6-digit code. It expires in 10 minutes."


class LoginIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class CodeRequestIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)
    purpose: Literal["login", "reset"]


class CodeVerifyIn(BaseModel):
    request_id: str = Field(min_length=10, max_length=64)
    code: str = Field(min_length=6, max_length=12)


class SignupIn(BaseModel):
    full_name: str = Field(max_length=80)
    email: str = Field(max_length=120)
    phone: str = Field(max_length=20)
    password: str = Field(max_length=200)
    confirm_password: str = Field(max_length=200)
    marketing_opt_in: bool = False


class ResetIn(BaseModel):
    reset_token: str = Field(min_length=10, max_length=100)
    password: str = Field(max_length=200)
    confirm_password: str = Field(max_length=200)


@router.get("/session")
def session(user=Depends(current_user)):
    return {"user": public_user(user) if user else None}


def _issue_code(conn, *, purpose: str, email: str, name: str = "", user_id: str | None = None,
                payload: dict | None = None, ip: str = "", deliver: bool = True) -> str:
    """Create a code request. With deliver=False it's a decoy that can never verify,
    so the response looks the same whether or not the account exists."""
    request_id = random_token(18)
    code = generate_otp()
    if deliver:
        conn.execute("UPDATE otp_requests SET consumed_at = ? WHERE email = ? AND purpose = ? AND consumed_at IS NULL",
                     (iso(), email, purpose))
    conn.execute(
        "INSERT INTO otp_requests (id, purpose, email, user_id, code_hash, payload, expires_at, created_at, ip) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, purpose, email, user_id, otp_hash(request_id, code) if deliver else None,
         json.dumps(payload) if payload else None, iso_in(minutes=settings.otp_ttl_minutes), iso(), ip),
    )
    if deliver:
        emails.send_otp(conn, email, name, code, purpose)
    return request_id


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn=Depends(get_db)):
    ip = client_ip(request)
    key = body.identifier.strip().lower()
    if rate_limited(conn, f"login:ip:{ip}", 30, 900) or rate_limited(conn, f"login:id:{key}", 8, 900):
        raise too_many("Too many sign-in attempts. Wait 15 minutes, or sign in with an emailed code.")
    user = find_by_identifier(conn, body.identifier)
    if not user or not user["is_active"] or not verify_password(user["password_hash"], body.password):
        raise unauthorized("That email or phone and password don't match. If you checked out as a guest, "
                           "sign in with an emailed code instead.", code="invalid_credentials")
    if user["must_reset_password"]:
        if rate_limited(conn, f"otp:id:{user['email']}", 3, 3600):
            raise too_many("We've already sent several codes. Check your inbox, or wait before asking again.")
        with transaction(conn):
            request_id = _issue_code(conn, purpose="reset", email=user["email"], name=user["full_name"],
                                     user_id=user["id"], ip=ip)
        raise conflict("For your security, please set a new password. We've emailed you a code.",
                       code="password_reset_required", request_id=request_id, destination=mask_email(user["email"]))
    with transaction(conn):
        create_session(conn, user["id"], request, response)
    return {"user": public_user(user)}


@router.post("/code")
def request_code(body: CodeRequestIn, request: Request, conn=Depends(get_db)):
    ip = client_ip(request)
    key = body.identifier.strip().lower()
    if rate_limited(conn, f"otp:ip:{ip}", 10, 3600) or rate_limited(conn, f"otp:id:{key}", 3, 3600):
        raise too_many("You've asked for several codes already. Check your inbox and spam folder, or wait before asking again.")
    user = find_by_identifier(conn, body.identifier)
    with transaction(conn):
        if user and user["is_active"]:
            request_id = _issue_code(conn, purpose=body.purpose, email=user["email"], name=user["full_name"],
                                     user_id=user["id"], ip=ip)
        else:
            request_id = _issue_code(conn, purpose=body.purpose, email=key[:120], ip=ip, deliver=False)
    return {"request_id": request_id, "message": CODE_SENT}


@router.post("/code/verify")
def verify_code(body: CodeVerifyIn, request: Request, response: Response, conn=Depends(get_db)):
    ip = client_ip(request)
    if rate_limited(conn, f"verify:ip:{ip}", 40, 900):
        raise too_many()
    code = "".join(ch for ch in body.code if ch.isdigit())
    error = None
    result = None
    with transaction(conn):
        req = one(conn, "SELECT * FROM otp_requests WHERE id = ?", (body.request_id,))
        if not req or req["consumed_at"] or req["expires_at"] <= iso():
            error = bad_request("That code has expired. Request a new one.", code="code_expired")
        elif req["attempts"] >= settings.otp_max_attempts:
            error = too_many("Too many incorrect tries. Request a new code.")
        else:
            conn.execute("UPDATE otp_requests SET attempts = attempts + 1 WHERE id = ?", (req["id"],))
            if not req["code_hash"] or len(code) != 6 or not hmac.compare_digest(req["code_hash"], otp_hash(req["id"], code)):
                remaining = settings.otp_max_attempts - req["attempts"] - 1
                if remaining <= 0:
                    conn.execute("UPDATE otp_requests SET consumed_at = ? WHERE id = ?", (iso(), req["id"]))
                    error = bad_request("That code isn't right, and it's now been used up. Request a new one.", code="code_expired")
                else:
                    error = bad_request(f"That code isn't right. {remaining} {'try' if remaining == 1 else 'tries'} left.",
                                        code="code_incorrect")
            else:
                error, result = _complete(conn, req, request, response)
                if not error:
                    conn.execute("UPDATE otp_requests SET consumed_at = ? WHERE id = ?", (iso(), req["id"]))
    if error:
        raise error
    return result


def _complete(conn, req: dict, request: Request, response: Response):
    now = iso()
    if req["purpose"] == "signup":
        profile = json.loads(req["payload"] or "{}")
        if one(conn, "SELECT id FROM users WHERE email = ?", (req["email"],)):
            return conflict("An account with this email already exists. Sign in instead.", code="account_exists"), None
        if phone_taken(conn, profile.get("phone")):
            return conflict("That mobile number is already linked to another account. Go back and use a different number.",
                            code="phone_taken"), None
        user = create_user(conn, email=req["email"], full_name=profile["full_name"], phone=profile["phone"],
                           password_hash=profile["password_hash"], verified=True, marketing=profile.get("marketing_opt_in", False))
        create_session(conn, user["id"], request, response)
        emails.send_welcome(conn, user)
        return None, {"user": public_user(user)}

    user = one(conn, "SELECT * FROM users WHERE id = ? AND is_active = 1", (req["user_id"],))
    if not user:
        return bad_request("That code has expired. Request a new one.", code="code_expired"), None
    conn.execute("UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?",
                 (now, now, user["id"]))
    if req["purpose"] == "login":
        create_session(conn, user["id"], request, response)
        return None, {"user": public_user(one(conn, "SELECT * FROM users WHERE id = ?", (user["id"],)))}
    token = random_token(32)
    conn.execute("INSERT INTO auth_tokens (token_hash, user_id, purpose, expires_at, created_at) VALUES (?, ?, 'reset', ?, ?)",
                 (sha256_hex(token), user["id"], iso_in(minutes=15), now))
    return None, {"reset_token": token}


@router.post("/signup")
def signup(body: SignupIn, request: Request, conn=Depends(get_db)):
    ip = client_ip(request)
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    name = clean_text(body.full_name, 80)
    fields = {}
    if not name:
        fields["full_name"] = "Enter your name."
    if not is_email(email):
        fields["email"] = "Enter a valid email address."
    if not is_phone(phone):
        fields["phone"] = "Enter a 10-digit Indian mobile number."
    problem = password_problem(body.password, email)
    if problem:
        fields["password"] = problem
    elif body.password != body.confirm_password:
        fields["confirm_password"] = "The two passwords don't match."
    if fields:
        raise bad_request("Some details need fixing.", fields=fields)
    if rate_limited(conn, f"signup:ip:{ip}", 10, 3600) or rate_limited(conn, f"otp:id:{email}", 3, 3600):
        raise too_many("Too many sign-up attempts. Please wait a while and try again.")

    existing = one(conn, "SELECT * FROM users WHERE email = ?", (email,))
    password_hash = None if existing else hash_password(body.password)
    with transaction(conn):
        if existing:
            emails.send_signup_existing(conn, email, existing["full_name"])
            request_id = _issue_code(conn, purpose="signup", email=email, ip=ip, deliver=False)
        else:
            payload = {"full_name": name, "phone": phone, "password_hash": password_hash, "marketing_opt_in": body.marketing_opt_in}
            request_id = _issue_code(conn, purpose="signup", email=email, name=name, payload=payload, ip=ip)
    return {"request_id": request_id, "message": "We've emailed a 6-digit code to confirm your address. It expires in 10 minutes."}


@router.post("/password/reset")
def reset_password(body: ResetIn, request: Request, response: Response, conn=Depends(get_db)):
    token = one(conn, "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = 'reset' AND used_at IS NULL AND expires_at > ?",
                (sha256_hex(body.reset_token), iso()))
    if not token:
        raise bad_request("This reset session has expired. Request a new code.", code="reset_expired")
    user = one(conn, "SELECT * FROM users WHERE id = ?", (token["user_id"],))
    problem = password_problem(body.password, user["email"])
    if problem:
        raise bad_request(problem, fields={"password": problem})
    if body.password != body.confirm_password:
        raise bad_request("The two passwords don't match.", fields={"confirm_password": "The two passwords don't match."})
    new_hash = hash_password(body.password)
    with transaction(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_reset_password = 0, updated_at = ? WHERE id = ?",
                     (new_hash, iso(), user["id"]))
        conn.execute("UPDATE auth_tokens SET used_at = ? WHERE token_hash = ?", (iso(), token["token_hash"]))
        revoke_all_sessions(conn, user["id"])
        create_session(conn, user["id"], request, response)
    return {"user": public_user(one(conn, "SELECT * FROM users WHERE id = ?", (user["id"],)))}


@router.post("/logout")
def logout(request: Request, response: Response, conn=Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL", (iso(), sha256_hex(token)))
    clear_session_cookie(response)
    return {"ok": True}
