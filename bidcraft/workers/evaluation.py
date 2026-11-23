"""质量评估后台任务：在 Job 中执行 build_quality_report 并回报报告索引。

同步的 ``POST /projects/{ref}/evaluation/run`` 保持兼容；本 runner 服务于
``POST /projects/{ref}/evaluation/run-async``，让长报表生成不再占用请求线程。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobs
from ..db import session_scope
from ..evaluation import build_quality_report
from ..jobqueue import fenced_job
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.api")


def _done(job_id: str, result: dict[str, Any], message: str) -> None:
    with session_scope() as db:
        job = fenced_job(db, job_id)
        if job is None:
            return
        job.status = "done"
        job.progress = 100
        job.message = message
        job.result = result
        jobs.publish_event(
            job_id,
            "done",
            {**jobs.job_payload(job), "result": result},
            db=db,
        )
        db.flush()


def _run_evaluation_job(
    job_id: str, project_id: str, note: str = "", created_by: str = ""
) -> None:
    """生成一份质量报告并写入 Job.result（report_id / report_version）。"""
    try:
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            job.status = "running"
            job.progress = 20
            job.message = "质量评估生成中…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        with session_scope() as db:
            report = build_quality_report(
                db, project_id, created_by=created_by or "system", note=note or ""
            )
            result = {
                "report_id": report.id,
                "report_version": report.report_version,
                "overall_score": report.overall_score,
                "grade": report.grade,
                "verdict": report.verdict,
            }

        _done(job_id, result, f"已生成质量报告 v{result['report_version']}")
    except Exception as exc:  # noqa: BLE001 - runner 自己落失败状态
        logger.exception("质量评估任务失败 job=%s", job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "质量评估生成失败", str(exc))
