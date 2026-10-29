from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class BillGenerate(BaseModel):
    period_start: date | None = None  # default: current billing period
    include_vacant: bool = False
    notify_residents: bool = True


class BillGenerateResult(BaseModel):
    period: str
    created: int
    skipped_existing: int
    skipped_no_amount: int
    total_amount: float


class BillUpdate(BaseModel):
    amount: float | None = Field(None, ge=0)
    late_fee: float | None = Field(None, ge=0)
    due_date: date | None = None


class ReminderRequest(BaseModel):
    bill_ids: list[int] | None = None  # None = every unpaid bill of the society
    sms: bool = True


class BillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bill_no: str
    society_id: int
    unit_id: int
    resident_id: int | None
    period: str
    period_start: date
    period_end: date
    amount: float
    late_fee: float
    paid_amount: float
    total_amount: float
    outstanding: float
    due_date: date
    status: str
    reminder_count: int
    last_reminder_at: datetime | None
    paid_at: datetime | None
    created_at: datetime
    unit_label: str | None = None


class MaintenanceSummary(BaseModel):
    period: str | None
    billed: float
    collected: float
    pending: float
    bills: int
    paid_bills: int
    overdue_bills: int


class MyMaintenanceOut(BaseModel):
    current_due: float
    next_due_date: date | None
    unpaid_bills: list[BillOut]
    history: list[BillOut]


class DefaulterOut(BaseModel):
    unit_id: int
    unit_label: str
    resident_name: str | None
    resident_mobile: str | None
    unpaid_bills: int
    outstanding: float
    oldest_due_date: date
