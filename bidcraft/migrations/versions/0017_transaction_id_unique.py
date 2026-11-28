"""v0.34.0：给已支付订单的微信交易号加部分唯一索引。

空串是「尚未回写交易号」的待支付订单，必须允许多个；真实 transaction_id 一旦
回写，数据库必须拒绝第二个订单复用它，避免重复回调/伪造回调把同一笔钱入账两次。
索引条件 ``transaction_id != ''`` 同时覆盖 SQLite / PostgreSQL；create_all 可能
已经按当前模型建出该索引，故 upgrade/downgrade 全部按名字判存、重复执行为空操作。

升级前先检测历史重复交易号：存在重复时不静默改写历史数据也不让 DDL 半途失败，
而是给出明确错误，要求运维先人工核对后再升级。

Revision ID: 0017_transaction_id_unique
Revises: 0016_fact_provenance
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "0017_transaction_id_unique"
down_revision = "0016_fact_provenance"
branch_labels = None
depends_on = None

TABLE = "recharge_orders"
INDEX = "uq_recharge_orders_transaction_id_nonempty"
WHERE_NONEMPTY = text("transaction_id != ''")


def _indexes(bind) -> set[str]:
    """只在本表存在时读取索引集合，避免空库上 inspect 抛错。"""

    if TABLE not in set(inspect(bind).get_table_names()):
        return set()
    return {item["name"] for item in inspect(bind).get_indexes(TABLE) if item.get("name")}


def _reject_duplicate_transaction_ids(bind) -> None:
    """历史重复交易号必须人工核对，不能靠吞掉 DDL 错误蒙混过关。"""

    if TABLE not in set(inspect(bind).get_table_names()):
        return
    rows = bind.execute(
        text(
            "SELECT transaction_id, COUNT(*) AS n FROM recharge_orders "
            "WHERE transaction_id IS NOT NULL AND transaction_id != '' "
            "GROUP BY transaction_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if rows:
        sample = ", ".join(f"{row[0]}({row[1]})" for row in rows[:5])
        raise RuntimeError(
            "recharge_orders 存在重复的非空 transaction_id，拒绝建立唯一索引；"
            f"请先人工核对历史订单后清理（示例：{sample}）"
        )


def upgrade() -> None:
    bind = op.get_bind()
    if INDEX in _indexes(bind):
        return
    _reject_duplicate_transaction_ids(bind)
    op.create_index(
        INDEX,
        TABLE,
        ["transaction_id"],
        unique=True,
        sqlite_where=WHERE_NONEMPTY,
        postgresql_where=WHERE_NONEMPTY,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if INDEX in _indexes(bind):
        op.drop_index(INDEX, table_name=TABLE)
