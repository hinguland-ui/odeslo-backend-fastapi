from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import MasjidStatus
from app.core.geo import bounding_box, haversine_km
from app.core.prayer_times import PRAYERS, calculate_prayer_times
from app.core.timeutils import now_ist
from app.modules.masjids.models import Masjid, MasjidTiming
from app.modules.settings.service import get_setting

IST_OFFSET_HOURS = 5.5


def active_timing(db: Session, masjid_id: int, day: date) -> MasjidTiming | None:
    return db.scalar(select(MasjidTiming).where(MasjidTiming.masjid_id == masjid_id, MasjidTiming.verified.is_(True),
                                                MasjidTiming.effective_from <= day)
                     .order_by(MasjidTiming.effective_from.desc(), MasjidTiming.id.desc()))


def calculated_times(db: Session, lat: float, lng: float, day: date) -> tuple[dict[str, str], str]:
    cfg = get_setting(db, "prayer_calculation")
    times = calculate_prayer_times(day, lat, lng, IST_OFFSET_HOURS, cfg.get("method", "karachi"),
                                   cfg.get("asr", "hanafi"))
    return times, cfg.get("method", "karachi")


def day_timings(db: Session, masjid: Masjid | None, lat: float, lng: float, day: date) -> dict:
    """Verified jamaat timings of the masjid, falling back to calculated times for the location."""
    calc, method = calculated_times(db, lat, lng, day)
    timing = active_timing(db, masjid.id, day) if masjid else None
    if timing:
        timings = {p: getattr(timing, p) for p in PRAYERS}
        timings["jummah"] = timing.jummah
        source = "masjid"
    else:
        timings = {p: calc[p] for p in PRAYERS}
        timings["jummah"] = None
        source = "calculated"
    return {"timings": timings, "sunrise": calc["sunrise"], "source": source,
            "calculation_method": None if source == "masjid" else method}


def _prayer_order_for(day: date, timings: dict[str, str | None]) -> list[tuple[str, str]]:
    order = []
    for p in PRAYERS:
        t = timings.get(p)
        if p == "dhuhr" and day.weekday() == 4 and timings.get("jummah"):  # Friday
            order.append(("jummah", timings["jummah"]))
        elif t:
            order.append((p, t))
    return order


def current_and_next(day_info_today: dict, day_info_tomorrow: dict, now: datetime | None = None) -> tuple:
    now = now or now_ist()
    today = now.date()
    order = _prayer_order_for(today, day_info_today["timings"])
    current = None
    for name, t in order:
        at = datetime.combine(today, datetime.strptime(t, "%H:%M").time())
        if at > now:
            return current, {"name": name, "time": t, "seconds_until": int((at - now).total_seconds()),
                             "is_tomorrow": False}
        current = name
    tomorrow = today + timedelta(days=1)
    first_name, first_time = _prayer_order_for(tomorrow, day_info_tomorrow["timings"])[0]
    at = datetime.combine(tomorrow, datetime.strptime(first_time, "%H:%M").time())
    return current, {"name": first_name, "time": first_time, "seconds_until": int((at - now).total_seconds()),
                     "is_tomorrow": True}


def timetable(db: Session, masjid: Masjid | None, lat: float, lng: float, day: date) -> dict:
    info = day_timings(db, masjid, lat, lng, day)
    current = nxt = None
    if day == now_ist().date():
        tomorrow = day_timings(db, masjid, lat, lng, day + timedelta(days=1))
        current, nxt = current_and_next(info, tomorrow)
    return {"masjid": masjid, "date": day, "source": info["source"], "timings": info["timings"],
            "sunrise": info["sunrise"], "current_prayer": current, "next_prayer": nxt,
            "calculation_method": info["calculation_method"]}


def nearby(db: Session, lat: float, lng: float, radius_km: float, limit: int = 30) -> list[tuple[Masjid, float]]:
    """Verified masjids within radius, nearest first. Unverified masjids are never shown to residents."""
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    candidates = db.scalars(select(Masjid).where(
        Masjid.verified.is_(True), Masjid.status == MasjidStatus.VERIFIED,
        Masjid.lat.between(min_lat, max_lat), Masjid.lng.between(min_lng, max_lng))).all()
    with_distance = [(m, haversine_km(lat, lng, m.lat, m.lng)) for m in candidates]
    return sorted([x for x in with_distance if x[1] <= radius_km], key=lambda x: x[1])[:limit]
