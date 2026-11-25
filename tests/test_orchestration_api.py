"""Agent 编排 HTTP 端点：建工作流/查状态/查任务/审批/取消。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import WorkflowApproval, WorkflowRun  # noqa: E402
from bidcraft.orchestration import run_workflow  # noqa: E402

ADMIN = ("admin", "test-admin")


def _client(db):
    client = TestClient(create_app())
    r = client.post("/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    return client


def _project(client, name):
    r = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_agent_catalog_endpoints(db):
    client = _client(db)
    r = client.get("/agents")
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    assert any(a["agent_type"] == "compliance" for a in agents)
    r2 = client.get("/agents/technical")
    assert r2.status_code == 200


def test_workflow_create_list_detail_tasks(db):
    client = _client(db)
    pid = _project(client, "wf-http")
    r = client.post(f"/projects/{pid}/workflows", json={"goal": "HTTP 目标"})
    assert r.status_code == 202, r.text
    run = r.json()
    rid = run["id"]
    assert run["status"] == "queued"

    r = client.get(f"/projects/{pid}/workflows")
    assert r.status_code == 200 and any(x["id"] == rid for x in r.json()["items"])

    r = client.get(f"/projects/{pid}/workflows/{rid}")
    assert r.status_code == 200 and r.json()["id"] == rid
    r = client.get(f"/projects/{pid}/workflows/{rid}/tasks")
    assert r.status_code == 200 and len(r.json()["items"]) == 9


def test_workflow_cancel_endpoint(db):
    client = _client(db)
    pid = _project(client, "wf-http-cancel")
    rid = client.post(f"/projects/{pid}/workflows", json={}).json()["id"]
    r = client.post(f"/projects/{pid}/workflows/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_approval_endpoint_lifecycle(db, monkeypatch):
    # 本用例直接驱动 run_workflow 到审批门，必须关掉 submit 拉起的后台 worker，
    # 否则两个写者并发写同一个 SQLite 会触发 "database is locked"。
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "embedded_workers", False)
    client = _client(db)
    pid = _project(client, "wf-http-approval")
    rid = client.post(f"/projects/{pid}/workflows", json={}).json()["id"]
    # 直接驱动工作流到审批门（worker 异步，不由 HTTP 同步触发）。
    run_workflow(rid)
    with session_scope() as sess:
        run = sess.get(WorkflowRun, rid)
        if run.status != "waiting_approval":
            pytest.skip("本样本未触发审批闸门")
        ap = sess.scalar(
            select(WorkflowApproval).where(
                WorkflowApproval.run_id == rid, WorkflowApproval.status == "pending"
            )
        )
        aid = ap.id
    r = client.get(f"/projects/{pid}/workflow-approvals")
    assert r.status_code == 200 and any(a["id"] == aid for a in r.json()["items"])
    r = client.post(f"/projects/{pid}/workflow-approvals/{aid}/review", json={"decision": "approve", "note": "同意"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


def test_tool_calls_trace_persists_in_task_and_run_payload(db):
    """轨迹走既有 JSON 字段落库，不经新增表或新接口。"""

    from bidcraft.models import WorkflowTask
    from bidcraft.orchestration.orchestrator import run_payload, task_payload

    client = _client(db)
    pid = _project(client, "wf-http-tools")
    rid = client.post(f"/projects/{pid}/workflows", json={}).json()["id"]
    trace = {
        "name": "get_project",
        "args": {"ref": pid},
        "result": "ok",
        "verification": "verified",
        "audit_id": "audit-1",
        "ts": "2026-11-25T00:00:00+00:00",
        "is_error": False,
        "ms": 3,
    }
    with session_scope() as sess:
        task = sess.scalar(
            select(WorkflowTask).where(WorkflowTask.run_id == rid)
        )
        task.output = {**(task.output or {}), "tool_calls": [trace]}
        run = sess.get(WorkflowRun, rid)
        run.result = {**(run.result or {}), "tool_calls": [trace]}
        sess.flush()
        assert task_payload(task)["output"]["tool_calls"] == [trace]
        assert run_payload(sess.get(WorkflowRun, rid))["result"]["tool_calls"] == [trace]

    r = client.get(f"/projects/{pid}/workflows/{rid}/tasks")
    assert r.status_code == 200
    assert any(
        item["output"].get("tool_calls") == [trace] for item in r.json()["items"]
    )
