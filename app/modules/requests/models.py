from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import RequestStatus


class ResidentRequest(Base, TimestampMixin):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    type: Mapped[str] = mapped_column(String(30))
    details: Mapped[str] = mapped_column(Text)
    attachment: Mapped[str | None] = mapped_column(String(255))
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    status: Mapped[str] = mapped_column(String(20), default=RequestStatus.OPEN, index=True)
    admin_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
