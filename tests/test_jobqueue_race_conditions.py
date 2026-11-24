"""v0.31 阶段 2：JobQueue / Reaper 并发与状态机治理测试。"""

from __future__ import annotations

import os
import random
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import Job, Project

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL") or ""


def _project() -> str:
    with session_scope() as db:
        row = Project(name="v031-queue-race", package="weakcurrent-cabling")
        db.add(row)
        db.flush()
        return row.id


def _running_job(*, lease_seconds: int = 300, attempts: int = 1, token: str = "tok") -> str:
    now = utc_now()
    with session_scope() as db:
        row = Job(
            project_id=_project(),
            kind="similarity",
            status="running",
            claim_token=token,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            worker_id="worker-a",
            attempts=attempts,
            result={"stale": True},
            progress=80,
            payload={"project_id": "p", "report_id": ""},
        )
        db.add(row)
        db.flush()
        return row.id


def _state(job_id: str) -> tuple[str, str | None, dict | None, int | None]:
    with session_scope() as db:
        row = db.get(Job, job_id)
        assert row is not None
        return (
            str(row.status),
            row.claim_token,
            row.result or None,
            int(row.progress or 0),
        )


def _event_count(job_id: str, kind: str) -> int:
    from bidcraft.models import JobEvent

    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(JobEvent)
                .where(JobEvent.job_id == job_id, JobEvent.kind == kind)
            )
            or 0
        )


def test_transition_job_enforces_state_machine(db):
    job_id = _running_job()
    with session_scope() as session:
        with pytest.raises(ValueError, match="非法任务状态转换"):
            jobqueue.transition_job(
                session,
                job_id,
                from_statuses=("running",),
                to_status="bogus",
            )
        done = jobqueue.transition_job(
            session,
            job_id,
            from_statuses=("running",),
            to_status="done",
            token="tok",
        )
        assert done is not None
        with pytest.raises(ValueError, match="非法任务状态转换"):
            jobqueue.transition_job(
                session,
                job_id,
                from_statuses=("done",),
                to_status="running",
            )
    assert _state(job_id)[0] == "done"


def test_requeue_clears_stale_result_progress_and_claim(db):
    job_id = _running_job(lease_seconds=-1)
    assert jobqueue.reap_expired() == (1, 0)
    status, token, result, progress = _state(job_id)
    assert status == "queued"
    assert token is None
    assert result is None
    assert progress == 0


def test_two_reapers_cannot_requeue_same_job_twice(db):
    job_id = _running_job(lease_seconds=-1)
    barrier = threading.Barrier(3)
    outcomes: list[tuple[int, int]] = []
    errors: list[str] = []

    def reap() -> None:
        barrier.wait()
        try:
            outcomes.append(jobqueue.reap_expired())
        except Exception as exc:  # noqa: BLE001 - surfaced by assertion
            errors.append(repr(exc))

    threads = [threading.Thread(target=reap) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert errors == []
    assert sum(requeued for requeued, _ in outcomes) == 1
    assert sum(failed for _, failed in outcomes) == 1
    assert _event_count(job_id, "queued") == 1


def test_50_rounds_two_reapers_only_one_wins(db):
    random.seed(20261124)
    for round_no in range(50):
        job_id = _running_job(lease_seconds=-1, token=f"tok-{round_no}")
        barrier = threading.Barrier(3)
        outcomes: list[tuple[int, int]] = []
        errors: list[str] = []

        def reap(barrier=barrier, outcomes=outcomes, errors=errors) -> None:
            barrier.wait()
            try:
                outcomes.append(jobqueue.reap_expired())
            except Exception as exc:  # noqa: BLE001 - surfaced by assertion
                errors.append(repr(exc))

        threads = [threading.Thread(target=reap) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), f"round {round_no} hung"
        assert errors == [], f"round {round_no}: {errors}"
        assert sum(requeued for requeued, _ in outcomes) == 1, round_no
        assert sum(failed for _, failed in outcomes) == 1, round_no
        assert _state(job_id)[0] == "queued"


def test_concurrent_set_failed_publishes_exactly_one_event(db):
    job_id = _running_job()
    barrier = threading.Barrier(3)
    wins: list[bool] = []
    errors: list[str] = []

    def fail() -> None:
        barrier.wait()
        try:
            with session_scope() as session:
                row = session.get(Job, job_id)
                assert row is not None
                wins.append(jobqueue._set_failed(session, row, "race"))
        except Exception as exc:  # noqa: BLE001 - surfaced by assertion
            errors.append(repr(exc))

    threads = [threading.Thread(target=fail) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert errors == []
    assert sorted(wins) == [False, True]
    assert _event_count(job_id, "failed") == 1
    assert _state(job_id)[0] == "failed"


class _OnceStop:
    """Heartbeat stop object: first wait proceeds, second one stops the loop."""

    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _interval: float) -> bool:
        self.calls += 1
        return self.calls > 1


def test_heartbeat_cannot_revive_expired_lease(db, monkeypatch):
    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.001)
    job_id = _running_job(lease_seconds=-1)
    before = utc_now() - timedelta(seconds=1)
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row is not None
        row.lease_expires_at = before
    abort = threading.Event()
    jobqueue._heartbeat(job_id, _OnceStop(), "tok", abort)
    assert abort.is_set()
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row is not None
        assert row.lease_expires_at == before
        assert row.status == "running"


def test_heartbeat_failure_sets_abort(db, monkeypatch):
    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.001)

    def broken_scope():
        raise RuntimeError("模拟心跳数据库故障")

    monkeypatch.setattr(jobqueue, "session_scope", broken_scope)
    abort = threading.Event()
    jobqueue._heartbeat("missing", _OnceStop(), "tok", abort)
    assert abort.is_set()


@pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL 才会运行真实 PostgreSQL 校验")
def test_postgresql_multi_reaper_optin(tmp_path, monkeypatch):
    from bidcraft.config import settings
    from bidcraft.db import init_db, reset_engine_for_tests

    home = tmp_path / ".bidcraft-pg-queue"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    reset_engine_for_tests(PG_URL)
    init_db()
    try:
        job_id = _running_job(lease_seconds=-1, token="pg-tok")
        barrier = threading.Barrier(3)
        outcomes: list[tuple[int, int]] = []
        errors: list[str] = []

        def reap() -> None:
            barrier.wait()
            try:
                outcomes.append(jobqueue.reap_expired())
            except Exception as exc:  # noqa: BLE001 - surfaced by assertion
                errors.append(repr(exc))

        threads = [threading.Thread(target=reap) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []
        assert sum(requeued for requeued, _ in outcomes) == 1
        assert _state(job_id)[0] == "queued"
    finally:
        reset_engine_for_tests(f"sqlite+pysqlite:///{home / 'local.db'}")
        init_db()


def test_audit_events_are_not_duplicated_on_terminal_race(db):
    """终态写入只记一次事件，重复回收/结算不会产生第二条审计事件。"""

    job_id = _running_job(lease_seconds=-1)
    assert jobqueue.reap_expired() == (1, 0)
    assert jobqueue.reap_expired() == (0, 0)
    assert _event_count(job_id, "queued") == 1
    assert _event_count(job_id, "failed") == 0

    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row is not None
        row.status = "running"
        row.claim_token = "tok-2"
        row.lease_expires_at = utc_now() - timedelta(seconds=1)
    assert jobqueue.reap_expired() == (1, 0)
    assert _event_count(job_id, "queued") == 2
