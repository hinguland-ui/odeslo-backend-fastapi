"""Seed a development database with the pilot society (Ajit Residency) and demo logins.

    python -m scripts.seed_demo

Demo logins (OTP_DEV_MODE returns the OTP in the API response):
    Super Admin   : admin@odeslo.app / Admin@1234  (+ OTP to 9982925680)
    Society Admin : mobile 9000000002 (OTP)
    Resident      : mobile 9000000003 (OTP)  - owner of A-101
    Staff         : mobile 9000000004 (OTP)
    Partner       : mobile 9000000005 (OTP)
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select

import app.models  # noqa: F401
from app.core.database import Base, SessionLocal, engine
from app.core.enums import (
    AdminRole,
    DealStatus,
    MasjidStatus,
    ResidentStatus,
    ResidentType,
    SocietyStatus,
    UnitType,
)
from app.core.timeutils import now_ist
from app.modules.announcements.models import Announcement
from app.modules.group_deals.models import GroupDeal
from app.modules.masjids.models import Masjid, MasjidTiming
from app.modules.super_admins.models import SuperAdmin
from app.modules.residents.models import Resident
from app.modules.service_partners.models import PartnerSociety, ServicePartner
from app.modules.societies.models import Society, Unit, Wing
from app.modules.societies.service import refresh_unit_occupancy
from app.modules.society_admins.models import SocietyAdmin
from app.modules.staff.models import Staff
from app.modules.super_admins.service import create_super_admin


def ensure_demo_content(db) -> None:
    """Published group deals + notices for the pilot society (added only if missing)."""
    society = db.scalar(select(Society).where(Society.code == "AJR001"))
    sa = db.scalar(select(SuperAdmin))
    partner = db.scalar(select(ServicePartner).where(ServicePartner.mobile == "9000000005"))
    today = date.today()
    deals = [
        ("AC Normal Service", "ac", "Filter, coil & drain cleaning for split / window AC", 549, 25, 6,
         ["9 – 12 AM", "12 – 3 PM", "3 – 6 PM", "6 – 8 PM"], False),
        ("Pest Control — Full Flat", "pest_control", "Cockroach, ant & termite treatment, odourless gel", 899, 20, 9,
         ["10 – 1 PM", "2 – 5 PM"], True),
        ("Water Tank Cleaning", "water_tank_cleaning", "Overhead tank cleaning with anti-bacterial wash", 399, 30, 3,
         [], True),
    ]
    for title, cat, desc, price, max_units, cutoff_days, slots, pay_later in deals:
        if db.scalar(select(GroupDeal).where(GroupDeal.title == title, GroupDeal.society_id == society.id)):
            continue
        db.add(GroupDeal(
            society_id=society.id, title=title, category=cat, description=desc,
            service_date=today + timedelta(days=cutoff_days + 2), slots=slots, price_per_unit=price,
            max_units=max_units, cutoff_date=datetime.combine(today + timedelta(days=cutoff_days), time(20, 0)),
            cancel_policy="Free cancellation before the booking cut-off.", pay_later_allowed=pay_later,
            partner_id=partner.id if cat == "ac" else None, status=DealStatus.PUBLISHED, published_at=now_ist(),
            created_by_type="super_admin", created_by_id=sa.id,
        ))
    notices = [
        ("Water supply shutdown", "Water supply will be off on Saturday 10 AM – 2 PM in Wing A & B for tank cleaning.",
         "emergency", True),
        ("Society general meeting", "Monthly meeting on Sunday at 7 PM in the clubhouse. All owners please attend.",
         "meeting", False),
    ]
    for title, body, cat, pinned in notices:
        if not db.scalar(select(Announcement).where(Announcement.title == title, Announcement.society_id == society.id)):
            db.add(Announcement(society_id=society.id, title=title, body=body, category=cat, is_pinned=pinned,
                                created_by_type="super_admin", created_by=sa.id))
    db.commit()


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(Society).where(Society.code == "AJR001")):
            ensure_demo_content(db)
            print("Demo data already present (deals & notices ensured).")
            return
        sa = create_super_admin(db, "Platform Admin", "admin@odeslo.app", "9982925680", "Admin@1234")

        society = Society(
            code="AJR001", name="Ajit Residency", address="Plot 12, Kondhwa Main Road", area="Kondhwa",
            city="Pune", state="Maharashtra", pincode="411048", lat=18.4766, lng=73.8913, type="apartment",
            maintenance_settings={"amounts": {"flat": 2000, "duplex": 3000, "shop": 1500}, "billing_cycle": "monthly",
                                  "due_day": 10, "late_fee_enabled": True, "late_fee": 100},
            features={"group_deals": True, "masjid": True, "complaints": True, "requests": True,
                      "maintenance": True, "staff": True, "announcements": True},
            status=SocietyStatus.ACTIVE, created_by=sa.id,
        )
        db.add(society)
        db.flush()
        units = {}
        for wing_name in ("A", "B"):
            wing = Wing(society_id=society.id, name=wing_name)
            db.add(wing)
            db.flush()
            for floor in range(1, 4):
                for n in range(1, 5):
                    unit = Unit(society_id=society.id, wing_id=wing.id, unit_no=f"{floor}0{n}",
                                unit_type=UnitType.FLAT, floor=floor)
                    db.add(unit)
                    db.flush()
                    units[f"{wing_name}-{unit.unit_no}"] = unit

        db.add(SocietyAdmin(society_id=society.id, name="Society Secretary", mobile="9000000002",
                            email="secretary@ajitresidency.in", designation="secretary", role=AdminRole.PRIMARY,
                            permissions=[], must_set_password=True, created_by_type="super_admin",
                            created_by_id=sa.id))
        a101 = units["A-101"]
        db.add(Resident(society_id=society.id, wing_id=a101.wing_id, unit_id=a101.id, name="Demo Resident",
                        mobile="9000000003", resident_type=ResidentType.OWNER, is_primary=True,
                        status=ResidentStatus.ACTIVE, approved_at=now_ist(), approved_by=sa.id,
                        approved_by_type="super_admin", last_lat=society.lat, last_lng=society.lng))
        db.flush()
        refresh_unit_occupancy(db, a101.id)
        db.add(Staff(society_id=society.id, name="Ramesh (Guard)", mobile="9000000004", type="guard",
                     id_proof="seed/placeholder.pdf", photo="seed/placeholder.jpg"))
        partner = ServicePartner(name="CoolAir Services", mobile="9000000005", business_name="CoolAir Services",
                                 categories=["ac"], id_proof="seed/placeholder.pdf", photo="seed/placeholder.jpg",
                                 verified=True, verified_at=now_ist(), verified_by=sa.id)
        db.add(partner)
        db.flush()
        db.add(PartnerSociety(partner_id=partner.id, society_id=society.id))

        masjid = Masjid(name="Masjid-e-Noor", address="Kondhwa Khurd, Pune", area="Kondhwa", city="Pune",
                        lat=18.4790, lng=73.8930, added_by_type="super_admin", added_by=sa.id, verified=True,
                        status=MasjidStatus.VERIFIED, verified_by=sa.id, verified_at=now_ist())
        db.add(masjid)
        db.flush()
        db.add(MasjidTiming(masjid_id=masjid.id, fajr="05:45", dhuhr="13:30", asr="17:15", maghrib="18:25",
                            isha="20:00", jummah="13:45", effective_from=date.today() - timedelta(days=30),
                            verified=True, status=MasjidStatus.VERIFIED, submitted_by_type="super_admin",
                            submitted_by=sa.id, verified_by=sa.id, verified_at=now_ist()))
        db.commit()
        ensure_demo_content(db)
        print(__doc__)


if __name__ == "__main__":
    main()
