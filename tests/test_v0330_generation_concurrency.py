"""RC-05：生成任务并发/账务单一归属与 fencing 回归（先于生产修复编写）。"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import (
    CreditTransaction,
    GenerationLease,
    Job,
    OutlineNode,
    PendingCharge,
    Section,
    User,
)


def _user(db, username: str, *, credits: int = 0) -> User:
    row = User(
        username=username,
        password_hash="x",
        role="manager",
        is_external=True,
        credits=0,
    )
    db.add(row)
    db.flush()
    if credits:
        billing.add_credits(db, row.id, credits, kind="admin", note="seed")
    return row


def _job(
    db,
    user: User,
    *,
    charged: int = 6,
    chapters: int = 3,
    done: int = 0,
    marker_id: str = "",
    lease_token: str = "",
) -> Job:
    row = Job(
        kind="generate",
        status="running",
        project_id="p33-rc05",
        claim_token="rc05-token",
        worker_id="rc05-worker",
        claimed_at=utc_now(),
        lease_expires_at=utc_now() + timedelta(minutes=5),
        payload={
            "charged": charged,
            "chapters": chapters,
            "user_id": user.id,
            "billing_account_id": user.id,
            "done_steps": [f"chapter:{i}" for i in range(done)],
            "pending_charge_id": marker_id,
            "generation_lease_token": lease_token,
        },
    )
    db.add(row)
    db.flush()
    return row


def _marker(db, user: User, *, note: str, credits: int = 6, chapters: int = 3) -> str:
    marker = PendingCharge(
        user_id=user.id,
        billing_account_id=user.id,
        project_id="p33-rc05",
        credits=credits,
        chapters=chapters,
        sections_at_charge=0,
        note=note,
        status="pending",
        updated_at=utc_now() - timedelta(hours=1),
    )
    db.add(marker)
    db.flush()
    return marker.id


def _credits(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def _refund_count(user_id: str) -> int:
    from sqlalchemy import func

    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count(CreditTransaction.id)).where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.amount > 0,
                    CreditTransaction.kind == "refund",
                )
            )
            or 0
        )


def _marker_status(marker_id: str) -> str:
    with session_scope() as db:
        return str(db.get(PendingCharge, marker_id).status)


def test_marker_settle_then_fail_job_refunds_once(db) -> None:
    """Marker ledger wins first; the job-level ledger must not pay again."""
    from bidcraft.services.job_support import _fail_job

    with session_scope() as session:
        user = _user(session, "rc05-marker-first", credits=4)
        marker_id = _marker(session, user, note="job:J-MARKER-FIRST|生成")
        job = _job(session, user, marker_id=marker_id)
        job_id = job.id
        session.flush()
        marker = session.get(PendingCharge, marker_id)
        marker.note = f"job:{job_id}|生成"
        paid = jobqueue.settle_pending_charge(session, marker_id, user, "p33-rc05", 3, 0)
        assert paid == 6
        _fail_job(session, session.get(Job, job_id), "重复失败回调")
    assert _credits(user.id) == 10
    assert _refund_count(user.id) == 1
    assert _marker_status(marker_id) in {"refunded", "cleared"}


def test_job_refund_first_then_reconcile_does_not_refund_twice(db) -> None:
    """Job ledger wins first; stale marker reconciliation must be a no-op."""
    from bidcraft.services.job_support import _fail_job

    with session_scope() as session:
        user = _user(session, "rc05-job-first", credits=4)
        marker_id = _marker(session, user, note="job:J-JOB-FIRST|生成")
        job = _job(session, user, marker_id=marker_id)
        job_id = job.id
        session.flush()
        session.get(PendingCharge, marker_id).note = f"job:{job_id}|生成"
        _fail_job(session, session.get(Job, job_id), "worker 失败")
    assert _credits(user.id) == 10
    with session_scope() as session:
        settled = jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session)
    assert settled == 0
    assert _credits(user.id) == 10
    assert _refund_count(user.id) == 1
    assert _marker_status(marker_id) == "cleared"


def test_cancel_stale_jobs_lost_transition_keeps_job_for_caller(db, monkeypatch) -> None:
    """A lost failure race must not hand the caller a second enqueue slot."""
    from bidcraft.services import job_support

    with session_scope() as session:
        user = _user(session, "rc05-cancel-race")
        job = _job(session, user, charged=0)
        job.updated_at = utc_now() - timedelta(hours=1)
        job_id = job.id

    monkeypatch.setattr(jobqueue, "transition_job", lambda *a, **k: None)
    with session_scope() as session:
        result = job_support._cancel_stale_jobs(session, ["generate"], 60, project_id="p33-rc05")
    assert result is not None
    assert result.id == job_id


def test_writer_fail_chapter_hook_keeps_fenced_write_owned(db) -> None:
    """ModelUnavailable failures must go through the worker's fenced hook."""
    from types import SimpleNamespace

    from bidcraft.generation.agent import ModelUnavailable
    from bidcraft.generation.writer import generate_document
    from bidcraft.pipeline import create_project
    from bidcraft.rules.loader import load_package
    from bidcraft.schemas import FactLedger

    with session_scope() as session:
        project = create_project(session, "rc05 失败钩子项目")
        node = OutlineNode(project_id=project.id, heading="技术方案", target_words=1200)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="", status="draft", words=0))
        ref = project.id
        package = project.package

    calls: list[dict] = []

    def boom(*args, **kwargs):
        raise ModelUnavailable("RC05 强制模型失败")

    client = SimpleNamespace(
        available=True,
        complete=boom,
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )
    with session_scope() as session:
        generate_document(
            session,
            ref,
            client,
            FactLedger(package=package),
            load_package(package),
            fail_chapter=lambda info: calls.append(dict(info)),
        )
    assert calls
    assert calls[0]["heading"] == "技术方案"
    with session_scope() as session:
        section = session.scalar(select(Section).join(OutlineNode).where(OutlineNode.project_id == ref))
        assert section is not None
        assert section.status != "failed"  # long-session write is gone


