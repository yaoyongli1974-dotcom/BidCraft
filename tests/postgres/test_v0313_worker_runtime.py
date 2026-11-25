"""v0.31.3 阶段 2：八类 Worker 在真实 PostgreSQL 上的运行时矩阵。

不使用源码检查作为证据：动态发现全部 runner，在真实 Job/JobEvent 行上以真实
claim token、租约与事务调用 runner 或共享终态路径，验证失效认领/已取消/预置
abort 不产生写入，正常终态只发一次事件。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

import bidcraft.workers  # noqa: F401 - 注册 runner
from bidcraft import jobqueue, jobs
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.jobqueue import JobAborted, execution_context
from bidcraft.models import Job, JobEvent

TOKEN = "v0313-pg-worker-token"
ARGS: dict[str, tuple[Any, ...]] = {
    "agent": ("run-1",),
    "batch_check": ([],),
    "batch_export": ([], "xlsx"),
    "evaluation": ("", "", ""),
    "generate": ("", False),
    "images": ("", []),
    "kb_reindex": ("kb-1",),
    "similarity": (),
}


def _kinds() -> list[str]:
    return [kind for kind in jobqueue._API_RUNNER_NAMES if kind in ARGS]


def _seed(kind: str) -> str:
    with session_scope() as db:
        row = Job(
            kind=kind,
            status="running",
            claim_token=TOKEN,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=300),
            attempts=1,
        )
        db.add(row)
        db.flush()
        return row.id


def _snapshot(job_id: str) -> tuple[str, list[str]]:
    with session_scope() as db:
        row = db.get(Job, job_id)
        events = list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
        return str(row.status), events


def _invoke(kind: str, job_id: str, *, token: str = TOKEN,
            abort: threading.Event | None = None):
    runner: Callable[..., Any] = jobqueue._RUNNERS[kind]
    with execution_context(token, abort or threading.Event(), job_id=job_id, kind=kind):
        runner(job_id, *ARGS[kind])


@pytest.mark.parametrize("kind", _kinds())
def test_all_runners_registered_on_pg(pg_engine, kind):
    assert pg_engine.dialect.name == "postgresql"
    assert isinstance(jobqueue._API_RUNNER_NAMES[kind], str)
    assert callable(jobqueue._RUNNERS[kind])


@pytest.mark.parametrize("kind", _kinds())
def test_old_token_cannot_write_terminal_state(pg_engine, kind):
    job_id = _seed(kind)
    with session_scope() as reaper_db:
        reaper_db.get(Job, job_id).claim_token = "new-token"
    try:
        _invoke(kind, job_id)
    except JobAborted:
        pass  # 合法：入口 fencing 拒绝
    assert _snapshot(job_id) == ("running", [])


@pytest.mark.parametrize("kind", _kinds())
def test_cancelled_job_cannot_be_resurrected(pg_engine, kind):
    job_id = _seed(kind)
    with session_scope() as cancel_db:
        cancel_db.get(Job, job_id).status = "cancelled"
    _invoke(kind, job_id)
    assert _snapshot(job_id) == ("cancelled", [])


@pytest.mark.parametrize("kind", _kinds())
def test_preset_abort_does_not_write(pg_engine, kind):
    job_id = _seed(kind)
    abort = threading.Event()
    abort.set()
    try:
        _invoke(kind, job_id, abort=abort)
    except JobAborted:
        pass  # 合法：入口/检查点中止
    assert _snapshot(job_id) == ("running", [])


@pytest.mark.parametrize("kind", _kinds())
def test_expired_lease_blocks_terminal_write(pg_engine, kind):
    job_id = _seed(kind)
    with session_scope() as stale_db:
        stale_db.get(Job, job_id).lease_expires_at = utc_now() - timedelta(seconds=1)
    with jobqueue.claim_context(TOKEN), pytest.raises(JobAborted):
        jobqueue.finalize_job_result(job_id, result={"bad": True}, message="过期租约")
    assert _snapshot(job_id) == ("running", [])


@pytest.mark.parametrize("kind", _kinds())
def test_normal_terminal_completion_once(pg_engine, kind):
    job_id = _seed(kind)
    with jobqueue.claim_context(TOKEN):
        jobqueue.finalize_job_result(job_id, result={"ok": True}, message="完成")
    assert _snapshot(job_id) == ("done", ["done"])


@pytest.mark.parametrize("kind", _kinds())
def test_duplicate_terminal_event_is_idempotent(pg_engine, kind):
    job_id = _seed(kind)
    with jobqueue.claim_context(TOKEN):
        jobqueue.finalize_job_result(job_id, result={"ok": 1}, message="完成")
        with pytest.raises(JobAborted):
            jobqueue.finalize_job_result(job_id, result={"duplicate": True}, message="重复")
    assert _snapshot(job_id) == ("done", ["done"])


def test_terminal_write_error_rolls_back_on_pg(pg_engine, monkeypatch):
    job_id = _seed("agent")
    original = jobs.publish_event

    def fail(*args, **kwargs):
        raise RuntimeError("模拟事件写入失败")

    monkeypatch.setattr(jobs, "publish_event", fail)
    with jobqueue.claim_context(TOKEN), pytest.raises(RuntimeError, match="事件写入"):
        jobqueue.finalize_job_result(job_id, result={"ok": True}, message="完成")
    monkeypatch.setattr(jobs, "publish_event", original)
    assert _snapshot(job_id) == ("running", [])
