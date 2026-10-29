from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.geo import bounding_box, haversine_km
from app.core.deps import CurrentUser, ensure_society_access, require_roles
from app.core.enums import AdminPermission, AdminRole, ResidentStatus, SocietyStatus, UserType
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.files import IMAGE_TYPES, SPREADSHEET_TYPES, read_upload, release_upload, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.notifications.service import notify_society_admins
from app.modules.residents.models import Resident
from app.modules.societies import deletion, service
from app.modules.societies.models import Society, Unit, Wing
from app.modules.societies.schemas import (
    SocietyDelete,
    Features,
    JoinInfoOut,
    MaintenanceSettings,
    SocietyCreate,
    SocietyDetailOut,
    SocietyOut,
    SocietyPublicOut,
    SocietyStatusUpdate,
    SocietyUpdate,
    UnitBulkResult,
    UnitCreate,
    UnitOut,
    UnitUpdate,
    WingCreate,
    WingOut,
)
from app.modules.society_admins.service import create_society_admin

router = APIRouter(prefix="/societies", tags=["Societies"])

SuperAdminOnly = require_roles(UserType.SUPER_ADMIN)
Admins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN)
UnitManagers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.UNITS)


def _society_for(db: Session, user: CurrentUser, society_id: int) -> Society:
    ensure_society_access(user, society_id, "Society")
    return service.get_society_or_404(db, society_id)


# ---------- Public (resident registration: select society / code / QR) ----------
@router.get("/public/search", response_model=list[SocietyPublicOut])
def search_societies(q: str = Query("", max_length=100), city: str | None = None,
                     lat: float | None = Query(None, ge=-90, le=90), lng: float | None = Query(None, ge=-180, le=180),
                     radius_km: float = Query(25, gt=0, le=200), db: Session = Depends(get_db)):
    """Name / area / code search. With lat & lng: societies within radius_km, nearest first (distance_km set)."""
    stmt = select(Society).where(Society.status == SocietyStatus.ACTIVE).order_by(Society.name)
    if q:
        stmt = stmt.where(or_(Society.name.ilike(f"%{q}%"), Society.area.ilike(f"%{q}%"), Society.code == q.upper()))
    if city:
        stmt = stmt.where(Society.city.ilike(city))
    if lat is None or lng is None:
        return db.scalars(stmt.limit(50)).all()

    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    stmt = stmt.where(Society.lat.between(min_lat, max_lat), Society.lng.between(min_lng, max_lng))
    nearby = []
    for society in db.scalars(stmt).all():
        distance = haversine_km(lat, lng, society.lat, society.lng)
        if distance <= radius_km:
            nearby.append({**SocietyPublicOut.model_validate(society).model_dump(), "distance_km": round(distance, 2)})
    return sorted(nearby, key=lambda s: s["distance_km"])[:50]


@router.get("/public/by-code/{code}", response_model=SocietyPublicOut)
def society_by_code(code: str, db: Session = Depends(get_db)):
    society = db.scalar(select(Society).where(Society.code == code.upper(), Society.status == SocietyStatus.ACTIVE))
    if not society:
        raise not_found("Society")
    return society


@router.get("/public/{society_id}/wings", response_model=list[WingOut])
def public_wings(society_id: int, db: Session = Depends(get_db)):
    society = db.get(Society, society_id)
    if not society or society.status != SocietyStatus.ACTIVE:
        raise not_found("Society")
    return db.scalars(select(Wing).where(Wing.society_id == society_id).order_by(Wing.name)).all()


@router.get("/public/wings/{wing_id}/units", response_model=list[UnitOut])
def public_units(wing_id: int, db: Session = Depends(get_db)):
    wing = db.get(Wing, wing_id)
    society = wing and db.get(Society, wing.society_id)
    if not society or society.status != SocietyStatus.ACTIVE:
        raise not_found("Wing")
    return db.scalars(select(Unit).where(Unit.wing_id == wing_id).order_by(Unit.floor, Unit.unit_no)).all()


