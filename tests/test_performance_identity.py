"""R19：业绩证据记录与项目身份/来源关联增强。

核心：有效业绩数量必须基于独立、可追溯的项目记录，不能简单统计日期数量；
来源拿不到时必须显式标记缺失，不伪造页码/文件 ID。
"""

from __future__ import annotations

from bidcraft.verdict import (
    _extract_performance_evidence,
    judge,
)

_T = "★ 近三年不少于3项类似项目业绩"
_DL = __import__("datetime").date(2026, 11, 22)


def _req(text: str, marker: str = "★"):
    from types import SimpleNamespace

    return SimpleNamespace(text=text, marker=marker, source_quote=text[:40])


def _nodes(body: str):
    return [{"id": "n1", "heading": "技术方案", "body": body, "position": 1}]


def test_same_project_full_name_and_short_name():
    """同一项目全称与简称 + 相同合同编号 → 合并为一条，不重复计数。"""
    recs = _extract_performance_evidence(
        "2025年完成甲方弱电智能化系统工程项目，合同编号HT-001。"
        "2025年完成弱电智能化项目，合同编号HT-001。",
        _T,
    )
    assert len(recs) == 1
    assert recs[0].contract_number == "HT-001"
    assert recs[0].identity_status == "certain"


def test_different_projects_with_similar_names():
    """不同合同编号的相似名称项目 → 各自独立，不合并。"""
    recs = _extract_performance_evidence(
        "2025年完成A项目，合同编号HT-001。2024年完成A项目，合同编号HT-002。", _T)
    assert len(recs) == 2
    assert {r.contract_number for r in recs} == {"HT-001", "HT-002"}


def test_multiple_projects_in_one_clause():
    """一个子句里多个项目 + 多个日期 → 各自成记录，日期按就近归属。"""
    recs = _extract_performance_evidence(
        "2025年完成A项目；2024年完成B项目；2023年12月完成C项目。", _T)
    assert len(recs) == 3
    assert {r.project_name for r in recs} == {"A", "B", "C"}


def test_multiple_dates_link_to_correct_project():
    """每个日期关联到正确的项目身份，不张冠李戴。"""
    recs = _extract_performance_evidence(
        "A项目于2025年签订，A项目于2025年完成。B项目于2024年完成。", _T)
    by_name = {r.project_name: r for r in recs}
    assert by_name["A"].contract_date == "2025"
    assert by_name["A"].completion_date == "2025"
    assert by_name["B"].completion_date == "2024"


def test_contract_number_identifies_same_project():
    """合同编号是同一项目的强身份信号。"""
    recs = _extract_performance_evidence(
        "2025年签订A项目合同，合同编号HT-9。2024年A项目验收，合同编号HT-9。", _T)
    assert len(recs) == 1
    assert recs[0].contract_date and recs[0].acceptance_date


def test_performance_evidence_has_real_source_reference():
    """每条业绩保留原文片段（真实来源），不凭空捏造页码。"""
    recs = _extract_performance_evidence("2025年完成A项目，合同编号HT-001。", _T)
    rec = recs[0]
    assert rec.source_quote and "A项目" in rec.source_quote
    d = rec.as_dict()
    assert {"source_quote", "source_reference_status", "contract_number"} <= set(d)


def test_missing_source_reference_is_explicit():
    """文本输入无真实页码/文件 ID → 显式标记缺失，不伪造。"""
    recs = _extract_performance_evidence("2025年完成A项目。", _T)
    rec = recs[0]
    assert rec.source_reference_status == "missing"
    assert rec.source_page == 0
    assert rec.evidence_file_id == ""


def test_unreliable_project_identity_requires_review():
    """同一子句出现多个冲突合同编号 → 身份不可靠 → 转人工复核。"""
    body = "2025年完成A项目，合同编号HT-1，合同编号HT-2。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    assert verdict.state == "review"


def test_duplicate_project_does_not_inflate_count():
    """同项目多日期 + 同合同编号 → 只算一项有效业绩。"""
    body = (
        "2025年完成A项目，合同编号HT-X。2025年验收A项目，合同编号HT-X。"
        "2025年完成A项目，合同编号HT-X。"
    )
    recs = _extract_performance_evidence(body, _T)
    assert len(recs) == 1
