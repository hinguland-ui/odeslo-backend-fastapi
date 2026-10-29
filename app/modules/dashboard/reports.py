"""Platform reports for the super admin: one filtered snapshot that feeds the charts, the tables and the exports.

Everything is worked out from the same rows with the same filters, so what the page shows is exactly what the Excel
and PDF contain. Time series are filled with zeros for days / weeks / months without activity so charts have no holes.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request
from app.core.timeutils import now_ist, today_ist
from app.modules.bookings.models import Booking
from app.modules.complaints.models import Complaint
from app.modules.group_deals.models import GroupDeal
from app.modules.maintenance.models import MaintenanceBill
from app.modules.payments.models import Payment
from app.modules.refunds.models import Refund
from app.modules.residents.models import Resident
from app.modules.service_jobs.models import ServiceJob
from app.modules.service_partners.models import ServicePartner
from app.modules.societies.models import Society, Unit

MAX_DAYS = 366 * 3
PAYMENT_ROWS_CAP = 10_000
GROUPS = ("day", "week", "month")


@dataclass
class Filters:
    date_from: date
    date_to: date
    society_ids: list[int] = field(default_factory=list)
    category: str | None = None
    mode: str | None = None
    group_by: str = "day"

    @classmethod
    def make(cls, date_from: date | None, date_to: date | None, society_ids: str | None, category: str | None,
             mode: str | None, group_by: str | None) -> "Filters":
        end = date_to or today_ist()
        start = date_from or (end - timedelta(days=29))
        if start > end:
            raise bad_request("The start date is after the end date")
        if (end - start).days > MAX_DAYS:
            raise bad_request("Choose a range of at most 3 years")
        ids = [int(x) for x in (society_ids or "").split(",") if x.strip().isdigit()]
        gb = group_by if group_by in GROUPS else ("day" if (end - start).days <= 62 else "week" if (end - start).days <= 366 else "month")
        return cls(start, end, ids, category or None, mode or None, gb)

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.date_from, time.min)

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.date_to, time.max)


# ------------------------------------------------------------------ time buckets
def bucket_of(d: date, group_by: str) -> date:
    if group_by == "week":
        return d - timedelta(days=d.weekday())
    if group_by == "month":
        return d.replace(day=1)
    return d


def all_buckets(f: Filters) -> list[date]:
    out, cur = [], bucket_of(f.date_from, f.group_by)
    while cur <= f.date_to:
        out.append(cur)
        if f.group_by == "month":
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            cur += timedelta(days=7 if f.group_by == "week" else 1)
    return out


def bucket_label(b: date, group_by: str) -> str:
    if group_by == "month":
        return b.strftime("%b %Y")
    if group_by == "week":
        return f"Wk {b.strftime('%d %b')}"
    return b.strftime("%d %b")


def _d(v: datetime | date | None) -> date | None:
    return v.date() if isinstance(v, datetime) else v


# ------------------------------------------------------------------ the snapshot
def build_report(db: Session, f: Filters) -> dict:
    sids = f.society_ids
    societies = {s.id: s for s in db.scalars(select(Society).where(Society.id.in_(sids)) if sids else select(Society)).all()}
    in_scope = lambda sid: (not sids) or sid in sids  # noqa: E731

    # ---- payments (money in): paid in range
    pay_stmt = select(Payment).where(Payment.status == "success", Payment.paid_at >= f.start_dt, Payment.paid_at <= f.end_dt)
    if sids:
        pay_stmt = pay_stmt.where(Payment.society_id.in_(sids))
    if f.mode:
        pay_stmt = pay_stmt.where(Payment.mode == f.mode)
    payments = db.scalars(pay_stmt.order_by(Payment.paid_at.desc())).all()

    booking_ids = {p.ref_id for p in payments if p.ref_type == "booking"}
    bookings_by_id = {b.id: b for b in db.scalars(select(Booking).where(Booking.id.in_(booking_ids))).all()} if booking_ids else {}
    deal_ids = {b.deal_id for b in bookings_by_id.values()}
    deals_by_id = {d.id: d for d in db.scalars(select(GroupDeal).where(GroupDeal.id.in_(deal_ids))).all()} if deal_ids else {}
    if f.category:  # a category filter keeps only the deal payments of that category
        payments = [p for p in payments if p.ref_type == "booking" and deals_by_id.get(bookings_by_id[p.ref_id].deal_id) and
                    deals_by_id[bookings_by_id[p.ref_id].deal_id].category == f.category]
    res_ids = {p.resident_id for p in payments if p.resident_id}
    residents_by_id = {r.id: r for r in db.scalars(select(Resident).where(Resident.id.in_(res_ids))).all()} if res_ids else {}

    # ---- failed payments in range (created)
    failed_stmt = select(Payment).where(Payment.status == "failed", Payment.created_at >= f.start_dt, Payment.created_at <= f.end_dt)
    if sids:
        failed_stmt = failed_stmt.where(Payment.society_id.in_(sids))
    failed_count = len(db.scalars(failed_stmt).all())

    # ---- refunds created in range
    ref_stmt = select(Refund).where(Refund.created_at >= f.start_dt, Refund.created_at <= f.end_dt)
    if sids:
        ref_stmt = ref_stmt.where(Refund.society_id.in_(sids))
    refunds = db.scalars(ref_stmt).all()
    money_out = [r for r in refunds if r.status in ("processed", "completed")]

    # ---- bookings created in range
    bk_stmt = select(Booking).where(Booking.created_at >= f.start_dt, Booking.created_at <= f.end_dt)
    if sids:
        bk_stmt = bk_stmt.where(Booking.society_id.in_(sids))
    bookings = db.scalars(bk_stmt).all()
    more_deals = {d.id: d for d in db.scalars(select(GroupDeal).where(GroupDeal.id.in_({b.deal_id for b in bookings} - set(deals_by_id)))).all()} if bookings else {}
    deals_by_id.update(more_deals)
    if f.category:
        bookings = [b for b in bookings if deals_by_id.get(b.deal_id) and deals_by_id[b.deal_id].category == f.category]

    # ---- deals created in range
    dl_stmt = select(GroupDeal).where(GroupDeal.created_at >= f.start_dt, GroupDeal.created_at <= f.end_dt)
    if sids:
        dl_stmt = dl_stmt.where(GroupDeal.society_id.in_(sids))
    if f.category:
        dl_stmt = dl_stmt.where(GroupDeal.category == f.category)
    deals = db.scalars(dl_stmt).all()

    # ---- maintenance bills due in range
    mb_stmt = select(MaintenanceBill).where(MaintenanceBill.due_date >= f.date_from, MaintenanceBill.due_date <= f.date_to)
    if sids:
        mb_stmt = mb_stmt.where(MaintenanceBill.society_id.in_(sids))
    bills = db.scalars(mb_stmt).all()

    # ---- residents joined in range, complaints raised in range, jobs created in range
    rs_stmt = select(Resident).where(Resident.created_at >= f.start_dt, Resident.created_at <= f.end_dt)
    if sids:
        rs_stmt = rs_stmt.where(Resident.society_id.in_(sids))
    residents = db.scalars(rs_stmt).all()
    cp_stmt = select(Complaint).where(Complaint.created_at >= f.start_dt, Complaint.created_at <= f.end_dt)
    if sids:
        cp_stmt = cp_stmt.where(Complaint.society_id.in_(sids))
    complaints = db.scalars(cp_stmt).all()
    jb_stmt = select(ServiceJob).where(ServiceJob.created_at >= f.start_dt, ServiceJob.created_at <= f.end_dt)
    if sids:
        jb_stmt = jb_stmt.where(ServiceJob.society_id.in_(sids))
    jobs = db.scalars(jb_stmt).all()
    partner_ids = {j.assigned_to_id for j in jobs if j.assigned_to_type == "partner" and j.assigned_to_id}
    partners = {p.id: p for p in db.scalars(select(ServicePartner).where(ServicePartner.id.in_(partner_ids))).all()} if partner_ids else {}

    name_of = lambda sid: societies[sid].name if sid in societies else f"Society #{sid}"  # noqa: E731

    # ---- money
    def money(p: Payment) -> float:
        return float(p.amount)

    booking_rev = sum(money(p) for p in payments if p.ref_type == "booking")
    maint_rev = sum(money(p) for p in payments if p.ref_type == "maintenance")
    gross = booking_rev + maint_rev
    refunded = sum(float(r.amount) for r in money_out)

    billed = sum(float(b.amount) + float(b.late_fee or 0) for b in bills)
    collected = sum(float(b.paid_amount or 0) for b in bills)

    resolved = [c for c in complaints if c.status in ("resolved", "closed")]
    rated = [j.rating for j in jobs if j.rating]
    kpis = {
        "gross_revenue": round(gross, 2), "booking_revenue": round(booking_rev, 2), "maintenance_revenue": round(maint_rev, 2),
        "refunded": round(refunded, 2), "net_revenue": round(gross - refunded, 2),
        "payments": len(payments), "failed_payments": failed_count,
        "avg_payment": round(gross / len(payments), 2) if payments else 0,
        "bookings": len(bookings), "units_booked": sum(b.units_count for b in bookings if b.status in ("confirmed", "completed", "pending_payment")),
        "deals_created": len(deals), "deals_cancelled": sum(1 for d in deals if d.status == "cancelled"),
        "maintenance_billed": round(billed, 2), "maintenance_collected": round(collected, 2),
        "collection_rate": round(collected / billed * 100, 1) if billed else 0,
        "residents_joined": len(residents), "residents_approved": sum(1 for r in residents if r.status == "active"),
        "complaints": len(complaints), "complaints_resolved": len(resolved),
        "resolution_rate": round(len(resolved) / len(complaints) * 100, 1) if complaints else 0,
        "jobs": len(jobs), "jobs_completed": sum(1 for j in jobs if j.status == "completed"),
        "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
        "societies": len(societies),
    }

    # ---- time series
    buckets = all_buckets(f)
    key = lambda d: bucket_of(_d(d), f.group_by)  # noqa: E731
    series = {b: {"booking": 0.0, "maintenance": 0.0, "refunds": 0.0, "payments": 0, "bookings": 0, "units": 0,
                  "residents": 0, "complaints": 0, "resolved": 0, "jobs": 0} for b in buckets}
    for p in payments:
        s = series.get(key(p.paid_at))
        if s:
            s["booking" if p.ref_type == "booking" else "maintenance"] += money(p)
            s["payments"] += 1
    for r in money_out:
        s = series.get(key(r.completed_at or r.processed_at or r.created_at))
        if s:
            s["refunds"] += float(r.amount)
    for b in bookings:
        s = series.get(key(b.created_at))
        if s:
            s["bookings"] += 1
            s["units"] += b.units_count
    for r in residents:
        s = series.get(key(r.created_at))
        if s:
            s["residents"] += 1
    for c in complaints:
        s = series.get(key(c.created_at))
        if s:
            s["complaints"] += 1
        if c.resolved_at and f.date_from <= _d(c.resolved_at) <= f.date_to:
            s2 = series.get(key(c.resolved_at))
            if s2:
                s2["resolved"] += 1
    for j in jobs:
        s = series.get(key(j.created_at))
        if s:
            s["jobs"] += 1
    timeseries = [{"date": b.isoformat(), "label": bucket_label(b, f.group_by), **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in series[b].items()},
                   "net": round(series[b]["booking"] + series[b]["maintenance"] - series[b]["refunds"], 2)} for b in buckets]

    # ---- breakdowns
    def count_by(items, getter):
        c: dict[str, int] = defaultdict(int)
        for i in items:
            c[getter(i)] += 1
        return [{"name": k, "value": v} for k, v in sorted(c.items(), key=lambda kv: -kv[1])]

    by_mode: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for p in payments:
        by_mode[p.mode]["amount"] += money(p)
        by_mode[p.mode]["count"] += 1
    revenue_by_mode = [{"name": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in sorted(by_mode.items(), key=lambda kv: -kv[1]["amount"])]

    cat: dict[str, dict] = defaultdict(lambda: {"deals": 0, "bookings": 0, "units": 0, "gmv": 0.0})
    for d in deals:
        cat[d.category]["deals"] += 1
    for b in bookings:
        d = deals_by_id.get(b.deal_id)
        if d:
            cat[d.category]["bookings"] += 1
            if b.status in ("confirmed", "completed", "pending_payment"):
                cat[d.category]["units"] += b.units_count
            if b.payment_status == "paid":
                cat[d.category]["gmv"] += float(b.amount)
    deals_by_category = [{"category": k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}} for k, v in sorted(cat.items(), key=lambda kv: -kv[1]["gmv"])]

    top_deals = []
    deal_units: dict[int, int] = defaultdict(int)
    deal_gmv: dict[int, float] = defaultdict(float)
    for b in bookings:
        if b.status in ("confirmed", "completed", "pending_payment"):
            deal_units[b.deal_id] += b.units_count
        if b.payment_status == "paid":
            deal_gmv[b.deal_id] += float(b.amount)
    for did, gmv in sorted(deal_gmv.items(), key=lambda kv: -kv[1])[:15]:
        d = deals_by_id.get(did)
        if d:
            top_deals.append({"deal": d.title, "society": name_of(d.society_id) if d.society_id else "All societies", "category": d.category,
                              "units": deal_units.get(did, 0), "price_per_unit": float(d.price_per_unit), "gmv": round(gmv, 2), "status": d.status})

    maint_periods: dict[str, dict] = defaultdict(lambda: {"billed": 0.0, "collected": 0.0, "bills": 0, "paid": 0, "overdue": 0})
    for b in bills:
        m = maint_periods[b.period]
        m["billed"] += float(b.amount) + float(b.late_fee or 0)
        m["collected"] += float(b.paid_amount or 0)
        m["bills"] += 1
        m["paid"] += 1 if b.status == "paid" else 0
        m["overdue"] += 1 if b.status == "overdue" else 0
    maintenance_by_period = [{"period": k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()},
                              "rate": round(v["collected"] / v["billed"] * 100, 1) if v["billed"] else 0} for k, v in sorted(maint_periods.items())]

    comp_cat = count_by(complaints, lambda c: c.category)
    comp_status = count_by(complaints, lambda c: c.status)
    comp_priority = count_by(complaints, lambda c: c.priority)
    job_status = count_by(jobs, lambda j: j.status)
    rating_dist = [{"name": f"{s} star", "value": sum(1 for r in rated if r == s)} for s in (5, 4, 3, 2, 1)]
    res_type = count_by(residents, lambda r: r.resident_type)
    res_status = count_by(residents, lambda r: r.status)
    booking_status = count_by(bookings, lambda b: b.status)
    refund_status = count_by(refunds, lambda r: r.status)

    vend: dict[int, dict] = defaultdict(lambda: {"jobs": 0, "completed": 0, "ratings": [], "payout": 0.0})
    for j in jobs:
        if j.assigned_to_type == "partner" and j.assigned_to_id:
            v = vend[j.assigned_to_id]
            v["jobs"] += 1
            v["completed"] += 1 if j.status == "completed" else 0
            if j.rating:
                v["ratings"].append(j.rating)
            v["payout"] += float(j.payout_amount or 0)
    vendors = [{"vendor": (partners[i].business_name or partners[i].name) if i in partners else f"Vendor #{i}", "jobs": v["jobs"], "completed": v["completed"],
                "avg_rating": round(sum(v["ratings"]) / len(v["ratings"]), 2) if v["ratings"] else None, "payout": round(v["payout"], 2)}
               for i, v in sorted(vend.items(), key=lambda kv: -kv[1]["jobs"])[:15]]

    # ---- per society (the main comparison table)
    per: dict[int, dict] = {}
    unit_counts: dict[int, int] = defaultdict(int)
    for sid_, in db.execute(select(Unit.society_id)):
        unit_counts[sid_] += 1
    for sid_, s in societies.items():
        per[sid_] = {"society_id": sid_, "society": s.name, "code": s.code, "city": s.city, "status": s.status, "units": unit_counts.get(sid_, 0),
                     "residents_joined": 0, "bookings": 0, "units_booked": 0, "booking_revenue": 0.0, "maintenance_revenue": 0.0, "refunded": 0.0,
                     "billed": 0.0, "collected": 0.0, "complaints": 0, "complaints_resolved": 0, "jobs": 0, "deals": 0}
    for r in residents:
        if r.society_id in per:
            per[r.society_id]["residents_joined"] += 1
    for b in bookings:
        if b.society_id in per:
            per[b.society_id]["bookings"] += 1
            per[b.society_id]["units_booked"] += b.units_count if b.status in ("confirmed", "completed", "pending_payment") else 0
    for p in payments:
        if p.society_id in per:
            per[p.society_id]["booking_revenue" if p.ref_type == "booking" else "maintenance_revenue"] += money(p)
    for r in money_out:
        if r.society_id in per:
            per[r.society_id]["refunded"] += float(r.amount)
    for b in bills:
        if b.society_id in per:
            per[b.society_id]["billed"] += float(b.amount) + float(b.late_fee or 0)
            per[b.society_id]["collected"] += float(b.paid_amount or 0)
    for c in complaints:
        if c.society_id in per:
            per[c.society_id]["complaints"] += 1
            per[c.society_id]["complaints_resolved"] += 1 if c.status in ("resolved", "closed") else 0
    for j in jobs:
        if j.society_id in per:
            per[j.society_id]["jobs"] += 1
    for d in deals:
        if d.society_id in per:
            per[d.society_id]["deals"] += 1
    society_rows = []
    for row in per.values():
        row["total_revenue"] = round(row["booking_revenue"] + row["maintenance_revenue"], 2)
        row["net_revenue"] = round(row["total_revenue"] - row["refunded"], 2)
        row["collection_rate"] = round(row["collected"] / row["billed"] * 100, 1) if row["billed"] else 0
        for k in ("booking_revenue", "maintenance_revenue", "refunded", "billed", "collected"):
            row[k] = round(row[k], 2)
        society_rows.append(row)
    society_rows.sort(key=lambda r: -r["total_revenue"])
    revenue_by_society = [{"name": r["society"], "booking": r["booking_revenue"], "maintenance": r["maintenance_revenue"], "total": r["total_revenue"]} for r in society_rows[:12]]

    # ---- the transaction list (also what the Excel / PDF list)
    rows = []
    for p in payments[:PAYMENT_ROWS_CAP]:
        res = residents_by_id.get(p.resident_id)
        what = ""
        if p.ref_type == "booking":
            b = bookings_by_id.get(p.ref_id)
            d = deals_by_id.get(b.deal_id) if b else None
            what = d.title if d else f"Booking #{p.ref_id}"
        else:
            what = f"Maintenance bill #{p.ref_id}"
        rows.append({"paid_at": p.paid_at.isoformat(), "txn_id": p.txn_id, "society": name_of(p.society_id), "resident": res.name if res else "",
                     "type": p.ref_type, "description": what, "mode": p.mode, "amount": money(p), "reference": p.gateway_txn_id or p.reference_no or ""})

    return {
        "filters": {"date_from": f.date_from.isoformat(), "date_to": f.date_to.isoformat(), "society_ids": f.society_ids,
                    "societies": [name_of(i) for i in f.society_ids] if f.society_ids else ["All societies"], "category": f.category,
                    "mode": f.mode, "group_by": f.group_by},
        "generated_at": now_ist().isoformat(timespec="seconds"),
        "kpis": kpis,
        "timeseries": timeseries,
        "revenue_by_mode": revenue_by_mode,
        "revenue_by_society": revenue_by_society,
        "booking_status": booking_status,
        "refund_status": refund_status,
        "deals_by_category": deals_by_category,
        "top_deals": top_deals,
        "maintenance_by_period": maintenance_by_period,
        "complaints_by_category": comp_cat,
        "complaints_by_status": comp_status,
        "complaints_by_priority": comp_priority,
        "jobs_by_status": job_status,
        "rating_distribution": rating_dist,
        "residents_by_type": res_type,
        "residents_by_status": res_status,
        "vendors": vendors,
        "societies": society_rows,
        "payments": rows,
        "payments_truncated": len(payments) > PAYMENT_ROWS_CAP,
        "payments_total": len(payments),
    }
