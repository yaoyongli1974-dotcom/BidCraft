"""v0.31.1 阶段 2：Reaper 租约判定与重试上限竞争。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import GenerationLease, Job, JobEvent


def _seed_job(*, attempts: int = 0, lease: timedelta | None = None) -> str:
    now = utc_now()
    with session_scope() as db:
        job = Job(
            kind="generate",
            status="running",
            attempts=attempts,
            max_attempts=3,
            claim_token="reaper-token",
            lease_expires_at=now + lease if lease is not None else now - timedelta(seconds=1),
        )
        db.add(job)
        db.flush()
        return job.id


def _snapshot(job_id: str) -> tuple[str, int, list[str]]:
    with session_scope() as db:
        job = db.get(Job, job_id)
        events = list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
        return str(job.status), int(job.attempts or 0), events


def _requeue_stale(job_id: str, *, scan_time=None):
    with session_scope() as db:
        row = db.get(Job, job_id)
        return jobqueue._requeue(
            db,
            row,
            "租约过期重新排队",
            lease_expired_before=scan_time or utc_now(),
        )


def test_heartbeat_wins_race_at_attempt_cap(db) -> None:
    """扫描时过期，但心跳在条件 UPDATE 前续租：不得误杀活跃任务。"""

    now = utc_now()
    job_id = _seed_job(attempts=3, lease=timedelta(seconds=300))
    assert _requeue_stale(job_id, scan_time=now) == "lost"
    status, attempts, events = _snapshot(job_id)
    assert (status, attempts, events) == ("running", 3, [])


def test_expired_job_at_attempt_cap_fails_exactly_once(db) -> None:
    job_id = _seed_job(attempts=3)
    assert _requeue_stale(job_id) == "failed"
    assert _requeue_stale(job_id) == "lost"
    status, _attempts, events = _snapshot(job_id)
    assert status == "failed"
    assert events == ["failed"]


def test_two_reapers_with_stale_rows_only_one_requeues(db) -> None:
    job_id = _seed_job()
    assert _requeue_stale(job_id) == "requeued"
    assert _requeue_stale(job_id) == "lost"
    status, _attempts, events = _snapshot(job_id)
    assert status == "queued"
    assert events == ["queued"]


def test_100_rounds_concurrent_reclaim_stays_consistent(db) -> None:
    for _ in range(100):
        job_id = _seed_job()
        outcomes = [_requeue_stale(job_id), _requeue_stale(job_id)]
        assert sorted(outcomes) == ["lost", "requeued"]
        status, _attempts, events = _snapshot(job_id)
        assert status == "queued"
        assert events == ["queued"]


def test_reaper_cleans_expired_generation_leases(db) -> None:
    with session_scope() as writer_db:
        writer_db.add(
            GenerationLease(
                project_id="p-expired",
                token="old",
                expires_at=utc_now() - timedelta(seconds=1),
            )
        )
    jobqueue.reap_expired()
    with session_scope() as reader_db:
        assert reader_db.get(GenerationLease, "p-expired") is None


def test_recovery_keeps_job_with_live_lease(db) -> None:
    job_id = _seed_job(lease=timedelta(seconds=300))
    requeued, failed = jobqueue.recover_on_startup()
    assert (requeued, failed) == (0, 0)
    status, _attempts, events = _snapshot(job_id)
    assert status == "running"
    assert events == []
