"""v0.30 阶段 3：同步扣费心跳、失败任务退款与台账标识的回归测试。"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    return TestClient(create_app())


def _register(username: str, password: str = "stage3-pass-1"):
    client = _client()
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": password,
            "display_name": username,
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return client


def _project(client, name: str) -> str:
    created = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _user_id(db, username: str) -> str:
    from sqlalchemy import select

    from bidcraft.models import User

    return str(db.scalar(select(User.id).where(User.username == username)))


def _credits(username: str) -> int:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import User

    with session_scope() as db:
        return int(db.scalar(select(User.credits).where(User.username == username)) or 0)


def _set_credits(username: str, amount: int) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import User

    with session_scope() as db:
        user_id = _user_id(db, username)
        db.get(User, user_id).credits = amount
        db.flush()
        return user_id


def _seed_ledger(db, project_id: str, *, with_outline: bool = False) -> str | None:
    from bidcraft.models import Fact, OutlineNode, Section

    db.add(
        Fact(
            project_id=project_id,
            key="project.name",
            label="项目名称",
            value="示例项目",
        )
    )
    db.flush()
    if not with_outline:
        return None
    node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
    db.add(node)
    db.flush()
    db.add(
        Section(
            node_id=node.id,
            body="",
            words=0,
            status="written",
            review_status="draft",
        )
    )
    db.flush()
    return node.id


def _report():
    return SimpleNamespace(
        written=["n1"],
        fallback=[],
        failed=[],
        suspicious=[],
        suspicious_count=0,
        knowledge_downgrades=0,
        verification={},
        usage={},
    )


def _marker(project_id: str):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import PendingCharge

    with session_scope() as db:
        rows = list(
            db.scalars(
                select(PendingCharge).where(PendingCharge.project_id == project_id)
            )
        )
        assert len(rows) == 1, rows
        row = rows[0]
        return row.id, row.status, int(row.refunded_credits)


def _install_fast_heartbeat(monkeypatch):
    """Real heartbeat + real touch, but a short interval we can observe in tests."""

    from bidcraft import jobqueue

    real_heartbeat = jobqueue.pending_charge_heartbeat
    real_touch = jobqueue.touch_pending_charge
    calls: list[str] = []
    touches: list[str] = []

    def counting_touch(marker_id):
        touches.append(marker_id)
        return real_touch(marker_id)

    @contextmanager
    def fast_heartbeat(marker_id, **_kwargs):
        calls.append(marker_id)
        with real_heartbeat(marker_id, interval=0.01):
            yield

    monkeypatch.setattr(jobqueue, "touch_pending_charge", counting_touch)
    monkeypatch.setattr(jobqueue, "pending_charge_heartbeat", fast_heartbeat, raising=False)
    return calls, touches


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("endpoint", ["chapter", "write"])
def test_sync_generation_heartbeat_keeps_marker_and_settles_once(endpoint, monkeypatch):
    """长同步生成期间 marker 被心跳续期；成功后只结算一次且不退款。"""
    from bidcraft.api.routes import projects as routes_projects
    from bidcraft.db import session_scope

    username = "stage3-sync-ok-" + endpoint
    client = _register(username)
    project_id = _project(client, "stage3 同步成功项目")
    with session_scope() as db:
        node_id = _seed_ledger(db, project_id, with_outline=True)
    _set_credits(username, 10)

    calls, touches = _install_fast_heartbeat(monkeypatch)
    settle_calls: list[str] = []
    real_settle = routes_projects.jobqueue.settle_pending_charge

    def counting_settle(db, marker_id, user, project_id, chapters, written):
        settle_calls.append(marker_id)
        return real_settle(db, marker_id, user, project_id, chapters, written)

    monkeypatch.setattr(routes_projects.jobqueue, "settle_pending_charge", counting_settle)
    # 外部用户需要可用模型；真正的 generate_document 已被替换，这里只满足门禁。
    monkeypatch.setattr(
        routes_projects, "_generation_client", lambda *_a, **_k: SimpleNamespace(available=True)
    )

    def slow_generate(*_args, **_kwargs):
        time.sleep(0.08)
        return _report()

    monkeypatch.setattr(routes_projects, "generate_document", slow_generate)
    try:
        url = (
            f"/projects/{project_id}/chapters/{node_id}/generate"
            if endpoint == "chapter"
            else f"/projects/{project_id}/write"
        )
        response = client.post(url)
        assert response.status_code == 200, response.text
        assert response.json()["written"] == ["n1"]
    finally:
        monkeypatch.undo()

    marker_id, status, refunded = _marker(project_id)
    assert status == "cleared"
    assert refunded == 0
    assert calls == [marker_id]
    assert touches, "heartbeat 应实际调用 touch_pending_charge"
    assert settle_calls == [marker_id], "只能结算一次"
    assert _credits(username) == 9  # 初始 10 点，成功生成 1 章扣 1 点


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("endpoint", "exc_type"),
    [("chapter", KeyboardInterrupt), ("write", SystemExit)],
)
def test_sync_generation_baseexception_refunds_once(endpoint, exc_type, monkeypatch):
    """BaseException（Ctrl-C/退出）也退还 marker，且不会二次退款。"""
    from bidcraft.api.routes import projects as routes_projects
    from bidcraft.db import session_scope
    from bidcraft.models import PendingCharge

    username = "stage3-sync-fail-" + endpoint
    client = _register(username)
    project_id = _project(client, "stage3 同步中断项目")
    with session_scope() as db:
        node_id = _seed_ledger(db, project_id, with_outline=True)
    _set_credits(username, 10)

    calls, touches = _install_fast_heartbeat(monkeypatch)
    settle_calls: list[str] = []
    real_settle = routes_projects.jobqueue.settle_pending_charge

    def counting_settle(db, marker_id, user, project_id, chapters, written):
        settle_calls.append(marker_id)
        return real_settle(db, marker_id, user, project_id, chapters, written)

    monkeypatch.setattr(routes_projects.jobqueue, "settle_pending_charge", counting_settle)
    monkeypatch.setattr(
        routes_projects, "_generation_client", lambda *_a, **_k: SimpleNamespace(available=True)
    )

    def interrupted(*_args, **_kwargs):
        time.sleep(0.04)
        raise exc_type("interrupted")

    monkeypatch.setattr(routes_projects, "generate_document", interrupted)
    url = (
        f"/projects/{project_id}/chapters/{node_id}/generate"
        if endpoint == "chapter"
        else f"/projects/{project_id}/write"
    )
    try:
        # TestClient/anyio 把 KeyboardInterrupt/SystemExit 转成 CancelledError，
        # 这里只要求异常确实穿透回来；退款与结算在路由内部已完成。
        with pytest.raises(BaseException, match=".*"):
            client.post(url)
    finally:
        monkeypatch.undo()

    marker_id, status, refunded = _marker(project_id)
    assert status == "refunded"
    assert refunded == 1
    assert calls == [marker_id]
    assert touches, "中断前心跳应已至少触碰一次 marker"
    assert settle_calls == [marker_id], "异常结算只能发生一次"
    assert _credits(username) == 10

    # 再次结算是 no-op：marker 已不在 pending，余额不再变化。
    with session_scope() as db:
        again = routes_projects.jobqueue.settle_pending_charge(
            db, marker_id, None, project_id, 1, 0
        )
    assert again == 0
    assert _credits(username) == 10
    with session_scope() as db:
        marker = db.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 1


@pytest.mark.usefixtures("db")
def test_stale_charged_generate_job_refunded_once():
    """_cancel_stale_jobs 通过 _fail_job 退款；再次 _fail_job 不会二次退款。"""
    from sqlalchemy import func, select, update

    from bidcraft import billing
    from bidcraft.clock import utc_now
    from bidcraft.db import session_scope
    from bidcraft.jobs import publish_event
    from bidcraft.models import CreditTransaction, Job, JobEvent, User
    from bidcraft.services.job_support import _cancel_stale_jobs, _fail_job

    username = "stage3-stale"
    client = _register(username)
    project_id = _project(client, "stage3 过期任务项目")
    user_id = _set_credits(username, 10)
    # 真实先扣 3 点，再由失败路径退还，最终余额回到 10。
    with session_scope() as db:
        assert billing.charge_chapters(db, db.get(User, user_id), 3, project_id=project_id) == 3

    with session_scope() as db:
        job = Job(
            project_id=project_id,
            kind="generate",
            status="running",
            payload={
                "owner_id": user_id,
                "user_id": user_id,
                "charged": 3,
                "chapters": 3,
            },
            claim_token="hung-token",
            lease_expires_at=utc_now() + timedelta(hours=1),
            worker_id="hung-worker",
            heartbeat_at=utc_now(),
        )
        db.add(job)
        db.flush()
        job_id = job.id
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(updated_at=utc_now() - timedelta(hours=2))
        )

    with session_scope() as db:
        assert (
            _cancel_stale_jobs(
                db,
                ["generate"],
                30 * 60,
                project_id=project_id,
                stale_message="生成任务超时中断",
            )
            is None
        )
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.claim_token is None
        assert job.lease_expires_at is None
        assert job.worker_id is None
        assert job.heartbeat_at is None

    def refund_count():
        with session_scope() as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(CreditTransaction)
                    .where(
                        CreditTransaction.user_id == user_id,
                        CreditTransaction.kind == "refund",
                    )
                )
                or 0
            )

    assert refund_count() == 1
    assert _credits(username) == 10
    with session_scope() as db:
        refund = db.scalar(
            select(CreditTransaction).where(
                CreditTransaction.user_id == user_id,
                CreditTransaction.kind == "refund",
            )
        )
        assert refund is not None and refund.note == "生成未完成退还[job:" + job_id + "]"
        job = db.get(Job, job_id)
        assert job.payload["refunded_credits"] == 3
        event = db.scalar(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.kind == "failed")
        )
        assert event is not None and event.data["refunded_credits"] == 3

    # 第二次失败（重试/reaper）是 no-op。
    with session_scope() as db:
        _fail_job(db, db.get(Job, job_id), "再次失败")
    assert refund_count() == 1
    assert _credits(username) == 10

    # 旧 worker 拿着过期 claim_token，fenced_job 不再返回任务，无法写 done。
    from bidcraft import jobqueue

    with jobqueue.claim_context("hung-token"), session_scope() as db:
        assert jobqueue.fenced_job(db, job_id) is None
        fenced = jobqueue.fenced_job(db, job_id)
        if fenced is not None:
            publish_event(job_id, "done", {"status": "done"}, db=db)
    with session_scope() as db:
        assert not list(
            db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.kind == "done"))
        )


@pytest.mark.usefixtures("db")
def test_fail_job_refund_error_emits_refund_failed_and_still_fails(monkeypatch):
    """退款异常时仍落 failed，同时留下 refund_failed 事件供对账。"""
    from sqlalchemy import select

    from bidcraft import jobqueue
    from bidcraft.db import session_scope
    from bidcraft.jobs import job_payload
    from bidcraft.models import Job, JobEvent
    from bidcraft.services.job_support import _fail_job

    def boom(*_args, **_kwargs):
        raise RuntimeError("ledger offline")

    monkeypatch.setattr(jobqueue, "refund_charged_job", boom)

    with session_scope() as db:
        job = Job(
            kind="generate",
            status="running",
            payload={"charged": 2, "chapters": 2, "user_id": "missing-user"},
        )
        db.add(job)
        db.flush()
        job_id = job.id

    with session_scope() as db:
        job = db.get(Job, job_id)
        _fail_job(db, job, "生成失败")
        assert job_payload(job)["status"] == "failed"

    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.result == {"error": "生成失败"}
        events = list(
            db.scalars(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )
        assert [event.kind for event in events] == ["failed", "refund_failed"]
        assert "ledger offline" in str(events[1].data)


@pytest.mark.usefixtures("db")
def test_refund_chapters_origin_marker_preserves_ledger_refs():
    """origin 只进入 note 标记，不改 ref_type/ref_id，也不影响默认文案。"""
    from sqlalchemy import select

    from bidcraft import billing
    from bidcraft.db import session_scope
    from bidcraft.models import CreditTransaction

    username = "stage3-billing"
    client = _register(username)
    project_id = _project(client, "stage3 台账项目")
    user_id = _set_credits(username, 10)

    with session_scope() as db:
        from bidcraft.models import User

        user = db.get(User, user_id)
        billing.refund_chapters(db, user, 2, project_id=project_id, origin="job:abc")
        row = db.scalar(
            select(CreditTransaction)
            .where(
                CreditTransaction.user_id == user_id,
                CreditTransaction.kind == "refund",
            )
            .order_by(CreditTransaction.created_at.desc(), CreditTransaction.id.desc())
        )
        assert row is not None
        assert row.note == "生成未完成退还[job:abc]"
        assert row.ref_type == "project"
        assert row.ref_id == project_id

        billing.refund_chapters(db, user, 1, project_id=project_id)
        rows = list(
            db.scalars(
                select(CreditTransaction)
                .where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.kind == "refund",
                )
                .order_by(CreditTransaction.created_at.desc(), CreditTransaction.id.desc())
            )
        )
        assert rows[0].note == "生成未完成退还"
        assert rows[0].ref_type == "project"
        assert rows[0].ref_id == project_id
