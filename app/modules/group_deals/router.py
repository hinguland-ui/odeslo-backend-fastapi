from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles, scoped_society_id
from app.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    AdminPermission,
    DealStatus,
    Feature,
    NotificationCategory,
    UserType,
)
from app.core.errors import bad_request
from app.modules.categories.models import ServiceCategoryRow
from app.modules.categories.service import validate_category
from app.core.files import IMAGE_TYPES, release_upload, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.timeutils import now_ist
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.bookings.models import Booking
from app.modules.bookings.router import _with_deal
from app.modules.bookings.schemas import BookingOut
from app.modules.bookings.service import cancel_booking, promote_waitlist, recalc_deal
from app.modules.group_deals import service
from app.modules.group_deals.models import DealApplication, GroupDeal
from app.modules.group_deals.schemas import (
    ApplicationAdminOut,
    ApplicationIn,
    ApplicationOut,
    DealAdminOut,
    DealCancel,
    DealCreate,
    DealOut,
    DealUpdate,
)
from app.modules.notifications.service import notify, notify_society_admins, notify_super_admins
from app.modules.service_partners.models import PartnerSociety, ServicePartner
from app.modules.service_partners.service import get_usable_partner
from app.modules.societies.service import ensure_feature_enabled, get_society_or_404

router = APIRouter(prefix="/group-deals", tags=["Group Deals"])

DealAdmins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.DEALS)
ActiveResident = require_roles(UserType.RESIDENT)
AnyDealViewer = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT)


@router.get("/categories")
def service_categories(db: Session = Depends(get_db)):
    rows = db.scalars(select(ServiceCategoryRow).where(ServiceCategoryRow.parent_id.is_(None), ServiceCategoryRow.active.is_(True))
                      .order_by(ServiceCategoryRow.sort_order, ServiceCategoryRow.id)).all()
    return [{"value": r.key, "label": r.name} for r in rows]


# ---------- Service partner (vendor) ----------
@router.get("/mine", response_model=list[DealAdminOut])
def partner_deals(status: str | None = None, user: CurrentUser = Depends(require_roles(UserType.PARTNER)),
                  db: Session = Depends(get_db)):
    """Deals awarded to the logged-in vendor (drafts are hidden)."""
    stmt = (select(GroupDeal).where(GroupDeal.partner_id == user.id, GroupDeal.status != DealStatus.DRAFT)
            .order_by(GroupDeal.service_date.desc()))
    if status:
        stmt = stmt.where(GroupDeal.status == status)
    return [service.deal_out(db, d, DealAdminOut) for d in db.scalars(stmt).all()]


@router.get("/opportunities", response_model=list[DealAdminOut])
def partner_opportunities(user: CurrentUser = Depends(require_roles(UserType.PARTNER)), db: Session = Depends(get_db)):
    """Not-yet-awarded deals the vendor could win: in a society assigned to the vendor (or platform-wide)
    and in one of the vendor's service categories. The society admin still decides who gets the deal."""
    society_ids = db.scalars(select(PartnerSociety.society_id).where(PartnerSociety.partner_id == user.id)).all()
    stmt = (select(GroupDeal)
            .where(GroupDeal.partner_id.is_(None),
                   GroupDeal.status.in_((DealStatus.PUBLISHED, DealStatus.FULL, DealStatus.CLOSED)),
                   or_(GroupDeal.society_id.in_(society_ids), GroupDeal.society_id.is_(None)))
            .order_by(GroupDeal.service_date))
    categories = set(user.entity.categories or [])
    return [service.deal_out(db, d, DealAdminOut) for d in db.scalars(stmt).all() if d.category in categories]


@router.get("/my-applications", response_model=list[ApplicationOut])
def my_applications(user: CurrentUser = Depends(require_roles(UserType.PARTNER)), db: Session = Depends(get_db)):
    return db.scalars(select(DealApplication).where(DealApplication.partner_id == user.id)
                      .order_by(DealApplication.id.desc())).all()


