from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.enums import AssigneeType


class JobAssign(BaseModel):
    assigned_to_type: AssigneeType
    assigned_to_id: int
    scheduled_at: datetime | None = None
    slot: str | None = None


class JobsFromDeal(BaseModel):
    assigned_to_type: AssigneeType | None = None
    assigned_to_id: int | None = None
    scheduled_at: datetime | None = None


class JobCreate(BaseModel):
    booking_id: int
    assign: JobAssign | None = None
    notes: str | None = None


class JobIssue(BaseModel):
    note: str = Field(min_length=3, max_length=1000)
    reschedule_to: datetime | None = None


class JobCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class JobPayout(BaseModel):
    reference: str = Field(min_length=3, max_length=100)
    amount: float | None = Field(None, ge=0)


class JobUpdateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    note: str | None
    photo: str | None
    updated_by_type: str
    created_at: datetime


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_no: str
    booking_id: int | None
    deal_id: int | None
    society_id: int | None
    society_name: str | None = None
    resident_id: int | None
    unit_id: int | None
    title: str
    assigned_to_type: str | None
    assigned_to_id: int | None
    status: str
    scheduled_at: datetime | None
    slot: str | None
    completed_at: datetime | None
    notes: str | None
    photo: str | None
    issue_note: str | None
    escalated: bool
    rating: int | None
    feedback: str | None
    created_at: datetime


class JobAdminOut(JobOut):
    payout_amount: float | None
    payout_status: str
    payout_reference: str | None


class JobDetailOut(JobAdminOut):
    timeline: list[JobUpdateOut]
    resident_name: str | None = None
    resident_mobile: str | None = None  # masked for assignees until the job is accepted
    unit_label: str | None = None
    assignee_name: str | None = None


AssigneeStatus = Literal["accepted", "on_the_way", "started", "completed"]


class MessageIn(BaseModel):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    sender_type: str
    sender_id: int
    sender_name: str
    body: str
    created_at: datetime


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(None, ge=0)


class LocationOut(BaseModel):
    sharing: bool  # vendor is on the way / working and the position is fresh
    lat: float | None = None
    lng: float | None = None
    accuracy: float | None = None
    updated_at: datetime | None = None
    job_status: str
    destination: dict | None = None  # the society: {"lat", "lng", "label"}
    distance_km: float | None = None
    eta_minutes: int | None = None
