"""插件市场（MCP 客户端插件）：客户端、聚合层、管理 API。

测试不依赖 npx/uvx：stdio 用临时 python 脚本扮演真实子进程 MCP server，
http 用 ``httpx.MockTransport`` 替换 ``plugins.httpx``。
"""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest

from bidcraft import plugins as plugin_client
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.mcp import TOOL_SPECS, handle_message
from bidcraft.models import Plugin

TOOL_NAMES = {
    "list_projects",
    "get_project",
    "get_ledger",
    "run_check",
    "export_document",
    "search_knowledge",
}

FAKE_SERVER = r'''
import json
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "回显文本",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两数相加",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        },
    },
]


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(request_id, value):
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


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
        result(
            rid,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-stdio", "version": "0.1"},
            },
        )
    elif method == "notifications/initialized":
        continue
    elif method == "ping":
        result(rid, {})
    elif method == "tools/list":
        result(rid, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            result(
                rid,
                {
                    "content": [{"type": "text", "text": str(arguments.get("text", ""))}],
                    "isError": False,
                },
            )
        elif name == "add":
            total = float(arguments.get("a", 0)) + float(arguments.get("b", 0))
            result(
                rid,
                {"content": [{"type": "text", "text": str(total)}], "isError": False},
            )
        elif name == "boom":
            result(
                rid,
                {"content": [{"type": "text", "text": "工具内部出错"}], "isError": True},
            )
        elif name == "slow":
            time.sleep(30)
            result(rid, {"content": [], "isError": False})
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32602, "message": "未知工具 " + str(name)},
                }
            )
    else:
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "未知方法"},
            }
        )
'''

# --------------------------------------------------------------------- 辅助


def _server(tmp_path) -> str:
    path = tmp_path / "fake_mcp_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return str(path)


def _ref(name: str = "demo", **overrides) -> plugin_client.PluginRef:
    data = {
        "id": "plugin-1",
        "name": name,
        "transport": "stdio",
        "command": sys.executable,
        "args": [],
        "env": {},
        "url": "",
        "enabled": True,
        "tool_allowlist": [],
    }
    data.update(overrides)
    return plugin_client.PluginRef(**data)


def _add_plugin(name: str, **overrides) -> str:
    fields = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [],
        "env": {},
        "url": "",
        "enabled": True,
        "tool_allowlist": [],
    }
    fields.update(overrides)
    with session_scope() as db:
        row = Plugin(name=name, **fields)
        db.add(row)
        db.flush()
        return row.id


def _rpc(method: str, params: dict | None = None, *, request_id: int = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        message["params"] = params
    return message


def _tools_list(*, readonly: bool = False) -> list[dict]:
    response = handle_message(_rpc("tools/list"), readonly=readonly)
    assert response is not None
    return response["result"]["tools"]


def _call(name: str, arguments: dict | None = None, *, readonly: bool = False) -> dict:
    response = handle_message(
        _rpc("tools/call", {"name": name, "arguments": arguments or {}}),
        readonly=readonly,
    )
    assert response is not None
    return response["result"]


def _client():
    fastapi = pytest.importorskip("fastapi")
    assert fastapi  # 仅用于 importorskip 返回值的显式使用
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


# ------------------------------------------------------------ stdio 插件客户端


def test_stdio_list_tools_and_call(tmp_path):
    server = _server(tmp_path)
    plugin = _ref(args=[server])

    tools = plugin_client.list_tools(plugin)
    assert [tool["name"] for tool in tools] == ["echo", "add"]
    assert tools[0]["description"] == "回显文本"
    assert tools[0]["inputSchema"]["type"] == "object"

    echoed = plugin_client.call_tool(plugin, "echo", {"text": "你好，插件"})
    assert echoed["isError"] is False
    assert echoed["content"][0]["text"] == "你好，插件"

    added = plugin_client.call_tool(plugin, "add", {"a": 1, "b": 2})
    assert added["content"][0]["text"] == "3.0"


def test_stdio_call_tool_passes_through_is_error_result(tmp_path):
    plugin = _ref(args=[_server(tmp_path)])
    result = plugin_client.call_tool(plugin, "boom", {})
    assert result["isError"] is True
    assert result["content"][0]["text"] == "工具内部出错"


def test_stdio_unknown_tool_raises_plugin_error(tmp_path):
    plugin = _ref(args=[_server(tmp_path)])
    with pytest.raises(plugin_client.PluginError) as excinfo:
        plugin_client.call_tool(plugin, "nope", {})
    assert "demo" in str(excinfo.value)
    assert "未知工具" in str(excinfo.value)


def test_stdio_timeout_raises_plugin_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "plugin_timeout", 0.4)
    plugin = _ref(args=[_server(tmp_path)])
    with pytest.raises(plugin_client.PluginError) as excinfo:
        plugin_client.call_tool(plugin, "slow", {})
    message = str(excinfo.value)
    assert "超时" in message
    assert "demo" in message


