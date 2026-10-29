import csv
import io
import json
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AdminPermission, PaymentRefType, PaymentStatus, UserType
from app.core.errors import bad_request, forbidden, not_found
from app.core.pagination import Page, PageParams, paginate
from app.integrations.payment_gateway import get_gateway
from app.modules.bookings.models import Booking
from app.modules.group_deals.models import GroupDeal
from app.modules.maintenance.models import MaintenanceBill
from app.modules.payments import service
from app.modules.payments.details import build_details
from app.modules.payments.invoice import build_invoice_pdf
from app.modules.payments.models import Payment
from app.modules.payments.schemas import (
    OfflinePaymentCreate,
    PaymentFailure,
    PaymentInitiate,
    PaymentOrderOut,
    PaymentOut,
    PaymentVerify,
    ReceiptOut,
)
from app.modules.residents.models import Resident
from app.modules.societies.models import Society, Unit, Wing

router = APIRouter(prefix="/payments", tags=["Payments"])

ActiveResident = require_roles(UserType.RESIDENT)
PaymentAdmins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.PAYMENTS)


def _get_payment(db: Session, user: CurrentUser, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise not_found("Payment")
    if user.is_resident:
        if payment.unit_id != user.unit_id:
            raise not_found("Payment")
    else:
        ensure_society_access(user, payment.society_id, "Payment")
    return payment


# ---------- Resident: online payment ----------
@router.post("/initiate", response_model=PaymentOrderOut)
def initiate(data: PaymentInitiate, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    payment, order = service.initiate_online_payment(db, user, data.ref_type, data.ref_id)
    db.commit()
    return {"payment_id": payment.id, "txn_id": payment.txn_id, "gateway": order.gateway,
            "gateway_order_id": order.order_id, "amount": payment.amount, "amount_paise": order.amount_paise,
            "currency": order.currency, "key_id": order.key_id}


@router.post("/verify", response_model=PaymentOut)
def verify(data: PaymentVerify, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    """Gateway checkout success callback from the app (signature is verified server-side)."""
    payment = _get_payment(db, user, data.payment_id)
    db.refresh(payment, with_for_update=True)  # two taps / two devices at once must not confirm it twice
    if payment.gateway_order_id != data.gateway_order_id:
        raise bad_request("Order mismatch")
    if not get_gateway(db, payment.gateway).verify_payment(data.gateway_order_id, data.gateway_payment_id, data.signature):
        service.mark_failed(db, payment, "Signature verification failed")
        db.commit()
        raise bad_request("Payment verification failed")
    payment.gateway_signature = data.signature
    service.mark_success(db, payment, user, gateway_txn_id=data.gateway_payment_id)
    db.commit()
    return payment


@router.post("/fail", response_model=PaymentOut)
def report_failure(data: PaymentFailure, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    payment = _get_payment(db, user, data.payment_id)
    service.mark_failed(db, payment, data.reason)
    db.commit()
    return payment


@router.post("/webhook/razorpay", include_in_schema=True)
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(""), db: Session = Depends(get_db)):
    """Server-to-server confirmation. Signature verified with the webhook secret."""
    body = await request.body()
    if not get_gateway(db, "razorpay").verify_webhook(body, x_razorpay_signature):
        raise forbidden("Invalid webhook signature")
    event = json.loads(body)
    kind = event.get("event", "")
    entity = event.get("payload", {}).get("payment", {}).get("entity", {})
    if kind in ("payment.captured", "order.paid", "payment.failed") and entity.get("order_id"):
        payment = service.find_by_order(db, entity["order_id"])
        if payment:
            if kind == "payment.failed":
                service.mark_failed(db, payment, entity.get("error_description") or "Payment failed", raw=entity)
            elif not service.gateway_amount_matches(payment, entity):
                # the gateway says a different amount was paid than the one we asked for: do not confirm, flag it
                service.flag_amount_mismatch(db, payment, entity)
            else:
                service.mark_success(db, payment, None, gateway_txn_id=entity.get("id"), raw=entity)
    elif kind.startswith("refund."):
        from app.modules.refunds.service import handle_gateway_refund_event

        handle_gateway_refund_event(db, kind, event.get("payload", {}).get("refund", {}).get("entity", {}))
    db.commit()
    return {"status": "ok"}


@router.get("/my", response_model=Page[PaymentOut])
def my_payments(ref_type: str | None = None, params: PageParams = Depends(),
                user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    stmt = select(Payment).where(Payment.unit_id == user.unit_id).order_by(Payment.id.desc())
    if ref_type:
        stmt = stmt.where(Payment.ref_type == ref_type)
    return paginate(db, stmt, params)


# ---------- Admin ----------
@router.post("/offline", response_model=PaymentOut, status_code=201)
def record_offline(
    data: OfflinePaymentCreate,
    user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN, perm=AdminPermission.PAYMENTS)),
    db: Session = Depends(get_db),
):
    """Mark an offline payment (cash / cheque / UPI / bank transfer) with its reference."""
    payment = service.record_offline_payment(db, user, data.ref_type, data.ref_id, data.amount, data.mode,
                                             data.reference_no, data.notes)
    db.commit()
    return payment


def _admin_stmt(user, society_id, status, ref_type, mode, date_from, date_to):
    stmt = select(Payment).order_by(Payment.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Payment.society_id == sid)
    if status:
        stmt = stmt.where(Payment.status == status)
    if ref_type:
        stmt = stmt.where(Payment.ref_type == ref_type)
    if mode:
        stmt = stmt.where(Payment.mode == mode)
    if date_from:
        stmt = stmt.where(Payment.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(Payment.created_at <= datetime.combine(date_to, time.max))
    return stmt


@router.get("", response_model=Page[PaymentOut])
def list_payments(society_id: int | None = None, status: str | None = None, ref_type: str | None = None,
                  mode: str | None = None, date_from: date | None = None, date_to: date | None = None,
                  params: PageParams = Depends(), user: CurrentUser = Depends(PaymentAdmins),
                  db: Session = Depends(get_db)):
    return paginate(db, _admin_stmt(user, society_id, status, ref_type, mode, date_from, date_to), params)


@router.get("/export")
def export_payments(society_id: int | None = None, status: str | None = None, ref_type: str | None = None,
                    mode: str | None = None, date_from: date | None = None, date_to: date | None = None,
                    user: CurrentUser = Depends(PaymentAdmins), db: Session = Depends(get_db)):
    rows = db.scalars(_admin_stmt(user, society_id, status, ref_type, mode, date_from, date_to)).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Txn ID", "Society ID", "Type", "Ref ID", "Amount", "Mode", "Gateway Txn", "Reference",
                     "Status", "Paid At", "Created At"])
    for p in rows:
        writer.writerow([p.txn_id, p.society_id, p.ref_type, p.ref_id, p.amount, p.mode, p.gateway_txn_id or "",
                         p.reference_no or "", p.status, p.paid_at or "", p.created_at])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=payments.csv"})


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int,
                user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN,
                                                          UserType.RESIDENT, perm=AdminPermission.PAYMENTS)),
                db: Session = Depends(get_db)):
    return _get_payment(db, user, payment_id)


