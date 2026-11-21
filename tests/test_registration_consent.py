"""注册协议同意必须显式提交：缺省/False 一律拒绝，同意动作留痕版本与时间。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from bidcraft import compliance
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import User


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "signup_credits", 0)
    from bidcraft.api import create_app

    app = create_app()
    app.state.testing = True
    return TestClient(app)


def _get_user(username: str) -> User:
    with session_scope() as session:
        return session.query(User).filter(User.username == username).one()


def test_missing_accept_terms_is_rejected(db, monkeypatch):
    """不传 accept_terms 不再被 Pydantic 默认成 True，服务端应拒绝。"""
    client = _client(monkeypatch)
    response = client.post(
        "/auth/register",
        json={"username": "no-consent", "password": "passw0rd123"},
    )
    assert response.status_code == 400
    assert "同意" in response.json()["detail"]
    with session_scope() as session:
        assert session.query(User).filter(User.username == "no-consent").count() == 0


def test_explicit_false_is_rejected(db, monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/auth/register",
        json={"username": "refuse-consent", "password": "passw0rd123", "accept_terms": False},
    )
    assert response.status_code == 400
    with session_scope() as session:
        assert session.query(User).filter(User.username == "refuse-consent").count() == 0


def test_explicit_true_records_version_and_time(db, monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/auth/register",
        json={"username": "agree-consent", "password": "passw0rd123", "accept_terms": True},
    )
    assert response.status_code == 201, response.text
    user = _get_user("agree-consent")
    assert user.terms_version == compliance.LEGAL_VERSION
    assert user.terms_accepted_at is not None
    assert compliance.consent_payload(user)["up_to_date"] is True


def test_custom_terms_version_is_recorded(db, monkeypatch):
    """客户端可提交具体条款版本，按提交值留痕（截断到 20 字）。"""
    client = _client(monkeypatch)
    response = client.post(
        "/auth/register",
        json={
            "username": "versioned-consent",
            "password": "passw0rd123",
            "accept_terms": True,
            "terms_version": "2026-11-01-v2",
        },
    )
    assert response.status_code == 201, response.text
    assert _get_user("versioned-consent").terms_version == "2026-11-01-v2"
