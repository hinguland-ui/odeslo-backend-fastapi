"""Complaint flow: Open -> Assigned -> In Progress -> Resolved -> Closed / Reopened.
Resolved complaints auto-close after COMPLAINT_AUTO_CLOSE_HOURS without a resident response."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.enums import ComplaintStatus, NotificationCategory, UserType
from app.core.errors import bad_request
from app.core.timeutils import now_ist
from app.modules.activity_logs.service import log_activity
from app.modules.complaints.models import Complaint, ComplaintComment
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.residents.models import Resident
from app.modules.societies.models import Unit, Wing
from app.modules.staff.models import Staff

STAFF_FLOW = {
    ComplaintStatus.ASSIGNED: {ComplaintStatus.IN_PROGRESS, ComplaintStatus.RESOLVED},
    ComplaintStatus.REOPENED: {ComplaintStatus.IN_PROGRESS, ComplaintStatus.RESOLVED},
    ComplaintStatus.IN_PROGRESS: {ComplaintStatus.RESOLVED},
}
ADMIN_FLOW = {
    ComplaintStatus.OPEN: {ComplaintStatus.IN_PROGRESS, ComplaintStatus.RESOLVED},
    **STAFF_FLOW,
}


def add_comment(db: Session, complaint: Complaint, actor: CurrentUser | None, text: str,
                status_change: str | None = None) -> ComplaintComment:
    comment = ComplaintComment(complaint_id=complaint.id, author_type=actor.user_type if actor else "system",
                               author_id=actor.id if actor else 0, author_name=actor.name if actor else "System",
                               comment=text, status_change=status_change)
    db.add(comment)
    return comment


def notify_resident(db: Session, complaint: Complaint, message: str) -> None:
    notify(db, UserType.RESIDENT, complaint.resident_id, NotificationCategory.COMPLAINT,
           f"Complaint {complaint.complaint_no} update", message, ref_type="complaint", ref_id=complaint.id)


def set_status(db: Session, complaint: Complaint, actor: CurrentUser | None, new_status: str, note: str | None) -> None:
    old = complaint.status
    complaint.status = new_status
    if new_status == ComplaintStatus.RESOLVED:
        complaint.resolved_at = now_ist()
    if new_status == ComplaintStatus.CLOSED:
        complaint.closed_at = now_ist()
    add_comment(db, complaint, actor, note or f"Status changed to {new_status.replace('_', ' ')}", new_status)
    log_activity(db, actor, "complaint.status", "complaint", complaint.id, {"status": old},
                 {"status": new_status, "note": note}, society_id=complaint.society_id)


def assign(db: Session, complaint: Complaint, actor: CurrentUser, staff: Staff, note: str | None) -> None:
    if complaint.status in (ComplaintStatus.CLOSED,):
        raise bad_request("Closed complaints cannot be assigned")
    complaint.assigned_to = staff.id
    set_status(db, complaint, actor, ComplaintStatus.ASSIGNED, note or f"Assigned to {staff.name}")
    notify(db, UserType.STAFF, staff.id, NotificationCategory.COMPLAINT, "New complaint assigned",
           f"{complaint.complaint_no}: {complaint.category} - {complaint.description[:80]}", ref_type="complaint",
           ref_id=complaint.id, sms=True)
    notify_resident(db, complaint, f"Your complaint has been assigned to {staff.name}.")


def update_status(db: Session, complaint: Complaint, actor: CurrentUser, new_status: str, note: str | None) -> None:
    flow = STAFF_FLOW if actor.user_type == UserType.STAFF else ADMIN_FLOW
    if new_status not in flow.get(complaint.status, set()):
        raise bad_request(f"Cannot change complaint from {complaint.status} to {new_status}")
    set_status(db, complaint, actor, new_status, note)
    msg = "is being worked on." if new_status == ComplaintStatus.IN_PROGRESS else \
        "has been resolved. Please confirm and close it, or reopen if the issue persists."
    notify_resident(db, complaint, f"Your complaint {msg}" + (f" Note: {note}" if note else ""))


def auto_close_resolved(db: Session) -> int:
    cutoff = now_ist() - timedelta(hours=settings.COMPLAINT_AUTO_CLOSE_HOURS)
    complaints = db.scalars(select(Complaint).where(Complaint.status == ComplaintStatus.RESOLVED,
                                                    Complaint.resolved_at < cutoff)).all()
    for c in complaints:
        set_status(db, c, None, ComplaintStatus.CLOSED,
                   f"Auto-closed: no response within {settings.COMPLAINT_AUTO_CLOSE_HOURS} hours of resolution")
        notify_resident(db, c, "Your complaint was closed automatically.")
    return len(complaints)


def detail(db: Session, complaint: Complaint) -> dict:
    from app.modules.complaints.schemas import ComplaintOut

    resident = db.get(Resident, complaint.resident_id)
    unit = db.get(Unit, complaint.unit_id)
    wing = db.get(Wing, unit.wing_id) if unit else None
    staff = db.get(Staff, complaint.assigned_to) if complaint.assigned_to else None
    comments = db.scalars(select(ComplaintComment).where(ComplaintComment.complaint_id == complaint.id)
                          .order_by(ComplaintComment.id)).all()
    return {**ComplaintOut.model_validate(complaint).model_dump(), "comments": comments,
            "resident_name": resident.name if resident else None,
            "unit_label": f"{wing.name}-{unit.unit_no}" if unit and wing else None,
            "assigned_staff_name": staff.name if staff else None}


def notify_new(db: Session, complaint: Complaint) -> None:
    notify_society_admins(db, complaint.society_id, NotificationCategory.COMPLAINT, "New complaint",
                          f"{complaint.complaint_no} ({complaint.priority}): {complaint.category}", perm="complaints",
                          ref_type="complaint", ref_id=complaint.id)
