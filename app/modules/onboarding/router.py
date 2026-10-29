import json
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_registration_mobile
from app.core.errors import bad_request
from app.modules.onboarding.models import OnboardingDraft

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

Role = Literal["resident", "partner"]
MAX_BYTES = 8000


class DraftIn(BaseModel):
    role: Role
    step: int = Field(ge=1, le=10)
    data: dict[str, Any] = Field(default_factory=dict)


class DraftOut(BaseModel):
    step: int
    data: dict[str, Any]


def clear_draft(db: Session, mobile: str, role: str) -> None:
    db.execute(delete(OnboardingDraft).where(OnboardingDraft.mobile == mobile, OnboardingDraft.role == role))


@router.get("/draft", response_model=DraftOut)
def get_draft(role: Role, mobile: str = Depends(get_registration_mobile), db: Session = Depends(get_db)):
    """Where this mobile stopped last time. step 0 = nothing saved, start from the beginning."""
    row = db.scalar(select(OnboardingDraft).where(OnboardingDraft.mobile == mobile, OnboardingDraft.role == role))
    return {"step": row.step, "data": row.data} if row else {"step": 0, "data": {}}


@router.put("/draft", response_model=DraftOut)
def save_draft(body: DraftIn, mobile: str = Depends(get_registration_mobile), db: Session = Depends(get_db)):
    if len(json.dumps(body.data)) > MAX_BYTES:
        raise bad_request("Draft is too large")
    row = db.scalar(select(OnboardingDraft).where(OnboardingDraft.mobile == mobile, OnboardingDraft.role == body.role))
    if row:
        row.step, row.data = body.step, body.data
    else:
        db.add(OnboardingDraft(mobile=mobile, role=body.role, step=body.step, data=body.data))
    db.commit()
    return {"step": body.step, "data": body.data}


@router.delete("/draft", status_code=204)
def discard_draft(role: Role, mobile: str = Depends(get_registration_mobile), db: Session = Depends(get_db)):
    clear_draft(db, mobile, role)
    db.commit()
