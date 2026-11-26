"""v0.33.0 RC-08：建议经验治理（分级 / 信任 / 有效期 / 撤销）。

全部落在真实 SQLite 库与真实 TestClient 上：策略归一化、项目 meta 往返、
生效过滤（撤销 / 过期 / 最低信任）、owner-only 撤销路由与审计留痕。
不 mock 来源行，撤销只改 meta，绝不改写 finding / revision / audit / plan。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bidcraft.db import session_scope
from bidcraft.evaluation.learning import (
    LESSON_ITEM_KEYS,
    LESSON_POLICY_KEYS,
    lesson_items,
    lessons_block,
    lessons_payload,
    normalize_lesson_policy,
)
from bidcraft.models import AuditLog, OutlineNode, Project, SectionRevision

pytest.importorskip("fastapi")

MEMBER_PASS = "lesson-gov-pass-1"


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    logged = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert logged.status_code == 200, logged.text
    return client


def _member(admin, username: str) -> str:
    """Create a global manager user; project-level role is granted separately."""

    created = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": MEMBER_PASS,
            "role": "manager",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _grant(admin, project_id: str, username: str, role: str) -> None:
    added = admin.post(
        f"/projects/{project_id}/members", json={"username": username, "role": role}
    )
    assert added.status_code == 201, added.text


def _login(username: str):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": username, "password": MEMBER_PASS}
    )
    assert response.status_code == 200, response.text
    return client


def _seed_project(db, name: str) -> tuple[Project, OutlineNode]:
    project = Project(id=f"p-gov-{name}", name=name, package="weakcurrent-cabling")
    db.add(project)
    db.flush()
    node = OutlineNode(project_id=project.id, heading="技术方案", intent="响应", target_words=100)
    db.add(node)
    db.flush()
    return project, node


def _revision(
    db, project_id: str, node_id: str, note: str, created: datetime, revision: int = 1
) -> SectionRevision:
    row = SectionRevision(
        project_id=project_id,
        node_id=node_id,
        revision=revision,
        change_type="edit",
        change_note=note,
        changed_by="编辑甲",
        created_at=created,
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------- 策略归一化


def test_normalize_lesson_policy_sanitizes_all_fields():
    policy = normalize_lesson_policy(
        {
            "grades": {"l1": "a", "l2": "Z", "l3": 7, "l4": "B"},
            "trust_levels": {"l1": "HIGH", "l2": "unknown", "l3": "low"},
            "min_trust": "MEDIUM",
            "ttl_days": 99999,
            "revoked_ids": ["l1", "l2", "l1", "", 7, "l3"],
        }
    )
    assert set(policy) == LESSON_POLICY_KEYS
    assert policy["grades"] == {"l1": "A", "l4": "B"}
    assert policy["trust_levels"] == {"l1": "high", "l3": "low"}
    assert policy["min_trust"] == "medium"
    assert policy["ttl_days"] == 3650
    assert policy["revoked_ids"] == ["l1", "l2", "l3"]
    assert normalize_lesson_policy(None) == {
        "grades": {},
        "trust_levels": {},
        "min_trust": None,
        "ttl_days": 0,
        "revoked_ids": [],
    }


# --------------------------------------------------------------- PATCH 往返


def test_lesson_policy_patch_roundtrip_and_owner_only(db):
    client = _client()
    owner_id = _member(client, "lesson-owner")
    _member(client, "lesson-editor")
    _member(client, "lesson-viewer")
    created = client.post("/projects", json={"name": "经验治理项目"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    _grant(client, project_id, "lesson-editor", "editor")
    _grant(client, project_id, "lesson-viewer", "viewer")

    with session_scope() as session:
        project = session.get(Project, project_id)
        project.owner_id = owner_id
        session.flush()

    patch = {
        "lesson_policy": {
            "grades": {"l1": "A", "l2": "bad"},
            "trust_levels": {"l1": "high"},
            "min_trust": "medium",
            "ttl_days": 30,
            "revoked_ids": ["l1", "l1", ""],
        }
    }
    rejected = _login("lesson-editor").patch(
        f"/projects/{project_id}", json=patch
    )
    assert rejected.status_code == 403
    rejected_viewer = _login("lesson-viewer").patch(
        f"/projects/{project_id}", json=patch
    )
    assert rejected_viewer.status_code == 403

    owner = _login("lesson-owner")
    patched = owner.patch(f"/projects/{project_id}", json=patch)
    assert patched.status_code == 200, patched.text
    echoed = patched.json()["lesson_policy"]
    assert echoed == normalize_lesson_policy(patch["lesson_policy"])
    assert echoed["revoked_ids"] == ["l1"]
    assert client.get(f"/projects/{project_id}").json()["lesson_policy"] == echoed

    with session_scope() as session:
        stored = session.get(Project, project_id).meta["lesson_policy"]
    assert stored["ttl_days"] == 30
    assert stored["revoked_ids"] == ["l1"]

    from sqlalchemy import select

    with session_scope() as session:
        updates = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == project_id, AuditLog.action == "project.update"
                )
            )
        )
    assert any("l1" in str(row.detail) for row in updates)


def test_lesson_policy_partial_patch_preserves_revoked_ids(db):
    client = _client()
    created = client.post("/projects", json={"name": "经验治理局部更新"})
    project_id = created.json()["id"]
    first = client.patch(
        f"/projects/{project_id}",
        json={"lesson_policy": {"revoked_ids": ["l1"], "ttl_days": 7}},
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/projects/{project_id}", json={"lesson_policy": {"min_trust": "high"}}
    )
    assert second.status_code == 200, second.text
    policy = second.json()["lesson_policy"]
    assert policy["revoked_ids"] == ["l1"]
    assert policy["ttl_days"] == 7
    assert policy["min_trust"] == "high"


# --------------------------------------------------------------- 生效过滤


def test_policy_filters_revoked_expired_and_low_trust_before_cap(db):
    now = datetime(2026, 11, 26, 12, 0, 0)
    with session_scope() as session:
        project, node = _seed_project(session, "filter")
        fresh = _revision(session, project.id, node.id, "新鲜建议", now - timedelta(days=1), 1)
        _revision(session, project.id, node.id, "过期建议", now - timedelta(days=60), 2)
        other = _revision(session, project.id, node.id, "被撤销建议", now - timedelta(days=1), 3)
        project.meta = {
            "lesson_policy": {
                "grades": {fresh.id: "A", other.id: "C"},
                "trust_levels": {fresh.id: "high", other.id: "low"},
                "ttl_days": 30,
                "revoked_ids": [other.id],
            }
        }
        session.flush()
        items = lesson_items(session, project, include_revoked=False)
        visible = {row["text"] for row in items}
        assert "新鲜建议" in visible
        assert "过期建议" not in visible  # TTL 过滤
        assert "被撤销建议" not in visible  # 撤销过滤
        block = lessons_block(session, project)
        assert "新鲜建议" in block
        assert "过期建议" not in block
        assert "被撤销建议" not in block

        # include_revoked 只把撤销项放回，过期项依旧不出现。
        restored = lesson_items(session, project, include_revoked=True)
        restored_by_text = {row["text"]: row for row in restored}
        assert restored_by_text["被撤销建议"]["revoked"] is True
        assert "过期建议" not in restored_by_text

        payload = lessons_payload(session, project)
        assert set(payload) == {"items", "node_id", "status", "policy"}
        assert set(payload["policy"]) == LESSON_POLICY_KEYS
        assert payload["policy"]["ttl_days"] == 30
        assert all(set(row) == LESSON_ITEM_KEYS for row in payload["items"])


def test_min_trust_filters_by_default_and_can_be_overridden(db):
    with session_scope() as session:
        project, node = _seed_project(session, "trust")
        low = _revision(session, project.id, node.id, "低信任建议", datetime(2026, 11, 25), 1)
        high = _revision(session, project.id, node.id, "高信任建议", datetime(2026, 11, 26), 2)
        project.meta = {
            "lesson_policy": {
                "min_trust": "high",
                "trust_levels": {high.id: "high", low.id: "low"},
            }
        }
        session.flush()
        visible = {row["text"] for row in lesson_items(session, project)}
        assert visible == {"高信任建议"}
        # 默认信任（medium）低于 high 时同样被过滤。
        project.meta["lesson_policy"]["trust_levels"] = {}
        session.flush()
        assert lesson_items(session, project) == []


def test_grade_trust_expires_at_fields_follow_policy(db):
    created = datetime.now().replace(microsecond=0) - timedelta(days=1)
    with session_scope() as session:
        project, node = _seed_project(session, "fields")
        row = _revision(session, project.id, node.id, "带治理字段", created)
        project.meta = {
            "lesson_policy": {
                "grades": {row.id: "A"},
                "trust_levels": {row.id: "high"},
                "ttl_days": 10,
            }
        }
        session.flush()
        item = {item["id"]: item for item in lesson_items(session, project)}[row.id]
    assert item["grade"] == "A"
    assert item["trust"] == "high"
    assert item["revoked"] is False
    expected = (
        datetime.fromisoformat(item["created_at"]) + timedelta(days=10)
    ).isoformat()
    assert item["expires_at"] == expected


# --------------------------------------------------------------- 撤销路由


def test_revoke_lesson_is_owner_only_cross_project_404_and_audited(db):
    client = _client()
    owner_id = _member(client, "revoke-owner")
    _member(client, "revoke-editor")
    _member(client, "revoke-viewer")
    with session_scope() as session:
        first, node_a = _seed_project(session, "revoke-a")
        second, node_b = _seed_project(session, "revoke-b")
        own = _revision(session, first.id, node_a.id, "本项目建议", datetime(2026, 11, 20))
        foreign = _revision(session, second.id, node_b.id, "别的项目建议", datetime(2026, 11, 20))
        for project_id in (first.id, second.id):
            session.get(Project, project_id).owner_id = owner_id
        session.flush()
        own_id, foreign_id = own.id, foreign.id
        source_ids = sorted(str(row.id) for row in session.query(SectionRevision).all())
    _grant(client, "p-gov-revoke-a", "revoke-editor", "editor")
    _grant(client, "p-gov-revoke-a", "revoke-viewer", "viewer")

    editor = _login("revoke-editor")
    viewer = _login("revoke-viewer")
    assert editor.patch(f"/projects/p-gov-revoke-a/lessons/{own_id}").status_code == 403
    assert viewer.patch(f"/projects/p-gov-revoke-a/lessons/{own_id}").status_code == 403

    owner = _login("revoke-owner")
    missing = owner.patch("/projects/p-gov-revoke-a/lessons/does-not-exist")
    assert missing.status_code == 404
    # 别的项目的真实来源 id 不能从本项目撤销。
    cross = owner.patch(f"/projects/p-gov-revoke-a/lessons/{foreign_id}")
    assert cross.status_code == 404

    revoked = owner.patch(f"/projects/p-gov-revoke-a/lessons/{own_id}")
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["lesson_id"] == own_id
    assert body["revoked"] is True
    assert body["policy"]["revoked_ids"] == [own_id]

    with session_scope() as session:
        meta = session.get(Project, "p-gov-revoke-a").meta
        assert meta["lesson_policy"]["revoked_ids"] == [own_id]
        assert sorted(str(row.id) for row in session.query(SectionRevision).all()) == source_ids
        assert session.get(SectionRevision, own_id).change_note == "本项目建议"
        audit_rows = (
            session.query(AuditLog)
            .filter(AuditLog.project_id == "p-gov-revoke-a", AuditLog.action == "learning.revoke")
            .all()
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].detail["lesson_id"] == own_id

    # 撤销后默认列表不再出现；include_revoked 可显式取回用于治理视图。
    with session_scope() as session:
        project = session.get(Project, "p-gov-revoke-a")
        assert all(row["id"] != own_id for row in lesson_items(session, project))
        restored = lesson_items(session, project, include_revoked=True)
        assert {row["id"] for row in restored} == {own_id}
        assert restored[0]["revoked"] is True
        assert own_id not in lessons_block(session, project)
