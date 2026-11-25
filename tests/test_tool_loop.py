"""Phase 4 Agent Tooling：真实 DB / registry / mcp，只桩模型 HTTP 响应。"""

from __future__ import annotations

import json
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bidcraft import mcp
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.generation.agent import ModelClient
from bidcraft.generation.tool_loop import (
    TRACE_KEYS,
    ToolLoopBudgetError,
    run_tool_loop,
)
from bidcraft.model_config import Endpoint
from bidcraft.models import (
    AuditLog,
    OutlineNode,
    Project,
    ProjectMember,
    Section,
    User,
    WorkflowRun,
    WorkflowTask,
)
from bidcraft.orchestration import orchestrator, registry
from bidcraft.orchestration.planner import TOOL_POLICIES, ToolPolicy
from bidcraft.pipeline import create_project
from bidcraft.security import hash_password


def _project(name: str, owner_id: str = "") -> str:
    with session_scope() as db:
        project = create_project(db, name)
        if owner_id:
            project.owner_id = owner_id
        return project.id


def _user(username: str, role: str = "super_admin", projects: dict[str, str] | None = None) -> dict:
    with session_scope() as db:
        user = User(username=username, password_hash=hash_password("pass-123"), role=role)
        db.add(user)
        db.flush()
        for project_id, member_role in (projects or {}).items():
            db.add(ProjectMember(project_id=project_id, user_id=user.id, role=member_role))
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        }


def _model(monkeypatch, responses: list[dict], provider: str = "openai") -> tuple[ModelClient, list]:
    monkeypatch.setattr(settings, "model_base_url", "http://model.local/v1")
    monkeypatch.setattr(settings, "model_api_key", "k")
    monkeypatch.setattr(settings, "model_name", "stub-model")
    monkeypatch.setattr(settings, "offline", False)
    endpoint = Endpoint(
        model_base_url="http://model.local/v1",
        model_api_key="k",
        model_name="stub-model",
        provider=provider,
    )
    sent: list[dict] = []

    def fake_post(url, **kwargs):
        sent.append(dict(kwargs["json"]))
        index = min(len(sent) - 1, len(responses) - 1)
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: responses[index],
        )

    monkeypatch.setattr("bidcraft.generation.agent.httpx.post", fake_post)
    return ModelClient(endpoint), sent


def _openai_call(name: str, arguments: str, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _first_tool_call(name: str, arguments: str, call_id: str = "call_1") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [_openai_call(name, arguments, call_id)],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _final(content: str = "写作要点") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _seed_body(project_id: str) -> None:
    with session_scope() as db:
        node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body="采用 Cat6A 综合布线，满足招标要求。",
                status="written",
                words=20,
                review_status="confirmed",
            )
        )


def _tool(name: str) -> list[dict]:
    return [spec for spec in mcp.TOOL_SPECS if spec["name"] == name]


# --------------------------------------------------------------------------- #


def test_tool_loop_executes_real_registry_tool_and_audits(db, monkeypatch):
    owner = _user("loop-owner")
    pid = _project("loop-real", owner["id"])
    client, sent = _model(
        monkeypatch, [_first_tool_call("get_project", json.dumps({"ref": pid})), _final()]
    )
    executor = partial(
        registry.call_tool_with_trace,
        actor=owner,
        project_id=pid,
        run_id="run-loop",
        task_id="task-loop",
    )
    result = run_tool_loop(
        client,
        messages=[{"role": "user", "content": "核对项目"}],
        tools=_tool("get_project"),
        executor=executor,
        required_tools=("get_project",),
    )

    assert result.content == "写作要点"
    assert result.rounds == 2
    assert len(result.tool_calls) == 1
    trace = result.tool_calls[0]
    assert set(trace) == TRACE_KEYS
    assert trace["name"] == "get_project"
    assert trace["verification"] == "verified"
    assert trace["is_error"] is False
    assert trace["audit_id"]
    assert pid in trace["result"]
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "call_1"
        for message in result.messages
    )
    # 工具观测确实回灌给模型（第二段 HTTP 请求的 messages 中含 role=tool）
    assert any(message.get("role") == "tool" for message in sent[1]["messages"])

    with session_scope() as db_session:
        log = db_session.scalar(
            select(AuditLog).where(AuditLog.action == "agent.tool.call")
        )
        assert log is not None
        assert log.project_id == pid
        assert log.user_id == owner["id"]
        assert log.username == owner["username"]
        assert log.detail["tool"] == "get_project"
        assert log.detail["isError"] is False
        assert log.detail["run_id"] == "run-loop"
        assert log.detail["task_id"] == "task-loop"


def test_cross_project_viewer_denied_and_audited(db, monkeypatch):
    owner = _user("scope-owner")
    pid_a = _project("scope-a", owner["id"])
    viewer = _user("scope-viewer", "viewer", {pid_a: "viewer"})
    pid_b = _project("scope-b", owner["id"])
    client, _sent = _model(
        monkeypatch,
        [
            _first_tool_call("get_project", json.dumps({"ref": pid_b})),
            _final("抱歉，越权读取被拒绝"),
        ],
    )
    executor = partial(
        registry.call_tool_with_trace, actor=viewer, project_id=pid_a, run_id="r-x"
    )
    result = run_tool_loop(
        client,
        messages=[{"role": "user", "content": "读取项目"}],
        tools=_tool("get_project"),
        executor=executor,
    )
    assert result.tool_calls[0]["verification"] == "failed"
    assert result.tool_calls[0]["is_error"] is True
    assert "项目范围不符" in result.tool_calls[0]["result"]

    with pytest.raises(LookupError):  # 绑定项目不可见时按不存在处理
        registry.call_tool(
            "get_project", {"ref": pid_b}, actor=viewer, project_id=pid_b
        )

    with session_scope() as db_session:
        logs = list(
            db_session.scalars(
                select(AuditLog).where(AuditLog.action == "agent.tool.call")
            )
        )
    assert any(log.project_id == pid_a for log in logs)
    assert any(log.detail["isError"] is True for log in logs)
    assert any(log.user_id == viewer["id"] for log in logs)


