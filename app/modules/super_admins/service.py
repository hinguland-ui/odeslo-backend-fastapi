from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import conflict
from app.core.security import hash_password
from app.modules.super_admins.models import SuperAdmin


def create_super_admin(db: Session, name: str, email: str, mobile: str, password: str) -> SuperAdmin:
    email = email.lower()
    if db.scalar(select(SuperAdmin).where(or_(SuperAdmin.email == email, SuperAdmin.mobile == mobile))):
        raise conflict("A super admin with this email or mobile already exists")
    admin = SuperAdmin(name=name, email=email, mobile=mobile, password_hash=hash_password(password))
    db.add(admin)
    db.flush()
    return admin
