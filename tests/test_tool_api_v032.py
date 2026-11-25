"""v0.32 Phase 4：Agent 工具目录 API 与工作流工具调用轨迹（真实 DB）。

只覆盖 API/前端契约边界：工具目录的鉴权与响应形状、工作流详情/任务列表
从 ``WorkflowTask.output["tool_calls"]`` 暴露的轨迹；工具循环本身由
``tests/test_tool_loop.py`` 覆盖。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "tool-api-pass-1"
AGENT_TYPE = "technical"

TRACE = {
    "name": "get_ledger",
    "args": {"ref": "p1"},
    "result": {"facts": 2},
    "verification": "verified",
    "audit_id": "audit-1",
    "ts": "2026-11-25T00:00:00+00:00",
    "is_error": False,
    "ms": 12,
}


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_user(admin, username: str, role: str = "manager") -> None:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text


@pytest.fixture()
def tool_team(db):
    """真实 SQLite：owner + viewer + manager 与一个项目。"""

    admin = _login(*ADMIN)
    for username in ("tool-owner", "tool-manager"):
        _make_user(admin, username)
    _make_user(admin, "tool-viewer", role="viewer")
    owner = _login("tool-owner")
    created = owner.post("/projects", json={"name": "工具目录项目", "package": "weakcurrent-cabling"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    added = owner.post(
        f"/projects/{project_id}/members",
        json={"username": "tool-viewer", "role": "viewer"},
    )
    assert added.status_code == 201, added.text
    return {
        "owner": owner,
        "viewer": _login("tool-viewer"),
        "manager": _login("tool-manager"),
        "project_id": project_id,
    }


def _seed_run(project_id: str, *, result: dict, output: dict) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import WorkflowRun, WorkflowTask

    with session_scope() as session:
        run = WorkflowRun(
            project_id=project_id,
            workflow_type="standard",
            status="done",
            goal="验证工具轨迹",
            result=result,
            created_by="tool-owner",
        )
        session.add(run)
        session.flush()
        session.add(
            WorkflowTask(
                run_id=run.id,
                seq=1,
                task_key="write",
                agent_type=AGENT_TYPE,
                status="done",
                output=output,
            )
        )
        session.flush()
        return run.id


def test_agent_tools_requires_authentication(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    response = TestClient(create_app()).get(f"/agents/{AGENT_TYPE}/tools")
    assert response.status_code == 401


def test_agent_tools_with_project_ref_allows_viewer_and_returns_catalog(tool_team):
    response = tool_team["viewer"].get(
        f"/agents/{AGENT_TYPE}/tools?ref={tool_team['project_id']}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"agent_type", "tools"}
    assert body["agent_type"] == AGENT_TYPE
    assert isinstance(body["tools"], list) and body["tools"]
    names = {tool["name"] for tool in body["tools"]}
    assert {"list_projects", "get_project", "get_ledger", "run_check", "export_document"} <= names
    for tool in body["tools"]:
        assert set(tool) == {"name", "description", "input_schema", "side_effect", "readonly"}
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str)
        assert isinstance(tool["input_schema"], dict)
        assert isinstance(tool["readonly"], bool)
        assert tool["side_effect"] in {"none", "write"}
    by_name = {tool["name"]: tool for tool in body["tools"]}
    assert by_name["get_ledger"]["readonly"] is True
    assert by_name["get_ledger"]["side_effect"] == "none"
    assert by_name["export_document"]["readonly"] is False
    assert by_name["export_document"]["input_schema"]["type"] == "object"


def test_agent_tools_without_project_ref_requires_global_write_role(tool_team):
    denied = tool_team["viewer"].get(f"/agents/{AGENT_TYPE}/tools")
    assert denied.status_code == 403
    allowed = tool_team["manager"].get(f"/agents/{AGENT_TYPE}/tools")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["agent_type"] == AGENT_TYPE


def test_agent_tools_unknown_agent_returns_400(tool_team):
    response = tool_team["owner"].get(
        f"/agents/not-a-real-agent/tools?ref={tool_team['project_id']}"
    )
    assert response.status_code == 400


def test_workflow_detail_exposes_tool_calls_from_task_output(tool_team):
    project_id = tool_team["project_id"]
    run_id = _seed_run(project_id, result={"tool_calls": [TRACE]}, output={"tool_calls": [TRACE]})

    response = tool_team["owner"].get(f"/projects/{project_id}/workflows/{run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool_calls"] == [TRACE]
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["tool_calls"] == [TRACE]
    # 原始 output 仍保留轨迹，顶层字段只是 HTTP 边界的便捷投影。
    assert body["tasks"][0]["output"]["tool_calls"] == [TRACE]
    assert set(body["tasks"][0]["tool_calls"][0]) == set(TRACE)


def test_workflow_tasks_trace_is_null_safe(tool_team):
    project_id = tool_team["project_id"]
    run_id = _seed_run(
        project_id,
        result={},
        output={"tool_calls": "not-a-list", "summary": "done"},
    )

    detail = tool_team["owner"].get(f"/projects/{project_id}/workflows/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["tool_calls"] == []
    assert detail.json()["tasks"][0]["tool_calls"] == []

    listed = tool_team["viewer"].get(f"/projects/{project_id}/workflows/{run_id}/tasks")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["tool_calls"] == []
    assert listed.json()["items"][0]["output"]["summary"] == "done"
