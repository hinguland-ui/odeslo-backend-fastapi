from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.enums import AnnouncementCategory


class Announcement(Base, TimestampMixin):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int | None] = mapped_column(ForeignKey("societies.id"), index=True)  # NULL = platform-wide
    title: Mapped[str] = mapped_column(String(190))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20), default=AnnouncementCategory.NOTICE)
    attachment: Mapped[str | None] = mapped_column(String(255))
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_type: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[int] = mapped_column(Integer)
