"""Booking engine: capacity, waitlist (first-come-first-served), promotion, cancellation.

Rules enforced here:
- Confirmed units of a deal can never exceed max units (deal row is locked while booking).
- Cancelled bookings are never counted in confirmed / active counts.
- Waitlist is ordered by booking timestamp.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    CAPACITY_BOOKING_STATUSES,
    BookingPaymentStatus,
    BookingStatus,
    DealStatus,
    JobStatus,
    NotificationCategory,
    PaymentRefType,
    PaymentStatus,
    UserType,
)
from app.core.errors import bad_request, conflict, forbidden
from app.core.timeutils import now_ist
from app.core.utils import generate_ref
from app.modules.activity_logs.service import log_activity
from app.modules.bookings.models import Booking
from app.modules.group_deals.models import GroupDeal
from app.modules.group_deals.service import OPEN_DEAL_STATUSES, get_deal_or_404
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.residents.models import Resident


def recalc_deal(db: Session, deal: GroupDeal) -> None:
    """Recompute confirmed units, Full/Published status and waitlist positions from bookings."""
    db.flush()
    held = db.scalar(select(func.coalesce(func.sum(Booking.units_count), 0)).where(
        Booking.deal_id == deal.id, Booking.status.in_(CAPACITY_BOOKING_STATUSES))) or 0
    deal.confirmed_units = int(held)
    if deal.status in OPEN_DEAL_STATUSES:
        deal.status = DealStatus.FULL if held >= deal.max_units else DealStatus.PUBLISHED
    waitlisted = db.scalars(select(Booking).where(Booking.deal_id == deal.id, Booking.status == BookingStatus.WAITLISTED)
                            .order_by(Booking.created_at, Booking.id)).all()
    for pos, booking in enumerate(waitlisted, start=1):
        booking.waitlist_position = pos


def _needs_payment(deal: GroupDeal, booking: Booking) -> bool:
    return booking.amount > 0 and booking.payment_status != BookingPaymentStatus.PAID and not deal.pay_later_allowed


def _place_in_capacity(deal: GroupDeal, booking: Booking) -> None:
    """Booking fits within max units: confirm it, or hold the capacity until payment."""
    booking.waitlist_position = None
    if _needs_payment(deal, booking):
        booking.status = BookingStatus.PENDING_PAYMENT
        booking.hold_expires_at = now_ist() + timedelta(minutes=settings.BOOKING_PAYMENT_HOLD_MINUTES)
    else:
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = now_ist()
        booking.hold_expires_at = None


def _notify_status(db: Session, booking: Booking, deal: GroupDeal, promoted: bool = False) -> None:
    if booking.status == BookingStatus.CONFIRMED:
        title = "Waitlist promoted - booking confirmed" if promoted else "Booking confirmed"
        body = f"Your booking {booking.booking_no} for {deal.title} on {deal.service_date:%d %b} is confirmed."
    elif booking.status == BookingStatus.PENDING_PAYMENT:
        title = "Waitlist promoted - complete payment" if promoted else "Complete your payment"
        body = (f"A slot is reserved for {deal.title}. Pay ₹{booking.amount:g} within "
                f"{settings.BOOKING_PAYMENT_HOLD_MINUTES} minutes to confirm.")
    elif booking.status == BookingStatus.WAITLISTED:
        title = "Booking waitlisted"
        body = f"{deal.title} is full. You are #{booking.waitlist_position} on the waitlist."
    else:
        return
    notify(db, UserType.RESIDENT, booking.resident_id, NotificationCategory.BOOKING, title, body,
           ref_type="booking", ref_id=booking.id, sms=True)


def create_booking(db: Session, deal_id: int, resident: Resident, units_count: int, slot: str | None,
                   actor: CurrentUser) -> Booking:
    deal = get_deal_or_404(db, deal_id, lock=True)  # row lock: no overbooking under concurrency
    if deal.society_id not in (None, resident.society_id):
        raise bad_request("This deal is not available for your society")
    if deal.status not in OPEN_DEAL_STATUSES:
        raise bad_request("This deal is not open for booking")
    if now_ist() > deal.cutoff_date:
        raise bad_request("Booking cut-off date has passed")
    if units_count > deal.max_units:
        raise bad_request(f"Maximum {deal.max_units} units can be booked for this deal")
    if deal.slots and slot not in deal.slots:
        raise bad_request(f"Choose a valid slot: {', '.join(deal.slots)}")
    if db.scalar(select(Booking.id).where(Booking.deal_id == deal.id, Booking.unit_id == resident.unit_id,
                                          Booking.status.in_(ACTIVE_BOOKING_STATUSES))):
        raise conflict("Your unit already has an active booking for this deal")

    booking = Booking(
        booking_no=generate_ref("BK"), deal_id=deal.id, society_id=resident.society_id, resident_id=resident.id,
        unit_id=resident.unit_id, units_count=units_count, slot=slot, amount=round(deal.price_per_unit * units_count, 2),
        payment_status=BookingPaymentStatus.UNPAID, booked_by_type=actor.user_type, booked_by_id=actor.id,
        status=BookingStatus.WAITLISTED, created_at=now_ist(),
    )
    db.add(booking)
    db.flush()
    available = deal.max_units - deal.confirmed_units
    if units_count <= available:
        _place_in_capacity(deal, booking)
    recalc_deal(db, deal)
    _notify_status(db, booking, deal)
    return booking


def promote_waitlist(db: Session, deal: GroupDeal, actor: CurrentUser | None) -> list[Booking]:
    """Promote waitlisted bookings strictly in FCFS order while the head of the queue fits."""
    if deal.status not in OPEN_DEAL_STATUSES and deal.status != DealStatus.CLOSED:
        return []
    recalc_deal(db, deal)
    promoted = []
    for booking in db.scalars(select(Booking).where(Booking.deal_id == deal.id,
                                                    Booking.status == BookingStatus.WAITLISTED)
                              .order_by(Booking.created_at, Booking.id)).all():
        if booking.units_count > deal.max_units - deal.confirmed_units:
            break
        _place_in_capacity(deal, booking)
        recalc_deal(db, deal)
        promoted.append(booking)
        log_activity(db, actor, "booking.promote", "booking", booking.id, {"status": BookingStatus.WAITLISTED},
                     {"status": booking.status}, society_id=booking.society_id)
        _notify_status(db, booking, deal, promoted=True)
    return promoted


def promote_booking(db: Session, booking: Booking, actor: CurrentUser) -> Booking:
    """Manual promotion by admin of a specific waitlisted booking."""
    if booking.status != BookingStatus.WAITLISTED:
        raise bad_request("Only waitlisted bookings can be promoted")
    deal = get_deal_or_404(db, booking.deal_id, lock=True)
    recalc_deal(db, deal)
    if booking.units_count > deal.max_units - deal.confirmed_units:
        raise bad_request("Not enough free units. Max units can never be exceeded.")
    _place_in_capacity(deal, booking)
    recalc_deal(db, deal)
    _notify_status(db, booking, deal, promoted=True)
    return booking


def latest_success_payment(db: Session, booking_id: int):
    from app.modules.payments.models import Payment

    return db.scalar(select(Payment).where(Payment.ref_type == PaymentRefType.BOOKING, Payment.ref_id == booking_id,
                                           Payment.status == PaymentStatus.SUCCESS).order_by(Payment.id.desc()))


def cancel_booking(db: Session, booking: Booking, actor: CurrentUser | None, reason: str | None,
                   promote: bool = True, notify_admins: bool = True) -> Booking:
    from app.modules.refunds.service import create_refund
    from app.modules.service_jobs.models import ServiceJob

    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise bad_request(f"Booking is already {booking.status}")
    deal = get_deal_or_404(db, booking.deal_id, lock=True)
    if actor and actor.is_resident:
        if booking.resident_id != actor.id and booking.unit_id != actor.unit_id:
            raise forbidden()
        if now_ist() > deal.cutoff_date:
            raise bad_request("Bookings can only be cancelled before the cut-off date. Please contact the admin.")

    old_status = booking.status
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now_ist()
    booking.cancel_reason = reason
    booking.cancelled_by_type = actor.user_type if actor else "system"
    booking.cancelled_by_id = actor.id if actor else None
    booking.waitlist_position = None
    booking.hold_expires_at = None

    job = db.scalar(select(ServiceJob).where(ServiceJob.booking_id == booking.id))
    if job and job.status not in (JobStatus.COMPLETED, JobStatus.CANCELLED):
        job.status = JobStatus.CANCELLED

    payment = latest_success_payment(db, booking.id)
    if booking.payment_status == BookingPaymentStatus.PAID and payment:
        create_refund(db, payment, payment.amount, f"Booking cancelled: {reason or 'no reason given'}", actor,
                      booking_id=booking.id)
        booking.payment_status = BookingPaymentStatus.REFUND_PENDING

    recalc_deal(db, deal)
    notify(db, UserType.RESIDENT, booking.resident_id, NotificationCategory.BOOKING, "Booking cancelled",
           f"Booking {booking.booking_no} for {deal.title} was cancelled."
           + (" A refund has been initiated." if booking.payment_status == BookingPaymentStatus.REFUND_PENDING else ""),
           ref_type="booking", ref_id=booking.id)
    if notify_admins:
        notify_society_admins(db, booking.society_id, NotificationCategory.BOOKING, "Booking cancelled",
                              f"Booking {booking.booking_no} ({deal.title}) was cancelled.", perm="deals",
                              ref_type="booking", ref_id=booking.id)
    log_activity(db, actor, "booking.cancel", "booking", booking.id, {"status": old_status},
                 {"status": booking.status, "reason": reason}, society_id=booking.society_id)

    if promote and old_status in CAPACITY_BOOKING_STATUSES and deal.auto_promote_waitlist:
        promote_waitlist(db, deal, actor)
    return booking


def confirm_paid_booking(db: Session, booking: Booking) -> None:
    """Called by the payment service after a successful payment for a booking."""
    booking.payment_status = BookingPaymentStatus.PAID
    if booking.status == BookingStatus.PENDING_PAYMENT:
        deal = get_deal_or_404(db, booking.deal_id, lock=True)
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = now_ist()
        booking.hold_expires_at = None
        recalc_deal(db, deal)
        _notify_status(db, booking, deal)


def expire_payment_holds(db: Session) -> int:
    """Scheduler: release capacity of bookings whose payment window expired."""
    expired = db.scalars(select(Booking).where(Booking.status == BookingStatus.PENDING_PAYMENT,
                                               Booking.hold_expires_at.is_not(None),
                                               Booking.hold_expires_at < now_ist())).all()
    for booking in expired:
        cancel_booking(db, booking, None, "Payment not completed in time", notify_admins=False)
    return len(expired)
