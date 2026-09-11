"""Append-only record of every admin write: who, what, when, from where."""
import json

from .db import iso


def record(conn, actor: dict | None, action: str, entity: str, entity_id=None, detail=None, ip: str | None = None) -> None:
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, actor_email, action, entity, entity_id, detail, ip, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            actor["id"] if actor else None,
            actor["email"] if actor else "system",
            action,
            entity,
            None if entity_id is None else str(entity_id),
            detail,
            ip,
            iso(),
        ),
    )
