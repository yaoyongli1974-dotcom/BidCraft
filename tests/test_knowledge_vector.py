from __future__ import annotations

import json
import time
from datetime import datetime

import httpx
import pytest

from bidcraft.config import settings

pytest.importorskip("fastapi")

DEFAULT_VECTOR = [1.0, 0.0, 0.0]


class FakeEmbeddingServer:
    """OpenAI-compatible ``/embeddings`` stand-in keyed by text substrings."""

    def __init__(self, rules=None, default=None, fail_substrings=None, status=500):
        self.rules = list(rules or [])
        self.default = list(DEFAULT_VECTOR if default is None else default)
        self.fail_substrings = list(fail_substrings or [])
        self.status = status
        self.requests: list[dict] = []

    def vector_for(self, text: str) -> list[float]:
        for needle, vector in self.rules:
            if needle in (text or ""):
                return list(vector)
        return list(self.default)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        inputs = body.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        inputs = list(inputs or [])
        for text in inputs:
            for needle in self.fail_substrings:
                if needle in text:
                    return httpx.Response(self.status, json={"error": {"message": "boom"}})
        data = [
            {"index": index, "embedding": self.vector_for(text)}
            for index, text in enumerate(inputs)
        ]
        return httpx.Response(200, json={"data": data})


def _patch_embed(monkeypatch, server: FakeEmbeddingServer) -> None:
    from bidcraft import knowledge

    def fake_post(url, **kwargs):
        kwargs.pop("timeout", None)
        request = httpx.Request("POST", url, **kwargs)
        response = httpx.MockTransport(server.handle).handle_request(request)
        response.request = request
        return response

    monkeypatch.setattr(knowledge.httpx, "post", fake_post)


def _vector_settings(monkeypatch, **overrides) -> None:
    from bidcraft.config import settings

    values = {
        "kb_vector_enabled": True,
        "kb_vector_weight": 0.6,
        "kb_vector_min_score": 0.1,
        "kb_vector_max_chunks": 4000,
        "kb_vector_batch": 16,
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)


def _online(monkeypatch) -> None:
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "offline", False)


def _embedding_config(
    *,
    name="向量模型",
    model="emb-main",
    is_default=True,
    enabled=True,
    base_url="http://fake/v1",
    updated_at=None,
) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import ModelConfig

    with session_scope() as session:
        row = ModelConfig(
            name=name,
            kind="embedding",
            provider="openai",
            base_url=base_url,
            model=model,
            enabled=enabled,
            is_default=is_default,
        )
        if updated_at is not None:
            row.updated_at = updated_at
        session.add(row)
        session.flush()
        return row.id


def _write_kb_dir(tmp_path, texts) -> object:
    root = tmp_path / "kb"
    root.mkdir(exist_ok=True)
    for index, text in enumerate(texts):
        (root / f"doc{index}.md").write_text(text, encoding="utf-8")
    return root


def _make_local_kb(tmp_path, texts, *, name="技术库") -> str:
    from bidcraft.db import session_scope
    from bidcraft.knowledge import index_local
    from bidcraft.models import KnowledgeBase

    root = _write_kb_dir(tmp_path, texts)
    with session_scope() as session:
        kb = KnowledgeBase(name=name, kind="local", provider="local", location=str(root))
        session.add(kb)
        session.flush()
        index_local(session, kb)
        return kb.id


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _wait_job(client, job_id: str, timeout: float = 10.0) -> dict:
    status: dict = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed", "cancelled"}:
            return status
        time.sleep(0.02)
    raise AssertionError(status)


