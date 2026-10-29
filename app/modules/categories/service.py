import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import ServiceCategory
from app.core.errors import bad_request
from app.modules.categories.models import ServiceCategoryRow


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48] or "category"


DEFAULT_NAMES = {
    "ac": "AC & appliances", "plumbing": "Plumbing", "electrical": "Electrical", "pest_control": "Pest control",
    "cleaning": "Cleaning", "carpentry": "Carpentry", "painting": "Painting", "appliance_repair": "Appliance repair",
    "water_tank_cleaning": "Water tank cleaning", "other": "Other",
}


def seed_defaults(db: Session) -> None:
    """First start: the built-in categories become editable rows (names of untouched built-ins are tidied once)."""
    if not db.scalar(select(func.count()).select_from(ServiceCategoryRow)):
        for i, c in enumerate(ServiceCategory):
            db.add(ServiceCategoryRow(key=c.value, name=DEFAULT_NAMES[c.value], sort_order=i))
    else:
        for r in db.scalars(select(ServiceCategoryRow).where(ServiceCategoryRow.parent_id.is_(None))):
            if r.key in DEFAULT_NAMES and r.name == r.key.replace("_", " ").title():
                r.name = DEFAULT_NAMES[r.key]
    db.commit()


def unique_key(db: Session, name: str) -> str:
    base = slugify(name)
    key, n = base, 2
    while db.scalar(select(ServiceCategoryRow.id).where(ServiceCategoryRow.key == key)):
        key, n = f"{base}_{n}", n + 1
    return key


def validate_category(db: Session, category: str, subcategory: str | None = None) -> None:
    """The category must be an active top-level category and the sub-category one of its active children."""
    row = db.scalar(select(ServiceCategoryRow).where(ServiceCategoryRow.key == category))
    if not row or row.parent_id is not None or not row.active:
        raise bad_request(f"Unknown category '{category}'")
    if subcategory:
        sub = db.scalar(select(ServiceCategoryRow).where(ServiceCategoryRow.key == subcategory))
        if not sub or sub.parent_id != row.id or not sub.active:
            raise bad_request(f"'{subcategory}' is not a sub-category of '{category}'")


def validate_categories(db: Session, keys: list[str]) -> list[str]:
    for k in keys:
        validate_category(db, k)
    return keys
