"""Private file storage (lesson videos, course resources, certificates),
public product image uploads, and short-lived signed video URLs."""
import re
import time
from pathlib import Path

from fastapi import UploadFile

from .config import settings
from .errors import bad_request
from .security import random_token, sign, verify_signature

VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
RESOURCE_TYPES = {".pdf": "application/pdf", ".zip": "application/zip", ".png": "image/png", ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg", ".txt": "text/plain"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

PLAYBACK_TTL_SECONDS = 4 * 3600


def _content_matches(ext: str, head: bytes) -> bool:
    """Check the file's first bytes, so a renamed .html can't be uploaded as a .jpg."""
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext == ".zip":
        return head.startswith(b"PK\x03\x04")
    if ext in (".mp4", ".m4v", ".mov"):
        return head[4:8] == b"ftyp"
    if ext == ".webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext == ".txt":
        return b"\x00" not in head
    return False


def safe_filename(name: str) -> str:
    stem = Path(name or "file").name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", stem).strip(" .") or "file"
    return stem[:120]


def resolve_key(key: str) -> Path:
    root = settings.storage_dir.resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise bad_request("Invalid file reference.")
    return path


def _write_upload(upload: UploadFile, dest: Path, allowed: dict, max_bytes: int) -> tuple[int, str]:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in allowed:
        raise bad_request(f"Upload one of: {', '.join(sorted(allowed))}.", code="file_type")
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    head = b""
    upload.file.seek(0)
    try:
        with open(dest, "wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                size += len(chunk)
                if size > max_bytes:
                    raise bad_request(f"That file is larger than {max_bytes // (1024 * 1024)} MB.", code="file_too_large")
                out.write(chunk)
        if size == 0:
            raise bad_request("That file is empty.", code="file_empty")
        if not _content_matches(ext, head):
            raise bad_request("That file's contents don't match its extension.", code="file_type")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return size, allowed[ext]


def save_private(upload: UploadFile, subdir: str, allowed: dict, max_bytes: int) -> dict:
    ext = Path(upload.filename or "").suffix.lower()
    key = f"{subdir}/{random_token(12)}{ext}"
    size, content_type = _write_upload(upload, resolve_key(key), allowed, max_bytes)
    return {"key": key, "size": size, "content_type": content_type, "filename": safe_filename(upload.filename)}


def save_public_image(upload: UploadFile, max_bytes: int = 8 * 1024 * 1024) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    name = f"{random_token(12)}{ext}"
    _write_upload(upload, settings.uploads_dir / "products" / name, IMAGE_TYPES, max_bytes)
    return f"/assets/uploads/products/{name}"


def delete_private(key: str | None) -> None:
    if key:
        try:
            resolve_key(key).unlink(missing_ok=True)
        except Exception:
            pass


def delete_public_upload(url: str) -> None:
    prefix = "/assets/uploads/products/"
    if url.startswith(prefix):
        name = Path(url[len(prefix):]).name
        (settings.uploads_dir / "products" / name).unlink(missing_ok=True)


def playback_url(lesson_id: int, user_id: str) -> dict:
    expires = int(time.time()) + PLAYBACK_TTL_SECONDS
    signature = sign(f"play|{lesson_id}|{user_id}|{expires}")
    return {"url": f"/media/lessons/{lesson_id}?u={user_id}&e={expires}&s={signature}", "expires_at": expires}


def playback_allowed(lesson_id: int, user_id: str, expires: str, signature: str) -> bool:
    try:
        if int(expires) < time.time():
            return False
    except (TypeError, ValueError):
        return False
    return verify_signature(f"play|{lesson_id}|{user_id}|{expires}", signature)
