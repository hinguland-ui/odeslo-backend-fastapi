"""Standard astronomical prayer-time calculation (fallback when a masjid has no verified timetable).

Based on the well known PrayTimes algorithm. Method and Asr juristic setting are configurable by the
Super Admin through platform settings.
"""

from datetime import date
from math import acos, asin, atan, atan2, cos, degrees, floor, radians, sin, tan

# fajr angle, isha angle (or minutes after maghrib when isha_minutes is set)
METHODS: dict[str, dict] = {
    "karachi": {"name": "University of Islamic Sciences, Karachi", "fajr": 18.0, "isha": 18.0},
    "mwl": {"name": "Muslim World League", "fajr": 18.0, "isha": 17.0},
    "isna": {"name": "Islamic Society of North America", "fajr": 15.0, "isha": 15.0},
    "egypt": {"name": "Egyptian General Authority of Survey", "fajr": 19.5, "isha": 17.5},
    "makkah": {"name": "Umm Al-Qura, Makkah", "fajr": 18.5, "isha_minutes": 90},
}
ASR_FACTORS = {"standard": 1, "hanafi": 2}

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")


def _dsin(d): return sin(radians(d))
def _dcos(d): return cos(radians(d))
def _dtan(d): return tan(radians(d))
def _darcsin(x): return degrees(asin(x))
def _darccos(x): return degrees(acos(max(-1.0, min(1.0, x))))
def _darctan2(y, x): return degrees(atan2(y, x))
def _darccot(x): return degrees(atan(1 / x))
def _fix(a, b): a = a - b * floor(a / b); return a + b if a < 0 else a


def _julian(d: date) -> float:
    y, m = d.year, d.month
    if m <= 2:
        y -= 1
        m += 12
    a = floor(y / 100)
    b = 2 - a + floor(a / 4)
    return floor(365.25 * (y + 4716)) + floor(30.6001 * (m + 1)) + d.day + b - 1524.5


def _sun_position(jd: float) -> tuple[float, float]:
    d = jd - 2451545.0
    g = _fix(357.529 + 0.98560028 * d, 360)
    q = _fix(280.459 + 0.98564736 * d, 360)
    lon = _fix(q + 1.915 * _dsin(g) + 0.020 * _dsin(2 * g), 360)
    e = 23.439 - 0.00000036 * d
    ra = _darctan2(_dcos(e) * _dsin(lon), _dcos(lon)) / 15
    eqt = q / 15 - _fix(ra, 24)
    decl = _darcsin(_dsin(e) * _dsin(lon))
    return decl, eqt


def calculate_prayer_times(
    day: date, lat: float, lng: float, tz_offset_hours: float = 5.5, method: str = "karachi", asr: str = "hanafi"
) -> dict[str, str]:
    """Return {'fajr','sunrise','dhuhr','asr','maghrib','isha'} as HH:MM local time."""
    params = METHODS.get(method, METHODS["karachi"])
    asr_factor = ASR_FACTORS.get(asr, 2)
    jd = _julian(day) - lng / (15 * 24.0)

    def mid_day(t):
        return _fix(12 - _sun_position(jd + t)[1], 24)

    def sun_angle_time(angle, t, ccw=False):
        decl = _sun_position(jd + t)[0]
        noon = mid_day(t)
        x = (-_dsin(angle) - _dsin(decl) * _dsin(lat)) / (_dcos(decl) * _dcos(lat))
        delta = _darccos(x) / 15
        return noon - delta if ccw else noon + delta

    def asr_time(t):
        decl = _sun_position(jd + t)[0]
        angle = -_darccot(asr_factor + _dtan(abs(lat - decl)))
        return sun_angle_time(angle, t)

    # initial guesses (hours) converted to day fraction, then one refinement pass
    guess = {"fajr": 5, "sunrise": 6, "dhuhr": 12, "asr": 13, "sunset": 18, "isha": 18}
    times = guess
    for _ in range(2):
        p = {k: v / 24.0 for k, v in times.items()}
        times = {
            "fajr": sun_angle_time(params["fajr"], p["fajr"], ccw=True),
            "sunrise": sun_angle_time(0.833, p["sunrise"], ccw=True),
            "dhuhr": mid_day(p["dhuhr"]),
            "asr": asr_time(p["asr"]),
            "sunset": sun_angle_time(0.833, p["sunset"]),
            "isha": sun_angle_time(params.get("isha", 18.0), p["isha"]) if "isha" in params else p["isha"] * 24,
        }

    adjust = tz_offset_hours - lng / 15.0
    result = {k: v + adjust for k, v in times.items()}
    result["dhuhr"] += 1 / 60.0  # dhuhr a minute after zenith
    result["maghrib"] = result["sunset"] + 1 / 60.0
    if "isha_minutes" in params:
        result["isha"] = result["maghrib"] + params["isha_minutes"] / 60.0

    def fmt(h: float) -> str:
        h = _fix(h + 0.5 / 60.0, 24)  # round to nearest minute
        hours = int(floor(h))
        minutes = int(floor((h - hours) * 60))
        return f"{hours:02d}:{minutes:02d}"

    return {k: fmt(result[k]) for k in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")}
