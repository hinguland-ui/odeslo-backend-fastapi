from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.timeutils import now_ist


class OnboardingDraft(Base):
    """Where a new resident / vendor stopped in the sign-up wizard (typed details only, never files).

    Read and written only with the registration token that proves ownership of the mobile (after the OTP).
    """

    __tablename__ = "onboarding_drafts"
    __table_args__ = (Index("uq_onboarding_draft", "mobile", "role", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mobile: Mapped[str] = mapped_column(String(15))
    role: Mapped[str] = mapped_column(String(20))  # resident | partner
    step: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, onupdate=now_ist)
