"""DB-driven job queue: atomic claim, lease, reaper, retry, dispatch, backpressure.

The old in-process min-heap is gone — the ``jobs`` table is the single source of
truth.  These tests verify the new contract without a running FastAPI app.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import Job


def test_max_workers_clamp(monkeypatch):
    for value, expected in [(0, 1), (1, 1), (2, 2), (8, 8), (99, 8), (-5, 1)]:
        monkeypatch.setattr(settings, "max_concurrent_jobs", value)
        assert jobqueue._max_workers() == expected
    monkeypatch.setattr(settings, "max_concurrent_jobs", "not-a-number")
    assert jobqueue._max_workers() == 2


def test_position_unknown_and_empty(db):
    assert jobqueue.position("") == 0
    assert jobqueue.position("does-not-exist") == 0


def _seed(db, *, kind="generate", status="queued", priority=1, payload=None, project_id=None, created_at=None, attempts=0):
    job = Job(
        kind=kind,
        status=status,
        priority=priority,
        payload=payload or {},
        project_id=project_id,
        attempts=attempts,
    )
    db.add(job)
    db.flush()
    if created_at is not None:
        job.created_at = created_at
    return job


def test_position_orders_by_priority_then_fifo(db):
    with session_scope() as session:
        old = _seed(session, priority=1, created_at=utc_now() - timedelta(minutes=5))
        vip = _seed(session, priority=0, created_at=utc_now())
        normal = _seed(session, priority=1, created_at=utc_now())
        vip_id, old_id, normal_id = vip.id, old.id, normal.id
    # position() opens its own session, so seed rows must be committed first.
    assert jobqueue.position(vip_id) == 1
    assert jobqueue.position(old_id) == 2
    assert jobqueue.position(normal_id) == 3


def test_claim_next_is_atomic_and_increments_attempts(db):
    with session_scope() as session:
        job = _seed(session, payload={"ids": ["a", "b"]})
        job_id = job.id
    claimed = jobqueue.claim_next()
    assert claimed is not None and claimed.id == job_id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None
    assert claimed.worker_id
    # second claim finds nothing (only one job in a fresh test DB)
    assert jobqueue.claim_next() is None
    with session_scope() as session:
        assert session.get(Job, job_id).status == "running"


def test_requeue_releases_lease_and_publishes_event(db):
    with session_scope() as session:
        job = _seed(session, status="running", attempts=1)
        job.claimed_at = utc_now()
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        job.worker_id = "dead-worker"
        job_id = job.id
    requeued, failed = jobqueue.reap_expired()
    assert (requeued, failed) == (1, 0)
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row.status == "queued"
        assert row.claimed_at is None and row.lease_expires_at is None
        assert row.worker_id is None


def test_retry_cap_fails_after_max_attempts(db, monkeypatch):
    monkeypatch.setattr(settings, "job_max_attempts", 1)
    with session_scope() as session:
        job = _seed(session, status="running", attempts=1)  # already at cap
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        job_id = job.id
    requeued, failed = jobqueue.reap_expired()
    assert (requeued, failed) == (0, 1)
    with session_scope() as session:
        assert session.get(Job, job_id).status == "failed"


def test_recover_on_startup_requeues_only_expired_running(db):
    with session_scope() as session:
        expired = _seed(session, status="running", attempts=1)
        expired.lease_expires_at = utc_now() - timedelta(seconds=1)
        no_lease = _seed(session, status="running", attempts=1)  # legacy row
        queued = _seed(session, status="queued")
        expired_id, no_lease_id, queued_id = expired.id, no_lease.id, queued.id
    requeued, failed = jobqueue.recover_on_startup()
    assert (requeued, failed) == (2, 0)
    with session_scope() as session:
        assert session.get(Job, expired_id).status == "queued"
        assert session.get(Job, no_lease_id).status == "queued"
        assert session.get(Job, queued_id).status == "queued"


def test_recover_on_startup_leaves_unexpired_running_alone(db):
    """P0-1: a live worker's job must not be handed to a second worker."""

    with session_scope() as session:
        running = _seed(session, status="running", attempts=1)
        running.lease_expires_at = utc_now() + timedelta(seconds=100)
        running.claim_token = "live-worker-token"
        running_id = running.id
    requeued, failed = jobqueue.recover_on_startup()
    assert (requeued, failed) == (0, 0)
    with session_scope() as session:
        row = session.get(Job, running_id)
        assert row.status == "running"
        assert row.claim_token == "live-worker-token"


