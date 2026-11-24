"""Batch 3 generation fixes: ledger budget, coverage evidence, prompt budget,
anti-fabrication flags, pending-chapter recovery, outline verification, stream
replay, outline persistence and scoring dedupe.

All tests here are deterministic: no model endpoint and no network needed.
"""

from __future__ import annotations

import copy

import httpx

from bidcraft.config import settings


def _ledger(**kwargs):
    from bidcraft.schemas import FactLedger

    return FactLedger(package="weakcurrent-cabling", **kwargs)


# ---------------------------------------------------------------- P1 ledger


def test_huge_ledger_keeps_guidance_last_and_fact_keys():
    from bidcraft.ledger import render_ledger
    from bidcraft.schemas import (
        FactItem,
        MaterialItem,
        ParameterItem,
        RequirementItem,
    )

    ledger = _ledger(
        facts=[
            FactItem(key="project.name", label="项目名称", value="某园区项目", status="confirmed"),
            FactItem(key="tech.anchor", label="关键口径", value="唯一取值", status="confirmed"),
        ],
        materials=[
            MaterialItem(name=f"材料{i}", spec="规格" * 20, unit="个", qty=i)
            for i in range(200)
        ],
        parameters=[ParameterItem(name=f"参数{i}", value="取值" * 20) for i in range(150)],
        requirements=[RequirementItem(kind="substantive", text=f"要求{i}" * 20) for i in range(80)],
    )
    text = render_ledger(ledger, max_chars=16000)
    assert len(text) <= 16000
    assert "【写作口径】" in text[-400:]
    assert text.rstrip().endswith("不新增无依据的量化承诺。")
    assert "tech.anchor" in text  # fact keys survive proportional trimming
    assert "project.name" in text
    assert "…（内容已截断）" in text
    # 截断只发生在整行，不能留下半行（每行以完整单位/分隔收尾）。
    material_lines = [line for line in text.splitlines() if line.startswith("- 材料")]
    assert material_lines
    assert all(line.endswith("个") for line in material_lines)


def test_small_ledger_is_unchanged_shape():
    from bidcraft.ledger import render_ledger
    from bidcraft.schemas import FactItem

    ledger = _ledger(facts=[FactItem(key="project.name", value="P", status="confirmed")])
    text = render_ledger(ledger, max_chars=16000)
    assert text.startswith("【项目事实")
    assert text.rstrip().endswith("不新增无依据的量化承诺。")
    assert "…（内容已截断）" not in text


# ------------------------------------------------------- P2 coverage anchors


def _score(name="技术方案", score=30.0, criteria="", hint="技术方案"):
    return {
        "id": "s1",
        "name": name,
        "score": score,
        "criteria": criteria,
        "category": "",
        "section_hint": hint,
    }


def _node(words=800, body="正文", **kwargs):
    data = {
        "id": "n1",
        "heading": "技术方案",
        "intent": "",
        "target_words": 1200,
        "words": words,
        "body": body,
        "review_status": "draft",
    }
    data.update(kwargs)
    return data


def test_criteria_absent_from_body_downgrades_coverage():
    from bidcraft.generation.coverage import (
        STATUS_COVERED,
        STATUS_WEAK,
        calculate_score_coverage,
    )

    score = _score(criteria="方案完整、响应招标要求")
    node = _node(words=800, body="无关内容，" * 400)  # ~800 words, no anchor
    report = calculate_score_coverage([score], [node])
    row = report["rows"][0]
    assert row["status"] in {STATUS_WEAK, "uncovered"}
    assert row["status"] != STATUS_COVERED
    assert row["anchors"] == ["方案完整"]
    assert row["nodes"][0]["anchor_hit"] is False
    assert row["nodes"][0]["evidence"]["paragraph"] is None

    hit_node = _node(words=800, body="无关内容，" * 400 + "\n\n方案完整，逐条响应。")
    covered = calculate_score_coverage([score], [hit_node])["rows"][0]
    assert covered["status"] == STATUS_COVERED
    assert covered["nodes"][0]["anchor_hit"] is True
    assert covered["nodes"][0]["evidence"]["paragraph"] == 1


def test_coverage_has_body_derived_from_seeded_body():
    from bidcraft.generation.coverage import calculate_score_coverage

    node = _node(body="已经有正文内容。", words=8)
    node.pop("has_body", None)  # load_outline does not promise this key
    row = calculate_score_coverage([_score()], [node])["rows"][0]
    assert row["nodes"][0]["has_body"] is True


