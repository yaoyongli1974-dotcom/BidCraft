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


def _register(client, username="vipbuyer", password="buyer-pass-1"):
    response = client.post(
        "/auth/register",
        json={"username": username, "password": password, "display_name": "VIP 投标人"},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def _user_id(admin, username):
    users = admin.get("/admin/users").json()
    return next(item["id"] for item in users if item["username"] == username)


def test_admin_grant_and_clear_vip(db, monkeypatch):
    from fastapi.testclient import TestClient

    app = _app(monkeypatch)
    client = TestClient(app)
    user = _register(client)
    assert user["is_vip"] is False

    admin = TestClient(app)
    _admin(admin)
    uid = _user_id(admin, "vipbuyer")

    granted = admin.post(f"/admin/users/{uid}/vip", json={"days": 30, "note": "开通月卡"})
    assert granted.status_code == 200, granted.text
    assert granted.json()["user"]["is_vip"] is True
    assert granted.json()["user"]["vip_expires_at"]

    # extending stacks onto the existing expiry
    before = granted.json()["user"]["vip_expires_at"]
    again = admin.post(f"/admin/users/{uid}/vip", json={"days": 30})
    assert again.json()["user"]["vip_expires_at"] > before

    cleared = admin.post(f"/admin/users/{uid}/vip", json={"days": 0})
    assert cleared.status_code == 200
    assert cleared.json()["user"]["is_vip"] is False
    assert cleared.json()["user"]["vip_expires_at"] is None

    # audit recorded
    actions = [row["action"] for row in admin.get("/admin/audit").json()["items"]]
    assert "user.vip" in actions


def test_vip_order_extends_expiry_without_credits(db, monkeypatch):
    from fastapi.testclient import TestClient

    app = _app(monkeypatch)
    client = TestClient(app)
    _register(client)

    summary = client.get("/billing/summary").json()["summary"]
    assert summary["is_vip"] is False
    assert len(summary["vip_packages"]) == 3
    balance_before = summary["balance"]

    order = client.post("/billing/vip/orders", json={"package_id": "vip-month"})
    assert order.status_code == 201, order.text
    body = order.json()
    assert body["credits"] == 0
    assert body["amount_cents"] == 3000

    paid = client.post(f"/billing/orders/{body['id']}/mock-pay")
    assert paid.status_code == 200, paid.text
    assert paid.json()["summary"]["is_vip"] is True
    # VIP purchase must not add credits
    assert paid.json()["summary"]["balance"] == balance_before

    kinds = [t["kind"] for t in client.get("/billing/summary").json()["transactions"]]
    assert "vip" in kinds
    assert "recharge" not in kinds

    # unknown package rejected
    assert client.post("/billing/vip/orders", json={"package_id": "nope"}).status_code == 400


def test_vip_discount_and_queue_priority(db, monkeypatch):
    from fastapi.testclient import TestClient

    from bidcraft import billing
    from bidcraft.db import session_scope
    from bidcraft.models import User

    app = _app(monkeypatch)
    client = TestClient(app)
    _register(client)

    admin = TestClient(app)
    _admin(admin)
    uid = _user_id(admin, "vipbuyer")
    admin.post(f"/admin/users/{uid}/vip", json={"days": 30})

    with session_scope() as session:
        row = session.get(User, uid)
        assert billing.is_vip(row) is True
        assert billing.queue_priority(session, row) == 0
        # chapter_cost=1, count=4 -> 4, VIP 0.5 discount -> ceil(2)=2
        cost = billing.charge_chapters(session, row, 4, project_id="p1", note="生成")
        assert cost == 2
        assert row.credits == 3
        # refund returns to the same account
        billing.refund_chapters(session, row, 2, project_id="p1")
        assert row.credits == 5

    # non-VIP external user pays full price and normal priority
    other = TestClient(app)
    other_user = _register(other, "plainbuyer")
    with session_scope() as session:
        row = session.get(User, other_user["id"])
        assert billing.is_vip(row) is False
        assert billing.queue_priority(session, row) == 1
        cost = billing.charge_chapters(session, row, 4, project_id="p2", note="生成")
        assert cost == 4


def test_admin_billing_vip_settings_clamp(db, monkeypatch):
    from fastapi.testclient import TestClient

    app = _app(monkeypatch)
    admin = TestClient(app)
    _admin(admin)

    saved = admin.put(
        "/admin/billing",
        json={"vip_discount": 5.0, "sub_account_limit": 99},
    )
    assert saved.status_code == 200, saved.text
    settings = saved.json()["settings"]
    assert settings["vip_discount"] == 1.0
    assert settings["sub_account_limit"] == 20
    # untouched keys preserved
    assert len(settings["vip_packages"]) == 3

    low = admin.put("/admin/billing", json={"vip_discount": 0.01, "sub_account_limit": -3})
    assert low.json()["settings"]["vip_discount"] == 0.1
    assert low.json()["settings"]["sub_account_limit"] == 0

    bad = admin.put(
        "/admin/billing",
        json={"vip_packages": [{"id": "x", "name": "X", "days": 0, "amount_cents": 100}]},
    )
    assert bad.status_code == 400
