"""v0.31 阶段 3：同项目并发生成互斥租约（用户批准方案 A）。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import GenerationLease, Job, User

pytest.importorskip("fastapi")


def test_generation_lease_is_exclusive_and_reclaimable(db):
    project_id = "p31-lease"
    with session_scope() as session:
        first = jobqueue.acquire_generation_lease(
            session, project_id, job_id="job-a", owner_id="owner-a", ttl_seconds=60
        )
        assert first
        assert jobqueue.acquire_generation_lease(session, project_id, job_id="job-b") is None
        assert jobqueue.touch_generation_lease(session, project_id, first) is True
        lease = session.get(GenerationLease, project_id)
        assert lease is not None and lease.token == first
        # 模拟崩溃：心跳不再刷新，租约过期后下一次请求可回收。
        lease.expires_at = utc_now() - timedelta(seconds=1)
    with session_scope() as session:
        second = jobqueue.acquire_generation_lease(
            session, project_id, job_id="job-b", owner_id="owner-b", ttl_seconds=60
        )
        assert second and second != first
        assert jobqueue.release_generation_lease(session, project_id, "wrong-token") is False
        assert jobqueue.release_generation_lease(session, project_id, second) is True
        assert session.get(GenerationLease, project_id) is None


def test_async_generate_rejects_second_request_while_lease_held(db, monkeypatch):
    """已持有租约时再请求生成 → 409，且不会扣费/建 Job。"""

    from bidcraft.api.routes import projects as routes
    from tests.test_billing_consistency_v031 import _client

    client = _client()
    response = client.post(
        "/auth/register",
        json={
            "username": "b31-lease-user",
            "password": "stage31-lease-pass",
            "display_name": "b31-lease-user",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    created = client.post(
        "/projects", json={"name": "b31-lease-project", "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    ref = created.json()["id"]

    from bidcraft.db import session_scope as scope
    from bidcraft.models import Fact

    with scope() as session:
        session.add(
            Fact(
                project_id=ref,
                key="project.name",
                label="项目名称",
                value="示例项目",
            )
        )
        row = session.scalar(select(User).where(User.username == "b31-lease-user"))
        assert row is not None
        billing.add_credits(session, row.id, 20, kind="admin", note="seed")
        balance_before = row.credits

    monkeypatch.setattr(routes, "_cancel_stale_jobs", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "_enqueue_or_429", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "_credit_guard", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "_pending_chapters", lambda *a, **kw: 3)
    monkeypatch.setattr(
        routes,
        "_generation_client",
        lambda *a, **kw: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(jobqueue, "submit", lambda *a, **kw: None)

    with session_scope() as session:
        token = jobqueue.acquire_generation_lease(
            session, ref, job_id="held", owner_id="holder", ttl_seconds=60
        )
        assert token

    blocked = client.post(f"/projects/{ref}/generate", json={})
    assert blocked.status_code == 409, blocked.text
    assert "已有生成任务" in blocked.json().get("detail", "")

    with session_scope() as session:
        assert session.scalar(select(Job).where(Job.project_id == ref)) is None
        row = session.scalar(select(User).where(User.username == "b31-lease-user"))
        assert row is not None and row.credits == balance_before
        jobqueue.release_generation_lease(session, ref, token)

    allowed = client.post(f"/projects/{ref}/generate", json={})
    assert allowed.status_code == 202, allowed.text
    job_id = allowed.json()["job_id"]
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert (job.payload or {}).get("generation_lease_token")


def test_two_concurrent_generate_requests_only_one_charges(db, monkeypatch):
    """真正并发的两个异步请求只有一个拿到租约并扣费。"""

    from bidcraft.api.routes import projects as routes
    from tests.test_billing_consistency_v031 import _client

    client = _client()
    assert (
        client.post(
            "/auth/register",
            json={
                "username": "b31-race-user",
                "password": "stage31-race-pass",
                "display_name": "b31-race-user",
                "accept_terms": True,
            },
        ).status_code
        == 201
    )
    created = client.post(
        "/projects", json={"name": "b31-race-project", "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    ref = created.json()["id"]

    from bidcraft.db import session_scope as scope
    from bidcraft.models import Fact

    with scope() as session:
        session.add(Fact(project_id=ref, key="project.name", label="项目名称", value="示例"))
        row = session.scalar(select(User).where(User.username == "b31-race-user"))
        assert row is not None
        billing.add_credits(session, row.id, 50, kind="admin", note="seed")

    barrier = threading.Barrier(2)
    monkeypatch.setattr(routes, "_cancel_stale_jobs", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "_enqueue_or_429", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "_credit_guard", lambda *a, **kw: barrier.wait(timeout=10))
    monkeypatch.setattr(routes, "_pending_chapters", lambda *a, **kw: 3)
    monkeypatch.setattr(
        routes,
        "_generation_client",
        lambda *a, **kw: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(jobqueue, "submit", lambda *a, **kw: None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: client.post(f"/projects/{ref}/generate", json={}), range(2))
        )
    codes = sorted(response.status_code for response in responses)
    assert codes == [202, 409], [response.text for response in responses]

    with session_scope() as session:
        jobs = session.scalars(select(Job).where(Job.project_id == ref)).all()
        assert len(jobs) == 1
        assert (jobs[0].payload or {}).get("generation_lease_token")
        assert session.scalar(select(GenerationLease).where(GenerationLease.project_id == ref))
