"""v0.31 stage3：同项目生成互斥租约表。

用户批准的业务规则 A：同一项目同一时刻只允许一个付费生成请求。租约行以
``project_id`` 为主键，第二个请求用 ``INSERT ... ON CONFLICT DO NOTHING``
原子失败并返回 409；worker 心跳刷新 ``expires_at``，崩溃后过期租约可被
下一次请求回收。无外键，避免影响账号删除与账单流水。

Revision ID: 0012_generation_lease
Revises: 0011_stage3_accounting
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0012_generation_lease"
down_revision = "0011_stage3_accounting"
branch_labels = None
depends_on = None

TABLE = "generation_leases"


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in inspect(bind).get_indexes(table) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in _tables(bind):
        op.create_table(
            TABLE,
            sa.Column("project_id", sa.String(36), primary_key=True),
            sa.Column("token", sa.String(36), nullable=False),
            sa.Column("job_id", sa.String(36), nullable=True),
            sa.Column("owner_id", sa.String(36), nullable=True),
            sa.Column("acquired_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
    existing = _indexes(bind, TABLE)
    if "ix_generation_leases_job_id" not in existing:
        op.create_index("ix_generation_leases_job_id", TABLE, ["job_id"])
    if "ix_generation_leases_owner_id" not in existing:
        op.create_index("ix_generation_leases_owner_id", TABLE, ["owner_id"])
    if "ix_generation_leases_expires_at" not in existing:
        op.create_index("ix_generation_leases_expires_at", TABLE, ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in _tables(bind):
        return
    existing = _indexes(bind, TABLE)
    for name in (
        "ix_generation_leases_expires_at",
        "ix_generation_leases_owner_id",
        "ix_generation_leases_job_id",
    ):
        if name in existing:
            op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
