from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import JobStatus, PayoutStatus
from app.core.timeutils import now_ist


class ServiceJob(Base, TimestampMixin):
    __tablename__ = "service_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), unique=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("group_deals.id"), index=True)
    # empty once the society was deleted: the job and its payout stay as the vendor's history
    society_id: Mapped[int | None] = mapped_column(ForeignKey("societies.id"), index=True)
    society_name: Mapped[str | None] = mapped_column(String(190))
    resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    title: Mapped[str] = mapped_column(String(190))
    assigned_to_type: Mapped[str | None] = mapped_column(String(20))  # staff | partner
    assigned_to_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.CREATED, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    slot: Mapped[str | None] = mapped_column(String(50))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    photo: Mapped[str | None] = mapped_column(String(255))
    issue_note: Mapped[str | None] = mapped_column(Text)
    escalated: Mapped[bool] = mapped_column(default=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str | None] = mapped_column(Text)
    payout_amount: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False))
    payout_status: Mapped[str] = mapped_column(String(20), default=PayoutStatus.NOT_APPLICABLE)
    payout_reference: Mapped[str | None] = mapped_column(String(100))


class ServiceJobUpdate(Base):
    """Status history of a service job (timeline shown to resident / admin)."""

    __tablename__ = "service_job_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("service_jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    photo: Mapped[str | None] = mapped_column(String(255))
    updated_by_type: Mapped[str] = mapped_column(String(20))
    updated_by_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)


class JobMessage(Base):
    """Chat on a service job between the resident, the assigned vendor / staff and the society admin."""

    __tablename__ = "service_job_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("service_jobs.id", ondelete="CASCADE"), index=True)
    sender_type: Mapped[str] = mapped_column(String(20))
    sender_id: Mapped[int] = mapped_column(Integer)
    sender_name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)


class JobLocation(Base):
    """Latest live position of the vendor while a job is active (one row per job)."""

    __tablename__ = "service_job_locations"

    job_id: Mapped[int] = mapped_column(ForeignKey("service_jobs.id", ondelete="CASCADE"), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, onupdate=now_ist)
