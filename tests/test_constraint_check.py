"""约束条件比较：数值方向、否定语义、复合条款拆分。

对应用户报告的三类误判：
1. 模式出现但数值更小（1000 Mbps vs 500 Mbps）却判满足；
2. 字面包含但语义相反（具备 vs 不具备）却判满足；
3. 一条复合条款只响应了部分约束却判整条覆盖。

每条都断言 ConstraintVerdict 的逐条结果，让「哪条约束没过」直接可见。
"""

from __future__ import annotations

import pytest

from bidcraft.constraint_check import (
    constraint_blocked,
    evaluate_constraints,
    extract_constraints,
    unit_dimension,
)
from bidcraft.substantive import find_covering_section

STAR = "★"


def _states(text: str, body: str) -> list[tuple[str, str]]:
    return [(item.constraint.label, item.state) for item in evaluate_constraints(text, body)]


def _blocked(text: str, body: str) -> bool:
    return constraint_blocked(evaluate_constraints(text, body))


# ---------------------------------------------------------- 问题 1：数值比较


@pytest.mark.parametrize(
    "requirement,body",
    [
        ("传输带宽不小于 1000 Mbps", "系统带宽为 500 Mbps"),
        ("不低于1000Mbps", "带宽500Mbps"),
        ("不少于3项业绩", "2项业绩"),
        ("近三年不少于3项类似项目业绩", "近三年2项类似项目业绩"),
        ("2小时内响应", "8小时内响应"),
        ("2小时内响应", "响应时间不得超过 8 小时"),
        ("质保5年", "质保3年"),
        ("一级资质", "二级资质"),
        ("建筑智能化一级资质", "建筑智能化二级资质"),
    ],
)
def test_smaller_or_lower_value_is_violated(requirement: str, body: str):
    assert _blocked(requirement, body) is True, _states(requirement, body)
    assert all(item[1] != "satisfied" for item in _states(requirement, body))


@pytest.mark.parametrize(
    "requirement,body",
    [
        ("传输带宽不小于 1000 Mbps", "链路带宽≥1000Mbps"),
        ("不低于1000Mbps", "带宽不低于1000Mbps"),  # 比较级里的「不」不是否定
        ("不少于3项业绩", "近五年已完成6个业绩"),
        ("2小时内响应", "1小时内响应"),
        ("质保5年", "质保5年"),
        ("质保5年", "质保60个月"),  # 单位归一后再比较
        ("一级资质", "特级资质"),
    ],
)
def test_equal_or_better_value_is_satisfied(requirement: str, body: str):
    assert _blocked(requirement, body) is False, _states(requirement, body)


def test_chinese_numeric_values_compare():
    assert _blocked("不少于五项业绩", "三项业绩") is True
    assert _blocked("不少于三项业绩", "六项业绩") is False
    assert _blocked("质保三年", "质保五年") is False


def test_near_years_window_is_anchor_not_comparison():
    """「近 N 年」是描述性锚点，不升级成 <= N 年（既有判定口径）。"""

    labels = [item.label for item in extract_constraints("近三年业绩不少于3项")]
    assert not any("年" in label for label in labels), labels
    assert _blocked("近三年业绩不少于3项", "近五年完成6项同类项目业绩") is False


# -------------------------------------------------- P0：单位缺失 / 跨量纲比较


def test_unit_compatibility_requires_explicit_same_dimension():
    """单位缺失绝不认定量纲一致；跨量纲（带宽 vs 字节率/频率）不可比较。"""

    assert unit_dimension("Mbps") == "bandwidth"
    assert unit_dimension("Gbps") == "bandwidth"
    assert unit_dimension("MB/s") == "byte_rate"
    assert unit_dimension("MHz") == "frequency"
    assert unit_dimension("") == ""
    assert unit_dimension("未知单位") == ""


def test_unitless_number_is_not_adopted_as_requirement_unit():
    """P0 核心：正文裸写的「500」不能被当成 500Mbps 去和 1000Mbps 比较。

    系统包含 500 台设备、带宽 1000Mbps → 可比数值只有 1000Mbps → 满足。
    """

    text = "传输带宽不小于1000Mbps"
    body = "系统包含500台设备，带宽1000Mbps"
    states = _states(text, body)
    assert all(item[1] != "unclear" for item in states), states
    assert _blocked(text, body) is False, states

    # 只有裸 500（无单位）时，转人工复核而非误判为满足/违反。
    unclear_states = _states(text, "系统吞吐为500")
    assert any(item[1] == "unclear" for item in unclear_states), unclear_states
    assert _blocked(text, "系统吞吐为500") is False  # unclear 不是硬伤，不走不满足拦截


def test_cross_dimension_frequency_and_byte_rate_are_unclear_not_equal():
    """500MHz 处理器、100MB/s 都不能和 1000Mbps 当成同一量纲比较。"""

    # 带宽约束在「500MHz 处理器」子句里没有可比数值 → 不判满足/违反。
    states = _states("带宽不小于1000Mbps", "采用500MHz处理器，带宽1000Mbps")
    assert all(item[1] != "violated" for item in states), states
    assert _blocked("带宽不小于1000Mbps", "采用500MHz处理器，带宽1000Mbps") is False

    # 100MB/s 与 1000Mbps 不是相同量纲，不能因为「100 < 1000」判违反。
    states2 = _states("带宽不小于1000Mbps", "接口带宽100MB/s")
    assert any(item[1] == "unclear" for item in states2), states2
    assert _blocked("带宽不小于1000Mbps", "接口带宽100MB/s") is False

    # 量纲登记本身也必须把两者分开。
    assert unit_dimension("Mbps") != unit_dimension("MB/s")
    assert unit_dimension("Mbps") != unit_dimension("MHz")


