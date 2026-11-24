"""BATCH 7: orchestration budget/graph/API/backpressure regressions."""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.config import settings  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import (  # noqa: E402
    Job,
    Project,
    WorkflowApproval,
    WorkflowRun,
    WorkflowTask,
    utcnow,
)
from bidcraft.orchestration import (  # noqa: E402
    approve_task,
    clock_budget,
    resume_workflow,
    run_workflow,
    start_workflow,
)
from bidcraft.orchestration.approval import request_approval  # noqa: E402
from bidcraft.orchestration.orchestrator import schedule_workflow  # noqa: E402
from bidcraft.orchestration.planner import (  # noqa: E402
    WORKFLOW_ALIASES,
    build_workflow,
    canonical_workflow_type,
)
from bidcraft.pipeline import create_project  # noqa: E402

ACTOR = {"id": "u-owner", "username": "owner", "role": "super_admin"}


def _project(name: str) -> str:
    with session_scope() as sess:
        return create_project(sess, name).id


def _start(pid: str, **kw) -> str:
    with session_scope() as sess:
        return start_workflow(sess, sess.get(Project, pid), **kw).id


def _pending_approval(run_id: str) -> str:
    with session_scope() as sess:
        approval = sess.scalar(
            select(WorkflowApproval).where(
                WorkflowApproval.run_id == run_id,
                WorkflowApproval.status == "pending",
            )
        )
    assert approval is not None
    return approval.id


def _client():
    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert resp.status_code == 200, resp.text
    return client


def _project_http(client, name: str) -> str:
    resp = client.post("/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _drive_through_gates(run_id: str) -> dict:
    result = run_workflow(run_id)
    for _ in range(5):
        if result["status"] != "waiting_approval":
            break
        approval_id = _pending_approval(run_id)
        with session_scope() as sess:
            approve_task(sess, approval_id, actor=ACTOR)
        with session_scope() as sess:
            resume_workflow(sess, run_id, actor=ACTOR)
        result = run_workflow(run_id)
    return result


def test_time_budget_excludes_approval_wait(db, monkeypatch):
    """A 61s approval wait with max_seconds=60 must not fail the resumed run."""

    run_id = _start(_project("b7-time"), budget={"max_seconds": 60})
    first = run_workflow(run_id)
    assert first["status"] == "waiting_approval"

    approval_id = _pending_approval(run_id)
    fake_now = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(clock_budget, "utc_now", lambda: fake_now)

    with session_scope() as sess:
        approve_task(sess, approval_id, actor=ACTOR)
    with session_scope() as sess:
        run = resume_workflow(sess, run_id, actor=ACTOR)
        assert run.status == "running"
    # 标准工作流还有第二道交付审批门：挂起等待 61s 后继续推进，全程不应熔断。
    result = _drive_through_gates(run_id)
    assert result["status"] == "done", result
    assert "预算熔断" not in str(result.get("run", {}).get("result") or "")


def test_time_budget_still_enforces_real_execution_time(db):
    """Excluding approval wait must not disable the max_seconds circuit breaker."""

    run_id = _start(_project("b7-time-enforce"), budget={"max_seconds": 60})
    with session_scope() as sess:
        run = sess.get(WorkflowRun, run_id)
        run.stats = {
            **(run.stats or {}),
            "_active_seconds": 61.0,  # clock paused at a gate; 61s actually worked
        }
    result = run_workflow(run_id)
    assert result["status"] == "failed", result
    assert "预算熔断" in str(result["run"]["result"])


def test_delivery_agent_receives_all_upstream_outputs(db):
    """The delivery reviewer must see the four real upstream task outputs."""

    run_id = _start(_project("b7-upstream"))
    result = _drive_through_gates(run_id)
    assert result["status"] == "done", result
    with session_scope() as sess:
        delivery = sess.scalar(
            select(WorkflowTask).where(
                WorkflowTask.run_id == run_id, WorkflowTask.task_key == "delivery"
            )
        )
        assert delivery is not None and delivery.status == "done"
        # AgentOutput.model_dump() -> {"result": {...}, ...}
        output = delivery.output["result"]
        assert output["missing"] == []
        assert output["deliverable"] is True
        assert {
            "tender_parse",
            "technical_draft",
            "commercial_draft",
            "compliance_check",
        } <= set(output["upstream_tasks"])


@pytest.mark.parametrize(
    "body",
    [
        {"workflow_type": "does-not-exist"},
        {"workflow_type": "custom", "input": {"tasks": [{"task_key": "x"}]}},
        {
            "workflow_type": "custom",
            "input": {
                "tasks": [
                    {"task_key": "a", "agent_type": "reviewer", "depends_on": ["b"]},
                    {"task_key": "b", "agent_type": "reviewer", "depends_on": ["a"]},
                ]
            },
        },
        {
            "workflow_type": "custom",
            "input": {"tasks": [{"task_key": "x", "agent_type": "reviewer", "seq": "abc"}]},
        },
    ],
)
def test_invalid_workflow_input_returns_400(db, body):
    client = _client()
    project_id = _project_http(client, "b7-invalid")
    resp = client.post(f"/projects/{project_id}/workflows", json=body)
    assert resp.status_code == 400, resp.text


def test_workflow_create_backpressure_returns_429(db, monkeypatch):
    client = _client()
    project_id = _project_http(client, "b7-backpressure-create")
    monkeypatch.setattr(settings, "max_queued_jobs", 1)
    with session_scope() as sess:
        sess.add(Job(project_id=project_id, kind="evaluation", status="queued", payload={}))
    resp = client.post(f"/projects/{project_id}/workflows", json={})
    assert resp.status_code == 429, resp.text
    with session_scope() as sess:
        assert sess.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id)) is None


