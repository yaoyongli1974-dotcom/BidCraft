"""统一金额服务：报价、税额、汇总的精度与舍入口径。

设计要点
--------
* 计算内部一律用 :class:`~decimal.Decimal`，避免 float 累加误差
  （``0.1 + 0.2 == 0.30000000000000004`` 这类问题）。
* 对外边界（JSON 列 / API 响应 / Excel 单元格 / 前端）仍输出 ``float``，
  保持既有请求-响应格式与测试断言不变——这是用户选定的「计算层 Decimal，
  出口 float」方案。
* 统一精度：单价 4 位、金额/税额/合计 2 位、工程量差异 6 位；
  统一舍入：ROUND_HALF_UP（商业惯例，区别于 Python 默认的银行家舍入）。

调用方约定：所有金额/数量在**参与运算前**用 :func:`to_decimal` 转 Decimal，
运算完成后用 :func:`to_float` 回到边界值。绝不要把 float 直接混进 Decimal
运算，也不要把 Decimal 直接写进 JSON 列。
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: 单价（unit_price）保留位数。
UNIT_PRICE_DIGITS = 4
#: 金额 / 税额 / 合计（amount/tax/total）保留位数。
AMOUNT_DIGITS = 2
#: 工程量差异比较位数（仅用于对齐分类，不改变原始 qty）。
QTY_DIGITS = 6

#: 统一舍入模式：四舍五入（商业惯例）。
ROUNDING = ROUND_HALF_UP

_ZERO = Decimal(0)


def to_decimal(value: Any) -> Decimal | None:
    """把边界值（float/int/str/Decimal）安全转 Decimal；非法值返回 None。

    bool 不是合法金额。float 走 ``str()`` 中转，保留 JSON 里的十进制书写
    （``12.5`` → ``Decimal("12.5")``），而不是把二进制展开带进金额运算。
    NaN / inf 一律拒绝。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        candidate = value
    else:
        try:
            candidate = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    return candidate if candidate.is_finite() else None


def quantize(value: Decimal, digits: int = AMOUNT_DIGITS) -> Decimal:
    """按 ``digits`` 位小数、统一舍入模式量化。"""
    quantum = Decimal(1).scaleb(-digits)
    return value.quantize(quantum, rounding=ROUNDING)


def to_float(value: Decimal | None, digits: int | None = None) -> float | None:
    """边界出口：Decimal → float（可选量化）；None 透传。"""
    if value is None:
        return None
    return float(quantize(value, digits) if digits is not None else value)


def unit_price(value: Any) -> float | None:
    """规范化单价：非法返回 None，合法量化到 4 位后输出 float。"""
    number = to_decimal(value)
    if number is None or number < 0:
        return None
    return to_float(quantize(number, UNIT_PRICE_DIGITS))


def line_amount(qty: Any, price: Any) -> float | None:
    """单行金额 = 工程量 × 单价，Decimal 运算后量化到分输出 float。

    任一参数非法或数量缺失返回 None（调用方据此标记 quantity_missing）。
    """
    quantity = to_decimal(qty)
    unit = to_decimal(price)
    if quantity is None or unit is None:
        return None
    return to_float(quantize(quantity * unit, AMOUNT_DIGITS))


def sum_amounts(values: Iterable[Any]) -> float:
    """汇总金额：先把每个边界值转 Decimal 再累加，最后量化到分输出 float。

    这里正是 float 累加误差的高发点——必须逐个转 Decimal，而不是
    ``sum(float_values)``。非法值按 0 处理（调用方已过滤 priced 行）。
    """
    total = _ZERO
    for value in values:
        number = to_decimal(value)
        if number is not None:
            total += number
    return to_float(quantize(total, AMOUNT_DIGITS)) or 0.0


def tax_amount(total: Any, tax_rate: Any) -> tuple[float, float]:
    """返回 ``(税额, 含税合计)``：税额 = 合计 × 税率，量化到分。"""
    base = to_decimal(total) or _ZERO
    rate = to_decimal(tax_rate) or _ZERO
    tax = quantize(base * rate, AMOUNT_DIGITS)
    with_tax = quantize(base + tax, AMOUNT_DIGITS)
    return to_float(tax) or 0.0, to_float(with_tax) or 0.0


def cny_total(total_with_tax: Any, exchange_rate: Any) -> float:
    """含税合计 × 汇率 → 人民币金额，量化到分。"""
    base = to_decimal(total_with_tax) or _ZERO
    rate = to_decimal(exchange_rate) or _ZERO
    return to_float(quantize(base * rate, AMOUNT_DIGITS)) or 0.0


def adjusted_total(total: Any, factor: Any, digits: int) -> float:
    """展示用调价后合计 = 合计 × 系数，按策略位数量化。"""
    base = to_decimal(total) or _ZERO
    multiplier = to_decimal(factor) or _ZERO
    return to_float(quantize(base * multiplier, digits)) or 0.0


def qty_delta(boq_qty: Any, ledger_qty: Any) -> float | None:
    """工程量差异（对齐分类用），Decimal 相减后量化到 6 位输出 float。"""
    left = to_decimal(boq_qty)
    right = to_decimal(ledger_qty)
    if left is None or right is None:
        return None
    return to_float(quantize(left - right, QTY_DIGITS))
