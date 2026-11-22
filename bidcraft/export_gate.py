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
from .models import Issue, OutlineNode, Section

# 被强制导出越过的「实质性条款」问题类别（SUB-* 的 category）。
SUBSTANTIVE_ISSUE_CATEGORY = "实质性响应"

#: 视为「已处置」的 Issue 状态；resolved/closed 是历史值，读侧兼容。
ISSUE_RESOLVED_STATUSES = {"handled", "ignored", "resolved", "closed"}
#: 受导出硬门禁约束的格式；xlsx（事实台账）不受限。
EXPORT_GATE_FORMATS = {"docx", "pdf", "md", "markdown"}
#: 章节复核状态；只有 confirmed / locked 的正文才算「已确认的版本」。
CHAPTER_CONFIRMED_STATUSES = {"confirmed", "locked"}
CHAPTER_STATUS_LABELS = {
    "draft": "草稿",
    "review": "待复核",
    "confirmed": "已确认",
    "locked": "已锁定",
}
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


def unconfirmed_chapters(
    db: Session, project_id: str
) -> list[tuple[str, str]]:
    """返回「有正文但尚未确认」的章节：[(heading, review_status)]。

    空章节不参与（还没写的章不该拦住整份交付）；已确认/已锁定视为可导出。
    """

    rows = db.execute(
        select(OutlineNode.heading, Section.body, Section.review_status)
        .join(Section, Section.node_id == OutlineNode.id, isouter=True)
        .where(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.position)
    ).all()
    pending: list[tuple[str, str]] = []
    for heading, body, review_status in rows:
        if not (body or "").strip():
            continue
        status = review_status or "draft"
        if status not in CHAPTER_CONFIRMED_STATUSES:
            pending.append((heading, status))
    return pending


def chapter_gate_message(pending: list[tuple[str, str]]) -> str:
    counts: dict[str, int] = {}
    for _heading, status in pending:
        counts[status] = counts.get(status, 0) + 1
    parts = [
        f"{CHAPTER_STATUS_LABELS.get(status, status)} {count} 章"
        for status, count in sorted(counts.items())
    ]
    names = "、".join(f"《{heading}》" for heading, _status in pending[:3])
    suffix = " 等" if len(pending) > 3 else ""
    return (
        f"还有 {len(pending)} 章未确认（{' / '.join(parts)}）：{names}{suffix}。"
        "导出只读取已确认的章节，请先完成复核确认"
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


def blocked_substantive_evidence(db: Session, project_id: str) -> list[dict[str, Any]]:
    """返回被门禁拦住的 ★/▲ 实质性条款证据，供强制导出审计留痕。

    每条给出问题编号、标记、原文、原始判定与原因。普通 blocking 风险不在此列，
    只有 category=="实质性响应" 的才记录——这些是"未确认不得交付"的人工检查点。
    """

    result: list[dict[str, Any]] = []
    for row in db.scalars(
        select(Issue).where(
            Issue.project_id == project_id,
            Issue.level == "blocking",
            Issue.category == SUBSTANTIVE_ISSUE_CATEGORY,
        )
    ):
        if not issue_is_open(row):
            continue
        evidence = row.evidence or {}
        result.append(
            {
                "code": row.code,
                "marker": evidence.get("marker", ""),
                "finding": (row.finding or "")[:200],
                "verdict": evidence.get("verdict", ""),
                "verdict_label": evidence.get("verdict_label", ""),
                "verdict_reason": evidence.get("verdict_reason", ""),
            }
        )
    return result


def enforce_export_gate(
    db: Session,
    project_id: str,
    fmt: str,
    *,
    force: bool = False,
    force_reason: str = "",
    source: str | None = None,
    authorize_force: Callable[[], None] | None = None,
    require_confirmed_chapters: bool = True,
) -> int:
    """执行导出硬门禁；通过时返回未处置 blocking 数。

    受门禁格式且存在未处置 blocking 时：未 force → 抛 ``ExportGateError(409)``；
    force 为真则先调 ``authorize_force``（HTTP 用它做 owner/admin 校验，MCP/CLI
    按本地操作者处理不传），再校验原因去空白后 ≥4 字（否则 400），通过后写
    ``export.force`` 审计。``source`` 写入审计 detail，用于区分 mcp/cli 入口
    （HTTP 不传，保持既有 detail 结构不变）。

    章节确认是人工检查点，``require_confirmed_chapters=False`` 只给
    ``bidcraft run``（一条命令解析→生成→导出、没有复核环节）使用；
    其余入口一律强制。强制导出**不能**绕过章节确认。
    """

    if fmt not in EXPORT_GATE_FORMATS:
        return 0
    blocking_open = count_open_blocking(db, project_id)
    if not blocking_open:
        # 只剩章节确认这一道；未确认的正文不得进入交付件（xlsx 已在上方豁免）。
        if require_confirmed_chapters:
            pending = unconfirmed_chapters(db, project_id)
            if pending:
                raise ExportGateError(409, chapter_gate_message(pending))
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
    # 完整审计：记录被越过的实质性条款（★/▲）及其原始判定，满足
    # 「强制导出必须有独立权限和完整审计记录」——谁、何时、越过哪些条款、原判定。
    substantives = blocked_substantive_evidence(db, project_id)
    if substantives:
        detail["overridden_substantives"] = substantives
    record_audit(db, project_id, "export.force", detail)
    # 强制导出只覆盖「未处置阻断风险」；章节未确认仍然拦截——强制导出不能绕过人工检查点。
    if require_confirmed_chapters:
        pending = unconfirmed_chapters(db, project_id)
        if pending:
            raise ExportGateError(409, chapter_gate_message(pending))
    return blocking_open
