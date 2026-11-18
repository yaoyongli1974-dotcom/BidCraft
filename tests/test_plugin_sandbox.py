"""插件沙箱（轻量资源隔离）：clamp、stdio preexec/cwd、管理 API 回显、legacy 迁移。

测试不在主进程内调用 preexec 钩子——那会把 rlimit 应用到 pytest 自身；
这里只断言 ``Popen`` 收到了 ``preexec_fn`` 与独立 cwd，并验证目录被清理。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from bidcraft import plugins as plugin_client
from bidcraft.db import session_scope
from bidcraft.models import Plugin

# ------------------------------------------------------------------ clamp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 512),
        ("", 512),
        ("abc", 512),
        (64, 64),
        (4096, 4096),
        (900, 900),
        (63, 512),
        (4097, 512),
        (-1, 512),
        (0, 512),
        (10**9, 512),
    ],
)
def test_clamp_mem_limit(value, expected):
    assert plugin_client.clamp_mem_limit(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 10),
        ("", 10),
        ("abc", 10),
        (1, 1),
        (120, 120),
        (30, 30),
        (0, 10),
        (121, 10),
        (-5, 10),
    ],
)
def test_clamp_cpu_seconds(value, expected):
    assert plugin_client.clamp_cpu_seconds(value) == expected


def test_sandbox_ranges_and_defaults_are_exposed():
    assert plugin_client.MEM_LIMIT_RANGE == (64, 4096)
    assert plugin_client.CPU_SECONDS_RANGE == (1, 120)


def test_apply_limits_returns_posix_preexec_hook(monkeypatch):
    """钩子本身可构造；这里不执行它，避免影响测试进程。"""
    monkeypatch.setattr(plugin_client, "_IS_POSIX", True)
    hook = plugin_client._apply_limits(128, 5)
    assert callable(hook)
    assert hook is not plugin_client._apply_limits(128, 5)


# ------------------------------------------------------ stdio spawn sandbox


def _ref(**overrides) -> plugin_client.PluginRef:
    data = {
        "id": "sandbox-1",
        "name": "sandbox",
        "transport": "stdio",
        "command": sys.executable,
        "args": [],
        "env": {},
        "url": "",
        "enabled": True,
        "tool_allowlist": [],
        "mem_limit_mb": 256,
        "cpu_seconds": 7,
    }
    data.update(overrides)
    return plugin_client.PluginRef(**data)


class _FakeProc:
    """最小 Popen 替身：handshake 立即 EOF，close() 走正常清理路径。"""

    def __init__(self) -> None:
        self.stdin = None
        self.stdout = None
        self._returncode: int | None = 1

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:  # pragma: no cover - 已退出时不会走到
        self._returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode or 0

    def kill(self) -> None:  # pragma: no cover
        self._returncode = -9


def test_stdio_spawn_gets_preexec_and_temp_cwd(monkeypatch):
    monkeypatch.setattr(plugin_client, "_IS_POSIX", True)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    session = plugin_client._StdioSession(_ref())
    with pytest.raises(plugin_client.PluginError):
        # 假进程没有 stdout，握手会失败，但 spawn 已经发生。
        session.__enter__()

    assert captured["preexec_fn"] is not None
    assert callable(captured["preexec_fn"])
    cwd = captured["cwd"]
    assert os.path.basename(cwd).startswith("bidcraft-plugin-")
    assert captured["env"]["PATH"]
    assert "DB" not in captured["env"]
    # 会话结束后临时工作目录被删除。
    assert not os.path.exists(cwd)


def test_temp_cwd_removed_after_successful_close(monkeypatch):
    monkeypatch.setattr(plugin_client, "_IS_POSIX", True)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    session = plugin_client._StdioSession(_ref())
    session._spawn()
    cwd = session._workdir
    assert cwd and os.path.isdir(cwd)

    session.close()
    assert not os.path.exists(cwd)
    # close 幂等：再次调用不会因目录已删而报错。
    session.close()


def test_non_posix_skips_preexec(monkeypatch):
    monkeypatch.setattr(plugin_client, "_IS_POSIX", False)
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    session = plugin_client._StdioSession(_ref())
    session._spawn()
    try:
        assert "preexec_fn" not in captured
        assert captured["cwd"].endswith(os.path.basename(session._workdir or ""))
    finally:
        session.close()


def test_spawn_failure_removes_temp_cwd(monkeypatch):
    created: list[str] = []

    def boom(argv, **kwargs):
        created.append(kwargs["cwd"])
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "Popen", boom)

    with pytest.raises(plugin_client.PluginError):
        plugin_client._StdioSession(_ref())._spawn()

    assert created and not os.path.exists(created[0])


# --------------------------------------------------------------- admin API


def _client():
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def test_admin_plugin_resource_limits_echo_and_clamp(db):
    client = _client()
    created = client.post(
        "/admin/plugins",
        json={
            "name": "沙箱插件",
            "command": "npx",
            "mem_limit_mb": 1024,
            "cpu_seconds": 30,
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["mem_limit_mb"] == 1024
    assert payload["cpu_seconds"] == 30
    plugin_id = payload["id"]

    # 越界值由 clamp 回退默认，而不是 400/422。
    out_of_range = client.patch(
        f"/admin/plugins/{plugin_id}",
        json={"mem_limit_mb": 99999, "cpu_seconds": 0},
    )
    assert out_of_range.status_code == 200, out_of_range.text
    body = out_of_range.json()
    assert body["mem_limit_mb"] == 512
    assert body["cpu_seconds"] == 10

    # 合法值保留并按 PATCH 语义入库。
    updated = client.patch(
        f"/admin/plugins/{plugin_id}",
        json={"mem_limit_mb": 128, "cpu_seconds": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["mem_limit_mb"] == 128
    assert updated.json()["cpu_seconds"] == 5

    listed = client.get("/admin/plugins").json()
    row = next(item for item in listed if item["id"] == plugin_id)
    assert row["mem_limit_mb"] == 128
    assert row["cpu_seconds"] == 5

    with session_scope() as session:
        stored = session.get(Plugin, plugin_id)
        assert stored is not None
        assert stored.mem_limit_mb == 128
        assert stored.cpu_seconds == 5


def test_admin_plugin_resource_limits_default_when_omitted(db):
    client = _client()
    created = client.post("/admin/plugins", json={"name": "默认资源", "command": "npx"})
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["mem_limit_mb"] == 512
    assert payload["cpu_seconds"] == 10

    # PATCH 不带两字段时保持原值，而不是重置为默认。
    patched = client.patch(
        f"/admin/plugins/{payload['id']}", json={"mem_limit_mb": 2048}
    )
    assert patched.status_code == 200
    assert patched.json()["mem_limit_mb"] == 2048
    assert patched.json()["cpu_seconds"] == 10


def test_plugin_payload_clamps_row_values(db):
    with session_scope() as session:
        row = Plugin(name="越界行", command="npx", mem_limit_mb=1, cpu_seconds=999)
        session.add(row)
        session.flush()
        payload = plugin_client.plugin_payload(row)
    assert payload["mem_limit_mb"] == 512
    assert payload["cpu_seconds"] == 10


# ---------------------------------------------------------------- migration


def test_legacy_plugins_table_gains_sandbox_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    from bidcraft.db import _migrate_columns

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'legacy-plugins.db'}", future=True
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE plugins ("
                "id VARCHAR(36) PRIMARY KEY, "
                "name VARCHAR(60) NOT NULL UNIQUE, "
                "transport VARCHAR(10) DEFAULT 'stdio', "
                "command VARCHAR(500) DEFAULT '', "
                "args JSON, "
                "env JSON, "
                "url VARCHAR(500) DEFAULT '', "
                "enabled BOOLEAN DEFAULT 1, "
                "tool_allowlist JSON, "
                "last_test_at DATETIME, "
                "last_test_ok BOOLEAN, "
                "last_test_message TEXT, "
                "fail_streak INTEGER DEFAULT 0, "
                "auto_disabled BOOLEAN DEFAULT 0, "
                "created_at DATETIME, "
                "updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO plugins (id, name, transport, command, args, env, url, "
                "enabled, tool_allowlist, last_test_at, last_test_ok, "
                "last_test_message, fail_streak, auto_disabled, created_at, updated_at) "
                "VALUES ('legacy-plugin', '旧插件', 'stdio', 'npx', '[]', '{}', '', 1, "
                "'[]', NULL, NULL, '', 0, 0, '2026-01-01 00:00:00', "
                "'2026-01-01 00:00:00')"
            )
        )

    _migrate_columns(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("plugins")}
    assert {"mem_limit_mb", "cpu_seconds"} <= columns

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        row = session.get(Plugin, "legacy-plugin")
        assert row is not None
        # 迁移后旧行读取到默认值（ALTER TABLE 的 DEFAULT）。
        assert row.mem_limit_mb == 512
        assert row.cpu_seconds == 10
        # 既可以写入，也能读到新值。
        row.mem_limit_mb = 256
        row.cpu_seconds = 3
        session.commit()
    finally:
        session.close()

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        row = session.get(Plugin, "legacy-plugin")
        assert row is not None
        assert row.mem_limit_mb == 256
        assert row.cpu_seconds == 3
    finally:
        session.close()
        engine.dispose()


def test_new_plugin_row_defaults_to_512_and_10(db):
    with session_scope() as session:
        row = Plugin(name="默认列", command="npx")
        session.add(row)
        session.flush()
        assert row.mem_limit_mb == 512
        assert row.cpu_seconds == 10
