"""Fix3：global_metrics 的 open_findings 口径、10 个指标键与 db=None 的 CLI 安全路径。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.evaluation.report import FINDING_STATUSES
from bidcraft.health import OPEN_FINDING_STATUSES, global_metrics
from bidcraft.models import Job, Project, QualityFinding, QualityReport, User

METRIC_KEYS = {
    "projects_by_status",
    "jobs_by_status",
    "projects",
    "jobs",
    "open_findings",
    "users",
    "workers_running",
    "queued_jobs",
    "oldest_queued_age_seconds",
    "stale_running_jobs",
}


def _seed_report(session, status="intake"):
    project = Project(name="健康指标用例", status=status)
    session.add(project)
    session.flush()
    report = QualityReport(project_id=project.id)
    session.add(report)
    session.flush()
    return report


def test_open_findings_honors_open_finding_statuses(db):
    """只统计真实 QualityFinding 状态里的未处置项；Issue 词不再是口径。"""
    assert OPEN_FINDING_STATUSES == tuple(
        status for status in FINDING_STATUSES if status == "open"
    ) + ("pending",)
    with session_scope() as session:
        report = _seed_report(session)
        for status in FINDING_STATUSES:
            session.add(QualityFinding(report_id=report.id, title=status, status=status))
        # 历史 Issue 状态词（new/reopened）不是 QualityFinding 的合法状态，不计待办。
        for status in ("new", "reopened"):
            session.add(QualityFinding(report_id=report.id, title=status, status=status))
        session.flush()

        metrics = global_metrics(session)
        assert set(metrics) == METRIC_KEYS
        assert metrics["open_findings"] == 1

        # 把未处置的 finding 全部标记 handled 后，待办数归零。
        for finding in session.scalars(
            select(QualityFinding).where(QualityFinding.status.in_(OPEN_FINDING_STATUSES))
        ):
            finding.status = "handled"
        session.flush()
        assert global_metrics(session)["open_findings"] == 0


def test_global_metrics_counts_all_six_keys(db):
    """核心计数键与队列态势键都与库内直接查询一致（历史用例名保留）。"""
    with session_scope() as session:
        _seed_report(session, status="drafting")
        session.add(Project(name="已就绪项目", status="ready"))
        session.add(Job(kind="check", status="queued", created_at=utc_now() - timedelta(seconds=90)))
        session.add(Job(kind="check", status="queued"))
        session.add(Job(kind="export", status="done"))
        session.add(
            Job(kind="check", status="running", lease_expires_at=utc_now() - timedelta(seconds=5))
        )
        session.add(
            Job(kind="check", status="running", lease_expires_at=utc_now() + timedelta(seconds=60))
        )
        session.flush()

        metrics = global_metrics(session)
        assert set(metrics) == METRIC_KEYS
        assert metrics["projects_by_status"] == {"drafting": 1, "ready": 1}
        assert metrics["jobs_by_status"] == {"queued": 2, "running": 2, "done": 1}
        assert metrics["projects"] == 2
        assert metrics["jobs"] == 5
        assert metrics["open_findings"] == 0
        expected_users = session.scalar(select(func.count()).select_from(User)) or 0
        assert metrics["users"] == expected_users
        # 新增的 4 个队列态势键
        assert metrics["workers_running"] is False
        assert metrics["queued_jobs"] == 2
        assert metrics["oldest_queued_age_seconds"] >= 80
        assert metrics["stale_running_jobs"] == 1


def test_global_metrics_without_db_is_cli_safe(db):
    """不传 db 时自行开短事务，CLI / 测试可直接调用。"""
    with session_scope() as session:
        session.add(Project(name="CLI 路径用例", status="intake"))
        session.flush()

    metrics = global_metrics()
    assert set(metrics) == METRIC_KEYS
    assert metrics["projects"] == 1
    assert metrics["projects_by_status"] == {"intake": 1}
    assert metrics["jobs"] == 0
    assert metrics["open_findings"] == 0
    assert isinstance(metrics["jobs_by_status"], dict)
    assert metrics["queued_jobs"] == 0
    assert metrics["oldest_queued_age_seconds"] == 0
    assert metrics["stale_running_jobs"] == 0
