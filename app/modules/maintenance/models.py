from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import BillStatus


class MaintenanceBill(Base, TimestampMixin):
    __tablename__ = "maintenance_bills"
    __table_args__ = (UniqueConstraint("unit_id", "period", name="uq_bill_unit_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    resident_id: Mapped[int | None] = mapped_column(ForeignKey("residents.id"))  # primary resident at generation
    period: Mapped[str] = mapped_column(String(10), index=True)  # 2026-10 | 2026-Q4
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    late_fee: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0)
    paid_amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default=BillStatus.GENERATED, index=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    generated_by: Mapped[int | None] = mapped_column(Integer)

    @property
    def total_amount(self) -> float:
        return round((self.amount or 0) + (self.late_fee or 0), 2)

    @property
    def outstanding(self) -> float:
        return round(max(self.total_amount - (self.paid_amount or 0), 0), 2)
