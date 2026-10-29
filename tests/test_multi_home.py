"""One resident, homes in two societies: add a home, get it approved by the other society, switch between them."""

from tests.conftest import API, PDF, auth, otp_login


def test_resident_with_homes_in_two_societies(client, state):
    r1 = state["r1"]  # signed in at society A (mobile 9444444441)
    flat = {"society_id": state["society_b"], "wing_id": state["wing_b"], "unit_id": state["units_b"][1],
            "resident_type": "owner"}

    # a second home: details come from the profile, the admin of society B gets a request
    r = client.post(f"{API}/residents/me/homes", headers=auth(r1), json=flat)
    assert r.status_code == 201, r.text
    home = r.json()
    new_id = home["id"]
    assert home["status"] == "pending_approval" and home["society_id"] == state["society_b"]
    assert home["name"] == "Owner One" and home["mobile"] == "9444444441"
    pending = client.get(f"{API}/residents/pending", headers=auth(state["soa_b"])).json()
    assert new_id in [i["id"] for i in pending["items"]]

    # the same flat twice, or a flat that already has an approved owner, is refused
    assert client.post(f"{API}/residents/me/homes", headers=auth(r1), json=flat).status_code == 409
    taken = {**flat, "unit_id": state["units_b"][0]}
    assert client.post(f"{API}/residents/me/homes", headers=auth(r1), json=taken).status_code == 409

    # the proof goes to the new home, and only that society's admin can see it
    docs_before = len(client.get(f"{API}/residents/{state['r1_id']}", headers=auth(state["soa"])).json()["documents"])
    doc = client.post(f"{API}/residents/me/documents", headers=auth(r1), data={"doc_type": "index_ii", "resident_id": new_id},
                      files={"file": ("p.pdf", PDF, "application/pdf")})
    assert doc.status_code == 201, doc.text
    assert len(client.get(f"{API}/residents/{new_id}", headers=auth(state["soa_b"])).json()["documents"]) == 1
    assert len(client.get(f"{API}/residents/{state['r1_id']}", headers=auth(state["soa"])).json()["documents"]) == docs_before
    assert client.get(f"{API}/residents/{new_id}", headers=auth(state["soa"])).status_code == 404  # other society

    # both homes are listed, one is current
    homes = client.get(f"{API}/residents/me/homes", headers=auth(r1)).json()
    assert len(homes) == 2 and [h["current"] for h in homes].count(True) == 1
    pend = next(h for h in homes if h["id"] == new_id)
    assert pend["status"] == "pending_approval" and pend["society_name"]

    # society B approves, the resident switches and everything shows society B
    assert client.post(f"{API}/residents/{new_id}/approve", headers=auth(state["soa_b"])).status_code == 200
    sw = client.post(f"{API}/residents/me/switch/{new_id}", headers=auth(r1))
    assert sw.status_code == 200, sw.text
    token_b = sw.json()["access_token"]
    assert client.get(f"{API}/dashboard/resident/home", headers=auth(token_b)).json()["society"]["id"] == state["society_b"]
    assert client.get(f"{API}/group-deals/available", headers=auth(token_b)).status_code == 200

    # nobody can switch into somebody else's home
    stranger = client.get(f"{API}/residents/me", headers=auth(state["r2"])).json()["id"]
    assert client.post(f"{API}/residents/me/switch/{stranger}", headers=auth(r1)).status_code == 404

    # the next sign-in lands in the home used last, and switching back works
    again = otp_login(client, "9444444441", "resident")
    assert client.get(f"{API}/residents/me", headers=auth(again["access_token"])).json()["id"] == new_id
    back = client.post(f"{API}/residents/me/switch/{state['r1_id']}", headers=auth(token_b)).json()["access_token"]
    assert client.get(f"{API}/dashboard/resident/home", headers=auth(back)).json()["society"]["id"] == state["society_id"]
