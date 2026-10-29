from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.timeutils import now_ist


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user", "user_type", "user_id", "is_read"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_type: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(190))
    body: Mapped[str] = mapped_column(Text)
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)


class DeviceToken(Base, TimestampMixin):
    __tablename__ = "device_tokens"
    __table_args__ = (UniqueConstraint("fcm_token", name="uq_device_token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_type: Mapped[str] = mapped_column(String(20), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    fcm_token: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(20))  # android | ios | web
