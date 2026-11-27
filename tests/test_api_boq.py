from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook, load_workbook

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


def _make_boq(path, rows) -> object:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _upload(client, project_id: str, path, sheet: str | None = None):
    data = {"sheet": sheet} if sheet is not None else None
    with path.open("rb") as handle:
        return client.post(
            f"/projects/{project_id}/boq",
            files={"file": (path.name, handle, XLSX_MIME)},
            data=data,
        )


def _row(payload, boq_row):
    return next(row for row in payload["rows"] if row["boq_row"] == boq_row)


def test_boq_api_full_flow(db, tmp_path):
    client = _client()
    created = client.post(
        "/projects", json={"name": "清单对齐项目", "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    empty = client.get(f"/projects/{project_id}/boq")
    assert empty.status_code == 200
    body = empty.json()
    assert body["snapshot"] is None
    assert body["rows"] == []
    assert body["summary"]["boq_rows"] == 0
    assert body["summary"]["match_rate"] == 0.0
    assert body["ledger_summary"]["materials"] == 0

    boq_path = _make_boq(
        tmp_path / "tender-boq.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "480", ""],
            [2, "配线架", "24 口", "个", "20", ""],
        ],
    )
    uploaded = _upload(client, project_id, boq_path)
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    assert payload["snapshot"]["filename"] == "tender-boq.xlsx"
    assert payload["snapshot"]["sheet"] == "工程量清单"
    assert payload["snapshot"]["item_count"] == 2
    assert payload["snapshot"]["warnings"] == []
    assert payload["summary"]["boq_rows"] == 2
    assert payload["summary"]["missing_in_ledger"] == 2
    assert payload["summary"]["matched"] == 0
    row = _row(payload, 2)
    assert row["key"] == "工程量清单!2"
    assert row["status"] == "missing_in_ledger"
    assert row["ledger"] is None

    sources = client.get(f"/projects/{project_id}/sources").json()
    # the BOQ workbook is managed by the 清单对齐 tab, never listed as a tender source
    assert all(item["role"] != "boq" for item in sources)

    from pathlib import Path

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import SourceFile

    def _boq_sources():
        with session_scope() as session:
            return list(
                session.scalars(
                    select(SourceFile).where(
                        SourceFile.project_id == project_id,
                        SourceFile.role == "boq",
                    )
                )
            )

    stored = _boq_sources()
    assert len(stored) == 1
    assert stored[0].filename == "tender-boq.xlsx"
    first_source_id = stored[0].id
    first_path = Path(stored[0].stored_path)
    assert first_path.is_file()

    # update without a matched material is a conflict
    conflict = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "update"}
    )
    assert conflict.status_code == 409

    # unknown row / unknown action
    assert (
        client.post(
            f"/projects/{project_id}/boq/apply", json={"boq_row": 999, "action": "update"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "explode"}
        ).status_code
        == 400
    )

    added = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "add"}
    )
    assert added.status_code == 200, added.text
    payload = added.json()
    added_row = _row(payload, 2)
    assert added_row["status"] == "matched"
    assert added_row["ledger"]["material_id"]
    assert payload["summary"]["matched"] == 1
    assert payload["summary"]["missing_in_ledger"] == 1
    assert payload["ledger_summary"]["materials"] == 1

    # re-upload replaces snapshot + source file, keeping the ledger
    replacement = _make_boq(
        tmp_path / "tender-boq-v2.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "500", ""],
            [2, "配线架", "24 口", "个", "20", ""],
        ],
    )
    replaced = _upload(client, project_id, replacement)
    assert replaced.status_code == 200, replaced.text
    payload = replaced.json()
    assert payload["snapshot"]["id"] != uploaded.json()["snapshot"]["id"]
    assert payload["summary"]["qty_diff"] == 1
    assert payload["summary"]["missing_in_ledger"] == 1
    assert _row(payload, 2)["diff"]["qty"] == {"boq": 500.0, "ledger": 480.0, "delta": 20.0}

    sources = client.get(f"/projects/{project_id}/sources").json()
    assert all(item["role"] != "boq" for item in sources)
    assert (
        client.get(f"/projects/{project_id}/sources/{first_source_id}/file").status_code
        == 404
    )
    # the replaced workbook is unlinked from the project's sources directory
    assert not first_path.exists()
    stored = _boq_sources()
    assert len(stored) == 1
    assert stored[0].id != first_source_id
    second_path = Path(stored[0].stored_path)
    assert second_path.is_file()

    # apply the BOQ quantity to the ledger -> zero difference
    updated = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "update"}
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    updated_row = _row(payload, 2)
    assert updated_row["status"] == "matched"
    assert updated_row["diff"] == {"qty": None, "unit": None, "spec": None}
    ledger = client.get(f"/projects/{project_id}/ledger").json()
    material = next(item for item in ledger["materials"] if item["name"] == "网络面板")
    assert material["qty"] == 500.0 and material["spec"] == "双口 Cat6A"

    # ignore / restore keeps the computed status but flags the row
    ignored = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 3, "action": "ignore"}
    )
    assert ignored.status_code == 200
    payload = ignored.json()
    assert _row(payload, 3)["ignored"] is True
    assert _row(payload, 3)["status"] == "missing_in_ledger"
    assert payload["summary"]["ignored"] == 1

    restored = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 3, "action": "restore"}
    )
    assert restored.status_code == 200
    payload = restored.json()
    assert _row(payload, 3)["ignored"] is False
    assert payload["summary"]["ignored"] == 0

    # add the remaining BOQ item, then export the response workbook
    assert (
        client.post(
            f"/projects/{project_id}/boq/apply", json={"boq_row": 3, "action": "add"}
        ).status_code
        == 200
    )
    exported = client.post(f"/projects/{project_id}/boq/export")
    assert exported.status_code == 200, exported.text
    assert exported.content[:2] == b"PK"
    assert "attachment" in exported.headers["content-disposition"]
    assert "utf-8" in exported.headers["content-disposition"]
    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames == ["清单对齐", "报价汇总", "差异汇总"]
    assert workbook["清单对齐"]["C2"].value in {"网络面板", "配线架"}

    # deleting the snapshot never touches the ledger
    before = client.get(f"/projects/{project_id}/ledger").json()
    deleted = client.delete(f"/projects/{project_id}/boq")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    after = client.get(f"/projects/{project_id}/boq").json()
    assert after["snapshot"] is None
    assert after["rows"] == []
    assert after["ledger_summary"]["materials"] == len(before["materials"]) == 2
    assert not second_path.exists()
    remaining = client.get(f"/projects/{project_id}/sources").json()
    assert all(item["role"] != "boq" for item in remaining)


