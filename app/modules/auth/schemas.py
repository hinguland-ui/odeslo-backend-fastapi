from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from app.core.config import settings
from app.core.types import Mobile

OtpUserType = Literal["super_admin", "society_admin", "resident", "staff", "partner"]


class OtpSendRequest(BaseModel):
    mobile: Mobile
    user_type: OtpUserType


class OtpSendResponse(BaseModel):
    message: str
    expires_in_seconds: int
    dev_otp: str | None = None  # only when OTP_DEV_MODE is on


class OtpVerifyRequest(BaseModel):
    mobile: Mobile
    otp: str = Field(min_length=4, max_length=8)
    user_type: OtpUserType


class SuperAdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class SuperAdminVerifyRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=4, max_length=8)


class PasswordLoginRequest(BaseModel):
    email: EmailStr
    password: str


class SetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=settings.PASSWORD_MIN_LENGTH, max_length=128)
    current_password: str | None = None


class AuthResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    user_type: str | None = None
    user_id: int | None = None
    status: str | None = None
    must_set_password: bool = False
    # Resident whose mobile is not registered yet: use registration_token on POST /residents/register
    registration_required: bool = False
    registration_token: str | None = None


class MeResponse(BaseModel):
    user_type: str
    id: int
    name: str
    mobile: str | None
    status: str
    society_id: int | None
    unit_id: int | None
    role: str | None
    permissions: list[str]
    resident_type: str | None
    profile: dict[str, Any]


class LogoutRequest(BaseModel):
    fcm_token: str | None = None


# ---------- One login for every mobile user (app) ----------
class MobileOtpSendRequest(BaseModel):
    mobile: Mobile


class MobileOtpVerifyRequest(BaseModel):
    mobile: Mobile
    otp: str = Field(min_length=4, max_length=8)


class AccountOption(BaseModel):
    user_type: str
    user_id: int
    name: str
    status: str
    access_token: str
    must_set_password: bool = False


class MobileAuthResponse(AuthResponse):
    """Primary account (society admin > partner > staff > resident) plus every account on this mobile."""
    accounts: list[AccountOption] = []
