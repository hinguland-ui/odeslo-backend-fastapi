from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles, scoped_society_id
from app.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    UNPAID_BILL_STATUSES,
    AccountStatus,
    AdminPermission,
    JobStatus,
    PaymentStatus,
    UserType,
)
from app.core.timeutils import today_ist
from app.modules.announcements.models import Announcement
from app.modules.bookings.models import Booking
from app.modules.dashboard import report_export, report_section, reports, service
from app.modules.maintenance.models import MaintenanceBill
from app.modules.notifications.models import Notification
from app.modules.payments.models import Payment
from app.modules.residents.service import can_view_maintenance
from app.modules.service_jobs.models import ServiceJob
from app.modules.societies.models import Society, Unit, Wing
from app.modules.society_admins.models import SocietyAdmin

router = APIRouter(tags=["Dashboards & Reports"])


@router.get("/dashboard/super-admin")
def super_admin_dashboard(user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN)),
                          db: Session = Depends(get_db)):
    return service.super_admin_dashboard(db)


@router.get("/dashboard/society-admin")
def society_admin_dashboard(user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN)),
                            db: Session = Depends(get_db)):
    return service.society_admin_dashboard(db, user.society_id)


@router.get("/dashboard/resident/home")
def resident_home(user: CurrentUser = Depends(require_roles(UserType.RESIDENT, allow_limited_resident=True)),
                  db: Session = Depends(get_db)):
    """Home - My Society overview card. Pending residents get a limited payload (masjid only)."""
    society = db.get(Society, user.society_id)
    unit = db.get(Unit, user.unit_id)
    wing = db.get(Wing, unit.wing_id)
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_type == user.user_type, Notification.user_id == user.id,
        Notification.is_read.is_(False))) or 0
    base = {
        "resident": {"id": user.id, "name": user.name, "status": user.status, "resident_type": user.resident_type,
                     "rejection_reason": getattr(user.entity, "rejection_reason", None)},
        "society": {"id": society.id, "name": society.name, "code": society.code, "logo": society.logo,
                    "area": society.area, "city": society.city, "lat": society.lat, "lng": society.lng,
                    "features": society.features},
        "unit": {"id": unit.id, "label": f"{wing.name}-{unit.unit_no}", "type": unit.unit_type},
        "unread_notifications": unread,
        "limited_access": user.status != "active",
    }
    if user.status != "active":
        return base
    maintenance = None
    if can_view_maintenance(user.entity):
        unpaid = db.scalars(select(MaintenanceBill).where(MaintenanceBill.unit_id == user.unit_id,
                                                          MaintenanceBill.status.in_(UNPAID_BILL_STATUSES))).all()
        maintenance = {"due": round(sum(b.outstanding for b in unpaid), 2),
                       "due_date": min((b.due_date for b in unpaid), default=None), "unpaid_bills": len(unpaid)}
    notices = db.scalars(select(Announcement).where(
        (Announcement.society_id == user.society_id) | (Announcement.society_id.is_(None)))
        .order_by(Announcement.is_pinned.desc(), Announcement.id.desc()).limit(3)).all()
    return {
        **base,
        "maintenance": maintenance,
        "active_bookings": db.scalar(select(func.count()).select_from(Booking).where(
            Booking.unit_id == user.unit_id, Booking.status.in_(ACTIVE_BOOKING_STATUSES))) or 0,
        "latest_notices": [{"id": n.id, "title": n.title, "category": n.category, "created_at": n.created_at}
                           for n in notices],
    }


