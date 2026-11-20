"""合规补齐：注册同意留痕、开票申请/开具、备份保留策略。"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from bidcraft import compliance
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import User


def _app(monkeypatch):
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "signup_credits", 0)
    from bidcraft.api import create_app

    app = create_app()
    app.state.testing = True
    return app


def _admin(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "admin", "password": settings.admin_password}
    )
    assert response.status_code == 200, response.text


def _register(client: TestClient, name: str = "buyer1", **extra):
    payload = {"username": name, "password": "Passw0rd!123", "display_name": name}
    payload.update(extra)
    return client.post("/auth/register", json=payload)


def test_register_records_terms_consent(db, monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        assert _register(client, "consent1").status_code == 201
        with session_scope() as session:
            user = session.query(User).filter(User.username == "consent1").one()
            assert user.terms_version == compliance.LEGAL_VERSION
            assert user.terms_accepted_at is not None
            payload = compliance.consent_payload(user)
            assert payload["up_to_date"] is True

        # 明确拒绝同意时不允许注册
        bad = _register(client, "consent2", accept_terms=False)
        assert bad.status_code == 400
        assert "同意" in bad.json()["detail"]


def test_legal_versions_and_privacy_retention(db, monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        versions = client.get("/legal/versions")
        assert versions.status_code == 200
        assert versions.json()["version"] == compliance.LEGAL_VERSION
        privacy = client.get("/legal/privacy").json()["text"]
        assert "备份与保留期限" in privacy
        assert "backup-prune" in privacy


def test_invoice_request_issue_download(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "home", tmp_path)
    app = _app(monkeypatch)
    with TestClient(app) as client:
        assert _register(client, "inv1").status_code == 201
        created = client.post(
            "/billing/invoices",
            json={
                "title": "某某科技有限公司",
                "tax_no": "91310000XXXX",
                "email": "fin@example.com",
                "amount_cents": 3990,
                "note": "6 月充值",
            },
        )
        assert created.status_code == 201, created.text
        invoice = created.json()
        assert invoice["status"] == "requested"
        assert invoice["status_label"] == "待开具"
        assert invoice["has_file"] is False
        assert client.get("/billing/invoices").json()[0]["id"] == invoice["id"]

        # 未开具时下载 404
        assert client.get(f"/billing/invoices/{invoice['id']}/file").status_code == 404

        # 普通用户不能开具
        assert client.post(
            f"/admin/invoices/{invoice['id']}/issue",
            data={"invoice_no": "NO-1"},
            files={"file": ("f.pdf", b"%PDF-1.4 demo", "application/pdf")},
        ).status_code in (403, 404)

        admin = TestClient(app)
        with admin:
            _admin(admin)
            queued = admin.get("/admin/invoices", params={"status": "requested"}).json()
            assert any(row["id"] == invoice["id"] for row in queued)
            assert queued[0]["username"]

            issued = admin.post(
                f"/admin/invoices/{invoice['id']}/issue",
                data={"invoice_no": "NO-2026-001"},
                files={"file": ("invoice.pdf", b"%PDF-1.4 demo", "application/pdf")},
            )
            assert issued.status_code == 200, issued.text
            assert issued.json()["status"] == "issued"
            assert issued.json()["invoice_no"] == "NO-2026-001"

        download = client.get(f"/billing/invoices/{invoice['id']}/file")
        assert download.status_code == 200
        assert download.content.startswith(b"%PDF")

        # 已开具不能再驳回
        admin2 = TestClient(app)
        with admin2:
            _admin(admin2)
            rejected = admin2.post(
                f"/admin/invoices/{invoice['id']}/reject", json={"reason": "重复申请"}
            )
            assert rejected.status_code == 400


def test_invoice_validation_and_reject(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "home", tmp_path)
    app = _app(monkeypatch)
    with TestClient(app) as client:
        assert _register(client, "inv2").status_code == 201
        assert client.post("/billing/invoices", json={"title": "", "amount_cents": 100}).status_code == 400
        assert client.post("/billing/invoices", json={"title": "x", "amount_cents": 0}).status_code == 400
        assert (
            client.post(
                "/billing/invoices",
                json={"title": "x", "amount_cents": 100, "email": "bad"},
            ).status_code
            == 400
        )
        row = client.post("/billing/invoices", json={"title": "抬头", "amount_cents": 100}).json()

        admin = TestClient(app)
        with admin:
            _admin(admin)
            assert admin.post(
                f"/admin/invoices/{row['id']}/issue",
                data={"invoice_no": "N1"},
                files={"file": ("f.exe", b"x", "application/octet-stream")},
            ).status_code == 400
            rejected = admin.post(
                f"/admin/invoices/{row['id']}/reject", json={"reason": "抬头与付款方不一致"}
            )
            assert rejected.status_code == 200
            assert rejected.json()["status"] == "rejected"
            assert "抬头" in rejected.json()["reject_reason"]
            # 驳回后不能再开具
            assert admin.post(
                f"/admin/invoices/{row['id']}/issue",
                data={"invoice_no": "N2"},
                files={"file": ("f.pdf", b"%PDF", "application/pdf")},
            ).status_code == 400


def test_prune_backups_keeps_newest(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "home", tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir(parents=True)
    base = datetime(2026, 1, 1)
    for index in range(12):
        path = folder / f"bidcraft-202601{index + 1:02d}-000000.zip"
        path.write_bytes(b"x")
        stamp = (base + timedelta(days=index)).timestamp()
        import os

        os.utime(path, (stamp, stamp))

    dry = compliance.prune_backups(3, dry_run=True)
    assert dry["total"] == 12
    assert len(dry["removed"]) == 9
    assert len(list(folder.glob("*.zip"))) == 12

    result = compliance.prune_backups(3)
    assert len(result["removed"]) == 9
    remaining = sorted(path.name for path in folder.glob("*.zip"))
    assert remaining == [
        "bidcraft-20260110-000000.zip",
        "bidcraft-20260111-000000.zip",
        "bidcraft-20260112-000000.zip",
    ]
    try:
        compliance.prune_backups(0)
    except compliance.ComplianceError:
        pass
    else:  # pragma: no cover - 保留份数必须为正
        raise AssertionError("keep=0 应当被拒绝")


def test_invoice_deleted_with_account(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "home", tmp_path)
    app = _app(monkeypatch)
    with TestClient(app) as client:
        assert _register(client, "inv3").status_code == 201
        client.post("/billing/invoices", json={"title": "抬头", "amount_cents": 100})
        with session_scope() as session:
            user = session.query(User).filter(User.username == "inv3").one()
            uid = user.id
            (tmp_path / "invoices" / uid).mkdir(parents=True, exist_ok=True)
            (tmp_path / "invoices" / uid / "x.pdf").write_bytes(b"%PDF")
            compliance.delete_account(session, user)
        assert not (tmp_path / "invoices" / uid).exists()
