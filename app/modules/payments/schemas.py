from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import PaymentMode, PaymentRefType


class PaymentInitiate(BaseModel):
    ref_type: PaymentRefType
    ref_id: int


class PaymentOrderOut(BaseModel):
    payment_id: int
    txn_id: str
    gateway: str
    gateway_order_id: str
    amount: float
    amount_paise: int
    currency: str
    key_id: str


class PaymentVerify(BaseModel):
    payment_id: int
    gateway_order_id: str
    gateway_payment_id: str
    signature: str


class PaymentFailure(BaseModel):
    payment_id: int
    reason: str = Field("Cancelled by user", max_length=500)


class OfflinePaymentCreate(BaseModel):
    ref_type: PaymentRefType
    ref_id: int
    amount: float = Field(gt=0)
    mode: PaymentMode
    reference_no: str | None = Field(None, max_length=100)
    notes: str | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    txn_id: str
    society_id: int
    resident_id: int | None
    unit_id: int | None
    ref_type: str
    ref_id: int
    amount: float
    mode: str
    gateway: str | None
    gateway_order_id: str | None
    gateway_txn_id: str | None
    reference_no: str | None
    status: str
    paid_at: datetime | None
    failure_reason: str | None
    notes: str | None
    recorded_by_type: str | None
    created_at: datetime


class ReceiptOut(BaseModel):
    receipt_no: str
    txn_id: str
    date: datetime | None
    society: str
    resident_name: str | None
    unit: str | None
    description: str
    amount: float
    mode: str
    reference_no: str | None
    gateway_txn_id: str | None
