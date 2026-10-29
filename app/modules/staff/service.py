from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AccountStatus
from app.core.errors import bad_request, conflict
from app.modules.service_partners.models import ServicePartner
from app.modules.staff.models import Staff


def ensure_staff_mobile_free(db: Session, mobile: str, exclude_id: int | None = None) -> None:
    stmt = select(Staff.id).where(Staff.mobile == mobile)
    if exclude_id:
        stmt = stmt.where(Staff.id != exclude_id)
    if db.scalar(stmt):
        raise conflict("A staff member with this mobile already exists")
    if db.scalar(select(ServicePartner.id).where(ServicePartner.mobile == mobile)):
        raise conflict("This mobile is registered as a service partner")


def get_active_staff_of_society(db: Session, staff_id: int, society_id: int) -> Staff:
    staff = db.get(Staff, staff_id)
    if not staff or staff.society_id != society_id or staff.status != AccountStatus.ACTIVE:
        raise bad_request("Staff member not found or inactive in this society")
    return staff
