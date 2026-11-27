"""v0.33.2：事实来源口径在规划、正文提示与引用卡中的真实链路留痕。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from bidcraft.db import session_scope
from bidcraft.generation.chapter_plan import (
    PROMPT_VERSION,
    generate_chapter_plan,
    plan_context_text,
    plan_dependency_staleness,
)
from bidcraft.ledger import update_fact
from bidcraft.models import (
    AuditLog,
    ChapterPlan,
    Fact,
    FactCitation,
    OutlineNode,
    Project,
    Section,
    SourceFile,
)
from bidcraft.provenance import node_citations

NOW = datetime(2026, 11, 26, 9, 0, 0)
QUOTE = "投标截止：2026 年 12 月 20 日 09:30"


def _seed_project(db) -> tuple[Project, OutlineNode, Section]:
    project = Project(name="来源留痕项目")
    db.add(project)
    db.flush()
    node = OutlineNode(
        project_id=project.id,
        heading="实施方案",
        position=1,
        target_words=800,
    )
    db.add(node)
    db.flush()
    section = Section(node_id=node.id, body="正文占位", status="draft")
    section.review_status = "confirmed"
    section.confirmed_by = "旧确认人"
    section.confirmed_at = NOW
    section.locked_by = "旧锁定人"
    section.locked_at = NOW
    db.add(section)
    db.flush()
    return project, node, section


def _seed_source(db, project_id: str) -> str:
    source = SourceFile(
        project_id=project_id,
        filename="招标文件.pdf",
        stored_path="/tmp/招标文件.pdf",
        text_content=QUOTE,
        page_count=5,
        parse_state="parsed",
    )
    db.add(source)
    db.flush()
    return source.id


def _seed_fact(
    db, project_id: str, node_id: str, *, file_id: str, value: str = "2026-12-20 09:30"
) -> Fact:
    fact = Fact(
        project_id=project_id,
        key="tender.deadline",
        label="投标截止时间",
        value=value,
        unit="",
        status="confirmed",
        provenance_kind="tender_extract",
        source_file_id=file_id,
        source_quote=QUOTE,
        source_page=2,
        confirmed_by="原确认人",
        confirmed_at=NOW,
    )
    db.add(fact)
    db.flush()
    citation = FactCitation(
        project_id=project_id,
        node_id=node_id,
        fact_key=fact.key,
        value=value,
        occurrences=1,
    )
    db.add(citation)
    db.flush()
    return fact


def test_provenance_change_invalidates_sections_and_audits_before_after(db):
    with session_scope() as session:
        project, node, section = _seed_project(session)
        file_id = _seed_source(session, project.id)
        _seed_fact(session, project.id, node.id, file_id=file_id)
        section_id = section.id
        project_id = project.id

    with session_scope() as session:
        project = session.get(Project, project_id)
        update_fact(
            session,
            project,
            {
                "key": "tender.deadline",
                "value": "2026-12-20 09:30",
                "status": "manual",
                "provenance_kind": "user_supplied",
                "supplier_note": "招标文件未给出，依据甲方口头澄清补充",
            },
            actor={"username": "owner", "display_name": "李工"},
            now=NOW,
        )

    with session_scope() as session:
        section = session.get(Section, section_id)
        assert section.review_status == "review"
        assert section.confirmed_by is None and section.confirmed_at is None
        assert section.locked_by is None and section.locked_at is None
        assert section.review_requested_by is None
        assert "provenance_changed" in (section.review_note or "")
        audits = list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.project_id == project_id)
                .order_by(AuditLog.id)
            )
        )
        update = next(a for a in audits if a.action == "fact.update")
        assert update.detail["old_provenance_kind"] == "tender_extract"
        assert update.detail["provenance_kind"] == "user_supplied"
        assert update.detail["provenance_changed"] is True
        assert update.detail["supplier_note"] == "招标文件未给出，依据甲方口头澄清补充"
        invalid = next(a for a in audits if a.action == "fact.invalidation")
        assert invalid.detail["reason"] == "provenance_changed"
        assert section_id in invalid.detail["invalidated_sections"]


def test_plan_context_and_fact_refs_carry_user_supplied_provenance(db):
    assert PROMPT_VERSION == "chapter_plan_v4"
    with session_scope() as session:
        project, node, _ = _seed_project(session)
        file_id = _seed_source(session, project.id)
        _seed_fact(session, project.id, node.id, file_id=file_id)
        update_fact(
            session,
            project,
            {
                "key": "tender.deadline",
                "value": "2026-12-20 09:30",
                "status": "manual",
                "provenance_kind": "user_supplied",
                "supplier_note": "招标文件未给出，依据甲方澄清补充",
            },
            actor="owner",
            now=NOW,
        )
        plan = generate_chapter_plan(
            session,
            project,
            node,
            "",
            "owner",
            False,
            client=None,
            model_output="",
            package_id=None,
            user_id=None,
        )
        refs = [r for r in (plan.fact_refs or []) if isinstance(r, dict)]
        assert refs
        ref = next(r for r in refs if r.get("key") == "tender.deadline")
        assert ref["provenance_kind"] == "user_supplied"
        assert ref["supplier_note"] == "招标文件未给出，依据甲方澄清补充"
        assert ref["confirmed_by"] == "owner"
        text = plan_context_text(plan)
        assert "来源：用户补充" in text
        assert "甲方澄清补充" in text


def test_plan_staleness_detects_provenance_change(db):
    with session_scope() as session:
        project, node, _ = _seed_project(session)
        file_id = _seed_source(session, project.id)
        _seed_fact(session, project.id, node.id, file_id=file_id)
        plan = generate_chapter_plan(
            session,
            project,
            node,
            "",
            "owner",
            False,
            client=None,
            model_output="",
            package_id=None,
            user_id=None,
        )
        plan.status = "CONFIRMED"
        session.flush()
        plan_id = plan.id
        project_id = project.id

    with session_scope() as session:
        project = session.get(Project, project_id)
        update_fact(
            session,
            project,
            {
                "key": "tender.deadline",
                "value": "2026-12-20 09:30",
                "status": "manual",
                "provenance_kind": "user_supplied",
                "supplier_note": "依据甲方澄清补充",
            },
            actor="owner",
            now=NOW,
        )

    with session_scope() as session:
        plan = session.get(ChapterPlan, plan_id)
        reasons = plan_dependency_staleness(session, plan)
        assert any("来源口径已变化" in reason for reason in reasons)


def test_citation_payload_exposes_provenance(db):
    with session_scope() as session:
        project, node, _ = _seed_project(session)
        file_id = _seed_source(session, project.id)
        _seed_fact(session, project.id, node.id, file_id=file_id)
        update_fact(
            session,
            project,
            {
                "key": "tender.deadline",
                "value": "2026-12-20 09:30",
                "status": "manual",
                "provenance_kind": "user_supplied",
                "supplier_note": "甲方澄清",
            },
            actor={"username": "owner", "display_name": "李工"},
            now=NOW,
        )
        project_id, node_id = project.id, node.id

    with session_scope() as session:
        rows = node_citations(session, project_id, node_id)
        assert rows
        row = rows[0]
        assert row["provenance_kind"] == "user_supplied"
        assert row["supplier_note"] == "甲方澄清"
        assert row["confirmed_by"] == "李工"
