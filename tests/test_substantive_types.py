"""实质性条款类型分类 + 结构化锚点判定。

3-gram 对「改写过但意思到了」的响应偏保守（数值变了、同义词换了就判未响应）。
这里验证新增的类型结构化路径：可归类的条款用「条款与正文都出现的模式」补判，
降低误报；但正文凭空出现的数字不算响应，通用条款仍严格回退 3-gram。
"""

from __future__ import annotations

from bidcraft.substantive import (
    REQ_TYPE_ACHIEVEMENT,
    REQ_TYPE_COMMITMENT,
    REQ_TYPE_GENERAL,
    REQ_TYPE_PARAMETER,
    REQ_TYPE_QUALIFICATION,
    anchor_coverage,
    anchor_evidence,
    classify_requirement,
    coverage_ratio,
    find_covering_section,
    type_anchor_patterns,
)

# ------------------------------------------------------- 分类


def test_classify_four_types_and_general():
    assert (
        classify_requirement("★ 投标人须具备建筑智能化工程专业承包一级资质证书")
        == REQ_TYPE_QUALIFICATION
    )
    assert classify_requirement("★ 近三年同类项目业绩不少于3项") == REQ_TYPE_ACHIEVEMENT
    assert classify_requirement("▲ 传输带宽不小于 1000 Mbps") == REQ_TYPE_PARAMETER
    assert classify_requirement("▲ 提供 2 小时响应的售后服务承诺") == REQ_TYPE_COMMITMENT
    # 没有具体信号词 → 通用，不强行归类
    assert classify_requirement("本项目应符合国家现行规范") == REQ_TYPE_GENERAL


# ------------------------------------------------------- 锚点


def test_only_patterns_present_in_requirement_count():
    """正文里凭空出现的模式不算响应：锚点只取条款自身也有的。"""

    text = "★ 投标人须提供近三年同类项目业绩不少于3项"
    patterns = type_anchor_patterns(text, REQ_TYPE_ACHIEVEMENT)
    # 「业绩」「近N年」「数字+项」「同类项目」都是条款里出现的
    assert len(patterns) >= 3
    assert anchor_coverage(text, "本方案采用 Cat6A 综合布线。", REQ_TYPE_ACHIEVEMENT) == 0.0


def test_rewritten_achievement_response_is_covered():
    """数值换了（3 项→6 个）、同义换了（同类→相近、近三年→近五年）仍算响应。"""

    text = "★ 投标人须提供近三年同类项目业绩不少于3项"
    body = "我司近五年已完成 6 个相近项目，合同见附件。"
    assert coverage_ratio(text, body, REQ_TYPE_ACHIEVEMENT) >= 0.75
    assert (
        find_covering_section(
            text, [{"position": 1, "heading": "商务响应", "body": body}], REQ_TYPE_ACHIEVEMENT
        )
        is not None
    )


def test_chinese_and_arabic_numbers_both_count():
    text = "★ 近三年业绩不少于3项"
    chinese = "我单位近五年完成六项同类业绩。"
    arabic = "我单位近5年完成6项同类项目业绩。"
    assert anchor_coverage(text, chinese, REQ_TYPE_ACHIEVEMENT) == 1.0
    assert anchor_coverage(text, arabic, REQ_TYPE_ACHIEVEMENT) == 1.0


def test_parameter_threshold_rewrite_is_covered():
    text = "▲ 系统传输带宽不小于 1000 Mbps"
    body = "本方案链路带宽≥1000Mbps，满足要求。"
    assert coverage_ratio(text, body, REQ_TYPE_PARAMETER) >= 0.75


def test_qualification_and_commitment_types():
    qual_text = "★ 项目经理须具备一级建造师执业资格"
    assert anchor_coverage(qual_text, "拟派项目经理持有一级建造师证书。", REQ_TYPE_QUALIFICATION) > 0
    commit_text = "▲ 中标人须提供 2 小时响应的售后服务承诺"
    assert anchor_coverage(commit_text, "我方承诺 2 小时内响应，提供质保服务。", REQ_TYPE_COMMITMENT) > 0


# ------------------------------------------------------- 保守性


