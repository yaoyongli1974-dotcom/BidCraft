from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook, load_workbook  # noqa: E402

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


def _project(client, name: str = "税币项目") -> str:
    created = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _seed(client, tmp_path) -> str:
    project_id = _project(client)
    path = tmp_path / "tax.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    sheet.append([1, "网络面板", "双口 Cat6A", "个", "2", ""])
    sheet.append([2, "线缆", "Cat6A", "米", "10", ""])
    workbook.save(path)
    with path.open("rb") as handle:
        uploaded = client.post(
            f"/projects/{project_id}/boq",
            files={"file": (path.name, handle, XLSX_MIME)},
        )
    assert uploaded.status_code == 200, uploaded.text
    for row, price in ((2, 12.5), (3, 3.0)):
        response = client.post(
            f"/projects/{project_id}/boq/price",
            json={"boq_row": row, "unit_price": price},
        )
        assert response.status_code == 200, response.text
    return project_id


def test_policy_defaults_and_clamping(db):
    client = _client()
    project_id = _project(client)

    policy = client.get(f"/projects/{project_id}/price-policy")
    assert policy.status_code == 200
    assert policy.json() == {
        "enabled": False,
        "factor": 1.0,
        "round_digits": 2,
        "tax_rate": 0.0,
        "currency": "CNY",
    }

    high = client.put(
        f"/projects/{project_id}/price-policy",
        json={"tax_rate": 0.75, "currency": "usd"},
    )
    assert high.status_code == 200, high.text
    assert high.json()["tax_rate"] == 0.5
    assert high.json()["currency"] == "USD"

    low = client.put(
        f"/projects/{project_id}/price-policy",
        json={"tax_rate": -0.4, "currency": "jpy"},
    )
    assert low.status_code == 200, low.text
    assert low.json()["tax_rate"] == 0.0
    assert low.json()["currency"] == "CNY"

    kept = client.put(
        f"/projects/{project_id}/price-policy", json={"tax_rate": 0.13}
    )
    assert kept.json()["tax_rate"] == 0.13
    assert kept.json()["currency"] == "CNY"


def test_policy_direct_clamp_and_invalid_fallback(db):
    from bidcraft import boq
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    client = _client()
    project_id = _project(client, "脏数据项目")
    with session_scope() as session:
        project = session.get(Project, project_id)
        project.meta = {
            "price_policy": {"tax_rate": 9, "currency": "gbp", "factor": "x"}
        }
    with session_scope() as session:
        policy = boq.get_price_policy(session.get(Project, project_id))
        assert policy["tax_rate"] == 0.5
        assert policy["currency"] == "CNY"
        assert policy["factor"] == 1.0

        project = session.get(Project, project_id)
        updated = boq.set_price_policy(
            session, project, {"tax_rate": "abc", "currency": "eur"}
        )
        assert updated["tax_rate"] == 0.0
        assert updated["currency"] == "EUR"
        invalid = boq.set_price_policy(
            session, project, {"tax_rate": float("inf"), "currency": "gbp"}
        )
        assert invalid["tax_rate"] == 0.0
        assert invalid["currency"] == "CNY"


def test_summary_tax_fields(db, tmp_path):
    client = _client()
    project_id = _seed(client, tmp_path)

    plain = client.get(f"/projects/{project_id}/boq").json()["summary"]
    assert plain["total_amount"] == 55.0
    assert plain["tax_amount"] == 0.0
    assert plain["total_with_tax"] == 55.0

    client.put(f"/projects/{project_id}/price-policy", json={"tax_rate": 0.13})
    taxed = client.get(f"/projects/{project_id}/boq").json()["summary"]
    assert taxed["total_amount"] == 55.0
    assert taxed["tax_amount"] == 7.15
    assert taxed["total_with_tax"] == 62.15

    # a project without any BOQ keeps a zero tax amount rather than None
    empty_project = _project(client, "空税项目")
    client.put(f"/projects/{empty_project}/price-policy", json={"tax_rate": 0.13})
    empty = client.get(f"/projects/{empty_project}/boq").json()["summary"]
    assert empty["total_amount"] == 0.0
    assert empty["tax_amount"] == 0.0
    assert empty["total_with_tax"] == 0.0


def test_apply_tax_without_total_yields_none():
    from bidcraft.boq import _apply_tax

    summary = _apply_tax({"total_amount": None}, {"tax_rate": 0.1})
    assert summary["tax_amount"] is None
    assert summary["total_with_tax"] is None


def test_export_quote_sheet_has_tax_rows(db, tmp_path):
    client = _client()
    project_id = _seed(client, tmp_path)
    client.put(f"/projects/{project_id}/price-policy", json={"tax_rate": 0.13})

    exported = client.post(f"/projects/{project_id}/boq/export")
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(io.BytesIO(exported.content))
    quote = workbook["报价汇总"]
    labels = [row[0].value for row in quote.iter_rows(min_row=2)]
    assert labels[-3:] == ["税率", "税额（元）", "含税合计（元）"]
    rows = {row[0].value: row[1] for row in quote.iter_rows(min_row=2)}
    assert rows["税率"].value == 0.13
    assert rows["税额（元）"].value == 7.15
    assert rows["含税合计（元）"].value == 62.15
    for label in ("税率", "税额（元）", "含税合计（元）"):
        assert rows[label].number_format == "#,##0.00"
