"""Cashfree Payment Gateway (API version 2023-08-01) + JS SDK v3 + webhooks.

Dashboard setup: Developers → Webhooks → add {PUBLIC_BASE_URL}/api/v1/webhooks/cashfree
for payment success, failed and user-dropped events. Webhooks are signed with
your secret key, so no separate webhook secret is needed.
"""
import base64
import hashlib
import hmac
import json

from ..config import settings
from ..emails import link
from ..security import sha256_hex
from . import GatewayStatus, PaymentError, Provider, SignatureError, WebhookEvent, http_json

API_VERSION = "2023-08-01"


class CashfreeProvider(Provider):
    name = "cashfree"
    label = "Cashfree"
    webhooks = True
    refunds = True

    @property
    def _base(self) -> str:
        return "https://api.cashfree.com/pg" if settings.cashfree_env == "production" else "https://sandbox.cashfree.com/pg"

    def _headers(self) -> dict:
        if not (settings.cashfree_app_id and settings.cashfree_secret_key):
            raise PaymentError("Cashfree keys are not configured.")
        return {
            "x-client-id": settings.cashfree_app_id,
            "x-client-secret": settings.cashfree_secret_key,
            "x-api-version": API_VERSION,
        }

    def start(self, conn, order: dict, customer: dict) -> tuple[str, dict]:
        meta = {"return_url": link(f"/order/{order['id']}")}
        if settings.public_base_url.startswith("https://"):
            meta["notify_url"] = link("/api/v1/webhooks/cashfree")
        created = http_json("POST", f"{self._base}/orders", headers=self._headers(), body={
            "order_id": order["id"],
            "order_amount": round(order["total_paise"] / 100, 2),
            "order_currency": "INR",
            "customer_details": {
                "customer_id": customer.get("id") or f"guest_{sha256_hex(customer['email'])[:16]}",
                "customer_email": customer["email"],
                "customer_phone": customer["phone"],
                "customer_name": customer["name"],
            },
            "order_meta": meta,
            "order_note": f"Rangdhaara order {order['id']}",
        })
        session_id = created.get("payment_session_id")
        if not session_id:
            raise PaymentError("Cashfree didn't return a payment session.")
        return order["id"], {
            "mode": "production" if settings.cashfree_env == "production" else "sandbox",
            "payment_session_id": session_id,
        }

    def status(self, conn, order: dict) -> GatewayStatus:
        payments = http_json("GET", f"{self._base}/orders/{order['id']}/payments", headers=self._headers())
        if not isinstance(payments, list):
            payments = []
        for payment in payments:
            if payment.get("payment_status") == "SUCCESS":
                return GatewayStatus("paid", str(payment.get("cf_payment_id")), round(float(payment["payment_amount"]) * 100))
        if any(p.get("payment_status") in ("PENDING", "NOT_ATTEMPTED") for p in payments):
            return GatewayStatus("pending")
        if payments:
            return GatewayStatus("failed", str(payments[-1].get("cf_payment_id")))
        return GatewayStatus("pending")

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        timestamp = headers.get("x-webhook-timestamp", "")
        secret = settings.cashfree_secret_key
        expected = base64.b64encode(hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()).decode() if secret else ""
        if not secret or not hmac.compare_digest(expected, headers.get("x-webhook-signature", "")):
            raise SignatureError("Cashfree signature mismatch")
        data = json.loads(body.decode("utf-8"))
        kind = data.get("type", "")
        inner = data.get("data") or {}
        order_entity = inner.get("order") or {}
        payment = inner.get("payment") or {}
        state = {
            "PAYMENT_SUCCESS_WEBHOOK": "paid",
            "PAYMENT_FAILED_WEBHOOK": "failed",
            "PAYMENT_USER_DROPPED_WEBHOOK": "failed",
        }.get(kind, "ignored")
        amount = payment.get("payment_amount") or order_entity.get("order_amount")
        payment_id = str(payment.get("cf_payment_id")) if payment.get("cf_payment_id") else None
        return WebhookEvent(
            event_id=headers.get("x-idempotency-key") or f"{kind}:{payment_id or order_entity.get('order_id')}",
            event_type=kind,
            gateway_order_id=order_entity.get("order_id"),
            order_id=order_entity.get("order_id"),
            payment_id=payment_id,
            state=state,
            amount_paise=round(float(amount) * 100) if amount is not None else None,
        )

    def refund(self, conn, order: dict, amount_paise: int, note: str) -> str:
        data = http_json("POST", f"{self._base}/orders/{order['id']}/refunds", headers=self._headers(), body={
            "refund_amount": round(amount_paise / 100, 2),
            "refund_id": f"rf_{order['id']}"[:40],
            "refund_note": (note or "Refund")[:100],
        })
        ref = data.get("cf_refund_id") or data.get("refund_id")
        if not ref:
            raise PaymentError("Cashfree didn't confirm the refund.")
        return str(ref)
