"""章节责任分工：CRUD、成员校验、权限与审计。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "assign-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_user(admin, username: str) -> None:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text


def _user_id(admin, username: str) -> str:
    for item in admin.get("/admin/users").json():
        if item["username"] == username:
            return item["id"]
    raise AssertionError(f"user not found: {username}")


def _seed_chapter(project_id: str, heading: str, position: int = 1) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading=heading, position=position)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="", status="pending", words=0))
        session.flush()
        return node.id


@pytest.fixture()
def team(db):
    admin = _login(*ADMIN)
    for username in ("as-owner", "as-editor", "as-viewer", "as-outsider"):
        _make_user(admin, username)
    owner = _login("as-owner")
    project = owner.post(
        "/projects", json={"name": "分工项目", "package": "weakcurrent-cabling"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    for username, role in (("as-editor", "editor"), ("as-viewer", "viewer")):
        assert owner.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": role},
        ).status_code == 201
    return {
        "admin": admin,
        "owner": owner,
        "editor": _login("as-editor"),
        "viewer": _login("as-viewer"),
        "outsider": _login("as-outsider"),
        "project_id": project_id,
        "node1": _seed_chapter(project_id, "总体方案", 1),
        "node2": _seed_chapter(project_id, "技术方案", 2),
    }


def _assign(team, node_id: str, assignee: str, **extra):
    return team["owner"].post(
        f"/projects/{team['project_id']}/assignments",
        json={"node_id": node_id, "assignee_id": assignee, **extra},
    )


def _list(client, project_id: str):
    response = client.get(f"/projects/{project_id}/assignments")
    assert response.status_code == 200, response.text
    return response.json()["assignments"]


def test_assignment_crud_and_status_flow(team):
    editor_id = _user_id(team["admin"], "as-editor")
    created = _assign(
        team,
        team["node1"],
        editor_id,
        deadline="2026-12-31T18:00:00+08:00",
        note="负责总体方案",
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["node_id"] == team["node1"]
    assert payload["heading"] == "总体方案"
    assert payload["assignee_id"] == editor_id
    assert payload["assignee_username"] == "as-editor"
    assert payload["assigned_by"] == "as-owner"
    assert payload["status"] == "assigned"
    assert payload["note"] == "负责总体方案"
    assert payload["deadline"] == "2026-12-31T18:00:00+08:00"
    assignment_id = payload["id"]

    listed = _list(team["viewer"], team["project_id"])
    assert len(listed) == 1
    assert listed[0]["id"] == assignment_id

    started = team["editor"].put(
        f"/projects/{team['project_id']}/assignments/{assignment_id}",
        json={"status": "in_progress", "note": "进行中"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    assert started.json()["note"] == "进行中"

    done = team["editor"].put(
        f"/projects/{team['project_id']}/assignments/{assignment_id}",
        json={"status": "done", "note": "已完成"},
    )
    assert done.status_code == 200
    assert done.json()["status"] == "done"

    invalid = team["editor"].put(
        f"/projects/{team['project_id']}/assignments/{assignment_id}",
        json={"status": "blocked", "note": ""},
    )
    assert invalid.status_code == 400

    actions = [
        item["action"]
        for item in team["owner"].get(f"/projects/{team['project_id']}/audit").json()["items"]
    ]
    assert "chapter.assign" in actions
    assert actions.count("chapter.assignment.update") == 2


def test_assignment_requires_assignee_membership(team):
    outsider_id = _user_id(team["admin"], "as-outsider")
    rejected = _assign(team, team["node1"], outsider_id)
    assert rejected.status_code == 400
    assert "项目成员" in rejected.json()["detail"]

    unknown_node = _assign(team, "no-such-node", _user_id(team["admin"], "as-editor"))
    assert unknown_node.status_code == 404

    # 负责人本人也是合法成员
    owner_id = _user_id(team["admin"], "as-owner")
    assert _assign(team, team["node2"], owner_id).status_code == 201

    # 成员被移除后不能再被指派
    editor_id = _user_id(team["admin"], "as-editor")
    assert team["owner"].delete(
        f"/projects/{team['project_id']}/members/{editor_id}"
    ).status_code == 200
    assert _assign(team, team["node1"], editor_id).status_code == 400


def test_assignment_permissions_and_project_scope(team):
    editor_id = _user_id(team["admin"], "as-editor")
    forbidden = team["viewer"].post(
        f"/projects/{team['project_id']}/assignments",
        json={"node_id": team["node1"], "assignee_id": editor_id},
    )
    assert forbidden.status_code == 403
    assert (
        team["outsider"].post(
            f"/projects/{team['project_id']}/assignments",
            json={"node_id": team["node1"], "assignee_id": editor_id},
        ).status_code
        == 404
    )

    created = _assign(team, team["node1"], editor_id)
    assert created.status_code == 201
    assignment_id = created.json()["id"]

    assert team["viewer"].put(
        f"/projects/{team['project_id']}/assignments/{assignment_id}",
        json={"status": "done", "note": ""},
    ).status_code == 403
    assert team["outsider"].put(
        f"/projects/{team['project_id']}/assignments/{assignment_id}",
        json={"status": "done", "note": ""},
    ).status_code == 404

    # 其他项目的分工不能通过本项目的 URL 修改
    other = team["owner"].post(
        "/projects", json={"name": "另一项目", "package": "weakcurrent-cabling"}
    ).json()
    other_node = _seed_chapter(other["id"], "另一章节")
    # 被分配人必须是目标项目成员（业务规则）
    assert team["owner"].post(
        f"/projects/{other['id']}/members",
        json={"username": "as-editor", "role": "editor"},
    ).status_code == 201
    other_assignment = team["owner"].post(
        f"/projects/{other['id']}/assignments",
        json={"node_id": other_node, "assignee_id": editor_id},
    )
    assert other_assignment.status_code == 201
    crossed = team["owner"].put(
        f"/projects/{team['project_id']}/assignments/{other_assignment.json()['id']}",
        json={"status": "done", "note": ""},
    )
    assert crossed.status_code == 404


def test_assignment_deadline_validation_and_complete_helper(team):
    editor_id = _user_id(team["admin"], "as-editor")
    bad = _assign(team, team["node1"], editor_id, deadline="not-a-date")
    assert bad.status_code == 400
    assert "截止时间" in bad.json()["detail"]

    good = _assign(team, team["node2"], editor_id, deadline="2027-01-15")
    assert good.status_code == 201
    assert good.json()["deadline"].startswith("2027-01-15")

    from bidcraft.collaboration import complete_assignment
    from bidcraft.db import session_scope

    with session_scope() as session:
        row = complete_assignment(session, good.json()["id"], "as-owner")
        assert row.status == "done"
    assert _list(team["owner"], team["project_id"])[0]["status"] == "done"
