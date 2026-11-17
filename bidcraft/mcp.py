"""MCP stdio 服务：把 BidCraft 内核能力暴露给 Claude/Cursor 等 MCP 客户端。

协议为 newline-delimited JSON-RPC 2.0（MCP 2024-11-05）：每行一个请求，
响应写一行 JSON。stdout 只输出协议内容，所有日志/提示走 stderr。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select

from . import __version__, knowledge, plugins
from .config import settings
from .db import init_db, session_scope
from .generation.outline import load_outline
from .ledger import ledger_summary, load_ledger, render_ledger
from .models import Plugin, Project
from .pipeline import check_project, resolve_project

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "bidcraft-mcp"

_REF_SCHEMA = {
    "type": "object",
    "properties": {
        "ref": {
            "type": "string",
            "description": "项目 ID、ID 前缀或项目名称",
        }
    },
    "required": ["ref"],
    "additionalProperties": False,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_projects",
        "description": "列出全部投标项目（按更新时间倒序，最多 100 个）。",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_project",
        "description": "按 ID/ID 前缀/名称读取单个项目的详情与事实台账摘要。",
        "inputSchema": _REF_SCHEMA,
    },
    {
        "name": "get_ledger",
        "description": "读取项目事实台账摘要与渲染文本（唯一口径，供撰写引用）。",
        "inputSchema": _REF_SCHEMA,
    },
    {
        "name": "run_check",
        "description": "对项目执行规则核对，返回未通过的问题列表与统计摘要。",
        "inputSchema": _REF_SCHEMA,
    },
    {
        "name": "export_document",
        "description": "导出投标文件到项目 output 目录，支持 docx、md、xlsx（PDF 请用 API）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "项目 ID、ID 前缀或项目名称",
                },
                "format": {
                    "type": "string",
                    "enum": ["docx", "md", "xlsx"],
                    "default": "docx",
                    "description": "导出格式，默认 docx",
                },
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_knowledge",
        "description": "在企业知识库中检索与查询相关的段落，返回来源与相关性得分。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 4,
                    "description": "返回段落数，1-10，默认 4",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]

_TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}
_NOTIFICATION_METHODS = {"notifications/initialized", "notifications/cancelled"}


class ToolError(RuntimeError):
    """工具层可预期的失败，转成 isError 文本而不是协议错误。"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_content(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}
        ],
        "isError": is_error,
    }


def _tool_error_text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _require_ref(args: dict[str, Any]) -> str:
    ref = args.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ToolError("缺少参数 ref（项目 ID、ID 前缀或名称）")
    return ref.strip()


def _project_payload(project: Project) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "package": project.package,
        "status": project.status,
        "updated_at": _iso(project.updated_at),
    }
    meta = project.meta or {}
    if isinstance(meta, dict) and meta.get("bid_section"):
        item["bid_section"] = meta["bid_section"]
    return item


def _tool_list_projects(db) -> list[dict[str, Any]]:
    projects = list(
        db.scalars(select(Project).order_by(Project.updated_at.desc()).limit(100))
    )
    return [_project_payload(project) for project in projects]


def _tool_get_project(db, args: dict[str, Any]) -> dict[str, Any]:
    project = resolve_project(db, _require_ref(args))
    ledger = load_ledger(db, project.id)
    return {
        "id": project.id,
        "name": project.name,
        "package": project.package,
        "status": project.status,
        "owner_id": project.owner_id,
        "meta": project.meta or {},
        "ledger_summary": ledger_summary(ledger),
    }


def _tool_get_ledger(db, args: dict[str, Any]) -> dict[str, Any]:
    project = resolve_project(db, _require_ref(args))
    ledger = load_ledger(db, project.id)
    return {
        "summary": ledger_summary(ledger),
        "text": render_ledger(ledger, max_chars=16000),
    }


def _issue_payload(result: Any) -> dict[str, Any]:
    """按 models.Issue 字段序列化核对结果；对象上没有的字段跳过。"""
    payload: dict[str, Any] = {}
    for field in ("id", "level", "category", "message", "location", "recommendation"):
        value = getattr(result, field, None)
        if field == "message" and not value:
            value = getattr(result, "finding", None)
        if value:
            payload[field] = value
    return payload


def _tool_run_check(db, args: dict[str, Any], readonly: bool) -> dict[str, Any]:
    if readonly:
        raise ToolError("只读模式：已禁用核对")
    project = resolve_project(db, _require_ref(args))
    issues, summary = check_project(db, project.id)
    return {
        "issues": [_issue_payload(issue) for issue in issues],
        "summary": summary,
    }


