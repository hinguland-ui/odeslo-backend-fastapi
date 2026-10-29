from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import NotificationCategory, RequestStatus, UserType
from app.core.errors import bad_request
from app.core.timeutils import now_ist
from app.modules.activity_logs.service import log_activity
from app.modules.notifications.service import notify
from app.modules.requests.models import ResidentRequest

FINAL = (RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.CLOSED)


def update_status(db: Session, req: ResidentRequest, actor: CurrentUser, status: str, note: str | None) -> None:
    if req.status in (RequestStatus.REJECTED, RequestStatus.CLOSED):
        raise bad_request(f"Request is already {req.status}")
    if actor.user_type == UserType.STAFF and status not in (RequestStatus.IN_PROGRESS, RequestStatus.CLOSED):
        raise bad_request("Staff can only mark requests in progress or closed")
    old = req.status
    req.status = status
    if note:
        req.admin_note = note
    if status in FINAL:
        req.resolved_at = now_ist()
    log_activity(db, actor, "request.status", "request", req.id, {"status": old}, {"status": status, "note": note},
                 society_id=req.society_id)
    notify(db, UserType.RESIDENT, req.resident_id, NotificationCategory.REQUEST,
           f"Request {req.request_no} {status.replace('_', ' ')}", note or f"Your {req.type} request was updated.",
           ref_type="request", ref_id=req.id)
