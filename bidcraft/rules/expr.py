from __future__ import annotations

import ast
import re
from collections.abc import Callable
from typing import Any

from ..constraint_check import (
    _DIRECTION_PATTERNS,
    _VALUE_RE,
    UNIT_COMPATIBLE,
    _cmp_with_tolerance,
    _cn_number,
    _to_base,
    unit_relation,
)

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


def _numeric_pass(value: float, threshold: float, comparator: str, tolerance: float) -> bool:
    """带可选容差的数值方向比较（单位已归一到同一量纲基准）。"""

    if tolerance <= 0:
        return _cmp_with_tolerance(value, threshold, comparator)
    slack = tolerance * max(abs(threshold), 1.0)
    if comparator == "<=":
        return value <= threshold + slack
    return value >= threshold - slack


def _required_numeric(parameter: dict[str, Any]) -> tuple[float, str, str] | None:
    """从参数项里取 (阈值, 单位, 比较方向)；取不到就退回字符串口径。

    只有出现明确比较词，或参数项已经登记了可识别单位并且 value 带数值时才做
    数值核对——否则「Cat6A」「GB 50311」这类文本会被误当成数值阈值。
    """

    name = str(parameter.get("name") or "")
    value_text = str(parameter.get("value") or "")
    requirement = str(parameter.get("requirement") or "")
    unit = str(parameter.get("unit") or "").strip()
    text = " ".join(part for part in (value_text, requirement, name) if part)

    # 显式方向词优先：不低于/不大于/至少/最多…（与 constraint_check 同一张表）。
    for pattern, comparator in _DIRECTION_PATTERNS:
        match = re.search(pattern, text)
        if match is None:
            continue
        number = _cn_number(match.group(1))
        if number is None:
            continue
        found_unit = (match.group(2) or unit).strip()
        if not found_unit:
            continue
        return float(number), found_unit, comparator

    if not unit:
        return None
    # 无方向词：参数项带单位且 value 里确实有「数字+单位」时按阈值核对。
    for match in _VALUE_RE.finditer(value_text):
        number = _cn_number(match.group(1))
        if number is None:
            continue
        found_unit = (match.group(2) or unit).strip()
        if not found_unit:
            continue
        comparator = (
            "<=" if re.search(r"不超过|不大于|最多|响应|时延|延迟|工期", text) else ">="
        )
        return float(number), found_unit, comparator
    return None


def _numeric_candidates(body: str, name: str, unit: str) -> list[float]:
    """取正文里与参数同名子句、且单位同量纲的候选数值（已归一基准）。"""

    compact = re.sub(r"[（(].*?[）)]", "", name).strip()
    span = max(24, len(name))
    candidates: list[float] = []
    for match in _VALUE_RE.finditer(body):
        cand_unit = (match.group(2) or "").strip()
        # 裸数值不能借用参数项的量纲（「500 台设备」≠「500Mbps 带宽」）。
        if not cand_unit or unit_relation(cand_unit, unit) != UNIT_COMPATIBLE:
            continue
        # 用数值附近的文本窗口做主体关联：参数表常见「带宽：500Mbps」这类
        # 冒号/换行分隔，硬按标点切子句会把参数名切掉。
        window = body[max(0, match.start() - span): min(len(body), match.end() + 8)]
        if name and not (contains(window, name) or (len(compact) >= 4 and contains(window, compact))):
            continue
        number = _cn_number(match.group(1))
        if number is None:
            continue
        candidates.append(_to_base(float(number), cand_unit))
    return candidates


def param_response_missing(
    parameters: Any, text: Any, tolerance: float = 0.0
) -> list[str]:
    """未获响应的参数名；数值阈值不满足（如 500Mbps 答 1000Mbps）也算未响应。

    ``tolerance`` 是相对容差（如 0.01 表示允许 1% 偏差），默认 0 仅吸收浮点
    换算误差。非数值参数仍按原来的 token 存在性判断。
    """

    missing_items: list[str] = []
    body = str(text or "")
    for parameter in parameters or []:
        parameter = parameter or {}
        name = str(parameter.get("name") or "")
        tokens = _param_tokens(parameter)
        if not tokens:
            continue
        required = _required_numeric(parameter)
        if required is not None:
            threshold, unit, comparator = required
            candidates = _numeric_candidates(body, name, unit)
            if candidates:
                base = _to_base(threshold, unit)
                if not any(_numeric_pass(item, base, comparator, tolerance) for item in candidates):
                    missing_items.append(name)
                continue
        if not any(contains(body, token) for token in tokens):
            missing_items.append(name)
    return missing_items


def param_response_ratio(parameters: Any, text: Any, tolerance: float = 0.0) -> float:
    items = [p for p in (parameters or []) if str((p or {}).get("name") or "").strip()]
    if not items:
        return 1.0
    return 1.0 - len(param_response_missing(items, text, tolerance)) / len(items)


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
        # AST 已由 _validate 白名单校验，且 __builtins__ 为空，仅执行受控表达式。
        return eval(compile(tree, "<check>", "eval"), {"__builtins__": {}}, env)  # nosec B307
    except ExpressionError:
        raise
    except Exception as exc:
        raise ExpressionError(f"表达式执行失败: {exc}") from exc
