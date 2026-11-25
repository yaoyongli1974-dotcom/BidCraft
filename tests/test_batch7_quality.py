"""BATCH 7 regressions: finding 归属/状态校验、评估任务重试幂等、跨项目审批工作台。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bidcraft import workers  # noqa: E402,F401  ensure worker registration
from bidcraft.api import create_app  # noqa: E402
from bidcraft.config import settings  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.evaluation.report import update_finding  # noqa: E402
from bidcraft.jobqueue import claim_context, claim_next  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    Issue,
    Job,
    Project,
    QualityFinding,
    QualityReport,
)
from bidcraft.orchestration.approval import request_approval  # noqa: E402
from bidcraft.orchestration.orchestrator import start_workflow  # noqa: E402

ADMIN = ("admin", "test-admin")
PASSWORD = "batch7-quality-pass"


def _client(monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(settings, "embedded_workers", False)
    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert resp.status_code == 200, resp.text
    return client


def _project(client, name: str) -> str:
    resp = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _finding_in(client, project_id: str) -> tuple[str, str]:
    """生成一份包含 finding 的报告，返回 (finding_id, report_id)。"""

    with session_scope() as db:
        db.add(
            Issue(
                project_id=project_id,
                code="B7-FINDING",
                level="blocking",
                category="核对项",
                location="",
                finding="测试阻断项",
                status="open",
            )
        )
    report = client.post(f"/projects/{project_id}/evaluation/run", json={})
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]
    with session_scope() as db:
        finding = db.scalar(
            select(QualityFinding).where(QualityFinding.report_id == report_id)
        )
    assert finding is not None
    return finding.id, report_id


# --------------------------------------------------------------------------- #
# Gap 1 + 8: finding disposition IDOR / status allowlist / audit project_id
# --------------------------------------------------------------------------- #
def test_finding_disposition_cross_project_is_404(db, monkeypatch):
    client = _client(monkeypatch)
    source = _project(client, "b7-finding-source")
    other = _project(client, "b7-finding-other")
    finding_id, report_id = _finding_in(client, source)

    resp = client.patch(
        f"/projects/{other}/evaluation/findings/{finding_id}",
        json={"status": "handled", "note": "跨项目篡改"},
    )
    assert resp.status_code == 404, resp.text

    with session_scope() as sess:
        finding = sess.get(QualityFinding, finding_id)
        assert finding.status == "open"
        assert "resolution_note" not in (finding.evidence or {})
        # 跨项目 PATCH 不应写入指向其它项目的处置审计。
        audit = sess.scalar(
            select(AuditLog).where(
                AuditLog.action == "quality.finding_update",
                AuditLog.project_id == other,
            )
        )
        assert audit is None

    # Service 层同样按归属项目收口（防御纵深）。
    with session_scope() as sess:
        assert update_finding(
            sess, finding_id, "handled", "x", "system", project_id=other
        ) is None
    with session_scope() as sess:
        assert sess.get(QualityFinding, finding_id).status == "open"

    # 合法的本处置依旧成功，审计落在报告的真实项目上。
    resp = client.patch(
        f"/projects/{source}/evaluation/findings/{finding_id}",
        json={"status": "ignored", "note": "已复核"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ignored"
    with session_scope() as sess:
        audit = sess.scalar(
            select(AuditLog).where(
                AuditLog.action == "quality.finding_update",
                AuditLog.project_id == source,
            )
        )
        assert audit is not None
        assert audit.detail["report_id"] == report_id


@pytest.mark.parametrize("bad", ["bogus", "", "DELETED", 123, None])
def test_finding_status_allowlist_returns_422(db, monkeypatch, bad):
    client = _client(monkeypatch)
    project_id = _project(client, "b7-finding-status")
    finding_id, _ = _finding_in(client, project_id)
    resp = client.patch(
        f"/projects/{project_id}/evaluation/findings/{finding_id}",
        json={"status": bad},
    )
    assert resp.status_code == 422, resp.text


def test_re_evaluation_carries_prior_finding_disposition(db, monkeypatch):
    client = _client(monkeypatch)
    project_id = _project(client, "b7-disposition-carry")
    finding_id, first_report_id = _finding_in(client, project_id)
    patched = client.patch(
        f"/projects/{project_id}/evaluation/findings/{finding_id}",
        json={"status": "handled", "note": "已在正文补充承诺"},
    )
    assert patched.status_code == 200, patched.text

    second = client.post(f"/projects/{project_id}/evaluation/run", json={})
    assert second.status_code == 201, second.text
    second_report_id = second.json()["id"]
    assert second_report_id != first_report_id

    with session_scope() as sess:
        original = sess.get(QualityFinding, finding_id)
        assert original.status == "handled"
        new = sess.scalar(
            select(QualityFinding).where(
                QualityFinding.report_id == second_report_id,
                QualityFinding.title.contains("B7-FINDING"),
            )
        )
        assert new is not None
        assert new.id != finding_id  # 新报告仍是独立可审计的新行
        assert new.report_id == second_report_id
        assert new.status == "handled"
        assert new.evidence["resolution_note"] == "已在正文补充承诺"
        assert new.evidence["resolved_by"] == "admin"
        assert new.evidence["disposition_from_finding_id"] == finding_id


# --------------------------------------------------------------------------- #
# Gap 6: evaluation Job retry must not create a second QualityReport
# --------------------------------------------------------------------------- #
def test_evaluation_job_retry_reuses_report(db, monkeypatch):
    from bidcraft.workers import evaluation as evaluation_worker

    project_id = _project(_client(), "b7-eval-retry")
    call_count = {"done": 0}
    original_done = evaluation_worker._done

    def flaky_done(job_id, result, message):
        call_count["done"] += 1
        if call_count["done"] == 1:
            # 模拟报告已与 Job.result 同事务提交、但 Job 尚未标记完成的崩溃。
            raise RuntimeError("simulated crash before job done")
        return original_done(job_id, result, message)

    monkeypatch.setattr(evaluation_worker, "_done", flaky_done)

    with session_scope() as sess:
        job = Job(
            project_id=project_id,
            kind="evaluation",
            # 真实 worker 只处理 running；首轮必须处于已认领状态才会生成报告。
            status="running",
            payload={"owner_id": "u-admin", "note": "", "created_by": "admin"},
        )
        sess.add(job)
        sess.flush()
        job.claim_token = "b7-claim"
        job_id = job.id

    with claim_context("b7-claim"):
        # 第一次：报告已落库，_done 崩溃由 runner 自己记失败。
        evaluation_worker._run_evaluation_job(job_id, project_id, note="", created_by="admin")

    # _fail_job 清除 fence token；真实重试必须重新认领，不能在旧 token 下直接重跑。
    with session_scope() as sess:
        requeued = sess.get(Job, job_id)
        requeued.status = "queued"
        requeued.attempts = 0
        sess.flush()
    claimed = claim_next()
    assert claimed is not None and claimed.id == job_id
    with claim_context(claimed.claim_token or ""):
        # 第二次：复用既有报告，只补写 Job.result/done。
        evaluation_worker._run_evaluation_job(job_id, project_id, note="", created_by="admin")

    with session_scope() as sess:
        reports = list(
            sess.scalars(
                select(QualityReport).where(QualityReport.project_id == project_id)
            )
        )
        assert len(reports) == 1, [r.report_version for r in reports]
        report = reports[0]
        job = sess.get(Job, job_id)
        assert job.payload["report_id"] == report.id
        assert job.result["report_id"] == report.id
        assert job.status == "done"


# --------------------------------------------------------------------------- #
# Gap 9: cross-project workflow-approval workbench (UI deferred by design)
# --------------------------------------------------------------------------- #
def test_cross_project_workflow_approval_workbench(db, monkeypatch):
    client = _client(monkeypatch)
    first = _project(client, "b7-workbench-a")
    second = _project(client, "b7-workbench-b")

    created = []
    for pid in (first, second):
        with session_scope() as sess:
            run = start_workflow(
                sess,
                sess.get(Project, pid),
                workflow_type="standard",
                goal=f"b7 goal {pid[:4]}",
            )
            approval = request_approval(
                sess, run, task=None, approval_type="delivery", submitted_by="requester"
            )
            created.append((pid, run.id, approval.id))

    resp = client.get("/approvals/workflows/pending")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    by_id = {item["id"]: item for item in items}
    for pid, run_id, approval_id in created:
        assert approval_id in by_id
        assert by_id[approval_id]["project_id"] == pid
        assert by_id[approval_id]["run_id"] == run_id
        assert "project_name" in by_id[approval_id]

    # 非成员看不到任何待审项（不泄露其它项目是否存在）。
    resp = client.post(
        "/admin/users",
        json={
            "username": "b7-outsider",
            "display_name": "outsider",
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert resp.status_code == 201, resp.text
    outsider = TestClient(create_app())
    assert (
        outsider.post(
            "/auth/login",
            json={"username": "b7-outsider", "password": PASSWORD},
        ).status_code
        == 200
    )
    assert outsider.get("/approvals/workflows/pending").json() == []


def test_update_finding_stamps_resolved_at_and_clears_on_reopen(db, monkeypatch):
    client = _client(monkeypatch)
    project_id = _project(client, "b7-resolved-at")
    finding_id, report_id = _finding_in(client, project_id)

    with session_scope() as sess:
        row = update_finding(
            sess, finding_id, "handled", "已复核", "owner", project_id=project_id
        )
        assert row is not None
        assert row.status == "handled"
        assert row.evidence["resolved_by"] == "owner"
        assert row.evidence["resolved_at"].endswith("+08:00")

    with session_scope() as sess:
        row = update_finding(
            sess, finding_id, "resolved", "结案", "owner", project_id=project_id
        )
        assert row is not None and row.status == "resolved"
        assert row.evidence["resolution_note"] == "结案"

    with session_scope() as sess:
        row = update_finding(
            sess, finding_id, "open", "", "owner", project_id=project_id
        )
        assert row is not None and row.status == "open"
        for field in ("resolution_note", "resolved_by", "resolved_at", "disposition_from_finding_id"):
            assert field not in row.evidence
        audits = [
            row
            for row in sess.scalars(
                select(AuditLog).where(
                    AuditLog.action == "quality.finding_update",
                    AuditLog.project_id == project_id,
                )
            )
            if (row.detail or {}).get("report_id") == report_id
        ]
        assert len(audits) == 3
        assert audits[-1].detail["reopened"] is True
