from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import DealStatus


class GroupDeal(Base, TimestampMixin):
    __tablename__ = "group_deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL = platform-level deal (visible to residents of every active society)
    society_id: Mapped[int | None] = mapped_column(ForeignKey("societies.id"), index=True)
    title: Mapped[str] = mapped_column(String(190))
    category: Mapped[str] = mapped_column(String(50), index=True)
    subcategory: Mapped[str | None] = mapped_column(String(50), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    image: Mapped[str | None] = mapped_column(String(255))
    service_date: Mapped[date] = mapped_column(Date)
    slots: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["09:00-12:00", "14:00-17:00"]
    price_per_unit: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    max_units: Mapped[int] = mapped_column(Integer)
    # Maintained by the booking service; counts pending_payment + confirmed + completed only (never cancelled)
    confirmed_units: Mapped[int] = mapped_column(Integer, default=0)
    cutoff_date: Mapped[datetime] = mapped_column(DateTime)
    cancel_policy: Mapped[str | None] = mapped_column(Text)
    pay_later_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_promote_waitlist: Mapped[bool] = mapped_column(Boolean, default=True)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("service_partners.id"))
    partner_payout_per_unit: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False))
    status: Mapped[str] = mapped_column(String(20), default=DealStatus.DRAFT, index=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by_type: Mapped[str] = mapped_column(String(20))
    created_by_id: Mapped[int] = mapped_column(Integer)


class DealApplication(Base, TimestampMixin):
    """A vendor's request to serve a deal (optionally with the payout per unit they ask for)."""

    __tablename__ = "deal_applications"
    __table_args__ = (UniqueConstraint("deal_id", "partner_id", name="uq_deal_application"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("group_deals.id", ondelete="CASCADE"), index=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("service_partners.id", ondelete="CASCADE"), index=True)
    quote_per_unit: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="applied")  # applied | accepted | rejected | withdrawn
