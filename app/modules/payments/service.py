"""Payments for bookings and maintenance bills. Every money movement gets a transaction ID + activity log."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import (
    BillStatus,
    BookingPaymentStatus,
    BookingStatus,
    NotificationCategory,
    PaymentMode,
    PaymentRefType,
    PaymentStatus,
    UNPAID_BILL_STATUSES,
    UserType,
)
from app.core.errors import bad_request, forbidden, not_found
from app.core.timeutils import now_ist
from app.core.utils import generate_ref
from app.integrations.payment_gateway import get_gateway
from app.modules.activity_logs.service import log_activity
from app.modules.bookings.models import Booking
from app.modules.maintenance.models import MaintenanceBill
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.payments.models import Payment


def resolve_reference(db: Session, ref_type: str, ref_id: int):
    model = Booking if ref_type == PaymentRefType.BOOKING else MaintenanceBill
    obj = db.get(model, ref_id)
    if not obj:
        raise not_found("Booking" if ref_type == PaymentRefType.BOOKING else "Maintenance bill")
    return obj


def payable_amount(db: Session, ref_type: str, obj) -> float:
    if ref_type == PaymentRefType.BOOKING:
        if obj.payment_status != BookingPaymentStatus.UNPAID:
            raise bad_request("This booking is already paid")
        if obj.status not in (BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED):
            raise bad_request("Payment is not allowed for a booking in status " + obj.status)
        return obj.amount
    if obj.status not in UNPAID_BILL_STATUSES:
        raise bad_request("This bill is already paid")
    return obj.outstanding


def initiate_online_payment(db: Session, user: CurrentUser, ref_type: str, ref_id: int) -> tuple[Payment, object]:
    from app.modules.residents.service import can_view_maintenance

    obj = resolve_reference(db, ref_type, ref_id)
    if obj.unit_id != user.unit_id:
        raise not_found("Payment reference")
    if ref_type == PaymentRefType.MAINTENANCE and not can_view_maintenance(user.entity):
        raise forbidden("You are not allowed to pay maintenance for this unit")
    amount = payable_amount(db, ref_type, obj)
    if amount <= 0:
        raise bad_request("Nothing to pay")

    gateway = get_gateway(db)
    payment = Payment(txn_id=generate_ref("TXN"), society_id=obj.society_id, resident_id=user.id,
                      unit_id=obj.unit_id, ref_type=ref_type, ref_id=ref_id, amount=amount,
                      mode=PaymentMode.ONLINE, gateway=gateway.name, status=PaymentStatus.INITIATED,
                      recorded_by_type=user.user_type, recorded_by_id=user.id)
    db.add(payment)
    db.flush()
    order = gateway.create_order(amount, payment.txn_id, {"ref_type": ref_type, "ref_id": str(ref_id)})
    payment.gateway_order_id = order.order_id
    log_activity(db, user, "payment.initiate", "payment", payment.id,
                 new={"txn_id": payment.txn_id, "amount": amount, "ref_type": ref_type, "ref_id": ref_id})
    return payment, order


def mark_success(db: Session, payment: Payment, actor: CurrentUser | None, gateway_txn_id: str | None = None,
                 raw: dict | None = None) -> Payment:
    if payment.status == PaymentStatus.SUCCESS:  # idempotent (callback + webhook may both arrive)
        return payment
    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = now_ist()
    payment.gateway_txn_id = gateway_txn_id or payment.gateway_txn_id
    if raw:
        payment.raw_response = raw
    _apply_to_reference(db, payment, actor)
    log_activity(db, actor, "payment.success", "payment", payment.id,
                 new={"txn_id": payment.txn_id, "amount": payment.amount, "gateway_txn_id": payment.gateway_txn_id},
                 society_id=payment.society_id)
    if payment.resident_id:
        notify(db, UserType.RESIDENT, payment.resident_id, NotificationCategory.PAYMENT, "Payment received",
               f"We received ₹{payment.amount:g} (Txn {payment.txn_id}). Thank you!", ref_type="payment",
               ref_id=payment.id)
    notify_society_admins(db, payment.society_id, NotificationCategory.PAYMENT, "Payment received",
                          f"₹{payment.amount:g} received for {payment.ref_type} #{payment.ref_id} ({payment.mode}).",
                          perm="payments", ref_type="payment", ref_id=payment.id)
    return payment


def gateway_amount_matches(payment: Payment, entity: dict) -> bool:
    """What the gateway reports as paid (paise) must equal the amount we asked for. No amount reported = nothing to compare."""
    paid = entity.get("amount")
    if paid is None:
        return True
    try:
        return int(paid) == round(payment.amount * 100)
    except (TypeError, ValueError):
        return False


def flag_amount_mismatch(db: Session, payment: Payment, entity: dict) -> None:
    log_activity(db, None, "payment.amount_mismatch", "payment", payment.id,
                 new={"expected_paise": round(payment.amount * 100), "gateway_paise": entity.get("amount"),
                      "gateway_payment_id": entity.get("id")}, society_id=payment.society_id)
    notify_society_admins(db, payment.society_id, NotificationCategory.PAYMENT, "Payment amount mismatch",
                          f"The gateway reported a different amount than expected for {payment.txn_id}. It was NOT confirmed.",
                          perm="payments", ref_type="payment", ref_id=payment.id)


def mark_failed(db: Session, payment: Payment, reason: str, raw: dict | None = None) -> Payment:
    if payment.status == PaymentStatus.SUCCESS:
        return payment
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason
    if raw:
        payment.raw_response = raw
    log_activity(db, None, "payment.failed", "payment", payment.id, new={"reason": reason},
                 society_id=payment.society_id)
    return payment


def _apply_to_reference(db: Session, payment: Payment, actor: CurrentUser | None) -> None:
    obj = resolve_reference(db, payment.ref_type, payment.ref_id)
    if payment.ref_type == PaymentRefType.BOOKING:
        from app.modules.bookings.service import confirm_paid_booking
        from app.modules.refunds.service import create_refund

        if obj.payment_status == BookingPaymentStatus.PAID:
            # a second payment for a booking that is already paid (two checkouts opened): give it straight back
            create_refund(db, payment, payment.amount, "Duplicate payment for an already paid booking", actor,
                          booking_id=obj.id)
        elif obj.status == BookingStatus.CANCELLED:
            # money arrived after the booking expired / was cancelled -> refund automatically
            obj.payment_status = BookingPaymentStatus.REFUND_PENDING
            create_refund(db, payment, payment.amount, "Payment received for a cancelled booking", actor,
                          booking_id=obj.id)
        else:
            confirm_paid_booking(db, obj)
    elif obj.status not in UNPAID_BILL_STATUSES or payment.amount > obj.outstanding + 0.001:
        from app.modules.refunds.service import create_refund

        # the bill is already paid, or this would pay more than is due: never credit it twice, refund it
        create_refund(db, payment, payment.amount, "Duplicate or excess payment for a maintenance bill", actor)
    else:
        obj.paid_amount = round((obj.paid_amount or 0) + payment.amount, 2)
        if obj.outstanding <= 0:
            obj.status = BillStatus.PAID
            obj.paid_at = now_ist()
        else:
            obj.status = BillStatus.PARTIALLY_PAID


def record_offline_payment(db: Session, actor: CurrentUser, ref_type: str, ref_id: int, amount: float, mode: str,
                           reference_no: str | None, notes: str | None) -> Payment:
    if mode == PaymentMode.ONLINE:
        raise bad_request("Use cash, cheque, upi or bank_transfer for offline payments")
    obj = resolve_reference(db, ref_type, ref_id)
    if not actor.is_super_admin and obj.society_id != actor.society_id:
        raise not_found("Payment reference")
    due = payable_amount(db, ref_type, obj)
    if amount <= 0 or amount > due + 0.001:
        raise bad_request(f"Amount must be between 0 and the outstanding amount ₹{due:g}")
    if ref_type == PaymentRefType.BOOKING and abs(amount - due) > 0.001:
        raise bad_request("Booking payments must be for the full amount")
    resident_id = getattr(obj, "resident_id", None)
    payment = Payment(txn_id=generate_ref("TXN"), society_id=obj.society_id, resident_id=resident_id,
                      unit_id=obj.unit_id, ref_type=ref_type, ref_id=ref_id, amount=amount, mode=mode,
                      reference_no=reference_no, notes=notes, status=PaymentStatus.PENDING,
                      recorded_by_type=actor.user_type, recorded_by_id=actor.id)
    db.add(payment)
    db.flush()
    mark_success(db, payment, actor)
    return payment


def find_by_order(db: Session, order_id: str) -> Payment | None:
    return db.scalar(select(Payment).where(Payment.gateway_order_id == order_id))
