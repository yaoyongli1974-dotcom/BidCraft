"""v0.30 Stage 3 核心：按 marker 心跳对账、章节基线、DB 层退款认领。"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import (
    CreditTransaction,
    Job,
    OutlineNode,
    PendingCharge,
    Project,
    Section,
    User,
)


def _user(db, username: str, *, credits: int = 20) -> User:
    user = User(
        username=username,
        password_hash="x",
        role="manager",
        is_external=True,
        credits=0,
    )
    db.add(user)
    db.flush()
    if credits:
        billing.add_credits(db, user.id, credits, kind="admin", note="seed")
    return user


def _stale_marker(db, marker_id: str, *, days: int = 1) -> None:
    marker = db.get(PendingCharge, marker_id)
    marker.updated_at = utc_now() - timedelta(days=days)
    db.flush()


def _refund_rows(db, user_id: str) -> list[CreditTransaction]:
    return list(
        db.scalars(
            select(CreditTransaction).where(
                CreditTransaction.user_id == user_id,
                CreditTransaction.kind == "refund",
            )
        )
    )


def _seed_section(db, node: OutlineNode, body: str) -> Section:
    section = Section(node_id=node.id, body=body, words=len(body))
    db.add(section)
    db.flush()
    return section


def _retry_on_lock(fn):
    """SQLite 并发：写锁短暂冲突时重试（生产环境由 PG 行锁处理）。"""

    for _ in range(20):
        try:
            return fn()
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.05)
    raise AssertionError("SQLite 一直锁住，并发用例无法推进")


# --- 心跳口径：created_at 无关，updated_at 说了算 -----------------------------


def test_fresh_updated_at_survives_reconcile_then_stale_refunds_once(db):
    with session_scope() as session:
        user = _user(session, "v3-ttl")
        marker_id = jobqueue.begin_pending_charge(session, user, "p-ttl", 6, 3)
        billing.add_credits(session, user.id, -6, kind="consume", note="charge")
        marker = session.get(PendingCharge, marker_id)
        marker.created_at = utc_now() - timedelta(days=30)  # 老创建时间不再有意义
        marker.updated_at = utc_now()  # 心跳仍然新鲜
        user_id = user.id

    # TTL 内的 marker（哪怕 created_at 很老）不能被误退。
    assert jobqueue.reconcile_pending_charges(older_than_seconds=60) == 0
    with session_scope() as session:
        assert session.get(PendingCharge, marker_id).status == "pending"
        assert session.get(User, user_id).credits == 14

    with session_scope() as session:
        _stale_marker(session, marker_id, days=1)

    assert jobqueue.reconcile_pending_charges(older_than_seconds=60) == 1
    # 第二次对账：条件 UPDATE 抢占失败，完全无操作。
    assert jobqueue.reconcile_pending_charges(older_than_seconds=60) == 0

    with session_scope() as session:
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 6
        assert session.get(User, user_id).credits == 20
        rows = _refund_rows(session, user_id)
        assert len(rows) == 1
        assert rows[0].amount == 6
        assert rows[0].ref_type == "project" and rows[0].ref_id == "p-ttl"
        assert f"marker:{marker_id}" in rows[0].note  # G8-6: 可追溯 origin


def test_unrelated_live_job_does_not_block_marker_refund(db):
    with session_scope() as session:
        user = _user(session, "v3-live")
        marker_id = jobqueue.begin_pending_charge(session, user, "p-shared", 4, 2)
        _stale_marker(session, marker_id)
        # 同项目上有一个无关的在途 Job：按 charge 粒度判定，它不应保护这个 marker。
        session.add(
            Job(
                kind="generate",
                status="running",
                project_id="p-shared",
                payload={"charged": 99, "chapters": 3, "user_id": user.id},
            )
        )
        user_id = user.id

    assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
    with session_scope() as session:
        assert session.get(PendingCharge, marker_id).status == "refunded"
        assert session.get(User, user_id).credits == 24


# --- 章节基线：只退扣费之后新增的章节 ----------------------------------------


def _seed_project_with_sections(db, project_id: str, count: int) -> list[OutlineNode]:
    db.add(Project(id=project_id, name="stage3", owner_id="u-owner"))
    db.flush()
    nodes = []
    for index in range(count):
        node = OutlineNode(
            project_id=project_id, heading=f"节点 {index}", position=index
        )
        db.add(node)
        db.flush()
        _seed_section(db, node, f"正文 {index}")
        nodes.append(node)
    return nodes


def test_sections_at_charge_baseline_refunds_only_new_chapters(db):
    project_id = "p-baseline"
    with session_scope() as session:
        user = _user(session, "v3-baseline")
        existing = _seed_project_with_sections(session, project_id, 3)
        billing.add_credits(
            session, user.id, -8, kind="consume", note="charge", ref_type="project",
            ref_id=project_id,
        )
        marker_id = jobqueue.begin_pending_charge(session, user, project_id, 8, 4)
        marker = session.get(PendingCharge, marker_id)
        assert marker.sections_at_charge == 3  # 扣费瞬间自动快照
        # 显式传值仍被尊重。
        explicit = jobqueue.begin_pending_charge(
            session, user, project_id, 1, 1, sections_at_charge=99
        )
        assert session.get(PendingCharge, explicit).sections_at_charge == 99
        session.delete(session.get(PendingCharge, explicit))

        # 模拟扣费后又落库了 2 章：4 章里只有 2 章属于本次扣费。
        for index in range(2):
            node = OutlineNode(
                project_id=project_id, heading=f"新增 {index}", position=10 + index
            )
            session.add(node)
            session.flush()
            _seed_section(session, node, f"新增正文 {index}")
        _stale_marker(session, marker_id)
        assert len(existing) == 3
        user_id = user.id

    with session_scope() as session:
        before = session.get(User, user_id).credits
        assert before == 12  # 20 - 8
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
        after = session.get(User, user_id).credits
        assert after == 16  # 退 2/4 章 = 4 点，不是全额 8 点
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 4
        rows = _refund_rows(session, user_id)
        assert len(rows) == 1 and rows[0].amount == 4
        assert f"marker:{marker_id}" in rows[0].note


def test_sections_at_charge_clears_marker_when_all_new_chapters_written(db):
    project_id = "p-cleared"
    with session_scope() as session:
        user = _user(session, "v3-cleared")
        _seed_project_with_sections(session, project_id, 3)
        billing.add_credits(session, user.id, -4, kind="consume", note="charge")
        marker_id = jobqueue.begin_pending_charge(session, user, project_id, 4, 2)
        # 扣费后 2 章全部落库 → written=2 → 退款 0 → 标记 cleared。
        for index in range(2):
            node = OutlineNode(
                project_id=project_id, heading=f"完成 {index}", position=10 + index
            )
            session.add(node)
            session.flush()
            _seed_section(session, node, f"完成正文 {index}")
        _stale_marker(session, marker_id)
        user_id = user.id

    with session_scope() as session:
        before = session.get(User, user_id).credits
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "cleared" and marker.refunded_credits == 0
        assert session.get(User, user_id).credits == before
        assert _refund_rows(session, user_id) == []


# --- DB 认领：并发退款只能成功一次 -------------------------------------------


def test_concurrent_refund_charged_job_refunds_once(db):
    with session_scope() as session:
        user = _user(session, "v3-concurrent")
        billing.add_credits(session, user.id, -9, kind="consume", note="charge")
        job = Job(
            kind="generate",
            status="running",
            project_id="p-concurrent",
            payload={"charged": 9, "chapters": 3, "user_id": user.id},
        )
        session.add(job)
        session.flush()
        job_id, user_id = job.id, user.id

    barrier = threading.Barrier(2)
    results: list[int] = []
    errors: list[Exception] = []

    def claim():
        barrier.wait(timeout=5)

        def attempt():
            with session_scope() as session:
                row = session.get(Job, job_id)
                value = jobqueue.refund_charged_job(session, row)
                session.flush()
                return value

        try:
            results.append(_retry_on_lock(attempt))
        except Exception as exc:  # noqa: BLE001 - 汇总到断言里统一报告
            errors.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    assert sorted(results) == [0, 9], results

    with session_scope() as session:
        assert session.get(User, user_id).credits == 20
        row = session.get(Job, job_id)
        assert row.refund_claimed is True
        assert row.payload["refunded"] is True and row.payload["refunded_credits"] == 9
        rows = _refund_rows(session, user_id)
        assert len(rows) == 1 and rows[0].amount == 9
        assert f"job:{job_id}" in rows[0].note  # G8-6: 可追溯 origin
        assert rows[0].ref_type == "project" and rows[0].ref_id == "p-concurrent"


def test_refund_charged_job_claims_even_when_nothing_due(db):
    with session_scope() as session:
        user = _user(session, "v3-claimed")
        job = Job(
            kind="generate",
            status="done",
            project_id="p-claimed",
            payload={
                "charged": 9,
                "chapters": 3,
                "user_id": user.id,
                "done_steps": ["chapter:a", "chapter:b", "chapter:c"],
            },
        )
        session.add(job)
        session.flush()
        job_id = job.id

    with session_scope() as session:
        assert jobqueue.refund_charged_job(session, session.get(Job, job_id)) == 0
        session.flush()
        row = session.get(Job, job_id)
        assert row.refund_claimed is True and row.payload["refunded"] is True

    with session_scope() as session:
        # 认领后即便 done_steps 被清空也不会再退第二次。
        row = session.get(Job, job_id)
        row.payload = {**row.payload, "done_steps": []}
        assert jobqueue.refund_charged_job(session, row) == 0


# --- 心跳线程本身 ------------------------------------------------------------


def test_touch_pending_charge_only_touches_pending(db):
    with session_scope() as session:
        user = _user(session, "v3-touch")
        marker_id = jobqueue.begin_pending_charge(session, user, "p-touch", 4, 2)
        _stale_marker(session, marker_id, days=1)
        original = session.get(PendingCharge, marker_id).updated_at

    assert jobqueue.touch_pending_charge("") is False
    assert jobqueue.touch_pending_charge(marker_id) is True
    with session_scope() as session:
        touched = session.get(PendingCharge, marker_id).updated_at
        assert touched > original
        session.get(PendingCharge, marker_id).status = "cleared"
        session.flush()

    assert jobqueue.touch_pending_charge(marker_id) is False


def test_heartbeat_refreshes_updated_at_while_active_and_stops_on_exit(db):
    with session_scope() as session:
        user = _user(session, "v3-heartbeat")
        marker_id = jobqueue.begin_pending_charge(session, user, "p-hb", 4, 2)
        _stale_marker(session, marker_id, days=1)
        original = session.get(PendingCharge, marker_id).updated_at

    # 空 marker_id 必须安全（billing-exempt 调用方拿到的是空串）。
    with jobqueue.pending_charge_heartbeat("", interval=0.01):
        pass

    with jobqueue.pending_charge_heartbeat(marker_id, interval=0.01):
        time.sleep(0.15)
        with session_scope() as session:
            during = session.get(PendingCharge, marker_id).updated_at
    assert during > original

    with session_scope() as session:
        stopped = session.get(PendingCharge, marker_id).updated_at
    time.sleep(0.08)  # 远大于 interval：若线程没停，updated_at 还会继续变化
    with session_scope() as session:
        assert session.get(PendingCharge, marker_id).updated_at == stopped
