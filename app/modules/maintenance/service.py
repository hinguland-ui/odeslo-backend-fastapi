import calendar
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import (
    BillingCycle,
    BillStatus,
    NotificationCategory,
    OccupancyStatus,
    ResidentStatus,
    UNPAID_BILL_STATUSES,
    UserType,
)
from app.core.timeutils import now_ist, today_ist
from app.core.utils import generate_ref
from app.modules.maintenance.models import MaintenanceBill
from app.modules.notifications.service import notify
from app.modules.residents.models import Resident
from app.modules.societies.models import Society, Unit, Wing


def period_for(cycle: str, day: date) -> tuple[str, date, date]:
    if cycle == BillingCycle.QUARTERLY:
        q = (day.month - 1) // 3
        start = date(day.year, q * 3 + 1, 1)
        end_month = q * 3 + 3
        end = date(day.year, end_month, calendar.monthrange(day.year, end_month)[1])
        return f"{day.year}-Q{q + 1}", start, end
    start = date(day.year, day.month, 1)
    end = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
    return f"{day:%Y-%m}", start, end


def bill_recipients(db: Session, unit_id: int) -> list[int]:
    """Owner + tenant(s) allowed to view maintenance."""
    residents = db.scalars(select(Resident).where(Resident.unit_id == unit_id,
                                                  Resident.status == ResidentStatus.ACTIVE)).all()
    from app.modules.residents.service import can_view_maintenance

    return [r.id for r in residents if can_view_maintenance(r)]


def generate_bills(db: Session, society: Society, actor: CurrentUser, period_start: date | None,
                   include_vacant: bool, notify_residents: bool) -> dict:
    settings = society.maintenance_settings or {}
    cycle = settings.get("billing_cycle", BillingCycle.MONTHLY)
    period, start, end = period_for(cycle, period_start or today_ist())
    due_day = min(int(settings.get("due_day", 10)), 28)
    due_date = date(start.year, start.month, due_day)
    amounts = settings.get("amounts", {})

    created = skipped_existing = skipped_no_amount = 0
    total = 0.0
    existing = set(db.scalars(select(MaintenanceBill.unit_id).where(MaintenanceBill.society_id == society.id,
                                                                    MaintenanceBill.period == period)).all())
    units = db.scalars(select(Unit).where(Unit.society_id == society.id)).all()
    for unit in units:
        if unit.id in existing:
            skipped_existing += 1
            continue
        if not include_vacant and unit.occupancy_status == OccupancyStatus.VACANT:
            continue
        amount = float(amounts.get(unit.unit_type, 0) or 0)
        if amount <= 0:
            skipped_no_amount += 1
            continue
        primary = db.scalar(select(Resident.id).where(Resident.unit_id == unit.id, Resident.is_primary.is_(True),
                                                      Resident.status == ResidentStatus.ACTIVE))
        bill = MaintenanceBill(bill_no=generate_ref("MB"), society_id=society.id, unit_id=unit.id,
                               resident_id=primary, period=period, period_start=start, period_end=end,
                               amount=amount, due_date=due_date,
                               status=BillStatus.DUE if notify_residents else BillStatus.GENERATED,
                               generated_by=actor.id)
        db.add(bill)
        db.flush()
        created += 1
        total += amount
        if notify_residents:
            for rid in bill_recipients(db, unit.id):
                notify(db, UserType.RESIDENT, rid, NotificationCategory.MAINTENANCE, "Maintenance bill generated",
                       f"₹{amount:g} for {period} is due by {due_date:%d %b %Y}.", ref_type="maintenance_bill",
                       ref_id=bill.id)
    return {"period": period, "created": created, "skipped_existing": skipped_existing,
            "skipped_no_amount": skipped_no_amount, "total_amount": round(total, 2)}


def send_reminder(db: Session, bill: MaintenanceBill, sms: bool) -> None:
    for rid in bill_recipients(db, bill.unit_id):
        notify(db, UserType.RESIDENT, rid, NotificationCategory.MAINTENANCE, "Maintenance payment reminder",
               f"₹{bill.outstanding:g} for {bill.period} was due on {bill.due_date:%d %b %Y}. Please pay to avoid "
               "late fees.", ref_type="maintenance_bill", ref_id=bill.id, sms=sms, whatsapp=sms)
    bill.reminder_count += 1
    bill.last_reminder_at = now_ist()
    if bill.status == BillStatus.GENERATED:
        bill.status = BillStatus.DUE


def apply_overdue_and_late_fees(db: Session) -> int:
    """Scheduler: bills past due become Overdue; the society's late fee is applied once."""
    today = today_ist()
    bills = db.scalars(select(MaintenanceBill).where(
        MaintenanceBill.status.in_((BillStatus.GENERATED, BillStatus.DUE, BillStatus.PARTIALLY_PAID)),
        MaintenanceBill.due_date < today)).all()
    societies: dict[int, Society] = {}
    for bill in bills:
        society = societies.setdefault(bill.society_id, db.get(Society, bill.society_id))
        settings = society.maintenance_settings or {}
        if settings.get("late_fee_enabled") and not bill.late_fee:
            bill.late_fee = float(settings.get("late_fee", 0) or 0)
        bill.status = BillStatus.OVERDUE
        send_reminder(db, bill, sms=True)
    return len(bills)


def with_unit_labels(db: Session, bills: Sequence[MaintenanceBill]) -> list[dict]:
    from app.modules.maintenance.schemas import BillOut

    unit_ids = {b.unit_id for b in bills}
    units = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}
    wing_ids = {u.wing_id for u in units.values()}
    wings = {w.id: w for w in db.scalars(select(Wing).where(Wing.id.in_(wing_ids))).all()} if wing_ids else {}
    out = []
    for b in bills:
        u = units.get(b.unit_id)
        label = f"{wings[u.wing_id].name}-{u.unit_no}" if u and u.wing_id in wings else None
        out.append({**BillOut.model_validate(b).model_dump(), "unit_label": label})
    return out


def unpaid_stmt(society_id: int):
    return select(MaintenanceBill).where(MaintenanceBill.society_id == society_id,
                                         MaintenanceBill.status.in_(UNPAID_BILL_STATUSES))
