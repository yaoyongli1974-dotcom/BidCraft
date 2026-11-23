"""质量评估 Phase 3：商务完整性联动 + 异步评估任务。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")


def _session():
    from bidcraft.db import session_scope

    return session_scope()


def _project(db):
    from bidcraft.models import Project

    project = Project(name="phase3-eval", package="weakcurrent-cabling")
    db.add(project)
    db.flush()
    return project


def _commercial_doc(db, project_id, *, validation=None, sections=None):
    from bidcraft.models import CommercialDoc

    doc = CommercialDoc(
        project_id=project_id,
        name="商务标",
        sections=sections or {},
        validation=validation or {},
        status="draft",
    )
    db.add(doc)
    db.flush()
    return doc


@pytest.mark.usefixtures("db")
def test_completeness_none_without_commercial_doc():
    from bidcraft.evaluation.commercial import collect_commercial_completeness

    with _session() as db:
        project = _project(db)
        assert collect_commercial_completeness(db, project.id) is None


@pytest.mark.usefixtures("db")
def test_completeness_reads_stored_validation():
    from bidcraft.evaluation.commercial import collect_commercial_completeness

    missing = [{"section": "quote", "field": "approved_version", "label": "已审批报价版本"}]
    with _session() as db:
        project = _project(db)
        _commercial_doc(
            db,
            project.id,
            validation={
                "completeness": {"ok": False, "missing": missing, "checked": 16},
                "quote": {},
            },
        )
        info = collect_commercial_completeness(db, project.id)
    assert info is not None
    assert info["ok"] is False
    assert info["checked"] == 16
    assert info["missing"] == missing
    assert info["ratio"] == round(15 / 16, 4)
    assert info["source"] == "commercial_doc"


@pytest.mark.usefixtures("db")
def test_completeness_recomputes_legacy_doc():
    from bidcraft.evaluation.commercial import collect_commercial_completeness

    with _session() as db:
        project = _project(db)
        _commercial_doc(db, project.id, validation={"quote": {}})
        info = collect_commercial_completeness(db, project.id)
    assert info is not None
    assert info["source"] == "recomputed"
    assert info["ok"] is False
    assert any(item["field"] == "approved_version" for item in info["missing"])


def test_scorer_uses_real_commercial_ratio():
    from bidcraft.evaluation.scorer import calculate_dimension_scores

    coverage = {
        "requirement": {"ratio": 1.0},
        "scoring": {"covered_ratio": 1.0},
        "evidence": {"ratio": 1.0},
        "chapter": {"ratio": 1.0},
    }
    proxy = calculate_dimension_scores(coverage, {"by_level": {"major": 3}})
    real = calculate_dimension_scores(
        coverage, {"by_level": {"major": 3}}, commercial_info={"ratio": 0.5}
    )
    proxy_commercial = next(d for d in proxy if d["key"] == "commercial")["score"]
    real_commercial = next(d for d in real if d["key"] == "commercial")["score"]
    assert proxy_commercial == 70.0
    assert real_commercial == 50.0


@pytest.mark.usefixtures("db")
def test_report_links_commercial_completeness():
    from bidcraft.evaluation.report import build_quality_report
    from bidcraft.models import QualityFinding

    with _session() as db:
        project = _project(db)
        _commercial_doc(
            db,
            project.id,
            validation={
                "completeness": {
                    "ok": False,
                    "missing": [{"section": "quote", "field": "approved_version", "label": "已审批报价版本"}],
                    "checked": 16,
                }
            },
        )
        report = build_quality_report(db, project.id, created_by="tester")
        commercial_dim = next(d for d in report.dimensions if d["key"] == "commercial")
        assert report.summary["commercial"]["ok"] is False
        assert report.summary["commercial"]["doc_status"] == "draft"
        assert commercial_dim["score"] == round(15 / 16 * 100, 2)
        assert report.risk_summary["commercial_incomplete"] is True
        assert report.verdict == "warn"
        findings = db.scalars(
            select(QualityFinding).where(QualityFinding.report_id == report.id)
        ).all()
        assert any(f.category == "商务完整性" for f in findings)


@pytest.mark.usefixtures("db")
def test_report_without_commercial_keeps_proxy():
    from bidcraft.evaluation.report import build_quality_report
    from bidcraft.models import QualityFinding

    with _session() as db:
        project = _project(db)
        report = build_quality_report(db, project.id)
        findings = db.scalars(
            select(QualityFinding).where(QualityFinding.report_id == report.id)
        ).all()
    assert "commercial" not in report.summary
    assert "commercial_incomplete" not in report.risk_summary
    assert not any(f.category == "商务完整性" for f in findings)


def test_jobqueue_evaluation_runner_registration():
    from bidcraft import jobqueue
    from bidcraft.models import Job

    assert jobqueue._API_RUNNER_NAMES["evaluation"] == "_run_evaluation_job"
    job = Job(
        project_id="p1",
        kind="evaluation",
        status="queued",
        payload={"note": "例行", "created_by": "tester"},
    )
    assert jobqueue._rebuild_args(job) == (job.id, "p1", "例行", "tester")


@pytest.mark.usefixtures("db")
def test_evaluation_worker_creates_report_and_finishes_job():
    from bidcraft import jobqueue
    from bidcraft.models import Job, QualityReport
    from bidcraft.workers.evaluation import _run_evaluation_job

    with _session() as db:
        project = _project(db)
        job = Job(
            project_id=project.id,
            kind="evaluation",
            status="queued",
            payload={"note": "后台评估", "created_by": "tester"},
        )
        db.add(job)
        db.flush()
        job_id, project_id = job.id, project.id

    claimed = jobqueue.claim_next()
    assert claimed is not None and claimed.id == job_id
    with jobqueue.claim_context(claimed.claim_token or ""):
        _run_evaluation_job(job_id, project_id, "后台评估", "tester")

    with _session() as db:
        job = db.get(Job, job_id)
        assert job.status == "done"
        assert job.progress == 100
        assert job.result["report_version"] == 1
        reports = db.scalars(
            select(QualityReport).where(QualityReport.project_id == project_id)
        ).all()
        assert len(reports) == 1
        assert reports[0].summary["note"] == "后台评估"


def _login():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.mark.usefixtures("db")
def test_async_evaluation_endpoint():
    client = _login()
    project_id = client.post(
        "/projects", json={"name": "异步评估", "package": "weakcurrent-cabling"}
    ).json()["id"]
    response = client.post(
        f"/projects/{project_id}/evaluation/run-async", json={"note": "异步说明"}
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["kind"] == "evaluation"
    assert job["project_id"] == project_id
    assert client.get(f"/projects/{project_id}/evaluation").status_code == 200
