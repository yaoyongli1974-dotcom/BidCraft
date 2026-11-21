"""报价版本保存不可变计算依据（单价之外的数量/税率/币种/汇率/系数/舍入位）。

这是 Alembic 引入后的**第一个真实 schema 变更**，用于验证「新增列走 revision」
这条路径可用。写成幂等：``create_all`` 可能已经按当前模型建出了 ``basis`` 列，
此时跳过，避免与 create_all 冲突。

Revision ID: 0002_quote_basis
Revises: 0001_baseline
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_quote_basis"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("boq_quote_versions")}
    if "basis" not in columns:
        op.add_column(
            "boq_quote_versions",
            sa.Column("basis", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("boq_quote_versions")}
    if "basis" in columns:
        op.drop_column("boq_quote_versions", "basis")
