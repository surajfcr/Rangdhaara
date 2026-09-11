"""Enrolments, lesson progress and course completion."""
from .db import all_rows, iso, iso_in, one, parse_iso, scalar, utcnow

COMPLETE_AT = 0.9  # watching 90% counts; outros and end cards shouldn't block completion


def course_with_product(conn, course_id: int) -> dict | None:
    return one(
        conn,
        "SELECT c.*, p.title, p.slug, p.description, p.is_active, p.id AS product_id FROM courses c "
        "JOIN products p ON p.id = c.product_id WHERE c.id = ?",
        (course_id,),
    )


def active_enrollment(conn, user_id: str, course_id: int) -> dict | None:
    return one(
        conn,
        "SELECT * FROM enrollments WHERE user_id = ? AND course_id = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (user_id, course_id, iso()),
    )


def grant_enrollment(conn, user_id: str, course_id: int, *, order_id: str | None = None,
                     source: str = "purchase", reason: str | None = None) -> tuple[dict, bool]:
    """Returns (enrollment, newly_granted). Re-activates a revoked or expired enrolment."""
    course = one(conn, "SELECT access_days FROM courses WHERE id = ?", (course_id,))
    expires = iso_in(days=course["access_days"]) if course and course["access_days"] else None
    existing = one(conn, "SELECT * FROM enrollments WHERE user_id = ? AND course_id = ?", (user_id, course_id))
    now = iso()
    if existing:
        still_active = not existing["revoked_at"] and (not existing["expires_at"] or existing["expires_at"] > now)
        if still_active:
            return existing, False
        conn.execute(
            "UPDATE enrollments SET revoked_at = NULL, revoke_reason = NULL, granted_at = ?, expires_at = ?, "
            "order_id = ?, source = ?, grant_reason = ? WHERE id = ?",
            (now, expires, order_id, source, reason, existing["id"]),
        )
        return one(conn, "SELECT * FROM enrollments WHERE id = ?", (existing["id"],)), True
    cur = conn.execute(
        "INSERT INTO enrollments (user_id, course_id, order_id, source, grant_reason, granted_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, course_id, order_id, source, reason, now, expires),
    )
    return one(conn, "SELECT * FROM enrollments WHERE id = ?", (cur.lastrowid,)), True


def revoke_enrollment(conn, enrollment_id: int, reason: str) -> None:
    conn.execute("UPDATE enrollments SET revoked_at = ?, revoke_reason = ? WHERE id = ? AND revoked_at IS NULL",
                 (iso(), reason[:300], enrollment_id))


def lessons_in_order(conn, course_id: int) -> list[dict]:
    return all_rows(
        conn,
        "SELECT l.*, m.title AS module_title, m.id AS module_id FROM lessons l "
        "JOIN course_modules m ON m.id = l.module_id WHERE m.course_id = ? ORDER BY m.position, m.id, l.position, l.id",
        (course_id,),
    )


def progress_map(conn, enrollment_id: int) -> dict[int, dict]:
    return {r["lesson_id"]: r for r in all_rows(conn, "SELECT * FROM lesson_progress WHERE enrollment_id = ?", (enrollment_id,))}


def summary(conn, enrollment: dict) -> dict:
    lessons = lessons_in_order(conn, enrollment["course_id"])
    progress = progress_map(conn, enrollment["id"])
    completed = [l for l in lessons if progress.get(l["id"], {}).get("completed_at")]
    resume = None
    touched = sorted((p for p in progress.values()), key=lambda p: p["updated_at"], reverse=True)
    if touched:
        last = touched[0]
        resume = last["lesson_id"]
        if last.get("completed_at"):
            ids = [l["id"] for l in lessons]
            if last["lesson_id"] in ids:
                idx = ids.index(last["lesson_id"])
                later = [l for l in lessons[idx + 1:] if not progress.get(l["id"], {}).get("completed_at")]
                resume = later[0]["id"] if later else last["lesson_id"]
    elif lessons:
        resume = lessons[0]["id"]
    total = len(lessons)
    return {
        "completed": len(completed),
        "total": total,
        "percent": round(100 * len(completed) / total) if total else 0,
        "resume_lesson_id": resume,
        "is_complete": total > 0 and len(completed) == total,
    }


def record_progress(conn, enrollment: dict, lesson: dict, position_s: int, duration_s: int | None = None,
                    mark_complete: bool = False) -> dict:
    position_s = max(0, int(position_s))
    duration = lesson["duration_s"]
    if not duration and duration_s and 1 <= duration_s <= 6 * 3600:
        conn.execute("UPDATE lessons SET duration_s = ? WHERE id = ? AND duration_s = 0", (int(duration_s), lesson["id"]))
        duration = int(duration_s)
    now = utcnow()
    existing = one(conn, "SELECT * FROM lesson_progress WHERE enrollment_id = ? AND lesson_id = ?",
                   (enrollment["id"], lesson["id"]))
    if existing:
        # Scrubbing to the end shouldn't count as watching it: watched time can only grow about as fast as real time.
        elapsed = (now - parse_iso(existing["updated_at"])).total_seconds()
        ceiling = existing["max_position_s"] + int(elapsed * 2.5) + 30
        max_pos = max(existing["max_position_s"], min(position_s, ceiling))
    else:
        max_pos = min(position_s, 30)
    completed_at = existing["completed_at"] if existing else None
    if not completed_at:
        if mark_complete and not lesson["video_key"]:
            completed_at = iso(now)
        elif duration and max_pos >= duration * COMPLETE_AT:
            completed_at = iso(now)
    conn.execute(
        "INSERT INTO lesson_progress (enrollment_id, lesson_id, last_position_s, max_position_s, completed_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(enrollment_id, lesson_id) DO UPDATE SET "
        "last_position_s = excluded.last_position_s, max_position_s = excluded.max_position_s, "
        "completed_at = excluded.completed_at, updated_at = excluded.updated_at",
        (enrollment["id"], lesson["id"], position_s, max_pos, completed_at, iso(now)),
    )
    return {"lesson_id": lesson["id"], "last_position_s": position_s, "completed": bool(completed_at)}


def enrollment_count_since(conn, since_iso: str) -> int:
    return scalar(conn, "SELECT COUNT(*) FROM enrollments WHERE granted_at >= ? AND revoked_at IS NULL", (since_iso,))
