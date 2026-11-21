"""PostgreSQL 支持的方言层单测（不需要真实数据库服务器）。

真实库的端到端验证在 ``test_postgres_integration.py``，通过
``BIDCRAFT_TEST_PG_URL`` 显式开启，避免默认套件依赖外部服务。
"""

from __future__ import annotations

import json
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql, sqlite

from bidcraft.config import (
    Settings,
    db_backend,
    normalize_db_url,
    redact_db_url,
    settings,
)


def test_backend_detection():
    assert db_backend("postgresql+psycopg://u:p@h/db") == "postgresql"
    assert db_backend("postgresql://u:p@h/db") == "postgresql"
    assert db_backend("postgres://u:p@h/db") == "postgresql"
    assert db_backend("sqlite+pysqlite:////tmp/x.db") == "sqlite"
    assert db_backend("") == "sqlite"


def test_url_normalization_uses_psycopg3():
    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_db_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_db_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_db_url("sqlite+pysqlite:////tmp/x.db") == "sqlite+pysqlite:////tmp/x.db"


def test_url_redaction_hides_password():
    redacted = redact_db_url("postgresql+psycopg://alice:s3cr3t@db:5432/bidcraft")
    assert "s3cr3t" not in redacted
    assert "alice:****@db:5432/bidcraft" in redacted
    assert redact_db_url("sqlite+pysqlite:////tmp/x.db") == "sqlite+pysqlite:////tmp/x.db"


def test_settings_read_pg_url_from_env(monkeypatch):
    # 直接构造 Settings，绝不调用 reload_settings()：那会替换全局对象，
    # 让 conftest/各模块持有的旧 settings 与新对象分叉，污染后续所有用例。
    monkeypatch.setenv("BIDCRAFT_DB_URL", "postgresql://u:p@h/db")
    cfg = Settings()
    assert cfg.db_url == "postgresql+psycopg://u:p@h/db"
    assert cfg.is_postgres is True
    assert cfg.db_url_display == "postgresql+psycopg://u:****@h/db"


def test_safe_dict_exposes_redacted_db_url():
    view = settings.safe_dict()
    assert view["db_backend"] in {"sqlite", "postgresql"}
    assert view["db_url"] == settings.db_url_display


def test_engine_options_are_dialect_aware():
    from bidcraft.db import _engine_options

    assert _engine_options("sqlite+pysqlite:////tmp/x.db") == {"future": True}
    pg = _engine_options("postgresql+psycopg://u:p@h/db")
    assert pg["pool_pre_ping"] is True
    assert pg["connect_args"]["connect_timeout"] == 10


def test_legacy_column_migration_skipped_on_postgres(monkeypatch):
    from bidcraft.db import _migrate_columns

    engine = create_engine("postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setattr(
        engine,
        "begin",
        lambda *a, **k: pytest.fail("PG 不应执行 SQLite 专属补列 DDL"),
    )
    _migrate_columns(engine)


def test_claim_statement_compiles_on_both_dialects():
    from datetime import timedelta

    from bidcraft.clock import utc_now
    from bidcraft.jobqueue import _claim_statement

    now = utc_now()
    statement = _claim_statement(now, now + timedelta(seconds=180), "t" * 32)
    pg_sql = str(statement.compile(dialect=postgresql.dialect()))
    sl_sql = str(statement.compile(dialect=sqlite.dialect()))
    assert "RETURNING" in pg_sql
    # 原子认领语句不含任何 SQLite 专属函数/pragma。
    for dialect_sql in (pg_sql, sl_sql):
        lowered = dialect_sql.lower()
        assert "julianday" not in lowered
        assert "pragma" not in lowered
        assert "rowid" not in lowered
        # 认领条件在编译后是绑定参数，靠出现两次 jobs.status 确认外层重检仍在。
        assert lowered.count("jobs.status") >= 2


def test_verify_postgres_dump_zip(tmp_path):
    from bidcraft.compliance import ComplianceError, verify_backup

    dump = (
        "SET statement_timeout = 0;\n"
        "CREATE TABLE public.projects (\n    id character varying(36) NOT NULL\n);\n"
        "CREATE TABLE public.users (\n    id character varying(36) NOT NULL\n);\n"
        "CREATE TABLE public.jobs (\n    id character varying(36) NOT NULL\n);\n"
        "COPY public.projects (id, name) FROM stdin;\n"
        "abc\\t项目一\n"
        "def\\t项目二\n"
        "\\.\n"
    )
    archive = tmp_path / "pg-backup.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("dump.sql", dump)
        bundle.writestr(
            "manifest.json", json.dumps({"db_backend": "postgresql", "db_file": "dump.sql"})
        )

    report = verify_backup(archive)
    assert report["ok"] is True
    assert report["backend"] == "postgresql"
    assert report["tables"] == 3
    assert report["rows"] == 2

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as bundle:
        bundle.writestr("dump.sql", "CREATE TABLE public.projects (id integer);\n")
    with pytest.raises(ComplianceError, match="关键表"):
        verify_backup(broken)
