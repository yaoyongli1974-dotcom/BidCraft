"""v0.32 Phase 2：知识资产元数据与查重候选表。

结构化知识资产必须挂在既有 ``KbDocument`` / ``KbChunk`` / ``KnowledgeBase``
上，绝不新建第二套知识库。本次变更只做加法：文档行增加可空的审核/结构化
列（``review_status`` 不加 server_default，历史行保持 NULL 以维持既有检索
行为），并新增同库内的查重候选组与组成员表。已有的 0001-0013 一律不改。

``create_all`` 可能已按当前模型建出列/表，因此 upgrade/downgrade 全部用
``inspect`` 判存；PG 上索引/表额外带 IF (NOT) EXISTS，SQLite 只靠判存守卫。

Revision ID: 0014_kb_asset_metadata
Revises: 0013_chapter_planning
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014_kb_asset_metadata"
down_revision = "0013_chapter_planning"
branch_labels = None
depends_on = None

DOCUMENT_TABLE = "kb_documents"
GROUP_TABLE = "kb_asset_duplicate_groups"
MEMBER_TABLE = "kb_asset_duplicate_members"

# 文档元数据列：全部可空，历史行不写默认值（尤其 review_status）。
DOCUMENT_COLUMNS = (
    "structured",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "published_at",
    "rejected_reason",
    "source_file_id",
)

UQ_GROUP = "uq_kb_asset_duplicate_group"
UQ_MEMBER = "uq_kb_asset_duplicate_member"
IX_GROUP_KB_ID = "ix_kb_asset_duplicate_groups_kb_id"
IX_GROUP_KB_STATUS = "ix_kb_asset_duplicate_groups_kb_status"
IX_MEMBER_GROUP_ID = "ix_kb_asset_duplicate_members_group_id"
IX_MEMBER_DOCUMENT_ID = "ix_kb_asset_duplicate_members_document_id"
GROUP_INDEXES = (IX_GROUP_KB_ID, IX_GROUP_KB_STATUS)
MEMBER_INDEXES = (IX_MEMBER_GROUP_ID, IX_MEMBER_DOCUMENT_ID)


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table)}


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


def _document_columns() -> list[sa.Column]:
    """与 models.KbDocument 一一对应的可空列，中文注释随迁移落库。"""

    return [
        sa.Column(
            "structured", sa.JSON(), nullable=True,
            comment="结构化资产字段（确定性抽取，含真实来源）",
        ),
        sa.Column(
            "review_status", sa.String(16), nullable=True,
            comment="资产审核状态；NULL 为历史文档，仍按原规则检索",
        ),
        sa.Column(
            "reviewed_by", sa.String(64), nullable=True, comment="最近一次审核人",
        ),
        sa.Column(
            "reviewed_at", sa.DateTime(), nullable=True,
            comment="最近一次审核时间（naive UTC）",
        ),
        sa.Column(
            "published_at", sa.DateTime(), nullable=True,
            comment="发布时间（naive UTC）",
        ),
        sa.Column(
            "rejected_reason", sa.Text(), nullable=True, comment="驳回原因",
        ),
        sa.Column(
            "source_file_id", sa.String(36), nullable=True,
            comment="来源文件 ID（真实文件溯源，不编造）",
        ),
    ]


def _group_columns() -> list[sa.Column]:
    """与 models.KbAssetDuplicateGroup 一一对应的列。"""

    return [
        sa.Column("id", sa.String(36), primary_key=True, comment="候选组 ID"),
        sa.Column(
            "kb_id", sa.String(36), nullable=False,
            comment="所属知识库 ID",
        ),
        sa.Column(
            "fingerprint", sa.String(64), nullable=False,
            comment="组内指纹（哈希摘要，同库唯一）",
        ),
        sa.Column(
            "similarity", sa.Float(), nullable=False, default=0.0,
            comment="组内相似度（精确哈希 1.0，题录规范化为固定值）",
        ),
        sa.Column(
            "status", sa.String(16), nullable=False, default="pending",
            comment="裁决状态：pending|resolved|dismissed",
        ),
        sa.Column(
            "reason", sa.Text(), nullable=True, comment="成组原因或裁决说明",
        ),
        sa.Column(
            "created_by", sa.String(64), nullable=False, default="",
            comment="候选组创建人（自动生成为 system）",
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=True,
            comment="创建时间（naive UTC）",
        ),
        sa.Column(
            "reviewed_by", sa.String(64), nullable=True, comment="裁决人",
        ),
        sa.Column(
            "reviewed_at", sa.DateTime(), nullable=True,
            comment="裁决时间（naive UTC）",
        ),
    ]


def _member_columns() -> list[sa.Column]:
    """与 models.KbAssetDuplicateMember 一一对应的列。"""

    return [
        sa.Column("id", sa.String(36), primary_key=True, comment="组成员 ID"),
        sa.Column(
            "group_id", sa.String(36), nullable=False,
            comment="所属候选组 ID",
        ),
        sa.Column(
            "document_id", sa.String(36), nullable=False, comment="真实文档 ID",
        ),
        sa.Column(
            "score", sa.Float(), nullable=False, default=0.0,
            comment="该成员与组基准的相似度",
        ),
        sa.Column(
            "role", sa.String(16), nullable=False, default="candidate",
            comment="成员角色：primary|candidate",
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    kw = _if_not_exists_kw(bind)

    # 1) 文档行只补可空列；create_all 已建出时必须为空操作。
    existing_columns = _columns(bind, DOCUMENT_TABLE)
    for column in _document_columns():
        if column.name not in existing_columns:
            op.add_column(DOCUMENT_TABLE, column)

    # 2) 查重候选组：同库内指纹唯一，按状态查询。
    if GROUP_TABLE not in tables:
        op.create_table(
            GROUP_TABLE,
            *_group_columns(),
            sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"]),
            sa.UniqueConstraint("kb_id", "fingerprint", name=UQ_GROUP),
            **kw,
        )
    group_indexes = _indexes(bind, GROUP_TABLE)
    if IX_GROUP_KB_ID not in group_indexes:
        op.create_index(IX_GROUP_KB_ID, GROUP_TABLE, ["kb_id"], **kw)
    if IX_GROUP_KB_STATUS not in group_indexes:
        op.create_index(IX_GROUP_KB_STATUS, GROUP_TABLE, ["kb_id", "status"], **kw)

    # 3) 组成员：只引用真实文档，同组内文档唯一。
    if MEMBER_TABLE not in tables:
        op.create_table(
            MEMBER_TABLE,
            *_member_columns(),
            sa.ForeignKeyConstraint(["group_id"], [f"{GROUP_TABLE}.id"]),
            sa.ForeignKeyConstraint(["document_id"], [f"{DOCUMENT_TABLE}.id"]),
            sa.UniqueConstraint("group_id", "document_id", name=UQ_MEMBER),
            **kw,
        )
    member_indexes = _indexes(bind, MEMBER_TABLE)
    if IX_MEMBER_GROUP_ID not in member_indexes:
        op.create_index(IX_MEMBER_GROUP_ID, MEMBER_TABLE, ["group_id"], **kw)
    if IX_MEMBER_DOCUMENT_ID not in member_indexes:
        op.create_index(IX_MEMBER_DOCUMENT_ID, MEMBER_TABLE, ["document_id"], **kw)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    kw = _if_exists_kw(bind)

    # SQLite 不允许删除仍被索引引用的列/表，先按反向顺序清索引。
    if MEMBER_TABLE in tables:
        member_indexes = _indexes(bind, MEMBER_TABLE)
        for name in reversed(MEMBER_INDEXES):
            if name in member_indexes:
                op.drop_index(name, table_name=MEMBER_TABLE, **kw)
        # 唯一约束随表一起消失，无需显式 DROP CONSTRAINT（SQLite 方言兼容）。
        op.drop_table(MEMBER_TABLE, **kw)

    if GROUP_TABLE in tables:
        group_indexes = _indexes(bind, GROUP_TABLE)
        for name in reversed(GROUP_INDEXES):
            if name in group_indexes:
                op.drop_index(name, table_name=GROUP_TABLE, **kw)
        op.drop_table(GROUP_TABLE, **kw)

    existing_columns = _columns(bind, DOCUMENT_TABLE)
    for name in reversed(DOCUMENT_COLUMNS):
        if name in existing_columns:
            op.drop_column(DOCUMENT_TABLE, name)
