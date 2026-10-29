from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import AccountStatus


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(30))  # guard | housekeeping | plumber | electrician ...
    category: Mapped[str | None] = mapped_column(String(50))
    id_proof: Mapped[str] = mapped_column(String(255))
    photo: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=AccountStatus.ACTIVE)
    created_by: Mapped[int | None] = mapped_column(Integer)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
