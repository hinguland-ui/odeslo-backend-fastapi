from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import ComplaintStatus, Priority
from app.core.timeutils import now_ist


class Complaint(Base, TimestampMixin):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    category: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text)
    photo: Mapped[str | None] = mapped_column(String(255))
    priority: Mapped[str] = mapped_column(String(10), default=Priority.MEDIUM)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ComplaintStatus.OPEN, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)


class ComplaintComment(Base):
    __tablename__ = "complaint_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    author_type: Mapped[str] = mapped_column(String(20))
    author_id: Mapped[int] = mapped_column(Integer)
    author_name: Mapped[str] = mapped_column(String(120))
    comment: Mapped[str] = mapped_column(Text)
    status_change: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
