"""Agent 工具调用循环（薄实现）。

只负责：调用 ``ModelClient.complete_with_tools`` → 校验模型给出的工具调用 →
通过调用方提供的 ``executor`` 执行 → 把 ``role=tool`` 观测写回消息 → 循环，
直到模型不再请求工具。业务权限、allowlist、审计与沙箱全部由 ``executor``
（生产环境为 :func:`bidcraft.orchestration.registry.call_tool_with_trace`）
负责，本模块**不**直接访问数据库或业务 Service。

普通生成路径仍走 :meth:`ModelClient.complete`；本模块只有被显式调用时才生效。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: 工具轨迹的对外契约：不多不少正好这些键。
TRACE_KEYS = frozenset(
    {"name", "args", "result", "verification", "audit_id", "ts", "is_error", "ms"}
)
VERIFICATIONS = frozenset({"verified", "unverified", "failed"})

#: 业务失败信号。工具调用成功返回并不等于业务成功；只要结果里出现这些
#: 字段/状态，就必须记为 failed，不能让模型把失败包装成成功。
_ERROR_KEYS = frozenset(
    {"error", "errors", "isError", "is_error", "exception", "traceback"}
)
_FALSE_SUCCESS_KEYS = frozenset({"ok", "success", "succeeded", "passed"})
_FAILED_STATUSES = frozenset(
    {
        "failed",
        "failure",
        "error",
        "errored",
        "blocked",
        "denied",
        "rejected",
        "cancelled",
        "canceled",
        "forbidden",
    }
)
#: 纯文本结果不能证明业务成功；但显式失败词必须被识别，避免"失败"被记为可验证。
_TEXT_FAILURE_MARKERS = (
    "失败",
    "错误",
    "异常",
    "拒绝",
    "无权",
    "未授权",
    "不存在",
    "timeout",
    "timed out",
    "forbidden",
    "unauthorized",
    "not found",
    "failed",
    "error",
)
#: 只有结构化字段才算可验证证据；message/text/warning 等自由文本不算。
_TEXT_ONLY_KEYS = frozenset({"text", "message", "messages", "warning", "warnings", "content"})


class ToolLoopBudgetError(RuntimeError):
    """工具循环超出轮次上限或文本上限。"""


class ToolVerificationError(RuntimeError):
    """必需工具缺失或执行结果无法验证；不得伪装成功。"""


class ToolExecutionError(RuntimeError):
    """执行器失败，携带真实审计 id（如有）供轨迹引用。"""

    def __init__(self, message: str, *, audit_id: str = "") -> None:
        super().__init__(message)
        self.audit_id = audit_id


@dataclass
class ToolInvocation:
    """``executor`` 可选的结构化返回，携带真实审计行 id。"""

    result: Any
    audit_id: str = ""
    is_error: bool = False


@dataclass
class ToolLoopResult:
    content: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0


def _int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for spec in tools or []:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if not name and isinstance(spec.get("function"), dict):
            name = spec["function"].get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def _payload_text(payload: Any, *, max_chars: int) -> str:
    """把任意工具返回压成给模型看的文本，并留出截断标记。"""

    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list) and content:
            parts = [
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            ]
            text = "\n".join(part for part in parts if part)
        else:
            text = json.dumps(payload, ensure_ascii=False, default=str)
    elif isinstance(payload, list):
        text = json.dumps(payload, ensure_ascii=False, default=str)
    else:
        text = "" if payload is None else str(payload)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…（结果已截断）"


def _text_has_failure(text: str) -> bool:
    """识别结果文本中的显式失败词；英文词按边界匹配避免 no errors 误报。"""

    lowered = text.lower()
    for marker in _TEXT_FAILURE_MARKERS:
        if any("\u4e00" <= char <= "\u9fff" for char in marker):
            if marker in lowered:
                return True
        elif re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered):
            return True
    return False


def _explicit_failure(payload: Any) -> bool:
    """只检查明确的业务失败信号，不把正常内容里的普通词当失败。"""

    if isinstance(payload, dict):
        if any(payload.get(key) for key in _ERROR_KEYS):
            return True
        if any(key in payload and payload[key] is False for key in _FALSE_SUCCESS_KEYS):
            return True
        state = str(payload.get("status") or payload.get("state") or "").strip().lower()
        if state in _FAILED_STATUSES:
            return True
        content = payload.get("content")
        if isinstance(content, list):
            return any(
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and _text_has_failure(item["text"])
                for item in content
            )
        return False
    if isinstance(payload, (list, tuple)):
        return any(
            _explicit_failure(item) if isinstance(item, (dict, list, tuple)) else (
                _text_has_failure(item) if isinstance(item, str) else False
            )
            for item in payload
        )
    return isinstance(payload, str) and _text_has_failure(payload)


def _meaningful_evidence(payload: Any) -> bool:
    """只有结构化结果才算可验证；纯文本/提示信息不能证明业务成功。"""

    if isinstance(payload, dict):
        return any(
            key not in _TEXT_ONLY_KEYS
            and value not in (None, "", [], {})
            for key, value in payload.items()
        )
    if isinstance(payload, (list, tuple)):
        return bool(payload) and any(
            isinstance(item, dict) and _meaningful_evidence(item) for item in payload
        )
    return False


def _verification(payload: Any, *, is_error: bool) -> str:
    """仅依据真实工具结果判定。

    ``verified`` 表示工具返回了可核验的结构化证据，**不代表**业务结论通过
    （例如 run_check 返回风险清单仍是 verified）；显式业务失败一律 failed；
    非空但只有自由文本的结果只能算 unverified，不能满足 required_tools。
    """

    if is_error:
        return "failed"
    if _explicit_failure(payload):
        return "failed"
    return "verified" if _meaningful_evidence(payload) else "unverified"


def _call_fields(call: Any) -> tuple[str, str, str, dict[str, Any]]:
    """归一化 OpenAI / Ollama 的 tool_call；raw 参数非法时原样返回供校验。"""

    if isinstance(call, dict):
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or call.get("tool_call_id") or "")
        raw = call.get("raw_arguments")
        arguments = call.get("arguments")
    else:
        name = str(getattr(call, "name", "") or "")
        call_id = str(getattr(call, "id", "") or "")
        raw = getattr(call, "raw_arguments", None)
        arguments = getattr(call, "arguments", None)
    if raw is None and isinstance(arguments, str):
        raw = arguments
    raw_text = "" if raw is None else str(raw)
    if isinstance(arguments, dict):
        args = dict(arguments)
    else:
        args = {}
    return name.strip(), call_id, raw_text, args


def _parse_args(raw_text: str, fallback: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """返回 (参数, 错误)；JSON 必须是对象，空串按空对象处理。"""

    if not raw_text.strip():
        if isinstance(fallback, dict):
            return dict(fallback), ""
        return {}, ""
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {}, f"工具参数不是合法 JSON：{exc}"
    if not isinstance(parsed, dict):
        return {}, "工具参数必须是 JSON 对象"
    return parsed, ""


def _check_required(traces: list[dict[str, Any]], required: tuple[str, ...] | list[str]) -> None:
    """必需工具必须执行成功；同名工具只要出现失败轨迹也不得放行。"""

    failed: list[str] = []
    unverified: list[str] = []
    for name in required or ():
        calls = [trace for trace in traces if trace["name"] == name]
        if not calls or not any(trace["verification"] == "verified" for trace in calls):
            unverified.append(name)
        elif any(trace["verification"] == "failed" for trace in calls):
            failed.append(name)
    if failed:
        raise ToolVerificationError(
            f"必需工具执行失败：{', '.join(dict.fromkeys(failed))}"
        )
    if unverified:
        raise ToolVerificationError(
            f"必需工具未成功执行或结果不可验证：{', '.join(dict.fromkeys(unverified))}"
        )


def _assistant_message(content: str, calls: list[Any]) -> dict[str, Any]:
    encoded: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        name, call_id, raw_text, args = _call_fields(call)
        encoded.append(
            {
                "id": call_id or f"call_{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_text or json.dumps(args, ensure_ascii=False),
                },
            }
        )
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if encoded:
        message["tool_calls"] = encoded
    return message


def _trace(
    *,
    name: str,
    args: dict[str, Any],
    result: str,
    verification: str,
    audit_id: str,
    is_error: bool,
    ms: int,
) -> dict[str, Any]:
    trace = {
        "name": name,
        "args": args,
        "result": result,
        "verification": verification,
        "audit_id": audit_id,
        "ts": datetime.now(UTC).isoformat(),
        "is_error": bool(is_error),
        "ms": int(ms),
    }
    assert set(trace) == TRACE_KEYS  # 契约由本函数唯一产出
    return trace


def run_tool_loop(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    executor: Callable[[str, dict[str, Any]], Any],
    max_rounds: int = 6,
    max_chars: int = 8000,
    max_result_chars: int = 2000,
    required_tools: tuple[str, ...] | list[str] = (),
    on_trace: Callable[[dict[str, Any]], None] | None = None,
    tool_choice: str | dict[str, Any] = "auto",
    max_tokens: int = 4000,
    temperature: float | None = None,
) -> ToolLoopResult:
    """执行受限的工具循环；``finish_reason`` 不参与终止判定。

    OpenAI 返回 ``finish_reason='tool_calls'``，Ollama 返回 ``done_reason='stop'``
    却仍带 ``tool_calls``；两者都以 ``tool_calls`` 是否为空作为继续条件。

    非法工具名 / 非法 JSON 参数在调用 ``executor`` 之前被拒绝，并作为错误观测
    回灌给模型。轮次或累计文本超限抛 :class:`ToolLoopBudgetError`；必需工具
    未成功验证抛 :class:`ToolVerificationError`。
    """

    max_rounds = _int(max_rounds, 6)
    max_chars = _int(max_chars, 8000)
    max_result_chars = _int(max_result_chars, 2000)
    available = _tool_names(tools)
    history: list[dict[str, Any]] = [dict(message) for message in (messages or []) if isinstance(message, dict)]
    traces: list[dict[str, Any]] = []
    total_chars = 0
    content = ""

    for _round in range(max_rounds):
        response = client.complete_with_tools(
            history,
            tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = str(getattr(response, "content", "") or "")
        calls = list(getattr(response, "tool_calls", None) or [])
        if not calls:
            _check_required(traces, required_tools)
            if content:
                history.append({"role": "assistant", "content": content})
            return ToolLoopResult(
                content=content,
                messages=history,
                tool_calls=traces,
                rounds=_round + 1,
            )

        history.append(_assistant_message(content, calls))
        for index, call in enumerate(calls):
            name, call_id, raw_text, parsed_args = _call_fields(call)
            call_id = call_id or f"call_{_round}_{index}"
            started = time.monotonic()
            args, parse_error = _parse_args(raw_text, parsed_args)
            audit_id = ""
            is_error = False
            result_text = ""

            if not name or name not in available:
                is_error = True
                result_text = f"工具调用被拒绝：未知工具 {name or '(空)'}"
                verification = "failed"
            elif parse_error:
                is_error = True
                result_text = parse_error
                verification = "failed"
            else:
                try:
                    returned = executor(name, args)
                except Exception as exc:  # noqa: BLE001 - 执行失败必须回灌给模型
                    is_error = True
                    audit_id = str(getattr(exc, "audit_id", "") or "")
                    result_text = f"工具执行失败：{exc}"
                    verification = "failed"
                else:
                    invocation = returned if isinstance(returned, ToolInvocation) else None
                    payload = invocation.result if invocation is not None else returned
                    audit_id = str(
                        (invocation.audit_id if invocation is not None else "")
                        or (payload.get("audit_id") if isinstance(payload, dict) else "")
                        or ""
                    )
                    is_error = bool(
                        (invocation.is_error if invocation is not None else False)
                        or (payload.get("isError") if isinstance(payload, dict) else False)
                        or (payload.get("is_error") if isinstance(payload, dict) else False)
                    )
                    result_text = _payload_text(payload, max_chars=max_result_chars)
                    verification = _verification(payload, is_error=is_error)

            total_chars += len(result_text)
            if total_chars > max_chars:
                raise ToolLoopBudgetError(
                    f"工具观测文本超过上限 {max_chars} 字符（已产生 {total_chars}）"
                )
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result_text,
                }
            )
            trace = _trace(
                name=name,
                args=args,
                result=result_text,
                verification=verification,
                audit_id=audit_id,
                is_error=is_error,
                ms=int((time.monotonic() - started) * 1000),
            )
            traces.append(trace)
            if on_trace is not None:
                on_trace(trace)

    raise ToolLoopBudgetError(f"工具调用超过 {max_rounds} 轮上限，已停止执行")


__all__ = [
    "TRACE_KEYS",
    "VERIFICATIONS",
    "ToolExecutionError",
    "ToolInvocation",
    "ToolLoopBudgetError",
    "ToolLoopResult",
    "ToolVerificationError",
    "run_tool_loop",
]
