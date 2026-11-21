"""DB 驱动任务队列：Job 增加认领/租约/心跳/重试列。

Job 行本身就是队列凭据：Worker 用条件 UPDATE 原子认领（status queued→running），
心跳续 lease_expires_at，进程崩溃/租约过期由 reaper 重排队（attempts 上限）。

写成幂等：``create_all`` 可能已经按当前模型建出了这些列，此时跳过，避免冲突。

Revision ID: 0003_job_queue
Revises: 0002_quote_basis
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_job_queue"
down_revision = "0002_quote_basis"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("attempts", sa.Column("attempts", sa.Integer(), nullable=True)),
    ("max_attempts", sa.Column("max_attempts", sa.Integer(), nullable=True)),
    ("claimed_at", sa.Column("claimed_at", sa.DateTime(), nullable=True)),
    ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(), nullable=True)),
    ("heartbeat_at", sa.Column("heartbeat_at", sa.DateTime(), nullable=True)),
    ("worker_id", sa.Column("worker_id", sa.String(80), nullable=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("jobs")}
    for name, column in _COLUMNS:
        if name not in columns:
            op.add_column("jobs", column)
    # lease_expires_at 在模型上带 index=True；用 IF NOT EXISTS 保持一致。
    op.create_index(
        "ix_jobs_lease_expires_at",
        "jobs",
        ["lease_expires_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs", if_exists=True)
    columns = {column["name"] for column in inspect(bind).get_columns("jobs")}
    for name, _column in reversed(_COLUMNS):
        if name in columns:
            op.drop_column("jobs", name)
