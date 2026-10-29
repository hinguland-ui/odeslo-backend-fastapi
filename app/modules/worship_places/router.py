from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles, scoped_society_id
from app.core.enums import AdminPermission, UserType
from app.core.errors import bad_request, not_found
from app.core.files import IMAGE_TYPES, release_upload, save_upload
from app.core.utils import snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.societies.service import get_society_or_404
from app.modules.worship_places.models import WorshipPlace
from app.modules.worship_places.schemas import PlaceCreate, PlaceOut, PlaceUpdate

router = APIRouter(prefix="/worship-places", tags=["Places of worship"])

MAX_PHOTOS = 4

# Society admins (co-admins with the "masjids" permission) manage the list; residents of the society read it.
Managers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.MASJIDS)
Readers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, UserType.RESIDENT)


def _get(db: Session, user: CurrentUser, place_id: int, *, manage: bool = False) -> WorshipPlace:
    place = db.get(WorshipPlace, place_id)
    if not place or (not user.is_super_admin and place.society_id != user.society_id):
        raise not_found("Place of worship")
    if not manage and user.is_resident and not place.is_active:
        raise not_found("Place of worship")
    return place


@router.get("", response_model=list[PlaceOut])
def list_places(society_id: int | None = None, kind: str | None = None, user: CurrentUser = Depends(Readers),
                db: Session = Depends(get_db)):
    sid = scoped_society_id(user, society_id)
    stmt = select(WorshipPlace).order_by(WorshipPlace.kind, WorshipPlace.name)
    if sid is not None:
        stmt = stmt.where(WorshipPlace.society_id == sid)
    if kind:
        stmt = stmt.where(WorshipPlace.kind == kind)
    if user.is_resident:
        stmt = stmt.where(WorshipPlace.is_active.is_(True))
    return db.scalars(stmt).all()


@router.get("/{place_id}", response_model=PlaceOut)
def get_place(place_id: int, user: CurrentUser = Depends(Readers), db: Session = Depends(get_db)):
    return _get(db, user, place_id)


@router.post("", response_model=PlaceOut, status_code=201)
def add_place(data: PlaceCreate, society_id: int | None = None, user: CurrentUser = Depends(Managers),
              db: Session = Depends(get_db)):
    sid = user.society_id if user.is_society_admin else society_id
    if sid is None:
        raise bad_request("society_id is required")
    get_society_or_404(db, sid)
    place = WorshipPlace(**data.model_dump(), society_id=sid, photos=[], created_by_type=user.user_type,
                         created_by=user.id)
    db.add(place)
    db.flush()
    log_activity(db, user, "worship_place.create", "worship_place", place.id, new=snapshot(place), society_id=sid)
    db.commit()
    return place


@router.patch("/{place_id}", response_model=PlaceOut)
def update_place(place_id: int, data: PlaceUpdate, user: CurrentUser = Depends(Managers),
                 db: Session = Depends(get_db)):
    place = _get(db, user, place_id, manage=True)
    old = snapshot(place)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(place, key, value)
    log_activity(db, user, "worship_place.update", "worship_place", place.id, old=old, new=snapshot(place),
                 society_id=place.society_id)
    db.commit()
    return place


@router.post("/{place_id}/photos", response_model=PlaceOut)
def add_photo(place_id: int, photo: UploadFile = File(...), replace: str | None = None,
              user: CurrentUser = Depends(Managers), db: Session = Depends(get_db)):
    """Add a photo, or (replace=<path of an existing photo>) swap that photo for the new one.

    A replaced photo takes the old one's place in the list and the old file is deleted from storage and from the
    file registry, so replacing never leaves an extra copy behind.
    """
    place = _get(db, user, place_id, manage=True)
    photos = list(place.photos or [])
    if replace is not None and replace not in photos:
        raise not_found("Photo")
    if replace is None and len(photos) >= MAX_PHOTOS:
        raise bad_request(f"At most {MAX_PHOTOS} photos per place")
    path = save_upload(photo, "worship_places", IMAGE_TYPES, db=db, user=user, audience="society",
                       society_id=place.society_id, category="worship_places")
    if replace is None:
        photos.append(path)
    else:
        photos[photos.index(replace)] = path
        release_upload(db, replace)
    place.photos = photos
    db.commit()
    return place


@router.delete("/{place_id}/photos", response_model=PlaceOut)
def remove_photo(place_id: int, path: str, user: CurrentUser = Depends(Managers), db: Session = Depends(get_db)):
    place = _get(db, user, place_id, manage=True)
    if path not in (place.photos or []):
        raise not_found("Photo")
    place.photos = [p for p in place.photos if p != path]
    release_upload(db, path)
    db.commit()
    return place


@router.delete("/{place_id}", status_code=204)
def delete_place(place_id: int, user: CurrentUser = Depends(Managers), db: Session = Depends(get_db)):
    place = _get(db, user, place_id, manage=True)
    for path in place.photos or []:
        release_upload(db, path)
    log_activity(db, user, "worship_place.delete", "worship_place", place.id, old=snapshot(place),
                 society_id=place.society_id)
    db.delete(place)
    db.commit()
