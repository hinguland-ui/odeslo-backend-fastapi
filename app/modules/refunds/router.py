from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AdminPermission, UserType
from app.core.errors import not_found
from app.core.pagination import Page, PageParams, paginate
from app.modules.payments.models import Payment
from app.modules.refunds import service
from app.modules.refunds.models import Refund
from app.modules.refunds.schemas import RefundCreate, RefundOut, RefundProcess, RefundReject

router = APIRouter(prefix="/refunds", tags=["Refunds"])

RefundAdmins = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.REFUNDS)
ActiveResident = require_roles(UserType.RESIDENT)


def _get(db: Session, user: CurrentUser, refund_id: int) -> Refund:
    refund = db.get(Refund, refund_id)
    if not refund:
        raise not_found("Refund")
    ensure_society_access(user, refund.society_id, "Refund")
    return refund


@router.get("/my", response_model=list[RefundOut])
def my_refunds(user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    return db.scalars(select(Refund).join(Payment, Payment.id == Refund.payment_id)
                      .where(Payment.unit_id == user.unit_id).order_by(Refund.id.desc())).all()


@router.post("", response_model=RefundOut, status_code=201)
def initiate_refund(data: RefundCreate, user: CurrentUser = Depends(RefundAdmins), db: Session = Depends(get_db)):
    payment = db.get(Payment, data.payment_id)
    if not payment:
        raise not_found("Payment")
    ensure_society_access(user, payment.society_id, "Payment")
    amount = data.amount or service.refundable_amount(db, payment)
    refund = service.create_refund(db, payment, amount, data.reason, user)
    db.commit()
    return refund


@router.get("", response_model=Page[RefundOut])
def refund_queue(society_id: int | None = None, status: str | None = None, params: PageParams = Depends(),
                 user: CurrentUser = Depends(RefundAdmins), db: Session = Depends(get_db)):
    stmt = select(Refund).order_by(Refund.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(Refund.society_id == sid)
    if status:
        stmt = stmt.where(Refund.status == status)
    return paginate(db, stmt, params)


@router.get("/{refund_id}", response_model=RefundOut)
def get_refund(refund_id: int, user: CurrentUser = Depends(RefundAdmins), db: Session = Depends(get_db)):
    return _get(db, user, refund_id)


@router.post("/{refund_id}/approve", response_model=RefundOut)
def approve_refund(refund_id: int, user: CurrentUser = Depends(RefundAdmins), db: Session = Depends(get_db)):
    refund = service.approve(db, _get(db, user, refund_id), user)
    db.commit()
    return refund


@router.post("/{refund_id}/reject", response_model=RefundOut)
def reject_refund(refund_id: int, data: RefundReject, user: CurrentUser = Depends(RefundAdmins),
                  db: Session = Depends(get_db)):
    refund = service.reject(db, _get(db, user, refund_id), user, data.reason)
    db.commit()
    return refund


@router.post("/{refund_id}/process", response_model=RefundOut)
def process_refund(refund_id: int, data: RefundProcess, user: CurrentUser = Depends(RefundAdmins),
                   db: Session = Depends(get_db)):
    """Gateway refund (online payments) or manual refund with a reference number."""
    refund = service.process(db, _get(db, user, refund_id), user, data.mode, data.reference_no)
    db.commit()
    return refund


@router.post("/{refund_id}/complete", response_model=RefundOut)
def complete_refund(refund_id: int, user: CurrentUser = Depends(RefundAdmins), db: Session = Depends(get_db)):
    refund = service.complete(db, _get(db, user, refund_id), user)
    db.commit()
    return refund
