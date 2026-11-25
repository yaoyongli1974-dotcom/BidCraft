"""v0.31.3 阶段 1A：JobQueue / Reaper 真实 PostgreSQL 并发验证。

每个用例都在独立线程中使用独立 Session，Barrier 同步起跑，核心条件 UPDATE
全部由生产代码执行；不 mock 数据库操作，不用 Python 业务锁。
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import CreditTransaction, Job, JobEvent, User

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL（专用测试库）")

TOKEN = "v0313-pg-reaper-token"
ROUNDS = 100


def _seed_job(*, attempts=0, lease_offset=300, charged=0, chapters=3, done=0):
    job_id = str(uuid.uuid4())
    project_id = f"pg-v0313-{uuid.uuid4().hex[:8]}"
    with session_scope() as db:
        payload = {"done_steps": [f"chapter:{i}" for i in range(done)]}
        if charged:
            payload.update(
                charged=charged,
                chapters=chapters,
                user_id="",
                billing_account_id="",
            )
        job = Job(
            id=job_id,
            kind="generate" if charged else "agent",
            project_id=project_id,
            status="running",
            attempts=attempts,
            max_attempts=3,
            claim_token=TOKEN,
            worker_id=jobqueue._worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=lease_offset),
            heartbeat_at=utc_now(),
            payload=payload,
        )
        db.add(job)
        if charged:
            user = User(
                id=str(uuid.uuid4()),
                username=f"pg31_{uuid.uuid4().hex[:8]}",
                display_name="PG billing",
                password_hash="test-not-a-real-hash",
                role="manager",
                is_external=True,
                credits=100,
            )
            db.add(user)
            db.flush()
            # Never mutate the dict already attached to the Job: SQLAlchemy
            # compares against that same object, so in-place changes look clean.
            job.payload = {
                **payload,
                "user_id": user.id,
                "billing_account_id": user.id,
            }
            billing.charge_chapters(db, user, charged, project_id=project_id, note="pg test")
    return job_id, project_id


def _snapshot(job_id):
    with session_scope() as db:
        job = db.get(Job, job_id)
        events = list(
            db.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id))
        )
        return {
            "status": job.status,
            "attempts": job.attempts,
            "token": job.claim_token,
            "lease": job.lease_expires_at,
            "events": events,
            "refund_claimed": bool(job.refund_claimed),
        }


def _reaper_attempt(job_id, cutoff):
    with session_scope() as db:
        row = db.get(Job, job_id)  # stale snapshot read before the conditional UPDATE
        return jobqueue._requeue(db, row, "PG reaper", lease_expired_before=cutoff)


def _pair(left, right):
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(name, fn):
        try:
            barrier.wait()
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 - preserve real failures from threads
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("left", left), name="pg-reaper-left"),
        threading.Thread(target=run, args=("right", right), name="pg-reaper-right"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "PG concurrency thread did not finish"
    assert not errors, f"unexpected thread errors: {errors!r}"
    return results["left"], results["right"]


def _balance(user_id):
    with session_scope() as db:
        return int(db.get(User, user_id).credits)


def _user_id(job_id):
    with session_scope() as db:
        return str((db.get(Job, job_id).payload or {}).get("billing_account_id") or "")


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_two_reapers_cannot_double_requeue(pg_engine, round_no):
    job_id, _ = _seed_job(lease_offset=-10)
    cutoff = utc_now()
    a, b = _pair(
        lambda: _reaper_attempt(job_id, cutoff),
        lambda: _reaper_attempt(job_id, cutoff),
    )
    assert sorted([a, b]) == ["lost", "requeued"]
    state = _snapshot(job_id)
    assert state["status"] == "queued"
    assert state["attempts"] == 0  # attempts increment only on claim, not requeue
    assert state["events"].count("queued") == 1
    assert "failed" not in state["events"]


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_reaper_vs_worker_final_commit(pg_engine, round_no):
    """Both can legitimately win depending on row-lock order; never both."""
    job_id, _ = _seed_job(lease_offset=1)
    cutoff = utc_now() + timedelta(seconds=5)

    def commit_worker():
        try:
            with jobqueue.claim_context(TOKEN), jobqueue.commit_fenced_result(job_id) as (db, row):
                row.status = "done"
                row.result = {"ok": True}
                db.flush()
            return "committed"
        except jobqueue.JobAborted:
            return "aborted"

    a, b = _pair(commit_worker, lambda: _reaper_attempt(job_id, cutoff))
    assert sorted([a, b]) in (["aborted", "requeued"], ["committed", "lost"])
    state = _snapshot(job_id)
    if state["status"] == "done":
        assert "failed" not in state["events"] and "queued" not in state["events"]
    else:
        assert state["status"] == "queued"
        assert state["events"].count("queued") == 1


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_two_reapers_at_attempt_cap_refund_once(pg_engine, round_no):
    job_id, _ = _seed_job(attempts=3, lease_offset=-10, charged=6, chapters=3, done=1)
    user_id = _user_id(job_id)
    cutoff = utc_now()
    a, b = _pair(
        lambda: _reaper_attempt(job_id, cutoff),
        lambda: _reaper_attempt(job_id, cutoff),
    )
    assert sorted([a, b]) == ["failed", "lost"]
    state = _snapshot(job_id)
    assert state["status"] == "failed"
    assert state["events"].count("failed") == 1
    assert state["refund_claimed"] is True
    # charged=6 / chapters=3 / done=1 => earned 2, refund 4:
    # 100 - 6 + 4 = 98.  A full refund would wrongly pay for the finished
    # chapter, so assert both the exact balance and the exact ledger entry.
    assert _balance(user_id) == 98
    with session_scope() as db:
        refunds = list(
            db.scalars(
                select(CreditTransaction.amount).where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.amount > 0,
                )
            )
        )
        assert refunds == [4]


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_null_and_expired_lease_reject_terminal_commit(pg_engine, round_no):
    for lease_offset in (None, -10):
        job_id, _ = _seed_job(lease_offset=lease_offset if lease_offset is not None else 300)
        if lease_offset is None:
            with session_scope() as db:
                db.get(Job, job_id).lease_expires_at = None
        with (
            pytest.raises(jobqueue.JobAborted),
            jobqueue.claim_context(TOKEN),
            jobqueue.commit_fenced_result(job_id) as (db, row),
        ):
            row.status = "done"
            row.result = {"illegal": True}
            db.flush()
        state = _snapshot(job_id)
        assert state["status"] == "running"
        assert state["events"] == []


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_stale_token_cannot_commit_after_reclaim(pg_engine, round_no):
    job_id, _ = _seed_job(lease_offset=-10)
    _reaper_attempt(job_id, utc_now())
    with (
        pytest.raises(jobqueue.JobAborted),
        jobqueue.claim_context(TOKEN),
        jobqueue.commit_fenced_result(job_id) as (db, row),
    ):
        row.status = "done"
        db.flush()
    state = _snapshot(job_id)
    assert state["status"] == "queued"
    assert state["token"] is None
    assert state["events"].count("queued") == 1


def test_postgres_dialect_is_real(pg_engine):
    with session_scope() as db:
        assert db.get_bind().dialect.name == "postgresql"
