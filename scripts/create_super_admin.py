"""Create a Super Admin (no public signup exists for this role).

    python -m scripts.create_super_admin --name "Akmal" --email akmal@example.com --mobile 9876543210
"""

import argparse
import getpass

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.utils import normalize_mobile
from app.modules.super_admins.service import create_super_admin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--mobile", required=True)
    parser.add_argument("--password", help="omit to be prompted securely")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        raise SystemExit(f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = create_super_admin(db, args.name, args.email, normalize_mobile(args.mobile), password)
        db.commit()
        print(f"Super admin created: id={admin.id} email={admin.email}")


if __name__ == "__main__":
    main()
