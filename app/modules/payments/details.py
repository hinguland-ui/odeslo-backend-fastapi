"""Everything about one payment in one place: who paid, who it went to, what for, how, and what happened to it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import PaymentRefType, UserType
from app.modules.bookings.models import Booking
from app.modules.group_deals.models import GroupDeal
from app.modules.maintenance.models import MaintenanceBill
from app.modules.payments.models import Payment
from app.modules.refunds.models import Refund
from app.modules.residents.models import Resident
from app.modules.service_partners.models import ServicePartner
from app.modules.societies.models import Society, Unit, Wing
from app.modules.society_admins.models import SocietyAdmin
from app.modules.super_admins.models import SuperAdmin

MODE_LABEL = {"online": "Online (gateway)", "cash": "Cash", "cheque": "Cheque", "upi": "UPI", "bank_transfer": "Bank transfer"}


def _recorded_by(db: Session, payment: Payment) -> dict | None:
    t, i = payment.recorded_by_type, payment.recorded_by_id
    if not t:
        return None
    name = None
    model = {UserType.RESIDENT: Resident, UserType.SOCIETY_ADMIN: SocietyAdmin, UserType.SUPER_ADMIN: SuperAdmin}.get(t)
    if model and i:
        row = db.get(model, i)
        name = getattr(row, "name", None)
    return {"type": t, "name": name or t.replace("_", " ").title()}


def build_details(db: Session, payment: Payment) -> dict:
    society = db.get(Society, payment.society_id)
    resident = db.get(Resident, payment.resident_id) if payment.resident_id else None
    unit = db.get(Unit, payment.unit_id) if payment.unit_id else None
    wing = db.get(Wing, unit.wing_id) if unit else None
    unit_label = f"{wing.name}-{unit.unit_no}" if unit and wing else None

    lines: list[dict] = []
    meta: dict = {}
    paid_to = {"kind": "society", "name": society.name, "detail": "Society account"}
    if payment.ref_type == PaymentRefType.BOOKING:
        booking = db.get(Booking, payment.ref_id)
        deal = db.get(GroupDeal, booking.deal_id)
        title = deal.title
        lines.append({"description": deal.title, "detail": f"Service on {deal.service_date:%d %b %Y}" + (f" · {booking.slot}" if booking.slot else ""),
                      "qty": booking.units_count, "rate": float(deal.price_per_unit), "amount": float(booking.amount)})
        meta = {"booking_no": booking.booking_no, "booking_status": booking.status, "booking_payment_status": booking.payment_status,
                "category": deal.category, "service_date": deal.service_date.isoformat(), "slot": booking.slot}
        partner = db.get(ServicePartner, deal.partner_id) if deal.partner_id else None
        if partner:
            paid_to = {"kind": "vendor", "name": partner.business_name or partner.name, "detail": f"Service partner · {partner.name} · {partner.mobile}"}
        else:
            paid_to = {"kind": "society", "name": society.name, "detail": "Deal run by the society"}
        invoice_no = f"INV-{booking.booking_no}"
        purpose_title = f"Group deal: {title}"
    else:
        bill = db.get(MaintenanceBill, payment.ref_id)
        lines.append({"description": f"Maintenance {bill.period}", "detail": f"Bill {bill.bill_no} · due {bill.due_date:%d %b %Y}", "qty": 1,
                      "rate": float(bill.amount), "amount": float(bill.amount)})
        if bill.late_fee:
            lines.append({"description": "Late fee", "detail": None, "qty": 1, "rate": float(bill.late_fee), "amount": float(bill.late_fee)})
        meta = {"bill_no": bill.bill_no, "period": bill.period, "bill_status": bill.status, "bill_total": float(bill.amount) + float(bill.late_fee or 0),
                "bill_paid_so_far": float(bill.paid_amount or 0), "due_date": bill.due_date.isoformat()}
        invoice_no = f"INV-{bill.bill_no}-{payment.txn_id[-6:]}"
        purpose_title = f"Maintenance {bill.period} ({bill.bill_no})"

    refunds = db.scalars(select(Refund).where(Refund.payment_id == payment.id).order_by(Refund.id)).all()
    timeline = [{"label": "Payment started", "at": payment.created_at}]
    if payment.paid_at:
        timeline.append({"label": "Payment received", "at": payment.paid_at})
    if payment.failure_reason:
        timeline.append({"label": f"Failed: {payment.failure_reason}", "at": payment.updated_at})
    for r in refunds:
        timeline.append({"label": f"Refund {r.refund_no} {r.status}", "at": r.completed_at or r.processed_at or r.approved_at or r.created_at})

    return {
        "id": payment.id,
        "txn_id": payment.txn_id,
        "invoice_no": invoice_no,
        "status": payment.status,
        "amount": float(payment.amount),
        "mode": payment.mode,
        "mode_label": MODE_LABEL.get(payment.mode, payment.mode.replace("_", " ").title()),
        "paid_at": payment.paid_at,
        "created_at": payment.created_at,
        "failure_reason": payment.failure_reason,
        "notes": payment.notes,
        "ref_type": payment.ref_type,
        "ref_id": payment.ref_id,
        "purpose": {"title": purpose_title, "lines": lines, "meta": meta},
        "society": {"id": society.id, "name": society.name, "code": society.code, "address": society.address, "area": society.area,
                    "city": society.city, "state": society.state, "pincode": society.pincode,
                    "registration_number": society.registration_number},
        "payer": {"id": resident.id, "name": resident.name, "mobile": resident.mobile, "email": resident.email,
                  "resident_type": resident.resident_type, "unit": unit_label} if resident else ({"unit": unit_label} if unit_label else None),
        "paid_to": paid_to,
        "gateway": {"name": payment.gateway, "order_id": payment.gateway_order_id, "payment_id": payment.gateway_txn_id,
                    "reference_no": payment.reference_no},
        "recorded_by": _recorded_by(db, payment),
        "refunds": [{"id": r.id, "refund_no": r.refund_no, "amount": float(r.amount), "status": r.status, "reason": r.reason,
                     "created_at": r.created_at, "completed_at": r.completed_at} for r in refunds],
        "refunded_total": round(sum(float(r.amount) for r in refunds if r.status != "rejected"), 2),
        "timeline": timeline,
    }
