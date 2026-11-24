"""Batch 4: HTTP/MCP/CLI 导出通道接线与交付前完整性校验。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from docx import Document  # noqa: E402

from bidcraft.db import session_scope  # noqa: E402
from bidcraft.export import templates  # noqa: E402
from bidcraft.models import (  # noqa: E402
    BoqQuoteVersion,
    OutlineNode,
    Project,
    Section,
    utcnow,
)

ADMIN = ("admin", "test-admin")
PASSWORD = "batch4-channel-pass"
BODY = (
    "## 背景\n\n详见 [招标公告](https://example.com/notice)。\n\n"
    "| 名称 | 数量 |\n| --- | --- |\n| 面板 | 480 |\n\n### 中间小节\n\n过渡。\n\n#### 深层小节\n\n正文。"
)


@pytest.fixture(autouse=True)
def _isolated_db(db):
    yield db


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _project_with_body(name: str) -> str:
    from bidcraft.pipeline import create_project

    with session_scope() as db:
        project = create_project(db, name)
        node = OutlineNode(project_id=project.id, heading="第一章 技术方案", position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body=BODY,
                status="written",
                words=len(BODY),
                review_status="confirmed",
            )
        )
        return project.id


def _base_template(path: Path) -> Path:
    document = Document()
    document.sections[0].header.paragraphs[0].text = "模板继承页眉"
    document.save(str(path))
    return path


def _assert_no_cover_and_compact_font(path: Path) -> None:
    document = Document(str(path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "投 标 文 件" not in text
    assert document.styles["Normal"].font.size.pt == 10.5


def test_api_export_uses_professional_engine_with_base_template(tmp_path, monkeypatch):
    client = _login(*ADMIN)
    routes_projects = sys.modules["bidcraft.api.routes.projects"]
    monkeypatch.setattr(routes_projects, "enforce_export_gate", lambda *a, **kw: None)
    project_id = _project_with_body("API 专业导出")
    base = _base_template(tmp_path / "api-base.docx")
    with session_scope() as db:
        project = db.get(Project, project_id)
        templates.save(project, "standard")
        project.meta = {
            **(project.meta or {}),
            "export_professional": True,
            "export_base_template": str(base),
        }
        # 两章 → IR 两节 → 渲染结果两节。
        node = OutlineNode(project_id=project_id, heading="第二章 实施方案", position=2)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body="实施方案正文。",
                status="written",
                words=8,
                review_status="confirmed",
            )
        )

    response = client.post(f"/projects/{project_id}/export", json={"format": "docx"})
    assert response.status_code == 200, response.text
    downloaded = tmp_path / "api.docx"
    downloaded.write_bytes(response.content)
    document = Document(str(downloaded))
    assert len(document.sections) == 2
    assert document.sections[0].header.paragraphs[0].text == "模板继承页眉"
    assert "NUMPAGES" in document.sections[0].footer._element.xml
    assert "w:tblHeader" in document.tables[0].rows[0]._tr.xml
    assert "Heading 4" in [paragraph.style.name for paragraph in document.paragraphs]


def test_api_export_blocks_corrupt_package(tmp_path, monkeypatch):
    client = _login(*ADMIN)
    routes_projects = sys.modules["bidcraft.api.routes.projects"]
    monkeypatch.setattr(routes_projects, "enforce_export_gate", lambda *a, **kw: None)

    def _corrupt(path, *_args, **_kwargs):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"corrupted delivery")
        return target

    monkeypatch.setattr(routes_projects, "export_docx", _corrupt)
    project_id = _project_with_body("API 损坏交付件")

    response = client.post(f"/projects/{project_id}/export", json={"format": "docx"})
    assert response.status_code == 500
    assert "完整性校验失败" in response.json()["detail"]


def test_commercial_export_passes_saved_template(tmp_path, monkeypatch):
    import bidcraft.export.commercial_docx as commercial_docx_module

    admin = _login(*ADMIN)
    created = admin.post(
        "/admin/users",
        json={
            "username": "batch4-commercial",
            "display_name": "商务用户",
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert created.status_code == 201, created.text
    client = _login("batch4-commercial")
    project = client.post(
        "/projects", json={"name": "商务模板项目", "package": "weakcurrent-cabling"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    with session_scope() as db:
        version = BoqQuoteVersion(
            project_id=project_id,
            package_id=None,
            name="定稿报价",
            status="approved",
            prices={"清单!2": 10.0},
            summary={"total_amount": 20.0, "tax_amount": 2.6, "total_with_tax": 22.6},
            basis={"qty": {"清单!2": 2.0}, "tax_rate": 0.13, "currency": "CNY"},
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        version_id = version.id
    created_doc = client.post(
        f"/projects/{project_id}/commercial",
        json={"name": "商务标", "quote_version_id": version_id},
    )
    assert created_doc.status_code == 201, created_doc.text
    doc_id = created_doc.json()["id"]
    saved = client.put(f"/projects/{project_id}/export-template", json={"id": "compact"})
    assert saved.status_code == 200, saved.text

    original = commercial_docx_module.export_commercial_docx
    captured: dict = {}

    def _spy(path, payload, *, template=None):
        captured["template"] = template
        return original(path, payload, template=template)

    monkeypatch.setattr(commercial_docx_module, "export_commercial_docx", _spy)
    response = client.post(
        f"/projects/{project_id}/commercial/{doc_id}/export?format=docx"
    )
    assert response.status_code == 200, response.text
    assert captured["template"]["id"] == "compact"
    downloaded = tmp_path / "commercial.docx"
    downloaded.write_bytes(response.content)
    text = "\n".join(p.text for p in Document(str(downloaded)).paragraphs)
    assert "商 务 标" not in text
    assert "目  录" in text


def test_mcp_export_honors_template_and_image_policy(monkeypatch):
    from bidcraft.mcp import handle_message

    project_id = _project_with_body("MCP 模板导出")
    with session_scope() as db:
        project = db.get(Project, project_id)
        templates.save(project, "compact")
        project.meta = {
            **(project.meta or {}),
            "image_policy": {"width_cm": 9.0, "caption_prefix": "插图"},
        }

    import bidcraft.export.docx as docx_export_module

    original = docx_export_module.export_docx
    captured: dict = {}

    def _spy(path, ledger, nodes, **kwargs):
        captured.update(kwargs)
        return original(path, ledger, nodes, **kwargs)

    # 局部 import 取的是模块属性，patch 一次即可覆盖 MCP 通道。
    monkeypatch.setattr(docx_export_module, "export_docx", _spy)
    try:
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "export_document", "arguments": {"ref": project_id}},
            }
        )
        assert response["result"]["isError"] is False, response
        payload = json.loads(response["result"]["content"][0]["text"])
    finally:
        docx_export_module.export_docx = original

    assert captured["template"]["id"] == "compact"
    assert captured["image_policy"]["width_cm"] == 9.0
    assert captured["image_policy"]["caption_prefix"] == "插图"
    _assert_no_cover_and_compact_font(Path(payload["path"]))


def test_cli_export_honors_template_and_image_policy(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.export import docx as docx_export_module

    project_id = _project_with_body("CLI 模板导出")
    with session_scope() as db:
        project = db.get(Project, project_id)
        templates.save(project, "compact")
        project.meta = {
            **(project.meta or {}),
            "image_policy": {"width_cm": 9.0, "caption_prefix": "插图"},
        }

    original = docx_export_module.export_docx
    captured: dict = {}

    def _spy(path, ledger, nodes, **kwargs):
        captured.update(kwargs)
        return original(path, ledger, nodes, **kwargs)

    monkeypatch.setattr(docx_export_module, "export_docx", _spy)
    target = tmp_path / "cli.docx"
    try:
        result = CliRunner().invoke(
            app, ["export", project_id, "--format", "docx", "--output", str(target)]
        )
    finally:
        docx_export_module.export_docx = original
    assert result.exit_code == 0, result.output
    assert captured["template"]["id"] == "compact"
    assert captured["image_policy"]["width_cm"] == 9.0
    _assert_no_cover_and_compact_font(target)


def test_commercial_export_gate_blocks_stale_quote_and_owner_force():
    """BATCH 5 集成：商务标引用旧报价时 409；owner 填足原因可强制导出并留痕。"""

    from bidcraft.models import AuditLog

    admin = _login(*ADMIN)
    created = admin.post(
        "/admin/users",
        json={
            "username": "batch4-stale-owner",
            "display_name": "商务负责人",
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert created.status_code == 201, created.text
    client = _login("batch4-stale-owner")
    project = client.post(
        "/projects", json={"name": "商务一致性导出", "package": "weakcurrent-cabling"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    with session_scope() as db:
        version = BoqQuoteVersion(
            project_id=project_id,
            package_id=None,
            name="定稿报价V1",
            status="approved",
            prices={"清单!2": 10.0},
            summary={"total_amount": 20.0, "tax_amount": 2.6, "total_with_tax": 22.6},
            basis={"qty": {"清单!2": 2.0}, "tax_rate": 0.13, "currency": "CNY"},
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        version_id = version.id
    created_doc = client.post(
        f"/projects/{project_id}/commercial",
        json={"name": "商务标", "quote_version_id": version_id},
    )
    assert created_doc.status_code == 201, created_doc.text
    doc_id = created_doc.json()["id"]

    # 报价版本退回草稿 → 商务标引用失效。
    with session_scope() as db:
        db.get(BoqQuoteVersion, version_id).status = "draft"

    blocked = client.post(f"/projects/{project_id}/commercial/{doc_id}/export?format=docx")
    assert blocked.status_code == 409
    assert "不能导出" in blocked.json()["detail"]

    short_reason = client.post(
        f"/projects/{project_id}/commercial/{doc_id}/export?format=docx"
        "&force=true&force_reason=x"
    )
    assert short_reason.status_code == 400
    assert "不少于 4" in short_reason.json()["detail"]

    forced = client.post(
        f"/projects/{project_id}/commercial/{doc_id}/export?format=docx"
        "&force=true&force_reason=客户书面确认使用旧报价"
    )
    assert forced.status_code == 200, forced.text
    assert ".docx" in forced.headers["content-disposition"]

    with session_scope() as db:
        force_audits = [
            row
            for row in db.query(AuditLog)
            .filter(AuditLog.project_id == project_id)
            .all()
            if row.action == "commercial.export_force"
        ]
    assert len(force_audits) == 1
    assert force_audits[0].detail["reason"] == "客户书面确认使用旧报价"
    assert force_audits[0].detail["doc_id"] == doc_id
