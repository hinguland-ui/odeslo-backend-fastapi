from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActivityLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int | None
    user_type: str
    user_id: int | None
    user_name: str | None
    action: str
    entity: str
    entity_id: int | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    ip: str | None
    created_at: datetime
