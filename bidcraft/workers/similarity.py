"""多维查重后台任务。

HTTP 层只创建 queued 报告与 Job；这里承担 O(n²) 的段落两两比对，完成后把
结果写回同一个 ``SimilarityReport``。所有参数来自 Job.payload，因此无需修改
jobqueue 的 ``_rebuild_args``（未知 kind 默认只传 job_id）。
"""

from __future__ import annotations

import logging

from .. import jobqueue, jobs
from ..db import session_scope
from ..jobqueue import JobAborted, fenced_job
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.workers")


def _run_similarity_job(job_id: str) -> None:
    """Background worker: run the similarity check for one queued job.

    Computation and final persistence are deliberately separated.  The costly
    O(n²) comparison runs in a throwaway session without writing a final report.
    Only after it finishes do we enter ``commit_fenced_result``: a conditional
    UPDATE verifies the current claim token **inside the same transaction** that
    writes report/hits/audit and the Job terminal state.  A reclaimed/expired
    worker therefore cannot leave orphan results or overwrite a newer worker.
    """

    from ..models import Job
    from ..similarity import service as similarity_service

    try:
        jobqueue.raise_if_aborted()
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            job.status = "running"
            job.progress = 5
            job.message = "正在执行多维查重…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        jobqueue.raise_if_aborted()
        with session_scope() as work_db:
            job = work_db.get(Job, job_id)
            if job is None:
                return
            jobqueue.raise_if_aborted()
            payload = dict(job.payload or {})
            prepared = similarity_service.prepare_similarity_result(
                work_db,
                str(job.project_id or payload.get("project_id") or ""),
                scope=str(payload.get("scope") or "project"),
                mode=str(payload.get("mode") or "text"),
                threshold=float(payload.get("threshold") or 0.85),
                source_project_id=str(payload.get("source_project_id") or "") or None,
                created_by=str(payload.get("created_by") or ""),
                report_id=str(payload.get("report_id") or "") or None,
                abort_check=jobqueue.raise_if_aborted,
            )

        with jobqueue.commit_fenced_result(job_id) as (final_db, row):
            report = similarity_service.persist_similarity_result(
                final_db,
                prepared,
                report_id=str((row.payload or {}).get("report_id") or "") or None,
            )
            result = {
                "report_id": report.id,
                "status": report.status,
                "hits": int((report.stats or {}).get("hits") or 0),
            }
            row.status = "done"
            row.progress = 100
            row.message = f"查重完成：{result['hits']} 条命中"
            row.result = result
            final_db.flush()
            jobs.publish_event(
                job_id,
                "done",
                {**jobs.job_payload(row), "report": result},
                db=final_db,
            )
    except JobAborted:
        # 租约失效/服务关闭：保持 running，由 reaper 重新排队后重试；不写
        # failed、不发布失败事件，也不要碰报告/命中。
        logger.info("查重任务 %s 已中止，等待重新排队", job_id)
    except Exception as exc:  # noqa: BLE001 - 失败原因落到 Job.result 供 UI 展示
        jobqueue.log_job_failure(logger)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "多维查重失败，详情见服务日志", str(exc)[:1000])