def test_random_numbers_in_body_do_not_cover():
    """正文里有数字但没有条款的关键事实，不能算响应。"""

    text = "★ 投标人须提供近三年同类项目业绩不少于3项"
    body = "本方案机房设 42 个机柜，桥架总长 300 米。"
    assert coverage_ratio(text, body, REQ_TYPE_ACHIEVEMENT) < 0.45
    assert find_covering_section(text, [{"position": 1, "heading": "技术方案", "body": body}], REQ_TYPE_ACHIEVEMENT) is None


def test_no_response_stays_uncovered():
    text = "▲ 项目经理须具备一级建造师执业资格"
    assert coverage_ratio(text, "本章节介绍项目总体技术方案。", REQ_TYPE_QUALIFICATION) == 0.0


def test_general_falls_back_to_3gram():
    """通用条款：严格走 3-gram，不启用结构化加成。"""

    text = "本项目应符合国家现行规范"
    body = "本项目严格应符合国家现行规范要求。"
    assert coverage_ratio(text, body, REQ_TYPE_GENERAL) > 0.45
    assert coverage_ratio(text, "无关内容", REQ_TYPE_GENERAL) == 0.0
    assert anchor_coverage(text, body, REQ_TYPE_GENERAL) == 0.0


# ------------------------------------------------------- 接口字段


def test_requirement_payload_exposes_type(db):
    from bidcraft.api.shared import _requirement_payload
    from bidcraft.db import session_scope
    from bidcraft.models import Requirement

    with session_scope() as session:
        row = Requirement(project_id="p", text="★ 需一级建造师资质证书", marker="★")
        session.add(row)
        session.flush()
        payload = _requirement_payload(session, row)

    assert payload["requirement_type"] == REQ_TYPE_QUALIFICATION
    assert payload["requirement_type_label"] == "资质资格"


# ------------------------------------------------------- 语义增强：深度改写容忍


def test_financial_numeral_and_synonym_qualification_is_covered():
    """「一级资质证书」写成「壹级资格」仍算响应（大写数字 + 同义词）。"""

    text = "★ 投标人须具备建筑智能化工程专业承包一级资质证书"
    body = "我司持有建筑智能化工程专业承包壹级资格，证书见附件。"
    assert anchor_coverage(text, body, REQ_TYPE_QUALIFICATION) == 1.0
    assert coverage_ratio(text, body, REQ_TYPE_QUALIFICATION) >= 0.45


def test_financial_numeral_and_synonym_achievement_is_covered():
    """「近三年…3项」写成「近伍年…叁项相近工程」仍算响应。"""

    text = "★ 近三年同类业绩不少于3项"
    body = "我单位近伍年完成叁项相近工程。"
    assert anchor_coverage(text, body, REQ_TYPE_ACHIEVEMENT) == 1.0
    assert coverage_ratio(text, body, REQ_TYPE_ACHIEVEMENT) >= 0.45


def test_synonym_hit_is_auditable():
    """语义扩展命中要留下证据，便于人工核对「为什么算/不算响应」。"""

    text = "★ 投标人须具备建筑智能化工程专业承包一级资质证书"
    body = "我司持有建筑智能化工程专业承包壹级资格，证书见附件。"
    evidence = anchor_evidence(text, body, REQ_TYPE_QUALIFICATION)
    assert any(item["via"] == "synonym" and item["variant"] == "壹级" for item in evidence)


def test_negated_synonym_is_still_not_covered():
    """语义扩展不能越过否定句：「不具备壹级建造师资格」不是响应。"""

    text = "★ 项目经理须具备一级建造师执业资格"
    body = "拟派项目经理不具备壹级建造师执业资格。"
    assert anchor_coverage(text, body, REQ_TYPE_QUALIFICATION) == 0.0
    assert coverage_ratio(text, body, REQ_TYPE_QUALIFICATION) == 0.0


def test_response_synonym_satisfies_numeric_constraint():
    """「2小时响应」写成「1小时内应答」仍算满足响应时限约束。"""

    text = "▲ 中标人须提供2小时响应的售后服务承诺"
    body = "我方保证1小时内应答，并提供保修服务。"
    assert coverage_ratio(text, body, REQ_TYPE_COMMITMENT) >= 0.45
