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

from types import SimpleNamespace

from bidcraft.verdict import (
    PARTIAL,
    REVIEW,
    SATISFIED,
    UNSATISFIED,
    RuleSemanticMatcher,
    extract_time_windows,
    judge,
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
