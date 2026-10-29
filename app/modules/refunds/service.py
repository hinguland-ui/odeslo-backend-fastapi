"""Refund lifecycle: Requested -> Approved -> Processed -> Completed (or Rejected). Resident notified at every step."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import (
    BookingPaymentStatus,
    NotificationCategory,
    PaymentMode,
    PaymentStatus,
    RefundMode,
    RefundStatus,
    UserType,
)
from app.core.errors import bad_request
from app.core.timeutils import now_ist
from app.core.utils import generate_ref
from app.integrations.payment_gateway import get_gateway
from app.modules.activity_logs.service import log_activity
from app.modules.bookings.models import Booking
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.payments.models import Payment
from app.modules.refunds.models import Refund

_STATUS_TEXT = {
    RefundStatus.REQUESTED: "has been requested",
    RefundStatus.APPROVED: "has been approved",
    RefundStatus.PROCESSED: "has been processed and is on its way",
    RefundStatus.COMPLETED: "is completed",
    RefundStatus.REJECTED: "was rejected",
}


def refundable_amount(db: Session, payment: Payment) -> float:
    already = db.scalar(select(func.coalesce(func.sum(Refund.amount), 0)).where(
        Refund.payment_id == payment.id, Refund.status != RefundStatus.REJECTED)) or 0
    return round(payment.amount - float(already), 2)


def _notify(db: Session, refund: Refund, extra: str = "") -> None:
    if refund.resident_id:
        notify(db, UserType.RESIDENT, refund.resident_id, NotificationCategory.REFUND,
               f"Refund {refund.status}", f"Your refund {refund.refund_no} of ₹{refund.amount:g} "
               f"{_STATUS_TEXT[refund.status]}.{extra}", ref_type="refund", ref_id=refund.id)


def create_refund(db: Session, payment: Payment, amount: float, reason: str, actor: CurrentUser | None,
                  booking_id: int | None = None) -> Refund:
    if payment.status != PaymentStatus.SUCCESS:
        raise bad_request("Only successful payments can be refunded")
    available = refundable_amount(db, payment)
    if amount <= 0 or amount > available + 0.001:
        raise bad_request(f"Refund amount must be between 0 and ₹{available:g}")
    refund = Refund(refund_no=generate_ref("RF"), payment_id=payment.id, society_id=payment.society_id,
                    booking_id=booking_id or (payment.ref_id if payment.ref_type == "booking" else None),
                    resident_id=payment.resident_id, amount=amount, reason=reason, status=RefundStatus.REQUESTED,
                    requested_by_type=actor.user_type if actor else "system",
                    requested_by_id=actor.id if actor else None)
    db.add(refund)
    db.flush()
    log_activity(db, actor, "refund.request", "refund", refund.id,
                 new={"refund_no": refund.refund_no, "amount": amount, "payment_txn": payment.txn_id},
                 society_id=refund.society_id)
    _notify(db, refund)
    notify_society_admins(db, refund.society_id, NotificationCategory.REFUND, "Refund request",
                          f"Refund {refund.refund_no} of ₹{amount:g} is awaiting approval.", perm="refunds",
                          ref_type="refund", ref_id=refund.id)
    return refund


def _transition(db: Session, refund: Refund, allowed_from: tuple, to: str, actor: CurrentUser | None) -> str:
    if refund.status not in allowed_from:
        raise bad_request(f"Refund is {refund.status}; cannot move to {to}")
    old = refund.status
    refund.status = to
    return old


def approve(db: Session, refund: Refund, actor: CurrentUser) -> Refund:
    old = _transition(db, refund, (RefundStatus.REQUESTED,), RefundStatus.APPROVED, actor)
    refund.approved_by, refund.approved_by_type, refund.approved_at = actor.id, actor.user_type, now_ist()
    log_activity(db, actor, "refund.approve", "refund", refund.id, {"status": old}, {"status": refund.status},
                 society_id=refund.society_id)
    _notify(db, refund)
    return refund


def reject(db: Session, refund: Refund, actor: CurrentUser, reason: str) -> Refund:
    old = _transition(db, refund, (RefundStatus.REQUESTED, RefundStatus.APPROVED), RefundStatus.REJECTED, actor)
    refund.rejection_reason = reason
    if refund.booking_id:
        booking = db.get(Booking, refund.booking_id)
        if booking and booking.payment_status == BookingPaymentStatus.REFUND_PENDING:
            booking.payment_status = BookingPaymentStatus.PAID
    log_activity(db, actor, "refund.reject", "refund", refund.id, {"status": old},
                 {"status": refund.status, "reason": reason}, society_id=refund.society_id)
    _notify(db, refund, f" Reason: {reason}")
    return refund


def process(db: Session, refund: Refund, actor: CurrentUser, mode: str, reference_no: str | None) -> Refund:
    payment = db.get(Payment, refund.payment_id)
    if refund.status != RefundStatus.APPROVED:
        raise bad_request("Refund must be approved before processing")
    if mode == RefundMode.GATEWAY:
        if payment.mode != PaymentMode.ONLINE or not payment.gateway_txn_id:
            raise bad_request("Gateway refund is possible only for online payments. Use manual mode.")
        result = get_gateway(db, payment.gateway).refund(payment.gateway_txn_id, refund.amount)
        refund.gateway_refund_id = result.refund_id
    elif not reference_no:
        raise bad_request("reference_no is required for manual refunds (UPI / bank / cheque reference)")
    old = refund.status
    refund.status = RefundStatus.PROCESSED
    refund.mode = mode
    refund.reference_no = reference_no
    refund.processed_at = now_ist()
    log_activity(db, actor, "refund.process", "refund", refund.id, {"status": old},
                 {"status": refund.status, "mode": mode, "gateway_refund_id": refund.gateway_refund_id,
                  "reference_no": reference_no}, society_id=refund.society_id)
    _notify(db, refund)
    return refund


def complete(db: Session, refund: Refund, actor: CurrentUser | None) -> Refund:
    old = _transition(db, refund, (RefundStatus.PROCESSED,), RefundStatus.COMPLETED, actor)
    refund.completed_at = now_ist()
    if refund.booking_id:
        booking = db.get(Booking, refund.booking_id)
        if booking:
            booking.payment_status = BookingPaymentStatus.REFUNDED
    log_activity(db, actor, "refund.complete", "refund", refund.id, {"status": old}, {"status": refund.status},
                 society_id=refund.society_id)
    _notify(db, refund)
    return refund


def handle_gateway_refund_event(db: Session, event: str, entity: dict) -> None:
    refund = db.scalar(select(Refund).where(Refund.gateway_refund_id == entity.get("id")))
    if refund and event == "refund.processed" and refund.status == RefundStatus.PROCESSED:
        complete(db, refund, None)
