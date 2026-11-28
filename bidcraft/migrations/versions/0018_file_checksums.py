"""v0.34.0：个人文档表记录 sha256 校验和。

``user_docs`` 此前只按扩展名校验上传，文件落盘后无法发现被替换/篡改。本迁移
给该表增加 ``checksum`` 列（64 位十六进制 sha256）：上传时写入，下载/检索时
重算比对。历史行没有可信哈希，统一用空串表示「未知」，读取端跳过校验，绝不
把历史文件伪造成"已验证"。

``create_all`` 可能已按更新后的部署脚本建出列，故 upgrade/downgrade 都按名字
判存，重复执行为空操作；downgrade 只移除这一列，不删任何文件或业务数据。

Revision ID: 0018_file_checksums
Revises: 0017_transaction_id_unique
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0018_file_checksums"
down_revision = "0017_transaction_id_unique"
branch_labels = None
depends_on = None

TABLE = "user_docs"
COLUMN = "checksum"

CHECKSUM_COLUMN = sa.Column(
    COLUMN,
    sa.String(64),
    nullable=False,
    server_default="",
    comment="上传内容的 sha256；空串表示历史文件未记录哈希，读取端跳过校验",
)


def _columns(bind) -> set[str]:
    """只在本表存在时读取列集合，避免空库上 inspect 抛错。"""

    if TABLE not in set(inspect(bind).get_table_names()):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    if COLUMN not in _columns(op.get_bind()):
        op.add_column(TABLE, CHECKSUM_COLUMN)


def downgrade() -> None:
    if COLUMN in _columns(op.get_bind()):
        op.drop_column(TABLE, COLUMN)