def test_workflow_resume_backpressure_returns_429(db, monkeypatch):
    client = _client()
    project_id = _project_http(client, "b7-backpressure-resume")
    run_id = _start(project_id)
    with session_scope() as sess:
        run = sess.get(WorkflowRun, run_id)
        run.status = "waiting_approval"
        schedule_workflow(sess, run, owner_id="u-admin")
    monkeypatch.setattr(settings, "max_queued_jobs", 1)
    resp = client.post(f"/projects/{project_id}/workflows/{run_id}/resume")
    assert resp.status_code == 429, resp.text
    with session_scope() as sess:
        assert sess.get(WorkflowRun, run_id).status == "waiting_approval"


def test_workflow_approve_backpressure_returns_429(db, monkeypatch):
    client = _client()
    project_id = _project_http(client, "b7-backpressure-approve")
    run_id = _start(project_id)
    with session_scope() as sess:
        run = sess.get(WorkflowRun, run_id)
        approval = request_approval(
            sess, run, task=None, approval_type="delivery", submitted_by="requester"
        )
        schedule_workflow(sess, run, owner_id="u-admin")
        approval_id = approval.id
    monkeypatch.setattr(settings, "max_queued_jobs", 1)
    resp = client.post(
        f"/projects/{project_id}/workflow-approvals/{approval_id}/review",
        json={"decision": "approve"},
    )
    assert resp.status_code == 429, resp.text
    with session_scope() as sess:
        assert sess.get(WorkflowApproval, approval_id).status == "pending"


def test_full_and_bid_are_annotated_aliases():
    assert WORKFLOW_ALIASES == {"full": "standard", "bid": "standard"}
    standard = build_workflow("standard", {})
    for alias, canonical in WORKFLOW_ALIASES.items():
        tasks = build_workflow(alias, {})
        assert canonical_workflow_type(alias) == canonical
        assert [t["task_key"] for t in tasks] == [t["task_key"] for t in standard]
        assert [t["depends_on"] for t in tasks] == [t["depends_on"] for t in standard]
        assert all(t["input"]["_workflow_alias"] == alias for t in tasks)
        assert all(t["input"]["_canonical_workflow_type"] == canonical for t in tasks)
