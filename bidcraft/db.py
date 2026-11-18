from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

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

    models.Base.metadata.create_all(get_engine())
    _migrate_columns(get_engine())


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
        "image_assets": [("options", "JSON")],
        "sections": [("revision", "INTEGER DEFAULT 0")],
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
