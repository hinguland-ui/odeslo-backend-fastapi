from typing import Literal

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import CO_ADMIN_PRESETS, AccountStatus, AdminRole, UserType
from app.core.errors import bad_request, forbidden, not_found
from app.core.files import DOCUMENT_TYPES, release_upload, save_upload
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.societies.models import Unit
from app.modules.societies.service import get_society_or_404
from app.modules.society_admins.models import SocietyAdmin
from app.modules.society_admins.schemas import (
    SocietyAdminCreate,
    SocietyAdminOut,
    SocietyAdminProfileUpdate,
    SocietyAdminUpdate,
)
from app.modules.society_admins.service import (
    create_society_admin,
    ensure_unique_contact,
    send_invite,
    validate_permissions,
)

router = APIRouter(prefix="/society-admins", tags=["Society Admins"])
Admins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN)
SocietyAdminOnly = require_roles(UserType.SOCIETY_ADMIN)


def _get_manageable(db: Session, user: CurrentUser, admin_id: int) -> SocietyAdmin:
    """Super admin manages everyone; a primary admin manages only co-admins of their own society."""
    admin = db.get(SocietyAdmin, admin_id)
    if not admin:
        raise not_found("Society admin")
    ensure_society_access(user, admin.society_id, "Society admin")
    if user.is_society_admin and (not user.is_primary_admin or admin.role == AdminRole.PRIMARY):
        raise forbidden("Only the primary admin can manage co-admins")
    return admin


# ---------- own profile ----------
@router.get("/me", response_model=SocietyAdminOut)
def my_profile(user: CurrentUser = Depends(SocietyAdminOnly)):
    return user.entity


@router.patch("/me", response_model=SocietyAdminOut)
def update_my_profile(data: SocietyAdminProfileUpdate, user: CurrentUser = Depends(SocietyAdminOnly),
                      db: Session = Depends(get_db)):
    admin: SocietyAdmin = user.entity
    before = snapshot(admin)
    values = data.model_dump(exclude_unset=True)
    if "email" in values:
        values["email"] = values["email"].lower()
        ensure_unique_contact(db, admin.mobile, values["email"], exclude_id=admin.id)
    if values.get("unit_id") is not None:
        unit = db.get(Unit, values["unit_id"])
        if not unit or unit.society_id != admin.society_id:
            raise bad_request("Unit does not belong to your society")
    for k, v in values.items():
        setattr(admin, k, v)
    db.flush()
    old, new = diff(before, snapshot(admin))
    log_activity(db, user, "society_admin.update_profile", "society_admin", admin.id, old, new)
    db.commit()
    return admin


@router.post("/me/documents/{doc}", response_model=SocietyAdminOut)
def upload_my_document(doc: Literal["id_proof", "authorisation_letter"], file: UploadFile = File(...),
                       user: CurrentUser = Depends(SocietyAdminOnly), db: Session = Depends(get_db)):
    admin: SocietyAdmin = user.entity
    old_file = getattr(admin, doc)
    setattr(admin, doc, save_upload(file, "society_admins", DOCUMENT_TYPES, db=db, user=user, audience="admin",
                                    society_id=admin.society_id, category="society_admins"))
    release_upload(db, old_file)
    log_activity(db, user, f"society_admin.upload_{doc}", "society_admin", admin.id)
    db.commit()
    return admin


