"""Batch 1 · 计费可靠性：失败任务的异步扣费退还与章节写入真相。"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import Job, PendingCharge, User


def _seed_user(db, credits: int = 20) -> User:
    user = User(
        username=f"b1-{len(db.query(User).all())}",
        password_hash="x",
        role="manager",
        is_external=True,
        credits=0,
    )
    db.add(user)
    db.flush()
    billing.add_credits(db, user.id, credits, kind="admin", note="seed")
    return user


def _seed_charged_job(db, user: User, *, done_steps: list[str] | None = None) -> Job:
    # 模拟 generate_all 已经扣过的 9 点 / 3 章。
    billing.add_credits(db, user.id, -9, kind="consume", note="charge")
    job = Job(
        kind="generate",
        status="running",
        attempts=1,
        payload={
            "charged": 9,
            "chapters": 3,
            "user_id": user.id,
            **({"done_steps": done_steps} if done_steps is not None else {}),
        },
    )
    db.add(job)
    db.flush()
    return job


# --- Gap 1: reaper / startup 失败路径必须退还异步扣费 -------------------------


def test_reaper_at_attempt_cap_refunds_charged_job(db, monkeypatch):
    monkeypatch.setattr(settings, "job_max_attempts", 1)
    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user)
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        job_id, user_id = job.id, user.id

    assert jobqueue.reap_expired() == (0, 1)
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row.status == "failed"
        assert session.get(User, user_id).credits == 20  # 9 点全额退还
        assert row.payload["refunded"] is True
        assert row.payload["refunded_credits"] == 9


def test_reaper_refunds_only_unwritten_chapters_from_done_steps(db, monkeypatch):
    monkeypatch.setattr(settings, "job_max_attempts", 1)
    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user, done_steps=["chapter:a", "chapter:b"])
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        job_id, user_id = job.id, user.id

    jobqueue.reap_expired()
    with session_scope() as session:
        # 2/3 章已落库：只退 3 点，不是全额 9 点。
        assert session.get(User, user_id).credits == 14
        assert session.get(Job, job_id).payload["refunded_credits"] == 3


def test_startup_recovery_at_attempt_cap_refunds_charged_job(db, monkeypatch):
    monkeypatch.setattr(settings, "job_max_attempts", 1)
    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user)
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        user_id = user.id

    jobqueue.recover_on_startup()
    with session_scope() as session:
        assert session.get(User, user_id).credits == 20


# --- Gap 2: done_steps 是章节写入的唯一真相 ----------------------------------


def test_job_refund_ignores_stale_written_when_ledger_present(db):
    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user, done_steps=["chapter:a", "chapter:b"])
        user_id = user.id
        # 调用方传 0（当前尝试没写新章）也不能忽略早前已落库的 2 章。
        assert jobqueue.refund_charged_job(session, job, 0) == 3
        session.flush()
        assert session.get(User, user_id).credits == 14


def test_job_refund_full_when_no_chapters_persisted(db):
    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user, done_steps=[])
        user_id = user.id
        assert jobqueue.refund_charged_job(session, job) == 9
        session.flush()
        assert session.get(User, user_id).credits == 20


def test_job_refund_is_idempotent(db):
    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user)
        user_id = user.id
        assert jobqueue.refund_charged_job(session, job) == 9
        assert jobqueue.refund_charged_job(session, job) == 0
        session.flush()
        assert session.get(User, user_id).credits == 20


def test_job_support_wrapper_uses_ledger_by_default(db):
    from bidcraft.services.job_support import _job_refund

    with session_scope() as session:
        user = _seed_user(session)
        job = _seed_charged_job(session, user, done_steps=["chapter:a"])
        user_id = user.id
        _job_refund(session, job)  # 无 written 参数：回退到 done_steps
        session.flush()
        assert session.get(User, user_id).credits == 17  # 退 6，留 3 章中 1 章的花费


# --- Gap 3: settle 的条件抢占与并发唯一退款 ----------------------------------


def _seed_marker(db, *, credits: int = 4, chapters: int = 2, age_days: int = 0):
    user = _seed_user(db, credits=20)
    marker_id = jobqueue.begin_pending_charge(db, user, "p1", credits, chapters)
    if age_days:
        marker = db.get(PendingCharge, marker_id)
        marker.created_at = utc_now() - timedelta(days=age_days)
        marker.updated_at = utc_now() - timedelta(days=age_days)
    db.flush()
    return marker_id, user


def test_settle_refuses_processing_marker(db):
    with session_scope() as session:
        marker_id, user = _seed_marker(session)
        user_id = user.id
    with session_scope() as session:
        # 另一实例已经抢占（status=processing）。
        session.get(PendingCharge, marker_id).status = "processing"
        session.flush()
    with session_scope() as session:
        user = session.get(User, user_id)
        assert jobqueue.settle_pending_charge(session, marker_id, user, "p1", 2, 0) == 0
        session.flush()
        assert session.get(User, user_id).credits == 20
        assert session.get(PendingCharge, marker_id).status == "processing"


def test_settle_twice_refunds_once(db):
    with session_scope() as session:
        marker_id, user = _seed_marker(session)
        user_id = user.id
    with session_scope() as session:
        user = session.get(User, user_id)
        assert jobqueue.settle_pending_charge(session, marker_id, user, "p1", 2, 0) == 4
        session.flush()
    with session_scope() as session:
        user = session.get(User, user_id)
        assert jobqueue.settle_pending_charge(session, marker_id, user, "p1", 2, 0) == 0
        assert session.get(User, user_id).credits == 24
        assert session.get(PendingCharge, marker_id).refunded_credits == 4


def _retry_on_lock(fn):
    for _ in range(10):
        try:
            return fn()
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.05)
    raise AssertionError("SQLite 一直锁住，并发用例无法推进")


def test_settle_and_reconcile_concurrently_refund_once(db):
    with session_scope() as session:
        marker_id, user = _seed_marker(session, age_days=1)
        user_id = user.id

    barrier = threading.Barrier(2)
    results: list[int] = []

    def settle_side():
        barrier.wait(timeout=5)

        def attempt():
            with session_scope() as session:
                value = jobqueue.settle_pending_charge(
                    session, marker_id, session.get(User, user_id), "p1", 2, 0
                )
                session.flush()
                return value

        results.append(_retry_on_lock(attempt))

    def reconcile_side():
        barrier.wait(timeout=5)

        def attempt():
            with session_scope() as session:
                value = jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session)
                session.flush()
                return value

        results.append(_retry_on_lock(attempt))

    threads = [threading.Thread(target=settle_side), threading.Thread(target=reconcile_side)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    # settle 返回退款点数（4），reconcile 返回结清的 marker 数（1）：只允许一个成功。
    assert set(results) in ({4, 0}, {0, 1}), results
    with session_scope() as session:
        assert session.get(User, user_id).credits == 24
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 4


if __name__ == "__main__":  # pragma: no cover - 便于单独调试
    pytest.main([__file__])
