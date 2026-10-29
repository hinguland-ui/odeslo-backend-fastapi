from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field



class BankDetails(BaseModel):
    account_name: str | None = None
    account_no: str | None = None
    ifsc: str | None = Field(None, pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")
    upi_id: str | None = None


class PartnerUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    email: str | None = None
    business_name: str | None = None
    gst: str | None = Field(None, max_length=20)
    categories: list[str] | None = None


class PartnerSelfUpdate(BaseModel):
    email: str | None = None
    business_name: str | None = None
    bank_details: BankDetails | None = None


class PartnerSocietiesUpdate(BaseModel):
    society_ids: list[int]


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    mobile: str
    email: str | None
    business_name: str | None
    gst: str | None
    categories: list[str]
    id_proof: str
    photo: str
    verified: bool
    verified_at: datetime | None
    rating_avg: float
    rating_count: int
    status: str
    created_at: datetime
    society_ids: list[int] = []


class PartnerPrivateOut(PartnerOut):
    bank_details: dict | None


class PartnerBriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    business_name: str | None
    categories: list[str]
    rating_avg: float
    rating_count: int


class EarningsOut(BaseModel):
    completed_jobs: int
    total_earned: float
    paid: float
    pending: float


class PartnerSocietyOut(BaseModel):
    """A society the vendor is assigned to, with how crowded it is (counts only, no other vendor details)."""
    id: int
    name: str
    area: str
    city: str
    assigned_at: datetime | None = None
    vendors_total: int  # verified active vendors assigned to this society (including this vendor)
    vendors_same_category: int  # other vendors who share at least one category with this vendor
    vendors_by_category: dict[str, int]
    open_deals: int  # unawarded deals in this society matching this vendor's categories
    my_jobs: int
