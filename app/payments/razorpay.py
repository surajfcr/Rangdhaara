"""Razorpay Orders API + Checkout.js + webhooks.

Dashboard setup: Settings → Webhooks → add {PUBLIC_BASE_URL}/api/v1/webhooks/razorpay
with events payment.captured, payment.failed and order.paid, and put the secret
you choose there into RAZORPAY_WEBHOOK_SECRET.
"""
import hashlib
import hmac
import json

from ..config import settings
from . import GatewayStatus, PaymentError, Provider, SignatureError, WebhookEvent, http_json

API = "https://api.razorpay.com/v1"


class RazorpayProvider(Provider):
    name = "razorpay"
    label = "Razorpay"
    webhooks = True
    refunds = True

    def _auth(self) -> tuple[str, str]:
        if not (settings.razorpay_key_id and settings.razorpay_key_secret):
            raise PaymentError("Razorpay keys are not configured.")
        return settings.razorpay_key_id, settings.razorpay_key_secret

    def start(self, conn, order: dict, customer: dict) -> tuple[str, dict]:
        created = http_json("POST", f"{API}/orders", basic_auth=self._auth(), body={
            "amount": order["total_paise"],
            "currency": "INR",
            "receipt": order["id"][:40],
            "notes": {"order_id": order["id"]},
        })
        gateway_order_id = created.get("id")
        if not gateway_order_id:
            raise PaymentError("Razorpay didn't return an order id.")
        return gateway_order_id, {
            "key_id": settings.razorpay_key_id,
            "order_id": gateway_order_id,
            "amount": order["total_paise"],
            "currency": "INR",
            "name": settings.store_name,
            "description": f"Order #{order['id']}",
            "prefill": {"name": customer["name"], "email": customer["email"], "contact": customer["phone"]},
        }

    def status(self, conn, order: dict) -> GatewayStatus:
        data = http_json("GET", f"{API}/orders/{order['gateway_order_id']}/payments", basic_auth=self._auth())
        payments = data.get("items") or []
        for payment in payments:
            if payment.get("status") == "captured":
                return GatewayStatus("paid", payment["id"], int(payment["amount"]))
        for payment in payments:
            if payment.get("status") == "authorized":
                try:  # accounts with manual capture: capture the exact amount we expect
                    captured = http_json("POST", f"{API}/payments/{payment['id']}/capture", basic_auth=self._auth(),
                                         body={"amount": int(payment["amount"]), "currency": "INR"})
                    if captured.get("status") == "captured":
                        return GatewayStatus("paid", payment["id"], int(payment["amount"]))
                except PaymentError:
                    pass
                return GatewayStatus("pending", payment["id"], int(payment["amount"]), "authorized, not captured")
        if payments and all(p.get("status") == "failed" for p in payments):
            return GatewayStatus("failed", payments[-1].get("id"), int(payments[-1].get("amount") or 0))
        return GatewayStatus("pending")

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        secret = settings.razorpay_webhook_secret
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""
        if not secret or not hmac.compare_digest(expected, headers.get("x-razorpay-signature", "")):
            raise SignatureError("Razorpay signature mismatch")
        data = json.loads(body.decode("utf-8"))
        event = data.get("event", "")
        payload = data.get("payload") or {}
        payment = (payload.get("payment") or {}).get("entity") or {}
        order_entity = (payload.get("order") or {}).get("entity") or {}
        notes = payment.get("notes") if isinstance(payment.get("notes"), dict) else {}
        state = {"payment.captured": "paid", "order.paid": "paid", "payment.failed": "failed"}.get(event, "ignored")
        amount = payment.get("amount") or order_entity.get("amount_paid")
        return WebhookEvent(
            event_id=headers.get("x-razorpay-event-id") or hashlib.sha256(body).hexdigest(),
            event_type=event,
            gateway_order_id=payment.get("order_id") or order_entity.get("id"),
            order_id=order_entity.get("receipt") or notes.get("order_id"),
            payment_id=payment.get("id"),
            state=state,
            amount_paise=int(amount) if amount is not None else None,
        )

    def refund(self, conn, order: dict, amount_paise: int, note: str) -> str:
        data = http_json("POST", f"{API}/payments/{order['gateway_payment_id']}/refund", basic_auth=self._auth(),
                         body={"amount": amount_paise, "notes": {"reason": note[:200]}})
        if not data.get("id"):
            raise PaymentError("Razorpay didn't confirm the refund.")
        return data["id"]