def _resolve_format(raw: Any) -> str:
    fmt = str(raw or "docx").strip().lower()
    if fmt == "markdown":
        fmt = "md"
    if fmt == "pdf":
        raise ToolError("MCP 暂不支持 PDF 导出，请使用 BidCraft API 或 Web 客户端导出 PDF")
    if fmt not in {"docx", "md", "xlsx"}:
        raise ToolError("format 只能是 docx、md、xlsx")
    return fmt


def _tool_export_document(db, args: dict[str, Any], readonly: bool) -> dict[str, Any]:
    if readonly:
        raise ToolError("只读模式：已禁用导出")
    fmt = _resolve_format(args.get("format"))
    project = resolve_project(db, _require_ref(args))
    ledger = load_ledger(db, project.id)
    nodes = load_outline(db, project.id)
    if fmt != "xlsx" and (not nodes or not any(node.get("body") for node in nodes)):
        raise ToolError("还没有正文，请先生成或编写章节")
    output_dir = settings.project_dir(project.id) / "output"

    if fmt == "docx":
        from .export.docx import export_docx
        from .images import url_resolver

        path = export_docx(
            output_dir / "bid.docx",
            ledger,
            nodes,
            image_resolver=url_resolver(project.id),
        )
    elif fmt == "md":
        from .export.docx import export_markdown

        path = export_markdown(output_dir / "bid.md", ledger, nodes)
    else:
        from .export.xlsx import export_xlsx

        path = export_xlsx(output_dir / "bid.xlsx", ledger)
    return {"path": str(path), "format": fmt}


def _tool_search_knowledge(db, args: dict[str, Any]) -> list[dict[str, Any]]:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("缺少参数 query（检索关键词或问题）")
    raw_limit = args.get("limit", 4)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise ToolError("limit 必须是 1-10 的整数") from None
    limit = max(1, min(10, limit))
    passages = knowledge.search(db, query.strip(), limit=limit)
    return [
        {
            "kb": passage.kb,
            "source": passage.source,
            "score": passage.score,
            "text": (passage.text or "")[:800],
        }
        for passage in passages
    ]


def _call_tool(name: str, args: dict[str, Any], *, readonly: bool) -> Any:
    if name not in _TOOL_NAMES:
        raise ToolError(f"未知工具: {name}")
    handlers: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
        "list_projects": lambda db, a: _tool_list_projects(db),
        "get_project": _tool_get_project,
        "get_ledger": _tool_get_ledger,
        "run_check": lambda db, a: _tool_run_check(db, a, readonly),
        "export_document": lambda db, a: _tool_export_document(db, a, readonly),
        "search_knowledge": _tool_search_knowledge,
    }
    with session_scope() as db:
        return handlers[name](db, args)


def _enabled_plugins() -> list[plugins.PluginRef]:
    """读取启用的插件；插件表/配置读失败不得让 MCP 服务不可用。"""
    try:
        with session_scope() as db:
            rows = list(
                db.scalars(
                    select(Plugin).where(Plugin.enabled.is_(True)).order_by(Plugin.name)
                )
            )
            return [plugins.snapshot(row) for row in rows]
    except Exception:  # noqa: BLE001 - 例如尚未 init_db 的临时环境
        return []


