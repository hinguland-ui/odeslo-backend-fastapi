from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Kind = Literal["mandir", "masjid", "church", "gurudwara", "jain_temple", "buddhist_vihar", "other"]


class Timing(BaseModel):
    label: str = Field(min_length=1, max_length=60)  # "Morning aarti", "Fajr", "Sunday mass", "Langar"
    # picked on a clock, stored the way people read it: "6:30 AM", "1:15 PM" (anything else goes in the note)
    time: str = Field(pattern=r"^(0?[1-9]|1[0-2]):[0-5]\d (AM|PM)$")
    note: str | None = Field(None, max_length=120)

    @field_validator("label", "time", "note", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class PlaceBase(BaseModel):
    kind: Kind
    name: str = Field(min_length=2, max_length=190)
    dedicated_to: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=2000)
    address: str | None = Field(None, max_length=500)
    contact: str | None = Field(None, max_length=20)
    timings: list[Timing] = Field(default_factory=list, max_length=20)


class PlaceCreate(PlaceBase):
    pass


class PlaceUpdate(BaseModel):
    kind: Kind | None = None
    name: str | None = Field(None, min_length=2, max_length=190)
    dedicated_to: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=2000)
    address: str | None = Field(None, max_length=500)
    contact: str | None = Field(None, max_length=20)
    timings: list[Timing] | None = Field(None, max_length=20)
    is_active: bool | None = None


class PlaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    kind: str
    name: str
    dedicated_to: str | None
    description: str | None
    address: str | None
    contact: str | None
    photos: list[str]
    timings: list[Timing]
    is_active: bool
    created_at: datetime
