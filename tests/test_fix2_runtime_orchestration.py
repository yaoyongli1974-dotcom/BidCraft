"""P1：审批通过后必须新建 agent Job，工作流才能真正恢复并走到 done。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.config import settings  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import Job, WorkflowApproval, WorkflowRun  # noqa: E402
from bidcraft.orchestration import run_workflow  # noqa: E402

ADMIN = ("admin", "test-admin")


def _client(db):
    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert resp.status_code == 200, resp.text
    return client


def _project(client, name):
    resp = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _agent_jobs(pid):
    with session_scope() as db:
        return list(
            db.scalars(select(Job).where(Job.project_id == pid, Job.kind == "agent"))
        )


def _pending_approval(rid):
    with session_scope() as db:
        run = db.get(WorkflowRun, rid)
        if run is None or run.status != "waiting_approval":
            return None
        return db.scalar(
            select(WorkflowApproval).where(
                WorkflowApproval.run_id == rid,
                WorkflowApproval.status == "pending",
            )
        )


def test_approval_review_schedules_job_and_run_completes(db, monkeypatch):
    monkeypatch.setattr(settings, "embedded_workers", False)
    client = _client(db)
    pid = _project(client, "fix2-approval-resume")
    rid = client.post(f"/projects/{pid}/workflows", json={}).json()["id"]

    run_workflow(rid)
    approval = _pending_approval(rid)
    if approval is None:
        pytest.skip("本样本未触发审批闸门")

    before = {job.id for job in _agent_jobs(pid)}
    resp = client.post(
        f"/projects/{pid}/workflow-approvals/{approval.id}/review",
        json={"decision": "approve", "note": "同意"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    after = _agent_jobs(pid)
    added = [job for job in after if job.id not in before]
    assert added, "审批通过后应新建一条排队中的 agent Job"
    assert any(job.status == "queued" for job in added)
    assert resp.json().get("job_id") == added[0].id

    # 模拟 worker 认领新 Job 并驱动工作流：可能还有后续审批闸门，逐个批准后
    # 必须能走到 done，而不是卡在 running / waiting_approval。
    job_id = added[0].id
    for _ in range(12):
        with session_scope() as sess:
            status = sess.get(WorkflowRun, rid).status
        if status == "done":
            break
        if status == "waiting_approval":
            gate = _pending_approval(rid)
            assert gate is not None, "挂起中却没有待审项"
            before = {job.id for job in _agent_jobs(pid)}
            approved = client.post(
                f"/projects/{pid}/workflow-approvals/{gate.id}/review",
                json={"decision": "approve", "note": "同意"},
            )
            assert approved.status_code == 200, approved.text
            new_jobs = [job for job in _agent_jobs(pid) if job.id not in before]
            assert new_jobs, "每个审批通过后都必须新建 agent Job"
            job_id = new_jobs[0].id
        run_workflow(rid, job_id=job_id)
    with session_scope() as sess:
        assert sess.get(WorkflowRun, rid).status == "done"


def test_rejected_approval_does_not_schedule_new_job(db, monkeypatch):
    monkeypatch.setattr(settings, "embedded_workers", False)
    client = _client(db)
    pid = _project(client, "fix2-approval-reject")
    rid = client.post(f"/projects/{pid}/workflows", json={}).json()["id"]
    run_workflow(rid)
    approval = _pending_approval(rid)
    if approval is None:
        pytest.skip("本样本未触发审批闸门")

    before = {job.id for job in _agent_jobs(pid)}
    resp = client.post(
        f"/projects/{pid}/workflow-approvals/{approval.id}/review",
        json={"decision": "reject", "note": "不同意"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    assert {job.id for job in _agent_jobs(pid)} == before
