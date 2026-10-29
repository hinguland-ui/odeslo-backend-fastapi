from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AdminPermission, ComplaintCategory, ComplaintStatus, Feature, Priority, UserType
from app.core.errors import bad_request, not_found
from app.core.files import IMAGE_TYPES, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.utils import generate_ref, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.complaints import service
from app.modules.complaints.models import Complaint
from app.modules.complaints.schemas import (
    CommentCreate,
    CommentOut,
    ComplaintAssign,
    ComplaintDetailOut,
    ComplaintOut,
    ComplaintReopen,
    ComplaintStatusUpdate,
)
from app.modules.societies.service import ensure_feature_enabled
from app.modules.staff.service import get_active_staff_of_society

router = APIRouter(prefix="/complaints", tags=["Complaints"])

ActiveResident = require_roles(UserType.RESIDENT)
ComplaintAdmins = require_roles(UserType.SOCIETY_ADMIN, perm=AdminPermission.COMPLAINTS)
ComplaintViewers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.COMPLAINTS)
Participants = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT, UserType.STAFF,
                             perm=AdminPermission.COMPLAINTS)


def _get(db: Session, user: CurrentUser, complaint_id: int) -> Complaint:
    complaint = db.get(Complaint, complaint_id)
    if not complaint:
        raise not_found("Complaint")
    if user.is_resident:
        if complaint.unit_id != user.unit_id:
            raise not_found("Complaint")
    elif user.user_type == UserType.STAFF:
        if complaint.assigned_to != user.id:
            raise not_found("Complaint")
    else:
        ensure_society_access(user, complaint.society_id, "Complaint")
    return complaint


