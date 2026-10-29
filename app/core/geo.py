from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    d_lat = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """Cheap pre-filter for DB queries before the exact Haversine check."""
    d_lat = radius_km / 111.0
    d_lng = radius_km / (111.0 * max(cos(radians(lat)), 0.01))
    return lat - d_lat, lat + d_lat, lng - d_lng, lng + d_lng