def test_boq_price_api_and_export(db, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "清单报价项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    boq_path = _make_boq(
        tmp_path / "priced-boq.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "10", ""],
            [2, "配线架", "24 口", "个", "20", ""],
        ],
    )
    uploaded = _upload(client, project_id, boq_path)
    assert uploaded.status_code == 200, uploaded.text
    assert _row(uploaded.json(), 2)["price"] is None

    priced = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 2, "unit_price": 12.5},
    )
    assert priced.status_code == 200, priced.text
    payload = priced.json()
    assert _row(payload, 2)["price"] == {"unit_price": 12.5, "amount": 125.0}
    assert payload["summary"]["priced"] == 1
    assert payload["summary"]["unpriced"] == 1
    assert payload["summary"]["total_amount"] == 125.0

    second = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 3, "unit_price": 7},
    )
    assert second.status_code == 200
    payload = second.json()
    assert payload["summary"]["priced"] == 2
    assert payload["summary"]["unpriced"] == 0
    assert payload["summary"]["total_amount"] == 265.0

    assert (
        client.post(
            f"/projects/{project_id}/boq/price",
            json={"boq_row": 2, "unit_price": -1},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/projects/{project_id}/boq/price",
            json={"boq_row": 999, "unit_price": 1},
        ).status_code
        == 404
    )

    exported = client.post(f"/projects/{project_id}/boq/export")
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames == ["清单对齐", "报价汇总", "差异汇总"]
    assert workbook["清单对齐"]["G2"].value == 12.5
    assert workbook["清单对齐"]["H2"].value == 125.0
    quote = {
        row[0].value: row[1].value
        for row in workbook["报价汇总"].iter_rows(min_row=2)
    }
    assert quote["已报价"] == 2
    assert quote["未报价"] == 0
    assert quote["报价合计（元）"] == 265.0

    cleared = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 2, "unit_price": None},
    )
    assert cleared.status_code == 200
    payload = cleared.json()
    assert _row(payload, 2)["price"] is None
    assert payload["summary"]["priced"] == 1
    assert payload["summary"]["unpriced"] == 1
    assert payload["summary"]["total_amount"] == 140.0

    # replacing the workbook starts a fresh quote
    replaced = _upload(client, project_id, boq_path)
    assert replaced.status_code == 200
    assert _row(replaced.json(), 3)["price"] is None
    assert replaced.json()["summary"]["priced"] == 0