def test_duplicate_scoring_items_are_merged_with_validation_issue():
    from bidcraft.generation.coverage import (
        calculate_score_coverage,
        validate_scoring_coverage,
    )
    from bidcraft.generation.outline import normalize_scoring

    scores = [
        _score(name="技术方案", score=10),
        _score(name=" 技术方案 ", score=10),
    ]
    merged, issues = normalize_scoring(scores)
    assert len(merged) == 1
    assert merged[0]["score"] == 10
    assert any("重复" in issue for issue in issues)
    assert any("总分" in issue for issue in issues)

    report = calculate_score_coverage(scores, [])
    assert report["total_score"] == 10.0
    assert report["total_items"] == 1
    assert any("重复" in issue for issue in validate_scoring_coverage(report))


# ---------------------------------------------------------- P2 prompt budget


def test_chapter_context_respects_budget_and_keeps_needs():
    from bidcraft.generation.prompts import build_chapter_context

    huge = "占位内容\n" * 4000
    text = build_chapter_context(
        project_name="项目",
        heading="技术方案",
        intent="逐条响应",
        target_words=1200,
        ledger_text=huge,
        outline_text=huge,
        previous_summary=huge,
        package_title="弱电",
        knowledge_text=huge,
        facts_text=huge,
        needs=["project.name", "materials.交换机"],
        context_tokens=8000,
        max_tokens=2000,
    )
    assert len(text) <= 6000  # 8000 - 2000
    assert "本章必须引用的事实字段" in text
    assert "project.name" in text
    assert "materials.交换机" in text
    assert "【全书大纲】" in text
    assert "【写作口径】" not in text  # prompt scaffolding, not ledger guidance


# ------------------------------------------------- P2 suspicious / downgrade


def test_ungrounded_long_number_is_flagged():
    from bidcraft.generation.writer import suspicious_numbers

    ledger = _ledger()
    flagged = suspicious_numbers("本项目编号为 12345678，请核对。", ledger)
    assert "12345678" in flagged


def test_check_project_surfaces_suspicious_number(db):
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section
    from bidcraft.pipeline import check_project, create_project

    with session_scope() as session:
        project = create_project(session, "batch3 检查项目")
        node = OutlineNode(project_id=project.id, heading="技术方案", target_words=1200)
        session.add(node)
        session.flush()
        session.add(
            Section(node_id=node.id, body="项目编号 12345678 未经台账确认。", status="drafted", words=20)
        )
        ref = project.id

    results, _payload = check_project(session, ref)
    assert any(result.code == "GEN-SUSPICIOUS-NUMBER" for result in results)
    issue = next(result for result in results if result.code == "GEN-SUSPICIOUS-NUMBER")
    assert "12345678" in issue.finding


def test_job_support_forwards_kb_downgrade_count(db, monkeypatch):
    from bidcraft.services import job_support

    monkeypatch.setattr(job_support.knowledge, "search", lambda *a, **k: [])
    monkeypatch.setattr(
        job_support.material_policy, "context_block", lambda _db, passages: ("知识块", 3)
    )
    provider = job_support._knowledge_provider(db)
    assert provider("技术方案") == ("知识块", 3)


# --------------------------------------------------- P2 pending / verification


def test_short_failed_chapter_is_pending_and_regenerated(db):
    from bidcraft.db import session_scope
    from bidcraft.generation.outline import load_outline
    from bidcraft.generation.writer import generate_document, is_pending
    from bidcraft.models import OutlineNode, Section
    from bidcraft.pipeline import create_project
    from bidcraft.rules.loader import load_package

    with session_scope() as session:
        project = create_project(session, "batch3 恢复项目")
        node = OutlineNode(project_id=project.id, heading="技术方案", target_words=1200)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="短" * 10, status="failed", words=20))
        ref = project.id

    with session_scope() as session:
        nodes = load_outline(session, ref)
        assert len([node for node in nodes if is_pending(node)]) == 1
        report = generate_document(
            session, ref, _offline_client(), _ledger(), load_package(project.package)
        )
        assert len(report.written) == 1
        section = session.get(Section, nodes[0]["section_id"])
        assert section.body != "短" * 10
        assert len(section.body) > 20


def _offline_client():
    from bidcraft.generation.agent import ModelClient

    local = copy.copy(settings)
    local.offline = True
    local.model_base_url = ""
    local.model_name = ""
    return ModelClient(local)


