from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import AccountStatus, AdminRole


class SocietyAdmin(Base, TimestampMixin):
    __tablename__ = "society_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(15), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    designation: Mapped[str] = mapped_column(String(30))
    role: Mapped[str] = mapped_column(String(20), default=AdminRole.CO_ADMIN)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    id_proof: Mapped[str | None] = mapped_column(String(255))
    authorisation_letter: Mapped[str | None] = mapped_column(String(255))
    must_set_password: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default=AccountStatus.ACTIVE)
    created_by_type: Mapped[str | None] = mapped_column(String(20))
    created_by_id: Mapped[int | None] = mapped_column(Integer)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
