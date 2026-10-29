from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.enums import AccountStatus, UserType
from app.core.errors import bad_request, conflict, not_found
from app.core.utils import diff, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.super_admins.models import SuperAdmin
from app.modules.super_admins.schemas import SuperAdminCreate, SuperAdminOut, SuperAdminUpdate
from app.modules.super_admins.service import create_super_admin

router = APIRouter(prefix="/super-admins", tags=["Super Admins"])
SuperAdminOnly = require_roles(UserType.SUPER_ADMIN)


@router.get("/me", response_model=SuperAdminOut)
def get_my_profile(user: CurrentUser = Depends(SuperAdminOnly)):
    return user.entity


@router.patch("/me", response_model=SuperAdminOut)
def update_my_profile(data: SuperAdminUpdate, user: CurrentUser = Depends(SuperAdminOnly),
                      db: Session = Depends(get_db)):
    admin: SuperAdmin = user.entity
    before = snapshot(admin)
    values = data.model_dump(exclude_unset=True)
    if "mobile" in values and db.scalar(select(SuperAdmin).where(SuperAdmin.mobile == values["mobile"],
                                                                 SuperAdmin.id != admin.id)):
        raise conflict("Mobile number already in use")
    for k, v in values.items():
        setattr(admin, k, v)
    db.flush()
    old, new = diff(before, snapshot(admin))
    log_activity(db, user, "super_admin.update_profile", "super_admin", admin.id, old, new)
    db.commit()
    return admin


@router.get("", response_model=list[SuperAdminOut])
def list_super_admins(user: CurrentUser = Depends(SuperAdminOnly), db: Session = Depends(get_db)):
    return db.scalars(select(SuperAdmin).order_by(SuperAdmin.id)).all()


@router.post("", response_model=SuperAdminOut, status_code=201)
def add_super_admin(data: SuperAdminCreate, user: CurrentUser = Depends(SuperAdminOnly),
                    db: Session = Depends(get_db)):
    admin = create_super_admin(db, data.name, data.email, data.mobile, data.password)
    log_activity(db, user, "super_admin.create", "super_admin", admin.id, new=snapshot(admin))
    db.commit()
    return admin


@router.post("/{admin_id}/deactivate", response_model=SuperAdminOut)
def deactivate_super_admin(admin_id: int, user: CurrentUser = Depends(SuperAdminOnly),
                           db: Session = Depends(get_db)):
    admin = db.get(SuperAdmin, admin_id)
    if not admin:
        raise not_found("Super admin")
    if admin.id == user.id:
        raise bad_request("You cannot deactivate your own account")
    admin.status = AccountStatus.INACTIVE
    log_activity(db, user, "super_admin.deactivate", "super_admin", admin.id, new={"status": admin.status})
    db.commit()
    return admin
