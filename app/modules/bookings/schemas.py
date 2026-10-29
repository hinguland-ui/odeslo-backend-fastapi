from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class BookingCreate(BaseModel):
    deal_id: int
    units_count: int = Field(1, ge=1, le=100)
    slot: str | None = None


class BookingOnBehalfCreate(BookingCreate):
    resident_id: int


class BookingCancel(BaseModel):
    reason: str | None = Field(None, max_length=500)


class BookingSlotChange(BaseModel):
    slot: str = Field(min_length=1, max_length=50)


class BookingRate(BaseModel):
    rating: int = Field(ge=1, le=5)
    feedback: str | None = Field(None, max_length=1000)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_no: str
    deal_id: int
    society_id: int
    resident_id: int
    unit_id: int
    units_count: int
    slot: str | None
    amount: float
    status: str
    payment_status: str
    waitlist_position: int | None
    hold_expires_at: datetime | None
    confirmed_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    booked_by_type: str
    created_at: datetime
    deal_title: str | None = None
    service_date: date | None = None
    deal_category: str | None = None


class BookingDetailOut(BookingOut):
    deal: dict
    payments: list[dict]
    job: dict | None
    refunds: list[dict]
    can_cancel: bool
    can_pay: bool
    can_rate: bool


class InvoiceOut(BaseModel):
    invoice_no: str
    booking_no: str
    date: datetime
    society: str
    resident_name: str
    unit: str
    service: str
    service_date: date
    units_count: int
    price_per_unit: float
    amount: float
    payment_status: str
    payments: list[dict]
