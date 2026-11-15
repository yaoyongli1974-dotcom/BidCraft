"""Resolve admin-managed model endpoints and test connectivity."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from .config import settings
from .models import ModelConfig
from .security import decrypt_secret

TIMEOUT = 20.0


@dataclass(slots=True)
class Endpoint:
    """Shape-compatible with :class:`bidcraft.config.Settings`."""

    model_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    model_temperature: float = 0.4
    model_timeout: float = 180.0
    provider: str = "openai"
    source: str = "env"
    config_id: str = ""
    config_name: str = ""

    @property
    def model_configured(self) -> bool:
        return bool(self.model_base_url and self.model_name)

    @property
    def label(self) -> str:
        if self.config_name:
            return f"{self.config_name} / {self.model_name}"
        return self.model_name


def api_base(value: str) -> str:
    base = (value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/images/generations", "/embeddings", "/models"):
        base = base.removesuffix(suffix)
    return base


def endpoint_from_config(config: ModelConfig) -> Endpoint:
    return Endpoint(
        model_base_url=api_base(config.base_url),
        model_api_key=decrypt_secret(config.api_key_enc),
        model_name=config.model,
        model_temperature=float(config.temperature or 0.4),
        model_timeout=float((config.options or {}).get("timeout") or 180),
        provider=config.provider or "openai",
        source="database",
        config_id=config.id,
        config_name=config.name,
    )


def resolve_chat_endpoint(db) -> Endpoint | None:
    if settings.offline:
        return None
    config = db.scalar(
        select(ModelConfig)
        .where(
            ModelConfig.kind == "chat",
            ModelConfig.enabled.is_(True),
            ModelConfig.is_default.is_(True),
        )
        .order_by(ModelConfig.updated_at.desc())
    )
    if config is None:
        config = db.scalar(
            select(ModelConfig)
            .where(ModelConfig.kind == "chat", ModelConfig.enabled.is_(True))
            .order_by(ModelConfig.updated_at.desc())
        )
    if config is not None:
        endpoint = endpoint_from_config(config)
        if endpoint.model_configured:
            return endpoint
    if settings.model_configured:
        return Endpoint(
            model_base_url=api_base(settings.model_base_url),
            model_api_key=settings.model_api_key,
            model_name=settings.model_name,
            model_temperature=settings.model_temperature,
            model_timeout=settings.model_timeout,
            provider="openai",
            source="env",
            config_name="环境变量",
        )
    return None


def resolve_embedding_endpoint(db) -> Endpoint | None:
    """Resolve an admin-managed embedding endpoint.

    There is deliberately no environment-variable fallback: ``BIDCRAFT_MODEL_*``
    only describes the chat endpoint. Offline mode disables all outbound calls.
    """
    if settings.offline:
        return None
    config = db.scalar(
        select(ModelConfig)
        .where(
            ModelConfig.kind == "embedding",
            ModelConfig.enabled.is_(True),
            ModelConfig.is_default.is_(True),
        )
        .order_by(ModelConfig.updated_at.desc())
    )
    if config is None:
        config = db.scalar(
            select(ModelConfig)
            .where(ModelConfig.kind == "embedding", ModelConfig.enabled.is_(True))
            .order_by(ModelConfig.updated_at.desc())
        )
    if config is None:
        return None
    endpoint = endpoint_from_config(config)
    return endpoint if endpoint.model_configured else None


def model_payload(config: ModelConfig) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "kind": config.kind,
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "options": config.options or {},
        "enabled": config.enabled,
        "is_default": config.is_default,
        "has_api_key": bool(config.api_key_enc),
        "last_test_at": _iso(config.last_test_at),
        "last_test_ok": config.last_test_ok,
        "last_test_message": config.last_test_message,
        "created_at": _iso(config.created_at),
        "updated_at": _iso(config.updated_at),
    }


def _iso(value) -> str | None:
    from datetime import UTC

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _headers(config: ModelConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = decrypt_secret(config.api_key_enc)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def test_model(config: ModelConfig) -> tuple[bool, str, int]:
    """Lightweight connectivity test; intentionally avoids image generation cost."""
    base = api_base(config.base_url)
    if not base:
        return False, "未填写 Base URL", 0
    headers = _headers(config)
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if config.provider == "ollama":
        try:
            response = httpx.get(f"{base}/api/tags", timeout=TIMEOUT)
            response.raise_for_status()
            names = [item.get("name", "") for item in response.json().get("models", [])]
            if config.model and names and config.model not in names:
                return True, f"Ollama 可达，未在模型列表中找到 {config.model}", elapsed()
            return True, f"Ollama 可达，发现 {len(names)} 个模型", elapsed()
        except (httpx.HTTPError, ValueError) as exc:
            return False, f"连接失败：{exc}", elapsed()

    try:
        response = httpx.get(f"{base}/models", headers=headers, timeout=TIMEOUT)
        if response.status_code == 200:
            try:
                names = [item.get("id", "") for item in response.json().get("data", [])]
            except ValueError:
                names = []
            if config.model and names and config.model not in names:
                return True, f"端点可达，模型列表中未找到 {config.model}", elapsed()
            return True, f"端点可达，发现 {len(names)} 个模型", elapsed()
        if response.status_code in {401, 403}:
            return False, f"鉴权失败（HTTP {response.status_code}）", elapsed()
    except httpx.HTTPError as exc:
        if config.kind == "chat":
            return _test_chat(config, headers, base, elapsed)
        return False, f"连接失败：{exc}", elapsed()

    if config.kind == "image":
        return True, "端点可达；图片生成未实际调用（避免计费）", elapsed()
    if config.kind == "embedding":
        return _test_embedding(config, headers, base, elapsed)
    return _test_chat(config, headers, base, elapsed)


def _test_chat(config: ModelConfig, headers: dict, base: str, elapsed) -> tuple[bool, str, int]:
    try:
        response = httpx.post(
            f"{base}/chat/completions",
            headers=headers,
            json={
                "model": config.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return True, "对话接口可用", elapsed()
    except httpx.HTTPStatusError as exc:
        return False, f"对话失败：HTTP {exc.response.status_code} {exc.response.text[:160]}", elapsed()
    except httpx.HTTPError as exc:
        return False, f"连接失败：{exc}", elapsed()


def _test_embedding(config: ModelConfig, headers: dict, base: str, elapsed) -> tuple[bool, str, int]:
    try:
        response = httpx.post(
            f"{base}/embeddings",
            headers=headers,
            json={"model": config.model, "input": "ping"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json().get("data") or []
        dimension = len((data[0] or {}).get("embedding") or []) if data else 0
        return True, f"向量接口可用，维度 {dimension}", elapsed()
    except httpx.HTTPStatusError as exc:
        return False, f"向量请求失败：HTTP {exc.response.status_code}", elapsed()
    except httpx.HTTPError as exc:
        return False, f"连接失败：{exc}", elapsed()
