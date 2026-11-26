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

import ipaddress
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
from httpx import HTTPError

try:  # POSIX 才有 resource；Windows 上跳过资源限制。
    import resource
except ImportError:  # pragma: no cover - 非 POSIX 平台
    resource = None  # type: ignore[assignment]

from . import __version__
from .config import settings

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "bidcraft-plugin-client"
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
#: 轻量沙箱：内存（MB）与 CPU 时间（秒）的允许区间及默认值。
MEM_LIMIT_RANGE = (64, 4096)
CPU_SECONDS_RANGE = (1, 120)
DEFAULT_MEM_LIMIT_MB = 512
DEFAULT_CPU_SECONDS = 10
#: 作用域：global 对所有调用者可见；owner 只对指定用户与个人可见。
PLUGIN_SCOPE_GLOBAL = "global"
PLUGIN_SCOPE_OWNER = "owner"
PLUGIN_SCOPES = (PLUGIN_SCOPE_GLOBAL, PLUGIN_SCOPE_OWNER)
#: 出站防护：本机/元数据地址默认拒绝，需超管显式打开 allow_private_net。
_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_METADATA_HOSTS = frozenset(
    {"metadata", "metadata.google.internal", "instance-data", "metadata.goog"}
)
#: 保留测试域不需要解析（永远不会解析成公网地址），避免离线环境 DNS 阻塞。
_SKIP_RESOLVE_SUFFIXES = (".example", ".test", ".invalid")
#: 子进程只用最小环境变量，避免把数据库、模型密钥等传给第三方插件。
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_IS_POSIX = os.name == "posix"

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


def _clamp_int(value: Any, bounds: tuple[int, int], default: int) -> int:
    """把配置值收敛到区间内；非法或越界一律回退默认值，不抛错。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    low, high = bounds
    return number if low <= number <= high else default


def clamp_mem_limit(value: Any) -> int:
    """内存上限（MB）：64–4096，缺省/非法/越界回退 512。"""
    return _clamp_int(value, MEM_LIMIT_RANGE, DEFAULT_MEM_LIMIT_MB)


def clamp_cpu_seconds(value: Any) -> int:
    """CPU 时间上限（秒）：1–120，缺省/非法/越界回退 10。"""
    return _clamp_int(value, CPU_SECONDS_RANGE, DEFAULT_CPU_SECONDS)


def plugin_scope(value: Any, *, default: str = PLUGIN_SCOPE_GLOBAL) -> str:
    """规范化插件作用域；非法值抛 ``ValueError``（API 转 400）。"""
    scope = str(value or default).strip().lower()
    if scope not in PLUGIN_SCOPES:
        raise ValueError(f"scope 只能是 {'/'.join(PLUGIN_SCOPES)}")
    return scope


def plugin_owner_id(value: Any) -> str:
    """个人插件的归属用户 id；空字符串表示无归属。"""
    return str(value or "").strip()[:36]


def _resolve_hosts(host: str) -> list[str]:
    """解析域名返回 IP 字符串列表；解析失败返回空列表。

    解析失败（离线部署/DNS 不可用）时放行，由部署方的网络出站规则兜底；
    应用层无法可靠阻止 DNS 重绑定，这一点在文档中明确说明。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return list({str(item[4][0]) for item in infos})


def plugin_url_allowed(
    url: str, *, allow_private_net: bool = False, resolve: bool = True
) -> bool:
    """HTTP 插件出站地址是否安全（SSRF 基础防护）。

    * 仅允许 ``http/https``，且必须带主机名，禁止 URL 内嵌用户名密码；
    * IP 字面量必须是中国公网以外的全局地址（``ipaddress.is_global``），
      loopback/私网/链路本地/保留地址默认拒绝；
    * ``localhost`` 与云元数据主机名一律拒绝；
    * 普通域名在调用前解析，任一解析结果为非全局地址即拒绝（防 DNS 重绑定）；
      保留测试域（``.example/.test/.invalid``）跳过解析，避免离线环境阻塞。

    ``allow_private_net=True`` 是超管对可信内网插件的显式放行，会跳过上述
    地址判定（仍要求 scheme/主机名/无 userinfo）。
    """
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if allow_private_net:
        return True
    host = parsed.hostname.strip().lower()
    if host in _LOCAL_HOSTS or host in _METADATA_HOSTS or host.endswith(".localhost"):
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return bool(literal.is_global)
    if resolve and not host.endswith(_SKIP_RESOLVE_SUFFIXES):
        for address in _resolve_hosts(host):
            try:
                resolved = ipaddress.ip_address(address)
            except ValueError:
                continue
            if not resolved.is_global:
                return False
    return True


