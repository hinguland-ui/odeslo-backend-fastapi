from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.enums import UserType
from app.core.errors import bad_request, not_found
from app.modules.categories import service
from app.modules.categories.models import ServiceCategoryRow
from app.modules.group_deals.models import GroupDeal
from app.modules.service_partners.models import ServicePartner

router = APIRouter(prefix="/categories", tags=["Categories"])
SuperAdmin = require_roles(UserType.SUPER_ADMIN)


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    parent_id: int | None = None


class CategoryPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=80)
    active: bool | None = None
    sort_order: int | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name: str
    parent_id: int | None
    sort_order: int
    active: bool
    deal_count: int = 0
    vendor_count: int = 0


def _out(db: Session, rows: list[ServiceCategoryRow]) -> list[CategoryOut]:
    deals_cat = dict(db.execute(select(GroupDeal.category, func.count()).group_by(GroupDeal.category)).all())
    deals_sub = dict(db.execute(select(GroupDeal.subcategory, func.count()).where(GroupDeal.subcategory.is_not(None))
                                .group_by(GroupDeal.subcategory)).all())
    vendors: dict[str, int] = {}
    for cats in db.scalars(select(ServicePartner.categories)).all():
        for c in cats or []:
            vendors[c] = vendors.get(c, 0) + 1
    out = []
    for r in rows:
        o = CategoryOut.model_validate(r)
        o.deal_count = (deals_cat if r.parent_id is None else deals_sub).get(r.key, 0)
        o.vendor_count = vendors.get(r.key, 0)
        out.append(o)
    return out


@router.get("", response_model=list[CategoryOut])
def list_categories(include_inactive: bool = False, db: Session = Depends(get_db)):
    """Public: needed by vendor onboarding and every deal form. Flat list, children point at parent_id."""
    stmt = select(ServiceCategoryRow).order_by(ServiceCategoryRow.sort_order, ServiceCategoryRow.id)
    if not include_inactive:
        stmt = stmt.where(ServiceCategoryRow.active.is_(True))
    return _out(db, list(db.scalars(stmt).all()))


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(data: CategoryIn, _: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    if data.parent_id is not None:
        parent = db.get(ServiceCategoryRow, data.parent_id)
        if not parent or parent.parent_id is not None:
            raise bad_request("Sub-categories can only be added under a main category")
    name = data.name.strip()
    clash = db.scalar(select(ServiceCategoryRow.id).where(func.lower(ServiceCategoryRow.name) == name.lower(),
                                                           ServiceCategoryRow.parent_id == data.parent_id))
    if clash:
        raise bad_request(f"'{name}' already exists here")
    last = db.scalar(select(func.max(ServiceCategoryRow.sort_order)).where(ServiceCategoryRow.parent_id == data.parent_id))
    row = ServiceCategoryRow(key=service.unique_key(db, name), name=name, parent_id=data.parent_id, sort_order=(last or 0) + 1)
    db.add(row)
    db.commit()
    return _out(db, [row])[0]


class ReorderIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)  # siblings in their new order


@router.post("/reorder", status_code=204)
def reorder_categories(data: ReorderIn, _: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    """Drag and drop: the ids (all under the same parent) get sort_order 0, 1, 2 ... in the given order."""
    rows = {r.id: r for r in db.scalars(select(ServiceCategoryRow).where(ServiceCategoryRow.id.in_(data.ids)))}
    if len(rows) != len(set(data.ids)) or len({r.parent_id for r in rows.values()}) > 1:
        raise bad_request("Only categories with the same parent can be reordered together")
    for i, cid in enumerate(data.ids):
        rows[cid].sort_order = i
    db.commit()


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, data: CategoryPatch, _: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    row = db.get(ServiceCategoryRow, category_id)
    if not row:
        raise not_found("Category")
    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        row.name = values["name"].strip()  # the key never changes, so existing deals and vendors stay linked
    if "sort_order" in values:
        row.sort_order = values["sort_order"]
    if "active" in values:
        row.active = values["active"]
        if row.parent_id is None and not row.active:  # hiding a main category hides its sub-categories too
            for child in db.scalars(select(ServiceCategoryRow).where(ServiceCategoryRow.parent_id == row.id)):
                child.active = False
    db.commit()
    return _out(db, [row])[0]


@router.delete("/{category_id}", status_code=204)
def delete_category(category_id: int, _: CurrentUser = Depends(SuperAdmin), db: Session = Depends(get_db)):
    row = db.get(ServiceCategoryRow, category_id)
    if not row:
        raise not_found("Category")
    out = _out(db, [row])[0]
    if out.deal_count or out.vendor_count:
        raise bad_request("This category is in use by deals or vendors. Hide it instead of deleting.")
    if db.scalar(select(func.count()).select_from(ServiceCategoryRow).where(ServiceCategoryRow.parent_id == row.id)):
        raise bad_request("Delete or move its sub-categories first")
    db.delete(row)
    db.commit()
