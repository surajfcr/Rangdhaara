"""Course player API: outline with progress, signed playback links, progress
tracking, resource downloads and certificates."""
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..academy import active_enrollment, course_with_product, lessons_in_order, progress_map, record_progress, summary
from ..auth import current_user, require_user
from ..certificates import get_or_issue
from ..db import all_rows, get_db, jloads, one, scalar, transaction
from ..errors import forbidden, not_found, unauthorized
from ..storage import playback_url, resolve_key

router = APIRouter()


def _access(conn, user: dict, course_id: int) -> tuple[dict, dict | None]:
    course = course_with_product(conn, course_id)
    if not course:
        raise not_found("That course doesn't exist.")
    enrollment = active_enrollment(conn, user["id"], course_id)
    if enrollment or user["role"] in ("staff", "admin"):
        return course, enrollment
    raise forbidden("You don't have access to this course. If you bought it, sign in with the email you used at checkout.")


@router.get("/courses/{course_id}/learn")
def learn(course_id: int, user=Depends(require_user), conn=Depends(get_db)):
    course, enrollment = _access(conn, user, course_id)
    lessons = lessons_in_order(conn, course_id)
    progress = progress_map(conn, enrollment["id"]) if enrollment else {}
    modules = []
    for module in all_rows(conn, "SELECT id, title FROM course_modules WHERE course_id = ? ORDER BY position, id", (course_id,)):
        modules.append({
            "id": module["id"],
            "title": module["title"],
            "lessons": [{
                "id": l["id"],
                "title": l["title"],
                "description": l["description"],
                "duration_s": l["duration_s"],
                "has_video": bool(l["video_key"]),
                "is_preview": bool(l["is_preview"]),
                "position_s": progress.get(l["id"], {}).get("last_position_s", 0),
                "completed": bool(progress.get(l["id"], {}).get("completed_at")),
            } for l in lessons if l["module_id"] == module["id"]],
        })
    image = scalar(conn, "SELECT url FROM product_media WHERE product_id = ? AND kind = 'image' ORDER BY position, id LIMIT 1",
                   (course["product_id"],))
    progress_summary = summary(conn, enrollment) if enrollment else None
    return {
        "course": {
            "id": course["id"],
            "title": course["title"],
            "description": course["description"],
            "level": course["level"],
            "instructor": course["instructor"],
            "outcomes": jloads(course["outcomes"]),
            "certificate_enabled": bool(course["certificate_enabled"]),
            "image": image or "",
        },
        "modules": modules,
        "resources": all_rows(conn, "SELECT id, title, filename, size_bytes, lesson_id FROM course_resources "
                                    "WHERE course_id = ? ORDER BY id", (course_id,)),
        "summary": progress_summary,
        "enrollment": {"granted_at": enrollment["granted_at"], "expires_at": enrollment["expires_at"]} if enrollment else None,
        "staff_preview": enrollment is None,
        "certificate_available": bool(course["certificate_enabled"] and progress_summary and progress_summary["is_complete"]),
    }


@router.get("/lessons/{lesson_id}/playback")
def playback(lesson_id: int, user=Depends(current_user), conn=Depends(get_db)):
    lesson = one(conn, "SELECT l.*, m.course_id FROM lessons l JOIN course_modules m ON m.id = l.module_id WHERE l.id = ?",
                 (lesson_id,))
    if not lesson or not lesson["video_key"]:
        raise not_found("This lesson doesn't have a video yet.")
    if lesson["is_preview"]:
        published = scalar(conn, "SELECT p.is_active FROM courses c JOIN products p ON p.id = c.product_id WHERE c.id = ?",
                           (lesson["course_id"],))
        if not published and not (user and user["role"] in ("staff", "admin")):
            raise not_found("This lesson doesn't have a video yet.")
        return playback_url(lesson_id, user["id"] if user else "preview")
    if not user:
        raise unauthorized("Sign in to watch this lesson.")
    _access(conn, user, lesson["course_id"])
    return playback_url(lesson_id, user["id"])


class ProgressIn(BaseModel):
    position_s: float = Field(ge=0, le=86400)
    duration_s: float | None = Field(default=None, ge=0, le=86400)
    completed: bool = False


@router.post("/lessons/{lesson_id}/progress")
def save_progress(lesson_id: int, body: ProgressIn, user=Depends(require_user), conn=Depends(get_db)):
    lesson = one(conn, "SELECT l.*, m.course_id FROM lessons l JOIN course_modules m ON m.id = l.module_id WHERE l.id = ?",
                 (lesson_id,))
    if not lesson:
        raise not_found("Lesson not found.")
    enrollment = active_enrollment(conn, user["id"], lesson["course_id"])
    if not enrollment:
        return {"recorded": False}
    with transaction(conn):
        result = record_progress(conn, enrollment, lesson, int(body.position_s),
                                 int(body.duration_s) if body.duration_s else None, body.completed)
    return {"recorded": True, "progress": result, "summary": summary(conn, enrollment)}


@router.get("/resources/{resource_id}/download")
def download_resource(resource_id: int, user=Depends(require_user), conn=Depends(get_db)):
    resource = one(conn, "SELECT * FROM course_resources WHERE id = ?", (resource_id,))
    if not resource:
        raise not_found("That file isn't available.")
    _access(conn, user, resource["course_id"])
    path = resolve_key(resource["storage_key"])
    if not path.exists():
        raise not_found("That file isn't available.")
    return FileResponse(path, media_type=resource["content_type"], filename=resource["filename"],
                        headers={"Cache-Control": "private, no-store"})


@router.get("/courses/{course_id}/certificate")
def certificate(course_id: int, user=Depends(require_user), conn=Depends(get_db)):
    course, enrollment = _access(conn, user, course_id)
    if not enrollment:
        raise forbidden("Certificates are issued to enrolled students.")
    if not course["certificate_enabled"]:
        raise not_found("This course doesn't include a certificate.")
    progress = summary(conn, enrollment)
    if not progress["is_complete"]:
        raise forbidden(f"Finish all {progress['total']} lessons to unlock your certificate. You've completed {progress['completed']}.")
    with transaction(conn):
        cert = get_or_issue(conn, enrollment, user["full_name"] or user["email"].split("@")[0], course["title"])
    return FileResponse(resolve_key(cert["storage_key"]), media_type="application/pdf",
                        filename=f"Rangdhara-certificate-{cert['id']}.pdf", headers={"Cache-Control": "private, no-store"})