def _apply_limits(mem_mb: int, cpu_s: int) -> Callable[[], None]:
    """返回供 ``Popen(preexec_fn=...)`` 使用的子进程预执行钩子（POSIX）。

    在 fork 之后、exec 之前在子进程里生效：限制地址空间与 CPU 时间，
    并把 umask 收紧到 0o077。任何一项设置失败都不阻止插件启动。
    """
    mem_bytes = mem_mb * 1024 * 1024
    cpu_seconds = cpu_s

    def _limits() -> None:
        if resource is not None:
            for limit, value in (
                (resource.RLIMIT_AS, mem_bytes),
                (resource.RLIMIT_CPU, cpu_seconds),
            ):
                try:
                    resource.setrlimit(limit, (value, value))
                except (ValueError, OSError):
                    pass
        try:
            os.umask(0o077)
        except OSError:
            pass

    return _limits


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
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB
    cpu_seconds: int = DEFAULT_CPU_SECONDS
    scope: str = PLUGIN_SCOPE_GLOBAL
    owner_id: str = ""
    allow_private_net: bool = False


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
        mem_limit_mb=clamp_mem_limit(getattr(plugin, "mem_limit_mb", None)),
        cpu_seconds=clamp_cpu_seconds(getattr(plugin, "cpu_seconds", None)),
        scope=plugin_scope(getattr(plugin, "scope", None)),
        owner_id=plugin_owner_id(getattr(plugin, "owner_id", None)),
        allow_private_net=bool(getattr(plugin, "allow_private_net", False)),
    )


def visible_to(
    plugin: Any, *, user_id: str | None = None, is_super_admin: bool = False
) -> bool:
    """作用域可见性：全局插件对所有调用者可见；个人插件仅归属者与超管可见。

    本地 MCP 不携带用户身份（``user_id=None``），因此默认只暴露全局插件，
    避免个人插件被本机任意调用者触发。
    """
    ref = snapshot(plugin)
    if ref.scope == PLUGIN_SCOPE_GLOBAL or is_super_admin:
        return True
    return bool(user_id) and bool(ref.owner_id) and ref.owner_id == user_id


def allows(plugin: Any, tool_name: str) -> bool:
    """allowlist 为空表示全部允许。"""
    ref = snapshot(plugin)
    return not ref.tool_allowlist or tool_name in ref.tool_allowlist


def plugin_name(value: Any) -> str:
    """插件名规范化：去空白、非空、≤60 字符、不含双下划线（工具名前缀分隔符）。

    与管理端表单同规则；非法时抛 ``ValueError``，由调用方决定转成什么状态码。
    """
    name = str(value or "").strip()
    if not name:
        raise ValueError("插件名称不能为空")
    if len(name) > 60:
        raise ValueError("插件名称不能超过 60 个字符")
    if "__" in name:
        raise ValueError("插件名称不能包含双下划线（用于工具名前缀分隔）")
    return name


def plugin_transport(value: Any, *, default: str = "stdio") -> str:
    transport = str(value or default).strip().lower()
    if transport not in {"stdio", "http"}:
        raise ValueError("transport 只能是 stdio/http")
    return transport


def plugin_validate(
    transport: str, command: str, url: str, *, allow_private_net: bool = False
) -> None:
    if transport == "stdio" and not command:
        raise ValueError("stdio 插件必须填写 command")
    if transport == "http":
        if not url:
            raise ValueError("http 插件必须填写 url")
        if not url.startswith(("http://", "https://")):
            raise ValueError("插件 url 必须以 http:// 或 https:// 开头")
        if not plugin_url_allowed(url, allow_private_net=allow_private_net):
            raise ValueError(
                "插件 url 指向本机/私网/元数据地址，已拒绝；"
                "如为可信内网插件，请显式开启 allow_private_net"
            )


