from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.timeutils import now_ist


class ActivityLog(Base):
    """Audit trail: every data-changing action records who, what and when."""

    __tablename__ = "activity_logs"
    __table_args__ = (Index("ix_activity_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int | None] = mapped_column(Integer, index=True)
    user_type: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
