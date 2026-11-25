"""v0.32 阶段6：质量评估引擎的真实证据口径（规划 / 事实 / 审计 / 门禁）。"""

from __future__ import annotations

from sqlalchemy import select

from bidcraft.audit import record_audit
from bidcraft.db import session_scope
from bidcraft.evaluation import (
    DIMENSION_WEIGHTS,
    calculate_chapter_coverage,
    calculate_evidence_coverage,
    calculate_plan_coverage,
    calculate_requirement_coverage,
)
from bidcraft.evaluation.report import build_quality_report
from bidcraft.evaluation.risk import collect_project_risks
from bidcraft.evaluation.scorer import calculate_dimension_scores
from bidcraft.models import (
    ChapterPlan,
    Issue,
    OutlineNode,
    Project,
    QualityFinding,
    QualityReport,
    Section,
)


def _project(db, name: str) -> Project:
    row = Project(name=name, package="weakcurrent-cabling")
    db.add(row)
    db.flush()
    return row


def _chapter(db, project_id: str, heading: str, body: str = "正文内容") -> OutlineNode:
    node = OutlineNode(project_id=project_id, heading=heading, position=len(heading))
    db.add(node)
    db.flush()
    db.add(Section(node_id=node.id, body=body, review_status="draft"))
    db.flush()
    return node


def _plan(db, node: OutlineNode, status: str, version: int = 1) -> ChapterPlan:
    plan = ChapterPlan(
        project_id=node.project_id,
        node_id=node.id,
        version=version,
        status=status,
        objective="覆盖评分点",
        knowledge_refs=[],
    )
    db.add(plan)
    db.flush()
    return plan


