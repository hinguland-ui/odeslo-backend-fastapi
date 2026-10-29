from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import NotificationCategory, ResidentStatus, ResidentType, UserType
from app.core.errors import bad_request, conflict, not_found
from app.core.timeutils import now_ist
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.residents.models import FamilyMember, Resident, ResidentDocument, Vehicle
from app.modules.societies.models import Society, Unit, Wing
from app.modules.societies.service import ensure_society_active, refresh_unit_occupancy


def active_primary_for_unit(db: Session, unit_id: int, exclude_id: int | None = None) -> Resident | None:
    stmt = select(Resident).where(Resident.unit_id == unit_id, Resident.is_primary.is_(True),
                                  Resident.status == ResidentStatus.ACTIVE)
    if exclude_id:
        stmt = stmt.where(Resident.id != exclude_id)
    return db.scalar(stmt)


def ensure_mobile_free(db: Session, mobile: str, society_id: int | None = None,
                       exclude_id: int | None = None) -> None:
    """A mobile may already live in another society, but only once per society."""
    stmt = select(Resident).where(Resident.mobile == mobile, Resident.status != ResidentStatus.DEACTIVATED)
    if society_id is not None:
        stmt = stmt.where(Resident.society_id == society_id)
    for existing in db.scalars(stmt).all():
        if existing.id != exclude_id:
            raise conflict("This mobile number is already registered as a resident")


def resolve_unit(db: Session, society_id: int | None, society_code: str | None, wing_id: int,
                 unit_id: int) -> tuple[Society, Wing, Unit]:
    society = (db.get(Society, society_id) if society_id
               else db.scalar(select(Society).where(Society.code == (society_code or "").upper())))
    if not society:
        raise not_found("Society")
    ensure_society_active(society)
    wing = db.get(Wing, wing_id)
    if not wing or wing.society_id != society.id:
        raise bad_request("Selected wing does not belong to this society")
    unit = db.get(Unit, unit_id)
    if not unit or unit.wing_id != wing.id:
        raise bad_request("Selected unit does not belong to this wing")
    return society, wing, unit


def register_resident(db: Session, mobile: str, data, add_home: bool = False) -> Resident:
    """First registration of a mobile, a re-apply after rejection, or (add_home) one more home for a resident."""
    society, wing, unit = resolve_unit(db, data.society_id, data.society_code, data.wing_id, data.unit_id)
    rows = db.scalars(select(Resident).where(Resident.mobile == mobile)).all()
    same_flat = next((r for r in rows if r.unit_id == unit.id), None)
    if same_flat and same_flat.status != ResidentStatus.REJECTED:
        raise conflict("You already have a request or an account for this flat.")
    if not add_home and any(r.status not in (ResidentStatus.REJECTED, ResidentStatus.DEACTIVATED) for r in rows):
        raise conflict("This mobile number is already registered. Please login.")
    # re-applying: the rejected request of this flat, else the rejected one of this society
    existing = same_flat or next((r for r in rows if r.status == ResidentStatus.REJECTED
                                  and r.society_id == society.id), None)

    is_primary = data.resident_type in (ResidentType.OWNER, ResidentType.TENANT)
    current = active_primary_for_unit(db, unit.id) if is_primary else None
    if current:
        raise conflict("This flat already has an approved resident. Choose another flat, "
                       "or ask the owner to add you as a family member.")
    occupied = False
    values = dict(
        society_id=society.id, wing_id=wing.id, unit_id=unit.id, name=data.name, mobile=mobile, email=data.email,
        resident_type=data.resident_type, is_primary=is_primary, status=ResidentStatus.PENDING_APPROVAL,
        unit_occupied_flag=occupied, rejection_reason=None, parent_resident_id=None,
        emergency_contact_name=data.emergency_contact_name, emergency_contact_mobile=data.emergency_contact_mobile,
    )
    if existing:  # re-applying after rejection
        for k, v in values.items():
            setattr(existing, k, v)
        resident = existing
    else:
        resident = Resident(**values)
        db.add(resident)
    db.flush()

    flag = " (Unit already occupied)" if occupied else ""
    notify_society_admins(
        db, society.id, NotificationCategory.REGISTRATION, "New resident registration",
        f"{resident.name} requested to join {wing.name}-{unit.unit_no} as {resident.resident_type}{flag}.",
        perm="residents", ref_type="resident", ref_id=resident.id, push=True, in_app=True,
    )
    return resident


