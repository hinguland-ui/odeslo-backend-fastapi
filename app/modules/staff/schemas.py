from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import StaffType


class StaffUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    type: StaffType | None = None
    category: str | None = None


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    name: str
    mobile: str
    type: str
    category: str | None
    id_proof: str
    photo: str
    status: str
    last_login_at: datetime | None
    created_at: datetime


class StaffPublicOut(BaseModel):
    """What residents see (no ID proof)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    category: str | None
    photo: str
