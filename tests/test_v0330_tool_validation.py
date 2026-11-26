"""v0.33.0 RC-01：工具结果验证必须以真实结构化结果为准。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from bidcraft.generation.tool_loop import (
    ToolVerificationError,
    _verification,
    run_tool_loop,
)

TOOLS = [{"name": "get_project", "description": "读取项目", "parameters": {}}]


class _Call:
    def __init__(self, name: str = "get_project", call_id: str = "c1") -> None:
        self.name = name
        self.id = call_id
        self.raw_arguments = json.dumps({"ref": "p1"})
        self.arguments = {"ref": "p1"}


class _Client:
    """按预设回应返回模型结果；只桩模型，不桩验证逻辑。"""

    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = payloads
        self.index = 0

    def complete_with_tools(self, _messages, _tools, **_kwargs):
        payload = self.payloads[min(self.index, len(self.payloads) - 1)]
        self.index += 1
        if isinstance(payload, _Call):
            return SimpleNamespace(content="", tool_calls=[payload], finish_reason="tool_calls")
        return SimpleNamespace(content="完成", tool_calls=[], finish_reason="stop")


def _run(payload: Any, *, required: bool = True) -> Any:
    client = _Client([_Call(), payload])
    return run_tool_loop(
        client,
        messages=[{"role": "user", "content": "核对"}],
        tools=TOOLS,
        executor=lambda _name, _args: payload,
        required_tools=("get_project",) if required else (),
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"id": "p1", "name": "项目"}, "verified"),
        ({"issues": [{"level": "major"}], "summary": {"total": 1}}, "verified"),
        ([{"kb_id": "kb-1", "text": "条款"}], "verified"),
        ("已核对", "unverified"),
        ("", "unverified"),
        ({"message": "已核对"}, "unverified"),
        ({"ok": False, "error": "配额不足"}, "failed"),
        ({"success": False, "message": "未写入"}, "failed"),
        ({"status": "failed", "issues": []}, "failed"),
        ({"content": [{"text": "执行失败：越权"}]}, "failed"),
        ("工具执行失败：不存在", "failed"),
        (["失败：无权访问"], "failed"),
        ({"status": "ok", "meta": {"note": "failed to parse"}}, "verified"),
    ],
)
def test_verification_semantics(payload: Any, expected: str) -> None:
    assert _verification(payload, is_error=False) == expected


@pytest.mark.parametrize(
    "payload",
    [
        "工具执行失败",
        {"ok": False, "error": "配额不足"},
        {"status": "failed", "issues": []},
        {"message": "已完成但没有结构化证据"},
    ],
)
def test_required_tool_cannot_pass_on_non_verifiable_result(payload: Any) -> None:
    with pytest.raises(ToolVerificationError, match="必需工具"):
        _run(payload)


def test_structured_risk_findings_still_count_as_verifiable_tool_result() -> None:
    """verified = 工具证据可核验，不等于业务结论通过。"""

    result = _run({"issues": [{"level": "blocking"}], "summary": {"blocking": 1}})
    assert result.tool_calls[0]["verification"] == "verified"
    assert result.tool_calls[0]["is_error"] is False


def test_required_tool_with_any_failed_attempt_is_rejected() -> None:
    client = _Client([_Call("get_project", "c1"), _Call("get_project", "c2"), "完成"])
    results = iter([{"ok": False, "error": "第一次失败"}, {"id": "p1", "name": "项目"}])
    with pytest.raises(ToolVerificationError, match="必需工具执行失败"):
        run_tool_loop(
            client,
            messages=[{"role": "user", "content": "核对两次"}],
            tools=TOOLS,
            executor=lambda _name, _args: next(results),
            required_tools=("get_project",),
        )
