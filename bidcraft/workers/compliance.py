"""批量合规核对后台任务。

核对逻辑在 :mod:`bidcraft.pipeline`；这里只负责 job 行/SSE 编排。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobs
from ..db import session_scope
from ..jobqueue import (
    JobAborted,
    assert_claim_current,
    fenced_job,
    finalize_job_result,
    log_job_failure,
    raise_if_aborted,
)
from ..models import Project
from ..pipeline import check_project
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.workers")


def _run_batch_check_job(job_id: str, project_ids: list[str]) -> None:
    """Background worker: run ``check_project`` for each project in turn.

    Every item uses a short session so SQLite has a single writer between items;
    a failing project is recorded in ``results`` and the batch continues.
    """

    total = len(project_ids)
    results: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0
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
            job.message = f"正在批量核对（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, project_id in enumerate(project_ids, start=1):
            # 放在内层 except Exception 之外：中止不能被当成单个项目失败吞掉。
            raise_if_aborted()
            name = ""
            item: dict[str, Any]
            try:
                with session_scope() as db:
                    project = db.get(Project, project_id)
                    if project is None:
                        raise ValueError("项目不存在")
                    name = project.name
                    issues, _summary = check_project(db, project_id)
                    blocking = len([issue for issue in issues if issue.level == "blocking"])
                    item = {
                        "project_id": project_id,
                        "name": name,
                        "ok": True,
                        "issues": len(issues),
                        "blocking": blocking,
                    }
                ok_count += 1
            except Exception as exc:  # noqa: BLE001 - recorded per project
                item = {
                    "project_id": project_id,
                    "name": name,
                    "ok": False,
                    "error": str(exc),
                }
                failed_count += 1
            results.append(item)
            raise_if_aborted()
            with session_scope() as db:
                job = fenced_job(db, job_id)
                if job is None:
                    return
                progress = int(index * 100 / total) if total else 100
                job.progress = progress
                job.payload = {
                    **(job.payload or {}),
                    "done": index,
                    "total": total,
                    "ok": ok_count,
                    "failed": failed_count,
                    "results": results,
                }
                job.message = f"已核对 {index}/{total}：{name or project_id}"
                jobs.publish_event(
                    job_id,
                    "batch_item",
                    {
                        **item,
                        "index": index,
                        "total": total,
                        "progress": progress,
                        "done": index,
                        "ok_count": ok_count,
                        "failed_count": failed_count,
                    },
                    db=db,
                )

        raise_if_aborted()
        assert_claim_current(job_id)
        finalize_job_result(
            job_id,
            result={
                "total": total,
                "ok": ok_count,
                "failed": failed_count,
                "results": results,
            },
            message=f"核对完成：成功 {ok_count} / 失败 {failed_count}",
        )
    except JobAborted:
        # 中止不是业务失败：保持 running，由 reaper 重新排队。
        logger.info("批量核对任务 %s 已中止，等待重新排队", job_id)
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        log_job_failure(logger)
        failed_message = "批量核对失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)
