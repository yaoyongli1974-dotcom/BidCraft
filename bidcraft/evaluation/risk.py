"""风险聚合：读取现有 Issue / ★条款 / 导出门禁状态，产生风险摘要与建议。

不复制规则引擎：所有风险来源都是现有权威组件的判定结果。硬性风险
（★条款未确认、未处置 blocking）单独统计，供 scorer 一票否决。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..export_gate import blocked_substantive_evidence, count_open_blocking, unconfirmed_chapters
from ..models import Issue, Requirement

#: 视为"未处置"的 Issue 状态（与 export_gate 共用口径）。
_OPEN_STATUSES = {"open", "pending", ""}


def collect_project_risks(db: Session, project_id: str) -> dict[str, Any]:
    """汇总项目各来源风险，返回结构化数据（供 classify / summary / findings 使用）。"""

    issues = list(db.scalars(select(Issue).where(Issue.project_id == project_id)))
    open_issues = [row for row in issues if (row.status or "").lower() in _OPEN_STATUSES]
    by_level: Counter[str] = Counter(row.level for row in open_issues)

    blocking_open = count_open_blocking(db, project_id)
    substantive_blocked = blocked_substantive_evidence(db, project_id)
    pending_chapters = unconfirmed_chapters(db, project_id)

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
        "star_unconfirmed": len(star_unconfirmed),
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
                "evidence": {"code": row.code, "location": row.location or "", "status": row.status},
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
    return {
        "hard_risks": hard,
        "has_hard_risk": bool(hard),
        "blocking_open": risks.get("blocking_open", 0),
        "by_level": by_level,
        "issues_open": risks.get("issues_open", 0),
        "star_unconfirmed": risks.get("star_unconfirmed", 0),
        "pending_chapters": risks.get("pending_chapters", 0),
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
    by_level = summary.get("by_level", {})
    if by_level.get("critical") or by_level.get("major"):
        recs.append("复核严重/主要问题后在风险总览中标记处置")
    if not recs:
        recs.append("当前无硬性风险，可按正常流程交付")
    return recs
