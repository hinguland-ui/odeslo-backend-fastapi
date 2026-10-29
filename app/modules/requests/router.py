from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, ensure_society_access, require_roles, scoped_society_id
from app.core.enums import AdminPermission, Feature, NotificationCategory, RequestStatus, RequestType, UserType
from app.core.errors import bad_request, not_found
from app.core.files import DOCUMENT_TYPES, save_upload
from app.core.pagination import Page, PageParams, paginate
from app.core.utils import generate_ref, snapshot
from app.modules.activity_logs.service import log_activity
from app.modules.notifications.service import notify, notify_society_admins
from app.modules.requests import service
from app.modules.requests.models import ResidentRequest
from app.modules.requests.schemas import RequestAssign, RequestOut, RequestStatusUpdate
from app.modules.societies.service import ensure_feature_enabled
from app.modules.staff.service import get_active_staff_of_society

router = APIRouter(prefix="/requests", tags=["Resident Requests"])

ActiveResident = require_roles(UserType.RESIDENT)
RequestAdmins = require_roles(UserType.SOCIETY_ADMIN, perm=AdminPermission.REQUESTS)
RequestViewers = require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN, perm=AdminPermission.REQUESTS)


def _get(db: Session, user: CurrentUser, request_id: int) -> ResidentRequest:
    req = db.get(ResidentRequest, request_id)
    if not req:
        raise not_found("Request")
    if user.is_resident:
        if req.unit_id != user.unit_id:
            raise not_found("Request")
    elif user.user_type == UserType.STAFF:
        if req.assigned_to != user.id:
            raise not_found("Request")
    else:
        ensure_society_access(user, req.society_id, "Request")
    return req


@router.get("/types")
def request_types():
    return [{"value": t.value, "label": t.value.replace("_", " ").title()} for t in RequestType]


@router.post("", response_model=RequestOut, status_code=201)
def create_request(type: RequestType = Form(...), details: str = Form(..., min_length=5),
                   attachment: UploadFile | None = File(None), user: CurrentUser = Depends(ActiveResident),
                   db: Session = Depends(get_db)):
    ensure_feature_enabled(db, user.society_id, Feature.REQUESTS)
    req = ResidentRequest(request_no=generate_ref("RQ"), society_id=user.society_id, resident_id=user.id,
                          unit_id=user.unit_id, type=type, details=details, status=RequestStatus.OPEN,
                          attachment=save_upload(attachment, "requests", DOCUMENT_TYPES, db=db, user=user, audience="unit", society_id=user.society_id, unit_id=user.unit_id) if attachment else None)
    db.add(req)
    db.flush()
    notify_society_admins(db, user.society_id, NotificationCategory.REQUEST, "New resident request",
                          f"{req.request_no}: {type.value.replace('_', ' ')}", perm="requests", ref_type="request",
                          ref_id=req.id)
    log_activity(db, user, "request.create", "request", req.id, new=snapshot(req))
    db.commit()
    return req


@router.get("/my", response_model=Page[RequestOut])
def my_requests(status: str | None = None, params: PageParams = Depends(),
                user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    stmt = select(ResidentRequest).where(ResidentRequest.unit_id == user.unit_id).order_by(ResidentRequest.id.desc())
    if status:
        stmt = stmt.where(ResidentRequest.status == status)
    return paginate(db, stmt, params)


@router.post("/{request_id}/withdraw", response_model=RequestOut)
def withdraw_request(request_id: int, user: CurrentUser = Depends(ActiveResident), db: Session = Depends(get_db)):
    req = _get(db, user, request_id)
    if req.status != RequestStatus.OPEN:
        raise bad_request("Only open requests can be withdrawn")
    req.status = RequestStatus.CLOSED
    req.admin_note = "Withdrawn by resident"
    log_activity(db, user, "request.withdraw", "request", req.id, {"status": RequestStatus.OPEN},
                 {"status": req.status})
    db.commit()
    return req


@router.get("/assigned", response_model=list[RequestOut])
def assigned_to_me(user: CurrentUser = Depends(require_roles(UserType.STAFF)), db: Session = Depends(get_db)):
    return db.scalars(select(ResidentRequest).where(ResidentRequest.assigned_to == user.id,
                                                    ResidentRequest.status.not_in(service.FINAL))
                      .order_by(ResidentRequest.id.desc())).all()


@router.get("", response_model=Page[RequestOut])
def list_requests(society_id: int | None = None, status: str | None = None, type: str | None = None,
                  params: PageParams = Depends(), user: CurrentUser = Depends(RequestViewers),
                  db: Session = Depends(get_db)):
    stmt = select(ResidentRequest).order_by(ResidentRequest.id.desc())
    sid = scoped_society_id(user, society_id)
    if sid is not None:
        stmt = stmt.where(ResidentRequest.society_id == sid)
    if status:
        stmt = stmt.where(ResidentRequest.status == status)
    if type:
        stmt = stmt.where(ResidentRequest.type == type)
    return paginate(db, stmt, params)


@router.get("/{request_id}", response_model=RequestOut)
def get_request(request_id: int,
                user: CurrentUser = Depends(require_roles(UserType.SUPER_ADMIN, UserType.SOCIETY_ADMIN,
                                                          UserType.RESIDENT, UserType.STAFF,
                                                          perm=AdminPermission.REQUESTS)),
                db: Session = Depends(get_db)):
    return _get(db, user, request_id)


@router.post("/{request_id}/assign", response_model=RequestOut)
def assign_request(request_id: int, data: RequestAssign, user: CurrentUser = Depends(RequestAdmins),
                   db: Session = Depends(get_db)):
    req = _get(db, user, request_id)
    staff = get_active_staff_of_society(db, data.staff_id, req.society_id)
    req.assigned_to = staff.id
    if req.status == RequestStatus.OPEN:
        req.status = RequestStatus.IN_PROGRESS
    notify(db, UserType.STAFF, staff.id, NotificationCategory.REQUEST, "New request assigned",
           f"{req.request_no}: {req.type}", ref_type="request", ref_id=req.id)
    log_activity(db, user, "request.assign", "request", req.id, new={"assigned_to": staff.id})
    db.commit()
    return req


@router.post("/{request_id}/status", response_model=RequestOut)
def update_request_status(request_id: int, data: RequestStatusUpdate,
                          user: CurrentUser = Depends(require_roles(UserType.SOCIETY_ADMIN, UserType.STAFF,
                                                                    perm=AdminPermission.REQUESTS)),
                          db: Session = Depends(get_db)):
    req = _get(db, user, request_id)
    service.update_status(db, req, user, data.status, data.admin_note)
    db.commit()
    return req
