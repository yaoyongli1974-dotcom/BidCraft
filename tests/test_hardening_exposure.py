"""生产环境暴露面收紧：文档关闭、健康检查最小化、安全响应头。"""

from __future__ import annotations


def _app(monkeypatch, **env):
    from fastapi.testclient import TestClient

    from bidcraft import config as config_module

    for key, value in env.items():
        monkeypatch.setattr(config_module.settings, key, value)
    from bidcraft.api import create_app

    return TestClient(create_app())


def test_docs_disabled_by_default(monkeypatch):
    client = _app(monkeypatch, docs_enabled=False)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_can_be_enabled(monkeypatch):
    client = _app(monkeypatch, docs_enabled=True)
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "BidCraft"


def test_health_minimal_hides_internals(monkeypatch):
    client = _app(monkeypatch, health_public=True)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "home" not in body
    assert "version" not in body
    assert "models" not in body
    assert "knowledge_bases" not in body
    # 前端仍需要的功能字段保留
    for key in ("registration", "wechat_login", "auth_required"):
        assert key in body


def test_health_full_when_public_disabled(monkeypatch):
    client = _app(monkeypatch, health_public=False)
    body = client.get("/health").json()
    assert "home" in body and "version" in body


def test_security_headers_on_api_and_spa(monkeypatch):
    client = _app(monkeypatch)
    for path in ("/health", "/login"):
        response = client.get(path)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        csp = response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
