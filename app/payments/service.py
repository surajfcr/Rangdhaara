"""Payment orchestration: start a payment, process webhooks idempotently, and
reconcile orders whose webhook never arrived.

The browser never tells the server a payment succeeded. A webhook only proves
the gateway sent it; before fulfilling, we ask the gateway's API for the
payment and check its amount against the order.
"""
import hashlib
import json
import traceback

from .. import orders
from ..db import all_rows, iso, iso_in, one, transaction
from . import PaymentError, SignatureError, get_provider

MAX_WEBHOOK_BYTES = 64 * 1024


def begin_payment(conn, order: dict, customer: dict) -> dict:
    provider = get_provider(order["payment_provider"])
    gateway_order_id, client = provider.start(conn, order, customer)
    conn.execute("UPDATE orders SET gateway_order_id = ?, gateway_client = ?, updated_at = ? WHERE id = ?",
                 (gateway_order_id, json.dumps(client), iso(), order["id"]))
    return client


def confirm_with_gateway(conn, order: dict) -> str:
    """Ask the gateway about this order; mark it paid only if the gateway says so."""
    if order["status"] not in ("pending_payment", "expired") or not order["gateway_order_id"]:
        return "not_applicable"
    try:
        provider = get_provider(order["payment_provider"])
        status = provider.status(conn, order)
    except PaymentError as exc:
        orders.add_note(conn, order["id"], f"Gateway check failed: {str(exc)[:200]}")
        return "gateway_unreachable"
    if status.state == "paid":
        return orders.mark_paid(conn, order["id"], payment_id=status.payment_id, amount_paise=status.amount_paise,
                                source=provider.name)
    return status.state


def handle_webhook(conn, provider_name: str, headers: dict, body: bytes) -> str:
    headers = {k.lower(): v for k, v in headers.items()}
    stored_headers = json.dumps({k: v for k, v in headers.items() if k.startswith(("x-", "content-type", "user-agent"))})
    body = body[:MAX_WEBHOOK_BYTES]
    try:
        provider = get_provider(provider_name)
        event = provider.parse_webhook(headers, body)
    except (SignatureError, PaymentError, ValueError, KeyError, TypeError) as exc:
        _store_rejected(conn, provider_name, stored_headers, body, type(exc).__name__)
        return "rejected"

    with transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO payment_events (provider, event_id, event_type, gateway_payment_id, state, amount_paise, "
            "signature_valid, headers, body, received_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (provider.name, event.event_id, event.event_type, event.payment_id, event.state, event.amount_paise,
             stored_headers, body.decode("utf-8", "replace"), iso()),
        )
        if cur.rowcount == 0:
            prior = one(conn, "SELECT processed_at FROM payment_events WHERE provider = ? AND event_id = ?",
                        (provider.name, event.event_id))
            if prior and prior["processed_at"]:
                return "duplicate"

    order = None
    if event.gateway_order_id:
        order = one(conn, "SELECT * FROM orders WHERE payment_provider = ? AND gateway_order_id = ?",
                    (provider.name, event.gateway_order_id))
    if not order and event.order_id:
        order = one(conn, "SELECT * FROM orders WHERE id = ? AND payment_provider = ?", (event.order_id, provider.name))

    error = None
    if not order:
        outcome = "unknown_order"
    elif event.state == "paid":
        try:
            outcome = confirm_with_gateway(conn, order)
        except Exception as exc:  # never 500 a gateway: it would retry forever; reconciliation picks it up
            traceback.print_exc()
            outcome, error = "error", str(exc)[:300]
    elif event.state == "failed":
        orders.add_note(conn, order["id"], f"Payment attempt failed ({event.event_type}). The customer can try again.")
        outcome = "payment_failed"
    else:
        outcome = "ignored"

    conn.execute(
        "UPDATE payment_events SET order_id = ?, outcome = ?, error = ?, processed_at = ? WHERE provider = ? AND event_id = ?",
        (order["id"] if order else None, outcome, error, iso(), provider.name, event.event_id),
    )
    return outcome


def _store_rejected(conn, provider_name: str, headers: str, body: bytes, reason: str) -> None:
    digest = hashlib.sha256(body).hexdigest()[:24]
    conn.execute(
        "INSERT OR IGNORE INTO payment_events (provider, event_id, event_type, signature_valid, headers, body, outcome, "
        "received_at, processed_at) VALUES (?, ?, '', 0, ?, ?, ?, ?, ?)",
        (provider_name[:20], f"rejected:{digest}:{iso()}", headers, body[:4096].decode("utf-8", "replace"),
         f"rejected_{reason}", iso(), iso()),
    )


def reconcile(conn) -> dict:
    """Catch payments whose webhook was lost: ask the gateway about stale unpaid orders."""
    candidates = all_rows(
        conn,
        "SELECT * FROM orders WHERE status IN ('pending_payment', 'expired') AND is_legacy = 0 "
        "AND gateway_order_id IS NOT NULL AND payment_provider NOT IN ('manual_upi') "
        "AND created_at <= ? AND created_at >= ?",
        (iso_in(minutes=-20), iso_in(hours=-48)),
    )
    results: dict[str, int] = {}
    for order in candidates:
        outcome = confirm_with_gateway(conn, order)
        results[outcome] = results.get(outcome, 0) + 1
    return results
