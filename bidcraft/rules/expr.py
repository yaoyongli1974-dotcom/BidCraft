from __future__ import annotations

import ast
import re
from collections.abc import Callable
from typing import Any

MAX_NODES = 400
MAX_TEXT = 2_000_000


class ExpressionError(ValueError):
    pass


def _norm(text: Any) -> str:
    value = str(text or "")
    value = value.replace("\u3000", " ")
    value = re.sub(r"\s+", "", value)
    return value.lower()


def present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    text = str(value).strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in {"none", "null", "nan", "未识别", "待填", "未提供"}:
        return False
    return "{{待填" not in text


def missing(value: Any) -> bool:
    return not present(value)


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return None
    factor = 1.0
    if "亿" in text:
        factor = 1e8
    elif "万" in text:
        factor = 1e4
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0)) * factor
    except ValueError:
        return None


def duration_days(value: Any) -> float | None:
    text = str(value or "")
    num = number(text)
    if num is None:
        return None
    if "年" in text:
        return num * 365
    if "月" in text:
        return num * 30
    if "周" in text or "星期" in text:
        return num * 7
    if "小时" in text:
        return num / 24
    return num


def contains(haystack: Any, needle: Any) -> bool:
    a, b = _norm(haystack), _norm(needle)
    if not b:
        return False
    return b in a


def contains_any(text: Any, items: Any) -> bool:
    return any(contains(text, item) for item in (items or []))


def contains_all(text: Any, items: Any) -> bool:
    items = list(items or [])
    return bool(items) and all(contains(text, item) for item in items)


def contains_ratio(text: Any, items: Any) -> float:
    items = list(items or [])
    if not items:
        return 1.0
    return sum(1 for item in items if contains(text, item)) / len(items)


_LENGTH_UNITS = ("米", "m", "km", "公里", "kg", "千克", "吨", "t")


def materials_total(materials: Any, include: str = "", exclude: str = "") -> float:
    """Sum quantities of BOM rows, excluding length/weight units by default.

    ``include``/``exclude`` are regex fragments matched against the row name.
    """
    total = 0.0
    for row in materials or []:
        name = str((row or {}).get("name") or "")
        unit = str((row or {}).get("unit") or "").strip().lower()
        qty = (row or {}).get("qty")
        if qty is None:
            continue
        if include and not re.search(include, name):
            continue
        if exclude and re.search(exclude, name):
            continue
        if unit and any(u == unit or unit.startswith(u) for u in _LENGTH_UNITS):
            continue
        total += float(qty)
    return round(total, 3)


def storage_tb(cameras: Any, days: Any, bitrate_mbps: Any) -> float:
    c = number(cameras) or 0
    d = number(days) or 0
    b = number(bitrate_mbps) or 0
    # Mbps -> MB/s = /8 ; one day = 86400 s ; TB = 1e6 MB
    return round(c * d * 86400 * b / 8 / 1_000_000, 2)


def _param_tokens(parameter: dict[str, Any]) -> list[str]:
    name = str(parameter.get("name") or "").strip()
    value = str(parameter.get("value") or "").strip()
    tokens: list[str] = []
    if name:
        tokens.append(name)
        # Chinese parameter names are often long; a 6-char prefix still identifies them.
        compact = re.sub(r"[（(].*?[）)]", "", name).strip()
        if len(compact) > 8:
            tokens.append(compact[:8])
    if value and len(value) <= 20:
        tokens.append(value)
    return [t for t in dict.fromkeys(tokens) if t]


def param_response_missing(parameters: Any, text: Any) -> list[str]:
    missing_items: list[str] = []
    body = str(text or "")
    for parameter in parameters or []:
        tokens = _param_tokens(parameter or {})
        if not tokens:
            continue
        if not any(contains(body, token) for token in tokens):
            missing_items.append(str((parameter or {}).get("name") or ""))
    return missing_items


def param_response_ratio(parameters: Any, text: Any) -> float:
    items = [p for p in (parameters or []) if str((p or {}).get("name") or "").strip()]
    if not items:
        return 1.0
    return 1.0 - len(param_response_missing(items, text)) / len(items)


def count_matches(pattern: Any, text: Any) -> int:
    try:
        return len(re.findall(str(pattern), str(text or "")))
    except re.error:
        return 0


def has_section(sections: Any, keyword: Any) -> bool:
    return any(contains((s or {}).get("heading"), keyword) for s in sections or [])


def section_text(sections: Any, keyword: Any) -> str:
    parts = [
        str((s or {}).get("body") or "")
        for s in sections or []
        if contains((s or {}).get("heading"), keyword)
    ]
    return "\n".join(parts)


def ratio(done: Any, total: Any) -> float:
    d = number(done) or 0
    t = number(total) or 0
    return 1.0 if t == 0 else d / t


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "present": present,
    "missing": missing,
    "number": number,
    "duration_days": duration_days,
    "contains": contains,
    "contains_any": contains_any,
    "contains_all": contains_all,
    "contains_ratio": contains_ratio,
    "materials_total": materials_total,
    "storage_tb": storage_tb,
    "param_response_ratio": param_response_ratio,
    "param_response_missing": param_response_missing,
    "count_matches": count_matches,
    "has_section": has_section,
    "section_text": section_text,
    "ratio": ratio,
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "round": round,
    "abs": abs,
    "str": str,
    "bool": bool,
}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Subscript,
    ast.Slice,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.IfExp,
)


def _validate(tree: ast.AST) -> None:
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ExpressionError("表达式过于复杂")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(f"不允许的表达式节点: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ExpressionError("只允许调用内置核对函数")
            if node.keywords:
                raise ExpressionError("核对函数不支持关键字参数")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ExpressionError("非法名称")


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    if not expression or len(expression) > 2000:
        raise ExpressionError("表达式为空或过长")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"表达式语法错误: {exc.msg}") from exc
    _validate(tree)
    env = dict(context)
    env.update(FUNCTIONS)
    try:
        return eval(compile(tree, "<check>", "eval"), {"__builtins__": {}}, env)
    except ExpressionError:
        raise
    except Exception as exc:
        raise ExpressionError(f"表达式执行失败: {exc}") from exc
