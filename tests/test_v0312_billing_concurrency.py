"""v0.31.2 阶段 5：账务竞争的真实 Session / 真实线程 50 轮回归。

每轮都使用独立的用户、任务/标记和独立 SQLAlchemy Session；两个线程由
Barrier 同步起跑，核心条件 UPDATE 不被 mock，也不使用 Python 全局锁替代
数据库 fencing。金额守恒以「用户余额 + 账本退款笔数 + 事件数」为准。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import CreditTransaction, Job, JobEvent, PendingCharge, User
from bidcraft.services.job_support import _fail_job

T = TypeVar("T")
ROUNDS = 50
TOKEN = "v0312-billing-token"


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


def _seed_job(round_no: int, *, done: int = 0, deleted_child: bool = False) -> tuple[str, str, str]:
    """创建已扣 6 点、共 3 章的任务；返回 (job_id, account_id, user_id)。"""
    stamp = f"{round_no}-{uuid4().hex}"
    with session_scope() as db:
        parent = _user(db, f"v0312-bill-parent-{stamp}", credits=4)
        child = parent if not deleted_child else _user(db, f"v0312-bill-child-{stamp}", parent=parent)
        if deleted_child:
            db.flush()
        job = Job(
            kind="generate",
            status="running",
            project_id=f"v0312-billing-{stamp}",
            claim_token=TOKEN,
            worker_id=jobqueue._worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=300),
            payload={
                "charged": 6,
                "chapters": 3,
                "user_id": child.id,
                # 子账号被删除时退款仍应回到父账号。
                "billing_account_id": parent.id,
                "done_steps": [f"chapter:{i}" for i in range(done)],
            },
        )
        db.add(job)
        db.flush()
        return job.id, parent.id, child.id


def _credits(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def _events(job_id: str) -> list[str]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )


def _refund_count(account_id: str, job_id: str) -> int:
    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(CreditTransaction)
                .where(
                    CreditTransaction.user_id == account_id,
                    CreditTransaction.note.contains(f"[job:{job_id}]"),
                )
            )
            or 0
        )


def _pair(first: Callable[[], T], second: Callable[[], T]) -> tuple[list[T], list[BaseException]]:
    """两个线程、两个 Session，Barrier 同步起跑；SQLite 短锁允许重试。"""
    barrier = threading.Barrier(2)
    results: list[T | BaseException] = [None, None]  # type: ignore[list-item]
    errors: list[BaseException] = []

    def run(index: int, fn: Callable[[], T]) -> None:
        barrier.wait()
        for attempt in range(20):
            try:
                results[index] = fn()
                return
            except BaseException as exc:  # noqa: BLE001 - 测试需捕获线程内全部异常
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    time.sleep(0.01)
                    continue
                errors.append(exc)
                return
        errors.append(AssertionError("SQLite 写锁重试 20 次仍失败"))

    threads = [
        threading.Thread(target=run, args=(0, first)),
        threading.Thread(target=run, args=(1, second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return list(results), errors  # type: ignore[return-value]


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_two_threads_refund_once(round_no: int) -> None:
    job_id, account_id, _ = _seed_job(round_no, done=1)

    def refund() -> int:
        with session_scope() as db:
            return jobqueue.refund_charged_job(db, db.get(Job, job_id))

    results, errors = _pair(refund, refund)
    assert not errors
    assert sorted(results) == [0, 4]  # (3-1)*6//3 = 4
    assert _credits(account_id) == 8
    assert _refund_count(account_id, job_id) == 1
    with session_scope() as db:
        assert db.get(Job, job_id).refund_claimed is True


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_reaper_fail_vs_worker_fail(round_no: int) -> None:
    job_id, account_id, _ = _seed_job(round_no, done=1)

    def reaper_fail() -> bool:
        with session_scope() as db:
            return jobqueue._set_failed(db, db.get(Job, job_id), "Reaper 回收")

    def worker_fail() -> None:
        with session_scope() as db:
            _fail_job(db, db.get(Job, job_id), "Worker 失败回调")

    _, errors = _pair(reaper_fail, worker_fail)
    assert not errors
    assert _events(job_id).count("failed") == 1
    assert _credits(account_id) == 8
    assert _refund_count(account_id, job_id) == 1


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_cancel_vs_complete(round_no: int) -> None:
    job_id, account_id, _ = _seed_job(round_no, done=1)

    def cancel() -> bool:
        with session_scope() as db:
            return jobqueue.transition_job(
                db, job_id, from_statuses=("running",), to_status="cancelled"
            ) is not None

    def complete() -> bool:
        # 与真实 worker 一致：终态写入必须在 claim_context 中，输掉竞争时
        # finalize 抛 JobAborted，视为合法的竞争失败而非测试错误。
        try:
            with jobqueue.claim_context(TOKEN):
                jobqueue.finalize_job_result(job_id, status="done", event_kind="done")
            return True
        except jobqueue.JobAborted:
            return False

    _, errors = _pair(cancel, complete)
    assert not errors
    events = _events(job_id)
    # 取消路径本身不发终态事件；完成路径最多一条 done。两条终态事件同时出现
    # 才说明 fencing 失效。
    assert events in ([], ["done"])
    with session_scope() as db:
        assert db.get(Job, job_id).status in ("cancelled", "done")
    # 该场景只验证终态互斥；金额守恒在退款场景中验证。
    assert _credits(account_id) == 4


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_settle_vs_reconcile(round_no: int) -> None:
    stamp = f"{round_no}-{uuid4().hex}"
    with session_scope() as db:
        user = _user(db, f"v0312-marker-{stamp}", credits=10)
        billing.charge_chapters(db, user, 6, note="v0312 marker seed")
        # job: 前缀让 reconciliation 也能从 done_steps 得到与 settle 相同的
        # 已写章节数（1），从而双方竞争时退还金额都是 (3-1)*6//3 = 4。
        job = Job(
            kind="generate",
            status="running",
            project_id=f"v0312-marker-{stamp}",
            claim_token=TOKEN,
            worker_id=jobqueue._worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=300),
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
        marker = jobqueue.begin_pending_charge(
            db,
            user,
            f"v0312-marker-{stamp}",
            6,
            3,
            note=f"job:{job.id}|生成正文",
            sections_at_charge=0,
        )
        user_id, marker_id = user.id, marker

    def settle() -> int:
        with session_scope() as db:
            return jobqueue.settle_pending_charge(db, marker_id, None, "", 3, 1)

    def reconcile() -> int:
        return jobqueue.reconcile_pending_charges(older_than_seconds=0)

    results, errors = _pair(settle, reconcile)
    settle_paid, reconciled = results
    assert not errors
    # settle 返回实际退还点数(4)；reconcile 返回安置的标记数(1)。
    assert (settle_paid, reconciled) in ((4, 0), (0, 1))
    assert _credits(user_id) == 8
    with session_scope() as db:
        assert db.get(PendingCharge, marker_id).status == "refunded"
    assert db_refund_count(user_id, marker_id) == 1


def db_refund_count(user_id: str, marker_id: str) -> int:
    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(CreditTransaction)
                .where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.note.contains(f"[marker:{marker_id}]"),
                )
            )
            or 0
        )


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_deleted_subaccount_refund_during_race(round_no: int) -> None:
    job_id, parent_id, child_id = _seed_job(round_no, deleted_child=True)
    with session_scope() as db:
        db.delete(db.get(User, child_id))

    def refund() -> int:
        with session_scope() as db:
            return jobqueue.refund_charged_job(db, db.get(Job, job_id))

    results, errors = _pair(refund, refund)
    assert not errors
    assert sorted(results) == [0, 6]
    assert _credits(parent_id) == 10
    assert _refund_count(parent_id, job_id) == 1


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_refund_db_error_then_retry(round_no: int, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id, account_id, _ = _seed_job(round_no, done=1)
    real_refund = billing.refund_chapters
    failed = False

    def flaky(*args: Any, **kwargs: Any) -> int:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("账本暂时不可用")
        return real_refund(*args, **kwargs)

    monkeypatch.setattr(billing, "refund_chapters", flaky)
    with session_scope() as db, pytest.raises(RuntimeError, match="账本"):
        jobqueue.refund_charged_job(db, db.get(Job, job_id))
    assert _credits(account_id) == 4
    with session_scope() as db:
        assert db.get(Job, job_id).refund_claimed is False
    monkeypatch.setattr(billing, "refund_chapters", real_refund)
    with session_scope() as db:
        assert jobqueue.refund_charged_job(db, db.get(Job, job_id)) == 4
    assert _credits(account_id) == 8
    assert _refund_count(account_id, job_id) == 1
