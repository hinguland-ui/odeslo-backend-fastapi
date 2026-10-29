from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class LocationRequest(Base, TimestampMixin):
    """A society admin asks to move the society's pin (and optionally its address). The super admin decides."""

    __tablename__ = "location_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    # where it is now, kept so the decision can be reviewed later even after the society moved
    old_lat: Mapped[float] = mapped_column(Float)
    old_lng: Mapped[float] = mapped_column(Float)
    old_address: Mapped[str | None] = mapped_column(Text)
    new_lat: Mapped[float] = mapped_column(Float)
    new_lng: Mapped[float] = mapped_column(Float)
    # address of the new spot; only the parts the admin chose to send are filled
    new_address: Mapped[str | None] = mapped_column(Text)
    new_area: Mapped[str | None] = mapped_column(String(120))
    new_city: Mapped[str | None] = mapped_column(String(100))
    new_state: Mapped[str | None] = mapped_column(String(100))
    new_pincode: Mapped[str | None] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | approved | rejected | cancelled
    requested_by: Mapped[int] = mapped_column(Integer)
    decided_by: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
