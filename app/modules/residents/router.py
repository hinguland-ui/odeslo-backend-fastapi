import csv
import io

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import (
    CurrentUser,
    ensure_society_access,
    get_registration_mobile,
    require_roles,
    scoped_society_id,
)
from app.core.enums import AdminPermission, DocumentType, ResidentStatus, ResidentType, UserType
from app.core.errors import bad_request, forbidden, not_found
from app.core.files import DOCUMENT_TYPES, IMAGE_TYPES, release_upload, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.security import create_access_token
from app.core.timeutils import now_ist
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.auth.schemas import AuthResponse
from app.modules.onboarding.router import clear_draft
from app.modules.residents import service
from app.modules.residents.models import FamilyMember, Resident, ResidentDocument, Vehicle
from app.modules.residents.schemas import (
    DocumentOut,
    FamilyMemberCreate,
    FamilyMemberOut,
    HomeOut,
    LocationUpdate,
    RejectRequest,
    ResidentAddHome,
    ResidentAdminUpdate,
    ResidentDetailOut,
    ResidentOut,
    ResidentProfileUpdate,
    ResidentRegister,
    ResidentSettings,
    ResidentSettingsUpdate,
    TenantCreate,
    TenantVisibilityUpdate,
    VehicleCreate,
    VehicleOut,
)
from app.modules.societies.models import Unit, Wing

router = APIRouter(prefix="/residents", tags=["Residents"])

# Residents waiting for approval can still use their own profile (and the Masjid feature)
AnyResident = require_roles(UserType.RESIDENT, allow_limited_resident=True)
ActiveResident = require_roles(UserType.RESIDENT)
ResidentManagers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.RESIDENTS)


def _get_resident(db: Session, user: CurrentUser, resident_id: int) -> Resident:
    resident = db.get(Resident, resident_id)
    if not resident:
        raise not_found("Resident")
    ensure_society_access(user, resident.society_id, "Resident")
    return resident


# =====================================================================
# Registration
# =====================================================================
@router.post("/register", response_model=AuthResponse, status_code=201)
def register(data: ResidentRegister, mobile: str = Depends(get_registration_mobile), db: Session = Depends(get_db)):
    """Step 2 of resident onboarding (after OTP verify returned a registration_token)."""
    resident = service.register_resident(db, mobile, data)
    clear_draft(db, mobile, "resident")
    log_activity(db, None, "resident.register", "resident", resident.id, new=snapshot(resident),
                 society_id=resident.society_id)
    db.commit()
    return {"access_token": create_access_token(UserType.RESIDENT, resident.id), "user_type": UserType.RESIDENT,
            "user_id": resident.id, "status": resident.status}


# =====================================================================
# Resident self-service (/me)
# =====================================================================
@router.get("/me", response_model=ResidentDetailOut)
def my_profile(user: CurrentUser = Depends(AnyResident), db: Session = Depends(get_db)):
    return service.resident_detail(db, user.entity)


@router.patch("/me", response_model=ResidentOut)
def update_my_profile(data: ResidentProfileUpdate, user: CurrentUser = Depends(AnyResident),
                      db: Session = Depends(get_db)):
    resident: Resident = user.entity
    before = snapshot(resident)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(resident, k, v)
    db.flush()
    old, new = diff(before, snapshot(resident))
    log_activity(db, user, "resident.update_profile", "resident", resident.id, old, new)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/me/photo", response_model=ResidentOut)
def upload_my_photo(file: UploadFile = File(...), user: CurrentUser = Depends(AnyResident),
                    db: Session = Depends(get_db)):
    resident: Resident = user.entity
    old_photo = resident.photo
    resident.photo = save_upload(file, "residents/photos", IMAGE_TYPES, db=db, user=user, audience="owner_admin",
                                 society_id=resident.society_id, unit_id=resident.unit_id, category="residents_photos")
    release_upload(db, old_photo)
    log_activity(db, user, "resident.upload_photo", "resident", resident.id)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/me/documents", response_model=DocumentOut, status_code=201)
