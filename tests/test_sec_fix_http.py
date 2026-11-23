"""边缘防护修复：请求 id、CSRF、进程内限流、真实来源 IP 与安全响应头。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _app(monkeypatch, *, trust_proxy: bool = False, **overrides):
    from fastapi.testclient import TestClient

    from bidcraft import config as config_module
    from bidcraft.api import create_app

    # 保留到用例结束（中间件在请求时才读取 settings），由 monkeypatch 自动还原。
    for key, value in {"trust_proxy": trust_proxy, **overrides}.items():
        monkeypatch.setattr(config_module.settings, key, value)
    return TestClient(create_app())


def _login(client, password: str = "test-admin", headers: dict | None = None):
    return client.post(
        "/auth/login",
        json={"username": "admin", "password": password},
        headers=headers or {},
    )


def test_request_id_echo_and_new_headers(db, monkeypatch):
    client = _app(monkeypatch)
    response = client.get("/health", headers={"X-Request-ID": "sec-fix-123"})
    assert response.headers["X-Request-ID"] == "sec-fix-123"
    generated = client.get("/health").headers["X-Request-ID"]
    assert generated and generated != "sec-fix-123"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_csrf_rejects_cross_origin_only_with_cookie(db, monkeypatch):
    client = _app(monkeypatch)
    assert _login(client).status_code == 200

    # 跨源带 Cookie：一律 403
    assert client.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert (
        client.post(
            "/auth/logout", headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    # 同源 / 无 Origin 头（curl、既有 TestClient 用例）保持兼容
    assert client.post("/auth/logout", headers={"Origin": "http://testserver"}).status_code == 200

    anonymous = _app(monkeypatch)
    response = anonymous.post(
        "/auth/login",
        json={"username": "admin", "password": "nope"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 401  # 没有 Cookie 时不触发 CSRF 检查


def test_per_ip_login_rate_limit(db, monkeypatch):
    # 每 IP 3 次/窗口：第 4 次应被 429 拦住（早于用户名维度的 8 次）。
    client = _app(
        monkeypatch,
        rate_limit_enabled=True,
        rate_limit_login_ip=3,
        rate_limit_login_global=100,
    )
    for _ in range(3):
        assert _login(client, password="wrong").status_code == 401
    blocked = _login(client, password="wrong")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "300"


def test_global_login_rate_limit(db, monkeypatch):
    client = _app(
        monkeypatch, rate_limit_enabled=True, rate_limit_login_ip=100, rate_limit_login_global=2
    )
    assert _login(client, password="wrong").status_code == 401
    assert _login(client, password="wrong").status_code == 401
    assert _login(client, password="wrong").status_code == 429


def test_sensitive_endpoint_rate_limit(db, monkeypatch):
    client = _app(
        monkeypatch,
        rate_limit_enabled=True,
        rate_limit_sensitive_ip=2,
        rate_limit_sensitive_global=100,
    )
    payload = {"username": "sec-rate-x", "password": "Passw0rd!123", "accept_terms": False}
    assert client.post("/auth/register", json=payload).status_code == 400
    assert client.post("/auth/register", json=payload).status_code == 400
    assert client.post("/auth/register", json=payload).status_code == 429


def test_trust_proxy_uses_forwarded_for_in_session_and_audit(db, monkeypatch):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, AuthSession

    client = _app(monkeypatch, trust_proxy=True)
    response = _login(client, headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert response.status_code == 200, response.text
    with session_scope() as session:
        ip = session.scalar(select(AuthSession.ip).order_by(AuthSession.created_at.desc()))
        assert ip == "203.0.113.9"
        detail = session.scalar(
            select(AuditLog.detail).where(AuditLog.action == "auth.login").order_by(AuditLog.id.desc())
        )
    assert detail["ip"] == "203.0.113.9"

    # 默认不信任代理，XFF 被忽略
    direct = _app(monkeypatch, trust_proxy=False)
    assert _login(direct, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200
    with session_scope() as session:
        ips = list(session.scalars(select(AuthSession.ip)))
    assert "198.51.100.7" not in ips
