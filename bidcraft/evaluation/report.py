"""质量报告编排：聚合各维度结果 → 评分 → 落库不可变快照 + finding 明细。

报告快照不可变：每次运行 report_version = 当前最大 + 1；已生成的报告/发现
不因后续核对而改写（finding 的 status 是可人工处置的动作字段，单独更新）。
"""

from __future__ import annotations

import re
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
from .quality_metrics import collect_quality_metrics
from .risk import (
    build_risk_recommendations,
    calculate_risk_summary,
    classify_quality_risks,
    collect_project_risks,
)
from .scorer import evaluate_project_quality
from .tender_quality import collect_tender_quality

#: 报告摘要里的后台 Job 标识，用于重跑时复用同一份报告（避免重复版本）。
EVALUATION_JOB_ID_KEY = "evaluation_job_id"


def _next_version(db: Session, project_id: str) -> int:
    current = db.scalar(
        select(QualityReport.report_version)
        .where(QualityReport.project_id == project_id)
        .order_by(QualityReport.report_version.desc())
    )
    return (current or 0) + 1


#: 已处置状态（含界面中文写法）；重新评估时这些处置结论必须被沿用。
_DISPOSED_FINDING_STATUSES = frozenset(
    {"handled", "ignored", "resolved", "closed", "已处理", "已忽略", "已解决", "已关闭"}
)
#: 随 finding 一起结转的人工处置字段（存在 evidence JSON 里）。
_DISPOSITION_FIELDS = ("resolution_note", "resolved_by", "resolved_at")


def _finding_keys(
    node_id: Any, category: Any, title: Any, evidence: dict[str, Any] | None
) -> set[tuple[str, str, str]]:
    """finding 的稳定键：node_id + category + title（有指纹则同时按指纹匹配）。"""

    def normalized(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).strip().lower()

    base = (str(node_id or ""), str(category or ""))
    keys = {(*base, normalized(title))}
    source = (evidence or {}).get("fingerprint") or (evidence or {}).get("stable_key")
    if source:
        keys.add((*base, normalized(source)))
    return keys


def _prior_dispositions(
    db: Session, project_id: str
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """快照本项目历史报告里已处置 finding 的结论（新报告版本优先）。"""

    rows = db.execute(
        select(QualityFinding, QualityReport)
        .join(QualityReport, QualityReport.id == QualityFinding.report_id)
        .where(QualityReport.project_id == project_id)
        .order_by(QualityReport.report_version.desc(), QualityFinding.id.desc())
    ).all()
    prior: dict[tuple[str, str, str], dict[str, Any]] = {}
    for finding, _report in rows:
        if str(finding.status or "").strip().lower() not in _DISPOSED_FINDING_STATUSES:
            continue
        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        carried: dict[str, Any] = {
            "status": finding.status,
            "disposition_from_finding_id": finding.id,
        }
        for field in _DISPOSITION_FIELDS:
            value = evidence.get(field)
            if value not in (None, ""):
                carried[field] = value
        for key in _finding_keys(finding.node_id, finding.category, finding.title, evidence):
            prior.setdefault(key, carried)
    return prior


def find_report_for_job(
    db: Session, project_id: str, job_id: str
) -> QualityReport | None:
    """Find the report already produced by an evaluation Job (retry-safe)."""

    if not job_id:
        return None
    rows = db.scalars(
        select(QualityReport)
        .where(QualityReport.project_id == project_id)
        .order_by(QualityReport.report_version.desc())
    )
    for row in rows:
        if (row.summary or {}).get(EVALUATION_JOB_ID_KEY) == job_id:
            return row
    return None


def build_quality_report(
    db: Session,
    project_id: str,
    *,
    created_by: str = "",
    note: str = "",
    weights: dict[str, float] | None = None,
    commercial_info: dict[str, Any] | None = None,
    job_id: str = "",
) -> QualityReport:
    """生成并落库一份质量报告（含 finding 明细）。与调用方同事务。

    传入 ``job_id`` 时先查同 Job 已提交的报告；命中则直接返回，使后台任务在
    「报告已落库但 Job 未标记完成」的崩溃重试中不会生成第二个版本。
    """

    if job_id:
        existing = find_report_for_job(db, project_id, job_id)
        if existing is not None:
            return existing
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

    # 重新评估会生成全新的 finding 行；人工处置状态/意见按稳定键结转，
    # 不因「报告快照不可变」而丢失（原处置审计仍指向旧 finding id）。
    prior_dispositions = _prior_dispositions(db, project_id)
    summary: dict[str, Any] = {
        "coverage": coverage,
        "recommendations": build_risk_recommendations(risk_summary),
        "note": note,
    }
    if job_id:
        summary[EVALUATION_JOB_ID_KEY] = job_id
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
        evidence = dict(item.get("evidence") or {})
        status = "open"
        keys = _finding_keys(
            item.get("node_id"), item.get("category"), item.get("title"), evidence
        )
        carried = next(
            (prior_dispositions[key] for key in sorted(keys) if key in prior_dispositions),
            None,
        )
        if carried is not None:
            status = carried["status"]
            for field in (*_DISPOSITION_FIELDS, "disposition_from_finding_id"):
                if field in carried:
                    evidence[field] = carried[field]
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
                evidence=evidence,
                recommendation=item.get("recommendation", ""),
                status=status,
            )
        )
    db.flush()

    # 指标在本报告的 finding 落库后采集（自身的 finding 也计入密度）；
    # 重新赋值整个 summary 以触发 JSON 字段变更追踪，保留全部既有键。
    report.summary = {
        **summary,
        "metrics": collect_quality_metrics(db, project_id),
        "tender_quality": collect_tender_quality(),
    }
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


#: finding 人工处置的合法状态（API schema 在此设了 Literal 校验，这里再兜一层）。
FINDING_STATUSES = ("open", "handled", "ignored")


def finding_for_project(
    db: Session, finding_id: str, project_id: str | None = None
) -> tuple[QualityFinding, QualityReport] | None:
    """Resolve a finding together with its report.

    ``project_id`` is authoritative when supplied: a finding that belongs to a
    different project is indistinguishable from a missing one (no cross-project
    data leak).  The joined report also supplies the real project id for audit.
    """

    result = db.execute(
        select(QualityFinding, QualityReport)
        .join(QualityReport, QualityReport.id == QualityFinding.report_id)
        .where(QualityFinding.id == finding_id)
    ).one_or_none()
    if result is None:
        return None
    row, report = result
    if project_id is not None and report.project_id != project_id:
        return None
    return row, report


def update_finding(
    db: Session,
    finding_id: str,
    status: str,
    note: str,
    actor: str = "system",
    *,
    project_id: str | None = None,
) -> QualityFinding | None:
    """Update one finding's disposition, scoped to its owning project.

    Returns ``None`` for an unknown finding or a cross-project mismatch.  The
    audit row always carries the real ``QualityReport.project_id`` (never a
    caller-supplied or null value).
    """

    if status is not None and status not in FINDING_STATUSES:
        raise ValueError(f"非法的质量问题状态: {status}")
    found = finding_for_project(db, finding_id, project_id)
    if found is None:
        return None
    row, report = found
    row.status = status or "handled"
    evidence = dict(row.evidence or {})
    if note:
        evidence["resolution_note"] = note
    evidence["resolved_by"] = actor
    row.evidence = evidence
    db.flush()
    record_audit(
        db,
        report.project_id,
        "quality.finding_update",
        {
            "finding_id": finding_id,
            "report_id": report.id,
            "status": row.status,
            "note": note,
        },
        actor={"username": actor},
    )
    return row
