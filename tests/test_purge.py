"""显式清理与孤儿检测（P1-4 第一阶段，不开外键强制）。

验证：删除项目时连带清理全部关联（含此前漏掉的报价版本/包件/引用），
且审计 user_id 留存红线不被触碰；孤儿检测能发现并清理悬挂引用。
"""

from __future__ import annotations

from sqlalchemy import select

from bidcraft.db import session_scope
from bidcraft.models import (
    AuditLog,
    BidPackage,
    BoqQuoteVersion,
    FactCitation,
    Issue,
    Job,
    JobEvent,
    OutlineNode,
    Project,
    Section,
    User,
)
from bidcraft.services.purge import (
    clean_orphans,
    delete_project_rows,
    find_orphans,
    project_child_counts,
)


def _seed_project(db, name: str = "清理项目") -> tuple[str, str, str]:
    """建一个带完整关联的项目，返回 (project_id, node_id, job_id)。"""
    user = User(username="owner", display_name="owner", password_hash="x")
    db.add(user)
    db.flush()
    project = Project(name=name, owner_id=user.id, package="weakcurrent-cabling")
    db.add(project)
    db.flush()
    node = OutlineNode(project_id=project.id, heading="总体方案", position=1)
    db.add(node)
    db.flush()
    db.add(Section(node_id=node.id, body="正文", words=2, status="written"))
    db.add(FactCitation(project_id=project.id, node_id=node.id, fact_key="k", value="字段"))
    db.add(Issue(project_id=project.id, node_id=node.id, level="blocking", finding="风险"))
    db.add(BidPackage(project_id=project.id, name="包件一"))
    db.add(
        BoqQuoteVersion(project_id=project.id, name="报价版本", prices={}, summary={})
    )
    job = Job(project_id=project.id, kind="generation", status="queued")
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, kind="progress", data={}))
    db.add(AuditLog(project_id=project.id, user_id=user.id, action="project.create", detail={}))
    db.flush()
    return project.id, node.id, job.id


def test_delete_project_rows_removes_all_children(db):
    with session_scope() as session:
        project_id, node_id, job_id = _seed_project(session)
        counts = project_child_counts(session, project_id)
        assert counts["sections"] == 1
        assert counts["boq_quote_versions"] == 1
        assert counts["fact_citations"] == 1
        removed = delete_project_rows(session, project_id)
        # 此前手工路径漏掉的三类现在一并清理
        assert removed["boq_quote_versions"] == 1
        assert removed["bid_packages"] == 1
        assert removed["fact_citations"] == 1
        assert removed["job_events"] == 1
        # 项目本体仍在，由调用方决定删除
        assert session.get(Project, project_id) is not None

    with session_scope() as session:
        assert session.scalar(select(Section).where(Section.node_id == node_id)) is None
        assert session.scalar(select(JobEvent).where(JobEvent.job_id == job_id)) is None
        assert (
            session.scalar(
                select(BoqQuoteVersion).where(BoqQuoteVersion.project_id == project_id)
            )
            is None
        )


def test_delete_project_keeps_audit_user_retention(db):
    """审计的 user_id 可悬挂留存；delete_project_rows 不碰 user_id 关联。"""
    with session_scope() as session:
        project_id, _node_id, _job_id = _seed_project(session)
        # 一条 user_id 指向不存在用户的审计（模拟 account.purge 留存）
        detached = AuditLog(user_id="deleted-user-id", action="account.purge", detail={})
        session.add(detached)
        session.flush()
        delete_project_rows(session, project_id)
    with session_scope() as session:
        kept = session.scalar(
            select(AuditLog).where(AuditLog.action == "account.purge")
        )
        assert kept is not None and kept.user_id == "deleted-user-id"


def test_find_and_clean_orphans(db):
    with session_scope() as session:
        project_id, node_id, _job_id = _seed_project(session)
        # 人为制造孤儿：删 node 但保留 section / fact_citation 的 node_id
        session.execute(Section.__table__.delete().where(Section.node_id == node_id))
        session.add(Section(node_id="ghost-node", body="孤儿正文", words=4, status="written"))
        session.add(FactCitation(project_id=project_id, node_id="ghost-node", fact_key="k2", value="字段"))
        session.flush()
        orphans = find_orphans(session)
        assert "sections.node_id" in orphans
        assert "fact_citations.node_id" in orphans
        cleaned = clean_orphans(session)
        assert cleaned.get("sections.node_id", 0) >= 1
        assert cleaned.get("fact_citations.node_id", 0) >= 1
        assert find_orphans(session) == {}
