"""报价审批多级流转：项目审批链（R18，R19 起回显 v2 阶段格式）。

Covers the contract's A5 matrix: PATCH approval_chain validation and echo
(R19: single-approver ``all`` stages), two-stage sequential review (403 for
the wrong level, super-admin override), mid-chain reject + resubmit restart,
empty-chain regression to the R14 single-owner behavior, payload fields and
the legacy-schema migration.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ADMIN_NAME = "超级管理员"
MEMBER_PASS = "member-pass-1"


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


def _project(client, name: str = "审批链项目") -> str:
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


def _seed(client, tmp_path, *, package: bool = True):
    project_id = _project(client)
    package_id = _package(client, project_id) if package else None
    path = _write_boq(
        tmp_path / "chain.xlsx",
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
    return client.post(
        f"/projects/{project_id}/boq/versions/{version_id}/submit",
        json={"note": note},
    )


def _review(client, project_id: str, version_id: str, decision: str, note: str = ""):
    return client.post(
        f"/projects/{project_id}/boq/versions/{version_id}/review",
        json={"decision": decision, "note": note},
    )


def _detail(client, project_id: str, version_id: str):
    response = client.get(f"/projects/{project_id}/boq/versions/{version_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _patch_chain(client, project_id: str, ids):
    return client.patch(f"/projects/{project_id}", json={"approval_chain": ids})


def _chain_payload(client, project_id: str):
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()["approval_chain"]


def _audit(project_id: str):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as db:
        rows = list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.project_id == project_id)
                .order_by(AuditLog.id)
            )
        )
        return [(row.action, dict(row.detail or {})) for row in rows]


def _two_approver_team(admin, tmp_path):
    """Project + package + two editor members wired as a 2-level chain."""
    project_id, package_id = _seed(admin, tmp_path)
    alice, alice_id = _member(admin, project_id, "chain-alice", "审批甲", "editor")
    bob, bob_id = _member(admin, project_id, "chain-bob", "审批乙", "editor")
    patched = _patch_chain(admin, project_id, [alice_id, bob_id])
    assert patched.status_code == 200, patched.text
    return project_id, package_id, alice, alice_id, bob, bob_id


# ------------------------------------------------------------------ PATCH 链


def test_patch_chain_owner_sets_and_echoes(db, tmp_path):
    admin = _client()
    project_id, _package_id, _alice, alice_id, _bob, bob_id = _two_approver_team(
        admin, tmp_path
    )

    expected = [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"},
        {"approvers": [{"id": bob_id, "name": "审批乙"}], "mode": "all"},
    ]
    assert _chain_payload(admin, project_id) == expected
    # PATCH response itself echoes the chain too
    patched = _patch_chain(admin, project_id, [alice_id, bob_id])
    assert patched.json()["approval_chain"] == expected

    # duplicates collapse while keeping order
    dedup = _patch_chain(admin, project_id, [bob_id, alice_id, bob_id])
    assert dedup.status_code == 200, dedup.text
    assert dedup.json()["approval_chain"] == [
        {"approvers": [{"id": bob_id, "name": "审批乙"}], "mode": "all"},
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"},
    ]

    # audit trail records the field
    updates = [detail for action, detail in _audit(project_id) if action == "project.update"]
    assert any("approval_chain" in detail.get("keys", []) for detail in updates)

    # empty list clears the chain (back to single-owner review)
    cleared = _patch_chain(admin, project_id, [])
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["approval_chain"] == []
    assert _chain_payload(admin, project_id) == []


def test_patch_chain_validation(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "chain-alice", "审批甲", "editor")
    editor, _editor_id = _member(admin, project_id, "chain-editor", "编辑丙", "editor")

    # unknown user id -> 400
    unknown = _patch_chain(admin, project_id, [alice_id, "no-such-user"])
    assert unknown.status_code == 400
    assert "审批人不存在" in unknown.json()["detail"]
    # the failed PATCH must not have written a partial chain
    assert _chain_payload(admin, project_id) == []

    # more than 5 approvers -> 400
    six_ids = [alice_id]
    for index in range(5):
        _client_i, user_id = _member(
            admin, project_id, f"chain-extra-{index}", f"审批extra{index}", "editor"
        )
        six_ids.append(user_id)
    too_many = _patch_chain(admin, project_id, six_ids)
    assert too_many.status_code == 400
    assert _chain_payload(admin, project_id) == []

    # exactly 5 is fine
    five = _patch_chain(admin, project_id, six_ids[:5])
    assert five.status_code == 200, five.text
    assert len(five.json()["approval_chain"]) == 5

    # editors cannot touch the chain (owner-only), other fields still writable
    forbidden = _patch_chain(editor, project_id, [alice_id])
    assert forbidden.status_code == 403
    renamed = editor.patch(f"/projects/{project_id}", json={"bid_section": "X 标段"})
    assert renamed.status_code == 200, renamed.text
    assert _chain_payload(admin, project_id) == five.json()["approval_chain"]


def test_patch_chain_name_is_a_snapshot(db, tmp_path):
    admin = _client()
    project_id, _package_id = _seed(admin, tmp_path)
    _alice, alice_id = _member(admin, project_id, "chain-alice", "审批甲", "editor")

    assert _patch_chain(admin, project_id, [alice_id]).status_code == 200
    # rename the user afterwards: the chain keeps the original snapshot
    renamed = admin.patch(f"/admin/users/{alice_id}", json={"display_name": "新名字"})
    assert renamed.status_code == 200, renamed.text
    assert _chain_payload(admin, project_id) == [
        {"approvers": [{"id": alice_id, "name": "审批甲"}], "mode": "all"}
    ]


# ------------------------------------------------------------------ 两级流转


def test_two_stage_sequential_review(db, tmp_path):
    admin = _client()
    project_id, package_id, alice, alice_id, bob, bob_id = _two_approver_team(
        admin, tmp_path
    )
    version = _save(alice, project_id, "链式报价", package_id)
    assert _submit(alice, project_id, version["id"], "请逐级审批").status_code == 200

    body = _detail(admin, project_id, version["id"])
    assert body["status"] == "submitted"
    assert body["approvals"] == []
    assert body["approval_stage"] == 0

    # wrong level: bob is level 2, level 1 is pending -> 403 with 第 1 级
    early = _review(bob, project_id, version["id"], "approve")
    assert early.status_code == 403
    assert "第 1 级" in early.json()["detail"]
    # a member outside the chain cannot review either
    outsider, _outsider_id = _member(
        admin, project_id, "chain-outsider", "局外人", "editor"
    )
    assert _review(outsider, project_id, version["id"], "approve").status_code == 403
    # state untouched by the refused attempts
    assert _detail(admin, project_id, version["id"])["approval_stage"] == 0

    # level 1 approves -> still submitted, one record, review_* untouched
    first = _review(alice, project_id, version["id"], "approve", "一级同意")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 1
    assert len(body["approvals"]) == 1
    record = body["approvals"][0]
    assert record["stage"] == 0
    assert record["user_id"] == alice_id
    assert record["name"] == "审批甲"
    assert record["decision"] == "approve"
    assert record["note"] == "一级同意"
    assert datetime.fromisoformat(record["at"])
    assert body["review_note"] == ""
    assert body["reviewed_by"] == ""
    assert body["reviewed_at"] is None

    # level 1 cannot approve twice: now level 2 is expected
    again = _review(alice, project_id, version["id"], "approve")
    assert again.status_code == 403
    assert "第 2 级" in again.json()["detail"]

    # level 2 approves -> approved with the final review trail
    final = _review(bob, project_id, version["id"], "approve", "二级定稿")
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["status"] == "approved"
    assert body["approval_stage"] == 0  # only meaningful while submitted
    assert len(body["approvals"]) == 2
    assert body["approvals"][1]["user_id"] == bob_id
    assert body["approvals"][1]["stage"] == 1
    assert body["review_note"] == "二级定稿"
    assert body["reviewed_by"] == "审批乙"
    assert datetime.fromisoformat(body["reviewed_at"])

    # list payload carries the same fields
    listed = admin.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
    ).json()
    approved_item = next(item for item in listed if item["id"] == version["id"])
    assert len(approved_item["approvals"]) == 2
    assert approved_item["approval_stage"] == 0

    # audit keeps quote.approve for every level (intermediate ones included)
    actions = [action for action, _detail in _audit(project_id)]
    assert actions.count("quote.approve") >= 2


def test_super_admin_may_review_any_level(db, tmp_path):
    admin = _client()
    project_id, package_id, alice, alice_id, _bob, _bob_id = _two_approver_team(
        admin, tmp_path
    )
    admin_id = admin.get("/auth/me").json()["user"]["id"]
    version = _save(alice, project_id, "超管代审", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200

    # super admin signs level 1 instead of alice
    first = _review(admin, project_id, version["id"], "approve", "超管一级")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "submitted"
    assert body["approval_stage"] == 1
    assert body["approvals"][0]["user_id"] == admin_id
    assert body["approvals"][0]["name"] == ADMIN_NAME

    # ... and level 2 instead of bob
    final = _review(admin, project_id, version["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert len(final.json()["approvals"]) == 2
    assert _chain_payload(admin, project_id)[0]["approvers"][0]["id"] == alice_id


def test_final_stage_retires_previous_approved(db, tmp_path):
    admin = _client()
    project_id, package_id, alice, alice_id, _bob, _bob_id = _two_approver_team(
        admin, tmp_path
    )
    # single-level chain: one approve is already final
    assert _patch_chain(admin, project_id, [alice_id]).status_code == 200

    first = _save(alice, project_id, "旧定稿", package_id)
    assert _submit(alice, project_id, first["id"]).status_code == 200
    assert _review(alice, project_id, first["id"], "approve").status_code == 200
    assert _detail(admin, project_id, first["id"])["status"] == "approved"

    second = _save(alice, project_id, "新定稿", package_id)
    assert _submit(alice, project_id, second["id"]).status_code == 200
    final = _review(alice, project_id, second["id"], "approve")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    # the previous winner is retired to draft, keeping its approval trail
    retired = _detail(admin, project_id, first["id"])
    assert retired["status"] == "draft"
    assert len(retired["approvals"]) == 1


# ------------------------------------------------------------------ 驳回与重提


def test_mid_chain_reject_and_resubmit_restart(db, tmp_path):
    admin = _client()
    project_id, package_id, alice, _alice_id, bob, _bob_id = _two_approver_team(
        admin, tmp_path
    )
    version = _save(alice, project_id, "驳回重提", package_id)
    assert _submit(alice, project_id, version["id"]).status_code == 200
    assert _review(alice, project_id, version["id"], "approve").status_code == 200

    # level 2 rejects mid-chain -> rejected immediately, both records kept
    rejected = _review(bob, project_id, version["id"], "reject", "二级驳回")
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["approval_stage"] == 0
    assert [record["decision"] for record in body["approvals"]] == ["approve", "reject"]
    assert body["approvals"][1]["note"] == "二级驳回"
    assert body["review_note"] == "二级驳回"
    assert body["reviewed_by"] == "审批乙"
    assert datetime.fromisoformat(body["reviewed_at"])

    # resubmit clears the chain progress and the stale review trail
    resubmitted = _submit(alice, project_id, version["id"], "已整改")
    assert resubmitted.status_code == 200, resubmitted.text
    body = resubmitted.json()
    assert body["status"] == "submitted"
    assert body["approvals"] == []
    assert body["approval_stage"] == 0
    assert body["review_note"] == ""
    assert body["reviewed_by"] == ""
    assert body["reviewed_at"] is None

    # the chain restarts at level 1: bob is refused again until alice acts
    assert _review(bob, project_id, version["id"], "approve").status_code == 403

    # level 1 may also reject outright, terminating the round with one record
    rejected_first = _review(alice, project_id, version["id"], "reject", "一级驳回")
    assert rejected_first.status_code == 200, rejected_first.text
    body = rejected_first.json()
    assert body["status"] == "rejected"
    assert len(body["approvals"]) == 1
    assert body["approvals"][0]["decision"] == "reject"

    # another resubmit walks the happy path back up to level 2
    assert _submit(alice, project_id, version["id"]).status_code == 200
    first = _review(alice, project_id, version["id"], "approve")
    assert first.status_code == 200, first.text
    assert first.json()["approval_stage"] == 1


# ------------------------------------------------------------------ 空链回归


def test_empty_chain_single_owner_regression(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    assert _chain_payload(admin, project_id) == []
    editor, _editor_id = _member(admin, project_id, "chain-editor", "编辑丙", "editor")

    version = _save(editor, project_id, "空链报价", package_id)
    assert _submit(editor, project_id, version["id"]).status_code == 200
    # editor still cannot review without a chain
    assert _review(editor, project_id, version["id"], "approve").status_code == 403
    assert _review(editor, project_id, version["id"], "reject").status_code == 403
    # the owner/super admin approves in one step, approvals stays []
    approved = _review(admin, project_id, version["id"], "approve", "负责人定稿")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["approvals"] == []
    assert body["approval_stage"] == 0
    assert body["reviewed_by"] == ADMIN_NAME


def test_empty_chain_non_super_owner_can_review(db, tmp_path):
    admin = _client()
    # a plain (non-super) owner: role=="owner" must suffice without a chain
    owner, _owner_id = _member(admin, None, "chain-owner", "负责人", "editor")
    project_id, package_id = _seed(owner, tmp_path)
    editor, _editor_id = _member(admin, project_id, "chain-editor", "编辑丙", "editor")
    # owner is not a super admin, so membership is required for the editor only
    version = _save(editor, project_id, "负责人单级", package_id)
    assert _submit(editor, project_id, version["id"]).status_code == 200
    assert _review(editor, project_id, version["id"], "approve").status_code == 403
    approved = _review(owner, project_id, version["id"], "approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approvals"] == []


# ------------------------------------------------------------------ payload/迁移


def test_payload_defaults_on_draft(db, tmp_path):
    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    version = _save(admin, project_id, "默认字段", package_id)
    assert version["approvals"] == []
    assert version["approval_stage"] == 0
    detail = _detail(admin, project_id, version["id"])
    assert detail["approvals"] == []
    assert detail["approval_stage"] == 0
    listed = admin.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
    ).json()
    assert listed[0]["approvals"] == []
    assert listed[0]["approval_stage"] == 0


def test_legacy_schema_migration_adds_approvals(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    from bidcraft.boq import version_payload
    from bidcraft.db import _migrate_columns
    from bidcraft.models import BoqQuoteVersion

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'legacy-chain.db'}", future=True
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE boq_quote_versions ("
                "id VARCHAR(36) PRIMARY KEY, "
                "project_id VARCHAR(36), "
                "package_id VARCHAR(36), "
                "name VARCHAR(80) NOT NULL, "
                "note VARCHAR(200), "
                "prices JSON, "
                "summary JSON, "
                "created_at DATETIME, "
                "status VARCHAR(16) DEFAULT 'draft', "
                "submit_note VARCHAR(200) DEFAULT '', "
                "submitted_by VARCHAR(80) DEFAULT '', "
                "submitted_at DATETIME, "
                "review_note VARCHAR(200) DEFAULT '', "
                "reviewed_by VARCHAR(80) DEFAULT '', "
                "reviewed_at DATETIME, "
                "basis JSON)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO boq_quote_versions "
                "(id, project_id, package_id, name, note, prices, summary, created_at) "
                "VALUES ('legacy-1', 'proj-1', NULL, '旧版本', '', '{}', '{}', "
                "'2026-01-01 00:00:00')"
            )
        )

    _migrate_columns(engine)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("boq_quote_versions")
    }
    assert "approvals" in columns

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        row = session.get(BoqQuoteVersion, "legacy-1")
        assert row is not None
        # migrated rows default to an empty approval trail
        assert row.approvals == []
        payload = version_payload(row)
        assert payload["approvals"] == []
        assert payload["approval_stage"] == 0
        # the new column is writable on the legacy schema too
        row.approvals = [
            {
                "stage": 0,
                "user_id": "u1",
                "name": "甲",
                "decision": "approve",
                "note": "",
                "at": "2026-01-01T00:00:00",
            }
        ]
        session.commit()
    finally:
        session.close()

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        row = session.get(BoqQuoteVersion, "legacy-1")
        assert len(row.approvals) == 1
        assert row.approvals[0]["decision"] == "approve"
    finally:
        session.close()
        engine.dispose()
