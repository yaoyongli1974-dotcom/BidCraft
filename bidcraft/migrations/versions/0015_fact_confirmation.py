"""v0.32 Phase 5：事实台账人工确认治理。

给 ``facts`` 增加两列可空确认信息（确认人 / 确认时间）。确认信息必须由服务端
在人工确认时盖章，因此**不设 server_default**：历史行保持 NULL，绝不能因为
迁移就把批量抽取出来的事实伪造成「已人工确认」。列为可空，旧库/新库都能
平滑升级；``create_all`` 可能已按当前模型建出列，故 upgrade/downgrade 全部
用 ``inspect`` 判存，重复执行为空操作。downgrade 只移除本次新增的两列，
不触碰既有事实值与来源列，完整可逆。

Revision ID: 0015_fact_confirmation
Revises: 0014_kb_asset_metadata
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0015_fact_confirmation"
down_revision = "0014_kb_asset_metadata"
branch_labels = None
depends_on = None

FACT_TABLE = "facts"

# 与 models.Fact 一一对应：全部可空，且没有 server_default（不伪造确认）。
CONFIRMATION_COLUMNS = (
    sa.Column(
        "confirmed_by",
        sa.String(80),
        nullable=True,
        comment="人工确认人（服务端盖章，不接受客户端传入）",
    ),
    sa.Column(
        "confirmed_at",
        sa.DateTime(),
        nullable=True,
        comment="人工确认时间（naive UTC，服务端盖章）",
    ),
)
CONFIRMATION_NAMES = tuple(column.name for column in CONFIRMATION_COLUMNS)


def _columns(bind) -> set[str]:
    """只在本表存在时读取列集合，避免空库上 inspect 抛错。"""

    if FACT_TABLE not in set(inspect(bind).get_table_names()):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(FACT_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for column in CONFIRMATION_COLUMNS:
        if column.name not in existing:
            op.add_column(FACT_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    # 逆序删除，保持与 upgrade 对称；只删本次新增列，历史事实值不受影响。
    for name in reversed(CONFIRMATION_NAMES):
        if name in existing:
            op.drop_column(FACT_TABLE, name)
