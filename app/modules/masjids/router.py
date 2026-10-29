from datetime import date

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.enums import AdminPermission, Feature, MasjidStatus, NotificationCategory, UserType
from app.core.errors import bad_request, forbidden, not_found
from app.core.files import IMAGE_TYPES, release_upload, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.timeutils import now_ist, today_ist
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.masjids import service
from app.modules.masjids.models import Masjid, MasjidTiming
from app.modules.masjids.schemas import (
    MasjidCreate,
    MasjidOut,
    MasjidUpdate,
    NearbyMasjidOut,
    PendingVerificationOut,
    RejectIn,
    TimetableOut,
    TimingCreate,
    TimingOut,
)
from app.modules.notifications.service import notify, notify_super_admins
from app.modules.societies.service import ensure_feature_enabled

router = APIRouter(prefix="/masjids", tags=["Masjid & Namaz"])

SuperAdminOnly = require_roles(UserType.SUPER_ADMIN)
MasjidAdmins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.MASJIDS)
# Masjid feature is available even while resident approval is pending
MasjidUsers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT,
                            allow_limited_resident=True)


def _visible(db: Session, user: CurrentUser, masjid_id: int) -> Masjid:
    masjid = db.get(Masjid, masjid_id)
    if not masjid:
        raise not_found("Masjid")
    if user.is_resident and not masjid.verified:
        raise not_found("Masjid")
    if user.is_society_admin and not masjid.verified and masjid.society_id != user.society_id:
        raise not_found("Masjid")
    return masjid


def _editable(db: Session, user: CurrentUser, masjid_id: int) -> Masjid:
    masjid = db.get(Masjid, masjid_id)
    if not masjid:
        raise not_found("Masjid")
    if user.is_society_admin and masjid.society_id != user.society_id:
        raise forbidden("You can only edit masjids added by your society")
    return masjid


def _check_feature(db: Session, user: CurrentUser) -> None:
    if not user.is_super_admin:
        ensure_feature_enabled(db, user.society_id, Feature.MASJID)


# ---------- Resident ----------
@router.get("/nearby", response_model=list[NearbyMasjidOut])
def nearby_masjids(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
                   radius_km: float = Query(settings.MASJID_DEFAULT_RADIUS_KM, gt=0, le=50),
                   user: CurrentUser = Depends(MasjidUsers), db: Session = Depends(get_db)):
    """Verified masjids within the radius (default 5 km), nearest first, with the next prayer."""
    _check_feature(db, user)
    if user.is_resident:
        user.entity.last_lat, user.entity.last_lng, user.entity.location_permission = lat, lng, True
        db.commit()
    result = []
    for masjid, distance in service.nearby(db, lat, lng, radius_km):
        tt = service.timetable(db, masjid, masjid.lat, masjid.lng, today_ist())
        result.append({**MasjidOut.model_validate(masjid).model_dump(), "distance_km": round(distance, 2),
                       "timings_source": tt["source"], "next_prayer": tt["next_prayer"]})
    return result


@router.get("/prayer-times", response_model=TimetableOut)
def prayer_times_for_location(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
                              on: date | None = None, user: CurrentUser = Depends(MasjidUsers),
                              db: Session = Depends(get_db)):
    """Calculated prayer times for a location (used when no verified masjid is nearby)."""
    return service.timetable(db, None, lat, lng, on or today_ist())


@router.get("/{masjid_id}/timetable", response_model=TimetableOut)
def masjid_timetable(masjid_id: int, on: date | None = None, user: CurrentUser = Depends(MasjidUsers),
                     db: Session = Depends(get_db)):
    masjid = _visible(db, user, masjid_id)
    return service.timetable(db, masjid, masjid.lat, masjid.lng, on or today_ist())


@router.post("/suggest", response_model=MasjidOut, status_code=201)
def suggest_masjid(data: MasjidCreate, user: CurrentUser = Depends(require_roles(UserType.RESIDENT)),
                   db: Session = Depends(get_db)):
    masjid = Masjid(**data.model_dump(), society_id=user.society_id, added_by_type=user.user_type, added_by=user.id,
                    is_suggestion=True, status=MasjidStatus.PENDING_VERIFICATION)
    db.add(masjid)
    db.flush()
    notify_super_admins(db, NotificationCategory.MASJID, "Masjid pending verification",
                        f"{masjid.name} was suggested by a resident.", ref_type="masjid", ref_id=masjid.id, push=False)
    log_activity(db, user, "masjid.suggest", "masjid", masjid.id, new=snapshot(masjid))
    db.commit()
    return masjid