# ---------- management ----------
@router.post("", response_model=SocietyAdminOut, status_code=201)
def add_society_admin(data: SocietyAdminCreate, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    if user.is_society_admin:
        if not user.is_primary_admin:
            raise forbidden("Only the primary admin can add co-admins")
        if data.role != AdminRole.CO_ADMIN:
            raise forbidden("Society admins can only add co-admins")
        society_id = user.society_id
    else:
        if not data.society_id:
            raise bad_request("society_id is required")
        society_id = data.society_id
    society = get_society_or_404(db, society_id)
    permissions = data.permissions
    if permissions is None and data.preset:
        permissions = [str(p) for p in CO_ADMIN_PRESETS[data.preset]]
    if data.role == AdminRole.CO_ADMIN and not permissions:
        raise bad_request("Co-admins need permissions or a preset (treasurer / operations)")
    admin = create_society_admin(
        db, society, name=data.name, mobile=data.mobile, email=data.email, designation=data.designation,
        role=data.role, permissions=permissions, unit_id=data.unit_id,
        created_by_type=user.user_type, created_by_id=user.id,
    )
    log_activity(db, user, "society_admin.create", "society_admin", admin.id, new=snapshot(admin),
                 society_id=society.id)
    db.commit()
    return admin


@router.get("", response_model=list[SocietyAdminOut])
def list_society_admins(society_id: int | None = None, status: str | None = None,
                        user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    stmt = select(SocietyAdmin).order_by(SocietyAdmin.society_id, SocietyAdmin.role.desc(), SocietyAdmin.id)
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(SocietyAdmin.society_id == sid)
    if status:
        stmt = stmt.where(SocietyAdmin.status == status)
    return db.scalars(stmt).all()


@router.get("/{admin_id}", response_model=SocietyAdminOut)
def get_society_admin(admin_id: int, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    admin = db.get(SocietyAdmin, admin_id)
    if not admin:
        raise not_found("Society admin")
    ensure_society_access(user, admin.society_id, "Society admin")
    return admin


@router.patch("/{admin_id}", response_model=SocietyAdminOut)
def update_society_admin(admin_id: int, data: SocietyAdminUpdate, user: CurrentUser = Depends(Admins),
                         db: Session = Depends(get_db)):
    admin = _get_manageable(db, user, admin_id)
    before = snapshot(admin)
    values = data.model_dump(exclude_unset=True)
    if "email" in values:
        values["email"] = values["email"].lower()
        ensure_unique_contact(db, admin.mobile, values["email"], exclude_id=admin.id)
    if "permissions" in values:
        if admin.role == AdminRole.PRIMARY:
            values.pop("permissions")  # primary always has full access
        else:
            values["permissions"] = validate_permissions(values["permissions"] or [])
    for k, v in values.items():
        setattr(admin, k, v)
    db.flush()
    old, new = diff(before, snapshot(admin))
    log_activity(db, user, "society_admin.update", "society_admin", admin.id, old, new, society_id=admin.society_id)
    db.commit()
    return admin


@router.post("/{admin_id}/deactivate", response_model=SocietyAdminOut)
def deactivate_society_admin(admin_id: int, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    admin = _get_manageable(db, user, admin_id)
    admin.status = AccountStatus.INACTIVE
    log_activity(db, user, "society_admin.deactivate", "society_admin", admin.id, new={"status": admin.status},
                 society_id=admin.society_id)
    db.commit()
    return admin


@router.post("/{admin_id}/activate", response_model=SocietyAdminOut)
def activate_society_admin(admin_id: int, user: CurrentUser = Depends(Admins), db: Session = Depends(get_db)):
    admin = _get_manageable(db, user, admin_id)
    admin.status = AccountStatus.ACTIVE
    log_activity(db, user, "society_admin.activate", "society_admin", admin.id, new={"status": admin.status},
                 society_id=admin.society_id)
    db.commit()
    return admin


@router.post("/{admin_id}/reset-access", response_model=SocietyAdminOut)
def reset_society_admin_access(admin_id: int, user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN)),
                               db: Session = Depends(get_db)):
    """Clear the password and resend the invite; the admin must log in with OTP and set a new password."""
    admin = db.get(SocietyAdmin, admin_id)
    if not admin:
        raise not_found("Society admin")
    admin.password_hash = None
    admin.must_set_password = True
    send_invite(admin, get_society_or_404(db, admin.society_id))
    log_activity(db, user, "society_admin.reset_access", "society_admin", admin.id, society_id=admin.society_id)
    db.commit()
    return admin
