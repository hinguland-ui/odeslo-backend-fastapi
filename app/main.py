import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401  (register all tables)
from app.core.config import settings
from app.core.context import request_ip
from app.core import realtime  # noqa: F401  (registers the commit hooks)
from app.core.database import Base, engine
from app.core.migrations import run_migrations
from app.modules.activity_logs.router import router as activity_logs_router
from app.modules.announcements.router import router as announcements_router
from app.modules.auth.router import router as auth_router
from app.modules.categories.router import router as categories_router
from app.modules.bookings.router import router as bookings_router
from app.modules.complaints.router import router as complaints_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.files.router import router as files_router
from app.modules.group_deals.router import router as group_deals_router
from app.modules.maintenance.router import router as maintenance_router
from app.modules.location_requests.router import router as location_requests_router
from app.modules.masjids.router import router as masjids_router
from app.modules.worship_places.router import router as worship_places_router
from app.modules.notifications.router import router as notifications_router
from app.modules.onboarding.router import router as onboarding_router
from app.modules.payments.router import router as payments_router
from app.modules.realtime.router import router as realtime_router
from app.modules.refunds.router import router as refunds_router
from app.modules.requests.router import router as requests_router
from app.modules.residents.router import router as residents_router
from app.modules.service_jobs.router import router as service_jobs_router
from app.modules.service_partners.router import router as service_partners_router
from app.modules.settings.router import router as settings_router
from app.modules.societies.router import router as societies_router
from app.modules.society_admins.router import router as society_admins_router
from app.modules.staff.router import router as staff_router
from app.modules.super_admins.router import router as super_admins_router
from app.scheduler.jobs import scheduler_loop

logging.basicConfig(level=logging.DEBUG if settings.DEBUG else logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _register_existing_files() -> None:
    from app.core.database import SessionLocal
    from app.modules.files.service import backfill

    try:
        with SessionLocal() as db:
            backfill(db)
    except Exception:  # never block start-up
        logging.getLogger("odeslo").exception("Could not register existing uploads")


def _seed_categories() -> None:
    from app.core.database import SessionLocal
    from app.modules.categories.service import seed_defaults

    with SessionLocal() as db:
        seed_defaults(db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
        run_migrations(engine)
        _register_existing_files()
        _seed_categories()
    realtime.hub.loop = asyncio.get_running_loop()
    task = asyncio.create_task(scheduler_loop()) if settings.SCHEDULER_ENABLED else None
    yield
    if task:
        task.cancel()


def _production_checks() -> None:
    """In production the server refuses to start with settings that would leak or weaken security."""
    if settings.ENVIRONMENT.lower() != "production":
        return
    problems = []
    if settings.OTP_DEV_MODE:
        problems.append("OTP_DEV_MODE must be false (it sends the OTP back in the API response)")
    if settings.TEST_OTP:
        problems.append("TEST_OTP must be empty")
    if len(settings.JWT_SECRET) < 32 or "change-this" in settings.JWT_SECRET:
        problems.append("JWT_SECRET must be a long random secret (32+ characters)")
    if "*" in settings.CORS_ORIGINS:
        problems.append("CORS_ORIGINS must list your real web addresses, not *")
    if settings.DEBUG:
        problems.append("DEBUG must be false")
    if problems:
        raise RuntimeError("Unsafe production settings: " + "; ".join(problems))


_production_checks()
_IS_PROD = settings.ENVIRONMENT.lower() == "production"

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    # the interactive API docs list every endpoint: only available while developing
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
    description="Society Management & Resident Services Platform - Super Admin, Society Admin, Resident and "
                "Staff / Service Partner APIs.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # the web app shows the real file name when it opens an upload
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
    h.setdefault("Cross-Origin-Resource-Policy", "same-site")
    if request.url.path.startswith(settings.API_PREFIX):
        h.setdefault("Cache-Control", "no-store")  # nobody's data is kept in a browser or proxy cache
    if _IS_PROD:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.middleware("http")
async def capture_client_ip(request: Request, call_next):
    forwarded = request.headers.get("x-forwarded-for")
    request_ip.set(forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None))
    return await call_next(request)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_: Request, exc: IntegrityError):
    logging.getLogger("odeslo").warning("Integrity error: %s", exc.orig)
    return JSONResponse(status_code=409, content={"detail": "Duplicate or conflicting record"})


ROUTERS = [
    auth_router, super_admins_router, societies_router, society_admins_router, residents_router, staff_router,
    service_partners_router, group_deals_router, bookings_router, service_jobs_router, payments_router,
    refunds_router, maintenance_router, complaints_router, requests_router, announcements_router, masjids_router,
    notifications_router, activity_logs_router, dashboard_router, settings_router, files_router, realtime_router,
    onboarding_router, categories_router, worship_places_router, location_requests_router,
]
for r in ROUTERS:
    app.include_router(r, prefix=settings.API_PREFIX)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "app": settings.APP_NAME, "environment": settings.ENVIRONMENT}