def approve_resident(db: Session, resident: Resident, actor: CurrentUser) -> Resident:
    if resident.status not in (ResidentStatus.PENDING_APPROVAL, ResidentStatus.REJECTED):
        raise bad_request(f"Resident is already {resident.status}")
    if resident.is_primary:
        current = active_primary_for_unit(db, resident.unit_id, exclude_id=resident.id)
        if current:
            raise conflict(f"Unit already occupied by an active primary account ({current.name}). "
                           "Deactivate it before approving this request.")
    resident.status = ResidentStatus.ACTIVE
    resident.unit_occupied_flag = False
    resident.rejection_reason = None
    resident.approved_by = actor.id
    resident.approved_by_type = actor.user_type
    resident.approved_at = now_ist()
    db.flush()
    refresh_unit_occupancy(db, resident.unit_id)
    notify(db, UserType.RESIDENT, resident.id, NotificationCategory.ACCOUNT, "Registration approved",
           "Welcome! Your society admin approved your registration. All features are now unlocked.",
           ref_type="resident", ref_id=resident.id, sms=True)
    return resident


def reject_resident(db: Session, resident: Resident, reason: str) -> Resident:
    if resident.status != ResidentStatus.PENDING_APPROVAL:
        raise bad_request("Only pending registrations can be rejected")
    resident.status = ResidentStatus.REJECTED
    resident.rejection_reason = reason
    notify(db, UserType.RESIDENT, resident.id, NotificationCategory.ACCOUNT, "Registration rejected",
           f"Your registration was rejected. Reason: {reason}", ref_type="resident", ref_id=resident.id, sms=True)
    return resident


def deactivate_resident(db: Session, resident: Resident) -> list[int]:
    """Deactivate a resident and everyone linked under them (family / tenant added by them)."""
    affected = [resident.id]
    resident.status = ResidentStatus.DEACTIVATED
    for dep in db.scalars(select(Resident).where(Resident.parent_resident_id == resident.id,
                                                 Resident.status == ResidentStatus.ACTIVE)).all():
        dep.status = ResidentStatus.DEACTIVATED
        affected.append(dep.id)
    db.flush()
    refresh_unit_occupancy(db, resident.unit_id)
    return affected


def create_linked_resident(db: Session, parent: Resident, name: str, mobile: str, email: str | None,
                           resident_type: str, maintenance_visible: bool = False) -> Resident:
    """Family member / tenant login added by the owner: linked to the same unit, active immediately."""
    ensure_mobile_free(db, mobile, parent.society_id)
    child = Resident(
        society_id=parent.society_id, wing_id=parent.wing_id, unit_id=parent.unit_id, name=name, mobile=mobile,
        email=email, resident_type=resident_type, is_primary=False, parent_resident_id=parent.id,
        status=ResidentStatus.ACTIVE, approved_by=parent.id, approved_by_type=UserType.RESIDENT,
        approved_at=now_ist(), maintenance_visible=maintenance_visible,
    )
    db.add(child)
    db.flush()
    refresh_unit_occupancy(db, parent.unit_id)
    return child


def can_view_maintenance(resident: Resident) -> bool:
    if resident.resident_type == ResidentType.OWNER:
        return True
    if resident.resident_type == ResidentType.TENANT:
        return resident.maintenance_visible
    return False


