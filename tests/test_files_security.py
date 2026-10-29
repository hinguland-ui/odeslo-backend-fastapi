"""Uploads: limits set by the super admin, content checks, encryption at rest, and who may open which file."""

import io
import os

from PIL import Image

from app.core.files import upload_root
from tests.conftest import API, PDF, PNG, auth


def _jpeg_with_exif() -> bytes:
    img = Image.new("RGB", (32, 32), (200, 40, 40))
    exif = img.getexif()
    exif[0x010F] = "SecretCamera"  # camera make: must not survive the upload
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _noise_png(side: int) -> bytes:
    img = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=0)
    return buf.getvalue()


def _complaint(client, token, photo=None, name="p.png", mime="image/png"):
    files = {"photo": (name, photo, mime)} if photo is not None else None
    return client.post(f"{API}/complaints", headers=auth(token), files=files,
                       data={"category": "plumbing", "description": "Leak in the bathroom"})


def _set_limits(client, state, **value):
    r = client.put(f"{API}/settings/uploads", headers=auth(state["sa"]), json={"value": value})
    assert r.status_code == 200, r.text
    return r.json()["value"]


def test_content_checks_and_super_admin_limits(client, state):
    r1 = state["r1"]
    # not a real image / renamed text / script-bearing PDF / SVG are all refused
    assert _complaint(client, r1, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).status_code == 400
    assert _complaint(client, r1, b"just some text", "x.jpg", "image/jpeg").status_code == 400
    assert _complaint(client, r1, b"<svg onload=alert(1)/>", "x.svg", "image/svg+xml").status_code == 400
    evil = b"%PDF-1.4\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj"
    r = client.post(f"{API}/requests", headers=auth(r1), data={"type": "noc", "details": "need a noc letter"},
                    files={"attachment": ("n.pdf", evil, "application/pdf")})
    assert r.status_code == 400 and "scripts" in r.json()["detail"]

    # the public limits endpoint (forms use it to warn before uploading)
    limits = client.get(f"{API}/settings/upload-limits").json()
    assert limits["max_image_mb"] == 5 and "pdf" in limits["document_formats"] and limits["hard_max_mb"] == 25

    # super admin lowers the image size: a bigger photo is refused, a small one still works
    big = _noise_png(700)  # about 1.5 MB
    assert len(big) > 1024 * 1024
    assert _complaint(client, r1, big).status_code == 201  # allowed at the default 5 MB
    saved = _set_limits(client, state, max_image_mb=1)
    assert saved["max_image_mb"] == 1
    r = _complaint(client, r1, big)
    assert r.status_code == 400 and "1 MB" in r.json()["detail"]
    assert _complaint(client, r1, PNG).status_code == 201

    # formats: no PDF for documents, no PNG for photos
    _set_limits(client, state, max_image_mb=5, document_formats=["jpg", "png"], image_formats=["jpg", "webp"])
    r = client.post(f"{API}/requests", headers=auth(r1), data={"type": "noc", "details": "need a noc letter"},
                    files={"attachment": ("n.pdf", PDF, "application/pdf")})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]
    assert _complaint(client, r1, PNG).status_code == 400
    assert _complaint(client, r1, _jpeg_with_exif(), "p.jpg", "image/jpeg").status_code == 201

    # sizes are clamped to the hard cap, formats not on the list are dropped
    saved = _set_limits(client, state, max_document_mb=999, image_formats=["jpg", "png", "exe"], document_formats=["pdf", "jpg", "png", "webp"])
    assert saved["max_document_mb"] == 25 and saved["image_formats"] == ["jpg", "png"]
    _set_limits(client, state, max_image_mb=5, max_document_mb=8, image_formats=["jpg", "png", "webp"])  # restore


