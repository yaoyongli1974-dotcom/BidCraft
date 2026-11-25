"""v0.31.2 阶段 2：Reaper/heartbeat 真实多线程、多 Session、多事务竞争。

每轮均为独立线程、独立 SQLAlchemy Session、独立事务，并由 Barrier 控制起跑；
不使用 Python 全局锁替代数据库 fencing，也不 mock 核心条件 UPDATE。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import Job, JobEvent, User

TOKEN = "v0312-race-token"


def _seed_job(*, attempts: int = 0, charged: int = 0, lease_expired: bool = True) -> str:
    now = utc_now()
    with session_scope() as db:
        payload: dict[str, Any] = {}
        user_id = ""
        if charged:
            user = User(
                username=f"v0312-race-{threading.get_ident()}-{now.timestamp()}",
                password_hash="x",
                role="manager",
                is_external=True,
                credits=0,
            )
            db.add(user)
            db.flush()
            billing.add_credits(db, user.id, 100, kind="admin", note="seed")
            billing.charge_chapters(db, user, charged, note="race seed")
            user_id = user.id
            payload = {"charged": charged, "chapters": 2, "user_id": user_id}
        row = Job(
            kind="generate",
            status="running",
            attempts=attempts,
            max_attempts=3,
            claim_token=TOKEN,
            worker_id=jobqueue._worker_id,
            claimed_at=now,
            lease_expires_at=now - timedelta(seconds=1) if lease_expired else now + timedelta(seconds=300),
            payload=payload,
        )
        db.add(row)
        db.flush()
        return row.id


def _state(job_id: str) -> tuple[str, int, list[str], bool, str]:
    with session_scope() as db:
        row = db.get(Job, job_id)
        events = list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
        return (
            str(row.status),
            int(row.attempts or 0),
            events,
            bool(row.refund_claimed),
            str(row.claim_token or ""),
        )


def _balance(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def _pair(first: Callable[[], Any], second: Callable[[], Any]) -> tuple[Any, Any, list[BaseException]]:
    barrier = threading.Barrier(2)
    results: dict[int, Any] = {}
    errors: list[BaseException] = []

    def run(index: int, fn: Callable[[], Any]) -> None:
        try:
            barrier.wait()
            results[index] = fn()
        except BaseException as exc:  # noqa: BLE001 - record every thread outcome
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(0, first)),
        threading.Thread(target=run, args=(1, second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "竞争线程未在 30s 内结束"
    assert errors == [], f"竞争线程异常：{errors}"
    return results[0], results[1], errors


def _reaper_attempt(job_id: str) -> str:
    with session_scope() as db:
        # Each thread reads its own stale snapshot BEFORE the conditional UPDATE.
        row = db.get(Job, job_id)
        return jobqueue._requeue(
            db, row, "租约过期重新排队", lease_expired_before=utc_now()
        )


# ---------------------------------------------------------------- scenario A


def test_100_rounds_two_reapers_one_wins(db) -> None:
    for _round in range(100):
        job_id = _seed_job()
        outcomes = _pair(
            lambda job_id=job_id: _reaper_attempt(job_id),
            lambda job_id=job_id: _reaper_attempt(job_id),
        )
        assert sorted([outcomes[0], outcomes[1]]) == ["lost", "requeued"]
        status, attempts, events, _claimed, _token = _state(job_id)
        assert status == "queued"
        # attempts 只在 claim 时自增；Reaper 回收不重复计数。
        assert attempts == 0
        assert events.count("queued") == 1
        assert "failed" not in events


# ---------------------------------------------------------------- scenario B/C


def test_100_rounds_heartbeat_wins_and_reaper_does_not_kill(db) -> None:
    for _round in range(100):
        job_id = _seed_job(lease_expired=False)
        hb_done = threading.Event()

        def heartbeat(job_id=job_id, hb_done=hb_done) -> bool:
            result = jobqueue._renew_lease_once(job_id, TOKEN)
            hb_done.set()
            return result

        def reaper(job_id=job_id, hb_done=hb_done) -> str:
            hb_done.wait(30)
            return _reaper_attempt(job_id)

        hb_result, reaper_result, _errors = _pair(heartbeat, reaper)
        assert hb_result is True
        assert reaper_result == "lost"
        status, _attempts, events, _claimed, _token = _state(job_id)
        assert status == "running"
        assert events == []


def test_100_rounds_reaper_wins_and_old_heartbeat_cannot_renew(db) -> None:
    for _round in range(100):
        job_id = _seed_job()
        reaper_done = threading.Event()

        def reaper(job_id=job_id, reaper_done=reaper_done) -> str:
            result = _reaper_attempt(job_id)
            reaper_done.set()
            return result

        def heartbeat(job_id=job_id, reaper_done=reaper_done) -> bool:
            reaper_done.wait(30)
            return jobqueue._renew_lease_once(job_id, TOKEN)

        reaper_result, hb_result, _errors = _pair(reaper, heartbeat)
        assert reaper_result == "requeued"
        assert hb_result is False
        status, _attempts, events, _claimed, token = _state(job_id)
        assert status == "queued"
        assert token == ""
        assert events == ["queued"]


# ---------------------------------------------------------------- scenario D


def test_100_rounds_attempt_cap_with_heartbeat_does_not_misfire(db) -> None:
    for _round in range(100):
        job_id = _seed_job(attempts=3, lease_expired=False)
        hb_done = threading.Event()

        def heartbeat(job_id=job_id, hb_done=hb_done) -> bool:
            result = jobqueue._renew_lease_once(job_id, TOKEN)
            hb_done.set()
            return result

        def reaper(job_id=job_id, hb_done=hb_done) -> str:
            hb_done.wait(30)
            return _reaper_attempt(job_id)

        hb_result, reaper_result, _errors = _pair(heartbeat, reaper)
        assert hb_result is True
        assert reaper_result == "lost"
        status, attempts, events, _claimed, _token = _state(job_id)
        assert (status, attempts, events) == ("running", 3, [])


# ---------------------------------------------------------------- scenario E


@pytest.mark.parametrize("_round", range(50))
def test_two_reapers_at_attempt_cap_refund_once(db, _round: int) -> None:
    job_id = _seed_job(attempts=3, charged=10)
    with session_scope() as reader_db:
        user_id = str(reader_db.get(Job, job_id).payload["user_id"])
    assert _balance(user_id) == 90

    outcomes = _pair(
        lambda job_id=job_id: _reaper_attempt(job_id),
        lambda job_id=job_id: _reaper_attempt(job_id),
    )
    assert sorted([outcomes[0], outcomes[1]]) == ["failed", "lost"]
    status, attempts, events, claimed, token = _state(job_id)
    assert status == "failed"
    assert attempts == 3
    assert events.count("failed") == 1
    assert claimed is True
    assert token == ""
    assert _balance(user_id) == 100
