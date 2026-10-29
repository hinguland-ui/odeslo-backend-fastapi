"""A new resident / vendor who closes the sign-up wizard comes back to the same step with the same details."""

from tests.conftest import API, PDF, PNG, auth, otp_login


def _reg_token(client, mobile, role):
    data = otp_login(client, mobile, role)
    assert data["registration_required"] is True
    return data["registration_token"]


def test_resident_draft_is_saved_resumed_and_cleared(client, state):
    token = _reg_token(client, "9888800001", "resident")
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(token)).json() == {"step": 0, "data": {}}

    saved = {"society": {"id": state["society_b"], "name": "B"}, "wingId": str(state["wing_b"]), "name": "Anita", "kind": "tenant"}
    r = client.put(f"{API}/onboarding/draft", headers=auth(token), json={"role": "resident", "step": 2, "data": saved})
    assert r.status_code == 200 and r.json()["step"] == 2

    # closing the page and coming back: OTP again -> a new registration token -> same step and details
    again = _reg_token(client, "9888800001", "resident")
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(again)).json() == {"step": 2, "data": saved}
    # saving again overwrites (one draft per mobile and role)
    client.put(f"{API}/onboarding/draft", headers=auth(again), json={"role": "resident", "step": 3, "data": {**saved, "email": "a@x.in"}})
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(token)).json()["step"] == 3

    # it belongs to that mobile only, and is never open without the OTP-verified token
    other = _reg_token(client, "9888800009", "resident")
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(other)).json()["step"] == 0
    assert client.get(f"{API}/onboarding/draft?role=resident").status_code == 401
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(state["r1"])).status_code == 401  # not a registration token
    big = {"x": "y" * 9000}
    assert client.put(f"{API}/onboarding/draft", headers=auth(token), json={"role": "resident", "step": 1, "data": big}).status_code == 400
    assert client.put(f"{API}/onboarding/draft", headers=auth(token), json={"role": "super_admin", "step": 1, "data": {}}).status_code == 422

    # finishing the registration removes the draft
    body = {"society_id": state["society_b"], "wing_id": state["wing_b"], "unit_id": state["units_b"][2], "resident_type": "owner", "name": "Anita"}
    assert client.post(f"{API}/residents/register", headers=auth(token), json=body).status_code == 201
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(token)).json() == {"step": 0, "data": {}}


def test_vendor_draft_is_separate_and_cleared_on_register(client, state):
    token = _reg_token(client, "9888800002", "partner")
    data = {"name": "Ravi", "business": "Ravi AC", "categories": ["ac", "plumbing"]}
    assert client.put(f"{API}/onboarding/draft", headers=auth(token), json={"role": "partner", "step": 2, "data": data}).status_code == 200
    assert client.get(f"{API}/onboarding/draft?role=partner", headers=auth(token)).json() == {"step": 2, "data": data}
    assert client.get(f"{API}/onboarding/draft?role=resident", headers=auth(token)).json()["step"] == 0  # other role untouched

    files = {"id_proof": ("id.pdf", PDF, "application/pdf"), "photo": ("p.png", PNG, "image/png")}
    r = client.post(f"{API}/service-partners/register", headers=auth(token), files=files,
                    data={"name": "Ravi", "categories": ["ac", "plumbing"], "business_name": "Ravi AC"})
    assert r.status_code == 201, r.text
    assert client.get(f"{API}/onboarding/draft?role=partner", headers=auth(token)).json() == {"step": 0, "data": {}}
