"""v0.32 Phase 1：章节规划版本表。

章节规划必须可生成、可校验、可确认、可追溯，且不得把编造的评分项/事实/
知识/章节 ID 带入正文生成。本 revision 新增 ``chapter_plans``，只追加历史
之后的变更；已有的 0001-0012 一律不改。

``create_all`` 可能已按当前模型建出该表，因此 upgrade/downgrade 全部用
``inspect`` 判存；PG 上索引/表额外带 IF (NOT) EXISTS，SQLite 只靠判存守卫。

Revision ID: 0013_chapter_planning
Revises: 0012_generation_lease
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013_chapter_planning"
down_revision = "0012_generation_lease"
branch_labels = None
depends_on = None

TABLE = "chapter_plans"
UQ = "uq_chapter_plan_project_node_version"
IX_STATUS = "ix_chapter_plans_project_node_status"
IX_PACKAGE = "ix_chapter_plans_project_package"
IX_PROJECT_ID = "ix_chapter_plans_project_id"
IX_NODE_ID = "ix_chapter_plans_node_id"
IX_STATUS_COLUMN = "ix_chapter_plans_status"
ALL_INDEXES = (IX_STATUS, IX_PACKAGE, IX_PROJECT_ID, IX_NODE_ID, IX_STATUS_COLUMN)


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in inspect(bind).get_indexes(table) if item.get("name")}


def _if_not_exists_kw(bind) -> dict:
    if bind.dialect.name == "postgresql":
        return {"if_not_exists": True}
    return {}


def _if_exists_kw(bind) -> dict:
    if bind.dialect.name == "postgresql":
        return {"if_exists": True}
    return {}


def _columns() -> list[sa.Column]:
    """列定义与 models.ChapterPlan 一一对应，中文注释随迁移落库。"""

    return [
        sa.Column("id", sa.String(36), primary_key=True, comment="规划记录 ID"),
        sa.Column(
            "project_id", sa.String(36), nullable=False, comment="所属项目 ID"
        ),
        sa.Column(
            "node_id", sa.String(36), nullable=False, comment="绑定的大纲节点 ID"
        ),
        sa.Column(
            "package_id", sa.String(36), nullable=True, comment="标段/包件 ID（空表示项目级通用）"
        ),
        sa.Column("version", sa.Integer(), nullable=False, comment="同章节内的规划版本号"),
        sa.Column(
            "status", sa.String(24), nullable=False, comment="规划状态（状态机见 generation/chapter_plan.py）"
        ),
        sa.Column("objective", sa.Text(), nullable=False, comment="本章写作目标"),
        sa.Column(
            "raw_response", sa.Text(), nullable=False, comment="模型原始返回或确定性回退说明"
        ),
        sa.Column(
            "model_id", sa.String(120), nullable=False, comment="生成该规划的模型名或回退标识"
        ),
        sa.Column(
            "prompt_version", sa.String(40), nullable=False, comment="生成该规划所用的提示词版本"
        ),
        sa.Column(
            "user_instruction", sa.Text(), nullable=False, comment="用户针对本章的补充写作指令"
        ),
        sa.Column("created_by", sa.String(80), nullable=False, comment="规划创建人"),
        sa.Column("confirmed_by", sa.String(80), nullable=True, comment="规划确认人"),
        sa.Column("created_at", sa.DateTime(), nullable=True, comment="创建时间（naive UTC）"),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=True, comment="最近更新时间（naive UTC）"
        ),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True, comment="确认时间（naive UTC）"),
        sa.Column(
            "used_at", sa.DateTime(), nullable=True, comment="最近一次用于正文生成的时间（naive UTC）"
        ),
        sa.Column(
            "score_points", sa.JSON(), nullable=False, comment="本章覆盖的评分项（含真实评分项 ID）"
        ),
        sa.Column(
            "requirements", sa.JSON(), nullable=False, comment="本章响应的招标要求（含真实需求 ID）"
        ),
        sa.Column("conditions", sa.JSON(), nullable=False, comment="本章必须满足的前提条件"),
        sa.Column("challenges", sa.JSON(), nullable=False, comment="本章的写作难点与风险"),
        sa.Column(
            "strategy", sa.JSON(), nullable=False, comment="写作策略（注入正文提示词）"
        ),
        sa.Column("measures", sa.JSON(), nullable=False, comment="具体落实措施"),
        sa.Column(
            "knowledge_refs", sa.JSON(), nullable=False, comment="引用的知识条目（含真实知识 ID）"
        ),
        sa.Column(
            "fact_refs", sa.JSON(), nullable=False, comment="引用的事实台账条目（含真实事实 ID）"
        ),
        sa.Column("planned_tables", sa.JSON(), nullable=False, comment="计划使用的表格"),
        sa.Column("planned_diagrams", sa.JSON(), nullable=False, comment="计划使用的图示"),
        sa.Column(
            "forbidden_assumptions", sa.JSON(), nullable=False, comment="本章禁止作出的假设"
        ),
        sa.Column("quality_criteria", sa.JSON(), nullable=False, comment="本章质量验收口径"),
        sa.Column("user_adjustments", sa.JSON(), nullable=False, comment="用户手工调整记录"),
        sa.Column(
            "validation_errors", sa.JSON(), nullable=False, comment="校验发现并已修复的问题（中文）"
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in _tables(bind):
        op.create_table(
            TABLE,
            *_columns(),
            sa.UniqueConstraint(
                "project_id", "node_id", "version", name=UQ
            ),
            **_if_not_exists_kw(bind),
        )
    existing = _indexes(bind, TABLE)
    kw = _if_not_exists_kw(bind)
    if IX_STATUS not in existing:
        op.create_index(
            IX_STATUS, TABLE, ["project_id", "node_id", "status"], **kw
        )
    if IX_PACKAGE not in existing:
        op.create_index(IX_PACKAGE, TABLE, ["project_id", "package_id"], **kw)
    # 模型里 project_id/node_id/status 各自带 index=True，create_all 会建出这三列
    # 的单列索引；老库（先 stamp 再 upgrade）可能没有，这里按约定补齐。
    for name, column in (
        (IX_PROJECT_ID, "project_id"),
        (IX_NODE_ID, "node_id"),
        (IX_STATUS_COLUMN, "status"),
    ):
        if name not in existing:
            op.create_index(name, TABLE, [column], **kw)


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE not in _tables(bind):
        return
    existing = _indexes(bind, TABLE)
    kw = _if_exists_kw(bind)
    # SQLite 不允许删除仍被索引引用的列，故先删索引再删表。
    for name in reversed(ALL_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=TABLE, **kw)
    # 唯一约束随表一起消失，无需显式 DROP CONSTRAINT（SQLite 方言兼容）。
    op.drop_table(TABLE, **kw)