def test_boq_upload_extracts_prices(db, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "上传识别报价", "package": "weakcurrent-cabling"}
    ).json()["id"]
    path = tmp_path / "priced-upload.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "单价（元）", "合价（元）"])
    sheet.append([1, "网络面板", "双口 Cat6A", "个", "10", "12.5", "125"])
    sheet.append([2, "配线架", "24 口", "个", "20", "", "200"])
    workbook.save(path)

    uploaded = _upload(client, project_id, path)
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    summary = payload["summary"]
    assert summary["priced"] == 2
    assert summary["unpriced"] == 0
    assert summary["total_amount"] == 325.0
    assert _row(payload, 2)["price"] == {"unit_price": 12.5, "amount": 125.0}
    assert _row(payload, 3)["price"] == {"unit_price": 10.0, "amount": 200.0}

    # prices round-trip through GET without recomputation drift
    fetched = client.get(f"/projects/{project_id}/boq").json()
    assert fetched["summary"]["priced"] == 2
    assert fetched["summary"]["unpriced"] == 0
    assert fetched["summary"]["total_amount"] == 325.0
    assert _row(fetched, 2)["price"] == {"unit_price": 12.5, "amount": 125.0}

    # extracted prices stay user-overridable through the existing price endpoint
    overridden = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 3, "unit_price": 8},
    )
    assert overridden.status_code == 200, overridden.text
    payload = overridden.json()
    assert _row(payload, 3)["price"] == {"unit_price": 8.0, "amount": 160.0}
    assert payload["summary"]["priced"] == 2
    assert payload["summary"]["unpriced"] == 0
    assert payload["summary"]["total_amount"] == 285.0


def test_boq_price_without_quantity_counts_as_missing(db, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "缺数量报价", "package": "weakcurrent-cabling"}
    ).json()["id"]
    boq_path = _make_boq(
        tmp_path / "missing-qty.xlsx",
        [[1, "待定设备", "X", "台", "", ""]],
    )
    assert _upload(client, project_id, boq_path).status_code == 200

    priced = client.post(
        f"/projects/{project_id}/boq/price",
        json={"boq_row": 2, "unit_price": 9.5},
    )
    assert priced.status_code == 200, priced.text
    payload = priced.json()
    row = _row(payload, 2)
    assert row["qty"] is None
    assert row["price"] == {"unit_price": 9.5, "amount": None}
    assert payload["summary"]["priced"] == 1
    assert payload["summary"]["unpriced"] == 0
    assert payload["summary"]["quantity_missing"] == 1
    assert payload["summary"]["total_amount"] == 0.0


def test_boq_add_rejects_already_matched_row(db, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "重复加入项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    boq_path = _make_boq(
        tmp_path / "boq.xlsx", [[1, "网络面板", "双口 Cat6A", "个", "480", ""]]
    )
    uploaded = _upload(client, project_id, boq_path)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["summary"]["missing_in_ledger"] == 1

    added = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "add"}
    )
    assert added.status_code == 200, added.text
    assert _row(added.json(), 2)["status"] == "matched"
    material_id = _row(added.json(), 2)["ledger"]["material_id"]

    duplicate = client.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "add"}
    )
    assert duplicate.status_code == 409
    assert "已匹配" in duplicate.json()["detail"]
    assert "更新台账" in duplicate.json()["detail"]

    ledger = client.get(f"/projects/{project_id}/ledger").json()
    assert len(ledger["materials"]) == 1
    assert ledger["materials"][0]["qty"] == 480.0
    unchanged = client.get(f"/projects/{project_id}/boq").json()
    assert unchanged["ledger_summary"]["materials"] == 1
    assert _row(unchanged, 2)["ledger"]["material_id"] == material_id


def test_boq_reparse_ignores_snapshot(db, tender_path, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "重解析项目", "package": "weakcurrent-cabling"}
    ).json()["id"]

    with tender_path.open("rb") as handle:
        parsed = client.post(
            f"/projects/{project_id}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert parsed.status_code == 200

    boq_path = _make_boq(
        tmp_path / "boq.xlsx",
        [[1, "清单独有物料XYZ", "SPEC-XYZ", "台", "3", ""]],
    )
    uploaded = _upload(client, project_id, boq_path)
    assert uploaded.status_code == 200, uploaded.text
    snapshot_id = uploaded.json()["snapshot"]["id"]

    reparsed = client.post(f"/projects/{project_id}/parse")
    assert reparsed.status_code == 200, reparsed.text

    payload = client.get(f"/projects/{project_id}/boq").json()
    assert payload["snapshot"]["id"] == snapshot_id
    assert payload["snapshot"]["item_count"] == 1
    assert payload["rows"][0]["name"] == "清单独有物料XYZ"

    ledger = client.get(f"/projects/{project_id}/ledger").json()
    assert all(item["name"] != "清单独有物料XYZ" for item in ledger["materials"])

    roles = {item["role"] for item in client.get(f"/projects/{project_id}/sources").json()}
    assert roles == {"tender"}
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import SourceFile

    with session_scope() as session:
        stored_roles = set(
            session.scalars(
                select(SourceFile.role).where(SourceFile.project_id == project_id)
            )
        )
    assert stored_roles == {"tender", "boq"}


def test_boq_upload_errors_are_user_actionable(db, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "错误处理项目", "package": "weakcurrent-cabling"}
    ).json()["id"]

    broken = client.post(
        f"/projects/{project_id}/boq",
        files={"file": ("bad.xlsx", b"not a workbook", XLSX_MIME)},
    )
    assert broken.status_code == 400
    assert "无法读取" in broken.json()["detail"]

    csv_path = tmp_path / "boq.csv"
    csv_path.write_text("名称,数量\n面板,1\n", encoding="utf-8")
    wrong_type = _upload(client, project_id, csv_path)
    assert wrong_type.status_code == 400
    assert ".xlsx" in wrong_type.json()["detail"]

    no_header = tmp_path / "plain.xlsx"
    workbook = Workbook()
    workbook.active.append(["一些说明"])
    workbook.save(no_header)
    missing_header = _upload(client, project_id, no_header)
    assert missing_header.status_code == 400
    assert "工程量清单" in missing_header.json()["detail"]

    valid = _make_boq(tmp_path / "ok.xlsx", [[1, "面板", "双口", "个", "2", ""]])
    wrong_sheet = _upload(client, project_id, valid, sheet="不存在的表")
    assert wrong_sheet.status_code == 400
    assert "不存在的表" in wrong_sheet.json()["detail"]
    assert "工程量清单" in wrong_sheet.json()["detail"]

    # failed uploads never create a snapshot
    assert client.get(f"/projects/{project_id}/boq").json()["snapshot"] is None