def plugin_items(
    value: Any, *, field: str, limits: int = 64, item_max: int | None = 200
) -> list[str]:
    """接受数组、JSON 文本或逗号分隔字符串，去空去重后返回。

    ``item_max`` 为 None 时不截断单项，交给调用方自行判定长度是否非法。
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = [part.strip() for part in text.split(",")]
        value = decoded
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004 - API 统一转 400
            f"{field} 必须是数组、JSON 文本或逗号分隔字符串"
        )
    items: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in items:
            items.append(text[:item_max] if item_max else text)
    return items[:limits]


def plugin_env(value: Any, *, field: str = "env") -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} 必须是 JSON 对象")  # noqa: TRY004 - API 统一转 400
    env: dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip()[:80]
        if name:
            env[name] = str(item)[:2000]
    return env


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
        "fail_streak": int(getattr(row, "fail_streak", 0) or 0),
        "auto_disabled": bool(getattr(row, "auto_disabled", False)),
        "mem_limit_mb": ref.mem_limit_mb,
        "cpu_seconds": ref.cpu_seconds,
        "scope": ref.scope,
        "owner_id": ref.owner_id,
        "allow_private_net": ref.allow_private_net,
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
        self._workdir: str | None = None
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
        # 每次调用独立的临时工作目录，进程结束后删除，避免插件写脏服务目录。
        self._workdir = tempfile.mkdtemp(prefix="bidcraft-plugin-")
        sandbox: dict[str, Any] = {}
        if _IS_POSIX:
            sandbox["preexec_fn"] = _apply_limits(
                self.plugin.mem_limit_mb, self.plugin.cpu_seconds
            )
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
                cwd=self._workdir,
                **sandbox,
            )
        except (OSError, ValueError) as exc:
            self._cleanup_workdir()
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
        try:
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
        finally:
            self._cleanup_workdir()

    def _cleanup_workdir(self) -> None:
        workdir = self._workdir
        self._workdir = None
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)

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
    # 每次调用重新校验出站地址：防止配置后 DNS 被改指向内网（重绑定）。
    if not plugin_url_allowed(plugin.url, allow_private_net=plugin.allow_private_net):
        raise PluginError(f"插件 {plugin.name} 的 url 指向本机/私网地址，已拒绝出站访问")
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
    """调用插件工具，原样返回 MCP result；同一插件串行执行。"""
    ref = snapshot(plugin)
    tool = (name or "").strip()
    if not tool:
        raise PluginError(f"插件 {ref.name} 缺少工具名")
    args = arguments if isinstance(arguments, dict) else {}
    if not allows(ref, tool):
        raise PluginError(f"插件 {ref.name} 未授权工具 {tool}")
    with _call_lock(ref.id), _session(ref) as session:
        return session.call_tool(tool, args)


_CALL_LOCKS: dict[str, threading.Lock] = {}


def _call_lock(plugin_id: str) -> threading.Lock:
    return _CALL_LOCKS.setdefault(plugin_id or "anonymous", threading.Lock())


def record_outcome(db: Any, row: Any, ok: bool) -> None:
    """累计连续失败；≥3 次自动停用，成功重置计数。"""
    if ok:
        row.fail_streak = 0
        row.auto_disabled = False
    else:
        row.fail_streak = (row.fail_streak or 0) + 1
        if row.fail_streak >= 3:
            row.enabled = False
            row.auto_disabled = True
            row.last_test_message = "连续失败 3 次，已自动停用"
    db.flush()


def reset_guard(db: Any, row: Any) -> None:
    row.fail_streak = 0
    row.auto_disabled = False
    db.flush()


def record_call(
    db: Any,
    plugin_id: str,
    tool: str,
    ok: bool,
    *,
    username: str | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
    call_id: str = "",
) -> None:
    """MCP 聚合调用后落库失败计数并写审计（独立短事务由调用方提供）。

    身份与运行范围均来自真实调用上下文；缺省保持旧行为的 ``system`` 占位，
    兼容本地 MCP 与既有测试。
    """
    from .models import AuditLog, Plugin

    row = db.get(Plugin, plugin_id)
    if row is None:
        return
    record_outcome(db, row, ok)
    actor = str(username or "").strip() or "system"
    db.add(
        AuditLog(
            project_id=project_id,
            username=actor,
            display_name=actor,
            action="plugin.call",
            detail={
                "plugin": row.name,
                "tool": tool,
                "ok": ok,
                "call_id": call_id,
                "scope": row.scope,
                "owner_id": row.owner_id or "",
                "user_id": user_id or "",
                "package_id": package_id or "",
                "run_id": run_id or "",
                "task_id": task_id or "",
            },
        )
    )
    db.flush()


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
