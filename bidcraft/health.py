"""跨进程聚合的全局健康指标。

``/admin/health`` 原本只反映当前进程（uptime、DB 文件大小等）。多实例部署时
需要一份**数据库-backed**的全局视图：所有实例看到同样的项目/任务/问题/用户
计数。这里只提供纯查询函数，由既有的 health 路由挂到响应 payload 的
``global_metrics`` 键上，不新增 HTTP 路由（路由数保持稳定）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .clock import utc_now
from .evaluation.report import FINDING_STATUSES
from .models import Job, Project, QualityFinding, User

#: 质量 finding 的"未处置"状态：只从 QualityFinding 的真实状态集合派生
#: （``open/handled/ignored``），不再误用 Issue 的 ``new/reopened``。
#: ``pending`` 仅为兼容历史数据的显式补充。
OPEN_FINDING_STATUSES = tuple(
    status for status in FINDING_STATUSES if status == "open"
) + ("pending",)


def _by_status(db: Session, model: Any) -> dict[str, int]:
    rows = db.execute(select(model.status, func.count()).group_by(model.status)).all()
    return {str(status or ""): int(count) for status, count in rows}


def global_metrics(db: Session | None = None) -> dict[str, Any]:
    """返回所有进程共享的库内聚合计数（只读，不依赖进程本地状态）。

    ``db`` 省略时自行开一个短事务，方便 CLI / 测试直接调用。
    """

    if db is None:
        from .db import session_scope

        with session_scope() as session:
            return global_metrics(session)
    findings = (
        db.scalar(
            select(func.count())
            .select_from(QualityFinding)
            .where(QualityFinding.status.in_(OPEN_FINDING_STATUSES))
        )
        or 0
    )
    users = db.scalar(select(func.count()).select_from(User)) or 0
    projects_by_status = _by_status(db, Project)
    jobs_by_status = _by_status(db, Job)
    # 队列健康度：积压量、最老等待时长与过期未回收的 running 行。
    now = utc_now()
    queued_jobs = int(
        db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) or 0
    )
    oldest_queued_at = db.scalar(
        select(func.min(Job.created_at)).where(Job.status == "queued")
    )
    oldest_queued_age = (
        max(0, int((now - oldest_queued_at).total_seconds())) if oldest_queued_at else 0
    )
    stale_running_jobs = int(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "running", Job.lease_expires_at < now)
        )
        or 0
    )
    # 延迟导入：jobqueue 依赖 config/db，避免健康模块在导入期形成环。
    from . import jobqueue

    return {
        # 明细口径
        "projects_by_status": projects_by_status,
        "jobs_by_status": jobs_by_status,
        # 常用总数（便于看板直接消费）
        "projects": sum(projects_by_status.values()),
        "jobs": sum(jobs_by_status.values()),
        "open_findings": int(findings),
        "users": int(users),
        # 队列/进程运行态势
        "workers_running": bool(jobqueue.workers_running()),
        "queued_jobs": queued_jobs,
        "oldest_queued_age_seconds": oldest_queued_age,
        "stale_running_jobs": stale_running_jobs,
    }


__all__ = ["global_metrics"]
