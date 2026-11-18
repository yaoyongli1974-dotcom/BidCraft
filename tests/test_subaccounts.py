from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _app(monkeypatch):
    from bidcraft.api import create_app

    monkeypatch.setattr("bidcraft.config.settings.wechat_mock", True)
    monkeypatch.setattr("bidcraft.config.settings.wechat_pay_mock", True)
    monkeypatch.setattr("bidcraft.config.settings.signup_credits", 5)
    monkeypatch.setattr("bidcraft.config.settings.chapter_cost", 1)
    return create_app()


def _admin(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _register(client, username, password="buyer-pass-1"):
    response = client.post(
        "/auth/register",
        json={"username": username, "password": password, "display_name": "主账号"},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def _vip_parent(app, monkeypatch, username="bosshost"):
    from fastapi.testclient import TestClient

    parent = TestClient(app)
    user = _register(parent, username)
    admin = TestClient(app)
    _admin(admin)
    uid = next(i["id"] for i in admin.get("/admin/users").json() if i["username"] == username)
    admin.post(f"/admin/users/{uid}/vip", json={"days": 30})
    return parent, user


def test_subaccount_requires_vip_and_inherits_billing(db, monkeypatch):
    from fastapi.testclient import TestClient

    app = _app(monkeypatch)
    parent = TestClient(app)
    _register(parent, "novip")
    # no VIP yet -> cannot create sub-account
    assert parent.post(
        "/account/sub-accounts",
        json={"username": "worker1", "password": "worker-pass-1"},
    ).status_code == 400

    admin = TestClient(app)
    _admin(admin)
    uid = next(i["id"] for i in admin.get("/admin/users").json() if i["username"] == "novip")
    admin.post(f"/admin/users/{uid}/vip", json={"days": 30})

    created = parent.post(
        "/account/sub-accounts",
        json={"username": "worker1", "password": "worker-pass-1", "display_name": "员工一"},
    )
    assert created.status_code == 201, created.text
    sub = created.json()
    assert sub["username"] == "worker1"
    assert sub["active"] is True

    # sub-account logs in and sees the parent's balance / VIP
    worker = TestClient(app)
    login = worker.post("/auth/login", json={"username": "worker1", "password": "worker-pass-1"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["is_sub"] is True
    summary = worker.get("/billing/summary").json()["summary"]
    assert summary["is_sub"] is True
    assert summary["parent_username"] == "novip"
    assert summary["is_vip"] is True
    assert summary["balance"] == 5  # parent's signup credits

    # sub-account cannot recharge / redeem / buy VIP
    assert worker.post("/billing/orders", json={"package_id": "starter"}).status_code == 403
    assert worker.post("/billing/redeem", json={"code": "XXXX"}).status_code == 403
    assert worker.post("/billing/vip/orders", json={"package_id": "vip-month"}).status_code == 403

    # sub-account cannot nest sub-accounts
    assert worker.post(
        "/account/sub-accounts",
        json={"username": "worker2", "password": "worker-pass-2"},
    ).status_code == 400


def test_subaccount_charge_deducts_parent(db, monkeypatch):
    from fastapi.testclient import TestClient

    from bidcraft import billing
    from bidcraft.db import session_scope
    from bidcraft.models import User

    app = _app(monkeypatch)
    parent, parent_user = _vip_parent(app, monkeypatch)
    parent.post(
        "/account/sub-accounts",
        json={"username": "worker1", "password": "worker-pass-1"},
    )
    worker = TestClient(app)
    worker.post("/auth/login", json={"username": "worker1", "password": "worker-pass-1"})
    sub_id = worker.get("/auth/me").json()["user"]["id"]

    with session_scope() as session:
        sub = session.get(User, sub_id)
        # VIP parent, chapter_cost=1, count=2 -> ceil(2*0.5)=1 charged to parent
        cost = billing.charge_chapters(session, sub, 2, project_id="p1", note="生成")
        assert cost == 1
        parent_row = session.get(User, parent_user["id"])
        assert parent_row.credits == 4
        assert sub.credits == 0  # sub never holds credits
        tx = billing.transactions(session, parent_user["id"])
        assert any("子账号 worker1" in t["note"] for t in tx)


def test_subaccount_limit_update_and_delete_transfer(db, monkeypatch):
    from fastapi.testclient import TestClient

    app = _app(monkeypatch)
    parent, parent_user = _vip_parent(app, monkeypatch, username="limitboss")

    admin = TestClient(app)
    _admin(admin)
    admin.put("/admin/billing", json={"sub_account_limit": 1})

    first = parent.post(
        "/account/sub-accounts", json={"username": "wkr1", "password": "worker-pass-1"}
    )
    assert first.status_code == 201
    over = parent.post(
        "/account/sub-accounts", json={"username": "wkr2", "password": "worker-pass-2"}
    )
    assert over.status_code == 400  # limit reached

    sub_id = first.json()["id"]
    listed = parent.get("/account/sub-accounts").json()
    assert [s["id"] for s in listed] == [sub_id]

    updated = parent.patch(f"/account/sub-accounts/{sub_id}", json={"display_name": "改名"})
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "改名"
    disabled = parent.patch(f"/account/sub-accounts/{sub_id}", json={"active": False})
    assert disabled.json()["active"] is False

    # sub owns a project; deleting transfers ownership to the parent
    worker = TestClient(app)
    worker.post("/auth/login", json={"username": "wkr1", "password": "worker-pass-1"})
    # disabled account cannot login; re-enable first
    parent.patch(f"/account/sub-accounts/{sub_id}", json={"active": True})
    worker.post("/auth/login", json={"username": "wkr1", "password": "worker-pass-1"})
    proj = worker.post("/projects", json={"name": "员工项目", "package": "weakcurrent-cabling"})
    assert proj.status_code == 201
    project_id = proj.json()["id"]

    deleted = parent.delete(f"/account/sub-accounts/{sub_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": sub_id}
    assert parent.get("/account/sub-accounts").json() == []
    # project now belongs to the parent
    assert parent.get(f"/projects/{project_id}").status_code == 200
    assert parent.get(f"/projects/{project_id}").json()["owner_id"] == parent_user["id"]

    # operating on a foreign / missing sub-account -> 404
    assert parent.patch(f"/account/sub-accounts/{sub_id}", json={"active": True}).status_code == 404
