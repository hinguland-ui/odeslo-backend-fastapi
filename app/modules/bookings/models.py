from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import BookingPaymentStatus, BookingStatus


class Booking(Base, TimestampMixin):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("group_deals.id"), index=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    units_count: Mapped[int] = mapped_column(Integer, default=1)
    slot: Mapped[str | None] = mapped_column(String(50))
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    status: Mapped[str] = mapped_column(String(20), index=True)
    payment_status: Mapped[str] = mapped_column(String(20), default=BookingPaymentStatus.UNPAID)
    waitlist_position: Mapped[int | None] = mapped_column(Integer)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime)  # pending_payment capacity hold
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by_type: Mapped[str | None] = mapped_column(String(20))
    cancelled_by_id: Mapped[int | None] = mapped_column(Integer)
    booked_by_type: Mapped[str] = mapped_column(String(20))
    booked_by_id: Mapped[int] = mapped_column(Integer)

    @property
    def is_waitlisted(self) -> bool:
        return self.status == BookingStatus.WAITLISTED
