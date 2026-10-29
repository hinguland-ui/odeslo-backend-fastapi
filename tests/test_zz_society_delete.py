"""Super admin deletes a society for good: its people and data go, vendors and their payment history stay."""

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.files import upload_root
from app.modules.activity_logs.models import ActivityLog
from app.modules.files.models import StoredFile
from tests.conftest import API, auth, otp_login


def test_delete_society_keeps_vendor_history(client, state):
    sa, sid = state["sa"], state["society_id"]
    partner = next(p for p in client.get(f"{API}/service-partners", headers=auth(sa)).json()["items"] if p["mobile"] == "9666666661")
    job = client.get(f"{API}/service-jobs?status=completed&society_id={sid}", headers=auth(sa)).json()["items"][0]
    # the society pays this vendor: that payment record must outlive the society
    paid = client.post(f"{API}/service-jobs/{job['id']}/payout", headers=auth(sa), json={"reference": "UTR123456", "amount": 450})
    assert paid.status_code == 200, paid.text
    job = client.get(f"{API}/service-jobs/{job['id']}", headers=auth(sa)).json()
    earnings_before = client.get(f"{API}/service-partners/{partner['id']}/earnings", headers=auth(sa)).json()
    assert earnings_before["completed_jobs"] >= 1 and earnings_before["total_earned"] == 450 and earnings_before["paid"] == 450
    with SessionLocal() as db:
        paths = list(db.scalars(select(StoredFile.path).where(StoredFile.society_id == sid)))
    assert paths and all((upload_root() / p).exists() for p in paths)

    # the confirmation screen lists what goes and what stays
    pv = client.get(f"{API}/societies/{sid}/delete-preview", headers=auth(sa)).json()
    d, k = pv["delete"], pv["keep"]
    assert d["residents"] >= 3 and d["flats"] > 0 and d["bookings"] > 0 and d["payments"] > 0 and d["files"] > 0
    assert k["vendor_jobs"] >= 1 and k["vendors_assigned"] >= 1

    # only a super admin, and only with the society's own code
    assert client.get(f"{API}/societies/{sid}/delete-preview", headers=auth(state["soa"])).status_code == 403
    assert client.post(f"{API}/societies/{sid}/delete", headers=auth(state["soa"]), json={"confirm_code": pv["society"]["code"]}).status_code == 403
    r = client.post(f"{API}/societies/{sid}/delete", headers=auth(sa), json={"confirm_code": "WRONG1"})
    assert r.status_code == 400
    assert client.get(f"{API}/societies/{sid}", headers=auth(sa)).status_code == 200  # nothing happened

    r = client.post(f"{API}/societies/{sid}/delete", headers=auth(sa), json={"confirm_code": pv["society"]["code"].lower()})
    assert r.status_code == 200, r.text
    assert r.json()["files_removed"] == len(paths)

    # the society, its people and its data are gone
    assert client.get(f"{API}/societies/{sid}", headers=auth(sa)).status_code == 404
    assert client.get(f"{API}/dashboard/resident/home", headers=auth(state["r2"])).status_code == 401  # login of a deleted resident
    assert client.get(f"{API}/dashboard/society-admin", headers=auth(state["soa"])).status_code == 401
    assert all(s["id"] != sid for s in client.get(f"{API}/societies", headers=auth(sa)).json()["items"])
    assert client.get(f"{API}/residents?society_id={sid}&status=active", headers=auth(sa)).json()["total"] == 0
    assert client.get(f"{API}/group-deals?society_id={sid}", headers=auth(sa)).json()["total"] == 0
    assert client.get(f"{API}/bookings?society_id={sid}", headers=auth(sa)).json()["total"] == 0
    assert client.get(f"{API}/payments?society_id={sid}", headers=auth(sa)).json()["total"] == 0
    assert client.get(f"{API}/maintenance/bills?society_id={sid}", headers=auth(sa)).json()["total"] == 0

    # every file of the society is gone: registry and disk
    with SessionLocal() as db:
        assert db.scalar(select(StoredFile.id).where(StoredFile.society_id == sid)) is None
        assert db.scalar(select(StoredFile.id).where(StoredFile.path.in_(paths))) is None
        log = db.scalars(select(ActivityLog).where(ActivityLog.action == "society.delete")).all()
    assert not any((upload_root() / p).exists() for p in paths)
    assert len(log) == 1 and log[0].society_id is None and log[0].old_value["code"] == pv["society"]["code"]  # the only trace

    # the vendor, their earnings and the job with its payout status are untouched
    assert client.get(f"{API}/service-partners/{partner['id']}", headers=auth(sa)).status_code == 200
    earnings_after = client.get(f"{API}/service-partners/{partner['id']}/earnings", headers=auth(sa)).json()
    assert earnings_after == earnings_before
    kept = client.get(f"{API}/service-jobs/{job['id']}", headers=auth(sa)).json()
    assert kept["status"] == "completed" and kept["society_id"] is None and kept["society_name"]
    assert kept["payout_status"] == "paid" and kept["payout_amount"] == 450 and kept["payout_reference"] == "UTR123456"
    assert kept["resident_name"] is None and kept["resident_id"] is None and kept["notes"] is None and kept["photo"] is None
    assert client.get(f"{API}/service-jobs/{job['id']}/messages", headers=auth(sa)).json() == []
    assert sid not in client.get(f"{API}/service-partners/{partner['id']}", headers=auth(sa)).json()["society_ids"]
    # the vendor's own job list still works and shows that job
    login = otp_login(client, "9666666661", "partner")
    mine = client.get(f"{API}/service-jobs/my?view=history", headers=auth(login["access_token"])).json()
    assert any(j["id"] == job["id"] for j in mine)

    # another society is untouched, and a person with a home there keeps it
    assert client.get(f"{API}/dashboard/resident/home", headers=auth(state["rb"])).status_code == 200
    again = otp_login(client, "9444444441", "resident")  # had a home in the deleted society and one in society B
    assert client.get(f"{API}/dashboard/resident/home", headers=auth(again["access_token"])).json()["society"]["id"] == state["society_b"]
