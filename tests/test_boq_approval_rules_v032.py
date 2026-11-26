"""v0.32.1 审批链规则命中条件的纯函数回归。

不依赖 mock：直接调用生产 ``boq.normalize_chain`` / ``applicable_stages`` /
``completed_stages``，验证 opt-in ``rules`` 的归一、AND 求值、未知值保守放行
与阶段序号语义。旧链（无 rules）行为必须完全不变。
"""

from __future__ import annotations

from bidcraft import boq


def _chain(*entries: dict) -> list[dict]:
    return boq.normalize_chain(list(entries))


def test_normalize_preserves_valid_rules_and_drops_invalid():
    chain = _chain(
        {
            "id": "u1",
            "rules": [
                {"field": "total_amount", "op": "gte", "value": 1000},
                {"field": "currency", "op": "in", "value": ["CNY", "cny"]},
                {"field": "package_id", "op": "contains", "value": "A"},
                {"field": "bad", "op": "eq", "value": "x"},
                {"field": "total_amount", "op": "between", "value": 1},
                "not-a-rule",
            ],
        }
    )
    assert chain[0]["rules"] == [
        {"field": "total_amount", "op": "gte", "value": 1000},
        {"field": "currency", "op": "in", "value": ["CNY", "cny"]},
        {"field": "package_id", "op": "contains", "value": "A"},
    ]


def test_numeric_rules_filter_stage_but_keep_fallback():
    chain = _chain(
        {"id": "u1", "rules": [{"field": "total_amount", "op": "gte", "value": 1000}]},
        {"id": "u2"},
    )
    assert [index for index, _stage in boq.applicable_stages(chain, 999)] == [1]
    assert [index for index, _stage in boq.applicable_stages(chain, 1000)] == [0, 1]


def test_lte_package_and_currency_rules_are_anded():
    chain = _chain(
        {
            "id": "u1",
            "rules": [
                {"field": "total_amount", "op": "lte", "value": 5000},
                {"field": "package_id", "op": "eq", "value": "pkg-a"},
                {"field": "currency", "op": "eq", "value": "cny"},
            ],
        },
        {"id": "u2"},
    )
    assert [i for i, _ in boq.applicable_stages(chain, 4000, "pkg-a", "CNY")] == [0, 1]
    assert [i for i, _ in boq.applicable_stages(chain, 6000, "pkg-a", "CNY")] == [1]
    assert [i for i, _ in boq.applicable_stages(chain, 4000, "pkg-b", "CNY")] == [1]
    assert [i for i, _ in boq.applicable_stages(chain, 4000, "pkg-a", "USD")] == [1]


def test_in_not_in_and_contains_rules():
    chain = _chain(
        {
            "id": "u1",
            "rules": [
                {"field": "package_id", "op": "in", "value": ["pkg-a", "pkg-b"]},
                {"field": "currency", "op": "not_in", "value": ["USD"]},
                {"field": "package_id", "op": "contains", "value": "-a"},
            ],
        },
        {"id": "u2"},
    )
    assert [i for i, _ in boq.applicable_stages(chain, 1, "pkg-a", "CNY")] == [0, 1]
    assert [i for i, _ in boq.applicable_stages(chain, 1, "pkg-b", "CNY")] == [1]
    assert [i for i, _ in boq.applicable_stages(chain, 1, "pkg-a", "USD")] == [1]


def test_unknown_context_keeps_rule_satisfied():
    chain = _chain(
        {
            "id": "u1",
            "rules": [
                {"field": "total_amount", "op": "gte", "value": 1000},
                {"field": "package_id", "op": "eq", "value": "pkg-a"},
            ],
        },
        {"id": "u2"},
    )
    assert [i for i, _ in boq.applicable_stages(chain)] == [0, 1]


def test_completed_stages_counts_original_indices_after_rule_filter():
    chain = _chain(
        {"id": "u1", "rules": [{"field": "total_amount", "op": "gte", "value": 1000}]},
        {"id": "u2"},
    )
    approvals = [{"decision": "approve", "stage": 1}]
    # Low amount: stage 0 is skipped, so the approval on original stage 1 is level 1.
    assert boq.completed_stages(chain, approvals, 999) == 1
    # High amount: stage 0 is pending, so no applicable level is complete.
    assert boq.completed_stages(chain, approvals, 1000) == 0


def test_legacy_chain_without_rules_is_unchanged():
    chain = _chain({"id": "u1"}, {"id": "u2"})
    assert all("rules" not in stage for stage in chain)
    assert [i for i, _ in boq.applicable_stages(chain, 1, "pkg", "CNY")] == [0, 1]
