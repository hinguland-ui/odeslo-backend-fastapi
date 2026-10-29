from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.enums import NotificationCategory, UserType
from app.core.errors import bad_request, forbidden, not_found
from app.core.timeutils import now_ist
from app.core.utils import snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.location_requests.models import LocationRequest
from app.modules.notifications.service import notify_society_admins, notify_super_admins
from app.modules.societies.models import Society

router = APIRouter(prefix="/location-requests", tags=["Society location requests"])

SuperAdminOnly = require_roles(UserType.SUPER_ADMIN)
Anyone = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN)


class LocationRequestCreate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address: str | None = Field(None, max_length=500)
    area: str | None = Field(None, max_length=120)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    reason: str | None = Field(None, max_length=500)


class Decision(BaseModel):
    note: str | None = Field(None, max_length=500)


class RejectIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


class LocationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    society_name: str | None = None
    society_code: str | None = None
    old_lat: float
    old_lng: float
    old_address: str | None
    new_lat: float
    new_lng: float
    new_address: str | None
    new_area: str | None
    new_city: str | None
    new_state: str | None
    new_pincode: str | None
    reason: str | None
    status: str
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


def _out(db: Session, r: LocationRequest) -> dict:
    s = db.get(Society, r.society_id)
    return {**LocationRequestOut.model_validate(r).model_dump(), "society_name": s.name if s else None, "society_code": s.code if s else None}


def _get(db: Session, user: CurrentUser, rid: int) -> LocationRequest:
    r = db.get(LocationRequest, rid)
    if not r or (user.is_society_admin and r.society_id != user.society_id):
        raise not_found("Location request")
    return r


@router.get("", response_model=list[LocationRequestOut])
def list_requests(status: str | None = None, user: CurrentUser = Depends(Anyone), db: Session = Depends(get_db)):
    """Super admin: every society's requests. Society admin: their own society's."""
    stmt = select(LocationRequest).order_by(LocationRequest.id.desc()).limit(200)
    if user.is_society_admin:
        stmt = stmt.where(LocationRequest.society_id == user.society_id)
    if status:
        stmt = stmt.where(LocationRequest.status == status)
    return [_out(db, r) for r in db.scalars(stmt).all()]


@router.post("", response_model=LocationRequestOut, status_code=201)
def create_request(data: LocationRequestCreate, user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN)),
                   db: Session = Depends(get_db)):
    """Ask the Odeslo team to move the society's pin. Only the primary admin can; one open request at a time."""
    if not user.is_primary_admin:
        raise forbidden("Only the primary admin can ask for a location change")
    society = db.get(Society, user.society_id)
    if abs(society.lat - data.lat) < 1e-6 and abs(society.lng - data.lng) < 1e-6:
        raise bad_request("This is already the society's location")
    if db.scalar(select(LocationRequest.id).where(LocationRequest.society_id == society.id, LocationRequest.status == "pending")):
        raise bad_request("A location change is already waiting for the Odeslo team. Cancel it first to send a new one.")
    req = LocationRequest(
        society_id=society.id, old_lat=society.lat, old_lng=society.lng, old_address=", ".join(x for x in (society.address, society.area, society.city) if x),
        new_lat=data.lat, new_lng=data.lng, new_address=data.address, new_area=data.area, new_city=data.city, new_state=data.state, new_pincode=data.pincode,
        reason=data.reason, requested_by=user.id)
    db.add(req)
    db.flush()
    notify_super_admins(db, NotificationCategory.GENERAL, "Society location change request",
                        f"{society.name} ({society.code}) wants to move its location.", ref_type="location_request", ref_id=req.id, push=False)
    log_activity(db, user, "society.location_request", "location_request", req.id, new=snapshot(req), society_id=society.id)
    db.commit()
    return _out(db, req)


@router.post("/{rid}/cancel", response_model=LocationRequestOut)
def cancel_request(rid: int, user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN)), db: Session = Depends(get_db)):
    req = _get(db, user, rid)
    if req.status != "pending":
        raise bad_request(f"This request is already {req.status}")
    req.status = "cancelled"
    req.decided_at = now_ist()
    log_activity(db, user, "society.location_request_cancel", "location_request", req.id, society_id=req.society_id)
    db.commit()
    return _out(db, req)


@router.post("/{rid}/approve", response_model=LocationRequestOut)
def approve_request(rid: int, data: Decision | None = None, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    """Move the society to the requested spot (and its address, when one was sent)."""
    req = _get(db, user, rid)
    if req.status != "pending":
        raise bad_request(f"This request is already {req.status}")
    society = db.get(Society, req.society_id)
    before = snapshot(society)
    society.lat, society.lng = req.new_lat, req.new_lng
    for field, value in (("address", req.new_address), ("area", req.new_area), ("city", req.new_city), ("state", req.new_state), ("pincode", req.new_pincode)):
        if value:
            setattr(society, field, value)
    req.status, req.decided_by, req.decided_at = "approved", user.id, now_ist()
    req.decision_note = data.note if data else None
    log_activity(db, user, "society.location_approved", "society", society.id, before, snapshot(society), society_id=society.id)
    notify_society_admins(db, society.id, NotificationCategory.GENERAL, "Location change approved",
                          f"The Odeslo team moved {society.name} to the new location.", ref_type="location_request", ref_id=req.id)
    db.commit()
    return _out(db, req)


@router.post("/{rid}/reject", response_model=LocationRequestOut)
def reject_request(rid: int, data: RejectIn, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    req = _get(db, user, rid)
    if req.status != "pending":
        raise bad_request(f"This request is already {req.status}")
    req.status, req.decided_by, req.decided_at, req.decision_note = "rejected", user.id, now_ist(), data.note
    society = db.get(Society, req.society_id)
    log_activity(db, user, "society.location_rejected", "location_request", req.id, new={"note": data.note}, society_id=req.society_id)
    notify_society_admins(db, req.society_id, NotificationCategory.GENERAL, "Location change not approved", f"{society.name}: {data.note}",
                          ref_type="location_request", ref_id=req.id)
    db.commit()
    return _out(db, req)
