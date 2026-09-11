"""Payment providers behind one interface.

Each provider can: start a payment for an order, report the gateway's own view
of whether it was paid, verify and parse a webhook, and (where supported) refund.
"""
import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..config import settings


class PaymentError(Exception):
    """The gateway call failed or returned something unusable."""


class SignatureError(Exception):
    """A webhook's signature didn't verify."""


@dataclass
class GatewayStatus:
    state: str  # "paid" | "pending" | "failed"
    payment_id: str | None = None
    amount_paise: int | None = None
    detail: str = ""


@dataclass
class WebhookEvent:
    event_id: str
    event_type: str
    gateway_order_id: str | None
    order_id: str | None
    payment_id: str | None
    state: str  # "paid" | "failed" | "ignored"
    amount_paise: int | None


class Provider:
    name = ""
    label = ""
    webhooks = False
    refunds = False

    def start(self, conn, order: dict, customer: dict) -> tuple[str, dict]:
        """Create the payment at the gateway. Returns (gateway_order_id, browser payload)."""
        raise NotImplementedError

    def status(self, conn, order: dict) -> GatewayStatus:
        raise NotImplementedError

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent:
        raise SignatureError("This provider doesn't accept webhooks.")

    def refund(self, conn, order: dict, amount_paise: int, note: str) -> str:
        raise PaymentError("Refunds aren't supported for this payment method.")


PROVIDER_NAMES = ("mock", "razorpay", "cashfree", "manual_upi")


def get_provider(name: str | None = None) -> Provider:
    from .cashfree import CashfreeProvider
    from .manual_upi import ManualUpiProvider
    from .mock import MockProvider
    from .razorpay import RazorpayProvider

    name = (name or settings.payment_provider).lower()
    registry = {"mock": MockProvider, "razorpay": RazorpayProvider, "cashfree": CashfreeProvider, "manual_upi": ManualUpiProvider}
    if name not in registry:
        raise PaymentError(f"Unknown payment provider '{name}'.")
    if name == "mock" and settings.is_production:
        raise PaymentError("The mock gateway is disabled in production.")
    return registry[name]()


def http_json(method: str, url: str, *, headers: dict | None = None, body: dict | None = None,
              basic_auth: tuple[str, str] | None = None, timeout: int = 15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if basic_auth:
        token = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise PaymentError(f"{exc.code} from gateway: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PaymentError(f"Couldn't reach the gateway: {exc}") from exc
    try:
        return json.loads(raw) if raw else {}
    except ValueError as exc:
        raise PaymentError("The gateway returned an unreadable response.") from exc
