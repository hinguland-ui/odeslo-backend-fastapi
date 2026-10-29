from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RefundMode


class RefundCreate(BaseModel):
    payment_id: int
    amount: float | None = Field(None, gt=0)  # default: full refundable amount
    reason: str = Field(min_length=3, max_length=500)


class RefundReject(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RefundProcess(BaseModel):
    mode: RefundMode
    reference_no: str | None = Field(None, max_length=100)


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    refund_no: str
    payment_id: int
    booking_id: int | None
    society_id: int
    resident_id: int | None
    amount: float
    reason: str | None
    status: str
    mode: str | None
    gateway_refund_id: str | None
    reference_no: str | None
    rejection_reason: str | None
    requested_by_type: str | None
    approved_by_type: str | None
    approved_at: datetime | None
    processed_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
