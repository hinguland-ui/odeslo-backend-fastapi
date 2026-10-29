import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(claims: dict[str, Any], expires_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {**claims, "iat": now, "exp": now + timedelta(minutes=expires_minutes)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_type: str, user_id: int) -> str:
    return create_token(
        {"sub": str(user_id), "typ": user_type, "scope": "access"}, settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )


def create_registration_token(mobile: str) -> str:
    return create_token({"sub": mobile, "scope": "registration"}, settings.REGISTRATION_TOKEN_EXPIRE_MINUTES)


_used_tickets: dict[str, float] = {}


def create_ws_ticket(user_type: str, user_id: int) -> str:
    """One-minute, one-use pass for opening the live-updates socket. The long-lived login never goes into a URL."""
    return create_token({"sub": str(user_id), "typ": user_type, "scope": "ws", "jti": secrets.token_hex(8)}, 1)


def use_ws_ticket(payload: dict[str, Any]) -> bool:
    """True the first time a ticket is shown, False when it was already used (a copied link does not work twice)."""
    import time

    now = time.time()
    for k in [k for k, exp in _used_tickets.items() if exp < now]:
        _used_tickets.pop(k, None)
    jti = str(payload.get("jti", ""))
    if not jti or jti in _used_tickets:
        return False
    _used_tickets[jti] = now + 120
    return True


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid / expired tokens."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def generate_otp(length: int | None = None) -> str:
    length = length or settings.OTP_LENGTH
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def hash_otp(mobile: str, otp: str) -> str:
    return hmac.new(settings.JWT_SECRET.encode(), f"{mobile}:{otp}".encode(), hashlib.sha256).hexdigest()


def verify_otp_hash(mobile: str, otp: str, otp_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(mobile, otp), otp_hash)
