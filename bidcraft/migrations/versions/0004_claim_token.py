"""fencing token：Job 增加 claim_token，每次认领刷新。

租约过期被回收后旧 worker 的令牌失效，其关键写操作（状态/进度/结果/退款）
与心跳都必须校验该令牌，避免旧 worker 与新 worker 并发执行同一任务。
至少一次执行（at-least-once）语义：不宣称 exactly-once。

幂等：``create_all`` 可能已经按当前模型建出该列，此时跳过。

Revision ID: 0004_claim_token
Revises: 0003_job_queue
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004_claim_token"
down_revision = "0003_job_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("jobs")}
    if "claim_token" not in columns:
        op.add_column("jobs", sa.Column("claim_token", sa.String(36), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("jobs")}
    if "claim_token" in columns:
        op.drop_column("jobs", "claim_token")
