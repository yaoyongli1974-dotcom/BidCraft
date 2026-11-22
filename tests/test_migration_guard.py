"""迁移锁的 fail-closed 语义（T07）。

用假的 engine/connection 注入 db.get_engine，直接观察 _pg_migration_guard 的行为：
- 咨询锁获取失败必须阻止迁移（抛错），绝不降级为本地文件锁；
- 成功时释放锁并关闭连接；
- body 抛错时仍然释放锁、关闭连接；
- SQLite 仍走文件锁（这里只验证分派，真实文件锁行为由 init_db 的既有用例覆盖）。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest


class _FakeConnection:
    def __init__(self, fail_lock: bool = False):
        self.fail_lock = fail_lock
        self.executed: list[str] = []
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        if "pg_advisory_lock" in sql and self.fail_lock:
            raise RuntimeError("permission denied for advisory lock")
        return self

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, connection: _FakeConnection):
        self._connection = connection

    def connect(self):
        return self._connection


def _patch_engine(monkeypatch, connection: _FakeConnection):
    from bidcraft import db

    monkeypatch.setattr(db, "get_engine", lambda: _FakeEngine(connection))


def test_pg_lock_failure_blocks_migration_without_file_lock_fallback(monkeypatch):
    """锁获取失败 → 阻止迁移；绝不调用文件锁兜底。"""

    from bidcraft import db

    conn = _FakeConnection(fail_lock=True)
    _patch_engine(monkeypatch, conn)

    called = {"file_guard": False}

    @contextmanager
    def _forbid_file_guard():
        called["file_guard"] = True
        yield

    monkeypatch.setattr(db, "_file_migration_guard", _forbid_file_guard)

    with pytest.raises(RuntimeError, match="咨询锁获取失败"), db._pg_migration_guard():
        pass  # 不应进入

    assert called["file_guard"] is False
    assert conn.closed is True
    assert not any("unlock" in sql for sql in conn.executed)


def test_pg_lock_success_releases_and_closes(monkeypatch):
    from bidcraft import db

    conn = _FakeConnection(fail_lock=False)
    _patch_engine(monkeypatch, conn)
    with db._pg_migration_guard():
        pass
    assert any("pg_advisory_lock" in sql for sql in conn.executed)
    assert any("pg_advisory_unlock" in sql for sql in conn.executed)
    assert conn.closed is True


def test_pg_lock_released_even_when_body_raises(monkeypatch):
    from bidcraft import db

    conn = _FakeConnection(fail_lock=False)
    _patch_engine(monkeypatch, conn)
    with pytest.raises(ValueError, match="boom"), db._pg_migration_guard():
        raise ValueError("boom")
    assert any("pg_advisory_unlock" in sql for sql in conn.executed)
    assert conn.closed is True


def test_migration_guard_dispatches_sqlite_to_file_lock(monkeypatch):
    """SQLite 仍走文件锁，与 PG 咨询锁路径分开。"""

    from bidcraft import db

    monkeypatch.setattr(db, "db_backend", lambda url: "sqlite" if url == "sqlite" else "postgresql")
    monkeypatch.setattr(db.settings, "db_url", "sqlite")

    seen = []

    @contextmanager
    def _file_guard():
        seen.append("file")
        yield

    @contextmanager
    def _pg_guard():
        seen.append("pg")
        yield

    monkeypatch.setattr(db, "_file_migration_guard", _file_guard)
    monkeypatch.setattr(db, "_pg_migration_guard", _pg_guard)
    with db._migration_guard():
        pass
    assert seen == ["file"]
