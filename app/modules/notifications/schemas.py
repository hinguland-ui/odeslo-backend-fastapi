from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    title: str
    body: str
    ref_type: str | None
    ref_id: int | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class UnreadCountOut(BaseModel):
    total: int
    by_category: dict[str, int]


class DeviceTokenIn(BaseModel):
    fcm_token: str = Field(min_length=10, max_length=255)
    platform: Literal["android", "ios", "web"] = "web"


class DeviceTokenDelete(BaseModel):
    fcm_token: str


class ManualNotificationIn(BaseModel):
    title: str = Field(min_length=1, max_length=190)
    body: str = Field(min_length=1)
    target: Literal["residents", "society_admins", "staff", "everyone"] = "residents"
    society_id: int | None = None  # super admin: None = every society
    resident_ids: list[int] | None = None  # optional: only these residents
    sms: bool = False


class ManualNotificationResult(BaseModel):
    recipients: int
