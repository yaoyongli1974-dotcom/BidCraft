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
from ...evaluation.report import finding_for_project  # noqa: F401
from ...models import Job, QualityFinding, QualityReport  # noqa: F401
from ..schemas import QualityFindingUpdate, QualityRunRequest  # noqa: F401
from ..shared import (  # noqa: F401
    BATCH_STALE_SECONDS,
    Any,
    HTTPException,
    User,
    _cancel_stale_jobs,
    _project,
    audit,
    billing,
    current_user,
    jobqueue,
    jobs,
    session_scope,
)

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
        # 同步运行也要带回本次生成的 finding，和 /evaluation/latest 的口径一致。
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        return report_payload(report, findings=findings)


@router.post("/projects/{ref}/evaluation/run-async", status_code=202)
def evaluation_run_async(ref: str, payload: QualityRunRequest) -> dict[str, Any]:
    """后台生成质量报告；返回 job_id，用 /jobs/{id} 或 SSE 跟踪进度。"""
    from ..shared import _require_write

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        if _cancel_stale_jobs(
            db,
            ["evaluation"],
            BATCH_STALE_SECONDS,
            project_id=project.id,
            stale_message="质量评估任务超时中断，已允许重新提交",
        ) is not None:
            raise HTTPException(status_code=409, detail="已有质量评估任务在运行")
        user = current_user() or {}
        error = jobqueue.check_backpressure(db, user.get("id") or "")
        if error:
            raise HTTPException(status_code=429, detail=error)
        note = payload.note or ""
        job = Job(
            project_id=project.id,
            kind="evaluation",
            status="queued",
            message="质量评估排队中…",
            payload={
                "owner_id": user.get("id") or "",
                "note": note,
                "created_by": _actor(),
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        owner_row = db.get(User, user.get("id") or "")
        job.priority = billing.queue_priority(db, owner_row) if owner_row else 1
        priority = job.priority
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(db, project.id, "quality.run.async", {"job_id": job_id, "note": note})
    from ..shared import _run_evaluation_job

    jobqueue.submit(
        _run_evaluation_job,
        (job_id, project.id, note, _actor()),
        priority=priority,
        job_id=job_id,
    )
    return {"job_id": job_id}


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
        # IDOR 防护：finding → report → project 归属必须一致；跨项目一律 404，
        # 不暴露其它项目 finding 是否存在。Service 层再用 project_id 兜一次。
        found = finding_for_project(db, finding_id, project.id)
        if found is None:
            raise LookupError("质量问题不存在")
        row = update_finding(
            db,
            finding_id,
            payload.status,
            payload.note or "",
            _actor(),
            project_id=project.id,
        )
        if row is None:
            raise LookupError("质量问题不存在")
        # 审计使用真实归属项目（finding_for_project 已校验），不用 ref 猜测。
        audit(db, found[1].project_id, "quality.finding.http", {"finding_id": finding_id, "status": row.status})
        return finding_payload(row)
