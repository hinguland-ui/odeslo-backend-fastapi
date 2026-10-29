"""Payment gateway abstraction (Razorpay + a mock gateway for development / testing)."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

import httpx

from app.core.config import settings
from app.core.errors import bad_request

MOCK_SIGNATURE = "mock_signature"


@dataclass
class GatewayConfig:
    provider: str  # mock | razorpay
    key_id: str = ""
    key_secret: str = ""
    webhook_secret: str = ""

    @property
    def mode(self) -> str:
        """test = mock gateway or Razorpay test keys, live = real money."""
        if self.provider != "razorpay":
            return "test"
        return "live" if self.key_id.startswith("rzp_live_") else "test"

    @property
    def configured(self) -> bool:
        return self.provider != "razorpay" or bool(self.key_id and self.key_secret)


def load_config(db: Session) -> GatewayConfig:
    """Gateway keys saved by the super admin (Platform settings). .env is only the fallback before anything is saved."""
    from app.modules.settings.models import PlatformSetting

    row = db.get(PlatformSetting, "payment_gateway")
    if row:
        v = row.value or {}
        return GatewayConfig(provider=(v.get("provider") or "mock").strip(), key_id=(v.get("key_id") or "").strip(),
                             key_secret=(v.get("key_secret") or "").strip(),
                             webhook_secret=(v.get("webhook_secret") or "").strip())
    return GatewayConfig(provider=settings.PAYMENT_GATEWAY, key_id=settings.RAZORPAY_KEY_ID,
                         key_secret=settings.RAZORPAY_KEY_SECRET, webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET)


@dataclass
class GatewayOrder:
    gateway: str
    order_id: str
    amount_paise: int
    currency: str
    key_id: str


@dataclass
class GatewayRefund:
    refund_id: str
    status: str


class BaseGateway:
    name = "base"

    def create_order(self, amount: float, receipt: str, notes: dict | None = None) -> GatewayOrder:
        raise NotImplementedError

    def verify_payment(self, order_id: str, payment_id: str, signature: str) -> bool:
        raise NotImplementedError

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        raise NotImplementedError

    def refund(self, payment_id: str, amount: float) -> GatewayRefund:
        raise NotImplementedError


class MockGateway(BaseGateway):
    name = "mock"

    def create_order(self, amount, receipt, notes=None):
        return GatewayOrder(self.name, f"mock_order_{secrets.token_hex(8)}", round(amount * 100),
                            settings.PAYMENT_CURRENCY, "mock_key")

    def verify_payment(self, order_id, payment_id, signature):
        return signature == MOCK_SIGNATURE

    def verify_webhook(self, body, signature):
        return signature == MOCK_SIGNATURE

    def refund(self, payment_id, amount):
        return GatewayRefund(f"mock_rfnd_{secrets.token_hex(8)}", "processed")


class RazorpayGateway(BaseGateway):
    name = "razorpay"
    base_url = "https://api.razorpay.com/v1"

    def __init__(self, config: GatewayConfig):
        self.config = config

    def _auth(self):
        if not self.config.key_id or not self.config.key_secret:
            raise bad_request("Online payment is not set up yet. Ask the platform admin to add the Razorpay keys.")
        return self.config.key_id, self.config.key_secret

    def check_credentials(self) -> tuple[bool, str]:
        """Ask Razorpay if these keys are valid (read-only call)."""
        try:
            resp = httpx.get(f"{self.base_url}/orders", params={"count": 1}, auth=self._auth(), timeout=15)
        except httpx.HTTPError as exc:
            return False, f"Could not reach Razorpay: {exc}"
        if resp.status_code == 200:
            return True, f"Razorpay accepted these keys ({self.config.mode} mode)."
        if resp.status_code == 401:
            return False, "Razorpay rejected these keys. Check the Key ID and Key secret."
        return False, f"Razorpay answered with status {resp.status_code}."

    def create_order(self, amount, receipt, notes=None):
        payload = {"amount": round(amount * 100), "currency": settings.PAYMENT_CURRENCY, "receipt": receipt,
                   "notes": notes or {}}
        try:
            resp = httpx.post(f"{self.base_url}/orders", json=payload, auth=self._auth(), timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise bad_request(f"Could not create payment order: {exc}")
        data = resp.json()
        return GatewayOrder(self.name, data["id"], data["amount"], data["currency"], self.config.key_id)

    def verify_payment(self, order_id, payment_id, signature):
        if not self.config.key_secret:
            return False
        expected = hmac.new(self.config.key_secret.encode(), f"{order_id}|{payment_id}".encode(),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def verify_webhook(self, body, signature):
        if not self.config.webhook_secret:  # an empty secret would let anyone forge a webhook
            return False
        expected = hmac.new(self.config.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def refund(self, payment_id, amount):
        try:
            resp = httpx.post(f"{self.base_url}/payments/{payment_id}/refund", json={"amount": round(amount * 100)},
                              auth=self._auth(), timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise bad_request(f"Gateway refund failed: {exc}")
        data = resp.json()
        return GatewayRefund(data["id"], data.get("status", "processed"))


def get_gateway(db: Session, name: str | None = None) -> BaseGateway:
    """The gateway for new payments (saved provider), or the one a payment was created with (`name`)."""
    config = load_config(db)
    if (name or config.provider) == "razorpay":
        return RazorpayGateway(config)
    if settings.ENVIRONMENT.lower() == "production":
        # the mock gateway accepts one fixed signature, so with it anyone could mark a payment as paid: never live
        raise bad_request("Online payment is not set up yet. Ask the platform admin to add the Razorpay keys.")
    return MockGateway()