def test_stdio_bad_command_raises_plugin_error(tmp_path):
    plugin = _ref(command=str(tmp_path / "no-such-binary-xyz"), args=[])
    with pytest.raises(plugin_client.PluginError) as excinfo:
        plugin_client.list_tools(plugin)
    assert "启动失败" in str(excinfo.value)
    assert "demo" in str(excinfo.value)


def test_stdio_nonzero_exit_raises_plugin_error(tmp_path):
    script = tmp_path / "crash.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    plugin = _ref(args=[str(script)])
    with pytest.raises(plugin_client.PluginError) as excinfo:
        plugin_client.list_tools(plugin)
    assert "退出码 3" in str(excinfo.value)


def test_stdio_call_tool_honours_allowlist(tmp_path):
    plugin = _ref(args=[_server(tmp_path)], tool_allowlist=["add"])
    with pytest.raises(plugin_client.PluginError):
        plugin_client.call_tool(plugin, "echo", {"text": "不允许"})


def test_stdio_processes_are_not_left_running(tmp_path):
    plugin = _ref(args=[_server(tmp_path)])
    plugin_client.list_tools(plugin)
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["pgrep", "-f", "fake_mcp_server.py"],
            check=True,
            capture_output=True,
        )


# ------------------------------------------------------------- http 插件客户端

HTTP_TOOLS = [
    {
        "name": "fetch_url",
        "description": "抓取网页",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
    }
]


class _FakeHttpx:
    """替换 ``plugins.httpx``：把 POST 交给 httpx.MockTransport。"""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):  # 对齐 httpx 签名
        self.calls.append({"url": url, "message": json, "timeout": timeout})
        transport = httpx.MockTransport(self.handler)
        with httpx.Client(transport=transport) as client:
            return client.post(url, json=json, timeout=timeout)


def _http_plugin(**overrides) -> plugin_client.PluginRef:
    return _ref(name="remote", transport="http", command="", url="https://plugin.example/mcp", **overrides)


def _http_handler(*, status: int = 200, error: str = ""):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        request_id = payload.get("id")
        if status >= 400:
            return httpx.Response(status, json={"detail": "boom"})
        if error:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "error": {"message": error}},
            )
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "fake-http", "version": "0.1"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": {"tools": HTTP_TOOLS}},
            )
        if method == "tools/call":
            arguments = (payload.get("params") or {}).get("arguments") or {}
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": str(arguments.get("url", ""))}],
                        "isError": False,
                    },
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": request_id, "error": {"message": "未知方法"}}
        )

    return handler


def test_http_list_tools_handshake_order(monkeypatch):
    stub = _FakeHttpx(_http_handler())
    monkeypatch.setattr(plugin_client, "httpx", stub)
    monkeypatch.setattr(settings, "plugin_timeout", 5)

    tools = plugin_client.list_tools(_http_plugin())
    assert [tool["name"] for tool in tools] == ["fetch_url"]
    methods = [call["message"]["method"] for call in stub.calls]
    assert methods == ["initialize", "notifications/initialized", "tools/list"]
    assert all(call["timeout"] == 5 for call in stub.calls)
    assert stub.calls[0]["url"] == "https://plugin.example/mcp"


