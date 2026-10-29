from datetime import date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import realtime
from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AdminPermission, BookingStatus, JobStatus, NotificationCategory, PayoutStatus, UserType
from app.core.errors import bad_request, not_found
from app.core.geo import haversine_km
from app.core.files import IMAGE_TYPES, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.timeutils import now_ist, today_ist
from app.modules.activity_logs.service import log_activity
from app.modules.bookings.models import Booking
from app.modules.group_deals.service import get_editable_deal
from app.modules.service_jobs import service
from app.modules.notifications.service import notify
from app.modules.service_jobs.models import JobLocation, JobMessage, ServiceJob
from app.modules.service_jobs.schemas import (
    AssigneeStatus,
    JobAdminOut,
    JobAssign,
    JobCancel,
    JobCreate,
    JobDetailOut,
    JobIssue,
    JobOut,
    JobPayout,
    JobsFromDeal,
    LocationIn,
    LocationOut,
    MessageIn,
    MessageOut,
)

router = APIRouter(prefix="/service-jobs", tags=["Service Jobs"])

JobAdmins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.SERVICE_JOBS)
JobManagers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.SERVICE_JOBS)
Assignees = require_roles(UserType.STAFF, UserType.PARTNER)
AnyJobViewer = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT, UserType.STAFF,
                             UserType.PARTNER, perm=AdminPermission.SERVICE_JOBS)


def _get_for_admin(db: Session, user: CurrentUser, job_id: int) -> ServiceJob:
    job = service.get_job_or_404(db, job_id)
    ensure_society_access(user, job.society_id, "Service job")
    return job


def _get_for_assignee(db: Session, user: CurrentUser, job_id: int) -> ServiceJob:
    job = service.get_job_or_404(db, job_id)
    if not service.is_assignee(user, job):
        raise not_found("Service job")
    return job


# ---------- Staff / Partner app (lite) ----------
@router.get("/my", response_model=list[JobOut])
def my_jobs(view: Literal["today", "upcoming", "history", "active"] = "today",
            user: CurrentUser = Depends(Assignees), db: Session = Depends(get_db)):
    stmt = select(ServiceJob).where(ServiceJob.assigned_to_type == user.user_type,
                                    ServiceJob.assigned_to_id == user.id)
    today = today_ist()
    if view == "today":
        stmt = stmt.where(ServiceJob.scheduled_at >= datetime.combine(today, time.min),
                          ServiceJob.scheduled_at <= datetime.combine(today, time.max))
    elif view == "upcoming":
        stmt = stmt.where(ServiceJob.scheduled_at > datetime.combine(today, time.max),
                          ServiceJob.status.not_in(service.FINAL))
    elif view == "active":
        stmt = stmt.where(ServiceJob.status.not_in(service.FINAL))
    else:
        stmt = stmt.where(ServiceJob.status.in_(service.FINAL))
    return db.scalars(stmt.order_by(ServiceJob.scheduled_at)).all()


@router.post("/{job_id}/status", response_model=JobDetailOut)
def update_job_status(job_id: int, status: AssigneeStatus = Form(...), note: str | None = Form(None),
                      photo: UploadFile | None = File(None), user: CurrentUser = Depends(Assignees),
                      db: Session = Depends(get_db)):
    """Accepted -> On the way -> Started -> Completed (completion needs a photo or note)."""
    job = _get_for_assignee(db, user, job_id)
    photo_path = save_upload(photo, "jobs", IMAGE_TYPES, db=db, user=user, audience="job", society_id=job.society_id, job_id=job.id, category="jobs") if photo else None
    service.update_status_by_assignee(db, job, user, status, note, photo_path)
    db.commit()
    return service.job_detail(db, job, user)


@router.post("/{job_id}/issue", response_model=JobDetailOut)
def report_issue(job_id: int, data: JobIssue, user: CurrentUser = Depends(Assignees), db: Session = Depends(get_db)):
    """Mark an issue / request reschedule. The admin is notified."""
    job = _get_for_assignee(db, user, job_id)
    service.report_issue(db, job, user, data.note, data.reschedule_to)
    db.commit()
    return service.job_detail(db, job, user)


# ---------- Admin ----------
@router.post("", response_model=JobAdminOut, status_code=201)
def create_job(data: JobCreate, user: CurrentUser = Depends(JobManagers), db: Session = Depends(get_db)):
    booking = db.get(Booking, data.booking_id)
    if not booking:
        raise not_found("Booking")
    ensure_society_access(user, booking.society_id, "Booking")
    job = service.create_job_for_booking(db, booking, user)
    if data.notes:
        job.notes = data.notes
    if data.assign:
        service.assign(db, job, user, data.assign.assigned_to_type, data.assign.assigned_to_id,
                       data.assign.scheduled_at, data.assign.slot)
    db.commit()
    return job


