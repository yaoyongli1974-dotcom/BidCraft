"""v0.32.1：审批链规则条件经 PATCH /projects/{ref} 的用户可见路径回显。

验证用户在审批链弹窗保存的 `rules` 不会在服务端归一化阶段被静默丢弃，
非法规则在入口被裁剪；无 mock，使用真实 TestClient 与 SQLite 会话。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

MEMBER_PASS = "member-pass-1"


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def _member(admin, username: str, display_name: str) -> str:
    created = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": display_name,
            "password": MEMBER_PASS,
            "role": "manager",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_project_patch_roundtrips_normalized_approval_rules(db):
    client = _client()
    u1 = _member(client, "v032-rule-api-1", "规则审批一")
    u2 = _member(client, "v032-rule-api-2", "规则审批二")
    created = client.post("/projects", json={"name": "规则回显项目"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    chain = [
        {
            "approvers": [u1],
            "mode": "all",
            "min_amount": 100,
            "rules": [
                {"field": "boq_rows", "op": "gte", "value": 10},
                {"field": "priced", "op": "gte", "value": 8},
                {"field": "currency", "op": "in", "value": ["CNY", "USD"]},
                {"field": "unknown", "op": "gte", "value": 1},
                {"field": "boq_rows", "op": "bad-op", "value": 1},
                {"field": "total_amount", "op": "gte", "value": True},
            ],
        },
        {"approvers": [u2], "mode": "any"},
    ]
    patched = client.patch(f"/projects/{project_id}", json={"approval_chain": chain})
    assert patched.status_code == 200, patched.text

    body = client.get(f"/projects/{project_id}").json()
    stored = body["approval_chain"]
    assert len(stored) == 2
    assert stored[0]["mode"] == "all"
    assert stored[0]["min_amount"] == 100
    assert stored[0]["rules"] == [
        {"field": "boq_rows", "op": "gte", "value": 10},
        {"field": "priced", "op": "gte", "value": 8},
        {"field": "currency", "op": "in", "value": ["CNY", "USD"]},
    ]
    assert stored[0]["approvers"][0]["name"] == "规则审批一"
    assert "rules" not in stored[1]
