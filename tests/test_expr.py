from __future__ import annotations

import pytest

from bidcraft.rules.expr import (
    ExpressionError,
    contains,
    evaluate,
    materials_total,
    number,
    storage_tb,
)


def test_number_parses_currency_units():
    assert number("人民币 350 万元") == 3_500_000
    assert number("1.2 亿元") == 120_000_000
    assert number("90 天") == 90
    assert number("") is None


def test_contains_is_whitespace_insensitive():
    assert contains("工作区子系统、水平子系统", "工作区子系统")
    assert contains("Cat6A 线缆", "cat6a")
    assert not contains("", "任何")


def test_materials_total_excludes_length_units():
    rows = [
        {"name": "信息点面板", "unit": "个", "qty": 480},
        {"name": "双绞线", "unit": "米", "qty": 12000},
        {"name": "配线架", "unit": "个", "qty": 20},
    ]
    assert materials_total(rows) == 500
    assert materials_total(rows, "面板") == 480


def test_storage_capacity_formula():
    assert storage_tb(96, 30, 4) == pytest.approx(124.42, abs=0.01)


def test_expression_whitelist_blocks_attribute_access():
    with pytest.raises(ExpressionError):
        evaluate("().__class__.__bases__", {})
    with pytest.raises(ExpressionError):
        evaluate("__import__('os')", {})


def test_expression_runs_checks():
    context = {
        "facts": {"project.budget": "350 万元", "bidder.price": "320 万元"},
        "doc": "投标有效期 90 天",
        "materials": [],
        "parameters": [],
    }
    assert evaluate("number(facts['project.budget']) >= number(facts['bidder.price'])", context)
    assert evaluate("contains(doc, '90 天')", context)
    assert not evaluate("contains(doc, '180 天')", context)