def test_files_are_private_encrypted_and_cleaned(client, state):
    r1, r2, rb = state["r1"], state["r2"], state["rb"]
    sa, soa, soa_b = state["sa"], state["soa"], state["soa_b"]

    # a complaint photo: encrypted on disk, EXIF removed, only that flat + its admins can open it
    c = _complaint(client, r1, _jpeg_with_exif(), "p.jpg", "image/jpeg")
    assert c.status_code == 201, c.text
    path = c.json()["photo"]
    raw = (upload_root() / path).read_bytes()
    assert raw.startswith(b"ODFE1") and b"\xff\xd8\xff" not in raw[:64] and b"SecretCamera" not in raw

    ok = client.get(f"{API}/files/{path}", headers=auth(r1))
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["cache-control"] == "private, no-store" and ok.headers["x-content-type-options"] == "nosniff"
    assert not Image.open(io.BytesIO(ok.content)).getexif()  # camera details are gone
    assert client.get(f"{API}/files/{path}", headers=auth(soa)).status_code == 200
    assert client.get(f"{API}/files/{path}", headers=auth(sa)).status_code == 200
    for stranger in (r2, rb, soa_b):  # neighbour, other society, other society's admin
        assert client.get(f"{API}/files/{path}", headers=auth(stranger)).status_code == 404
    assert client.get(f"{API}/files/{path}").status_code == 401
    assert client.get(f"{API}/files/..%2F..%2Fetc%2Fpasswd", headers=auth(sa)).status_code == 404
    assert client.get(f"{API}/files/complaints/does-not-exist.jpg", headers=auth(sa)).status_code == 404

    # a resident's own document: the resident and their society admin only
    d = client.post(f"{API}/residents/me/documents", headers=auth(r1), data={"doc_type": "index_ii"},
                    files={"file": ("d.pdf", PDF, "application/pdf")})
    assert d.status_code == 201, d.text
    doc = d.json()["file_path"]
    assert client.get(f"{API}/files/{doc}", headers=auth(r1)).status_code == 200
    assert client.get(f"{API}/files/{doc}", headers=auth(soa)).status_code == 200
    assert client.get(f"{API}/files/{doc}", headers=auth(r2)).status_code == 404
    assert client.get(f"{API}/files/{doc}", headers=auth(soa_b)).status_code == 404
    # opening such a document is written to the activity log
    log = client.get(f"{API}/activity-logs?entity=stored_file", headers=auth(sa)).json()
    assert any(row["action"] == "file.view" for row in log["items"])

    # a vendor's ID proof: Odeslo super admin only, not even a society admin
    partner = next(p for p in client.get(f"{API}/service-partners", headers=auth(sa)).json()["items"] if p["mobile"] == "9666666661")
    assert client.get(f"{API}/files/{partner['id_proof']}", headers=auth(sa)).status_code == 200
    assert client.get(f"{API}/files/{partner['id_proof']}", headers=auth(soa)).status_code == 404
    assert client.get(f"{API}/files/{partner['photo']}", headers=auth(r1)).status_code == 404

    # a service-job photo: the flat that booked it, its society admin, nobody else
    job = client.get(f"{API}/service-jobs?status=completed", headers=auth(soa)).json()["items"][0]
    detail = client.get(f"{API}/service-jobs/{job['id']}", headers=auth(soa)).json()
    jphoto = next(t["photo"] for t in detail["timeline"] if t.get("photo"))
    assert client.get(f"{API}/files/{jphoto}", headers=auth(soa)).status_code == 200
    assert client.get(f"{API}/files/{jphoto}", headers=auth(state["r3"])).status_code == 200
    assert client.get(f"{API}/files/{jphoto}", headers=auth(r2)).status_code == 404
    assert client.get(f"{API}/files/{jphoto}", headers=auth(soa_b)).status_code == 404

    # replacing a photo deletes the old file for good
    p1 = client.post(f"{API}/residents/me/photo", headers=auth(r1), files={"file": ("a.png", PNG, "image/png")}).json()["photo"]
    p2 = client.post(f"{API}/residents/me/photo", headers=auth(r1), files={"file": ("b.png", PNG, "image/png")}).json()["photo"]
    assert p1 != p2
    assert client.get(f"{API}/files/{p1}", headers=auth(r1)).status_code == 404
    assert not (upload_root() / p1).exists() and (upload_root() / p2).exists()


def test_storage_usage_for_super_admin(client, state):
    usage = client.get(f"{API}/files/usage/summary", headers=auth(state["sa"])).json()
    assert usage["total_files"] > 0 and usage["total_bytes"] > 0
    a = next(s for s in usage["societies"] if s["society_id"] == state["society_id"])
    assert a["files"] > 0 and a["bytes"] > 0 and a["by_category"]
    assert usage["platform"]["files"] > 0  # vendor documents are platform level
    assert usage["societies"] == sorted(usage["societies"], key=lambda s: s["bytes"], reverse=True)
    assert client.get(f"{API}/files/usage/summary", headers=auth(state["soa"])).status_code == 403
    assert client.get(f"{API}/files/usage/summary", headers=auth(state["r1"])).status_code == 403
