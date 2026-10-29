from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AdminPermission, BillStatus, Feature, UNPAID_BILL_STATUSES, UserType
from app.core.errors import bad_request, forbidden, not_found
from app.core.pagination import Page, PageParams, paginate
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.maintenance import service
from app.modules.maintenance.models import MaintenanceBill
from app.modules.maintenance.schemas import (
    BillGenerate,
    BillGenerateResult,
    BillOut,
    BillUpdate,
    DefaulterOut,
    MaintenanceSummary,
    MyMaintenanceOut,
    ReminderRequest,
)
from app.modules.residents.models import Resident
from app.modules.residents.service import can_view_maintenance
from app.modules.societies.service import ensure_feature_enabled, get_society_or_404

router = APIRouter(prefix="/maintenance", tags=["Maintenance"])

MaintenanceAdmins = require_roles(UserType.SOCIETY_ADMIN, perm=AdminPermission.MAINTENANCE)
MaintenanceViewers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.MAINTENANCE)
ActiveResident = require_roles(UserType.RESIDENT)


def _get_bill(db: Session, user: CurrentUser, bill_id: int) -> MaintenanceBill:
    bill = db.get(MaintenanceBill, bill_id)
    if not bill:
        raise not_found("Bill")
    if user.is_resident:
        if bill.unit_id != user.unit_id or not can_view_maintenance(user.entity):
            raise not_found("Bill")
    else:
        ensure_society_access(user, bill.society_id, "Bill")
    return bill


