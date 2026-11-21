"""任务队列可靠性：幂等账本、中止信号与优雅退出。

这些用例针对验收标准的两个残余缺口：
- 租约过期/心跳失败后旧 worker 不得继续产生副作用（中止信号）；
- 服务重启/租约回收后重跑不得重复已完成的单元（幂等账本）。

fencing（claim_token）、启动恢复、独立 reaper、API/Worker 分离在
``test_jobqueue.py`` / ``test_hardening.py`` 覆盖，这里只测新增部分。
"""

from __future__ import annotations

import threading
import time

import pytest

from bidcraft import jobqueue
from bidcraft.db import session_scope
from bidcraft.jobqueue import (
    JobAborted,
    claim_context,
    current_claim_token,
    execution_context,
    is_shutdown_requested,
    mark_step_done,
    raise_if_aborted,
    step_status,
    workers_running,
)
from bidcraft.models import Job


def _seed_job(*, status: str = "running", token: str = "tok-1") -> str:
    with session_scope() as session:
        job = Job(kind="generate", status=status, claim_token=token)
        session.add(job)
        session.flush()
        return job.id


# --------------------------------------------------------------- 幂等账本


def test_step_ledger_survives_reclaim_and_skips_done_units(db):
    """同一单元被不同认领令牌执行两次，副作用只记一次。"""

    job_id = _seed_job(token="tok-A")
    side_effects: list[str] = []

    def run_attempt(token: str) -> None:
        with claim_context(token), session_scope() as session:
            for step in ("chapter:1", "chapter:2", "chapter:3"):
                if step_status(session, job_id, step):
                    continue
                side_effects.append(step)  # 副作用（真环境是模型调用/章节写入）
                mark_step_done(session, job_id, step, current_claim_token())

    run_attempt("tok-A")
    assert side_effects == ["chapter:1", "chapter:2", "chapter:3"]

    # 租约过期 → reaper 回收 → 新 worker 以新令牌认领并重跑。
    with session_scope() as session:
        session.get(Job, job_id).claim_token = "tok-B"
        session.flush()
    run_attempt("tok-B")
    assert side_effects == ["chapter:1", "chapter:2", "chapter:3"]  # 没有重复


def test_mark_step_done_rejects_stale_token(db):
    job_id = _seed_job(token="tok-current")
    with claim_context("tok-stale"), session_scope() as session:
        mark_step_done(session, job_id, "chapter:1", "tok-stale")
    with claim_context("tok-current"), session_scope() as session:
        assert step_status(session, job_id, "chapter:1") is False
        mark_step_done(session, job_id, "chapter:1", "tok-current")
        assert step_status(session, job_id, "chapter:1") is True


def test_step_status_returns_none_without_claim(db):
    job_id = _seed_job()
    with session_scope() as session:  # 无 claim_context
        assert step_status(session, job_id, "chapter:1") is None


# --------------------------------------------------------------- 中止信号


def test_heartbeat_loss_sets_abort_event(db, monkeypatch):
    """租约被回收后心跳停止续租并设置中止信号。"""

    job_id = _seed_job(token="tok-A")
    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.05)
    monkeypatch.setattr(jobqueue, "_lease_seconds", lambda: 30)
    stop = threading.Event()
    abort = threading.Event()
    hb = threading.Thread(
        target=jobqueue._heartbeat, args=(job_id, stop, "tok-A", abort), daemon=True
    )
    hb.start()
    try:
        time.sleep(0.02)
        with session_scope() as session:  # 模拟另一个 worker 认领（令牌改变）
            session.get(Job, job_id).claim_token = "tok-B"
            session.flush()
        deadline = time.monotonic() + 2
        while not abort.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert abort.is_set(), "租约丢失后必须设置中止信号"
    finally:
        stop.set()
        hb.join(timeout=2)


def test_raise_if_aborted_on_shutdown(db):
    jobqueue._reset()
    abort = threading.Event()
    with execution_context("tok-1", abort):
        raise_if_aborted()  # 尚未中止
        abort.set()
        with pytest.raises(JobAborted):
            raise_if_aborted()
        jobqueue.request_shutdown()
        try:
            with pytest.raises(JobAborted):
                raise_if_aborted()
        finally:
            jobqueue._reset()


def test_job_aborted_does_not_mark_failed(db, monkeypatch):
    """runner 抛 JobAborted 时任务被退回排队（而非 failed）。"""

    job_id = _seed_job(token="tok-1")
    calls: list[str] = []

    def aborted_runner(job_id_arg: str, *args) -> None:
        calls.append(job_id_arg)
        raise JobAborted("测试中止")

    monkeypatch.setattr(jobqueue, "_runner_for", lambda kind: aborted_runner)

    with session_scope() as session:
        jobqueue._execute(session.get(Job, job_id))
    with session_scope() as session:
        job = session.get(Job, job_id)
        # finalize 把它退回 queued（而非 failed）——中止不是业务失败。
        assert job.status in {"queued", "running"}
        assert job.result != {"error": "任务执行异常"}
    assert calls == [job_id]


# --------------------------------------------------------------- 优雅退出


def test_request_shutdown_stops_workers(db, monkeypatch):
    monkeypatch.setattr(jobqueue, "_max_workers", lambda: 2)
    monkeypatch.setattr(jobqueue, "_REAP_INTERVAL", 60)
    monkeypatch.setattr(jobqueue, "_IDLE_WAIT", 0.05)
    jobqueue._reset()
    assert not workers_running()
    jobqueue.start_workers()
    assert workers_running()
    assert jobqueue.is_shutdown_requested() is False

    jobqueue.request_shutdown()
    assert is_shutdown_requested() is True
    jobqueue.wait_for_workers(timeout=3)
    assert all(not thread.is_alive() for thread in jobqueue._worker_threads)
    jobqueue._reset()