def test_boq_viewer_cannot_write(db, tmp_path):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project_id = admin.post(
        "/projects", json={"name": "只读清单项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    boq_path = _make_boq(tmp_path / "boq.xlsx", [[1, "面板", "双口", "个", "2", ""]])
    assert _upload(admin, project_id, boq_path).status_code == 200

    created = admin.post(
        "/admin/users",
        json={
            "username": "boq-viewer",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201

    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "boq-viewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200
    blocked = viewer.post(
        f"/projects/{project_id}/boq/apply", json={"boq_row": 2, "action": "add"}
    )
    assert blocked.status_code == 403
    assert (
        viewer.post(
            f"/projects/{project_id}/boq/price",
            json={"boq_row": 2, "unit_price": 1},
        ).status_code
        == 403
    )
    with boq_path.open("rb") as handle:
        assert (
            viewer.post(
                f"/projects/{project_id}/boq",
                files={"file": ("boq.xlsx", handle, XLSX_MIME)},
            ).status_code
            == 403
        )


def test_boq_export_without_snapshot_conflicts(db):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "空清单项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    exported = client.post(f"/projects/{project_id}/boq/export")
    assert exported.status_code == 409
    assert "尚未上传" in exported.json()["detail"]


def test_boq_source_hidden_from_tender_sources(db, tender_path, tmp_path):
    client = _client()
    project_id = client.post(
        "/projects", json={"name": "清单来源隔离项目", "package": "weakcurrent-cabling"}
    ).json()["id"]

    with tender_path.open("rb") as handle:
        assert (
            client.post(
                f"/projects/{project_id}/sources",
                files={"file": ("tender.txt", handle, "text/plain")},
            ).status_code
            == 200
        )
    before = client.get(f"/projects/{project_id}").json()
    assert before["stats"]["sources"] == 1

    boq_path = _make_boq(tmp_path / "boq.xlsx", [[1, "面板", "双口", "个", "2", ""]])
    assert _upload(client, project_id, boq_path).status_code == 200

    sources = client.get(f"/projects/{project_id}/sources").json()
    assert [item["role"] for item in sources] == ["tender"]
    assert all(item["filename"] != "boq.xlsx" for item in sources)

    detail = client.get(f"/projects/{project_id}").json()
    assert [item["role"] for item in detail["sources"]] == ["tender"]
    # the BOQ workbook must not inflate the tender source counter
    assert detail["stats"]["sources"] == before["stats"]["sources"] == 1


def test_project_delete_removes_boq_snapshot(db, tmp_path):
    from sqlalchemy import func, select

    from bidcraft.db import session_scope
    from bidcraft.models import BoqSnapshot

    client = _client()
    project_id = client.post(
        "/projects", json={"name": "删除清理项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    boq_path = _make_boq(tmp_path / "boq.xlsx", [[1, "面板", "双口", "个", "2", ""]])
    assert _upload(client, project_id, boq_path).status_code == 200

    with session_scope() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BoqSnapshot)
                .where(BoqSnapshot.project_id == project_id)
            )
            == 1
        )

    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    with session_scope() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BoqSnapshot)
                .where(BoqSnapshot.project_id == project_id)
            )
            == 0
        )
    assert client.get(f"/projects/{project_id}").status_code == 404


def test_boq_unknown_project(db):
    client = _client()
    assert client.get("/projects/nope/boq").status_code == 404
