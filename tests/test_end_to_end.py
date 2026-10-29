"""End-to-end role-wise flows (spec section 11.2 test checklist). Tests run in order and share `state`."""

from datetime import date, datetime, timedelta

from app.core.database import SessionLocal
from app.modules.super_admins.service import create_super_admin
from tests.conftest import API, PDF, PNG, auth, otp_login


def _society_payload(name: str, admin_mobile: str, admin_email: str, lat=18.4766, lng=73.8913):
    return {
        "name": name, "address": "Plot 12, Main Road", "area": "Kondhwa", "city": "Pune", "state": "Maharashtra",
        "pincode": "411048", "lat": lat, "lng": lng, "type": "apartment",
        "wings": [{"name": "A", "units": [{"unit_no": f"10{i}", "floor": 1} for i in range(1, 5)]}],
        "primary_admin": {"name": "Secretary", "mobile": admin_mobile, "email": admin_email,
                          "designation": "secretary"},
        "maintenance_settings": {"amounts": {"flat": 2000}, "billing_cycle": "monthly", "due_day": 10,
                                 "late_fee_enabled": True, "late_fee": 100},
    }


def _register_resident(client, mobile, society_id, wing_id, unit_id, rtype="owner", name="Resident"):
    data = otp_login(client, mobile, "resident")
    assert data["registration_required"] is True
    r = client.post(f"{API}/residents/register", headers=auth(data["registration_token"]),
                    json={"society_id": society_id, "wing_id": wing_id, "unit_id": unit_id,
                          "resident_type": rtype, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- Super admin & society setup
def test_super_admin_2fa_and_society_setup(client, state):
    with SessionLocal() as db:
        create_super_admin(db, "Akmal", "akmal@odeslo.app", "9111111111", "Secret@123")
        db.commit()

    r = client.post(f"{API}/auth/super-admin/login", json={"email": "akmal@odeslo.app", "password": "wrong-pass"})
    assert r.status_code == 401
    r = client.post(f"{API}/auth/super-admin/login", json={"email": "akmal@odeslo.app", "password": "Secret@123"})
    assert r.status_code == 200
    r = client.post(f"{API}/auth/super-admin/verify", json={"email": "akmal@odeslo.app", "otp": r.json()["dev_otp"]})
    assert r.status_code == 200
    sa = state["sa"] = r.json()["access_token"]

    r = client.post(f"{API}/societies", headers=auth(sa), json=_society_payload("Ajit Residency", "9222222222",
                                                                                 "sec@ajit.in"))
    assert r.status_code == 201, r.text
    soc = r.json()
    assert soc["code"] == "AJR001" and soc["status"] == "pending"
    state["society_id"] = soc["id"]

    # residents cannot find a pending society
    assert client.get(f"{API}/societies/public/by-code/AJR001").status_code == 404
    r = client.post(f"{API}/societies/{soc['id']}/status", headers=auth(sa), json={"status": "active"})
    assert r.status_code == 200
    r = client.get(f"{API}/societies/public/{soc['id']}/wings")
    state["wing_id"] = r.json()[0]["id"]
    units = client.get(f"{API}/societies/public/wings/{state['wing_id']}/units").json()
    state["units"] = [u["id"] for u in units]

    # second society for isolation tests
    r = client.post(f"{API}/societies", headers=auth(sa), json=_society_payload("Green Park", "9333333333",
                                                                                 "sec@green.in"))
    state["society_b"] = r.json()["id"]
    client.post(f"{API}/societies/{state['society_b']}/status", headers=auth(sa), json={"status": "active"})
    state["wing_b"] = client.get(f"{API}/societies/public/{state['society_b']}/wings").json()[0]["id"]
    state["units_b"] = [u["id"] for u in client.get(f"{API}/societies/public/wings/{state['wing_b']}/units").json()]


def test_society_admin_first_login_and_password(client, state):
    data = otp_login(client, "9222222222", "society_admin")
    assert data["must_set_password"] is True
    r = client.post(f"{API}/auth/set-password", headers=auth(data["access_token"]),
                    json={"new_password": "Society@123"})
    assert r.status_code == 200
    r = client.post(f"{API}/auth/society-admin/login", json={"email": "sec@ajit.in", "password": "Society@123"})
    assert r.status_code == 200 and r.json()["must_set_password"] is False
    state["soa"] = r.json()["access_token"]
    state["soa_b"] = otp_login(client, "9333333333", "society_admin")["access_token"]

    # primary admin adds a treasurer co-admin with limited permissions
    r = client.post(f"{API}/society-admins", headers=auth(state["soa"]),
                    json={"name": "Treasurer", "mobile": "9222222223", "email": "tr@ajit.in",
                          "designation": "treasurer", "preset": "treasurer"})
    assert r.status_code == 201, r.text
    treasurer = otp_login(client, "9222222223", "society_admin")["access_token"]
    assert client.get(f"{API}/complaints", headers=auth(treasurer)).status_code == 403
    assert client.get(f"{API}/maintenance/bills", headers=auth(treasurer)).status_code == 200


# ---------------------------------------------------------------- Resident onboarding
def test_resident_registration_and_approval(client, state):
    sid, wid, units = state["society_id"], state["wing_id"], state["units"]
    reg = _register_resident(client, "9444444441", sid, wid, units[0], name="Owner One")
    token = reg["access_token"]
    assert reg["status"] == "pending_approval"

    # pending resident: only masjid feature + own profile
    assert client.get(f"{API}/residents/me", headers=auth(token)).status_code == 200
    assert client.get(f"{API}/group-deals/available", headers=auth(token)).status_code == 403
    assert client.get(f"{API}/masjids/nearby?lat=18.47&lng=73.89", headers=auth(token)).status_code == 200

    # admin approval queue
    pending = client.get(f"{API}/residents/pending", headers=auth(state["soa"])).json()
    assert pending["total"] == 1
    rid = pending["items"][0]["id"]
    assert client.post(f"{API}/residents/{rid}/approve", headers=auth(state["soa"])).status_code == 200
    assert client.get(f"{API}/group-deals/available", headers=auth(token)).status_code == 200
    state["r1"], state["r1_id"] = token, rid

    # dashboard residents count -> approved list
    dash = client.get(f"{API}/dashboard/society-admin", headers=auth(state["soa"])).json()
    assert dash["residents"]["count"] == 1 and dash["residents"]["link"] == "/residents?status=active"

    # a flat that already has an approved owner cannot take another owner / tenant
    login = otp_login(client, "9444444449", "resident")
    body = {"society_id": sid, "wing_id": wid, "unit_id": units[0], "resident_type": "owner", "name": "Second Owner"}
    r = client.post(f"{API}/residents/register", headers=auth(login["registration_token"]), json=body)
    assert r.status_code == 409, r.text

    # the same person registers on a vacant flat instead
    body["unit_id"] = units[3]
    r = client.post(f"{API}/residents/register", headers=auth(login["registration_token"]), json=body)
    assert r.status_code == 201, r.text
    rid2 = r.json()["user_id"]

    # admin edits the request (name, type), then tries the occupied flat: flagged, approval blocked
    r = client.patch(f"{API}/residents/{rid2}", headers=auth(state["soa"]),
                     json={"name": "Second Owner Fixed", "resident_type": "tenant"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Second Owner Fixed" and r.json()["resident_type"] == "tenant"
    r = client.patch(f"{API}/residents/{rid2}", headers=auth(state["soa"]), json={"unit_id": units[0]})
    assert r.status_code == 200 and r.json()["unit_occupied_flag"] is True
    detail = client.get(f"{API}/residents/{rid2}", headers=auth(state["soa"])).json()
    assert detail["occupied_by"]["id"] == rid
    assert client.post(f"{API}/residents/{rid2}/approve", headers=auth(state["soa"])).status_code == 409
    assert client.post(f"{API}/residents/{rid2}/reject", headers=auth(state["soa"]),
                       json={"reason": "Unit already occupied"}).status_code == 200

    # the rejected resident sees the reason and applies again with their own login
    again = otp_login(client, "9444444449", "resident")
    home = client.get(f"{API}/dashboard/resident/home", headers=auth(again["access_token"])).json()
    assert home["resident"]["status"] == "rejected"
    assert home["resident"]["rejection_reason"] == "Unit already occupied"
    r = client.post(f"{API}/residents/register", headers=auth(again["access_token"]), json=body)
    assert r.status_code == 201, r.text
    assert client.get(f"{API}/residents/{rid2}", headers=auth(state["soa"])).json()["status"] == "pending_approval"

    # rejected requests can also be corrected and approved straight away
    assert client.post(f"{API}/residents/{rid2}/reject", headers=auth(state["soa"]),
                       json={"reason": "Wrong details"}).status_code == 200
    r = client.patch(f"{API}/residents/{rid2}", headers=auth(state["soa"]), json={"unit_id": units[3], "name": "Second Owner"})
    assert r.status_code == 200 and r.json()["unit_occupied_flag"] is False
    assert client.post(f"{API}/residents/{rid2}/approve", headers=auth(state["soa"])).status_code == 200
    # flat / type are locked once approved, name is not
    assert client.patch(f"{API}/residents/{rid2}", headers=auth(state["soa"]), json={"unit_id": units[1]}).status_code == 400
    assert client.patch(f"{API}/residents/{rid2}", headers=auth(state["soa"]), json={"name": "Second Owner Jr"}).status_code == 200
    assert client.post(f"{API}/residents/{rid2}/deactivate", headers=auth(state["soa"])).status_code == 200

    # two more residents for deal tests
    for i, key in ((1, "r2"), (2, "r3")):
        reg = _register_resident(client, f"944444444{i + 1}", sid, wid, units[i], name=f"Owner {i + 1}")
        client.post(f"{API}/residents/{reg['user_id']}/approve", headers=auth(state["soa"]))
        state[key] = reg["access_token"]

    # resident in society B
    reg = _register_resident(client, "9555555551", state["society_b"], state["wing_b"], state["units_b"][0])
    client.post(f"{API}/residents/{reg['user_id']}/approve", headers=auth(state["soa_b"]))
    state["rb"] = reg["access_token"]


def test_owner_adds_family_member_and_tenant(client, state):
    r = client.post(f"{API}/residents/me/family", headers=auth(state["r1"]),
                    json={"name": "Wife", "relation": "spouse", "mobile": "9444444460", "grant_app_access": True})
    assert r.status_code == 201, r.text
    fam = otp_login(client, "9444444460", "resident")
    assert fam["status"] == "active"
    # family member cannot add members
    r = client.post(f"{API}/residents/me/family", headers=auth(fam["access_token"]),
                    json={"name": "Kid", "relation": "son"})
    assert r.status_code == 403
    # family member cannot see maintenance
    assert client.get(f"{API}/maintenance/my", headers=auth(fam["access_token"])).status_code == 403


# ---------------------------------------------------------------- Society isolation
def test_cross_society_isolation(client, state):
    assert client.get(f"{API}/residents/{state['r1_id']}", headers=auth(state["soa_b"])).status_code == 404
    assert client.get(f"{API}/societies/{state['society_id']}", headers=auth(state["soa_b"])).status_code == 404
    r = client.get(f"{API}/residents?society_id={state['society_id']}", headers=auth(state["soa_b"]))
    assert r.status_code == 403


# ---------------------------------------------------------------- Group deals, waitlist, payments, refunds
def test_group_deal_capacity_waitlist_and_promotion(client, state):
    cutoff = (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")
    r = client.post(f"{API}/group-deals", headers=auth(state["soa"]), json={
        "title": "AC Service", "category": "ac", "service_date": str(date.today() + timedelta(days=5)),
        "slots": ["09:00-12:00"], "price_per_unit": 500, "max_units": 2, "cutoff_date": cutoff,
    })
    assert r.status_code == 201, r.text
    deal_id = state["deal_id"] = r.json()["id"]
    assert client.post(f"{API}/group-deals/{deal_id}/publish", headers=auth(state["soa"])).status_code == 200

    # society B cannot see society A's deal
    assert client.get(f"{API}/group-deals/{deal_id}", headers=auth(state["rb"])).status_code == 404

    booking = lambda tok: client.post(f"{API}/bookings", headers=auth(tok),  # noqa: E731
                                      json={"deal_id": deal_id, "units_count": 1, "slot": "09:00-12:00"})
    b1, b2, b3 = booking(state["r1"]).json(), booking(state["r2"]).json(), booking(state["r3"]).json()
    assert b1["status"] == "pending_payment" and b2["status"] == "pending_payment"
    assert b3["status"] == "waitlisted" and b3["waitlist_position"] == 1
    deal = client.get(f"{API}/group-deals/{deal_id}", headers=auth(state["soa"])).json()
    assert deal["confirmed_units"] == 2 and deal["status"] == "full"

    # resident 1 pays online (mock gateway)
    order = client.post(f"{API}/payments/initiate", headers=auth(state["r1"]),
                        json={"ref_type": "booking", "ref_id": b1["id"]}).json()
    r = client.post(f"{API}/payments/verify", headers=auth(state["r1"]),
                    json={"payment_id": order["payment_id"], "gateway_order_id": order["gateway_order_id"],
                          "gateway_payment_id": "pay_mock_1", "signature": "mock_signature"})
    assert r.status_code == 200 and r.json()["status"] == "success"
    b1 = client.get(f"{API}/bookings/{b1['id']}", headers=auth(state["r1"])).json()
    assert b1["status"] == "confirmed" and b1["payment_status"] == "paid"
    state["b1"], state["b3"] = b1["id"], b3["id"]

    # resident 2 cancels -> resident 3 auto-promoted (FCFS)
    r = client.post(f"{API}/bookings/{b2['id']}/cancel", headers=auth(state["r2"]), json={"reason": "Travelling"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    b3 = client.get(f"{API}/bookings/{b3['id']}", headers=auth(state["r3"])).json()
    assert b3["status"] == "pending_payment" and b3["waitlist_position"] is None

    # cancelled bookings never count
    deal = client.get(f"{API}/group-deals/{deal_id}", headers=auth(state["soa"])).json()
    assert deal["confirmed_units"] == 2
    active = client.get(f"{API}/bookings/my?tab=active", headers=auth(state["r2"])).json()
    assert active["total"] == 0
    dash = client.get(f"{API}/dashboard/society-admin", headers=auth(state["soa"])).json()
    assert dash["active_bookings"]["count"] == 2

    # other resident's booking is not accessible
    assert client.get(f"{API}/bookings/{state['b1']}", headers=auth(state["r2"])).status_code == 404
    assert client.get(f"{API}/bookings/{state['b1']}", headers=auth(state["rb"])).status_code == 404


def test_cancel_paid_booking_refund_flow(client, state):
    r = client.post(f"{API}/bookings/{state['b1']}/cancel", headers=auth(state["r1"]), json={"reason": "Changed mind"})
    assert r.status_code == 200 and r.json()["payment_status"] == "refund_pending"
    refunds = client.get(f"{API}/refunds?status=requested", headers=auth(state["soa"])).json()
    assert refunds["total"] == 1
    rid = refunds["items"][0]["id"]
    assert client.post(f"{API}/refunds/{rid}/approve", headers=auth(state["soa"])).json()["status"] == "approved"
    r = client.post(f"{API}/refunds/{rid}/process", headers=auth(state["soa"]), json={"mode": "gateway"})
    assert r.status_code == 200 and r.json()["status"] == "processed"
    assert client.post(f"{API}/refunds/{rid}/complete", headers=auth(state["soa"])).json()["status"] == "completed"
    b1 = client.get(f"{API}/bookings/{state['b1']}", headers=auth(state["r1"])).json()
    assert b1["payment_status"] == "refunded" and len(b1["refunds"]) == 1
    notes = client.get(f"{API}/notifications?category=refund", headers=auth(state["r1"])).json()
    assert notes["total"] >= 4  # requested, approved, processed, completed


# ---------------------------------------------------------------- Staff, partners, service jobs
def test_service_job_flow(client, state):
    # r3 pays to confirm the promoted booking
    order = client.post(f"{API}/payments/initiate", headers=auth(state["r3"]),
                        json={"ref_type": "booking", "ref_id": state["b3"]}).json()
    client.post(f"{API}/payments/verify", headers=auth(state["r3"]),
                json={"payment_id": order["payment_id"], "gateway_order_id": order["gateway_order_id"],
                      "gateway_payment_id": "pay_mock_3", "signature": "mock_signature"})

    files = {"id_proof": ("id.pdf", PDF, "application/pdf"), "photo": ("p.png", PNG, "image/png")}
    r = client.post(f"{API}/service-partners", headers=auth(state["sa"]), files=files,
                    data={"name": "CoolAir", "mobile": "9666666661", "categories": ["ac"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert client.post(f"{API}/auth/otp/send", json={"mobile": "9666666661", "user_type": "partner"}).status_code == 403
    client.post(f"{API}/service-partners/{pid}/verify", headers=auth(state["sa"]))
    client.put(f"{API}/service-partners/{pid}/societies", headers=auth(state["sa"]),
               json={"society_ids": [state["society_id"]]})
    partner = otp_login(client, "9666666661", "partner")["access_token"]

    r = client.post(f"{API}/service-jobs/from-deal/{state['deal_id']}", headers=auth(state["soa"]),
                    json={"assigned_to_type": "partner", "assigned_to_id": pid})
    assert r.status_code == 201 and len(r.json()) == 1
    job_id = r.json()[0]["id"]

    detail = client.get(f"{API}/service-jobs/{job_id}", headers=auth(partner)).json()
    assert "X" in detail["resident_mobile"]  # masked until accepted
    def go(status):
        r = client.post(f"{API}/service-jobs/{job_id}/status", headers=auth(partner), data={"status": status})
        assert r.status_code == 200, r.text

    # live location is refused until the vendor has accepted the job
    spot = {"lat": 18.55, "lng": 73.85, "accuracy": 12}
    assert client.post(f"{API}/service-jobs/{job_id}/location", headers=auth(partner), json=spot).status_code == 400
    go("accepted")
    # ...and still refused while the job is only accepted (it may be days away)
    assert client.post(f"{API}/service-jobs/{job_id}/location", headers=auth(partner), json=spot).status_code == 400
    go("on_the_way")

    # real-time: the resident's open page is told about chat messages and the vendor's position
    with client.websocket_connect(f"{API}/realtime/ws?token={state['r3']}") as ws:
        assert ws.receive_json() == {"type": "ready"}
        r = client.post(f"{API}/service-jobs/{job_id}/messages", headers=auth(partner), json={"body": "Reaching in 10 min"})
        assert r.status_code == 201 and r.json()["sender_type"] == "partner"
        event = ws.receive_json()
        assert event["type"] == "chat" and event["job_id"] == job_id
        r = client.post(f"{API}/service-jobs/{job_id}/location", headers=auth(partner), json=spot)
        assert r.status_code == 200 and r.json()["sharing"] is True
        assert ws.receive_json() == {"type": "job_location", "job_id": job_id}
    assert client.get(f"{API}/realtime/ws", headers=auth(state["r3"])).status_code in (403, 404, 426)  # plain GET is not a socket

    # the resident sees where the vendor is, how far and about how long
    loc = client.get(f"{API}/service-jobs/{job_id}/location", headers=auth(state["r3"])).json()
    assert loc["sharing"] is True and loc["lat"] == 18.55 and loc["destination"] and loc["distance_km"] is not None
    assert loc["eta_minutes"] >= 1
    assert client.get(f"{API}/service-jobs/{job_id}/location", headers=auth(state["soa"])).json()["sharing"] is True
    # a stranger (other society's admin / another resident) cannot track or read the chat
    assert client.get(f"{API}/service-jobs/{job_id}/location", headers=auth(state["soa_b"])).status_code == 404
    assert client.get(f"{API}/service-jobs/{job_id}/messages", headers=auth(state["soa_b"])).status_code == 404
    assert client.get(f"{API}/service-jobs/{job_id}/messages", headers=auth(state["r2"])).status_code == 404
    # the resident, the society admin and the vendor all talk in the same thread
    assert client.post(f"{API}/service-jobs/{job_id}/messages", headers=auth(state["r3"]), json={"body": "Ok, gate is open"}).status_code == 201
    assert client.post(f"{API}/service-jobs/{job_id}/messages", headers=auth(state["soa"]), json={"body": "Please send photos"}).status_code == 201
    thread = client.get(f"{API}/service-jobs/{job_id}/messages", headers=auth(partner)).json()
    assert [m["sender_type"] for m in thread] == ["partner", "resident", "society_admin"]
    assert client.get(f"{API}/service-jobs/{job_id}/messages?after_id={thread[1]['id']}", headers=auth(partner)).json()[0]["body"] == "Please send photos"
    assert client.post(f"{API}/service-jobs/{job_id}/messages", headers=auth(partner), json={"body": "   "}).status_code == 422

    go("started")
    r = client.post(f"{API}/service-jobs/{job_id}/status", headers=auth(partner), data={"status": "completed"})
    assert r.status_code == 400  # needs photo or note
    r = client.post(f"{API}/service-jobs/{job_id}/status", headers=auth(partner),
                    data={"status": "completed", "note": "Done"}, files={"photo": ("d.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    # once the job is done the live position stops
    assert client.get(f"{API}/service-jobs/{job_id}/location", headers=auth(state["r3"])).json()["sharing"] is False
    assert client.post(f"{API}/service-jobs/{job_id}/location", headers=auth(partner), json=spot).status_code == 400

    b3 = client.get(f"{API}/bookings/{state['b3']}", headers=auth(state["r3"])).json()
    assert b3["status"] == "completed" and b3["can_rate"] is True
    assert client.post(f"{API}/bookings/{state['b3']}/rate", headers=auth(state["r3"]),
                       json={"rating": 5}).status_code == 200

    # staff added by society admin
    r = client.post(f"{API}/staff", headers=auth(state["soa"]), files=files,
                    data={"name": "Ramesh", "mobile": "9777777771", "type": "plumber"})
    assert r.status_code == 201, r.text
    state["staff_id"] = r.json()["id"]
    state["staff"] = otp_login(client, "9777777771", "staff")["access_token"]
    # staff of another society cannot be used
    assert client.get(f"{API}/staff/{state['staff_id']}", headers=auth(state["soa_b"])).status_code == 404


# ---------------------------------------------------------------- Complaints
def test_complaint_flow(client, state):
    r = client.post(f"{API}/complaints", headers=auth(state["r1"]),
                    data={"category": "plumbing", "description": "Kitchen tap leaking", "priority": "high"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"{API}/complaints/{cid}/assign", headers=auth(state["soa"]), json={"staff_id": state["staff_id"]})
    assert r.json()["status"] == "assigned"
    assert client.get(f"{API}/complaints/assigned", headers=auth(state["staff"])).json()[0]["id"] == cid
    for st in ("in_progress", "resolved"):
        r = client.post(f"{API}/complaints/{cid}/status", headers=auth(state["staff"]), json={"status": st})
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/complaints/{cid}/reopen", headers=auth(state["r1"]), json={"reason": "Still leaking"})
    assert r.json()["status"] == "reopened"
    client.post(f"{API}/complaints/{cid}/status", headers=auth(state["staff"]), json={"status": "resolved"})
    assert client.post(f"{API}/complaints/{cid}/close", headers=auth(state["r1"])).json()["status"] == "closed"
    assert client.get(f"{API}/complaints/{cid}", headers=auth(state["rb"])).status_code == 404


# ---------------------------------------------------------------- Maintenance
def test_maintenance_billing(client, state):
    r = client.post(f"{API}/maintenance/bills/generate", headers=auth(state["soa"]), json={})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 3  # 3 occupied units
    again = client.post(f"{API}/maintenance/bills/generate", headers=auth(state["soa"]), json={}).json()
    assert again["created"] == 0 and again["skipped_existing"] == 3

    my = client.get(f"{API}/maintenance/my", headers=auth(state["r1"])).json()
    assert my["current_due"] == 2000
    bill_id = my["unpaid_bills"][0]["id"]
    r = client.post(f"{API}/payments/offline", headers=auth(state["soa"]),
                    json={"ref_type": "maintenance", "ref_id": bill_id, "amount": 500, "mode": "cash"})
    assert r.status_code == 201
    assert client.get(f"{API}/maintenance/bills/{bill_id}", headers=auth(state["r1"])).json()["status"] == "partially_paid"

    order = client.post(f"{API}/payments/initiate", headers=auth(state["r1"]),
                        json={"ref_type": "maintenance", "ref_id": bill_id}).json()
    assert order["amount"] == 1500
    r = client.post(f"{API}/payments/verify", headers=auth(state["r1"]),
                    json={"payment_id": order["payment_id"], "gateway_order_id": order["gateway_order_id"],
                          "gateway_payment_id": "pay_m", "signature": "mock_signature"})
    assert r.status_code == 200
    assert client.get(f"{API}/maintenance/my", headers=auth(state["r1"])).json()["current_due"] == 0
    receipt = client.get(f"{API}/payments/{r.json()['id']}/receipt", headers=auth(state["r1"]))
    assert receipt.status_code == 200 and receipt.json()["amount"] == 1500
    # bad signature fails
    bills = client.get(f"{API}/maintenance/my", headers=auth(state["r2"])).json()
    order = client.post(f"{API}/payments/initiate", headers=auth(state["r2"]),
                        json={"ref_type": "maintenance", "ref_id": bills["unpaid_bills"][0]["id"]}).json()
    r = client.post(f"{API}/payments/verify", headers=auth(state["r2"]),
                    json={"payment_id": order["payment_id"], "gateway_order_id": order["gateway_order_id"],
                          "gateway_payment_id": "x", "signature": "forged"})
    assert r.status_code == 400


# ---------------------------------------------------------------- Masjid & Namaz
def test_masjid_verification_and_nearby(client, state):
    r = client.post(f"{API}/masjids", headers=auth(state["soa"]), json={
        "name": "Masjid-e-Noor", "address": "Kondhwa Khurd, Pune", "lat": 18.4790, "lng": 73.8930})
    assert r.status_code == 201 and r.json()["status"] == "pending_verification"
    mid = r.json()["id"]
    # unverified masjids are hidden from residents
    assert client.get(f"{API}/masjids/nearby?lat=18.4766&lng=73.8913", headers=auth(state["r1"])).json() == []
    assert client.get(f"{API}/masjids/{mid}", headers=auth(state["r1"])).status_code == 404

    pending = client.get(f"{API}/masjids/pending", headers=auth(state["sa"])).json()
    assert len(pending["masjids"]) == 1
    client.post(f"{API}/masjids/{mid}/verify", headers=auth(state["sa"]))
    near = client.get(f"{API}/masjids/nearby?lat=18.4766&lng=73.8913", headers=auth(state["r1"])).json()
    assert len(near) == 1 and near[0]["distance_km"] < 1 and near[0]["timings_source"] == "calculated"

    r = client.post(f"{API}/masjids/{mid}/timings", headers=auth(state["soa"]), json={
        "fajr": "05:45", "dhuhr": "13:30", "asr": "17:15", "maghrib": "18:25", "isha": "20:00", "jummah": "13:45",
        "effective_from": str(date.today())})
    tid = r.json()["id"]
    tt = client.get(f"{API}/masjids/{mid}/timetable", headers=auth(state["r1"])).json()
    assert tt["source"] == "calculated"  # still pending verification
    client.post(f"{API}/masjids/timings/{tid}/verify", headers=auth(state["sa"]))
    tt = client.get(f"{API}/masjids/{mid}/timetable", headers=auth(state["r1"])).json()
    assert tt["source"] == "masjid" and tt["timings"]["fajr"] == "05:45" and tt["next_prayer"]["seconds_until"] > 0


# ---------------------------------------------------------------- Notifications & audit
def test_notifications_and_activity_log(client, state):
    count = client.get(f"{API}/notifications/unread-count", headers=auth(state["r1"])).json()
    assert count["total"] > 0
    client.post(f"{API}/notifications/read-all", headers=auth(state["r1"]))
    assert client.get(f"{API}/notifications/unread-count", headers=auth(state["r1"])).json()["total"] == 0

    logs = client.get(f"{API}/activity-logs?entity=booking", headers=auth(state["soa"])).json()
    assert logs["total"] > 0 and all(l["society_id"] == state["society_id"] for l in logs["items"])
    assert client.get(f"{API}/activity-logs", headers=auth(state["r1"])).status_code == 403

    dash = client.get(f"{API}/dashboard/super-admin", headers=auth(state["sa"])).json()
    assert dash["societies"]["total"] == 2 and dash["revenue"] > 0
