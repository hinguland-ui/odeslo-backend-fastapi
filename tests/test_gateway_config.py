"""Super admin saves Razorpay keys in Platform settings -> every Pay button uses them (test keys = test mode)."""

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.integrations.payment_gateway import GatewayConfig, MockGateway, RazorpayGateway, get_gateway, load_config
from app.modules.super_admins.models import SuperAdmin
from tests.conftest import API, auth


def _super_admin_token() -> str:
    from app.modules.super_admins.service import create_super_admin

    with SessionLocal() as db:
        sa = db.scalars(select(SuperAdmin).order_by(SuperAdmin.id)).first()
        if not sa:
            sa = create_super_admin(db, "Gateway Admin", "gw@odeslo.app", "9111111199", "Secret@123")
            db.commit()
        return create_access_token("super_admin", sa.id)


def _save(client, token, **value):
    r = client.put(f"{API}/settings/payment_gateway", headers=auth(token), json={"value": value})
    assert r.status_code == 200, r.text
    return r.json()["value"]


def test_gateway_follows_saved_settings():
    from app.modules.settings.service import set_setting

    with SessionLocal() as db:
        assert load_config(db).provider == "mock"  # nothing saved yet -> .env fallback
        assert isinstance(get_gateway(db), MockGateway)

        set_setting(db, "payment_gateway", {"provider": "razorpay", "key_id": "rzp_test_abc", "key_secret": "sec", "webhook_secret": ""}, None)
        db.commit()
        cfg = load_config(db)
        assert (cfg.provider, cfg.mode, cfg.configured) == ("razorpay", "test", True)
        assert isinstance(get_gateway(db), RazorpayGateway)
        # an old payment keeps the gateway it was created with
        assert isinstance(get_gateway(db, "mock"), MockGateway)
        # no webhook secret saved -> every webhook is refused (an empty secret could be forged)
        assert get_gateway(db).verify_webhook(b"{}", "anything") is False

        set_setting(db, "payment_gateway", {"key_id": "rzp_live_xyz"}, None)
        db.commit()
        assert load_config(db).mode == "live"

        set_setting(db, "payment_gateway", {"provider": "mock"}, None)
        db.commit()
        assert isinstance(get_gateway(db), MockGateway)


def test_razorpay_without_keys_refuses_to_take_money():
    gw = RazorpayGateway(GatewayConfig(provider="razorpay"))
    with pytest.raises(HTTPException) as e:
        gw.create_order(100, "TXN1")
    assert e.value.status_code == 400
    assert gw.verify_payment("order", "pay", "sig") is False


def test_settings_api_status_mask_and_key_check(client, monkeypatch):
    token = _super_admin_token()

    saved = _save(client, token, provider="razorpay", key_id="rzp_test_demo", key_secret="supersecret", webhook_secret="whsec")
    assert saved["key_secret"] == "********" and saved["webhook_secret"] == "********" and saved["key_id"] == "rzp_test_demo"

    status = client.get(f"{API}/settings/payment-gateway/status", headers=auth(token)).json()
    assert status == {"provider": "razorpay", "mode": "test", "configured": True, "has_webhook_secret": True}
    cfg = client.get(f"{API}/settings/app-config").json()
    assert cfg["payment_gateway"] == "razorpay" and cfg["payment_key_id"] == "rzp_test_demo" and cfg["payment_mode"] == "test"

    class Resp:
        def __init__(self, code):
            self.status_code = code

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(200))
    ok = client.post(f"{API}/settings/payment-gateway/test", headers=auth(token)).json()
    assert ok["ok"] is True and ok["mode"] == "test"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp(401))
    bad = client.post(f"{API}/settings/payment-gateway/test", headers=auth(token)).json()
    assert bad["ok"] is False and "rejected" in bad["message"]

    # live keys -> real money
    _save(client, token, key_id="rzp_live_demo")
    assert client.get(f"{API}/settings/payment-gateway/status", headers=auth(token)).json()["mode"] == "live"

    # only a super admin can see or change this
    assert client.get(f"{API}/settings/payment-gateway/status").status_code == 401

    # back to the mock gateway so other tests are unaffected
    _save(client, token, provider="mock")
    assert client.post(f"{API}/settings/payment-gateway/test", headers=auth(token)).json()["ok"] is True
    assert client.get(f"{API}/settings/app-config").json()["payment_key_id"] == "mock_key"
