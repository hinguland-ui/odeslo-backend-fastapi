from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.timeutils import now_ist


class StoredFile(Base):
    """Every uploaded file: who may read it (audience) and how much space it uses.

    Audience: society (everyone of that society, or all users when society_id is empty) | unit (residents of one flat)
    | owner_admin (the person it belongs to + the society admins) | admin (society admins only)
    | job (people on one service job) | platform_admin (Odeslo super admins only).
    """

    __tablename__ = "stored_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    path: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    audience: Mapped[str] = mapped_column(String(20))
    society_id: Mapped[int | None] = mapped_column(Integer, index=True)
    unit_id: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(Integer)
    uploader_type: Mapped[str | None] = mapped_column(String(20))
    uploader_id: Mapped[int | None] = mapped_column(Integer)
    original_name: Mapped[str | None] = mapped_column(String(190))  # the name the person gave the file, shown when it is opened
    mime: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
