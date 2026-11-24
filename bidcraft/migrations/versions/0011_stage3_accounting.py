"""v0.30 stage3：计费对账的账本凭据（章节基线 + 退款认领）。

两列都由运行时模型直接声明，``create_all`` 可能已经把它们建出来；因此本
revision 完全幂等：先 ``inspect`` 判存，再加 ``IF NOT EXISTS``（PG），SQLite
只靠 inspect 守卫（其 ADD COLUMN 不支持该子句）。降级同样守卫，可反复升降级。

- ``pending_charges.sections_at_charge``：扣费瞬间该项目已落库章节数。对账时
  用当前章节数减去它，只退本次扣费之后新增章节对应的点数。
- ``jobs.refund_claimed``：DB 层的退款认领标记。条件 UPDATE 保证多个 worker /
  进程对同一个 job 只能退一次；``payload['refunded']`` 降级为兼容日志标记。

Revision ID: 0011_stage3_accounting
Revises: 0010_batch1_billing_reliability
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0011_stage3_accounting"
down_revision = "0010_batch1_billing_reliability"
branch_labels = None
depends_on = None

_SECTIONS_AT_CHARGE = "sections_at_charge"
_REFUND_CLAIMED = "refund_claimed"


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _if_exists_kw(bind) -> dict:
    """PostgreSQL understands IF (NOT) EXISTS; SQLite only has inspect guards."""

    if bind.dialect.name == "postgresql":
        return {"if_not_exists": True}
    return {}


def _drop_kw(bind) -> dict:
    if bind.dialect.name == "postgresql":
        return {"if_exists": True}
    return {}


def upgrade() -> None:
    bind = op.get_bind()
    add_kw = _if_exists_kw(bind)

    # _columns() 内部已经用 _tables() 守卫，表不存在时返回空集合。
    if _SECTIONS_AT_CHARGE not in _columns(bind, "pending_charges"):
        op.add_column(
            "pending_charges",
            sa.Column(
                _SECTIONS_AT_CHARGE,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            **add_kw,
        )

    if _REFUND_CLAIMED not in _columns(bind, "jobs"):
        # PG 拒绝把整数 0 隐式转成 boolean 默认值；SQLite 则只认 0/1。
        default = sa.text("false") if bind.dialect.name == "postgresql" else sa.text("0")
        op.add_column(
            "jobs",
            sa.Column(
                _REFUND_CLAIMED,
                sa.Boolean(),
                nullable=False,
                server_default=default,
            ),
            **add_kw,
        )


def downgrade() -> None:
    bind = op.get_bind()
    drop_kw = _drop_kw(bind)

    # 顺序无关（两列互不依赖），但先 marker 再 job，与 upgrade 阅读顺序一致。
    if _SECTIONS_AT_CHARGE in _columns(bind, "pending_charges"):
        op.drop_column("pending_charges", _SECTIONS_AT_CHARGE, **drop_kw)
    if _REFUND_CLAIMED in _columns(bind, "jobs"):
        op.drop_column("jobs", _REFUND_CLAIMED, **drop_kw)
