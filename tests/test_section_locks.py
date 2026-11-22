"""章节编辑锁：互斥、过期接管、拥有者校验、viewer 拒绝、成员移除失效。"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "lock-pass-1"


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


def _seed_chapter(project_id: str, heading: str = "总体方案") -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading=heading, position=1)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="初始正文", status="written", words=4))
        session.flush()
        return node.id


@pytest.fixture()
def team(db):
    admin = _login(*ADMIN)
    for username in ("lk-owner", "lk-editor", "lk-editor2", "lk-viewer", "lk-outsider"):
        _make_user(admin, username)
    owner = _login("lk-owner")
    project = owner.post(
        "/projects", json={"name": "编辑锁项目", "package": "weakcurrent-cabling"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    for username, role in (("lk-editor", "editor"), ("lk-editor2", "editor"), ("lk-viewer", "viewer")):
        assert owner.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": role},
        ).status_code == 201
    return {
        "admin": admin,
        "owner": owner,
        "editor": _login("lk-editor"),
        "editor2": _login("lk-editor2"),
        "viewer": _login("lk-viewer"),
        "outsider": _login("lk-outsider"),
        "project_id": project_id,
        "node_id": _seed_chapter(project_id),
    }


def _lock_url(team) -> str:
    return f"/projects/{team['project_id']}/chapters/{team['node_id']}/lock"


def test_only_one_editor_holds_a_lock(team):
    first = team["editor"].post(_lock_url(team), params={"ttl_seconds": 120})
    assert first.status_code == 200, first.text
    token = first.json()["lock_token"]
    assert first.json()["node_id"] == team["node_id"]
    assert first.json()["expires_at"]

    blocked = team["editor2"].post(_lock_url(team))
    assert blocked.status_code == 409

    # 同一用户重复获取会刷新并轮换 token
    refreshed = team["editor"].post(_lock_url(team), params={"ttl_seconds": 120})
    assert refreshed.status_code == 200
    new_token = refreshed.json()["lock_token"]
    assert new_token != token

    # 旧 token 不能再续期，他人 token 也不能续期他人锁
    assert team["editor"].put(_lock_url(team), json={"lock_token": token}).status_code == 409
    assert team["editor2"].put(
        _lock_url(team), json={"lock_token": new_token}
    ).status_code == 409
    renewed = team["editor"].put(_lock_url(team), json={"lock_token": new_token})
    assert renewed.status_code == 200
    assert renewed.json()["lock_token"] == new_token

    # 非拥有者 / 错误 token 无法释放
    assert team["editor2"].request(
        "DELETE", _lock_url(team), json={"lock_token": new_token}
    ).status_code == 409
    assert team["editor"].request(
        "DELETE", _lock_url(team), json={"lock_token": "not-the-token"}
    ).status_code == 409
    # 锁仍在：editor2 依然抢不到
    assert team["editor2"].post(_lock_url(team)).status_code == 409

    released = team["editor"].request("DELETE", _lock_url(team), json={"lock_token": new_token})
    assert released.status_code == 200
    assert released.json() == {"released": True}

    taken = team["editor2"].post(_lock_url(team))
    assert taken.status_code == 200
    assert taken.json()["user_id"]


def test_expired_lock_can_be_taken_over(team):
    acquired = team["editor"].post(_lock_url(team), params={"ttl_seconds": 60})
    assert acquired.status_code == 200
    old_token = acquired.json()["lock_token"]

    from bidcraft.clock import utc_now
    from bidcraft.db import session_scope
    from bidcraft.models import SectionEditLock

    with session_scope() as session:
        lock = session.get(SectionEditLock, team["node_id"])
        assert lock is not None
        lock.expires_at = utc_now() - timedelta(seconds=1)

    taken = team["editor2"].post(_lock_url(team))
    assert taken.status_code == 200, taken.text
    assert taken.json()["lock_token"] != old_token
    assert team["editor"].put(
        _lock_url(team), json={"lock_token": old_token}
    ).status_code == 409

    from bidcraft.collaboration import check_section_lock

    with session_scope() as session:
        current = check_section_lock(session, team["node_id"])
        assert current is not None
        assert current["user_id"] == taken.json()["user_id"]


def test_viewer_and_outsider_cannot_acquire(team):
    forbidden = team["viewer"].post(_lock_url(team))
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "只读成员不能修改该项目"

    hidden = team["outsider"].post(_lock_url(team))
    assert hidden.status_code == 404

    from bidcraft.authorization import Forbidden
    from bidcraft.collaboration import acquire_section_lock
    from bidcraft.db import session_scope

    with session_scope() as session, pytest.raises(Forbidden):
        acquire_section_lock(
            session,
            team["project_id"],
            team["node_id"],
            _user_id(team["admin"], "lk-viewer"),
            role="viewer",
        )


def test_removed_member_lock_is_invalidated(team):
    acquired = team["editor"].post(_lock_url(team))
    assert acquired.status_code == 200
    editor_id = _user_id(team["admin"], "lk-editor")

    removed = team["owner"].delete(
        f"/projects/{team['project_id']}/members/{editor_id}"
    )
    assert removed.status_code == 200

    from bidcraft.collaboration import check_section_lock
    from bidcraft.db import session_scope

    with session_scope() as session:
        assert check_section_lock(session, team["node_id"]) is None

    # 失效锁被清理后，负责人可以立即接管
    taken = team["owner"].post(_lock_url(team))
    assert taken.status_code == 200, taken.text
