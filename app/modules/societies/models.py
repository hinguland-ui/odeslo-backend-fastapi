from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import OccupancyStatus, SocietyStatus, UnitType


class Society(Base, TimestampMixin):
    __tablename__ = "societies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(190))
    address: Mapped[str] = mapped_column(Text)
    area: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(100))
    pincode: Mapped[str] = mapped_column(String(10))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    type: Mapped[str] = mapped_column(String(20))
    registration_number: Mapped[str | None] = mapped_column(String(100))
    logo: Mapped[str | None] = mapped_column(String(255))
    # {"amounts": {"flat": 2000, ...}, "billing_cycle": "monthly", "due_day": 10, "late_fee_enabled": true, "late_fee": 100}
    maintenance_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"group_deals": true, "masjid": true, ...}
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    require_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default=SocietyStatus.PENDING, index=True)
    created_by: Mapped[int | None] = mapped_column(Integer)


class Wing(Base, TimestampMixin):
    __tablename__ = "wings"
    __table_args__ = (UniqueConstraint("society_id", "name", name="uq_wing_society_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))


class Unit(Base, TimestampMixin):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("wing_id", "unit_no", name="uq_unit_wing_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id", ondelete="CASCADE"), index=True)
    wing_id: Mapped[int] = mapped_column(ForeignKey("wings.id", ondelete="CASCADE"), index=True)
    unit_no: Mapped[str] = mapped_column(String(20))
    unit_type: Mapped[str] = mapped_column(String(20), default=UnitType.FLAT)
    floor: Mapped[int | None] = mapped_column(Integer)
    occupancy_status: Mapped[str] = mapped_column(String(20), default=OccupancyStatus.VACANT)
