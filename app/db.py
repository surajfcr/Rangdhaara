"""SQLite access: connections, transactions and schema migrations.

Money is always stored as integer paise. Timestamps are UTC ISO-8601 strings
ending in "Z", which sort correctly as text and parse in SQLite date functions.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_in(**delta) -> str:
    return iso(utcnow() + timedelta(**delta))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        settings.database_path,
        isolation_level=None,  # autocommit; transactions are explicit via transaction()
        check_same_thread=False,  # FastAPI may run a request's dependency and handler on different threads
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def get_db():
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = True):
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-write logic
    (stock, seats, idempotency) can't interleave with another request."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def all_rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def jloads(value, default=None):
    if value in (None, ""):
        return default if default is not None else []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else []


def kv_get(conn, key: str, default: str | None = None) -> str | None:
    value = scalar(conn, "SELECT value FROM kv WHERE key = ?", (key,))
    return default if value is None else value


def kv_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, iso()),
    )


SCHEMA_V1 = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone TEXT UNIQUE,
    full_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    must_reset_password INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'staff', 'admin')),
    email_verified_at TEXT,
    marketing_opt_in INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    legacy_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    pincode TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_addresses_user ON addresses(user_id);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    revoked_at TEXT
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

CREATE TABLE otp_requests (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('login', 'signup', 'reset')),
    email TEXT NOT NULL COLLATE NOCASE,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT,
    payload TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL,
    ip TEXT
);
CREATE INDEX idx_otp_email ON otp_requests(email, created_at);

CREATE TABLE auth_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE rate_events (
    key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_rate_events ON rate_events(key, created_at);

CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE products (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('physical', 'kit', 'course', 'workshop')),
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '[]',
    includes TEXT NOT NULL DEFAULT '[]',
    tools_info TEXT NOT NULL DEFAULT '',
    material TEXT NOT NULL DEFAULT '',
    badge TEXT NOT NULL DEFAULT '',
    rating REAL,
    reviews_count INTEGER NOT NULL DEFAULT 0,
    compare_at_paise INTEGER,
    is_active INTEGER NOT NULL DEFAULT 0,
    is_featured INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_products_kind ON products(kind, is_active);

CREATE TABLE product_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '',
    sku TEXT UNIQUE,
    price_paise INTEGER NOT NULL CHECK (price_paise >= 0),
    on_hand INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_variants_product ON product_variants(product_id);

CREATE TABLE product_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'image' CHECK (kind IN ('image', 'video')),
    alt TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_media_product ON product_media(product_id, position);

CREATE TABLE coupons (
    code TEXT PRIMARY KEY COLLATE NOCASE,
    kind TEXT NOT NULL CHECK (kind IN ('percent', 'flat')),
    value INTEGER NOT NULL CHECK (value > 0),
    min_subtotal_paise INTEGER NOT NULL DEFAULT 0,
    max_discount_paise INTEGER,
    usage_limit INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0,
    starts_at TEXT,
    ends_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    email TEXT NOT NULL COLLATE NOCASE DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    customer_name TEXT NOT NULL DEFAULT '',
    requires_shipping INTEGER NOT NULL DEFAULT 1,
    ship_name TEXT NOT NULL DEFAULT '',
    ship_phone TEXT NOT NULL DEFAULT '',
    ship_line1 TEXT NOT NULL DEFAULT '',
    ship_line2 TEXT NOT NULL DEFAULT '',
    ship_city TEXT NOT NULL DEFAULT '',
    ship_state TEXT NOT NULL DEFAULT '',
    ship_pincode TEXT NOT NULL DEFAULT '',
    subtotal_paise INTEGER NOT NULL,
    discount_paise INTEGER NOT NULL DEFAULT 0,
    shipping_paise INTEGER NOT NULL DEFAULT 0,
    total_paise INTEGER NOT NULL,
    coupon_code TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('pending_payment', 'paid', 'packed', 'shipped', 'delivered', 'cancelled', 'refunded', 'expired')),
    needs_review TEXT,
    payment_provider TEXT,
    gateway_order_id TEXT,
    gateway_payment_id TEXT,
    gateway_client TEXT,
    payment_reference TEXT,
    paid_at TEXT,
    packed_at TEXT,
    shipped_at TEXT,
    delivered_at TEXT,
    cancelled_at TEXT,
    refunded_at TEXT,
    expired_at TEXT,
    delivery_min_date TEXT,
    delivery_max_date TEXT,
    courier TEXT,
    awb TEXT,
    fulfilled_at TEXT,
    fulfilment_error TEXT,
    marketing_opt_in INTEGER NOT NULL DEFAULT 0,
    abandoned_email_sent_at TEXT,
    is_legacy INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_orders_status ON orders(status, created_at);
CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_email ON orders(email);
CREATE INDEX idx_orders_paid ON orders(paid_at);
CREATE UNIQUE INDEX idx_orders_gateway ON orders(payment_provider, gateway_order_id)
    WHERE gateway_order_id IS NOT NULL;

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id TEXT REFERENCES products(id) ON DELETE SET NULL,
    variant_id INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    variant_label TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    unit_price_paise INTEGER NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    line_total_paise INTEGER NOT NULL
);
CREATE INDEX idx_order_items_order ON order_items(order_id);

CREATE TABLE stock_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
    qty INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'converted', 'released')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_reservations_variant ON stock_reservations(variant_id, status, expires_at);
CREATE INDEX idx_reservations_order ON stock_reservations(order_id);

CREATE TABLE payment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT '',
    order_id TEXT,
    gateway_payment_id TEXT,
    state TEXT,
    amount_paise INTEGER,
    signature_valid INTEGER NOT NULL,
    headers TEXT NOT NULL,
    body TEXT NOT NULL,
    outcome TEXT,
    error TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE (provider, event_id)
);
CREATE INDEX idx_payment_events_order ON payment_events(order_id);

CREATE TABLE order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT,
    actor_user_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_order_events_order ON order_events(order_id);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL UNIQUE REFERENCES products(id) ON DELETE CASCADE,
    level TEXT NOT NULL DEFAULT 'Beginner',
    instructor TEXT NOT NULL DEFAULT '',
    outcomes TEXT NOT NULL DEFAULT '[]',
    certificate_enabled INTEGER NOT NULL DEFAULT 1,
    access_days INTEGER
);

CREATE TABLE course_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_modules_course ON course_modules(course_id, position);

CREATE TABLE lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL REFERENCES course_modules(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    video_key TEXT,
    video_size INTEGER,
    duration_s INTEGER NOT NULL DEFAULT 0,
    is_preview INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_lessons_module ON lessons(module_id, position);

CREATE TABLE course_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lesson_id INTEGER REFERENCES lessons(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    source TEXT NOT NULL CHECK (source IN ('purchase', 'manual')),
    grant_reason TEXT,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    revoke_reason TEXT,
    UNIQUE (user_id, course_id)
);

CREATE TABLE lesson_progress (
    enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    last_position_s INTEGER NOT NULL DEFAULT 0,
    max_position_s INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (enrollment_id, lesson_id)
);

CREATE TABLE certificates (
    id TEXT PRIMARY KEY,
    enrollment_id INTEGER NOT NULL UNIQUE REFERENCES enrollments(id) ON DELETE CASCADE,
    student_name TEXT NOT NULL,
    course_title TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    issued_at TEXT NOT NULL
);

CREATE TABLE workshop_sessions (
    variant_id INTEGER PRIMARY KEY REFERENCES product_variants(id) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    duration_min INTEGER NOT NULL DEFAULT 120,
    seats_total INTEGER NOT NULL,
    meeting_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE workshop_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    seats INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    attended INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_bookings_variant ON workshop_bookings(variant_id);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id TEXT,
    actor_email TEXT,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail TEXT,
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_audit_created ON audit_log(created_at);

CREATE TABLE email_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    html TEXT NOT NULL,
    text_body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed', 'logged')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    send_after TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX idx_outbox_status ON email_outbox(status, send_after);

CREATE TABLE pincode_cache (
    pincode TEXT PRIMARY KEY,
    city TEXT,
    state TEXT,
    areas TEXT,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE mock_payments (
    gateway_order_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('created', 'paid', 'failed')),
    payment_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE job_leases (
    name TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SCHEMA_V2 = """
ALTER TABLE products ADD COLUMN archived_at TEXT;
"""

SCHEMA_V3 = """
CREATE TABLE captcha_challenges (
    id TEXT PRIMARY KEY,
    answer_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_captcha_created ON captcha_challenges(created_at);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, SCHEMA_V1),
    # Products that have been sold can't be deleted without breaking order history, so they're archived instead.
    (2, SCHEMA_V2),
    # The answer is kept server-side so a bot can't read it out of the page, and each row is
    # single-use so a solved challenge can't be replayed to spam the signup mailer.
    (3, SCHEMA_V3),
]


def migrate(conn: sqlite3.Connection | None = None) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    own = conn is None
    conn = conn or connect()
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_sql(sql):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            current = version
        return current
    finally:
        if own:
            conn.close()


def _split_sql(script: str) -> list[str]:
    """Split a schema script on semicolons that end a statement (no triggers here)."""
    statements, buf = [], []
    for line in script.splitlines():
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt.rstrip(";").strip():
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
