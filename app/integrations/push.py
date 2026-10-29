"""Firebase Cloud Messaging push sender. Logs only when FCM is disabled."""

import logging

from app.core.config import settings

logger = logging.getLogger("odeslo.push")
_firebase_app = None


def _get_messaging():
    global _firebase_app
    import firebase_admin
    from firebase_admin import credentials, messaging

    if _firebase_app is None:
        _firebase_app = firebase_admin.initialize_app(credentials.Certificate(settings.FCM_CREDENTIALS_FILE))
    return messaging


def send_push(tokens: list[str], title: str, body: str, data: dict[str, str] | None = None) -> list[str]:
    """Send to the given FCM tokens. Returns tokens that are invalid and should be removed."""
    if not tokens:
        return []
    if not settings.FCM_ENABLED:
        logger.info("[Push -> %d device(s)] %s | %s", len(tokens), title, body)
        return []
    try:
        messaging = _get_messaging()
        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        response = messaging.send_each_for_multicast(message)
        invalid = []
        for token, result in zip(tokens, response.responses):
            if not result.success and result.exception and "registration" in str(result.exception).lower():
                invalid.append(token)
        return invalid
    except Exception as exc:  # never fail the business action because push failed
        logger.error("FCM push failed: %s", exc)
        return []
