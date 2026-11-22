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
    _UNIT_DIMENSIONS,
    _VALUE_RE,
    UNIT_COMPATIBLE,
    UNIT_INCOMPATIBLE,
    UNIT_UNKNOWN,
    constraint_blocked,
    evaluate_constraints,
    extract_constraints,
    unit_dimension,
    unit_relation,
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


# -------------------------------------------------- T02：单位三态 + 存储量纲


def test_unit_relation_three_states():
    """T02：兼容 / 不可兼容 / 未知三态，缺单位默认 unknown 而非兼容。"""

    assert unit_relation("Mbps", "Gbps") == UNIT_COMPATIBLE
    assert unit_relation("Mbps", "MHz") == UNIT_INCOMPATIBLE
    assert unit_relation("Mbps", "") == UNIT_UNKNOWN
    assert unit_relation("", "Mbps") == UNIT_UNKNOWN
    assert unit_relation("GB", "Gbps") == UNIT_INCOMPATIBLE  # 存储 vs 带宽
    assert unit_relation("GB", "TB") == UNIT_COMPATIBLE  # 同为存储
    assert unit_dimension("GB") == "storage"
    assert unit_dimension("GB/s") == "byte_rate"


def test_storage_capacity_units_do_not_cross_compare():
    """存储与带宽跨量纲不可比；同为存储容量则按量纲归一后真实比较。

    R04 修订：GB/TB 已纳入方向正则后，500GB 应作为真实存储值被比较，
    「500GB < 1000GB」→ violated（不再因「提取不到单位」误转 unclear）。
    """

    # 同为存储容量，归一后可比（1000GB = 1TB）→ 满足。
    assert _blocked("存储容量不小于1000GB", "设备配备1TB硬盘") is False
    # 500GB < 1000GB → 真实不满足（R04 明确要求）。
    states = _states("存储容量不小于1000GB", "设备配备500GB硬盘")
    assert any(item[1] == "violated" for item in states), states
    # 跨量纲：存储证据不能回答带宽约束（absent/unclear 皆可，唯独不能满足或违反）。
    cross = _states("传输带宽不小于1000Mbps", "设备配备500GB硬盘")
    assert all(item[1] in {"absent", "unclear"} for item in cross), cross


# -------------------------------------------------- R04：单位与约束提取完整性


def test_storage_capacity_1000gb_equals_1tb():
    """R04 特别要求：必须断言真实状态 satisfied，而非只看 constraint_blocked。"""

    states = _states("存储容量不小于1000GB", "设备配备1TB硬盘")
    assert states and all(item[1] == "satisfied" for item in states), states


def test_storage_capacity_500gb_below_1000gb():
    """R04 特别要求：500GB < 1000GB 必须真实判 violated。"""

    states = _states("存储容量不小于1000GB", "设备配备500GB硬盘")
    assert any(item[1] == "violated" for item in states), states


def test_bandwidth_mbps_and_byte_rate_not_equal():
    """R04：带宽(Mbps) 与字节率(MB/s) 不同量纲，不得认定相等，转 unclear。"""

    states = _states("带宽不小于1000Mbps", "接口带宽100MB/s")
    assert any(item[1] == "unclear" for item in states), states
    assert unit_relation("Mbps", "MB/s") == UNIT_INCOMPATIBLE


def test_all_registered_units_are_extractable():
    """R04：单位已注册但方向约束正则提取不到 = 失效；此处逐一验证可提取。"""

    for unit in _UNIT_DIMENSIONS:
        match = _VALUE_RE.search(f"1{unit}")
        assert match is not None, f"单位 {unit} 无法被数值正则提取"
        assert match.group(2) == unit, f"单位 {unit} 被截断为 {match.group(2)}"


def test_unknown_unit_routes_to_review():
    """R04：单位无法确定必须转人工复核（unclear 不硬拦，但上层不覆盖）。"""

    from bidcraft.substantive import find_covering_section

    states = _states("带宽不小于1000Mbps", "链路带宽1000xyz")
    assert any(item[1] == "unclear" for item in states), states
    # unclear 不得自动放行 → find_covering_section 不覆盖。
    assert find_covering_section(
        "带宽不小于1000Mbps", [{"id": "n1", "position": 1, "body": "链路带宽1000xyz"}]
    ) is None


