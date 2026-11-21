"""身份 ContextVar 的 set/reset 语义（脱离 FastAPI）。

认证中间件把身份放进 ContextVar，请求结束后必须还原：否则同一 context 里
后续执行的工作会「继承」上一个请求的用户。后台任务刻意不读这个上下文，
显式传 user_id / project_id / 权限（见 workers/ 与 services/）。
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, copy_context

import pytest

from bidcraft.context import current_user, reset_current_user, set_current_user


def test_set_returns_token_and_current_user_reads_payload():
    assert current_user() is None
    payload = {"id": "u-1", "username": "alice", "role": "owner"}
    token = set_current_user(payload)
    try:
        assert current_user() == payload
    finally:
        reset_current_user(token)
    assert current_user() is None


def test_reset_restores_previous_value():
    first = {"username": "first"}
    second = {"username": "second"}
    t1 = set_current_user(first)
    t2 = set_current_user(second)
    try:
        assert current_user() == second
        reset_current_user(t2)
        assert current_user() == first
    finally:
        reset_current_user(t1)
    assert current_user() is None


def test_reset_token_from_another_context_raises():
    """ContextVar token 只能在创建它的 context 里 reset（标准语义）。"""

    token_holder: list = []

    def set_in_context():
        token_holder.append(set_current_user({"username": "inner"}))

    ctx = copy_context()
    ctx.run(set_in_context)
    token = token_holder[0]
    with pytest.raises(ValueError):
        reset_current_user(token)


def test_context_is_isolated_between_threads():
    """新线程有独立的空 context——后台线程读不到请求身份，必须显式传参。"""

    seen: list = []

    def read_in_thread() -> None:
        seen.append(current_user())

    token = set_current_user({"username": "request-user"})
    try:
        thread = threading.Thread(target=read_in_thread)
        thread.start()
        thread.join()
        # 线程不继承父线程的 ContextVar 值，读到默认 None
        assert seen == [None]
    finally:
        reset_current_user(token)
    assert current_user() is None


def test_can_reset_to_none_explicitly():
    token = set_current_user({"username": "x"})
    reset_current_user(token)
    assert current_user() is None


def test_contextvar_type_is_standard():
    """守护：底层仍是标准 ContextVar，reset 走标准 Token API。"""

    var = ContextVar("probe", default=None)
    t = var.set("v")
    assert var.get() == "v"
    var.reset(t)
    assert var.get() is None


def test_auth_middleware_resets_identity_after_request(db, monkeypatch):
    """认证中间件在请求结束后必须调用 reset_current_user(token)。"""

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bidcraft import api as api_module
    from bidcraft.config import settings

    calls: list = []
    real_reset = api_module.reset_current_user

    def spy_reset(token):
        calls.append(token)
        return real_reset(token)

    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "signup_credits", 0)
    monkeypatch.setattr(api_module, "reset_current_user", spy_reset)
    from bidcraft.api import create_app

    app = create_app()
    app.state.testing = True
    client = TestClient(app)
    # 注册会自动建立会话并写入 cookie；随后的 /auth/me 是需认证路由，
    # 中间件会在其 context 里 set 身份，请求返回后必须 reset。
    reg = client.post(
        "/auth/register",
        json={
            "username": "ctx-reset-user",
            "password": "passw0rd123",
            "accept_terms": True,
        },
    )
    assert reg.status_code == 201, reg.text
    # 注册请求本身无 cookie（401 早返回，不 set/reset）；两次已认证请求各 reset 一次
    assert client.get("/auth/me").status_code == 200
    assert client.get("/auth/me").status_code == 200
    assert len(calls) == 2
