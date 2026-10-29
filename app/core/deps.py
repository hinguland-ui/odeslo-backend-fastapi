"""Authentication & authorisation dependencies.

Every API route must declare who may call it via `require_roles(...)`. The frontend is never trusted:
the user is reloaded from the DB on every request, and society scoping is enforced server-side.
"""

from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.enums import AccountStatus, AdminRole, ResidentStatus, SocietyStatus, UserType
from app.core.errors import forbidden, not_found, unauthorized
from app.core.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_type: str
    id: int
    name: str
    mobile: str | None
    status: str
    society_id: int | None = None
    unit_id: int | None = None
    role: str | None = None  # society admin role (primary / co_admin)
    permissions: list[str] = field(default_factory=list)
    resident_type: str | None = None
    is_primary: bool = False
    entity: Any = None

    @property
    def is_super_admin(self) -> bool:
        return self.user_type == UserType.SUPER_ADMIN

    @property
    def is_society_admin(self) -> bool:
        return self.user_type == UserType.SOCIETY_ADMIN

    @property
    def is_resident(self) -> bool:
        return self.user_type == UserType.RESIDENT

    @property
    def is_admin(self) -> bool:
        return self.is_super_admin or self.is_society_admin

    @property
    def is_primary_admin(self) -> bool:
        return self.is_society_admin and self.role == AdminRole.PRIMARY

    def has_perm(self, perm: str) -> bool:
        if self.is_super_admin:
            return True
        if self.is_society_admin:
            return self.role == AdminRole.PRIMARY or perm in (self.permissions or [])
        return False


def _load_user(db: Session, user_type: str, user_id: int) -> CurrentUser | None:
    from app.modules.residents.models import Resident
    from app.modules.service_partners.models import ServicePartner
    from app.modules.society_admins.models import SocietyAdmin
    from app.modules.staff.models import Staff
    from app.modules.super_admins.models import SuperAdmin

    if user_type == UserType.SUPER_ADMIN:
        u = db.get(SuperAdmin, user_id)
        return u and CurrentUser(user_type, u.id, u.name, u.mobile, u.status, entity=u)
    if user_type == UserType.SOCIETY_ADMIN:
        u = db.get(SocietyAdmin, user_id)
        return u and CurrentUser(
            user_type, u.id, u.name, u.mobile, u.status,
            society_id=u.society_id, unit_id=u.unit_id, role=u.role, permissions=list(u.permissions or []), entity=u,
        )
    if user_type == UserType.RESIDENT:
        u = db.get(Resident, user_id)
        return u and CurrentUser(
            user_type, u.id, u.name, u.mobile, u.status,
            society_id=u.society_id, unit_id=u.unit_id, resident_type=u.resident_type, is_primary=u.is_primary,
            entity=u,
        )
    if user_type == UserType.STAFF:
        u = db.get(Staff, user_id)
        return u and CurrentUser(user_type, u.id, u.name, u.mobile, u.status, society_id=u.society_id, entity=u)
    if user_type == UserType.PARTNER:
        u = db.get(ServicePartner, user_id)
        return u and CurrentUser(user_type, u.id, u.name, u.mobile, u.status, entity=u)
    return None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if not creds:
        raise unauthorized()
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise unauthorized("Invalid or expired token")
    if payload.get("scope") != "access":
        raise unauthorized("Invalid token scope")

    user = _load_user(db, payload.get("typ"), int(payload.get("sub")))
    if not user:
        raise unauthorized("User not found")
    db.info["actor"] = (user.user_type, user.society_id)  # who is writing -> which society gets the live refresh

    if user.is_resident:
        if user.status == ResidentStatus.DEACTIVATED:
            raise forbidden("Your account has been deactivated")
    elif user.status != AccountStatus.ACTIVE:
        raise forbidden("Your account is inactive")

    if user.society_id and not user.is_super_admin:
        from app.modules.societies.models import Society

        society = db.get(Society, user.society_id)
        if society and society.status == SocietyStatus.SUSPENDED:
            raise forbidden("This society is suspended. Please contact the platform administrator.")
    return user


def require_roles(*roles: str, perm: str | None = None, allow_limited_resident: bool = False):
    """Allow only the given user types.

    perm: for society admins, the co-admin permission needed (primary admins have all).
    allow_limited_resident: let residents whose approval is pending / rejected through
    (they only get the Masjid feature and their own profile).
    """

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.user_type not in roles:
            raise forbidden()
        if user.is_resident and user.status != ResidentStatus.ACTIVE and not allow_limited_resident:
            raise forbidden("Your account is pending approval by the society admin")
        if perm and user.is_society_admin and not user.has_perm(perm):
            raise forbidden(f"Missing permission: {perm}")
        return user

    return dependency


def get_registration_mobile(creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
                            db: Session = Depends(get_db)) -> str:
    """Mobile for resident registration: a registration token, or the login of a rejected resident re-applying."""
    if not creds:
        raise unauthorized()
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise unauthorized("Registration session expired, please verify OTP again")
    if payload.get("scope") == "registration":
        return payload["sub"]
    if payload.get("scope") == "access" and payload.get("typ") == UserType.RESIDENT:
        user = _load_user(db, UserType.RESIDENT, int(payload["sub"]))
        if user and user.status == ResidentStatus.REJECTED:
            return user.mobile
    raise unauthorized("Invalid registration token")


def scoped_society_id(user: CurrentUser, requested: int | None) -> int | None:
    """Society filter to apply: super admin may choose any (None = all); everybody else is locked to their own."""
    if user.is_super_admin:
        return requested
    if user.society_id is None:
        raise forbidden()
    if requested is not None and requested != user.society_id:
        raise forbidden("You can only access your own society")
    return user.society_id


def ensure_society_access(user: CurrentUser, society_id: int | None, what: str = "Resource") -> None:
    """Raise 404 (not 403, so IDs from other societies are not leaked) if the record belongs to another society."""
    if user.is_super_admin:
        return
    if society_id is None or society_id != user.society_id:
        raise not_found(what)