def test_unit_conversion_decimal_and_binary():
    """R04：十进制(1000) 与二进制(1024) 容量按各自基准换算，不可混为等值。"""

    assert unit_dimension("GiB") == "storage"
    assert unit_relation("GB", "GiB") == UNIT_COMPATIBLE  # 同量纲，可按字节换算
    # 十进制：1TB = 1000GB。
    assert _states("存储容量不小于1000GB", "配备1TB硬盘")[0][1] == "satisfied"
    # 二进制：1GiB ≈ 1.074GB，小于 2GB 要求 → violated（不会因「1GiB=1GB」误判）。
    states = _states("存储容量不小于2GB", "配备1GiB硬盘")
    assert any(item[1] in {"violated", "unclear"} for item in states), states


def test_direction_pattern_captures_complete_unit():
    """R04：方向约束正则必须捕获完整单位（GB/TB/MB/s/GHz 不被截断）。"""

    text = "存储不小于1000GB，带宽至少500Mbps，主频不低于2GHz，速率不低于100MB/s"
    units = [item.constraint.unit for item in evaluate_constraints(text, "x")]
    for expected in ("GB", "Mbps", "GHz", "MB/s"):
        assert expected in units, f"{expected} 未被方向正则完整提取: {units}"


# -------------------------------------------------- T04：多候选数值关联


def test_multiple_candidates_use_subject_association_not_first_or_max():
    """T04：多候选场景不能取第一个也不能取最大值，按主体关联归因；冲突转 unclear。"""

    # 两个候选都是带宽量纲、结论一致（都满足）→ 采纳满足。
    states_ok = _states("带宽不小于100Mbps", "链路带宽1000Mbps，备份带宽200Mbps")
    assert all(item[1] != "violated" for item in states_ok), states_ok

    # 两个候选结论冲突（满足 vs 违反），且主体关联分相同（都含「带宽」）→ 转 unclear。
    conflict_clause = "带宽1000Mbps；带宽50Mbps"
    text = "带宽不小于500Mbps"
    conflict = _states(text, conflict_clause)
    assert any(item[1] == "unclear" for item in conflict), conflict
    # unclear 不是硬伤，但也不能自动放行 → find_covering_section 不覆盖。
    from bidcraft.substantive import find_covering_section

    assert find_covering_section(text, [{"id": "n1", "position": 1, "body": conflict_clause}]) is None


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


# =============================================================== R11：局部主体（无标点）
def test_family_local_scope_without_punctuation():
    """R11：无标点的复合条款，两约束各归各族，主体不串味。"""
    from bidcraft.constraint_check import extract_constraints

    cs = extract_constraints("近三年不少于3项类似项目业绩且网络带宽不低于1000Mbps")
    families = {c.family for c in cs}
    assert "achievement" in families and "bandwidth" in families
    bw = [c for c in cs if c.family == "bandwidth"]
    assert bw and any(c.threshold == 1000 for c in bw)


def test_achievement_and_bandwidth_without_comma():
    """R11：业绩数量不能用于带宽判定（局部上下文隔离）。"""
    from bidcraft.constraint_check import evaluate_constraints

    body = "近三年完成三项类似项目业绩，网络带宽500Mbps。"
    verdicts = evaluate_constraints("近三年不少于3项类似项目业绩且网络带宽不低于1000Mbps", body)
    bw = [v for v in verdicts if v.constraint.family == "bandwidth"]
    assert bw and any(v.state == "violated" for v in bw)
    ach = [v for v in verdicts if v.constraint.family == "achievement"]
    assert not any(v.state == "violated" for v in ach)


def test_warranty_and_response_same_clause():
    """R11：同一标点子句内的质保与响应约束各自归族。"""
    from bidcraft.constraint_check import extract_constraints

    cs = extract_constraints("质保5年并提供2小时内响应承诺")
    families = {c.family for c in cs}
    assert "warranty" in families and "response" in families


