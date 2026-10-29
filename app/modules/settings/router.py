from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core import upload_limits
from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.enums import UserType
from app.core.errors import bad_request, not_found
from app.integrations.payment_gateway import RazorpayGateway, load_config
from app.modules.activity_logs.service import log_activity
from app.modules.settings.schemas import AppConfigOut, EmailTest, SettingOut, SettingUpdate, SmsTest
from app.modules.settings.service import DEFAULT_SETTINGS, get_setting, mask_secrets, secret_values, set_setting

router = APIRouter(prefix="/settings", tags=["Platform Settings"])
SuperAdmin = require_roles(UserType.SUPER_ADMIN)


@router.get("/app-config", response_model=AppConfigOut)
def public_app_config(db: Session = Depends(get_db)):
    """Public config for the apps (version check, prayer method, payment key id)."""
    cfg = load_config(db)
    return {
        "app_version": get_setting(db, "app_version"),
        "prayer_calculation": get_setting(db, "prayer_calculation"),
        "masjid": get_setting(db, "masjid"),
        "payment_gateway": cfg.provider,
        "payment_key_id": cfg.key_id if cfg.provider == "razorpay" else "mock_key",
        "payment_mode": cfg.mode,
    }


@router.get("/upload-limits")
def public_upload_limits():
    """Max sizes and allowed formats, so forms can warn before uploading. Public, contains nothing secret."""
    return {**upload_limits.current_limits(), "hard_max_mb": upload_limits.HARD_MAX_MB}


@router.get("", response_model=list[SettingOut])
def list_settings(user: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    return [{"key": k, "value": mask_secrets(get_setting(db, k))} for k in DEFAULT_SETTINGS]


@router.put("/{key}", response_model=SettingOut)
def update_setting(key: str, data: SettingUpdate, user: CurrentUser = Depends(SuperAdmin),
                   db: Session = Depends(get_db)):
    if key not in DEFAULT_SETTINGS:
        raise not_found("Setting")
    old = mask_secrets(get_setting(db, key))
    payload = data.value
    if key == "uploads":
        payload = upload_limits.clean({**get_setting(db, key), **payload})  # clamp sizes, keep only known formats
    if key == "email":
        payload = _clean_email(payload)
    value = set_setting(db, key, payload, user.id)
    log_activity(db, user, "settings.update", "platform_setting", None, old=old, new=mask_secrets(value))
    db.commit()
    if key == "uploads":
        upload_limits.clear_cache()
    return {"key": key, "value": mask_secrets(value)}


@router.get("/payment-gateway/status")
def payment_gateway_status(user: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    """What the app will actually use for the Pay buttons right now."""
    cfg = load_config(db)
    return {"provider": cfg.provider, "mode": cfg.mode, "configured": cfg.configured,
            "has_webhook_secret": bool(cfg.webhook_secret)}


@router.post("/payment-gateway/test")
def test_payment_gateway(user: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    """Check the saved gateway keys with Razorpay without charging anything."""
    cfg = load_config(db)
    if cfg.provider != "razorpay":
        return {"ok": True, "mode": "test", "message": "Mock gateway: payments are simulated, no real money moves."}
    ok, message = RazorpayGateway(cfg).check_credentials()
    return {"ok": ok, "mode": cfg.mode, "message": message}


def _clean_email(p: dict) -> dict:
    out = dict(p)
    if "smtp_port" in out:
        try:
            out["smtp_port"] = int(out["smtp_port"])
        except (TypeError, ValueError):
            raise bad_request("SMTP port must be a number")
        if not 1 <= out["smtp_port"] <= 65535:
            raise bad_request("SMTP port must be between 1 and 65535")
    if "security" in out and out["security"] not in ("starttls", "ssl", "none"):
        raise bad_request("Security must be STARTTLS, SSL or none")
    for k in ("smtp_host", "smtp_user", "from", "from_name", "admin_email"):
        if k in out and isinstance(out[k], str):
            out[k] = out[k].strip()
    return out


@router.get("/{key}/reveal")
def reveal_secrets(key: str, user: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    """The saved secrets of one setting in clear text, for the "show" eye next to a hidden field. Every use is logged."""
    if key not in DEFAULT_SETTINGS:
        raise not_found("Setting")
    log_activity(db, user, "settings.reveal", "platform_setting", None, new={"setting": key})
    db.commit()
    return secret_values(get_setting(db, key))


@router.post("/email/test")
def test_email(data: EmailTest, user: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    """Send a real test email with the SAVED SMTP settings and say plainly whether it worked."""
    from app.integrations.messaging import email_config, try_email

    cfg = email_config()
    where = cfg.host or "no SMTP host (log only)"
    body = f"This is a test email from Odeslo.\n\nIf you can read this, your email settings work.\nSent through {where}."
    ok, message = try_email(data.to, "Odeslo test email", body)
    log_activity(db, user, "settings.email_test", "platform_setting", None, new={"to": data.to, "ok": ok})
    db.commit()
    return {"ok": ok, "message": message, "host": cfg.host, "port": cfg.port, "security": cfg.security}


@router.post("/sms/test")
def test_sms(data: SmsTest, user: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    """Send a real test SMS with the SAVED SMS settings."""
    from app.integrations.messaging import try_sms

    ok, message = try_sms(data.mobile, "Odeslo test message. Your SMS settings work.")
    log_activity(db, user, "settings.sms_test", "platform_setting", None, new={"mobile": data.mobile, "ok": ok})
    db.commit()
    return {"ok": ok, "message": message}
