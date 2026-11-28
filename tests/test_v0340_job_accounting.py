"""v0.34.0 P0：失败/退款单一归属、重排队租约恢复与排队任务计费保护。

所有用例都使用真实 SQLite Session / 真实线程与真实条件 UPDATE；只在模型调用
边界替换生成函数，账务、事件、租约和 Job 状态全部从数据库读取断言。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar

import pytest
from sqlalchemy import func, select, update

from bidcraft import billing, jobqueue, jobs
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.generation.writer import GenerationReport
from bidcraft.models import (
    CreditTransaction,
    GenerationLease,
    Job,
    JobEvent,
    OutlineNode,
    PendingCharge,
    Section,
    User,
)

PROJECT_ID = "p-v0340"
TOKEN = "v0340-claim-token"
T = TypeVar("T")


def _user(db, username: str, *, credits: int = 4) -> User:
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


def _seed_charged_job(
    db,
    user: User,
    *,
    status: str = "running",
    token: str | None = TOKEN,
    done: int = 0,
    lease_seconds: int = 300,
    attempts: int = 1,
    project_id: str = PROJECT_ID,
    with_marker: bool = True,
    stale_marker: bool = True,
) -> tuple[Job, str]:
    """已扣 6 点 / 3 章的生成任务，可选关联 job:<id> 扣费标记。"""

    now = utc_now()
    job = Job(
        kind="generate",
        status=status,
        project_id=project_id,
        claim_token=token,
        worker_id=jobqueue._worker_id if token else None,
        claimed_at=now if token else None,
        lease_expires_at=now + timedelta(seconds=lease_seconds) if token else None,
        heartbeat_at=now if token else None,
        attempts=attempts,
        payload={
            "charged": 6,
            "chapters": 3,
            "user_id": user.id,
            "billing_account_id": user.id,
            "done_steps": [f"chapter:{i}" for i in range(done)],
        },
    )
    db.add(job)
    db.flush()
    marker_id = ""
    if with_marker:
        marker = PendingCharge(
            user_id=user.id,
            billing_account_id=user.id,
            project_id=project_id,
            credits=6,
            chapters=3,
            sections_at_charge=0,
            note=f"job:{job.id}|生成正文",
            status="pending",
            updated_at=now - timedelta(hours=1 if stale_marker else 0),
        )
        db.add(marker)
        db.flush()
        marker_id = marker.id
        payload = dict(job.payload or {})
        payload["pending_charge_id"] = marker_id
        job.payload = payload
        db.flush()
    return job, marker_id


def _attach_generation_lease(db, job: Job, project_id: str = PROJECT_ID) -> str:
    token = jobqueue.acquire_generation_lease(
        db, project_id, job_id=job.id, owner_id=str((job.payload or {}).get("user_id") or ""),
        ttl_seconds=300,
    )
    assert token
    payload = dict(job.payload or {})
    payload["generation_lease_token"] = token
    job.payload = payload
    db.flush()
    return token


def _credits(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def _refund_count(user_id: str) -> int:
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


def _events(job_id: str) -> list[str]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )


def _event_count(job_id: str, kind: str) -> int:
    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count(JobEvent.id)).where(
                    JobEvent.job_id == job_id, JobEvent.kind == kind
                )
            )
            or 0
        )


def _retry_on_lock(fn: Callable[[], T]) -> T:
    """SQLite 单写者短暂冲突时重试；不吞掉其他异常。"""

    for _ in range(25):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 仅写锁冲突可重试
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(0.01)
    raise AssertionError("SQLite 写锁重试 25 次仍失败")


def _seed_project_with_ledger(db, name: str) -> Any:
    from bidcraft.ledger import FactItemV032, save_ledger
    from bidcraft.pipeline import create_project
    from bidcraft.schemas import FactLedger

    project = create_project(db, name)
    save_ledger(
        db,
        project.id,
        FactLedger(
            package=project.package,
            facts=[
                FactItemV032(
                    key="project_name",
                    label="项目名称",
                    value="v0340 租约恢复项目",
                    required=True,
                )
            ],
        ),
        actor="cli",
    )
    node = OutlineNode(project_id=project.id, heading="技术方案", target_words=100)
    db.add(node)
    db.flush()
    db.add(Section(node_id=node.id, body="", status="draft", words=0))
    db.flush()
    return project


@pytest.mark.parametrize("round_no", range(6))
def test_fail_job_vs_worker_finalize_single_owner(db, monkeypatch, round_no: int) -> None:
    """并发失败回调与 worker 提交：只有状态转换赢家能写终态和退款。"""

    from bidcraft.services.job_support import _fail_job

    with session_scope() as session:
        user = _user(session, f"v0340-race-{round_no}")
        job, marker_id = _seed_charged_job(session, user, done=1)
        job_id, project_id, user_id = job.id, str(job.project_id), user.id

    real_transition = jobqueue.transition_job
    real_refund = jobqueue.refund_charged_job
    fail_ops: list[str] = []

    def spy_transition(*args: Any, **kwargs: Any) -> Any:
        result = real_transition(*args, **kwargs)
        fail_ops.append("transition:won" if result is not None else "transition:lost")
        return result

    def spy_refund(*args: Any, **kwargs: Any) -> int:
        fail_ops.append("refund")
        return int(real_refund(*args, **kwargs) or 0)

    monkeypatch.setattr(jobqueue, "transition_job", spy_transition)
    monkeypatch.setattr(jobqueue, "refund_charged_job", spy_refund)

    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}
    errors: list[BaseException] = []

    def fail_side() -> None:
        try:
            with session_scope() as session:
                row = session.get(Job, job_id)
                assert row is not None
                barrier.wait(timeout=10)
                # 使用屏障前取得的真实行：模拟持有旧快照的并发失败回调。
                won = _retry_on_lock(lambda: _fail_job(session, row, "并发失败回调"))
            results["fail"] = bool(won)
        except BaseException as exc:  # noqa: BLE001 - 由断言汇总
            errors.append(exc)

    def finalize_side() -> None:
        try:
            barrier.wait(timeout=10)

            def attempt() -> bool:
                with (
                    jobqueue.claim_context(TOKEN),
                    jobqueue.commit_fenced_result(job_id) as (session, row),
                ):
                    row.status = "done"
                    row.progress = 100
                    written = jobqueue._chapter_done_steps(dict(row.payload or {})) or 0
                    jobqueue.settle_pending_charge(
                        session, marker_id, None, project_id, 3, written
                    )
                    jobs.publish_event(
                        job_id, "done", {**jobs.job_payload(row), "ok": True}, db=session
                    )
                return True

            results["finalize"] = _retry_on_lock(attempt)
        except jobqueue.JobAborted:
            results["finalize"] = False
        except BaseException as exc:  # noqa: BLE001 - 由断言汇总
            errors.append(exc)

    threads = [
        threading.Thread(target=fail_side, name="fail"),
        threading.Thread(target=finalize_side, name="finalize"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "并发线程超时"
    assert errors == []
    assert set(results) == {"fail", "finalize"}
    # 退款只能由抢到 failed 状态转换的一方调用；输家不触碰账务。
    assert fail_ops == (
        ["transition:won", "refund"] if results["fail"] else ["transition:lost"]
    )

    kinds = _events(job_id)
    assert len(kinds) == 1
    assert kinds[0] in {"done", "failed"}
    assert _event_count(job_id, "refund_failed") == 0
    assert _credits(user_id) == 8  # 4 seed + (3-1)*6//3
    assert _refund_count(user_id) == 1
    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row is not None
        assert row.status == kinds[0]
        assert row.refund_claimed is True
        marker = session.get(PendingCharge, marker_id)
        assert marker is not None and marker.status in {"refunded", "cleared"}


def test_fail_job_transitions_before_refund(db, monkeypatch) -> None:
    """退款发生时可观察到 Job 已经是 failed；转换顺序不可颠倒。"""

    from bidcraft.services.job_support import _fail_job

    with session_scope() as session:
        user = _user(session, "v0340-order")
        job, _marker_id = _seed_charged_job(session, user)
        job_id, user_id = job.id, user.id

    real_transition = jobqueue.transition_job
    real_refund = jobqueue.refund_charged_job
    observed: list[Any] = []

    def spy_transition(*args: Any, **kwargs: Any) -> Any:
        observed.append("transition")
        return real_transition(*args, **kwargs)

    def spy_refund(db_arg, job_arg, *args: Any, **kwargs: Any) -> int:
        observed.append(("refund", str(job_arg.status)))
        return int(real_refund(db_arg, job_arg, *args, **kwargs) or 0)

    monkeypatch.setattr(jobqueue, "transition_job", spy_transition)
    monkeypatch.setattr(jobqueue, "refund_charged_job", spy_refund)

    with session_scope() as session:
        assert _fail_job(session, session.get(Job, job_id), "订单顺序") is True

    assert observed == ["transition", ("refund", "failed")]
    assert _events(job_id) == ["failed"]
    assert _credits(user_id) == 10
    assert _refund_count(user_id) == 1


@pytest.mark.parametrize("recovery", ["reaper", "startup"])
def test_requeue_clears_stale_generation_lease_token(db, recovery: str) -> None:
    """reaper 与启动恢复重排队时清除陈旧 token 并释放旧生成租约。"""

    with session_scope() as session:
        user = _user(session, f"v0340-requeue-{recovery}")
        job, _marker_id = _seed_charged_job(
            session, user, done=1, lease_seconds=-1
        )
        stale_token = _attach_generation_lease(session, job)
        job_id, user_id = job.id, user.id

    if recovery == "reaper":
        assert jobqueue.reap_expired() == (1, 0)
    else:
        assert jobqueue.recover_on_startup() == (1, 0)

    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row is not None
        assert row.status == "queued"
        assert "generation_lease_token" not in (row.payload or {})
        # 其他 payload 事实必须原样保留（复制字典而不是丢弃字段）。
        assert (row.payload or {}).get("done_steps") == ["chapter:0"]
        assert (row.payload or {}).get("charged") == 6
        assert session.get(GenerationLease, PROJECT_ID) is None
        event = session.scalar(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.kind == "queued")
        )
        assert event is not None
        assert "generation_lease_token" not in (event.data.get("payload") or {})
    assert _event_count(job_id, "queued") == 1
    assert _event_count(job_id, "failed") == 0
    assert _credits(user_id) == 4
    assert stale_token  # 旧 token 确实存在过，而不是测试未铺设


def test_new_worker_reacquires_lease_and_heartbeat_after_requeue(db, monkeypatch) -> None:
    """新 worker 认领后重取生成租约、写回 payload，心跳持续有效直到 done。"""

    import bidcraft.workers.generation as gen_worker

    with session_scope() as session:
        user = _user(session, "v0340-reacquire")
        project = _seed_project_with_ledger(session, "v0340 租约恢复项目")
        job, _marker_id = _seed_charged_job(
            session, user, done=3, lease_seconds=-1, project_id=project.id
        )
        stale_token = _attach_generation_lease(session, job, project.id)
        initial_heartbeat = session.get(GenerationLease, project.id).heartbeat_at
        job_id, project_id, user_id = job.id, project.id, user.id

    assert jobqueue.reap_expired() == (1, 0)
    claimed = jobqueue.claim_next()
    assert claimed is not None and claimed.id == job_id
    assert claimed.claim_token != TOKEN
    new_claim_token = str(claimed.claim_token)

    abort = threading.Event()
    observed: dict[str, Any] = {}

    def fake_generate(db_arg, pid, client, ledger, package, **kwargs):
        with session_scope() as check:
            lease = check.get(GenerationLease, pid)
            assert lease is not None
            token = str(lease.token)
            row = check.get(Job, job_id)
            assert (row.payload or {}).get("generation_lease_token") == token
            assert token != stale_token
            assert row.claim_token == new_claim_token
            assert lease.heartbeat_at > initial_heartbeat
            observed["token"] = token
            observed["expires_at"] = lease.expires_at
            observed["job_lease_until"] = row.lease_expires_at
        for _ in range(5):
            assert jobqueue.renew_generation_lease(pid, observed["token"]) == "active"
            time.sleep(0.03)
        with session_scope() as check:
            assert check.get(GenerationLease, pid).expires_at > observed["expires_at"]
            row = check.get(Job, job_id)
            assert row is not None
            assert row.lease_expires_at > observed["job_lease_until"]
        return GenerationReport(written=[])

    monkeypatch.setattr(gen_worker, "generate_document", fake_generate)
    monkeypatch.setattr(gen_worker, "job_abort_event", lambda: abort)
    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.01)
    monkeypatch.setattr(jobqueue, "_generation_heartbeat_seconds", lambda: 0.01)

    worker = threading.Thread(target=jobqueue._execute, args=(claimed,), name="new-worker")
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive(), "新 worker 线程超时"
    assert not abort.is_set(), "新 worker 不应因租约失效而 abort"

    with session_scope() as session:
        row = session.get(Job, job_id)
        assert row is not None
        assert row.status == "done"
        assert session.get(GenerationLease, project_id) is None
    assert _events(job_id).count("done") == 1
    assert _event_count(job_id, "failed") == 0
    assert _event_count(job_id, "refund_failed") == 0
    assert _event_count(job_id, "queued") == 1  # 恢复一次，不应 abort→fail→重试循环
    assert _credits(user_id) == 4  # done_steps 已覆盖全部章节，零退款
    assert _refund_count(user_id) == 0


def test_cancel_releases_generation_lease_and_refunds_once(db) -> None:
    """用户取消经 _fail_job 单点退款并释放 GenerationLease，重复回调无副作用。"""

    from bidcraft.services import job_support

    with session_scope() as session:
        user = _user(session, "v0340-cancel")
        job, marker_id = _seed_charged_job(session, user, done=1)
        stale_token = _attach_generation_lease(session, job)
        job.updated_at = utc_now() - timedelta(hours=1)
        job_id, user_id = job.id, user.id

    with session_scope() as session:
        assert (
            job_support._cancel_stale_jobs(session, ["generate"], 60, project_id=PROJECT_ID)
            is None
        )
        assert session.get(Job, job_id).status == "failed"
    assert stale_token
    assert _credits(user_id) == 8
    assert _refund_count(user_id) == 1
    assert _events(job_id) == ["failed"]
    with session_scope() as session:
        assert session.get(GenerationLease, PROJECT_ID) is None
        assert session.get(PendingCharge, marker_id).status == "cleared"

    with session_scope() as session:
        assert job_support._fail_job(session, session.get(Job, job_id), "再次取消") is False
    assert _credits(user_id) == 8
    assert _refund_count(user_id) == 1
    assert _events(job_id) == ["failed"]


def test_queued_marker_not_early_refunded_while_job_queued(db) -> None:
    """排队中的任务心跳停止也不能被 TTL 对账提前退款。"""

    with session_scope() as session:
        user = _user(session, "v0340-queued-marker")
        job, marker_id = _seed_charged_job(session, user, status="queued", token=None)
        job_id, user_id = job.id, user.id

    with session_scope() as session:
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session) == 0
        assert session.get(PendingCharge, marker_id).status == "pending"
        assert session.get(Job, job_id).status == "queued"
    assert _credits(user_id) == 4
    assert _refund_count(user_id) == 0

    with session_scope() as session:
        session.get(Job, job_id).status = "failed"
    with session_scope() as session:
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session) == 1
    assert _credits(user_id) == 10
    assert _refund_count(user_id) == 1


def test_queued_marker_ttl_skip_is_bounded(db) -> None:
    """排队保护有上界：远超租约窗口的僵尸排队任务仍会被对账退款。"""

    with session_scope() as session:
        user = _user(session, "v0340-queued-bounded")
        job, marker_id = _seed_charged_job(session, user, status="queued", token=None)
        job_id, user_id = job.id, user.id
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(created_at=utc_now() - timedelta(days=2))
        )
        session.flush()

    with session_scope() as session:
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session) == 1
        assert session.get(PendingCharge, marker_id).status == "refunded"
    assert _credits(user_id) == 10
    assert _refund_count(user_id) == 1