def test_subject_does_not_leak_between_atoms():
    """R11：带宽主体词不泄漏到业绩约束。"""
    from bidcraft.constraint_check import extract_constraints

    cs = extract_constraints("类似项目业绩不少于3项且带宽不低于1000Mbps")
    ach = [c for c in cs if c.family == "achievement"]
    assert ach and all("带宽" not in (c.object or "") for c in ach)


def test_multiple_link_bandwidth_disambiguation():
    """R11：主备链路多个带宽候选，按局部上下文归因。"""
    from bidcraft.constraint_check import evaluate_constraints

    verdicts = evaluate_constraints("主链路带宽不低于1000Mbps", "主链路带宽500Mbps。备链路带宽2000Mbps。")
    assert verdicts and verdicts[0].state == "violated"  # 主链路500 < 1000，不被备链路2000替代


def test_ambiguous_subject_returns_unclear():
    """R11：主体关联不明确时返回 unclear 转人工复核。"""
    from bidcraft.constraint_check import evaluate_constraints

    verdicts = evaluate_constraints("性能指标不低于1000Mbps", "数值500。数值2000。")
    # 无带宽主体词 → family 为空或无法归因，不得自动判 satisfied。
    assert all(v.state != "satisfied" for v in verdicts)


# =============================================================== R13：单位注册表完整性
def test_all_registered_units_can_be_extracted():
    """R13：每个已登记单位都能被约束方向正则正确提取。"""
    from bidcraft.constraint_check import unit_registry_issues

    assert unit_registry_issues() == []


def test_unit_registry_has_no_duplicate_conflict():
    """R13：单位注册表无重复键、量纲/系数一致。"""
    from bidcraft.constraint_check import _UNIT_DIMENSIONS, _UNITS

    assert len(set(_UNITS.split("|")) - {""}) >= len(_UNIT_DIMENSIONS)
    for dim, coef in _UNIT_DIMENSIONS.values():
        assert dim and coef > 0


def test_storage_decimal_conversion():
    """R13：十进制存储 1000GB == 1TB。"""
    from bidcraft.constraint_check import evaluate_constraints

    v = evaluate_constraints("存储容量不小于1000GB", "存储容量1TB")[0]
    assert v.state == "satisfied"


def test_storage_binary_conversion():
    """R13：二进制存储 1TiB == 1024GiB。"""
    from bidcraft.constraint_check import evaluate_constraints

    v = evaluate_constraints("存储容量不小于1024GiB", "存储容量1TiB")[0]
    assert v.state == "satisfied"


def test_bandwidth_and_byte_rate_separate():
    """R13：Mbps 与 MB/s 不同量纲，不得视为相同。"""
    from bidcraft.constraint_check import UNIT_INCOMPATIBLE, evaluate_constraints, unit_relation

    assert unit_relation("Mbps", "MB/s") == UNIT_INCOMPATIBLE
    v = evaluate_constraints("带宽不小于1000Mbps", "带宽100MB/s")[0]
    assert v.state in {"unclear", "absent"}


def test_month_year_duration_conversion():
    """R13：质保 5 年 == 60 个月（统一基准）。"""
    from bidcraft.constraint_check import evaluate_constraints

    v = evaluate_constraints("质保不少于5年", "质保60个月")[0]
    assert v.state == "satisfied"


def test_unknown_unit_returns_unclear():
    """R13：未登记单位不自动兼容，转人工复核。"""
    from bidcraft.constraint_check import UNIT_UNKNOWN, unit_relation

    assert unit_relation("Mbps", "未知单位") == UNIT_UNKNOWN


def test_float_boundary_comparison():
    """R13：浮点换算边界不因精度误差误判。"""
    from bidcraft.constraint_check import evaluate_constraints

    v = evaluate_constraints("带宽不小于1Gbps", "带宽1000Mbps")[0]
    assert v.state == "satisfied"
