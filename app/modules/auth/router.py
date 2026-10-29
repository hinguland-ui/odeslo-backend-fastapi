from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user
from app.core.enums import UserType
from app.core.errors import bad_request, forbidden, unauthorized
from app.core.security import hash_password, verify_password
from app.core.timeutils import now_ist
from app.core.utils import snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.auth import service
from app.modules.auth.schemas import (
    AuthResponse,
    LogoutRequest,
    MeResponse,
    MobileAuthResponse,
    MobileOtpSendRequest,
    MobileOtpVerifyRequest,
    OtpSendRequest,
    OtpSendResponse,
    OtpVerifyRequest,
    PasswordLoginRequest,
    SetPasswordRequest,
    SuperAdminLoginRequest,
    SuperAdminVerifyRequest,
)
from app.modules.notifications.models import DeviceToken
from app.modules.society_admins.models import SocietyAdmin
from app.modules.super_admins.models import SuperAdmin

router = APIRouter(prefix="/auth", tags=["Auth"])


def _otp_response(otp: str, message: str) -> dict:
    return {
        "message": message,
        "expires_in_seconds": settings.OTP_EXPIRE_MINUTES * 60,
        "dev_otp": otp if settings.OTP_DEV_MODE else None,
    }


# ---------- Mobile OTP (Super Admin, Society Admin, Resident, Staff, Service Partner) ----------
@router.post("/otp/send", response_model=OtpSendResponse)
def send_login_otp(data: OtpSendRequest, db: Session = Depends(get_db)):
    account = service.find_account_by_mobile(db, data.user_type, data.mobile)
    if data.user_type not in (UserType.RESIDENT, UserType.PARTNER):
        # residents and vendors may sign up with OTP; every other login type must be created by an admin first
        if not account:
            raise bad_request("No account found for this mobile number")
        service.ensure_can_login(data.user_type, account)
    elif data.user_type == UserType.PARTNER and account:
        service.ensure_can_login(data.user_type, account)  # existing vendor: must be active and verified
    otp = service.send_otp(db, data.mobile, data.user_type)
    return _otp_response(otp, "OTP sent to your mobile number")


@router.post("/otp/verify", response_model=AuthResponse)
def verify_login_otp(data: OtpVerifyRequest, db: Session = Depends(get_db)):
    return service.otp_login(db, data.mobile, data.otp, data.user_type)


# ---------- Super Admin: email + password + OTP (2FA) ----------
@router.post("/super-admin/login", response_model=OtpSendResponse)
def super_admin_login(data: SuperAdminLoginRequest, db: Session = Depends(get_db)):
    admin = db.scalar(select(SuperAdmin).where(SuperAdmin.email == data.email.lower()))
    if not admin or not verify_password(data.password, admin.password_hash):
        raise unauthorized("Invalid email or password")
    service.ensure_can_login(UserType.SUPER_ADMIN, admin)
    otp = service.send_otp(db, admin.mobile, UserType.SUPER_ADMIN, purpose="2fa")
    return _otp_response(otp, "Password verified. OTP sent to your registered mobile.")


@router.post("/super-admin/verify", response_model=AuthResponse)
def super_admin_verify(data: SuperAdminVerifyRequest, db: Session = Depends(get_db)):
    admin = db.scalar(select(SuperAdmin).where(SuperAdmin.email == data.email.lower()))
    if not admin:
        raise unauthorized("Invalid login")
    service.verify_otp(db, admin.mobile, data.otp, purpose="2fa")
    service.ensure_can_login(UserType.SUPER_ADMIN, admin)
    admin.last_login_at = now_ist()
    db.commit()
    return service.token_response(UserType.SUPER_ADMIN, admin)


# ---------- Society Admin: email + password ----------
@router.post("/society-admin/login", response_model=AuthResponse)
def society_admin_password_login(data: PasswordLoginRequest, db: Session = Depends(get_db)):
    admin = db.scalar(select(SocietyAdmin).where(SocietyAdmin.email == data.email.lower()))
    if not admin or not verify_password(data.password, admin.password_hash):
        raise unauthorized("Invalid email or password. First login? Use mobile OTP and set your password.")
    service.ensure_can_login(UserType.SOCIETY_ADMIN, admin)
    admin.last_login_at = now_ist()
    db.commit()
    return service.token_response(UserType.SOCIETY_ADMIN, admin)


@router.post("/set-password")
def set_password(data: SetPasswordRequest, user: CurrentUser = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if user.user_type not in (UserType.SOCIETY_ADMIN, UserType.SUPER_ADMIN):
        raise forbidden("Password login is not available for this account type")
    account = user.entity
    first_time = user.user_type == UserType.SOCIETY_ADMIN and account.must_set_password
    if not first_time and not verify_password(data.current_password or "", account.password_hash):
        raise bad_request("Current password is incorrect")
    account.password_hash = hash_password(data.new_password)
    if user.user_type == UserType.SOCIETY_ADMIN:
        account.must_set_password = False
    log_activity(db, user, "auth.password_set", user.user_type, user.id)
    db.commit()
    return {"message": "Password updated"}


# ---------- Session ----------
@router.post("/ws-ticket")
def ws_ticket(user: CurrentUser = Depends(get_current_user)):
    """A one-minute, one-use ticket to open the live-updates socket (so the login token is never put in a URL)."""
    from app.core.security import create_ws_ticket

    return {"ticket": create_ws_ticket(user.user_type, user.id)}



@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "user_type": user.user_type,
        "id": user.id,
        "name": user.name,
        "mobile": user.mobile,
        "status": user.status,
        "society_id": user.society_id,
        "unit_id": user.unit_id,
        "role": user.role,
        "permissions": user.permissions,
        "resident_type": user.resident_type,
        "profile": snapshot(user.entity),
    }


@router.post("/logout")
def logout(data: LogoutRequest, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.fcm_token:
        db.execute(delete(DeviceToken).where(DeviceToken.fcm_token == data.fcm_token,
                                             DeviceToken.user_type == user.user_type,
                                             DeviceToken.user_id == user.id))
        db.commit()
    return {"message": "Logged out"}


# ---------- Mobile app: one OTP login for residents, society admins, staff and service partners ----------
@router.post("/mobile/send", response_model=OtpSendResponse)
def send_mobile_otp(data: MobileOtpSendRequest, db: Session = Depends(get_db)):
    """OTP for any app user. New numbers are allowed (they sign up as residents after verifying)."""
    otp = service.send_otp(db, data.mobile, UserType.RESIDENT)
    return _otp_response(otp, "OTP sent to your mobile number")


@router.post("/mobile/verify", response_model=MobileAuthResponse)
def verify_mobile_otp(data: MobileOtpVerifyRequest, db: Session = Depends(get_db)):
    return service.mobile_login(db, data.mobile, data.otp)