def with_unit_labels(db: Session, residents: Sequence[Resident]) -> list[dict]:
    """Attach wing name and unit number for list views (avoids N+1 queries)."""
    from app.modules.residents.schemas import ResidentOut

    unit_ids = {r.unit_id for r in residents}
    units = {u.id: u for u in db.scalars(select(Unit).where(Unit.id.in_(unit_ids))).all()} if unit_ids else {}
    wing_ids = {u.wing_id for u in units.values()}
    wings = {w.id: w for w in db.scalars(select(Wing).where(Wing.id.in_(wing_ids))).all()} if wing_ids else {}
    out = []
    for r in residents:
        unit = units.get(r.unit_id)
        wing = wings.get(unit.wing_id) if unit else None
        out.append({**ResidentOut.model_validate(r).model_dump(), "wing_name": wing.name if wing else None,
                    "unit_no": unit.unit_no if unit else None})
    return out


def resident_detail(db: Session, resident: Resident) -> dict:
    data = with_unit_labels(db, [resident])[0]
    data["documents"] = db.scalars(select(ResidentDocument).where(ResidentDocument.resident_id == resident.id)).all()
    data["family_members"] = db.scalars(select(FamilyMember).where(FamilyMember.resident_id == resident.id)).all()
    data["vehicles"] = db.scalars(select(Vehicle).where(Vehicle.resident_id == resident.id)).all()
    occupant = active_primary_for_unit(db, resident.unit_id, exclude_id=resident.id) if resident.is_primary else None
    data["occupied_by"] = with_unit_labels(db, [occupant])[0] if occupant else None
    return data


def admin_update_resident(db: Session, resident: Resident, values: dict, actor: CurrentUser) -> Resident:
    """Society admin corrects a registration (name, flat, type, contact) before approving it.

    Flat and type can only change while the request is pending or rejected.
    """
    reviewable = resident.status in (ResidentStatus.PENDING_APPROVAL, ResidentStatus.REJECTED)
    wants_flat = {"wing_id", "unit_id", "resident_type"} & values.keys()
    if wants_flat and not reviewable:
        raise bad_request("Flat and resident type can only be changed while the request is pending or rejected")

    if "wing_id" in values or "unit_id" in values:
        _, wing, unit = resolve_unit(db, resident.society_id, None, values.get("wing_id") or resident.wing_id,
                                     values.get("unit_id") or resident.unit_id)
        resident.wing_id, resident.unit_id = wing.id, unit.id
    for key in ("name", "email", "resident_type", "emergency_contact_name", "emergency_contact_mobile"):
        if key in values:
            setattr(resident, key, values[key])
    if reviewable:
        resident.is_primary = resident.resident_type in (ResidentType.OWNER, ResidentType.TENANT)
        resident.unit_occupied_flag = bool(
            resident.is_primary and active_primary_for_unit(db, resident.unit_id, exclude_id=resident.id))
    db.flush()
    if wants_flat:
        notify(db, UserType.RESIDENT, resident.id, NotificationCategory.ACCOUNT, "Registration details updated",
               "Your society admin corrected your flat or resident type details.", ref_type="resident",
               ref_id=resident.id)
    return resident


def list_homes(db: Session, mobile: str) -> list[dict]:
    """Every home of this mobile (one row per flat), for the "switch home" menu."""
    rows = db.scalars(select(Resident).where(Resident.mobile == mobile, Resident.status != ResidentStatus.DEACTIVATED)
                      .order_by(Resident.id)).all()
    labelled = {r["id"]: r for r in with_unit_labels(db, rows)}
    societies = {s.id: s for s in db.scalars(select(Society).where(Society.id.in_({r.society_id for r in rows}))).all()} \
        if rows else {}
    out = []
    for r in rows:
        lab, soc = labelled[r.id], societies.get(r.society_id)
        out.append({"id": r.id, "society_id": r.society_id, "society_name": soc.name if soc else "",
                    "society_code": soc.code if soc else "", "society_area": soc.area if soc else "",
                    "wing_name": lab["wing_name"], "unit_no": lab["unit_no"],
                    "unit_label": f"{lab['wing_name']}-{lab['unit_no']}", "resident_type": r.resident_type,
                    "status": r.status, "rejection_reason": r.rejection_reason})
    return out
