from __future__ import annotations

import json
import sqlite3
import zipfile

from sqlalchemy import func, select
from typer.testing import CliRunner

from bidcraft.cli import app
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import Project


def _seed_project(name: str = "备份一致性测试") -> int:
    with session_scope() as db:
        db.add(Project(name=name))
    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(Project)) or 0


def _project_count(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        conn.close()


def test_backup_default_writes_consistent_zip(db, tmp_path):
    count = _seed_project()
    (settings.home / "secret.key").write_bytes(b"test-secret-key")

    result = CliRunner().invoke(app, ["backup"])

    assert result.exit_code == 0, result.output
    assert "备份完成" in result.output
    archives = list((settings.home / "backups").glob("bidcraft-*.zip"))
    assert len(archives) == 1
    archive = archives[0]

    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {"bidcraft.db", "secret.key", "manifest.json"} <= names
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        assert manifest["version"]
        assert manifest["created_at"]
        assert manifest["db_bytes"] > 0
        assert manifest["includes_secret_key"] is True
        assert manifest["bidcraft_home"] == str(settings.home)
        restored = tmp_path / "restored.db"
        restored.write_bytes(bundle.read("bidcraft.db"))

    assert _project_count(restored) == count
    conn = sqlite3.connect(str(restored))
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    # 原库未被备份过程破坏，数据仍可读。
    assert _project_count(settings.home / "bidcraft.db") == count


def test_backup_output_directory_autonames(db, tmp_path):
    _seed_project()
    target_dir = tmp_path / "custom-backups"

    result = CliRunner().invoke(app, ["backup", "-o", str(target_dir)])

    assert result.exit_code == 0, result.output
    archives = list(target_dir.glob("bidcraft-*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as bundle:
        assert "bidcraft.db" in bundle.namelist()
        assert "secret.key" not in bundle.namelist()
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        assert manifest["includes_secret_key"] is False


def test_backup_output_file_path(db, tmp_path):
    _seed_project()
    target = tmp_path / "manual.zip"

    result = CliRunner().invoke(app, ["backup", "--output", str(target)])

    assert result.exit_code == 0, result.output
    assert target.is_file()
    with zipfile.ZipFile(target) as bundle:
        assert "bidcraft.db" in bundle.namelist()


def test_backup_bad_output_path_exits_one(db, tmp_path):
    _seed_project()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    result = CliRunner().invoke(app, ["backup", "-o", str(blocker / "sub" / "x.zip")])

    assert result.exit_code == 1
    assert "备份失败" in result.output


def test_backup_list_shows_existing_archives(db):
    _seed_project()
    assert CliRunner().invoke(app, ["backup"]).exit_code == 0

    result = CliRunner().invoke(app, ["backup", "--list"])

    assert result.exit_code == 0, result.output
    assert "bidcraft-" in result.output


def test_backup_verify_command_is_registered(db):
    """回归：backup-verify 曾误绑到 backup-prune，真正的校验函数无装饰器。"""
    from bidcraft.cli import app

    _seed_project()
    made = CliRunner().invoke(app, ["backup"])
    assert made.exit_code == 0, made.output
    archive = next(iter((settings.home / "backups").glob("bidcraft-*.zip")))

    result = CliRunner().invoke(app, ["backup-verify", str(archive)])
    assert result.exit_code == 0, result.output
    assert "备份完整" in result.output
