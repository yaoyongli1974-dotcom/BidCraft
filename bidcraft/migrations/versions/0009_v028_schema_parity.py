"""v0.28 补遗：插件/判定列对齐 + 队列 owner_id + 计费对账表。

历史补列只写在 SQLite 专用的 ``db._migrate_columns`` 里，PostgreSQL 升级时
不会执行；本 revision 把这些列（含 v0.19/v0.20 加入的 requirements 列）统一
补进 Alembic，使 v0.27 → v0.28 的真实库升级后所有 ORM 查询可用。

同时为背压检查增加归一化的 ``jobs.owner_id``（带索引，按 payload 回填），并为
「已扣费、生成中」这一崩溃窗口增加 ``pending_charges`` durable marker，供
``jobqueue.reconcile_pending_charges`` 在启动/CLI 对账。

所有 DDL 均写成幂等：``create_all`` 可能已按当前模型建出列/表/索引。

Revision ID: 0009_v028_schema_parity
Revises: 0008_job_queue_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009_v028_schema_parity"
down_revision = "0008_job_queue_indexes"
branch_labels = None
depends_on = None

# (table, column name, type factory, nullable, server_default)
# 默认值与 db._migrate_columns 保持一致；列定义每次调用都新建，避免复用 Column 对象。
_ADD_COLUMNS: tuple[tuple[str, str, object, bool, str | None], ...] = (
    ("plugins", "fail_streak", sa.Integer, True, "0"),
    ("plugins", "auto_disabled", sa.Boolean, True, "0"),
    ("plugins", "mem_limit_mb", sa.Integer, True, "512"),
    ("plugins", "cpu_seconds", sa.Integer, True, "10"),
    ("plugins", "scope", sa.String(10), True, "global"),
    ("plugins", "owner_id", sa.String(36), True, None),
    ("plugins", "allow_private_net", sa.Boolean, True, "0"),
    ("requirements", "page", sa.Integer, True, "0"),
    ("requirements", "clause_no", sa.String(40), True, ""),
    ("requirements", "response_verdict", sa.String(16), True, ""),
    ("requirements", "response_verdict_label", sa.String(32), True, ""),
    ("requirements", "response_verdict_reason", sa.Text, True, ""),
    ("jobs", "owner_id", sa.String(36), True, None),
)

_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_plugins_owner_id", "plugins", ("owner_id",)),
    ("ix_jobs_owner_id", "jobs", ("owner_id",)),
    ("ix_pending_charges_user_id", "pending_charges", ("user_id",)),
    ("ix_pending_charges_project_id", "pending_charges", ("project_id",)),
    ("ix_pending_charges_status", "pending_charges", ("status",)),
)

# (name, type factory, nullable, primary_key, server_default)
_PENDING_COLUMNS: tuple[tuple[str, object, bool, bool, str | None], ...] = (
    ("id", sa.String(36), False, True, None),
    ("user_id", sa.String(36), False, False, None),
    ("project_id", sa.String(36), False, False, None),
    ("credits", sa.Integer, False, False, "0"),
    ("chapters", sa.Integer, False, False, "1"),
    ("status", sa.String(16), False, False, "pending"),
    ("note", sa.Text, False, False, ""),
    ("refunded_credits", sa.Integer, False, False, "0"),
    ("created_at", sa.DateTime, False, False, None),
    ("updated_at", sa.DateTime, False, False, None),
)


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in inspect(bind).get_indexes(table)}


def _backfill_job_owner(bind) -> None:
    """Normalize legacy payload['owner_id'] into the indexed column."""

    if bind.dialect.name == "postgresql":
        expression = "payload->>'owner_id'"
    else:
        expression = "json_extract(payload, '$.owner_id')"
    op.execute(
        f"UPDATE jobs SET owner_id = {expression} "
        "WHERE owner_id IS NULL AND payload IS NOT NULL"
    )


def _make_column(name: str, type_, nullable: bool, default: str | None) -> sa.Column:
    return sa.Column(name, type_, nullable=nullable, server_default=default)


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = _tables(bind)
    existing_columns = {
        table: _columns(bind, table)
        for table in {table for table, _, _, _, _ in _ADD_COLUMNS} & existing_tables
    }
    for table, name, type_, nullable, default in _ADD_COLUMNS:
        if table not in existing_tables or name in existing_columns.get(table, set()):
            continue
        op.add_column(table, _make_column(name, type_, nullable, default))
    if "jobs" in existing_tables:
        _backfill_job_owner(bind)

    if "pending_charges" not in existing_tables:
        op.create_table(
            "pending_charges",
            *(
                sa.Column(
                    name,
                    type_,
                    nullable=nullable,
                    primary_key=primary_key,
                    server_default=default,
                )
                for name, type_, nullable, primary_key, default in _PENDING_COLUMNS
            ),
        )
    existing_indexes = {
        table: _indexes(bind, table)
        for table in {table for _, table, _ in _INDEXES} & _tables(bind)
    }
    for name, table, cols in _INDEXES:
        if name not in existing_indexes.get(table, set()):
            op.create_index(name, table, list(cols), unique=False, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    existing_tables = _tables(bind)
    # SQLite 不允许删除仍被索引引用的列：先移除本 revision 建的索引。
    for name, table, _cols in reversed(_INDEXES):
        if table in existing_tables and name in _indexes(bind, table):
            op.drop_index(name, table_name=table, if_exists=True)
    existing_columns = {
        table: _columns(bind, table)
        for table in {table for table, _, _, _, _ in _ADD_COLUMNS} & _tables(bind)
    }
    for table, name, _type_, _nullable, _default in reversed(_ADD_COLUMNS):
        if name in existing_columns.get(table, set()):
            op.drop_column(table, name)
    if "pending_charges" in _tables(bind):
        op.drop_table("pending_charges")
