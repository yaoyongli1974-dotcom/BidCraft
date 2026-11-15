from __future__ import annotations

import io
from urllib.parse import unquote

from bidcraft.rules.loader import list_packages, load_package, validate_package


def test_all_packages_validate():
    for item in list_packages():
        problems = validate_package(str(item["name"]))
        assert problems == [], f"{item['name']}: {problems}"


def test_cabling_package_extends_common():
    package = load_package("weakcurrent-cabling")
    keys = [field.key for field in package.fields]
    assert "project.name" in keys
    assert "cabling.point_count" in keys
    assert any(check.id == "CBL-001" for check in package.checks)
    assert any(check.id == "COM-001" for check in package.checks)
    assert len(package.outline) >= 10


def test_field_required_flags():
    package = load_package("weakcurrent-cabling")
    mapping = package.field_map()
    assert mapping["project.name"].required
    assert mapping["cabling.point_count"].required


# --------------------------------------------------- bid packages / shared ledger

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _project(client, name: str = "多包件项目") -> str:
    created = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _write_boq(path, rows) -> object:
    from openpyxl import Workbook

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


def _row(payload, boq_row):
    return next(row for row in payload["rows"] if row["boq_row"] == boq_row)


def _audit_actions(project_id: str) -> list[str]:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project_id)
            .order_by(AuditLog.id)
        )
        return [row.action for row in rows]


