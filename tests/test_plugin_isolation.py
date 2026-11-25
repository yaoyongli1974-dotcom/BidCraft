"""插件沙箱与作用域隔离：出站地址防护、个人插件可见性、审计归属。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from bidcraft import mcp, plugins
from bidcraft.db import session_scope
from bidcraft.models import AuditLog, Plugin


@pytest.fixture
def client(db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    test_client = TestClient(create_app())
    response = test_client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return test_client


def _create_user(client, username: str) -> str:
    response = client.post(
        "/admin/users",
        json={"username": username, "password": "viewer-pass-1", "role": "viewer"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --------------------------------------------------------------------- 作用域


def test_plugin_scope_validation():
    assert plugins.plugin_scope(None) == "global"
    assert plugins.plugin_scope(" OWNER ") == "owner"
    with pytest.raises(ValueError, match="scope"):
        plugins.plugin_scope("tenant")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/mcp",
        "http://[::1]/mcp",
        "http://10.0.0.5/mcp",
        "http://192.168.1.10/mcp",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/mcp",
        "http://metadata.google.internal/computeMetadata",
        "http://user:pass@plugin.example/mcp",
        "ftp://plugin.example/mcp",
    ],
)
def test_plugin_url_allowed_rejects_unsafe(url):
    assert plugins.plugin_url_allowed(url) is False


@pytest.mark.parametrize(
    "url",
    ["https://plugin.example/mcp", "https://8.8.8.8/mcp", "http://1.1.1.1/mcp"],
)
def test_plugin_url_allowed_accepts_public(url):
    assert plugins.plugin_url_allowed(url) is True


def test_plugin_url_allow_private_net_opt_in():
    url = "http://10.0.0.5/mcp"
    assert plugins.plugin_url_allowed(url) is False
    assert plugins.plugin_url_allowed(url, allow_private_net=True) is True
    with pytest.raises(ValueError, match="allow_private_net"):
        plugins.plugin_validate("http", "", url)
    plugins.plugin_validate("http", "", url, allow_private_net=True)


def test_plugin_url_resolution_blocks_private_ip(monkeypatch):
    monkeypatch.setattr(
        plugins.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))]
    )
    assert plugins.plugin_url_allowed("https://plugin.internal/mcp") is False
    # 保留测试域跳过解析，避免离线环境 DNS 阻塞
    assert plugins.plugin_url_allowed("https://plugin.example/mcp") is True


def test_plugin_url_dns_failure_fails_open(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("dns down")

    monkeypatch.setattr(plugins.socket, "getaddrinfo", boom)
    assert plugins.plugin_url_allowed("https://plugin.internal/mcp") is True


def test_http_post_rejects_private_url_at_call_time(monkeypatch):
    called = []
    monkeypatch.setattr(
        plugins.httpx, "post", lambda *a, **k: called.append(True) or None  # noqa: ARG005
    )
    plugin = plugins.PluginRef(
        id="1",
        name="内网插件",
        enabled=True,
        transport="http",
        url="http://127.0.0.1:8080/mcp",
    )
    with pytest.raises(plugins.PluginError, match="私网"):
        plugins._http_post(plugin, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert called == []


def test_visible_to():
    owner_plugin = plugins.PluginRef(
        id="1", name="p", enabled=True, transport="stdio", scope="owner", owner_id="u-1"
    )
    global_plugin = plugins.PluginRef(
        id="2", name="g", enabled=True, transport="stdio", scope="global"
    )
    assert plugins.visible_to(global_plugin) is True
    assert plugins.visible_to(global_plugin, user_id="u-2") is True
    assert plugins.visible_to(owner_plugin) is False  # 本地 MCP 无身份
    assert plugins.visible_to(owner_plugin, user_id="u-2") is False
    assert plugins.visible_to(owner_plugin, user_id="u-1") is True
    assert plugins.visible_to(owner_plugin, is_super_admin=True) is True


def test_snapshot_and_payload_expose_scope():
    row = Plugin(
        name="scoped",
        transport="stdio",
        command="python",
        scope="owner",
        owner_id="u-1",
        allow_private_net=True,
    )
    ref = plugins.snapshot(row)
    assert (ref.scope, ref.owner_id, ref.allow_private_net) == ("owner", "u-1", True)
    payload = plugins.plugin_payload(row)
    assert payload["scope"] == "owner"
    assert payload["owner_id"] == "u-1"
    assert payload["allow_private_net"] is True


# ------------------------------------------------------------------ 管理 API


def test_admin_create_owner_plugin_validation(client, db):
    bad = client.post(
        "/admin/plugins",
        json={"name": "缺归属", "command": "npx", "scope": "owner"},
    )
    assert bad.status_code == 400
    missing = client.post(
        "/admin/plugins",
        json={"name": "用户不存在", "command": "npx", "scope": "owner", "owner_id": "ghost"},
    )
    assert missing.status_code == 400

    owner_id = _create_user(client, "plugowner")
    created = client.post(
        "/admin/plugins",
        json={
            "name": "个人插件",
            "command": "npx",
            "scope": "owner",
            "owner_id": owner_id,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["scope"] == "owner"
    assert created.json()["owner_id"] == owner_id


def test_admin_rejects_private_http_url(client, db):
    bad = client.post(
        "/admin/plugins",
        json={"name": "内网", "transport": "http", "url": "http://127.0.0.1/mcp"},
    )
    assert bad.status_code == 400
    ok = client.post(
        "/admin/plugins",
        json={
            "name": "可信内网",
            "transport": "http",
            "url": "http://10.0.0.5/mcp",
            "allow_private_net": True,
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["allow_private_net"] is True


def test_admin_update_scope_clears_owner(client, db):
    owner_id = _create_user(client, "scopeowner")
    created = client.post(
        "/admin/plugins",
        json={"name": "切换", "command": "npx", "scope": "owner", "owner_id": owner_id},
    )
    plugin_id = created.json()["id"]
    updated = client.patch(f"/admin/plugins/{plugin_id}", json={"scope": "global"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["scope"] == "global"
    assert updated.json()["owner_id"] in (None, "")


# -------------------------------------------------------------- MCP 与审计


def test_mcp_enabled_plugins_and_target_visibility(db):
    with session_scope() as session:
        session.add_all(
            [
                Plugin(name="glob", transport="stdio", command="npx", enabled=True),
                Plugin(
                    name="mine",
                    transport="stdio",
                    command="npx",
                    enabled=True,
                    scope="owner",
                    owner_id="u-1",
                ),
            ]
        )
        session.flush()

    assert [p.name for p in mcp._enabled_plugins()] == ["glob"]
    assert {p.name for p in mcp._enabled_plugins(user_id="u-1")} == {"glob", "mine"}
    assert {p.name for p in mcp._enabled_plugins(is_super_admin=True)} == {"glob", "mine"}
    assert mcp._plugin_target("mine__echo") is None
    assert mcp._plugin_target("mine__echo", user_id="u-2") is None
    target = mcp._plugin_target("mine__echo", user_id="u-1")
    assert target is not None and target[1] == "echo"


def test_record_call_attribution(db):
    with session_scope() as session:
        row = Plugin(name="审计归属", transport="stdio", command="npx")
        session.add(row)
        session.flush()
        plugin_id = row.id

    with session_scope() as session:
        plugins.record_call(
            session, plugin_id, "echo", True, username="alice", project_id="p-1"
        )

    with session_scope() as session:
        log = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "plugin.call", AuditLog.username == "alice"
            )
        )
    assert log is not None
    assert log.project_id == "p-1"
    assert log.detail["scope"] == "global"


def test_registry_identity_from_context(monkeypatch):
    from bidcraft import orchestration

    monkeypatch.setattr(
        orchestration.registry, "current_user", lambda: {"id": "u-9", "username": "bob", "role": "viewer"}
    )
    identity = orchestration.registry._plugin_identity()
    assert identity == {
        "user_id": "u-9",
        "is_super_admin": False,
        "username": "bob",
    }
    monkeypatch.setattr(orchestration.registry, "current_user", lambda: None)
    assert orchestration.registry._plugin_identity() == {
        "user_id": None,
        "is_super_admin": False,
        "username": None,
    }


# ------------------------------------------------------------ Agent 插件工具审计


_ECHO_SERVER = r'''
import json
import sys

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    try:
        msg = json.loads(raw)
    except ValueError:
        continue
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {},
                  "serverInfo": {"name": "echo", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "echo",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        text = str(((msg.get("params") or {}).get("arguments") or {}).get("text", ""))
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()
'''


def test_agent_plugin_tool_real_execution_and_audit(db, tmp_path):
    """真实 stdio 插件进程 + 真实 registry/mcp；审计带 project_id/user_id。"""

    import sys

    from bidcraft.models import User
    from bidcraft.orchestration import registry
    from bidcraft.pipeline import create_project
    from bidcraft.security import hash_password

    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER, encoding="utf-8")
    with session_scope() as session:
        owner = User(
            username="plugin-agent-owner",
            password_hash=hash_password("pass-123"),
            role="super_admin",
        )
        session.add(owner)
        session.flush()
        project = create_project(session, "plugin-agent-project")
        project.owner_id = owner.id
        session.add(
            Plugin(name="agent_echo", transport="stdio", command=sys.executable,
                   args=[str(script)], enabled=True)
        )
        actor = {"id": owner.id, "username": owner.username, "role": owner.role}
        pid = project.id

    result = registry.call_tool(
        "agent_echo__echo",
        {"text": "真实插件执行"},
        readonly=False,
        actor=actor,
        project_id=pid,
        run_id="run-plugin",
        task_id="task-plugin",
    )
    assert result["isError"] is False
    assert "真实插件执行" in result["content"][0]["text"]

    denied = registry.call_tool(
        "agent_echo__echo", {"text": "拒绝"}, readonly=True, actor=actor, project_id=pid
    )
    assert denied["isError"] is True

    with session_scope() as session:
        logs = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(("plugin.call", "agent.tool.call"))
                )
            )
        )
    assert any(log.project_id == pid and log.username == actor["username"] for log in logs)
    assert any(log.detail["tool"] == "echo" and log.detail["user_id"] == actor["id"] for log in logs)
    assert any(log.detail["ok"] is True for log in logs)
    assert any(log.detail["ok"] is False for log in logs)
