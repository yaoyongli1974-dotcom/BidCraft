"""P1/P3：边缘防护（限流上限、代理信任、CSRF）、请求 id 与聚合指标。"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import AuthSession, Job, Project, QualityFinding, User  # noqa: E402


def _app(monkeypatch, **overrides):
    from bidcraft import config as config_module

    for key, value in overrides.items():
        monkeypatch.setattr(config_module.settings, key, value)
    return TestClient(create_app())


def _login(client, password="test-admin", headers=None):
    return client.post(
        "/auth/login",
        json={"username": "admin", "password": password},
        headers=headers or {},
    )


def _rate_limiter():
    module = importlib.import_module("bidcraft.api.app")
    return module._RateLimiter


def test_rate_limiter_expires_and_is_bounded(db, monkeypatch):
    cls = _rate_limiter()
    module = importlib.import_module("bidcraft.api.app")
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    limiter = cls(window_seconds=1)
    assert limiter.check("login", "a", 1) is True
    assert limiter.check("login", "a", 1) is False
    now[0] += 2
    assert limiter.check("login", "a", 1) is True  # TTL 到期后重新放行

    monkeypatch.setattr(cls, "MAX_KEYS", 5)
    monkeypatch.setattr(cls, "SWEEP_EVERY_CHECKS", 1)
    bounded = cls(window_seconds=60)
    for index in range(40):
        assert bounded.check("login", f"198.51.100.{index}", 1) is True
    assert bounded.size <= cls.MAX_KEYS
    # 最近使用的 bucket 必须还在（LRU，而不是随意淘汰）。
    assert bounded.check("login", "198.51.100.39", 1) is False


def test_request_id_on_early_403_401_429(db, monkeypatch):
    client = _app(monkeypatch, rate_limit_enabled=True, rate_limit_login_ip=1,
                  rate_limit_login_global=100)
    forbidden = client.get("/admin/health")
    assert forbidden.status_code == 401
    assert forbidden.headers.get("X-Request-ID")

    assert _login(client).status_code == 200
    csrf = client.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert csrf.status_code == 403
    assert csrf.headers.get("X-Request-ID")

    limited = _login(client, password="wrong")
    assert limited.status_code == 429
    assert limited.headers.get("X-Request-ID")


def test_public_origin_and_forwarded_headers_behind_proxy(db, monkeypatch):
    origins = "https://public.example, https://other.example/"
    client = _app(
        monkeypatch,
        trust_proxy=True,
        public_origin=origins,
    )
    assert _login(client).status_code == 200
    proxy = {
        "Origin": "https://public.example",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "internal.example:8443",
    }
    assert client.post("/auth/logout", headers=proxy).status_code == 200
    bad = _app(monkeypatch, trust_proxy=True, public_origin=origins)
    assert _login(bad).status_code == 200
    assert bad.post(
        "/auth/logout",
        headers={**proxy, "Origin": "https://evil.example"},
    ).status_code == 403

    # 关闭 trust_proxy 时 X-Forwarded-Proto/Host 不得参与同源判定。
    direct = _app(monkeypatch, trust_proxy=False, public_origin="")
    assert _login(direct).status_code == 200
    assert direct.post(
        "/auth/logout",
        headers={
            "Origin": "https://internal.example:8443",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "internal.example:8443",
        },
    ).status_code == 403


def test_untrusted_forwarded_for_ignored_for_rate_and_audit(db, monkeypatch):
    client = _app(
        monkeypatch,
        trust_proxy=False,
        rate_limit_enabled=True,
        rate_limit_login_ip=1,
        rate_limit_login_global=100,
    )
    spoofed = {"X-Forwarded-For": "198.51.100.77"}
    assert _login(client, password="wrong", headers=spoofed).status_code == 401
    # 同一直接来源、不同伪造 XFF：仍命中同一个 bucket。
    assert _login(client, password="wrong", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 429

    trusted = _app(
        monkeypatch,
        trust_proxy=True,
        rate_limit_enabled=True,
        rate_limit_login_ip=1,
        rate_limit_login_global=100,
    )
    assert _login(trusted, password="wrong", headers=spoofed).status_code == 401
    assert _login(trusted, password="wrong", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401

    audit_client = _app(monkeypatch, trust_proxy=False)
    assert _login(audit_client, headers={"X-Forwarded-For": "198.51.100.78"}).status_code == 200
    with session_scope() as sess:
        ips = list(sess.scalars(select(AuthSession.ip)))
    assert "198.51.100.78" not in ips


def test_uvicorn_proxy_headers_follows_trust_proxy(db, monkeypatch):
    import uvicorn

    from bidcraft import cli, jobqueue

    captured: list[dict] = []

    def _fake_run(_app, **kwargs):
        captured.append(kwargs)
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(jobqueue, "recover_on_startup", lambda: (0, 0))
    monkeypatch.setattr(jobqueue, "start_workers", lambda: None)
    monkeypatch.setattr(cli, "_install_shutdown_handlers", lambda: None)

    from bidcraft import config as config_module

    monkeypatch.setattr(config_module.settings, "trust_proxy", False)
    with pytest.raises(RuntimeError):
        cli.serve(host="127.0.0.1", port=8000, open_browser=False)
    assert captured[-1]["proxy_headers"] is False
    assert captured[-1]["forwarded_allow_ips"] != "*"

    monkeypatch.setattr(config_module.settings, "trust_proxy", True)
    with pytest.raises(RuntimeError):
        cli.serve(host="127.0.0.1", port=8000, open_browser=False)
    assert captured[-1]["proxy_headers"] is True


def test_admin_health_exposes_global_metrics(db, monkeypatch):
    client = _app(monkeypatch)
    assert client.get("/admin/health").status_code == 401
    assert _login(client).status_code == 200
    body = client.get("/admin/health").json()
    metrics = body["global_metrics"]
    assert set(metrics) >= {"projects_by_status", "jobs_by_status", "open_findings", "users"}
    assert "models" in body and "status" in body  # 既有字段保持不变
    with session_scope() as sess:
        users = sess.scalar(select(func.count()).select_from(User)) or 0
        projects = sess.scalar(select(func.count()).select_from(Project)) or 0
        jobs = sess.scalar(select(func.count()).select_from(Job)) or 0
        findings = sess.scalar(select(func.count()).select_from(QualityFinding)) or 0
    assert metrics["users"] == users
    assert sum(metrics["projects_by_status"].values()) == projects
    assert sum(metrics["jobs_by_status"].values()) == jobs
    assert metrics["open_findings"] <= findings
    # 公开探活端点不暴露全局指标。
    assert "global_metrics" not in client.get("/health").json()
