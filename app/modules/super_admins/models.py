from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import AccountStatus


class SuperAdmin(Base, TimestampMixin):
    __tablename__ = "super_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    mobile: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=AccountStatus.ACTIVE)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
