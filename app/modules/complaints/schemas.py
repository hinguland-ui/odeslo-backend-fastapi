from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ComplaintAssign(BaseModel):
    staff_id: int
    note: str | None = None


class ComplaintStatusUpdate(BaseModel):
    status: Literal["in_progress", "resolved"]
    note: str | None = Field(None, max_length=1000)


class ComplaintReopen(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CommentCreate(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_type: str
    author_name: str
    comment: str
    status_change: str | None
    created_at: datetime


class ComplaintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    complaint_no: str
    society_id: int
    resident_id: int
    unit_id: int
    category: str
    description: str
    photo: str | None
    priority: str
    assigned_to: int | None
    status: str
    resolved_at: datetime | None
    closed_at: datetime | None
    reopened_count: int
    created_at: datetime
    updated_at: datetime


class ComplaintDetailOut(ComplaintOut):
    comments: list[CommentOut]
    resident_name: str | None = None
    unit_label: str | None = None
    assigned_staff_name: str | None = None
