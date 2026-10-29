from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core import realtime
from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user, require_roles, scoped_society_id
from app.core.enums import AccountStatus, AdminPermission, NotificationCategory, ResidentStatus, UserType
from app.core.errors import not_found
from app.core.pagination import Page, PageParams, paginate
from app.core.timeutils import now_ist
from app.modules.activity_logs.service import log_activity
from app.modules.notifications.models import DeviceToken, Notification
from app.modules.notifications.schemas import (
    DeviceTokenDelete,
    DeviceTokenIn,
    ManualNotificationIn,
    ManualNotificationResult,
    NotificationOut,
    UnreadCountOut,
)
from app.modules.notifications.service import notify

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _mine(user: CurrentUser):
    return (Notification.user_type == user.user_type, Notification.user_id == user.id)


@router.get("", response_model=Page[NotificationOut])
def list_my_notifications(
    category: str | None = None,
    unread_only: bool = False,
    params: PageParams = Depends(),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Notification).where(*_mine(user)).order_by(Notification.id.desc())
    if category:
        stmt = stmt.where(Notification.category == category)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    return paginate(db, stmt, params)


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Notification.category, func.count())
        .where(*_mine(user), Notification.is_read.is_(False))
        .group_by(Notification.category)
    ).all()
    by_category = {c: n for c, n in rows}
    return {"total": sum(by_category.values()), "by_category": by_category}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.scalar(select(Notification).where(Notification.id == notification_id, *_mine(user)))
    if not n:
        raise not_found("Notification")
    if not n.is_read:
        n.is_read = True
        n.read_at = now_ist()
        realtime.quiet(db)
        db.commit()
    return n


@router.post("/read-all")
def mark_all_read(category: str | None = None, user: CurrentUser = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    stmt = update(Notification).where(*_mine(user), Notification.is_read.is_(False))
    if category:
        stmt = stmt.where(Notification.category == category)
    result = db.execute(stmt.values(is_read=True, read_at=now_ist()))
    realtime.quiet(db)
    db.commit()
    return {"updated": result.rowcount}


@router.post("/device-tokens", status_code=201)
def register_device_token(data: DeviceTokenIn, user: CurrentUser = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    token = db.scalar(select(DeviceToken).where(DeviceToken.fcm_token == data.fcm_token))
    if token:  # same device, possibly a different user now
        token.user_type, token.user_id, token.platform = user.user_type, user.id, data.platform
    else:
        db.add(DeviceToken(user_type=user.user_type, user_id=user.id, fcm_token=data.fcm_token,
                           platform=data.platform))
    db.commit()
    return {"message": "Device registered"}


@router.delete("/device-tokens")
def remove_device_token(data: DeviceTokenDelete, user: CurrentUser = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    db.execute(delete(DeviceToken).where(DeviceToken.fcm_token == data.fcm_token,
                                         DeviceToken.user_type == user.user_type, DeviceToken.user_id == user.id))
    db.commit()
    return {"message": "Device removed"}


@router.post("/send", response_model=ManualNotificationResult)
def send_manual_notification(
    data: ManualNotificationIn,
    user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN,
                                              perm=AdminPermission.NOTIFICATIONS)),
    db: Session = Depends(get_db),
):
    """Manual push: Super Admin to anyone, Society Admin to their own society only."""
    from app.modules.residents.models import Resident
    from app.modules.society_admins.models import SocietyAdmin
    from app.modules.staff.models import Staff

    society_id = scoped_society_id(user, data.society_id)
    recipients: list[tuple[str, int]] = []

    if data.target in ("residents", "everyone"):
        stmt = select(Resident.id).where(Resident.status == ResidentStatus.ACTIVE)
        if society_id is not None:
            stmt = stmt.where(Resident.society_id == society_id)
        if data.resident_ids:
            stmt = stmt.where(Resident.id.in_(data.resident_ids))
        recipients += [(UserType.RESIDENT, rid) for rid in db.scalars(stmt).all()]
    if data.target in ("society_admins", "everyone") and user.is_super_admin:
        stmt = select(SocietyAdmin.id).where(SocietyAdmin.status == AccountStatus.ACTIVE)
        if society_id is not None:
            stmt = stmt.where(SocietyAdmin.society_id == society_id)
        recipients += [(UserType.SOCIETY_ADMIN, aid) for aid in db.scalars(stmt).all()]
    if data.target in ("staff", "everyone"):
        stmt = select(Staff.id).where(Staff.status == AccountStatus.ACTIVE)
        if society_id is not None:
            stmt = stmt.where(Staff.society_id == society_id)
        recipients += [(UserType.STAFF, sid) for sid in db.scalars(stmt).all()]

    for user_type, uid in recipients:
        notify(db, user_type, uid, NotificationCategory.GENERAL, data.title, data.body, sms=data.sms)
    log_activity(db, user, "notification.manual_send", "notification", None,
                 new={"title": data.title, "target": data.target, "recipients": len(recipients)},
                 society_id=society_id)
    db.commit()
    return {"recipients": len(recipients)}
