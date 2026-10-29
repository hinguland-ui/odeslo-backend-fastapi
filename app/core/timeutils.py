from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings

TZ = ZoneInfo(settings.TIMEZONE)


def now_ist() -> datetime:
    """Current time in Asia/Kolkata as a naive datetime (all DB datetimes are stored in IST)."""
    return datetime.now(TZ).replace(tzinfo=None)


def today_ist() -> date:
    return now_ist().date()
