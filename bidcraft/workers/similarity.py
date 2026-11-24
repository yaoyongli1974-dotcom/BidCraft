"""多维查重后台任务。

HTTP 层只创建 queued 报告与 Job；这里承担 O(n²) 的段落两两比对，完成后把
结果写回同一个 ``SimilarityReport``。所有参数来自 Job.payload，因此无需修改
jobqueue 的 ``_rebuild_args``（未知 kind 默认只传 job_id）。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobqueue, jobs
from ..db import session_scope
from ..jobqueue import JobAborted, fenced_job
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

        result: dict[str, Any] | None = None
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
            # Re-check the claim before this session commits report/hits/audit.
            # The reaper may have reclaimed the lease while the comparison ran;
            # raising here rolls the whole unit back (no orphaned results).
            jobqueue.assert_claim_current(job_id)

        if result is not None:
            # Use a FRESH session for the terminal write: fenced_job must read
            # the current claim token from the database, not the stale
            # identity-mapped Job loaded by the work session above.
            with session_scope() as done_db:
                jobqueue.assert_claim_current(job_id)
                row = fenced_job(done_db, job_id)
                if row is not None:
                    row.status = "done"
                    row.progress = 100
                    row.message = f"查重完成：{result['hits']} 条命中"
                    row.result = result
                    done_db.flush()
                    jobs.publish_event(
                        job_id,
                        "done",
                        {**jobs.job_payload(row), "report": result},
                        db=done_db,
                    )
    except JobAborted:
        # 租约失效/服务关闭：保持 running，由 reaper 重新排队后重试；不写
        # failed、不发布失败事件，也不要碰报告/命中。
        logger.info("查重任务 %s 已中止，等待重新排队", job_id)
    except Exception as exc:  # noqa: BLE001 - 失败原因落到 Job.result 供 UI 展示
        logger.exception("查重任务 %s 失败", job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "多维查重失败，详情见服务日志", str(exc)[:1000])
