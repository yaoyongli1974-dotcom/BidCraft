"""对标易标第三批（v0.26.0）：Agent 编排 + 协作与版本管理。

新增 6 张表：workflow_runs / workflow_tasks / workflow_approvals /
section_revisions / section_edit_locks / chapter_assignments。
幂等：``create_all`` 可能已按当前模型建出表，逐项探测后跳过。
Revision ID: 0006_yibiao_batch3
Revises: 0005_yibiao_batch2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006_yibiao_batch3"
down_revision = "0005_yibiao_batch2"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    "workflow_runs",
    "workflow_tasks",
    "workflow_approvals",
    "section_revisions",
    "section_edit_locks",
    "chapter_assignments",
)

# 建表后要创建的索引（与模型 index=True 对齐）。
_INDEXES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "workflow_runs": [
        ("ix_workflow_runs_project_id", ("project_id",)),
        ("ix_workflow_runs_job_id", ("job_id",)),
    ],
    "workflow_tasks": [("ix_workflow_tasks_run_id", ("run_id",))],
    "workflow_approvals": [
        ("ix_workflow_approvals_run_id", ("run_id",)),
        ("ix_workflow_approvals_task_id", ("task_id",)),
    ],
    "section_revisions": [
        ("ix_section_revisions_project_id", ("project_id",)),
        ("ix_section_revisions_node_id", ("node_id",)),
    ],
    "section_edit_locks": [
        ("ix_section_edit_locks_project_id", ("project_id",)),
        ("ix_section_edit_locks_node_id", ("node_id",)),
        ("ix_section_edit_locks_user_id", ("user_id",)),
    ],
    "chapter_assignments": [
        ("ix_chapter_assignments_project_id", ("project_id",)),
        ("ix_chapter_assignments_node_id", ("node_id",)),
        ("ix_chapter_assignments_assignee_id", ("assignee_id",)),
    ],
}


def _existing_tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _existing_indexes(bind, table: str) -> set[str]:
    return {ix["name"] for ix in inspect(bind).get_indexes(table)} if table in _existing_tables(bind) else set()


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_tables(bind)

    if "workflow_runs" not in existing:
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("job_id", sa.String(36), nullable=True),
            sa.Column("workflow_type", sa.String(40), nullable=False, server_default="standard"),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("goal", sa.Text(), nullable=False, server_default=""),
            sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("stats", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("budget", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
    if "workflow_tasks" not in existing:
        op.create_table(
            "workflow_tasks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("task_key", sa.String(80), nullable=False, server_default=""),
            sa.Column("agent_type", sa.String(40), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("input", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("output", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
    if "workflow_approvals" not in existing:
        op.create_table(
            "workflow_approvals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
            sa.Column("task_id", sa.String(36), nullable=True),
            sa.Column("approval_type", sa.String(40), nullable=False, server_default="delivery"),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("submitted_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("reviewed_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("submitted_at", sa.DateTime(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        )
    if "section_revisions" not in existing:
        op.create_table(
            "section_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("node_id", sa.String(36), sa.ForeignKey("outline_nodes.id"), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("body_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("changed_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("change_type", sa.String(20), nullable=False, server_default="edit"),
            sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("node_id", "revision", name="uq_section_revisions_node_revision"),
        )
    if "section_edit_locks" not in existing:
        op.create_table(
            "section_edit_locks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("node_id", sa.String(36), sa.ForeignKey("outline_nodes.id"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("lock_token", sa.String(36), nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "chapter_assignments" not in existing:
        op.create_table(
            "chapter_assignments",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("node_id", sa.String(36), sa.ForeignKey("outline_nodes.id"), nullable=False),
            sa.Column("assignee_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("assigned_by", sa.String(80), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="assigned"),
            sa.Column("deadline", sa.DateTime(), nullable=True),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    # 索引：仅在表存在且索引缺失时创建（create_all 可能已建）。
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
