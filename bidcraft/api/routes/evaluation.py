"""API routes: /evaluation（对标易标第四批 v0.27.0）。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from sqlalchemy import select  # noqa: F401

from ...evaluation import (  # noqa: F401
    build_quality_report,
    finding_payload,
    list_reports,
    load_report,
    report_payload,
    update_finding,
)
from ...models import QualityFinding  # noqa: F401
from ..schemas import QualityFindingUpdate, QualityRunRequest  # noqa: F401
from ..shared import Any, _project, audit, current_user, session_scope  # noqa: F401

router = APIRouter()


def _actor() -> str:
    return str((current_user() or {}).get("username") or "system")


@router.post("/projects/{ref}/evaluation/run", status_code=201)
def evaluation_run(ref: str, payload: QualityRunRequest) -> dict[str, Any]:
    """生成一份质量报告快照（六维评分 + 硬性风险一票否决）。"""
    from ..shared import _require_write

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        report = build_quality_report(
            db, project.id, created_by=_actor(), note=payload.note or ""
        )
        audit(db, project.id, "quality.run.http", {"report_id": report.id, "version": report.report_version})
        return report_payload(report, findings=[])


@router.get("/projects/{ref}/evaluation")
def evaluation_latest(ref: str) -> dict[str, Any]:
    """最新一份质量报告（含 findings）。"""
    with session_scope() as db:
        project, _role = _project(db, ref)
        reports = list_reports(db, project.id, limit=1)
        if not reports:
            return {"report": None}
        report = reports[0]
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        return {"report": report_payload(report, findings=findings)}


@router.get("/projects/{ref}/evaluation/reports")
def evaluation_reports(ref: str, limit: int = 20) -> list[dict[str, Any]]:
    """历史报告列表（版本倒序，快照不可变）。"""
    with session_scope() as db:
        project, _role = _project(db, ref)
        return [report_payload(row, findings=[]) for row in list_reports(db, project.id, limit=limit)]


@router.get("/projects/{ref}/evaluation/reports/{report_id}")
def evaluation_report_detail(ref: str, report_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        report = load_report(db, report_id)
        if report is None or report.project_id != project.id:
            raise LookupError("质量报告不存在")
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        return report_payload(report, findings=findings)


@router.get("/projects/{ref}/evaluation/reports/{report_id}/findings")
def evaluation_findings(ref: str, report_id: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        report = load_report(db, report_id)
        if report is None or report.project_id != project.id:
            raise LookupError("质量报告不存在")
        rows = db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        return [finding_payload(row) for row in rows]


@router.patch("/projects/{ref}/evaluation/findings/{finding_id}")
def evaluation_finding_update(
    ref: str, finding_id: str, payload: QualityFindingUpdate
) -> dict[str, Any]:
    """人工处置质量问题（记录处置人/意见，不改原始报告快照）。"""
    from ..shared import _require_write

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        row = update_finding(db, finding_id, payload.status, payload.note or "", _actor())
        if row is None:
            raise LookupError("质量问题不存在")
        audit(db, project.id, "quality.finding.http", {"finding_id": finding_id, "status": row.status})
        return finding_payload(row)
