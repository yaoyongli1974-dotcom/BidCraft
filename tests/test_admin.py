from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _wait_job(client, job_id: str, timeout: float = 10.0) -> dict:
    status: dict = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed", "cancelled"}:
            return status
        time.sleep(0.02)
    raise AssertionError(status)


def test_login_and_rbac(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    anonymous = TestClient(create_app())
    assert anonymous.get("/projects").status_code == 401
    assert anonymous.get("/auth/me").status_code == 401

    admin = _client()
    assert admin.get("/auth/me").json()["user"]["role"] == "super_admin"

    created = admin.post(
        "/admin/users",
        json={
            "username": "viewer1",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201
    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "viewer1", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200
    assert viewer.get("/projects").status_code == 200
    blocked = viewer.post("/projects", json={"name": "不应创建", "package": "weakcurrent-cabling"})
    assert blocked.status_code == 403
    assert viewer.get("/admin/models").status_code == 403
    viewer_token = login.cookies.get("bidcraft_session")
    assert viewer_token
    assert viewer.post("/auth/logout").status_code == 200


def test_model_config_crud_and_secret_masking(db):
    client = _client()
    created = client.post(
        "/admin/models",
        json={
            "name": "生产对话模型",
            "kind": "chat",
            "provider": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "example-chat",
            "api_key": "sk-super-secret",
            "is_default": True,
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["has_api_key"] is True
    assert "sk-super-secret" not in str(payload)
    model_id = payload["id"]

    updated = client.patch(
        f"/admin/models/{model_id}",
        json={"model": "example-chat-v2", "api_key": ""},
    )
    assert updated.status_code == 200
    assert updated.json()["model"] == "example-chat-v2"
    assert updated.json()["has_api_key"] is True

    cleared = client.patch(f"/admin/models/{model_id}", json={"clear_api_key": True})
    assert cleared.json()["has_api_key"] is False

    image = client.post(
        "/admin/models",
        json={
            "name": "配图模型",
            "kind": "image",
            "provider": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "example-image",
            "is_default": True,
        },
    )
    assert image.status_code == 201
    assert image.json()["kind"] == "image"
    assert client.get("/admin/models").json()

    assert client.delete(f"/admin/models/{model_id}").json()["deleted"] is True


def test_local_knowledge_base_index_and_search(db, tmp_path):
    client = _client()
    root = tmp_path / "kb"
    root.mkdir()
    (root / "cabling.md").write_text(
        "综合布线系统采用 Cat6A 线缆，信息点测试执行 GB 50311-2016。", encoding="utf-8"
    )
    (root / "ba.md").write_text(
        "楼宇自控采用 BACnet 协议，DDC 控制器统一点位命名。", encoding="utf-8"
    )
    created = client.post(
        "/admin/kbs",
        json={
            "name": "公司技术资料",
            "kind": "local",
            "provider": "local",
            "location": str(root),
            "is_default": True,
        },
    )
    assert created.status_code == 201
    kb_id = created.json()["id"]

    indexed = client.post(f"/admin/kbs/{kb_id}/reindex")
    assert indexed.status_code == 202
    job = _wait_job(client, indexed.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["indexed"] == 2
    assert job["result"]["kb"]["chunk_count"] == 2
    assert job["result"]["vector"] is None

    tested = client.post(f"/admin/kbs/{kb_id}/test")
    assert tested.json()["ok"] is True

    from bidcraft import knowledge
    from bidcraft.db import session_scope

    with session_scope() as session:
        hits = knowledge.search(session, "综合布线 Cat6A 测试")
    assert hits
    assert "Cat6A" in hits[0].text

    updated = client.patch(f"/admin/kbs/{kb_id}", json={"name": "公司技术资料（更新）"})
    assert updated.status_code == 200, updated.text
    assert client.delete(f"/admin/kbs/{kb_id}").json()["deleted"] is True

    from sqlalchemy import select

    from bidcraft.models import AuditLog

    with session_scope() as session:
        actions = set(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.action.in_({"kb.create", "kb.update", "kb.delete"})
                )
            )
        )
    assert {"kb.create", "kb.update", "kb.delete"} <= actions


def test_user_management_guards(db):
    client = _client()
    admin = client.get("/auth/me").json()["user"]
    assert client.delete(f"/admin/users/{admin['id']}").status_code == 400
    assert (
        client.patch(f"/admin/users/{admin['id']}", json={"active": False}).status_code == 400
    )

    created = client.post(
        "/admin/users",
        json={
            "username": "manager1",
            "display_name": "投标经理",
            "password": "manager-pass-1",
            "role": "manager",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]
    promoted = client.patch(f"/admin/users/{user_id}", json={"role": "super_admin"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "super_admin"
    assert client.delete(f"/admin/users/{user_id}").status_code == 200


def test_manager_permissions_and_password_change(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    created = admin.post(
        "/admin/users",
        json={
            "username": "manager1",
            "display_name": "投标经理",
            "password": "manager-pass-1",
            "role": "manager",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    manager = TestClient(create_app())
    assert manager.post(
        "/auth/login", json={"username": "manager1", "password": "manager-pass-1"}
    ).status_code == 200
    assert manager.post(
        "/projects", json={"name": "经理创建项目", "package": "weakcurrent-cabling"}
    ).status_code == 201
    assert manager.get("/admin/models").status_code == 403

    changed = manager.post(
        "/auth/password",
        json={"current_password": "manager-pass-1", "new_password": "manager-pass-2"},
    )
    assert changed.status_code == 200
    assert changed.json()["relogin"] is True
    assert manager.get("/auth/me").status_code == 401
    fresh = TestClient(create_app())
    assert fresh.post(
        "/auth/login", json={"username": "manager1", "password": "manager-pass-2"}
    ).status_code == 200

    promoted = admin.patch(f"/admin/users/{user_id}", json={"role": "viewer"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "viewer"


def test_last_super_admin_cannot_be_removed(db):
    client = _client()
    me = client.get("/auth/me").json()["user"]
    assert client.delete(f"/admin/users/{me['id']}").status_code == 400
    assert client.patch(
        f"/admin/users/{me['id']}", json={"active": False}
    ).status_code == 400
    assert client.patch(
        f"/admin/users/{me['id']}", json={"role": "viewer"}
    ).status_code == 400


def test_default_chat_model_resolution(db, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "offline", False)
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.model_config import resolve_chat_endpoint
    from bidcraft.models import ModelConfig

    client = _client()
    first = client.post(
        "/admin/models",
        json={
            "name": "备用对话",
            "kind": "chat",
            "base_url": "http://127.0.0.1:9999/v1",
            "model": "fallback-chat",
            "is_default": True,
        },
    ).json()
    second = client.post(
        "/admin/models",
        json={
            "name": "主对话",
            "kind": "chat",
            "base_url": "http://127.0.0.1:9998/v1",
            "model": "main-chat",
            "is_default": True,
        },
    ).json()
    assert second["is_default"] is True

    with session_scope() as session:
        rows = list(session.scalars(select(ModelConfig).where(ModelConfig.kind == "chat")))
        assert sum(1 for row in rows if row.is_default) == 1
        endpoint = resolve_chat_endpoint(session)
        assert endpoint is not None
        assert endpoint.model_name == "main-chat"

    client.patch(f"/admin/models/{second['id']}", json={"enabled": False})
    client.patch(f"/admin/models/{first['id']}", json={"is_default": True})
    with session_scope() as session:
        endpoint = resolve_chat_endpoint(session)
        assert endpoint is not None
        assert endpoint.model_name == "fallback-chat"
