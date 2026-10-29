from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, get_registration_mobile, require_roles
from app.core.enums import AccountStatus, NotificationCategory, UserType
from app.modules.categories.service import validate_categories
from app.core.errors import bad_request, conflict, not_found
from app.core.files import DOCUMENT_TYPES, IMAGE_TYPES, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.timeutils import now_ist
from app.core.utils import diff, normalize_mobile, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.notifications.service import notify_super_admins
from app.modules.onboarding.router import clear_draft
from app.modules.service_partners import service
from app.modules.service_partners.models import PartnerSociety, ServicePartner
from app.modules.service_partners.schemas import (
    EarningsOut,
    PartnerBriefOut,
    PartnerOut,
    PartnerPrivateOut,
    PartnerSelfUpdate,
    PartnerSocietyOut,
    PartnerSocietiesUpdate,
    PartnerUpdate,
)
from app.modules.societies.models import Society
from app.modules.staff.models import Staff

router = APIRouter(prefix="/service-partners", tags=["Service Partners"])
SuperAdminOnly = require_roles(UserType.SUPER_ADMIN)
PartnerOnly = require_roles(UserType.PARTNER)


def _get(db: Session, partner_id: int) -> ServicePartner:
    partner = db.get(ServicePartner, partner_id)
    if not partner:
        raise not_found("Service partner")
    return partner


# ---------- Partner self ----------
@router.get("/me", response_model=PartnerPrivateOut)
def my_profile(user: CurrentUser = Depends(PartnerOnly), db: Session = Depends(get_db)):
    return service.to_out(db, user.entity, PartnerPrivateOut)


@router.patch("/me", response_model=PartnerPrivateOut)
def update_my_profile(data: PartnerSelfUpdate, user: CurrentUser = Depends(PartnerOnly),
                      db: Session = Depends(get_db)):
    partner: ServicePartner = user.entity
    values = data.model_dump(exclude_unset=True)
    if "bank_details" in values and values["bank_details"] is not None:
        values["bank_details"] = data.bank_details.model_dump()
    for k, v in values.items():
        setattr(partner, k, v)
    log_activity(db, user, "partner.update_profile", "service_partner", partner.id,
                 new={k: v for k, v in values.items() if k != "bank_details"})
    db.commit()
    return service.to_out(db, partner, PartnerPrivateOut)


@router.get("/me/earnings", response_model=EarningsOut)
def my_earnings(user: CurrentUser = Depends(PartnerOnly), db: Session = Depends(get_db)):
    return service.earnings(db, user.id)


@router.get("/me/societies", response_model=list[PartnerSocietyOut])
def my_societies(user: CurrentUser = Depends(PartnerOnly), db: Session = Depends(get_db)):
    """Societies assigned to this vendor, with vendor counts (overall / same category / per category)."""
    return service.my_societies(db, user.entity)


# ---------- Society admin: partners available for their society ----------
@router.get("/available", response_model=list[PartnerBriefOut])
def available_partners(category: str | None = None,
                       user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN)),
                       society_id: int | None = None, db: Session = Depends(get_db)):
    sid = user.society_id if user.is_society_admin else society_id
    stmt = select(ServicePartner).where(ServicePartner.verified.is_(True),
                                        ServicePartner.status == AccountStatus.ACTIVE)
    if sid is not None:
        stmt = stmt.join(PartnerSociety, PartnerSociety.partner_id == ServicePartner.id).where(
            PartnerSociety.society_id == sid)
    partners = db.scalars(stmt.order_by(ServicePartner.rating_avg.desc())).all()
    if category:
        partners = [p for p in partners if category in (p.categories or [])]
    return partners


# ---------- Self registration (after OTP verify returned a registration_token) ----------
@router.post("/register", status_code=201)
def register_partner(
    name: str = Form(..., min_length=2, max_length=120),
    categories: list[str] = Form(...),
    email: str | None = Form(None),
    business_name: str | None = Form(None),
    gst: str | None = Form(None),
    id_proof: UploadFile = File(...),
    photo: UploadFile = File(...),
    mobile: str = Depends(get_registration_mobile),
    db: Session = Depends(get_db),
):
    """A vendor signs up. The account stays unverified (no login, no jobs) until a super admin verifies it."""
    if db.scalar(select(ServicePartner.id).where(ServicePartner.mobile == mobile)) or \
            db.scalar(select(Staff.id).where(Staff.mobile == mobile)):
        raise conflict("This mobile is already registered as staff or partner")
    partner = ServicePartner(
        name=name, mobile=mobile, email=email, business_name=business_name, gst=gst,
        categories=validate_categories(db, categories),
        id_proof=save_upload(id_proof, "partners/id_proofs", DOCUMENT_TYPES, db=db, audience="platform_admin", category="partners_documents"),
        photo=save_upload(photo, "partners/photos", IMAGE_TYPES, db=db, audience="platform_admin", category="partners_photos"),
    )
    db.add(partner)
    db.flush()
    clear_draft(db, mobile, "partner")
    log_activity(db, None, "partner.register", "service_partner", partner.id, new=snapshot(partner))
    notify_super_admins(db, NotificationCategory.REGISTRATION, "New vendor registration",
                        f"{business_name or name} ({mobile}) registered and is waiting for verification.")
    db.commit()
    return {"message": "Registration received. You can sign in once the Odeslo team verifies your account.",
            "verified": False}


