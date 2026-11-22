"""编辑锁并发：同章节并发只有一人成功；不同章节互不阻塞；过期清理。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "conc-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_user(admin, username: str) -> str:
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
    return response.json()["id"]


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
def arena(db):
    admin = _login(*ADMIN)
    _make_user(admin, "cc-owner")
    editor1 = _make_user(admin, "cc-editor1")
    editor2 = _make_user(admin, "cc-editor2")
    owner = _login("cc-owner")
    project = owner.post(
        "/projects", json={"name": "并发锁项目", "package": "weakcurrent-cabling"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    for username in ("cc-editor1", "cc-editor2"):
        assert owner.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": "editor"},
        ).status_code == 201
    return {
        "project_id": project_id,
        "node1": _seed_chapter(project_id, "章节一", 1),
        "node2": _seed_chapter(project_id, "章节二", 2),
        "editor1": editor1,
        "editor2": editor2,
    }


def test_concurrent_acquire_only_one_wins(arena):
    from bidcraft.collaboration import acquire_section_lock
    from bidcraft.db import session_scope

    barrier = threading.Barrier(2)

    def attempt(user_id: str):
        with session_scope() as session:
            barrier.wait(timeout=10)
            return acquire_section_lock(
                session,
                arena["project_id"],
                arena["node1"],
                user_id,
                role="editor",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [arena["editor1"], arena["editor2"]]))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1, results
    winner_id = winners[0]["user_id"]
    assert winner_id in {arena["editor1"], arena["editor2"]}

    from bidcraft.collaboration import check_section_lock

    with session_scope() as session:
        current = check_section_lock(session, arena["node1"])
        assert current is not None
        assert current["user_id"] == winner_id


def test_concurrent_acquire_on_different_nodes_both_win(arena):
    from bidcraft.collaboration import acquire_section_lock
    from bidcraft.db import session_scope

    barrier = threading.Barrier(2)

    def attempt(user_id: str, node_id: str):
        with session_scope() as session:
            barrier.wait(timeout=10)
            return acquire_section_lock(
                session,
                arena["project_id"],
                node_id,
                user_id,
                role="editor",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(attempt, arena["editor1"], arena["node1"]),
            pool.submit(attempt, arena["editor2"], arena["node2"]),
        ]
        results = [future.result(timeout=15) for future in futures]

    assert all(result is not None for result in results)
    assert results[0]["node_id"] == arena["node1"]
    assert results[1]["node_id"] == arena["node2"]


def test_expired_lock_can_be_reaped_and_reacquired(arena):
    from bidcraft.clock import utc_now
    from bidcraft.collaboration import (
        acquire_section_lock,
        check_section_lock,
        reap_expired_locks,
        renew_section_lock,
    )
    from bidcraft.db import session_scope
    from bidcraft.models import SectionEditLock

    with session_scope() as session:
        first = acquire_section_lock(
            session,
            arena["project_id"],
            arena["node1"],
            arena["editor1"],
            ttl_seconds=60,
            role="editor",
        )
        assert first is not None
    token = first["lock_token"]

    with session_scope() as session:
        lock = session.get(SectionEditLock, arena["node1"])
        lock.expires_at = utc_now() - timedelta(seconds=5)
        lock_token = lock.lock_token
        assert lock_token == token

    # 过期后不能续期；reap 清理；另一用户可重新获取
    with session_scope() as session:
        assert (
            renew_section_lock(
                session, arena["node1"], arena["editor1"], token
            )
            is None
        )
    with session_scope() as session:
        assert reap_expired_locks(session) >= 1
        assert check_section_lock(session, arena["node1"]) is None
    with session_scope() as session:
        second = acquire_section_lock(
            session,
            arena["project_id"],
            arena["node1"],
            arena["editor2"],
            role="editor",
        )
        assert second is not None
        assert second["user_id"] == arena["editor2"]
