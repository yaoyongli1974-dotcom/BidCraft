"""v0.31.2 阶段 1：Job 租约 NULL/过期时禁止终态提交（真实事务）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from bidcraft import jobqueue, jobs
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import Job, JobEvent

TOKEN = "v0312-lease-token"


def _seed(
    *,
    status: str = "running",
    token: str = TOKEN,
    lease: timedelta | None = timedelta(seconds=300),
) -> str:
    now = utc_now()
    with session_scope() as db:
        row = Job(
            kind="generate",
            status=status,
            claim_token=token,
            claimed_at=now,
            lease_expires_at=now + lease if lease is not None else None,
            heartbeat_at=now,
            worker_id="v0312-worker",
            attempts=1,
        )
        db.add(row)
        db.flush()
        return row.id


def _state(job_id: str) -> tuple[str, dict, list[str]]:
    with session_scope() as db:
        row = db.get(Job, job_id)
        events = list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
        return str(row.status), dict(row.result or {}), events


def _commit(job_id: str, token: str = TOKEN) -> None:
    with jobqueue.claim_context(token), jobqueue.commit_fenced_result(job_id) as (
        db,
        row,
    ):
        row.result = {"written": True}
        jobs.publish_event(job_id, "done", jobs.job_payload(row), db=db)


@pytest.mark.parametrize(
    "status,token,lease,allowed",
    [
        ("running", TOKEN, timedelta(seconds=300), True),
        ("running", TOKEN, None, False),
        ("running", TOKEN, timedelta(seconds=-1), False),
        ("running", "wrong-token", timedelta(seconds=300), False),
        ("failed", TOKEN, timedelta(seconds=300), False),
        ("cancelled", TOKEN, timedelta(seconds=300), False),
    ],
)
def test_terminal_fence_matrix(db, status, token, lease, allowed) -> None:
    job_id = _seed(status=status, token=TOKEN, lease=lease)
    if allowed:
        _commit(job_id, token)
        state, result, events = _state(job_id)
        assert state == "running"  # commit_fenced_result does not change status
        assert result == {"written": True}
        assert events == ["done"]
    else:
        with pytest.raises(jobqueue.JobAborted):
            _commit(job_id, token)
        state, result, events = _state(job_id)
        assert state == status
        assert result == {}
        assert events == []


def test_reaper_reclaim_rejects_old_worker(db) -> None:
    job_id = _seed()
    with session_scope() as reaper_db:
        reaper_db.get(Job, job_id).claim_token = "new-token"
    with pytest.raises(jobqueue.JobAborted):
        _commit(job_id)
    _status, result, events = _state(job_id)
    assert result == {}
    assert events == []


def test_failed_fence_writes_no_result_and_no_success_event(db) -> None:
    job_id = _seed(lease=None)
    with pytest.raises(jobqueue.JobAborted):
        _commit(job_id)
    assert _state(job_id)[1:] == ({}, [])