def upload_my_document(doc_type: DocumentType = Form(...), file: UploadFile = File(...),
                       resident_id: int | None = Form(None), user: CurrentUser = Depends(AnyResident),
                       db: Session = Depends(get_db)):
    """Ownership / rent proof: Index II, sale deed, rent agreement, electricity bill...

    resident_id: attach it to another home of the same mobile (e.g. the flat just added in another society).
    """
    target_id = user.id
    if resident_id and resident_id != user.id:
        other = db.get(Resident, resident_id)
        if not other or other.mobile != user.mobile:
            raise not_found("Home")
        target_id = other.id
    holder = db.get(Resident, target_id)  # the home the proof belongs to (may be in another society)
    doc = ResidentDocument(resident_id=target_id, doc_type=doc_type,
                           file_path=save_upload(file, "residents/documents", DOCUMENT_TYPES, db=db, user=user,
                                                 audience="owner_admin", society_id=holder.society_id,
                                                 unit_id=holder.unit_id, category="residents_documents"))
    db.add(doc)
    db.flush()
    log_activity(db, user, "resident.upload_document", "resident_document", doc.id, new={"doc_type": doc_type})
    db.commit()
    return doc


# ---------- Homes: the same person in more than one flat / society ----------
@router.get("/me/homes", response_model=list[HomeOut])
def my_homes(user: CurrentUser = Depends(AnyResident), db: Session = Depends(get_db)):
    return [{**h, "current": h["id"] == user.id} for h in service.list_homes(db, user.mobile)]


@router.post("/me/switch/{resident_id}", response_model=AuthResponse)
def switch_home(resident_id: int, user: CurrentUser = Depends(AnyResident), db: Session = Depends(get_db)):
    """Continue as the same person in another home. Every screen then shows that society's data."""
    home = db.get(Resident, resident_id)
    if not home or home.mobile != user.mobile or home.status == ResidentStatus.DEACTIVATED:
        raise not_found("Home")
    home.last_login_at = now_ist()
    log_activity(db, user, "resident.switch_home", "resident", home.id, society_id=home.society_id)
    db.commit()
    return {"access_token": create_access_token(UserType.RESIDENT, home.id), "user_type": UserType.RESIDENT,
            "user_id": home.id, "status": home.status}


