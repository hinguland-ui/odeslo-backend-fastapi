"""Central notification dispatcher: in-app (DB row), push (FCM) and SMS / WhatsApp.

Channel choice per event follows the Notification Matrix in the spec; callers pass the flags.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import realtime
from app.core.enums import AccountStatus, AdminRole, ResidentStatus, UserType
from app.integrations.messaging import send_sms, send_whatsapp
from app.integrations.push import send_push
from app.modules.notifications.models import DeviceToken, Notification

logger = logging.getLogger("odeslo.notifications")


def _recipient_mobile(db: Session, user_type: str, user_id: int) -> str | None:
    from app.core.deps import _load_user

    user = _load_user(db, user_type, user_id)
    return user.mobile if user else None


def _push_allowed(db: Session, user_type: str, user_id: int, category: str) -> bool:
    if user_type != UserType.RESIDENT:
        return True
    from app.modules.residents.models import Resident

    resident = db.get(Resident, user_id)
    prefs = (resident.notification_prefs or {}) if resident else {}
    return prefs.get(category, True)


def notify(
    db: Session,
    user_type: str,
    user_id: int,
    category: str,
    title: str,
    body: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    push: bool = True,
    in_app: bool = True,
    sms: bool = False,
    whatsapp: bool | None = None,
) -> None:
    """Notify one user. The in-app row is added to the session (committed by the caller)."""
    if in_app:
        db.add(Notification(user_type=user_type, user_id=user_id, category=category, title=title, body=body,
                            ref_type=ref_type, ref_id=ref_id))
        realtime.queue_user(db, user_type, user_id, {"type": "notification", "category": category, "title": title,
                                                      "body": body, "ref_type": ref_type, "ref_id": ref_id})
    if push and _push_allowed(db, user_type, user_id, category):
        tokens = db.scalars(
            select(DeviceToken.fcm_token).where(DeviceToken.user_type == user_type, DeviceToken.user_id == user_id)
        ).all()
        data = {"category": category, "ref_type": ref_type or "", "ref_id": str(ref_id or "")}
        invalid = send_push(list(tokens), title, body, data)
        if invalid:
            db.execute(delete(DeviceToken).where(DeviceToken.fcm_token.in_(invalid)))
    if sms or whatsapp:
        mobile = _recipient_mobile(db, user_type, user_id)
        if mobile:
            text = f"{title}: {body}"
            if sms:
                send_sms(mobile, text)
            if whatsapp if whatsapp is not None else sms:
                send_whatsapp(mobile, text)


def notify_society_admins(db: Session, society_id: int, category: str, title: str, body: str,
                          perm: str | None = None, **kwargs) -> None:
    from app.modules.society_admins.models import SocietyAdmin

    admins = db.scalars(
        select(SocietyAdmin).where(SocietyAdmin.society_id == society_id, SocietyAdmin.status == AccountStatus.ACTIVE)
    ).all()
    for admin in admins:
        if perm and admin.role != AdminRole.PRIMARY and perm not in (admin.permissions or []):
            continue
        notify(db, UserType.SOCIETY_ADMIN, admin.id, category, title, body, **kwargs)


def notify_society_residents(db: Session, society_id: int | None, category: str, title: str, body: str,
                             **kwargs) -> int:
    """Notify every active resident of a society (society_id None = all active residents on the platform)."""
    from app.modules.residents.models import Resident

    stmt = select(Resident.id).where(Resident.status == ResidentStatus.ACTIVE)
    if society_id is not None:
        stmt = stmt.where(Resident.society_id == society_id)
    ids = db.scalars(stmt).all()
    for rid in ids:
        notify(db, UserType.RESIDENT, rid, category, title, body, **kwargs)
    return len(ids)


def notify_super_admins(db: Session, category: str, title: str, body: str, **kwargs) -> None:
    from app.modules.super_admins.models import SuperAdmin

    for sa_id in db.scalars(select(SuperAdmin.id).where(SuperAdmin.status == AccountStatus.ACTIVE)).all():
        notify(db, UserType.SUPER_ADMIN, sa_id, category, title, body, **kwargs)