@router.post("/from-deal/{deal_id}", response_model=list[JobAdminOut], status_code=201)
def create_jobs_from_deal(deal_id: int, data: JobsFromDeal, user: CurrentUser = Depends(JobManagers),
                          db: Session = Depends(get_db)):
    """Create one service job per confirmed booking (skips bookings that already have a job)."""
    deal = get_editable_deal(db, user, deal_id)
    existing = select(ServiceJob.booking_id).where(ServiceJob.booking_id.is_not(None))
    stmt = select(Booking).where(Booking.deal_id == deal.id, Booking.status == BookingStatus.CONFIRMED,
                                 Booking.id.not_in(existing))
    if user.is_society_admin:
        stmt = stmt.where(Booking.society_id == user.society_id)
    assignee_type = data.assigned_to_type or ("partner" if deal.partner_id else None)
    assignee_id = data.assigned_to_id or deal.partner_id
    jobs = []
    for booking in db.scalars(stmt.order_by(Booking.created_at)).all():
        job = service.create_job_for_booking(db, booking, user)
        if assignee_type and assignee_id:
            service.assign(db, job, user, assignee_type, assignee_id, data.scheduled_at)
        jobs.append(job)
    db.commit()
    return jobs


@router.get("", response_model=Page[JobAdminOut])
def list_jobs(society_id: int | None = None, status: str | None = None, deal_id: int | None = None,
              assigned_to_type: str | None = None, assigned_to_id: int | None = None, escalated: bool | None = None,
              scheduled_on: date | None = None, params: PageParams = Depends(),
              user: CurrentUser = Depends(JobAdmins), db: Session = Depends(get_db)):
    stmt = select(ServiceJob).order_by(ServiceJob.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(ServiceJob.society_id == sid)
    if status:
        stmt = stmt.where(ServiceJob.status == status)
    if deal_id:
        stmt = stmt.where(ServiceJob.deal_id == deal_id)
    if assigned_to_type:
        stmt = stmt.where(ServiceJob.assigned_to_type == assigned_to_type)
    if assigned_to_id:
        stmt = stmt.where(ServiceJob.assigned_to_id == assigned_to_id)
    if escalated is not None:
        stmt = stmt.where(ServiceJob.escalated.is_(escalated))
    if scheduled_on:
        stmt = stmt.where(ServiceJob.scheduled_at >= datetime.combine(scheduled_on, time.min),
                          ServiceJob.scheduled_at <= datetime.combine(scheduled_on, time.max))
    return paginate(db, stmt, params)


@router.post("/{job_id}/assign", response_model=JobDetailOut)
def assign_job(job_id: int, data: JobAssign, user: CurrentUser = Depends(JobManagers), db: Session = Depends(get_db)):
    """Assign or reassign to society staff or a verified service partner of the society."""
    job = _get_for_admin(db, user, job_id)
    service.assign(db, job, user, data.assigned_to_type, data.assigned_to_id, data.scheduled_at, data.slot)
    job.escalated = False
    db.commit()
    return service.job_detail(db, job, user)


@router.post("/{job_id}/cancel", response_model=JobDetailOut)
def cancel_job(job_id: int, data: JobCancel, user: CurrentUser = Depends(JobManagers), db: Session = Depends(get_db)):
    job = _get_for_admin(db, user, job_id)
    service.cancel(db, job, user, data.reason)
    db.commit()
    return service.job_detail(db, job, user)


@router.post("/{job_id}/payout", response_model=JobDetailOut)
def mark_payout_paid(job_id: int, data: JobPayout, user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN)),
                     db: Session = Depends(get_db)):
    job = service.get_job_or_404(db, job_id)
    if job.assigned_to_type != "partner" or job.status != JobStatus.COMPLETED:
        raise bad_request("Payouts apply only to completed partner jobs")
    if job.payout_status == PayoutStatus.PAID:
        raise bad_request("Payout already marked as paid")
    if data.amount is not None:
        job.payout_amount = data.amount
    job.payout_status, job.payout_reference = PayoutStatus.PAID, data.reference
    log_activity(db, user, "service_job.payout", "service_job", job.id,
                 new={"amount": job.payout_amount, "reference": data.reference}, society_id=job.society_id)
    db.commit()
    return service.job_detail(db, job, user)


# ---------- Shared detail ----------
@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int, user: CurrentUser = Depends(AnyJobViewer), db: Session = Depends(get_db)):
    job = service.get_job_or_404(db, job_id)
    if user.is_resident:
        if job.unit_id != user.unit_id:
            raise not_found("Service job")
    elif user.user_type in (UserType.STAFF, UserType.PARTNER):
        if not service.is_assignee(user, job):
            raise not_found("Service job")
    else:
        ensure_society_access(user, job.society_id, "Service job")
    return service.job_detail(db, job, user)


# ---------- Chat & live location (resident <-> vendor / staff, society admin can watch and reply) ----------
LIVE_STATUSES = (JobStatus.ON_THE_WAY, JobStatus.STARTED)  # never while the job is merely accepted (could be days away)
AVG_SPEED_KMPH = 25  # city traffic, only for the "about N min away" hint


