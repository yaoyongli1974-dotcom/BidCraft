"""v0.34.0 P0：支付回调与模拟支付的边界防护（TEST FIRST）。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from bidcraft import wechatpay  # noqa: E402
from bidcraft.api import create_app  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import AuditLog, RechargeOrder, User  # noqa: E402

MCH_ID = "1900000001"
APP_ID = "wx-test-appid"
OUT_TRADE_NO = "V0340-NOTIFY-1"
AMOUNT = 8800
NOTIFY_PATH = "/billing/wechat/notify"
MAX_BODY = 1024 * 1024


def _payload(**overrides):
    data = {
        "out_trade_no": OUT_TRADE_NO,
        "transaction_id": "TX-V0340-1",
        "trade_state": "SUCCESS",
        "amount": AMOUNT,
        "currency": "CNY",
        "mchid": MCH_ID,
        "appid": APP_ID,
        "event_type": "TRANSACTION.SUCCESS",
        "raw": {},
    }
    data.update(overrides)
    return data


def _client_and_order(db, *, provider="wechat", amount=AMOUNT):
    client = TestClient(create_app())
    with session_scope() as sess:
        user = sess.scalar(select(User).where(User.username == "admin"))
        sess.add(
            RechargeOrder(
                user_id=user.id,
                out_trade_no=OUT_TRADE_NO,
                amount_cents=amount,
                credits=88,
                status="pending",
                provider=provider,
            )
        )
    return client


def _order_state(db):
    with session_scope() as sess:
        order = sess.scalar(
            select(RechargeOrder).where(RechargeOrder.out_trade_no == OUT_TRADE_NO)
        )
        return order.status, order.transaction_id or None, order.provider


def _order_id(db):
    with session_scope() as sess:
        return sess.scalar(
            select(RechargeOrder.id).where(RechargeOrder.out_trade_no == OUT_TRADE_NO)
        )


def _notify_audit_count(db):
    with session_scope() as sess:
        return sess.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "billing.payment.notify",
                AuditLog.detail["out_trade_no"].as_string() == OUT_TRADE_NO,
            )
        )


@pytest.fixture()
def pay_settings(tmp_path, monkeypatch):
    from bidcraft.config import settings

    key = tmp_path / "merchant.pem"
    cert = tmp_path / "platform.pem"
    key.write_bytes(b"not-a-real-key")
    cert.write_bytes(b"not-a-real-cert")
    monkeypatch.setattr(settings, "wechat_pay_mch_id", MCH_ID)
    monkeypatch.setattr(settings, "wechat_pay_serial_no", "MERCHANT-SERIAL-1")
    monkeypatch.setattr(settings, "wechat_pay_private_key", str(key))
    monkeypatch.setattr(settings, "wechat_pay_platform_cert", str(cert))
    monkeypatch.setattr(settings, "wechat_pay_platform_serial_no", "PLATFORM-SERIAL-1")
    monkeypatch.setattr(settings, "wechat_pay_apiv3_key", "0" * 32)
    monkeypatch.setattr(settings, "wechat_pay_notify_url", "https://example.com/notify")
    monkeypatch.setattr(settings, "wechat_app_id", APP_ID)
    monkeypatch.setattr(settings, "wechat_pay_mock", False)
    return settings


def test_configured_requires_platform_cert_and_serial(pay_settings, monkeypatch):
    assert wechatpay.configured() is True

    monkeypatch.setattr(pay_settings, "wechat_pay_platform_serial_no", "")
    assert wechatpay.configured() is False
    monkeypatch.setattr(pay_settings, "wechat_pay_platform_serial_no", "PLATFORM-SERIAL-1")

    monkeypatch.setattr(pay_settings, "wechat_pay_platform_cert", "")
    assert wechatpay.configured() is False
    monkeypatch.setattr(pay_settings, "wechat_pay_platform_cert", "/missing/platform.pem")
    assert wechatpay.configured() is False


def test_notify_rejects_oversized_body(db, monkeypatch):
    client = _client_and_order(db)

    def _must_not_parse(headers, body):
        raise AssertionError("oversized body must be rejected before parsing")

    monkeypatch.setattr(wechatpay, "parse_notification", _must_not_parse)
    resp = client.post(NOTIFY_PATH, content=b"x" * (MAX_BODY + 1))

    assert resp.status_code == 413
    assert resp.json()["code"] == "FAIL"
    assert _order_state(db)[0] == "pending"


def test_notify_accepts_body_at_size_limit(db, monkeypatch):
    client = _client_and_order(db)
    monkeypatch.setattr(wechatpay, "parse_notification", lambda headers, body: _payload())
    resp = client.post(NOTIFY_PATH, content=b"x" * MAX_BODY)
    assert resp.status_code == 200, resp.text


def test_notify_rejects_non_wechat_provider(db, monkeypatch):
    client = _client_and_order(db, provider="mock")
    monkeypatch.setattr(wechatpay, "parse_notification", lambda headers, body: _payload())

    resp = client.post(NOTIFY_PATH, json={})

    assert resp.status_code in {400, 403, 404}
    assert resp.json()["code"] == "FAIL"
    assert _order_state(db) == ("pending", None, "mock")
    assert _notify_audit_count(db) == 0


@pytest.mark.parametrize("state", ["CLOSED", "FAIL", "PAYERROR"])
def test_notify_acks_non_success_without_credit(db, monkeypatch, state):
    client = _client_and_order(db)
    monkeypatch.setattr(
        wechatpay, "parse_notification", lambda headers, body: _payload(trade_state=state)
    )

    resp = client.post(NOTIFY_PATH, json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == "SUCCESS"
    assert _order_state(db) == ("pending", None, "wechat")
    assert _notify_audit_count(db) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"out_trade_no": ""},
        {"out_trade_no": 123},
        {"transaction_id": ""},
        {"transaction_id": None},
        {"trade_state": "UNKNOWN"},
        {"amount": True},
        {"amount": 9900.0},
        {"amount": "8800"},
        {"amount": 0},
        {"amount": -1},
        {"currency": "USD"},
        {"currency": None},
        {"mchid": ""},
        {"appid": ""},
        {"event_type": "REFUND.SUCCESS"},
    ],
)
def test_notify_rejects_invalid_normalized_fields(db, monkeypatch, overrides):
    client = _client_and_order(db)
    monkeypatch.setattr(
        wechatpay, "parse_notification", lambda headers, body: _payload(**overrides)
    )

    resp = client.post(NOTIFY_PATH, json={})

    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "FAIL"
    assert _order_state(db) == ("pending", None, "wechat")
    assert _notify_audit_count(db) == 0


def test_notify_rejects_amount_mismatch(db, monkeypatch):
    client = _client_and_order(db)
    monkeypatch.setattr(
        wechatpay, "parse_notification", lambda headers, body: _payload(amount=AMOUNT + 1)
    )

    resp = client.post(NOTIFY_PATH, json={})

    assert resp.status_code == 400
    assert resp.json()["code"] == "FAIL"
    assert _order_state(db)[0] == "pending"
    assert _notify_audit_count(db) == 0


def test_notify_marks_paid_with_valid_payload(db, monkeypatch):
    client = _client_and_order(db)
    monkeypatch.setattr(wechatpay, "parse_notification", lambda headers, body: _payload())

    resp = client.post(NOTIFY_PATH, json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == "SUCCESS"
    assert _order_state(db) == ("paid", "TX-V0340-1", "wechat")
    assert _notify_audit_count(db) == 1


def test_mock_pay_rejects_wechat_order_even_when_mock_enabled(db, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_mock", True)
    client = _client_and_order(db, provider="wechat")
    client.post("/auth/login", json={"username": "admin", "password": "test-admin"})

    resp = client.post(f"/billing/orders/{_order_id(db)}/mock-pay")

    assert resp.status_code in {403, 404}
    assert _order_state(db) == ("pending", None, "wechat")


def test_mock_pay_still_requires_mock_setting(db, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_mock", False)
    client = _client_and_order(db, provider="mock")
    client.post("/auth/login", json={"username": "admin", "password": "test-admin"})

    resp = client.post(f"/billing/orders/{_order_id(db)}/mock-pay")

    assert resp.status_code == 404
    assert _order_state(db) == ("pending", None, "mock")


def test_mock_pay_still_works_for_mock_provider(db, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "wechat_pay_mock", True)
    client = _client_and_order(db, provider="mock")
    client.post("/auth/login", json={"username": "admin", "password": "test-admin"})

    resp = client.post(f"/billing/orders/{_order_id(db)}/mock-pay")

    assert resp.status_code == 200, resp.text
    assert _order_state(db)[0] == "paid"