# ---------- Admin ----------
@router.post("", response_model=MasjidOut, status_code=201)
def add_masjid(data: MasjidCreate, user: CurrentUser = Depends(MasjidAdmins), db: Session = Depends(get_db)):
    """Super Admin additions are verified immediately; Society Admin additions wait for verification."""
    _check_feature(db, user)
    verified = user.is_super_admin
    masjid = Masjid(**data.model_dump(), society_id=user.society_id, added_by_type=user.user_type, added_by=user.id,
                    verified=verified,
                    status=MasjidStatus.VERIFIED if verified else MasjidStatus.PENDING_VERIFICATION,
                    verified_by=user.id if verified else None, verified_at=now_ist() if verified else None)
    db.add(masjid)
    db.flush()
    if not verified:
        notify_super_admins(db, NotificationCategory.MASJID, "Masjid pending verification",
                            f"{masjid.name} was added and needs verification.", ref_type="masjid", ref_id=masjid.id,
                            push=False)
    log_activity(db, user, "masjid.create", "masjid", masjid.id, new=snapshot(masjid))
    db.commit()
    return masjid


@router.get("", response_model=Page[MasjidOut])
def list_masjids(status: str | None = None, q: str | None = None, params: PageParams = Depends(),
                 user: CurrentUser = Depends(MasjidAdmins), db: Session = Depends(get_db)):
    stmt = select(Masjid).order_by(Masjid.id.desc())
    if user.is_society_admin:
        stmt = stmt.where(or_(Masjid.verified.is_(True), Masjid.society_id == user.society_id))
    if status:
        stmt = stmt.where(Masjid.status == status)
    if q:
        stmt = stmt.where(or_(Masjid.name.ilike(f"%{q}%"), Masjid.area.ilike(f"%{q}%")))
    return paginate(db, stmt, params)