def _api_embedding_model(client, model="emb-main", name="向量模型") -> str:
    response = client.post(
        "/admin/models",
        json={
            "name": name,
            "kind": "embedding",
            "provider": "openai",
            "base_url": "http://fake/v1",
            "model": model,
            "is_default": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _api_local_kb(client, root, name="技术库") -> str:
    response = client.post(
        "/admin/kbs",
        json={
            "name": name,
            "kind": "local",
            "provider": "local",
            "location": str(root),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_resolve_embedding_endpoint_prefers_default_and_respects_offline(db, monkeypatch):
    from sqlalchemy import select

    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.model_config import resolve_embedding_endpoint
    from bidcraft.models import ModelConfig

    _vector_settings(monkeypatch)
    monkeypatch.setattr(settings, "offline", True)
    with session_scope() as session:
        assert resolve_embedding_endpoint(session) is None

    _online(monkeypatch)
    _embedding_config(
        name="旧向量",
        model="emb-old",
        is_default=False,
        updated_at=datetime(2026, 1, 1),
    )
    _embedding_config(
        name="新向量",
        model="emb-new",
        is_default=False,
        updated_at=datetime(2026, 2, 1),
    )
    with session_scope() as session:
        endpoint = resolve_embedding_endpoint(session)
    assert endpoint is not None
    assert endpoint.model_name == "emb-new"

    _embedding_config(
        name="默认向量",
        model="emb-main",
        is_default=True,
        updated_at=datetime(2025, 1, 1),
    )
    with session_scope() as session:
        endpoint = resolve_embedding_endpoint(session)
    assert endpoint is not None
    assert endpoint.model_name == "emb-main"

    with session_scope() as session:
        row = session.scalar(select(ModelConfig).where(ModelConfig.model == "emb-main"))
        assert row is not None
        row.enabled = False
    with session_scope() as session:
        endpoint = resolve_embedding_endpoint(session)
    assert endpoint is not None
    assert endpoint.model_name == "emb-new"


def test_resolve_embedding_endpoint_has_no_env_fallback(db, monkeypatch):
    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.model_config import resolve_embedding_endpoint

    _online(monkeypatch)
    monkeypatch.setattr(settings, "model_base_url", "http://env.example/v1")
    monkeypatch.setattr(settings, "model_name", "env-chat")
    monkeypatch.setattr(settings, "model_api_key", "env-key")
    with session_scope() as session:
        assert resolve_embedding_endpoint(session) is None


def test_build_vectors_writes_vectors_and_key(db, tmp_path, monkeypatch):
    from sqlalchemy import select

    from bidcraft import knowledge
    from bidcraft.db import session_scope
    from bidcraft.models import KbChunk

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    config_id = _embedding_config()
    server = FakeEmbeddingServer(rules=[("双绞", [1.0, 0.0, 0.0])])
    _patch_embed(monkeypatch, server)
    kb_id = _make_local_kb(tmp_path, ["双绞线缆", "网线面板"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        result = knowledge.build_vectors(session, kb)
    assert result == {
        "embedded": 2,
        "total": 2,
        "model": "emb-main",
        "dim": 3,
        "error": "",
    }

    expected_key = f"{config_id}:emb-main"
    with session_scope() as session:
        rows = list(session.scalars(select(KbChunk).order_by(KbChunk.ordinal)))
    assert len(rows) == 2
    for row in rows:
        assert row.embed_key == expected_key
        assert row.embed_dim == 3
        assert row.embedding == server.vector_for(row.text)

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        again = knowledge.build_vectors(session, kb)
    assert again["embedded"] == 0
    assert again["error"] == ""
    assert again["total"] == 2


def test_model_change_invalidates_old_vectors(db, tmp_path, monkeypatch):
    from sqlalchemy import select

    from bidcraft import knowledge
    from bidcraft.db import session_scope
    from bidcraft.models import KbChunk, ModelConfig

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    config_id = _embedding_config(model="emb-1")
    _patch_embed(monkeypatch, FakeEmbeddingServer())
    kb_id = _make_local_kb(tmp_path, ["双绞线缆", "网线面板"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        assert knowledge.build_vectors(session, kb)["embedded"] == 2

    with session_scope() as session:
        config = session.get(ModelConfig, config_id)
        config.model = "emb-2"

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        result = knowledge.build_vectors(session, kb)
    assert result["embedded"] == 2
    assert result["model"] == "emb-2"

    with session_scope() as session:
        rows = list(session.scalars(select(KbChunk)))
    assert {row.embed_key for row in rows} == {f"{config_id}:emb-2"}


def test_hybrid_search_prefers_semantic_match(db, tmp_path, monkeypatch):
    from bidcraft import knowledge
    from bidcraft.db import session_scope

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    _embedding_config()
    server = FakeEmbeddingServer(
        rules=[("面板", [0.0, 1.0, 0.0]), ("双绞", [1.0, 0.0, 0.0])],
        default=[1.0, 0.0, 0.0],
    )
    _patch_embed(monkeypatch, server)
    kb_id = _make_local_kb(tmp_path, ["双绞线缆", "网线面板"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        assert knowledge.build_vectors(session, kb)["embedded"] == 2

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        hits = knowledge.search_local(session, kb, "网线", 5)
    assert hits
    assert hits[0].text == "双绞线缆"
    assert hits[0].score == 0.6
    assert [hit.text for hit in hits][1:] == ["网线面板"]


def test_search_without_model_falls_back_to_bigram(db, tmp_path, monkeypatch):
    from bidcraft import knowledge
    from bidcraft.db import session_scope

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    server = FakeEmbeddingServer()
    _patch_embed(monkeypatch, server)
    kb_id = _make_local_kb(tmp_path, ["双绞线缆", "网线面板"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        hits = knowledge.search_local(session, kb, "网线", 5)
    assert [hit.text for hit in hits] == ["网线面板"]
    assert server.requests == []


def test_query_embedding_failure_falls_back_to_bigram(db, tmp_path, monkeypatch):
    from bidcraft import knowledge
    from bidcraft.db import session_scope

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    _embedding_config()
    _patch_embed(
        monkeypatch,
        FakeEmbeddingServer(rules=[("双绞", [1.0, 0.0, 0.0])], default=[1.0, 0.0, 0.0]),
    )
    kb_id = _make_local_kb(tmp_path, ["双绞线缆", "网线面板"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        assert knowledge.build_vectors(session, kb)["embedded"] == 2

    failing = FakeEmbeddingServer(fail_substrings=["网线"])
    _patch_embed(monkeypatch, failing)
    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        hits = knowledge.search_local(session, kb, "网线", 5)
    assert [hit.text for hit in hits] == ["网线面板"]
    assert failing.requests


def test_min_score_filters_weak_semantic_matches(db, tmp_path, monkeypatch):
    from bidcraft import knowledge
    from bidcraft.db import session_scope

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    _embedding_config()
    _patch_embed(
        monkeypatch,
        FakeEmbeddingServer(
            rules=[
                ("无关", [0.05, 0.9987, 0.0]),
                ("双绞", [1.0, 0.0, 0.0]),
            ],
            default=[1.0, 0.0, 0.0],
        ),
    )
    kb_id = _make_local_kb(tmp_path, ["完全无关", "双绞线缆"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        assert knowledge.build_vectors(session, kb)["embedded"] == 2
        hits = knowledge.search_local(session, kb, "网线", 5)
    assert [hit.text for hit in hits] == ["双绞线缆"]

    _vector_settings(monkeypatch, kb_vector_min_score=0.0)
    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        hits = knowledge.search_local(session, kb, "网线", 5)
    assert [hit.text for hit in hits] == ["双绞线缆", "完全无关"]


def test_max_chunks_truncates_and_reports(db, tmp_path, monkeypatch):
    from sqlalchemy import select

    from bidcraft import knowledge
    from bidcraft.db import session_scope
    from bidcraft.models import KbChunk

    _vector_settings(monkeypatch, kb_vector_max_chunks=1)
    _online(monkeypatch)
    _embedding_config()
    _patch_embed(monkeypatch, FakeEmbeddingServer())
    kb_id = _make_local_kb(tmp_path, ["双绞线缆", "网线面板", "机柜配线"])

    with session_scope() as session:
        kb = session.get(knowledge.KnowledgeBase, kb_id)
        result = knowledge.build_vectors(session, kb)
    assert result["embedded"] == 1
    assert result["total"] == 3
    assert "上限" in result["error"]

    with session_scope() as session:
        embedded = list(session.scalars(select(KbChunk).where(KbChunk.embedding.is_not(None))))
    assert len(embedded) == 1


def test_reindex_returns_vector_and_payload_fields(db, tmp_path, monkeypatch):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import KbChunk

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    _patch_embed(monkeypatch, FakeEmbeddingServer())
    client = _client()
    _api_embedding_model(client)
    root = _write_kb_dir(tmp_path, ["双绞线缆", "网线面板"])
    kb_id = _api_local_kb(client, root)

    created = client.get("/admin/kbs").json()[0]
    assert created["vector_enabled"] is True
    assert created["vector_total"] == 0
    assert created["vector_ready"] is False
    assert created["vector_stale"] is False
    assert created["vector_model"] == "emb-main"

    accepted = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert accepted.status_code == 202, accepted.text
    job = _wait_job(client, accepted.json()["job_id"])
    assert job["status"] == "done", job
    body = job["result"]
    assert body["vector"] == {
        "embedded": 2,
        "total": 2,
        "model": "emb-main",
        "dim": 3,
        "error": "",
    }
    kb = body["kb"]
    assert kb["vector_embedded"] == 2
    assert kb["vector_total"] == 2
    assert kb["vector_dim"] == 3
    assert kb["vector_ready"] is True
    assert kb["vector_stale"] is False

    with session_scope() as session:
        row = session.scalar(select(KbChunk).limit(1))
        row.embed_key = ""
    stale = client.get("/admin/kbs").json()[0]
    assert stale["vector_embedded"] == 1
    assert stale["vector_ready"] is False
    assert stale["vector_stale"] is True

    monkeypatch.setattr(settings, "health_public", False)
    health = client.get("/health").json()
    assert health["embedding_configured"] is True
    assert health["embedding_model"] == "emb-main"


def test_embed_route_validation_errors(db, tmp_path, monkeypatch):
    from bidcraft.config import settings

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    _patch_embed(monkeypatch, FakeEmbeddingServer())
    client = _client()

    api_kb = client.post(
        "/admin/kbs",
        json={
            "name": "接口库",
            "kind": "api",
            "provider": "generic",
            "location": "http://retrieval.example/v1",
        },
    )
    assert api_kb.status_code == 201, api_kb.text
    rejected = client.post(f"/admin/kbs/{api_kb.json()['id']}/embed")
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "仅本地知识库支持向量索引"

    root = _write_kb_dir(tmp_path, ["双绞线缆"])
    kb_id = _api_local_kb(client, root)
    empty = client.post(f"/admin/kbs/{kb_id}/embed")
    assert empty.status_code == 400
    assert empty.json()["detail"] == "请先重建索引"

    accepted = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert accepted.status_code == 202
    assert _wait_job(client, accepted.json()["job_id"])["status"] == "done"
    monkeypatch.setattr(settings, "offline", True)
    missing = client.post(f"/admin/kbs/{kb_id}/embed")
    assert missing.status_code == 400
    assert "未配置可用的向量模型" in missing.json()["detail"]

    monkeypatch.setattr(settings, "health_public", False)
    health = client.get("/health").json()
    assert health["embedding_configured"] is False
    assert health["embedding_model"] == ""


def test_embed_route_partial_failure_is_200(db, tmp_path, monkeypatch):
    _vector_settings(monkeypatch, kb_vector_batch=1)
    _online(monkeypatch)
    client = _client()
    _api_embedding_model(client)
    _patch_embed(monkeypatch, FakeEmbeddingServer(fail_substrings=["网线面板"]))
    root = _write_kb_dir(tmp_path, ["双绞线缆", "网线面板"])
    kb_id = _api_local_kb(client, root)

    accepted = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert accepted.status_code == 202, accepted.text
    job = _wait_job(client, accepted.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["vector"]["embedded"] == 1

    # Simulate a stale index so the second call reprocesses both chunks.
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import KbChunk

    with session_scope() as session:
        for row in session.scalars(select(KbChunk)):
            row.embed_key = ""

    response = client.post(f"/admin/kbs/{kb_id}/embed")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["embedded"] == 1
    assert payload["total"] == 2
    assert payload["model"] == "emb-main"
    assert payload["error"]
    assert payload["kb"]["vector_stale"] is True

    from bidcraft.models import AuditLog

    with session_scope() as session:
        embed_audits = list(
            session.scalars(select(AuditLog).where(AuditLog.action == "kb.embed"))
        )
    assert embed_audits
    assert any(str(kb_id) in str(item.detail) for item in embed_audits)


def test_kb_payload_vector_fields_for_api_kb(db, monkeypatch):
    from bidcraft import knowledge
    from bidcraft.db import session_scope
    from bidcraft.models import KnowledgeBase

    _vector_settings(monkeypatch)
    _online(monkeypatch)
    _embedding_config()
    with session_scope() as session:
        kb = KnowledgeBase(
            name="接口库",
            kind="api",
            provider="generic",
            location="http://retrieval.example/v1",
        )
        session.add(kb)
        session.flush()
        payload = knowledge.kb_payload(kb, session)
    assert payload["vector_total"] == 0
    assert payload["vector_embedded"] == 0
    assert payload["vector_ready"] is False
    assert payload["vector_stale"] is False


def test_kb_chunk_embedding_columns_exist(db):
    from sqlalchemy import inspect

    from bidcraft.db import get_engine

    columns = {column["name"] for column in inspect(get_engine()).get_columns("kb_chunks")}
    assert {"embedding", "embed_key", "embed_dim"} <= columns


def test_migrate_columns_adds_embedding_to_legacy_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from bidcraft.db import _migrate_columns

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}", future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE kb_chunks (id VARCHAR(36) PRIMARY KEY, text TEXT)"))
    _migrate_columns(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("kb_chunks")}
    assert {"embedding", "embed_key", "embed_dim"} <= columns


def test_search_exposes_chunk_trace_and_rerank_score(db, tmp_path, monkeypatch):
    from bidcraft import knowledge
    from bidcraft.db import session_scope

    kb_id = _make_local_kb(tmp_path, ["综合布线 Cat6A 测试"])
    with session_scope() as session:
        hits = knowledge.search(session, "综合布线", limit=4, kb_ids=[kb_id])
        assert hits and hits[0].chunk_id
        assert hits[0].chunk_index == 0
        assert hits[0].rerank_score is not None
        assert f"块 {hits[0].chunk_id}" in knowledge.trace_label(hits[0])

        diagnostics = knowledge.search(
            session, "综合布线", limit=4, kb_ids=[kb_id], with_diagnostics=True
        )
        assert isinstance(diagnostics, knowledge.SearchResult)
        assert diagnostics.diagnostics["searched"][0]["kb_id"] == kb_id
