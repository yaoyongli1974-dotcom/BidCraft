"""v0.33.0 RC-02：Agent 工具轨迹脱敏、预算、审批门禁与审计关联。"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from bidcraft import audit, mcp
from bidcraft.db import session_scope
from bidcraft.generation.tool_loop import (
    ToolLoopBudgetError,
    ToolVerificationError,
    run_tool_loop,
)
from bidcraft.models import AuditLog, User
from bidcraft.orchestration import registry
from bidcraft.orchestration.agents.technical import TechnicalAgent
from bidcraft.pipeline import create_project
from bidcraft.security import hash_password

TOOLS = [{"name": "get_project", "description": "读取项目", "parameters": {}}]


class _Call:
    name = "get_project"
    id = "c1"
    raw_arguments = json.dumps({"ref": "p1", "api_key": "sk-1234567890abcdef"})
    arguments: ClassVar[dict[str, str]] = {"ref": "p1"}


def _call(call_id: str = "c1") -> _Call:
    call = _Call()
    call.id = call_id
    return call


class _Client:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = payloads
        self.index = 0

    def complete_with_tools(self, _messages, _tools, **_kwargs):
        payload = self.payloads[min(self.index, len(self.payloads) - 1)]
        self.index += 1
        if isinstance(payload, _Call):
            return SimpleNamespace(content="", tool_calls=[payload], finish_reason="tool_calls")
        return SimpleNamespace(content="完成", tool_calls=[], finish_reason="stop")


class _SleepingClient:
    def complete_with_tools(self, _messages, _tools, **_kwargs):
        time.sleep(0.2)
        return SimpleNamespace(content="完成", tool_calls=[], finish_reason="stop")


def _loop(client, executor, **kwargs):
    return run_tool_loop(
        client,
        messages=[{"role": "user", "content": "核对"}],
        tools=TOOLS,
        executor=executor,
        **kwargs,
    )


def test_trace_args_and_result_are_redacted() -> None:
    secret = "sk-1234567890abcdef"
    result = _loop(
        _Client([_Call(), {"id": "p1", "api_key": secret, "note": "Authorization: Bearer abcdefghijkl"}]),
        lambda _name, _args: {"id": "p1", "api_key": secret},
    )
    trace = result.tool_calls[0]
    assert secret not in json.dumps(trace, ensure_ascii=False)
    assert "REDACTED" in json.dumps(trace, ensure_ascii=False)
    assert trace["args"]["api_key"] == "***REDACTED***"


def test_fail_on_tool_error_rejects_final_text() -> None:
    with pytest.raises(ToolVerificationError, match="工具调用失败"):
        _loop(
            _Client([_Call(), "完成"]),
            lambda _name, _args: {"ok": False, "error": "权限不足"},
            fail_on_tool_error=True,
        )


def test_wall_clock_budget_stops_loop() -> None:
    with pytest.raises(ToolLoopBudgetError, match="秒预算"):
        _loop(_SleepingClient(), lambda _name, _args: {}, max_seconds=0.05)


def test_dedupe_avoids_repeated_same_tool_call() -> None:
    calls: list[tuple[str, dict]] = []

    def executor(name: str, args: dict):
        calls.append((name, args))
        return {"id": "p1", "name": "项目"}

    result = _loop(
        _Client([_call("c1"), _call("c2"), "完成"]),
        executor,
        dedupe=True,
    )
    assert len(calls) == 1
    assert result.tool_calls[1]["result"].startswith("[重复调用，复用首次结果]")


def test_technical_agent_reports_real_token_usage() -> None:
    client = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30))
    payload = TechnicalAgent._usage(client)
    assert payload["tokens"] == 150
    assert payload["usage"] == {"prompt_tokens": 120, "completion_tokens": 30}


def _seed_project(owner_id: str) -> str:
    with session_scope() as db:
        project = create_project(db, "rc02-audit")
        project.owner_id = owner_id
        return project.id


def _owner() -> dict[str, Any]:
    with session_scope() as db:
        user = User(username="rc02-owner", password_hash=hash_password("pass-123"), role="super_admin")
        db.add(user)
        db.flush()
        return {"id": user.id, "username": user.username, "role": user.role}


def test_mutating_builtin_tool_blocked_during_pending_approval(db, monkeypatch) -> None:
    owner = _owner()
    pid = _seed_project(owner["id"])
    monkeypatch.setattr("bidcraft.orchestration.approval.pending_approval_exists", lambda *_: True)
    with pytest.raises(registry.AgentError, match="人工审批"):
        registry.call_tool(
            "run_check",
            {"ref": pid},
            readonly=False,
            actor=owner,
            project_id=pid,
            run_id="pending-run",
        )


def test_audit_failure_fails_tool_and_does_not_fake_success(db, monkeypatch) -> None:
    owner = _owner()
    pid = _seed_project(owner["id"])

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit, "record_audit", fail_audit)
    with pytest.raises(Exception, match="审计写入失败"):
        registry.call_tool_with_trace(
            "get_project", {"ref": pid}, actor=owner, project_id=pid
        )


def test_audit_id_is_not_guessed_from_old_rows(db, monkeypatch) -> None:
    owner = _owner()
    pid = _seed_project(owner["id"])
    with session_scope() as audit_db:
        audit_db.add(
            AuditLog(
                project_id=pid,
                username=owner["username"],
                action="agent.tool.call",
                detail={"tool": "get_project", "name": "get_project", "run_id": "old"},
            )
        )
    monkeypatch.setattr(mcp, "_audit_builtin_call", lambda **_kwargs: None)
    trace = registry.call_tool_with_trace(
        "get_project", {"ref": pid}, actor=owner, project_id=pid, run_id="new"
    )
    assert trace.audit_id == ""
