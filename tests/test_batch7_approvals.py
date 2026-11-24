"""BATCH 7: cross-project workflow approval workbench visibility."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import Project  # noqa: E402
from bidcraft.orchestration import start_workflow  # noqa: E402
from bidcraft.orchestration.approval import request_approval  # noqa: E402
from bidcraft.orchestration.orchestrator import schedule_workflow  # noqa: E402

USER_PASS = "b7-approval-pass"


def _client(username: str = "admin", password: str = "test-admin"):
    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _create_user(admin, username: str):
    resp = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": USER_PASS,
            "role": "manager",
        },
    )
    assert resp.status_code == 201, resp.text
    return _client(username, USER_PASS)


def _project(client, name: str) -> str:
    resp = client.post("/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _pending_gate(project_id: str, goal: str) -> str:
    with session_scope() as db:
        run = start_workflow(db, db.get(Project, project_id), goal=goal)
        schedule_workflow(db, run, owner_id="u-test")
        approval = request_approval(
            db, run, task=None, approval_type="delivery", submitted_by="requester"
        )
        db.flush()
        return approval.id


def test_workflow_approval_workbench_scopes_by_project_membership(db):
    admin = _client()
    lead = _create_user(admin, "b7-approval-lead")
    stranger = _create_user(admin, "b7-approval-stranger")
    project_a = _project(lead, "b7-approval-A")
    project_b = _project(admin, "b7-approval-B")
    gate_a = _pending_gate(project_a, "A 项目目标")
    gate_b = _pending_gate(project_b, "B 项目目标")

    mine = lead.get("/approvals/workflows/pending")
    assert mine.status_code == 200, mine.text
    ids = [item["id"] for item in mine.json()]
    assert gate_a in ids
    assert gate_b not in ids
    by_id = {item["id"]: item for item in mine.json()}
    assert by_id[gate_a]["project_id"] == project_a
    assert by_id[gate_a]["project_name"] == "b7-approval-A"
    assert by_id[gate_a]["goal"] == "A 项目目标"
    assert by_id[gate_a]["approval_type"] == "delivery"
    assert by_id[gate_a]["status"] == "pending"

    # Super admin aggregates across projects; a non-member sees nothing.
    admin_ids = [item["id"] for item in admin.get("/approvals/workflows/pending").json()]
    assert gate_a in admin_ids and gate_b in admin_ids
    assert stranger.get("/approvals/workflows/pending").json() == []

    # The existing quote-version inbox keeps its original flat format.
    assert isinstance(admin.get("/approvals/pending").json(), list)