def test_mbps_and_gbps_convert_within_same_dimension():
    """1000Mbps 与 1Gbps 同量纲，归一后相等 → 满足。"""

    assert _blocked("传输带宽不小于1000Mbps", "链路带宽1Gbps") is False
    assert all(item[1] != "unclear" for item in _states("传输带宽不小于1000Mbps", "链路带宽1Gbps"))


def test_unclear_routes_to_review_via_judge():
    """单位 deficiency 在五层判定里走 REVIEW，且 ★ 为 blocking。"""

    from types import SimpleNamespace

    from bidcraft.verdict import REVIEW, judge

    req = SimpleNamespace(
        text="★ 传输带宽不小于1000Mbps",
        marker="★",
        source_quote="带宽要求",
    )
    verdict = judge(req, [{"id": "n1", "position": 1, "body": "接口带宽100MB/s"}])
    assert verdict.state == REVIEW
    assert verdict.level == "blocking"
    assert "单位" in verdict.reason or "量纲" in verdict.reason


# ---------------------------------------------------------- 问题 2：否定语义


@pytest.mark.parametrize(
    "requirement,body",
    [
        ("具备一级建造师资格", "我方不具备一级建造师资格"),
        ("具备一级建造师资格", "我方没有一级建造师证书"),
        ("提供2小时响应服务", "我方不提供2小时响应服务"),
        ("质保5年", "本产品不质保5年"),
    ],
)
def test_negated_assertion_is_violated(requirement: str, body: str):
    assert _blocked(requirement, body) is True, _states(requirement, body)


def test_negation_does_not_leak_across_clauses():
    """否定词只在同子句内生效，不能误伤另一句的正面响应。"""

    body = "我方具备一级建造师资格。其他专业资质暂不具备。"
    assert _blocked("具备一级建造师资格", body) is False, _states("具备一级建造师资格", body)


def test_negated_core_inclusion_does_not_cover():
    """整句字面包含也必须在肯定句里——这是用户指出的直接 bug 点。"""

    text = "★ 项目经理须具备一级建造师执业资格"
    nodes = [{"id": "n1", "position": 1, "heading": "人员", "body": "我方不具备一级建造师执业资格。"}]
    assert find_covering_section(text, nodes) is None

    nodes_good = [{"id": "n1", "position": 1, "heading": "人员", "body": "我方具备一级建造师执业资格。"}]
    assert find_covering_section(text, nodes_good) is not None


def test_negated_anchor_does_not_cover():
    """锚点命中也要查否定。"""

    from bidcraft.substantive import anchor_coverage, classify_requirement

    text = "★ 项目经理须具备一级建造师执业资格"
    req_type = classify_requirement(text)
    assert anchor_coverage(text, "我方不具备一级建造师资格。", req_type) == 0.0
    assert anchor_coverage(text, "我方具备一级建造师资格。", req_type) > 0


# ---------------------------------------------------------- 问题 3：复合条款拆分


def test_compound_requirement_splits_into_multiple_constraints():
    text = (
        "投标人须具有建筑智能化一级资质，近三年具有不少于3项类似项目业绩，"
        "并提供2小时内到场服务承诺。"
    )
    constraints = extract_constraints(text)
    labels = [item.label for item in constraints]
    assert len(constraints) >= 3, labels
    assert any("等级" in label for label in labels), labels
    assert any("至少" in label and "项" in label for label in labels), labels
    assert any("响应不超过" in label for label in labels), labels


def test_partial_response_does_not_cover_compound_requirement():
    text = (
        "投标人须具有建筑智能化一级资质，近三年具有不少于3项类似项目业绩，"
        "并提供2小时内到场服务承诺。"
    )
    # 资质与业绩响应了，响应时间没写 → 整条不算覆盖
    partial = (
        "我方具有建筑智能化一级资质，近三年完成 5 项类似项目业绩。"
    )
    assert find_covering_section(text, [{"id": "n1", "position": 1, "body": partial}]) is None
    assert _blocked(text, partial) is True

    # 三条都响应 → 覆盖
    full = partial + "并提供1小时内到场服务承诺。"
    assert find_covering_section(text, [{"id": "n1", "position": 1, "body": full}]) is not None


def test_constraints_can_be_satisfied_across_chapters():
    """复合条款的三条约束可分散在多个章节响应。"""

    text = (
        "投标人须具有建筑智能化一级资质，近三年具有不少于3项类似项目业绩，"
        "并提供2小时内到场服务承诺。"
    )
    nodes = [
        {"id": "a", "position": 1, "body": "我方具有建筑智能化一级资质。"},
        {"id": "b", "position": 2, "body": "近三年完成 5 项类似项目业绩。"},
        {"id": "c", "position": 3, "body": "提供 1 小时内到场服务承诺。"},
    ]
    assert find_covering_section(text, nodes) is not None


# ---------------------------------------------------------- 举证信息


def test_verdicts_are_exposed_in_issue_evidence():
    from types import SimpleNamespace

    from bidcraft.substantive import requirement_issues

    requirement = SimpleNamespace(
        id="r1",
        text="★ 传输带宽不小于1000Mbps",
        marker="★",
        mandatory=True,
        source_quote="★ …",
        response_status="pending",
    )
    nodes = [{"id": "n1", "position": 1, "body": "系统带宽为500Mbps。"}]
    issues = requirement_issues([requirement], nodes)
    assert len(issues) == 1
    constraints = issues[0].evidence["constraints"]
    assert constraints and constraints[0]["state"] == "violated"
    assert "1000" in constraints[0]["label"]
    assert "不符合" in issues[0].message
