"""Background work that must never run inside a customer's request.

One thread wakes every few seconds and runs whatever is due. A lease row in
the database means that if several server processes start, only one of them
does the work.
"""
import os
import socket
import sqlite3
import threading
import traceback
from datetime import datetime

from . import captcha, emails, orders
from .config import settings
from .db import connect, iso, iso_in, kv_get, kv_set, one, transaction
from .payments.service import reconcile
from .security import purge_rate_events
from .shipping import IST

TICK_SECONDS = 10


def cleanup(conn) -> str:
    purge_rate_events(conn)
    captcha.purge(conn)
    conn.execute("DELETE FROM otp_requests WHERE expires_at < ?", (iso_in(days=-1),))
    conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (iso_in(days=-1),))
    conn.execute("DELETE FROM sessions WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)",
                 (iso(), iso_in(days=-7)))
    conn.execute("DELETE FROM email_outbox WHERE status IN ('sent', 'logged') AND created_at < ?", (iso_in(days=-60),))
    return "ok"


def backup_database(conn) -> str:
    """A consistent daily snapshot in data/backups (keeps 14). If the project folder
    syncs to OneDrive, these files are your off-site copy."""
    folder = settings.data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target_path = folder / f"rangdhaara-{datetime.now(IST):%Y%m%d}.db"
    target = sqlite3.connect(target_path)
    try:
        conn.backup(target)
    finally:
        target.close()
    snapshots = sorted(folder.glob("rangdhaara-*.db"))
    for old in snapshots[:-14]:
        old.unlink(missing_ok=True)
    return target_path.name


TASKS = [
    # (name, minimum seconds between runs, function)
    ("email", 0, emails.deliver_pending),
    ("holds", 60, orders.release_expired_holds),
    ("expire", 300, orders.expire_unpaid),
    ("reconcile", 600, reconcile),
    ("abandoned", 900, orders.send_abandoned_cart_emails),
    ("cleanup", 3600, cleanup),
    ("backup", 86400, backup_database),
]


def run_due(conn, force: bool = False) -> dict:
    results = {}
    for name, interval, task in TASKS:
        last = kv_get(conn, f"job:{name}")
        if not force and interval and last and last > iso_in(seconds=-interval):
            continue
        try:
            results[name] = task(conn)
        except Exception:
            traceback.print_exc()
            results[name] = "error"
        if interval:
            kv_set(conn, f"job:{name}", iso())
    return results


class BackgroundJobs:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.holder = f"{socket.gethostname()}:{os.getpid()}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="rangdhaara-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            conn = None
            try:
                conn = connect()
                if self._hold_lease(conn):
                    run_due(conn)
            except Exception:
                traceback.print_exc()
            finally:
                if conn is not None:
                    conn.close()
            self._stop.wait(TICK_SECONDS)

    def _hold_lease(self, conn) -> bool:
        with transaction(conn):
            lease = one(conn, "SELECT * FROM job_leases WHERE name = 'main'")
            if lease and lease["holder"] != self.holder and lease["expires_at"] > iso():
                return False
            conn.execute(
                "INSERT INTO job_leases (name, holder, expires_at) VALUES ('main', ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET holder = excluded.holder, expires_at = excluded.expires_at",
                (self.holder, iso_in(seconds=TICK_SECONDS * 6)),
            )
        return True
