"""v0.28.1 batch1：补全归属人回填 + 未结扣费记录付款账号。

0009 的 ``_backfill_job_owner`` 只认 ``payload['owner_id']``；更早的 job 只写了
``payload['user_id']``（或干脆只有 project_id），owner_id 仍为 NULL，per-user
背压对它们形同虚设。本 revision 用与运行时 ``models._job_owner_from_payload``
相同的 COALESCE 链重新回填，SQLite / PostgreSQL 各用各的 JSON 取值语法。

同时给 ``pending_charges`` 加 ``billing_account_id``：子账号消费扣的是主账号
的点数，marker 必须记下真正的付款账号，否则子账号在 TTL 对账前被删除后退款
无处可去（旧行为把 marker 标成 refunded 却退了 0 点）。

DDL 全部幂等：``create_all`` 可能已按当前模型建出列。

Revision ID: 0010_batch1_billing_reliability
Revises: 0009_v028_schema_parity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010_batch1_billing_reliability"
down_revision = "0009_v028_schema_parity"
branch_labels = None
depends_on = None

_BILLING_ACCOUNT_COLUMN = "billing_account_id"


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _backfill_job_owner(bind) -> None:
    """Mirror the runtime event's COALESCE chain into legacy rows.

    ``owner_id`` → ``user_id`` → the owning project's owner, with empty strings
    treated as missing so a partial legacy payload cannot shadow a good value.
    """

    if "jobs" not in _tables(bind):
        return
    if bind.dialect.name == "postgresql":
        owner = "NULLIF(payload->>'owner_id', '')"
        user = "NULLIF(payload->>'user_id', '')"
    else:
        owner = "NULLIF(json_extract(payload, '$.owner_id'), '')"
        user = "NULLIF(json_extract(payload, '$.user_id'), '')"
    project_owner = "(SELECT owner_id FROM projects WHERE id = jobs.project_id)"
    op.execute(
        "UPDATE jobs SET owner_id = COALESCE(" + owner + ", " + user + ", "
        + project_owner + ") "
        "WHERE (owner_id IS NULL OR owner_id = '') AND payload IS NOT NULL"
    )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_job_owner(bind)

    if "pending_charges" not in _tables(bind):
        return
    if _BILLING_ACCOUNT_COLUMN not in _columns(bind, "pending_charges"):
        op.add_column(
            "pending_charges",
            sa.Column(_BILLING_ACCOUNT_COLUMN, sa.String(36), nullable=True),
        )
    # Legacy markers: the paying account is the user's parent when present.
    op.execute(
        "UPDATE pending_charges SET billing_account_id = COALESCE("
        "(SELECT parent_id FROM users WHERE id = pending_charges.user_id), user_id) "
        "WHERE billing_account_id IS NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _BILLING_ACCOUNT_COLUMN in _columns(bind, "pending_charges"):
        op.drop_column("pending_charges", _BILLING_ACCOUNT_COLUMN)
