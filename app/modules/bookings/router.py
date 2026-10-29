from datetime import date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    AdminPermission,
    BookingPaymentStatus,
    BookingStatus,
    Feature,
    JobStatus,
    PaymentRefType,
    ResidentStatus,
    UserType,
)
from app.core.errors import bad_request, not_found
from app.core.pagination import Page, PageParams, paginate
from app.core.timeutils import now_ist
from app.core.utils import snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.bookings import service
from app.modules.bookings.models import Booking
from app.modules.bookings.schemas import (
    BookingCancel,
    BookingCreate,
    BookingDetailOut,
    BookingOnBehalfCreate,
    BookingOut,
    BookingRate,
    BookingSlotChange,
    InvoiceOut,
)
from app.modules.group_deals.models import GroupDeal
from app.modules.group_deals.service import is_booking_open
from app.modules.payments.models import Payment
from app.modules.refunds.models import Refund
from app.modules.residents.models import Resident
from app.modules.service_jobs.models import ServiceJob
from app.modules.societies.models import Society, Unit, Wing
from app.modules.societies.service import ensure_feature_enabled

router = APIRouter(prefix="/bookings", tags=["Bookings & Waitlist"])

ActiveResident = require_roles(UserType.RESIDENT)
DealAdmins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.DEALS)
BookingViewers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT)


def _with_deal(db: Session, bookings) -> list[dict]:
    deal_ids = {b.deal_id for b in bookings}
    deals = {d.id: d for d in db.scalars(select(GroupDeal).where(GroupDeal.id.in_(deal_ids))).all()} if deal_ids else {}
    out = []
    for b in bookings:
        d = deals.get(b.deal_id)
        out.append({**BookingOut.model_validate(b).model_dump(), "deal_title": d and d.title,
                    "service_date": d and d.service_date, "deal_category": d and d.category})
    return out


