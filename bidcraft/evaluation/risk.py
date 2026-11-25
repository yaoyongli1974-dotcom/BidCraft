"""风险聚合：读取现有 Issue / ★条款 / 导出门禁 / Agent 工具审计，产生风险摘要。

不复制规则引擎：Issue 未处置直接复用 ``export_gate.issue_is_open``，门禁证据
也全部来自 export_gate 的只读函数。硬性风险（★条款未确认、未处置 blocking、
实质性响应证据未确认）单独统计，供 scorer 一票否决。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..export_gate import (
    blocked_substantive_evidence,
    count_open_blocking,
    issue_is_open,
    stale_section_evidence,
    unconfirmed_chapters,
)
from ..models import AuditLog, Issue, Requirement

#: 工具调用失败的审计标记（真实 agent.tool.call payload，不新建数据表）。
_TOOL_ERROR_KEYS = ("isError", "is_error", "failed")
_TOOL_ERROR_TEXT_KEYS = ("error", "reason", "detail", "message")


def _tool_call_failed(detail: dict[str, Any]) -> bool:
    """审计 payload 里的失败标记；未知字段一律不臆断为失败。"""
    payload = detail if isinstance(detail, dict) else {}
    if any(payload.get(key) is True for key in _TOOL_ERROR_KEYS):
        return True
    if payload.get("ok") is False:
        return True
    for key in _TOOL_ERROR_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict) and any(
            bool(item) for item in value.values() if isinstance(item, (str, bool))
        ):
            return True
    return False


def collect_tool_audit_failures(db: Session, project_id: str) -> list[dict[str, Any]]:
    """从真实 AuditLog 读取失败的 agent.tool.call（成功/未知的不计）。"""
    failures: list[dict[str, Any]] = []
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.project_id == project_id, AuditLog.action == "agent.tool.call")
        .order_by(AuditLog.id)
    )
    for row in rows:
        detail = row.detail if isinstance(row.detail, dict) else {}
        if not _tool_call_failed(detail):
            continue
        failures.append(
            {
                "id": row.id,
                "tool": str(detail.get("tool") or detail.get("name") or ""),
                "detail": detail,
                "username": row.username or "system",
                "created_at": row.created_at,
            }
        )
    return failures


def collect_project_risks(db: Session, project_id: str) -> dict[str, Any]:
    """汇总项目各来源风险，返回结构化数据（供 classify / summary / findings 使用）。"""

    issues = list(db.scalars(select(Issue).where(Issue.project_id == project_id)))
    # 直接复用导出门禁的判定：in_progress/unknown 等状态仍然算未处置，不再漏掉。
    open_issues = [row for row in issues if issue_is_open(row)]
    by_level: Counter[str] = Counter(row.level for row in open_issues)

    blocking_open = count_open_blocking(db, project_id)
    substantive_blocked = blocked_substantive_evidence(db, project_id)
    pending_chapters = unconfirmed_chapters(db, project_id)
    stale_sections = stale_section_evidence(db, project_id)
    stale_evidence = sum(int(item.get("stale", 0) or 0) for item in stale_sections)
    tool_failures = collect_tool_audit_failures(db, project_id)

    requirements = list(
        db.scalars(select(Requirement).where(Requirement.project_id == project_id))
    )
    star_unconfirmed = [
        row
        for row in requirements
        if (row.marker or "").strip() == "★"
        and row.response_status not in {"responded", "na"}
        and (row.response_verdict or "").lower() != "satisfied"
    ]

    return {
        "issues_total": len(issues),
        "issues_open": len(open_issues),
        "by_level": dict(by_level),
        "blocking_open": blocking_open,
        "blocking_substantive": substantive_blocked,
        "pending_chapters": len(pending_chapters),
        "pending_chapter_rows": pending_chapters,
        "stale_evidence": stale_evidence,
        "stale_sections": stale_sections,
        "star_unconfirmed": len(star_unconfirmed),
        "tool_failures": tool_failures,
        "tool_failure_count": len(tool_failures),
        "open_issue_rows": open_issues,
        "star_unconfirmed_rows": star_unconfirmed,
    }


def classify_quality_risks(risks: dict[str, Any]) -> list[dict[str, Any]]:
    """把风险数据转成统一 finding 结构（category/level/title/description/evidence）。"""

    out: list[dict[str, Any]] = []

    for row in risks.get("open_issue_rows", []):
        out.append(
            {
                "category": "核对问题",
                "level": row.level or "major",
                "node_id": row.node_id,
                "requirement_id": None,
                "scoring_item_id": None,
                "title": f"[{row.code}] {row.category or '核对项'}",
                "description": row.finding or "",
                "recommendation": row.recommendation or "在风险总览中处置该问题",
                "evidence": {
                    "code": row.code,
                    "location": row.location or "",
                    "status": row.status,
                    "issue_id": row.id,
                    "stable_key": f"issue:{row.id}",
                },
            }
        )

    for row in risks.get("star_unconfirmed_rows", []):
        out.append(
            {
                "category": "实质性条款",
                "level": "blocking",
                "node_id": row.node_id,
                "requirement_id": row.id,
                "scoring_item_id": None,
                "title": "★ 实质性条款未确认",
                "description": (row.text or "")[:500],
                "recommendation": "由项目负责人确认该 ★ 条款的响应后方可交付",
                "evidence": {
                    "marker": row.marker,
                    "response_verdict": row.response_verdict,
                    "response_status": row.response_status,
                    "clause_no": row.clause_no,
                    "page": row.page,
                    "stable_key": f"requirement:{row.id}",
                },
            }
        )

    for failure in risks.get("tool_failures", []):
        detail = failure.get("detail") or {}
        out.append(
            {
                "category": "工具调用",
                "level": "major",
                "node_id": None,
                "requirement_id": None,
                "scoring_item_id": None,
                "title": f"Agent 工具调用失败：{failure.get('tool') or '未知工具'}",
                "description": (
                    str(detail.get("reason") or detail.get("error") or detail.get("detail") or "")
                    or "审计记录标记该次工具调用失败"
                ),
                "recommendation": "核对 agent.tool.call 审计并在需要时重试该工具",
                "evidence": {
                    "audit_id": failure.get("id"),
                    "tool": failure.get("tool"),
                    "isError": detail.get("isError"),
                    "run_id": detail.get("run_id") or "",
                    "task_id": detail.get("task_id") or "",
                    "detail": detail,
                    "stable_key": f"audit:{failure.get('id')}",
                },
            }
        )

    return out


def calculate_risk_summary(risks: dict[str, Any]) -> dict[str, Any]:
    """风险摘要：硬性风险清单 + 四档计数。"""

    by_level = risks.get("by_level", {})
    hard: list[str] = []
    if risks.get("star_unconfirmed", 0):
        hard.append("★ 实质性条款未确认")
    if risks.get("blocking_open", 0):
        hard.append("未处置的废标/阻断风险")
    # 导出门禁的三类拦截：实质性响应未确认直接否决，其余至少把结论压到 warn，
    # 防止综合分 PASS 掩盖「实际导不出」的事实（不改 export_gate.py）。
    export_blockers: list[str] = []
    if risks.get("blocking_substantive"):
        hard.append("实质性响应证据未确认（导出门禁拦截）")
        export_blockers.append("blocking_substantive")
    if risks.get("pending_chapters", 0):
        export_blockers.append("pending_chapters")
    if risks.get("stale_evidence", 0):
        export_blockers.append("stale_evidence")
    return {
        "hard_risks": hard,
        "has_hard_risk": bool(hard),
        "blocking_open": risks.get("blocking_open", 0),
        "by_level": by_level,
        "issues_open": risks.get("issues_open", 0),
        "star_unconfirmed": risks.get("star_unconfirmed", 0),
        "pending_chapters": risks.get("pending_chapters", 0),
        "stale_evidence": risks.get("stale_evidence", 0),
        "tool_failure_count": risks.get("tool_failure_count", 0),
        "export_blockers": export_blockers,
        "export_blocked": bool(export_blockers),
    }


def build_risk_recommendations(summary: dict[str, Any]) -> list[str]:
    """基于风险摘要给出人可读的处置建议。"""

    recs: list[str] = []
    if summary.get("star_unconfirmed", 0):
        recs.append(f"先确认 {summary['star_unconfirmed']} 条 ★ 实质性条款的响应")
    if summary.get("blocking_open", 0):
        recs.append(f"处置 {summary['blocking_open']} 项未关闭的废标/阻断风险")
    if summary.get("pending_chapters", 0):
        recs.append(f"完成 {summary['pending_chapters']} 章的复核确认（导出只认已确认章节）")
    if summary.get("stale_evidence", 0):
        recs.append(f"复核并更新 {summary['stale_evidence']} 处过期事实引用后再导出")
    if summary.get("tool_failure_count", 0):
        recs.append(f"排查 {summary['tool_failure_count']} 次 Agent 工具调用失败")
    by_level = summary.get("by_level", {})
    if by_level.get("critical") or by_level.get("major"):
        recs.append("复核严重/主要问题后在风险总览中标记处置")
    if not recs:
        recs.append("当前无硬性风险，可按正常流程交付")
    return recs
