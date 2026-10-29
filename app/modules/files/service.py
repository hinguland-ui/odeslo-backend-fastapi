"""Who may read which file, how much space each society uses, and registering files that existed before the registry."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.enums import UserType
from app.core.files import MIME_BY_EXT, encrypt, resolve_upload, upload_root
from app.modules.files.models import StoredFile

log = logging.getLogger("odeslo.files")


def can_read(db: Session, user: CurrentUser, f: StoredFile) -> bool:
    """One rule for every file. A stolen login only ever exposes what that login itself could already open."""
    from app.modules.residents.models import Resident
    from app.modules.service_jobs.models import ServiceJob
    from app.modules.service_jobs.service import is_assignee

    if user.is_super_admin:
        return True
    if f.uploader_type == user.user_type and f.uploader_id == user.id:
        return True  # what you uploaded yourself
    aud = f.audience
    if aud == "platform_admin":
        return False

    society_id = f.society_id
    job = None
    if aud == "job":
        job = db.get(ServiceJob, f.job_id) if f.job_id else None
        if not job:
            return False
        society_id = job.society_id

    if user.is_society_admin:
        return society_id is not None and society_id == user.society_id

    if aud == "admin":
        return False
    if aud == "owner_admin":
        # the person the document belongs to (also from their other homes: same mobile)
        if user.is_resident and f.uploader_type == UserType.RESIDENT and f.uploader_id:
            owner = db.get(Resident, f.uploader_id)
            return bool(owner and owner.mobile == user.mobile)
        return False
    if aud == "unit":
        if user.is_resident:
            return f.unit_id is not None and f.unit_id == user.unit_id
        if user.user_type == UserType.STAFF:  # the staff member the complaint / request is assigned to
            return _assigned_to_staff(db, f.path, user.id)
        return False
    if aud == "society":
        if f.society_id is None:
            return True  # platform-wide (e.g. a platform deal image): every signed-in user
        if user.user_type == UserType.PARTNER:
            from app.modules.service_partners.models import PartnerSociety

            return db.get(PartnerSociety, (user.id, f.society_id)) is not None
        return user.society_id == f.society_id
    if aud == "job":
        if user.is_resident:
            return job.unit_id == user.unit_id
        return is_assignee(user, job)
    return False


def _assigned_to_staff(db: Session, path: str, staff_id: int) -> bool:
    from app.modules.complaints.models import Complaint
    from app.modules.requests.models import ResidentRequest

    if db.scalar(select(Complaint.id).where(Complaint.photo == path, Complaint.assigned_to == staff_id)):
        return True
    return bool(db.scalar(select(ResidentRequest.id).where(ResidentRequest.attachment == path,
                                                           ResidentRequest.assigned_to == staff_id)))


# ------------------------------------------------------------------ storage usage (for the super admin)
def usage(db: Session) -> dict:
    from app.modules.societies.models import Society

    rows = db.execute(select(StoredFile.society_id, StoredFile.category, func.count(), func.coalesce(func.sum(StoredFile.size_bytes), 0))
                      .group_by(StoredFile.society_id, StoredFile.category)).all()
    by_society: dict[int | None, dict] = {}
    for sid, category, count, size in rows:
        entry = by_society.setdefault(sid, {"files": 0, "bytes": 0, "by_category": {}})
        entry["files"] += count
        entry["bytes"] += int(size)
        entry["by_category"][category] = {"files": count, "bytes": int(size)}

    societies = []
    for s in db.scalars(select(Society).order_by(Society.name)).all():
        e = by_society.pop(s.id, {"files": 0, "bytes": 0, "by_category": {}})
        societies.append({"society_id": s.id, "name": s.name, "code": s.code, "status": s.status, **e})
    societies.sort(key=lambda x: x["bytes"], reverse=True)
    platform = by_society.pop(None, {"files": 0, "bytes": 0, "by_category": {}})
    total_bytes = sum(x["bytes"] for x in societies) + platform["bytes"]
    total_files = sum(x["files"] for x in societies) + platform["files"]
    return {"total_bytes": total_bytes, "total_files": total_files, "societies": societies, "platform": platform}


# ------------------------------------------------------------------ files that existed before the registry
def backfill(db: Session) -> int:
    """Register (and encrypt) every file on disk that has no registry row yet. Safe to run on every start."""
    from app.modules.announcements.models import Announcement
    from app.modules.complaints.models import Complaint
    from app.modules.group_deals.models import GroupDeal
    from app.modules.masjids.models import Masjid
    from app.modules.requests.models import ResidentRequest
    from app.modules.residents.models import Resident, ResidentDocument
    from app.modules.service_jobs.models import ServiceJob, ServiceJobUpdate
    from app.modules.service_partners.models import ServicePartner
    from app.modules.societies.models import Society
    from app.modules.society_admins.models import SocietyAdmin
    from app.modules.staff.models import Staff

    root = upload_root()
    known = set(db.scalars(select(StoredFile.path)).all())
    on_disk = [p for p in root.rglob("*") if p.is_file()]
    todo = {p.relative_to(root).as_posix(): p for p in on_disk if p.relative_to(root).as_posix() not in known}
    if not todo:
        return 0

    meta: dict[str, dict] = {}

    def add(path, **kw):
        if path and path in todo:
            meta.setdefault(path, kw)

    for r in db.scalars(select(Resident).where(Resident.photo.is_not(None))):
        add(r.photo, category="residents_photos", audience="owner_admin", society_id=r.society_id, unit_id=r.unit_id, uploader_type="resident", uploader_id=r.id)
    for d, r in db.execute(select(ResidentDocument, Resident).join(Resident, Resident.id == ResidentDocument.resident_id)):
        add(d.file_path, category="residents_documents", audience="owner_admin", society_id=r.society_id, unit_id=r.unit_id, uploader_type="resident", uploader_id=r.id)
    for c in db.scalars(select(Complaint).where(Complaint.photo.is_not(None))):
        add(c.photo, category="complaints", audience="unit", society_id=c.society_id, unit_id=c.unit_id, uploader_type="resident", uploader_id=c.resident_id)
    for q in db.scalars(select(ResidentRequest).where(ResidentRequest.attachment.is_not(None))):
        add(q.attachment, category="requests", audience="unit", society_id=q.society_id, unit_id=q.unit_id, uploader_type="resident", uploader_id=q.resident_id)
    for d in db.scalars(select(GroupDeal).where(GroupDeal.image.is_not(None))):
        add(d.image, category="deals", audience="society", society_id=d.society_id)
    for a in db.scalars(select(Announcement).where(Announcement.attachment.is_not(None))):
        add(a.attachment, category="announcements", audience="society", society_id=a.society_id)
    from app.modules.worship_places.models import WorshipPlace

    for w in db.scalars(select(WorshipPlace)):
        for p in w.photos or []:
            add(p, category="worship_places", audience="society", society_id=w.society_id)
    for m in db.scalars(select(Masjid).where(Masjid.photo.is_not(None))):
        add(m.photo, category="masjids", audience="society", society_id=m.society_id)
    for s in db.scalars(select(Society).where(Society.logo.is_not(None))):
        add(s.logo, category="societies", audience="society", society_id=s.id)
    for a in db.scalars(select(SocietyAdmin)):
        for col in (a.id_proof, a.authorisation_letter):
            add(col, category="society_admins", audience="admin", society_id=a.society_id, uploader_type="society_admin", uploader_id=a.id)
    for s in db.scalars(select(Staff)):
        for col in (s.id_proof, s.photo):
            add(col, category="staff", audience="admin", society_id=s.society_id)
    for p in db.scalars(select(ServicePartner)):
        for col in (p.id_proof, p.photo):
            add(col, category="partners", audience="platform_admin", society_id=None)
    for j in db.scalars(select(ServiceJob).where(ServiceJob.photo.is_not(None))):
        add(j.photo, category="jobs", audience="job", society_id=j.society_id, job_id=j.id)
    for u, j in db.execute(select(ServiceJobUpdate, ServiceJob).join(ServiceJob, ServiceJob.id == ServiceJobUpdate.job_id).where(ServiceJobUpdate.photo.is_not(None))):
        add(u.photo, category="jobs", audience="job", society_id=j.society_id, job_id=j.id)

    for rel, path in todo.items():
        raw = path.read_bytes()
        if not raw.startswith(b"ODFE1"):
            path.write_bytes(encrypt(raw))  # protect files saved before encryption existed
        ext = path.suffix.lower()
        m = meta.get(rel, {"category": "unlinked", "audience": "platform_admin", "society_id": None})
        db.add(StoredFile(id=path.stem[:32], path=rel, mime=MIME_BY_EXT.get(ext, "application/octet-stream"),
                          size_bytes=len(raw[5:]) if raw.startswith(b"ODFE1") else len(raw), encrypted=True, **m))
    db.commit()
    log.info("Registered %d existing upload(s) in the file registry", len(todo))
    return len(todo)
