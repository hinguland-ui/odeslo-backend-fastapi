from typing import Any

from sqlalchemy.orm import Session

from app.core.context import request_ip
from app.core.deps import CurrentUser
from app.modules.activity_logs.models import ActivityLog


def log_activity(
    db: Session,
    actor: CurrentUser | None,
    action: str,
    entity: str,
    entity_id: int | None = None,
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
    society_id: int | None = None,
) -> None:
    """Add an audit entry to the current transaction (committed together with the business change)."""
    db.add(
        ActivityLog(
            society_id=society_id if society_id is not None else (actor.society_id if actor else None),
            user_type=actor.user_type if actor else "system",
            user_id=actor.id if actor else None,
            user_name=actor.name if actor else "System",
            action=action,
            entity=entity,
            entity_id=entity_id,
            old_value=old,
            new_value=new,
            ip=request_ip.get(),
        )
    )
