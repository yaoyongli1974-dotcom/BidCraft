"""Request-scoped identity used by project ownership checks.

``set_current_user`` returns the ContextVar token so callers (the auth
middleware) can restore the previous value in a ``finally`` block.  Without a
reset the identity set for one request can leak into other work executed in the
same context (e.g. subsequent requests handled by the same task, or code that
runs after the response).  Background workers deliberately do **not** rely on
this context — they receive explicit user_id / project_id / role arguments.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current: ContextVar[dict | None] = ContextVar("bidcraft_current_user", default=None)


def set_current_user(payload: dict | None) -> Token:
    """Bind ``payload`` as the current identity and return the reset token.

    The caller should pair this with ``reset_current_user(token)`` in a
    ``finally`` block so the identity does not outlive the request.
    """

    return _current.set(payload)


def reset_current_user(token: Token) -> None:
    """Restore the identity to whatever it was before ``set_current_user``."""

    _current.reset(token)


def current_user() -> dict | None:
    return _current.get()
