from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    OPEN_COMPLAINT_STATUSES,
    UNPAID_BILL_STATUSES,
    AccountStatus,
    BillStatus,
    BookingStatus,
    DealStatus,
    JobStatus,
    MasjidStatus,
    PaymentStatus,
    RefundStatus,
    ResidentStatus,
    SocietyStatus,
)
from app.core.timeutils import today_ist
from app.modules.bookings.models import Booking
from app.modules.complaints.models import Complaint
from app.modules.group_deals.models import GroupDeal
from app.modules.maintenance.models import MaintenanceBill
from app.modules.masjids.models import Masjid, MasjidTiming
from app.modules.payments.models import Payment
from app.modules.refunds.models import Refund
from app.modules.requests.models import ResidentRequest
from app.modules.residents.models import Resident
from app.modules.service_jobs.models import ServiceJob
from app.modules.service_partners.models import ServicePartner
from app.modules.societies.models import Society, Unit


def _count(db: Session, model, *conds) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0


def _sum(db: Session, col, *conds) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(col), 0)).where(*conds)) or 0)


def super_admin_dashboard(db: Session) -> dict:
    return {
        "societies": {
            "total": _count(db, Society),
            "active": _count(db, Society, Society.status == SocietyStatus.ACTIVE),
            "pending": _count(db, Society, Society.status == SocietyStatus.PENDING),
            "suspended": _count(db, Society, Society.status == SocietyStatus.SUSPENDED),
        },
        "residents": _count(db, Resident, Resident.status == ResidentStatus.ACTIVE),
        "pending_residents": _count(db, Resident, Resident.status == ResidentStatus.PENDING_APPROVAL),
        "active_deals": _count(db, GroupDeal, GroupDeal.status.in_((DealStatus.PUBLISHED, DealStatus.FULL))),
        # cancelled bookings are never counted
        "active_bookings": _count(db, Booking, Booking.status.in_(ACTIVE_BOOKING_STATUSES)),
        "confirmed_bookings": _count(db, Booking, Booking.status == BookingStatus.CONFIRMED),
        "revenue": _sum(db, Payment.amount, Payment.status == PaymentStatus.SUCCESS),
        "revenue_by_type": {
            "booking": _sum(db, Payment.amount, Payment.status == PaymentStatus.SUCCESS, Payment.ref_type == "booking"),
            "maintenance": _sum(db, Payment.amount, Payment.status == PaymentStatus.SUCCESS,
                                Payment.ref_type == "maintenance"),
        },
        "refunded": _sum(db, Refund.amount, Refund.status == RefundStatus.COMPLETED),
        "pending_refunds": _count(db, Refund, Refund.status.in_((RefundStatus.REQUESTED, RefundStatus.APPROVED,
                                                                 RefundStatus.PROCESSED))),
        "pending_masjid_verifications": _count(db, Masjid, Masjid.status == MasjidStatus.PENDING_VERIFICATION)
        + _count(db, MasjidTiming, MasjidTiming.status == MasjidStatus.PENDING_VERIFICATION),
        "escalated_jobs": _count(db, ServiceJob, ServiceJob.escalated.is_(True),
                                 ServiceJob.status.not_in((JobStatus.COMPLETED, JobStatus.CANCELLED))),
        "unverified_partners": _count(db, ServicePartner, ServicePartner.verified.is_(False),
                                      ServicePartner.status == AccountStatus.ACTIVE),
    }


def society_admin_dashboard(db: Session, society_id: int) -> dict:
    today = today_ist()
    day_start, day_end = datetime.combine(today, time.min), datetime.combine(today, time.max)
    bills = MaintenanceBill.society_id == society_id
    return {
        # Clicking "residents" must open the APPROVED residents list (GET /residents?status=active)
        "residents": {"count": _count(db, Resident, Resident.society_id == society_id,
                                      Resident.status == ResidentStatus.ACTIVE),
                      "link": "/residents?status=active"},
        "pending_approvals": {"count": _count(db, Resident, Resident.society_id == society_id,
                                              Resident.status == ResidentStatus.PENDING_APPROVAL),
                              "link": "/residents/pending"},
        "units": {
            "total": _count(db, Unit, Unit.society_id == society_id),
            "vacant": _count(db, Unit, Unit.society_id == society_id, Unit.occupancy_status == "vacant"),
        },
        "active_bookings": {
            "count": _count(db, Booking, Booking.society_id == society_id,
                            Booking.status.in_(ACTIVE_BOOKING_STATUSES)),  # excludes cancelled
            "confirmed": _count(db, Booking, Booking.society_id == society_id,
                                Booking.status == BookingStatus.CONFIRMED),
            "waitlisted": _count(db, Booking, Booking.society_id == society_id,
                                 Booking.status == BookingStatus.WAITLISTED),
            "pending_payment": _count(db, Booking, Booking.society_id == society_id,
                                      Booking.status == BookingStatus.PENDING_PAYMENT),
        },
        "maintenance": {
            "collected": _sum(db, MaintenanceBill.paid_amount, bills),
            "pending": round(_sum(db, MaintenanceBill.amount, bills, MaintenanceBill.status.in_(UNPAID_BILL_STATUSES))
                             + _sum(db, MaintenanceBill.late_fee, bills,
                                    MaintenanceBill.status.in_(UNPAID_BILL_STATUSES))
                             - _sum(db, MaintenanceBill.paid_amount, bills,
                                    MaintenanceBill.status.in_(UNPAID_BILL_STATUSES)), 2),
            "overdue_bills": _count(db, MaintenanceBill, bills, MaintenanceBill.status == BillStatus.OVERDUE),
        },
        "open_complaints": _count(db, Complaint, Complaint.society_id == society_id,
                                  Complaint.status.in_(OPEN_COMPLAINT_STATUSES)),
        "open_requests": _count(db, ResidentRequest, ResidentRequest.society_id == society_id,
                                ResidentRequest.status.in_(("open", "in_progress"))),
        "todays_service_jobs": _count(db, ServiceJob, ServiceJob.society_id == society_id,
                                      ServiceJob.scheduled_at.between(day_start, day_end)),
        "pending_refunds": _count(db, Refund, Refund.society_id == society_id,
                                  Refund.status.in_((RefundStatus.REQUESTED, RefundStatus.APPROVED))),
        "active_deals": _count(db, GroupDeal, GroupDeal.society_id == society_id,
                               GroupDeal.status.in_((DealStatus.PUBLISHED, DealStatus.FULL))),
    }