# ---------- Super admin management ----------
@router.post("", response_model=PartnerOut, status_code=201)
def add_partner(
    name: str = Form(..., min_length=2, max_length=120),
    mobile: str = Form(...),
    categories: list[str] = Form(...),
    email: str | None = Form(None),
    business_name: str | None = Form(None),
    gst: str | None = Form(None),
    id_proof: UploadFile = File(...),
    photo: UploadFile = File(...),
    user: CurrentUser = Depends(SuperAdminOnly),
    db: Session = Depends(get_db),
):
    try:
        mobile = normalize_mobile(mobile)
    except ValueError as exc:
        raise bad_request(str(exc))
    if db.scalar(select(ServicePartner.id).where(ServicePartner.mobile == mobile)) or \
            db.scalar(select(Staff.id).where(Staff.mobile == mobile)):
        raise conflict("This mobile is already registered as staff or partner")
    partner = ServicePartner(
        name=name, mobile=mobile, email=email, business_name=business_name, gst=gst,
        categories=validate_categories(db, categories),
        id_proof=save_upload(id_proof, "partners/id_proofs", DOCUMENT_TYPES, db=db, user=user, audience="platform_admin", category="partners_documents"),
        photo=save_upload(photo, "partners/photos", IMAGE_TYPES, db=db, user=user, audience="platform_admin", category="partners_photos"),
    )
    db.add(partner)
    db.flush()
    log_activity(db, user, "partner.create", "service_partner", partner.id, new=snapshot(partner))
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.get("", response_model=Page[PartnerOut])
def list_partners(verified: bool | None = None, category: str | None = None, society_id: int | None = None,
                  status: str | None = None, params: PageParams = Depends(),
                  user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    stmt = select(ServicePartner).order_by(ServicePartner.id.desc())
    if verified is not None:
        stmt = stmt.where(ServicePartner.verified.is_(verified))
    if status:
        stmt = stmt.where(ServicePartner.status == status)
    if society_id:
        stmt = stmt.join(PartnerSociety, PartnerSociety.partner_id == ServicePartner.id).where(
            PartnerSociety.society_id == society_id)
    page = paginate(db, stmt, params)
    items = page["items"]
    if category:
        items = [p for p in items if category in (p.categories or [])]
    page["items"] = [service.to_out(db, p, PartnerOut) for p in items]
    return page


@router.get("/{partner_id}/applications")
def partner_applications(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    """Every deal this vendor applied to (newest first) with the outcome. Super admin only."""
    from app.modules.group_deals.models import DealApplication, GroupDeal

    _get(db, partner_id)
    rows = db.execute(select(DealApplication, GroupDeal).join(GroupDeal, GroupDeal.id == DealApplication.deal_id)
                      .where(DealApplication.partner_id == partner_id).order_by(DealApplication.id.desc())).all()
    return [{"id": a.id, "deal_id": d.id, "deal_title": d.title, "society_id": d.society_id, "category": d.category,
             "quote_per_unit": a.quote_per_unit, "message": a.message, "status": a.status,
             "created_at": a.created_at} for a, d in rows]


@router.get("/{partner_id}", response_model=PartnerPrivateOut)
def get_partner(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    return service.to_out(db, _get(db, partner_id), PartnerPrivateOut)


@router.patch("/{partner_id}", response_model=PartnerOut)
def update_partner(partner_id: int, data: PartnerUpdate, user: CurrentUser = Depends(SuperAdminOnly),
                   db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    before = snapshot(partner)
    values = data.model_dump(exclude_unset=True)
    if "categories" in values:
        values["categories"] = validate_categories(db, data.categories or [])
    for k, v in values.items():
        setattr(partner, k, v)
    db.flush()
    old, new = diff(before, snapshot(partner))
    log_activity(db, user, "partner.update", "service_partner", partner.id, old, new)
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.post("/{partner_id}/verify", response_model=PartnerOut)
def verify_partner(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    """Verify documents (ID proof + photo) so the partner can log in and be assigned jobs."""
    partner = _get(db, partner_id)
    partner.verified, partner.verified_at, partner.verified_by = True, now_ist(), user.id
    log_activity(db, user, "partner.verify", "service_partner", partner.id, new={"verified": True})
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.post("/{partner_id}/unverify", response_model=PartnerOut)
def unverify_partner(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    partner.verified = False
    log_activity(db, user, "partner.unverify", "service_partner", partner.id, new={"verified": False})
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.put("/{partner_id}/societies", response_model=PartnerOut)
def assign_societies(partner_id: int, data: PartnerSocietiesUpdate, user: CurrentUser = Depends(SuperAdminOnly),
                     db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    ids = sorted(set(data.society_ids))
    found = set(db.scalars(select(Society.id).where(Society.id.in_(ids))).all()) if ids else set()
    if missing := set(ids) - found:
        raise bad_request(f"Unknown societies: {sorted(missing)}")
    old = service.partner_society_ids(db, partner.id)
    db.execute(delete(PartnerSociety).where(PartnerSociety.partner_id == partner.id))
    for sid in ids:
        db.add(PartnerSociety(partner_id=partner.id, society_id=sid))
    log_activity(db, user, "partner.assign_societies", "service_partner", partner.id, {"society_ids": old},
                 {"society_ids": ids})
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.post("/{partner_id}/deactivate", response_model=PartnerOut)
def deactivate_partner(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    partner.status = AccountStatus.INACTIVE
    log_activity(db, user, "partner.deactivate", "service_partner", partner.id, new={"status": partner.status})
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.post("/{partner_id}/activate", response_model=PartnerOut)
def activate_partner(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    partner = _get(db, partner_id)
    partner.status = AccountStatus.ACTIVE
    log_activity(db, user, "partner.activate", "service_partner", partner.id, new={"status": partner.status})
    db.commit()
    return service.to_out(db, partner, PartnerOut)


@router.get("/{partner_id}/earnings", response_model=EarningsOut)
def partner_earnings(partner_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    _get(db, partner_id)
    return service.earnings(db, partner_id)
