"""真实 PostgreSQL 测试基础设施（opt-in）。

设置 ``BIDCRAFT_TEST_PG_URL`` 后，本目录下的用例直接连接真实 PostgreSQL；
未设置时整目录跳过（环境缺失，不作通过结论）。每轮测试前 TRUNCATE 应用表，
仅保留 alembic_version；线程间不共享 Session。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import inspect, text

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()


@pytest.fixture(scope="module")
def pg_engine():
    if not PG_URL:
        # conftest 里的模块级 pytestmark 对测试模块无效；必须在 fixture 中显式 skip，
        # 否则默认 pytest job 会进入 fixture 并以空 URL 触发 67 个 ERROR。
        pytest.skip("需要 BIDCRAFT_TEST_PG_URL（专用测试库）")
    from bidcraft.config import settings
    from bidcraft.db import get_engine, init_db, reset_engine_for_tests
    from bidcraft.models import Base

    old_url = settings.db_url
    try:
        settings.db_url = PG_URL
        reset_engine_for_tests(PG_URL)
        engine = get_engine()
        with engine.connect() as connection:
            assert connection.dialect.name == "postgresql"
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        init_db()
        yield engine
    finally:
        settings.db_url = old_url
        reset_engine_for_tests(old_url)


@pytest.fixture(autouse=True)
def _pg_isolation(pg_engine):
    """每个用例前清空应用数据，保留 Alembic 版本与队列内部状态。"""
    from bidcraft import jobqueue

    jobqueue._reset()
    inspector = inspect(pg_engine)
    tables = [name for name in inspector.get_table_names() if name != "alembic_version"]
    with pg_engine.begin() as connection:
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
