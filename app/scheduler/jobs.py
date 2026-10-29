"""Periodic background jobs (run in-process every SCHEDULER_INTERVAL_SECONDS).

For multi-server deployments run only one scheduler instance (SCHEDULER_ENABLED=false elsewhere)
or call `python -m app.scheduler.run_once` from cron.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.enums import NotificationCategory, ResidentStatus, UserType
from app.core.prayer_times import PRAYERS
from app.core.timeutils import now_ist

logger = logging.getLogger("odeslo.scheduler")

_sent_namaz_alerts: set[tuple[int, str, str]] = set()  # (resident_id, date, prayer)
_last_daily_run: str | None = None


def run_frequent_jobs() -> dict:
    from app.modules.bookings.service import expire_payment_holds
    from app.modules.complaints.service import auto_close_resolved
    from app.modules.group_deals.service import close_expired_deals

    with SessionLocal() as db:
        result = {
            "expired_holds": expire_payment_holds(db),
            "closed_deals": close_expired_deals(db),
            "auto_closed_complaints": auto_close_resolved(db),
        }
        db.commit()
    return result


def run_daily_jobs() -> dict:
    from app.modules.maintenance.service import apply_overdue_and_late_fees

    with SessionLocal() as db:
        result = {"overdue_bills": apply_overdue_and_late_fees(db)}
        db.commit()
    return result


def send_namaz_alerts(now: datetime | None = None) -> int:
    """Push 'X minutes before jamaat' alerts to opted-in residents (push only, no in-app)."""
    from app.modules.masjids import service as masjid_service
    from app.modules.masjids.models import Masjid
    from app.modules.notifications.service import notify
    from app.modules.residents.models import Resident

    now = (now or now_ist()).replace(second=0, microsecond=0)
    today = now.date()
    sent = 0
    with SessionLocal() as db:
        residents = db.scalars(select(Resident).where(Resident.namaz_alerts_enabled.is_(True),
                                                      Resident.status == ResidentStatus.ACTIVE)).all()
        cache: dict = {}
        for r in residents:
            masjid = db.get(Masjid, r.preferred_masjid_id) if r.preferred_masjid_id else None
            if masjid is None and r.last_lat is not None:
                near = masjid_service.nearby(db, r.last_lat, r.last_lng, settings.MASJID_DEFAULT_RADIUS_KM, limit=1)
                masjid = near[0][0] if near else None
            lat, lng = (masjid.lat, masjid.lng) if masjid else (r.last_lat, r.last_lng)
            if lat is None:
                continue
            key = masjid.id if masjid else (round(lat, 2), round(lng, 2))
            if key not in cache:
                cache[key] = masjid_service.day_timings(db, masjid, lat, lng, today)["timings"]
            timings = cache[key]
            for prayer in PRAYERS:
                t = timings.get("jummah") if prayer == "dhuhr" and today.weekday() == 4 and timings.get("jummah") \
                    else timings.get(prayer)
                if not t:
                    continue
                at = datetime.combine(today, datetime.strptime(t, "%H:%M").time())
                alert_key = (r.id, today.isoformat(), prayer)
                if at - timedelta(minutes=r.namaz_alert_minutes) == now and alert_key not in _sent_namaz_alerts:
                    place = f" at {masjid.name}" if masjid else ""
                    notify(db, UserType.RESIDENT, r.id, NotificationCategory.NAMAZ, f"{prayer.title()} in "
                           f"{r.namaz_alert_minutes} min", f"{prayer.title()} jamaat{place} at {t}.",
                           ref_type="masjid", ref_id=masjid.id if masjid else None, push=True, in_app=False)
                    _sent_namaz_alerts.add(alert_key)
                    sent += 1
        db.commit()
    # keep memory bounded
    for k in [k for k in _sent_namaz_alerts if k[1] != today.isoformat()]:
        _sent_namaz_alerts.discard(k)
    return sent


def tick() -> None:
    global _last_daily_run
    try:
        result = run_frequent_jobs()
        if any(result.values()):
            logger.info("Scheduler: %s", result)
        today = now_ist().date().isoformat()
        if _last_daily_run != today:
            logger.info("Scheduler daily: %s", run_daily_jobs())
            _last_daily_run = today
        send_namaz_alerts()
    except Exception:
        logger.exception("Scheduler tick failed")


async def scheduler_loop() -> None:
    while True:
        await asyncio.to_thread(tick)
        await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)
