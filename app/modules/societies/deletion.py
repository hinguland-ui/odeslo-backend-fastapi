"""Permanent removal of a society and everything that belongs to it (super admin only).

What goes: wings and flats, residents (with family, vehicles, documents), society admins, staff, deals and bookings,
payments and refunds, maintenance bills, complaints, requests, notices, masjids added by the society, chats,
notifications, the activity log of the society and every uploaded file (photos, documents).

What stays: vendors (accounts, documents, ratings) and their history. A vendor's job and payout rows are kept, only
detached from the society and from the resident (no names, no flat, no chat, no notes), so earnings and payment
history stay correct. Jobs that were still open are cancelled. Platform-level data (platform deals, notices, masjids)
is not touched.
"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import AssigneeType, JobStatus
from app.modules.activity_logs.models import ActivityLog
from app.modules.activity_logs.service import log_activity
from app.modules.announcements.models import Announcement
from app.modules.bookings.models import Booking
from app.modules.complaints.models import Complaint, ComplaintComment
from app.modules.files.models import StoredFile
from app.modules.group_deals.models import DealApplication, GroupDeal
from app.modules.maintenance.models import MaintenanceBill
from app.modules.masjids.models import Masjid, MasjidTiming
from app.modules.worship_places.models import WorshipPlace
from app.modules.location_requests.models import LocationRequest
from app.modules.notifications.models import DeviceToken, Notification
from app.modules.payments.models import Payment
from app.modules.refunds.models import Refund
from app.modules.requests.models import ResidentRequest
from app.modules.residents.models import FamilyMember, Resident, ResidentDocument, Vehicle
from app.modules.service_jobs.models import JobLocation, JobMessage, ServiceJob, ServiceJobUpdate
from app.modules.service_partners.models import PartnerSociety
from app.modules.societies.models import Society, Unit, Wing
from app.modules.society_admins.models import SocietyAdmin
from app.modules.staff.models import Staff


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def preview(db: Session, society: Society) -> dict:
    """What deleting this society would remove and what would be kept, for the confirmation screen."""
    sid = society.id
    partner_jobs = (ServiceJob.society_id == sid, ServiceJob.assigned_to_type == AssigneeType.PARTNER)
    by_status = dict(db.execute(select(Resident.status, func.count()).where(Resident.society_id == sid)
                                .group_by(Resident.status)).all())
    pay = db.execute(select(func.count(), func.coalesce(func.sum(Payment.amount), 0)).where(Payment.society_id == sid)).one()
    files = db.execute(select(func.count(), func.coalesce(func.sum(StoredFile.size_bytes), 0))
                       .where(StoredFile.society_id == sid)).one()
    open_partner_jobs = _count(db, ServiceJob, *partner_jobs, ServiceJob.status.not_in((JobStatus.COMPLETED, JobStatus.CANCELLED)))
    pending_payouts = db.execute(select(func.count(), func.coalesce(func.sum(ServiceJob.payout_amount), 0))
                                 .where(*partner_jobs, ServiceJob.status == JobStatus.COMPLETED,
                                        ServiceJob.payout_status == "pending")).one()
    return {
        "society": {"id": sid, "name": society.name, "code": society.code, "status": society.status},
        "delete": {
            "wings": _count(db, Wing, Wing.society_id == sid),
            "flats": _count(db, Unit, Unit.society_id == sid),
            "residents": sum(by_status.values()),
            "residents_by_status": by_status,
            "society_admins": _count(db, SocietyAdmin, SocietyAdmin.society_id == sid),
            "staff": _count(db, Staff, Staff.society_id == sid),
            "deals": _count(db, GroupDeal, GroupDeal.society_id == sid),
            "bookings": _count(db, Booking, Booking.society_id == sid),
            "payments": pay[0],
            "payments_amount": float(pay[1]),
            "refunds": _count(db, Refund, Refund.society_id == sid),
            "maintenance_bills": _count(db, MaintenanceBill, MaintenanceBill.society_id == sid),
            "complaints": _count(db, Complaint, Complaint.society_id == sid),
            "requests": _count(db, ResidentRequest, ResidentRequest.society_id == sid),
            "announcements": _count(db, Announcement, Announcement.society_id == sid),
            "masjids": _count(db, Masjid, Masjid.society_id == sid) + _count(db, WorshipPlace, WorshipPlace.society_id == sid),
            "files": files[0],
            "files_bytes": int(files[1]),
        },
        "keep": {
            "vendors_assigned": _count(db, PartnerSociety, PartnerSociety.society_id == sid),
            "vendor_jobs": _count(db, ServiceJob, *partner_jobs),
            "vendor_jobs_open": open_partner_jobs,
            "vendor_payouts_pending": pending_payouts[0],
            "vendor_payouts_pending_amount": float(pending_payouts[1]),
        },
    }


def delete_society(db: Session, society: Society, actor: CurrentUser, delete_media: bool = True) -> dict:
    """Remove the society for good. The caller commits. Returns what was deleted (same shape as preview)."""
    summary = preview(db, society)
    sid, name, code = society.id, society.name, society.code

    resident_ids = list(db.scalars(select(Resident.id).where(Resident.society_id == sid)))
    staff_ids = list(db.scalars(select(Staff.id).where(Staff.society_id == sid)))
    admin_ids = list(db.scalars(select(SocietyAdmin.id).where(SocietyAdmin.society_id == sid)))
    deal_ids = select(GroupDeal.id).where(GroupDeal.society_id == sid)
    all_job_ids = select(ServiceJob.id).where(ServiceJob.society_id == sid)
    partner_job = (ServiceJob.society_id == sid, ServiceJob.assigned_to_type == AssigneeType.PARTNER)
    partner_job_ids = select(ServiceJob.id).where(*partner_job)
    other_job_ids = select(ServiceJob.id).where(ServiceJob.society_id == sid, ServiceJob.assigned_to_type.is_distinct_from(AssigneeType.PARTNER))

    # ---- files: the registry says which ones belong to this society
    files = list(db.scalars(select(StoredFile).where(StoredFile.society_id == sid)))
    paths = [f.path for f in files]

    # ---- service jobs: chats and live positions go; jobs of staff / nobody go; vendor jobs stay, detached
    db.execute(delete(JobMessage).where(JobMessage.job_id.in_(all_job_ids)))
    db.execute(delete(JobLocation).where(JobLocation.job_id.in_(all_job_ids)))
    db.execute(delete(ServiceJobUpdate).where(ServiceJobUpdate.job_id.in_(other_job_ids)))
    db.execute(delete(ServiceJob).where(ServiceJob.id.in_(other_job_ids)))
    # work that was still open cannot continue without a society
    db.execute(update(ServiceJob).where(*partner_job, ServiceJob.status.not_in((JobStatus.COMPLETED, JobStatus.CANCELLED)))
               .values(status=JobStatus.CANCELLED, payout_status="not_applicable"))
    # no resident details in the vendor's history: notes in the timeline, flat, resident, booking
    db.execute(update(ServiceJobUpdate).where(ServiceJobUpdate.job_id.in_(partner_job_ids)).values(note=None))
    detach = dict(society_name=name, society_id=None, booking_id=None, resident_id=None, unit_id=None, deal_id=None,
                  notes=None, feedback=None, issue_note=None)
    if delete_media:
        detach["photo"] = None
        db.execute(update(ServiceJobUpdate).where(ServiceJobUpdate.job_id.in_(partner_job_ids)).values(photo=None))
    db.execute(update(ServiceJob).where(*partner_job).values(**detach))

    # ---- money and bookings
    db.execute(delete(Refund).where(Refund.society_id == sid))
    db.execute(delete(Payment).where(Payment.society_id == sid))
    db.execute(delete(Booking).where(Booking.society_id == sid))
    db.execute(delete(DealApplication).where(DealApplication.deal_id.in_(deal_ids)))
    db.execute(delete(GroupDeal).where(GroupDeal.society_id == sid))
    db.execute(delete(MaintenanceBill).where(MaintenanceBill.society_id == sid))

    # ---- complaints, requests, notices, masjids
    db.execute(delete(ComplaintComment).where(ComplaintComment.complaint_id.in_(select(Complaint.id).where(Complaint.society_id == sid))))
    db.execute(delete(Complaint).where(Complaint.society_id == sid))
    db.execute(delete(ResidentRequest).where(ResidentRequest.society_id == sid))
    db.execute(delete(Announcement).where(Announcement.society_id == sid))
    db.execute(delete(MasjidTiming).where(MasjidTiming.masjid_id.in_(select(Masjid.id).where(Masjid.society_id == sid))))
    db.execute(delete(Masjid).where(Masjid.society_id == sid))
    db.execute(delete(WorshipPlace).where(WorshipPlace.society_id == sid))
    db.execute(delete(LocationRequest).where(LocationRequest.society_id == sid))

    # ---- people
    db.execute(delete(Staff).where(Staff.society_id == sid))
    if resident_ids:
        db.execute(delete(FamilyMember).where(FamilyMember.resident_id.in_(resident_ids)))
        db.execute(update(FamilyMember).where(FamilyMember.linked_resident_id.in_(resident_ids)).values(linked_resident_id=None))
        db.execute(delete(Vehicle).where(Vehicle.resident_id.in_(resident_ids)))
        db.execute(delete(ResidentDocument).where(ResidentDocument.resident_id.in_(resident_ids)))
        db.execute(update(Resident).where(Resident.society_id == sid).values(parent_resident_id=None))
    db.execute(delete(Resident).where(Resident.society_id == sid))
    db.execute(delete(SocietyAdmin).where(SocietyAdmin.society_id == sid))
    db.execute(delete(PartnerSociety).where(PartnerSociety.society_id == sid))

    # ---- their notifications and phone tokens
    for user_type, ids in (("resident", resident_ids), ("staff", staff_ids), ("society_admin", admin_ids)):
        if ids:
            db.execute(delete(Notification).where(Notification.user_type == user_type, Notification.user_id.in_(ids)))
            db.execute(delete(DeviceToken).where(DeviceToken.user_type == user_type, DeviceToken.user_id.in_(ids)))

    # ---- structure, logs, society
    db.execute(delete(Unit).where(Unit.society_id == sid))
    db.execute(delete(Wing).where(Wing.society_id == sid))
    db.execute(delete(ActivityLog).where(ActivityLog.society_id == sid))
    db.execute(delete(Society).where(Society.id == sid))

    # ---- uploaded files: gone from the registry and from disk after the commit (or kept for the super admin only)
    from app.core.files import release_upload

    if delete_media:
        for f in files:
            release_upload(db, f.path)
    else:
        for f in files:
            f.society_id, f.unit_id, f.job_id, f.audience, f.category = None, None, None, "platform_admin", "archived_society"
    log_activity(db, actor, "society.delete", "society", sid, old={"name": name, "code": code, "status": summary["society"]["status"]},
                 new={"deleted": summary["delete"], "kept": summary["keep"], "media_deleted": delete_media}, society_id=None)
    return {**summary, "files_removed": len(paths) if delete_media else 0}