@router.get("/pending", response_model=PendingVerificationOut)
def pending_verification(user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    return {
        "masjids": db.scalars(select(Masjid).where(Masjid.status == MasjidStatus.PENDING_VERIFICATION)
                              .order_by(Masjid.created_at)).all(),
        "timings": db.scalars(select(MasjidTiming).where(MasjidTiming.status == MasjidStatus.PENDING_VERIFICATION)
                              .order_by(MasjidTiming.created_at)).all(),
    }


@router.get("/{masjid_id}", response_model=MasjidOut)
def get_masjid(masjid_id: int, user: CurrentUser = Depends(MasjidUsers), db: Session = Depends(get_db)):
    return _visible(db, user, masjid_id)


@router.patch("/{masjid_id}", response_model=MasjidOut)
def update_masjid(masjid_id: int, data: MasjidUpdate, user: CurrentUser = Depends(MasjidAdmins),
                  db: Session = Depends(get_db)):
    masjid = _editable(db, user, masjid_id)
    before = snapshot(masjid)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(masjid, k, v)
    if user.is_society_admin:  # edits by society admins need re-verification
        masjid.verified, masjid.status = False, MasjidStatus.PENDING_VERIFICATION
        notify_super_admins(db, NotificationCategory.MASJID, "Masjid pending verification",
                            f"{masjid.name} was edited and needs verification.", ref_type="masjid",
                            ref_id=masjid.id, push=False)
    db.flush()
    old, new = diff(before, snapshot(masjid))
    log_activity(db, user, "masjid.update", "masjid", masjid.id, old, new)
    db.commit()
    return masjid


@router.post("/{masjid_id}/photo", response_model=MasjidOut)
def upload_masjid_photo(masjid_id: int, file: UploadFile = File(...), user: CurrentUser = Depends(MasjidAdmins),
                        db: Session = Depends(get_db)):
    masjid = _editable(db, user, masjid_id)
    old_photo = masjid.photo
    masjid.photo = save_upload(file, "masjids", IMAGE_TYPES, db=db, user=user, audience="society", society_id=masjid.society_id)
    release_upload(db, old_photo)
    log_activity(db, user, "masjid.upload_photo", "masjid", masjid.id)
    db.commit()
    return masjid


@router.post("/{masjid_id}/verify", response_model=MasjidOut)
def verify_masjid(masjid_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    masjid = db.get(Masjid, masjid_id)
    if not masjid:
        raise not_found("Masjid")
    masjid.verified, masjid.status = True, MasjidStatus.VERIFIED
    masjid.verified_by, masjid.verified_at, masjid.rejection_reason = user.id, now_ist(), None
    log_activity(db, user, "masjid.verify", "masjid", masjid.id, new={"status": masjid.status})
    if masjid.added_by_type != UserType.SUPER_ADMIN:
        notify(db, masjid.added_by_type, masjid.added_by, NotificationCategory.MASJID, "Masjid verified",
               f"{masjid.name} is now verified and visible to residents.", ref_type="masjid", ref_id=masjid.id)
    db.commit()
    return masjid


@router.post("/{masjid_id}/reject", response_model=MasjidOut)
def reject_masjid(masjid_id: int, data: RejectIn, user: CurrentUser = Depends(SuperAdminOnly),
                  db: Session = Depends(get_db)):
    masjid = db.get(Masjid, masjid_id)
    if not masjid:
        raise not_found("Masjid")
    masjid.verified, masjid.status, masjid.rejection_reason = False, MasjidStatus.REJECTED, data.reason
    log_activity(db, user, "masjid.reject", "masjid", masjid.id, new={"status": masjid.status, "reason": data.reason})
    if masjid.added_by_type != UserType.SUPER_ADMIN:
        notify(db, masjid.added_by_type, masjid.added_by, NotificationCategory.MASJID, "Masjid rejected",
               f"{masjid.name}: {data.reason}", ref_type="masjid", ref_id=masjid.id)
    db.commit()
    return masjid


# ---------- Timetables ----------
@router.get("/{masjid_id}/timings", response_model=list[TimingOut])
def list_timings(masjid_id: int, user: CurrentUser = Depends(MasjidAdmins), db: Session = Depends(get_db)):
    _visible(db, user, masjid_id)
    return db.scalars(select(MasjidTiming).where(MasjidTiming.masjid_id == masjid_id)
                      .order_by(MasjidTiming.effective_from.desc())).all()


@router.post("/{masjid_id}/timings", response_model=TimingOut, status_code=201)
def submit_timings(masjid_id: int, data: TimingCreate, user: CurrentUser = Depends(MasjidAdmins),
                   db: Session = Depends(get_db)):
    """Jamaat timetable. Super Admin entries are verified at once; Society Admin entries wait for verification."""
    masjid = _editable(db, user, masjid_id)
    verified = user.is_super_admin
    timing = MasjidTiming(masjid_id=masjid.id, **data.model_dump(), verified=verified,
                          status=MasjidStatus.VERIFIED if verified else MasjidStatus.PENDING_VERIFICATION,
                          submitted_by_type=user.user_type, submitted_by=user.id,
                          verified_by=user.id if verified else None, verified_at=now_ist() if verified else None)
    db.add(timing)
    db.flush()
    if not verified:
        notify_super_admins(db, NotificationCategory.MASJID, "Timetable pending verification",
                            f"New timetable for {masjid.name} from {data.effective_from:%d %b %Y}.",
                            ref_type="masjid_timing", ref_id=timing.id, push=False)
    log_activity(db, user, "masjid.submit_timings", "masjid_timing", timing.id, new=snapshot(timing))
    db.commit()
    return timing


def _timing(db: Session, timing_id: int) -> MasjidTiming:
    timing = db.get(MasjidTiming, timing_id)
    if not timing:
        raise not_found("Timetable")
    return timing


@router.post("/timings/{timing_id}/verify", response_model=TimingOut)
def verify_timings(timing_id: int, user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    timing = _timing(db, timing_id)
    masjid = db.get(Masjid, timing.masjid_id)
    if not masjid.verified:
        raise bad_request("Verify the masjid first")
    timing.verified, timing.status, timing.verified_by, timing.verified_at = True, MasjidStatus.VERIFIED, user.id, now_ist()
    log_activity(db, user, "masjid.verify_timings", "masjid_timing", timing.id, new={"status": timing.status})
    db.commit()
    return timing


@router.post("/timings/{timing_id}/reject", response_model=TimingOut)
def reject_timings(timing_id: int, data: RejectIn, user: CurrentUser = Depends(SuperAdminOnly),
                   db: Session = Depends(get_db)):
    timing = _timing(db, timing_id)
    timing.verified, timing.status, timing.rejection_reason = False, MasjidStatus.REJECTED, data.reason
    log_activity(db, user, "masjid.reject_timings", "masjid_timing", timing.id,
                 new={"status": timing.status, "reason": data.reason})
    db.commit()
    return timing
