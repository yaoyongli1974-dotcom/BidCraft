"""Project membership, concurrency lock and audit trail (backend lane)."""

from __future__ import annotations

import itertools

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "collab-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _admin():
    return _login(*ADMIN)


def _make_user(admin, username: str, role: str = "manager") -> dict:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_project(client, name: str = "协作项目") -> dict:
    response = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _add_member(client, project_id: str, username: str, role: str = "editor"):
    return client.post(
        f"/projects/{project_id}/members",
        json={"username": username, "role": role},
    )


def _members(client, project_id: str) -> dict[str, str]:
    response = client.get(f"/projects/{project_id}/members")
    assert response.status_code == 200, response.text
    return {item["username"]: item["role"] for item in response.json()}


@pytest.fixture()
def team(db):
    admin = _admin()
    _make_user(admin, "collab-owner")
    _make_user(admin, "collab-editor")
    _make_user(admin, "collab-viewer")
    _make_user(admin, "collab-outsider")
    owner = _login("collab-owner")
    editor = _login("collab-editor")
    viewer = _login("collab-viewer")
    outsider = _login("collab-outsider")
    return {
        "admin": admin,
        "owner": owner,
        "editor": editor,
        "viewer": viewer,
        "outsider": outsider,
    }


def test_member_crud_and_permission_matrix(team):
    project = _make_project(team["owner"], "成员权限项目")
    project_id = project["id"]
    assert project["my_role"] == "owner"

    assert _add_member(team["owner"], project_id, "collab-editor", "editor").status_code == 201
    assert _add_member(team["owner"], project_id, "collab-viewer", "viewer").status_code == 201
    assert _members(team["owner"], project_id) == {
        "collab-owner": "owner",
        "collab-editor": "editor",
        "collab-viewer": "viewer",
    }

    # owner entry is listed first
    listed = team["owner"].get(f"/projects/{project_id}/members").json()
    assert listed[0]["role"] == "owner"

    # read is open to any member, writes follow the project role
    for client in (team["editor"], team["viewer"]):
        assert client.get(f"/projects/{project_id}").status_code == 200
    assert team["editor"].put(
        f"/projects/{project_id}/outline", json={"chapters": []}
    ).status_code == 200
    blocked = team["viewer"].put(
        f"/projects/{project_id}/outline", json={"chapters": []}
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "只读成员不能修改该项目"

    # a global manager without a membership grant sees nothing (404, not 403)
    assert team["outsider"].get(f"/projects/{project_id}").status_code == 404
    assert team["outsider"].put(
        f"/projects/{project_id}/outline", json={"chapters": []}
    ).status_code == 404

    # role changes take effect immediately
    updated = team["owner"].patch(
        f"/projects/{project_id}/members/{_user_id(team['admin'], 'collab-editor')}",
        json={"role": "viewer"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "viewer"
    assert team["editor"].put(
        f"/projects/{project_id}/outline", json={"chapters": []}
    ).status_code == 403

    # duplicate / unknown / invalid payloads
    assert _add_member(team["owner"], project_id, "collab-editor", "editor").status_code == 409
    assert _add_member(team["owner"], project_id, "collab-ghost", "editor").status_code == 404
    assert _add_member(team["owner"], project_id, "collab-owner", "editor").status_code == 409
    assert _add_member(team["owner"], project_id, "collab-viewer", "super_admin").status_code == 400

    removed = team["owner"].delete(
        f"/projects/{project_id}/members/{_user_id(team['admin'], 'collab-viewer')}"
    )
    assert removed.status_code == 200
    assert removed.json() == {"deleted": True}
    assert "collab-viewer" not in _members(team["owner"], project_id)
    assert team["viewer"].get(f"/projects/{project_id}").status_code == 404

    # the owner entry cannot be demoted or removed
    owner_id = _user_id(team["admin"], "collab-owner")
    assert team["owner"].patch(
        f"/projects/{project_id}/members/{owner_id}", json={"role": "viewer"}
    ).status_code == 400
    assert team["owner"].delete(
        f"/projects/{project_id}/members/{owner_id}"
    ).status_code == 400
    assert team["owner"].delete(
        f"/projects/{project_id}/members/{_user_id(team['admin'], 'collab-outsider')}"
    ).status_code == 404


def test_project_list_includes_member_projects(team):
    owned = _make_project(team["owner"], "负责人项目")
    shared = _make_project(team["owner"], "共享项目")
    other = _make_project(team["owner"], "未共享项目")
    assert _add_member(team["owner"], shared["id"], "collab-editor", "editor").status_code == 201
    assert _add_member(team["owner"], owned["id"], "collab-viewer", "viewer").status_code == 201

    editor_list = {
        item["id"]: item["my_role"] for item in team["editor"].get("/projects").json()
    }
    assert editor_list == {shared["id"]: "editor"}

    viewer_list = {
        item["id"]: item["my_role"] for item in team["viewer"].get("/projects").json()
    }
    assert viewer_list == {owned["id"]: "viewer"}

    assert team["editor"].get(f"/projects/{shared['id']}").json()["my_role"] == "editor"

    admin_list = {
        item["id"]: item["my_role"] for item in team["admin"].get("/projects").json()
    }
    assert admin_list[shared["id"]] == "admin"
    assert admin_list[other["id"]] == "admin"
    assert len([key for key in admin_list if key in {owned["id"], shared["id"], other["id"]}]) == 3


def test_audit_log_and_pagination(team):
    project = _make_project(team["owner"], "审计项目")
    project_id = project["id"]
    for username in ("collab-editor", "collab-viewer"):
        assert _add_member(team["owner"], project_id, username, "editor").status_code == 201

    first = team["owner"].get(f"/projects/{project_id}/audit?limit=2")
    assert first.status_code == 200
    payload = first.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["id"] > payload["items"][1]["id"]
    assert payload["next_before"] == payload["items"][-1]["id"]
    assert payload["items"][0]["action"] == "member.add"
    assert payload["items"][0]["username"] == "collab-owner"
    assert payload["items"][0]["detail"]["role"] == "editor"

    collected = list(payload["items"])
    cursor = payload["next_before"]
    while cursor is not None:
        page = team["owner"].get(
            f"/projects/{project_id}/audit?limit=2&before={cursor}"
        ).json()
        collected.extend(page["items"])
        cursor = page["next_before"]

    actions = [item["action"] for item in collected]
    assert actions.count("project.create") == 1
    assert actions.count("member.add") == 2
    assert len({item["id"] for item in collected}) == 3
    assert all(
        earlier["id"] > later["id"]
        for earlier, later in itertools.pairwise(collected)
    )

    # any member may read the project trail, outsiders cannot
    assert team["editor"].get(f"/projects/{project_id}/audit").status_code == 200
    assert team["outsider"].get(f"/projects/{project_id}/audit").status_code == 404


def test_chapter_revision_conflict_and_forced_overwrite(team, tender_path):
    owner = team["owner"]
    project = _make_project(owner, "并发章节项目")
    project_id = project["id"]
    with tender_path.open("rb") as handle:
        uploaded = owner.post(
            f"/projects/{project_id}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert uploaded.status_code == 200, uploaded.text
    outline = owner.post(f"/projects/{project_id}/outline", json={"mode": "package"})
    assert outline.status_code == 200, outline.text
    node_id = outline.json()["chapters"][0]["id"]

    detail = owner.get(f"/projects/{project_id}/chapters/{node_id}").json()
    assert detail["revision"] == 0

    saved = owner.put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": "第一版正文，编辑者甲。", "base_revision": 0},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1

    stale = owner.put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": "过期正文，编辑者乙。", "base_revision": 0},
    )
    assert stale.status_code == 409
    conflict = stale.json()
    assert conflict["detail"] == "章节已被他人修改，请载入最新或强制覆盖"
    assert conflict["current_revision"] == 1
    assert conflict["current_body"] == "第一版正文，编辑者甲。"
    assert conflict["current_words"] > 0

    # 强制覆盖: resend using the freshly reported revision
    forced = owner.put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": "第二版正文，编辑者乙。", "base_revision": conflict["current_revision"]},
    )
    assert forced.status_code == 200
    assert forced.json()["revision"] == 2
    assert forced.json()["body"] == "第二版正文，编辑者乙。"

    # omitting base_revision keeps the legacy behaviour but still bumps the token
    legacy = owner.put(
        f"/projects/{project_id}/chapters/{node_id}", json={"body": "第三版正文。"}
    )
    assert legacy.status_code == 200
    assert legacy.json()["revision"] == 3
    assert owner.get(f"/projects/{project_id}/chapters/{node_id}").json()["revision"] == 3

    # the audit trail records both saves
    actions = [
        item["action"]
        for item in owner.get(f"/projects/{project_id}/audit").json()["items"]
    ]
    assert actions.count("chapter.save") == 3


def test_only_owner_and_admin_manage_members_and_delete(team):
    project = _make_project(team["owner"], "负责人专属项目")
    project_id = project["id"]
    assert _add_member(team["owner"], project_id, "collab-editor", "editor").status_code == 201
    editor_id = _user_id(team["admin"], "collab-editor")

    forbidden = team["editor"].post(
        f"/projects/{project_id}/members",
        json={"username": "collab-viewer", "role": "viewer"},
    )
    assert forbidden.status_code == 403
    assert team["editor"].patch(
        f"/projects/{project_id}/members/{editor_id}", json={"role": "viewer"}
    ).status_code == 403
    assert team["editor"].delete(
        f"/projects/{project_id}/members/{editor_id}"
    ).status_code == 403
    assert team["outsider"].post(
        f"/projects/{project_id}/members",
        json={"username": "collab-viewer", "role": "viewer"},
    ).status_code == 404

    assert team["editor"].delete(f"/projects/{project_id}").status_code == 403
    # super administrators act as project admins
    assert team["admin"].delete(f"/projects/{project_id}").status_code == 200


def test_delete_project_cascades_members_and_audit(team):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, ProjectMember

    project = _make_project(team["owner"], "级联删除项目")
    project_id = project["id"]
    assert _add_member(team["owner"], project_id, "collab-editor", "editor").status_code == 201

    with session_scope() as session:
        assert session.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).all()
        assert session.scalars(
            select(AuditLog).where(AuditLog.project_id == project_id)
        ).all()

    assert team["owner"].delete(f"/projects/{project_id}").json()["deleted"] is True

    with session_scope() as session:
        assert (
            session.scalars(
                select(ProjectMember).where(ProjectMember.project_id == project_id)
            ).all()
            == []
        )
        assert (
            session.scalars(
                select(AuditLog).where(AuditLog.project_id == project_id)
            ).all()
            == []
        )
        # the deletion itself is still recorded, detached from the dead project
        deletion = session.scalars(
            select(AuditLog).where(AuditLog.action == "project.delete")
        ).all()
        assert [row.username for row in deletion] == ["collab-owner"]
        assert deletion[0].project_id is None
        assert deletion[0].detail["project_id"] == project_id


def test_admin_audit_requires_super_admin(team):
    project = _make_project(team["owner"], "全局审计项目")
    project_id = project["id"]
    assert _add_member(team["owner"], project_id, "collab-editor", "editor").status_code == 201

    assert team["owner"].get("/admin/audit").status_code == 403
    assert team["editor"].get("/admin/audit").status_code == 403

    response = team["admin"].get(f"/admin/audit?limit=5&project_id={project_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"]
    for item in payload["items"]:
        assert item["project_id"] == project_id
        assert item["project_name"] == "全局审计项目"
        assert "action" in item and "username" in item
        assert "detail" in item
    assert payload["items"][0]["action"] == "member.add"
    assert payload["next_before"] is None


def test_package_actions_are_audited_globally(team):
    admin = team["admin"]
    document = (
        "package: fire-collab-audit\n"
        "version: 1\n"
        "title: 协作审计规则包\n"
        "description: 审计用规则包\n"
        "project_type: fire\n"
        "project_type_label: 消防工程\n"
        "extends: [common-bid-facts]\n"
        "outline:\n"
        "  items:\n"
        "    - heading: 项目概述\n"
        "      intent: 概述项目范围\n"
        "checks:\n"
        "  - id: FIRE-001\n"
        '    assert: "True"\n'
        "    message: 占位核对项\n"
    )
    assert (
        admin.post("/admin/rule-packages", json={"document": document}).status_code == 201
    )
    updated = document.replace("title: 协作审计规则包", "title: 协作审计规则包（修订）")
    assert (
        admin.put(
            "/admin/rule-packages/fire-collab-audit", json={"document": updated}
        ).status_code
        == 200
    )
    assert admin.delete("/admin/rule-packages/fire-collab-audit").status_code == 200
    assert admin.post("/admin/rule-packages/fire-collab-audit/restore").status_code == 200

    items = admin.get("/admin/audit?limit=10").json()["items"]
    package_items = [item for item in items if item["action"].startswith("package.")]
    assert [item["action"] for item in package_items] == [
        "package.restore",
        "package.delete",
        "package.update",
        "package.create",
    ]
    for item in package_items:
        assert item["project_id"] is None
        assert item["project_name"] is None
        assert item["username"] == "admin"
    assert package_items[-1]["detail"] == {"name": "fire-collab-audit"}


def _user_id(admin, username: str) -> str:
    for item in admin.get("/admin/users").json():
        if item["username"] == username:
            return item["id"]
    raise AssertionError(f"user not found: {username}")
