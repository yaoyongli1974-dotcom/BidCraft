"""Alembic 基线：老库收敛后记账、幂等、连正确的库。

create_all + _migrate_columns 仍是历史 schema 的收敛手段（baseline），
Alembic 从 0001_baseline 开始为**后续**变更记账。本文件守护这条边界。
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import inspect, text

from bidcraft.config import settings
from bidcraft.db import get_engine, init_db, reset_engine_for_tests

#: 当前 alembic head（新增 revision 时同步这里）。
HEAD = "0004_claim_token"


def _version() -> list[str]:
    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    return [row[0] for row in rows]


def test_fresh_db_is_stamped_to_baseline(db):
    assert _version() == [HEAD]


def test_repeated_init_is_idempotent(db):
    init_db()
    init_db()
    assert _version() == [HEAD]


def test_legacy_db_is_converged_then_stamped(tmp_path, monkeypatch):
    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True)
    monkeypatch.setattr(settings, "home", home)
    reset_engine_for_tests(f"sqlite+pysqlite:///{home / 'bidcraft.db'}")
    try:
        # 老式 kb_chunks：只有 id/text，缺向量三列
        with sqlite3.connect(home / "bidcraft.db") as conn:
            conn.execute(
                "CREATE TABLE kb_chunks (id VARCHAR(36) PRIMARY KEY, text TEXT)"
            )
            conn.execute("INSERT INTO kb_chunks (id, text) VALUES ('old-1', 'legacy')")

        init_db()

        with get_engine().connect() as connection:
            cols = {column["name"] for column in inspect(connection).get_columns("kb_chunks")}
        assert {"embedding", "embed_key", "embed_dim"} <= cols
        assert _version() == [HEAD]
    finally:
        reset_engine_for_tests("sqlite+pysqlite:///:memory:")


def test_migrations_follow_runtime_home(tmp_path, monkeypatch):
    """迁移必须连运行时注入的库，而不是任何配置文件里的固定路径。"""
    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    target = home / "custom.sqlite"
    reset_engine_for_tests(f"sqlite+pysqlite:///{target}")
    try:
        init_db()
        assert target.exists()
        with sqlite3.connect(target) as conn:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall() == [(HEAD,)]
    finally:
        reset_engine_for_tests("sqlite+pysqlite:///:memory:")
