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
    client, _ = _client("deleteuser")
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
    assert ok.json()["deleted"] is True and ok.json()["projects"] == 1
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
