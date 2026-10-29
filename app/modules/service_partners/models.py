from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import AccountStatus
from app.core.timeutils import now_ist


class ServicePartner(Base, TimestampMixin):
    __tablename__ = "service_partners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(190))
    business_name: Mapped[str | None] = mapped_column(String(190))
    gst: Mapped[str | None] = mapped_column(String(20))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    id_proof: Mapped[str] = mapped_column(String(255))
    photo: Mapped[str] = mapped_column(String(255))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    verified_by: Mapped[int | None] = mapped_column(Integer)
    bank_details: Mapped[dict | None] = mapped_column(JSON)  # {"account_name","account_no","ifsc","upi_id"}
    rating_avg: Mapped[float] = mapped_column(Float, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=AccountStatus.ACTIVE)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class PartnerSociety(Base):
    __tablename__ = "partner_societies"

    partner_id: Mapped[int] = mapped_column(ForeignKey("service_partners.id", ondelete="CASCADE"), primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id", ondelete="CASCADE"), primary_key=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
