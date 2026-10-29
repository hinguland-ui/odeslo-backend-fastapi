from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.core.enums import DocumentType, ResidentType
from app.core.types import Mobile


class ResidentRegister(BaseModel):
    society_id: int | None = None
    society_code: str | None = None
    wing_id: int
    unit_id: int
    resident_type: ResidentType
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr | None = None
    emergency_contact_name: str | None = None
    emergency_contact_mobile: Mobile | None = None

    @model_validator(mode="after")
    def society_ref(self):
        if not self.society_id and not self.society_code:
            raise ValueError("society_id or society_code is required")
        return self


class ResidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    wing_id: int
    unit_id: int
    name: str
    mobile: str
    email: str | None
    resident_type: str
    is_primary: bool
    parent_resident_id: int | None
    photo: str | None
    status: str
    unit_occupied_flag: bool
    rejection_reason: str | None
    approved_at: datetime | None
    emergency_contact_name: str | None
    emergency_contact_mobile: str | None
    maintenance_visible: bool
    created_at: datetime
    wing_name: str | None = None
    unit_no: str | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_type: str
    file_path: str
    status: str
    created_at: datetime


class FamilyMemberCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    relation: str = Field(min_length=2, max_length=50)
    mobile: Mobile | None = None
    grant_app_access: bool = False  # creates a Family Member login linked to the same unit

    @model_validator(mode="after")
    def mobile_for_access(self):
        if self.grant_app_access and not self.mobile:
            raise ValueError("mobile is required to give app access")
        return self


class FamilyMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resident_id: int
    name: str
    relation: str
    mobile: str | None
    linked_resident_id: int | None


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    mobile: Mobile
    email: EmailStr | None = None
    maintenance_visible: bool = False


class TenantVisibilityUpdate(BaseModel):
    maintenance_visible: bool


class VehicleCreate(BaseModel):
    vehicle_type: Literal["car", "bike", "scooter", "other"]
    vehicle_number: str = Field(min_length=4, max_length=20)
    parking_slot: str | None = None


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resident_id: int
    vehicle_type: str
    vehicle_number: str
    parking_slot: str | None


class ResidentDetailOut(ResidentOut):
    documents: list[DocumentOut]
    family_members: list[FamilyMemberOut]
    vehicles: list[VehicleOut]
    occupied_by: ResidentOut | None = None  # existing active primary when unit_occupied_flag is set


class ResidentProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    email: EmailStr | None = None
    emergency_contact_name: str | None = None
    emergency_contact_mobile: Mobile | None = None


class ResidentSettings(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_prefs: dict[str, bool] = Field(default_factory=dict)
    namaz_alerts_enabled: bool = False
    namaz_alert_minutes: int = Field(10, ge=0, le=60)
    preferred_masjid_id: int | None = None
    location_permission: bool = False
    notification_permission: bool = False


class ResidentSettingsUpdate(BaseModel):
    notification_prefs: dict[str, bool] | None = None
    namaz_alerts_enabled: bool | None = None
    namaz_alert_minutes: int | None = Field(None, ge=0, le=60)
    preferred_masjid_id: int | None = None
    location_permission: bool | None = None
    notification_permission: bool | None = None


class LocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DocumentUploadType(BaseModel):
    doc_type: DocumentType


class ResidentAdminUpdate(BaseModel):
    """Society admin edits a registration before approving it."""

    name: str | None = Field(None, min_length=2, max_length=120)
    email: EmailStr | None = None
    resident_type: ResidentType | None = None
    wing_id: int | None = None
    unit_id: int | None = None
    emergency_contact_name: str | None = None
    emergency_contact_mobile: Mobile | None = None


class ResidentAddHome(BaseModel):
    """One more home for a signed-in resident. Name, email and emergency contact default to their profile."""

    society_id: int | None = None
    society_code: str | None = None
    wing_id: int
    unit_id: int
    resident_type: ResidentType
    name: str | None = Field(None, min_length=2, max_length=120)
    email: EmailStr | None = None
    emergency_contact_name: str | None = None
    emergency_contact_mobile: Mobile | None = None

    @model_validator(mode="after")
    def society_ref(self):
        if not self.society_id and not self.society_code:
            raise ValueError("society_id or society_code is required")
        return self


class HomeOut(BaseModel):
    id: int
    society_id: int
    society_name: str
    society_code: str
    society_area: str
    wing_name: str | None
    unit_no: str | None
    unit_label: str
    resident_type: str
    status: str
    rejection_reason: str | None
    current: bool = False
