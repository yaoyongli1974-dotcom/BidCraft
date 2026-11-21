from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

try:  # 服务端是 Linux；桌面端已冻结，Windows 下降级为无锁
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

logger = logging.getLogger("bidcraft.db")

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _configure_sqlite(engine) -> None:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_engine():
    global _engine
    if _engine is None:
        settings.home.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(settings.db_url, future=True)
        _configure_sqlite(_engine)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


def init_db() -> None:
    from . import models

    engine = get_engine()
    with _migration_guard():
        # 1) 历史兼容：建缺失表 + 补 0.8.0 之前遗留的列
        models.Base.metadata.create_all(engine)
        _migrate_columns(engine)
        # 2) 新 schema 变更统一走 Alembic revision（幂等，见 0001_baseline）
        _run_alembic(engine)


@contextmanager
def _migration_guard() -> Iterator[None]:
    """跨进程串行化 schema 变更：serve / CLI / MCP 可能同时启动。"""
    if fcntl is None:  # pragma: no cover - Windows
        yield
        return
    lock = settings.home / ".migration.lock"
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_alembic(engine) -> None:
    """把库带入 Alembic 记账：老库 stamp 基线，再执行后续 revision。"""
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parent / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current is None:
        # 旧机制已把库收敛到基线 schema，这里只记账，不重复执行基线的 DDL。
        logger.info("数据库首次纳入 Alembic 管理，stamp 到 0001_baseline")
        command.stamp(cfg, "0001_baseline")
    command.upgrade(cfg, "head")
    # Alembic 用独立连接执行 DDL；回收应用连接池，避免缓存旧 schema。
    engine.dispose()


def _migrate_columns(engine) -> None:
    """Additive migrations for SQLite deployments created before a release.

    The project intentionally avoids a migration framework; these are simple,
    idempotent column additions. New tables are handled by ``create_all``.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    additions: dict[str, list[tuple[str, str]]] = {
        "projects": [
            ("owner_id", "VARCHAR(36)"),
            ("is_template", "BOOLEAN NOT NULL DEFAULT 0"),
        ],
        "users": [
            ("credits", "INTEGER DEFAULT 0"),
            ("is_external", "BOOLEAN DEFAULT FALSE"),
            ("wechat_openid", "VARCHAR(128)"),
            ("wechat_unionid", "VARCHAR(128)"),
            ("wechat_nickname", "VARCHAR(120) DEFAULT ''"),
            ("avatar_url", "VARCHAR(500) DEFAULT ''"),
            ("vip_expires_at", "DATETIME"),
            ("parent_id", "VARCHAR(36)"),
            ("deletion_requested_at", "DATETIME"),
            ("deletion_due_at", "DATETIME"),
            ("terms_version", "VARCHAR(20) DEFAULT ''"),
            ("terms_accepted_at", "DATETIME"),
        ],
        "jobs": [("priority", "INTEGER DEFAULT 1")],
        "boq_snapshots": [("prices", "JSON"), ("package_id", "VARCHAR(36)")],
        "boq_quote_versions": [
            ("status", "VARCHAR(16) DEFAULT 'draft'"),
            ("submit_note", "VARCHAR(200) DEFAULT ''"),
            ("submitted_by", "VARCHAR(80) DEFAULT ''"),
            ("submitted_at", "DATETIME"),
            ("review_note", "VARCHAR(200) DEFAULT ''"),
            ("reviewed_by", "VARCHAR(80) DEFAULT ''"),
            ("reviewed_at", "DATETIME"),
            ("approvals", "TEXT NOT NULL DEFAULT '[]'"),
        ],
        "plugins": [
            ("fail_streak", "INTEGER DEFAULT 0"),
            ("auto_disabled", "BOOLEAN DEFAULT 0"),
            ("mem_limit_mb", "INTEGER DEFAULT 512"),
            ("cpu_seconds", "INTEGER DEFAULT 10"),
        ],
        "scoring_items": [
            ("actual_score", "FLOAT"),
            ("actual_bidder", "VARCHAR(80) DEFAULT ''"),
            ("score_note", "VARCHAR(200) DEFAULT ''"),
        ],
        "image_assets": [("options", "JSON")],
        "issues": [
            ("node_id", "VARCHAR(36)"),
            ("checked_at", "DATETIME"),
            ("resolved_at", "DATETIME"),
            ("resolved_by", "VARCHAR(80)"),
            ("resolution_note", "TEXT DEFAULT ''"),
        ],
        "requirements": [
            ("marker", "VARCHAR(8) DEFAULT ''"),
            ("response_status", "VARCHAR(16) DEFAULT 'pending'"),
            ("response_note", "TEXT DEFAULT ''"),
            ("responded_by", "VARCHAR(80)"),
            ("responded_at", "DATETIME"),
            ("section_id", "VARCHAR(36)"),
            ("node_id", "VARCHAR(36)"),
            ("auto_covered", "BOOLEAN DEFAULT 0"),
        ],
        "sections": [
            ("revision", "INTEGER DEFAULT 0"),
            ("review_status", "VARCHAR(16) DEFAULT 'draft'"),
            ("review_note", "TEXT DEFAULT ''"),
            ("review_requested_at", "DATETIME"),
            ("review_requested_by", "VARCHAR(80)"),
            ("confirmed_at", "DATETIME"),
            ("confirmed_by", "VARCHAR(80)"),
            ("locked_at", "DATETIME"),
            ("locked_by", "VARCHAR(80)"),
        ],
        "kb_chunks": [
            ("embedding", "JSON"),
            ("embed_key", "VARCHAR(200) DEFAULT ''"),
            ("embed_dim", "INTEGER DEFAULT 0"),
        ],
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # Older SQLite files gain the column through ALTER TABLE, which cannot
        # create the model's index; keep the lookup indexed there too.
        if "boq_snapshots" in tables:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_boq_snapshots_package_id "
                    "ON boq_snapshots (package_id)"
                )
            )


@contextmanager
def session_scope() -> Iterator[Session]:
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_engine_for_tests(db_url: str) -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(db_url, future=True)
    _configure_sqlite(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
