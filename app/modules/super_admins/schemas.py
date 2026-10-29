from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.config import settings
from app.core.types import Mobile


class SuperAdminCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    mobile: Mobile
    password: str = Field(min_length=settings.PASSWORD_MIN_LENGTH, max_length=128)


class SuperAdminUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    mobile: Mobile | None = None


class SuperAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    mobile: str
    status: str
    last_login_at: datetime | None
    created_at: datetime
