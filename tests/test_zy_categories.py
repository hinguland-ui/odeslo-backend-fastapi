"""Editable service categories: the super admin adds categories and sub-categories, deals must use them."""

from datetime import date, datetime, timedelta

from tests.conftest import API, auth


def test_categories_and_subcategories(client, state):
    sa, soa = state["sa"], state["soa"]
    names = {c["key"] for c in client.get(f"{API}/categories").json()}
    assert {"ac", "plumbing", "other"} <= names  # the built-in ones are seeded

    # only the super admin edits categories
    assert client.post(f"{API}/categories", headers=auth(soa), json={"name": "Solar"}).status_code == 403

    main = client.post(f"{API}/categories", headers=auth(sa), json={"name": "Solar panels"})
    assert main.status_code == 201, main.text
    main = main.json()
    assert main["key"] == "solar_panels" and main["parent_id"] is None
    assert client.post(f"{API}/categories", headers=auth(sa), json={"name": "solar panels"}).status_code == 400  # duplicate

    sub = client.post(f"{API}/categories", headers=auth(sa), json={"name": "Panel cleaning", "parent_id": main["id"]}).json()
    assert sub["parent_id"] == main["id"]
    # a sub-category cannot have children
    assert client.post(f"{API}/categories", headers=auth(sa), json={"name": "Deep", "parent_id": sub["id"]}).status_code == 400

    # deals accept the new category and its sub-category, and refuse mismatches
    body = {"title": "Rooftop clean", "category": main["key"], "subcategory": sub["key"], "service_date": str(date.today() + timedelta(days=5)),
            "slots": ["09:00-12:00"], "price_per_unit": 800, "max_units": 5, "cutoff_date": (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")}
    ok = client.post(f"{API}/group-deals", headers=auth(soa), json=body)
    assert ok.status_code == 201, ok.text
    assert ok.json()["subcategory"] == sub["key"]
    assert client.post(f"{API}/group-deals", headers=auth(soa), json={**body, "category": "ac"}).status_code == 400
    assert client.post(f"{API}/group-deals", headers=auth(soa), json={**body, "category": "nope", "subcategory": None}).status_code == 400

    # in use -> cannot be deleted, only hidden; renaming keeps the key
    assert client.delete(f"{API}/categories/{sub['id']}", headers=auth(sa)).status_code == 400
    renamed = client.patch(f"{API}/categories/{main['id']}", headers=auth(sa), json={"name": "Solar & inverters"}).json()
    assert renamed["name"] == "Solar & inverters" and renamed["key"] == "solar_panels"
    hidden = client.patch(f"{API}/categories/{main['id']}", headers=auth(sa), json={"active": False}).json()
    assert hidden["active"] is False
    listed = {c["key"] for c in client.get(f"{API}/categories").json()}
    assert "solar_panels" not in listed and sub["key"] not in listed  # hiding the main one hides its children
    assert client.post(f"{API}/group-deals", headers=auth(soa), json=body).status_code == 400

    # an unused category can be removed
    junk = client.post(f"{API}/categories", headers=auth(sa), json={"name": "Temporary"}).json()
    assert client.delete(f"{API}/categories/{junk['id']}", headers=auth(sa)).status_code == 204
