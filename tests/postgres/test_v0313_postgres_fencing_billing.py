"""v0.31.3 阶段 1B：GenerationLease / 终态 fencing / 账务的真实 PostgreSQL 并发验证。

每个用例都使用独立线程 + 独立 Session + Barrier 同步起跑；核心条件 UPDATE
全部由生产代码执行，不 mock 数据库操作，不用 Python 业务锁。
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
from bidcraft.models import (
    CreditTransaction,
    Job,
    JobEvent,
    OutlineNode,
    PendingCharge,
    Project,
    Section,
    User,
)
from bidcraft.workers.generation import commit_generation_chapter

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL（专用测试库）")

TOKEN = "v0313-pg-fence-token"
ROUNDS = 100


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
        threading.Thread(target=run, args=("left", left), name="pg-fence-left"),
        threading.Thread(target=run, args=("right", right), name="pg-fence-right"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "PG concurrency thread did not finish"
    assert not errors, f"unexpected thread errors: {errors!r}"
    return results["left"], results["right"]


def _seed_project(db):
    project = Project(id=f"pg-v0313-{uuid.uuid4().hex[:8]}", name="PG fencing", owner_id=None)
    db.add(project)
    db.flush()
    return project.id


def _seed_user(db):
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
    return user


def _seed_job(*, project_id: str, charged: int = 0, chapters: int = 3, done: int = 0,
              user: User | None = None):
    job_id = str(uuid.uuid4())
    payload: dict[str, object] = {"done_steps": [f"chapter:{i}" for i in range(done)]}
    if charged:
        payload.update(charged=charged, chapters=chapters)
        if user is not None:
            payload.update(user_id=user.id, billing_account_id=user.id)
    job = Job(
        id=job_id,
        kind="generate" if charged else "agent",
        project_id=project_id,
        status="running",
        attempts=0,
        max_attempts=3,
        claim_token=TOKEN,
        worker_id=jobqueue._worker_id,
        claimed_at=utc_now(),
        lease_expires_at=utc_now() + timedelta(seconds=300),
        heartbeat_at=utc_now(),
        payload=payload,
    )
    return job_id, job, payload


def _chapter_state(job_id, node_id, section_id):
    with session_scope() as db:
        section = db.get(Section, section_id)
        node = db.get(OutlineNode, node_id)
        events = list(
            db.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id))
        )
        return {
            "body": section.body if section else None,
            "node_status": node.status if node else None,
            "events": events,
        }


def _balance(user_id):
    with session_scope() as db:
        return int(db.get(User, user_id).credits)


def _positive_refunds(user_id):
    with session_scope() as db:
        return list(
            db.scalars(
                select(CreditTransaction.amount).where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.amount > 0,
                )
            )
        )


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_generation_lease_release_vs_chapter_commit(round_no):
    with session_scope() as db:
        project_id = _seed_project(db)
        node_id = str(uuid.uuid4())
        section_id = str(uuid.uuid4())
        db.add(OutlineNode(id=node_id, project_id=project_id, heading="PG chapter", status="pending"))
        db.add(Section(id=section_id, node_id=node_id, body="", status="pending", words=0))
        job_id, job, _ = _seed_job(project_id=project_id)
        db.add(job)
        db.flush()
        lease_token = jobqueue.acquire_generation_lease(
            db, project_id, job_id=job_id, ttl_seconds=300
        )
        assert lease_token

    info = {"node_id": node_id, "section_id": section_id, "words": 10}

    def commit():
        try:
            with jobqueue.claim_context(TOKEN):
                commit_generation_chapter(job_id, project_id, lease_token, info, "PG正文", "model")
            return "committed"
        except jobqueue.JobAborted:
            return "aborted"

    def release():
        with session_scope() as db:
            return jobqueue.release_generation_lease(db, project_id, lease_token)

    outcome, released = _pair(commit, release)
    assert released is True
    state = _chapter_state(job_id, node_id, section_id)
    if outcome == "committed":
        assert state["body"] == "PG正文"
        assert state["node_status"] == "drafted"
        assert state["events"].count("chapter_committed") == 1
    else:
        assert outcome == "aborted"
        assert state["body"] in (None, "")
        assert state["node_status"] == "pending"
        assert state["events"].count("chapter_committed") == 0


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_two_workers_finalize_one_job_once(round_no):
    with session_scope() as db:
        project_id = _seed_project(db)
        job_id, job, _ = _seed_job(project_id=project_id)
        db.add(job)

    def finalize():
        try:
            with jobqueue.claim_context(TOKEN):
                jobqueue.finalize_job_result(job_id, result={"ok": True})
            return "committed"
        except jobqueue.JobAborted:
            return "aborted"

    a, b = _pair(finalize, finalize)
    assert sorted([a, b]) == ["aborted", "committed"]
    with session_scope() as db:
        row = db.get(Job, job_id)
        events = list(
            db.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id))
        )
        assert row.status == "done" and row.result == {"ok": True}
        assert events.count("done") == 1


@pytest.mark.parametrize("round_no", range(50))
def test_two_reapers_refund_charged_job_once(round_no):
    with session_scope() as db:
        project_id = _seed_project(db)
        user = _seed_user(db)
        job_id, job, payload = _seed_job(
            project_id=project_id, charged=6, chapters=3, done=1, user=user
        )
        db.add(job)
        db.flush()
        job.payload = {**payload}
        billing.charge_chapters(db, user, 6, project_id=project_id, note="PG refund race")

    def refund():
        with session_scope() as db:
            row = db.get(Job, job_id)
            return jobqueue.refund_charged_job(db, row)

    a, b = _pair(refund, refund)
    assert sorted([a, b]) == [0, 4]
    assert _balance(user.id) == 98
    assert _positive_refunds(user.id) == [4]
    with session_scope() as db:
        assert bool(db.get(Job, job_id).refund_claimed) is True


@pytest.mark.parametrize("round_no", range(50))
def test_settle_vs_reconcile_refund_once(round_no):
    with session_scope() as db:
        project_id = _seed_project(db)
        user = _seed_user(db)
        job_id, job, _payload = _seed_job(
            project_id=project_id, charged=6, chapters=3, done=1, user=user
        )
        db.add(job)
        db.flush()
        billing.charge_chapters(db, user, 6, project_id=project_id, note="PG marker race")
        marker_id = jobqueue.begin_pending_charge(
            db, user, project_id, 6, 3, note=f"job:{job_id}|PG marker race"
        )
        assert marker_id

    def settle():
        with session_scope() as db:
            return jobqueue.settle_pending_charge(db, marker_id, None, project_id, 3, 0)

    def reconcile():
        with session_scope() as db:
            return jobqueue.reconcile_pending_charges(older_than_seconds=0, db=db)

    settle_paid, reconciled = _pair(settle, reconcile)
    assert (settle_paid, reconciled) in ((4, 0), (0, 1))
    assert _balance(user.id) == 98
    assert _positive_refunds(user.id) == [4]
    with session_scope() as db:
        marker = db.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 4
