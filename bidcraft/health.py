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

from .models import Job, Project, QualityFinding, User

#: 质量 finding 的"未处置"状态；处置完成后不再计入待办。
OPEN_FINDING_STATUSES = ("open", "new", "reopened")


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
    return {
        # 明细口径
        "projects_by_status": projects_by_status,
        "jobs_by_status": jobs_by_status,
        # 常用总数（便于看板直接消费）
        "projects": sum(projects_by_status.values()),
        "jobs": sum(jobs_by_status.values()),
        "open_findings": int(findings),
        "users": int(users),
    }


__all__ = ["global_metrics"]
