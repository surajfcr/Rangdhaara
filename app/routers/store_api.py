"""Storefront API: store bootstrap, catalogue, pincode lookup, cart pricing,
checkout, order status, gateway webhooks and the development payment page."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import emails, orders
from ..auth import client_ip, current_user, public_user
from ..catalogue import get_product, list_products
from ..config import settings
from ..db import all_rows, connect, get_db, one
from ..errors import ApiError, bad_request, not_found, too_many
from ..payments import PROVIDER_NAMES, PaymentError, get_provider
from ..payments.mock import MockProvider
from ..payments.service import begin_payment, confirm_with_gateway, handle_webhook
from ..pricing import price_cart
from ..security import is_pincode, order_access_token, rate_limited
from ..shipping import INDIAN_STATES, estimate_delivery, lookup_pincode

router = APIRouter()


class CartItem(BaseModel):
    variant_id: int
    qty: int = Field(default=1, ge=1, le=99)


class PriceIn(BaseModel):
    items: list[CartItem] = Field(default_factory=list, max_length=50)
    coupon_code: str | None = Field(default=None, max_length=40)
    pincode: str | None = Field(default=None, max_length=10)


class ContactIn(BaseModel):
    name: str = Field(max_length=80)
    email: str = Field(max_length=120)
    phone: str = Field(max_length=20)


class AddressIn(BaseModel):
    full_name: str = Field(max_length=80)
    phone: str = Field(max_length=20)
    line1: str = Field(max_length=160)
    line2: str = Field(default="", max_length=160)
    city: str = Field(max_length=60)
    state: str = Field(max_length=60)
    pincode: str = Field(max_length=10)
    save: bool = False


class CheckoutIn(BaseModel):
    items: list[CartItem] = Field(max_length=50)
    coupon_code: str | None = Field(default=None, max_length=40)
    contact: ContactIn
    address: AddressIn | None = None
    marketing_opt_in: bool = False


@router.get("/store")
def store(user=Depends(current_user)):
    provider = get_provider()
    return {
        "name": settings.store_name,
        "whatsapp_number": settings.whatsapp_number,
        "instagram_handle": settings.instagram_handle,
        "payment": {"provider": provider.name, "label": provider.label},
        "dev_mode": not settings.is_production,
        "email_dev_mode": not settings.smtp_configured and not settings.is_production,
        "states": INDIAN_STATES,
        "user": public_user(user) if user else None,
    }


@router.get("/catalogue")
def catalogue(conn=Depends(get_db)):
    return {
        "products": list_products(conn),
        "categories": all_rows(conn, "SELECT id, name FROM categories ORDER BY sort_order, name"),
    }


@router.get("/catalogue/{slug}")
def product(slug: str, conn=Depends(get_db)):
    item = get_product(conn, slug)
    if not item:
        raise not_found("That item isn't available any more.")
    return item


@router.get("/pincode/{pincode}")
def pincode(pincode: str, request: Request, conn=Depends(get_db)):
    if not is_pincode(pincode):
        raise bad_request("Enter a valid 6-digit PIN code.", code="invalid_pincode")
    if rate_limited(conn, f"pin:{client_ip(request)}", 60, 600):
        raise too_many()
    info = lookup_pincode(conn, pincode)
    if not info["valid"]:
        raise bad_request("We couldn't find that PIN code. Check it and try again.", code="unknown_pincode")
    info["delivery"] = estimate_delivery(pincode)
    return info


@router.post("/cart/price")
def cart_price(body: PriceIn, conn=Depends(get_db), user=Depends(current_user)):
    pin = body.pincode if body.pincode and is_pincode(body.pincode) else None
    return price_cart(conn, [i.model_dump() for i in body.items], body.coupon_code, pin, user["id"] if user else None)


@router.post("/checkout")
def checkout(body: CheckoutIn, request: Request, conn=Depends(get_db), user=Depends(current_user)):
    if rate_limited(conn, f"checkout:{client_ip(request)}", 20, 600):
        raise too_many("Too many checkout attempts. Please wait a few minutes.")
    try:
        provider = get_provider()
    except PaymentError as exc:
        raise ApiError(503, "payments_unavailable", str(exc))

    order, _ = orders.create_order(
        conn,
        items=[i.model_dump() for i in body.items],
        coupon_code=(body.coupon_code or "").strip() or None,
        contact=body.contact.model_dump(),
        address=body.address.model_dump() if body.address else None,
        user=user,
        marketing_opt_in=body.marketing_opt_in,
        provider_name=provider.name,
    )
    token = order_access_token(order["id"])
    result = {
        "order_id": order["id"],
        "token": token,
        "order_url": f"/order/{order['id']}?t={token}",
        "total_paise": order["total_paise"],
        "provider": provider.name,
        "client": None,
    }
    if order["total_paise"] == 0:
        orders.mark_paid(conn, order["id"], amount_paise=0, source="free order")
        return result

    customer = {"id": user["id"] if user else None, "name": order["customer_name"], "email": order["email"], "phone": order["phone"]}
    try:
        result["client"] = begin_payment(conn, order, customer)
    except PaymentError as exc:
        orders.cancel_order(conn, order["id"], None, f"Gateway unavailable: {str(exc)[:200]}")
        raise ApiError(502, "gateway_unavailable",
                       "We couldn't reach the payment gateway, so nothing was charged. Please try again in a moment.")
    if provider.name == "manual_upi":
        emails.send_order_awaiting_payment(conn, orders.get_order(conn, order["id"]), orders.get_items(conn, order["id"]))
    return result


def _viewable(conn, order_id: str, user: dict | None, token: str | None) -> dict:
    order = orders.get_order(conn, order_id)
    if not order or not orders.can_view(order, user, token):
        raise not_found("We couldn't find that order. Open it from the link in your email, or sign in.")
    return order


@router.get("/orders/{order_id}")
def order_status(order_id: str, t: str | None = None, conn=Depends(get_db), user=Depends(current_user)):
    return orders.serialize(conn, _viewable(conn, order_id, user, t))


@router.post("/orders/{order_id}/refresh")
def order_refresh(order_id: str, t: str | None = None, conn=Depends(get_db), user=Depends(current_user)):
    """"Check again": ask the gateway directly, for when the webhook is slow or lost."""
    order = _viewable(conn, order_id, user, t)
    if (order["status"] in ("pending_payment", "expired") and order["payment_provider"] not in ("manual_upi", "legacy")
            and not rate_limited(conn, f"refresh:{order_id}", 12, 60)):
        confirm_with_gateway(conn, order)
        order = orders.get_order(conn, order_id)
    return orders.serialize(conn, order)


@router.post("/webhooks/{provider_name}")
async def webhook(provider_name: str, request: Request):
    body = await request.body()
    if provider_name in PROVIDER_NAMES:
        await run_in_threadpool(_process_webhook, provider_name, dict(request.headers), body)
    return {"ok": True}  # always 200: a rejected or unknown event teaches a prober nothing


def _process_webhook(provider_name: str, headers: dict, body: bytes) -> str:
    conn = connect()
    try:
        return handle_webhook(conn, provider_name, headers, body)
    finally:
        conn.close()


class MockSettleIn(BaseModel):
    outcome: Literal["paid", "failed", "paid_no_webhook", "cancel"]


@router.post("/payments/mock/{gateway_order_id}/settle", include_in_schema=False)
def mock_settle(gateway_order_id: str, body: MockSettleIn, conn=Depends(get_db)):
    if settings.is_production:
        raise not_found()
    row = one(conn, "SELECT * FROM mock_payments WHERE gateway_order_id = ?", (gateway_order_id,))
    if not row:
        raise not_found("Unknown test payment.")
    if body.outcome != "cancel":
        settled = MockProvider.settle(conn, gateway_order_id, "paid" if body.outcome.startswith("paid") else "failed")
        if settled and body.outcome != "paid_no_webhook":
            payload, headers = settled
            handle_webhook(conn, "mock", headers, payload)
    return {"redirect_url": f"/order/{row['order_id']}?t={order_access_token(row['order_id'])}"}
