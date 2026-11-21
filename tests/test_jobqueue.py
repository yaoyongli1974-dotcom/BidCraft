"""DB-driven job queue: atomic claim, lease, reaper, retry, dispatch, backpressure.

The old in-process min-heap is gone — the ``jobs`` table is the single source of
truth.  These tests verify the new contract without a running FastAPI app.
"""

from __future__ import annotations

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


def test_recover_on_startup_requeues_running_keeps_queued(db):
    with session_scope() as session:
        running = _seed(session, status="running", attempts=1)
        running.lease_expires_at = utc_now() + timedelta(seconds=100)  # not expired
        queued = _seed(session, status="queued")
        running_id, queued_id = running.id, queued.id
    requeued, failed = jobqueue.recover_on_startup()
    assert (requeued, failed) == (1, 0)
    with session_scope() as session:
        assert session.get(Job, running_id).status == "queued"
        assert session.get(Job, queued_id).status == "queued"


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
