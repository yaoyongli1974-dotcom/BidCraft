"""质量报告编排：聚合各维度结果 → 评分 → 落库不可变快照 + finding 明细。

报告快照不可变：每次运行 report_version = 当前最大 + 1；已生成的报告/发现
不因后续核对而改写（finding 的 status 是可人工处置的动作字段，单独更新）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..audit import record_audit
from ..models import QualityFinding, QualityReport
from .commercial import collect_commercial_completeness
from .coverage import (
    calculate_chapter_coverage,
    calculate_evidence_coverage,
    calculate_requirement_coverage,
    calculate_scoring_coverage,
)
from .risk import (
    build_risk_recommendations,
    calculate_risk_summary,
    classify_quality_risks,
    collect_project_risks,
)
from .scorer import evaluate_project_quality


def _next_version(db: Session, project_id: str) -> int:
    current = db.scalar(
        select(QualityReport.report_version)
        .where(QualityReport.project_id == project_id)
        .order_by(QualityReport.report_version.desc())
    )
    return (current or 0) + 1


def build_quality_report(
    db: Session,
    project_id: str,
    *,
    created_by: str = "",
    note: str = "",
    weights: dict[str, float] | None = None,
    commercial_info: dict[str, Any] | None = None,
) -> QualityReport:
    """生成并落库一份质量报告（含 finding 明细）。与调用方同事务。"""

    if commercial_info is None:
        commercial_info = collect_commercial_completeness(db, project_id)
    coverage = {
        "requirement": calculate_requirement_coverage(db, project_id),
        "scoring": calculate_scoring_coverage(db, project_id),
        "evidence": calculate_evidence_coverage(db, project_id),
        "chapter": calculate_chapter_coverage(db, project_id),
    }
    risks = collect_project_risks(db, project_id)
    risk_summary = calculate_risk_summary(risks)
    if commercial_info is not None:
        # 商务标必填项未完成计入 major，既反映在结论也进入 finding 清单。
        risk_summary = dict(risk_summary)
        by_level = dict(risk_summary.get("by_level") or {})
        if not commercial_info.get("ok"):
            by_level["major"] = int(by_level.get("major") or 0) + 1
        risk_summary["by_level"] = by_level
        risk_summary["commercial_incomplete"] = not commercial_info.get("ok")
    quality = evaluate_project_quality(
        {"requirement": coverage["requirement"], "scoring": coverage["scoring"],
         "evidence": coverage["evidence"], "chapter": coverage["chapter"]},
        {**risks, "risk_summary": risk_summary},
        weights=weights,
        commercial_info=commercial_info,
    )

    findings = classify_quality_risks(risks)
    # 覆盖类维度也生成 finding（非硬性，level=info/major）
    if coverage["requirement"]["star_unconfirmed"]:
        pass  # 已在 classify_quality_risks 生成（★ 硬风险）
    if coverage["evidence"]["stale"]:
        findings.append(
            {
                "category": "来源追溯",
                "level": "major",
                "node_id": None,
                "requirement_id": None,
                "scoring_item_id": None,
                "title": "正文引用与事实台账不一致",
                "description": f"{coverage['evidence']['stale']} 处引用已过期（台账取值已变更）",
                "recommendation": "在正文中核对并更新相关事实引用",
                "evidence": {"stale": coverage["evidence"]["stale"]},
            }
        )
    if commercial_info is not None and not commercial_info.get("ok"):
        missing = commercial_info.get("missing") or []
        labels = "、".join(str(item.get("label") or item.get("field") or "") for item in missing[:6])
        findings.append(
            {
                "category": "商务完整性",
                "level": "major",
                "node_id": None,
                "requirement_id": None,
                "scoring_item_id": None,
                "title": "商务标必填项未完成",
                "description": (
                    f"已检查 {commercial_info.get('checked', 0)} 项，"
                    f"缺失 {len(missing)} 项：{labels or '详见商务标校验详情'}"
                ),
                "recommendation": "补齐商务标必填字段、确认已审批报价版本与分项报价明细后重新评估",
                "evidence": commercial_info,
            }
        )

    summary: dict[str, Any] = {
        "coverage": coverage,
        "recommendations": build_risk_recommendations(risk_summary),
        "note": note,
    }
    if commercial_info is not None:
        summary["commercial"] = commercial_info

    report = QualityReport(
        project_id=project_id,
        report_version=_next_version(db, project_id),
        overall_score=quality["overall_score"],
        grade=quality["grade"],
        verdict=quality["verdict"],
        dimensions=quality["dimensions"],
        summary=summary,
        risk_summary=risk_summary,
        created_by=created_by or "system",
        created_at=clock.utc_now(),
    )
    db.add(report)
    db.flush()

    for item in findings:
        db.add(
            QualityFinding(
                report_id=report.id,
                category=item.get("category", ""),
                level=item.get("level", "major"),
                node_id=item.get("node_id"),
                scoring_item_id=item.get("scoring_item_id"),
                requirement_id=item.get("requirement_id"),
                title=item.get("title", ""),
                description=item.get("description", ""),
                evidence=item.get("evidence", {}),
                recommendation=item.get("recommendation", ""),
                status="open",
            )
        )
    db.flush()

    record_audit(
        db,
        project_id,
        "quality.run",
        {
            "report_id": report.id,
            "report_version": report.report_version,
            "verdict": report.verdict,
            "overall_score": report.overall_score,
        },
        actor={"username": created_by or "system"},
    )
    return report


def load_report(db: Session, report_id: str) -> QualityReport | None:
    return db.get(QualityReport, report_id)


def list_reports(db: Session, project_id: str, limit: int = 20) -> list[QualityReport]:
    return list(
        db.scalars(
            select(QualityReport)
            .where(QualityReport.project_id == project_id)
            .order_by(QualityReport.report_version.desc())
            .limit(limit)
        )
    )


def report_payload(report: QualityReport, findings: list[QualityFinding] | None = None) -> dict[str, Any]:
    return {
        "id": report.id,
        "project_id": report.project_id,
        "report_version": report.report_version,
        "overall_score": report.overall_score,
        "grade": report.grade,
        "verdict": report.verdict,
        "dimensions": report.dimensions or {},
        "summary": report.summary or {},
        "risk_summary": report.risk_summary or {},
        "created_by": report.created_by,
        "created_at": clock.iso_beijing(report.created_at),
        "findings": [finding_payload(row) for row in (findings or [])],
    }


def finding_payload(row: QualityFinding) -> dict[str, Any]:
    return {
        "id": row.id,
        "report_id": row.report_id,
        "category": row.category,
        "level": row.level,
        "node_id": row.node_id,
        "scoring_item_id": row.scoring_item_id,
        "requirement_id": row.requirement_id,
        "title": row.title,
        "description": row.description,
        "evidence": row.evidence or {},
        "recommendation": row.recommendation,
        "status": row.status,
    }


def update_finding(db: Session, finding_id: str, status: str, note: str, actor: str = "system") -> QualityFinding | None:
    row = db.get(QualityFinding, finding_id)
    if row is None:
        return None
    row.status = status or "handled"
    evidence = dict(row.evidence or {})
    if note:
        evidence["resolution_note"] = note
    evidence["resolved_by"] = actor
    row.evidence = evidence
    record_audit(
        db,
        None,
        "quality.finding_update",
        {"finding_id": finding_id, "status": row.status, "note": note},
        actor={"username": actor},
    )
    return row
