"""BATCH 7: evaluation IDOR / status validation / async-retry idempotency."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bidcraft import jobqueue  # noqa: E402
from bidcraft.api import create_app  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    Issue,
    Job,
    QualityFinding,
    QualityReport,
)
from bidcraft.pipeline import create_project  # noqa: E402
from bidcraft.workers import evaluation as evaluation_worker  # noqa: E402

ADMIN_PASS = "test-admin"
USER_PASS = "b7-eval-pass"


def _client(username: str = "admin", password: str = ADMIN_PASS):
    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _create_user(admin, username: str):
    resp = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": USER_PASS,
            "role": "manager",
        },
    )
    assert resp.status_code == 201, resp.text
    return _client(username, USER_PASS)


def _project(client, name: str) -> str:
    resp = client.post("/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _finding_in_project(admin, project_id: str, marker: str) -> str:
    """Force a finding by inserting a blocking issue before the report."""

    with session_scope() as db:
        db.add(
            Issue(
                project_id=project_id,
                code=f"B7-{marker}",
                level="blocking",
                category="核对项",
                location="",
                finding="BATCH 7 测试阻断",
                status="open",
            )
        )
    resp = admin.post(f"/projects/{project_id}/evaluation/run", json={"note": marker})
    assert resp.status_code == 201, resp.text
    findings = admin.get(f"/projects/{project_id}/evaluation/reports/{resp.json()['id']}/findings")
    assert findings.status_code == 200 and findings.json(), findings.text
    return findings.json()[0]["id"]


@pytest.mark.usefixtures("db")
def test_cross_project_finding_disposition_is_rejected():
    admin = _client()
    attacker = _create_user(admin, "b7-finding-attacker")
    project_a = _project(attacker, "b7-A")
    project_b = _project(admin, "b7-B")
    finding_id = _finding_in_project(admin, project_b, "IDOR")

    # The attacker has write access to A (owner) but none to B: patching B's
    # finding through A's ref must not be allowed and must not change the row.
    resp = attacker.patch(
        f"/projects/{project_a}/evaluation/findings/{finding_id}",
        json={"status": "handled", "note": "cross-project attempt"},
    )
    assert resp.status_code == 404, resp.text
    with session_scope() as sess:
        row = sess.get(QualityFinding, finding_id)
        assert row.status == "open"
        assert "cross-project attempt" not in str(row.evidence or {})
        leaked = list(
            sess.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == project_a,
                    AuditLog.action.in_(["quality.finding_update", "quality.finding.http"]),
                )
            )
        )
        assert leaked == []

    # Legitimate disposition works and the service audit is project-scoped.
    ok = admin.patch(
        f"/projects/{project_b}/evaluation/findings/{finding_id}",
        json={"status": "handled", "note": "已复核"},
    )
    assert ok.status_code == 200, ok.text
    with session_scope() as sess:
        assert sess.get(QualityFinding, finding_id).status == "handled"
        audit = sess.scalar(
            select(AuditLog).where(
                AuditLog.project_id == project_b,
                AuditLog.action == "quality.finding_update",
            )
        )
        assert audit is not None
        assert audit.detail["finding_id"] == finding_id


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("status", ["bogus", "resolved", "in_progress", "", None])
def test_invalid_finding_status_is_rejected(status):
    admin = _client()
    project_id = _project(admin, "b7-status")
    finding_id = _finding_in_project(admin, project_id, "STATUS")
    payload = {"note": "x", "status": status}
    resp = admin.patch(
        f"/projects/{project_id}/evaluation/findings/{finding_id}", json=payload
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.usefixtures("db")
def test_async_evaluation_retry_creates_exactly_one_report(monkeypatch):
    with session_scope() as sess:
        project = create_project(sess, "b7-eval-retry")
        job = Job(
            project_id=project.id,
            kind="evaluation",
            status="queued",
            payload={"note": "retry"},
        )
        sess.add(job)
        sess.flush()
        job_id, project_id = job.id, project.id

    claimed = jobqueue.claim_next()
    assert claimed is not None and claimed.id == job_id
    with jobqueue.claim_context(claimed.claim_token or ""):
        original_done = evaluation_worker._done

        def _crash(*args, **kwargs):
            raise RuntimeError("simulated crash after report commit")

        # The report + job.result commit before _done; simulate a crash there.
        # The runner surfaces it as a failed Job (it never kills the worker).
        monkeypatch.setattr(evaluation_worker, "_done", _crash)
        evaluation_worker._run_evaluation_job(job_id, project_id, "retry", "tester")
        monkeypatch.setattr(evaluation_worker, "_done", original_done)
        with session_scope() as sess:
            assert sess.get(Job, job_id).status == "failed"

        # Requeue/replay the same Job: it must finish the existing report.
        evaluation_worker._run_evaluation_job(job_id, project_id, "retry", "tester")

    with session_scope() as sess:
        reports = list(
            sess.scalars(
                select(QualityReport).where(QualityReport.project_id == project_id)
            )
        )
        assert len(reports) == 1
        job = sess.get(Job, job_id)
        assert job.status == "done"
        assert job.result["report_id"] == reports[0].id
        assert (job.payload or {}).get("report_id") == reports[0].id