# ---------- Resident ----------
@router.get("/my", response_model=MyMaintenanceOut)
def my_maintenance(user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    ensure_feature_enabled(db, user.society_id, Feature.MAINTENANCE)
    if not can_view_maintenance(user.entity):
        raise forbidden("Maintenance details are not shared with your account")
    bills = db.scalars(select(MaintenanceBill).where(MaintenanceBill.unit_id == user.unit_id)
                       .order_by(MaintenanceBill.period_start.desc())).all()
    unpaid = [b for b in bills if b.status in UNPAID_BILL_STATUSES]
    return {
        "current_due": round(sum(b.outstanding for b in unpaid), 2),
        "next_due_date": min((b.due_date for b in unpaid), default=None),
        "unpaid_bills": service.with_unit_labels(db, unpaid),
        "history": service.with_unit_labels(db, [b for b in bills if b.status not in UNPAID_BILL_STATUSES]),
    }


# ---------- Admin ----------
@router.post("/bills/generate", response_model=BillGenerateResult)
def generate_bills(data: BillGenerate, user: CurrentUser = Depends(MaintenanceAdmins), db: Session = Depends(get_db)):
    ensure_feature_enabled(db, user.society_id, Feature.MAINTENANCE)
    society = get_society_or_404(db, user.society_id)
    result = service.generate_bills(db, society, user, data.period_start, data.include_vacant, data.notify_residents)
    log_activity(db, user, "maintenance.generate_bills", "maintenance_bill", None, new=result)
    db.commit()
    return result


@router.get("/bills", response_model=Page[BillOut])
def list_bills(society_id: int | None = None, period: str | None = None, status: str | None = None,
               unit_id: int | None = None, unpaid_only: bool = False, params: PageParams = Depends(),
               user: CurrentUser = Depends(MaintenanceViewers), db: Session = Depends(get_db)):
    stmt = select(MaintenanceBill).order_by(MaintenanceBill.period_start.desc(), MaintenanceBill.unit_id)
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(MaintenanceBill.society_id == sid)
    if period:
        stmt = stmt.where(MaintenanceBill.period == period)
    if status:
        stmt = stmt.where(MaintenanceBill.status == status)
    if unit_id:
        stmt = stmt.where(MaintenanceBill.unit_id == unit_id)
    if unpaid_only:
        stmt = stmt.where(MaintenanceBill.status.in_(UNPAID_BILL_STATUSES))
    page = paginate(db, stmt, params)
    page["items"] = service.with_unit_labels(db, page["items"])
    return page


@router.get("/summary", response_model=MaintenanceSummary)
def summary(society_id: int | None = None, period: str | None = None,
            user: CurrentUser = Depends(MaintenanceViewers), db: Session = Depends(get_db)):
    stmt = select(MaintenanceBill)
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(MaintenanceBill.society_id == sid)
    if period:
        stmt = stmt.where(MaintenanceBill.period == period)
    bills = db.scalars(stmt).all()
    billed = sum(b.total_amount for b in bills)
    collected = sum(b.paid_amount or 0 for b in bills)
    return {"period": period, "billed": round(billed, 2), "collected": round(collected, 2),
            "pending": round(sum(b.outstanding for b in bills), 2), "bills": len(bills),
            "paid_bills": sum(1 for b in bills if b.status == BillStatus.PAID),
            "overdue_bills": sum(1 for b in bills if b.status == BillStatus.OVERDUE)}


@router.get("/defaulters", response_model=list[DefaulterOut])
def defaulters(society_id: int | None = None, user: CurrentUser = Depends(MaintenanceViewers),
               db: Session = Depends(get_db)):
    sid = scoped_society_id(user, society_id)
    if sid is None:
        raise bad_request("society_id is required")
    bills = db.scalars(service.unpaid_stmt(sid).where(MaintenanceBill.status == BillStatus.OVERDUE)).all()
    labels = {b["id"]: b["unit_label"] for b in service.with_unit_labels(db, bills)}
    grouped: dict[int, list[MaintenanceBill]] = defaultdict(list)
    for b in bills:
        grouped[b.unit_id].append(b)
    result = []
    for unit_id, unit_bills in grouped.items():
        resident = db.scalar(select(Resident).where(Resident.unit_id == unit_id, Resident.is_primary.is_(True),
                                                    Resident.status == "active"))
        result.append({"unit_id": unit_id, "unit_label": labels[unit_bills[0].id] or str(unit_id),
                       "resident_name": resident.name if resident else None,
                       "resident_mobile": resident.mobile if resident else None,
                       "unpaid_bills": len(unit_bills), "outstanding": round(sum(b.outstanding for b in unit_bills), 2),
                       "oldest_due_date": min(b.due_date for b in unit_bills)})
    return sorted(result, key=lambda r: r["outstanding"], reverse=True)


@router.post("/reminders")
def send_reminders(data: ReminderRequest, user: CurrentUser = Depends(MaintenanceAdmins),
                   db: Session = Depends(get_db)):
    stmt = service.unpaid_stmt(user.society_id)
    if data.bill_ids:
        stmt = stmt.where(MaintenanceBill.id.in_(data.bill_ids))
    bills = db.scalars(stmt).all()
    for bill in bills:
        service.send_reminder(db, bill, data.sms)
    log_activity(db, user, "maintenance.send_reminders", "maintenance_bill", None, new={"bills": len(bills)})
    db.commit()
    return {"reminded": len(bills)}


@router.get("/bills/{bill_id}", response_model=BillOut)
def get_bill(bill_id: int,
             user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT,
                                                       perm=AdminPermission.MAINTENANCE)),
             db: Session = Depends(get_db)):
    return service.with_unit_labels(db, [_get_bill(db, user, bill_id)])[0]


@router.patch("/bills/{bill_id}", response_model=BillOut)
def update_bill(bill_id: int, data: BillUpdate, user: CurrentUser = Depends(MaintenanceAdmins),
                db: Session = Depends(get_db)):
    bill = _get_bill(db, user, bill_id)
    if bill.status == BillStatus.PAID:
        raise bad_request("Paid bills cannot be changed")
    before = snapshot(bill)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(bill, k, v)
    if bill.paid_amount and bill.outstanding <= 0:
        bill.status = BillStatus.PAID
    db.flush()
    old, new = diff(before, snapshot(bill))
    log_activity(db, user, "maintenance.update_bill", "maintenance_bill", bill.id, old, new)
    db.commit()
    return service.with_unit_labels(db, [bill])[0]
