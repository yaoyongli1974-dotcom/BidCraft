"""v0.33.2：事实台账补充来源类型与用户说明。

缺失项是招标文件本身没有的信息，不能伪造原文与页码。本迁移给 ``facts`` 增加
两列可空字段：``provenance_kind`` 区分 ``tender_extract``（招标文件原文，必须
保留来源校验）与 ``user_supplied``（用户补充，凭说明理由留痕，不写 source_*）；
``supplier_note`` 记录用户提供该值的说明。两列都**不设 server_default**：历史
NULL 一律按 ``tender_extract`` 解释，绝不把旧数据美化成用户补充说明。

``create_all`` 可能已按当前模型建出列，故 upgrade/downgrade 全部用 inspect
判存，重复执行为空操作；downgrade 只移除本次新增两列，事实值与原来源列不动。

Revision ID: 0016_fact_provenance
Revises: 0015_fact_confirmation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0016_fact_provenance"
down_revision = "0015_fact_confirmation"
branch_labels = None
depends_on = None

FACT_TABLE = "facts"

PROVENANCE_COLUMNS = (
    sa.Column(
        "provenance_kind",
        sa.String(24),
        nullable=True,
        comment="事实来源类型：tender_extract=招标文件原文；user_supplied=用户补充说明；NULL 视作 tender_extract",
    ),
    sa.Column(
        "supplier_note",
        sa.Text(),
        nullable=True,
        comment="用户补充说明：user_supplied 必填，tender_extract 可空",
    ),
)
PROVENANCE_NAMES = tuple(column.name for column in PROVENANCE_COLUMNS)


def _columns(bind) -> set[str]:
    """只在本表存在时读取列集合，避免空库上 inspect 抛错。"""

    if FACT_TABLE not in set(inspect(bind).get_table_names()):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(FACT_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for column in PROVENANCE_COLUMNS:
        if column.name not in existing:
            op.add_column(FACT_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for name in reversed(PROVENANCE_NAMES):
        if name in existing:
            op.drop_column(FACT_TABLE, name)
