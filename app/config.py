"""Runtime settings, loaded from environment variables and an optional .env file.

Nothing secret lives in source. In development the app runs with safe defaults
(mock payments, emails written to data/outbox); production refuses to start
until the real credentials are configured.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = BASE_DIR / "public"


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE parser so we don't need python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else BASE_DIR / p


class Settings:
    def __init__(self) -> None:
        self.app_env = _env("APP_ENV", "development").lower()
        self.is_production = self.app_env == "production"
        self.public_base_url = _env("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
        self.host = _env("HOST", "127.0.0.1")
        self.port = _env_int("PORT", 8080)

        self.data_dir = _path(_env("DATA_DIR", "data"))
        self.database_path = _path(_env("DATABASE_PATH", "data/rangdhaara.db"))
        self.storage_dir = _path(_env("STORAGE_DIR", "storage"))
        # Defaults to a folder inside the app (fine for a single disk locally). On a host with a
        # separate persistent volume, point UPLOADS_DIR at it so uploaded product photos survive deploys.
        self.uploads_dir = _path(_env("UPLOADS_DIR", "public/assets/uploads"))

        self.secret_key = _env("SECRET_KEY") or self._dev_secret()
        cookie_secure = _env("COOKIE_SECURE", "auto").lower()
        self.cookie_secure = (
            self.public_base_url.startswith("https://") if cookie_secure == "auto" else cookie_secure in ("1", "true", "yes")
        )
        self.session_days = _env_int("SESSION_DAYS", 30)
        self.staff_idle_hours = _env_int("STAFF_IDLE_HOURS", 12)

        self.store_name = _env("STORE_NAME", "Rangdhara Art Studio")
        self.store_admin_email = _env("STORE_ADMIN_EMAIL")
        self.whatsapp_number = _env("WHATSAPP_NUMBER", "918080007684")
        self.instagram_handle = _env("INSTAGRAM_HANDLE", "rangdhaara_")

        self.smtp_host = _env("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = _env_int("SMTP_PORT", 587)
        self.smtp_user = _env("SMTP_USER")
        # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces are for reading, not part of the secret.
        self.smtp_password = _env("SMTP_PASSWORD").replace(" ", "")
        self.mail_from = _env("MAIL_FROM") or (f"{self.store_name} <{self.smtp_user}>" if self.smtp_user else "")

        self.payment_provider = _env("PAYMENT_PROVIDER", "mock").lower()
        self.razorpay_key_id = _env("RAZORPAY_KEY_ID")
        self.razorpay_key_secret = _env("RAZORPAY_KEY_SECRET")
        self.razorpay_webhook_secret = _env("RAZORPAY_WEBHOOK_SECRET")
        self.cashfree_app_id = _env("CASHFREE_APP_ID")
        self.cashfree_secret_key = _env("CASHFREE_SECRET_KEY")
        self.cashfree_env = _env("CASHFREE_ENV", "sandbox").lower()
        self.upi_vpa = _env("UPI_VPA", "8080007684@ybl")
        self.upi_payee_name = _env("UPI_PAYEE_NAME", self.store_name)

        self.origin_pincode = _env("ORIGIN_PINCODE", "431001")
        self.dispatch_days = _env_int("DISPATCH_DAYS", 1)
        self.shipping_flat_paise = _env_int("SHIPPING_FLAT_PAISE", 0)
        self.free_shipping_threshold_paise = _env_int("FREE_SHIPPING_THRESHOLD_PAISE", 0)

        # How long a checkout holds stock. Long enough to finish paying (a UPI collect request or
        # a card OTP takes a couple of minutes), short enough that a tab closed mid-checkout doesn't
        # leave a one-off piece reading "Sold out" for long. Raise it if payments start lapsing.
        self.reservation_minutes = _env_int("RESERVATION_MINUTES", 5)
        self.otp_ttl_minutes = 10
        self.otp_max_attempts = 5
        self.max_video_upload_mb = _env_int("MAX_VIDEO_UPLOAD_MB", 2048)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_user and self.smtp_password)

    def _dev_secret(self) -> str:
        """Persist a random key for development so sessions survive restarts."""
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(48)
        key_file.write_text(key, encoding="utf-8")
        return key

    def problems(self, assume_production: bool = False) -> list[str]:
        """Configuration that makes the app unsafe to run in production."""
        issues = []
        if not (self.is_production or assume_production):
            return issues
        if not os.environ.get("SECRET_KEY"):
            issues.append("SECRET_KEY must be set in production.")
        if self.payment_provider == "mock":
            issues.append("PAYMENT_PROVIDER=mock cannot take real payments. Use razorpay, cashfree or manual_upi.")
        if self.payment_provider == "razorpay" and not (
            self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_webhook_secret
        ):
            issues.append("Razorpay needs RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET.")
        if self.payment_provider == "cashfree" and not (self.cashfree_app_id and self.cashfree_secret_key):
            issues.append("Cashfree needs CASHFREE_APP_ID and CASHFREE_SECRET_KEY.")
        if not self.smtp_configured:
            issues.append("SMTP_USER and SMTP_PASSWORD must be set so customers receive codes and receipts.")
        if not self.public_base_url.startswith("https://"):
            issues.append("PUBLIC_BASE_URL must be https:// in production.")
        return issues


settings = Settings()