@router.post("/me/homes", response_model=ResidentOut, status_code=201)
def add_home(data: ResidentAddHome, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    """Request another home (another flat or another society). Its own society admin has to approve it."""
    me: Resident = user.entity
    merged = ResidentRegister(
        society_id=data.society_id, society_code=data.society_code, wing_id=data.wing_id, unit_id=data.unit_id,
        resident_type=data.resident_type, name=data.name or me.name, email=data.email or me.email,
        emergency_contact_name=data.emergency_contact_name or me.emergency_contact_name,
        emergency_contact_mobile=data.emergency_contact_mobile or me.emergency_contact_mobile)
    home = service.register_resident(db, me.mobile, merged, add_home=True)
    log_activity(db, user, "resident.add_home", "resident", home.id, new=snapshot(home), society_id=home.society_id)
    db.commit()
    return service.with_unit_labels(db, [home])[0]


@router.get("/me/settings", response_model=ResidentSettings)
def my_settings(user: CurrentUser = Depends(AnyResident)):
    return user.entity


@router.patch("/me/settings", response_model=ResidentSettings)
def update_my_settings(data: ResidentSettingsUpdate, user: CurrentUser = Depends(AnyResident),
                       db: Session = Depends(get_db)):
    resident: Resident = user.entity
    values = data.model_dump(exclude_unset=True)
    if "notification_prefs" in values:
        values["notification_prefs"] = {**(resident.notification_prefs or {}), **values["notification_prefs"]}
    for k, v in values.items():
        setattr(resident, k, v)
    log_activity(db, user, "resident.update_settings", "resident", resident.id, new=values)
    db.commit()
    return resident


@router.post("/me/location", response_model=ResidentSettings)
def update_my_location(data: LocationUpdate, user: CurrentUser = Depends(AnyResident), db: Session = Depends(get_db)):
    """Last known location (used for nearest-masjid Namaz alerts)."""
    resident: Resident = user.entity
    resident.last_lat, resident.last_lng, resident.location_permission = data.lat, data.lng, True
    db.commit()
    return resident


# ---------- Family members ----------
@router.get("/me/family", response_model=list[FamilyMemberOut])
def my_family(user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    owner_id = user.entity.parent_resident_id or user.id  # family members see the household list
    return db.scalars(select(FamilyMember).where(FamilyMember.resident_id == owner_id)).all()


@router.post("/me/family", response_model=FamilyMemberOut, status_code=201)
def add_family_member(data: FamilyMemberCreate, user: CurrentUser = Depends(ActiveResident),
                      db: Session = Depends(get_db)):
    resident: Resident = user.entity
    if resident.resident_type == ResidentType.FAMILY:
        raise forbidden("Family members cannot add members")
    linked_id = None
    if data.grant_app_access:
        child = service.create_linked_resident(db, resident, data.name, data.mobile, None, ResidentType.FAMILY)
        linked_id = child.id
    member = FamilyMember(resident_id=resident.id, name=data.name, relation=data.relation, mobile=data.mobile,
                          linked_resident_id=linked_id)
    db.add(member)
    db.flush()
    log_activity(db, user, "resident.add_family_member", "family_member", member.id, new=snapshot(member))
    db.commit()
    return member


@router.delete("/me/family/{member_id}", status_code=204)
def remove_family_member(member_id: int, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    if user.resident_type != ResidentType.OWNER:
        raise forbidden("Only the owner can remove members")
    member = db.scalar(select(FamilyMember).where(FamilyMember.id == member_id, FamilyMember.resident_id == user.id))
    if not member:
        raise not_found("Family member")
    if member.linked_resident_id:
        linked = db.get(Resident, member.linked_resident_id)
        if linked:
            service.deactivate_resident(db, linked)
    db.delete(member)
    log_activity(db, user, "resident.remove_family_member", "family_member", member_id, old=snapshot(member))
    db.commit()


# ---------- Tenant (owner only) ----------
@router.post("/me/tenant", response_model=ResidentOut, status_code=201)
def add_tenant(data: TenantCreate, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    if user.resident_type != ResidentType.OWNER:
        raise forbidden("Only the owner can add a tenant")
    tenant = service.create_linked_resident(db, user.entity, data.name, data.mobile, data.email,
                                            ResidentType.TENANT, data.maintenance_visible)
    log_activity(db, user, "resident.add_tenant", "resident", tenant.id, new=snapshot(tenant))
    db.commit()
    return service.with_unit_labels(db, [tenant])[0]


@router.get("/me/linked", response_model=list[ResidentOut])
def my_linked_accounts(user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    """Tenant / family logins created by this owner."""
    linked = db.scalars(select(Resident).where(Resident.parent_resident_id == user.id)).all()
    return service.with_unit_labels(db, linked)


@router.patch("/me/tenant/{tenant_id}/maintenance-visibility", response_model=ResidentOut)
def set_tenant_maintenance_visibility(tenant_id: int, data: TenantVisibilityUpdate,
                                      user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    tenant = db.scalar(select(Resident).where(Resident.id == tenant_id, Resident.parent_resident_id == user.id,
                                              Resident.resident_type == ResidentType.TENANT))
    if not tenant:
        raise not_found("Tenant")
    tenant.maintenance_visible = data.maintenance_visible
    log_activity(db, user, "resident.tenant_maintenance_visibility", "resident", tenant.id,
                 new={"maintenance_visible": data.maintenance_visible})
    db.commit()
    return service.with_unit_labels(db, [tenant])[0]


@router.post("/me/linked/{resident_id}/deactivate", response_model=ResidentOut)
def deactivate_linked(resident_id: int, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    if user.resident_type != ResidentType.OWNER:
        raise forbidden("Only the owner can remove members")
    linked = db.scalar(select(Resident).where(Resident.id == resident_id, Resident.parent_resident_id == user.id))
    if not linked:
        raise not_found("Resident")
    service.deactivate_resident(db, linked)
    log_activity(db, user, "resident.deactivate_linked", "resident", linked.id, new={"status": linked.status})
    db.commit()
    return service.with_unit_labels(db, [linked])[0]


# ---------- Vehicles ----------
@router.get("/me/vehicles", response_model=list[VehicleOut])
def my_vehicles(user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    return db.scalars(select(Vehicle).where(Vehicle.resident_id == user.id)).all()


@router.post("/me/vehicles", response_model=VehicleOut, status_code=201)
def add_vehicle(data: VehicleCreate, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    vehicle = Vehicle(resident_id=user.id, vehicle_type=data.vehicle_type,
                      vehicle_number=data.vehicle_number.upper().replace(" ", ""), parking_slot=data.parking_slot)
    db.add(vehicle)
    db.flush()
    log_activity(db, user, "resident.add_vehicle", "vehicle", vehicle.id, new=snapshot(vehicle))
    db.commit()
    return vehicle


@router.delete("/me/vehicles/{vehicle_id}", status_code=204)
def remove_vehicle(vehicle_id: int, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    vehicle = db.scalar(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.resident_id == user.id))
    if not vehicle:
        raise not_found("Vehicle")
    db.delete(vehicle)
    log_activity(db, user, "resident.remove_vehicle", "vehicle", vehicle_id, old=snapshot(vehicle))
    db.commit()


# =====================================================================
# Admin: residents management
# =====================================================================
def _list_stmt(user, society_id, status, wing_id, unit_id, resident_type, q):
    stmt = select(Resident).order_by(Resident.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Resident.society_id == sid)
    if status:
        stmt = stmt.where(Resident.status == status)
    if wing_id:
        stmt = stmt.where(Resident.wing_id == wing_id)
    if unit_id:
        stmt = stmt.where(Resident.unit_id == unit_id)
    if resident_type:
        stmt = stmt.where(Resident.resident_type == resident_type)
    if q:
        stmt = stmt.where(or_(Resident.name.ilike(f"%{q}%"), Resident.mobile.like(f"%{q}%")))
    return stmt


@router.get("", response_model=Page[ResidentOut])
def list_residents(
    society_id: int | None = None,
    status: str | None = ResidentStatus.ACTIVE,  # default: approved residents (dashboard "Residents" link)
    wing_id: int | None = None,
    unit_id: int | None = None,
    resident_type: str | None = None,
    q: str | None = None,
    params: PageParams = Depends(),
    user: CurrentUser = Depends(ResidentManagers),
    db: Session = Depends(get_db),
):
    page = paginate(db, _list_stmt(user, society_id, status, wing_id, unit_id, resident_type, q), params)
    page["items"] = service.with_unit_labels(db, page["items"])
    return page


@router.get("/pending", response_model=Page[ResidentOut])
def pending_approvals(society_id: int | None = None, params: PageParams = Depends(),
                      user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    """Approval queue (oldest first). unit_occupied_flag marks 'Unit already occupied'."""
    stmt = _list_stmt(user, society_id, ResidentStatus.PENDING_APPROVAL, None, None, None, None)
    page = paginate(db, stmt.order_by(None).order_by(Resident.created_at), params)
    page["items"] = service.with_unit_labels(db, page["items"])
    return page


@router.get("/export")
def export_residents(society_id: int | None = None, status: str | None = None,
                     user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    residents = db.scalars(_list_stmt(user, society_id, status, None, None, None, None)).all()
    rows = service.with_unit_labels(db, residents)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Name", "Mobile", "Email", "Wing", "Unit", "Type", "Status", "Registered On"])
    for r in rows:
        writer.writerow([r["id"], r["name"], r["mobile"], r["email"] or "", r["wing_name"], r["unit_no"],
                         r["resident_type"], r["status"], r["created_at"].strftime("%Y-%m-%d")])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=residents.csv"})


@router.get("/{resident_id}", response_model=ResidentDetailOut)
def get_resident(resident_id: int, user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    return service.resident_detail(db, _get_resident(db, user, resident_id))


@router.patch("/{resident_id}", response_model=ResidentOut)
def admin_edit_resident(resident_id: int, data: ResidentAdminUpdate, user: CurrentUser = Depends(ResidentManagers),
                        db: Session = Depends(get_db)):
    """Edit a registration (name, email, flat, type, emergency contact), then approve it."""
    resident = _get_resident(db, user, resident_id)
    before = snapshot(resident)
    service.admin_update_resident(db, resident, data.model_dump(exclude_unset=True), user)
    old, new = diff(before, snapshot(resident))
    log_activity(db, user, "resident.admin_edit", "resident", resident.id, old, new, society_id=resident.society_id)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/{resident_id}/approve", response_model=ResidentOut)
def approve(resident_id: int, user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    resident = _get_resident(db, user, resident_id)
    before = resident.status
    service.approve_resident(db, resident, user)
    for doc in db.scalars(select(ResidentDocument).where(ResidentDocument.resident_id == resident.id)).all():
        doc.status = "verified"
    log_activity(db, user, "resident.approve", "resident", resident.id, {"status": before},
                 {"status": resident.status}, society_id=resident.society_id)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/{resident_id}/reject", response_model=ResidentOut)
def reject(resident_id: int, data: RejectRequest, user: CurrentUser = Depends(ResidentManagers),
           db: Session = Depends(get_db)):
    resident = _get_resident(db, user, resident_id)
    service.reject_resident(db, resident, data.reason)
    log_activity(db, user, "resident.reject", "resident", resident.id, {"status": ResidentStatus.PENDING_APPROVAL},
                 {"status": resident.status, "reason": data.reason}, society_id=resident.society_id)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/{resident_id}/deactivate", response_model=ResidentOut)
def deactivate(resident_id: int, user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    resident = _get_resident(db, user, resident_id)
    if resident.status == ResidentStatus.DEACTIVATED:
        raise bad_request("Resident is already deactivated")
    old = resident.status
    affected = service.deactivate_resident(db, resident)
    log_activity(db, user, "resident.deactivate", "resident", resident.id, {"status": old},
                 {"status": resident.status, "affected_ids": affected}, society_id=resident.society_id)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/{resident_id}/reactivate", response_model=ResidentOut)
def reactivate(resident_id: int, user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    resident = _get_resident(db, user, resident_id)
    if resident.status != ResidentStatus.DEACTIVATED:
        raise bad_request("Only deactivated residents can be reactivated")
    if resident.is_primary and service.active_primary_for_unit(db, resident.unit_id, exclude_id=resident.id):
        raise bad_request("Unit already has an active primary account")
    resident.status = ResidentStatus.ACTIVE
    db.flush()
    from app.modules.societies.service import refresh_unit_occupancy

    refresh_unit_occupancy(db, resident.unit_id)
    log_activity(db, user, "resident.reactivate", "resident", resident.id, {"status": ResidentStatus.DEACTIVATED},
                 {"status": resident.status}, society_id=resident.society_id)
    db.commit()
    return service.with_unit_labels(db, [resident])[0]


@router.post("/{resident_id}/family", response_model=FamilyMemberOut, status_code=201)
def admin_add_family_member(resident_id: int, data: FamilyMemberCreate,
                            user: CurrentUser = Depends(ResidentManagers), db: Session = Depends(get_db)):
    resident = _get_resident(db, user, resident_id)
    linked_id = None
    if data.grant_app_access:
        linked_id = service.create_linked_resident(db, resident, data.name, data.mobile, None,
                                                   ResidentType.FAMILY).id
    member = FamilyMember(resident_id=resident.id, name=data.name, relation=data.relation, mobile=data.mobile,
                          linked_resident_id=linked_id)
    db.add(member)
    db.flush()
    log_activity(db, user, "resident.admin_add_family_member", "family_member", member.id, new=snapshot(member),
                 society_id=resident.society_id)
    db.commit()
    return member


@router.post("/{resident_id}/tenant", response_model=ResidentOut, status_code=201)
def admin_add_tenant(resident_id: int, data: TenantCreate, user: CurrentUser = Depends(ResidentManagers),
                     db: Session = Depends(get_db)):
    owner = _get_resident(db, user, resident_id)
    if owner.resident_type != ResidentType.OWNER:
        raise bad_request("Tenants can only be added under an owner")
    tenant = service.create_linked_resident(db, owner, data.name, data.mobile, data.email, ResidentType.TENANT,
                                            data.maintenance_visible)
    log_activity(db, user, "resident.admin_add_tenant", "resident", tenant.id, new=snapshot(tenant),
                 society_id=owner.society_id)
    db.commit()
    return service.with_unit_labels(db, [tenant])[0]
