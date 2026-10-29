from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "Odeslo Society Platform API"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"
    APP_BASE_URL: str = "http://localhost:8000"
    RESIDENT_APP_URL: str = "http://localhost:3000"
    ADMIN_PANEL_URL: str = "http://localhost:3001"
    # Only these web origins may call the API from a browser (the phone app is not a browser and ignores CORS).
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    TIMEZONE: str = "Asia/Kolkata"

    # PostgreSQL: postgresql+psycopg://user:pass@localhost:5432/odeslo (SQLite works for quick local tests)
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/odeslo"
    AUTO_CREATE_TABLES: bool = True

    # Auth
    JWT_SECRET: str = "change-this-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 365  # a phone stays signed in until the person logs out
    REGISTRATION_TOKEN_EXPIRE_MINUTES: int = 30
    PASSWORD_MIN_LENGTH: int = 8

    # OTP
    OTP_LENGTH: int = 6
    OTP_EXPIRE_MINUTES: int = 5
    OTP_MAX_ATTEMPTS: int = 5
    OTP_MAX_PER_WINDOW: int = 5
    OTP_WINDOW_MINUTES: int = 15
    OTP_DEV_MODE: bool = True  # returns the OTP in the API response; never enable in production
    # A fixed code that is always accepted next to the real OTP (testing on phones). Only works while OTP_DEV_MODE is on and ENVIRONMENT is not production.
    TEST_OTP: str = ""

    # Uploads (stored outside any public root, served through an authenticated endpoint)
    UPLOAD_DIR: str = "./storage/uploads"
    MAX_UPLOAD_MB: int = 5  # only for non-image/PDF uploads (spreadsheets); image/document limits are in Platform settings
    FILE_ENCRYPTION_KEY: str = ""  # Fernet key for files at rest. Empty = a key file is created next to the uploads (dev)

    # Payments
    PAYMENT_GATEWAY: str = "mock"  # mock | razorpay
    PAYMENT_CURRENCY: str = "INR"
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # Notifications
    FCM_ENABLED: bool = False
    FCM_CREDENTIALS_FILE: str = ""
    SMS_PROVIDER: str = "console"  # console | http
    SMS_API_URL: str = ""
    SMS_API_KEY: str = ""
    WHATSAPP_PROVIDER: str = "console"  # console | http
    WHATSAPP_API_URL: str = ""
    WHATSAPP_API_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@odeslo.app"

    # Business rules
    BOOKING_PAYMENT_HOLD_MINUTES: int = 15
    COMPLAINT_AUTO_CLOSE_HOURS: int = 72
    MASJID_DEFAULT_RADIUS_KM: float = 5.0
    DEFAULT_NAMAZ_ALERT_MINUTES: int = 10

    # Background scheduler
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_SECONDS: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