def test_force_pending_chapters_ignores_confirmed_and_locked(db) -> None:
    from bidcraft.api.shared import _pending_chapters
    from bidcraft.pipeline import create_project

    with session_scope() as session:
        project = create_project(session, "rc05 已确认章节项目")
        for status in ("confirmed", "locked"):
            node = OutlineNode(project_id=project.id, heading=f"章节-{status}", target_words=100)
            session.add(node)
            session.flush()
            session.add(Section(node_id=node.id, body="正文", status=status, review_status=status, words=100))
        ref = project.id
    with session_scope() as session:
        assert _pending_chapters(session, ref, force=True) == 0


def test_heartbeat_tolerates_two_transient_failures_then_aborts_on_three(db, monkeypatch) -> None:
    calls: list[int] = []

    def fake_renew(job_id: str, token: str) -> bool:
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("transient")
        return True

    monkeypatch.setattr(jobqueue, "_renew_lease_once", fake_renew)
    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.01)
    stop = threading.Event()
    abort = threading.Event()
    thread = threading.Thread(
        target=jobqueue._heartbeat, args=("job-1", stop, "token", abort), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 2
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=2)
    assert len(calls) >= 3
    assert not abort.is_set()

    always: list[int] = []

    def always_fail(job_id: str, token: str) -> bool:
        always.append(1)
        raise RuntimeError("persistent")

    monkeypatch.setattr(jobqueue, "_renew_lease_once", always_fail)
    stop2 = threading.Event()
    abort2 = threading.Event()
    thread2 = threading.Thread(
        target=jobqueue._heartbeat, args=("job-2", stop2, "token", abort2), daemon=True
    )
    thread2.start()
    deadline = time.monotonic() + 2
    while not abort2.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    stop2.set()
    thread2.join(timeout=2)
    assert abort2.is_set()
    assert len(always) >= 3


def test_claim_statement_skip_locked_only_on_postgres() -> None:
    now = utc_now()
    statement = jobqueue._claim_statement(now, now, "token", skip_locked=True)
    pg_sql = str(statement.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(statement.compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE" in pg_sql
    assert "SKIP LOCKED" in pg_sql
    assert "SKIP LOCKED" not in sqlite_sql
    assert "RETURNING" in pg_sql


def test_fail_job_releases_persisted_generation_lease(db) -> None:
    from bidcraft.services.job_support import _fail_job

    with session_scope() as session:
        user = _user(session, "rc05-lease-release")
        token = jobqueue.acquire_generation_lease(
            session, "p33-rc05", job_id="lease-job", owner_id=user.id, ttl_seconds=300
        )
        assert token
        job = _job(session, user, charged=0, lease_token=token)
        job_id = job.id
        _fail_job(session, session.get(Job, job_id), "失败并释放租约")
    with session_scope() as session:
        remaining = session.scalar(
            select(GenerationLease.project_id).where(
                GenerationLease.project_id == "p33-rc05"
            )
        )
    assert remaining is None


def test_mark_job_refund_settled_is_single_owner(db) -> None:
    with session_scope() as session:
        user = _user(session, "rc05-refund-owner")
        job = _job(session, user)
        job_id = job.id
    with session_scope() as session:
        assert jobqueue.mark_job_refund_settled(session, job_id, source="marker") is True
        assert jobqueue.mark_job_refund_settled(session, job_id, source="job") is False
        row = session.get(Job, job_id)
        assert row.refund_claimed is True
        assert row.payload["refunded"] is True
        assert row.payload["refund_source"] == "marker"
