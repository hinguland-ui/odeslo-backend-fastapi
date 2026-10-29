from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AccountStatus, AdminPermission, Feature, StaffType, UserType
from app.core.errors import bad_request, not_found
from app.core.files import DOCUMENT_TYPES, IMAGE_TYPES, release_upload, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.utils import diff, normalize_mobile, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.societies.service import ensure_feature_enabled
from app.modules.staff.models import Staff
from app.modules.staff.schemas import StaffOut, StaffPublicOut, StaffUpdate
from app.modules.staff.service import ensure_staff_mobile_free

router = APIRouter(prefix="/staff", tags=["Staff"])

StaffManagers = require_roles(UserType.SOCIETY_ADMIN, perm=AdminPermission.STAFF)
StaffViewers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.STAFF)
StaffOnly = require_roles(UserType.STAFF)


def _get_staff(db: Session, user: CurrentUser, staff_id: int) -> Staff:
    staff = db.get(Staff, staff_id)
    if not staff:
        raise not_found("Staff")
    ensure_society_access(user, staff.society_id, "Staff")
    return staff


@router.get("/me", response_model=StaffOut)
def my_profile(user: CurrentUser = Depends(StaffOnly)):
    return user.entity


@router.post("", response_model=StaffOut, status_code=201)
def add_staff(
    name: str = Form(..., min_length=2, max_length=120),
    mobile: str = Form(...),
    type: StaffType = Form(...),
    category: str | None = Form(None),
    id_proof: UploadFile = File(...),
    photo: UploadFile = File(...),
    user: CurrentUser = Depends(StaffManagers),
    db: Session = Depends(get_db),
):
    """Society staff (guard, housekeeping, plumber, electrician...). ID proof and photo are mandatory."""
    ensure_feature_enabled(db, user.society_id, Feature.STAFF)
    try:
        mobile = normalize_mobile(mobile)
    except ValueError as exc:
        raise bad_request(str(exc))
    ensure_staff_mobile_free(db, mobile)
    staff = Staff(
        society_id=user.society_id, name=name, mobile=mobile, type=type, category=category,
        id_proof=save_upload(id_proof, "staff/id_proofs", DOCUMENT_TYPES, db=db, user=user, audience="admin", society_id=user.society_id, category="staff_documents"),
        photo=save_upload(photo, "staff/photos", IMAGE_TYPES, db=db, user=user, audience="admin", society_id=user.society_id, category="staff_photos"), created_by=user.id,
    )
    db.add(staff)
    db.flush()
    log_activity(db, user, "staff.create", "staff", staff.id, new=snapshot(staff))
    db.commit()
    return staff


@router.get("", response_model=Page[StaffOut])
def list_staff(society_id: int | None = None, type: str | None = None, status: str | None = None,
               params: PageParams = Depends(), user: CurrentUser = Depends(StaffViewers),
               db: Session = Depends(get_db)):
    stmt = select(Staff).order_by(Staff.name)
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Staff.society_id == sid)
    if type:
        stmt = stmt.where(Staff.type == type)
    if status:
        stmt = stmt.where(Staff.status == status)
    return paginate(db, stmt, params)


@router.get("/directory", response_model=list[StaffPublicOut])
def staff_directory(user: CurrentUser = Depends(require_roles(UserType.RESIDENT)), db: Session = Depends(get_db)):
    """Active staff of the resident's society (My Society screen)."""
    return db.scalars(select(Staff).where(Staff.society_id == user.society_id, Staff.status == AccountStatus.ACTIVE)
                      .order_by(Staff.type, Staff.name)).all()


@router.get("/{staff_id}", response_model=StaffOut)
def get_staff(staff_id: int, user: CurrentUser = Depends(StaffViewers), db: Session = Depends(get_db)):
    return _get_staff(db, user, staff_id)


@router.patch("/{staff_id}", response_model=StaffOut)
def update_staff(staff_id: int, data: StaffUpdate, user: CurrentUser = Depends(StaffManagers),
                 db: Session = Depends(get_db)):
    staff = _get_staff(db, user, staff_id)
    before = snapshot(staff)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(staff, k, v)
    db.flush()
    old, new = diff(before, snapshot(staff))
    log_activity(db, user, "staff.update", "staff", staff.id, old, new)
    db.commit()
    return staff


@router.post("/{staff_id}/documents", response_model=StaffOut)
def replace_staff_documents(staff_id: int, id_proof: UploadFile | None = File(None),
                            photo: UploadFile | None = File(None), user: CurrentUser = Depends(StaffManagers),
                            db: Session = Depends(get_db)):
    staff = _get_staff(db, user, staff_id)
    if not id_proof and not photo:
        raise bad_request("Upload id_proof and/or photo")
    if id_proof:
        old_id = staff.id_proof
        staff.id_proof = save_upload(id_proof, "staff/id_proofs", DOCUMENT_TYPES, db=db, user=user, audience="admin", society_id=staff.society_id, category="staff_documents")
        release_upload(db, old_id)
    if photo:
        old_photo = staff.photo
        staff.photo = save_upload(photo, "staff/photos", IMAGE_TYPES, db=db, user=user, audience="admin", society_id=staff.society_id, category="staff_photos")
        release_upload(db, old_photo)
    log_activity(db, user, "staff.update_documents", "staff", staff.id)
    db.commit()
    return staff


@router.post("/{staff_id}/deactivate", response_model=StaffOut)
def deactivate_staff(staff_id: int, user: CurrentUser = Depends(StaffManagers), db: Session = Depends(get_db)):
    staff = _get_staff(db, user, staff_id)
    staff.status = AccountStatus.INACTIVE
    log_activity(db, user, "staff.deactivate", "staff", staff.id, new={"status": staff.status})
    db.commit()
    return staff


@router.post("/{staff_id}/activate", response_model=StaffOut)
def activate_staff(staff_id: int, user: CurrentUser = Depends(StaffManagers), db: Session = Depends(get_db)):
    staff = _get_staff(db, user, staff_id)
    staff.status = AccountStatus.ACTIVE
    log_activity(db, user, "staff.activate", "staff", staff.id, new={"status": staff.status})
    db.commit()
    return staff
