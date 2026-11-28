"""v0.34.0 P1：真实 PostgreSQL 上的退款锁顺序与唯一入账验证（opt-in）。

未设置 ``BIDCRAFT_TEST_PG_URL`` 时整模块干净跳过。每个用例使用独立线程 +
独立 Session + Barrier 起跑，所有锁与条件 UPDATE 都由生产代码发出；账本写入
只有短暂延迟注入以放大竞争窗口，不替换任何数据库操作。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql, sqlite

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import CreditTransaction, Job, PendingCharge, Project, User

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL（专用测试库）")

ROUNDS = 20


@contextmanager
def _pg_session():
    """Real PG session with a bounded statement timeout for deadlock detection."""
    with session_scope() as db:
        db.execute(text("SET LOCAL statement_timeout = '10000'"))
        yield db


def _seed(db):
    project_id = f"pg-v0340-{uuid.uuid4().hex[:8]}"
    db.add(Project(id=project_id, name="PG refund ordering", owner_id=None))
    db.flush()
    user = User(
        username=f"pg34_{uuid.uuid4().hex[:8]}",
        display_name="PG refund",
        password_hash="test-only",
        role="manager",
        is_external=True,
        credits=0,
    )
    db.add(user)
    db.flush()
    # 先为该测试账户注入恰好 6 点，扣费后才可能得到「余额 4」的退款断言口径。
    billing.add_credits(db, user.id, 6, kind="recharge", note="PG refund seed")
    charged = billing.charge_chapters(
        db, user, 6, project_id=project_id, note="PG refund ordering"
    )
    assert charged == 6
    job = Job(
        id=str(uuid.uuid4()),
        kind="generate",
        project_id=project_id,
        status="running",
        attempts=1,
        max_attempts=5,
        claim_token="pg34-token",
        worker_id=jobqueue._worker_id,
        claimed_at=utc_now(),
        lease_expires_at=utc_now() + timedelta(seconds=300),
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
    marker_id = jobqueue.begin_pending_charge(
        db, user, project_id, 6, 3, note=f"job:{job.id}|PG refund ordering"
    )
    assert marker_id
    job.payload = {**dict(job.payload or {}), "pending_charge_id": marker_id}
    db.flush()
    return project_id, user.id, job.id, marker_id


def _pair(left, right):
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(name, fn):
        try:
            barrier.wait(timeout=10)
            results[name] = fn()
        except BaseException as exc:  # noqa: BLE001 - report real PG errors
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("left", left), name="pg34-refund-left"),
        threading.Thread(target=run, args=("right", right), name="pg34-refund-right"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=45)
        assert not thread.is_alive(), "PG 并发线程未在 45 秒内结束（疑似死锁）"
    assert not errors, f"unexpected thread errors: {errors!r}"
    return results["left"], results["right"]


def _refund_state(user_id, marker_id, job_id):
    with session_scope() as db:
        balance = int(db.get(User, user_id).credits or 0)
        positives = list(
            db.scalars(
                select(CreditTransaction).where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.kind == "refund",
                    CreditTransaction.amount > 0,
                )
            )
        )
        marker = db.get(PendingCharge, marker_id)
        row = db.get(Job, job_id)
        return {
            "balance": balance,
            "amounts": [int(tx.amount) for tx in positives],
            "notes": [str(tx.note or "") for tx in positives],
            "marker_status": marker.status,
            "refund_claimed": bool(row.refund_claimed),
        }


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_job_refund_vs_marker_settle_no_deadlock_one_refund(pg_engine, monkeypatch, round_no):
    with session_scope() as db:
        project_id, user_id, job_id, marker_id = _seed(db)

    real_refund = billing.refund_chapters

    def delayed_refund(db, payee, credits, **kwargs):
        time.sleep(0.02)
        return real_refund(db, payee, credits, **kwargs)

    monkeypatch.setattr(jobqueue.billing, "refund_chapters", delayed_refund)

    def by_job():
        with _pg_session() as db:
            return jobqueue.refund_charged_job(db, db.get(Job, job_id))

    def by_marker():
        with _pg_session() as db:
            return jobqueue.settle_pending_charge(db, marker_id, None, project_id, 3, 0)

    first, second = _pair(by_job, by_marker)
    assert first + second == 4, (first, second)
    state = _refund_state(user_id, marker_id, job_id)
    assert state["balance"] == 4
    assert state["amounts"] == [4]
    assert state["marker_status"] in {"refunded", "cleared"}
    assert state["refund_claimed"] is True
    assert len(state["notes"]) == 1
    assert "[job:" in state["notes"][0] or "[marker:" in state["notes"][0]


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_job_refund_vs_reconcile_no_deadlock_one_refund(pg_engine, monkeypatch, round_no):
    with session_scope() as db:
        _project_id, user_id, job_id, marker_id = _seed(db)
        marker = db.get(PendingCharge, marker_id)
        marker.updated_at = utc_now() - timedelta(hours=2)

    real_refund = billing.refund_chapters

    def delayed_refund(db, payee, credits, **kwargs):
        time.sleep(0.02)
        return real_refund(db, payee, credits, **kwargs)

    monkeypatch.setattr(jobqueue.billing, "refund_chapters", delayed_refund)

    def by_job():
        with _pg_session() as db:
            return jobqueue.refund_charged_job(db, db.get(Job, job_id))

    def by_reconcile():
        with _pg_session() as db:
            return jobqueue.reconcile_pending_charges(older_than_seconds=0, db=db)

    first, second = _pair(by_job, by_reconcile)
    assert first + (4 * second) == 4, (first, second)
    state = _refund_state(user_id, marker_id, job_id)
    assert state["balance"] == 4
    assert state["amounts"] == [4]
    assert state["refund_claimed"] is True
    assert len(state["notes"]) == 1


def test_pg_refund_claims_compile_on_both_dialects(pg_engine):
    """Production claim statements must remain SQLite/PG portable."""
    statements = (
        jobqueue._claim_job_refund_statement("J"),
        jobqueue._release_job_refund_statement("J"),
        jobqueue._lock_marker_statement("M"),
    )
    for statement in statements:
        assert "UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
        assert "UPDATE" in str(statement.compile(dialect=sqlite.dialect()))
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
