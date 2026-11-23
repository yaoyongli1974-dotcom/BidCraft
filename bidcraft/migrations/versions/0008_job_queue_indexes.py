"""任务队列热路径索引：状态与（状态、优先级、创建时间）组合索引。

认领任务时按 ``status='queued' ORDER BY priority, created_at LIMIT 1`` 取任务，
背压检查按 status 统计；缺少索引时每次都要全表扫描 + 排序。写成幂等，
兼容已由 ``create_all`` 按当前模型建出索引的库。

Revision ID: 0008_job_queue_indexes
Revises: 0007_yibiao_batch4
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0008_job_queue_indexes"
down_revision = "0007_yibiao_batch4"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_jobs_status", ["status"]),
    ("ix_jobs_status_priority_created", ["status", "priority", "created_at"]),
)


def _existing_indexes() -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes("jobs")}


def upgrade() -> None:
    existing = _existing_indexes()
    for name, columns in _INDEXES:
        if name not in existing:
            op.create_index(name, "jobs", columns, unique=False, if_not_exists=True)


def downgrade() -> None:
    existing = _existing_indexes()
    for name, _columns in reversed(_INDEXES):
        if name in existing:
            op.drop_index(name, table_name="jobs", if_exists=True)
