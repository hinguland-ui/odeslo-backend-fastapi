"""Places of worship: the society admin keeps a list (any religion, free text timings, photos); residents read it."""

from tests.conftest import API, PNG, auth


def test_worship_places(client, state):
    soa, soa_b, r1 = state["soa"], state["soa_b"], state["r1"]
    sid = state["society_id"]

    body = {
        "kind": "mandir", "name": "Shri Hanuman Mandir", "dedicated_to": "Hanuman ji", "description": "Next to the main gate",
        "contact": "9876543210",
        "timings": [{"label": "Morning aarti", "time": "6:30 AM"}, {"label": "Evening aarti", "time": "7:15 PM", "note": "Tuesday: 8 PM"}],
    }
    # a resident cannot add
    assert client.post(f"{API}/worship-places", headers=auth(r1), json=body).status_code == 403
    # an unknown kind is refused
    assert client.post(f"{API}/worship-places", headers=auth(soa), json={**body, "kind": "cinema"}).status_code == 422

    made = client.post(f"{API}/worship-places", headers=auth(soa), json=body)
    assert made.status_code == 201, made.text
    place = made.json()
    assert place["society_id"] == sid and len(place["timings"]) == 2 and place["photos"] == []

    masjid = client.post(f"{API}/worship-places", headers=auth(soa), json={"kind": "masjid", "name": "Society Masjid", "timings": [{"label": "Jummah", "time": "1:15 PM"}]})
    assert masjid.status_code == 201

    # photos
    ph = client.post(f"{API}/worship-places/{place['id']}/photos", headers=auth(soa), files={"photo": ("a.png", PNG, "image/png")})
    assert ph.status_code == 200, ph.text
    path = ph.json()["photos"][0]
    got = client.get(f"{API}/files/{path}", headers=auth(r1))
    assert got.status_code == 200  # residents of the society can see it
    assert 'filename="a.png"' in got.headers["content-disposition"]  # the real name, not the stored id

    # residents see the list (read only); another society's admin does not see or touch it
    listed = client.get(f"{API}/worship-places", headers=auth(r1)).json()
    assert {p["name"] for p in listed} == {"Shri Hanuman Mandir", "Society Masjid"}
    assert client.get(f"{API}/worship-places", headers=auth(soa_b)).json() == []
    assert client.get(f"{API}/worship-places/{place['id']}", headers=auth(soa_b)).status_code == 404
    assert client.patch(f"{API}/worship-places/{place['id']}", headers=auth(soa_b), json={"name": "Xyz"}).status_code == 404
    assert client.patch(f"{API}/worship-places/{place['id']}", headers=auth(r1), json={"name": "Xyz"}).status_code == 403
    assert len(client.get(f"{API}/worship-places?kind=mandir", headers=auth(r1)).json()) == 1

    # edit timings, then hide it from residents
    upd = client.patch(f"{API}/worship-places/{place['id']}", headers=auth(soa), json={"timings": [{"label": "Aarti", "time": "6:00 AM"}]})
    assert upd.status_code == 200 and upd.json()["timings"] == [{"label": "Aarti", "time": "6:00 AM", "note": None}]
    client.patch(f"{API}/worship-places/{place['id']}", headers=auth(soa), json={"is_active": False})
    assert {p["name"] for p in client.get(f"{API}/worship-places", headers=auth(r1)).json()} == {"Society Masjid"}
    assert client.get(f"{API}/worship-places/{place['id']}", headers=auth(r1)).status_code == 404

    # a time must be a real clock time; free text goes in the note
    assert client.post(f"{API}/worship-places", headers=auth(soa), json={**body, "timings": [{"label": "Aarti", "time": "after sunset"}]}).status_code == 422
    assert client.post(f"{API}/worship-places", headers=auth(soa), json={**body, "timings": [{"label": "Aarti", "time": "13:70 PM"}]}).status_code == 422

    # replacing a photo keeps its place in the list and deletes the old file (no extra copy is kept)
    client.patch(f"{API}/worship-places/{place['id']}", headers=auth(soa), json={"is_active": True})
    second = client.post(f"{API}/worship-places/{place['id']}/photos", headers=auth(soa), files={"photo": ("b.png", PNG, "image/png")}).json()["photos"]
    assert len(second) == 2 and second[0] == path
    swapped = client.post(f"{API}/worship-places/{place['id']}/photos", headers=auth(soa), params={"replace": path}, files={"photo": ("c.png", PNG, "image/png")})
    assert swapped.status_code == 200, swapped.text
    now = swapped.json()["photos"]
    assert len(now) == 2 and now[1] == second[1] and now[0] not in (path, second[1])
    assert client.get(f"{API}/files/{path}", headers=auth(soa)).status_code == 404  # the old file is gone
    assert client.get(f"{API}/files/{now[0]}", headers=auth(r1)).status_code == 200
    assert client.post(f"{API}/worship-places/{place['id']}/photos", headers=auth(soa), params={"replace": "nope.png"}, files={"photo": ("d.png", PNG, "image/png")}).status_code == 404
    path = now[0]
    assert client.delete(f"{API}/worship-places/{place['id']}/photos", headers=auth(soa), params={"path": now[1]}).status_code == 200

    # remove a photo, then the place
    assert client.delete(f"{API}/worship-places/{place['id']}/photos", headers=auth(soa), params={"path": path}).json()["photos"] == []
    assert client.delete(f"{API}/worship-places/{place['id']}", headers=auth(soa)).status_code == 204
    assert client.delete(f"{API}/worship-places/{masjid.json()['id']}", headers=auth(soa)).status_code == 204
