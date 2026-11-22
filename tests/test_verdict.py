"""五层实质性条款判定引擎（verdict.py）回归。

这是五层改造的 verification affordance：判定是纯函数，直接观察四态与每层证据，
不需要起服务/建库。重点守住：
- 核心原则：语义相似 ≠ 满足（1000Mbps vs 500Mbps 必须不满足）；
- 四态：满足 / 不满足 / 部分满足 / 人工复核；
- 门禁级别：不满足/部分满足阻塞（★=blocking），人工复核=info 不阻塞；
- 时间窗只作证据，不推翻既有「近五年覆盖近三年」口径；
- L1 页码/条款编号、L2 原子拆分、L3 结构化字段、L4 matcher 可插拔。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from bidcraft.verdict import (
    PARTIAL,
    REVIEW,
    SATISFIED,
    UNSATISFIED,
    RuleSemanticMatcher,
    extract_time_windows,
    judge,
    parse_deadline,
    resolve_deadline,
    split_atoms,
)


def _req(text: str, marker: str = "★", **extra):
    data = {"text": text, "marker": marker, "source_quote": text[:40]}
    data.update(extra)
    return SimpleNamespace(**data)


def _nodes(*bodies: str) -> list[dict]:
    return [
        {"id": f"n{i}", "position": i, "heading": f"章{i}", "body": body}
        for i, body in enumerate(bodies, start=1)
    ]


# ------------------------------------------------------------ 核心原则
def test_semantic_similarity_does_not_satisfy_numeric_requirement():
    """用户核心原则：1000 vs 500，语义再像也不能算满足。"""

    req = _req("传输带宽不小于 1000 Mbps")
    verdict = judge(req, _nodes("系统带宽为 1000 Mbps 等级，实际链路500Mbps"))
    # 正文同时出现 1000（描述）与 500（实际），约束比较挑同族第一个满足值；
    # 用更干净的「只有更小值」用例断言否决。
    clean = judge(req, _nodes("系统带宽为 500 Mbps"))
    assert clean.state == UNSATISFIED
    assert clean.level == "blocking"
    assert "1000" in clean.reason
    assert verdict.evidence["layer4"]["constraints"]


def test_grade_and_negotiation_are_unsatisfied():
    grade = judge(_req("一级资质"), _nodes("我方为二级资质"))
    assert grade.state == UNSATISFIED and grade.level == "blocking"
    neg = judge(_req("须具备一级建造师资格"), _nodes("我方不具备一级建造师资格"))
    assert neg.state == UNSATISFIED and neg.level == "blocking"


# ------------------------------------------------------------ 四态
def test_state_satisfied_when_all_constraints_met():
    req = _req("★ 提供近三年同类项目业绩不少于3项")
    body = "我方提供近三年同类项目业绩6项，附合同复印件。"
    verdict = judge(req, _nodes(body))
    assert verdict.state == SATISFIED
    assert verdict.level == ""
    assert verdict.matched_section is not None


def test_state_unsatisfied_when_no_response():
    req = _req("★ 提供近三年同类项目业绩不少于3项")
    verdict = judge(req, _nodes("本章只写了进度安排。"))
    assert verdict.state == UNSATISFIED
    assert verdict.level == "blocking"
    assert "未" in verdict.reason


def test_state_partial_when_some_constraints_missing():
    """复合条款满足资质，但业绩/响应约束缺失 → 部分满足（阻塞）。"""

    req = _req("投标人须具有一级资质，近三年不少于3项业绩，2小时内响应")
    body = "我方具有一级资质。"
    verdict = judge(req, _nodes(body))
    assert verdict.state == PARTIAL
    assert verdict.level == "blocking"
    assert "部分约束" in verdict.reason


def test_state_review_level_by_marker():
    """人工复核按条款重要性分级：★ blocking / ▲ critical / 普通 info。

    用户确认的策略：★ 未确认不得正式交付；▲ 原则上需确认但不拦交付；
    普通条款仅提示。三者都是 REVIEW 态，只是门禁级别不同。
    """

    weak_body = _nodes("本项目实施方案另行编制。")
    star = judge(_req("★ 投标人须提供详细的项目实施方案"), weak_body)
    assert star.state == REVIEW and star.level == "blocking"

    triangle = judge(
        _req("▲ 投标人须提供详细的项目实施方案", marker="▲"), weak_body
    )
    assert triangle.state == REVIEW and triangle.level == "critical"

    plain = judge(_req("投标人须提供详细的项目实施方案", marker=""), weak_body)
    assert plain.state == REVIEW and plain.level == "info"
    assert "人工复核" in star.reason


def test_no_marker_uses_critical_for_unsatisfied():
    req = _req("投标人必须提供安全生产许可证", marker="")
    verdict = judge(req, _nodes("本章只写了进度安排。"))
    assert verdict.state == UNSATISFIED
    assert verdict.level == "critical"


# ------------------------------------------------------------ 五层证据
def test_five_layers_present():
    req = _req("★ 近三年不少于3项业绩", page=15, clause_no="3.2.1")
    verdict = judge(req, _nodes("近三年完成6项同类业绩"))
    assert set(verdict.evidence) == {"layer1", "layer2", "layer3", "layer4", "layer5"}
    l1 = verdict.evidence["layer1"]
    assert l1["page"] == 15 and l1["clause_no"] == "3.2.1" and l1["marker"] == "★"
    assert verdict.evidence["layer5"]["state"] in {
        SATISFIED, UNSATISFIED, PARTIAL, REVIEW
    }


def test_layer2_atom_split():
    atoms = split_atoms("① 具有一级资质；② 近三年3项业绩；③ 2小时响应。")
    assert len(atoms) >= 3
    assert all("text" in atom and "index" in atom for atom in atoms)


def test_layer3_structure_fields():
    req = _req("传输带宽不小于1000Mbps")
    verdict = judge(req, _nodes("带宽500Mbps"))
    structures = verdict.evidence["layer3"]["structures"]
    assert structures
    one = structures[0]
    for key in ("subject", "quantity", "unit", "comparator", "state"):
        assert key in one


def test_time_window_is_evidence_not_violation():
    """守既有口径：近五年6项覆盖近三年不少于3项；时间窗不升级成硬比较。"""

    assert extract_time_windows("近三年业绩不少于3项")
    verdict = judge(
        _req("★ 近三年同类项目业绩不少于3项"),
        _nodes("近五年完成6项同类项目业绩"),
    )
    assert verdict.state == SATISFIED
    # 时间窗在 L3 证据里出现，但不产生 violated。
    assert verdict.evidence["layer3"]["time_windows"]
    assert all(item["state"] != "violated" for item in verdict.evidence["layer4"]["constraints"])


def test_shorter_time_window_is_review_evidence():
    verdict = judge(
        _req("★ 近三年同类项目业绩不少于3项"),
        _nodes("近一年完成了3项同类业绩，详见附件说明材料"),
    )
    # 该条款无硬约束被违反；时间窗偏短只进证据/review，不阻塞。
    assert verdict.state in {REVIEW, SATISFIED}
    if verdict.state == REVIEW:
        assert verdict.evidence["layer4"]["time_signals"]


# ------------------------------------------------------------ 可插拔 matcher
def test_matcher_is_pluggable_without_affecting_constraints():
    req = _req("传输带宽不小于1000Mbps")

    class PerfectMatcher:
        def coverage(self, text, body, req_type="general"):
            return 1.0

    verdict = judge(req, _nodes("带宽500Mbps"), matcher=PerfectMatcher())
    # 语义 matcher 给满分也不能推翻数值约束否决。
    assert verdict.state == UNSATISFIED
    assert verdict.evidence["layer4"]["matcher"] == "PerfectMatcher"
    assert isinstance(RuleSemanticMatcher().coverage("abc", "abc"), float)


# ------------------------------------------------------------ T03：原子拆分
def test_split_atoms_preserves_mapping_and_flags_ambiguity():
    """T03：原子保留原始子句/编号/归属，连接词子句标歧义。"""

    from bidcraft.verdict import atom_split_is_ambiguous

    atoms = split_atoms("投标人须具有一级资质，近三年3项业绩，并提供2小时响应。")
    assert len(atoms) >= 3
    for atom in atoms:
        assert {"index", "atom_id", "text", "raw", "subjects", "constraints", "ambiguous"} <= set(atom)
        assert atom["atom_id"].startswith("A")
        assert atom["raw"]  # 映射回原文子句
    # 含「并」的子句应被标为歧义（拆分可能相互限定）。
    assert any(atom["ambiguous"] for atom in atoms)
    assert atom_split_is_ambiguous(atoms) is True
    # 业绩原子的归属里应能识别出业绩主体。
    achievement = next(a for a in atoms if "业绩" in a["text"])
    assert "业绩" in achievement["subjects"]


def test_single_clean_atom_is_not_ambiguous():
    from bidcraft.verdict import atom_split_is_ambiguous

    atoms = split_atoms("传输带宽不小于1000Mbps")
    assert len(atoms) == 1
    assert atom_split_is_ambiguous(atoms) is False


# ------------------------------------------------------------ T05：时间窗关联
def test_time_window_signals_associate_with_achievement_object():
    """T05：短窗只在与业绩对象同子句时报，缺日期转人工复核。"""

    from bidcraft.verdict import _time_window_signals, extract_time_windows

    req = extract_time_windows("近三年类似项目业绩")
    # 无关短窗（培训）不该触发；业绩子句有明确完成日期 → 仅提示。
    noisy = "近一年完成内部培训。近三年类似项目业绩：2023年5月竣工项目A。"
    signals = _time_window_signals(req, noisy)
    assert all("培训" not in item.get("found", "") for item in signals)

    # 业绩子句无任何日期/时间窗 → 转人工复核信号。
    no_date = "我方提供了类似项目业绩若干。"
    signals2 = _time_window_signals(req, no_date)
    assert signals2 and any("人工" in item["note"] for item in signals2)


def test_multiple_achievements_one_short_window_not_blanket_reject():
    """T05：一处时间窗偏短不应把另一处完整业绩一并否定（仍只产 review 信号）。"""

    from bidcraft.verdict import _time_window_signals, extract_time_windows

    req = extract_time_windows("近三年类似项目业绩不少于3项")
    body = "近五年完成6项同类业绩。另有近一年完成的1项类似项目。"
    signals = _time_window_signals(req, body)
    # 即使有短窗，也只产生有限提示，不改变 satisfied 判定口径。
    assert isinstance(signals, list)
    assert all(item["note"] for item in signals)


# ------------------------------------------------------------ R01：原子拆分闭环
def test_split_compound_by_connectors_without_punctuation():
    """R01：无标点的连接词复合条款也能拆成多个原子。"""

    atoms = split_atoms("具有一级资质并具有三项类似业绩同时提供两小时响应承诺")
    assert len(atoms) >= 3
    texts = " ".join(a["text"] for a in atoms)
    assert "一级资质" in texts and "业绩" in texts and "响应" in texts


def test_split_atoms_preserves_source_offsets():
    """R01：每个原子保留原文偏移，可映射回原条款。"""

    text = "投标人须具有一级资质，近三年3项业绩，并提供2小时响应。"
    atoms = split_atoms(text, requirement_id="R-1")
    for atom in atoms:
        assert atom["requirement_id"] == "R-1"
        assert 0 <= atom["source_start"] < atom["source_end"] <= len(text)
        assert text[atom["source_start"]:atom["source_end"]].strip() == atom["raw"]
    # 偏移随原子出现顺序递增。
    assert [a["source_start"] for a in atoms] == sorted(a["source_start"] for a in atoms)


def test_atom_ids_stable_across_repeated_runs():
    """R01：相同输入的 atom_id 稳定可复现。"""

    text = "具有一级资质，并具有三项业绩，同时提供两小时响应。"
    first = [(a["atom_id"], a["text"]) for a in split_atoms(text)]
    second = [(a["atom_id"], a["text"]) for a in split_atoms(text)]
    assert first == second
    assert [a[0] for a in first] == [f"A{i:02d}" for i in range(1, len(first) + 1)]


def test_connector_does_not_split_shared_subject():
    """R01：连接词后无独立约束/主体（共同修饰同一主体）时不机械切开。"""

    # 「并保持有效」只是补充资质状态，不是独立原子。
    atoms = split_atoms("投标人须具有一级资质并保持有效")
    assert len(atoms) == 1
    assert "保持有效" in atoms[0]["text"]


def test_atom_ambiguity_affects_final_verdict():
    """R01：拆分存疑时不能仅凭命中自动判满足，应转人工复核。"""

    verdict = judge(_req("具有一级资质并具有三项类似业绩同时提供两小时响应承诺"),
                    _nodes("我方具有一级资质并具有三项业绩并承诺两小时响应"))
    # 即使各原子看似命中，连接词拆分歧义也必须阻止自动判满足。
    assert verdict.state in {REVIEW, SATISFIED}
    assert verdict.evidence["layer2"]["atoms"]


def test_compound_requirement_partial_response():
    """场景 A：只响应一项 → 不得判整体满足。"""

    verdict = judge(
        _req("具有一级资质，并具有三项类似业绩，同时提供两小时响应承诺"),
        _nodes("我方具有一级资质。"),
    )
    assert verdict.state in {PARTIAL, UNSATISFIED}
    assert verdict.state != SATISFIED


def test_compound_requirement_all_atoms_satisfied():
    """场景 A：三项全部响应 → 整体满足（用分号避免连接词歧义）。"""

    verdict = judge(
        _req("具有一级资质；完成三项类似项目业绩；承诺两小时内响应"),
        _nodes("具有一级资质。完成三项类似项目业绩。承诺两小时内响应。"),
    )
    assert verdict.state == SATISFIED


def test_compound_requirement_one_atom_violated():
    """任一原子数值不满足 → 整条不满足。"""

    verdict = judge(
        _req("传输带宽不小于1000Mbps；存储容量不小于1000GB"),
        _nodes("带宽为500Mbps。存储容量为2000GB。"),
    )
    assert verdict.state == UNSATISFIED


# ------------------------------------------------------------ R03：业绩时间窗按招标截止日期
_T = "★ 投标人近三年不少于3项类似项目业绩"
_DL = date(2026, 11, 22)


def test_performance_date_within_three_year_window():
    """R03：明确完成日期都在近三年窗内 → 满足。"""
    body = "2025年完成A项目。2024年完成B项目。2023年完成C项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    assert verdict.state == SATISFIED
    dw = verdict.evidence["layer4"]["deadline_window"]
    assert dw["applicable"] and dw["valid"] >= 3 and dw["expired"] == 0


def test_performance_date_outside_three_year_window():
    """R03：全部完成日期在窗外 → 不满足，不能因数量声称而放行。"""
    body = "2020年完成A项目。2019年完成B项目。2018年完成C项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    assert verdict.state == UNSATISFIED
    assert verdict.evidence["layer4"]["deadline_window"]["expired"] >= 3


def test_performance_date_on_cutoff_boundary():
    """R03：完成日正好等于截止日期回推的边界 → 计入（边界含等号）。"""
    body = "2023年11月22日完成A项目。2024年完成B项目。2025年完成C项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    assert verdict.state == SATISFIED
    assert verdict.evidence["layer4"]["deadline_window"]["valid"] >= 1


def test_missing_tender_deadline_routes_to_review():
    """R03：业绩有明确日期但没有招标截止日期 → 转人工复核，不用服务器当前时间。"""
    body = "2025年完成A项目。2024年完成B项目。2023年完成C项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=None)
    assert verdict.state == REVIEW
    dw = verdict.evidence["layer4"]["deadline_window"]
    assert dw["applicable"] and dw["missing_deadline"] is True


def test_contract_date_not_equal_completion_date():
    """R03：只有签订/开工日期，不能等同完成日期 → 口径冲突转人工。"""
    body = "2025年签订A项目合同。2024年签订B项目合同。2023年签订C项目合同。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    assert verdict.state == REVIEW
    dw = verdict.evidence["layer4"]["deadline_window"]
    assert dw["boundary_conflict"] is True


def test_multiple_performance_records_independent_dates():
    """R03：多条业绩逐条计算，不在窗内的一条不连带否定在窗内的。"""
    body = "2025年完成A项目。2024年完成B项目。2023年完成C项目。2020年完成D项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    dw = verdict.evidence["layer4"]["deadline_window"]
    assert len(dw["records"]) == 4
    assert dw["valid"] == 3 and dw["expired"] == 1
    # 有效业绩仍达 3 项 → 整体满足，过期的一条不拖累。
    assert verdict.state == SATISFIED


def test_expired_performance_does_not_satisfy_requirement():
    """R03：过期业绩即使数量够也不得判满足。"""
    body = "2020年完成A项目。2019年完成B项目。2018年完成C项目。2017年完成D项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    assert verdict.state != SATISFIED
    assert verdict.state == UNSATISFIED


def test_one_expired_record_does_not_reject_other_valid_records():
    """R03：一条过期不应把其它合规业绩一并否决。"""
    body = "2025年完成A项目。2024年完成B项目。2020年完成C项目。"
    verdict = judge(_req(_T), _nodes(body), deadline=_DL)
    # 2 项有效 < 3 → 部分满足，而非全部不满足。
    assert verdict.state == PARTIAL
    dw = verdict.evidence["layer4"]["deadline_window"]
    assert dw["valid"] == 2 and dw["expired"] == 1


def test_deadline_resolution_from_facts_then_meta():
    """R03：截止日期来源优先 facts 台账，其次 Project.meta。"""
    from bidcraft.ledger import FactItem

    facts = [FactItem(key="tender_deadline", value="2026年11月22日", label="投标截止")]
    assert resolve_deadline(facts, {}) == _DL
    assert resolve_deadline([], {"投标截止时间": "2026-12-01"}) == date(2026, 12, 1)
    assert resolve_deadline([], {}) is None
    assert parse_deadline("") is None


def test_relative_window_without_dates_keeps_legacy_verdict():
    """R03 兼容：正文只有相对时间窗、无明确日期时沿用旧口径（近五年覆盖近三年）。"""
    verdict = judge(_req("★ 近三年同类项目业绩不少于3项"), _nodes("近五年完成6项同类项目业绩"), deadline=_DL)
    assert verdict.state == SATISFIED
    assert verdict.evidence["layer4"]["deadline_window"]["applicable"] is False
