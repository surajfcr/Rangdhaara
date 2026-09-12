"""Pincode lookup and honest delivery estimates.

A single "4-5 business days" promise can't hold for both Pune and Port Blair,
so estimates are zoned from the studio's origin pincode and shown as dates.
"""
import json
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from .config import settings
from .db import iso, one, parse_iso, utcnow
from .security import is_pincode

IST = timezone(timedelta(hours=5, minutes=30))

INDIAN_STATES = [
    "Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
    "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry",
    "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal",
]

# Postal circle by leading digits. Three-digit entries override two-digit ones.
_PREFIX_STATE = {
    "11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab", "16": "Punjab",
    "17": "Himachal Pradesh", "18": "Jammu and Kashmir", "19": "Jammu and Kashmir",
    **{str(n): "Uttar Pradesh" for n in range(20, 29)},
    **{str(n): "Rajasthan" for n in range(30, 35)},
    **{str(n): "Gujarat" for n in range(36, 40)},
    **{str(n): "Maharashtra" for n in range(40, 45)},
    **{str(n): "Madhya Pradesh" for n in range(45, 49)},
    "49": "Chhattisgarh", "50": "Telangana", "51": "Andhra Pradesh", "52": "Andhra Pradesh", "53": "Andhra Pradesh",
    **{str(n): "Karnataka" for n in range(56, 60)},
    **{str(n): "Tamil Nadu" for n in range(60, 65)},
    "67": "Kerala", "68": "Kerala", "69": "Kerala",
    **{str(n): "West Bengal" for n in range(70, 75)},
    "75": "Odisha", "76": "Odisha", "77": "Odisha", "78": "Assam",
    **{str(n): "Bihar" for n in range(80, 86)},
}
_PREFIX3_STATE = {
    "160": "Chandigarh", "194": "Ladakh", "246": "Uttarakhand", "247": "Uttarakhand", "248": "Uttarakhand",
    "249": "Uttarakhand", "262": "Uttarakhand", "263": "Uttarakhand", "403": "Goa", "605": "Puducherry",
    "682": "Kerala", "737": "Sikkim", "744": "Andaman and Nicobar Islands", "790": "Arunachal Pradesh",
    "791": "Arunachal Pradesh", "792": "Arunachal Pradesh", "793": "Meghalaya", "794": "Meghalaya",
    "795": "Manipur", "796": "Mizoram", "797": "Nagaland", "798": "Nagaland", "799": "Tripura",
    **{str(n): "Jharkhand" for n in (814, 815, 816, 825, 826, 827, 828, 829, 831, 832, 833, 834, 835)},
}
_METRO_PREFIXES = {"110", "400", "411", "560", "600", "500", "700", "380"}
_REMOTE_PREFIX2 = {"18", "19", "78", "79"}
_REMOTE_PREFIX3 = {"194", "737", "744", "790", "791", "792", "793", "794", "795", "796", "797", "798", "799"}


def state_for_pincode(pincode: str) -> str | None:
    return _PREFIX3_STATE.get(pincode[:3]) or _PREFIX_STATE.get(pincode[:2])


def lookup_pincode(conn, pincode: str) -> dict:
    """City and state for a pincode, via India Post with a local fallback. Cached for 30 days."""
    if not is_pincode(pincode):
        return {"pincode": pincode, "valid": False}
    cached = one(conn, "SELECT * FROM pincode_cache WHERE pincode = ?", (pincode,))
    if cached and parse_iso(cached["fetched_at"]) > utcnow() - timedelta(days=30):
        return _shape(pincode, cached["city"], cached["state"], json.loads(cached["areas"] or "[]"), cached["source"])

    city, state, areas, source = None, None, [], "prefix"
    try:
        req = urllib.request.Request(
            f"https://api.postalpincode.in/pincode/{pincode}", headers={"User-Agent": "Rangdhara/1.0"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        offices = (payload[0] or {}).get("PostOffice") or [] if payload else []
        if offices:
            city = Counter(o.get("District") for o in offices if o.get("District")).most_common(1)[0][0]
            state = Counter(o.get("State") for o in offices if o.get("State")).most_common(1)[0][0]
            areas = sorted({o["Name"] for o in offices if o.get("Name")})[:20]
            source = "indiapost"
    except Exception:
        pass  # network or API trouble: fall back to the postal-circle table below

    if source == "prefix":
        state = state_for_pincode(pincode)
        if state is None:
            return {"pincode": pincode, "valid": False}
    else:
        conn.execute(
            "INSERT INTO pincode_cache (pincode, city, state, areas, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pincode) DO UPDATE SET city = excluded.city, state = excluded.state, areas = excluded.areas, "
            "source = excluded.source, fetched_at = excluded.fetched_at",
            (pincode, city, state, json.dumps(areas), source, iso()),
        )
    return _shape(pincode, city, state, areas, source)


def _shape(pincode, city, state, areas, source) -> dict:
    return {"pincode": pincode, "valid": True, "city": city or "", "state": state or "", "areas": areas, "source": source}


def _add_business_days(start: date, days: int) -> date:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() != 6:  # couriers don't deliver on Sundays
            added += 1
    return current


def estimate_delivery(pincode: str, today: date | None = None) -> dict | None:
    if not is_pincode(pincode):
        return None
    origin = settings.origin_pincode
    if pincode[:3] == origin[:3]:
        zone, low, high = "local", 1, 2
    elif pincode[:2] in _REMOTE_PREFIX2 or pincode[:3] in _REMOTE_PREFIX3:
        zone, low, high = "remote", 6, 9
    elif state_for_pincode(pincode) and state_for_pincode(pincode) == state_for_pincode(origin):
        zone, low, high = "state", 2, 4
    elif pincode[:3] in _METRO_PREFIXES:
        zone, low, high = "metro", 3, 5
    else:
        zone, low, high = "national", 4, 7
    low += settings.dispatch_days
    high += settings.dispatch_days
    start = today or datetime.now(IST).date()
    min_date = _add_business_days(start, low)
    max_date = _add_business_days(start, high)
    return {
        "zone": zone,
        "min_days": low,
        "max_days": high,
        "min_date": min_date.isoformat(),
        "max_date": max_date.isoformat(),
        "label": f"Arrives {min_date.strftime('%a %d %b')} – {max_date.strftime('%a %d %b')}",
    }
