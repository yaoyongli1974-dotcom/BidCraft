"""企业 CRUD、成员权限、企业文档与审计（Lane A）。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

PASSWORD = "ent-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _admin():
    return _login("admin", "test-admin")


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


@pytest.fixture()
def team(db):
    admin = _admin()
    _make_user(admin, "ent-owner")
    _make_user(admin, "ent-member")
    _make_user(admin, "ent-outsider")
    _make_user(admin, "ent-viewer", role="viewer")
    return {
        "admin": admin,
        "owner": _login("ent-owner"),
        "member": _login("ent-member"),
        "outsider": _login("ent-outsider"),
        "viewer": _login("ent-viewer"),
    }


def test_enterprise_crud_and_owner_only_member_management(team):
    created = team["owner"].post("/enterprise", json={"name": "示例建工集团"})
    assert created.status_code == 201, created.text
    enterprise = created.json()
    assert enterprise["name"] == "示例建工集团"
    assert enterprise["role"] == "owner"
    assert enterprise["is_owner"] is True

    listed = team["owner"].get("/enterprise").json()
    assert [item["id"] for item in listed] == [enterprise["id"]]

    added = team["owner"].post(
        f"/enterprise/{enterprise['id']}/members",
        json={"username": "ent-member", "role": "member"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["role"] == "member"

    members = team["owner"].get(f"/enterprise/{enterprise['id']}/members").json()
    assert {item["username"] for item in members} == {"ent-owner", "ent-member"}

    # member 能看到企业，但不能管理成员
    assert team["member"].get("/enterprise").json()[0]["id"] == enterprise["id"]
    blocked = team["member"].post(
        f"/enterprise/{enterprise['id']}/members",
        json={"username": "ent-outsider", "role": "member"},
    )
    assert blocked.status_code == 403

    # 非成员看不到企业（404，不泄露存在性）
    assert team["outsider"].get("/enterprise").json() == []
    assert team["outsider"].get(f"/enterprise/{enterprise['id']}/members").status_code == 404

    removed = team["owner"].delete(
        f"/enterprise/{enterprise['id']}/members/{added.json()['user_id']}"
    )
    assert removed.status_code == 200
    assert [item["username"] for item in team["owner"].get(
        f"/enterprise/{enterprise['id']}/members"
    ).json()] == ["ent-owner"]


def test_viewer_cannot_write_enterprise(team):
    response = team["viewer"].post("/enterprise", json={"name": "只读企业"})
    assert response.status_code == 403
    assert response.json()["detail"] == "只读成员不能执行此操作"


def test_enterprise_writes_are_audited(team):
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    created = team["owner"].post("/enterprise", json={"name": "审计企业"}).json()
    team["owner"].post(
        f"/enterprise/{created['id']}/members",
        json={"username": "ent-member", "role": "member"},
    )
    with session_scope() as db:
        actions = {
            row.action
            for row in db.query(AuditLog)
            .filter(AuditLog.action.like("enterprise.%"))
            .all()
        }
    assert {"enterprise.create", "enterprise.member_add"} <= actions


def test_project_enterprise_match_and_package_endpoints(team):
    from bidcraft.db import session_scope
    from bidcraft.qualifications import create_enterprise_qualification

    enterprise = team["owner"].post("/enterprise", json={"name": "投标企业"}).json()
    team["owner"].post(
        f"/enterprise/{enterprise['id']}/members",
        json={"username": "ent-member", "role": "member"},
    )
    owner_id = team["owner"].get("/auth/me").json()["user"]["id"]
    with session_scope() as session:
        create_enterprise_qualification(
            session,
            {"id": owner_id},
            enterprise["id"],
            {
                "kind": "license",
                "name": "电子与智能化工程专业承包二级",
                "valid_to": "2030-12-31",
            },
        )
    project = team["owner"].post(
        "/projects", json={"name": "企业匹配项目", "package": "weakcurrent-cabling"}
    ).json()
    assert team["owner"].post(
        f"/projects/{project['id']}/members",
        json={"username": "ent-member", "role": "editor"},
    ).status_code == 201

    matched = team["member"].post(
        f"/projects/{project['id']}/enterprise/match",
        json={"requirement": "须具备电子与智能化工程专业承包二级资质", "kind": "license"},
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["matched"] is True

    empty = team["member"].post(
        f"/projects/{project['id']}/enterprise/match",
        json={"requirement": "", "kind": "license"},
    )
    assert empty.status_code == 400

    package = team["member"].get(f"/projects/{project['id']}/enterprise/package")
    assert package.status_code == 200, package.text
    assert package.json()["project_id"] == project["id"]


def test_enterprise_documents_incremental_and_member_read(team):
    enterprise = team["owner"].post("/enterprise", json={"name": "文档企业"}).json()
    team["owner"].post(
        f"/enterprise/{enterprise['id']}/members",
        json={"username": "ent-member", "role": "member"},
    )
    url = f"/enterprise/{enterprise['id']}/documents"

    first = team["owner"].post(
        url,
        json={
            "source": "安全生产许可证.md",
            "content": "# 安全生产许可证\n有效期至 2030-01-01。",
            "kind": "license",
            "tags": ["安全"],
            "valid_to": "2030-01-01",
        },
    )
    assert first.status_code == 201, first.text
    doc = first.json()
    assert doc["version"] == 1
    assert doc["valid_to"].startswith("2030-01-01")

    same = team["owner"].post(
        url,
        json={
            "source": "安全生产许可证.md",
            "content": "# 安全生产许可证\n有效期至 2030-01-01。",
            "kind": "license",
        },
    ).json()
    assert same["id"] == doc["id"] and same["version"] == 1

    # member 只读；member 入库 → 403
    listed = team["member"].get(url)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [doc["id"]]
    assert team["member"].post(
        url, json={"source": "x.md", "content": "x", "kind": "other"}
    ).status_code == 403

    deleted = team["owner"].delete(f"{url}/{doc['id']}")
    assert deleted.status_code == 200
    assert team["owner"].get(url).json() == []
