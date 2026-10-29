from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import AccountStatus, ResidentStatus, UserType
from app.core.errors import bad_request, forbidden, unauthorized
from app.core.security import (
    create_access_token,
    create_registration_token,
    generate_otp,
    hash_otp,
    verify_otp_hash,
)
from app.core.timeutils import now_ist
from app.integrations.messaging import send_sms
from app.modules.auth.models import OtpLog
from app.modules.residents.models import Resident
from app.modules.service_partners.models import ServicePartner
from app.modules.society_admins.models import SocietyAdmin
from app.modules.staff.models import Staff
from app.modules.super_admins.models import SuperAdmin

_MODEL_BY_TYPE = {
    UserType.SUPER_ADMIN: SuperAdmin,
    UserType.SOCIETY_ADMIN: SocietyAdmin,
    UserType.RESIDENT: Resident,
    UserType.STAFF: Staff,
    UserType.PARTNER: ServicePartner,
}


def pick_resident(db: Session, mobile: str) -> Resident | None:
    """A mobile can have several homes. Sign in to an approved one (the one used last), else a waiting one."""
    rows = db.scalars(select(Resident).where(Resident.mobile == mobile)).all()
    if not rows:
        return None
    rank = {ResidentStatus.ACTIVE: 0, ResidentStatus.PENDING_APPROVAL: 1, ResidentStatus.REJECTED: 2}
    return sorted(rows, key=lambda r: (rank.get(r.status, 3), -(r.last_login_at.timestamp() if r.last_login_at else 0),
                                       -r.id))[0]


def find_account_by_mobile(db: Session, user_type: str, mobile: str):
    if user_type == UserType.RESIDENT:
        return pick_resident(db, mobile)
    model = _MODEL_BY_TYPE[user_type]
    return db.scalar(select(model).where(model.mobile == mobile))


def send_otp(db: Session, mobile: str, user_type: str, purpose: str = "login") -> str:
    window_start = now_ist() - timedelta(minutes=settings.OTP_WINDOW_MINUTES)
    recent = db.scalar(
        select(func.count()).select_from(OtpLog).where(OtpLog.mobile == mobile, OtpLog.created_at >= window_start)
    )
    if recent >= settings.OTP_MAX_PER_WINDOW:
        raise bad_request("Too many OTP requests. Please try again after some time.")

    otp = generate_otp()
    db.add(OtpLog(mobile=mobile, user_type=user_type, purpose=purpose, otp_hash=hash_otp(mobile, otp),
                  expires_at=now_ist() + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)))
    db.commit()
    send_sms(mobile, f"{otp} is your Odeslo verification code. Valid for {settings.OTP_EXPIRE_MINUTES} minutes.")
    return otp


def _is_test_otp(otp: str) -> bool:
    return bool(settings.TEST_OTP) and settings.OTP_DEV_MODE and settings.ENVIRONMENT != "production" and otp == settings.TEST_OTP


def verify_otp(db: Session, mobile: str, otp: str, purpose: str = "login") -> None:
    if _is_test_otp(otp):  # development only: the fixed test code works without asking for an SMS
        for pending in db.scalars(select(OtpLog).where(OtpLog.mobile == mobile, OtpLog.purpose == purpose, OtpLog.verified_at.is_(None))):
            pending.verified_at = now_ist()
        db.commit()
        return
    log = db.scalar(
        select(OtpLog)
        .where(OtpLog.mobile == mobile, OtpLog.purpose == purpose, OtpLog.verified_at.is_(None))
        .order_by(OtpLog.id.desc())
    )
    if not log or log.expires_at < now_ist():
        raise bad_request("OTP expired or not requested. Please request a new OTP.")
    if log.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise bad_request("Too many wrong attempts. Please request a new OTP.")
    if not verify_otp_hash(mobile, otp, log.otp_hash):
        log.attempts += 1
        db.commit()
        raise bad_request("Invalid OTP")
    log.verified_at = now_ist()
    db.commit()


def ensure_can_login(user_type: str, account) -> None:
    if user_type == UserType.RESIDENT:
        if account.status == ResidentStatus.DEACTIVATED:
            raise forbidden("Your account has been deactivated. Please contact your society admin.")
        return
    if account.status != AccountStatus.ACTIVE:
        raise forbidden("Your account is inactive. Please contact the administrator.")
    if user_type == UserType.PARTNER and not account.verified:
        raise forbidden("Your service partner account is not verified yet.")


def otp_login(db: Session, mobile: str, otp: str, user_type: str) -> dict:
    account = find_account_by_mobile(db, user_type, mobile)
    verify_otp(db, mobile, otp)

    if not account:
        if user_type in (UserType.RESIDENT, UserType.PARTNER):
            return {"registration_required": True, "registration_token": create_registration_token(mobile)}
        raise unauthorized("No account found for this mobile number")

    ensure_can_login(user_type, account)
    account.last_login_at = now_ist()
    db.commit()
    return token_response(user_type, account)


def token_response(user_type: str, account) -> dict:
    return {
        "access_token": create_access_token(user_type, account.id),
        "user_type": user_type,
        "user_id": account.id,
        "status": account.status,
        "must_set_password": bool(getattr(account, "must_set_password", False)),
    }


# Order in which the app opens an account when one mobile has several roles.
MOBILE_LOGIN_PRIORITY = (UserType.SOCIETY_ADMIN, UserType.PARTNER, UserType.STAFF, UserType.RESIDENT)


def mobile_login(db: Session, mobile: str, otp: str) -> dict:
    """Verify the OTP once, then log in to every account registered on this mobile.

    No account at all -> new user: registration token for the resident sign-up flow.
    """
    verify_otp(db, mobile, otp)
    accounts, blocked = [], None
    for user_type in MOBILE_LOGIN_PRIORITY:
        account = find_account_by_mobile(db, user_type, mobile)
        if not account:
            continue
        try:
            ensure_can_login(user_type, account)
        except HTTPException as exc:
            blocked = blocked or exc
            continue
        account.last_login_at = now_ist()
        accounts.append({**token_response(user_type, account), "name": account.name})
    db.commit()

    if accounts:
        primary = accounts[0]
        return {
            **{k: v for k, v in primary.items() if k != "name"},
            "accounts": [
                {"user_type": a["user_type"], "user_id": a["user_id"], "name": a["name"], "status": a["status"],
                 "access_token": a["access_token"], "must_set_password": a["must_set_password"]}
                for a in accounts
            ],
        }
    if blocked:
        raise blocked
    return {"registration_required": True, "registration_token": create_registration_token(mobile)}
