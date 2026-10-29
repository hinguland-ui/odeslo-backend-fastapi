from typing import Any

from pydantic import BaseModel, EmailStr

from app.core.types import Mobile


class SettingOut(BaseModel):
    key: str
    value: dict[str, Any]


class SettingUpdate(BaseModel):
    value: dict[str, Any]


class AppConfigOut(BaseModel):
    app_version: dict[str, Any]
    prayer_calculation: dict[str, Any]
    masjid: dict[str, Any]
    payment_gateway: str
    payment_key_id: str
    payment_mode: str = "test"


class EmailTest(BaseModel):
    to: EmailStr


class SmsTest(BaseModel):
    mobile: Mobile
