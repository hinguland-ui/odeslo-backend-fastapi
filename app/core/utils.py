import re
import secrets
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import inspect

from app.core.timeutils import now_ist

SENSITIVE_FIELDS = {"password_hash", "otp_hash", "bank_details", "fcm_token"}


def normalize_mobile(value: str) -> str:
    """Normalise an Indian mobile number to 10 digits."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("Invalid mobile number")
    return digits


def mask_mobile(mobile: str | None) -> str | None:
    if not mobile or len(mobile) < 4:
        return mobile
    return mobile[:2] + "X" * (len(mobile) - 4) + mobile[-2:]


def generate_ref(prefix: str) -> str:
    """Human readable unique reference, e.g. BK2610041A2B3C."""
    return f"{prefix}{now_ist():%y%m%d}{secrets.token_hex(3).upper()}"


def jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


def snapshot(obj: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    """Column values of an ORM object as a JSON-safe dict (used for audit old/new values)."""
    exclude = (exclude or set()) | SENSITIVE_FIELDS
    return {
        attr.key: jsonable(getattr(obj, attr.key))
        for attr in inspect(obj).mapper.column_attrs
        if attr.key not in exclude
    }


def diff(old: dict[str, Any], new: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return only the changed keys of two snapshots."""
    changed = {k for k in new if old.get(k) != new.get(k)} - {"updated_at"}
    return {k: old.get(k) for k in changed}, {k: new.get(k) for k in changed}
