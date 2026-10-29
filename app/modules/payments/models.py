from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import PaymentStatus


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    txn_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # internal transaction ID
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    ref_type: Mapped[str] = mapped_column(String(20))  # booking | maintenance
    ref_id: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    mode: Mapped[str] = mapped_column(String(20))  # online | cash | cheque | upi | bank_transfer
    gateway: Mapped[str | None] = mapped_column(String(20))
    gateway_order_id: Mapped[str | None] = mapped_column(String(100), index=True)
    gateway_txn_id: Mapped[str | None] = mapped_column(String(100), index=True)
    gateway_signature: Mapped[str | None] = mapped_column(String(255))
    reference_no: Mapped[str | None] = mapped_column(String(100))  # cheque no / UPI ref for offline payments
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.INITIATED, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_type: Mapped[str | None] = mapped_column(String(20))
    recorded_by_id: Mapped[int | None] = mapped_column(Integer)
    raw_response: Mapped[dict | None] = mapped_column(JSON)
