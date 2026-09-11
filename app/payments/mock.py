"""A local stand-in for a real gateway, for development only.

It exercises the same path production uses: a hosted payment page, then a
signed webhook, then server-side confirmation. It can also simulate a lost
webhook, so the reconciliation job and the "check again" button can be tested.
"""
import json

from ..db import iso, one
from ..security import random_token, sign, verify_signature
from . import GatewayStatus, Provider, SignatureError, WebhookEvent


class MockProvider(Provider):
    name = "mock"
    label = "Test gateway (no real money)"
    webhooks = True
    refunds = True

    def start(self, conn, order: dict, customer: dict) -> tuple[str, dict]:
        gateway_order_id = f"mock_{random_token(9)}"
        now = iso()
        conn.execute(
            "INSERT INTO mock_payments (gateway_order_id, order_id, amount_paise, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'created', ?, ?)",
            (gateway_order_id, order["id"], order["total_paise"], now, now),
        )
        return gateway_order_id, {"redirect_url": f"/pay/mock/{gateway_order_id}"}

    def status(self, conn, order: dict) -> GatewayStatus:
        row = one(conn, "SELECT * FROM mock_payments WHERE gateway_order_id = ?", (order["gateway_order_id"],))
        if not row:
            return GatewayStatus("pending", detail="no mock payment")
        if row["status"] == "paid":
            return GatewayStatus("paid", row["payment_id"], row["amount_paise"])
        if row["status"] == "failed":
            return GatewayStatus("failed", row["payment_id"], row["amount_paise"])
        return GatewayStatus("pending")

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        text = body.decode("utf-8")
        if not verify_signature(f"mockhook|{text}", headers.get("x-mock-signature", "")):
            raise SignatureError("bad mock signature")
        data = json.loads(text)
        return WebhookEvent(
            event_id=data["event_id"],
            event_type=data["event"],
            gateway_order_id=data["gateway_order_id"],
            order_id=data["order_id"],
            payment_id=data.get("payment_id"),
            state="paid" if data["event"] == "payment.captured" else "failed",
            amount_paise=data["amount_paise"],
        )

    def refund(self, conn, order: dict, amount_paise: int, note: str) -> str:
        return f"mock_rfnd_{random_token(6)}"

    @staticmethod
    def settle(conn, gateway_order_id: str, outcome: str) -> tuple[bytes, dict] | None:
        """Record the simulated result and build the signed webhook the gateway would send."""
        row = one(conn, "SELECT * FROM mock_payments WHERE gateway_order_id = ?", (gateway_order_id,))
        if not row or row["status"] == "paid":
            return None  # like a real gateway, a failed attempt can be retried; a paid one can't be paid twice
        payment_id = f"mock_pay_{random_token(8)}"
        status = "paid" if outcome == "paid" else "failed"
        conn.execute("UPDATE mock_payments SET status = ?, payment_id = ?, updated_at = ? WHERE gateway_order_id = ?",
                     (status, payment_id, iso(), gateway_order_id))
        body = json.dumps({
            "event_id": f"evt_{random_token(10)}",
            "event": "payment.captured" if status == "paid" else "payment.failed",
            "gateway_order_id": gateway_order_id,
            "order_id": row["order_id"],
            "payment_id": payment_id,
            "amount_paise": row["amount_paise"],
        })
        return body.encode("utf-8"), {"x-mock-signature": sign(f"mockhook|{body}")}
