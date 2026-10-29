"""Secure file uploads.

Every upload is: checked against the super admin's limits (size + formats), verified by its real content (magic bytes,
no scripts inside PDFs), images are re-encoded (removes GPS / EXIF and anything hidden in the file), encrypted on disk
with a server key, saved under a random name outside any public folder, and registered in `stored_files` with the
audience that is allowed to read it. Reading goes only through the authenticated /files API.
"""

import base64
import io
import logging
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core import upload_limits
from app.core.config import settings
from app.core.errors import bad_request

log = logging.getLogger("odeslo.files")

IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
DOCUMENT_TYPES = {**IMAGE_TYPES, "application/pdf": ".pdf"}
SPREADSHEET_TYPES = {
    "text/csv": ".csv",
    "application/vnd.ms-excel": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
MIME_BY_EXT = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf"}

_ENC_MAGIC = b"ODFE1"  # files written by this module start with this, older plain files do not
Image.MAX_IMAGE_PIXELS = 50_000_000  # decompression-bomb guard
_MAX_SIDE = 4000  # larger photos are scaled down (saves storage)
_PDF_FORBIDDEN = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA")


def upload_root() -> Path:
    root = Path(settings.UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


# ------------------------------------------------------------------ encryption at rest
_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet:
        return _fernet
    key = (settings.FILE_ENCRYPTION_KEY or "").strip()
    if not key:
        # no key configured: create one next to the uploads (dev). In production set FILE_ENCRYPTION_KEY and back it up.
        key_file = upload_root().parent / ".file_key"
        if key_file.exists():
            key = key_file.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            key_file.write_text(key)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            log.warning("FILE_ENCRYPTION_KEY is not set. A key was created at %s. Back it up: without it files cannot be read.", key_file)
    _fernet = Fernet(key.encode())
    return _fernet


def encrypt(data: bytes) -> bytes:
    return _ENC_MAGIC + _cipher().encrypt(data)


def decrypt(blob: bytes) -> bytes:
    if not blob.startswith(_ENC_MAGIC):
        return blob  # file saved before encryption existed
    try:
        return _cipher().decrypt(blob[len(_ENC_MAGIC):])
    except InvalidToken:
        log.error("A stored file could not be decrypted (wrong FILE_ENCRYPTION_KEY?)")
        raise bad_request("This file cannot be opened right now")


# ------------------------------------------------------------------ validation
_SIGNATURES = {
    ".jpg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".webp": [b"RIFF"],
    ".pdf": [b"%PDF"],
}


def _kind_of(allowed: dict[str, str]) -> str | None:
    """'image' / 'document' for the standard sets (limits come from the super admin); None for e.g. spreadsheets."""
    if allowed is IMAGE_TYPES or allowed == IMAGE_TYPES:
        return "image"
    if allowed is DOCUMENT_TYPES or allowed == DOCUMENT_TYPES:
        return "document"
    return None


def read_upload(file: UploadFile, allowed: dict[str, str], max_mb: int | None = None) -> tuple[bytes, str]:
    kind = _kind_of(allowed)
    if kind:
        allowed = upload_limits.allowed_for(kind)
        max_mb = upload_limits.max_mb_for(kind)
    limit_mb = max_mb or settings.MAX_UPLOAD_MB
    max_bytes = limit_mb * 1024 * 1024
    ext = allowed.get((file.content_type or "").lower())
    if not ext:
        names = ", ".join(sorted({e.lstrip(".").upper() for e in allowed.values()}))
        raise bad_request(f"This file type is not allowed. Allowed: {names}")
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise bad_request(f"File too large. Maximum size is {limit_mb} MB")
    if not data:
        raise bad_request("Empty file")
    signatures = _SIGNATURES.get(ext)
    if signatures and not any(data.startswith(sig) for sig in signatures):
        raise bad_request("File content does not match its type")
    if ext == ".webp" and data[8:12] != b"WEBP":
        raise bad_request("File content does not match its type")
    if ext == ".pdf" and any(tag in data for tag in _PDF_FORBIDDEN):
        raise bad_request("This PDF contains scripts or attachments, which are not allowed")
    return data, ext


def _clean_image(data: bytes, ext: str) -> bytes:
    """Decode and re-encode: drops EXIF / GPS / hidden payloads, fixes rotation, caps the size."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise bad_request("This image is damaged or not a real image")
    img = ImageOps.exif_transpose(img)
    if max(img.size) > _MAX_SIDE:
        img.thumbnail((_MAX_SIDE, _MAX_SIDE))
    out = io.BytesIO()
    if ext == ".jpg":
        img.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
    elif ext == ".png":
        img.save(out, "PNG", optimize=True)
    else:
        img.save(out, "WEBP", quality=88)
    return out.getvalue()


# ------------------------------------------------------------------ saving
def save_upload(
    file: UploadFile,
    folder: str,
    allowed: dict[str, str] | None = None,
    *,
    db: Session,
    audience: str,
    user=None,
    society_id: int | None = None,
    unit_id: int | None = None,
    job_id: int | None = None,
    category: str | None = None,
) -> str:
    """Validate, clean, encrypt and store the upload; returns its relative path (e.g. 'residents/documents/ab12….pdf').

    `audience` decides who can open it later (see StoredFile). The file is removed again if the request fails
    before the database commit, so a half-finished form never leaves files behind.
    """
    from app.modules.files.models import StoredFile

    data, ext = read_upload(file, allowed or DOCUMENT_TYPES)
    if ext != ".pdf":
        data = _clean_image(data, ext)
    file_id = uuid.uuid4().hex
    rel = f"{folder}/{file_id}{ext}"
    target = upload_root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encrypt(data))

    db.add(StoredFile(
        id=file_id, path=rel, category=category or folder.replace("/", "_"), audience=audience, society_id=society_id,
        unit_id=unit_id, job_id=job_id, uploader_type=getattr(user, "user_type", None), uploader_id=getattr(user, "id", None),
        original_name=clean_name(file.filename, ext), mime=MIME_BY_EXT[ext], size_bytes=len(data), encrypted=True))
    db.info.setdefault("new_files", []).append(rel)
    return rel


def clean_name(name: str | None, ext: str) -> str | None:
    """The name the person's file had, made safe to show and to put in a header (no folders, no control characters)."""
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '"<>|:*?').strip()
    if not base or len(base) > 190:
        base = base[:190].strip()
    if not base:
        return None
    return base if base.lower().endswith(ext) or (ext == ".jpg" and base.lower().endswith(".jpeg")) else base + ext


def release_upload(db: Session, relative_path: str | None) -> None:
    """The record no longer uses this file (replaced / removed): drop its registry row and delete it after the commit."""
    from sqlalchemy import select

    from app.modules.files.models import StoredFile

    if not relative_path:
        return
    row = db.scalar(select(StoredFile).where(StoredFile.path == relative_path))
    if row:
        db.delete(row)
    db.info.setdefault("drop_files", []).append(relative_path)


def resolve_upload(relative_path: str) -> Path | None:
    root = upload_root()
    path = (root / relative_path).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def read_stored(relative_path: str) -> bytes | None:
    path = resolve_upload(relative_path)
    return decrypt(path.read_bytes()) if path else None


def _delete_files(paths: list[str]) -> None:
    for rel in paths:
        path = resolve_upload(rel)
        if path:
            try:
                path.unlink()
            except OSError:
                log.warning("Could not delete %s", rel)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    session.info.pop("new_files", None)
    _delete_files(session.info.pop("drop_files", []))


@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(session: Session, _previous) -> None:
    _delete_files(session.info.pop("new_files", []))  # saved during a request that failed: remove
    session.info.pop("drop_files", None)
