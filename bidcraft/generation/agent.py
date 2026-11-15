from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from ..config import Settings


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ModelClient:
    """Chat client for OpenAI-compatible endpoints and native Ollama."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = str(getattr(settings, "provider", "openai") or "openai")
        self.usage = Usage()
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self.settings.model_configured

    def _url(self) -> str:
        base = self.settings.model_base_url.rstrip("/")
        if base.endswith(("/chat/completions", "/api/chat")):
            return base
        if self.provider == "ollama":
            return f"{base}/api/chat"
        return f"{base}/chat/completions"

    def _payload(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float | None,
        stream: bool,
    ) -> dict:
        resolved_temperature = (
            self.settings.model_temperature if temperature is None else temperature
        )
        if self.provider == "ollama":
            return {
                "model": self.settings.model_name,
                "messages": messages,
                "stream": stream,
                "options": {
                    "temperature": resolved_temperature,
                    "num_predict": max_tokens,
                },
            }
        return {
            "model": self.settings.model_name,
            "messages": messages,
            "temperature": resolved_temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    def _content(self, data: dict) -> str:
        if self.provider == "ollama":
            return str((data.get("message") or {}).get("content") or "")
        return str(data["choices"][0]["message"]["content"] or "")

    def _usage(self, data: dict) -> None:
        usage = data.get("usage") or {}
        if self.provider == "ollama":
            self.usage.prompt_tokens += int(data.get("prompt_eval_count") or 0)
            self.usage.completion_tokens += int(data.get("eval_count") or 0)
        else:
            self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        temperature: float | None = None,
        stream: bool = False,
        on_delta=None,
    ) -> str:
        if not self.available:
            raise ModelUnavailable("未配置模型端点（BIDCRAFT_MODEL_BASE_URL / BIDCRAFT_MODEL_NAME）")
        payload = self._payload(
            messages, max_tokens=max_tokens, temperature=temperature, stream=stream
        )
        headers = {"Content-Type": "application/json"}
        if self.settings.model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.model_api_key}"

        attempts = 0
        while True:
            attempts += 1
            try:
                if stream:
                    return self._stream(payload, headers, on_delta)
                response = httpx.post(
                    self._url(),
                    json=payload,
                    headers=headers,
                    timeout=self.settings.model_timeout,
                )
                if response.status_code == 429 and attempts < 3:
                    time.sleep(2 * attempts)
                    continue
                response.raise_for_status()
                data = response.json()
                self._usage(data)
                return self._content(data)
            except httpx.HTTPStatusError as exc:
                self.last_error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
                if exc.response.status_code in {429, 500, 502, 503, 504} and attempts < 3:
                    time.sleep(2 * attempts)
                    continue
                raise ModelUnavailable(self.last_error) from exc
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                self.last_error = str(exc)
                if attempts < 2:
                    time.sleep(1)
                    continue
                raise ModelUnavailable(self.last_error) from exc

    def _stream(self, payload: dict, headers: dict[str, str], on_delta) -> str:
        chunks: list[str] = []
        with httpx.stream(
            "POST",
            self._url(),
            json=payload,
            headers=headers,
            timeout=self.settings.model_timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    if self.provider == "ollama":
                        parsed = json.loads(line)
                        if parsed.get("done"):
                            self.usage.prompt_tokens += int(parsed.get("prompt_eval_count") or 0)
                            self.usage.completion_tokens += int(parsed.get("eval_count") or 0)
                        delta = (parsed.get("message") or {}).get("content") or ""
                    else:
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        parsed = json.loads(data)
                        delta = parsed["choices"][0].get("delta", {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    chunks.append(delta)
                    if on_delta:
                        on_delta(delta)
        return "".join(chunks)
