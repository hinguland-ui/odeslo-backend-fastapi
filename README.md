# Odeslo Backend — Society Management & Resident Services Platform

Python **FastAPI** + **SQLAlchemy 2** backend for the four logins in the functional spec (Super Admin, Society Admin, Resident, Staff / Service Partner).

## Quick start

```bash
cd odeslo-backend
python -m venv .venv && .venv\Scripts\activate        # Windows  (Linux/mac: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                                  # edit values
python -m scripts.seed_demo                             # optional: pilot society "Ajit Residency" + demo logins
uvicorn app.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- All APIs are under `/api/v1`
- Create a real Super Admin: `python -m scripts.create_super_admin --name "Akmal" --email you@x.com --mobile 98XXXXXXXX`
- Run the tests: `python -m pytest -q`

**Database: PostgreSQL.** Create an empty database named `odeslo` (pgAdmin → Databases → Create → Database), then set
`DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/odeslo` in `.env`. All tables are created automatically on first start.
To review the SQL before running it: `python -m scripts.export_schema_sql > schema.sql` (then you can set `AUTO_CREATE_TABLES=false`).

## Folder structure

Each function has its own folder with its **own models**, schemas (request/response), service (business logic) and router (API).

```
odeslo-backend/
├── app/
│   ├── main.py                  # FastAPI app, routers, middleware, scheduler start
│   ├── models.py                # imports every module's models (table registry)
│   ├── core/                    # shared building blocks
│   │   ├── config.py            # settings from .env
│   │   ├── database.py          # engine, session, Base, timestamps
│   │   ├── enums.py             # every status & choice (spec section 7)
│   │   ├── deps.py              # auth + role/permission checks + society scoping
│   │   ├── security.py          # bcrypt, JWT, OTP hashing
│   │   ├── files.py             # upload validation (type, size, magic bytes), random names
│   │   ├── prayer_times.py      # prayer time calculation fallback
│   │   ├── geo.py               # Haversine distance
│   │   └── pagination.py, errors.py, utils.py, types.py, timeutils.py, context.py
│   ├── integrations/            # SMS / WhatsApp / email, FCM push, payment gateway (Razorpay + mock)
│   ├── scheduler/               # background jobs (expire payment holds, close deals, late fees, auto-close, namaz alerts)
│   └── modules/
│       ├── auth/                # OTP login, Super Admin 2FA, Society Admin password, /me
│       ├── super_admins/
│       ├── societies/           # societies, wings, units, bulk upload, features, join code / QR
│       ├── society_admins/      # primary + co-admins (treasurer / operations presets)
│       ├── residents/           # registration, approval, family, tenant, vehicles, documents, settings
│       ├── staff/
│       ├── service_partners/    # verification, society assignment, earnings
│       ├── group_deals/
│       ├── bookings/            # capacity, waitlist, promotion, cancellation, invoice, rating
│       ├── service_jobs/        # assign, status flow, issues, payouts
│       ├── payments/            # online (gateway + webhook) & offline, receipts, export
│       ├── refunds/             # Requested → Approved → Processed → Completed / Rejected
│       ├── maintenance/         # bill generation, reminders, defaulters, summary
│       ├── complaints/          # with comments, reopen, 72h auto-close
│       ├── requests/            # NOC, move-in/out, parking ...
│       ├── announcements/
│       ├── masjids/             # nearby, timetable, verification, prayer times
│       ├── notifications/       # in-app list, unread badge, device tokens, manual push
│       ├── activity_logs/       # audit trail (who / what / when / old / new / IP)
│       ├── dashboard/           # dashboards for each role + reports
│       ├── settings/            # platform settings (gateway, SMS, prayer method, app version)
│       └── files/               # authenticated file download
├── scripts/                     # create_super_admin, seed_demo, export_schema_sql
└── tests/                       # end-to-end role-wise tests (spec 11.2 checklist)
```

Each module contains `models.py` · `schemas.py` · `service.py` · `router.py` (smaller modules leave out the files they don't need).

## Logins

| Login | Endpoint(s) |
|---|---|
| Super Admin | `POST /auth/super-admin/login` (email + password) → `POST /auth/super-admin/verify` (OTP) |
| Society Admin | `POST /auth/otp/send` + `/auth/otp/verify` (`user_type=society_admin`) or `POST /auth/society-admin/login`. First login returns `must_set_password=true` → `POST /auth/set-password` |
| Resident | `POST /auth/otp/send` + `/auth/otp/verify` (`user_type=resident`). A new mobile gets a `registration_token` → `POST /residents/register` |
| Staff / Partner | `POST /auth/otp/send` + `/auth/otp/verify` (`user_type=staff` / `partner`) |

Send the token as `Authorization: Bearer <token>`. The user is reloaded and the role is checked on every request.

## Business rules enforced in code

- **Cancelled bookings are never counted.** `confirmed_units` is recomputed from bookings in `pending_payment` / `confirmed` / `completed` status only.
- **Confirmed units can never go above max units.** The deal row is locked (`SELECT … FOR UPDATE`) while booking.
- **Waitlist is first-come-first-served** by booking time. When capacity frees up, the next booking is promoted automatically (if the deal allows it) or the admin promotes it manually.
- **Payment holds:** an unpaid booking holds its units for `BOOKING_PAYMENT_HOLD_MINUTES`. After that it is cancelled and the waitlist moves up.
- **One active primary account per unit.** A registration for an occupied unit is flagged; approving it returns `409`.
- **Society isolation.** Residents and Society Admins only see their own society. Records from other societies return `404`.
- **Unverified masjids are never shown to residents.** Timetables use verified jamaat times and fall back to calculated prayer times.
- **Every money movement has a transaction ID and a log entry.** Every action that changes data is written to `activity_logs`.
- **Dashboard "Residents" count** links to `/residents?status=active` (approved list), not the approval queue.
- Pending residents can only use the Masjid feature and their own profile.

## Production checklist

- `OTP_DEV_MODE=false`, a strong `JWT_SECRET`, and `DEBUG=false`
- `PAYMENT_GATEWAY=razorpay` with keys. Webhook URL: `POST /api/v1/payments/webhook/razorpay`
- `FCM_ENABLED=true` + `pip install firebase-admin` + service-account JSON
- SMS / WhatsApp HTTP provider URLs
- Run a single scheduler instance, or set `SCHEDULER_ENABLED=false` and use cron: `python -m app.scheduler.run_once`
- `UPLOAD_DIR` must be outside the public web root
