"""投标质量评估中心（第四批）：聚合只读、六维评分、硬性风险一票否决、不可变快照。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

pytest.importorskip("fastapi")


@contextmanager
def _session():
    from bidcraft.db import session_scope

    with session_scope() as db:
        yield db


def _project(db, name="evaluation-project"):
    from bidcraft.models import Project

    project = Project(name=name, package="weakcurrent-cabling")
    db.add(project)
    db.flush()
    return project


def _seed_issue(db, project_id, *, level="blocking", status="open", code="X-001"):
    from bidcraft.models import Issue

    row = Issue(
        project_id=project_id,
        code=code,
        level=level,
        category="核对项",
        location="",
        finding=f"测试问题 {code}",
        recommendation="请处置",
        status=status,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- coverage

@pytest.mark.usefixtures("db")
def test_requirement_coverage_four_states():
    from bidcraft.evaluation.coverage import calculate_requirement_coverage
    from bidcraft.models import Requirement

    with _session() as db:
        project = _project(db)
        for verdict, status, marker in [
            ("satisfied", "", "★"),
            ("unsatisfied", "", "★"),
            ("partial", "", "▲"),
            ("review", "", "★"),
            ("satisfied", "responded", "★"),
        ]:
            db.add(
                Requirement(
                    project_id=project.id,
                    kind="substantive",
                    text="要求",
                    source_quote="要求",
                    marker=marker,
                    response_verdict=verdict,
                    response_status=status,
                )
            )
        db.flush()
        data = calculate_requirement_coverage(db, project.id)
    assert data["total"] == 5
    assert data["substantive_total"] == 5
    assert data["star_unconfirmed"] >= 1
    assert 0.0 <= data["ratio"] <= 1.0


@pytest.mark.usefixtures("db")
def test_chapter_coverage_counts_pending():
    from bidcraft.evaluation.coverage import calculate_chapter_coverage
    from bidcraft.models import OutlineNode, Section

    with _session() as db:
        project = _project(db)
        for i, status in enumerate(["confirmed", "draft"]):
            node = OutlineNode(project_id=project.id, heading=f"第{i}章", position=i)
            db.add(node)
            db.flush()
            db.add(
                Section(
                    node_id=node.id,
                    body="正文内容足够长以参与门禁判定" * 5,
                    review_status=status,
                )
            )
        db.flush()
        data = calculate_chapter_coverage(db, project.id)
    assert data["total"] == 2
    assert data["unconfirmed"] == 1
    assert data["ratio"] == 0.5


# ---------------------------------------------------------------- risk

@pytest.mark.usefixtures("db")
def test_risk_summary_hard_veto_on_star_and_blocking():
    from bidcraft.evaluation.risk import calculate_risk_summary, collect_project_risks
    from bidcraft.models import Requirement

    with _session() as db:
        project = _project(db)
        _seed_issue(db, project.id, level="blocking")
        db.add(
            Requirement(
                project_id=project.id,
                kind="substantive",
                text="★ 未确认条款",
                source_quote="★ 未确认条款",
                marker="★",
                response_verdict="review",
                response_status="pending",
            )
        )
        db.flush()
        risks = collect_project_risks(db, project.id)
        summary = calculate_risk_summary(risks)
    assert summary["has_hard_risk"] is True
    assert "★ 实质性条款未确认" in summary["hard_risks"]
    assert "未处置的废标/阻断风险" in summary["hard_risks"]


@pytest.mark.usefixtures("db")
def test_classify_quality_risks_distinguishes_levels():
    from bidcraft.evaluation.risk import classify_quality_risks, collect_project_risks

    with _session() as db:
        project = _project(db)
        _seed_issue(db, project.id, level="major", code="M-1")
        _seed_issue(db, project.id, level="blocking", status="handled", code="B-DONE")
        db.flush()
        findings = classify_quality_risks(collect_project_risks(db, project.id))
    assert any(f["level"] == "major" for f in findings)
    assert not any(f.get("evidence", {}).get("code") == "B-DONE" for f in findings)


# ---------------------------------------------------------------- scorer

def test_dimension_weights_and_hard_veto():
    from bidcraft.evaluation.scorer import (
        DIMENSION_WEIGHTS,
        calculate_dimension_scores,
        calculate_overall_quality,
    )

    assert round(sum(DIMENSION_WEIGHTS.values()), 2) == 100.0
    coverage = {
        "requirement": {"ratio": 1.0},
        "scoring": {"covered_ratio": 1.0},
        "evidence": {"ratio": 1.0},
        "chapter": {"ratio": 1.0},
    }
    dims = calculate_dimension_scores(coverage, {"by_level": {}})
    assert len(dims) == 6
    full = calculate_overall_quality(dims, {"has_hard_risk": False, "by_level": {}})
    assert full["verdict"] == "pass"
    assert full["overall_score"] > 90

    veto = calculate_overall_quality(dims, {"has_hard_risk": True, "by_level": {"blocking": 1}})
    assert veto["verdict"] == "blocked"
    assert veto["hard_risk_veto"] is True
    assert veto["overall_score"] == full["overall_score"]


def test_custom_weights_respected():
    from bidcraft.evaluation.scorer import calculate_dimension_scores

    dims = calculate_dimension_scores({}, {"by_level": {}}, weights={"substantive": 50.0})
    sub = next(d for d in dims if d["key"] == "substantive")
    assert sub["weight"] == 50.0


# ---------------------------------------------------------------- report

@pytest.mark.usefixtures("db")
def test_build_quality_report_creates_immutable_snapshot():
    from bidcraft.evaluation.report import build_quality_report, list_reports, report_payload

    with _session() as db:
        project = _project(db)
        _seed_issue(db, project.id, level="major", code="M-1")
        db.flush()
        r1 = build_quality_report(db, project.id, created_by="owner-1", note="第一次")
        r2 = build_quality_report(db, project.id, created_by="owner-1", note="第二次")
        assert r1.report_version == 1
        assert r2.report_version == 2
        assert r1.id != r2.id
        assert r1.summary["note"] == "第一次"
        assert r2.summary["note"] == "第二次"
        payload = report_payload(r2, findings=[])
        reports = list_reports(db, project.id)
    assert payload["report_version"] == 2
    assert len(reports) == 2


@pytest.mark.usefixtures("db")
def test_report_contains_hard_risk_finding():
    from sqlalchemy import select

    from bidcraft.evaluation.report import build_quality_report, finding_payload
    from bidcraft.models import QualityFinding, Requirement

    with _session() as db:
        project = _project(db)
        _seed_issue(db, project.id, level="blocking")
        db.add(
            Requirement(
                project_id=project.id,
                kind="substantive",
                text="★ 未确认",
                source_quote="★ 未确认",
                marker="★",
                response_verdict="review",
                response_status="pending",
            )
        )
        db.flush()
        report = build_quality_report(db, project.id, created_by="owner")
        findings = list(
            db.scalars(select(QualityFinding).where(QualityFinding.report_id == report.id))
        )
        verdict = report.verdict
        finding0 = finding_payload(findings[0]) if findings else None
    assert verdict == "blocked"
    assert any(f.category == "实质性条款" and f.level == "blocking" for f in findings)
    assert finding0["status"] == "open"
