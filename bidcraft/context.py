"""Request-scoped identity used by project ownership checks."""

from __future__ import annotations

from contextvars import ContextVar

_current: ContextVar[dict | None] = ContextVar("bidcraft_current_user", default=None)


def set_current_user(payload: dict | None) -> None:
    _current.set(payload)


def current_user() -> dict | None:
    return _current.get()
