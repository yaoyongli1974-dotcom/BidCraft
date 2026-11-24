"""真实备份恢复演练（opt-in）。

`backup-verify` 只做结构校验（SQLite integrity_check / PG dump 解析 + 文件哈希），
不能证明密钥能解密、ORM 能读出数据。本用例补一次端到端 restore drill：

1. 用临时 BIDCRAFT_HOME 建库、写 secret.key 加密一段数据、放一个额外数据目录；
2. 跑 `bidcraft backup` 生成 zip；
3. 解压到一个**全新的 home**，用该目录的 secret.key 解密，并用 ORM 读回项目数据。

默认跳过，CI 不会碰真实数据也不会变慢。需要执行时设置环境变量::

    BIDCRAFT_RESTORE_DRILL=1 .venv/bin/python -m pytest -o addopts="" -q tests/test_fix3_backup_drill.py

演练只使用 pytest 的临时目录，不读写线上 BIDCRAFT_HOME。
"""

from __future__ import annotations

import os
import zipfile

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("BIDCRAFT_RESTORE_DRILL") != "1",
    reason="需要 BIDCRAFT_RESTORE_DRILL=1（恢复演练是 opt-in，默认跳过）",
)


def test_backup_restore_drill_decrypts_secret_and_reads_db(db, tmp_path, monkeypatch):
    import json
    import sqlite3

    from sqlalchemy import select
    from typer.testing import CliRunner

    from bidcraft import security
    from bidcraft.cli import app
    from bidcraft.compliance import verify_backup
    from bidcraft.config import settings
    from bidcraft.db import init_db, reset_engine_for_tests, session_scope
    from bidcraft.models import Project

    source_home = settings.home
    monkeypatch.setattr(settings, "secret_key", "")  # 强制使用 home 下的 secret.key
    monkeypatch.setattr(settings, "backup_data_dirs", "projects,packages,output,docs")

    # 1) 造源 home：DB + 用主密钥加密的串 + 额外数据目录。
    token = security.encrypt_secret("restore-drill-secret")
    assert token and (source_home / "secret.key").is_file()
    extra_dir = source_home / "mydata"
    extra_dir.mkdir(parents=True, exist_ok=True)
    (extra_dir / "note.txt").write_text("restore me", encoding="utf-8")
    with session_scope() as session:
        session.add(Project(name="恢复演练项目"))

    archive = tmp_path / "drill-backup.zip"
    made = CliRunner().invoke(
        app, ["backup", "--output", str(archive), "--include-dir", "mydata"]
    )
    assert made.exit_code == 0, made.output
    assert archive.is_file()
    report = verify_backup(archive)
    assert report["ok"] is True and report["backend"] == "sqlite"
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {"bidcraft.db", "secret.key", "manifest.json"} <= names
        assert "mydata/note.txt" in names
        manifest = json.loads(bundle.read("manifest.json"))
        assert any(item["path"] == "mydata/note.txt" for item in manifest["data_files"])

    # 2) 恢复到全新 home。
    restore_home = tmp_path / "restored-home"
    restore_home.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(restore_home)

    try:
        monkeypatch.setattr(settings, "home", restore_home)
        reset_engine_for_tests(f"sqlite+pysqlite:///{restore_home / 'bidcraft.db'}")
        init_db()

        # 3) 断言：密钥可解密（备份确实带回了同一把主密钥）。
        assert (restore_home / "secret.key").is_file()
        assert security.decrypt_secret(token) == "restore-drill-secret"
        # DB 可读：结构校验之外再用 ORM 读回业务数据。
        with session_scope() as session:
            names = list(session.scalars(select(Project.name)))
        assert "恢复演练项目" in names
        conn = sqlite3.connect(str(restore_home / "bidcraft.db"))
        try:
            assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            conn.close()
        assert (restore_home / "mydata" / "note.txt").read_text(encoding="utf-8") == "restore me"
    finally:
        monkeypatch.setattr(settings, "home", source_home)
        reset_engine_for_tests(f"sqlite+pysqlite:///{source_home / 'bidcraft.db'}")
