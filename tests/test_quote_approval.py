"""报价审批流：提交、审批、驳回、撤销定稿与自动退回（R14）。

Covers the contract's A4 matrix: happy path, auto-withdraw of the previous
approved version in the same package, state-machine 409s, invalid decision
400, read-only 403, payload fields and the legacy-schema migration.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ADMIN_NAME = "超级管理员"

PAYLOAD_FIELDS = {
    "status",
    "submit_note",
    "submitted_by",
    "submitted_at",
    "review_note",
    "reviewed_by",
    "reviewed_at",
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


def _project(client, name: str = "审批流项目") -> str:
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
        tmp_path / "approval.xlsx",
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


def _unapprove(client, project_id: str, version_id: str):
    return client.post(f"/projects/{project_id}/boq/versions/{version_id}/unapprove")


def _status(client, project_id: str, version_id: str) -> str:
    response = client.get(f"/projects/{project_id}/boq/versions/{version_id}")
    assert response.status_code == 200, response.text
    return response.json()["status"]


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


def test_full_approval_flow_and_auto_withdraw(db, tmp_path):
    client = _client()
    project_id, package_id = _seed(client, tmp_path)

    first = _save(client, project_id, "初稿", package_id)
    assert first["status"] == "draft"
    assert first["submitted_by"] == ""
    assert first["submitted_at"] is None
    assert first["reviewed_by"] == ""
    assert first["reviewed_at"] is None

    # draft -> submitted
    submitted = client.post(
        f"/projects/{project_id}/boq/versions/{first['id']}/submit",
        json={"note": "请负责人审批"},
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "submitted"
    assert body["submit_note"] == "请负责人审批"
    assert body["submitted_by"] == ADMIN_NAME
    assert datetime.fromisoformat(body["submitted_at"])
    assert body["review_note"] == ""
    assert body["reviewed_by"] == ""

    # submitted -> rejected
    rejected = _review(client, project_id, first["id"], "reject", "单价偏高")
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["review_note"] == "单价偏高"
    assert body["reviewed_by"] == ADMIN_NAME
    assert datetime.fromisoformat(body["reviewed_at"])

    # rejected -> submitted (a new round clears stale review feedback)
    resubmitted = _submit(client, project_id, first["id"], "已下调单价")
    assert resubmitted.status_code == 200, resubmitted.text
    body = resubmitted.json()
    assert body["status"] == "submitted"
    assert body["submit_note"] == "已下调单价"
    assert body["review_note"] == ""
    assert body["reviewed_by"] == ""
    assert body["reviewed_at"] is None

    # submitted -> approved
    approved = _review(client, project_id, first["id"], "approve", "同意")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["review_note"] == "同意"

    # a second version in the same package is approved -> first auto-withdrawn
    second = _save(client, project_id, "终版", package_id)
    assert second["status"] == "draft"
    assert _submit(client, project_id, second["id"]).status_code == 200
    approved_second = _review(client, project_id, second["id"], "approve")
    assert approved_second.status_code == 200, approved_second.text
    assert approved_second.json()["status"] == "approved"

    retired = client.get(
        f"/projects/{project_id}/boq/versions/{first['id']}"
    ).json()
    assert retired["status"] == "draft"
    # the retired version keeps its own review trail untouched
    assert retired["reviewed_by"] == ADMIN_NAME
    assert retired["review_note"] == "同意"

    # approved -> draft (withdraw the approval itself)
    withdrawn = _unapprove(client, project_id, second["id"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "draft"
    assert _status(client, project_id, second["id"]) == "draft"

    actions = [action for action, _detail in _audit(project_id)]
    assert "quote.submit" in actions
    assert "quote.reject" in actions
    assert "quote.approve" in actions
    assert "quote.unapprove" in actions


def test_auto_withdraw_only_within_same_package(db, tmp_path):
    client = _client()
    project_id, package_a = _seed(client, tmp_path)
    package_b = _package(client, project_id, "B 标段")
    path_b = _write_boq(tmp_path / "b.xlsx", [[1, "桥架", "200x100", "米", "5", ""]])
    assert _upload(client, project_id, path_b, package_id=package_b).status_code == 200

    version_a = _save(client, project_id, "A 定稿", package_a)
    assert _submit(client, project_id, version_a["id"]).status_code == 200
    assert _review(client, project_id, version_a["id"], "approve").status_code == 200

    version_b = _save(client, project_id, "B 定稿", package_b)
    assert _submit(client, project_id, version_b["id"]).status_code == 200
    assert _review(client, project_id, version_b["id"], "approve").status_code == 200

    # B's approval must not retire A's approved version
    assert _status(client, project_id, version_a["id"]) == "approved"
    assert _status(client, project_id, version_b["id"]) == "approved"


def test_payload_fields_on_list_and_detail(db, tmp_path):
    client = _client()
    project_id, package_id = _seed(client, tmp_path)
    version = _save(client, project_id, "字段检查", package_id)
    assert _submit(client, project_id, version["id"], "备注").status_code == 200
    assert _review(client, project_id, version["id"], "approve", "ok").status_code == 200

    detail = client.get(
        f"/projects/{project_id}/boq/versions/{version['id']}"
    ).json()
    assert PAYLOAD_FIELDS <= set(detail)
    assert detail["status"] == "approved"
    assert detail["submit_note"] == "备注"
    assert detail["review_note"] == "ok"
    assert datetime.fromisoformat(detail["submitted_at"])
    assert datetime.fromisoformat(detail["reviewed_at"])
    assert "prices" in detail

    listed = client.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
    ).json()
    assert len(listed) == 1
    assert PAYLOAD_FIELDS <= set(listed[0])
    assert "prices" not in listed[0]
    assert listed[0]["status"] == "approved"


def test_state_machine_conflicts(db, tmp_path):
    client = _client()
    project_id, package_id = _seed(client, tmp_path)
    draft = _save(client, project_id, "草稿", package_id)

    # draft cannot be reviewed or unapproved
    assert _review(client, project_id, draft["id"], "approve").status_code == 409
    assert _review(client, project_id, draft["id"], "reject").status_code == 409
    assert _unapprove(client, project_id, draft["id"]).status_code == 409

    assert _submit(client, project_id, draft["id"]).status_code == 200
    # submitted cannot be submitted again or unapproved
    assert _submit(client, project_id, draft["id"]).status_code == 409

    assert _review(client, project_id, draft["id"], "approve").status_code == 200
    # approved cannot be submitted or reviewed again, only unapproved
    assert _submit(client, project_id, draft["id"]).status_code == 409
    assert _review(client, project_id, draft["id"], "approve").status_code == 409
    assert _unapprove(client, project_id, draft["id"]).status_code == 200
    # draft again: unapprove is refused a second time
    assert _unapprove(client, project_id, draft["id"]).status_code == 409

    missing = "does-not-exist"
    assert _submit(client, project_id, missing).status_code == 404
    assert _review(client, project_id, missing, "approve").status_code == 404
    assert _unapprove(client, project_id, missing).status_code == 404


def test_invalid_decision_is_400(db, tmp_path):
    client = _client()
    project_id, package_id = _seed(client, tmp_path)
    version = _save(client, project_id, "待审", package_id)
    assert _submit(client, project_id, version["id"]).status_code == 200

    bad = _review(client, project_id, version["id"], "maybe", "看不懂")
    assert bad.status_code == 400
    assert "decision" in bad.json()["detail"]
    # rejected decision leaves the state untouched
    assert _status(client, project_id, version["id"]) == "submitted"

    empty = client.post(
        f"/projects/{project_id}/boq/versions/{version['id']}/review",
        json={"note": "缺 decision"},
    )
    assert empty.status_code == 422


def test_viewer_and_editor_permissions(db, tmp_path):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project_id, package_id = _seed(admin, tmp_path)
    version = _save(admin, project_id, "权限基线", package_id)

    def make_member(username: str, role: str, global_role: str = "viewer"):
        created = admin.post(
            "/admin/users",
            json={
                "username": username,
                "display_name": username,
                "password": "member-pass-1",
                "role": global_role,
            },
        )
        assert created.status_code == 201, created.text
        assert (
            admin.post(
                f"/projects/{project_id}/members",
                json={"username": username, "role": role},
            ).status_code
            == 201
        )
        client = TestClient(create_app())
        assert (
            client.post(
                "/auth/login",
                json={"username": username, "password": "member-pass-1"},
            ).status_code
            == 200
        )
        return client

    viewer = make_member("approval-viewer", "viewer")
    editor = make_member("approval-editor", "editor", global_role="manager")

    # viewers can read the status but cannot act at all
    assert (
        viewer.get(
            f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
        ).status_code
        == 200
    )
    listed = viewer.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_id}
    ).json()
    assert listed[0]["status"] == "draft"
    assert _submit(viewer, project_id, version["id"]).status_code == 403
    assert _review(viewer, project_id, version["id"], "approve").status_code == 403
    assert _unapprove(viewer, project_id, version["id"]).status_code == 403

    # editors may submit but not approve/reject (owner or super admin only)
    assert _submit(editor, project_id, version["id"], "编辑提交").status_code == 200
    assert _review(editor, project_id, version["id"], "approve").status_code == 403
    assert _review(editor, project_id, version["id"], "reject").status_code == 403
    # ... and the version is still waiting for the owner
    assert _status(admin, project_id, version["id"]) == "submitted"
    assert _review(admin, project_id, version["id"], "approve").status_code == 200
    # editors may also withdraw an approval
    assert _unapprove(editor, project_id, version["id"]).status_code == 200


def test_legacy_schema_migration_adds_approval_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    from bidcraft.db import _migrate_columns
    from bidcraft.models import BoqQuoteVersion

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'legacy-quote.db'}", future=True
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
                "basis JSON, "
                "created_at DATETIME)"
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
    assert {
        "status",
        "submit_note",
        "submitted_by",
        "submitted_at",
        "review_note",
        "reviewed_by",
        "reviewed_at",
    } <= columns

    # the legacy row is readable through the ORM and defaults to a draft
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        row = session.get(BoqQuoteVersion, "legacy-1")
        assert row is not None
        assert row.status == "draft"
        assert row.submit_note == ""
        assert row.submitted_at is None
        # migrated columns are writable too
        row.status = "submitted"
        row.review_note = "旧库可写"
        session.commit()
    finally:
        session.close()

    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        row = session.get(BoqQuoteVersion, "legacy-1")
        assert row.status == "submitted"
        assert row.review_note == "旧库可写"
    finally:
        session.close()
        engine.dispose()
