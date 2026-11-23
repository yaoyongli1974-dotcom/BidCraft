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
HEAD = "0008_job_queue_indexes"


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


def test_batch2_downgrade_drops_indexes_before_columns(tmp_path, monkeypatch):
    """0005 的 downgrade 必须先删索引再删列（SQLite 不允许删被索引引用的列）。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    db_file = home / "dgrade.sqlite"
    reset_engine_for_tests(f"sqlite+pysqlite:///{db_file}")
    try:
        init_db()
        engine = get_engine()

        def table_set():
            return set(inspect(engine).get_table_names())

        added = {
            "enterprises", "enterprise_members", "kb_documents",
            "similarity_reports", "similarity_hits", "commercial_docs",
        }
        assert added <= table_set()

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(migrations_init).parent))
        cfg.set_main_option(
            "sqlalchemy.url",
            engine.url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        # downgrade 到上一版：索引与列都必须干净移除，且可重复升级回来
        command.downgrade(cfg, "0004_claim_token")
        assert not (added & table_set())
        cols = {c["name"] for c in inspect(engine).get_columns("kb_chunks")}
        assert not ({"document_id", "page_no", "heading", "chunk_index", "hash"} & cols)
        command.upgrade(cfg, "head")
        assert added <= table_set()
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


def test_job_queue_indexes_created_and_reversible(db):
    """0008 建立队列热路径索引，降级后必须干净移除。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    engine = get_engine()
    expected = {"ix_jobs_status", "ix_jobs_status_priority_created"}

    def indexes():
        with engine.connect() as connection:
            return {idx["name"] for idx in inspect(connection).get_indexes("jobs")}

    assert expected <= indexes()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(migrations_init).parent))
    cfg.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.downgrade(cfg, "0007_yibiao_batch4")
    assert not (expected & indexes())
    command.upgrade(cfg, "head")
    assert expected <= indexes()
    assert _version() == [HEAD]
