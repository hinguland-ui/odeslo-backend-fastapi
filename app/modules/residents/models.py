from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base, TimestampMixin
from app.core.enums import DocumentStatus, ResidentStatus


class Resident(Base, TimestampMixin):
    __tablename__ = "residents"
    # A person can live in several flats / societies: one row per home, but never two rows for the same flat.
    __table_args__ = (Index("uq_resident_mobile_unit", "mobile", "unit_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    wing_id: Mapped[int] = mapped_column(ForeignKey("wings.id"))
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(15), index=True)
    email: Mapped[str | None] = mapped_column(String(190))
    resident_type: Mapped[str] = mapped_column(String(20))  # owner | tenant | family
    # One unit = one active primary account (owner or tenant). Family members are never primary.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"))
    photo: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default=ResidentStatus.PENDING_APPROVAL, index=True)
    unit_occupied_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(Integer)
    approved_by_type: Mapped[str | None] = mapped_column(String(20))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120))
    emergency_contact_mobile: Mapped[str | None] = mapped_column(String(15))
    # tenant only: whether the owner allows the tenant to see maintenance
    maintenance_visible: Mapped[bool] = mapped_column(Boolean, default=True)

    # Settings
    notification_prefs: Mapped[dict] = mapped_column(JSON, default=dict)  # {"deal": false, ...} push opt-outs
    namaz_alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    namaz_alert_minutes: Mapped[int] = mapped_column(Integer, default=settings.DEFAULT_NAMAZ_ALERT_MINUTES)
    preferred_masjid_id: Mapped[int | None] = mapped_column(Integer)
    location_permission: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_permission: Mapped[bool] = mapped_column(Boolean, default=False)
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lng: Mapped[float | None] = mapped_column(Float)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class ResidentDocument(Base, TimestampMixin):
    __tablename__ = "resident_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(30))
    file_path: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.PENDING)


class FamilyMember(Base, TimestampMixin):
    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    relation: Mapped[str] = mapped_column(String(50))
    mobile: Mapped[str | None] = mapped_column(String(15))
    # set when the family member was given their own app login
    linked_resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"))


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id", ondelete="CASCADE"), index=True)
    vehicle_type: Mapped[str] = mapped_column(String(30))  # car | bike | other
    vehicle_number: Mapped[str] = mapped_column(String(20))
    parking_slot: Mapped[str | None] = mapped_column(String(30))
