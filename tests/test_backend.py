"""End-to-end checks for the Rangdhaara backend.

Run from the project folder:
    python -m unittest discover -s tests -v

Uses a throwaway database, a fake legacy export and the mock payment gateway.
Nothing in data/ or storage/ is touched.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="rangdhaara-test-"))
os.environ.update({
    "APP_ENV": "development",
    "PUBLIC_BASE_URL": "http://testserver",
    "DATABASE_PATH": str(TMP / "test.db"),
    "DATA_DIR": str(TMP / "data"),
    "STORAGE_DIR": str(TMP / "storage"),
    "PAYMENT_PROVIDER": "mock",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "STORE_ADMIN_EMAIL": "owner@example.com",
    "SECRET_KEY": "test-only-secret-key-0123456789-abcdefghijklmnop",
})
sys.path.insert(0, str(ROOT))

_legacy = TMP / "data" / "legacy"
_legacy.mkdir(parents=True)
(_legacy / "users.json").write_text(json.dumps([{
    "id": "usr_1", "fullName": "Legacy Customer", "phone": "9876543210", "email": "legacy@example.com",
    "address": "12 Old Street", "city": "PUNE", "state": "maharashtra", "pincode": "411001", "password": "oldpass123",
}]))
(_legacy / "orders.json").write_text(json.dumps([{
    "orderId": "RANG-10001", "customerName": "Legacy Customer", "email": "legacy@example.com", "phone": "9876543210",
    "address": "12 Old Street", "city": "Pune", "pincode": "411001",
    "items": [{"title": "Old Canvas", "typeLabel": "Ready Product", "quantity": 1, "price": 999}], "totalAmount": 999,
}]))

from fastapi.testclient import TestClient  # noqa: E402

from app import jobs  # noqa: E402
from app import orders as order_logic  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, iso_in, one, scalar, transaction  # noqa: E402
from app.main import app  # noqa: E402
from app.payments.mock import MockProvider  # noqa: E402
from app.payments.service import handle_webhook  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.shipping import IST  # noqa: E402
from app.users import create_user  # noqa: E402

H = {"X-Requested-With": "rangdhaara"}
PASSWORD = "Correct-Horse-9"
CONTACT = {"name": "Asha Rao", "email": "asha@example.com", "phone": "9820012345"}
ADDRESS = {"full_name": "Asha Rao", "phone": "9820012345", "line1": "Flat 4, Lotus Apartments", "line2": "",
           "city": "Pune", "state": "Maharashtra", "pincode": "411001"}

_lifespan = None


def setUpModule():
    global _lifespan
    _lifespan = TestClient(app)
    _lifespan.__enter__()


def tearDownModule():
    _lifespan.__exit__(None, None, None)
    shutil.rmtree(TMP, ignore_errors=True)


def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def post(c, path, body=None):
    return c.post(path, json=body if body is not None else {}, headers=H)


def sql(query, params=()):
    conn = connect()
    try:
        return conn.execute(query, params)
    finally:
        conn.close()


def value(query, params=()):
    conn = connect()
    try:
        return scalar(conn, query, params)
    finally:
        conn.close()


def row(query, params=()):
    conn = connect()
    try:
        return one(conn, query, params)
    finally:
        conn.close()


def variant_of(product_id):
    return value("SELECT id FROM product_variants WHERE product_id = ? ORDER BY position, id LIMIT 1", (product_id,))


def last_code(email):
    subject = value("SELECT subject FROM email_outbox WHERE to_email = ? AND kind LIKE 'otp_%' ORDER BY id DESC LIMIT 1", (email,))
    return re.match(r"^(\d{6})", subject).group(1)


def make_user(email, role="customer", password=PASSWORD):
    existing = row("SELECT * FROM users WHERE email = ?", (email,))
    if existing:
        return existing
    conn = connect()
    try:
        with transaction(conn):
            return create_user(conn, email=email, full_name=email.split("@")[0].title(), password_hash=hash_password(password),
                               verified=True, role=role)
    finally:
        conn.close()


def login(c, email, password=PASSWORD):
    r = post(c, "/api/v1/auth/login", {"identifier": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def checkout(c, items, contact=CONTACT, address=ADDRESS, coupon=None, marketing=False):
    return post(c, "/api/v1/checkout", {"items": items, "contact": contact, "address": address,
                                        "coupon_code": coupon, "marketing_opt_in": marketing})


def settle(c, redirect_url, outcome="paid"):
    return post(c, f"/api/v1/payments/mock/{redirect_url.rsplit('/', 1)[1]}/settle", {"outcome": outcome})


def reset_stock():
    sql("UPDATE product_variants SET on_hand = 5 WHERE on_hand IS NOT NULL AND product_id NOT LIKE 'workshop%'")
    sql("UPDATE stock_reservations SET status = 'released' WHERE status = 'active'")


class Case(unittest.TestCase):
    def setUp(self):
        sql("DELETE FROM rate_events")


class SurfaceTests(Case):
    def test_private_files_are_not_served(self):
        c = client()
        for path in ("/database/users.json", "/data/legacy/users.json", "/app/config.py", "/.env", "/manage.py",
                     "/data/rangdhaara.db", "/storage/videos/a.mp4", "/server.py", "/assets/../app/config.py"):
            self.assertIn(c.get(path).status_code, (404, 405), path)
        self.assertEqual(c.get("/assets/css/styles.css").status_code, 200)
        self.assertEqual(c.get("/").status_code, 200)

    def test_security_headers_and_csrf(self):
        c = client()
        r = c.get("/")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(c.post("/api/v1/cart/price", json={"items": []}).status_code, 403)
        self.assertEqual(post(c, "/api/v1/cart/price", {"items": []}).status_code, 200)

    def test_catalogue_hides_drafts_and_prices_come_from_the_server(self):
        c = client()
        products = c.get("/api/v1/catalogue").json()["products"]
        ids = {p["id"] for p in products}
        self.assertIn("rtb-ta-03", ids)
        self.assertNotIn("draft-shiva-relief", ids)
        self.assertNotIn("course-palette-knife", ids)
        starry = next(p for p in products if p["id"] == "rtb-ta-03")
        self.assertEqual(starry["price_paise"], 320000)
        priced = post(c, "/api/v1/cart/price", {"items": [{"variant_id": starry["variants"][0]["id"], "qty": 2, "price": 1}]}).json()
        self.assertEqual(priced["total_paise"], 640000)

    def test_coupons_are_checked_on_the_server(self):
        c = client()
        items = [{"variant_id": variant_of("rtb-ta-03"), "qty": 1}]
        good = post(c, "/api/v1/cart/price", {"items": items, "coupon_code": "rangdhaara10"}).json()
        self.assertTrue(good["coupon"]["applied"])
        self.assertEqual(good["discount_paise"], 32000)
        bad = post(c, "/api/v1/cart/price", {"items": items, "coupon_code": "FREEMONEY"}).json()
        self.assertFalse(bad["coupon"]["applied"])
        self.assertEqual(bad["total_paise"], 320000)

    def test_pincode_lookup_and_estimate(self):
        c = client()
        info = c.get("/api/v1/pincode/431001").json()
        self.assertEqual(info["state"], "Maharashtra")
        self.assertEqual(info["delivery"]["zone"], "local")
        self.assertEqual(c.get("/api/v1/pincode/000000").status_code, 400)


class AuthTests(Case):
    def test_signup_needs_the_emailed_code(self):
        c = client()
        r = post(c, "/api/v1/auth/signup", {"full_name": "Meera Joshi", "email": "meera@example.com", "phone": "9811111111",
                                            "password": "Tulips-in-bloom-7", "confirm_password": "Tulips-in-bloom-7"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(c.get("/api/v1/auth/session").json()["user"])
        code = last_code("meera@example.com")
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(post(c, "/api/v1/auth/code/verify", {"request_id": r.json()["request_id"], "code": wrong}).status_code, 400)
        good = post(c, "/api/v1/auth/code/verify", {"request_id": r.json()["request_id"], "code": code})
        self.assertEqual(good.status_code, 200, good.text)
        user = c.get("/api/v1/auth/session").json()["user"]
        self.assertEqual(user["email"], "meera@example.com")
        self.assertTrue(user["email_verified"])

    def test_signup_rejects_weak_or_mismatched_passwords(self):
        c = client()
        weak = post(c, "/api/v1/auth/signup", {"full_name": "A", "email": "weak@example.com", "phone": "9822222222",
                                               "password": "password123", "confirm_password": "password123"})
        self.assertIn("password", weak.json()["error"]["fields"])
        mismatch = post(c, "/api/v1/auth/signup", {"full_name": "A", "email": "weak@example.com", "phone": "9822222222",
                                                   "password": "Strong-pass-42", "confirm_password": "Strong-pass-43"})
        self.assertIn("confirm_password", mismatch.json()["error"]["fields"])

    def test_code_requests_dont_reveal_which_accounts_exist(self):
        make_user("known@example.com")
        c = client()
        known = post(c, "/api/v1/auth/code", {"identifier": "known@example.com", "purpose": "login"}).json()
        unknown = post(c, "/api/v1/auth/code", {"identifier": "nobody@example.com", "purpose": "login"}).json()
        self.assertEqual(known["message"], unknown["message"])
        self.assertEqual(value("SELECT COUNT(*) FROM email_outbox WHERE to_email = 'nobody@example.com'"), 0)
        self.assertEqual(post(c, "/api/v1/auth/code/verify", {"request_id": unknown["request_id"], "code": "123456"}).status_code, 400)

    def test_code_is_used_up_after_five_wrong_tries(self):
        make_user("locked@example.com")
        c = client()
        request_id = post(c, "/api/v1/auth/code", {"identifier": "locked@example.com", "purpose": "login"}).json()["request_id"]
        code = last_code("locked@example.com")
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(5):
            post(c, "/api/v1/auth/code/verify", {"request_id": request_id, "code": wrong})
        self.assertNotEqual(post(c, "/api/v1/auth/code/verify", {"request_id": request_id, "code": code}).status_code, 200)

    def test_password_sign_in_and_out(self):
        make_user("pat@example.com")
        c = client()
        wrong = post(c, "/api/v1/auth/login", {"identifier": "pat@example.com", "password": "not-it-at-all"})
        self.assertEqual(wrong.status_code, 401)
        login(c, "pat@example.com")
        self.assertIsNotNone(c.get("/api/v1/auth/session").json()["user"])
        post(c, "/api/v1/auth/logout")
        self.assertIsNone(c.get("/api/v1/auth/session").json()["user"])

    def test_imported_accounts_must_reset_their_password(self):
        c = client()
        r = post(c, "/api/v1/auth/login", {"identifier": "9876543210", "password": "oldpass123"})
        self.assertEqual(r.status_code, 409, r.text)
        error = r.json()["error"]
        self.assertEqual(error["code"], "password_reset_required")
        token = post(c, "/api/v1/auth/code/verify", {"request_id": error["request_id"], "code": last_code("legacy@example.com")}).json()["reset_token"]
        mismatch = post(c, "/api/v1/auth/password/reset", {"reset_token": token, "password": "New-garden-path-5", "confirm_password": "nope"})
        self.assertEqual(mismatch.status_code, 400)
        done = post(c, "/api/v1/auth/password/reset", {"reset_token": token, "password": "New-garden-path-5", "confirm_password": "New-garden-path-5"})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertFalse(done.json()["user"]["must_reset_password"])
        other = client()
        self.assertEqual(post(other, "/api/v1/auth/login", {"identifier": "legacy@example.com", "password": "oldpass123"}).status_code, 401)
        login(other, "legacy@example.com", "New-garden-path-5")
        legacy_order = row("SELECT * FROM orders WHERE id = 'RANG-10001'")
        self.assertEqual((legacy_order["status"], legacy_order["needs_review"]), ("pending_payment", "legacy_unverified_payment"))
        self.assertEqual(value("SELECT state FROM addresses a JOIN users u ON u.id = a.user_id WHERE u.email = 'legacy@example.com'"), "Maharashtra")
        self.assertIsNone(value("SELECT 1 FROM users WHERE password_hash = 'oldpass123'"))

    def test_password_reset_signs_out_other_devices(self):
        make_user("devices@example.com")
        laptop = client()
        login(laptop, "devices@example.com")
        phone = client()
        request_id = post(phone, "/api/v1/auth/code", {"identifier": "devices@example.com", "purpose": "reset"}).json()["request_id"]
        token = post(phone, "/api/v1/auth/code/verify", {"request_id": request_id, "code": last_code("devices@example.com")}).json()["reset_token"]
        post(phone, "/api/v1/auth/password/reset", {"reset_token": token, "password": "Brand-new-pass-8", "confirm_password": "Brand-new-pass-8"})
        self.assertIsNone(laptop.get("/api/v1/auth/session").json()["user"])
        self.assertIsNotNone(phone.get("/api/v1/auth/session").json()["user"])


class CheckoutTests(Case):
    def setUp(self):
        super().setUp()
        reset_stock()

    def test_paid_checkout_holds_then_commits_stock(self):
        c = client()
        variant = variant_of("rtb-ta-03")
        r = checkout(c, [{"variant_id": variant, "qty": 2}])
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["total_paise"], 640000)
        held = post(c, "/api/v1/cart/price", {"items": [{"variant_id": variant, "qty": 1}]}).json()["lines"][0]["available"]
        self.assertEqual(held, 3)
        self.assertEqual(settle(c, data["client"]["redirect_url"]).status_code, 200)
        order = c.get(f"/api/v1/orders/{data['order_id']}?t={data['token']}").json()
        self.assertEqual(order["status"], "paid")
        self.assertEqual(value("SELECT on_hand FROM product_variants WHERE id = ?", (variant,)), 3)
        self.assertEqual(value("SELECT COUNT(*) FROM email_outbox WHERE kind = 'order_confirmed' AND html LIKE ?", (f"%{data['order_id']}%",)), 1)
        self.assertEqual(value("SELECT COUNT(*) FROM email_outbox WHERE kind = 'admin_new_order' AND subject LIKE ?", (f"%{data['order_id']}%",)), 1)
        self.assertIsNotNone(value("SELECT a.id FROM addresses a JOIN users u ON u.id = a.user_id WHERE u.email = 'asha@example.com'"))
        receipt = c.get(f"/order/{data['order_id']}/receipt?t={data['token']}")
        self.assertIn("Payment receipt", receipt.text)

    def test_order_links_need_the_right_token(self):
        data = checkout(client(), [{"variant_id": variant_of("rtb-km-flatlay"), "qty": 1}]).json()
        stranger = client()
        self.assertEqual(stranger.get(f"/api/v1/orders/{data['order_id']}").status_code, 404)
        self.assertEqual(stranger.get(f"/api/v1/orders/{data['order_id']}?t=wrong").status_code, 404)
        self.assertEqual(stranger.get(f"/api/v1/orders/{data['order_id']}?t={data['token']}").status_code, 200)

    def test_duplicate_and_forged_webhooks_change_nothing(self):
        c = client()
        variant = variant_of("rtb-km-mango-crate")
        data = checkout(c, [{"variant_id": variant, "qty": 1}]).json()
        gateway_order_id = data["client"]["redirect_url"].rsplit("/", 1)[1]
        conn = connect()
        try:
            body, headers = MockProvider.settle(conn, gateway_order_id, "paid")
            self.assertEqual(handle_webhook(conn, "mock", headers, body), "paid")
            self.assertEqual(handle_webhook(conn, "mock", headers, body), "duplicate")
        finally:
            conn.close()
        forged = c.post("/api/v1/webhooks/mock", content=body, headers={"x-mock-signature": "forged", "content-type": "application/json"})
        self.assertEqual(forged.status_code, 200)
        self.assertGreaterEqual(value("SELECT COUNT(*) FROM payment_events WHERE signature_valid = 0"), 1)
        self.assertEqual(value("SELECT on_hand FROM product_variants WHERE id = ?", (variant,)), 4)

    def test_lost_webhook_is_recovered_by_checking_again(self):
        c = client()
        data = checkout(c, [{"variant_id": variant_of("rtb-cdd-01"), "qty": 1}]).json()
        settle(c, data["client"]["redirect_url"], "paid_no_webhook")
        url = f"/api/v1/orders/{data['order_id']}?t={data['token']}"
        self.assertEqual(c.get(url).json()["status"], "pending_payment")
        self.assertEqual(post(c, f"/api/v1/orders/{data['order_id']}/refresh?t={data['token']}").json()["status"], "paid")

    def test_amount_mismatch_is_flagged_not_fulfilled(self):
        c = client()
        data = checkout(c, [{"variant_id": variant_of("diy-kit-02"), "qty": 1}]).json()
        gateway_order_id = data["client"]["redirect_url"].rsplit("/", 1)[1]
        sql("UPDATE mock_payments SET amount_paise = amount_paise - 100 WHERE gateway_order_id = ?", (gateway_order_id,))
        settle(c, data["client"]["redirect_url"])
        order = row("SELECT status, needs_review FROM orders WHERE id = ?", (data["order_id"],))
        self.assertEqual((order["status"], order["needs_review"]), ("pending_payment", "amount_mismatch"))
        self.assertGreaterEqual(value("SELECT COUNT(*) FROM email_outbox WHERE kind = 'admin_alert' AND subject LIKE ?", (f"%{data['order_id']}%",)), 1)

    def test_failed_payment_can_be_retried(self):
        c = client()
        data = checkout(c, [{"variant_id": variant_of("diy-kit-03"), "qty": 1}]).json()
        settle(c, data["client"]["redirect_url"], "failed")
        order = c.get(f"/api/v1/orders/{data['order_id']}?t={data['token']}").json()
        self.assertEqual(order["status"], "pending_payment")
        self.assertTrue(order["payment"]["can_pay"])
        settle(c, order["payment"]["client"]["redirect_url"], "paid")
        self.assertEqual(c.get(f"/api/v1/orders/{data['order_id']}?t={data['token']}").json()["status"], "paid")

    def test_hold_blocks_a_second_buyer_until_it_lapses(self):
        variant = variant_of("diy-kit-04")
        sql("UPDATE product_variants SET on_hand = 1 WHERE id = ?", (variant,))
        first = checkout(client(), [{"variant_id": variant, "qty": 1}])
        self.assertEqual(first.status_code, 200, first.text)
        buyer_b = client()
        other = {"name": "Rohan", "email": "rohan@example.com", "phone": "9855555555"}
        second = checkout(buyer_b, [{"variant_id": variant, "qty": 1}], contact=other)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "cart_changed")
        sql("UPDATE stock_reservations SET expires_at = ? WHERE order_id = ?", (iso_in(minutes=-1), first.json()["order_id"]))
        self.assertEqual(checkout(buyer_b, [{"variant_id": variant, "qty": 1}], contact=other).status_code, 200)

    def test_customer_input_is_escaped_in_emails(self):
        c = client()
        contact = {"name": "<img src=x onerror=alert(1)>", "email": "xss@example.com", "phone": "9866666666"}
        address = {**ADDRESS, "full_name": "<script>alert(1)</script>"}
        data = checkout(c, [{"variant_id": variant_of("diy-kit-05"), "qty": 1}], contact=contact, address=address).json()
        settle(c, data["client"]["redirect_url"])
        html = value("SELECT html FROM email_outbox WHERE kind = 'order_confirmed' AND to_email = 'xss@example.com' ORDER BY id DESC LIMIT 1")
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;img", html)

    def test_unpaid_orders_expire_and_get_one_reminder(self):
        contact = {"name": "Reminder", "email": "reminder@example.com", "phone": "9877777777"}
        data = checkout(client(), [{"variant_id": variant_of("rtb-km-flatlay"), "qty": 1}], contact=contact, marketing=True).json()
        sql("UPDATE orders SET created_at = ? WHERE id = ?", (iso_in(hours=-5), data["order_id"]))
        conn = connect()
        try:
            self.assertGreaterEqual(order_logic.expire_unpaid(conn), 1)
            self.assertEqual(value("SELECT status FROM orders WHERE id = ?", (data["order_id"],)), "expired")
            order_logic.send_abandoned_cart_emails(conn)
            order_logic.send_abandoned_cart_emails(conn)
        finally:
            conn.close()
        reminders = value("SELECT COUNT(*) FROM email_outbox WHERE kind = 'abandoned_cart' AND to_email = 'reminder@example.com'")
        self.assertEqual(reminders, 1)
        self.assertIn("/?cart=", value("SELECT html FROM email_outbox WHERE kind = 'abandoned_cart' AND to_email = 'reminder@example.com'"))

    def test_manual_upi_waits_for_the_studio(self):
        settings.payment_provider = "manual_upi"
        try:
            c = client()
            contact = {"name": "Upi Buyer", "email": "upi@example.com", "phone": "9888888888"}
            data = checkout(c, [{"variant_id": variant_of("diy-kit-01"), "qty": 1}], contact=contact).json()
            self.assertIn("<svg", data["client"]["qr_svg"])
            self.assertEqual(data["client"]["vpa"], settings.upi_vpa)
            self.assertEqual(value("SELECT COUNT(*) FROM email_outbox WHERE kind = 'order_awaiting_payment' AND to_email = 'upi@example.com'"), 1)
            self.assertEqual(post(c, f"/api/v1/orders/{data['order_id']}/refresh?t={data['token']}").json()["status"], "pending_payment")
            make_user("upi-owner@example.com", role="admin")
            owner = client()
            login(owner, "upi-owner@example.com")
            paid = post(owner, f"/api/v1/admin/orders/{data['order_id']}/mark-paid", {"reference": "UPI 4521"})
            self.assertEqual(paid.status_code, 200, paid.text)
            self.assertEqual(paid.json()["status"], "paid")
        finally:
            settings.payment_provider = "mock"


class AcademyTests(Case):
    @classmethod
    def setUpClass(cls):
        sql("DELETE FROM rate_events")
        make_user("academy-owner@example.com", role="admin")
        cls.owner = client()
        login(cls.owner, "academy-owner@example.com")
        cls.course_id = value("SELECT id FROM courses WHERE product_id = 'course-texture-paste'")
        detail = post(cls.owner, f"/api/v1/admin/courses/{cls.course_id}/modules", {"title": "Getting started"}).json()
        module_id = detail["modules"][0]["id"]
        detail = post(cls.owner, f"/api/v1/admin/modules/{module_id}/lessons", {"title": "Mixing the base paste"}).json()
        cls.lesson_id = detail["modules"][0]["lessons"][0]["id"]
        video = b"\x00\x00\x00\x18ftypmp42" + bytes(4096)
        upload = cls.owner.post(f"/api/v1/admin/lessons/{cls.lesson_id}/video", files={"file": ("lesson.mp4", video, "video/mp4")},
                                data={"duration_s": "20"}, headers=H)
        assert upload.status_code == 200, upload.text
        blocked = cls.owner.patch("/api/v1/admin/products/course-texture-paste", json={"is_active": True}, headers=H)
        assert blocked.status_code == 400 and blocked.json()["error"]["code"] == "not_publishable", blocked.text
        cls.variant_id = variant_of("course-texture-paste")
        assert cls.owner.patch(f"/api/v1/admin/variants/{cls.variant_id}", json={"price_paise": 149900}, headers=H).status_code == 200
        live = cls.owner.patch("/api/v1/admin/products/course-texture-paste", json={"is_active": True}, headers=H)
        assert live.status_code == 200, live.text

    def test_purchase_unlocks_player_and_certificate_and_refund_revokes(self):
        c = client()
        contact = {"name": "Kavya Iyer", "email": "kavya@example.com", "phone": "9833333333"}
        data = checkout(c, [{"variant_id": self.variant_id, "qty": 1}], contact=contact, address=None).json()
        settle(c, data["client"]["redirect_url"])
        order = c.get(f"/api/v1/orders/{data['order_id']}?t={data['token']}").json()
        self.assertEqual(order["status"], "delivered")
        self.assertEqual(order["access"][0]["course_id"], self.course_id)

        request_id = post(c, "/api/v1/auth/code", {"identifier": "kavya@example.com", "purpose": "login"}).json()["request_id"]
        self.assertEqual(post(c, "/api/v1/auth/code/verify", {"request_id": request_id, "code": last_code("kavya@example.com")}).status_code, 200)
        self.assertEqual(c.get("/api/v1/me/courses").json()["courses"][0]["course_id"], self.course_id)
        self.assertFalse(c.get(f"/api/v1/courses/{self.course_id}/learn").json()["certificate_available"])

        play = c.get(f"/api/v1/lessons/{self.lesson_id}/playback").json()
        media = c.get(play["url"], headers={"Range": "bytes=0-99"})
        self.assertEqual(media.status_code, 206)
        self.assertEqual(len(media.content), 100)
        self.assertEqual(client().get(play["url"] + "x").status_code, 403)

        progress = post(c, f"/api/v1/lessons/{self.lesson_id}/progress", {"position_s": 19, "duration_s": 20}).json()
        self.assertTrue(progress["summary"]["is_complete"])
        certificate = c.get(f"/api/v1/courses/{self.course_id}/certificate")
        self.assertEqual(certificate.status_code, 200, certificate.text)
        self.assertTrue(certificate.content.startswith(b"%PDF"))
        again = post(c, "/api/v1/cart/price", {"items": [{"variant_id": self.variant_id, "qty": 1}]}).json()
        self.assertEqual(again["lines"][0]["issue"], "already_enrolled")

        refund = post(self.owner, f"/api/v1/admin/orders/{data['order_id']}/refund", {"note": "Test", "via_gateway": True, "restock": False})
        self.assertEqual(refund.status_code, 200, refund.text)
        self.assertEqual(c.get(f"/api/v1/courses/{self.course_id}/learn").status_code, 403)
        self.assertEqual(c.get(play["url"]).status_code, 403)

    def test_strangers_cannot_watch(self):
        make_user("nosy@example.com")
        c = client()
        login(c, "nosy@example.com")
        self.assertEqual(c.get(f"/api/v1/courses/{self.course_id}/learn").status_code, 403)
        self.assertEqual(c.get(f"/api/v1/lessons/{self.lesson_id}/playback").status_code, 403)
        self.assertEqual(client().get(f"/api/v1/lessons/{self.lesson_id}/playback").status_code, 401)

    def test_workshop_seats_are_booked(self):
        start = (datetime.now(IST) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")
        created = post(self.owner, "/api/v1/admin/products/workshop-weekend-texture/sessions",
                       {"starts_at": start, "duration_min": 120, "seats_total": 2, "price_paise": 99900,
                        "meeting_url": "https://meet.example.com/abc"})
        self.assertEqual(created.status_code, 200, created.text)
        variant = created.json()["workshops"][0]["sessions"][-1]["variant_id"]
        live = self.owner.patch("/api/v1/admin/products/workshop-weekend-texture", json={"is_active": True}, headers=H)
        self.assertEqual(live.status_code, 200, live.text)
        c = client()
        contact = {"name": "Ravi", "email": "ravi@example.com", "phone": "9844400000"}
        data = checkout(c, [{"variant_id": variant, "qty": 1}], contact=contact, address=None).json()
        settle(c, data["client"]["redirect_url"])
        self.assertEqual(c.get(f"/api/v1/orders/{data['order_id']}?t={data['token']}").json()["status"], "delivered")
        self.assertEqual(post(c, "/api/v1/cart/price", {"items": [{"variant_id": variant, "qty": 1}]}).json()["lines"][0]["available"], 1)
        bookings = self.owner.get(f"/api/v1/admin/sessions/{variant}/bookings").json()["bookings"]
        self.assertEqual([b["email"] for b in bookings], ["ravi@example.com"])


class AdminTests(Case):
    def test_roles_are_enforced(self):
        make_user("shopper@example.com")
        shopper = client()
        login(shopper, "shopper@example.com")
        self.assertEqual(shopper.get("/api/v1/admin/overview").status_code, 403)
        make_user("packer@example.com", role="staff")
        staff = client()
        login(staff, "packer@example.com")
        self.assertEqual(staff.get("/api/v1/admin/overview").status_code, 200)
        variant = variant_of("diy-kit-05")
        self.assertEqual(staff.patch(f"/api/v1/admin/variants/{variant}", json={"price_paise": 100}, headers=H).status_code, 403)
        self.assertEqual(staff.patch(f"/api/v1/admin/variants/{variant}", json={"on_hand": 7}, headers=H).status_code, 200)
        self.assertEqual(staff.get("/api/v1/admin/revenue").status_code, 403)
        self.assertEqual(client().get("/api/v1/admin/overview").status_code, 401)

    def test_staff_sessions_time_out(self):
        user = make_user("sleepy@example.com", role="staff")
        staff = client()
        login(staff, "sleepy@example.com")
        sql("UPDATE sessions SET last_seen_at = ? WHERE user_id = ?", (iso_in(hours=-13), user["id"]))
        first = staff.get("/api/v1/admin/overview")
        self.assertEqual((first.status_code, first.json()["error"]["code"]), (401, "session_timeout"))
        self.assertEqual(staff.get("/api/v1/admin/overview").status_code, 401)

    def test_fulfilment_steps_and_audit_trail(self):
        reset_stock()
        make_user("boss@example.com", role="admin")
        owner = client()
        login(owner, "boss@example.com")
        c = client()
        contact = {"name": "Neha", "email": "neha@example.com", "phone": "9811100000"}
        data = checkout(c, [{"variant_id": variant_of("rtb-km-flatlay"), "qty": 1}], contact=contact).json()
        settle(c, data["client"]["redirect_url"])
        order_id = data["order_id"]
        self.assertEqual(post(owner, f"/api/v1/admin/orders/{order_id}/status", {"status": "shipped"}).status_code, 400)
        self.assertEqual(post(owner, f"/api/v1/admin/orders/{order_id}/status", {"status": "packed"}).status_code, 200)
        shipped = post(owner, f"/api/v1/admin/orders/{order_id}/status", {"status": "shipped", "courier": "DTDC", "awb": "D12345678"})
        self.assertEqual(shipped.json()["status"], "shipped")
        self.assertIn("D12345678", value("SELECT html FROM email_outbox WHERE kind = 'order_shipped' AND to_email = 'neha@example.com'"))
        backwards = post(owner, f"/api/v1/admin/orders/{order_id}/status", {"status": "packed"})
        self.assertEqual((backwards.status_code, backwards.json()["error"]["code"]), (409, "invalid_transition"))
        actions = [e["action"] for e in owner.get("/api/v1/admin/audit").json()["entries"]]
        self.assertIn("order.shipped", actions)
        self.assertIn("Packing slip", owner.get(f"/admin/orders/{order_id}/packing-slip").text)

    def test_publish_guard_and_revenue_export(self):
        reset_stock()
        make_user("books@example.com", role="admin")
        owner = client()
        login(owner, "books@example.com")
        blocked = owner.patch("/api/v1/admin/products/draft-shiva-relief", json={"is_active": True}, headers=H)
        self.assertEqual(blocked.json()["error"]["code"], "not_publishable")
        c = client()
        contact = {"name": "=HYPERLINK(1)", "email": "csv@example.com", "phone": "9844444444"}
        data = checkout(c, [{"variant_id": variant_of("rtb-km-mango-crate"), "qty": 1}], contact=contact).json()
        settle(c, data["client"]["redirect_url"])
        revenue = owner.get("/api/v1/admin/revenue?period=day&span=7").json()
        self.assertGreater(revenue["totals"]["products_paise"], 0)
        exported = owner.get("/api/v1/admin/revenue.csv?period=day&span=7").text
        self.assertIn("'=HYPERLINK(1)", exported)

    def test_products_are_deleted_or_archived(self):
        make_user("remover@example.com", role="admin")
        owner = client()
        login(owner, "remover@example.com")
        png = b"\x89PNG\r\n\x1a\n" + bytes(64)

        unsold = post(owner, "/api/v1/admin/products", {"kind": "physical", "title": "Unsold Test Bowl", "price_paise": 50000, "on_hand": 3}).json()
        gone = owner.delete(f"/api/v1/admin/products/{unsold['id']}", headers=H).json()
        self.assertEqual(gone["result"], "deleted")
        self.assertEqual(owner.get(f"/api/v1/admin/products/{unsold['id']}").status_code, 404)

        sold = post(owner, "/api/v1/admin/products", {"kind": "physical", "title": "Sold Test Vase", "price_paise": 70000, "on_hand": 4}).json()
        owner.post(f"/api/v1/admin/products/{sold['id']}/media", files={"file": ("vase.png", png, "image/png")}, headers=H)
        self.assertEqual(owner.patch(f"/api/v1/admin/products/{sold['id']}", json={"is_active": True}, headers=H).status_code, 200)
        buyer = {"name": "Vase Buyer", "email": "vase@example.com", "phone": "9811122233"}
        self.assertEqual(checkout(client(), [{"variant_id": variant_of(sold["id"]), "qty": 1}], contact=buyer).status_code, 200)

        archived = owner.delete(f"/api/v1/admin/products/{sold['id']}", headers=H).json()
        self.assertEqual(archived["result"], "archived")
        self.assertNotIn(sold["id"], {p["id"] for p in client().get("/api/v1/catalogue").json()["products"]})
        self.assertNotIn(sold["id"], {p["id"] for p in owner.get("/api/v1/admin/products").json()["products"]})
        self.assertIn(sold["id"], {p["id"] for p in owner.get("/api/v1/admin/products?archived=true").json()["products"]})
        self.assertEqual(owner.patch(f"/api/v1/admin/products/{sold['id']}", json={"is_active": True}, headers=H).status_code, 400)
        restored = post(owner, f"/api/v1/admin/products/{sold['id']}/restore").json()
        self.assertIsNone(restored["archived_at"])

        make_user("stock-only@example.com", role="staff")
        staff = client()
        login(staff, "stock-only@example.com")
        self.assertEqual(staff.delete(f"/api/v1/admin/products/{sold['id']}", headers=H).status_code, 403)

    def test_customer_record_and_export(self):
        reset_stock()
        make_user("records@example.com", role="admin")
        owner = client()
        login(owner, "records@example.com")
        c = client()
        contact = {"name": "Record Keeper", "email": "record@example.com", "phone": "9822233344"}
        data = checkout(c, [{"variant_id": variant_of("diy-kit-05"), "qty": 1}], contact=contact).json()
        settle(c, data["client"]["redirect_url"])

        listed = owner.get("/api/v1/admin/customers?q=record@example.com").json()["customers"][0]
        self.assertEqual(listed["city"], "Pune")
        record = owner.get(f"/api/v1/admin/customers/{listed['id']}").json()
        self.assertEqual(record["stats"]["paid_orders"], 1)
        self.assertEqual(record["orders"][0]["id"], data["order_id"])
        self.assertEqual(record["addresses"][0]["pincode"], "411001")

        exported = owner.get("/api/v1/admin/customers.csv")
        self.assertTrue(exported.headers["content-type"].startswith("text/csv"))
        self.assertIn("record@example.com", exported.text)
        self.assertIn("customer.export", [e["action"] for e in owner.get("/api/v1/admin/audit").json()["entries"]])

        make_user("nosy-staff@example.com", role="staff")
        staff = client()
        login(staff, "nosy-staff@example.com")
        self.assertEqual(staff.get(f"/api/v1/admin/customers/{listed['id']}").status_code, 403)
        self.assertEqual(staff.get("/api/v1/admin/customers.csv").status_code, 403)

    def test_background_jobs_run_cleanly(self):
        conn = connect()
        try:
            results = jobs.run_due(conn, force=True)
        finally:
            conn.close()
        self.assertNotIn("error", results.values())
        self.assertTrue(any((TMP / "data" / "backups").glob("rangdhaara-*.db")))


if __name__ == "__main__":
    unittest.main()
