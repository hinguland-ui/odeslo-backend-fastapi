from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles, scoped_society_id
from app.core.enums import AdminPermission, AnnouncementCategory, Feature, NotificationCategory, UserType
from app.core.errors import not_found
from app.core.files import DOCUMENT_TYPES, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.utils import snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.announcements.models import Announcement
from app.modules.announcements.schemas import AnnouncementOut
from app.modules.notifications.service import notify_society_residents
from app.modules.societies.service import ensure_feature_enabled, get_society_or_404

router = APIRouter(prefix="/announcements", tags=["Announcements / Notices"])

Publishers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.ANNOUNCEMENTS)
Readers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT, UserType.STAFF)


def _out(a: Announcement) -> dict:
    return {**AnnouncementOut.model_validate(a).model_dump(), "is_platform": a.society_id is None}


@router.post("", response_model=AnnouncementOut, status_code=201)
def post_announcement(
    title: str = Form(..., min_length=3, max_length=190),
    body: str = Form(..., min_length=3),
    category: AnnouncementCategory = Form(AnnouncementCategory.NOTICE),
    is_pinned: bool = Form(False),
    society_id: int | None = Form(None),  # super admin only; empty = platform-wide
    push: bool = Form(True),
    sms: bool = Form(False),
    attachment: UploadFile | None = File(None),
    user: CurrentUser = Depends(Publishers),
    db: Session = Depends(get_db),
):
    target = user.society_id if user.is_society_admin else society_id
    if target is not None:
        get_society_or_404(db, target)
        ensure_feature_enabled(db, target, Feature.ANNOUNCEMENTS)
    ann = Announcement(society_id=target, title=title, body=body, category=category, is_pinned=is_pinned,
                       attachment=save_upload(attachment, "announcements", DOCUMENT_TYPES, db=db, user=user, audience="society", society_id=target) if attachment else None,
                       created_by_type=user.user_type, created_by=user.id)
    db.add(ann)
    db.flush()
    recipients = notify_society_residents(db, target, NotificationCategory.ANNOUNCEMENT, title, body[:180],
                                          ref_type="announcement", ref_id=ann.id, push=push, sms=sms)
    log_activity(db, user, "announcement.create", "announcement", ann.id,
                 new={**snapshot(ann), "recipients": recipients}, society_id=target)
    db.commit()
    return _out(ann)


@router.get("", response_model=Page[AnnouncementOut])
def list_announcements(society_id: int | None = None, category: str | None = None, params: PageParams = Depends(),
                       user: CurrentUser = Depends(Readers), db: Session = Depends(get_db)):
    """Residents / admins / staff see their society's notices plus platform-wide ones."""
    stmt = select(Announcement).order_by(Announcement.is_pinned.desc(), Announcement.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(or_(Announcement.society_id == sid, Announcement.society_id.is_(None)))
    if category:
        stmt = stmt.where(Announcement.category == category)
    page = paginate(db, stmt, params)
    page["items"] = [_out(a) for a in page["items"]]
    return page


@router.get("/{announcement_id}", response_model=AnnouncementOut)
def get_announcement(announcement_id: int, user: CurrentUser = Depends(Readers), db: Session = Depends(get_db)):
    ann = db.get(Announcement, announcement_id)
    if not ann or (not user.is_super_admin and ann.society_id not in (None, user.society_id)):
        raise not_found("Announcement")
    return _out(ann)


@router.delete("/{announcement_id}", status_code=204)
def delete_announcement(announcement_id: int, user: CurrentUser = Depends(Publishers), db: Session = Depends(get_db)):
    ann = db.get(Announcement, announcement_id)
    if not ann or (user.is_society_admin and ann.society_id != user.society_id):
        raise not_found("Announcement")
    db.delete(ann)
    log_activity(db, user, "announcement.delete", "announcement", announcement_id, old=snapshot(ann),
                 society_id=ann.society_id)
    db.commit()