def test_plan_coverage_uses_real_confirmed_plans_and_bodies(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-plan-coverage")
        confirmed_node = _chapter(sess, project.id, "已确认章")
        draft_node = _chapter(sess, project.id, "草稿章")
        empty_node = _chapter(sess, project.id, "空章", body="")
        confirmed = _plan(sess, confirmed_node, "CONFIRMED")
        draft = _plan(sess, draft_node, "DRAFT", version=2)
        _plan(sess, empty_node, "CONFIRMED")

        data = calculate_plan_coverage(sess, project.id)
        assert data["total"] == 2  # 空章不进分母
        assert data["covered"] == 1
        assert data["ratio"] == 0.5
        assert data["measured"] is True
        assert data["plans"][0]["plan_id"] == confirmed.id
        assert data["unconfirmed_count"] == 1
        assert data["unconfirmed"][0]["node_id"] == draft_node.id
        assert data["unconfirmed"][0]["plan_id"] == draft.id
        assert data["unconfirmed"][0]["status"] == "DRAFT"

        scoped = calculate_plan_coverage(sess, project.id, nodes=[confirmed_node])
        assert scoped["total"] == 1 and scoped["ratio"] == 1.0

        unmeasured = calculate_plan_coverage(sess, project.id, nodes=[empty_node])
        assert unmeasured["ratio"] is None and unmeasured["measured"] is False


def test_scorer_feeds_plan_without_changing_weights(db) -> None:
    base = {
        "requirement": {"ratio": 0.8},
        "scoring": {"covered_ratio": 0.6},
        "evidence": {"ratio": 0.5},
        "chapter": {"ratio": 0.5},
    }
    no_plan = calculate_dimension_scores(base, {"by_level": {}})
    with_plan = calculate_dimension_scores(
        {**base, "plan": {"ratio": 1.0, "measured": True}}, {"by_level": {}}
    )
    assert [d["weight"] for d in no_plan] == [d["weight"] for d in with_plan]
    assert sum(d["weight"] for d in with_plan) == 100.0
    assert {d["key"] for d in with_plan} == set(DIMENSION_WEIGHTS)
    for key in ("technical", "delivery"):
        before = next(d for d in no_plan if d["key"] == key)["score"]
        after = next(d for d in with_plan if d["key"] == key)["score"]
        assert after > before
    # 零分母未测量时沿用旧口径，不因缺数据凭空给满分。
    legacy = calculate_dimension_scores(
        {**base, "plan": {"ratio": None, "measured": False}}, {"by_level": {}}
    )
    assert legacy == no_plan


def test_zero_denominator_coverage_is_explicitly_unmeasured(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-zero-denominator")
        requirement = calculate_requirement_coverage(sess, project.id)
        evidence = calculate_evidence_coverage(sess, project.id)
        chapter = calculate_chapter_coverage(sess, project.id)
        plan = calculate_plan_coverage(sess, project.id)
        for data in (requirement, evidence, chapter, plan):
            assert data["ratio"] is None
            assert data["measured"] is False


def test_report_does_not_pass_when_export_gate_blocks(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-export-blocker")
        node = _chapter(sess, project.id, "待确认章")
        draft = _plan(sess, node, "DRAFT")

        report = build_quality_report(sess, project.id, created_by="tester")
        assert report.verdict == "warn"  # 章节未确认拦住导出，不能 PASS
        summary = report.risk_summary
        assert summary["export_blocked"] is True
        assert "pending_chapters" in summary["export_blockers"]
        assert report.summary["coverage"]["plan"]["unconfirmed"][0]["plan_id"] == draft.id
        findings = list(
            sess.scalars(
                select(QualityFinding).where(QualityFinding.report_id == report.id)
            )
        )
        plan_finding = next(f for f in findings if f.category == "章节规划")
        assert plan_finding.evidence["plan_id"] == draft.id
        assert plan_finding.evidence["stable_key"] == f"chapter_plan:{draft.id}"


def test_in_progress_substantive_issue_vetoes_via_export_gate_predicate(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-in-progress-blocker")
        issue = Issue(
            project_id=project.id,
            code="SUB-1",
            level="blocking",
            category="实质性响应",
            finding="★ 未确认响应",
            status="in_progress",  # 旧本地口径会漏掉，这里必须复用 issue_is_open
        )
        sess.add(issue)
        sess.flush()

        risks = collect_project_risks(sess, project.id)
        assert any(row.id == issue.id for row in risks["open_issue_rows"])
        assert risks["blocking_substantive"]
        findings = []
        from bidcraft.evaluation.risk import classify_quality_risks

        findings = classify_quality_risks(risks)
        assert any(
            f["evidence"]["stable_key"] == f"issue:{issue.id}" for f in findings
        )

        report = build_quality_report(sess, project.id, created_by="tester")
        assert report.verdict == "blocked"
        assert "实质性响应证据未确认（导出门禁拦截）" in report.risk_summary["hard_risks"]


def test_tool_audit_failures_become_findings_with_audit_key(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-tool-audit")
        failed = record_audit(
            sess,
            project.id,
            "agent.tool.call",
            {"tool": "get_project", "isError": True, "reason": "项目范围不符", "run_id": "r1"},
            actor={"username": "tester"},
        )
        record_audit(
            sess,
            project.id,
            "agent.tool.call",
            {"tool": "get_project", "isError": False, "run_id": "r2"},
            actor={"username": "tester"},
        )
        risks = collect_project_risks(sess, project.id)
        assert risks["tool_failure_count"] == 1
        assert [item["id"] for item in risks["tool_failures"]] == [failed.id]

        report = build_quality_report(sess, project.id, created_by="tester")
        assert report.verdict == "warn"  # 工具失败是 major，不允许 PASS
        findings = list(
            sess.scalars(
                select(QualityFinding).where(QualityFinding.report_id == report.id)
            )
        )
        tool_finding = next(f for f in findings if f.category == "工具调用")
        assert tool_finding.evidence["stable_key"] == f"audit:{failed.id}"
        assert tool_finding.evidence["audit_id"] == failed.id
        assert tool_finding.evidence["detail"]["isError"] is True
        assert report.risk_summary["tool_failure_count"] == 1


def test_quality_report_persists_all_new_evidence_fields(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-report-payload")
        node = _chapter(sess, project.id, "已确认章")
        _plan(sess, node, "USED_FOR_GENERATION")
        report = build_quality_report(sess, project.id, created_by="tester")
        sess.expire_all()
        stored = sess.get(QualityReport, report.id)
        assert stored is not None
        coverage = stored.summary["coverage"]
        assert set(coverage) == {
            "requirement", "scoring", "evidence", "chapter", "plan"
        }
        assert coverage["plan"]["covered"] == 1
        assert coverage["evidence"]["plan_refs"] == 0
        assert isinstance(stored.risk_summary["export_blockers"], list)
        assert all(isinstance(row, dict) for row in coverage["plan"]["plans"])
