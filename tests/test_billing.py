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


def _register(client, username: str = "buyer1", password: str = "buyer-pass-1"):
    return client.post(
        "/auth/register",
        json={"username": username, "password": password, "display_name": "外部投标人", "accept_terms": True},
    )


def test_registration_credits_and_project_isolation(db, monkeypatch, tender_path):
    from fastapi.testclient import TestClient

    app = _app(monkeypatch)
    buyer = TestClient(app)
    response = _register(buyer)
    assert response.status_code == 201, response.text
    body = response.json()["user"]
    assert body["role"] == "manager"
    assert body["is_external"] is True
    assert body["credits"] == 5
    assert buyer.post("/auth/register", json={"username": "buyer1", "password": "12345678", "accept_terms": True}).status_code == 400

    created = buyer.post(
        "/projects", json={"name": "外部用户项目", "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert created.json()["owner_id"] == body["id"]

    other = TestClient(app)
    assert _register(other, "buyer2").status_code == 201
    assert other.get(f"/projects/{project_id}").status_code == 404
    assert other.get("/projects").json() == []

    admin = TestClient(app)
    _admin(admin)
    assert len(admin.get("/projects").json()) == 1


def test_billing_order_redeem_and_admin_adjust(db, monkeypatch):
    app = _app(monkeypatch)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert _register(client).status_code == 201

    summary = client.get("/billing/summary").json()
    assert summary["summary"]["balance"] == 5
    assert len(summary["summary"]["packages"]) == 3
    assert summary["wechat_pay_mock"] is True

    order = client.post("/billing/orders", json={"package_id": "starter"})
    assert order.status_code == 201, order.text
    order_body = order.json()
    assert order_body["amount_cents"] == 990
    assert order_body["credits"] == 10
    assert order_body["provider"] == "mock"

    paid = client.post(f"/billing/orders/{order_body['id']}/mock-pay")
    assert paid.status_code == 200, paid.text
    assert paid.json()["summary"]["balance"] == 15
    assert paid.json()["order"]["status"] == "paid"

    admin = TestClient(app)
    _admin(admin)
    codes = admin.post(
        "/admin/redeem-codes",
        json={"count": 2, "credits": 3, "amount_cents": 0, "days": 30},
    )
    assert codes.status_code == 201, codes.text
    code = codes.json()["codes"][0]

    redeemed = client.post("/billing/redeem", json={"code": code})
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["summary"]["balance"] == 18
    assert client.post("/billing/redeem", json={"code": code}).status_code == 400

    users = admin.get("/admin/users").json()
    buyer = next(item for item in users if item["username"] == "buyer1")
    adjusted = admin.post(
        f"/admin/users/{buyer['id']}/credits", json={"amount": 2, "note": "人工补偿"}
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["user"]["credits"] == 20

    billing = admin.get("/admin/billing").json()
    assert billing["stats"]["external_users"] == 1
    assert billing["stats"]["revenue_cents"] == 990
    assert len(billing["orders"]) == 1


def test_wechat_mock_login_creates_external_user(db, monkeypatch):
    app = _app(monkeypatch)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/auth/wechat/mock?next=/projects&openid=wx-demo-1", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/projects"
    me = client.get("/auth/me").json()["user"]
    assert me["is_external"] is True
    assert me["username"].startswith("wx_")
    assert me["credits"] == 5
    assert me["wechat_nickname"]

    again = TestClient(app)
    assert again.get("/auth/wechat/mock?openid=wx-demo-1", follow_redirects=False).status_code == 302
    assert again.get("/auth/me").json()["user"]["id"] == me["id"]


def test_wechat_start_reports_unconfigured(db, monkeypatch):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    monkeypatch.setattr("bidcraft.config.settings.wechat_mock", False)
    monkeypatch.setattr("bidcraft.config.settings.wechat_app_id", "")
    monkeypatch.setattr("bidcraft.config.settings.wechat_app_secret", "")
    monkeypatch.setattr("bidcraft.config.settings.wechat_redirect_uri", "")
    client = TestClient(create_app())
    assert client.get("/auth/wechat/start").status_code == 503


def test_charge_and_refund_helpers(db, monkeypatch):
    from sqlalchemy import select

    from bidcraft import billing
    from bidcraft.db import session_scope
    from bidcraft.models import User

    _app(monkeypatch)
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == "admin"))
    with session_scope() as session:
        session.add(
            User(
                username="credit-user",
                display_name="付费用户",
                password_hash="x",
                role="manager",
                active=True,
                is_external=True,
                credits=3,
            )
        )
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == "credit-user"))
        assert billing.charge_chapters(session, user, 2) == 2
        assert billing.summary(session, user)["balance"] == 1
        with pytest.raises(billing.InsufficientCredits):
            billing.charge_chapters(session, user, 2)
        billing.refund_chapters(session, user, 1)
        assert billing.summary(session, user)["balance"] == 2