@router.post("/{deal_id}/apply", response_model=ApplicationOut, status_code=201)
def apply_to_deal(deal_id: int, data: ApplicationIn, user: CurrentUser = Depends(require_roles(UserType.PARTNER)),
                  db: Session = Depends(get_db)):
    """Vendor asks to serve this deal. The society admin (or super admin for platform deals) decides."""
    deal = service.get_deal_or_404(db, deal_id)
    partner: ServicePartner = user.entity
    if deal.partner_id is not None:
        raise bad_request("This deal has already been awarded to a vendor")
    if deal.status not in (DealStatus.PUBLISHED, DealStatus.FULL, DealStatus.CLOSED):
        raise bad_request("This deal is not open for vendors")
    if deal.category not in (partner.categories or []):
        raise bad_request("This deal is not in one of your service categories")
    if deal.society_id is not None and not db.get(PartnerSociety, (partner.id, deal.society_id)):
        raise bad_request("You are not assigned to this society")
    app = db.scalar(select(DealApplication).where(DealApplication.deal_id == deal.id,
                                                   DealApplication.partner_id == partner.id))
    if app and app.status in ("applied", "accepted"):
        raise bad_request("You have already applied to this deal")
    if app:  # re-apply after withdrawing / rejection
        app.status, app.quote_per_unit, app.message = "applied", data.quote_per_unit, data.message
    else:
        app = DealApplication(deal_id=deal.id, partner_id=partner.id, quote_per_unit=data.quote_per_unit,
                              message=data.message, status="applied")
        db.add(app)
    db.flush()
    who = partner.business_name or partner.name
    title, body = "Vendor applied to a deal", f"{who} applied to serve '{deal.title}'."
    if deal.society_id:
        notify_society_admins(db, deal.society_id, NotificationCategory.DEAL, title, body,
                              ref_type="group_deal", ref_id=deal.id)
    else:
        notify_super_admins(db, NotificationCategory.DEAL, title, body, ref_type="group_deal", ref_id=deal.id)
    log_activity(db, user, "deal.vendor_apply", "group_deal", deal.id, new={"quote": data.quote_per_unit},
                 society_id=deal.society_id)
    db.commit()
    return app


@router.post("/{deal_id}/withdraw", response_model=ApplicationOut)
def withdraw_application(deal_id: int, user: CurrentUser = Depends(require_roles(UserType.PARTNER)),
                         db: Session = Depends(get_db)):
    app = db.scalar(select(DealApplication).where(DealApplication.deal_id == deal_id,
                                                   DealApplication.partner_id == user.id))
    if not app or app.status != "applied":
        raise bad_request("No pending application to withdraw")
    app.status = "withdrawn"
    log_activity(db, user, "deal.vendor_withdraw", "group_deal", deal_id)
    db.commit()
    return app


# ---------- Resident ----------
@router.get("/available", response_model=list[DealOut])
def available_deals(category: str | None = None, user: CurrentUser = Depends(ActiveResident),
                    db: Session = Depends(get_db)):
    """Services screen: open deals for the resident's society + platform-level deals."""
    ensure_feature_enabled(db, user.society_id, Feature.GROUP_DEALS)
    stmt = (service.visible_deals_stmt(user)
            .where(GroupDeal.status.in_(service.OPEN_DEAL_STATUSES), GroupDeal.cutoff_date >= now_ist())
            .order_by(GroupDeal.service_date))
    if category:
        stmt = stmt.where(GroupDeal.category == category)
    return [service.deal_out(db, d, DealOut) for d in db.scalars(stmt).all()]