@router.get("/dashboard/resident/my-society")
def my_society(user: CurrentUser = Depends(require_roles(UserType.RESIDENT)), db: Session = Depends(get_db)):
    """My Society screen: society profile + committee contacts."""
    society = db.get(Society, user.society_id)
    from app.modules.staff.models import Staff

    committee = db.scalars(select(SocietyAdmin).where(SocietyAdmin.society_id == user.society_id,
                                                      SocietyAdmin.status == AccountStatus.ACTIVE)).all()

    def count(model, *conds):
        return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0

    from app.modules.group_deals.models import GroupDeal
    from app.modules.service_partners.models import PartnerSociety, ServicePartner

    staff = db.scalars(select(Staff).where(Staff.society_id == society.id, Staff.status == AccountStatus.ACTIVE)
                       .order_by(Staff.type, Staff.name)).all()
    vendors = db.scalars(select(ServicePartner).join(PartnerSociety, PartnerSociety.partner_id == ServicePartner.id)
                         .where(PartnerSociety.society_id == society.id, ServicePartner.verified.is_(True),
                                ServicePartner.status == AccountStatus.ACTIVE)).all()
    vendor_categories: dict[str, int] = {}
    for v in vendors:
        for c in v.categories or []:
            vendor_categories[c] = vendor_categories.get(c, 0) + 1
    open_deals = count(GroupDeal, (GroupDeal.society_id == society.id) | GroupDeal.society_id.is_(None),
                       GroupDeal.status.in_(("published", "full")))

    return {
        "society": {"id": society.id, "code": society.code, "name": society.name, "address": society.address,
                    "area": society.area, "city": society.city, "state": society.state, "pincode": society.pincode,
                    "logo": society.logo, "type": society.type, "status": society.status,
                    "registration_number": society.registration_number, "features": society.features or {}},
        "staff": [{"name": s.name, "type": s.type, "mobile": s.mobile} for s in staff],
        "vendors": {"total": len(vendors), "by_category": vendor_categories},
        "open_deals": open_deals,
        "stats": {
            "units": count(Unit, Unit.society_id == society.id),
            "wings": count(Wing, Wing.society_id == society.id),
            "staff": count(Staff, Staff.society_id == society.id, Staff.status == AccountStatus.ACTIVE),
        },
        "committee": [{"name": a.name, "designation": a.designation, "mobile": a.mobile, "email": a.email}
                      for a in committee],
    }


@router.get("/dashboard/staff")
def staff_dashboard(user: CurrentUser = Depends(require_roles(UserType.STAFF, UserType.PARTNER)),
                    db: Session = Depends(get_db)):
    today = today_ist()
    mine = (ServiceJob.assigned_to_type == user.user_type, ServiceJob.assigned_to_id == user.id)

    def count(*conds):
        return db.scalar(select(func.count()).select_from(ServiceJob).where(*mine, *conds)) or 0

    data = {
        "today_jobs": count(ServiceJob.scheduled_at.between(datetime.combine(today, time.min),
                                                            datetime.combine(today, time.max))),
        "active_jobs": count(ServiceJob.status.not_in((JobStatus.COMPLETED, JobStatus.CANCELLED))),
        "completed_jobs": count(ServiceJob.status == JobStatus.COMPLETED),
    }
    if user.user_type == UserType.STAFF:
        from app.modules.complaints.models import Complaint
        from app.core.enums import OPEN_COMPLAINT_STATUSES

        data["open_complaints"] = db.scalar(select(func.count()).select_from(Complaint).where(
            Complaint.assigned_to == user.id, Complaint.status.in_(OPEN_COMPLAINT_STATUSES))) or 0
    else:
        from app.modules.service_partners.service import earnings

        data["earnings"] = earnings(db, user.id)
    return data


# ---------- Reports ----------
Reporters = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.REPORTS)


def _range(stmt, col, date_from: date | None, date_to: date | None):
    if date_from:
        stmt = stmt.where(col >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(col <= datetime.combine(date_to, time.max))
    return stmt


@router.get("/reports/bookings")
def bookings_report(society_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                    user: CurrentUser = Depends(Reporters), db: Session = Depends(get_db)):
    stmt = select(Booking.society_id, Booking.status, func.count(), func.coalesce(func.sum(Booking.units_count), 0),
                  func.coalesce(func.sum(Booking.amount), 0)).group_by(Booking.society_id, Booking.status)
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Booking.society_id == sid)
    stmt = _range(stmt, Booking.created_at, date_from, date_to)
    return [{"society_id": s, "status": st, "bookings": c, "units": int(u), "amount": float(a)}
            for s, st, c, u, a in db.execute(stmt).all()]


