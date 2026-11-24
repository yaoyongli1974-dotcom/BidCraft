"""Batch 1 · PendingCharge：TTL 存活判定与子账号退款落点。"""

from __future__ import annotations

from datetime import timedelta

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import Job, PendingCharge, User


def _user(db, username: str, *, credits: int = 20, parent=None) -> User:
    user = User(
        username=username,
        password_hash="x",
        role="manager",
        is_external=True,
        credits=0,
        parent_id=parent.id if parent else None,
    )
    db.add(user)
    db.flush()
    if credits:
        billing.add_credits(db, user.id, credits, kind="admin", note="seed")
    return user


def _old_marker(db, user: User, *, credits: int = 4, chapters: int = 2) -> str:
    marker_id = jobqueue.begin_pending_charge(db, user, "p-live", credits, chapters)
    marker = db.get(PendingCharge, marker_id)
    # 心跳以 updated_at 为准；陈旧 marker 必须连心跳也停止。
    marker.created_at = utc_now() - timedelta(days=1)
    marker.updated_at = utc_now() - timedelta(days=1)
    db.flush()
    return marker_id


def test_reconcile_ignores_unrelated_live_job(db):
    """旧语义按项目级 live job 阻塞；新语义只认本笔扣费的心跳。

    同项目上的无关任务不应阻止一笔已经停止心跳的 marker 被对账退款。
    """
    with session_scope() as session:
        user = _user(session, "b1-live")
        marker_id = _old_marker(session, user)
        job = Job(kind="generate", status="running", project_id="p-live", payload={})
        session.add(job)
        user_id = user.id

    assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
    with session_scope() as session:
        assert session.get(PendingCharge, marker_id).status == "refunded"
        assert session.get(User, user_id).credits == 24


def test_reconcile_refunds_after_live_job_finishes(db):
    with session_scope() as session:
        user = _user(session, "b1-done")
        marker_id = _old_marker(session, user)
        job = Job(kind="generate", status="running", project_id="p-live", payload={})
        session.add(job)
        session.flush()
        job.status = "done"
        user_id = user.id

    assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
    with session_scope() as session:
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 4
        assert session.get(User, user_id).credits == 24


def test_reconcile_refunds_parent_after_sub_account_deleted(db):
    with session_scope() as session:
        parent = _user(session, "b1-parent")
        sub = _user(session, "b1-sub", credits=0, parent=parent)
        charged = billing.charge_chapters(session, sub, 3, project_id="p-sub")
        assert charged == 3
        marker_id = jobqueue.begin_pending_charge(session, sub, "p-sub", 3, 3)
        marker = session.get(PendingCharge, marker_id)
        assert marker.billing_account_id == parent.id
        marker.created_at = utc_now() - timedelta(days=1)
        marker.updated_at = utc_now() - timedelta(days=1)
        session.flush()
        parent_id, sub_id = parent.id, sub.id

    with session_scope() as session:
        session.delete(session.get(User, sub_id))
        session.flush()

    assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
    with session_scope() as session:
        assert session.get(User, parent_id).credits == 20  # 子账号扣的 3 点退回主账号
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 3


def test_reconcile_refuses_to_clear_when_no_payable_account(db):
    marker_id = "orphan-marker"
    with session_scope() as session:
        marker = PendingCharge(
            id=marker_id,
            user_id="deleted-user",
            billing_account_id="deleted-parent",
            project_id="p-orphan",
            credits=4,
            chapters=2,
            status="pending",
            created_at=utc_now() - timedelta(days=1),
            updated_at=utc_now() - timedelta(days=1),
        )
        session.add(marker)

    assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 0
    with session_scope() as session:
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "pending"  # 留给后续重试，绝不零退款清账
        assert marker.refunded_credits == 0


def test_settle_uses_recorded_billing_account_for_sub_account(db):
    with session_scope() as session:
        parent = _user(session, "b1-parent-settle")
        sub = _user(session, "b1-sub-settle", credits=0, parent=parent)
        billing.charge_chapters(session, sub, 3, project_id="p-sub")
        marker_id = jobqueue.begin_pending_charge(session, sub, "p-sub", 3, 3)
        parent_id, sub_id = parent.id, sub.id

    with session_scope() as session:
        user = session.get(User, sub_id)
        refund = jobqueue.settle_pending_charge(session, marker_id, user, "p-sub", 3, 0)
        session.flush()
        assert refund == 3
        assert session.get(User, parent_id).credits == 20
        assert session.get(PendingCharge, marker_id).status == "refunded"
