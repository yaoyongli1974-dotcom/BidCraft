"""MCP 客户端插件：把外部 MCP server 的工具聚合进 BidCraft 自己的 MCP 服务。

两种传输方式：

* ``stdio``：以本机子进程运行 ``command + args``，逐行 JSON-RPC；
* ``http``：把 JSON-RPC 作为 POST body 发到插件 ``url``。

两者都按 MCP 2024-11-05 握手：``initialize`` → ``notifications/initialized``
→ ``tools/list`` 或 ``tools/call``。插件不常驻：每次调用结束后子进程被终止
（HTTP 则直接结束请求），因此插件崩溃不会留下僵尸进程，也不会在两次调用之间
保留内存状态。

安全边界：插件以当前操作系统用户身份运行，或把请求发到用户填写的 URL。
只有超级管理员能配置插件，且只应在受信的部署里启用。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

import httpx
from httpx import HTTPError

from . import __version__
from .config import settings

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "bidcraft-plugin-client"
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
#: 子进程只用最小环境变量，避免把数据库、模型密钥等传给第三方插件。
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

#: 内置预设：只提供一份可编辑的模板，绝不自动安装或启用。
PLUGIN_CATALOG: list[dict[str, Any]] = [
    {
        "name": "filesystem",
        "description": "读写本机指定目录（官方 filesystem server，需要 Node/npx；请把最后一个参数换成允许访问的目录）",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/directory"],
        "url": "",
    },
    {
        "name": "fetch",
        "description": "抓取网页正文（官方 fetch server，需要 uv/uvx）",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "url": "",
    },
    {
        "name": "memory",
        "description": "跨会话知识图谱记忆（官方 memory server，需要 Node/npx）",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "url": "",
    },
    {
        "name": "time",
        "description": "时区与时间换算（官方 time server，需要 uv/uvx）",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-time"],
        "url": "",
    },
]


class PluginError(RuntimeError):
    """插件启动、握手或调用失败；消息为中文，可直接展示给管理员。"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def timeout_seconds() -> float:
    """单请求超时（秒）。配置层已 clamp 到 5..120，这里再兜底一次。"""
    try:
        value = float(getattr(settings, "plugin_timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


@dataclass(slots=True)
class PluginRef:
    """与数据库解耦的插件快照，子进程/HTTP 调用期间不需要持有 session。"""

    id: str = ""
    name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    enabled: bool = True
    tool_allowlist: list[str] = field(default_factory=list)


def snapshot(plugin: Any) -> PluginRef:
    """把 ORM 行（或任何同名字段的对象）转成 PluginRef。"""
    if isinstance(plugin, PluginRef):
        return plugin
    return PluginRef(
        id=str(getattr(plugin, "id", "") or ""),
        name=str(getattr(plugin, "name", "") or ""),
        transport=str(getattr(plugin, "transport", "stdio") or "stdio").strip().lower(),
        command=str(getattr(plugin, "command", "") or "").strip(),
        args=[str(item) for item in (getattr(plugin, "args", None) or [])],
        env={str(key): str(value) for key, value in (getattr(plugin, "env", None) or {}).items()},
        url=str(getattr(plugin, "url", "") or "").strip(),
        enabled=bool(getattr(plugin, "enabled", True)),
        tool_allowlist=[
            str(item).strip()
            for item in (getattr(plugin, "tool_allowlist", None) or [])
            if str(item).strip()
        ],
    )


def allows(plugin: Any, tool_name: str) -> bool:
    """allowlist 为空表示全部允许。"""
    ref = snapshot(plugin)
    return not ref.tool_allowlist or tool_name in ref.tool_allowlist


def plugin_payload(row: Any) -> dict[str, Any]:
    """管理端序列化；env 可能含密钥，此 payload 只允许超管路由返回。"""
    ref = snapshot(row)
    return {
        "id": ref.id,
        "name": ref.name,
        "transport": ref.transport,
        "command": ref.command,
        "args": list(ref.args),
        "env": dict(ref.env),
        "url": ref.url,
        "enabled": bool(getattr(row, "enabled", True)),
        "tool_allowlist": list(ref.tool_allowlist),
        "last_test_ok": getattr(row, "last_test_ok", None),
        "last_test_at": _iso(getattr(row, "last_test_at", None)),
        "last_test_message": getattr(row, "last_test_message", "") or "",
        "created_at": _iso(getattr(row, "created_at", None)),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _child_env(plugin: PluginRef) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH") or _FALLBACK_PATH,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    for key, value in plugin.env.items():
        key = str(key).strip()
        if key:
            env[key] = str(value)
    return env


def _normalize_tools(raw: Any, plugin_name: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise PluginError(f"插件 {plugin_name} 的 tools/list 返回格式不正确")
    tools: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        schema = item.get("inputSchema")
        tools.append(
            {
                "name": name.strip(),
                "description": str(item.get("description") or ""),
                "inputSchema": schema
                if isinstance(schema, dict)
                else {"type": "object", "properties": {}},
            }
        )
    return tools


class _StdioSession:
    """一次调用生命周期内的 stdio MCP 会话。"""

    def __init__(self, plugin: PluginRef) -> None:
        self.plugin = plugin
        self.proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0

    # -- 生命周期 -----------------------------------------------------
    def __enter__(self) -> Self:
        self._spawn()
        try:
            self._handshake()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        self.close()
        return False

    def _spawn(self) -> None:
        if not self.plugin.command:
            raise PluginError(f"插件 {self.plugin.name} 未配置 command")
        argv = [self.plugin.command, *self.plugin.args]
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=_child_env(self.plugin),
            )
        except (OSError, ValueError) as exc:
            raise PluginError(f"插件 {self.plugin.name} 启动失败：{exc}") from exc
        threading.Thread(
            target=self._pump,
            name=f"plugin-{self.plugin.name}",
            daemon=True,
        ).start()

    def _pump(self) -> None:
        proc = self.proc
        try:
            if proc is not None and proc.stdout is not None:
                for line in proc.stdout:
                    self._lines.put(line)
        except Exception:  # noqa: BLE001 - 读线程不得抛出
            return
        finally:
            self._lines.put(None)

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        finally:
            if proc.poll() is None:
                proc.kill()

    # -- 协议 ---------------------------------------------------------
    def _send(self, message: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise PluginError(f"插件 {self.plugin.name} 已停止")
        try:
            proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise PluginError(f"插件 {self.plugin.name} 连接中断：{exc}") from exc

    def _exit_code(self) -> int | None:
        return self.proc.poll() if self.proc is not None else None

    def _read(self) -> dict[str, Any]:
        limit = timeout_seconds()
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PluginError(
                    f"插件 {self.plugin.name} 响应超时（超过 {limit:.0f} 秒）"
                )
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                code = self._exit_code()
                if code is not None:
                    raise PluginError(
                        f"插件 {self.plugin.name} 提前退出（退出码 {code}）"
                    ) from None
                raise PluginError(
                    f"插件 {self.plugin.name} 响应超时（超过 {limit:.0f} 秒）"
                ) from None
            if line is None:
                code = self._exit_code()
                raise PluginError(f"插件 {self.plugin.name} 连接已关闭（退出码 {code}）")
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # 有些 server 会往 stdout 混入日志；跳过而不是直接失败。
                continue
            if isinstance(data, dict):
                return data

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        while True:
            data = self._read()
            if data.get("method") is not None:
                # server → client 的请求：本客户端不支持，回错误避免对端挂住。
                if "id" in data:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": data.get("id"),
                            "error": {"code": -32601, "message": "客户端不支持该请求"},
                        }
                    )
                continue
            if data.get("id") != request_id:
                continue
            error = data.get("error")
            if error:
                detail = (
                    error.get("message") if isinstance(error, dict) else str(error)
                ) or "未知错误"
                raise PluginError(f"插件 {self.plugin.name} 返回错误：{detail}")
            result = data.get("result")
            return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _handshake(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
            },
        )
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        return _normalize_tools(result.get("tools"), self.plugin.name)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def _http_post(plugin: PluginRef, message: dict[str, Any]) -> dict[str, Any] | None:
    """POST 一条 JSON-RPC；通知没有响应体时返回 None。"""
    try:
        response = httpx.post(plugin.url, json=message, timeout=timeout_seconds())
    except HTTPError as exc:
        raise PluginError(f"插件 {plugin.name} 连接失败：{exc}") from exc
    except Exception as exc:
        raise PluginError(f"插件 {plugin.name} 连接失败：{exc}") from exc
    if response.status_code >= 400:
        raise PluginError(f"插件 {plugin.name} 返回 HTTP {response.status_code}")
    declared = (response.headers.get("content-length") or "").strip()
    if (declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES) or (
        len(response.content or b"") > MAX_RESPONSE_BYTES
    ):
        raise PluginError(f"插件 {plugin.name} 响应过大（超过 2MB）")
    try:
        data = response.json()
    except ValueError:
        if "method" in message and "id" not in message:
            return None
        raise PluginError(f"插件 {plugin.name} 响应不是 JSON") from None
    if not isinstance(data, dict):
        if "id" not in message:
            return None
        raise PluginError(f"插件 {plugin.name} 响应格式不正确")
    error = data.get("error")
    if error:
        detail = (error.get("message") if isinstance(error, dict) else str(error)) or "未知错误"
        raise PluginError(f"插件 {plugin.name} 返回错误：{detail}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


class _HttpSession:
    """HTTP 传输没有进程可管理，只保留 JSON-RPC 请求序号。"""

    def __init__(self, plugin: PluginRef) -> None:
        self.plugin = plugin
        self._next_id = 0

    def __enter__(self) -> Self:
        self._handshake()
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        return _http_post(self.plugin, message) or {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        _http_post(
            self.plugin,
            {"jsonrpc": "2.0", "method": method, "params": params or {}},
        )

    def _handshake(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
            },
        )
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        return _normalize_tools(result.get("tools"), self.plugin.name)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def _session(plugin: Any) -> _StdioSession | _HttpSession:
    ref = snapshot(plugin)
    if ref.transport == "http":
        if not ref.url:
            raise PluginError(f"插件 {ref.name} 未配置 url")
        return _HttpSession(ref)
    if ref.transport == "stdio":
        return _StdioSession(ref)
    raise PluginError(f"插件 {ref.name} 的传输方式不支持：{ref.transport}")


def list_tools(plugin: Any) -> list[dict[str, Any]]:
    """握手后列出插件工具（name/description/inputSchema）。"""
    with _session(plugin) as session:
        return session.list_tools()


def call_tool(plugin: Any, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """调用插件工具，原样返回 MCP result。"""
    ref = snapshot(plugin)
    tool = (name or "").strip()
    if not tool:
        raise PluginError(f"插件 {ref.name} 缺少工具名")
    args = arguments if isinstance(arguments, dict) else {}
    if not allows(ref, tool):
        raise PluginError(f"插件 {ref.name} 未授权工具 {tool}")
    with _session(ref) as session:
        return session.call_tool(tool, args)


def test_plugin(plugin: Any) -> tuple[bool, str, list[str]]:
    """测试连接：返回 (ok, message, 工具名列表)，失败不抛异常。"""
    ref = snapshot(plugin)
    try:
        tools = list_tools(ref)
    except PluginError as exc:
        return False, str(exc)[:500], []
    except Exception as exc:  # noqa: BLE001 - 管理端点不得 500
        return False, f"插件 {ref.name} 测试失败：{exc}"[:500], []
    names = [tool["name"] for tool in tools]
    return True, f"连接成功，发现 {len(names)} 个工具", names


def warn(message: str) -> None:
    """插件聚合层的告警统一走 stderr，stdout 只留协议内容。"""
    print(f"[bidcraft-mcp] {message}", file=sys.stderr)
