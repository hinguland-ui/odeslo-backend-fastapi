from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator



class DealCreate(BaseModel):
    society_id: int | None = None  # super admin: None = platform-level deal; society admins: own society
    title: str = Field(min_length=3, max_length=190)
    category: str = Field(min_length=2, max_length=50)
    subcategory: str | None = Field(None, max_length=50)
    description: str | None = None
    service_date: date
    slots: list[str] = Field(default_factory=list)
    price_per_unit: float = Field(ge=0)
    max_units: int = Field(ge=1, le=10000)
    cutoff_date: datetime
    cancel_policy: str | None = None
    pay_later_allowed: bool = False
    auto_promote_waitlist: bool = True
    partner_id: int | None = None
    partner_payout_per_unit: float | None = Field(None, ge=0)

    @model_validator(mode="after")
    def check_dates(self):
        if self.cutoff_date.date() > self.service_date:
            raise ValueError("Booking cut-off must be on or before the service date")
        return self


class DealUpdate(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=190)
    category: str | None = Field(None, min_length=2, max_length=50)
    subcategory: str | None = Field(None, max_length=50)
    description: str | None = None
    service_date: date | None = None
    slots: list[str] | None = None
    price_per_unit: float | None = Field(None, ge=0)
    max_units: int | None = Field(None, ge=1, le=10000)
    cutoff_date: datetime | None = None
    cancel_policy: str | None = None
    pay_later_allowed: bool | None = None
    auto_promote_waitlist: bool | None = None
    partner_id: int | None = None
    partner_payout_per_unit: float | None = Field(None, ge=0)


class DealCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int | None
    title: str
    category: str
    subcategory: str | None = None
    description: str | None
    image: str | None
    service_date: date
    slots: list[str]
    price_per_unit: float
    max_units: int
    confirmed_units: int
    cutoff_date: datetime
    cancel_policy: str | None
    pay_later_allowed: bool
    auto_promote_waitlist: bool
    partner_id: int | None
    status: str
    cancel_reason: str | None
    published_at: datetime | None
    created_at: datetime
    available_units: int = 0
    waitlist_count: int = 0
    is_booking_open: bool = False


class DealAdminOut(DealOut):
    partner_payout_per_unit: float | None
    created_by_type: str
    created_by_id: int


class ApplicationIn(BaseModel):
    quote_per_unit: float | None = Field(None, ge=0)
    message: str | None = Field(None, max_length=1000)


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    deal_id: int
    partner_id: int
    quote_per_unit: float | None
    message: str | None
    status: str
    created_at: datetime


class ApplicationAdminOut(ApplicationOut):
    partner_name: str | None = None
    business_name: str | None = None
    rating_avg: float = 0
    rating_count: int = 0
