"""Upload limits the super admin sets in Platform settings (max size and allowed formats per kind of file)."""

import threading
import time

DEFAULTS = {
    "max_image_mb": 5,
    "max_document_mb": 8,
    "image_formats": ["jpg", "png", "webp"],
    "document_formats": ["pdf", "jpg", "png", "webp"],
}
HARD_MAX_MB = 25  # whatever is saved in settings, nothing above this is ever accepted
KNOWN_FORMATS = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}
IMAGE_FORMATS = ("jpg", "png", "webp")

_TTL_SECONDS = 10
_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


def clean(value: dict | None) -> dict:
    """Fill gaps and clamp: sizes between 1 MB and the hard cap, formats only from the known list."""
    v = {**DEFAULTS, **(value or {})}
    out = dict(DEFAULTS)
    for key in ("max_image_mb", "max_document_mb"):
        try:
            out[key] = max(1, min(HARD_MAX_MB, int(v[key])))
        except (TypeError, ValueError):
            pass
    img = [f for f in IMAGE_FORMATS if f in (v.get("image_formats") or [])]
    doc = [f for f in KNOWN_FORMATS if f in (v.get("document_formats") or [])]
    out["image_formats"] = img or DEFAULTS["image_formats"]
    out["document_formats"] = doc or DEFAULTS["document_formats"]
    return out


def current_limits() -> dict:
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache[0] < _TTL_SECONDS:
            return _cache[1]
    from app.core.database import SessionLocal
    from app.modules.settings.models import PlatformSetting

    try:
        with SessionLocal() as db:
            row = db.get(PlatformSetting, "uploads")
            limits = clean(row.value if row else None)
    except Exception:  # database not ready (first start): safe defaults
        limits = clean(None)
    with _lock:
        _cache = (now, limits)
    return limits


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def allowed_for(kind: str) -> dict[str, str]:
    """mime -> extension, for 'image' or 'document' uploads, after the super admin's format choice."""
    limits = current_limits()
    formats = limits["image_formats"] if kind == "image" else limits["document_formats"]
    return {KNOWN_FORMATS[f]: f".{f}" for f in formats}


def max_mb_for(kind: str) -> int:
    limits = current_limits()
    return limits["max_image_mb"] if kind == "image" else limits["max_document_mb"]
