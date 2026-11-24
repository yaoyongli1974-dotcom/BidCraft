"""v0.31.1 阶段 5：取消/陈旧 worker/重复退款的账务竞争场景。"""

from __future__ import annotations

from sqlalchemy import select

from bidcraft import billing, jobqueue
from bidcraft.db import session_scope
from bidcraft.models import Job, JobEvent, User
from bidcraft.services.job_support import _fail_job


def _user(db, username: str, *, credits: int = 0, parent=None) -> User:
    row = User(
        username=username,
        password_hash="x",
        role="manager",
        is_external=True,
        credits=0,
        parent_id=parent.id if parent else None,
    )
    db.add(row)
    db.flush()
    if credits:
        billing.add_credits(db, row.id, credits, kind="admin", note="seed")
    return row


def _job(db, *, user: User, charged: int = 6, chapters: int = 3, done: int = 0) -> Job:
    row = Job(
        kind="generate",
        status="running",
        project_id="p31-stage5",
        claim_token="stage5-token",
        payload={
            "charged": charged,
            "chapters": chapters,
            "user_id": user.id,
            "billing_account_id": user.id,
            "done_steps": [f"chapter:{i}" for i in range(done)],
        },
    )
    db.add(row)
    db.flush()
    return row


def _events(job_id: str) -> list[str]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )


def _credits(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def test_cancel_refunds_once_even_if_fail_job_repeated(db) -> None:
    with session_scope() as writer:
        user = _user(writer, "s5-cancel", credits=4)  # 6 点已扣，余额应为 4
        job = _job(writer, user=user, charged=6, chapters=3)
        job_id = job.id
        _fail_job(writer, job, "用户取消")
    assert _credits(user.id) == 10  # 3*2 points refunded
    with session_scope() as writer:
        _fail_job(writer, writer.get(Job, job_id), "重复取消")
    assert _credits(user.id) == 10
    assert _events(job_id) == ["failed"]


def test_stale_worker_failure_does_not_refund_earned_chapters(db) -> None:
    with session_scope() as writer:
        user = _user(writer, "s5-stale", credits=4)
        job = _job(writer, user=user, charged=6, chapters=3, done=3)
        job_id = job.id
        _fail_job(writer, job, "陈旧 worker 失败回调")
    assert _credits(user.id) == 4
    with session_scope() as reader:
        assert reader.get(Job, job_id).refund_claimed is True


def test_deleted_subaccount_refunds_to_billing_account(db) -> None:
    with session_scope() as writer:
        parent = _user(writer, "s5-parent", credits=4)
        child = _user(writer, "s5-child", parent=parent)
        job = _job(writer, user=child, charged=6, chapters=3)
        job.payload = {**(job.payload or {}), "billing_account_id": parent.id}
        db_child_id = child.id
        db_parent_id = parent.id
        job_id = job.id
        _fail_job(writer, job, "子账号任务失败")
        writer.delete(writer.get(User, db_child_id))
    assert _credits(db_parent_id) == 10
    with session_scope() as reader:
        assert reader.get(Job, job_id).status == "failed"


def test_refund_failure_emits_event_and_leaves_claim_retryable(db, monkeypatch) -> None:
    with session_scope() as writer:
        user = _user(writer, "s5-refund-fail", credits=4)
        job = _job(writer, user=user, charged=6, chapters=3)
        job_id = job.id
        user_id = user.id

        def boom(*args, **kwargs):
            raise RuntimeError("账本不可用")

        monkeypatch.setattr(billing, "refund_chapters", boom)
        _fail_job(writer, job, "任务失败")
    assert _credits(user_id) == 4  # debit stays; nothing refunded
    assert _events(job_id) == ["failed", "refund_failed"]
    monkeypatch.undo()
    with session_scope() as writer:
        row = writer.get(Job, job_id)
        assert row.refund_claimed is False
        assert jobqueue.refund_charged_job(writer, row) == 6
    assert _credits(user_id) == 10