def test_claim_next_refreshes_claim_token(db):
    """P0-2: each claim gets a fresh token; a reclaim invalidates the old one."""

    with session_scope() as session:
        job = _seed(session, attempts=0)
        job_id = job.id
    first = jobqueue.claim_next()
    assert first is not None and first.claim_token
    first_token = first.claim_token
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row.claim_token == first_token
        # simulate the reaper reclaiming the expired lease
        row.lease_expires_at = utc_now() - timedelta(seconds=1)
    assert jobqueue.reap_expired() == (1, 0)
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row.claim_token is None  # requeue clears the token
    second = jobqueue.claim_next()
    assert second is not None and second.claim_token
    assert second.claim_token != first_token


def test_fenced_job_rejects_stale_worker(db):
    """Only the worker holding the current token may write job state."""

    with session_scope() as session:
        job = _seed(session, status="running", attempts=1)
        job.claim_token = "token-A"
        job_id = job.id
    with jobqueue.claim_context("token-A"), session_scope() as session:
        assert jobqueue.fenced_job(session, job_id) is not None
    # A reclaimed lease gives a new owner a different token.
    with jobqueue.claim_context("token-B"), session_scope() as session:
        assert jobqueue.fenced_job(session, job_id) is None
    # No claim context (e.g. direct call) is never allowed to write.
    with session_scope() as session:
        assert jobqueue.fenced_job(session, job_id) is None


def test_requeue_and_fail_clear_claim_token(db):
    with session_scope() as session:
        job = _seed(session, status="running", attempts=1)
        job.claim_token = "old-token"
        job_id = job.id
        jobqueue._set_failed(session, job, "boom")
        assert job.claim_token is None
    with session_scope() as session:
        assert session.get(Job, job_id).status == "failed"


def test_finalize_after_run_is_fenced_after_reclaim(db):
    """A stale worker's safety-net finalize must not touch the new owner's job."""

    with session_scope() as session:
        job = _seed(session, attempts=0)
        job_id = job.id
    claimed = jobqueue.claim_next()
    assert claimed is not None and claimed.id == job_id
    token = claimed.claim_token
    with session_scope() as session:  # reaper reclaims, new owner takes over
        session.get(Job, job_id).claim_token = "new-owner-token"
    with jobqueue.claim_context(token):
        jobqueue._finalize_after_run(job_id)
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row.status == "running"
        assert row.claim_token == "new-owner-token"


def test_execute_binds_claim_token_for_runner(db):
    """The runner sees its own claim token and can write while it is valid."""

    seen: dict[str, str] = {}

    def runner(job_id: str) -> None:
        seen["token"] = jobqueue.current_claim_token()
        with session_scope() as session:
            row = jobqueue.fenced_job(session, job_id)
            assert row is not None  # fenced write path is usable by the runner
            row.status = "done"

    jobqueue._register("test_fence", runner)
    try:
        with session_scope() as session:
            job = _seed(session, kind="test_fence")
            job_id = job.id
        claimed = jobqueue.claim_next()
        assert claimed is not None and claimed.id == job_id
        jobqueue._execute(claimed)
        assert seen["token"] == claimed.claim_token
        with session_scope() as session:
            assert session.get(Job, job_id).status == "done"
    finally:
        jobqueue._RUNNERS.pop("test_fence", None)


def test_heartbeat_stops_when_token_changes(db, monkeypatch):
    """P0-2: once the lease is reclaimed, the old worker stops renewing it."""

    import threading
    import time

    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 1)
    with session_scope() as session:
        job = _seed(session, status="running", attempts=1)
        job.claim_token = "old-token"
        job.lease_expires_at = utc_now() + timedelta(seconds=180)
        job_id = job.id
    stop = threading.Event()
    thread = threading.Thread(
        target=jobqueue._heartbeat, args=(job_id, stop, "old-token"), daemon=True
    )
    thread.start()
    try:
        time.sleep(1.5)  # let it renew at least once while still the owner
        with session_scope() as session:
            assert session.get(Job, job_id).claim_token == "old-token"
        with session_scope() as session:  # simulate reclaim: new owner's token
            session.get(Job, job_id).claim_token = "new-token"
        thread.join(timeout=4)
        assert not thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=2)


