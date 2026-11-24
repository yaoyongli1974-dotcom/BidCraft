"""BATCH 2 修复回归：原子时间窗、并列交付物、参数数值核对、判定口径与可追溯证据。"""

from __future__ import annotations

import importlib
from datetime import date
from types import SimpleNamespace

import pytest

from bidcraft.parsing.loader import Table
from bidcraft.rules.expr import param_response_missing, param_response_ratio
from bidcraft.schemas import FactItem
from bidcraft.verdict import (
    PARTIAL,
    UNSATISFIED,
    judge,
    resolve_deadline,
    split_atoms,
)


def _req(text: str, marker: str = "★", **extra):
    data = {"id": "R-1", "text": text, "marker": marker, "source_quote": text[:40],
            "response_status": "pending"}
    data.update(extra)
    return SimpleNamespace(**data)


def _nodes(*bodies: str) -> list[dict]:
    return [
        {"id": f"n{i}", "position": i, "heading": f"章{i}", "section_id": f"s{i}", "body": body}
        for i, body in enumerate(bodies, start=1)
    ]


# ---------------------------------------------------------------- P0 时间窗丢失
def test_compound_atom_inherits_time_window_from_full_requirement():
    """「近三年」独立成短片段被丢弃后，业绩原子仍须按整条条款的时间窗核日期。"""

    text = "★近三年，至少3项类似项目业绩，且带宽不低于1000Mbps"
    body = "带宽1200Mbps。2015年完成A项目。2015年完成B项目。2015年完成C项目。"
    verdict = judge(_req(text), _nodes(body), deadline=date(2025, 3, 1))

    assert verdict.state in {UNSATISFIED, PARTIAL}
    atom_windows = verdict.evidence["layer5_detail"]["atom_deadline_windows"]
    achievement_atom = next(
        atom["atom_id"] for atom in verdict.evidence["layer2"]["atoms"]
        if "业绩" in atom["text"]
    )
    detail = atom_windows[achievement_atom]
    assert detail["applicable"] is True
    assert detail["expired"] == 3
    assert detail["valid"] == 0


def test_achievement_context_does_not_borrow_unrelated_type_hint():
    """带宽约束不能把业绩项目类型带成「网络通信」而误转人工复核。"""

    from bidcraft.verdict import _required_project_type

    text = "★近三年，至少3项类似项目业绩，且带宽不低于1000Mbps"
    assert _required_project_type(text) == ""


# ---------------------------------------------------------------- P1 连接词
@pytest.mark.parametrize("connector", ["和", "与", "及"])
def test_connectors_split_self_contained_deliverables(connector):
    text = f"提供营业执照{connector}税务登记证{connector}组织机构代码证"
    atoms = split_atoms(text)
    assert len(atoms) >= 3, atoms

    verdict = judge(_req(text), _nodes("提供营业执照。"))
    assert verdict.state in {PARTIAL, UNSATISFIED}
    assert verdict.state != "satisfied"


def test_shared_modifier_is_not_split():
    atoms = split_atoms("投标人须具有一级资质并保持有效")
    assert len(atoms) == 1
    assert "保持有效" in atoms[0]["text"]


# ---------------------------------------------------------------- P1 参数数值核对
def test_param_response_missing_rejects_smaller_numeric_value():
    param = {"name": "链路带宽", "value": "不低于1000Mbps", "unit": "Mbps"}
    assert param_response_missing([param], "链路带宽为500Mbps") == ["链路带宽"]
    assert param_response_missing([param], "链路带宽为1200Mbps") == []
    assert param_response_ratio([param], "链路带宽为500Mbps") == 0.0
    assert param_response_ratio([param], "链路带宽为1200Mbps") == 1.0


def test_param_response_ratio_supports_relative_tolerance():
    param = {"name": "链路带宽", "value": "不低于1000Mbps", "unit": "Mbps"}
    assert param_response_ratio([param], "链路带宽为995Mbps", 0.01) == 1.0
    # 容差只允许小偏差，不允许把 500Mbps 当 1000Mbps。
    assert param_response_ratio([param], "链路带宽为500Mbps", 0.01) == 0.0


