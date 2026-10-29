from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import AccountStatus, AdminPermission, AdminRole
from app.core.errors import bad_request, conflict
from app.integrations.messaging import send_email, send_sms, send_whatsapp
from app.modules.societies.models import Society, Unit
from app.modules.society_admins.models import SocietyAdmin

VALID_PERMISSIONS = {p.value for p in AdminPermission}


def validate_permissions(permissions: list[str]) -> list[str]:
    invalid = set(permissions) - VALID_PERMISSIONS
    if invalid:
        raise bad_request(f"Invalid permissions: {', '.join(sorted(invalid))}")
    return sorted(set(permissions))


def ensure_unique_contact(db: Session, mobile: str, email: str, exclude_id: int | None = None) -> None:
    stmt = select(SocietyAdmin).where(or_(SocietyAdmin.mobile == mobile, SocietyAdmin.email == email))
    if exclude_id:
        stmt = stmt.where(SocietyAdmin.id != exclude_id)
    if db.scalar(stmt):
        raise conflict("A society admin with this mobile or email already exists")


def create_society_admin(
    db: Session,
    society: Society,
    *,
    name: str,
    mobile: str,
    email: str,
    designation: str,
    role: str,
    permissions: list[str] | None,
    unit_id: int | None,
    created_by_type: str,
    created_by_id: int,
) -> SocietyAdmin:
    email = email.lower()
    ensure_unique_contact(db, mobile, email)
    if role == AdminRole.PRIMARY and db.scalar(select(SocietyAdmin.id).where(
            SocietyAdmin.society_id == society.id, SocietyAdmin.role == AdminRole.PRIMARY,
            SocietyAdmin.status == AccountStatus.ACTIVE)):
        raise conflict("This society already has an active primary admin")
    if unit_id is not None:
        unit = db.get(Unit, unit_id)
        if not unit or unit.society_id != society.id:
            raise bad_request("Unit does not belong to this society")
    admin = SocietyAdmin(
        society_id=society.id,
        name=name,
        mobile=mobile,
        email=email,
        designation=designation,
        role=role,
        permissions=[] if role == AdminRole.PRIMARY else validate_permissions(permissions or []),
        unit_id=unit_id,
        must_set_password=True,
        created_by_type=created_by_type,
        created_by_id=created_by_id,
    )
    db.add(admin)
    db.flush()
    send_invite(admin, society)
    return admin


def send_invite(admin: SocietyAdmin, society: Society) -> None:
    link = f"{settings.ADMIN_PANEL_URL}/login"
    text = (f"Hello {admin.name}, you have been added as {admin.designation.title()} admin of {society.name} "
            f"on Odeslo. Login with your mobile {admin.mobile} (OTP) at {link} and set your password.")
    send_sms(admin.mobile, text)
    send_whatsapp(admin.mobile, text)
    send_email(admin.email, f"Your {society.name} admin access", text)
