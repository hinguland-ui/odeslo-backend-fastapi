from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.enums import AdminRole, Designation
from app.core.types import Mobile


class SocietyAdminCreate(BaseModel):
    society_id: int | None = None  # required for super admin; ignored for society admins (own society)
    name: str = Field(min_length=2, max_length=120)
    mobile: Mobile
    email: EmailStr
    designation: Designation
    role: AdminRole = AdminRole.CO_ADMIN
    # either explicit permissions or a preset ("treasurer" / "operations")
    permissions: list[str] | None = None
    preset: str | None = Field(None, pattern="^(treasurer|operations)$")
    unit_id: int | None = None


class SocietyAdminUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    email: EmailStr | None = None
    designation: Designation | None = None
    permissions: list[str] | None = None
    unit_id: int | None = None


class SocietyAdminProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    email: EmailStr | None = None
    unit_id: int | None = None


class SocietyAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    name: str
    mobile: str
    email: str
    designation: str
    role: str
    permissions: list[str]
    unit_id: int | None
    id_proof: str | None
    authorisation_letter: str | None
    must_set_password: bool
    status: str
    last_login_at: datetime | None
    created_at: datetime
