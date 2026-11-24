"""Agent 编排后台任务。

工作流的状态机、DAG 派发、审批闸门都在 :mod:`bidcraft.orchestration`。
这里只负责 Job 行/SSE 编排：fencing 校验、进度回报、终态落账。

审批挂起（``waiting_approval``）不是失败：Job 以 ``done`` 结束，工作流本身
停在 waiting_approval，审批通过后由路由重新 schedule 一个 agent Job 继续。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobs
from ..db import session_scope
from ..jobqueue import JobAborted, assert_claim_current, fenced_job, raise_if_aborted
from ..models import Job
from ..orchestration import run_workflow as _drive_workflow
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.api")


def _terminal_job(job_id: str, status: str, result: dict[str, Any], message: str) -> None:
    # Terminal write: re-read the token in a fresh session so a reclaimed lease
    # cannot be overwritten by a stale identity-mapped row.
    assert_claim_current(job_id)
    with session_scope() as db:
        job = fenced_job(db, job_id)
        if job is None:
            return
        job.status = status
        job.progress = 100
        job.message = message
        job.result = result
        jobs.publish_event(
            job_id,
            "done" if status == "done" else "failed",
            {**jobs.job_payload(job), "result": result},
            db=db,
        )
        db.flush()


def _run_agent_job(job_id: str, run_id: str) -> None:
    """Drive one WorkflowRun from its queued/running state to a terminal or gate."""

    def on_progress(payload: dict[str, Any]) -> None:
        raise_if_aborted()
        with session_scope() as db:
            job: Job | None = fenced_job(db, job_id)
            if job is None:
                return
            stats = payload.get("stats") or {}
            total = int(stats.get("tasks_total") or 0)
            done = int(stats.get("tasks_done") or 0)
            job.progress = min(95, int(done / total * 100)) if total else 20
            job.message = f"Agent 工作流执行中（{done}/{total}）…"
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)
            db.flush()

    try:
        raise_if_aborted()
        assert_claim_current(job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = "Agent 工作流启动中…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        result = _drive_workflow(run_id, job_id=job_id, progress=on_progress)
        raise_if_aborted()
        run = result.get("run") or {}
        status = str(run.get("status") or "")
        if status == "waiting_approval":
            _terminal_job(
                job_id,
                "done",
                {**result, "note": "工作流已挂起，等待人工审批"},
                "工作流已挂起，等待人工审批",
            )
        elif status == "failed":
            _terminal_job(job_id, "done", result, "工作流执行结束（失败，详见结果）")
        elif status == "cancelled":
            _terminal_job(job_id, "done", result, "工作流已取消")
        else:
            _terminal_job(job_id, "done", result, "Agent 工作流完成")
    except JobAborted:
        # 租约失效/优雅关闭：不写 failed、不退款，留给 reaper 重新排队。
        logger.info("Agent 工作流任务 %s 已中止，等待重新排队", job_id)
    except Exception as exc:  # noqa: BLE001 - job-level surface, never kill the worker
        logger.exception("Agent 工作流任务 %s 失败", job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "Agent 工作流执行失败", str(exc))
