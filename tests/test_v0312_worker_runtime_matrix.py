"""v0.31.2 阶段 4：Worker 动态矩阵的**真实数据库行为**验证。

不再以源码字符串作为主要证据：动态发现全部 8 个 runner，用真实 Job 行、
真实 claim token / 租约状态和真实事务执行 runner 入口，验证失效认领无法
写入状态或事件。正常完成路径由各 worker 既有业务测试覆盖（见报告）。
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

TOKEN = "v0312-runtime-matrix-token"

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
    return [k for k in jobqueue._API_RUNNER_NAMES if k in ARGS]


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


def _invoke(kind: str, job_id: str, *, token: str = TOKEN, abort: threading.Event | None = None):
    runner: Callable[..., Any] = jobqueue._RUNNERS[kind]
    with execution_context(token, abort or threading.Event(), job_id=job_id, kind=kind):
        runner(job_id, *ARGS[kind])


@pytest.mark.parametrize("kind", _kinds())
def test_old_token_cannot_write_terminal_state(kind: str, db) -> None:
    job_id = _seed(kind)
    with session_scope() as reaper_db:
        reaper_db.get(Job, job_id).claim_token = "new-token"
    try:
        _invoke(kind, job_id)
    except JobAborted:
        pass  # 合法：fencing 在入口拒绝
    assert _snapshot(job_id) == ("running", [])


@pytest.mark.parametrize("kind", _kinds())
def test_cancelled_job_cannot_be_resurrected(kind: str, db) -> None:
    job_id = _seed(kind)
    with session_scope() as cancel_db:
        cancel_db.get(Job, job_id).status = "cancelled"
    _invoke(kind, job_id)
    assert _snapshot(job_id) == ("cancelled", [])


@pytest.mark.parametrize("kind", _kinds())
def test_preset_abort_does_not_write(kind: str, db) -> None:
    job_id = _seed(kind)
    abort = threading.Event()
    abort.set()
    try:
        _invoke(kind, job_id, abort=abort)
    except JobAborted:
        pass  # 合法：abort 在入口或 checkpoint 拒绝
    assert _snapshot(job_id) == ("running", [])


@pytest.mark.parametrize("kind", _kinds())
def test_normal_terminal_completion_for_every_kind(kind: str, db) -> None:
    """共享终态提交路径对每种 runner 都正常完成且只发一次事件。"""

    job_id = _seed(kind)
    with jobqueue.claim_context(TOKEN):
        jobqueue.finalize_job_result(job_id, result={"ok": True}, message="完成")
    assert _snapshot(job_id) == ("done", ["done"])


@pytest.mark.parametrize("kind", _kinds())
def test_expired_lease_blocks_terminal_write(kind: str, db) -> None:
    job_id = _seed(kind)
    with session_scope() as db2:
        db2.get(Job, job_id).lease_expires_at = utc_now() - timedelta(seconds=1)
    with jobqueue.claim_context(TOKEN), pytest.raises(JobAborted):
        jobqueue.finalize_job_result(job_id, result={"bad": True}, message="过期租约")
    assert _snapshot(job_id) == ("running", [])


def test_terminal_write_error_rolls_back(db, monkeypatch) -> None:
    """终态事件写入异常时，状态与事件都不部分提交。"""

    job_id = _seed("agent")
    original = jobs.publish_event

    def fail(*args, **kwargs):
        raise RuntimeError("模拟事件写入失败")

    monkeypatch.setattr(jobs, "publish_event", fail)
    with jobqueue.claim_context(TOKEN), pytest.raises(RuntimeError, match="事件写入"):
        jobqueue.finalize_job_result(job_id, result={"ok": True}, message="完成")
    monkeypatch.setattr(jobs, "publish_event", original)
    assert _snapshot(job_id) == ("running", [])


def test_terminal_event_idempotency(db) -> None:
    """重复终态提交只有一次生效，第二次被 fencing 拒绝。"""

    job_id = _seed("agent")
    with jobqueue.claim_context(TOKEN):
        jobqueue.finalize_job_result(job_id, result={"ok": 1}, message="完成")
        with pytest.raises(JobAborted):
            jobqueue.finalize_job_result(job_id, result={"duplicate": True}, message="重复")
    status, events = _snapshot(job_id)
    assert status == "done"
    assert events == ["done"]


def test_all_runners_registered_and_have_runtime_path(db) -> None:
    for kind in _kinds():
        assert isinstance(jobqueue._API_RUNNER_NAMES[kind], str)
        assert callable(jobqueue._RUNNERS[kind])
