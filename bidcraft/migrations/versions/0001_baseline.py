"""Alembic 基线标记（v0.8.0 之前的 schema）。

本 revision **故意为空**：它只是记账起点，不执行任何 DDL。

背景：BidCraft 早期用 ``create_all`` + ``_migrate_columns`` 管理 SQLite schema。
引入 Alembic 后，旧库由旧机制收敛到当前 schema 再 ``stamp`` 到本版本；全新库
由 ``create_all`` 建出当前 schema 后同样 stamp 到本版本。

约定（重要）：
- 本 revision 之后的每个 revision 都必须写成**幂等 / checkfirst**：
  因为 ``create_all`` 始终按当前模型建表，可能已经建出 revision 要加的列；
  幂等 revision 在列/表已存在时跳过，才不会与 create_all 冲突。
- 新增/改类型/重命名/加索引一律走新 revision，不再往 ``_migrate_columns``
  的 additions 字典里加东西（那里只保留历史遗留补列）。
"""

from __future__ import annotations

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
