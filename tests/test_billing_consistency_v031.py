"""v0.31 阶段 3（不含业务规则变更）：账务一致性与异步扣费标记测试。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import Job, PendingCharge, User

pytest.importorskip("fastapi")


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


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    return TestClient(create_app())


def test_refund_charged_job_uses_recorded_billing_account(db):
    with session_scope() as session:
        parent = _user(session, "b31-parent", credits=20)
        child = _user(session, "b31-child", parent=parent)
        job = Job(
            kind="generate",
            status="running",
            project_id="p31",
            claim_token="tok",
            payload={
                "charged": 4,
                "chapters": 2,
                "user_id": child.id,
                "billing_account_id": parent.id,
                "done_steps": ["chapter:a"],
            },
        )
        session.add(job)
        session.flush()
        assert jobqueue.refund_charged_job(session, job, written=1) == 2
        parent_id, child_id = parent.id, child.id

    with session_scope() as session:
        # 子账号自己的余额没有被误退；扣费方父账号收到退款。
        assert session.get(User, parent_id).credits == 22
        assert session.get(User, child_id).credits == 0


def test_reconcile_uses_job_ledger_not_project_wide_delta(db):
    with session_scope() as session:
        user = _user(session, "b31-reconcile", credits=6)
        marker = PendingCharge(
            user_id=user.id,
            billing_account_id=user.id,
            project_id="p31-reconcile",
            credits=6,
            chapters=3,
            sections_at_charge=0,
            status="pending",
            note="job:job31|生成全书正文(异步)",
            updated_at=utc_now() - timedelta(days=1),
        )
        session.add(marker)
        job = Job(
            id="job31",
            kind="generate",
            status="done",
            project_id="p31-reconcile",
            payload={"done_steps": ["chapter:a", "chapter:b"], "chapters": 3},
        )
        session.add(job)
        user_id = user.id

    assert jobqueue.reconcile_pending_charges(older_than_seconds=0) == 1
    with session_scope() as session:
        assert session.get(User, user_id).credits == 8  # 6 - ceil(6*2/3)=2 退回
        marker = session.scalar(
            select(PendingCharge).where(PendingCharge.note.like("job:job31%"))
        )
        assert marker is not None and marker.status == "refunded"


def test_negative_reconcile_ttl_is_rejected(db):
    with pytest.raises(ValueError, match="TTL"):
        jobqueue.reconcile_pending_charges(older_than_seconds=-1)


def test_generate_all_records_async_charge_marker(db, monkeypatch):
    """异步生成把扣费 marker 与 Job 绑定；崩溃对账可按 done_steps 精确退款。"""

    from bidcraft.api.routes import projects as routes

    client = _client()
    response = client.post(
        "/auth/register",
        json={
            "username": "b31-async",
            "password": "stage31-pass-1",
            "display_name": "b31-async",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    created = client.post(
        "/projects", json={"name": "b31-async-project", "package": "weakcurrent-cabling"}
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
        rows = session.query(User).filter(User.username == "b31-async").all()
        assert rows
        billing.add_credits(session, rows[0].id, 20, kind="admin", note="seed")

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

    result = client.post(f"/projects/{ref}/generate", json={})
    assert result.status_code == 202, result.text
    job_id = result.json()["job_id"]

    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        marker_id = str((job.payload or {}).get("pending_charge_id") or "")
        assert marker_id
        assert (job.payload or {}).get("billing_account_id")
        marker = session.get(PendingCharge, marker_id)
        assert marker is not None
        assert marker.note.startswith(f"job:{job_id}|")
        assert marker.chapters == 3