# ---------- Admin ----------
@router.post("", response_model=DealAdminOut, status_code=201)
def create_deal(data: DealCreate, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    society_id = user.society_id if user.is_society_admin else data.society_id
    if society_id is not None:
        get_society_or_404(db, society_id)
        ensure_feature_enabled(db, society_id, Feature.GROUP_DEALS)
    validate_category(db, data.category, data.subcategory)
    if data.partner_id:
        get_usable_partner(db, data.partner_id, society_id)
    deal = GroupDeal(**data.model_dump(exclude={"society_id"}), society_id=society_id, status=DealStatus.DRAFT,
                     created_by_type=user.user_type, created_by_id=user.id)
    db.add(deal)
    db.flush()
    log_activity(db, user, "deal.create", "group_deal", deal.id, new=snapshot(deal), society_id=society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.get("", response_model=Page[DealAdminOut])
def list_deals(society_id: int | None = None, status: str | None = None, category: str | None = None,
               include_platform: bool = True, params: PageParams = Depends(),
               user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    stmt = select(GroupDeal).order_by(GroupDeal.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        cond = GroupDeal.society_id == sid
        stmt = stmt.where(or_(cond, GroupDeal.society_id.is_(None)) if include_platform else cond)
    if status:
        stmt = stmt.where(GroupDeal.status == status)
    if category:
        stmt = stmt.where(GroupDeal.category == category)
    page = paginate(db, stmt, params)
    page["items"] = [service.deal_out(db, d, DealAdminOut) for d in page["items"]]
    return page


@router.get("/{deal_id}", response_model=DealOut)
def get_deal(deal_id: int, user: CurrentUser = Depends(AnyDealViewer), db: Session = Depends(get_db)):
    deal = service.get_visible_deal(db, user, deal_id)
    return service.deal_out(db, deal, DealAdminOut if user.is_admin else DealOut)


def _award_applications(db: Session, deal: GroupDeal, values: dict) -> None:
    """Winner's application -> accepted (its quote becomes the payout if none was set); others -> rejected."""
    apps = db.scalars(select(DealApplication).where(DealApplication.deal_id == deal.id,
                                                      DealApplication.status == "applied")).all()
    for app in apps:
        if app.partner_id == deal.partner_id:
            app.status = "accepted"
            if app.quote_per_unit is not None and values.get("partner_payout_per_unit") is None \
                    and deal.partner_payout_per_unit is None:
                deal.partner_payout_per_unit = app.quote_per_unit
        else:
            app.status = "rejected"
            notify(db, UserType.PARTNER, app.partner_id, NotificationCategory.DEAL, "Deal not awarded to you",
                   f"'{deal.title}' was awarded to another vendor.", ref_type="group_deal", ref_id=deal.id)
    notify(db, UserType.PARTNER, deal.partner_id, NotificationCategory.DEAL, "You got a new deal",
           f"'{deal.title}' has been awarded to you.", ref_type="group_deal", ref_id=deal.id)


@router.patch("/{deal_id}", response_model=DealAdminOut)
def update_deal(deal_id: int, data: DealUpdate, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    deal = service.get_editable_deal(db, user, deal_id)
    if deal.status not in (DealStatus.DRAFT, *service.OPEN_DEAL_STATUSES):
        raise bad_request("Only draft or published deals can be edited")
    values = data.model_dump(exclude_unset=True)
    if deal.status != DealStatus.DRAFT and {"price_per_unit", "category"} & values.keys():
        raise bad_request("Price and category cannot be changed after publishing")
    if "category" in values or "subcategory" in values:
        cat = values.get("category", deal.category)
        sub = values.get("subcategory") if "category" in values else values.get("subcategory", deal.subcategory)
        validate_category(db, cat, sub)
    if "max_units" in values and values["max_units"] < deal.confirmed_units:
        raise bad_request(f"Max units cannot be lower than already confirmed units ({deal.confirmed_units})")
    if values.get("partner_id"):
        get_usable_partner(db, values["partner_id"], deal.society_id)
    before = snapshot(deal)
    newly_awarded = bool(values.get("partner_id")) and values["partner_id"] != deal.partner_id
    for k, v in values.items():
        setattr(deal, k, v)
    if newly_awarded:
        _award_applications(db, deal, values)
    if deal.cutoff_date.date() > deal.service_date:
        raise bad_request("Booking cut-off must be on or before the service date")
    recalc_deal(db, deal)
    if "max_units" in values and deal.auto_promote_waitlist:
        promote_waitlist(db, deal, user)  # increased capacity frees slots for the waitlist
    old, new = diff(before, snapshot(deal))
    log_activity(db, user, "deal.update", "group_deal", deal.id, old, new, society_id=deal.society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.post("/{deal_id}/image", response_model=DealAdminOut)
def upload_deal_image(deal_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(DealAdmins),
                      db: Session = Depends(get_db)):
    deal = service.get_editable_deal(db, user, deal_id)
    old_image = deal.image
    deal.image = save_upload(file, "deals", IMAGE_TYPES, db=db, user=user, audience="society", society_id=deal.society_id)
    release_upload(db, old_image)
    log_activity(db, user, "deal.upload_image", "group_deal", deal.id, society_id=deal.society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.post("/{deal_id}/publish", response_model=DealAdminOut)
def publish_deal(deal_id: int, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    deal = service.get_editable_deal(db, user, deal_id)
    service.publish(db, deal)
    log_activity(db, user, "deal.publish", "group_deal", deal.id, {"status": DealStatus.DRAFT},
                 {"status": deal.status}, society_id=deal.society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.post("/{deal_id}/close", response_model=DealAdminOut)
def close_deal(deal_id: int, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    deal = service.get_editable_deal(db, user, deal_id)
    old = deal.status
    service.close(deal)
    log_activity(db, user, "deal.close", "group_deal", deal.id, {"status": old}, {"status": deal.status},
                 society_id=deal.society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.post("/{deal_id}/complete", response_model=DealAdminOut)
def complete_deal(deal_id: int, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    deal = service.get_editable_deal(db, user, deal_id)
    old = deal.status
    service.complete(db, deal)
    log_activity(db, user, "deal.complete", "group_deal", deal.id, {"status": old}, {"status": deal.status},
                 society_id=deal.society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.post("/{deal_id}/cancel", response_model=DealAdminOut)
def cancel_deal(deal_id: int, data: DealCancel, user: CurrentUser = Depends(DealAdmins),
                db: Session = Depends(get_db)):
    """Cancel the whole deal: every active booking is cancelled and paid ones get a refund request."""
    deal = service.get_editable_deal(db, user, deal_id)
    if deal.status in (DealStatus.COMPLETED, DealStatus.CANCELLED):
        raise bad_request(f"Deal is already {deal.status}")
    old = deal.status
    deal.status = DealStatus.CANCELLED
    deal.cancel_reason = data.reason
    bookings = db.scalars(select(Booking).where(Booking.deal_id == deal.id,
                                                Booking.status.in_(ACTIVE_BOOKING_STATUSES))).all()
    for booking in bookings:
        cancel_booking(db, booking, user, f"Deal cancelled: {data.reason}", promote=False, notify_admins=False)
    log_activity(db, user, "deal.cancel", "group_deal", deal.id, {"status": old},
                 {"status": deal.status, "reason": data.reason, "bookings_cancelled": len(bookings)},
                 society_id=deal.society_id)
    db.commit()
    return service.deal_out(db, deal, DealAdminOut)


@router.get("/{deal_id}/bookings", response_model=list[BookingOut])
def deal_bookings(deal_id: int, status: str | None = None, user: CurrentUser = Depends(DealAdmins),
                  db: Session = Depends(get_db)):
    deal = service.get_visible_deal(db, user, deal_id)
    stmt = select(Booking).where(Booking.deal_id == deal.id).order_by(Booking.created_at)
    if user.is_society_admin:
        stmt = stmt.where(Booking.society_id == user.society_id)
    if status:
        stmt = stmt.where(Booking.status == status)
    return _with_deal(db, db.scalars(stmt).all())


@router.get("/{deal_id}/applications", response_model=list[ApplicationAdminOut])
def deal_applications(deal_id: int, user: CurrentUser = Depends(DealAdmins), db: Session = Depends(get_db)):
    """Vendors who applied for this deal (newest first), for the award screen."""
    deal = service.get_visible_deal(db, user, deal_id)
    rows = db.execute(select(DealApplication, ServicePartner)
                      .join(ServicePartner, ServicePartner.id == DealApplication.partner_id)
                      .where(DealApplication.deal_id == deal.id, DealApplication.status != "withdrawn")
                      .order_by(DealApplication.id.desc())).all()
    return [{**ApplicationOut.model_validate(a).model_dump(), "partner_name": p.name,
             "business_name": p.business_name, "rating_avg": p.rating_avg, "rating_count": p.rating_count}
            for a, p in rows]