_AnyPaymentViewer = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT, perm=AdminPermission.PAYMENTS)


@router.get("/{payment_id}/details")
def payment_details(payment_id: int, user: CurrentUser = Depends(_AnyPaymentViewer), db: Session = Depends(get_db)):
    """Who paid, to whom, through which society, for what, how, and what happened to it (admins and the payer)."""
    return build_details(db, _get_payment(db, user, payment_id))


@router.get("/{payment_id}/invoice")
def payment_invoice(payment_id: int, user: CurrentUser = Depends(_AnyPaymentViewer), db: Session = Depends(get_db)):
    """The invoice / receipt of a successful payment as a PDF."""
    payment = _get_payment(db, user, payment_id)
    if payment.status != PaymentStatus.SUCCESS:
        raise bad_request("An invoice is available only for successful payments")
    details = build_details(db, payment)
    return Response(content=build_invoice_pdf(details), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="Invoice-{payment.txn_id}.pdf"',
        "Cache-Control": "private, no-store",
    })


@router.get("/{payment_id}/receipt", response_model=ReceiptOut)
def receipt(payment_id: int,
            user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT,
                                                      perm=AdminPermission.PAYMENTS)),
            db: Session = Depends(get_db)):
    payment = _get_payment(db, user, payment_id)
    if payment.status != PaymentStatus.SUCCESS:
        raise bad_request("Receipt is available only for successful payments")
    society = db.get(Society, payment.society_id)
    resident = db.get(Resident, payment.resident_id) if payment.resident_id else None
    unit = db.get(Unit, payment.unit_id) if payment.unit_id else None
    wing = db.get(Wing, unit.wing_id) if unit else None
    if payment.ref_type == PaymentRefType.BOOKING:
        booking = db.get(Booking, payment.ref_id)
        deal = db.get(GroupDeal, booking.deal_id)
        description = f"Group deal: {deal.title} ({booking.booking_no})"
    else:
        bill = db.get(MaintenanceBill, payment.ref_id)
        description = f"Maintenance {bill.period} ({bill.bill_no})"
    return {
        "receipt_no": f"RCPT-{payment.txn_id}", "txn_id": payment.txn_id, "date": payment.paid_at,
        "society": society.name, "resident_name": resident.name if resident else None,
        "unit": f"{wing.name}-{unit.unit_no}" if unit and wing else None, "description": description,
        "amount": payment.amount, "mode": payment.mode, "reference_no": payment.reference_no,
        "gateway_txn_id": payment.gateway_txn_id,
    }
