from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import realtime
from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user, require_roles
from app.core.enums import UserType
from app.core.errors import not_found
from app.core.files import read_stored
from app.modules.activity_logs.service import log_activity
from app.modules.files import service
from app.modules.files.models import StoredFile

router = APIRouter(prefix="/files", tags=["Files"])

SENSITIVE = ("owner_admin", "admin", "platform_admin")


@router.get("/usage/summary")
def storage_usage(user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN)), db: Session = Depends(get_db)):
    """Space used per society (files and megabytes), by kind of file. Informational: there is no per-society cap."""
    return service.usage(db)


@router.get("/{path:path}")
def download_file(path: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Uploads are encrypted, stored outside the public root, and opened only by people the file was shared with.

    "Not yours" and "does not exist" look the same, so nobody can probe which files exist.
    """
    f = db.scalar(select(StoredFile).where(StoredFile.path == path))
    if not f or not service.can_read(db, user, f):
        raise not_found("File")
    data = read_stored(f.path)
    if data is None:
        raise not_found("File")
    if f.audience in SENSITIVE:
        log_activity(db, user, "file.view", "stored_file", None, new={"path": f.path, "category": f.category},
                     society_id=f.society_id)
        realtime.quiet(db)
        db.commit()
    # the real name when we have it; older uploads have none, and a made-up id is never shown as a name
    ext = f.path[f.path.rfind("."):]
    if f.original_name:
        ascii_name = f.original_name.encode("ascii", "ignore").decode() or f"file{ext}"
        disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f.original_name)}"
    else:
        disposition = "inline"
    return Response(
        content=data,
        media_type=f.mime,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": disposition,
            "Cross-Origin-Resource-Policy": "same-site",
        },
    )
