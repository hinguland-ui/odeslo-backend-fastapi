"""Browser-facing hardening: one-use socket tickets, security headers, no cached API responses."""

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import API, auth


def test_ws_ticket_is_one_use_and_not_a_login(client, state):
    soa = state["soa"]
    ticket = client.post(f"{API}/auth/ws-ticket", headers=auth(soa)).json()["ticket"]

    # the ticket is not a login token: the normal API refuses it
    assert client.get(f"{API}/auth/me", headers=auth(ticket)).status_code == 401

    with client.websocket_connect(f"{API}/realtime/ws?ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "ready"

    # the same ticket cannot be replayed
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{API}/realtime/ws?ticket={ticket}") as ws:
            ws.receive_json()

    # a made-up ticket and a registration-style token do not open it either
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{API}/realtime/ws?ticket=abc") as ws:
            ws.receive_json()

    # the phone app still connects with its access token
    with client.websocket_connect(f"{API}/realtime/ws?token={soa}") as ws:
        assert ws.receive_json()["type"] == "ready"

    # a ticket needs a login to be issued
    assert client.post(f"{API}/auth/ws-ticket").status_code in (401, 403)


def test_security_headers_and_no_cache(client, state):
    r = client.get(f"{API}/auth/me", headers=auth(state["soa"]))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


def test_settings_reveal_and_email_test(client, state, monkeypatch):
    sa, soa = state["sa"], state["soa"]
    base = f"{API}/settings"

    # secrets are hidden in the list, and shown only to the super admin on request
    client.put(f"{base}/email", headers=auth(sa), json={"value": {"smtp_host": "smtp.test.local", "smtp_port": 465, "security": "ssl", "smtp_user": "u@test.local", "smtp_password": "s3cret-pass", "from": "no-reply@test.local"}})
    listed = {s["key"]: s["value"] for s in client.get(base, headers=auth(sa)).json()}
    assert listed["email"]["smtp_password"] == "********"
    assert client.get(f"{base}/email/reveal", headers=auth(sa)).json() == {"smtp_password": "s3cret-pass"}
    assert client.get(f"{base}/email/reveal", headers=auth(soa)).status_code == 403
    assert client.get(f"{base}/nope/reveal", headers=auth(sa)).status_code == 404

    # bad values are refused
    assert client.put(f"{base}/email", headers=auth(sa), json={"value": {"smtp_port": 99999}}).status_code == 400
    assert client.put(f"{base}/email", headers=auth(sa), json={"value": {"security": "weird"}}).status_code == 400

    # the test email uses the saved settings and reports the real outcome
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0, context=None):
            sent["conn"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, context=None): sent["tls"] = True
        def login(self, user, pw): sent["login"] = (user, pw)
        def send_message(self, msg): sent["msg"] = msg

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    ok = client.post(f"{base}/email/test", headers=auth(sa), json={"to": "me@example.com"}).json()
    assert ok["ok"] is True and sent["conn"] == ("smtp.test.local", 465) and sent["login"] == ("u@test.local", "s3cret-pass")
    assert sent["msg"]["To"] == "me@example.com" and "no-reply@test.local" in sent["msg"]["From"]
    assert "s3cret-pass" not in str(ok)  # the password never comes back in a message

    def boom(self, *a, **k): raise smtplib.SMTPAuthenticationError(535, b"bad")
    monkeypatch.setattr(FakeSMTP, "login", boom)
    bad = client.post(f"{base}/email/test", headers=auth(sa), json={"to": "me@example.com"}).json()
    assert bad["ok"] is False and "username or password" in bad["message"]
    assert client.post(f"{base}/email/test", headers=auth(sa), json={"to": "not-an-email"}).status_code == 422
    assert client.post(f"{base}/email/test", headers=auth(soa), json={"to": "me@example.com"}).status_code == 403
