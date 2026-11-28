"""P1/P3：同步评估 finding、重复支付回调审计、清理解绑计数口径。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.audit import record_audit  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    Issue,
    Project,
    QualityFinding,
    RechargeOrder,
    User,
)
from bidcraft.services.purge import delete_project_rows  # noqa: E402


def _client(db):
    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert resp.status_code == 200, resp.text
    return client


def _project(client, name):
    resp = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_sync_evaluation_returns_real_findings(db):
    client = _client(db)
    pid = _project(client, "fix2-eval-findings")
    with session_scope() as sess:
        sess.add(
            Issue(
                project_id=pid,
                code="FIX2-BLOCK",
                level="blocking",
                category="核对项",
                location="",
                finding="同步评估阻断项",
                status="open",
            )
        )

    resp = client.post(f"/projects/{pid}/evaluation/run", json={"note": "fix2"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["findings"], "同步评估必须带回本次生成的 finding"
    with session_scope() as sess:
        count = sess.scalar(
            select(func.count())
            .select_from(QualityFinding)
            .where(QualityFinding.report_id == body["id"])
        )
    assert len(body["findings"]) == count
    # 与 /latest 的口径一致
    latest = client.get(f"/projects/{pid}/evaluation").json()["report"]
    assert {item["id"] for item in latest["findings"]} == {
        item["id"] for item in body["findings"]
    }


def test_duplicate_wechat_notify_writes_single_audit(db, monkeypatch):
    from bidcraft import wechatpay

    client = TestClient(create_app())  # create_app 确保初始管理员已落库
    with session_scope() as sess:
        user = sess.scalar(select(User).where(User.username == "admin"))
        sess.add(
            RechargeOrder(
                user_id=user.id,
                out_trade_no="FIX2-NOTIFY-1",
                amount_cents=8800,
                credits=88,
                status="pending",
                provider="wechat",
            )
        )

    payload = {
        "out_trade_no": "FIX2-NOTIFY-1",
        "transaction_id": "TX-FIX2-1",
        "trade_state": "SUCCESS",
        "amount": 8800,
        "currency": "CNY",
        "mchid": "1900000001",
        "appid": "wx-test-appid",
        "event_type": "TRANSACTION.SUCCESS",
        "raw": {},
    }
    monkeypatch.setattr(wechatpay, "parse_notification", lambda headers, body: payload)

    # 金额校验（上一轮修复）不能被本轮改动破坏。
    monkeypatch.setattr(
        wechatpay,
        "parse_notification",
        lambda headers, body: {**payload, "amount": 1},
    )
    assert client.post("/billing/wechat/notify", json={}).status_code == 400
    monkeypatch.setattr(wechatpay, "parse_notification", lambda headers, body: payload)

    for _ in range(2):
        resp = client.post("/billing/wechat/notify", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["code"] == "SUCCESS"

    with session_scope() as sess:
        rows = list(
            sess.scalars(
                select(AuditLog).where(
                    AuditLog.action == "billing.payment.notify",
                    AuditLog.detail["out_trade_no"].as_string() == "FIX2-NOTIFY-1",
                )
            )
        )
    assert len(rows) == 1
    assert rows[0].detail["amount_cents"] == 8800
    with session_scope() as sess:
        assert sess.scalar(
            select(RechargeOrder.status).where(RechargeOrder.out_trade_no == "FIX2-NOTIFY-1")
        ) == "paid"


def test_purge_counts_detached_separately(db):
    with session_scope() as sess:
        project = Project(name="fix2-purge")
        sess.add(project)
        sess.flush()
        pid = project.id
        record_audit(sess, pid, "fix2.purge.probe", {"kept": True})

    with session_scope() as sess:
        removed = delete_project_rows(sess, pid)

    assert removed["audit_logs"] == 0
    assert removed["audit_logs_detached"] >= 1
    with session_scope() as sess:
        assert (
            sess.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.project_id == pid)
            )
            == 0
        )
        # 审计行本身必须保留，供全局追责查询。
        assert (
            sess.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "fix2.purge.probe")
            )
            == 1
        )
