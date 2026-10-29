from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.types import TimeHHMM


class MasjidCreate(BaseModel):
    name: str = Field(min_length=3, max_length=190)
    address: str = Field(min_length=5)
    area: str | None = None
    city: str | None = None
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    contact: str | None = Field(None, max_length=20)
    imam_name: str | None = None
    has_ladies_section: bool = False
    has_parking: bool = False


class MasjidUpdate(BaseModel):
    name: str | None = Field(None, min_length=3, max_length=190)
    address: str | None = None
    area: str | None = None
    city: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    contact: str | None = None
    imam_name: str | None = None
    has_ladies_section: bool | None = None
    has_parking: bool | None = None


class TimingCreate(BaseModel):
    fajr: TimeHHMM
    dhuhr: TimeHHMM
    asr: TimeHHMM
    maghrib: TimeHHMM
    isha: TimeHHMM
    jummah: TimeHHMM | None = None
    effective_from: date


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class MasjidOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    area: str | None
    city: str | None
    lat: float
    lng: float
    contact: str | None
    imam_name: str | None
    photo: str | None
    has_ladies_section: bool
    has_parking: bool
    society_id: int | None
    added_by_type: str
    is_suggestion: bool
    verified: bool
    status: str
    rejection_reason: str | None
    verified_at: datetime | None
    created_at: datetime


class TimingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    masjid_id: int
    fajr: str
    dhuhr: str
    asr: str
    maghrib: str
    isha: str
    jummah: str | None
    effective_from: date
    verified: bool
    status: str
    rejection_reason: str | None
    submitted_by_type: str
    created_at: datetime


class NextPrayer(BaseModel):
    name: str
    time: str
    seconds_until: int
    is_tomorrow: bool = False


class TimetableOut(BaseModel):
    masjid: MasjidOut | None
    date: date
    source: str  # "masjid" = verified jamaat timings, "calculated" = astronomical fallback
    timings: dict[str, str | None]
    sunrise: str | None
    current_prayer: str | None
    next_prayer: NextPrayer | None
    calculation_method: str | None = None


class NearbyMasjidOut(MasjidOut):
    distance_km: float
    timings_source: str
    next_prayer: NextPrayer | None


class PendingVerificationOut(BaseModel):
    masjids: list[MasjidOut]
    timings: list[TimingOut]