# ---------- Resident ----------
@router.post("", response_model=ComplaintOut, status_code=201)
def raise_complaint(category: ComplaintCategory = Form(...), description: str = Form(..., min_length=5),
                    priority: Priority = Form(Priority.MEDIUM), photo: UploadFile | None = File(None),
                    user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    ensure_feature_enabled(db, user.society_id, Feature.COMPLAINTS)
    complaint = Complaint(complaint_no=generate_ref("CM"), society_id=user.society_id, resident_id=user.id,
                          unit_id=user.unit_id, category=category, description=description, priority=priority,
                          photo=save_upload(photo, "complaints", IMAGE_TYPES, db=db, user=user, audience="unit", society_id=user.society_id, unit_id=user.unit_id) if photo else None,
                          status=ComplaintStatus.OPEN)
    db.add(complaint)
    db.flush()
    service.notify_new(db, complaint)
    log_activity(db, user, "complaint.create", "complaint", complaint.id, new=snapshot(complaint))
    db.commit()
    return complaint


@router.get("/my", response_model=Page[ComplaintOut])
def my_complaints(status: str | None = None, params: PageParams = Depends(),
                  user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    stmt = select(Complaint).where(Complaint.unit_id == user.unit_id).order_by(Complaint.id.desc())
    if status:
        stmt = stmt.where(Complaint.status == status)
    return paginate(db, stmt, params)


@router.post("/{complaint_id}/close", response_model=ComplaintOut)
def close_complaint(complaint_id: int, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    complaint = _get(db, user, complaint_id)
    if complaint.status != ComplaintStatus.RESOLVED:
        raise bad_request("Only resolved complaints can be closed")
    service.set_status(db, complaint, user, ComplaintStatus.CLOSED, "Closed by resident")
    db.commit()
    return complaint


@router.post("/{complaint_id}/reopen", response_model=ComplaintOut)
def reopen_complaint(complaint_id: int, data: ComplaintReopen, user: CurrentUser = Depends(ActiveResident),
                     db: Session = Depends(get_db)):
    complaint = _get(db, user, complaint_id)
    if complaint.status not in (ComplaintStatus.RESOLVED, ComplaintStatus.CLOSED):
        raise bad_request("Only resolved or closed complaints can be reopened")
    complaint.reopened_count += 1
    complaint.resolved_at = complaint.closed_at = None
    service.set_status(db, complaint, user, ComplaintStatus.REOPENED, f"Reopened: {data.reason}")
    from app.modules.notifications.service import notify_society_admins

    notify_society_admins(db, complaint.society_id, "complaint", "Complaint reopened",
                          f"{complaint.complaint_no}: {data.reason}", perm="complaints", ref_type="complaint",
                          ref_id=complaint.id)
    if complaint.assigned_to:
        from app.modules.notifications.service import notify

        notify(db, UserType.STAFF, complaint.assigned_to, "complaint", "Complaint reopened",
               f"{complaint.complaint_no}: {data.reason}", ref_type="complaint", ref_id=complaint.id)
    db.commit()
    return complaint


# ---------- Staff ----------
@router.get("/assigned", response_model=list[ComplaintOut])
def assigned_to_me(include_closed: bool = False, user: CurrentUser = Depends(require_roles(UserType.STAFF)),
                   db: Session = Depends(get_db)):
    stmt = select(Complaint).where(Complaint.assigned_to == user.id).order_by(Complaint.id.desc())
    if not include_closed:
        stmt = stmt.where(Complaint.status.not_in((ComplaintStatus.CLOSED, ComplaintStatus.RESOLVED)))
    return db.scalars(stmt).all()


# ---------- Admin ----------
@router.get("", response_model=Page[ComplaintOut])
def list_complaints(society_id: int | None = None, status: str | None = None, category: str | None = None,
                    priority: str | None = None, assigned_to: int | None = None, open_only: bool = False,
                    params: PageParams = Depends(), user: CurrentUser = Depends(ComplaintViewers),
                    db: Session = Depends(get_db)):
    from app.core.enums import OPEN_COMPLAINT_STATUSES

    stmt = select(Complaint).order_by(Complaint.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Complaint.society_id == sid)
    if status:
        stmt = stmt.where(Complaint.status == status)
    if open_only:
        stmt = stmt.where(Complaint.status.in_(OPEN_COMPLAINT_STATUSES))
    if category:
        stmt = stmt.where(Complaint.category == category)
    if priority:
        stmt = stmt.where(Complaint.priority == priority)
    if assigned_to:
        stmt = stmt.where(Complaint.assigned_to == assigned_to)
    return paginate(db, stmt, params)


@router.post("/{complaint_id}/assign", response_model=ComplaintDetailOut)
def assign_complaint(complaint_id: int, data: ComplaintAssign, user: CurrentUser = Depends(ComplaintAdmins),
                     db: Session = Depends(get_db)):
    complaint = _get(db, user, complaint_id)
    staff = get_active_staff_of_society(db, data.staff_id, complaint.society_id)
    service.assign(db, complaint, user, staff, data.note)
    db.commit()
    return service.detail(db, complaint)


@router.post("/{complaint_id}/status", response_model=ComplaintDetailOut)
def update_complaint_status(complaint_id: int, data: ComplaintStatusUpdate,
                            user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN, UserType.STAFF,
                                                                      perm=AdminPermission.COMPLAINTS)),
                            db: Session = Depends(get_db)):
    """Admin or the assigned staff: In Progress / Resolved."""
    complaint = _get(db, user, complaint_id)
    service.update_status(db, complaint, user, data.status, data.note)
    db.commit()
    return service.detail(db, complaint)


# ---------- Shared ----------
@router.get("/{complaint_id}", response_model=ComplaintDetailOut)
def get_complaint(complaint_id: int, user: CurrentUser = Depends(Participants), db: Session = Depends(get_db)):
    return service.detail(db, _get(db, user, complaint_id))


@router.post("/{complaint_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(complaint_id: int, data: CommentCreate,
                user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN, UserType.RESIDENT, UserType.STAFF,
                                                          perm=AdminPermission.COMPLAINTS)),
                db: Session = Depends(get_db)):
    complaint = _get(db, user, complaint_id)
    if complaint.status == ComplaintStatus.CLOSED:
        raise bad_request("Complaint is closed")
    comment = service.add_comment(db, complaint, user, data.comment)
    if not user.is_resident:
        service.notify_resident(db, complaint, f"New comment: {data.comment[:100]}")
    else:
        from app.modules.notifications.service import notify_society_admins

        notify_society_admins(db, complaint.society_id, "complaint", f"Comment on {complaint.complaint_no}",
                              data.comment[:100], perm="complaints", ref_type="complaint", ref_id=complaint.id)
    db.flush()
    log_activity(db, user, "complaint.comment", "complaint", complaint.id, new={"comment": data.comment[:200]},
                 society_id=complaint.society_id)
    db.commit()
    return comment
