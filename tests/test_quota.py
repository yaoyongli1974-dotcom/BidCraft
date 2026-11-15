"""企业定额库、清单定额套用与调价策略（后端车道）。"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")

from openpyxl import Workbook, load_workbook

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _client(username: str = "admin", password: str = "test-admin"):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return client


def _make_boq(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _upload(client, project_id: str, path):
    with path.open("rb") as handle:
        response = client.post(
            f"/projects/{project_id}/boq",
            files={"file": (path.name, handle, XLSX_MIME)},
        )
    assert response.status_code == 200, response.text
    return response.json()


def _project(client, name: str = "定额项目") -> str:
    created = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _make_user(admin, username: str, role: str) -> None:
    created = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": "member-pass-1",
            "role": role,
        },
    )
    assert created.status_code == 201, created.text


def _create_quota(client, **fields):
    payload = {
        "name": fields.pop("name"),
        "spec": fields.pop("spec", ""),
        "unit": fields.pop("unit", ""),
        "unit_price": fields.pop("unit_price", 0.0),
        "category": fields.pop("category", ""),
        "note": fields.pop("note", ""),
        **fields,
    }
    return client.post("/admin/quotas", json=payload)


def _audit_actions(project_id: str | None = None) -> list[dict]:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as db:
        statement = select(AuditLog).order_by(AuditLog.id)
        if project_id is not None:
            statement = statement.where(AuditLog.project_id == project_id)
        return [
            {"action": row.action, "detail": row.detail or {}}
            for row in db.scalars(statement)
        ]


def _seed_boq(client, tmp_path):
    project_id = _project(client)
    path = _make_boq(
        tmp_path / "quota-boq.xlsx",
        [
            [1, "网络面板", "双口 Cat6A", "个", "10", ""],
            [2, "配线架", "24 口", "个", "2", ""],
            [3, "未知材料", "ZZ-9", "个", "5", ""],
        ],
    )
    _upload(client, project_id, path)
    return project_id


# ------------------------------------------------------------------ quota CRUD


def test_quota_admin_crud_search_and_match(db):
    client = _client()
    panel = _create_quota(
        client,
        name="网络面板",
        spec="双口 Cat6A",
        unit="个",
        unit_price=12.5,
        category="弱电",
    )
    assert panel.status_code == 201, panel.text
    body = panel.json()
    assert body["name"] == "网络面板"
    assert body["unit_price"] == 12.5
    assert body["category"] == "弱电"
    quota_id = body["id"]

    rack = _create_quota(
        client,
        name="配线架",
        spec="24 口",
        unit="个",
        unit_price=100,
        category="综合布线",
    )
    assert rack.status_code == 201
    rack_id = rack.json()["id"]

    # validation: name required / bounds, price finite and non-negative
    assert _create_quota(client, name="", unit_price=1).status_code == 400
    assert _create_quota(client, name="x" * 121, unit_price=1).status_code == 400
    assert _create_quota(client, name="负价", unit_price=-1).status_code == 400
    assert (
        client.post(
            "/admin/quotas", json={"name": "非数", "unit_price": "abc"}
        ).status_code
        == 422
    )
    listed = client.get("/admin/quotas").json()
    assert {item["id"] for item in listed} == {quota_id, rack_id}

    by_name = client.get("/admin/quotas", params={"search": "网络"}).json()
    assert [item["id"] for item in by_name] == [quota_id]
    by_category = client.get("/admin/quotas", params={"search": "综合布线"}).json()
    assert [item["id"] for item in by_category] == [rack_id]
    by_spec = client.get("/admin/quotas", params={"search": "Cat6A"}).json()
    assert [item["id"] for item in by_spec] == [quota_id]
    assert client.get("/admin/quotas", params={"limit": 1}).json()[0]["id"] in {
        quota_id,
        rack_id,
    }

    matched = client.get(
        "/admin/quotas/match", params={"name": "网络面板", "spec": "双口 Cat6A"}
    ).json()
    assert matched[0]["id"] == quota_id
    assert matched[0]["score"] == 1.0
    assert matched[0]["unit_price"] == 12.5

    # below the 0.75 threshold -> no candidate
    assert (
        client.get("/admin/quotas/match", params={"name": "完全无关材料"}).json() == []
    )

    patched = client.patch(f"/admin/quotas/{quota_id}", json={"unit_price": 15})
    assert patched.status_code == 200
    assert patched.json()["unit_price"] == 15
    assert client.patch(f"/admin/quotas/{quota_id}", json={"name": ""}).status_code == 400
    assert (
        client.patch(f"/admin/quotas/{quota_id}", json={"unit_price": -2}).status_code
        == 400
    )
    assert client.patch("/admin/quotas/missing-id", json={"unit": "米"}).status_code == 404

    assert client.delete(f"/admin/quotas/{rack_id}").status_code == 200
    assert client.delete(f"/admin/quotas/{rack_id}").status_code == 404
    remaining = client.get("/admin/quotas").json()
    assert [item["id"] for item in remaining] == [quota_id]

    actions = [row["action"] for row in _audit_actions()]
    assert "quota.create" in actions
    assert "quota.update" in actions
    assert "quota.delete" in actions


# ------------------------------------------------------------------ quota fill


def test_quota_fill_dry_run_then_apply(db, tmp_path):
    client = _client()
    project_id = _seed_boq(client, tmp_path)
    _create_quota(
        client, name="网络面板", spec="双口 Cat6A", unit="个", unit_price=12.5
    )
    _create_quota(client, name="配线架", spec="24 口", unit="个", unit_price=100)

    preview = client.post(f"/projects/{project_id}/boq/quota-fill", json={})
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["applied"] == 0
    assert [item["boq_row"] for item in preview_body["proposals"]] == [2, 3]
    assert {item["quota_name"] for item in preview_body["proposals"]} == {
        "网络面板",
        "配线架",
    }
    assert preview_body["proposals"][0]["unit_price"] == 12.5

    # a dry run never writes prices
    after_preview = client.get(f"/projects/{project_id}/boq").json()
    assert after_preview["summary"]["priced"] == 0
    assert all(row["price"] is None for row in after_preview["rows"])

    applied = client.post(
        f"/projects/{project_id}/boq/quota-fill", json={"dry_run": False}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 2
    filled = client.get(f"/projects/{project_id}/boq").json()
    assert filled["summary"]["priced"] == 2
    priced = {
        row["boq_row"]: row["price"]["unit_price"]
        for row in filled["rows"]
        if row["price"]
    }
    assert priced[2] == 12.5
    assert priced[3] == 100.0
    assert 4 not in priced

    # only_missing=True leaves the priced rows untouched
    again = client.post(
        f"/projects/{project_id}/boq/quota-fill", json={"dry_run": False}
    )
    assert again.json()["applied"] == 0

    # manual price wins until only_missing=False is requested
    override = client.post(
        f"/projects/{project_id}/boq/price", json={"boq_row": 2, "unit_price": 1.0}
    )
    assert override.status_code == 200
    assert (
        client.post(
            f"/projects/{project_id}/boq/quota-fill", json={"dry_run": False}
        ).json()["applied"]
        == 0
    )
    overwritten = client.post(
        f"/projects/{project_id}/boq/quota-fill",
        json={"dry_run": False, "only_missing": False},
    )
    assert overwritten.json()["applied"] == 2
    prices = {
        row["boq_row"]: row["price"]["unit_price"]
        for row in client.get(f"/projects/{project_id}/boq").json()["rows"]
        if row["price"]
    }
    assert prices[2] == 12.5
    assert prices[3] == 100.0

    fills = [
        row["detail"]
        for row in _audit_actions(project_id)
        if row["action"] == "boq.quota_fill"
    ]
    assert fills[-1] == {"applied": 2, "only_missing": False}


def test_quota_fill_requires_snapshot(db):
    client = _client()
    project_id = _project(client, "无清单项目")
    _create_quota(client, name="网络面板", unit_price=1)
    response = client.post(f"/projects/{project_id}/boq/quota-fill", json={})
    assert response.status_code == 409
    assert "清单" in response.json()["detail"]
    assert (
        client.post(
            f"/projects/{project_id}/boq/quota-fill",
            json={"dry_run": False},
        ).status_code
        == 409
    )


# --------------------------------------------------------------- price policy


def test_price_policy_clamp_and_adjusted_total(db, tmp_path):
    client = _client()
    project_id = _seed_boq(client, tmp_path)
    policy = client.get(f"/projects/{project_id}/price-policy").json()
    assert policy == {"enabled": False, "factor": 1.0, "round_digits": 2}

    client.post(
        f"/projects/{project_id}/boq/price", json={"boq_row": 2, "unit_price": 10}
    )
    client.post(
        f"/projects/{project_id}/boq/price", json={"boq_row": 3, "unit_price": 20}
    )
    summary = client.get(f"/projects/{project_id}/boq").json()["summary"]
    assert summary["total_amount"] == 140.0
    assert summary["adjusted_total"] is None

    clamped = client.put(
        f"/projects/{project_id}/price-policy",
        json={"enabled": True, "factor": 99, "round_digits": 9},
    )
    assert clamped.status_code == 200, clamped.text
    assert clamped.json() == {"enabled": True, "factor": 10.0, "round_digits": 4}
    low = client.put(
        f"/projects/{project_id}/price-policy",
        json={"factor": 0.001, "round_digits": -3},
    ).json()
    assert low == {"enabled": True, "factor": 0.1, "round_digits": 0}

    client.put(
        f"/projects/{project_id}/price-policy",
        json={"factor": 1.5, "round_digits": 2},
    )
    summary = client.get(f"/projects/{project_id}/boq").json()["summary"]
    # rows keep their raw unit prices; only the summary carries the adjusted total
    assert summary["total_amount"] == 140.0
    assert summary["adjusted_total"] == 210.0
    rows = client.get(f"/projects/{project_id}/boq").json()["rows"]
    panel_row = next(row for row in rows if row["boq_row"] == 2)
    assert panel_row["price"]["unit_price"] == 10.0

    actions = [row["action"] for row in _audit_actions(project_id)]
    assert "price.policy" in actions


def test_adjust_multiplies_prices_and_rounds(db, tmp_path):
    client = _client()
    project_id = _seed_boq(client, tmp_path)
    client.post(
        f"/projects/{project_id}/boq/price", json={"boq_row": 2, "unit_price": 12.5}
    )
    client.post(
        f"/projects/{project_id}/boq/price", json={"boq_row": 3, "unit_price": 100}
    )
    client.put(
        f"/projects/{project_id}/price-policy",
        json={"enabled": True, "factor": 1.333, "round_digits": 1},
    )

    adjusted = client.post(f"/projects/{project_id}/boq/adjust", json={})
    assert adjusted.status_code == 200, adjusted.text
    payload = adjusted.json()
    prices = {
        row["boq_row"]: row["price"]["unit_price"]
        for row in payload["rows"]
        if row["price"]
    }
    assert prices[2] == 16.7
    assert prices[3] == 133.3
    assert payload["summary"]["total_amount"] == round(10 * 16.7 + 2 * 133.3, 2)
    # adjusted total uses the policy on top of the new (already adjusted) total
    assert payload["summary"]["adjusted_total"] == round(
        payload["summary"]["total_amount"] * 1.333, 1
    )

    detail = next(
        row["detail"] for row in _audit_actions(project_id) if row["action"] == "boq.adjust"
    )
    assert detail == {"factor": 1.333, "rows": 2}


def test_adjust_without_prices_is_conflict(db, tmp_path):
    client = _client()
    project_id = _seed_boq(client, tmp_path)
    response = client.post(f"/projects/{project_id}/boq/adjust", json={})
    assert response.status_code == 409

    empty_project = _project(client, "未上传清单")
    assert (
        client.post(f"/projects/{empty_project}/boq/adjust", json={}).status_code == 409
    )


# ------------------------------------------------------------------- exporting


def test_export_adds_policy_rows_only_when_enabled(db, tmp_path):
    client = _client()
    project_id = _seed_boq(client, tmp_path)
    client.post(
        f"/projects/{project_id}/boq/price", json={"boq_row": 2, "unit_price": 10}
    )

    plain = client.post(f"/projects/{project_id}/boq/export")
    assert plain.status_code == 200, plain.text
    quote = load_workbook(io.BytesIO(plain.content))["报价汇总"]
    labels = [row[0].value for row in quote.iter_rows(min_row=2)]
    assert "调价系数" not in labels
    assert "调整后合计（元）" not in labels

    client.put(
        f"/projects/{project_id}/price-policy",
        json={"enabled": True, "factor": 1.2, "round_digits": 2},
    )
    exported = client.post(f"/projects/{project_id}/boq/export")
    assert exported.status_code == 200
    quote = load_workbook(io.BytesIO(exported.content))["报价汇总"]
    mapping = {row[0].value: row[1].value for row in quote.iter_rows(min_row=2)}
    assert mapping["调价系数"] == 1.2
    assert mapping["调整后合计（元）"] == 120.0


# ------------------------------------------------------------------------ RBAC


def test_viewer_and_manager_cannot_touch_quotas(db, tmp_path):
    admin = _client()
    _make_user(admin, "quota-viewer", "viewer")
    _make_user(admin, "quota-manager", "manager")

    viewer = _client("quota-viewer", "member-pass-1")
    manager = _client("quota-manager", "member-pass-1")

    for client in (viewer, manager):
        assert client.get("/admin/quotas").status_code == 403
        assert (
            client.post(
                "/admin/quotas", json={"name": "不应创建", "unit_price": 1}
            ).status_code
            == 403
        )
        assert (
            client.get("/admin/quotas/match", params={"name": "网络面板"}).status_code
            == 403
        )

    project_id = _seed_boq(admin, tmp_path)
    _create_quota(admin, name="网络面板", spec="双口 Cat6A", unit_price=12.5)
    assert (
        viewer.put(
            f"/projects/{project_id}/price-policy", json={"enabled": True}
        ).status_code
        == 403
    )
    assert (
        viewer.post(f"/projects/{project_id}/boq/quota-fill", json={}).status_code
        == 403
    )
    assert viewer.post(f"/projects/{project_id}/boq/adjust", json={}).status_code == 403
