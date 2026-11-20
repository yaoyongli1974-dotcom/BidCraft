from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest

pytest.importorskip("fastapi")


def _client(username: str = "compuser"):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "comp-pass-1", "display_name": username},
    )
    assert response.status_code == 201, response.text
    return client, response.json()["user"]


def test_legal_documents(db):
    client, _ = _client("legaluser")
    for name in ("terms", "privacy"):
        payload = client.get(f"/legal/{name}").json()
        assert payload["name"] == name
        assert "BidCraft" in payload["text"]
    assert client.get("/legal/nope").status_code == 404


def test_account_export_contains_project(db):
    client, _ = _client("exportuser")
    created = client.post(
        "/projects", json={"name": "导出项目", "package": "weakcurrent-cabling"}
    ).json()
    response = client.get("/account/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["user"]["username"] == "exportuser"
    assert any(item["id"] == created["id"] for item in manifest["projects"])
    assert f"projects/{created['id']}.json" in archive.namelist()


def test_billing_profile_roundtrip(db):
    client, _ = _client("billprofile")
    assert client.get("/account/billing-profile").json() == {
        "title": "",
        "tax_no": "",
        "email": "",
    }
    saved = client.put(
        "/account/billing-profile",
        json={"title": "某某科技有限公司", "tax_no": "9133", "email": "a@b.c"},
    )
    assert saved.status_code == 200
    assert saved.json()["tax_no"] == "9133"
    assert client.get("/account/billing-profile").json()["title"] == "某某科技有限公司"


def test_statement_csv_has_rows(db):
    client, _ = _client("stmtuser")
    response = client.get("/billing/statement")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = response.content.decode("utf-8-sig")
    assert body.splitlines()[0] == "类型,时间,条目,数量,余额,备注"
    # 注册赠送会写一条 signup 流水
    assert "signup" in body


def test_account_delete_requires_confirm_and_password(db):
    client, user = _client("deleteuser")
    client.post("/projects", json={"name": "待删项目", "package": "weakcurrent-cabling"})
    assert (
        client.post(
            "/account/delete", json={"password": "comp-pass-1", "confirm": "删除"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/account/delete", json={"password": "wrong-pass", "confirm": "注销"}
        ).status_code
        == 400
    )
    ok = client.post(
        "/account/delete", json={"password": "comp-pass-1", "confirm": "注销"}
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["pending"] is True and body["days_left"] == 7
    # 冷静期内账号仍可用，且可以撤销
    assert client.get("/auth/me").status_code == 200
    state = client.get("/account/deletion").json()
    assert state["pending"] is True
    cancel = client.post("/account/delete/cancel")
    assert cancel.status_code == 200 and cancel.json()["pending"] is False
    # 再次申请后推进到到期，purge 才真正删除
    assert client.post(
        "/account/delete", json={"password": "comp-pass-1", "confirm": "注销"}
    ).status_code == 200
    from datetime import datetime

    from bidcraft import compliance
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, User

    with session_scope() as session:
        row = session.get(User, user["id"])
        row.deletion_due_at = datetime(2020, 1, 1)
    with session_scope() as session:
        result = compliance.purge_due_accounts(session)
        assert len(result["purged"]) == 1
        assert result["purged"][0]["removed"]["projects"] == 1
        rows = session.query(AuditLog).filter(AuditLog.action == "account.purge").all()
        assert rows and rows[0].username == "deleteuser"
        assert rows[0].detail["removed"]["projects"] == 1
    assert client.get("/auth/me").status_code in (401, 403)


def test_account_delete_blocked_by_sub_accounts(db):
    client, user = _client("parentuser")
    from datetime import datetime

    from bidcraft import accounts, billing
    from bidcraft.db import session_scope
    from bidcraft.models import User

    with session_scope() as session:
        row = session.get(User, user["id"])
        billing.add_credits(session, row.id, 100, kind="admin", note="test")
        row.vip_expires_at = datetime(2099, 1, 1)
        accounts.create_sub_account(session, row, username="subuser1", password="sub-pass-1")
    response = client.post(
        "/account/delete", json={"password": "comp-pass-1", "confirm": "注销"}
    )
    assert response.status_code == 400
    assert "子账号" in response.json()["detail"]


def test_verify_backup(tmp_path):
    from bidcraft.compliance import ComplianceError, verify_backup

    db_path = tmp_path / "ok.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE demo (id INTEGER)")
    conn.execute("INSERT INTO demo VALUES (1)")
    conn.commit()
    conn.close()
    report = verify_backup(db_path)
    assert report["ok"] is True and report["counts"]["demo"] == 1

    broken = tmp_path / "missing.db"
    with pytest.raises(ComplianceError):
        verify_backup(broken)


def test_deletion_needs_password_and_can_be_cancelled(db):
    client, _ = _client("cooluser")
    assert client.get("/account/deletion").json()["pending"] is False
    assert client.post("/account/delete/cancel").status_code == 400
    assert (
        client.post("/account/delete", json={"password": "", "confirm": "注销"}).status_code
        == 400
    )
    assert (
        client.post(
            "/account/delete", json={"password": "nope-pass", "confirm": "注销"}
        ).status_code
        == 400
    )
    first = client.post(
        "/account/delete", json={"password": "comp-pass-1", "confirm": "注销"}
    )
    assert first.status_code == 200
    again = client.post(
        "/account/delete", json={"password": "comp-pass-1", "confirm": "注销"}
    )
    assert again.status_code == 200 and again.json()["pending"] is True


def test_admin_purge_dry_run_and_super_only(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.db import session_scope
    from bidcraft.models import User

    admin = TestClient(create_app())
    client, user = _client("dryuser")
    assert client.post(
        "/account/delete", json={"password": "comp-pass-1", "confirm": "注销"}
    ).status_code == 200
    admin.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert client.post("/admin/accounts/purge").status_code in (401, 403)
    from datetime import datetime

    with session_scope() as session:
        session.get(User, user["id"]).deletion_due_at = datetime(2020, 1, 1)
    dry = admin.post("/admin/accounts/purge", params={"dry_run": True})
    assert dry.status_code == 200, dry.text
    assert [item["username"] for item in dry.json()["purged"]] == ["dryuser"]
    with session_scope() as session:
        assert session.get(User, user["id"]) is not None
    real = admin.post("/admin/accounts/purge")
    assert real.status_code == 200
    assert len(real.json()["purged"]) == 1
    with session_scope() as session:
        assert session.get(User, user["id"]) is None
