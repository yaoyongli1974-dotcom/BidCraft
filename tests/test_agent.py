from __future__ import annotations

from types import SimpleNamespace

import pytest

from bidcraft.generation.agent import ModelClient
from bidcraft.model_config import Endpoint


def test_ollama_client_uses_native_api(monkeypatch):
    calls = {}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["payload"] = kwargs["json"]
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "message": {"content": "响应正文"},
                "prompt_eval_count": 12,
                "eval_count": 7,
            },
        )

    monkeypatch.setattr("bidcraft.generation.agent.httpx.post", fake_post)
    client = ModelClient(
        Endpoint(
            model_base_url="http://127.0.0.1:11434",
            model_name="qwen2.5:7b",
            model_temperature=0.2,
            provider="ollama",
        )
    )
    body = client.complete([{"role": "user", "content": "你好"}], max_tokens=128)
    assert body == "响应正文"
    assert calls["url"] == "http://127.0.0.1:11434/api/chat"
    assert calls["payload"]["stream"] is False
    assert calls["payload"]["options"]["num_predict"] == 128
    assert client.usage.prompt_tokens == 12
    assert client.usage.completion_tokens == 7


def test_streaming_client_emits_deltas(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"你"}}]}',
                    "",
                    'data: {"choices":[{"delta":{"content":"好"}}]}',
                    "",
                    "data: [DONE]",
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "bidcraft.generation.agent.httpx.stream", lambda *_args, **_kwargs: FakeStream()
    )
    client = ModelClient(
        Endpoint(
            model_base_url="http://localhost:8000/v1",
            model_name="demo",
            provider="openai",
        )
    )
    chunks: list[str] = []
    body = client.complete(
        [{"role": "user", "content": "hi"}], stream=True, on_delta=chunks.append
    )
    assert body == "你好"
    assert chunks == ["你", "好"]


def test_openai_client_still_uses_chat_completions(monkeypatch):
    calls = {}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["payload"] = kwargs["json"]
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": "ok"}}]},
        )

    monkeypatch.setattr("bidcraft.generation.agent.httpx.post", fake_post)
    client = ModelClient(
        Endpoint(
            model_base_url="http://localhost:8000/v1",
            model_name="demo",
            provider="openai",
        )
    )
    assert client.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert calls["url"] == "http://localhost:8000/v1/chat/completions"
    assert calls["payload"]["max_tokens"] > 0


def test_complete_with_tools_parses_tool_calls(monkeypatch):
    """新增的结构化调用解析 tool_calls，且不改变 complete 的字符串契约。"""
    from bidcraft.generation.agent import ModelClient

    calls = {}

    def fake_post(url, **kwargs):
        calls["payload"] = kwargs["json"]
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_project", "arguments": '{"ref": "p1"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    monkeypatch.setattr("bidcraft.generation.agent.httpx.post", fake_post)
    client = ModelClient(
        Endpoint(model_base_url="http://x/v1", model_name="m", model_api_key="k")
    )
    tools = [{"type": "function", "function": {"name": "get_project"}}]
    result = client.complete_with_tools([{"role": "user", "content": "go"}], tools)
    assert calls["payload"]["tools"] == tools
    assert calls["payload"]["tool_choice"] == "auto"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_project"
    assert result.finish_reason == "tool_calls"
    # 原 complete 仍返回字符串（签名不变）
    assert isinstance(client.complete([{"role": "user", "content": "hi"}]), str)


def test_tool_loop_enforces_text_budget():
    """文本超限时明确抛错，不把半截观测伪装成成功。"""

    from bidcraft.generation.agent import ChatResult, ToolCall
    from bidcraft.generation.tool_loop import ToolLoopBudgetError, run_tool_loop

    class _Client:
        def complete_with_tools(self, _messages, _tools, **_kwargs):
            return ChatResult(
                content="",
                tool_calls=[ToolCall(id="c1", name="get_project", arguments={"ref": "p"})],
            )

    calls = []

    def _executor(_name, _args):
        calls.append(True)
        return {"content": [{"type": "text", "text": "x" * 100}]}

    with pytest.raises(ToolLoopBudgetError, match="文本超过上限"):
        run_tool_loop(
            _Client(),
            messages=[],
            tools=[{"name": "get_project"}],
            executor=_executor,
            max_chars=20,
        )
    assert len(calls) == 1
