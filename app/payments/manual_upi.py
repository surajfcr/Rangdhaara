"""UPI transfer to the studio's own UPI ID, confirmed by a person.

For running before a gateway account is approved. The order stays
"Awaiting payment" until someone checks the bank statement and presses
"Mark as paid" in the admin panel — nothing is ever auto-confirmed.
"""
from urllib.parse import urlencode

import segno

from ..config import settings
from . import GatewayStatus, Provider


class ManualUpiProvider(Provider):
    name = "manual_upi"
    label = "UPI transfer (confirmed by the studio)"
    webhooks = False
    refunds = False

    def start(self, conn, order: dict, customer: dict) -> tuple[str, dict]:
        amount = f"{order['total_paise'] / 100:.2f}"
        uri = "upi://pay?" + urlencode({
            "pa": settings.upi_vpa,
            "pn": settings.upi_payee_name,
            "am": amount,
            "cu": "INR",
            "tn": f"Rangdhaara {order['id']}",
        })
        qr = segno.make(uri, error="m").svg_inline(scale=5, dark="#1C1917", light="#FFFFFF", border=2)
        return order["id"], {
            "vpa": settings.upi_vpa,
            "payee": settings.upi_payee_name,
            "amount_paise": order["total_paise"],
            "note": order["id"],
            "upi_uri": uri,
            "qr_svg": qr,
        }

    def status(self, conn, order: dict) -> GatewayStatus:
        return GatewayStatus("pending", detail="Waiting for the studio to confirm the UPI transfer")
