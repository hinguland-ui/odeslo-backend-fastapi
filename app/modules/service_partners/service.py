from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import AccountStatus, AssigneeType, JobStatus, PayoutStatus
from app.core.errors import bad_request
from app.modules.service_jobs.models import ServiceJob
from app.modules.service_partners.models import PartnerSociety, ServicePartner


def partner_society_ids(db: Session, partner_id: int) -> list[int]:
    return list(db.scalars(select(PartnerSociety.society_id).where(PartnerSociety.partner_id == partner_id)).all())


def to_out(db: Session, partner: ServicePartner, schema) -> dict:
    return {**schema.model_validate(partner).model_dump(), "society_ids": partner_society_ids(db, partner.id)}


def get_usable_partner(db: Session, partner_id: int, society_id: int | None) -> ServicePartner:
    """A partner can be used for a society's deal / job only if verified, active and assigned to that society."""
    partner = db.get(ServicePartner, partner_id)
    if not partner or not partner.verified or partner.status != AccountStatus.ACTIVE:
        raise bad_request("Service partner not found, not verified or inactive")
    if society_id is not None and not db.get(PartnerSociety, (partner_id, society_id)):
        raise bad_request("Service partner is not assigned to this society")
    return partner


def earnings(db: Session, partner_id: int) -> dict:
    base = select(ServiceJob).where(ServiceJob.assigned_to_type == AssigneeType.PARTNER,
                                    ServiceJob.assigned_to_id == partner_id,
                                    ServiceJob.status == JobStatus.COMPLETED)
    sub = base.subquery()
    completed = db.scalar(select(func.count()).select_from(sub)) or 0
    total = db.scalar(select(func.coalesce(func.sum(sub.c.payout_amount), 0))) or 0
    paid = db.scalar(select(func.coalesce(func.sum(sub.c.payout_amount), 0))
                     .where(sub.c.payout_status == PayoutStatus.PAID)) or 0
    return {"completed_jobs": completed, "total_earned": float(total), "paid": float(paid),
            "pending": float(total) - float(paid)}


def update_rating(db: Session, partner_id: int, rating: int) -> None:
    partner = db.get(ServicePartner, partner_id)
    if not partner:
        return
    total = partner.rating_avg * partner.rating_count + rating
    partner.rating_count += 1
    partner.rating_avg = round(total / partner.rating_count, 2)


def my_societies(db: Session, partner: ServicePartner) -> list[dict]:
    """Societies assigned to the vendor + how many vendors serve each (counts only)."""
    from app.core.enums import DealStatus
    from app.modules.group_deals.models import GroupDeal
    from app.modules.societies.models import Society

    mine = set(partner.categories or [])
    links = db.execute(select(PartnerSociety, Society).join(Society, Society.id == PartnerSociety.society_id)
                       .where(PartnerSociety.partner_id == partner.id).order_by(Society.name)).all()
    result = []
    for link, society in links:
        others = db.scalars(select(ServicePartner).join(PartnerSociety, PartnerSociety.partner_id == ServicePartner.id)
                            .where(PartnerSociety.society_id == society.id, ServicePartner.verified.is_(True),
                                   ServicePartner.status == AccountStatus.ACTIVE)).all()
        by_category: dict[str, int] = {}
        for p in others:
            for c in p.categories or []:
                by_category[c] = by_category.get(c, 0) + 1
        open_deals = [d for d in db.scalars(select(GroupDeal).where(
            GroupDeal.society_id == society.id, GroupDeal.partner_id.is_(None),
            GroupDeal.status.in_((DealStatus.PUBLISHED, DealStatus.FULL, DealStatus.CLOSED)))).all()
            if d.category in mine]
        my_jobs = db.scalar(select(func.count()).select_from(ServiceJob).where(
            ServiceJob.society_id == society.id, ServiceJob.assigned_to_type == AssigneeType.PARTNER,
            ServiceJob.assigned_to_id == partner.id)) or 0
        result.append({
            "id": society.id, "name": society.name, "area": society.area, "city": society.city,
            "assigned_at": link.assigned_at,
            "vendors_total": len(others),
            "vendors_same_category": sum(1 for p in others if p.id != partner.id and mine & set(p.categories or [])),
            "vendors_by_category": by_category,
            "open_deals": len(open_deals),
            "my_jobs": my_jobs,
        })
    return result
