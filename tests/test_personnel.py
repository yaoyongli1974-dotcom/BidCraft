from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402


def _client(username: str = "personuser"):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "person-pass-1", "display_name": username},
    )
    assert response.status_code == 201, response.text
    return client, response.json()["user"]


def _admin_client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def _create(client, **overrides):
    body = {
        "name": "张三",
        "title": "项目经理",
        "specialty": "机电工程",
        "cert_no": "P-001",
        "social_months": 24,
        "locked": True,
        "note": "一级建造师",
    }
    body.update(overrides)
    response = client.post("/my/personnel", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_crud_payload(db):
    owner, _user = _client("personcrud")

    created = _create(owner)
    assert set(created) == {
        "id",
        "name",
        "title",
        "specialty",
        "cert_no",
        "social_months",
        "locked",
        "note",
        "created_at",
        "updated_at",
    }
    assert created["name"] == "张三"
    assert created["social_months"] == 24 and created["locked"] is True
    person_id = created["id"]

    listed = owner.get("/my/personnel")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [person_id]

    patched = owner.patch(
        f"/my/personnel/{person_id}",
        json={"title": "技术负责人", "social_months": 36, "locked": False},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "技术负责人"
    assert patched.json()["social_months"] == 36
    assert patched.json()["locked"] is False
    # untouched fields stay put
    assert patched.json()["cert_no"] == "P-001"

    deleted = owner.delete(f"/my/personnel/{person_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": person_id}
    assert owner.get("/my/personnel").json() == []


def test_owner_isolation_404(db):
    owner, _owner_user = _client("personowner")
    other, _other_user = _client("personother")
    person_id = _create(owner)["id"]

    assert other.get("/my/personnel").json() == []
    assert other.patch(
        f"/my/personnel/{person_id}", json={"name": "劫持"}
    ).status_code == 404
    assert other.delete(f"/my/personnel/{person_id}").status_code == 404
    # owner's row is still there
    assert owner.get("/my/personnel").json()[0]["id"] == person_id
    assert owner.patch("/my/personnel/does-not-exist", json={"name": "x"}).status_code == 404


def test_validation_matrix(db):
    owner, _user = _client("personinvalid")

    cases = [
        ({"name": "  "}, "姓名不能为空"),
        ({"name": "名" * 81}, "姓名不能超过 80 字"),
        ({"title": "职" * 81}, "职务不能超过 80 字"),
        ({"specialty": "专" * 81}, "注册专业不能超过 80 字"),
        ({"cert_no": "证" * 81}, "证书号不能超过 80 字"),
        ({"note": "备" * 201}, "备注不能超过 200 字"),
        ({"social_months": -1}, "社保缴纳月份应在 0–600 之间"),
        ({"social_months": 601}, "社保缴纳月份应在 0–600 之间"),
        ({"social_months": "abc"}, "社保缴纳月份必须为整数"),
        ({"social_months": 1.5}, "社保缴纳月份必须为整数"),
        ({"locked": "yes"}, "在建锁定必须为布尔值"),
    ]
    for payload, detail in cases:
        response = owner.post("/my/personnel", json={"name": "合法姓名", **payload})
        assert response.status_code == 400, (payload, response.text)
        assert response.json()["detail"] == detail

    # update goes through the same validator and rejects empty names
    person_id = _create(owner)["id"]
    empty = owner.patch(f"/my/personnel/{person_id}", json={"name": ""})
    assert empty.status_code == 400
    assert empty.json()["detail"] == "姓名不能为空"
    assert owner.patch(
        f"/my/personnel/{person_id}", json={"social_months": 900}
    ).status_code == 400


def test_defaults_locked_payload_and_sorting(db):
    owner, _user = _client("personsort")

    minimal = _create(owner, name="王工", title="", specialty="", cert_no="",
                      social_months=0, locked=False, note="")
    assert minimal["social_months"] == 0
    assert minimal["locked"] is False
    assert minimal["title"] == ""

    _create(owner, name="张工", locked=True)
    _create(owner, name="李工", locked=False)
    names = [row["name"] for row in owner.get("/my/personnel").json()]
    assert names == sorted(["王工", "张工", "李工"])

    locked_flags = {
        row["name"]: row["locked"] for row in owner.get("/my/personnel").json()
    }
    assert locked_flags == {"王工": False, "张工": True, "李工": False}

    from bidcraft import personnel

    assert personnel.policies()["required"] == ["cert_no", "social_months", "locked"]


def test_audit_trail(db):
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    owner, _user = _client("personaudit")
    created = _create(owner)
    person_id = created["id"]
    owner.patch(f"/my/personnel/{person_id}", json={"note": "已核对"})
    owner.delete(f"/my/personnel/{person_id}")

    with session_scope() as sess:
        actions = [
            row.action
            for row in sess.scalars(
                select(AuditLog).where(AuditLog.action.like("personnel.%"))
            )
        ]
    assert actions.count("personnel.create") == 1
    assert actions.count("personnel.update") == 1
    assert actions.count("personnel.delete") == 1


def test_viewer_cannot_write(db):
    from bidcraft.db import session_scope
    from bidcraft.models import User

    viewer, viewer_user = _client("personviewer")
    person_id = _create(viewer)["id"]

    with session_scope() as sess:
        row = sess.get(User, viewer_user["id"])
        row.role = "viewer"

    assert viewer.get("/my/personnel").status_code == 200
    assert viewer.get("/my/personnel").json()[0]["id"] == person_id
    blocked = viewer.post("/my/personnel", json={"name": "只读新增"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "只读成员不能执行此操作"
    assert viewer.patch(
        f"/my/personnel/{person_id}", json={"name": "只读改名"}
    ).status_code == 403
    assert viewer.delete(f"/my/personnel/{person_id}").status_code == 403


def test_kb_docs_returns_policy_items_and_sources(db, tmp_path):
    from bidcraft.db import session_scope
    from bidcraft.models import KbChunk

    admin = _admin_client()
    created = admin.post(
        "/admin/kbs",
        json={
            "name": "人员素材库",
            "kind": "local",
            "provider": "local",
            "location": str(tmp_path / "kb"),
        },
    )
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    with session_scope() as sess:
        sess.add_all(
            [
                KbChunk(kb_id=kb_id, source="/kb/a.md", ordinal=0, text="A0"),
                KbChunk(kb_id=kb_id, source="/kb/a.md", ordinal=1, text="A1"),
                KbChunk(kb_id=kb_id, source="/kb/b.md", ordinal=0, text="B0"),
            ]
        )
        sess.flush()

    upserted = admin.put(
        f"/admin/kbs/{kb_id}/docs",
        json={
            "source": "/kb/a.md",
            "kind": "qualification",
            "cert_no": "D-1",
            "valid_to": "2099-01-01",
        },
    )
    assert upserted.status_code == 200, upserted.text

    body = admin.get(f"/admin/kbs/{kb_id}/docs").json()
    assert body["policy"]["personnel"]["required"] == [
        "cert_no",
        "social_months",
        "locked",
    ]
    assert body["policy"]["qualification"]["required"] == ["cert_no", "valid_to"]
    assert body["sources"] == [
        {"source": "/kb/a.md", "chunks": 2},
        {"source": "/kb/b.md", "chunks": 1},
    ]

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["source"] == "/kb/a.md"
    assert item["kind"] == "qualification"
    assert item["fields"]["cert_no"] == "D-1"
    assert item["fields"]["valid_to"] == "2099-01-01"
    assert item["ok"] is True and item["reason"] == ""

    # PUT/DELETE remain keyed by source
    removed = admin.delete(f"/admin/kbs/{kb_id}/docs", params={"source": "/kb/a.md"})
    assert removed.status_code == 200
    assert removed.json() == {"deleted": True, "source": "/kb/a.md"}
    assert admin.get(f"/admin/kbs/{kb_id}/docs").json()["items"] == []


def test_kb_docs_missing_kb_404(db):
    admin = _admin_client()
    assert admin.get("/admin/kbs/no-such-kb/docs").status_code == 404