def test_param_response_nonnumeric_keeps_substring_fallback():
    param = {"name": "线缆等级", "value": "Cat6A"}
    assert param_response_missing([param], "采用 Cat6A 线缆") == []
    assert param_response_missing([param], "采用 Cat5e 线缆") == ["线缆等级"]


def test_extract_parameters_sets_registered_unit():
    from bidcraft.parsing.extract import extract_parameters

    table = Table(page=1, rows=[
        ["参数", "要求"],
        ["链路带宽", "不低于1000Mbps"],
    ])
    items = extract_parameters([table], "", [])
    assert items
    assert items[0].unit == "Mbps"


# ---------------------------------------------------------------- P2 判定口径一致
def test_judge_for_project_resolves_deadline_and_matcher(monkeypatch):
    from bidcraft import verdict_llm

    shared = importlib.import_module("bidcraft.api.shared")

    captured = {}

    def fake_judge(requirement, nodes, *, matcher=None, deadline=None):
        captured.update(matcher=matcher, deadline=deadline)
        return SimpleNamespace(state="satisfied", label="满足", reason="", level="", evidence={})

    class FakeDB:
        def get(self, entity, ident):
            return SimpleNamespace(meta={"投标截止日期": "2025-03-01"})

    monkeypatch.setattr(shared, "judge_requirement", fake_judge)
    monkeypatch.setattr(
        shared, "load_ledger",
        lambda db, project_id: SimpleNamespace(facts=[FactItem(key="bid_opening_time", value="2025-03-02")]),
    )
    monkeypatch.setattr(verdict_llm, "build_verdict_matcher", lambda db: "MATCHER")

    shared.judge_for_project(FakeDB(), SimpleNamespace(project_id="p"), [])

    # 事实台账优先于 meta；两种别名都能解析。
    assert captured["deadline"] == date(2025, 3, 2)
    assert captured["matcher"] == "MATCHER"


def test_requirement_payload_uses_project_aware_judge(monkeypatch):
    shared = importlib.import_module("bidcraft.api.shared")

    seen = []

    def fake_judge_for_project(db, requirement, nodes):
        seen.append(nodes)
        return SimpleNamespace(
            state="satisfied", label="满足", reason="ok", level="",
            evidence={"layer1": {}, "layer2": {}, "layer3": {},
                      "layer4": {"constraints": []}, "layer5": {}},
        )

    monkeypatch.setattr(shared, "judge_for_project", fake_judge_for_project)
    req = SimpleNamespace(
        id="r1", project_id="p", text="★ 要求", marker="★", kind="substantive",
        mandatory=True, source_quote="", page=0, clause_no="", response_status="pending",
        response_note="", responded_by=None, responded_at=None, response_verdict="",
        response_verdict_label="", response_verdict_reason="", section_id=None,
        node_id=None, auto_covered=False,
    )
    context = SimpleNamespace(outline_nodes=[], heading_of=lambda node_id: "")
    payload = shared._requirement_payload(object(), req, context=context)

    assert seen == [[]]
    assert payload["verdict"] == "satisfied"


def test_coverage_helper_reuses_deadline_aware_verdict():
    """旧 find_covering_section 无 deadline 会说已覆盖；同口径 helper 不会。"""

    from bidcraft.substantive import coverage_from_verdict, find_covering_section

    text = "★ 近三年不少于3项类似项目业绩"
    nodes = _nodes("已完成6项类似项目业绩。2015年完成A项目。2015年完成B项目。2015年完成C项目。")
    req = _req(text)

    # 旧口径：只看数字/锚点，判为覆盖。
    assert find_covering_section(text, nodes) is not None
    # 新口径：按截止日期核算后业绩全部过期。
    covered, section = coverage_from_verdict(req, nodes, deadline=date(2025, 3, 1))
    assert covered is False
    assert section is None


# ---------------------------------------------------------------- P2 证据可追溯
def test_semantic_evidence_carries_node_identity():
    from bidcraft.substantive import requirement_issues

    req = _req("★ 传输带宽不小于1000Mbps")
    issues = requirement_issues([req], _nodes("链路带宽为500Mbps。"))
    assert issues
    matches = issues[0].evidence["semantic_matches"]
    assert matches
    assert all({"node_id", "section_id", "heading"} <= set(item) for item in matches)
    assert all(item["node_id"] == "n1" for item in matches)
    assert all(isinstance(item["position"], int) for item in matches)