def _get_booking(db: Session, user: CurrentUser, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if not booking:
        raise not_found("Booking")
    if user.is_resident:
        if booking.unit_id != user.unit_id:  # household members share the unit's bookings
            raise not_found("Booking")
    else:
        ensure_society_access(user, booking.society_id, "Booking")
    return booking


# ---------- Resident ----------
@router.post("", response_model=BookingOut, status_code=201)
def book_deal(data: BookingCreate, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    ensure_feature_enabled(db, user.society_id, Feature.GROUP_DEALS)
    booking = service.create_booking(db, data.deal_id, user.entity, data.units_count, data.slot, user)
    log_activity(db, user, "booking.create", "booking", booking.id, new=snapshot(booking))
    db.commit()
    return _with_deal(db, [booking])[0]


@router.get("/my", response_model=Page[BookingOut])
def my_bookings(tab: Literal["active", "completed", "cancelled", "all"] = "active", params: PageParams = Depends(),
                user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    """Activity tab: Active (pending payment / confirmed / waitlisted), Completed, Cancelled."""
    stmt = select(Booking).where(Booking.unit_id == user.unit_id).order_by(Booking.id.desc())
    if tab == "active":
        stmt = stmt.where(Booking.status.in_(ACTIVE_BOOKING_STATUSES))
    elif tab == "completed":
        stmt = stmt.where(Booking.status == BookingStatus.COMPLETED)
    elif tab == "cancelled":
        stmt = stmt.where(Booking.status == BookingStatus.CANCELLED)
    page = paginate(db, stmt, params)
    page["items"] = _with_deal(db, page["items"])
    return page


@router.patch("/{booking_id}/slot", response_model=BookingOut)
def change_slot(booking_id: int, data: BookingSlotChange, user: CurrentUser = Depends(ActiveResident),
                db: Session = Depends(get_db)):
    """Change the time slot of an active booking (before the deal's cut-off)."""
    booking = _get_booking(db, user, booking_id)
    deal = db.get(GroupDeal, booking.deal_id)
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise bad_request(f"Booking is {booking.status}")
    if now_ist() > deal.cutoff_date:
        raise bad_request("Slots can only be changed before the cut-off date")
    if data.slot not in (deal.slots or []):
        raise bad_request(f"Choose a valid slot: {', '.join(deal.slots or [])}")
    job = db.scalar(select(ServiceJob).where(ServiceJob.booking_id == booking.id))
    if job and job.status not in (JobStatus.CREATED, JobStatus.CANCELLED):
        raise bad_request("A technician is already assigned. Please contact the society admin.")
    old = booking.slot
    booking.slot = data.slot
    if job:
        job.slot = data.slot
    log_activity(db, user, "booking.change_slot", "booking", booking.id, {"slot": old}, {"slot": data.slot})
    db.commit()
    return _with_deal(db, [booking])[0]


@router.post("/{booking_id}/rate", response_model=BookingOut)
def rate_booking(booking_id: int, data: BookingRate, user: CurrentUser = Depends(ActiveResident),
                 db: Session = Depends(get_db)):
    from app.modules.service_partners.service import update_rating

    booking = _get_booking(db, user, booking_id)
    job = db.scalar(select(ServiceJob).where(ServiceJob.booking_id == booking.id))
    if not job or job.status != JobStatus.COMPLETED:
        raise bad_request("You can rate only after the service is completed")
    if job.rating:
        raise bad_request("You have already rated this service")
    job.rating, job.feedback = data.rating, data.feedback
    if job.assigned_to_type == "partner" and job.assigned_to_id:
        update_rating(db, job.assigned_to_id, data.rating)
    log_activity(db, user, "booking.rate", "service_job", job.id, new={"rating": data.rating})
    db.commit()
    return _with_deal(db, [booking])[0]


# ---------- Admin ----------
@router.post("/on-behalf", response_model=BookingOut, status_code=201)
def book_on_behalf(data: BookingOnBehalfCreate, user: CurrentUser = Depends(DealAdmins),
                   db: Session = Depends(get_db)):
    resident = db.get(Resident, data.resident_id)
    if not resident or resident.status != ResidentStatus.ACTIVE:
        raise not_found("Active resident")
    ensure_society_access(user, resident.society_id, "Resident")
    booking = service.create_booking(db, data.deal_id, resident, data.units_count, data.slot, user)
    log_activity(db, user, "booking.create_on_behalf", "booking", booking.id, new=snapshot(booking),
                 society_id=resident.society_id)
    db.commit()
    return _with_deal(db, [booking])[0]


@router.get("", response_model=Page[BookingOut])
def list_bookings(
    society_id: int | None = None,
    deal_id: int | None = None,
    status: str | None = None,
    exclude_cancelled: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    params: PageParams = Depends(),
    user: CurrentUser = Depends(DealAdmins),
    db: Session = Depends(get_db),
):
    stmt = select(Booking).order_by(Booking.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Booking.society_id == sid)
    if deal_id:
        stmt = stmt.where(Booking.deal_id == deal_id)
    if status:
        stmt = stmt.where(Booking.status == status)
    if exclude_cancelled:
        stmt = stmt.where(Booking.status != BookingStatus.CANCELLED)
    if date_from:
        stmt = stmt.where(Booking.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(Booking.created_at <= datetime.combine(date_to, time.max))
    page = paginate(db, stmt, params)
    page["items"] = _with_deal(db, page["items"])
    return page


@router.get("/waitlist", response_model=Page[BookingOut])
def waitlist(society_id: int | None = None, deal_id: int | None = None, params: PageParams = Depends(),
             user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    stmt = select(Booking).where(Booking.status == BookingStatus.WAITLISTED).order_by(
        Booking.deal_id, Booking.waitlist_position)
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Booking.society_id == sid)
    if deal_id:
        stmt = stmt.where(Booking.deal_id == deal_id)
    page = paginate(db, stmt, params)
    page["items"] = _with_deal(db, page["items"])
    return page


@router.post("/{booking_id}/promote", response_model=BookingOut)
def promote(booking_id: int, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    booking = _get_booking(db, user, booking_id)
    service.promote_booking(db, booking, user)
    log_activity(db, user, "booking.promote_manual", "booking", booking.id, {"status": BookingStatus.WAITLISTED},
                 {"status": booking.status}, society_id=booking.society_id)
    db.commit()
    return _with_deal(db, [booking])[0]


# ---------- Shared ----------
@router.get("/{booking_id}", response_model=BookingDetailOut)
def booking_detail(booking_id: int, user: CurrentUser = Depends(BookingViewers), db: Session = Depends(get_db)):
    booking = _get_booking(db, user, booking_id)
    deal = db.get(GroupDeal, booking.deal_id)
    job = db.scalar(select(ServiceJob).where(ServiceJob.booking_id == booking.id))
    payments = db.scalars(select(Payment).where(Payment.ref_type == PaymentRefType.BOOKING,
                                                Payment.ref_id == booking.id).order_by(Payment.id)).all()
    refunds = db.scalars(select(Refund).where(Refund.booking_id == booking.id).order_by(Refund.id)).all()
    is_active = booking.status in ACTIVE_BOOKING_STATUSES
    return {
        **_with_deal(db, [booking])[0],
        "deal": snapshot(deal, exclude={"partner_payout_per_unit"}),
        "payments": [snapshot(p, exclude={"raw_response", "gateway_signature"}) for p in payments],
        "job": snapshot(job, exclude={"payout_amount", "payout_status", "payout_reference"}) if job else None,
        "refunds": [snapshot(r) for r in refunds],
        "can_cancel": is_active and now_ist() <= deal.cutoff_date,
        "can_pay": booking.payment_status == BookingPaymentStatus.UNPAID and booking.amount > 0
                   and booking.status in (BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED),
        "can_rate": bool(job and job.status == JobStatus.COMPLETED and not job.rating),
    }


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel(booking_id: int, data: BookingCancel, user: CurrentUser = Depends(BookingViewers),
           db: Session = Depends(get_db)):
    booking = _get_booking(db, user, booking_id)
    if user.is_society_admin and not user.has_perm(AdminPermission.DEALS):
        raise not_found("Booking")
    service.cancel_booking(db, booking, user, data.reason)
    db.commit()
    return _with_deal(db, [booking])[0]


@router.get("/{booking_id}/invoice", response_model=InvoiceOut)
def invoice(booking_id: int, user: CurrentUser = Depends(BookingViewers), db: Session = Depends(get_db)):
    booking = _get_booking(db, user, booking_id)
    deal = db.get(GroupDeal, booking.deal_id)
    resident = db.get(Resident, booking.resident_id)
    unit = db.get(Unit, booking.unit_id)
    wing = db.get(Wing, unit.wing_id)
    society = db.get(Society, booking.society_id)
    payments = db.scalars(select(Payment).where(Payment.ref_type == PaymentRefType.BOOKING,
                                                Payment.ref_id == booking.id)).all()
    return {
        "invoice_no": f"INV-{booking.booking_no}",
        "booking_no": booking.booking_no,
        "date": booking.created_at,
        "society": society.name,
        "resident_name": resident.name,
        "unit": f"{wing.name}-{unit.unit_no}",
        "service": deal.title,
        "service_date": deal.service_date,
        "units_count": booking.units_count,
        "price_per_unit": deal.price_per_unit,
        "amount": booking.amount,
        "payment_status": booking.payment_status,
        "payments": [{"txn_id": p.txn_id, "amount": p.amount, "mode": p.mode, "status": p.status,
                      "paid_at": p.paid_at} for p in payments],
    }