def _participant(db: Session, user: CurrentUser, job_id: int) -> ServiceJob:
    """Same visibility rule as the job detail: the resident's unit, the assignee, the society admin, super admin."""
    job = service.get_job_or_404(db, job_id)
    if user.is_resident:
        if job.unit_id != user.unit_id:
            raise not_found("Service job")
    elif user.user_type in (UserType.STAFF, UserType.PARTNER):
        if not service.is_assignee(user, job):
            raise not_found("Service job")
    else:
        ensure_society_access(user, job.society_id, "Service job")
    return job


def _job_rooms(job: ServiceJob) -> list[str]:
    rooms = [realtime.room_society(job.society_id), realtime.SUPER]
    if job.assigned_to_type and job.assigned_to_id:
        rooms.append(realtime.room_user(job.assigned_to_type, job.assigned_to_id))
    return rooms


@router.get("/{job_id}/messages", response_model=list[MessageOut])
def list_messages(job_id: int, after_id: int = 0, user: CurrentUser = Depends(AnyJobViewer),
                  db: Session = Depends(get_db)):
    job = _participant(db, user, job_id)
    stmt = (select(JobMessage).where(JobMessage.job_id == job.id, JobMessage.id > after_id)
            .order_by(JobMessage.id).limit(300))
    return db.scalars(stmt).all()


@router.post("/{job_id}/messages", response_model=MessageOut, status_code=201)
def send_message(job_id: int, data: MessageIn, user: CurrentUser = Depends(AnyJobViewer),
                 db: Session = Depends(get_db)):
    job = _participant(db, user, job_id)
    if job.status == JobStatus.CANCELLED:
        raise bad_request("This job was cancelled, so the chat is closed")
    msg = JobMessage(job_id=job.id, sender_type=user.user_type, sender_id=user.id, sender_name=user.name,
                     body=data.body.strip())
    db.add(msg)
    db.flush()
    realtime.queue(db, _job_rooms(job), {"type": "chat", "job_id": job.id, "message_id": msg.id})
    # phone push for the other side (the open web page is already updated by the event above)
    others: list[tuple[str, int]] = []
    if job.resident_id and not (user.is_resident and user.id == job.resident_id):
        others.append((UserType.RESIDENT, job.resident_id))
    if job.assigned_to_type and job.assigned_to_id and not service.is_assignee(user, job):
        others.append((job.assigned_to_type, job.assigned_to_id))
    for t, i in others:
        notify(db, t, i, NotificationCategory.SERVICE_JOB, f"Message from {user.name}", data.body.strip()[:120],
               ref_type="service_job", ref_id=job.id, in_app=False)
    realtime.quiet(db)
    db.commit()
    return msg


@router.post("/{job_id}/location", response_model=LocationOut)
def share_location(job_id: int, data: LocationIn, user: CurrentUser = Depends(Assignees),
                   db: Session = Depends(get_db)):
    """The vendor's phone / browser sends its position while the job is active (every few seconds)."""
    job = _get_for_assignee(db, user, job_id)
    if job.status not in LIVE_STATUSES:
        raise bad_request("Location is shared only while you are on the way or working, until the job is completed")
    loc = db.get(JobLocation, job.id)
    if loc:
        loc.lat, loc.lng, loc.accuracy, loc.updated_at = data.lat, data.lng, data.accuracy, now_ist()
    else:
        loc = JobLocation(job_id=job.id, lat=data.lat, lng=data.lng, accuracy=data.accuracy)
        db.add(loc)
    db.flush()
    realtime.queue(db, _job_rooms(job), {"type": "job_location", "job_id": job.id})
    realtime.quiet(db)
    db.commit()
    return _location_out(db, job, loc)


def _location_out(db: Session, job: ServiceJob, loc: JobLocation | None) -> dict:
    from app.modules.societies.models import Society

    society = db.get(Society, job.society_id)
    dest = {"lat": society.lat, "lng": society.lng, "label": society.name} if society else None
    fresh = bool(loc and (now_ist() - loc.updated_at).total_seconds() < 180)
    live = bool(loc) and job.status in LIVE_STATUSES
    out = {"sharing": live and fresh, "job_status": job.status, "destination": dest, "lat": None, "lng": None,
           "accuracy": None, "updated_at": None, "distance_km": None, "eta_minutes": None}
    if loc and live:
        out.update(lat=loc.lat, lng=loc.lng, accuracy=loc.accuracy, updated_at=loc.updated_at)
        if dest:
            km = haversine_km(loc.lat, loc.lng, dest["lat"], dest["lng"])
            out["distance_km"] = round(km, 2)
            if job.status == JobStatus.ON_THE_WAY:
                out["eta_minutes"] = max(1, round(km / AVG_SPEED_KMPH * 60))
    return out


@router.get("/{job_id}/location", response_model=LocationOut)
def get_location(job_id: int, user: CurrentUser = Depends(AnyJobViewer), db: Session = Depends(get_db)):
    job = _participant(db, user, job_id)
    return _location_out(db, job, db.get(JobLocation, job.id))
