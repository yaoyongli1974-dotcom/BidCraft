"""统一金额服务：Decimal 计算消除累加误差，边界出口仍是 float。"""

from __future__ import annotations

from decimal import Decimal

from bidcraft.services import money


def test_accumulation_has_no_float_drift():
    # 0.1 + 0.2 在 float 下是 0.30000000000000004；Decimal 下精确 0.3。
    assert money.sum_amounts([0.1, 0.2]) == 0.3
    assert money.sum_amounts(["0.1", "0.2", "0.3"]) == 0.6


def test_line_amount_uses_decimal_multiplication():
    # 单行金额 = qty × 单价，量化到分。
    assert money.line_amount(3, 12.5) == 37.5
    assert money.line_amount(1.1, 0.1) == 0.11  # float 下 0.11000000000000001
    assert money.line_amount(None, 12.5) is None
    assert money.line_amount(1, None) is None


def test_unit_price_rounding_and_validation():
    assert money.unit_price(12.34567) == 12.3457
    assert money.unit_price(12.5) == 12.5
    assert money.unit_price("abc") is None
    assert money.unit_price(-1) is None
    assert money.unit_price(True) is None
    assert money.unit_price(float("nan")) is None


def test_tax_and_total_use_round_half_up():
    tax, total = money.tax_amount(55.0, 0.13)
    assert tax == 7.15
    assert total == 62.15
    # 银行家舍入会把 2.5→2，这里商业惯例 ROUND_HALF_UP 应为 3。
    assert money.quantize(Decimal("2.5"), 0) == Decimal(3)


def test_cny_and_adjusted_total():
    assert money.cny_total(62.15, 7.2) == 447.48
    assert money.adjusted_total(100.0, 1.1, 2) == 110.0
    assert money.adjusted_total(55.5, 0.1, 0) == 6.0


def test_qty_delta_and_decimal_conversion():
    assert money.qty_delta(10.0, 9.5) == 0.5
    assert money.qty_delta(10, 10) == 0.0
    assert money.qty_delta(None, 10) is None
    assert money.to_decimal("0.1") == Decimal("0.1")
    assert money.to_decimal(float("inf")) is None
    assert money.to_decimal(True) is None


def test_to_float_none_passthrough():
    assert money.to_float(None) is None
    assert money.to_float(Decimal("12.345"), 2) == 12.35
