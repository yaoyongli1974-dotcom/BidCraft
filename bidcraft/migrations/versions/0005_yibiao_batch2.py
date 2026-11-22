"""对标易标第二批（v0.25.0）：企业共享、知识库文档级、查重、商务标。

新增 7 张表并为 qualifications / personnel / kb_chunks 追加可空列。
幂等：``create_all`` 可能已按当前模型建出表/列，逐项探测后跳过。
Revision ID: 0005_yibiao_batch2
Revises: 0004_claim_token
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_yibiao_batch2"
down_revision = "0004_claim_token"
branch_labels = None
depends_on = None

# (表名, 列定义工厂) —— 追加到已有表的可空列
_ADD_COLS: list[tuple[str, str, object]] = [
    ("qualifications", "enterprise_id", sa.String(36)),
    ("personnel", "enterprise_id", sa.String(36)),
    ("kb_chunks", "document_id", sa.String(36)),
    ("kb_chunks", "page_no", sa.Integer()),
    ("kb_chunks", "heading", sa.String(300)),
    ("kb_chunks", "chunk_index", sa.Integer()),
    ("kb_chunks", "hash", sa.String(64)),
]

_NEW_TABLES = (
    "enterprises",
    "enterprise_members",
    "kb_documents",
    "similarity_reports",
    "similarity_hits",
    "commercial_docs",
)


def _columns(bind) -> dict[str, set[str]]:
    return {
        table: {c["name"] for c in inspect(bind).get_columns(table)}
        for table in (
            "qualifications", "personnel", "kb_chunks",
            *_NEW_TABLES,
        )
        if table in set(inspect(bind).get_table_names())
    }


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(inspect(bind).get_table_names())

    if "enterprises" not in existing_tables:
        op.create_table(
            "enterprises",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_enterprises_owner_id", "enterprises", ["owner_id"])
    if "enterprise_members" not in existing_tables:
        op.create_table(
            "enterprise_members",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("enterprise_id", sa.String(36), sa.ForeignKey("enterprises.id"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("role", sa.String(20), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("enterprise_id", "user_id"),
        )
        op.create_index("ix_enterprise_members_enterprise_id", "enterprise_members", ["enterprise_id"])
        op.create_index("ix_enterprise_members_user_id", "enterprise_members", ["user_id"])
    if "kb_documents" not in existing_tables:
        op.create_table(
            "kb_documents",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("kb_id", sa.String(36), sa.ForeignKey("knowledge_bases.id"), nullable=False),
            sa.Column("source", sa.String(500), nullable=True),
            sa.Column("sha256", sa.String(64), nullable=True),
            sa.Column("size", sa.Integer, nullable=True),
            sa.Column("mime", sa.String(120), nullable=True),
            sa.Column("pages", sa.Integer, nullable=True),
            sa.Column("status", sa.String(20), nullable=True),
            sa.Column("version", sa.Integer, nullable=True),
            sa.Column("kind", sa.String(30), nullable=True),
            sa.Column("tags", sa.JSON, nullable=True),
            sa.Column("valid_to", sa.DateTime, nullable=True),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_kb_documents_kb_id", "kb_documents", ["kb_id"])
    if "similarity_reports" not in existing_tables:
        op.create_table(
            "similarity_reports",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("scope", sa.String(30), nullable=True),
            sa.Column("mode", sa.String(30), nullable=True),
            sa.Column("threshold", sa.Float, nullable=True),
            sa.Column("status", sa.String(20), nullable=True),
            sa.Column("stats", sa.JSON, nullable=True),
            sa.Column("skipped", sa.JSON, nullable=True),
            sa.Column("created_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("completed_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_similarity_reports_project_id", "similarity_reports", ["project_id"])
    if "similarity_hits" not in existing_tables:
        op.create_table(
            "similarity_hits",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("report_id", sa.String(36), sa.ForeignKey("similarity_reports.id"), nullable=False),
            sa.Column("kind", sa.String(20), nullable=True),
            sa.Column("source_a", sa.String(500), nullable=True),
            sa.Column("source_b", sa.String(500), nullable=True),
            sa.Column("loc_a", sa.String(120), nullable=True),
            sa.Column("loc_b", sa.String(120), nullable=True),
            sa.Column("excerpt_a", sa.Text, nullable=True),
            sa.Column("excerpt_b", sa.Text, nullable=True),
            sa.Column("similarity", sa.Float, nullable=True),
            sa.Column("evidence", sa.JSON, nullable=True),
            sa.Column("review_status", sa.String(20), nullable=True),
            sa.Column("review_note", sa.Text, nullable=True),
        )
        op.create_index("ix_similarity_hits_report_id", "similarity_hits", ["report_id"])
    if "commercial_docs" not in existing_tables:
        op.create_table(
            "commercial_docs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("name", sa.String(120), nullable=True),
            sa.Column("quote_version_id", sa.String(36), nullable=True),
            sa.Column("package_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(20), nullable=True),
            sa.Column("sections", sa.JSON, nullable=True),
            sa.Column("validation", sa.JSON, nullable=True),
            sa.Column("submit_note", sa.Text, nullable=True),
            sa.Column("submitted_by", sa.String(80), nullable=True),
            sa.Column("submitted_at", sa.DateTime, nullable=True),
            sa.Column("created_by", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_commercial_docs_project_id", "commercial_docs", ["project_id"])

    cols = _columns(bind)
    for table, name, typ in _ADD_COLS:
        if name not in cols.get(table, set()):
            op.add_column(table, sa.Column(name, typ, nullable=True))
    # 索引（幂等）
    for table, name in (
        ("qualifications", "enterprise_id"),
        ("personnel", "enterprise_id"),
        ("kb_chunks", "document_id"),
    ):
        idx = f"ix_{table}_{name}"
        if idx not in {i["name"] for i in inspect(bind).get_indexes(table)}:
            op.create_index(idx, table, [name])


def downgrade() -> None:
    bind = op.get_bind()
    # SQLite 不允许删除被索引引用的列：先删掉 upgrade 在既有表上建立的索引
    # （新表上的索引会随 drop_table 一起消失，无需单独处理）。
    for table, name in (
        ("qualifications", "enterprise_id"),
        ("personnel", "enterprise_id"),
        ("kb_chunks", "document_id"),
    ):
        idx = f"ix_{table}_{name}"
        if idx in {i["name"] for i in inspect(bind).get_indexes(table)}:
            op.drop_index(idx, table_name=table)
    cols = _columns(bind)
    for table, name, _typ in reversed(_ADD_COLS):
        if name in cols.get(table, set()):
            op.drop_column(table, name)
    existing_tables = set(inspect(bind).get_table_names())
    for table in reversed(_NEW_TABLES):
        if table in existing_tables:
            op.drop_table(table)
