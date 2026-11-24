"""Fix3：global_metrics 的 open_findings 口径与 db=None 的 CLI 安全路径。"""

from __future__ import annotations

from sqlalchemy import func, select

from bidcraft.db import session_scope
from bidcraft.health import OPEN_FINDING_STATUSES, global_metrics
from bidcraft.models import Job, Project, QualityFinding, QualityReport, User


def _seed_report(session, status="intake"):
    project = Project(name="健康指标用例", status=status)
    session.add(project)
    session.flush()
    report = QualityReport(project_id=project.id)
    session.add(report)
    session.flush()
    return report


def test_open_findings_honors_open_finding_statuses(db):
    """只统计 OPEN_FINDING_STATUSES，处置后的 finding 不再计入待办。"""
    with session_scope() as session:
        report = _seed_report(session)
        for status in OPEN_FINDING_STATUSES:
            session.add(QualityFinding(report_id=report.id, title=status, status=status))
        session.add(QualityFinding(report_id=report.id, title="handled", status="handled"))
        session.add(QualityFinding(report_id=report.id, title="ignored", status="ignored"))
        session.flush()

        metrics = global_metrics(session)
        assert set(metrics) == {
            "projects_by_status",
            "jobs_by_status",
            "projects",
            "jobs",
            "open_findings",
            "users",
        }
        assert metrics["open_findings"] == len(OPEN_FINDING_STATUSES)

        # 把未处置的 finding 全部标记 handled 后，待办数归零。
        for finding in session.scalars(
            select(QualityFinding).where(QualityFinding.status.in_(OPEN_FINDING_STATUSES))
        ):
            finding.status = "handled"
        session.flush()
        assert global_metrics(session)["open_findings"] == 0


def test_global_metrics_counts_all_six_keys(db):
    """六个真实键的计数与库内直接查询一致。"""
    with session_scope() as session:
        _seed_report(session, status="drafting")
        session.add(Project(name="已就绪项目", status="ready"))
        session.add(Job(kind="check", status="queued"))
        session.add(Job(kind="check", status="queued"))
        session.add(Job(kind="export", status="done"))
        session.flush()

        metrics = global_metrics(session)
        assert metrics["projects_by_status"] == {"drafting": 1, "ready": 1}
        assert metrics["jobs_by_status"] == {"queued": 2, "done": 1}
        assert metrics["projects"] == 2
        assert metrics["jobs"] == 3
        assert metrics["open_findings"] == 0
        expected_users = session.scalar(select(func.count()).select_from(User)) or 0
        assert metrics["users"] == expected_users


def test_global_metrics_without_db_is_cli_safe(db):
    """不传 db 时自行开短事务，CLI / 测试可直接调用。"""
    with session_scope() as session:
        session.add(Project(name="CLI 路径用例", status="intake"))
        session.flush()

    metrics = global_metrics()
    assert metrics["projects"] == 1
    assert metrics["projects_by_status"] == {"intake": 1}
    assert metrics["jobs"] == 0
    assert metrics["open_findings"] == 0
    assert isinstance(metrics["jobs_by_status"], dict)
