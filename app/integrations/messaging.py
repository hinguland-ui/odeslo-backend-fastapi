"""SMS, WhatsApp and email senders.

What to use is read from the Platform settings the super admin saved (so a change applies at once, no restart);
the .env values are only the fallback before anything is saved. 'console' providers only log (development).
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

import httpx

from app.core.config import settings

logger = logging.getLogger("odeslo.messaging")


def _saved(key: str) -> dict:
    """The saved platform setting (never raises: a broken database must not stop a message)."""
    try:
        from app.core.database import SessionLocal
        from app.modules.settings.service import get_setting

        with SessionLocal() as db:
            return get_setting(db, key)
    except Exception:  # noqa: BLE001
        return {}


def _post(url: str, api_key: str, payload: dict) -> tuple[bool, str]:
    try:
        resp = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
        resp.raise_for_status()
        return True, "Sent"
    except httpx.HTTPStatusError as exc:
        logger.error("Messaging provider error (%s): %s", url, exc)
        return False, f"The provider answered {exc.response.status_code}"
    except httpx.HTTPError as exc:
        logger.error("Messaging provider error (%s): %s", url, exc)
        return False, f"Could not reach the provider: {exc.__class__.__name__}"


# ------------------------------------------------------------------ SMS / WhatsApp
def try_sms(mobile: str, message: str) -> tuple[bool, str]:
    s = _saved("sms")
    provider = s.get("provider") or settings.SMS_PROVIDER
    url = s.get("api_url") or settings.SMS_API_URL
    key = s.get("api_key") or settings.SMS_API_KEY
    if provider == "http" and url:
        payload = {"to": f"+91{mobile}", "message": message}
        if s.get("sender_id"):
            payload["sender_id"] = s["sender_id"]
        return _post(url, key, payload)
    logger.info("[SMS -> %s] %s", mobile, message)
    return True, "SMS provider is 'console': the message was only written to the server log"


def send_sms(mobile: str, message: str) -> bool:
    return try_sms(mobile, message)[0]


def send_whatsapp(mobile: str, message: str) -> bool:
    s = _saved("whatsapp")
    provider = s.get("provider") or settings.WHATSAPP_PROVIDER
    url = s.get("api_url") or settings.WHATSAPP_API_URL
    key = s.get("api_key") or settings.WHATSAPP_API_KEY
    if provider == "http" and url:
        return _post(url, key, {"to": f"91{mobile}", "message": message})[0]
    logger.info("[WhatsApp -> %s] %s", mobile, message)
    return True


# ------------------------------------------------------------------ email (SMTP)
@dataclass
class EmailConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    security: str  # starttls | ssl | none
    admin_email: str


def email_config() -> EmailConfig:
    s = _saved("email")
    return EmailConfig(
        host=(s.get("smtp_host") or settings.SMTP_HOST or "").strip(),
        port=int(s.get("smtp_port") or settings.SMTP_PORT or 587),
        user=(s.get("smtp_user") or settings.SMTP_USER or "").strip(),
        password=s.get("smtp_password") or settings.SMTP_PASSWORD or "",
        from_email=(s.get("from") or settings.SMTP_FROM or "").strip(),
        from_name=(s.get("from_name") or "Odeslo").strip(),
        security=(s.get("security") or "starttls"),
        admin_email=(s.get("admin_email") or "").strip(),
    )


def try_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Send one email with the saved SMTP settings. The text says what went wrong, never the password."""
    cfg = email_config()
    if not cfg.host:
        logger.info("[Email -> %s] %s | %s", to, subject, body)
        return True, "No SMTP host is set: the email was only written to the server log"
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.from_email)) if cfg.from_email else cfg.from_name
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if cfg.security == "ssl":
            smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=15, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=15)
        with smtp:
            smtp.ehlo()
            if cfg.security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if cfg.user:
                smtp.login(cfg.user, cfg.password)
            smtp.send_message(msg)
        return True, f"Sent to {to}"
    except smtplib.SMTPAuthenticationError:
        return False, "The server rejected the username or password"
    except smtplib.SMTPRecipientsRefused:
        return False, "The server refused the recipient address"
    except smtplib.SMTPSenderRefused:
        return False, "The server refused the 'from' address (it may need to match your account)"
    except smtplib.SMTPNotSupportedError:
        return False, "This server does not support that security mode. Try SSL on port 465 or STARTTLS on 587"
    except smtplib.SMTPException as exc:
        return False, f"SMTP error: {exc.__class__.__name__}"
    except (TimeoutError, OSError) as exc:
        return False, f"Could not connect to {cfg.host}:{cfg.port} ({exc.__class__.__name__}). Check the host, port and security mode"


def send_email(to: str, subject: str, body: str) -> bool:
    ok, message = try_email(to, subject, body)
    if not ok:
        logger.error("Email send failed to %s: %s", to, message)
    return ok
