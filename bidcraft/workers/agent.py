"""Agent 编排后台任务。

工作流的状态机、DAG 派发、审批闸门都在 :mod:`bidcraft.orchestration`。
这里只负责 Job 行/SSE 编排：fencing 校验、进度回报、终态落账。

审批挂起（``waiting_approval``）不是失败：Job 以 ``done`` 结束，工作流本身
停在 waiting_approval，审批通过后由路由重新 schedule 一个 agent Job 继续。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

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
from ..models import Job, User
from ..orchestration import run_workflow as _drive_workflow
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.workers")


def _terminal_job(job_id: str, status: str, result: dict[str, Any], message: str) -> None:
    # Terminal write: the fencing UPDATE and event share one transaction, so a
    # reclaimed lease cannot slip in between the check and the commit.
    finalize_job_result(job_id, status=status, result=result, message=message)


def _actor_from_job(db, job: Job) -> dict[str, Any] | None:
    """从 Job.payload 的 owner_id/user_id 还原真实提交人；找不到返回 None。

    ``owner_id`` 是用户 id（API schedule_workflow 写入），``user_id`` 历史上
    是用户名；两者都查不到时保持匿名，写工具随后会 fail closed。
    """

    payload = job.payload or {}
    owner_id = str(payload.get("owner_id") or "").strip()
    login = str(payload.get("user_id") or "").strip()
    user = db.get(User, owner_id) if owner_id else None
    if user is None and login:
        user = db.scalar(select(User).where(User.username == login))
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name or user.username,
    }


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
            if job.status != "running":
                return
            actor = _actor_from_job(db, job)
            job.status = "running"
            job.message = "Agent 工作流启动中…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        result = _drive_workflow(
            run_id, job_id=job_id, actor=actor, progress=on_progress
        )
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
        log_job_failure(logger)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, "Agent 工作流执行失败", str(exc))