def test_underlength_chapter_attempts_continuation_and_records_flags():
    from bidcraft.generation.writer import write_chapter
    from bidcraft.rules.loader import Package

    class FakeClient:
        available = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return "技术方案正文。" + "内容" * 50
            return "后续内容。" + "继续" * 20

    client = FakeClient()
    node = {
        "heading": "技术方案",
        "intent": "方案完整",
        "target_words": 1200,
        "needs": [],
    }
    body, source = write_chapter(client, _ledger(), Package(name="t", title="T"), node, [node], "")
    assert source == "model"
    assert client.calls >= 2
    verification = node["_verification"]
    assert verification["underlength"] is True
    assert verification["word_ratio"] < 0.8
    assert 0 <= verification["adherence"] <= 1
    assert len(body) > 100


# -------------------------------------------------------- P2 stream replay


def test_stream_retry_suppresses_replayed_deltas(monkeypatch):
    from bidcraft.generation import agent

    local = copy.copy(settings)
    local.offline = False
    local.model_base_url = "http://localhost:9999/v1"
    local.model_name = "test-model"
    local.model_api_key = ""
    client = agent.ModelClient(local)
    assert client.available

    monkeypatch.setattr(agent.time, "sleep", lambda *_args, **_kwargs: None)
    attempts = {"n": 0}

    def fake_stream(payload, headers, on_delta):
        index = attempts["n"]
        attempts["n"] += 1
        chunks = ["AAAA", "BBBB"]
        for chunk in chunks:
            on_delta(chunk)
        if index == 0:
            raise httpx.HTTPError("stream broke")
        on_delta("CCCC")
        chunks.append("CCCC")
        return "".join(chunks)

    monkeypatch.setattr(client, "_stream", fake_stream)
    emitted: list[str] = []
    result = client.complete(
        [{"role": "user", "content": "hi"}], stream=True, on_delta=emitted.append
    )
    assert result == "AAAABBBBCCCC"
    # 第一次尝试的 AAAA/BBBB 是正常首播；第二次尝试只补发超出已播前缀的部分。
    assert "".join(emitted) == result
    assert emitted == ["AAAA", "BBBB", "CCCC"]  # 重试的前两个块被抑制，没有重播


# --------------------------------------------- P3 outline mode / hierarchy


def test_outline_rebuild_preserves_body_mode_and_parent(db):
    from bidcraft.db import session_scope
    from bidcraft.generation.outline import load_outline, save_outline
    from bidcraft.models import Section
    from bidcraft.pipeline import create_project
    from bidcraft.schemas import OutlineItem, OutlineSpec

    spec = OutlineSpec(
        title="投标文件",
        mode="scoring",
        items=[
            OutlineItem(
                heading="父章",
                intent="总体",
                target_words=1200,
                children=[OutlineItem(heading="子章", intent="细分", target_words=600)],
            )
        ],
    )
    with session_scope() as session:
        project = create_project(session, "batch3 大纲项目")
        ref = project.id
        nodes = save_outline(session, ref, spec)
        parent_id, child_id = nodes[0].id, nodes[1].id
        from sqlalchemy import select

        section = session.scalar(select(Section).where(Section.node_id == parent_id))
        section.body = "已经写好的正文"
        session.commit()

    with session_scope() as session:
        save_outline(session, ref, spec)
        session.commit()

    with session_scope() as session:
        nodes = load_outline(session, ref)
        assert [node["id"] for node in nodes] == [parent_id, child_id]
        assert nodes[0]["body"] == "已经写好的正文"
        assert nodes[0]["parent_id"] is None
        assert nodes[1]["parent_id"] == parent_id
        from bidcraft.models import Project

        assert session.get(Project, ref).meta["outline_mode"] == "scoring"


# ------------------------------------- P3 scoring allocation / conservation


def test_scoring_outline_dedupes_duplicate_names():
    from bidcraft.generation.outline import _scoring_outline
    from bidcraft.rules.loader import load_package
    from bidcraft.schemas import ScoringItemModel

    ledger = _ledger(
        scoring=[
            ScoringItemModel(name="技术方案", score=10, criteria="方案完整"),
            ScoringItemModel(name=" 技术方案 ", score=10, criteria="响应招标要求"),
        ]
    )
    items = _scoring_outline(ledger, load_package("weakcurrent-cabling"), 10000)
    matches = [item for item in items if item.heading == "技术方案"]
    assert len(matches) == 1
    assert "评分为 10 分" in matches[0].intent
