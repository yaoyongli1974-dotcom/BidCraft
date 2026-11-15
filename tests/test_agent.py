from __future__ import annotations

from types import SimpleNamespace

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
