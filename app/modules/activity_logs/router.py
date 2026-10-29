from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles, scoped_society_id
from app.core.enums import AdminPermission, UserType
from app.core.pagination import Page, PageParams, paginate
from app.modules.activity_logs.models import ActivityLog
from app.modules.activity_logs.schemas import ActivityLogOut

router = APIRouter(prefix="/activity-logs", tags=["Activity Logs"])


@router.get("", response_model=Page[ActivityLogOut])
def list_activity_logs(
    society_id: int | None = None,
    entity: str | None = None,
    entity_id: int | None = None,
    user_type: str | None = None,
    action: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    params: PageParams = Depends(),
    user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.REPORTS)),
    db: Session = Depends(get_db),
):
    stmt = select(ActivityLog).order_by(ActivityLog.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(ActivityLog.society_id == sid)
    if entity:
        stmt = stmt.where(ActivityLog.entity == entity)
    if entity_id:
        stmt = stmt.where(ActivityLog.entity_id == entity_id)
    if user_type:
        stmt = stmt.where(ActivityLog.user_type == user_type)
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    if date_from:
        stmt = stmt.where(ActivityLog.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(ActivityLog.created_at <= datetime.combine(date_to, time.max))
    return paginate(db, stmt, params)
