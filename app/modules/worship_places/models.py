from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class WorshipPlace(Base, TimestampMixin):
    """A temple, masjid, church, gurudwara... inside or next to a society, added and kept by its admin.

    Unlike `masjids` (public map, calculated prayer times, super admin verification) this is the society's own
    list: free text timings (aarti, namaz, prayer, langar...) and a few photos, shown to its residents.
    """

    __tablename__ = "worship_places"

    id: Mapped[int] = mapped_column(primary_key=True)
    society_id: Mapped[int] = mapped_column(ForeignKey("societies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(190))
    dedicated_to: Mapped[str | None] = mapped_column(String(120))  # deity / saint / denomination, e.g. "Hanuman ji"
    description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(20))
    photos: Mapped[list] = mapped_column(JSON, default=list)  # stored file paths
    timings: Mapped[list] = mapped_column(JSON, default=list)  # [{"label": "Aarti", "time": "6:30 AM", "note": "..."}]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_type: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[int] = mapped_column(Integer)
