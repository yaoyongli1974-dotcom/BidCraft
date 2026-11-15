from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")


def _make_user(username: str, password: str = "correct-pass-1") -> None:
    from bidcraft.db import session_scope
    from bidcraft.models import User
    from bidcraft.security import hash_password

    with session_scope() as db:
        db.add(
            User(
                username=username,
                display_name=username,
                password_hash=hash_password(password),
                role="viewer",
                active=True,
            )
        )


def _attempt(username: str, password: str):
    from bidcraft import auth
    from bidcraft.db import session_scope

    with session_scope() as db:
        return auth.login(db, username, password)


def _counter(key: str):
    from bidcraft.db import session_scope
    from bidcraft.models import LoginRateLimit

    with session_scope() as db:
        return db.get(LoginRateLimit, key)


def _wrong(username: str):
    with pytest.raises(PermissionError) as caught:
        _attempt(username, "definitely-wrong")
    return str(caught.value)


def test_constants_match_contract():
    from bidcraft import auth

    assert auth._FAILURE_WINDOW == 300.0
    assert auth._FAILURE_LIMIT == 8


def test_failures_persist_in_db_and_block_on_threshold(db):
    from bidcraft.models import LoginRateLimit

    _make_user("lock-user")

    for index in range(7):
        # the throttle key is case-insensitive, so mixed case shares a counter
        message = _wrong("LOCK-User" if index == 3 else "lock-user")
        assert "不正确" in message
    row = _counter("lock-user")
    assert isinstance(row, LoginRateLimit)
    assert row.hits == 7

    assert "不正确" in _wrong("lock-user")
    assert _counter("lock-user").hits == 8

    # the 9th attempt is rejected before the password is even checked
    with pytest.raises(PermissionError) as caught:
        _attempt("lock-user", "correct-pass-1")
    assert "次数过多" in str(caught.value)
    assert _counter("lock-user").hits == 8


def test_expired_window_resets_counter(db):
    from bidcraft.db import session_scope
    from bidcraft.models import LoginRateLimit, utcnow

    _make_user("window-user")
    assert "不正确" in _wrong("window-user")
    assert "不正确" in _wrong("window-user")
    assert "不正确" in _wrong("window-user")
    assert _counter("window-user").hits == 3

    with session_scope() as session:
        row = session.get(LoginRateLimit, "window-user")
        row.window_start = utcnow() - timedelta(seconds=301)
        row.hits = 8

    # the stale window no longer counts, and the next failure starts a new one
    assert "不正确" in _wrong("window-user")
    row = _counter("window-user")
    assert row.hits == 1
    assert (utcnow() - row.window_start).total_seconds() < 5


def test_successful_login_clears_counter(db):
    _make_user("clear-user")
    assert "不正确" in _wrong("clear-user")
    assert "不正确" in _wrong("clear-user")
    assert _counter("clear-user") is not None

    user, token = _attempt("clear-user", "correct-pass-1")
    assert user.username == "clear-user"
    assert token
    assert _counter("clear-user") is None


def test_purge_expired_rows(db):
    from bidcraft import auth
    from bidcraft.db import session_scope
    from bidcraft.models import LoginRateLimit, utcnow

    now = utcnow()
    with session_scope() as session:
        session.add(
            LoginRateLimit(
                key="stale-key", hits=8, window_start=now - timedelta(seconds=301)
            )
        )
        session.add(LoginRateLimit(key="fresh-key", hits=2, window_start=now))
        removed = auth.purge_expired_rate_limits(session)
    assert removed == 1
    assert _counter("stale-key") is None
    assert _counter("fresh-key").hits == 2


def test_api_login_returns_429_after_threshold(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    for _ in range(8):
        failed = client.post(
            "/auth/login", json={"username": "admin", "password": "nope-nope"}
        )
        assert failed.status_code == 401, failed.text
    assert _counter("admin").hits == 8

    blocked = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert blocked.status_code == 429
    assert "次数过多" in blocked.json()["detail"]

    # the counter is persisted, not per-client
    other = TestClient(create_app())
    assert (
        other.post(
            "/auth/login", json={"username": "admin", "password": "test-admin"}
        ).status_code
        == 429
    )


def test_api_successful_login_clears_counter(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = TestClient(create_app())
    assert (
        admin.post(
            "/auth/login", json={"username": "admin", "password": "test-admin"}
        ).status_code
        == 200
    )
    created = admin.post(
        "/admin/users",
        json={
            "username": "rate-clear",
            "display_name": "限流用户",
            "password": "correct-pass-1",
            "role": "manager",
        },
    )
    assert created.status_code == 201, created.text

    first = TestClient(create_app())
    assert (
        first.post(
            "/auth/login", json={"username": "rate-clear", "password": "wrong-pass"}
        ).status_code
        == 401
    )
    assert (
        first.post(
            "/auth/login", json={"username": "rate-clear", "password": "wrong-pass"}
        ).status_code
        == 401
    )
    assert _counter("rate-clear").hits == 2

    assert (
        first.post(
            "/auth/login",
            json={"username": "rate-clear", "password": "correct-pass-1"},
        ).status_code
        == 200
    )
    assert _counter("rate-clear") is None


def test_registration_still_logs_in(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "fresh-register",
            "password": "register-pass-1",
            "display_name": "新用户",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["user"]["username"] == "fresh-register"
