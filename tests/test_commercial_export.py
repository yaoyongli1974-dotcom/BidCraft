"""商务标导出：XLSX 多工作表、DOCX 内容与 export_gate 硬门禁。"""

from __future__ import annotations

import pytest

from bidcraft import commercial
from bidcraft.db import session_scope
from bidcraft.export.commercial_docx import export_commercial_docx
from bidcraft.export.commercial_xlsx import export_commercial_xlsx
from bidcraft.models import BoqQuoteVersion, Issue, Project, User, utcnow

pytest.importorskip("fastapi")

PASSWORD = "commercial-export-pass"
ADMIN = ("admin", "test-admin")

EXPECTED_SHEETS = {
    "商务标信息",
    "投标函",
    "开标一览表",
    "分项报价表",
    "资格响应表",
    "业绩表",
    "人员表",
    "商务偏离表",
}


@pytest.fixture(autouse=True)
def _isolated_db(db):
    yield db


def _seed() -> dict[str, str]:
    with session_scope() as db:
        owner = User(
            username="comm-export-owner",
            display_name="王经理",
            password_hash="x",
            role="manager",
        )
        db.add(owner)
        db.flush()
        project = Project(name="商务标导出项目", buyer="某管委会", owner_id=owner.id)
        db.add(project)
        db.flush()
        version = BoqQuoteVersion(
            project_id=project.id,
            package_id=None,
            name="定稿报价V1",
            status="approved",
            prices={"清单!2": 100.0},
            summary={"total_amount": 200.0, "tax_amount": 26.0, "total_with_tax": 226.0},
            basis={
                "qty": {"清单!2": 2.0},
                "tax_rate": 0.13,
                "currency": "CNY",
                "exchange_rate": 1.0,
                "factor": 1.0,
                "round_digits": 2,
            },
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        return {
            "project_id": project.id,
            "version_id": version.id,
            "owner_id": owner.id,
        }


def _payload(seed: dict[str, str]) -> dict:
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        return commercial.build_commercial_payload(db, project)


def test_xlsx_has_all_sheets_and_quote_provenance(tmp_path):
    from openpyxl import load_workbook

    seed = _seed()
    payload = _payload(seed)
    path = export_commercial_xlsx(tmp_path / "commercial.xlsx", payload)
    workbook = load_workbook(path)
    try:
        assert EXPECTED_SHEETS <= set(workbook.sheetnames)
        info = {
            row[0].value: row[1].value
            for row in workbook["商务标信息"].iter_rows(min_row=2)
            if row[0].value
        }
        assert info["报价版本"] == "定稿报价V1"
        assert info["审批人"] == "李总"
        assert info["含税合计（元）"] == 226.0
        assert info["校验结论"] == "通过"
        letter = workbook["投标函"]
        values = {row[0].value: row[1].value for row in letter.iter_rows(min_row=2)}
        assert "226" in str(values["投标报价（含税）"])
        price = workbook["分项报价表"]
        assert price.max_row >= 3  # 表头 + 一行明细 + 合计
    finally:
        workbook.close()


def test_docx_contains_sections_and_quote_metadata(tmp_path):
    from docx import Document

    seed = _seed()
    payload = _payload(seed)
    path = export_commercial_docx(tmp_path / "commercial.docx", payload)
    document = Document(str(path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "商 务 标" in text
    assert "投标函" in text
    assert "分项报价表" in text
    assert len(document.tables) >= 7
    cover_values = [
        cell.text for table in document.tables[:1] for row in table.rows for cell in row.cells
    ]
    assert "定稿报价V1" in cover_values
    assert "李总" in cover_values


def test_export_payload_detects_withdrawn_cited_version():
    seed = _seed()
    actor = {
        "id": seed["owner_id"],
        "username": "王经理",
        "display_name": "王经理",
    }
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=actor
        )
        version = db.get(BoqQuoteVersion, seed["version_id"])
        version.status = "draft"
        db.flush()
        row = commercial.get_commercial(db, project, doc["id"])
        payload = commercial.export_payload(db, project, row)
        assert payload["validation"]["ok"] is False
        assert any("已不是已审批状态" in error for error in payload["validation"]["errors"])


# ------------------------------------------------------------------ HTTP gate


def _login(username: str, password: str):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return client


def _make_owner(admin) -> None:
    response = admin.post(
        "/admin/users",
        json={
            "username": "comm-export-http",
            "display_name": "导出用户",
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text


def _make_project(client) -> dict:
    response = client.post("/projects", json={"name": "导出项目", "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()


def _seed_http_quote(project_id: str) -> str:
    with session_scope() as db:
        version = BoqQuoteVersion(
            project_id=project_id,
            package_id=None,
            name="HTTP定稿",
            status="approved",
            prices={"清单!2": 10.0},
            summary={"total_amount": 20.0, "tax_amount": 2.6, "total_with_tax": 22.6},
            basis={"qty": {"清单!2": 2.0}, "tax_rate": 0.13, "currency": "CNY"},
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        return version.id


def test_http_export_goes_through_gate():
    admin = _login(*ADMIN)
    _make_owner(admin)
    owner = _login("comm-export-http", PASSWORD)
    project = _make_project(owner)
    project_id = project["id"]
    version_id = _seed_http_quote(project_id)
    created = owner.post(
        f"/projects/{project_id}/commercial",
        json={"name": "商务标", "quote_version_id": version_id},
    )
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]

    ok = owner.post(f"/projects/{project_id}/commercial/{doc_id}/export?format=xlsx")
    assert ok.status_code == 200, ok.text
    assert ".xlsx" in ok.headers["content-disposition"]

    with session_scope() as db:
        db.add(
            Issue(
                project_id=project_id,
                code="SUB-001",
                level="blocking",
                category="实质性响应",
                finding="未响应实质性条款",
                status="open",
            )
        )
        db.flush()

    blocked = owner.post(f"/projects/{project_id}/commercial/{doc_id}/export?format=docx")
    assert blocked.status_code == 409
    assert "未处置" in blocked.json()["detail"]

    with session_scope() as db:
        issue = db.query(Issue).filter(Issue.project_id == project_id).one()
        issue.status = "handled"
        db.flush()

    docx = owner.post(f"/projects/{project_id}/commercial/{doc_id}/export?format=docx")
    assert docx.status_code == 200, docx.text
    assert ".docx" in docx.headers["content-disposition"]
