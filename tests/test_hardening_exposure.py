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


def test_admin_health_requires_super_admin(db, monkeypatch):
    """超管可拿完整运行状态；匿名 401、普通成员 403，公开 /health 仍然最小化。"""
    from bidcraft import __version__

    client = _app(monkeypatch, health_public=True)
    assert client.get("/admin/health").status_code == 401

    member = _app(monkeypatch, health_public=True)
    assert (
        member.post(
            "/auth/register",
            json={"username": "health-member", "password": "Passw0rd!123", "accept_terms": True},
        ).status_code
        == 201
    )
    assert member.get("/admin/health").status_code == 403

    admin = _app(monkeypatch, health_public=True)
    login = admin.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200, login.text
    body = admin.get("/admin/health").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["uptime_seconds"] >= 0
    assert body["started_at"]
    assert body["health_public"] is True
    assert "home" in body and body["users"] >= 2
    assert body["db_size_bytes"] > 0

    # 公开端点不因超管 cookie 而变大
    minimal = admin.get("/health").json()
    assert "version" not in minimal and "home" not in minimal
