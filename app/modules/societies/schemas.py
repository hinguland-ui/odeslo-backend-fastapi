from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.enums import BillingCycle, Designation, SocietyStatus, SocietyType, UnitType
from app.core.types import Mobile


class MaintenanceSettings(BaseModel):
    amounts: dict[UnitType, float] = Field(default_factory=lambda: {UnitType.FLAT: 0})
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    due_day: int = Field(10, ge=1, le=28)
    late_fee_enabled: bool = False
    late_fee: float = Field(0, ge=0)


class Features(BaseModel):
    group_deals: bool = True
    masjid: bool = True
    complaints: bool = True
    requests: bool = True
    maintenance: bool = True
    staff: bool = True
    announcements: bool = True


class UnitIn(BaseModel):
    unit_no: str = Field(min_length=1, max_length=20)
    unit_type: UnitType = UnitType.FLAT
    floor: int | None = None


class WingIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    units: list[UnitIn] = Field(default_factory=list)


class PrimaryAdminIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    mobile: Mobile
    email: EmailStr
    designation: Designation = Designation.SECRETARY


class SocietyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=190)
    address: str
    area: str
    city: str
    state: str
    pincode: str = Field(pattern=r"^\d{6}$")
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    type: SocietyType
    registration_number: str | None = None
    require_documents: bool = False
    wings: list[WingIn] = Field(min_length=1)
    primary_admin: PrimaryAdminIn
    maintenance_settings: MaintenanceSettings
    features: Features = Field(default_factory=Features)


class SocietyUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=190)
    address: str | None = None
    area: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = Field(None, pattern=r"^\d{6}$")
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    type: SocietyType | None = None
    registration_number: str | None = None
    require_documents: bool | None = None


class SocietyDelete(BaseModel):
    """Delete a society for good. `confirm_code` must be the society's own code."""

    confirm_code: str = Field(min_length=1, max_length=20)
    delete_media: bool = True  # photos and documents too (false: kept for the super admin only)


class SocietyStatusUpdate(BaseModel):
    status: SocietyStatus
    reason: str | None = None


class SocietyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    address: str
    area: str
    city: str
    state: str
    pincode: str
    lat: float
    lng: float
    type: str
    registration_number: str | None
    logo: str | None
    maintenance_settings: dict
    features: dict
    require_documents: bool
    status: str
    created_at: datetime


class SocietyStats(BaseModel):
    wings: int
    units: int
    occupied_units: int
    active_residents: int
    pending_residents: int


class SocietyDetailOut(SocietyOut):
    stats: SocietyStats


class SocietyPublicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    area: str
    city: str
    logo: str | None
    lat: float
    lng: float
    distance_km: float | None = None  # set when searching by location


class WingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class WingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    name: str


class UnitCreate(UnitIn):
    wing_id: int


class UnitUpdate(BaseModel):
    unit_no: str | None = Field(None, min_length=1, max_length=20)
    unit_type: UnitType | None = None
    floor: int | None = None


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    wing_id: int
    unit_no: str
    unit_type: str
    floor: int | None
    occupancy_status: str


class UnitBulkResult(BaseModel):
    created: int
    skipped: int
    errors: list[str]


class JoinInfoOut(BaseModel):
    society_id: int
    code: str
    name: str
    join_url: str
    qr_payload: str
