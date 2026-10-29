from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import MasjidStatus


class Masjid(Base, TimestampMixin):
    __tablename__ = "masjids"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(190))
    address: Mapped[str] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(100))
    lat: Mapped[float] = mapped_column(Float, index=True)
    lng: Mapped[float] = mapped_column(Float, index=True)
    contact: Mapped[str | None] = mapped_column(String(20))
    imam_name: Mapped[str | None] = mapped_column(String(120))
    photo: Mapped[str | None] = mapped_column(String(255))
    has_ladies_section: Mapped[bool] = mapped_column(Boolean, default=False)
    has_parking: Mapped[bool] = mapped_column(Boolean, default=False)
    society_id: Mapped[int | None] = mapped_column(ForeignKey("societies.id"))  # society that added it
    added_by_type: Mapped[str] = mapped_column(String(20))
    added_by: Mapped[int] = mapped_column(Integer)
    is_suggestion: Mapped[bool] = mapped_column(Boolean, default=False)  # suggested by a resident
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default=MasjidStatus.PENDING_VERIFICATION, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[int | None] = mapped_column(Integer)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)


class MasjidTiming(Base, TimestampMixin):
    """Jamaat timetable of a masjid. The latest verified row with effective_from <= date is used."""

    __tablename__ = "masjid_timings"

    id: Mapped[int] = mapped_column(primary_key=True)
    masjid_id: Mapped[int] = mapped_column(ForeignKey("masjids.id", ondelete="CASCADE"), index=True)
    fajr: Mapped[str] = mapped_column(String(5))
    dhuhr: Mapped[str] = mapped_column(String(5))
    asr: Mapped[str] = mapped_column(String(5))
    maghrib: Mapped[str] = mapped_column(String(5))
    isha: Mapped[str] = mapped_column(String(5))
    jummah: Mapped[str | None] = mapped_column(String(5))
    effective_from: Mapped[date] = mapped_column(Date)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default=MasjidStatus.PENDING_VERIFICATION, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    submitted_by_type: Mapped[str] = mapped_column(String(20))
    submitted_by: Mapped[int] = mapped_column(Integer)
    verified_by: Mapped[int | None] = mapped_column(Integer)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