def test_package_crud_duplicate_name_and_audit(db):
    client = _client()
    project_id = _project(client)

    assert client.get(f"/projects/{project_id}/packages").json() == []

    created = client.post(
        f"/projects/{project_id}/packages", json={"name": "一标段", "number": "A-01"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"id", "name", "number", "position", "has_boq", "created_at"}
    assert body["name"] == "一标段"
    assert body["number"] == "A-01"
    assert body["position"] == 0
    assert body["has_boq"] is False
    assert body["created_at"]

    duplicate = client.post(f"/projects/{project_id}/packages", json={"name": "一标段"})
    assert duplicate.status_code == 409
    assert "已存在" in duplicate.json()["detail"]

    second = client.post(
        f"/projects/{project_id}/packages", json={"name": "二标段", "number": "A-02"}
    )
    assert second.status_code == 201
    second_id = second.json()["id"]
    assert second.json()["position"] == 1

    renamed = client.patch(
        f"/projects/{project_id}/packages/{body['id']}",
        json={"name": "一标段（改）", "number": "B-01"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "一标段（改）"
    assert renamed.json()["number"] == "B-01"

    clash = client.patch(
        f"/projects/{project_id}/packages/{second_id}", json={"name": "一标段（改）"}
    )
    assert clash.status_code == 409
    assert "已存在" in clash.json()["detail"]

    listed = client.get(f"/projects/{project_id}/packages").json()
    assert [item["name"] for item in listed] == ["一标段（改）", "二标段"]

    assert (
        client.patch(
            f"/projects/{project_id}/packages/missing", json={"name": "不存在"}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/projects/{project_id}/packages/missing").status_code == 404
    )

    deleted = client.delete(f"/projects/{project_id}/packages/{second_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": second_id}
    assert len(client.get(f"/projects/{project_id}/packages").json()) == 1

    actions = _audit_actions(project_id)
    assert actions.count("project.package_create") == 2
    assert actions.count("project.package_update") == 1
    assert actions.count("project.package_delete") == 1


def test_first_package_adopts_legacy_null_snapshot(db, tmp_path):
    client = _client()
    project_id = _project(client, "收养快照项目")

    boq_path = _write_boq(
        tmp_path / "legacy-boq.xlsx", [[1, "网络面板", "双口 Cat6A", "个", "480"]]
    )
    uploaded = _upload(client, project_id, boq_path)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["snapshot"]["package_id"] is None
    snapshot_id = uploaded.json()["snapshot"]["id"]

    legacy = client.get(f"/projects/{project_id}/boq")
    assert legacy.status_code == 200
    assert legacy.json()["snapshot"]["id"] == snapshot_id

    package = client.post(f"/projects/{project_id}/packages", json={"name": "一标段"})
    assert package.status_code == 201
    package_id = package.json()["id"]
    assert package.json()["has_boq"] is True

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import BoqSnapshot

    with session_scope() as session:
        snapshot = session.scalar(
            select(BoqSnapshot).where(BoqSnapshot.project_id == project_id)
        )
        assert snapshot is not None
        assert snapshot.package_id == package_id

    # Once packages exist every BOQ call must name one.
    missing = client.get(f"/projects/{project_id}/boq")
    assert missing.status_code == 400
    assert "请指定包件" in missing.json()["detail"]

    scoped = client.get(f"/projects/{project_id}/boq?package_id={package_id}")
    assert scoped.status_code == 200
    assert scoped.json()["snapshot"]["id"] == snapshot_id
    assert scoped.json()["snapshot"]["package_id"] == package_id

    other = client.post(f"/projects/{project_id}/packages", json={"name": "二标段"})
    assert other.status_code == 201
    assert other.json()["has_boq"] is False
    empty = client.get(f"/projects/{project_id}/boq?package_id={other.json()['id']}")
    assert empty.status_code == 200
    assert empty.json()["snapshot"] is None
    assert empty.json()["rows"] == []


def test_two_packages_keep_actions_isolated_but_share_ledger(db, tmp_path):
    client = _client()
    project_id = _project(client, "双包件项目")
    first = client.post(f"/projects/{project_id}/packages", json={"name": "一标段"}).json()
    second = client.post(
        f"/projects/{project_id}/packages", json={"name": "二标段"}
    ).json()

    first_boq = _write_boq(
        tmp_path / "first.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "480"],
            [2, "配线架", "24 口", "个", "20"],
        ],
    )
    second_boq = _write_boq(tmp_path / "second.xlsx", [[1, "机柜", "42U", "台", "4"]])

    up_first = _upload(client, project_id, first_boq, first["id"])
    assert up_first.status_code == 200, up_first.text
    up_second = _upload(client, project_id, second_boq, second["id"])
    assert up_second.status_code == 200, up_second.text
    assert up_first.json()["snapshot"]["filename"] == "first.xlsx"
    assert up_second.json()["snapshot"]["filename"] == "second.xlsx"

    # Re-reading one package never leaks the other package's snapshot.
    reread_first = client.get(f"/projects/{project_id}/boq?package_id={first['id']}").json()
    assert reread_first["snapshot"]["filename"] == "first.xlsx"
    assert reread_first["summary"]["boq_rows"] == 2
    reread_second = client.get(
        f"/projects/{project_id}/boq?package_id={second['id']}"
    ).json()
    assert reread_second["snapshot"]["filename"] == "second.xlsx"
    assert reread_second["summary"]["boq_rows"] == 1

    priced = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 2, "unit_price": 10, "package_id": first["id"]},
    )
    assert priced.status_code == 200, priced.text
    assert _row(priced.json(), 2)["price"] == {"unit_price": 10.0, "amount": 4800.0}
    other = client.get(f"/projects/{project_id}/boq?package_id={second['id']}").json()
    assert _row(other, 2)["price"] is None
    assert other["summary"]["total_amount"] == 0.0

    ignored = client.post(
        f"/projects/{project_id}/boq/apply",
        json={"boq_row": 2, "action": "ignore", "package_id": first["id"]},
    )
    assert ignored.status_code == 200, ignored.text
    assert _row(ignored.json(), 2)["ignored"] is True
    other = client.get(f"/projects/{project_id}/boq?package_id={second['id']}").json()
    assert _row(other, 2)["ignored"] is False

    # apply "add" writes to the shared project ledger, visible from both packages.
    added = client.post(
        f"/projects/{project_id}/boq/apply",
        json={"boq_row": 2, "action": "add", "package_id": first["id"]},
    )
    assert added.status_code == 200, added.text
    assert _row(added.json(), 2)["status"] == "matched"
    assert added.json()["ledger_summary"]["materials"] == 1
    other = client.get(f"/projects/{project_id}/boq?package_id={second['id']}").json()
    assert other["ledger_summary"]["materials"] == 1
    assert other["summary"]["missing_in_boq"] == 1
    assert other["snapshot"]["filename"] == "second.xlsx"
    assert _row(other, 2)["status"] == "missing_in_ledger"

    # Deleting one package keeps the other package's snapshot intact.
    assert (
        client.delete(f"/projects/{project_id}/boq?package_id={first['id']}").status_code
        == 200
    )
    assert client.get(f"/projects/{project_id}/boq?package_id={first['id']}").json()[
        "snapshot"
    ] is None
    assert (
        client.get(f"/projects/{project_id}/boq?package_id={second['id']}").json()[
            "snapshot"
        ]["filename"]
        == "second.xlsx"
    )


def test_package_id_validation_errors(db, tmp_path):
    client = _client()
    project_id = _project(client, "包件校验项目")
    package_id = client.post(
        f"/projects/{project_id}/packages", json={"name": "一标段"}
    ).json()["id"]
    boq_path = _write_boq(tmp_path / "boq.xlsx", [[1, "机柜", "42U", "台", "4"]])

    assert client.get(f"/projects/{project_id}/boq").status_code == 400
    assert _upload(client, project_id, boq_path).status_code == 400
    assert (
        client.post(
            f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "ignore"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/projects/{project_id}/boq/price", json={"boq_row": 2, "unit_price": 1}
        ).status_code
        == 400
    )
    assert client.delete(f"/projects/{project_id}/boq").status_code == 400
    assert client.post(f"/projects/{project_id}/boq/export").status_code == 400

    unknown = client.get(f"/projects/{project_id}/boq?package_id=missing").json()
    assert unknown["detail"].startswith("包件不存在")
    assert client.get(f"/projects/{project_id}/boq?package_id=missing").status_code == 404

    # A project without packages rejects an explicit package id (only NULL allowed).
    plain_id = _project(client, "无包件项目")
    plain = client.get(f"/projects/{plain_id}/boq?package_id={package_id}")
    assert plain.status_code == 400
    assert "尚未启用包件" in plain.json()["detail"]


def test_delete_package_removes_snapshot_source_and_file(db, tmp_path):
    client = _client()
    project_id = _project(client, "删包项目")
    package_id = client.post(
        f"/projects/{project_id}/packages", json={"name": "一标段"}
    ).json()["id"]
    boq_path = _write_boq(tmp_path / "boq.xlsx", [[1, "机柜", "42U", "台", "4"]])
    assert _upload(client, project_id, boq_path, package_id).status_code == 200

    from pathlib import Path

    from sqlalchemy import func, select

    from bidcraft.db import session_scope
    from bidcraft.models import BoqSnapshot, SourceFile

    with session_scope() as session:
        stored_path = Path(
            session.scalar(
                select(SourceFile.stored_path).where(
                    SourceFile.project_id == project_id, SourceFile.role == "boq"
                )
            )
        )
    assert stored_path.is_file()

    deleted = client.delete(f"/projects/{project_id}/packages/{package_id}")
    assert deleted.status_code == 200
    assert not stored_path.exists()

    with session_scope() as session:
        snapshots = session.scalar(
            select(func.count())
            .select_from(BoqSnapshot)
            .where(BoqSnapshot.project_id == project_id)
        )
        sources = session.scalar(
            select(func.count())
            .select_from(SourceFile)
            .where(SourceFile.project_id == project_id, SourceFile.role == "boq")
        )
    assert snapshots == 0
    assert sources == 0

    # With no packages left the legacy project-wide BOQ view is available again.
    remaining = client.get(f"/projects/{project_id}/boq")
    assert remaining.status_code == 200
    assert remaining.json()["snapshot"] is None


def test_export_filename_contains_package_name(db, tmp_path):
    client = _client()
    project_id = _project(client, "导出项目")
    package_id = client.post(
        f"/projects/{project_id}/packages", json={"name": "二标段"}
    ).json()["id"]
    boq_path = _write_boq(tmp_path / "boq.xlsx", [[1, "机柜", "42U", "台", "4"]])
    assert _upload(client, project_id, boq_path, package_id).status_code == 200

    exported = client.post(f"/projects/{project_id}/boq/export?package_id={package_id}")
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"
    disposition = unquote(exported.headers["content-disposition"])
    assert "导出项目-二标段-清单响应表.xlsx" in disposition

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames == ["清单对齐", "报价汇总", "差异汇总"]


def test_viewer_cannot_mutate_packages(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project_id = _project(admin, "只读包件项目")
    package_id = admin.post(
        f"/projects/{project_id}/packages", json={"name": "一标段"}
    ).json()["id"]

    created = admin.post(
        "/admin/users",
        json={"username": "package-viewer", "password": "viewer-pass-1", "role": "viewer"},
    )
    assert created.status_code == 201, created.text
    member = admin.post(
        f"/projects/{project_id}/members",
        json={"username": "package-viewer", "role": "viewer"},
    )
    assert member.status_code == 201, member.text

    viewer = TestClient(create_app())
    assert viewer.post(
        "/auth/login", json={"username": "package-viewer", "password": "viewer-pass-1"}
    ).status_code == 200

    assert viewer.get(f"/projects/{project_id}/packages").status_code == 200
    assert (
        viewer.get(f"/projects/{project_id}/boq?package_id={package_id}").status_code
        == 200
    )
    assert (
        viewer.post(
            f"/projects/{project_id}/packages", json={"name": "只读新增"}
        ).status_code
        == 403
    )
    assert (
        viewer.patch(
            f"/projects/{project_id}/packages/{package_id}", json={"name": "改名"}
        ).status_code
        == 403
    )
    assert (
        viewer.delete(f"/projects/{project_id}/packages/{package_id}").status_code == 403
    )