@router.get("/reports/revenue")
def revenue_report(society_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                   user: CurrentUser = Depends(Reporters), db: Session = Depends(get_db)):
    day = func.date(Payment.paid_at)
    stmt = (select(day, Payment.ref_type, Payment.mode, func.count(), func.sum(Payment.amount))
            .where(Payment.status == PaymentStatus.SUCCESS).group_by(day, Payment.ref_type, Payment.mode)
            .order_by(day))
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Payment.society_id == sid)
    stmt = _range(stmt, Payment.paid_at, date_from, date_to)
    return [{"date": str(d), "ref_type": rt, "mode": m, "payments": c, "amount": float(a or 0)}
            for d, rt, m, c, a in db.execute(stmt).all()]


@router.get("/reports/maintenance")
def maintenance_report(society_id: int | None = None, user: CurrentUser = Depends(Reporters),
                       db: Session = Depends(get_db)):
    stmt = (select(MaintenanceBill.period, MaintenanceBill.status, func.count(),
                   func.coalesce(func.sum(MaintenanceBill.amount + MaintenanceBill.late_fee), 0),
                   func.coalesce(func.sum(MaintenanceBill.paid_amount), 0))
            .group_by(MaintenanceBill.period, MaintenanceBill.status).order_by(MaintenanceBill.period.desc()))
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(MaintenanceBill.society_id == sid)
    return [{"period": p, "status": s, "bills": c, "billed": float(b), "collected": float(paid)}
            for p, s, c, b, paid in db.execute(stmt).all()]


@router.get("/reports/service-jobs")
def jobs_report(society_id: int | None = None, user: CurrentUser = Depends(Reporters), db: Session = Depends(get_db)):
    stmt = (select(ServiceJob.assigned_to_type, ServiceJob.assigned_to_id, ServiceJob.status, func.count(),
                   func.avg(ServiceJob.rating))
            .group_by(ServiceJob.assigned_to_type, ServiceJob.assigned_to_id, ServiceJob.status))
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(ServiceJob.society_id == sid)
    return [{"assignee_type": t, "assignee_id": i, "status": s, "jobs": c,
             "avg_rating": round(float(r), 2) if r else None} for t, i, s, c, r in db.execute(stmt).all()]


# ---------- Super admin: the full report (page, Excel, PDF) ----------
_ReportAdmin = require_roles(UserType.SUPER_ADMIN)


def _filters(date_from, date_to, society_ids, category, mode, group_by) -> reports.Filters:
    return reports.Filters.make(date_from, date_to, society_ids, category, mode, group_by)


@router.get("/reports/overview")
def report_overview(date_from: date | None = None, date_to: date | None = None,
                    society_ids: str | None = Query(None, description="comma separated society ids; empty = all"),
                    category: str | None = None, mode: str | None = None, group_by: str | None = None,
                    user: CurrentUser = Depends(_ReportAdmin), db: Session = Depends(get_db)):
    """Everything the Reports page shows, for one set of filters."""
    return reports.build_report(db, _filters(date_from, date_to, society_ids, category, mode, group_by))


@router.get("/reports/export")
def report_export_file(format: str = Query("xlsx", pattern="^(xlsx|pdf)$"), date_from: date | None = None, date_to: date | None = None,
                       society_ids: str | None = None, category: str | None = None, mode: str | None = None,
                       group_by: str | None = None,
                       section: str | None = Query(None, pattern="^(revenue|societies|bookings|maintenance|complaints|residents)$"),
                       user: CurrentUser = Depends(_ReportAdmin), db: Session = Depends(get_db)):
    """The same report as an Excel workbook (one sheet per topic, real dates and numbers) or a PDF."""
    f = _filters(date_from, date_to, society_ids, category, mode, group_by)
    data = reports.build_report(db, f)
    name = f"Odeslo-{section or 'report'}-{f.date_from:%Y%m%d}-{f.date_to:%Y%m%d}"
    if section:  # one chart: its own period, its own file
        blob = report_section.to_section_pdf(data, section) if format == "pdf" else report_section.to_section_xlsx(data, section)
        mime = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(blob, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}.{format}"', "Cache-Control": "private, no-store"})
    if format == "pdf":
        return Response(report_export.to_pdf(data), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"', "Cache-Control": "private, no-store"})
    return Response(report_export.to_xlsx(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"', "Cache-Control": "private, no-store"})