def test_http_call_tool(monkeypatch):
    monkeypatch.setattr(plugin_client, "httpx", _FakeHttpx(_http_handler()))
    result = plugin_client.call_tool(_http_plugin(), "fetch_url", {"url": "https://example.com"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "https://example.com"


def test_http_error_status_and_rpc_error(monkeypatch):
    monkeypatch.setattr(plugin_client, "httpx", _FakeHttpx(_http_handler(status=500)))
    with pytest.raises(plugin_client.PluginError) as excinfo:
        plugin_client.list_tools(_http_plugin())
    assert "HTTP 500" in str(excinfo.value)

    monkeypatch.setattr(plugin_client, "httpx", _FakeHttpx(_http_handler(error="握手被拒")))
    with pytest.raises(plugin_client.PluginError) as excinfo:
        plugin_client.list_tools(_http_plugin())
    assert "握手被拒" in str(excinfo.value)


def test_test_plugin_reports_ok_and_failure(monkeypatch):
    monkeypatch.setattr(plugin_client, "httpx", _FakeHttpx(_http_handler()))
    ok, message, tools = plugin_client.test_plugin(_http_plugin())
    assert ok is True
    assert tools == ["fetch_url"]
    assert "1 个工具" in message

    broken = _ref(name="broken", command="/no/such/binary")
    ok, message, tools = plugin_client.test_plugin(broken)
    assert ok is False
    assert tools == []
    assert "broken" in message


def test_unsupported_transport_raises_plugin_error():
    plugin = _ref(transport="carrier-pigeon")
    with pytest.raises(plugin_client.PluginError):
        plugin_client.list_tools(plugin)


def test_catalog_presets_are_templates_only(db):
    names = [item["name"] for item in plugin_client.PLUGIN_CATALOG]
    assert {"filesystem", "fetch", "memory", "time"} <= set(names)
    for item in plugin_client.PLUGIN_CATALOG:
        assert set(item) == {"name", "description", "transport", "command", "args", "url"}
        assert item["transport"] == "stdio"
        assert item["args"]
        # 预设只是模板：不安装、不写库。
    with session_scope() as session:
        assert session.query(Plugin).count() == 0


# ------------------------------------------------------------------ 管理 API


def test_admin_plugin_crud_and_validation(db):
    client = _client()
    created = client.post(
        "/admin/plugins",
        json={
            "name": "本地文件",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {"LOG_LEVEL": "error"},
            "tool_allowlist": "read_file, list_directory",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["name"] == "本地文件"
    assert payload["transport"] == "stdio"
    assert payload["args"][0] == "-y"
    assert payload["env"] == {"LOG_LEVEL": "error"}
    assert payload["tool_allowlist"] == ["read_file", "list_directory"]
    assert payload["enabled"] is True
    assert payload["last_test_ok"] is None
    plugin_id = payload["id"]

    duplicated = client.post(
        "/admin/plugins", json={"name": "本地文件", "command": "npx"}
    )
    assert duplicated.status_code == 409
    assert "已存在" in duplicated.json()["detail"]

    assert client.post("/admin/plugins", json={"name": "缺命令"}).status_code == 400
    no_url = client.post("/admin/plugins", json={"name": "缺地址", "transport": "http"})
    assert no_url.status_code == 400
    assert "url" in no_url.json()["detail"]
    bad_transport = client.post(
        "/admin/plugins", json={"name": "怪传输", "transport": "carrier"}
    )
    assert bad_transport.status_code == 400
    double_underscore = client.post(
        "/admin/plugins", json={"name": "a__b", "command": "npx"}
    )
    assert double_underscore.status_code == 400

    listed = client.get("/admin/plugins")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["本地文件"]

    patched = client.patch(
        f"/admin/plugins/{plugin_id}",
        json={"enabled": False, "tool_allowlist": [], "args": "[]", "command": "uvx"},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["args"] == []
    assert patched.json()["command"] == "uvx"

    conflict = client.patch(
        "/admin/plugins", json={}
    )  # 无 id 不合法：确认路由不会被 catch-all 吞掉
    assert conflict.status_code in {404, 405}

    deleted = client.delete(f"/admin/plugins/{plugin_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": plugin_id}
    assert client.delete(f"/admin/plugins/{plugin_id}").status_code == 404
    assert client.patch("/admin/plugins/missing", json={"enabled": True}).status_code == 404


def test_admin_plugin_catalog_endpoint(db):
    client = _client()
    response = client.get("/admin/plugins/catalog")
    assert response.status_code == 200
    items = response.json()
    assert [item["name"] for item in items] == ["filesystem", "fetch", "memory", "time"]
    assert items[0]["command"] == "npx"


def test_admin_plugin_test_writes_last_test_fields(db, tmp_path):
    client = _client()
    server = _server(tmp_path)
    created = client.post(
        "/admin/plugins",
        json={
            "name": "假 server",
            "transport": "stdio",
            "command": sys.executable,
            "args": [server],
        },
    )
    assert created.status_code == 201
    plugin_id = created.json()["id"]

    tested = client.post(f"/admin/plugins/{plugin_id}/test")
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] is True
    assert body["tools"] == ["echo", "add"]
    assert body["message"]

    listed = client.get("/admin/plugins").json()
    row = next(item for item in listed if item["id"] == plugin_id)
    assert row["last_test_ok"] is True
    assert row["last_test_at"]
    assert row["last_test_message"] == body["message"]


def test_admin_plugin_test_failure_is_not_500(db):
    client = _client()
    created = client.post(
        "/admin/plugins",
        json={"name": "坏插件", "command": "/definitely/not/a/binary"},
    )
    plugin_id = created.json()["id"]

    tested = client.post(f"/admin/plugins/{plugin_id}/test")
    assert tested.status_code == 200
    body = tested.json()
    assert body["ok"] is False
    assert body["tools"] == []
    assert "坏插件" in body["message"]

    row = next(item for item in client.get("/admin/plugins").json() if item["id"] == plugin_id)
    assert row["last_test_ok"] is False
    assert client.post("/admin/plugins/missing/test").status_code == 404


def test_admin_plugin_requires_super_admin(db):
    admin = _client()
    assert (
        admin.post(
            "/admin/users",
            json={
                "username": "viewer2",
                "password": "viewer-pass-2",
                "role": "viewer",
            },
        ).status_code
        == 201
    )

    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "viewer2", "password": "viewer-pass-2"}
    )
    assert login.status_code == 200
    assert viewer.get("/admin/plugins").status_code == 403
    assert viewer.get("/admin/plugins/catalog").status_code == 403
    assert viewer.post("/admin/plugins", json={"name": "x", "command": "npx"}).status_code == 403


def test_plugin_audit_entries(db):
    from sqlalchemy import select

    from bidcraft.models import AuditLog

    client = _client()
    created = client.post(
        "/admin/plugins", json={"name": "审计插件", "command": "/no/such/plugin-binary"}
    )
    plugin_id = created.json()["id"]
    client.patch(f"/admin/plugins/{plugin_id}", json={"enabled": False})
    client.post(f"/admin/plugins/{plugin_id}/test")
    client.delete(f"/admin/plugins/{plugin_id}")

    with session_scope() as session:
        actions = set(
            session.scalars(
                select(AuditLog.action).where(AuditLog.action.like("plugin.%"))
            ).all()
        )
    assert {"plugin.create", "plugin.update", "plugin.test", "plugin.delete"} <= actions


# --------------------------------------------------------------- MCP 聚合


def test_tools_list_has_six_builtin_tools_without_plugins(db):
    tools = _tools_list()
    assert len(tools) == len(TOOL_SPECS) == 6
    assert {tool["name"] for tool in tools} == TOOL_NAMES


def test_tools_list_aggregates_enabled_plugins_with_allowlist(db, tmp_path):
    server = _server(tmp_path)
    _add_plugin("demo", args=[server])
    _add_plugin("limited", args=[server], tool_allowlist=["echo"])
    _add_plugin("hidden", args=[server], enabled=False)

    tools = _tools_list()
    names = [tool["name"] for tool in tools]
    assert names[:6] == [spec["name"] for spec in TOOL_SPECS]
    assert "demo__echo" in names
    assert "demo__add" in names
    assert "limited__echo" in names
    assert "limited__add" not in names
    assert "hidden__echo" not in names

    demo_echo = next(tool for tool in tools if tool["name"] == "demo__echo")
    assert demo_echo["description"] == "[demo] 回显文本"
    assert demo_echo["inputSchema"]["type"] == "object"


def test_tools_list_skips_failing_plugin_with_stderr_warning(db, tmp_path, capsys):
    _add_plugin("broken", command="/no/such/plugin-binary")
    _add_plugin("working", args=[_server(tmp_path)])

    tools = _tools_list()
    names = [tool["name"] for tool in tools]
    assert "working__echo" in names
    assert not any(name.startswith("broken__") for name in names)
    assert len(names) == 6 + 2

    err = capsys.readouterr().err
    assert "broken" in err
    assert "已跳过" in err


def test_tools_call_routes_to_plugin(db, tmp_path):
    _add_plugin("demo", args=[_server(tmp_path)])

    result = _call("demo__echo", {"text": "聚合路由"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "聚合路由"

    passthrough = _call("demo__boom")
    assert passthrough["isError"] is True
    assert passthrough["content"][0]["text"] == "工具内部出错"


def test_tools_call_enforces_allowlist_and_enabled(db, tmp_path):
    _add_plugin("limited", args=[_server(tmp_path)], tool_allowlist=["add"])
    _add_plugin("hidden", args=[_server(tmp_path)], enabled=False)

    denied = _call("limited__echo", {"text": "越权"})
    assert denied["isError"] is True
    assert "allowlist" in denied["content"][0]["text"]

    allowed = _call("limited__add", {"a": 2, "b": 5})
    assert allowed["isError"] is False
    assert allowed["content"][0]["text"] == "7.0"

    disabled = _call("hidden__echo", {"text": "禁用"})
    assert disabled["isError"] is True
    assert "已禁用" in disabled["content"][0]["text"]


def test_tools_call_readonly_refuses_plugin_tools(db, tmp_path):
    _add_plugin("demo", args=[_server(tmp_path)])

    result = _call("demo__echo", {"text": "只读"}, readonly=True)
    assert result["isError"] is True
    assert "只读" in result["content"][0]["text"]

    # 内置工具行为不变
    listing = _call("list_projects", readonly=True)
    assert listing["isError"] is False


def test_tools_call_plugin_failure_is_tool_error(db, tmp_path):
    _add_plugin("broken", command="/no/such/plugin-binary")

    result = _call("broken__echo", {"text": "x"})
    assert result["isError"] is True
    assert "broken" in result["content"][0]["text"]


def test_unknown_tool_still_reports_unknown(db):
    result = _call("no_such_tool")
    assert result["isError"] is True
    assert "未知工具" in result["content"][0]["text"]

    result = _call("no_such_plugin__echo")
    assert result["isError"] is True
    assert "未知工具" in result["content"][0]["text"]
