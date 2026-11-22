"""对标易标第四批（v0.27.0）：投标质量评估中心。

新增 2 张表：quality_reports / quality_findings。
幂等：``create_all`` 可能已按当前模型建出表，逐项探测后跳过。
Revision ID: 0007_yibiao_batch4
Revises: 0006_yibiao_batch3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_yibiao_batch4"
down_revision = "0006_yibiao_batch3"
branch_labels = None
depends_on = None

_NEW_TABLES = ("quality_reports", "quality_findings")

_INDEXES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "quality_reports": [("ix_quality_reports_project_id", ("project_id",))],
    "quality_findings": [
        ("ix_quality_findings_report_id", ("report_id",)),
        ("ix_quality_findings_category", ("category",)),
    ],
}


def _existing_tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _existing_indexes(bind, table: str) -> set[str]:
    return {ix["name"] for ix in inspect(bind).get_indexes(table)} if table in _existing_tables(bind) else set()


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_tables(bind)

    if "quality_reports" not in existing:
        op.create_table(
            "quality_reports",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("report_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("overall_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("grade", sa.String(4), nullable=False, server_default="C"),
            sa.Column("verdict", sa.String(20), nullable=False, server_default="warn"),
            sa.Column("dimensions", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("risk_summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "quality_findings" not in existing:
        op.create_table(
            "quality_findings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("report_id", sa.String(36), sa.ForeignKey("quality_reports.id"), nullable=False),
            sa.Column("category", sa.String(40), nullable=False, server_default=""),
            sa.Column("level", sa.String(20), nullable=False, server_default="major"),
            sa.Column("node_id", sa.String(36), nullable=True),
            sa.Column("scoring_item_id", sa.String(36), nullable=True),
            sa.Column("requirement_id", sa.String(36), nullable=True),
            sa.Column("title", sa.Text(), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("recommendation", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        )

    bind = op.get_bind()
    for table, indexes in _INDEXES.items():
        if table not in _existing_tables(bind):
            continue
        have = _existing_indexes(bind, table)
        for name, cols in indexes:
            if name not in have:
                op.create_index(name, table, list(cols))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_tables(bind)
    for table, indexes in reversed(list(_INDEXES.items())):
        if table not in existing:
            continue
        have = _existing_indexes(bind, table)
        for name, _cols in indexes:
            if name in have:
                op.drop_index(name, table_name=table)
    for table in reversed(_NEW_TABLES):
        if table in _existing_tables(bind):
            op.drop_table(table)
