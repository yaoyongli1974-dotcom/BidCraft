"""审计盲点修复：管理端用户操作、登录/退出/改密与项目删除的审计留存。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _actions(project_id: str | None = None) -> list[str]:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as db:
        stmt = select(AuditLog.action).order_by(AuditLog.id)
        if project_id:
            stmt = stmt.where(AuditLog.project_id == project_id)
        return list(db.scalars(stmt))


def test_admin_user_lifecycle_is_audited_without_secrets(db):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    admin = _client()
    me = admin.get("/auth/me").json()["user"]
    response = admin.post(
        "/admin/users",
        json={
            "username": "audit-user",
            "display_name": "审计对象",
            "password": "super-secret-pass",
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]

    assert admin.patch(f"/admin/users/{user_id}", json={"role": "viewer"}).status_code == 200
    assert (
        admin.patch(f"/admin/users/{user_id}", json={"password": "another-secret-pass"})
        .status_code
        == 200
    )
    assert admin.patch(f"/admin/users/{user_id}", json={"active": False}).status_code == 200
    assert admin.delete(f"/admin/users/{user_id}").status_code == 200

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AuditLog).where(AuditLog.action.like("admin.user.%")).order_by(AuditLog.id)
            )
        )
    actions = [row.action for row in rows]
    for expected in (
        "admin.user.create",
        "admin.user.update",
        "admin.user.role",
        "admin.user.password",
        "admin.user.deactivate",
        "admin.user.delete",
    ):
        assert expected in actions
    # 口令/哈希绝不进入审计 detail
    assert all(
        "super-secret-pass" not in str(row.detail)
        and "another-secret-pass" not in str(row.detail)
        and "password_hash" not in str(row.detail)
        for row in rows
    )
    role_row = next(row for row in rows if row.action == "admin.user.role")
    assert role_row.detail["to"] == "viewer"
    assert role_row.detail["from"] == "manager"
    assert me["username"] == "admin"


def test_login_logout_and_password_change_are_audited(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/auth/login", json={"username": "admin", "password": "test-admin"}).status_code == 200
    actions = _actions()
    assert "auth.login_failed" in actions
    assert "auth.login" in actions

    registered = client.post(
        "/auth/register",
        json={"username": "sec-audit-user", "password": "Passw0rd!123", "accept_terms": True},
    )
    assert registered.status_code == 201, registered.text
    assert "auth.register" in _actions()

    # 改密：中间件记录成功事件；当前会话被吊销，需要重新登录。
    changed = client.post(
        "/auth/password",
        json={"current_password": "Passw0rd!123", "new_password": "Passw0rd!456"},
    )
    assert changed.status_code == 200, changed.text
    assert "auth.password_change" in _actions()
    assert client.post("/auth/logout").status_code == 401  # 会话已吊销

    fresh = TestClient(create_app())
    assert (
        fresh.post(
            "/auth/login", json={"username": "sec-audit-user", "password": "Passw0rd!456"}
        ).status_code
        == 200
    )
    assert fresh.post("/auth/logout").status_code == 200
    assert "auth.logout" in _actions()


def test_login_lockout_is_audited(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    for _ in range(8):
        assert (
            client.post("/auth/login", json={"username": "admin", "password": "wrong-pass"})
            .status_code
            == 401
        )
    assert (
        client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
        .status_code
        == 429
    )
    assert "auth.login_locked" in _actions()


def test_project_delete_detaches_audit_instead_of_deleting(db):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, Project
    from bidcraft.services.purge import ORPHAN_RULES, delete_project_rows

    with session_scope() as session:
        project = Project(name="审计留存项目", owner_id="owner-1")
        session.add(project)
        session.flush()
        session.add(
            AuditLog(
                project_id=project.id,
                user_id="owner-1",
                action="project.create",
                detail={"name": "审计留存项目"},
            )
        )
        session.flush()
        project_id = project.id
        removed = delete_project_rows(session, project_id)

    # 审计行不删除，只解绑项目外键；保留审计计数为 0，另记解绑数。
    assert removed["audit_logs"] == 0
    assert removed["audit_logs_detached"] == 1
    with session_scope() as session:
        row = session.scalar(
            select(AuditLog).where(AuditLog.action == "project.create")
        )
        assert row is not None
        assert row.project_id is None
        assert row.detail == {"name": "审计留存项目"}
        assert session.get(Project, project_id) is not None  # 本体由调用方删除
    assert (AuditLog, "project_id", Project, "id") not in ORPHAN_RULES