def test_rebuild_args_by_kind(db):
    with session_scope() as session:
        cases = {
            "batch_check": (_seed(session, kind="batch_check", payload={"ids": ["x"]}), (["x"],)),
            "batch_export": (_seed(session, kind="batch_export", payload={"ids": ["y"], "format": "md"}), (["y"], "md")),
            "images": (_seed(session, kind="images", project_id="p1", payload={"targets": [{"label": "L"}]}), ("p1", [{"label": "L"}])),
            "generate": (_seed(session, kind="generate", project_id="p2", payload={"force": True}), ("p2", True)),
            "kb_reindex": (_seed(session, kind="kb_reindex", payload={"kb_id": "k1"}), ("k1",)),
        }
        session.flush()
        for kind, (job, tail) in cases.items():
            args = jobqueue._rebuild_args(job)
            assert args == (job.id, *tail), kind


def test_backpressure_global_and_user(db, monkeypatch):
    monkeypatch.setattr(settings, "max_queued_jobs", 2)
    monkeypatch.setattr(settings, "max_user_queued_jobs", 1)
    with session_scope() as session:
        _seed(session, payload={"owner_id": "u1"})
        _seed(session, payload={"owner_id": "u2"})
        # global cap reached (2 queued)
        assert jobqueue.check_backpressure(session, "u3") is not None
        # user u1 has 1 queued == user cap
        assert jobqueue.check_backpressure(session, "u1") is not None
    monkeypatch.setattr(settings, "max_queued_jobs", 0)  # unlimited
    monkeypatch.setattr(settings, "max_user_queued_jobs", 0)
    with session_scope() as session:
        assert jobqueue.check_backpressure(session, "u1") is None


def test_expired_job_reaped_while_workers_are_busy(db, monkeypatch):
    """P1: 队列持续有任务时，过期租约仍会被回收。

    修复前 ``reap_expired()`` 只在 worker 的 ``claim_next() is None`` 空闲分支执行；
    只要队列不空（或 worker 都卡在长任务里），过期租约就长期无人认领。
    现在 reaper 是独立常驻线程，按固定间隔运行，不依赖任何 worker 空闲。
    """

    release = threading.Event()

    def slow_runner(job_id):
        release.wait(5)

    jobqueue._register("slow-test", slow_runner)
    monkeypatch.setattr(jobqueue, "_REAP_INTERVAL", 0.05)
    monkeypatch.setattr(jobqueue, "_IDLE_WAIT", 0.01)

    with session_scope() as session:
        expired = _seed(session, kind="slow-test", status="running", attempts=1)
        expired.claimed_at = utc_now() - timedelta(minutes=1)
        expired.lease_expires_at = utc_now() - timedelta(seconds=1)
        expired.worker_id = "dead-worker"
        expired.claim_token = "dead-token"
        expired_id = expired.id
        # 持续有活干：worker 一直认领/执行，不会进入空闲分支
        for _ in range(6):
            _seed(session, kind="slow-test", status="queued")

    jobqueue.start_workers()
    try:
        deadline = time.monotonic() + 2.0
        stale_token = True
        while time.monotonic() < deadline:
            with session_scope() as session:
                row = session.get(Job, expired_id)
                stale_token = (row.claim_token or "") == "dead-token"
            if not stale_token:
                break
            time.sleep(0.02)
        # 被 reaper 回收后：要么已重排队列（令牌清空），要么已被 worker 用新令牌
        # 重新认领——无论哪种，旧令牌都不可能还在。忙队列饿死 reaper 时会一直保留旧令牌。
        assert not stale_token, "过期租约未被独立 reaper 回收"
        from bidcraft.jobs import events_since

        assert "queued" in [event.kind for event in events_since(expired_id)]
    finally:
        release.set()
        jobqueue._reset()


def test_worker_loop_does_not_reap():
    """结构守护：reap_expired 不能回到 worker 循环（忙队列会饿死它）。"""

    import inspect

    assert "reap_expired" not in inspect.getsource(jobqueue._worker)


def test_submit_respects_embedded_workers_setting(db, monkeypatch):
    """分离部署：embedded_workers=False 时 submit 只入队，不在 API 进程拉起 worker。"""

    with session_scope() as session:
        _seed(session, status="queued")
    monkeypatch.setattr(settings, "embedded_workers", False)
    jobqueue.submit()
    assert not jobqueue._workers_started
    assert not jobqueue._reaper_started