# ---------- Societies (Super Admin) ----------
@router.post("", response_model=SocietyOut, status_code=201)
def create_society(data: SocietyCreate, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    society = Society(
        code=service.generate_society_code(db, data.name),
        **data.model_dump(exclude={"wings", "primary_admin", "maintenance_settings", "features"}),
        maintenance_settings=data.maintenance_settings.model_dump(mode="json"),
        features=data.features.model_dump(),
        status=SocietyStatus.PENDING,
        created_by=user.id,
    )
    db.add(society)
    db.flush()
    for wing_in in data.wings:
        wing = service.add_wing(db, society.id, wing_in.name)
        for unit_in in wing_in.units:
            service.add_unit(db, wing, unit_in.unit_no, unit_in.unit_type, unit_in.floor)
    pa = data.primary_admin
    admin = create_society_admin(
        db, society, name=pa.name, mobile=pa.mobile, email=pa.email, designation=pa.designation,
        role=AdminRole.PRIMARY, permissions=None, unit_id=None,
        created_by_type=user.user_type, created_by_id=user.id,
    )
    log_activity(db, user, "society.create", "society", society.id, new=snapshot(society), society_id=society.id)
    log_activity(db, user, "society_admin.create", "society_admin", admin.id, new=snapshot(admin),
                 society_id=society.id)
    db.commit()
    return society


@router.get("", response_model=Page[SocietyOut])
def list_societies(status: str | None = None, city: str | None = None, q: str | None = None,
                   params: PageParams = Depends(), user: CurrentUser = Depends(SuperAdminOnly),
                   db: Session = Depends(get_db)):
    stmt = select(Society).order_by(Society.id.desc())
    if status:
        stmt = stmt.where(Society.status == status)
    if city:
        stmt = stmt.where(Society.city.ilike(city))
    if q:
        stmt = stmt.where(or_(Society.name.ilike(f"%{q}%"), Society.code == q.upper()))
    return paginate(db, stmt, params)


@router.get("/{society_id}/delete-preview")
def delete_preview(society_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    """What deleting this society would remove, and what stays (vendors and their payment history)."""
    return deletion.preview(db, service.get_society_or_404(db, society_id))


@router.post("/{society_id}/delete")
def delete_society(society_id: int, data: SocietyDelete, user: CurrentUser = Depends(SuperAdminOnly),
                   db: Session = Depends(get_db)):
    """Permanently delete the society and all of its data. Needs the society code typed again as confirmation."""
    society = service.get_society_or_404(db, society_id)
    if data.confirm_code.strip().upper() != society.code.upper():
        raise bad_request("The code you typed does not match this society's code")
    result = deletion.delete_society(db, society, user, delete_media=data.delete_media)
    db.commit()
    return result


@router.get("/{society_id}", response_model=SocietyDetailOut)
def get_society(society_id: int, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    society = _society_for(db, user, society_id)
    return {**SocietyOut.model_validate(society).model_dump(), "stats": service.society_stats(db, society_id)}


@router.patch("/{society_id}", response_model=SocietyOut)
def update_society(society_id: int, data: SocietyUpdate, user: CurrentUser = Depends(Admins),
                   db: Session = Depends(get_db)):
    society = _society_for(db, user, society_id)
    if user.is_society_admin and not user.is_primary_admin:
        raise forbidden("Only the primary admin can edit the society profile")
    changes = data.model_dump(exclude_unset=True)
    if user.is_society_admin and ("lat" in changes or "lng" in changes):
        raise forbidden("A society's location is changed by the Odeslo team. Send a location change request instead.")
    before = snapshot(society)
    for k, v in changes.items():
        setattr(society, k, v)
    db.flush()
    old, new = diff(before, snapshot(society))
    log_activity(db, user, "society.update", "society", society.id, old, new, society_id=society.id)
    db.commit()
    return society


@router.post("/{society_id}/logo", response_model=SocietyOut)
def upload_logo(society_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(Admins),
                db: Session = Depends(get_db)):
    society = _society_for(db, user, society_id)
    old_logo = society.logo
    society.logo = save_upload(file, "societies", IMAGE_TYPES, db=db, user=user, audience="society", society_id=society.id)
    release_upload(db, old_logo)
    log_activity(db, user, "society.upload_logo", "society", society.id, society_id=society.id)
    db.commit()
    return society


@router.put("/{society_id}/maintenance-settings", response_model=SocietyOut)
def update_maintenance_settings(
    society_id: int, data: MaintenanceSettings,
    user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN,
                                              perm=AdminPermission.MAINTENANCE)),
    db: Session = Depends(get_db),
):
    society = _society_for(db, user, society_id)
    old = society.maintenance_settings
    society.maintenance_settings = data.model_dump(mode="json")
    log_activity(db, user, "society.update_maintenance_settings", "society", society.id,
                 old={"maintenance_settings": old}, new={"maintenance_settings": society.maintenance_settings},
                 society_id=society.id)
    db.commit()
    return society


@router.put("/{society_id}/features", response_model=SocietyOut)
def update_features(society_id: int, data: Features, user: CurrentUser = Depends(SuperAdminOnly),
                    db: Session = Depends(get_db)):
    society = service.get_society_or_404(db, society_id)
    old = society.features
    society.features = data.model_dump()
    log_activity(db, user, "society.update_features", "society", society.id, old={"features": old},
                 new={"features": society.features}, society_id=society.id)
    db.commit()
    return society


@router.post("/{society_id}/status", response_model=SocietyOut)
def change_society_status(society_id: int, data: SocietyStatusUpdate, user: CurrentUser = Depends(SuperAdminOnly),
                          db: Session = Depends(get_db)):
    society = service.get_society_or_404(db, society_id)
    if data.status == SocietyStatus.ACTIVE:
        if not db.scalar(select(func.count()).select_from(Unit).where(Unit.society_id == society.id)):
            raise bad_request("Add wings and units before activating the society")
    old = society.status
    society.status = data.status
    log_activity(db, user, "society.status_change", "society", society.id, old={"status": old},
                 new={"status": data.status, "reason": data.reason}, society_id=society.id)
    notify_society_admins(db, society.id, "account", "Society status updated",
                          f"{society.name} is now {data.status.value}.", ref_type="society", ref_id=society.id)
    db.commit()
    return society


@router.get("/{society_id}/join-info", response_model=JoinInfoOut)
def join_info(society_id: int, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    """Society code + QR payload to share with residents (frontend renders the QR)."""
    society = _society_for(db, user, society_id)
    url = f"{settings.RESIDENT_APP_URL}/join?code={society.code}"
    return {"society_id": society.id, "code": society.code, "name": society.name, "join_url": url, "qr_payload": url}


# ---------- Wings ----------
@router.get("/{society_id}/wings", response_model=list[WingOut])
def list_wings(society_id: int, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    _society_for(db, user, society_id)
    return db.scalars(select(Wing).where(Wing.society_id == society_id).order_by(Wing.name)).all()


@router.post("/{society_id}/wings", response_model=WingOut, status_code=201)
def create_wing(society_id: int, data: WingCreate, user: CurrentUser = Depends(UnitManagers),
                db: Session = Depends(get_db)):
    _society_for(db, user, society_id)
    wing = service.add_wing(db, society_id, data.name)
    log_activity(db, user, "wing.create", "wing", wing.id, new=snapshot(wing), society_id=society_id)
    db.commit()
    return wing


@router.patch("/wings/{wing_id}", response_model=WingOut)
def rename_wing(wing_id: int, data: WingCreate, user: CurrentUser = Depends(UnitManagers),
                db: Session = Depends(get_db)):
    wing = db.get(Wing, wing_id)
    if not wing:
        raise not_found("Wing")
    ensure_society_access(user, wing.society_id, "Wing")
    if db.scalar(select(Wing.id).where(Wing.society_id == wing.society_id, Wing.name == data.name,
                                       Wing.id != wing.id)):
        raise conflict("A wing with this name already exists")
    old = wing.name
    wing.name = data.name
    log_activity(db, user, "wing.update", "wing", wing.id, {"name": old}, {"name": wing.name},
                 society_id=wing.society_id)
    db.commit()
    return wing


@router.delete("/wings/{wing_id}", status_code=204)
def delete_wing(wing_id: int, user: CurrentUser = Depends(UnitManagers), db: Session = Depends(get_db)):
    wing = db.get(Wing, wing_id)
    if not wing:
        raise not_found("Wing")
    ensure_society_access(user, wing.society_id, "Wing")
    if db.scalar(select(Resident.id).where(Resident.wing_id == wing.id).limit(1)):
        raise conflict("Wing has residents and cannot be deleted")
    for unit in db.scalars(select(Unit).where(Unit.wing_id == wing.id)).all():
        db.delete(unit)
    db.delete(wing)
    log_activity(db, user, "wing.delete", "wing", wing_id, old={"name": wing.name}, society_id=wing.society_id)
    db.commit()


# ---------- Units ----------
@router.get("/{society_id}/units", response_model=Page[UnitOut])
def list_units(society_id: int, wing_id: int | None = None, occupancy_status: str | None = None,
               q: str | None = None, params: PageParams = Depends(), user: CurrentUser = Depends(Admins),
               db: Session = Depends(get_db)):
    _society_for(db, user, society_id)
    stmt = select(Unit).where(Unit.society_id == society_id).order_by(Unit.wing_id, Unit.floor, Unit.unit_no)
    if wing_id:
        stmt = stmt.where(Unit.wing_id == wing_id)
    if occupancy_status:
        stmt = stmt.where(Unit.occupancy_status == occupancy_status)
    if q:
        stmt = stmt.where(Unit.unit_no.ilike(f"%{q}%"))
    return paginate(db, stmt, params)


@router.post("/{society_id}/units", response_model=UnitOut, status_code=201)
def create_unit(society_id: int, data: UnitCreate, user: CurrentUser = Depends(UnitManagers),
                db: Session = Depends(get_db)):
    _society_for(db, user, society_id)
    wing = db.get(Wing, data.wing_id)
    if not wing or wing.society_id != society_id:
        raise bad_request("Wing does not belong to this society")
    unit = service.add_unit(db, wing, data.unit_no, data.unit_type, data.floor)
    log_activity(db, user, "unit.create", "unit", unit.id, new=snapshot(unit), society_id=society_id)
    db.commit()
    return unit


@router.post("/{society_id}/units/bulk-upload", response_model=UnitBulkResult)
def bulk_upload_units(society_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(UnitManagers),
                      db: Session = Depends(get_db)):
    """CSV / XLSX with columns: wing, unit_no, unit_type, floor."""
    _society_for(db, user, society_id)
    content, _ = read_upload(file, SPREADSHEET_TYPES)
    result = service.bulk_upload_units(db, society_id, file.filename or "units.csv", content)
    log_activity(db, user, "unit.bulk_upload", "unit", None, new=result, society_id=society_id)
    db.commit()
    return result


@router.patch("/units/{unit_id}", response_model=UnitOut)
def update_unit(unit_id: int, data: UnitUpdate, user: CurrentUser = Depends(UnitManagers),
                db: Session = Depends(get_db)):
    unit = db.get(Unit, unit_id)
    if not unit:
        raise not_found("Unit")
    ensure_society_access(user, unit.society_id, "Unit")
    values = data.model_dump(exclude_unset=True)
    if "unit_no" in values and db.scalar(select(Unit.id).where(
            Unit.wing_id == unit.wing_id, Unit.unit_no == values["unit_no"], Unit.id != unit.id)):
        raise conflict("Unit number already exists in this wing")
    before = snapshot(unit)
    for k, v in values.items():
        setattr(unit, k, v)
    db.flush()
    old, new = diff(before, snapshot(unit))
    log_activity(db, user, "unit.update", "unit", unit.id, old, new, society_id=unit.society_id)
    db.commit()
    return unit


@router.delete("/units/{unit_id}", status_code=204)
def delete_unit(unit_id: int, user: CurrentUser = Depends(UnitManagers), db: Session = Depends(get_db)):
    unit = db.get(Unit, unit_id)
    if not unit:
        raise not_found("Unit")
    ensure_society_access(user, unit.society_id, "Unit")
    if db.scalar(select(Resident.id).where(Resident.unit_id == unit.id).limit(1)):
        raise conflict("Unit has resident records and cannot be deleted")
    db.delete(unit)
    log_activity(db, user, "unit.delete", "unit", unit_id, old=snapshot(unit), society_id=unit.society_id)
    db.commit()
