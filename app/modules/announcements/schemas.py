from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int | None
    title: str
    body: str
    category: str
    attachment: str | None
    is_pinned: bool
    created_by_type: str
    created_by: int
    created_at: datetime
    is_platform: bool = False