def test_outline_payload_has_body_uses_stripped_text(monkeypatch):
    shared = importlib.import_module("bidcraft.api.shared")

    monkeypatch.setattr(
        shared, "load_outline",
        lambda db, project_id: [
            {"id": "a", "heading": "有正文", "body": "正文内容"},
            {"id": "b", "heading": "空白", "body": "   "},
            {"id": "c", "heading": "无 body"},
        ],
    )
    monkeypatch.setattr(shared.provenance, "node_flags", lambda db, project_id: {})
    rows = shared._outline_payload(object(), "p")
    assert {row["id"]: row["has_body"] for row in rows} == {"a": True, "b": False, "c": False}


def test_pending_chapters_uses_writer_is_pending(monkeypatch):
    """重生成恢复口径与 writer.is_pending 一致：残稿/失败态/字数不足都算待生成。"""

    shared = importlib.import_module("bidcraft.api.shared")

    nodes = [
        {"id": "ok", "body": "完整正文", "status": "written", "words": 100, "target_words": 100},
        {"id": "empty", "body": "   ", "status": "written", "words": 0, "target_words": 100},
        {"id": "failed", "body": "半截正文", "status": "failed", "words": 50, "target_words": 100},
        {"id": "short", "body": "半截正文", "status": "written", "words": 30, "target_words": 100},
    ]
    monkeypatch.setattr(shared, "load_outline", lambda db, project_id: nodes)

    assert shared._pending_chapters(object(), "p") == 3
    assert shared._pending_chapters(object(), "p", force=True) == 4


# ---------------------------------------------------------------- P2 截止日期别名
@pytest.mark.parametrize(
    "key", ["bid_opening_time", "投标截止日期", "deadline", "submission_deadline", "开标日期"]
)
def test_deadline_aliases_resolve_to_date(key):
    assert resolve_deadline([FactItem(key=key, value="2025-03-01")], {}) == date(2025, 3, 1)
    assert resolve_deadline([], {key: "2025年3月1日"}) == date(2025, 3, 1)


def test_deadline_resolution_never_invents_current_time():
    assert resolve_deadline([], {}) is None
    assert resolve_deadline([FactItem(key="deadline", value="无法解析")], {}) is None


# ---------------------------------------------------------------- P2 表格实质性条款
def test_table_star_requirements_are_extracted_and_deduped():
    from bidcraft.parsing.extract import extract_requirements_and_risks
    from bidcraft.rules.loader import load_package

    table = Table(page=1, rows=[
        ["序号", "实质性要求"],
        ["1", "★投标人须提供营业执照"],
        ["2", "▲投标人须提供税务登记证"],
        ["3", "一般说明文字"],
        ["4", "★投标人须提供营业执照"],
    ])
    # 表格内重复行只保留一条。
    text = "正文没有实质性要求"
    reqs, _risks = extract_requirements_and_risks(
        text, load_package("weakcurrent-cabling"), pages=[text], tables=[table]
    )
    marked = [item for item in reqs if item.marker in {"★", "▲"}]
    assert len(marked) == 2, marked
    assert {item.marker for item in marked} == {"★", "▲"}
    assert sum(item.text.endswith("★投标人须提供营业执照") for item in reqs) == 1


# ---------------------------------------------------------------- P3 可追溯字段
def test_constraint_verdict_as_dict_exposes_trace_fields():
    from bidcraft.constraint_check import evaluate_constraints

    verdicts = evaluate_constraints("至少3项类似项目业绩", "完成3项类似项目业绩")
    assert verdicts
    data = verdicts[0].as_dict()
    for key in ("source_span", "subject", "quantity", "unit", "comparator"):
        assert key in data
    assert data["subject"] in {"业绩", "类似项目", "项目"}
    assert data["comparator"] == ">="


def test_layer3_structure_uses_actual_subject_and_source_span():
    verdict = judge(_req("至少3项类似项目业绩"), _nodes("完成3项类似项目业绩。"))
    structures = verdict.evidence["layer3"]["structures"]
    assert structures
    first = structures[0]
    assert first["subject"] not in {"achievement", ""}
    assert first["source_span"]
    assert first["comparator"] == ">="