def test_tool_error_fails_task_with_verification(db, monkeypatch):
    owner = _user("fail-owner")
    pid = _project("fail-project", owner["id"])
    _seed_body(pid)
    policy = ToolPolicy(
        enabled=True,
        allowed=("get_project",),
        readonly=True,
        required=("get_project",),
    )
    monkeypatch.setitem(TOOL_POLICIES["custom"], "technical", policy)
    _model(
        monkeypatch,
        [
            _first_tool_call("get_project", json.dumps({"ref": "ghost-project"})),
            _final("不该成功"),
        ],
    )
    with session_scope() as db_session:
        run = orchestrator.start_workflow(
            db_session,
            db_session.get(Project, pid),
            workflow_type="custom",
            input_payload={
                "tasks": [
                    {
                        "task_key": "technical_draft",
                        "agent_type": "technical",
                        "phase": "draft",
                    }
                ]
            },
        )
        rid = run.id

    result = orchestrator.run_workflow(rid, actor=owner)
    assert result["status"] == "failed"
    assert "必需工具" in result["run"]["result"]["error"]
    with session_scope() as db_session:
        task = db_session.scalar(
            select(WorkflowTask).where(WorkflowTask.run_id == rid)
        )
        assert task.status == "failed"
        traces = (task.output or {}).get("tool_calls") or []
        assert len(traces) == 1
        assert set(traces[0]) == TRACE_KEYS
        assert traces[0]["verification"] == "failed"
        assert traces[0]["is_error"] is True
        assert traces[0]["audit_id"]  # 失败调用同样落真实审计行
        stored = db_session.get(WorkflowRun, rid)
        assert stored.result["tool_calls"] == traces


def test_endless_calls_hit_round_budget(db, monkeypatch):
    owner = _user("budget-owner")
    pid = _project("budget-project", owner["id"])
    client, _sent = _model(
        monkeypatch, [_first_tool_call("get_project", json.dumps({"ref": pid}))]
    )
    seen: list[dict] = []
    with pytest.raises(ToolLoopBudgetError, match="轮上限"):
        run_tool_loop(
            client,
            messages=[{"role": "user", "content": "继续调用"}],
            tools=_tool("get_project"),
            executor=partial(registry.call_tool_with_trace, actor=owner, project_id=pid),
            max_rounds=2,
            on_trace=seen.append,
        )
    assert len(seen) == 2


def test_ollama_done_reason_still_executes_call(db, monkeypatch):
    owner = _user("ollama-owner")
    pid = _project("ollama-project", owner["id"])
    responses = [
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c-ollama",
                        "function": {
                            "name": "get_project",
                            "arguments": {"ref": pid},
                        },
                    }
                ],
            },
            "done_reason": "stop",
        },
        {"message": {"content": "已核对"}, "done_reason": "stop"},
    ]
    client, _sent = _model(monkeypatch, responses, provider="ollama")
    result = run_tool_loop(
        client,
        messages=[{"role": "user", "content": "核对"}],
        tools=_tool("get_project"),
        executor=partial(registry.call_tool_with_trace, actor=owner, project_id=pid),
        required_tools=("get_project",),
    )
    assert result.content == "已核对"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["verification"] == "verified"


def test_malformed_and_unknown_calls_rejected_before_execution(db, monkeypatch):
    owner = _user("malformed-owner")
    pid = _project("malformed-project", owner["id"])
    model_response = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        _openai_call("get_project", "{not json", "c-bad"),
                        _openai_call("drop_all", "{}", "c-unknown"),
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    client, _sent = _model(monkeypatch, [model_response, _final("已拒绝非法调用")])
    executed: list[tuple[str, dict]] = []

    def executor(name, args):
        executed.append((name, args))
        return registry.call_tool_with_trace(
            name, args, actor=owner, project_id=pid
        )

    result = run_tool_loop(
        client,
        messages=[{"role": "user", "content": "试一下"}],
        tools=_tool("get_project"),
        executor=executor,
    )
    assert executed == []
    assert [trace["verification"] for trace in result.tool_calls] == [
        "failed",
        "failed",
    ]
    assert "合法 JSON" in result.tool_calls[0]["result"]
    assert "未知工具" in result.tool_calls[1]["result"]
    assert result.content == "已拒绝非法调用"


def test_mutating_tool_requires_write_permission(db):
    owner = _user("write-owner")
    pid = _project("write-project", owner["id"])
    viewer = _user("write-viewer", "viewer", {pid: "viewer"})
    from bidcraft.authorization import Forbidden

    with pytest.raises(Forbidden):
        registry.call_tool(
            "run_check", {"ref": pid}, readonly=False, actor=viewer, project_id=pid
        )
    with pytest.raises(registry.AgentError, match="写工具"):
        registry._authorize_mutating_tool(None, pid, "")
