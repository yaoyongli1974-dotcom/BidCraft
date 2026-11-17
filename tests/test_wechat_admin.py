"""管理端微信开放平台配置。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    assert client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    ).status_code == 200
    return client


def _config(client, **fields):
    response = client.put("/admin/wechat", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def test_wechat_config_defaults_and_hot_effect(db):
    client = _client()
    body = client.get("/admin/wechat").json()
    assert body["available"] is False and body["source"] == "env"
    assert client.get("/health").json()["wechat_login"] is False

    body = _config(
        client,
        app_id="wx-admin",
        app_secret="secret-value",
        redirect_uri="https://example.com/auth/wechat/callback",
        enabled=True,
    )
    assert body["has_secret"] is True and body["source"] == "db"
    assert "secret-value" not in str(body)
    assert client.get("/health").json()["wechat_login"] is True

    body = _config(client, clear_secret=True)
    assert body["has_secret"] is False
    assert client.get("/health").json()["wechat_login"] is False


def test_wechat_mock_mode_and_test_route(db):
    client = _client()
    incomplete = client.post("/admin/wechat/test").json()
    assert incomplete["ok"] is False

    _config(client, mock=True, enabled=True)
    assert client.get("/health").json()["wechat_login"] is True
    result = client.post("/admin/wechat/test").json()
    assert result["ok"] is True and "模拟" in result["message"]


def test_wechat_config_requires_super_and_audits(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    admin.post(
        "/admin/users",
        json={"username": "wx-viewer", "password": "viewer-pass-1", "role": "viewer"},
    )
    viewer = TestClient(create_app())
    viewer.post("/auth/login", json={"username": "wx-viewer", "password": "viewer-pass-1"})
    assert viewer.put("/admin/wechat", json={"mock": True}).status_code == 403

    _config(admin, app_id="wx-audit")
    actions = [item["action"] for item in admin.get("/admin/audit").json()["items"]]
    assert "wechat.config" in actions
    assert admin.put("/admin/wechat", json={}).status_code == 400
