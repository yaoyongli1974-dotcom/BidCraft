"""P1：内置备份覆盖数据目录，backup-verify 不再对缺文件误报通过。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile

from sqlalchemy import select
from typer.testing import CliRunner

from bidcraft.cli import app
from bidcraft.compliance import ComplianceError, verify_backup
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import Project


def _seed_project(name="fix2-backup"):
    with session_scope() as db:
        db.add(Project(name=name))
    with session_scope() as db:
        return db.scalar(select(Project.id).order_by(Project.created_at.desc()))


def _make_backup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backup_data_dirs", "projects,packages,output,docs")
    pid = _seed_project()
    files = {
        f"projects/{pid}/output/bid.docx": b"fake-docx",
        "docs/fix2-note.md": b"# note\nkeep me\n",
        "packages/custom.yaml": b"name: custom\n",
        "output/batch.zip": b"zip-bytes",
    }
    for rel, content in files.items():
        target = settings.home / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (settings.home / "secret.key").write_bytes(b"test-secret-key")

    result = CliRunner().invoke(app, ["backup", "--output", str(tmp_path / "backup.zip")])
    assert result.exit_code == 0, result.output
    return tmp_path / "backup.zip", files


def _rewrite_zip(source, target, *, skip=(), replace=None):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for item in original.infolist():
            if item.filename in skip:
                continue
            data = original.read(item.filename)
            if replace and item.filename in replace:
                data = replace[item.filename]
            out.writestr(item.filename, data)


def test_backup_includes_data_dirs_and_verifies(db, monkeypatch, tmp_path):
    archive, files = _make_backup(monkeypatch, tmp_path)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {"bidcraft.db", "secret.key", "manifest.json"} <= names
        for rel in files:
            assert rel in names
            assert bundle.read(rel) == files[rel]
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["data_files"]
        assert {entry["path"] for entry in manifest["data_files"]} == set(files)
        for entry in manifest["data_files"]:
            assert entry["sha256"] == hashlib.sha256(files[entry["path"]]).hexdigest()
            assert entry["bytes"] == len(files[entry["path"]])

    report = verify_backup(archive)
    assert report["ok"] is True
    assert report["data_files"]["checked"] == len(files)
    result = CliRunner().invoke(app, ["backup-verify", str(archive)])
    assert result.exit_code == 0, result.output
    assert "数据文件" in result.output


def test_verify_rejects_missing_manifest_file(db, monkeypatch, tmp_path):
    archive, _files = _make_backup(monkeypatch, tmp_path)
    missing = "docs/fix2-note.md"
    broken = tmp_path / "missing.zip"
    _rewrite_zip(archive, broken, skip=(missing,))
    try:
        verify_backup(broken)
    except ComplianceError as exc:
        assert "数据文件" in str(exc)
    else:
        raise AssertionError("清单里的文件缺失时 backup-verify 必须失败")
    result = CliRunner().invoke(app, ["backup-verify", str(broken)])
    assert result.exit_code == 2
    assert "数据文件" in result.output


def test_verify_rejects_hash_mismatch(db, monkeypatch, tmp_path):
    archive, _files = _make_backup(monkeypatch, tmp_path)
    tampered = tmp_path / "tampered.zip"
    _rewrite_zip(archive, tampered, replace={"packages/custom.yaml": b"name: evil\n"})
    try:
        verify_backup(tampered)
    except ComplianceError as exc:
        assert "哈希" in str(exc)
    else:
        raise AssertionError("数据文件被篡改时 backup-verify 必须失败")


def test_old_backup_without_data_manifest_is_readable(db, monkeypatch, tmp_path):
    archive, _files = _make_backup(monkeypatch, tmp_path)
    legacy = tmp_path / "legacy.zip"
    # 旧版备份只有数据库，没有 manifest.json / data_files。
    _rewrite_zip(archive, legacy, skip=("manifest.json",))
    report = verify_backup(legacy)
    assert report["ok"] is True
    assert report["backend"] == "sqlite"
    assert "data_files" not in report
    with zipfile.ZipFile(legacy) as bundle:
        restored = tmp_path / "restored.db"
        restored.write_bytes(bundle.read("bidcraft.db"))
    conn = sqlite3.connect(str(restored))
    try:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 1
    finally:
        conn.close()
