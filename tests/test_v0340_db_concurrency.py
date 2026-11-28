"""v0.34.0 P1：退款锁顺序、失败退款重试与启动恢复的真实 SQLite 并发/会话验证。

所有用例都使用真实 Session、真实条件 UPDATE 与真实账本写入；线程用例用
``threading.Barrier`` 同步起跑，不依赖进程内 Python 锁保护业务状态。模型客户端
不存在于本文件的路径上，唯一的替换点是账本写入的瞬态故障注入。
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from bidcraft import billing, jobqueue
from bidcraft.api.routes import projects
from bidcraft.clock import utc_now
from bidcraft.context import reset_current_user, set_current_user
from bidcraft.db import session_scope
from bidcraft.models import (
    CreditTransaction,
    Fact,
    GenerationLease,
    Job,
    OutlineNode,
    PendingCharge,
    Project,
    Section,
    User,
)

PROJECT_ID = "v0340-db-concurrency"


def _seed_user(db, username: str) -> User:
    user = User(
        username=username,
        password_hash="test-only",
        role="manager",
        is_external=True,
        credits=0,
    )
    db.add(user)
    db.flush()
    return user


def _seed_project(db) -> None:
    db.add(Project(id=PROJECT_ID, name="v0340 DB concurrency", owner_id=None))
    db.flush()


def _seed_job(
    db,
    user: User,
    *,
    status: str = "running",
    marker: bool = False,
    expired: bool = False,
) -> tuple[str, str]:
    """Seed a partially written charged generate job (1 of 3 chapters -> refund 4)."""
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        kind="generate",
        project_id=PROJECT_ID,
        status=status,
        attempts=1,
        max_attempts=5,
        claim_token="v0340-token",
        worker_id="v0340-worker",
        claimed_at=utc_now(),
        lease_expires_at=utc_now() + timedelta(seconds=-1 if expired else 300),
        heartbeat_at=utc_now(),
        payload={
            "charged": 6,
            "chapters": 3,
            "user_id": user.id,
            "billing_account_id": user.id,
            "done_steps": ["chapter:1"],
        },
    )
    db.add(job)
    db.flush()
    marker_id = ""
    if marker:
        marker_id = jobqueue.begin_pending_charge(
            db, user, PROJECT_ID, 6, 3, note=f"job:{job_id}|v0340"
        )
        job.payload = {**dict(job.payload or {}), "pending_charge_id": marker_id}
        db.flush()
    return job_id, marker_id


def _balance(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def _refunds(user_id: str) -> list[int]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(CreditTransaction.amount).where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.kind == "refund",
                    CreditTransaction.amount > 0,
                )
            )
        )


def _retry_count(job_id: str) -> int:
    with session_scope() as db:
        return int((db.get(Job, job_id).payload or {}).get("refund_retries") or 0)


def _pair(left, right):
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(name, fn):
        try:
            barrier.wait(timeout=10)
            results[name] = fn()
        except BaseException as exc:  # noqa: BLE001 - surface real thread failures
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("left", left), name="v0340-left"),
        threading.Thread(target=run, args=("right", right), name="v0340-right"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "并发线程未在 30 秒内结束"
    assert not errors, f"unexpected thread errors: {errors!r}"
    return results["left"], results["right"]


@pytest.mark.parametrize("round_no", range(5))
def test_job_refund_vs_marker_settle_pays_exactly_once(db, monkeypatch, round_no):
    """Job ledger and marker ledger race; the job->marker->user order must hold."""
    with session_scope() as session:
        _seed_project(session)
        user = _seed_user(session, f"v0340-race-{round_no}")
        job_id, marker_id = _seed_job(session, user, marker=True)

    real_refund = billing.refund_chapters

    def delayed_refund(session, payee, credits, **kwargs):
        # Widen the overlap window; both threads must reach the refund path.
        time.sleep(0.05)
        return real_refund(session, payee, credits, **kwargs)

    monkeypatch.setattr(jobqueue.billing, "refund_chapters", delayed_refund)

    def by_job():
        with session_scope() as session:
            return jobqueue.refund_charged_job(session, session.get(Job, job_id))

    def by_marker():
        with session_scope() as session:
            return jobqueue.settle_pending_charge(
                session, marker_id, None, PROJECT_ID, 3, 0
            )

    first, second = _pair(by_job, by_marker)
    assert first + second == 4, (first, second)
    assert _balance(user.id) == 4
    assert _refunds(user.id) == [4]
    with session_scope() as session:
        row = session.get(Job, job_id)
        marker = session.get(PendingCharge, marker_id)
        assert bool(row.refund_claimed) is True
        assert marker.status in {"refunded", "cleared"}
        assert marker.refunded_credits in {0, 4}


@pytest.mark.parametrize("round_no", range(5))
def test_job_refund_vs_reconcile_pays_exactly_once(db, monkeypatch, round_no):
    """The periodic reconcile path must share the single job-level refund claim."""
    with session_scope() as session:
        _seed_project(session)
        user = _seed_user(session, f"v0340-reconcile-{round_no}")
        job_id, marker_id = _seed_job(session, user, marker=True)
        stale = session.get(PendingCharge, marker_id)
        stale.updated_at = utc_now() - timedelta(hours=2)

    real_refund = billing.refund_chapters

    def delayed_refund(session, payee, credits, **kwargs):
        time.sleep(0.05)
        return real_refund(session, payee, credits, **kwargs)

    monkeypatch.setattr(jobqueue.billing, "refund_chapters", delayed_refund)

    def by_job():
        with session_scope() as session:
            return jobqueue.refund_charged_job(session, session.get(Job, job_id))

    def by_reconcile():
        with session_scope() as session:
            return jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session)

    first, second = _pair(by_job, by_reconcile)
    # reconcile returns the settled marker count, not credits paid.
    assert first + (4 * second) == 4, (first, second)
    assert _balance(user.id) == 4
    assert _refunds(user.id) == [4]


def test_failed_refund_is_retried_once_by_reconcile(db, monkeypatch):
    """A transient ledger failure must be retried on the next reconcile tick."""
    with session_scope() as session:
        _seed_project(session)
        user = _seed_user(session, "v0340-retry-once")
        job_id, _ = _seed_job(session, user, status="failed")

    real_refund = billing.refund_chapters
    attempts: list[int] = []

    def flaky(session, payee, credits, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient ledger outage")
        return real_refund(session, payee, credits, **kwargs)

    monkeypatch.setattr(jobqueue.billing, "refund_chapters", flaky)

    with session_scope() as session:
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session) == 0
    assert _balance(user.id) == 0
    assert _retry_count(job_id) == 1

    with session_scope() as session:
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session) == 0
    assert len(attempts) == 2
    assert _balance(user.id) == 4
    assert _refunds(user.id) == [4]
    with session_scope() as session:
        assert bool(session.get(Job, job_id).refund_claimed) is True
        assert _retry_count(job_id) == 1


def test_failed_refund_retry_is_bounded(db, monkeypatch):
    """Persistent ledger failures stop after the configured retry budget."""
    with session_scope() as session:
        _seed_project(session)
        user = _seed_user(session, "v0340-retry-bounded")
        job_id, _ = _seed_job(session, user, status="failed")

    attempts: list[int] = []

    def always_fail(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("persistent ledger outage")

    monkeypatch.setattr(jobqueue.billing, "refund_chapters", always_fail)
    for _ in range(jobqueue.MAX_REFUND_RETRIES + 3):
        with session_scope() as session:
            jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session)

    assert len(attempts) == jobqueue.MAX_REFUND_RETRIES
    assert _retry_count(job_id) == jobqueue.MAX_REFUND_RETRIES
    assert _balance(user.id) == 0
    assert _refunds(user.id) == []

    # Even after the ledger recovers, the exhausted budget prevents new attempts.
    monkeypatch.setattr(jobqueue.billing, "refund_chapters", billing.refund_chapters)
    with session_scope() as session:
        jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session)
    assert len(attempts) == jobqueue.MAX_REFUND_RETRIES
    assert _balance(user.id) == 0


def test_sync_generation_stops_when_lease_is_aborted_mid_run(db, monkeypatch):
    """Sync routes must re-check the lease before every chapter, like the worker."""
    from types import SimpleNamespace

    from bidcraft.pipeline import create_project

    body = "本章正文内容用于验证生成租约中止后的章节落库门禁。" * 4
    with session_scope() as session:
        project = create_project(session, "v0340 同步生成中止")
        session.add(
            Fact(
                project_id=project.id,
                key="project.name",
                label="项目名称",
                value="v0340 同步生成中止",
                status="confirmed",
                required=True,
            )
        )
        for heading in ("第一章", "第二章", "第三章"):
            node = OutlineNode(
                project_id=project.id, heading=heading, target_words=50
            )
            session.add(node)
            session.flush()
            session.add(Section(node_id=node.id, body="", status="draft", words=0))
        user = _seed_user(session, "v0340-sync-user")
        billing.add_credits(session, user.id, 10, kind="admin", note="seed")
        project.owner_id = user.id
        session.flush()
        project_id = project.id

    class AbortingClient:
        """Real generation path with a model stub that aborts after chapter 1."""

        available = True
        usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0)
        last_error = ""

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                with session_scope() as session:
                    lease = session.scalar(
                        select(GenerationLease).where(
                            GenerationLease.project_id == project_id
                        )
                    )
                    assert lease is not None
                    jobqueue.release_generation_lease(session, project_id, lease.token)
            return body

    client = AbortingClient()
    monkeypatch.setattr(projects, "_generation_client", lambda db: client)
    monkeypatch.setattr(
        projects, "scoped_knowledge_provider", lambda *a, **k: lambda query: ""
    )
    token = set_current_user({"id": user.id, "role": "super_admin"})
    try:
        with pytest.raises(jobqueue.JobAborted):
            projects.write(project_id)
    finally:
        reset_current_user(token)

    assert client.calls >= 1
    with session_scope() as session:
        written = list(
            session.scalars(
                select(Section.body).join(OutlineNode).where(
                    OutlineNode.project_id == project_id, Section.body != ""
                )
            )
        )
        assert written == []
        assert session.scalar(
            select(func.count()).select_from(GenerationLease).where(
                GenerationLease.project_id == project_id
            )
        ) == 0
    assert _refunds(user.id) == [3]


def test_recover_on_startup_only_requeues_expired_leases(db):
    """Startup recovery must not mass-fail or refund live charged jobs."""
    with session_scope() as session:
        _seed_project(session)
        user = _seed_user(session, "v0340-startup")
        live_id, _ = _seed_job(session, user, expired=False)
        expired_id, _ = _seed_job(session, user, expired=True)

    assert jobqueue.recover_on_startup() == (1, 0)
    with session_scope() as session:
        live = session.get(Job, live_id)
        expired = session.get(Job, expired_id)
        assert live.status == "running"
        assert expired.status == "queued"
        assert bool(live.refund_claimed) is False
        assert bool(expired.refund_claimed) is False
    assert _balance(user.id) == 0
    assert _refunds(user.id) == []
