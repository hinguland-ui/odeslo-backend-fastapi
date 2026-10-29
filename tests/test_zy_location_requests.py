"""A society admin cannot move the society's pin; they ask, and the super admin decides."""

from tests.conftest import API, auth


def test_location_change_needs_super_admin(client, state):
    sa, soa, soa_b = state["sa"], state["soa"], state["soa_b"]
    sid = state["society_id"]
    base = f"{API}/location-requests"
    now = client.get(f"{API}/societies/{sid}", headers=auth(sa)).json()
    new = {"lat": round(now["lat"] + 0.01, 6), "lng": round(now["lng"] + 0.01, 6), "area": "New Area", "pincode": "411099", "reason": "Pin was on the wrong gate"}

    # the admin can no longer move the pin directly
    assert client.patch(f"{API}/societies/{sid}", headers=auth(soa), json={"lat": new["lat"], "lng": new["lng"]}).status_code == 403
    assert client.get(f"{API}/societies/{sid}", headers=auth(sa)).json()["lat"] == now["lat"]

    # a request is created, only one at a time, and nobody else's admin or a resident can send one
    r = client.post(base, headers=auth(soa), json=new)
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending" and req["old_lat"] == now["lat"] and req["society_code"] == now["code"]
    assert client.post(base, headers=auth(soa), json={**new, "lat": new["lat"] + 0.001}).status_code == 400
    assert client.post(base, headers=auth(state["r1"]), json=new).status_code == 403
    assert client.post(base, headers=auth(sa), json=new).status_code == 403  # the super admin decides, never asks

    # the super admin sees it, another society's admin does not
    assert [x["id"] for x in client.get(f"{base}?status=pending", headers=auth(sa)).json()] == [req["id"]]
    assert client.get(base, headers=auth(soa_b)).json() == []
    assert client.post(f"{base}/{req['id']}/approve", headers=auth(soa)).status_code == 403

    # cancel then re-send, then reject with a reason: the pin stays
    assert client.post(f"{base}/{req['id']}/cancel", headers=auth(soa)).json()["status"] == "cancelled"
    second = client.post(base, headers=auth(soa), json=new).json()
    assert client.post(f"{base}/{second['id']}/reject", headers=auth(sa), json={"note": "x"}).status_code == 422
    rej = client.post(f"{base}/{second['id']}/reject", headers=auth(sa), json={"note": "Please send a photo of the gate"}).json()
    assert rej["status"] == "rejected" and rej["decision_note"]
    assert client.get(f"{API}/societies/{sid}", headers=auth(sa)).json()["lat"] == now["lat"]

    # approved: the society moves, its address parts follow, the admin is told
    third = client.post(base, headers=auth(soa), json=new).json()
    ok = client.post(f"{base}/{third['id']}/approve", headers=auth(sa), json={})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    moved = client.get(f"{API}/societies/{sid}", headers=auth(sa)).json()
    assert (moved["lat"], moved["lng"], moved["area"], moved["pincode"]) == (new["lat"], new["lng"], "New Area", "411099")
    assert client.post(f"{base}/{third['id']}/approve", headers=auth(sa)).status_code == 400  # already decided
    assert any("Location change approved" in n["title"] for n in client.get(f"{API}/notifications", headers=auth(soa)).json()["items"])

    # same spot again is refused
    assert client.post(base, headers=auth(soa), json={"lat": new["lat"], "lng": new["lng"]}).status_code == 400
    # the super admin can still move it directly
    assert client.patch(f"{API}/societies/{sid}", headers=auth(sa), json={"lat": now["lat"], "lng": now["lng"]}).status_code == 200
