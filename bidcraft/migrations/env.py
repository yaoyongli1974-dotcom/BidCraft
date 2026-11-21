"""Alembic 环境：连接串由 ``bidcraft.db.init_db`` 在运行时注入。

不依赖 ``alembic.ini``（仓库里没有该配置文件），这样：
- Docker 单进程、测试临时库都能用 programmatic API 调用；
- 不会出现“配置文件里的 URL 与运行时 BIDCRAFT_HOME 不一致”连错库。
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from bidcraft.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite 改类型/加约束时走 batch 重建
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
