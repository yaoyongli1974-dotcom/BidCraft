from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def _project(client, name: str = "报价版本项目") -> str:
    created = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _write_boq(path, rows) -> object:
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


def _seed(client, tmp_path):
    project_id = _project(client)
    boq_path = _write_boq(
        tmp_path / "versions.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "2", ""],
            [2, "线缆", "Cat6A", "米", "10", ""],
        ],
    )
    assert _upload(client, project_id, boq_path).status_code == 200
    return project_id


def _price(client, project_id: str, row: int, price: float | None):
    response = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": row, "unit_price": price},
    )
    assert response.status_code == 200, response.text
    return response.json()


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


def test_version_save_list_detail_restore_delete(db, tmp_path):
    client = _client()
    project_id = _seed(client, tmp_path)
    _price(client, project_id, 2, 12.5)
    _price(client, project_id, 3, 3.0)

    saved = client.post(
        f"/projects/{project_id}/boq/versions",
        json={"name": "初稿", "note": "首轮报价"},
    )
    assert saved.status_code == 201, saved.text
    first = saved.json()
    assert set(first) == {
        "id",
        "name",
        "note",
        "package_id",
        "summary",
        "created_at",
        "price_count",
    }
    assert first["name"] == "初稿"
    assert first["note"] == "首轮报价"
    assert first["package_id"] is None
    assert first["price_count"] == 2
    assert first["summary"]["total_amount"] == 55.0
    assert first["created_at"]

    # change the live prices, then save a second version
    _price(client, project_id, 2, 20.0)
    second = client.post(
        f"/projects/{project_id}/boq/versions", json={"name": "第二版"}
    )
    assert second.status_code == 201, second.text
    assert second.json()["summary"]["total_amount"] == 70.0
    assert second.json()["note"] == ""

    listed = client.get(f"/projects/{project_id}/boq/versions")
    assert listed.status_code == 200, listed.text
    items = listed.json()
    assert [item["name"] for item in items] == ["第二版", "初稿"]
    assert all("prices" not in item for item in items)
    assert [item["price_count"] for item in items] == [2, 2]

    detail = client.get(f"/projects/{project_id}/boq/versions/{first['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["prices"] == {"工程量清单!2": 12.5, "工程量清单!3": 3.0}
    assert body["summary"]["total_amount"] == 55.0

    missing = client.get(f"/projects/{project_id}/boq/versions/does-not-exist")
    assert missing.status_code == 404
    assert client.post(
        f"/projects/{project_id}/boq/versions/does-not-exist/restore"
    ).status_code == 404

    restored = client.post(f"/projects/{project_id}/boq/versions/{first['id']}/restore")
    assert restored.status_code == 200, restored.text
    payload = restored.json()
    assert payload["summary"]["total_amount"] == 55.0
    row_two = next(row for row in payload["rows"] if row["boq_row"] == 2)
    assert row_two["price"]["unit_price"] == 12.5
    # the change was written back to the active snapshot, not only to the response
    live = client.get(f"/projects/{project_id}/boq").json()
    live_two = next(row for row in live["rows"] if row["boq_row"] == 2)
    assert live_two["price"]["unit_price"] == 12.5

    deleted = client.delete(f"/projects/{project_id}/boq/versions/{second.json()['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": True, "id": second.json()["id"]}
    assert client.get(
        f"/projects/{project_id}/boq/versions/{second.json()['id']}"
    ).status_code == 404
    assert len(client.get(f"/projects/{project_id}/boq/versions").json()) == 1

    actions = [action for action, _detail in _audit(project_id)]
    assert "boq.version_save" in actions
    assert "boq.version_restore" in actions
    assert "boq.version_delete" in actions


def test_version_limit_is_twenty(db, tmp_path):
    client = _client()
    project_id = _seed(client, tmp_path)

    for index in range(20):
        saved = client.post(
            f"/projects/{project_id}/boq/versions", json={"name": f"版本 {index + 1}"}
        )
        assert saved.status_code == 201, saved.text

    overflow = client.post(
        f"/projects/{project_id}/boq/versions", json={"name": "版本 21"}
    )
    assert overflow.status_code == 409
    assert "20" in overflow.json()["detail"]
    assert len(client.get(f"/projects/{project_id}/boq/versions").json()) == 20


def test_version_name_validation(db, tmp_path):
    client = _client()
    project_id = _seed(client, tmp_path)

    blank = client.post(f"/projects/{project_id}/boq/versions", json={"name": "   "})
    assert blank.status_code == 400
    assert "名称" in blank.json()["detail"]

    too_long = client.post(
        f"/projects/{project_id}/boq/versions", json={"name": "版" * 81}
    )
    assert too_long.status_code == 400
    assert "80" in too_long.json()["detail"]

    # a version cannot be saved before a BOQ workbook exists
    empty_project = _project(client, "无清单项目")
    no_boq = client.post(
        f"/projects/{empty_project}/boq/versions", json={"name": "空"}
    )
    assert no_boq.status_code == 409


def test_version_viewer_cannot_write(db, tmp_path):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project_id = _seed(admin, tmp_path)
    version_id = admin.post(
        f"/projects/{project_id}/boq/versions", json={"name": "只读基线"}
    ).json()["id"]

    created = admin.post(
        "/admin/users",
        json={
            "username": "version-viewer",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201
    assert (
        admin.post(
            f"/projects/{project_id}/members",
            json={"username": "version-viewer", "role": "viewer"},
        ).status_code
        == 201
    )

    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "version-viewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200, login.text

    assert viewer.get(f"/projects/{project_id}/boq/versions").status_code == 200
    assert viewer.get(
        f"/projects/{project_id}/boq/versions/{version_id}"
    ).status_code == 200
    assert (
        viewer.post(
            f"/projects/{project_id}/boq/versions", json={"name": "越权"}
        ).status_code
        == 403
    )
    assert (
        viewer.post(
            f"/projects/{project_id}/boq/versions/{version_id}/restore"
        ).status_code
        == 403
    )
    assert (
        viewer.delete(
            f"/projects/{project_id}/boq/versions/{version_id}"
        ).status_code
        == 403
    )


def test_versions_follow_package(db, tmp_path):
    client = _client()
    project_id = _project(client, "多包件版本项目")
    package_a = client.post(
        f"/projects/{project_id}/packages", json={"name": "A 标段"}
    ).json()["id"]
    package_b = client.post(
        f"/projects/{project_id}/packages", json={"name": "B 标段"}
    ).json()["id"]

    for package_id, name in ((package_a, "a.xlsx"), (package_b, "b.xlsx")):
        path = _write_boq(tmp_path / name, [[1, "面板", "双口", "个", "2", ""]])
        assert _upload(client, project_id, path, package_id=package_id).status_code == 200

    saved = client.post(
        f"/projects/{project_id}/boq/versions",
        json={"name": "A 首版", "package_id": package_a},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["package_id"] == package_a

    list_a = client.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_a}
    ).json()
    list_b = client.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": package_b}
    ).json()
    assert [item["name"] for item in list_a] == ["A 首版"]
    assert list_b == []

    # packaged projects must name the package explicitly
    assert client.get(f"/projects/{project_id}/boq/versions").status_code == 400

    restored = client.post(
        f"/projects/{project_id}/boq/versions/{saved.json()['id']}/restore"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["snapshot"]["package_id"] == package_a
