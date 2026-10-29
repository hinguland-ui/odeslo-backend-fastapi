from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestStatusUpdate(BaseModel):
    status: Literal["in_progress", "approved", "rejected", "closed"]
    admin_note: str | None = Field(None, max_length=1000)


class RequestAssign(BaseModel):
    staff_id: int


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_no: str
    society_id: int
    resident_id: int
    unit_id: int
    type: str
    details: str
    attachment: str | None
    assigned_to: int | None
    status: str
    admin_note: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
