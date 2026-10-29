from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import RefundStatus


class Refund(Base, TimestampMixin):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), index=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=RefundStatus.REQUESTED, index=True)
    mode: Mapped[str | None] = mapped_column(String(20))  # gateway | manual
    gateway_refund_id: Mapped[str | None] = mapped_column(String(100))
    reference_no: Mapped[str | None] = mapped_column(String(100))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    requested_by_type: Mapped[str | None] = mapped_column(String(20))
    requested_by_id: Mapped[int | None] = mapped_column(Integer)
    approved_by_type: Mapped[str | None] = mapped_column(String(20))
    approved_by: Mapped[int | None] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