def _plugin_tools() -> list[dict[str, Any]]:
    """聚合启用插件的工具：stdio/http 握手失败只警告并跳过。"""
    merged: list[dict[str, Any]] = []
    seen = set(_TOOL_NAMES)
    for plugin in _enabled_plugins():
        try:
            tools = plugins.list_tools(plugin)
        except plugins.PluginError as exc:
            plugins.warn(f"插件 {plugin.name} 不可用，已跳过：{exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - 单个插件不得阻断 tools/list
            plugins.warn(f"插件 {plugin.name} 不可用，已跳过：{exc}")
            continue
        for tool in tools:
            if not plugins.allows(plugin, tool["name"]):
                continue
            full_name = f"{plugin.name}__{tool['name']}"
            if full_name in seen:
                continue
            seen.add(full_name)
            merged.append(
                {
                    "name": full_name,
                    "description": f"[{plugin.name}] {tool['description'] or tool['name']}",
                    "inputSchema": tool["inputSchema"],
                }
            )
    return merged


def _plugin_target(name: str) -> tuple[plugins.PluginRef, str] | None:
    """把 ``{plugin}__{tool}`` 解析成插件与工具名；不是插件工具则返回 None。"""
    prefix, sep, tool = name.partition("__")
    if not sep or not prefix or not tool:
        return None
    try:
        with session_scope() as db:
            row = db.scalar(select(Plugin).where(Plugin.name == prefix))
            if row is None:
                return None
            return plugins.snapshot(row), tool
    except Exception:  # noqa: BLE001 - 读不到配置时按未知工具处理
        return None


def _plugin_result(result: dict[str, Any]) -> dict[str, Any]:
    """原样透传插件的 MCP result，只补齐 content/isError 两个必需字段。"""
    payload = dict(result) if isinstance(result, dict) else {"value": result}
    if not isinstance(payload.get("content"), list) or not payload["content"]:
        text = json.dumps(
            payload.get("structuredContent") or payload,
            ensure_ascii=False,
            default=str,
        )
        payload["content"] = [{"type": "text", "text": text}]
    payload["isError"] = bool(payload.get("isError"))
    return payload


def _call_plugin_tool(
    plugin: plugins.PluginRef,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    readonly: bool,
) -> dict[str, Any]:
    full_name = f"{plugin.name}__{tool_name}"
    if readonly:
        return _tool_error_text(f"只读模式：已禁用插件工具 {full_name}")
    if not plugin.enabled:
        return _tool_error_text(f"插件 {plugin.name} 已禁用")
    if not plugins.allows(plugin, tool_name):
        return _tool_error_text(f"插件 {plugin.name} 未授权工具 {tool_name}（不在 allowlist 中）")
    ok = True
    try:
        result = plugins.call_tool(plugin, tool_name, arguments)
    except plugins.PluginError as exc:
        ok = False
        payload = _tool_error_text(str(exc))
    except Exception as exc:  # noqa: BLE001 - 插件异常不得中断服务
        ok = False
        payload = _tool_error_text(f"插件 {plugin.name} 调用失败：{exc}")
    else:
        payload = _plugin_result(result)
        ok = not payload.get("isError")
    from .db import session_scope

    with session_scope() as db:
        plugins.record_call(db, plugin.id, tool_name, ok)
    return payload


def _handle_tools_call(params: Any, *, readonly: bool) -> dict[str, Any]:
    if not isinstance(params, dict):
        return _tool_error_text("tools/call 参数必须是对象")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return _tool_error_text("tools/call 缺少工具名 name")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _tool_error_text("tools/call 的 arguments 必须是对象")
    if name not in _TOOL_NAMES:
        target = _plugin_target(name)
        if target is not None:
            plugin, tool_name = target
            return _call_plugin_tool(plugin, tool_name, arguments, readonly=readonly)
    try:
        payload = _call_tool(name, arguments, readonly=readonly)
    except ToolError as exc:
        return _tool_error_text(str(exc))
    except (LookupError, ValueError) as exc:
        return _tool_error_text(str(exc) or "项目或数据不存在")
    except Exception as exc:  # noqa: BLE001 - 工具异常不得中断服务
        return _tool_error_text(f"工具执行失败：{exc}")
    return _tool_content(payload)


def handle_message(msg: dict, *, readonly: bool = False) -> dict | None:
    """处理单条 JSON-RPC 消息；notification 返回 None。"""
    if not isinstance(msg, dict):
        return _error(None, -32600, "无效请求：消息必须是 JSON 对象")

    method = msg.get("method")
    if not isinstance(method, str):
        if "id" not in msg:
            return None
        return _error(msg.get("id"), -32600, "无效请求：缺少 method")
    if method in _NOTIFICATION_METHODS:
        return None
    if "id" not in msg:
        return None

    request_id = msg.get("id")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [*TOOL_SPECS, *_plugin_tools()]})
    if method == "tools/call":
        return _result(
            request_id,
            _handle_tools_call(msg.get("params"), readonly=readonly),
        )
    return _error(request_id, -32601, f"未知方法: {method}")


def handle_line(line: str, *, readonly: bool = False) -> dict | None:
    """解析并处理一行输入；JSON 解析失败返回 -32700 错误响应。"""
    text = (line or "").strip()
    if not text:
        return None
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return _error(None, -32700, "JSON 解析失败")
    return handle_message(msg, readonly=readonly)


def serve_stdio(readonly: bool = False) -> None:
    """初始化数据库，然后逐行处理 stdin；EOF 时退出。"""
    init_db()
    for line in sys.stdin:
        try:
            response = handle_line(line, readonly=readonly)
        except Exception as exc:  # noqa: BLE001 - 单行失败不退出循环
            response = _error(None, -32603, f"内部错误：{exc}")
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
