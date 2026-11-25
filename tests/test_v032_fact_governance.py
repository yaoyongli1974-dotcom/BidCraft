"""v0.32 Phase 5：事实确认治理与只读经验端点。

全部断言走真实 SQLite + 当前模型建表 + 真实 AuditLog，不 mock 核心链路：
确认盖章、伪造证据拒绝、重解析保留/强制覆盖、章节失效、来源文件往返、
viewer 只读经验与 CLI 审计。HTTP 用例使用真实 TestClient 与登录会话。
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from bidcraft import cli  # noqa: E402
from bidcraft.api import create_app  # noqa: E402
from bidcraft.api.routes import projects as project_routes  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.export_gate import ExportGateError, enforce_export_gate  # noqa: E402
from bidcraft.ledger import FactItemV032, FactLedgerV032, save_ledger, update_fact  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    Fact,
    FactCitation,
    OutlineNode,
    Project,
    QualityFinding,
    QualityReport,
    Section,
    SourceFile,
)
from bidcraft.schemas import SourceRef  # noqa: E402

PASSWORD = "v032-fact-pass"
QUOTE = "投标截止：2026 年 12 月 20 日 09:30"
NOW = datetime(2026, 11, 25, 8, 30, 0)


def _login(username: str, password: str = PASSWORD):
    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _seed_project(*, name: str = "事实治理项目") -> str:
    with session_scope() as db:
        project = Project(name=name)
        db.add(project)
        db.flush()
        return project.id


def _seed_source(project_id: str, *, text: str = QUOTE, page_count: int = 5) -> str:
    with session_scope() as db:
        source = SourceFile(
            project_id=project_id,
            filename="招标文件.pdf",
            stored_path="/tmp/招标文件.pdf",
            text_content=text,
            page_count=page_count,
            parse_state="parsed",
        )
        db.add(source)
        db.flush()
        return source.id


def _seed_fact(project_id: str, file_id: str, *, value="旧口径", status="extracted") -> str:
    with session_scope() as db:
        fact = Fact(
            project_id=project_id,
            key="tender.deadline",
            label="投标截止时间",
            value=value,
            unit="",
            status=status,
            source_file_id=file_id,
            source_quote=QUOTE,
            source_page=2,
        )
        db.add(fact)
        db.flush()
        return fact.id


def _audits(project_id: str, action: str):
    with session_scope() as db:
        return list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.project_id == project_id, AuditLog.action == action)
                .order_by(AuditLog.id)
            )
        )


def test_confirmation_stamps_server_actor_and_evidence(db):
    project_id = _seed_project()
    file_id = _seed_source(project_id)
    _seed_fact(project_id, file_id)
    with session_scope() as session:
        project = session.get(Project, project_id)
        row = update_fact(
            session,
            project,
            {
                "key": "tender.deadline",
                "value": "2026-12-20 09:30",
                "status": "confirmed",
                "source_file_id": file_id,
                "source_quote": QUOTE,
                "source_page": 2,
            },
            actor={"username": "owner", "display_name": "李工"},
            now=NOW,
        )
        assert row.confirmed_by == "李工"
        assert row.confirmed_at == NOW
        assert row.source_file_id == file_id
        assert row.source_quote == QUOTE
        assert row.source_page == 2
    audit_rows = _audits(project_id, "fact.update")
    assert len(audit_rows) == 1
    assert audit_rows[0].user_id is None
    assert audit_rows[0].username == "owner"
    assert audit_rows[0].detail["confirmed_by"] == "李工"
    assert audit_rows[0].detail["source_file_id"] == file_id


def test_fabricated_evidence_is_rejected(db):
    project_id = _seed_project()
    file_id = _seed_source(project_id)
    other_project = _seed_project(name="另一个项目")
    other_file = _seed_source(other_project)
    _seed_fact(project_id, file_id)
    bad_payloads = [
        {"source_file_id": file_id, "source_quote": "没有出现在文件里的假引文", "source_page": 2},
        {"source_file_id": other_file, "source_quote": QUOTE, "source_page": 2},
        {"source_file_id": file_id, "source_quote": QUOTE, "source_page": 0},
        {"source_file_id": file_id, "source_quote": QUOTE, "source_page": 99},
    ]
    for payload in bad_payloads:
        with session_scope() as session:
            project = session.get(Project, project_id)
            with pytest.raises(ValueError):
                update_fact(
                    session,
                    project,
                    {
                        "key": "tender.deadline",
                        "value": "伪造值",
                        "status": "confirmed",
                        **payload,
                    },
                    actor="cli",
                )
    with session_scope() as session:
        fact = session.scalar(select(Fact).where(Fact.project_id == project_id))
        assert fact.value == "旧口径"
        assert fact.confirmed_by is None
    assert _audits(project_id, "fact.update") == []


def test_reparse_preserves_confirmed_facts_and_force_overwrites(db):
    project_id = _seed_project()
    file_id = _seed_source(project_id)
    _seed_fact(project_id, file_id, value="人工口径", status="confirmed")
    with session_scope() as session:
        fact = session.scalar(select(Fact).where(Fact.project_id == project_id))
        fact.confirmed_by = "李工"
        fact.confirmed_at = NOW

    new_ledger = FactLedgerV032(
        facts=[
            FactItemV032(
                key="tender.deadline",
                label="投标截止时间",
                value="解析新口径",
                status="extracted",
                source=SourceRef(file="招标文件.pdf", page=2, quote=QUOTE),
            )
        ]
    )
    with session_scope() as session:
        save_ledger(session, project_id, new_ledger, actor="cli")
    with session_scope() as session:
        fact = session.scalar(select(Fact).where(Fact.project_id == project_id))
        assert fact.value == "人工口径"
        assert fact.status == "confirmed"
        assert fact.confirmed_by == "李工"
        assert fact.confirmed_at == NOW

    with session_scope() as session:
        save_ledger(session, project_id, new_ledger, force=True, actor="cli", now=NOW)
    with session_scope() as session:
        fact = session.scalar(select(Fact).where(Fact.project_id == project_id))
        assert fact.value == "解析新口径"
        assert fact.status == "extracted"
        assert fact.confirmed_by is None and fact.confirmed_at is None
    forced = _audits(project_id, "fact.update")
    assert len(forced) == 1
    assert forced[0].username == "cli"
    assert forced[0].detail["forced"] is True


def test_value_change_invalidates_citing_section_without_export_bypass(db):
    project_id = _seed_project()
    file_id = _seed_source(project_id)
    fact_id = _seed_fact(project_id, file_id, value="旧口径", status="confirmed")
    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body="正文仍写着旧口径",
                review_status="confirmed",
                confirmed_by="王工",
                confirmed_at=NOW,
            )
        )
        session.flush()
        session.add(
            FactCitation(
                project_id=project_id,
                node_id=node.id,
                fact_key="tender.deadline",
                value="旧口径",
            )
        )
        node_id = node.id

    with session_scope() as session:
        project = session.get(Project, project_id)
        update_fact(
            session,
            project,
            {
                "key": "tender.deadline",
                "value": "新口径",
                "status": "confirmed",
                "source_file_id": file_id,
                "source_quote": QUOTE,
                "source_page": 2,
            },
            actor={"username": "owner", "display_name": "李工"},
            now=NOW,
        )
    with session_scope() as session:
        section = session.scalar(select(Section).where(Section.node_id == node_id))
        assert section.review_status == "review"
        assert section.confirmed_by is None and section.confirmed_at is None
    audit_rows = _audits(project_id, "fact.invalidation")
    assert len(audit_rows) == 1
    assert audit_rows[0].detail["fact_id"] == fact_id
    assert audit_rows[0].detail["node_ids"] == [node_id]

    # 强制导出也不能越过「章节已失效需复核」这道门禁。
    with session_scope() as session:
        with pytest.raises(ExportGateError) as exc:
            enforce_export_gate(
                session,
                project_id,
                "docx",
                force=True,
                force_reason="已确认需要强制导出",
            )
        assert exc.value.status == 409
        assert "未确认" in str(exc.value)
    assert _audits(project_id, "export.force") == []


def test_source_file_id_roundtrip_through_save_and_load(db):
    project_id = _seed_project()
    file_id = _seed_source(project_id)
    ledger = FactLedgerV032(
        facts=[
            FactItemV032(
                key="tender.deadline",
                value="2026-12-20",
                status="extracted",
                source=SourceRef(file="招标文件.pdf", page=2, quote=QUOTE),
            )
        ]
    )
    with session_scope() as session:
        save_ledger(session, project_id, ledger)
    from bidcraft.ledger import load_ledger

    with session_scope() as session:
        fact = session.scalar(select(Fact).where(Fact.project_id == project_id))
        assert fact.source_file_id == file_id
        loaded = load_ledger(session, project_id)
    item = loaded.facts[0]
    assert item.source.file == "招标文件.pdf"
    assert item.source_file_id == file_id
    assert item.source_file_name == "招标文件.pdf"
    assert item.source_page == 2
    assert item.source_quote == QUOTE


def _api_context():
    admin = _login("admin", "test-admin")
    for name in ("fact-owner", "fact-viewer", "fact-outsider"):
        resp = admin.post(
            "/admin/users",
            json={"username": name, "display_name": name, "password": PASSWORD, "role": "manager"},
        )
        assert resp.status_code == 201, resp.text
    owner = _login("fact-owner")
    project_id = owner.post(
        "/projects", json={"name": "HTTP 事实项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    owner.post(
        f"/projects/{project_id}/members", json={"username": "fact-viewer", "role": "viewer"}
    )
    return {
        "admin": admin,
        "owner": owner,
        "viewer": _login("fact-viewer"),
        "outsider": _login("fact-outsider"),
        "project": project_id,
    }


def test_patch_fact_keeps_write_role_and_exposes_confirmation(db):
    ctx = _api_context()
    project_id = ctx["project"]
    file_id = _seed_source(project_id)
    _seed_fact(project_id, file_id)

    denied = ctx["viewer"].patch(
        f"/projects/{project_id}/facts/tender.deadline",
        json={
            "value": "viewer 改的值",
            "status": "confirmed",
            "source_file_id": file_id,
            "source_quote": QUOTE,
            "source_page": 2,
        },
    )
    assert denied.status_code == 403
    patched = ctx["owner"].patch(
        f"/projects/{project_id}/facts/tender.deadline",
        json={
            "value": "2026-12-20 09:30",
            "status": "manual",
            "source_file_id": file_id,
            "source_quote": QUOTE,
            "source_page": 2,
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["status"] == "manual"
    assert body["status"] in {"confirmed", "manual"}
    assert body["confirmed_by"] in {"fact-owner", "owner"}
    assert body["confirmed_at"]
    assert body["source_file_id"] == file_id
    assert body["source_file_name"] == "招标文件.pdf"
    assert body["source_quote"] == QUOTE
    assert body["source_page"] == 2
    assert _audits(project_id, "fact.update")[0].detail["key"] == "tender.deadline"


def test_lessons_endpoint_is_viewer_safe_and_audited(db, monkeypatch):
    ctx = _api_context()
    project_id = ctx["project"]
    captured = {}

    def _lessons(db, project, node_id=None):
        captured["node_id"] = node_id
        return {
            "items": [{"id": "l1", "text": "经验一"}, {"id": "l2", "text": "经验二"}],
            "node_id": node_id,
            "status": "未审核建议经验",
        }

    monkeypatch.setattr(project_routes, "lessons_payload", _lessons)
    response = ctx["viewer"].get(f"/projects/{project_id}/lessons?node_id=node-1&limit=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["l1"]
    assert body["status"] == "未审核建议经验"
    assert body["limit"] == 1
    assert captured["node_id"] == "node-1"
    assert ctx["outsider"].get(f"/projects/{project_id}/lessons").status_code == 404

    rows = _audits(project_id, "learning.read")
    assert len(rows) == 1
    assert rows[0].username == "fact-viewer"
    assert rows[0].detail["node_id"] == "node-1"
    assert rows[0].detail["count"] == 1


def test_lessons_endpoint_uses_real_learning_payload(db):
    """真实 learning.lessons_payload 聚合.handled finding，只读端点可展示。"""

    ctx = _api_context()
    project_id = ctx["project"]
    with session_scope() as session:
        report = QualityReport(project_id=project_id, report_version=1)
        session.add(report)
        session.flush()
        session.add(
            QualityFinding(
                report_id=report.id,
                title="资质附件顺序",
                status="handled",
                evidence={
                    "resolution_note": "已按招标文件页码重排附件",
                    "resolved_by": "李工",
                    "resolved_at": "2026-02-02T10:00:00+08:00",
                },
            )
        )

    response = ctx["viewer"].get(f"/projects/{project_id}/lessons")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "未审核建议经验"
    assert any(
        item["source"] == "quality_finding" and "资质附件顺序" in item["text"]
        for item in body["items"]
    )
    assert _audits(project_id, "learning.read")


def test_cli_fact_writes_audit_as_cli(db):
    project_id = _seed_project()
    file_id = _seed_source(project_id)
    _seed_fact(project_id, file_id)
    result = CliRunner().invoke(
        cli.app,
        [
            "fact",
            project_id,
            "tender.deadline",
            "--value",
            "CLI 口径",
            "--source-file-id",
            file_id,
            "--source-quote",
            QUOTE,
            "--source-page",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    with session_scope() as session:
        fact = session.scalar(select(Fact).where(Fact.project_id == project_id))
        assert fact.value == "CLI 口径"
        assert fact.confirmed_by == "cli"
        assert fact.confirmed_at is not None
    assert _audits(project_id, "fact.update")[0].username == "cli"


def test_chapter_save_change_note_reaches_revision_and_audit(db):
    ctx = _api_context()
    project_id = ctx["project"]
    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading="施工组织", position=1)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="旧正文"))
        session.flush()
        node_id = node.id

    response = ctx["owner"].put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": "新正文", "change_note": "按评标办法补充了质量措施"},
    )
    assert response.status_code == 200, response.text

    from bidcraft.models import SectionRevision

    with session_scope() as session:
        revision = session.scalar(
            select(SectionRevision).where(SectionRevision.node_id == node_id)
        )
        assert revision is not None
        assert revision.change_note == "按评标办法补充了质量措施"
    assert _audits(project_id, "chapter.save")[0].detail["change_note"] == (
        "按评标办法补充了质量措施"
    )
