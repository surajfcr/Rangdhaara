#!/usr/bin/env python3
"""Rangdhara management commands.

  python manage.py setup                prepare .env, the database and the catalogue (safe to re-run)
  python manage.py serve [--reload]     start the website
  python manage.py create-admin         create the owner account, or promote an existing customer
  python manage.py set-role EMAIL ROLE  change someone's role: customer, staff or admin
  python manage.py check                show what's configured and what production still needs
  python manage.py backup               save a database snapshot to data/backups
  python manage.py send-test-email TO   send a test email using your SMTP settings
"""
import argparse
import getpass
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ensure_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        print("  Created .env from .env.example")


def cmd_setup(args) -> None:
    ensure_env()
    from app import seed
    from app.config import settings
    from app.db import connect, migrate, scalar

    version = migrate()
    conn = connect()
    try:
        report = seed.ensure_seeded(conn)
        live = scalar(conn, "SELECT COUNT(*) FROM products WHERE is_active = 1")
        drafts = scalar(conn, "SELECT COUNT(*) FROM products WHERE is_active = 0")
        customers = scalar(conn, "SELECT COUNT(*) FROM users")
        admins = scalar(conn, "SELECT COUNT(*) FROM users WHERE role = 'admin'")
    finally:
        conn.close()

    print(f"  Database ready (schema v{version}) at {settings.database_path}")
    if report.get("legacy"):
        legacy = report["legacy"]
        print(f"  Imported from the old site: {legacy['users']} customers, {legacy['orders']} orders "
              "(passwords must be reset on next sign-in; old orders are flagged for payment review)")
    print(f"  Catalogue: {live} live, {drafts} drafts | {customers} customer accounts")
    print(f"  Payments: {settings.payment_provider} | Email: {'SMTP' if settings.smtp_configured else 'saved to data/outbox (development)'}")
    if not admins:
        if sys.stdin.isatty() and not args.no_input:
            answer = input("\n  No admin account yet. Create one now? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                cmd_create_admin(argparse.Namespace(email=None, name=None, password_stdin=False))
                return
        print("\n  No admin account yet. Run:  python manage.py create-admin")


def cmd_create_admin(args) -> None:
    ensure_env()
    from app import audit
    from app.db import connect, migrate, one, transaction
    from app.security import hash_password, is_email, normalize_email, password_problem
    from app.users import create_user

    migrate()
    email = normalize_email(args.email or input("  Admin email: "))
    if not is_email(email):
        sys.exit("  That doesn't look like an email address.")
    conn = connect()
    try:
        user = one(conn, "SELECT * FROM users WHERE email = ?", (email,))
        name = args.name or (user["full_name"] if user and user["full_name"] else input("  Your name: ").strip())
        while True:
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\n")
                confirm = password
            else:
                password = getpass.getpass("  Choose a password (at least 8 characters): ")
                confirm = getpass.getpass("  Type it again: ")
            problem = password_problem(password, email)
            if problem or password != confirm:
                print(f"  {problem or 'Those passwords did not match.'}")
                if args.password_stdin:
                    sys.exit(1)
                continue
            break
        password_hash = hash_password(password)
        with transaction(conn):
            if user:
                conn.execute("UPDATE users SET role = 'admin', password_hash = ?, must_reset_password = 0, full_name = ?, "
                             "email_verified_at = COALESCE(email_verified_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) WHERE id = ?",
                             (password_hash, name, user["id"]))
                user_id = user["id"]
            else:
                user_id = create_user(conn, email=email, full_name=name, password_hash=password_hash, verified=True, role="admin")["id"]
            audit.record(conn, None, "user.role", "user", user_id, {"to": "admin", "via": "manage.py"})
    finally:
        conn.close()
    print(f"  {email} is now an admin. Sign in at /admin.")


def cmd_set_role(args) -> None:
    from app import audit
    from app.db import connect, one, transaction
    from app.security import normalize_email

    if args.role not in ("customer", "staff", "admin"):
        sys.exit("  Role must be customer, staff or admin.")
    conn = connect()
    try:
        user = one(conn, "SELECT * FROM users WHERE email = ?", (normalize_email(args.email),))
        if not user:
            sys.exit("  No account with that email.")
        with transaction(conn):
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (args.role, user["id"]))
            audit.record(conn, None, "user.role", "user", user["id"], {"from": user["role"], "to": args.role, "via": "manage.py"})
    finally:
        conn.close()
    print(f"  {user['email']} is now {args.role}.")


def cmd_serve(args) -> None:
    ensure_env()
    import uvicorn

    from app.config import settings

    print(f"\n  Rangdhara is running:  {settings.public_base_url}\n  Admin panel:            {settings.public_base_url}/admin\n"
          "  Press Ctrl+C to stop.\n", flush=True)
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=args.reload, proxy_headers=True,
                forwarded_allow_ips=args.forwarded_allow_ips, log_level="info")


def cmd_check(args) -> None:
    ensure_env()
    from app.config import settings

    print(f"  Environment: {settings.app_env}")
    print(f"  Public URL:  {settings.public_base_url}")
    print(f"  Payments:    {settings.payment_provider}")
    print(f"  Email:       {'SMTP as ' + settings.smtp_user if settings.smtp_configured else 'not configured (development outbox)'}")
    problems = settings.problems(assume_production=True)
    if problems:
        print("\n  Before going live, fix:")
        for problem in problems:
            print(f"   - {problem}")
    else:
        print("\n  Production configuration looks complete.")


def cmd_backup(args) -> None:
    from app.db import connect
    from app.jobs import backup_database

    conn = connect()
    try:
        print(f"  Saved data/backups/{backup_database(conn)}")
    finally:
        conn.close()


def cmd_send_test_email(args) -> None:
    ensure_env()
    from app import emails
    from app.config import settings
    from app.db import connect, one

    conn = connect()
    try:
        emails.enqueue(conn, "test", args.to, "Rangdhara test email", "<p>Email delivery from your store is working.</p>")
        emails.deliver_pending(conn)
        row = one(conn, "SELECT status, last_error FROM email_outbox WHERE kind = 'test' ORDER BY id DESC LIMIT 1")
    finally:
        conn.close()
    if row["status"] == "sent":
        print(f"  Sent to {args.to}.")
    elif row["status"] == "logged":
        print(f"  SMTP isn't configured, so the email was saved in {settings.data_dir / 'outbox'} instead.")
    else:
        print(f"  Not sent yet: {row['last_error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rangdhara management commands")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--no-input", action="store_true")
    p.set_defaults(func=cmd_setup)
    p = sub.add_parser("serve")
    p.add_argument("--reload", action="store_true")
    p.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("create-admin")
    p.add_argument("--email")
    p.add_argument("--name")
    p.add_argument("--password-stdin", action="store_true")
    p.set_defaults(func=cmd_create_admin)
    p = sub.add_parser("set-role")
    p.add_argument("email")
    p.add_argument("role")
    p.set_defaults(func=cmd_set_role)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("backup").set_defaults(func=cmd_backup)
    p = sub.add_parser("send-test-email")
    p.add_argument("to")
    p.set_defaults(func=cmd_send_test_email)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    args.func(args)


if __name__ == "__main__":
    main()
