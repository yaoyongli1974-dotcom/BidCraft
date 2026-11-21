from __future__ import annotations

import json
import time

import httpx
import pytest

pytest.importorskip("fastapi")

DEFAULT_VECTOR = [0.1, 0.2, 0.3]


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


def _local_kb(client, tmp_path, name: str = "重建索引库") -> str:
    root = tmp_path / "kb"
    root.mkdir(exist_ok=True)
    (root / "cabling.md").write_text(
        "综合布线系统采用 Cat6A 线缆，信息点测试执行 GB 50311-2016。", encoding="utf-8"
    )
    (root / "ba.md").write_text(
        "楼宇自控采用 BACnet 协议，DDC 控制器统一点位命名。", encoding="utf-8"
    )
    created = client.post(
        "/admin/kbs",
        json={
            "name": name,
            "kind": "local",
            "provider": "local",
            "location": str(root),
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _patch_embed(monkeypatch) -> None:
    from bidcraft import knowledge

    def fake_post(url, **kwargs):
        kwargs.pop("timeout", None)
        request = httpx.Request("POST", url, **kwargs)
        body = json.loads(request.content or b"{}")
        inputs = body.get("input")
        if isinstance(inputs, str) or inputs is None:
            inputs = [inputs or ""]
        data = [
            {"index": index, "embedding": list(DEFAULT_VECTOR)}
            for index, _text in enumerate(inputs)
        ]
        response = httpx.Response(200, json={"data": data})
        response.request = request
        return response

    monkeypatch.setattr(knowledge.httpx, "post", fake_post)


def _embedding_model(client) -> None:
    created = client.post(
        "/admin/models",
        json={
            "name": "向量模型",
            "kind": "embedding",
            "provider": "openai",
            "base_url": "http://fake/v1",
            "model": "emb-main",
            "is_default": True,
        },
    )
    assert created.status_code == 201, created.text


def test_reindex_job_returns_202_and_completes_with_vector(db, tmp_path, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "offline", False)
    monkeypatch.setattr(settings, "kb_vector_enabled", True)
    monkeypatch.setattr(settings, "kb_vector_batch", 16)
    _patch_embed(monkeypatch)
    client = _client()
    _embedding_model(client)
    kb_id = _local_kb(client, tmp_path)

    accepted = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert set(body) == {"job_id"}
    assert body["job_id"]

    job = _wait_job(client, body["job_id"])
    assert job["status"] == "done", job
    assert job["project_id"] is None
    assert job["kind"] == "kb_reindex"

    result = job["result"]
    assert result["indexed"] == 2
    assert result["vector"] == {
        "embedded": 2,
        "total": 2,
        "model": "emb-main",
        "dim": 3,
        "error": "",
    }
    assert result["kb"]["id"] == kb_id
    assert result["kb"]["chunk_count"] == 2
    assert result["kb"]["vector_embedded"] == 2
    assert result["kb"]["vector_ready"] is True


def test_reindex_job_rejects_non_local_kb(db):
    client = _client()
    created = client.post(
        "/admin/kbs",
        json={
            "name": "接口知识库",
            "kind": "api",
            "provider": "generic",
            "location": "http://retrieval.example/v1",
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(f"/admin/kbs/{created.json()['id']}/reindex")
    assert response.status_code == 400
    assert response.json()["detail"] == "只有本地知识库需要建索引"


def test_reindex_job_unknown_kb_returns_404(db):
    client = _client()
    response = client.post("/admin/kbs/kb-does-not-exist/reindex")
    assert response.status_code == 404
    assert response.json()["detail"] == "知识库不存在"


def test_reindex_job_duplicate_submission_returns_409(db, tmp_path, monkeypatch):
    import threading

    from bidcraft import api

    # DB 驱动队列：worker 认领后立即执行 runner。用阻塞 runner 让 job 停在 running，
    # 才有「已有任务在运行」可查；释放事件放在 finally，避免遗留阻塞线程。
    release = threading.Event()

    def blocking(job_id, kb_id):
        release.wait(5)

    monkeypatch.setattr(api, "_run_kb_reindex_job", blocking)
    client = _client()
    kb_id = _local_kb(client, tmp_path)

    try:
        first = client.post(f"/admin/kbs/{kb_id}/reindex")
        assert first.status_code == 202, first.text
        second = client.post(f"/admin/kbs/{kb_id}/reindex")
        assert second.status_code == 409
        assert second.json()["detail"] == "该知识库已有重建索引任务在运行"
    finally:
        release.set()


def test_reindex_job_worker_failure_is_stable_and_hides_traceback(
    db, tmp_path, monkeypatch
):
    from bidcraft import knowledge

    def explode(db, kb):
        raise RuntimeError("secret boom: cannot read /etc/passwd")

    monkeypatch.setattr(knowledge, "index_local", explode)
    client = _client()
    kb_id = _local_kb(client, tmp_path)

    accepted = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert accepted.status_code == 202, accepted.text
    job = _wait_job(client, accepted.json()["job_id"])

    assert job["status"] == "failed"
    message = job["message"]
    assert message == "知识库索引失败：服务内部错误，详情见服务日志"
    assert "secret boom" not in message
    assert "Traceback" not in message
    assert job["result"]["error"] == message
    assert "Traceback" not in job["result"]["error"]


def test_reindex_job_missing_directory_message_is_chinese(db, tmp_path, monkeypatch):
    from bidcraft import knowledge

    def missing(db, kb):
        raise knowledge.KnowledgeError("目录不存在：/nope")

    monkeypatch.setattr(knowledge, "index_local", missing)
    client = _client()
    kb_id = _local_kb(client, tmp_path)

    accepted = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert accepted.status_code == 202, accepted.text
    job = _wait_job(client, accepted.json()["job_id"])
    assert job["status"] == "failed"
    assert job["message"] == "知识库索引失败：本地目录不可用或内容无法解析，详情见服务日志"
