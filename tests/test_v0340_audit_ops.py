"""v0.34.0 P1：审计覆盖与运维硬化（真实 CLI / 真实 zip / 真实审计表）。"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile

import pytest

pytest.importorskip("fastapi")


def _admin_client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    assert (
        client.post(
            "/auth/login", json={"username": "admin", "password": "test-admin"}
        ).status_code
        == 200
    )
    return client


def _audit_rows(action_prefix: str) -> list:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as db:
        return list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.action.like(f"{action_prefix}%"))
                .order_by(AuditLog.id)
            )
        )


def test_model_config_changes_are_audited_without_secrets(db):
    client = _admin_client()
    secret = "sk-v0340-top-secret"
    created = client.post(
        "/admin/models",
        json={
            "name": "审计模型",
            "kind": "chat",
            "provider": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "audit-chat",
            "api_key": secret,
            "is_default": True,
        },
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert (
        client.patch(
            f"/admin/models/{model_id}", json={"model": "audit-chat-v2"}
        ).status_code
        == 200
    )
    assert client.delete(f"/admin/models/{model_id}").status_code == 200

    rows = _audit_rows("model.")
    actions = {row.action for row in rows}
    assert {"model.create", "model.update", "model.delete"} <= actions
    assert all(secret not in str(row.detail) for row in rows)
    assert all(row.username == "admin" for row in rows)


def test_billing_settings_redeem_and_credit_adjustments_are_audited(db):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import User

    client = _admin_client()
    with session_scope() as session:
        user_id = session.scalar(select(User.id).limit(1))

    before = client.get("/admin/billing").json()["settings"]
    payload = dict(before)
    payload["chapter_cost"] = 7
    payload["vip_discount"] = 0.33
    saved = client.put("/admin/billing", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["chapter_cost"] == 7

    codes = client.post(
        "/admin/redeem-codes",
        json={"count": 2, "credits": 5, "amount_cents": 100, "days": 30},
    )
    assert codes.status_code == 201, codes.text
    assert len(codes.json()["codes"]) == 2

    adjusted = client.post(
        f"/admin/users/{user_id}/credits", json={"amount": 3, "note": "审计补偿"}
    )
    assert adjusted.status_code == 200, adjusted.text

    rows = _audit_rows("billing.")
    actions = {row.action for row in rows}
    assert {"billing.settings", "billing.redeem", "billing.credit_adjust"} <= actions
    assert all(row.username == "admin" for row in rows)
    settings_row = next(row for row in rows if row.action == "billing.settings")
    assert "chapter_cost" in settings_row.detail and "vip_discount" in settings_row.detail
    redeem_row = next(row for row in rows if row.action == "billing.redeem")
    # 兑换码本身不入审计：它是可消费的凭证，详情只留批次与数量。
    assert all(code not in str(redeem_row.detail) for code in codes.json()["codes"])


def test_cli_reset_password_is_audited_without_secret(db):
    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.db import session_scope
    from bidcraft.models import User
    from bidcraft.security import hash_password

    with session_scope() as db_session:
        db_session.add(
            User(username="cli-reset-target", password_hash=hash_password("old-pass-1"))
        )
    password = "CliReset!2026"
    result = CliRunner().invoke(
        app, ["reset-password", "cli-reset-target", "--password", password]
    )
    assert result.exit_code == 0, result.output
    rows = _audit_rows("admin.user.password_reset")
    assert len(rows) == 1
    assert password not in str(rows[0].detail)
    assert "password_hash" not in str(rows[0].detail)


def test_admin_health_web_ready_and_health_ready_smoke(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    ready = client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["web_ready"] is True

    health = _admin_client().get("/admin/health")
    assert health.status_code == 200, health.text
    assert health.json()["web_ready"] is True


def test_statement_csv_has_explicit_order_columns(db):
    import csv
    from datetime import timedelta

    from bidcraft.compliance import statement_csv
    from bidcraft.db import session_scope
    from bidcraft.models import RechargeOrder, utcnow

    with session_scope() as session:
        session.add(
            RechargeOrder(
                user_id="v0340-csv-user",
                out_trade_no="V0340-ORDER-1",
                amount_cents=1234,
                credits=7,
                status="paid",
                provider="wechat",
                transaction_id="V0340-TX-1",
                created_at=utcnow() - timedelta(minutes=1),
            )
        )

    with session_scope() as session:
        body = statement_csv(session, "v0340-csv-user")
    lines = body.splitlines()
    order_header = next(line for line in lines if "金额（分）" in line)
    assert all(
        name in order_header
        for name in ("点数", "状态", "交易号", "支付渠道")
    )
    order_row = next(line for line in lines if "V0340-ORDER-1" in line)
    cells = next(csv.reader([order_row]))
    assert "1234" in cells
    assert "7" in cells
    assert "paid" in cells
    assert "V0340-TX-1" in cells
    assert "wechat" in cells


def test_backup_manifest_contains_db_and_secret_key_hashes(db):
    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.config import settings

    (settings.home / "secret.key").write_bytes(b"v0340-backup-secret")
    result = CliRunner().invoke(app, ["backup"])
    assert result.exit_code == 0, result.output
    archive = next(iter((settings.home / "backups").glob("bidcraft-*.zip")))
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        db_bytes = bundle.read("bidcraft.db")
        key_bytes = bundle.read("secret.key")
    assert manifest["db_sha256"] == hashlib.sha256(db_bytes).hexdigest()
    assert manifest["secret_key_sha256"] == hashlib.sha256(key_bytes).hexdigest()


def test_backup_verify_fails_closed_on_recorded_hash_mismatch(db, tmp_path):
    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.compliance import ComplianceError, verify_backup
    from bidcraft.config import settings

    (settings.home / "secret.key").write_bytes(b"v0340-verify-secret")
    assert CliRunner().invoke(app, ["backup"]).exit_code == 0
    archive = next(iter((settings.home / "backups").glob("bidcraft-*.zip")))
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["db_sha256"] = "0" * 64
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    with pytest.raises(ComplianceError):
        verify_backup(tampered)


def test_pg_dump_backup_uses_single_transaction(db, monkeypatch):
    """PG 备份必须是单事务一致快照；用假的 pg_dump 捕获真实命令行参数。"""

    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "db_url", "postgresql://u:p@127.0.0.1:5432/bidcraft")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/pg_dump")
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        dump = Path(cmd[cmd.index("-f") + 1])
        dump.write_text("-- fake dump\nCREATE TABLE projects (id text);\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = CliRunner().invoke(app, ["backup"])
    assert result.exit_code == 0, result.output
    assert "--single-transaction" in captured["cmd"]


@pytest.mark.skipif(
    os.getenv("BIDCRAFT_RESTORE_DRILL") != "1",
    reason="需要 BIDCRAFT_RESTORE_DRILL=1（恢复演练是 opt-in）",
)
def test_restore_drill_verifies_manifest_hashes_after_extract(db, tmp_path):
    """opt-in：备份解压后 DB/secret.key 的真实哈希必须与清单一致。"""

    import zipfile

    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.config import settings

    (settings.home / "secret.key").write_bytes(b"v0340-drill-key")
    result = CliRunner().invoke(app, ["backup"])
    assert result.exit_code == 0, result.output
    archive = next(iter((settings.home / "backups").glob("bidcraft-*.zip")))
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(tmp_path / "restored")
        manifest = json.loads((tmp_path / "restored" / "manifest.json").read_bytes())
    root = tmp_path / "restored"
    assert hashlib.sha256((root / "bidcraft.db").read_bytes()).hexdigest() == manifest[
        "db_sha256"
    ]
    assert hashlib.sha256((root / "secret.key").read_bytes()).hexdigest() == manifest[
        "secret_key_sha256"
    ]
