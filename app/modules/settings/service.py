from typing import Any

from sqlalchemy.orm import Session

from app.modules.settings.models import PlatformSetting

DEFAULT_SETTINGS: dict[str, dict[str, Any]] = {
    "payment_gateway": {"provider": "mock", "key_id": "", "key_secret": "", "webhook_secret": ""},
    "sms": {"provider": "console", "api_url": "", "api_key": "", "sender_id": ""},
    "whatsapp": {"provider": "console", "api_url": "", "api_key": ""},
    "email": {"smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "", "security": "starttls",
              "from": "", "from_name": "Odeslo", "admin_email": ""},
    "prayer_calculation": {"method": "karachi", "asr": "hanafi"},
    "app_version": {"latest_version": "1.0.0", "min_supported_version": "1.0.0", "force_update": False},
    "masjid": {"default_radius_km": 5},
    "uploads": {"max_image_mb": 5, "max_document_mb": 8, "image_formats": ["jpg", "png", "webp"],
                "document_formats": ["pdf", "jpg", "png", "webp"]},
}

SECRET_KEYS = {"key_secret", "webhook_secret", "api_key", "smtp_password"}


def get_setting(db: Session, key: str) -> dict[str, Any]:
    row = db.get(PlatformSetting, key)
    return {**DEFAULT_SETTINGS.get(key, {}), **(row.value if row else {})}


def set_setting(db: Session, key: str, value: dict[str, Any], updated_by: int | None) -> dict[str, Any]:
    row = db.get(PlatformSetting, key)
    merged = {**get_setting(db, key), **value}
    if row:
        row.value = merged
        row.updated_by = updated_by
    else:
        db.add(PlatformSetting(key=key, value=merged, updated_by=updated_by))
    return merged


def secret_values(value: dict[str, Any]) -> dict[str, Any]:
    """Only the secret fields, as saved (for the super admin's "show" toggle)."""
    return {k: v for k, v in value.items() if k in SECRET_KEYS}


def mask_secrets(value: dict[str, Any]) -> dict[str, Any]:
    return {k: ("********" if k in SECRET_KEYS and v else v) for k, v in value.items()}
