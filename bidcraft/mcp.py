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

from . import __version__, audit, authorization, knowledge, plugins
from .config import settings
from .db import init_db, session_scope
from .export_gate import ExportGateError, enforce_export_gate
from .generation.outline import load_outline
from .ledger import ledger_summary, load_ledger, render_ledger
from .models import Plugin, Project
from .pipeline import check_project, resolve_project
from .security import redact_mapping

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
        "description": (
            "导出投标文件到项目 output 目录，支持 docx、md、xlsx（PDF 请用 API）。"
            "存在未处置的废标/阻断风险时禁止导出 docx/md；确需导出须传 "
            "force=true 并填写不少于 4 个字的 force_reason。xlsx 不受限制。"
        ),
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
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "存在未处置废标/阻断风险时强制导出",
                },
                "force_reason": {
                    "type": "string",
                    "default": "",
                    "description": "强制导出原因（去空白后不少于 4 个字）",
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


def _tool_list_projects(
    db, *, allowed_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    projects = list(
        db.scalars(select(Project).order_by(Project.updated_at.desc()).limit(100))
    )
    if allowed_ids is not None:
        projects = [project for project in projects if project.id in allowed_ids]
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
    # 交付硬门禁与 HTTP/MCP/CLI 共用 export_gate；MCP 无角色上下文，按本地操作者处理。
    try:
        enforce_export_gate(
            db,
            project.id,
            fmt,
            force=bool(args.get("force", False)),
            force_reason=str(args.get("force_reason") or ""),
            source="mcp",
        )
    except ExportGateError as exc:
        raise ToolError(str(exc)) from exc
    ledger = load_ledger(db, project.id)
    nodes = load_outline(db, project.id)
    if fmt != "xlsx" and (not nodes or not any(node.get("body") for node in nodes)):
        raise ToolError("还没有正文，请先生成或编写章节")
    output_dir = settings.project_dir(project.id) / "output"

    if fmt == "docx":
        from .export import templates as export_templates
        from .export.docx import export_docx
        from .images import get_policy, url_resolver

        path = export_docx(
            output_dir / "bid.docx",
            ledger,
            nodes,
            image_resolver=url_resolver(project.id),
            image_policy=get_policy(project),
            template=export_templates.load(project),
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
            "kb_id": passage.kb_id,
            "source": passage.source,
            "document_id": passage.document_id,
            "page": passage.page,
            "heading": passage.heading,
            "chunk_id": passage.chunk_id,
            "chunk_index": passage.chunk_index,
            "score": passage.score,
            "rerank_score": passage.rerank_score,
            "text": (passage.text or "")[:800],
        }
        for passage in passages
    ]


#: 写工具：必须显式 actor + write 授权，且不能通过 readonly=False 绕过 export_gate。
_MUTATING_TOOLS = frozenset({"run_check", "export_document"})


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """审计 detail 里的参数只留短字符串，且先脱敏，绝不写 secrets/长正文。"""

    safe: dict[str, Any] = {}
    for key, value in redact_mapping(args or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = str(value)[:200]
    return safe


def _audit_builtin_call(
    *,
    name: str,
    args: dict[str, Any],
    project_id: str,
    actor: dict[str, Any],
    error: BaseException | None,
    readonly: bool,
    run_id: str,
    task_id: str,
    package_id: str,
    call_id: str = "",
) -> None:
    """每次 agent 内置工具调用都落一条真实 audit_logs 行（成功/失败都记）。"""

    detail = {
        "tool": name,
        "name": name,
        "call_id": call_id,
        "args": _safe_args(args),
        "isError": error is not None,
        "readonly": bool(readonly),
        "run_id": run_id or "",
        "task_id": task_id or "",
        "package_id": package_id or "",
    }
    try:
        with session_scope() as db:
            audit.record_audit(
                db, project_id or None, "agent.tool.call", detail, actor=actor
            )
    except Exception as exc:  # noqa: BLE001 - 审计失败必须让工具调用失败
        raise ToolError(f"工具 {name} 审计写入失败，结果不予采信") from exc


def _authorize_builtin_access(
    db,
    name: str,
    args: dict[str, Any],
    actor: dict[str, Any] | None,
    project_id: str,
) -> tuple[Any, set[str] | None]:
    """返回 (绑定项目或 None, list_projects 的可见 id 集合)。

    有显式 actor（Agent 路径）才施加授权；本地 stdio 的 ``actor=None`` 沿用
    "本机可信操作者" 的既有语义。模型给出的 ref 绝不能越过绑定项目。
    """

    if actor is None:
        return None, None
    if not isinstance(actor, dict) or not actor.get("id"):
        raise ToolError("缺少调用身份，无法授权工具调用")
    action = "write" if name in _MUTATING_TOOLS else "read"
    if name == "list_projects":
        ids = [str(row) for row in db.scalars(select(Project.id))]
        allowed = authorization.authorize_projects(db, ids, user=actor)
        return None, {project.id for project in allowed}
    if name == "search_knowledge":
        # 企业知识检索不携带项目 ref；只要求一个可识别的登录身份。
        return None, None
    if project_id:
        bound, _role = authorization.authorize_project(
            db, project_id, action=action, user=actor
        )
        ref = str(args.get("ref") or "").strip()
        if ref:
            try:
                requested = resolve_project(db, ref)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            if requested.id != bound.id:
                raise ToolError("项目范围不符：ref 不属于本次工作流绑定的项目")
        return bound, None
    bound, _role = authorization.authorize_project(
        db, str(args.get("ref") or ""), action=action, user=actor
    )
    return bound, None


def _call_tool(
    name: str,
    args: dict[str, Any],
    *,
    readonly: bool,
    actor: dict[str, Any] | None = None,
    project_id: str = "",
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
    call_id: str = "",
) -> Any:
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
    error: BaseException | None = None
    payload: Any = None
    bound_id = ""
    try:
        with session_scope() as db:
            try:
                bound, allowed_ids = _authorize_builtin_access(
                    db, name, args, actor, project_id
                )
                bound_id = getattr(bound, "id", "") or ""
                handler_args = (
                    {**args, "ref": bound.id} if bound is not None else dict(args)
                )
                if name == "list_projects" and allowed_ids is not None:
                    payload = _tool_list_projects(db, allowed_ids=allowed_ids)
                else:
                    payload = handlers[name](db, handler_args)
            except Exception as exc:  # noqa: BLE001 - 统一转成 isError 审计与回灌
                error = exc
    except Exception as exc:  # noqa: BLE001 - session_scope 本身失败（连接/提交）
        error = exc

    if actor is not None:
        _audit_builtin_call(
            name=name,
            args=args,
            project_id=project_id or bound_id,
            actor=actor,
            error=error,
            readonly=readonly,
            run_id=run_id,
            task_id=task_id,
            package_id=package_id,
            call_id=call_id,
        )
    if error is not None:
        raise error
    return payload


def _enabled_plugins(
    *, user_id: str | None = None, is_super_admin: bool = False
) -> list[plugins.PluginRef]:
    """读取当前身份可见的启用插件；读失败不得让 MCP 服务不可用。"""
    try:
        with session_scope() as db:
            rows = list(
                db.scalars(
                    select(Plugin).where(Plugin.enabled.is_(True)).order_by(Plugin.name)
                )
            )
            return [
                plugins.snapshot(row)
                for row in rows
                if plugins.visible_to(row, user_id=user_id, is_super_admin=is_super_admin)
            ]
    except Exception:  # noqa: BLE001 - 例如尚未 init_db 的临时环境
        return []


def _plugin_tools(
    *, user_id: str | None = None, is_super_admin: bool = False
) -> list[dict[str, Any]]:
    """聚合可见插件的工具：stdio/http 握手失败只警告并跳过。"""
    merged: list[dict[str, Any]] = []
    seen = set(_TOOL_NAMES)
    for plugin in _enabled_plugins(user_id=user_id, is_super_admin=is_super_admin):
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


def _plugin_target(
    name: str, *, user_id: str | None = None, is_super_admin: bool = False
) -> tuple[plugins.PluginRef, str] | None:
    """把 ``{plugin}__{tool}`` 解析成可见插件与工具名；越权按未知工具处理。"""
    prefix, sep, tool = name.partition("__")
    if not sep or not prefix or not tool:
        return None
    try:
        with session_scope() as db:
            row = db.scalar(select(Plugin).where(Plugin.name == prefix))
            if row is None or not plugins.visible_to(
                row, user_id=user_id, is_super_admin=is_super_admin
            ):
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


def _audit_plugin_rejection(
    full_name: str,
    actor: dict[str, Any] | None,
    project_id: str | None,
    message: str,
    *,
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
    call_id: str = "",
) -> None:
    """只读拒绝也留痕，但不能计入插件连续失败（避免误自动停用）。"""

    if actor is None:
        return
    try:
        with session_scope() as db:
            audit.record_audit(
                db,
                project_id or None,
                "agent.tool.call",
                {
                    "tool": full_name,
                    "name": full_name,
                    "call_id": call_id,
                    "isError": True,
                    "ok": False,
                    "reason": message[:200],
                    "package_id": package_id,
                    "run_id": run_id,
                    "task_id": task_id,
                },
                actor=actor,
            )
    except Exception as exc:  # noqa: BLE001 - 审计失败不得伪造工具结果
        plugins.warn(f"插件 {full_name} 拒绝审计写入失败：{exc}")


def _call_plugin_tool(
    plugin: plugins.PluginRef,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    readonly: bool,
    username: str | None = None,
    project_id: str | None = None,
    actor: dict[str, Any] | None = None,
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
    call_id: str = "",
) -> dict[str, Any]:
    full_name = f"{plugin.name}__{tool_name}"
    if readonly:
        message = f"只读模式：已禁用插件工具 {full_name}"
        _audit_plugin_rejection(
            full_name,
            actor,
            project_id,
            message,
            package_id=package_id,
            run_id=run_id,
            task_id=task_id,
            call_id=call_id,
        )
        return _tool_error_text(message)
    if not plugin.enabled:
        message = f"插件 {plugin.name} 已禁用"
        _audit_plugin_rejection(
            full_name,
            actor,
            project_id,
            message,
            package_id=package_id,
            run_id=run_id,
            task_id=task_id,
            call_id=call_id,
        )
        return _tool_error_text(message)
    if not plugins.allows(plugin, tool_name):
        message = f"插件 {plugin.name} 未授权工具 {tool_name}（不在 allowlist 中）"
        _audit_plugin_rejection(
            full_name,
            actor,
            project_id,
            message,
            package_id=package_id,
            run_id=run_id,
            task_id=task_id,
            call_id=call_id,
        )
        return _tool_error_text(message)
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
        plugins.record_call(
            db,
            plugin.id,
            tool_name,
            ok,
            username=username,
            project_id=project_id,
            user_id=str((actor or {}).get("id") or "") or None,
            package_id=package_id,
            run_id=run_id,
            task_id=task_id,
            call_id=call_id,
        )
    return payload


def _handle_tools_call(
    params: Any,
    *,
    readonly: bool,
    user_id: str | None = None,
    is_super_admin: bool = False,
    username: str | None = None,
    project_id: str | None = None,
    actor: dict[str, Any] | None = None,
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
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
        target = _plugin_target(
            name, user_id=user_id, is_super_admin=is_super_admin
        )
        if target is not None:
            plugin, tool_name = target
            return _call_plugin_tool(
                plugin,
                tool_name,
                arguments,
                readonly=readonly,
                username=username,
                project_id=project_id,
                actor=actor,
                package_id=package_id,
                run_id=run_id,
                task_id=task_id,
            )
    try:
        payload = _call_tool(
            name,
            arguments,
            readonly=readonly,
            actor=actor,
            project_id=project_id or "",
            package_id=package_id,
            run_id=run_id,
            task_id=task_id,
        )
    except ToolError as exc:
        return _tool_error_text(str(exc))
    except (LookupError, ValueError) as exc:
        return _tool_error_text(str(exc) or "项目或数据不存在")
    except authorization.Forbidden as exc:
        return _tool_error_text(f"无权调用工具：{exc}")
    except Exception as exc:  # noqa: BLE001 - 工具异常不得中断服务
        return _tool_error_text(f"工具执行失败：{exc}")
    return _tool_content(payload)


def handle_message(
    msg: dict,
    *,
    readonly: bool = False,
    user_id: str | None = None,
    is_super_admin: bool = False,
    username: str | None = None,
    project_id: str | None = None,
    actor: dict[str, Any] | None = None,
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
) -> dict | None:
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
        return _result(
            request_id,
            {
                "tools": [
                    *TOOL_SPECS,
                    *_plugin_tools(user_id=user_id, is_super_admin=is_super_admin),
                ]
            },
        )
    if method == "tools/call":
        return _result(
            request_id,
            _handle_tools_call(
                msg.get("params"),
                readonly=readonly,
                user_id=user_id,
                is_super_admin=is_super_admin,
                username=username,
                project_id=project_id,
                actor=actor,
                package_id=package_id,
                run_id=run_id,
                task_id=task_id,
            ),
        )
    return _error(request_id, -32601, f"未知方法: {method}")


def handle_line(
    line: str,
    *,
    readonly: bool = False,
    user_id: str | None = None,
    is_super_admin: bool = False,
    username: str | None = None,
    project_id: str | None = None,
    actor: dict[str, Any] | None = None,
    package_id: str = "",
    run_id: str = "",
    task_id: str = "",
) -> dict | None:
    """解析并处理一行输入；JSON 解析失败返回 -32700 错误响应。"""
    text = (line or "").strip()
    if not text:
        return None
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return _error(None, -32700, "JSON 解析失败")
    return handle_message(
        msg,
        readonly=readonly,
        user_id=user_id,
        is_super_admin=is_super_admin,
        username=username,
        project_id=project_id,
        actor=actor,
        package_id=package_id,
        run_id=run_id,
        task_id=task_id,
    )


def serve_stdio(
    readonly: bool = False,
    *,
    plugin_owner: str | None = None,
    plugin_all: bool = False,
) -> None:
    """初始化数据库，然后逐行处理 stdin；EOF 时退出。

    本地 MCP 默认只暴露全局插件；``--plugin-owner`` 指定归属用户后可看到其
    个人插件，``--plugin-all`` 按超管视角暴露全部（仅本机可信调用者使用）。
    """
    init_db()
    identity = {
        "user_id": plugin_owner,
        "is_super_admin": plugin_all,
        "username": plugin_owner or "mcp-local",
    }
    for line in sys.stdin:
        try:
            response = handle_line(line, readonly=readonly, **identity)
        except Exception as exc:  # noqa: BLE001 - 单行失败不退出循环
            response = _error(None, -32603, f"内部错误：{exc}")
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
