"""Request-scoped context (client IP) used by the activity log."""

from contextvars import ContextVar

request_ip: ContextVar[str | None] = ContextVar("request_ip", default=None)
