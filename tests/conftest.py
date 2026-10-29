import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="odeslo-test-")
# Set TEST_DATABASE_URL to run against PostgreSQL (use a separate, empty test database)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["OTP_DEV_MODE"] = "true"
os.environ["PAYMENT_GATEWAY"] = "mock"
os.environ["OTP_MAX_PER_WINDOW"] = "1000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import event  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.main import app  # noqa: E402


@event.listens_for(engine, "connect")
def _foreign_keys_on(dbapi_connection, _record):
    """SQLite ignores foreign keys unless told: turn them on so wrong delete orders fail in tests like on PostgreSQL."""
    if engine.dialect.name == "sqlite":
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

def _real_png(size=(8, 8), color=(20, 120, 200)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


PNG = _real_png()  # uploads are decoded and re-encoded, so test images must be real
PDF = b"%PDF-1.4\n" + b"0" * 64
API = "/api/v1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def state():
    return {}


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def otp_login(client, mobile: str, user_type: str) -> dict:
    r = client.post(f"{API}/auth/otp/send", json={"mobile": mobile, "user_type": user_type})
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/auth/otp/verify", json={"mobile": mobile, "otp": r.json()["dev_otp"],
                                                    "user_type": user_type})
    assert r.status_code == 200, r.text
    return r.json()
