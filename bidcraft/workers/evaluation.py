"""质量评估后台任务：在 Job 中执行 build_quality_report 并回报报告索引。

同步的 ``POST /projects/{ref}/evaluation/run`` 保持兼容；本 runner 服务于
``POST /projects/{ref}/evaluation/run-async``，让长报表生成不再占用请求线程。

报告落库与 ``job.payload/result`` 的 report_id 写入共用同一事务；若进程在
「报告已提交、Job 未标记完成」之间崩溃，重跑会读取 report_id 直接收尾，
不会再生成第二个报告版本。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobs
from ..db import session_scope
from ..evaluation import build_quality_report, find_report_for_job
from ..jobqueue import (
    JobAborted,
    assert_claim_current,
    fenced_job,
    finalize_job_result,
    log_job_failure,
    raise_if_aborted,
)
from ..models import QualityReport
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.workers")


def _report_result(report: QualityReport) -> dict[str, Any]:
    return {
        "report_id": report.id,
        "report_version": report.report_version,
        "overall_score": report.overall_score,
        "grade": report.grade,
        "verdict": report.verdict,
    }


def _done(job_id: str, result: dict[str, Any], message: str) -> None:
    raise_if_aborted()
    finalize_job_result(job_id, result=result, message=message)


def _run_evaluation_job(
    job_id: str, project_id: str, note: str = "", created_by: str = ""
) -> None:
    """Generate one report per Job and write its index into ``Job.result``."""
    try:
        raise_if_aborted()
        assert_claim_current(job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            if job.status != "running":
                return
            job.status = "running"
            job.progress = 20
            job.message = "质量评估生成中…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        result: dict[str, Any] = {}
        # 报告生成是重操作：开始前再检查一次租约/关闭信号。
        raise_if_aborted()
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            known_id = str(
                (job.result or {}).get("report_id")
                or (job.payload or {}).get("report_id")
                or ""
            )
            report = db.get(QualityReport, known_id) if known_id else None
            if report is None or report.project_id != project_id:
                report = find_report_for_job(db, project_id, job_id)
            if report is None:
                report = build_quality_report(
                    db,
                    project_id,
                    created_by=created_by or "system",
                    note=note or "",
                    job_id=job_id,
                )
            result = dict(job.result or {}) if (job.result or {}).get("report_id") == report.id else {}
            result = {**result, **_report_result(report)}
            # Same transaction as the report insert: a crash cannot leave a
            # committed report with no pointer in the Job.
            job.result = result
            job.payload = {**(job.payload or {}), "report_id": report.id}
            db.flush()

        _done(job_id, result, f"已生成质量报告 v{result['report_version']}")
    except JobAborted:
        # 中止不是业务失败：保持 running，由 reaper 重新排队。
        logger.info("质量评估任务 %s 已中止，等待重新排队", job_id)
    except Exception as exc:  # noqa: BLE001 - runner 自己落失败状态
        log_job_failure(logger)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "质量评估生成失败", str(exc))
