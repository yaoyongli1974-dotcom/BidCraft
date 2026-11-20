"""Append-only audit writer shared by the HTTP / MCP / CLI entry points.

Lives below ``api.py`` on purpose: MCP and CLI must be able to record audit
rows without importing the HTTP layer (which would create a cycle). The
request-scoped user comes from :mod:`bidcraft.context`; MCP/CLI run as the
local operator, so the context is empty and the row is attributed to
``"system"``.
"""

from __future__ import annotations

from typing import Any

from .context import current_user
from .models import AuditLog


def record_audit(
    db, project_id: str | None, action: str, detail: dict[str, Any] | None = None
) -> AuditLog:
    """Record a successful mutating action for the current context user."""

    user = current_user() or {}
    row = AuditLog(
        project_id=project_id,
        user_id=user.get("id"),
        username=user.get("username") or "system",
        display_name=user.get("display_name") or user.get("username") or "system",
        action=action,
        detail=detail or {},
    )
    db.add(row)
    db.flush()
    return row
