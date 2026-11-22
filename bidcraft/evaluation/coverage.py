"""覆盖率维度：实质性条款 / 评分点 / 证据 / 章节确认。

全部只读现有权威来源：Requirement（含 verdict 状态）、ScoringItem、
provenance.report、generation.coverage、export_gate.unconfirmed_chapters。
不修改任何业务数据。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OutlineNode, Requirement, ScoringItem


def calculate_requirement_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """实质性条款四态分布：satisfied / unsatisfied / partial / review。

    `response_status` 已人工确认（responded/na）的条款按"已处置"计入覆盖。
    ★ 未确认（pending 且 verdict=review/unsatisfied/partial）单列为硬风险。
    """

    rows = list(
        db.scalars(
            select(Requirement).where(Requirement.project_id == project_id)
        )
    )
    states: Counter[str] = Counter()
    star_unconfirmed = 0
    substantive_total = 0
    substantive_covered = 0
    for row in rows:
        marker = (row.marker or "").strip()
        is_substantive = bool(marker) or bool(row.mandatory)
        state = (row.response_verdict or "").strip().lower() or "pending"
        # 人工已确认响应/不适用 → 视为覆盖
        if row.response_status in {"responded", "na"}:
            state = "satisfied"
        states[state] += 1
        if is_substantive:
            substantive_total += 1
            if state == "satisfied":
                substantive_covered += 1
            if marker == "★" and row.response_status not in {"responded", "na"} and state != "satisfied":
                star_unconfirmed += 1
    total = len(rows)
    ratio = round(substantive_covered / substantive_total, 4) if substantive_total else 1.0
    return {
        "total": total,
        "states": dict(states),
        "satisfied": states.get("satisfied", 0),
        "unsatisfied": states.get("unsatisfied", 0),
        "partial": states.get("partial", 0),
        "review": states.get("review", 0),
        "pending": states.get("pending", 0),
        "substantive_total": substantive_total,
        "substantive_covered": substantive_covered,
        "ratio": ratio,
        "star_unconfirmed": star_unconfirmed,
    }


def calculate_scoring_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """评分点覆盖：复用 generation.coverage 的只读矩阵口径。"""

    from ..generation.coverage import calculate_score_coverage
    from ..generation.outline import load_outline
    from ..scoring import score_payload

    items = list(
        db.scalars(
            select(ScoringItem).where(ScoringItem.project_id == project_id)
        )
    )
    scores = [score_payload(item) for item in items]
    nodes = load_outline(db, project_id)
    report = calculate_score_coverage(scores, nodes)
    return {
        "total_items": report.get("total_items", len(items)),
        "total_score": report.get("total_score", 0.0),
        "covered_score": report.get("covered_score", 0.0),
        "covered_ratio": report.get("covered_ratio", 0.0),
        "uncovered_count": report.get("uncovered_count", 0),
    }


def calculate_evidence_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """证据/来源追溯：复用 provenance.report 的 stale 统计。"""

    from ..provenance import report as provenance_report

    data = provenance_report(db, project_id)
    summary = data.get("summary", {}) if isinstance(data, dict) else {}
    citations = summary.get("citations", 0)
    stale = summary.get("stale", 0)
    return {
        "citations": citations,
        "stale": stale,
        "facts_cited": summary.get("facts_cited", 0),
        "sections_citing": summary.get("sections_citing", 0),
        "ratio": round((citations - stale) / citations, 4) if citations else 1.0,
    }


def calculate_chapter_coverage(db: Session, project_id: str) -> dict[str, Any]:
    """章节确认覆盖：复用 export_gate.unconfirmed_chapters（空章节不拦）。"""

    from ..export_gate import unconfirmed_chapters

    pending = unconfirmed_chapters(db, project_id)
    total = len(
        list(db.scalars(select(OutlineNode).where(OutlineNode.project_id == project_id)))
    )
    confirmed = max(0, total - len(pending))
    return {
        "total": total,
        "confirmed": confirmed,
        "unconfirmed": len(pending),
        "pending_chapters": [heading for heading, _status in pending],
        "ratio": round(confirmed / total, 4) if total else 1.0,
    }
