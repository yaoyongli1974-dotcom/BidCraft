"""批量合规核对后台任务。

核对逻辑在 :mod:`bidcraft.pipeline`；这里只负责 job 行/SSE 编排。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobs
from ..db import session_scope
from ..models import Job, Project
from ..pipeline import check_project
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.api")


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
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = f"正在批量核对（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, project_id in enumerate(project_ids, start=1):
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
            with session_scope() as db:
                job = db.get(Job, job_id)
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

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"核对完成：成功 {ok_count} / 失败 {failed_count}"
                job.result = {
                    "total": total,
                    "ok": ok_count,
                    "failed": failed_count,
                    "results": results,
                }
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("批量核对任务 %s 失败", job_id)
        failed_message = "批量核对失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)
