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
    db,
    project_id: str | None,
    action: str,
    detail: dict[str, Any] | None = None,
    *,
    actor: dict[str, Any] | None = None,
) -> AuditLog:
    """Record a successful mutating action.

    身份来源优先级：显式 ``actor``（供 Service 层由调用入口注入）> 请求上下文
    ``current_user()``（HTTP 中间件注入）> ``system``（CLI/MCP 本地操作者）。

    抽 Service 层后，HTTP 会把真实用户作为 ``actor`` 传入，CLI/MCP 传 ``None``
    保持记 system 的既有语义（测试已固化）。未传 ``actor`` 时行为完全不变，
    export_gate / 旧路由调用点无需改动。
    """

    user = actor if actor is not None else (current_user() or {})
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
