from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import ACTIVE_BOOKING_STATUSES, BookingStatus, DealStatus, NotificationCategory
from app.core.errors import bad_request, forbidden, not_found
from app.core.timeutils import now_ist
from app.modules.bookings.models import Booking
from app.modules.group_deals.models import GroupDeal
from app.modules.notifications.service import notify_society_residents

OPEN_DEAL_STATUSES = (DealStatus.PUBLISHED, DealStatus.FULL)


def is_booking_open(deal: GroupDeal) -> bool:
    return deal.status in OPEN_DEAL_STATUSES and now_ist() <= deal.cutoff_date


def deal_out(db: Session, deal: GroupDeal, schema) -> dict:
    waitlist = db.scalar(select(func.count()).select_from(Booking).where(
        Booking.deal_id == deal.id, Booking.status == BookingStatus.WAITLISTED)) or 0
    return {
        **schema.model_validate(deal).model_dump(),
        "available_units": max(deal.max_units - deal.confirmed_units, 0),
        "waitlist_count": waitlist,
        "is_booking_open": is_booking_open(deal),
    }


def get_deal_or_404(db: Session, deal_id: int, lock: bool = False) -> GroupDeal:
    stmt = select(GroupDeal).where(GroupDeal.id == deal_id)
    if lock:
        stmt = stmt.with_for_update()
    deal = db.scalar(stmt)
    if not deal:
        raise not_found("Deal")
    return deal


def can_view(user: CurrentUser, deal: GroupDeal) -> bool:
    if user.is_super_admin:
        return True
    if deal.society_id not in (None, user.society_id):
        return False
    if user.is_resident and deal.status == DealStatus.DRAFT:
        return False
    return True


def get_visible_deal(db: Session, user: CurrentUser, deal_id: int) -> GroupDeal:
    deal = get_deal_or_404(db, deal_id)
    if not can_view(user, deal):
        raise not_found("Deal")
    return deal


def get_editable_deal(db: Session, user: CurrentUser, deal_id: int) -> GroupDeal:
    deal = get_deal_or_404(db, deal_id)
    if user.is_society_admin and deal.society_id != user.society_id:
        raise not_found("Deal") if deal.society_id is not None else forbidden("Platform deals are managed by the Super Admin")
    return deal


def visible_deals_stmt(user: CurrentUser):
    stmt = select(GroupDeal)
    if not user.is_super_admin:
        stmt = stmt.where(or_(GroupDeal.society_id == user.society_id, GroupDeal.society_id.is_(None)))
    return stmt


def publish(db: Session, deal: GroupDeal) -> None:
    if deal.status != DealStatus.DRAFT:
        raise bad_request("Only draft deals can be published")
    if deal.cutoff_date < now_ist():
        raise bad_request("Cut-off date is already in the past")
    deal.status = DealStatus.PUBLISHED
    deal.published_at = now_ist()
    notify_society_residents(
        db, deal.society_id, NotificationCategory.DEAL, f"New group deal: {deal.title}",
        f"₹{deal.price_per_unit:g} per unit on {deal.service_date:%d %b}. Book before {deal.cutoff_date:%d %b %I:%M %p}.",
        ref_type="deal", ref_id=deal.id,
    )


def close(deal: GroupDeal) -> None:
    if deal.status not in OPEN_DEAL_STATUSES:
        raise bad_request("Only published deals can be closed")
    deal.status = DealStatus.CLOSED
    deal.closed_at = now_ist()


def complete(db: Session, deal: GroupDeal) -> None:
    if deal.status not in (*OPEN_DEAL_STATUSES, DealStatus.CLOSED):
        raise bad_request("Deal cannot be completed in its current status")
    active = db.scalar(select(func.count()).select_from(Booking).where(
        Booking.deal_id == deal.id, Booking.status.in_(ACTIVE_BOOKING_STATUSES)))
    if active:
        raise bad_request(f"{active} booking(s) are still active. Complete or cancel them first.")
    deal.status = DealStatus.COMPLETED


def close_expired_deals(db: Session) -> int:
    """Scheduler: after cut-off, deals are closed for booking."""
    deals = db.scalars(select(GroupDeal).where(GroupDeal.status.in_(OPEN_DEAL_STATUSES),
                                               GroupDeal.cutoff_date < now_ist())).all()
    for deal in deals:
        deal.status = DealStatus.CLOSED
        deal.closed_at = now_ist()
    return len(deals)
