"""多维查重后台任务。

HTTP 层只创建 queued 报告与 Job；这里承担 O(n²) 的段落两两比对，完成后把
结果写回同一个 ``SimilarityReport``。所有参数来自 Job.payload，因此无需修改
jobqueue 的 ``_rebuild_args``（未知 kind 默认只传 job_id）。
"""

from __future__ import annotations

import logging

from .. import jobs
from ..db import session_scope
from ..jobqueue import fenced_job
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.api")


def _run_similarity_job(job_id: str) -> None:
    """Background worker: run the similarity check for one queued job."""

    from ..models import Job
    from ..similarity import service as similarity_service

    try:
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            job.status = "running"
            job.progress = 5
            job.message = "正在执行多维查重…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            payload = dict(job.payload or {})
            report = similarity_service.run_similarity(
                db,
                str(job.project_id or payload.get("project_id") or ""),
                scope=str(payload.get("scope") or "project"),
                mode=str(payload.get("mode") or "text"),
                threshold=float(payload.get("threshold") or 0.85),
                source_project_id=str(payload.get("source_project_id") or "") or None,
                created_by=str(payload.get("created_by") or ""),
                report_id=str(payload.get("report_id") or "") or None,
            )
            result = {
                "report_id": report.id,
                "status": report.status,
                "hits": int((report.stats or {}).get("hits") or 0),
            }
            job = fenced_job(db, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"查重完成：{result['hits']} 条命中"
                job.result = result
                db.flush()
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": result},
                    db=db,
                )
    except Exception as exc:  # noqa: BLE001 - 失败原因落到 Job.result 供 UI 展示
        logger.exception("查重任务 %s 失败", job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "多维查重失败，详情见服务日志", str(exc)[:1000])
