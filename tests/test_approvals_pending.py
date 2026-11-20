"""待我审批（R20）：跨项目审批待办 GET /approvals/pending。

Covers the contract's A3 matrix: empty-chain owner-only inbox (editor excluded,
super admin sees all), two-level all-chain visibility shifting as approvals
land, any-level visibility until the first signature (advance or finalize),
non-member isolation, draft/approved/rejected exclusion, global viewer [],
payload shape + submitted_at descending (None last), cross-project aggregation
and the 100-item cap. Client fixtures follow test_quote_approval_parallel.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEMBER_PASS = "member-pass-1"
PENDING_KEYS = {
    "project_id",
    "project_name",
    "version_id",
    "version_name",
    "package_id",
    "stage",
    "stage_count",
    "mode",
    "submitted_by",
    "submitted_at",
    "total_amount",
}


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def _login(username: str):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": username, "password": MEMBER_PASS}
    )
    assert response.status_code == 200, response.text
    return client


def _member(admin, project_id: str | None, username: str, display_name: str, role: str):
    """Create a global user (optionally a project member) and return (client, id)."""
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
    user_id = created.json()["id"]
    if project_id is not None:
        added = admin.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": role},
        )
        assert added.status_code == 201, added.text
    return _login(username), user_id


def _viewer(admin, project_id: str | None, username: str, display_name: str):
    """Create a global viewer (optionally a project member) and return its client."""
    created = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": display_name,
            "password": MEMBER_PASS,
            "role": "viewer",
        },
    )
    assert created.status_code == 201, created.text
    if project_id is not None:
        added = admin.post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": "editor"},
        )
        assert added.status_code == 201, added.text
    return _login(username)


def _project(client, name: str) -> str:
    created = client.post("/projects", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _package(client, project_id: str, name: str = "A 标段") -> str:
    created = client.post(f"/projects/{project_id}/packages", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _write_boq(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _upload(client, project_id: str, path, package_id: str | None = None):
    data = {"package_id": package_id} if package_id is not None else None
    with path.open("rb") as handle:
        return client.post(
            f"/projects/{project_id}/boq",
            files={"file": (path.name, handle, XLSX_MIME)},
            data=data,
        )


def _seed(client, tmp_path, name: str = "待审项目", filename: str = "pending.xlsx"):
    project_id = _project(client, name)
    package_id = _package(client, project_id)
    path = _write_boq(
        tmp_path / filename,
        [
            [1, "网络面板", "双口 Cat6A", "个", "2", ""],
            [2, "线缆", "Cat6A", "米", "10", ""],
        ],
    )
    assert _upload(client, project_id, path, package_id=package_id).status_code == 200
    priced = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 2, "unit_price": 12.5, "package_id": package_id},
    )
    assert priced.status_code == 200, priced.text
    return project_id, package_id


def _save(client, project_id: str, name: str, package_id: str | None = None):
    body = {"name": name}
    if package_id is not None:
        body["package_id"] = package_id
    response = client.post(f"/projects/{project_id}/boq/versions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client, project_id: str, version_id: str, note: str = ""):
    response = client.post(
        f"/projects/{project_id}/boq/versions/{version_id}/submit",
        json={"note": note},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _review(client, project_id: str, version_id: str, decision: str, note: str = ""):
    response = client.post(
        f"/projects/{project_id}/boq/versions/{version_id}/review",
        json={"decision": decision, "note": note},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _patch_chain(client, project_id: str, chain):
    response = client.patch(f"/projects/{project_id}", json={"approval_chain": chain})
    assert response.status_code == 200, response.text
    return response.json()


def _pending(client):
    response = client.get("/approvals/pending")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    return body


def _ids(items):
    return [item["version_id"] for item in items]


def _stage(ids, mode="all"):
    return {"approvers": list(ids), "mode": mode}


# ------------------------------------------------------------------ 空链


def test_empty_chain_owner_sees_editor_not_super_sees_all(db, tmp_path):
    admin = _client()
    owner, _owner_id = _member(admin, None, "pend-owner", "项目负责人", "editor")
    project_id, package_id = _seed(owner, tmp_path, name="负责人项目")
    editor, _editor_id = _member(admin, project_id, "pend-editor", "编辑成员", "editor")
    outsider, _outsider_id = _member(admin, None, "pend-outsider", "局外人", "editor")

    version = _save(editor, project_id, "空链报价", package_id)
    _submit(editor, project_id, version["id"])

    # owner of the project sees the submitted version (single owner level)
    mine = _pending(owner)
    assert len(mine) == 1
    item = mine[0]
    assert item["project_id"] == project_id
    assert item["project_name"] == "负责人项目"
    assert item["version_id"] == version["id"]
    assert item["version_name"] == "空链报价"
    assert item["package_id"] == package_id
    assert item["stage"] == 1
    assert item["stage_count"] == 1
    assert item["mode"] == ""
    assert item["submitted_by"] == "编辑成员"
    assert datetime.fromisoformat(item["submitted_at"])
    assert item["total_amount"] == 25.0  # boq_row 2（首个数据行）：2 个 × 12.5 元

    # an editor member is not the owner: empty chain gives them no inbox item
    assert _pending(editor) == []
    # a global user without membership sees nothing
    assert _pending(outsider) == []
    # super admin sees every submitted version, including empty-chain ones
    assert version["id"] in _ids(_pending(admin))


# ------------------------------------------------------------------ 两级链 all


def test_two_level_all_chain_visibility_shifts(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "pend-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "pend-bob", "审批乙", "editor")
    _patch_chain(admin, project_id, [_stage([alice_id]), _stage([bob_id])])

    version = _save(alice, project_id, "两级会签", package_id)
    _submit(alice, project_id, version["id"])

    # stage 1 pending: only 甲 sees it; 乙 waits for level 2
    mine = _pending(alice)
    assert _ids(mine) == [version["id"]]
    assert mine[0]["stage"] == 1
    assert mine[0]["stage_count"] == 2
    assert mine[0]["mode"] == "all"
    assert _pending(bob) == []
    # super admin may act at any level, so the item is always in their inbox
    assert version["id"] in _ids(_pending(admin))

    # 甲 signs level 1 (all, single approver) -> advances to level 2
    body = _review(alice, project_id, version["id"], "approve")
    assert body["status"] == "submitted"
    assert _pending(alice) == []
    theirs = _pending(bob)
    assert _ids(theirs) == [version["id"]]
    assert theirs[0]["stage"] == 2
    assert theirs[0]["mode"] == "all"
    assert version["id"] in _ids(_pending(admin))

    # 乙 signs the final level -> approved, nobody has it pending anymore
    assert _review(bob, project_id, version["id"], "approve")["status"] == "approved"
    assert _pending(alice) == []
    assert _pending(bob) == []
    assert _pending(admin) == []


def test_all_level_countersign_hides_only_the_signed_approver(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "pend-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "pend-bob", "审批乙", "editor")
    _patch_chain(admin, project_id, [_stage([alice_id, bob_id])])

    version = _save(alice, project_id, "同级会签", package_id)
    _submit(alice, project_id, version["id"])
    assert version["id"] in _ids(_pending(alice))
    assert version["id"] in _ids(_pending(bob))

    # 甲 signs but the all-level still waits for 乙: 甲's item disappears, 乙 keeps it
    _review(alice, project_id, version["id"], "approve")
    assert _pending(alice) == []
    theirs = _pending(bob)
    assert _ids(theirs) == [version["id"]]
    assert theirs[0]["stage"] == 1  # level not complete yet

    _review(bob, project_id, version["id"], "approve")
    assert _pending(bob) == []


# ------------------------------------------------------------------ 或签 any


def test_any_level_visible_to_all_until_first_sign(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "pend-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "pend-bob", "审批乙", "editor")
    carol, carol_id = _member(admin, project_id, "pend-carol", "审批丙", "editor")
    _patch_chain(
        admin, project_id, [_stage([alice_id, bob_id], "any"), _stage([carol_id])]
    )

    version = _save(alice, project_id, "或签两级", package_id)
    _submit(alice, project_id, version["id"])

    # any-level: both approvers see it while none has signed
    for client in (alice, bob):
        mine = _pending(client)
        assert _ids(mine) == [version["id"]]
        assert mine[0]["stage"] == 1
        assert mine[0]["mode"] == "any"
    assert _pending(carol) == []

    # first signature advances the version: both level-1 approvers lose the item
    body = _review(alice, project_id, version["id"], "approve")
    assert body["status"] == "submitted"
    assert _pending(alice) == []
    assert _pending(bob) == []
    final_level = _pending(carol)
    assert _ids(final_level) == [version["id"]]
    assert final_level[0]["stage"] == 2

    # final approval clears every inbox
    assert _review(carol, project_id, version["id"], "approve")["status"] == "approved"
    assert _pending(alice) == []
    assert _pending(bob) == []
    assert _pending(carol) == []
    assert _pending(admin) == []


def test_single_any_level_finalizes_on_first_sign(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path, name="单级或签", filename="any.xlsx")
    alice, alice_id = _member(admin, project_id, "pend-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "pend-bob", "审批乙", "editor")
    _patch_chain(admin, project_id, [_stage([alice_id, bob_id], "any")])

    version = _save(alice, project_id, "或签定稿", package_id)
    _submit(alice, project_id, version["id"])
    assert version["id"] in _ids(_pending(alice))
    assert version["id"] in _ids(_pending(bob))

    # 乙 signs first: the single any-level completes -> approved, both lose it
    assert _review(bob, project_id, version["id"], "approve")["status"] == "approved"
    assert _pending(alice) == []
    assert _pending(bob) == []


# ------------------------------------------------------------------ 隔离与状态


def test_non_member_cannot_see_others_projects(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    version = _save(admin, project_id, "他人项目", package_id)
    _submit(admin, project_id, version["id"])

    stranger, _stranger_id = _member(admin, None, "pend-stranger", "陌生人", "editor")
    assert _pending(stranger) == []

    # even an editor membership on an empty-chain project grants no inbox item
    added = admin.post(
        f"/projects/{project_id}/members",
        json={"username": "pend-stranger", "role": "editor"},
    )
    assert added.status_code == 201, added.text
    assert _pending(stranger) == []

    # membership on a chained project only helps when listed at the current level
    alice, alice_id = _member(admin, project_id, "pend-alice", "审批甲", "editor")
    _patch_chain(admin, project_id, [_stage([alice_id])])
    assert version["id"] in _ids(_pending(alice))
    assert _pending(stranger) == []


def test_only_submitted_versions_appear(db, tmp_path):
    admin = _client()
    owner, _owner_id = _member(admin, None, "pend-owner", "项目负责人", "editor")
    project_id, package_id = _seed(owner, tmp_path)

    draft = _save(owner, project_id, "草稿", package_id)
    pending = _save(owner, project_id, "待审", package_id)
    _submit(owner, project_id, pending["id"])
    approved = _save(owner, project_id, "已定稿", package_id)
    _submit(owner, project_id, approved["id"])
    _review(owner, project_id, approved["id"], "approve")
    rejected = _save(owner, project_id, "已驳回", package_id)
    _submit(owner, project_id, rejected["id"])
    _review(owner, project_id, rejected["id"], "reject")

    assert _ids(_pending(owner)) == [pending["id"]]
    assert draft["id"] not in _ids(_pending(admin))

    # resubmitting a rejected version puts it back into the inbox
    _submit(owner, project_id, rejected["id"], "已整改")
    assert _ids(_pending(owner)) == [rejected["id"], pending["id"]]


def test_viewer_gets_empty_and_login_required(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    version = _save(admin, project_id, "只读测试", package_id)
    _submit(admin, project_id, version["id"])

    # global viewer: empty inbox even with an editor membership on the project
    viewer = _viewer(admin, project_id, "pend-viewer", "只读成员")
    assert _pending(viewer) == []

    # the endpoint requires login (auth guard covers /approvals/)
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    anonymous = TestClient(create_app())
    response = anonymous.get("/approvals/pending")
    assert response.status_code == 401


# ------------------------------------------------------------------ 形状与排序


def test_payload_shape_sorting_and_none_last(db, tmp_path):
    admin = _client()
    owner, _owner_id = _member(admin, None, "pend-owner", "项目负责人", "editor")
    project_id, package_id = _seed(owner, tmp_path)
    first = _save(owner, project_id, "最早", package_id)
    second = _save(owner, project_id, "居中", package_id)
    third = _save(owner, project_id, "最新", package_id)
    for version in (first, second, third):
        _submit(owner, project_id, version["id"])

    # force deterministic timestamps: one legacy row without submitted_at
    from bidcraft.db import session_scope
    from bidcraft.models import BoqQuoteVersion

    with session_scope() as session:
        session.get(BoqQuoteVersion, first["id"]).submitted_at = None
        session.get(BoqQuoteVersion, second["id"]).submitted_at = datetime(2026, 1, 1, 8, 0, 0)
        session.get(BoqQuoteVersion, third["id"]).submitted_at = datetime(2026, 6, 1, 8, 0, 0)

    items = _pending(owner)
    assert _ids(items) == [third["id"], second["id"], first["id"]]
    assert items[0]["submitted_at"] == "2026-06-01T08:00:00+00:00"
    assert items[1]["submitted_at"] == "2026-01-01T08:00:00+00:00"
    assert items[2]["submitted_at"] is None  # None sorts last
    for item in items:
        assert set(item) == PENDING_KEYS
        assert item["project_id"] == project_id
        assert item["project_name"] == "待审项目"
        assert item["package_id"] == package_id
        assert item["stage"] == 1
        assert item["stage_count"] == 1
        assert item["mode"] == ""
        assert item["submitted_by"] == "项目负责人"
        assert item["total_amount"] == 25.0


def test_cross_project_aggregation(db, tmp_path):
    admin = _client()
    owner, owner_id = _member(admin, None, "pend-owner", "项目负责人", "editor")

    # A: owned, empty chain; B: owned, chained to the owner; C: admin-owned,
    # owner is an editor member and the level-1 approver.
    project_a, package_a = _seed(owner, tmp_path, name="项目甲", filename="a.xlsx")
    project_b, package_b = _seed(owner, tmp_path, name="项目乙", filename="b.xlsx")
    _patch_chain(owner, project_b, [_stage([owner_id])])
    project_c, package_c = _seed(admin, tmp_path, name="项目丙", filename="c.xlsx")
    added = admin.post(
        f"/projects/{project_c}/members",
        json={"username": "pend-owner", "role": "editor"},
    )
    assert added.status_code == 201, added.text
    _patch_chain(admin, project_c, [_stage([owner_id])])

    version_a = _save(owner, project_a, "报价甲", package_a)
    version_b = _save(owner, project_b, "报价乙", package_b)
    version_c = _save(owner, project_c, "报价丙", package_c)
    for project_id, version in (
        (project_a, version_a),
        (project_b, version_b),
        (project_c, version_c),
    ):
        _submit(owner, project_id, version["id"])

    items = _pending(owner)
    assert len(items) == 3
    assert {item["project_id"] for item in items} == {project_a, project_b, project_c}
    # submitted_at descending: the most recent submission (C) comes first
    assert _ids(items) == [version_c["id"], version_b["id"], version_a["id"]]
    by_version = {item["version_id"]: item for item in items}
    assert by_version[version_a["id"]]["mode"] == ""
    assert by_version[version_b["id"]]["mode"] == "all"
    assert by_version[version_c["id"]]["project_name"] == "项目丙"

    # super admin inbox aggregates the same three projects
    admin_items = _pending(admin)
    assert {item["project_id"] for item in admin_items} >= {
        project_a,
        project_b,
        project_c,
    }


def test_pending_limit_and_none_last_direct(db):
    from bidcraft import boq
    from bidcraft.db import session_scope
    from bidcraft.models import BoqQuoteVersion, Project, utcnow

    user = {"id": "u-limit", "role": "manager"}
    with session_scope() as session:
        session.add(Project(id="p-limit", name="限额项目", owner_id="u-limit", meta={}))
        session.flush()
        base = utcnow()
        for index in range(101):
            session.add(
                BoqQuoteVersion(
                    project_id="p-limit",
                    name=f"v{index}",
                    status="submitted",
                    submitted_by="提交人",
                    submitted_at=(
                        None if index in (0, 1) else base + timedelta(minutes=index)
                    ),
                    summary={"total_amount": float(index)},
                )
            )
        session.flush()
        items = boq.pending_for_user(session, user)

    assert len(items) == 100
    assert items[0]["version_name"] == "v100"  # newest first
    # 99 timestamped rows fill the cap first; the two None rows sort last and
    # only the first of them (stable order) survives the 100-item cut.
    assert items[-1]["version_name"] == "v0"
    assert items[-1]["submitted_at"] is None
    stamps = [item["submitted_at"] for item in items if item["submitted_at"] is not None]
    assert len(stamps) == 99
    assert stamps == sorted(stamps, reverse=True)
    # global viewer short-circuits even with visible projects
    with session_scope() as session:
        assert boq.pending_for_user(session, {"id": "u-limit", "role": "viewer"}) == []
