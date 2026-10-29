"""Service job tracking: Created -> Assigned -> Accepted -> On the way -> Started -> Completed
(or Rescheduled / Cancelled)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import (
    AssigneeType,
    BookingStatus,
    JobStatus,
    NotificationCategory,
    PayoutStatus,
    UserType,
)
from app.core.errors import bad_request, not_found
from app.core.timeutils import now_ist
from app.core.utils import generate_ref, mask_mobile
from app.modules.activity_logs.service import log_activity
from app.modules.bookings.models import Booking
from app.modules.bookings.service import recalc_deal
from app.modules.group_deals.models import GroupDeal
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.residents.models import Resident
from app.modules.service_jobs.models import ServiceJob, ServiceJobUpdate
from app.modules.service_partners.models import ServicePartner
from app.modules.service_partners.service import get_usable_partner
from app.modules.societies.models import Unit, Wing
from app.modules.staff.models import Staff
from app.modules.staff.service import get_active_staff_of_society

# assignee-driven transitions
ASSIGNEE_FLOW: dict[str, set[str]] = {
    JobStatus.ASSIGNED: {JobStatus.ACCEPTED},
    JobStatus.RESCHEDULED: {JobStatus.ACCEPTED},
    JobStatus.ACCEPTED: {JobStatus.ON_THE_WAY},
    JobStatus.ON_THE_WAY: {JobStatus.STARTED},
    JobStatus.STARTED: {JobStatus.COMPLETED},
}
FINAL = (JobStatus.COMPLETED, JobStatus.CANCELLED)
STATUS_LABEL = {
    JobStatus.ASSIGNED: "assigned to a service professional",
    JobStatus.ACCEPTED: "accepted by the service professional",
    JobStatus.ON_THE_WAY: "on the way",
    JobStatus.STARTED: "started",
    JobStatus.COMPLETED: "completed",
    JobStatus.RESCHEDULED: "rescheduled",
    JobStatus.CANCELLED: "cancelled",
}


def add_update(db: Session, job: ServiceJob, actor: CurrentUser | None, note: str | None = None,
               photo: str | None = None) -> None:
    db.add(ServiceJobUpdate(job_id=job.id, status=job.status, note=note, photo=photo,
                            updated_by_type=actor.user_type if actor else "system",
                            updated_by_id=actor.id if actor else 0))


def notify_resident(db: Session, job: ServiceJob) -> None:
    if job.resident_id:
        notify(db, UserType.RESIDENT, job.resident_id, NotificationCategory.SERVICE_JOB,
               f"Service {STATUS_LABEL.get(job.status, job.status)}",
               f"{job.title} ({job.job_no}) is {STATUS_LABEL.get(job.status, job.status)}.",
               ref_type="service_job", ref_id=job.id)


def create_job_for_booking(db: Session, booking: Booking, actor: CurrentUser | None) -> ServiceJob:
    if booking.status != BookingStatus.CONFIRMED:
        raise bad_request(f"Booking {booking.booking_no} is not confirmed")
    if db.scalar(select(ServiceJob.id).where(ServiceJob.booking_id == booking.id)):
        raise bad_request(f"A service job already exists for booking {booking.booking_no}")
    deal = db.get(GroupDeal, booking.deal_id)
    payout = round((deal.partner_payout_per_unit or 0) * booking.units_count, 2) if deal.partner_payout_per_unit else None
    job = ServiceJob(job_no=generate_ref("JB"), booking_id=booking.id, deal_id=deal.id, society_id=booking.society_id,
                     resident_id=booking.resident_id, unit_id=booking.unit_id,
                     title=f"{deal.title} x{booking.units_count}", status=JobStatus.CREATED, slot=booking.slot,
                     payout_amount=payout)
    db.add(job)
    db.flush()
    add_update(db, job, actor, "Job created")
    log_activity(db, actor, "service_job.create", "service_job", job.id,
                 new={"job_no": job.job_no, "booking_id": booking.id}, society_id=job.society_id)
    return job


def assign(db: Session, job: ServiceJob, actor: CurrentUser, assignee_type: str, assignee_id: int,
           scheduled_at=None, slot: str | None = None) -> ServiceJob:
    if job.status in FINAL:
        raise bad_request(f"Job is already {job.status}")
    if assignee_type == AssigneeType.STAFF:
        assignee = get_active_staff_of_society(db, assignee_id, job.society_id)
    else:
        assignee = get_usable_partner(db, assignee_id, job.society_id)
    old = {"status": job.status, "assigned_to_type": job.assigned_to_type, "assigned_to_id": job.assigned_to_id}
    job.assigned_to_type, job.assigned_to_id = assignee_type, assignee_id
    job.status = JobStatus.ASSIGNED
    if scheduled_at:
        job.scheduled_at = scheduled_at
    elif not job.scheduled_at and job.deal_id:
        from datetime import datetime, time

        deal = db.get(GroupDeal, job.deal_id)
        job.scheduled_at = datetime.combine(deal.service_date, time(9, 0))
    if slot:
        job.slot = slot
    if assignee_type == AssigneeType.PARTNER and job.payout_amount:
        job.payout_status = PayoutStatus.PENDING
    elif assignee_type == AssigneeType.STAFF:
        job.payout_status = PayoutStatus.NOT_APPLICABLE
    add_update(db, job, actor, f"Assigned to {assignee.name}")
    log_activity(db, actor, "service_job.assign", "service_job", job.id, old,
                 {"status": job.status, "assigned_to_type": assignee_type, "assigned_to_id": assignee_id},
                 society_id=job.society_id)
    when = f" on {job.scheduled_at:%d %b %I:%M %p}" if job.scheduled_at else ""
    notify(db, assignee_type, assignee_id, NotificationCategory.SERVICE_JOB, "New job assigned",
           f"{job.title}{when}. Job {job.job_no}.", ref_type="service_job", ref_id=job.id, sms=True)
    notify_resident(db, job)
    return job


def update_status_by_assignee(db: Session, job: ServiceJob, actor: CurrentUser, new_status: str,
                              note: str | None, photo: str | None) -> ServiceJob:
    allowed = ASSIGNEE_FLOW.get(job.status, set())
    if new_status not in allowed:
        raise bad_request(f"Cannot move job from {job.status} to {new_status}")
    if new_status == JobStatus.COMPLETED and not (photo or note):
        raise bad_request("Add a completion photo or note")
    old = job.status
    job.status = new_status
    if note:
        job.notes = note
    if photo:
        job.photo = photo
    if new_status == JobStatus.COMPLETED:
        job.completed_at = now_ist()
        _complete_booking(db, job)
    add_update(db, job, actor, note, photo)
    log_activity(db, actor, "service_job.status", "service_job", job.id, {"status": old}, {"status": new_status},
                 society_id=job.society_id)
    notify_resident(db, job)
    return job


def _complete_booking(db: Session, job: ServiceJob) -> None:
    if not job.booking_id:
        return
    booking = db.get(Booking, job.booking_id)
    if booking and booking.status == BookingStatus.CONFIRMED:
        booking.status = BookingStatus.COMPLETED
        booking.completed_at = now_ist()
        recalc_deal(db, db.get(GroupDeal, booking.deal_id))


def report_issue(db: Session, job: ServiceJob, actor: CurrentUser, note: str, reschedule_to) -> ServiceJob:
    if job.status in FINAL:
        raise bad_request(f"Job is already {job.status}")
    old = job.status
    job.status = JobStatus.RESCHEDULED
    job.issue_note = note
    job.escalated = True
    if reschedule_to:
        job.scheduled_at = reschedule_to
    add_update(db, job, actor, f"Issue: {note}")
    log_activity(db, actor, "service_job.issue", "service_job", job.id, {"status": old},
                 {"status": job.status, "issue": note, "reschedule_to": str(reschedule_to or "")},
                 society_id=job.society_id)
    if job.society_id:
      notify_society_admins(db, job.society_id, NotificationCategory.SERVICE_JOB, "Job issue reported",
                          f"{job.job_no}: {note}", perm="service_jobs", ref_type="service_job", ref_id=job.id)
    notify_resident(db, job)
    return job


def cancel(db: Session, job: ServiceJob, actor: CurrentUser, reason: str) -> ServiceJob:
    if job.status in FINAL:
        raise bad_request(f"Job is already {job.status}")
    old = job.status
    job.status = JobStatus.CANCELLED
    add_update(db, job, actor, f"Cancelled: {reason}")
    log_activity(db, actor, "service_job.cancel", "service_job", job.id, {"status": old},
                 {"status": job.status, "reason": reason}, society_id=job.society_id)
    notify_resident(db, job)
    if job.assigned_to_type and job.assigned_to_id:
        notify(db, job.assigned_to_type, job.assigned_to_id, NotificationCategory.SERVICE_JOB, "Job cancelled",
               f"{job.job_no} has been cancelled.", ref_type="service_job", ref_id=job.id)
    return job


def is_assignee(user: CurrentUser, job: ServiceJob) -> bool:
    return job.assigned_to_type == user.user_type and job.assigned_to_id == user.id


def assignee_name(db: Session, job: ServiceJob) -> str | None:
    if not job.assigned_to_id:
        return None
    model = Staff if job.assigned_to_type == AssigneeType.STAFF else ServicePartner
    obj = db.get(model, job.assigned_to_id)
    return obj.name if obj else None


def job_detail(db: Session, job: ServiceJob, viewer: CurrentUser) -> dict:
    from app.modules.service_jobs.schemas import JobAdminOut

    resident = db.get(Resident, job.resident_id) if job.resident_id else None
    unit = db.get(Unit, job.unit_id) if job.unit_id else None
    wing = db.get(Wing, unit.wing_id) if unit else None
    mobile = resident.mobile if resident else None
    if viewer.user_type in (UserType.STAFF, UserType.PARTNER) and job.status in (JobStatus.ASSIGNED,
                                                                                 JobStatus.RESCHEDULED):
        mobile = mask_mobile(mobile)  # contact revealed only after the job is accepted
    timeline = db.scalars(select(ServiceJobUpdate).where(ServiceJobUpdate.job_id == job.id)
                          .order_by(ServiceJobUpdate.id)).all()
    data = JobAdminOut.model_validate(job).model_dump()
    if not viewer.is_admin and viewer.user_type != UserType.PARTNER:
        data.update(payout_amount=None, payout_reference=None)
    return {**data, "timeline": timeline, "resident_name": resident.name if resident else None,
            "resident_mobile": mobile, "unit_label": f"{wing.name}-{unit.unit_no}" if unit and wing else None,
            "assignee_name": assignee_name(db, job)}


def get_job_or_404(db: Session, job_id: int) -> ServiceJob:
    job = db.get(ServiceJob, job_id)
    if not job:
        raise not_found("Service job")
    return job
