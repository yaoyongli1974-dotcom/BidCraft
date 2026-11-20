"""交付硬门禁：HTTP / MCP / CLI 三条导出路径共用同一口径。

依赖方向：本模块只依赖 ``models`` / ``context`` / ``audit`` 这些底层模块，
``api.py``、``mcp.py``、``cli.py`` 单向 import 本模块，因此 MCP / CLI 不需要
（也不允许）import ``api.py``，避免循环依赖。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_audit
from .models import Issue

#: 视为「已处置」的 Issue 状态；resolved/closed 是历史值，读侧兼容。
ISSUE_RESOLVED_STATUSES = {"handled", "ignored", "resolved", "closed"}
#: 受导出硬门禁约束的格式；xlsx（事实台账）不受限。
EXPORT_GATE_FORMATS = {"docx", "pdf", "md", "markdown"}
#: 强制导出原因的净长度下限（去空白后按字符计）。
FORCE_REASON_MIN_CHARS = 4
FORCE_REASON_REQUIRED = "强制导出必须填写不少于 4 个字的原因"


class ExportGateError(Exception):
    """门禁拒绝；``status`` 沿用 HTTP 语义（409 未处置 / 400 原因不合规）。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def issue_is_open(issue: Issue) -> bool:
    return issue.status not in ISSUE_RESOLVED_STATUSES


def gate_blocked_message(blocking_open: int) -> str:
    return (
        f"存在 {blocking_open} 项未处置的废标/阻断风险，"
        "请先在风险总览中处置；确需导出请强制导出并填写原因"
    )


def count_open_blocking(db: Session, project_id: str) -> int:
    """统计项目内 level=="blocking" 且未处置的 Issue 数。"""

    return sum(
        1
        for row in db.scalars(
            select(Issue).where(Issue.project_id == project_id, Issue.level == "blocking")
        )
        if issue_is_open(row)
    )


def enforce_export_gate(
    db: Session,
    project_id: str,
    fmt: str,
    *,
    force: bool = False,
    force_reason: str = "",
    source: str | None = None,
    authorize_force: Callable[[], None] | None = None,
) -> int:
    """执行导出硬门禁；通过时返回未处置 blocking 数。

    受门禁格式且存在未处置 blocking 时：未 force → 抛 ``ExportGateError(409)``；
    force 为真则先调 ``authorize_force``（HTTP 用它做 owner/admin 校验，MCP/CLI
    按本地操作者处理不传），再校验原因去空白后 ≥4 字（否则 400），通过后写
    ``export.force`` 审计。``source`` 写入审计 detail，用于区分 mcp/cli 入口
    （HTTP 不传，保持既有 detail 结构不变）。
    """

    if fmt not in EXPORT_GATE_FORMATS:
        return 0
    blocking_open = count_open_blocking(db, project_id)
    if not blocking_open:
        return 0
    if not force:
        raise ExportGateError(409, gate_blocked_message(blocking_open))
    if authorize_force is not None:
        authorize_force()
    reason = (force_reason or "").strip()
    if len(reason) < FORCE_REASON_MIN_CHARS:
        raise ExportGateError(400, FORCE_REASON_REQUIRED)
    detail: dict[str, Any] = {
        "format": fmt,
        "blocking_open": blocking_open,
        "reason": reason,
    }
    if source:
        detail["source"] = source
    record_audit(db, project_id, "export.force", detail)
    return blocking_open
